"""``player_page``: open the site's own player page and read the media URLs out of it with declarative rules.

For players the site hosts itself (``<iframe src="/player/oynat/<hash>">``) that no provider module knows. The page can
be fetched over plain HTTP or through the browser engine (Cloudflare / JS-built players), nested iframes can be
followed (``follow``) and the media URL is found by ``extract`` rules (regex / CSS / JSON path, optional p.a.c.k.e.r.
unpacking and base64). No code is ever executed: the rules are data, the JS unpacker only substitutes dictionary words.

Safety: the player page and every ``follow`` / ``verify`` URL goes through :func:`app.netguard.check_url` (a yaml written
by an LLM must not make the server fetch internal addresses); plain-HTTP redirects are re-checked on every hop.
"""
from __future__ import annotations

import base64
import binascii
import html as html_lib
import json
import re
import time
from types import SimpleNamespace
from typing import Any, Optional
from urllib.parse import urlparse

from selectolax.parser import HTMLParser

from ... import config as app_config, netguard
from . import _unpack, _util as util
from ..providers import trace

NAME = "player_page"
DESCRIPTION = ("Opens the player page the detail page points to (typically an iframe on the site's own host), over plain "
               "HTTP or through the browser for Cloudflare / JS-built players, optionally follows nested iframes, and "
               "finds the media URLs in it with declarative extract rules (regex, CSS, JSON path, p.a.c.k.e.r. "
               "unpacking, base64). No code is written. Reusable players belong in the provider library (a data-driven provider recipe, "
               "found by host; the same rules): use player_page only for a player that is specific to this one site.")
PARAMS = {
    "selector": {"type": "str", "required": True, "default": None,
                 "help": "CSS selector of the element on the detail page that carries the player URL, e.g. "
                         "'iframe[src*=\"/player/\"]'."},
    "attr": {"type": "str", "required": False, "default": "src",
             "help": "Attribute holding the player URL (relative URLs are made absolute)."},
    "host_regex": {"type": "str", "required": False, "default": None,
                   "help": "Keep only player URLs whose whole hostname matches this regex (full match, case-insensitive)."},
    "label": {"type": "str", "required": False, "default": "Player",
              "help": "Candidate label; it is also the provider name shown for the streams."},
    "lang": {"type": "str", "required": False, "default": None,
             "help": "Constant language code of the candidates, e.g. 'tr'."},
    "fetch": {"type": "str", "required": False, "default": "http",
              "help": "How the player page is fetched: 'http' (HTTP request with a Chrome TLS fingerprint: passes "
                      "Cloudflare's TLS check and sends the Referer / headers / cookies) or 'browser' (browser engine; "
                      "only for pages built by JavaScript, it cannot send a Referer). Independent of the site's own "
                      "fetch_mode."},
    "wait_for": {"type": "str", "required": False, "default": None,
                 "help": "CSS selector the browser waits for before reading the page (only with fetch: browser)."},
    "referer": {"type": "str", "required": False, "default": "{page_url}",
                "help": "Referer header for the player page (only fetch: http, sent with the Chrome TLS fingerprint); "
                        "'{page_url}' is the detail page, '{base}' the site base URL. Players often answer 404 / 403 "
                        "without the detail page as referer."},
    "headers": {"type": "dict", "required": False, "default": None,
                "help": "Extra request headers {name: value} (only fetch: http, merged over the Chrome defaults); values "
                        "accept '{page_url}' and '{base}'."},
    "warm_session": {"type": "bool", "required": False, "default": False,
                     "help": "Only fetch: http. true = GET the detail page first in the same HTTP session so the cookies "
                             "the site sets (PHPSESSID, cf_clearance, ...) go along with the player request (and the "
                             "follow hops). One extra request: use it only when the player refuses a cookie-less "
                             "request."},
    "follow": {"type": "list", "required": False, "default": [],
               "help": "Up to 2 extra hops for nested iframes: each {selector, attr: src, regex?} opens the first element "
                       "of the fetched page whose URL (and, when given, regex search on that URL) matches, and "
                       "continues there."},
    "extract": {"type": "list", "required": True, "default": None,
                "help": "1-8 rules tried in order, all finds merged; each is a mapping with exactly one of regex "
                        "(group: capture group, default 1), css (+ attr, default src) or json_path (dotted path, '[*]' "
                        "for lists), plus optional unpack (p.a.c.k.e.r. JS), base64, unescape (default true), type "
                        "(auto|hls|mp4), quality, label, and for a regex rule quality_group / label_group (capture group "
                        "numbers whose text becomes the stream's quality / label: one regex over a sources list "
                        "[{file, label}] gives one stream per entry). Leave type on auto (default): an unmistakable URL "
                        "extension (.m3u8 = hls, .mp4 / .webm = mp4) always wins over a declared type, a URL without an "
                        "extension takes the declared type (mp4 when auto). Streams whose quality came from a "
                        "quality_group are ordered best (highest number) first, unlabelled / auto last; duplicate URLs "
                        "are kept once. Relative URLs are resolved against the player page."},
    "verify": {"type": "bool", "required": False, "default": False,
               "help": "Probe every found stream URL with a 1-byte request and drop the unreachable ones."},
    "stream_headers": {"type": "dict", "required": False, "default": None, "help": util.STREAM_HEADERS_HELP},
    "cache_ttl": {"type": "int", "required": False, "default": None, "help": util.CACHE_TTL_HELP},
}

MAX_BODY = 3_000_000
MAX_RULES = 8
MAX_FOLLOW = 2
MAX_STREAMS = 12
MAX_MATCHES = 40
MAX_REGEX = 500
MAX_LABEL = 40
FETCH_TIMEOUT = 12.0

_FETCH_MODES = ("http", "browser")
_RULE_KEYS = {"regex", "group", "css", "attr", "json_path", "unpack", "base64", "unescape", "type", "quality", "label",
              "quality_group", "label_group"}
_FOLLOW_KEYS = {"selector", "attr", "regex"}
_RULE_TYPES = ("auto", "hls", "mp4")
_FORBIDDEN_HEADERS = {"host", "content-length", "transfer-encoding", "connection"}
_NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)[+*{]")
_CHALLENGE = re.compile(r"just a moment|cf-browser-verification|challenge-platform|cf_chl_|attention required", re.I)
_ENTITY = re.compile(r"&(?:#\d+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);")
_PATH_PART = re.compile(r"^([^\[\]]*)((?:\[(?:\*|\d+)\])*)$")


# --- validation --------------------------------------------------------------------------------------------------
def _bad_regex(pattern: str) -> Optional[str]:
    if len(pattern) > MAX_REGEX:
        return f"regex longer than {MAX_REGEX} characters"
    try:
        re.compile(pattern)
    except re.error as exc:
        return f"invalid regex ({exc})"
    if _NESTED_QUANTIFIER.search(pattern):
        return "regex has a quantified group containing a quantifier, e.g. '(a+)+' (catastrophic backtracking); rewrite it"
    return None


def _bad_css(selector: str) -> bool:
    try:
        HTMLParser("").css(selector)
    except Exception:
        return True
    return False


def _check_rule(index: int, rule: Any) -> list[str]:
    prefix = f"extract[{index}]"
    if not isinstance(rule, dict):
        return [f"{prefix}: must be a mapping (got {type(rule).__name__})"]
    errors = [f"{prefix}: unknown key '{key}' (allowed: {', '.join(sorted(_RULE_KEYS))})" for key in rule if key not in _RULE_KEYS]
    sources = [key for key in ("regex", "css", "json_path") if rule.get(key) not in (None, "")]
    if len(sources) != 1:
        errors.append(f"{prefix}: needs exactly one of regex / css / json_path (got {', '.join(sources) or 'none'})")
    for key in ("regex", "css", "json_path", "attr", "label"):
        if rule.get(key) not in (None, "") and not isinstance(rule[key], str):
            errors.append(f"{prefix}: '{key}' must be a string")
    for key in ("unpack", "base64", "unescape"):
        if key in rule and not isinstance(rule[key], bool):
            errors.append(f"{prefix}: '{key}' must be true or false")
    if "quality" in rule and (isinstance(rule["quality"], bool) or not isinstance(rule["quality"], (str, int))):
        errors.append(f"{prefix}: 'quality' must be a string or number")
    if rule.get("type") not in (None, *_RULE_TYPES):
        errors.append(f"{prefix}: 'type' must be one of {', '.join(_RULE_TYPES)}")
    pattern = rule.get("regex")
    if isinstance(pattern, str) and pattern:
        problem = _bad_regex(pattern)
        if problem:
            errors.append(f"{prefix}: {problem}")
        else:
            groups = re.compile(pattern).groups
            for key in ("group", "quality_group", "label_group"):
                number = rule.get(key)
                if number is not None and (isinstance(number, bool) or not isinstance(number, int) or number < 0):
                    errors.append(f"{prefix}: '{key}' must be a non-negative integer")
                elif isinstance(number, int) and number > groups:
                    errors.append(f"{prefix}: {key} {number} but the regex has {groups} capture group(s)")
    else:
        errors += [f"{prefix}: '{key}' only applies to a regex rule" for key in ("group", "quality_group", "label_group")
                   if key in rule]
    if isinstance(rule.get("css"), str) and rule["css"] and _bad_css(rule["css"]):
        errors.append(f"{prefix}: invalid CSS selector {rule['css']!r}")
    return errors


def _check_follow(index: int, hop: Any) -> list[str]:
    prefix = f"follow[{index}]"
    if not isinstance(hop, dict):
        return [f"{prefix}: must be a mapping (got {type(hop).__name__})"]
    errors = [f"{prefix}: unknown key '{key}' (allowed: selector, attr, regex)" for key in hop if key not in _FOLLOW_KEYS]
    selector = hop.get("selector")
    if not isinstance(selector, str) or not selector:
        errors.append(f"{prefix}: missing 'selector'")
    elif _bad_css(selector):
        errors.append(f"{prefix}: invalid CSS selector {selector!r}")
    if hop.get("attr") not in (None, "") and not isinstance(hop["attr"], str):
        errors.append(f"{prefix}: 'attr' must be a string")
    pattern = hop.get("regex")
    if pattern not in (None, ""):
        problem = _bad_regex(pattern) if isinstance(pattern, str) else "'regex' must be a string"
        if problem:
            errors.append(f"{prefix}: {problem}")
    return errors


def check(params: dict) -> list[str]:
    """Semantic checks beyond the generic schema (called by ``resolvers.validate``)."""
    errors: list[str] = []
    mode = params.get("fetch")
    if isinstance(mode, str) and mode not in _FETCH_MODES:
        errors.append("parameter 'fetch' must be 'http' or 'browser'")
    browser = mode == "browser"
    if browser and params.get("warm_session") is True:
        errors.append("parameter 'warm_session' only applies with fetch: http")
    if params.get("wait_for") not in (None, ""):
        if not browser:
            errors.append("parameter 'wait_for' only applies with fetch: browser")
        elif isinstance(params["wait_for"], str) and _bad_css(params["wait_for"]):
            errors.append(f"parameter 'wait_for': invalid CSS selector {params['wait_for']!r}")
    if browser:
        for name in ("referer", "headers"):
            if params.get(name) not in (None, "", {}):
                errors.append(f"parameter '{name}' only applies with fetch: http")
    headers = params.get("headers")
    if isinstance(headers, dict):
        for name, value in headers.items():
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9-]+", name):
                errors.append(f"parameter 'headers': invalid header name {name!r}")
            elif name.lower() in _FORBIDDEN_HEADERS:
                errors.append(f"parameter 'headers': '{name}' cannot be set")
            elif isinstance(value, bool) or not isinstance(value, (str, int, float)) or re.search(r"[\r\n]", str(value)):
                errors.append(f"parameter 'headers': value of '{name}' must be single-line text")
    errors += util.check_stream_headers(params)
    errors += util.check_cache_ttl(params)
    follow = params.get("follow")
    if isinstance(follow, list):
        if len(follow) > MAX_FOLLOW:
            errors.append(f"parameter 'follow': at most {MAX_FOLLOW} hops (got {len(follow)})")
        for index, hop in enumerate(follow[:MAX_FOLLOW]):
            errors += _check_follow(index, hop)
    rules = params.get("extract")
    if isinstance(rules, list):
        if not rules:
            errors.append("parameter 'extract': needs at least one rule")
        elif len(rules) > MAX_RULES:
            errors.append(f"parameter 'extract': at most {MAX_RULES} rules (got {len(rules)})")
        for index, rule in enumerate(rules[:MAX_RULES]):
            errors += _check_rule(index, rule)
    return errors


# --- discover ----------------------------------------------------------------------------------------------------
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
        candidate = {"url": url, "label": util.pick_label(node, p, "Player")}
        if p["fetch"] == "browser":
            # a browser session takes 10+ s: live playback (videos._run_candidates) gives this candidate a longer budget
            candidate["timeout"] = app_config.RESOLVE_BROWSER_TIMEOUT
        found.append(util.with_language(candidate, node, p))
    return found


# --- extraction --------------------------------------------------------------------------------------------------
def _unescape_js(text: str) -> str:
    """``\\/`` -> ``/``, ``\\uXXXX`` / ``\\xXX``, and HTML entities that end in ``;`` (``&amp;``; a bare ``&copy=1``
    inside a URL query is left alone)."""
    text = text.replace("\\/", "/")
    text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)
    text = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), text)
    return _ENTITY.sub(lambda m: html_lib.unescape(m.group(0)), text)


def _b64(text: str) -> str:
    """Decoded text of a base64 value (standard or URL-safe alphabet, padding optional); '' when it is no URL-ish text."""
    raw = re.sub(r"\s+", "", text)
    if not raw or len(raw) > 4096:
        return ""
    for alt in ((b"-_",) if re.search(r"[-_]", raw) else (None, b"-_")):
        try:
            decoded = base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=alt)
        except (binascii.Error, ValueError):
            continue
        out = decoded.decode("utf-8", "ignore").strip()
        if out.startswith(("http://", "https://", "//", "/")):
            return out
    return ""


def _json_values(body: str, path: str) -> list[str]:
    try:
        data = json.loads(body.lstrip("﻿"))
    except ValueError:
        return []
    nodes: list[Any] = [data]
    for part in path.split("."):
        found = _PATH_PART.match(part.strip())
        if not found:
            return []
        name, indexes = found.group(1), re.findall(r"\[(\*|\d+)\]", found.group(2))
        if name:
            nodes = [node[name] for node in nodes if isinstance(node, dict) and name in node]
        for index in indexes:
            if index == "*":
                nodes = [item for node in nodes if isinstance(node, list) for item in node]
            else:
                nodes = [node[int(index)] for node in nodes if isinstance(node, list) and int(index) < len(node)]
    out: list[str] = []
    for node in nodes:
        if isinstance(node, str):
            out.append(node)
        elif isinstance(node, list):
            out += [item for item in node if isinstance(item, str)]
    return out


def _values(rule: dict, text: str) -> list[tuple[str, str, str]]:
    """What one rule finds in ``text`` as ``(raw value, quality text, label text)`` (not yet unescaped / absolute); the
    quality / label text is only filled by a regex rule's ``quality_group`` / ``label_group`` (``""`` otherwise)."""
    if rule.get("json_path"):
        return [(v, "", "") for v in _json_values(text, rule["json_path"])[:MAX_MATCHES]]
    if rule.get("css"):
        attr = rule.get("attr") or "src"
        out = []
        for node in HTMLParser(text).css(rule["css"]):
            value = node.text(strip=True) if attr == "text" else node.attributes.get(attr)
            if value:
                out.append((value, "", ""))
            if len(out) >= MAX_MATCHES:
                break
        return out
    out = []
    pattern = re.compile(rule["regex"])
    group = rule.get("group")
    if group is None:
        group = 1 if pattern.groups else 0
    for match in pattern.finditer(text):
        value = match.group(group) if group <= pattern.groups else match.group(0)
        if value:
            out.append((value, _group_text(match, rule.get("quality_group")), _group_text(match, rule.get("label_group"))))
        if len(out) >= MAX_MATCHES:
            break
    return out


def _group_text(match, number) -> str:
    """The text of capture group ``number`` of ``match`` (single line, clipped); ``""`` when there is none / it did not take part."""
    if not isinstance(number, int) or isinstance(number, bool) or number > match.re.groups:
        return ""
    return " ".join((match.group(number) or "").split())[:MAX_LABEL]


def _clean(value: str, rule: dict, base: str) -> str:
    text = value.strip()
    if rule.get("unescape", True):
        text = _unescape_js(text)
    if rule.get("base64"):
        text = _b64(text)
        if text and rule.get("unescape", True):
            text = _unescape_js(text)
    url = util.absolute(base, text)
    return url if url and len(url) <= 2048 and not re.search(r"[\s\\]", url) else ""


def _media_type(url: str, rule: dict) -> tuple[Optional[str], str]:
    """``(type, note)`` of a stream URL. An unmistakable extension decides (``.m3u8`` = hls, ``.mp4`` / ``.m4v`` / ``.webm`` =
    mp4) even against the declared ``type``: the player must be given what the URL is (``note`` then says so). A URL without
    one takes the declared type (``mp4`` for ``auto``). ``(None, note)`` = a format the clients cannot play (DASH ``.mpd``)."""
    declared = rule.get("type") or "auto"
    path = urlparse(url).path.lower()
    if ".m3u8" in path:
        actual, shown = "hls", "m3u8"
    elif path.endswith(".mpd"):
        return None, "skipped a DASH (.mpd) stream: not supported"
    elif path.endswith((".mp4", ".m4v", ".webm")):
        actual, shown = "mp4", path.rsplit(".", 1)[-1]
    else:
        return (declared if declared in ("hls", "mp4") else "mp4"), ""
    if declared in ("hls", "mp4") and declared != actual:
        return actual, f"declared {declared}, url is {shown} -> {actual}"
    return actual, ""


_QUALITY_WORDS = {"uhd": 2160, "4k": 2160, "fullhd": 1080, "fhd": 1080, "hd": 720, "sd": 480}


def _quality_number(text: str) -> int:
    """The vertical resolution a quality text names (``720p`` / ``720`` / ``1920x1080`` / ``4K`` / ``HD``); 0 = unknown."""
    low = (text or "").lower()
    found = re.search(r"\d{3,4}\s*[x\u00d7]\s*(\d{3,4})", low) or re.search(r"(?<!\d)(\d{3,4})\s*p?(?!\d)", low)
    if found:
        return int(found.group(1))
    word = re.sub(r"[^a-z0-9]", "", low)
    return _QUALITY_WORDS.get(word, 0)


def extract(body: str, base: str, rules: list[dict], notes: Optional[list] = None) -> list[dict]:
    """The streams ``[{url, type, quality, label}]`` the ``rules`` find in ``body`` (de-duplicated, at most
    :data:`MAX_STREAMS`). Order: the rule order; but as soon as one stream got its quality from a ``quality_group`` the
    list is sorted best quality first (highest number; ``auto`` / unlabelled last, stable). Pure function: no network;
    warnings (a URL that contradicts its declared type, a skipped DASH stream) are appended to ``notes`` when given."""
    streams: list[dict] = []
    index: dict[str, dict] = {}
    unpacked: Optional[list[str]] = None
    page_quality = False
    for rule in rules:
        text = body
        if rule.get("unpack"):
            if unpacked is None:
                unpacked = _unpack.unpack(body)
            if unpacked:
                text = body + "\n" + "\n".join(unpacked)
        for value, found_quality, found_label in _values(rule, text):
            url = _clean(value, rule, base)
            if not url:
                continue
            known = index.get(url)
            if known is not None:   # the same URL again: only a real quality improves an unlabelled earlier find
                if found_quality and known.get("quality") == "auto":
                    known["quality"], known["label"] = found_quality, found_label or found_quality
                    page_quality = True
                continue
            kind, note = _media_type(url, rule)
            if note and notes is not None and note not in notes:
                notes.append(note)
            if kind is None:
                index[url] = {}   # a skipped URL stays skipped when another rule finds it again
                continue
            quality = found_quality or str(rule.get("quality") or "auto")
            page_quality = page_quality or bool(found_quality)
            stream = {"url": url, "type": kind, "quality": quality, "label": found_label or found_quality or str(rule.get("label") or quality)}
            index[url] = stream
            streams.append(stream)
            if len(streams) >= MAX_STREAMS:
                break
        if len(streams) >= MAX_STREAMS:
            break
    if page_quality:
        streams.sort(key=lambda s: (0, -_quality_number(s["quality"])) if _quality_number(s["quality"]) else (1, 0))
    return streams


# --- fetching ----------------------------------------------------------------------------------------------------
def _allowed(url: str) -> bool:
    try:
        netguard.check_url(url)
        return True
    except ValueError:
        return False


def _load(ctx, p: dict, url: str, referer: str, page_url: str, stage: str, session=None) -> Optional[str]:
    """The page body of ``url`` (None on any failure, which is traced). SSRF-checked first; HTTP redirects are
    re-checked on every hop. ``session`` = the shared Chrome-impersonating HTTP session (``warm_session``)."""
    host = util.host_of(url)
    started = time.monotonic()
    try:
        netguard.check_url(url)
    except ValueError as exc:
        trace.note(stage, host, False, started, f"blocked: {exc}")
        return None
    try:
        if p["fetch"] == "browser":
            body = ctx.fetch.browser_page(ctx.cfg, url, wait_for=p["wait_for"] or "")
        else:
            headers = {"Referer": referer}
            for name, value in (p["headers"] or {}).items():
                headers[str(name)] = util.expand(str(value).replace("{page_url}", page_url), ctx, page_url)
            body = ctx.fetch.fetch_impersonated(url, headers=headers, timeout=FETCH_TIMEOUT, max_bytes=MAX_BODY,
                                                max_redirects=3, allow=_allowed, session=session)
    except Exception as exc:
        trace.note(stage, host, False, started, exc)
        return None
    if isinstance(body, (bytes, bytearray)):
        body = bytes(body).decode("utf-8", "replace")
    if not isinstance(body, str) or not body.strip():
        trace.note(stage, host, False, started, "empty page")
        return None
    trace.note(stage, host, True, started)
    return body[:MAX_BODY]


def _follow(body: str, page_url: str, hop: dict) -> str:
    """The first URL of the elements ``hop`` selects (matching its ``regex`` when given)."""
    pattern = re.compile(hop["regex"]) if hop.get("regex") else None
    for node in HTMLParser(body).css(hop["selector"]):
        url = util.absolute(page_url, node.attributes.get(hop.get("attr") or "src"))
        if url and (pattern is None or pattern.search(url)):
            return url
    return ""


def _warm(ctx, page_url: str, session) -> None:
    """``warm_session``: one GET of the detail page in the shared session, so its cookies reach the player request.
    A failure is traced and ignored (the player request may still work without cookies)."""
    started = time.monotonic()
    host = util.host_of(page_url)
    try:
        netguard.check_url(page_url)
        ctx.fetch.fetch_impersonated(page_url, headers={}, timeout=FETCH_TIMEOUT, max_bytes=MAX_BODY, max_redirects=3,
                                     allow=_allowed, session=session)
    except Exception as exc:
        trace.note(f"{NAME}.warm", host, False, started, exc)
        return
    trace.note(f"{NAME}.warm", host, True, started)


@util.guard(None)
def resolve_candidate(ctx, candidate, page_url, params, load_cookies):
    p = util.with_defaults(PARAMS, params)
    session = None
    try:
        if p["fetch"] != "browser" and p["warm_session"]:
            session = ctx.fetch.impersonated_session()
            _warm(ctx, page_url, session)
        return _resolve(ctx, candidate, page_url, p, session)
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass


def _resolve(ctx, candidate, page_url: str, p: dict, session) -> Optional[dict]:
    url = util.absolute(page_url, candidate.get("url"))
    if not url:
        trace.note(f"{NAME}.fetch", util.host_of(page_url), False, time.monotonic(), "candidate has no player URL")
        return None
    referer = util.expand(str(p["referer"] or "{page_url}").replace("{page_url}", page_url), ctx, page_url)
    stream, inner = _fetch_and_extract(ctx, url, page_url, p, referer, session, handoff=True)
    if stream is not None:
        return {**candidate, "stream": stream}
    if inner:
        return {**candidate, "url": inner}   # the registry carries on with a known provider
    return None


@util.guard(None)
def resolve_player(url: str, params: dict, *, referer: str, fetch_api, base_url: str = "", cfg=None) -> Optional[dict]:
    """The core of this type without a site yaml: open the player page ``url``, apply ``follow`` / ``extract`` of ``params``
    (the parameters of a ``player_page`` item that matter for a player URL already known: ``fetch``, ``referer``, ``headers``,
    ``warm_session``, ``wait_for``, ``follow``, ``extract``, ``verify``, ``stream_headers``, ``cache_ttl`` and ``label`` = the
    provider name of the streams) and return the registry stream ``{url, type, quality, duration, provider, streams}`` (None = no stream; the
    stages are traced exactly like :func:`resolve_candidate`). ``referer`` is the page the player is embedded in (the detail /
    episode page; ``{page_url}`` of the templates); ``fetch_api`` the transport (the ``app.scraper.fetch`` module or a fake with
    ``fetch_impersonated`` / ``browser_page`` / ``impersonated_session`` / ``reachable``); ``base_url`` = ``{base}`` (default:
    the origin of ``referer``). A nested iframe is never handed on: use ``follow``. Used by the data-driven provider recipes
    (``providers/recipes.py``)."""
    p = util.with_defaults(PARAMS, params)
    page_url = referer or ""
    ctx = SimpleNamespace(site_id="", base_url=base_url or "", cfg=cfg, fetch=fetch_api)
    session = None
    try:
        if p["fetch"] != "browser" and p["warm_session"] and page_url:
            session = fetch_api.impersonated_session()
            _warm(ctx, page_url, session)
        header = util.expand(str(p["referer"] or "{page_url}").replace("{page_url}", page_url), ctx, page_url)
        if not header:   # no referring page known: the player's own origin is what a browser would send to its own scripts
            parts = urlparse(url)
            header = f"{parts.scheme}://{parts.netloc}/"
        stream, _inner = _fetch_and_extract(ctx, url, page_url, p, header, session, handoff=False)
        return stream
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass


def _fetch_and_extract(ctx, url: str, page_url: str, p: dict, referer: str, session, handoff: bool) -> tuple[Optional[dict], str]:
    """Fetch the player page, follow the hops, run the rules. ``(stream, "")`` on success (the registry stream dict), else
    ``(None, inner iframe URL)`` when ``handoff`` and the page only holds another player to carry on with, else ``(None, "")``."""
    body = _load(ctx, p, url, referer, page_url, f"{NAME}.fetch", session)
    if body is None:
        return None, ""
    for step, hop in enumerate(list(p["follow"] or [])[:MAX_FOLLOW], start=1):
        stage = f"{NAME}.follow{step}"
        started = time.monotonic()
        nxt = _follow(body, url, hop)
        if not nxt:
            trace.note(stage, util.host_of(url), False, started, "no element matches the follow selector")
            return None, ""
        body = _load(ctx, p, nxt, url, page_url, stage, session)   # a nested iframe is requested the way a browser does: parent as referer
        if body is None:
            return None, ""
        url = nxt

    started = time.monotonic()
    notes: list[str] = []
    streams = extract(body, url, list(p["extract"] or [])[:MAX_RULES], notes)
    host = util.host_of(url)
    for text in notes:   # warnings that do not fail the stage (the stage name carries the text: events keep an error only when not ok)
        trace.note(f"{NAME}.type: {text}", host, True, started)
    if streams and p["verify"]:
        found = len(streams)
        streams = [stream for stream in streams if _allowed(stream["url"]) and ctx.fetch.reachable(stream["url"])]
        if not streams:
            trace.note(f"{NAME}.verify", host, False, started,
                       f"none of {found} stream(s) answered the 1-byte verify request")
            return None, ""
        trace.note(f"{NAME}.verify", host, True, started)
    if streams:
        trace.note(f"{NAME}.extract", host, True, started)
        best = streams[0]
        # stream_headers: what the media host wants when the FILE is fetched; the streams carry them as request_headers
        streams = util.with_request_headers(streams, util.stream_request_headers(ctx, p, page_url, url))
        return util.with_cache_ttl({"url": best["url"], "type": best["type"], "quality": best["quality"], "duration": 0,
                                    "provider": str(p["label"] or "Player"), "streams": streams}, p), ""
    node = HTMLParser(body).css_first("iframe[src]")
    inner = util.absolute(url, node.attributes.get("src")) if node is not None else ""
    if handoff and inner and inner != url and _allowed(inner):
        trace.note(f"{NAME}.iframe", host, True, started)
        return None, inner
    reason = "no extract rule found a media URL" + (f" ({'; '.join(notes)})" if notes else "")
    if not handoff and inner and inner != url:
        reason += f"; the page holds an iframe to {util.host_of(inner)}: add a follow hop for it"
    if _CHALLENGE.search(body[:200_000]):
        reason += "; the page looks like a Cloudflare challenge" + ("" if p["fetch"] == "browser" else " (try fetch: browser)")
    trace.note(f"{NAME}.extract", host, False, started, reason)
    return None, ""
