"""Generic resolver types selected by the site map (``pages.player.resolvers`` list).

A type knows how to FIND provider candidates in a catalogue page (``discover``) and, when the catalogue hides the
provider behind a session hand-off, how to turn a candidate into a provider URL (``resolve_candidate``). Video
host parsing stays in :mod:`app.core.play.providers` (K4: resolver code is generic, the site-specific part is the PARAMETERS).

A yaml item is flat: ``{type: iframe, selector: "#player iframe", attr: src}``; everything but ``type`` is the
parameter set of that type (:func:`params_of`).
"""
from __future__ import annotations

import copy
import re
from typing import Any, Optional

from selectolax.parser import HTMLParser

from ..ctx import PlayCtx, make_ctx  # noqa: F401  (re-exported: resolvers.make_ctx / resolvers.PlayCtx)
from . import ajax, ajax_handoff, anchor_host, data_attr, data_attr_token, embedded_json, iframe, json_api, player_page, script_regex

Ctx = PlayCtx


TYPES: dict[str, Any] = {   # type name -> module exposing NAME, DESCRIPTION, PARAMS, discover, resolve_candidate
    module.NAME: module for module in (iframe, anchor_host, data_attr_token, ajax_handoff, json_api, player_page, embedded_json,
                                     data_attr, script_regex, ajax)}

_PYTHON_TYPES = {"str": str, "int": int, "bool": bool, "list": list, "dict": dict}


def params_of(item: dict) -> dict:
    """The parameters of one yaml resolver item (every key but ``type``)."""
    return {name: value for name, value in item.items() if name != "type"}


def _type_ok(value: Any, kind: str) -> bool:
    if kind == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, _PYTHON_TYPES[kind])


def _check_value(name: str, value: Any) -> str | None:
    """Compile-time checks of values that are regexes / selectors, by parameter name."""
    if not isinstance(value, str):
        return None
    if name.endswith("_regex"):
        try:
            re.compile(value)
        except re.error as exc:
            return f"parameter '{name}': invalid regex ({exc})"
    elif name == "selector":
        try:
            HTMLParser("").css(value)
        except Exception:
            return f"parameter '{name}': invalid CSS selector {value!r}"
    return None


def _validate_item(index: int, item: Any) -> list[str]:
    prefix = f"resolvers[{index}]"
    if not isinstance(item, dict):
        return [f"{prefix}: must be a mapping with a 'type' (got {type(item).__name__})"]
    kind = item.get("type")
    if not isinstance(kind, str) or not kind:
        return [f"{prefix}: missing 'type'"]
    module = TYPES.get(kind)
    if module is None:
        return [f"{prefix}: unknown type '{kind}'"]
    errors: list[str] = []
    params = params_of(item)
    for name in params:
        if name not in module.PARAMS:
            errors.append(f"{prefix}: unknown parameter '{name}' for type '{kind}'")
    for name, spec in module.PARAMS.items():
        value = params.get(name)
        if value is None or value == "":
            if spec.get("required"):
                errors.append(f"{prefix}: missing required parameter '{name}'")
            continue
        if not _type_ok(value, spec["type"]):
            errors.append(f"{prefix}: parameter '{name}' must be {spec['type']} (got {type(value).__name__})")
            continue
        problem = _check_value(name, value)
        if problem:
            errors.append(f"{prefix}: {problem}")
    check = getattr(module, "check", None)
    if check is not None and not errors:
        errors += [f"{prefix}: {message}" for message in check(params)]
    return errors


def validate(resolvers) -> list[str]:
    """Error messages of a ``resolvers`` list (empty when valid): unknown type, unknown / missing / wrongly typed
    parameter, a regex or CSS selector that does not compile. ``validate([item])`` checks one item (index 0)."""
    if not isinstance(resolvers, list):
        return [f"resolvers must be a list (got {type(resolvers).__name__})"]
    errors: list[str] = []
    for index, item in enumerate(resolvers):
        errors += _validate_item(index, item)
    return errors


def catalog() -> list[dict]:
    """``[{type, description, params}]`` of every type, in :data:`TYPES` order (params = the PARAMS schema)."""
    return [{"type": name, "description": module.DESCRIPTION, "params": copy.deepcopy(module.PARAMS)}
            for name, module in TYPES.items()]


# --- LLM leniency: common shapes normalised BEFORE validate ----------------------------------------------------------
_REFERER_WORDS = {"self": "{page_url}", "page": "{page_url}", "page_url": "{page_url}", "detail": "{page_url}",
                  "episode": "{page_url}", "parent": "{page_url}", "true": "{page_url}", "base": "{base}", "site": "{base}",
                  "origin": "{base}", "home": "{base}"}


def normalize(resolvers: Any, notes: Optional[list] = None) -> Any:
    """Common LLM shapes of a ``resolvers`` list turned into the schema's (in place where possible; returns the list):
    a single item -> a list; ``referer: self|page`` -> ``{page_url}`` (``base|site`` -> ``{base}``); and the item's type
    hook (``player_page.normalize``: ``extract: "<regex>"`` -> ``[{regex, type: auto}]``, ``follow: true`` dropped ...).
    Every change appends a line to ``notes``; what cannot be read safely is left alone (validate still reports it)."""
    def note(text: str) -> None:
        if notes is not None and text not in notes:
            notes.append(text)

    if isinstance(resolvers, dict) and "type" in resolvers:
        resolvers = [resolvers]
        note("normalleştirme: tek çözücü listeye alındı")
    if not isinstance(resolvers, list):
        return resolvers
    for index, item in enumerate(resolvers):
        if not isinstance(item, dict):
            continue
        module = TYPES.get(str(item.get("type") or ""))
        params = getattr(module, "PARAMS", {}) or {}
        ref = item.get("referer")
        if "referer" in params and (isinstance(ref, bool) or (isinstance(ref, str) and ref.strip().lower() in _REFERER_WORDS)):
            word = "true" if ref is True else str(ref).strip().lower()
            if ref is False:
                item.pop("referer")
                note(f"normalleştirme: resolvers[{index}].referer false kaldırıldı")
            else:
                item["referer"] = _REFERER_WORDS[word]
                note(f"normalleştirme: resolvers[{index}].referer {ref!r} -> {item['referer']!r}")
        hook = getattr(module, "normalize", None)
        if hook is not None:
            for text in hook(item):
                note(f"normalleştirme: resolvers[{index}].{text}")
    return resolvers


def example(kind: str) -> Optional[dict]:
    """One VALID item of type ``kind`` built from its schema (required parameters with a placeholder of their type, the
    type's own ``EXAMPLE_PARAMS`` hook for structured values); checked by :func:`validate` (None when it would not pass)."""
    module = TYPES.get(kind)
    if module is None:
        return None
    item: dict = {"type": kind}
    hook = getattr(module, "example_params", None)
    own = hook() if hook is not None else {}
    for name, spec in module.PARAMS.items():
        if name in own:
            item[name] = copy.deepcopy(own[name])
        elif spec.get("required"):
            text = "<value>"
            if name == "selector":
                text = "iframe[src]"
            elif name == "host_regex":
                text = r"(?:.+\.)?player\.example"
            elif name == "url_template":
                text = "{base}/embed/{token}"
            elif "regex" in name:
                text = r'file:\s*"([^"]+)"'
            item[name] = {"str": text, "int": 1, "bool": False, "list": [], "dict": {}}[spec["type"]]
    return item if not validate([item]) else None


def schema_text(kind: str) -> str:
    """The exact parameter schema of type ``kind`` (``name: type, required|optional, default``) + one valid example
    (:func:`example`), for format-fix and stage prompts. Generated from ``PARAMS`` / the validator, never written by hand."""
    module = TYPES.get(kind)
    if module is None:
        return ""
    lines = [f"type {kind}: {' '.join(module.DESCRIPTION.split())[:220]}"]
    for name, spec in module.PARAMS.items():
        default = spec.get("default")
        lines.append(f"  {name}: {spec['type']}, {'REQUIRED' if spec.get('required') else 'optional'}"
                     + (f", default {default!r}" if default not in (None, "", [], {}) else ""))
    extra = getattr(module, "schema_notes", None)
    if extra is not None:
        lines += ["  " + t for t in extra()]
    sample = example(kind)
    if sample is not None:
        import json as _json
        lines.append("  example (valid): " + _json.dumps(sample, ensure_ascii=False))
    return "\n".join(lines)


def types_in(errors: Any) -> list[str]:
    """The resolver types an error text / list names (``resolvers[0]`` of the reply, ``type 'x'``), known types only."""
    text = " ".join(errors) if isinstance(errors, list) else str(errors or "")
    return [name for name in TYPES if re.search(r"(?<![\w])" + re.escape(name) + r"(?![\w])", text)]


def catalog_text() -> str:
    """Compact resolver catalogue for the A5 / A6 prompts: per type ``param: type`` (``*`` = required) + one valid example."""
    import json as _json
    lines = []
    for name, module in TYPES.items():
        params = ", ".join(f"{p}{'*' if spec.get('required') else ''}: {spec['type']}" for p, spec in module.PARAMS.items())
        sample = example(name)
        lines.append(f"- {name} ({params})" + (f"; example: {_json.dumps(sample, ensure_ascii=False)}" if sample else ""))
    return "\n".join(lines)


SCHEMA_TITLE = "Resolver types (param: type, * = required; one valid example each) + player_page extract rules"


def prompt_block(kinds: Optional[list] = None) -> str:
    """THE resolver schema block of every A5 / A6 prompt (first read, fix round, format fix: one source, generated from the
    validator): the catalogue, the ``player_page`` extract / follow rules and, for ``kinds``, the exact schema of each."""
    lines = [catalog_text(), "player_page extract rules:"] + ["  " + t for t in player_page.schema_notes()]
    kinds = [k for k in dict.fromkeys(kinds or []) if k in TYPES][:3]
    if kinds:
        lines.append("Exact schema of the resolver type(s) concerned (keep every parameter to this type and shape):")
        lines += [schema_text(k) for k in kinds]
    return "\n".join(lines)
