"""``data_attr``: a (possibly encoded) URL / id in an attribute of tab buttons, list items or ``<option>`` elements."""
from __future__ import annotations

from urllib.parse import quote

from selectolax.parser import HTMLParser

from . import _decode, _util as util

NAME = "data_attr"
DESCRIPTION = ("Reads a value from an attribute (data-src, data-video, value of an <option> ...) of every selected element, "
               "decodes it (decode: base64 | hex | urldecode | json | reverse, in order) and takes it as the candidate URL; "
               "with url_template the decoded value is an id put into '{value}'. A media file URL (.m3u8 / .mp4) plays as is.")
PARAMS = {
    "selector": {"type": "str", "required": True, "default": None,
                 "help": "CSS selector of the elements carrying the value, e.g. '.player-tabs [data-src]', 'select.alt option'."},
    "attr": {"type": "str", "required": True, "default": None, "help": "Attribute holding the value, e.g. 'data-src' or 'value'."},
    "decode": {"type": "list", "required": False, "default": [],
               "help": "Decode steps applied in order: base64, hex, urldecode, json, reverse."},
    "url_template": {"type": "str", "required": False, "default": None,
                     "help": "Candidate URL with '{value}' (the decoded value, percent-encoded) and optional '{base}'."},
    "host_regex": {"type": "str", "required": False, "default": None,
                   "help": "Keep only URLs whose whole hostname matches this regex (full match, case-insensitive)."},
    "label": {"type": "str", "required": False, "default": None, "help": "Constant candidate label."},
    "label_from": {"type": "str", "required": False, "default": "text",
                   "help": "Where the label comes from: 'text' (element text) or an attribute name."},
    "lang": {"type": "str", "required": False, "default": None, "help": "Constant language code, e.g. 'tr'."},
    "lang_from": {"type": "str", "required": False, "default": None,
                  "help": "Read the language from the element: 'text' or an attribute name."},
}


def check(params: dict) -> list[str]:
    errors = _decode.check_steps(params.get("decode"))
    template = params.get("url_template")
    if isinstance(template, str) and "{value}" not in template:
        errors.append("parameter 'url_template' must contain '{value}'")
    return errors


def candidate_of(url: str, label: str) -> dict:
    """A candidate for ``url``; a media file becomes a ready ``stream`` (no provider needed)."""
    kind = _decode.media_type(url)
    cand = {"url": url, "label": label}
    if kind:
        cand["stream"] = {"provider": label or util.host_of(url), "streams": [{"url": url, "type": kind, "quality": "auto",
                                                                              "label": label or "Otomatik"}]}
    return cand


@util.guard([])
def discover(ctx, html, page_url, params):
    p = util.with_defaults(PARAMS, params)
    seen: set[str] = set()
    found: list[dict] = []
    for node in HTMLParser(html or "").css(p["selector"]):
        raw = (node.attributes.get(p["attr"]) or "").strip()
        value = _decode.chain(raw, p["decode"]) if raw else ""
        if not value:
            continue
        if p["url_template"]:
            value = util.expand(p["url_template"], ctx, page_url, value=quote(value.strip(), safe="=+-_.~/:"))
        url = util.absolute(page_url, value.strip())
        if not url or url in seen or not util.host_matches(url, p["host_regex"]):
            continue
        seen.add(url)
        found.append(util.with_language(candidate_of(url, util.pick_label(node, p, util.host_of(url))), node, p))
    return found


resolve_candidate = util.passthrough
