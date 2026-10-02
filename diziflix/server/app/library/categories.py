"""Admin-managed home-screen categories ("Kategoriler"): a to-do-list-like set of rows the home shows in admin order.

Table ``home_categories`` (slug, title, position, enabled, min_items). Membership is NOT stored here: it is read from
``library_lists`` where a category's lists are ``category_<slug>_<site_id>`` (written by the scraper's ``category`` role).
Deleting a category only drops its row here; the membership lists stay, so adding the same slug again brings the
titles back. A title may be in many categories. The home row of a category is ``cat_<slug>`` (``homelayout``).

Slugs are ``[a-z0-9-]`` (no underscore: that is what separates the slug from the site id in a list id).
"""
from __future__ import annotations

import re
import time
import unicodedata
from contextlib import closing
from typing import Any, Optional

from .. import db

LIST_PREFIX = "category_"
ROW_PREFIX = "cat_"
MAX_SLUG = 40
MAX_TITLE = 60
DEFAULT_MIN_ITEMS = 6
MIN_ITEMS_RANGE = (1, 100)
_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_TR = str.maketrans({"ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g", "ı": "i", "İ": "i", "I": "i", "ö": "o", "Ö": "o",
                     "ş": "s", "Ş": "s", "ü": "u", "Ü": "u"})


class CategoryError(ValueError):
    """A rejected category operation: ``status`` is the HTTP status, ``code`` the error-envelope code."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code = status, code


def slugify(title: str) -> str:
    """``[a-z0-9-]`` slug of a title (Turkish letters folded, at most ``MAX_SLUG`` characters; may be empty)."""
    text = unicodedata.normalize("NFKD", str(title or "").translate(_TR).lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:MAX_SLUG].strip("-")


def valid_slug(slug: str) -> bool:
    return bool(slug) and len(slug) <= MAX_SLUG and bool(_SLUG_RE.match(slug))


def row_id(slug: str) -> str:
    return ROW_PREFIX + slug


def slug_of_row(rid: str) -> Optional[str]:
    """Slug of a ``cat_<slug>`` home row id (None for any other id)."""
    if isinstance(rid, str) and rid.startswith(ROW_PREFIX) and valid_slug(rid[len(ROW_PREFIX):]):
        return rid[len(ROW_PREFIX):]
    return None


def slug_of_list(list_id: str) -> Optional[str]:
    """Slug of a ``category_<slug>_<site_id>`` list id (None for any other list)."""
    if not isinstance(list_id, str) or not list_id.startswith(LIST_PREFIX):
        return None
    slug = list_id[len(LIST_PREFIX):].split("_", 1)[0]
    return slug if valid_slug(slug) and "_" in list_id[len(LIST_PREFIX):] else None


def _clean_title(title: Any) -> str:
    text = " ".join(str(title or "").split())
    if not text:
        raise CategoryError(400, "invalid_title", "Kategori adı boş olamaz")
    if len(text) > MAX_TITLE:
        raise CategoryError(400, "invalid_title", f"Kategori adı en fazla {MAX_TITLE} karakter olabilir")
    return text


def _clean_min_items(value: Any) -> int:
    try:
        n = int(value)
        if isinstance(value, bool):
            raise ValueError
    except (TypeError, ValueError):
        raise CategoryError(400, "invalid_min_items", "En az öğe sayısı bir sayı olmalı") from None
    lo, hi = MIN_ITEMS_RANGE
    if not lo <= n <= hi:
        raise CategoryError(400, "invalid_min_items", f"En az öğe sayısı {lo} ile {hi} arasında olmalı")
    return n


def _row(r) -> dict[str, Any]:
    return {"slug": r["slug"], "title": r["title"], "position": r["position"], "enabled": bool(r["enabled"]),
            "min_items": r["min_items"]}


def get(slug: str) -> Optional[dict[str, Any]]:
    r = db.query_one("SELECT * FROM home_categories WHERE slug = ?", (slug,))
    return _row(r) if r else None


def exists(slug: str) -> bool:
    return bool(slug) and db.query_one("SELECT 1 FROM home_categories WHERE slug = ?", (slug,)) is not None


def title_of(slug: str) -> Optional[str]:
    r = db.query_one("SELECT title FROM home_categories WHERE slug = ?", (slug,))
    return r["title"] if r else None


def _rows(include_disabled: bool) -> list[dict[str, Any]]:
    sql = "SELECT * FROM home_categories" + ("" if include_disabled else " WHERE enabled = 1") + " ORDER BY position, slug"
    return [_row(r) for r in db.query(sql)]


def enabled_slugs() -> list[str]:
    """Slugs of the enabled categories in admin order (``[]`` when the table is unreadable)."""
    try:
        return [c["slug"] for c in _rows(False)]
    except Exception:   # noqa: BLE001 - the home must never fail because of the category table
        return []


def membership() -> dict[str, dict[str, list[str]]]:
    """``{slug: {list_id: [canonical ids in position order]}}`` of every ``category_*`` list in ``library_lists``."""
    out: dict[str, dict[str, list[str]]] = {}
    for r in db.query("SELECT list_id, canonical_id FROM library_lists WHERE list_id GLOB 'category_*' "
                      "ORDER BY list_id, position, canonical_id"):
        slug = slug_of_list(r["list_id"])
        if slug:
            out.setdefault(slug, {}).setdefault(r["list_id"], []).append(r["canonical_id"])
    return out


def member_lists(snap, slug: str) -> list[list[dict]]:
    """The ``category_<slug>_*`` lists of EVERY site as lists of snapshot items (list order; ids missing from the
    snapshot are dropped). Non-library snapshots have no lists."""
    if not valid_slug(slug) or getattr(snap, "source", "") != "library":
        return []
    by_list: dict[str, list[dict]] = {}
    for r in db.query("SELECT list_id, canonical_id FROM library_lists WHERE list_id GLOB ? "
                      "ORDER BY list_id, position, canonical_id", (f"{LIST_PREFIX}{slug}_*",)):
        item = snap.by_id.get(r["canonical_id"])
        if item:
            by_list.setdefault(r["list_id"], []).append(item)
    return list(by_list.values())


def _is_ready(item: dict) -> bool:
    return (item.get("availability") or {}).get("state", "ready") == "ready"


def list_all(include_disabled: bool = True) -> list[dict[str, Any]]:
    """Categories in admin order with counts: ``lists`` (number of site lists), ``titles`` (distinct titles in them),
    ``playable`` (of those, ready in the current catalogue snapshot)."""
    cats = _rows(include_disabled)
    if not cats:
        return []
    members = membership()
    snap = None
    try:
        from .. import cache
        snap = cache.get()
    except Exception:   # noqa: BLE001 - counts degrade to "unknown playable", never an error
        snap = None
    for c in cats:
        lists = members.get(c["slug"], {})
        ids = {cid for cids in lists.values() for cid in cids}
        c["lists"] = len(lists)
        c["titles"] = len(ids)
        playable = set()
        if snap is not None and getattr(snap, "source", "") == "library":
            for cid in ids:
                item = snap.by_id.get(cid)
                if item and _is_ready(item):
                    playable.add(item["id"])
        c["playable"] = len(playable)
    return cats


def create(title: str, slug: Optional[str] = None) -> dict[str, Any]:
    """Add a category at the end of the order. A slug that is already taken is a 409 ``slug_exists``; a slug whose
    category was deleted earlier is free again and its titles come back."""
    title = _clean_title(title)
    if slug is None or slug == "":
        slug = slugify(title)
        if not slug:
            raise CategoryError(400, "invalid_slug", "Bu addan bir kısa ad üretilemedi; harf ya da rakam kullanın")
    elif not valid_slug(slug):
        raise CategoryError(400, "invalid_slug", "Kısa ad yalnız küçük harf, rakam ve tire içerebilir")
    now = int(time.time())
    with closing(db.connect()) as conn, conn:
        if conn.execute("SELECT 1 FROM home_categories WHERE slug = ?", (slug,)).fetchone():
            raise CategoryError(409, "slug_exists", "Bu kategori zaten var")
        pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 AS n FROM home_categories").fetchone()["n"]
        conn.execute("INSERT INTO home_categories (slug, title, position, enabled, min_items, created_at, updated_at) "
                     "VALUES (?,?,?,?,?,?,?)", (slug, title, pos, 1, DEFAULT_MIN_ITEMS, now, now))
    return get(slug) or {}


def update(slug: str, title: Optional[str] = None, enabled: Optional[bool] = None,
           min_items: Optional[int] = None) -> dict[str, Any]:
    sets, params = [], []
    if title is not None:
        sets.append("title = ?")
        params.append(_clean_title(title))
    if enabled is not None:
        sets.append("enabled = ?")
        params.append(1 if enabled else 0)
    if min_items is not None:
        sets.append("min_items = ?")
        params.append(_clean_min_items(min_items))
    with closing(db.connect()) as conn, conn:
        if not conn.execute("SELECT 1 FROM home_categories WHERE slug = ?", (slug,)).fetchone():
            raise CategoryError(404, "not_found", "Kategori bulunamadı")
        if sets:
            conn.execute(f"UPDATE home_categories SET {', '.join(sets)}, updated_at = ? WHERE slug = ?",
                         (*params, int(time.time()), slug))
    return get(slug) or {}


def reorder(slugs: list[str]) -> list[dict[str, Any]]:
    """Set the admin order: ``slugs`` first (in that order), categories not listed keep their relative order after
    them. An unknown slug is a 400 ``unknown_slug``."""
    wanted: list[str] = []
    for s in slugs or []:
        if s not in wanted:
            wanted.append(s)
    with closing(db.connect()) as conn, conn:
        current = [r["slug"] for r in conn.execute("SELECT slug FROM home_categories ORDER BY position, slug")]
        unknown = [s for s in wanted if s not in current]
        if unknown:
            raise CategoryError(400, "unknown_slug", f"Bilinmeyen kategori: {unknown[0]}")
        final = wanted + [s for s in current if s not in wanted]
        now = int(time.time())
        conn.executemany("UPDATE home_categories SET position = ?, updated_at = ? WHERE slug = ?",
                         [(pos, now, s) for pos, s in enumerate(final)])
    return list_all(True)


def delete(slug: str) -> None:
    """Drop the category row only; its ``category_<slug>_*`` membership lists stay."""
    with closing(db.connect()) as conn, conn:
        if conn.execute("DELETE FROM home_categories WHERE slug = ?", (slug,)).rowcount == 0:
            raise CategoryError(404, "not_found", "Kategori bulunamadı")
