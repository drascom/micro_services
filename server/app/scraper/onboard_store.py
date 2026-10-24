"""Throw-away storage of the heal repair sandbox: fetched pages and repair proposals, under ``DATA_DIR/onboard/``.

    pages/<page_id>.html + pages/<page_id>.json   fetched HTML (capped) + meta {url, final_url, fetch_mode, status, bytes, fetched_at[, referer]}
    drafts/                                       legacy: drafts of the removed site onboarding (2026-10-24); only pruned
    repairs/<job_id>.json                         the proposal of a heal repair agent (``submit_repair``; see the repairs section)

Nothing here ever touches ``scraper/configs``. Writes are atomic (tmp + ``os.replace``). ``prune()`` drops pages, old
drafts and repair records older than ``ONBOARD_RETENTION_DAYS`` (``saved`` drafts stay).
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

PRUNE_INTERVAL = 3600.0   # maybe_prune(): at most once an hour

_PAGE_RE = re.compile(r"^pg_[0-9a-f]{12}$")
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


# --- pages ------------------------------------------------------------------------------------------------------

def save_page(url: str, final_url: str, fetch_mode: str, status: int, html: str, referer: str = "", post: Optional[str] = None) -> dict:
    """Store a fetched page (HTML cut at ``ONBOARD_MAX_PAGE_BYTES``) and return its meta, ``page_id`` included
    (``referer`` is recorded only when the page was fetched with one; ``post`` = the urlencoded body of a POST fetch, ``""`` = empty POST)."""
    raw = (html or "").encode("utf-8", errors="replace")
    truncated = len(raw) > config.ONBOARD_MAX_PAGE_BYTES
    if truncated:
        raw = raw[:config.ONBOARD_MAX_PAGE_BYTES]
    page_id = "pg_" + uuid.uuid4().hex[:12]
    meta = {"page_id": page_id, "url": url, "final_url": final_url, "fetch_mode": fetch_mode, "status": status,
            "bytes": len(raw), "truncated": truncated, "fetched_at": _now()}
    if referer:
        meta["referer"] = referer
    if post is not None:
        meta["method"], meta["post_data"] = "POST", post
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
