"""Configuration for the diziflix server, sourced from the environment / .env."""
from __future__ import annotations

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_dotenv() -> None:
    path = os.path.join(BASE_DIR, ".env")
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


_load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _path(name: str, default_rel: str) -> str:
    value = os.environ.get(name) or default_rel
    return value if os.path.isabs(value) else os.path.join(BASE_DIR, value)


HOST = os.environ.get("HOST", "0.0.0.0")
PORT = _int("PORT", 8090)

SOURCE = os.environ.get("SOURCE", "mock")
FIXTURE_PATH = _path("FIXTURE_PATH", "data/fixture.json")
# Everything the server writes lives under DATA_DIR by default (db, image cache, ops settings,
# TMDB previews, scraper state). DB_PATH / IMG_CACHE_DIR can still be overridden one by one.
DATA_DIR = _path("DATA_DIR", "data")
DB_PATH = _path("DB_PATH", os.path.join(DATA_DIR, "diziflix.db"))
IMG_CACHE_DIR = _path("IMG_CACHE_DIR", os.path.join(DATA_DIR, "imgcache"))

# Seconds between background catalogue refreshes.
CACHE_TTL = _int("CACHE_TTL", 900)
# Mid-scan refreshes: while an ingest runs, the in-memory catalogue is rebuilt at its stages (titles written, series
# inventory per series, TMDB seasons) so the TV client sees what was found without waiting for the scan to end. This
# is the minimum number of seconds between two such refreshes (0 = no debounce); one at a time either way.
INGEST_REFRESH_MIN_INTERVAL = max(0.0, _float("INGEST_REFRESH_MIN_INTERVAL", 20.0))

IMG_QUALITY = _int("IMG_QUALITY", 82)
IMG_MAX_AGE = _int("IMG_MAX_AGE", 86400)

# Remote artwork fetching: per-request timeout, background prewarm after each
# catalogue refresh, and how long a failed URL is skipped.
IMG_FETCH_TIMEOUT = float(os.environ.get("IMG_FETCH_TIMEOUT") or 5)
IMG_PREWARM = (os.environ.get("IMG_PREWARM", "1") or "1").strip().lower() not in ("0", "false", "no", "off")
IMG_PREWARM_CONCURRENCY = _int("IMG_PREWARM_CONCURRENCY", 4)
# Episode stills are numerous (thousands): by default they are downloaded on demand by /img and
# cached on disk; IMG_PREWARM_STILLS=1 also prewarms them after every catalogue refresh.
IMG_PREWARM_STILLS = (os.environ.get("IMG_PREWARM_STILLS", "0") or "0").strip().lower() in ("1", "true", "yes", "on")
IMG_NEG_TTL = _int("IMG_NEG_TTL", 60)

ROW_LIMIT = _int("ROW_LIMIT", 20)

# Home screen of the tv-v1 boot (app/homelayout.py): rows top to bottom (comma-separated row ids; a row whose pool is
# empty is left out, so `mylist` only shows when it has titles), hero carousel size per type, and whether the home screen
# (rows + hero) lists only playable titles (availability.state == "ready"; search/catalogue still list everything).
HOME_LAYOUT = [r.strip() for r in (os.environ.get("HOME_LAYOUT")
               or "continue,trending_series,series,trending_movies,noteworthy_movies,movies,mylist").split(",") if r.strip()]
HERO_SERIES = max(0, _int("HERO_SERIES", 3))
HERO_MOVIES = max(0, _int("HERO_MOVIES", 3))
HOME_ONLY_READY = (os.environ.get("HOME_ONLY_READY", "1") or "1").strip().lower() not in ("0", "false", "no", "off")
# "Dikkate Değer Filmler" = the sites' noteworthy_movies lists + CLASSICS: playable movies rated >= CLASSIC_MIN_RATING and
# released at least CLASSIC_MIN_AGE_YEARS years ago (no year = not a classic).
CLASSIC_MIN_RATING = _float("CLASSIC_MIN_RATING", 7.5)
CLASSIC_MIN_AGE_YEARS = max(0, _int("CLASSIC_MIN_AGE_YEARS", 15))

# Remote artwork proxy: hosts the /img proxy may fetch real posters from
# (SSRF guard). Comma-separated; suffix match on the request host. images.remote_host_allowed() ADDS the host of
# every site config's base_url and its yaml ``image_hosts:`` list to this (env never shrinks them).
REMOTE_IMG_HOSTS = [
    h.strip().lower()
    for h in (os.environ.get("REMOTE_IMG_HOSTS") or "sinemalar.com,cdn.sinemalar.com,yabancidizi.news,image.tmdb.org").split(",")
    if h.strip()
]
# TMDB artwork is always proxy-able (the enrichment feature depends on it).
if "image.tmdb.org" not in REMOTE_IMG_HOSTS:
    REMOTE_IMG_HOSTS.append("image.tmdb.org")

# Subtitle proxy (app/subtitles.py, GET /api/subtitles/<id>.vtt): hosts a soft-subtitle source may live on (SSRF
# guard; also re-checked on every redirect). Built-in: VidMolly's subtitle host on any single-label TLD; SUBTITLE_HOSTS
# (comma-separated) ADDS to it. Entries: ``host`` (that host and its subdomains) or ``label.*`` (``srt.vidmoly.*``).
# Cache: converted WebVTT files under SUBTITLE_CACHE_DIR for SUBTITLE_CACHE_TTL seconds (a stale copy is still served
# when the source is down); download limits SUBTITLE_MAX_BYTES / SUBTITLE_TIMEOUT (total seconds).
SUBTITLE_HOSTS = ["srt.vidmoly.*", "srt.vidmolly.*"] + [
    h.strip().lower() for h in (os.environ.get("SUBTITLE_HOSTS") or "").split(",") if h.strip()
]
SUBTITLE_CACHE_DIR = _path("SUBTITLE_CACHE_DIR", os.path.join(DATA_DIR, "subcache"))
SUBTITLE_CACHE_TTL = max(0.0, _float("SUBTITLE_CACHE_TTL", 7 * 86400.0))
SUBTITLE_MAX_BYTES = max(1024, _int("SUBTITLE_MAX_BYTES", 1_000_000))
SUBTITLE_TIMEOUT = max(0.5, _float("SUBTITLE_TIMEOUT", 5.0))

# Periodic library ingest (SOURCE=library). Empty INGEST_SITES disables it.
INGEST_SITES = [s.strip() for s in (os.environ.get("INGEST_SITES") or "").split(",") if s.strip()]
# Seconds between ingest runs (default 6h). Be gentle to upstream sites.
INGEST_INTERVAL = _int("INGEST_INTERVAL", 21600)

# TMDB enrichment (credentials are read lazily by app.library.tmdb from
# TMDB_ACCESS_KEY / TMDB_TOKEN / TMDB_API_KEY; never log them).
TMDB_ENRICH_TYPES = {t.strip().lower() for t in (os.environ.get("TMDB_ENRICH_TYPES") or "movie").split(",") if t.strip()}
TMDB_RETRY_DAYS = float(os.environ.get("TMDB_RETRY_DAYS") or 7)
TMDB_POSTER_SIZE = (os.environ.get("TMDB_POSTER_SIZE") or "w500").strip()
TMDB_BACKDROP_SIZE = (os.environ.get("TMDB_BACKDROP_SIZE") or "w1280").strip()
# Episode stills (TMDB still sizes: w92 w185 w300 original) and season posters (TMDB_POSTER_SIZE).
TMDB_STILL_SIZE = (os.environ.get("TMDB_STILL_SIZE") or "original").strip()
TMDB_TIMEOUT = float(os.environ.get("TMDB_TIMEOUT") or 6)
TMDB_CONCURRENCY = max(1, _int("TMDB_CONCURRENCY", 4))
# Total seconds one ingest may spend on TMDB; the rest is deferred to the next run.
TMDB_BUDGET_SECONDS = float(os.environ.get("TMDB_BUDGET_SECONDS") or 90)
# Match thresholds (see app/library/tmdb.py). A title is auto-matched only when the
# combined score >= TMDB_AUTO_SCORE *and* the normalised title similarity alone
# >= TMDB_MIN_SIM (year agreement can never rescue a weak title) and the best
# candidate leads the runner-up by TMDB_MARGIN. Sources without a year need a
# near-exact title (TMDB_MIN_SIM_NOYEAR). Below TMDB_REVIEW_SCORE -> unmatched.
TMDB_AUTO_SCORE = _float("TMDB_AUTO_SCORE", 0.85)
TMDB_REVIEW_SCORE = _float("TMDB_REVIEW_SCORE", 0.55)
TMDB_MARGIN = _float("TMDB_MARGIN", 0.08)
TMDB_MIN_SIM = _float("TMDB_MIN_SIM", 0.9)
TMDB_MIN_SIM_NOYEAR = _float("TMDB_MIN_SIM_NOYEAR", 0.97)
# Alternative/translated-title lookups per title (extra requests for the best
# candidates when the search hit's own title is not similar enough); 0 disables.
TMDB_ALT_TITLE_LOOKUPS = max(0, _int("TMDB_ALT_TITLE_LOOKUPS", 3))

# Full series inventory crawl (app/library/series_crawl.py): after every ingest a budgeted, polite pass over the
# series pages of a site (one page per series: it lists every season) writes ALL seasons/episodes to the library.
# SERIES_CRAWL_BUDGET = series per run (0 disables the stage; the rest waits for the next run), SERIES_CRAWL_SECONDS =
# wall-clock budget per run, SERIES_CRAWL_DELAY = seconds between two page requests (on top of the transport's own
# pause; one request at a time), SERIES_CRAWL_REFRESH_DAYS = a complete inventory is re-read at least this often,
# SERIES_CRAWL_RETRY_HOURS = minimum gap before an unfinished / failed / already-crawled series is asked again
# (doubles per consecutive failure, max x16), SERIES_CRAWL_MAX_PAGES = page cap per series (series page + season
# pages a series page did not list), SERIES_CRAWL_MAX_ERRORS = consecutive fetch failures that stop the stage.
SERIES_CRAWL_BUDGET = max(0, _int("SERIES_CRAWL_BUDGET", 15))
SERIES_CRAWL_SECONDS = max(1.0, _float("SERIES_CRAWL_SECONDS", 300.0))
SERIES_CRAWL_DELAY = max(0.0, _float("SERIES_CRAWL_DELAY", 3.0))
SERIES_CRAWL_REFRESH_DAYS = max(0.0, _float("SERIES_CRAWL_REFRESH_DAYS", 7.0))
SERIES_CRAWL_RETRY_HOURS = max(0.0, _float("SERIES_CRAWL_RETRY_HOURS", 6.0))
SERIES_CRAWL_MAX_PAGES = max(1, _int("SERIES_CRAWL_MAX_PAGES", 6))
SERIES_CRAWL_MAX_ERRORS = max(1, _int("SERIES_CRAWL_MAX_ERRORS", 5))

# Content that is not public (yaml ``blocked:`` rules + ``availability_gate:``; app/scraper/blocked.py, app/library/gate.py):
# BLOCKED_RECHECK_DAYS = a page / series found blocked is judged again after this many days (a site that lifts the block
# gets its content back; 0 = never fresh, so every scan re-judges), GATE_BUDGET = pages one scan may probe BEFORE writing
# films / card-only series (the gate; the per-series probes of the inventory stage ride on SERIES_CRAWL_* and
# SERIES_CRAWL_DELAY), GATE_RECHECK_PAGES = blocked episode / film pages re-judged per scan (the blocked ones that are
# older than BLOCKED_RECHECK_DAYS).
BLOCKED_RECHECK_DAYS = max(0.0, _float("BLOCKED_RECHECK_DAYS", 7.0))
GATE_BUDGET = max(0, _int("GATE_BUDGET", 40))
GATE_RECHECK_PAGES = max(0, _int("GATE_RECHECK_PAGES", 3))

# Playback resolution (user-triggered: library/videos.py + scraper/fetch.fetch_url + scraper/providers). The polite
# crawl path (fetch.fetch, MIN_DELAY 2 s per host, 3 tries) is NOT affected by any of these.
# RESOLVE_TIMEOUT = seconds per HTTP request, RESOLVE_RETRIES = tries per request (network/429/5xx only; 0.3 s / 0.8 s
# backoff), RESOLVE_MIN_DELAY = seconds between requests to one host (0 = none),
# RESOLVE_PARALLEL = candidates of one source resolved at once (a source lists e.g. 2x vidmoly + moly + OK.ru),
# RESOLVE_SOURCES_PARALLEL = sources of one episode/film resolved at once,
# RESOLVE_CANDIDATE_TIMEOUT / RESOLVE_TOTAL_TIMEOUT = per-candidate / whole-source wall clock limit,
# RESOLVE_GRACE = once one candidate produced streams, wait at most this long for the others,
# RESOLVE_BROWSER_TIMEOUT = time budget of ONE browser-carried candidate (player_page `fetch: browser`: Cloudflare / JS
# player, a browser session takes 10+ s); it only lengthens that candidate's limit (and the whole-source limit, +2 s), the
# other candidates keep RESOLVE_CANDIDATE_TIMEOUT,
# RESOLVE_FAST_FIRST = 1 puts candidates that answered clearly faster (0.5 s buckets) first, 0 keeps page order,
# RESOLVE_CACHE_TTL = seconds a resolved payload is reused (stream URLs may be bound to the server IP: keep it short;
# 0 = never reuse); this is the lifetime of a payload whose stream URLs say nothing about their own expiry,
# RESOLVE_CACHE_MAX_TTL = upper limit of the reuse time of a payload whose URLs carry an expiry hint (OK.ru ``expires``,
# googlevideo ``expire``, signed CDN ``X-Amz-*`` ...: reused until that moment minus RESOLVE_CACHE_MARGIN, at most this
# long; never below RESOLVE_CACHE_TTL),
# RESOLVE_CACHE_MARGIN = seconds before the URL's own expiry a payload stops being reused,
# RESOLVE_REFRESH_AHEAD = a payload that is reused but runs out within this many seconds is answered from the cache AND
# resolved again in the background, so the next playback finds a fresh one (library/videos.py ``resolve_source``),
# RESOLVE_NEG_TTL = seconds a failed candidate/source is not asked again, RESOLVE_PREFETCH = 1 resolves the most
# likely source in the background when a detail page opens (default off).
RESOLVE_TIMEOUT = max(1.0, _float("RESOLVE_TIMEOUT", 6.0))
RESOLVE_RETRIES = max(1, _int("RESOLVE_RETRIES", 2))
RESOLVE_MIN_DELAY = max(0.0, _float("RESOLVE_MIN_DELAY", 0.0))
RESOLVE_PARALLEL = max(1, _int("RESOLVE_PARALLEL", 4))
RESOLVE_SOURCES_PARALLEL = max(1, _int("RESOLVE_SOURCES_PARALLEL", 3))
RESOLVE_CANDIDATE_TIMEOUT = max(1.0, _float("RESOLVE_CANDIDATE_TIMEOUT", 12.0))
RESOLVE_TOTAL_TIMEOUT = max(1.0, _float("RESOLVE_TOTAL_TIMEOUT", 20.0))
RESOLVE_GRACE = max(0.0, _float("RESOLVE_GRACE", 6.0))  # 3.0 cut the OK.ru hand-off (ajax + iframe + metadata, ~2-4 s) off
RESOLVE_BROWSER_TIMEOUT = max(1.0, _float("RESOLVE_BROWSER_TIMEOUT", 40.0))
RESOLVE_FAST_FIRST = _int("RESOLVE_FAST_FIRST", 1) != 0
RESOLVE_CACHE_TTL = max(0.0, _float("RESOLVE_CACHE_TTL", 900.0))
RESOLVE_CACHE_MAX_TTL = max(RESOLVE_CACHE_TTL, _float("RESOLVE_CACHE_MAX_TTL", 21600.0))
RESOLVE_CACHE_MARGIN = max(0.0, _float("RESOLVE_CACHE_MARGIN", 300.0))
RESOLVE_REFRESH_AHEAD = max(0.0, _float("RESOLVE_REFRESH_AHEAD", 600.0))
RESOLVE_NEG_TTL = max(0.0, _float("RESOLVE_NEG_TTL", 60.0))
RESOLVE_PREFETCH = _int("RESOLVE_PREFETCH", 0) != 0

# Stream proxy (routers/stream_proxy.py, app/streamproxy.py; GET|HEAD /api/stream-proxy/<token>): a stream whose URL is
# bound to the User-Agent the resolver used (OK.ru: ``srcAg=CHROME``) carries ``request_headers``; /api/streams then hands
# the client a signed proxy URL and the server fetches the file with those headers. STREAM_PROXY_SECRET = HMAC key of the
# tokens (empty = generated once and kept in DATA_DIR/stream_proxy.key, 0600; never logged), STREAM_PROXY_TTL = seconds a
# token (= a /api/streams answer) stays valid, STREAM_PROXY_MAX = simultaneous proxied connections (the rest get 503).
STREAM_PROXY_SECRET = (os.environ.get("STREAM_PROXY_SECRET") or "").strip()
STREAM_PROXY_TTL = max(60, _int("STREAM_PROXY_TTL", 6 * 3600))
STREAM_PROXY_MAX = max(1, _int("STREAM_PROXY_MAX", 8))
# Base address of the proxy URLs in /api/streams. Empty (default) = the address the CLIENT used: X-Forwarded-Proto /
# X-Forwarded-Host (or Forwarded) when a tunnel / reverse proxy sets them, else the Host header + the request's scheme, so
# a LAN client stays on the LAN and never streams through the tunnel. Set only to force one fixed base for every client
# (``https://host[:port][/prefix]``; an invalid value is ignored). The token itself holds no base address.
STREAM_PROXY_BASE_URL = (os.environ.get("STREAM_PROXY_BASE_URL") or "").strip().rstrip("/")
# What else goes through the proxy (app/streamproxy.py ``proxy_reason``; /api/streams ``proxy_reason`` = ua | ip | recipe |
# learned | env). STREAM_PROXY_HOSTS = comma separated hosts (a name also covers its subdomains) whose mp4 / hls streams are
# always proxied; STREAM_PROXY_FORCE=1 proxies EVERY mp4 / hls stream (bandwidth passes through the server).
STREAM_PROXY_HOSTS = tuple(h.strip().lower().strip(".") for h in (os.environ.get("STREAM_PROXY_HOSTS") or "").split(",") if h.strip())
STREAM_PROXY_FORCE = _int("STREAM_PROXY_FORCE", 0) != 0
# HLS proxy (the playlist is fetched and every URI rewritten, segments / keys are streamed): a player asks for many segments at
# once, so the limit is per STREAM (group) instead of the progressive-file STREAM_PROXY_MAX: STREAM_PROXY_HLS_MAX upstream
# connections at a time per group; a request over it waits up to STREAM_PROXY_QUEUE_WAIT seconds for a place before 503.
# STREAM_PROXY_PLAYLIST_MAX = longest playlist read (bytes), STREAM_PROXY_PLAYLIST_URIS = most URIs one playlist may carry.
STREAM_PROXY_HLS_MAX = max(1, _int("STREAM_PROXY_HLS_MAX", 24))
STREAM_PROXY_QUEUE_WAIT = max(0.0, _float("STREAM_PROXY_QUEUE_WAIT", 8.0))
STREAM_PROXY_PLAYLIST_MAX = max(4096, _int("STREAM_PROXY_PLAYLIST_MAX", 2 * 1024 * 1024))
STREAM_PROXY_PLAYLIST_URIS = max(10, _int("STREAM_PROXY_PLAYLIST_URIS", 20000))
# Playback failure diagnosis (library/streamdiag.py): after a failed playback report the server probes the stream in the
# background (once per source per STREAM_DIAG_COOLDOWN seconds, at most STREAM_DIAG_PARALLEL at a time, STREAM_DIAG_TIMEOUT
# seconds per request) and stores the verdict in video_sources.last_diag; STREAM_DIAG=0 switches it off (the report still
# updates the source's health as before).
STREAM_DIAG = _int("STREAM_DIAG", 1) != 0
STREAM_DIAG_COOLDOWN = max(0.0, _float("STREAM_DIAG_COOLDOWN", 600.0))
STREAM_DIAG_PARALLEL = max(1, _int("STREAM_DIAG_PARALLEL", 2))
STREAM_DIAG_TIMEOUT = max(1.0, _float("STREAM_DIAG_TIMEOUT", 8.0))

# Trailer liveness check (library/trailer_check.py): a YouTube trailer is verified with the oEmbed endpoint when a
# detail page opens (200 = alive, 401/403/404 = dead/not embeddable; timeouts and other errors are never cached and
# leave the trailer "unknown = assumed alive"). TRAILER_CHECK = 0 switches it off, TRAILER_CHECK_TIMEOUT = seconds per
# oEmbed request, TRAILER_CHECK_BUDGET = the longest a detail/streams response waits for a check (the check goes on in
# the background and lands in the cache), TRAILER_OK_TTL / TRAILER_DEAD_TTL = seconds a verdict is reused.
TRAILER_CHECK = _int("TRAILER_CHECK", 1) != 0
TRAILER_CHECK_TIMEOUT = max(0.5, _float("TRAILER_CHECK_TIMEOUT", 3.0))
TRAILER_CHECK_BUDGET = max(0.0, _float("TRAILER_CHECK_BUDGET", 2.5))
TRAILER_OK_TTL = max(1.0, _float("TRAILER_OK_TTL", 86400.0))
TRAILER_DEAD_TTL = max(1.0, _float("TRAILER_DEAD_TTL", 21600.0))

# Site-onboarding sandbox (routers/onboard_sandbox.py, scraper/onboard_store.py): ONBOARD_TOOL_TIMEOUT = longest one sandbox
# call may take (s; 504 after that), ONBOARD_MAX_PAGE_BYTES = cap of one stored page, ONBOARD_RETENTION_DAYS = age after
# which stored pages and drafts (except saved ones) are deleted.
ONBOARD_TOOL_TIMEOUT = max(5.0, _float("ONBOARD_TOOL_TIMEOUT", 120.0))
ONBOARD_MAX_PAGE_BYTES = max(100_000, _int("ONBOARD_MAX_PAGE_BYTES", 3_000_000))
ONBOARD_RETENTION_DAYS = max(1, _int("ONBOARD_RETENTION_DAYS", 7))
# Site-onboarding job manager (scraper/onboard.py, routers/ops_onboard.py): ONBOARD_ENABLED = 0 turns the feature off,
# ONBOARD_TIMEOUT = longest one pi run may take (s), ONBOARD_PI_BIN = pi binary (falls back to SCRAPER_HEAL_PI_BIN, then
# "pi"), ONBOARD_MODEL = model of the agent ("" = the heal model, SCRAPER_HEAL_MODEL).
ONBOARD_ENABLED = _int("ONBOARD_ENABLED", 1) != 0
ONBOARD_TIMEOUT = max(1.0, _float("ONBOARD_TIMEOUT", 900.0))
ONBOARD_PI_BIN = (os.environ.get("ONBOARD_PI_BIN") or os.environ.get("SCRAPER_HEAL_PI_BIN") or "pi").strip()
ONBOARD_MODEL = (os.environ.get("ONBOARD_MODEL") or "").strip()
# Playback-triggered heal (scraper/playheal.py): when the sources of one site keep failing the same way, a heal run is
# started (it still needs SCRAPER_HEAL_ENABLED; applying the result follows the admin "heal_autoapply" setting).
# PLAYHEAL_ENABLED = 0 turns the trigger off, PLAYHEAL_WINDOW = how many different sources of a site the sliding window
# keeps, PLAYHEAL_MIN_SOURCES = at least this many different failed sources, PLAYHEAL_FAIL_RATIO = share of failed
# sources in the window (0.1 .. 1) that starts a heal.
PLAYHEAL_ENABLED = _int("PLAYHEAL_ENABLED", 1) != 0
PLAYHEAL_WINDOW = max(3, _int("PLAYHEAL_WINDOW", 12))
PLAYHEAL_MIN_SOURCES = max(1, _int("PLAYHEAL_MIN_SOURCES", 3))
PLAYHEAL_FAIL_RATIO = min(1.0, max(0.1, _float("PLAYHEAL_FAIL_RATIO", 0.6)))
# A scan whose series items mostly have NO episode video source (library/ingest.py coverage; a registered site whose normalize
# has no episode_source / series_page): PLAYHEAL_COVERAGE_RATIO = share of such series items (0.1 .. 1) and
# PLAYHEAL_COVERAGE_MIN_SERIES = least number of series items that make it a signal: a warning in the scan record and a repair
# run (evidence kind "no_sources", same gates as a playback heal). Series the inventory stage has not reached yet do not count.
PLAYHEAL_COVERAGE_RATIO = min(1.0, max(0.1, _float("PLAYHEAL_COVERAGE_RATIO", 0.5)))
PLAYHEAL_COVERAGE_MIN_SERIES = max(1, _int("PLAYHEAL_COVERAGE_MIN_SERIES", 5))
# Multi-site live search (library/search_all.py, GET /api/search): the query fans out to every site that can search.
# SEARCH_PARALLEL = sites searched at the same time, SEARCH_SITE_TIMEOUT = seconds one site may take (search + cache write),
# SEARCH_TOTAL_TIMEOUT = seconds the whole fan-out may take (sites still running then are reported as timed out),
# SEARCH_BREAKER_FAILS = consecutive failures that park a site, SEARCH_BREAKER_COOLDOWN = seconds it is skipped.
SEARCH_PARALLEL = max(1, _int("SEARCH_PARALLEL", 4))
SEARCH_SITE_TIMEOUT = max(0.5, _float("SEARCH_SITE_TIMEOUT", 8.0))
SEARCH_TOTAL_TIMEOUT = max(0.5, _float("SEARCH_TOTAL_TIMEOUT", 12.0))
SEARCH_BREAKER_FAILS = max(1, _int("SEARCH_BREAKER_FAILS", 3))
SEARCH_BREAKER_COOLDOWN = max(1.0, _float("SEARCH_BREAKER_COOLDOWN", 300.0))

# Source finder (library/sourcefinder.py, GET /api/notifications, GET /api/source-finder/<id>): a play request that ends
# without a playable stream starts a background job (re-resolve the known sources by force -> search the title on other
# sites -> repair agent) and a found source becomes a notification. SOURCEFINDER_ENABLED = 0 turns it off,
# SOURCEFINDER_COOLDOWN = seconds before the same title+episode may start another job (also after a found one),
# SOURCEFINDER_MAX_JOBS = jobs running at once, SOURCEFINDER_MAX_SITES = other sites searched per job,
# SOURCEFINDER_DAILY_BUDGET = repair-agent runs the finder may start per 24 h (0 = the agent step never runs).
SOURCEFINDER_ENABLED = _int("SOURCEFINDER_ENABLED", 1) != 0
SOURCEFINDER_COOLDOWN = max(0, _int("SOURCEFINDER_COOLDOWN", 3600))
SOURCEFINDER_MAX_JOBS = max(1, _int("SOURCEFINDER_MAX_JOBS", 2))
SOURCEFINDER_MAX_SITES = max(1, _int("SOURCEFINDER_MAX_SITES", 4))
SOURCEFINDER_DAILY_BUDGET = max(0, _int("SOURCEFINDER_DAILY_BUDGET", 10))

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
os.makedirs(IMG_CACHE_DIR, exist_ok=True)
