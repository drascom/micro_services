"""A4 episodes: where a series lists its episodes (its title page, or one page per season), the episode row rule and the
season/episode numbering read from the episode address. A site without series (every A3 sample is a film) skips it."""
from __future__ import annotations

import re
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from selectolax.parser import HTMLParser

from ...core.extract import episodes as x_episodes, outline
from ...core.extract._util import SAFE_CLASS, absolute, doc_base, selector_of
from ..sitemap import adapters, classify
from . import common, hints, prompts_a4_a7, state
from .base import Finding, Measurement, Stage, StageCtx, judge, measurement

MAX_SERIES = 3                     # series pages looked at
MAX_SEASON_PAGES = 6               # season pages read per series
_SEASON_PATH = re.compile(r"(?:sezon|season)[-_/]?(\d+)/?$", re.I)


def _plain_numbering(regex: str, default_season: int = 1) -> Optional[dict]:
    """``episode_url_regex`` with named groups -> the map's ``numbering``: season/episode become plain groups and their
    numbers count plain groups only (``adapters.name_groups``); the ``slug`` group stays named (it ties an episode to
    its series)."""
    try:
        compiled = re.compile(regex)
    except re.error:
        return None
    index = compiled.groupindex
    if "episode" not in index:
        return None
    kept = [n for name, n in index.items() if name not in ("season", "episode")]

    def plain(name: str) -> int:
        return index[name] - sum(1 for n in kept if n < index[name])

    out = {"from": ["url", "text", "position"], "regex": re.sub(r"\(\?P<(?:season|episode)>", "(", regex), "episode": plain("episode"),
           "default_season": default_season}
    if "season" in index:
        out["season"] = plain("season")
    return out


def _season_links(html: str, url: str, base: str) -> list:
    """Anchors of the page that point at a season page of the same series (``.../season-2``, ``/sezon-3``)."""
    tree = HTMLParser(html or "")
    own = urlsplit(url).path.rstrip("/")
    link_base = doc_base(tree, url)
    out, seen = [], set()
    for node in tree.css("a[href]"):
        href = absolute(link_base, node.attributes.get("href"))
        if not href or not classify.same_site(href, base) or outline.region_of(node) is not None:
            continue
        path = urlsplit(href).path
        if _SEASON_PATH.search(path) and path.rstrip("/") != own and path not in seen:
            seen.add(path)
            out.append(node)
    return out


def _season_rule(nodes: list, html: str) -> Optional[dict]:
    """``seasons: {menu_selector, url_regex}`` from the season links of a series page."""
    if len(nodes) < 1:
        return None
    selector, _n = outline.anchor_selector(HTMLParser(html or ""), nodes)
    last = urlsplit(nodes[0].attributes.get("href") or "").path.rstrip("/").rsplit("/", 1)[-1]
    found = re.search(r"\d+", last)
    if not found:
        return None
    regex = re.escape(last[:found.start()]) + r"(\d+)/?$"
    return {"menu_selector": selector, "url_regex": regex}


MAX_GROUPS = 6                     # candidate link groups shown to the LLM
FOREIGN_WARN = 0.2                 # share of rows that look like another series from which the LLM is asked to choose


def _group_handle(node) -> str:
    """The selector of a link's group: its nearest ancestor with an id / safe classes (+ `` a``)."""
    parent, depth = node.parent, 0
    while parent is not None and depth < 4 and parent.tag not in ("body", "html"):
        ident = parent.attributes.get("id") or ""
        if ident and SAFE_CLASS.match(ident):
            return f"#{ident} a"
        sel = selector_of(parent.tag, tuple((parent.attributes.get("class") or "").split()))
        if sel and sel != parent.tag:
            return f"{sel} a"
        parent, depth = parent.parent, depth + 1
    return "a"


def link_groups(html: str, url: str, base: str, *, slug: Optional[str], regex: str = "",
                title_pattern: Optional[str] = None) -> list[dict]:
    """The page's same-site links grouped by the block they sit in: ``{selector, count, region, episode_like, mine,
    foreign, title_links, samples:[{href, text}]}`` (largest groups first). ``mine``/``foreign`` = episode-like links whose
    series part looks like (does not look like) this series (normalised tokens, filler words learned)."""
    tree = HTMLParser(html or "")
    own = urlsplit(url).path.rstrip("/")
    try:
        titles = re.compile(title_pattern) if title_pattern else None
    except re.error:
        titles = None
    groups: dict[str, dict] = {}
    prefixes: list[str] = []
    link_base = doc_base(tree, url)   # relative links resolve against <base href> (a browser does the same)
    for node in tree.css("a[href]"):
        href = absolute(link_base, node.attributes.get("href"))
        if not href or not classify.same_site(href, base):
            continue
        path = urlsplit(href).path
        if not path or path.rstrip("/") == own:
            continue
        handle = _group_handle(node)
        g = groups.setdefault(handle, {"selector": handle, "count": 0, "region": outline.region_of(node) or "",
                                       "episode_like": 0, "mine": 0, "foreign": 0, "title_links": 0,
                                       "samples": [], "_prefixes": []})
        g["count"] += 1
        prefix = x_episodes.episode_prefix(href, regex) if regex else None
        if prefix is not None:
            g["episode_like"] += 1
            g["_prefixes"].append(prefix)
            prefixes.append(prefix)
        elif titles is not None and titles.search(path):
            g["title_links"] += 1
        if len(g["samples"]) < 3:
            g["samples"].append({"href": path[:120], "text": " ".join(node.text(strip=True).split())[:40]})
    filler = x_episodes.fillers([slug or ""] + prefixes)
    out = []
    for g in groups.values():
        for prefix in g.pop("_prefixes"):
            same = slug is None or x_episodes.same_series_score(prefix, slug, filler) >= x_episodes.SAME_SERIES
            g["mine" if same else "foreign"] += 1
        if g["count"] >= 2:
            out.append(g)
    out.sort(key=lambda g: (-g["episode_like"], -g["count"]))
    return out[:MAX_GROUPS]


def ambiguity(groups: list[dict], ownership: Optional[dict]) -> str:
    """Why the discovery should be settled by the LLM ("" = clear): several groups of episode-like links, or many rows
    of the chosen list that look like another series."""
    episode_groups = [g for g in groups if g.get("episode_like", 0) >= 2]
    if len(episode_groups) > 1:
        return f"{len(episode_groups)} bölüm bağlantısı grubu"
    rows, foreign = int((ownership or {}).get("rows") or 0), int((ownership or {}).get("foreign") or 0)
    if rows and foreign / rows > FOREIGN_WARN:
        return f"{foreign}/{rows} satır başka diziye benziyor"
    return ""


def series_samples(ctx_map: dict, samples: list, base: str, types: Optional[dict] = None) -> list[str]:
    """Series title pages among ``samples``: the known type (``types``: A3's handoff = provenance / page content) first,
    else ``title.type`` says series. A film never goes to A4 (no "0 episodes" failure for it)."""
    out = []
    types = types or {}
    for url in samples:
        kind = types.get(url) or classify.title_type(ctx_map, url)
        if classify.same_site(url, base) and (url in types or classify.title_key(ctx_map, url)) and \
                kind == "series" and url not in out:
            out.append(url)
    return out


def title_types(site_id: str) -> dict:
    """``{title url: series|movie}`` A3 handed over (T30: mixed sites)."""
    return dict(((state.get(site_id, "A3") or {}).get("handoff") or {}).get("types") or {})


def numbering_line(counts: dict) -> str:
    """T30: "numaralandırma kaynağı: url %x, metin %y, sıra %z" (shares of the episodes read), "" when none."""
    total = sum(int(v or 0) for v in counts.values())
    if not total:
        return ""
    pct = {k: round(100 * int(counts.get(k) or 0) / total) for k in ("url", "text", "position")}
    return f"numaralandırma kaynağı: url %{pct['url']}, metin %{pct['text']}, sıra %{pct['position']}"


def _add_ownership(info: dict, inv: dict) -> None:
    for src, n in (((inv.get("metrics") or {}).get("numbering")) or {}).items():
        if src in info.get("numbering", {}):
            info["numbering"][src] += int(n or 0)
    own = inv.get("ownership") or {}
    info["ownership"]["rows"] += int(own.get("rows") or 0)
    info["ownership"]["foreign"] += int(own.get("foreign") or 0)
    if own.get("example") and not info["ownership"]["example"]:
        info["ownership"]["example"] = own["example"]


def series_episodes(get: Callable[[str, str], Any], site_map: dict, url: str, *, page=None) -> tuple[list[dict], dict]:
    """Every episode of one series with the map's ``episodes`` rule: the title page, then (``where: season_pages`` or a
    season menu) each season page. ``([{season, episode, url, title, air_date?}], info)``; ``info`` = ``{seasons,
    season_menu, season_pages, warnings}``."""
    spec = adapters.episodes_spec(site_map)
    info: dict = {"seasons": [], "season_menu": 0, "season_pages": 0, "warnings": [],
                  "ownership": {"rows": 0, "foreign": 0, "example": ""}, "numbering": {"url": 0, "text": 0, "position": 0}}
    if not spec:
        info["warnings"].append("bölüm kuralı yok")
        return [], info
    page = page if page is not None else get(url, "title")
    if page is None:
        info["warnings"].append("dizi sayfası getirilemedi")
        return [], info
    base = str(site_map.get("base_url") or "")
    final = page.final_url or url
    inv = x_episodes.series_inventory(page.html, final, spec)
    info["season_menu"] = len(_season_links(page.html, final, base))
    info["warnings"] += list(inv.get("warnings") or [])[:2]
    _add_ownership(info, inv)
    found: dict[tuple, dict] = {}
    for ep in inv.get("video_sources") or []:
        found.setdefault((ep["season"], ep["episode"]), ep)
    season_spec = {k: v for k, v in spec.items() if k != "series_url_regex"}   # a season page is not the title page
    for season_url in list(inv.get("season_pages") or [])[:MAX_SEASON_PAGES]:
        if not classify.same_site(season_url, base):
            continue
        spage = get(season_url, "episodes")
        if spage is None:
            info["warnings"].append(f"sezon sayfası getirilemedi: {season_url}")
            continue
        info["season_pages"] += 1
        sinv = x_episodes.series_inventory(spage.html, spage.final_url or season_url, season_spec)
        _add_ownership(info, sinv)
        for ep in sinv.get("video_sources") or []:
            found.setdefault((ep["season"], ep["episode"]), ep)
    episodes = [found[k] for k in sorted(found)]
    info["seasons"] = sorted({e["season"] for e in episodes})
    return [{k: e[k] for k in ("season", "episode", "url", "title", "air_date") if k in e} for e in episodes], info


def _episode_rows(html: str, url: str, selector: str, regex: str) -> int:
    """Rows of ``selector`` whose own / first link is an episode address (``regex``)."""
    try:
        nodes = HTMLParser(html or "").css(selector)
        rx = re.compile(regex)
    except Exception:
        return 0
    got = 0
    link_base = doc_base(html or "", url)
    for node in nodes:
        link = node if node.tag == "a" else node.css_first("a[href]")
        href = absolute(link_base, (link.attributes.get("href") if link is not None else None) or "")
        if href and rx.search(x_episodes.regex_target(href, rx)):
            got += 1
    return got


def note_rows(ctx: StageCtx, rule: dict, holder: str) -> tuple[dict, list[str]]:
    """Site note "bölüm listesi .x içinde" -> row selector candidates (``.x li``, ``.x a``, ``.x``) tried on the page that
    holds the list; a candidate that reads at least as many episode rows wins (the note is the admin's word)."""
    sels = hints.selector_list([ctx.note], "episode_list", pages=("title", "episode", "listing")) if ctx.note else []
    if not sels:
        return rule, []
    page = ctx.get(holder, "episodes")
    regex = (rule.get("numbering") or {}).get("regex") or ""
    if page is None or not regex:
        return rule, [f"bölüm listesi seçicisi {sels[0][0]} denenemedi"]
    html, url = page.html, page.final_url or holder
    current = _episode_rows(html, url, rule["row_selector"], regex)
    for sel, tag in sels:
        for cand in ([sel] if tag in ("li", "a", "tr") else [f"{sel} li", f"{sel} a", sel]):
            n = _episode_rows(html, url, cand, regex)
            if n >= max(1, current):
                return {**rule, "row_selector": cand}, [f"bölüm satırı {cand} ({n} bölüm)"]
    return rule, [f"bölüm listesi seçicisi {sels[0][0]} keşiften az satır okudu ({current})"]


def _series_pool(ctx, site_map: dict, urls: list) -> list[str]:
    """The series pages to measure: excluded (and, with "only mine", non-admin) addresses never; a series page derived
    from an allowed episode address is kept (T25)."""
    types = title_types(ctx.site_id) if getattr(ctx, "site_id", None) else {}
    if hasattr(ctx, "pool"):
        got = series_samples(site_map, ctx.pool(urls), ctx.base_url, types)
        return [u for u in got if ctx.allowed(u, own=False)][:MAX_SERIES]
    return series_samples(site_map, urls, ctx.base_url, types)[:MAX_SERIES]


class EpisodesStage(Stage):
    id = "A4"
    page_type = "episodes"
    title = "Bölümler"
    needs = ("A3",)
    never_skip = ("url",)

    def _series(self, ctx: StageCtx) -> list[str]:
        meta = ((ctx.map.get("meta") or {}).get("title") or {}).get("samples") or []
        return _series_pool(ctx, ctx.map, list(ctx.samples) + list(meta))

    @staticmethod
    def _shapes(ctx: StageCtx) -> dict:
        """What tells an episode list from a list of other series: the title page address regex (A3) and the episode
        addresses the A1 review saw (episode cards)."""
        a1 = (state.get(ctx.site_id, "A1") or {}).get("handoff") or {}
        return {"title_pattern": ctx.page("title").get("url_pattern"),
                "episode_hrefs": [common.path(u) for u in a1.get("episode_cards") or []][:12]}

    def discover(self, ctx: StageCtx) -> Finding:
        series = self._series(ctx)
        shapes = self._shapes(ctx)
        evidence: dict = {"tried": series, "looked_for": "bölüm bağlantıları (sezon/bölüm numarası adreste), sezon menüsü"}
        if not series:
            evidence["summary"] = "örneklerde dizi yok (hepsi film): bölüm aşaması gerekmiyor"
            evidence["skip_stage"] = "sitede dizi örneği yok"
            return Finding({}, evidence, 0.0)
        best, tried = None, []
        for url in series:
            page = ctx.get(url, "title")
            if page is None:
                tried.append({"url": url, "problem": "getirilemedi"})
                continue
            final = page.final_url or url
            spec, ev = x_episodes.build_series_page(page.html, final, outline.outline(page.html, final), **shapes)
            where, seasons, holder = "title_page", None, final
            if spec is None:   # no episode list on the title page: try its season pages
                links = _season_links(page.html, final, ctx.base_url)
                seasons = _season_rule(links, page.html)
                for node in links[:2]:
                    surl = absolute(doc_base(page.html, final), node.attributes.get("href"))
                    spage = ctx.get(surl, "episodes")
                    if spage is None:
                        continue
                    sfinal = spage.final_url or surl
                    spec, ev = x_episodes.build_series_page(spage.html, sfinal, outline.outline(spage.html, sfinal),
                                                           **shapes)
                    if spec is not None:
                        where, holder = "season_pages", sfinal
                        break
            tried.append({"url": url, "where": where, "problem": None if spec else ev.get("problem")})
            if spec is None:
                continue
            numbering = _plain_numbering(spec["episode_url_regex"], spec.get("default_season", 1))
            if numbering is None:
                tried[-1]["problem"] = "bölüm adresinde numara grubu yok"
                continue
            rule: dict = {"where": where, "row_selector": spec["row_selector"], "numbering": numbering}
            fields = {k: v for k, v in (spec.get("fields") or {}).items() if k in ("url", "title", "air_date")}
            if fields:
                rule["fields"] = fields
            if where == "season_pages" and seasons:
                rule["seasons"] = seasons
            best = (rule, ev, holder)
            break
        evidence["series_tried"] = tried
        if best is None:
            evidence["summary"] = "dizi sayfalarında okunabilir bölüm listesi bulunamadı"
            return Finding({}, evidence, 0.0)
        rule, ev, holder = best
        rule, used = note_rows(ctx, rule, holder)
        if used:
            evidence["note_used"] = used
        hpage = ctx.get(holder, "episodes" if rule["where"] == "season_pages" else "title")
        if hpage is not None:   # the candidate link groups: the LLM chooses among them when the discovery is ambiguous
            groups = link_groups(hpage.html, hpage.final_url or holder, ctx.base_url,
                                 slug=classify.title_key(ctx.map, series[0]) if series else None,
                                 regex=(adapters.episodes_spec({"pages": {"episodes": rule}}) or {}).get("episode_url_regex", ""),
                                 title_pattern=shapes.get("title_pattern"))
            why = ambiguity(groups, (ev.get("inventory") or {}).get("ownership"))
            if why:
                evidence["ask_llm"] = why
                evidence["link_groups"] = groups
        evidence.update({"samples": series, "found": {k: ev.get(k) for k in ("row_selector", "episode_url_regex", "episodes",
                                                                         "seasons", "structured", "confidence")},
                         "episode_page": holder,
                         "summary": f"{ev.get('episodes')} bölüm, {rule['where']}, satır {rule['row_selector']}"})
        return Finding({"pages": {"episodes": rule}}, evidence, 0.8)

    def verify(self, ctx: StageCtx, patch: dict) -> Measurement:
        site_map = ctx.merged(patch)
        series = _series_pool(ctx, site_map, list(ctx.samples) + list(
            ((site_map.get("meta") or {}).get("title") or {}).get("samples") or []))
        samples, counts, numbers_ok, unique_ok, seasons_ok = [], [], [], [], []
        own_rows, own_foreign, own_example = 0, 0, ""
        numbering = {"url": 0, "text": 0, "position": 0}
        for url in series:
            episodes, info = series_episodes(ctx.get, site_map, url)
            for src, n in info.get("numbering", {}).items():
                numbering[src] += n
            pairs = [(e.get("season"), e.get("episode")) for e in episodes]
            nums = all(isinstance(s, int) and isinstance(n, int) and n >= 1 for s, n in pairs)
            unique = len(set(pairs)) == len(pairs)
            menu = info["season_menu"] >= 1
            multi = (len(info["seasons"]) >= 2) if menu else True
            own = info["ownership"]
            own_rows, own_foreign = own_rows + own["rows"], own_foreign + own["foreign"]
            own_example = own_example or own["example"]
            counts.append(len(episodes))
            numbers_ok.append(nums)
            unique_ok.append(unique)
            seasons_ok.append(multi)
            note = f"{len(episodes)} bölüm, sezonlar {info['seasons'] or '-'}"
            if menu and not multi:
                note += "; sezon menüsü var ama tek sezon okundu"
            if info["warnings"]:
                note += "; " + info["warnings"][0][:160]
            samples.append({"url": url, "ok": bool(episodes) and nums and unique and multi, "note": note,
                            "episodes": len(episodes), "seasons": info["seasons"],
                            "_eps": [e.get("url") for e in episodes if e.get("url")][:2]})
        opened = self._open_episodes(ctx, samples)
        for s in samples:
            s.pop("_eps", None)
        n = len(series)
        values: dict = {"series_samples": n}
        if n:
            values.update({"episodes_ok_ratio": round(sum(1 for c in counts if c > 0) / n, 3),
                           "numbers_ok": int(all(numbers_ok)), "unique_ok": int(all(unique_ok)),
                           "seasons_ok": int(all(seasons_ok))})
        extra = None
        if own_rows:   # rows of the series' own page that look like another series: a warning, never a failure
            values["foreign_rows_ratio"] = round(own_foreign / own_rows, 3)
            extra = {"foreign_rows_ratio": {"bound": FOREIGN_WARN, "op": "max", "optional": True}}
        values["episode_pages_ok"] = int(bool(opened) and all(o["ok"] for o in opened))
        if sum(numbering.values()):   # T30: episodes numbered by list order only (no number in address / text) -> warning
            values["numbering_position_count"] = numbering["position"]
            extra = {**(extra or {}), "numbering_position_count": {"bound": 0, "op": "max", "optional": True}}
        checks = judge(site_map, "episodes", values, extra=extra)
        notes = [f"{n} dizide bölüm okundu: " + ", ".join(str(c) for c in counts)] if n else ["dizi örneği yok"]
        if opened:
            notes.append("bölüm adresleri açıldı: " + "; ".join(f"{o['url']} {o['note']}" for o in opened))
        if own_foreign:
            notes.append(f"{own_foreign} bağlantı başka diziye benziyor: örn. {own_example}")
        line = numbering_line(numbering)
        if line:
            notes.append(line)
        if n and not all(seasons_ok):
            notes.append("çok sezonlu dizide ikinci sezon okunamadı")
        return measurement(checks, samples, notes)

    OPEN_EPISODES = 2

    def _open_episodes(self, ctx: StageCtx, samples: list) -> list[dict]:
        """At least 2 of the produced episode addresses are really fetched (the ``/a/a/`` kind of join bug gives 404s):
        ``[{url, ok, note}]``; ok = the page opened (2xx) and did not land back on the series page / home page."""
        urls: list = []
        for s in [x for x in samples if x.get("ok")] + [x for x in samples if not x.get("ok")]:
            for u in s.get("_eps") or []:
                if u not in [x[0] for x in urls] and len(urls) < self.OPEN_EPISODES:
                    urls.append((u, s["url"]))
        out = []
        for url, series_url in urls:
            log = getattr(ctx, "errors", None) or []
            errors = len(log)
            page = ctx.get(url, "player")
            if page is None:
                code = (log[-1] if len(log) > errors else {}).get("code") or "getirilemedi"
                out.append({"url": url, "ok": False, "note": f"açılmadı ({code})"})
                continue
            status = int(getattr(page, "status", 200) or 200)
            landed = (page.final_url or url).rstrip("/")
            back = landed in (series_url.rstrip("/"), ctx.base_url.rstrip("/"))
            ok = status < 400 and not back
            out.append({"url": url, "ok": ok, "note": f"HTTP {status}" + (", dizi/ana sayfaya döndü" if back else "")})
        return out

    def meta_for(self, ctx: StageCtx, patch: dict, m: Measurement) -> dict:
        good = [s["url"] for s in m.samples if s.get("ok") and s.get("url")]
        return {"samples": good[:3]} if good else {}

    def result_line(self, patch: Optional[dict]) -> str:
        rule = ((patch or {}).get("pages") or {}).get("episodes") or {}
        return (f"{rule.get('where')}, satır {rule.get('row_selector')}, numara {(rule.get('numbering') or {}).get('regex')}")

    reads_first = True

    def read_pages(self, ctx: StageCtx, finding: Finding, last: Optional[Measurement] = None) -> list:
        urls = [u for u in [(finding.evidence or {}).get("episode_page")] + self._series(ctx) if u]
        return [p for p in (ctx.get(u, "title") for u in list(dict.fromkeys(urls))[:2]) if p is not None]

    def prompt(self, ctx: StageCtx, finding: Finding, hint: str, last: Optional[Measurement]) -> str:
        return prompts_a4_a7.full(self, ctx, finding, hint, last, self.read_pages(ctx, finding, last))
