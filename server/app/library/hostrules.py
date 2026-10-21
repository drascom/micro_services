"""Stream HOST rules: what the server's probe VERIFIED about one stream host is applied to every stream of that host.

``streamdiag`` learns per SOURCE (``video_sources.proxy_required`` / ``proxy_headers``): episode 1 of a site learns "this host wants the page's Referer"
but episode 2 (another source row, same stream host) had to fail first. When the probe VERIFIES a fix (plain 403 -> 200 with a Referer; an IP-bound host
that only the server can fetch) ``streamdiag.record`` also writes a rule here, keyed by the stream host. Reading (``routers/streams.py`` ``public_streams``,
``streamdiag.diagnose`` / ``public_diagnosis``): a stream whose host has an active rule is served through the stream proxy (``proxy_reason: learned``) with the
rule's headers; priority: the stream's own ``request_headers`` > the source's own learning > the host rule.

Safety (a rule must never become a wrong generalisation): it expires after ``STREAM_HOST_RULE_TTL`` (30 days); every failed probe of a stream that was
served through it counts (``note_probe``): ``STREAM_HOST_RULE_FAILS`` (2) of them suspend it (streams go back to the plain path), a probe that works resets
the count; the admin can suspend / delete one (``routers/ops.py``). It is only ever written from a verified probe, never from a guess. Never raises.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional
from urllib.parse import urlparse

from .. import config, db, streamproxy

log = logging.getLogger("diziflix.hostrules")


def _now() -> int:
    return int(time.time())


def host_of(url: Any) -> str:
    try:
        return (urlparse(str(url or "")).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _public(row) -> dict:
    try:
        headers = streamproxy.clean_headers(json.loads(row["headers"] or "{}"))
    except (ValueError, TypeError):
        headers = {}
    return {"host": row["host"], "proxy_required": bool(row["proxy_required"]), "headers": headers, "reason": row["reason"],
            "learned_from_source": row["learned_from_source"], "learned_at": row["learned_at"], "last_ok_at": row["last_ok_at"],
            "fail_count": row["fail_count"], "expires_at": row["expires_at"], "suspended": bool(row["suspended"])}


def get(host: str) -> Optional[dict]:
    """The ACTIVE rule of ``host`` (not suspended, not expired) or None."""
    if not config.STREAM_HOST_RULES or not host:
        return None
    try:
        row = db.query_one("SELECT * FROM stream_host_rules WHERE host=? AND suspended=0 AND expires_at>?", (host.lower(), _now()))
        return _public(row) if row else None
    except Exception:
        return None


def find(host: str) -> Optional[dict]:
    """The rule of ``host`` in ANY state (active / suspended / expired; ``active`` says which) for the admin, or None."""
    try:
        row = db.query_one("SELECT * FROM stream_host_rules WHERE host=?", (host.lower(),)) if host else None
        return {**_public(row), "active": not row["suspended"] and row["expires_at"] > _now()} if row else None
    except Exception:
        return None


def for_hosts(hosts) -> dict:
    """``{host: rule}`` of the active rules among ``hosts`` (one query)."""
    hosts = sorted({h.lower() for h in hosts if h})
    if not config.STREAM_HOST_RULES or not hosts:
        return {}
    try:
        rows = db.query("SELECT * FROM stream_host_rules WHERE suspended=0 AND expires_at>? AND host IN (%s)" % ",".join("?" * len(hosts)),
                        (_now(), *hosts))
        return {r["host"]: _public(r) for r in rows}
    except Exception:
        return {}


def learn(host: str, headers: Optional[dict], reason: str, source_id: str = "") -> bool:
    """Write (or renew) the rule of ``host`` from a VERIFIED probe: ``headers`` = the Referer that worked (``{}`` for an IP-bound host that needs the
    proxy itself), ``reason`` referer | ip. A renewed rule starts clean (fail count 0, not suspended)."""
    if not config.STREAM_HOST_RULES or not host or reason not in ("referer", "ip"):
        return False
    try:
        now = _now()
        clean = streamproxy.clean_headers(headers)
        db.execute("""INSERT INTO stream_host_rules(host,proxy_required,headers,reason,learned_from_source,learned_at,last_ok_at,fail_count,expires_at,suspended)
            VALUES (?,?,?,?,?,?,?,0,?,0) ON CONFLICT(host) DO UPDATE SET proxy_required=1,headers=excluded.headers,reason=excluded.reason,
            learned_from_source=excluded.learned_from_source,learned_at=excluded.learned_at,last_ok_at=excluded.last_ok_at,fail_count=0,
            expires_at=excluded.expires_at,suspended=0""",
                   (host.lower(), 1, json.dumps(clean), reason, source_id or "", now, now, now + config.STREAM_HOST_RULE_TTL))
        return True
    except Exception:
        log.warning("host rule of %s not written", host, exc_info=True)
        return False


def note_probe(host: str, ok: bool) -> None:
    """The server's probe of a stream that was served through ``host``'s rule: a working one resets the failure count, a failing one counts and, at
    ``STREAM_HOST_RULE_FAILS``, suspends the rule (streams of the host go back to the normal path)."""
    try:
        if ok:
            db.execute("UPDATE stream_host_rules SET fail_count=0,last_ok_at=? WHERE host=?", (_now(), host.lower()))
        else:
            db.execute("UPDATE stream_host_rules SET fail_count=fail_count+1,suspended=CASE WHEN fail_count+1>=? THEN 1 ELSE suspended END WHERE host=? AND suspended=0",
                       (config.STREAM_HOST_RULE_FAILS, host.lower()))
    except Exception:
        pass


def suspend(host: str) -> bool:
    db.execute("UPDATE stream_host_rules SET suspended=1 WHERE host=?", (host.lower(),))
    return db.query_one("SELECT 1 FROM stream_host_rules WHERE host=?", (host.lower(),)) is not None


def delete(host: str) -> bool:
    existed = db.query_one("SELECT 1 FROM stream_host_rules WHERE host=?", (host.lower(),)) is not None
    db.execute("DELETE FROM stream_host_rules WHERE host=?", (host.lower(),))
    return existed


def listing() -> list[dict]:
    """Every rule (active, suspended, expired), newest first, for the admin."""
    rows = db.query("SELECT * FROM stream_host_rules ORDER BY learned_at DESC LIMIT 200")
    now = _now()
    return [{**_public(r), "active": not r["suspended"] and r["expires_at"] > now} for r in rows]
