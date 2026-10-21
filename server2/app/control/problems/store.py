"""Problem record (README §5.7): the ONE place problem rows, their events and watchers are written.

State lives only in the ``problems`` table (no in-memory state). Every state change goes through :func:`transition`, which
checks the allowed-transition table below (an unlisted move raises :class:`InvalidTransition`) and writes one event row.

Fingerprints: ``episode:<cid>:<episode_id>``, ``title:<cid>`` (a film), ``title:<cid>:image`` (a broken poster),
``site:<site>:<page_type>:<class>``, ``host:<host>``.
"""
from __future__ import annotations

import builtins
import json
import time
from typing import Any, Optional

from ... import db

STATES = ("open", "diagnosing", "acting", "verifying", "resolved", "confirmed", "waiting_llm", "waiting_user",
          "waiting_capability", "closed_not_public", "exhausted", "closed")
#: the client still gets "searching" in these (while the deadline has not passed)
SEARCHING = ("open", "diagnosing", "acting", "verifying", "waiting_llm")
FOUND = ("resolved", "confirmed")
#: no more work: a new report may reopen them
FINISHED = ("resolved", "confirmed", "closed", "closed_not_public", "exhausted")
WORKING = ("open", "diagnosing", "acting", "verifying")
_WAITING_EXITS = {"open", "diagnosing", "acting", "verifying", "exhausted", "closed_not_public", "waiting_user", "waiting_llm",
                  "waiting_capability"}

# The allowed-transition table (README §5.7 / ARCHITECTURE-PROPOSAL §6.2). The only copy.
ALLOWED: dict[str, frozenset] = {
    "open": frozenset({"diagnosing", "acting", "waiting_user", "waiting_capability", "exhausted", "closed_not_public"}),
    "diagnosing": frozenset({"acting", "verifying", "open", "closed_not_public", "waiting_capability", "waiting_user",
                             "waiting_llm", "exhausted"}),
    "acting": frozenset({"verifying", "open", "waiting_user", "waiting_llm", "waiting_capability", "closed_not_public",
                         "exhausted"}),
    "verifying": frozenset({"resolved", "acting", "open", "exhausted", "waiting_user"}),
    "resolved": frozenset({"confirmed", "open", "closed"}),
    "confirmed": frozenset({"open"}),
    "waiting_llm": frozenset(_WAITING_EXITS - {"waiting_llm"}),
    "waiting_user": frozenset(_WAITING_EXITS - {"waiting_user"}),
    "waiting_capability": frozenset(_WAITING_EXITS - {"waiting_capability"}),
    "exhausted": frozenset({"open"}),
    "closed_not_public": frozenset({"open"}),
    "closed": frozenset({"open"}),
}

FIELDS = ("scope", "site_id", "page_type", "host", "canonical_id", "episode_id", "source_id", "class", "layer", "parent_id",
          "gap_id", "evidence", "attempts", "llm_runs", "next_at", "deadline_at", "resolution")
_STAMPS = {"resolved": "resolved_at", "confirmed": "confirmed_at", "closed": "closed_at", "closed_not_public": "closed_at",
           "exhausted": "closed_at"}


class InvalidTransition(ValueError):
    """A state change the table does not allow."""


def now() -> int:
    """The clock of the problem core (tests patch it)."""
    return int(time.time())


def _dump(value: Any) -> Optional[str]:
    return None if value is None else json.dumps(value, ensure_ascii=False, default=str)


def _load(text: Optional[str]) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def to_dict(row) -> Optional[dict]:
    if row is None:
        return None
    out = dict(row)
    out["evidence"] = _load(out.get("evidence")) or {}
    return out


def episode_fp(canonical_id: str, episode_id: str = "") -> str:
    return f"episode:{canonical_id}:{episode_id}" if episode_id else f"title:{canonical_id}"


def site_fp(site_id: str, page_type: str, klass: str) -> str:
    return f"site:{site_id}:{page_type or '-'}:{klass}"


def _values(fields: dict) -> dict:
    bad = [k for k in fields if k not in FIELDS]
    if bad:
        raise ValueError(f"unknown problem fields: {bad}")
    return {k: (_dump(v) if k == "evidence" else v) for k, v in fields.items()}


def open_or_get(fingerprint: str, **fields: Any) -> tuple[dict, bool]:
    """The problem of ``fingerprint``; created (state ``open``) with ``fields`` when there is none. ``(problem, created)``."""
    vals = _values({"scope": "episode", **fields})
    t = now()
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM problems WHERE fingerprint=?", (fingerprint,)).fetchone()
        if row is not None:
            return to_dict(row), False
        cols = ["fingerprint", "state", "opened_at", "updated_at", *vals]
        cur = conn.execute(f"INSERT INTO problems({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                           (fingerprint, "open", t, t, *vals.values()))
        pid = int(cur.lastrowid)
        conn.execute("INSERT INTO problem_events(problem_id,at,actor,kind,note,data) VALUES (?,?,?,?,?,?)",
                     (pid, t, "system", "opened", fingerprint, None))
        return to_dict(conn.execute("SELECT * FROM problems WHERE id=?", (pid,)).fetchone()), True


def by_fingerprint(fingerprint: str) -> Optional[dict]:
    return to_dict(db.query_one("SELECT * FROM problems WHERE fingerprint=?", (fingerprint,)))


def row(problem_id: int) -> Optional[dict]:
    return to_dict(db.query_one("SELECT * FROM problems WHERE id=?", (int(problem_id),)))


def get(problem_id: int) -> Optional[dict]:
    """The problem with its ``events`` (oldest first) and ``watchers``."""
    out = row(problem_id)
    if out is None:
        return None
    out["events"] = [{**dict(e), "data": _load(e["data"])}
                     for e in db.query("SELECT * FROM problem_events WHERE problem_id=? ORDER BY id", (out["id"],))]
    out["watchers"] = [dict(w) for w in db.query("SELECT * FROM problem_watchers WHERE problem_id=?", (out["id"],))]
    return out


def list(filters: Optional[dict] = None, limit: int = 200) -> builtins.list[dict]:  # noqa: A001 - contract name
    """Problems matching ``filters`` (``state`` str|list, ``scope``, ``site_id``, ``canonical_id``, ``class``, ``parent_id``),
    newest change first."""
    where, params = [], []
    for key, value in (filters or {}).items():
        if key not in ("state", "scope", "site_id", "canonical_id", "episode_id", "class", "parent_id", "page_type"):
            raise ValueError(f"unknown filter {key}")
        if isinstance(value, (builtins.list, tuple, set, frozenset)):
            where.append(f"{key} IN ({','.join('?' * len(value))})")
            params.extend(value)
        else:
            where.append(f"{key}=?")
            params.append(value)
    sql = "SELECT * FROM problems" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY updated_at DESC, id DESC LIMIT ?"
    return [to_dict(r) for r in db.query(sql, (*params, int(limit)))]


def event(problem_id: int, actor: str, kind: str, note: str = "", data: Any = None) -> int:
    cur = db.execute("INSERT INTO problem_events(problem_id,at,actor,kind,note,data) VALUES (?,?,?,?,?,?)",
                     (int(problem_id), now(), actor, kind, str(note or "")[:500], _dump(data)))
    return int(cur.lastrowid)


def events(problem_id: int, kind: Optional[str] = None, since: int = 0) -> builtins.list[dict]:
    sql = "SELECT * FROM problem_events WHERE problem_id=? AND at>=?" + (" AND kind=?" if kind else "") + " ORDER BY id"
    params = (int(problem_id), int(since), *((kind,) if kind else ()))
    return [{**dict(e), "data": _load(e["data"])} for e in db.query(sql, params)]


def update(problem_id: int, **fields: Any) -> None:
    """Change fields without a state change (diagnosis result, evidence, parent link)."""
    vals = _values(fields)
    if not vals:
        return
    db.execute(f"UPDATE problems SET {','.join(k + '=?' for k in vals)}, updated_at=? WHERE id=?",
               (*vals.values(), now(), int(problem_id)))


def transition(problem_id: int, to_state: str, *, actor: str, kind: str, note: str = "", data: Any = None,
               **fields: Any) -> dict:
    """Move the problem to ``to_state`` (allowed-table checked; :class:`InvalidTransition` otherwise), set ``fields`` and the
    state's time stamp, write one event. Returns the updated problem."""
    if to_state not in STATES:
        raise InvalidTransition(f"unknown state {to_state!r}")
    vals = _values(fields)
    t = now()
    with db.transaction() as conn:
        cur = conn.execute("SELECT state FROM problems WHERE id=?", (int(problem_id),)).fetchone()
        if cur is None:
            raise KeyError(f"problem {problem_id} not found")
        if to_state not in ALLOWED[cur["state"]]:
            raise InvalidTransition(f"problem {problem_id}: {cur['state']} -> {to_state} is not allowed")
        sets = {"state": to_state, "updated_at": t, **vals}
        stamp = _STAMPS.get(to_state)
        if stamp:
            sets[stamp] = t
        if to_state == "open":
            sets.setdefault("resolved_at", None)
            sets.setdefault("confirmed_at", None)
            sets.setdefault("closed_at", None)
            # a reopened problem owes every watcher a new answer
            conn.execute("UPDATE problem_watchers SET notified_at=NULL WHERE problem_id=?", (int(problem_id),))
        conn.execute(f"UPDATE problems SET {','.join(k + '=?' for k in sets)} WHERE id=?", (*sets.values(), int(problem_id)))
        conn.execute("INSERT INTO problem_events(problem_id,at,actor,kind,note,data) VALUES (?,?,?,?,?,?)",
                     (int(problem_id), t, actor, kind, f"{cur['state']} -> {to_state}" + (f": {note}" if note else ""),
                      _dump(data)))
        return to_dict(conn.execute("SELECT * FROM problems WHERE id=?", (int(problem_id),)).fetchone())


def watch(problem_id: int, profile_id: str, canonical_id: str, episode_id: str = "") -> bool:
    """Add a watcher (profile waiting for this content). True when it is new."""
    if not profile_id:
        return False
    cur = db.execute("INSERT OR IGNORE INTO problem_watchers(problem_id,profile_id,canonical_id,episode_id) VALUES (?,?,?,?)",
                     (int(problem_id), str(profile_id), canonical_id or "", episode_id or ""))
    return cur.rowcount > 0


def watchers(problem_id: int, pending_only: bool = False) -> builtins.list[dict]:
    sql = "SELECT * FROM problem_watchers WHERE problem_id=?" + (" AND notified_at IS NULL" if pending_only else "")
    return [dict(r) for r in db.query(sql, (int(problem_id),))]


def mark_notified(problem_id: int, profile_id: str, episode_id: str) -> bool:
    cur = db.execute("UPDATE problem_watchers SET notified_at=? WHERE problem_id=? AND profile_id=? AND episode_id=? "
                     "AND notified_at IS NULL", (now(), int(problem_id), profile_id, episode_id or ""))
    return cur.rowcount > 0
