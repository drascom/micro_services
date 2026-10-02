# Acceptance criteria

`test_config` and `submit_draft` return `criteria` (`{value, min|max, ok}` per row below) and a single `passed` flag. The admin only trusts
`passed: true`; a failed draft can be saved only by a deliberate override.

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
| `playable_ratio` | >= 0.67 | with `playable: true` / `submit_draft` (always): up to 3 DIFFERENT normalized titles are followed from their playback page to a stream; resolved / checked (`skipped` not counted; at least one must resolve) |
| `series_have_episode_sources` | >= 0.9 | same, series items only: those carrying episode `video_sources` / all, OR (the better) the share of series pages the yaml `series_page` gave episodes for |
| `search_ok` | >= 1 | same AND a `search:` block: ONE live query gives >= 1 result AND the list item is among them (`found_known` not false); no block = only a warning |
| `availability_gate_defined` | >= 1 | NEW `playback: video` site: an `availability_gate` that is on (`probe` >= 1) (`blocked.md`); never skippable, never asked about |
| `series_signal_collection` | >= 3 | NEW series site, `collections: true`: the most usable items any `trending` / `latest_series` collection yields. Exempt after "Sitede yok, atla: `home_series_section`" |
| `series_full_inventory` | >= 1 | NEW series site, `playback: video`: a `series_page:` inventory OR no card is ONE EPISODE's page. Exempt after "Sitede yok, atla: `series_inventory`" |
| `home_path_is_canonical` | >= 1 | NEW site with a CERTAIN `redirect_hint` on the `list_url` page: 0 while `list_url` or a collection `path` still names the redirecting path |
| `collection_poster_fill` | >= 0.8 | NEW site, `collections: true`: the LEAST `poster_url` fill over the collections of the roles `trending`, `latest_series`, `latest_movies`, `noteworthy_movies`, `featured`. Exempt after "Sitede yok, atla: `collection_poster`" |
| `detail_info_defined` | >= 3 | NEW site: how many of the info groups `synopsis`, `year`, `cast`, `genres`, `rating`, `trailer_url`, `poster_url` the DETAIL fields define AND fill on EVERY parsed detail page (the `detail_page_id` page + a second one). Each group the admin answered "Sitede yok, atla: <field>" for leaves the bar; the agent never skips on its own |
| `series_inventory_ok` | >= 1 | with `playable: true` AND a `series_page:` block: the series pages of up to 3 DIFFERENT series are read (`series{}`); every one must give an episode (`skipped` not counted, none read = 0) |
| `ingest_sample_ok` | >= 0.8 | NEW series site with a `series_page:` that has `series_url_regex`: up to 10 DIFFERENT series items of the list AND the collections (episode cards included) go through the production key / title cleanup and the series-page directory (`series-page.md`), up to 5 of their pages are read; the share that resolves to a series page with a non-empty inventory. Never skippable |

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
| `ingest_sample_ok` | `series.ingest_sample{samples[], reasons}`, `diagnostics.series` (`where: ingest_sample`) | series that come from EPISODE cards cannot be read: `series_page_unknown` = no series page of the list / collections carries that title (make the series archive the `list_url` or a collection; match title / `normalize.key`), `not a series page` = `series_url_regex` does not fit, `same_series` = `series_slug_regex` / `same_series_regex`, `empty_inventory` = `row_selector` / `episode_url_regex` (`series-page.md`, "Series directory"). Never skippable |
| `search_ok` | `search{}`, `test_search` | no result (selectors / `url` / method / missing `Referer`; JavaScript-built results: `fetch: browser`) or `found_known: false` (`row_selector` matches other blocks, or another URL shape): `search.md`. No search form: no `search:` block, say so in `notes` |
| `availability_gate_defined` | the yaml | `playback: video` without `availability_gate`: write `availability_gate: {probe: 2, require: player}` (`blocked.md`); never a question |
| `series_signal_collection` | `collections[]`, `outline_page` `blocks` / `sections` / `nav_links` | no `trending` / `latest_series` collection gives >= 3 items: open the home blocks you have not judged yet ("Son Eklenen DİZİLER", "Trendler", menu pages); `link_kind: episode` = `latest_episodes`, `series` = `latest_series`. Only when the site really has none: `ask_user(field: "home_series_section")` |
| `series_full_inventory` | `normalize.episode_cards`, `blocks[].link_kind`, `nav_links` | EPISODE-page cards and no `series_page`: find the series archive, make `list:` read SERIES cards from it and write `series_page:` (`series-page.md`); episode cards stay in `latest_episodes`. Only when the series pages do not list episodes: `ask_user(field: "series_inventory")` |
| `home_path_is_canonical` | `redirect_hint` + `canonical_url` | `list_url` names a path the site redirects away from: fetch `redirect_hint.target`, write it as `list_url` and as the `path` of the home collections |
| `collection_poster_fill` | `collections[].field_fill`, the card's `outer_html` | a collection fills `poster_url` in < 80% of its cards (own `fields:` without it = 0): take it from the card's `<img>` (`data-src` before `src`). Only when the cards carry no picture: `ask_user(field: "collection_poster")` |
| `detail_info_defined` | `detail_info{good, missing}`, `detail.samples[]` | fewer than 3 info groups are defined AND filled on EVERY parsed detail page: `grep_page` the labels ("Özet", "Yıl", "Oyuncular", "Tür", "IMDb", "Fragman") and the og: meta tags (take a value from a block like `#icerikcat2` with a `regex`). A field the site really lacks is asked per field (`ask_user(field: "<field>")`); only the admin's "Sitede yok" counts, never delete a field to lower the bar |

`normalize.key: key regex bölüm eki bırakıyor ...` (warning): the engine cut episode / release tails (`-26-bolum`, `-son-bolum-izle`, `-izle-hd`) off a series key
(and ` izle`, ` HD`, `| Site`, `N. Bölüm` off titles) because your `key.regex` let them through: fix the regex to capture the show slug only.

`removed_fields` (the FIRST warning "alan kaldırıldı: ..."): a detail / collection field that gave values in your previous submission is gone from
the yaml; it stays reported until you put it back or the admin answers "Sitede yok, atla: <field>". Never drop a working field to get a criterion
through. `warnings` do not fail a draft but still matter (`no collections: ...`, a `skipped` collection, `no search block: this site will not be
searchable`, ...).

## Engine facts

Every list and collection ingests at most `item_limit` items (default 30, max 200); there is NO pagination and no way to take "all N titles".
Never claim a count beyond what `test_config` reports (`ingest.would_ingest`; per collection `collections[].would_ingest`). The whole catalogue is
the search feature's job (`search.md`). A low `valid_count` is a selector problem.

## Rules for the loop

Re-run `test_config` after EVERY edit and never report a result you did not just see; pass the `page_id` (and `detail_page_id`); never lower a bar by
editing the sample. At most 6 rounds of `test_config` for list / normalize, at most 4 for ONE failing criterion; a problem still open after that is
asked (`ask_user`, "Missing information protocol" in `SKILL.md`), not submitted with a note. Before `submit_draft` run `test_config(..., collections:
true, playable: true)` once (`submit_draft` always runs it); never write `playback: video` for a draft whose `playable.resolved` is 0.
