"""LLM fix prompts of A1-A3 (``core.llm.prompts.build``). The run's FIRST call is the read of ``prompts_read`` (purpose +
page map, T23); these prompts fix rules that did not pass: the stage's purpose + the page map + the clipped sample HTML +
the rules measured so far + the measurement + hint; the answer is only the ``pages.<type>`` yaml fragment. Repair runs (and
the older discover-first order) start here. Round 2 in the same session = only the new measurement (+ a new hint)."""
from __future__ import annotations

import json
from typing import Any, Optional

from ...core.extract import htmlclip
from ...core.llm import prompts
from . import brief as site_brief

MAX_PROMPT = 30_000          # characters of a full prompt
HTML_CLIP = 9_000            # characters of one clipped sample page
EVIDENCE_CLIP = 4_000
PAGE_MAP_CLIP = 4_000        # characters of the page map in a fix prompt

SHAPES = {
    "home": """pages:
  home:
    url: /
    sections:
      - {id: s1, title: "<row heading>", row_selector: "<css of ONE card in this row only>", target: title|episode,
         fields: {url: {selector: "a", attr: href}, title: {selector: "..."}, poster: {selector: "img", attr: data-src}}}
    nav: {links: [{name: "<menu text>", url: "/path/", kind: genre|category|type|archive}]}""",
    "listing": """pages:
  listing:
    url_pattern: "^/path-shape/[^/]+/?$"
    row_selector: "<css of one row>"
    fields: {url: {selector: "a", attr: href}, title: {selector: "..."}, poster: {selector: "img", attr: src}, year: {...}}
    pagination: {kind: next_link, selector: "a.next"}   # or {kind: page_param, template: "?page={n}"} or {kind: none}
    listings: [{name: "...", url: "/path/", kind: genre}]   # + pagination: {...} when THIS list differs from the default
    blocks: [{url: "/path/", index: 1, heading: "...", type: series|movie|mixed, pagination: {kind: none}}]   # per-block pager""",
    "title": """pages:
  title:
    url_pattern: "^/(?:a|b)/[^/]+/?$"   # or url_patterns: ["^/a/[^/]+/?$", "^/izle/[^/]+$"] (several shapes)
    key: {regex: "^/(?:a|b)/([^/]+)", template: "{1}"}
    type: {from: url, map: {<path segment>: series, <path segment>: movie}, default: series}
    fields: {title: {selector: "h1"}, poster: {selector: "meta[property='og:image']", attr: content}, year: {...},
             genres: {...}, overview: {...}}""",
}

RULES = [
    "Answer for THIS site only; never use knowledge about other sites, host names or site-specific tricks.",
    "Change only pages.{page_type}; do not write meta, gate or any other page type.",
    "Every CSS selector must match the evidence HTML; prefer short, stable class selectors.",
    "Field rule keys: selector, attr, regex, split, index, cast (int|float|date_tr), replace, fallback, all, multi, self.",
    "Regexes are written with a single backslash (\\d, not \\\\d) in SINGLE quotes: regex: '^/(.+?)-(\\d+)-bolum' "
    "(inside double quotes \\d is a YAML error).",
    "Field rule grammar: {selector: \"<css>\", attr: <attribute, omit for text>, regex: \"(group)\", fallback: [<field rule>, ...]}. "
    "fallback is a non-empty LIST of field rules tried in order after the rule's own selector, e.g. "
    "poster: {selector: \".a img\", attr: data-src, fallback: [{selector: \"#b img\", attr: src}]}. "
    "Never write fallback: [] or fallback: null; leave it out instead.",
    "If you are not sure about a field, leave it out (an empty field is better than a wrong one).",
    "URL'ler standart olmayabilir; sayfa türünü bağlantının nerede bulunduğundan ve sayfa içeriğinden belirle; url kalıbı "
    "yalnız ipucu (several address shapes -> url_patterns: [..]).",
]


def _rules(page_type: str) -> list[str]:
    return [r.replace("{page_type}", page_type) for r in RULES]


def _measured(last: Any) -> str:
    if last is None:
        return "(not measured yet)"
    checks = [c for c in last.checks]
    lines = [f"- {c['name']}: {c.get('value')} (needs {'>=' if c.get('op') == 'min' else '<='} {c.get('bound')})"
             f" {'OK' if c.get('ok') else 'FAILED'}" for c in checks]
    lines += [f"- note: {n}" for n in last.notes[:6]]
    return "\n".join(lines) or "(no checks)"


def _finding(finding: Any) -> str:
    ev = dict(finding.evidence or {})
    body = {"discovery_patch": finding.patch or {}, "evidence": ev}
    return json.dumps(body, ensure_ascii=False, indent=1, default=str)[:EVIDENCE_CLIP]


def purpose_of(stage) -> str:
    from .prompts_read import PURPOSE
    return f"PURPOSE ({stage.id} {stage.title}): {PURPOSE.get(stage.id, stage.title)}"


def page_map(pages: list) -> dict[str, str]:
    """The page map of the first sample page (``core.extract.pagemap``), as one evidence entry."""
    from ...core.extract import pagemap
    if not pages:
        return {}
    page = pages[0]
    url = getattr(page, "final_url", None) or page.url
    return {f"Page map of {url}": pagemap.render(pagemap.build(page.html, url), PAGE_MAP_CLIP)}


def full(stage, ctx, finding, hint: str, last, pages: list) -> str:
    """Fix prompt: rules that did not pass (also the fallback when the session was lost)."""
    page_type = stage.page_type
    evidence: dict[str, str] = {"Site brief (server memory)": site_brief.brief(ctx.site_id)}
    evidence.update(page_map(pages))
    evidence["Rules measured so far (discovery / your earlier answer) and what was found / missed"] = _finding(finding)
    evidence["Measurement of the current proposal"] = _measured(last)
    if hint:
        evidence["Admin hint"] = hint
    tokens = htmlclip.selector_tokens([s for s in _selectors(finding.patch)])
    for i, page in enumerate(pages[:2], 1):
        evidence[f"Sample page {i}: {page.url}"] = htmlclip.clean_html(page.html, HTML_CLIP, tokens)
    task = (f"{purpose_of(stage)}\nWrite the '{page_type}' part of a site map for {ctx.base_url}. The rules measured so far "
            f"did not pass the measurement below; fix them so that every FAILED check passes.")
    answer = "Only one ```yaml block, shaped like:\n" + SHAPES.get(page_type, f"pages:\n  {page_type}: ...")
    text = prompts.build(task, _rules(page_type), evidence, answer, evidence_limit=HTML_CLIP)
    if len(text) > MAX_PROMPT:   # drop the second page, then clip harder
        evidence.pop(next((k for k in evidence if k.startswith("Sample page 2")), ""), None)
        text = prompts.build(task, _rules(page_type), evidence, answer, evidence_limit=HTML_CLIP // 2)
    return text[:MAX_PROMPT]


def follow_up(stage, last, hint: str = "") -> str:
    """Round-2 prompt inside the same LLM session: the measurement of the previous answer (+ a new hint), no HTML."""
    parts = [f"Your previous '{stage.page_type}' yaml was applied and measured:", _measured(last)]
    if hint:
        parts += ["", "Admin hint: " + hint]
    parts += ["", f"Fix it. Answer again with only one ```yaml block (pages.{stage.page_type} only)."]
    return "\n".join(parts)


FIELD_GRAMMAR = ("Field rule: {selector: \"<css>\", attr: <name>, regex: '...', fallback: [<field rule>, ...]} "
                 "(fallback = a NON-EMPTY list, e.g. poster: {selector: \".a img\", attr: data-src, "
                 "fallback: [{selector: \"#b img\", attr: src}]}). Write regex values in SINGLE quotes: "
                 "regex: '^/(.+?)-(\\d+)-bolum'.")


def shape_of(page_type: str) -> str:
    from . import prompts_a4_a7
    return SHAPES.get(page_type) or prompts_a4_a7.SHAPES.get(page_type) or f"pages:\n  {page_type}: ..."


def format_fix(stage, error: str, types: Optional[list] = None) -> str:
    """T18: the one format-fix request of a round (same session): the error + the expected shape, nothing else. A player
    answer whose resolver items failed gets the EXACT parameter schema + one valid example of each resolver type involved
    (``types`` = the types of the reply, plus the ones the error names), generated from the validator."""
    from .prompts_read import error_text
    parts = [f"Your last answer could not be used:\n{error_text(error)}",
             f"Answer again with ONLY one ```yaml block (no prose), changing only pages.{stage.page_type}, shaped like:",
             shape_of(stage.page_type), FIELD_GRAMMAR]
    if stage.page_type == "player":   # the same schema block as the first read (one source)
        from ...core.play import resolvers as play_resolvers
        kinds = [t for t in (types or []) if t in play_resolvers.TYPES] + play_resolvers.types_in(error)
        parts = [p for p in parts if p != FIELD_GRAMMAR] + [play_resolvers.SCHEMA_TITLE + ":", play_resolvers.prompt_block(kinds)]
    return "\n".join(parts)


def _selectors(patch: Optional[dict]) -> list[str]:
    out: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, sub in value.items():
                if key in ("row_selector", "selector") and isinstance(sub, str):
                    out.append(sub)
                else:
                    walk(sub)
        elif isinstance(value, list):
            for sub in value:
                walk(sub)

    walk(patch or {})
    return out
