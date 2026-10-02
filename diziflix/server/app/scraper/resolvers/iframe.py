"""``iframe``: provider URLs taken from an attribute (``src``) of the elements a CSS selector matches."""
from __future__ import annotations

from selectolax.parser import HTMLParser

from . import _util as util

NAME = "iframe"
DESCRIPTION = ("Takes the URL in an attribute (default src) of every element the CSS selector matches, such as the "
               "player iframe of a detail page, as a provider candidate. Optional host filter, label and language.")
PARAMS = {
    "selector": {"type": "str", "required": True, "default": None,
                 "help": "CSS selector of the elements carrying the player URL, e.g. '#player iframe[src]'."},
    "attr": {"type": "str", "required": False, "default": "src",
             "help": "Attribute holding the URL (relative URLs are made absolute)."},
    "label": {"type": "str", "required": False, "default": None,
              "help": "Constant candidate label (default: the URL's host)."},
    "label_from": {"type": "str", "required": False, "default": None,
                   "help": "Read the label from the element: 'text' or an attribute name. Ignored when 'label' is set."},
    "host_regex": {"type": "str", "required": False, "default": None,
                   "help": "Keep only URLs whose whole hostname matches this regex (full match, case-insensitive)."},
    "lang": {"type": "str", "required": False, "default": None,
             "help": "Constant language code of the candidates, e.g. 'tr'."},
    "lang_from": {"type": "str", "required": False, "default": None,
                  "help": "Read the language from the element: 'text' or an attribute name (e.g. 'Türkçe Altyazı')."},
}

resolve_candidate = util.passthrough


@util.guard([])
def discover(ctx, html, page_url, params):
    p = util.with_defaults(PARAMS, params)
    seen: set[str] = set()
    found: list[dict] = []
    for node in HTMLParser(html or "").css(p["selector"]):
        url = util.absolute(page_url, node.attributes.get(p["attr"]))
        if not url or url in seen or not util.host_matches(url, p["host_regex"]):
            continue
        seen.add(url)
        found.append(util.with_language({"url": url, "label": util.pick_label(node, p, util.host_of(url))}, node, p))
    return found
