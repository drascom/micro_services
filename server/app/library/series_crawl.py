"""Full season/episode inventory of series, read politely from the source's series pages.

Why this exists: a home/list card names only the LATEST episode of a series ("4. Sezon 10. Bölüm"). Without
this stage a series in the library had exactly that one episode until somebody opened it, and even then the
old on-demand hydrate skipped it (the series already "had seasons"). Here the whole inventory (every season,
episode number, title, air date, episode page URL) is read from the source's series page and written to the
library with the normal identity / ``video_sources`` rules. Video hosts are NEVER resolved: an episode row only
holds the episode PAGE url (``resolver=page``); the player/hand-off is resolved when playback starts.

Pieces (all network access goes through ``fetch.page``, the project's own Obscura/Crawlee transport):

* ``due``       - when does a series need a (re)crawl: never read, unfinished/failed (backoff), a newer episode on
                  the home feed, an announced episode whose air date passed, or older than SERIES_CRAWL_REFRESH_DAYS.
* ``crawl``     - fetch the series page (+ season pages it did not list), parse with the yaml ``series_page``
                  selectors, verify against the site's own first/last-episode links. No writes.
* ``apply``     - merge one crawl result into a series' ``normalized`` dict (union: episodes and their playback
                  history are never removed; a real title is never replaced by a placeholder) + the inventory marker.
* ``run_stage`` - the budgeted pass run after every ingest (and by ``tools.series_crawl``): one request at a time,
                  delays, per-run series and time budgets, per-series error isolation.
* ``judge_series`` - content that is not public (``library/gate.py``): after the inventory is read, <= ``probe`` episode pages
                  (newest + oldest) are checked against the yaml ``blocked:`` rules / ``availability_gate:``; a blocked series is
                  not written (and removed when it already was), a blocked episode is dropped from the result.
"""
from __future__ import annotations

import copy
import datetime
import json
import logging
import re
import time
from typing import Any, Callable, Optional
from urllib.parse import urljoin, urlparse

from .. import config, db
from ..genres import canonical_labels
from ..scraper import blocked as sblocked, config as scfg, drift, fetch, series_generic, site_extractors
from . import gate, series_dir, tmdb, videos

log = logging.getLogger("library.series_crawl")

MARKER = "_series_inventory"
# underscore-prefixed keys of ``normalized`` are bookkeeping: ignored by the "updated" diff and carried over
# by every ingest (old values fill what a fresh card does not have).

# order in which due series are crawled (lower first): news first, never-read next, then retries, refreshes last
_RANK = {"forced": 0, "new_episode": 0, "aired": 1, "missing": 2, "retry": 3, "stale": 4}
_DETAIL_FIELDS = ("poster_url", "backdrop_url", "overview", "genres", "cast", "rating", "country", "runtime",
                  "followers", "trailer_url", "original_title")

_sleep = time.sleep  # tests replace it


def new_counters() -> dict[str, Any]:
    return {"budget": 0, "due": 0, "series": 0, "seasons": 0, "episodes": 0, "new_episodes": 0, "unaired": 0,
            "pages": 0, "incomplete": 0, "fallback": 0, "warnings": 0, "errors": 0, "deferred": 0, "skipped": 0,
            "skipped_unknown": 0, "backoff": 0, "resolved": 0, "unknown": 0,
            "seconds": 0.0, "stopped": None, "disabled": False, "items": [], "cids": [], "gone": [], "blocked": gate.new_counters()}


def _filled(value: Any) -> bool:
    return value is not None and value != "" and value != []


def _has(value: Any) -> bool:
    """Non-empty AND non-zero: a runtime/season of 0 is "unknown" when merging episode fields."""
    return _filled(value) and value is not False and value != 0


def enabled(cfg: scfg.SiteConfig) -> bool:
    """The inventory stage runs for a site with a code module marked ``series_catalog: true`` (yabancidizi) or for a
    module-less site whose yaml ``series_page`` carries the generic keys (``scraper/series_generic.py``); a site with
    neither is skipped. ``series_crawl: {enabled: false}`` switches either off."""
    block = cfg.data.get("series_crawl")
    if isinstance(block, dict) and block.get("enabled") is False:
        return False
    if cfg.data.get("series_catalog"):
        return True
    return series_generic.is_generic_spec(cfg.series_page) and not site_extractors.has_inventory_module(cfg.site_id)


# --- when is a series due --------------------------------------------------------------------------------

def _pair(value: Any) -> tuple[int, int]:
    try:
        return int(value[0]), int(value[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return (0, 0)


MIN_GAP = 3600.0  # a series that was read fine is not read again within this many seconds, whatever asks


#: multiples of SERIES_CRAWL_RETRY_HOURS between two attempts after the 1st, 2nd, 3rd, 4th+ consecutive failure of one series
#: (6 h -> 24 h -> 72 h -> 7 days with the default 6 h): a page that keeps failing is asked for less and less often
BACKOFF_STEPS = (1, 4, 12, 28)


def _gap(inv: dict, retry_hours: float) -> float:
    """Minimum seconds between two attempts: an hour after a good read; RETRY_HOURS after an unfinished one, then the
    ``BACKOFF_STEPS`` ladder per consecutive failure."""
    if inv.get("ok_at") and inv.get("complete") and not inv.get("fails"):
        return MIN_GAP
    step = BACKOFF_STEPS[min(max(int(inv.get("fails") or 0) - 1, 0), len(BACKOFF_STEPS) - 1)]
    return retry_hours * 3600.0 * step


def in_backoff(inv: Optional[dict], now: Optional[float] = None) -> bool:
    """Is the series waiting out a failure backoff (not a fresh good read: those are ``MIN_GAP`` business)?"""
    now = time.time() if now is None else now
    if not isinstance(inv, dict) or not inv.get("fails"):
        return False
    return now - float(inv.get("at") or 0) < _gap(inv, config.SERIES_CRAWL_RETRY_HOURS)


def latest_card_episode(norm: dict) -> Optional[tuple[int, int]]:
    """Highest ``(season, episode)`` a home card carries in ``video_sources`` (None without one)."""
    found = [(e["season"], e["episode"]) for e in norm.get("video_sources") or []
             if isinstance(e, dict) and e.get("kind") == "episode"
             and isinstance(e.get("season"), int) and isinstance(e.get("episode"), int)]
    return max(found) if found else None


def due(inv: Optional[dict], card: Optional[tuple[int, int]] = None, now: Optional[float] = None) -> Optional[str]:
    """Why a series needs a crawl (``missing`` | ``retry`` | ``new_episode`` | ``aired`` | ``stale``) or None.

    ``inv`` is the ``_series_inventory`` marker of the series (None = never crawled by this stage; the legacy
    ``_series_catalog_at`` stamp is deliberately ignored: the old code also stamped it after reading only
    the metadata, so it proves nothing about the episode list). ``card`` is the newest episode the home feed
    shows for the series in this run."""
    now = time.time() if now is None else now
    retry_hours = config.SERIES_CRAWL_RETRY_HOURS
    inv = inv if isinstance(inv, dict) else None
    if not inv:
        return "missing"
    if now - float(inv.get("at") or 0) < _gap(inv, retry_hours):
        return None  # asked recently (or backing off after failures)
    if not inv.get("ok_at"):
        return "retry"
    if not inv.get("complete"):
        return "retry"
    if card and card > max(_pair(inv.get("last")), _pair(inv.get("card"))):
        return "new_episode"
    next_air = inv.get("next_air")
    if next_air:
        try:
            if datetime.datetime.fromtimestamp(now, datetime.timezone.utc).date() > datetime.date.fromisoformat(next_air):
                return "aired"
        except ValueError:
            pass
    refresh = config.SERIES_CRAWL_REFRESH_DAYS
    if refresh and now - float(inv.get("ok_at") or 0) >= refresh * 86400.0:
        return "stale"
    return None


def source_rows(item_id: str) -> list:
    """``source_items`` rows of one library title in the order an on-demand read looks at them: sites that own a code
    inventory module first (yabancidizi), then the rest by name."""
    rows = db.query("SELECT * FROM source_items WHERE canonical_id=?", (item_id,))
    return sorted(rows, key=lambda row: (not site_extractors.has_inventory_module(row["source"]), row["source"]))


def inventory_due(item_id: str) -> bool:
    """On-demand check (detail request): does this opened series need its inventory read? Cheap, never raises.
    Looks at the first source of the title (``source_rows`` order) the inventory stage is enabled for."""
    try:
        for row in source_rows(item_id):
            try:
                cfg = scfg.load_site(row["source"])
                norm = json.loads(row["normalized"] or "{}")
            except (FileNotFoundError, TypeError, ValueError):
                continue
            if norm.get("type") != "series" or not enabled(cfg):
                continue
            if series_dir.unknown_for(norm) and not series_dir.resolvable(cfg, norm):
                return False   # no series page known for its URL: opening it again changes nothing
            return config.SERIES_CRAWL_BUDGET > 0 and due(norm.get(MARKER), None) is not None
        return False
    except Exception as exc:  # pragma: no cover - a detail request must never fail on this
        log.debug("inventory_due(%s) failed: %s", item_id, exc)
        return False


# --- one series: fetch + parse (no writes) -------------------------------------------------------------------

class NotSeriesPage(ValueError):
    """The page the inventory was asked to read is not a series page (``series_url_regex`` does not match): the record's
    ``source_url`` is wrong, which retrying never fixes (``series_dir`` / ``_series_page_unknown``), not a transient error."""


def series_url(cfg: scfg.SiteConfig, key: str, norm: dict) -> str:
    """The series page (not an episode page: only the series page lists every season)."""
    return urljoin(cfg.base_url + "/", norm.get("source_url") or key)


def crawl(cfg: scfg.SiteConfig, url: str) -> dict[str, Any]:
    """Read one series' inventory: its page, plus one page per declared season the first page did not list (a site
    module names those as ``<series>/sezon-N``; the generic engine returns the season-page URLs itself).

    Returns ``{episodes, unaired, metadata, seasons, declared, missing, first, last, pages, warnings, structured,
    drift}``. Raises ``ValueError`` when the page is not a series page at all (blocked / removed) and lets
    ``fetch.FetchError`` of the FIRST page propagate; a failing extra season page only makes the result
    incomplete."""
    spec = cfg.series_page
    delay = config.SERIES_CRAWL_DELAY
    root = re.match(r"^(https?://[^/]+/dizi/[^/?#]+)", url)   # site-module URL scheme (``/dizi/<slug>/sezon-N``)
    root_url = root.group(1) if root else url.rstrip("/")
    queue = [url]
    visited: list[str] = []
    episodes: dict[tuple[int, int], dict] = {}
    unaired: dict[tuple[int, int], Optional[str]] = {}
    metadata: dict[str, Any] = {}
    warnings: list[str] = []
    declared: set[int] = set()
    seen_seasons: set[int] = set()
    first = last = None
    structured = False
    drift_reasons: list[str] = []
    fetch_failed = False
    generic_pages = False   # the page parser names the other season pages itself (generic engine)
    parser_warnings: list[str] = []
    while queue and len(visited) < config.SERIES_CRAWL_MAX_PAGES:
        page_url = queue.pop(0)
        if page_url in visited:
            continue
        if visited:
            _sleep(delay)
        try:
            html = fetch.page(cfg, page_url)
        except Exception as exc:
            if not visited:
                raise
            fetch_failed = True
            warnings.append(f"sezon sayfası alınamadı: {str(exc)[:120]}")
            visited.append(page_url)
            continue
        visited.append(page_url)
        if len(visited) == 1:   # a ``blocked:`` rule on the series page itself: nothing to read, the series is not public
            hit = sblocked.match(html, gate.series_rules(cfg), "series_page")
            if hit:
                return {"episodes": [], "unaired": [], "metadata": {}, "seasons": [], "declared": [], "missing": [], "first": None,
                        "last": None, "pages": 1, "warnings": [], "structured": False, "drift": [], "fetch_failed": False,
                        "blocked_page": hit}
        inv = site_extractors.series_inventory(cfg.site_id, html, page_url, spec)
        if len(visited) == 1:
            structured = bool(inv.get("structured"))
            if spec and inv.get("metrics") is not None:
                verdict = drift.detect(inv["metrics"], (cfg.baseline() or {}).get("series_page") or {"min_items": 1})
                if verdict["drift"]:
                    drift_reasons = list(verdict["reasons"])
                    warnings.append("series_page seçicileri kaymış: " + "; ".join(drift_reasons)[:200])
        for field, value in (inv.get("metadata") or {}).items():
            if _filled(value):
                metadata[field] = value
        for entry in inv.get("video_sources") or []:
            episodes[(entry["season"], entry["episode"])] = entry
        for item in inv.get("unaired") or []:
            unaired[(item["season"], item["episode"])] = item.get("air_date")
        declared.update(inv.get("declared_seasons") or [])
        seen_seasons.update(inv.get("tab_seasons") or [])
        first = first or inv.get("first")
        last = last or inv.get("last")
        parser_warnings.extend(inv.get("warnings") or [])
        warnings.extend(w for w in inv.get("warnings") or [] if w not in warnings)
        if "season_pages" in inv:  # generic engine: it lists the other season pages (absolute URLs) itself
            generic_pages = True
            more = list(inv["season_pages"] or [])
        else:  # a site module: a declared season without a panel here has its own page ``<series>/sezon-N`` (bounded)
            more = [f"{root_url}/sezon-{season}" for season in sorted(declared - seen_seasons)]
        for season_url in more:
            if season_url not in visited and season_url not in queue:
                queue.append(season_url)
    missing = sorted(declared - seen_seasons)
    if generic_pages and queue:  # season pages the page cap kept us from reading: incomplete, not lost
        fetch_failed = True
        warnings.append(f"{len(queue)} sezon sayfası SERIES_CRAWL_MAX_PAGES sınırı yüzünden okunmadı")
    if not episodes and not unaired and not metadata.get("title"):
        why = f": {parser_warnings[0]}" if generic_pages and parser_warnings else ""
        if any("series_url_regex" in w for w in parser_warnings):
            raise NotSeriesPage("sayfa dizi sayfası değil (series_url_regex eşleşmedi)")
        raise ValueError("sayfa bir dizi sayfası gibi görünmüyor (engellenmiş ya da yapı değişmiş olabilir)" + why)
    result = {
        "episodes": [episodes[key] for key in sorted(episodes)],
        "unaired": [{"season": s, "episode": e, "air_date": d} for (s, e), d in sorted(unaired.items())],
        "metadata": metadata, "seasons": sorted({s for s, _ in episodes}), "declared": sorted(declared),
        "missing": missing, "first": first, "last": last, "pages": len(visited), "warnings": warnings,
        "structured": structured, "drift": drift_reasons, "fetch_failed": fetch_failed,
    }
    result["warnings"].extend(w for w in verify(result) if w not in warnings)
    return result


def verify(result: dict) -> list[str]:
    """Compare the parsed list with the site's own first/last-episode links; the caller logs the findings."""
    problems: list[str] = []
    keys = sorted((e["season"], e["episode"]) for e in result["episodes"])
    if not keys:
        return problems
    first, last = result.get("first"), result.get("last")
    if first and tuple(first) != keys[0]:
        problems.append(f"ilk bölüm bağlantısı s{first[0]}e{first[1]}, listedeki ilk s{keys[0][0]}e{keys[0][1]}")
    if last and tuple(last) != keys[-1]:
        problems.append(f"son bölüm bağlantısı s{last[0]}e{last[1]}, listedeki son s{keys[-1][0]}e{keys[-1][1]}")
    return problems


# --- content that is not public (library/gate.py) -------------------------------------------------------------------

def judge_series(cfg: scfg.SiteConfig, key: str, norm: dict, result: dict, url: str, now: float,
                 pacer: Optional[gate.Pacer] = None) -> Optional[dict]:
    """Judge the series of a fresh ``crawl`` result against the site's ``blocked:`` rules / ``availability_gate:``; None when the
    site has neither (nothing changes). Returns the verdict (``gate.series_verdict`` shape + ``removable``): ``blocked`` = every
    probed episode page (or the series page) is blocked; ``retry`` = a transient failure, nothing is known yet; ``ok`` = the
    series is taken (``result['episodes']`` loses the blocked episode pages, the verdict is stored in ``blocked_pages``). A series
    the gate verified within ``BLOCKED_RECHECK_DAYS`` (``_gate`` marker) is not probed again. ``removable`` says whether the
    series has to leave the library (blocked, or transient while it was never verified ok)."""
    if not gate.configured(cfg):
        return None
    site = cfg.site_id
    previous = norm.get(gate.MARKER) if isinstance(norm.get(gate.MARKER), dict) else {}
    if result.get("blocked_page"):
        hit = result["blocked_page"]
        verdict = {"state": gate.BLOCKED, "reason": hit["reason"], "via": "rule", "probed": 0, "pages": [], "blocked": [], "retry": [],
                   "evidence": hit.get("evidence", "")}
    elif gate.marker_ok(previous, now):
        verdict = {"state": gate.OK, "reason": "", "via": "", "probed": 0, "pages": [], "blocked": [], "retry": [], "cached": True}
    else:
        known = gate.fresh_blocked_urls(site, key)
        verdict = gate.series_verdict(cfg, result.get("episodes") or [], known=known, pacer=pacer)
        verdict["blocked"] = sorted(set(verdict["blocked"]) | set(known))
    if not verdict.get("cached"):
        gate.store(site, key, url, verdict, kind=gate.KIND_SERIES, now=int(now))
    verified = previous.get("state") == gate.OK
    verdict["removable"] = verdict["state"] == gate.BLOCKED or (verdict["state"] == gate.RETRY and not verified)
    return verdict


def gate_apply(norm: dict, result: dict, verdict: dict, now: float) -> bool:
    """Apply ``judge_series``'s verdict to the crawl ``result`` / the series' ``normalized``: True = the series is taken (go on with
    ``apply``). An ok series loses its blocked episode pages and carries the ``_gate`` marker; a blocked / unverified-transient one
    gets the marker (``_crawl_persist`` then removes it from the library); a transient failure of an already verified series is
    only a failed attempt (backoff), nothing else changes."""
    state = verdict["state"]
    if state == gate.OK:
        if verdict.get("blocked"):
            result["episodes"] = gate.drop_episodes(result["episodes"], set(verdict["blocked"]))
        if not verdict.get("cached"):
            norm[gate.MARKER] = gate.make_marker(verdict, now)
        return True
    if verdict["removable"]:
        norm[gate.MARKER] = gate.make_marker(verdict, now)
        return False
    mark_failed(norm, RuntimeError(verdict.get("reason") or "geçici denetim hatası"), now)
    return False


def gate_removes(norm: dict) -> bool:
    """The series' ``_gate`` marker says it must not be in the library (blocked, or not yet verified after a transient failure)."""
    marker = norm.get(gate.MARKER)
    return isinstance(marker, dict) and marker.get("state") in (gate.BLOCKED, gate.RETRY)


# --- merge into normalized ---------------------------------------------------------------------------------------

def episode_key(entry: Any) -> Optional[tuple]:
    if not isinstance(entry, dict):
        return None
    if entry.get("kind") == "episode" and isinstance(entry.get("season"), int) and isinstance(entry.get("episode"), int):
        return ("episode", entry["season"], entry["episode"])
    return (entry.get("kind"), entry.get("key") or entry.get("url"))


def merge_sources(old: list, new: list) -> list:
    """Union of two ``video_sources`` lists keyed by episode: fields of ``new`` override, the rest of ``old`` stays
    (a home card must never shrink the inventory nor blank a title/air date the inventory already knows)."""
    merged: dict[tuple, dict] = {}
    for entry in list(old or []) + list(new or []):
        key = episode_key(entry)
        if key is None:
            continue
        if key not in merged:
            merged[key] = dict(entry)
        else:
            merged[key].update({k: v for k, v in entry.items() if _has(v)})
    episodes = sorted((k for k in merged if k[0] == "episode"), key=lambda k: k[1:])
    return [merged[k] for k in episodes + [k for k in merged if k[0] != "episode"]]


def reapply_detail(norm: dict, old: dict) -> None:
    """Page-derived series facts win over the home card's thinner ones (poster, overview, cast, ...)."""
    kept = old.get("_series_detail_metadata")
    if isinstance(kept, dict):
        for field in _DETAIL_FIELDS:
            if _filled(kept.get(field)):
                norm[field] = kept[field]


def _next_air(unaired: list[dict], today: datetime.date) -> Optional[str]:
    future = sorted(d for d in (u.get("air_date") for u in unaired) if d and d > today.isoformat())
    return future[0] if future else None


def apply(norm: dict, result: dict, now: float, card: Optional[tuple[int, int]] = None) -> int:
    """Merge a crawl result into ``norm`` (in place). Returns the number of NEW episodes."""
    known = {episode_key(e) for e in norm.get("video_sources") or [] if episode_key(e) and episode_key(e)[0] == "episode"}
    old_by_key = {episode_key(e): e for e in norm.get("video_sources") or [] if episode_key(e)}
    fresh = []
    for entry in result["episodes"]:
        entry = dict(entry)
        old = old_by_key.get(episode_key(entry)) or {}
        if _filled(old.get("title")) and not tmdb.generic_episode_title(old["title"]) and tmdb.generic_episode_title(entry.get("title")):
            entry["title"] = old["title"]  # a real title is never replaced by "N. Bölüm" / "Episode N"
        for field in ("overview", "runtime"):
            if _filled(old.get(field)) and not _filled(entry.get(field)):
                entry[field] = old[field]
        fresh.append(entry)
    new_count = sum(1 for e in fresh if episode_key(e) not in known)
    norm["video_sources"] = merge_sources(norm.get("video_sources") or [], fresh)

    applied: dict[str, Any] = {}
    for field, value in result["metadata"].items():
        if not _filled(value) or (field == "rating" and not (isinstance(value, (int, float)) and value > 0)):
            continue  # 0.0 is "no rating yet", not a rating
        if field == "genres":
            mapped, unmapped = canonical_labels(value)
            if unmapped:
                norm["genres_unmapped"] = unmapped  # kept for review, never shown as genres
            if not mapped:
                continue
            value = mapped
        if field in ("title", "year", "original_title") and _filled(norm.get(field)):
            continue  # identity fields stay as the catalogue card / TMDB matching set them
        norm[field] = value
        if field in _DETAIL_FIELDS:
            applied[field] = value
    norm["_series_detail_metadata"] = applied

    today = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).date()
    keys = [(e["season"], e["episode"]) for e in fresh]
    has_content = bool(keys) or bool(result["unaired"])
    previous = norm.get(MARKER) if isinstance(norm.get(MARKER), dict) else {}
    norm[MARKER] = {
        "at": int(now), "ok_at": int(now), "fails": 0, "error": None,
        "complete": has_content and not result["missing"] and not result.get("fetch_failed"),
        "seasons": len({s for s, _ in keys}), "episodes": len(keys), "unaired": len(result["unaired"]),
        "last": list(max(keys)) if keys else None, "next_air": _next_air(result["unaired"], today),
        "card": list(card) if card else previous.get("card"), "pages": result.get("pages", 1),
        "structured": bool(result.get("structured")), "missing": result["missing"],
        "warnings": list(result["warnings"])[:5],
    }
    return new_count


def mark_failed(norm: dict, exc: Exception, now: float) -> None:
    """Remember a failed attempt (backoff) without touching what an earlier crawl learned."""
    previous = norm.get(MARKER) if isinstance(norm.get(MARKER), dict) else {}
    norm[MARKER] = {**previous, "at": int(now), "fails": int(previous.get("fails") or 0) + 1,
                    "error": str(exc)[:200], "complete": bool(previous.get("complete")) and bool(previous.get("ok_at"))}


def _merge3(base: dict, mine: dict, current: dict) -> dict:
    """Re-apply what the crawl changed (``mine`` vs ``base``) on the row as it is NOW (``current``)."""
    out = dict(current)
    for key in set(mine) | set(base):
        if mine.get(key) == base.get(key):
            continue
        if key == "video_sources":
            out[key] = merge_sources(current.get(key) or [], mine.get(key) or [])
        elif key in mine:
            out[key] = mine[key]
        else:
            out.pop(key, None)
    return out


# --- candidates + the budgeted stage -------------------------------------------------------------------

def select(site: str, cfg: scfg.SiteConfig, by_key: Optional[dict] = None, now: Optional[float] = None,
           force: bool = False, only: Optional[str] = None, stats: Optional[dict] = None) -> list[dict]:
    """Series of ``site`` that need a crawl, most urgent first: ``[{key, cid, reason, card, at}]``. A series whose ``source_url``
    is no series page and for which no series page is known (``series_dir``: ``_series_page_unknown`` for that very URL) is left out
    (``stats["skipped_unknown"]``), one still waiting out a failure backoff is counted in ``stats["backoff"]``; neither is an error."""
    now = time.time() if now is None else now
    out = []
    directory = None
    for row in db.query("SELECT source_key, canonical_id, normalized FROM source_items WHERE source=?", (site,)):
        if only and row["source_key"] != only:
            continue
        if not row["canonical_id"]:
            continue
        try:
            norm = json.loads(row["normalized"] or "{}")
        except (TypeError, ValueError):
            continue
        if norm.get("type") != "series":
            continue
        in_run = bool(by_key and row["source_key"] in by_key)
        card = latest_card_episode(by_key[row["source_key"]]) if in_run else None
        inv = norm.get(MARKER) if isinstance(norm.get(MARKER), dict) else {}
        if series_dir.unknown_for(norm):   # no series page known for this URL: not retried until the URL (or the directory) changes
            directory = directory if directory is not None else series_dir.for_site(cfg)
            if not series_dir.resolvable(cfg, norm, directory):
                if stats is not None:
                    stats["skipped_unknown"] = stats.get("skipped_unknown", 0) + 1
                continue
        reason = "forced" if force else due(inv, card, now)
        if not reason and stats is not None and in_backoff(inv, now):
            stats["backoff"] = stats.get("backoff", 0) + 1
        if reason:
            out.append({"key": row["source_key"], "cid": row["canonical_id"], "reason": reason, "card": card,
                        "in_run": in_run, "at": int(inv.get("at") or 0)})
    out.sort(key=lambda c: (_RANK.get(c["reason"], 9), 0 if c["in_run"] else 1, c["at"], c["key"]))
    return out


def _crawl_persist(cfg: scfg.SiteConfig, site: str, cand: dict, ingest_module) -> dict:
    """Crawl one series and write it (row re-read under the discovery lock; concurrent edits are merged)."""
    key = cand["key"]
    row = db.query_one("SELECT * FROM source_items WHERE source=? AND source_key=?", (site, key))
    if not row:
        return {"status": "skipped", "key": key}
    try:
        base = json.loads(row["normalized"] or "{}")
        raw = json.loads(row["raw"] or "{}")
    except (TypeError, ValueError):
        return {"status": "skipped", "key": key}
    mine = copy.deepcopy(base)
    reports: list[dict] = []
    errors: list[dict] = []
    ingest_module._enrich_series_catalogs(cfg, {key: mine}, {key: raw}, reports, errors,
                                          force=True, cards={key: cand["card"]} if cand["card"] else None)
    report = dict(reports[-1]) if reports else {"status": "skipped"}
    report["key"] = key
    report["cid"] = row["canonical_id"]
    if errors and report.get("status") != "error":
        report["status"] = "error"
        report["error"] = errors[-1].get("error")
    if gate_removes(mine) and mine.get(gate.MARKER) != base.get(gate.MARKER):
        # content that is not public: the series is not written, and when an earlier scan wrote it, it leaves the library
        # (user data stays; ``blocked_pages`` keeps the verdict so the next scans do not write it again)
        report["status"] = "blocked" if mine[gate.MARKER]["state"] == gate.BLOCKED else "retry"
        report["removed"] = gate.remove_items(site, [key])
        report["written"] = True
        return report
    if mine != base:
        with ingest_module._discovery_lock:
            conn = db.connect()
            try:
                current_row = conn.execute("SELECT normalized FROM source_items WHERE source=? AND source_key=?",
                                           (site, key)).fetchone()
                if current_row is None:
                    return report
                if current_row["normalized"] == row["normalized"]:
                    final = mine
                else:
                    final = _merge3(base, mine, json.loads(current_row["normalized"] or "{}"))
                conn.execute(
                    "UPDATE source_items SET normalized=?, trailer_url=COALESCE(?, trailer_url), source_url=COALESCE(?, source_url) "
                    "WHERE source=? AND source_key=?",
                    (json.dumps(final, ensure_ascii=False), final.get("trailer_url"), final.get("source_url"), site, key))
                ingest_module.merge_canonical(conn, row["canonical_id"])
                videos.sync_source(conn, site, key, row["canonical_id"], final, cfg)
                conn.commit()
                report["written"] = True
            finally:
                conn.close()
    return report


def run_stage(cfg: scfg.SiteConfig, site: str, by_key: Optional[dict] = None, *, force: bool = False,
              limit: Optional[int] = None, dry_run: bool = False, only: Optional[str] = None,
              on_progress: Optional[Callable[[], None]] = None) -> dict[str, Any]:
    """The budgeted inventory pass: at most ``limit``/SERIES_CRAWL_BUDGET series and SERIES_CRAWL_SECONDS per run,
    one request at a time with SERIES_CRAWL_DELAY between series, a failing series never stops the others (only
    SERIES_CRAWL_MAX_ERRORS failures in a row do: then the site is most likely blocking us). What is not reached
    stays due and is the first thing the next run does. Returns the run counters (``items``: per-series lines).
    ``on_progress()`` is called after every series whose episodes were just written to the library (so a long pass
    is visible while it runs); it may be slow or raise, the pass carries on either way."""
    from . import ingest as ingest_module  # late: ingest imports this module

    counters = new_counters()
    if not enabled(cfg):
        counters["disabled"] = True
        return counters
    budget = config.SERIES_CRAWL_BUDGET if limit is None else max(0, int(limit))
    counters["budget"] = budget
    if budget <= 0:
        counters["disabled"] = True
        return counters
    started = time.monotonic()
    select_stats: dict[str, int] = {}
    candidates = select(site, cfg, by_key, force=force, only=only, stats=select_stats)
    counters["due"] = len(candidates)
    counters["skipped_unknown"] += select_stats.get("skipped_unknown", 0)
    counters["backoff"] += select_stats.get("backoff", 0)
    crawled = 0
    streak = 0
    fetched_before = False
    for index, cand in enumerate(candidates):
        left = len(candidates) - index
        if crawled >= budget:
            counters["stopped"] = "budget"
        elif time.monotonic() - started >= config.SERIES_CRAWL_SECONDS:
            counters["stopped"] = "time"
        elif streak >= config.SERIES_CRAWL_MAX_ERRORS:
            counters["stopped"] = "errors"
        if counters["stopped"]:
            counters["deferred"] = left
            break
        if dry_run:
            counters["items"].append({"key": cand["key"], "status": "due", "reason": cand["reason"]})
            counters["deferred"] += 1
            continue
        if fetched_before:
            _sleep(config.SERIES_CRAWL_DELAY)
        t0 = time.monotonic()
        try:
            report = _crawl_persist(cfg, site, cand, ingest_module)
        except Exception as exc:  # isolation: one series never stops the pass
            log.warning("series crawl %s/%s failed: %s", site, cand["key"], exc)
            report = {"status": "error", "key": cand["key"], "error": str(exc)[:200]}
        status = report.get("status")
        line = {"key": cand["key"], "status": status, "reason": cand["reason"],
                "seconds": round(time.monotonic() - t0, 2)}
        if status in ("success", "error") and report.get("url"):
            line["url"] = str(report["url"])[:200]   # the series page (the heal evidence of a pass that mostly fails: scraper/playheal.py)
        if status in ("blocked", "retry"):   # content that is not public / a transient gate failure (library/gate.py)
            crawled += 1
            streak = 0
            fetched_before = True
            counters["pages"] += int(report.get("pages") or 0)
            verdict = report.get("gate") or {"state": status, "probed": 0, "pages": [], "reason": report.get("reason") or ""}
            gate.count(counters["blocked"], verdict, kind=gate.KIND_SERIES)
            counters["blocked"]["removed"] += int(report.get("removed") or 0)
            if report.get("removed") or report.get("written"):   # taken out of the library: the rest of the scan must not count it
                counters["gone"].append(cand["key"])
            line["reason_blocked"] = (verdict.get("reason") or "")[:80]
        elif status == "success":
            crawled += 1
            streak = 0
            fetched_before = True
            counters["series"] += 1
            if report.get("cid") and report["cid"] not in counters["cids"]:
                counters["cids"].append(report["cid"])
            counters["pages"] += int(report.get("pages") or 0)
            counters["seasons"] += int(report.get("seasons") or 0)
            counters["episodes"] += int(report.get("count") or 0)
            counters["new_episodes"] += int(report.get("new") or 0)
            counters["unaired"] += int(report.get("unaired") or 0)
            counters["warnings"] += len(report.get("warnings") or [])
            counters["incomplete"] += 0 if report.get("complete") else 1
            counters["fallback"] += 0 if report.get("structured") else 1
            line.update({"episodes": report.get("count"), "new": report.get("new"), "seasons": report.get("seasons")})
            if report.get("gate"):   # a taken series may have lost some blocked episode pages
                gate.count(counters["blocked"], {**report["gate"], "state": gate.OK}, kind=gate.KIND_SERIES)
        elif status == "error":   # a failed read is no quota: only a successful read counts against the budget (the streak and the time budget still bound it)
            streak += 1
            fetched_before = True
            counters["errors"] += 1
            line["error"] = report.get("error")
        elif status == "unknown":   # no series page known for the card's URL: no request was made, no error, no quota
            counters["skipped_unknown"] += 1
            line["reason_unknown"] = "no series page known for " + str(report.get("url") or "")[:100]
        else:
            counters["skipped"] += 1
        counters["items"].append(line)
        if on_progress is not None and status == "success" and report.get("written"):
            try:
                on_progress()
            except Exception as exc:  # a consumer's problem never stops the inventory
                log.warning("series crawl %s: progress callback failed: %s", site, exc)
    counters["items"] = counters["items"][:40]
    counters["seconds"] = round(time.monotonic() - started, 2)
    if counters["due"] or counters["series"] or counters["errors"]:
        log.info("series crawl %s: due=%d crawled=%d episodes=%d (+%d) errors=%d deferred=%d stopped=%s",
                 site, counters["due"], counters["series"], counters["episodes"], counters["new_episodes"],
                 counters["errors"], counters["deferred"], counters["stopped"])
    return counters
