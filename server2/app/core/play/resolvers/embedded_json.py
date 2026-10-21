"""``embedded_json``: provider URLs taken from JSON that the detail / episode page itself embeds (no extra request).

For sites (Next.js, Nuxt, ...) whose player buttons carry no URL: the sources only exist in the page's data, as a
``<script type="application/json">`` / ``__NEXT_DATA__`` document or in the Next.js App Router "flight" chunks
(``self.__next_f.push([1,"...\\"sources\\":[{...}]..."])``). The chunks are joined and JSON-unescaped, the first key of
``json_path`` locates the value (balanced JSON, so a ``"sources":[...]`` array inside a bigger blob is enough) and the
rest of the path walks it. Every URL becomes a candidate; the registry then hands it to the provider library by host
(unknown hosts are skipped there, as always). No code is executed, no network is used.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from selectolax.parser import HTMLParser

from . import _util as util

NAME = "embedded_json"
DESCRIPTION = ("Reads provider URLs from JSON embedded in the page itself (no extra request): a <script "
               "type=\"application/json\"> / __NEXT_DATA__ document or Next.js flight chunks (self.__next_f.push). "
               "json_path (dotted, '[*]' for lists) names the list of source objects, e.g. 'chapterContent.sources[*]', "
               "url_field / label_field pick the URL and its label inside each object (or the path may end in a string "
               "field itself). Protocol-less '//host/..' URLs get https; the provider library then takes the host.")
PARAMS = {
    "json_path": {"type": "str", "required": True, "default": None,
                  "help": "Dotted path with '[*]' / '[n]', e.g. 'chapterContent.sources[*]' (objects) or "
                          "'chapterContent.sources[*].url' (plain URL strings). Its FIRST key (here chapterContent) "
                          "locates the data inside flight chunks, so pick a distinctive one."},
    "source": {"type": "str", "required": False, "default": "auto",
               "help": "Where the JSON lives: 'auto' (json scripts, then flight chunks), 'json_script' "
                       "(<script type=application/json>, __NEXT_DATA__) or 'flight' (self.__next_f.push chunks)."},
    "url_field": {"type": "str", "required": False, "default": "url",
                  "help": "Field of a source object holding the URL (ignored when json_path ends in strings)."},
    "label_field": {"type": "str", "required": False, "default": "name",
                    "help": "Field of a source object holding its label (default: the URL's host). Ignored when 'label' is set."},
    "label": {"type": "str", "required": False, "default": None, "help": "Constant candidate label."},
    "host_regex": {"type": "str", "required": False, "default": None,
                   "help": "Keep only URLs whose whole hostname matches this regex (full match, case-insensitive)."},
    "lang": {"type": "str", "required": False, "default": None,
             "help": "Constant language code of the candidates, e.g. 'tr'."},
}

MAX_HTML = 3_000_000
MAX_CANDIDATES = 24
MAX_LABEL = 40
MAX_URL = 2048
_SOURCES = ("auto", "json_script", "flight")
_PART = re.compile(r"^([^\[\]]*)((?:\[(?:\*|\d+)\])*)$")
_PUSH = "self.__next_f.push("

resolve_candidate = util.passthrough


# --- path -------------------------------------------------------------------------------------------------------
def _parse_path(path: str) -> Optional[list[tuple[str, list[str]]]]:
    """``[(key, [index, ...]), ...]`` of a dotted path (``None`` = malformed)."""
    parts = []
    for raw in (path or "").split("."):
        found = _PART.match(raw.strip())
        if not found or not (found.group(1) or found.group(2)):
            return None
        parts.append((found.group(1), re.findall(r"\[(\*|\d+)\]", found.group(2))))
    return parts or None


def _walk(nodes: list[Any], parts: list[tuple[str, list[str]]]) -> list[Any]:
    for name, indexes in parts:
        if name:
            nodes = [n[name] for n in nodes if isinstance(n, dict) and name in n]
        for index in indexes:
            if index == "*":
                nodes = [item for n in nodes if isinstance(n, list) for item in n]
            else:
                nodes = [n[int(index)] for n in nodes if isinstance(n, list) and int(index) < len(n)]
    return nodes


def check(params: dict) -> list[str]:
    errors = []
    path = params.get("json_path")
    if isinstance(path, str):
        parts = _parse_path(path)
        if parts is None:
            errors.append("parameter 'json_path' is malformed (dotted keys with optional '[*]' / '[0]', e.g. 'a.sources[*].url')")
        elif not parts[0][0]:
            errors.append("parameter 'json_path' must start with a key (it locates the data in the page), not an index")
    if params.get("source") not in (None, "") and params.get("source") not in _SOURCES:
        errors.append(f"parameter 'source' must be one of {', '.join(_SOURCES)}")
    return errors


# --- locating the JSON in the page -------------------------------------------------------------------------------
def _json_scripts(tree: HTMLParser) -> list[Any]:
    out = []
    for node in tree.css('script[type="application/json"], script#__NEXT_DATA__'):
        try:
            out.append(json.loads((node.text() or "").strip().lstrip("﻿")))
        except ValueError:
            continue
    return out


def _flight_text(tree: HTMLParser) -> str:
    """The Next.js flight payload: the string halves of every ``self.__next_f.push([1,"..."])`` call, JSON-unescaped and
    joined (a JSON value may be split over two pushes)."""
    decoder = json.JSONDecoder()
    chunks: list[str] = []
    for node in tree.css("script"):
        body = node.text() or ""
        at = body.find(_PUSH)
        while at != -1:
            start = at + len(_PUSH)
            try:
                arg, end = decoder.raw_decode(body, start)
            except ValueError:
                arg, end = None, start
            if isinstance(arg, list) and len(arg) >= 2 and arg[0] == 1 and isinstance(arg[1], str):
                chunks.append(arg[1])
            at = body.find(_PUSH, max(end, start))
    return "".join(chunks)


def _flight_values(text: str, key: str) -> list[Any]:
    """Every JSON object / array that follows ``"<key>":`` in the flight text (balanced, via ``raw_decode``)."""
    decoder = json.JSONDecoder()
    out: list[Any] = []
    needle = json.dumps(key) + ":"
    at = text.find(needle)
    while at != -1 and len(out) < 8:
        start = at + len(needle)
        if text[start:start + 1] in ("{", "["):
            try:
                out.append(decoder.raw_decode(text, start)[0])
            except ValueError:
                pass
        at = text.find(needle, at + len(needle))
    return out


def _deep_values(node: Any, key: str, out: list[Any], depth: int = 0) -> None:
    """Every value stored under ``key`` at any depth of a parsed JSON document (bounded)."""
    if depth > 12 or len(out) >= 8:
        return
    if isinstance(node, dict):
        if key in node:
            out.append(node[key])
        for value in node.values():
            _deep_values(value, key, out, depth + 1)
    elif isinstance(node, list):
        for value in node:
            _deep_values(value, key, out, depth + 1)


def find_values(html: str, path: str, source: str = "auto") -> list[Any]:
    """What ``path`` selects from the JSON embedded in ``html`` (raw nodes: dicts / strings), de-duplicated by value."""
    parts = _parse_path(path)
    if parts is None or not parts[0][0]:
        return []
    tree = HTMLParser((html or "")[:MAX_HTML])
    found: list[Any] = []
    if source in ("auto", "json_script"):
        roots: list[Any] = []
        for doc in _json_scripts(tree):   # the first key may sit anywhere (__NEXT_DATA__: props.pageProps.<key>)
            _deep_values(doc, parts[0][0], roots)
        found = _walk([{parts[0][0]: root} for root in roots], parts)
    if not found and source in ("auto", "flight"):
        text = _flight_text(tree)
        if text:
            roots = _flight_values(text, parts[0][0])
            found = _walk([{parts[0][0]: root} for root in roots], parts)   # the located value is the first key's value
    seen, out = set(), []
    for node in found:
        mark = json.dumps(node, sort_keys=True, default=str)
        if mark not in seen:
            seen.add(mark)
            out.append(node)
    return out


# --- discover ----------------------------------------------------------------------------------------------------
def _clip(text: Any) -> str:
    return " ".join(str(text or "").split())[:MAX_LABEL]


@util.guard([])
def discover(ctx, html, page_url, params):
    p = util.with_defaults(PARAMS, params)
    found: list[dict] = []
    seen: set[str] = set()
    for node in find_values(html, p["json_path"], p["source"] or "auto"):
        if isinstance(node, dict):
            raw, name = node.get(p["url_field"] or "url"), node.get(p["label_field"] or "name")
        else:
            raw, name = node, None
        if not isinstance(raw, str):
            continue
        raw = raw.strip()
        url = util.absolute(page_url, "https:" + raw if raw.startswith("//") else raw)   # protocol-less -> https
        if not url or len(url) > MAX_URL or url in seen or not util.host_matches(url, p["host_regex"]):
            continue
        seen.add(url)
        label = str(p["label"]) if p["label"] else (_clip(name) or util.host_of(url))
        candidate = {"url": url, "label": label}
        if p["lang"]:
            candidate["lang"] = str(p["lang"]).strip().lower()
        found.append(candidate)
        if len(found) >= MAX_CANDIDATES:
            break
    return found
