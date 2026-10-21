"""``routing_rules`` schema + store (README §5.1). A rule: "a title whose origin/field matches X goes to group Y".

match_kind: ``section`` (home section title) | ``listing`` (listing name) | ``genre`` (origin or the title's ``genres``) |
``type`` (``movie|series``) | ``url_pattern`` (regex on the source address). Values compare with ``textnorm.fold``.
target_kind: ``role`` (one of :data:`ROLES`) | ``category`` (``home_categories.slug``) | ``ignore``.
``site_id`` NULL = the rule holds for every site.
"""
from __future__ import annotations

import re
import time
from typing import Any, Optional

from ... import db
from ...core.catalog import categories, lists

MATCH_KINDS = ("section", "listing", "genre", "type", "url_pattern")
TARGET_KINDS = ("role", "category", "ignore")
ROLES = ("trending", "latest_episodes", "latest_series", "latest_movies", "featured", "noteworthy_movies")
TYPES = ("movie", "series")
CREATORS = ("admin", "llm")
MAX_VALUE = 200
MAX_REGEX = 200
PRIORITY_RANGE = (-1000, 1000)


class RoutingError(ValueError):
    """A rejected routing operation: ``status`` = HTTP status, ``code`` = error-envelope code."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code = status, code


def _bad(code: str, message: str) -> RoutingError:
    return RoutingError(400, code, message)


def clean(data: Any, *, allow_new_category: bool = False) -> dict[str, Any]:
    """Validate a rule draft (admin form or LLM yaml). Returns the clean rule; an unknown category is an error unless
    ``allow_new_category`` (then ``new_category`` carries its name and ``target_value`` its slug)."""
    if not isinstance(data, dict):
        raise _bad("invalid_rule", "Kural bir sözlük olmalı")
    mk = str(data.get("match_kind") or "").strip()
    if mk not in MATCH_KINDS:
        raise _bad("invalid_match_kind", f"Bilinmeyen eşleşme türü: {mk or '-'}")
    mv = " ".join(str(data.get("match_value") or "").split())
    if not mv or len(mv) > (MAX_REGEX if mk == "url_pattern" else MAX_VALUE):
        raise _bad("invalid_match_value", "Eşleşme değeri boş ya da çok uzun")
    if mk == "url_pattern":
        problem = regex_problem(mv)
        if problem:
            raise _bad("invalid_match_value", f"Adres kalıbı kabul edilmedi: {problem}")
    if mk == "type":
        mv = mv.lower()
        if mv not in TYPES:
            raise _bad("invalid_match_value", "Tür movie ya da series olmalı")
    tk = str(data.get("target_kind") or "").strip()
    if tk not in TARGET_KINDS:
        raise _bad("invalid_target_kind", f"Bilinmeyen hedef türü: {tk or '-'}")
    tv = str(data.get("target_value") or "").strip()
    new_category = None
    if tk == "role":
        if tv not in ROLES:
            raise _bad("unknown_role", f"Bilinmeyen rol: {tv or '-'}")
    elif tk == "category":
        slug = tv if categories.valid_slug(tv) and categories.exists(tv) else _category_by_title(tv)
        if slug is None:
            slug = categories.slugify(tv)
            if not allow_new_category or not slug or not lists.CATEGORY_SLUG_RE.match(slug):
                raise _bad("unknown_category", f"Bilinmeyen kategori: {tv or '-'}")
            new_category = tv
        tv = slug
    else:
        tv = ""
    try:
        priority = int(data.get("priority") or 0)
    except (TypeError, ValueError):
        raise _bad("invalid_priority", "Öncelik bir sayı olmalı") from None
    lo, hi = PRIORITY_RANGE
    if not lo <= priority <= hi:
        raise _bad("invalid_priority", f"Öncelik {lo} ile {hi} arasında olmalı")
    out = {"match_kind": mk, "match_value": mv, "target_kind": tk, "target_value": tv, "priority": priority,
           "enabled": bool(data.get("enabled", True)), "note": " ".join(str(data.get("note") or "").split())[:200]}
    if new_category:
        out["new_category"] = new_category
    return out


_BACKREF = re.compile(r"\\[1-9]|\(\?P=|\\g<|\\k<")


def regex_problem(pattern: str) -> Optional[str]:
    """``None`` when ``pattern`` is a simple regex safe to run on every source address (it may come from the LLM):
    compiles, at most ``MAX_REGEX`` chars, no back-references, no quantified group that holds a quantifier (``(a+)+``,
    ``(\\w*)*``: catastrophic backtracking). Otherwise a short reason."""
    if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_REGEX:
        return "boş ya da çok uzun"
    try:
        re.compile(pattern)
    except re.error:
        return "geçerli bir düzenli ifade değil"
    if _BACKREF.search(pattern):
        return "geri başvuru içeriyor"
    stack: list[bool] = []          # per open group: does it hold a quantifier?
    i, n, in_class, inner = 0, len(pattern), False, False
    while i < n:
        ch = pattern[i]
        if ch == "\\":
            i += 2
            continue
        if in_class:
            in_class = ch != "]"
        elif ch == "[":
            in_class = True
            if pattern[i + 1:i + 2] == "]":
                i += 1
        elif ch == "(":
            stack.append(False)
        elif ch == ")":
            inner = stack.pop() if stack else False
            if inner and pattern[i + 1:i + 2] in ("*", "+", "{"):
                return "iç içe niceleyici (yavaş eşleşme riski)"
            if stack and inner:
                stack[-1] = True
        elif ch in "*+{" or (ch == "?" and i and pattern[i - 1] not in "(*+?}"):
            if stack:
                stack[-1] = True
        i += 1
    return None


def _category_by_title(text: str) -> Optional[str]:
    """Slug of the category whose slug or title folds to ``text`` (LLMs often answer with the title)."""
    from ...core.catalog.textnorm import fold
    key = fold(text)
    if not key:
        return None
    for c in db.query("SELECT slug, title FROM home_categories WHERE kind = 'category' ORDER BY position, slug"):
        if fold(c["slug"]) == key or fold(c["title"]) == key:
            return c["slug"]
    return None


def _row(r) -> dict[str, Any]:
    d = dict(r)
    d["enabled"] = bool(d["enabled"])
    return d


def get(rid: int) -> Optional[dict]:
    r = db.query_one("SELECT * FROM routing_rules WHERE id = ?", (int(rid),))
    return _row(r) if r else None


def for_site(site_id: str, *, enabled_only: bool = False) -> list[dict]:
    """The site's own rules + the rules for every site (``site_id`` NULL)."""
    sql = "SELECT * FROM routing_rules WHERE (site_id = ? OR site_id IS NULL OR site_id = '')"
    if enabled_only:
        sql += " AND enabled = 1"
    return [_row(r) for r in db.query(sql + " ORDER BY priority DESC, id", (site_id,))]


def add(site_id: Optional[str], data: Any, *, created_by: str = "admin") -> dict:
    rule = clean(data)
    if created_by not in CREATORS:
        raise ValueError("created_by")
    cur = db.execute(
        "INSERT INTO routing_rules (site_id, match_kind, match_value, target_kind, target_value, priority, enabled,"
        " created_by, note, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (site_id or None, rule["match_kind"], rule["match_value"], rule["target_kind"], rule["target_value"],
         rule["priority"], int(rule["enabled"]), created_by, rule["note"], int(time.time())))
    return get(int(cur.lastrowid)) or {}


def update(rid: int, data: Any) -> dict:
    """Partial update: the given fields over the stored rule, validated as a whole."""
    old = get(rid)
    if old is None:
        raise RoutingError(404, "not_found", "Kural bulunamadı")
    merged = {**old, **{k: v for k, v in (data or {}).items() if k in (
        "match_kind", "match_value", "target_kind", "target_value", "priority", "enabled", "note")}}
    rule = clean(merged)
    db.execute("UPDATE routing_rules SET match_kind=?, match_value=?, target_kind=?, target_value=?, priority=?, enabled=?,"
               " note=? WHERE id=?", (rule["match_kind"], rule["match_value"], rule["target_kind"], rule["target_value"],
                                      rule["priority"], int(rule["enabled"]), rule["note"], int(rid)))
    return get(rid) or {}


def delete(rid: int) -> dict:
    old = get(rid)
    if old is None:
        raise RoutingError(404, "not_found", "Kural bulunamadı")
    db.execute("DELETE FROM routing_rules WHERE id = ?", (int(rid),))
    return old
