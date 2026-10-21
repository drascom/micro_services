"""Site map schema (README §5.5) and its validation: ``validate(data) -> [Problem(path, message, level)]``.

Unknown keys are errors (never swallowed). Selectors are compiled with selectolax, regexes with ``re`` plus the
double-backslash lint; ``url_pattern`` must match its own ``meta.<type>.samples``; ``title.key`` must give non-empty,
distinct keys for the title samples; ``player.resolvers`` / ``gate`` go to ``core.play`` validators (injectable while
T03 is missing), ``search`` to ``core.extract.search.validate_spec``, ``episodes`` (through the adapter) to
``core.extract.episodes.validate_spec``; ``base_url`` must pass netguard.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from selectolax.parser import HTMLParser

from ...core.extract import episodes as x_episodes
from ...core.extract import search as x_search
from ...core.fetch import netguard
from . import adapters, classify

MAP_VERSION = 1
SITE_ID_RE = re.compile(r"^[a-z0-9_]{2,32}$")
PAGE_TYPES = ("home", "listing", "title", "episodes", "player", "sources", "search")
#: stage id -> page type (README §5.5; A7 = search + gate)
STAGE_PAGE = {"A1": "home", "A2": "listing", "A3": "title", "A4": "episodes", "A5": "player", "A6": "sources",
              "A7": "search"}

TOP_KEYS = {"map", "site_id", "base_url", "fetch", "pages", "gate", "meta"}
FETCH_KEYS = {"mode", "delay_ms", "concurrency", "cookies"}   # T32: cookies = [{name, value|now_ms|now_s}]
FETCH_MODES = ("http", "browser", "auto")
PAGE_KEYS = {
    "home": {"url", "url_pattern", "url_patterns", "sections", "nav"},
    "listing": {"url_pattern", "url_patterns", "row_selector", "fields", "pagination", "listings", "blocks"},
    "title": {"url_pattern", "url_patterns", "key", "type", "fields"},
    "episodes": {"url_pattern", "url_patterns", "where", "row_selector", "fields", "numbering", "seasons"},
    "player": {"url_pattern", "url_patterns", "title_link", "resolvers", "fields", "by_type"},
    "sources": {"providers", "alternatives"},
    "search": set(x_search.SPEC_KEYS),
}
SECTION_KEYS = {"id", "title", "row_selector", "target", "fields", "ignore"}
SECTION_TARGETS = ("title", "episode")
NAV_KEYS = {"selector", "links"}
LINK_KEYS = {"name", "url", "kind", "content", "ignore", "title_type", "manual", "pagination", "row_selector", "fields"}
LINK_KINDS = ("genre", "category", "type", "archive")
#: what a menu link lists (A1 review): ``title`` cards (a title listing) or ``episode`` cards (never a title listing)
LINK_CONTENT = ("title", "episode")
PAGINATION_KEYS = {"kind", "selector", "template"}
#: A2 blocks of one listing page (same row structure under several headings): which ones are read, as which title type
BLOCK_KEYS = {"url", "index", "selector", "heading", "type", "ignore", "pagination", "row_selector", "fields"}
BLOCK_TYPES = ("series", "movie", "mixed")
#: ``path_number`` = a page number in the path (``/page/{n}/``, ``-{n}``), read like ``page_param`` (T30 item 8)
PAGINATION_KINDS = ("next_link", "page_param", "path_number", "none")
KEY_KEYS = {"regex", "template"}
TYPE_KEYS = {"from", "map", "default", "field"}
TITLE_TYPES = ("movie", "series")
WHERE = ("title_page", "season_pages")
NUMBERING_KEYS = {"from", "regex", "season", "episode", "default_season"}
#: T30: where an episode's season/episode numbers come from, tried in this order (``from`` = one of them or a list)
NUMBERING_SOURCES = ("url", "text", "position")
SEASONS_KEYS = {"menu_selector", "url_regex"}
TITLE_LINK_KEYS = {"selector", "attr"}
GATE_KEYS = {"require", "probe", "blocked"}
META_KEYS = {"samples", "fingerprint", "skipped", "expect", "reviewed"}
FIELD_KEYS = {"selector", "attr", "regex", "split", "index", "then_split", "multi", "cast", "replace", "fallback",
              "all", "self"}
CASTS = ("int", "float", "date_tr")


@dataclass
class Problem:
    path: str
    message: str
    level: str = "error"   # error | warn

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------------------------- injectable validators

def _play_validator(name: str) -> Optional[Callable[[Any], list]]:
    """``core.play`` validators (T03): ``resolvers`` -> ``resolvers.validate``, ``gate`` -> ``gate.validate_gate``
    (or ``blocked.validate_gate``), ``blocked`` -> ``blocked.validate``. ``None`` while the module is missing."""
    try:
        if name == "resolvers":
            from ...core.play import resolvers
            return resolvers.validate
        from ...core.play import blocked
        if name == "blocked":
            return blocked.validate
        fn = getattr(blocked, "validate_gate", None)
        if fn is None:
            from ...core.play import gate
            fn = gate.validate_gate
        return fn
    except Exception:
        return None


#: tests / later tasks may override a validator here (``name -> fn(value) -> [message]``)
VALIDATORS: dict[str, Callable[[Any], list]] = {}


def _validator(name: str) -> Optional[Callable[[Any], list]]:
    return VALIDATORS.get(name) or _play_validator(name)


# ---------------------------------------------------------------------------------------------- helpers

class _Out:
    def __init__(self) -> None:
        self.items: list[Problem] = []

    def err(self, path: str, message: str) -> None:
        self.items.append(Problem(path, message, "error"))

    def warn(self, path: str, message: str) -> None:
        self.items.append(Problem(path, message, "warn"))


def _join(path: str, key: Any) -> str:
    return f"{path}.{key}" if path else str(key)


def _keys(out: _Out, path: str, value: Any, allowed: set) -> bool:
    if not isinstance(value, dict):
        out.err(path, "must be a mapping")
        return False
    for key in value:
        if key not in allowed:
            out.err(_join(path, key), f"unknown key (allowed: {', '.join(sorted(allowed))})")
    return True


def selector_ok(selector: Any) -> bool:
    if not isinstance(selector, str) or not selector.strip():
        return False
    try:
        HTMLParser("<div></div>").css(selector)
    except Exception:
        return False
    return True


def _selector(out: _Out, path: str, value: Any) -> None:
    if not selector_ok(value):
        out.err(path, f"invalid CSS selector {value!r}")


def _regex(out: _Out, path: str, value: Any) -> Optional[re.Pattern]:
    if not isinstance(value, str) or not value:
        out.err(path, "must be a non-empty regex string")
        return None
    try:
        rx = re.compile(value)
    except re.error as exc:
        out.err(path, f"regex does not compile ({exc})")
        return None
    pair = x_episodes.double_backslash(value)
    if pair:
        out.err(path, f"regex contains a double backslash ({pair!r}); write {pair[1:]!r}")
    return rx


def _field(out: _Out, path: str, spec: Any) -> None:
    """One field rule (old ``onboard_sandbox._check_spec``, stricter: unknown keys are errors)."""
    if not _keys(out, path, spec, FIELD_KEYS):
        return
    if "fallback" in spec:
        alternatives = spec["fallback"]
        if not isinstance(alternatives, list) or not alternatives:
            out.err(_join(path, "fallback"), "must be a non-empty list of field rules")
            return
        for i, alt in enumerate(alternatives):
            _field(out, f"{path}.fallback[{i}]", alt)
        if spec.get("selector") is not None:   # a rule with its own selector is tried first (parse.apply_field)
            _selector(out, _join(path, "selector"), spec.get("selector"))
        return
    if spec.get("self") is not True:
        _selector(out, _join(path, "selector"), spec.get("selector"))
    if spec.get("regex") is not None:
        _regex(out, _join(path, "regex"), spec["regex"])
    if spec.get("cast") is not None and spec["cast"] not in CASTS:
        out.warn(_join(path, "cast"), f"unsupported cast ignored (supported: {', '.join(CASTS)})")
    if spec.get("attr") is not None and not isinstance(spec["attr"], str):
        out.err(_join(path, "attr"), "must be an attribute name")


def normalize_rules(data: Any, notes: Optional[list] = None) -> Any:
    """LLM leniency (in place): a field rule's ``fallback: []`` / ``null`` is dropped, a single rule is wrapped in a list,
    an unsupported ``cast`` is dropped (the raw text is kept; ``notes`` gets a line)."""
    if isinstance(data, list):
        for item in data:
            normalize_rules(item, notes)
    elif isinstance(data, dict):
        if "fallback" in data and set(data) <= FIELD_KEYS:
            alt = data["fallback"]
            if alt is None or alt == [] or alt == {}:
                data.pop("fallback")
            elif isinstance(alt, dict):
                data["fallback"] = [alt]
        if "cast" in data and set(data) <= FIELD_KEYS and data["cast"] is not None and data["cast"] not in CASTS:
            bad = data.pop("cast")
            if notes is not None:
                notes.append(f"desteklenmeyen cast {bad!r} yok sayıldı (desteklenen: {', '.join(CASTS)})")
        if isinstance(data.get("resolvers"), (list, dict)):   # resolver items: the LLM's common shapes (core.play.resolvers)
            try:
                from ...core.play import resolvers as play_resolvers
                data["resolvers"] = play_resolvers.normalize(data["resolvers"], notes)
            except ImportError:
                pass
        for key, value in data.items():
            if key != "resolvers":
                normalize_rules(value, notes)
    return data


def _fields(out: _Out, path: str, fields: Any, required: tuple = ()) -> None:
    if fields is None and not required:
        return
    if not isinstance(fields, dict):
        out.err(path, "must be a mapping of field name -> rule")
        return
    for name in required:
        if name not in fields:
            out.err(_join(path, name), "required")
    for name, spec in fields.items():
        _field(out, _join(path, name), spec)


def _int(out: _Out, path: str, value: Any, lo: int, hi: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        out.err(path, f"must be a whole number {lo}..{hi}")


def _links(out: _Out, path: str, links: Any) -> None:
    if not isinstance(links, list):
        out.err(path, "must be a list of {name, url, kind}")
        return
    for i, link in enumerate(links):
        where = f"{path}[{i}]"
        if not _keys(out, where, link, LINK_KEYS):
            continue
        if not isinstance(link.get("url"), str) or not link["url"]:
            out.err(_join(where, "url"), "required")
        if link.get("kind") is not None and link["kind"] not in LINK_KINDS:
            out.err(_join(where, "kind"), f"must be one of {', '.join(LINK_KINDS)}")
        if link.get("content") is not None and link["content"] not in LINK_CONTENT:
            out.err(_join(where, "content"), f"must be one of {', '.join(LINK_CONTENT)}")
        if link.get("title_type") is not None and link["title_type"] not in TITLE_TYPES:
            out.err(_join(where, "title_type"), f"must be one of {', '.join(TITLE_TYPES)}")
        for flag in ("ignore", "manual"):
            if link.get(flag) is not None and not isinstance(link[flag], bool):
                out.err(_join(where, flag), "must be true/false")


# ---------------------------------------------------------------------------------------------- page types

def _home(out: _Out, page: dict) -> None:
    sections = page.get("sections")
    if sections is not None:
        if not isinstance(sections, list):
            out.err("pages.home.sections", "must be a list")
        else:
            ids = set()
            for i, section in enumerate(sections):
                where = f"pages.home.sections[{i}]"
                if not _keys(out, where, section, SECTION_KEYS):
                    continue
                if section.get("id") in ids:
                    out.err(_join(where, "id"), f"duplicate section id {section.get('id')!r}")
                ids.add(section.get("id"))
                _selector(out, _join(where, "row_selector"), section.get("row_selector"))
                if section.get("target", "title") not in SECTION_TARGETS:
                    out.err(_join(where, "target"), f"must be one of {', '.join(SECTION_TARGETS)}")
                if section.get("ignore") is not None and not isinstance(section["ignore"], bool):
                    out.err(_join(where, "ignore"), "must be true/false")
                _fields(out, _join(where, "fields"), section.get("fields"), ("url",))
    nav = page.get("nav")
    if nav is not None and _keys(out, "pages.home.nav", nav, NAV_KEYS):
        if nav.get("selector") is not None:
            _selector(out, "pages.home.nav.selector", nav["selector"])
        if nav.get("links") is not None:
            _links(out, "pages.home.nav.links", nav["links"])


def _listing(out: _Out, page: dict) -> None:
    _selector(out, "pages.listing.row_selector", page.get("row_selector"))
    _fields(out, "pages.listing.fields", page.get("fields"), ("url",))
    _pagination(out, "pages.listing.pagination", page.get("pagination"))
    if page.get("listings") is not None:
        _links(out, "pages.listing.listings", page["listings"])
        for i, link in enumerate(page["listings"] if isinstance(page["listings"], list) else []):
            if isinstance(link, dict) and link.get("pagination") is not None:   # this listing's own pagination
                _pagination(out, f"pages.listing.listings[{i}].pagination", link["pagination"])
            if isinstance(link, dict) and link.get("row_selector") is not None:   # T31: this listing's own row rule
                _selector(out, f"pages.listing.listings[{i}].row_selector", link["row_selector"])
                _fields(out, f"pages.listing.listings[{i}].fields", link.get("fields"), ("url",))
    if page.get("blocks") is not None:
        _blocks(out, "pages.listing.blocks", page["blocks"])


def _pagination(out: _Out, where: str, pag: Any) -> None:
    """A pagination rule: the site-wide default (``listing.pagination``), a listing's own or a block's own (T30)."""
    if pag is None or not _keys(out, where, pag, PAGINATION_KEYS):
        return
    kind = pag.get("kind")
    if kind not in PAGINATION_KINDS:
        out.err(f"{where}.kind", f"must be one of {', '.join(PAGINATION_KINDS)}")
    if kind == "next_link":
        _selector(out, f"{where}.selector", pag.get("selector"))
    if kind in ("page_param", "path_number") and not re.search(r"\{n(?::0\d)?\}", str(pag.get("template") or "")):
        out.err(f"{where}.template", "must contain {n} (or {n:02} for a zero-padded end number)")


def _blocks(out: _Out, path: str, blocks: Any) -> None:
    if not isinstance(blocks, list):
        out.err(path, "must be a list of {url, index, heading?, type, ignore?}")
        return
    seen = set()
    for i, block in enumerate(blocks):
        where = f"{path}[{i}]"
        if not _keys(out, where, block, BLOCK_KEYS):
            continue
        if not isinstance(block.get("url"), str) or not block["url"]:
            out.err(_join(where, "url"), "required (the listing page the block is on)")
        _int(out, _join(where, "index"), block.get("index"), 1, 50)
        if (block.get("url"), block.get("index")) in seen:
            out.err(_join(where, "index"), "duplicate block of the same page")
        seen.add((block.get("url"), block.get("index")))
        if block.get("type") is not None and block["type"] not in BLOCK_TYPES:
            out.err(_join(where, "type"), f"must be one of {', '.join(BLOCK_TYPES)}")
        if block.get("ignore") is not None and not isinstance(block["ignore"], bool):
            out.err(_join(where, "ignore"), "must be true/false")
        for key in ("heading", "selector"):
            if block.get(key) is not None and not isinstance(block[key], str):
                out.err(_join(where, key), "must be text")
        if block.get("row_selector") is not None:   # T31: a block with its own rows (blocks of different row shapes)
            _selector(out, _join(where, "row_selector"), block["row_selector"])
            if block.get("fields") is not None:
                _fields(out, _join(where, "fields"), block["fields"], ("url",))
        if block.get("pagination") is not None:   # only this block is paginated (the other blocks: first page only)
            _pagination(out, _join(where, "pagination"), block["pagination"])


def _title(out: _Out, page: dict) -> None:
    _fields(out, "pages.title.fields", page.get("fields"))
    key = page.get("key")
    if key is not None and _keys(out, "pages.title.key", key, KEY_KEYS):
        rx = _regex(out, "pages.title.key.regex", key.get("regex"))
        template = key.get("template")
        if template is not None and not isinstance(template, str):
            out.err("pages.title.key.template", "must be a string such as '{1}'")
        elif rx is not None and template:
            for name in re.findall(r"\{(\w+)\}", template):
                if (name.isdigit() and int(name) > rx.groups) or (not name.isdigit() and name not in rx.groupindex):
                    out.err("pages.title.key.template", f"refers to group {{{name}}} the regex does not have")
    rule = page.get("type")
    if rule is not None and _keys(out, "pages.title.type", rule, TYPE_KEYS):
        if rule.get("from", "url") not in ("url", "field"):
            out.err("pages.title.type.from", "must be url or field")
        mapping = rule.get("map")
        if mapping is not None and (not isinstance(mapping, dict) or any(v not in TITLE_TYPES for v in mapping.values())):
            out.err("pages.title.type.map", f"must map tokens to {' / '.join(TITLE_TYPES)}")
        if rule.get("default") is not None and rule["default"] not in TITLE_TYPES:
            out.err("pages.title.type.default", f"must be one of {', '.join(TITLE_TYPES)}")


def numbering_sources(numbering: Any) -> list[str]:
    """``numbering.from`` as an ordered list (``url`` when missing); ``[]`` when it names an unknown source."""
    raw = (numbering or {}).get("from", "url") if isinstance(numbering, dict) else "url"
    items = raw if isinstance(raw, list) else [raw]
    if not items or any(i not in NUMBERING_SOURCES for i in items):
        return []
    return list(dict.fromkeys(items))


def _episodes(out: _Out, page: dict, site_map: dict) -> None:
    if page.get("where", "title_page") not in WHERE:
        out.err("pages.episodes.where", f"must be one of {', '.join(WHERE)}")
    _selector(out, "pages.episodes.row_selector", page.get("row_selector"))
    _fields(out, "pages.episodes.fields", page.get("fields"))
    numbering = page.get("numbering")
    if not isinstance(numbering, dict):
        out.err("pages.episodes.numbering", "required: {from: url, regex, season, episode, default_season}")
    elif _keys(out, "pages.episodes.numbering", numbering, NUMBERING_KEYS):
        sources = numbering_sources(numbering)
        if not sources:
            out.err("pages.episodes.numbering.from", f"one of {', '.join(NUMBERING_SOURCES)} or a list of them "
                                                      "(tried in order: url -> text -> position)")
        needs_rx = "url" in sources or numbering.get("regex") is not None
        rx = _regex(out, "pages.episodes.numbering.regex", numbering.get("regex")) if needs_rx else None
        for name in ("season", "episode"):
            idx = numbering.get(name)
            if idx is None:
                continue
            if isinstance(idx, bool) or not isinstance(idx, int) or idx < 1:
                out.err(f"pages.episodes.numbering.{name}", "must be a group number >= 1")
            elif rx is not None and idx > rx.groups:
                out.err(f"pages.episodes.numbering.{name}", f"group {idx} does not exist in the regex")
        if "default_season" in numbering:
            _int(out, "pages.episodes.numbering.default_season", numbering["default_season"], 1, 1000)
    seasons = page.get("seasons")
    if seasons is not None and _keys(out, "pages.episodes.seasons", seasons, SEASONS_KEYS):
        if seasons.get("menu_selector") is not None:
            _selector(out, "pages.episodes.seasons.menu_selector", seasons["menu_selector"])
        if seasons.get("url_regex") is not None:
            _regex(out, "pages.episodes.seasons.url_regex", seasons["url_regex"])
    if page.get("where") == "season_pages" and not (seasons or {}).get("menu_selector"):
        out.err("pages.episodes.seasons.menu_selector", "required when where=season_pages")
    if not any(p.level == "error" and p.path.startswith("pages.episodes") for p in out.items):
        for message in x_episodes.validate_spec(adapters.episodes_spec(site_map)):
            out.err("pages.episodes", f"engine spec: {message}")


def _bare_host(url: Any) -> str:
    host = (urlsplit(url).hostname or "").lower() if isinstance(url, str) else ""
    return host[4:] if host.startswith("www.") else host


def _browser_rules(out: _Out, resolvers: Any, base_url: Any) -> None:
    """While the browser engine is unguarded (``core.fetch.browser.GUARDED`` False) ``core.play`` opens a ``fetch: browser``
    player page only on the site's own host: a rule that can only point elsewhere is an error, one that may is a warning."""
    from ...core.fetch import browser as fetch_browser
    if fetch_browser.GUARDED or not isinstance(resolvers, list):
        return
    own = _bare_host(base_url)
    for i, rule in enumerate(resolvers):
        if not isinstance(rule, dict) or rule.get("fetch") != "browser":
            continue
        where = f"pages.player.resolvers.{i}.fetch"
        regex = rule.get("host_regex")
        hosts = {own, f"www.{own}"} if own else set()
        if isinstance(regex, str) and regex:
            try:
                ok = any(re.fullmatch(regex, h, re.I) for h in hosts)
            except re.error:
                ok = True   # the regex itself is reported by the resolver validator
            if not ok:
                out.err(where, f"fetch: browser points at another host (host_regex {regex!r}); the browser is unguarded and "
                               f"only opens pages on {own or 'the site host'}")
                continue
        else:
            out.warn(where, "fetch: browser works only for player pages on the site's own host (browser unguarded); "
                            "set host_regex to the site host")
        if rule.get("follow"):
            out.warn(where, "fetch: browser with follow hops: a hop to another host is refused (browser unguarded)")


def _player(out: _Out, page: dict, base_url: Any = None) -> None:
    link = page.get("title_link")
    if link is not None and _keys(out, "pages.player.title_link", link, TITLE_LINK_KEYS):
        _selector(out, "pages.player.title_link.selector", link.get("selector"))
    _fields(out, "pages.player.fields", page.get("fields"))
    by_type = page.get("by_type")
    if by_type is not None:   # T30: separate resolvers for episode pages / film pages (both optional)
        if not isinstance(by_type, dict) or any(k not in ("episode", "movie") for k in by_type):
            out.err("pages.player.by_type", "must be {episode: [resolvers], movie: [resolvers]}")
        else:
            fn = _validator("resolvers")
            for k, items in by_type.items():
                if fn is not None:
                    for message in fn(items):
                        out.err(f"pages.player.by_type.{k}", str(message))
                elif not isinstance(items, list):
                    out.err(f"pages.player.by_type.{k}", "must be a list of {type, ...}")
                _browser_rules(out, items, base_url)
    resolvers = page.get("resolvers")
    if resolvers is None:
        return
    fn = _validator("resolvers")
    if fn is not None:
        for message in fn(resolvers):
            out.err("pages.player.resolvers", str(message))
    elif not isinstance(resolvers, list) or not all(isinstance(r, dict) and isinstance(r.get("type"), str) for r in resolvers):
        out.err("pages.player.resolvers", "must be a list of {type, ...}")
    else:
        out.warn("pages.player.resolvers", "core.play resolver validator not available: only the shape was checked")
    _browser_rules(out, resolvers, base_url)


def _sources(out: _Out, page: dict) -> None:
    providers = page.get("providers")
    if providers is not None and not (isinstance(providers, list) and all(isinstance(p, str) and p for p in providers)):
        out.err("pages.sources.providers", "must be a list of provider names")
    alts = page.get("alternatives")
    if alts is not None and not (isinstance(alts, list) and all(isinstance(a, dict) and isinstance(a.get("key"), str) and a["key"]
                                                               for a in alts)):
        out.err("pages.sources.alternatives", "must be a list of {key, label?, host?, status?, ...}")


def _search(out: _Out, page: dict, base_url: str) -> None:
    for message in x_search.validate_spec(page, base_url or None):
        out.err("pages.search", str(message))


def _gate(out: _Out, gate: Any) -> None:
    if not _keys(out, "gate", gate, GATE_KEYS):
        return
    plain = {k: gate[k] for k in ("require", "probe") if k in gate}
    fn = _validator("gate")
    if fn is not None:
        for message in fn(plain):
            out.err("gate", str(message))
    else:
        if plain.get("require", "player") not in ("player", "stream"):
            out.err("gate.require", "must be player or stream")
        if "probe" in plain:
            _int(out, "gate.probe", plain["probe"], 0, 10)
    blocked = gate.get("blocked")
    if blocked is None:
        return
    fn = _validator("blocked")
    if fn is not None:
        for message in fn(blocked):
            out.err("gate.blocked", str(message))
    elif not isinstance(blocked, list):
        out.err("gate.blocked", "must be a list of rules")
    else:
        for i, rule in enumerate(blocked):
            if not isinstance(rule, dict):
                out.err(f"gate.blocked[{i}]", "must be a mapping")
                continue
            for key in ("html_regex", "iframe_src_regex"):
                if rule.get(key) is not None:
                    _regex(out, f"gate.blocked[{i}].{key}", rule[key])


# ---------------------------------------------------------------------------------------------- meta + samples

def _meta(out: _Out, meta: Any, site_map: dict) -> None:
    if not isinstance(meta, dict):
        out.err("meta", "must be a mapping of page type -> {samples, fingerprint, skipped, expect}")
        return
    pages = site_map.get("pages") if isinstance(site_map.get("pages"), dict) else {}
    base = str(site_map.get("base_url") or "")
    for page_type, entry in meta.items():
        where = f"meta.{page_type}"
        if page_type not in PAGE_TYPES:
            out.err(where, f"unknown page type (known: {', '.join(PAGE_TYPES)})")
            continue
        if not _keys(out, where, entry, META_KEYS):
            continue
        samples = entry.get("samples")
        if samples is not None and not (isinstance(samples, list) and all(isinstance(s, str) and s for s in samples)):
            out.err(f"{where}.samples", "must be a list of addresses")
            samples = None
        for key in ("fingerprint", "expect"):
            if entry.get(key) is not None and not isinstance(entry[key], dict):
                out.err(f"{where}.{key}", "must be a mapping")
        skipped = entry.get("skipped")
        if skipped is not None and not (isinstance(skipped, list) and all(isinstance(s, str) for s in skipped)):
            out.err(f"{where}.skipped", "must be a list of field names")
        page = pages.get(page_type) if isinstance(pages.get(page_type), dict) else {}
        patterns = classify.patterns_of(page)
        for i, sample in enumerate(samples or []):
            if not classify.same_site(sample, base):
                out.err(f"{where}.samples[{i}]", f"{sample!r} is not on the site's host")
            elif patterns and not any(classify.pattern_matches(p, sample, base) for p in patterns):
                out.err(f"pages.{page_type}.url_pattern", f"does not match its own sample {sample!r}")
        if page_type == "title" and samples and isinstance(page.get("key"), dict):
            keys = [classify.key_from(page["key"], s, base) for s in samples]
            for sample, key in zip(samples, keys):
                if not key:   # T30: the crawl falls back to the last path segment / canonical / id parameter
                    out.warn("pages.title.key", f"gives no key for sample {sample!r} (fallback key used)")
            seen: dict[str, str] = {}
            for sample, key in zip(samples, keys):
                if key and key in seen:
                    out.err("pages.title.key", f"samples {seen[key]!r} and {sample!r} give the same key {key!r}")
                elif key:
                    seen[key] = sample


# ---------------------------------------------------------------------------------------------- entry point

def _base_url(out: _Out, value: Any) -> None:
    if not isinstance(value, str) or not re.match(r"^https?://[^/\s]+", value):
        out.err("base_url", "must be an absolute http(s) address")
        return
    try:
        netguard.check_url(value)
    except ValueError as exc:
        out.err("base_url", f"rejected by netguard ({exc})")
    if urlsplit(value).path not in ("", "/"):
        out.warn("base_url", "has a path; only the origin is used")


def validate(data: Any) -> list[Problem]:
    """Every problem of a site map (empty = valid). ``level="error"`` blocks saving; ``warn`` is advice."""
    out = _Out()
    if not isinstance(data, dict):
        out.err("", "site map must be a mapping")
        return out.items
    _keys(out, "", data, TOP_KEYS)
    if data.get("map") != MAP_VERSION:
        out.err("map", f"must be {MAP_VERSION}")
    if not isinstance(data.get("site_id"), str) or not SITE_ID_RE.match(data["site_id"]):
        out.err("site_id", "must match [a-z0-9_]{2,32}")
    _base_url(out, data.get("base_url"))
    fetch = data.get("fetch")
    if fetch is not None and _keys(out, "fetch", fetch, FETCH_KEYS):
        if fetch.get("mode", "auto") not in FETCH_MODES:
            out.err("fetch.mode", f"must be one of {', '.join(FETCH_MODES)}")
        elif adapters.browser_refused(data):
            out.warn("fetch.mode", "browser is unguarded (its own requests skip netguard): the crawl and the stages "
                                   "fetch this site over http")
        if "delay_ms" in fetch:
            _int(out, "fetch.delay_ms", fetch["delay_ms"], 0, 60_000)
        if "concurrency" in fetch:
            _int(out, "fetch.concurrency", fetch["concurrency"], 1, 8)
        if "cookies" in fetch:
            from ...core.fetch import cookies as fetch_cookies
            if not isinstance(fetch["cookies"], list):
                out.err("fetch.cookies", "must be a list of {name, value} (value: a literal, now_ms or now_s)")
            else:
                for problem in fetch_cookies.check_specs(fetch["cookies"], "fetch.cookies"):
                    path, _, msg = problem.partition(": ")
                    out.err(path, msg)
    pages = data.get("pages")
    if pages is None:
        pages = {}
    if not isinstance(pages, dict):
        out.err("pages", "must be a mapping of page type -> rules")
        pages = {}
    for page_type, page in pages.items():
        where = f"pages.{page_type}"
        if page_type not in PAGE_KEYS:
            out.err(where, f"unknown page type (known: {', '.join(PAGE_TYPES)})")
            continue
        if not _keys(out, where, page, PAGE_KEYS[page_type]):
            continue
        if page.get("url_pattern") is not None:
            _regex(out, f"{where}.url_pattern", page["url_pattern"])
        if page.get("url_patterns") is not None:
            many = page["url_patterns"]
            if not isinstance(many, list) or not all(isinstance(p, str) and p for p in many):
                out.err(f"{where}.url_patterns", "must be a list of regexes (several address shapes of this page type)")
            else:
                for i, p in enumerate(many):
                    _regex(out, f"{where}.url_patterns[{i}]", p)
        if page_type == "home":
            _home(out, page)
        elif page_type == "listing":
            _listing(out, page)
        elif page_type == "title":
            _title(out, page)
        elif page_type == "episodes":
            _episodes(out, page, data)
        elif page_type == "player":
            _player(out, page, data.get("base_url"))
        elif page_type == "sources":
            _sources(out, page)
        elif page_type == "search":
            _search(out, page, str(data.get("base_url") or ""))
    if data.get("gate") is not None:
        _gate(out, data["gate"])
    if data.get("meta") is not None:
        _meta(out, data["meta"], data)
    return out.items


def errors(problems: list[Problem]) -> list[Problem]:
    return [p for p in problems if p.level == "error"]
