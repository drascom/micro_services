"""``json_api``: the site's own JSON player API answers with the media URLs directly (no provider module)."""
from __future__ import annotations

import re
from types import SimpleNamespace
from urllib.parse import urlparse

from selectolax.parser import HTMLParser

from . import _util as util

NAME = "json_api"
DESCRIPTION = ("Resolves the media straight from a JSON player API: the embed URL (or video id) goes into an endpoint "
               "template and the answer's source list becomes the streams. Same recipe as the legacy stream_resolver "
               "block; with 'selector' the embed URL is found on the page, otherwise candidates come from elsewhere.")
PARAMS = {
    "endpoint": {"type": "str", "required": True, "default": None,
                 "help": "API URL with '{video_id}', e.g. 'https://api.example.com/v1/video/{video_id}'."},
    "referer": {"type": "str", "required": False, "default": None, "help": "Referer header sent to the API."},
    "video_id_regex": {"type": "str", "required": False, "default": r"(\d+)",
                       "help": "Regex with one group that extracts the video id from the embed URL."},
    "sources_json_path": {"type": "str", "required": False, "default": "media.level",
                          "help": "Dotted JSON path of the list of sources in the answer."},
    "source_field": {"type": "str", "required": False, "default": "source", "help": "Field of a source holding the media URL."},
    "quality_field": {"type": "str", "required": False, "default": "value", "help": "Field of a source holding its quality."},
    "quality_preference": {"type": "list", "required": False, "default": [],
                           "help": "Quality values in preferred order; the rest sorts highest numeric first."},
    "verify": {"type": "bool", "required": False, "default": False,
               "help": "Probe every source URL and drop the unreachable ones."},
    "media_type": {"type": "str", "required": False, "default": "mp4",
                   "help": "Media type of the sources ('mp4' or 'hls'); this is the legacy stream_resolver 'type' "
                           "field, renamed because 'type' selects the resolver type in the yaml item."},
    "duration_json_path": {"type": "str", "required": False, "default": None,
                           "help": "Dotted JSON path of the duration in milliseconds."},
    "selector": {"type": "str", "required": False, "default": None,
                 "help": "Optional CSS selector finding the embed URL on the page. Without it discover finds nothing."},
    "attr": {"type": "str", "required": False, "default": "src",
             "help": "Attribute of the selected element holding the embed URL."},
    "stream_headers": {"type": "dict", "required": False, "default": None, "help": util.STREAM_HEADERS_HELP},
    "cache_ttl": {"type": "int", "required": False, "default": None, "help": util.CACHE_TTL_HELP},
}

_NOT_RECIPE = ("selector", "attr", "media_type", "stream_headers", "cache_ttl")


def check(params: dict) -> list[str]:
    errors = []
    pattern = params.get("video_id_regex")
    if isinstance(pattern, str):
        try:
            if re.compile(pattern).groups < 1:
                errors.append("parameter 'video_id_regex' needs one capture group around the id")
        except re.error:
            pass   # reported by the generic regex check
    errors += util.check_stream_headers(params)
    errors += util.check_cache_ttl(params)
    return errors


@util.guard([])
def discover(ctx, html, page_url, params):
    p = util.with_defaults(PARAMS, params)
    if not p["selector"]:
        return []
    seen: set[str] = set()
    found: list[dict] = []
    for node in HTMLParser(html or "").css(p["selector"]):
        url = util.absolute(page_url, node.attributes.get(p["attr"]))
        if url and url not in seen:
            seen.add(url)
            found.append({"url": url, "label": urlparse(url).hostname or NAME})
    return found


@util.guard(None)
def resolve_candidate(ctx, candidate, page_url, params, load_cookies):
    from .. import resolve   # late: patchable, and keeps the package import light
    p = util.with_defaults(PARAMS, params)
    recipe = {name: value for name, value in p.items() if name not in _NOT_RECIPE and value not in (None, "")}
    recipe["type"] = p["media_type"] or "mp4"
    stream = resolve.resolve_stream(SimpleNamespace(stream_resolver=recipe), candidate.get("url") or "")
    if not stream:
        return None
    stream = util.with_cache_ttl(stream, p)
    headers = util.stream_request_headers(ctx, p, page_url, candidate.get("url") or "")
    if headers:   # the streams carry what the media host wants as request_headers (served through the stream proxy)
        stream = {**stream, "streams": util.with_request_headers(list(stream.get("streams") or []), headers)}
    return {**candidate, "stream": stream}
