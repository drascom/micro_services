"""Server-side investigation steps the LLM may ask for while it looks for a page's video sources (A5 / A6 "inceleme").

The LLM never fetches anything itself (pi runs ``--no-tools``): it names steps, this module runs them and the stage engine
sends the trimmed results back in the same session. Allowed steps (anything else is refused):

    fetch         {url, method: GET|POST, data: {..}, referer, headers, xhr}   a page / endpoint (http(s) only, never a
                                                                 private address); ``xhr: true`` = an AJAX call (X-Requested-With,
                                                                 Origin, Accept json); the status of a refused call is reported
    post          {url, data, referer, headers}                  ``fetch`` with method POST + xhr (an AJAX endpoint)
    set_cookie    {name, value | now_ms | now_s}                 a cookie the site's JS would set, for THIS session only
    show_script   {index | url, from, to}                        a slice of an inline script of the page (or an external one)
    decode        {kind: base64|hex|packed|urldecode|json, text | ref}
    follow_iframe {url, referer}                                 open a player page, report its frames / scripts / URLs
    find          {in: page|script|<ref>, regex}                 regex matches with a little context
    probe         {url, referer, referer_policy: page|none|origin, type}   the real playback test (``playtest``) of a
                                                                 stream address the LLM found: played / step / codec

Every network call goes through the playback transport (``core.fetch``: netguard, DNS pin, size cap); the only cookies sent
are the session's own (the ``set_cookie`` steps + the sites' ``Set-Cookie`` answers, in memory, never stored); no secret is
ever sent; results are clipped. Nothing here knows a site (K3/K4): the steps are generic tools.
"""
from __future__ import annotations

import ipaddress
import json
import re
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit

from selectolax.parser import HTMLParser

from ..extract import htmlclip
from ..extract._util import doc_base
from .resolvers import _decode, _unpack

ACTIONS = ("fetch", "post", "set_cookie", "show_script", "decode", "follow_iframe", "find", "probe")
HEADER_ALLOW = ("x-requested-with", "accept", "origin", "content-type")
COOKIE_HINT = ("5xx / boş cevap: site çerezi eksik olabilir — betikte document.cookie / çerez yazan kodu ara (show_script, "
               "find), set_cookie ile ekle (now_ms = Date.now()) ve isteği tekrarla")
MAX_ACTIONS = 6          # per step
RESULT_CLIP = 2500       # characters of one action's result text
MAX_POST_FIELDS = 12
MAX_POST_CHARS = 2000
MAX_REGEX = 300
FETCH_TIMEOUT = 10.0
_NESTED = re.compile(r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)[+*{]")
_URL = re.compile(r"""https?:(?:\\?/){2}[^\s"'<>\\)]+""")


class Refused(ValueError):
    """A step that must not run (unknown kind, bad address, oversize body ...)."""


def check_url(url: Any) -> str:
    """``url`` when it may be fetched by an investigation step (http(s), no credentials, port 80/443, not a private /
    loopback / link-local IP literal or ``localhost``); :class:`Refused` otherwise. The transport's netguard checks the
    DNS answer again."""
    text = str(url or "").strip()
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise Refused(f"yalnız http(s) adresi: {text[:80]}")
    if "@" in parts.netloc:
        raise Refused("adreste kimlik bilgisi olamaz")
    try:
        port = parts.port
    except ValueError:
        raise Refused("geçersiz port")
    if port not in (None, 80, 443):
        raise Refused(f"port {port} kapalı")
    host = parts.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local") or host.endswith(".internal"):
        raise Refused(f"özel adres: {host}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and not ip.is_global:
        raise Refused(f"özel adres: {host}")
    return text


def check_regex(pattern: Any) -> str:
    if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_REGEX:
        raise Refused(f"regex 1..{MAX_REGEX} karakter olmalı")
    try:
        re.compile(pattern)
    except re.error as exc:
        raise Refused(f"geçersiz regex ({exc})")
    if _NESTED.search(pattern):
        raise Refused("regex iç içe niceleyici içeriyor (ör. (a+)+)")
    return pattern


def _text(body: Any) -> str:
    if isinstance(body, (bytes, bytearray)):
        return bytes(body).decode("utf-8", "replace")
    return body if isinstance(body, str) else str(body or "")


def summarize(html: str, url: str) -> dict:
    """What a fetched page holds for a source hunt: iframes, script srcs, inline script count, URLs in it, packed blocks."""
    tree = HTMLParser(html or "")
    base = doc_base(tree, url)   # relative links resolve against the page's <base href>
    frames = [urljoin(base, n.attributes.get("src") or n.attributes.get("data-src") or "")
              for n in tree.css("iframe") if n.attributes.get("src") or n.attributes.get("data-src")]
    scripts = [urljoin(base, n.attributes["src"]) for n in tree.css("script[src]") if n.attributes.get("src")]
    inline = [n.text() or "" for n in tree.css("script") if not n.attributes.get("src")]
    urls = list(dict.fromkeys(m.group(0).replace("\\/", "/") for m in _URL.finditer(html or "")))
    from . import direct
    direct_found = [{"url": s["url"], "type": s["type"], "label": s["label"]} for s in direct.direct_streams(html, url)]
    ads = direct.ads_in(html, url)
    extra = {"direct_streams": direct_found[:6]} if direct_found else {}
    if ads:
        extra["ads_dropped"] = ads[:4]
    return {**extra, "bytes": len(html or ""), "iframes": frames[:8], "script_src": scripts[:10], "inline_scripts": len(inline),
            "packed": sum(1 for s in inline if "eval(function(p,a,c,k,e," in s.replace(" ", "")),
            "urls": [u for u in urls if not u.endswith((".css", ".png", ".jpg", ".svg", ".woff2"))][:15]}


EMPTY_PAGE = "örnek sayfa yok (page boş): önce fetch ile bir sayfa getir, sonra in: r<N> / show_script url: kullan"


class Bench:
    """One investigation session's workbench: the page under study, the documents fetched so far (``r1``, ``r2`` ...) and
    the transport (``fetch_url`` / ``post_url``, the playback transport by default)."""

    def __init__(self, page_url: str, html: str = "", transport: Any = None) -> None:
        if transport is None:
            from . import transport as default
            transport = default
        self.page_url = page_url
        self.html = html or ""
        self.link_base = doc_base(self.html, page_url)   # what the page's relative addresses resolve against
        self.transport = transport
        self.docs: dict[str, str] = {}
        from ..fetch.cookies import Jar
        self.jar = Jar(host=(urlsplit(page_url or "").hostname or "").lower())   # this investigation session only

    def scripts(self) -> list[str]:
        return [n.text() or "" for n in HTMLParser(self.html).css("script") if not n.attributes.get("src")]

    def _store(self, text: str) -> str:
        ref = f"r{len(self.docs) + 1}"
        self.docs[ref] = text
        return ref

    def _get(self, url: str, referer: str = "", method: str = "GET", data: Optional[dict] = None,
             extra: Optional[dict] = None) -> str:
        from ..fetch import cookies as fetch_cookies
        from .resolvers._util import _takes
        url = check_url(url)
        headers = {"Referer": check_url(referer)} if referer else {"Referer": self.page_url}
        headers.update(extra or {})
        headers = fetch_cookies.apply(headers, self.jar, url)
        fn = self.transport.post_url if method == "POST" else self.transport.fetch_url
        kw: dict = {"headers": headers, "timeout": FETCH_TIMEOUT, "retries": 0}
        if method == "POST":
            kw["data"] = data or {}
        else:
            kw["check_robots"] = False
        if _takes(fn, "jar"):
            kw["jar"] = self.jar
        return _text(fn(url, **kw))

    def _headers(self, a: dict, url: str) -> dict:
        """The extra request headers of a step: ``xhr: true`` (or a POST step) = what a page's AJAX call sends; ``headers``
        only from :data:`HEADER_ALLOW` (no Cookie / Authorization: cookies go through ``set_cookie``)."""
        out: dict = {}
        if a.get("xhr"):
            parts = urlsplit(self.page_url or url)
            out = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json, text/javascript, */*; q=0.01",
                   "Origin": f"{parts.scheme}://{parts.netloc}"}
        given = a.get("headers") or {}
        if not isinstance(given, dict):
            raise Refused("headers bir eşleme olmalı")
        for name, value in given.items():
            if str(name).lower() not in HEADER_ALLOW:
                raise Refused(f"başlık izinli değil: {name} (izinli: {', '.join(HEADER_ALLOW)}; çerez için set_cookie)")
            if re.search(r"[\r\n]", str(value)) or len(str(value)) > 300:
                raise Refused(f"başlık değeri tek satır olmalı: {name}")
            out[str(name)] = str(value)
        return out

    # --- the steps ------------------------------------------------------------------------------------------------
    def fetch(self, a: dict) -> tuple[str, str]:
        method = str(a.get("method") or "GET").upper()
        if method not in ("GET", "POST"):
            raise Refused("method GET ya da POST olmalı")
        data = a.get("data") or {}
        if not isinstance(data, dict) or len(data) > MAX_POST_FIELDS \
                or len(json.dumps(data, ensure_ascii=False, default=str)) > MAX_POST_CHARS:
            raise Refused(f"POST gövdesi en çok {MAX_POST_FIELDS} alan / {MAX_POST_CHARS} karakter")
        parts = urlsplit(self.page_url or "")
        raw_url = str(a.get("url") or "").replace("{base}", f"{parts.scheme}://{parts.netloc}" if parts.netloc else "")
        url = urljoin(self.link_base, raw_url)
        try:
            body = self._get(url, str(a.get("referer") or ""), method, {str(k): str(v) for k, v in data.items()},
                             self._headers(a, url))
        except Refused:
            raise
        except Exception as exc:
            status = getattr(exc, "status", None)
            hint = f" — {COOKIE_HINT}" if isinstance(status, int) and status >= 500 else ""
            cookies = ", ".join(self.jar.names(url)) or "-"
            raise RuntimeError(f"{method} {url[:120]} -> HTTP {status or '?'} ({' '.join(str(exc).split())[:80]}; "
                               f"çerezler: {cookies}){hint}") from exc
        ref = self._store(body)
        info = summarize(body, url)
        stripped = body.strip()
        text = stripped if stripped[:1] in "{[" else htmlclip.clean_html(body, RESULT_CLIP)
        hint = f" — {COOKIE_HINT}" if not stripped else ""
        cookies = ", ".join(self.jar.names(url)) or "-"
        return (f"{method} {url} -> {ref} ({info['bytes']} bayt, {len(info['iframes'])} iframe, çerezler: {cookies}){hint}",
                f"ref: {ref}\ncookies: {cookies}\nsummary: {json.dumps(info, ensure_ascii=False)}\nbody: {text}{hint}")

    def post(self, a: dict) -> tuple[str, str]:
        """An AJAX POST (``fetch`` with method POST; X-Requested-With / Origin / Accept json unless ``xhr: false``)."""
        return self.fetch({**a, "method": "POST", "xhr": a.get("xhr", True)})

    def set_cookie(self, a: dict) -> tuple[str, str]:
        """A cookie the site's own script sets (``now_ms`` = Date.now()), for this investigation session only. It is never
        stored: a rule that needs it writes ``cookies: [{name, value: now_ms}]`` (ajax resolver) / ``fetch.cookies``."""
        from ..fetch import cookies as fetch_cookies
        name, value = str(a.get("name") or ""), a.get("value", "")
        problems = fetch_cookies.check_specs([{"name": name, "value": value}], "set_cookie")
        if problems:
            raise Refused("; ".join(problems))
        host = (urlsplit(check_url(a["url"]) if a.get("url") else self.page_url).hostname or "").lower()
        if not host:
            raise Refused("çerez için sayfa adresi yok")
        self.jar.set(name, value, host)
        shown = value if value in fetch_cookies.GENERATORS else "<değer>"
        return f"çerez {name}={shown} ({host}) bu oturuma eklendi", f"cookies now: {', '.join(self.jar.names())}"

    def show_script(self, a: dict) -> tuple[str, str]:
        start = max(0, int(a.get("from") or 0))
        end = min(start + 4000, int(a.get("to") or start + RESULT_CLIP))
        if a.get("url"):
            url = urljoin(self.link_base, str(a["url"]))
            text = self._get(url)
            self._store(text)
            where = url
        else:
            if not self.html.strip():
                raise Refused(EMPTY_PAGE)
            scripts = self.scripts()
            index = int(a.get("index") or 0)
            if not scripts:
                raise Refused("sayfada satır içi betik yok (dış betik için show_script url: kullan)")
            if not 0 <= index < len(scripts):
                raise Refused(f"betik sırası 0..{len(scripts) - 1}")
            text, where = scripts[index], f"inline #{index}"
        return f"script {where} [{start}:{end}] / {len(text)}", text[start:end]

    def decode(self, a: dict) -> tuple[str, str]:
        kind = str(a.get("kind") or "")
        if kind not in ("base64", "hex", "packed", "urldecode", "json"):
            raise Refused("kind: base64 | hex | packed | urldecode | json")
        source = self.docs.get(str(a.get("ref") or "")) if a.get("ref") else str(a.get("text") or "")
        if source is None:
            raise Refused(f"bilinmeyen ref {a.get('ref')}")
        out = "\n".join(_unpack.unpack(source)) if kind == "packed" else _decode.step(kind, source)
        if not out:
            return f"decode {kind}: sonuç yok", ""
        ref = self._store(out)
        urls = list(dict.fromkeys(m.group(0).replace("\\/", "/") for m in _URL.finditer(out)))[:10]
        return f"decode {kind} -> {ref} ({len(out)} krk, {len(urls)} adres)", f"ref: {ref}\nurls: {urls}\ntext: {out}"

    def follow_iframe(self, a: dict) -> tuple[str, str]:
        url = urljoin(self.link_base, str(a.get("url") or ""))
        body = self._get(url, str(a.get("referer") or ""))
        ref = self._store(body)
        info = summarize(body, url)
        return (f"follow {url} -> {ref} ({len(info['iframes'])} iframe, {len(info['urls'])} adres)",
                f"ref: {ref}\nsummary: {json.dumps(info, ensure_ascii=False)}")

    def find(self, a: dict) -> tuple[str, str]:
        pattern = check_regex(a.get("regex"))
        where = str(a.get("in") or "page")
        if where in ("page", "script") and not self.html.strip():
            raise Refused(EMPTY_PAGE)
        if where == "page":
            texts = [self.html]
        elif where == "script":
            texts = self.scripts()
        elif where in self.docs:
            texts = [self.docs[where]]
        else:
            raise Refused("in: page | script | <ref>")
        hits = []
        for text in texts:
            for m in re.finditer(pattern, text[:2_000_000]):
                lo, hi = max(0, m.start() - 60), min(len(text), m.end() + 60)
                hits.append({"match": m.group(0)[:200], "groups": [g[:200] for g in m.groups() if g][:4],
                             "context": text[lo:hi]})
                if len(hits) >= 10:
                    break
            if len(hits) >= 10:
                break
        return f"find /{pattern[:40]}/ in {where}: {len(hits)} eşleşme", json.dumps(hits, ensure_ascii=False)


    def probe(self, a: dict) -> tuple[str, str]:
        """The playback test of one stream address (only media: a script / image is refused): ``{played, step, error?,
        codec?, resolution?, bytes, ms, referer_policy}``."""
        from . import direct, playtest, refpolicy
        url = check_url(urljoin(self.link_base, str(a.get("url") or "")))
        kind = str(a.get("type") or "").lower()
        why = direct.media_reason({"url": url, "type": kind})
        if why:
            raise Refused(f"medya adresi değil ({why}): {url[:100]}")
        policy = str(a.get("referer_policy") or "page")
        if policy not in refpolicy.POLICIES:
            raise Refused(f"referer_policy: {' | '.join(refpolicy.POLICIES)}")
        referer = check_url(a["referer"]) if a.get("referer") else self.page_url
        parts = urlsplit(referer)
        headers = refpolicy.headers_for({"Referer": referer, "Origin": f"{parts.scheme}://{parts.netloc}"}, policy)
        got = playtest.play_test(url, headers=headers, kind=kind if kind in ("hls", "mp4") else None) or {}
        brief = {k: got[k] for k in ("played", "step", "error", "code", "codec", "resolution", "container", "bytes", "ms",
                                     "referer_policy", "policy_note") if got.get(k) not in (None, "")}
        return f"probe {url[:100]}: {playtest.summary(got)}", json.dumps(brief, ensure_ascii=False)


def run(bench: Bench, actions: Any) -> list[dict]:
    """Run at most :data:`MAX_ACTIONS` steps: ``[{action, ok, summary, text}]`` (``text`` clipped; a refused / failed step
    is reported, never raised)."""
    out: list[dict] = []
    for raw in list(actions or [])[:MAX_ACTIONS]:
        name, args = _split(raw)
        entry = {"action": name or "?", "ok": False, "summary": "", "text": ""}
        try:
            if name not in ACTIONS:
                raise Refused(f"izinli adımlar: {', '.join(ACTIONS)}")
            summary, text = getattr(bench, name)(args)
            entry.update(ok=True, summary=summary, text=_clip(text))
        except Refused as exc:
            entry["summary"] = f"reddedildi: {exc}"
        except Exception as exc:
            entry["summary"] = f"hata: {' '.join(str(exc).split())[:400]}"
        out.append(entry)
    return out


def _split(raw: Any) -> tuple[str, dict]:
    """``{fetch: {url: ..}}`` or ``{action: fetch, url: ..}`` -> ``("fetch", {url: ..})``."""
    if not isinstance(raw, dict):
        return "", {}
    if "action" in raw:
        return str(raw.get("action") or ""), {k: v for k, v in raw.items() if k != "action"}
    if len(raw) == 1:
        name, args = next(iter(raw.items()))
        return str(name), args if isinstance(args, dict) else {"url": args} if isinstance(args, str) else {}
    return "", {}


def _clip(text: str) -> str:
    text = str(text or "")
    return text if len(text) <= RESULT_CLIP else text[:RESULT_CLIP] + f" [... {len(text) - RESULT_CLIP} krk kırpıldı]"
