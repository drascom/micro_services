"""Page fingerprint (pure): how a page looks to its extraction rule, independent of its content.

``make(html, rule)`` -> ``{rows, field_fill: {field: ratio}, skeleton}``; ``rule`` = ``{row_selector?, fields}`` (no
``row_selector`` = a detail page: ``rows`` is 1). ``skeleton`` is a short, stable digest of the tag / class path of the row
node (ancestors + the row itself, classes shared by every row) or, for a detail page, of each field's first matched node.
``compare(reference, current, *, tolerance)`` -> ``{ok, reasons[]}``."""
from __future__ import annotations

import hashlib
from typing import Any, Optional

from selectolax.parser import HTMLParser

from . import parse
from ._select import stable
from ._util import filled

ANCESTORS = 3          # levels above the row node that go into the skeleton
SAMPLE_ROWS = 10
REQUIRED_FILL = 0.8    # a field is "required" when the reference fills at least this share of rows


def _label(node, classes: Optional[set] = None) -> str:
    own = [c for c in (node.attributes.get("class") or "").split() if stable(c)]
    if classes is not None:
        own = [c for c in own if c in classes]
    return node.tag + "".join("." + c for c in sorted(own))


def _path(node, classes: Optional[set] = None) -> str:
    parts = [_label(node, classes)]
    parent, depth = node.parent, 0
    while parent is not None and depth < ANCESTORS and parent.tag not in ("html", "body", "-undef"):
        parts.append(_label(parent))
        parent, depth = parent.parent, depth + 1
    return " < ".join(parts)


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def make(html: str, rule: dict) -> dict:
    tree = HTMLParser(html or "")
    fields = (rule or {}).get("fields") or {}
    row_selector = (rule or {}).get("row_selector")
    if row_selector:
        try:
            rows = tree.css(row_selector)
        except Exception:
            rows = []
        values = [{name: parse.apply_field(row, spec) for name, spec in fields.items()} for row in rows]
        n = len(rows)
        fill = {name: (round(sum(1 for v in values if filled(v.get(name))) / n, 3) if n else 0.0) for name in fields}
        if rows:
            shared = set.intersection(*[set((r.attributes.get("class") or "").split()) for r in rows[:SAMPLE_ROWS]])
            text = _path(rows[0], shared)
        else:
            text = "-"
        return {"rows": n, "field_fill": fill, "skeleton": f"{_digest(text)} {text[:120]}"}
    root = tree.root
    fill, paths = {}, []
    for name, spec in fields.items():
        fill[name] = 1.0 if filled(parse.apply_field(root, spec)) else 0.0
        node = None
        selector = spec.get("selector") if isinstance(spec, dict) else None
        if selector and "script" not in selector and "meta" not in selector:
            try:
                node = tree.css_first(selector)
            except Exception:
                node = None
        paths.append(f"{name}={_path(node) if node is not None else '-'}")
    text = "; ".join(sorted(paths))
    return {"rows": 1, "field_fill": fill, "skeleton": f"{_digest(text)} {text[:120]}"}


def compare(reference: dict, current: dict, *, tolerance: float = 0.2, required: Optional[list] = None) -> dict:
    """``ok`` unless: the row count fell below ``reference.rows * (1 - tolerance)``, a required field's fill fell by more than
    ``tolerance`` (required = ``required`` or the fields the reference fills >= REQUIRED_FILL), or the skeleton changed."""
    reasons: list[str] = []
    ref_rows, cur_rows = int(reference.get("rows") or 0), int(current.get("rows") or 0)
    floor = ref_rows * (1 - tolerance)
    if cur_rows == 0 or cur_rows < floor:
        reasons.append(f"rows {cur_rows} < {round(floor, 1)} (reference {ref_rows})")
    ref_fill: dict[str, Any] = reference.get("field_fill") or {}
    cur_fill: dict[str, Any] = current.get("field_fill") or {}
    names = required if required is not None else [f for f, v in ref_fill.items() if float(v) >= REQUIRED_FILL]
    for name in names:
        before, after = float(ref_fill.get(name, 0.0)), float(cur_fill.get(name, 0.0))
        if after < before - tolerance:
            reasons.append(f"field {name!r} fill {after} < {before} - {tolerance}")
    if (reference.get("skeleton") or "").split(" ", 1)[0] != (current.get("skeleton") or "").split(" ", 1)[0]:
        reasons.append(f"skeleton changed: {reference.get('skeleton')!r} -> {current.get('skeleton')!r}")
    return {"ok": not reasons, "reasons": reasons}
