"""Serialisation to the public API schema plus the home-screen row layout."""
from __future__ import annotations

import datetime
import re
import zlib
from typing import Any, Optional

from . import config, db, catalog_view, images
from .cache import Snapshot, fold, get as get_cache
from .scraper.collections import HOME_ROLES

ROW_TITLES = {
    "continue": "İzlemeye Devam Et",
    "new": "En Yeniler",
    "top10": "Bugünün En Çok İzlenen 10 Yapımı",
    "mylist": "Listem",
    "trending": "Trendler",
    "latest_episodes": "Yeni Eklenen Bölümler",
    "new_episodes": "Yeni Eklenen Bölümler",
    "new_series": "Yeni Eklenen Diziler",
    "latest_series": "Yeni Eklenen Diziler",
    "noteworthy_movies": "Dikkate Değer Filmler",
}
# Titles of the rows of the tv-v1 home layout (`series`, `movies`, `trending_*`, `continue`, `mylist`) are owned by
# `homelayout.TITLES` (row_title consults it first); the entries above serve the legacy boot and the old row ids.

# Title of the eager "new" row under the real (library) home layout.
NEW_TITLE_REAL = "Yeni Eklenenler"

MAX_NEW = 20
CONTINUE_LIMIT = 20


# --------------------------------------------------------------------------
# progress helpers
# --------------------------------------------------------------------------
def pct(position: int, duration: int) -> int:
    if not duration:
        return 0
    return max(0, min(100, int(round(position * 100.0 / duration))))


def progress_map(profile_id: str) -> dict[str, dict[str, Any]]:
    """item_id -> most recent unwatched progress entry for that item."""
    if not profile_id:
        return {}
    rows = db.query(
        "SELECT item_id, episode_id, position, duration, updated_at "
        "FROM progress WHERE profile_id = ? AND watched = 0 "
        "ORDER BY updated_at ASC, rowid ASC",
        (profile_id,),
    )
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        # last write wins per item, so a finished episode that queued the next
        # one leaves the follow-up as the item's continue entry
        out[r["item_id"]] = {
            "episode_id": r["episode_id"],
            "position": r["position"],
            "duration": r["duration"],
            "pct": pct(r["position"], r["duration"]),
            "updated_at": r["updated_at"],
        }
    return out


def episode_progress(profile_id: str, item_id: str) -> dict[str, dict[str, Any]]:
    if not profile_id:
        return {}
    rows = db.query(
        "SELECT episode_id, position, duration FROM progress "
        "WHERE profile_id = ? AND item_id = ?",
        (profile_id, item_id),
    )
    return {
        r["episode_id"]: {
            "position": r["position"],
            "duration": r["duration"],
            "pct": pct(r["position"], r["duration"]),
        }
        for r in rows
    }


def unwatched_episode_progress(profile_id: str) -> dict[str, dict[str, Any]]:
    """episode_id -> unfinished progress of the profile (``{episode_id, position, duration, pct}``); one query."""
    if not profile_id:
        return {}
    return {
        r["episode_id"]: {
            "episode_id": r["episode_id"],
            "position": r["position"],
            "duration": r["duration"],
            "pct": pct(r["position"], r["duration"]),
        }
        for r in db.query(
            "SELECT episode_id, position, duration FROM progress WHERE profile_id = ? AND watched = 0",
            (profile_id,),
        )
    }


def watched_items(profile_id: str) -> set[str]:
    if not profile_id:
        return set()
    return {
        r["item_id"]
        for r in db.query(
            "SELECT DISTINCT item_id FROM progress WHERE profile_id = ?", (profile_id,)
        )
    }


def hidden_map(profile_id: str) -> dict[str, int]:
    """item_id -> ``hidden_at`` of the entries the profile removed from "Devam Et" (one query)."""
    if not profile_id:
        return {}
    return {r["item_id"]: r["hidden_at"]
            for r in db.query("SELECT item_id, hidden_at FROM continue_hidden WHERE profile_id = ?", (profile_id,))}


def is_hidden(prog: Optional[dict], hidden_at: Optional[int]) -> bool:
    """An unfinished item is hidden from "Devam Et" while its removal is not older than its latest progress; a
    later progress write (``updated_at > hidden_at``) brings it back."""
    return bool(prog) and hidden_at is not None and hidden_at >= prog["updated_at"]


def mylist_ids(profile_id: str) -> list[str]:
    if not profile_id:
        return []
    return [
        r["item_id"]
        for r in db.query(
            "SELECT item_id FROM mylist WHERE profile_id = ? ORDER BY added_at DESC",
            (profile_id,),
        )
    ]


# --------------------------------------------------------------------------
# serialisation
# --------------------------------------------------------------------------
WATCHED_PCT = 92   # a progress at/over this percentage counts as watched by the clients (server flag: > 0.92)


def episode_label(season: int, episode: int, title: Optional[str]) -> str:
    """"S04 B10 · Başlık" (season 0 = specials: "Özel B02 · Başlık"); a generic "10. Bölüm" title is not repeated."""
    head = f"S{season:02d} B{episode:02d}" if season else f"Özel B{episode:02d}"
    name = (title or "").strip()
    if not name or re.fullmatch(r"\d+\.\s*Bölüm", name):
        return head
    return f"{head} · {name}"


def episode_entry(item: dict, season: int, ep: dict, progress: Optional[dict] = None) -> dict:
    """Request-local copy of ``item`` that ``item_json`` serialises as an EPISODE card (``card_kind=episode``).

    The shared catalogue dict is never touched; ``progress`` is the profile's unfinished progress of THIS episode."""
    return {**item, "_card": {"season": season, "episode": ep, "progress": progress}}


def _episode_card_fields(out: dict, card: dict) -> None:
    """Episode-card additions (DATA-CONTRACT-V1 section 5): ``id`` stays the production id."""
    ep = card["episode"]
    eid = ep["id"]
    still = f"/img/{eid}/still?w=320&h=180"
    out.update({
        "card_kind": "episode",
        "card_key": f"episode:{eid}",
        "episode_id": eid,
        "episode_label": episode_label(card["season"], ep["episode"], ep.get("title")),
        "season": card["season"],
        "episode": ep["episode"],
        "still_url": still,
        "has_still": bool(ep.get("still_remote") and images.remote_host_allowed(ep["still_remote"])),
    })
    # the state of the TARGET episode; the trailer belongs to the production
    av = ep.get("availability") or {}
    out["availability"] = {"state": av.get("state", "ready"), "reason": av.get("reason"),
                           "has_trailer": bool(out["availability"].get("has_trailer"))}
    prog = out["progress"]
    resumable = bool(prog and prog["position"] > 0 and prog["pct"] < WATCHED_PCT)
    action = {"kind": "resume_episode" if resumable else "play_episode", "item_id": out["id"], "episode_id": eid}
    if resumable:
        action["position"] = prog["position"]
    out["primary_action"] = action


def item_json(item: dict[str, Any], progress: Optional[dict] = None) -> dict[str, Any]:
    iid = item["id"]
    card = item.get("_card")   # set by episode_entry(): this entry is an episode card, its progress is the episode's
    if card is not None:
        progress = card["progress"]
    prog = None
    if progress:
        prog = {
            "episode_id": progress["episode_id"],
            "position": int(progress["position"]),
            "duration": int(progress["duration"]),
            "pct": progress.get("pct", pct(progress["position"], progress["duration"])),
        }
    out = {
        "id": iid,
        "card_kind": "title",
        "card_key": f"title:{iid}",
        "availability": item.get("availability", {"state":"ready","reason":None,"has_trailer":False}),
        "type": item["type"],
        "title": item["title"],
        "year": item["year"],
        "card": f"/img/{iid}/card?w=342&h=192",
        "portrait": f"/img/{iid}/portrait?w=300&h=450",
        "backdrop": f"/img/{iid}/backdrop?w=1280&h=720",
        "has_backdrop": bool(item.get("backdrop_url")),
        "overview": item["overview"],
        "genres": list(item.get("genres", [])),
        "rating": item.get("rating"),
        "country": item.get("country"),
        "followers": item.get("followers"),
        "badge": item.get("badge"),
        "sources": item.get("sources", []),
        "tmdb_id": item.get("tmdb_id"),
        "imdb_id": item.get("imdb_id"),
        "playback": item.get("playback", "video"),
        "progress": prog,
    }
    if card is not None:
        _episode_card_fields(out, card)
    return out


def items_json(items, pmap: dict, limit: Optional[int] = None) -> list[dict]:
    if limit is not None:
        items = items[:limit]
    return [item_json(i, pmap.get(i["id"])) for i in items]


def episode_json(ep: dict, item_id: str, eprog: dict) -> dict[str, Any]:
    still = f"/img/{ep['id']}/still?w=320&h=180"
    return {
        "id": ep["id"],
        "season": ep["season"],
        "availability": ep.get("availability", {"state":"ready","reason":None,"has_trailer":False}),
        "episode": ep["episode"],
        "title": ep["title"],
        "overview": ep["overview"],
        "runtime": ep["runtime"],
        "still": still,
        # additive (TMDB-merged): same image as `still`; `has_still` = a real TMDB/source still exists
        # (else `/img` serves the generated placeholder)
        "still_url": still,
        "has_still": bool(ep.get("still_remote") and images.remote_host_allowed(ep["still_remote"])),
        "air_date": ep.get("air_date"),
        "progress": eprog.get(ep["id"]),
    }


def season_json(item_id: str, season: dict, eprog: dict) -> dict[str, Any]:
    """Season object: legacy ``season``/``title``/``episodes`` + additive ``name``, ``overview``, ``air_date``,
    ``poster_url`` (season-specific TMDB poster; else the series portrait), ``has_poster``, ``episode_count``."""
    sid = f"{item_id}:s{season['season']}"
    has_poster = bool(season.get("poster_url"))
    episodes = [episode_json(e, item_id, eprog) for e in season["episodes"]]
    return {
        "season": season["season"],
        "title": season["title"],
        "name": season.get("name") or season["title"],
        "overview": season.get("overview") or "",
        "air_date": season.get("air_date"),
        "poster_url": f"/img/{sid}/portrait?w=300&h=450" if has_poster else f"/img/{item_id}/portrait?w=300&h=450",
        "has_poster": has_poster,
        "episode_count": len(episodes),
        "episodes": episodes,
    }


# --------------------------------------------------------------------------
# row construction
# --------------------------------------------------------------------------
def row_title(snap: Snapshot, row_id: str) -> str:
    from . import homelayout
    row_id = "movies" if row_id == "new_movies" else row_id   # DATA-CONTRACT-V1 name of the "movies" row
    if row_id in homelayout.TITLES:
        return homelayout.TITLES[row_id]
    if row_id.startswith("cat_"):   # admin category row: its (editable) title
        from .library import categories
        slug = categories.slug_of_row(row_id)
        return (slug and categories.title_of(slug)) or row_id
    if row_id == "new" and getattr(snap, "real_home", False):
        return NEW_TITLE_REAL
    if row_id == "yakinda":
        up = getattr(snap, "upcoming", None)
        return up["title"] if up else "Yakında"
    if row_id in ROW_TITLES:
        return ROW_TITLES[row_id]
    if row_id.startswith("genre_"):
        return snap.slug_genre.get(row_id[len("genre_") :], row_id)
    return row_id


def continue_items(snap: Snapshot, pmap: dict, profile_id: str = "") -> list[dict]:
    """"Devam Et" entries, most recently watched first, minus the ones the profile removed (``continue_hidden``,
    see :func:`is_hidden`; one query). A series whose progress points at a known episode is an
    EPISODE card (``card_kind=episode``, ``id`` = production id + ``episode_id``); movies (and a series whose
    progress episode is unknown) stay title cards. ``progress`` is unchanged for old clients."""
    hidden = hidden_map(profile_id) if pmap else {}   # nothing in progress = nothing to hide (no query)
    entries = sorted(((k, v) for k, v in pmap.items() if not is_hidden(v, hidden.get(k))),
                     key=lambda kv: -kv[1]["updated_at"])
    episodes = getattr(snap, "episodes", None) or {}
    out = []
    for item_id, prog in entries[:CONTINUE_LIMIT]:
        item = snap.by_id.get(item_id)
        if not item:
            continue
        hit = episodes.get(prog["episode_id"]) if item["type"] == "series" else None
        if hit and hit[0]["id"] == item["id"]:
            out.append(episode_entry(item, hit[1], hit[2], prog))
        else:
            out.append(item)
    return out


def _role_by_list(snap: Snapshot, role: str) -> dict[str, list[dict]]:
    """``{list_id: [items in position order]}`` of the ``<role>_<site_id>`` lists of EVERY site (``library_lists``; ids
    follow ``scraper.collections.list_id``; only ``HOME_ROLES``, so ``genre_*``/``source_*`` lists never mix in).
    Ids missing from the snapshot (or its ``source`` view) are dropped; non-library snapshots have no lists."""
    if getattr(snap, "source", "") != "library" or role not in HOME_ROLES:
        return {}
    by_list: dict[str, list[dict]] = {}
    for r in db.query(
        "SELECT list_id, canonical_id FROM library_lists WHERE list_id GLOB ? ORDER BY list_id, position, canonical_id",
        (role + "_*",),
    ):
        item = snap.by_id.get(r["canonical_id"])
        if item:
            by_list.setdefault(r["list_id"], []).append(item)
    return by_list


def _round_robin(lists: list[list[dict]]) -> list[dict]:
    """Merge lists round-robin (first of every list, then the second of every list, ...), one card per canonical id
    (first occurrence wins). One list is returned as it is (minus repeats)."""
    out, seen = [], set()
    for depth in range(max(map(len, lists), default=0)):
        for items in lists:
            if depth < len(items) and items[depth]["id"] not in seen:
                seen.add(items[depth]["id"])
                out.append(items[depth])
    return out


def _role_lists(snap: Snapshot, role: str, kind: Optional[str] = None) -> list[dict]:
    """Items of the ``<role>_<site_id>`` lists of EVERY site (see :func:`_role_by_list`).

    Each site's list keeps its own order; the lists are merged round-robin (:func:`_round_robin`) so no single site
    dominates the row, one card per canonical id. With one site this is exactly that site's list in ``position``
    order. ``kind`` (``series`` | ``movie``) keeps only titles of that type (applied per list, before the merge)."""
    lists = list(_role_by_list(snap, role).values())
    if kind:
        lists = [[i for i in items if i["type"] == kind] for items in lists]
    return _round_robin(lists)


def _newest_episode(item: dict, today: str) -> Optional[tuple[int, dict]]:
    """(season, episode) of the newest PUBLISHED episode of a series: highest regular season/episode number
    (specials, season 0, only when there is nothing else); an announced one (future ``air_date``, no ready
    source) is not out yet."""
    best = None
    for season in item.get("seasons") or []:
        n = season["season"]
        for ep in season.get("episodes") or []:
            if (ep.get("air_date") or "")[:10] > today and (ep.get("availability") or {}).get("state") != "ready":
                continue
            key = (n != 0, n, ep["episode"])
            if best is None or key > best[0]:
                best = (key, n, ep)
    return (best[1], best[2]) if best else None


def new_episode_entries(snap: Snapshot, profile_id: str) -> list[dict]:
    """``new_episodes`` row: the newest READY episode of every series on the sites' "latest episodes" lists
    (``latest_episodes_<site>``, merged by ``_role_lists``; each site's order = newest first); one card per
    production (an episode has one canonical id whatever site it came from). Series without a ready newest
    episode are left out (new-content rows only show ``ready``)."""
    listed = _role_lists(snap, "latest_episodes")
    if not listed:
        return []
    today = datetime.date.today().isoformat()
    eprog = unwatched_episode_progress(profile_id)
    out, seen = [], set()
    for item in listed:
        if item["type"] != "series" or item["id"] in seen:
            continue
        seen.add(item["id"])
        found = _newest_episode(item, today)
        if found and (found[1].get("availability") or {}).get("state", "ready") == "ready":
            out.append(episode_entry(item, found[0], found[1], eprog.get(found[1]["id"])))
    return out


def new_series_entries(snap: Snapshot) -> list[dict]:
    """``new_series`` row: the series cards of the sites' "newly added series" lists (``latest_series_<site>``, merged
    by ``_role_lists``, newest first); series only (a title of another type on such a list is ignored), one card per
    canonical id."""
    return [i for i in _role_lists(snap, "latest_series") if i["type"] == "series"]


def row_pool(snap: Snapshot, row_id: str, profile_id: str, pmap: dict) -> list[dict]:
    """The full (unpaged) item list backing a row id."""
    from . import homelayout
    if row_id == "new_movies":  # DATA-CONTRACT-V1 name of the "movies" row
        row_id = "movies"
    if row_id in homelayout.POOL_IDS or row_id.startswith("cat_"):   # tv-v1 home rows (+ the admin's category rows)
        return homelayout.pool(snap, row_id, profile_id, pmap)
    if row_id in ("new_episodes", "latest_episodes"):  # latest_episodes: the previous id, kept as an alias
        return new_episode_entries(snap, profile_id)
    if row_id == "continue":
        return continue_items(snap, pmap, profile_id)
    if row_id == "new":
        return snap.newest[:MAX_NEW]
    if row_id == "yakinda":
        up = getattr(snap, "upcoming", None)
        return up["items"] if up else []
    if row_id == "top10":
        return snap.top10
    if row_id == "mylist":
        return [snap.by_id[i] for i in mylist_ids(profile_id) if i in snap.by_id]
    if row_id in ("new_series", "latest_series"):  # latest_series: the role name, kept as an alias
        return new_series_entries(snap)
    if row_id in ("trending", "noteworthy_movies"):
        return _role_lists(snap, row_id)
    if row_id.startswith("genre_"):
        return snap.by_genre.get(row_id[len("genre_") :], [])
    return []


def hero(snap: Snapshot, profile_id: str, pmap: dict) -> Optional[dict]:
    seen = watched_items(profile_id)
    pool = [
        i
        for i in snap.items
        if i["id"] not in seen and (i.get("rating") or 0) >= 7.8
    ]
    if not pool:
        pool = [i for i in snap.items if (i.get("rating") or 0) >= 7.8] or snap.items
    pool = sorted(pool, key=lambda i: (-(i.get("rating") or 0), i["id"]))[:25]
    if not pool:
        return None
    idx = (zlib.crc32((profile_id or "anon").encode("utf-8")) & 0xFFFFFFFF) % len(pool)
    item = pool[idx]
    out = item_json(item, pmap.get(item["id"]))
    out["logo_text"] = item["title"]
    out["tagline"] = item.get("tagline") or item["overview"].split(".")[0] + "."
    return out


def tv_boot(snap: Snapshot, profile_id: str, pmap: dict) -> dict:
    """The tv-v1 home: hero carousel + rows, all decided by :mod:`homelayout` (rows with an empty pool are left out)."""
    from . import homelayout
    home = homelayout.compose(snap, profile_id, pmap)
    return {"hero": home["heroes"][0] if home["heroes"] else None, "heroes": home["heroes"], "rows": home["rows"],
            "layout": "tv-v1", "catalog_total": len(snap.items), "source": ""}


def boot(profile_id: str, source: str = "", layout: str = "") -> dict[str, Any]:
    snap = catalog_view.select(get_cache(), source)
    pmap = progress_map(profile_id)
    if layout == "tv-v1":
        return tv_boot(snap, profile_id, pmap)
    rows: list[dict[str, Any]] = []

    cont = continue_items(snap, pmap, profile_id)
    if cont:
        rows.append(
            {
                "id": "continue",
                "title": ROW_TITLES["continue"],
                "loaded": True,
                "items": items_json(cont, pmap, config.ROW_LIMIT),
            }
        )

    real = getattr(snap, "real_home", False)

    # "Yakında Vizyonda" (real sinemalar upcoming list) — real layout only, lazy.
    up = getattr(snap, "upcoming", None) if real else None
    if up and up.get("items"):
        rows.append(
            {"id": up["id"], "title": up["title"], "loaded": False, "count": len(up["items"])}
        )

    new_items = snap.newest[:MAX_NEW]
    rows.append(
        {
            "id": "new",
            "title": NEW_TITLE_REAL if real else ROW_TITLES["new"],
            "loaded": True,
            "items": items_json(new_items, pmap, config.ROW_LIMIT),
        }
    )

    # Legacy (mock): keep the top10 row. Real layout drops the fabricated
    # "most watched" row in favour of real genre pages.
    if not real:
        rows.append(
            {"id": "top10", "title": ROW_TITLES["top10"], "loaded": False, "count": len(snap.top10)}
        )

    mine = [iid for iid in mylist_ids(profile_id) if iid in snap.by_id]
    if mine:
        rows.append(
            {"id": "mylist", "title": ROW_TITLES["mylist"], "loaded": False, "count": len(mine)}
        )

    # Real layout preserves the yaml collection order; legacy folds alphabetically.
    genre_items = (
        list(snap.genre_slug.items()) if real
        else sorted(snap.genre_slug.items(), key=lambda kv: fold(kv[0]))
    )
    for name, slug in genre_items:
        bucket = snap.by_genre.get(slug, [])
        if bucket:
            rows.append(
                {"id": f"genre_{slug}", "title": name, "loaded": False, "count": len(bucket)}
            )

    return {"hero": hero(snap, profile_id, pmap), "rows": rows, "source": source, "sources": catalog_view.sources(get_cache())}


def row(row_id: str, profile_id: str, offset: int, limit: int, source: str = "") -> dict[str, Any]:
    snap = catalog_view.select(get_cache(), source)
    pmap = progress_map(profile_id)
    pool = row_pool(snap, row_id, profile_id, pmap)
    window = pool[offset : offset + limit]
    return {
        "id": row_id,
        "title": row_title(snap, row_id),
        "items": items_json(window, pmap),
        "offset": offset,
        "limit": limit,
        "total": len(pool),
    }


PLAYABLE = ("ready", "check_required")


def detail_actions(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Targeted actions of a detail response (DATA-CONTRACT-V1 section 5), most important first:
    ``play_movie`` / ``resume_movie`` (film with a playable full source), ``play_episode`` / ``resume_episode``
    (series: the profile's unfinished episode, else the first playable one; only when that episode has a source),
    ``play_trailer`` (a live trailer exists). Nothing is invented for a title without a source. Call it again
    after ``trailer_check.apply_detail`` (it changes ``availability.has_trailer``)."""
    actions: list[dict[str, Any]] = []
    iid = data["id"]
    prog = data.get("progress")
    resumable = bool(prog and prog["position"] > 0 and prog["pct"] < WATCHED_PCT)
    if data["type"] == "movie":
        if (data.get("availability") or {}).get("state") in PLAYABLE:
            if resumable:
                actions.append({"kind": "resume_movie", "item_id": iid, "position": prog["position"]})
            else:
                actions.append({"kind": "play_movie", "item_id": iid})
    else:
        episodes = [e for s in sorted(data.get("seasons") or [], key=lambda s: (s["season"] == 0, s["season"]))
                    for e in s["episodes"]]
        playable = [e for e in episodes if (e.get("availability") or {}).get("state") in PLAYABLE]
        target = next((e for e in playable if e["id"] == (data.get("resume") or {}).get("episode_id")), None)
        if target is None and playable:
            target = playable[0]
        if target is not None:
            if resumable and prog["episode_id"] == target["id"]:
                actions.append({"kind": "resume_episode", "item_id": iid, "episode_id": target["id"],
                                "position": prog["position"]})
            else:
                actions.append({"kind": "play_episode", "item_id": iid, "episode_id": target["id"]})
    if (data.get("availability") or {}).get("has_trailer"):
        actions.append({"kind": "play_trailer", "item_id": iid})
    return actions


def source_names(site_ids) -> list[dict[str, str]]:
    """``[{"id", "name"}]`` for the sites a title comes from (``name`` = the site yaml's ``display_name``; the id itself
    for a site that is unknown/removed or whose config cannot be read). Never raises."""
    out = []
    for sid in site_ids or []:
        if not isinstance(sid, str) or not sid:
            continue
        name = sid
        try:
            from .scraper import config as scfg
            name = str(scfg.load_site(sid).data.get("display_name") or sid)
        except Exception:
            pass
        out.append({"id": sid, "name": name})
    return out


def detail(item_id: str, profile_id: str) -> Optional[dict[str, Any]]:
    snap = get_cache()
    item = snap.by_id.get(item_id)
    if not item:
        return None
    item_id = item["id"]
    pmap = progress_map(profile_id)
    eprog = episode_progress(profile_id, item_id)
    out = item_json(item, pmap.get(item_id))

    out["cast"] = list(item.get("cast", []))
    out["director"] = item.get("director")
    out["runtime"] = item.get("runtime")
    out["in_mylist"] = bool(
        profile_id
        and db.query_one(
            "SELECT 1 FROM mylist WHERE profile_id = ? AND item_id = ?", (profile_id, item_id)
        )
    )
    out["seasons"] = [season_json(item_id, s, eprog) for s in item.get("seasons", [])]
    out["source_names"] = source_names(out.get("sources"))

    prog = pmap.get(item_id)
    if item["type"] == "movie":
        out["resume"] = {
            "episode_id": item_id,
            "position": int(prog["position"]) if prog else 0,
        }
    elif prog:
        out["resume"] = {"episode_id": prog["episode_id"], "position": int(prog["position"])}
    else:
        first = snap.first_episode(item_id)
        out["resume"] = {"episode_id": first["id"] if first else item_id, "position": 0}
    out["actions"] = detail_actions(out)

    similar = [
        i
        for i in snap.items
        if i["id"] != item_id and set(i.get("genres", [])) & set(item.get("genres", []))
    ]
    similar.sort(key=lambda i: (-(i.get("rating") or 0), i["id"]))
    out["similar"] = items_json(similar, pmap, 12)
    return out


def search(q: str, profile_id: str, limit: int, source: str = "") -> dict[str, Any]:
    snap = catalog_view.select(get_cache(), source)
    needle = fold(q or "").strip()
    if not needle:
        return {"items": []}
    pmap = progress_map(profile_id)
    hits = []
    for haystack, item in snap.search_index:
        pos = haystack.find(needle)
        if pos >= 0:
            hits.append((pos, -(item.get("rating") or 0), item["id"], item))
    hits.sort(key=lambda t: (t[0], t[1], t[2]))
    return {"items": items_json([h[3] for h in hits], pmap, limit)}
