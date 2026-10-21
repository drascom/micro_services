"""Regenerate the machine-derived parts of the pi onboarding skill references (no network, no real data touched).

    venv/bin/python -m tools.gen_onboard_refs          # rewrite the generated blocks
    venv/bin/python -m tools.gen_onboard_refs --check  # exit 1 when a file is out of date (tests/test_onboard_refs.py)

Everything else in ``server/pi/skills/diziflix-site-onboarding/references/`` is written by hand. A generated block sits
between ``<!-- BEGIN GENERATED <name> ... -->`` and ``<!-- END GENERATED <name> -->`` markers:

* ``resolvers-catalog`` in ``resolvers.md``: the resolver types (``resolvers.catalog()``) and the CODE providers
  (``registry.catalog()``, ``kind: code``; the data-driven recipes of the provider library change at run time and are
  listed by the ``list_resolvers`` tool), so the skill always names exactly what the code accepts.
* ``quality-criteria`` in ``quality.md``: the acceptance constants of ``routers/onboard_sandbox.py`` (``passed``).
* ``collection-roles`` in ``collections.md``: the collection roles (``scraper/collections.ROLES`` / ``HOME_ROLES``), which of
  them onboarding writes (``onboard_sandbox.ONBOARD_ROLES``) and the id convention (``collections.list_id``).
* ``series-page-keys`` in ``series-page.md``: the keys of the yaml ``series_page:`` block (``series_generic.SPEC_KEYS``, each
  with its meaning from ``SERIES_KEY_DOCS`` below; a key without a doc line is an error here, so the table is never stale).
* ``edit-tools`` / ``edit-layers`` in ``edit.md``: the edit-mode tool list (``pi_agent.EDIT_TOOLS``) and the layers an edit request may touch
  (``heal_agent.LAYERS`` keys + ``EDIT_LAYER_DOCS`` below: what the request is about; a layer without a doc line is an error here).
* ``search-keys`` in ``search.md``: the keys of the yaml ``search:`` block (``search_generic.SPEC_KEYS``, each with its meaning from
  ``SEARCH_KEY_DOCS`` below; same rule: an undocumented key is an error).
"""
import json
import os
import re
import sys

_SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_SERVER, "tests"))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB, must precede any `app` import

REFS_DIR = os.path.join(_SERVER, "pi", "skills", "diziflix-site-onboarding", "references")
#: file (relative to REFS_DIR) -> generated block names it must contain
TARGETS = {"resolvers.md": ("resolvers-catalog",), "quality.md": ("quality-criteria",),
           "collections.md": ("collection-roles",), "series-page.md": ("series-page-keys",), "search.md": ("search-keys",),
           "heal.md": ("repair-layers", "repair-gates"), "edit.md": ("edit-tools", "edit-layers")}
#: key of the yaml ``series_page:`` block -> (required?, meaning); the keys must be exactly ``series_generic.SPEC_KEYS``
SERIES_KEY_DOCS = {
    "row_selector": ("yes", "CSS, one match per episode row (`<li>` / `<tr>` / `<div>` holding the episode link) or the episode links themselves (`a[href]`)"),
    "episode_url_regex": ("yes", "regex on the URL PATH of an episode link; named group `episode` mandatory, `season` optional, `slug` = the series part "
                                 "(drops other series' episodes); the numbers always come from the URL, never the markup"),
    "fields": ("no", "`url` (default: the row, or its first `a[href]`), `title` (default \"N. Bölüm\"), `air_date` (`cast: date_tr`); field specs as in `config-schema.md`"),
    "default_season": ("no", "season of a site whose episode URLs carry none (whole number >= 1, default 1)"),
    "series_url_regex": ("no", "the PAGE must be a series page: regex on its URL path (else nothing is read)"),
    "series_slug_regex": ("no", "regex on the page URL path whose group `slug` (or group 1) is the series slug; default: the most common slug among the episode links"),
    "same_series_regex": ("no", "regex with `{slug}` an episode path must match: drops episodes of OTHER series (\"similar series\" blocks)"),
    "season_pages": ("no", "seasons on separate pages: `{season_menu: <CSS of the season links>, season_url_regex: <regex with group `season`>}` or a bare CSS selector"),
    "season_menu": ("no", "top-level spelling of `season_pages.season_menu` (prefer `season_pages`)"),
    "season_url_regex": ("no", "top-level spelling of `season_pages.season_url_regex` (prefer `season_pages`)"),
    "unaired_classes": ("no", "row classes of episodes not aired yet (not written)"),
    "first_episode": ("no", "CSS of the site's own \"first episode\" link: the list is verified against it"),
    "last_episode": ("no", "CSS of the site's own \"last / newest episode\" link: the list is verified against it"),
}
#: key of the yaml ``search:`` block -> (required?, meaning); the keys must be exactly ``search_generic.SPEC_KEYS``
SEARCH_KEY_DOCS = {
    "url": ("yes", "where the query goes: a site path (`/?s={query}`), `{base}/...` or an absolute URL on the SAME host; `{query}` is URL-encoded by the server; "
                   "a GET url must contain it"),
    "method": ("no", "`GET` (default) or `POST`"),
    "form": ("no", "POST body as a urlencoded form `{field: \"{query}\", ...}` (the inputs' `name`s; fixed fields as plain text); a POST needs `{query}` in `url`, `form` or `json`"),
    "json": ("no", "POST body as JSON instead of `form` (`{query}` in any text value); only one of the two"),
    "headers": ("no", "ONLY `User-Agent`, `Referer`, `Origin`, `Accept`, `X-Requested-With` (never a cookie or token); `{base}` / `{query}` work; `Referer` defaults to the home page"),
    "fetch": ("no", "`http` (default; Chrome TLS fingerprint, a POST shares a cookie session with a first page view) or `browser` (slow; GET + html only; JavaScript-built results)"),
    "format": ("no", "`html` (default: `row_selector` + `fields` as CSS) or `json` (dotted paths)"),
    "row_selector": ("html", "CSS, one match per result card (or the result links themselves); `fields` are relative to ONE row"),
    "fields": ("yes", "`title` and `detail_url` mandatory, `poster_url` and `year` optional. html: field specs as in `config-schema.md`; json: a dotted path "
                      "(`data.name`) or `{path, template}` with `{value}` / `{base}` (`{base}/dizi/{value}`)"),
    "results_path": ("no", "json: dotted path of the result LIST (`data.result`); empty = the answer is the list"),
    "limit": ("no", "most results kept, 1..20 (default 20)"),
    "cache_ttl": ("no", "seconds a query is remembered, 0..3600 (default 300; 0 = never)"),
}


#: layer -> what an EDIT request about it sounds like; the layers are ``heal_agent.LAYERS`` (the same scope vocabulary as a repair, keys
#: from there) plus ``EDIT_EXTRA_LAYERS`` (parts of a site a repair never changes but an admin may ask to)
EDIT_LAYER_DOCS = {
    "list": "the list page or a home section (`collections:`): other / more titles, a new or changed section, title / poster / year selectors",
    "detail": "the fields of a detail page: synopsis, genres, cast, trailer, player address",
    "series_page": "the episode list of a series page: rows, season pages, episode dates",
    "normalize": "how a title is keyed and typed, where episode sources come from (`normalize.episode_source`)",
    "resolvers": "how the player is found on a page (`resolvers:`) and which providers the site may use",
    "provider": "a video HOST that changed or is new: its recipe lives in the provider LIBRARY (`provider_recipes`); the site yaml only names it in `providers`",
    "fetch": "how a page is fetched: plain http or the browser",
}
EDIT_EXTRA_LAYERS = {
    "search": (("search",), "the live search of the site (`search:`)"),
    "catalogue": (("list_url", "item_limit", "playback"), "the main list page, how many titles a scan takes, `video` or only a trailer"),
    "images": (("image_hosts",), "the hosts the artwork may come from"),
}


def _cell(value) -> str:
    return re.sub(r"\s+", " ", str(value)).replace("|", "\\|").strip()


def _flat(value) -> str:
    """One line of text (for a list item, where ``|`` needs no escape)."""
    return re.sub(r"\s+", " ", str(value)).strip()


def _default(value) -> str:
    return "-" if value is None else "`%s`" % _cell(json.dumps(value, ensure_ascii=False))


#: (resolver type, parameter) -> a short meaning where the code's own ``help`` (written for the admin API) is long or cuts badly at its first
#: sentence; every other parameter gets the first clause of the code's help (``_gloss``). The full text is what ``list_resolvers`` returns.
HELP_SHORT = {
    ("data_attr_token", "url_template"): "candidate URL with `{token}` (percent-encoded) and optional `{base}`, e.g. `{base}/api/moly/{token}`",
    ("data_attr_token", "expect_host_regex"): "hand-off mode: the URL is a site page, its first `iframe[src]` becomes the provider URL (hostname must match this regex)",
    ("data_attr_token", "browser_fallback"): "retry once with the browser session's cookies (10+ s) when the site refuses the light cookies",
    ("ajax_handoff", "form"): "`{field: 'attr:<attribute>' | 'const:<text>'}`, e.g. `{link: 'attr:data-link', type: 'const:videoGet'}`",
    ("ajax_handoff", "json_key"): "dotted key of the JSON answer holding the URL (else the first `iframe[src]` of the answer)",
    ("ajax_handoff", "expect_host_regex"): "the provider URL's hostname must match; a URL on the site's own host is opened once and its first `iframe[src]` taken",
    ("ajax_handoff", "cookie_seed"): "cookies for the requests: `{name: 'now_ms' | 'now_s' | literal}`, e.g. `{udys: 'now_ms'}`",
    ("ajax_handoff", "browser_fallback"): "retry once with the browser session's cookies (10+ s) when the site refuses the light cookies",
    ("json_api", "media_type"): "`mp4` or `hls` (the legacy stream_resolver `type`, renamed: `type` selects the resolver)",
    ("json_api", "stream_headers"): "headers the media FILE needs `{name: value}` (only User-Agent, Referer, Origin, Cookie; `{page_url}`, `{player_url}`, `{base}` work); "
                                    "served through the signed stream proxy (an HLS stream too when it is proxied at all: recipe `stream_proxy: true`, a server-IP-bound URL, "
                                    "learned or env; the proxy then sends them for the playlist and segments); only when the file refuses a plain player (HTTP 400 / 403)",
    ("json_api", "cache_ttl"): "seconds the streams may be reused (60..86400); leave it out unless the URLs carry no expiry and live much longer or shorter than ~15 minutes",
    ("player_page", "fetch"): "`http` (Chrome TLS fingerprint, sends Referer / headers / cookies) or `browser` (JavaScript-built pages only, no Referer); independent of the site's `fetch_mode`",
    ("player_page", "referer"): "Referer for the player page (`fetch: http`): `{page_url}` = the detail page, `{base}`; players often 404 / 403 without it",
    ("player_page", "warm_session"): "`fetch: http` only: GET the detail page first in the same session so its cookies go along (one extra request)",
    ("player_page", "follow"): "up to 2 hops into nested iframes, each `{selector, attr: src, regex?}`",
    ("player_page", "extract"): "1-8 rules (`regex` group | `css` + `attr` | `json_path`; plus `unpack`, `base64`, `unescape`, `type` auto|hls|mp4, `quality`, `label`, "
                                "`quality_group` / `label_group`): `references/player-authoring.md`; leave `type` on auto (a URL extension wins)",
    ("player_page", "stream_headers"): "as in `json_api`",
    ("player_page", "cache_ttl"): "as in `json_api`",
}
DESC_SHORT = {
    "player_page": "Opens the player page the detail page points to (typically an iframe on the site's own host; plain HTTP or the browser), optionally follows nested "
                   "iframes and finds the media URLs with declarative extract rules. Reusable players belong in the provider library as a recipe: use `player_page` only "
                   "for a player specific to this one site.",
}
COMMON_TYPES = ("iframe", "anchor_host", "data_attr_token", "ajax_handoff")
COMMON_PARAMS = ("label", "label_from", "lang", "lang_from")


def _first_sentence(text: str) -> str:
    """The first sentence of a type description (the rest is detail the parameter list repeats)."""
    match = re.search(r"(?<!e\.g)(?<!i\.e)\.(?:\s+(?=[A-Z])|$)", text)
    return text[:match.start() + 1] if match else text


def _gloss(rtype: str, name: str, spec: dict) -> str:
    """One short clause for a parameter: the override, else the first clause of the code's own help."""
    text = HELP_SHORT.get((rtype, name))
    if text:
        return text
    text = re.split(r"(?<!e\.g)\.\s+(?=[A-Z])|; | \(e\.g\.|, e\.g\.| e\.g\.", spec.get("help", ""))[0].rstrip(".")
    return text[:1].lower() + text[1:] if text[:2].istitle() or text[:1].isupper() and text[1:2].islower() else text


def render_resolvers() -> str:
    from app.scraper import resolvers
    from app.scraper.providers import registry

    out = ["Resolver types (`resolvers:` items are flat: `{type: <name>, <parameter>: <value>, ...}`). `iframe`, `anchor_host`, `data_attr_token` and "
           "`ajax_handoff` also take `label` (constant candidate label; default the host), `label_from` (`text` or an attribute name), `lang` (constant "
           "language code, e.g. `tr`) and `lang_from` (`text` or an attribute name). The full parameter text is what `list_resolvers` returns.", ""]
    for entry in resolvers.catalog():
        shared = entry["type"] in COMMON_TYPES
        out += ["### `%s`" % entry["type"], "", _flat(DESC_SHORT.get(entry["type"]) or _first_sentence(entry["description"])), "",
                "| parameter | default | meaning |", "|---|---|---|"]
        for name, spec in entry["params"].items():
            if shared and name in COMMON_PARAMS:
                continue
            default = "**required**" if spec.get("required") else _default(spec.get("default"))
            out.append("| `%s` | %s | %s |" % (name, default, _cell(_gloss(entry["type"], name, spec))))
        out.append("")
    out += ["### Providers (`providers:` names; a provider turns a player URL into streams)", "",
            "`code` = a server module (listed here); `recipe` = a provider-library recipe (`configs/providers/<name>.yaml`; the library grows, so call "
            "`list_resolvers` for the recipes that exist now: `hosts` = `[host_regex, path_regex?]`).",
            "", "| name | kind | hosts | description |", "|---|---|---|---|"]
    for entry in registry.catalog():
        if entry.get("kind", "code") != "code":
            continue
        out.append("| `%s` | %s | %s | %s |" % (entry["name"], entry.get("kind", "code"), ", ".join("`%s`" % h for h in entry["hosts"]),
                                                _cell(entry["description"])))
    return "\n".join(out)


def render_quality() -> str:
    from app.routers import onboard_sandbox as sb

    rows = [("valid_count", ">= %d" % sb.MIN_VALID_COUNT, "list rows that pass the schema")]
    rows += [("%s_fill" % name, ">= %g" % minimum, "share of ALL parsed list rows with `%s` filled" % name)
             for name, minimum in sb.MIN_FILL.items()]
    rows += [("normalize_ok_ratio", ">= %g" % sb.MIN_NORMALIZE_OK_RATIO, "normalized items / valid items"),
             ("duplicate_key_ratio", "<= %g" % sb.MAX_DUPLICATE_KEY_RATIO,
              "keys produced by more than one SAME item (same title and episode) / normalized items"),
             ("config_errors", "== 0", "`errors` of the report (yaml, selectors, resolvers, normalize, list parse, collections)"),
             ("collections_valid_count", ">= %d" % sb.MIN_COLLECTION_COUNT,
              "with `collections: true`: the LEAST usable items any checked collection yields (unreadable = 0)"),
             ("collections_normalize_ok_ratio", ">= %g" % sb.MIN_NORMALIZE_OK_RATIO,
              "same: the LOWEST normalized / usable share over the collections"),
             ("playable_ratio", ">= %g" % sb.MIN_PLAYABLE_RATIO,
              "with `playable: true` / `submit_draft` (always): up to %d DIFFERENT normalized titles are followed from their playback page "
              "to a stream; resolved / checked (`skipped` not counted; at least one must resolve)" % sb.PLAYABLE_SAMPLES),
             ("series_have_episode_sources", ">= %g" % sb.MIN_SERIES_SOURCE_RATIO,
              "same, series items only: those carrying episode `video_sources` / all, OR (the better) the share of series pages the yaml "
              "`series_page` gave episodes for"),
             ("search_ok", ">= 1",
              "same AND a `search:` block: ONE live query gives >= 1 result AND the list item is among them (`found_known` not false); "
              "no block = only a warning"),
             ("availability_gate_defined", ">= 1",
              "NEW `playback: video` site: an `availability_gate` that is on (`probe` >= 1) (`blocked.md`); never skippable, never asked about"),
             ("series_signal_collection", ">= %d" % sb.MIN_COLLECTION_COUNT,
              "NEW series site, `collections: true`: the most usable items any `trending` / `latest_series` collection yields. "
              "Exempt after \"Sitede yok, atla: `%s`\"" % sb.SKIP_HOME_SERIES),
             ("series_full_inventory", ">= 1",
              "NEW series site, `playback: video`: a `series_page:` inventory OR no card is ONE EPISODE's page. "
              "Exempt after \"Sitede yok, atla: `%s`\"" % sb.SKIP_SERIES_INVENTORY),
             ("home_path_is_canonical", ">= 1",
              "NEW site with a CERTAIN `redirect_hint` on the `list_url` page: 0 while `list_url` or a collection `path` still names the redirecting path"),
             ("collection_poster_fill", ">= %g" % sb.MIN_COLLECTION_POSTER_FILL,
              "NEW site, `collections: true`: the LEAST `poster_url` fill over the collections of the roles %s. Exempt after \"Sitede yok, atla: `%s`\" (the poster field STAYS: taken where cards have it; `exempt[].optional`)"
              % (", ".join("`%s`" % r for r in sb.POSTER_ROLES), sb.SKIP_COLLECTION_POSTER)),
             ("detail_info_defined", ">= %d" % sb.MIN_DETAIL_INFO,
              "NEW site: how many of the info groups %s the DETAIL fields define AND fill on EVERY parsed detail page (the `detail_page_id` page + "
              "a second one). Each group the admin answered \"Sitede yok, atla: <field>\" for (\"Varsa al, yoksa atla\") leaves the bar but its field is KEPT in the yaml (taken on the pages that have it, empty on the rest; listed in `exempt` as `optional`); the agent never skips on its own"
              % ", ".join("`%s`" % g for g in sb.INFO_FIELDS)),
             ("series_inventory_ok", ">= %g" % sb.MIN_SERIES_INVENTORY_RATIO,
              "with `playable: true` AND a `series_page:` block: the series pages of up to %d DIFFERENT series are read (`series{}`); every one must "
              "give an episode (`skipped` not counted, none read = 0)" % sb.SERIES_SAMPLES),
             ("ingest_sample_ok", ">= %g" % sb.MIN_INGEST_SAMPLE_OK,
              "NEW series site with a `series_page:` that has `series_url_regex`: up to %d DIFFERENT series items of the list AND the collections (episode "
              "cards included) go through the production key / title cleanup and the series-page directory (`series-page.md`), up to %d of their pages are "
              "read; the share that resolves to a series page with a non-empty inventory. Never skippable"
              % (sb.INGEST_SAMPLE_ITEMS, sb.SERIES_READS))]
    out = ["| criterion | must be | meaning |", "|---|---|---|"]
    out += ["| `%s` | %s | %s |" % row for row in rows]
    out += ["", "`passed` is true only when every criterion is met."]
    return "\n".join(out)


def render_collection_roles() -> str:
    from app.routers import onboard_sandbox as sb
    from app.scraper import collections as col

    out = ["A collection id is `<role>_<site_id>` (`collections.list_id`), e.g. `trending_ornekfilm`; one collection per role, at most %d "
           "per site (role `category`: id `category_<slug>_<site_id>` + key `category: <slug>`, one per slug). The home screen merges the "
           "collections of the same role of EVERY site." % sb.MAX_COLLECTIONS,
           "", "| role | feeds | write it? |", "|---|---|---|"]
    for role, feeds in col.ROLES.items():
        if role in sb.ONBOARD_ROLES:
            use = "yes" if role in col.HOME_ROLES else "yes (not on the home screen)"
        else:
            use = "no"
        out.append("| `%s` | %s | %s |" % (role, _cell(re.sub(r"\s*\([^)]*\)", "", feeds)), use))
    return "\n".join(out)


def render_series_page_keys() -> str:
    from app.scraper import series_generic

    missing = sorted(series_generic.SPEC_KEYS - set(SERIES_KEY_DOCS))
    extra = sorted(set(SERIES_KEY_DOCS) - series_generic.SPEC_KEYS)
    if missing or extra:
        raise ValueError("SERIES_KEY_DOCS and series_generic.SPEC_KEYS differ (undocumented: %s; unknown: %s)"
                         % (", ".join(missing) or "-", ", ".join(extra) or "-"))
    out = ["| key | required | meaning |", "|---|---|---|"]
    out += ["| `%s` | %s | %s |" % (key, SERIES_KEY_DOCS[key][0], _cell(SERIES_KEY_DOCS[key][1])) for key in SERIES_KEY_DOCS]
    out += ["", "Any other key is an error (`series_page: unknown key ...`)."]
    return "\n".join(out)


def render_search_keys() -> str:
    from app.scraper import search_generic

    missing = sorted(search_generic.SPEC_KEYS - set(SEARCH_KEY_DOCS))
    extra = sorted(set(SEARCH_KEY_DOCS) - search_generic.SPEC_KEYS)
    if missing or extra:
        raise ValueError("SEARCH_KEY_DOCS and search_generic.SPEC_KEYS differ (undocumented: %s; unknown: %s)"
                         % (", ".join(missing) or "-", ", ".join(extra) or "-"))
    out = ["| key | required | meaning |", "|---|---|---|"]
    out += ["| `%s` | %s | %s |" % (key, SEARCH_KEY_DOCS[key][0], _cell(SEARCH_KEY_DOCS[key][1])) for key in SEARCH_KEY_DOCS]
    out += ["", "Any other key is an error (`search: unknown key ...`)."]
    return "\n".join(out)


def render_repair_layers() -> str:
    """The layers a repair is scoped to (``scraper/heal_agent.LAYERS``): evidence -> the top-level yaml keys it may change."""
    from app.scraper import heal_agent

    out = ["| layer | the evidence that points at it | top-level yaml keys you may change |", "|---|---|---|"]
    for name, (keys, doc) in heal_agent.LAYERS.items():
        out.append("| `%s` | %s | %s |" % (name, _cell(doc), ", ".join("`%s`" % k for k in keys)))
    out += ["", "`version`, `updated_at` and `site_id` are set by the server and never count as a change. The fields inside `list` / `detail` "
                "keep the no-field-dropping rule."]
    return "\n".join(out)


def render_repair_gates() -> str:
    """The checks the server runs on a ``submit_repair`` proposal (``scraper/heal_agent.py`` / ``heal.py`` constants)."""
    from app.routers import onboard_sandbox as sb
    from app.scraper import heal, heal_agent

    rows = [
        ("scope", "only the top-level keys of the diagnosed layer(s) change (table above); recipes only in the `provider` layer, related ones only, a new "
                  "one only for a new host (else `scope: touched ... outside layer ...`)"),
        ("identity", "site id, schema, the host of `base_url`, the `normalize` key rules (and whether a `normalize:` block exists), `image_hosts` are unchanged"),
        ("fields", "no field of `list.fields` / `detail.fields` dropped, `attr` / `cast` / `all` unchanged, a usable selector each"),
        ("validity", "yaml and recipes validate without an error the ACTIVE ones did not already have (unknown `providers:` names, bad `resolvers:`, invalid recipe)"),
        ("baseline", "the list parse still meets the baseline (`test_config(baseline: true)`: `baseline.ok`): `valid_count` >= `min_items`, fill >= `min_fill_ratio` "
                     "and the critical fields, not more than %g (aggregate) / %g (one field) below the last good parse"
                     % (heal.FILL_TOLERANCE, heal.FIELD_FILL_TOLERANCE)),
        ("failing examples", "the failing playback pages of the evidence (up to %d) play again: resolved / checked >= %g, at least one"
                             % (heal_agent.FIX_EXAMPLES, sb.MIN_PLAYABLE_RATIO)),
        ("working examples", "the working pages (up to %d) still play, else `regression: <site>`" % heal_agent.REGRESSION_SAMPLES),
        ("other sites", "every other site that uses a NEW or CHANGED recipe: up to %d sites x %d working examples still play, else `regression: <site>`"
                        % (heal_agent.REGRESSION_SITES, heal_agent.REGRESSION_SAMPLES)),
        ("no sources", "`problem: no_sources`: `test_config(playable: true)` meets `series_have_episode_sources` and `playable_ratio` (instead of the examples)"),
        ("series pages", "`problem: series_inventory`: `test_config(playable: true)` meets `series_inventory_ok` (instead of the examples)"),
    ]
    out = ["| gate | what must hold |", "|---|---|"]
    out += ["| %s | %s |" % (_cell(a), _cell(b)) for a, b in rows]
    out += ["", "Only a proposal that passes every gate is applied, and only while the admin setting `heal_autoapply` is on (otherwise it is "
                "kept for the admin). A site yaml change is a new VERSION (the old one is archived, `rollback_config` restores it), a recipe "
                "too (`<name>.vN.yaml`). A run is limited to %d s; a failed or unapplied repair starts the site's heal cooldown."
                % heal.AGENT_TIMEOUT_DEFAULT]
    return "\n".join(out)


def render_edit_tools() -> str:
    from app.scraper import pi_agent

    return "Edit-mode tools: " + ", ".join("`%s`" % name for name in pi_agent.EDIT_TOOLS) + " (+ `read`)."


def render_edit_layers() -> str:
    """The layers an edit request may touch: the repair layers (``heal_agent.LAYERS``) with the wording of a REQUEST, plus the extras."""
    from app.scraper import heal_agent

    missing = sorted(set(heal_agent.LAYERS) - set(EDIT_LAYER_DOCS))
    extra = sorted(set(EDIT_LAYER_DOCS) - set(heal_agent.LAYERS))
    clash = sorted(set(EDIT_EXTRA_LAYERS) & set(heal_agent.LAYERS))
    if missing or extra or clash:
        raise ValueError("EDIT_LAYER_DOCS and heal_agent.LAYERS differ (undocumented: %s; unknown: %s; extra layers that clash: %s)"
                         % (", ".join(missing) or "-", ", ".join(extra) or "-", ", ".join(clash) or "-"))
    out = ["| layer | the request is about | top-level yaml keys you may change |", "|---|---|---|"]
    for name, (keys, _doc) in heal_agent.LAYERS.items():
        out.append("| `%s` | %s | %s |" % (name, _cell(EDIT_LAYER_DOCS[name]), ", ".join("`%s`" % k for k in keys)))
    for name, (keys, doc) in EDIT_EXTRA_LAYERS.items():
        out.append("| `%s` | %s | %s |" % (name, _cell(doc), ", ".join("`%s`" % k for k in keys)))
    out += ["", "`version`, `updated_at` and `site_id` are set by the server and never count as a change. Identity keys (`schema`, `base_url`, "
                "`normalize.key`, `display_name`) only change when the request names them (rule 3)."]
    return "\n".join(out)


RENDERERS = {"resolvers-catalog": render_resolvers, "quality-criteria": render_quality,
             "collection-roles": render_collection_roles, "series-page-keys": render_series_page_keys,
             "search-keys": render_search_keys,
             "repair-layers": render_repair_layers, "repair-gates": render_repair_gates,
             "edit-tools": render_edit_tools, "edit-layers": render_edit_layers}


def _markers(name: str) -> tuple:
    return ("<!-- BEGIN GENERATED %s" % name, "<!-- END GENERATED %s -->" % name)


def replace_block(text: str, name: str, body: str) -> str:
    """``text`` with the generated block ``name`` replaced by ``body`` (ValueError when the markers are missing)."""
    begin, end = _markers(name)
    pattern = re.compile(re.escape(begin) + r"[^\n]*-->\n.*?" + re.escape(end), re.S)
    header = "%s (tools/gen_onboard_refs.py; do not edit by hand) -->" % begin
    if not pattern.search(text):
        raise ValueError("generated block %r not found (needs %r ... %r)" % (name, begin, end))
    return pattern.sub(lambda _m: "%s\n%s\n%s" % (header, body, end), text, count=1)


def render_file(rel: str) -> str:
    """The expected content of one references file."""
    with open(os.path.join(REFS_DIR, rel), "r", encoding="utf-8") as fh:
        text = fh.read()
    for name in TARGETS[rel]:
        text = replace_block(text, name, RENDERERS[name]())
    return text


def outdated() -> list:
    """References files whose generated blocks differ from what the code produces now."""
    stale = []
    for rel in TARGETS:
        with open(os.path.join(REFS_DIR, rel), "r", encoding="utf-8") as fh:
            if fh.read() != render_file(rel):
                stale.append(rel)
    return stale


def main(argv: list) -> int:
    if "--check" in argv:
        stale = outdated()
        for rel in stale:
            print("out of date: %s (run: venv/bin/python -m tools.gen_onboard_refs)" % rel)
        return 1 if stale else 0
    for rel in TARGETS:
        new = render_file(rel)
        path = os.path.join(REFS_DIR, rel)
        with open(path, "r", encoding="utf-8") as fh:
            changed = fh.read() != new
        if changed:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
        print("%s %s" % ("updated" if changed else "ok     ", rel))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
