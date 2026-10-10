"""Sample page store: the bodies of fetched pages, per site, for the stage engine / heal / cache_s.

Body: ``DATA_DIR/pages/<site>/<sha1(url)>.html``; record: ``sample_pages`` (one row per site + url, a re-save
replaces it). At most ``MAX_PER_SITE`` pages per site, the oldest are dropped.
"""
from __future__ import annotations

import builtins
import hashlib
import os
import re
import time
from typing import Optional

from ... import config, db
from .base import Page

MAX_PER_SITE = 200
_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _site_dir(site_id: str) -> str:
    safe = _SAFE.sub("_", site_id or "_")[:80].lstrip(".") or "_"
    return os.path.join(config.PAGES_DIR, safe)


def _sha1(url: str) -> str:
    return hashlib.sha1((url or "").encode("utf-8")).hexdigest()


def _path(site_id: str, sha1: str) -> str:
    return os.path.join(_site_dir(site_id), sha1 + ".html")


def save(site_id: str, page_type: str, page: Page) -> str:
    """Store ``page`` (body file + row); returns the body path."""
    sha1 = _sha1(page.url)
    path = _path(site_id, sha1)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(page.html or "")
    os.replace(tmp, path)
    db.execute(
        "INSERT INTO sample_pages(site_id, page_type, url, final_url, status, engine, sha1, bytes, ms, fetched_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(site_id, url) DO UPDATE SET page_type=excluded.page_type,"
        " final_url=excluded.final_url, status=excluded.status, engine=excluded.engine, sha1=excluded.sha1,"
        " bytes=excluded.bytes, ms=excluded.ms, fetched_at=excluded.fetched_at",
        (site_id, page_type or "page", page.url, page.final_url or page.url, int(page.status or 0), page.engine,
         sha1, len((page.html or "").encode("utf-8")), int(page.ms or 0), time.time()))
    _prune(site_id)
    return path


def _prune(site_id: str) -> None:
    rows = db.query("SELECT id, sha1 FROM sample_pages WHERE site_id=? ORDER BY fetched_at DESC, id DESC LIMIT -1 OFFSET ?",
                    (site_id, MAX_PER_SITE))
    for row in rows:
        db.execute("DELETE FROM sample_pages WHERE id=?", (row["id"],))
        try:
            os.remove(_path(site_id, row["sha1"]))
        except OSError:
            pass


def _row_page(site_id: str, row) -> Optional[Page]:
    try:
        with open(_path(site_id, row["sha1"]), "r", encoding="utf-8") as fh:
            html = fh.read()
    except OSError:
        return None
    return Page(row["url"], row["final_url"], row["status"], html, row["engine"], row["ms"], True)


def load(site_id: str, url: str, *, max_age_s: Optional[float] = None) -> Optional[Page]:
    """The stored page of ``url`` (``from_cache=True``), or None; ``max_age_s``: only when stored that recently."""
    row = db.query_one("SELECT * FROM sample_pages WHERE site_id=? AND url=?", (site_id, url))
    if row is None or (max_age_s is not None and time.time() - row["fetched_at"] > max_age_s):
        return None
    return _row_page(site_id, row)


def list_pages(site_id: str, page_type: str = "") -> builtins.list[dict]:
    """Metadata of the stored pages (newest first), optionally of one ``page_type``."""
    if page_type:
        rows = db.query("SELECT * FROM sample_pages WHERE site_id=? AND page_type=? ORDER BY fetched_at DESC, id DESC",
                        (site_id, page_type))
    else:
        rows = db.query("SELECT * FROM sample_pages WHERE site_id=? ORDER BY fetched_at DESC, id DESC", (site_id,))
    return [{k: r[k] for k in r.keys() if k != "id"} | {"path": _path(site_id, r["sha1"])} for r in rows]


list = list_pages   # README name: pages.list(site_id, page_type)
