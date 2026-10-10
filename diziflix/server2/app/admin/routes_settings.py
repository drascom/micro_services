"""Admin Settings screen endpoints (T15): runtime settings (README §6 + ``settings.DEFAULTS``), LLM health/usage, queue summary.

- ``GET/PUT /api/control/settings``: only the keys of ``settings.DEFAULTS``; an unknown key, a wrong type or an out-of-range
  value is a 400 and nothing is written (the whole body is validated first).
- ``GET/POST /api/control/llm/health``: cached / forced ``pi auth check``.
- ``GET /api/control/llm/kinds``: job kinds that hold the LLM slot (``deploy.sh`` uses it while draining).
Secrets (``TMDB_ACCESS_KEY``, ``STREAM_PROXY_SECRET``) are never returned; the response says only whether they are set.
"""
from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, Body

from .. import config, db, settings
from ..control import wiring
from ..control.jobs import store as jobs_store, worker as jobs_worker
from ..core.llm import budget, health
from ..errors import ApiError

router = APIRouter()

_HOST_RE = re.compile(r"^(?=.{1,253}$)(\*\.)?[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$")
TMDB_TYPES = ("movie", "series")

# key -> (kind, lower, upper, Turkish label). Every key of settings.DEFAULTS must be here (enforced by a test).
SCHEMA: dict[str, tuple[str, Any, Any, str]] = {
    "backfill_days": ("int", 1, 60, "Tam aktarım süresi (gün)"),
    "refresh_hour": ("int", 0, 23, "Günlük tazeleme saati"),
    "crawl_gate_daily_per_site": ("int", 10, 2000, "Site başına günlük kapı (telif) kontrol sayfası"),
    "source_recheck_days": ("int", 1, 90, "Bölüm kaynak adaylarını yeniden okuma aralığı (gün)"),
    "llm_daily_calls_per_site": ("int", 0, 1000, "Site başına günlük arka plan LLM çağrısı"),
    "repair_autoapply": ("bool", None, None, "Doğrulanan onarım kendiliğinden uygulansın"),
    "problem_deadline_s": ("int", 60, 86400, "İstemciye söz süresi (sn)"),
    "confirm_seconds": ("int", 3, 120, "Kullanıcı onayı: hatasız oynatma (sn)"),
    "tmdb_auto": ("bool", None, None, "TMDB otomatik zenginleştirme"),
    "tmdb_types": ("choices", TMDB_TYPES, None, "TMDB türleri"),
    "image_hosts_extra": ("hosts", 0, 50, "Ek afiş host'ları"),
    "stream_proxy_hosts": ("hosts", 0, 100, "Akışı her zaman vekilden geçen host'lar"),
    "stream_proxy_force": ("bool", None, None, "Her akışı vekilden geçir"),
}


def _bad(key: str, message: str) -> ApiError:
    return ApiError(400, "invalid_setting", f"{key}: {message}")


def validate(key: str, value: Any) -> Any:
    """Normalised value or ``ApiError(400)``."""
    if key not in SCHEMA:
        raise ApiError(400, "unknown_setting", f"bilinmeyen ayar: {key}")
    kind, lo, hi, _ = SCHEMA[key]
    if kind == "bool":
        if not isinstance(value, bool):
            raise _bad(key, "true/false olmalı")
        return value
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise _bad(key, "tam sayı olmalı")
        if not lo <= value <= hi:
            raise _bad(key, f"{lo}..{hi} aralığında olmalı")
        return value
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise _bad(key, "metin listesi olmalı")
    items = list(dict.fromkeys(v.strip().lower() for v in value if v.strip()))
    if kind == "choices":
        wrong = [v for v in items if v not in lo]
        if wrong:
            raise _bad(key, f"geçersiz değer {wrong[0]!r} (izinli: {', '.join(lo)})")
        return items
    if len(items) > hi:
        raise _bad(key, f"en çok {hi} host")
    wrong = [v for v in items if not _HOST_RE.match(v)]
    if wrong:
        raise _bad(key, f"geçersiz host {wrong[0]!r}")
    return items


def _jobs_summary() -> dict:
    counts = {r["state"]: int(r["n"]) for r in db.query("SELECT state, COUNT(*) AS n FROM jobs GROUP BY state")}
    return {"queued": counts.get("queued", 0), "running": counts.get("running", 0), "waiting": counts.get("waiting", 0),
            "failed": counts.get("failed", 0), "drain": jobs_store.drain_status()}


def _secrets() -> dict:
    """Only "set / not set" for each secret: the value never leaves the process."""
    return {"tmdb_key": bool((config.TMDB_ACCESS_KEY or "").strip()),
            "stream_proxy_secret": bool((config.STREAM_PROXY_SECRET or "").strip())}


def _view() -> dict:
    stored = settings.all()
    values = {k: stored.get(k, settings.DEFAULTS[k]) for k in SCHEMA}
    schema = {k: {"type": kind, "min": lo if kind in ("int", "hosts") else None,
                  "max": hi, "choices": list(lo) if kind == "choices" else None, "label": label,
                  "default": settings.DEFAULTS[k]}
              for k, (kind, lo, hi, label) in SCHEMA.items()}
    return {"settings": values, "schema": schema, "secrets": _secrets(), "tmdb": wiring.tmdb_stats(),
            "llm": {"model": health.model(), "usage": budget.usage(days=7)}, "jobs": _jobs_summary()}


@router.get("/api/control/settings")
def get_settings() -> dict:
    return _view()


@router.put("/api/control/settings")
def put_settings(body: Any = Body(...)) -> dict:
    if not isinstance(body, dict) or not body:
        raise ApiError(400, "invalid_body", "gövde boş olmayan bir nesne olmalı: {anahtar: değer}")
    clean = {key: validate(key, value) for key, value in body.items()}   # all-or-nothing
    with db.transaction() as conn:
        for key, value in clean.items():
            conn.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (key, json.dumps(value, ensure_ascii=False)))
    return {**_view(), "saved": sorted(clean)}


@router.post("/api/control/settings/tmdb-backfill")
def tmdb_backfill_now() -> dict:
    """"Eksikleri şimdi doldur": queue a TMDB job for every title still missing TMDB data (idempotent, staggered)."""
    if not (config.TMDB_ACCESS_KEY or "").strip():
        raise ApiError(409, "tmdb_off", "TMDB anahtarı tanımsız (.env TMDB_ACCESS_KEY)")
    if wiring.tmdb_key_invalid():
        raise ApiError(409, "tmdb_key_invalid", "TMDB anahtarı geçersiz; .env'i düzeltip sunucuyu yeniden başlatın")
    if not settings.get("tmdb_auto"):
        raise ApiError(409, "tmdb_auto_off", "TMDB otomatik zenginleştirme kapalı")
    return {**wiring.tmdb_backfill(), "tmdb": wiring.tmdb_stats()}


@router.get("/api/control/llm/health")
def llm_health() -> dict:
    return health.check()


@router.post("/api/control/llm/health")
def llm_health_recheck() -> dict:
    return health.check(force=True)


@router.get("/api/control/llm/kinds")
def llm_kinds() -> dict:
    return {"kinds": sorted(jobs_worker.llm_kinds())}
