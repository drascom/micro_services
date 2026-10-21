"""``match_providers``: which provider of the library reads a player page, whatever its host says.

Given a player URL (and the page it is embedded in) the player page is fetched ONCE with the transport a recipe uses (Chrome TLS
fingerprint + Referer, ``fetch.fetch_impersonated``), every provider of the library is dry-run on it with the host match IGNORED (the
recipes through ``RecipeProvider.resolve`` on a shared, memoizing transport: one request serves them all; the code providers through
their own ``resolve``), and the outcome is turned into a recommendation:

* ``use_provider``  a provider whose ``match`` already covers the URL resolved it: just list it in ``providers:``;
* ``add_host``      a RECIPE resolved it but its ``match`` does not cover this host: ``recipe_yaml`` is the same recipe with the host
                    added to ``match.host_regex`` (exact-host alternative, nothing narrowed); hand it in as ``provider_recipes``
                    ``[{name, yaml, mode: "update"}]`` (``recipes.update_problems`` is the gate that accepts only that change);
* ``new_recipe``    nobody resolved it: write a recipe (``references/player-authoring.md``); the answer carries what the page holds
                    (``page.media_urls``, ``page.iframes``, ``page.packed``) to start from;
* ``needs_code``    nobody resolved it and the page is protected (challenge / captcha / 401-403-429) or the stream comes from a
                    script API call, or only a code provider could read it.

Nothing is written, no database; the run is bounded (``MAX_SECONDS``, ``WORKERS`` at a time, browser recipes whose host does not match
are skipped: a browser start for a guess is not worth it).
"""
from __future__ import annotations

import copy
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any, Optional
from urllib.parse import urlsplit

import yaml

from .providers import recipes, registry, trace
from .resolvers import player_page

log = logging.getLogger("scraper.provider_match")

MAX_SECONDS = 50.0          # the whole run
WORKERS = 3                 # providers dry-run at a time
FETCH_TIMEOUT = player_page.FETCH_TIMEOUT
_CHALLENGE = re.compile(r"just a moment|cf-browser-verification|challenge-platform|cf_chl_|attention required", re.I)
_CAPTCHA = re.compile(r"g-recaptcha|h-captcha|hcaptcha|cf-turnstile|turnstile|captcha", re.I)
_BLOCKED_STATUS = re.compile(r"\b(?:401|403|429|503)\b|forbidden|challenge")
_MEDIA = re.compile(r"""https?:(?:\\?/){2}[^\s"'<>\\]+?\.(?:m3u8|mp4)(?:[^\s"'<>\\]*)""", re.I)
_PACKED = re.compile(r"eval\(function\(p,a,c,k,e", re.I)
_API_CALL = re.compile(r"(?:fetch|XMLHttpRequest|\$\.(?:ajax|post|get)|axios)[^;]{0,120}?(?:/api/|/ajax|token|sign|source|embed)", re.I)


class SharedFetch:
    """The ``app.scraper.fetch`` module's player transport with ``fetch_impersonated`` memoized per (url, headers): the recipes of a run
    that read the same page share one request (a failure is shared too). A call with a ``session`` (``warm_session``) is not cached.
    Everything else (``browser_page``, ``impersonated_session``, ``reachable``) is the module's own."""

    def __init__(self, api: Any) -> None:
        self._api = api
        self._lock = threading.Lock()
        self._cache: dict[tuple, dict] = {}
        self.requests = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._api, name)

    def fetch_impersonated(self, url: str, **kwargs: Any) -> str:
        if kwargs.get("session") is not None:
            self.requests += 1
            return self._api.fetch_impersonated(url, **kwargs)
        key = (url, tuple(sorted((str(k), str(v)) for k, v in (kwargs.get("headers") or {}).items())))
        with self._lock:
            entry = self._cache.get(key)
            owner = entry is None
            if owner:
                entry = self._cache[key] = {"done": threading.Event(), "body": None, "error": None}
        if owner:
            try:
                self.requests += 1
                entry["body"] = self._api.fetch_impersonated(url, **kwargs)
            except BaseException as exc:
                entry["error"] = exc
            finally:
                entry["done"].set()
        else:
            entry["done"].wait(timeout=FETCH_TIMEOUT * 2)
        if entry["error"] is not None:
            raise entry["error"]
        if entry["body"] is None:
            raise TimeoutError("shared player fetch did not finish")
        return entry["body"]


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _page_facts(body: Optional[str], error: str) -> dict:
    """What the player page is (``page`` of the answer): status, size, protection hints, what a recipe could start from."""
    page: dict[str, Any] = {"ok": body is not None}
    if body is None:
        page["error"] = error[:200]
        page["blocked"] = bool(_BLOCKED_STATUS.search(error.lower()))
        return page
    page["bytes"] = len(body)
    page["blocked"] = bool(_CHALLENGE.search(body[:200_000]))
    page["captcha"] = bool(_CAPTCHA.search(body[:200_000]))
    page["media_urls"] = list(dict.fromkeys(m.group(0).replace("\\/", "/") for m in _MEDIA.finditer(body[:500_000])))[:3]
    page["iframes"] = list(dict.fromkeys(re.findall(r"<iframe[^>]+src=[\"']([^\"']+)", body[:500_000], re.I)))[:3]
    page["packed"] = bool(_PACKED.search(body[:500_000]))
    page["api_call"] = bool(_API_CALL.search(body[:500_000])) and not page["media_urls"] and not page["packed"]
    return page


def _run_recipe(provider: Any, url: str, referer: str, shared: SharedFetch, matches: bool) -> dict:
    row: dict[str, Any] = {"provider": provider.name, "kind": "recipe", "host_match": matches, "ok": False}
    if (provider.data.get("fetch") == "browser") and not matches:
        row["skipped"] = "browser recipe whose match does not cover this host: not started"
        return row
    started = time.monotonic()
    trace.begin()
    try:
        runner = recipes.RecipeProvider(provider.data, fetch_api=shared)
        result = runner.resolve(url, referer=referer)
    except Exception as exc:
        result, row["error"] = None, trace.short(exc)
    events = trace.take()
    return _finish(row, result, events, started)


def _run_code(provider: Any, url: str, referer: str, matches: bool) -> dict:
    row: dict[str, Any] = {"provider": provider.name, "kind": "code", "host_match": matches, "ok": False}
    started = time.monotonic()
    trace.begin()
    try:
        result = provider.resolve(url, referer=referer)
    except Exception as exc:
        result, row["error"] = None, trace.short(exc)
    events = trace.take()
    return _finish(row, result, events, started)


def _finish(row: dict, result: Optional[dict], events: list, started: float) -> dict:
    streams = [s for s in (result or {}).get("streams") or [] if isinstance(s, dict) and s.get("url")]
    row["ms"] = int((time.monotonic() - started) * 1000)
    if streams:
        row.update(ok=True, stream_type=streams[0].get("type"), stream_host=_host(streams[0]["url"]))
    elif "error" not in row:
        failed = [e for e in events if not e.get("ok") and e.get("error")]
        row["error"] = (f"{failed[-1]['stage']}: {failed[-1]['error']}" if failed else "no stream found")[:200]
    return row


def _widen(provider: Any, url: str) -> Optional[dict]:
    """The ``add_host`` recommendation of a recipe that resolved ``url`` without its ``match`` covering it (None when the recipe cannot
    be widened to cover it: the path does not fit, or the regex would be too long)."""
    host = _host(url).removeprefix("www.")
    data = {k: copy.deepcopy(v) for k, v in provider.data.items() if k != "updated_at"}
    match = dict(data.get("match") or {})
    new_regex = recipes.widen_host_regex(str(match.get("host_regex") or ""), [host])
    data["match"] = {**match, "host_regex": new_regex}
    data["version"] = int(provider.data.get("version") or 1) + 1
    if recipes.validate_recipe(data) or recipes.update_problems(provider.data, data):
        return None
    try:
        if not recipes.RecipeProvider(data).matches(url):
            return None
    except Exception:
        return None
    return {"action": "add_host", "recipe": provider.name, "host": host, "host_regex": new_regex,
            "recipe_yaml": yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120), "mode": "update",
            "note": f"hand it in as provider_recipes [{{name: {provider.name}, yaml: recipe_yaml, mode: \"update\"}}] and list {provider.name} in providers:"}


def recommend(matches: list[dict], page: dict, url: str, providers: list) -> dict:
    """The recommendation for ``matches`` (see the module docstring)."""
    by_name = {p.name: p for p in providers}
    ok = [m for m in matches if m["ok"]]
    covered = [m for m in ok if m["host_match"]]
    if covered:
        return {"action": "use_provider", "provider": covered[0]["provider"],
                "note": f"the library already covers this host: list {covered[0]['provider']} in providers:"}
    for row in ok:
        if row["kind"] == "recipe" and row["provider"] in by_name:
            widened = _widen(by_name[row["provider"]], url)
            if widened:
                return widened
    if ok:
        names = ", ".join(m["provider"] for m in ok)
        return {"action": "new_recipe" if all(m["kind"] == "recipe" for m in ok) else "needs_code", "provider": ok[0]["provider"],
                "note": (f"{names} reads the page but its match cannot be widened to this URL (path / length): write a new recipe"
                         if all(m["kind"] == "recipe" for m in ok) else
                         f"only a CODE provider ({names}) reads it: its host list is code, not a recipe")}
    if page.get("blocked") or page.get("captcha"):
        return {"action": "needs_code", "note": "no provider resolved it and the page is protected (challenge / captcha / HTTP 401-403-429): "
                "`needs code: <host>` in notes, playback: trailer"}
    if page.get("api_call"):
        return {"action": "needs_code", "note": "no provider resolved it and the page has no media URL: the stream comes from a script API "
                "call or signature: `needs code: <host>` unless fetch_page mode browser shows it in the HTML"}
    return {"action": "new_recipe", "note": "no provider resolved it: write a recipe for the library (references/player-authoring.md); "
            "page.media_urls / iframes / packed show where to start"}


def match(url: str, *, referer: str = "", fetch_api: Any = None, deadline: Optional[float] = None) -> dict:
    """The answer of ``match_providers``: ``{player_url, host, referer, page, matches[], recommendation, ms, notes[]}``.
    ``url`` has been checked by the caller (``netguard``); ``fetch_api`` = the transport (default ``app.scraper.fetch``)."""
    if fetch_api is None:
        from . import fetch as fetch_api
    started = time.monotonic()
    limit = min(deadline if deadline is not None else started + MAX_SECONDS, started + MAX_SECONDS)
    shared = SharedFetch(fetch_api)
    notes: list[str] = []
    parts = urlsplit(url)
    header = referer or f"{parts.scheme}://{parts.netloc}/"
    if not referer:
        notes.append("no referer given: a player usually sits in a detail / episode page; pass that page's URL (the player may refuse a "
                     "request without it)")
    body, error = None, ""
    try:
        body = shared.fetch_impersonated(url, headers={"Referer": header}, timeout=FETCH_TIMEOUT, max_bytes=player_page.MAX_BODY,
                                         max_redirects=3, allow=player_page._allowed)
        body = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else body
    except Exception as exc:
        error = trace.short(exc)
    page = _page_facts(body if isinstance(body, str) and body.strip() else None, error or "empty page")
    providers = registry.providers()
    rows: list[Optional[dict]] = [None] * len(providers)
    pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="match-providers")
    futures = {}
    try:
        for index, provider in enumerate(providers):
            covers = _safe_matches(provider, url)
            if getattr(provider, "kind", "code") == "recipe" or hasattr(provider, "data"):
                futures[pool.submit(_run_recipe, provider, url, referer, shared, covers)] = index
            else:
                futures[pool.submit(_run_code, provider, url, referer, covers)] = index
        done, pending = wait(set(futures), timeout=max(1.0, limit - time.monotonic()))
        for future in done:
            try:
                rows[futures[future]] = future.result()
            except Exception as exc:
                rows[futures[future]] = {"provider": providers[futures[future]].name, "kind": "code", "host_match": False, "ok": False,
                                         "error": trace.short(exc)}
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    for future in futures:
        if rows[futures[future]] is None:
            provider = providers[futures[future]]
            rows[futures[future]] = {"provider": provider.name, "kind": getattr(provider, "kind", "code"), "ok": False,
                                     "host_match": _safe_matches(provider, url), "error": f"not finished within the {MAX_SECONDS:g}s limit"}
    matches = sorted((r for r in rows if r), key=lambda r: (not r["ok"], not r["host_match"]))
    return {"player_url": url, "host": parts.hostname or "", "referer": referer, "page": page, "matches": matches,
            "recommendation": recommend(matches, page, url, providers), "requests": shared.requests,
            "ms": int((time.monotonic() - started) * 1000), "notes": notes}


def _safe_matches(provider: Any, url: str) -> bool:
    try:
        return bool(provider.matches(url))
    except Exception:
        return False
