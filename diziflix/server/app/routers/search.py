from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from .. import cache, rows
from ..deps import optional_profile
from ..library import search_all
from ..scraper import site_search

router = APIRouter(tags=["search"])


def _interleave(lists: list[list[str]]) -> list[str]:
    """Round-robin over the sites' hits (best hit of every site first), duplicates (one title on two sites) once."""
    out: list[str] = []
    seen: set[str] = set()
    for rank in range(max((len(x) for x in lists), default=0)):
        for ids in lists:
            if rank < len(ids) and ids[rank] not in seen:
                seen.add(ids[rank])
                out.append(ids[rank])
    return out


def _remote_error(results: dict[str, dict]) -> str | None:
    """Short text of the sites that failed (one site: its bare message, as before the multi-site search)."""
    parts = []
    for site, r in results.items():
        if r["ok"]:
            continue
        msg = r["error"] or ("temporarily skipped" if r["skipped"] == "breaker" else "")
        if msg:
            parts.append(msg if len(results) == 1 else f"{site}: {msg}")
    return "; ".join(parts)[:300] or None


def _remote_sites(results: dict[str, dict]) -> dict[str, dict]:
    out = {}
    for site, r in results.items():
        info = {"ok": r["ok"], "count": r["count"], "ms": r["ms"]}
        if r["error"]:
            info["error"] = r["error"]
        if r["skipped"]:
            info["skipped"] = r["skipped"]
        out[site] = info
    return out


@router.get("/api/search")
def search(
    q: str = Query("", description="Search term"),
    profile: str = Depends(optional_profile),
    limit: int = Query(20, ge=1, le=100),
    source: str = "",
) -> dict:
    local = rows.search(q, profile, limit, source).get("items", [])
    remote_items: list[dict] = []
    results: dict[str, dict] = {}
    remote_error = None
    remote = False
    if len(" ".join(q.split())) >= 3:
        try:
            capable = list(site_search.search_sites())
            targets = ([source] if source in capable else []) if source else capable
            if targets:
                remote = True
                results = search_all.search_sites(q, sites=targets, limit=limit)
                remote_error = _remote_error(results)
                snap = cache.get()
                ids = _interleave([results[s]["ids"] for s in targets if s in results])
                remote_items = rows.items_json([snap.by_id[i] for i in ids if i in snap.by_id],
                                               rows.progress_map(profile), limit)
        except Exception as exc:
            remote = True
            remote_items = []
            remote_error = str(exc)

    seen = {item["id"] for item in remote_items}
    combined = (remote_items + [item for item in local if item["id"] not in seen])[:limit]
    # additive: where each hit can be watched (site, episode count, health) = the client's source choice
    options = search_all.source_options({item["id"]: item["type"] for item in combined})
    for item in combined:
        item["source_options"] = options.get(item["id"], [])
    return {
        "items": combined,
        "total": len(combined),
        "remote": remote,
        "remote_error": remote_error,
        "remote_sites": _remote_sites(results),
    }
