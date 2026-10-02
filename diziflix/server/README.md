# diziflix — server

The shared scraper integration is documented in [CRAWLEE.md](CRAWLEE.md).
Sinemalar uses Crawlee HTTP. Obscura is the single browser engine for
Yabancidizi catalogue pages, same-session artwork and on-demand provider
discovery. Admin "Tara ve Aktar" updates the canonical library and refreshes
the API cache.

Backend for the diziflix Tizen TV film-discovery client.
FastAPI + SQLite + Pillow, listening on `0.0.0.0:8090`.

**Source-agnostic.** The wire format is fixed by [`../API.md`](../API.md) — the
TV client is written against it, so treat that file as the contract. The client
always sees the same standard API; whichever backend feeds it (bundled fixture
or real scraped films) is normalised on the server, never in the client.

## Install / run

```bash
cd diziflix/server
./install.sh                       # venv + requirements + fixture + (Linux) systemd
```

`install.sh` creates `venv/`, installs `requirements.txt` (via `uv` when
available, else pip), copies `.env.example` → `.env` if missing, generates
`data/fixture.json` when absent, and — on Linux with `systemctl` — installs and
starts the `diziflix` unit (`diziflix.service`). On macOS the systemd step is
skipped and the manual `uvicorn` command is printed.

```bash
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8090
uvicorn app.main:app --reload --port 8090          # development

sudo systemctl status diziflix
sudo journalctl -u diziflix -f
sudo systemctl restart diziflix                    # after editing .env
```

Interactive docs: <http://localhost:8090/docs>. Same repo/service pattern as the
sibling `scan` service.

## Source layer (`SOURCE`)

Routers only ever talk to a `SourceAdapter` (`app/sources/base.py`), never to a
concrete backend, so swapping data sources is a one-line `.env` change. A new
source is a new adapter module — nothing else changes.

| `SOURCE` | Module | Data |
|----------|--------|------|
| `mock` (default) | `app/sources/mock.py` | committed `data/fixture.json`; offline dev |
| `library` | `app/sources/library.py` | real scraped films from the canonical library (SQLite) |

`app/sources/__init__.py` finds the `SourceAdapter` subclass in the selected
module by name.

## Canonical library

Real films are ingested into a canonical store (`app/db.py`), then served by the
`library` adapter in the exact same internal shape as the fixture:

- `library_items` — one canonical film (nullable `tmdb_id`; provisional slug+year
  id until TMDB is enabled), merged from all its sources.
- `source_items` — the per-source raw record (keyed by `canonical_id`).
- `library_lists` — collection/category membership + ordering (`list_id`, position).
- `field_provenance` — which source supplied each field.
- `video_sources` — provider rows (movie / episode / trailer; episode rows carry the source's title, air date, page URL).
- `library_seasons` / `library_episodes` — TMDB season posters and episode title / overview / air date /
  runtime / still (see "TMDB > Seasons and episodes"). Keyed by canonical series id (+ season / episode).

`app/library/ingest.py` runs `scraper.run_site → normalize → upsert source_items
→ resolve canonical id (TMDB when available, else provisional) → merge into
library_items`. Multi-list, deduplicated; `~30` items per collection, first page
only. `app/library/normalize.py` maps each source's fields to the canonical
shape via a `@register`-keyed function (new source = one function).

## Self-healing scraper

`app/scraper/` is a **multi-site, multi-list, config-as-data** scraper. Each site
is a yaml file under `app/scraper/configs/<site>.yaml`; dropping one in registers
the site with **zero code changes** (`._*`/hidden and `.v<N>.` archive files are
  ignored). Registered today: `sinemalar` (movies) and `yabancidizi`
  (movies and series).

Pipeline (`runner.run_site`): **fetch → deterministic parse → schema validate →
drift check → (optional) LLM self-heal → re-parse if healed & applied**.

- **Selectors are data**, not code — CSS selectors, casts, regex, split rules all
  live in the yaml `list.fields` / `detail.fields` (`selectolax` parser).
- **Collections** (`collections:` in yaml, pure data) define the home-screen rows
  each list page maps to (`new` / `upcoming` / `genre_<slug>`).
- **Drift** (`drift.py`) trips on zero items, count below `min_items`, aggregate
  fill below `min_fill_ratio`, or a critical field's fill below threshold —
  compared against `configs/<site>.baseline.json`.
- **Self-heal** (`heal.py`) rewrites broken selectors with an LLM, never at parse
  time. A proposal is validated in a sandbox against the same HTML (parse →
  schema → re-check drift) before it can be saved; only a proposal that clears
  the baseline is versioned (`<site>.v<N>.yaml` archive) and, with `AUTOAPPLY`,
  activated. Every failure degrades gracefully — the old config keeps serving.
- Per-site metrics/health/history persist as `data/scraper_state/<site>.json`
  (`state.py`), read by the admin dashboard.
- **Artwork**: `/img` proxies remote posters/backdrops (SSRF-guarded by
  `REMOTE_IMG_HOSTS`, suffix match) and falls back to a local Pillow placeholder.

## Video discovery and providers

Page playback has two deliberately separate layers. A site module under
`app/scraper/site_extractors/<site>.py` only finds that catalogue site's player
or hand-off URL. `app/scraper/providers/` then resolves the URL by provider host;
the same VidMolly and OK.ru resolvers are therefore reusable by every site. The
registry can follow a short iframe hand-off chain without knowing which catalogue
produced it. Session-bound catalogue hand-offs are resolved by that site's module
(`resolve_candidate`): yabancidizi's OK.ru (`POST /ajax/service`, then the `/api/ruplay/<token>` iframe) and VidMolly
(`/api/moly/<token>`) hand-offs need only the cookie the site's own script sets (`udys=<Date.now()>`; `ci_session` is
issued by the server), so they open in ~2 s WITHOUT a browser. The browser session's cookie jar (Obscura, 10+ s) is
only the fallback, used when the site answers the light request with a block/challenge (HTTP 403/429/5xx, non-JSON),
never for a definite "no" (`success:0`). Every attempt is a stage in the trail (`yabancidizi.handoff`,
`yabancidizi.handoff.browser`).
For unknown providers, the existing YAML `stream_resolver` remains the fallback.
For sinemalar: `/embed/<id>` → video id
→ `https://vm.empower.net/player/sinemalar/<id>` JSON → `media.level` list →
pick highest working MP4 quality (`1080→720→…`; advertised `"0"` 403s so it's
last, `verify:true` probes and skips 403/unreachable), 24h cache. Emits
`type: mp4` + duration; the client plays natively (embed iframe as fallback when
resolution fails).

### Playback resolution: speed and explainability

- **OK.ru**: primary `POST https://ok.ru/dk?cmd=videoPlayerMetadata` (`mid=<id>`, ~3 KB JSON, independent of the
  page markup); the embed page's `data-options` parse is the fallback. Same output (mp4 list named
  `full`/`quad`/... = 1080p/1440p/..., signed URLs bound to the server's public IP so the TV on the same LAN plays
  them; HLS manifest — `hlsManifestUrl`/`hlsMasterPlaylistUrl`/`ondemandHls` (the adaptive master playlist the web
  player starts with) — only when there is no mp4). An empty result is explained in the trail
  (`no playable video in metadata (videos=N, all disallowed, error=...)`).
- **VidMolly**: `/dl/<code>`, `/w/<code>`, `/v/<code>`, `embed-<code>[.html]` all normalise to
  `https://vidmoly.biz/embed-<code>.html`; after an HTTP block / challenge page / empty page the same URL is retried
  with `Cookie: cf_turnstile_demo_pass_<code>=1`, then the URL's own host.
- **Transport** (`scraper/fetch.fetch_url` / `post_url`, playback only): shared `httpx.Client` (keep-alive), no
  courtesy delay (`RESOLVE_MIN_DELAY=0`), 6 s timeout, 2 tries with 0.3/0.8 s backoff on network/429/5xx only.
  The crawl transport `fetch.fetch` keeps its 2 s per host, 3 tries and 2/4/6 s sleeps.
- **Sources** (`library/videos.py streams`): the candidates of one source (`RESOLVE_PARALLEL`) and the sources of one
  episode (`RESOLVE_SOURCES_PARALLEL`) resolve concurrently with per-candidate / total limits and a grace period once
  one candidate has streams (`RESOLVE_GRACE`, default 6 s; 3 s cut the OK.ru hand-off off behind the ~0.2 s VidMolly
  `/dl/` candidates); clearly faster candidates are listed first (`RESOLVE_FAST_FIRST`) — but inside a subtitle
  language group: candidates carry `lang`/`language` from the site's tab (`İngilizce Altyazılı İndir` links name
  their language; hand-offs and the default player belong to the ACTIVE tab, "Türkçe Altyazı"), Turkish-subtitle
  streams whose subtitle state is measured (VidMolly) are listed first, then files of unknown subtitle state (OK.ru), then
  English. The menu label is `<provider> · <language> · <quality>` (`VidMolly · Türkçe altyazı · auto`); the language part
  is only written when the subtitle state is measured (or for a dub tab), so OK.ru reads `OK.ru · 1080p`; no language when
  the page does not say. Re-signed duplicates of one file are merged, a real spare copy is `... · yedek`, and ` (2)`
  only remains for two different files with identical labels (see "Audio / subtitle tracks" below). A fresh
  `resolved_payload` is reused for `RESOLVE_CACHE_TTL` (short: URLs may carry `i=`/`asn=`/`srcIp=` of the server) or, when
  its stream URLs name their own expiry, until then (`valid_until` in the payload, see the env table: `RESOLVE_CACHE_MAX_TTL`,
  `RESOLVE_CACHE_MARGIN`, `RESOLVE_REFRESH_AHEAD`; a player/json_api resolver may also say `cache_ttl: <60..86400 s>`), failed
  candidates/sources are remembered for `RESOLVE_NEG_TTL` (in memory; admin "yeniden dene" = `retry()` bypasses),
  expired `playback_attempts` are pruned every 10 minutes (not per request). Optional `RESOLVE_PREFETCH=1`
  resolves the most likely source when a detail page opens. Response contract (fields, one `attempt_token` per
  source) is unchanged.
- **Explainability**: providers leave a per-thread trail (`providers/trace.py`: stage, host, ok, ms, short error);
  every page-source resolution is written with `state.record_resolver` (`last_resolver` in
  `data/scraper_state/<site>.json`: ok, ms, page_ms, streams, error, candidates[] with `lang`, `from_cache` = false and
  `valid_in` = seconds the payload will be reused; an answer served from the stored payload writes a light event with
  `from_cache: true` and the seconds left, but never over a failed `last_resolver`) and shown in the admin
  (Kaynaklar card line "Son çözümleme: ok/hata - süre", event detail block "Son oynatma çözümlemesi").
  A candidate that produced no streams is always `ok=false` with a reason, also when one of its stages reported
  success (`okru.metadata başarılı görünüyor ama akış yok`); a candidate left out by a time limit says which one
  (`zaman aşımı: ilk akıştan sonra 6 sn bekleme süresi doldu` / `aday süresi (12 sn) doldu` / `toplam süre (20 sn) doldu`).
  Live check after a deploy: `GET /api/streams/<id>?episode=<ep>` then the admin `last_resolver` of the site
  (`/api/ops/overview`, `sites[].last_resolver.candidates`).
- **Audio / subtitle tracks** (`library/tracks.py`, `subtitles.py`, `routers/subtitles.py`, `langs.py`): `providers/vidmolly.py`
  `subtitle_tracks()` reads the embed page's JW Player `tracks` array (a JavaScript literal: `var baseTracks = [{file, kind,
  label, "default"}, ...]`; `thumbnails` = the seek sprite is ignored) into `resolved_payload.subtitles` (`RESOLVER_VERSION` 5;
  a parse failure is logged + traced as stage `vidmolly.tracks` and only loses the subtitle). `streams()` adds to every stream
  `variant_id` (stable provider-file id), `audio_lang`, `sub_mode` (`hard`|`soft`|`none`), `hard_lang`, and the response root gets
  `subtitles[]` + `audio[]` (additive: old fields unchanged, see API.md). Facts, not guesses: yabancidizi's Turkish-subtitle VidMolly
  file is burned-in (`hard`/`tr`), the English one is clean with a soft VTT, a hard file takes its audio language from the clean sibling of
  the same source; OK.ru stays `none` (never measured: the tab's "Türkçe altyazı" is the site's claim, so it is not put in the label:
  `OK.ru · 1080p`). Later additive stream fields: `sub_known` (subtitle state measured: VidMolly / soft track found; false = "none" means
  unknown), `site_lang_hint` (language code of the page's tab, a claim), `mirror_of` (`"<variant_id>:<quality>"` of the primary stream when
  this one is a spare copy, else null). Duplicates: two candidates leading to one VidMolly file (`/dl/<code>` link + `/api/moly` hand-off)
  each fetch the embed page and get a freshly signed `master.m3u8` (same host + path, other `s`/`t`): `tracks.file_identity` (provider + host
  + path; root-path URLs such as OK.ru keep the query minus signing keys) merges them into ONE stream; the same file + quality on another
  host/path is kept as a spare (`tracks.link_mirrors`: `mirror_of`, label `... · yedek` / `yedek 2`, placed right behind its primary,
  `variant_id` shared so the client's track panel gets no second row). Menu order: measured Turkish-subtitle files, then files of unknown
  subtitle state (OK.ru), then English ones; `(2)` remains only for two different files with identical labels. `GET /api/subtitles/<id>.vtt`: `<id>` = sha1 of the source URL registered server-side
  (`data/subcache/<id>.src`, survives restarts); download via `fetch.fetch_limited` (host allow-list re-checked on every redirect,
  `SUBTITLE_MAX_BYTES` 1 MB, `SUBTITLE_TIMEOUT` 5 s total), converted to clean WebVTT (BOM, SRT, `MM:SS.mmm` -> `HH:MM:SS.mmm`),
  cached in `data/subcache/<id>.vtt` (`SUBTITLE_CACHE_TTL`; a stale copy beats an error; a dead source is not asked again for 60 s),
  `ETag` + `Cache-Control` + `Access-Control-Allow-Origin: *`, 404 when dead (the TV silently shows no subtitle).
- **Stream proxy** (`app/streamproxy.py`, `routers/stream_proxy.py`, `routers/streams.py public_streams`): some signed file URLs only
  work for the client that resolved them (OK.ru mp4: `srcAg=CHROME` binds the URL to the User-Agent of the metadata request, so Safari /
  Tizen AVPlay / ExoPlayer get HTTP 400). A stream may therefore carry `request_headers` (only `User-Agent` / `Referer` / `Cookie` /
  `Origin`; the OK.ru provider sets its `User-Agent` from `fetch.USER_AGENT`, the `player_page` / `json_api` resolver types from their
  `stream_headers` parameter; `RESOLVER_VERSION` 7). The stored `resolved_payload` keeps the direct URL + `request_headers`; `/api/streams`
  rewrites those direct-file streams (and, for the reasons listed below, hls ones; never `embed`) to
  `<base>api/stream-proxy/<token>` with `proxied: true`, `request_headers` is never in the answer (every stream has `proxied`). `<base>` is the
  address the client used: `X-Forwarded-Proto` + `X-Forwarded-Host` (or `Forwarded`) behind a tunnel, else the `Host` header + scheme (a LAN
  client stays on the LAN), `STREAM_PROXY_BASE_URL` only as an explicit override; invalid header values are ignored, no usable base = the stream
  is left direct. Token = base64url(JSON `{u, h, exp}`) + `.` + HMAC-SHA256 (`STREAM_PROXY_SECRET`, empty = random key kept in
  `data/stream_proxy.key`, 0600; TTL `STREAM_PROXY_TTL` 6 h; bad format / signature 403, expired 410; the token holds no base address). The
  endpoint `GET|HEAD /api/stream-proxy/<token>` only serves such tokens (no open relay), checks the target with `netguard.check_url` on every hop
  (private / loopback / link-local / non-80-443 refused, redirects followed by hand, max 3, a cookie never follows to another host), takes
  the headers from the token only (the player's own User-Agent is ignored), passes `Range` / `If-Range` on and the 200 / 206 / 416 answer with
  `Content-Type` / `Content-Length` / `Content-Range` / `Accept-Ranges` back (streamed, upstream closed when the client goes), at most
  `STREAM_PROXY_MAX` (8) connections at once (503 above). The file's bytes pass through the server (bandwidth). Playback reports are unaffected
  (`attempt_token`).
- **Which streams are proxied** (`streamproxy.proxy_reason`, `streams[].proxy_reason`): `ua` (a file with `request_headers`), `recipe` (provider recipe key
  `stream_proxy: true`), `ip` (googlevideo `videoplayback` + `ip=`/`ipbits=`: only the server's address can fetch it), `learned`
  (`video_sources.proxy_required`, see the diagnosis below), `env` (`STREAM_PROXY_FORCE=1` = every mp4 / hls stream, `STREAM_PROXY_HOSTS` = listed hosts
  and their subdomains). `embed` is never proxied. HLS is proxied too: `url = <base>api/stream-proxy/<token>/index.m3u8` (`type` stays `hls`; the name is
  a hint for players that pick a parser by extension, the server ignores it). The proxy fetches the playlist with the token's headers (`#EXTM3U` is required,
  the extension may be `.txt`), replaces EVERY URI in it (segment lines, `URI="..."` of `#EXT-X-KEY` / `-MAP` / `-MEDIA` / `-I-FRAME-STREAM-INF`, a
  master's variant playlists; relative URIs resolve against the playlist's own URL) by a fresh signed proxy URL (token `{u, h, exp, k: playlist | segment | key,
  g: group}`; a `Cookie` is only sent to the token's own host) and answers `application/vnd.apple.mpegurl`; segments and keys are streamed with `Range` /
  `If-Range`. Every hop is `netguard`-checked (a segment on a private address is refused when it is fetched). The connection limit is per stream group
  (`STREAM_PROXY_HLS_MAX`, a request over it waits up to `STREAM_PROXY_QUEUE_WAIT` seconds, then 503); progressive files keep `STREAM_PROXY_MAX`.
  Risks: a long playlist is large after rewriting (about 450 bytes per URI; `STREAM_PROXY_PLAYLIST_URIS` caps it) and all video bytes pass through the
  server (bandwidth), so `STREAM_PROXY_FORCE` is for diagnosis only.
- **Playback failure diagnosis** (`library/streamdiag.py`): a failed `POST /api/playback-report` (optional `detail`, <= 120 characters) probes the source's
  streams from the server in the background (once per source per `STREAM_DIAG_COOLDOWN` = 10 min, `STREAM_DIAG_PARALLEL` = 2 at a time; the answer is never
  delayed): a ranged GET for a file, the playlist for HLS, `netguard` on every hop. The verdict is stored in `video_sources.last_diag` (JSON <= 600 bytes:
  `code`, `http`, `ct`, `ms`, `at`, `note`): `reachable`, `forbidden` (401 / 403), `not_media` (200 with an HTML / JSON page or a playlist without `#EXTM3U`),
  `gone` (404 / 410), `timeout`, `expired` (the URL's own `expire` hint is past), `ip_bound`, `hls_unsupported_browser`, `server_blocked` (the host
  refuses the server too: the proxy cannot help), `unreachable`. A stream that is refused without its own `request_headers` but answers with them, or
  that carries `ip=` and answers the server, is LEARNED: `proxy_required = 1`, the stored resolution is dropped, the next `/api/streams` proxies the source
  (`proxy_reason: learned`). Nothing is learned without that evidence. A failure of an all-HLS source reported by the browser's own player
  (`engine: html5`, desktop Chrome / Firefox / Edge `User-Agent`) is a client capability, not a source fault: it is NOT counted (no suspect / broken, the
  stored link stays). The admin Kütüphane source list shows the verdict (`diagnosis` of `GET /api/ops/library/{id}/videos`); K/T is unchanged.
- **yabancidizi "Mac" button** = the site's own player (`/api/drive/<token>` -> iframe `ydx.molystream.org/embed/...`):
  not resolved (no provider module; an unknown host ends the hand-off chain).

### Trailer liveness (`library/trailer_check.py`)

A source site can hand us a YouTube trailer that was later deleted / made private / made non-embeddable (the TV plays
it in an iframe). When a detail page opens (`routers/detail.py`), and when a trailer is about to be streamed
(`videos.streams`), the trailer's YouTube id is taken from the locator (`/embed/<id>`, `watch?v=`, `youtu.be/`) and
checked with `https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v=<id>&format=json`
(shared `httpx.Client`): `200` alive, `404` gone/private, `401/403` embedding disabled (both = dead). Other hosts are
not verified (assumed alive).
- **Cache**: table `trailer_checks` (per video id; alive `TRAILER_OK_TTL` 24 h, dead `TRAILER_DEAD_TTL` 6 h). A timeout,
  network error, 429 or 5xx is inconclusive: nothing is stored and the trailer stays "unknown = assumed alive". One probe
  per id at a time (concurrent detail requests share it). A changed trailer URL is a different id, so it is verified anew.
- **Budget**: the detail/streams response waits at most `TRAILER_CHECK_BUDGET` (2.5 s) for a check; over budget = unknown,
  the probe finishes in the background and the next view is answered from the cache.
- **Effect (detail)**: all active trailer sources dead -> `availability.has_trailer=false`, additive
  `availability.trailer='dead'` (`ok` / `unknown` otherwise), and `playback` `trailer` -> `unavailable` (a full source keeps
  `video`). The TV client draws the "Fragmanı Oynat" button only when `has_trailer` / `playback=trailer`, so a dead trailer
  simply has no button. `GET /api/streams/{id}?kind=trailer` returns `streams: []` for a dead trailer.
- **Storage**: `video_sources.trailer_dead` (+ `trailer_checked_at`) mirrors the verdict for the matching trailer rows
  (catalogue snapshot `availability`/`playback` after the next refresh, which a flipped flag triggers, coalesced). It is
  NOT playback health: `status`/`failures` are untouched, so the admin **K/T** count never changes; the admin video list
  shows an "Ölü fragman" badge (`GET /api/ops/library/{id}/videos`: `videos[].trailer_dead`, `summary.trailers_dead`).
  A new trailer URL on ingest is a new row (flag 0); admin "yeniden dene" on a trailer row forgets its verdict.
- `TRAILER_CHECK=0` switches everything off. Tests: `tests/test_trailer_check.py` (mocked oEmbed; `_sandbox.py` sets
  `TRAILER_CHECK=0` so no other test can reach YouTube).

Browser rendering is a transport concern and always uses Obscura. Catalogue
extractors and provider resolvers consume the same page-bundle contract without
knowing the browser implementation. The pinned no-render stealth build executes
JavaScript/DOM work without screenshots or native media. Crawlee remains a
small, browser-free worker for sites whose YAML selects `fetch_mode: http`.
Install or refresh the isolated runtimes with:

```bash
sudo server/tools/install_crawler.sh
# or only Obscura:
sudo server/tools/install_obscura.sh
```

## Categories (`app/rows.py`)

Under the real (`library`) layout `/api/boot` emits rows in order:
`continue` → `yakinda` (Yakında Vizyonda / upcoming) → `new` (Yeni Eklenenler)
→ `top10` → `mylist` → **21 real sinemalar genres** (membership from the real
`/filmler/<tür>` list pages, e.g. aksiyon, komedi, dram, korku, bilim-kurgu,
gerilim, romantik, animasyon, belgesel, macera, suç, fantastik, aile, savaş,
tarih, müzikal, western, gizem, biyografi, spor, gençlik). `continue` and
`mylist` are omitted when empty. **Staged load**: only `continue` and `new`
arrive `loaded: true` with items; genres and `yakinda` are lazy (`loaded:false`,
fetched via `/api/row/{id}`).

## Admin (`/admin`)

Tek sayfa "Olay defteri", açık/koyu tema, vanilla JS, build/CDN yok (`app/static/admin/`),
token yok (kapalı LAN). Üstte canlı iş çubuğu (2.5 sn polling, iş bitince yenilenir);
kaynak süzgeçleri her kaynak için sağlık şeridi (son 24 tarama), hız ve süre sparkline'ı
gösterir; altta tarama/heal/cooldown/geri alma olaylarının birleşik zaman çizgisi; sağda
seçilen olayın detayı (süre, öğe/sayfa, hız, hata, drift nedenleri, heal önce/sonra
selector diff, provider, TMDB sayaçları varsa, onaylı **Geri al**, **Tara**, **Heal**
(drift yoksa force onayı), kaynak başına katalog sayısı). Prototipler `app/static/admin-next/`.

API (`app/routers/ops.py`): `GET /api/ops/overview` (site başına `recent` son 24 run,
`catalog.item_count/recent_items`), `GET /api/ops/active`, `GET /api/ops/runs`,
`GET /api/ops/heals`, `GET /api/ops/events[?site=&limit=&before=]` (run+heal birleşik,
`kind` scan|heal|cooldown|rollback, `next_before` imleci, heal için `can_rollback`),
`POST /api/ops/sites/{site}/scan|heal[?force=true]|rollback` (rollback bir `rolled_back`
olayı kaydeder). Geçmiş `data/scraper_state/_ops.json` (son 200 run + 200 heal), aktif
işler bellek içi. Site devir notu: `data/site_handoffs/<site>.md` (≤6 KB; düzenleme/heal ajanı bunu okur, `GET /api/ops/sites/{site}/handoff`, admin Siteler "Devir notu"; site silinince silinir). Bakım uçları `/api/admin/video-sources`, `/api/admin/identities`
UI'sız korunur.

### Ayarlar sekmesi (`/admin#settings`)

Otomatik tarama ve heal davranışı `.env` yerine panelden yönetilir; **yeniden başlatma gerekmez**.
Kalıcı değerler `data/ops_settings.json` (atomik yazım; bozuk/eksik dosya = varsayılanlar). `.env`
(`INGEST_SITES`, `INGEST_INTERVAL` sn, `SCRAPER_HEAL_AUTOAPPLY`) yalnızca **varsayılandır**: bir
site/ayar panelden kaydedilene kadar geçerlidir, kaydedilince panel değeri kazanır.
- **Otomatik tarama**: `scraper/configs/*.yaml` sitesi başına aç/kapat + aralık (15 dk, 1/3/6/12/24 sa
  veya özel; 0.25–168 saat), son çalışma, sonraki planlı zaman (geri sayım), "Şimdi tara". Hiçbiri açık
  değilse üstte "Otomatik tarama kapalı" uyarısı. Zamanlayıcı (`app/autoscan.py`, `cache.py`) her 60 sn'de
  bir "tick" atar (açılıştan ~60 sn sonra başlar): vadesi gelen (son run + aralık ≤ şimdi, hiç çalışmamış
  dahil) siteleri sırayla ingest eder (`trigger="scheduled"`), çalışan site atlanır, siteler arası 5 sn
  bekler; hata loglanır ve run geçmişine `error` olarak yazılır.
- **Kendi kendini düzeltme (LLM)**: `heal_autoapply` anahtarı (kapalıyken LLM öneri üretir ama uygulamaz;
  her heal'de dinamik okunur), sağlayıcı/model/cooldown (salt-okunur, `.env`), ve **LLM hesabı** kartı:
  `pi auth check --provider <modelin provider öneki> --json --no-refresh` (10 sn timeout; `--credentials`
  yok, çıktıda token dönmez; yalnızca durum + bitiş/yenileme zamanı). Rozet: geçerli / süresi dolmuş /
  hata / pi yok. Süresi dolmuşsa sunucuda `pi` ile yeniden giriş yapılır.

- **TMDB zenginleştirme** kartı: anahtar var mı rozeti (değer asla gösterilmez), kapsam sayıları ("418
  filmden 34 TMDB'li, 384 posteri yer tutucu"), **"Yeni öğeler için otomatik zenginleştir"** (`tmdb_auto`,
  varsayılan açık; ingest sırasında arka planda, eşleşmeyenler `TMDB_RETRY_DAYS`=7 gün sonra tekrar
  denenir; anahtar yokken etkisiz), tür kutuları film/dizi (`tmdb_types`, varsayılan `.env`
  `TMDB_ENRICH_TYPES`), **Önizleme (yazmadan)** / **Uygula (eksikleri doldur)** (bkz. "TMDB > Admin backfill"); tür seçicisinin
  üçüncü seçeneği **Sezon ve bölüm görselleri** (dizi sezon posterleri + bölüm başlık/özet/tarih/süre/görsel: aynı
  önizleme/uygula akışı, kapsam satırı "sezonlu diziler / sezon posteri / bölüm görseli", bkz. "TMDB > Seasons and episodes").
  Ayarlar ingest/enrich tarafından her seferinde dinamik okunur; `.env` yalnızca varsayılan.

API (`app/routers/ops_settings.py`): `GET /api/ops/settings` (site başına `enabled`, `interval_hours`,
`last_run_at`, `next_scan_at`, `running`, `heal_cooldown_until`; `any_enabled`; `heal`
`{autoapply, enabled, provider, model, cooldown_seconds}`; `tmdb` `{configured, auto, types}`),
`PUT /api/ops/settings` (kısmi: `{"sites": {"<site>": {"enabled"?, "interval_hours"?}}, "heal_autoapply"?,
"tmdb": {"auto"?: bool, "types"?: ["movie","series"]}}`; bilinmeyen site / aralık sınırı / tip hatası /
`tmdb.configured` (salt-okunur) 422), `GET /api/ops/llm/health` (60 sn önbellek), `POST /api/ops/llm/health`
(önbelleği atlar, "Test et"). Testler: `tests/test_ops_settings.py`, `tests/test_ops_tmdb.py`.

### Kütüphane sekmesi (`/admin#library`)

Üst menüde "Olay defteri" yanında "Kütüphane": tüm kaynaklardan taranmış merkezi kütüphane
(`library_items`, SQLite; `SOURCE` mock olsa da tablolar okunur, boşsa "Henüz veri yok").
Grid (poster kartı) / liste görünümü; poster TMDB varsa o, yoksa kaynak posteri (`/img/{id}/portrait?w=200`,
lazy-load), detayda afiş (`/img/{id}/card?w=500`). Satırda başlık, yıl, tür, kaynak site(ler), TMDB durumu
(`matched` eşleşti · `review` inceleme · `unmatched` yok/denenmedi), imdb id ve **K/T** rozeti.
Rozete ya da öğeye tıklayınca video kaynakları listelenir (site, host/sağlayıcı, durum, son hata,
kontrol/deneme/başarı zamanı; kırıklar önce).

**K/T** = `video_sources` (`library/videos.py` sağlığı) üzerinden öğe başına:
`T` = devre dışı (`disabled`) olmayan tüm video kaynakları (film, bölüm, fragman), `K` = `status='broken'`
(3 ardışık oynatma hatası). `status='suspect'` (1-2 hata) **K'ya katılmaz**, rozette ayrı küçük `?N` işaretiyle
görünür. **Ölü fragman** (YouTube videosu silinmiş/özel/gömülemez, `video_sources.trailer_dead`) de K'ya katılmaz ve
`status`'u değiştirmez: kaynak listesinde satırda "Ölü fragman" rozeti, özet kartında sayısı görünür. `1/3` = 3 kaynağın 1'i kırık. Renk: yeşil hepsi sağlam (T>0, K=0) · sarı/turuncu kısmen kırık
(0<K<T) · kırmızı tamamen kırık (K=T>0) · gri `0/0` kaynak yok. Rozet öğenin TÜM kaynaklarını sayar
(kaynak süzgeci yalnızca öğenin hangi siteden tarandığını süzer).

API (`app/routers/ops_library.py`, tek geçici bağlantıda tek GROUP BY + TEMP tablo; 600 öğede ms düzeyi,
`idx_video_health` kapsayıcı indeks):
`GET /api/ops/library?site=&broken=&kind=&tmdb=&q=&sort=&limit=50&offset=0[&facets=0]`
- `site` tekrarlı ya da virgüllü (çoklu, OR); `broken` = `ok` | `partial` | `dead` | `suspect` | `none` (boş/`all` = hepsi);
  `kind` = `movie` | `series`; `tmdb` = `matched` | `review` | `unmatched`; `q` başlık/özgün başlık/yıl,
  büyük-küçük harf ve Türkçe aksan duyarsız, sözcüklerin hepsi geçmeli; `sort` = `recent` | `title` | `broken`
  (en çok kırık: K, sonra K/T oranı, sonra şüpheli). Geçersiz değer 422.
- Yanıt: `items[]` (`videos:{total,broken,suspect,disabled,trailers,state,label}`), `total` (süzgeç sonrası),
  `library_total`, `has_more`, `source_mode`, `facets` = `sites[{site,count}]`, `broken{all,ok,partial,dead,suspect,none}`,
  `kind`, `tmdb`. Her facet kendi süzgeci hariç diğerlerini uygular; `facets=0` "daha fazla" isteklerinde atlanır.
`GET /api/ops/library/{id}/videos[?limit=500]` -> `summary` (K/T, sağlam/doğrulanmadı/devre dışı) + `videos[]`
(`status`, `site`, `host`, `providers`, `last_error`, `failures`, `last_checked_at`, `last_success_at`,
`last_attempt_at`, `last_failure_at`, `trailer_dead`/`trailer_checked_at` (yalnızca fragman satırları); `summary.trailers_dead`;
akış URL'si/token dönmez), 404 bilinmeyen öğe.
Testler: `tests/test_ops_library.py`.

## LLM heal setup (host `61`)

`SCRAPER_HEAL_PROVIDER` selects the backend:
`codex_cli` (subscription codex CLI, default) | `pi` (pi CLI, one-shot spawn per
heal, subscription-authed via `~/.pi/agent/auth.json`, no API key) |
`pi_agent` (the onboarding skill in **repair mode**: a pi agent with the sandbox tools
inspects the pages, tests yaml / provider recipes and hands in a proposal with `submit_repair`;
serves the list-drift heal AND the playback-triggered heal / "no sources" repair of
`scraper/heal_agent.py`; every proposal passes the same gates: no field dropping, baseline, the
failing and the working examples, every other user of a changed provider recipe; a repair is also
scoped to the LAYER the evidence points at, e.g. list / detail / resolvers / provider recipe: a proposal
touching other yaml keys is rejected as `scope: touched ...`, and the ops heal record carries `layers`
and `touched_keys`; see
`pi/skills/diziflix-site-onboarding/references/heal.md`; run limit `SCRAPER_HEAL_AGENT_TIMEOUT`,
default 300 s) | `openai_compatible` (future). On `61` heal runs autonomously with the `pi`
provider, model `openai-codex/gpt-5.6-terra`, and autoapply on (admin **Ayarlar** tab;
`SCRAPER_HEAL_AUTOAPPLY` is only the default until saved there) to version + activate
validated configs. Account validity (`pi auth check`) is shown in the same tab.

## TMDB

`app/library/tmdb.py` (client) + `app/library/enrich.py` (orchestration). Set
`TMDB_ACCESS_KEY` (v4 bearer JWT `eyJ...` or v3 api key, auto-detected; the older
`TMDB_TOKEN` / `TMDB_API_KEY` still work). Without a key everything is skipped.

- Matching: `search/movie` (tr-TR, then en-US; extra queries only when nothing plausible
  turns up: dotless-`ı` slips, `'99` -> `1999`). Titles are compared after folding
  (accents, `ı`, escaped quotes), roman/arabic numerals (`XXVI` = `26`) and leading
  `WWE`/`The`; a shared main title before `:`/` - ` with a different or missing subtitle
  is capped at 0.85. score = similarity*0.7 + year (+0.3 same, +0.15 off by one, -0.1 when
  both years are known and >1 apart; no year -> similarity*0.7).
  **Auto-match** (tmdb_id, imdb_id) needs score >= `TMDB_AUTO_SCORE` *and* title similarity
  alone >= `TMDB_MIN_SIM` (a matching year never rescues a weak title), year within +-1,
  and a lead of `TMDB_MARGIN` over the runner-up. A source **without a year** is
  auto-matched only for a near-exact title (`TMDB_MIN_SIM_NOYEAR`) with that lead; a source
  year with a candidate that has none is never auto. Everything else at or above
  `TMDB_REVIEW_SCORE` goes to `identity_reviews` (`tmdb_low_confidence`, artwork untouched);
  below it is unmatched.
- Alternative titles: when no candidate is auto-matchable, the best `TMDB_ALT_TITLE_LOOKUPS`
  (default 3, 0 = off) plausible candidates (year not clearly different) are fetched with
  `append_to_response=alternative_titles,translations` and re-scored against those names
  (rescues Japanese/Korean/Turkish-only hits; flagged `via alt title` in `--verbose`).
  Worst case per title: 2 base + 2 fallback searches + 3 alt lookups + 1 details.
- Artwork: TMDB poster/backdrop are stored in `library_items.tmdb_poster_url` /
  `tmdb_backdrop_url` and served instead of the source image; the source image is the
  fallback (no key, no match, error). Other fields only fill gaps, never overwrite the source.
- Failures never fail ingest. Errors are retried next ingest; unmatched/review titles
  are not re-queried for `TMDB_RETRY_DAYS`; matched titles are never re-queried.
  429 honours `Retry-After`. Each ingest run reports `tmdb_enrich` counters
  (matched/unmatched/review/skipped/deferred/errors/seconds).
- Series: off by default; select **Dizi** in the admin Ayarlar TMDB card (or `TMDB_ENRICH_TYPES=movie,series`
  as the default). Same matcher on the TV path: `search/tv` + `tv/{id}` (`external_ids,images`; alt/translated
  titles via `alternative_titles,translations`), year = `first_air_date`, title = `name`/`original_name`, same
  poster/backdrop choice (tr, no-language, en), same thresholds/lead/no-year/numeral rules. For search and scoring
  only, `Sezon N` / `N. Sezon` / `Season N` / `(Dizi)` are stripped from the source title (the stored title is
  never changed). Only the series' main `library_items` row (+ `external_ids`, `source_items.normalized` gap
  fill) is written; season/episode rows (`video_sources`) are never touched. Series runtime = TMDB episode runtime
  (`episode_run_time`, else `last_episode_to_air.runtime`); may stay empty.
- The selected types (`settings.tmdb_types()`, admin Ayarlar; default = `TMDB_ENRICH_TYPES`) and the auto switch
  (`tmdb_auto`) are read on every ingest/backfill, no restart.
- Backfill existing library (CLI): `python -m tools.tmdb_enrich [--type movie|series] [--limit N] [--dry-run] [--force] [--verbose]`.
  `--verbose` adds one line per title: `[auto|review|unmatched] source (year) -> candidate (year)
  tmdb=ID score=S | reason` (no key is ever printed); without it the output stays counters-only.
  The CLI and the admin job share `enrich.backfill(kind, dry_run, limit, on_progress)`.
- **Seasons and episodes** (`app/library/seasons.py`): for TMDB-matched series, season posters and every episode's
  title / overview / air date / runtime / still.
  - Requests: `tv/{id}/season/{n}?language=tr-TR`, plus one `en-US` request for that season only when a season
    overview, an episode overview or an episode title is blank/generic ("Bölüm 3") in tr-TR (blank fields are then
    filled from en-US; tr-TR always wins when real). Only seasons the library really has are asked (`video_sources`
    episode rows ∪ the episode list in `source_items.normalized`) - never all seasons TMDB knows. Same env, timeout,
    429 (`Retry-After`) and budget rules as the title lookups (`tmdb._get`, `TMDB_BUDGET_SECONDS`, `TMDB_CONCURRENCY`).
  - Storage - `library_seasons(canonical_id, season, name, overview, air_date, tmdb_poster_url, status, checked_at)`
    and `library_episodes(canonical_id, season, episode, title, overview, air_date, runtime_minutes, tmdb_still_url,
    checked_at)`, created by `db.init()` (`CREATE TABLE IF NOT EXISTS`, so an old database just gains them).
    Why separate tables and not `video_sources` columns: a provider row is per site *and* per video (one episode can
    have several), TMDB metadata is per (series, season, episode) - it would be duplicated per provider, lost when a
    provider disappears, and DATA-CONTRACT §6 already names `library_episodes` as the target ("metadata without any
    video"). `identity.merge` moves the rows with the identity (and aliases `<old>:s<n>` season ids).
  - Source data is never overwritten (`video_sources` / `source_items` are not written): the catalogue merges at read
    time - source title / overview / runtime win when non-empty (a title that is only a label like "3. Bölüm" counts
    as empty), TMDB fills the blanks, `air_date` comes from TMDB; artwork = TMDB first, the source's still as
    fallback (posters: same rule as titles).
  - Stamps: TMDB answered -> `status='ok'`; TMDB has nothing (404 / no usable data) -> `status='empty'`, not asked again
    for `TMDB_RETRY_DAYS`; network / 429 / auth errors are **not** stamped (retried next run; 3 errors in a row stop
    the batch). A stored `ok` season is only asked again when the library got episodes it lacks *and* the stamp is older
    than `TMDB_RETRY_DAYS` (or with `force`). A blank never wipes a stored value.
  - Automatic: `ingest` (after the merge, for the series it touched, same TMDB time budget, ingest lock already held;
    `tmdb_auto` + `series` in `tmdb_types` apply; result key `tmdb_seasons`), when a series' season list is hydrated on
    open, and when a matched series with seasons is opened (`schedule_seasons`, background, at most every 5 min per series).
  - Admin: the TMDB card's **Sezon ve bölüm görselleri** selector = `POST /api/ops/tmdb/backfill {kind: series,
    scope: "seasons", dry_run, limit?, force?, use_preview?}` (422 with `kind=movie`; same 409/400 rules). Preview:
    per series the seasons/episodes found, sample season posters/stills (`GET /api/ops/tmdb/preview?scope=seasons`,
    stored in `data/tmdb_seasons_preview.json`, 30 min, apply-from-preview makes no TMDB request); apply writes under the
    ingest lock per chunk of 10 series, job bar `TMDB sezon ve bölüm görselleri (dizi) x/y`, `kind=tmdb` event with
    `scope=seasons` (series/seasons/episodes/posters/stills/empty/errors), then `cache.refresh_and_prewarm()`.
    `GET /api/ops/tmdb/status` adds `coverage.seasons` (`series`, `series_tmdb`, `seasons`, `seasons_checked`,
    `season_posters`, `episodes`, `tmdb_stills`, `episode_stills`) and `season_preview`. The admin Kütüphane detail of a
    series shows a season poster strip (`GET /api/ops/library/{id}/videos` -> `seasons[]`).
  - CLI: `python -m tools.tmdb_enrich --seasons [--limit N] [--dry-run] [--force]` (limit counts series).
  - Images: `/img/{cid}:s{n}/portrait` (season poster; falls back to the series poster) and
    `/img/{episode_id}/still` (TMDB still, else the source still, else the generated placeholder) go through the
    normal `/img` proxy (allow-list, ETag, disk cache). `TMDB_STILL_SIZE` (default `original`, ~270 KB raw; `w300`
    saves disk) and `TMDB_POSTER_SIZE` pick the TMDB size. Prewarm downloads season posters after every catalogue
    refresh but **not** episode stills (thousands): those are fetched on the first `/img` request and cached on disk;
    `IMG_PREWARM_STILLS=1` prewarms them too.
- **Admin backfill** (`app/library/tmdb_admin.py`, `app/routers/ops_tmdb.py`; Ayarlar > TMDB zenginleştirme):
  - `POST /api/ops/tmdb/backfill` `{kind: movie|series, dry_run, limit?, force?, use_preview?}` starts one in-process
    background job (`dry_run` defaults to true = preview only). 409 already running; 400 "TMDB anahtarı tanımlı
    değil" / type not selected; 422 bad body. Only one TMDB job at a time; it appears in the admin job bar
    (`/api/ops/active`) as `TMDB zenginleştirme (film|dizi) x/y`. DB writes take the ingest file lock per chunk, so
    a running ingest and the job never write the same item concurrently (the job waits for the scan).
  - `GET /api/ops/tmdb/status`: `configured` (bool only), `auto`, `types`, `retry_days`, `coverage` per type (`total`,
    `matched`, `review`, `unmatched`, `tmdb_poster`, `no_poster` = placeholder/absent poster, `no_backdrop`), running
    `job` + progress, `last_job`, last `preview` summary, `prewarm` (image download in progress).
  - `GET /api/ops/tmdb/preview?decision=auto|review|unmatched|error&limit=&offset=`: last preview, per title source
    title (year) -> candidate (title, year, tmdb_id, small `poster_url`), score, decision, reason.
  - A preview (`dry_run=true`) writes nothing but `data/tmdb_preview.json` (atomic). `dry_run=false` right after a fresh
    preview (< 30 min, same type, not yet applied) applies exactly the previewed **auto** decisions without querying
    TMDB again; otherwise it runs live. Only `auto` matches change item data (poster/backdrop always TMDB, summary /
    genres / rating / runtime only when empty); review/unmatched keep the existing bookkeeping (status stamp +
    `identity_reviews` queue, 7-day retry window). Rows changed since the preview (matched by an ingest, gone) are skipped.
  - Finish: a `kind=tmdb` event (matched/unmatched/review/skipped/errors/seconds, dry-run, written) lands in
    `/api/ops/events` (admin "TMDB" filter) and, after a real apply, `cache.refresh_and_prewarm()` runs so the new
    artwork is pulled into the server image cache ("Resimler indiriliyor…").

## Deploy

Deploy to `61` uses **tar-over-ssh** (rsync unavailable), excluding AppleDouble
cruft (`--exclude "._*"`). `.env`, `data/`, and `venv/` are preserved across
deploys.

## Periodic refresh

Scheduled ingest is managed in the admin **Ayarlar** tab (per site on/off + interval, no
restart; see above). `INGEST_SITES` / `INGEST_INTERVAL` (`.env`) are now only the
**defaults** until a site is saved there (`INGEST_SITES` empty / `INGEST_INTERVAL=0` =
off by default — the default on `61`, where the catalogue was ingested once and is served
statically). A 60 s tick starts ingests that are due; or run `python tools/ingest.py
<site>` manually.

**Mid-scan visibility.** The TV client reads the in-memory snapshot (`cache.py`), not the database, so a scan
used to be invisible to it until it ended (minutes). Now the snapshot is rebuilt *during* the scan, through a
progress hook (`ingest.set_progress_hook`; `cache.start()` binds `cache.refresh_progress`, so the ingest never
imports the cache): after the title rows are committed (`titles`, once; also starts the artwork prewarm), after
each series whose episode inventory was written (`inventory`), and after the TMDB season pass (`seasons`).
Every stage but `titles` is debounced to `INGEST_REFRESH_MIN_INTERVAL` seconds (default 20) after the previous
mid-scan refresh, and only one refresh runs at a time (a call that arrives meanwhile is skipped, never queued).
It behaves the same for the admin scan, the scheduled tick and any other in-server ingest; the CLI
(`tools.ingest`, `tools.series_crawl`) has no hook and only writes the database. The full refresh at the end of a
scan (`cache.refresh()` after the admin scan, `refresh_and_prewarm` after a tick) is unchanged. A hook that raises
is logged and ignored: it can never break or fail a scan. A snapshot is one consistent read view of the database
(WAL: it neither blocks nor is blocked by the scan's writes), requests keep being served from the previous
snapshot until the new one is swapped in, and an older build never replaces a newer one.

## Series inventory crawl

Why: a home/list card carries only the newest episode ("4. Sezon 10. Bölüm"), so a series used to hold
exactly that one episode until somebody opened it - and opening it did nothing when the series already "had a
season" and an overview. `app/library/series_crawl.py` now reads the **full inventory** (all seasons; episode
number, title, air date, episode page URL; series facts) of every series that needs it, as a stage of every
ingest (after the write, before the TMDB season pass) and on demand.

- **One request per series.** The site renders every season as a tab on `/dizi/<slug>` (and on each
  `/dizi/<slug>/sezon-N`), so the series page is the whole inventory. A declared season whose panel the page did
  not carry is read from its own `/sezon-N` page (capped by `SERIES_CRAWL_MAX_PAGES`). Season/episode numbers come
  from the episode URL (never guessed); the panel attribute is only cross-checked.
- **Config-as-data.** Selectors live in `configs/yabancidizi.yaml` `series_page:` (season menu, season panels, episode
  rows, `url`/`title`/`air_date` fields through the shared field engine - `cast: date_tr` turns "24 Temmuz 2026"
  into `2026-07-24` -, `unaired_classes`, first/last-episode links) with drift thresholds in
  `yabancidizi.baseline.json` `series_page`. Parsing code: `site_extractors/yabancidizi.py:series_inventory`.
  If the row selectors stop matching, the URL-pattern scan still finds the episodes (no dates; counted in
  `series_crawl.fallback`, drift reasons logged). LLM heal is NOT wired to this page type (it heals `list`/`detail`).
  Series-level facts (genres/cast/overview...) are still parsed in code (`_detail_metadata`).
- **What is stored.** Episodes go to `source_items.normalized.video_sources` and, through the normal
  `videos.sync_source`, to `video_sources` (same ids `vs_...`, same canonical episode id `{cid}:s{n}:e{m}`; `resolver=page`,
  only the episode PAGE url - no host is resolved, the player is found at playback time). New column
  `video_sources.episode_air_date` (source air date; API `episodes[].air_date`: source first, TMDB fills). Episodes
  announced but not aired (`not_yet` rows) are not written; `_series_inventory.next_air` remembers the date. Trailer ->
  existing `trailer_url` / `kind=trailer` (`embed`). Extra facts kept in `normalized` only: `cast_photos`
  (name + photo URL; TMDB credits will be the real source), `overview_en` (the English half of the site summary),
  `genres_unmapped`. Genres map to the central labels (`app/genres.py`, same dictionary as `/api/genres`).
  Existing rows are only updated (COALESCE: an empty value never blanks a known one, a real title is never
  replaced by "N. Bölüm"); nothing is deleted; status/failures/`playback_attempts` are untouched.
- **First/last episode check.** The site's "Dizinin İlk/Son Bölümünü İzle" links must equal the first/last listed
  (aired) episode; a mismatch is logged (`series inventory <site>/<key>: ...`), kept in `_series_inventory.warnings`.
- **When a series is read** (`series_crawl.due`, marker `normalized._series_inventory`): never read (`missing`,
  incl. everything already in the DB - the old `_series_catalog_at` stamp proves nothing and is ignored); a home card newer
  than the stored last episode (`new_episode`, once per announcement); an announced air date has passed (`aired`);
  unfinished/failed (`retry`, `SERIES_CRAWL_RETRY_HOURS`, doubling per failure, max x16); older than
  `SERIES_CRAWL_REFRESH_DAYS` (`stale`). A series read fine is not read again within an hour. It runs even
  when the home cards are "unchanged" and also covers series that left the home page.
- **Politeness.** One request at a time (plus the transport's own pause), `SERIES_CRAWL_DELAY` between requests,
  per-run budgets `SERIES_CRAWL_BUDGET` series (default 15; `0` disables the stage) and `SERIES_CRAWL_SECONDS` (300);
  what is not reached stays due (`deferred`) and goes first next run. A failing series never stops the others; only
  `SERIES_CRAWL_MAX_ERRORS` (5) failures in a row stop the pass (site blocking us). Per-site switch: yaml `series_crawl.enabled`.
- **Records.** Run record / `/api/ops/events` `series_crawl`: `due, series, seasons, episodes, new_episodes, unaired, pages,
  incomplete, fallback, warnings, errors, deferred, skipped, seconds, budget, stopped (budget|time|errors)`; shown in the
  admin scan detail ("Dizi bölüm envanteri").
- **On demand.** `GET /api/detail/<id>` of a series whose inventory is due schedules the background hydrate (also for a series
  that already shows a season); the result is visible on the next request.
- **Backlog / manual.** Existing libraries fill over successive scans (15 series each). `venv/bin/python -m tools.series_crawl
  yabancidizi [--dry-run] [--limit N] [--force] [--key dizi/<slug>]` runs the same pass by hand (takes the ingest lock,
  then fills TMDB season data of the crawled series; the server's catalogue refreshes on its TTL / next scan).

## Adding a source or category

- **New scraper site / category**: drop `configs/<site>.yaml` (with `collections`
  and, for playback, `stream_resolver`) plus an optional `<site>.baseline.json`.
  Add a normalizer in `app/library/normalize.py`; if the player markup is unique,
  add `site_extractors/<site>.py`. Reused provider hosts require no site-specific
  resolver change; a new host gets one module in `scraper/providers/`.
- **New catalogue backend**: add `app/sources/<name>.py` with a `SourceAdapter`
  subclass (`catalog`, `detail`, `streams`) and set `SOURCE=<name>`.

## Configuration (`.env`)

Copy `.env.example` → `.env`. Relative paths resolve against `server/`.

| Key | Default | Meaning |
|-----|---------|---------|
| `HOST` / `PORT` | `0.0.0.0` / `8090` | HTTP bind |
| `SOURCE` | `mock` | source adapter: `mock` \| `library` |
| `FIXTURE_PATH` | `data/fixture.json` | fixture for the mock source |
| `DB_PATH` | `<DATA_DIR>/diziflix.db` | SQLite (profiles, progress, my-list, library) |
| `IMG_CACHE_DIR` | `<DATA_DIR>/imgcache` | rendered/proxied JPEG cache |
| `CACHE_TTL` | `900` | seconds between catalogue snapshot refreshes (`0` off) |
| `INGEST_REFRESH_MIN_INTERVAL` | `20` | minimum seconds between two **mid-scan** snapshot refreshes (`0` = no debounce); see "Periodic refresh" |
| `IMG_QUALITY` / `IMG_MAX_AGE` | `82` / `86400` | JPEG quality · artwork `max-age` |
| `ROW_LIMIT` | `20` | max items in an eagerly loaded `/api/boot` row |
| `HOME_LAYOUT` | `continue,trending_series,series,trending_movies,noteworthy_movies,movies,mylist` | tv-v1 home rows, top to bottom (row ids; a row with an empty pool is left out) |
| `HERO_SERIES` / `HERO_MOVIES` | `3` / `3` | slider size per type (best `trend_score` titles with a backdrop, alternating series/movie) |
| `HOME_ONLY_READY` | `1` | home rows + slider list only playable (`availability.state=ready`) titles; search/catalogue unaffected |
| `CLASSIC_MIN_RATING` / `CLASSIC_MIN_AGE_YEARS` | `7.5` / `15` | "Dikkate Değer Filmler" classics: movies rated >= / released >= N years ago (no year = not a classic) |
| `INGEST_SITES` | *(empty)* | **default only** (admin Ayarlar wins once saved): comma-separated sites with auto scan on; empty = off |
| `INGEST_INTERVAL` | `21600` | **default only**: seconds between scheduled ingests (`0` = off; clamped to 15 min – 7 days) |
| `REMOTE_IMG_HOSTS` | `sinemalar.com,cdn.sinemalar.com` | `/img` proxy allow-list (SSRF guard; `image.tmdb.org` always added) |
| `TMDB_ACCESS_KEY` (or `TMDB_TOKEN` / `TMDB_API_KEY`) | *(empty)* | enable TMDB enrichment (v4 bearer or v3 key, auto-detected) |
| `TMDB_ENRICH_TYPES` | `movie` | **default only** (admin Ayarlar wins once saved): `movie,series` also enriches series |
| `TMDB_RETRY_DAYS` | `7` | do not re-query unmatched titles within this window |
| `TMDB_POSTER_SIZE` / `TMDB_BACKDROP_SIZE` | `w500` / `w1280` | TMDB image sizes (poster size also for season posters) |
| `TMDB_STILL_SIZE` | `original` | TMDB episode-still size (`w92` `w185` `w300` `original`) |
| `IMG_PREWARM_STILLS` | `0` | `1` also prewarms episode stills after a catalogue refresh (default: on demand via `/img`) |
| `DATA_DIR` | `data` | base of everything the server writes (db, image cache, ops settings, TMDB previews, scraper state); `DB_PATH` / `IMG_CACHE_DIR` override individually |
| `TMDB_TIMEOUT` / `TMDB_CONCURRENCY` / `TMDB_BUDGET_SECONDS` | `6` / `4` / `90` | per-request timeout, parallel lookups, per-ingest time budget |
| `TMDB_AUTO_SCORE` / `TMDB_MIN_SIM` / `TMDB_MARGIN` | `0.85` / `0.9` / `0.08` | auto-match: min score, min title similarity (year cannot rescue), min lead over the runner-up |
| `TMDB_MIN_SIM_NOYEAR` / `TMDB_REVIEW_SCORE` | `0.97` / `0.55` | similarity needed to auto-match a source without a year; below the review score -> unmatched |
| `TMDB_ALT_TITLE_LOOKUPS` | `3` | alternative/translated-title requests per title when the hit's own titles are not similar enough (`0` disables) |
| `SERIES_CRAWL_BUDGET` / `SERIES_CRAWL_SECONDS` | `15` / `300` | series inventory stage: series per ingest run (`0` = stage off) / wall-clock budget; the rest waits for the next run |
| `SERIES_CRAWL_DELAY` | `3` | seconds between two page requests of the stage (on top of the transport's own pause) |
| `SERIES_CRAWL_REFRESH_DAYS` / `SERIES_CRAWL_RETRY_HOURS` | `7` / `6` | re-read a complete inventory at least this often (`0` = never by age) / minimum gap after an unfinished or failed read (doubles per failure) |
| `SERIES_CRAWL_MAX_PAGES` / `SERIES_CRAWL_MAX_ERRORS` | `6` / `5` | pages per series (series page + missing season pages) / consecutive failures that stop the pass |
| `BLOCKED_RECHECK_DAYS` | `7` | content that is not public (yaml `blocked:` / `availability_gate:`, `library/gate.py`): a blocked page / series is judged again after this many days (`0` = every scan); a site that lifts the block gets its content back |
| `GATE_BUDGET` / `GATE_RECHECK_PAGES` | `40` / `3` | pages one scan may probe before writing films / card-only series (the gate; items beyond it wait for the next scan) / old blocked episode and film pages re-judged per scan (the per-series probes of the inventory stage ride on `SERIES_CRAWL_*`) |
| `RESOLVE_TIMEOUT` / `RESOLVE_RETRIES` / `RESOLVE_MIN_DELAY` | `6` / `2` / `0` | playback resolution only (`fetch.fetch_url`/`post_url`, shared connection pool): per-request timeout (s), tries (network/429/5xx; 0.3/0.8 s backoff), courtesy delay per host (s). The crawl transport (`fetch.fetch`: 2 s per host, 3 tries) is unchanged |
| `RESOLVE_PARALLEL` / `RESOLVE_SOURCES_PARALLEL` | `4` / `3` | candidates of one source (e.g. 2x vidmoly + moly + OK.ru) / sources of one episode or film resolved at once |
| `RESOLVE_CANDIDATE_TIMEOUT` / `RESOLVE_TOTAL_TIMEOUT` / `RESOLVE_GRACE` | `12` / `20` / `6` | seconds: per candidate / whole source; once one candidate has streams the others get `RESOLVE_GRACE` more seconds (slow ones are left out of that answer, with the reason in the admin trail) |
| `RESOLVE_BROWSER_TIMEOUT` | `40` | seconds: time budget of one browser-carried candidate (`player_page` with `fetch: browser`; its `discover` stamps `timeout` on the candidate). Per-candidate limit = `max(RESOLVE_CANDIDATE_TIMEOUT, candidate timeout)`, whole-source limit = `max(RESOLVE_TOTAL_TIMEOUT, longest candidate limit + 2)`; `RESOLVE_GRACE` still applies once another candidate has streams |
| `RESOLVE_FAST_FIRST` | `1` | `1` puts candidates that answered clearly faster (0.5 s buckets) first, `0` keeps the page's order |
| `RESOLVE_CACHE_TTL` / `RESOLVE_NEG_TTL` | `900` / `60` | seconds a resolved payload (`video_sources.resolved_payload`) is reused when its stream URLs carry no expiry hint (URLs may be bound to the server IP; `0` = never reuse) / a failed candidate or source is not asked again (in memory; admin "yeniden dene" bypasses) |
| `RESOLVE_CACHE_MAX_TTL` / `RESOLVE_CACHE_MARGIN` / `RESOLVE_REFRESH_AHEAD` | `21600` / `300` / `600` | link-lifetime cache (`library/streamlife.py`): a payload whose URLs say when they expire (`expires`/`expire`/`exp`, `X-Amz-*`, `/expire/<epoch>/` ...) is reused until that moment minus `RESOLVE_CACHE_MARGIN`, at most `RESOLVE_CACHE_MAX_TTL` (never below `RESOLVE_CACHE_TTL`); a reused payload that runs out within `RESOLVE_REFRESH_AHEAD` is answered from the cache and re-resolved in the background; a playback error or admin retry drops the source's payload |
| `SUBTITLE_HOSTS` | — | comma-separated hosts (or `label.*`) ADDED to the built-in subtitle allow-list `srt.vidmoly.*`, `srt.vidmolly.*` (SSRF guard of `/api/subtitles`, checked on every redirect hop) |
| `SUBTITLE_CACHE_DIR` / `SUBTITLE_CACHE_TTL` | `data/subcache` / `604800` | converted WebVTT cache + source registry / seconds a cached file is fresh (stale copy still served when the source is down) |
| `SUBTITLE_MAX_BYTES` / `SUBTITLE_TIMEOUT` | `1000000` / `5` | subtitle download limits: bytes / total seconds |
| `STREAM_PROXY_SECRET` | *(empty)* | HMAC key of the `/api/stream-proxy` tokens; empty = a random key is generated once and kept in `<DATA_DIR>/stream_proxy.key` (0600). Never logged |
| `STREAM_PROXY_TTL` / `STREAM_PROXY_MAX` | `21600` / `8` | seconds a proxy token (= a `/api/streams` answer) stays valid / simultaneous proxied connections (503 above) |
| `STREAM_PROXY_FORCE` / `STREAM_PROXY_HOSTS` | `0` / *(empty)* | `1` proxies every mp4 / hls stream (`proxy_reason: env`); comma separated hosts (subdomains too) that are always proxied |
| `STREAM_PROXY_HLS_MAX` / `STREAM_PROXY_QUEUE_WAIT` | `24` / `8` | HLS: upstream connections at a time per stream group / seconds a request waits for a place before 503 |
| `STREAM_PROXY_PLAYLIST_MAX` / `STREAM_PROXY_PLAYLIST_URIS` | `2097152` / `20000` | HLS: longest playlist read (bytes) / most URIs one playlist may hold (502 above) |
| `STREAM_DIAG` | `1` | `0` switches off the background probe after a failed playback report (`video_sources.last_diag`, learned proxy) |
| `STREAM_DIAG_COOLDOWN` / `STREAM_DIAG_PARALLEL` / `STREAM_DIAG_TIMEOUT` | `600` / `2` / `8` | seconds between probes of one source / probes at a time / seconds per probe request |
| `STREAM_PROXY_BASE_URL` | *(empty)* | base of the proxy URLs in `/api/streams`; empty = the address the client used (`X-Forwarded-Proto`/`-Host`, `Forwarded`, else `Host` + scheme). Set (`https://host[:port][/prefix]`) only to force one base for every client |
| `RESOLVE_PREFETCH` | `0` | `1` resolves the most likely source in the background when a detail page opens |
| `TRAILER_CHECK` | `1` | `0` disables the YouTube trailer liveness check (see "Trailer liveness") |
| `TRAILER_CHECK_TIMEOUT` / `TRAILER_CHECK_BUDGET` | `3` / `2.5` | seconds: per oEmbed request / longest a detail or streams response waits for a check (the rest finishes in the background) |
| `TRAILER_OK_TTL` / `TRAILER_DEAD_TTL` | `86400` / `21600` | seconds a verified-alive / verified-dead trailer verdict is reused (timeouts are never cached) |
| `SCRAPER_HEAL_ENABLED` | `false` | master switch for LLM self-heal on drift |
| `SCRAPER_HEAL_PROVIDER` | `codex_cli` | `codex_cli` \| `pi` \| `pi_agent` \| `openai_compatible` |
| `SCRAPER_HEAL_AGENT_TIMEOUT` / `SCRAPER_HEAL_VERIFY_TIMEOUT` | `300` / `420` | `pi_agent` only: longest one repair-agent run / longest server-side verification of its proposal (seconds) |
| `PLAYHEAL_STREAM_MIN_SOURCES` | `2` | different sources (episodes) of one site refused by the same stream host (the server's own probe fails too: 403 / "security error" page, `streamdiag`) within 24 h that start a repair run (evidence `stream_blocked`, layer `provider`; `blocked` and `hls_unsupported_browser` never count) |
| `STREAM_HOST_RULES` / `STREAM_HOST_RULE_TTL` / `STREAM_HOST_RULE_FAILS` | `1` / `2592000` / `2` | stream host rules (`library/hostrules.py`): a Referer / proxy the server's probe verified for one stream host is applied to every stream of that host; TTL seconds, failed probes that suspend it |
| `PLAYHEAL_ISSUE_MIN_SOURCES` / `_TTL` / `_COOLDOWN` | `2` / `86400` / `21600` | playback issue ledger (`library/playissues.py`, admin Olay defteri "Oynatma sorunu"): different sources (episodes) of one site with the same issue class (`forbidden`, `not_media`, `server_blocked`, `gone`, `unreachable`, `ip_bound`, client `playback_failed` / `timeout` / `network`; never `hls_unsupported_browser`, `expired`, `unsupported`, `decode`) within the TTL start a repair run (evidence `playback` + `issue`); sources of a started repair do not count again for the cooldown |
| `PLAYHEAL_COVERAGE_RATIO` / `PLAYHEAL_COVERAGE_MIN_SERIES` | `0.5` / `5` | share of series items without episode sources (and least number of series) that is a "no sources" signal: warning in the scan record + a repair run |
| `SCRAPER_HEAL_TIMEOUT` | `120` | seconds to wait for the LLM |
| `SCRAPER_HEAL_AUTOAPPLY` | `false` | **default only** (admin Ayarlar wins once saved): version + activate a sandbox-validated heal |
| `SCRAPER_HEAL_MODEL` / `SCRAPER_HEAL_PI_BIN` | — | pi model / binary override |
| `ONBOARD_AUTO_ROUNDS` | `2` | admin "Siteler" onboarding agent: how many AUTOMATIC correction rounds the same pi session gets when it hands in a draft that fails the acceptance criteria (the server sends the failing criteria + hints + diagnostics back before the admin sees the draft); `0` = off, at most `5`. Read at call time, not at start-up. An EDIT of a registered site and a draft with a pending question never get one |
| `ONBOARD_HARDEN` | `1` | admin "Siteler" onboarding: the hardening criteria of a NEW site (`availability_gate_defined`, `series_signal_collection`, `series_full_inventory`, `home_path_is_canonical`, `collection_poster_fill`, `detail_info_defined`; `removed_fields` warning) that stop an agent from passing with the easy yaml; `0` / `false` / `off` = kill switch (older criteria only). Never applies to repair or edit runs. Read at call time |
| `SCRAPER_HEAL_BASE_URL` / `SCRAPER_HEAL_API_KEY` | — | `openai_compatible` only (future) |

## API summary

Full schemas in `../API.md`. All responses JSON, `Access-Control-Allow-Origin: *`;
errors `{"error": {"code": ..., "message": ...}}`.

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/health` | status, source, item count, cache age |
| GET | `/api/profiles` | list · `POST` create · `DELETE /api/profiles/{id}` |
| GET | `/api/boot?profile=` | tier 1 — hero + row skeleton |
| GET | `/api/row/{row_id}?profile=&offset=&limit=` | tier 2 — lazy row paging |
| GET | `/api/detail/{item_id}?profile=` | tier 3 — seasons, cast, similar, resume |
| GET | `/api/streams/{item_id}?episode=&profile=` | resolved MP4/HLS, subtitles, resume |
| GET | `/api/subtitles/{id}.vtt` | soft subtitle of a stream as clean WebVTT (proxy, cached) |
| GET/HEAD | `/api/stream-proxy/{token}` | signed pass-through of a direct media file whose host needs headers the player cannot send (URL comes from `/api/streams`, `proxied: true`) |
| POST | `/api/progress` | upsert watch position |
| GET/POST/DELETE | `/api/mylist` | my-list management |
| GET | `/api/search?q=&profile=&limit=` | local + user-triggered live source search |
| GET | `/img/{item_id}/{kind}?w=&h=` | proxied poster or local Pillow JPEG |
| GET | `/img/avatar/{profile_id}?w=&h=` | profile avatar JPEG |

Dev helpers on any endpoint: `?delay=<ms>` (artificial latency),
`?fail=1` (forced 500 with the error envelope).

## Layout

```
server/
├── app/
│   ├── main.py config.py db.py cache.py images.py rows.py deps.py errors.py
│   ├── routers/        health profiles boot rows detail streams progress
│   │                   mylist search images ops ops_library
│   ├── sources/        base.py · mock.py · library.py
│   ├── library/        ingest.py · normalize.py · tmdb.py (stub)
│   └── scraper/        config.py runner.py parse.py schema.py fetch.py
│       │               resolve.py drift.py heal.py state.py
│       └── configs/    <site>.yaml (+ <site>.baseline.json)
├── data/               fixture.json · diziflix.db · imgcache/ · scraper_state/ · site_handoffs/<site>.md (devir notu)
├── tools/              gen_fixture.py ingest.py homepage_probe.py scraper_smoke.py
└── requirements.txt · install.sh · diziflix.service · .env.example
```

## Yabancidizi homepage scraper (experimental)

`app/scraper/configs/yabancidizi.yaml` registers `yabancidizi` in the scraper
registry/admin panel. It parses homepage film/series cards, latest episodes
and upcoming-season cards. The normalizer resolves relative URLs and groups
episode links by series. A home card only names the LATEST episode of a series,
so a separate **series inventory stage** (see "Series inventory crawl") reads each
series page and stores every season/episode plus the site's portrait poster, wide
cover, year, overview, genres, cast, rating, runtime and trailer without resolving
video providers. Detail-page playback is
resolved only when a movie or episode is opened. It prefers a stable VidMolly
file hint when present and uses the protected, session-bound OK.ru hand-off as a
fallback. Provider-specific VidMolly HLS and OK.ru MP4 extraction live in the
shared registry. The Obscura cookie session and lightweight TLS client are only
created if playback reaches that protected fallback.

From `server/`:

```bash
venv/bin/python -m tools.homepage_probe yabancidizi
venv/bin/python -m tools.homepage_probe yabancidizi --html tests/fixtures/yabancidizi_home.html
venv/bin/python -m unittest discover -s tests -v
```

**Test isolation.** Every test module starts with `import _sandbox` (`tests/_sandbox.py`): before any `app` import it points
`DATA_DIR` / `DB_PATH` / `IMG_CACHE_DIR` (and so the ops settings, TMDB previews and scraper state) at a temp directory and
blanks `TMDB_ACCESS_KEY` / `TMDB_TOKEN` / `TMDB_API_KEY`, whatever `.env` says. A tripwire compares the real `server/data`
before and after the run and exits with status 3 (`SANDBOX VIOLATION`) if a test changed it - run the suite when no server
uses that directory. (Found and fixed: `test_perf.HttpTests` booted the real app - `db.init()` + `videos.backfill()` rewrote
the real `data/diziflix.db` - and `test_ops` / `test_ops_settings` overview tests opened the real db and read the real
`ops_settings.json`.)


The probe is read-only: no database ingestion, state writes, healing or detail
fetches. `--html /path/homepage.html --output /tmp/report.json` accepts a saved
browser page. The bundled fixture contains reduced representative card HTML.
On 2026-09-05 the selectors matched 66 browser cards with title/image/link;
direct HTTP was blocked by Cloudflare/the fetcher's robots check locally.
Live scraping depends on access from the deployed server. Existing access
checks remain enabled.

Once HTTP access works, `python -m tools.ingest yabancidizi` uses the standard
library path. Append `yabancidizi.news` to `REMOTE_IMG_HOSTS` to serve its
artwork. The deployment preserves the server's `.env` and catalogue data.
