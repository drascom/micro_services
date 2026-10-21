"""A5 player: on an episode / film page, how the site points at its video host (the resolver items of
``pages.player.resolvers``, K4: site knowledge as PARAMETERS), the player page address pattern and ``title_link`` (episode
page -> its title page). Placeholder ("not public") samples leave the denominator and a spare sample is taken."""
from __future__ import annotations

import json
from typing import Optional

from selectolax.parser import HTMLParser

from ...core.extract import parse, player as x_player, urlpatterns
from ...core.extract._util import SAFE_CLASS, absolute, doc_base, repeat_fixed
from ...core.play import candidates as play_candidates
from ...core.play.providers import registry
from ..sitemap import classify, meta as map_meta
from . import common, hints, playside, prompts_a4_a7, state
from .a4_episodes import series_episodes, series_samples, title_types
from .base import Finding, Measurement, Stage, StageCtx, judge, measurement

WANT = 3          # measured samples (2 episodes + 1 film when the site has both)
MAX_TRIES = 8     # pages looked at to find WANT usable ones
#: T29/T32: discovery plays its LLM-free rules first; when one sample plays, the run uses no LLM (no read, no choice, no
#: investigation). (tests of the older LLM flows switch it off)
LLM_FREE_FIRST = True


def _meta(site_map: dict, page_type: str) -> list[str]:
    return list(((site_map.get("meta") or {}).get(page_type) or {}).get("samples") or [])


def episode_section(site_map: dict) -> bool:
    home = (site_map.get("pages") or {}).get("home") or {}
    return any(s.get("target") == "episode" and not s.get("ignore") for s in home.get("sections") or [])


def pick_pages(ctx: StageCtx, site_map: dict) -> tuple[list[str], set]:
    """``(ordered sample addresses, episode addresses)``: user samples, 2 episodes + 1 film, then spares."""
    base = ctx.base_url
    types = title_types(ctx.site_id)   # T30: A3's types (provenance / page content) for a mixed site
    titles = list(dict.fromkeys(_meta(site_map, "title") + list(types)))
    series = _meta(site_map, "episodes") or series_samples(site_map, titles, base, types)
    per_series = []
    if (site_map.get("pages") or {}).get("episodes") and not ctx.own_only:
        for url in series[:3]:
            eps, _info = series_episodes(ctx.get, site_map, url)
            if eps:
                per_series.append([eps[-1]["url"], eps[0]["url"]] if len(eps) > 1 else [eps[0]["url"]])
    episodes = [lst[i] for i in range(2) for lst in per_series if len(lst) > i]
    known = [u for u in ((state.get(ctx.site_id, "A1") or {}).get("handoff") or {}).get("episode_cards") or []]
    if known and episodes:   # a doubled path segment (/a/a/) that only the corrected form of the known episode shape fits
        shapes = [c.pattern_regex for c in urlpatterns.cluster(known, base)]
        episodes = [repeat_fixed(u, shapes) for u in episodes]
    films = [u for u in titles if (types.get(u) or classify.title_type(site_map, u)) == "movie"]
    user = [u for u in ctx.samples if classify.same_site(u, base)]
    if episodes and films:
        first = episodes[:2] + films[:1]
    else:
        first = (episodes or films)[:WANT]
    ordered = []
    for url in ctx.pool(user + first + episodes + films):   # T25: excluded / (own only) non-admin samples never come back
        if url not in ordered and classify.same_site(url, base):
            ordered.append(url)
    return ordered, set(episodes)


def type_line(per_kind: dict) -> str:
    """"bölüm: 2/2 aday, 1/1 oynadı; film: 1/1 aday, 0/1 oynadı" (T30 mixed sites)."""
    names = {"episode": "bölüm", "movie": "film"}
    return "; ".join(f"{names.get(k, k)}: {v['found']}/{v['pages']} aday, {v['played']}/{v['tried']} oynadı"
                     for k, v in sorted(per_kind.items()))


def _title_link(pages: list, title_pattern: str, base: str) -> Optional[dict]:
    """``{selector, attr: href}`` whose first match on every episode page is a title page address."""
    if not pages or not title_pattern:
        return None
    first_url, first_html = pages[0]
    tree = HTMLParser(first_html)
    options: list[str] = []
    first_base = doc_base(tree, first_url)
    for node in tree.css("a[href]"):
        href = absolute(first_base, node.attributes.get("href"))
        if not href or not classify.pattern_matches(title_pattern, href, base):
            continue
        for cls in (node.attributes.get("class") or "").split():
            if SAFE_CLASS.match(cls):
                options.append(f"a.{cls}")
        parent = node.parent
        if parent is not None and parent.tag not in ("body", "html"):
            for cls in (parent.attributes.get("class") or "").split():
                if SAFE_CLASS.match(cls):
                    options.append(f"{parent.tag}.{cls} a")
        if len(options) >= 6:
            break
    for selector in dict.fromkeys(options):
        rule = {"selector": selector, "attr": "href"}
        if all(_link_of(html, url, rule, title_pattern, base) for url, html in pages):
            return rule
    return None


def _link_of(html: str, url: str, rule: dict, title_pattern: str, base: str) -> Optional[str]:
    try:
        value = parse.parse_detail(html, {"u": {"selector": rule["selector"], "attr": rule.get("attr") or "href"}}).get("u")
    except Exception:
        return None
    link = absolute(doc_base(html, url), value) if value else None
    return link if link and classify.pattern_matches(title_pattern, link, base) else None


def _pattern(urls: list[str], base: str) -> Optional[str]:
    regexes = [c.pattern_regex for c in urlpatterns.cluster(urls, base)]
    # several shapes (one of them often a single sample = a literal address): one generalised pattern first
    merged = urlpatterns.merged_pattern(urls, base) if len(regexes) > 1 else None
    pattern = merged or (common.union_regex(regexes) if regexes else None)
    if pattern and all(classify.pattern_matches(pattern, u, base) for u in urls):
        return pattern
    return None


def _patterns(urls: list[str], base: str) -> list[str]:
    """T30: the player page's address shapes; several when one merged pattern cannot cover them (``url_patterns``)."""
    pats = urlpatterns.merged_patterns(urls, base)
    return pats if pats and all(any(classify.pattern_matches(p, u, base) for p in pats) for u in urls) else []


def note_resolvers(ctx: StageCtx, kept: list, resolvers: list) -> list[str]:
    """Site note "oynatıcı .x içinde" -> an ``iframe`` resolver over that container, put FIRST when it finds a player
    address on a sample page (``resolvers`` is changed in place). Returns the card lines."""
    sels = hints.selector_list([ctx.note], "player", pages=("episode", "player", "title")) if ctx.note else []
    used = []
    for sel, tag in sels:
        selector = sel if tag == "iframe" else f"{sel} iframe[src]"
        hits = 0
        for _url, page, _c in kept:
            try:
                hits += any(n.attributes.get("src") for n in HTMLParser(page.html).css(selector))
            except Exception:
                break
        if not hits:
            used.append(f"oynatıcı seçicisi {selector} örneklerde adres bulmadı")
            continue
        item = {"type": "iframe", "selector": selector, "attr": "src"}
        if item in resolvers:
            resolvers.remove(item)
        resolvers.insert(0, item)
        used.append(f"oynatıcı kuralı {selector} ({hits} sayfada)")
        break
    return used


def _alternatives(kept: list) -> list[dict]:
    """The source alternatives of the sample pages, merged by label + host + resolver: ``[{label, host, kind, how, decode,
    found_by, resolver, pages}]`` (the admin card's table; ``resolver`` None = undecoded)."""
    merged: dict[str, dict] = {}
    for url, _page, usable in kept:
        for alt in x_player.alternatives(usable):
            key = json.dumps([alt["label"], alt["host"], alt["resolver"]], sort_keys=True)
            row = merged.setdefault(key, {k: alt[k] for k in ("label", "host", "kind", "how", "decode", "found_by", "resolver")}
                                    | {"pages": 0})
            row["pages"] += 1
    return sorted(merged.values(), key=lambda a: (a["resolver"] is None, -a["pages"]))


#: A5 compares the LLM-free and the LLM resolvers by the real playback test when they differ (False = off)
PLAY_COMPARE = True
COMPARE_SAMPLES = 2
COMPARE_SECONDS = 20.0


def _played_items(ctx: StageCtx, resolvers: list, urls: list) -> dict:
    """``{resolver index: samples played}`` of ``resolvers`` on ``urls``: each sample is followed to the end (every
    alternative) and every alternative that gave streams is PLAYED (``a6_sources.CHECK_STREAM``: the playback test)."""
    from . import a6_sources
    site_map = ctx.merged({"pages": {"player": {**ctx.page("player"), "resolvers": resolvers}}})
    pctx = playside.play_ctx(site_map, ctx.get)
    played: dict = {}
    if pctx is None:
        return played
    for url in urls:
        page = ctx.get(url, "player")
        if page is None:
            continue
        res = playside.follow_sample(pctx, url, html=page.html, seconds=COMPARE_SECONDS, all_alternatives=True)
        for alt in res.get("alternatives") or []:
            index = alt.get("resolver")
            if not alt.get("ok") or not isinstance(index, int):
                continue
            check = a6_sources.CHECK_STREAM(alt.get("streams") or [], url)
            if check.get("ok"):
                played[index] = played.get(index, 0) + 1
    return played


def a6_chain(site_id: str) -> list:
    """The resolver chain A6's LLM found and measured (``a6_sources._save_player``), valid items only."""
    from ... import settings
    from ...core.play import resolvers as play_resolvers
    from .a6_sources import A6_CHAIN_KEY
    items = [i for i in settings.get(A6_CHAIN_KEY.format(site=site_id), []) or [] if isinstance(i, dict)]
    return [i for i in items if not play_resolvers.validate([i])]


def play_sample(pctx, url: str, html: str, base: str = "") -> dict:
    """A6's playback test on one A5 sample (candidate -> provider -> stream -> ``a6_sources.CHECK_STREAM``), briefly:
    ``{url, ok, reached, note}``. ``ok`` = a stream played; ``reached`` = played, or the chain left the site's own pages
    and reached a video host (a host no provider reads yet / a bad stream is A6's provider-library business, not A5's)."""
    from . import a6_sources
    try:
        res = playside.follow_sample(pctx, url, html=html, seconds=COMPARE_SECONDS, all_alternatives=True)
    except Exception as exc:
        return {"url": url, "ok": False, "reached": False, "note": f"oynatma ölçülemedi ({type(exc).__name__})"}
    alts = [a for a in res.get("alternatives") or [] if a.get("ok")]
    streams = [a.get("streams") or [] for a in alts] or ([res["streams"]] if res.get("ok") and res.get("streams") else [])
    last = ""
    for group in streams:
        check = a6_sources.CHECK_STREAM(group, url)
        if check.get("ok"):
            return {"url": url, "ok": True, "reached": True, "note": "oynadı"}
        last = str(check.get("note") or "akış oynamadı")
    step, host = a6_sources.stopped_at(res, base)
    reached = step in ("provider", "stream") and bool(host or streams)
    why = last or res.get("error") or res.get("stage") or "akış yok"
    where = f"sağlayıcıya vardı ({host or 'akış'}), oynamadı" if reached else "sitenin kendi sayfasında kaldı"
    return {"url": url, "ok": False, "reached": reached, "note": f"{where}: {str(why)[:120]}"}


def compare_by_playback(ctx: StageCtx, finding: Finding, patch: dict) -> tuple[dict, list]:
    """User decision (trust the LLM more, measure by playing): when the LLM's resolvers differ from the LLM-free ones or
    from the CURRENT (earlier passed) A5 rule, all of them are played on the samples; the items that play are kept (the
    LLM's first, then the LLM-free ones, then the current ones: a playing rule is never replaced by one that does not
    play). Nothing plays -> the LLM patch unchanged and the compare says so ("geçmiş sayılmaz"): only A5's own measurement
    (``reached_samples``: the chain must at least leave the site's own pages) can still pass it."""
    page = ((patch or {}).get("pages") or {}).get("player") or {}
    llm = [i for i in page.get("resolvers") or [] if isinstance(i, dict)]
    found = [i for i in ((((finding.patch or {}).get("pages") or {}).get("player") or {}).get("resolvers") or [])
             if isinstance(i, dict)]
    current = [i for i in ctx.page("player").get("resolvers") or [] if isinstance(i, dict)]
    key = lambda items: json.dumps(items, sort_keys=True)
    rivals = [(name, items) for name, items in (("LLM'siz", found), ("mevcut", current))
              if items and key(items) != key(llm)]
    rivals = [r for i, r in enumerate(rivals) if key(r[1]) not in [key(x[1]) for x in rivals[:i]]]
    if not PLAY_COMPARE or not llm or not rivals:
        return patch, []
    urls = list((finding.evidence or {}).get("samples") or [])[:COMPARE_SAMPLES]
    if not urls:
        return patch, []
    try:
        llm_ok = _played_items(ctx, llm, urls)
        others = [(name, items, _played_items(ctx, items, urls)) for name, items in rivals]
    except Exception as exc:   # a measurement failure never loses the LLM answer
        return patch, [f"oynatma karşılaştırması yapılamadı: {type(exc).__name__}"]
    keep = [llm[i] for i in sorted(llm_ok)]
    for _name, items, ok in others:
        keep += [items[i] for i in sorted(ok) if items[i] not in keep]
    note = (f"oynatma karşılaştırması ({len(urls)} örnek): LLM {len(llm_ok)}/{len(llm)} kural oynadı, "
            + ", ".join(f"{name} {len(ok)}/{len(items)}" for name, items, ok in others))
    if not keep:
        return patch, [note + "; hiçbiri oynamadı (geçmiş sayılmaz)"]
    out = json.loads(json.dumps(patch))
    out["pages"]["player"]["resolvers"] = keep
    return out, [note + f"; oynayan {len(keep)} kural tutuldu"]


class PlayerStage(Stage):
    id = "A5"
    page_type = "player"
    title = "Oynatıcı"
    needs = ("A4",)
    consumes_handoff = False
    never_skip = ("url", "title", "resolvers")
    investigates = True   # LLM rounds may ask the server for investigation steps first (control.stages.investigate)

    def discover(self, ctx: StageCtx) -> Finding:
        base = ctx.base_url
        ordered, episode_urls = pick_pages(ctx, ctx.map)
        evidence: dict = {"tried": ordered[:MAX_TRIES], "looked_for": "oynatıcı çerçevesi / sekme / bağlantı (video sağlayıcısı adayı)"}
        pool = registry.providers()
        kept, placeholders, no_rule = [], [], []
        for url in ordered[:MAX_TRIES]:
            if len(kept) >= WANT:
                break
            page = ctx.get(url, "player")
            if page is None:
                continue
            final = page.final_url or url
            cands = x_player.player_candidates(page.html, final, base, providers=pool)
            usable = [c for c in cands if not c["trailer"] and not c["placeholder"]]
            if not usable:
                marker = playside.placeholder_marker(page.html)
                if marker:
                    placeholders.append({"url": url, **marker})
                    continue
            kept.append((url, page, usable))
            no_rule += [{"page": url, "url": c["url"], "how": c["how"], "provider": c["provider"]}
                        for c in usable if not c["resolver"]]
        # EVERY source alternative needs its resolver chain (backup sources): all distinct resolver items, the ones that
        # cover more sample pages first (a site-wide tab list before a one-page leftover)
        cover: dict[str, list] = {}
        for url, _page, usable in kept:
            for c in usable:
                if c["resolver"]:
                    key = json.dumps(c["resolver"], sort_keys=True)
                    cover.setdefault(key, [c["resolver"], set()])[1].add(url)
        resolvers = [item for item, _pages in sorted(cover.values(), key=lambda v: -len(v[1]))]
        alts = _alternatives(kept)
        undecoded = [a for a in alts if not a["resolver"]]
        played_free = self._llm_free_played(ctx, kept, resolvers) if resolvers and LLM_FREE_FIRST else None
        if played_free:   # T29/T32: the LLM-free rules already play: no LLM read / choice / investigation for this run
            evidence["llm_free_played"] = played_free
            undecoded = []
        if undecoded:
            evidence["ask_llm"] = (f"{len(undecoded)} kaynak alternatifi bulundu ama çözülemedi: "
                                   + ", ".join(f"{a['label']} ({a['how']})" for a in undecoded[:3]))
        evidence["alternatives"] = alts[:12]
        used = note_resolvers(ctx, kept, resolvers)
        if used:
            evidence["note_used"] = used
        chain = a6_chain(ctx.site_id)
        if chain:   # the chain A6's LLM found and measured stays in front: an A5 rerun / chain reset never reverts it
            for item in reversed(chain):
                if item in resolvers:
                    resolvers.remove(item)
                resolvers.insert(0, item)
            evidence["a6_chain"] = f"A6'nın bulduğu kaynak zinciri korunuyor ({len(chain)} çözücü, önde)"
        urls = [u for u, _p, _c in kept]
        rule: dict = {}
        pattern = _pattern(urls, base)
        pats = _patterns(urls, base)
        if len(pats) > 1 and (not pattern or "|" in pattern and pattern not in pats):
            rule["url_patterns"] = pats   # several shapes, no honest single pattern (T30)
        elif pattern:
            rule["url_pattern"] = pattern
        if resolvers:
            rule["resolvers"] = resolvers
        title_pattern = classify.page_regex(ctx.page("title")) or ""
        eps = [(u, p.html) for u, p, _c in kept if u in episode_urls or not classify.title_key(ctx.map, u)]
        link = _title_link(eps, title_pattern, base)
        if link:
            rule["title_link"] = link
        evidence.update({"samples": urls, "placeholders": placeholders, "no_rule": no_rule[:8],
                         "found": {"resolvers": resolvers, "title_link": link},
                         "summary": f"{len(urls)} oynatıcı sayfası, {len(resolvers)} kural"
                                    + (f", {len(placeholders)} yer tutucu örnek elendi" if placeholders else "")})
        return Finding({"pages": {"player": rule}} if resolvers else {}, evidence, 0.8 if resolvers else 0.0)

    @staticmethod
    def _llm_free_played(ctx: StageCtx, kept: list, resolvers: list) -> str:
        """The first sample the LLM-free resolvers play (A6's playback test), '' when none (one try per sample, at most 2)."""
        if ctx.hint or getattr(ctx, "downstream", None):
            return ""   # a hint / an A6 hand-back asks for the LLM on purpose
        try:
            pctx = playside.play_ctx(ctx.merged({"pages": {"player": {"resolvers": resolvers}}}), ctx.get)
        except Exception:
            return ""
        if pctx is None:
            return ""
        for url, page, _usable in kept[:2]:
            trial = play_sample(pctx, url, page.html, ctx.base_url)
            if trial["ok"]:
                return url
        return ""

    def verify(self, ctx: StageCtx, patch: dict) -> Measurement:
        site_map = ctx.merged(patch)
        rule = (site_map.get("pages") or {}).get("player") or {}
        base = ctx.base_url
        pctx = playside.play_ctx(site_map, ctx.get)
        title_pattern = classify.page_regex((site_map.get("pages") or {}).get("title")) or ""
        urls = [u for u in ctx.pool(list(ctx.samples) + _meta(site_map, "player")) if classify.same_site(u, base)]
        samples, oks, link_oks, dropped = [], [], [], 0
        played, tried = [], []
        needed = episode_section(site_map)
        types = title_types(ctx.site_id)
        pctxs: dict = {}
        per_kind: dict = {}   # T30: kind -> {pages, found, played, tried}
        for url in list(dict.fromkeys(urls))[:MAX_TRIES]:
            if len(oks) >= WANT:
                break
            page = ctx.get(url, "player")
            if page is None:
                samples.append({"url": url, "ok": False, "note": "getirilemedi"})
                oks.append(False)
                continue
            final = page.final_url or url
            found = play_candidates.discover(pctx, page.html, final) if pctx is not None else []
            if not found and playside.placeholder_marker(page.html):
                dropped += 1
                continue
            # T30: a film's title page is its player page; the type comes from A3's handoff, else the address
            kind = "movie" if types.get(url) == "movie" or (url not in types and classify.title_key(site_map, url)) \
                else "episode"
            if rule.get("by_type"):   # separate resolvers per type: measure each page with its own list
                kpctx = pctxs.setdefault(kind, playside.play_ctx(site_map, ctx.get, kind))
                found = play_candidates.discover(kpctx, page.html, final) if kpctx is not None else []
            else:
                kpctx = pctx
            stats = per_kind.setdefault(kind, {"pages": 0, "found": 0, "played": 0, "tried": 0})
            stats["pages"] += 1
            stats["found"] += bool(found)
            note = f"{len(found)} aday" + (f" ({found[0].get('resolver_type')})" if found else "")
            episode = kind == "episode"
            link_ok = None
            if episode and title_pattern:
                link_ok = bool(rule.get("title_link")) and bool(_link_of(page.html, final, rule["title_link"], title_pattern, base))
                link_oks.append(link_ok)
                note += ", başlık bağlantısı " + ("var" if link_ok else "yok")
            oks.append(bool(found))
            # brief: the samples are played until ONE plays (per type on a mixed site: one episode + one film)
            if found and kpctx is not None and not stats["played"] and (not played or len(per_kind) > 1):
                trial = play_sample(kpctx, url, page.html, base)
                played.append(trial) if trial["ok"] else tried.append(trial)
                stats["tried"] += 1
                stats["played"] += bool(trial["ok"])
                note += ", " + trial["note"]
            samples.append({"url": url, "ok": bool(found) and (link_ok is not False or not needed), "note": note,
                            "candidates": len(found), "candidate_urls": [str(c.get("url") or "") for c in found][:4],
                            "alternatives": [{"label": str(c.get("label") or ""), "type": c.get("resolver_type")}
                                             for c in found][:8]})
        n = len(oks)
        values: dict = {"samples_count": n}
        if n:
            values["candidate_ratio"] = round(sum(oks) / n, 3)
        values["title_link_ok"] = int(all(link_oks) and bool(link_oks)) if needed else 1
        values["reached_samples"] = len(played) + sum(1 for t in tried if t["reached"])
        checks = judge(site_map, "player", values)
        notes = [f"{n} oynatıcı sayfası ölçüldü, {sum(oks)} sayfada aday var"]
        notes.append(f"oynatma: {played[0]['url']} oynadı" if played else
                     "oynatma: hiçbir örnek oynamadı" + (f" ({tried[0]['note']})" if tried else ""))
        if dropped:
            notes.append(f"{dropped} yer tutucu örnek paydadan düştü")
        if len(per_kind) > 1:   # T30: a mixed site, both types reported separately
            notes.append(type_line(per_kind))
        if needed and not values["title_link_ok"]:
            notes.append("ana sayfada bölüm kartları var ama bölüm sayfasından başlık sayfasına bağlantı bulunamadı")
        return measurement(checks, samples, notes)

    def handoff(self, ctx: StageCtx, patch: dict, m: Measurement) -> dict:
        """A6's input: every measured player page with a candidate (the title link is A5's own business) + the candidate
        addresses found on it + the resolvers."""
        pages = [{"url": s["url"], "candidates": list(s.get("candidate_urls") or [])}
                 for s in m.samples if s.get("url") and s.get("candidates")]
        rule = (((patch or {}).get("pages") or {}).get("player") or {})
        return {"urls": [p["url"] for p in pages][:map_meta.MAX_SAMPLES], "pages": pages[:map_meta.MAX_SAMPLES],
                "resolvers": list(rule.get("resolvers") or (ctx.page("player").get("resolvers") or []))}

    def on_passed(self, ctx: StageCtx, patch: dict, m: Measurement) -> None:
        """Crawl rows that waited for the player rule (home episode cards, titles' playback rows) are read now."""
        from ..crawl import plan as crawl_plan, run as crawl_run
        from ..sitemap import store as map_store
        site = map_store.get(ctx.site_id) or {}
        if site.get("status") == "active" and crawl_plan.rules_changed(ctx.site_id):
            crawl_run.kick(ctx.site_id)

    def result_line(self, patch: Optional[dict]) -> str:
        rule = ((patch or {}).get("pages") or {}).get("player") or {}
        kinds = ", ".join(str(r.get("type")) for r in rule.get("resolvers") or [])
        return f"kurallar: {kinds or '-'}; kalıp {' | '.join(classify.patterns_of(rule)) or None}; başlık bağlantısı {'var' if rule.get('title_link') else 'yok'}"

    def merge_llm(self, ctx: StageCtx, finding: Finding, patch: dict) -> tuple[dict, list]:
        """Engine hook after a valid LLM patch: :func:`compare_by_playback`."""
        return compare_by_playback(ctx, finding, patch)

    def parse_reply(self, ctx: StageCtx, data) -> Optional[tuple]:
        """An investigation ``result: {resolvers: [...], title_link?, url_pattern?}`` becomes the player page (missing keys
        from the discovery patch); a plain ``pages: player:`` answer goes through the engine's scope check."""
        if not isinstance(data, dict) or "result" not in data:
            return None
        result = data.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("resolvers"), list) or not result["resolvers"]:
            return None, "result.resolvers bir liste olmalı (her kaynak alternatifi için bir çözücü)"
        from . import state as stage_state
        found = (((stage_state.get(ctx.site_id, self.id) or {}).get("patch") or {}).get("pages") or {}).get("player") or {}
        cookies = result.get("cookies") or ((result.get("fetch") or {}).get("cookies") if isinstance(result.get("fetch"), dict)
                                            else None)
        if cookies:   # T32: a JS-set cookie the investigation found travels with the AJAX rules (generated fresh at play)
            result = {**result, "resolvers": [({**r, "cookies": cookies} if isinstance(r, dict) and r.get("type") == "ajax"
                                               and not r.get("cookies") else r) for r in result["resolvers"]]}
        page = {**ctx.page("player"), **found, **{k: v for k, v in result.items() if k in ("resolvers", "title_link", "url_pattern", "url_patterns")}}
        from .engine import map_problems
        patch = {"pages": {"player": page}}
        problems = map_problems(ctx, self, patch)
        if problems:
            return None, "harita geçersiz: " + "; ".join(problems[:3])
        return patch, None

    reads_first = True

    def read_pages(self, ctx: StageCtx, finding: Finding, last: Optional[Measurement] = None) -> list:
        urls = list((finding.evidence or {}).get("samples") or [])
        bad = [s["url"] for s in (last.samples if last else []) if not s.get("ok") and s.get("url")]
        order = list(dict.fromkeys(bad + urls))
        return [p for p in (ctx.get(u, "player") for u in order[:2]) if p is not None]

    def prompt(self, ctx: StageCtx, finding: Finding, hint: str, last: Optional[Measurement]) -> str:
        return prompts_a4_a7.full(self, ctx, finding, hint, last, self.read_pages(ctx, finding, last))
