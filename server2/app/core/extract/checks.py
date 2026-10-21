"""Pure measurement helpers of the stage ``verify`` steps: a criterion, field fill ratios and the diagnostics of a list /
detail parse (why rows / fields came out empty and what else sits there). Diagnostics are RETURNED (a list of small
``{where, problem, ...}`` entries), never stored."""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from selectolax.parser import HTMLParser

from . import parse
from ._util import SAFE_CLASS, SKIP_TAGS, clip, filled

DIAG_ENTRY_BYTES = 1000       # one entry (JSON bytes)
DIAG_ROWS = 2                 # rows whose extracted values are shown
_IMG_ATTRS = ("src", "data-src", "data-lazy-src", "data-original", "data-lazy", "srcset", "data-srcset", "poster", "alt")
_LABELS = {"year": "Yapım Yılı|Yıl|Year", "director": "Yönetmen|Director", "cast": "Oyuncular|Oyuncu|Cast", "runtime": "Süre|Duration|Runtime",
           "country": "Ülke|Country", "genres": "Tür|Genre", "overview": "Özet|Konu|Synopsis", "rating": "IMDb|Puan|Rating",
           "original_title": "Orijinal|Original"}


def criterion(value: float, bound: float, kind: str) -> dict:
    """``{value, min|max: bound, ok}``; ``kind`` is ``min`` (value >= bound) or ``max`` (value <= bound)."""
    ok = value >= bound if kind == "min" else value <= bound
    return {"value": value, kind: bound, "ok": ok}


def ratio(num: float, den: float) -> float:
    return round(num / den, 3) if den else 0.0


def field_fill(rows: list[dict], names: Any) -> dict[str, float]:
    """Fill ratio (0..1) per field over parsed rows."""
    n = len(rows)
    return {f: (round(sum(1 for it in rows if filled(it.get(f))) / n, 3) if n else 0.0) for f in names}


def _shrink(value: Any, width: int) -> Any:
    if isinstance(value, str):
        value = " ".join(value.split())
        return value if len(value) <= width else value[:max(1, width - 1)] + "…"
    if isinstance(value, (list, tuple)):
        return [_shrink(v, width) for v in value[:4]]
    if isinstance(value, dict):
        return {str(k): _shrink(v, width) for k, v in list(value.items())[:12]}
    return value


def cap_entry(entry: Any, limit: int = DIAG_ENTRY_BYTES) -> Any:
    """``entry`` shrunk until its JSON fits ``limit`` bytes."""
    for width in (160, 100, 60, 30, 15):
        shrunk = _shrink(entry, width)
        if len(json.dumps(shrunk, ensure_ascii=False)) <= limit:
            return shrunk
    return _shrink(entry, 8)


def handle(node) -> str:
    """``tag.class1.class2`` of a node."""
    if node is None:
        return ""
    classes = [c for c in (node.attributes.get("class") or "").split() if SAFE_CLASS.match(c)][:2]
    return node.tag + "".join("." + c for c in classes)


def _row_hints(row, limit: int = 8) -> list[str]:
    out: list[str] = []
    for node in row.css("*")[:80]:
        if node.tag in SKIP_TAGS or node.tag == "-text":
            continue
        own = " ".join((node.text(deep=False, strip=True) or "").split())[:30]
        extra = ""
        for attr in ("href", "src", "data-src"):
            value = node.attributes.get(attr)
            if value:
                extra = f" {attr}={clip(value, 50)}"
                break
        if not own and not extra:
            continue
        hint = handle(node) + extra + (f" '{own}'" if own else "")
        if hint not in out:
            out.append(hint)
        if len(out) >= limit:
            break
    return out


def _field_state(node, spec: Any) -> tuple[bool, Optional[str]]:
    """``(the selector matched an element, the raw text / attribute it read)`` (first fallback alternative that reads something)."""
    if not isinstance(spec, dict):
        return False, None
    if "fallback" in spec and isinstance(spec["fallback"], list) and spec["fallback"]:
        for alternative in spec["fallback"]:
            matched, raw = _field_state(node, alternative)
            if raw not in (None, ""):
                return matched, raw
        return _field_state(node, spec["fallback"][0])
    if spec.get("self") is True:
        found = node
    else:
        try:
            found = node.css_first(str(spec.get("selector") or ""))
        except Exception:
            found = None
    if found is None:
        return False, None
    attr = spec.get("attr")
    return True, (found.attributes.get(attr) if attr else found.text(strip=True))


def _field_hints(node, spec: dict) -> list[str]:
    attr = str(spec.get("attr") or "")
    selector = str(spec.get("selector") or "")
    if attr in _IMG_ATTRS or "img" in selector:
        imgs = node.css("img")[:3]
        if not imgs:
            return ["bu satırda <img> yok"]
        return [handle(img) + " " + " ".join(f"{k}={clip(v, 60)}" for k, v in img.attributes.items() if k in _IMG_ATTRS and v)
                for img in imgs]
    if attr == "href":
        return [clip(a.attributes.get("href") or "", 80) for a in node.css("a[href]")[:3]] or ["bu satırda a[href] yok"]
    return _row_hints(node, 6)


def _problem(spec: dict, matched: bool, raw: Optional[str], rows: int = 0) -> str:
    if not matched:
        where = f" ilk {rows} satırda" if rows else ""
        return f"selector {spec.get('selector')!r}{where} 0 eleman eşledi"
    if spec.get("attr") and raw in (None, ""):
        return f"selector eşledi ama {spec['attr']!r} özniteliği yok / boş"
    if spec.get("regex") and raw not in (None, ""):
        return f"selector eşledi (ham metin {clip(raw, 60)!r}) ama regex {clip(spec['regex'], 60)!r} uymadı"
    return "selector eşledi ama değer boş"


def rows_diagnostics(html: str, row_selector: str, fields: dict, where: str = "list", fill: Optional[dict] = None,
                     min_fill: Optional[dict] = None) -> list[dict]:
    """Why a list page gave few or poor rows: how many rows matched, what each field extracted from the FIRST rows, and for an
    empty (or below ``min_fill``) field what else sits in those rows."""
    out: list[dict] = []
    try:
        tree = HTMLParser(html or "")
        rows = tree.css(row_selector)
    except Exception as exc:
        return [{"where": f"{where}.row_selector", "problem": f"geçersiz selector {row_selector!r}: {clip(str(exc), 80)}"}]
    if not rows:
        groups: dict[str, int] = {}
        for node in tree.css("div, li, article, tr, a"):
            h = handle(node)
            if "." in h:
                groups[h] = groups.get(h, 0) + 1
        repeating = [f"{h} ×{n}" for h, n in sorted(groups.items(), key=lambda kv: -kv[1]) if n >= 3][:4]
        return [cap_entry({"where": f"{where}.row_selector", "problem": f"selector {row_selector!r} 0 eleman eşledi ({len(html or '')} bayt sayfa)",
                           "alternatives": repeating or ["sayfada tekrar eden kart öğesi bulunamadı (sayfa JS ile mi kuruluyor?)"]})]
    sample = rows[:DIAG_ROWS]
    extracted = {name: [clip(parse.apply_field(row, spec), 80) if isinstance(spec, dict) else None for row in sample]
                 for name, spec in (fields or {}).items()}
    out.append(cap_entry({"where": where, "rows_matched": len(rows), "first_rows": [
        {name: values[i] for name, values in extracted.items() if i < len(values)} for i in range(len(sample))]}))
    bounds = min_fill or {}
    for name, spec in (fields or {}).items():
        values = extracted[name]
        low = fill is not None and name in fill and fill[name] < bounds.get(name, 0.0)
        empty = all(v in (None, "", []) for v in values)
        if not (empty or low) or not isinstance(spec, dict):
            continue
        matched = [_field_state(row, spec) for row in sample]
        hit = next(((m, r) for m, r in matched if m), (False, None))
        problem = _problem(spec, hit[0], hit[1], len(sample))
        if low and not empty:
            problem = f"alan satırların yalnız %{round(fill[name] * 100)}'inde dolu (en az %{round(bounds[name] * 100)}): " + problem
        out.append(cap_entry({"where": f"{where}.fields.{name}", "problem": problem, "extracted": values,
                              "alternatives": _field_hints(sample[0], spec)}))
    return out


def _label_snippet(html: str, name: str, spec: Any) -> str:
    words = _LABELS.get(name, "")
    regex = str((spec or {}).get("regex") or "") if isinstance(spec, dict) else ""
    literal = "|".join(sorted({w for w in re.findall(r"[^\W\d_]{4,}", regex)} - {"digit", "word"}, key=len, reverse=True)[:3])
    pattern = "|".join(x for x in (words, literal) if x)
    if not pattern:
        return ""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    try:
        match = re.search(pattern, text, re.I)
    except re.error:
        return ""
    if match is None:
        return ""
    return " ".join(text[max(0, match.start() - 30):match.end() + 130].split())[:170]


def detail_diagnostics(html: str, fields: dict, parsed: dict, where: str = "detail") -> list[dict]:
    """Empty detail fields: whether the selector found an element, and what the page offers instead (og: meta, lazy images,
    the HTML around the field's label)."""
    out: list[dict] = []
    try:
        tree = HTMLParser(html or "")
    except Exception:
        return out
    root = tree.root
    for name, spec in (fields or {}).items():
        if filled(parsed.get(name)) or not isinstance(spec, dict):
            continue
        matched, raw = _field_state(root, spec)
        problem = _problem(spec, matched, raw)
        alternatives: list[str] = []
        selector = str(spec.get("selector") or "")
        metas = [f"{m.attributes.get('property') or m.attributes.get('name')}={clip(m.attributes.get('content') or '', 40)}"
                 for m in tree.css("meta[property], meta[name]")
                 if str(m.attributes.get("property") or m.attributes.get("name") or "").startswith(("og:", "twitter:"))][:6]
        if "meta" in selector or spec.get("attr") == "content":
            alternatives += metas or ["sayfada og:/twitter: meta etiketi yok"]
        if name in ("poster", "backdrop", "poster_url", "backdrop_url", "image") or spec.get("attr") in _IMG_ATTRS:
            alternatives += [f"{handle(i)} " + " ".join(f"{k}={clip(v, 50)}" for k, v in i.attributes.items() if k in _IMG_ATTRS and v)
                             for i in tree.css("img[data-src], img[data-lazy-src]")[:2]] + [m for m in metas if "image" in m][:1]
        snippet = _label_snippet(html, name, spec)
        if snippet:
            alternatives.append("etiket çevresindeki HTML: " + snippet)
        entry: dict[str, Any] = {"where": f"{where}.fields.{name}", "problem": problem}
        if alternatives:
            entry["alternatives"] = alternatives
        out.append(cap_entry(entry))
    return out
