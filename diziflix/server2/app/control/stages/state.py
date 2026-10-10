"""``site_stages`` / ``stage_messages`` rows (README §5.1). Every stage state lives here (restart-safe)."""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from ... import db

STATES = ("pending", "running", "passed", "passed_warn", "review", "needs_human", "engine_gap", "skipped")
PASSED = ("passed", "passed_warn")
DONE = PASSED + ("skipped",)
ROLES = ("user", "system", "llm")
MAX_MESSAGE = 2000
#: user message prefixes written by actions (not hints)
ACTION_TEXTS = ("Örnek adres", "Örnek çıkarıldı", "Örnek geri alındı", "Alan yok sayıldı", "Aşama atlandı", "Motor boşluğu",
                "İnceleme onaylandı", "Liste sayfası verildi", "Örnek düzenlendi",
                "Yalnız benim örneklerim")
_JSON = ("patch", "measurement", "evidence", "user_samples", "excluded_samples", "handoff")


def _now() -> int:
    return int(time.time())


def _dump(value: Any) -> Optional[str]:
    return None if value is None else json.dumps(value, ensure_ascii=False, default=str)


def _load(text: Optional[str]) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def ensure(site_id: str, stage: str) -> None:
    now = _now()
    db.execute("INSERT OR IGNORE INTO site_stages(site_id, stage, state, round, updated_at) VALUES (?, ?, 'pending', 0, ?)",
               (site_id, stage, now))


def get(site_id: str, stage: str) -> Optional[dict]:
    row = db.query_one("SELECT * FROM site_stages WHERE site_id=? AND stage=?", (site_id, stage))
    if row is None:
        return None
    out = dict(row)
    for key in _JSON:
        out[key] = _load(out.get(key))
    out["user_samples"] = out["user_samples"] or []
    out["excluded_samples"] = out.get("excluded_samples") or []
    out["own_samples_only"] = bool(out.get("own_samples_only"))
    return out


def all_for(site_id: str) -> dict[str, dict]:
    rows = db.query("SELECT stage FROM site_stages WHERE site_id=?", (site_id,))
    return {r["stage"]: get(site_id, r["stage"]) for r in rows}


def state_of(site_id: str, stage: str) -> Optional[str]:
    row = db.query_one("SELECT state FROM site_stages WHERE site_id=? AND stage=?", (site_id, stage))
    return row["state"] if row else None


def update(site_id: str, stage: str, **fields: Any) -> None:
    """Set columns; ``patch``/``measurement``/``evidence``/``user_samples`` are JSON-encoded."""
    if "state" in fields and fields["state"] not in STATES:
        raise ValueError(f"unknown stage state {fields['state']!r}")
    ensure(site_id, stage)
    cols, params = [], []
    for key, value in fields.items():
        if key in _JSON:
            value = _dump(value)
        cols.append(f"{key}=?")
        params.append(value)
    cols.append("updated_at=?")
    params.append(_now())
    db.execute(f"UPDATE site_stages SET {', '.join(cols)} WHERE site_id=? AND stage=?", (*params, site_id, stage))


def next_run(site_id: str, stage: str) -> int:
    """A new run of the stage: ``runs + 1`` (the LLM session id carries it)."""
    with db.transaction():
        ensure(site_id, stage)
        db.execute("UPDATE site_stages SET runs=runs+1, updated_at=? WHERE site_id=? AND stage=?", (_now(), site_id, stage))
        return int(db.query_one("SELECT runs FROM site_stages WHERE site_id=? AND stage=?", (site_id, stage))["runs"])


def add_message(site_id: str, stage: str, role: str, text: str) -> int:
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    cur = db.execute("INSERT INTO stage_messages(site_id, stage, role, text, at) VALUES (?, ?, ?, ?, ?)",
                     (site_id, stage, role, str(text or "")[:MAX_MESSAGE], _now()))
    return int(cur.lastrowid)


def messages(site_id: str, stage: Optional[str] = None, *, role: Optional[str] = None, limit: int = 50) -> list[dict]:
    sql, params = "SELECT id, stage, role, text, at FROM stage_messages WHERE site_id=?", [site_id]
    if stage is not None:
        sql += " AND stage=?"
        params.append(stage)
    if role is not None:
        sql += " AND role=?"
        params.append(role)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    return [dict(r) for r in reversed(db.query(sql, params))]


def delete_site(site_id: str) -> None:
    with db.transaction() as conn:
        conn.execute("DELETE FROM site_stages WHERE site_id=?", (site_id,))
        conn.execute("DELETE FROM stage_messages WHERE site_id=?", (site_id,))
