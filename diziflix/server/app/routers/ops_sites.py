"""Admin "Siteler" tab: list every registered site (hand-built ones included), view its yaml, rename, delete.

`GET    /api/ops/sites/manage`            ``{sites: [row]}``: ``site_id, display_name, base_url, version, hand_built,
                                          auto_scan{enabled, interval_hours, next_scan_at}, last_run{at, status, scraped,
                                          ingested}|null, counts{titles, series, movies, episodes, with_sources}, search,
                                          providers[], can_rollback, busy`` (``busy`` = scan | heal | onboard | finder | null)
`GET    /api/ops/sites/{site}/config`     ``{site_id, version, yaml_text, versions[{version, updated_at, active}], baseline}``
                                          (secret-looking values masked; 404 for a site that is not registered)
`POST   /api/ops/sites/{site}/rename`     ``{display_name}`` -> new config version ``{version, display_name}``
`DELETE /api/ops/sites/{site}?purge=1`    ``{deleted, files, purged{...}, tombstone, hand_built, note?, kept_user_data, warnings?}``;
                                          409 ``busy`` while the site scans / heals / is being edited, 404 unknown site.
                                          ``purge=0`` removes only the config + settings (library records stay).
Delete, scan, heal, rollback, single-site settings stay in ``ops.py`` / ``ops_settings.py``; the removal itself is
``library/purge.py``.
"""
from __future__ import annotations

import copy
import datetime
import os
import re
import time
from typing import Any, Optional

import yaml
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from .. import autoscan, db
from ..errors import ApiError
from ..library import purge
from ..scraper import config as scfg, site_search, state as sstate

router = APIRouter(prefix="/api/ops/sites")

BUSY_ORDER = ("scan", "heal", "onboard", "finder")
YAML_MAX = 200_000
_SECRET_LINE = re.compile(r"(?im)^(\s*(?:cookie|authorization|proxy-authorization|x-api-key|api[_-]?key|token|secret|password)\s*:\s*).+$")


class RenameBody(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=80)


# --- helpers ----------------------------------------------------------------------------------------------------------

def _registered(site: str) -> scfg.SiteConfig:
    if site not in scfg.list_sites():
        raise ApiError(404, "not_found", f"unknown scraper site {site!r}")
    try:
        return scfg.load_site(site)
    except Exception as exc:
        raise ApiError(500, "config_unreadable", f"the config of {site!r} cannot be read ({type(exc).__name__})")


def _redact(text: str) -> str:
    """The yaml text with the value of secret-looking keys masked (a site config should carry none)."""
    return _SECRET_LINE.sub(lambda m: m.group(1) + "***", text)


def _busy_map() -> dict[str, str]:
    """``{site: running job kind}`` (scan | heal | onboard | finder; the first of that order when several run)."""
    kinds: dict[str, set[str]] = {}
    active = sstate.activity_list()
    for job in active:
        if job["kind"] in ("scan", "heal"):
            kinds.setdefault(job["site"], set()).add(job["kind"])
    for job in active:   # an onboarding run that EDITS a registered site
        if job["site"] == "_onboard" and job["kind"] == "onboard" and job.get("draft_id"):
            try:
                from ..scraper import onboard_store
                draft = onboard_store.get_draft(str(job["draft_id"])) or {}
            except Exception:
                draft = {}
            if draft.get("edit_site_id"):
                kinds.setdefault(str(draft["edit_site_id"]), set()).add("onboard")
    try:   # a source-finder job running for a title this site serves may still write / repair its sources
        from ..library import sourcefinder
        with sourcefinder._lock:
            cids = [j.get("cid") for j in sourcefinder._inflight.values() if j.get("cid")]
        if cids:
            marks = ",".join("?" * len(cids))
            for row in db.query(f"SELECT DISTINCT source FROM video_sources WHERE canonical_id IN ({marks})", cids):
                kinds.setdefault(row["source"], set()).add("finder")
    except Exception:
        pass
    return {site: next(k for k in BUSY_ORDER if k in found) for site, found in kinds.items()}


def _counts() -> dict[str, dict[str, int]]:
    """Per-source library counts in two grouped queries: titles / series / movies (distinct canonical ids of the source's
    ``source_items``), episodes (distinct episodes with a video source) and titles with a playable (non-trailer) source."""
    out: dict[str, dict[str, int]] = {}

    def row(source: str) -> dict[str, int]:
        return out.setdefault(source, {"titles": 0, "series": 0, "movies": 0, "episodes": 0, "with_sources": 0})

    try:
        for r in db.query("SELECT s.source AS source, i.type AS type, COUNT(DISTINCT s.canonical_id) AS n "
                          "FROM source_items s JOIN library_items i ON i.id=s.canonical_id GROUP BY s.source, i.type"):
            item = row(r["source"])
            item["titles"] += r["n"]
            if r["type"] == "series":
                item["series"] += r["n"]
            elif r["type"] == "movie":
                item["movies"] += r["n"]
        for r in db.query("SELECT source, COUNT(DISTINCT CASE WHEN kind='episode' THEN canonical_id||'|'||episode_id END) AS episodes, "
                          "COUNT(DISTINCT CASE WHEN kind!='trailer' THEN canonical_id END) AS with_sources "
                          "FROM video_sources GROUP BY source"):
            item = row(r["source"])
            item["episodes"], item["with_sources"] = r["episodes"], r["with_sources"]
    except Exception:   # the list must come up even when the library tables are not readable
        pass
    return out


def _interval(hours: Any) -> Any:
    try:
        value = float(hours)
    except (TypeError, ValueError):
        return None
    return int(value) if value == int(value) else round(value, 2)


def _auto_scan(site: str) -> dict[str, Any]:
    try:
        st = autoscan.site_status(site)
        return {"enabled": bool(st["enabled"]), "interval_hours": _interval(st["interval_hours"]),
                "next_scan_at": st["next_scan_at"]}
    except Exception:
        return {"enabled": False, "interval_hours": None, "next_scan_at": None}


def _row(site: str, last_runs: dict[str, dict], counts: dict, busy: dict) -> dict[str, Any]:
    run = last_runs.get(site)
    row: dict[str, Any] = {
        "site_id": site, "display_name": site, "base_url": "", "version": None, "hand_built": False,
        "auto_scan": _auto_scan(site),
        "last_run": ({"at": run.get("started_at"), "status": run.get("status"), "scraped": run.get("scraped"),
                      "ingested": run.get("ingested")} if run else None),
        "counts": counts.get(site) or {"titles": 0, "series": 0, "movies": 0, "episodes": 0, "with_sources": 0},
        "search": False, "providers": [], "can_rollback": False, "busy": busy.get(site),
    }
    try:
        row["hand_built"] = purge.is_hand_built(site)
    except Exception:
        pass
    try:
        row["search"] = bool(site_search.supports(site))
    except Exception:
        pass
    try:
        cfg = scfg.load_site(site)
    except Exception as exc:   # an unreadable yaml still lists (the admin can delete it)
        row["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        return row
    row["display_name"] = str(cfg.data.get("display_name") or "").strip() or site
    row["base_url"] = cfg.base_url
    row["version"] = cfg.version
    try:
        row["providers"] = list(cfg.providers or [])
    except Exception:
        pass
    row["can_rollback"] = any(v < cfg.version for v in scfg.archived_versions(site))
    return row


# --- read -------------------------------------------------------------------------------------------------------------

@router.get("/manage")
def manage() -> dict[str, Any]:
    """Every registered site with its status, counts and what can be done to it (see the module doc)."""
    last_runs: dict[str, dict] = {}
    for run in sstate.list_ops("runs", None, 1000):   # newest first: the first run seen of a site is its last one
        last_runs.setdefault(str(run.get("site")), run)
    counts, busy = _counts(), _busy_map()
    return {"sites": [_row(site, last_runs, counts, busy) for site in scfg.list_sites()]}


def _iso(value: Any, path: str) -> Optional[str]:
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        return value.strip()
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(os.path.getmtime(path)))
    except OSError:
        return None


def _versions(site: str, cfg: scfg.SiteConfig) -> list[dict[str, Any]]:
    rows = [{"version": cfg.version, "updated_at": _iso(cfg.data.get("updated_at"), cfg.path), "active": True}]
    for number in scfg.archived_versions(site):
        path = os.path.join(scfg.CONFIG_DIR, f"{site}.v{number}.yaml")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError):
            data = {}
        rows.append({"version": number, "updated_at": _iso(data.get("updated_at") if isinstance(data, dict) else None, path),
                     "active": False})
    return sorted(rows, key=lambda r: r["version"], reverse=True)


@router.get("/{site}/config")
def site_config(site: str) -> dict[str, Any]:
    """The active yaml text (secrets masked), its version, the archived versions and the baseline of a registered site."""
    cfg = _registered(site)
    try:
        with open(cfg.path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        raise ApiError(404, "not_found", f"the config file of {site!r} could not be read")
    try:
        baseline = cfg.baseline()
    except (OSError, ValueError):
        baseline = {}
    return {"site_id": site, "version": cfg.version, "yaml_text": _redact(text)[:YAML_MAX], "yaml_truncated": len(text) > YAML_MAX,
            "versions": _versions(site, cfg), "baseline": baseline}


# --- write ------------------------------------------------------------------------------------------------------------

@router.post("/{site}/rename")
def rename(site: str, body: RenameBody) -> dict[str, Any]:
    """Change the display name: a new config version (the previous one is archived, so a rollback restores it)."""
    cfg = _registered(site)
    name = " ".join(body.display_name.split())
    if not name:
        raise ApiError(422, "invalid_name", "display_name is empty")
    kind = _busy_map().get(site)
    if kind in ("heal", "onboard"):   # they write the config themselves
        raise ApiError(409, "busy", f"site is busy ({kind})")
    data = copy.deepcopy(cfg.data)
    data["display_name"] = name
    return {"version": scfg.save_new_version(site, data), "display_name": name}


@router.delete("/{site}")
def delete(site: str, purge_records: bool = Query(True, alias="purge")) -> dict[str, Any]:
    """Delete a registered site (hand-built ones too); see ``library/purge.py`` for what goes."""
    _registered(site)
    kind = _busy_map().get(site)
    if kind:
        raise ApiError(409, "busy", f"site is busy ({kind}); wait for it to finish")
    if not sstate.activity_start(site, "scan", "delete"):   # holds the scan slot: no manual / scheduled scan starts meanwhile
        raise ApiError(409, "busy", "site is busy (scan); wait for it to finish")
    try:
        result = purge.delete_site(site, purge=purge_records)
    except LookupError as exc:
        raise ApiError(404, "not_found", str(exc))
    except purge.DeleteIncomplete as exc:
        raise ApiError(500, "delete_incomplete", f"the site was only partly deleted: {exc}; purged={exc.result.get('purged')}")
    finally:
        sstate.activity_end(site, "scan")
    out = {k: result[k] for k in ("deleted", "files", "purged", "tombstone", "hand_built", "kept_user_data")}
    for key in ("note", "warnings"):
        if result.get(key):
            out[key] = result[key]
    return out
