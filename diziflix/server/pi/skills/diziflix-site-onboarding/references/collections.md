# Collections (home sections)

A **collection** is one named group of titles the site itself shows on its HOME page (or a menu page such as "Trendler"). diziflix merges the
collections of every site by role: all "Trendler" fill the "Haftanın Trendleri" rows, all "Dikkate değer" the "Dikkate Değer Filmler" row; the
new-series / new-episodes / new-films sections and the hero slider have no row, they only rank titles for the slider and trend rows. The home
screen shows playable titles only. The question is NOT "what is in the whole catalogue" (search handles that) but "which notable sections does
this home page offer": write every section the site shows, never invent one.

Understand the site first (`outline_page` on the home page: `blocks`, `sections`, `nav_links`; `redirect_hint` = the home page is another path:
fetch that), then one collection per section. Decide EVERY repeating block of `blocks` (a role, or "not used" with the reason in `notes`):
`link_kind` `series` = `latest_series` / `trending`, `episode` = `latest_episodes`. A series site needs a `trending` or `latest_series`
collection with >= 3 items (`series_signal_collection`); every `trending` / `latest_series` / `latest_movies` / `noteworthy_movies` /
`featured` collection needs `poster_url` in >= 80% of its cards (`collection_poster_fill`; with own `fields`, add it from the card's `<img>`).

<!-- BEGIN GENERATED collection-roles (tools/gen_onboard_refs.py; do not edit by hand) -->
A collection id is `<role>_<site_id>` (`collections.list_id`), e.g. `trending_ornekfilm`; one collection per role, at most 8 per site. The home screen merges the collections of the same role of EVERY site.

| role | feeds | write it? |
|---|---|---|
| `trending` | feeds the 'Haftanın Trendleri' rows and scores for the slider | yes |
| `latest_episodes` | ranking signal for the home slider and the trend rows; no row of its own | yes |
| `latest_series` | ranking signal for the home slider and the trend rows; no row of its own | yes |
| `latest_movies` | ranking signal for the home slider and the trend rows; no row of its own | yes |
| `noteworthy_movies` | feeds the 'Dikkate Değer Filmler' row and also scores for the slider and trends | yes |
| `featured` | ranking signal for the home slider and the trend rows; no row of its own | yes |
| `upcoming` | the 'Yakında' row: not on the home screen, only reachable through /api/row/yakinda | yes (not on the home screen) |
| `new` | newest titles list | no |
| `catalog` | plain catalogue list | no |
| `genre` | genre row | no |
<!-- END GENERATED collection-roles -->

## Section name -> role

| the site says (examples) | role |
|---|---|
| Trendler, Popüler, Çok izlenenler, Haftanın trendleri | `trending` |
| Son eklenen diziler, Yeni diziler, Yeni eklenen diziler, Yeni başlayan diziler | `latest_series` |
| Yeni eklenen bölümler, Son bölümler, Güncellenen diziler | `latest_episodes` |
| Yeni filmler, Son eklenen filmler, En son filmler | `latest_movies` |
| Dikkate değer, Editörün seçimi, IMDb en iyi, Önerilen filmler | `noteworthy_movies` |
| the hero / slider / "Öne çıkanlar" at the top | `featured` |
| Yakında, Vizyona girecekler | `upcoming` |

`latest_series` vs `latest_episodes`: look at what ONE card is. A SHOW (links to a series page; "Yeni eklenen DİZİLER") = `latest_series`; ONE
EPISODE ("Yeni eklenen BÖLÜMLER", names a season / episode) = `latest_episodes`. Never mix them up; a site often has both (two collections).
Never write `role: new` or `role: catalog`: nothing reads them. Never "Tüm diziler", "Tüm filmler", an A-Z index, a genre listing or a paged
catalogue (`?page=2`); a site without such sections gets fewer collections (or none): say so in `notes`.

## Keys of one collection

| key | meaning |
|---|---|
| `id` | `<role>_<site_id>` (the yaml `site_id` = your `site_id_suggestion`); one collection per role |
| `title` | the section's name as the site writes it |
| `path` | a path of the site (`/`, `/trendler`, `/filmler?sirala=imdb`) or an absolute URL on the SAME host |
| `role` | one of the roles above |
| `row_selector` | optional: the cards of THIS section (when it shares a page with others, or its container differs from `list.row_selector`) |
| `fields` | optional: own field specs; they REPLACE `list.fields` completely (so they need `title` and `detail_url`) |
| `required_fields` / `excluded_fields` | keep only cards where these fields are filled / empty (e.g. `required_fields: [season, episode]`) |
| `sort_by` / `sort_desc` | order by a parsed numeric field, e.g. a score |

`path` is fetched and parsed with `list.row_selector` + `list.fields` unless the collection has its own. Each card must normalize with the SAME
`normalize:` rules as the list. At most 8 collections, each with at least 3 usable items (`collections_valid_count`); each ingests at most
`item_limit` items (`would_ingest` in the report).

## Examples

Sections on the home page (`list_url: /`): same `path`, one `row_selector` each.

```yaml
site_id: ornekfilm
base_url: https://ornekfilm.example
list_url: /
collections:
  - id: trending_ornekfilm
    title: Trendler
    path: /
    role: trending
    row_selector: "section.trending div.film-card"
  - id: latest_movies_ornekfilm
    title: Son Eklenen Filmler
    path: /
    role: latest_movies
    row_selector: "section.latest div.film-card"
  - id: featured_ornekfilm
    title: Öne Çıkanlar
    path: /
    role: featured
    row_selector: "div.hero-slider div.film-card"
```

Sections on their own pages (`nav_links`), the list's card markup: only `path` and `role`.

```yaml
site_id: ornekfilm
base_url: https://ornekfilm.example
list_url: /
collections:
  - {id: trending_ornekfilm, title: Trendler, path: /trendler, role: trending}
  - {id: noteworthy_movies_ornekfilm, title: Dikkate Değer Filmler, path: "/filmler?sirala=imdb", role: noteworthy_movies}
```

A section with its own markup: own `fields` (and a numeric sort).

```yaml
site_id: ornekfilm
base_url: https://ornekfilm.example
list_url: /
collections:
  - id: trending_ornekfilm
    title: Trendler
    path: /trendler
    role: trending
    row_selector: "#weekly .item > a"
    sort_by: score
    sort_desc: true
    fields:
      title: {selector: h5}
      detail_url: {self: true, attr: href}
      poster_url:
        fallback:
          - {selector: "img[data-src]", attr: data-src}
          - {selector: "img[src]", attr: src}
      score: {selector: .description, regex: '(\d+)\s*puan', cast: int}
```

A series site: new SERIES (cards are shows), new EPISODES (`season` / `episode` must be fields the list parse fills; `required_fields` keeps the
cards that have them, `excluded_fields` drops hero cards) and the upcoming titles.

```yaml
site_id: ornekdizi
base_url: https://ornekdizi.example
list_url: /
collections:
  - id: latest_series_ornekdizi
    title: Son Eklenen Diziler
    path: /
    role: latest_series
    row_selector: "section.new-series article"
  - id: latest_episodes_ornekdizi
    title: Yeni Eklenen Bölümler
    path: /
    role: latest_episodes
    row_selector: "section.new-episodes article"
    required_fields: [season, episode]
    excluded_fields: [featured]
  - {id: upcoming_ornekdizi, title: Yakında, path: /yakinda, role: upcoming}
```

## Loop

`test_config(yaml_text, page_id, detail_page_id, collections: true)`: each `collections[]` entry has `status` (ok / error / skipped), `count`,
`valid_count`, `normalize_ok`, `errors`, `samples` and a `page_id` for `query_html`. Fix the failing ones (selector matches nothing, too few
cards, a normalize reject) or drop them (at most 6 rounds); `skipped` = out of call time: test again with `page_id` + `detail_page_id`. In
`notes` list the roles you found (with the site's section name) and those you looked for but the site does not have.
