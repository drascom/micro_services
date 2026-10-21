"""Data-driven video provider recipes: the reusable provider LIBRARY next to the code modules (``vidmolly`` / ``okru``).

A recipe is a yaml file ``scraper/configs/providers/<name>.yaml`` (archive ``<name>.v<N>.yaml``) that says WHICH player URLs
it owns (``match``) and HOW to read the media out of the player page (``fetch`` / ``referer`` / ``follow`` / ``extract`` ...,
the same rules as a ``player_page`` resolver item). A site yaml only names it (``providers: [trdizi_player]``) and finds the
player URL with its ``resolvers:``; every other site that embeds the same player gets it for free. No code runs: the rules are
data, the engine is :func:`app.scraper.resolvers.player_page.resolve_player`.

    name: trdizi_player                # ^[a-z][a-z0-9_]{1,31}$, never a code provider's name
    description: "..."                 # 1-2 sentences (shown in the catalogue)
    version: 1
    match:
      host_regex: '(^|\\.)trdiziizle\\.tv$'   # re.search on the lower-case HOSTNAME (case-insensitive), required
      path_regex: '^/player/oynat/'          # optional, re.search on the URL path
    fetch: http                        # http (Chrome TLS fingerprint) | browser
    referer: "{page_url}"              # {page_url} = the page the player is embedded in, {base} = its origin
    headers: {}                        # optional extra request headers
    warm_session: false
    follow: []                         # as in player_page
    extract: [...]                     # as in player_page (regex | css | json_path, unpack, base64, quality_group ...)
    stream_headers: {}                 # request_headers of the streams
    cache_ttl: 3600                    # optional, 60..86400 s: how long the server may reuse the resolved streams
    stream_proxy: true                 # optional bool: /api/streams serves this recipe's streams (mp4 AND hls) through the
                                       # server's signed stream proxy (the media host is bound to the server's IP / session)
    verify: false
    label: "Trdizi"                    # optional provider name shown for the streams (default: the name)

``load_all()`` reads every active recipe (cached until a file's mtime / size changes; a broken file is logged once and
skipped); ``RecipeProvider`` has the shape the registry expects of a provider (``name``, ``matches(url)``,
``resolve(url, referer=)``, ``description``, ``hosts``).
"""
from __future__ import annotations

import copy
import logging
import os
import re
import threading
from typing import Any, Optional
from urllib.parse import urlparse

import yaml

from .. import config as scfg
from . import okru, vidmolly
# ``resolvers`` / ``resolvers.player_page`` are imported inside the functions: the resolver types import ``providers.trace``,
# which imports the registry, which imports this module (a top-level import here would be circular)

log = logging.getLogger("scraper.recipes")

NAME_RE = scfg.RECIPE_NAME_RE
CODE_NAMES = (vidmolly.NAME, okru.NAME)         # a recipe can never take a code provider's name
MAX_RECIPE_BYTES = 20_000
MAX_DESCRIPTION = 300
MAX_LABEL = 40
MAX_MATCH_REGEX = 200
#: top-level keys a recipe may carry (``updated_at`` is written by ``config.save_recipe``)
RECIPE_KEYS = frozenset({"name", "description", "version", "label", "match", "fetch", "referer", "headers", "warm_session",
                         "wait_for", "follow", "extract", "stream_headers", "verify", "cache_ttl", "stream_proxy", "updated_at"})
#: the keys that are parameters of the ``player_page`` engine
PLAYER_KEYS = ("fetch", "referer", "headers", "warm_session", "wait_for", "follow", "extract", "stream_headers", "verify",
               "cache_ttl")
MATCH_KEYS = frozenset({"host_regex", "path_regex"})
_SENTINEL_HOSTS = ("example.com", "vidmolly.to", "cdn.some-host.net", "localhost")   # a host_regex that owns all of them owns the web


# --- parsing / validation ---------------------------------------------------------------------------------------
class _NoAliasLoader(yaml.SafeLoader):
    """safe_load without aliases (a few kB of nested aliases can expand to gigabytes)."""

    def compose_node(self, parent, index):
        if self.check_event(yaml.events.AliasEvent):
            raise yaml.YAMLError("yaml aliases (*name) are not allowed")
        return super().compose_node(parent, index)


def parse(text: Any) -> tuple[Optional[dict], Optional[str]]:
    """``(recipe mapping, None)`` or ``(None, problem)`` of a recipe's yaml text (size capped, no aliases)."""
    if not isinstance(text, str) or not text.strip():
        return None, "recipe yaml is empty"
    if len(text.encode("utf-8", "replace")) > MAX_RECIPE_BYTES:
        return None, f"recipe yaml is longer than {MAX_RECIPE_BYTES} bytes"
    try:
        data = yaml.load(text, Loader=_NoAliasLoader)
    except yaml.YAMLError as exc:
        return None, "yaml: " + " ".join(str(exc).split())[:300]
    if not isinstance(data, dict):
        return None, "yaml: the document must be a mapping (a provider recipe)"
    return data, None


def _match_regex_error(key: str, pattern: Any) -> Optional[str]:
    if not isinstance(pattern, str) or not pattern.strip():
        return f"match.{key}: must be a non-empty regex string"
    if len(pattern) > MAX_MATCH_REGEX:
        return f"match.{key}: longer than {MAX_MATCH_REGEX} characters"
    from ..resolvers import player_page
    problem = player_page._bad_regex(pattern)
    return f"match.{key}: {problem}" if problem else None


def _validate_match(match: Any) -> list[str]:
    if not isinstance(match, dict):
        return ["match: must be a mapping with host_regex (and optionally path_regex)"]
    errors = [f"match: unknown key '{k}' (allowed: host_regex, path_regex)" for k in match if k not in MATCH_KEYS]
    host = match.get("host_regex")
    if host is None:
        errors.append("match.host_regex: required (a regex searched in the lower-case hostname, e.g. '(^|\\.)example\\.tv$')")
    else:
        problem = _match_regex_error("host_regex", host)
        if problem:
            errors.append(problem)
        elif re.search(r"://|https?\??:|:\d", host):
            errors.append("match.host_regex: is matched against the hostname only (no scheme, port or path)")
        elif re.search(host, "", re.I):
            errors.append("match.host_regex: matches every host (it also matches the empty hostname); anchor it to the player's domain")
        elif all(re.search(host, sample, re.I) for sample in _SENTINEL_HOSTS):
            errors.append("match.host_regex: matches every host; anchor it to the player's domain, e.g. '(^|\\.)example\\.tv$'")
    if match.get("path_regex") is not None:
        problem = _match_regex_error("path_regex", match["path_regex"])
        if problem:
            errors.append(problem)
    return errors


def validate_recipe(data: Any) -> list[str]:
    """Problems of a recipe mapping (empty = valid): name, description, version, ``match``, ``fetch`` and every ``player_page``
    rule (``extract`` / ``follow`` / ``headers`` / ``stream_headers`` ... with the engine's own validator and limits)."""
    if not isinstance(data, dict):
        return [f"recipe must be a mapping (got {type(data).__name__})"]
    errors = [f"unknown key '{k}' (allowed: {', '.join(sorted(RECIPE_KEYS))})" for k in data if k not in RECIPE_KEYS]
    name = data.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        errors.append("name: must match ^[a-z][a-z0-9_]{1,31}$")
    elif name in CODE_NAMES:
        errors.append(f"name: {name!r} is a code provider's name; choose another")
    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        errors.append("description: required (1-2 sentences: which player this recipe reads)")
    elif len(description) > MAX_DESCRIPTION:
        errors.append(f"description: longer than {MAX_DESCRIPTION} characters")
    version = data.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        errors.append("version: must be a positive integer")
    label = data.get("label")
    if label is not None and (not isinstance(label, str) or not label.strip() or len(label) > MAX_LABEL or "\n" in label):
        errors.append(f"label: must be one line of text, at most {MAX_LABEL} characters")
    errors += _validate_match(data.get("match"))
    if data.get("fetch") not in (None, "http", "browser"):
        errors.append("fetch: must be 'http' or 'browser'")
    if data.get("stream_proxy") is not None and not isinstance(data["stream_proxy"], bool):
        errors.append("stream_proxy: must be true or false")
    from .. import resolvers
    item = {"type": "player_page", "selector": "iframe[src]", **{k: data[k] for k in PLAYER_KEYS if data.get(k) is not None}}
    for problem in resolvers.validate([item]):
        errors.append(re.sub(r"^resolvers\[0\]:\s*", "", problem))
    return errors


# --- updating an existing recipe: only ``match.host_regex`` may widen --------------------------------------------------
# A draft may carry ``{name, yaml, mode: "update"}`` for a recipe that is already in the library (``scraper/provider_match.py`` writes
# the yaml: the same recipe with one more player host). The update is a NEW VERSION (``config.save_recipe``) and may change nothing but
# ``match.host_regex``, which gets exact-host alternatives appended: ``(?:<old>)|(?:^(?:www\\.)?<host>$)``. Everything else must be
# identical to the active recipe, so an update can never change how an existing host is read.
_HOST_NAME = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
_HOST_ALT_RE = r"\(\?:\^\(\?:www\\\.\)\?((?:[a-z0-9]|\\\.|\\-)+)\$\)"
_HOST_ALTS = re.compile(rf"(?:{_HOST_ALT_RE})(?:\|(?:{_HOST_ALT_RE}))*")
_HOST_ALT_ONE = re.compile(_HOST_ALT_RE)
_UPDATE_IGNORED = frozenset({"version", "updated_at", "match"})


def host_alternative(host: str) -> str:
    """The regex alternative that matches exactly ``host`` (and its ``www.`` form): ``(?:^(?:www\\.)?player\\.example\\.tv$)``."""
    return "(?:^(?:www\\.)?" + re.escape(host.lower().removeprefix("www.")) + "$)"


def widen_host_regex(old: str, hosts: list) -> str:
    """``old`` with an exact-host alternative per host appended (``(?:old)|(?:^...$)|...``)."""
    return f"(?:{old})|" + "|".join(host_alternative(h) for h in hosts)


def update_problems(active: Any, new: Any) -> list[str]:
    """Why ``new`` (a parsed recipe mapping) is not a valid UPDATE of the library recipe ``active`` (empty = valid): every key but
    ``version`` / ``updated_at`` / ``match`` equal, ``match.path_regex`` equal, ``match.host_regex`` = the old one + exact-host
    alternatives of public host names that the old regex did not already match."""
    if not isinstance(active, dict) or not isinstance(new, dict):
        return ["update: no recipe to compare with"]
    problems: list[str] = []
    for key in sorted((set(active) | set(new)) - _UPDATE_IGNORED):
        if active.get(key) != new.get(key):
            problems.append(f"{key}: an update may only widen match.host_regex, but {key} differs from the library recipe")
    old_match, new_match = active.get("match") or {}, new.get("match") or {}
    if not isinstance(new_match, dict) or set(new_match) - MATCH_KEYS:
        return problems + ["match: must be a mapping with host_regex (and the unchanged path_regex)"]
    if old_match.get("path_regex") != new_match.get("path_regex"):
        problems.append("match.path_regex: an update may not change it")
    old, host = str(old_match.get("host_regex") or ""), str(new_match.get("host_regex") or "")
    prefix = f"(?:{old})|"
    if host == old:
        problems.append("match.host_regex: unchanged (an update adds a host)")
    elif not host.startswith(prefix) or not _HOST_ALTS.fullmatch(host[len(prefix):]):
        problems.append("match.host_regex: an update must be the old regex plus exact-host alternatives, i.e. " + prefix
                        + host_alternative("player.example.tv") + " (use the recipe_yaml of match_providers as it is)")
    else:
        hosts = [m.replace("\\.", ".").replace("\\-", "-") for m in _HOST_ALT_ONE.findall(host[len(prefix):])]
        bad = [h for h in hosts if not _HOST_NAME.match(h) or re.fullmatch(r"[0-9.]+", h)]
        if bad:
            problems.append("match.host_regex: not a public host name: " + ", ".join(bad))
        elif old and all(re.search(old, h, re.I) for h in hosts):
            problems.append("match.host_regex: the recipe already matches " + ", ".join(hosts))
    return problems


# --- the provider -----------------------------------------------------------------------------------------------
class RecipeProvider:
    """One recipe as a registry provider. ``fetch_api`` is the transport (default: the ``app.scraper.fetch`` module; tests and
    the sandbox pass fakes)."""
    kind = "recipe"

    def __init__(self, data: dict, *, fetch_api: Any = None, path: str = "") -> None:
        errors = validate_recipe(data)
        if errors:
            raise ValueError("; ".join(errors[:3]))
        self.data = copy.deepcopy(data)
        self.path = path
        self.name: str = data["name"]
        self.description: str = data["description"].strip()
        self.version: int = int(data.get("version") or 1)
        self.label: str = (data.get("label") or self.name).strip()
        self.stream_proxy: bool = data.get("stream_proxy") is True   # the streams go through the server's proxy (not an engine parameter)
        match = data["match"]
        self.host_regex: str = match["host_regex"]
        self.path_regex: str = match.get("path_regex") or ""
        self._host = re.compile(self.host_regex, re.I)
        self._path = re.compile(self.path_regex) if self.path_regex else None
        self._fetch_api = fetch_api

    @property
    def hosts(self) -> tuple:
        return tuple(x for x in (self.host_regex, self.path_regex) if x)

    def matches(self, url: str) -> bool:
        try:
            parts = urlparse(url or "")
        except ValueError:
            return False
        host = (parts.hostname or "").lower()
        if parts.scheme not in ("http", "https") or not host or not self._host.search(host):
            return False
        return self._path is None or bool(self._path.search(parts.path or "/"))

    def params(self) -> dict:
        """The ``player_page`` parameters of this recipe (+ ``label`` = the provider name of the streams)."""
        out = {k: copy.deepcopy(self.data[k]) for k in PLAYER_KEYS if self.data.get(k) is not None}
        out["label"] = self.label
        return out

    def resolve(self, url: str, *, referer: str = "") -> Optional[dict]:
        """The registry stream ``{url, type, quality, duration, provider, streams}`` of the player URL ``url`` (embedded in the
        page ``referer``), or None."""
        from ..resolvers import player_page
        api = self._fetch_api
        if api is None:
            from .. import fetch as api
        result = player_page.resolve_player(url, self.params(), referer=referer or "", fetch_api=api)
        if result and self.stream_proxy:   # marker read by routers/streams.py (``streamproxy.proxy_reason``: "recipe"), never sent to a client
            result = {**result, "streams": [{**stream, "stream_proxy": True} for stream in result.get("streams") or []]}
        return result

    def catalog_entry(self) -> dict:
        return {"name": self.name, "description": self.description, "hosts": list(self.hosts), "kind": "recipe",
                "version": self.version, "fetch": self.data.get("fetch") or "http",
                **({"stream_proxy": True} if self.stream_proxy else {})}


def from_text(text: str, *, fetch_api: Any = None) -> tuple[Optional[RecipeProvider], list[str]]:
    """``(provider, [])`` of a recipe's yaml text, or ``(None, errors)``."""
    data, problem = parse(text)
    if problem:
        return None, [problem]
    errors = validate_recipe(data)
    if errors:
        return None, errors
    return RecipeProvider(data, fetch_api=fetch_api), []


# --- the library on disk (mtime cache) ---------------------------------------------------------------------------
_lock = threading.Lock()
_cache: dict[str, Any] = {"sig": None, "providers": []}
_loads = 0           # how many times the directory was (re)read (tests: the cache is only refreshed when a file changed)
_bad_logged: set = set()


def _signature(folder: str) -> tuple:
    names = scfg.recipe_names()
    sig = [folder]
    for name in names:
        try:
            st = os.stat(os.path.join(folder, f"{name}.yaml"))
        except OSError:
            continue
        sig.append((name, st.st_mtime_ns, st.st_size))
    return tuple(sig)


def load_count() -> int:
    return _loads


def load_all() -> list[RecipeProvider]:
    """Every valid active recipe, sorted by name. The directory is only read again when a recipe file was added, removed or
    changed; a broken file (unreadable, invalid) is logged once and skipped, it never takes the other providers down."""
    global _loads
    folder = scfg.provider_dir()
    sig = _signature(folder)
    with _lock:
        if _cache["sig"] == sig:
            return list(_cache["providers"])
        found: list[RecipeProvider] = []
        for name in scfg.recipe_names():
            path = os.path.join(folder, f"{name}.yaml")
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = yaml.safe_load(fh)
                if isinstance(data, dict) and data.get("name") in (None, ""):
                    data = {**data, "name": name}
                if isinstance(data, dict) and data.get("name") != name:
                    raise ValueError(f"name {data.get('name')!r} differs from the file name")
                found.append(RecipeProvider(data, path=path))
            except Exception as exc:
                key = (path, str(exc))
                if key not in _bad_logged:
                    _bad_logged.add(key)
                    log.warning("provider recipe %s skipped: %s: %s", name, type(exc).__name__, exc)
        _loads += 1
        _cache["sig"], _cache["providers"] = sig, found
        return list(found)
