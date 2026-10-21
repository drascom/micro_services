"""Sade operasyon paneli: kaynak özeti, tarama geçmişi, canlı aktivite, self-heal.

`/admin` tek sayfa; veri `/api/ops/*`. Kapalı LAN test uygulaması: token yok.
Video kaynağı / kimlik bakım uçları (`/api/admin/video-sources`, `/api/admin/identities`)
API.md'de anıldığı için korunur, arayüzde yoktur.
"""
from __future__ import annotations

import os
from collections import Counter
from contextlib import closing
from typing import Any, Optional
from urllib.parse import urljoin

from fastapi import APIRouter, BackgroundTasks, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import autoscan, cache, db, settings
from ..errors import ApiError
from ..scraper import config as scfg, playheal, resolvers as sresolvers, state as sstate
from ..scraper.providers import registry

router = APIRouter()

ADMIN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "admin")
_ASSETS = {"app.js": "text/javascript", "library.js": "text/javascript", "settings.js": "text/javascript",
           "categories.js": "text/javascript",
           "onboard.js": "text/javascript", "style.css": "text/css"}


# --- pages ------------------------------------------------------------------

@router.get("/admin", include_in_schema=False)
def admin_page() -> FileResponse:
    return FileResponse(os.path.join(ADMIN_DIR, "index.html"), media_type="text/html",
                        headers={"Cache-Control": "no-cache"})


@router.get("/admin/{name}", include_in_schema=False)
def admin_asset(name: str) -> FileResponse:
    if name not in _ASSETS:
        raise ApiError(404, "not_found", "not found")
    return FileResponse(os.path.join(ADMIN_DIR, name), media_type=_ASSETS[name],
                        headers={"Cache-Control": "no-cache"})


# --- helpers ----------------------------------------------------------------

def _known_site(site: str) -> None:
    if site not in scfg.list_sites():
        raise ApiError(404, "not_found", f"unknown scraper site {site!r}")


def _next_scan(site: str) -> Optional[str]:
    """ISO time of the next automatic scan of ``site`` (None when auto scan is off for it).

    Comes from the admin settings (``app.settings``) + last run, not from a fixed job."""
    try:
        return autoscan.next_scan_iso(site)
    except Exception:
        return None


def _auto_scan(site: str) -> Optional[dict[str, Any]]:
    """Auto-scan setting of ``site`` for the overview chips (enabled / interval / next)."""
    try:
        st = autoscan.site_status(site)
        return {"enabled": st["enabled"], "interval_hours": st["interval_hours"], "next_scan_at": st["next_scan_at"]}
    except Exception:
        return None


def _status(state: Optional[dict], last_run: Optional[dict]) -> str:
    if not state and not last_run:
        return "never_run"
    if last_run and last_run.get("status") == "error" or (state or {}).get("last_error"):
        return "error"
    if ((state or {}).get("drift") or {}).get("drift"):
        return "drift"
    if last_run and last_run.get("status") == "partial":
        return "partial"
    return "healthy"


RECENT_N = 24


def _recent(site: str) -> list[dict[str, Any]]:
    """Last ``RECENT_N`` runs of ``site``, oldest first (health strip)."""
    rows = sstate.list_ops("runs", site, RECENT_N)[::-1]
    return [{"id": r.get("id"), "started_at": r.get("started_at"), "duration": r.get("duration"),
             "status": r.get("status"), "scraped": r.get("scraped"), "pages": r.get("pages"),
             "rate": r.get("rate"), "error": r.get("error")} for r in rows]


def _catalog_counts() -> dict[str, dict[str, Any]]:
    """Per-source item count + a few newest titles from the existing catalog tables."""
    out: dict[str, dict[str, Any]] = {}
    try:
        for r in db.query("SELECT source, COUNT(*) n FROM source_items GROUP BY source"):
            out[r["source"]] = {"item_count": r["n"], "recent_items": []}
        for src, v in out.items():
            rows = db.query(
                """SELECT i.title, i.added_at FROM source_items s JOIN library_items i ON i.id=s.canonical_id
                   WHERE s.source=? ORDER BY s.fetched_at DESC LIMIT 3""", (src,))
            v["recent_items"] = [{"title": x["title"], "added_at": x["added_at"]} for x in rows]
    except Exception:
        pass
    return out


def _playback_health(site: str, last_heal: Optional[dict]) -> dict[str, Any]:
    """Playback-heal window of ``site`` (memory, light). ``last_trigger_at`` falls back to the newest heal when that
    was a playback-triggered one (the memory is empty after a restart)."""
    try:
        out = playheal.summary(site)
    except Exception:
        out = {"window_n": 0, "failed": 0, "last_trigger_at": None, "last_skip": None}
    if not out.get("last_trigger_at") and last_heal and last_heal.get("trigger") == "playback":
        out["last_trigger_at"] = last_heal.get("at")
    try:   # the playback issue ledger (library/playissues.py): sources with a reported / diagnosed problem now
        from ..library import playissues
        out["issues"] = playissues.summary(site)
    except Exception:
        out["issues"] = {"open": 0, "heal_open": 0}
    return out


def _site_summary(site: str, state: Optional[dict], active: list[dict]) -> dict[str, Any]:
    last = (sstate.list_ops("runs", site, 1) or [None])[0]
    lh = (sstate.list_ops("heals", site, 1) or [None])[0]
    busy = {a["kind"] for a in active if a["site"] == site}
    return {
        "site": site,
        "status": _status(state, last),
        "last_run_at": (last or {}).get("started_at") or (state or {}).get("last_run_at"),
        "duration": (last or {}).get("duration"),
        "scraped": (last or {}).get("scraped"),
        "pages": (last or {}).get("pages"),
        "rate": (last or {}).get("rate"),
        "last_status": (last or {}).get("status"),
        "last_error": (last or {}).get("error") or (state or {}).get("last_error"),
        "drift": bool(((state or {}).get("drift") or {}).get("drift")),
        "drift_reasons": ((state or {}).get("drift") or {}).get("reasons") or [],
        "config_version": (state or {}).get("config_version"),
        "next_scan": _next_scan(site),
        "auto_scan": _auto_scan(site),
        "last_heal": lh,
        "last_resolver": (state or {}).get("last_resolver"),  # last playback resolution (state.record_resolver)
        "playback_health": _playback_health(site, lh),        # playheal window: {window_n, failed, last_trigger_at, last_skip}
        "heal_cooldown_until": (sstate.get_heal_cooldown(site) or {}).get("until_at"),
        "scanning": "scan" in busy,
        "healing": "heal" in busy,
        "consecutive_failures": (state or {}).get("consecutive_failures"),
        "recent": _recent(site),
    }


# --- read -------------------------------------------------------------------

def _schedule() -> dict[str, Any]:
    """Legacy ``schedule`` block, now derived from the admin settings."""
    per_site = settings.all_sites()
    enabled = [s for s, v in per_site.items() if v["enabled"]]
    return {"sites": enabled,
            "interval": int(min(per_site[s]["interval_hours"] for s in enabled) * 3600) if enabled else 0,
            "intervals": {s: per_site[s]["interval_hours"] for s in enabled}}


@router.get("/api/ops/overview")
def overview() -> dict[str, Any]:
    states = {s["site_id"]: s for s in sstate.list_sites_state()}
    active = sstate.activity_list()
    catalog = _catalog_counts()
    sites = [_site_summary(s, states.get(s), active) for s in scfg.list_sites()]
    for s in sites:
        s["catalog"] = catalog.get(s["site"]) or {"item_count": 0, "recent_items": []}
    return {
        "now": sstate._now(),
        "sites": sites,
        "active": active,
        "schedule": _schedule(),
    }


@router.get("/api/ops/active")
def active() -> dict[str, Any]:
    # `recent_heal`: az önce biten heal (banner için); ayrıca polling bitişi yakalar.
    heals = sstate.list_ops("heals", None, 1)
    try:   # newest finished source-finder job: lets the panel notice a new ``finder`` event
        from ..library import sourcefinder
        last_finder = (sourcefinder.events(1) or [None])[0]
    except Exception:  # pragma: no cover
        last_finder = None
    return {"now": sstate._now(), "active": sstate.activity_list(),
            "last_run": (sstate.list_ops("runs", None, 1) or [None])[0],
            "last_heal": heals[0] if heals else None,
            "last_tmdb": (sstate.list_ops("tmdb", None, 1) or [None])[0],
            "last_finder": last_finder}


@router.get("/api/ops/runs")
def runs(site: Optional[str] = Query(None), limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    return {"runs": sstate.list_ops("runs", site, limit)}


@router.get("/api/ops/stream-host-rules")
def stream_host_rules() -> dict[str, Any]:
    """What the server's probe verified per stream host (``library/hostrules.py``): ``{rules: [{host, proxy_required, headers, reason,
    learned_from_source, learned_at, last_ok_at, fail_count, expires_at, suspended, active}]}``."""
    from ..library import hostrules
    return {"rules": hostrules.listing()}


@router.post("/api/ops/stream-host-rules/{host}/suspend")
def stream_host_rule_suspend(host: str) -> dict[str, Any]:
    from ..library import hostrules
    return {"ok": hostrules.suspend(host)}


@router.delete("/api/ops/stream-host-rules/{host}")
def stream_host_rule_delete(host: str) -> dict[str, Any]:
    from ..library import hostrules
    return {"ok": hostrules.delete(host)}


@router.get("/api/ops/heals")
def heals(site: Optional[str] = Query(None), limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    return {"heals": sstate.list_ops("heals", site, limit)}


def _heal_kind(h: dict[str, Any]) -> str:
    if h.get("outcome") == "rolled_back" or h.get("kind") == "rollback":
        return "rollback"
    if h.get("outcome") == "skipped_cooldown":
        return "cooldown"
    return "heal"


def _top(counter: Counter, n: int = 3) -> list[dict[str, Any]]:
    return [{"name": k, "count": c} for k, c in counter.most_common(n)]


def _agent_summary(h: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Boiled-down agent log of a heal (``h["agent"]["events"]`` in the onboarding event format: kind
    tool|tool_result|say|error|retry|user): counts, tool usage and the agent's last words. None without a log."""
    agent = h.get("agent")
    if not isinstance(agent, dict):
        return None
    evs = [e for e in (agent.get("events") or []) if isinstance(e, dict)]
    tools = Counter(str(e.get("name") or "?") for e in evs if e.get("kind") == "tool")
    turns = next((v for v in (agent.get("turns"), h.get("turns")) if isinstance(v, int) and not isinstance(v, bool)),
                 sum(tools.values()))
    say = next((str(e.get("text")) for e in reversed(evs) if e.get("kind") == "say" and e.get("text")), "")
    return {"events": len(evs) + int(agent.get("events_dropped") or 0), "turns": turns, "tools": dict(tools),
            "tests": tools.get("test_config", 0), "errors": sum(1 for e in evs if e.get("kind") == "error"),
            "last_say": say[:300]}


def _evidence_summary(evidence: Any) -> Optional[dict[str, Any]]:
    """Why a playback heal ran (``heal_site_playback`` evidence): how many failed sources, which stage/host/error."""
    if not isinstance(evidence, dict):
        return None
    failing = [f for f in (evidence.get("failing") or []) if isinstance(f, dict)]
    window = evidence.get("window") if isinstance(evidence.get("window"), dict) else {}
    return {"sources": len(failing), "window_n": window.get("n"), "window_failed": window.get("failed"),
            "stages": _top(Counter(str(f["stage"]) for f in failing if f.get("stage"))),
            "hosts": _top(Counter(str(f["host"]) for f in failing if f.get("host"))),
            "errors": _top(Counter(str(f["error"])[:80] for f in failing if f.get("error")), 2),
            "ok_examples": len(evidence.get("ok_examples") or [])}


def _playback_view(h: dict[str, Any]) -> dict[str, Any]:
    """Extra feed fields of a heal event: ``playback`` (triggered by playback failures), ``evidence_summary``,
    ``agent_summary``. The raw ``evidence`` / ``agent`` stay on the event."""
    extra: dict[str, Any] = {}
    evidence, agent = _evidence_summary(h.get("evidence")), _agent_summary(h)
    if h.get("trigger") == "playback" or h.get("page") == "playback" or evidence:
        extra["playback"] = True
    if evidence:
        if isinstance(h.get("layers"), list) and h["layers"]:   # the layer(s) the repair was scoped to (heal_agent scope gate)
            evidence["layers"] = h["layers"]
        extra["evidence_summary"] = evidence
    if agent:
        extra["agent_summary"] = agent
    return extra


@router.get("/api/ops/events")
def events(site: Optional[str] = Query(None), limit: int = Query(50, ge=1, le=200),
           before: Optional[str] = Query(None)) -> dict[str, Any]:
    """Merged run + heal feed, newest first. ``before``: ISO time cursor (exclusive).

    Each event: ``kind`` scan|heal|cooldown|rollback|tmdb|onboard|finder, ``at``, plus the raw record.
    ``finder`` events (``?site=`` matches the site a source was found on) are finished source-finder jobs: ``id, title,
    canonical_id, episode_id, season, episode, state`` (found | not_found), ``method`` (retry | search | heal), ``site``,
    ``steps`` (``[{name, ok, ms, note}]``), ``source_id, error, trigger, seconds``.
    ``tmdb`` events (site ``"tmdb"``) are finished admin backfills/previews (counters flat).
    ``onboard`` events (site ``"onboard"``, ``?site=`` also matches their ``site_id``) are finished site-onboarding
    pi runs / saved sites: ``draft_id, url, site_id?, status, seconds, turns, passed, notes``.
    ``playback_issue`` events are the playback problems clients reported / the server diagnosed (``library/playissues.py``): ``id, at,
    first_at, site, code`` (issue class), ``label, host, provider, stream_type, note, http, sources, reports, heal_class, triggered_at``
    (a playback heal was started for it), ``episodes[{source_id, title, season, episode, locator, reports, client_code, at}]``; the
    admin button "Ajan düzeltsin" posts ``/api/ops/sites/{site}/heal-playback``; ``host_rule`` = the stream host's verified rule
    (``/api/ops/stream-host-rules``, ``library/hostrules.py``) or null.
    Heal events carry ``can_rollback`` (newest applied heal of the site, not yet rolled back). Heals started by playback
    failures (``trigger=="playback"``, or any heal with ``evidence``) also carry ``playback: true``, ``evidence_summary``
    (``sources, window_n, window_failed, stages, hosts, errors, ok_examples``; ``layers`` = the layer(s) the agent repair was scoped to)
    and, when an agent ran, ``agent_summary`` (``events, turns, tools, tests, errors, last_say``); the raw record also carries
    ``layers``, ``touched_keys`` / ``touched_paths`` (what the proposal changed) of an agent repair.
    """
    heals_all = sstate.list_ops("heals", None, 1000)  # newest first
    revertable: set[str] = set()
    for s in {h.get("site") for h in heals_all}:
        for h in heals_all:  # newest first: first applied heal decides; a later rollback cancels
            if h.get("site") != s:
                continue
            if _heal_kind(h) == "rollback":
                break
            if h.get("applied") and h.get("outcome") == "fixed":
                revertable.add(h.get("id"))
                break
    rows: list[dict[str, Any]] = []
    for r in sstate.list_ops("runs", site, 1000):
        rows.append({**r, "kind": "scan", "at": r.get("started_at")})
    for h in heals_all:
        if site and h.get("site") != site:
            continue
        rows.append({**h, "kind": _heal_kind(h), "can_rollback": h.get("id") in revertable, **_playback_view(h)})
    for t in sstate.list_ops("tmdb", site, 1000):
        rows.append({**t, "kind": "tmdb", "at": t.get("started_at")})
    for o in sstate.list_ops("onboard", None, 1000):
        if not site or site in (o.get("site"), o.get("site_id")):
            rows.append({**o, "kind": "onboard", "at": o.get("at")})
    try:   # playback issues clients reported / the server diagnosed (library/playissues.py): one event per (site, class, stream host)
        from ..library import playissues
        rows.extend(playissues.events(60, site))
    except Exception:  # pragma: no cover - the feed must never fail on an optional kind
        pass
    try:   # source finder jobs (library/sourcefinder.py, table finder_jobs): ``?site=`` matches the site a source was found on
        from ..library import sourcefinder
        rows.extend(f for f in sourcefinder.events(200) if not site or f.get("site") == site)
    except Exception:  # pragma: no cover - the feed must never fail on an optional kind
        pass
    rows = [e for e in rows if e.get("at") and (not before or e["at"] < before)]
    rows.sort(key=lambda e: e["at"], reverse=True)
    page = rows[:limit]
    return {"events": page, "next_before": page[-1]["at"] if len(rows) > limit else None}


# --- trigger (background) ---------------------------------------------------

def _bg_scan(site: str) -> None:
    try:
        from ..library import ingest_source
        ingest_source(site, trigger="manual")
        cache.refresh()
    except Exception:  # pragma: no cover
        pass
    finally:
        sstate.activity_end(site, "scan")


def _bg_heal(site: str, force: bool = False) -> None:
    from ..scraper import drift as sdrift, fetch, heal as sheal, parse, schema
    rescan = False
    try:
        cfg = scfg.load_site(site)
        prev = sstate.get_site_state(site) or {}
        reasons = (prev.get("drift") or {}).get("reasons") or ["manual heal requested"]
        try:
            html = fetch.page(cfg, urljoin(cfg.base_url, cfg.list_url), wait_for=cfg.row_selector)
        except Exception as exc:
            sstate.record_ops_heal({
                "site": site, "at": sstate._now(), "trigger": "manual", "reasons": reasons,
                "provider": os.environ.get("SCRAPER_HEAL_PROVIDER") or "codex_cli", "page": "list",
                "outcome": sheal.FAILED, "applied": False, "error": f"fetch_failed: {str(exc)[:200]}",
                "diff": [], "duration": 0,
            })
            return
        if not force:  # live re-check: state may be stale, never "heal" a healthy page
            _v, metrics = schema.validate_items(
                cfg.schema, parse.parse_list(html, cfg.row_selector, cfg.list_fields))
            verdict = sdrift.detect(metrics, cfg.baseline())
            if not verdict["drift"]:
                return
            reasons = verdict["reasons"]
        # trigger="manual": an explicit admin action ignores the heal cooldown.
        res = sheal.heal(cfg, page="list", html=html, reasons=reasons, trigger="manual")
        sstate.record_heal(site, {
            "status": res.get("outcome"), "outcome": res.get("outcome"),
            "applied": bool(res.get("applied")), "new_version": res.get("new_version"),
            "reason": res.get("reason"),
        })
        rescan = bool(res.get("applied"))
    except Exception:  # pragma: no cover
        pass
    finally:
        sstate.activity_end(site, "heal")
    if rescan and sstate.activity_start(site, "scan", "heal"):
        _bg_scan(site)  # healed config -> pick the data up right away


@router.post("/api/ops/sites/{site}/scan")
def scan(site: str, background_tasks: BackgroundTasks) -> dict[str, Any]:
    _known_site(site)
    if not sstate.activity_start(site, "scan", "manual"):
        return {"started": False, "reason": "already_running"}
    background_tasks.add_task(_bg_scan, site)
    return {"started": True}


@router.post("/api/ops/sites/{site}/heal")
def heal(site: str, background_tasks: BackgroundTasks, force: bool = Query(False)) -> dict[str, Any]:
    """Manual heal. Without ``force`` it only runs when drift is recorded."""
    _known_site(site)
    if not force and not (((sstate.get_site_state(site) or {}).get("drift") or {}).get("drift")):
        return {"started": False, "reason": "no_drift"}
    if not sstate.activity_start(site, "heal", "manual"):
        return {"started": False, "reason": "already_running"}
    background_tasks.add_task(_bg_heal, site, force)
    return {"started": True}


def _bg_heal_playback(site: str, evidence: dict[str, Any]) -> None:
    try:
        playheal.run_reserved(site, evidence, "manual")
    except Exception:  # pragma: no cover
        pass


@router.post("/api/ops/sites/{site}/heal-playback")
def heal_playback(site: str, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Manual playback heal: repair run from the playback-failure evidence of ``site`` (``trigger="manual"``).

    Ignores the thresholds and the cooldown and uses the best evidence there is (the playheal window, else the
    sources the library marked suspect/broken). ``{started: false, reason: no_evidence | already_running}`` otherwise."""
    _known_site(site)
    evidence, reason = playheal.begin_manual(site)
    if evidence is None:
        return {"started": False, "reason": reason}
    background_tasks.add_task(_bg_heal_playback, site, evidence)
    return {"started": True}


@router.post("/api/ops/sites/{site}/rollback")
def rollback(site: str) -> dict[str, Any]:
    _known_site(site)
    if any(a["site"] == site and a["kind"] in ("scan", "heal") for a in sstate.activity_list()):
        raise ApiError(409, "busy", "site is scanning/healing")
    try:
        version = scfg.rollback_config(site)
    except ValueError as exc:
        raise ApiError(409, "no_previous_version", str(exc))
    sstate.record_ops_heal({
        "site": site, "at": sstate._now(), "trigger": "manual", "reasons": [], "provider": None,
        "outcome": "rolled_back", "kind": "rollback", "applied": False, "new_version": version,
        "error": None, "diff": [], "duration": 0,
    })
    return {"ok": True, "version": version}


@router.get("/api/ops/resolvers")
def resolver_catalog() -> dict[str, Any]:
    """Generic yaml resolver types (name, description, parameter schema) and the video host providers."""
    return {"resolvers": sresolvers.catalog(), "providers": registry.catalog()}


# --- maintenance endpoints kept for API.md (no UI) --------------------------

@router.get("/api/admin/video-sources")
def video_sources_admin():
    result = db.query('''SELECT v.id,v.canonical_id,v.source,v.kind,v.episode_id,v.status,v.failures,v.last_error,
        v.last_checked_at,v.last_success_at,v.trailer_dead,i.title FROM video_sources v JOIN library_items i ON i.id=v.canonical_id
        ORDER BY CASE v.status WHEN 'broken' THEN 0 WHEN 'suspect' THEN 1 ELSE 2 END,i.title,v.id''')
    return {"sources": [dict(r) for r in result]}


@router.post("/api/admin/video-sources/{source_id}/{action}")
def video_source_action(source_id: str, action: str):
    from ..library import videos
    row = db.query_one("SELECT id FROM video_sources WHERE id=?", (source_id,))
    if not row:
        raise ApiError(404, "not_found", "Kaynak bulunamadı")
    try:
        if action == "retry":
            result = videos.retry(source_id)
        elif action == "disable":
            db.execute("UPDATE video_sources SET status='disabled' WHERE id=?", (source_id,))
            result = {"ok": True}
        elif action == "enable":
            db.execute("UPDATE video_sources SET status='unknown',failures=0,last_error=NULL,resolved_payload=NULL,resolved_at=NULL WHERE id=?", (source_id,))
            result = {"ok": True}
        else:
            raise ValueError("Bilinmeyen işlem")
    except Exception as exc:
        raise ApiError(400, "source_action_failed", str(exc)[:250])
    cache.refresh()
    return result


@router.get("/api/admin/identities")
def identities_admin():
    from ..library import tmdb
    rows = db.query('''SELECT i.id,i.title,i.year,i.type,i.tmdb_id,i.imdb_id,
        (SELECT reason FROM identity_reviews WHERE canonical_id=i.id AND status='pending' LIMIT 1) reason
        FROM library_items i ORDER BY i.title''')
    return {"tmdb_configured": tmdb.enabled(), "items": [dict(r) for r in rows]}


class IdentityBinding(BaseModel):
    tmdb_id: Optional[int] = Field(default=None, gt=0)
    imdb_id: Optional[str] = Field(default=None, pattern=r"^tt\d{7,10}$")


@router.post("/api/admin/identities/{item_id}")
def identity_bind(item_id: str, body: IdentityBinding):
    from ..library import identity
    try:
        with closing(db.connect()) as conn, conn:
            identity.bind(conn, item_id, body.tmdb_id, body.imdb_id)
    except ValueError as exc:
        raise ApiError(409, "identity_conflict", str(exc))
    cache.refresh()
    return {"ok": True}
