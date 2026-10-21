# `series_page:` - the episodes of a series whose cards link to a SERIES page

Use it when a list card leads to a **series page**: one page per show listing its seasons and episodes (`/diziler/<show>-izle/`), each episode with
its own page (`/<show>-<S>-sezon-<E>-bolum-izle-full-tek-parca/`). The card carries no episode, so without `series_page` the title would exist
in the library but never open (`series_have_episode_sources` fails). With it the server reads the series page after every scan (a few series at
a time) and whenever a series is opened, and writes every season / episode into the library. No video host is resolved there: an episode row
only holds the episode PAGE URL; the player is found on that page at play time, so `resolvers:` must find the player on an EPISODE page.

Cards that are EPISODE pages (`-3-sezon-5-bolum`) have no series page behind them: find the series archive (menu "Diziler", an alphabetical
archive, the series link on an episode page), read SERIES cards from it in `list:` and write `series_page:` (`series_full_inventory` fails a draft
with episode-page cards and no `series_page:`); ask (`ask_user`, field `series_inventory`) only when the series pages really do not list the
episodes. Cards that already are EPISODE pages use `normalize.episode_source` instead (`normalize.md`, "Playable chain"); never both.

## Series directory (cards that link EPISODE pages)

A `latest_episodes` card ("Halef 37.Bölüm") is a `type: series` record whose `source_url` is the EPISODE page, which `series_page` cannot read. With
`series_url_regex` set (the series pages' URL shape) the server rewrites such a record's `source_url` to the series page: it matches the card's
clean title / key slug against the rows of the list and the collections (`title`, then `slug`, then a single `prefix` candidate), no request. So the
alphabetical series archive must be in `list_url` or a collection (every row, `item_limit` does not cut it); no match = `series_page_unknown` (kept,
not crawled, no error). The key tail (`-37-bolum`) and the title litter are cut by the engine (`normalize.md`). `test_config` runs the check:
`series.ingest_sample` (<= 10 series items of the list + collections, <= 5 pages read, criterion `ingest_sample_ok` >= 0.8; reasons `series_page_unknown`,
`not a series page`, `same_series`, `empty_inventory`, `key_mismatch`). `same_series_regex` filters only the link-scan fallback, never the structured rows.

## The normalize block of a series card

The key is the SHOW slug, `type: series`, NO `episode_source`; `source_url` stays the series page (the default: the card's `detail_url`).

```yaml
normalize:
  host: www.example-dizi.com
  key:
    from: [detail_url]
    regex: '^/diziler/(?P<slug>[^/]+?)(?:-izle)?/?$'
    template: '{slug}'
  type: series
```

## How to write it

1. `fetch_page` ONE series page (a `detail_url` of `list.samples`), `outline_page(page_id)`: `episode_links[]` lists groups of links that differ only
   in numbers: `shape` (digits `N`, the series slug `<slug>`), `selector`, `count`, `sample_hrefs`. The biggest group with an episode-like shape is
   the episode list.
2. `episode_url_regex`: `shape` as a regex searched on the URL PATH, numbers as named groups `(?P<season>\d+)`, `(?P<episode>\d+)` (mandatory),
   the show part `(?P<slug>[^/]+?)`. No season in the URLs: leave `season` out, write `default_season: 1`, say so in `notes`.
   In a SINGLE-quoted yaml scalar write `\d` (a double-quoted one needs `\\d`): `\\d` in single quotes = a literal backslash + `d`, matches no digit
   (`rows_matched` > 0 but `rows_accepted: 0`; `config_errors` names it). `rejected_by` quotes the whole regex and the path it did not match.
3. `row_selector`: the element holding ONE episode (`ul.episodes li`, `table.episodes tr`) or the episode links themselves; `query_html` must
   count exactly the page's episodes. A selector that matches nothing is not fatal (the engine scans every link, `structured: false`) but titles
   and dates are lost: fix it. Optional `fields` (`title`, `air_date`, `url`).
4. Other series on the page ("similar series"): `series_slug_regex` + `same_series_regex`. Seasons on separate pages: `season_pages`. Rows not
   aired yet: `unaired_classes`. The site's "first / last episode" buttons: `first_episode` / `last_episode`.
5. `test_config(yaml_text, page_id, detail_page_id, playable: true)`, read `series{}` (below).

## Keys

<!-- BEGIN GENERATED series-page-keys (tools/gen_onboard_refs.py; do not edit by hand) -->
| key | required | meaning |
|---|---|---|
| `row_selector` | yes | CSS, one match per episode row (`<li>` / `<tr>` / `<div>` holding the episode link) or the episode links themselves (`a[href]`) |
| `episode_url_regex` | yes | regex on the URL PATH of an episode link; named group `episode` mandatory, `season` optional, `slug` = the series part (drops other series' episodes); the numbers always come from the URL, never the markup |
| `fields` | no | `url` (default: the row, or its first `a[href]`), `title` (default "N. Bölüm"), `air_date` (`cast: date_tr`); field specs as in `config-schema.md` |
| `default_season` | no | season of a site whose episode URLs carry none (whole number >= 1, default 1) |
| `series_url_regex` | no | the PAGE must be a series page: regex on its URL path (else nothing is read) |
| `series_slug_regex` | no | regex on the page URL path whose group `slug` (or group 1) is the series slug; default: the most common slug among the episode links |
| `same_series_regex` | no | regex with `{slug}` an episode path must match: drops episodes of OTHER series ("similar series" blocks) |
| `season_pages` | no | seasons on separate pages: `{season_menu: <CSS of the season links>, season_url_regex: <regex with group `season`>}` or a bare CSS selector |
| `season_menu` | no | top-level spelling of `season_pages.season_menu` (prefer `season_pages`) |
| `season_url_regex` | no | top-level spelling of `season_pages.season_url_regex` (prefer `season_pages`) |
| `unaired_classes` | no | row classes of episodes not aired yet (not written) |
| `first_episode` | no | CSS of the site's own "first episode" link: the list is verified against it |
| `last_episode` | no | CSS of the site's own "last / newest episode" link: the list is verified against it |

Any other key is an error (`series_page: unknown key ...`).
<!-- END GENERATED series-page-keys -->

Do not write the keys of a hand-built site module (`tab_selector`, `tab_season_attr`): they are an error here.

## Examples (each one is tested against a sample page)

### 1. Episode rows with names and dates, similar series on the page

One `ul.episodes` lists every season; a "similar series" box (other shows' episodes) is dropped by `same_series_regex`.

```yaml
series_page:
  row_selector: "ul.episodes li"
  fields:
    url: {selector: "a", attr: href}
    title: {selector: ".name"}
    air_date: {selector: ".date", cast: date_tr}
  episode_url_regex: '^/(?P<slug>[^/]+?)-(?P<season>\d+)-sezon-(?P<episode>\d+)-bolum'
  series_url_regex: '^/diziler/'
  series_slug_regex: '^/diziler/(?P<slug>[^/]+?)(?:-izle)?/?$'
  same_series_regex: '^/{slug}-\d+-sezon'
```

### 2. A site without seasons

No season in the episode URL (`/<show>-<E>-bolum-izle/`), rows are the links themselves: everything is season 1.

```yaml
series_page:
  row_selector: "div.bolumler a"
  episode_url_regex: '^/(?P<slug>[^/]+?)-(?P<episode>\d+)-bolum'
  default_season: 1
```

### 3. One page per season, announced episodes, first / last links

Each season is its own page (`/dizi/<show>/sezon-<S>`) with a menu of all of them (`season_pages`); rows with the class `soon` have not aired.

```yaml
series_page:
  row_selector: "table.episodes tr"
  fields:
    title: {selector: "td.title"}
    air_date: {selector: "td.date", cast: date_tr}
  episode_url_regex: '^/dizi/(?P<slug>[^/]+)/sezon-(?P<season>\d+)/bolum-(?P<episode>\d+)'
  season_pages:
    season_menu: "#seasons a[href]"
    season_url_regex: 'sezon-(?P<season>\d+)'
  unaired_classes: [soon]
  first_episode: "a.first-ep"
  last_episode: "a.last-ep"
```

### 4. The minimal block: every episode link of the page

No usable row container: every link that looks like an episode; the engine keeps the series most links belong to (a warning says how many dropped).

```yaml
series_page:
  row_selector: "a[href*='-bolum-']"
  episode_url_regex: '^/(?P<slug>[^/]+?)-(?P<season>\d+)-sezon-(?P<episode>\d+)-bolum'
```

## What `test_config(playable: true)` reports

`series: {checked, with_episodes, skipped, samples[]}`: up to 3 DIFFERENT series pages of the list's series cards are read with your `series_page`;
a sample is `{key, series_url, episodes, seasons, structured, first, last, season_pages, warnings, error}`; the `playable` samples are then episode
pages of that inventory. Series cards without episodes and no `series_page`: `series.hint` = "cards link to series pages: add series_page".

- `series_page: ...` in `errors`: the block does not validate (the message names the key), nothing is read.
- `episodes: 0`: `episode_url_regex` does not match, or the page is JavaScript-built (`mode: "browser"` + `fetch_mode: browser`), or the card's
  `source_url` is not the series page. `structured: false`: `row_selector` matches nothing (the link scan found the episodes): fix it, titles and
  dates are lost.
- `seasons: 1` but more exist: `season_pages` (or the regex lacks a `season` group). Episodes of other shows: `series_slug_regex` +
  `same_series_regex`. `error: page: ...`: the series page could not be fetched (blocked, private host): check the URL / `fetch_mode`.

On `save` the server writes `series_page: {min_items}` (half the measured episode count) into the site's baseline: a series page that suddenly
yields fewer rows is reported as drift.
