"""Home screen of the tv-v1 boot: the hero carousel, the rows, and the score that ranks "trending".

Layout (top to bottom): hero carousel (the sites' ``featured_<site>`` hero sections first, in site order, then filled
by score up to ``HERO_SERIES`` + ``HERO_MOVIES``, series/movies alternating; fixed on top) ->
the rows in the admin's order (``home_categories``), by default series and movie rows alternating: ``continue`` ->
``new_episodes`` (series) -> ``latest_movies`` (movies) -> ``trending_series`` -> ``trending_movies`` -> ``must_watch``
(mixed) -> the admin's category rows ``cat_<slug>`` (``library/categories.py``) -> ``series`` -> ``movies`` (``mylist``
is no row: the clients open "Listem" as its own page, ``/api/row/mylist`` still answers);
the skeleton rows can only be moved, categories can be put anywhere between them (see ``config.HOME_LAYOUT``;
:func:`layout` is the one place that decides the row list, per profile). A ``cat_<slug>`` row = the ``category_<slug>_*``
lists of all sites merged round-robin (playable only per ``HOME_ONLY_READY``); hidden while the category is off or has
fewer playable titles than its ``min_items``.

Classification: every canonical library item (all sites merged) belongs to its ``type`` (series | movie). The rows are
type pools: ``series`` / ``movies`` = ALL titles of the type, newest added first; ``latest_movies`` = the sites'
``latest_movies_<site>`` lists (movies, round-robin); ``trending_<type>`` = the type's titles of the sites'
``spotlight_<site>`` + ``trending_<site>`` lists (each site's spotlight then trending list, round-robin over sites),
filled up to ``TREND_TARGET`` with the best :func:`trend_score` titles. ``must_watch`` = the sites'
``noteworthy_series`` + ``noteworthy_movies`` lists round-robin, followed by the CLASSICS (:func:`is_classic`). The old
row ids ``spotlight``, ``noteworthy_series``, ``noteworthy_movies`` are no home rows any more but still answer
``/api/row/<id>``. The role lists are also SCORING SIGNALS (:class:`Signals`; ``featured`` also leads the slider,
:func:`heroes`); classics score nothing. With
``config.HOME_ONLY_READY`` (default) rows and hero list only playable titles (``availability.state == "ready"``);
search and catalogue are not affected. A classic is never picked by score for the trending fill or the slider (the
"İzlenmesi Gereken" row is its place; only a site's own trending list puts one up there). ``continue`` is the
profile's own list and is shown as it is.

Everything is a pure function of (snapshot, profile id, progress map, clock) plus reads of ``library_lists``;
missing fields count as 0, nothing here raises for odd data.
"""
from __future__ import annotations

import datetime
import math
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from . import config, db, rows
from .library import categories, live

# ---- rows ---------------------------------------------------------------------------------------------------------
# the skeleton rows (ids = ``categories.SYSTEM_DEFAULT``) and their titles
TITLES = {
    "continue": "İzlemeye Devam Et",
    "new_episodes": "Yeni Bölümler",
    "latest_movies": "Yeni Filmler",
    "trending_series": "Trend Diziler",
    "trending_movies": "Trend Filmler",
    "must_watch": "İzlenmesi Gereken Dizi ve Filmler",
    "series": "Tüm Diziler",
    "movies": "Tüm Filmler",
}
# old home rows: no skeleton rows any more, still fetchable via `/api/row/<id>` (titles in `rows.ROW_TITLES`)
LEGACY_IDS = ("spotlight", "noteworthy_series", "noteworthy_movies")
# `mylist` ("Listem") is no home row either: the clients open it as its own page; `/api/row/mylist` still answers
# ids `rows.row_pool` hands to :func:`pool`
POOL_IDS = ("latest_movies", "trending_series", "trending_movies", "must_watch", "series", "movies") + LEGACY_IDS

# ---- sections (the menu of the home, client/: `/api/sections`, `/api/section/<id>`) ----------------------------------
# (id, title) in menu order; a "soon" section has no source yet (no rows, no heroes) but is listed
SECTIONS = (("featured", "Öne Çıkanlar"), ("series", "Diziler"), ("movies", "Filmler"), ("live_tv", "Canlı TV"),
            ("anime", "Animeler"), ("dvr", "DVR"))
SOON_SECTIONS = ("dvr",)
LIVE_SECTION = "live_tv"   # Canlı TV: rows / heroes are channels (library/live.py), not catalogue titles
# rows of a section (``featured`` = the home rows of :func:`layout` minus ``continue``: the new home shows "Devam Et" on
# the menu screen itself, from ``/api/row/continue``)
SECTION_ROWS = {"series": ("featured_series", "new_episodes", "trending_series", "series"),
                "movies": ("featured_movies", "latest_movies", "trending_movies", "movies"),
                "anime": ("anime",)}
# rows only the sections use (fetchable via `/api/row/<id>` too); NOT skeleton rows, so not in TITLES
SECTION_ROW_TITLES = {"featured_series": "Öne Çıkan Diziler", "featured_movies": "Öne Çıkan Filmler", "anime": "Animeler"}
SECTION_HEROES = 6      # heroes per section (the vitrin of a menu entry)
ANIME_CATEGORY = "animeler"        # the admin category of the sites' anime lists (diziasya ``category_animeler_*``)
ANIME_URL_LIKE = "%/anime/%"       # a series whose source page lives under /anime/ (diziasya ``kind: anime``)
FILLED_CATEGORIES = (ANIME_CATEGORY,)   # category rows topped up to TREND_TARGET from the library (best rated first)
FEATURED_SKIP = ("continue", "series", "movies")   # home rows the `featured` section leaves out (the boot keeps them)
POOL_IDS += tuple(SECTION_ROW_TITLES)
ROW_ITEMS = 20          # cards per row in the boot (`/api/row/<id>` pages through the rest)
TREND_TARGET = 20       # a trending row is filled with scored titles up to this many

# ---- trend_score constants (points; the maximum is 160) ---------------------------------------------------------------
TREND_TOP = 50          # a title on a site's trending list: TREND_TOP - position (0-based), best site wins ...
TREND_MIN = 5           # ... but never less than this (listed = ahead of every unlisted title)
ROLE_POINTS = 20        # per other role list the title is on (latest_episodes/_series/_movies, noteworthy_movies, featured)
ROLE_CAP = 40           # ... at most this many in total
SCORE_ROLES = ("latest_episodes", "latest_series", "latest_movies", "noteworthy_movies", "noteworthy_series", "spotlight", "featured")
ADDED_WINDOWS = ((14, 15), (45, 8))   # (days since `added_at`, points): the first window that fits counts
YEAR_POINTS = 10        # release year >= this year - 1
EPISODE_DAYS, EPISODE_POINTS = 14, 15   # series whose newest published episode aired within this many days
RATING_FLOOR, RATING_PER_POINT, RATING_CAP = 6.0, 5.0, 20.0   # (rating - 6) * 5, at most 20
FOLLOWERS_CAP, FOLLOWERS_FULL = 10.0, 10000   # log-scaled: FOLLOWERS_FULL followers earn the cap
FRESH_PARTS = ("added", "year", "episode")    # the time-based parts (left out by `include_fresh=False`)
DAY = 86400.0

TAGLINES = {"trending_series": "Haftanın Dizisi", "trending_movie": "Haftanın Filmi", "new": "Yeni", "popular": "Popüler"}


def _num(value: Any) -> float:
    """Float of a field, 0.0 when it is missing, not a number or NaN."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return out if out == out else 0.0


def is_ready(item: dict) -> bool:
    return (item.get("availability") or {}).get("state", "ready") == "ready"


def is_classic(item: dict, now: float) -> bool:
    """A classic: a movie rated ``config.CLASSIC_MIN_RATING`` or more, released ``config.CLASSIC_MIN_AGE_YEARS`` or more
    years before the current year; an item without a year (or rating) is no classic."""
    year = int(_num(item.get("year")))
    return (item.get("type") == "movie" and _num(item.get("rating")) >= config.CLASSIC_MIN_RATING
            and 0 < year <= datetime.date.fromtimestamp(now).year - config.CLASSIC_MIN_AGE_YEARS)


# ---- signals + score ----------------------------------------------------------------------------------------------------
@dataclass
class Signals:
    """What the sites say about popularity, read once per request from the ``library_lists`` of the snapshot."""
    trending: dict = field(default_factory=dict)         # canonical id -> best (lowest) 0-based position on a trending list
    roles: dict = field(default_factory=dict)            # canonical id -> set of SCORE_ROLES it is listed under
    trending_lists: list = field(default_factory=list)   # each site's trending list as items, in the site's order


def signals(snap) -> Signals:
    """Signals of a snapshot (empty for a non-library snapshot or a library without role lists)."""
    lists = list(rows._role_by_list(snap, "trending").values())
    best: dict[str, int] = {}
    for items in lists:
        for pos, item in enumerate(items):
            best[item["id"]] = min(pos, best.get(item["id"], pos))
    roles: dict[str, set] = {}
    for role in SCORE_ROLES:
        for items in rows._role_by_list(snap, role).values():
            for item in items:
                roles.setdefault(item["id"], set()).add(role)
    return Signals(best, roles, lists)


def _episode_points(item: dict, today: datetime.date) -> float:
    if item.get("type") != "series":
        return 0.0
    try:
        found = rows._newest_episode(item, today.isoformat())
        if not found:
            return 0.0
        days = (today - datetime.date.fromisoformat((found[1].get("air_date") or "")[:10])).days
    except (TypeError, ValueError, KeyError, AttributeError, IndexError):
        return 0.0
    return float(EPISODE_POINTS) if 0 <= days <= EPISODE_DAYS else 0.0


def score_parts(item: dict, sig: Optional[Signals], now: float) -> dict[str, float]:
    """The components of :func:`trend_score` (all >= 0; a missing field contributes 0):

    * ``trending`` - on a site's trending list: ``TREND_TOP - position`` (>= ``TREND_MIN``), best list wins
    * ``roles`` - ``ROLE_POINTS`` per other role list (latest_*, noteworthy_movies, featured), at most ``ROLE_CAP``
    * ``added`` - ``added_at`` within 14 days +15, within 45 days +8 (a future/clock-skewed stamp counts as new)
    * ``year`` - release year >= this year - 1: +10
    * ``episode`` - series whose newest published episode aired within the last 14 days: +15
    * ``rating`` - ``(rating - 6) * 5`` for rating >= 6, at most 20
    * ``followers`` - log10 scaled, ``FOLLOWERS_CAP`` at ``FOLLOWERS_FULL`` followers
    """
    sig = sig or Signals()
    iid = item.get("id")
    parts = dict.fromkeys(("trending", "roles") + FRESH_PARTS + ("rating", "followers"), 0.0)
    pos = sig.trending.get(iid)
    if pos is not None:
        parts["trending"] = float(max(TREND_MIN, TREND_TOP - pos))
    parts["roles"] = float(min(ROLE_CAP, ROLE_POINTS * len(sig.roles.get(iid) or ())))
    added = _num(item.get("added_at"))
    if added > 0:
        age = (now - added) / DAY
        parts["added"] = next((float(points) for days, points in ADDED_WINDOWS if age <= days), 0.0)
    year = int(_num(item.get("year")))
    today = datetime.date.fromtimestamp(now)
    if year > 0 and year >= today.year - 1:
        parts["year"] = float(YEAR_POINTS)
    parts["episode"] = _episode_points(item, today)
    rating = _num(item.get("rating"))
    if rating >= RATING_FLOOR:
        parts["rating"] = min(RATING_CAP, (rating - RATING_FLOOR) * RATING_PER_POINT)
    followers = _num(item.get("followers"))
    if followers > 0:
        parts["followers"] = min(FOLLOWERS_CAP, FOLLOWERS_CAP * math.log10(1 + followers) / math.log10(1 + FOLLOWERS_FULL))
    return parts


def trend_score(item: dict, sig: Optional[Signals], now: Optional[float] = None, include_fresh: bool = True) -> float:
    """Deterministic popularity/recency score of a library item (higher = more "trending"); see :func:`score_parts`.
    ``include_fresh=False`` drops the time-based parts (added / year / episode): the "popular" ranking."""
    parts = score_parts(item, sig, time.time() if now is None else now)
    return sum(v for k, v in parts.items() if include_fresh or k not in FRESH_PARTS)


# ---- layout ---------------------------------------------------------------------------------------------------------------
def skeleton_order() -> list[str]:
    """The skeleton rows in the order the home had before the admin could move them: ``config.HOME_LAYOUT`` (known
    skeleton ids only), any missing one in its default place."""
    out = [r for r in config.HOME_LAYOUT if r in TITLES]
    return list(dict.fromkeys(out + [r for r in categories.SYSTEM_DEFAULT if r not in out]))


def seed_skeleton() -> bool:
    """Make sure the skeleton rows exist in ``home_categories`` (idempotent, keeps the admin's order)."""
    return categories.ensure_system(TITLES, skeleton_order())


def _fixed_layout() -> list[str]:
    """``config.HOME_LAYOUT`` (repeats dropped) with the enabled category rows in front of ``series`` - the layout when
    the table cannot be read."""
    out: list[str] = []
    for row_id in config.HOME_LAYOUT:
        if row_id not in out:
            out.append(row_id)
    cats = [categories.row_id(s) for s in categories.enabled_slugs()]
    if cats:   # the admin's category rows: before "Tüm Diziler"
        at = next((out.index(r) for r in ("series", "movies") if r in out), len(out))
        out[at:at] = cats
    return out


def layout(profile_id: str = "") -> list[str]:
    """Row ids of the home screen, top to bottom, for ``profile_id`` (the single place that decides the row list).

    The order is the admin's (``home_categories``: skeleton rows + category rows ``cat_<slug>``, disabled categories
    left out; a skeleton row only while ``config.HOME_LAYOUT`` names it). Skeleton rows keep their own content rules (``continue``/``mylist`` show only when filled, ...), see
    :meth:`Context._build`. Extra ids of ``config.HOME_LAYOUT`` (e.g. ``genre_<slug>``) stay after the row that precedes
    them there. Table empty/unreadable: the fixed layout (:func:`_fixed_layout`, as before the skeleton was movable).
    Genre rows chosen from the profile's watch habits plug in HERE later; nothing else has to change."""
    try:
        seed_skeleton()
        table = categories.ordered_rows()
    except Exception:   # noqa: BLE001 - the home must never fail because of the table
        return _fixed_layout()
    if not any(r["kind"] == "system" for r in table):
        return _fixed_layout()
    out: list[str] = []
    for r in table:
        if r["kind"] == "system":
            if r["slug"] in config.HOME_LAYOUT:   # the HOME_LAYOUT env can still switch a skeleton row off
                out.append(r["slug"])
        elif r["enabled"]:
            out.append(categories.row_id(r["slug"]))
    prev = None
    for row_id in config.HOME_LAYOUT:   # rows the table does not know (genre_*): keep them behind their predecessor
        if row_id in out:
            prev = row_id
        elif row_id not in TITLES:
            out.insert(out.index(prev) + 1 if prev in out else 0, row_id)
            prev = row_id
    return list(dict.fromkeys(out))


class Context:
    """One request's view of the home: snapshot, profile, progress map and clock; signals and pools are computed once."""

    def __init__(self, snap, profile_id: str = "", pmap: Optional[dict] = None, now: Optional[float] = None) -> None:
        self.snap = snap
        self.profile_id = profile_id or ""
        self.pmap = pmap if pmap is not None else {}
        self.now = time.time() if now is None else float(now)
        self._signals: Optional[Signals] = None
        self._saved: Optional[list[str]] = None
        self._pools: dict[str, list[dict]] = {}

    @property
    def signals(self) -> Signals:
        if self._signals is None:
            self._signals = signals(self.snap)
        return self._signals

    @property
    def saved(self) -> list[str]:
        if self._saved is None:
            self._saved = rows.mylist_ids(self.profile_id)
        return self._saved

    def show(self, item: dict) -> bool:
        """May the home screen list this title (``HOME_ONLY_READY``: only playable ones)."""
        return not config.HOME_ONLY_READY or is_ready(item)

    def pool(self, row_id: str) -> list[dict]:
        """The full (unpaged) item list behind a row id; ``[]`` for an unknown id."""
        if row_id not in self._pools:
            self._pools[row_id] = self._build(row_id)
        return self._pools[row_id]

    def _build(self, row_id: str) -> list[dict]:
        if row_id in ("series", "movies"):
            kind = "series" if row_id == "series" else "movie"
            return sorted((i for i in self.snap.items if i["type"] == kind and self.show(i)),
                          key=lambda i: (-(i.get("added_at") or 0), i["id"]))
        if row_id in ("trending_series", "trending_movies"):
            return self._trending("series" if row_id == "trending_series" else "movie")
        if row_id == "latest_movies":
            return [i for i in rows._role_lists(self.snap, "latest_movies", kind="movie") if self.show(i)]
        if row_id == "must_watch":
            return self._must_watch()
        if row_id in ("featured_series", "featured_movies"):   # the sites' hero sections, one type
            kind = "series" if row_id == "featured_series" else "movie"
            return [i for i in rows._role_lists(self.snap, "featured", kind=kind) if self.show(i)]
        if row_id == "anime":
            return self._anime()
        if row_id == "noteworthy_movies":
            return self._noteworthy()
        if row_id in ("noteworthy_series", "spotlight"):   # the sites' own section lists (round-robin over sites, list order)
            kind = "series" if row_id == "noteworthy_series" else None
            return [i for i in rows._role_lists(self.snap, row_id, kind=kind) if self.show(i)]
        slug = categories.slug_of_row(row_id)
        if slug:
            return self._category(slug)
        if row_id == "mylist":
            return [self.snap.by_id[i] for i in self.saved if i in self.snap.by_id]
        return rows.row_pool(self.snap, row_id, self.profile_id, self.pmap)   # continue, genre_*, the old row ids

    def _site_lists(self, *roles: str) -> list[list[dict]]:
        """Per site: the site's ``<role>_<site>`` lists of ``roles`` concatenated in that order (sites in list-id order)."""
        per_site: dict[str, list[dict]] = {}
        for role in roles:
            for list_id, items in rows._role_by_list(self.snap, role).items():
                per_site.setdefault(list_id[len(role) + 1:], []).extend(items)
        return [per_site[k] for k in sorted(per_site)]

    def _trending(self, kind: str) -> list[dict]:
        """The sites' spotlight + trending lists (this type only; per site spotlight first, then trending; round-robin
        over the sites, no repeats), then - while shorter than ``TREND_TARGET`` - the best-scoring remaining titles of
        the type (score 0 is never added)."""
        pool = rows._round_robin([[i for i in items if i["type"] == kind and self.show(i)]
                                  for items in self._site_lists("spotlight", "trending")])
        if len(pool) < TREND_TARGET:
            taken = {i["id"] for i in pool}
            scored = []
            for item in self.snap.items:
                if item["type"] == kind and item["id"] not in taken and self.show(item) and not is_classic(item, self.now):
                    score = trend_score(item, self.signals, self.now)
                    if score > 0:
                        scored.append((-score, -_num(item.get("rating")), item["id"], item))
            scored.sort(key=lambda t: t[:3])
            pool += [t[3] for t in scored[:TREND_TARGET - len(pool)]]
        return pool

    def _category(self, slug: str) -> list[dict]:
        """A category row: the ``category_<slug>_*`` lists of every site (round-robin, no repeats, list order), playable
        only per ``HOME_ONLY_READY``. ``[]`` (row hidden) when the category is gone or off, or has fewer playable titles
        than its ``min_items``. A ``FILLED_CATEGORIES`` row (animeler) shorter than ``TREND_TARGET`` is topped up with
        the library's other anime series (:meth:`_anime_extra`) best rated first (ties: id); ``min_items`` counts the
        filled row."""
        cat = categories.get(slug)
        if not cat or not cat["enabled"]:
            return []
        pool = [i for i in rows._round_robin(categories.member_lists(self.snap, slug)) if self.show(i)]
        if slug in FILLED_CATEGORIES and len(pool) < TREND_TARGET:
            more = [i for i in self._anime_extra({i["id"] for i in pool}) if self.show(i)]
            more.sort(key=lambda i: (-_num(i.get("rating")), i["id"]))
            pool += more[:TREND_TARGET - len(pool)]
        return pool if sum(1 for i in pool if is_ready(i)) >= cat["min_items"] else []

    def _anime(self) -> list[dict]:
        """``anime`` ("Animeler"): the sites' ``category_animeler_*`` lists (round-robin, list order; whether the admin
        switched the category row on or not), then every other series whose source page is an anime page (source URL
        under ``/anime/``), newest added first; playable per ``HOME_ONLY_READY``, no repeats. The library keeps no anime
        flag of its own: the source URL is the only mark (diziasya); other sites add nothing here."""
        pool = rows._round_robin(categories.member_lists(self.snap, ANIME_CATEGORY))
        more = self._anime_extra({i["id"] for i in pool})
        more.sort(key=lambda i: (-(i.get("added_at") or 0), i["id"]))
        return [i for i in pool + more if self.show(i)]

    def _anime_extra(self, taken: set) -> list[dict]:
        """Library series (not in ``taken``) with a source page under ``/anime/``, unordered; ``[]`` off the library."""
        if getattr(self.snap, "source", "") != "library":
            return []
        ids = {r["canonical_id"] for r in db.query(
            "SELECT DISTINCT canonical_id FROM source_items WHERE source_url LIKE ?", (ANIME_URL_LIKE,))}
        return [self.snap.by_id[i] for i in ids
                if i in self.snap.by_id and i not in taken and self.snap.by_id[i]["type"] == "series"]

    def _must_watch(self) -> list[dict]:
        """``must_watch`` ("İzlenmesi Gereken Dizi ve Filmler"): the sites' ``noteworthy_series`` + ``noteworthy_movies``
        lists (each its own type, all lists round-robin, list order, playable per ``HOME_ONLY_READY``), then the classics
        as in :meth:`_noteworthy`; no repeats."""
        lists = [[i for i in items if i["type"] == kind and self.show(i)]
                 for role, kind in (("noteworthy_series", "series"), ("noteworthy_movies", "movie"))
                 for items in rows._role_by_list(self.snap, role).values()]
        pool = rows._round_robin(_alternate(lists))
        taken = {i["id"] for i in pool}
        classics = [i for i in self.snap.items
                    if i["id"] not in taken and self.show(i) and is_classic(i, self.now)]
        classics.sort(key=lambda i: (-_num(i.get("rating")), i["id"]))
        return pool + classics

    def _noteworthy(self) -> list[dict]:
        """The sites' ``noteworthy_movies`` lists (movies, round-robin over the sites, list order), then the classics
        (:func:`is_classic`, playable per ``HOME_ONLY_READY``) best rated first (ties: id); no repeats."""
        pool = [i for i in rows._role_lists(self.snap, "noteworthy_movies", kind="movie") if self.show(i)]
        taken = {i["id"] for i in pool}
        classics = [i for i in self.snap.items
                    if i["id"] not in taken and self.show(i) and is_classic(i, self.now)]
        classics.sort(key=lambda i: (-_num(i.get("rating")), i["id"]))
        return pool + classics


def _alternate(lists: list[list[dict]]) -> list[list[dict]]:
    """Order the given lists so a round-robin merge alternates the halves (series lists, movie lists): s1, m1, s2, m2."""
    series = [l for l in lists if l and l[0]["type"] == "series"]
    movies = [l for l in lists if l and l[0]["type"] != "series"]
    out = []
    for k in range(max(len(series), len(movies))):
        out.extend(a[k] for a in (series, movies) if k < len(a))
    return out


def pool(snap, row_id: str, profile_id: str = "", pmap: Optional[dict] = None, now: Optional[float] = None) -> list[dict]:
    """Pool of a home row id (what ``rows.row_pool`` delegates to for ``series``, ``movies``, ``trending_*``)."""
    return Context(snap, profile_id, pmap, now).pool(row_id)


def title(snap, row_id: str) -> str:
    return TITLES.get(row_id) or SECTION_ROW_TITLES.get(row_id) or rows.row_title(snap, row_id)


# ---- hero -----------------------------------------------------------------------------------------------------------------
def _interleave(series: list, movies: list) -> list:
    out = []
    for k in range(max(len(series), len(movies))):
        out.extend(a[k] for a in (series, movies) if k < len(a))
    return out


def hero_tagline(item: dict, parts: dict[str, float]) -> str:
    if parts["trending"] > 0:
        return TAGLINES["trending_series" if item.get("type") == "series" else "trending_movie"]
    return TAGLINES["new"] if sum(parts[k] for k in FRESH_PARTS) > 0 else TAGLINES["popular"]


def heroes(ctx: Context) -> list[dict]:
    """The slider: first the sites' own hero sections (the ``featured_<site>`` lists, round-robin over the sites, each
    site's order kept, no type alternation), then - while shorter than ``HERO_SERIES + HERO_MOVIES`` - the best-scoring
    series and movies (ties: rating, then id), shown series, movie, series, ...; a type with too few candidates is topped
    up from the other. Candidates must have a backdrop and (``HOME_ONLY_READY``) be playable; no title twice."""
    def ok(item: dict) -> bool:
        return bool(item.get("backdrop_url")) and ctx.show(item)

    total = config.HERO_SERIES + config.HERO_MOVIES
    picked = rows._round_robin([[i for i in items if ok(i)]
                                for items in rows._role_by_list(ctx.snap, "featured").values()])[:max(total, 0)]
    picked = [(i, score_parts(i, ctx.signals, ctx.now)) for i in picked]
    taken = {i["id"] for i, _p in picked}
    ranked = []
    for item in ctx.snap.items:
        if item["id"] not in taken and ok(item):
            parts = score_parts(item, ctx.signals, ctx.now)
            if parts["trending"] <= 0 and is_classic(item, ctx.now):
                continue   # a classic is never picked by score alone (only a site's trending list puts it up here)
            ranked.append((-sum(parts.values()), -_num(item.get("rating")), item["id"], item, parts))
    ranked.sort(key=lambda t: t[:3])
    series = [t for t in ranked if t[3]["type"] == "series"]
    movies = [t for t in ranked if t[3]["type"] != "series"]
    if picked:   # fill the rest of the slider by score, both types together
        want = total - len(picked)
        want_s = min(len(series), max(want - want // 2, 0))
        want_m = want - want_s
    else:
        want_s, want_m = config.HERO_SERIES, config.HERO_MOVIES
    n_s, n_m = min(len(series), want_s), min(len(movies), want_m)
    spare = want_s + want_m - n_s - n_m
    n_s += min(len(series) - n_s, spare)
    spare = want_s + want_m - n_s - n_m
    n_m += min(len(movies) - n_m, spare)
    picked += [(t[3], t[4]) for t in _interleave(series[:n_s], movies[:n_m])]
    return [_hero_card(ctx, item, parts) for item, parts in picked]


def _hero_card(ctx: Context, item: dict, parts: Optional[dict] = None) -> dict:
    """A slider card: the item card + ``logo_text``, ``tagline``, ``in_mylist``."""
    parts = parts if parts is not None else score_parts(item, ctx.signals, ctx.now)
    card = rows.item_json(item, ctx.pmap.get(item["id"]))
    card.update(logo_text=item["title"], tagline=hero_tagline(item, parts), in_mylist=item["id"] in set(ctx.saved))
    return card


def compose(snap, profile_id: str, pmap: dict, now: Optional[float] = None) -> dict:
    """``{"heroes": [...], "rows": [...]}`` of the tv-v1 home for a profile: rows in :func:`layout` order, each
    ``{id, title, loaded: true, items (first ROW_ITEMS cards), total, offset: 0}``; rows with an empty pool are left out."""
    ctx = Context(snap, profile_id, pmap, now)
    return {"heroes": heroes(ctx), "rows": _rows_json(ctx, layout(ctx.profile_id))}


def _rows_json(ctx: Context, row_ids) -> list[dict]:
    """Boot-style rows (``{id, title, loaded, items, total, offset}``) of ``row_ids``; empty pools left out."""
    out = []
    for row_id in row_ids:
        items = ctx.pool(row_id)
        if items:
            out.append({"id": row_id, "title": title(ctx.snap, row_id), "loaded": True,
                        "items": rows.items_json(items, ctx.pmap, ROW_ITEMS), "total": len(items), "offset": 0})
    return out


# ---- sections -------------------------------------------------------------------------------------------------------------
def section_rows(section_id: str, profile_id: str = "") -> list[str]:
    """Row ids of a section (the one place that decides them); ``featured`` = :func:`layout` without ``FEATURED_SKIP``
    (``continue``, and "Tüm Diziler"/"Tüm Filmler": those live in the Diziler/Filmler sections)."""
    if section_id == "featured":
        return [r for r in layout(profile_id) if r not in FEATURED_SKIP]
    return list(SECTION_ROWS.get(section_id, ()))


VITRIN_KINDS = ("series", "movie", "anime")   # the Öne Çıkanlar vitrin cycles these: dizi, film, anime, dizi, ...
VITRIN_SECTIONS = (("series", "series"), ("movies", "movie"), ("anime", "anime"))   # (section, kind), decided in order


def _vitrins(ctx: Context) -> dict[str, list[dict]]:
    """The vitrin (heroes) of every ready section, decided together so they do not repeat each other.

    A title's kind is ``anime`` when it is in the anime pool (``ctx.pool("anime")``), else its type. Per kind the
    candidates are (backdrop + ``ctx.show`` only): ``featured`` = the sites' ``featured_<site>`` lists of that kind
    (round-robin over the sites, each site's order kept), ``new`` = the sites' latest lists (series: ``latest_series``
    + ``latest_episodes``; movie: ``latest_movies``; anime: the anime pool), ``scored`` = the best-scoring titles
    (:func:`score_parts`; ties: rating, then id; a classic only with a trending score, as in :func:`heroes`).

    * ``featured`` (Öne Çıkanlar): dizi, film, anime, dizi, ... each from that kind's featured, then scored candidates;
      a kind with no candidate left is skipped (the others fill); no title twice; at most ``SECTION_HEROES``.
    * ``series`` / ``movies`` / ``anime`` (in that order): the kind's featured, new, scored candidates minus every title
      already in the Öne Çıkanlar vitrin or an earlier section's vitrin; short of ``SECTION_HEROES`` the skipped ones
      come back (a repeat rather than an empty vitrin); Diziler takes anime series only when it has no series at all."""
    anime_ids = {i["id"] for i in ctx.pool("anime")}
    parts_of: dict[str, dict] = {}

    def ok(item: dict) -> bool:
        return bool(item.get("backdrop_url")) and ctx.show(item)

    def kind_of(item: dict) -> str:
        return "anime" if item["id"] in anime_ids else ("series" if item["type"] == "series" else "movie")

    def of_kind(items, kind: str) -> list[dict]:
        return [i for i in items if ok(i) and kind_of(i) == kind]

    site_featured = list(rows._role_by_list(ctx.snap, "featured").values())
    featured = {k: rows._round_robin([of_kind(items, k) for items in site_featured]) for k in VITRIN_KINDS}
    new = {"series": rows._round_robin([of_kind(items, "series") for items in ctx._site_lists("latest_series", "latest_episodes")]),
           "movie": of_kind(ctx.pool("latest_movies"), "movie"),
           "anime": of_kind(ctx.pool("anime"), "anime")}
    ranked = []
    for item in ctx.snap.items:
        if ok(item):
            parts = score_parts(item, ctx.signals, ctx.now)
            parts_of[item["id"]] = parts
            if parts["trending"] <= 0 and is_classic(item, ctx.now):
                continue
            ranked.append((-sum(parts.values()), -_num(item.get("rating")), item["id"], item))
    ranked.sort(key=lambda t: t[:3])
    scored = {k: [t[3] for t in ranked if kind_of(t[3]) == k] for k in VITRIN_KINDS}

    def card(item: dict) -> dict:
        return _hero_card(ctx, item, parts_of.get(item["id"]))

    used: set = set()
    queues = {k: featured[k] + scored[k] for k in VITRIN_KINDS}
    pos = dict.fromkeys(VITRIN_KINDS, 0)
    top: list[dict] = []
    while len(top) < SECTION_HEROES:
        moved = False
        for kind in VITRIN_KINDS:
            if len(top) >= SECTION_HEROES:
                break
            queue = queues[kind]
            while pos[kind] < len(queue) and queue[pos[kind]]["id"] in used:
                pos[kind] += 1
            if pos[kind] < len(queue):
                used.add(queue[pos[kind]]["id"])
                top.append(queue[pos[kind]])
                moved = True
        if not moved:
            break
    out = {"featured": [card(i) for i in top]}
    for sid, kind in VITRIN_SECTIONS:
        own = list({i["id"]: i for i in featured[kind] + new[kind] + scored[kind]}.values())
        tiers = [[i for i in own if i["id"] not in used], [i for i in own if i["id"] in used]]
        if kind == "series" and not own:   # anime series are series too, but they belong to the Animeler vitrin
            tiers.append([i for i in featured["anime"] + new["anime"] + scored["anime"]])
        picked, have = [], set()
        for tier in tiers:
            for item in tier:
                if len(picked) >= SECTION_HEROES:
                    break
                if item["id"] not in have:
                    have.add(item["id"])
                    picked.append(item)
        used |= have
        out[sid] = [card(i) for i in picked]
    return out


def _section_heroes(ctx: Context, section_id: str, vitrins: Optional[dict] = None) -> list[dict]:
    """The vitrin of a section (:func:`_vitrins`; Canlı TV: a few channel cards); ``[]`` for a "soon" or unknown section."""
    if section_id in SOON_SECTIONS or section_id not in dict(SECTIONS):
        return []
    if section_id == LIVE_SECTION:
        return live.heroes()
    vitrins = _vitrins(ctx) if vitrins is None else vitrins
    return vitrins.get(section_id, [])


def sections(snap, profile_id: str, pmap: dict, now: Optional[float] = None) -> dict:
    """``{"sections": [{id, title, status: ready|soon, heroes}]}``: the menu of the new home, in menu order."""
    ctx = Context(snap, profile_id, pmap, now)
    vitrins = _vitrins(ctx)
    return {"sections": [{"id": sid, "title": stitle, "status": "soon" if sid in SOON_SECTIONS else "ready",
                          "heroes": _section_heroes(ctx, sid, vitrins)} for sid, stitle in SECTIONS]}


def section(snap, section_id: str, profile_id: str, pmap: dict, now: Optional[float] = None) -> Optional[dict]:
    """``{id, title, status, heroes, rows}`` of one section (rows as in the boot); ``None`` for an unknown id."""
    names = dict(SECTIONS)
    if section_id not in names:
        return None
    ctx = Context(snap, profile_id, pmap, now)
    rows_out = live.rows_json(ROW_ITEMS) if section_id == LIVE_SECTION else _rows_json(ctx, section_rows(section_id, ctx.profile_id))
    return {"id": section_id, "title": names[section_id], "status": "soon" if section_id in SOON_SECTIONS else "ready",
            "heroes": _section_heroes(ctx, section_id), "rows": rows_out}
