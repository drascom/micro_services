"""Admin-managed home-screen categories ("Kategoriler"): a to-do-list-like set of rows the home shows in admin order.

Table ``home_categories`` (slug, title, position, enabled, min_items). Membership is NOT stored here: it is read from
``library_lists`` where a category's lists are ``category_<slug>_<site_id>`` (written by routing, ``lists.set_list``).
Deleting a category only drops its row here; the membership lists stay, so adding the same slug again brings the
titles back. A title may be in many categories. The home row of a category is ``cat_<slug>`` (``homelayout``).

Slugs are ``[a-z0-9-]`` (no underscore: that is what separates the slug from the site id in a list id).

The same table also holds the home's fixed "skeleton" rows (``kind = 'system'``: ``continue``, ``trending_series``, ...;
:data:`SYSTEM_DEFAULT`). They are seeded lazily (:func:`ensure_system`, idempotent, never touches an existing order),
cannot be deleted, hidden or renamed (409 ``locked``) and only change position, so the admin can put a category between
them. Every category helper below works on ``kind = 'category'`` rows only unless it says otherwise.

T24 (server2, admin "Kategoriler"): the skeleton rows are the FIXED categories (seeded by migration 020; they can be hidden,
renamed and given a ``max_items`` limit, never deleted); ``kind = 'category'`` rows are the DYNAMIC ones. A dynamic row's
``source`` (JSON, :func:`clean_source`) says where its titles come from: ``lists`` (default: only the routing lists
``category_<slug>_*``), ``genre``, ``type``, ``site`` (a site's home section / listing / genre origin) or ``list`` (one
``library_lists`` id). Whatever the source, the routing lists of the slug come first (a routing rule may target any
category), then the source's titles; ``max_items`` (0 = no limit) cuts the row, ``min_items`` hides a too-short one.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from typing import Any, Optional

from ... import db
from .textnorm import fold

LIST_PREFIX = "category_"
ROW_PREFIX = "cat_"
MAX_SLUG = 40
MAX_TITLE = 60
DEFAULT_MIN_ITEMS = 6
MIN_ITEMS_RANGE = (1, 100)
# The home's fixed rows in today's order (row id = slug). Titles come from ``homelayout.TITLES`` (passed to ensure_system).
SYSTEM_DEFAULT = ("continue", "trending_series", "series", "trending_movies", "noteworthy_movies", "movies", "mylist")
SYSTEM_SLUGS = frozenset(SYSTEM_DEFAULT)
SYSTEM_TITLES = {
    "continue": "İzlemeye Devam Et",
    "trending_series": "Haftanın Trendleri · Diziler",
    "series": "Tüm Diziler",
    "trending_movies": "Haftanın Trendleri · Filmler",
    "noteworthy_movies": "Dikkate Değer Filmler",
    "movies": "Tüm Filmler",
    "mylist": "Listem",
}
MAX_ITEMS_RANGE = (0, 500)   # 0 = no limit
SOURCE_KINDS = ("lists", "genre", "type", "site", "list")
ORIGIN_KINDS = ("section", "listing", "genre")
TYPES = ("movie", "series")
_LIST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$")
CATEGORY_AFTER = "trending_series"   # where the categories sit in the default order (after it, before "series")
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


def _clean_max_items(value: Any) -> int:
    try:
        n = int(value)
        if isinstance(value, bool):
            raise ValueError
    except (TypeError, ValueError):
        raise CategoryError(400, "invalid_max_items", "Sınır bir sayı olmalı") from None
    lo, hi = MAX_ITEMS_RANGE
    if not lo <= n <= hi:
        raise CategoryError(400, "invalid_max_items", f"Sınır {lo} ile {hi} arasında olmalı (0 = sınırsız)")
    return n


def _bad_source(message: str) -> CategoryError:
    return CategoryError(400, "invalid_source", message)


def clean_source(data: Any) -> dict[str, Any]:
    """Validate a dynamic category's definition. ``None``/``{}`` = ``{"kind": "lists"}`` (routing lists only).

    * ``{"kind": "genre", "genre": "<name>", "type"?}`` - titles whose ``genres`` hold the name (folded compare)
    * ``{"kind": "type", "type": "movie|series"}``
    * ``{"kind": "site", "site_id", "origin": "section|listing|genre", "value", "type"?}`` - a site's origin group
    * ``{"kind": "list", "list_id", "type"?}`` - one ``library_lists`` list (e.g. a routing role list)
    Every kind takes an optional ``type`` filter."""
    if data in (None, "", {}):
        return {"kind": "lists"}
    if not isinstance(data, dict):
        raise _bad_source("Kaynak bir sözlük olmalı")
    kind = str(data.get("kind") or "lists").strip()
    if kind not in SOURCE_KINDS:
        raise _bad_source(f"Bilinmeyen kaynak türü: {kind}")
    out: dict[str, Any] = {"kind": kind}
    typ = str(data.get("type") or "").strip().lower()
    if typ and typ not in TYPES:
        raise _bad_source("Tür movie ya da series olmalı")
    if kind == "type" and not typ:
        raise _bad_source("Tür seçilmeli (movie ya da series)")
    if kind == "genre":
        genre = " ".join(str(data.get("genre") or "").split())
        if not genre or len(genre) > MAX_TITLE:
            raise _bad_source("Tür (genre) adı boş ya da çok uzun")
        out["genre"] = genre
    elif kind == "site":
        site_id = str(data.get("site_id") or "").strip()
        if not site_id or db.query_one("SELECT 1 FROM sites WHERE site_id = ?", (site_id,)) is None:
            raise _bad_source("Site bulunamadı")
        origin = str(data.get("origin") or "").strip()
        if origin not in ORIGIN_KINDS:
            raise _bad_source("Bölüm türü section, listing ya da genre olmalı")
        value = " ".join(str(data.get("value") or "").split())
        if not value or len(value) > 200:
            raise _bad_source("Bölüm / liste adı boş ya da çok uzun")
        out.update(site_id=site_id, origin=origin, value=value)
    elif kind == "list":
        lid = str(data.get("list_id") or "").strip()
        if not _LIST_ID_RE.match(lid):
            raise _bad_source("Liste kimliği geçersiz")
        out["list_id"] = lid
    if typ:
        out["type"] = typ
    return out


def _load_source(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        value = {}
    return value if isinstance(value, dict) and value.get("kind") in SOURCE_KINDS else {"kind": "lists"}


def _col(r, name: str, default: Any) -> Any:
    try:
        value = r[name]
    except (IndexError, KeyError):
        return default
    return default if value is None else value


def _row(r) -> dict[str, Any]:
    out = {"slug": r["slug"], "title": r["title"], "position": r["position"], "enabled": bool(r["enabled"]),
           "min_items": r["min_items"], "kind": r["kind"] or "category", "max_items": int(_col(r, "max_items", 0) or 0),
           "source": _load_source(_col(r, "source", ""))}
    if out["kind"] == "system":
        out.update(locked=True, min_items=None, source=None)
    return out


def get_any(slug: str) -> Optional[dict[str, Any]]:
    """The row ``slug`` of either kind (skeleton or category)."""
    r = db.query_one("SELECT * FROM home_categories WHERE slug = ?", (slug,))
    return _row(r) if r else None


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
    return CategoryError(409, "locked", "Sabit satır silinemez (yalnız gizlenir); kaynağı ve en az öğe sayısı değişmez")


def _rows(include_disabled: bool) -> list[dict[str, Any]]:
    """Categories only (admin order)."""
    sql = ("SELECT * FROM home_categories WHERE kind = 'category'" + ("" if include_disabled else " AND enabled = 1")
           + " ORDER BY position, slug")
    return [_row(r) for r in db.query(sql)]


def ordered_rows() -> list[dict[str, Any]]:
    """EVERY row (skeleton + categories) in admin order, ``[]`` when none."""
    return [_row(r) for r in db.query("SELECT * FROM home_categories ORDER BY position, slug")]


def seed_system(conn, titles: Optional[dict[str, str]] = None, order: Optional[list[str]] = None) -> bool:
    """Seed the missing skeleton rows on ``conn`` (``titles``: slug -> title, default :data:`SYSTEM_TITLES`; ``order``:
    preferred skeleton order, default :data:`SYSTEM_DEFAULT`). Nothing missing = nothing written (idempotent; an
    existing order is never touched).

    First seeding: skeleton rows in ``order`` with the existing categories (their relative order kept) after
    ``CATEGORY_AFTER`` - exactly the home as it was before this table held the skeleton. A single missing skeleton row
    later goes right after the skeleton row that precedes it in ``order`` (or first). Returns True when it wrote."""
    titles = titles or SYSTEM_TITLES
    wanted = [s for s in (order or SYSTEM_DEFAULT) if s in SYSTEM_SLUGS]
    wanted += [s for s in SYSTEM_DEFAULT if s not in wanted]
    now = int(time.time())
    have = conn.execute("SELECT slug, kind FROM home_categories ORDER BY position, slug").fetchall()
    present = {r["slug"] for r in have if r["kind"] == "system"}
    missing = [s for s in wanted if s not in present]
    if not missing:
        return False
    cur = [r["slug"] for r in have]
    if not present:
        skeleton = list(wanted)
        at = skeleton.index(CATEGORY_AFTER) + 1 if CATEGORY_AFTER in skeleton else len(skeleton)
        final = skeleton[:at] + cur + skeleton[at:]
    else:
        final = list(cur)
        for slug in missing:
            prev = [s for s in wanted[:wanted.index(slug)] if s in final]
            final.insert(final.index(prev[-1]) + 1 if prev else 0, slug)
    for slug in missing:
        conn.execute("INSERT OR IGNORE INTO home_categories (slug, title, position, enabled, min_items, created_at, "
                     "updated_at, kind) VALUES (?,?,?,?,?,?,?, 'system')",
                     (slug, titles.get(slug) or SYSTEM_TITLES.get(slug) or slug, 0, 1, DEFAULT_MIN_ITEMS, now, now))
    conn.executemany("UPDATE home_categories SET position = ? WHERE slug = ?",
                     [(pos, s) for pos, s in enumerate(final)])
    return True


def ensure_system(titles: dict[str, str], order: Optional[list[str]] = None) -> bool:
    """:func:`seed_system` in its own transaction (the migration seeds already; this re-adds a missing row)."""
    with db.transaction() as conn:
        return seed_system(conn, titles, order)


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
    snapshot are dropped)."""
    if not valid_slug(slug):
        return []
    by_list: dict[str, list[dict]] = {}
    for r in db.query("SELECT list_id, canonical_id FROM library_lists WHERE list_id GLOB ? "
                      "ORDER BY list_id, position, canonical_id", (f"{LIST_PREFIX}{slug}_*",)):
        item = snap.by_id.get(r["canonical_id"])
        if item:
            by_list.setdefault(r["list_id"], []).append(item)
    return list(by_list.values())


def _type_ok(item: dict, src: dict) -> bool:
    return not src.get("type") or item.get("type") == src["type"]


def source_items(snap, src: dict[str, Any]) -> list[dict]:
    """The titles a category definition ``src`` selects from ``snap`` (``snap.items`` = the pool to pick from,
    ``snap.listed``/``by_id`` for id lookups), in the row order. ``lists`` selects nothing here (routing lists only)."""
    kind = (src or {}).get("kind") or "lists"
    by_id = getattr(snap, "listed", None) or {i["id"]: i for i in snap.items}
    if kind == "genre":
        key = fold(src["genre"])
        out = [i for i in snap.items if _type_ok(i, src) and any(fold(g) == key for g in i.get("genres") or [])]
        return sorted(out, key=lambda i: (-(i.get("rating") or 0), i["id"]))
    if kind == "type":
        out = [i for i in snap.items if _type_ok(i, src)]
        return sorted(out, key=lambda i: (-(i.get("added_at") or 0), i["id"]))
    if kind == "site":
        key, best = fold(src["value"]), {}
        for r in db.query("SELECT s.canonical_id, o.value, o.position FROM source_origins o JOIN source_items s "
                          "ON s.site_id = o.site_id AND s.key = o.source_key WHERE o.site_id = ? AND o.kind = ?",
                          (src["site_id"], src["origin"])):
            cid = r["canonical_id"]
            if cid and fold(r["value"]) == key:
                pos = int(r["position"] or 0)
                best[cid] = min(pos, best.get(cid, pos))
        ids = sorted(best, key=lambda c: (best[c], c))
    elif kind == "list":
        ids = [r["canonical_id"] for r in db.query(
            "SELECT canonical_id FROM library_lists WHERE list_id = ? ORDER BY position, canonical_id", (src["list_id"],))]
    else:
        return []
    out, seen = [], set()
    for cid in ids:
        item = by_id.get(cid)
        if item and item["id"] not in seen and _type_ok(item, src):
            seen.add(item["id"])
            out.append(item)
    return out


def category_items(snap, cat: dict[str, Any], round_robin) -> list[dict]:
    """A dynamic category's full pool: its routing lists (``round_robin`` merges the sites) then its source's titles,
    no repeats (the row limit is applied by the caller)."""
    listed = getattr(snap, "listed", None) or getattr(snap, "by_id", {})
    out = list(round_robin(member_lists(_ByIdView(listed), cat["slug"])))
    seen = {i["id"] for i in out}
    for item in source_items(snap, cat.get("source") or {}):
        if item["id"] not in seen:
            seen.add(item["id"])
            out.append(item)
    return out


class _ByIdView:
    def __init__(self, by_id: dict) -> None:
        self.by_id = by_id


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
        from . import cache
        snap = cache.get()
    except Exception:   # noqa: BLE001 - counts degrade to "unknown playable", never an error
        snap = None
    for c in cats:
        if c["kind"] == "system":
            continue
        lists = members.get(c["slug"], {})
        ids = {cid for cids in lists.values() for cid in cids}
        c["lists"] = len(lists)
        playable = set()
        if snap is not None:
            for cid in list(ids):
                item = snap.by_id.get(cid)
                if item and _is_ready(item):
                    playable.add(item["id"])
                if item and item["id"] != cid:
                    ids.discard(cid)
                    ids.add(item["id"])
            if (c["source"] or {}).get("kind", "lists") != "lists":
                try:
                    extra = source_items(_AllView(snap), c["source"])
                except Exception:   # noqa: BLE001 - counts only
                    extra = []
                ids |= {i["id"] for i in extra}
                playable |= {i["id"] for i in extra if _is_ready(i) and not i.get("unlisted")}
        c["titles"] = len(ids)
        c["playable"] = len(playable)
    return cats


class _AllView:
    """Every title of a snapshot (admin counts: ``titles`` = all, ``playable`` = ready ones)."""

    def __init__(self, snap) -> None:
        self.items = list(snap.items)
        self.listed = {i["id"]: i for i in self.items}


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


def create(title: str, slug: Optional[str] = None, source: Any = None, max_items: Any = None,
           min_items: Any = None) -> dict[str, Any]:
    """Add a dynamic category after the last one. A slug that is already taken is a 409 ``slug_exists``; a slug whose
    category was deleted earlier is free again and its routing titles come back."""
    title = _clean_title(title)
    src = clean_source(source)
    limit = 0 if max_items in (None, "") else _clean_max_items(max_items)
    least = DEFAULT_MIN_ITEMS if min_items in (None, "") else _clean_min_items(min_items)
    if slug is None or slug == "":
        slug = slugify(title)
        if not slug:
            raise CategoryError(400, "invalid_slug", "Bu addan bir kısa ad üretilemedi; harf ya da rakam kullanın")
    elif not valid_slug(slug):
        raise CategoryError(400, "invalid_slug", "Kısa ad yalnız küçük harf, rakam ve tire içerebilir")
    now = int(time.time())
    if slug in SYSTEM_SLUGS:
        raise CategoryError(409, "slug_exists", "Bu ad ana ekranın kilitli satırlarından birine ait")
    with db.transaction() as conn:
        if conn.execute("SELECT 1 FROM home_categories WHERE slug = ?", (slug,)).fetchone():
            raise CategoryError(409, "slug_exists", "Bu kategori zaten var")
        pos = _new_position(conn)
        conn.execute("INSERT INTO home_categories (slug, title, position, enabled, min_items, created_at, updated_at, "
                     "source, max_items) VALUES (?,?,?,?,?,?,?,?,?)",
                     (slug, title, pos, 1, least, now, now, json.dumps(src, ensure_ascii=False), limit))
    return get(slug) or {}


def update(slug: str, title: Optional[str] = None, enabled: Optional[bool] = None,
           min_items: Optional[int] = None, max_items: Optional[int] = None, source: Any = None) -> dict[str, Any]:
    """Change a row. Skeleton (fixed) rows: title, enabled and max_items only (409 ``locked`` otherwise)."""
    if slug in SYSTEM_SLUGS and (min_items is not None or source is not None):
        raise _locked(slug)
    sets, params = [], []
    if max_items is not None:
        sets.append("max_items = ?")
        params.append(_clean_max_items(max_items))
    if source is not None:
        sets.append("source = ?")
        params.append(json.dumps(clean_source(source), ensure_ascii=False))
    if title is not None:
        sets.append("title = ?")
        params.append(_clean_title(title))
    if enabled is not None:
        sets.append("enabled = ?")
        params.append(1 if enabled else 0)
    if min_items is not None:
        sets.append("min_items = ?")
        params.append(_clean_min_items(min_items))
    with db.transaction() as conn:
        if not conn.execute("SELECT 1 FROM home_categories WHERE slug = ?", (slug,)).fetchone():
            raise CategoryError(404, "not_found", "Kategori bulunamadı")
        if sets:
            conn.execute(f"UPDATE home_categories SET {', '.join(sets)}, updated_at = ? WHERE slug = ?",
                         (*params, int(time.time()), slug))
    return get_any(slug) or {}


def reorder(slugs: list[str]) -> list[dict[str, Any]]:
    """Set the admin order: ``slugs`` first (in that order), categories not listed keep their relative order after
    them. Works on every row (skeleton + categories). An unknown slug is a 400 ``unknown_slug``."""
    wanted: list[str] = []
    for s in slugs or []:
        if s not in wanted:
            wanted.append(s)
    with db.transaction() as conn:
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
    with db.transaction() as conn:
        if conn.execute("DELETE FROM home_categories WHERE slug = ?", (slug,)).rowcount == 0:
            raise CategoryError(404, "not_found", "Kategori bulunamadı")
