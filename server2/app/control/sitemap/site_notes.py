"""Site note: the admin's own description of a site (sections, list/archive addresses, search format, where the poster is).
Stored in ``sites.notes`` (DB only, at most ``MAX_CHARS``). Two uses:

- LLM: :func:`block` heads EVERY LLM prompt of that site (stage rounds, repair, routing suggestions) as the
  "Kullanıcının site notu" block, scrubbed and clipped; it is the first thing in the prompt (stable prefix, cacheable) and
  is read by ``site_id``, so another site's prompt never gets it.
- LLM-free: ``control.stages.hints`` reads addresses / selectors out of it for discovery (A1 review, A3-A5, A7).
"""
from __future__ import annotations

import time
from typing import Optional

from ... import db
from ...core.llm.client import scrub
from . import store

MAX_CHARS = 4000
TITLE = "Kullanıcının site notu"


def get(site_id: str) -> str:
    row = db.query_one("SELECT notes FROM sites WHERE site_id=?", (site_id,))
    return str(row["notes"] or "") if row else ""


def info(site_id: str) -> dict:
    """``{notes, updated_at, applied_at}`` (``SiteNotFound`` for an unknown site)."""
    row = db.query_one("SELECT notes, notes_updated_at, notes_applied_at FROM sites WHERE site_id=?", (site_id,))
    if row is None:
        raise store.SiteNotFound(site_id)
    return {"notes": str(row["notes"] or ""), "updated_at": row["notes_updated_at"], "applied_at": row["notes_applied_at"]}


def clean(text: object) -> str:
    """The stored form; ``ValueError`` over ``MAX_CHARS``."""
    out = str(text or "").replace("\r\n", "\n").strip()
    if len(out) > MAX_CHARS:
        raise ValueError(f"site notu en çok {MAX_CHARS} karakter olabilir ({len(out)})")
    return out


def save(site_id: str, text: object, *, applied: bool = False) -> dict:
    """Store the note (does NOT reset or rerun any stage). ``applied``: the stages have not run yet / run with it now."""
    note = clean(text)
    old = info(site_id)
    now = int(time.time())
    if note == old["notes"] and not applied:
        return info(site_id)
    db.execute("UPDATE sites SET notes=?, notes_updated_at=?, notes_applied_at=COALESCE(?, notes_applied_at) WHERE site_id=?",
               (note or None, now, now if applied else None, site_id))
    return info(site_id)


def mark_applied(site_id: str) -> None:
    db.execute("UPDATE sites SET notes_applied_at=? WHERE site_id=?", (int(time.time()), site_id))


def changed(site_id: str) -> bool:
    """The note was edited after the stages last ran with it."""
    try:
        got = info(site_id)
    except store.SiteNotFound:
        return False
    return bool(got["updated_at"]) and int(got["updated_at"]) > int(got["applied_at"] or 0)


def block(site_id: Optional[str]) -> str:
    """The prompt prefix of ``site_id`` ("" without a note): scrubbed (a password / key in user text never reaches the
    LLM) and clipped to ``MAX_CHARS``."""
    if not site_id:
        return ""
    note = scrub(get(site_id))[:MAX_CHARS].strip()
    if not note:
        return ""
    return (f"## {TITLE.upper()} ({TITLE}; the site's admin wrote it about THIS site, use it as a strong hint)\n'''\n"
            f"{note}\n'''\n\n")


def with_note(site_id: Optional[str], prompt: str) -> str:
    return block(site_id) + str(prompt or "")
