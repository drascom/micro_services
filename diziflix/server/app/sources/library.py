"""Source adapter backed by the canonical library (SQLite).

Serves the same internal catalogue shape as the mock adapter, so ``app.rows``
and the API contract are unchanged — only the data is now real films ingested
from sinemalar (and, later, other sources) via ``app.library.ingest``.

Enable with ``SOURCE=library``. Mock stays the default.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from .. import db
from ..library import seasons as tmdb_seasons
from ..library.normalize import is_placeholder_image
from .base import SourceAdapter

log = logging.getLogger("sources.library")

# Catalogue navigation uses genres, not country/format tags found on source cards.
DISPLAY_GENRES = set("Aksiyon|Komedi|Dram|Korku|Bilim Kurgu|Gerilim|Romantik|Animasyon|Belgesel|Macera|Suç|Fantastik|Aile|Savaş|Tarih|Müzikal|Western|Gizem|Biyografi|Spor|Gençlik|Çocuk|Politik".split("|"))


def _genres(raw: Optional[str]) -> list[str]:
    try:
        val = json.loads(raw or "[]")
        return [g for g in val if g] if isinstance(val, list) else []
    except (TypeError, ValueError):
        return []


def _string_list(raw: Optional[str]) -> list[str]:
    try:
        value = json.loads(raw or "[]")
        return [str(item) for item in value if item] if isinstance(value, list) else []
    except (TypeError, ValueError):
        return []


def _col(row, name):
    try:
        return row[name]
    except (IndexError, KeyError):
        return None


def _real_art(url):
    """A site's "no picture" file is not artwork (``normalize.is_placeholder_image``): rows written before that rule read as empty."""
    return None if is_placeholder_image(url) else url


def _to_item(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "type": row["type"] or "movie",
        "title": row["title"],
        "original_title": row["original_title"],
        "year": row["year"],
        "overview": row["overview"] or "",
        "genres": _genres(row["genres"]),
        "rating": row["rating"],
        "badge": None,
        "popularity": row["popularity"] or 0,
        "added_at": row["added_at"] or 0,
        "tagline": None,
        "director": None,
        "cast": _string_list(row["cast"]),
        "country": row["country"],
        "followers": row["followers"],
        "seasons": [],
        "runtime": row["runtime"] or 0,
        # remote artwork (proxied by /img); absent -> Pillow placeholder
        # TMDB artwork wins when enrichment found it; the source image is the fallback.
        "poster_url": _col(row, "tmdb_poster_url") or _real_art(row["poster_url"]),
        "backdrop_url": _col(row, "tmdb_backdrop_url") or _real_art(row["backdrop_url"]),
    }


class LibrarySource(SourceAdapter):
    name = "library"

    # -- home-screen collections (real sinemalar list pages) -------------
    def _collections(self) -> list[dict[str, Any]]:
        """Collection metadata (id/title/role/genre) from every site's yaml."""
        from ..scraper import config as scfg

        out: list[dict[str, Any]] = []
        for sid in scfg.list_sites():
            try:
                out.extend(scfg.load_site(sid).collections)
            except Exception:  # pragma: no cover - defensive
                continue
        return out

    def _list_ids(self, list_id: str) -> list[str]:
        rows = db.query(
            "SELECT l.canonical_id AS cid FROM library_lists l "
            "JOIN library_items i ON i.id = l.canonical_id "
            "WHERE l.list_id = ? ORDER BY l.position ASC, l.canonical_id ASC",
            (list_id,),
        )
        return [r["cid"] for r in rows]

    def _list_counts(self) -> dict[str, int]:
        rows = db.query(
            "SELECT list_id, COUNT(*) AS n FROM library_lists GROUP BY list_id"
        )
        return {r["list_id"]: r["n"] for r in rows}

    @property
    def genres(self) -> list[dict[str, str]]:
        from ..cache import fold
        names = {g for row in db.query("SELECT genres FROM library_items") for g in _genres(row["genres"])}
        return [{"name": "Filmler", "slug": "movies"}, {"name": "Diziler", "slug": "series"}] + [
            {"name": n, "slug": "tag_" + fold(n).replace(" ", "_")} for n in sorted(names & DISPLAY_GENRES)]

    def genre_members(self) -> dict[str, list[str]]:
        from ..cache import fold
        out = {"movies": [], "series": []}
        for row in db.query("SELECT id,type,genres FROM library_items ORDER BY rating DESC,id"):
            out["series" if row["type"] == "series" else "movies"].append(row["id"])
            for name in _genres(row["genres"]):
                out.setdefault("tag_" + fold(name).replace(" ", "_"), []).append(row["id"])
        return out

    def newest_ids(self) -> list[str]:
        return [r["id"] for r in db.query("SELECT id FROM library_items ORDER BY added_at DESC,id")]

    def upcoming(self) -> Optional[dict[str, Any]]:
        return None

    def catalog(self) -> list[dict[str, Any]]:
        rows = db.query(
            "SELECT * FROM library_items ORDER BY added_at ASC, id ASC"
        )
        links = {}
        for link in db.query("SELECT DISTINCT canonical_id, source FROM source_items ORDER BY source"):
            links.setdefault(link["canonical_id"], []).append(link["source"])
        providers = {}
        for provider in db.query("SELECT * FROM video_sources ORDER BY season,episode"):
            providers.setdefault(provider["canonical_id"], []).append(provider)
        tm_seasons, tm_episodes = tmdb_seasons.load_tmdb()  # TMDB season posters / episode metadata (may be empty)
        out = []
        for row in rows:
            from ..catalog import availability, trailer_dead
            item = _to_item(row)
            video = providers.get(row["id"], [])
            # a trailer whose YouTube video was verified dead (trailer_check) does not make the title "trailer only"
            usable = [v for v in video if not trailer_dead(v)]
            item.update({"sources": links.get(row["id"], []), "tmdb_id": row["tmdb_id"], "imdb_id": row["imdb_id"],
                         "playback": "video" if any(v["kind"] != "trailer" for v in video) else "trailer" if usable else "unavailable"})
            item["availability"] = availability(video)
            seasons = {}
            for v in video:
                if v["kind"] != "episode": continue
                eps = seasons.setdefault(v["season"], {})
                eps[v["episode"]] = {"id": v["episode_id"], "season": v["season"], "episode": v["episode"],
                    "title": v["episode_title"] or str(v["episode"]) + ". Bölüm",
                    "overview": v["episode_overview"] or "", "runtime": v["episode_runtime"] or 0,
                    "still": v["episode_still_url"], "air_date": v["episode_air_date"],
                    "availability": availability([p for p in video if p["episode_id"] == v["episode_id"]])}
            # source values win when non-empty; TMDB fills the blanks, artwork: TMDB first, source fallback
            for n, eps in seasons.items():
                for number, ep in eps.items():
                    tmdb_seasons.merge_episode(ep, tm_episodes.get((row["id"], n, number)))
            item["seasons"] = [tmdb_seasons.merge_season({"season": n, "title": str(n) + ". Sezon", "episodes": list(eps.values())},
                                                         tm_seasons.get((row["id"], n))) for n,eps in seasons.items()]
            out.append(item)
        return out

    def detail(self, item_id: str) -> Optional[dict[str, Any]]:
        row = db.query_one("SELECT * FROM library_items WHERE id = ?", (item_id,))
        return _to_item(row) if row else None

    def streams(self, item_id: str, episode_id: Optional[str] = None) -> dict[str, Any]:
        from ..library import videos
        return videos.streams(item_id, episode_id)
