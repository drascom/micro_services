# `search:` - the live search of a site

The list and the `collections` give a page of notable titles, never the whole catalogue; the rest is found by SEARCHING: the server sends a
user's query (live, one request per site and query) to the `search:` block of every site that has one and merges the answers by title. The block
tells the server how to ask THIS site (where the query goes, how the answer is read). No code, no crawling: one request, the answer is read into
`title`, `detail_url`, `poster_url`, `year`. Write it only when the site has a search form or endpoint you have seen; without one: no block (the
server warns "no search block: this site will not be searchable"), and say why in `notes`.

## Finding the search

The home page of step 1: `query_html(page_id, "form[action]", attr: "action")` and `form input[name]` (`method="get"` + `action="/"` + `name="s"` =
WordPress, `/?s={query}`; `method="post"` + an `action` path = `method: POST` + `form`). No form (a script calls an endpoint): `grep_page(page_id,
"ajax|/api/|search|arama|keyword")` finds the URL and parameter; such endpoints answer JSON (`format: json`) or an html fragment. `fetch_page` that URL
with a list item's title: the answer is what the server will read (`outline_page` / `query_html` give `row_selector` and `fields`; JSON: dotted
paths). 403 / 404 / "Just a moment": fetch with `referer` (the home page URL) and put the same `Referer` into `headers:`; only a results page
JavaScript builds (empty `html_excerpt`, results in `mode: "browser"`) needs `fetch: browser`.

## Keys

<!-- BEGIN GENERATED search-keys (tools/gen_onboard_refs.py; do not edit by hand) -->
| key | required | meaning |
|---|---|---|
| `url` | yes | where the query goes: a site path (`/?s={query}`), `{base}/...` or an absolute URL on the SAME host; `{query}` is URL-encoded by the server; a GET url must contain it |
| `method` | no | `GET` (default) or `POST` |
| `form` | no | POST body as a urlencoded form `{field: "{query}", ...}` (the inputs' `name`s; fixed fields as plain text); a POST needs `{query}` in `url`, `form` or `json` |
| `json` | no | POST body as JSON instead of `form` (`{query}` in any text value); only one of the two |
| `headers` | no | ONLY `User-Agent`, `Referer`, `Origin`, `Accept`, `X-Requested-With` (never a cookie or token); `{base}` / `{query}` work; `Referer` defaults to the home page |
| `fetch` | no | `http` (default; Chrome TLS fingerprint, a POST shares a cookie session with a first page view) or `browser` (slow; GET + html only; JavaScript-built results) |
| `format` | no | `html` (default: `row_selector` + `fields` as CSS) or `json` (dotted paths) |
| `row_selector` | html | CSS, one match per result card (or the result links themselves); `fields` are relative to ONE row |
| `fields` | yes | `title` and `detail_url` mandatory, `poster_url` and `year` optional. html: field specs as in `config-schema.md`; json: a dotted path (`data.name`) or `{path, template}` with `{value}` / `{base}` (`{base}/dizi/{value}`) |
| `results_path` | no | json: dotted path of the result LIST (`data.result`); empty = the answer is the list |
| `limit` | no | most results kept, 1..20 (default 20) |
| `cache_ttl` | no | seconds a query is remembered, 0..3600 (default 300; 0 = never) |

Any other key is an error (`search: unknown key ...`).
<!-- END GENERATED search-keys -->

The server enforces (an error of `test_search`): `{query}` is the user's text (3 to 100 characters); every URL a public address on the SITE'S OWN
host (a result on another host is dropped; a poster may be elsewhere); at most 3 redirects, 10 s and 3 MB per request, no retry. No cookie, token
or session value in the block.

## What `test_search` tells you

`test_search(yaml_text, query?, page_id?)` runs the block on ONE live query in memory (nothing is written): `valid`, `errors`, `warnings`,
`count`, `samples[]` (up to 5), `normalize_ok_ratio` and the strong proof `found_known`: without `query` it searches the title of the FIRST
LIST ITEM (pass the list `page_id`), and `found_known: true` means that item's `detail_url` (or normalize key) is among the results. A `query`
that is part of a list item's title is judged the same way; any other query is not (`found_known: null`). The query is the SERIES name: a card title
("Halef 37.Bölüm", "Dizi S04E10", "... izle | Site") is cut to "Halef"; 0 results = one retry with the first two words (`query_used`,
`fallback_used`); `resolved_to_series` counts the results that resolve to a series page (`series-page.md`, "Series directory").

| you see | cause / fix |
|---|---|
| `errors`: `url: ...`, `fields.detail_url: required`, `row_selector: required` | the block does not validate: fix the key the message names |
| `count: 0`, `errors` empty | the selectors match nothing: `fetch_page` the search URL again and compare its `html_excerpt` with `row_selector`; an empty page = JavaScript (`fetch: browser`) or a missing header / `Referer`; "no results": try another list title with `query` |
| `errors`: `... failed (arama isteği başarısız: HTTP 403)` | the site refuses the request: `headers: {Referer: "{base}/"}` (and `X-Requested-With: XMLHttpRequest` for an ajax endpoint), check GET vs POST and the form field names |
| `errors`: `... geçersiz JSON döndürdü` | the endpoint answers html: use `format: html`, or the JSON endpoint is another URL |
| `count >= 1` but `found_known: false` | the results are other pages (`row_selector` also matches "related" blocks: narrow it) or their links have another shape than the list cards (a `template` / the real `href` selector): compare `samples[].detail_url` with the list item's |
| `normalize_ok_ratio` low | the results link to URLs `normalize.key.regex` rejects (search shows series pages, the list episode pages): the key must name the same title in both |

`test_config(playable: true)` and `submit_draft` run this query once more as the criterion `search_ok` (>= 1 result and `found_known` not
false); a yaml without `search:` has no such criterion, only a warning.

## Examples

The `search:` block only (`base_url` is the site's own): the four shapes sites really have.

### 1. WordPress `?s=` (GET, html)

`<form method="get" action="/"><input name="s">`: cards, lazy-loaded posters (`data-src` first).

```yaml
search:
  url: "/?s={query}"
  row_selector: "div.search-results article.post"
  fields:
    title: {selector: "h2.entry-title a"}
    detail_url: {selector: "h2.entry-title a", attr: href}
    poster_url:
      fallback:
        - {selector: "img", attr: data-src}
        - {selector: "img", attr: src}
    year: {selector: "span.year", regex: '(\d{4})', cast: int}
```

### 2. POST form (html)

`<form method="post" action="/arama"><input name="q"><input type="hidden" name="tur" value="tumu">`: the form's fields go into `form` (a POST first
views the home page to share its cookies).

```yaml
search:
  url: "/arama"
  method: POST
  form: {q: "{query}", tur: "tumu"}
  row_selector: "ul.sonuclar li"
  fields:
    title: {selector: "a.baslik"}
    detail_url: {selector: "a.baslik", attr: href}
    poster_url: {selector: "img", attr: src}
```

### 3. JSON endpoint (POST, json)

`POST /api/search` with `{"keyword": "..."}` answers `{"data": {"items": [{"name", "slug", "poster", "year"}]}}`: `format: json`, `results_path`,
dotted paths; a `template` turns a slug into the page URL.

```yaml
search:
  url: "/api/search"
  method: POST
  json: {keyword: "{query}", type: "all"}
  format: json
  results_path: "data.items"
  fields:
    title: "name"
    detail_url: {path: "slug", template: "{base}/dizi/{value}"}
    poster_url: {path: "poster", template: "{base}/uploads/{value}"}
    year: "year"
```

### 4. Ajax fragment that wants a Referer (GET, html)

`/ajax/ara?kelime=dark` answers 403 to a plain request (it needs the page script's `Referer` and `X-Requested-With`) and returns result links
(the link is the row: `self: true`).

```yaml
search:
  url: "/ajax/ara?kelime={query}"
  headers: {Referer: "{base}/", X-Requested-With: XMLHttpRequest}
  fetch: http
  row_selector: "a.arama-sonuc"
  fields:
    title: {selector: "span.ad"}
    detail_url: {self: true, attr: href}
    poster_url: {selector: "img", attr: src}
```
