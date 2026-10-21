"""Series page directory: from a card that links to an EPISODE page to the series' own page.

Why: a "latest episodes" card (``Halef 37.Bölüm``) is a ``type: series`` record whose ``source_url`` is the EPISODE page; the
episode inventory (``series_crawl``) can only read a SERIES page (yaml ``series_page.series_url_regex``), so such a record
could never be crawled ("sayfa dizi sayfası değil", the same error every few hours, the budget gone). The site's full list pages
(``list_url``, an alphabetical archive, any collection) already name every series page: this module builds a DIRECTORY out of
those rows (clean title + slug -> series page URL; only rows whose URL matches ``series_url_regex``) and rewrites the record's
``source_url`` to the series page. No request is made: the directory is built from rows the scan has fetched anyway (and from the
series pages the library already knows: ``source_items`` of the site), kept in memory per site.

Match order (first step with exactly ONE distinct series page wins; several = ambiguous, the next step is tried):
``title`` (equal folded clean title) -> ``slug`` (equal clean slug of the record key / URL) -> ``prefix`` (one slug is the other
followed by ``-...``: ``halef`` / ``halef-koklerin-cagrisi``). No match: the record stays, marked ``_series_page_unknown`` (the URL it
had); the crawl skips it without an error until its ``source_url`` changes.

Applies only to a module-less site whose yaml ``series_page`` carries ``series_url_regex`` (the generic engine); sites with a code
module (yabancidizi) already link series pages and are untouched.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

from .. import config, db
from ..scraper import series_generic, site_extractors
from .normalize import _site_names, clean_series_slug, clean_title, title_key

UNKNOWN = "_series_page_unknown"       # normalized marker: {"url": <the source_url that is no series page>, "at": ts}
FROM = "_series_page_from"             # normalized note of a resolved record: {"url": <the card's episode URL>, "how": title|slug|prefix}
DB_TTL = 300.0                         # seconds the database part of a site's directory is kept before it is read again


def spec_of(cfg, site_id: Optional[str] = None) -> Optional[dict]:
    """The ``series_page`` block when the directory applies to ``cfg`` (generic engine + ``series_url_regex``), else None.
    ``site_id`` = the id the site's code modules are looked up by when it is not ``cfg.site_id`` (an onboarding draft)."""
    spec = getattr(cfg, "series_page", None)
    if not (isinstance(spec, dict) and spec.get("series_url_regex") and series_generic.is_generic_spec(spec)):
        return None
    return None if site_extractors.has_inventory_module(site_id or cfg.site_id) else spec


def _path(url: str) -> str:
    try:
        return urlparse(url).path or "/"
    except ValueError:
        return "/"


def is_series_page(spec: dict, url: str) -> bool:
    try:
        return bool(re.search(spec["series_url_regex"], _path(url)))
    except re.error:
        return False


def page_url(cfg, norm: dict) -> str:
    """The page the inventory would be read from for ``norm`` (``series_crawl.series_url``): its ``source_url``, else its key."""
    return urljoin(cfg.base_url.rstrip("/") + "/", str(norm.get("source_url") or norm.get("source_key") or ""))


def _canon(url: str) -> str:
    return url.rstrip("/")


def _tail(text: str) -> str:
    parts = [p for p in str(text or "").split("/") if p]
    return parts[-1] if parts else ""


def _slugs(spec: dict, key: Any, url: str) -> set[str]:
    """Clean slugs a record / series page goes by: its key's last segment and its URL's slug (``series_slug_regex``, else the last
    path segment), each without episode / release tails."""
    out = {clean_series_slug(_tail(str(key or "")).lower())}
    slug = None
    if spec.get("series_slug_regex"):
        try:
            found = re.search(spec["series_slug_regex"], _path(url))
        except re.error:
            found = None
        if found:
            slug = (found.group("slug") if "slug" in found.re.groupindex else found.group(1)) or None
    out.add(clean_series_slug((slug or _tail(_path(url))).lower()))
    return {s for s in out if s}


def _prefixes(a: str, b: str) -> bool:
    return a != b and (b.startswith(a + "-") or a.startswith(b + "-"))


class Directory:
    """Series pages of one site: ``[{url, key, title, tkey, slugs}]`` (one entry per distinct page URL)."""

    def __init__(self, cfg, site_id: Optional[str] = None) -> None:
        self.spec = spec_of(cfg, site_id) or {}
        self.entries: list[dict] = []
        self._urls: set[str] = set()

    def __len__(self) -> int:
        return len(self.entries)

    def add(self, norm: dict, base_url: str = "") -> bool:
        """Take a normalized record that IS a series page (type series, ``source_url`` matches ``series_url_regex``)."""
        if not self.spec or not isinstance(norm, dict) or norm.get("type") != "series":
            return False
        url = urljoin(base_url.rstrip("/") + "/", str(norm.get("source_url") or "")) if base_url else str(norm.get("source_url") or "")
        if not url or not is_series_page(self.spec, url) or _canon(url) in self._urls:
            return False
        self._urls.add(_canon(url))
        # the title of a row written before the engine cleaned titles ("Halef: Köklerin Çağrısı HD") is cleaned the same way (idempotent)
        title = clean_title(norm.get("title"), series=True, site_names=_site_names(base_url, None)) if base_url else str(norm.get("title") or "")
        self.entries.append({"url": url, "key": norm.get("source_key"), "title": title,
                             "tkey": title_key(title), "slugs": _slugs(self.spec, norm.get("source_key"), url)})
        return True

    def merge(self, other: "Directory") -> None:
        for entry in other.entries:
            if _canon(entry["url"]) not in self._urls:
                self._urls.add(_canon(entry["url"]))
                self.entries.append(entry)

    def resolve(self, norm: dict) -> Optional[tuple[str, str]]:
        """``(series page URL, how)`` for a record that links an episode page, None when no step has exactly one candidate.
        ``how`` = ``title`` | ``slug`` | ``prefix``."""
        hit = self.lookup(norm)
        return (hit[0]["url"], hit[1]) if hit else None

    def lookup(self, norm: dict) -> Optional[tuple[dict, str]]:
        """``resolve`` with the whole directory entry (``{url, key, title, ...}``) instead of its URL."""
        if not self.entries:
            return None
        tkey = title_key(norm.get("title"))
        slugs = _slugs(self.spec, norm.get("source_key"), str(norm.get("source_url") or ""))
        steps = (
            ("title", lambda e: bool(tkey) and e["tkey"] == tkey),
            ("slug", lambda e: bool(slugs & e["slugs"])),
            ("prefix", lambda e: any(_prefixes(a, b) for a in slugs for b in e["slugs"])),
        )
        for how, fits in steps:
            found = {_canon(e["url"]): e for e in self.entries if fits(e)}
            if len(found) == 1:
                return next(iter(found.values())), how
        return None


def build(cfg, norms, site_id: Optional[str] = None) -> Directory:
    """A directory out of normalized records (``norms``: any iterable of ``normalize`` results)."""
    directory = Directory(cfg, site_id)
    for norm in norms:
        directory.add(norm, cfg.base_url)
    return directory


# --- per-site memory + the library's own series pages -----------------------------------------------------------------

_lock = threading.Lock()
_remembered: dict[tuple, Directory] = {}          # (db path, site) -> series pages seen by scans of this process
_from_db: dict[tuple, tuple[float, Directory]] = {}   # (db path, site) -> (loaded at, series pages of source_items)


def reset() -> None:
    """Forget every directory (tests)."""
    with _lock:
        _remembered.clear()
        _from_db.clear()


def remember(cfg, directory: Directory) -> None:
    """Keep the series pages a scan found (so a live-search hit / an on-demand read after it resolves without a request)."""
    if not directory.spec:
        return
    with _lock:
        slot = _remembered.setdefault((config.DB_PATH, cfg.site_id), Directory(cfg))
        slot.merge(directory)


def _load_db(cfg) -> Directory:
    directory = Directory(cfg)
    try:
        rows = db.query("SELECT source_key, source_url, json_extract(normalized,'$.title') AS title, "
                        "json_extract(normalized,'$.type') AS type FROM source_items WHERE source=?", (cfg.site_id,))
    except Exception:
        return directory
    for row in rows:
        directory.add({"source_key": row["source_key"], "source_url": row["source_url"], "title": row["title"], "type": row["type"]},
                      cfg.base_url)
    return directory


def for_site(cfg) -> Directory:
    """The directory of a site outside a scan (live search, on-demand read, the inventory pass): the pages scans remembered plus the
    series pages of the site's ``source_items`` (read from the database at most every ``DB_TTL`` s). No request."""
    key = (config.DB_PATH, cfg.site_id)
    now = time.monotonic()
    with _lock:
        cached = _from_db.get(key)
        remembered = _remembered.get(key)
    if cached is None or now - cached[0] > DB_TTL:
        loaded = _load_db(cfg)
        with _lock:
            _from_db[key] = (now, loaded)
    else:
        loaded = cached[1]
    merged = Directory(cfg)
    merged.merge(loaded)
    if remembered is not None:
        merged.merge(remembered)
    return merged


# --- applying it -------------------------------------------------------------------------------------------------------

def unknown_for(norm: dict) -> bool:
    """Is ``norm`` marked "no series page known" FOR ITS CURRENT ``source_url`` (a changed URL clears the mark)?"""
    mark = norm.get(UNKNOWN)
    return isinstance(mark, dict) and mark.get("url") == norm.get("source_url")


def resolvable(cfg, norm: dict, directory: Optional[Directory] = None) -> bool:
    """Would ``ensure`` find a series page for ``norm`` now (no write)?"""
    spec = spec_of(cfg)
    if spec is None or norm.get("type") != "series" or is_series_page(spec, page_url(cfg, norm)):
        return False
    return (directory or for_site(cfg)).resolve(norm) is not None


def ensure(cfg, norm: dict, directory: Optional[Directory] = None, now: Optional[float] = None,
           site_id: Optional[str] = None) -> str:
    """Make ``norm["source_url"]`` a series page (in place): ``ok`` (it already is one) | ``resolved`` (rewritten to the series page the
    directory names; ``_series_page_from`` keeps the card's URL, the match step and the series page's key) | ``unknown`` (no series page found: marked ``_series_page_unknown``) |
    ``n/a`` (not a series record, or the site has no ``series_url_regex``: nothing is done)."""
    spec = spec_of(cfg, site_id)
    if spec is None or not isinstance(norm, dict) or norm.get("type") != "series":
        return "n/a"
    url = page_url(cfg, norm)
    if is_series_page(spec, url):
        return "ok"
    hit = (directory if directory is not None else for_site(cfg)).lookup(norm)
    if hit:
        norm[FROM] = {"url": norm.get("source_url"), "how": hit[1], "key": hit[0]["key"]}
        norm["source_url"] = hit[0]["url"]
        norm.pop(UNKNOWN, None)
        return "resolved"
    mark_unknown(norm, now)
    return "unknown"


def mark_unknown(norm: dict, now: Optional[float] = None) -> None:
    """Mark ``norm`` "no series page known" for its current ``source_url`` (an existing mark for the same URL is left as it is)."""
    if not unknown_for(norm):
        norm[UNKNOWN] = {"url": norm.get("source_url"), "at": int(time.time() if now is None else now)}


def card_resolution(norm: dict) -> Optional[dict]:
    """What ``ensure`` recorded for a resolved record (``{url, how}``), None otherwise (admin / diagnostics)."""
    mark = norm.get(FROM)
    return mark if isinstance(mark, dict) else None
