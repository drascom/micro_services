# `discover_site(url)` - the draft yaml built by code

One call reads at most 8 pages of the site (root page, its canonical home, the series archive, up to 2 series pages, up to 2 episode pages) and
returns a DRAFT. It never judges: what it is not sure of is in `missing`. Every page is stored: `pages[]` = `{role, url, page_id, fetch_mode}`;
use the `page_id`s with `query_html` / `grep_page` / `outline_page` / `test_config(page_id, detail_page_id)`.

## The answer

| field | meaning |
|---|---|
| `yaml_text` | the draft: `list`, `collections` (role by heading), `detail`, `normalize`, `series_page` / `episode_source`, `resolvers`, `providers`, `playback`, `availability_gate` |
| `found.<section>` | the evidence: `home` (path, blocks), `list` (page, `row_selector`, rows, fill), `collections[]`, `series_page` (episodes, seasons), `detail.fields.<name>` (`source`: `label` / `meta` / `jsonld`), `player` (candidates, host, provider), `normalize` (key regex, keys of a sample), `search_form` |
| `missing[]` | `{field, tried[], hint?}`: `list`, `home_series_section`, `collection:<role>`, `series_page`, `detail.<field>`, `player`, `search` (the form only: `search-hint:`), `needs_recipe` (`host`, `player_url`: no library provider covers it: `match_providers`) |
| `confidence` | per section `high` / `medium` / `low` (list, collections, series_page, detail, player, normalize) |
| `notes` | one line per home block (`block <x>: <role>` / `not used (<why>)`) and decisions (home path, archive page): copy them into your `notes` |
| `errors` | the sandbox's own checks of the draft (schema keys, selectors, regexes): fix first |

## Confidence

- `high`: verified on the page (list >= 8 valid rows with posters, series engine read every episode link, >= 3 detail fields from visible labels,
  a known provider matched, every card keyed). Still run `test_config`.
- `medium`: works but thin (few rows, fields from meta / JSON-LD, unknown provider host, key regex loose) - look at it.
- `low`: a guess or nothing: do the step by hand.

## Not decided by the tool

- `playback: video` is provisional: only `test_resolvers` / `test_config(playable: true)` prove a stream. A player built by script or a data attribute is
  listed in `found.player` but not turned into `resolvers` (`missing: player`).
- `search:` is never written: the form is reported; the result page needs a `row_selector` proven with `test_search`.
- `detail.fields` read the series (or film) page; the episode page is not used for them unless the cards are episode pages.

## When to correct by hand

A `missing` entry: look at the right stored page first (`query_html` on its `page_id`), then `ask_user`. A wrong `row_selector` / field: change that
key only and `test_config` (the `diagnostics` show what each selector extracted). The home is JavaScript-built (`errors`, no blocks): `fetch_page`
with `mode: "browser"` and continue with the workflow steps.
