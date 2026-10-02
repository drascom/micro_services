"""Generic live catalogue search, driven only by the yaml ``search:`` block (no site code).

``site_search.search`` uses this engine for every site that has no registered adapter module of its own (yabancidizi
keeps its module) and whose yaml carries a valid ``search:`` block. The result items have exactly the shape of the
module adapters: ``{title, detail_url (absolute, same host as base_url), poster_url (absolute or None), year (int or
None), genres: []}``.

Spec keys (yaml ``search:``; only ``url`` is mandatory, plus ``row_selector``/``fields`` for html)::

    url: "/?s={query}"          # site path ("/...", "{base}/...") or an absolute URL on the SAME host; {query} is URL-encoded
                                #   (quote_plus after a "?", plain %-encoding inside the path)
    method: GET                 # GET | POST
    form:   {s: "{query}"}      # POST body (urlencoded form); or json: {...}; {query} is the raw text there
    headers: {}                 # only User-Agent / Referer / Origin / Accept / X-Requested-With; {base} and {query} templates
    fetch: http                 # http (Chrome TLS fingerprint, POST shares one cookie session) | browser (GET + html only)
    format: html                # html | json
    row_selector: "div.card"    # html: one match per result
    fields:                     # html: shared parse-engine field specs {title, detail_url, poster_url?, year?}
      title: {selector: "h2"}   # json: dotted paths {title: "name", detail_url: {path: "slug", template: "{base}/dizi/{value}"}}
      detail_url: {selector: "a[href]", attr: href}
    results_path: "data.result" # json: dotted path of the result list (empty/missing = the document itself)
    limit: 20                   # upper bound of results (<= 20)
    cache_ttl: 300              # seconds a query is cached in memory (0 = never, <= 3600)

A site tells the user's query to its search endpoint; nothing here crawls. Safety: every URL goes through
``netguard.check_url`` and ``detail_url`` must be on the host of ``base_url`` (anything else is dropped; a poster may
live on another public host, it is never fetched here), redirects are followed by hand (<= 3 hops, every hop checked),
10 s / 3 MB per request, no retry. A failure raises :class:`SearchError` with a short message.
"""
from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
import threading
import time
from typing import Any, Optional
from urllib.parse import quote, quote_plus, urljoin, urlsplit

from selectolax.parser import HTMLParser

from .. import netguard
from . import fetch, parse

#: every key a ``search`` block may carry (anything else is reported by ``validate_spec``)
SPEC_KEYS = frozenset({"url", "method", "form", "json", "headers", "fetch", "format", "row_selector", "fields",
                       "results_path", "limit", "cache_ttl"})

REQUEST_TIMEOUT = 10.0     # seconds for the whole search (warm-up + request + redirects)
WARMUP_TIMEOUT = 4.0       # share of it the POST cookie warm-up may take
MAX_BYTES = 3_000_000
MAX_REDIRECTS = 3
MAX_RESULTS = 20
MAX_ROWS = 200             # rows looked at in one page (the cap on results is what counts)
MAX_QUERY = 100
MIN_QUERY = 3
DEFAULT_CACHE_TTL = 300
MAX_CACHE_TTL = 3600
_CACHE_ENTRIES = 100

_METHODS = ("GET", "POST")
_FETCHES = ("http", "browser")
_FORMATS = ("html", "json")
_HEADER_NAMES = {"user-agent": "User-Agent", "referer": "Referer", "origin": "Origin", "accept": "Accept",
                 "x-requested-with": "X-Requested-With"}
_FIELD_NAMES = ("title", "detail_url", "poster_url", "year")
_REQUIRED_FIELDS = ("title", "detail_url")
_FIELD_SPEC_KEYS = {"selector", "attr", "all", "regex", "split", "index", "then_split", "multi", "cast", "replace",
                    "fallback", "self"}
_JSON_FIELD_KEYS = {"path", "template"}
_CASTS = ("int", "float", "date_tr")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_YEAR = re.compile(r"(?<!\d)(1[89]\d{2}|20\d{2})(?!\d)")
_URL_START = ("/", "{base}", "http://", "https://")


class SearchError(RuntimeError):
    """A search that could not be run or answered (short message, safe to show)."""


# --- validation -------------------------------------------------------------------------------------------------------

def _selector_ok(selector: Any) -> bool:
    if not isinstance(selector, str) or not selector.strip():
        return False
    try:
        HTMLParser("<div></div>").css(selector)
    except Exception:
        return False
    return True


def _regex_ok(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return "must be a non-empty regex string"
    try:
        re.compile(value)
    except re.error as exc:
        return f"does not compile ({exc})"
    return None


def _validate_html_field(name: str, spec: Any, errs: list[str], where: Optional[str] = None) -> None:
    where = where or f"fields.{name}"
    if not isinstance(spec, dict):
        errs.append(f"{where}: must be a mapping (selector/attr/regex/cast...)")
        return
    for key in spec:
        if key not in _FIELD_SPEC_KEYS:
            errs.append(f"{where}: unknown key {key!r}")
    if "fallback" in spec:
        alternatives = spec["fallback"]
        if not isinstance(alternatives, list) or not alternatives:
            errs.append(f"{where}.fallback: must be a non-empty list of field specs")
        else:
            for i, alt in enumerate(alternatives):
                _validate_html_field(name, alt, errs, f"{where}.fallback[{i}]")
        return
    if not spec.get("self") and not _selector_ok(spec.get("selector")):
        errs.append(f"{where}.selector: required, a valid CSS selector")
    if spec.get("regex") is not None:
        problem = _regex_ok(spec["regex"])
        if problem:
            errs.append(f"{where}.regex: {problem}")
    if spec.get("cast") is not None and spec["cast"] not in _CASTS:
        errs.append(f"{where}.cast: must be one of {', '.join(_CASTS)}")


def _path_ok(path: Any) -> bool:
    """A dotted path (``data.result.0.name``): non-empty segments without blanks, at most 8 deep."""
    if not isinstance(path, str) or not path.strip():
        return False
    parts = path.split(".")
    return len(parts) <= 8 and all(p and not re.search(r"\s", p) for p in parts)


def _validate_json_field(name: str, spec: Any, errs: list[str]) -> None:
    where = f"fields.{name}"
    if isinstance(spec, str):
        if not _path_ok(spec):
            errs.append(f"{where}: must be a dotted path such as 'data.name'")
        return
    if not isinstance(spec, dict):
        errs.append(f"{where}: must be a dotted path or a mapping {{path, template}}")
        return
    errs += [f"{where}: unknown key {key!r}" for key in spec if key not in _JSON_FIELD_KEYS]
    if not _path_ok(spec.get("path")):
        errs.append(f"{where}.path: required, a dotted path such as 'data.name'")
    template = spec.get("template")
    if template is not None and not (isinstance(template, str) and "{value}" in template and len(template) <= 300
                                     and not _CONTROL.search(template)):
        errs.append(f"{where}.template: must be a string containing {{value}} (and optionally {{base}})")


def _strings(value: Any, depth: int = 0) -> list[str]:
    """Every string inside a (nested) form/json value; non-JSON-able content is reported by the caller."""
    if isinstance(value, str):
        return [value]
    if depth >= 5:
        return []
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v, depth + 1)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v, depth + 1)]
    return []


def _json_value_ok(value: Any, depth: int = 0) -> bool:
    if depth > 5:
        return False
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        return all(_json_value_ok(v, depth + 1) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _json_value_ok(v, depth + 1) for k, v in value.items())
    return False


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").rstrip(".").lower()
    except ValueError:
        return ""


def _validate_url(spec: dict, method: str, base_url: Optional[str], errs: list[str]) -> None:
    url = spec.get("url")
    if not isinstance(url, str) or not url.strip():
        errs.append("url: required (a site path such as \"/?s={query}\" or an absolute URL on the site's host)")
        return
    if len(url) > 500 or _CONTROL.search(url) or " " in url:
        errs.append("url: must be one line without spaces (at most 500 characters)")
        return
    if not url.startswith(_URL_START) or url.startswith("//"):
        errs.append("url: must start with '/', '{base}' or http(s)://")
        return
    if url.startswith(("http://", "https://")):
        authority = url.split("://", 1)[1].split("/", 1)[0].split("?", 1)[0]
        if "{" in authority or not authority:
            errs.append("url: the host part must be fixed text (no {query})")
            return
        if base_url and _host(url) != _host(base_url):
            errs.append(f"url: host {_host(url)!r} is not the site's host {_host(base_url)!r}")
    in_url = "{query}" in url
    if method == "GET" and not in_url:
        errs.append("url: a GET search url must contain {query}")


def validate_spec(spec: Any, base_url: Optional[str] = None) -> list[str]:
    """Problems with a ``search`` block (empty list = usable). Messages name the offending key. ``base_url`` (optional)
    lets an absolute ``url`` be checked against the site's host. Used when a site config is loaded (an invalid block
    is logged and skipped) and by the onboarding sandbox."""
    if not isinstance(spec, dict):
        return ["search: must be a mapping"]
    errs = [f"unknown key {key!r}" for key in spec if key not in SPEC_KEYS]

    method = spec.get("method", "GET")
    method = method.upper() if isinstance(method, str) else method
    if method not in _METHODS:
        errs.append("method: must be GET or POST")
        method = "GET"
    fetch_mode = spec.get("fetch", "http")
    if fetch_mode not in _FETCHES:
        errs.append("fetch: must be http or browser")
    fmt = spec.get("format", "html")
    if fmt not in _FORMATS:
        errs.append("format: must be html or json")
        fmt = "html"
    if fetch_mode == "browser" and (method != "GET" or fmt != "html"):
        errs.append("fetch: browser supports only method GET with format html")

    _validate_url(spec, method, base_url, errs)

    form, body_json = spec.get("form"), spec.get("json")
    if form is not None and body_json is not None:
        errs.append("form/json: give only one request body")
    if method == "GET" and (form is not None or body_json is not None):
        errs.append("form/json: only for method POST")
    if form is not None:
        if not isinstance(form, dict) or not form or not all(
                isinstance(k, str) and isinstance(v, (str, int, float)) and not isinstance(v, bool)
                for k, v in form.items()):
            errs.append("form: must be a non-empty mapping of text/number values")
    if body_json is not None and (not isinstance(body_json, dict) or not _json_value_ok(body_json)):
        errs.append("json: must be a mapping of JSON values")
    if method == "POST":
        carriers = [spec.get("url")] + _strings(form) + _strings(body_json)
        if not any(isinstance(s, str) and "{query}" in s for s in carriers):
            errs.append("{query}: a POST search must carry {query} in url, form or json")

    headers = spec.get("headers")
    if headers is not None:
        if not isinstance(headers, dict):
            errs.append("headers: must be a mapping")
        else:
            for name, value in headers.items():
                if not isinstance(name, str) or name.lower() not in _HEADER_NAMES:
                    errs.append(f"headers: {name!r} is not allowed (only {', '.join(_HEADER_NAMES.values())})")
                elif not isinstance(value, str) or not value.strip() or len(value) > 512 or _CONTROL.search(value):
                    errs.append(f"headers.{name}: must be one line of text (at most 512 characters)")

    fields = spec.get("fields")
    if fmt == "html":
        if "results_path" in spec:
            errs.append("results_path: only for format json")
        if not _selector_ok(spec.get("row_selector")):
            errs.append("row_selector: required for format html, a valid CSS selector (one match per result)")
    else:
        if "row_selector" in spec:
            errs.append("row_selector: only for format html (use results_path)")
        path = spec.get("results_path")
        if path not in (None, "") and not _path_ok(path):
            errs.append("results_path: must be a dotted path such as 'data.result'")
    if not isinstance(fields, dict) or not fields:
        errs.append(f"fields: required, a mapping with {' and '.join(_REQUIRED_FIELDS)}")
    else:
        for name in _REQUIRED_FIELDS:
            if name not in fields:
                errs.append(f"fields.{name}: required")
        for name, field_spec in fields.items():
            if name not in _FIELD_NAMES:
                errs.append(f"fields: unknown field {name!r} (known: {', '.join(_FIELD_NAMES)})")
            elif fmt == "html":
                _validate_html_field(name, field_spec, errs)
            else:
                _validate_json_field(name, field_spec, errs)

    limit = spec.get("limit")
    if "limit" in spec and (isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RESULTS):
        errs.append(f"limit: must be a whole number 1..{MAX_RESULTS}")
    ttl = spec.get("cache_ttl")
    if "cache_ttl" in spec and (isinstance(ttl, bool) or not isinstance(ttl, int) or not 0 <= ttl <= MAX_CACHE_TTL):
        errs.append(f"cache_ttl: must be a whole number of seconds 0..{MAX_CACHE_TTL}")
    return errs


# --- request building -------------------------------------------------------------------------------------------------

def _base(cfg) -> str:
    base = str(getattr(cfg, "base_url", "") or "").rstrip("/")
    parts = urlsplit(base) if base else None
    if parts is None or parts.scheme not in ("http", "https") or not parts.hostname:
        raise SearchError("sitenin base_url adresi geçersiz")
    return base


def _spec_of(cfg) -> dict:
    data = getattr(cfg, "data", None)
    spec = data.get("search") if isinstance(data, dict) else getattr(cfg, "search", None)
    if not isinstance(spec, dict) or not spec:
        raise SearchError("sitenin arama tanımı (search:) yok")
    errs = validate_spec(spec, str(getattr(cfg, "base_url", "") or "") or None)
    if errs:
        raise SearchError(("arama tanımı geçersiz: " + "; ".join(errs))[:200])
    return spec


def _sub(text: str, base: str, query: str) -> str:
    return text.replace("{base}", base).replace("{query}", quote_plus(query))


def _sub_body(value: Any, query: str, depth: int = 0) -> Any:
    if isinstance(value, str):
        return value.replace("{query}", query)
    if isinstance(value, dict):
        return {k: _sub_body(v, query, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_sub_body(v, query, depth + 1) for v in value]
    return value


def _request_url(spec: dict, base: str, query: str) -> str:
    template = spec["url"].replace("{base}", base)
    head, sep, tail = template.partition("?")
    head = head.replace("{query}", quote(query, safe=""))   # inside the path a space is %20, not '+'
    tail = tail.replace("{query}", quote_plus(query))
    url = head + sep + tail
    return base + url if url.startswith("/") else url


def _request_headers(spec: dict, base: str, query: str, method: str, fmt: str) -> dict:
    headers = {
        "Accept": "application/json, text/plain, */*" if fmt == "json" else "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "tr,en;q=0.8",
        "Referer": base + "/",
    }
    if method == "POST":
        headers["Origin"] = base
    for name, value in (spec.get("headers") or {}).items():
        headers[_HEADER_NAMES[name.lower()]] = _sub(value, base, query)
    return headers


# --- transport --------------------------------------------------------------------------------------------------------

def _make_allow(base: str):
    """The per-URL / per-redirect-hop guard: a public address (netguard) on the host of ``base_url``."""
    base_host = _host(base)

    def allow(url: str) -> bool:
        if _host(url) != base_host:
            return False
        try:
            netguard.check_url(url)
        except ValueError:
            return False
        return True
    return allow


def _left(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0.2:
        raise SearchError("arama zaman aşımına uğradı")
    return left


def _post_once(sess, url: str, kwargs: dict, headers: dict, timeout: float) -> tuple[int, Any, bytes]:
    """One POST (no redirect following) with the size and time caps of ``fetch.impersonated_get``."""
    chunks: list[bytes] = []
    state = {"size": 0, "over": False, "late": False}
    deadline = time.monotonic() + timeout

    def collect(chunk: bytes) -> int:
        state["size"] += len(chunk)
        if state["size"] > MAX_BYTES:
            state["over"] = True
            return fetch._WRITE_ABORT
        if time.monotonic() > deadline:
            state["late"] = True
            return fetch._WRITE_ABORT
        chunks.append(chunk)
        return len(chunk)

    try:
        resp = sess.post(url, headers=headers, timeout=timeout, allow_redirects=False, content_callback=collect, **kwargs)
    except Exception as exc:   # curl_cffi RequestException / CurlError (a deliberate abort ends here too)
        if state["over"]:
            raise fetch.FetchError(f"response larger than {MAX_BYTES} bytes") from exc
        if state["late"] or getattr(exc, "code", None) == 28:
            raise fetch.FetchError(f"timeout after {timeout:g}s") from exc
        raise fetch.FetchError(f"failed to fetch {url}: {exc}") from exc
    if state["over"]:
        raise fetch.FetchError(f"response larger than {MAX_BYTES} bytes")
    if state["late"]:
        raise fetch.FetchError(f"timeout after {timeout:g}s")
    return int(resp.status_code), resp.headers, b"".join(chunks)


def _http_post(spec: dict, url: str, base: str, headers: dict, query: str, deadline: float, allow) -> fetch.ImpersonatedPage:
    kwargs: dict[str, Any] = {}
    if spec.get("json") is not None:
        kwargs["json"] = _sub_body(spec["json"], query)
    elif spec.get("form") is not None:
        kwargs["data"] = {k: str(_sub_body(v, query)) for k, v in spec["form"].items()}
    sess = fetch.impersonated_session()
    try:
        try:   # one page view first: the site's session / anti-bot cookies belong to the same session as the POST
            fetch.impersonated_get(base + "/", headers={k: v for k, v in headers.items() if k in ("Accept", "Accept-Language")},
                                   timeout=min(WARMUP_TIMEOUT, _left(deadline)), max_bytes=MAX_BYTES,
                                   max_redirects=MAX_REDIRECTS, allow=allow, session=sess)
        except fetch.FetchError:
            pass   # the POST decides: many sites answer it without cookies
        current = url
        for hop in range(MAX_REDIRECTS + 1):
            if not allow(current):
                raise fetch.FetchError(f"host not allowed: {_host(current)}")
            status, reply_headers, body = _post_once(sess, current, kwargs, headers, _left(deadline))
            if status in (301, 302, 303, 307, 308):
                location = reply_headers.get("location")
                if not location:
                    raise fetch.FetchError(f"redirect without location from {_host(current)}", status)
                current = urljoin(current, location)
                if status in (301, 302, 303):   # the browser rule: the answer page is read with a GET
                    return fetch.impersonated_get(current, headers={k: v for k, v in headers.items() if k != "Origin"},
                                                  timeout=_left(deadline), max_bytes=MAX_BYTES,
                                                  max_redirects=max(0, MAX_REDIRECTS - hop - 1), allow=allow, session=sess)
                continue
            if status != 200:
                raise fetch.FetchError(f"HTTP {status} for {current}", status)
            return fetch.ImpersonatedPage(fetch._decode_body(body, reply_headers.get("content-type") or ""), current, status)
        raise fetch.FetchError("too many redirects")
    finally:
        try:
            sess.close()
        except Exception:
            pass


def _fetch_body(cfg, spec: dict, url: str, base: str, query: str, method: str, fmt: str) -> tuple[str, str]:
    """``(body text, final url)`` of the search request."""
    allow = _make_allow(base)
    if not allow(url):
        raise SearchError("arama adresine izin verilmedi (site dışı ya da özel adres)")
    deadline = time.monotonic() + REQUEST_TIMEOUT
    headers = _request_headers(spec, base, query, method, fmt)
    try:
        if spec.get("fetch", "http") == "browser":
            html = fetch.browser_page(cfg, url)
            if not isinstance(html, str):
                html = str(html or "")
            if len(html) > MAX_BYTES:
                raise SearchError("arama yanıtı çok büyük")
            return html, url
        if method == "POST":
            page = _http_post(spec, url, base, headers, query, deadline, allow)
        else:
            page = fetch.impersonated_get(url, headers=headers, timeout=_left(deadline), max_bytes=MAX_BYTES,
                                          max_redirects=MAX_REDIRECTS, allow=allow)
        return page.text, page.url or url
    except SearchError:
        raise
    except fetch.FetchError as exc:
        status = getattr(exc, "status", None)
        message = f"HTTP {status}" if status else " ".join(str(exc).split())[:100]
        raise SearchError(f"arama isteği başarısız: {message}") from exc
    except Exception as exc:   # curl_cffi import problems, browser worker errors, ...
        raise SearchError(f"arama isteği başarısız: {type(exc).__name__}") from exc


# --- parsing ----------------------------------------------------------------------------------------------------------

def _scalar(value: Any) -> Optional[str]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, (str, int, float)):
        text = str(value).strip()
        return text or None
    return None


def _dig(obj: Any, path: str) -> Any:
    for part in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(part)
        elif isinstance(obj, list) and re.fullmatch(r"-?\d+", part):
            index = int(part)
            obj = obj[index] if -len(obj) <= index < len(obj) else None
        else:
            return None
        if obj is None:
            return None
    return obj


def _year(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        value = int(value)
        return value if 1800 <= value <= 2100 else None
    found = _YEAR.search(str(value or ""))
    return int(found.group(1)) if found else None


def _html_rows(html: str, spec: dict) -> list[dict[str, Any]]:
    tree = HTMLParser(html)
    fields = spec["fields"]
    out = []
    for row in tree.css(spec["row_selector"])[:MAX_ROWS]:
        out.append({name: parse.apply_field(row, field_spec) for name, field_spec in fields.items()})
    return out


def _json_rows(text: str, spec: dict, base: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise SearchError("arama geçersiz JSON döndürdü") from exc
    path = spec.get("results_path") or ""
    found = _dig(payload, path) if path else payload
    if not isinstance(found, list):
        return []
    out = []
    for entry in found[:MAX_ROWS]:
        if not isinstance(entry, dict):
            continue
        row: dict[str, Any] = {}
        for name, field_spec in spec["fields"].items():
            path_, template = (field_spec, None) if isinstance(field_spec, str) else (field_spec["path"], field_spec.get("template"))
            value = _dig(entry, path_)
            if name == "year":
                row[name] = value
                continue
            text_value = _scalar(value)
            if text_value is not None and template:
                text_value = template.replace("{base}", base).replace("{value}", text_value)
            row[name] = text_value
        out.append(row)
    return out


def _items(rows: list[dict[str, Any]], page_url: str, base: str, fmt: str, limit: int) -> list[dict]:
    base_host = _host(base)
    checked: dict[str, Optional[str]] = {}

    def url_of(raw: Any, same_host: bool) -> Optional[str]:
        text = _scalar(raw)
        if not text or text.startswith(("#", "javascript:", "data:", "mailto:")):
            return None
        absolute = urljoin(page_url if fmt == "html" else base + "/", text)
        parts = urlsplit(absolute)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return None
        if same_host and parts.hostname.rstrip(".").lower() != base_host:
            return None
        key = f"{parts.scheme}://{parts.netloc}".lower()
        if key not in checked:   # one address check per origin (a result page repeats the same host)
            try:
                checked[key] = netguard.check_url(f"{parts.scheme}://{parts.netloc}/")
            except ValueError:
                checked[key] = None
        if checked[key] is None:
            return None
        try:
            return netguard.check_url(absolute)
        except ValueError:
            return None

    out: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        title = row.get("title")
        title = " ".join(html_lib.unescape(title).split())[:200] if isinstance(title, str) else ""
        detail = url_of(row.get("detail_url"), True)
        if not title or not detail:
            continue
        key = detail.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "title": title,
            "detail_url": detail,
            "poster_url": url_of(row.get("poster_url"), False),
            "year": _year(row.get("year")),
            "genres": [],
        })
        if len(out) >= limit:
            break
    return out


# --- cache + entry point ----------------------------------------------------------------------------------------------

_lock = threading.RLock()
_cache: dict[tuple, tuple[float, list[dict]]] = {}


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _cache_key(cfg, spec: dict, query: str) -> tuple:
    digest = hashlib.sha1(json.dumps(spec, sort_keys=True, default=str).encode()).hexdigest()
    return (str(getattr(cfg, "site_id", "")), str(getattr(cfg, "base_url", "")), digest, query.casefold())


def search(cfg, query: str, limit: int = MAX_RESULTS) -> list[dict]:
    """Live search of one site with its ``search:`` block (``cfg``: a ``SiteConfig``, also an in-memory draft).

    Returns ``[{title, detail_url, poster_url, year, genres}]`` (at most ``limit`` clamped to 1..20 and the block's own
    ``limit``); ``[]`` for a query under 3 characters. Raises :class:`SearchError` for a missing/invalid block, a
    refused address, a failed request or an unreadable answer."""
    text = " ".join(str(query or "").split())[:MAX_QUERY]
    if len(text) < MIN_QUERY:
        return []
    limit = max(1, min(MAX_RESULTS, int(limit)))
    spec = _spec_of(cfg)
    base = _base(cfg)
    cap = min(limit, spec.get("limit", MAX_RESULTS))
    ttl = spec.get("cache_ttl", DEFAULT_CACHE_TTL)
    key = _cache_key(cfg, spec, text)
    if ttl > 0:
        with _lock:
            hit = _cache.get(key)
            if hit and time.time() - hit[0] < ttl:
                return [dict(item, genres=[]) for item in hit[1][:cap]]

    method = str(spec.get("method", "GET")).upper()
    fmt = spec.get("format", "html")
    url = _request_url(spec, base, text)
    body, final_url = _fetch_body(cfg, spec, url, base, text, method, fmt)
    rows = _html_rows(body, spec) if fmt == "html" else _json_rows(body, spec, base)
    items = _items(rows, final_url, base, fmt, spec.get("limit", MAX_RESULTS))

    if ttl > 0:
        with _lock:
            _cache[key] = (time.time(), [dict(item) for item in items])
            if len(_cache) > _CACHE_ENTRIES:   # bound the process cache without a background cleanup task
                for old in sorted(_cache, key=lambda k: _cache[k][0])[:20]:
                    _cache.pop(old, None)
    return items[:cap]
