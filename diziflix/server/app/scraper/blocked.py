"""Content that is not public: the yaml ``blocked:`` rules and the ``availability_gate:`` (pure, no network, no database).

Some sites keep a title in their catalogue but put a placeholder where the player should be ("telif engeli": an
``<iframe src="/player/telif.html">``). The user's decision: only publicly available content enters the library. Two
yaml blocks say so (both optional; a site without them behaves exactly as before)::

    blocked:                               # known placeholders (<= 6 rules)
      - on: episode_page                   # episode_page (a playback page: episode OR film) | series_page
        iframe_src_regex: '/player/telif'  # | html_regex | selector (at least one; several = OR)
        reason: telif engeli               # <= 80 characters (shown to the admin, stored with the verdict)

    availability_gate:                     # the general rule: no player on the page = not taken
      probe: 2                             # episode pages checked per series (newest + oldest [+ middle]); a film: its page; 0 = off
      require: player                      # player = the resolvers give >= 1 candidate | stream = a stream really resolves

This module only parses and judges markup; ``library/gate.py`` fetches pages, keeps the verdicts (``blocked_pages``) and applies
them (nothing is written for a blocked series / episode). Regexes are validated with the ``player_page`` helper (<= 500
characters, no catastrophic-backtracking shapes).
"""
from __future__ import annotations

import functools
import re
from typing import Any, Callable, Optional

from selectolax.parser import HTMLParser

RULE_KEYS = frozenset({"on", "iframe_src_regex", "html_regex", "selector", "reason"})
CONDITIONS = ("iframe_src_regex", "html_regex", "selector")
PAGE_KINDS = ("episode_page", "series_page")
MAX_RULES = 6
MAX_REGEX = 500
MAX_SELECTOR = 300
MAX_REASON = 80
DEFAULT_REASON = "engelli içerik"
HTML_CAP = 2_000_000          # a page is searched up to this size
EVIDENCE_CAP = 120

GATE_KEYS = frozenset({"probe", "require"})
REQUIRES = ("player", "stream")
MAX_PROBE = 3                 # newest + oldest + middle episode page of a series
DEFAULT_PROBE = 2

# what a placeholder iframe usually calls itself (used for the advice when the yaml has no rule)
PLACEHOLDER_WORDS = re.compile(r"telif|copyright|blocked|unavailable|restricted", re.I)
_FRAME_ATTRS = ("src", "data-src", "data-lazy-src")


def _regex_problem(pattern: str) -> Optional[str]:
    from .resolvers import player_page
    return player_page._bad_regex(pattern)


def _selector_problem(selector: str) -> Optional[str]:
    if len(selector) > MAX_SELECTOR:
        return f"selector longer than {MAX_SELECTOR} characters"
    try:
        HTMLParser("<div></div>").css(selector)
    except Exception:
        return f"invalid CSS selector {selector!r}"
    return None


# --- blocked: rules ---------------------------------------------------------------------------------------------------

def _named(rule: Any) -> Any:
    """``rule`` with its keys as the yaml author meant them: YAML 1.1 (PyYAML) reads a bare ``on:`` as the boolean ``True``, so that key is
    taken as ``on`` (a config the server saved itself has it quoted: ``'on'``)."""
    if isinstance(rule, dict) and True in rule and "on" not in rule:
        return {("on" if key is True else key): value for key, value in rule.items()}
    return rule


def validate(raw: Any) -> list[str]:
    """Problems of a ``blocked:`` value (empty list = usable; ``None`` = no block = fine). Messages name the rule."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        return ["blocked: must be a list of rules {on, iframe_src_regex | html_regex | selector, reason}"]
    errs: list[str] = []
    if len(raw) > MAX_RULES:
        errs.append(f"blocked: at most {MAX_RULES} rules (got {len(raw)})")
    for index, rule in enumerate(raw[:MAX_RULES]):
        errs += _validate_rule(index, rule)
    return errs


def _validate_rule(index: int, rule: Any) -> list[str]:
    rule = _named(rule)
    where = f"blocked[{index}]"
    if not isinstance(rule, dict):
        return [f"{where}: must be a mapping (on, iframe_src_regex | html_regex | selector, reason)"]
    errs = [f"{where}: unknown key {key!r} (allowed: {', '.join(sorted(RULE_KEYS))})" for key in rule if key not in RULE_KEYS]
    if rule.get("on") not in PAGE_KINDS:
        errs.append(f"{where}.on: must be one of {', '.join(PAGE_KINDS)}")
    present = [key for key in CONDITIONS if rule.get(key) not in (None, "")]
    if not present:
        errs.append(f"{where}: needs at least one of {', '.join(CONDITIONS)}")
    for key in ("iframe_src_regex", "html_regex"):
        value = rule.get(key)
        if value in (None, ""):
            continue
        if not isinstance(value, str):
            errs.append(f"{where}.{key}: must be a regex string")
            continue
        problem = _regex_problem(value)
        if problem:
            errs.append(f"{where}.{key}: {problem}")
    selector = rule.get("selector")
    if selector not in (None, ""):
        problem = "must be a CSS selector string" if not isinstance(selector, str) else _selector_problem(selector)
        if problem:
            errs.append(f"{where}.selector: {problem}")
    reason = rule.get("reason")
    if reason is not None and (not isinstance(reason, str) or not reason.strip()):
        errs.append(f"{where}.reason: must be a non-empty string")
    elif isinstance(reason, str) and len(reason) > MAX_REASON:
        errs.append(f"{where}.reason: at most {MAX_REASON} characters")
    return errs


def rules_of(raw: Any, warn: Optional[Callable[[str, str], None]] = None) -> list[dict]:
    """The usable rules of a ``blocked:`` value: a bad rule is reported through ``warn(part, message)`` and skipped (never
    raised), the rest of the block still works. Each rule comes back normalized (``on``, the conditions it has, ``reason``)."""
    if not raw:
        return []
    if not isinstance(raw, list):
        if warn:
            warn("blocked", "must be a list of rules, ignored")
        return []
    out: list[dict] = []
    for index, rule in enumerate(raw):
        if index >= MAX_RULES:
            if warn:
                warn("blocked", f"at most {MAX_RULES} rules: the rest is ignored")
            break
        rule = _named(rule)
        problems = _validate_rule(index, rule)
        if problems:
            if warn:
                for message in problems:
                    warn(f"blocked[{index}]", re.sub(r"^blocked\[\d+\]\.?[a-z_]*:\s*", "", message) + " (skipped)")
            continue
        item = {"on": rule["on"], "reason": (rule.get("reason") or DEFAULT_REASON).strip()}
        item.update({key: rule[key] for key in CONDITIONS if rule.get(key) not in (None, "")})
        out.append(item)
    return out


@functools.lru_cache(maxsize=64)
def _compiled(pattern: str) -> "re.Pattern[str]":
    return re.compile(pattern, re.I | re.S)


def frame_sources(html: str, limit: int = 4) -> list[str]:
    """The ``src`` of the iframes of a page (diagnostics: what sits where the player should be), at most ``limit``."""
    if not isinstance(html, str) or not html:
        return []
    return [_clip(src) for src in _frame_sources(HTMLParser(html[:HTML_CAP]))[:limit]]


def _frame_sources(tree: HTMLParser) -> list[str]:
    out: list[str] = []
    for node in tree.css("iframe"):
        for attr in _FRAME_ATTRS:
            value = (node.attributes.get(attr) or "").strip()
            if value:
                out.append(value)
    return out


def _clip(text: str) -> str:
    text = " ".join(str(text).split())
    return text[:EVIDENCE_CAP]


def match(html: str, rules: list[dict], on: str = "episode_page") -> Optional[dict]:
    """The first rule for page kind ``on`` whose condition holds on ``html``: ``{reason, rule, by, evidence}`` (``rule`` = the
    index among the usable rules, ``by`` = the condition that matched, ``evidence`` = the iframe src / matched text, <= 120
    characters), else None. Several conditions of one rule are alternatives (OR)."""
    if not rules or not isinstance(html, str) or not html:
        return None
    html = html[:HTML_CAP]
    tree: Optional[HTMLParser] = None
    sources: Optional[list[str]] = None
    for index, rule in enumerate(rules):
        if rule.get("on") != on:
            continue
        if rule.get("iframe_src_regex"):
            if tree is None:
                tree = HTMLParser(html)
            if sources is None:
                sources = _frame_sources(tree)
            pattern = _compiled(rule["iframe_src_regex"])
            hit = next((src for src in sources if pattern.search(src)), None)
            if hit is not None:
                return {"reason": rule["reason"], "rule": index, "by": "iframe_src_regex", "evidence": _clip(hit)}
        if rule.get("html_regex"):
            found = _compiled(rule["html_regex"]).search(html)
            if found:
                return {"reason": rule["reason"], "rule": index, "by": "html_regex", "evidence": _clip(found.group(0))}
        if rule.get("selector"):
            if tree is None:
                tree = HTMLParser(html)
            try:
                node = tree.css_first(rule["selector"])
            except Exception:
                node = None
            if node is not None:
                return {"reason": rule["reason"], "rule": index, "by": "selector", "evidence": _clip(rule["selector"])}
    return None


def describe(rules: list[dict]) -> list[dict]:
    """The rules as the report shows them: ``[{on, by: [conditions], reason}]``."""
    return [{"on": r["on"], "by": [k for k in CONDITIONS if r.get(k)], "reason": r["reason"]} for r in rules]


def placeholder_hint(html: str) -> Optional[dict]:
    """A frame on the page whose address looks like a content-block placeholder (``telif|copyright|blocked|unavailable|
    restricted`` in its src): ``{src, word}``, else None. Used for the advice "write a ``blocked:`` rule" when a page does not
    resolve and the yaml has no rule."""
    if not isinstance(html, str) or not html:
        return None
    for src in _frame_sources(HTMLParser(html[:HTML_CAP])):
        found = PLACEHOLDER_WORDS.search(src)
        if found:
            return {"src": _clip(src), "word": found.group(0).lower()}
    return None


# --- availability_gate: -----------------------------------------------------------------------------------------------

def validate_gate(raw: Any) -> list[str]:
    """Problems of an ``availability_gate:`` value (empty list = usable; ``None`` = no gate)."""
    if raw is None:
        return []
    if not isinstance(raw, dict):
        return ["availability_gate: must be a mapping {probe, require}"]
    errs = [f"availability_gate: unknown key {key!r} (allowed: {', '.join(sorted(GATE_KEYS))})" for key in raw if key not in GATE_KEYS]
    probe = raw.get("probe", DEFAULT_PROBE)
    if isinstance(probe, bool) or not isinstance(probe, int) or not 0 <= probe <= MAX_PROBE:
        errs.append(f"availability_gate.probe: must be a whole number 0..{MAX_PROBE} (episode pages checked per series; 0 = off)")
    if raw.get("require", "player") not in REQUIRES:
        errs.append(f"availability_gate.require: must be one of {', '.join(REQUIRES)}")
    return errs


def gate_of(raw: Any, warn: Optional[Callable[[str, str], None]] = None) -> dict:
    """The usable gate ``{probe, require}``; ``{}`` when absent, switched off (``probe: 0``) or invalid (reported through ``warn``)."""
    if raw is None:
        return {}
    problems = validate_gate(raw)
    if problems:
        if warn:
            for message in problems:
                warn("availability_gate", message.replace("availability_gate: ", "").replace("availability_gate.", "") + " (gate ignored)")
        return {}
    probe = raw.get("probe", DEFAULT_PROBE)
    if probe == 0:
        return {}
    return {"probe": probe, "require": raw.get("require", "player")}
