"""``ajax``: a source button that loads its player with an AJAX call (``admin-ajax.php``-like ``action`` + post id / nonce, or a
``fetch('/x/' + id)`` loader). The parameters come from the button (``attr:``), constants (``const:``) or the page itself
(``page:<regex>``, e.g. a nonce in an inline script); the answer (JSON or HTML) is read generically and decoded."""
from __future__ import annotations

import json
import re
import time
from typing import Any, Optional
from urllib.parse import quote, urlencode, urlparse

from selectolax.parser import HTMLParser

from . import _decode, _util as util
from .. import langs
from .data_attr import candidate_of
from ..providers import trace

NAME = "ajax"
DESCRIPTION = ("For every selected source button (each one = its own alternative) sends one AJAX request (POST form or GET) to a site endpoint. Fields: "
               "'attr:<attribute>' of the button, 'const:<text>', or 'page:<regex>' (group 1 of a regex over the page HTML, "
               "e.g. a nonce). '{field}' placeholders in url take a field's value (that field then leaves the body). The URL is "
               "read from the JSON answer (json_key, else the first URL / iframe inside it) or the HTML answer (first iframe), "
               "then decoded (decode steps). A media file plays as is; a site page is followed to its provider. Optional: site cookies "
               "its JavaScript sets (cookies), outer language tabs loading their own buttons by AJAX (tab_group).")
PARAMS = {
    "selector": {"type": "str", "required": True, "default": None,
                 "help": "CSS selector of the source buttons, e.g. 'li.player-option[data-post]'."},
    "url": {"type": "str", "required": True, "default": None,
            "help": "Endpoint on the site's own host; '{base}' = site base URL, '{name}' = the value of form field 'name', "
                    "e.g. '{base}/wp-admin/admin-ajax.php' or '{base}/ajax/embed/{id}'."},
    "method": {"type": "str", "required": False, "default": "POST", "help": "'POST' (form body) or 'GET' (query string)."},
    "form": {"type": "dict", "required": False, "default": {},
             "help": "Fields {name: 'attr:<attribute>' | 'attr:<attribute>|<default>' | 'const:<text>' | 'page:<regex>'}; a "
                     "button missing a value (and no default) is skipped."},
    "json_key": {"type": "str", "required": False, "default": None,
                 "help": "Dotted key of the JSON answer holding the URL / embed HTML (e.g. 'embed_url'); without it the first "
                         "http(s) URL or iframe found in the answer is used."},
    "decode": {"type": "list", "required": False, "default": [],
               "help": "Decode steps for the found value: base64, hex, urldecode, json, reverse."},
    "expect_host_regex": {"type": "str", "required": False, "default": None,
                          "help": "The provider URL's hostname must match (full match); a URL on the site's own host is "
                                  "opened once and its first iframe[src] taken."},
    "label": {"type": "str", "required": False, "default": None, "help": "Constant candidate label."},
    "label_from": {"type": "str", "required": False, "default": "text",
                   "help": "Where the label comes from: 'text' (button text) or an attribute name."},
    "lang": {"type": "str", "required": False, "default": None, "help": "Constant language code, e.g. 'tr'."},
    "lang_from": {"type": "str", "required": False, "default": None,
                  "help": "Read the language from the button: 'text' or an attribute name."},
    "result": {"type": "str", "required": False, "default": "auto",
               "help": "What the answer value is: 'iframe_url' (a URL; a same-site intermediate page is followed hop by hop to "
                       "the provider), 'html' (an HTML piece: its iframe / URL is taken), 'direct' (a media file URL: played as "
                       "is) or 'auto' (guess)."},
    "cookies": {"type": "list", "required": False, "default": [],
                "help": "Cookies the site's own JavaScript sets before the AJAX call works: [{name, value}], value a literal or "
                        "a generator 'now_ms' (Date.now()) / 'now_s'; generated fresh on every play. Server cookies (Set-Cookie, "
                        "e.g. a session id) are carried between the hops of one play by themselves. A 5xx / empty answer "
                        "without them usually means a missing cookie (search the scripts for document.cookie)."},
    "tab_group": {"type": "dict", "required": False, "default": None,
                  "help": "Outer tabs (e.g. language: dublaj / altyazılı) that load their own set of source buttons by AJAX: "
                          "{selector, url, method, form, json_key (default 'data'), label_from, lang}. Each tab is called, "
                          "'selector' (the buttons) runs on the HTML of its answer; alternative label = '<tab> · <button>', "
                          "language / dub / subtitle hint from the tab text. Without an answer the page's own buttons are used."},
}
RESULTS = ("auto", "iframe_url", "html", "direct")
TAB_KEYS = {"selector", "url", "method", "form", "json_key", "label_from", "lang"}
MAX_TABS = 6
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
MAX_DEPTH = 6


def check(params: dict) -> list[str]:
    errors = _decode.check_steps(params.get("decode"))
    method = params.get("method")
    if isinstance(method, str) and method.upper() not in ("GET", "POST"):
        errors.append("parameter 'method' must be GET or POST")
    form = params.get("form") or {}
    if isinstance(form, dict):
        for name, spec in form.items():
            if not isinstance(spec, str) or not spec.startswith(("attr:", "const:", "page:")) \
                    or (spec.startswith(("attr:", "page:")) and not spec[5:].strip()):
                errors.append(f"parameter 'form': field '{name}' must be 'attr:<attribute>', 'const:<text>' or 'page:<regex>'")
                continue
            if spec.startswith("page:"):
                try:
                    re.compile(spec[5:])
                except re.error as exc:
                    errors.append(f"parameter 'form': field '{name}': invalid regex ({exc})")
    url = params.get("url")
    if isinstance(url, str) and isinstance(form, dict):
        for name in _PLACEHOLDER.findall(url):
            if name != "base" and name not in form:
                errors.append(f"parameter 'url': placeholder '{{{name}}}' is not a form field")
    result = params.get("result")
    if result is not None and result not in RESULTS:
        errors.append(f"parameter 'result' must be one of {', '.join(RESULTS)}")
    if params.get("cookies") is not None:
        from ...fetch import cookies as fetch_cookies
        errors += [f"parameter '{e}" if e.startswith("cookies") else e
                   for e in fetch_cookies.check_specs(params.get("cookies"), "cookies'")]
    tabs = params.get("tab_group")
    if tabs is not None:
        if not isinstance(tabs, dict):
            errors.append("parameter 'tab_group' must be a mapping {selector, url, method, form, json_key, label_from, lang}")
        else:
            for key in tabs:
                if key not in TAB_KEYS:
                    errors.append(f"parameter 'tab_group': unknown key '{key}' (allowed: {', '.join(sorted(TAB_KEYS))})")
            for key in ("selector", "url"):
                if not isinstance(tabs.get(key), str) or not tabs.get(key, "").strip():
                    errors.append(f"parameter 'tab_group': '{key}' is required (text)")
            if isinstance(tabs.get("selector"), str):
                try:
                    HTMLParser("").css(tabs["selector"])
                except Exception:
                    errors.append(f"parameter 'tab_group': invalid CSS selector {tabs['selector']!r}")
            if str(tabs.get("method") or "POST").upper() not in ("GET", "POST"):
                errors.append("parameter 'tab_group': method must be GET or POST")
            if tabs.get("form") is not None and not isinstance(tabs.get("form"), dict):
                errors.append("parameter 'tab_group': form must be a mapping")
            else:
                errors += _form_errors(tabs.get("form") or {}, "tab_group.form")
    return errors


def _form_errors(form, where: str) -> list[str]:
    errors = []
    if not isinstance(form, dict):
        return errors
    for name, spec in form.items():
        if not isinstance(spec, str) or not spec.startswith(("attr:", "const:", "page:")) \
                or (spec.startswith(("attr:", "page:")) and not spec[5:].strip()):
            errors.append(f"parameter '{where}': field '{name}' must be 'attr:<attribute>', 'const:<text>' or 'page:<regex>'")
    return errors


def _value(node, spec: str, page_values: dict) -> Optional[str]:
    if spec.startswith("attr:"):   # 'attr:<name>|<default>': the default when the attribute is missing / empty
        name, _, default = spec[5:].partition("|")
        return (node.attributes.get(name.strip()) or "").strip() or (default if default else None)
    if spec.startswith("const:"):
        return spec[6:]
    return page_values.get(spec)


def _page_values(form_spec: dict, html: str) -> dict:
    out: dict = {}
    for spec in form_spec.values():
        if spec.startswith("page:") and spec not in out:
            hit = re.search(spec[5:], html or "")
            out[spec] = (hit.group(1) if hit and hit.groups() else hit.group(0) if hit else None)
    return out


def tab_hint(text: str) -> dict:
    """What a tab text says about the alternatives under it: ``{sub_mode: dublaj|altyazı|orijinal, lang?, language?,
    audio_lang?}`` (a named language from the text, else the tab group's constant ``lang``)."""
    folded = (text or "").casefold()
    mode = "dublaj" if "dubla" in folded else "altyazı" if "altyaz" in folded or "subtit" in folded else \
        "orijinal" if "orijinal" in folded or "original" in folded else ""
    return {"sub_mode": mode} if mode else {}


@util.guard([])
def discover(ctx, html, page_url, params):
    p = util.with_defaults(PARAMS, params)
    util.seed_cookies(ctx, p["cookies"], page_url)
    tabs = p.get("tab_group")
    if isinstance(tabs, dict) and tabs.get("selector") and tabs.get("url"):
        found = _tabbed(ctx, html, page_url, p, tabs)
        if found:
            return found
    return _buttons(ctx, html, page_url, p, html)


def _tabbed(ctx, html: str, page_url: str, p: dict, tabs: dict) -> list[dict]:
    """Each outer tab's own AJAX call -> the buttons HTML of its answer -> the buttons of that tab (label '<tab> · <button>')."""
    form_spec = {str(k): str(v) for k, v in (tabs.get("form") or {}).items()}
    page_values = _page_values(form_spec, html)
    page = urlparse(page_url)
    headers = {"Accept": "application/json, text/javascript, text/html, */*; q=0.01", "Referer": page_url,
               "Origin": f"{page.scheme}://{page.netloc}", "X-Requested-With": "XMLHttpRequest"}
    out: list[dict] = []
    seen: set = set()
    for node in HTMLParser(html or "").css(str(tabs["selector"]))[:MAX_TABS]:
        values = {name: _value(node, spec, page_values) for name, spec in form_spec.items()}
        if any(v is None for v in values.values()):
            continue
        used = set(_PLACEHOLDER.findall(str(tabs["url"])))
        endpoint = util.absolute(page_url, util.expand(str(tabs["url"]), ctx, page_url,
                                                       **{k: quote(v, safe="") for k, v in values.items() if k in used}))
        if not endpoint or not util.same_site(ctx, page_url, endpoint):
            continue
        form = {k: v for k, v in values.items() if k not in used}
        started = time.monotonic()
        try:
            if str(tabs.get("method") or "POST").upper() == "GET":
                sep = "&" if "?" in endpoint else "?"
                body = util.send(ctx, "GET", endpoint + (sep + urlencode(form) if form else ""), headers=headers)
            else:
                body = util.send(ctx, "POST", endpoint, headers=headers, data=form)
        except Exception as exc:
            trace.note(f"{NAME}.tab", page.hostname or "", False, started, exc)
            continue
        inner = util.first_attributes(_answer_piece(body, str(tabs.get("json_key") or "data")))
        tab_text = util.node_value(node, str(tabs.get("label_from") or "text"))
        if not inner:
            continue
        hint = tab_hint(tab_text)
        lang = util.read_language(tab_text)
        if lang:
            hint["lang"], hint["language"] = lang
        elif tabs.get("lang") and hint.get("sub_mode"):
            code = str(tabs["lang"]).strip().lower()
            hint["lang"], hint["language"] = code, f"{langs.label(code) or code} {hint['sub_mode']}"
        if hint.get("sub_mode") == "dublaj" and hint.get("lang"):
            hint["audio_lang"] = hint["lang"]
        for cand in _buttons(ctx, inner, page_url, p, html):
            marker = json.dumps(cand.get("handoff"), sort_keys=True, default=str)
            if marker in seen:
                continue
            seen.add(marker)
            label = cand.get("label") or ""
            cand = {**cand, **hint, "label": f"{tab_text} · {label}" if tab_text else label, "tab": tab_text}
            out.append(cand)
    return out


def _answer_piece(body: str, key: str) -> str:
    """The HTML / text of a tab answer: ``key`` of a JSON answer, else the whole body (an HTML answer)."""
    try:
        data = json.loads(body)
    except (TypeError, ValueError):
        return body or ""
    piece = _dig(data, key) if key else None
    return piece if isinstance(piece, str) else ""


def _buttons(ctx, html: str, page_url: str, p: dict, page_html: str) -> list[dict]:
    form_spec = {str(k): str(v) for k, v in (p["form"] or {}).items()}
    page_values = _page_values(form_spec, page_html)
    seen: set = set()
    found: list[dict] = []
    for node in HTMLParser(html or "").css(p["selector"]):
        values = {name: _value(node, spec, page_values) for name, spec in form_spec.items()}
        if any(v is None for v in values.values()):
            continue
        used = set(_PLACEHOLDER.findall(p["url"]))
        endpoint = util.expand(p["url"], ctx, page_url, **{k: quote(v, safe="") for k, v in values.items() if k in used})
        endpoint = util.absolute(page_url, endpoint)
        if not endpoint:
            continue
        form = {k: v for k, v in values.items() if k not in used}
        marker = (endpoint, tuple(sorted(form.items())))
        if marker in seen:
            continue
        seen.add(marker)
        handoff = {"url": endpoint, "method": str(p["method"] or "POST").upper(), "form": form, "json_key": p["json_key"] or "",
                   "decode": list(p["decode"] or []), "expect_host_regex": p["expect_host_regex"] or ""}
        if p.get("result") and p["result"] != "auto":
            handoff["result"] = p["result"]
        if p.get("cookies"):   # the SPECS (generators), never a value: a stored candidate makes them fresh at play time
            handoff["cookies"] = list(p["cookies"])
        cand = {"url": page_url, "label": util.pick_label(node, p, util.host_of(endpoint)), "handoff": handoff}
        found.append(util.with_language(cand, node, p))
    return found


def _dig(data: Any, path: str) -> Any:
    for key in path.split("."):
        if isinstance(data, dict) and key in data:
            data = data[key]
        elif isinstance(data, list) and key.isdigit() and int(key) < len(data):
            data = data[int(key)]
        else:
            return None
    return data


def _first_text(data: Any, depth: int = 0) -> str:
    """The first string of a JSON answer that holds a URL or an iframe (depth-first)."""
    if depth > MAX_DEPTH:
        return ""
    if isinstance(data, str):
        return data if ("http" in data or "<iframe" in data or data.startswith("//")) else ""
    items = data.values() if isinstance(data, dict) else data if isinstance(data, list) else []
    for item in items:
        hit = _first_text(item, depth + 1)
        if hit:
            return hit
    return ""


def url_in(text: str, base: str) -> str:
    """A URL from an answer piece: an iframe's src, a bare URL (also protocol-relative) or the first URL inside."""
    text = (text or "").strip()
    if "<iframe" in text.lower():
        node = HTMLParser(text).css_first("iframe[src]")
        return util.absolute(base, node.attributes.get("src")) if node is not None else ""
    if text.startswith(("http://", "https://", "//", "/")) and " " not in text:
        return util.absolute(base, text.replace("\\/", "/"))
    return _decode.first_url(text)


def answer_url(body: str, key: str, steps: list, base: str, result: str = "auto") -> tuple[str, str]:
    """``(url, why_not)`` of an AJAX answer (``result``: what the value is, see the ``result`` parameter)."""
    try:
        data = json.loads(body)
    except (TypeError, ValueError):
        data = None
    if data is not None:
        piece = _dig(data, key) if key else _first_text(data)
        if not isinstance(piece, str) or not piece.strip():
            return "", (f"no '{key}' in the answer" if key else "JSON answer holds no URL")
    else:
        piece = body or ""
    if steps:
        piece = _decode.chain(piece.strip(), steps)
        if not piece:
            return "", "decode failed"
    if result == "iframe_url":
        text = piece.strip().replace("\\/", "/")
        url = util.absolute(base, text) if " " not in text and "<" not in text else ""
    elif result == "html":
        node = HTMLParser(piece).css_first("iframe[src], iframe[data-src], embed[src], video[src], source[src]")
        url = util.absolute(base, (node.attributes.get("src") or node.attributes.get("data-src"))) if node is not None \
            else _decode.first_url(piece)
    else:
        url = url_in(piece, base)
    if url and result == "direct" and not _decode.media_type(url):
        return "", f"answer value is not a media file ({url[:80]})"
    return url, ("" if url else "answer holds no usable URL")


@util.guard(None)
def resolve_candidate(ctx, candidate, page_url, params, load_cookies):
    handoff = candidate.get("handoff")
    if not isinstance(handoff, dict) or not handoff.get("url"):
        return candidate
    page = urlparse(page_url)
    host = page.hostname or ""
    stage = f"{NAME}.call"
    started = time.monotonic()
    endpoint = handoff["url"]
    if not util.same_site(ctx, page_url, endpoint) or urlparse(endpoint).scheme not in ("http", "https"):
        trace.note(stage, host, False, started, f"ajax address is not on {host}")
        return None
    headers = {"Accept": "application/json, text/javascript, text/html, */*; q=0.01", "Referer": page_url,
               "Origin": f"{page.scheme}://{page.netloc}", "X-Requested-With": "XMLHttpRequest"}
    form = handoff.get("form") or {}
    util.seed_cookies(ctx, handoff.get("cookies"), endpoint)
    jar = util.jar_of(ctx)
    sent = ", ".join(jar.names(endpoint)) if jar is not None else ""
    method = "GET" if handoff.get("method") == "GET" else "POST"
    try:
        if method == "GET":
            sep = "&" if "?" in endpoint else "?"
            body = util.send(ctx, "GET", endpoint + (sep + urlencode(form) if form else ""), headers=headers)
        else:
            body = util.send(ctx, "POST", endpoint, headers=headers, data=form)
    except Exception as exc:
        status = util.status_of(exc)
        hint = " (5xx: site çerezi eksik olabilir)" if isinstance(status, int) and status >= 500 and not sent else ""
        trace.note(stage, host, False, started, f"{exc}{hint}", detail=f"{method} {urlparse(endpoint).path} "
                                                                     f"çerez: {sent or '-'}")
        return None
    key = handoff.get("json_key") or ""
    found, why = answer_url(body if isinstance(body, str) else util_text(body), key, handoff.get("decode") or [], endpoint,
                            str(handoff.get("result") or "auto"))
    if not found:
        trace.note(stage, host, False, started, why, detail=f"{method} {urlparse(endpoint).path} HTTP 200")
        return None
    trace.note(stage + ".answer", host, True, started,
               detail=f"{method} {urlparse(endpoint).path} HTTP 200, JSON '{key or '?'}' -> {found[:120]}"
                      + (f" (çerez: {sent})" if sent else ""))
    expect = handoff.get("expect_host_regex") or None
    found_on = ""   # the page the provider address was found on (Referer for the provider), when not the episode page
    if expect and not util.host_matches(found, expect):
        if not util.same_site(ctx, page_url, found):
            trace.note(stage, host, False, started, f"ajax answer host {util.host_of(found)} is not the expected one")
            return None
        found_on = found
        found, _ = util.follow_page(ctx, found, page_url, [], expect, stage + ".page")
        if not found:
            return None
    trace.note(stage, host, True, started)
    out = {k: v for k, v in candidate.items() if k != "handoff"}
    if handoff.get("result") == "iframe_url" and not found_on and util.same_site(ctx, page_url, found):
        out["referer"] = page_url   # a same-site intermediate page: resolve.walk_site_hops follows it (Referer = this page)
    return {**out, **candidate_of(found, out.get("label") or ""), **({"referer": found_on} if found_on else {})}


def util_text(body: Any) -> str:
    if isinstance(body, (bytes, bytearray)):
        return bytes(body).decode("utf-8", "replace")
    return str(body or "")


def schema_notes() -> list[str]:
    """Extra lines of the ``ajax`` schema in the prompts: a full tabbed / cookie example (generic names)."""
    sample = {"type": NAME, "selector": ".alternatives [data-link]", "url": "{base}/ajax/service", "method": "POST",
              "form": {"link": "attr:data-link", "hash": "attr:data-hash", "type": "const:videoGet"},
              "json_key": "api_iframe", "result": "iframe_url", "cookies": [{"name": "visit_ts", "value": "now_ms"}],
              "tab_group": {"selector": "[data-lang-tab]", "url": "{base}/ajax/service",
                            "form": {"lang": "attr:data-lang-tab", "episode": "attr:data-eid", "type": "const:langTab"},
                            "json_key": "data"}}
    return ["cookies: [{name, value}] value literal | now_ms | now_s (a cookie the site's JS sets, e.g. document.cookie = "
            "'x=' + Date.now()); server Set-Cookie is carried automatically",
            "tab_group: outer tabs whose AJAX answer (json_key, default 'data') is the HTML of the source buttons",
            "example with tabs + cookie: " + json.dumps(sample, ensure_ascii=False)]
