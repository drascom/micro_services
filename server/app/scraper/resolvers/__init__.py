"""Generic, config-driven resolver types selected from the site yaml (``resolvers:`` list).

A type knows how to FIND provider candidates in a catalogue page (``discover``) and, when the catalogue hides the
provider behind a session hand-off, how to turn a candidate into a provider URL (``resolve_candidate``). Video
host parsing stays in :mod:`app.scraper.providers`. This module must not import ``scraper.config`` (config imports
it for validation).

A yaml item is flat: ``{type: iframe, selector: "#player iframe", attr: src}``; everything but ``type`` is the
parameter set of that type (:func:`params_of`).
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

from selectolax.parser import HTMLParser

from . import ajax_handoff, anchor_host, data_attr_token, embedded_json, iframe, json_api, player_page


@dataclass
class Ctx:
    site_id: str
    base_url: str
    cfg: Any
    fetch: Any   # the ``app.scraper.fetch`` module (playback transport: fetch_url / post_url)


def make_ctx(cfg) -> Ctx:
    from .. import fetch
    return Ctx(site_id=getattr(cfg, "site_id", "") or "", base_url=getattr(cfg, "base_url", "") or "",
               cfg=cfg, fetch=fetch)


TYPES: dict[str, Any] = {   # type name -> module exposing NAME, DESCRIPTION, PARAMS, discover, resolve_candidate
    module.NAME: module for module in (iframe, anchor_host, data_attr_token, ajax_handoff, json_api, player_page, embedded_json)}

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
