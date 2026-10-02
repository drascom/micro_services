"""Deleting a scraper site: its config files, admin settings and (``purge``) the library records it fed.

``delete_site(site, purge=True)`` (admin ``DELETE /api/ops/sites/{site}``):

1. ``purge``: ONE database transaction removes the site's ``video_sources`` (+ their ``playback_attempts`` / ``finder_jobs``),
   ``source_items``, ``identity_reviews``, home lists (``library_lists``: ``<role>_<site>``, ``source_<site>``,
   ``detail_<site>_*`` and the ids its yaml ``collections:`` declared) and the notifications of the site. A canonical title
   is deleted (with its seasons/episodes, external ids, provenance, aliases) ONLY when no other source is left for it;
   a title another site also serves stays and is re-merged from the remaining sources. User data (``progress``,
   ``mylist``, ``continue_hidden``) is never touched, even for a deleted title (the answer reports how many rows stay).
2. the tombstone (``scraper.config.tombstone_add``) is written, then the config files go
   (``<site>.yaml``, ``.baseline.json``, ``.v<N>.yaml``; provider recipes stay), the admin settings entry is forgotten.
3. the catalogue snapshot is rebuilt. Image / subtitle caches are not touched. The ops history (``_ops.json``) and
   ``scraper_state/<site>.json`` stay (audit trail; the site just no longer lists).

A database failure leaves everything as it was (the exception propagates); a failure after the commit raises
:class:`DeleteIncomplete` carrying what was done. The code modules of a hand-built site stay in the repository.
"""
from __future__ import annotations

import logging
import os
import re
from contextlib import closing
from typing import Any

import yaml

from .. import db, settings
from ..scraper import collections as scollections, config as scfg

log = logging.getLogger("library.purge")

SITE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
HAND_BUILT_NOTE = "kod modülü repoda kalır; yeniden eklemek için yaml gerekir"


class DeleteIncomplete(Exception):
    """The database part was committed but a later step failed; ``result`` is what was done (and ``errors``)."""

    def __init__(self, result: dict[str, Any]) -> None:
        super().__init__("; ".join(result.get("errors") or ["incomplete"]))
        self.result = result


# --- what is a hand-built site -----------------------------------------------------------------------------------

def is_hand_built(site: str) -> bool:
    """A site with its own code: a ``@register``ed normalize function, a ``site_extractors/<site>.py`` module or a live
    search adapter module (the config-only sites the onboarding writes have none of these)."""
    from ..scraper import site_extractors, site_search
    from . import normalize
    if site in normalize._REGISTRY or site in site_search._REGISTRY:
        return True
    return os.path.isfile(os.path.join(os.path.dirname(os.path.abspath(site_extractors.__file__)), f"{site}.py"))


# --- list ids ------------------------------------------------------------------------------------------------------

def _declared_collection_ids(site: str) -> set[str]:
    """Collection ids the site's yaml (active + archived versions) declares."""
    paths = [os.path.join(scfg.CONFIG_DIR, f"{site}.yaml")]
    paths += [os.path.join(scfg.CONFIG_DIR, f"{site}.v{n}.yaml") for n in scfg.archived_versions(site)]
    ids: set[str] = set()
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError):
            continue
        cols = data.get("collections") if isinstance(data, dict) else None
        for col in cols if isinstance(cols, list) else []:
            if isinstance(col, dict) and isinstance(col.get("id"), str) and col["id"].strip():
                ids.add(col["id"].strip())
    return ids


def list_ids(site: str) -> tuple[set[str], list[str]]:
    """``(exact list ids, ids of the ``detail_<site>_*`` lists present)`` of ``library_lists`` that belong to ``site``.

    Exact: ``<role>_<site>`` for every role, ``source_<site>`` and the collection ids its yaml declared, minus the ids
    another registered site declares too (a generic id such as ``new`` is not deleted from under it)."""
    others = [s for s in scfg.list_sites() if s != site]
    mine = {scollections.list_id(role, site) for role in scollections.ROLES} | {f"source_{site}"}
    mine |= _declared_collection_ids(site)
    for other in others:
        mine -= _declared_collection_ids(other)
    prefix = f"detail_{site}_"
    longer = [f"detail_{o}_" for o in others if o.startswith(site + "_")]   # site "a" vs site "a_b": the longer id wins
    details = [r["list_id"] for r in db.query("SELECT DISTINCT list_id FROM library_lists WHERE list_id GLOB ?", (prefix + "*",))
               if not any(r["list_id"].startswith(p) for p in longer)]
    return mine, details


# --- the database part ---------------------------------------------------------------------------------------------

_COUNTERS = ("source_items", "video_sources", "library_lists", "library_items", "notifications",
             "playback_attempts", "finder_jobs", "identity_reviews", "shared_kept", "blocked_pages")


def _count(conn, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row else 0


def _purge_db(site: str, exact_lists: set[str], detail_lists: list[str]) -> dict[str, Any]:
    """The whole purge in ONE ``BEGIN IMMEDIATE`` transaction (see the module doc); returns the counters."""
    from .ingest import merge_canonical
    out: dict[str, Any] = {k: 0 for k in _COUNTERS}
    with closing(db.connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("CREATE TEMP TABLE _cand (id TEXT PRIMARY KEY)")
            conn.execute("CREATE TEMP TABLE _vs (id TEXT PRIMARY KEY)")
            conn.execute("CREATE TEMP TABLE _orph (id TEXT PRIMARY KEY)")
            conn.execute("INSERT OR IGNORE INTO _cand SELECT canonical_id FROM source_items WHERE source=? AND canonical_id IS NOT NULL", (site,))
            conn.execute("INSERT OR IGNORE INTO _cand SELECT canonical_id FROM video_sources WHERE source=?", (site,))
            conn.execute("INSERT OR IGNORE INTO _vs SELECT id FROM video_sources WHERE source=?", (site,))

            out["playback_attempts"] = conn.execute("DELETE FROM playback_attempts WHERE source_id IN (SELECT id FROM _vs)").rowcount
            out["finder_jobs"] = conn.execute("DELETE FROM finder_jobs WHERE source_id IN (SELECT id FROM _vs)").rowcount
            out["video_sources"] = conn.execute("DELETE FROM video_sources WHERE source=?", (site,)).rowcount
            out["source_items"] = conn.execute("DELETE FROM source_items WHERE source=?", (site,)).rowcount
            out["identity_reviews"] = conn.execute("DELETE FROM identity_reviews WHERE source=?", (site,)).rowcount
            out["blocked_pages"] = conn.execute("DELETE FROM blocked_pages WHERE site=?", (site,)).rowcount   # verdicts of blocked content (library/gate.py)
            lists = sorted(exact_lists | set(detail_lists))
            for start in range(0, len(lists), 200):
                chunk = lists[start:start + 200]
                out["library_lists"] += conn.execute(
                    "DELETE FROM library_lists WHERE list_id IN (%s)" % ",".join("?" * len(chunk)), chunk).rowcount
            out["notifications"] = conn.execute(
                "DELETE FROM notifications WHERE CASE WHEN json_valid(payload) THEN json_extract(payload,'$.site') END = ?",
                (site,)).rowcount

            # a candidate with no source left anywhere is an orphan: it leaves the library; the others are re-merged below
            conn.execute("""INSERT INTO _orph SELECT c.id FROM _cand c
                            WHERE NOT EXISTS (SELECT 1 FROM source_items s WHERE s.canonical_id=c.id)
                              AND NOT EXISTS (SELECT 1 FROM video_sources v WHERE v.canonical_id=c.id)""")
            kept_user = {t: _count(conn, f"SELECT COUNT(*) FROM {t} WHERE item_id IN (SELECT id FROM _orph)")
                         for t in ("progress", "mylist", "continue_hidden")}
            for table in ("library_seasons", "library_episodes", "external_ids", "field_provenance", "catalogue_aliases"):
                conn.execute(f"DELETE FROM {table} WHERE canonical_id IN (SELECT id FROM _orph)")
            out["library_lists"] += conn.execute("DELETE FROM library_lists WHERE canonical_id IN (SELECT id FROM _orph)").rowcount
            out["identity_reviews"] += conn.execute("DELETE FROM identity_reviews WHERE canonical_id IN (SELECT id FROM _orph)").rowcount
            out["finder_jobs"] += conn.execute("DELETE FROM finder_jobs WHERE canonical_id IN (SELECT id FROM _orph)").rowcount
            out["notifications"] += conn.execute("DELETE FROM notifications WHERE canonical_id IN (SELECT id FROM _orph)").rowcount
            out["library_items"] = conn.execute("DELETE FROM library_items WHERE id IN (SELECT id FROM _orph)").rowcount

            kept = [r[0] for r in conn.execute("SELECT id FROM _cand WHERE id NOT IN (SELECT id FROM _orph)").fetchall()]
            for cid in kept:   # shared titles: fields come from the remaining sources only
                merge_canonical(conn, cid)
            out["shared_kept"] = len(kept)
            out["kept_user_data"] = kept_user
            for temp in ("_cand", "_vs", "_orph"):
                conn.execute(f"DROP TABLE {temp}")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return out


# --- a few titles of a site (content that is not public, library/gate.py) -------------------------------------------

_ITEM_COUNTERS = ("source_items", "video_sources", "library_lists", "library_items", "notifications",
                  "playback_attempts", "finder_jobs", "identity_reviews", "shared_kept")


def purge_source_items(site: str, keys: list[str]) -> dict[str, Any]:
    """Take the source rows ``keys`` (``source_key``) of ONE site out of the library, in one ``BEGIN IMMEDIATE`` transaction: their
    ``video_sources`` (+ attempts / finder jobs), ``source_items`` and identity reviews. A canonical title that has no source left
    anywhere is deleted with its seasons / episodes / external ids / provenance / aliases / list memberships / notifications; a title
    another source (or site) still serves stays and is re-merged from what is left. User data (``progress``, ``mylist``,
    ``continue_hidden``) is never touched: it waits for the title to come back. The site's config, settings and its other
    items are not touched. Returns the counters of ``_ITEM_COUNTERS`` (``library_items`` = titles that left the library). The
    catalogue snapshot is NOT rebuilt here (the caller's progress hook / refresh does that)."""
    from .ingest import merge_canonical
    out: dict[str, Any] = {k: 0 for k in _ITEM_COUNTERS}
    keys = sorted({str(k) for k in keys if k})
    if not keys:
        return out
    with closing(db.connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("CREATE TEMP TABLE _cand (id TEXT PRIMARY KEY)")
            conn.execute("CREATE TEMP TABLE _vs (id TEXT PRIMARY KEY)")
            conn.execute("CREATE TEMP TABLE _orph (id TEXT PRIMARY KEY)")
            conn.execute("CREATE TEMP TABLE _keys (k TEXT PRIMARY KEY)")
            conn.executemany("INSERT OR IGNORE INTO _keys VALUES (?)", [(k,) for k in keys])
            conn.execute("INSERT OR IGNORE INTO _cand SELECT canonical_id FROM source_items WHERE source=? AND source_key IN (SELECT k FROM _keys) "
                         "AND canonical_id IS NOT NULL", (site,))
            conn.execute("INSERT OR IGNORE INTO _cand SELECT canonical_id FROM video_sources WHERE source=? AND source_key IN (SELECT k FROM _keys)", (site,))
            conn.execute("INSERT OR IGNORE INTO _vs SELECT id FROM video_sources WHERE source=? AND source_key IN (SELECT k FROM _keys)", (site,))
            out["playback_attempts"] = conn.execute("DELETE FROM playback_attempts WHERE source_id IN (SELECT id FROM _vs)").rowcount
            out["finder_jobs"] = conn.execute("DELETE FROM finder_jobs WHERE source_id IN (SELECT id FROM _vs)").rowcount
            out["video_sources"] = conn.execute("DELETE FROM video_sources WHERE source=? AND source_key IN (SELECT k FROM _keys)", (site,)).rowcount
            out["source_items"] = conn.execute("DELETE FROM source_items WHERE source=? AND source_key IN (SELECT k FROM _keys)", (site,)).rowcount
            out["identity_reviews"] = conn.execute("DELETE FROM identity_reviews WHERE source=? AND source_key IN (SELECT k FROM _keys)",
                                                   (site,)).rowcount
            conn.execute("""INSERT INTO _orph SELECT c.id FROM _cand c
                            WHERE NOT EXISTS (SELECT 1 FROM source_items s WHERE s.canonical_id=c.id)
                              AND NOT EXISTS (SELECT 1 FROM video_sources v WHERE v.canonical_id=c.id)""")
            for table in ("library_seasons", "library_episodes", "external_ids", "field_provenance", "catalogue_aliases"):
                conn.execute(f"DELETE FROM {table} WHERE canonical_id IN (SELECT id FROM _orph)")
            out["library_lists"] = conn.execute("DELETE FROM library_lists WHERE canonical_id IN (SELECT id FROM _orph)").rowcount
            out["identity_reviews"] += conn.execute("DELETE FROM identity_reviews WHERE canonical_id IN (SELECT id FROM _orph)").rowcount
            out["finder_jobs"] += conn.execute("DELETE FROM finder_jobs WHERE canonical_id IN (SELECT id FROM _orph)").rowcount
            out["notifications"] = conn.execute("DELETE FROM notifications WHERE canonical_id IN (SELECT id FROM _orph)").rowcount
            out["library_items"] = conn.execute("DELETE FROM library_items WHERE id IN (SELECT id FROM _orph)").rowcount
            kept = [r[0] for r in conn.execute("SELECT id FROM _cand WHERE id NOT IN (SELECT id FROM _orph)").fetchall()]
            for cid in kept:   # shared titles: fields come from the remaining sources only
                merge_canonical(conn, cid)
            out["shared_kept"] = len(kept)
            for temp in ("_cand", "_vs", "_orph", "_keys"):
                conn.execute(f"DROP TABLE {temp}")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return out


# --- the whole deletion --------------------------------------------------------------------------------------------

def delete_site(site: str, *, purge: bool = True, refresh: bool = True) -> dict[str, Any]:
    """Delete a registered site (see the module doc). ``LookupError`` for a bad / unregistered id;
    :class:`DeleteIncomplete` when a step after the database commit failed.

    Returns ``{"deleted": True, "files": [...], "purged": {...}|None, "tombstone": True, "hand_built": bool,
    "settings_removed": bool, "kept_user_data": {...}, "note"?: str, "warnings": [...]}``."""
    if not SITE_ID_RE.match(site or "") or site not in scfg.list_sites():
        raise LookupError(f"unknown scraper site {site!r}")
    hand_built = is_hand_built(site)
    result: dict[str, Any] = {"deleted": False, "files": [], "purged": None, "tombstone": False, "hand_built": hand_built,
                              "settings_removed": False, "kept_user_data": None, "warnings": [], "errors": []}
    if hand_built:
        result["note"] = HAND_BUILT_NOTE

    if purge:
        exact, details = list_ids(site)
        purged = _purge_db(site, exact, details)   # a failure here changes nothing and propagates
        result["kept_user_data"] = purged.pop("kept_user_data")
        result["purged"] = purged
    else:   # library records stay: nothing purged (zero counters keep the answer's shape)
        result["purged"] = {k: 0 for k in _COUNTERS}

    files = scfg.site_files(site)
    try:
        scfg.tombstone_add(site, files)   # first: when the file removal dies half way deploy.sh still sees the deletion
        result["tombstone"] = True
        result["files"] = scfg.delete_site_files(site)
        left = scfg.site_files(site)
        if left:
            raise OSError("config files left: " + ", ".join(left))
    except Exception as exc:
        log.exception("site %s: config files not removed", site)
        result["errors"].append(f"config files: {type(exc).__name__}: {exc}")
        if site in scfg.list_sites():   # still registered: no tombstone for a site that is still there
            try:
                scfg.tombstone_clear(site)
                result["tombstone"] = False
            except Exception:
                log.exception("site %s: tombstone not cleared", site)
        raise DeleteIncomplete(result)

    try:
        result["settings_removed"] = settings.forget_site(site)
    except Exception as exc:   # the site is gone anyway; a stale settings entry is harmless
        log.warning("site %s: settings entry not removed: %s", site, exc)
        result["warnings"].append(f"settings: {type(exc).__name__}")
    try:   # the removed yaml's image_hosts leave the artwork proxy allow-list at once
        from .. import images
        images._site_hosts_cache = (None, 0.0, [])
    except Exception:
        pass
    if refresh:
        try:
            from .. import cache
            cache.refresh()
        except Exception as exc:
            log.warning("site %s: catalogue not refreshed: %s", site, exc)
            result["warnings"].append(f"cache: {type(exc).__name__}")
    result["deleted"] = True
    result.pop("errors", None)
    return result
