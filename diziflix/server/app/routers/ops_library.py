"""Admin "Kütüphane": merkezi kütüphane listesi + video kaynağı sağlığı (K/T).

`GET /api/ops/library`              süzgeçli/sayfalı liste + toplam + facet sayıları
`GET /api/ops/library/{id}/videos`  bir öğenin video kaynakları (durum, host, son hata/deneme, `diagnosis` = son oynatma
                                    hatasının sunucu yoklaması + vekil durumu); diziyse
                                    `seasons[]` = sezon şeridi (TMDB sezon posteri var mı, bölüm/still sayısı)

K/T tanımı (öğe başına, `video_sources` tablosundan; oynatma sağlığı `library/videos.py`):
  T = devre dışı (`disabled`) ve engelli (`blocked`) olmayan tüm video kaynakları (film, bölüm, fragman)
  K = `status='broken'` olanlar (3 ardışık oynatma hatası = broken)
  ? = `status='suspect'` olanlar (1-2 hata): ayrı işaret, K'ya dahil DEĞİL
  engelli = `status='blocked'` (telif/erişim engeli yer tutucusu, `library/gate.py`): T/K/? dışında, ayrı sayaç (`videos.blocked`)
Kırık durumu: none (T=0) | ok (T>0, K=0) | partial (0<K<T) | dead (K=T>0).

Tek bir istek = tek geçici SQLite bağlantısı: öğe başına toplama (GROUP BY) bir kez TEMP
tabloya yazılır; sayfa, toplam ve facet sorguları o tablo üstünde çalışır.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Iterable, Optional
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Query

from .. import config, db
from ..cache import fold
from ..errors import ApiError
from ..library import seasons as tmdb_seasons, streamdiag

router = APIRouter()

BROKEN_STATES = ("ok", "partial", "dead", "suspect", "none")
TMDB_STATES = ("matched", "review", "unmatched")
KINDS = ("movie", "series")

# K/T'ye göre kırık durumu süzgeçleri (TEMP tablo kolonları: t, k, s).
_BROKEN_SQL = {
    "ok": "t > 0 AND k = 0",
    "partial": "k > 0 AND k < t",
    "dead": "t > 0 AND k >= t",
    "suspect": "s > 0",
    "none": "t = 0",
}
_ORDER = {
    "recent": "added_at DESC, updated_at DESC, id",
    "title": "fold(title), id",
    "broken": "k DESC, (CAST(k AS REAL) / MAX(t, 1)) DESC, s DESC, added_at DESC, id",
}
_STATUS_RANK = "CASE status WHEN 'broken' THEN 0 WHEN 'suspect' THEN 1 WHEN 'unknown' THEN 2 WHEN 'healthy' THEN 3 ELSE 4 END"
_KIND_RANK = "CASE kind WHEN 'movie' THEN 0 WHEN 'episode' THEN 1 ELSE 2 END"


# --- helpers ----------------------------------------------------------------

def _iso(ts: Optional[int]) -> Optional[str]:
    if not ts:
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _split_sites(values: Optional[Iterable[str]]) -> list[str]:
    """`site=a&site=b` ve `site=a,b` biçimlerinin ikisi de kabul edilir."""
    out: list[str] = []
    for value in values or []:
        for part in str(value).split(","):
            part = part.strip()
            if part and part not in out:
                out.append(part)
    return out


def _art(url: Optional[str]) -> Optional[str]:
    """Kaynak sitelerin "poster yok" yer tutucuları (svg) resim sayılmaz."""
    if not url:
        return None
    low = url.lower().split("?")[0]
    if "no-poster" in low or low.endswith(".svg"):
        return None
    return url


def _video_state(t: int, k: int) -> str:
    if t <= 0:
        return "none"
    if k >= t:
        return "dead"
    return "partial" if k > 0 else "ok"


def _like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class _Filters:
    def __init__(self, sites: list[str], broken: str, kind: str, tmdb: str, q: str) -> None:
        self.sites, self.broken, self.kind, self.tmdb = sites, broken, kind, tmdb
        self.tokens = [fold(t) for t in (q or "").split() if t]
        self.q = (q or "").strip()

    def where(self, skip: str = "") -> tuple[str, list[Any]]:
        """WHERE parçası; `skip` facet hesabında kendi boyutunu dışarıda bırakır."""
        clauses: list[str] = []
        params: list[Any] = []
        if self.sites and skip != "site":
            marks = ",".join("?" * len(self.sites))
            clauses.append("EXISTS (SELECT 1 FROM source_items si WHERE si.canonical_id = lib_b.id "
                           f"AND si.source IN ({marks}))")
            params += self.sites
        if self.broken in _BROKEN_SQL and skip != "broken":
            clauses.append("(" + _BROKEN_SQL[self.broken] + ")")
        if self.kind and skip != "kind":
            clauses.append("type = ?")
            params.append(self.kind)
        if self.tmdb and skip != "tmdb":
            clauses.append("tmdb_state = ?")
            params.append(self.tmdb)
        for token in self.tokens:  # her sözcük başlıkta ya da özgün başlıkta geçmeli
            clauses.append("fold(title || ' ' || COALESCE(original_title, '') || ' ' || COALESCE(year, '')) LIKE ? ESCAPE '\\'")
            params.append("%" + _like(token) + "%")
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def _materialize(conn: sqlite3.Connection) -> None:
    """Öğe başına K/T + TMDB durumu: tek JOIN/GROUP BY, sonuç TEMP tabloda."""
    conn.create_function("fold", 1, lambda v: fold(v) if isinstance(v, str) else "", deterministic=True)
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("DROP TABLE IF EXISTS temp.lib_b")
    conn.execute("""
        CREATE TEMP TABLE lib_b AS
        SELECT i.id AS id, COALESCE(i.type, 'movie') AS type, i.title AS title,
               i.original_title AS original_title, i.year AS year,
               COALESCE(i.added_at, 0) AS added_at, i.updated_at AS updated_at,
               i.tmdb_id AS tmdb_id, i.imdb_id AS imdb_id,
               i.tmdb_poster_url AS tmdb_poster_url, i.poster_url AS poster_url,
               i.tmdb_backdrop_url AS tmdb_backdrop_url, i.backdrop_url AS backdrop_url,
               CASE WHEN i.tmdb_id IS NOT NULL OR i.tmdb_enrich_status = 'matched' THEN 'matched'
                    WHEN i.tmdb_enrich_status = 'review' THEN 'review'
                    ELSE 'unmatched' END AS tmdb_state,
               COALESCE(v.t, 0) AS t, COALESCE(v.k, 0) AS k, COALESCE(v.s, 0) AS s,
               COALESCE(v.d, 0) AS d, COALESCE(v.b, 0) AS b, COALESCE(v.tr, 0) AS tr
        FROM library_items i
        LEFT JOIN (
            SELECT canonical_id AS cid,
                   SUM(status NOT IN ('disabled', 'blocked')) AS t,
                   SUM(status = 'broken') AS k,
                   SUM(status = 'suspect') AS s,
                   SUM(status = 'disabled') AS d,
                   SUM(status = 'blocked') AS b,
                   SUM(status NOT IN ('disabled', 'blocked') AND kind = 'trailer') AS tr
            FROM video_sources GROUP BY canonical_id
        ) v ON v.cid = i.id""")


def _img(item_id: str, kind: str, w: int) -> str:
    return f"/img/{quote(item_id, safe='')}/{kind}?w={w}"


def _item_json(r: sqlite3.Row, sources: list[str]) -> dict[str, Any]:
    t, k, s = int(r["t"]), int(r["k"]), int(r["s"])
    poster_tmdb, poster_src = _art(r["tmdb_poster_url"]), _art(r["poster_url"])
    back_tmdb, back_src = _art(r["tmdb_backdrop_url"]), _art(r["backdrop_url"])
    has_poster = poster_tmdb or poster_src
    has_backdrop = back_tmdb or back_src or has_poster  # /img card: afiş yoksa poster
    return {
        "id": r["id"],
        "title": r["title"],
        "original_title": r["original_title"],
        "year": r["year"],
        "type": r["type"],
        "sources": sources,
        "tmdb": r["tmdb_state"],
        "tmdb_id": r["tmdb_id"],
        "imdb_id": r["imdb_id"],
        "poster": _img(r["id"], "portrait", 200) if has_poster else None,
        "poster_origin": "tmdb" if poster_tmdb else "source" if poster_src else None,
        "backdrop": _img(r["id"], "card", 500) if has_backdrop else None,
        "backdrop_origin": "tmdb" if back_tmdb else "source" if (back_src or poster_src) else None,
        "added_at": _iso(r["added_at"]),
        "updated_at": _iso(r["updated_at"]),
        "videos": {"total": t, "broken": k, "suspect": s, "disabled": int(r["d"]), "blocked": int(r["b"]),
                   "trailers": int(r["tr"]), "state": _video_state(t, k), "label": f"{k}/{t}"},
    }


def _facets(conn: sqlite3.Connection, f: _Filters, selected_sites: list[str]) -> dict[str, Any]:
    """Her boyutun sayısı, kendi süzgeci hariç diğerleri uygulanarak (çoklu seçime uygun)."""
    w, p = f.where("site")
    counted = {r["source"]: r["n"] for r in conn.execute(
        "SELECT source, COUNT(DISTINCT canonical_id) AS n FROM source_items "
        f"WHERE canonical_id IN (SELECT id FROM lib_b{w}) GROUP BY source", p)}
    universe = [r["source"] for r in conn.execute(
        "SELECT source FROM source_items WHERE canonical_id IN (SELECT id FROM lib_b) "
        "GROUP BY source ORDER BY source")]
    names = sorted(set(universe) | set(selected_sites))
    sites = [{"site": n, "count": counted.get(n, 0)} for n in names]

    w, p = f.where("broken")
    row = conn.execute(
        "SELECT COUNT(*) AS n, "
        "COALESCE(SUM(t > 0 AND k = 0), 0) AS ok, COALESCE(SUM(k > 0 AND k < t), 0) AS partial, "
        "COALESCE(SUM(t > 0 AND k >= t), 0) AS dead, COALESCE(SUM(s > 0), 0) AS suspect, "
        f"COALESCE(SUM(t = 0), 0) AS none FROM lib_b{w}", p).fetchone()
    broken = {"all": row["n"], **{name: row[name] for name in BROKEN_STATES}}

    w, p = f.where("kind")
    kinds = {name: 0 for name in KINDS}
    for r in conn.execute(f"SELECT type, COUNT(*) AS n FROM lib_b{w} GROUP BY type", p):
        kinds[r["type"]] = r["n"]

    w, p = f.where("tmdb")
    tmdb = {name: 0 for name in TMDB_STATES}
    for r in conn.execute(f"SELECT tmdb_state, COUNT(*) AS n FROM lib_b{w} GROUP BY tmdb_state", p):
        tmdb[r["tmdb_state"]] = r["n"]
    return {"sites": sites, "broken": broken, "kind": kinds, "tmdb": tmdb}


def _empty(mode: str, limit: int, offset: int, facets: bool) -> dict[str, Any]:
    out: dict[str, Any] = {"source_mode": mode, "library_total": 0, "total": 0, "limit": limit,
                           "offset": offset, "has_more": False, "items": []}
    if facets:
        out["facets"] = {"sites": [], "broken": {"all": 0, **{n: 0 for n in BROKEN_STATES}},
                         "kind": {n: 0 for n in KINDS}, "tmdb": {n: 0 for n in TMDB_STATES}}
    return out


# --- endpoints --------------------------------------------------------------

@router.get("/api/ops/library")
def library(
    site: Optional[list[str]] = Query(None, description="tekrarlı ya da virgüllü; öğe bu sitelerden en az birinden gelmiş olmalı"),
    broken: str = Query("", pattern="^(|all|ok|partial|dead|suspect|none)$"),
    kind: str = Query("", pattern="^(|movie|series)$"),
    tmdb: str = Query("", pattern="^(|matched|review|unmatched)$"),
    q: str = Query("", max_length=200),
    sort: str = Query("recent", pattern="^(recent|title|broken)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    facets: bool = Query(True, description="0: facet sayılarını atla ('daha fazla' isteklerinde)"),
) -> dict[str, Any]:
    sites = _split_sites(site)
    f = _Filters(sites, broken, kind, tmdb, q)
    try:
        with closing(db.connect()) as conn:
            _materialize(conn)
            library_total = conn.execute("SELECT COUNT(*) FROM lib_b").fetchone()[0]
            where, params = f.where()
            total = conn.execute(f"SELECT COUNT(*) FROM lib_b{where}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM lib_b{where} ORDER BY {_ORDER[sort]} LIMIT ? OFFSET ?",
                [*params, limit, offset]).fetchall()
            by_id: dict[str, list[str]] = {}
            if rows:
                ids = [r["id"] for r in rows]
                marks = ",".join("?" * len(ids))
                for r in conn.execute(
                        "SELECT canonical_id, source FROM source_items "
                        f"WHERE canonical_id IN ({marks}) GROUP BY canonical_id, source ORDER BY source", ids):
                    by_id.setdefault(r["canonical_id"], []).append(r["source"])
            out: dict[str, Any] = {
                "source_mode": config.SOURCE,
                "library_total": library_total,
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(rows) < total,
                "items": [_item_json(r, by_id.get(r["id"], [])) for r in rows],
            }
            if facets:
                out["facets"] = _facets(conn, f, sites)
            return out
    except sqlite3.OperationalError:  # tablolar yok (henüz init edilmemiş DB): boş durum
        return _empty(config.SOURCE, limit, offset, facets)


def _provider_info(payload: Optional[str]) -> tuple[list[str], list[str]]:
    """Çözülmüş yükten (resolved_payload) sağlayıcı adları + akış host'ları (URL/token yok)."""
    providers: list[str] = []
    hosts: list[str] = []
    try:
        streams = (json.loads(payload) if payload else {}).get("streams") or []
    except (TypeError, ValueError, AttributeError):
        return providers, hosts
    for st in streams:
        if not isinstance(st, dict):
            continue
        name = st.get("provider")
        if name and name not in providers:
            providers.append(str(name))
        host = urlparse(str(st.get("url") or "")).hostname
        if host and host not in hosts:
            hosts.append(host)
    return providers, hosts


def _diagnosis(v: sqlite3.Row) -> dict[str, Any]:
    """Why the last playback failed (library/streamdiag.py) and what the stream proxy does for this source: ``code``
    (reachable | forbidden | not_media | gone | timeout | expired | ip_bound | hls_unsupported_browser | server_blocked |
    unreachable; None = never diagnosed), ``note`` (one Turkish sentence), ``at`` (ISO), ``http``, ``proxied`` (its streams are
    served through the proxy now), ``proxy_reason`` (ua | ip | recipe | learned | env), ``proxy_required`` (learned). Does not
    change the K/T counters."""
    out = streamdiag.public_diagnosis(v)
    out["at"] = _iso(out["at"])
    return out


def _video_json(v: sqlite3.Row, att: Optional[sqlite3.Row]) -> dict[str, Any]:
    providers, stream_hosts = _provider_info(v["resolved_payload"])
    return {
        "id": v["id"],
        "kind": v["kind"],
        "season": v["season"],
        "episode": v["episode"],
        "episode_title": v["episode_title"],
        "site": v["source"],
        "host": urlparse(v["locator"] or "").hostname,
        "locator": v["locator"],
        "resolver": v["resolver"],
        "media_type": v["media_type"],
        "label": v["label"],
        "providers": providers,
        "stream_hosts": stream_hosts,
        "status": v["status"],
        # trailer whose YouTube video was verified dead (library/trailer_check.py): a separate flag, NOT a playback
        # failure - it never changes status/failures and is not counted in K
        "trailer_dead": bool(v["trailer_dead"]) and v["kind"] == "trailer",
        "trailer_checked_at": _iso(v["trailer_checked_at"]) if v["kind"] == "trailer" else None,
        "failures": v["failures"],
        "last_error": v["last_error"],
        "last_checked_at": _iso(v["last_checked_at"]),
        "last_success_at": _iso(v["last_success_at"]),
        "resolved_at": _iso(v["resolved_at"]),
        "last_attempt_at": _iso(att["a"]) if att else None,
        "last_failure_at": _iso(att["f"]) if att else None,
        "diagnosis": _diagnosis(v),
    }


@router.get("/api/ops/library/{item_id}/videos")
def library_videos(item_id: str, limit: int = Query(500, ge=1, le=2000)) -> dict[str, Any]:
    """Bir öğenin video kaynakları: kırık > şüpheli > diğerleri, sonra film/bölüm/fragman sırası."""
    try:
        with closing(db.connect()) as conn:
            item = conn.execute("SELECT id, title, year, COALESCE(type, 'movie') AS type "
                                "FROM library_items WHERE id = ?", (item_id,)).fetchone()
            if not item:
                raise ApiError(404, "not_found", "kütüphane öğesi bulunamadı")
            agg = conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(status = 'broken'), 0) AS broken, "
                "COALESCE(SUM(status = 'suspect'), 0) AS suspect, COALESCE(SUM(status = 'healthy'), 0) AS healthy, "
                "COALESCE(SUM(status = 'unknown'), 0) AS unknown, COALESCE(SUM(status = 'disabled'), 0) AS disabled, "
                "COALESCE(SUM(status = 'blocked'), 0) AS blocked, "
                "COALESCE(SUM(status NOT IN ('disabled', 'blocked') AND kind = 'trailer'), 0) AS trailers, "
                "COALESCE(SUM(status NOT IN ('disabled', 'blocked') AND kind = 'trailer' AND trailer_dead = 1), 0) AS trailers_dead "
                "FROM video_sources WHERE canonical_id = ?", (item_id,)).fetchone()
            rows = conn.execute(
                f"SELECT * FROM video_sources WHERE canonical_id = ? "
                f"ORDER BY {_STATUS_RANK}, {_KIND_RANK}, season, episode, source, id LIMIT ?",
                (item_id, limit)).fetchall()
            attempts: dict[str, sqlite3.Row] = {}
            ids = [r["id"] for r in rows]
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                for a in conn.execute(
                        "SELECT source_id, MAX(created_at) AS a, MAX(failure_at) AS f FROM playback_attempts "
                        f"WHERE source_id IN ({','.join('?' * len(chunk))}) GROUP BY source_id", chunk):
                    attempts[a["source_id"]] = a
            strip = tmdb_seasons.season_strip(conn, item_id) if item["type"] == "series" else []
    except sqlite3.OperationalError:
        raise ApiError(404, "not_found", "kütüphane öğesi bulunamadı")
    total = int(agg["n"]) - int(agg["disabled"]) - int(agg["blocked"])
    return {
        "item": {"id": item["id"], "title": item["title"], "year": item["year"], "type": item["type"]},
        "summary": {"total": total, "broken": int(agg["broken"]), "suspect": int(agg["suspect"]),
                    "healthy": int(agg["healthy"]), "unknown": int(agg["unknown"]),
                    "disabled": int(agg["disabled"]), "blocked": int(agg["blocked"]), "trailers": int(agg["trailers"]),
                    "trailers_dead": int(agg["trailers_dead"]),
                    "state": _video_state(total, int(agg["broken"])),
                    "label": f"{int(agg['broken'])}/{total}"},
        "videos": [_video_json(r, attempts.get(r["id"])) for r in rows],
        "truncated": int(agg["n"]) > len(rows),
        "seasons": strip,
    }
