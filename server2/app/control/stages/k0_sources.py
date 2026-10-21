"""Playback rows of catalogue titles, outside the crawl (T11 replaced the ``k0_sources`` job with the crawl's title pages;
what stays is the one-title path of the problem core's "search elsewhere" and ``run`` for a direct pass):
every title the site already has in the catalogue gets its playback rows: a series its episode inventory
(``upsert_episodes``), a film its film page (``set_movie_source``). The site's gate decides first: a title / episode
page that is not public is stored with ``mark_blocked`` (K5: invisible to clients) and gets no row."""
from __future__ import annotations

import json
from typing import Any

from ... import db
from ...core.catalog import write as catalog
from ...core.play import gate as play_gate
from ..sitemap import adapters, classify, store as map_store
from . import playside
from .a4_episodes import series_episodes
from .a6_sources import site_getter

TIMEOUT_S = 900
MAX_TITLES = 60


def _titles(site_id: str) -> list[dict]:
    out = []
    for row in db.query("SELECT key, source_url, normalized FROM source_items WHERE site_id=? ORDER BY fetched_at, key LIMIT ?",
                        (site_id, MAX_TITLES)):
        try:
            norm = json.loads(row["normalized"] or "{}")
        except ValueError:
            norm = {}
        out.append({"key": row["key"], "url": row["source_url"], "type": norm.get("type") or norm.get("kind")})
    return out


def _context(site_id: str):
    site_map = map_store.active(site_id) or map_store.draft(site_id)
    get = site_getter(site_id, site_map)
    return site_map, get, playside.play_ctx(site_map, get)


def prepare_title(site_id: str, site_map: dict, get, pctx, title: dict, out: dict) -> None:
    """Playback rows of ONE catalogue title of the site (gate first); counts into ``out``."""
    base = str(site_map.get("base_url") or "")
    fetcher = lambda _ctx, url: (get(url, "player") or _missing(url)).html   # noqa: E731
    pacer = play_gate.Pacer(sleep=lambda _s: None)   # core.fetch's limiter already paces the site's host (delay_ms)
    url = title["url"]
    if not url or not classify.same_site(url, base):
        out["failed"] += 1
        return
    kind = title.get("type") or classify.title_type(site_map, url) or "series"
    try:
        if kind == "series":
            episodes, _info = series_episodes(get, site_map, url)
            if not episodes:
                out["failed"] += 1
                return
            verdict = play_gate.series_verdict(pctx, episodes, fetcher=fetcher, pacer=pacer)
            if verdict["state"] == play_gate.BLOCKED:
                catalog.mark_blocked(site_id, url, kind="series", reason=verdict["reason"], via=verdict["via"] or "gate",
                                     key=title["key"])
                out["blocked"] += 1
                return
            for page in verdict["pages"]:
                if page["state"] == play_gate.BLOCKED:
                    catalog.mark_blocked(site_id, page["url"], kind="episode", reason=page.get("reason") or "",
                                         via=page.get("via") or "gate", key=title["key"])
            episodes = play_gate.drop_episodes(episodes, set(verdict["blocked"]))
            out["episodes"] += catalog.upsert_episodes(site_id, title["key"], episodes, adapters.alternatives(site_map))
            out["series"] += 1
        else:
            verdict = play_gate.item_verdict(pctx, url, fetcher=fetcher, pacer=pacer)
            if verdict["state"] == play_gate.BLOCKED:
                catalog.mark_blocked(site_id, url, kind="movie", reason=verdict["reason"], via=verdict["via"] or "gate",
                                     key=title["key"])
                out["blocked"] += 1
                return
            catalog.set_movie_source(site_id, title["key"], url, adapters.alternatives(site_map))
            out["movies"] += 1
    except Exception:
        out["failed"] += 1


def _empty() -> dict:
    return {"series": 0, "movies": 0, "episodes": 0, "blocked": 0, "failed": 0}


def run(site_id: str) -> dict:
    site_map, get, pctx = _context(site_id)
    out = _empty()
    if pctx is None:
        return {**out, "error": "player sayfası kuralı yok"}
    for title in _titles(site_id):
        prepare_title(site_id, site_map, get, pctx, title, out)
    return out


def run_one(site_id: str, canonical_id: str) -> dict:
    """Playback rows of the site's catalogue records of ONE canonical title (the problem core's "search elsewhere")."""
    site_map, get, pctx = _context(site_id)
    out = _empty()
    if pctx is None:
        return {**out, "error": "player sayfası kuralı yok"}
    for row in db.query("SELECT key, source_url, normalized FROM source_items WHERE site_id=? AND canonical_id=?",
                        (site_id, canonical_id)):
        try:
            norm = json.loads(row["normalized"] or "{}")
        except ValueError:
            norm = {}
        prepare_title(site_id, site_map, get, pctx,
                      {"key": row["key"], "url": row["source_url"], "type": norm.get("type") or norm.get("kind")}, out)
    return out


def _missing(url: str):
    from ...core.fetch import FetchError
    raise FetchError("unavailable", "page not fetched", url=url)
