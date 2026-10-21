"""Site map -> engine inputs (pure; README §5.5). Every adapter deep-copies, never mutates the map, and returns ``None``
when the page type is missing."""
from __future__ import annotations

import copy
from typing import Any, Callable, Optional

from ...core.fetch import SiteCtx
from . import classify

FETCH_DEFAULTS = {"mode": "auto", "delay_ms": 1000, "concurrency": 2}


def _page(site_map: dict, page_type: str) -> Optional[dict]:
    page = (site_map.get("pages") or {}).get(page_type)
    return copy.deepcopy(page) if isinstance(page, dict) else None


def browser_refused(site_map: dict) -> bool:
    """The map asks ``fetch.mode: browser`` while the browser engine is unguarded (``core.fetch.browser.GUARDED`` False:
    its own requests skip netguard): the site's pages are fetched over ``http`` instead."""
    from ...core.fetch import browser
    return str((site_map.get("fetch") or {}).get("mode") or "").lower() == "browser" and not browser.GUARDED


def site_ctx(site_map: dict) -> SiteCtx:
    fetch = {**FETCH_DEFAULTS, **(site_map.get("fetch") or {})}
    mode = "http" if browser_refused(site_map) else fetch["mode"]
    extra = {"cookies": list(fetch["cookies"])} if isinstance(fetch.get("cookies"), list) and fetch["cookies"] else {}
    return SiteCtx(site_id=str(site_map.get("site_id") or ""), base_url=str(site_map.get("base_url") or ""),
                   mode=mode, delay_ms=int(fetch["delay_ms"]), concurrency=int(fetch["concurrency"]), extra=extra)


def note_browser_refused(site_map: dict) -> bool:
    """Crawl / stage fetch setup: one unread admin note per site while its map asks for the unguarded browser."""
    if not browser_refused(site_map):
        return False
    from ... import db
    from .. import notes
    site_id = str(site_map.get("site_id") or "") or None
    try:
        if db.query_one("SELECT 1 FROM admin_notes WHERE site_id IS ? AND ref_kind='fetch_mode' AND read_at IS NULL",
                        (site_id,)) is None:
            notes.add("warn", f"{site_id}: site haritası fetch.mode: browser istiyor; tarayıcı motoru korumasız olduğu için "
                              "sayfalar http ile getiriliyor", site_id=site_id, ref_kind="fetch_mode", ref_id=site_id)
    except Exception:   # a note never breaks a fetch
        pass
    return True


def home_rules(site_map: dict) -> Optional[list[tuple[dict, str, dict]]]:
    """``[(section, row_selector, fields)]``; ``section`` = the section without its rule (``id, title, target``)."""
    home = _page(site_map, "home")
    if home is None:
        return None
    out = []
    for section in home.get("sections") or []:
        if isinstance(section, dict) and section.get("row_selector") and not section.get("ignore"):   # T17: ignored rows
            info = {k: v for k, v in section.items() if k not in ("row_selector", "fields")}
            out.append((info, section["row_selector"], section.get("fields") or {}))
    return out


def home_nav(site_map: dict) -> Optional[dict]:
    home = _page(site_map, "home")
    return None if home is None else (home.get("nav") or None)


def listing_rule(site_map: dict) -> Optional[dict]:
    """``{url_pattern, row_selector, fields, pagination, listings, blocks}`` (pagination defaults to ``{kind: none}``)."""
    page = _page(site_map, "listing")
    if page is None:
        return None
    return {"url_pattern": classify.page_regex(page), "row_selector": page.get("row_selector"),
            "fields": page.get("fields") or {}, "pagination": page.get("pagination") or {"kind": "none"},
            "listings": page.get("listings") or [], "blocks": page.get("blocks") or []}


def title_rule(site_map: dict) -> Optional[dict]:
    page = _page(site_map, "title")
    if page is None:
        return None
    return {"url_pattern": classify.page_regex(page), "key": page.get("key"), "type": page.get("type"),
            "fields": page.get("fields") or {}}


def name_groups(regex: str, names: dict[int, str]) -> str:
    """``regex`` with its n-th plain capturing group renamed (``(`` -> ``(?P<name>``) for every ``n -> name``."""
    out, i, n, in_class = [], 0, 0, False
    while i < len(regex):
        ch = regex[i]
        if ch == "\\":
            out.append(regex[i:i + 2])
            i += 2
            continue
        if in_class:
            in_class = ch != "]"
        elif ch == "[":
            in_class = True
        elif ch == "(" and not regex.startswith("(?", i):
            n += 1
            if n in names:
                out.append(f"(?P<{names[n]}>")
                i += 1
                continue
        out.append(ch)
        i += 1
    return "".join(out)


#: an episode regex that matches nothing (numbering only from the row text / list order)
NEVER_EPISODE = r"(?!)(?P<episode>\d+)"


def episodes_spec(site_map: dict) -> Optional[dict]:
    """The ``series_page`` spec of ``core.extract.episodes`` (``numbering`` -> ``episode_url_regex`` with named groups;
    ``where: season_pages`` -> ``season_pages``; the title page's ``url_pattern``/``key`` -> series page / slug regexes)."""
    page = _page(site_map, "episodes")
    if page is None:
        return None
    numbering = page.get("numbering") or {}
    regex = str(numbering.get("regex") or "")
    raw_from = numbering.get("from", "url")
    sources = [x for x in (raw_from if isinstance(raw_from, list) else [raw_from]) if x in ("url", "text", "position")]
    if not regex:   # numbers from the row text / list order only: no address regex
        sources = [x for x in sources if x != "url"]
    names = {}
    for name in ("season", "episode"):
        idx = numbering.get(name)
        if isinstance(idx, int) and not isinstance(idx, bool):
            names[idx] = name
    spec: dict[str, Any] = {"row_selector": page.get("row_selector"),
                            "episode_url_regex": (name_groups(regex, names) if names else regex) if regex else NEVER_EPISODE,
                            "default_season": numbering.get("default_season", 1)}
    if isinstance(raw_from, list) and sources != ["url"]:
        spec["numbering_from"] = sources or ["url"]
    if page.get("fields"):
        spec["fields"] = page["fields"]
    if page.get("where") == "season_pages":
        seasons = page.get("seasons") or {}
        spec["season_pages"] = {k: v for k, v in (("season_menu", seasons.get("menu_selector")),
                                                  ("season_url_regex", seasons.get("url_regex"))) if v}
    title = _page(site_map, "title") or {}
    if classify.page_regex(title):
        spec["series_url_regex"] = classify.page_regex(title)
    key = title.get("key") or {}
    if key.get("regex") and str(key.get("template") or "{1}") == "{1}":
        spec["series_slug_regex"] = key["regex"]
    return spec


def _play_ctx_class():
    try:
        from ...core import play
        cls = getattr(play, "PlayCtx", None)
        if cls is None:
            from ...core.play import ctx
            cls = getattr(ctx, "PlayCtx", None)
        return cls
    except Exception:   # T03 not there yet
        return None


PLAYER_KINDS = ("episode", "movie")


def player_resolvers(player: dict, kind: Optional[str] = None) -> list:
    """T30 item 9: ``pages.player.resolvers`` + the optional per-type lists ``by_type: {episode: [...], movie: [...]}``
    (a film page may be built differently from an episode page). ``kind`` given: that type's list first, then the shared
    one; no kind (playback that does not know the type): the shared list, then every per-type item (union, in order)."""
    base = list(player.get("resolvers") or [])
    by_type = player.get("by_type") if isinstance(player.get("by_type"), dict) else {}
    if kind in PLAYER_KINDS and by_type.get(kind):
        own = list(by_type[kind])
        return own + [r for r in base if r not in own]
    out = list(base)
    for k in PLAYER_KINDS:
        out += [r for r in by_type.get(k) or [] if r not in out]
    return out


def play_ctx(site_map: dict, fetch: Optional[Callable] = None, kind: Optional[str] = None):
    """``core.play.PlayCtx(site_id, base_url, resolvers, providers_allow, gate, fetch, mode)`` (``mode`` = the map's
    ``fetch.mode``; ``gate`` = the map's ``gate``
    as is, ``providers_allow`` ``None`` = every provider); a plain dict with the same
    keys while ``core.play`` has no ``PlayCtx``. ``None`` without a ``player`` page."""
    player = _page(site_map, "player")
    if player is None:
        return None
    sources = _page(site_map, "sources") or {}
    values = {"site_id": str(site_map.get("site_id") or ""), "base_url": str(site_map.get("base_url") or ""),
              "resolvers": player_resolvers(player, kind), "providers_allow": list(sources.get("providers") or []) or None,
              "gate": copy.deepcopy(site_map.get("gate") or {}), "fetch": fetch,
              "mode": {**FETCH_DEFAULTS, **(site_map.get("fetch") or {})}["mode"],   # the map's choice, as is
              "alternatives": alternatives(site_map),
              "cookies": copy.deepcopy((site_map.get("fetch") or {}).get("cookies") or [])}   # T32: site cookie specs
    cls = _play_ctx_class()
    if cls is None:
        return values
    try:
        return cls(**values)
    except TypeError:
        return values


def alternatives(site_map: dict) -> list:
    """A6's source alternatives in playback order (``pages.sources.alternatives``: ``[{key, label, host, status, ...}]``)."""
    sources = _page(site_map, "sources") or {}
    return [dict(a) for a in sources.get("alternatives") or [] if isinstance(a, dict) and a.get("key")]


def search_spec(site_map: dict) -> Optional[dict]:
    return _page(site_map, "search")


def gate_rules(site_map: dict) -> Optional[dict]:
    """``{gate: {require, probe}, blocked: [...]}`` (the shapes of ``core.play`` ``validate_gate`` / ``blocked``)."""
    gate = site_map.get("gate")
    if not isinstance(gate, dict):
        return None
    gate = copy.deepcopy(gate)
    return {"gate": {k: gate[k] for k in ("require", "probe") if k in gate}, "blocked": gate.get("blocked") or []}
