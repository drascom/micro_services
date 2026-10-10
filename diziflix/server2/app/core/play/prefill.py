"""Source candidates collected by the crawl (T28): every alternative of an episode / film page down to its PERSISTENT
candidate address, stored on its ``video_sources`` row so playback can skip the episode page (and the intermediate pages).

- ``collect(ctx, html, locator)``: the page's candidates (``candidates.discover``, the site map's resolvers, no LLM), each
  taken through its hand-off and the same-site intermediate pages (``resolve.walk_site_hops``) -> ``{alt_key, url, referer,
  needs_referer, kind, label, host}``. ``kind``: ``provider`` (a provider / embed address: resolved straight away),
  ``hop`` (an intermediate page that writes the media itself: read on every play, the media URL is signed), ``page`` (the
  type produced the media from the episode page itself, or the walk failed: playback keeps the full path). Ads / trackers
  are dropped. The final stream address is NEVER stored (signed / expiring).
- ``store(site, locator, collected)``: the rows of that page take their alternative's candidates (the ``alt_key`` match;
  rows whose alternative is missing take the unclaimed ones in order, the same rule as playback). No candidate on the page
  -> the rows turn ``no_source`` (K5: invisible; admin "Oynatılamayanlar"); a later candidate makes them ``unknown`` again.
- ``fresh(row)``: the stored candidates while younger than ``source_recheck_days`` (setting, default 7).
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

from ... import db
from . import badhosts, blocked as sblocked, candidates as cands, direct, gate, limits, resolve

log = logging.getLogger("play.prefill")

NO_SOURCE = "no_source"
NO_SOURCE_TEXT = "taramada kaynak bulunamadı"
RECHECK_DAYS = 7
SHORTCUT = ("provider", "hop", "ajax")   # candidate kinds playback resolves without the episode page
AJAX_TYPES = ("ajax",)              # T32: session-bound AJAX hand-offs: the element's tokens are stored, the call runs at play


def _ajax_item(raw: dict, locator: str) -> Optional[dict]:
    """T32: an AJAX alternative stored as its PERSISTENT part (the element's tokens / form + cookie SPECS, never a cookie value
    or the answer's session-bound URL); playback sends the call again with a fresh cookie jar."""
    handoff = raw.get("handoff")
    if not isinstance(handoff, dict) or not handoff.get("url") or raw.get("resolver_type") not in AJAX_TYPES:
        return None
    item = {"alt_key": cands.alt_key(raw), "label": str(raw.get("label") or "")[:80], "kind": "ajax", "url": locator,
            "referer": locator, "needs_referer": False, "handoff": dict(handoff), "resolver": raw.get("resolver"),
            "resolver_type": raw.get("resolver_type"), "host": (urlparse(handoff["url"]).hostname or "").lower()}
    for key in ("lang", "language", "sub_mode", "audio_lang"):
        if raw.get(key):
            item[key] = raw[key]
    return item


def recheck_days() -> float:
    try:
        return max(0.5, float(limits.setting("source_recheck_days", RECHECK_DAYS)))
    except (TypeError, ValueError):
        return float(RECHECK_DAYS)


def _ad(url: str) -> str:
    return badhosts.bad_stream_host(url) or direct.ad_reason(url) or ""


def collect(ctx, html: str, locator: str) -> dict:
    """``{blocked: reason|None, found: [candidate]}`` of one episode / film page (see the module text). Never raises."""
    hit = sblocked.match(html, gate.episode_rules(ctx), "episode_page")
    if hit:
        return {"blocked": hit["reason"], "found": []}
    try:
        found = cands.discover(ctx, html, locator)
    except Exception:
        log.warning("candidate discovery of %s failed", locator, exc_info=True)
        found = []
    out = []
    for raw in found:
        if raw.get("trailer"):
            continue
        ajax_item = _ajax_item(raw, locator)
        if ajax_item is not None:
            out.append(ajax_item)
            continue
        item = {"alt_key": cands.alt_key(raw), "label": str(raw.get("label") or "")[:80], "kind": "page",
                "url": urljoin(locator, str(raw.get("url") or "")), "referer": locator, "needs_referer": False}
        try:
            cand = cands.resolve_candidate(ctx, raw, locator)
            if cand and isinstance(cand.get("stream"), dict) and cand["stream"]:
                streams = [s for s in cand["stream"].get("streams") or [] if isinstance(s, dict)]
                if streams and all(_ad(str(s.get("url") or "")) for s in streams):
                    continue                       # the type found only ad / tracker media
            elif cand:
                url = urljoin(locator, str(cand.get("url") or ""))
                if _ad(url):
                    continue
                hops = resolve.walk_site_hops(ctx, url, str(cand.get("referer") or locator))
                if _ad(hops["url"]):
                    continue
                item.update(url=hops["url"], referer=hops["referer"], needs_referer=bool(hops["needs_referer"]),
                            kind="hop" if hops.get("streams") else "provider")
        except Exception as exc:   # an intermediate page that did not answer now: playback keeps the full path
            item["error"] = " ".join(str(exc).split())[:120]
        if _ad(item["url"]):
            continue
        item["host"] = (urlparse(item["url"]).hostname or "").lower()
        out.append(item)
    return {"blocked": None, "found": out}


def assign(keys: list, found: list) -> dict:
    """``{alt_key: [candidates]}`` for the rows ``keys`` of one page (``''`` = the whole page: every candidate). The same rule
    as playback (``sources._own_candidates``): an alternative missing from the page takes the unclaimed candidates in order
    (one each, the last missing one the rest)."""
    out: dict = {}
    claimed = set(k for k in keys if k)
    unclaimed = [c for c in found if c["alt_key"] not in claimed]
    missing = [k for k in keys if k and not any(c["alt_key"] == k for c in found)]
    for k in keys:
        if not k:
            out[k] = list(found)
            continue
        mine = [c for c in found if c["alt_key"] == k]
        if not mine and k in missing:
            pos = missing.index(k)
            if pos < len(unclaimed):
                mine = unclaimed[pos:] if pos == len(missing) - 1 else [unclaimed[pos]]
        out[k] = mine
    return out


_PUBLIC = ("url", "referer", "needs_referer", "kind", "label", "host", "handoff", "resolver", "resolver_type", "lang",
           "language", "sub_mode", "audio_lang")


def _public(c: dict) -> dict:
    return {k: c[k] for k in _PUBLIC if k in c}


def store(site_id: str, locator: str, collected: dict, *, now: Optional[float] = None) -> dict:
    """Write the candidates of one page to its rows. ``{rows, with_candidates, no_source, revived}``."""
    t = int(time.time() if now is None else now)
    out = {"rows": 0, "with_candidates": 0, "no_source": 0, "revived": 0}
    found = list(collected.get("found") or [])
    with db.transaction() as conn:
        rows = conn.execute("SELECT id, alt_key, status FROM video_sources WHERE source=? AND locator=? AND kind!='trailer' "
                            "ORDER BY alt_index, id", (site_id, locator)).fetchall()
        mine = assign([r["alt_key"] for r in rows], found)
        for r in rows:
            out["rows"] += 1
            items = [_public(c) for c in mine.get(r["alt_key"]) or []]
            if items:
                out["with_candidates"] += 1
                first = next((c["url"] for c in items if c.get("kind") in SHORTCUT), items[0]["url"])
                conn.execute("UPDATE video_sources SET candidate_url=?, candidate_json=?, candidate_at=? WHERE id=?",
                             (first, json.dumps(items), t, r["id"]))
                if r["status"] == NO_SOURCE:
                    conn.execute("UPDATE video_sources SET status='unknown', failures=0, last_error=NULL, updated_at=? "
                                 "WHERE id=?", (t, r["id"]))
                    out["revived"] += 1
            else:
                conn.execute("UPDATE video_sources SET candidate_url=NULL, candidate_json=NULL, candidate_at=? WHERE id=?",
                             (t, r["id"]))
        if rows and not found:   # nothing on the page at all: K5 hides the episode (admin: "Oynatılamayanlar")
            out["no_source"] = conn.execute(
                "UPDATE video_sources SET status=?, last_error=?, resolved_payload=NULL, resolved_at=NULL, updated_at=? "
                "WHERE source=? AND locator=? AND kind!='trailer' AND status IN ('unknown','healthy','suspect','no_source')",
                (NO_SOURCE, NO_SOURCE_TEXT, t, site_id, locator)).rowcount
    if out["no_source"] or out["revived"]:
        from ..catalog import cache
        cache.refresh_soon()
    return out


def fresh(row: Any, now: Optional[float] = None) -> list:
    """The shortcut candidates (``provider`` / ``hop``) stored on ``row`` while fresh; [] otherwise."""
    try:
        at, raw = row["candidate_at"], row["candidate_json"]
    except (IndexError, KeyError):
        return []
    if not at or not raw:
        return []
    if (time.time() if now is None else now) - int(at) > recheck_days() * 86400:
        return []
    try:
        items = json.loads(raw)
    except ValueError:
        return []
    return [c for c in items if isinstance(c, dict) and c.get("kind") in SHORTCUT and c.get("url")
            and (c.get("kind") != "ajax" or isinstance(c.get("handoff"), dict))] if isinstance(items, list) else []


def as_raw(c: dict) -> dict:
    """A stored candidate as a resolver-free playback candidate (no hand-off; ``referer`` = the page it was found on). T32: an
    ``ajax`` one keeps its hand-off + resolver index: the AJAX call runs again now (fresh cookies)."""
    if c.get("kind") == "ajax" and isinstance(c.get("handoff"), dict):
        raw = {"url": c["url"], "label": c.get("label") or "", "handoff": dict(c["handoff"]), "resolver": c.get("resolver"),
               "resolver_type": c.get("resolver_type") or "ajax"}
        for key in ("lang", "language", "sub_mode", "audio_lang"):
            if c.get(key):
                raw[key] = c[key]
        return raw
    return {"url": c["url"], "label": c.get("label") or "", "referer": c.get("referer") or "",
            "resolver_type": "candidate"}


def update_from_play(row: Any, found: list, outcomes: list) -> None:
    """The full path just ran (the stored candidate failed or was missing): its reached addresses become the row's
    candidates (only the ones that produced streams)."""
    items = []
    for raw, o in zip(found, outcomes):
        if not o or not o.get("streams") or not o.get("provider_url"):
            continue
        ajax_item = _ajax_item(raw, row["locator"])
        if ajax_item is not None:   # T32: never the session-bound answer URL, always the call itself
            ajax_item.pop("alt_key", None)
            items.append(ajax_item)
            continue
        if raw.get("stream") and not o.get("direct"):
            kind = "page"
        else:
            kind = "hop" if o.get("direct") else "provider"
        url = o["provider_url"]
        items.append({"url": url, "referer": o.get("referer") or row["locator"], "needs_referer": bool(o.get("needs_referer")),
                      "kind": kind, "label": str(raw.get("label") or "")[:80], "host": (urlparse(url).hostname or "").lower()})
    if not items:
        return
    first = next((c["url"] for c in items if c["kind"] in SHORTCUT), items[0]["url"])
    try:
        db.execute("UPDATE video_sources SET candidate_url=?, candidate_json=?, candidate_at=? WHERE id=?",
                   (first, json.dumps(items), int(time.time()), row["id"]))
    except Exception as exc:   # never breaks playback (an old partial test schema has no candidate columns)
        log.debug("candidate update of %s failed: %s", row["id"], exc)
