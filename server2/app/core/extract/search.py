"""Search (pure part): find the site's search form on a page (``search_form``) and validate a site map ``search`` rule
(``validate_spec``). The network code that runs a search lives in T09.

Rule keys (site map ``pages.search``; only ``url`` is mandatory, plus ``row_selector``/``fields`` for html)::

    url: "/?s={query}"          # site path ("/...", "{base}/...") or an absolute URL on the SAME host
    method: GET                 # GET | POST
    form:   {s: "{query}"}      # POST body (urlencoded form); or json: {...}
    headers: {}                 # only User-Agent / Referer / Origin / Accept / X-Requested-With
    fetch: http                 # http | browser (GET + html only)
    format: html                # html | json
    row_selector: "div.card"    # html: one match per result
    fields: {title: {...}, url: {...}, poster?: {...}, year?: {...}}  (site map names)   # json: dotted paths / {path, template}
    results_path: "data.result" # json only
    limit: 20                   # <= 20
    cache_ttl: 300              # 0..3600
"""
from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlsplit

from selectolax.parser import HTMLParser

from ._util import absolute, host_key, path_of

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
_FIELD_NAMES = ("title", "url", "poster", "year")
_REQUIRED_FIELDS = ("title", "url")
_FIELD_SPEC_KEYS = {"selector", "attr", "all", "regex", "split", "index", "then_split", "multi", "cast", "replace",
                    "fallback", "self"}
_JSON_FIELD_KEYS = {"path", "template"}
_CASTS = ("int", "float", "date_tr")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_YEAR = re.compile(r"(?<!\d)(1[89]\d{2}|20\d{2})(?!\d)")
_URL_START = ("/", "{base}", "http://", "https://")


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
    # an unsupported ``cast`` is not an error: ``parse.apply_field`` keeps the raw text (the map validator warns)


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


_SEARCH_NAMES = frozenset({"s", "q", "query", "search", "keyword", "ara", "arama", "term", "k", "aranacak", "kelime"})


def search_form(html_or_tree: Any, base: str) -> Optional[dict]:
    """``{action (path), method, input}`` of the page's first same-host search form, None when there is none."""
    tree = html_or_tree if isinstance(html_or_tree, HTMLParser) else HTMLParser(html_or_tree or "")
    for form in tree.css("form"):
        for field in form.css("input[name]"):
            name = (field.attributes.get("name") or "").strip()
            kind = (field.attributes.get("type") or "text").lower()
            if kind in ("search", "text", "") and (name.lower() in _SEARCH_NAMES or kind == "search"):
                action = absolute(base, form.attributes.get("action") or "") or base
                if host_key(action) != host_key(base):
                    continue
                method = (form.attributes.get("method") or "get").upper()
                return {"action": path_of(action), "method": method if method in ("GET", "POST") else "GET", "input": name}
    return None


def rule_from_form(form: dict) -> Optional[dict]:
    """A GET search rule skeleton (``url`` only) from ``search_form``'s answer; None for a POST form (needs a body)."""
    if not form or form.get("method") != "GET":
        return None
    action = form.get("action") or "/"
    sep = "&" if "?" in action else "?"
    return {"url": f"{action}{sep}{form['input']}={{query}}", "method": "GET"}
