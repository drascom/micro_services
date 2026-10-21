"""Admin endpoints for sites, their stage cards and map versions (/api/control/sites*, /api/control/summary). T10.

Stage work is delegated to ``control.stages.actions`` / ``control.stages.status`` (T08); this module only validates input,
maps errors to the common envelope (Turkish ``message``) and reads a few counters straight from the DB.
"""
from __future__ import annotations

import importlib
from typing import Any, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, Body, Response

from .. import db
from ..core.fetch import netguard
from ..errors import ApiError
from ..control.jobs import store as jobs_store
from ..control.crawl import status as crawl_status  # T11
from ..control.sitemap import meta as sitemap_meta, site_notes, store as sitemap_store
from ..control.sitemap.schema import STAGE_PAGE

router = APIRouter()

MAX_HINT = 2000
MAX_TITLE = 200
MAX_URL = 2048
MAX_NAME = 80
PASSED = ("passed", "passed_warn", "skipped")
#: optional fields the admin may mark "not on this site" per stage (url/title/key never)
EXTRA_SKIPPABLE = {"title": ("genres", "overview")}
NEVER_SKIP = set(sitemap_meta.NEVER_SKIPPED) | {"key"}
PAUSED_REASON = "site paused"   # == control.stages.engine.PAUSED (that module is imported lazily)


# ---------------------------------------------------------------------------------------------- helpers

def _err(status: int, code: str, message: str) -> ApiError:
    return ApiError(status, code, message)


def _stages():
    """``(actions, status)`` of the stage engine (T08); 503 while it is not installed."""
    try:
        actions = importlib.import_module("..control.stages.actions", __package__)
        status = importlib.import_module("..control.stages.status", __package__)
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("app.control.stages"):
            raise _err(503, "stages_unavailable", "Aşama motoru henüz kurulu değil") from None
        raise
    return actions, status


def _table(name: str) -> bool:
    return db.query_one("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)) is not None


def _site(site_id: str) -> dict:
    row = sitemap_store.get(site_id)
    if row is None:
        raise _err(404, "not_found", "Site bulunamadı")
    return row


def _stage(stage: str) -> str:
    if stage not in STAGE_PAGE:
        raise _err(404, "not_found", "Aşama bulunamadı")
    return stage


def _text(value: Any, limit: int, what: str, *, required: bool = True) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if required and not text:
        raise _err(400, "bad_request", f"{what} boş olamaz")
    if len(text) > limit:
        raise _err(400, "bad_request", f"{what} en çok {limit} karakter olabilir")
    return text


def _public_url(value: Any) -> str:
    url = _text(value, MAX_URL, "Adres")
    try:
        return netguard.check_url(url)
    except ValueError as exc:
        raise _err(400, "bad_url", f"Bu adres kullanılamaz ({exc})") from None


def _host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def skippable(stage: str) -> list[str]:
    page = STAGE_PAGE[stage]
    fields = {c.get("field") for c in sitemap_meta.EXPECT.get(page, {}).values() if c.get("field")}
    fields |= set(EXTRA_SKIPPABLE.get(page, ()))
    return sorted(f for f in fields if f not in NEVER_SKIP)


def _call(fn, *args, **kwargs):
    """Run a stage action; engine errors -> envelope (unknown site/stage 404, refused input 400, stage running 409)."""
    try:
        return fn(*args, **kwargs)
    except ApiError:
        raise
    except Exception as exc:
        if type(exc).__name__ == "StageBusy":     # control.stages.actions.StageBusy (imported lazily)
            raise _err(409, "stage_busy", str(exc)) from None
        if isinstance(exc, (sitemap_store.SiteNotFound, LookupError)):
            raise _err(404, "not_found", f"Bulunamadı ({exc})") from None
        if isinstance(exc, ValueError):
            raise _err(400, "bad_request", f"İstek reddedildi ({exc})") from None
        raise


def _stage_rows(site_id: Optional[str] = None) -> list[dict]:
    if not _table("site_stages"):
        return []
    if site_id is None:
        return [dict(r) for r in db.query("SELECT site_id, stage, state FROM site_stages")]
    return [dict(r) for r in db.query("SELECT site_id, stage, state FROM site_stages WHERE site_id=?", (site_id,))]


def _open_problems(site_id: Optional[str] = None) -> int:
    if not _table("problems"):
        return 0   # T13 adds the table
    closed = ("resolved", "confirmed", "closed_not_public", "exhausted")
    marks = ",".join("?" * len(closed))
    if site_id is None:
        return db.query_one(f"SELECT COUNT(*) FROM problems WHERE state NOT IN ({marks})", closed)[0]
    return db.query_one(f"SELECT COUNT(*) FROM problems WHERE site_id=? AND state NOT IN ({marks})",
                        (site_id, *closed))[0]


def _map_state(rows: list[dict]) -> dict:
    by_stage = {r["stage"]: r["state"] for r in rows}
    stuck = None
    for sid in STAGE_PAGE:
        state = by_stage.get(sid)
        if state in ("review", "needs_human", "engine_gap") and stuck is None:
            stuck = {"stage": sid, "state": state}
    warn = next((sid for sid in STAGE_PAGE if by_stage.get(sid) == "passed_warn"), None)
    running = next((sid for sid in STAGE_PAGE if by_stage.get(sid) == "running"), None)
    return {"passed": sum(1 for s in by_stage.values() if s in PASSED), "total": len(STAGE_PAGE), "stuck": stuck,
            "warn": warn, "running": running}


# ---------------------------------------------------------------------------------------------- summary

@router.get("/api/control/summary")
def summary() -> dict:
    running = db.query_one("SELECT COUNT(*) FROM jobs WHERE state='running'")[0]
    stages = _stage_rows()
    if _table("problems"):   # T14: one count per thing to do (a stage with a waiting repair problem is not counted twice)
        from ..control.problems import views as problem_views
        waiting = problem_views.waiting_for_you(stages)
    else:
        waiting = sum(1 for r in stages if r["state"] in ("needs_human", "review"))
    unread = db.query_one("SELECT COUNT(*) FROM admin_notes WHERE read_at IS NULL")[0]
    return {"running_jobs": running, "waiting_for_you": waiting, "open_problems": _open_problems(), "unread_notes": unread,
            "drain": jobs_store.draining()}


# ---------------------------------------------------------------------------------------------- sites

@router.get("/api/control/sites")
def list_sites() -> dict:
    rows = _stage_rows()
    out = []
    for site in sitemap_store.list_sites():
        sid = site["site_id"]
        titles = db.query_one("SELECT COUNT(*) FROM source_items WHERE site_id=?", (sid,))[0]
        last = db.query_one("SELECT MAX(finished_at) FROM jobs WHERE site_id=? AND state='done'"
                            " AND kind IN ('crawl', 'refresh')", (sid,))[0]
        out.append({**site, "map": _map_state([r for r in rows if r["site_id"] == sid]), "titles": titles,
                    "last_refresh": last, "open_problems": _open_problems(sid),
                    "crawl": crawl_status.progress(sid)})   # T11
    return {"sites": out}


def _note(value: Any) -> str:
    if value is not None and not isinstance(value, str):
        raise _err(400, "bad_request", "Site notu metin olmalı")
    try:
        return site_notes.clean(value)
    except ValueError:
        raise _err(400, "bad_request", f"Site notu en çok {site_notes.MAX_CHARS} karakter olabilir") from None


@router.post("/api/control/sites")
def create_site(body: dict = Body(...)) -> dict:
    url = _public_url(body.get("url"))
    name = _text(body.get("name"), MAX_NAME, "Ad", required=False) or None
    note = _note(body.get("notes"))
    try:
        site_id = sitemap_store.create_site(url, name)
    except sitemap_store.ValidationError as exc:
        raise _err(400, "bad_url", f"Bu adres kullanılamaz ({exc})") from None
    if note:   # before A1 starts: the first run already reads it
        site_notes.save(site_id, note, applied=True)
    started = False
    try:
        actions, _ = _stages()
        _call(actions.start, site_id)
        started = True
    except ApiError as exc:
        if exc.status_code != 503:
            raise
    return {"site_id": site_id, "started": started}


@router.post("/api/control/sites/import")
def import_site(body: dict = Body(...)) -> dict:
    text = body.get("yaml")
    if not isinstance(text, str) or not text.strip():
        raise _err(400, "bad_request", "yaml boş olamaz")
    if len(text.encode("utf-8")) > sitemap_store.MAX_YAML_BYTES:
        raise _err(400, "bad_request", "yaml çok büyük")
    try:
        site_id = sitemap_store.import_yaml(text, overwrite=bool(body.get("overwrite")))
    except sitemap_store.SiteExists:
        raise _err(409, "site_exists", "Bu site zaten var (üzerine yazmak için 'overwrite' gerekir)") from None
    except sitemap_store.ValidationError as exc:
        raise _err(400, "invalid_map", f"Harita geçersiz: {exc}") from None
    return {"site_id": site_id, "draft": True}


@router.get("/api/control/sites/{site_id}")
def get_site(site_id: str) -> dict:
    site = _site(site_id)
    return {"site": site, "map": _map_state(_stage_rows(site_id)), "jobs": jobs_store.active(site_id)}


@router.delete("/api/control/sites/{site_id}")
def delete_site(site_id: str, purge: bool = False) -> dict:
    _site(site_id)
    # every active job of the site (stage:<site>:<Ax>, crawl:<site>, ...) is cancelled first: a running stage job loses its
    # lease and can no longer write; then T08 drops the stage rows/messages (state.delete_site)
    cancelled = sum(1 for job in jobs_store.active(site_id) if jobs_store.cancel(job_id=job["id"]))
    if _table("site_stages"):
        from ..control.stages import state as stage_state
        stage_state.delete_site(site_id)
    crawl_status.state.delete(site_id)   # T11: frontier / origins / crawl state of the site
    if _table("routing_rules"):
        from ..control import routing
        routing.delete_site(site_id)      # T12: the site's own rules / drafts / list bookkeeping
    return {"deleted": sitemap_store.delete_site(site_id, purge=purge), "cancelled_jobs": cancelled}


@router.post("/api/control/sites/{site_id}/pause")
def pause_site(site_id: str) -> dict:
    """Only the status: the site's stage / crawl jobs see it when they start and wait (``Wait("site paused")``)."""
    _site(site_id)
    sitemap_store.set_status(site_id, "paused")
    return {"site": sitemap_store.get(site_id)}


@router.post("/api/control/sites/{site_id}/resume")
def resume_site(site_id: str) -> dict:
    site = _site(site_id)
    sitemap_store.set_status(site_id, "active" if site["active_version"] is not None else "draft")
    # jobs that found the site paused (stage / crawl handlers return Wait("site paused")) run again
    woken = sum(1 for job in jobs_store.active(site_id)
                if job["state"] == "waiting" and job.get("wait_reason") == PAUSED_REASON and jobs_store.wake(job["key"]))
    return {"site": sitemap_store.get(site_id), "woken_jobs": woken}


# ---------------------------------------------------------------------------------------------- site note

@router.get("/api/control/sites/{site_id}/notes")
def get_notes(site_id: str) -> dict:
    _site(site_id)
    actions, _ = _stages()
    return {**site_notes.info(site_id), "changed": actions.notes_changed(site_id), "max": site_notes.MAX_CHARS}


@router.put("/api/control/sites/{site_id}/notes")
def put_notes(site_id: str, body: dict = Body(...)) -> dict:
    """``{notes}``: the admin's description of the site (at most 4000 chars; "" clears it). Stages are not reset."""
    _site(site_id)
    note = _note(body.get("notes") if isinstance(body, dict) else None)
    actions, _ = _stages()
    return {"ok": True, **_call(actions.save_notes, site_id, note)}


@router.post("/api/control/sites/{site_id}/notes/rerun")
def rerun_notes(site_id: str) -> dict:
    """"Not değişti — aşamaları notla yeniden çalıştır": A1 again, the later stages follow in chain order."""
    _site(site_id)
    actions, _ = _stages()
    return {"ok": True, "result": _call(actions.rerun_with_notes, site_id)}


# ---------------------------------------------------------------------------------------------- stages

@router.get("/api/control/sites/{site_id}/stages")
def stages(site_id: str) -> dict:
    _site(site_id)
    _, status = _stages()
    data = _call(status.overview, site_id) or {}
    for st in data.get("stages") or []:
        if st.get("id") in STAGE_PAGE:
            st.setdefault("skippable", skippable(st["id"]))
    data["busy"] = any(j["kind"] == "stage" for j in jobs_store.active(site_id))   # T11: the long-lived crawl job is not "busy"
    return data


@router.post("/api/control/sites/{site_id}/stages/{stage}/hint")
def stage_hint(site_id: str, stage: str, body: dict = Body(...)) -> dict:
    _site(site_id), _stage(stage)
    text = _text(body.get("text"), MAX_HINT, "İpucu")
    actions, _ = _stages()
    return {"ok": True, "result": _call(actions.hint, site_id, stage, text)}


@router.post("/api/control/sites/{site_id}/stages/{stage}/sample")
def stage_sample(site_id: str, stage: str, body: dict = Body(...)) -> dict:
    site = _site(site_id)
    _stage(stage)
    url = _public_url(body.get("url"))
    if _host(url) != _host(site["base_url"] or ""):
        raise _err(400, "bad_url", "Örnek adres bu sitenin adresi olmalı")
    actions, _ = _stages()
    return {"ok": True, "result": _call(actions.sample, site_id, stage, url)}


@router.post("/api/control/sites/{site_id}/stages/{stage}/exclude")
def stage_exclude(site_id: str, stage: str, body: dict = Body(...)) -> dict:
    """T18: ``{url, undo?}``: the sample is left out of the stage's measurement (persistent); ``undo`` takes it back."""
    _site(site_id), _stage(stage)
    url = _text(body.get("url"), 2000, "Adres")
    actions, _ = _stages()
    return {"ok": True, "result": _call(actions.exclude, site_id, stage, url, undo=bool(body.get("undo")))}


def _samples_mod():
    _stages()
    return importlib.import_module("..control.stages.samples", __package__)


@router.get("/api/control/sites/{site_id}/stages/{stage}/samples")
def stage_samples(site_id: str, stage: str) -> dict:
    """T25: the stage's samples (address, source, last result), the deleted ones and the "only mine" switch."""
    _site(site_id), _stage(stage)
    return {"ok": True, "result": _call(_samples_mod().listing, site_id, stage)}


SAMPLE_OPS = ("add", "edit", "delete", "restore", "toggle", "run")


@router.post("/api/control/sites/{site_id}/stages/{stage}/samples")
def stage_samples_edit(site_id: str, stage: str, body: dict = Body(...)) -> dict:
    """T25: ``{op: add|edit|delete|restore|toggle|run, url?, new_url?, on?}``; only ``run`` starts the stage."""
    site = _site(site_id)
    _stage(stage)
    op = body.get("op")
    if op not in SAMPLE_OPS:
        raise _err(400, "bad_request", "op: " + "|".join(SAMPLE_OPS))
    mod = _samples_mod()

    def own(value):
        url = _public_url(value)
        if _host(url) != _host(site["base_url"] or ""):
            raise _err(400, "bad_url", "Örnek adres bu sitenin adresi olmalı")
        return url

    if op == "add":
        res = _call(mod.add, site_id, stage, own(body.get("url")))
    elif op == "edit":
        old = _text(body.get("url"), MAX_URL, "Adres")
        res = _call(mod.edit, site_id, stage, old, own(body.get("new_url")))
    elif op in ("delete", "restore"):
        res = _call(getattr(mod, op), site_id, stage, _text(body.get("url"), MAX_URL, "Adres"))
    elif op == "toggle":
        res = _call(mod.toggle, site_id, stage, bool(body.get("on")))
    else:
        res = _call(mod.run, site_id, stage)
    return {"ok": True, "result": res}


@router.post("/api/control/sites/{site_id}/stages/{stage}/skip")
def stage_skip(site_id: str, stage: str, body: Optional[dict] = Body(None)) -> dict:
    _site(site_id), _stage(stage)
    field = (body or {}).get("field")
    if field is not None:
        field = _text(field, 40, "Alan")
        if field not in skippable(stage):
            raise _err(400, "not_skippable", f"'{field}' alanı bu aşamada atlanamaz")
    actions, _ = _stages()
    return {"ok": True, "result": _call(actions.skip, site_id, stage, field=field)}


@router.post("/api/control/sites/{site_id}/stages/{stage}/rerun")
def stage_rerun(site_id: str, stage: str) -> dict:
    _site(site_id), _stage(stage)
    actions, _ = _stages()
    return {"ok": True, "result": _call(actions.rerun, site_id, stage)}


@router.post("/api/control/sites/{site_id}/stages/{stage}/review")
def stage_review(site_id: str, stage: str, body: dict = Body(...)) -> dict:
    """A1 review card (T17): ``{sections: [{id, target}], links: [{url, meaning}], added: [{name, url, meaning}]}``;
    A2 block card: ``{blocks: [{url, index, choice: series|movie|mixed|ignore}]}``."""
    _site(site_id), _stage(stage)
    if not isinstance(body, dict):
        raise _err(400, "bad_request", "gövde bir nesne olmalı")
    actions, _ = _stages()
    return {"ok": True, "result": _call(actions.review, site_id, stage, body)}


@router.post("/api/control/sites/{site_id}/stages/{stage}/gap")
def stage_gap(site_id: str, stage: str, body: dict = Body(...)) -> dict:
    _site(site_id), _stage(stage)
    title = _text(body.get("title"), MAX_TITLE, "Başlık")
    actions, _ = _stages()
    return {"ok": True, "result": _call(actions.mark_gap, site_id, stage, title)}


# ---------------------------------------------------------------------------------------------- versions

@router.get("/api/control/sites/{site_id}/versions")
def versions(site_id: str) -> dict:
    site = _site(site_id)
    return {"versions": sitemap_store.versions(site_id), "active_version": site["active_version"]}


@router.get("/api/control/sites/{site_id}/versions/diff")
def versions_diff(site_id: str, a: Optional[int] = None, b: Optional[int] = None) -> dict:
    _site(site_id)
    try:
        return {"a": a, "b": b, "changes": sitemap_store.diff(site_id, a, b)}
    except KeyError:
        raise _err(404, "not_found", "Sürüm bulunamadı") from None


@router.post("/api/control/sites/{site_id}/versions/{version}/rollback")
def versions_rollback(site_id: str, version: int) -> dict:
    _site(site_id)
    try:
        new = sitemap_store.rollback(site_id, version, created_by="admin")
    except sitemap_store.SiteNotFound:
        raise _err(404, "not_found", "Site bulunamadı") from None
    except KeyError:
        raise _err(404, "not_found", "Sürüm bulunamadı") from None
    except sitemap_store.ValidationError as exc:
        raise _err(400, "invalid_map", f"Bu sürüm artık geçerli değil: {exc}") from None
    return {"active_version": new}


@router.get("/api/control/sites/{site_id}/export")
def export(site_id: str, version: Optional[int] = None, download: int = 1) -> Response:
    _site(site_id)
    try:
        text = sitemap_store.export_yaml(site_id, version)
    except KeyError:
        raise _err(404, "not_found", "Sürüm bulunamadı") from None
    headers = {"Cache-Control": "no-cache"}
    if download:
        suffix = f"-v{version}" if version is not None else ""
        headers["Content-Disposition"] = f'attachment; filename="{site_id}{suffix}.yaml"'
    return Response(text, media_type="text/yaml; charset=utf-8", headers=headers)
