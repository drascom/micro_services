"""One frontier page -> catalogue writes + new frontier rows (LLM-free; every rule comes from the site map adapters).

- ``home``: section cards -> ``title`` rows (``target: episode`` cards -> ``episode`` rows), priority K0, origin
  ``("section", <section title>)``.
- ``listing``: rows -> ``title`` rows (origin ``("genre"|"type"|"listing", <name>)``), pagination -> the next ``listing``
  page (a next page that is already known, or a page without new rows, ends the list: no loop).
- ``title``: ``title`` rule -> ``upsert_title(origins=<the page's origins>)``; series: the episode list of the title page
  (``upsert_episodes``) + its season pages as ``season`` rows; film: ``set_movie_source``. A new title passes the gate
  first (``series_verdict`` / ``item_verdict``, daily probe budget); not public -> ``mark_blocked`` (counted, invisible).
- ``season``: episodes -> ``upsert_episodes``.
- ``episode`` (home episode cards only): ``player.title_link`` -> the title page (K0; a known title is crawled again).
Every page is also a structure check (``drift.check``, the page CONTENT fingerprint, never the address).

T30 provenance: a link's page type comes from WHERE it was found (a ``content: title`` section / listing row -> title page,
a title page's episode list -> episode page, ``player.title_link`` -> title page), recorded as ``origin_role``. The url
patterns are only a sanity check here: a link from the right place whose address fits no pattern is still crawled
(``frontier.warn(url_mismatch)``); a title address the key regex does not fit gets a fallback key (``fallback_key``)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ... import settings
from ...core.catalog import write as catalog
from ...core.extract import blocks as x_blocks, detail as x_detail, episodes as x_episodes, parse, urlpatterns
from ...core.extract._util import absolute
from ...core.fetch import FetchError
from ...core.play import gate as play_gate, prefill
from ..sitemap import adapters, classify, labels, store as map_store
from ..stages import common, playside
from . import drift, fetcher, frontier, state

GATE_DAILY = 60          # gate probe pages per site and day (fallback of the ``crawl_gate_daily_per_site`` setting)
MAX_LIST_PAGES = 500
MAP_TYPE = {"home": "home", "listing": "listing", "title": "title", "season": "episodes", "episode": "player", "sources": "player"}
_LISTING_ORIGIN = {"genre": "genre", "type": "type"}
#: origin kind of the title type a listing block gave its rows (A2 blocks): a crawl hint, never a catalogue origin
BLOCK_TYPE = "block_type"


@dataclass
class Outcome:
    ok: bool
    url: str
    page_type: str
    error: str = ""                       # FetchError code (or a short reason)
    fingerprint_ok: Optional[bool] = None
    reasons: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    mark: str = ""
    retry_at: Optional[int] = None        # keep the row ``new`` until then (gate budget)
    zero: bool = False                    # the page gave nothing (structure rule)
    fetched: bool = True                  # False: the page waited for a rule and was not fetched (no K1 quota)
    has_next: Optional[bool] = None       # listing: the page links a next page (not the list's last page)


class Ctx:
    """What the pages of one crawl run share: the site's map, the getter, the gate context."""

    def __init__(self, site_id: str, site_map: dict, get: Optional[Callable] = None, *, k1_complete: bool = False,
                 admin: bool = False):
        self.site_id = site_id
        self.admin = admin          # an admin-triggered crawl: outside the daily gate budget (not counted)
        self.map = site_map
        self.base = str(site_map.get("base_url") or "")
        self.get = get or fetcher.getter(site_map)
        self.k1_complete = k1_complete
        self._pctx: Any = False

    @property
    def pctx(self):
        if self._pctx is False:
            self._pctx = playside.play_ctx(self.map, fetcher.quiet(self.get))
        return self._pctx

    def pctx_for(self, kind: str):
        """T30: the play context of one page type (``pages.player.by_type``); the shared one without per-type rules."""
        player = (self.map.get("pages") or {}).get("player") or {}
        if not (player.get("by_type") or {}).get(kind):
            return self.pctx
        cache = self.__dict__.setdefault("_pctx_kind", {})
        if kind not in cache:
            cache[kind] = playside.play_ctx(self.map, fetcher.quiet(self.get), kind)
        return cache[kind]


def context(site_id: str, **kw) -> Ctx:
    site_map = map_store.active(site_id) or map_store.draft(site_id)
    return Ctx(site_id, site_map, **kw)


def tomorrow(t: float) -> int:
    lt = time.localtime(t)
    return int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + 1, 0, 0, 0, 0, 0, -1)))


def gate_daily() -> int:
    """Gate probe pages per site and calendar day (setting ``crawl_gate_daily_per_site``)."""
    try:
        return max(1, int(settings.get("crawl_gate_daily_per_site", GATE_DAILY)))
    except (TypeError, ValueError):
        return GATE_DAILY


def gate_used(site_id: str, st: Optional[dict] = None) -> int:
    st = state.get(site_id) if st is None else st
    return int(st.get("gate_used") or 0) if st.get("gate_date") == state.today() else 0


def reserve_gate(site_id: str, pages: int, *, admin: bool = False) -> bool:
    """Take ``pages`` from today's gate budget; False when it does not fit. ``admin``: always fits, never counted."""
    if admin:
        return True
    ok = {}
    limit = gate_daily()

    def fn(data: dict) -> None:
        day = state.today()
        if data.get("gate_date") != day:
            data.update(gate_date=day, gate_used=0)
        if int(data["gate_used"]) + pages <= limit:
            data["gate_used"] = int(data["gate_used"]) + pages
            ok["yes"] = True
    state.update(site_id, fn)
    return bool(ok)


def process(site_id: str, row: dict, ctx: Optional[Ctx] = None) -> Outcome:
    ctx = ctx or context(site_id)
    page_type, url = row["page_type"], row["url"]
    handler = HANDLERS.get(page_type)
    if handler is None or not _ready(ctx, page_type):
        return Outcome(True, url, page_type, mark="wait_rules", fetched=False)   # the rule comes with a later stage
    try:
        page = ctx.get(url, MAP_TYPE.get(page_type, page_type))
    except FetchError as exc:
        return Outcome(False, url, page_type, error=exc.code)
    except Exception as exc:   # a broken engine never kills the run
        return Outcome(False, url, page_type, error=type(exc).__name__)
    out = handler(ctx, row, page)
    if out.fingerprint_ok is None and page_type in ("home", "listing", "title"):
        out.fingerprint_ok, out.reasons = drift.check(ctx.map, page_type, page.html, last_page=out.has_next is not True)
    return out


def _ready(ctx: Ctx, page_type: str) -> bool:
    """The map has the rule this page type needs."""
    pages = ctx.map.get("pages") or {}
    if page_type == "episode":
        rule = (pages.get("player") or {}).get("title_link")
        return isinstance(rule, dict) and bool(rule.get("selector"))
    if page_type == "season":
        return bool(pages.get("episodes"))
    if page_type == "sources":
        return ctx.pctx is not None
    if page_type in ("title", "listing", "home"):
        return bool(pages.get(page_type))
    return True


# ---------------------------------------------------------------------------------------------- page types

def _sanity(ctx: Ctx, page_type: str, url: str) -> Optional[bool]:
    """T30: does a link of KNOWN provenance fit the url patterns of its type? A mismatch is counted as a warning
    (``url_mismatch``), never a reason to drop the link or change its type. None = the page type has no pattern."""
    fits = classify.matches_page(ctx.map, page_type, url)
    if fits is False:
        frontier.warn(ctx.site_id, "url_mismatch", url)
    return fits


def _home(ctx: Ctx, row: dict, page) -> Outcome:
    out = Outcome(True, row["url"], "home", counts={"titles": 0, "episodes": 0, "foreign": 0, "not_title": 0})
    base_page = common.page_base(page, row["url"])   # honours <base href>
    for info, selector, fields in adapters.home_rules(ctx.map) or []:
        section = str(info.get("title") or info.get("id") or "")
        for pos, card in enumerate(common.rows_of(page.html, selector, fields), 1):
            url = absolute(base_page, card.get("url"))
            if not url:
                continue
            if not classify.same_site(url, ctx.base):
                out.counts["foreign"] += 1
                continue
            if info.get("target") == "episode":
                frontier.add(ctx.site_id, url, "episode", priority=frontier.K0, parent_url=row["url"],
                             origin=("section", section), position=pos, role="episode_row")
                out.counts["episodes"] += 1
            else:   # a title section row IS a title page (provenance); the address shape is only checked
                if _sanity(ctx, "title", url) is False:
                    out.counts["not_title"] += 1
                frontier.add(ctx.site_id, url, "title", priority=frontier.K0, parent_url=row["url"],
                             origin=("section", section), position=pos, role="title_row")
                out.counts["titles"] += 1
    out.zero = not (out.counts["titles"] or out.counts["episodes"])
    return out


def _listing(ctx: Ctx, row: dict, page) -> Outcome:
    rule = adapters.listing_rule(ctx.map)
    if not rule or not rule.get("row_selector"):
        return Outcome(True, row["url"], "listing", mark="wait_rules")
    page_no = max(1, int(row.get("position") or 1))
    # T30: page 2.. of a list remembers its first page (``parent_url``): its own / its blocks' pagination are looked up there
    root = (row.get("parent_url") or row["url"]) if page_no > 1 and row.get("origin_role") == "next_page" else row["url"]
    pagination, walked = labels.pagination_for(rule, root)
    blocks = labels.blocks_for(rule, root)   # A2: a page with several blocks -> only the chosen blocks' rows
    if page_no > 1 and walked is not None:   # only the paginated block(s) go on; the others were read on page 1
        blocks = labels.walked_blocks(rule, root, walked)
    own = labels.rule_for(rule, root)   # T31: a list with its own row rule (a film archive's cards)
    rows = x_blocks.parse_rows(page.html, own["row_selector"], own.get("fields") or {}, blocks) if blocks else \
        common.rows_of(page.html, own["row_selector"], own.get("fields") or {})
    base_page = common.page_base(page, row["url"])   # honours <base href>
    origin = (row.get("origin_kind") or "listing", row.get("origin_value")) if row.get("origin_value") else None
    priority = frontier.K2 if ctx.k1_complete else frontier.K1
    out = Outcome(True, row["url"], "listing", counts={"rows": len(rows), "new": 0, "foreign": 0})
    headings = {b.get("index"): b["heading"] for b in blocks or [] if b.get("heading") and not b.get("ignore")}
    list_type = labels.list_type(rule, root)
    for i, card in enumerate(rows, 1):
        url = absolute(base_page, card.get("url"))
        if not url or not classify.same_site(url, ctx.base):
            out.counts["foreign"] += 1
            continue
        if _sanity(ctx, "title", url) is False:   # a listing row is a title page (provenance): warning only
            out.counts["url_mismatch"] = out.counts.get("url_mismatch", 0) + 1
        if frontier.add(ctx.site_id, url, "title", priority=priority, parent_url=row["url"], origin=origin,
                        position=(page_no - 1) * len(rows) + i, role="title_row"):
            out.counts["new"] += 1
        typed = card.get("_type") if card.get("_type") in ("series", "movie") else list_type
        if typed:   # provenance: a film list / block row is a film, a series list row a series (T30 mixed sites)
            frontier.add(ctx.site_id, url, "title", priority=priority, origin=(BLOCK_TYPE, typed))
        if card.get("_block") in headings:   # T30: a block row's origin = its heading (A2 "Ana ekranda" routing)
            frontier.add(ctx.site_id, url, "title", priority=priority, origin=("listing", headings[card["_block"]]))
    out.zero = not rows
    kind = pagination.get("kind") or "none"
    numbered = kind in labels.NUMBERED
    if rows and kind != "none" and page_no < MAX_LIST_PAGES and (not numbered or out.counts["new"]):
        if numbered:
            nxt = urlpatterns.page_url_of(pagination, root, page_no + 1)
        else:
            nxt = urlpatterns.page_url_of({**pagination, "attr": pagination.get("attr") or "href"}, base_page, 2, page.html)
            known = frontier.get(ctx.site_id, nxt) if nxt else None   # a "next" back to an earlier page: the last page
            out.has_next = bool(nxt) and nxt not in (row["url"], base_page) and \
                (known is None or int(known.get("position") or 0) > page_no)
        if nxt and nxt not in (row["url"], base_page) and classify.same_site(nxt, ctx.base) \
                and frontier.get(ctx.site_id, nxt) is None:
            frontier.add(ctx.site_id, nxt, "listing", priority=int(row["priority"]), parent_url=root,
                         origin=origin, position=page_no + 1, role="next_page")
            out.counts["next"] = 1
    return out


def _title_record(ctx: Ctx, url: str, html: str) -> dict:
    rule = adapters.title_rule(ctx.map) or {}
    values = parse.parse_detail(html, rule.get("fields") or {})
    key, fell_back = classify.title_key_info(ctx.map, url, html)
    if fell_back:
        frontier.warn(ctx.site_id, "fallback_key", url)
    rec = {"key": key, "type": _record_type(ctx, url, values, html),
           "title": values.get("title"), "source_url": url, "year": values.get("year"), "poster_url": values.get("poster"),
           "genres": values.get("genres"), "overview": x_detail.clean_overview(values.get("overview"))}
    for extra in ("rating", "cast"):
        if values.get(extra) not in (None, "", []):
            rec[extra] = values[extra]
    return {k: v for k, v in rec.items() if v not in (None, "", [])}


def _record_type(ctx: Ctx, url: str, values: dict, html: str = "") -> str:
    """T30 (mixed sites): provenance first (the type of the listing block / title list the title came from), then the
    title rule's ``map`` (address / field), then the page CONTENT (an episode list / season links -> series, a player on
    the title page -> movie), then the rule's ``default``."""
    hinted = [v for k, v, _p in frontier.origins(ctx.site_id, url) if k == BLOCK_TYPE and v in ("series", "movie")]
    if hinted:
        return hinted[0]
    rule = ((ctx.map.get("pages") or {}).get("title") or {}).get("type")
    if isinstance(rule, dict) and "default" in rule:
        bare = {**ctx.map, "pages": {**ctx.map["pages"], "title": {**ctx.map["pages"]["title"],
                                                                     "type": {k: v for k, v in rule.items() if k != "default"}}}}
        kind = classify.title_type(bare, url, values)
        if kind:
            return kind
    elif isinstance(rule, dict):
        kind = classify.title_type(ctx.map, url, values)
        if kind:
            return kind
    if html:
        kind = content_type(ctx, url, html)
        if kind:
            return kind
    return classify.title_type(ctx.map, url, values) or "series"


def content_type(ctx: Ctx, url: str, html: str) -> Optional[str]:
    """The title page's own content: episode links / season links -> series; a player candidate -> movie; else None."""
    from ..stages.a3_title import series_signal
    if series_signal(html, url) == "series":
        return "series"
    pctx = ctx.pctx_for("movie")
    if pctx is not None:
        try:
            from ...core.play import candidates as play_candidates
            if play_candidates.discover(pctx, html, url):
                return "movie"
        except Exception:   # noqa: BLE001 - no verdict, the default decides
            return None
    return None


def _page_fetcher(ctx: Ctx):
    get = fetcher.quiet(ctx.get)
    return lambda _pctx, url: (get(url, "player") or _missing(url)).html


def _missing(url: str):
    raise FetchError("unavailable", "page not fetched", url=url)


def _season_spec(ctx: Ctx) -> Optional[dict]:
    spec = adapters.episodes_spec(ctx.map)
    return {k: v for k, v in spec.items() if k != "series_url_regex"} if spec else None


def _gate_wait(out: Outcome, reason: str) -> Outcome:
    out.mark, out.retry_at = "gate_wait", tomorrow(state.now())
    out.counts["gate_wait"] = 1
    out.error = reason
    return out


def _title(ctx: Ctx, row: dict, page) -> Outcome:
    url = row["url"]
    out = Outcome(True, url, "title", counts={})
    if row.get("mark") == "blocked":
        out.mark = "blocked"
        return out
    rec = _title_record(ctx, url, page.html)
    if not rec.get("key") or not rec.get("title"):
        out.zero, out.mark, out.error = True, "empty", "no_title"
        return out
    try:
        catalog.upsert_title(ctx.site_id, rec, origins=[o for o in frontier.origins(ctx.site_id, url) if o[0] != BLOCK_TYPE])
    except Exception as exc:
        return Outcome(False, url, "title", error=f"write:{type(exc).__name__}")
    out.counts["titles"] = 1
    key, kind = rec["key"], rec["type"]
    frontier.set_key(ctx.site_id, url, key)
    pctx = ctx.pctx
    need_gate = row.get("mark") not in ("ok",)
    if pctx is None:
        out.mark = "wait_rules"       # no player rule yet (A5): the title waits for its playback rows
        return out
    pacer = play_gate.Pacer(sleep=lambda _s: None)   # core.fetch's limiter already paces the host
    try:
        if kind == "series":
            spec = adapters.episodes_spec(ctx.map)
            if not spec:
                out.mark = "wait_rules"
                return out
            inv = x_episodes.series_inventory(page.html, page.final_url or url, spec)
            _numbering_warn(ctx, inv, url)
            episodes = list(inv.get("video_sources") or [])
            seasons = [s for s in inv.get("season_pages") or [] if classify.same_site(s, ctx.base)]
            if need_gate:
                probe_eps = episodes or _season_probe(ctx, seasons)
                if not probe_eps and _gate_rules(pctx):
                    if seasons:    # season pages exist but none could be read: judge later (K5: never ``ok`` unprobed)
                        return Outcome(False, url, "title", error="gate_no_probe", counts=out.counts)
                    out.mark, out.error = "empty", "no_episodes"   # nothing to probe, nothing to show
                    return out
                if not reserve_gate(ctx.site_id, play_gate.probe_count(pctx), admin=ctx.admin):
                    return _gate_wait(out, "gate_budget")
                verdict = play_gate.series_verdict(pctx, probe_eps, fetcher=_page_fetcher(ctx), pacer=pacer)
                if verdict["state"] == play_gate.BLOCKED:
                    catalog.mark_blocked(ctx.site_id, url, kind="series", reason=verdict["reason"],
                                         via=verdict["via"] or "gate", key=key)
                    out.mark, out.counts["blocked"] = "blocked", 1
                    return out
                if not verdict["pages"] and _gate_rules(pctx):   # nothing was judged (no usable episode numbers)
                    return Outcome(False, url, "title", error="gate_no_probe", counts=out.counts)
                if verdict["state"] == play_gate.RETRY:
                    out.mark, out.retry_at, out.error = "gate_wait", int(state.now()) + 3600, "gate_retry"
                    return out
                for checked in verdict["pages"]:
                    if checked["state"] == play_gate.BLOCKED:
                        catalog.mark_blocked(ctx.site_id, checked["url"], kind="episode", reason=checked.get("reason") or "",
                                             via=checked.get("via") or "gate", key=key)
                episodes = play_gate.drop_episodes(episodes, set(verdict["blocked"]))
            entries = [{k: e[k] for k in ("season", "episode", "url", "title", "air_date") if k in e} for e in episodes]
            out.counts["episodes"] = catalog.upsert_episodes(ctx.site_id, key, entries, adapters.alternatives(ctx.map)) if entries else 0
            if out.counts["episodes"]:
                add_sources(ctx, entries, row)
            for s_url in seasons:
                frontier.add(ctx.site_id, s_url, "season", priority=int(row["priority"]), parent_url=url, role="season_link")
            if seasons and row.get("crawled_at"):   # a refresh: the newest season page is read again
                last = frontier.get(ctx.site_id, seasons[-1]) or {}
                if last.get("state") == "done":
                    frontier.reschedule(ctx.site_id, seasons[-1], state.now(), priority=frontier.K2)
            out.counts["seasons"] = len(seasons)
        else:
            if need_gate:
                if not reserve_gate(ctx.site_id, 1, admin=ctx.admin):
                    return _gate_wait(out, "gate_budget")
                verdict = play_gate.item_verdict(pctx, url, fetcher=lambda _c, _u: page.html, pacer=pacer)
                if verdict["state"] == play_gate.BLOCKED:
                    catalog.mark_blocked(ctx.site_id, url, kind="movie", reason=verdict["reason"],
                                         via=verdict["via"] or "gate", key=key)
                    out.mark, out.counts["blocked"] = "blocked", 1
                    return out
            catalog.set_movie_source(ctx.site_id, key, url, adapters.alternatives(ctx.map))
            out.counts["movies"] = 1
            res = collect_sources(ctx, url, page.html, kind="movie", key=key)   # the film page is the player page: no fetch
            out.counts["candidates"] = res.get("with_candidates", 0)
    except Exception as exc:
        return Outcome(False, url, "title", error=f"write:{type(exc).__name__}")
    out.mark = "ok"
    return out


def _numbering_warn(ctx: Ctx, inv: dict, url: str) -> None:
    """T30: episodes numbered from the row text / list order (not the address) are counted per page as warnings."""
    numbering = (inv.get("metrics") or {}).get("numbering") or {}
    for src in ("text", "position"):
        if numbering.get(src):
            frontier.warn(ctx.site_id, f"numbering_{src}", url)


def _gate_rules(pctx) -> bool:
    """The gate has something to check (a ``require`` or episode-page rules): a verdict needs a probed page."""
    return bool(play_gate.gate_of(pctx).get("require") or play_gate.episode_rules(pctx))


def _season_probe(ctx: Ctx, seasons: list) -> list:
    """Episodes of the first and the last season page (the gate's newest + oldest) when the title page lists none."""
    spec = _season_spec(ctx)
    if not spec or not seasons:
        return []
    get = fetcher.quiet(ctx.get)
    found = []
    for s_url in dict.fromkeys([seasons[0], seasons[-1]]):
        spage = get(s_url, "episodes")
        if spage is not None:
            found += x_episodes.series_inventory(spage.html, spage.final_url or s_url, spec).get("video_sources") or []
    return found


def _season(ctx: Ctx, row: dict, page) -> Outcome:
    out = Outcome(True, row["url"], "season", counts={"episodes": 0})
    parent = row.get("parent_url") or ""
    prow = frontier.get(ctx.site_id, parent) or {}
    if prow.get("mark") == "blocked":
        out.mark = "blocked"
        return out
    spec, key = _season_spec(ctx), prow.get("item_key") or classify.title_key_info(ctx.map, parent)[0]
    if not spec or not key:
        out.mark = "wait_rules"
        return out
    inv = x_episodes.series_inventory(page.html, page.final_url or row["url"], spec)
    _numbering_warn(ctx, inv, row["url"])
    entries = [{k: e[k] for k in ("season", "episode", "url", "title", "air_date") if k in e}
               for e in inv.get("video_sources") or []]
    try:
        out.counts["episodes"] = catalog.upsert_episodes(ctx.site_id, key, entries, adapters.alternatives(ctx.map)) if entries else 0
        if out.counts["episodes"]:
            add_sources(ctx, entries, prow or row)
    except Exception as exc:
        return Outcome(False, row["url"], "season", error=f"write:{type(exc).__name__}")
    out.zero = not entries
    return out


def _episode(ctx: Ctx, row: dict, page) -> Outcome:
    out = Outcome(True, row["url"], "episode", counts={})
    rule = ((ctx.map.get("pages") or {}).get("player") or {}).get("title_link")
    if not isinstance(rule, dict) or not rule.get("selector"):
        out.mark = "wait_rules"           # A5 gives the title link; the card waits for it
        return out
    value = parse.parse_detail(page.html, {"u": {"selector": rule["selector"], "attr": rule.get("attr") or "href"}}).get("u")
    title_url = absolute(common.page_base(page, row["url"]), value) if value else None
    if not title_url or not classify.same_site(title_url, ctx.base):
        out.zero, out.mark, out.error = True, "empty", "no_title_link"
        return out
    _sanity(ctx, "title", title_url)   # player.title_link points at the title page (provenance): warning only
    created = frontier.add(ctx.site_id, title_url, "title", priority=frontier.K0, parent_url=row["url"], role="title_link")
    frontier.copy_origins(ctx.site_id, row["url"], title_url)
    if not created:
        known = frontier.get(ctx.site_id, title_url) or {}
        if known.get("state") == "done" and known.get("mark") != "blocked":
            frontier.reschedule(ctx.site_id, title_url, state.now(), priority=frontier.K0)   # a new episode: read again
    out.counts["title"] = 1
    return out


def add_sources(ctx: Ctx, entries: list, row: dict) -> int:
    """T28: the episode pages just written go into the frontier as ``sources`` rows (read for their candidates; same quota /
    politeness / gate rules as every page; just below the title's own priority). Known rows keep their plan (the refresh
    re-reads them every ``source_recheck_days``)."""
    prio = max(1, int(row.get("priority") or frontier.K1) - 1)
    added = 0
    for e in entries:
        if e.get("url") and frontier.add(ctx.site_id, e["url"], "sources", priority=prio, parent_url=row.get("url"),
                                         role="episode_row"):
            added += 1
    return added


def collect_sources(ctx: Ctx, url: str, html: str, *, kind: str = "episode", key: str = "") -> dict:
    """The candidates of one player page -> its ``video_sources`` rows (``core.play.prefill``; no LLM, K6)."""
    pctx = ctx.pctx_for("movie" if kind == "movie" else "episode")
    if pctx is None:
        return {}
    found = prefill.collect(pctx, html, url)
    if found.get("blocked"):
        catalog.mark_blocked(ctx.site_id, url, kind=kind, reason=found["blocked"], via="rule", key=key)
        return {"blocked": 1}
    return prefill.store(ctx.site_id, url, found)


def _sources(ctx: Ctx, row: dict, page) -> Outcome:
    out = Outcome(True, row["url"], "sources", counts={})
    if row.get("mark") == "blocked":
        out.mark = "blocked"
        return out
    try:
        res = collect_sources(ctx, row["url"], page.html)
    except Exception as exc:
        return Outcome(False, row["url"], "sources", error=f"write:{type(exc).__name__}")
    if res.get("blocked"):
        out.mark, out.counts["blocked"] = "blocked", 1
        return out
    out.counts.update(rows=res.get("rows", 0), candidates=res.get("with_candidates", 0), no_source=res.get("no_source", 0))
    out.mark = "ok" if res.get("with_candidates") else "empty"
    return out


HANDLERS: dict[str, Callable[[Ctx, dict, Any], Outcome]] = {
    "home": _home, "listing": _listing, "title": _title, "season": _season, "episode": _episode, "sources": _sources}
