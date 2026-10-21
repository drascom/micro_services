"""A6 sources: the measurement that a sample episode / film really plays (``core.play.follow`` -> stream ->
``quick_check`` -> plausible length). No map fragment of its own (``pages.sources`` stays an empty allow-list = every
provider); the work is the measurement and the provider LIBRARY (K4: host knowledge only):

- a candidate host no provider reads -> ``provider_match`` dry run (LLM-free): ``add_host`` widens that recipe by itself
  (``recipes.update_problems`` gate) and the stage measures again; ``new_recipe`` -> ONE LLM call writes a recipe
  (validated, measured, then saved); ``needs_code`` -> ``engine_gap`` (host + why in the evidence);
- the chain stops on the site's own player page -> A5 runs again in repair mode (nothing site specific is written here);
- a recipe change must not break other active sites: one sample of each site that plays through it is measured again on a
  temporary provider pool (``registry.overlay``); the recipe is saved only when none broke (nothing to roll back).
- repair mode (T14, K8): recipes are only measured in the overlay; ``apply_extras`` saves them when the repair is applied.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import logging
from typing import Any, Optional

import yaml

from ...core.extract import htmlclip
from ...core.extract._util import host_key
from ...core.play import provider_match, transport
from ...core.play.providers import recipes, registry, store as recipe_store
from ...core.play.resolvers import player_page
from ... import db, settings
from .. import notes
from ..sitemap import adapters, classify, store as map_store
from . import playside, prompts_a4_a7, state
from .base import Finding, Measurement, Stage, StageCtx, judge, measurement

log = logging.getLogger("diziflix.stages")

WANT = 3
MAX_TRIES = 6
BODY_CLIP = 6000
#: injectable (tests): ``CHECK_STREAM(streams, locator) -> {ok, note, duration_min?}``
CHECK_STREAM = playside.check_stream


class _Recorder:
    """``provider_match`` transport that keeps the player page body it read (for the recipe prompt)."""

    def __init__(self) -> None:
        self.bodies: dict[str, str] = {}

    def fetch_impersonated(self, url: str, **kwargs: Any):
        body = transport.fetch_impersonated(url, **kwargs)
        text = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else str(body or "")
        self.bodies.setdefault(url, text)
        return body

    def __getattr__(self, name: str) -> Any:
        return getattr(transport, name)


def _play_brief(play: dict) -> dict:
    """The evidence form of a ``playtest`` result: ``{played, step, bytes, ms, error?, codec?, resolution?}``."""
    return {k: play[k] for k in ("played", "step", "bytes", "ms", "error", "codec", "resolution", "container", "duration_s")
            if play.get(k) not in (None, "")}


def _played(sample: dict) -> dict:
    ok = next((c for c in sample.get("candidates") or [] if c.get("ok")), {})
    stream = (sample.get("streams") or [{}])[0]
    return {"provider": str(stream.get("provider") or ""), "host": ok.get("host") or "", "player_url": ok.get("url") or ""}


def site_getter(site_id: str, site_map: dict):
    """``get(url, page_type) -> Page | None`` of another site (the engine's cached fetcher, own host only)."""
    from .engine import fetch_fn
    base = str(site_map.get("base_url") or "")
    fetch = fetch_fn(site_id, adapters.site_ctx(site_map))

    def get(url: str, page_type: str = "page"):
        if not classify.same_site(url, base):
            return None
        try:
            return fetch(url, page_type)
        except Exception:
            return None
    return get


def other_sites_broken(site_id: str, name: str, data: dict) -> list[dict]:
    """Active sites (other than ``site_id``) whose A6 sample played through recipe ``name`` (or a host it now claims):
    one sample each is measured again; the ones that do not play any more."""
    try:
        claim = recipes.RecipeProvider(data)
    except Exception:
        claim = None
    broken = []
    for site in map_store.list_sites():
        other = site["site_id"]
        if other == site_id or site.get("status") != "active":
            continue
        row = state.get(other, "A6") or {}
        played = [s for s in (row.get("measurement") or {}).get("samples") or [] if s.get("ok")]
        sample = next((s for s in played if s.get("provider") == name or s.get("recipe") == name
                       or (claim is not None and s.get("player_url") and claim.matches(s["player_url"]))), None)
        if sample is None:
            continue
        smap = map_store.active(other) or map_store.draft(other)
        get = site_getter(other, smap)
        pctx = playside.play_ctx(smap, get)
        page = get(sample["url"], "player")
        result = playside.follow_sample(pctx, sample["url"], html=page.html if page else None) if pctx else {"ok": False}
        if not result.get("ok"):
            broken.append({"site": other, "url": sample["url"], "error": result.get("error") or "oynamadı"})
    return broken


def _measured(data: dict):
    """``registry.overlay`` with ``data`` as an in-memory provider: measurements see it, the library (clients) does not."""
    return registry.overlay([recipes.RecipeProvider(data)])


def add_host_measured(site_id: str, rec: dict) -> dict:
    """The full ``add_host`` gate WITHOUT saving (repair mode, K8): ``{ok, recipe, note, data?}``; ``data`` is measured in
    the overlay and saved only when the repair is applied (:meth:`SourcesStage.apply_extras`)."""
    name = rec.get("recipe") or ""
    out: dict = {"ok": False, "recipe": name}
    data, problem = recipes.parse(rec.get("recipe_yaml") or "")
    try:
        active = recipe_store.load_recipe(name)
    except Exception as exc:
        return {**out, "note": f"tarif okunamadı: {exc}"}
    problems = [problem] if problem else recipes.validate_recipe(data) + recipes.update_problems(active, data)
    if problems:
        return {**out, "note": "; ".join(str(p) for p in problems[:2])}
    with _measured(data):
        broken = other_sites_broken(site_id, name, data)
    if broken:
        return {**out, "broken": broken,
                "note": "başka sitenin örneğini bozdu, kaydedilmedi: " + ", ".join(b["site"] for b in broken)}
    return {**out, "ok": True, "data": data, "note": "ölçüldü; onarım uygulanınca kaydedilir"}


def add_host_checked(site_id: str, rec: dict) -> dict:
    """``provider_match``'s ``add_host`` recommendation through the full gate (:func:`add_host_measured`), then saved.
    Shared by A6 (normal mode) and the problem core. ``{ok, recipe, note, broken?}``."""
    got = add_host_measured(site_id, rec)
    data = got.pop("data", None)
    if not got["ok"]:
        return got
    recipe_store.save_recipe(got["recipe"], data)
    return {**got, "note": "kaydedildi"}


def from_a5(ctx: StageCtx) -> list[str]:
    """A5's player pages (its handoff: pages with a candidate); an A5 that passed before the handoff carried them gives
    its measured pages with a candidate. The catalogue is not asked (the crawl fills it only after A6)."""
    handoff = ctx.handoff if (ctx.handoff or {}).get("from") == "A5" else {}
    urls = [u for u in handoff.get("urls") or [] if u]
    if not urls:
        row = state.get(ctx.site_id, "A5") or {}
        if row.get("state") in state.PASSED:
            urls = [x["url"] for x in (row.get("measurement") or {}).get("samples") or []
                    if x.get("url") and x.get("candidates")]
    return [u for u in dict.fromkeys(urls) if ctx.allowed(u)]


def stopped_at(res: dict, base: str) -> tuple[str, str]:
    """``(step, provider host)`` of one followed sample: ``page`` (not read) | ``candidate`` (no candidate, or only the
    site's own intermediate page) | ``provider`` (a foreign host no provider reads) | ``stream`` (a provider answered:
    no / a bad stream, or it plays)."""
    base_host = host_key(base)
    hosts = [h for h in playside.candidate_hosts(res) if h]
    foreign = [h for h in hosts if host_key("https://" + h + "/") != base_host]
    host = foreign[0] if foreign else ""
    if res.get("ok") or res.get("stage") == "stream":
        return "stream", host
    if res.get("stage") == "page":
        return "page", host
    if res.get("stage") == "discover" or not foreign:
        return "candidate", host
    pool = registry.providers()
    claimed = any(c.get("provider") or any(p.matches(c.get("url") or "") for p in pool)
                  for c in res.get("candidates") or [] if (c.get("host") or "") in foreign)
    return ("stream" if claimed else "provider"), host


def alternative_step(alt: dict, base: str) -> str:
    """How far one alternative got: ``stream`` (plays, or a provider answered without a good stream) | ``provider`` (a
    foreign host no provider reads) | ``candidate`` (stuck on the site's own hand-off / page)."""
    if alt.get("ok") or alt.get("streams"):   # streams came back: the step is the stream's (the best one), not the hand-off
        return "stream"
    host = alt.get("host") or ""
    if not host or host_key("https://" + host + "/") == host_key(base):
        return "candidate"
    claimed = alt.get("provider") or any(p.matches("https://" + host + "/") for p in registry.providers())
    return "stream" if claimed else "provider"


def _check_rank(check: dict) -> int:
    """Plays first, then how far a failed check got (``playside.check_one`` ``rank``)."""
    return 100 if check.get("ok") else int(check.get("rank") or 0)


def full_trace(results: list[dict]) -> list[dict]:
    """The LLM investigation's view of one measurement: per sample every step (page -> candidate / intermediate page ->
    provider -> stream) with its address, error (http ...), the playback test and the dropped non-media addresses."""
    out = []
    for res in results:
        alts = []
        for a in res.get("alternatives") or []:
            check = a.get("check") or {}
            alts.append({"label": a.get("label"), "resolver": a.get("resolver_type"), "host": a.get("host"),
                         "provider": a.get("provider") or "", "ok": bool(a.get("ok")),
                         "streams": [{"url": str(s.get("url") or "")[:200], "type": s.get("type")}
                                     for s in a.get("streams") or []][:3],
                         **({"error": str(a.get("error"))[:200]} if a.get("error") else {}),
                         **({"check": str(check.get("note") or "")[:240]} if check else {}),
                         **({"play": _play_brief(check["play"])} if isinstance(check.get("play"), dict) else {}),
                         **({"referer": a["referer"]} if a.get("referer") else {})})
        out.append({"page": res.get("url"), "ok": bool(res.get("ok")), "stage": res.get("stage"),
                    **({"error": str(res.get("error"))[:200]} if res.get("error") else {}),
                    "candidates": [{"url": str(c.get("url") or "")[:200], "ok": bool(c.get("ok")),
                                    **({"error": str(c.get("error"))[:160]} if c.get("error") else {}),
                                    **({"provider": c["provider"]} if c.get("provider") else {}),
                                    **({"dropped_non_media": c["dropped"][:6]} if c.get("dropped") else {})}
                                   for c in res.get("candidates") or []][:6],
                    "alternatives": alts[:8]})
    return out


def alternatives_table(results: list[dict], base: str) -> list[dict]:
    """Every source alternative over the measured samples, in playback order (plays more often first, then faster):
    ``[{key, label, host, kind, status, plays, samples, ms, per_sample: [{url, ok, step, ms}]}]``; status ``çalışıyor``
    (>= 2/3 of the samples), ``kısmen`` or ``çalışmıyor`` (kept: a backup, measured again later)."""
    n = len(results)
    table: dict[str, dict] = {}
    for res in results:
        for alt in res.get("alternatives") or []:
            key = alt.get("key") or ""
            if not key:
                continue
            row = table.setdefault(key, {"key": key, "label": alt.get("label") or alt.get("host") or "",
                                         "host": alt.get("host") or "", "kind": alt.get("resolver_type") or "",
                                         "plays": 0, "per_sample": [], "_ms": [], "_first": len(table)})
            if alt.get("ok") and alt.get("host"):
                row["host"] = alt["host"]
            if alt.get("needs_referer"):
                row["needs_referer"] = True   # an intermediate page opens only with its embedding page as the Referer
            policy = (alt.get("check") or {}).get("referer_policy")
            if alt.get("ok") and policy:
                row["referer_policy"] = policy   # what the media host accepted (refpolicy: page | none | origin)
            row["per_sample"].append({"url": res["url"], "ok": bool(alt.get("ok")), "step": alternative_step(alt, base),
                                      "ms": int(alt.get("ms") or 0), **({"error": str(alt.get("error") or "")[:120]}
                                                                         if not alt.get("ok") else {})})
            if alt.get("ok"):
                row["plays"] += 1
                row["_ms"].append(int(alt.get("ms") or 0))
    out = []
    for row in sorted(table.values(), key=lambda r: (-r["plays"], (sum(r["_ms"]) / len(r["_ms"])) if r["_ms"] else 1e9,
                                                       r["_first"])):
        ms = int(sum(row["_ms"]) / len(row["_ms"])) if row["_ms"] else 0
        status = "çalışıyor" if n and row["plays"] * 3 >= 2 * n else "kısmen" if row["plays"] else "çalışmıyor"
        out.append({k: v for k, v in row.items() if not k.startswith("_")} | {"samples": n, "ms": ms, "status": status})
    return out


def map_alternatives(table: list[dict]) -> list[dict]:
    """The map's ``pages.sources.alternatives``: order + status (the crawl writes one row per entry, playback tries them in
    this order)."""
    return [{"key": a["key"], "label": a["label"], "host": a["host"], "kind": a["kind"], "status": a["status"],
             "plays": f"{a['plays']}/{a['samples']}", "ms": a["ms"], **({"needs_referer": True} if a.get("needs_referer") else {}),
             **({"referer_policy": a["referer_policy"]} if a.get("referer_policy") else {})}
            for a in table]


HANDBACK_KEY = "stages:handback:{site}"
A6_CHAIN_KEY = "stages:a6_chain:{site}"   # the resolver chain A6's LLM found (A5 keeps it in front on every rerun)
HANDBACK_MAX = 20


def handback_key(failed_patch: Optional[dict], why: dict) -> str:
    """Fingerprint of one A6 -> A5 hand-back: the failing A5 rule (hash) + the error class (where the chain stopped)."""
    rule = json.dumps((((failed_patch or {}).get("pages") or {}).get("player") or {}).get("resolvers") or [], sort_keys=True,
                      default=str)
    cls = f"{why.get('kind') or 'page'}:{why.get('stage') or '-'}"
    return hashlib.sha1(rule.encode()).hexdigest()[:12] + ":" + cls


def handback_seen(site_id: str, mark: str) -> bool:
    return mark in (settings.get(HANDBACK_KEY.format(site=site_id), []) or [])


def handback_add(site_id: str, mark: str) -> None:
    key = HANDBACK_KEY.format(site=site_id)
    marks = [m for m in settings.get(key, []) or [] if m != mark] + [mark]
    settings.set(key, marks[-HANDBACK_MAX:])


def downstream_result(ev: dict, failed_patch: Optional[dict], why: dict) -> dict:
    """What A5 gets back from A6 ("A6 sonucu", not an admin hint): the failing rule, where the chain stopped, the error and
    the per-sample playback trace."""
    return {"stage": "A6", "summary": str(ev.get("summary") or "")[:200],
            "failed_rule": (((failed_patch or {}).get("pages") or {}).get("player") or {}).get("resolvers") or [],
            "stopped_at": why.get("stage") or "page", "error": str(why.get("error") or "")[:300], "url": why.get("url"),
            "trace": list(ev.get("trace") or [])[:3]}


class SourcesStage(Stage):
    id = "A6"
    page_type = "sources"
    title = "Kaynaklar"
    needs = ("A5",)
    consumes_handoff = False
    max_llm_rounds = 1
    never_skip = ("url",)

    # --- measurement ------------------------------------------------------------------------------------------------
    def _measure(self, ctx: StageCtx, site_map: dict) -> tuple[list[dict], int]:
        base = ctx.base_url
        meta = list(((site_map.get("meta") or {}).get("player") or {}).get("samples") or [])
        urls = [u for u in ctx.pool(list(ctx.samples) + from_a5(ctx) + meta) if classify.same_site(u, base)]
        pctx = playside.play_ctx(site_map, ctx.get)
        out, dropped = [], 0
        for url in urls[:MAX_TRIES]:
            if len(out) >= WANT:
                break
            page = ctx.get(url, "player")
            if page is None or pctx is None:
                out.append({"url": url, "ok": False, "stage": "page", "error": "getirilemedi", "candidates": []})
                continue
            res = playside.follow_sample(pctx, url, html=page.html, all_alternatives=True)
            if res["blocked"]:
                dropped += 1
                continue
            if res["ok"]:
                self._check_alternatives(res, url)
            out.append(res)
        return out, dropped

    @staticmethod
    def _check_alternatives(res: dict, url: str) -> None:
        """Every alternative that gave streams is checked on its own (``CHECK_STREAM``); the sample plays when one of them
        passes (no early stop: backups are measured too)."""
        alts = [a for a in res.get("alternatives") or [] if a.get("ok")]
        if not alts:
            check = CHECK_STREAM(res["streams"], url)
            res["ok"], res["check"] = bool(check.get("ok")), check
        else:
            best = None
            for alt in alts:
                check = CHECK_STREAM(alt.get("streams") or res["streams"], url)
                alt["ok"], alt["check"] = bool(check.get("ok")), check
                if not alt["ok"]:
                    alt["error"] = check.get("note") or "akış denetimi geçmedi"
                if best is None or _check_rank(check) > _check_rank(best):   # the best alternative speaks for the sample
                    best = check
            res["ok"], res["check"] = any(a["ok"] for a in alts), best or {}
        if not res["ok"]:
            res["error"] = (res.get("check") or {}).get("note") or "akış denetimi geçmedi"

    def _sample_rows(self, results: list[dict], base: str = "") -> list[dict]:
        rows = []
        for r in results:
            step, provider_host = stopped_at(r, base)
            note = (f"oynuyor ({(r.get('check') or {}).get('note', '')})" if r["ok"]
                    else f"{step}: {r.get('error') or '-'}")
            row = {"url": r["url"], "ok": r["ok"], "note": note[:200], "stage": r.get("stage"), "stopped_at": step,
                   "provider_host": provider_host, "hosts": sorted({h for h in playside.candidate_hosts(r) if h}),
                   "alternatives": [{"label": a.get("label"), "host": a.get("host"), "ok": bool(a.get("ok")),
                                     "step": alternative_step(a, base),
                                     **({"play": _play_brief(a["check"]["play"])}
                                        if isinstance((a.get("check") or {}).get("play"), dict) else {})}
                                    for a in r.get("alternatives") or []][:8]}
            play = (r.get("check") or {}).get("play")
            if isinstance(play, dict):   # the real playback test (played / step / bytes / codec ...)
                row["play"] = _play_brief(play)
            if r["ok"]:
                row.update(_played(r))
            rows.append(row)
        return rows

    # --- stage contract ---------------------------------------------------------------------------------------------
    def discover(self, ctx: StageCtx) -> Finding:
        page = {"providers": list(ctx.page("sources").get("providers") or [])}
        results, dropped = self._measure(ctx, ctx.map)
        base_host = host_key(ctx.base_url)
        fixes, recipe_needed, gaps, back, held = [], [], [], [], []
        seen: set = set()
        for res in results:
            if res["ok"]:
                continue
            if playside.own_page_failure(res):
                back.append({"url": res["url"], "error": res.get("error"), "kind": "page", "stage": res.get("stage")})
                continue
            for cand in res.get("candidates") or []:
                host = host_key(cand.get("url") or "") or host_key("https://" + str(cand.get("host") or "") + "/")
                if cand.get("ok") or not host or host in seen:
                    continue
                seen.add(host)
                if host == base_host:   # the site's own hand-off page: A5's business, never a library recipe
                    back.append({"url": res["url"], "error": f"sitenin kendi oynatıcı sayfası çözülemedi ({cand.get('error')})",
                                 "kind": "handoff"})
                    continue
                recorder = _Recorder()
                try:
                    found = provider_match.match(cand["url"], referer=res["url"], fetch_api=recorder)
                except Exception as exc:
                    log.warning("provider_match %s failed: %s", host, exc)
                    continue
                rec = found.get("recommendation") or {}
                action = rec.get("action")
                if action == "add_host" and ctx.repair:   # K8: measured in the overlay, saved when the repair is applied
                    got = add_host_measured(ctx.site_id, rec)
                    fixes.append({"host": host, "action": "add_host", "recipe": got["recipe"], "applied": False,
                                  "pending": got["ok"], "note": got["note"]})
                    if got["ok"]:
                        held.append(got["data"])
                elif action == "add_host":
                    fixes.append(self._add_host(ctx, rec, host))
                elif action == "use_provider":
                    fixes.append({"host": host, "action": "use_provider", "provider": rec.get("provider"), "applied": False,
                                  "note": "kütüphane bu host'u zaten okuyor; hata geçici olabilir"})
                elif action == "new_recipe":
                    body = next(iter(recorder.bodies.values()), "")
                    recipe_needed.append({"host": host, "player_url": cand["url"], "page_url": res["url"],
                                          "page": {k: v for k, v in (found.get("page") or {}).items()},
                                          "note": rec.get("note"), "body": htmlclip.clean_html(body, BODY_CLIP) if body else ""})
                else:
                    gaps.append({"host": host, "player_url": cand["url"], "why": rec.get("note") or "kod gerekiyor"})
        ok = sum(1 for r in results if r["ok"])
        table = alternatives_table(results, ctx.base_url)
        if table:
            page["alternatives"] = map_alternatives(table)
        evidence = {"trace": full_trace(results), "alternatives": table[:12], "tried": [r["url"] for r in results], "looked_for": "örnek bölüm/film sayfasından akışa giden zincir",
                    "samples": [r["url"] for r in results], "initial": self._sample_rows(results, ctx.base_url), "dropped": dropped,
                    "from_a5": len(from_a5(ctx)),
                    "fixes": fixes, "recipe_needed": recipe_needed[:2], "gaps": gaps, "back_to_a5": back,
                    "summary": f"{ok}/{len(results)} örnek oynadı" + (f"; {sum(1 for f in fixes if f.get('applied'))} tarif "
                                                                         f"kendiliğinden genişletildi" if fixes else "")}
        patch: dict = {"pages": {"sources": page}}
        if held:
            patch["_hosts"] = held
        return Finding(patch, evidence, 0.8)

    def _add_host(self, ctx: StageCtx, rec: dict, host: str) -> dict:
        name = rec.get("recipe") or ""
        out = {"host": host, "action": "add_host", "recipe": name, "applied": False}
        got = add_host_checked(ctx.site_id, rec)
        if not got["ok"]:
            out["note"] = got["note"]
            return out
        out["applied"] = True
        notes.add("info", f"{ctx.site_id} A6: '{name}' provider tarifine {host} host'u eklendi (LLM'siz, ölçümle)",
                  site_id=ctx.site_id, ref_kind="recipe", ref_id=name)
        return out

    def verify(self, ctx: StageCtx, patch: dict) -> Measurement:
        site_map = ctx.merged({k: v for k, v in patch.items() if k != "_player"})
        player_extra = list((patch.get("_player") or {}).get("resolvers") or []) if isinstance(patch.get("_player"), dict) else []
        if player_extra:   # the LLM's chain measured in front of A5's resolvers
            page = site_map.setdefault("pages", {}).setdefault("player", {})
            page["resolvers"] = self._player_resolvers(page.get("resolvers") or [], player_extra)
        recipe = patch.get("_recipe") if isinstance(patch.get("_recipe"), dict) else None
        held = [h for h in patch.get("_hosts") or [] if isinstance(h, dict)]
        overlay = [recipes.RecipeProvider(d) for d in held + ([recipe] if recipe else [])]
        # an LLM recipe is measured on a TEMPORARY pool (``overlay``): it reaches the library only after every check passed,
        # so clients never resolve through an unverified recipe and a crash mid-measurement leaves nothing to undo
        with (registry.overlay(overlay) if overlay else contextlib.nullcontext()):
            results, dropped = self._measure(ctx, site_map)
        n = len(results)
        values: dict = {"samples_count": n}
        table = alternatives_table(results, ctx.base_url)
        if n:
            values["playable_ratio"] = round(sum(1 for r in results if r["ok"]) / n, 3)
            values["alt_playable_ratio"] = round(max([a["plays"] for a in table] or [0]) / n, 3) if table \
                else values["playable_ratio"]
        if table:   # the measured order + status go into the map with the patch (crawl rows, playback order)
            patch.setdefault("pages", {}).setdefault("sources", {})["alternatives"] = map_alternatives(table)
        extra = None
        notes_ = [f"{sum(1 for r in results if r['ok'])}/{n} örnek oynadı"]
        if table:
            notes_.append(f"{len(table)} kaynak alternatifi: " + ", ".join(f"{a['label']} {a['plays']}/{n}" for a in table[:5])
                          + (f"; çalışmayan {sum(1 for a in table if a['status'] == 'çalışmıyor')} yedek olarak tutuldu"
                             if any(a["status"] == "çalışmıyor" for a in table) else ""))
        got = from_a5(ctx)
        notes_.append(f"önceki aşamadan gelen (A5): {len(got)} oynatıcı sayfası" if got
                      else "önceki aşamadan (A5) oynatıcı sayfası gelmedi")
        if dropped:
            notes_.append(f"{dropped} engelli örnek paydadan düştü")
        if recipe:
            extra = {"other_sites_ok": {"bound": 1, "op": "min"}}
            base_ok = all(c["ok"] for c in judge(site_map, "sources", values) if not c.get("optional"))
            broken = []
            if base_ok:
                with _measured(recipe):
                    broken = other_sites_broken(ctx.site_id, recipe["name"], recipe)
            values["other_sites_ok"] = int(not broken)
            if not base_ok or broken:
                notes_.append("yeni tarif kaydedilmedi" + (": başka sitenin örneği bozuldu (" + ", ".join(b["site"] for b in broken)
                                                           + ")" if broken else ""))
            elif ctx.repair:   # K8: the library changes only when the repair is applied (apply_extras)
                notes_.append(f"yeni provider tarifi ölçüldü: {recipe['name']} (onarım uygulanınca kaydedilir)")
            else:
                recipe_store.save_recipe(recipe["name"], recipe)
                notes_.append(f"yeni provider tarifi eklendi: {recipe['name']}")
                notes.add("info", f"{ctx.site_id} A6: yeni provider tarifi eklendi: {recipe['name']} (LLM, ölçümle doğrulandı)",
                          site_id=ctx.site_id, ref_kind="recipe", ref_id=recipe["name"])
        checks = judge(site_map, "sources", values, extra=extra)
        if player_extra:
            passed = all(c["ok"] for c in checks if not c.get("optional"))
            patch["_player"] = {"resolvers": player_extra, "merged": site_map["pages"]["player"]["resolvers"]}
            if not passed:
                notes_.append("LLM'in kaynak zinciri (çözücü) ölçümü geçmedi; haritaya yazılmadı")
            elif ctx.repair:
                notes_.append("LLM'in kaynak zinciri ölçüldü (onarım uygulanınca oynatıcı haritasına yazılır)")
            else:
                self._save_player(ctx.site_id, site_map["pages"]["player"]["resolvers"], player_extra)
                notes_.append(f"LLM'in kaynak zinciri oynatıcı haritasına eklendi ({len(player_extra)} çözücü, önde)")
        rows = self._sample_rows(results, ctx.base_url)
        if recipe:
            for row in rows:
                if row.get("ok"):
                    row["recipe"] = recipe["name"]
        return measurement(checks, rows, notes_)

    def escalate(self, ctx: StageCtx, finding: Finding, m: Measurement) -> Optional[tuple]:
        ev = finding.evidence or {}
        own_page = [b for b in ev.get("back_to_a5") or [] if b.get("kind", "page") == "page"]
        if own_page:   # no candidate on the site's own player page: A5 first (a recipe for another alternative can wait)
            from . import engine
            if state.state_of(ctx.site_id, "A5") in state.PASSED:
                why = own_page[0]
                failed = (state.get(ctx.site_id, "A5") or {}).get("patch")
                mark = handback_key(failed, why)
                if not handback_seen(ctx.site_id, mark):   # at most ONE hand-back per failing rule + error class
                    handback_add(ctx.site_id, mark)
                    engine.enqueue(ctx.site_id, "A5", created_by="repair", trigger=engine.current_trigger(ctx.site_id, self.id),
                                   failed_patch=failed, downstream=downstream_result(ev, failed, why))
                    state.add_message(ctx.site_id, self.id, "system",
                                      "Sorun sitenin oynatıcı sayfasında: A5 onarım için yeniden çalışıyor (A6 sonucuyla)")
                    return ("needs_human", "")
                state.add_message(ctx.site_id, self.id, "system", "Aynı kural aynı hatayla A5'e zaten bir kez geri verildi: "
                                                                  "A5 yeniden çalıştırılmıyor, LLM kaynak incelemesi başlıyor")
                return None
        if ev.get("recipe_needed"):
            return None                                   # one LLM call writes the recipe
        if ev.get("gaps"):
            first = ev["gaps"][0]
            return ("engine_gap", f"{first['host']}: desteklenmeyen gömme ({str(first.get('why') or '')[:160]})")
        # 0 plays or < 2/3: the LLM investigation ALWAYS runs (full trace, probe); "Yardım gerekiyor" only after it failed
        state.add_message(ctx.site_id, self.id, "system", "LLM'siz ölçüm geçmedi: LLM kaynak incelemesi başlıyor")
        return None

    def parse_reply(self, ctx: StageCtx, data: Any) -> Optional[tuple]:
        if isinstance(data, dict) and set(data) == {"result"} and isinstance(data["result"], dict):
            data = data["result"]   # investigation answer: result: {recipe: {...}} / {resolvers: [...]} (or the recipe itself)
        if isinstance(data, dict) and isinstance((data.get("pages") or {}).get("player"), dict):
            data = data["pages"]["player"]
        if isinstance(data, dict) and "resolvers" in data and "name" not in data:
            return self._player_reply(ctx, data.get("resolvers"))
        if isinstance(data, dict) and set(data) == {"recipe"}:
            data = data["recipe"]
        if not isinstance(data, dict):
            return None, "tarif bir eşleme değil"
        data = {k: v for k, v in data.items() if k != "updated_at"}
        problems = recipes.validate_recipe(data)
        name = data.get("name")
        if isinstance(name, str) and name in recipe_store.recipe_names():
            problems.append(f"'{name}' adlı tarif zaten var (yeni tarif yeni ad alır)")
        text = yaml.safe_dump(data, allow_unicode=True).lower()
        label = host_key(ctx.base_url).split(".")[0]
        if (len(label) >= 4 and label in text) or (len(ctx.site_id) >= 4 and ctx.site_id.lower() in text):
            problems.append("tarif host'a aittir: site adı/yolu içeremez")
        if problems:
            return None, "tarif geçersiz: " + "; ".join(str(p) for p in problems[:3])
        page = {"providers": list(ctx.page("sources").get("providers") or [])}
        return {"pages": {"sources": page}, "_recipe": copy.deepcopy(data)}, None

    investigates = True   # the recipe round may ask for investigation steps on the provider page first
    investigate_steps = 6          # A6 source hunt: 6 steps x 6 actions per run (A5: investigate.MAX_STEPS)
    investigate_chars = 60_000

    def _player_reply(self, ctx: StageCtx, items: Any) -> tuple:
        """``result: {resolvers: [...]}``: the site's chain (page -> intermediate page -> stream) as player resolvers. They
        are measured IN FRONT of A5's resolvers (``_player``); the alternatives that play win the order, the map's
        ``pages.player`` gets them only when the measurement passed (repair mode: when the repair is applied)."""
        from ...core.play import resolvers as play_resolvers
        items = [items] if isinstance(items, dict) else items
        if not isinstance(items, list) or not items:
            return None, "resolvers bir liste olmalı"
        problems = play_resolvers.validate(items)
        if problems:
            return None, "çözücü geçersiz: " + "; ".join(str(p) for p in problems[:3])
        page = {"providers": list(ctx.page("sources").get("providers") or [])}
        return {"pages": {"sources": page}, "_player": {"resolvers": copy.deepcopy(items)}}, None

    @staticmethod
    def _player_resolvers(current: list, extra: list) -> list:
        """The LLM's resolvers first, then A5's (an identical item once)."""
        out, seen = [], set()
        for item in list(extra) + list(current or []):
            key = yaml.safe_dump(item, sort_keys=True)
            if key not in seen:
                seen.add(key)
                out.append(copy.deepcopy(item))
        return out

    @staticmethod
    def _save_player(site_id: str, resolvers_: list, chain: Optional[list] = None) -> None:
        """The chain the LLM found in A6 passed: ``pages.player.resolvers`` of the draft AND A5's own row (patch + handoff)
        in one transaction (one source); the chain is also kept for A5 (``A6_CHAIN_KEY``) so a later A5 rerun / chain reset
        never drops it (``a5_player.discover`` puts it first)."""
        with db.transaction():
            data = map_store.draft(site_id)
            data.setdefault("pages", {}).setdefault("player", {})["resolvers"] = copy.deepcopy(resolvers_)
            map_store.save_draft(site_id, data)
            row = state.get(site_id, "A5") or {}
            if row:
                patch = copy.deepcopy(row.get("patch") or {})
                patch.setdefault("pages", {}).setdefault("player", {})["resolvers"] = copy.deepcopy(resolvers_)
                fields: dict = {"patch": patch}
                if isinstance(row.get("handoff"), dict):
                    fields["handoff"] = {**row["handoff"], "resolvers": copy.deepcopy(resolvers_)}
                state.update(site_id, "A5", **fields)
            if chain:
                settings.set(A6_CHAIN_KEY.format(site=site_id), copy.deepcopy(chain))

    def on_passed(self, ctx: StageCtx, patch: dict, m: Measurement) -> None:
        from ..crawl import plan as crawl_plan, run as crawl_run   # T11: playback rows through the crawl
        site = map_store.get(ctx.site_id) or {}
        if site.get("status") == "active" and crawl_plan.rules_changed(ctx.site_id, regate=True):
            crawl_run.kick(ctx.site_id)

    def apply_extras(self, site_id: str, patch: dict) -> None:
        """Repair mode: the recipes measured in the overlay (``_hosts`` widenings, an LLM ``_recipe``) reach the library."""
        recipe = patch.get("_recipe") if isinstance(patch.get("_recipe"), dict) else None
        merged = (patch.get("_player") or {}).get("merged") if isinstance(patch.get("_player"), dict) else None
        if merged:
            self._save_player(site_id, merged, (patch.get("_player") or {}).get("resolvers"))
        for data in [h for h in patch.get("_hosts") or [] if isinstance(h, dict)] + ([recipe] if recipe else []):
            recipe_store.save_recipe(data["name"], data)
            notes.add("info", f"{site_id} A6: provider tarifi '{data['name']}' onarımla kaydedildi (ölçümle doğrulandı)",
                      site_id=site_id, ref_kind="recipe", ref_id=data["name"])

    def meta_for(self, ctx: StageCtx, patch: dict, m: Measurement) -> dict:
        return {}

    def handoff(self, ctx: StageCtx, patch: dict, m: Measurement) -> dict:
        """A7's input: the pages that played (gate: must not be caught) + the A3/A4 title pages (search: a title to look
        for when the catalogue has none yet)."""
        meta = ctx.map.get("meta") or {}
        titles = list((meta.get("title") or {}).get("samples") or []) + list((meta.get("episodes") or {}).get("samples") or [])
        return {"urls": [x["url"] for x in m.samples if x.get("ok") and x.get("url")][:5],
                "title_urls": list(dict.fromkeys(titles))[:5]}

    def result_line(self, patch: Optional[dict]) -> str:
        return "örnekler oynuyor (ayrıntı ölçümde)"

    def prompt(self, ctx: StageCtx, finding: Finding, hint: str, last: Optional[Measurement]) -> str:
        if (finding.evidence or {}).get("recipe_needed"):
            return prompts_a4_a7.recipe(ctx, finding, hint, last, player_page.FETCH_TIMEOUT)
        return prompts_a4_a7.source_hunt(ctx, finding, hint, last, from_a5(ctx))
