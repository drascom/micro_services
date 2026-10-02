# Site yaml reference

The result of onboarding is ONE yaml document (the `yaml_text` of the tools). The server runs it deterministically on every scan and play
request (no LLM): selectors must be stable and correct, not clever.

Plain yaml, no anchors / aliases (`&x`, `*x`: the sandbox refuses them). Do not write `version`, `updated_at` or `site_id`: the server owns
them (`site_id` is chosen at save time, you only suggest one in `submit_draft`).

## Top-level keys

| key | required | meaning |
|---|---|---|
| `display_name` | yes | name shown in the admin panel, e.g. `Örnek Film` |
| `base_url` | yes | absolute origin, no trailing path: `https://www.example.com` |
| `list_url` | yes | path (or URL) of the page that lists titles, relative to `base_url`: `/filmler` |
| `fetch_mode` | yes | `http` or `browser` (headless; JS-rendered pages, Cloudflare-style challenges; slow). Copy what `fetch_page` reported as used (`chrome`, reported for a fetch with `referer`, is a diagnostic transport and never a `fetch_mode`) |
| `schema` | no | `MovieItem` (default; one card per title) or `HomepageItem` (plus `featured`, `season`, `episode`, `trend_score`; mixed film / series cards announcing the latest episode) |
| `playback` | yes | `video` = playable through `resolvers` / `providers`; `trailer` = no usable video host (the TV only offers the trailer) |
| `list` | yes | `row_selector` + `fields`: how to read the list page |
| `detail` | recommended | `fields`: how to read one detail page (synopsis, genres, trailer, ...) |
| `normalize` | yes | rules turning a parsed item into the canonical title (key, type, ...): `normalize.md`. A new site has no code normalizer |
| `resolvers` | for `playback: video` | ordered chain that finds player candidates on a detail page: `resolvers.md` |
| `providers` | no | allowed providers: code modules (`vidmolly`, `okru`) and library recipes, including the NEW ones you hand in with `submit_draft(provider_recipes=...)`; omit = all. Only names of the catalog (`list_resolvers`) or your own recipes |
| `image_hosts` | no | extra poster hosts when they differ from the `base_url` host (`[img.example-cdn.com]`; the `base_url` host is already allowed): look at the real poster URLs |
| `collections` | recommended | the notable home / menu sections (`[{id, title, path, role}]`, `id` = `<role>_<site_id>`; each may carry its own `row_selector` / `fields`). Needs `site_id`. Add them after the list works: `collections.md`; never a full catalogue |
| `item_limit` | no | items a scan takes from EACH list and EACH collection: a whole number 1..200, default 30 (else an error). The engine reads one page per list / collection and has NO pagination: this caps that page, it never takes "all N titles". `test_config` reports `ingest: {item_limit, list_items_on_page, would_ingest}` (per collection too with `collections: true`); never promise more titles than it says. Set it only when a home section really shows more than 30 titles that belong on the home screen |
| `series_page` | for cards that link to a SERIES page | how to read a series page into seasons and episodes (`row_selector` + `episode_url_regex` mandatory): `series-page.md`. Only when the normalize block has `type: series` and no `episode_source` |
| `search` | when the site has a search form / endpoint | the site's live search (`url` with `{query}`, `method`, `form` / `json`, `headers`, `format`, `row_selector` + `fields` or `results_path`, `fetch`): `search.md`. Prove it with `test_search` (`found_known: true`); no search form: no block |

Do NOT write: `stream_resolver` (legacy; use a `json_api` resolver), `use_site_module`, `series_catalog`, `series_crawl`, `detail_pages`,
`detail_prewarm_*`, `obscura_*`, `cache_images`: they belong to the two hand-built sites or need site code (say so in the notes if the site
needs one). (`series_page` is allowed in its generic form only: `series-page.md`; its module keys `tab_selector`, `tab_season_attr` are errors.)

## `list`

```yaml
list:
  row_selector: "div.film-card"      # one element per title; CSS (selectolax)
  fields:
    title: {selector: "h3.film-title a"}
    detail_url: {selector: "h3.film-title a", attr: href}
```

`row_selector` must match exactly the title cards (not the whole grid, not ads, not carousels you do not want). Every `fields` selector is
evaluated INSIDE one row. `title` and `detail_url` are mandatory.

### Field spec (list and detail)

A field is a mapping; the value is the row's text, or the attribute named by `attr`.

| key | meaning |
|---|---|
| `selector` | CSS selector inside the row (first match); required unless `self: true` or `fallback` |
| `self: true` | the row element itself (the row is the `<a>`): `{self: true, attr: href}` |
| `attr` | read this attribute instead of the text (`href`, `src`, `data-src`, `content`, `alt`, `class`, ...) |
| `all: true` | collect every match as a list (genres, cast) |
| `regex` | applied to the text / attr value; group 1 (or the whole match) is kept; no match = empty |
| `replace` | `{"from": "to"}` literal replacements, before `regex` |
| `split` | split by this separator; `index` (negative allowed) picks one part, `multi: true` keeps a list; `then_split` splits the picked part again |
| `cast` | `int`, `float` (comma becomes dot) or `date_tr` (`24 Temmuz 2026`, `24.07.2026` -> `2026-07-24`); fields typed int / float in the schema (`year`, `rating`, `runtime`, `followers`, `season`, `episode`) need the matching cast |
| `fallback` | field specs tried in order; the first non-empty value wins (lazy images, mixed card types) |

Order: text / attr -> whitespace collapse -> `replace` -> `regex` -> `split` -> `then_split` -> `cast`.

Field names the schema knows: `title` (required), `original_title`, `year` (int), `poster_url`, `backdrop_url`, `genres` (list), `synopsis`, `rating`
(float), `runtime` (int, minutes), `country`, `followers` (int), `cast` (list), `trailer_url`, `detail_url`, `tmdb_id` (int), `imdb_id`;
`HomepageItem` adds `featured`, `season`, `episode`, `trend_score` (int). Fill only what the page really shows: an empty field is fine, a wrong
one is not.

Poster tips: lazy images keep the real URL in `data-src` / `data-lazy-src` / `data-original` and a placeholder in `src` (often a `data:` URI, which
the normalizer throws away): the lazy attribute FIRST in a `fallback` list, `src` last. `poster_url_fill` counts a `data:` / `blank.gif` poster as
filled: look at the `samples`. `<meta property="og:image" content=...>` is a fine `detail.fields.poster_url`.

## `detail`

```yaml
detail:
  fields:
    synopsis: {selector: "div.summary p"}
    genres: {selector: "div.cats a", all: true}
    trailer_url: {selector: 'meta[property="og:video:url"]', attr: content}
```

The whole document is the context (`<head>` meta tags work). `test_config` runs it on ONE detail page (fill reported per field). Detail
values fill gaps the list page cannot give.

## Complete example (a movie site; validated by the test-suite)

```yaml
display_name: Örnek Film
base_url: https://www.ornekfilm.example
list_url: /filmler
fetch_mode: http
schema: MovieItem
playback: video
image_hosts: [img.ornekfilm-cdn.example]
list:
  row_selector: "div.film-card"
  fields:
    title:
      selector: "h3.film-title a"
    detail_url:
      selector: "h3.film-title a"
      attr: href
    poster_url:
      fallback:
        - {selector: "img", attr: data-src}
        - {selector: "img", attr: src}
    year:
      selector: "span.year"
      regex: '(?:19|20)\d{2}'
      cast: int
detail:
  fields:
    synopsis:
      selector: "div.summary p"
    trailer_url:
      selector: 'meta[property="og:video:url"]'
      attr: content
resolvers:
  - type: iframe
    selector: "div.player iframe"
    attr: src
providers: [vidmolly, okru]
normalize:
  host: www.ornekfilm.example
  key:
    from: [detail_url]
    regex: '^/film/(?P<slug>[^/]+)'
    template: '{slug}'
  type: movie
```

Two real hand-built configs: `examples/sinemalar.yaml` (movie list, `playback: trailer`) and `examples/yabancidizi.yaml` (mixed cards, `HomepageItem`,
`fetch_mode: browser`, per-collection selectors): good list / detail selectors, but no `normalize:` / `resolvers:` (those sites use code) and keys you
must not copy (see "Do NOT write"); write your `series_page:` in the generic form of `series-page.md`.
