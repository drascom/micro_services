"""Playback issue ledger: every playback problem a client reports or the server diagnoses, kept per video source, shown in the admin
Olay defteri (``kind: playback_issue``) and the evidence of the playback heal.

Why it exists: ``videos.feedback`` only moved the source's health columns (suspect / broken) and never for a device / browser-HLS
failure; the playheal window is fed by RESOLUTIONS only, and its ``stream_blocked`` signal is memory-only, counts nothing but a
server-probe refusal on one stream host. A user who could not play an episode left no trace anywhere. Now:

* ``record_failure`` (``videos.feedback``, failure reports other than ``aborted`` / ``offline`` / ``autoplay``): upserts the source's row of table
  ``playback_issues`` (site, series / episode, locator, the client's code, the last server diagnosis, stream host / type / provider, how many
  reports); ``clear`` (a success report) deletes it: the source plays.
* ``note_diag`` (``streamdiag.run``): the fresh server-side verdict joins the row (``diag_code``, note, http) and may change its class.
* The class (``issue_class``) = the diagnosis code when it names a problem (``forbidden`` / ``not_media`` / ``server_blocked`` / ``gone`` /
  ``unreachable`` / ``ip_bound`` / ``timeout`` / ``expired``), ``hls_unsupported_browser`` for a browser that cannot play HLS natively (visible, never a
  fault of the source, never a heal trigger), ``proxy_learned`` when the probe learned the proxy / a Referer fixes it (self-corrected), else the
  client's own code (``playback_failed`` / ``timeout`` / ``network`` / ``unsupported`` / ``decode``).
* ``events`` = the admin feed: one event per (site, class, stream host), newest first.
* ``evidence`` (``playheal``): at least ``PLAYHEAL_ISSUE_MIN_SOURCES`` DIFFERENT sources of one site with the same HEAL class within
  ``PLAYHEAL_ISSUE_TTL`` seconds, sources of a started repair excluded for ``PLAYHEAL_ISSUE_COOLDOWN`` (``mark_triggered``) = a repair run
  (evidence kind ``playback`` + ``issue``, layer provider, same gates / ``heal_autoapply`` as any playback heal); ``check`` asks the trigger and notes
  why it did not start (heal disabled, cooldown, busy ...) in ``playheal.summary(site)["last_skip"]``.

Everything here is best effort and never raises into a request. Failure reports never change the health columns (``videos._feedback`` does).
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from typing import Any, Optional
from urllib.parse import urlparse

from .. import config, db

log = logging.getLogger("diziflix.playissues")

#: classes that may be the recipe's / the site's fault: they count for the playback heal
HEAL_CLASSES = ("forbidden", "not_media", "server_blocked", "gone", "unreachable", "ip_bound", "playback_failed", "timeout", "network")
#: heal classes whose repair is verified with the server's own reachability probe (``heal_agent._follow(probe=True)``)
PROBE_CLASSES = ("forbidden", "not_media", "server_blocked")
#: shown in the admin, never a heal signal
VISIBLE_ONLY = ("hls_unsupported_browser", "proxy_learned", "expired", "unsupported", "decode", "reachable")
SKIP_CODES = ("aborted", "offline", "autoplay")      # the client's own, unrelated to the stream: not recorded at all
DIAG_PROBLEMS = ("forbidden", "not_media", "server_blocked", "gone", "unreachable", "ip_bound", "timeout", "expired")
KEEP_SECONDS = 7 * 86400
FEED_ROWS = 400
FEED_GROUPS = 60
SAMPLES = 5
HEAL_EXAMPLES = 8
OK_EXAMPLES = 3
_CLIENT_NOTES = {
    "playback_failed": "Oynatıcı akışı oynatamadı.", "timeout": "Oynatma zaman aşımına uğradı.", "network": "Oynatıcı ağ hatası bildirdi.",
    "unsupported": "Cihaz bu biçimi desteklemiyor.", "decode": "Cihaz videoyu çözemedi.",
}
LABELS = {   # the Turkish names the admin shows (also used for the heal record's reason)
    "forbidden": "akış erişilemiyor (403)", "not_media": "akış video yerine hata sayfası veriyor", "server_blocked": "akışa sunucu da erişemiyor",
    "gone": "video kaynakta yok", "unreachable": "kaynağa ulaşılamıyor", "ip_bound": "adres IP'ye bağlı", "timeout": "oynatma zaman aşımı",
    "expired": "adresin süresi dolmuş", "hls_unsupported_browser": "tarayıcı HLS'i oynatamıyor", "proxy_learned": "vekil öğrenildi (kendiliğinden düzelir)",
    "playback_failed": "oynatıcı hatası", "network": "ağ hatası", "unsupported": "cihaz desteklemiyor", "decode": "cihaz çözemiyor",
}
_lock = threading.Lock()


def _now() -> int:
    return int(time.time())


def _iso(ts: Any) -> Optional[str]:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ts))) if ts else None


def _s(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _host(url: Any) -> str:
    try:
        return (urlparse(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def label(code: str) -> str:
    return LABELS.get(code, code or "oynatma sorunu")


# --- the class ---------------------------------------------------------------------------------------------------------
def issue_class(client_code: str, diag_code: str = "", browser_hls: bool = False, learned: bool = False) -> str:
    """The issue class of one failure: see the module text. ``learned`` = the server's probe learned the proxy / a Referer helps."""
    if learned:
        return "proxy_learned"
    if diag_code in DIAG_PROBLEMS:
        return diag_code
    if browser_hls or diag_code == "hls_unsupported_browser":
        return "hls_unsupported_browser"
    return client_code or "playback_failed"


def _client_note(code: str, detail: str = "") -> str:
    text = _CLIENT_NOTES.get(code or "playback_failed", "Oynatma hatası bildirildi.")
    return f"{text} (istemci: {_s(detail, 100)})" if detail else text


# --- writing -----------------------------------------------------------------------------------------------------------
def _source(token: str):
    return db.query_one("SELECT s.* FROM playback_attempts a JOIN video_sources s ON s.id=a.source_id WHERE a.token=?", (token,))


def _stream_facts(job: Optional[dict]) -> dict:
    first = next((s for s in (job or {}).get("streams") or [] if isinstance(s, dict) and s.get("url")), None)
    if not first:
        return {}
    from .. import streamproxy
    return {"host": _host(first.get("url")), "stream_type": _s(first.get("type"), 10), "provider": _s(first.get("provider"), 60),
            "stream_group": streamproxy.group_of(first["url"]), "stream_url": str(first["url"])[:1000]}


def record_failure(token: str, code: str, engine: str = "", detail: str = "", job: Optional[dict] = None) -> str:
    """A client reported a playback failure of the attempt ``token``: upsert the source's issue row. Returns the issue class, ``""`` when
    nothing was recorded (a device-side code, a trailer, an unknown attempt). Never raises."""
    try:
        if code in SKIP_CODES:
            return ""
        source = _source(token)
        if source is None or source["kind"] == "trailer":
            return ""
        from . import streamdiag
        diag = streamdiag.read_diag(source["last_diag"]) or {}
        now = _now()
        diag_code = _s(diag.get("code"), 40) if diag and now - int(diag.get("at") or 0) < config.PLAYHEAL_ISSUE_TTL else ""
        facts = _stream_facts(job)
        browser_hls = bool((job or {}).get("browser_hls"))
        client = _s(code, 30) or "playback_failed"
        with _lock:
            old = db.query_one("SELECT * FROM playback_issues WHERE source_id=?", (source["id"],))
            cls = issue_class(client, diag_code, browser_hls)
            note = _s(diag.get("note"), 300) if diag_code and diag_code != "reachable" and cls == diag_code else _client_note(client, detail)
            values = {"site": source["source"], "canonical_id": source["canonical_id"], "episode_id": source["episode_id"] or "", "kind": source["kind"],
                      "locator": _s(source["locator"], 500), "client_code": client, "diag_code": diag_code, "issue_class": cls, "note": note,
                      "host": facts.get("host") or (old["host"] if old else ""), "provider": facts.get("provider") or (old["provider"] if old else ""),
                      "stream_type": facts.get("stream_type") or (old["stream_type"] if old else source["media_type"] or ""),
                      "stream_group": facts.get("stream_group") or (old["stream_group"] if old else ""),
                      "stream_url": facts.get("stream_url") or (old["stream_url"] if old else ""),
                      "http": diag.get("http") if diag_code else None, "engine": _s(engine, 30), "last_at": now}
            if old is None:
                values.update(source_id=source["id"], reports=1, first_at=now)
                cols = ",".join(values)
                db.execute(f"INSERT INTO playback_issues({cols}) VALUES ({','.join('?' * len(values))})", tuple(values.values()))
            else:
                sets = ",".join(f"{k}=?" for k in values)
                db.execute(f"UPDATE playback_issues SET {sets},reports=reports+1 WHERE source_id=?", (*values.values(), source["id"]))
        _prune(now)
        check(source["source"])
        return cls
    except Exception:
        log.warning("playback issue of attempt could not be recorded", exc_info=True)
        return ""


def note_diag(job: dict, verdict: dict) -> None:
    """The fresh server-side verdict of ``streamdiag.diagnose`` joins the source's row (only an existing row: a diagnosis without a
    client report is not an issue). Never raises."""
    try:
        sid, code = job.get("source_id"), _s(verdict.get("code"), 40)
        if not sid or not code:
            return
        stream = verdict.get("stream") if isinstance(verdict.get("stream"), dict) else {}
        with _lock:
            old = db.query_one("SELECT * FROM playback_issues WHERE source_id=?", (sid,))
            if old is None:
                return
            cls = issue_class(old["client_code"], code, bool(job.get("browser_hls")) and code in ("reachable", "hls_unsupported_browser"),
                              learned=bool(verdict.get("learn")))
            from . import streamdiag
            note = _s(streamdiag._NOTES.get(code, "").format(http=verdict.get("http") or "", why=""), 300) if code != "reachable" else old["note"]
            if verdict.get("learn"):
                note = "Kaynak site düz isteği reddediyor, başlıklarla veriyor; sunucu vekille oynatılacak."
            db.execute("UPDATE playback_issues SET diag_code=?,issue_class=?,note=?,http=?,host=?,stream_type=? WHERE source_id=?",
                       (code, cls, note or old["note"], verdict.get("http"), stream.get("host") or old["host"],
                        stream.get("type") or old["stream_type"], sid))
        check(old["site"])
    except Exception:
        log.warning("playback issue diagnosis could not be noted", exc_info=True)


def open_issue(source_id: str):
    """The source's open issue row (inside the TTL) or None. Never raises."""
    try:
        row = db.query_one("SELECT * FROM playback_issues WHERE source_id=? AND last_at>=?", (source_id, _now() - config.PLAYHEAL_ISSUE_TTL))
        return row
    except Exception:
        return None


def recently_triggered(site: str, source_ids: list) -> bool:
    """A playback heal was started for one of these sources of ``site`` within ``PLAYHEAL_ISSUE_COOLDOWN`` (the finder's heal step does
    not open a second run for it)."""
    try:
        ids = [i for i in source_ids if i]
        if not ids:
            return False
        row = db.query_one("SELECT 1 FROM playback_issues WHERE site=? AND triggered_at>=? AND source_id IN (%s)" % ",".join("?" * len(ids)),
                           (site, _now() - config.PLAYHEAL_ISSUE_COOLDOWN, *ids))
        return row is not None
    except Exception:
        return False


def failing_entry(row) -> dict:
    """One ``failing`` example of heal evidence built from an issue row (the shape ``evidence`` uses)."""
    cls = row["issue_class"]
    return {"source_id": row["source_id"], "kind": row["kind"], "episode_id": row["episode_id"], "locator": row["locator"],
            "error": f"{label(cls)} ({row['reports']} rapor)", "stage": "stream", "host": "",
            "stream": {"host": row["host"], "type": row["stream_type"], "code": cls, "http": row["http"], "ct": "", "body": _s(row["note"], 80),
                       "sent": {}, "proxied": False}, "candidates": []}


def forget_streams(site: str) -> None:
    """A repair was applied for ``site``: what a client could not play before says nothing about the changed recipe, so the finder may offer
    the same file again (it forgets the recorded stream group; the issue rows stay as the record). Never raises."""
    try:
        db.execute("UPDATE playback_issues SET stream_group='' WHERE site=?", (site,))
    except Exception:
        pass


def clear(source_id: str) -> None:
    """The source plays: its issue row goes. Never raises."""
    try:
        db.execute("DELETE FROM playback_issues WHERE source_id=?", (source_id,))
    except Exception:
        pass


def clear_for_token(token: str) -> None:
    try:
        source = _source(token)
        if source is not None:
            clear(source["id"])
    except Exception:
        pass


_last_prune = [0]


def _prune(now: int) -> None:
    if now - _last_prune[0] < 600:
        return
    _last_prune[0] = now
    try:
        db.execute("DELETE FROM playback_issues WHERE last_at<?", (now - KEEP_SECONDS,))
    except Exception:
        pass


def reset() -> None:
    db.execute("DELETE FROM playback_issues")
    _last_prune[0] = 0


def mark_triggered(site: str, *, code: Optional[str] = None, host: Optional[str] = None) -> None:
    """A repair started for these sources (``issue_class`` ``code`` and / or stream ``host`` of ``site``): they do not start another for
    ``PLAYHEAL_ISSUE_COOLDOWN`` seconds. Never raises."""
    try:
        where, args = ["site=?"], [site]
        if code:
            where.append("issue_class=?")
            args.append(code)
        if host:
            where.append("host=?")
            args.append(host)
        db.execute(f"UPDATE playback_issues SET triggered_at=? WHERE {' AND '.join(where)}", (_now(), *args))
    except Exception:
        pass


# --- the trigger ---------------------------------------------------------------------------------------------------------
def check(site: str) -> str:
    """Ask the playback heal whether the ledger of ``site`` is a signal now; a trigger that cannot start says why in
    ``playheal.summary(site)["last_skip"]``. Cheap, never raises. Returns what ``playheal.maybe_trigger`` returned (or ``"error"``)."""
    try:
        from ..scraper import playheal
        if live_groups(site) == []:
            return "no_signal"
        result = playheal.maybe_trigger(site)
        if result in ("heal_disabled", "cooldown", "busy", "disabled", "llm_unhealthy"):
            playheal.note_trigger_result(site, result)
        return result
    except Exception:
        return "error"


def _rows(site: str, *, force: bool = False) -> list:
    now = _now()
    rows = db.query("SELECT * FROM playback_issues WHERE site=? AND last_at>=? AND issue_class IN (%s) ORDER BY last_at" % ",".join("?" * len(HEAL_CLASSES)),
                    (site, now - config.PLAYHEAL_ISSUE_TTL, *HEAL_CLASSES))
    if force:
        return list(rows)
    cool = now - config.PLAYHEAL_ISSUE_COOLDOWN
    return [r for r in rows if not r["triggered_at"] or r["triggered_at"] < cool]


def live_groups(site: str, *, force: bool = False) -> list[list]:
    """The heal-class rows of ``site`` grouped by issue class, the largest group (then the newest) first; without ``force`` only
    groups with at least ``PLAYHEAL_ISSUE_MIN_SOURCES`` different sources."""
    groups: dict[str, list] = {}
    for row in _rows(site, force=force):
        groups.setdefault(row["issue_class"], []).append(row)
    need = 1 if force else max(1, int(config.PLAYHEAL_ISSUE_MIN_SOURCES))
    out = [g for g in groups.values() if len(g) >= need]
    return sorted(out, key=lambda g: (-len(g), -max(r["last_at"] for r in g)))


def _ok_examples(site: str) -> list[dict]:
    try:
        rows = db.query("SELECT id,locator FROM video_sources WHERE source=? AND resolver='page' AND kind!='trailer' AND status='healthy' "
                        "AND id NOT IN (SELECT source_id FROM playback_issues) ORDER BY last_success_at DESC LIMIT ?", (site, OK_EXAMPLES))
    except Exception:
        return []
    return [{"source_id": r["id"], "locator": r["locator"]} for r in rows]


def evidence(site: str, *, force: bool = False) -> Optional[dict]:
    """The ``heal.heal_site_playback`` evidence of the largest issue group of ``site`` (see the module text) or None. ``force`` (the admin
    button): any one row counts, sources of an earlier repair too."""
    groups = live_groups(site, force=force)
    if not groups:
        return None
    group = groups[0]
    cls = group[0]["issue_class"]
    newest = sorted(group, key=lambda r: -r["last_at"])
    hosts: dict[str, int] = {}
    for r in group:
        if r["host"]:
            hosts[r["host"]] = hosts.get(r["host"], 0) + 1
    host = max(hosts, key=hosts.get) if hosts else ""
    top = newest[0]
    failing = [failing_entry(r) for r in newest[:HEAL_EXAMPLES]]
    ok = _ok_examples(site)
    return {"site": site, "kind": "playback", "window": {"n": len(group) + len(ok), "failed": len(group)}, "failing": failing, "ok_examples": ok,
            "issue": {"code": cls, "label": label(cls), "host": host, "provider": top["provider"], "stream_type": top["stream_type"],
                      "note": top["note"], "sources": len(group), "reports": sum(r["reports"] for r in group),
                      "client_codes": sorted({r["client_code"] for r in group})},
            "probe": cls in PROBE_CLASSES}


# --- the admin feed ------------------------------------------------------------------------------------------------------
def summary(site: str) -> dict:
    """``{open, heal_open}``: sources of ``site`` with an issue inside the TTL, and those of a heal class (the admin overview)."""
    try:
        rows = db.query("SELECT issue_class FROM playback_issues WHERE site=? AND last_at>=?", (site, _now() - config.PLAYHEAL_ISSUE_TTL))
    except Exception:
        return {"open": 0, "heal_open": 0}
    return {"open": len(rows), "heal_open": sum(1 for r in rows if r["issue_class"] in HEAL_CLASSES)}


def _host_rule(host: str):
    try:
        from . import hostrules
        return hostrules.find(host)
    except Exception:
        return None


def events(limit: int = FEED_GROUPS, site: Optional[str] = None) -> list[dict]:
    """The issues as admin events (``kind: playback_issue``), newest first: one per (site, class, stream host) =
    ``{id, kind, at, first_at, site, code, label, host, provider, stream_type, note, http, sources, reports, heal_class, triggered_at,
    episodes: [{source_id, title, season, episode, episode_id, locator, reports, client_code, at}]}``."""
    try:
        where, args = "WHERE i.last_at>=?", [_now() - KEEP_SECONDS]
        if site:
            where += " AND i.site=?"
            args.append(site)
        rows = db.query(f"""SELECT i.*, li.title AS title, vs.season AS season, vs.episode AS episode FROM playback_issues i
            LEFT JOIN library_items li ON li.id=i.canonical_id LEFT JOIN video_sources vs ON vs.id=i.source_id {where}
            ORDER BY i.last_at DESC LIMIT ?""", (*args, FEED_ROWS))
    except Exception:
        return []
    groups: dict[tuple, dict] = {}
    for r in rows:
        key = (r["site"], r["issue_class"], r["host"])
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"id": "pi_" + hashlib.sha1("|".join(key).encode()).hexdigest()[:10], "kind": "playback_issue", "at": _iso(r["last_at"]),
                               "first_at": _iso(r["first_at"]), "site": r["site"], "code": r["issue_class"], "label": label(r["issue_class"]),
                               "host": r["host"], "provider": r["provider"], "stream_type": r["stream_type"], "note": r["note"], "http": r["http"],
                               "sources": 0, "reports": 0, "heal_class": r["issue_class"] in HEAL_CLASSES,
                               "triggered_at": None, "episodes": [], "host_rule": _host_rule(r["host"])}
        g["sources"] += 1
        g["reports"] += r["reports"]
        g["first_at"] = min(g["first_at"], _iso(r["first_at"]))
        if r["triggered_at"] and (not g["triggered_at"] or _iso(r["triggered_at"]) > g["triggered_at"]):
            g["triggered_at"] = _iso(r["triggered_at"])
        if len(g["episodes"]) < SAMPLES:
            g["episodes"].append({"source_id": r["source_id"], "title": r["title"] or r["canonical_id"], "season": r["season"], "episode": r["episode"],
                                  "episode_id": r["episode_id"], "locator": r["locator"], "reports": r["reports"], "client_code": r["client_code"],
                                  "at": _iso(r["last_at"])})
    out = sorted(groups.values(), key=lambda g: g["at"], reverse=True)
    return out[:max(1, int(limit))]
