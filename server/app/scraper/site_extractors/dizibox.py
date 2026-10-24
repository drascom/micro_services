"""Player discovery + hand-off for dizibox.live episode pages.

An episode page (``/<slug>-<s>-sezon-<e>-bolum-izle/``) shows ONE player; the others sit on numbered pages of the same post
(``.../2/``, ``.../3/``, ...), listed in the player drop-down (``select.woca-linkpages-dd``: ``<option value="<page>">Moly+</option>``).
The un-numbered page's own player ("DBX Pro", ``/player/king/king.php``) is closed outside Turkey: it is never a candidate.
Candidates = the numbered pages of the drop-down (counted from the page, no fixed number). Each such page embeds the site's
wrapper ``/player/...php`` in ``#video-area``:

* ``/player/haydi.php?v=<base64 of https://ok.ru/video/<id>>`` -> decoded without a request -> OK.ru provider
* ``/player/moly/moly.php?h=<hash>`` -> its body is ``document.write(atob(unescape("%50%43...")))`` holding
  ``<iframe src="https://vidmoly.biz/embed-<code>.html">`` -> VidMoly provider

The media itself is the provider library's job (``providers/vidmolly.py``, ``providers/okru.py``). No ``series_inventory`` here:
the generic yaml ``series_page`` engine reads the series pages.
"""
from __future__ import annotations

import base64
import binascii
import re
import time
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from selectolax.parser import HTMLParser

from ..providers import trace

OPTIONS = "select.woca-linkpages-dd option[value]"
PLAYER_IFRAME = "#video-area iframe[src], iframe[src*='/player/']"
_ATOB = re.compile(r"atob\(\s*unescape\(\s*[\"']([^\"']{8,})[\"']\s*\)\s*\)")
_IFRAME_SRC = re.compile(r"<iframe\b[^>]*\bsrc=[\"']([^\"']+)[\"']", re.I)
_GEO_LOCKED = re.compile(r"/player/king/", re.I)   # "DBX Pro": closed outside Turkey


def _norm(url: str) -> str:
    return (url or "").split("#", 1)[0].rstrip("/")


def discover(html: str, page_url: str) -> list[dict]:
    """The numbered player pages of the episode's drop-down (the page itself and the geo-locked player excluded)."""
    tree = HTMLParser(html or "")
    own = _norm(page_url)
    base = re.sub(r"/\d+$", "", own)   # a numbered page (``.../2``) belongs to the same post
    out: list[dict] = []
    seen: set[str] = set()
    for node in tree.css(OPTIONS):
        url = urljoin(page_url, (node.attributes.get("value") or "").strip())
        norm = _norm(url)
        if not url.startswith("http") or norm in seen or norm in (own, base) or not re.search(r"/\d+$", norm):
            continue
        if urlparse(url).hostname != urlparse(page_url).hostname:
            continue
        seen.add(norm)
        label = " ".join(node.text().split()) or "Player"
        out.append({"url": url, "label": label})
    return out


def _b64(text: str) -> str:
    raw = (text or "").strip()
    for alt in (None, b"-_"):
        try:
            return base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=alt).decode("utf-8", "replace")
        except (binascii.Error, ValueError):
            continue
    return ""


def haydi_target(url: str) -> str:
    """``/player/haydi.php?v=<base64>`` -> the provider URL it wraps (``https://ok.ru/video/<id>``), '' when it is none."""
    value = (parse_qs(urlparse(url).query).get("v") or [""])[0]
    target = _b64(value).strip()
    return target if target.startswith(("http://", "https://")) else ""


def written_iframe(body: str, base_url: str) -> str:
    """The first iframe of a wrapper page, also when it is hidden in ``document.write(atob(unescape("...")))``."""
    for blob in _ATOB.findall(body or ""):
        found = _IFRAME_SRC.search(_b64(unquote(blob)))
        if found:
            return urljoin(base_url, found.group(1))
    node = HTMLParser(body or "").css_first("iframe[src]")
    return urljoin(base_url, node.attributes.get("src") or "") if node is not None else ""


def _allowed(url: str) -> bool:
    from ... import netguard
    try:
        netguard.check_url(url)
        return True
    except ValueError:
        return False


def _get(url: str, referer: str) -> str:
    """Playback fetch with a Chrome TLS fingerprint (Cloudflare answers plain httpx with 403) + Referer; every hop netguard-checked.
    Playback, not crawling: the player wrappers live under robots' ``/player/*`` like every embedded player of the site."""
    from .. import fetch
    return fetch.fetch_impersonated(url, headers={"Referer": referer}, timeout=12.0, max_bytes=3_000_000, max_redirects=3,
                                    allow=_allowed)


def provider_url(player: str, page_url: str) -> str:
    """The provider URL behind one wrapper iframe of a numbered page ('' = none / geo-locked)."""
    if not player or _GEO_LOCKED.search(player):
        return ""
    path = urlparse(player).path
    if path.endswith("/haydi.php"):
        return haydi_target(player)
    if "/player/" in path:
        return written_iframe(_get(player, page_url), player)
    return player   # already a third-party embed (vidmoly / ok.ru / ...): the registry decides


def resolve_candidate(candidate: dict, page_url: str, load_cookies) -> dict | None:
    """Numbered player page -> wrapper iframe -> provider URL (the registry resolves the media)."""
    url = candidate.get("url") or ""
    started = time.monotonic()
    host = urlparse(url).hostname or ""
    try:
        body = _get(url, page_url)
        node = HTMLParser(body).css_first(PLAYER_IFRAME)
        player = urljoin(url, node.attributes.get("src") or "") if node is not None else ""
        if not player:
            trace.note("dizibox.player_page", host, False, started, "no player iframe on the numbered page")
            return None
        target = provider_url(player, url)
    except Exception as exc:
        trace.note("dizibox.player_page", host, False, started, exc)
        return None
    if not target:
        trace.note("dizibox.handoff", urlparse(player).hostname or host, False, started, "wrapper holds no provider URL")
        return None
    trace.note("dizibox.handoff", urlparse(target).hostname or "", True, started)
    return {**candidate, "url": target}
