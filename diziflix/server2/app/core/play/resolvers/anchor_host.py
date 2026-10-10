"""``anchor_host``: links (``a[href]``) whose host is a known provider host."""
from __future__ import annotations

from selectolax.parser import HTMLParser

from . import _util as util

NAME = "anchor_host"
DESCRIPTION = ("Takes the links the selector matches (default every a[href]) whose hostname matches a provider host "
               "regex, such as download or watch links to a video host. Label and language come from the link text.")
PARAMS = {
    "selector": {"type": "str", "required": False, "default": "a[href]",
                 "help": "CSS selector of the links, e.g. 'a[href*=\"/dl/\"]'."},
    "host_regex": {"type": "str", "required": True, "default": None,
                   "help": "Keep only links whose whole hostname matches this regex (full match, case-insensitive), "
                           "e.g. '(?:.+\\.)?vidmol{1,2}y\\.[a-z0-9.-]+'."},
    "label": {"type": "str", "required": False, "default": None,
              "help": "Constant candidate label (overrides 'label_from')."},
    "label_from": {"type": "str", "required": False, "default": "text",
                   "help": "Where the label comes from: 'text' (link text) or an attribute name. Empty -> the host."},
    "lang": {"type": "str", "required": False, "default": None,
             "help": "Constant language code of the candidates, e.g. 'tr'."},
    "lang_from": {"type": "str", "required": False, "default": None,
                  "help": "Read the language from the link: 'text' or an attribute name "
                          "(e.g. 'İngilizce Altyazılı İndir' -> en)."},
}

resolve_candidate = util.passthrough


@util.guard([])
def discover(ctx, html, page_url, params):
    p = util.with_defaults(PARAMS, params)
    seen: set[str] = set()
    found: list[dict] = []
    for node in HTMLParser(html or "").css(p["selector"]):
        url = util.absolute(page_url, node.attributes.get("href"))
        if not url or url in seen or not util.host_matches(url, p["host_regex"]):
            continue
        seen.add(url)
        found.append(util.with_language({"url": url, "label": util.pick_label(node, p, util.host_of(url))}, node, p))
    return found
