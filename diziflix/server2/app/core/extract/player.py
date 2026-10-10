"""Player candidates of an episode / film page (pure): iframes, tab / data-attribute sources, links to a known provider host,
URLs inside inline scripts, each with the resolver item (site map ``pages.player.resolvers``) that would find it.

The provider pool is a parameter (``core.play`` gives it): any iterable of objects with ``matches(url) -> bool`` (and optionally
``name``), or of plain callables ``url -> bool``."""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional
from urllib.parse import urlsplit

from selectolax.parser import HTMLParser

from ..play.resolvers import _decode, _unpack
from ._select import SAFE, stable
from ._util import absolute, doc_base, host_key, text_of

MAX_CANDIDATES = 12
_ADS = re.compile(r"(?:google|doubleclick|googlesyndication|googletagmanager|facebook|twitter|disqus|histats|yandex|recaptcha|"
                  r"gstatic|cloudflare|addthis|sharethis|gravatar|wp\.com|amazon-adsystem|adsterra|popads)", re.I)
_TRAILER_HOST = re.compile(r"(?:youtube\.com|youtu\.be|youtube-nocookie\.com)", re.I)
_PLACEHOLDER = re.compile(r"telif|copyright|blocked|unavailable|restricted|yasakli|engel", re.I)
_PLAYER_PATH = re.compile(r"embed|player|/e/|/v/|/play|iframe|oynat|watch", re.I)
_URL_IN_SCRIPT = re.compile(r"""https?:(?:\\?/){2}[A-Za-z0-9.-]+(?:\\?/[^\s"'<>\\]*)*""")
_PLAYER_ATTRS = ("src", "data-src", "data-lazy-src", "data-litespeed-src")
_TAB_ATTRS = ("data-video", "data-embed", "data-player", "data-iframe", "data-url", "data-src", "data-link", "data-source",
              "data-file", "data-frame", "data-hash")
_MEDIA_ATTRS = (("embed", "src"), ("video", "src"), ("video source", "src"), ("object", "data"))
# AJAX source buttons: id-like data attributes (the request parameters) + an endpoint seen in the page's scripts
_AJAX_ATTRS = ("data-post", "data-id", "data-nume", "data-type", "data-player", "data-video", "data-embed", "data-source",
               "data-link", "data-hash", "data-episode", "data-server", "data-vid")
_AJAX_IDS = ("data-post", "data-id", "data-nume", "data-player", "data-video", "data-embed", "data-source", "data-link",
             "data-hash", "data-episode", "data-server", "data-vid")
_STRONG_IDS = ("data-post", "data-nume", "data-player", "data-embed", "data-video", "data-source", "data-server", "data-hash",
               "data-vid")
_SOURCEY = re.compile(r"player|source|kaynak|alternat|server|sunucu|embed|video|oynat|tab|option|secenek", re.I)
_ID_VALUE = re.compile(r"^[\w=+/.-]{1,80}$")
_ADMIN_AJAX = re.compile(r"""["']((?:https?:)?(?:\\?/|[^"'\s])*?admin-ajax\.php)["']""")
_AJAX_CALL = re.compile(r"""(?:\$\.(?:post|ajax|get)|jQuery\.(?:post|ajax|get)|axios\.(?:post|get))\(\s*["']([^"']+)["']""")
_URL_KEY = re.compile(r"""\burl\s*:\s*["']([^"']+)["']""")
_FETCH_CONCAT = re.compile(r"""fetch\(\s*["']([^"']+)["']\s*\+\s*(?:[\w$]+\.)*([\w$]+)""")
_ACTION = re.compile(r"""\baction["']?\s*[:=]\s*["']([\w-]{2,60})["']""")
_NONCE = re.compile(r"""\b(\w*nonce\w*)["']?\s*[:=]\s*["']([\w-]{4,80})["']""", re.I)
# hidden URLs in inline scripts: (regex given to the script_regex resolver, decode steps, how)
_ATOB = r"""atob\(\s*["']([A-Za-z0-9+/=_-]{12,})["']\s*\)"""
_HEX = r"""["']((?:\\x[0-9a-fA-F]{2}){10,})["']"""
_URIDEC = r"""decodeURIComponent\(\s*["']([^"']*%3A%2F%2F[^"']*)["']\s*\)"""
_KEYED = r"""["']?(?:file|src|source|url|embed_url|iframe)["']?\s*:\s*["'](https?:(?:\\?/){2}[^"']+)["']"""
_SCRIPT_RULES = ((_ATOB, ["base64"], "script atob (base64)"), (_HEX, ["hex"], "script hex"),
                 (_URIDEC, ["urldecode"], "script decodeURIComponent"))


def _provider_of(providers: list, url: str) -> Optional[str]:
    """Name of the first provider that claims ``url`` ('' for an unnamed one), None when none does."""
    for provider in providers:
        try:
            hit = provider.matches(url) if hasattr(provider, "matches") else provider(url)
        except Exception:
            hit = False
        if hit:
            return str(getattr(provider, "name", "") or "")
    return None


def _registrable(host: str) -> str:
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _iframe_selector(tree, node, attr: str, url: str, site_host: str) -> str:
    host = host_key(url)
    path = urlsplit(url).path
    options: list[str] = []
    if host != site_host:
        options.append(f'iframe[{attr}*="{host}"]')
    else:
        prefix = path[: path.rfind("/") + 1]
        if len(prefix) >= 3:
            options.append(f'iframe[{attr}*="{prefix}"]')
    ident = node.attributes.get("id") or ""
    if ident and SAFE.match(ident):
        options.append(f"iframe#{ident}")
    options.append(f"iframe[{attr}]")
    for selector in options:
        try:
            matched = tree.css(selector)
        except Exception:
            continue
        if any(m.mem_id == node.mem_id for m in matched):
            return selector
    return f"iframe[{attr}]"




def _attr_selector(tree, node, attr: str) -> str:
    """``.<stable class of the element or its parent> [attr]`` (else ``tag[attr]``) that matches ``node``."""
    options: list[str] = []
    own = [c for c in (node.attributes.get("class") or "").split() if stable(c)]
    if own:
        options.append(f"{node.tag}.{own[0]}[{attr}]")
    parent = node.parent
    for _ in range(3):
        if parent is None or parent.tag in ("body", "html"):
            break
        classes = [c for c in (parent.attributes.get("class") or "").split() if stable(c)]
        ident = parent.attributes.get("id") or ""
        if ident and SAFE.match(ident):
            options.append(f"#{ident} [{attr}]")
        if classes:
            options.append(f".{classes[0]} [{attr}]")
            break
        parent = parent.parent
    options.append(f"{node.tag}[{attr}]")
    for selector in options:
        try:
            if any(m.mem_id == node.mem_id for m in tree.css(selector)):
                return selector
        except Exception:
            continue
    return f"[{attr}]"


def _label(node, default: str) -> str:
    text = text_of(node) if node is not None else ""
    return (text[:40] if text else "") or default


def _decoded(value: str) -> tuple[str, list]:
    """``(url, decode steps)`` of an attribute value that is an encoded URL (base64 / hex / percent), else ``("", [])``."""
    for steps in (["base64"], ["hex"], ["urldecode"], ["reverse"]):
        out = _decode.chain(value, steps).strip()
        if out.startswith(("http://", "https://")) and absolute(out, out):
            return out, steps
    return "", []


def ajax_endpoints(html: str) -> list[dict]:
    """AJAX loaders named by the page: ``[{url, method, action?, nonce?: (field, regex), var?}]`` (admin-ajax-like POST with an
    ``action``, ``$.post('/x')`` / ``url: '/x'`` calls, ``fetch('/x/' + id)`` GET templates)."""
    scripts = " ".join((n.text() or "")[:200_000] for n in HTMLParser(html or "").css("script") if not n.attributes.get("src"))
    action = _ACTION.search(scripts)
    nonce = _NONCE.search(scripts)
    extra = {}
    if action:
        extra["action"] = action.group(1)
    if nonce:
        extra["nonce"] = (nonce.group(1), r"""\b""" + re.escape(nonce.group(1)) + r"""["']?\s*[:=]\s*["']([\w-]{4,80})["']""")
    out: list[dict] = []
    seen: set = set()
    for match in _FETCH_CONCAT.finditer(scripts):
        if match.group(1) not in seen:
            seen.add(match.group(1))
            out.append({"url": match.group(1), "method": "GET", "var": match.group(2)})
    for regex in (_ADMIN_AJAX, _AJAX_CALL, _URL_KEY):
        for match in regex.finditer(scripts if regex is not _ADMIN_AJAX else (html or "")):
            url = match.group(1).replace("\\/", "/")
            if url in seen or url.startswith("#") or url.endswith((".js", ".css", ".png", ".jpg")):
                continue
            seen.add(url)
            out.append({"url": url, "method": "POST", **extra})
    return out[:4]


def _ajax_rule(selector: str, attrs: list, endpoint: dict) -> dict:
    form: dict = {}
    url = endpoint["url"]
    if endpoint.get("var"):   # fetch('/x/' + id): the id-like attribute goes into the address
        var = endpoint["var"].lower()
        attr = next((a for a in attrs if a[5:].replace("-", "") == var.replace("_", "")), None) or \
            next((a for a in attrs if a in _AJAX_IDS), attrs[0])
        field = re.sub(r"[^A-Za-z0-9_]", "_", attr[5:]) or "id"
        form[field] = f"attr:{attr}"
        url = url + "{" + field + "}"
    else:
        for attr in attrs:
            form[re.sub(r"[^A-Za-z0-9_]", "_", attr[5:])] = f"attr:{attr}"
        if endpoint.get("action"):
            form["action"] = f"const:{endpoint['action']}"
        if endpoint.get("nonce"):
            form[endpoint["nonce"][0]] = "page:" + endpoint["nonce"][1]
    if url.startswith("/") and not url.startswith("//"):
        url = "{base}" + url
    return {"type": "ajax", "selector": selector, "url": url, "method": endpoint["method"], "form": form}


def player_candidates(html: str, page_url: str, base: str = "", providers: Iterable[Any] = ()) -> list[dict]:
    """ALL source alternatives of a player page: ``[{url, host, how, kind, label, decode, trailer, placeholder, provider,
    resolver}]``: ``provider`` = the matching provider's name (None = no provider claims it), ``resolver`` = the site map resolver
    item that finds it (None = found but not decodable by a simple rule: the LLM investigation's job). Kinds: ``iframe`` (also
    embed / video source), ``tab`` (data-* attribute / select option, plain or encoded), ``ajax`` (a source button loaded by an
    AJAX call), ``script`` (URL in an inline script: plain, atob, hex, decodeURIComponent, p.a.c.k.e.r.), ``link``."""
    pool = list(providers or ())
    tree = HTMLParser(html or "")
    link_base = doc_base(tree, page_url)   # relative links resolve against the page's <base href>
    site_host = host_key(base or page_url)
    out: list[dict] = []
    seen: set = set()

    def add(url: str, how: str, resolver: Optional[dict], *, kind: str, label: str = "", decode: Optional[list] = None,
            key: str = "") -> None:
        marker = key or url
        if not url or marker in seen or len(out) >= MAX_CANDIDATES:
            return
        host = host_key(url)
        if not host or _ADS.search(host):
            return
        seen.add(marker)
        out.append({"url": url, "host": host, "how": how, "kind": kind, "label": label or host, "decode": list(decode or []),
                    "trailer": bool(_TRAILER_HOST.search(host)),
                    "placeholder": bool(_PLACEHOLDER.search(urlsplit(url).path)) and host == site_host,
                    "provider": _provider_of(pool, url), "resolver": resolver})

    def wanted(url: str) -> bool:
        return bool(_PLAYER_PATH.search(url) or _decode.media_type(url) or _provider_of(pool, url) is not None)

    for node in tree.css("iframe, frame"):
        for attr in _PLAYER_ATTRS:
            value = (node.attributes.get(attr) or "").strip()
            url = absolute(link_base, value) if value and not value.lower().startswith("about:") else ""
            if url:
                resolver = {"type": "iframe", "selector": _iframe_selector(tree, node, attr, url, site_host)}
                if attr != "src":
                    resolver["attr"] = attr
                add(url, "iframe", resolver, kind="iframe")
                break
    for css, attr in _MEDIA_ATTRS:
        for node in tree.css(f"{css}[{attr}]"):
            url = absolute(link_base, node.attributes.get(attr))
            if url:
                add(url, css, {"type": "iframe", "selector": f"{css}[{attr}]", "attr": attr}, kind="iframe")
    tabs = tree.css("[" + "], [".join(_TAB_ATTRS) + "], select option[value]")
    for node in tabs:
        if node.tag in ("iframe", "img", "script", "source", "video", "meta", "link"):
            continue
        attrs = ("value",) if node.tag == "option" else _TAB_ATTRS
        for attr in attrs:
            value = (node.attributes.get(attr) or "").strip()
            if not value:
                continue
            kind = "option" if node.tag == "option" else "tab"
            label = _label(node, "")
            if value.startswith(("http", "/")):
                url = absolute(link_base, value)
                if url and wanted(url):
                    add(url, f"data attribute {attr}", {"type": "iframe", "selector": _attr_selector(tree, node, attr), "attr": attr},
                        kind=kind, label=label)
                continue
            url, steps = _decoded(value)
            if url and wanted(url):
                add(url, f"data attribute {attr} ({'+'.join(steps)})",
                    {"type": "data_attr", "selector": _attr_selector(tree, node, attr), "attr": attr, "decode": steps},
                    kind=kind, label=label, decode=steps)
    _ajax_candidates(tree, html, page_url, site_host, add)
    _script_candidates(tree, link_base, site_host, add, wanted)
    for node in tree.css("a[href]"):
        url = absolute(link_base, node.attributes.get("href"))
        if url and host_key(url) != site_host and _provider_of(pool, url) is not None:
            add(url, "link", {"type": "anchor_host", "host_regex": r"(?:.+\.)?" + re.escape(_registrable(host_key(url)))},
                kind="link", label=_label(node, ""))
    out.sort(key=lambda p: (p["placeholder"], p["trailer"], p["resolver"] is None, p["how"] != "iframe"))
    return out


def _source_context(node) -> bool:
    """The element or one of its 3 parents is named like a source / player list (class or id)."""
    for _ in range(4):
        if node is None or node.tag in ("body", "html"):
            return False
        if _SOURCEY.search((node.attributes.get("class") or "") + " " + (node.attributes.get("id") or "")):
            return True
        node = node.parent
    return False


def _ajax_candidates(tree, html: str, page_url: str, site_host: str, add) -> None:
    """Source buttons with id-like data attributes; with an endpoint in the page they become ``ajax`` resolvers, without one
    they stay undecoded alternatives (the LLM investigation looks for the loader)."""
    buttons = []
    for node in tree.css("[" + "], [".join(_AJAX_IDS) + "]"):
        if node.tag in ("iframe", "img", "script", "source", "video", "meta", "link", "html", "body", "option"):
            continue
        attrs = [a for a in _AJAX_ATTRS if _ID_VALUE.match((node.attributes.get(a) or "").strip())
                 and not (node.attributes.get(a) or "").strip().startswith(("http", "/"))]
        if not any(a in _AJAX_IDS for a in attrs) or _decoded((node.attributes.get(attrs[0]) or "").strip())[0]:
            continue
        if any(a in _STRONG_IDS for a in attrs) or _source_context(node):
            buttons.append((node, attrs))
    if not buttons:
        return
    endpoints = ajax_endpoints(html)
    page_base = page_url
    for node, attrs in buttons[:8]:
        selector = _attr_selector(tree, node, attrs[0] if not [a for a in attrs if a in _AJAX_IDS] else
                                  [a for a in attrs if a in _AJAX_IDS][0])
        label = _label(node, "")
        values = "&".join(f"{a}={node.attributes.get(a)}" for a in attrs)
        if endpoints:
            rule = _ajax_rule(selector, attrs, endpoints[0])
            target = absolute(page_base, rule["url"].replace("{base}", "").split("{")[0]) or page_base
            add(target, f"ajax {rule['method']} ({', '.join(attrs)})", rule, kind="ajax", label=label, key="ajax:" + values)
        else:
            add(page_base, f"button {', '.join(attrs)} (loader not found)", None, kind="ajax", label=label, key="ajax:" + values)


def _script_candidates(tree, page_url: str, site_host: str, add, wanted) -> None:
    texts = [(n.text() or "")[:300_000] for n in tree.css("script") if not n.attributes.get("src")]
    for text in texts:
        for regex, steps, how in _SCRIPT_RULES:
            for match in re.finditer(regex, text):
                url = _decode.chain(match.group(1), steps).strip()
                if url.startswith(("http://", "https://")) and wanted(url):
                    add(url, how, {"type": "script_regex", "regex": regex, "decode": list(steps)}, kind="script", decode=steps)
        for block in _unpack.unpack(text) if "eval(function(p,a,c,k,e," in text.replace(" ", "") else []:
            for match in re.finditer(_KEYED, block):
                url = match.group(1).replace("\\/", "/")
                if wanted(url):
                    add(url, "script packed (p.a.c.k.e.r.)", {"type": "script_regex", "regex": _KEYED, "unpack": True},
                        kind="script", decode=["packed"])
        for match in _URL_IN_SCRIPT.finditer(text):
            url = match.group(0).replace("\\/", "/")
            if host_key(url) == site_host or not wanted(url):
                continue
            keyed = any(m.group(1).replace("\\/", "/") == url for m in re.finditer(_KEYED, text))
            if keyed:
                rule = {"type": "script_regex", "regex": _KEYED, "decode": ["json"]}
            else:
                rule = {"type": "script_regex", "regex": r"""["'](https?:(?:\\?/){2}(?:[\w-]+\.)*"""
                        + re.escape(_registrable(host_key(url))) + r"""[^"']*)["']""", "decode": ["json"]}
            add(url, "script", rule, kind="script")


def alternatives(candidates: list) -> list[dict]:
    """The source alternatives of one page for the admin card / evidence: ``[{label, kind, how, host, url, resolver,
    decode, found_by}]`` (trailers / placeholders left out; same host + same address once)."""
    out, seen = [], set()
    for c in candidates or []:
        if c.get("trailer") or c.get("placeholder"):
            continue
        key = (c.get("host"), c.get("url"), c.get("label"))
        if key in seen:
            continue
        seen.add(key)
        out.append({"label": c.get("label") or c.get("host"), "kind": c.get("kind") or "", "how": c.get("how") or "",
                    "host": c.get("host") or "", "url": c.get("url") or "", "resolver": c.get("resolver"),
                    "decode": list(c.get("decode") or []), "found_by": "LLM'siz" if c.get("resolver") else "çözülemedi"})
    return out
