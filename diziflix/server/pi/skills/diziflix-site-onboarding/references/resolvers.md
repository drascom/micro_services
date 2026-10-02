# Resolvers and providers

Playing a title takes two stages, both driven by the site yaml: **resolvers** (`resolvers:`) run on the DETAIL page and find player candidates (a
provider URL, or a hand-off to one; they know the SITE's markup, list order = priority); **providers** (`providers:`) turn a player URL into
streams (they know the HOST: code modules, `kind: code`, or provider-library recipes, `kind: recipe`, `references/providers.md`). You only NAME the
providers the site may use. A player on the site's own host, or a host no provider knows: `iframe` / `anchor_host` finds the player URL and a
provider RECIPE reads it (`references/providers.md`, `references/player-authoring.md`); only when that fails too (browser mode included)
`playback: trailer`, no `resolvers`, evidence in the notes (`needs code: <host>`, which page, what you saw).

## Choosing a type

| what the detail page shows | type |
|---|---|
| `<iframe src="https://host/embed/...">` of the player | `iframe` |
| plain links (`<a href>`) to the video host (download / watch links) | `anchor_host` |
| a token in a data attribute (`data-link="abc"`) the site turns into a URL `/api/x/abc` | `data_attr_token` |
| data attributes the site sends to an AJAX endpoint that answers with the iframe URL | `ajax_handoff` |
| an embed id the site's own JSON API answers with media URLs for | `json_api` |
| an iframe to a player page (site's own host, or a host without provider) whose script holds the media URL | `iframe` + a provider RECIPE (`providers: [<recipe>]`); `player_page` only for a player specific to this one site |

Start with the simplest type (`iframe`, then `anchor_host`); hand-off types are for sites that hide the provider behind their own session.
Several items can be combined (one per way of finding alternative servers).

## Catalog (generated from the code: these are the ONLY types, parameters and providers that exist)

<!-- BEGIN GENERATED resolvers-catalog (tools/gen_onboard_refs.py; do not edit by hand) -->
Resolver types (`resolvers:` items are flat: `{type: <name>, <parameter>: <value>, ...}`). `iframe`, `anchor_host`, `data_attr_token` and `ajax_handoff` also take `label` (constant candidate label; default the host), `label_from` (`text` or an attribute name), `lang` (constant language code, e.g. `tr`) and `lang_from` (`text` or an attribute name). The full parameter text is what `list_resolvers` returns.

### `iframe`

Takes the URL in an attribute (default src) of every element the CSS selector matches, such as the player iframe of a detail page, as a provider candidate.

| parameter | default | meaning |
|---|---|---|
| `selector` | **required** | CSS selector of the elements carrying the player URL |
| `attr` | `"src"` | attribute holding the URL (relative URLs are made absolute) |
| `host_regex` | - | keep only URLs whose whole hostname matches this regex (full match, case-insensitive) |

### `anchor_host`

Takes the links the selector matches (default every a[href]) whose hostname matches a provider host regex, such as download or watch links to a video host.

| parameter | default | meaning |
|---|---|---|
| `selector` | `"a[href]"` | CSS selector of the links |
| `host_regex` | **required** | keep only links whose whole hostname matches this regex (full match, case-insensitive) |

### `data_attr_token`

Reads a token from an attribute (e.g. data-link) of the selected elements and puts it into a URL template ('{base}/api/moly/{token}') to build the candidate URL.

| parameter | default | meaning |
|---|---|---|
| `selector` | **required** | CSS selector of the elements carrying the token |
| `attr` | **required** | attribute holding the token |
| `url_template` | **required** | candidate URL with `{token}` (percent-encoded) and optional `{base}`, e.g. `{base}/api/moly/{token}` |
| `filter_regex` | - | keep only elements whose label text matches this regex (search, case-insensitive) |
| `expect_host_regex` | - | hand-off mode: the URL is a site page, its first `iframe[src]` becomes the provider URL (hostname must match this regex) |
| `cookie_seed` | - | cookies sent with the hand-off page request: {name: 'now_ms' \| 'now_s' \| literal} |
| `browser_fallback` | `false` | retry once with the browser session's cookies (10+ s) when the site refuses the light cookies |

### `ajax_handoff`

For every selected element builds a hand-off (POST/GET to a site endpoint with form fields taken from the element's attributes).

| parameter | default | meaning |
|---|---|---|
| `selector` | **required** | CSS selector of the elements that carry the hand-off data |
| `method` | `"POST"` | 'POST' (form body) or 'GET' (form fields as query string) |
| `url` | **required** | endpoint URL |
| `form` | `{}` | `{field: 'attr:<attribute>' \| 'const:<text>'}`, e.g. `{link: 'attr:data-link', type: 'const:videoGet'}` |
| `json_key` | - | dotted key of the JSON answer holding the URL (else the first `iframe[src]` of the answer) |
| `expect_host_regex` | - | the provider URL's hostname must match; a URL on the site's own host is opened once and its first `iframe[src]` taken |
| `cookie_seed` | - | cookies for the requests: `{name: 'now_ms' \| 'now_s' \| literal}`, e.g. `{udys: 'now_ms'}` |
| `browser_fallback` | `false` | retry once with the browser session's cookies (10+ s) when the site refuses the light cookies |
| `filter_regex` | - | keep only elements whose label text matches this regex (search, case-insensitive) |

### `json_api`

Resolves the media straight from a JSON player API: the embed URL (or video id) goes into an endpoint template and the answer's source list becomes the streams.

| parameter | default | meaning |
|---|---|---|
| `endpoint` | **required** | API URL with '{video_id}' |
| `referer` | - | referer header sent to the API |
| `video_id_regex` | `"(\\d+)"` | regex with one group that extracts the video id from the embed URL |
| `sources_json_path` | `"media.level"` | dotted JSON path of the list of sources in the answer |
| `source_field` | `"source"` | field of a source holding the media URL |
| `quality_field` | `"value"` | field of a source holding its quality |
| `quality_preference` | `[]` | quality values in preferred order |
| `verify` | `false` | probe every source URL and drop the unreachable ones |
| `media_type` | `"mp4"` | `mp4` or `hls` (the legacy stream_resolver `type`, renamed: `type` selects the resolver) |
| `duration_json_path` | - | dotted JSON path of the duration in milliseconds |
| `selector` | - | optional CSS selector finding the embed URL on the page |
| `attr` | `"src"` | attribute of the selected element holding the embed URL |
| `stream_headers` | - | headers the media FILE needs `{name: value}` (only User-Agent, Referer, Origin, Cookie; `{page_url}`, `{player_url}`, `{base}` work); served through the signed stream proxy (an HLS stream too when it is proxied at all: recipe `stream_proxy: true`, a server-IP-bound URL, learned or env; the proxy then sends them for the playlist and segments); only when the file refuses a plain player (HTTP 400 / 403) |
| `cache_ttl` | - | seconds the streams may be reused (60..86400); leave it out unless the URLs carry no expiry and live much longer or shorter than ~15 minutes |

### `player_page`

Opens the player page the detail page points to (typically an iframe on the site's own host; plain HTTP or the browser), optionally follows nested iframes and finds the media URLs with declarative extract rules. Reusable players belong in the provider library as a recipe: use `player_page` only for a player specific to this one site.

| parameter | default | meaning |
|---|---|---|
| `selector` | **required** | CSS selector of the element on the detail page that carries the player URL |
| `attr` | `"src"` | attribute holding the player URL (relative URLs are made absolute) |
| `host_regex` | - | keep only player URLs whose whole hostname matches this regex (full match, case-insensitive) |
| `label` | `"Player"` | candidate label |
| `lang` | - | constant language code of the candidates |
| `fetch` | `"http"` | `http` (Chrome TLS fingerprint, sends Referer / headers / cookies) or `browser` (JavaScript-built pages only, no Referer); independent of the site's `fetch_mode` |
| `wait_for` | - | CSS selector the browser waits for before reading the page (only with fetch: browser) |
| `referer` | `"{page_url}"` | Referer for the player page (`fetch: http`): `{page_url}` = the detail page, `{base}`; players often 404 / 403 without it |
| `headers` | - | extra request headers {name: value} (only fetch: http, merged over the Chrome defaults) |
| `warm_session` | `false` | `fetch: http` only: GET the detail page first in the same session so its cookies go along (one extra request) |
| `follow` | `[]` | up to 2 hops into nested iframes, each `{selector, attr: src, regex?}` |
| `extract` | **required** | 1-8 rules (`regex` group \| `css` + `attr` \| `json_path`; plus `unpack`, `base64`, `unescape`, `type` auto\|hls\|mp4, `quality`, `label`, `quality_group` / `label_group`): `references/player-authoring.md`; leave `type` on auto (a URL extension wins) |
| `verify` | `false` | probe every found stream URL with a 1-byte request and drop the unreachable ones |
| `stream_headers` | - | as in `json_api` |
| `cache_ttl` | - | as in `json_api` |

### Providers (`providers:` names; a provider turns a player URL into streams)

`code` = a server module (listed here); `recipe` = a provider-library recipe (`configs/providers/<name>.yaml`; the library grows, so call `list_resolvers` for the recipes that exist now: `hosts` = `[host_regex, path_regex?]`).

| name | kind | hosts | description |
|---|---|---|---|
| `vidmolly` | code | `vidmoly.*`, `vidmolly.*` | VidMolly embedded player: resolves /dl, /w, /v and embed-<code> links to HLS/MP4 streams with soft subtitle tracks, using a cookie fallback for challenge pages. |
| `okru` | code | `ok.ru` | OK.ru video player: reads the player metadata API (MP4 qualities, HLS fallback) for /videoembed/<id> links. |
<!-- END GENERATED resolvers-catalog -->

## Examples (validated by the test-suite)

Alternative servers behind a site hand-off (the button's `data-link` goes to an AJAX endpoint that answers with the host's iframe URL; a light
`udys` cookie, browser cookies as the last resort). A token turned into a page URL: `data_attr_token`; plain links to a known host: `anchor_host`
(`host_regex`, `lang_from: text`):

```yaml
resolvers:
  - type: ajax_handoff
    selector: ".alternatives-for-this [data-link]"
    method: POST
    url: "{base}/ajax/service"
    form: {link: "attr:data-link", hash: "attr:data-hash", querytype: "const:alternate"}
    json_key: api_iframe
    label: OK.ru
    expect_host_regex: '(?:.+\.)?ok\.ru'
    cookie_seed: {udys: now_ms}
    browser_fallback: true
providers: [okru]
```

The site's own JSON player API (embed `https://site/embed/123` -> `https://api.site/v1/video/123` answers
`{"media": {"level": [{"value": "720", "source": "https://cdn/x.mp4"}]}}`):

```yaml
resolvers:
  - type: json_api
    selector: "iframe[src*='/embed/']"
    attr: src
    endpoint: "https://api.example.com/v1/video/{video_id}"
    referer: "https://www.example.com/"
    video_id_regex: '/embed/(\d+)'
    sources_json_path: media.level
    quality_preference: ["1080", "720", "480"]
    media_type: mp4
```

A player page on the site's own host: the site yaml only finds the player iframe and names the provider recipe that reads it
(`references/providers.md`):

```yaml
resolvers:
  - type: iframe
    selector: 'iframe[src*="/player/oynat/"]'
    attr: src
providers: [referer_player]
```

## Testing

`test_resolvers` runs the resolvers on a detail page (`detail_url` or a `page_id`) and resolves up to 8 candidates like a play request:
`candidates`, `resolved[]` (`ok`, `provider`, `streams_count`, `error`), `status` (`resolved`, `no_stream`, `no_candidates`, `error`; `json_api` /
`player_page` give streams directly). Zero candidates = the selectors do not match this page (`query_html` on the same `page_id`). Candidates but
no stream = the host needs another provider / hand-off or blocks us: read `resolved[].error` and `trace`, write a recipe (SKILL.md "Player
authoring"). Test on a PLAYABLE title, ideally a second one too.
