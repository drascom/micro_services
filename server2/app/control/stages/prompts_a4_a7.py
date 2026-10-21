"""LLM prompts of A4-A7 (same rules as ``prompts_a1_a3``: ONE page type, the answer is only its yaml fragment, round 2 in
the same session = only the new measurement) and the A6 provider recipe prompt (the answer is a recipe of the provider
LIBRARY: it belongs to the video host and never names the site, its paths or its selectors)."""
from __future__ import annotations

import json
from typing import Any, Optional

from ...core.extract import htmlclip
from ...core.llm import prompts
from ...core.play import resolvers as play_resolvers
from . import brief as site_brief
from .prompts_a1_a3 import EVIDENCE_CLIP, HTML_CLIP, MAX_PROMPT, _finding, _measured, _rules, _selectors, page_map, purpose_of

SHAPES = {
    "episodes": """pages:
  episodes:
    where: title_page            # or season_pages (one page per season, reached through a season menu)
    row_selector: "<css of ONE episode row>"
    fields: {url: {selector: "a", attr: href}, title: {selector: "..."}, air_date: {selector: "...", cast: date_tr}}
    numbering: {from: [url, text, position], regex: '^/(.+?)-(\\d+)-sezon-(\\d+)-bolum', season: 2, episode: 3, default_season: 1}
    seasons: {menu_selector: "<css of the season links>", url_regex: 'season-(\\d+)/?$'}   # only for season_pages""",
    "player": """pages:
  player:
    url_pattern: "^/<episode or film page address shape>"
    title_link: {selector: "<css of the link to the series page>", attr: href}
    resolvers:
      - {type: iframe, selector: "<css of the element carrying the player URL>", attr: src}""",
    "search": """pages:
  search:
    url: "/?s={query}"
    method: GET
    row_selector: "<css of ONE result row>"
    fields: {url: {selector: "a", attr: href}, title: {selector: "..."}, poster: {selector: "img", attr: src}}
gate:
  require: player
  probe: 2
  blocked: [{on: episode_page, selector: "<css only a not-public placeholder page has>", reason: "telif"}]""",
}

RECIPE_SHAPE = """```yaml
name: <host_based_name>            # ^[a-z][a-z0-9_]{1,31}$, named after the VIDEO HOST, never after the site
description: "<1-2 sentences: which player this recipe reads>"
version: 1
match: {host_regex: '(^|\\.)player\\.example$'}   # the player's host only
fetch: http
referer: "{page_url}"
extract:
  - {regex: 'file:\\s*"([^"]+\\.m3u8[^"]*)"', type: hls}
```"""

RECIPE_RULES = [
    "The recipe belongs to the VIDEO HOST (provider library), not to the site: no site name, site path, site selector or "
    "site address anywhere in it; it must work for every site that embeds this player.",
    "match.host_regex matches the player's hostname only (anchored to its domain).",
    "extract rules: exactly one of regex / css / json_path each; a regex is written with single backslashes.",
    "Use follow hops only for nested iframes of the player itself; use unpack: true for p.a.c.k.e.r. scripts.",
    "If you cannot see the media URL in the evidence, answer with the most likely rule; never invent a host.",
]


def full(stage, ctx, finding, hint: str, last, pages: list) -> str:
    """Round-1 prompt of A4/A5/A7 (also the fallback when the LLM session was lost)."""
    page_type = stage.page_type
    groups = (finding.evidence or {}).get("link_groups") if page_type == "episodes" else None
    if groups:   # an ambiguous discovery: the groups go in their own block (not clipped away inside the finding)
        finding = type(finding)(finding.patch, {k: v for k, v in (finding.evidence or {}).items() if k != "link_groups"},
                                finding.confidence)
    evidence: dict[str, str] = {"Site brief (server memory)": site_brief.brief(ctx.site_id)}
    if stage.id in ("A4", "A5"):
        evidence.update(page_map(pages))
    evidence["Rules measured so far (discovery / your earlier answer) and what was found / missed"] = _finding(finding)
    evidence["Measurement of the current proposal"] = _measured(last)
    if groups:
        evidence["Candidate link groups on the series page"] = json.dumps(groups, ensure_ascii=False)[:EVIDENCE_CLIP * 2]
    if page_type == "player":   # the same schema block as the first read / format fix (one source)
        evidence = {play_resolvers.SCHEMA_TITLE: play_resolvers.prompt_block(), **evidence}
        from .prompts_read import downstream_text
        down = downstream_text(ctx)
        if down:
            evidence["A6 sonucu (the next stage measured the current rule and it failed there; fix it)"] = down
    if hint:
        evidence["Admin hint"] = hint
    tokens = htmlclip.selector_tokens(_selectors(finding.patch))
    if page_type == "player" and pages:   # the source hunt's evidence bundle (scripts, alternatives so far, player area)
        from .investigate import evidence_bundle
        for name, body in evidence_bundle(pages[0].html, pages[0].url, finding, ctx.base_url).items():
            evidence[name] = body
        pages = pages[1:]
    for i, page in enumerate(pages[:2], 1):
        evidence[f"Sample page {i}: {page.url}"] = htmlclip.clean_html(page.html, HTML_CLIP, tokens)
    task = (f"Write the '{page_type}' part of a site map for {ctx.base_url} ({stage.title}). The rules measured so far did "
            f"not pass the measurement below; fix them so that every FAILED check passes.")
    if stage.id in ("A4", "A5"):
        task = purpose_of(stage) + "\n" + task
    rules = _rules(page_type)
    if groups:
        task = (f"{purpose_of(stage)}\nWrite the '{page_type}' part of a site map for {ctx.base_url} ({stage.title}). The series page has "
                "several candidate link groups (listed below: sample href + text, how many look like THIS series (mine), "
                "like other series (foreign), menu region, links to other title pages). Of these groups, which one is THIS "
                "series' episode list? Exclude the other links (favourite button, A-Z menu, other series); write a "
                "row_selector + url rule that reflects your choice.")
    if stage.extra_keys:
        rules.append(f"You may also write the top-level key(s): {', '.join(stage.extra_keys)}.")
    if page_type == "player":
        rules.append("Resolver items carry only parameters of the listed types (K4): how THIS site points at the video host.")
        rules.append("The page may offer SEVERAL sources (tabs, buttons, a select, AJAX loaders, encoded scripts, nested "
                     "iframes): write one resolver item for EVERY alternative (they are kept as backups), peel every "
                     "wrapping layer (decode steps, ajax parameters from the page, script_regex).")
        rules.append(AJAX_RULE)
    answer = "Only one ```yaml block, shaped like:\n" + SHAPES.get(page_type, f"pages:\n  {page_type}: ...")
    if page_type == "player":
        from .investigate import ANSWER_HELP
        answer = (ANSWER_HELP + "\nFinal answer shape:\nresult:\n  resolvers:\n    - {type: iframe, selector: \"...\"}\n"
                  "    - {type: ajax, selector: \"...\", url: \"{base}/...\", form: {id: \"attr:data-id\"}}\n"
                  "  title_link: {selector: \"...\", attr: href}   # optional\n(or the plain shape:)\n" + SHAPES["player"])
    text = prompts.build(task, rules, evidence, answer, evidence_limit=HTML_CLIP)
    if len(text) > MAX_PROMPT:
        evidence.pop(next((k for k in evidence if k.startswith("Sample page 2")), ""), None)
        text = prompts.build(task, rules, evidence, answer, evidence_limit=HTML_CLIP // 2)
    return text[:MAX_PROMPT]


def recipe(ctx, finding, hint: str, last: Optional[Any], fetch_timeout: float = 0) -> str:
    """The ONE A6 call: a provider recipe for the first host no provider reads."""
    ev = finding.evidence or {}
    need = (ev.get("recipe_needed") or [{}])[0]
    evidence: dict[str, str] = {
        "Player": json.dumps({"host": need.get("host"), "player_url": need.get("player_url")}, ensure_ascii=False),
        "match_providers page summary": json.dumps(need.get("page") or {}, ensure_ascii=False, default=str)[:EVIDENCE_CLIP],
        "Why no provider reads it": str(need.get("note") or "")[:600],
        "Measurement": _measured(last),
    }
    if hint:
        evidence["Admin hint"] = hint
    if need.get("body"):
        evidence["Player page body (clipped)"] = need["body"]
    task = ("No provider of the video library reads the player below. Write ONE new provider recipe (data-driven, no code) "
            "that reads its media URL from the player page.")
    from .investigate import answer_help
    answer = (answer_help(6, probe=True) + "\nFinal answer: the recipe (optionally under result: {recipe: ...}), shaped like:\n"
              + RECIPE_SHAPE)
    text = prompts.build(task, RECIPE_RULES, evidence, answer, evidence_limit=HTML_CLIP)
    return text[:MAX_PROMPT]


HUNT_PURPOSE = ("Bu bölümün oynayan video kaynağını bul; tüm alternatifleri topla; zinciri (sayfa→ara sayfa→sağlayıcı→akış) "
                "kural olarak yaz. (Find the playing video source of this episode; collect ALL alternatives; write the chain "
                "page -> intermediate page -> provider -> stream as rules.) The LLM-free measurement below did not play "
                "(enough): read the full trace, investigate the pages, PROBE the stream addresses you find, then answer.")

AJAX_RULE = ("Kaynak sekmeleri AJAX ile yükleniyor olabilir; JS'teki click handler'ı bul (show_script), isteği kopyala "
             "(post: url, data, xhr başlıkları); 5xx/boş cevap çerez eksikliği olabilir — JS'te document.cookie/cookie set "
             "eden kodu ara, set_cookie ile dene. (Source tabs may load by AJAX: find the click handler, copy the request; a "
             "5xx / empty answer may be a missing cookie the JS sets.) Write it as an ajax resolver: one item per element, "
             "result: iframe_url | html | direct, cookies: [{name, value: now_ms}] for a JS-set cookie, tab_group for outer "
             "language tabs that load their own buttons.")

HUNT_RULES = [
    "Only media is a stream: .m3u8 / .mp4 / .mpd / .webm / .mkv, or a sources[] entry with type hls|mp4; a .js / .css / "
    "image / font / json address is never a stream.",
    "Probe a stream address before you answer with it; a rule whose stream did not play is not an answer.",
    "Answer with player resolvers (the site's chain: which element / script / AJAX call of the episode page leads to the "
    "intermediate page and where the media URL is in it, e.g. player_page with extract rules), or with a provider recipe "
    "when the stream sits on a FOREIGN video host no provider reads (the recipe never names the site).",
    "Your resolvers are measured in front of the existing ones on every sample: the alternatives that play win the order.",
    AJAX_RULE,
]

HUNT_ANSWER = """Final answer (one ```yaml block), either the site's chain:
result:
  resolvers:
    - {type: player_page, selector: "iframe[src*=\"/player/\"]", extract: [{regex: 'file:\\s*"([^"]+\\.m3u8[^"]*)"', type: hls}]}
or a provider recipe for a foreign video host:
result:
  recipe: {...}   # shaped like:
""" + RECIPE_SHAPE


def source_hunt(ctx, finding, hint: str, last: Optional[Any], a5_pages: Optional[list] = None) -> str:
    """The A6 LLM investigation (0 plays or < 2/3 without a recipe need): the FULL trace of the LLM-free measurement, the
    alternatives, the A5 hand-off (pages + resolvers), the site brief (the site note heads the prompt) and the outline of
    the first player page; the LLM may ask for investigation steps (incl. ``probe``) before it writes the chain."""
    ev = finding.evidence or {}
    player = ctx.page("player") or {}
    evidence: dict[str, str] = {
        "Site brief (server memory)": site_brief.brief(ctx.site_id),
        "Full trace of the LLM-free measurement (per sample: page -> candidate / intermediate page -> provider -> streams; "
        "error / http, playback test, dropped non-media addresses)": json.dumps(ev.get("trace") or [], ensure_ascii=False,
                                                                               default=str)[:EVIDENCE_CLIP * 3],
        "Source alternatives table": json.dumps(ev.get("alternatives") or [], ensure_ascii=False, default=str)[:EVIDENCE_CLIP],
        "A5 hand-off (player pages + the resolvers A5 wrote)": json.dumps(
            {"pages": list(a5_pages or [])[:5], "resolvers": player.get("resolvers") or []}, ensure_ascii=False,
            default=str)[:EVIDENCE_CLIP],
        "Measurement": _measured(last),
        "Resolver types (core.play catalogue: param: type, * = required; one valid example each)":
            play_resolvers.catalog_text()[:EVIDENCE_CLIP],
        "player_page extract rules": "\n".join(play_resolvers.player_page.schema_notes()),
    }
    if hint:
        evidence["Admin hint"] = hint
    for url in list(ev.get("samples") or [])[:3]:
        page = ctx.get(url, "player")
        if page is not None:
            from .investigate import evidence_bundle
            for name, body in evidence_bundle(page.html, page.final_url or url, finding, ctx.base_url).items():
                evidence[name] = body
            break
    from .investigate import answer_help
    answer = answer_help(6, probe=True) + "\n" + HUNT_ANSWER
    text = prompts.build(HUNT_PURPOSE, RECIPE_RULES[:1] + HUNT_RULES, evidence, answer, evidence_limit=HTML_CLIP)
    if len(text) > MAX_PROMPT:
        evidence.pop("Resolver types (core.play catalogue: param: type, * = required; one valid example each)", None)
        text = prompts.build(HUNT_PURPOSE, RECIPE_RULES[:1] + HUNT_RULES, evidence, answer, evidence_limit=HTML_CLIP // 2)
    return text[:MAX_PROMPT]
