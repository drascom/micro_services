"""Session cookie jar of ONE resolution / crawl operation (T32).

A site's own JavaScript often sets a cookie before its AJAX endpoints answer (e.g. a ``Date.now()`` timestamp); the server
sets session cookies (``Set-Cookie``) that later requests of the same visit must carry. ``Jar`` holds both, in memory only
(never written to disk): seeded from the map's cookie specs (``[{name, value}]``, ``value`` a literal or a generator:
``now_ms`` / ``now_s``, generated fresh when the jar is made), filled by the ``Set-Cookie`` answers of every hop, and sent
back only to the host (or a sub-domain of the domain) that owns each cookie. netguard / same-site rules are untouched:
the jar only adds a ``Cookie`` header to requests that are allowed anyway.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Iterable, Optional
from urllib.parse import urlsplit

GENERATORS = ("now_ms", "now_s")
MAX_COOKIES = 40
MAX_VALUE = 4000
_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}$")
SPEC_HELP = ("cookies: [{name, value}] — value a literal or a generator: now_ms (Date.now(), milliseconds) | now_s (seconds); "
             "generated fresh for every operation")


def generate(value: Any) -> str:
    """The value of one spec: ``now_ms`` / ``now_s`` = the current time, anything else literally."""
    text = str(value if value is not None else "")
    if text == "now_ms":
        return str(int(time.time() * 1000))
    if text == "now_s":
        return str(int(time.time()))
    return text


def check_specs(specs: Any, where: str = "cookies") -> list[str]:
    """Errors of a ``[{name, value}]`` cookie spec list (a ``{name: value}`` mapping is accepted too)."""
    if specs is None:
        return []
    items = list(as_specs(specs))
    if not isinstance(specs, (list, dict)) or (isinstance(specs, list) and len(items) != len(specs)):
        return [f"{where}: must be a list of {{name, value}} (value: a literal, now_ms or now_s)"]
    errors = []
    if len(items) > MAX_COOKIES:
        errors.append(f"{where}: at most {MAX_COOKIES} cookies")
    for i, (name, value) in enumerate(items):
        if not _NAME.match(name):
            errors.append(f"{where}[{i}].name: invalid cookie name {name!r}")
        if isinstance(value, bool) or not isinstance(value, (str, int)) or re.search(r"[\r\n;,]", str(value)) \
                or len(str(value)) > MAX_VALUE:
            errors.append(f"{where}[{i}].value: single-line text without ';' / ',' (or now_ms / now_s)")
    return errors


def as_specs(specs: Any) -> Iterable[tuple[str, Any]]:
    """``(name, value)`` pairs of a spec list ``[{name, value}]`` or a mapping ``{name: value}`` (bad items skipped)."""
    if isinstance(specs, dict):
        for name, value in specs.items():
            yield str(name), value
        return
    for item in specs or []:
        if isinstance(item, dict) and item.get("name"):
            yield str(item["name"]), item.get("value", "")


def _host(url: str) -> str:
    return (urlsplit(url or "").hostname or "").lower()


def _domain_ok(host: str, domain: str) -> bool:
    domain = domain.lstrip(".").lower()
    return bool(host) and (host == domain or host.endswith("." + domain))


def set_cookie_values(headers: Any) -> list[str]:
    """Every ``Set-Cookie`` value of a response header object (httpx / curl_cffi / plain dict)."""
    if headers is None:
        return []
    for getter in ("get_list", "getlist"):
        fn = getattr(headers, getter, None)
        if callable(fn):
            try:
                values = fn("set-cookie")
                if values:
                    return [str(v) for v in values]
            except Exception:
                pass
    multi = getattr(headers, "multi_items", None)
    if callable(multi):
        try:
            return [str(v) for k, v in multi() if str(k).lower() == "set-cookie"]
        except Exception:
            pass
    try:
        value = headers.get("set-cookie") or headers.get("Set-Cookie")
    except Exception:
        value = None
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [value] if value else []


class Jar:
    """In-memory cookie jar: ``{(domain, name): value}``. Thread-safe (the alternatives of a page resolve in parallel)."""

    def __init__(self, specs: Any = None, host: str = "") -> None:
        self._lock = threading.Lock()
        self._items: dict[tuple[str, str], str] = {}
        self.host = (host or "").lower()
        if specs:
            self.seed(specs, self.host)

    def seed(self, specs: Any, host: str = "", replace: bool = False) -> list[str]:
        """Add the spec cookies for ``host`` (generators evaluated now); an existing cookie of that name is kept unless
        ``replace``. Returns the names added."""
        host = (host or self.host).lower()
        added = []
        if not host:
            return added
        with self._lock:
            for name, value in as_specs(specs):
                if not _NAME.match(name):
                    continue
                if not replace and any(n == name and _domain_ok(host, d) for d, n in self._items):
                    continue
                self._items[(host, name)] = generate(value)[:MAX_VALUE]
                added.append(name)
        return added

    def set(self, name: str, value: Any, url_or_host: str) -> None:
        host = _host(url_or_host) if "://" in str(url_or_host) else str(url_or_host or "").lower()
        if host and _NAME.match(str(name)):
            with self._lock:
                self._items[(host, str(name))] = generate(value)[:MAX_VALUE]

    def absorb(self, url: str, values: Iterable[str]) -> list[str]:
        """Take the ``Set-Cookie`` answers of a response from ``url`` (a ``Domain=`` the host does not belong to is ignored;
        ``Max-Age=0`` / an empty value deletes). Returns the names stored."""
        host = _host(url)
        stored = []
        if not host:
            return stored
        for raw in values or []:
            parts = [p.strip() for p in str(raw).split(";")]
            if not parts or "=" not in parts[0]:
                continue
            name, value = (s.strip() for s in parts[0].split("=", 1))
            if not _NAME.match(name):
                continue
            domain, delete = host, value == ""
            for attr in parts[1:]:
                key, _, val = attr.partition("=")
                key = key.strip().lower()
                if key == "domain" and val.strip():
                    if _domain_ok(host, val.strip()):
                        domain = val.strip().lstrip(".").lower()
                    else:
                        domain = ""
                elif key == "max-age" and val.strip().lstrip("-").isdigit() and int(val.strip()) <= 0:
                    delete = True
            if not domain:
                continue
            with self._lock:
                if delete:
                    self._items.pop((domain, name), None)
                elif len(self._items) < MAX_COOKIES or (domain, name) in self._items:
                    self._items[(domain, name)] = value[:MAX_VALUE]
                    stored.append(name)
        return stored

    def header(self, url: str) -> str:
        """The ``Cookie`` header value for a request to ``url`` (``""`` when no cookie belongs to its host)."""
        host = _host(url)
        with self._lock:
            pairs = {name: value for (domain, name), value in self._items.items() if _domain_ok(host, domain)}
        return "; ".join(f"{k}={v}" for k, v in pairs.items())

    def names(self, url: Optional[str] = None) -> list[str]:
        host = _host(url) if url else ""
        with self._lock:
            return [n for (d, n) in self._items if not host or _domain_ok(host, d)]

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:   # an empty jar is still a jar (callers test ``is None``)
        return True


def apply(headers: Optional[dict], jar: Optional[Jar], url: str) -> dict:
    """``headers`` with the jar's ``Cookie`` for ``url`` (merged after an explicit Cookie header the caller gave)."""
    out = dict(headers or {})
    if jar is None:
        return out
    mine = jar.header(url)
    given = next((k for k in out if k.lower() == "cookie"), None)
    if given:
        own = {p.split("=", 1)[0].strip() for p in str(out[given]).split(";") if "=" in p}
        extra = "; ".join(p for p in mine.split("; ") if p and p.split("=", 1)[0] not in own)
        if extra:
            out[given] = f"{out[given]}; {extra}"
    elif mine:
        out["Cookie"] = mine
    return out
