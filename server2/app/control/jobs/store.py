"""Persistent job queue: every state transition is one DB statement / transaction (README §5.3).

No queue state lives in process memory: callers (the worker, admin routes) always read the ``jobs`` table. Worker-side
transitions (``renew/complete/fail/retry/wait/checkpoint``) are guarded by ``state='running' AND lease_owner=?`` so a
job whose lease was lost (recovered, cancelled) can no longer be written by its old runner.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Iterable, Optional

from ... import db, settings

LEASE_S = 120
MAX_ATTEMPTS = 5
ACTIVE_STATES = ("queued", "running", "waiting")
FINAL_STATES = ("done", "failed")
DRAIN_KEY = "jobs:drain"


def _now(now: Optional[float] = None) -> int:
    return int(time.time() if now is None else now)


def _dump(value: Any) -> Optional[str]:
    return None if value is None else json.dumps(value, ensure_ascii=False)


def _load(text: Optional[str]) -> Any:
    if text is None:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def to_dict(row) -> Optional[dict]:
    if row is None:
        return None
    out = dict(row)
    out["payload"] = _load(out.get("payload"))
    out["result"] = _load(out.get("result"))
    return out


def get(job_id: int) -> Optional[dict]:
    return to_dict(db.query_one("SELECT * FROM jobs WHERE id = ?", (job_id,)))


def get_by_key(key: str) -> Optional[dict]:
    return to_dict(db.query_one("SELECT * FROM jobs WHERE key = ?", (key,)))


# --------------------------------------------------------------------------- producer side

def enqueue(kind: str, key: Optional[str] = None, *, site_id: Optional[str] = None, payload: Any = None,
            run_after: float = 0, priority: int = 0, now: Optional[float] = None) -> int:
    """Queue a job. Same ``key`` while queued/running/waiting: no-op, returns the existing id. A finished
    (done/failed) row with that key is reset for a new run (same id)."""
    key = key or f"{kind}:{uuid.uuid4().hex}"
    t = _now(now)
    with db.transaction():
        row = db.query_one("SELECT id, state FROM jobs WHERE key = ?", (key,))
        if row is not None:
            if row["state"] in ACTIVE_STATES:
                return int(row["id"])
            db.execute(
                """UPDATE jobs SET kind=?, site_id=?, state='queued', step=0, payload=?, result=NULL, attempts=0,
                   priority=?, run_after=?, lease_until=NULL, lease_owner=NULL, error=NULL, wait_reason=NULL,
                   updated_at=?, finished_at=NULL WHERE id=?""",
                (kind, site_id, _dump(payload), int(priority), int(run_after), t, row["id"]),
            )
            return int(row["id"])
        cur = db.execute(
            """INSERT INTO jobs(kind, key, site_id, state, step, payload, attempts, priority, run_after, created_at, updated_at)
               VALUES (?, ?, ?, 'queued', 0, ?, 0, ?, ?, ?, ?)""",
            (kind, key, site_id, _dump(payload), int(priority), int(run_after), t, t),
        )
        return int(cur.lastrowid)


def wake(key: str, now: Optional[float] = None) -> bool:
    """A waiting job goes back to the queue (runs as soon as a worker is free)."""
    cur = db.execute(
        "UPDATE jobs SET state='queued', run_after=0, wait_reason=NULL, updated_at=? WHERE key=? AND state='waiting'",
        (_now(now), key),
    )
    return cur.rowcount > 0


def cancel(key: Optional[str] = None, *, job_id: Optional[int] = None, now: Optional[float] = None) -> bool:
    """Active job -> failed('cancelled'). A running job's lease is dropped, so its runner can no longer write it."""
    where, arg = ("id=?", job_id) if job_id is not None else ("key=?", key)
    t = _now(now)
    cur = db.execute(
        f"""UPDATE jobs SET state='failed', error='cancelled', lease_until=NULL, lease_owner=NULL, updated_at=?,
            finished_at=? WHERE {where} AND state IN ('queued','running','waiting')""",
        (t, t, arg),
    )
    return cur.rowcount > 0


def update_payload(job_id: int, payload: Any, now: Optional[float] = None) -> bool:
    """Replace the payload of a job that has not started yet (queued / waiting). False once a worker leased it."""
    cur = db.execute("UPDATE jobs SET payload=?, updated_at=? WHERE id=? AND state IN ('queued','waiting')",
                     (_dump(payload), _now(now), job_id))
    return cur.rowcount > 0


def requeue(job_id: int, now: Optional[float] = None) -> bool:
    """Admin "retry": a failed/done/waiting job is queued now with a fresh attempt budget; step/payload kept
    (it resumes from its last checkpoint). A queued job is just made due now."""
    cur = db.execute(
        """UPDATE jobs SET state='queued', attempts=0, run_after=0, error=NULL, wait_reason=NULL, lease_until=NULL,
           lease_owner=NULL, finished_at=NULL, updated_at=? WHERE id=? AND state IN ('failed','done','waiting','queued')""",
        (_now(now), job_id),
    )
    return cur.rowcount > 0


# --------------------------------------------------------------------------- drain (persisted flag)

def drain(on: bool) -> None:
    settings.set(DRAIN_KEY, bool(on))


def draining() -> bool:
    return bool(settings.get(DRAIN_KEY, False))


def drain_status() -> dict:
    row = db.query_one("SELECT COUNT(*) AS n FROM jobs WHERE state='running'")
    return {"on": draining(), "running": int(row["n"])}


# --------------------------------------------------------------------------- worker side

def lease(owner: str, kinds: Iterable[str], now: Optional[float] = None, *, llm_kinds: Iterable[str] = (),
          lease_s: int = LEASE_S) -> Optional[dict]:
    """Pick and lease one due job in a single ``BEGIN IMMEDIATE`` transaction. Order: priority desc, run_after asc.
    ``llm_kinds``: at most one such job runs at a time in the whole system (hence also per site). Drain on: None."""
    kinds = list(kinds)
    llm = set(llm_kinds)
    if not kinds:
        return None
    t = _now(now)
    marks = ",".join("?" * len(kinds))
    with db.transaction():
        if draining():
            return None
        llm_busy = False
        if llm:
            lm = ",".join("?" * len(llm))
            llm_busy = db.query_one(
                f"SELECT 1 FROM jobs WHERE state='running' AND kind IN ({lm}) AND lease_until >= ? LIMIT 1",
                (*llm, t),
            ) is not None
        params: list[Any] = [*kinds, t]
        extra = ""
        if llm_busy:
            extra = f" AND kind NOT IN ({','.join('?' * len(llm))})"
            params.extend(llm)
        row = db.query_one(
            f"""SELECT id FROM jobs WHERE state='queued' AND kind IN ({marks}) AND run_after <= ?{extra}
                ORDER BY priority DESC, run_after ASC, id ASC LIMIT 1""",
            params,
        )
        if row is None:
            return None
        db.execute(
            """UPDATE jobs SET state='running', lease_owner=?, lease_until=?, attempts=attempts+1, updated_at=?
               WHERE id=?""",
            (owner, t + lease_s, t, row["id"]),
        )
        return get(int(row["id"]))


def _owned(sql_set: str, params: tuple, job_id: int, owner: str) -> bool:
    cur = db.execute(
        f"UPDATE jobs SET {sql_set} WHERE id=? AND state='running' AND lease_owner=?", (*params, job_id, owner)
    )
    return cur.rowcount > 0


def renew(owner: str, now: Optional[float] = None, lease_s: int = LEASE_S,
          job_ids: Optional[Iterable[int]] = None) -> int:
    """Extend the lease of the running jobs of ``owner`` (only ``job_ids`` when given). Returns how many."""
    t = _now(now)
    sql = "UPDATE jobs SET lease_until=?, updated_at=? WHERE state='running' AND lease_owner=?"
    params: list[Any] = [t + lease_s, t, owner]
    if job_ids is not None:
        ids = [int(i) for i in job_ids]
        if not ids:
            return 0
        sql += f" AND id IN ({','.join('?' * len(ids))})"
        params.extend(ids)
    return db.execute(sql, params).rowcount


def checkpoint(job_id: int, owner: str, step: int, payload: Any = None, now: Optional[float] = None) -> bool:
    if payload is None:
        return _owned("step=?, updated_at=?", (int(step), _now(now)), job_id, owner)
    return _owned("step=?, payload=?, updated_at=?", (int(step), _dump(payload), _now(now)), job_id, owner)


def set_result(job_id: int, owner: str, result: Any) -> bool:
    return _owned("result=?", (_dump(result),), job_id, owner)


def complete(job_id: int, owner: str, result: Any = None, now: Optional[float] = None) -> bool:
    t = _now(now)
    return _owned("state='done', result=?, error=NULL, lease_until=NULL, lease_owner=NULL, updated_at=?, finished_at=?",
                  (_dump(result), t, t), job_id, owner)


def fail(job_id: int, owner: str, error: str, now: Optional[float] = None) -> bool:
    t = _now(now)
    return _owned("state='failed', error=?, lease_until=NULL, lease_owner=NULL, updated_at=?, finished_at=?",
                  (str(error)[:4000], t, t), job_id, owner)


def retry(job_id: int, owner: str, after_s: float, error: str = "", now: Optional[float] = None,
          max_attempts: int = MAX_ATTEMPTS, refund: bool = False) -> str:
    """Back to the queue after ``after_s``; once ``attempts`` reached ``max_attempts`` the job fails instead.
    ``refund`` (a planned yield at the handler's deadline): this run's attempt is given back and never fails the job.
    Returns the new state ('queued' | 'failed') or '' if the lease was lost."""
    row = db.query_one("SELECT attempts FROM jobs WHERE id=? AND state='running' AND lease_owner=?", (job_id, owner))
    if row is None:
        return ""
    if refund:
        t = _now(now)
        ok = _owned("state='queued', run_after=?, error=?, attempts=MAX(attempts-1,0), lease_until=NULL, lease_owner=NULL, "
                    "updated_at=?", (t + int(after_s), str(error)[:4000], t), job_id, owner)
        return "queued" if ok else ""
    if int(row["attempts"]) >= max_attempts:
        return "failed" if fail(job_id, owner, f"max attempts ({max_attempts}): {error}", now) else ""
    t = _now(now)
    ok = _owned("state='queued', run_after=?, error=?, lease_until=NULL, lease_owner=NULL, updated_at=?",
                (t + int(after_s), str(error)[:4000], t), job_id, owner)
    return "queued" if ok else ""


def wait(job_id: int, owner: str, reason: str, now: Optional[float] = None) -> bool:
    return _owned("state='waiting', wait_reason=?, lease_until=NULL, lease_owner=NULL, updated_at=?",
                  (str(reason)[:1000], _now(now)), job_id, owner)


def release(owner: str, now: Optional[float] = None) -> int:
    """Shutdown: running jobs of ``owner`` go back to the queue at once (attempts and checkpoint kept)."""
    cur = db.execute(
        "UPDATE jobs SET state='queued', lease_until=NULL, lease_owner=NULL, updated_at=? WHERE state='running' AND lease_owner=?",
        (_now(now), owner),
    )
    return cur.rowcount


def recover(now: Optional[float] = None, max_attempts: int = MAX_ATTEMPTS) -> int:
    """Running jobs whose lease expired -> queued (attempts, step and payload kept); one that already used
    ``max_attempts`` leases (it keeps crashing the process) -> failed with error ``crashed``."""
    t = _now(now)
    with db.transaction():
        dead = db.execute(
            """UPDATE jobs SET state='failed', error='crashed', lease_until=NULL, lease_owner=NULL, updated_at=?,
               finished_at=? WHERE state='running' AND lease_until < ? AND attempts >= ?""",
            (t, t, t, int(max_attempts)),
        ).rowcount
        cur = db.execute(
            "UPDATE jobs SET state='queued', lease_until=NULL, lease_owner=NULL, updated_at=? WHERE state='running' AND lease_until < ?",
            (t, t),
        )
    return cur.rowcount + dead


# --------------------------------------------------------------------------- reads

def active(site_id: Optional[str] = None) -> list[dict]:
    sql = "SELECT * FROM jobs WHERE state IN ('queued','running','waiting')"
    params: tuple = ()
    if site_id is not None:
        sql += " AND site_id=?"
        params = (site_id,)
    return [to_dict(r) for r in db.query(sql + " ORDER BY priority DESC, run_after ASC, id ASC", params)]


def history(site_id: Optional[str] = None, limit: int = 50) -> list[dict]:
    sql = "SELECT * FROM jobs WHERE state IN ('done','failed')"
    params: list[Any] = []
    if site_id is not None:
        sql += " AND site_id=?"
        params.append(site_id)
    params.append(int(limit))
    return [to_dict(r) for r in db.query(sql + " ORDER BY finished_at DESC, id DESC LIMIT ?", params)]


def list_jobs(state: Optional[str] = None, site_id: Optional[str] = None, limit: int = 200) -> list[dict]:
    clauses, params = [], []
    if state:
        clauses.append("state=?")
        params.append(state)
    if site_id:
        clauses.append("site_id=?")
        params.append(site_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(int(limit))
    return [to_dict(r) for r in db.query(f"SELECT * FROM jobs{where} ORDER BY updated_at DESC, id DESC LIMIT ?", params)]


def gc(older_than_s: int = 14 * 86400, now: Optional[float] = None) -> int:
    cur = db.execute("DELETE FROM jobs WHERE state IN ('done','failed') AND finished_at < ?", (_now(now) - older_than_s,))
    return cur.rowcount
