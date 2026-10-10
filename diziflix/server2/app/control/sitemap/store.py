"""Site rows and their maps in the DB (README §5.1/§5.5): working copy ``sites.draft_yaml``, history ``site_versions``.

Nothing here writes a file: the map lives only in the DB (export/import go through yaml TEXT).
"""
from __future__ import annotations

import copy
import re
import time
from typing import Any, Optional
from urllib.parse import urlsplit

import yaml

from ... import db
from ...core import yamlsafe
from . import schema
from .adapters import FETCH_DEFAULTS

STATUSES = ("draft", "active", "paused")
CREATED_BY = ("onboard", "edit", "repair", "admin")
_SECOND_LEVEL = {"co", "com", "net", "org", "gov", "edu", "ac"}
_META_ALIAS = {"search_gate": "search"}   # A7's stage name -> its page/meta key
MAX_YAML_BYTES = 256 * 1024   # a site map is a few kB; import / stored text above this is refused


class SiteNotFound(KeyError):
    pass


class ScopeError(ValueError):
    """A patch touched something outside its own page type."""


class ValidationError(ValueError):
    def __init__(self, problems: list) -> None:
        self.problems = problems
        super().__init__("; ".join(f"{p.path}: {p.message}" for p in problems[:5]) or "invalid site map")


# ---------------------------------------------------------------------------------------------- yaml

class _NoAliasDumper(getattr(yaml, "CSafeDumper", yaml.SafeDumper)):
    """Shared sub-objects are written out in full: ``load`` refuses aliases."""

    def ignore_aliases(self, data):
        return True


def dump(data: dict) -> str:
    return yaml.dump(data, Dumper=_NoAliasDumper, sort_keys=False, allow_unicode=True, default_flow_style=False)


def load(text: str) -> dict:
    """A map from yaml text: no aliases (alias bomb), at most ``MAX_YAML_BYTES``; ``ValidationError`` otherwise."""
    try:
        data = yamlsafe.load(text or "", max_bytes=MAX_YAML_BYTES) or {}
    except yaml.YAMLError as exc:
        raise ValidationError([schema.Problem("", "yaml: " + " ".join(str(exc).split())[:300])]) from None
    if not isinstance(data, dict):
        raise ValidationError([schema.Problem("", "site map must be a mapping")])
    return data


# ---------------------------------------------------------------------------------------------- sites

def _now() -> int:
    return int(time.time())


def _row(site_id: str):
    row = db.query_one("SELECT * FROM sites WHERE site_id=?", (site_id,))
    if row is None:
        raise SiteNotFound(site_id)
    return row


def _origin(base_url: str) -> str:
    parts = urlsplit(base_url.strip())
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


def _id_from_host(base_url: str) -> str:
    host = (urlsplit(base_url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    labels = [part for part in host.split(".") if part]
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL:
        stem = labels[-3]
    else:
        stem = labels[-2] if len(labels) >= 2 else (labels[0] if labels else "site")
    stem = re.sub(r"[^a-z0-9]+", "_", stem).strip("_")[:28] or "site"
    return stem if len(stem) >= 2 else f"{stem}_s"


def empty_map(site_id: str, base_url: str) -> dict:
    return {"map": schema.MAP_VERSION, "site_id": site_id, "base_url": base_url, "fetch": dict(FETCH_DEFAULTS),
            "pages": {}, "meta": {}}


def _insert(site_id: str, base_url: str, display_name: Optional[str], data: dict) -> None:
    now = _now()
    db.execute("INSERT INTO sites(site_id, display_name, base_url, status, active_version, draft_yaml, created_at, updated_at)"
               " VALUES (?, ?, ?, 'draft', NULL, ?, ?, ?)", (site_id, display_name or site_id, base_url, dump(data), now, now))


def create_site(base_url: str, display_name: Optional[str] = None) -> str:
    """New site with an empty draft map; id = host stem (``_2``, ``_3`` ... on collision). Raises ``ValidationError``
    when ``base_url`` is not a public http(s) address."""
    problems = [p for p in schema.validate(empty_map("xx", base_url)) if p.path == "base_url" and p.level == "error"]
    if problems:
        raise ValidationError(problems)
    origin = _origin(base_url)
    stem = _id_from_host(origin)
    with db.transaction():
        site_id, n = stem, 1
        while db.query_one("SELECT 1 FROM sites WHERE site_id=?", (site_id,)):
            n += 1
            site_id = f"{stem}_{n}"
        _insert(site_id, origin, display_name, empty_map(site_id, origin))
    return site_id


def get(site_id: str) -> Optional[dict]:
    row = db.query_one("SELECT * FROM sites WHERE site_id=?", (site_id,))
    return dict(row) if row else None


def list_sites() -> list[dict]:
    return [{k: r[k] for k in r.keys() if k != "draft_yaml"} for r in db.query("SELECT * FROM sites ORDER BY created_at, site_id")]


def set_status(site_id: str, status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    _row(site_id)
    db.execute("UPDATE sites SET status=?, updated_at=? WHERE site_id=?", (status, _now(), site_id))


def delete_site(site_id: str, *, purge: bool) -> dict:
    """Remove the site row and its map history; ``purge`` also removes what it fed into the catalogue."""
    _row(site_id)
    out: dict[str, Any] = {"site_id": site_id}
    if purge:
        from ...core.catalog import write
        out["catalog"] = write.delete_site(site_id)
    with db.transaction() as conn:
        out["versions"] = conn.execute("DELETE FROM site_versions WHERE site_id=?", (site_id,)).rowcount
        conn.execute("DELETE FROM sites WHERE site_id=?", (site_id,))
    return out


# ---------------------------------------------------------------------------------------------- draft

def draft(site_id: str) -> dict:
    return load(_row(site_id)["draft_yaml"])


def _check(site_id: str, data: dict) -> None:
    problems = schema.errors(schema.validate(data))
    if data.get("site_id") != site_id:
        problems.insert(0, schema.Problem("site_id", f"must stay {site_id!r}"))
    if problems:
        raise ValidationError(problems)


def save_draft(site_id: str, data: dict) -> dict:
    """Replace the working copy; refused (``ValidationError``) when the map has errors."""
    _row(site_id)
    _check(site_id, data)
    db.execute("UPDATE sites SET draft_yaml=?, base_url=?, updated_at=? WHERE site_id=?",
               (dump(data), _origin(data["base_url"]), _now(), site_id))
    return copy.deepcopy(data)


def _merge(target: dict, patch: dict) -> None:
    for key, value in patch.items():
        if value is None:
            target.pop(key, None)
        elif isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


def apply_patch(site_id: str, page_type: str, patch: dict, *, meta: Optional[dict] = None) -> dict:
    """Merge a map-shaped ``patch`` (``{pages: {<page_type>: ...}, meta: {<page_type>: ...}}``, plus ``gate`` for A7) into
    the draft. Anything else in the patch -> ``ScopeError``. ``None`` values delete a key. Returns the new draft."""
    key = _META_ALIAS.get(page_type, page_type)
    if key not in schema.PAGE_TYPES:
        raise ValueError(f"unknown page type {page_type!r}")
    patch = patch or {}
    if not isinstance(patch, dict):
        raise ScopeError("patch must be a mapping")
    allowed = {"pages", "meta"} | ({"gate"} if key == "search" else set())
    for top, value in patch.items():
        if top not in allowed:
            raise ScopeError(f"patch for {page_type!r} touches {top!r}")
        if top in ("pages", "meta"):
            if not isinstance(value, dict) or set(value) - {key}:
                others = sorted(set(value) - {key}) if isinstance(value, dict) else [value]
                raise ScopeError(f"patch for {page_type!r} touches {top}.{others}")
    data = draft(site_id)
    _merge(data, patch)
    if meta:
        _merge(data, {"meta": {key: meta}})
    return save_draft(site_id, data)


# ---------------------------------------------------------------------------------------------- versions

def _version_yaml(site_id: str, version: int) -> str:
    row = db.query_one("SELECT yaml FROM site_versions WHERE site_id=? AND version=?", (site_id, version))
    if row is None:
        raise KeyError(f"{site_id} v{version}")
    return row["yaml"]


def active(site_id: str) -> Optional[dict]:
    row = _row(site_id)
    return None if row["active_version"] is None else load(_version_yaml(site_id, row["active_version"]))


def _write_version(site_id: str, data: dict, created_by: str, note: str) -> int:
    if created_by not in CREATED_BY:
        raise ValueError(f"created_by must be one of {', '.join(CREATED_BY)}")
    _check(site_id, data)
    with db.transaction() as conn:
        row = conn.execute("SELECT active_version FROM sites WHERE site_id=?", (site_id,)).fetchone()
        if row is None:
            raise SiteNotFound(site_id)
        if row["active_version"] is not None:
            current = conn.execute("SELECT yaml FROM site_versions WHERE site_id=? AND version=?",
                                   (site_id, row["active_version"])).fetchone()
            if current is not None and load(current["yaml"]) == data:
                return row["active_version"]
        last = conn.execute("SELECT MAX(version) FROM site_versions WHERE site_id=?", (site_id,)).fetchone()[0] or 0
        version = last + 1
        now = _now()
        text = dump(data)
        conn.execute("INSERT INTO site_versions(site_id, version, yaml, created_at, created_by, note) VALUES (?, ?, ?, ?, ?, ?)",
                     (site_id, version, text, now, created_by, note or ""))
        conn.execute("UPDATE sites SET active_version=?, draft_yaml=?, base_url=?, updated_at=? WHERE site_id=?",
                     (version, text, _origin(data["base_url"]), now, site_id))
    return version


def commit(site_id: str, *, created_by: str, note: str = "") -> int:
    """Write the draft as a new version and make it active; returns the active version (unchanged when the draft equals it)."""
    return _write_version(site_id, draft(site_id), created_by, note)


def versions(site_id: str) -> list[dict]:
    _row(site_id)
    return [dict(r) for r in db.query("SELECT version, created_at, created_by, note FROM site_versions WHERE site_id=?"
                                      " ORDER BY version", (site_id,))]


def rollback(site_id: str, version: int, *, created_by: str = "admin") -> int:
    """Re-commit version ``version`` as a NEW version (history is never deleted); the draft follows."""
    data = load(_version_yaml(site_id, version))
    return _write_version(site_id, data, created_by, f"rollback to v{version}")


def _flat(value: Any, path: str, out: dict) -> None:
    if isinstance(value, dict) and value:
        for key, sub in value.items():
            _flat(sub, f"{path}.{key}" if path else str(key), out)
    else:
        out[path] = value


def _resolve(site_id: str, version: Optional[int]) -> dict:
    return draft(site_id) if version is None else load(_version_yaml(site_id, version))


def diff(site_id: str, a: Optional[int], b: Optional[int]) -> list[dict]:
    """``[{path, before, after}]`` between versions ``a`` and ``b`` (``None`` = the draft); lists compare as one value."""
    left, right = {}, {}
    _flat(_resolve(site_id, a), "", left)
    _flat(_resolve(site_id, b), "", right)
    paths = list(left) + [p for p in right if p not in left]
    return [{"path": p, "before": left.get(p), "after": right.get(p)} for p in paths if left.get(p) != right.get(p)]


def export_yaml(site_id: str, version: Optional[int] = None) -> str:
    """The yaml of ``version`` (default: the active one, else the draft)."""
    row = _row(site_id)
    if version is None:
        return _version_yaml(site_id, row["active_version"]) if row["active_version"] is not None else row["draft_yaml"]
    return _version_yaml(site_id, version)


class SiteExists(ValueError):
    """``import_yaml`` of a map whose ``site_id`` already exists, without ``overwrite=True``."""


def import_yaml(text: str, display_name: Optional[str] = None, *, overwrite: bool = False) -> str:
    """Load a map as a DRAFT: a new site with its ``site_id``; into the existing site of that id only with
    ``overwrite=True`` (else ``SiteExists``)."""
    data = load(text)
    site_id = data.get("site_id")
    problems = schema.errors(schema.validate(data))
    if problems:
        raise ValidationError(problems)
    if get(site_id) is None:
        with db.transaction():
            _insert(site_id, _origin(data["base_url"]), display_name, data)
        return site_id
    if not overwrite:
        raise SiteExists(f"site {site_id!r} already exists (pass overwrite=True to replace its draft)")
    save_draft(site_id, data)
    return site_id
