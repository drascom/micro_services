"""Deterministic parsing: apply yaml-defined CSS selectors with selectolax.

No LLM here. A field spec is a small, LLM-regeneratable dict:

    field_name:
      selector: "div.card-title h2"   # required, CSS
      attr: "src"                     # optional; default = text
      all: true                       # optional; collect every match -> list
      regex: "\\b(19|20)\\d{2}\\b"    # optional; first match / group(1)
      split: "•"                      # optional; split the string
      index: -1                       # optional; pick a segment after split
      then_split: ","                 # optional; second split -> list
      multi: true                     # optional; keep the split result as a list
      cast: int|float|date_tr         # optional; numeric cast (',' -> '.') / Turkish date -> ISO (YYYY-MM-DD)
      replace: {",": "."}             # optional; literal replacements

The engine is intentionally generic so the same code serves any site/schema.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from selectolax.parser import HTMLParser, Node


def _raw_value(node: Node, spec: dict) -> Optional[str]:
    attr = spec.get("attr")
    if attr:
        return node.attributes.get(attr)
    return node.text(strip=True)


def _post(value: Optional[str], spec: dict) -> Any:
    if value is None:
        return None
    value = re.sub(r"\s+", " ", value).strip()

    for a, b in (spec.get("replace") or {}).items():
        value = value.replace(a, b)

    if spec.get("regex"):
        m = re.search(spec["regex"], value)
        if not m:
            return None
        value = m.group(1) if m.groups() else m.group(0)

    if spec.get("split"):
        parts = [p.strip() for p in value.split(spec["split"])]
        if spec.get("index") is not None:
            try:
                value = parts[spec["index"]]
            except IndexError:
                return None
        elif spec.get("multi"):
            return _finish_list(parts, spec)
        else:
            value = parts[0]

    if spec.get("then_split"):
        parts = [p.strip() for p in value.split(spec["then_split"]) if p.strip()]
        return _finish_list(parts, spec)

    return _cast(value, spec)


def _finish_list(parts: list[str], spec: dict) -> list:
    return [_cast(p, spec) for p in parts if p]


_TR_MONTHS = {"oca": 1, "sub": 2, "mar": 3, "nis": 4, "may": 5, "haz": 6,
              "tem": 7, "agu": 8, "eyl": 9, "eki": 10, "kas": 11, "ara": 12}
_TR_FOLD = str.maketrans({"ı": "i", "İ": "i", "I": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
                          "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c"})


def turkish_date(text: Optional[str]) -> Optional[str]:
    """"24 Temmuz 2026" / "3 Şub 2025" / "24.07.2026" / "2026-07-24" -> ISO ``YYYY-MM-DD`` (None when it is
    not a real calendar date). Month names are matched on their first three letters, case- and
    diacritic-insensitively (Ocak..Aralık, also the usual abbreviations)."""
    import datetime
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if not value:
        return None
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", value)
    if m:
        y, mo, d = (int(g) for g in m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", value)
        if m:
            d, mo, y = (int(g) for g in m.groups())
        else:
            m = re.fullmatch(r"(\d{1,2})\.?\s+([^\W\d_]+)\.?,?\s+(\d{4})", value)
            if not m:
                return None
            d, y = int(m.group(1)), int(m.group(3))
            mo = _TR_MONTHS.get(m.group(2).translate(_TR_FOLD).lower()[:3])
            if not mo:
                return None
    try:
        return datetime.date(y, mo, d).isoformat()
    except ValueError:
        return None


def _cast(value: str, spec: dict) -> Any:
    cast = spec.get("cast")
    if cast == "date_tr":
        return turkish_date(value)
    if cast == "int":
        try:
            return int(re.sub(r"[^\d-]", "", value))
        except ValueError:
            return None
    if cast == "float":
        try:
            return float(value.replace(",", "."))
        except ValueError:
            return None
    return value


def apply_field(node: Node, spec: dict) -> Any:
    # Ordered alternatives support mixed homepage cards and lazy image attrs.
    if "fallback" in spec:
        for alternative in spec["fallback"]:
            value = apply_field(node, alternative)
            if value not in (None, "", []):
                return value
        return None
    if spec.get("self"):
        return _post(_raw_value(node, spec), spec)
    sel = spec.get("selector")
    if not sel:
        return None
    if spec.get("all"):
        return [
            v
            for v in (_post(_raw_value(n, spec), spec) for n in node.css(sel))
            if v not in (None, "")
        ]
    found = node.css_first(sel)
    if found is None:
        return None
    return _post(_raw_value(found, spec), spec)


def parse_list(html: str, row_selector: str, fields: dict[str, dict]) -> list[dict]:
    """Parse a list page into raw item dicts (one per ``row_selector`` match)."""
    tree = HTMLParser(html)
    items: list[dict] = []
    for row in tree.css(row_selector):
        item = {name: apply_field(row, spec) for name, spec in fields.items()}
        items.append(item)
    return items


def parse_detail(html: str, fields: dict[str, dict]) -> dict:
    """Parse a detail page (whole document is the context node)."""
    tree = HTMLParser(html)
    root = tree.root  # whole document, so <head> meta selectors resolve too
    return {name: apply_field(root, spec) for name, spec in fields.items()}
