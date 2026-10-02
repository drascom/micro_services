"""Admin "Kategoriler" API: the home-screen categories as an editable, orderable list (``library/categories.py``).

``GET    /api/ops/categories``                 -> ``{categories:[{slug,title,position,enabled,min_items,lists,titles,playable}]}``
``POST   /api/ops/categories {title, slug?}``  -> the new category (409 ``slug_exists``)
``PUT    /api/ops/categories/{slug} {title?, enabled?, min_items?}``
``PUT    /api/ops/categories-order {order:[slug,...]}``
``DELETE /api/ops/categories/{slug}``          -> only the row; the titles' membership stays (same slug later = they return)

The home screen reads the table on every request (nothing is cached), so a change shows on the next boot.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body

from ..errors import ApiError
from ..library import categories

router = APIRouter()


def _fail(e: categories.CategoryError) -> ApiError:
    return ApiError(e.status, e.code, str(e))


def _body(data: Any) -> dict:
    if not isinstance(data, dict):
        raise ApiError(400, "bad_request", "JSON nesnesi bekleniyor")
    return data


@router.get("/api/ops/categories")
def list_categories() -> dict[str, Any]:
    return {"categories": categories.list_all(True)}


@router.post("/api/ops/categories")
def create_category(data: Any = Body(...)) -> dict[str, Any]:
    data = _body(data)
    try:
        return categories.create(data.get("title"), data.get("slug") or None)
    except categories.CategoryError as e:
        raise _fail(e) from None


@router.put("/api/ops/categories-order")
def reorder_categories(data: Any = Body(...)) -> dict[str, Any]:
    order = _body(data).get("order")
    if not isinstance(order, list) or not all(isinstance(s, str) for s in order):
        raise ApiError(400, "bad_request", "order: kategori kısa adlarının listesi olmalı")
    try:
        return {"categories": categories.reorder(order)}
    except categories.CategoryError as e:
        raise _fail(e) from None


@router.put("/api/ops/categories/{slug}")
def update_category(slug: str, data: Any = Body(...)) -> dict[str, Any]:
    data = _body(data)
    if "enabled" in data and not isinstance(data["enabled"], bool):
        raise ApiError(400, "bad_request", "enabled: true ya da false olmalı")
    try:
        return categories.update(slug, title=data.get("title"), enabled=data.get("enabled"),
                                 min_items=data.get("min_items"))
    except categories.CategoryError as e:
        raise _fail(e) from None


@router.delete("/api/ops/categories/{slug}")
def delete_category(slug: str) -> dict[str, Any]:
    try:
        categories.delete(slug)
    except categories.CategoryError as e:
        raise _fail(e) from None
    return {"deleted": slug}
