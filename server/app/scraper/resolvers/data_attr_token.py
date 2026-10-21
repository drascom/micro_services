"""``data_attr_token``: an opaque token in a data attribute becomes a candidate URL through a template."""
from __future__ import annotations

from urllib.parse import quote

from selectolax.parser import HTMLParser

from . import _util as util

NAME = "data_attr_token"
DESCRIPTION = ("Reads a token from an attribute (e.g. data-link) of the selected elements and puts it into a URL "
               "template ('{base}/api/moly/{token}') to build the candidate URL. When the URL is a site hand-off page "
               "that redirects to the provider, set expect_host_regex: the page is then opened and its first iframe "
               "becomes the provider URL.")
PARAMS = {
    "selector": {"type": "str", "required": True, "default": None,
                 "help": "CSS selector of the elements carrying the token, e.g. '.alternatives-for-this [data-link]'."},
    "attr": {"type": "str", "required": True, "default": None,
             "help": "Attribute holding the token, e.g. 'data-link'. Elements without it are skipped."},
    "url_template": {"type": "str", "required": True, "default": None,
                     "help": "Candidate URL with '{token}' (percent-encoded, '=+-_.~' kept) and optional '{base}' "
                             "(the site base URL), e.g. '{base}/api/moly/{token}'."},
    "label": {"type": "str", "required": False, "default": None,
              "help": "Constant candidate label (overrides 'label_from')."},
    "label_from": {"type": "str", "required": False, "default": "text",
                   "help": "Where the label comes from: 'text' (element text) or an attribute name. Empty -> the host."},
    "filter_regex": {"type": "str", "required": False, "default": None,
                     "help": "Keep only elements whose label text matches this regex (search, case-insensitive), "
                             "e.g. '^vidmoly$'."},
    "lang": {"type": "str", "required": False, "default": None,
             "help": "Constant language code of the candidates, e.g. 'tr'."},
    "lang_from": {"type": "str", "required": False, "default": None,
                  "help": "Read the language from the element: 'text' or an attribute name."},
    "expect_host_regex": {"type": "str", "required": False, "default": None,
                          "help": "Hand-off mode: the candidate URL is a page on the site; resolving opens it and "
                                  "takes the first iframe[src], whose whole hostname must match this regex."},
    "cookie_seed": {"type": "dict", "required": False, "default": None,
                    "help": "Cookies sent with the hand-off page request: {name: 'now_ms' | 'now_s' | literal}."},
    "browser_fallback": {"type": "bool", "required": False, "default": False,
                         "help": "When the site refuses the light cookies (HTTP block / challenge page) retry once with "
                                 "the browser session's cookies (10+ seconds)."},
}


def check(params: dict) -> list[str]:
    """Semantic checks beyond the generic schema (called by ``resolvers.validate``)."""
    template = params.get("url_template")
    if isinstance(template, str) and "{token}" not in template:
        return ["parameter 'url_template' must contain '{token}'"]
    return []


@util.guard([])
def discover(ctx, html, page_url, params):
    p = util.with_defaults(PARAMS, params)
    seen: set[str] = set()
    found: list[dict] = []
    for node in HTMLParser(html or "").css(p["selector"]):
        token = (node.attributes.get(p["attr"]) or "").strip()
        if not token or not util.label_matches(node, p):
            continue
        url = util.absolute(page_url, util.expand(p["url_template"], ctx, page_url,
                                                  token=quote(token, safe="=+-_.~")))
        if not url or url in seen:
            continue
        seen.add(url)
        found.append(util.with_language({"url": url, "label": util.pick_label(node, p, util.host_of(url))}, node, p))
    return found


@util.guard(None)
def resolve_candidate(ctx, candidate, page_url, params, load_cookies):
    p = util.with_defaults(PARAMS, params)
    expect = p["expect_host_regex"]
    url = util.absolute(page_url, candidate.get("url"))
    if not expect or not url or util.host_matches(url, expect) or not util.same_site(ctx, page_url, url):
        return candidate   # no hand-off configured / already the provider / not a page of this site
    stage = f"{NAME}.handoff"
    found = util.with_browser_fallback(
        lambda cookies, name: util.follow_page(ctx, url, page_url, cookies, expect, name),
        page_url, p["cookie_seed"], bool(p["browser_fallback"]), load_cookies, stage)
    return {**candidate, "url": found} if found else None
