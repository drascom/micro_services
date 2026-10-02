"""``ajax_handoff``: a session-bound AJAX call turns an element's data attributes into the provider URL."""
from __future__ import annotations

import json
import time
from typing import Optional
from urllib.parse import urlencode, urlparse

from selectolax.parser import HTMLParser

from . import _util as util
from ..providers import trace

NAME = "ajax_handoff"
DESCRIPTION = ("For every selected element builds a hand-off (POST/GET to a site endpoint with form fields taken "
               "from the element's attributes). Resolving sends it, reads a URL from the JSON answer (json_key), or "
               "the first iframe of an HTML answer, and checks the provider host. Optional light cookie seed and "
               "browser-cookie fallback for Cloudflare-protected sites.")
PARAMS = {
    "selector": {"type": "str", "required": True, "default": None,
                 "help": "CSS selector of the elements that carry the hand-off data, e.g. "
                         "'.alternatives-for-this [data-link]'."},
    "method": {"type": "str", "required": False, "default": "POST",
               "help": "'POST' (form body) or 'GET' (form fields as query string)."},
    "url": {"type": "str", "required": True, "default": None,
            "help": "Endpoint URL; '{base}' is the site base URL, e.g. '{base}/ajax/service'. It must be on the "
                    "site's own host."},
    "form": {"type": "dict", "required": False, "default": {},
             "help": "Request fields: {field: 'attr:<attribute>' (read from the element, element skipped when empty) "
                     "| 'const:<text>'}, e.g. {link: 'attr:data-link', type: 'const:videoGet'}."},
    "json_key": {"type": "str", "required": False, "default": None,
                 "help": "Dotted key of the JSON answer holding the URL (e.g. 'api_iframe'). Without it (or when the "
                         "answer is HTML) the first iframe[src] of the answer is used."},
    "expect_host_regex": {"type": "str", "required": False, "default": None,
                          "help": "The provider URL's whole hostname must match this regex (full match, "
                                  "case-insensitive). A URL found on the site's own host is opened once and its first "
                                  "iframe[src] is taken (hand-off page). Without it the found URL is used as is."},
    "cookie_seed": {"type": "dict", "required": False, "default": None,
                    "help": "Cookies sent with the requests: {name: 'now_ms' | 'now_s' | literal}, "
                            "e.g. {udys: 'now_ms'}."},
    "browser_fallback": {"type": "bool", "required": False, "default": False,
                         "help": "When the site refuses the light cookies (HTTP 401/403/429/503/52x, non-JSON or "
                                 "iframe-less answer) retry once with the browser session's cookies (10+ seconds). "
                                 "Never retried for a definite 'no' (error inside the JSON)."},
    "label": {"type": "str", "required": False, "default": None,
              "help": "Constant candidate label (overrides 'label_from'), e.g. 'OK.ru'."},
    "label_from": {"type": "str", "required": False, "default": "text",
                   "help": "Where the label comes from: 'text' (element text) or an attribute name."},
    "filter_regex": {"type": "str", "required": False, "default": None,
                     "help": "Keep only elements whose label text matches this regex (search, case-insensitive), "
                             "e.g. '^ok\\.?ru$'."},
    "lang": {"type": "str", "required": False, "default": None,
             "help": "Constant language code of the candidates, e.g. 'tr'."},
    "lang_from": {"type": "str", "required": False, "default": None,
                  "help": "Read the language from the element: 'text' or an attribute name."},
}


def check(params: dict) -> list[str]:
    """Semantic checks beyond the generic schema (called by ``resolvers.validate``)."""
    errors = []
    method = params.get("method")
    if isinstance(method, str) and method.upper() not in ("GET", "POST"):
        errors.append("parameter 'method' must be GET or POST")
    form = params.get("form")
    if isinstance(form, dict):
        for field, spec in form.items():
            if not isinstance(spec, str) or not (spec.startswith("const:") or (spec.startswith("attr:") and spec[5:].strip())):
                errors.append(f"parameter 'form': field '{field}' must be 'attr:<attribute>' or 'const:<text>'")
    seed = params.get("cookie_seed")
    if isinstance(seed, dict) and not all(isinstance(value, (str, int)) for value in seed.values()):
        errors.append("parameter 'cookie_seed': values must be text ('now_ms', 'now_s' or a literal)")
    return errors


def _field(node, spec) -> Optional[str]:
    spec = str(spec)
    if spec.startswith("attr:"):
        return (node.attributes.get(spec[5:].strip()) or "").strip() or None
    return spec[6:] if spec.startswith("const:") else spec


@util.guard([])
def discover(ctx, html, page_url, params):
    p = util.with_defaults(PARAMS, params)
    endpoint = util.absolute(page_url, util.expand(p["url"], ctx, page_url))
    if not endpoint:
        return []
    seen: set[tuple] = set()
    found: list[dict] = []
    for node in HTMLParser(html or "").css(p["selector"]):
        if not util.label_matches(node, p):
            continue
        form = {str(name): _field(node, spec) for name, spec in (p["form"] or {}).items()}
        if any(value is None for value in form.values()):
            continue
        marker = tuple(sorted(form.items()))
        if marker in seen:
            continue
        seen.add(marker)
        handoff = {"url": endpoint, "method": str(p["method"] or "POST").upper(), "form": form,
                   "json_key": p["json_key"] or "", "expect_host_regex": p["expect_host_regex"] or "",
                   "cookie_seed": dict(p["cookie_seed"] or {}), "browser_fallback": bool(p["browser_fallback"])}
        candidate = {"url": page_url, "label": util.pick_label(node, p, util.host_of(endpoint)), "handoff": handoff}
        found.append(util.with_language(candidate, node, p))
    return found


def _dig(data, path: str):
    for key in path.split("."):
        if isinstance(data, dict) and key in data:
            data = data[key]
        else:
            return None
    return data


def _json(body: str):
    try:
        return json.loads(body)
    except (TypeError, ValueError):
        return None


def _attempt(ctx, handoff: dict, page_url: str, cookies: list[dict], stage: str) -> tuple[Optional[str], bool]:
    """One complete hand-off attempt. Returns ``(provider_url, retry_with_browser)``; the second is set only for
    answers that look cookie / Cloudflare related (HTTP block, non-JSON answer without an iframe), never for a
    definite "no"."""
    page = urlparse(page_url)
    host = page.hostname or ""
    endpoint = handoff["url"]
    expect = handoff.get("expect_host_regex") or None
    started = time.monotonic()
    headers = {"Accept": "application/json, text/javascript, */*; q=0.01",
               "Origin": f"{page.scheme}://{page.netloc}", "Referer": page_url, "X-Requested-With": "XMLHttpRequest"}
    cookie = util.cookie_header(cookies, util.host_of(endpoint))
    if cookie:
        headers["Cookie"] = cookie
    form = handoff.get("form") or {}
    try:
        if handoff.get("method") == "GET":
            sep = "&" if "?" in endpoint else "?"
            body = ctx.fetch.fetch_url(endpoint + (sep + urlencode(form) if form else ""), headers=headers,
                                       timeout=10, retries=1, check_robots=False)
        else:
            body = ctx.fetch.post_url(endpoint, data=form, headers=headers, timeout=10, retries=1)
    except Exception as exc:
        trace.note(stage, host, False, started, exc)
        return None, util.status_of(exc) in util.BLOCKED

    answer = _json(body)
    key = handoff.get("json_key") or ""
    if answer is not None:
        value = _dig(answer, key) if key else None
        if not isinstance(value, str) or not value.strip():
            reason = (answer.get("error") if isinstance(answer, dict) else None) or (
                f"no '{key}' in the answer" if key else "JSON answer but no json_key configured")
            trace.note(stage, host, False, started, f"hand-off answer: {reason}")
            return None, False
        found = util.absolute(page_url, value)
    else:   # HTML answer: its first iframe is the hand-off; no iframe at all = challenge / interstitial page
        node = HTMLParser(body or "").css_first("iframe[src]")
        if node is None:
            trace.note(stage, host, False, started, "hand-off answered with a non-JSON page without iframe")
            return None, True
        found = util.absolute(endpoint, node.attributes.get("src"))
    if not found:
        trace.note(stage, host, False, started, "hand-off answer holds no usable URL")
        return None, False
    if not expect or util.host_matches(found, expect):
        trace.note(stage, host, True, started)
        return found, False
    if util.same_site(ctx, page_url, found):   # an on-site hand-off page: its iframe is the provider
        trace.note(stage, host, True, started)
        return util.follow_page(ctx, found, page_url, cookies, expect, stage + ".page")
    trace.note(stage, host, False, started, f"hand-off address host {util.host_of(found)} is not the expected one")
    return None, False


@util.guard(None)
def resolve_candidate(ctx, candidate, page_url, params, load_cookies):
    handoff = candidate.get("handoff")
    if not isinstance(handoff, dict) or not handoff.get("url"):
        return candidate   # not a hand-off candidate of this type
    page = urlparse(page_url)
    if not util.same_site(ctx, page_url, handoff["url"]) or urlparse(handoff["url"]).scheme not in ("http", "https"):
        trace.note(f"{NAME}.handoff", page.hostname or "", False, time.monotonic(),
                   f"hand-off address is not on {page.hostname}")
        return None
    found = util.with_browser_fallback(
        lambda cookies, stage: _attempt(ctx, handoff, page_url, cookies, stage),
        page_url, handoff.get("cookie_seed"), bool(handoff.get("browser_fallback")), load_cookies, f"{NAME}.handoff")
    if not found:
        return None
    return {key: value for key, value in {**candidate, "url": found}.items() if key != "handoff"}
