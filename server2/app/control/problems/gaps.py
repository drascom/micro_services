"""Engine capability gaps (T14 item 2): what the general engine cannot do yet, as ONE ``capability_gaps`` row per ``key``
(``provider:<host>`` / ``resolver:<reason>`` / ``embed:<kind>``) whose ``sites`` list grows. Sources: a stage's ``engine_gap``
result, the admin's ``mark_gap`` and the diagnosis class ``capability_missing``. Problems tied to a gap wait in
``waiting_capability`` (their watchers hear ``source_unavailable``).

Closing: at startup the capability catalogue (resolver types + provider names + recipe names) is hashed; when the hash changed,
the problems of the open gaps go back to ``open`` and the stages that were ``engine_gap`` run again (one admin note). The admin
can mark a gap ``shipped`` (same re-run) or ``declined`` (its problems are ``exhausted``).
"""
from __future__ import annotations

import builtins
import hashlib
import json
import logging
import re
from typing import Optional

from ... import db
from .. import notes
from . import store

log = logging.getLogger("diziflix.problems")

STATUSES = ("open", "shipped", "declined")
_SLUG = re.compile(r"[^a-z0-9.]+")
_HOST = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$")
_checked = {"done": False}   # once per process (startup); the hash itself is on disk


def slug(text: str) -> str:
    return _SLUG.sub("-", str(text or "").lower()).strip("-")[:60] or "bilinmeyen"


def key_for(*, host: str = "", reason: str = "", embed: str = "") -> str:
    if host:
        return f"provider:{str(host).lower().removeprefix('www.')}"
    if embed:
        return f"embed:{slug(embed)}"
    return f"resolver:{slug(reason)}"


def _load(row) -> Optional[dict]:
    if row is None:
        return None
    out = dict(row)
    try:
        out["sites"] = json.loads(out.get("sites") or "[]")
    except ValueError:
        out["sites"] = []
    return out


def get(gap_id: int) -> Optional[dict]:
    return _load(db.query_one("SELECT * FROM capability_gaps WHERE id=?", (int(gap_id),)))


def by_key(key: str) -> Optional[dict]:
    return _load(db.query_one("SELECT * FROM capability_gaps WHERE key=?", (key,)))


def record(key: str, title: str, site_id: Optional[str] = None, detail: str = "") -> dict:
    """The gap of ``key`` (created ``open``; a ``shipped``/``declined`` one opens again), ``site_id`` added to its sites."""
    t = store.now()
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM capability_gaps WHERE key=?", (key,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO capability_gaps(key,title,detail,status,first_at,sites,catalog_hash) VALUES (?,?,?,?,?,?,?)",
                         (key, str(title or key)[:200], str(detail or "")[:1000], "open", t,
                          json.dumps([site_id] if site_id else []), catalog_hash()))
        else:
            sites = _load(row)["sites"]
            if site_id and site_id not in sites:
                sites.append(site_id)
            conn.execute("UPDATE capability_gaps SET sites=?, status='open', detail=COALESCE(NULLIF(?,''),detail) WHERE id=?",
                         (json.dumps(sites), str(detail or "")[:1000], row["id"]))
        return _load(conn.execute("SELECT * FROM capability_gaps WHERE key=?", (key,)).fetchone())


def list_all() -> builtins.list[dict]:
    """Every gap with ``waiting`` = its problems still in ``waiting_capability``."""
    rows = db.query("SELECT g.*, (SELECT COUNT(*) FROM problems p WHERE p.gap_id=g.id AND p.state='waiting_capability') AS waiting "
                    "FROM capability_gaps g ORDER BY CASE g.status WHEN 'open' THEN 0 ELSE 1 END, g.first_at DESC")
    return [_load(r) for r in rows]


# --------------------------------------------------------------------------- sources

def from_stage(site_id: str, stage_id: str, evidence: Optional[dict], text: str = "") -> dict:
    """A stage ended ``engine_gap``: A6's evidence names the host (``provider:<host>``); otherwise the engine's reason."""
    ev = evidence or {}
    first = next(iter(ev.get("gaps") or []), None)
    if isinstance(first, dict) and first.get("host"):
        key = key_for(host=first["host"])
        title = f"{first['host']}: desteklenmeyen video sağlayıcısı"
    else:
        reason = str(ev.get("gap") or text or stage_id)
        key = key_for(reason="browser" if "tarayıcı" in reason else reason)
        title = reason
    return record(key, title, site_id, detail=f"{stage_id}: {text or ev.get('gap') or ''}")


def from_mark_gap(site_id: str, stage_id: str, title: str) -> int:
    """``stages.actions.mark_gap`` hook: the admin names the gap. A title that starts with a host (``player.x.test`` or
    ``player.x.test: why``) -> ``provider:<host>``, the same key the engine's own gap of that host gets."""
    first = re.split(r"[\s:/,;()]+", str(title or "").strip().lower(), maxsplit=1)[0]
    key = key_for(host=first) if _HOST.match(first) else key_for(reason=title)
    return int(record(key, title, site_id, detail=f"{stage_id}: admin")["id"])


def from_diagnosis(problem: dict, verdict: dict) -> dict:
    ev = verdict.get("evidence") or {}
    key = ev.get("gap_key") or key_for(host=verdict.get("host") or "")
    kind = key.split(":", 1)[-1]
    return record(key, f"{kind}: gömülü oynatıcı desteklenmiyor", verdict.get("site_id") or problem.get("site_id"),
                  detail=f"sorun #{problem['id']}")


# --------------------------------------------------------------------------- closing

def catalog_hash() -> str:
    """Digest of what the engine can do: resolver types + provider names + recipe names."""
    from ...core.play import resolvers
    from ...core.play.providers import recipes, registry
    names: list[str] = sorted(f"resolver:{n}" for n in getattr(resolvers, "TYPES", {}) or {})
    try:
        names += sorted(f"provider:{p.name}" for p in registry.providers(extra=[registry.YOUTUBE]))
    except Exception:
        log.warning("provider list unavailable for the catalogue hash", exc_info=True)
    try:
        names += sorted(f"recipe:{(r.get('name') if isinstance(r, dict) else getattr(r, 'name', r))}" for r in recipes.load_all())
    except Exception:
        log.warning("recipe list unavailable for the catalogue hash", exc_info=True)
    return hashlib.sha256("\n".join(names).encode("utf-8")).hexdigest()[:16]


def _stored_hash() -> Optional[str]:
    row = db.query_one("SELECT data FROM problem_signals WHERE kind='catalog_hash' AND ref='-'")
    return row["data"] if row else None


def _store_hash(value: str) -> None:
    db.execute("INSERT INTO problem_signals(kind,ref,site_id,at,data) VALUES ('catalog_hash','-',NULL,?,?) "
               "ON CONFLICT(kind,ref) DO UPDATE SET data=excluded.data, at=excluded.at", (store.now(), value))


def retry_gap(gap: dict, why: str, *, admin: bool = False) -> dict:
    """Problems of ``gap`` -> ``open`` (+ job); its sites' ``engine_gap`` stages -> run again. ``{problems, stages}``."""
    from . import run
    from ..stages import engine, state as stage_state
    n_problems = n_stages = 0
    for p in store.list({"state": "waiting_capability"}, limit=1000):
        if p.get("gap_id") != gap["id"]:
            continue
        try:
            done = store.transition(p["id"], "open", actor="system", kind="capability_retry", note=why, next_at=0,
                                    deadline_at=store.now() + 900)
        except store.InvalidTransition:
            continue
        run.enqueue(done, admin=admin)
        n_problems += 1
    for site_id in gap.get("sites") or []:
        for sid, row in stage_state.all_for(site_id).items():
            if row.get("state") == "engine_gap":
                stage_state.update(site_id, sid, state="pending")
                try:
                    n_stages += int(bool(engine.enqueue(site_id, sid, trigger="admin" if admin else "system")["queued"]))
                except Exception:
                    log.warning("gap retry: stage %s/%s not queued", site_id, sid, exc_info=True)
    return {"problems": n_problems, "stages": n_stages}


def check_catalog(force: bool = False) -> dict:
    """Startup (recover hook, once per process): when the catalogue hash changed, every open gap is retried + ONE note."""
    if _checked["done"] and not force:
        return {"changed": False}
    _checked["done"] = True
    try:
        current = catalog_hash()
        before = _stored_hash()
    except Exception:   # tables missing in a partial test database
        log.warning("catalogue hash check skipped", exc_info=True)
        return {"changed": False}
    _store_hash(current)
    if before is None or before == current:
        return {"changed": False, "hash": current}
    total = {"problems": 0, "stages": 0}
    for gap in list_all():
        if gap["status"] != "open":
            continue
        got = retry_gap(gap, "yetenek kataloğu değişti")
        total["problems"] += got["problems"]
        total["stages"] += got["stages"]
        db.execute("UPDATE capability_gaps SET catalog_hash=? WHERE id=?", (current, gap["id"]))
    n = total["problems"] + total["stages"]
    if n:
        notes.add("info", f"Yetenek kataloğu değişti, {n} bekleyen iş yeniden deneniyor", ref_kind="gaps", ref_id="catalog")
    return {"changed": True, "hash": current, **total}


def set_status(gap_id: int, status: str) -> dict:
    """Admin: ``shipped`` -> its problems / stages run again; ``declined`` -> its problems are ``exhausted`` (watchers hear it)."""
    from . import notify
    if status not in ("shipped", "declined", "open"):
        raise ValueError("durum shipped, declined ya da open olmalı")
    gap = get(gap_id)
    if gap is None:
        raise KeyError(gap_id)
    db.execute("UPDATE capability_gaps SET status=?, shipped_at=? WHERE id=?",
               (status, store.now() if status == "shipped" else None, int(gap_id)))
    out = {"id": int(gap_id), "status": status}
    if status == "shipped":
        out.update(retry_gap(gap, "motor boşluğu kapandı (admin)", admin=True))
    elif status == "declined":
        n = 0
        for p in store.list({"state": "waiting_capability"}, limit=1000):
            if p.get("gap_id") == gap["id"]:
                store.transition(p["id"], "exhausted", actor="admin", kind="gap_declined", note=gap["key"])
                notify.send(p["id"])
                n += 1
        out["closed"] = n
    return out
