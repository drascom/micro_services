"""Daily LLM budget per site. System-started (``background``) calls are limited by ``settings.llm_daily_calls_per_site``;
admin-started calls (``background=0``: every call of a run the admin started, its chain included) are never blocked and do
NOT use up the budget; they are still logged in ``llm_calls`` and shown apart (``usage``: ``admin`` / ``background``)."""
from __future__ import annotations

import time
from typing import Any, Optional

from ... import db, settings

# Rows that never reached the model do not use up the budget.
_NOT_SPENT = ("prompt_too_large", "session_lost", "pi_missing", "fake_empty")


def _day_start(now: Optional[float] = None) -> int:
    lt = time.localtime(now if now is not None else time.time())
    return int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))


def spent_today(site_id: Optional[str], *, background_only: bool = True) -> int:
    sql = ("SELECT COUNT(*) AS n FROM llm_calls WHERE site_id IS ? AND at >= ?"
           f" AND (error IS NULL OR error NOT IN ({','.join('?' * len(_NOT_SPENT))}))")
    params: list[Any] = [site_id, _day_start(), *_NOT_SPENT]
    if background_only:
        sql += " AND background = 1"
    row = db.query_one(sql, params)
    return int(row["n"]) if row else 0


def allowed(site_id: Optional[str], *, background: bool) -> tuple[bool, str]:
    if not background:
        return True, "admin"
    try:
        limit = int(settings.get("llm_daily_calls_per_site"))
    except (TypeError, ValueError):
        limit = int(settings.DEFAULTS["llm_daily_calls_per_site"])
    used = spent_today(site_id)
    if used >= limit:
        return False, f"daily_limit ({used}/{limit})"
    return True, f"ok ({used}/{limit})"


def stage_calls(site_id: str, stage: str, since: int) -> dict[str, int]:
    """LLM calls of a stage since ``since`` (its run's start): ``{calls, admin, system}`` (stage card line)."""
    row = db.query_one("SELECT COUNT(*) AS n, SUM(background) AS bg FROM llm_calls WHERE site_id=? AND stage=? AND at >= ?",
                       (site_id, stage, int(since or 0)))
    n = int(row["n"] or 0) if row else 0
    bg = int(row["bg"] or 0) if row else 0
    return {"calls": n, "admin": n - bg, "system": bg}


def usage(site_id: Optional[str] = None, days: int = 7) -> dict[str, Any]:
    """Summary for the admin Settings screen (T15): totals and per site over the last ``days`` days."""
    since = _day_start() - (max(int(days), 1) - 1) * 86400
    where, params = "at >= ?", [since]
    if site_id is not None:
        where += " AND site_id = ?"
        params.append(site_id)
    cols = ("COUNT(*) AS calls, SUM(ok) AS ok, SUM(background) AS background, SUM(COALESCE(ms,0)) AS ms,"
            " SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens, SUM(cached_tokens) AS cached_tokens,"
            " SUM(prompt_chars) AS prompt_chars, SUM(reply_chars) AS reply_chars")
    total = db.query_one(f"SELECT {cols} FROM llm_calls WHERE {where}", params)
    rows = db.query(f"SELECT site_id, {cols} FROM llm_calls WHERE {where} GROUP BY site_id ORDER BY calls DESC", params)

    def clean(row) -> dict[str, Any]:
        out = {k: (row[k] or 0) if k not in ("input_tokens", "output_tokens", "cached_tokens") else row[k]
               for k in row.keys()}
        out["admin"] = int(out.get("calls") or 0) - int(out.get("background") or 0)
        return out

    return {"days": int(days), "since": since, "total": clean(total), "sites": [clean(r) for r in rows],
            "limit_per_site": settings.get("llm_daily_calls_per_site")}
