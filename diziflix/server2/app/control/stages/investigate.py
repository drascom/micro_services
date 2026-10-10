"""LLM investigation loop of A5 (player) and A6 (sources): the LLM may answer a stage round with ``actions: [...]`` instead
of its final rule; the server runs them (``core.play.inspect``: fetch / show_script / decode / follow_iframe / find, network
only through ``core.fetch``), sends the trimmed results back in the SAME session and the LLM continues, until it answers with
``result: {...}`` (A5: ``resolvers`` for EVERY source alternative; A6: a provider ``recipe``) or a plain map fragment.

Limits (K6 exception, see the T21 report): at most :data:`MAX_STEPS` investigation steps per LLM round, at most
``inspect.MAX_ACTIONS`` actions per step, :data:`MAX_CHARS` result characters per round; every step is an LLM call and asks
the budget first (background runs). Every request / result is a short ``stage_messages`` line and an "inceleme izi"
entry in the stage evidence (the admin card shows it folded).
"""
from __future__ import annotations

import json
from typing import Any, Callable, Optional

from ...core.extract import htmlclip, player as x_player
from ...core.llm import budget as llm_budget, parse as llm_parse
from ...core.play import inspect
from . import state

MAX_STEPS = 4
MAX_CHARS = 40_000
TRAIL_MAX = 40
LIMIT_TEXT = "inceleme sınırı"

_HELP = """You may first ask the server to investigate (the page's sources can be hidden: several tabs / buttons, AJAX loaders,
base64 / hex / packed scripts, nested iframes). Then answer with ONE ```yaml block, either
  actions:                      # the server runs them (max 6 per step, max {steps} steps) and sends you the results
    - {{fetch: {{url: "...", method: GET, referer: "..."}}}}          # method POST + data: {{field: value}} for an AJAX endpoint
    - {{post: {{url: "{{base}}/ajax/...", data: {{id: "..."}}}}}}            # an AJAX POST (X-Requested-With, Origin, Accept json)
    - {{set_cookie: {{name: "...", value: now_ms}}}}                    # a cookie the site's JS sets (now_ms = Date.now()), this session only
    - {{show_script: {{index: 0, from: 0, to: 2500}}}}                 # an inline script of the page (or url: an external one)
    - {{decode: {{kind: base64, text: "..."}}}}                        # kind: base64 | hex | packed | urldecode | json; ref: r1
    - {{follow_iframe: {{url: "...", referer: "..."}}}}
    - {{find: {{in: page, regex: 'atob\\("([^"]+)"\\)'}}}}               # in: page | script | r1 ...{probe}
or your final answer (see the shape below). Find ALL source alternatives, not only the first one that works.
Source tabs may load by AJAX: find the click handler (show_script), copy its request with post; a 5xx / empty answer may be
a missing cookie: search the scripts for document.cookie and retry after set_cookie."""
_PROBE = """
    - {probe: {url: "https://.../x.m3u8", referer: "...", referer_policy: page}}   # the real playback test of a stream
                                                    # address you found (referer_policy: page | none | origin): played / step / codec
  Only media is a stream (.m3u8 .mp4 .mpd .webm .mkv or a sources[] entry with type hls|mp4): a .js / .css / image /
  font / json address is NEVER a stream (read a script with show_script instead)."""


def answer_help(steps: int = 4, probe: bool = False) -> str:
    """The investigation part of a prompt (``steps`` = this stage's step limit, ``probe`` = offer the playback test)."""
    return _HELP.format(steps=steps, probe=_PROBE if probe else "")


ANSWER_HELP = answer_help(4)


def requested_actions(data: Any) -> Optional[list]:
    """The ``actions`` list of a parsed reply that asks for investigation (and gives no final answer), else None."""
    if not isinstance(data, dict) or "result" in data or "pages" in data:
        return None
    actions = data.get("actions")
    return actions if isinstance(actions, list) and actions else None


def bench_for(stage, ctx, finding) -> inspect.Bench:
    """The page under study: A5 = the first sample page (a failing one first), A6 = the player page of the recipe need."""
    ev = (finding.evidence if finding is not None else {}) or {}
    need = (ev.get("recipe_needed") or [{}])[0]
    if stage.page_type == "sources" and need.get("player_url"):
        return inspect.Bench(need["player_url"], "", ctx_transport(ctx))
    for url in list(ev.get("samples") or []) + list(ctx.samples or []):
        page = ctx.get(url, "player")
        if page is not None:
            return inspect.Bench(page.final_url or url, page.html, ctx_transport(ctx))
    return inspect.Bench(ctx.base_url, "", ctx_transport(ctx))


def ctx_transport(ctx) -> Any:
    from ...core.play import transport
    return transport


def evidence_bundle(html: str, page_url: str, finding=None, base: str = "") -> dict[str, str]:
    """Prompt evidence for a source hunt on one page: the clean HTML around the player, the script list (src + a short excerpt
    of each inline script), the alternatives found so far and where they stopped."""
    out: dict[str, str] = {}
    from selectolax.parser import HTMLParser
    tree = HTMLParser(html or "")
    scripts = []
    for i, node in enumerate(tree.css("script")):
        src = node.attributes.get("src")
        text = " ".join((node.text() or "").split())
        scripts.append({"index": i if not src else None, "src": src, "chars": len(text), "excerpt": text[:220]} if not src
                       else {"src": src})
    out["Scripts of the page (inline index + excerpt)"] = json.dumps([s for s in scripts if s.get("excerpt") or s.get("src")][:25],
                                                                       ensure_ascii=False)[:5000]
    alts = x_player.alternatives(x_player.player_candidates(html, page_url, base))
    ev = (finding.evidence if finding is not None else {}) or {}
    known = ev.get("alternatives") or alts
    out["Source alternatives found so far (LLM-free first layer)"] = json.dumps(
        [{k: a.get(k) for k in ("label", "kind", "how", "host", "found_by", "resolver", "status", "plays")} for a in known][:12],
        ensure_ascii=False, default=str)[:5000]
    out[f"Player area of {page_url} (clean HTML)"] = htmlclip.clean_html(html, 6000, ["iframe", "player", "video", "data-"])
    return out


def _note(site_id: str, stage_id: str, role: str, text: str) -> None:
    try:
        state.add_message(site_id, stage_id, role, text[:300])
    except Exception:
        pass


def _save_trail(site_id: str, stage_id: str, trail: list) -> None:
    try:
        row = state.get(site_id, stage_id) or {}
        ev = dict(row.get("evidence") or {})
        ev["investigation"] = trail[-TRAIL_MAX:]
        state.update(site_id, stage_id, evidence=ev)
    except Exception:
        pass


def loop(stage, ctx, p: dict, reply_text: str, *, complete: Callable[..., Any], session: Optional[str], first_prompt: str,
         finding=None, background: bool = False) -> tuple[Optional[str], str]:
    """Run the investigation steps the LLM asks for until it gives its final answer. Returns ``(final reply text, "")`` or
    ``(None, error)`` (``inceleme sınırı`` / budget / LLM failure)."""
    data, _err = llm_parse.extract_yaml(reply_text)
    actions = requested_actions(data)
    if actions is None:
        return reply_text, ""
    trail = list(p.get("investigation") or [])
    bench = bench_for(stage, ctx, finding)
    max_steps = int(getattr(stage, "investigate_steps", 0) or MAX_STEPS)
    max_chars = int(getattr(stage, "investigate_chars", 0) or MAX_CHARS)
    used, history = 0, []
    answer, error = None, LIMIT_TEXT
    for step in range(1, max_steps + 2):
        if step > max_steps:
            msg = f"{LIMIT_TEXT}: {max_steps} adımda sonuç gelmedi"
            _note(ctx.site_id, stage.id, "system", msg)
            trail.append({"step": step, "action": "-", "ok": False, "summary": msg})
            break
        names = ", ".join(str(next(iter(a), "?")) if isinstance(a, dict) else "?" for a in actions[:inspect.MAX_ACTIONS])
        _note(ctx.site_id, stage.id, "llm", f"İnceleme {step}: {names}")
        results = inspect.run(bench, actions)
        for i, r in enumerate(results, 1):
            trail.append({"step": step, "n": i, "action": r["action"], "ok": r["ok"], "summary": r["summary"][:200]})
            _note(ctx.site_id, stage.id, "system", f"İnceleme {step}.{i}: {r['action']} -> {r['summary']}")
        text = "\n\n".join(f"### step {step}.{i} {r['action']}: {r['summary']}\n{r['text']}" for i, r in enumerate(results, 1))
        used += len(text)
        _save_trail(ctx.site_id, stage.id, trail)
        p["investigation"] = trail
        if used > max_chars:
            msg = f"{LIMIT_TEXT}: sonuçlar {max_chars} karakteri aştı"
            _note(ctx.site_id, stage.id, "system", msg)
            trail.append({"step": step, "action": "-", "ok": False, "summary": msg})
            break
        if background:
            allowed, why = llm_budget.allowed(ctx.site_id, background=True)
            if not allowed:
                error = f"LLM bütçesi doldu ({why})"
                break
        history.append(text)
        follow = ("## INVESTIGATION RESULTS (the server ran your steps)\n" + text +
                  "\n\n## NEXT\nAnswer again with ONE ```yaml block: more `actions:` (step "
                  f"{step + 1} of at most {max_steps}) or your final answer.")
        prompt = follow if session else first_prompt + "\n\n" + "\n\n".join(history) + follow[follow.index("## NEXT"):]
        reply = complete(prompt, purpose="stage", site_id=ctx.site_id, stage=stage.id, timeout=120, session=session,
                         background=background)
        if not getattr(reply, "ok", False):
            error = f"llm_call_failed:{getattr(reply, 'error', '')}"
            break
        data, _err = llm_parse.extract_yaml(reply.text)
        actions = requested_actions(data)
        if actions is None:
            trail.append({"step": step, "action": "result", "ok": True, "summary": "LLM son yanıtını verdi"})
            _note(ctx.site_id, stage.id, "llm", f"İnceleme bitti ({step} adım): son yanıt geldi")
            answer, error = reply.text, ""
            break
    _save_trail(ctx.site_id, stage.id, trail)
    p["investigation"] = trail
    mark_tried(finding, p, trail)
    return answer, error


def tried_summary(trail: list, last: int = 3) -> str:
    """What the LLM tried, its last ``last`` steps in one line: ``adım 2: fetch ✓, probe ✗ (oynamadı: segment ...)``."""
    steps: dict[int, list] = {}
    for t in trail or []:
        if not isinstance(t, dict) or t.get("action") in ("-", "result") or not isinstance(t.get("step"), int):
            continue
        mark = "+" if t.get("ok") else "x"
        text = f"{t.get('action')} {mark}"
        if t.get("action") == "probe" or not t.get("ok"):
            text += f" ({str(t.get('summary') or '')[:90]})"
        steps.setdefault(t["step"], []).append(text)
    if not steps:
        return ""
    keys = sorted(steps)[-last:]
    return "; ".join(f"adım {k}: " + ", ".join(steps[k][:6]) for k in keys)


def mark_tried(finding, p: dict, trail: list) -> None:
    """The summary of the investigation goes into the stage evidence (the "Yardım gerekiyor" question's "Sonuç" line
    names what the LLM tried when the stage still fails)."""
    line = tried_summary(trail)
    if not line or finding is None:
        return
    targets = [finding.evidence] + ([p["finding"].setdefault("evidence", {})] if isinstance(p.get("finding"), dict) else [])
    for ev in targets:
        if ev is None:
            continue
        base = ev.get("summary_base") or ev.get("summary") or ""
        ev["summary_base"] = base
        ev["llm_tried"] = line
        ev["summary"] = (f"{base}; " if base else "") + f"LLM incelemesi (son adımlar): {line}"
