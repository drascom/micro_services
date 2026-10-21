"""Response schemas = the fields the TV client (``tizen-client/js``) and a future Android client READ.

Pure Python, no ``app`` import: usable from tests and from ad-hoc checks of a live server's JSON.
A spec is a dict ``{field: type}`` where type is one of the codes below, ``lst(x)`` (list of x), ``opt(x)`` (x or null),
``enum(...)`` (one of the literals, ``None`` allowed by listing it) or a nested spec. Extra fields are always allowed
(additive evolution); a missing field, ``null`` where a value is required or a wrong JSON type is an error.
"""

PY = {"str": str, "int": int, "num": (int, float), "bool": bool, "dict": dict, "list": list}


def lst(x):
    return ("list", x)


def opt(x):
    return ("opt", x)


def enum(*values):
    return ("enum", values)


def problems(value, want, where="$"):
    """List of human readable schema violations of ``value`` against ``want`` (empty = conforms)."""
    if isinstance(want, tuple) and want[0] == "opt":
        return [] if value is None else problems(value, want[1], where)
    if isinstance(want, tuple) and want[0] == "enum":
        return [] if value in want[1] else ["%s: %r is not one of %r" % (where, value, want[1])]
    if isinstance(want, tuple) and want[0] == "list":
        if not isinstance(value, list):
            return ["%s: expected a list, got %s" % (where, type(value).__name__)]
        out = []
        for i, x in enumerate(value):
            out += problems(x, want[1], "%s[%d]" % (where, i))
        return out
    if isinstance(want, dict):
        if not isinstance(value, dict):
            return ["%s: expected an object, got %s" % (where, type(value).__name__)]
        out = []
        for key, sub in want.items():
            if key not in value:
                out.append("%s.%s: missing" % (where, key))
            else:
                out += problems(value[key], sub, "%s.%s" % (where, key))
        return out
    if value is None:
        return ["%s: null where %s is required" % (where, want)]
    if want in ("int", "num") and isinstance(value, bool):
        return ["%s: bool where %s is required" % (where, want)]
    return [] if isinstance(value, PY[want]) else ["%s: expected %s, got %s" % (where, want, type(value).__name__)]


# ---- shared shapes -----------------------------------------------------------------------------------------------
AVAILABILITY = {"state": enum("ready", "check_required", "unavailable"),
                "reason": enum(None, "no_video_source", "sources_unavailable"),
                "has_trailer": "bool"}
ITEM_PROGRESS = {"episode_id": "str", "position": "int", "duration": "int", "pct": "int"}

# a card (boot rows, /api/row, catalog, search, mylist, similar): what card.js / row.js / hero.js / catalog.js read
ITEM = {"id": "str", "type": enum("movie", "series"), "title": "str", "year": opt("int"),
        "card": "str", "portrait": "str", "backdrop": "str", "has_backdrop": "bool",
        "overview": "str", "genres": lst("str"), "rating": opt("num"), "badge": opt("str"),
        "progress": opt(ITEM_PROGRESS), "availability": AVAILABILITY,
        "playback": enum("video", "trailer", "unavailable"),
        "card_kind": enum("title", "episode"), "card_key": "str",
        "sources": lst("str"), "tmdb_id": opt("int"), "imdb_id": opt("str"),
        "country": opt("str"), "followers": opt("int")}

# card_kind == "episode" adds (home.js cardParams / row.js label / card.js data-focus-key)
EPISODE_CARD = {"episode_id": "str", "episode_label": "str", "season": "int", "episode": "int",
                "still_url": "str", "has_still": "bool",
                "primary_action": {"kind": enum("play_episode", "resume_episode"), "item_id": "str", "episode_id": "str"}}

LEGACY_HERO = dict(ITEM, logo_text="str", tagline="str")
HERO = dict(LEGACY_HERO, in_mylist="bool")             # the tv-v1 layout adds in_mylist
ROW = {"id": "str", "title": "str", "loaded": "bool", "items": lst(ITEM)}
BOOT_TV = {"hero": opt(HERO), "heroes": lst(HERO), "rows": lst(ROW), "catalog_total": "int", "layout": enum("tv-v1")}
LAZY_ROW = {"id": "str", "title": "str", "loaded": "bool"}          # legacy boot: loaded:false rows carry ``count``
BOOT_LEGACY = {"hero": opt(LEGACY_HERO), "rows": lst(LAZY_ROW), "source": "str", "sources": lst({"id": "str", "name": "str"})}
ROW_PAGE = {"id": "str", "title": "str", "items": lst(ITEM), "offset": "int", "limit": "int", "total": "int"}

EPISODE = {"id": "str", "season": "int", "episode": "int", "title": "str", "overview": "str", "runtime": "int",
           "still": "str", "still_url": "str", "has_still": "bool", "air_date": opt("str"),
           "progress": opt({"position": "int", "duration": "int", "pct": "int"}), "availability": AVAILABILITY}
SEASON = {"season": "int", "title": "str", "name": "str", "overview": "str", "air_date": opt("str"),
          "poster_url": "str", "has_poster": "bool", "episode_count": "int", "episodes": lst(EPISODE)}
ACTION = {"kind": enum("play_movie", "resume_movie", "play_episode", "resume_episode", "play_trailer"),
          "item_id": "str"}
DETAIL = dict(ITEM, cast=lst("str"), director=opt("str"), runtime="int", in_mylist="bool", seasons=lst(SEASON),
              similar=lst(ITEM), resume={"episode_id": "str", "position": "int"}, actions=lst(ACTION),
              hydrating="bool", source_names=lst({"id": "str", "name": "str"}))
DETAIL_TRAILER = {"state": "str"}  # availability.trailer (only with a trailer source): checked separately

# track fields (js/tracks.js): present on EVERY stream; sub_known / site_lang_hint / mirror_of were added later (additive),
# proxied = ``url`` is a signed /api/stream-proxy/<token> URL (the media host needs headers the player cannot send)
STREAM = {"url": "str", "type": enum("hls", "mp4", "embed"), "quality": "str", "label": "str",
          "attempt_token": "str", "kind": enum("movie", "episode", "trailer"), "source_id": "str", "source": "str",
          "variant_id": "str", "audio_lang": opt("str"), "sub_mode": enum("hard", "soft", "none"),
          "hard_lang": opt("str"), "sub_known": "bool", "site_lang_hint": opt("str"), "mirror_of": opt("str"),
          "proxied": "bool"}
# proxy_reason: only on a proxied stream (``proxied: true``), why the server proxies it; additive, so it is not part of STREAM
PROXY_REASON = enum("ua", "ip", "recipe", "learned", "env")
STREAMS = {"streams": lst(STREAM), "subtitles": lst("dict"), "resume_position": "int", "duration": "int"}

PROFILE = {"id": "str", "name": "str", "avatar_seed": "str", "avatar": "str", "is_kids": "bool"}
PROFILES = {"profiles": lst(PROFILE)}
AVATARS = {"avatars": lst({"seed": "str", "url": "str"})}
HEALTH = {"status": enum("ok"), "source": "str", "items": "int", "cache_age": "int"}
CATALOG = {"items": lst(ITEM), "total": "int", "offset": "int", "limit": "int",
           "genres": lst({"id": "str", "name": "str"}), "years": lst("int")}
# additive (multi-site search; the client does not read them yet): per card the sites that hold the title, and per
# searched site how its live search went (`error` / `skipped` appear only when set; `skipped` = breaker|unsupported|short_query)
SOURCE_OPTION = {"site": "str", "name": "str", "kind": enum("series", "movie"), "episodes": "int",
                 "status": enum("ok", "unknown", "broken")}
SEARCH_ITEM = dict(ITEM, source_options=lst(SOURCE_OPTION))
REMOTE_SITE = {"ok": "bool", "count": "int", "ms": "int"}
SEARCH = {"items": lst(SEARCH_ITEM), "total": "int", "remote": "bool", "remote_error": opt("str"),
          "remote_sites": "dict"}               # {site id: REMOTE_SITE}
MYLIST = {"items": lst(ITEM)}
MYLIST_MUTATION = {"ok": "bool", "in_mylist": "bool"}
CONTINUE_REMOVE = {"ok": "bool", "removed": "bool"}     # DELETE /api/continue/{item_id}
PROGRESS_ACK = {"ok": "bool", "watched": "bool", "next_episode": opt("str")}
ERROR = {"error": {"code": "str", "message": "str"}}

# source finder (library/sourcefinder.py): what a client READS to tell the user "searching for a source" / "source found"
FINDER_STATE = {"state": enum("searching", "not_found")}   # the optional ``finder`` field of /api/streams (only when nothing plays)
SOURCE_FINDER = {"state": enum("idle", "searching", "found", "not_found"),
                 "steps": lst({"name": "str", "ok": "bool", "ms": "int", "note": "str"}), "updated_at": "int"}
NOTIFICATION = {"id": "int", "kind": enum("source_found"), "canonical_id": "str", "episode_id": "str", "title": "str",
                "season": opt("int"), "episode": opt("int"), "site": "str", "method": enum("retry", "search", "heal"),
                "created_at": "int"}
NOTIFICATIONS = {"items": lst(NOTIFICATION), "last_id": "int"}
NOTIFICATIONS_READ = {"ok": "bool", "marked": "int"}
