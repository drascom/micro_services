"""The provider recipe library on disk: ``DATA_DIR/providers/<name>.yaml`` (archive ``<name>.v<N>.yaml``).

Seeds: on first use the generic host recipes of ``server2/seed/providers/*.yaml`` that are missing from the library are
copied in (never overwriting a recipe that is there). Versioning (``save_recipe`` / ``recipe_snapshot`` /
``restore_recipe``) as the old ``scraper/config.py`` had it.
"""
from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import re
import shutil
import tempfile
import threading
import time
from typing import Optional

import yaml

from .. import limits

log = logging.getLogger("play.recipes")

RECIPE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_VERSION_RE = re.compile(r"\.v(\d+)\.yaml$")
SEED_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))), "seed", "providers")

_seeded: set = set()
_seed_lock = threading.Lock()


def provider_dir() -> str:
    folder = limits.providers_dir()
    _seed(folder)
    return folder


def _seed(folder: str) -> None:
    """Copy the seed recipes missing from ``folder`` (once per folder and process)."""
    with _seed_lock:
        if folder in _seeded:
            return
        _seeded.add(folder)
        if not os.path.isdir(SEED_DIR):
            return
        try:
            os.makedirs(folder, exist_ok=True)
            for name in sorted(os.listdir(SEED_DIR)):
                stem = name[:-len(".yaml")] if name.endswith(".yaml") else ""
                if not stem or _VERSION_RE.search(name) or not RECIPE_NAME_RE.match(stem):
                    continue
                target = os.path.join(folder, name)
                if not os.path.exists(target):
                    shutil.copyfile(os.path.join(SEED_DIR, name), target)
        except OSError as exc:
            log.warning("provider seeds not copied: %s", exc)


def reset_seed_memo() -> None:
    """Tests: let the next ``provider_dir()`` copy the seeds again."""
    with _seed_lock:
        _seeded.clear()


def _atomic_dump(path: str, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-", suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _recipe_path(name: str, version: Optional[int] = None) -> str:
    if not RECIPE_NAME_RE.match(name or ""):
        raise ValueError(f"invalid provider recipe name {name!r}")
    return os.path.join(provider_dir(), f"{name}.yaml" if version is None else f"{name}.v{version}.yaml")


def recipe_names() -> list:
    """Names of the ACTIVE recipes (``<name>.yaml``; archives, hidden and badly named files are skipped), sorted."""
    folder = provider_dir()
    if not os.path.isdir(folder):
        return []
    return [f[:-len(".yaml")] for f in sorted(os.listdir(folder))
            if f.endswith(".yaml") and not _VERSION_RE.search(f) and RECIPE_NAME_RE.match(f[:-len(".yaml")])]


def load_recipe(name: str) -> dict:
    path = _recipe_path(name)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"no provider recipe {name!r}")
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"provider recipe {name!r} is not a mapping")
    return data


def recipe_archived_versions(name: str) -> list:
    folder = provider_dir()
    if not os.path.isdir(folder):
        return []
    pat = re.compile(rf"^{re.escape(name)}\.v(\d+)\.yaml$")
    return sorted(int(m.group(1)) for n in os.listdir(folder) if (m := pat.match(n)))


@contextlib.contextmanager
def _recipe_lock():
    folder = provider_dir()
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, ".recipes.lock"), "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def save_recipe(name: str, data: dict) -> int:
    """Archive the active recipe (``<name>.v<old>.yaml``) and write ``data`` as the new active one; returns the new version."""
    path = _recipe_path(name)
    with _recipe_lock():
        old_version = 0
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                old = yaml.safe_load(fh) or {}
            old_version = int(old.get("version", 1))
            _atomic_dump(_recipe_path(name, old_version), old)
        new_version = max([old_version, *recipe_archived_versions(name)]) + 1
        new_data = dict(data)
        new_data["name"] = name
        new_data["version"] = new_version
        new_data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _atomic_dump(path, new_data)
        return new_version


def recipe_snapshot(name: str) -> dict:
    """What :func:`restore_recipe` needs to undo a ``save_recipe``: ``{data (active mapping or None), archives}``."""
    try:
        data: Optional[dict] = load_recipe(name)
    except (FileNotFoundError, ValueError):
        data = None
    return {"data": data, "archives": recipe_archived_versions(name)}


def restore_recipe(name: str, snapshot: dict) -> None:
    old = snapshot.get("data")
    if old is None:
        delete_recipe(name)
        return
    with _recipe_lock():
        _atomic_dump(_recipe_path(name), old)
        for version in recipe_archived_versions(name):
            if version not in (snapshot.get("archives") or []):
                try:
                    os.unlink(_recipe_path(name, version))
                except FileNotFoundError:
                    pass


def delete_recipe(name: str) -> None:
    with _recipe_lock():
        for path in (_recipe_path(name), *(_recipe_path(name, v) for v in recipe_archived_versions(name))):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


def list_recipes() -> list:
    out = []
    for name in recipe_names():
        try:
            data = load_recipe(name)
        except Exception as exc:
            log.warning("provider recipe %s unreadable: %s", name, exc)
            continue
        out.append({"name": name, "version": int(data.get("version") or 1), "data": data})
    return out
