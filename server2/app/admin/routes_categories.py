"""Admin "Kategoriler" (T24): the home screen's fixed rows + the admin's dynamic categories as one ordered list
(``core.catalog.categories``; the client's boot / ``/api/row/<id>`` read the table on every request).

    GET    /api/control/categories                    {categories:[...] (home order), options:{genres, sites, lists, types}}
    POST   /api/control/categories {title, slug?, source?, max_items?, min_items?}   new dynamic category (201)
    PUT    /api/control/categories/{slug} {title?, enabled?, max_items?, min_items?, source?}
                                                      fixed rows: title / enabled / max_items only (409 ``locked``)
    PUT    /api/control/categories-order {order:[slug,...]}
    DELETE /api/control/categories/{slug}             dynamic only (fixed: 409 ``locked``, hide it instead)
    POST   /api/control/categories/preview {source, slug?}   -> {count, playable, items: first 10 {id,title,type,poster_url}}
    GET    /api/control/categories/{slug}/preview     the stored category's row as the client would get it (limit applied)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body

from .. import db
from ..core.catalog import cache, categories
from ..core.catalog.textnorm import fold
from ..errors import ApiError

router = APIRouter()
P = "/api/control/categories"
PREVIEW_N = 10


def _guard(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except categories.CategoryError as exc:
        raise ApiError(exc.status, exc.code, str(exc)) from None


def _body(data: Any) -> dict:
    if not isinstance(data, dict):
        raise ApiError(400, "bad_request", "JSON nesnesi bekleniyor")
    return data


def _seed() -> None:
    try:
        categories.ensure_system(categories.SYSTEM_TITLES)
    except Exception:   # noqa: BLE001 - a broken table must not hide the list
        pass


def _round_robin(lists: list[list[dict]]) -> list[dict]:
    out, seen = [], set()
    for depth in range(max(map(len, lists), default=0)):
        for items in lists:
            if depth < len(items) and items[depth]["id"] not in seen:
                seen.add(items[depth]["id"])
                out.append(items[depth])
    return out


class _Visible:
    """What the client sees (K5): the snapshot's visible titles."""

    def __init__(self, snap) -> None:
        self.items = list(snap.visible)
        self.listed = {i["id"]: i for i in self.items}
        self.by_id = self.listed


def _card(item: dict) -> dict:
    return {"id": item["id"], "title": item.get("title") or "", "type": item.get("type") or "",
            "poster_url": f"/img/{item['id']}/portrait?w=300&h=450"}


def _preview(pool: list[dict], limit: int = 0) -> dict:
    total = len(pool)
    if limit > 0:
        pool = pool[:limit]
    return {"count": total, "shown": len(pool), "items": [_card(i) for i in pool[:PREVIEW_N]]}


def options() -> dict[str, Any]:
    """What the "yeni dinamik kategori" form offers: genres of the catalogue, each site's sections / listings / genre
    origins (with title counts), the ``library_lists`` ids, the types."""
    snap = cache.get()
    genres: dict[str, list] = {}
    for item in snap.visible:
        for g in item.get("genres") or []:
            genres.setdefault(fold(g), [g, 0])[1] += 1
    sites = []
    for s in db.query("SELECT site_id, display_name FROM sites ORDER BY site_id"):
        groups: dict[str, dict[str, list]] = {k: {} for k in categories.ORIGIN_KINDS}
        for r in db.query("SELECT kind, value, COUNT(DISTINCT source_key) AS n FROM source_origins WHERE site_id = ? "
                          "GROUP BY kind, value ORDER BY kind, n DESC", (s["site_id"],)):
            if r["kind"] in groups:
                groups[r["kind"]].setdefault(fold(r["value"]), [r["value"], 0])[1] += r["n"]
        sites.append({"site_id": s["site_id"], "name": s["display_name"] or s["site_id"],
                      **{k: [{"value": v, "count": n} for v, n in g.values()] for k, g in groups.items()}})
    lists = [{"list_id": r["list_id"], "count": r["n"]} for r in db.query(
        "SELECT list_id, COUNT(*) AS n FROM library_lists GROUP BY list_id ORDER BY list_id")]
    return {"genres": sorted(({"value": v, "count": n} for v, n in genres.values()), key=lambda g: fold(g["value"])),
            "sites": sites, "lists": lists, "types": list(categories.TYPES), "source_kinds": list(categories.SOURCE_KINDS)}


@router.get(P)
def list_categories() -> dict[str, Any]:
    _seed()
    return {"categories": categories.list_all(True, include_system=True), "options": options()}


@router.post(P, status_code=201)
def create_category(data: Any = Body(...)) -> dict[str, Any]:
    data = _body(data)
    _seed()
    return {"category": _guard(categories.create, data.get("title"), data.get("slug") or None, data.get("source"),
                               data.get("max_items"), data.get("min_items"))}


@router.put(P.replace("categories", "categories-order"))
def reorder_categories(data: Any = Body(...)) -> dict[str, Any]:
    order = _body(data).get("order")
    if not isinstance(order, list) or not all(isinstance(s, str) for s in order):
        raise ApiError(400, "bad_request", "order: kategori kısa adlarının listesi olmalı")
    _seed()
    return {"categories": _guard(categories.reorder, order)}


@router.post(P + "/preview")
def preview_source(data: Any = Body(...)) -> dict[str, Any]:
    data = _body(data)
    src = _guard(categories.clean_source, data.get("source"))
    slug = data.get("slug") if categories.valid_slug(str(data.get("slug") or "")) else ""
    view = _Visible(cache.get())
    pool = categories.category_items(view, {"slug": slug or "-", "source": src}, _round_robin) if slug else \
        categories.source_items(view, src)
    return _preview(pool)


@router.get(P + "/{slug}/preview")
def preview_category(slug: str) -> dict[str, Any]:
    cat = categories.get(slug)
    if cat is None:
        raise ApiError(404, "not_found", "Kategori bulunamadı")
    pool = categories.category_items(_Visible(cache.get()), cat, _round_robin)
    out = _preview(pool, cat["max_items"])
    out["hidden_reason"] = ("kapalı" if not cat["enabled"] else
                            f"en az {cat['min_items']} oynatılabilir başlık gerekir" if len(pool) < cat["min_items"] else "")
    return out


@router.put(P + "/{slug}")
def update_category(slug: str, data: Any = Body(...)) -> dict[str, Any]:
    data = _body(data)
    if "enabled" in data and not isinstance(data["enabled"], bool):
        raise ApiError(400, "bad_request", "enabled: true ya da false olmalı")
    return {"category": _guard(categories.update, slug, title=data.get("title"), enabled=data.get("enabled"),
                               min_items=data.get("min_items"), max_items=data.get("max_items"),
                               source=data.get("source"))}


@router.delete(P + "/{slug}")
def delete_category(slug: str) -> dict[str, Any]:
    _guard(categories.delete, slug)
    return {"deleted": slug}
