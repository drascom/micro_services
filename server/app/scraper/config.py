"""Site configuration: yaml load/save, versioning, and a filesystem registry.

A site config lives at ``configs/<site>.yaml`` (the ACTIVE version). Historical
versions are archived as ``configs/<site>.v<N>.yaml`` when a heal replaces the
active one, so nothing is ever lost and a rollback is a file copy.

The registry simply scans ``configs/`` — dropping in a new ``<site>.yaml`` (plus
an optional ``<site>.baseline.json``) registers a new site with zero code
changes.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import yaml

from .. import config as app_config

log = logging.getLogger("scraper.config")

CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "configs")
TOMBSTONE_PATH = os.path.join(app_config.DATA_DIR, "deleted_sites.json")   # sites deleted from the admin (deploy.sh reads it)
PROVIDER_DIR = os.path.join(CONFIG_DIR, "providers")   # data-driven video provider recipes (providers/recipes.py)
_PROVIDER_DIR_OF = CONFIG_DIR   # the CONFIG_DIR PROVIDER_DIR was derived from (tests re-point CONFIG_DIR at a temp dir)

_VERSION_RE = re.compile(r"\.v(\d+)\.yaml$")


_warned: set[tuple] = set()


def _warn_once(site_id: str, what: str, message: str) -> None:
    """A malformed config part is logged, not raised; once per (site, part, message) so per-request reads stay quiet."""
    key = (site_id, what, message)
    if key not in _warned:
        _warned.add(key)
        log.warning("site %s: %s: %s", site_id, what, message)


# --- regex lint: the "double backslash" mistake -------------------------------------------------------------------------
# In a SINGLE-quoted yaml scalar ``\\d`` is two real backslashes: the regex then looks for a literal "\" + "d" and matches no digit
# (the trdiziizle onboarding case: every episode row rejected). Only the exact pair + a class / special letter is flagged, so an
# intentional ``\\\\`` (four) stays untouched.

_DOUBLE_BACKSLASH = re.compile(r"(?<!\\)\\\\(?!\\)([dDwWsSbB.])")


def double_backslash_hint(regex: Any) -> Optional[str]:
    """``"\\d"`` (the first suspicious pair found in the regex string), or None when the regex does not look like the mistake."""
    if not isinstance(regex, str):
        return None
    found = _DOUBLE_BACKSLASH.search(regex)
    return found.group(0) if found else None


def _regex_values(node: Any, where: str):
    """``(where, regex string)`` of every ``regex`` / ``*_regex`` key of a yaml tree (a string or a list of strings), recursively:
    series_page, normalize.key, collection / list / detail fields, search, resolvers, blocked, ..."""
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{where}.{key}" if where else str(key)
            if isinstance(key, str) and (key == "regex" or key.endswith("_regex")):
                if isinstance(value, str):
                    yield path, value
                elif isinstance(value, list):
                    for index, item in enumerate(value):
                        if isinstance(item, str):
                            yield f"{path}[{index}]", item
                continue
            yield from _regex_values(value, path)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from _regex_values(item, f"{where}[{index}]")


def regex_lint(data: Any) -> list[str]:
    """Problems of the regexes of a site yaml that look like the double-backslash mistake (empty = none). Never raises.

    The onboarding sandbox turns these into config errors (a NEW site must not pass with a regex that cannot match); loading an
    existing site only LOGS them (``load_site``), so a hand-built / server-healed config is never broken by this check."""
    out: list[str] = []
    try:
        for where, text in _regex_values(data, ""):
            pair = double_backslash_hint(text)
            if pair:
                out.append(f"{where}: regex çift ters eğik çizgi içeriyor (`{pair}`): tek tırnaklı yaml'da `{pair[1:]}` yaz "
                           f"(çift tırnakta `{pair}`) / regex contains a double backslash (`{pair}`): write `{pair[1:]}` in a "
                           "single-quoted yaml scalar")
    except Exception:   # a lint must never take a site down
        log.debug("regex_lint failed", exc_info=True)
    return out


@dataclass
class SiteConfig:
    site_id: str
    data: dict[str, Any]
    path: str

    @property
    def version(self) -> int:
        return int(self.data.get("version", 1))

    @property
    def schema(self) -> str:
        return self.data.get("schema", "MovieItem")

    @property
    def base_url(self) -> str:
        return self.data.get("base_url", "")

    @property
    def list_url(self) -> str:
        return self.data.get("list_url", "")

    @property
    def fetch_mode(self) -> str:
        mode = self.data.get("fetch_mode", "http")
        if mode not in ("http", "browser"):
            raise ValueError(f"unsupported fetch_mode {mode!r}")
        return mode

    @property
    def row_selector(self) -> str:
        return self.data.get("list", {}).get("row_selector", "")

    @property
    def list_fields(self) -> dict[str, Any]:
        return self.data.get("list", {}).get("fields", {})

    @property
    def detail_fields(self) -> dict[str, Any]:
        return self.data.get("detail", {}).get("fields", {})

    @property
    def collections(self) -> list[dict[str, Any]]:
        """Home-screen list definitions (pure data). Empty when a site defines
        only the single ``list_url`` — the ingester then treats that as ``new``."""
        cols = self.data.get("collections") or []
        return [c for c in cols if isinstance(c, dict) and c.get("path")]

    @property
    def stream_resolver(self) -> dict[str, Any]:
        """Config-driven recipe to turn an embed/player URL into a real media URL
        (empty dict when the site defines no resolver)."""
        return self.data.get("stream_resolver", {}) or {}

    @property
    def resolvers(self) -> list[dict[str, Any]]:
        """Ordered generic resolver chain (yaml ``resolvers:``; see ``scraper/resolvers``). A malformed item is
        logged and skipped (never raised), so the list index a candidate carries is its position HERE."""
        raw = self.data.get("resolvers")
        if not raw:
            return []
        if not isinstance(raw, list):
            _warn_once(self.site_id, "resolvers", "must be a list, ignored")
            return []
        from . import resolvers as rtypes
        out: list[dict[str, Any]] = []
        for i, item in enumerate(raw):
            try:
                errors = rtypes.validate([item])
            except Exception as exc:  # a broken validator must not take the site down
                errors = [f"cannot validate ({type(exc).__name__}: {exc})"]
            if errors:
                for err in errors:
                    _warn_once(self.site_id, f"resolvers[{i}]", re.sub(r"^resolvers\[0\]:?\s*", "", err) + " (skipped)")
                continue
            out.append(item)
        return out

    @property
    def providers(self) -> Optional[list[str]]:
        """Allowed video provider names (yaml ``providers:``), in priority order; None = every provider
        (missing, empty or invalid)."""
        raw = self.data.get("providers")
        if raw is None:
            return None
        if not isinstance(raw, list):
            _warn_once(self.site_id, "providers", "must be a list of provider names, ignored")
            return None
        names = [x.strip() for x in raw if isinstance(x, str) and x.strip()]
        if len(names) != len(raw):
            _warn_once(self.site_id, "providers", "non-string or empty entries ignored")
        try:
            from .providers import registry
            known = {p.name for p in registry.PROVIDERS} | {p.name for p in getattr(self, "extra_providers", None) or ()}
        except Exception:
            known = set()
        for name in names:
            if known and name not in known:
                _warn_once(self.site_id, "providers", f"unknown provider {name!r} (known: {', '.join(sorted(known))})")
        return names or None

    @property
    def use_site_module(self) -> bool:
        """Keep the site's own ``site_extractors/<site>.py`` candidates next to the ``resolvers:`` list."""
        return self.data.get("use_site_module") is True

    @property
    def series_page(self) -> dict[str, Any]:
        """The yaml ``series_page:`` block (series-page selectors of the episode inventory; ``{}`` when absent).

        A site whose own module provides ``series_inventory`` (yabancidizi) keeps its block verbatim. For every other
        site the block is the spec of the generic engine (``scraper/series_generic.py``): an invalid block is
        logged once and skipped (``{}``), never raised."""
        raw = self.data.get("series_page")
        if not raw:
            return {}
        if not isinstance(raw, dict):
            _warn_once(self.site_id, "series_page", "must be a mapping, ignored")
            return {}
        from . import series_generic, site_extractors
        if site_extractors.has_inventory_module(self.site_id):
            return raw
        try:
            errors = series_generic.validate_spec(raw)
        except Exception as exc:  # a broken validator must not take the site down
            errors = [f"cannot validate ({type(exc).__name__}: {exc})"]
        for err in errors:
            _warn_once(self.site_id, "series_page", err + " (block skipped)")
        return {} if errors else raw

    @property
    def search(self) -> dict[str, Any]:
        """The yaml ``search:`` block (the site's live catalogue search endpoint, run by ``scraper/search_generic.py``
        when no adapter module is registered for the site), validated; ``{}`` when absent. An invalid block is logged
        once and skipped (``{}``), never raised."""
        raw = self.data.get("search")
        if not raw:
            return {}
        if not isinstance(raw, dict):
            _warn_once(self.site_id, "search", "must be a mapping, ignored")
            return {}
        from . import search_generic
        try:
            errors = search_generic.validate_spec(raw, self.base_url or None)
        except Exception as exc:  # a broken validator must not take the site down
            errors = [f"cannot validate ({type(exc).__name__}: {exc})"]
        for err in errors:
            _warn_once(self.site_id, "search", err + " (block skipped)")
        return {} if errors else raw

    @property
    def blocked(self) -> list[dict[str, Any]]:
        """The yaml ``blocked:`` rules (placeholders of content that is not public, ``scraper/blocked.py``), the usable ones only:
        a bad rule is logged once and skipped (never raised). ``[]`` when absent: the site then behaves as before."""
        raw = self.data.get("blocked")
        if not raw:
            return []
        from . import blocked
        try:
            return blocked.rules_of(raw, lambda part, message: _warn_once(self.site_id, part, message))
        except Exception as exc:  # a broken validator must not take the site down
            _warn_once(self.site_id, "blocked", f"cannot validate ({type(exc).__name__}: {exc})")
            return []

    @property
    def availability_gate(self) -> dict[str, Any]:
        """The yaml ``availability_gate:`` as ``{probe, require}`` (opt-in; ``{}`` when absent, ``probe: 0`` or invalid: the
        invalid block is logged once and ignored, never raised)."""
        raw = self.data.get("availability_gate")
        if raw is None:
            return {}
        from . import blocked
        try:
            return blocked.gate_of(raw, lambda part, message: _warn_once(self.site_id, part, message))
        except Exception as exc:
            _warn_once(self.site_id, "availability_gate", f"cannot validate ({type(exc).__name__}: {exc})")
            return {}

    def baseline(self) -> dict[str, Any]:
        p = os.path.join(CONFIG_DIR, f"{self.site_id}.baseline.json")
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as fh:
                return json.load(fh)
        return {}


def _active_path(site_id: str) -> str:
    return os.path.join(CONFIG_DIR, f"{site_id}.yaml")


def list_sites() -> list[str]:
    """All registered site ids (active ``<site>.yaml`` files in configs/)."""
    if not os.path.isdir(CONFIG_DIR):
        return []
    out = []
    for name in sorted(os.listdir(CONFIG_DIR)):
        # Skip hidden / macOS AppleDouble (._*) files so tar-deploy cruft
        # never registers as a phantom site.
        if name.startswith("."):
            continue
        if name.endswith(".yaml") and not _VERSION_RE.search(name):
            sid = name[: -len(".yaml")]
            if sid.startswith((".", "_")):
                continue
            out.append(sid)
    return out


def load_site(site_id: str) -> SiteConfig:
    path = _active_path(site_id)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"no config for site {site_id!r} at {path}")
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    for message in regex_lint(data):   # only a log line: an existing site is never broken by the lint
        _warn_once(site_id, "regex", message)
    return SiteConfig(site_id=site_id, data=data, path=path)


def registry() -> list[dict[str, Any]]:
    """Describe every registered site for dashboards/admin APIs."""
    out = []
    for sid in list_sites():
        try:
            cfg = load_site(sid)
            out.append(
                {
                    "site_id": sid,
                    "path": cfg.path,
                    "version": cfg.version,
                    "schema": cfg.schema,
                    "base_url": cfg.base_url,
                    "engine": "obscura" if cfg.fetch_mode == "browser" else "crawlee-http",
                    "fetch_mode": cfg.fetch_mode,
                }
            )
        except Exception as exc:  # pragma: no cover - defensive
            out.append({"site_id": sid, "error": str(exc)})
    return out


@contextlib.contextmanager
def _lock():
    """Cross-process lock for config writes (flock on a hidden file in CONFIG_DIR)."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(os.path.join(CONFIG_DIR, ".config.lock"), "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _atomic_dump(path: str, data: dict[str, Any]) -> None:
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


def archived_versions(site_id: str) -> list[int]:
    """Archived version numbers (``<site>.v<N>.yaml``), ascending."""
    if not os.path.isdir(CONFIG_DIR):
        return []
    pat = re.compile(rf"^{re.escape(site_id)}\.v(\d+)\.yaml$")
    return sorted(int(m.group(1)) for n in os.listdir(CONFIG_DIR) if (m := pat.match(n)))


def _yaml11_keys(data: dict[str, Any]) -> dict[str, Any]:
    """``data`` with the YAML 1.1 accident undone: a bare ``on:`` of a ``blocked:`` rule loads as the boolean ``True`` and would be
    written back as ``true:``; the rule keys are named ``on`` again (the dumper then quotes it: ``'on':``)."""
    rules = data.get("blocked")
    if isinstance(rules, list) and any(isinstance(r, dict) and True in r and "on" not in r for r in rules):
        data = dict(data)
        data["blocked"] = [{("on" if k is True else k): v for k, v in r.items()} if isinstance(r, dict) and True in r and "on" not in r else r
                           for r in rules]
    return data


def save_new_version(site_id: str, new_data: dict[str, Any]) -> int:
    """Archive the current active config and write ``new_data`` as active.

    Returns the new version number. The previous active file is copied to
    ``<site>.v<old_version>.yaml`` first. Atomic (tmp + os.replace) and locked.
    """
    new_data = _yaml11_keys(new_data)
    with _lock():
        active = _active_path(site_id)
        old_version = 0
        if os.path.isfile(active):
            with open(active, "r", encoding="utf-8") as fh:
                old = yaml.safe_load(fh) or {}
            old_version = int(old.get("version", 1))
            _atomic_dump(os.path.join(CONFIG_DIR, f"{site_id}.v{old_version}.yaml"), old)

        # Never reuse a number that an archive already holds (e.g. after a rollback).
        new_version = max([old_version, *archived_versions(site_id)]) + 1
        new_data = dict(new_data)
        new_data["version"] = new_version
        new_data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _atomic_dump(active, new_data)
    try:   # a (re)registered site is no longer "deleted": deploy.sh must stop removing its local files
        tombstone_clear(site_id)
    except Exception:
        log.warning("site %s: tombstone not cleared", site_id, exc_info=True)
    return new_version


def site_files(site_id: str) -> list[str]:
    """File names (in ``CONFIG_DIR``) that make up a site: ``<site>.yaml``, ``<site>.baseline.json`` and the archives
    ``<site>.v<N>.yaml``, sorted. Provider recipes (``providers/``) and the config lock are not part of a site."""
    if not _SITE_ID_RE.match(site_id or "") or not os.path.isdir(CONFIG_DIR):
        return []
    pat = re.compile(rf"^{re.escape(site_id)}\.(yaml|baseline\.json|v\d+\.yaml)$")
    return sorted(n for n in os.listdir(CONFIG_DIR) if pat.match(n))


def delete_site_files(site_id: str) -> list[str]:
    """Remove ``<site>.yaml``, ``<site>.baseline.json`` and every ``<site>.v<N>.yaml`` (the site is no longer registered);
    returns the names removed. Provider recipes stay. Locked like every config write; ``ValueError`` for a bad id."""
    if not _SITE_ID_RE.match(site_id or ""):
        raise ValueError(f"invalid site id {site_id!r}")
    removed: list[str] = []
    with _lock():
        for name in site_files(site_id):
            try:
                os.unlink(os.path.join(CONFIG_DIR, name))
                removed.append(name)
            except FileNotFoundError:
                pass
    return removed


# --- tombstones: sites deleted from the admin (``DATA_DIR/deleted_sites.json``) -------------------------------------
# ``{site: {"at": iso, "files": [names]}}``. deploy.sh reads the server's file so a site that was deleted THERE is also
# removed from the local mirror (and never pushed back); saving the site again (``save_new_version``) clears its entry.

@contextlib.contextmanager
def _tombstone_lock():
    directory = os.path.dirname(TOMBSTONE_PATH)
    os.makedirs(directory, exist_ok=True)
    with open(TOMBSTONE_PATH + ".lock", "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def tombstones() -> dict[str, dict[str, Any]]:
    """The deleted-sites record (``{}`` when absent or unreadable; entries that are not a mapping are dropped)."""
    try:
        with open(TOMBSTONE_PATH, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, dict)}


def _tombstones_write(data: dict[str, Any]) -> None:
    directory = os.path.dirname(TOMBSTONE_PATH)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".deleted_sites.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, TOMBSTONE_PATH)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def tombstone_add(site_id: str, files: list[str]) -> dict[str, Any]:
    """Record that ``site_id`` was deleted (``files`` = the config file names removed with it); returns the entry."""
    if not _SITE_ID_RE.match(site_id or ""):
        raise ValueError(f"invalid site id {site_id!r}")
    entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "files": sorted(str(f) for f in files)}
    with _tombstone_lock():
        data = tombstones()
        data[site_id] = entry
        _tombstones_write(data)
    return entry


def tombstone_clear(site_id: str) -> bool:
    """Forget the deletion of ``site_id`` (it was registered again); False when there was none. Never creates the file."""
    if not os.path.isfile(TOMBSTONE_PATH):
        return False
    with _tombstone_lock():
        data = tombstones()
        if site_id not in data:
            return False
        del data[site_id]
        _tombstones_write(data)
    return True


def rollback_config(site_id: str) -> int:
    """Make the newest archived version below the active one active again.

    The active (bad) config is archived as ``<site>.v<cur>.yaml`` first, so
    nothing is lost. Returns the restored version; raises ``ValueError`` when
    there is no earlier archive and ``FileNotFoundError`` for an unknown site.
    """
    with _lock():
        active = _active_path(site_id)
        if not os.path.isfile(active):
            raise FileNotFoundError(f"no config for site {site_id!r}")
        with open(active, "r", encoding="utf-8") as fh:
            cur = yaml.safe_load(fh) or {}
        cur_version = int(cur.get("version", 1))
        older = [v for v in archived_versions(site_id) if v < cur_version]
        if not older:
            raise ValueError("no previous version to roll back to")
        target = older[-1]
        with open(os.path.join(CONFIG_DIR, f"{site_id}.v{target}.yaml"), "r", encoding="utf-8") as fh:
            prev = yaml.safe_load(fh) or {}
        _atomic_dump(os.path.join(CONFIG_DIR, f"{site_id}.v{cur_version}.yaml"), cur)
        prev = dict(prev)
        prev["version"] = target
        _atomic_dump(active, prev)
        return target


def update_baseline(site_id: str, last_good: dict[str, Any]) -> None:
    """Record the latest known-good parse (``last_good``) in ``<site>.baseline.json``.

    Thresholds (min_items, ...) are kept; only ``last_good`` is refreshed.
    """
    path = os.path.join(CONFIG_DIR, f"{site_id}.baseline.json")
    with _lock():
        data: dict[str, Any] = {}
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        data["last_good"] = {**last_good, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        fd, tmp = tempfile.mkstemp(dir=CONFIG_DIR, prefix=".tmp-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


BASELINE_THRESHOLD_KEYS = ("min_items", "min_fill_ratio", "critical_field_fill")
_SITE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


def write_baseline_thresholds(site_id: str, thresholds: dict[str, Any]) -> None:
    """Write the drift thresholds (``min_items``, ``min_fill_ratio``, ``critical_field_fill``) of a NEW site's
    ``<site>.baseline.json`` (onboarding). Other keys already in the file (``last_good``, ``series_page``) are kept,
    unknown keys in ``thresholds`` are ignored. Atomic (tmp + os.replace) and locked like ``update_baseline``."""
    if not _SITE_ID_RE.match(site_id or ""):
        raise ValueError(f"invalid site id {site_id!r}")
    path = os.path.join(CONFIG_DIR, f"{site_id}.baseline.json")
    with _lock():
        data: dict[str, Any] = {}
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        data.update({k: thresholds[k] for k in BASELINE_THRESHOLD_KEYS if k in thresholds})
        series = thresholds.get("series_page")   # the series-page drift floor (``series_crawl``): {min_items: n}
        if isinstance(series, dict) and isinstance(series.get("min_items"), int) and not isinstance(series["min_items"], bool):
            data["series_page"] = {**(data["series_page"] if isinstance(data.get("series_page"), dict) else {}),
                                   "min_items": max(1, series["min_items"])}
        fd, tmp = tempfile.mkstemp(dir=CONFIG_DIR, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


# --- provider recipes (``configs/providers/<name>.yaml``, archive ``<name>.v<N>.yaml``) -----------------------------
RECIPE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


def provider_dir() -> str:
    """Directory of the provider recipes: ``PROVIDER_DIR``, or ``<CONFIG_DIR>/providers`` when CONFIG_DIR was re-pointed."""
    return PROVIDER_DIR if CONFIG_DIR == _PROVIDER_DIR_OF else os.path.join(CONFIG_DIR, "providers")


def _recipe_path(name: str, version: Optional[int] = None) -> str:
    if not RECIPE_NAME_RE.match(name or ""):
        raise ValueError(f"invalid provider recipe name {name!r}")
    return os.path.join(provider_dir(), f"{name}.yaml" if version is None else f"{name}.v{version}.yaml")


def recipe_names() -> list[str]:
    """Names of the ACTIVE recipes (``<name>.yaml``; archives, hidden and badly named files are skipped), sorted."""
    folder = provider_dir()
    if not os.path.isdir(folder):
        return []
    out = []
    for file in sorted(os.listdir(folder)):
        if file.endswith(".yaml") and not _VERSION_RE.search(file) and RECIPE_NAME_RE.match(file[: -len(".yaml")]):
            out.append(file[: -len(".yaml")])
    return out


def load_recipe(name: str) -> dict[str, Any]:
    """The yaml mapping of the active recipe ``name`` (FileNotFoundError when there is none)."""
    path = _recipe_path(name)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"no provider recipe {name!r} at {path}")
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"provider recipe {name!r} is not a mapping")
    return data


def list_recipes() -> list[dict[str, Any]]:
    """``[{name, version, path, data}]`` of every readable active recipe (a broken file is logged and left out)."""
    out = []
    for name in recipe_names():
        try:
            data = load_recipe(name)
        except Exception as exc:
            _warn_once("providers", name, f"unreadable recipe ({type(exc).__name__}: {exc})")
            continue
        out.append({"name": name, "version": int(data.get("version") or 1), "path": _recipe_path(name), "data": data})
    return out


def recipe_archived_versions(name: str) -> list[int]:
    """Archived version numbers of a recipe (``<name>.v<N>.yaml``), ascending."""
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


def save_recipe(name: str, data: dict[str, Any]) -> int:
    """Archive the current active recipe (as ``<name>.v<old>.yaml``) and write ``data`` as the active one, like
    :func:`save_new_version` does for a site. Returns the new version (1 for a new recipe). Atomic and locked."""
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


def recipe_snapshot(name: str) -> dict[str, Any]:
    """What ``restore_recipe`` needs to undo a ``save_recipe`` of ``name``: ``{data (the active mapping or None), archives (versions)}``."""
    try:
        data: Optional[dict[str, Any]] = load_recipe(name)
    except (FileNotFoundError, ValueError):
        data = None
    return {"data": data, "archives": recipe_archived_versions(name)}


def restore_recipe(name: str, snapshot: dict[str, Any]) -> None:
    """Put recipe ``name`` back to ``snapshot`` (:func:`recipe_snapshot`): the active file as it was (or removed when there was none)
    and the archives written since gone. Undo of an onboarding save that failed after a recipe update."""
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
    """Remove a recipe with all its archives (undo of a failed onboarding save; a recipe in use is never removed by hand)."""
    with _recipe_lock():
        for path in (_recipe_path(name), *(_recipe_path(name, v) for v in recipe_archived_versions(name))):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
