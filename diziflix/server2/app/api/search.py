"""``GET /api/search``: local catalogue search (Snapshot ``search_index``) + the live-search hook (``_search_live``, T09).
Copied from the old routers/search.py + the old ``source_options``."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from .. import db
from ..core.catalog import cache
from . import _search_live, rows, view
from .deps import dev_tools, optional_profile

router = APIRouter(tags=["search"], dependencies=[Depends(dev_tools)])
_STATUS_RANK = {"ok": 0, "unknown": 1, "broken": 2}


def source_options(types: dict[str, str]) -> dict[str, list[dict]]:
    """``{canonical_id: [{site, name, kind, episodes, status}]}`` (where each hit can be watched); best sources first."""
    ids = [i for i in types if i]
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    known: dict[str, dict[str, dict]] = {i: {} for i in ids}
    for row in db.query(f"SELECT DISTINCT canonical_id, site_id FROM source_items WHERE canonical_id IN ({marks})", ids):
        known[row["canonical_id"]].setdefault(row["site_id"], {"eps": 0, "n": 0, "broken": 0, "tried": 0})
    for row in db.query(
            "SELECT canonical_id, source, COUNT(*) AS n, COUNT(DISTINCT CASE WHEN episode_id != '' THEN episode_id END) AS eps,"
            " SUM(status = 'broken') AS broken, SUM(status != 'unknown') AS tried"
            f" FROM video_sources WHERE canonical_id IN ({marks}) AND kind != 'trailer' AND status NOT IN ('disabled', 'blocked')"
            " GROUP BY canonical_id, source", ids):
        known[row["canonical_id"]][row["source"]] = {"eps": row["eps"] or 0, "n": row["n"] or 0,
                                                     "broken": row["broken"] or 0, "tried": row["tried"] or 0}
    names = view.site_names(sorted({s for per in known.values() for s in per}))
    out: dict[str, list[dict]] = {}
    for cid, per in known.items():
        kind = "series" if types.get(cid) == "series" else "movie"
        entries = []
        for site, v in per.items():
            status = "unknown" if not v["n"] or not v["tried"] else "broken" if v["broken"] == v["n"] else "ok"
            episodes = v["eps"] if kind == "series" else (1 if v["n"] else 0)
            entries.append({"site": site, "name": names.get(site, site), "kind": kind, "episodes": episodes, "status": status})
        entries.sort(key=lambda e: (_STATUS_RANK[e["status"]], -e["episodes"], e["site"]))
        out[cid] = entries
    return out


@router.get("/api/search")
def search(q: str = Query("", description="Search term"), profile: str = Depends(optional_profile),
           limit: int = Query(20, ge=1, le=100), source: str = "") -> dict:
    local = rows.search(q, profile, limit, source).get("items", [])
    remote_items: list[dict] = []
    info = {"remote": False, "remote_error": None, "remote_sites": {}}
    if len(" ".join(q.split())) >= 3:
        ids, info = _search_live.search(q, source, limit)
        snap = view.get()
        live = [snap.listed[i] for i in ids if i in snap.listed]   # K5: a live hit without a playable source stays hidden
        remote_items = rows.items_json(live, rows.progress_map(profile), limit)
    seen = {item["id"] for item in remote_items}
    combined = (remote_items + [item for item in local if item["id"] not in seen])[:limit]
    options = source_options({item["id"]: item["type"] for item in combined})
    for item in combined:
        item["source_options"] = options.get(item["id"], [])
    return {"items": combined, "total": len(combined), "remote": bool(info.get("remote")),
            "remote_error": info.get("remote_error"), "remote_sites": info.get("remote_sites") or {}}
