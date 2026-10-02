# Provider recipes: the reusable provider library

A **provider** turns a player URL into streams. `list_resolvers` lists them all, each with a `kind`: `code` (a module inside the server: `vidmolly`,
`okru`; you cannot add one) or `recipe` (a yaml file of the provider library, `configs/providers/<name>.yaml`; you may add one). A recipe says WHICH
player URLs it owns (`match`) and HOW to read the media out of the player page (`fetch`, `referer`, `follow`, `extract`: the rules of
`references/player-authoring.md`). Once saved, every site whose resolvers find such a URL plays through it: the next site that embeds the same
player costs nothing.

## The modular split (the rule)

| part | where it lives |
|---|---|
| where on the DETAIL page the player URL stands (an iframe, links, a token): selector, attribute | the site yaml, `resolvers:` (`iframe`, `anchor_host`, `data_attr_token`, ...) |
| what a player URL of host X means: how to fetch it, which referer, where the media URL is | a **provider**: an existing one (`providers: [name]`) or a NEW recipe (`submit_draft(provider_recipes=[...])`) |

Do NOT put a `player_page` resolver into the site yaml for a player host another site could embed too: write a recipe (a site-embedded `player_page`
is only for a player meaningless anywhere else; say why in `notes`).

## Decision flow

1. `list_resolvers`: a provider (code or recipe) whose `hosts` cover the player's host? Then the site yaml only needs a resolver that finds the URL
   and `providers: [<name>]`; `test_resolvers`. Never write a second recipe for a host a recipe already owns.
2. No provider knows the host (or the player is on the site's own host: `/player/oynat/...`): write a recipe (`references/player-authoring.md`),
   try it with `test_provider` on several player URLs; the site yaml gets `resolvers:` (finds the player URL) + `providers: [<recipe name>]`.
3. The player needs something rules cannot do (a signature computed by JavaScript, a cookie only a login gives, a token from a second API call):
   `needs code: <host>` in `notes` with the evidence, and stop; never fake a recipe.

## Recipe format

```yaml
name: trdizi_player             # ^[a-z][a-z0-9_]{1,31}$; never "vidmolly" / "okru"; not the name of a recipe that exists
description: "Player pages of trdiziizle.tv: HLS URL in a sources array; 404 without the detail page as referer."
version: 1
match:
  host_regex: '(^|\.)trdiziizle\.tv$'     # required: regex searched in the lower-case HOSTNAME (case-insensitive)
  path_regex: '^/player/oynat/'            # optional: regex searched in the URL path
fetch: http                     # http (default; Chrome TLS fingerprint) | browser (10+ s per page)
referer: "{page_url}"           # the Referer header; {page_url} = the detail / episode page, {base} = its origin
extract:
  - regex: 'sources\s*:\s*\[\s*\{\s*file\s*:\s*\x22([^\x22]+)\x22'
```

| key | meaning |
|---|---|
| `name`, `description`, `version` | required (`version: 1` for a new recipe; the server numbers it); `description` = 1-2 sentences for the catalog |
| `match.host_regex` / `match.path_regex` | which player URLs the recipe owns (below) |
| `label` | optional provider name shown for the streams (default: the `name`) |
| `fetch`, `referer`, `headers`, `warm_session`, `wait_for`, `follow`, `extract`, `stream_headers`, `verify` | as the `player_page` parameters (`references/resolvers.md`; the rules: `references/player-authoring.md`) |

No `selector` / `attr` / `host_regex` outside `match`: a recipe starts from a player URL the site yaml's `resolvers:` already found. Unknown keys
are errors.

## `match`

- `host_regex` is **searched** (`re.search`, not a full match) in the lower-case hostname, so anchor it: `(^|\.)example\.tv$` matches `example.tv`
  and `www.example.tv`, not `badexample.tv`; several domains: `(^|\.)(a|b)\.tv$`. `path_regex` narrows it to the player's paths when the host also
  serves other things. A host regex that matches every host (`.*`, `.`) is refused: keep it as narrow as the player really is.
- Code providers are tried first, then recipes by name; the first whose `match` fits a URL owns it. A site yaml with `providers: [...]` lets only
  the listed providers own URLs (list your recipe name there).

## Write, test, hand in

1. `test_provider(recipe_yaml, sample_url, referer)` with `sample_url` = a player URL (a `candidates[].url` of `test_resolvers` or the iframe `src`)
   and `referer` = the detail / episode page it sits in: `valid` / `errors`, `matched` (does `match` cover the URL), `status` (`resolved`,
   `no_stream`, `no_match`, `invalid`), `streams[{type, host, quality}]`, `trace`, `warnings`. Run it on at least 3 player URLs of DIFFERENT
   titles before you trust the recipe.
2. End to end: `test_resolvers(yaml_text, detail_url, provider_recipes=[{name, yaml}])` and `test_config(..., playable: true,
   provider_recipes=[...])`: the recipe joins the providers in memory, so `providers: [<name>]` in the draft yaml is accepted.
3. `submit_draft(..., provider_recipes=[{name, yaml}])` (at most 3 per draft). The report lists each recipe (valid? playable samples resolved
   through it, example streams); the admin saves the recipes into the library with the site. Recipes the draft already carries are kept when you
   submit again without `provider_recipes`; a list replaces them.

The `name` of an entry and the `name:` in its yaml must be the same. A name that is a code provider's, or already taken in the library, is
refused (reference the existing recipe in `providers:`).

## Example recipes (each is validated and run against a sample page by the test-suite)

Read the file that looks like what `grep_page` found (absolute path inside the skill directory):

| file in `references/examples/providers/` | the player page shows |
|---|---|
| `plain_url_player.yaml` | `file:"https://x/y.m3u8"` or `"src":"https:\/\/x\/y.mp4"` in a script: a whole URL, two rules |
| `source_tag_player.yaml` | `<video><source src=...>`: `css` rules, one per quality |
| `packed_player.yaml` | `eval(function(p,a,c,k,e,d)`: `unpack: true` |
| `base64_player.yaml` | `atob("aHR0c...")`: `base64: true` |
| `nested_browser_player.yaml` | Cloudflare plus an inner iframe: `fetch: browser`, `wait_for`, `follow` |
| `json_api_player.yaml` | the player URL answers JSON: `json_path`, a site referer, an XHR header |
| `referer_player.yaml` | `404` / "Just a moment" without the detail page as referer: `fetch: http` + `referer: "{page_url}"` |
| `quality_player.yaml` | `sources: [{file, label}, ...]`: `quality_group`, best quality first |
| `mixed_formats_player.yaml` | HLS on some titles, extension-less mp4 file hosts on others: one rule per format |

A site yaml that uses a recipe only names it and finds the player URL (`referer_player` is the recipe above):

```yaml
resolvers:
  - type: iframe
    selector: 'iframe[src*="/player/oynat/"]'
    attr: src
providers: [referer_player]
```

## Rules

One recipe per player host (family); no site name, title or session value (cookie, token) in it (every site shares it). `playback: video` and the
recipe only when `test_provider` / `test_resolvers` really produced streams. A recipe that resolved on one page only is a guess: several player URLs,
one `extract` rule per format. No code, ever: `needs code: <host>`.
