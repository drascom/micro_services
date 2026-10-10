"""Admin actions on a site's stages (README §5.6; the T10 endpoints call these). Errors: ``SiteNotFound`` / ``LookupError``
(unknown site / stage), ``ValueError`` (refused input; the message says why)."""
from __future__ import annotations

import time
from typing import Optional

from ...core.fetch import netguard
from .. import notes
from ..sitemap import classify, meta as map_meta, site_notes, store as map_store
from . import common, engine, state


#: ``fn(site_id, stage_id, title) -> gap id`` (T14 ``problems.gaps``); unbound = only the state + the admin note
GAP_HOOK = None


class StageBusy(Exception):
    """The stage is running right now: the request cannot reach this run (admin: 409 ``stage_busy``)."""


def _site(site_id: str) -> dict:
    site = map_store.get(site_id)
    if site is None:
        raise map_store.SiteNotFound(site_id)
    return site


def _running(site_id: str, stage_id: str) -> bool:
    from ..jobs import store as job_store
    job = job_store.get_by_key(engine.job_key(site_id, stage_id))
    return job is not None and job["state"] == "running"


def start(site_id: str) -> dict:
    """Create the stage rows and queue A1 (later stages queue themselves when their ``needs`` pass)."""
    _site(site_id)
    for sid in engine.STAGES:
        state.ensure(site_id, sid)
    first = next(iter(engine.STAGES))
    return engine.enqueue(site_id, first)


def hint(site_id: str, stage: str, text: str) -> dict:
    """The hint goes to the history (``user``) and the stage runs again with it (a new LLM session). A run that is
    still queued takes the hint into its payload; a running one cannot -> ``StageBusy`` (nothing is lost silently)."""
    from ..jobs import store as job_store
    _site(site_id)
    engine.stage_of(stage)
    text = str(text or "").strip()
    if not text:
        raise ValueError("ipucu boş")
    busy = StageBusy("Aşama çalışıyor; bitince ipucunu tekrar gönder")
    job = job_store.get_by_key(engine.job_key(site_id, stage))
    if job is not None and job["state"] == "running":
        raise busy
    if job is not None and job["state"] in ("queued", "waiting"):
        payload = {**(job.get("payload") or {}), "hint": text, "hint_new": True, "fresh_at": time.time()}
        if not job_store.update_payload(job["id"], payload):
            raise busy   # leased in between
        state.add_message(site_id, stage, "user", text)
        return {"queued": False, "job_id": job["id"], "hint_added": True}
    state.add_message(site_id, stage, "user", text)
    return engine.enqueue(site_id, stage, hint=text, fresh=True)


def sample(site_id: str, stage: str, url: str, *, run: bool = True) -> dict:
    """A sample address for the stage (netguard + the site's own host), first in its list; the stage runs again
    (``run=False``, T25 sample editor: only saved; the admin re-runs with "Örneklerle yeniden çalıştır")."""
    site = _site(site_id)
    engine.stage_of(stage)
    try:
        url = netguard.check_url(str(url or "").strip())
    except ValueError as exc:
        raise ValueError(f"adres reddedildi ({exc})") from None
    if not classify.same_site(url, site["base_url"]):
        raise ValueError("örnek adres sitenin kendi adresinde olmalı")
    home = _home_sample(site_id, stage, url)
    if home is not None:
        return home
    row = state.get(site_id, stage) or {}
    given = [url]
    rows = _list_rows(site_id, stage, url)
    if rows is not None:   # T18: a LIST page was given for a title stage: its row links become the samples, not the list
        if not rows:
            raise ValueError("liste sayfası verildi ama satır bağlantısı okunamadı")
        given = rows[:map_meta.MAX_SAMPLES]
        state.add_message(site_id, stage, "user", f"Örnek adres: {url}")
        state.add_message(site_id, stage, "system", f"Liste sayfası verildi; satırlarından {len(given)} örnek alındı")
    else:
        state.add_message(site_id, stage, "user", f"Örnek adres: {url}")
    excluded = [u for u in row.get("excluded_samples") or [] if u not in given]
    samples = given + [u for u in row.get("user_samples") or [] if u not in given]
    state.update(site_id, stage, user_samples=samples[:map_meta.MAX_SAMPLES], excluded_samples=excluded)
    out = engine.enqueue(site_id, stage, fresh=True) if run else {"queued": False, "saved": given}
    if rows is not None:
        out["from_list"] = len(given)
    return out


HOME_SAMPLE = "Bu adres sitenin ana sayfası (yönlendirme hedefi); A1 ana sayfa adresi olarak işlendi"


def _home_sample(site_id: str, stage: str, url: str) -> Optional[dict]:
    """The address given as a sample IS the home page (``/`` or where it redirects): never a listing / title sample. It
    becomes ``pages.home.url`` when the map still says otherwise (A1 runs again, the later stages wait for it)."""
    from ..sitemap import labels
    from . import a1_home
    first = next(iter(engine.STAGES))
    ctx = engine.build_ctx(site_id, engine.stage_of(first))
    given = labels.path(url)
    if given not in a1_home.home_paths(ctx):
        return None
    state.add_message(site_id, stage, "user", f"Örnek adres: {url}")
    state.add_message(site_id, stage, "system", HOME_SAMPLE)
    row = state.get(site_id, stage) or {}
    pinned = [u for u in row.get("user_samples") or [] if labels.path(u) != given]
    state.update(site_id, stage, user_samples=pinned)
    target = given if given != "/" else (a1_home.redirect_path(ctx, common.site_url(ctx.base_url, "/")) or "/")
    out: dict = {"queued": False, "home": True, "home_url": target}
    draft = map_store.draft(site_id)
    home = (draft.get("pages") or {}).get("home")
    if isinstance(home, dict) and labels.path(common.site_url(ctx.base_url, home.get("url") or "/")) != target:
        home["url"] = target
        map_store.save_draft(site_id, draft)
        state.add_message(site_id, first, "system", a1_home.REDIRECT_NOTE.format(to=target))
        out.update(engine.enqueue(site_id, first, fresh=True), home=True, home_url=target)
    return out


#: stages whose samples are title pages: a list page given as their sample means "take its rows"
_TITLE_STAGES = ("title",)


def _list_rows(site_id: str, stage: str, url: str) -> Optional[list]:
    """``None`` when ``url`` is not a list page (or the stage does not take title pages); else the row links of that list
    page (fetched now, the cache bypassed)."""
    from . import a3_title
    st = engine.stage_of(stage)
    if st.page_type not in _TITLE_STAGES:
        return None
    ctx = engine.build_ctx(site_id, st, fresh_at=time.time())
    ctx.own_only = False   # the rows of the given list become the admin's own samples
    path = common.path(url).rstrip("/")
    lists = {common.path(u).rstrip("/") for u in a3_title.listing_urls(ctx)}
    pattern = ctx.page("listing").get("url_pattern")
    if path not in lists and not (pattern and classify.pattern_matches(pattern, url, ctx.base_url)):
        return None
    picker = a3_title.Picker(ctx)
    return [u for u in dict.fromkeys(a3_title.listing_rows(ctx, url)) if picker.ok(u)]


def exclude(site_id: str, stage: str, url: str, undo: bool = False, *, run: bool = True) -> dict:
    """T18: the admin removes a sample from the stage's measurement (kept across runs; ``undo`` takes it back); the stage
    runs again (more samples are taken when too few are left)."""
    _site(site_id)
    engine.stage_of(stage)
    url = str(url or "").strip()
    if not url:
        raise ValueError("adres boş")
    if _running(site_id, stage):
        raise StageBusy("Aşama çalışıyor; bitince tekrar dene")
    row = state.get(site_id, stage) or {}
    excluded = [u for u in row.get("excluded_samples") or [] if u != url]
    if not undo:
        excluded = [url] + excluded
    pinned = [u for u in row.get("user_samples") or [] if u != url or undo]
    state.update(site_id, stage, excluded_samples=excluded[:50], user_samples=pinned)
    state.add_message(site_id, stage, "user", f"Örnek {'geri alındı' if undo else 'çıkarıldı'}: {url}")
    return {"excluded": excluded[:50], **(engine.enqueue(site_id, stage, fresh=True) if run else {"queued": False})}


def skip(site_id: str, stage: str, field: Optional[str] = None) -> dict:
    """``field``: "not on this site" (``meta.<type>.skipped``; its checks are dropped) and the stage is measured again.
    No field: the whole stage is skipped, only when it is optional."""
    _site(site_id)
    st = engine.stage_of(stage)
    if field:
        field = str(field).strip()
        if field in st.never_skip or field in map_meta.NEVER_SKIPPED:
            raise ValueError(f"'{field}' alanı atlanamaz")
        map_store.save_draft(site_id, map_meta.skip(map_store.draft(site_id), st.page_type, field))
        state.add_message(site_id, stage, "user", f"Alan yok sayıldı: {field}")
        return {"skipped_field": field, **engine.enqueue(site_id, stage)}
    if not st.optional:
        raise ValueError(f"{stage} aşaması atlanamaz (isteğe bağlı değil)")
    state.update(site_id, stage, state="skipped")
    state.add_message(site_id, stage, "user", "Aşama atlandı")
    return {"skipped_stage": stage, "queued": engine.queue_followers(site_id)}


def rerun(site_id: str, stage: str, *, repair: bool = False, evidence: Optional[dict] = None,
          samples: Optional[dict] = None, problem_id: Optional[int] = None, klass: str = "", hint: str = "",
          admin: bool = False) -> dict:
    """``repair=True`` (T14): the run measures ``samples`` (``{broken: [...], good: [...]}``) first, passes only when the
    broken ones pass and the good ones do not break, carries ``evidence`` into the LLM prompt and commits ``created_by=repair``."""
    _site(site_id)
    engine.stage_of(stage)
    if not repair:
        return engine.enqueue(site_id, stage, hint=hint, fresh=True)
    samples = samples or {}
    spec = {"problem_id": problem_id, "class": klass, "evidence": evidence or {},
            "broken": list(samples.get("broken") or []), "good": list(samples.get("good") or []), "admin": bool(admin)}
    if hint:
        state.add_message(site_id, stage, "user", hint)
    return engine.enqueue(site_id, stage, hint=hint, repair=spec)


def mark_gap(site_id: str, stage: str, title: str) -> dict:
    """The admin says the engine cannot do this yet: the state, an admin note and (T14) a ``capability_gaps`` record."""
    _site(site_id)
    st = engine.stage_of(stage)
    title = str(title or "").strip()
    if not title:
        raise ValueError("başlık boş")
    if _running(site_id, stage):
        raise ValueError("aşama şu an çalışıyor")
    row = state.get(site_id, stage) or {}
    state.update(site_id, stage, state="engine_gap", evidence={**(row.get("evidence") or {}), "gap": title})
    state.add_message(site_id, stage, "user", f"Motor boşluğu: {title}")
    note_id = notes.add("action", f"{site_id} {stage} {st.title}: motor boşluğu - {title}"[:500], site_id=site_id,
                        ref_kind="stage", ref_id=stage)
    out = {"state": "engine_gap", "note_id": note_id}
    if GAP_HOOK is not None:   # T14: the capability_gaps record (bound by control.problems)
        out["gap_id"] = GAP_HOOK(site_id, stage, title)
    return out


MAX_ADDED = 20


def review(site_id: str, stage: str, body: dict) -> dict:
    """The admin's answer to the A1 review card (T17). ``body``: ``{sections: [{id, target: title|episode|ignore}],
    links: [{url, meaning}], added: [{name, url, meaning}]}`` (meanings: ``sitemap.labels.MEANINGS``); the A2 block card:
    ``{blocks: [{url, index, choice: series|movie|mixed|ignore}]}``; two passing rule sets (T23): ``{candidate: llm|discover}``.
    An added address
    must pass netguard and sit on the site's own host. The stage passes with the labels (activation / version / crawl)."""
    from ..sitemap import labels
    site = _site(site_id)
    engine.stage_of(stage)
    if state.state_of(site_id, stage) != "review":
        raise ValueError("bu aşamada onay bekleyen inceleme yok")
    body = body if isinstance(body, dict) else {}
    out: dict = {"sections": {}, "links": {}, "added": []}
    homes: dict = {}   # T30: (row kind, row key) -> home screen target ("Ana ekranda")
    if body.get("candidate") is not None:
        if body["candidate"] not in ("llm", "discover"):
            raise ValueError(f"geçersiz aday: {body['candidate']!r} (llm | discover)")
        out["candidate"] = body["candidate"]
    for item in body.get("sections") or []:
        if not isinstance(item, dict) or not item.get("id"):
            raise ValueError("bölüm satırı {id, target} olmalı")
        if item.get("target") not in labels.SECTION_CHOICES:
            raise ValueError(f"geçersiz bölüm hedefi: {item.get('target')!r}")
        out["sections"][str(item["id"])] = item["target"]
        _home_choice(homes, ("section", str(item["id"])), item)
    for item in body.get("links") or []:
        if not isinstance(item, dict) or not item.get("url"):
            raise ValueError("bağlantı satırı {url, meaning} olmalı")
        if item.get("meaning") not in labels.MEANINGS:
            raise ValueError(f"geçersiz anlam: {item.get('meaning')!r}")
        out["links"][labels.path(str(item["url"]))] = item["meaning"]
        _home_choice(homes, ("link", labels.path(str(item["url"]))), item)
    out["blocks"] = {}
    for item in body.get("blocks") or []:   # A2: what each block of a listing page is
        if not isinstance(item, dict) or not item.get("url") or not isinstance(item.get("index"), int):
            raise ValueError("blok satırı {url, index, choice} olmalı")
        if item.get("choice") not in labels.BLOCK_CHOICES:
            raise ValueError(f"geçersiz blok seçimi: {item.get('choice')!r}")
        key = f"{labels.path(str(item['url']))}#{item['index']}"
        out["blocks"][key] = item["choice"]
        _home_choice(homes, ("block", key), item)
        if item.get("paged") is not None:   # T30: this block's own pagination (var / yok)
            if not isinstance(item["paged"], bool):
                raise ValueError("blok sayfalaması true/false olmalı")
            out.setdefault("block_pages", {})[key] = item["paged"]
    added = body.get("added") or []
    if not isinstance(added, list) or len(added) > MAX_ADDED:
        raise ValueError(f"en çok {MAX_ADDED} adres eklenebilir")
    for item in added:
        if not isinstance(item, dict):
            raise ValueError("eklenen adres {name, url, meaning} olmalı")
        meaning = item.get("meaning")
        if meaning not in labels.MEANINGS or meaning == "ignore":
            raise ValueError(f"eklenen adresin anlamı geçersiz: {meaning!r}")
        raw = str(item.get("url") or "").strip()
        if not raw:
            raise ValueError("eklenen adres boş")
        try:
            url = netguard.check_url(common.site_url(site["base_url"], raw))
        except ValueError as exc:
            raise ValueError(f"adres reddedildi ({exc})") from None
        if not classify.same_site(url, site["base_url"]):
            raise ValueError("eklenen adres sitenin kendi adresinde olmalı")
        name = str(item.get("name") or "").strip()[:60] or labels.path(url)
        out["added"].append({"name": name, "url": labels.path(url), "meaning": meaning})
    result = engine.confirm_review(site_id, stage, out)
    if homes:
        try:
            result = {**(result or {}), "home_targets": apply_home_targets(site_id, homes)}
        except Exception as exc:   # the confirmation stands; the admin sees why the routing part failed
            result = {**(result or {}), "home_targets": {"error": str(exc)[:200]}}
    return result


def _home_choice(homes: dict, key: tuple, item: dict) -> None:
    from ..routing import home_targets
    if item.get("home") not in (None, ""):
        homes[key] = home_targets.clean(item["home"])


def apply_home_targets(site_id: str, homes: dict) -> dict:
    """T30 item 7: the "Ana ekranda" choices of a confirmed A1/A2 review -> categories + routing rules (match = the row's
    crawl origin: a home section's title, a listing link's name as genre/type/listing, a block's heading as listing)."""
    from ..routing import home_targets
    from ..sitemap import labels
    smap = map_store.draft(site_id) or {}
    pages = smap.get("pages") or {}
    secs = {str(s.get("id")): s for s in (pages.get("home") or {}).get("sections") or []}
    links = {labels.path(l["url"]): l for l in ((pages.get("home") or {}).get("nav") or {}).get("links") or [] if l.get("url")}
    blocks = {f"{labels.path(b['url'])}#{b.get('index')}": b for b in (pages.get("listing") or {}).get("blocks") or []
              if isinstance(b, dict) and b.get("url")}
    rows = []
    for (kind, key), home in homes.items():
        if kind == "section" and key in secs:
            rows.append({"match_kind": "section", "match_value": secs[key].get("title") or key, "home": home})
        elif kind == "link" and key in links:
            link = links[key]
            mk = {"genre": "genre", "type": "type"}.get(link.get("kind") or "", "listing")
            rows.append({"match_kind": mk, "match_value": link.get("name") or link["url"], "home": home})
        elif kind == "block" and key in blocks:
            rows.append({"match_kind": "listing", "match_value": blocks[key].get("heading") or key, "home": home})
    return home_targets.apply(site_id, rows)


# ---------------------------------------------------------------------------------------------- site note

def _a1_ran(site_id: str) -> bool:
    row = state.get(site_id, next(iter(engine.STAGES))) or {}
    return bool(row.get("runs")) and row.get("state") != "pending"


def save_notes(site_id: str, text: str) -> dict:
    """Store the site note (``ValueError`` over the limit). Nothing is reset or re-run: once the stages have run, the cards
    offer "Not değişti — aşamaları notla yeniden çalıştır" (:func:`rerun_with_notes`)."""
    _site(site_id)
    out = site_notes.save(site_id, text, applied=not _a1_ran(site_id))
    return {**out, "changed": notes_changed(site_id)}


def notes_changed(site_id: str) -> bool:
    return _a1_ran(site_id) and site_notes.changed(site_id)


def rerun_with_notes(site_id: str) -> dict:
    """The stages run again with the current note, in chain order: A1 (through its review card, existing labels kept) and
    every later stage that already ran goes back to ``pending`` and follows A1."""
    _site(site_id)
    first = next(iter(engine.STAGES))
    if _running(site_id, first):
        raise StageBusy("A1 çalışıyor; bitince tekrar dene")
    queued = engine.enqueue(site_id, first, fresh=True)
    reset = engine.reset_after(site_id, first)
    site_notes.mark_applied(site_id)
    state.add_message(site_id, first, "system", "Site notu değişti; aşamalar notla yeniden çalışıyor")
    return {**queued, "reset": reset}
