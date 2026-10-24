# Acceptance criteria

`test_config` returns `criteria` (`{value, min|max, ok}` per row below) and a single `passed` flag. A repair proposal is judged by the
server's own gates (`heal.md`); `passed: true` on the parts you changed is the evidence you hand in.

<!-- BEGIN GENERATED quality-criteria (tools/gen_onboard_refs.py; do not edit by hand) -->
| criterion | must be | meaning |
|---|---|---|
| `valid_count` | >= 8 | list rows that pass the schema |
| `title_fill` | >= 0.95 | share of ALL parsed list rows with `title` filled |
| `detail_url_fill` | >= 0.95 | share of ALL parsed list rows with `detail_url` filled |
| `poster_url_fill` | >= 0.8 | share of ALL parsed list rows with `poster_url` filled |
| `normalize_ok_ratio` | >= 0.9 | normalized items / valid items |
| `duplicate_key_ratio` | <= 0.1 | keys produced by more than one SAME item (same title and episode) / normalized items |
| `config_errors` | == 0 | `errors` of the report (yaml, selectors, resolvers, normalize, list parse, collections) |
| `collections_valid_count` | >= 3 | with `collections: true`: the LEAST usable items any checked collection yields (unreadable = 0) |
| `collections_normalize_ok_ratio` | >= 0.9 | same: the LOWEST normalized / usable share over the collections |
| `playable_ratio` | >= 0.67 | with `playable: true`: up to 3 DIFFERENT normalized titles are followed from their playback page to a stream; resolved / checked (`skipped` not counted; at least one must resolve) |
| `series_have_episode_sources` | >= 0.9 | same, series items only: those carrying episode `video_sources` / all, OR (the better) the share of series pages the yaml `series_page` gave episodes for |
| `series_inventory_ok` | >= 1 | with `playable: true` AND a `series_page:` block: the series pages of up to 3 DIFFERENT series are read (`series{}`); every one must give an episode (`skipped` not counted, none read = 0) |

`passed` is true only when every criterion is met.
<!-- END GENERATED quality-criteria -->

## What fixes what

Every failing criterion also comes with a `hint` in `failing[]`; the `diagnostics` of the report show the first rows and what each selector caught.

| failing criterion | look at | typical cause / fix |
|---|---|---|
| `config_errors` > 0 with a regex message | `errors`, `failing[].hint` | a regex with a DOUBLE backslash (`\\d`): in a single-quoted yaml scalar write `\d` (double-quoted: `\\d`); `\\d` matches a literal backslash + d, never a digit |
| `valid_count` low | `list.count`, `list.valid_count`, `errors` | `row_selector` matches too little (JavaScript-rendered page: `fetch_mode: browser`) or rows fail the schema (empty `title`, wrong type: add `cast`) |
| `title_fill` / `detail_url_fill` | `list.field_fill`, `list.samples` | the selector misses some card variants (`fallback`), or `row_selector` also matches non-title blocks |
| `poster_url_fill` | `list.field_fill.poster_url` | lazy images: `data-src` first, `src` last (`fallback`) |
| `normalize_ok_ratio` | `normalize.rejected` | `key.regex` does not match the real detail paths, or `host` is wrong (`normalize.md`) |
| `duplicate_key_ratio` | `normalize.duplicate_keys` | the key is too coarse (a category slug, a season number) or the list repeats cards |
| `config_errors` | `errors` | every message names the path (`list.fields.year.cast`, `resolvers[0]`): fix the first, test again |
| `collections_valid_count` | `collections[]` | a `row_selector` matches too few cards, `fields` miss `title` / `detail_url`, or `required_fields` removes everything: fix or drop it (`collections.md`) |
| `collections_normalize_ok_ratio` | `collections[].normalize_ok` | the cards link to another URL shape than the list's, so `normalize:` rejects them: fix or drop the collection |
| `playable_ratio` | `playable.samples[]`, `test_resolvers` | no player the `resolvers` match (`candidates: 0`), the provider / recipe fails on that host (`error`), `providers:` lacks the recipe, or `playback: trailer`: fix until every sample plays (`player-authoring.md`) |
| `series_have_episode_sources` | `normalize.episode_items`, `normalize.with_video_sources`, `normalize.series_without_sources`, `series{}` | EPISODE-page cards: key = SERIES slug + `normalize.episode_source` (`normalize.md`); SERIES-page cards (`series.hint`): a `series_page:` block (`series-page.md`); neither can work (JavaScript / API episode list): "needs series inventory" in `notes`, the admin decides |
| `series_inventory_ok` | `series.samples[]` | `series_page` finds no episode: `episode_url_regex` does not match, `row_selector` is wrong, the page needs `fetch_mode: browser`, or `source_url` is not the series page |

`normalize.key: key regex bölüm eki bırakıyor ...` (warning): the engine cut episode / release tails (`-26-bolum`, `-son-bolum-izle`, `-izle-hd`) off a series key
(and ` izle`, ` HD`, `| Site`, `N. Bölüm` off titles) because your `key.regex` let them through: fix the regex to capture the show slug only.

`warnings` do not fail a proposal but still matter (`no collections: ...`, a `skipped` collection, ...). Never drop a working field to
get a criterion through.

## Engine facts

Every list and collection ingests at most `item_limit` items (default 30, max 200); there is NO pagination and no way to take "all N titles".
Never claim a count beyond what `test_config` reports (`ingest.would_ingest`; per collection `collections[].would_ingest`). The whole catalogue is
the search feature's job (`search.md`). A low `valid_count` is a selector problem.

## Rules for the loop

Re-run `test_config` after EVERY edit and never report a result you did not just see; pass the `page_id` (and `detail_page_id`); never lower a bar by
editing the sample. At most 6 rounds of `test_config` for list / normalize, at most 4 for ONE failing criterion; a problem still open after that is
written in `notes` (`needs code: <host>` when yaml and recipes cannot fix it). Before `submit_repair` run `test_config(..., baseline: true,
playable: true)` once; never write `playback: video` for a yaml whose `playable.resolved` is 0.
