# Player authoring: reading a player page into a provider recipe

Read this after the "Player authoring" section of `SKILL.md` and `references/providers.md` (recipe format, `match`, testing, handing in). A recipe
opens the player page (a URL a detail page points to: an iframe on the site's own host such as `/player/oynat/<hash>`, or a video host),
optionally follows a nested iframe, and finds the media URL with `extract` rules. Plain yaml, nothing is executed (a packed script is only unpacked
by substituting its word table). The rule parameters and limits are the `player_page` parameters of `list_resolvers` (`references/resolvers.md`).

## Pattern found -> recipe

Example recipes are in `references/examples/providers/` (each is tested against a sample page):

| `grep_page` / `fetch_page` shows | rule or parameter | example recipe |
|---|---|---|
| `file:"https://x/y.m3u8"`, `"src":"https:\/\/x\/y.mp4"`, `source = '...'`: a whole URL in a script or JSON | `regex` rule on the URL itself | `plain_url_player.yaml` |
| `<source src=...>` or `<video src=...>` | `css` rule, `attr: src` | `source_tag_player.yaml` |
| `eval(function(p,a,c,k,e,d)` and no readable media URL | the plain `regex` plus `unpack: true` | `packed_player.yaml` |
| `atob("aHR0c...")` or another long base64 string | `regex` capturing the base64 text plus `base64: true` | `base64_player.yaml` |
| `<iframe src=...>` to another page, no media URL | `follow: [{selector: "iframe[src]"}]`, at most 2 hops | `nested_browser_player.yaml` |
| the player URL itself answers JSON (`{"sources": [...]}`) | `json_path` rule | `json_api_player.yaml` |
| `fetch_page(player_url)` answers 404 / 403 / "Just a moment", but `fetch_page(player_url, referer=<detail page>)` returns the page | keep `fetch: http`; the default `referer: "{page_url}"` is that request | `referer_player.yaml` |
| http mode AND the referer fetch fail (403 / 503 / "Just a moment" / empty), `mode: "browser"` works | `fetch: browser`, optional `wait_for` | `nested_browser_player.yaml` |
| the player answers only with the site as referer | `referer: "{base}/"`, `headers` | `json_api_player.yaml` |
| `sources: [{file, label}, ...]` with several qualities | `quality_group` / `label_group` on one regex | `quality_player.yaml` |
| HLS on one title, file-host mp4 on the next | one `extract` rule per format, `type` on auto | `mixed_formats_player.yaml` |

Rules of thumb:

- **Test on at least 3 different player URLs of different titles before submitting; sites often serve different hosts or types per title.**
  `test_provider` once per sample URL; `test_resolvers` tries `detail_url`, then the `detail_urls` you name (up to 4) or 2 sampled from the list
  page: the end-to-end check once the recipe is part of the site yaml. A recipe made from the first page alone is a guess.
- The rule matches the RAW page text, the text `grep_page` searches: prove a `regex` with `grep_page` first. JSON in a script escapes slashes
  (`https:\/\/cdn...`): match them with `(?:\\?/)`; the found URL is unescaped afterwards (`unescape: true`, the default) and made absolute against
  the player page. In a single-quoted yaml string a backslash is literal (`\.`, `\s` go in as they are); write a double quote as `\x22` and a
  single quote as `\x27`.
- `regex`: group 1 is the URL when the pattern has a capture group, else the whole match. Leave `type` out: the default `auto` reads the URL
  (`.m3u8` = `hls`, `.mp4` / `.webm` = `mp4`; extension-less = `mp4`). Do not pin a `type` from the first page (sites serve `mp4` on one title and
  `m3u8` on the next, and a URL extension beats a declared `type`: `test_provider` warns "declared mp4, url is m3u8 -> hls"); set `type` only
  for an extension-less URL that is NOT mp4 (a bare HLS endpoint). `quality` / `label` name the stream; `quality_group` / `label_group` take them
  from the page. Up to 8 rules; all that find something are merged (the first is the best stream; `quality_group` streams are ordered best first).
- `fetch: http` carries a Chrome TLS fingerprint (passes Cloudflare's TLS check), the `referer` (default: the detail page) and any `headers`: a page
  that is "404 Not Found" without its referer or "Just a moment" to a plain client is usually fine here, so try `fetch_page(player_url,
  referer=<detail page>)` before the browser. `warm_session: true` only when the player also wants the cookies the detail page sets.
- `fetch: browser` costs 10+ seconds per page, runs one at a time and cannot send a referer: only when http mode (with its referer) really is
  blocked or the page is JavaScript-built; prefer one `follow` hop over three rules. `verify: true` drops stream URLs that do not answer a 1-byte
  request: only when `test_provider` shows a dead or bogus URL. A recipe cannot hand a nested iframe on to another provider: `follow` into it.

## Reading the `test_provider` answer

- `status: resolved` with a `host` that looks like a media CDN (not an ad, a tracker or the player's own site, which a wrong rule can produce as a
  bogus URL): this sample is done. `valid: false`: read `errors` and fix the yaml.
- `status: no_match` / `matched: false`: `match.host_regex` / `match.path_regex` do not cover the sample URL; loosen the regex (anchor it:
  `(^|\.)host\.tv$`).
- `status: no_stream`: `error` and `trace` name the failing stage. `player_page.extract` = the rule does not match what the page holds (or it is not
  the real player): `grep_page` the player page again and compare. `player_page.fetch` with `HTTP 404` / `403` = another referer or header is
  needed (compare with `fetch_page(..., referer=...)`). `.follow1` = the nested iframe selector matches nothing. A Cloudflare challenge: with
  `fetch: http` check the referer first, then `fetch: browser`; if the browser gets it too, the page cannot be read. `blocked:` in the trace = a
  private / internal address, refused on purpose. The warning `declared ..., url is ...`: take `type` out.

## Reading the `test_resolvers` answer (the recipe inside the site yaml)

- `status: resolved` (EVERY page resolved; `pages[]` per page with its `streams`) and `resolved[].ok: true` with a media CDN `host`: done.
  `pages[].status: skipped` = no time for that page, not judged: test it in a second call with `detail_urls`.
- `status: partial` or a `varied hosts/types` warning: the recipe fits only some pages. Read `pages[].error` of the failing ones, generalize the
  recipe (one `extract` rule per format as in `mixed_formats_player.yaml`, `type: auto`), test again; a kind of page that really cannot be played:
  say which in `notes`.
- `no_candidates`: the site yaml's resolver `selector` does not match the detail page. `no_stream` although `test_provider` resolved the same URL:
  the site yaml's `providers:` lacks the recipe, or the candidate URL differs from the sample (compare `candidates[].url` with `match`).

Nothing works: `playback: trailer`, no `resolvers`, the evidence in `notes` (player URL, modes tried, what `fetch_page` returned, what `grep_page`
found, Cloudflare / JavaScript-only: `needs code: <host>`). Do not guess a URL or invent a stream.
