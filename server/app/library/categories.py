"""Admin-managed home-screen categories ("Kategoriler"): a to-do-list-like set of rows the home shows in admin order.

Table ``home_categories`` (slug, title, position, enabled, min_items). Membership is NOT stored here: it is read from
``library_lists`` where a category's lists are ``category_<slug>_<site_id>`` (written by the scraper's ``category`` role).
Deleting a category only drops its row here; the membership lists stay, so adding the same slug again brings the
titles back. A title may be in many categories. The home row of a category is ``cat_<slug>`` (``homelayout``).

Slugs are ``[a-z0-9-]`` (no underscore: that is what separates the slug from the site id in a list id).

The same table also holds the home's fixed "skeleton" rows (``kind = 'system'``: ``continue``, ``trending_series``, ...;
:data:`SYSTEM_DEFAULT`). They are seeded lazily (:func:`ensure_system`, idempotent, never touches an existing order),
cannot be deleted, hidden or renamed (409 ``locked``) and only change position, so the admin can put a category between
them. Every category helper below works on ``kind = 'category'`` rows only unless it says otherwise.
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
# The home's fixed rows in the default order (row id = slug; series/movie rows alternate). Titles come from
# ``homelayout.TITLES`` (passed to ensure_system).
SYSTEM_DEFAULT = ("continue", "new_episodes", "latest_movies", "trending_series", "trending_movies", "must_watch",
                  "series", "movies")
SYSTEM_SLUGS = frozenset(SYSTEM_DEFAULT)
CATEGORY_AFTER = "must_watch"   # where the categories sit in the default order (after it, before "series")
# skeleton rows of earlier defaults: dropped from the table by ensure_system (their row ids stay fetchable via /api/row;
# "mylist" is its own client page now)
RETIRED_SYSTEM = ("spotlight", "noteworthy_series", "noteworthy_movies", "mylist")
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
    out = {"slug": r["slug"], "title": r["title"], "position": r["position"], "enabled": bool(r["enabled"]),
           "min_items": r["min_items"], "kind": r["kind"] or "category"}
    if out["kind"] == "system":
        out.update(locked=True, enabled=True, min_items=None)
    return out


def get(slug: str) -> Optional[dict[str, Any]]:
    """The category ``slug`` (``None`` for an unknown slug and for a skeleton row)."""
    r = db.query_one("SELECT * FROM home_categories WHERE slug = ? AND kind = 'category'", (slug,))
    return _row(r) if r else None


def exists(slug: str) -> bool:
    return bool(slug) and db.query_one("SELECT 1 FROM home_categories WHERE slug = ? AND kind = 'category'",
                                       (slug,)) is not None


def title_of(slug: str) -> Optional[str]:
    r = db.query_one("SELECT title FROM home_categories WHERE slug = ? AND kind = 'category'", (slug,))
    return r["title"] if r else None


def is_system(slug: str) -> bool:
    return slug in SYSTEM_SLUGS


def _locked(slug: str) -> CategoryError:
    return CategoryError(409, "locked", "Bu satır kilitli: silinemez, gizlenemez ve adı değişmez; yalnız sırası değişir")


def _rows(include_disabled: bool) -> list[dict[str, Any]]:
    """Categories only (admin order)."""
    sql = ("SELECT * FROM home_categories WHERE kind = 'category'" + ("" if include_disabled else " AND enabled = 1")
           + " ORDER BY position, slug")
    return [_row(r) for r in db.query(sql)]


def ordered_rows() -> list[dict[str, Any]]:
    """EVERY row (skeleton + categories) in admin order, ``[]`` when none."""
    return [_row(r) for r in db.query("SELECT * FROM home_categories ORDER BY position, slug")]


def ensure_system(titles: dict[str, str], order: Optional[list[str]] = None) -> bool:
    """Seed the missing skeleton rows (``titles``: slug -> title; ``order``: preferred skeleton order, default
    :data:`SYSTEM_DEFAULT`). Nothing missing = nothing written (idempotent; an existing order is never touched).

    First seeding: skeleton rows in ``order`` with the existing categories (their relative order kept) after
    ``CATEGORY_AFTER`` - exactly the home as it was before this table held the skeleton. A single missing skeleton row
    later goes right after the skeleton row that precedes it in ``order`` (or first). Skeleton rows that are no longer
    in :data:`SYSTEM_SLUGS` (``RETIRED_SYSTEM``: earlier defaults) are deleted; the categories that sat behind one of
    them move behind ``CATEGORY_AFTER`` (as on a first seeding). A skeleton row whose title is its own slug (seeded
    without a title) gets ``titles[slug]``; any other title (the admin's) is kept. Returns True when it wrote."""
    wanted = _wanted(order)
    now = int(time.time())
    with closing(db.connect()) as conn, conn:
        have = conn.execute("SELECT slug, kind FROM home_categories ORDER BY position, slug").fetchall()
        retired = {r["slug"] for r in have if r["kind"] == "system" and r["slug"] not in SYSTEM_SLUGS}
        orphans: list[str] = []   # categories whose preceding skeleton row is retired
        anchor = None
        for r in have:
            if r["kind"] == "system":
                anchor = r["slug"]
            elif anchor in retired:
                orphans.append(r["slug"])
        if retired:
            conn.executemany("DELETE FROM home_categories WHERE slug = ? AND kind = 'system'", [(s,) for s in retired])
        present = {r["slug"] for r in have if r["kind"] == "system"} - retired
        missing = [s for s in wanted if s not in present]
        # a skeleton row seeded without its title (title = the slug itself) gets the default title; a title the admin gave stays
        untitled = [(titles[s], s) for s in present if titles.get(s) and titles[s] != s]
        repaired = conn.executemany("UPDATE home_categories SET title = ?, updated_at = %d WHERE slug = ? AND kind = 'system' "
                                    "AND title = slug" % now, untitled).rowcount if untitled else 0
        if not missing and not retired:
            return repaired > 0
        cur = [r["slug"] for r in have if r["slug"] not in retired]
        if not present:
            skeleton = list(wanted)
            at = skeleton.index(CATEGORY_AFTER) + 1 if CATEGORY_AFTER in skeleton else len(skeleton)
            final = skeleton[:at] + cur + skeleton[at:]
        else:
            final = [s for s in cur if s not in orphans]
            for slug in missing:
                prev = [s for s in wanted[:wanted.index(slug)] if s in final]
                final.insert(final.index(prev[-1]) + 1 if prev else 0, slug)
            if orphans:
                at = final.index(CATEGORY_AFTER) + 1 if CATEGORY_AFTER in final else len(final)
                while at < len(final) and final[at] not in SYSTEM_SLUGS:   # behind the categories already there
                    at += 1
                final[at:at] = orphans
        for slug in missing:
            conn.execute("INSERT OR IGNORE INTO home_categories (slug, title, position, enabled, min_items, created_at, "
                         "updated_at, kind) VALUES (?,?,?,?,?,?,?, 'system')",
                         (slug, titles.get(slug) or slug, 0, 1, DEFAULT_MIN_ITEMS, now, now))
        conn.executemany("UPDATE home_categories SET position = ? WHERE slug = ?",
                         [(pos, s) for pos, s in enumerate(final)])
    return True


def _wanted(order: Optional[list[str]] = None) -> list[str]:
    wanted = [s for s in dict.fromkeys(order or SYSTEM_DEFAULT) if s in SYSTEM_SLUGS]
    return wanted + [s for s in SYSTEM_DEFAULT if s not in wanted]


def reset_order(order: Optional[list[str]] = None) -> list[dict[str, Any]]:
    """"Varsayılan sıraya döndür": the skeleton rows go back to ``order`` (default :data:`SYSTEM_DEFAULT`); every
    category stays behind the skeleton row it follows now (its anchor; several keep their relative order), a category
    with no anchor in the new skeleton (first row, or after a retired row) goes after ``CATEGORY_AFTER``. Categories
    are not changed otherwise (title, enabled, min_items kept). Returns the full list (:func:`list_all`)."""
    wanted = _wanted(order)
    with closing(db.connect()) as conn, conn:
        have = conn.execute("SELECT slug, kind FROM home_categories ORDER BY position, slug").fetchall()
        skeleton = [s for s in wanted if any(r["slug"] == s and r["kind"] == "system" for r in have)]
        groups: dict[Optional[str], list[str]] = {}
        anchor = None
        for r in have:
            if r["kind"] == "system":
                anchor = r["slug"]
            else:
                groups.setdefault(anchor if anchor in skeleton else None, []).append(r["slug"])
        loose = groups.pop(None, [])
        final: list[str] = []
        for s in skeleton:
            final.append(s)
            final += groups.get(s, [])
            if s == CATEGORY_AFTER:
                final += loose
                loose = []
        final += loose   # no CATEGORY_AFTER in the skeleton: at the end
        final += [r["slug"] for r in have if r["slug"] not in final]   # system rows not in the skeleton order
        now = int(time.time())
        conn.executemany("UPDATE home_categories SET position = ?, updated_at = ? WHERE slug = ?",
                         [(pos, now, s) for pos, s in enumerate(final)])
    return list_all(True, include_system=True)


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


def list_all(include_disabled: bool = True, include_system: bool = False) -> list[dict[str, Any]]:
    """Categories in admin order with counts: ``lists`` (number of site lists), ``titles`` (distinct titles in them),
    ``playable`` (of those, ready in the current catalogue snapshot). ``include_system``: the skeleton rows too, in
    their place (``kind: "system"``, ``locked: true``, no counts)."""
    cats = ordered_rows() if include_system else _rows(include_disabled)
    if include_system and not include_disabled:
        cats = [c for c in cats if c["enabled"]]
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
        if c["kind"] == "system":
            continue
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


def _new_position(conn) -> int:
    """Position of a new category. Skeleton seeded: right after the last category (or after ``CATEGORY_AFTER`` when there
    is none) so the home keeps its shape, later rows shifted down; otherwise the end."""
    if conn.execute("SELECT 1 FROM home_categories WHERE kind = 'system' LIMIT 1").fetchone() is None:
        return conn.execute("SELECT COALESCE(MAX(position), -1) + 1 AS n FROM home_categories").fetchone()["n"]
    row = conn.execute("SELECT MAX(position) AS p FROM home_categories WHERE kind = 'category'").fetchone()
    if row["p"] is None:
        row = conn.execute("SELECT position AS p FROM home_categories WHERE slug = ?", (CATEGORY_AFTER,)).fetchone()
    if row is None or row["p"] is None:
        return conn.execute("SELECT COALESCE(MAX(position), -1) + 1 AS n FROM home_categories").fetchone()["n"]
    pos = row["p"] + 1
    conn.execute("UPDATE home_categories SET position = position + 1 WHERE position >= ?", (pos,))
    return pos


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
    if slug in SYSTEM_SLUGS:
        raise CategoryError(409, "slug_exists", "Bu ad ana ekranın kilitli satırlarından birine ait")
    with closing(db.connect()) as conn, conn:
        if conn.execute("SELECT 1 FROM home_categories WHERE slug = ?", (slug,)).fetchone():
            raise CategoryError(409, "slug_exists", "Bu kategori zaten var")
        pos = _new_position(conn)
        conn.execute("INSERT INTO home_categories (slug, title, position, enabled, min_items, created_at, updated_at) "
                     "VALUES (?,?,?,?,?,?,?)", (slug, title, pos, 1, DEFAULT_MIN_ITEMS, now, now))
    return get(slug) or {}


def update(slug: str, title: Optional[str] = None, enabled: Optional[bool] = None,
           min_items: Optional[int] = None) -> dict[str, Any]:
    if slug in SYSTEM_SLUGS and (title is not None or enabled is not None or min_items is not None):
        raise _locked(slug)
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
    them. Works on every row (skeleton + categories). An unknown slug is a 400 ``unknown_slug``."""
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
    return list_all(True, include_system=True)


def delete(slug: str) -> None:
    """Drop the category row only; its ``category_<slug>_*`` membership lists stay. A skeleton row is 409 ``locked``."""
    if slug in SYSTEM_SLUGS:
        raise _locked(slug)
    with closing(db.connect()) as conn, conn:
        if conn.execute("DELETE FROM home_categories WHERE slug = ?", (slug,)).rowcount == 0:
            raise CategoryError(404, "not_found", "Kategori bulunamadı")
