"""Admin "TMDB zenginleştirme" (Ayarlar sekmesi): backfill işi, durum, önizleme.

`POST /api/ops/tmdb/backfill`  ``{kind: movie|series, scope?: items|seasons, dry_run, limit?, force?, use_preview?}``
                               iş başlatır (409 zaten çalışıyor; 400 anahtar yok / tür seçili değil; 422 geçersiz gövde).
                               ``scope=seasons`` (yalnız ``kind=series``): sezon posterleri + bölüm başlık/özet/tarih/süre/görsel
`GET  /api/ops/tmdb/status`    anahtar var mı (değer asla), kapsam sayıları (``coverage.seasons`` = sezon/bölüm görseli),
                               çalışan iş + ilerleme, son önizleme özetleri (``preview``, ``season_preview``)
`GET  /api/ops/tmdb/preview`   son önizleme (öğe başına kaynak → aday, skor, karar); süzgeç + sayfalama;
                               ``?scope=seasons``: dizi başına bulunan sezon/bölüm sayısı + örnek görseller

İş, uygulama içinde arka planda koşar (`library/tmdb_admin.py`); ilerleme iş çubuğunda
(`/api/ops/active`) ``TMDB zenginleştirme (film|dizi) x/y`` olarak görünür, bitince `kind=tmdb`
olay kaydı düşer. Yalnızca otomatik (auto) eşleşmeler yazılır.
"""
from __future__ import annotations

from contextlib import closing
from typing import Any, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Query
from pydantic import BaseModel, Field

from .. import config, db, images, settings
from ..errors import ApiError
from ..library import seasons, tmdb, tmdb_admin
from ..scraper import state as sstate
from .ops_library import _art

router = APIRouter()


class BackfillBody(BaseModel):
    kind: Literal["movie", "series"]
    scope: Literal["items", "seasons"] = Field("items", description="seasons: TMDB'li dizilerin sezon posterleri + bölüm verisi/görselleri (yalnız kind=series)")
    dry_run: bool = Field(True, description="true: yalnızca önizleme (hiçbir şey yazılmaz)")
    limit: Optional[int] = Field(None, ge=1, le=1_000_000, description="en fazla bu kadar öğe sorgula")
    force: bool = Field(False, description="eşleşmiş / yakın zamanda denenmiş öğeleri de yeniden sorgula")
    use_preview: bool = Field(True, description="taze önizleme varsa TMDB'yi yeniden sorgulamadan onu uygula")


def _coverage() -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {k: {"total": 0, "matched": 0, "review": 0, "unmatched": 0, "tmdb_poster": 0,
                                          "no_poster": 0, "no_backdrop": 0} for k in settings.TMDB_KINDS}
    with closing(db.connect()) as conn:
        rows = conn.execute("SELECT COALESCE(type,'movie') AS type, tmdb_id, tmdb_enrich_status, "
                            "tmdb_poster_url, poster_url, tmdb_backdrop_url, backdrop_url FROM library_items").fetchall()
        season_cov = seasons.coverage(conn)
    for r in rows:
        c = out.get(r["type"])
        if c is None:
            continue
        c["total"] += 1
        if r["tmdb_id"] is not None or r["tmdb_enrich_status"] == "matched":
            c["matched"] += 1
        elif r["tmdb_enrich_status"] == "review":
            c["review"] += 1
        else:
            c["unmatched"] += 1
        if _art(r["tmdb_poster_url"]):
            c["tmdb_poster"] += 1
        if not (_art(r["tmdb_poster_url"]) or _art(r["poster_url"])):
            c["no_poster"] += 1
        if not (_art(r["tmdb_backdrop_url"]) or _art(r["backdrop_url"])):
            c["no_backdrop"] += 1
    out["seasons"] = season_cov  # sezon/bölüm görselleri (library/seasons.py): {series, seasons, season_posters, episodes, ...}
    return out


def _job() -> Optional[dict[str, Any]]:
    for a in sstate.activity_list():
        if a["site"] == tmdb_admin.JOB_SITE and a["kind"] == tmdb_admin.JOB_KIND:
            return {k: a.get(k) for k in ("label", "media_type", "scope", "dry_run", "from_preview", "done", "total",
                                          "phase", "elapsed", "trigger", "started_at")}
    return None


@router.post("/api/ops/tmdb/backfill")
def start_backfill(body: BackfillBody, background_tasks: BackgroundTasks) -> dict[str, Any]:
    try:
        job = tmdb_admin.begin(body.kind, body.dry_run, body.limit, body.force, body.use_preview, scope=body.scope)
    except tmdb_admin.StartError as exc:
        raise ApiError(exc.status, exc.code, exc.message)
    background_tasks.add_task(tmdb_admin.execute, job)
    return {"started": True, "kind": job["kind"], "scope": job["scope"], "dry_run": job["dry_run"],
            "from_preview": job["preview"] is not None, "label": job["label"]}


@router.get("/api/ops/tmdb/status")
def status() -> dict[str, Any]:
    cfg = settings.tmdb_settings()
    return {
        "configured": tmdb.enabled(),  # yalnızca var/yok; anahtar değeri asla dönmez
        "auto": cfg["auto"],
        "types": cfg["types"],
        "retry_days": config.TMDB_RETRY_DAYS,  # eşleşemeyenler bu kadar gün sonra tekrar denenir
        "coverage": _coverage(),
        "job": _job(),
        "last_job": (sstate.list_ops("tmdb", None, 1) or [None])[0],
        "preview": tmdb_admin.preview_summary(tmdb_admin.load_preview()),
        "season_preview": tmdb_admin.preview_summary(tmdb_admin.load_preview("seasons")),
        "prewarm": images.prewarm_running(),
    }


@router.get("/api/ops/tmdb/preview")
def preview(
    decision: str = Query("", pattern="^(|auto|review|unmatched|empty|error|deferred)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    scope: str = Query("items", pattern="^(items|seasons)$"),
) -> dict[str, Any]:
    data = tmdb_admin.load_preview(scope)
    if data is None:
        return {"available": False, "summary": None, "total": 0, "limit": limit, "offset": offset,
                "has_more": False, "items": []}
    rows = [i for i in data["items"] if not decision or i.get("decision") == decision]
    page = rows[offset:offset + limit]
    items = [{k: v for k, v in i.items() if k != "data"} for i in page]  # `data` = apply payload, not for the UI
    return {"available": True, "summary": tmdb_admin.preview_summary(data), "total": len(rows),
            "limit": limit, "offset": offset, "has_more": offset + limit < len(rows), "items": items}
