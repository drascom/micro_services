"""Helpers shared by the resolver types: absolute URLs, label / language reading, host checks, the "light cookie"
seed and the browser-cookie fallback of session hand-offs. Network access only through ``ctx.fetch``
(``fetch_url`` / ``post_url``: the playback transport), every network stage leaves a ``trace.note``.

Matching semantics (shared by every type, so one yaml reads the same everywhere):
- ``*host_regex``: ``re.fullmatch`` on the lower-case HOSTNAME of the URL (``(?:.+\\.)?vidmol{1,2}y\\.[a-z0-9.-]+``).
- ``filter_regex``: ``re.search`` on the label text, case-insensitive (``^okru$``).
"""
from __future__ import annotations

import copy
import functools
import logging
import re
import time
from typing import Any, Callable, Optional
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

from ... import langs, streamproxy
from ..providers import trace

log = logging.getLogger("scraper.resolvers")

# HTTP statuses that mean "the site refuses this client / cookie set" (retry with the browser cookies), never "no".
BLOCKED = (401, 403, 429, 503, 520, 521, 522, 523, 524, 525, 526)


def with_defaults(schema: dict, params: Optional[dict]) -> dict:
    """``params`` with the schema defaults filled in (``None`` values count as absent; extra keys such as the
    item's own ``type`` are kept and ignored by the types)."""
    out = {name: copy.deepcopy(spec.get("default")) for name, spec in schema.items()}
    for name, value in (params or {}).items():
        if value is not None:
            out[name] = value
    return out


def guard(default: Any = None) -> Callable:
    """A resolver type must never take the resolution down: any error (e.g. a regex that slipped past validation)
    is logged and answered with ``default`` (``[]`` for discover, ``None`` for resolve_candidate)."""
    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                log.warning("resolver %s.%s failed: %s", fn.__module__.rsplit(".", 1)[-1], fn.__name__, exc)
                return list(default) if isinstance(default, list) else default
        return wrapper
    return decorate


def passthrough(ctx, candidate, page_url, params, load_cookies):
    """Default ``resolve_candidate``: the candidate needs no hand-off."""
    return candidate


# --- URLs, hosts, regexes ---------------------------------------------------------------------------------------
def host_of(url: str) -> str:
    return (urlparse(url or "").hostname or "").lower()


def absolute(page_url: str, value: Optional[str]) -> str:
    """``value`` resolved against ``page_url``; ``""`` for empty / fragment / non-http(s) values."""
    value = (value or "").strip()
    if not value or value.startswith("#"):
        return ""
    url = urljoin(page_url, value)
    parsed = urlparse(url)
    return url if parsed.scheme in ("http", "https") and parsed.netloc else ""


def base_url(ctx, page_url: str) -> str:
    base = (getattr(ctx, "base_url", "") or "").rstrip("/")
    if base:
        return base
    parsed = urlparse(page_url or "")
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else ""


def expand(template: str, ctx, page_url: str, **values: str) -> str:
    """``{base}`` (site base URL, no trailing slash) and ``{name}`` placeholders; plain replacement, so braces in
    the rest of the template are left alone."""
    out = template.replace("{base}", base_url(ctx, page_url))
    for name, value in values.items():
        out = out.replace("{" + name + "}", value)
    return out


def host_matches(url: str, regex: Optional[str]) -> bool:
    """True when there is no ``regex`` or its full match covers the hostname of ``url``."""
    return not regex or bool(re.fullmatch(regex, host_of(url), re.I))


def same_site(ctx, page_url: str, url: str) -> bool:
    """``url`` lives on the page's own host (or the configured base host): hand-offs never leave the site."""
    host = host_of(url)
    return bool(host) and host in (host_of(page_url), host_of(getattr(ctx, "base_url", "") or ""))


# --- stream request headers (``stream_headers`` of player_page / json_api) --------------------------------------
STREAM_HEADERS_HELP = (
    "Headers the media server insists on when the STREAM FILE is downloaded, {name: value}; only User-Agent, Referer, "
    "Origin and Cookie are accepted, values accept '{page_url}', '{player_url}' and '{base}'. Every stream the type finds "
    "carries them as request_headers: /api/streams then serves a progressive file through the server's signed stream proxy, "
    "and an HLS stream through it too when it is proxied at all (a recipe with stream_proxy: true, a URL bound to the server's "
    "IP, a source that failed for a client and the proxy helped in the server's probe, or the STREAM_PROXY_* env); the proxy "
    "then sends these headers for the playlist and its segments. Use only when the file refuses a plain player "
    "(HTTP 400 / 403 without them).")


def check_stream_headers(params: dict) -> list[str]:
    """Semantic checks of the ``stream_headers`` parameter (names from the stream proxy's allow-list, single-line text)."""
    headers = params.get("stream_headers")
    if not isinstance(headers, dict):
        return []
    errors = []
    allowed = {name.lower() for name in streamproxy.ALLOWED_HEADERS}
    for name, value in headers.items():
        if not isinstance(name, str) or name.strip().lower() not in allowed:
            errors.append(f"parameter 'stream_headers': '{name}' is not allowed (only {', '.join(streamproxy.ALLOWED_HEADERS)})")
        elif isinstance(value, bool) or not isinstance(value, (str, int, float)) or not str(value).strip() \
                or re.search(r"[\r\n]", str(value)):
            errors.append(f"parameter 'stream_headers': value of '{name}' must be single-line text")
    return errors


CACHE_TTL_HELP = (
    "How many seconds the resolved streams of this player may be reused before the server resolves them again (60..86400). "
    "Leave it out unless the stream URLs say nothing about their own lifetime (an `expires=` / `expire=` / `X-Amz-Expires` "
    "query already tells the server) AND the player's links are known to live much longer (or shorter) than the server's "
    "default of about 15 minutes.")
CACHE_TTL_RANGE = (60, 86400)


def check_cache_ttl(params: dict) -> list[str]:
    """Range check of the ``cache_ttl`` parameter (the generic validator already made sure it is an int)."""
    value = params.get("cache_ttl")
    if isinstance(value, int) and not isinstance(value, bool) and not CACHE_TTL_RANGE[0] <= value <= CACHE_TTL_RANGE[1]:
        return [f"parameter 'cache_ttl' must be between {CACHE_TTL_RANGE[0]} and {CACHE_TTL_RANGE[1]} seconds (got {value})"]
    return []


def with_cache_ttl(stream: dict, params: dict) -> dict:
    """``stream`` (the registry stream dict of a resolver) with ``cache_ttl`` set when the parameter is given and valid."""
    value = params.get("cache_ttl")
    if isinstance(value, int) and not isinstance(value, bool) and CACHE_TTL_RANGE[0] <= value <= CACHE_TTL_RANGE[1]:
        return {**stream, "cache_ttl": value}
    return stream


def stream_request_headers(ctx, params: dict, page_url: str, player_url: str = "") -> dict:
    """The ``request_headers`` of the streams a resolver finds: the ``stream_headers`` parameter with ``{page_url}`` /
    ``{player_url}`` / ``{base}`` expanded and cleaned by the stream proxy's rules (``{}`` when there is none)."""
    raw = params.get("stream_headers")
    if not isinstance(raw, dict):
        return {}
    expanded = {name: expand(str(value).replace("{page_url}", page_url).replace("{player_url}", player_url), ctx, page_url)
                for name, value in raw.items()}
    return streamproxy.clean_headers(expanded)


def with_request_headers(streams: list[dict], headers: dict) -> list[dict]:
    """``streams`` with ``request_headers`` set (a copy per stream); unchanged when there are no headers."""
    return [{**stream, "request_headers": dict(headers)} for stream in streams] if headers else streams


# --- label / language -------------------------------------------------------------------------------------------
def node_value(node, source: Optional[str]) -> str:
    """Text of ``node`` (``source`` empty / ``"text"``) or one of its attributes."""
    if not source or source == "text":
        return " ".join(node.text(separator=" ", strip=True).split())
    return (node.attributes.get(source) or "").strip()


def pick_label(node, params: dict, default: str = "") -> str:
    if params.get("label"):
        return str(params["label"])
    return (node_value(node, params["label_from"]) if params.get("label_from") else "") or default


def label_matches(node, params: dict) -> bool:
    """``filter_regex`` against the label text (``label_from``, default text); no filter -> True."""
    pattern = params.get("filter_regex")
    return not pattern or bool(re.search(pattern, node_value(node, params.get("label_from")), re.I))


def read_language(text: str) -> Optional[tuple[str, str]]:
    """``(code, label)`` named by a tab / link text (``"Türkçe Altyazılı İndir"`` -> ``("tr", "Türkçe altyazı")``,
    ``"Türkçe Dublaj"`` -> ``("tr", "Türkçe dublaj")``); None when the text names no language (the language is then
    left out instead of guessed)."""
    code = langs.code(text)
    if not code:
        return None
    kind = "dublaj" if "dublaj" in (text or "").casefold() else "altyazı"
    return code, f"{langs.label(code) or code} {kind}"


def with_language(candidate: dict, node, params: dict) -> dict:
    """``lang`` / ``language`` of a candidate: read from ``lang_from`` when it names a language, else the constant
    ``lang`` code (no ``language`` label then: the yaml states a code, not a tab text)."""
    if params.get("lang_from"):
        found = read_language(node_value(node, params["lang_from"]))
        if found:
            candidate["lang"], candidate["language"] = found
            return candidate
    code = str(params.get("lang") or "").strip().lower()
    if code:
        candidate["lang"] = code
    return candidate


# --- session hand-offs ------------------------------------------------------------------------------------------
def light_cookies(hostname: str, seed: Optional[dict]) -> list[dict]:
    """The cheap cookie set a site's own script would set before a hand-off works (``cookie_seed``): value
    ``"now_ms"`` / ``"now_s"`` = current time in milliseconds / seconds, anything else is taken literally. No
    browser needed (a browser session costs 10+ seconds)."""
    cookies = []
    for name, value in (seed or {}).items():
        text = str(value)
        if text == "now_ms":
            text = str(int(time.time() * 1000))
        elif text == "now_s":
            text = str(int(time.time()))
        cookies.append({"name": str(name), "value": text, "domain": hostname, "path": "/"})
    return cookies


def merge_cookies(primary: list[dict], extra: list[dict]) -> list[dict]:
    """``primary`` plus the ``extra`` cookies whose name ``primary`` does not already carry."""
    known = {cookie.get("name") for cookie in primary}
    return list(primary) + [cookie for cookie in extra if cookie.get("name") not in known]


def cookie_header(cookies: list[dict], hostname: str) -> str:
    pairs = []
    for cookie in cookies:
        name, value = cookie.get("name"), cookie.get("value")
        if not name or value is None:
            continue
        domain = (cookie.get("domain") or "").lstrip(".").lower()
        if domain and hostname != domain and not hostname.endswith("." + domain):
            continue
        pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def status_of(exc: BaseException) -> Optional[int]:
    return getattr(exc, "status", None)


def follow_page(ctx, url: str, page_url: str, cookies: list[dict], expect: Optional[str],
                stage: str) -> tuple[Optional[str], bool]:
    """GET a site hand-off page and take its first ``iframe[src]``. Returns ``(provider_url, retry_with_browser)``;
    ``retry_with_browser`` is set only when the answer looks cookie / Cloudflare related (HTTP block, a page
    without any iframe = the challenge page), never for a definite "no" (an iframe on the wrong host)."""
    host = host_of(page_url)
    started = time.monotonic()
    headers = {"Referer": page_url}
    cookie = cookie_header(cookies, host_of(url))
    if cookie:
        headers["Cookie"] = cookie
    try:
        body = ctx.fetch.fetch_url(url, headers=headers, timeout=10, retries=1, check_robots=False)
    except Exception as exc:
        trace.note(stage, host, False, started, exc)
        return None, status_of(exc) in BLOCKED
    node = HTMLParser(body or "").css_first("iframe[src]")
    if node is None:
        trace.note(stage, host, False, started, "hand-off page has no iframe")
        return None, True
    found = absolute(url, node.attributes.get("src"))
    if not found or not host_matches(found, expect):
        trace.note(stage, host, False, started, f"hand-off page iframe host {host_of(found) or '-'} is not the expected one")
        return None, False
    trace.note(stage, host, True, started)
    return found, False


def with_browser_fallback(attempt: Callable[[list[dict], str], tuple[Optional[str], bool]], page_url: str,
                          seed: Optional[dict], browser_fallback: bool, load_cookies, stage: str) -> Optional[str]:
    """Cheap first: ``attempt(cookies, stage)`` with the light cookies only. When the site refuses that (the
    attempt reports ``retry_with_browser``) and ``browser_fallback`` is on, once more with the browser session's
    cookie jar (``load_cookies``: a browser run, 10+ seconds) merged with the light cookies."""
    host = host_of(page_url)
    light = light_cookies(host, seed)
    result, need_browser = attempt(light, stage)
    if result or not need_browser or not browser_fallback or load_cookies is None:
        return result
    started = time.monotonic()
    try:
        cookies = list(load_cookies())
    except Exception as exc:
        trace.note(stage + ".browser", host, False, started, exc)
        return None
    result, _ = attempt(merge_cookies(cookies, light), stage + ".browser")
    return result
