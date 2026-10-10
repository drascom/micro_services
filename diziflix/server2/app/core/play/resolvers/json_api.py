"""``json_api``: the site's own JSON player API answers with the media URLs directly (no provider module)."""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Optional
from urllib.parse import urlparse

from selectolax.parser import HTMLParser

from . import _util as util
from ..providers import trace

log = logging.getLogger("play.json_api")

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
    p = util.with_defaults(PARAMS, params)
    recipe = {name: value for name, value in p.items() if name not in _NOT_RECIPE and value not in (None, "")}
    recipe["type"] = p["media_type"] or "mp4"
    stream = resolve_stream(ctx.fetch, recipe, candidate.get("url") or "")
    if not stream:
        return None
    stream = util.with_cache_ttl(stream, p)
    headers = util.stream_request_headers(ctx, p, page_url, candidate.get("url") or "")
    if headers:   # the streams carry what the media host wants as request_headers (served through the stream proxy)
        stream = {**stream, "streams": util.with_request_headers(list(stream.get("streams") or []), headers)}
    return {**candidate, "stream": stream}


# --- the JSON API engine (old ``scraper/resolve.py``: only the json_api resolver uses it now) -------------------
def _dig(data: Any, path: str) -> Any:
    cur = data
    for key in path.split("."):
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return None
    return cur


def _rank(value: str, preference: list) -> tuple:
    v = str(value)
    if v in preference:
        return (0, preference.index(v))
    try:
        return (1, -int(v))
    except ValueError:
        return (2, 0)


def _label(value: str) -> str:
    v = str(value)
    try:
        return (v + "p") if int(v) > 0 else "En Yüksek"
    except ValueError:
        return v or "Fragman"


def resolve_stream(fetch_api, rc: dict, embed_url_or_video_id: str) -> Optional[dict]:
    """``{url, type, quality, duration, streams}`` from a JSON player API (``rc`` = the json_api parameters), or None."""
    if not rc or not rc.get("endpoint") or not embed_url_or_video_id:
        return None
    if embed_url_or_video_id.isdigit():
        video_id = embed_url_or_video_id
    else:
        m = re.search(rc.get("video_id_regex", r"(\d+)"), embed_url_or_video_id)
        video_id = m.group(1) if m else None
    if not video_id:
        return None
    endpoint = rc["endpoint"].replace("{video_id}", video_id)
    headers = {"Referer": rc["referer"]} if rc.get("referer") else {}
    started = time.monotonic()
    try:
        data = json.loads(fetch_api.fetch_url(endpoint, headers=headers, check_robots=False))
        trace.note("json_api", urlparse(endpoint).hostname or "", True, started)
    except Exception as exc:
        trace.note("json_api", urlparse(endpoint).hostname or "", False, started, exc)
        return None
    sources = _dig(data, rc.get("sources_json_path", "media.level"))
    if not isinstance(sources, list) or not sources:
        return None
    src_field, q_field = rc.get("source_field", "source"), rc.get("quality_field", "value")
    preference = [str(x) for x in (rc.get("quality_preference") or [])]
    found = [(str(s.get(q_field, "")), s.get(src_field)) for s in sources if isinstance(s, dict) and s.get(src_field)]
    found.sort(key=lambda c: _rank(c[0], preference))
    streams = []
    for quality, url in found:
        if not isinstance(url, str) or (rc.get("verify") and not fetch_api.reachable(url)):
            continue
        streams.append({"url": url, "type": rc.get("type", "mp4"), "quality": quality, "label": _label(quality)})
    if not streams:
        return None
    duration = 0
    if rc.get("duration_json_path"):
        try:
            duration = int(int(_dig(data, rc["duration_json_path"])) / 1000)
        except (TypeError, ValueError):
            duration = 0
    best = streams[0]
    return {"url": best["url"], "type": best["type"], "quality": best["quality"], "duration": duration, "streams": streams}
