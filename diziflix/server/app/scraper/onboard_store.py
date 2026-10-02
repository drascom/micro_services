"""Throw-away storage of the site-onboarding sandbox: fetched pages and drafts, under ``DATA_DIR/onboard/``.

    pages/<page_id>.html + pages/<page_id>.json   fetched HTML (capped) + meta {url, final_url, fetch_mode, status, bytes, fetched_at[, referer]}
    drafts/<draft_id>.json                        {id, url, status, created_at, updated_at, site_id_suggestion, yaml_text, report, events, error}
    repairs/<job_id>.json                         the proposal of a heal repair agent (``submit_repair``; see the repairs section)

Nothing here ever touches ``scraper/configs``. Writes are atomic (tmp + ``os.replace``) and the draft read-modify-write
is serialised by one lock. ``prune()`` drops pages and drafts older than ``ONBOARD_RETENTION_DAYS`` (``saved`` drafts stay).
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import uuid
from typing import Any, Optional

from .. import config

STATUSES = ("running", "needs_input", "ready", "failed", "cancelled", "saved")
MAX_EVENTS = 1000
PRUNE_INTERVAL = 3600.0   # maybe_prune(): at most once an hour

_PAGE_RE = re.compile(r"^pg_[0-9a-f]{12}$")
_DRAFT_RE = re.compile(r"^od_[0-9a-f]{12}$")
_lock = threading.RLock()
_last_prune = 0.0


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def root() -> str:
    return os.path.join(config.DATA_DIR, "onboard")


def _dir(kind: str) -> str:
    path = os.path.join(root(), kind)
    os.makedirs(path, exist_ok=True)
    return path


def _atomic_write(path: str, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _write_json(path: str, data: Any) -> None:
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))


def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def valid_page_id(page_id: Any) -> bool:
    return isinstance(page_id, str) and bool(_PAGE_RE.match(page_id))


def valid_draft_id(draft_id: Any) -> bool:
    return isinstance(draft_id, str) and bool(_DRAFT_RE.match(draft_id))


# --- pages ------------------------------------------------------------------------------------------------------

def save_page(url: str, final_url: str, fetch_mode: str, status: int, html: str, referer: str = "") -> dict:
    """Store a fetched page (HTML cut at ``ONBOARD_MAX_PAGE_BYTES``) and return its meta, ``page_id`` included
    (``referer`` is recorded only when the page was fetched with one)."""
    raw = (html or "").encode("utf-8", errors="replace")
    truncated = len(raw) > config.ONBOARD_MAX_PAGE_BYTES
    if truncated:
        raw = raw[:config.ONBOARD_MAX_PAGE_BYTES]
    page_id = "pg_" + uuid.uuid4().hex[:12]
    meta = {"page_id": page_id, "url": url, "final_url": final_url, "fetch_mode": fetch_mode, "status": status,
            "bytes": len(raw), "truncated": truncated, "fetched_at": _now()}
    if referer:
        meta["referer"] = referer
    pages = _dir("pages")
    _atomic_write(os.path.join(pages, page_id + ".html"), raw)
    _write_json(os.path.join(pages, page_id + ".json"), meta)
    return meta


def load_page(page_id: str) -> Optional[tuple[str, dict]]:
    """``(html, meta)`` of a stored page, ``None`` when unknown (malformed ids never touch the disk)."""
    if not valid_page_id(page_id):
        return None
    base = os.path.join(root(), "pages", page_id)
    meta = _read_json(base + ".json")
    if meta is None:
        return None
    try:
        with open(base + ".html", "rb") as fh:
            return fh.read().decode("utf-8", errors="replace"), meta
    except OSError:
        return None


# --- drafts -----------------------------------------------------------------------------------------------------

def _draft_path(draft_id: str) -> Optional[str]:
    return os.path.join(root(), "drafts", draft_id + ".json") if valid_draft_id(draft_id) else None


def create_draft(url: str, site_id_suggestion: str = "") -> dict:
    now = _now()
    draft = {"id": "od_" + uuid.uuid4().hex[:12], "url": url, "status": "running", "created_at": now,
             "updated_at": now, "site_id_suggestion": site_id_suggestion or "", "yaml_text": "", "report": None,
             "events": [], "error": None}
    with _lock:
        _write_json(os.path.join(_dir("drafts"), draft["id"] + ".json"), draft)
    return draft


def get_draft(draft_id: str) -> Optional[dict]:
    path = _draft_path(draft_id)
    return _read_json(path) if path else None


def update_draft(draft_id: str, **fields: Any) -> Optional[dict]:
    """Merge ``fields`` into a draft (``id`` / ``created_at`` are fixed, ``status`` must be a known one) and return it;
    ``None`` when the draft does not exist."""
    if "status" in fields and fields["status"] not in STATUSES:
        raise ValueError(f"unknown draft status {fields['status']!r}")
    fields.pop("id", None)
    fields.pop("created_at", None)
    path = _draft_path(draft_id)
    if path is None:
        return None
    with _lock:
        draft = _read_json(path)
        if draft is None:
            return None
        draft.update(fields)
        draft["updated_at"] = _now()
        _write_json(path, draft)
        return draft


def append_event(draft_id: str, event: dict) -> Optional[dict]:
    """Append one event (``ts`` is added when missing); only the newest ``MAX_EVENTS`` are kept."""
    path = _draft_path(draft_id)
    if path is None:
        return None
    with _lock:
        draft = _read_json(path)
        if draft is None:
            return None
        events = list(draft.get("events") or [])
        events.append({"ts": _now(), **event})
        draft["events"] = events[-MAX_EVENTS:]
        draft["updated_at"] = _now()
        _write_json(path, draft)
        return draft


# --- live tool results (the pipeline view while the agent works) ------------------------------------------------------
# ``draft["live"]`` = {"test_config": {...}, "test_resolvers": {...}, "test_search": {...}, "test_provider": {...}, "at": ts}:
# the LATEST answer of each sandbox tool the agent called, each cut to ``LIVE_MAX_BYTES`` and stamped with ``_at``. Only
# ``scraper/onboard_pipeline.py`` reads it (``GET /api/ops/onboard/{id}`` never returns it raw).

LIVE_KINDS = ("test_config", "test_resolvers", "test_search", "test_provider")
LIVE_MAX_BYTES = 60_000
_LIVE_LEVELS = ((50, 600, ()), (20, 300, ()), (10, 160, ("samples",)), (5, 100, ("samples", "fields", "candidates", "trace", "resolved", "streams")))


def _shrink(value: Any, max_list: int, max_str: int, drop: tuple, depth: int = 0) -> Any:
    """``value`` with strings cut, lists capped, and (``drop``) the named list / dict members removed; JSON-safe."""
    if isinstance(value, str):
        return value if len(value) <= max_str else value[:max_str - 1] + "…"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if depth > 8:
        return None
    if isinstance(value, (list, tuple)):
        return [_shrink(v, max_list, max_str, drop, depth + 1) for v in value[:max_list]]
    if isinstance(value, dict):
        return {str(k): _shrink(v, max_list, max_str, drop, depth + 1) for k, v in value.items()
                if not (str(k) in drop and isinstance(v, (list, dict)))}
    return str(value)[:max_str]


def trim_live(result: Any, limit: int = LIVE_MAX_BYTES) -> dict:
    """A sandbox tool answer cut down to at most ``limit`` bytes of JSON: strings and lists get shorter step by step, then the
    bulky example members go; as a last resort only the scalar members stay (``_truncated: true``)."""
    out: Any = result
    for max_list, max_str, drop in _LIVE_LEVELS:
        out = _shrink(result, max_list, max_str, drop)
        if isinstance(out, dict) and len(json.dumps(out, ensure_ascii=False).encode("utf-8")) <= limit:
            return out
    base = out if isinstance(out, dict) else {}
    return {**{k: v for k, v in base.items() if not isinstance(v, (list, dict))}, "_truncated": True}


def save_live(draft_id: str, kind: str, result: Any) -> Optional[dict]:
    """Remember the latest ``kind`` answer (one of ``LIVE_KINDS``) in ``draft["live"]``; the others stay. Atomic read-modify-write
    under the draft lock. ``None`` when the draft does not exist (a repair job's id included); ``ValueError`` for an unknown
    ``kind``; a result that is not a dict is ignored."""
    if kind not in LIVE_KINDS:
        raise ValueError(f"unknown live kind {kind!r}")
    path = _draft_path(draft_id)
    if path is None or not isinstance(result, dict):
        return None
    trimmed = trim_live(result)
    with _lock:
        draft = _read_json(path)
        if draft is None:
            return None
        now = _now()
        live = dict(draft.get("live") or {})
        live[kind] = {**trimmed, "_at": now}
        live["at"] = now
        draft["live"] = live
        draft["updated_at"] = now
        _write_json(path, draft)
        return draft


def list_drafts(status: Optional[str] = None) -> list[dict]:
    """Drafts, newest first (optionally only one status)."""
    folder = os.path.join(root(), "drafts")
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    out = []
    for name in names:
        if not name.endswith(".json") or not valid_draft_id(name[:-5]):
            continue
        draft = _read_json(os.path.join(folder, name))
        if draft is not None and (status is None or draft.get("status") == status):
            out.append(draft)
    out.sort(key=lambda d: (d.get("created_at") or "", d.get("id") or ""), reverse=True)
    return out


# --- repairs (heal agent working records) -----------------------------------------------------------------------
# ``repairs/<job_id>.json``: what the repair agent of ``scraper/heal_agent.py`` handed in with ``submit_repair``
# ({job_id, site_id, yaml_text, provider_recipes, notes, submitted_at, submissions}). A record is a PROPOSAL: nothing
# here (or in the sandbox) applies it; the heal gates decide.

_REPAIR_RE = re.compile(r"^rp_[0-9a-f]{12}$")


def valid_repair_id(job_id: Any) -> bool:
    return isinstance(job_id, str) and bool(_REPAIR_RE.match(job_id))


def new_repair_id() -> str:
    return "rp_" + uuid.uuid4().hex[:12]


def _repair_path(job_id: str) -> Optional[str]:
    return os.path.join(root(), "repairs", job_id + ".json") if valid_repair_id(job_id) else None


def save_repair(job_id: str, record: dict) -> Optional[dict]:
    """Write (replace) the proposal of a repair job; ``submissions`` counts the calls. ``None`` for a malformed job id."""
    path = _repair_path(job_id)
    if path is None:
        return None
    with _lock:
        old = _read_json(path) or {}
        data = {**record, "job_id": job_id, "submitted_at": _now(), "submissions": int(old.get("submissions") or 0) + 1}
        _dir("repairs")
        _write_json(path, data)
        return data


def load_repair(job_id: str) -> Optional[dict]:
    path = _repair_path(job_id)
    return _read_json(path) if path else None


# --- retention --------------------------------------------------------------------------------------------------

def prune(now: Optional[float] = None) -> dict:
    """Delete pages and drafts untouched for ``ONBOARD_RETENTION_DAYS`` (``saved`` drafts are kept). Returns counts."""
    cutoff = (time.time() if now is None else now) - config.ONBOARD_RETENTION_DAYS * 86400
    removed = {"pages": 0, "drafts": 0}
    pages = os.path.join(root(), "pages")
    try:
        names = os.listdir(pages)
    except OSError:
        names = []
    for name in names:
        path = os.path.join(pages, name)
        try:
            old = os.stat(path).st_mtime < cutoff
        except OSError:
            continue
        if old:
            try:
                os.unlink(path)
            except OSError:
                continue
            if name.endswith(".json"):
                removed["pages"] += 1
    folder = os.path.join(root(), "drafts")
    try:
        names = os.listdir(folder)
    except OSError:
        names = []
    with _lock:
        for name in names:
            path = os.path.join(folder, name)
            try:
                old = os.stat(path).st_mtime < cutoff
            except OSError:
                continue
            if not old:
                continue
            draft = _read_json(path) if name.endswith(".json") else None
            if draft is not None and draft.get("status") == "saved":
                continue
            try:
                os.unlink(path)
                removed["drafts"] += 1
            except OSError:
                pass
    folder = os.path.join(root(), "repairs")   # repair proposals: same age limit, not counted
    try:
        names = os.listdir(folder)
    except OSError:
        names = []
    for name in names:
        path = os.path.join(folder, name)
        try:
            if os.stat(path).st_mtime < cutoff:
                os.unlink(path)
        except OSError:
            pass
    return removed


def maybe_prune() -> None:
    """``prune()`` at most once per ``PRUNE_INTERVAL`` (cheap to call on every sandbox fetch; never raises)."""
    global _last_prune
    now = time.time()
    if now - _last_prune < PRUNE_INTERVAL:
        return
    _last_prune = now
    try:
        prune()
    except Exception:
        pass
