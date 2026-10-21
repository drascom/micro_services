"""T30 item 7: the A1 / A2 review card's "Ana ekranda" column (home screen destination of a section / listing / block).

A choice value is one of:
  ``slider`` (role ``featured``) | ``role:<role>`` (a fixed home row) | ``cat:<slug>`` (a dynamic category) |
  ``new:<name>`` (a category created on confirm) | ``yok`` (no rule).
On confirm, ``apply`` creates the missing categories (dynamic, once per name: several rows may go to the same one),
writes one routing rule per row (``match`` = the row's origin, ``target`` = the category, ``created_by=admin``) and queues
the routing job. Proposals come from the LLM's reading (``home_target`` free text) through ``propose``."""
from __future__ import annotations

from typing import Any, Optional

from ... import db
from ...core.catalog import categories
from ...core.catalog.textnorm import fold
from . import rules as rule_store

NONE = "yok"
SLIDER = "slider"
#: the fixed home rows a routing rule can fill (role -> label)
FIXED = {"trending": "Haftanın Trendleri (dizi / film)", "noteworthy_movies": "Dikkate Değer Filmler",
         "latest_episodes": "Son Bölümler", "latest_series": "Tüm Diziler (son eklenenler)",
         "latest_movies": "Tüm Filmler (son eklenenler)"}
MAX_NAME = 60


def options() -> list[dict]:
    """Dropdown entries ``[{value, label}]``: fixed rows, dynamic categories, slider, none (+ the client's "yeni kategori…")."""
    out = [{"value": f"role:{r}", "label": label} for r, label in FIXED.items()]
    try:
        cats = [c for c in categories.list_all(include_disabled=True) if c.get("kind", "category") == "category"]
    except Exception:   # noqa: BLE001 - a card without the dynamic categories still works
        cats = []
    out += [{"value": f"cat:{c['slug']}", "label": c.get("title") or c["slug"]} for c in cats]
    out += [{"value": SLIDER, "label": "Slider"}, {"value": NONE, "label": "Yok"}]
    return out


def clean(value: Any) -> str:
    """A valid choice value or ``ValueError``."""
    text = " ".join(str(value or "").split())
    if text in ("", NONE):
        return NONE
    if text == SLIDER:
        return SLIDER
    kind, _, rest = text.partition(":")
    if kind == "role" and rest in FIXED:
        return text
    if kind == "cat" and categories.valid_slug(rest):
        return text
    if kind == "new" and rest.strip() and categories.slugify(rest):
        return f"new:{rest.strip()[:MAX_NAME]}"
    raise ValueError(f"geçersiz ana ekran hedefi: {text!r} (slider | yok | role:<rol> | cat:<kısa ad> | new:<ad>)")


import re as _re

#: T31: what a free-text destination MEANS (folded: lower case, ASCII, no spaces) -> an existing fixed row. The fixed rows
#: ``series`` / ``movies`` (every title of the type) are fed by the ``latest_series`` / ``latest_movies`` lists, the
#: ``trending_series`` / ``trending_movies`` rows by ``trending`` (split by the title's type).
_MEANING = (
    (_re.compile(r"^(?:slider|manset|hero|featured|vitrin|onecikan(?:lar)?)$"), SLIDER),
    (_re.compile(r"^(?:haftanin)?(?:trend|trendler|trendleri|trending|populer|populerler|encokizlenen(?:ler)?)"
                 r"(?:dizi|diziler|film|filmler|series|movies)?$"), "role:trending"),
    (_re.compile(r"^(?:dikkatedeger|noteworthy)(?:film|filmler|movies)?$"), "role:noteworthy_movies"),
    (_re.compile(r"^(?:son)?(?:bolum|bolumler|episodes|latestepisodes)$"), "role:latest_episodes"),
    (_re.compile(r"^(?:tum|all|son|yeni|latest)?(?:dizi|diziler|dizileri|series)$"), "role:latest_series"),
    (_re.compile(r"^(?:tum|all|son|yeni|latest)?(?:film|filmler|filmleri|movies)$"), "role:latest_movies"),
)
#: the fixed home rows (``home_categories`` system slugs) -> the routing value that fills them
_SYSTEM_ROWS = {"trending_series": "role:trending", "trending_movies": "role:trending", "series": "role:latest_series",
                "movies": "role:latest_movies", "noteworthy_movies": "role:noteworthy_movies"}
#: filler words around a destination ("... bölümüne", "... satırı", "kategorisi")
_FILLER = _re.compile(r"(?:bolumune|bolumu|bolumunde|satirina|satiri|kategorisine|kategorisi|row|section)$")


def _key(text: str) -> str:
    key = fold(text)
    while True:
        cut = _FILLER.sub("", key)
        if cut == key or not cut:
            return key
        key = cut


def display_name(text: str) -> str:
    """A category's display name: every word capitalised the Turkish way ("diziler özel" -> "Diziler Özel")."""
    def cap(word: str) -> str:
        if not word:
            return word
        first = {"i": "İ", "ı": "I"}.get(word[0], word[0].upper())
        return first + word[1:]
    return " ".join(cap(w) for w in str(text or "").split())[:MAX_NAME]


def propose(text: Any) -> Optional[str]:
    """The LLM's free-text ``home_target`` -> a choice value. Existing rows win: a fixed row / category by its name, then
    by meaning (T31: "trend" -> the trending rows, "diziler"/"tüm diziler" -> the series row, "filmler" -> the movies
    row, "dikkate değer filmler" -> the noteworthy row, "slider"), then a dynamic category by name; only a destination
    that matches none of them proposes a NEW category (display name capitalised)."""
    raw = " ".join(str(text or "").split())
    if not raw:
        return None
    key = _key(raw)
    if not key or key in (fold(NONE), "none", "no", "hayir", "ignore", "yoksay"):
        return NONE
    for slug, title in categories.SYSTEM_TITLES.items():   # a fixed row by its slug / title ("Tüm Diziler")
        if slug in _SYSTEM_ROWS and key in (fold(slug), fold(title)):
            return _SYSTEM_ROWS[slug]
    for value in options():
        if _key(value["label"]) == key or fold(value["value"].split(":", 1)[-1]) == key:
            return value["value"]
    for rx, value in _MEANING:
        if rx.match(key):
            return value
    return f"new:{display_name(raw)}"


def _target(value: str, made: dict) -> Optional[tuple[str, str]]:
    """``(target_kind, target_value)`` of a choice; ``new:`` categories are created once per name (``made``)."""
    if value == NONE:
        return None
    if value == SLIDER:
        return "role", "featured"
    kind, _, rest = value.partition(":")
    if kind == "role":
        return "role", rest
    if kind == "cat":
        return "category", rest
    name = rest.strip()
    slug = made.get(fold(name)) or rule_store._category_by_title(name)
    if slug is None:
        slug = categories.slugify(name)
        if not categories.exists(slug):
            categories.create(display_name(name), slug)
    made[fold(name)] = slug
    return "category", slug


def apply(site_id: str, rows: list[dict]) -> dict:
    """``rows`` = ``[{match_kind, match_value, home}]`` -> categories + routing rules + the routing job.
    Returns ``{rules: [...ids], categories: [...slugs created], skipped: n}``."""
    made: dict = {}
    before = {c["slug"] for c in categories.list_all(include_disabled=True)}
    ids, skipped = [], 0
    for row in rows or []:
        target = _target(clean(row.get("home")), made)
        if target is None or not row.get("match_value"):
            skipped += 1
            continue
        same = db.query_one("SELECT id FROM routing_rules WHERE site_id=? AND match_kind=? AND match_value=? AND "
                            "target_kind=? AND target_value=?", (site_id, row["match_kind"], row["match_value"], *target))
        if same is not None:
            ids.append(int(same["id"]))
            continue
        saved = rule_store.add(site_id, {"match_kind": row["match_kind"], "match_value": row["match_value"],
                                         "target_kind": target[0], "target_value": target[1],
                                         "note": "A1/A2 incelemesi (ana ekranda)"}, created_by="admin")
        ids.append(int(saved["id"]))
    created = sorted({c["slug"] for c in categories.list_all(include_disabled=True)} - before)
    if ids:
        from . import schedule
        schedule(site_id)
    return {"rules": ids, "categories": created, "skipped": skipped}
