"""Deterministic library fixture shared by test_client_contract.py and tools/gen_api_samples.py.

Fictional titles, every scenario the TV/Android client has to survive: film / series with seasons and specials /
series without any episode / poster-less film / dead or missing trailer / episode without a live source /
announced (not yet aired) episode. Import ``_sandbox`` (or run under it) BEFORE this module.
"""
import contextlib
import hashlib
import io
import json
import os
import tempfile
import time
from contextlib import closing

from app import config, db

NOW = int(time.time())

POSTER = "https://image.tmdb.org/t/p/w500/poster-sample.jpg"
BACKDROP = "https://image.tmdb.org/t/p/w1280/backdrop-sample.jpg"
STILL = "https://image.tmdb.org/t/p/original/still-sample.jpg"
YT = "https://www.youtube.com/embed/aaaaaaaaaaa"


def item(conn, cid, title, type_="movie", year=2024, overview="", genres=(), rating=None, runtime=None, poster=None,
         backdrop=None, added=0, tmdb_id=None, imdb_id=None, cast=(), country="US", followers=None):
    conn.execute(
        "INSERT INTO library_items(id,tmdb_id,type,title,original_title,year,overview,genres,rating,runtime,country,"
        "followers,cast,poster_url,backdrop_url,popularity,added_at,updated_at,imdb_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (cid, tmdb_id, type_, title, None, year, overview, json.dumps(list(genres), ensure_ascii=False), rating, runtime,
         country, followers, json.dumps(list(cast), ensure_ascii=False), poster, backdrop, 1.0, added, NOW, imdb_id))


def video(conn, cid, kind, locator, status="healthy", resolver="direct", media_type="mp4", season=None, episode=None,
          title=None, overview=None, still=None, runtime=None, air=None, trailer_dead=0, source="yabancidizi", label=None):
    episode_id = "%s:s%d:e%d" % (cid, season, episode) if kind == "episode" else ""
    sid = "vs_" + hashlib.sha1((episode_id or cid + ":" + kind + ":" + locator).encode()).hexdigest()[:24]
    conn.execute(
        "INSERT INTO video_sources(id,canonical_id,source,source_key,episode_id,season,episode,kind,locator,resolver,"
        "media_type,label,episode_title,episode_overview,episode_still_url,episode_runtime,episode_air_date,status,"
        "failures,updated_at,trailer_dead) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, cid, source, cid + "-key", episode_id, season, episode, kind, locator, resolver, media_type,
         label or ("Fragman" if kind == "trailer" else "VidMolly"), title, overview, still, runtime, air, status,
         0 if status in ("unknown", "healthy") else 3 if status == "broken" else 1, NOW, trailer_dead))


def ep(conn, cid, season, episode, **kw):
    kw.setdefault("title", "Bölüm %d-%d" % (season, episode))
    kw.setdefault("air", "2026-%02d-%02d" % (min(season, 12), min(episode, 28)))
    video(conn, cid, "episode", "https://cdn.example/%s/s%de%d.mp4" % (cid, season, episode), season=season, episode=episode, **kw)


def lists(conn, list_id, *ids):
    for pos, cid in enumerate(ids):
        conn.execute("INSERT INTO library_lists(list_id,canonical_id,position) VALUES (?,?,?)", (list_id, cid, pos))


def seed():
    """Fill the (already ``db.init()``-ed) database. Returns nothing; ids are module-level constants below."""
    with closing(db.connect()) as conn, conn:
        # 1. ordinary film: full source + live trailer, real artwork, progress by p1
        item(conn, FILM, "Gece Yarısı Treni", "movie", 2024, "Bir tren, bir bavul ve çözülmesi gereken bir gece.",
             ["Gerilim", "Dram"], 7.4, 108, POSTER, BACKDROP, added=50, tmdb_id=1001, imdb_id="tt1000001",
             cast=["Ada Yıldız", "Mert Kaya"], followers=310)
        video(conn, FILM, "movie", "https://cdn.example/film/master.m3u8?t=k3Jx9QvT&e=1791000000&s=7f2a91", media_type="hls",
              resolver="direct")
        video(conn, FILM, "trailer", YT, status="unknown", resolver="embed", media_type="embed")
        # 2. poster-less, overview-less film with only a not-yet-tested source
        item(conn, NOPOSTER, "Sessiz Liman", "movie", None, "", [], None, None, None, None, added=40, country=None)
        video(conn, NOPOSTER, "movie", "https://cdn.example/liman.mp4", status="unknown")
        # 3. trailer-only film whose YouTube trailer is verified dead (no button, no full source)
        item(conn, DEADTRAILER, "Kayıp Sahil", "movie", 2023, "Fragmanı silinmiş bir film.", ["Macera"], 6.1, 95,
             POSTER, None, added=30)
        video(conn, DEADTRAILER, "trailer", YT.replace("aaaaaaaaaaa", "bbbbbbbbbbb"), status="unknown",
              resolver="embed", media_type="embed", trailer_dead=1)
        # 4. catalogue-only film (metadata, no video row at all)
        item(conn, METAONLY, "Eski Defter", "movie", 1999, "Yalnızca katalog bilgisi.", ["Dram"], 8.0, 120, POSTER, None,
             added=20)
        # 5. series with seasons + specials, every episode state, real progress
        item(conn, SERIES, "Kayıp Sinyal", "series", 2021, "Uzak bir istasyondan gelen sinyalin peşinde bir ekip.",
             ["Bilim Kurgu", "Gizem"], 8.3, 45, POSTER, BACKDROP, added=60, tmdb_id=2001, imdb_id="tt2000001",
             cast=["Deniz Arı"], followers=1172)
        video(conn, SERIES, "trailer", YT, status="unknown", resolver="embed", media_type="embed")
        ep(conn, SERIES, 0, 1, title="Perde Arkası", air="2021-06-01")               # special (season 0)
        ep(conn, SERIES, 1, 1, still=STILL, overview="İlk sinyal.", runtime=44)
        ep(conn, SERIES, 1, 2)
        ep(conn, SERIES, 2, 1, title="1. Bölüm")                                     # generic title: label has no name
        ep(conn, SERIES, 2, 2, status="broken")                                      # no live source
        ep(conn, SERIES, 2, 3, status="suspect")                                     # check required
        ep(conn, SERIES, 3, 1, title="Yankı", still=STILL, status="unknown", air="2026-09-20")   # newest, ready
        # 6. series with no episode inventory yet (trailer only)
        item(conn, NOSEASONS, "Yeni Umut", "series", 2026, "Bölüm listesi henüz gelmedi.", ["Dram"], None, None, POSTER,
             None, added=35)
        video(conn, NOSEASONS, "trailer", YT.replace("aaaaaaaaaaa", "ccccccccccc"), status="unknown",
              resolver="embed", media_type="embed")
        # 7. series that only has the card episode of the home page
        item(conn, ONEEP, "Sınır Hattı", "series", 2026, "Tek bölümü olan dizi.", ["Aksiyon"], 7.9, 50, POSTER, None,
             added=55)
        ep(conn, ONEEP, 1, 5, title="Hat Kopuyor", status="unknown", air="2026-09-28")
        # 8. series whose newest episode is announced (future air date) and has no working source
        item(conn, UNAIRED, "Yakında Gelen", "series", 2026, "Yeni bölümü henüz yayınlanmadı.", ["Komedi"], 7.0, 30,
             POSTER, None, added=45)
        ep(conn, UNAIRED, 1, 1, title="Pilot", status="unknown", air="2026-01-05")
        ep(conn, UNAIRED, 1, 2, title="İkinci", status="disabled", air="2099-01-01")
        # 9. series whose NEWEST episode has no live source (older ones play): not a "new episode" card
        item(conn, BROKENLAST, "Bozuk Son", "series", 2026, "Son bölümün kaynağı çalışmıyor.", ["Dram"], 6.5, 40,
             POSTER, None, added=25)
        ep(conn, BROKENLAST, 1, 1, title="Baş", status="unknown", air="2026-08-01")
        ep(conn, BROKENLAST, 1, 2, title="Son", status="broken", air="2026-09-01")
        # 10. more playable titles WITH a backdrop (the slider needs 3 series + 3 films) and a CLASSIC film (old + well rated:
        #     "Dikkate Değer Filmler"); the catalogue-only METAONLY (1999, rated 8.0) is a classic too but not playable
        item(conn, FILM2, "Mavi Liman Yolu", "movie", 2023, "Bir liman kasabasında geçen sıcak bir hikaye.", ["Dram"], 8.1, 101,
             POSTER, BACKDROP, added=48, followers=90)
        video(conn, FILM2, "movie", "https://cdn.example/film2/master.m3u8", media_type="hls")
        item(conn, FILM3, "Son Durak", "movie", 2022, "Son trenin son yolcuları.", ["Gerilim"], 7.0, 97, POSTER, BACKDROP, added=47)
        video(conn, FILM3, "movie", "https://cdn.example/film3.mp4", status="unknown")
        item(conn, CLASSIC, "Eski Dostlar", "movie", 1985, "Yıllar sonra bir araya gelen dostlar.", ["Dram"], 8.6, 118, POSTER,
             None, added=10)
        video(conn, CLASSIC, "movie", "https://cdn.example/klasik.mp4", status="unknown")
        item(conn, SERIES2, "Yedinci Merdiven", "series", 2024, "Bir apartmanın yedinci katındaki sırlar.", ["Gizem"], 8.0, 42,
             POSTER, BACKDROP, added=58, followers=420)
        ep(conn, SERIES2, 1, 1, title="Merdiven", status="unknown", air="2026-02-01")
        item(conn, SERIES3, "Gölge Oyunu", "series", 2023, "Gölgelerin arkasındaki oyun.", ["Aksiyon"], 7.2, 44, POSTER, BACKDROP,
             added=57)
        ep(conn, SERIES3, 1, 1, title="Perde", status="unknown", air="2026-03-01")
        lists(conn, "latest_episodes_yabancidizi", SERIES, ONEEP, NOSEASONS, BROKENLAST, UNAIRED)
        lists(conn, "trending_yabancidizi", SERIES, FILM)
        lists(conn, "latest_series_yabancidizi", ONEEP, FILM, SERIES)   # the film is no series card: left out of `new_series`
        lists(conn, "latest_movies_yabancidizi", FILM, NOPOSTER)
        lists(conn, "noteworthy_movies_yabancidizi", FILM2, SERIES2)   # a series on the list is no "Dikkate Değer Film"
        lists(conn, "featured_yabancidizi", FILM, SERIES)
        for cid in (FILM, NOPOSTER, DEADTRAILER, METAONLY, SERIES, NOSEASONS, ONEEP, UNAIRED, BROKENLAST,
                    FILM2, FILM3, CLASSIC, SERIES2, SERIES3):
            conn.execute("INSERT INTO source_items(source,source_key,canonical_id,fetched_at) VALUES ('yabancidizi',?,?,?)",
                         (cid + "-key", cid, NOW))
        # progress: one unfinished episode (S2E1 of the big series), one unfinished film
        conn.execute("INSERT INTO progress VALUES ('p1',?,?,600,2700,0,?)", (SERIES, SERIES + ":s2:e1", NOW - 60))
        conn.execute("INSERT INTO progress VALUES ('p1',?,?,3000,6480,0,?)", (FILM, FILM, NOW - 30))
        conn.execute("INSERT INTO mylist VALUES ('p1',?,?)", (FILM, NOW - 100))
        # source finder: one finished job that found a source by re-resolving it + its (unread) notification for p1
        conn.execute("INSERT INTO finder_jobs(canonical_id,episode_id,profile_id,state,trigger,started_at,finished_at,steps,source_id,method) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (FILM, "", "p1", "found", "play", NOW - 90, NOW - 85,
                      json.dumps([{"name": "retry", "ok": True, "ms": 4200, "note": "1/1 kaynak çözüldü"}]), "vs_sample", "retry"))
        conn.execute("INSERT INTO notifications(profile_id,kind,canonical_id,episode_id,payload,created_at) VALUES (?,?,?,?,?,?)",
                     ("p1", "source_found", FILM, "", json.dumps({"title": "Gece Yarısı Treni", "season": None, "episode": None,
                                                                   "site": "yabancidizi", "method": "retry"}), NOW - 85))


FILM, NOPOSTER, DEADTRAILER, METAONLY = "film-gece", "film-liman", "film-sahil", "film-defter"
SERIES, NOSEASONS, ONEEP, UNAIRED, BROKENLAST = "dizi-sinyal", "dizi-umut", "dizi-hat", "dizi-yakinda", "dizi-bozuk"
FILM2, FILM3, CLASSIC, SERIES2, SERIES3 = "film-mavi", "film-durak", "film-klasik", "dizi-merdiven", "dizi-golge"


@contextlib.contextmanager
def seeded_client():
    """A TestClient on the real FastAPI app, backed by a temp DB seeded by :func:`seed` (``SOURCE=library``
    behaviour, remote artwork answered by a fake, no trailer/oEmbed traffic, no scheduler: the lifespan is not run)."""
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from PIL import Image
    from app import cache, config, images
    from app.routers import detail as detail_router
    from app.sources.library import LibrarySource

    def fake_fetch(_url):
        buf = io.BytesIO()
        Image.new("RGB", (64, 96), (30, 60, 120)).save(buf, "PNG")
        return buf.getvalue()

    with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
        for target, name, value in ((config, "DB_PATH", os.path.join(tmp, "contract.db")),
                                    (config, "IMG_CACHE_DIR", os.path.join(tmp, "imgcache")),
                                    (config, "TRAILER_CHECK", False),
                                    (cache, "_adapter", LibrarySource()), (cache, "_snapshot", None),
                                    (images, "_http_fetch", fake_fetch),
                                    # no background hydrate (it would fetch the source site, and `hydrating` must be
                                    # deterministic in the samples): the detail answers with the seeded data only
                                    (detail_router, "schedule_hydrate", lambda *_a, **_k: False),
                                    (detail_router, "schedule_seasons", lambda *_a, **_k: False)):
            stack.enter_context(patch.object(target, name, value))
        os.makedirs(config.IMG_CACHE_DIR, exist_ok=True)
        db.init()
        seed()
        from app.main import app
        yield TestClient(app)


# ---- sample responses (docs/api-samples) -------------------------------------------------------------------------
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SAMPLE_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "docs", "api-samples"))

SAMPLES = {
    "boot.json": "/api/boot?profile=p1&layout=tv-v1",
    "row-new_episodes.json": "/api/row/new_episodes?profile=p1&offset=0&limit=20",
    "detail-movie.json": "/api/detail/%s?profile=p1" % FILM,
    "detail-movie-no-source.json": "/api/detail/%s?profile=p1" % METAONLY,
    "detail-series.json": "/api/detail/%s?profile=p1" % SERIES,
    "detail-series-no-episodes.json": "/api/detail/%s?profile=p1" % NOSEASONS,
    "streams-movie.json": "/api/streams/%s?profile=p1&kind=video" % FILM,
    "streams-trailer.json": "/api/streams/%s?profile=p1&kind=trailer" % FILM,
    "streams-episode.json": "/api/streams/%s?profile=p1&episode=%s:s1:e1&kind=video" % (SERIES, SERIES),
    "profiles.json": "/api/profiles",
    "avatars.json": "/api/avatars",
    "catalog.json": "/api/catalog?profile=p1&type=series&limit=2",
    "search.json": "/api/search?q=ka&profile=p1&limit=3",
    "mylist.json": "/api/mylist?profile=p1",
    "notifications.json": "/api/notifications?profile=p1",
    "source-finder.json": "/api/source-finder/%s" % FILM,
    "error-not-found.json": "/api/detail/yok?profile=p1",
}


def redact(obj):
    """Censor what must never be copied out of a real response: attempt tokens and the values of signed stream URLs
    (``?t=...&e=...&s=...`` -> ``?t=REDACTED&...``, a ``/api/stream-proxy/<token>`` token -> ``REDACTED``). Keys and
    shapes stay."""
    if isinstance(obj, dict):
        out = {k: redact(v) for k, v in obj.items()}
        if "attempt_token" in out:
            out["attempt_token"] = "REDACTED"
        if "attempt_token" in obj and isinstance(out.get("url"), str) and "/api/stream-proxy/" in out["url"]:
            out["url"] = out["url"].split("/api/stream-proxy/")[0] + "/api/stream-proxy/REDACTED"   # the token holds the real signed URL
        elif "attempt_token" in obj and isinstance(out.get("url"), str):
            parts = urlsplit(out["url"])
            if parts.query:
                query = urlencode([(k, "REDACTED") for k, _ in parse_qsl(parts.query, keep_blank_values=True)])
                out["url"] = urlunsplit(parts._replace(query=query))
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


def build_samples(client):
    """{file name: censored JSON body} for every sample, taken from the seeded server behind ``client``."""
    out = {}
    for name, path in SAMPLES.items():
        body = client.get(path).json()
        if name == "avatars.json":
            body = {"avatars": body["avatars"][:3]}
        out[name] = redact(body)
    return out


def dump(obj):
    import json as _json
    return _json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
