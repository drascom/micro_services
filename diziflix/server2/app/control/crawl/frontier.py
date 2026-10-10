"""``crawl_frontier`` (README §5.1): every page the crawl knows of a site, one row per address.

States ``new, done, failed, gone``. Priority: K0 = 100, K2 = 50, K1 = 10 (``add`` never lowers it, never drops a row).
A failure backs off 1 h / 6 h / 24 h; the 4th one makes the row ``gone`` (the daily refresh gives a ``gone`` row one more
chance after 7 days). ``mark`` = why a done/new row waits: ``blocked`` (not public), ``gate_wait`` (gate budget used up),
``wait_rules`` (the map has no rule for it yet). Origins of a page (``crawl_origins``) accumulate: a title reached from
two sections keeps both.

T30 provenance: ``origin_role`` says WHERE the link was found (``ROLES``); the page type follows it, never the address
shape. ``url_pattern`` only judges links of unknown provenance; a mismatch is a warning (``warn``), not a new type."""
from __future__ import annotations

from typing import Iterable, Optional

from ... import db
from . import state

K0, K2, K1 = 100, 50, 10
STATES = ("new", "done", "failed", "gone")
BACKOFF_S = (3600, 6 * 3600, 24 * 3600, 7 * 24 * 3600)
MAX_FAILS = 4
#: where a frontier link was found (T30): it decides the page type
ROLES = ("title_row", "episode_row", "season_link", "title_link", "next_page", "seed", "unknown")
#: provenance role -> the page type a link of that role is
ROLE_TYPE = {"title_row": "title", "episode_row": "sources", "season_link": "season", "title_link": "title",
             "next_page": "listing"}
TYPES = ("home", "listing", "title", "season", "episode", "sources")   # sources: an episode page read for its candidates (T28)


def _now(now: Optional[float] = None) -> int:
    return int(state.now() if now is None else now)


def add(site_id: str, url: str, page_type: str, *, priority: int, parent_url: Optional[str] = None,
        origin: Optional[tuple] = None, position: int = 0, now: Optional[float] = None, role: str = "") -> bool:
    """Discover ``url``. A known row keeps its state (only its priority may rise); the origin is recorded either way.
    True when the row is new."""
    t = _now(now)
    kind, value = (tuple(origin) + (None, None))[:2] if origin else (None, None)
    with db.transaction() as conn:
        row = conn.execute("SELECT priority, origin_role FROM crawl_frontier WHERE site_id=? AND url=?",
                           (site_id, url)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO crawl_frontier(site_id, url, page_type, state, priority, parent_url, origin_kind, origin_value,"
                " position, discovered_at, next_at, fail_count, origin_role) VALUES (?,?,?,'new',?,?,?,?,?,?,?,0,?)",
                (site_id, url, page_type, int(priority), parent_url, kind, value, int(position or 0), t, t, role or ""))
        elif role and not row["origin_role"]:
            conn.execute("UPDATE crawl_frontier SET origin_role=? WHERE site_id=? AND url=?", (role, site_id, url))
        elif int(priority) > int(row["priority"]):
            conn.execute("UPDATE crawl_frontier SET priority=? WHERE site_id=? AND url=?", (int(priority), site_id, url))
        if kind and value not in (None, ""):
            conn.execute("INSERT INTO crawl_origins(site_id, url, kind, value, position) VALUES (?,?,?,?,?) "
                         "ON CONFLICT(site_id, url, kind, value) DO UPDATE SET position=excluded.position",
                         (site_id, url, str(kind), str(value), int(position or 0)))
    return row is None


def set_key(site_id: str, url: str, key: str) -> None:
    """The title key the crawl gave this title page (a fallback key included): its season pages use the same one."""
    db.execute("UPDATE crawl_frontier SET item_key=? WHERE site_id=? AND url=?", (key, site_id, url))


WARN_EXAMPLES = 3


def warn(site_id: str, kind: str, url: str = "") -> None:
    """Count a T30 address warning in the crawl state (``url_warn: {kind: {count, examples}}``): ``url_mismatch`` (a link
    from the right place whose address fits no url pattern), ``fallback_key``, ``numbering_text`` / ``numbering_position``."""
    def fn(data: dict) -> None:
        bucket = data.setdefault("url_warn", {}).setdefault(kind, {"count": 0, "examples": []})
        bucket["count"] = int(bucket.get("count") or 0) + 1
        if url and url not in bucket["examples"] and len(bucket["examples"]) < WARN_EXAMPLES:
            bucket["examples"].append(url)
    state.update(site_id, fn)


def url_warnings(site_id: str) -> dict:
    return dict(state.get(site_id).get("url_warn") or {})


def get(site_id: str, url: str) -> Optional[dict]:
    row = db.query_one("SELECT * FROM crawl_frontier WHERE site_id=? AND url=?", (site_id, url))
    return dict(row) if row else None


def origins(site_id: str, url: str) -> list[tuple]:
    return [(r["kind"], r["value"], r["position"]) for r in
            db.query("SELECT kind, value, position FROM crawl_origins WHERE site_id=? AND url=? ORDER BY kind, value",
                     (site_id, url))]


def copy_origins(site_id: str, src: str, dst: str) -> None:
    """The origins of ``src`` (a home episode card) also belong to ``dst`` (its title page)."""
    db.execute("INSERT OR IGNORE INTO crawl_origins(site_id, url, kind, value, position) "
               "SELECT site_id, ?, kind, value, position FROM crawl_origins WHERE site_id=? AND url=?", (dst, site_id, src))


def take(site_id: str, n: int, now: Optional[float] = None, *, skip_types: Iterable[str] = (),
         quota: Optional[int] = None) -> list[dict]:
    """Up to ``n`` due rows (``new``/``failed`` with ``next_at <= now``), priority first. K1 title rows (priority below K2)
    only up to ``quota`` (None = no limit); rows of ``skip_types`` (stopped page types) wait."""
    t = _now(now)
    skip = [s for s in skip_types]
    extra = f" AND page_type NOT IN ({','.join('?' * len(skip))})" if skip else ""
    rows = db.query(f"SELECT * FROM crawl_frontier WHERE site_id=? AND state IN ('new','failed') AND next_at<=?{extra} "
                    "ORDER BY priority DESC, next_at ASC, discovered_at ASC, position ASC LIMIT ?",
                    (site_id, t, *skip, int(n) * 4 + 20))
    out, k1 = [], 0
    for row in rows:
        if is_quota(row):
            if quota is not None and k1 >= quota:
                continue
            k1 += 1
        out.append(dict(row))
        if len(out) >= n:
            break
    return out


def is_quota(row) -> bool:
    """A K1 (full import) title page: counted against the daily quota."""
    return row["page_type"] == "title" and int(row["priority"]) < K2


def done(site_id: str, url: str, *, mark: str = "", now: Optional[float] = None) -> None:
    t = _now(now)
    db.execute("UPDATE crawl_frontier SET state='done', crawled_at=?, fail_count=0, mark=?, error=NULL WHERE site_id=? AND url=?",
               (t, mark or "", site_id, url))


def failed(site_id: str, url: str, error: str = "", *, now: Optional[float] = None) -> str:
    """One more failure: ``next_at`` climbs the back-off ladder; the ``MAX_FAILS``-th failure -> ``gone``. Returns the state."""
    t = _now(now)
    with db.transaction() as conn:
        row = conn.execute("SELECT fail_count FROM crawl_frontier WHERE site_id=? AND url=?", (site_id, url)).fetchone()
        if row is None:
            return ""
        fails = int(row["fail_count"] or 0) + 1
        new_state = "gone" if fails >= MAX_FAILS else "failed"
        conn.execute("UPDATE crawl_frontier SET state=?, fail_count=?, next_at=?, crawled_at=?, error=? WHERE site_id=? AND url=?",
                     (new_state, fails, t + BACKOFF_S[min(fails, len(BACKOFF_S)) - 1], t, (error or "")[:300], site_id, url))
    return new_state


def reschedule(site_id: str, url: str, next_at: float, *, priority: Optional[int] = None, mark: Optional[str] = None) -> None:
    """Back to ``new`` at ``next_at`` (a refresh, a wait); the failure count is kept."""
    sets, args = ["state='new'", "next_at=?"], [int(next_at)]
    if priority is not None:
        sets.append("priority=MAX(priority, ?)")
        args.append(int(priority))
    if mark is not None:
        sets.append("mark=?")
        args.append(mark)
    db.execute(f"UPDATE crawl_frontier SET {', '.join(sets)} WHERE site_id=? AND url=?", (*args, site_id, url))


def stats(site_id: str) -> dict:
    """``{discovered, done, failed, gone, blocked, waiting, by_type: {type: {discovered, new, done, failed, gone}}}``."""
    out = {"discovered": 0, "new": 0, "done": 0, "failed": 0, "gone": 0, "blocked": 0, "gate_wait": 0, "wait_rules": 0,
           "by_type": {}}
    for row in db.query("SELECT page_type, state, mark, COUNT(*) AS n FROM crawl_frontier WHERE site_id=? "
                        "GROUP BY page_type, state, mark", (site_id,)):
        n = int(row["n"])
        bucket = out["by_type"].setdefault(row["page_type"], {"discovered": 0, "new": 0, "done": 0, "failed": 0, "gone": 0,
                                                              "blocked": 0})
        bucket["discovered"] += n
        bucket[row["state"]] = bucket.get(row["state"], 0) + n
        out["discovered"] += n
        out[row["state"]] = out.get(row["state"], 0) + n
        if row["mark"] in ("blocked", "gate_wait", "wait_rules"):
            out[row["mark"]] += n
            if row["mark"] == "blocked":
                bucket["blocked"] += n
    return out
