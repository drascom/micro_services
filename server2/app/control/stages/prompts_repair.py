"""Repair-mode addition to a stage's round-1 prompt (T14 item 5): the broken sample's clipped HTML, what the CURRENT rule
returns on the broken and on the good samples, and the rule "fix only the broken rule". No site name beyond the stage
prompt's own evidence, no secrets (the problem evidence is reduced to short measured values)."""
from __future__ import annotations

import json
from typing import Any, Callable

from ...core.extract import htmlclip

HTML_CLIP = 6_000
EVIDENCE_CLIP = 1_500
RULES = [
    "REPAIR MODE: the map worked before; something on the site changed. Fix ONLY the rule that breaks on the broken samples.",
    "Do not change fields / rules that still work: the good samples must keep passing (a regression is refused).",
    "The answer is measured again on the broken AND the good samples; it is applied only when all of them pass.",
]


def _sample_lines(measurement: Any, urls: list) -> str:
    seen = {s.get("url"): s for s in getattr(measurement, "samples", None) or [] if isinstance(s, dict)}
    lines = []
    for url in urls:
        s = seen.get(url)
        lines.append(f"{url}: " + ("not measured" if s is None else f"{'ok' if s.get('ok') else 'FAILS'} ({s.get('note') or '-'})"))
    return "\n".join(lines) or "-"


def section(stage, ctx, spec: dict, measure: Callable[[dict], Any]) -> str:
    """The ``## REPAIR`` block (rules + evidence)."""
    broken, good = list(spec.get("broken") or []), list(spec.get("good") or [])
    current = {"pages": {stage.page_type: ctx.page(stage.page_type)}}
    try:
        old = measure(current) if current["pages"][stage.page_type] else None
    except Exception:   # the old rule's measurement is evidence only
        old = None
    ev = {k: v for k, v in (spec.get("evidence") or {}).items() if k in ("class", "stage", "trace", "error", "reasons", "pages",
                                                                          "candidates", "note", "counts")}
    parts = ["## REPAIR", *[f"- {r}" for r in RULES],
             f"### Problem: {spec.get('class') or '-'}", "'''", json.dumps(ev, ensure_ascii=False, default=str)[:EVIDENCE_CLIP], "'''",
             "### Current rule on the broken samples", "'''", _sample_lines(old, broken), "'''",
             "### Current rule on the good samples (must stay ok)", "'''", _sample_lines(old, good), "'''"]
    if broken:
        page = ctx.get(broken[0], stage.page_type)
        if page is not None:
            parts += [f"### Broken sample page: {broken[0]}", "'''", htmlclip.clean_html(page.html, HTML_CLIP), "'''"]
    return "\n".join(parts) + "\n"


def add(text: str, stage, ctx, spec: dict, measure: Callable[[dict], Any]) -> str:
    """``text`` with the repair block placed before its answer format."""
    block = section(stage, ctx, spec, measure)
    marker = "\n## ANSWER FORMAT"
    if marker in text:
        return text.replace(marker, "\n" + block + marker, 1)
    return text + "\n" + block
