# `normalize:` rules

A new site has no hand-written normalizer: the `normalize:` block turns every parsed list item into the canonical title of the library. It is pure
data and REQUIRED (`normalize: missing` otherwise). Two things matter: a stable, unique **key** per title (from the detail URL) and the **type**
(`movie` or `series`); the rest has defaults.

## Shape (reference; working examples below)

```text
normalize:
  host: example.com                   # optional; str or list: the detail_url host must be one of these, else host_mismatch
  base_url: https://example.com/      # optional; default = the site base_url (makes relative URLs absolute)
  absolute_urls: true                 # optional (default true); false = URL fields pass through as scraped
  key:                                # REQUIRED
    from: [detail_url]                # raw fields tried in order; each is made absolute first
    regex: '^/(?P<kind>dizi|film)/(?P<slug>[^/]+)'   # str or list; searched in the URL PATH (match: path, default) or the whole URL (match: url)
    template: '{kind}/{slug}'         # from the named groups; omitted = first group, else the whole match
  type: movie                         # movie | series | {from_group: kind, map: {dizi: series, film: movie}, default: movie}
  source_url: 'https://example.com/{kind}/{slug}'    # optional template (groups and {url}); default = the absolute detail_url
  fields: {overview: synopsis, trailer_url: null}    # canonical <- raw field (name | list = first filled | null = off); title cannot be off
  split: {genres: ','}                # genres / cast: split each entry, trim, drop empties
  episode_source:                     # only for series items that announce a season + episode
    enabled: true
    url_template: '{url}/bolum-{episode}'             # optional; default = the absolute detail_url
    when: {has: [season], missing: [episode]}         # optional; key.regex groups that gate url_template
    label: '{season}. Sezon {episode}. Bölüm'
    default_season: 1                                 # optional; season when neither the raw field nor a key.regex group has one
```

Template variables: the named groups of `key.regex`; `url` (absolute detail URL without trailing `/`); in `episode_source` also `season`, `episode`
(resolved integers) and `source_url`; only plain `{name}` placeholders. Allowed top-level keys: `host`, `base_url`, `absolute_urls`, `key`, `type`,
`source_url`, `fields`, `split`, `episode_source`, `clean` (any other is an error). `clean: false` switches the safety net below off. Canonical fields come from the raw field of the same name (`overview` <-
synopsis); use `fields:` only when your `list.fields` names differ.

## How to choose the key

Look at several `list.samples[].detail_url` values. The key is the part that identifies the title and never changes: a slug (`/film/the-matrix`) or a
numeric id (`/film/287065/drama`: `regex: ['/film/(\d+)', '/movie/(\d+)/']`, no `template`, the first group is the key; a poster URL in `from` is a
second source for the id), never a page number, a query string, a season / episode. Write `key.regex` against the URL PATH (leading `/`), named
groups when a template or the type needs them. Two different titles must never share a key (`duplicate_keys` empty or tiny) and one title always
gets the same key. A series card whose URL points at an episode (`/dizi/x/sezon-2/bolum-5`): the key is still the SERIES (`dizi/x`): leave season /
episode out of `template`, keep the episode with `episode_source`.

Safety net (`clean`, default on): a `type: series` key built from a `slug` group loses episode / release tails (`-26-bolum`, `-106-bolum-1-ekim`,
`-son-bolum-izle6`, `-izle-hd`, `-full-izle-tek-parca`; never a bare trailing number: `daha-17`, `7-numara`) and a title loses ` izle`, ` HD`, ` Full`,
` Tek Parça`, ` | <Site>` (series: ` Son Bölüm`, `37. Bölüm ...`). `source_url` and `episode_source` keep the original URL; the report warns
(`normalize.key: key regex bölüm eki bırakıyor`, `normalize.cleaned`): capture the show slug only.

`type`: only films `movie`, only series `series`, mixed from a URL group (`type: {from_group: kind, map: {dizi: series, film: movie}}`).

## Reading the `normalize` report of `test_config`

`{total, ok, rejected: {no_key: 3}, duplicate_keys, types, with_video_sources, episode_items, series_without_sources, samples[], errors}`.
Reject reasons: `no_title` (the raw `title` is empty: fix `list.fields.title`), `no_key` (no `key.from` URL matched `key.regex`: compare it with a sample
`detail_url` path; normal for a few non-title cards), `host_mismatch` (the detail URL host is not in `host`: add it, or fix `detail_url`; www vs
non-www!), `bad_rules:...` (the block is invalid; the message names the key). `with_video_sources` = items carrying a `video_sources` list (the
episodes the library can play), `episode_items` = items of type `series`, `series_without_sources` = series items WITHOUT one; `duplicate_keys`
lists a key only when the same title AND episode came twice. Check `samples`: `poster_url` must be an absolute http(s) URL (a `data:` poster
becomes empty), `source_url` must open the title page.

## Example: mixed series and films, episode cards (yabancidizi-like)

```yaml
normalize:
  host: yabancidizi.news
  base_url: https://yabancidizi.news/
  key:
    from: [detail_url]
    regex: '^/(?P<kind>dizi|film)/(?P<slug>[^/]+)(?:/sezon-(?P<season>\d+)(?:/bolum-(?P<episode>\d+))?)?/?$'
    template: '{kind}/{slug}'
  type:
    from_group: kind
    map: {dizi: series, film: movie}
    default: movie
  source_url: 'https://yabancidizi.news/{kind}/{slug}'
  fields: {original_title: null, trailer_url: null}
  split: {genres: ','}
  episode_source:
    enabled: true
    url_template: '{url}/bolum-{episode}'
    when: {has: [season], missing: [episode]}
    label: '{season}. Sezon {episode}. Bölüm'
```

The latest-episode cards link to `/dizi/<slug>/sezon-4/bolum-2`; the key is still `dizi/<slug>`, `source_url` is the series page, and
`episode_source` adds the episode (season / episode from the raw fields or the URL groups). A card with only `/sezon-4` gets `/bolum-<episode>`
appended by `url_template`. A film-only site skips `episode_source`.

## Playable chain: a series needs episodes (read this for every series site)

After `normalize` look at `normalize.with_video_sources` and `normalize.series_without_sources`. A `series` item without `video_sources` has
nothing to play: the library holds a title with no episode, so it is NOT playable. `test_config(playable: true)` and `submit_draft` fail it
(`series_have_episode_sources`, at least 90% of the series items need an episode source).

**Episode pages as cards** (a card = one episode page, the URL carries show, season and episode, e.g. `/<show>-<season>-sezon-<episode>-bolum-.../`):
the `key` is the SHOW only. Do NOT make every episode page a title of its own (`^/(?P<slug>[^/]+)` over the whole episode slug: every episode
becomes another "series", no `video_sources`). Split the URL with the named groups `slug`, `season`, `episode`, keep only `{slug}` in
`template`, set `type: series` and turn `episode_source` on: the episode page itself is the playback page.

```yaml
normalize:
  host: example.com
  key:
    from: [detail_url]
    regex: '^/(?P<slug>[a-z0-9-]+?)-(?P<season>\d+)-sezon-(?P<episode>\d+)-bolum[^/]*/?$'
    template: '{slug}'
  type: series
  episode_source:
    enabled: true
    label: '{season}. Sezon {episode}. Bölüm'
```

`/some-show-2-sezon-5-bolum-izle/` becomes the title `some-show` with the episode `s2e5` whose page is that URL (opened only when someone plays
it). Try the regex against the real card URLs: `rejected.no_key` small, `with_video_sources` close to `ok`.

**No season in the URL** (`/<show>-<episode>-bolum-.../`): the generic normalizer cannot know it. Write the assumption down:
`episode_source.default_season: 1` (a season the URL or the raw item states wins) and say in `notes` that the site has no seasons and season 1
is assumed.

```yaml
normalize:
  key:
    from: [detail_url]
    regex: '^/(?P<slug>[a-z0-9-]+?)-(?P<episode>\d+)-bolum[^/]*/?$'
    template: '{slug}'
  type: series
  episode_source: {enabled: true, default_season: 1}
```

**Cards that are series PAGES** (`/diziler/<show>-izle/`, no episode in the card): `normalize` cannot list the episodes; the yaml `series_page:`
block does (`series-page.md`): the `key` is the show slug, `type: series`, NO `episode_source`. Do not invent episodes and do not point
`episode_source` at a page that is not an episode page. Only when no `series_page` can work (the episode list is built by JavaScript or an
API): write `needs series inventory` in `notes` with the evidence; `series_have_episode_sources` stays failed and the admin decides. A site
whose cards are FILMS needs none of this (only `playable_ratio`).
