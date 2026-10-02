"""Site-onboarding sandbox: the back end of the pi agent's tools (``/api/onboard/sandbox/*``).

The agent fetches pages, tries selectors, tests a DRAFT site yaml (list parse, schema, normalize, detail fields),
tries the resolver chain on a detail page and finally hands the draft in. Nothing here writes ``scraper/configs`` or a
baseline: pages and drafts live in ``DATA_DIR/onboard`` (``scraper/onboard_store.py``).

Access (every endpoint): the client must be localhost (``127.0.0.1`` / ``::1``) AND send an ``X-Onboard-Token`` header
that ``issue_token(draft_id)`` handed out (tokens live in memory only; ``revoke_token`` ends them). Every URL goes
through ``netguard.check_url`` (SSRF), before the fetch and, when the transport reports it, after redirects. Answers
are small on purpose (they go to an LLM): text is clipped, lists are capped.

``POST /fetch``          {url, mode: auto|http|browser|chrome, wait_for?, referer?}
                                                                      -> page_id, final_url, fetch_mode, status, bytes, title, html_excerpt
                                                                      (+ ``canonical_url`` and ``redirect_hint {kind, target, note}`` when the page is
                                                                      really ANOTHER path of the site: http / meta refresh / bare JS jump /
                                                                      the root's canonical or JSON-LD WebPage url; ``_page_signals``)
                                                                      (``chrome`` / a ``referer`` = Chrome TLS fingerprint + Referer through
                                                                      ``fetch.impersonated_get``: Cloudflare-safe player pages that 404 without it)
``POST /query``          {page_id, selector, attr?, limit}           -> count, items[{text, attr_value?, outer_html}]
``POST /grep``           {page_id, pattern, context=120, limit=10, flags?} -> count, matches[{offset, text}] (regex on the RAW page text)
``POST /outline``        {page_id}                                   -> title, repeating card candidates, iframe/link host counts,
                                                                      ``nav_links`` (menu/header/nav/footer links), ``sections`` (home
                                                                      sections: heading + the card group under it) and ``blocks`` (EVERY
                                                                      repeating card block, ``link_kind`` series | episode | film | mixed |
                                                                      other), + ``canonical_url`` / ``redirect_hint`` as ``/fetch``
``POST /test_config``    {yaml_text, page_id?, detail_page_id?, collections?, playable?} -> valid, errors, warnings, list, normalize,
                                                                      detail, passed, criteria (+ ``collections[]``: every yaml ``collections:``
                                                                      entry checked and fetched, when ``collections`` is true; + ``playable``
                                                                      {checked, resolved, samples[]}: up to 3 normalized items followed from
                                                                      their playback page to a stream, with the ``playable_ratio`` and
                                                                      ``series_have_episode_sources`` criteria, when ``playable`` is true;
                                                                      + ``search`` {query, count, samples[], found_known, ...} and the
                                                                      criterion ``search_ok``, when ``playable`` is true AND the yaml has a
                                                                      ``search:`` block; no block = a warning, no criterion)
                                                                      Report fields added for the agent (``test_config`` and ``submit``):
                                                                      * ``diagnostics`` = ``{list, detail, series, collections, search, player}``,
                                                                        each a (possibly empty) list of ``{where, problem, ...}`` entries: how
                                                                        many rows a selector matched, what each field extracted from the FIRST
                                                                        rows (``first_rows[]``, ``extracted``), why a row / field was rejected
                                                                        (``rejected_by``: e.g. fields.url took the row's first <a>, an add-to-
                                                                        favourites link, which episode_url_regex does not fit) and what else sits
                                                                        there (``alternatives``: og: meta tags, lazy images, the HTML around a
                                                                        field's label, repeating cards). <= 1 KB per entry, <= 10 KB in all.
                                                                      * ``series.samples[].diagnostics`` = ``{rows_matched, rows_accepted,
                                                                        anchors_matched, first_rows[{raw, rejected_by?}], episode_links_sit_in?}``
                                                                        (the generic series engine) and ``series.samples[].blocked`` (bool,
                                                                        + ``reason``): a series that production would not take.
                                                                      * ``failing`` (only when ``passed`` is false) = ``[{criterion, value,
                                                                        bound, hint}]``: every failed criterion with one sentence on why / what
                                                                        to try.
                                                                      * NEW-site hardening (``_hardening``; not repair / edit mode;
                                                                        ``ONBOARD_HARDEN=0`` switches it off): criteria ``availability_gate_defined``,
                                                                        ``series_signal_collection``, ``series_full_inventory``,
                                                                        ``home_path_is_canonical``, ``collection_poster_fill``,
                                                                        ``detail_info_defined`` where they apply; ``exempt`` = what the admin's "Sitede
                                                                        yok, atla" answers (draft ``skipped_fields``) covered, ``redirect_hint``,
                                                                        ``detail_info {good, missing, required}``, ``removed_fields`` (a field that
                                                                        gave values in the draft's previous submission is gone).
                                                                      * ``blocked`` = ``{count, rules, samples[{url, reason}]}`` (``playable``
                                                                        runs): content that is not public (a ``blocked:`` rule matched or the
                                                                        ``availability_gate`` found no player): series / pages production would
                                                                        not write; ``rules`` = the draft's rules (``on``, conditions, ``reason``).
                                                                        ``playable.samples[].blocked`` / ``reason`` and ``playable.blocked`` N: a
                                                                        blocked sample is left OUT of ``playable_ratio`` (not a broken player) and
                                                                        replaced by another series (<= 2 spares); the samples are one episode of
                                                                        up to 3 DIFFERENT series.
                                                                      * ``gate`` = ``{probed, passed, skipped, retry, samples[{url, reason}]}``
                                                                        (only with an ``availability_gate:``): "the gate removed ``skipped`` of
                                                                        ``probed`` series / films, ``passed`` remain"; ``playable_ratio`` is judged
                                                                        over the ones that pass. Without a rule, a page that does not resolve
                                                                        while its iframe looks like a block placeholder (telif / copyright /
                                                                        blocked / unavailable / restricted) gets a warning advising a ``blocked:``
                                                                        rule (``references/blocked.md``).
``POST /test_search``    {yaml_text, query?, page_id?, detail_page_id?}
                                                                      -> valid, errors, warnings, query, count, samples[{title, detail_url,
                                                                      poster_url, year}] (<= 5), found_known (the list item the query was
                                                                      taken from is among the results: its detail_url or normalize key),
                                                                      normalize_ok_ratio, ms: the draft's yaml ``search:`` block run on ONE
                                                                      live query in memory (``scraper/search_generic.py``; nothing is written)
``POST /discover_site``  {url}                                       -> yaml_text, found, missing[{field, tried, ...}], confidence, pages_fetched, pages[{role, page_id,
                                                                      ...}], notes, errors: the DRAFT site yaml built by code from <= 8 pages of the
                                                                      site (``scraper/discover.py``: home sections + roles, list, series page,
                                                                      detail fields, episode page, player candidates, normalize); it judges nothing:
                                                                      what it is unsure of is in ``missing``; the pages are stored (``page_id``)
``POST /match_providers`` {player_url, referer?, detail_url?}         -> matches[{provider, kind, host_match, ok, stream_type, stream_host, ms}],
                                                                      page, recommendation {action: use_provider | add_host | new_recipe |
                                                                      needs_code, recipe_yaml?, host_regex?}: every provider of the library dry-run on
                                                                      one player page, host match ignored (``scraper/provider_match.py``; nothing is written)
``GET  /resolvers``                                                  -> resolver type + provider catalogs (code modules AND the data-driven
                                                                      provider recipes of ``configs/providers/``: ``kind`` code | recipe)
``POST /test_provider``  {recipe_yaml, sample_url, referer?}         -> valid, errors, matched, status (resolved | no_stream | no_match |
                                                                      invalid), streams[{type, host, quality}], trace: a provider RECIPE
                                                                      validated and run on one player URL (nothing is written)
``POST /test_resolvers`` {yaml_text, detail_url, page_id?, detail_urls?, list_page_id?}
                                                                      -> status, candidates, resolved (a resolver ``stream`` shortcut
                                                                      counts as resolved; ``fetch: browser`` items run within the call limit)
                                                                      for ``detail_url``, plus ``pages[]``: every page tried (``detail_urls``,
                                                                      else up to 2 more sampled from the list page of the draft yaml), one
                                                                      after the other; ``status`` resolved (all) / partial (some)
``POST /submit``         {draft_id, yaml_text, site_id_suggestion, notes, handoff?, page_id?, detail_page_id?, provider_recipes?}
                                                                      -> test_config again (always with collections AND playable), written
                                                                      to the draft, status=ready
``provider_recipes`` = ``[{name, yaml, mode?}]`` (at most 3): new provider recipes for the library; ``mode: "update"`` = a NEW VERSION of a recipe
that is in the library, allowed to widen ``match.host_regex`` only (``recipes.update_problems``; the yaml comes from ``match_providers``). ``test_config`` / ``test_resolvers`` /
``submit`` take them (the draft's stored ones are always included) and add them IN MEMORY to the providers the site yaml may name in
``providers:``; nothing is written to ``configs/providers`` here (only ``onboard.save`` does).

Edit mode (an admin changes a REGISTERED site, ``onboard.start(mode="edit")``; the draft has ``mode: "edit"`` + ``edit_site_id``): the
same tools, plus ``GET /site_config/{site_id}`` for THAT site only. ``submit`` locks ``site_id_suggestion`` to the edited site (whatever
the agent sends), ``test_config`` / ``submit`` check the collection ids against it (``site_hint``) and look the site's code modules up by
its real id (``module_site``), so a hand-built site's own extractor is found.

Repair mode (the heal agent, ``scraper/heal_agent.py``; the token belongs to a repair job ``rp_...``, not a draft):
``GET  /site_config/{site_id}?recipe=``                              -> the ACTIVE yaml text + version + baseline of a registered site and
                                                                      the approved provider recipes (yaml of the ones the site names, and of ``recipe``)
                                                                      + ``handoff``: the site's handoff note (``scraper/site_handoff.py``)
``POST /test_config``    {..., baseline: true}                        -> + ``baseline {ok, reasons}`` and the criterion ``baseline_ok``: the yaml against the
                                                                      active config / baseline of its ``site_id`` (no field dropped, fill not clearly lower);
                                                                      in a repair run a ``provider_recipes`` name that is in the library UPDATES that recipe
``POST /submit_repair``  {site_id, yaml_text?, provider_recipes?, notes, handoff?}
                                                                      -> records the PROPOSAL in ``DATA_DIR/onboard/repairs/<job_id>.json``; applies nothing
                                                                      (``scfg`` / ``configs`` are never written here)
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import functools
import hmac
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Annotated, Any, Literal, NamedTuple, Optional
from urllib.parse import urljoin, urlsplit

import yaml
from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from selectolax.parser import HTMLParser

from .. import config, netguard
from ..errors import ApiError
from ..scraper import collections as site_collections
from ..scraper import blocked as sblocked, fetch, heal, onboard_store, parse, resolvers, schema, series_generic, site_handoff
from ..scraper import config as scfg
from ..scraper.providers import recipes, registry, trace

log = logging.getLogger("onboard.sandbox")
router = APIRouter(prefix="/api/onboard/sandbox")

# --- acceptance criteria of a draft (``passed`` in test_config) ---
MIN_VALID_COUNT = 8                                           # list items that pass the schema
MIN_FILL = {"title": 0.95, "detail_url": 0.95, "poster_url": 0.8}   # share of list rows with the field filled
MIN_NORMALIZE_OK_RATIO = 0.9                                  # normalized / valid items
MAX_DUPLICATE_KEY_RATIO = 0.1                                 # duplicated normalize keys / normalized items
MIN_COLLECTION_COUNT = 3                                      # items a collection (home section) must yield (``collections: true``)
MAX_COLLECTIONS = 8                                           # yaml ``collections:`` entries accepted
MAX_PROVIDER_RECIPES = 3                                      # provider recipes one draft may carry
DISCOVER_SECONDS = 60.0                                       # discover_site: longest one call (page requests stop NEED_SECONDS before it)
# playability (``playable: true`` / always in submit): a few normalized items are followed from their playback page to a stream
MIN_PLAYABLE_RATIO = 0.67                                     # resolved / checked samples (``skipped`` excluded; at least 1 resolved)
MIN_SERIES_SOURCE_RATIO = 0.9                                 # series items that carry episode ``video_sources`` / series items
PLAYABLE_SAMPLES = 3                                          # distinct normalized items checked end to end
PLAYABLE_SPARES = 2                                           # more picks followed when a sample turned out to be blocked content (<= 2 spares)
PLAYABLE_SAMPLE_SECONDS = 25.0                                # longest one sample may take (page fetch + candidates)
PLAYABLE_MIN_LEFT = 8.0                                       # seconds of the call that must be left to start another sample
SERIES_SOURCES_ERROR = ("series items carry no episode sources: configure normalize.episode_source (cards that are episode "
                        "pages) or series_page (cards that link to series pages: references/series-page.md); a site that "
                        "needs more than that: say so in notes")
# series inventory (``playable: true``): the series pages of a few normalized series items are read with the yaml ``series_page:``
SERIES_SAMPLES = 3                                            # distinct series pages read (one playable sample each: ``_inventory_picks``)
SERIES_SPARES = 2                                             # more series pages read when blocked / gate-failing ones left too few (<= 2 spares)
SERIES_SAMPLE_SECONDS = 25.0                                  # longest one series page may take (fetch + parse)
MIN_SERIES_INVENTORY_RATIO = 1.0                              # checked series pages that must give at least one episode
SERIES_HINT = "cards link to series pages: add series_page (see references/series-page.md)"
# the ingest sample (``harden``, new site): up to INGEST_SAMPLE_ITEMS distinct series items of the list + the collections go through the
# production identity / series-page resolution (key + title cleanup, ``library/series_dir.py``) in memory and up to SERIES_READS of their
# series pages are read; ``ingest_sample_ok`` = the share that resolves to a series page with a non-empty episode inventory
SERIES_READS = 5                                              # series pages read when the ingest sample runs (SERIES_SAMPLES otherwise)
INGEST_SAMPLE_ITEMS = 10                                      # distinct series items judged by the ingest sample
MIN_INGEST_SAMPLE_OK = 0.8                                    # ``ingest_sample_ok`` bound
# live search (``search:`` block, Faz 6): ONE query is run with the draft's spec (``scraper/search_generic.py``) in ``test_search`` and in
# the search stage of ``test_config(playable: true)`` / ``submit`` (always); criterion ``search_ok`` exists only when the yaml has ``search:``
SEARCH_LIMIT = 20             # results asked for (the engine caps at 20)
SEARCH_NEED = 14.0            # seconds of the call that must be left to start the search stage (engine: 10 s per request), else ``skipped``
SEARCH_SAMPLES = 5            # results listed in the answer
SEARCH_QUERY_MAX = 100        # characters of a query (the engine's own cap)
NO_SEARCH_WARNING = ("no search block: this site will not be searchable (add `search:` when the site has a search form; "
                     "see notes `search-hint`)")
MAX_EPISODE_LINKS = 5        # outline: episode-link groups listed
EPISODE_LINK_MIN = 3          # outline: links of one shape that make a group
# roles onboarding writes: sections of the home page / the site's "trending" and "newest" pages. ``new`` / ``catalog`` /
# ``genre`` (whole catalogues) stay valid roles of ``scraper/collections`` but a draft gets a warning for them
ONBOARD_ROLES = ("trending", "latest_episodes", "latest_series", "latest_movies", "noteworthy_movies", "featured", "upcoming")
COLLECTION_KEYS = frozenset({"id", "title", "path", "role", "row_selector", "fields", "required_fields", "excluded_fields",
                             "sort_by", "sort_desc", "genre"})
NO_COLLECTIONS_WARNING = ("no collections: ana ekran satırları (trendler/yeni/dikkate değer) bu siteden dolmayacak; "
                          "ana sayfada ilgili bölümler varsa ekle")
# Hardening criteria of a NEW site's onboarding (``_analyze(harden=True)``: ``test_config`` / ``submit`` / ``onboard.save`` of a new
# site, never repair or edit mode, so registered / hand-built sites are untouched). The rules live here, not only in the skill text:
# an agent that took the easy way (list_url "/", one collection, no series_page, no availability_gate) gets ``passed: false`` and the
# automatic correction round (``onboard._begin_auto_round``) sends it back with ``failing[].hint``.
SERIES_SIGNAL_ROLES = ("trending", "latest_series")           # collection roles that carry SERIES: ``series_signal_collection`` needs one
SKIP_HOME_SERIES = "home_series_section"                      # draft ``skipped_fields`` entry (admin: "Sitede yok, atla") exempting series_signal_collection
SKIP_SERIES_INVENTORY = "series_inventory"                    # same, exempting series_full_inventory
SKIP_COLLECTION_POSTER = "collection_poster"                  # same, exempting collection_poster_fill
HARDEN_CRITERIA = ("availability_gate_defined", "series_signal_collection", "series_full_inventory", "home_path_is_canonical",
                   "collection_poster_fill", "detail_info_defined", "ingest_sample_ok")
POSTER_ROLES = ("trending", "latest_series", "latest_movies", "noteworthy_movies", "featured")   # collections whose cards need a poster
MIN_COLLECTION_POSTER_FILL = 0.8                              # ``poster_url`` fill of every such collection (``collection_poster_fill``)
#: the information a detail page gives a title (``detail_info_defined``): group -> the detail field names that count for it (and that an
#: admin's "Sitede yok, atla: <name>" may name). A group is GOOD when one of its fields is defined AND filled in the sample pages.
INFO_FIELDS = {"synopsis": ("synopsis", "overview", "description", "summary", "plot"), "year": ("year", "release_year"),
               "cast": ("cast", "actors"), "genres": ("genres", "genre"), "rating": ("rating", "imdb", "score"),
               "trailer_url": ("trailer_url", "trailer"), "poster_url": ("poster_url", "poster")}
MIN_DETAIL_INFO = 3           # good info groups a detail page must give (fewer when the admin said the site lacks some)
DETAIL_SAMPLES = 2            # detail pages parsed for ``detail_info_defined`` (the stored one + one more list item); a field must fill in all of them
DETAIL_SAMPLE_NEED = 8.0      # seconds of the call that must be left to fetch the second detail page
MAX_REMOVED = 6               # ``removed_fields`` entries kept
HINT_CLIP = 700               # characters of one ``failing[].hint`` (the hardening hints are several sentences long)
MAX_BLOCKS = 16               # outline: repeating home blocks listed (``blocks``)
BLOCK_MIN_CARDS = 3           # outline: cards of one repeating group that make a block

# --- answer sizes ---
TEXT_CLIP = 200
OUTER_CLIP = 600
FIELD_CLIP = 150
LIST_CAP = 10
EXCERPT_CHARS = 15000
MAX_QUERY_ITEMS = 50
MAX_CANDIDATES = 8            # resolver candidates tried by test_resolvers
MAX_NAV_LINKS = 60            # outline: menu/header/nav/footer links listed
MAX_SECTIONS = 12             # outline: home sections (heading + card group) listed
COLLECTION_NEEDS = {"http": 6.0, "browser": 15.0}   # seconds that must be left before a collection page is fetched (else: skipped)
THIN_CHARS = 1500             # auto mode: less visible markup than this after an http fetch -> try the browser
CHROME_MAX_BYTES = 3_000_000  # chrome mode: body cap (same as player_page.MAX_BODY)
CHROME_TIMEOUT = 20.0         # chrome mode: total seconds of one fetch (all redirect hops), also bounded by the call limit
GREP_MAX_PATTERN = 200        # characters of a grep_page regex
GREP_MAX_BYTES = 3_000_000    # a stored page is searched up to this size
GREP_MAX_CONTEXT = 500
GREP_MAX_LIMIT = 30
GREP_MATCH_CLIP = 500         # a match longer than this is cut (the answer goes to an LLM)
GREP_COUNT_CAP = 2000         # counting stops here (``count_capped``)
GREP_TIMEOUT = 5.0            # seconds the regex may run; the child process is killed after that (backtracking)
RESOLVE_MARGIN = 6.0          # test_resolvers: stop waiting for candidates this long before the call limit (answer in time)
TRIAL_AUTO_PAGES = 3          # test_resolvers: pages tried when no detail_urls are given (detail_url + items sampled from the list)
TRIAL_EXPLICIT_PAGES = 5      # test_resolvers: detail_url + at most 4 detail_urls
TRIAL_PAGE_SLACK = 15.0       # test_resolvers: seconds on top of the candidate limit that must be left to start another page
TRIAL_STREAMS = 8             # test_resolvers: streams listed per page
TRIAL_TYPE_STAGE = "player_page.type: "   # trace stage prefix of a player_page warning (a URL that contradicts its declared type)
LOCAL_HOSTS = frozenset({"127.0.0.1", "::1"})
DRAFT_SITE_ID = "onboard_draft"   # site id of the in-memory draft config (never a real site)
# the id the SITE CODE MODULES (``site_extractors/<id>.py``: discover / resolve_candidate / series_inventory) are looked up by: the draft
# id, or in an edit run the real id of the edited site (``module_site``; a ContextVar because the helpers are called deep inside the stages)
_MODULE_SITE: contextvars.ContextVar = contextvars.ContextVar("onboard_module_site", default=DRAFT_SITE_ID)
SITE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_ESCALATE = ("challenge_blocked", "http_401", "http_403", "http_429", "http_503", "empty response", "timeout")
_SKIP_TAGS = frozenset({"html", "head", "body", "script", "style", "svg", "path", "br", "meta", "link", "noscript", "use"})
_SAFE_CLASS = re.compile(r"^[A-Za-z0-9_-]+$")
_CASTS = (None, "int", "float", "date_tr")

_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="onboard")
_tokens: dict[str, str] = {}   # token -> draft id (memory only)
_tokens_lock = threading.Lock()


# --- tokens / access --------------------------------------------------------------------------------------------

def issue_token(draft_id: str) -> str:
    """A fresh secret that lets the sandbox calls of ``draft_id`` through."""
    token = secrets.token_urlsafe(24)
    with _tokens_lock:
        _tokens[token] = draft_id
    return token


def revoke_token(token: str) -> None:
    with _tokens_lock:
        _tokens.pop(token, None)


def _draft_of(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    with _tokens_lock:
        items = list(_tokens.items())
    found = None
    for known, draft_id in items:   # compare every token (no early exit on the secret)
        if hmac.compare_digest(known.encode(), token.encode()):
            found = draft_id
    return found


def _guard(request: Request, x_onboard_token: Optional[str] = Header(None)) -> str:
    host = request.client.host if request.client else ""
    if host not in LOCAL_HOSTS:
        raise ApiError(403, "forbidden", "onboarding sandbox is reachable from localhost only")
    draft_id = _draft_of(x_onboard_token)
    if draft_id is None:
        raise ApiError(403, "forbidden", "missing or invalid X-Onboard-Token")
    return draft_id


async def _bounded(fn, *args, hint: str = ""):
    """Run ``fn(*args, deadline=...)`` in the sandbox pool; 504 when it takes longer than ONBOARD_TOOL_TIMEOUT. The
    worker cannot be killed: helpers check ``deadline`` before each network step. ``hint`` is added to the 504 text."""
    limit = config.ONBOARD_TOOL_TIMEOUT
    deadline = time.monotonic() + limit
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(loop.run_in_executor(_pool, functools.partial(fn, *args, deadline=deadline)), limit)
    except asyncio.TimeoutError:
        raise ApiError(504, "timeout", f"the call took longer than {limit:g}s and was abandoned; try a smaller step"
                                       + (f" ({hint})" if hint else ""))


def _check_deadline(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise ApiError(504, "timeout", "the call exceeded its time limit")


# --- small helpers ----------------------------------------------------------------------------------------------

def _clip(value: Any, limit: int = FIELD_CLIP) -> Any:
    """Text shortened, lists capped, dicts clipped value by value (answers go to an LLM)."""
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit - 1] + "…"
    if isinstance(value, (list, tuple)):
        return [_clip(v, limit) for v in value[:LIST_CAP]]
    if isinstance(value, dict):
        return {str(k): _clip(v, limit) for k, v in list(value.items())[:30]}
    return value


def _filled(value: Any) -> bool:
    return schema._filled(value)


def _compact(item: dict) -> dict:
    """An item with its empty fields dropped and the rest clipped."""
    return {k: _clip(v) for k, v in item.items() if _filled(v)}


def _text(node) -> str:
    return re.sub(r"\s+", " ", node.text(strip=True) or "").strip()


def _module_site() -> str:
    return _MODULE_SITE.get()


@contextlib.contextmanager
def module_site(site_id: str):
    """Within the block the site code modules are looked up by ``site_id`` (an edit run: the registered site being edited)."""
    token = _MODULE_SITE.set(site_id or DRAFT_SITE_ID)
    try:
        yield
    finally:
        _MODULE_SITE.reset(token)


def _scoped(fn, site_id: str):
    """``fn`` run inside ``module_site(site_id)`` (the pool threads do not inherit the context); ``fn`` itself without a site."""
    if not site_id:
        return fn

    def run(*args, **kwargs):
        with module_site(site_id):
            return fn(*args, **kwargs)
    return run


def _edit_site(job_id: Optional[str]) -> str:
    """The registered site an EDIT draft (``od_...`` with ``mode: "edit"``) belongs to, else ``""`` (a new-site draft, a repair job)."""
    if not onboard_store.valid_draft_id(job_id):
        return ""
    draft = onboard_store.get_draft(job_id) or {}
    site = draft.get("edit_site_id")
    return site if draft.get("mode") == "edit" and isinstance(site, str) and SITE_ID_RE.match(site) else ""


def _check(url: str) -> str:
    try:
        return netguard.check_url(url)
    except ValueError as exc:
        raise ApiError(400, "url_rejected", str(exc))


def _draft_cfg(data: dict, extra_providers: Optional[list] = None) -> scfg.SiteConfig:
    """The in-memory config of a draft; ``extra_providers`` = its provider recipes (not on disk yet), added to the registry."""
    cfg = scfg.SiteConfig(site_id=DRAFT_SITE_ID, data=data, path="")
    cfg.extra_providers = list(extra_providers or [])
    return cfg


def _load_page(page_id: Optional[str]) -> tuple[str, dict]:
    found = onboard_store.load_page(page_id or "")
    if found is None:
        raise ApiError(404, "not_found", f"page {page_id!r} not found (fetch it first; pages expire)")
    return found


class _NoAliasLoader(yaml.SafeLoader):
    """safe_load without aliases (a few kB of nested aliases can expand to gigabytes)."""

    def compose_node(self, parent, index):
        if self.check_event(yaml.events.AliasEvent):
            raise yaml.YAMLError("yaml aliases (*name) are not allowed")
        return super().compose_node(parent, index)


def _load_yaml(text: str) -> tuple[Optional[dict], Optional[str]]:
    try:
        data = yaml.load(text, Loader=_NoAliasLoader)
    except yaml.YAMLError as exc:
        return None, "yaml: " + " ".join(str(exc).split())[:300]
    if not isinstance(data, dict):
        return None, "yaml: the document must be a mapping (site config)"
    return data, None


# --- actionable diagnostics (``report.diagnostics``) -------------------------------------------------------------

DIAG_STAGES = ("list", "detail", "series", "collections", "search", "player")
DIAG_ENTRY_BYTES = 1000       # one entry of ``report.diagnostics`` / one series sample's ``diagnostics`` (JSON bytes)
DIAG_STAGE_ENTRIES = 6        # entries kept per stage
DIAG_TOTAL_BYTES = 10_000     # the whole ``report.diagnostics``
DIAG_ROWS = 2                 # rows whose extracted values are shown
_IMG_ATTRS = ("src", "data-src", "data-lazy-src", "data-original", "data-lazy", "srcset", "data-srcset", "poster", "alt")
_LABELS = {"year": "Yapım Yılı|Yıl|Year", "director": "Yönetmen|Director", "cast": "Oyuncular|Oyuncu|Cast", "runtime": "Süre|Duration|Runtime",
           "country": "Ülke|Country", "genres": "Tür|Genre", "genre": "Tür|Genre", "overview": "Özet|Konu|Synopsis", "synopsis": "Özet|Konu|Synopsis",
           "rating": "IMDb|Puan|Rating", "original_title": "Orijinal|Original"}
_DIAG: contextvars.ContextVar = contextvars.ContextVar("onboard_diag", default=None)


def _shrink(value: Any, width: int) -> Any:
    """``value`` with every string cut to ``width`` characters, lists to 4 items, dicts to 12 keys (recursively)."""
    if isinstance(value, str):
        value = " ".join(value.split())
        return value if len(value) <= width else value[:max(1, width - 1)] + "…"
    if isinstance(value, (list, tuple)):
        return [_shrink(v, width) for v in value[:4]]
    if isinstance(value, dict):
        return {str(k): _shrink(v, width) for k, v in list(value.items())[:12]}
    return value


REJECT_KEEP = 520             # characters of the FIRST ``rejected_by`` (it names the whole regex: shortened last, never to the width of the rest)


def _cap_entry(entry: Any, limit: int = DIAG_ENTRY_BYTES) -> Any:
    """``entry`` shrunk until its JSON fits ``limit`` bytes (no secret / cookie / long html can get through). The first row's
    ``rejected_by`` (why the episode regex did not match: the WHOLE regex + the path) is kept up to ``REJECT_KEEP`` characters while the rest
    of the entry shrinks around it: the explanation is shortened, never the regex."""
    keep = None
    rows = entry.get("first_rows") if isinstance(entry, dict) else None
    if isinstance(rows, list) and rows and isinstance(rows[0], dict) and isinstance(rows[0].get("rejected_by"), str):
        keep = " ".join(rows[0]["rejected_by"].split())[:REJECT_KEEP]
        entry = {**entry, "first_rows": [{**rows[0], "rejected_by": ""}, *rows[1:]]}

    def put(shrunk: Any) -> Any:
        if keep is not None and isinstance(shrunk, dict) and isinstance(shrunk.get("first_rows"), list) and shrunk["first_rows"]:
            shrunk["first_rows"][0]["rejected_by"] = keep
        return shrunk

    for width in (160, 100, 60, 30, 15):
        shrunk = put(_shrink(entry, width))
        if len(json.dumps(shrunk, ensure_ascii=False)) <= limit:
            return shrunk
    return put(_shrink(entry, 8))


def _diag_start() -> tuple[dict, Any]:
    box: dict[str, Any] = {"stages": {name: [] for name in DIAG_STAGES}, "bytes": 0}
    return box, _DIAG.set(box)


def _diag(stage: str, entry: dict) -> None:
    """Add one diagnostic entry (``{where, problem, ...}``) to ``report.diagnostics[stage]`` of the running analysis; a no-op outside
    one. Bounded: <= DIAG_STAGE_ENTRIES per stage, DIAG_ENTRY_BYTES per entry, DIAG_TOTAL_BYTES in all."""
    box = _DIAG.get()
    if box is None or stage not in box["stages"]:
        return
    items = box["stages"][stage]
    if len(items) >= DIAG_STAGE_ENTRIES:
        return
    capped = _cap_entry(entry)
    size = len(json.dumps(capped, ensure_ascii=False))
    if box["bytes"] + size > DIAG_TOTAL_BYTES:
        return
    box["bytes"] += size
    items.append(capped)


def _handle(node) -> str:
    """``tag.class1.class2`` of a node: a short selector-like handle."""
    if node is None:
        return ""
    classes = [c for c in (node.attributes.get("class") or "").split() if _SAFE_CLASS.match(c)][:2]
    return node.tag + "".join("." + c for c in classes)


def _row_hints(row, limit: int = 8) -> list[str]:
    """What a row contains: ``tag.class 'own text'`` (+ the link / image address) of its first descendant elements."""
    out: list[str] = []
    for node in row.css("*")[:80]:
        if node.tag in _SKIP_TAGS or node.tag == "-text":
            continue
        own = " ".join((node.text(deep=False, strip=True) or "").split())[:30]
        extra = ""
        for attr in ("href", "src", "data-src"):
            value = node.attributes.get(attr)
            if value:
                extra = f" {attr}={_clip(value, 50)}"
                break
        if not own and not extra:
            continue
        hint = _handle(node) + extra + (f" '{own}'" if own else "")
        if hint not in out:
            out.append(hint)
        if len(out) >= limit:
            break
    return out


def _field_state(node, spec: Any) -> tuple[bool, Optional[str]]:
    """``(the selector matched an element, the raw text / attribute it read)`` of a field spec on ``node`` (first fallback alternative)."""
    if not isinstance(spec, dict):
        return False, None
    if "fallback" in spec and isinstance(spec["fallback"], list) and spec["fallback"]:
        for alternative in spec["fallback"]:
            matched, raw = _field_state(node, alternative)
            if raw not in (None, ""):
                return matched, raw
        return _field_state(node, spec["fallback"][0])
    if spec.get("self") is True:
        found = node
    else:
        try:
            found = node.css_first(str(spec.get("selector") or ""))
        except Exception:
            found = None
    if found is None:
        return False, None
    attr = spec.get("attr")
    return True, (found.attributes.get(attr) if attr else found.text(strip=True))


def _field_hints(node, spec: dict) -> list[str]:
    """Candidates that sit where an empty field's selector found nothing: the images / links / elements of the row (or page)."""
    attr = str(spec.get("attr") or "")
    selector = str(spec.get("selector") or "")
    if attr in _IMG_ATTRS or "img" in selector:
        imgs = node.css("img")[:3]
        if not imgs:
            return ["bu satırda <img> yok"]
        return [_handle(img) + " " + " ".join(f"{k}={_clip(v, 60)}" for k, v in img.attributes.items() if k in _IMG_ATTRS and v)
                for img in imgs]
    if attr == "href":
        return [_clip(a.attributes.get("href") or "", 80) for a in node.css("a[href]")[:3]] or ["bu satırda a[href] yok"]
    return _row_hints(node, 6)


def _rows_diagnostics(html: str, row_selector: str, fields: dict, where: str, fill: Optional[dict] = None, valid: Optional[int] = None,
                      stage: str = "list") -> None:
    """Why a list / collection page gave few or poor rows: how many rows the selector matched, what each field extracted from the
    FIRST rows, and (for an empty field) what else sits in those rows. Entries go to ``report.diagnostics[stage]``."""
    try:
        tree = HTMLParser(html)
        rows = tree.css(row_selector)
    except Exception:
        return
    if not rows:
        groups: dict[str, int] = {}
        for node in tree.css("div, li, article, tr, a"):
            handle = _handle(node)
            if "." in handle:
                groups[handle] = groups.get(handle, 0) + 1
        repeating = [f"{h} ×{n}" for h, n in sorted(groups.items(), key=lambda kv: -kv[1]) if n >= 3][:4]
        _diag(stage, {"where": f"{where}.row_selector", "problem": f"selector {row_selector!r} 0 eleman eşledi ({len(html)} bayt sayfa)",
                      "alternatives": repeating or ["sayfada tekrar eden kart öğesi bulunamadı (sayfa JS ile mi kuruluyor? fetch_mode: browser dene)"]})
        return
    sample = rows[:DIAG_ROWS]
    extracted = {name: [_clip(parse.apply_field(row, spec), 80) if isinstance(spec, dict) else None for row in sample]
                 for name, spec in (fields or {}).items()}
    _diag(stage, {"where": where, "rows_matched": len(rows), "rows_accepted": valid, "first_rows": [
        {name: values[i] for name, values in extracted.items() if i < len(values)} for i in range(len(sample))]})
    for name, spec in (fields or {}).items():
        values = extracted[name]
        low = fill is not None and name in fill and fill[name] < MIN_FILL.get(name, 0.0)
        empty = all(v in (None, "", []) for v in values)
        if not (empty or low) or not isinstance(spec, dict):
            continue
        matched = [_field_state(row, spec) for row in sample]
        if not any(m for m, _raw in matched):
            problem = f"selector {spec.get('selector')!r} ilk {len(sample)} satırda 0 eleman eşledi"
        else:
            raw = next((r for m, r in matched if m), None)
            if spec.get("attr") and raw in (None, ""):
                problem = f"selector eşledi ama {spec['attr']!r} özniteliği yok / boş"
            elif spec.get("regex") and raw not in (None, ""):
                problem = f"selector eşledi (ham metin {_clip(raw, 60)!r}) ama regex {_clip(spec['regex'], 60)!r} uymadı"
            else:
                problem = "selector eşledi ama değer boş"
        if low and not empty:
            problem = f"alan satırların yalnız %{round(fill[name] * 100)}'inde dolu (en az %{round(MIN_FILL[name] * 100)}): " + problem
        _diag(stage, {"where": f"{where}.fields.{name}", "problem": problem, "extracted": values,
                      "alternatives": _field_hints(sample[0], spec)})


def _label_snippet(html: str, name: str, spec: Any) -> str:
    """A short piece of HTML around the label that belongs to a detail field (``Yapım Yılı``...), '' when the page does not show it."""
    words = _LABELS.get(name, "")
    regex = str((spec or {}).get("regex") or "") if isinstance(spec, dict) else ""
    literal = "|".join(sorted({w for w in re.findall(r"[^\W\d_]{4,}", regex)} - {"digit", "word"}, key=len, reverse=True)[:3])
    pattern = "|".join(x for x in (words, literal) if x)
    if not pattern:
        return ""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    try:
        match = re.search(pattern, text, re.I)
    except re.error:
        return ""
    if match is None:
        return ""
    return " ".join(text[max(0, match.start() - 30):match.end() + 130].split())[:170]


def _detail_diagnostics(html: str, fields: dict, parsed: dict, where: str = "detail") -> None:
    """Empty detail fields: whether the selector found an element at all, and what the page offers instead (og: meta tags, lazy
    images, the HTML around the field's label)."""
    try:
        tree = HTMLParser(html)
    except Exception:
        return
    root = tree.root
    for name, spec in (fields or {}).items():
        if _filled(parsed.get(name)) or not isinstance(spec, dict):
            continue
        matched, raw = _field_state(root, spec)
        if not matched:
            problem = f"selector {spec.get('selector')!r} 0 eleman eşledi"
        elif spec.get("attr") and raw in (None, ""):
            problem = f"selector eşledi ama {spec['attr']!r} özniteliği yok / boş"
        elif spec.get("regex") and raw not in (None, ""):
            problem = f"selector eşledi (ham metin {_clip(raw, 60)!r}) ama regex uymadı"
        else:
            problem = "selector eşledi ama değer boş"
        alternatives: list[str] = []
        selector = str(spec.get("selector") or "")
        metas = [f"{m.attributes.get('property') or m.attributes.get('name')}={_clip(m.attributes.get('content') or '', 40)}"
                 for m in tree.css("meta[property], meta[name]")
                 if str(m.attributes.get("property") or m.attributes.get("name") or "").startswith(("og:", "twitter:"))][:6]
        if "meta" in selector or spec.get("attr") == "content":
            alternatives += metas or ["sayfada og:/twitter: meta etiketi yok"]
        if name in ("poster_url", "backdrop_url", "image", "poster", "backdrop") or spec.get("attr") in _IMG_ATTRS:
            alternatives += [f"{_handle(i)} " + " ".join(f"{k}={_clip(v, 50)}" for k, v in i.attributes.items() if k in _IMG_ATTRS and v)
                             for i in tree.css("img[data-src], img[data-lazy-src]")[:2]] + [m for m in metas if "image" in m][:1]
        snippet = _label_snippet(html, name, spec)
        if snippet:
            alternatives.append("etiket çevresindeki HTML: " + snippet)
        entry: dict[str, Any] = {"where": f"{where}.fields.{name}", "problem": problem}
        if alternatives:
            entry["alternatives"] = alternatives
        _diag("detail", entry)


def _failing_hint(name: str, c: dict, out: dict) -> str:
    """One sentence for a failed acceptance criterion: why, and what to try (``report.failing[].hint``)."""
    value = c.get("value")
    play = out.get("playable") or {}
    series = out.get("series") or {}
    if name == "valid_count":
        return f"liste yalnız {value} geçerli öğe verdi (en az {MIN_VALID_COUNT}): list.row_selector / fields.title / fields.detail_url'i diagnostics.list'e göre düzelt"
    if name.endswith("_fill") and name[:-5] in MIN_FILL:
        return (f"list.fields.{name[:-5]} satırların yalnız %{round((value or 0) * 100)}'inde dolu: selector'ı / attr'ı (lazy-load: data-src) "
                "diagnostics.list'teki 'alternatives'e göre değiştir")
    if name == "normalize_ok_ratio":
        return "normalize edilemeyen öğeler var: normalize.key.regex / template'i report.normalize.rejected örneklerine göre düzelt"
    if name == "duplicate_key_ratio":
        return "normalize anahtarı birçok öğede aynı çıkıyor: key.from / regex daha ayırt edici olmalı (slug + yıl gibi)"
    if name == "config_errors":
        lint = next((e for e in out.get("errors") or [] if "çift ters eğik çizgi" in str(e)), None)
        return ((f"{_clip(str(lint), 300)}. " if lint else "")
                + "yaml hataları var: errors listesinin ilk maddesinden başla")
    if name == "playable_ratio":
        first = next((s.get("error") for s in play.get("samples") or [] if s.get("error") and not s.get("blocked")), "")
        return (f"{play.get('resolved', 0)}/{play.get('checked', 0)} örnek akışa çözülmedi" + (f" (ilk hata: {_clip(first, 100)})" if first else "")
                + ": resolvers / providers'ı diagnostics.player'a göre düzelt; engelli içerik ise blocked: ya da availability_gate: yaz")
    if name == "series_have_episode_sources":
        return "diziler bölüm kaynağı taşımıyor: normalize.episode_source ya da series_page yaz (references/series-page.md)"
    if name == "series_inventory_ok":
        why = next((((s.get("diagnostics") or {}).get("first_rows") or [{}])[0].get("rejected_by") for s in series.get("samples") or []
                    if not s.get("episodes") and not s.get("skipped") and not s.get("blocked")), None)
        return "dizi sayfasından bölüm okunamadı" + (f": {_clip(why, 450)}" if why else ": series_page.row_selector / fields.url / episode_url_regex'i diagnostics.series'e göre düzelt")
    if name == "ingest_sample_ok":
        sample = series.get("ingest_sample") or {}
        reasons = {k: n for k, n in (sample.get("reasons") or {}).items() if k != "key_mismatch"}
        top = max(reasons, key=reasons.get) if reasons else ""
        mismatch = (sample.get("reasons") or {}).get("key_mismatch")
        return (f"ingest örneğinde {sample.get('ok', 0)}/{sample.get('judged', 0)} dizi okunabilir bir dizi sayfasına çözülüp bölüm listesi veriyor "
                f"(en az %{round(MIN_INGEST_SAMPLE_OK * 100)}); nedenler: {', '.join(f'{n}x {why}' for why, n in reasons.items()) or '?'}. "
                + (_INGEST_ADVICE.get(top, "") + ". " if top else "")
                + ("Kartın anahtarı dizi sayfasınınkinden farklı (key_mismatch): normalize.key.regex'i dizi slug'ına göre düzelt. " if mismatch else "")
                + "Atlanamaz: bölüm kartlarından gelen diziler de okunabilmeli (diagnostics.series, where=ingest_sample)")
    if name == "search_ok":
        return "canlı arama sonuç vermedi ya da listedeki başlığı bulamadı: search.url / row_selector / fields'i diagnostics.search'e göre düzelt"
    if name.startswith("collections_"):
        return "bir koleksiyon yeterli öğe vermedi: collections[].path / row_selector'ı diagnostics.collections'a göre düzelt ya da koleksiyonu çıkar"
    if name == "baseline_ok":
        return "öneri mevcut yaml'ın alanlarını düşürüyor ya da doluluğu belirgin azaltıyor: report.baseline.reasons'a bak"
    if name == "availability_gate_defined":
        return ("`availability_gate: {probe: 2, require: player}` yaz (references/blocked.md): oynatıcısı bulunamayan / telif engelli "
                "diziler ve filmler alınmasın. Atlanamaz: kullanıcıya sorulmaz")
    if name == "series_signal_collection":
        return ("ana sayfada / menüde dizi bölümü yok ya da en az 3 öğe vermiyor (trending ya da latest_series): ana sayfa / menü bölüm "
                "bağlantılarını aç (outline_page `blocks`, `nav_links`, `sections`): 'Son Eklenen Diziler', 'Trendler', ... Sitede gerçekten "
                f"yoksa ask_user (field `{SKIP_HOME_SERIES}`) ile sor")
    if name == "series_full_inventory":
        cards = _episode_cards(out.get("normalize"), [(None, e) for e in out.get("collections") or [] if isinstance(e, dict)])
        return (f"kartlar bölüm kartı ({cards} kart tek bir bölümün sayfasına gidiyor) ve series_page yok: her dizi kütüphanede kartın tek "
                "bölümüyle kalır. Dizi arşivi / dizi sayfası bağlantısı bul (menü 'Diziler', alfabetik arşiv, bölüm sayfasındaki dizi "
                "bağlantısı): list: dizi kartlarından, series_page: ile bölümler; bölüm kartı yalnız latest_episodes koleksiyonu olsun. "
                f"Dizi sayfalarında bölümler gerçekten listelenmiyorsa ask_user (field `{SKIP_SERIES_INVENTORY}`) ile sor")
    if name == "collection_poster_fill":
        poor = [f"{e.get('role')} (%{round(float((e.get('field_fill') or {}).get('poster_url') or 0) * 100)})"
                for e in out.get("collections") or []
                if isinstance(e, dict) and e.get("role") in POSTER_ROLES and e.get("status") != "skipped" and int(e.get("valid_count") or 0) > 0
                and float((e.get("field_fill") or {}).get("poster_url") or 0) < MIN_COLLECTION_POSTER_FILL]
        return ("koleksiyon kartlarında poster_url yok ya da seyrek" + (f" ({', '.join(poor[:4])})" if poor else "") + ": kart içindeki <img> "
                "(data-src / src) ile poster_url yaz (collections[].fields ya da liste alanları; query_html ile kartın outer_html'ine bak). "
                f"Kartta gerçekten poster yoksa ask_user (field `{SKIP_COLLECTION_POSTER}`) ile sor")
    if name == "detail_info_defined":
        info = out.get("detail_info") or {}
        names = ", ".join(info.get("missing") or []) or "?"
        return (f"dizi / film sayfasından yalnız {value} bilgi alanı alınıyor (en az {c.get('min')}): eksik gruplar: {names} (synopsis, year, cast, "
                "genres, rating, trailer_url, poster_url). Alan etiketleri sayfada geçiyorsa ('Özet', 'Yıl', 'Oyuncular', 'Tür', 'IMDb', 'Fragman') "
                "grep_page ile bulup o bloktan selector / regex ile detail.fields'a yaz; sayfada gerçekten yoksa ask_user (field = alan adı, "
                "alan alan) ile sor: kullanıcı 'sitede yok' demeden alanı atlama")
    if name == "home_path_is_canonical":
        target = (out.get("redirect_hint") or {}).get("target") or "?"
        return (f"site ana sayfası aslında {target}: list_url ve koleksiyon path'lerini bu adrese yaz (tarayıcı yönlendirmesi motorda "
                "görünmez); yönlendirmeyi fetch_page(url) sonucundaki redirect_hint / canonical_url gösterir")
    return f"{name}: değer {value}, sınır {c.get('min', c.get('max'))}"


def _failing(out: dict) -> list[dict]:
    """``report.failing``: every criterion that did not pass as ``{criterion, value, bound, hint}`` (only when ``passed`` is false)."""
    rows = []
    removed = out.get("removed_fields")
    for name, c in (out.get("criteria") or {}).items():
        if isinstance(c, dict) and c.get("ok") is False:
            hint = _failing_hint(name, c, out)
            if removed and name in ("detail_info_defined", "collection_poster_fill", "collections_valid_count"):
                hint = f"{_removed_warning(removed)}. {hint}"   # a failing field criterion that follows a removed field: say so first
            rows.append({"criterion": name, "value": c.get("value"), "bound": c.get("min", c.get("max")),
                         "hint": _clip(hint, HINT_CLIP)})
    return rows


# --- fetching ---------------------------------------------------------------------------------------------------

def _escalate(exc: Exception) -> bool:
    message = str(exc).lower()
    return "robots.txt" not in message and any(marker in message for marker in _ESCALATE)


def _thin(html: str) -> bool:
    return len(heal._clean_html(html, limit=10 ** 8)) < THIN_CHARS


def _url_allowed(url: str) -> bool:
    try:
        netguard.check_url(url)
        return True
    except ValueError:
        return False


def _clean_referer(referer: Optional[str]) -> str:
    """The Referer header value (``""`` = none). Only an http(s) URL on one line: it is sent as a header, never fetched."""
    value = (referer or "").strip()
    if not value:
        return ""
    if not re.match(r"^https?://[^\s]+$", value) or re.search(r"[\x00-\x1f\x7f]", value):
        raise ApiError(400, "bad_referer", "referer must be one absolute http(s) URL (the detail page the player is embedded in)")
    return value


def _fetch_once(url: str, mode: str, wait_for: str, referer: str = "", deadline: Optional[float] = None) -> dict:
    if mode == "chrome":
        left = CHROME_TIMEOUT if deadline is None else max(1.0, min(CHROME_TIMEOUT, deadline - time.monotonic()))
        page = fetch.impersonated_get(url, headers={"Referer": referer} if referer else {}, timeout=left,
                                      max_bytes=CHROME_MAX_BYTES, max_redirects=3, allow=_url_allowed)
        return {"html": page.text, "initial_html": page.text, "status": page.status, "final_url": page.url}
    bundle = fetch.page_bundle(_draft_cfg({"fetch_mode": mode}), url, wait_for=wait_for)
    return bundle if isinstance(bundle, dict) else {"html": str(bundle or "")}


def _fetch_store(url: str, mode: str, wait_for: str, deadline: float, referer: str = "") -> dict:
    """Fetch ``url`` (``mode`` auto = http first, browser on a challenge / thin page; ``chrome`` or a ``referer`` = the
    Chrome-fingerprint transport with that Referer, no escalation), keep it in the page store.

    Returns ``{meta, html, bundle, attempts}``. ``ApiError`` for a refused URL (400), a failed fetch (502) or time (504)."""
    url = _check(url)
    referer = _clean_referer(referer)
    if referer:
        mode = "chrome"
    modes = ["http", "browser"] if mode == "auto" else [mode]
    attempts: list[str] = []
    thin: Optional[tuple[str, dict]] = None
    result: Optional[tuple[str, dict]] = None
    for index, current in enumerate(modes):
        _check_deadline(deadline)
        try:
            bundle = _fetch_once(url, current, wait_for, referer, deadline)
        except fetch.FetchError as exc:
            attempts.append(f"{current}: {' '.join(str(exc).split())[:200]}")
            if mode == "auto" and index == 0 and _escalate(exc):
                continue
            if thin is not None:
                result = thin   # the browser failed, the thin http page is all there is
                break
            raise ApiError(502, "fetch_failed", attempts[-1])
        html = bundle.get("html") or ""
        if mode == "auto" and index == 0 and _thin(html):
            attempts.append(f"http: thin content ({len(html)} bytes), trying the browser")
            thin = (current, bundle)
            continue
        attempts.append(f"{current}: ok")
        result = (current, bundle)
        break
    if result is None:
        result = thin
    if result is None:
        raise ApiError(502, "fetch_failed", attempts[-1] if attempts else "fetch failed")
    used, bundle = result
    html = bundle.get("html") or ""
    final = _check(str(bundle.get("final_url") or url))   # redirects (when the transport reports them) are re-checked
    status = int(bundle.get("status") or 200)
    meta = onboard_store.save_page(url, final, used, status, html, referer=referer if used == "chrome" else "")
    onboard_store.maybe_prune()
    return {"meta": meta, "html": html, "bundle": bundle, "attempts": attempts}


def _title_of(html: str) -> str:
    node = HTMLParser(html).css_first("title")
    return _clip(_text(node), TEXT_CLIP) if node is not None else ""


# --- canonical address / redirect hint ---------------------------------------------------------------------------
# A server-side fetch does not see what a browser does with a page: a JavaScript / meta redirect, or a home page that calls itself
# something else (``<link rel=canonical>``, ``og:url``, JSON-LD ``WebPage.url``). ``_page_signals`` reads those from the stored page so
# the agent learns "the home page is really /tr2/" (``canonical_url`` + ``redirect_hint`` of ``fetch_page`` / ``outline_page``) and
# ``home_path_is_canonical`` can judge a yaml that still reads the redirecting path.
_JS_JUMP = re.compile(r"""(?:^|[;{}\s])(?:(?:window|document|top|self|parent)\s*\.\s*)?location(?:\s*\.\s*href)?\s*=(?!=)\s*(['"])([^'"\s]{1,300})\1""")
_JS_CALL = re.compile(r"""(?:^|[;{}\s])(?:(?:window|document|top|self|parent)\s*\.\s*)?location\s*\.\s*(?:replace|assign)\s*\(\s*(['"])([^'"\s]{1,300})\1\s*\)""")
_JS_CONDITIONAL = re.compile(r"\b(?:if|else|switch|navigator|userAgent|innerWidth|screen|matchMedia|cookie|localStorage|referrer)\b|\?")
_META_REFRESH = re.compile(r"url\s*=\s*['\"]?\s*([^'\"\s;]+)", re.I)
_WEBPAGE_TYPES = frozenset({"WebPage", "CollectionPage"})
JSONLD_MAX = 200_000          # characters of one JSON-LD script that are parsed
JS_JUMP_MAX = 800             # a script longer than this is an application, not a redirect


def _norm_path(url: str) -> str:
    """Comparable path of ``url`` (lower case, no trailing slash, ``/`` for the root); query and fragment do not count."""
    path = (urlsplit(url or "").path or "/").lower().rstrip("/")
    return path or "/"


def _jsonld_pages(tree) -> list[dict]:
    """The ``WebPage`` / ``CollectionPage`` objects of the page's JSON-LD scripts that carry a ``url``."""
    out: list[dict] = []
    for node in tree.css('script[type="application/ld+json"]'):
        text = node.text() or ""
        if not text.strip() or len(text) > JSONLD_MAX:
            continue
        try:
            stack: list = [json.loads(text)]
        except ValueError:
            continue
        while stack:
            item = stack.pop(0)
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                graph = item.get("@graph")
                if isinstance(graph, list):
                    stack.extend(graph)
                kinds = item.get("@type")
                kinds = kinds if isinstance(kinds, list) else [kinds]
                if _WEBPAGE_TYPES & {k for k in kinds if isinstance(k, str)} and isinstance(item.get("url"), str):
                    out.append(item)
    return out


def _page_signals(html: str, requested: str, final: str = "") -> dict:
    """``{canonical_url?, redirect_hint?}`` of a stored page.

    ``canonical_url`` = ``<link rel=canonical>``, else ``og:url``, else a JSON-LD ``WebPage`` / ``CollectionPage`` url (absolute).
    ``redirect_hint`` = ``{kind, target, note}`` when the page is really ANOTHER path of the same site than the one asked for
    (``requested``): ``http`` (the transport ended elsewhere: ``final`` differs), ``meta`` (``<meta http-equiv=refresh>``), ``js``
    (a bare ``location = '...'`` / ``location.replace('...')`` script, no condition around it), ``canonical`` (only for a request of
    the site's ROOT ``/``: the page names another path as its canonical address / its own "Anasayfa"). Another host is never a hint
    (a path of the site is what a yaml ``list_url`` / collection ``path`` can name); the order is http > meta > js > canonical."""
    out: dict[str, Any] = {}
    if not html or not requested:
        return out
    base = final or requested
    try:
        tree = HTMLParser(html)
    except Exception:
        return out
    candidates: list[tuple[str, str]] = []
    link = tree.css_first('link[rel="canonical"]')
    if link is not None and (link.attributes.get("href") or "").strip():
        candidates.append(("link", urljoin(base, link.attributes["href"].strip())))
    og = tree.css_first('meta[property="og:url"]')
    if og is not None and (og.attributes.get("content") or "").strip():
        candidates.append(("og", urljoin(base, og.attributes["content"].strip())))
    for page in _jsonld_pages(tree):
        candidates.append(("jsonld", urljoin(base, page["url"].strip())))
    if candidates:
        out["canonical_url"] = _clip(candidates[0][1], 300)
    want = _norm_path(requested)

    def elsewhere(target: str) -> Optional[str]:
        """``target`` as an absolute URL when it is another path of the requested site (http / https, same host)."""
        try:
            absolute = urljoin(base, target.strip())
            parts = urlsplit(absolute)
        except ValueError:
            return None
        if parts.scheme not in ("http", "https") or _host_key(absolute) != _host_key(requested) or _norm_path(absolute) == want:
            return None
        return absolute

    found: Optional[tuple[str, str]] = None
    if final and _host_key(final) == _host_key(requested) and _norm_path(final) != want:
        found = ("http", final)
    if found is None:
        for node in tree.css("meta[http-equiv]"):
            if (node.attributes.get("http-equiv") or "").strip().lower() == "refresh":
                match = _META_REFRESH.search(node.attributes.get("content") or "")
                target = elsewhere(match.group(1)) if match else None
                if target:
                    found = ("meta", target)
                    break
    if found is None:
        for node in tree.css("script"):
            if node.attributes.get("src"):
                continue
            text = (node.text() or "").strip()
            if not text or len(text) > JS_JUMP_MAX or _JS_CONDITIONAL.search(text):
                continue
            match = _JS_JUMP.search(text) or _JS_CALL.search(text)
            target = elsewhere(match.group(2)) if match else None
            if target:
                found = ("js", target)
                break
    if found is None and want == "/":
        for _source, candidate in candidates:
            target = elsewhere(candidate)
            if target:
                found = ("canonical", target)
                break
    if found is not None:
        kind, target = found
        what = "site ana sayfası aslında" if want == "/" else "bu sayfa aslında"
        tail = ("list_url ve koleksiyon path'lerini bu adrese yaz (tarayıcı yönlendirmesi motorda görünmez)" if kind != "http"
                else "list_url ve koleksiyon path'lerini doğrudan bu adrese yaz")
        out["redirect_hint"] = {"kind": kind, "target": _clip(target, 300), "note": f"{what} {_clip(target, 200)}: {tail}"}
    return out


def _do_fetch(body, *, deadline: float) -> dict:
    got = _fetch_store(body.url, body.mode, body.wait_for or "", deadline, body.referer or "")
    meta, html = got["meta"], got["html"]
    out = {"page_id": meta["page_id"], "final_url": meta["final_url"], "fetch_mode": meta["fetch_mode"],
           "status": meta["status"], "bytes": meta["bytes"], "title": _title_of(html),
           "html_excerpt": heal._clean_html(html, limit=EXCERPT_CHARS)}
    out.update(_page_signals(html, meta.get("url") or body.url, meta.get("final_url") or ""))
    if meta["fetch_mode"] == "chrome":
        out["hint"] = ("fetch_mode 'chrome' is a diagnostic transport (Chrome TLS fingerprint + the Referer): it is NOT a "
                       "valid yaml fetch_mode. To read this page at playback time use a player_page resolver "
                       "(fetch: http, referer: \"{page_url}\").")
    if meta.get("truncated"):
        out["truncated"] = True
    if len(got["attempts"]) > 1:
        out["attempts"] = got["attempts"]
    return out


# --- query / outline --------------------------------------------------------------------------------------------

def _do_query(body, *, deadline: float) -> dict:
    html, _meta = _load_page(body.page_id)
    try:
        nodes = HTMLParser(html).css(body.selector)
    except Exception as exc:
        raise ApiError(400, "bad_selector", f"invalid CSS selector {body.selector!r}: {' '.join(str(exc).split())[:150]}")
    limit = min(max(int(body.limit), 1), MAX_QUERY_ITEMS)
    items = []
    for node in nodes[:limit]:
        item = {"text": _clip(_text(node), TEXT_CLIP)}
        if body.attr:
            item["attr_value"] = _clip(node.attributes.get(body.attr), 300)
        item["outer_html"] = _clip(node.html or "", OUTER_CLIP)
        items.append(item)
    return {"count": len(nodes), "items": items}


def _selector_of(tag: str, classes: tuple) -> Optional[str]:
    safe = [c for c in classes if _SAFE_CLASS.match(c)][:4]
    return tag + "".join("." + c for c in safe) if safe else None


def _host_counts(urls: list) -> dict:
    counts: dict[str, int] = {}
    for url in urls:
        host = (urlsplit(url).hostname or "").lower()
        if host and urlsplit(url).scheme in ("http", "https"):
            counts[host] = counts.get(host, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1])[:LIST_CAP])


_REGION_TAGS = {"nav": "nav", "header": "header", "footer": "footer"}
_MENU_TOKEN = re.compile(r"(?:^|[-_ ])(?:menu|navbar|nav|navigation|topbar|sidebar)(?:$|[-_ ])", re.I)
_NO_LINK = ("#", "javascript:", "mailto:", "tel:", "data:")
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
# class tokens of section headings written as <div class="section-title"> ... ("title" alone is a card's title, not a section)
_HEADING_TOKEN = re.compile(r"(?:^|[-_])(?:title|heading|headline|baslik)(?:$|[-_])", re.I)
_NOT_HEADING_TOKEN = re.compile(r"^(?:card|item|post|entry|movie|film|poster|episode|series|show|video|product|media|thumb|tile|"
                                r"modal|dialog|popup|tooltip|meta|site|logo|brand|page|player)", re.I)
_VISUAL_ATTRS = ("data-bg", "data-background", "data-src", "data-image", "data-original", "data-lazy-src")


def _host_key(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _region_of(node) -> Optional[str]:
    """The page region a link sits in: nav / header / footer (their tag) or menu (a menu-like class or id), nearest first."""
    parent, depth = node.parent, 0
    while parent is not None and depth < 15:
        tag = parent.tag
        if tag in _REGION_TAGS:
            return _REGION_TAGS[tag]
        attrs = parent.attributes
        if _MENU_TOKEN.search((attrs.get("class") or "") + " " + (attrs.get("id") or "")):
            return "menu"
        parent, depth = parent.parent, depth + 1
    return None


def _nav_links(tree, base: str) -> list[dict]:
    """Unique same-host links of the menu / header / nav / footer, as ``{text, href (path), region}`` (document order)."""
    host, seen, out = _host_key(base), set(), []
    for node in tree.css("a[href]"):
        href = (node.attributes.get("href") or "").strip()
        if not href or href.lower().startswith(_NO_LINK):
            continue
        absolute = urljoin(base, href)
        if urlsplit(absolute).scheme not in ("http", "https") or _host_key(absolute) != host:
            continue
        region = _region_of(node)
        if region is None:
            continue
        parts = urlsplit(absolute)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        if path in seen:
            continue
        text = _text(node) or (node.attributes.get("title") or node.attributes.get("aria-label") or "").strip()
        if not text:
            image = node.css_first("img[alt]")
            text = (image.attributes.get("alt") or "").strip() if image is not None else ""
        seen.add(path)
        out.append({"text": _clip(text, 60), "href": _clip(path, 200), "region": region})
        if len(out) >= MAX_NAV_LINKS:
            break
    return out


def _has_visual(node) -> bool:
    if node.tag in ("img", "picture") or node.css_first("img, picture, [style*='url(']") is not None:
        return True
    return any(node.attributes.get(a) for a in _VISUAL_ATTRS) or node.css_first(
        ",".join(f"[{a}]" for a in _VISUAL_ATTRS)) is not None


def _is_section_class(classes: tuple) -> bool:
    return any(c != "title" and _HEADING_TOKEN.search(c) and not _NOT_HEADING_TOKEN.match(c) for c in classes)


def _scoped_selector(tree, nodes: list, card_sel: str) -> tuple[str, int]:
    """A selector for exactly the cards of one section: ``card_sel`` as is when nothing else on the page matches it, else
    prefixed with the nearest ancestor (id or classes) that fences them in. Returns (selector, what it matches on the page)."""
    want = len(nodes)
    try:
        everywhere = len(tree.css(card_sel))
    except Exception:
        return card_sel, want
    if everywhere == want:
        return card_sel, everywhere
    parent, depth = nodes[0].parent, 0
    while parent is not None and depth < 6:
        attrs = parent.attributes
        ident = attrs.get("id") or ""
        prefix = f"#{ident}" if ident and _SAFE_CLASS.match(ident) else _selector_of(
            parent.tag, tuple(sorted(set((attrs.get("class") or "").split()))))
        if prefix and parent.tag not in _SKIP_TAGS:
            candidate = f"{prefix} {card_sel}"
            try:
                if len(tree.css(candidate)) == want:
                    return candidate, want
            except Exception:
                pass
        parent, depth = parent.parent, depth + 1
    return card_sel, everywhere


_ACTION_HREF = re.compile(r"^\?|wpfpaction|favori|favorite|wishlist|watchlist|bookmark|[?&]action=", re.I)   # add-to-favourites links are no content link
_KIND_EPISODE = re.compile(r"(?:bolum|bölüm|episode|-ep-|/ep-?\d)", re.I)
_KIND_FILM = re.compile(r"/(?:film|filmler|movie|movies)(?:/|-|$)", re.I)
_KIND_SERIES = re.compile(r"/(?:dizi|diziler|series|show|shows|tv-show|tvshow)(?:/|-|$)", re.I)


def _link_kind(href: str) -> str:
    """What a card link opens: ``episode`` (a page of ONE episode: ``-3-sezon-5-bolum``, ``/episode/``), ``film``, ``series`` (a
    ``/dizi/<slug>`` style series page) or ``other`` (the address does not say: read ``sample_hrefs``). Only the ADDRESS counts: a series
    card often carries a "10 Bölüm" label too."""
    path = urlsplit(href or "").path
    if _KIND_EPISODE.search(path) and re.search(r"\d", path):
        return "episode"
    if _KIND_FILM.search(path):
        return "film"
    if _KIND_SERIES.search(path):
        return "series"
    return "other"


def _block_facts(nodes: list, base: str) -> dict:
    """What the cards of one repeating block are: ``link_kind`` (the kind of at least 80% of the first 10 links, else ``mixed``),
    ``sample_title`` (first card), ``sample_link`` and ``sample_hrefs`` (<= 3 paths)."""
    pairs: list[tuple[str, str]] = []
    title = ""
    for node in nodes[:10]:
        anchor = node if node.tag == "a" else next((a for a in node.css("a[href]") if not _ACTION_HREF.search(a.attributes.get("href") or "")), None)
        href = (anchor.attributes.get("href") or "").strip() if anchor is not None else ""
        if not href or href.lower().startswith(_NO_LINK):
            continue
        absolute = urljoin(base, href)
        pairs.append((absolute, _text(anchor) or _text(node)))
        if not title:
            heading = node.css_first("h1, h2, h3, h4, h5, h6, [class*=title]")
            image = node.css_first("img[alt]")
            title = (_text(heading) if heading is not None else "") or _text(node) or (
                (image.attributes.get("alt") or "").strip() if image is not None else "")
    kinds = [_link_kind(h) for h, _t in pairs]
    top = max(set(kinds), key=kinds.count) if kinds else "other"
    return {"link_kind": top if kinds and kinds.count(top) * 5 >= len(kinds) * 4 else ("mixed" if kinds else "other"),
            "sample_title": _clip(title, 80), "sample_link": _clip(pairs[0][0], 200) if pairs else None,
            "sample_hrefs": [_clip(urlsplit(h).path or "/", 120) for h, _t in pairs[:3]]}


def _heading_text(node) -> str:
    """The nearest heading BEFORE ``node`` in the document (a heading tag or a ``*-title`` class element among the previous siblings
    of the node or of an ancestor, <= 6 levels up); "" when none is close. Best effort: a block without one is listed anyway."""
    current, depth = node, 0
    while current is not None and depth < 6:
        sibling, hops = current.prev, 0
        while sibling is not None and hops < 8:
            tag = sibling.tag
            if tag and not tag.startswith("-") and tag not in ("script", "style"):
                classes = tuple(sorted(set((sibling.attributes.get("class") or "").split())))
                if tag in _HEADING_TAGS or (classes and _is_section_class(classes)):
                    text = _text(sibling)
                    if 2 <= len(text) <= 80:
                        return text
                elif len(sibling.css("a[href]")) <= 3:   # a block of cards is another section, not this one's heading
                    heading = sibling.css_first("h1, h2, h3, h4, h5, h6")
                    text = _text(heading) if heading is not None else ""
                    if 2 <= len(text) <= 80:
                        return text
            sibling, hops = sibling.prev, hops + 1
        current, depth = current.parent, depth + 1
    return ""


def _blocks(tree, groups: dict, base: str, sections: list) -> list[dict]:
    """EVERY repeating card block of the page, one entry each: ``{heading?, selector, count, link_kind, sample_title, sample_link,
    sample_hrefs}`` (+ ``selector_matches`` when the selector is broader than the block). The ``sections`` (heading + card group) come
    first, then the other repeating groups of >= ``BLOCK_MIN_CARDS`` linked, pictured cards that sit outside the menu / header / footer and
    whose first link no section covers (a block the heading scan missed). The agent must decide for EVERY block whether it becomes
    a collection (and with which role) or why not (``link_kind`` says whether its cards are series pages or episode pages)."""
    out: list[dict] = []
    seen: set = set()
    for section in sections:
        entry = {k: section[k] for k in ("heading", "selector", "count", "selector_matches", "link_kind", "sample_title", "sample_link",
                                          "sample_hrefs") if k in section}
        out.append(entry)
        seen.add(section.get("sample_link"))
    ranked = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    for (tag, classes), nodes in ranked:
        if len(out) >= MAX_BLOCKS:
            break
        if len(nodes) < BLOCK_MIN_CARDS or tag in _SKIP_TAGS or not tag or tag.startswith("-"):
            continue
        selector_of = _selector_of(tag, classes)
        if selector_of is None:
            continue
        sample = nodes[:20]
        if sum(1 for n in sample if n.tag == "a" or n.css_first("a[href]") is not None) / len(sample) < 0.6:
            continue
        if _region_of(nodes[0]) is not None:
            continue   # menu / header / footer links are no home block
        facts = _block_facts(nodes, base)
        visual = sum(1 for n in sample if _has_visual(n)) / len(sample)
        if facts["sample_link"] in seen or not (visual >= 0.5 or facts["link_kind"] in ("episode", "series", "film")):
            continue
        seen.add(facts["sample_link"])
        selector, matches = _scoped_selector(tree, nodes, selector_of)
        entry = {"selector": selector, "count": len(nodes), **facts}
        heading = _heading_text(nodes[0])
        if heading:
            entry = {"heading": _clip(heading, 80), **entry}
        if matches != len(nodes):
            entry["selector_matches"] = matches
        out.append(entry)
    return out


def _heading_link(node, base: str) -> Optional[str]:
    anchor = node if node.tag == "a" else node.css_first("a[href]")
    if anchor is None and node.parent is not None and node.parent.tag == "a":
        anchor = node.parent
    href = (anchor.attributes.get("href") or "").strip() if anchor is not None else ""
    if not href or href.lower().startswith(_NO_LINK):
        return None
    return _clip(urljoin(base, href), 200)


def _sections(tree, groups: dict, base: str) -> list[dict]:
    """Home sections: a heading (h1-h6 or a ``*-title`` class) and the repeating card group that follows it in document
    order, as ``{heading, selector, count, sample_link}`` (+ ``heading_link``, ``selector_matches`` when the selector is
    broader than the section). Headings inside a card (a title in each poster) are not sections."""
    card_keys: set = set()
    for key, nodes in groups.items():
        if len(nodes) < 3 or _selector_of(*key) is None:
            continue
        sample = nodes[:20]
        linked = sum(1 for n in sample if n.tag == "a" or n.css_first("a[href]") is not None) / len(sample)
        links = sum(len(n.css("a[href]")) for n in sample) / len(sample)
        if linked >= 0.8 and links <= 6:
            card_keys.add(key)
    if not card_keys:
        return []
    card_ids = {n.mem_id for key in card_keys for n in groups[key]}
    sections: list[dict] = []
    current: Optional[dict] = None
    for node in tree.css("*"):
        tag = node.tag
        if tag in _SKIP_TAGS or not tag or tag.startswith("-"):
            continue
        classes = tuple(sorted(set((node.attributes.get("class") or "").split())))
        trusted = bool(classes) and _is_section_class(classes)
        if tag in _HEADING_TAGS or trusted:
            text = _text(node)
            if 2 <= len(text) <= 80:
                inside = False
                if not trusted:   # a plain h2..h6 inside a card is the card's title
                    parent, depth = node.parent, 0
                    while parent is not None and depth < 8 and not inside:
                        inside = parent.mem_id in card_ids
                        parent, depth = parent.parent, depth + 1
                if not inside:
                    current = {"heading": text, "node": node, "buckets": {}}
                    sections.append(current)
                    continue
        if current is not None and (tag, classes) in card_keys:
            current["buckets"].setdefault((tag, classes), []).append(node)
    out: list[dict] = []
    seen: set = set()
    for section in sections:
        best, best_score = None, 0.0
        for key, nodes in section["buckets"].items():
            if len(nodes) < 3:
                continue
            visual = sum(1 for n in nodes[:20] if _has_visual(n)) / min(len(nodes), 20)
            if visual < 0.5:
                continue   # footer / menu link lists have no pictures: cards do
            score = len(nodes) * (1 + visual)
            if score > best_score:
                best, best_score = (key, nodes), score
        if best is None:
            continue
        (tag, classes), nodes = best
        selector, matches = _scoped_selector(tree, nodes, _selector_of(tag, classes))
        marker = (section["heading"], selector)
        if marker in seen:
            continue
        seen.add(marker)
        first = nodes[0]
        anchor = first if first.tag == "a" else first.css_first("a[href]")
        entry: dict[str, Any] = {"heading": _clip(section["heading"], 80), "selector": selector, "count": len(nodes),
                                 "sample_link": _clip(urljoin(base, anchor.attributes.get("href") or ""), 200)
                                 if anchor is not None else None}
        facts = _block_facts(nodes, base)   # what the cards are: a series page, an episode page, a film (``link_kind``)
        entry.update({k: facts[k] for k in ("link_kind", "sample_title", "sample_hrefs")})
        link = _heading_link(section["node"], base)
        if link:
            entry["heading_link"] = link
        if matches != len(nodes):
            entry["selector_matches"] = matches
        out.append(entry)
        if len(out) >= MAX_SECTIONS:
            break
    return out


# --- episode links (series pages) -------------------------------------------------------------------------------

_SLUG_NOT = frozenset({"sezon", "sezonu", "season", "bolum", "episode", "ep", "part", "page", "sayfa", "s", "e", "izle", "watch"})
_EPISODE_WORD = re.compile(r"(?:bolum|bölüm|episode|sezon|season|izle|watch)", re.I)   # in the URL of an episode link ...
_EPISODE_TEXT = re.compile(r"(?:bölüm|bolum|episode|\bep\b|\bs\d+\s*e\d+)", re.I)   # ... or in its text ("5. Bölüm")


def _link_shape(path: str) -> str:
    """The URL path with every run of digits masked as ``N``; the series slug (the text before the first number of the first
    segment that has one: ``/dizi-adi-1-sezon-2-bolum-x/``) is written ``<slug>``, so episodes of other series match too."""
    parts = re.sub(r"\d+", "N", path).split("/")
    for i, seg in enumerate(parts):
        if "N" not in seg:
            continue
        found = re.match(r"^(.+?)-N(?=-|$)", seg)
        if found and not all(w in _SLUG_NOT for w in found.group(1).lower().split("-")):
            parts[i] = "<slug>" + seg[len(found.group(1)):]
        break
    return "/".join(parts)


def _anchor_selector(tree, nodes: list) -> tuple[str, int]:
    """A selector for the links of one group: the anchor's own classes, else prefixed with the nearest ancestor (id or classes)
    that fences them in. Returns (selector, what it matches on the page); the closest count to the group wins."""
    want = len(nodes)
    first = nodes[0]
    own = _selector_of("a", tuple(sorted(set((first.attributes.get("class") or "").split())))) or "a"
    candidates = [own] if own != "a" else []
    parent, depth = first.parent, 0
    while parent is not None and depth < 6:
        attrs = parent.attributes
        ident = attrs.get("id") or ""
        prefix = f"#{ident}" if ident and _SAFE_CLASS.match(ident) else _selector_of(
            parent.tag, tuple(sorted(set((attrs.get("class") or "").split()))))
        if prefix and parent.tag not in _SKIP_TAGS:
            candidates.append(f"{prefix} {own}")
        parent, depth = parent.parent, depth + 1
    best: Optional[tuple[int, str, int]] = None
    for selector in candidates + ["a[href]"]:
        try:
            matches = len(tree.css(selector))
        except Exception:
            continue
        gap = matches - want if matches >= want else (want - matches) * 2
        if best is None or gap < best[0]:
            best = (gap, selector, matches)
        if gap == 0:
            break
    return (best[1], best[2]) if best else ("a[href]", want)


def _episode_links(tree, base: str) -> list[dict]:
    """Groups of same-host links that differ only in numbers: the episode links of a series page, as ``{selector, count,
    sample_hrefs (<= 3 paths), shape}`` (``selector_matches`` when the selector matches another number of links). The shape
    is what an ``episode_url_regex`` has to match: ``/<slug>-N-sezon-N-bolum-izle-full-tek-parca/`` (the episodes of one
    series are counted together; groups of other series with the same shape are left out)."""
    host = _host_key(base)
    own_path = urlsplit(base).path
    groups: dict[str, dict[str, Any]] = {}
    for node in tree.css("a[href]"):
        href = (node.attributes.get("href") or "").strip()
        if not href or href.lower().startswith(_NO_LINK):
            continue
        absolute = urljoin(base, href)
        parts = urlsplit(absolute)
        if parts.scheme not in ("http", "https") or _host_key(absolute) != host or not re.search(r"\d", parts.path):
            continue
        if parts.path == own_path:
            continue
        groups.setdefault(re.sub(r"\d+", "N", parts.path), {}).setdefault(parts.path, node)
    found = []
    for paths in groups.values():
        if len(paths) < EPISODE_LINK_MIN:
            continue
        nodes = list(paths.values())
        shape = _link_shape(next(iter(paths)))
        if not (_EPISODE_WORD.search(shape) or any(_EPISODE_TEXT.search(_text(n)) for n in nodes[:5])):
            continue   # numbered links that do not look like episodes (pagination, categories, film pages)
        selector, matches = _anchor_selector(tree, nodes)
        entry: dict[str, Any] = {"selector": selector, "count": len(nodes),
                                 "sample_hrefs": [_clip(p, 200) for p in list(paths)[:3]], "shape": _clip(shape, 200)}
        if matches != len(nodes):
            entry["selector_matches"] = matches
        found.append((-len(nodes), len(found), entry))
    found.sort(key=lambda row: row[:2])
    out: list[dict] = []
    for row in found:
        if all(row[2]["shape"] != other["shape"] for other in out):   # the biggest group of a shape speaks for it
            out.append(row[2])
    return out[:MAX_EPISODE_LINKS]


def _do_outline(body, *, deadline: float) -> dict:
    html, meta = _load_page(body.page_id)
    base = meta.get("final_url") or meta.get("url") or ""
    return _outline_of(html, base, meta.get("url") or base, meta.get("final_url") or "")


def _outline_of(html: str, base: str, requested: str, final: str = "") -> dict:
    """The outline of a page's HTML (``/outline`` answer; also what ``scraper/discover.py`` reads): ``base`` = the page's final URL,
    ``requested`` / ``final`` as ``_page_signals`` wants them."""
    tree = HTMLParser(html)
    groups: dict[tuple, list] = {}
    for node in tree.css("*"):
        tag = node.tag
        if tag in _SKIP_TAGS or not tag or tag.startswith("-"):
            continue
        classes = tuple(sorted(set((node.attributes.get("class") or "").split())))
        if classes:
            groups.setdefault((tag, classes), []).append(node)
    found = []
    for (tag, classes), nodes in groups.items():
        if len(nodes) < 5:
            continue
        selector = _selector_of(tag, classes)
        if selector is None:
            continue
        sample = nodes[:20]
        link = sum(1 for n in sample if n.tag == "a" or n.css_first("a[href]") is not None) / len(sample)
        image = sum(1 for n in sample if n.tag == "img" or n.css_first("img") is not None) / len(sample)
        first = nodes[0]
        anchor = first if first.tag == "a" else first.css_first("a[href]")
        picture = first if first.tag == "img" else first.css_first("img")
        try:
            matches = len(tree.css(selector))
        except Exception:
            matches = len(nodes)
        found.append({"selector": selector, "count": len(nodes), "selector_matches": matches,
                      "with_link": round(link, 2), "with_image": round(image, 2),
                      "link_kind": _block_facts(nodes, base)["link_kind"],
                      "sample_text": _clip(_text(first), 100),
                      "sample_href": _clip(anchor.attributes.get("href"), 200) if anchor is not None else None,
                      "sample_image": _clip((picture.attributes.get("data-src") or picture.attributes.get("src")), 200)
                      if picture is not None else None,
                      "sample_html": _clip(first.html or "", 400)})
    found.sort(key=lambda g: (not (g["with_link"] >= 0.8 and g["with_image"] >= 0.5), -g["count"]))
    iframes = [urljoin(base, n.attributes.get("src") or n.attributes.get("data-src") or "") for n in tree.css("iframe")]
    links = [urljoin(base, n.attributes.get("href") or "") for n in tree.css("a[href]")]
    sections = _sections(tree, groups, base)
    out = {"title": _title_of(html),
           "counts": {"a": len(links), "img": len(tree.css("img")), "iframe": len(iframes),
                      "script": len(tree.css("script"))},
           "repeating": found[:LIST_CAP], "iframe_hosts": _host_counts(iframes), "link_hosts": _host_counts(links),
           "nav_links": _nav_links(tree, base), "sections": sections, "blocks": _blocks(tree, groups, base, sections),
           "episode_links": _episode_links(tree, base)}
    out.update(_page_signals(html, requested or base, final or ""))
    return out


# --- grep -------------------------------------------------------------------------------------------------------

_GREP_FLAGS = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}
# Runs in a child interpreter (``python -I -S``): a pattern with catastrophic backtracking cannot be interrupted inside
# ``re`` (it holds the GIL, a thread would freeze the whole server), so the child is killed after GREP_TIMEOUT instead.
_GREP_SCRIPT = r"""
import json, re, sys
job = json.loads(sys.stdin.buffer.read().decode("utf-8"))
text, ctx, limit, clip, cap = job["text"], job["context"], job["limit"], job["clip"], job["cap"]
rx = re.compile(job["pattern"], job["flags"])
count, matches = 0, []
for m in rx.finditer(text):
    count += 1
    if len(matches) < limit:
        s, e = m.start(), m.end()
        body, after = text[s:e], text[e:e + ctx]
        if len(body) > clip:
            body, after = body[:clip] + "…", ""
        matches.append({"offset": s, "text": text[max(0, s - ctx):s] + body + after})
    if count >= cap:
        break
sys.stdout.write(json.dumps({"count": count, "count_capped": count >= cap, "matches": matches}))
"""


def _grep_flags(flags: Optional[str]) -> int:
    value = 0
    for char in (flags or "").lower():
        if char not in _GREP_FLAGS:
            raise ApiError(422, "bad_flags", f"unknown regex flag {char!r} (allowed: i = ignore case, m = multiline, s = dot matches newline)")
        value |= _GREP_FLAGS[char]
    return value


def _do_grep(body, *, deadline: float) -> dict:
    """Regex search in the RAW stored page (scripts and all: ``fetch_page``'s ``html_excerpt`` and ``query_html`` hide or
    reshape them). Offsets are character offsets into the stored page text."""
    html, meta = _load_page(body.page_id)
    flags = _grep_flags(body.flags)
    try:
        re.compile(body.pattern, flags)
    except re.error as exc:
        raise ApiError(422, "bad_pattern", f"invalid regex {body.pattern!r}: {exc}")
    except (RecursionError, OverflowError) as exc:
        raise ApiError(422, "bad_pattern", f"regex too complex ({type(exc).__name__})")
    _check_deadline(deadline)
    scanned = html[:GREP_MAX_BYTES]
    job = {"text": scanned, "pattern": body.pattern, "flags": flags, "context": min(max(int(body.context), 0), GREP_MAX_CONTEXT),
           "limit": min(max(int(body.limit), 1), GREP_MAX_LIMIT), "clip": GREP_MATCH_CLIP, "cap": GREP_COUNT_CAP}
    try:
        done = subprocess.run([sys.executable, "-I", "-S", "-c", _GREP_SCRIPT], input=json.dumps(job).encode("ascii"),
                              capture_output=True, timeout=GREP_TIMEOUT, env={"LANG": "C.UTF-8"})
    except subprocess.TimeoutExpired:
        raise ApiError(422, "pattern_too_slow", f"the regex ran longer than {GREP_TIMEOUT:g}s on this page and was stopped: "
                                                "simplify it (no nested quantifiers like (a+)+, use [^\"']* instead of .*)")
    except OSError as exc:
        raise ApiError(500, "grep_failed", f"cannot run the search ({exc})")
    if done.returncode != 0:
        raise ApiError(500, "grep_failed", "the search failed: " + " ".join(done.stderr.decode("utf-8", "replace").split())[-200:])
    out = json.loads(done.stdout.decode("utf-8"))
    if not out.get("count_capped"):
        out.pop("count_capped", None)   # only reported when the count stopped at GREP_COUNT_CAP
    if len(html) > GREP_MAX_BYTES or meta.get("truncated"):
        out["page_truncated"] = True
    return out


# --- config checks ----------------------------------------------------------------------------------------------

def _check_selector(selector: Any) -> Optional[str]:
    if not isinstance(selector, str) or not selector.strip():
        return "selector must be a non-empty string"
    try:
        HTMLParser("").css(selector)
    except Exception:
        return f"invalid CSS selector {selector!r}"
    return None


def _check_spec(path: str, spec: Any, errors: list) -> None:
    if not isinstance(spec, dict):
        errors.append(f"{path}: must be a mapping (selector, attr, ...)")
        return
    if "fallback" in spec:
        alternatives = spec["fallback"]
        if not isinstance(alternatives, list) or not alternatives:
            errors.append(f"{path}.fallback: must be a non-empty list of field specs")
            return
        for i, alternative in enumerate(alternatives):
            _check_spec(f"{path}.fallback[{i}]", alternative, errors)
        return
    if spec.get("self") is not True:
        problem = _check_selector(spec.get("selector"))
        if problem:
            errors.append(f"{path}: {problem}")
    if spec.get("regex") is not None:
        try:
            re.compile(str(spec["regex"]))
        except re.error as exc:
            errors.append(f"{path}.regex: invalid regex ({exc})")
    if spec.get("cast") not in _CASTS:
        errors.append(f"{path}.cast: must be one of int, float, date_tr")


def _check_fields(data: dict) -> list[str]:
    errors: list[str] = []
    block = data.get("list")
    if not isinstance(block, dict):
        return ["list: missing (needs row_selector and fields)"]
    problem = _check_selector(block.get("row_selector"))
    if problem:
        errors.append(f"list.row_selector: {problem}")
    fields = block.get("fields")
    if not isinstance(fields, dict) or not fields:
        errors.append("list.fields: must be a mapping of field name -> spec")
    else:
        for name in ("title", "detail_url"):
            if name not in fields:
                errors.append(f"list.fields.{name}: required")
        for name, spec in fields.items():
            _check_spec(f"list.fields.{name}", spec, errors)
    detail = data.get("detail")
    if detail is not None:
        dfields = detail.get("fields") if isinstance(detail, dict) else None
        if not isinstance(dfields, dict):
            errors.append("detail.fields: must be a mapping of field name -> spec")
        else:
            for name, spec in dfields.items():
                _check_spec(f"detail.fields.{name}", spec, errors)
    return errors


ITEM_LIMIT_MAX = 200   # ``library.ingest`` clamps the yaml ``item_limit`` to 1..200 (default ``ingest.COLLECTION_LIMIT``)


def _item_limit(data: dict) -> int:
    """The per-list / per-collection item cap of ingest for this yaml: top-level ``item_limit`` (clamped to 1..200 like
    ``ingest._ingest_source``), else ``ingest.COLLECTION_LIMIT``; an unusable value counts as the default (``_check_core`` reports it)."""
    from ..library import ingest
    value = data.get("item_limit")
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        value = ingest.COLLECTION_LIMIT
    try:
        return max(1, min(ITEM_LIMIT_MAX, int(value)))
    except (TypeError, ValueError):
        return ingest.COLLECTION_LIMIT


def _check_core(data: dict) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    limit = data.get("item_limit")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= ITEM_LIMIT_MAX):
        errors.append(f"item_limit: must be a whole number 1..{ITEM_LIMIT_MAX} (every list and collection ingests at most this "
                      "many items; default 30; there is no pagination)")
    base = data.get("base_url")
    if not isinstance(base, str) or not re.match(r"^https?://[^/\s]+", base):
        errors.append("base_url: must be an absolute http(s) URL")
    if data.get("list_url") is not None and not isinstance(data["list_url"], str):
        errors.append("list_url: must be a string (path or URL)")
    if data.get("fetch_mode", "http") not in ("http", "browser"):
        errors.append("fetch_mode: must be 'http' or 'browser'")
    if data.get("schema", "MovieItem") not in schema.SCHEMAS:
        errors.append(f"schema: unknown {data.get('schema')!r} (known: {', '.join(sorted(schema.SCHEMAS))})")
    if data.get("site_id") is not None and not (isinstance(data["site_id"], str) and SITE_ID_RE.match(data["site_id"])):
        errors.append("site_id: must match ^[a-z][a-z0-9_]{1,31}$")
    playback = data.get("playback")
    if playback is None:
        warnings.append("playback: missing (use 'video' for playable episodes/films or 'trailer')")
    elif playback not in ("video", "trailer"):
        errors.append("playback: must be 'video' or 'trailer'")
    if not data.get("display_name"):
        warnings.append("display_name: missing")
    images = data.get("image_hosts")
    if images is not None and not (isinstance(images, list) and all(isinstance(h, str) for h in images)):
        errors.append("image_hosts: must be a list of host names")
    return errors, warnings


def _check_playback(data: dict, extra_names: Any = ()) -> tuple[list[str], list[str]]:
    """``resolvers`` / ``providers`` problems (what a play request would need); ``extra_names`` = names of the draft's own
    provider recipes (valid in ``providers:`` although they are not in the library yet)."""
    errors: list[str] = []
    warnings: list[str] = []
    raw = data.get("resolvers")
    if raw is not None:
        errors += resolvers.validate(raw)
    names = data.get("providers")
    if names is not None:
        known = {p.name for p in registry.PROVIDERS} | set(extra_names)
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            errors.append("providers: must be a list of provider names")
        else:
            errors += [f"providers: unknown provider {n!r} (known: {', '.join(sorted(known))})" for n in names if n not in known]
    if data.get("playback") == "video" and not raw and not data.get("stream_resolver"):
        warnings.append("playback is 'video' but there is no resolvers list: nothing will be playable")
    return errors, warnings


def _check_normalize(data: dict) -> list[str]:
    rules = data.get("normalize")
    if not isinstance(rules, dict) or not rules:
        return ["normalize: missing (a new site has no code normalizer; define key.regex / key.from / type)"]
    try:
        from ..library import normalize as nrm
        return [f"normalize: {m}" if not str(m).startswith("normalize") else str(m) for m in nrm.validate_rules(rules)]
    except Exception as exc:
        return [f"normalize: cannot validate ({type(exc).__name__}: {exc})"]


def _url_params_errors(data: dict) -> list[str]:
    """Network targets that sit in the yaml itself (resolver endpoints, legacy stream_resolver): SSRF check."""
    errors: list[str] = []
    base = str(data.get("base_url") or "")

    def probe(where: str, value: Any) -> None:
        if not isinstance(value, str) or not value:
            return
        rendered = value.replace("{base}", base.rstrip("/")).replace("{page_url}", base.rstrip("/") + "/").replace("{video_id}", "1")
        try:
            netguard.check_url(rendered)
        except ValueError as exc:
            errors.append(f"{where}: {exc}")

    for i, item in enumerate(data.get("resolvers") or []):
        if isinstance(item, dict):
            for key in ("url", "endpoint", "referer"):
                if key in item and (key != "url" or str(item[key]).startswith(("http://", "https://", "{base}"))):
                    probe(f"resolvers[{i}].{key}", item[key])
    legacy = data.get("stream_resolver")
    if isinstance(legacy, dict):
        probe("stream_resolver.endpoint", legacy.get("endpoint"))
    return errors


# --- collections (home sections) ---------------------------------------------------------------------------------

_GENRE_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def _check_collection(index: int, c: Any, base: str) -> tuple[dict, dict]:
    """Syntax of one yaml ``collections:`` entry (no network: the page goes through the SSRF guard when it is fetched)."""
    spec = c if isinstance(c, dict) else {}
    entry: dict[str, Any] = {"id": str(spec.get("id") or f"#{index}"), "role": str(spec.get("role") or ""),
                             "path": str(spec.get("path") or ""), "status": "pending", "count": 0, "valid_count": 0,
                             "would_ingest": 0, "normalize_ok": False, "normalize_ok_ratio": 0.0, "errors": [], "samples": []}
    errors = entry["errors"]
    if not isinstance(c, dict):
        errors.append("must be a mapping (id, title, path, role, ...)")
        return spec, entry
    unknown = sorted(str(k) for k in spec if k not in COLLECTION_KEYS)
    if unknown:
        errors.append(f"unknown key(s) {', '.join(unknown)} (allowed: {', '.join(sorted(COLLECTION_KEYS))})")
    role = spec.get("role")
    if role not in site_collections.ROLES:
        errors.append(f"role: {role!r} is not one of {', '.join(site_collections.ROLES)}")
    if not isinstance(spec.get("id"), str) or not spec.get("id"):
        errors.append("id: required (<role>_<site_id>)")
    path = spec.get("path")
    if not isinstance(path, str) or not path.strip():
        errors.append("path: required (a path of the site or an absolute URL on the same host)")
    elif re.match(r"^https?://[^/\s]+", base):
        url = urljoin(base, path.strip())
        if urlsplit(url).scheme not in ("http", "https") or _host_key(url) != _host_key(base):
            errors.append(f"path: {path!r} must stay on the site's own host ({urlsplit(base).hostname})")
    if spec.get("row_selector") is not None:
        problem = _check_selector(spec["row_selector"])
        if problem:
            errors.append(f"row_selector: {problem}")
    if spec.get("fields") is not None:
        fields = spec["fields"]
        if not isinstance(fields, dict) or not fields:
            errors.append("fields: must be a mapping of field name -> spec")
        else:
            errors += [f"fields.{name}: required (a collection's own fields replace the list fields entirely)"
                       for name in ("title", "detail_url") if name not in fields]
            for name, field in fields.items():
                _check_spec(f"fields.{name}", field, errors)
    for key in ("required_fields", "excluded_fields"):
        if spec.get(key) is not None and not (isinstance(spec[key], list) and all(isinstance(v, str) for v in spec[key])):
            errors.append(f"{key}: must be a list of field names")
    if spec.get("sort_by") is not None and not isinstance(spec["sort_by"], str):
        errors.append("sort_by: must be a field name")
    if spec.get("sort_desc") is not None and not isinstance(spec["sort_desc"], bool):
        errors.append("sort_desc: must be true or false")
    if role == "genre":
        if not (isinstance(spec.get("genre"), str) and _GENRE_SLUG_RE.match(spec["genre"])):
            errors.append("genre: role genre needs a slug (genre: <slug>, lower-case letters, digits, - _)")
    return spec, entry


def _check_collections(data: dict, site_hint: str) -> tuple[list[tuple[dict, dict]], list[str], list[str]]:
    """Every ``collections:`` entry of the yaml checked (syntax; ids = ``collections.list_id(role, site_id)``).

    Returns ([(spec, entry)], errors, warnings); the errors are also in each entry's own ``errors`` (unprefixed)."""
    raw = data.get("collections")
    if raw is None or raw == []:
        return [], [], []
    if not isinstance(raw, list):
        return [], ["collections: must be a list of {id, title, path, role, ...}"], []
    errors: list[str] = []
    warnings: list[str] = []
    if len(raw) > MAX_COLLECTIONS:
        errors.append(f"collections: at most {MAX_COLLECTIONS} entries (got {len(raw)}); drop the least useful")
        raw = raw[:MAX_COLLECTIONS]
    in_yaml = data.get("site_id") if isinstance(data.get("site_id"), str) and SITE_ID_RE.match(data["site_id"]) else ""
    hint = site_hint if SITE_ID_RE.match(site_hint or "") else ""
    site_id = hint or in_yaml
    if hint and in_yaml and hint != in_yaml:
        warnings.append(f"collections: site_id {in_yaml!r} in the yaml differs from site_id_suggestion {hint!r}; "
                        f"the collection ids must end in the site id that is saved")
    base = str(data.get("base_url") or "")
    pairs = [_check_collection(i, c, base) for i, c in enumerate(raw)]
    seen: dict[str, int] = {}
    derived: set[str] = set()
    for spec, entry in pairs:
        cid, role = spec.get("id"), spec.get("role")
        if role in site_collections.ROLES and role not in ONBOARD_ROLES:
            warnings.append(f"collections[{entry['id']}]: role {role!r} lists a whole catalogue: onboarding writes home "
                            f"sections only ({', '.join(ONBOARD_ROLES)}); drop this collection")
        if isinstance(cid, str) and cid:
            seen[cid] = seen.get(cid, 0) + 1
            if seen[cid] == 2:
                entry["errors"].append(f"id: {cid!r} is used twice (one collection per role: <role>_<site_id>)")
            elif seen[cid] > 2:
                entry["errors"].append(f"id: {cid!r} is used more than twice")
            if role in site_collections.ROLES:
                if site_id:
                    want = site_collections.list_id(role, site_id)
                    if cid != want:
                        entry["errors"].append(f"id: must be {want!r} (<role>_<site_id>), got {cid!r}")
                elif cid.startswith(role + "_") and SITE_ID_RE.match(cid[len(role) + 1:]):
                    derived.add(cid[len(role) + 1:])
                else:
                    entry["errors"].append(f"id: must be <role>_<site_id> (e.g. {role}_mysite), got {cid!r}")
    if not site_id and pairs:
        if len(derived) > 1:
            errors.append(f"collections: ids must share one site id (<role>_<site_id>), found {', '.join(sorted(derived))}")
        warnings.append("collections: no site_id in the yaml (add site_id: <id>): the collection id suffix is not checked "
                        "against the site id yet")
    for spec, entry in pairs:
        errors += [f"collections[{entry['id']}]: {e}" for e in entry["errors"]]
    return pairs, errors, warnings


def _parse_collection(cfg, spec: dict, html: str, limit: int, all_out: Optional[list] = None) -> tuple[int, list[dict], dict]:
    """The collection's own rows out of a fetched page: the steps of ``ingest._fetch_collection`` after the fetch (parse with
    the collection's ``row_selector`` / ``fields`` or the list ones, schema, then ``ingest._collection_items`` for the
    required / excluded / sort / limit rules); the fetch itself goes through the sandbox guard, and there is no drift
    check (a draft has no baseline). Returns (rows matched, items, fill); ``all_out`` (a list) receives every valid row BEFORE the
    ``item_limit`` / required / sort rules (the series directory is built from whole pages)."""
    from ..library import ingest
    fields = spec.get("fields") or cfg.list_fields
    raw = parse.parse_list(html, spec.get("row_selector") or cfg.row_selector, fields)
    valid, _metrics = schema.validate_items(cfg.schema, raw)
    if all_out is not None:
        all_out.extend(valid)
    return len(raw), ingest._collection_items(valid, spec, limit), heal.field_fill(raw, fields.keys())


def _collections_stage(cfg, pairs: list, main: Optional[tuple[str, str, list, int]], deadline: float,
                       norm_ok: bool, errors: list, warnings: list, rows_out: Optional[list] = None) -> None:
    """Fetch and judge every collection (in order, within the call's time; the ones that no longer fit are ``skipped``).

    ``main`` = (list page url, its page id, its validated items, rows matched) of the list stage. Entries are filled in
    place: status ok|error|skipped, count, valid_count, normalize_ok(+_ratio), errors, samples (<= 3), page_id. Problems
    also go to the report's ``errors`` (a collection that cannot be read) or ``warnings`` (too few items: a criterion). ``rows_out`` (a
    list) receives the valid rows of every collection that was read (whole pages, before the ``item_limit``; main-list collections add
    nothing: the list stage has those rows already)."""
    from ..library import ingest, normalize as nrm
    limit = _item_limit(cfg.data)
    pages: dict[str, tuple[str, str]] = {}   # url -> (html, page id): two collections on one page fetch it once
    if main:
        found = onboard_store.load_page(main[1])
        if found:
            pages[main[0]] = (found[0], main[1])
    skipped: list[str] = []
    for spec, entry in pairs:
        if entry["errors"]:
            entry["status"] = "error"
            continue
        url = urljoin(cfg.base_url, spec["path"].strip())
        problems: list[str] = []   # what stops this collection from being read: also errors of the report
        try:
            if main and ingest.parses_main_list(cfg, spec):   # the list page, parsed the list way: filter what the list stage parsed
                count, items = main[3], ingest._collection_items(main[2], spec, limit)
                entry["page_id"] = main[1]
                if len(items) < MIN_COLLECTION_COUNT:   # why so few: which required field the list items lack
                    required = [f for f in spec.get("required_fields") or [] if isinstance(f, str)]
                    missing = {f: sum(1 for i in main[2] if not _filled(i.get(f))) for f in required}
                    _diag("collections", {"where": f"collections[{entry['id']}]", "rows_matched": count, "rows_accepted": len(items),
                                          "problem": f"the list page's {len(main[2])} valid item(s) gave {len(items)} after required_fields / excluded_fields",
                                          "list_items_missing_required_field": missing or None})
            else:
                if url not in pages:
                    if deadline - time.monotonic() < COLLECTION_NEEDS.get(cfg.fetch_mode, 15.0):
                        entry["status"] = "skipped"
                        skipped.append(entry["id"])
                        continue
                    got = _fetch_store(url, cfg.fetch_mode, spec.get("row_selector") or cfg.row_selector, deadline)
                    pages[url] = (got["html"], got["meta"]["page_id"])
                html, entry["page_id"] = pages[url]
                count, items, fill = _parse_collection(cfg, spec, html, limit, rows_out)
                if not count or len(items) < MIN_COLLECTION_COUNT or any(fill.get(n, 0.0) == 0.0 for n in ("title", "detail_url")):
                    _rows_diagnostics(html, spec.get("row_selector") or cfg.row_selector, spec.get("fields") or cfg.list_fields,
                                      f"collections[{entry['id']}]", fill, len(items), "collections")   # report.diagnostics.collections
                for name in ("title", "detail_url"):
                    if count and fill.get(name, 0.0) == 0.0:
                        problems.append(f"fields.{name}: selector found nothing in any of the {count} rows")
                if not count:
                    problems.append(f"row_selector {spec.get('row_selector') or cfg.row_selector!r} matched 0 elements "
                                    f"on {urlsplit(url).path or '/'} ({len(html)} bytes)")
        except ApiError as exc:
            message = exc.detail.get("message") if isinstance(exc.detail, dict) else str(exc.detail)
            if exc.status_code == 504:
                entry["status"] = "skipped"
                skipped.append(entry["id"])
            else:
                entry["status"] = "error"
                entry["errors"].append(f"page not available ({message})")
                errors.append(f"collections[{entry['id']}]: {entry['errors'][-1]}")
            continue
        except Exception as exc:
            entry["errors"].append(f"parsing failed ({type(exc).__name__}: {exc})")
            entry["status"] = "error"
            errors.append(f"collections[{entry['id']}]: {entry['errors'][-1]}")
            continue
        entry["errors"] += problems
        errors += [f"collections[{entry['id']}]: {e}" for e in problems]
        entry["count"], entry["valid_count"] = count, len(items)
        entry["would_ingest"] = len(items)   # ``_collection_items`` applied the item_limit already: what ingest takes
        entry["samples"] = [_compact(i) for i in items[:3]]
        entry["field_fill"] = {name: _ratio(sum(1 for i in items if _filled(i.get(name))), len(items))
                               for name in (spec.get("fields") or cfg.list_fields)}   # per field, over the items ingest takes
        if items and norm_ok:
            try:
                report = nrm.preview(cfg.data["normalize"], items, base_url=cfg.base_url)
                entry["normalize_ok_ratio"] = _ratio(report.get("ok") or 0, report.get("total") or 0)
                entry["episode_cards"] = int(report.get("episode_cards") or 0)   # cards that are ONE episode's page (series_full_inventory)
            except Exception as exc:
                entry["errors"].append(f"normalize: preview failed ({type(exc).__name__}: {exc})")
                errors.append(f"collections[{entry['id']}]: {entry['errors'][-1]}")
        entry["normalize_ok"] = entry["normalize_ok_ratio"] >= MIN_NORMALIZE_OK_RATIO
        if not problems and entry["valid_count"] < MIN_COLLECTION_COUNT:
            entry["errors"].append(f"only {entry['valid_count']} usable item(s) (needs {MIN_COLLECTION_COUNT}): check "
                                   "row_selector / fields / required_fields, or drop this collection")
            warnings.append(f"collections[{entry['id']}]: {entry['errors'][-1]}")
        entry["status"] = "error" if entry["errors"] else "ok"
    if skipped:
        warnings.append(f"collections: {', '.join(skipped)} not checked (the call ran out of time); call test_config again "
                        "(collections: true) with page_id and detail_page_id to check them")


# --- test_config ------------------------------------------------------------------------------------------------

def _ratio(num: float, den: float) -> float:
    return round(num / den, 3) if den else 0.0


def _criterion(value: float, bound: float, kind: str) -> dict:
    ok = value >= bound if kind == "min" else value <= bound
    return {"value": value, kind: bound, "ok": ok}


def _criteria(valid_count: int, fill: dict, norm: Optional[dict], error_count: int) -> dict:
    out = {"valid_count": _criterion(valid_count, MIN_VALID_COUNT, "min")}
    for name, minimum in MIN_FILL.items():
        out[f"{name}_fill"] = _criterion(fill.get(name, 0.0), minimum, "min")
    total = (norm or {}).get("total") or 0
    ok = (norm or {}).get("ok") or 0
    out["normalize_ok_ratio"] = _criterion(_ratio(ok, total), MIN_NORMALIZE_OK_RATIO, "min")
    out["duplicate_key_ratio"] = _criterion(
        _ratio(len((norm or {}).get("duplicate_keys") or []), ok) if norm else 1.0, MAX_DUPLICATE_KEY_RATIO, "max")
    out["config_errors"] = _criterion(error_count, 0, "max")
    return out


def _ingest_block(data: dict, valid_items: int) -> dict:
    """What a scan would take from the list page: ``item_limit`` (the cap of EVERY list and collection), the valid items the
    page gave, and ``would_ingest`` = min of the two (``ingest._ingest_source``; there is no pagination, so a site's whole
    catalogue is never ingested: that is the search feature's job)."""
    limit = _item_limit(data)
    return {"item_limit": limit, "list_items_on_page": valid_items, "would_ingest": min(valid_items, limit)}


def _list_stage(cfg, html: str, errors: list, warnings: list) -> tuple[Optional[dict], list, dict]:
    """parse_list -> validate_items -> field_fill. Returns (answer block, valid items, raw fill)."""
    try:
        raw = parse.parse_list(html, cfg.row_selector, cfg.list_fields)
        items, metrics = schema.validate_items(cfg.schema, raw)
        fill = heal.field_fill(raw, cfg.list_fields.keys())
    except Exception as exc:
        errors.append(f"list: parsing failed ({type(exc).__name__}: {exc})")
        return None, [], {}
    if not raw:
        errors.append(f"list.row_selector {cfg.row_selector!r} matched 0 elements on the page ({len(html)} bytes)")
    else:
        if metrics["invalid_count"]:
            warnings.append(f"list: {metrics['invalid_count']} of {len(raw)} rows rejected by schema {cfg.schema} "
                            f"(title missing or a field of the wrong type)")
        for name, value in fill.items():
            if value == 0.0 and name in ("title", "detail_url"):
                errors.append(f"list.fields.{name}: selector found nothing in any of the {len(raw)} rows")
            elif value < 1.0 and name in MIN_FILL and value < MIN_FILL[name]:
                warnings.append(f"list.fields.{name}: filled in only {round(value * 100)}% of {len(raw)} rows "
                                f"(needs {round(MIN_FILL[name] * 100)}%)")
            elif value == 0.0:
                warnings.append(f"list.fields.{name}: selector found nothing in any row")
    block = {"count": len(raw), "valid_count": metrics["valid_count"], "field_fill": fill,
             "fill_ratio": metrics["fill_ratio"], "key_field_fill": metrics["field_fill"],   # what the baseline comparison reads
             "samples": [_compact(item) for item in items[:5]]}
    if (not raw) or metrics["valid_count"] < MIN_VALID_COUNT or any(fill.get(n, 0.0) < m for n, m in MIN_FILL.items()):
        _rows_diagnostics(html, cfg.row_selector, cfg.list_fields, "list", fill, metrics["valid_count"], "list")   # report.diagnostics.list
    return block, items, fill


def _normalize_stage(cfg, items: list, errors: list, warnings: Optional[list] = None) -> Optional[dict]:
    if not items:
        return None
    try:
        from ..library import normalize as nrm
        report = nrm.preview(cfg.data["normalize"], items, base_url=cfg.base_url)
    except Exception as exc:
        errors.append(f"normalize: preview failed ({type(exc).__name__}: {exc})")
        return None
    report = dict(report)
    report["samples"] = [_clip(s) for s in (report.get("samples") or [])[:5]]
    report["duplicate_keys"] = list(report.get("duplicate_keys") or [])
    cleaned = report.get("cleaned") or {}
    if warnings is not None and (cleaned.get("key") or cleaned.get("title")):   # the engine's safety net changed something: say so (D1)
        shown = {field: next((x[field] for x in cleaned.get("samples") or [] if field in x), None) for field in ("key", "title")}
        if cleaned.get("key") and shown["key"]:
            warnings.append(f"normalize.key: key regex bölüm eki bırakıyor ({cleaned['key']} anahtar, örn. {shown['key'][0]!r} -> {shown['key'][1]!r}), "
                            "motor temizledi: regex'i düzelt (yalnız dizi slug'ını yakala)")
        if cleaned.get("title") and shown["title"]:
            warnings.append(f"normalize: {cleaned['title']} başlık site artığı taşıyor (örn. {_clip(shown['title'][0], 60)!r} -> "
                            f"{_clip(shown['title'][1], 60)!r}); motor temizledi (ham başlık raw'da kalır)")
    return report


def _detail_stage(cfg, items: list, detail_page_id: Optional[str], deadline: float, warnings: list,
                  extra_sample: bool = False) -> Optional[dict]:
    """The detail fields parsed on ONE detail page (the stored ``detail_page_id``, else the first list item's). ``extra_sample`` (a hardened
    new site, ``detail_info_defined``): a SECOND page (another list item) is parsed too when time allows; the block then carries
    ``samples`` = ``[{url, fill}]`` of every page parsed."""
    fields = cfg.detail_fields
    if not fields:
        return None
    try:
        if detail_page_id:
            html, meta = _load_page(detail_page_id)
            page_id, url = detail_page_id, meta.get("final_url") or meta.get("url") or ""
        else:
            target = next((i["detail_url"] for i in items if i.get("detail_url")), None)
            if not target:
                warnings.append("detail: no list item with a detail_url to fetch")
                return None
            got = _fetch_store(urljoin(cfg.base_url, target), cfg.fetch_mode, "", deadline)
            html, page_id, url = got["html"], got["meta"]["page_id"], got["meta"]["final_url"]
        parsed = parse.parse_detail(html, fields)
        if any(not _filled(v) for v in parsed.values()):
            _detail_diagnostics(html, fields, parsed)   # report.diagnostics.detail
    except ApiError as exc:
        warnings.append(f"detail: page not available ({exc.detail.get('message') if isinstance(exc.detail, dict) else exc.detail})")
        return None
    except Exception as exc:
        warnings.append(f"detail: parsing failed ({type(exc).__name__}: {exc})")
        return None
    fill = {k: (1.0 if _filled(v) else 0.0) for k, v in parsed.items()}
    block = {"page_id": page_id, "url": url, "fields": {k: _clip(v) for k, v in parsed.items()}, "fill": fill}
    if extra_sample:
        samples = [{"url": url, "fill": fill}]
        first = _norm_path(url)
        target = next((i["detail_url"] for i in items if i.get("detail_url")
                       and _norm_path(urljoin(cfg.base_url, i["detail_url"])) != first), None)
        if target and deadline - time.monotonic() >= DETAIL_SAMPLE_NEED:
            try:
                got = _fetch_store(urljoin(cfg.base_url, target), cfg.fetch_mode, "", deadline)
                more = parse.parse_detail(got["html"], fields)
                samples.append({"url": got["meta"]["final_url"], "fill": {k: (1.0 if _filled(v) else 0.0) for k, v in more.items()}})
            except Exception as exc:   # the second sample is a bonus: its absence only means the first one decides
                warnings.append(f"detail: second sample page not parsed ({type(exc).__name__})")
        block["samples"] = samples
    return block


# --- content that is not public (``blocked:`` / ``availability_gate:``) -------------------------------------------

def _lgate():
    """``library/gate.py`` (the production judgement); imported lazily like the other library modules."""
    from ..library import gate
    return gate


def _check_blocked_blocks(data: dict) -> list[str]:
    """Static problems of the yaml ``blocked:`` rules and ``availability_gate:``; [] when both are absent."""
    errors: list[str] = []
    try:
        errors += sblocked.validate(data.get("blocked"))
        errors += sblocked.validate_gate(data.get("availability_gate"))
    except Exception as exc:
        errors.append(f"blocked / availability_gate: cannot validate ({type(exc).__name__}: {exc})")
    return errors


class _GateRun:
    """The "is it public" judgement of ONE analysis: the ``blocked:`` rules and the ``availability_gate:`` of the draft, applied with
    the production logic (``library/gate.py``) to the pages the sandbox reads (one request per page: the fetched pages are kept for
    the playable stage). ``items`` = every series / film judged (``{url, kind, state, reason, via, probed}``); ``eliminated`` = the
    ones (and episode pages) that production would not take, with the reason."""

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.rules = [r for r in cfg.blocked if r.get("on") == "episode_page"]
        self.series_rules = [r for r in cfg.blocked if r.get("on") == "series_page"]
        self.gate = cfg.availability_gate
        self.require = "player" if self.gate else None   # ``stream``: the playable stage resolves the stream itself
        self.probe = int(self.gate["probe"]) if self.gate else sblocked.DEFAULT_PROBE
        self.pages: dict[str, dict] = {}                  # url -> the fetched page (``_fetch_store`` answer)
        self.verdicts: dict[str, dict] = {}
        self.items: list[dict] = []
        self.eliminated: list[dict] = []

    @property
    def active(self) -> bool:
        return bool(self.rules or self.series_rules or self.gate)

    def judge_page(self, url: str, deadline: float) -> dict:
        """Fetch and judge one playback page (cached); ``ApiError`` 504 when the call ran out of time."""
        if url in self.verdicts:
            return self.verdicts[url]
        gate = _lgate()
        try:
            got = _fetch_store(url, self.cfg.fetch_mode, "", deadline)
        except ApiError as exc:
            if exc.status_code == 504:
                raise
            message = exc.detail.get("message") if isinstance(exc.detail, dict) else str(exc.detail)
            verdict = gate.error_verdict(RuntimeError(str(message)), self.require)
        else:
            self.pages[url] = got
            html = got["bundle"].get("initial_html") or got["html"]
            extra = [got["html"]] if got["html"] and got["html"] != html else None
            verdict = gate.judge_html(self.cfg, html, got["meta"]["final_url"], kind="episode", require=self.require, htmls=extra,
                                      module_site=_module_site())
        self.verdicts[url] = verdict
        return verdict

    def judge_series(self, entries: list, deadline: float) -> dict:
        return _lgate().series_verdict(self.cfg, entries, probe=self.probe, require=self.require,
                                       judge=lambda url: self.judge_page(url, deadline))

    def record(self, url: str, kind: str, verdict: dict) -> None:
        """Remember an item's verdict; a blocked one is an eliminated sample."""
        item = {"url": url, "kind": kind, "state": verdict["state"], "reason": str(verdict.get("reason") or "")[:120],
                "via": verdict.get("via") or "", "probed": int(verdict.get("probed") or 0)}
        self.items.append(item)
        if verdict["state"] == "blocked":
            self.eliminated.append({"url": url, "reason": item["reason"], "via": item["via"], "kind": kind})

    def blocked_report(self) -> dict:
        """``report.blocked``: ``{count, rules, samples[{url, reason}]}`` (count = series / pages blocked by a rule or the gate)."""
        return {"count": len(self.eliminated), "rules": sblocked.describe(self.cfg.blocked),
                "samples": [{"url": e["url"], "reason": e["reason"]} for e in self.eliminated[:5]]}

    def gate_report(self) -> Optional[dict]:
        """``report.gate``: ``{probed, passed, skipped, retry, samples[{url, reason}]}`` over the items the gate judged; None without a gate."""
        if not self.gate:
            return None
        judged = [i for i in self.items if i["kind"] in ("series", "movie")]
        return {"probed": len(judged), "passed": sum(1 for i in judged if i["state"] == "ok"),
                "skipped": sum(1 for i in judged if i["state"] == "blocked"), "retry": sum(1 for i in judged if i["state"] == "retry"),
                "samples": [{"url": e["url"], "reason": e["reason"]} for e in self.eliminated if e["kind"] in ("series", "movie")][:5]}


# --- playability (``playable: true``) ---------------------------------------------------------------------------

def _playback_address(cfg, norm: dict, raw: dict) -> tuple[str, str]:
    """(page url, kind) the library would open to play a normalized item: the first episode ``video_sources`` page of a
    series, else ``source_url`` / the list item's detail page (a film, or a series card without an episode source)."""
    for entry in norm.get("video_sources") or []:
        url = entry.get("url") if isinstance(entry, dict) else None
        if isinstance(url, str) and url.strip():
            return urljoin(cfg.base_url, url.strip()), str(entry.get("kind") or "episode")
    url = norm.get("source_url") or raw.get("detail_url")
    kind = "series_page" if norm.get("type") == "series" else "movie"
    return (urljoin(cfg.base_url, url.strip()), kind) if isinstance(url, str) and url.strip() else ("", kind)


def _spread(seq: list, k: int) -> list:
    """``k`` items of ``seq`` spread over it (first, middle, last ...): a site that serves different players per title is
    seen better than with the first ``k`` neighbours."""
    if len(seq) <= k:
        return list(seq)
    if k <= 1:
        return seq[:1]
    return [seq[round(i * (len(seq) - 1) / (k - 1))] for i in range(k)]


def _inventory_picks(inventories: Optional[list], limit: int = PLAYABLE_SAMPLES) -> list[dict]:
    """Up to ``limit`` DIFFERENT episode pages out of the series inventories (``_series_stage``) as ``{key, kind, locator}``: ONE
    episode per DIFFERENT series first (three series = three different series; the episode page the availability check already
    fetched, else the middle one), then, with fewer series than samples, more episodes of those series (first, last, middle)."""
    usable = [inv for inv in inventories or [] if inv.get("episodes")]
    out: list[dict] = []
    seen: set = set()

    def add(inv: dict, episode: dict) -> None:
        if episode["url"] not in seen and len(out) < limit:
            seen.add(episode["url"])
            out.append({"key": inv["key"], "kind": "episode", "locator": episode["url"]})

    for inv in usable:
        episodes = inv["episodes"]
        add(inv, next((e for e in episodes if e["url"] == inv.get("ok_page")), None) or episodes[len(episodes) // 2])
    for rank in range(3):   # fewer series than samples: more episodes of the same series, spread
        for inv in usable:
            episodes = inv["episodes"]
            add(inv, [episodes[0], episodes[-1], episodes[len(episodes) // 2]][rank])
    return out


def _playable_picks(cfg, items: list, inventories: Optional[list] = None, spares: int = 0, skip_series: bool = False) -> list[dict]:
    """Up to PLAYABLE_SAMPLES DIFFERENT normalized items (titles first; when the list repeats titles, other episode pages of
    them) as ``{key, kind, locator}``, spread over the list, followed by ``spares`` more picks (the replacements of samples that turn
    out to be blocked content). Series cards without ``video_sources`` are followed through the ``series_page`` inventory instead
    (``inventories``: episode pages of the series pages that were read, one per different series), a film of the list keeps one place.
    ``skip_series``: every series that was read is blocked content, so the series cards (which only have a series page) are not followed."""
    from ..library import normalize as nrm
    rules = cfg.data["normalize"]
    from_inventory = _inventory_picks(inventories, PLAYABLE_SAMPLES + spares)
    titles: list[dict] = []
    repeats: list[dict] = []
    keys: set = set()
    urls: set = set()
    for raw in items:
        norm = nrm.generic_normalize(rules, raw, base_url=cfg.base_url)
        if not norm:
            continue
        if (from_inventory or skip_series) and norm.get("type") == "series" and not norm.get("video_sources"):
            continue   # its series page is followed through the inventory
        url, kind = _playback_address(cfg, norm, raw)
        if not url or url in urls:
            continue
        urls.add(url)
        pick = {"key": str(norm.get("source_key") or ""), "kind": kind, "locator": url}
        (repeats if pick["key"] in keys else titles).append(pick)
        keys.add(pick["key"])
    if from_inventory:
        films = _spread(titles, 1)
        main = from_inventory[:PLAYABLE_SAMPLES - len(films)] + films
        spare = from_inventory[PLAYABLE_SAMPLES - len(films):][:spares]
        return main + spare
    chosen = _spread(titles, PLAYABLE_SAMPLES)
    if len(chosen) < PLAYABLE_SAMPLES:
        chosen += _spread(repeats, PLAYABLE_SAMPLES - len(chosen))
    left = [p for p in titles + repeats if p not in chosen]
    return chosen + left[:spares]


def _cookie_loader(cfg, locator: str):
    lock = threading.Lock()
    cookies: dict[str, Any] = {}

    def load_cookies():
        with lock:
            if "jar" not in cookies and "error" not in cookies:
                try:
                    cookies["jar"] = fetch.session_cookies(cfg, locator)
                except Exception as exc:
                    cookies["error"] = exc
            if "error" in cookies:
                raise cookies["error"]
            return cookies["jar"]

    return load_cookies


def _follow_playback(cfg, locator: str, deadline: float, site_id: str = DRAFT_SITE_ID, got: Optional[dict] = None,
                     raw: Optional[list] = None) -> dict:
    """One playback page, end to end like ``library/videos._resolve_page`` without the database: fetch it with the site
    transport, ``site_extractors.discover`` with the draft resolvers, resolve every candidate (SSRF-guarded, within the live
    limits). Returns ``{ok, streams: [{type, host, quality}], error, candidates, ms, timeout}`` (+ ``placeholder`` / ``frames`` when
    it failed and the page carries a block-placeholder-like iframe / what sits where the player should be); never raises.
    ``site_id`` is the id the site modules are looked up by (``DRAFT_SITE_ID`` for a draft; the heal passes the real site's
    id so a site without a ``resolvers:`` list still finds its own module). ``got`` = the page already fetched (``_fetch_store``
    answer: the availability check read it, so it is not requested twice). ``raw`` (a list) receives the resolved streams in full
    (``url`` / ``type`` / ``request_headers`` ...) for a caller that probes them (the heal's ``stream_blocked`` gate); the returned
    dict never carries them."""
    from ..library import videos
    from ..scraper import site_extractors
    if site_id == DRAFT_SITE_ID:
        site_id = _module_site()   # an edit run: the real id of the edited site
    started = time.monotonic()
    out: dict[str, Any] = {"ok": False, "streams": [], "error": "", "candidates": 0, "timeout": False, "providers": []}
    html = ""
    try:
        try:
            got = got or _fetch_store(locator, cfg.fetch_mode, "", deadline)
        except ApiError as exc:
            out["timeout"] = exc.status_code == 504
            out["error"] = "page: " + str(exc.detail.get("message") if isinstance(exc.detail, dict) else exc.detail)
            return out
        html = got["bundle"].get("initial_html") or got["html"]
        final = got["meta"]["final_url"]
        candidates = site_extractors.discover(site_id, html, final, cfg=cfg)[:MAX_CANDIDATES]
        out["candidates"] = len(candidates)
        if not candidates:
            out["error"] = "no candidates found on the page (the resolvers match nothing here)"
            return out
        load_cookies = _cookie_loader(cfg, final)   # one browser session for all candidates of the page
        run_one = lambda raw: _resolve_one(cfg, raw, final, load_cookies, deadline, site_id)
        outcomes = (_run_within(candidates, run_one, deadline) if _uses_browser(cfg.data)
                    else videos._run_candidates(candidates, run_one))
        seen: set = set()
        problems: list[str] = []
        for outcome in outcomes:
            outcome = outcome or {}
            if outcome.get("streams") and outcome.get("provider") and outcome["provider"] not in out["providers"]:
                out["providers"].append(outcome["provider"])
            for stream in outcome.get("streams") or []:
                if raw is not None and isinstance(stream, dict):
                    raw.append(stream)
                entry = {"type": str(stream.get("type") or ""), "host": (urlsplit(str(stream.get("url") or "")).hostname or "").lower(),
                         "quality": str(stream.get("quality") or "")}
                if tuple(entry.values()) not in seen and len(out["streams"]) < 6:
                    seen.add(tuple(entry.values()))
                    out["streams"].append(entry)
            if not outcome.get("streams") and outcome.get("error") and outcome["error"] not in problems:
                problems.append(outcome["error"])
        out["ok"] = bool(out["streams"])
        if not out["ok"]:
            out["error"] = _clip(("no stream: " + "; ".join(problems[:2])) if problems else "no stream", 300)
    except Exception as exc:
        out["error"] = _clip(trace.short(exc), 300)
    finally:
        out["ms"] = int((time.monotonic() - started) * 1000)
        if not out["ok"] and html:   # what sits where the player should be (a "telif" placeholder is not a broken player)
            try:
                hint = sblocked.placeholder_hint(html)
                if hint:
                    out["placeholder"] = hint
                if not out["candidates"]:
                    out["frames"] = sblocked.frame_sources(html)
            except Exception:
                pass
    return out


def _playable_stage(cfg, items: list, deadline: float, warnings: list, inventories: Optional[list] = None,
                    gate: Optional["_GateRun"] = None) -> dict:
    """Follow up to PLAYABLE_SAMPLES normalized items from their playback page to a stream, one after the other (each at most
    PLAYABLE_SAMPLE_SECONDS, all within the call's time). ``{checked, resolved, skipped, samples[]}``; a sample that no
    longer fits the call is ``skipped`` (not counted by the criterion). ``inventories``: the episode pages of the series pages
    read by ``_series_stage`` (a series card without ``video_sources`` is followed from there, one episode per different series).

    ``gate`` (the draft has ``blocked:`` rules or an ``availability_gate:``): every picked page is judged first; a BLOCKED one
    (``blocked: true`` + ``reason`` on the sample, ``blocked`` N on the block) is left out of ``checked`` / ``resolved`` (it would
    not be taken: it is not a broken player) and replaced by the next pick (<= PLAYABLE_SPARES spares)."""
    out: dict[str, Any] = {"checked": 0, "resolved": 0, "skipped": 0, "samples": []}
    gated = gate is not None and gate.active
    if gated:
        out["blocked"] = 0
    no_public = gated and any(e["kind"] == "series" for e in gate.eliminated) and not any(i.get("episodes") for i in inventories or [])
    picks = _playable_picks(cfg, items, inventories, PLAYABLE_SPARES if gated else 0, skip_series=no_public)
    if not picks:
        warnings.append("playable: every series that was read is blocked content, nothing public to check" if no_public
                        else "playable: no normalized item with a playback address to check")
    for pick in picks:
        if out["checked"] + out["skipped"] >= PLAYABLE_SAMPLES:
            break   # the spares only replace blocked samples
        sample = {"key": pick["key"], "locator": pick["locator"], "kind": pick["kind"], "ok": False, "streams": [], "error": ""}
        now = time.monotonic()
        skip = deadline - now < PLAYABLE_MIN_LEFT
        if not skip:
            sample_deadline = min(deadline, now + PLAYABLE_SAMPLE_SECONDS)
            got = None
            if gated:
                try:
                    verdict = gate.judge_page(pick["locator"], sample_deadline)
                except ApiError:   # 504: the sample's time (or the call) ran out
                    verdict = {"state": "timeout"}
                if verdict["state"] == "timeout":
                    skip = sample_deadline >= deadline - 0.01
                    if not skip:
                        sample["error"] = "page: timeout"
                elif verdict["state"] == "blocked":
                    sample.update(blocked=True, reason=str(verdict.get("reason") or "")[:120], via=verdict.get("via") or "")
                    out["blocked"] += 1
                    if pick["kind"] == "episode":
                        gate.eliminated.append({"url": pick["locator"], "reason": sample["reason"], "via": sample["via"], "kind": "page"})
                    else:
                        gate.record(pick["locator"], "movie", verdict)
                    out["samples"].append(sample)
                    continue
                else:
                    got = gate.pages.get(pick["locator"])
                    if pick["kind"] != "episode":
                        gate.record(pick["locator"], "movie", verdict)
            if not skip and not sample["error"]:
                found = _follow_playback(cfg, pick["locator"], sample_deadline, **({"got": got} if got else {}))
                skip = found["timeout"] and sample_deadline >= deadline - 0.01   # the CALL ran out, not the sample's own time
                if not skip:
                    sample.update({k: found[k] for k in ("ok", "streams", "error", "candidates", "ms")})
                    sample["providers"] = list(found.get("providers") or [])   # who produced the streams (provider recipes' usage)
                    if not sample["ok"]:
                        _player_diagnostic(sample, found, gate, warnings)
        if skip:
            sample.update(skipped=True, error="not checked: the call ran out of time")
            out["skipped"] += 1
        else:
            out["checked"] += 1
            out["resolved"] += 1 if sample["ok"] else 0
        out["samples"].append(sample)
    if out["skipped"]:
        warnings.append(f"playable: {out['skipped']} sample(s) not checked (the call ran out of time); call test_config again "
                        "(playable: true) with page_id and detail_page_id to check them")
    if out.get("blocked"):
        warnings.append(f"playable: {out['blocked']} sample(s) are blocked content (leaving them out of the ratio): "
                        + "; ".join(f"{s['locator']} ({s['reason']})" for s in out["samples"] if s.get("blocked"))[:300])
    return out


def _player_diagnostic(sample: dict, found: dict, gate: Optional["_GateRun"], warnings: list) -> None:
    """``report.diagnostics.player`` for a playable sample that did not resolve, and the advice when the page carries a block
    placeholder while the yaml has no ``blocked:`` rule."""
    entry: dict[str, Any] = {"where": f"playable[{sample['key']}]", "url": sample["locator"], "problem": sample["error"],
                             "candidates": found.get("candidates", 0)}
    if found.get("frames") is not None:
        entry["frames_on_page"] = found["frames"] or ["sayfada iframe yok"]
    placeholder = found.get("placeholder")
    if placeholder:
        entry["placeholder"] = placeholder
        if not (gate is not None and gate.rules):
            text = (f"playable: {sample['locator']}: this page looks like a content-block placeholder (iframe {placeholder['src']!r}): "
                    "write a `blocked:` rule for it (references/blocked.md): content that is not public is not taken")
            if text not in warnings:
                warnings.append(text)
            entry["advice"] = "blocked: kuralı yaz (iframe_src_regex)"
    _diag("player", entry)


def _playable_criteria(block: Optional[dict], norm: Optional[dict], series_block: Optional[dict] = None) -> dict:
    """``playable_ratio`` (resolved / checked samples; 2 of 3 passes: the ratio is judged at two decimals) and, when the
    list has series items, ``series_have_episode_sources``: series items that carry episode ``video_sources`` OR (the better of
    the two) the share of the series pages the yaml ``series_page`` gave episodes for (``series_block`` of ``_series_stage``)."""
    checked = (block or {}).get("checked") or 0
    resolved = (block or {}).get("resolved") or 0
    out = {"playable_ratio": _criterion(round(resolved / checked, 2) if checked else 0.0, MIN_PLAYABLE_RATIO, "min")}
    series = (norm or {}).get("episode_items") or 0
    if series:
        covered = series - ((norm or {}).get("series_without_sources") or 0)
        value = _ratio(covered, series)
        inventoried = (series_block or {}).get("checked") or 0
        if inventoried:
            value = max(value, _ratio((series_block or {}).get("with_episodes") or 0, inventoried))
        out["series_have_episode_sources"] = _criterion(value, MIN_SERIES_SOURCE_RATIO, "min")
    return out


# --- series inventory (``playable: true``) -----------------------------------------------------------------------

def _check_series_page(data: dict) -> list[str]:
    """Problems of the yaml ``series_page:`` block (``series_generic.validate_spec``); [] when there is none."""
    if not data.get("series_page"):
        return []
    return [f"series_page: {message}" for message in series_generic.validate_spec(data["series_page"])]


def _series_candidates(cfg, items: list, directory=None) -> list[dict]:
    """The DISTINCT normalized series items of the list as ``{key, url, sources, card_url, resolution, how, page_key}``: ``url`` = the page the
    library reads the inventory from (``series_crawl.series_url``: ``source_url``, else the key), ``sources`` = the card already carries
    episode ``video_sources``. With a series-page ``directory`` (``_series_directory``) a card that links an EPISODE page is resolved
    like a scan does (``library/series_dir.ensure``): ``url`` is then the series page, ``card_url`` where the card linked, ``resolution``
    ``ok`` | ``resolved`` | ``unknown`` (no series page known) | ``n/a``, ``how`` the match step, ``page_key`` the series page's own key."""
    from ..library import normalize as nrm, series_dir
    rules = cfg.data["normalize"]
    base = cfg.base_url.rstrip("/") + "/"
    out: list[dict] = []
    seen: set = set()
    for raw in items:
        norm = nrm.generic_normalize(rules, raw, base_url=cfg.base_url)
        if not norm or norm.get("type") != "series":
            continue
        key = str(norm.get("source_key") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        card_url = urljoin(base, str(norm.get("source_url") or key))
        resolution = series_dir.ensure(cfg, norm, directory, site_id=_module_site()) if directory is not None else "n/a"
        found = series_dir.card_resolution(norm) or {}
        out.append({"key": key, "url": urljoin(base, str(norm.get("source_url") or key)), "sources": bool(norm.get("video_sources")),
                    "card_url": card_url, "resolution": resolution, "how": found.get("how"), "page_key": found.get("key")})
    return out


def _episode_ref(entry: dict) -> dict:
    return {"season": entry.get("season"), "episode": entry.get("episode"), "url": _clip(entry.get("url") or "", 200)}


def _series_sample(cfg, spec: dict, pick: dict, deadline: float, warnings: list,
                   gate: Optional["_GateRun"] = None) -> tuple[dict, list]:
    """One series page read with ``series_page``: ``(sample, episode entries)``; ``skipped`` when the call ran out of time,
    ``blocked`` (+ ``reason``) when a ``blocked:`` rule ``on: series_page`` matches the page (the series would not be taken).
    ``diagnostics`` = what the engine extracted from the first rows and why a row was rejected (generic engine only)."""
    from ..scraper import site_extractors
    sample: dict[str, Any] = {"key": pick["key"], "series_url": pick["url"], "episodes": 0, "seasons": 0, "structured": False,
                              "first": None, "last": None, "season_pages": 0, "warnings": [], "error": "", "blocked": False}
    now = time.monotonic()
    if deadline - now < PLAYABLE_MIN_LEFT:
        sample.update(skipped=True, error="not checked: the call ran out of time")
        return sample, []
    sample_deadline = min(deadline, now + SERIES_SAMPLE_SECONDS)
    entries: list[dict] = []
    try:
        got = _fetch_store(pick["url"], cfg.fetch_mode, "", sample_deadline)
        if gate is not None and gate.series_rules:
            hit = sblocked.match(got["html"], gate.series_rules, "series_page")
            if hit:
                sample.update(blocked=True, reason=hit["reason"], via="rule")
                return sample, []
        inventory = site_extractors.series_inventory(_module_site(), got["html"], got["meta"]["final_url"], spec)
    except ApiError as exc:
        if exc.status_code == 504 and sample_deadline >= deadline - 0.01:   # the CALL ran out, not the sample's own time
            sample.update(skipped=True, error="not checked: the call ran out of time")
            return sample, []
        sample["error"] = "page: " + str(exc.detail.get("message") if isinstance(exc.detail, dict) else exc.detail)
        return sample, []
    except Exception as exc:
        sample["error"] = _clip(f"inventory failed ({type(exc).__name__}: {exc})", 300)
        return sample, []
    entries = [e for e in inventory.get("video_sources") or [] if isinstance(e, dict) and e.get("url")]
    sample.update(episodes=len(entries), seasons=len({e.get("season") for e in entries}),
                  structured=bool(inventory.get("structured")), season_pages=len(inventory.get("season_pages") or []),
                  first=_episode_ref(entries[0]) if entries else None, last=_episode_ref(entries[-1]) if entries else None,
                  warnings=[_clip(w, 600) for w in (inventory.get("warnings") or [])[:4]])
    if isinstance(inventory.get("diagnostics"), dict):
        sample["diagnostics"] = _cap_entry(inventory["diagnostics"])
    return sample, entries


def _series_stage(cfg, items: list, spec_errors: list, deadline: float, warnings: list,
                  gate: Optional["_GateRun"] = None, extra_rows: Optional[list] = None, directory=None,
                  ingest_sample: bool = False) -> tuple[Optional[dict], list]:
    """Read the series pages of up to SERIES_SAMPLES DIFFERENT normalized series items with the yaml ``series_page`` (the
    engine of the real crawl, ``site_extractors.series_inventory``; in memory, nothing is written). Returns ``(block,
    inventories)``: ``block`` = ``{checked, with_episodes, skipped, samples[{key, series_url, episodes, seasons, structured,
    first, last, season_pages, warnings, error, blocked, diagnostics}]}`` (+ ``blocked`` N and ``hint`` when the list has series
    cards without episode sources and the yaml has no ``series_page``; None when the list has no series item), ``inventories`` =
    ``[{key, url, episodes[{season, episode, url}], ok_page}]`` for the playable stage. A sample that no longer fits the call is
    ``skipped`` (not counted).

    ``extra_rows`` (raw rows of the collections) join the list's as candidates; ``directory`` (``_series_directory``) resolves a card that
    links an EPISODE page to its series page like a scan does (a sample then carries ``resolution`` / ``how`` / ``card_url``; a card with no
    series page known is not read). ``ingest_sample`` (a hardened new site): up to SERIES_READS pages are read and the block gets
    ``ingest_sample`` = ``{items, judged, ok, read, resolved, unknown, reasons{}, samples[]}`` (``_ingest_sample_block``).

    ``gate`` (the draft has ``blocked:`` rules / an ``availability_gate:``): each series is judged like production does (a
    ``series_page`` rule on its page; the newest + oldest episode pages against the ``episode_page`` rules / the gate): a BLOCKED
    series is left out (``blocked: true`` + ``reason`` on its sample, not counted in ``checked``), and up to SERIES_SPARES more
    series pages are read to replace them."""
    candidates = _series_candidates(cfg, list(items) + list(extra_rows or []), directory)
    readable = [c for c in candidates if c["resolution"] != "unknown"]   # no series page known = nothing to read (the ingest sample counts it)
    spec = cfg.data.get("series_page")
    block: dict[str, Any] = {"checked": 0, "with_episodes": 0, "skipped": 0, "samples": []}
    if not spec:
        if any(not c["sources"] for c in candidates):
            block["hint"] = SERIES_HINT
            return block, []
        return None, []
    if not candidates:
        warnings.append("series_page: the list has no series item to check it on (normalize type: series)")
        return block, []
    if spec_errors:
        return block, []   # the errors of the report say what is wrong; nothing to read
    inventories: list[dict] = []
    gated = gate is not None and gate.active

    def read(pick: dict, spare: bool = False) -> None:
        sample, entries = _series_sample(cfg, spec, pick, deadline, warnings, gate)
        if pick["resolution"] != "n/a":
            sample.update(resolution=pick["resolution"], how=pick["how"], card_url=_clip(pick["card_url"], 200))
        if spare:
            sample["spare"] = True
        block["samples"].append(sample)
        if sample.get("skipped"):
            block["skipped"] += 1
            return
        if sample.get("blocked"):   # a series_page rule: not public, left out of the sampling
            block["blocked"] = block.get("blocked", 0) + 1
            gate.record(pick["url"], "series", {"state": "blocked", "reason": sample["reason"], "via": "rule", "probed": 0})
            return
        ok_page = None
        if gated and entries and not sample["error"]:
            try:
                verdict = gate.judge_series(entries, deadline)
            except ApiError:   # 504: the call ran out of time while the pages were judged
                sample.update(skipped=True, error="not checked: the call ran out of time")
                block["skipped"] += 1
                return
            sample["gate"] = {"state": verdict["state"], "reason": str(verdict.get("reason") or "")[:120], "probed": verdict["probed"]}
            gate.record(pick["url"], "series", verdict)
            if verdict["state"] == "blocked":
                sample.update(blocked=True, reason=sample["gate"]["reason"], via=verdict.get("via") or "")
                block["blocked"] = block.get("blocked", 0) + 1
                return
            if verdict["state"] == "retry":
                warnings.append(f"series_page: could not judge the episode pages of {pick['url']} ({verdict['reason']}); try again")
            ok_page = next((p["url"] for p in verdict["pages"] if p["state"] == "ok"), None)
        block["checked"] += 1
        block["with_episodes"] += 1 if sample["episodes"] else 0
        diagnostics = sample.get("diagnostics") or {}
        if sample["error"]:
            warnings.append(f"series_page: series page not available ({sample['error']}): {pick['url']}")
        elif not sample["episodes"]:
            why = f" ({sample['warnings'][0]})" if sample["warnings"] else ""
            warnings.append(f"series_page: no episode found on {pick['url']}{why}; check row_selector / episode_url_regex "
                            "against this page with query_html")
            _diag("series", {"where": "series_page", "series_url": pick["url"], "problem": "bölüm bulunamadı", **diagnostics})
        else:
            if not sample["structured"]:
                rejected = next((r.get("rejected_by") for r in diagnostics.get("first_rows") or [] if r.get("rejected_by")), "")
                if diagnostics.get("rows_matched"):   # the row selector is right: the rows were not accepted as episodes
                    warnings.append(f"series_page: row_selector matched {diagnostics['rows_matched']} row(s) on {pick['url']} but none was "
                                    f"accepted as an episode" + (f" ({rejected})" if rejected else "") + "; the episodes were found by "
                                    "scanning every link (structured=false). The row_selector is fine: fix fields.url / episode_url_regex")
                else:
                    sits = diagnostics.get("episode_links_sit_in")
                    warnings.append(f"series_page: row_selector matched 0 elements on {pick['url']}; the episodes were found by scanning "
                                    "every link (structured=false): fix row_selector"
                                    + (f" (the episode links sit in: {', '.join(sits)})" if sits else ""))
                _diag("series", {"where": "series_page", "series_url": pick["url"],
                                 "problem": "satırlar bölüm olarak kabul edilmedi" if diagnostics.get("rows_matched") else "row_selector 0 eleman eşledi",
                                 **diagnostics})
            inventories.append({"key": pick["key"], "url": pick["url"], "ok_page": ok_page, "episodes": [
                {"season": e.get("season"), "episode": e.get("episode"), "url": e["url"]} for e in entries]})

    order = _spread(readable, SERIES_READS if ingest_sample else SERIES_SAMPLES)
    for pick in order:
        read(pick)
    if gated:   # blocked series are replaced by other series (the playable ratio is judged over series that would be taken)
        spares = _spread([c for c in readable if c not in order], SERIES_SPARES)
        used = 0
        while spares and used < SERIES_SPARES and block.get("blocked", 0) > used:
            read(spares.pop(0), spare=True)
            used += 1
    if ingest_sample:
        block["ingest_sample"] = _ingest_sample_block(candidates, block, warnings)
    elif candidates and not readable:
        warnings.append(f"series_page: none of the {len(candidates)} series card(s) links a series page and none resolves to one "
                        "(series_page_unknown): see references/series-page.md, 'Series directory'")
    if block["skipped"]:
        warnings.append(f"series: {block['skipped']} series page(s) not checked (the call ran out of time); call test_config "
                        "again (playable: true) to check them")
    return block, inventories


def _why_empty(sample: dict) -> str:
    """Why a series page read gave no episode, as one of the sample reasons: ``not a series page`` (``series_url_regex`` does not match the
    page), ``same_series`` (every row was taken for another series' episode: ``series_slug_regex`` / ``same_series_regex``), else
    ``empty_inventory`` (the row selector / episode regex found nothing)."""
    warns = " ".join(str(w) for w in sample.get("warnings") or [])
    if "dizi sayfası değil" in warns:
        return "not a series page"
    if "bu diziye ait sayılmadı" in warns or "başka diziye ait" in warns:
        return "same_series"
    return "empty_inventory"


def _ingest_sample_block(candidates: list, block: dict, warnings: list) -> dict:
    """The ingest sample of ``_series_stage``: up to INGEST_SAMPLE_ITEMS distinct series items (list + collections, episode-card ones
    included), each as production would write it (key + title cleanup, series page resolved through the directory) and, when its series
    page was read in this call, whether the page gave an inventory. Per item ``state``: ``ok`` (page read, episodes found) | ``resolvable``
    (resolves to a series page; the page was not among the reads) | ``fail`` + ``reason`` (``series_page_unknown`` | ``not a series page`` |
    ``same_series`` | ``empty_inventory`` | ``page_error``) | ``skipped`` / ``blocked`` (not judged). ``key_mismatch`` = the card's key
    differs from the series page's own key (the two records are one title only through TMDB). ``judged`` / ``ok`` feed ``ingest_sample_ok``."""
    picks = _spread(candidates, INGEST_SAMPLE_ITEMS)
    reads = {s["series_url"]: s for s in block["samples"]}
    out: dict[str, Any] = {"items": len(picks), "judged": 0, "ok": 0, "read": 0, "resolved": 0, "unknown": 0, "reasons": {}, "samples": []}
    for c in picks:
        row: dict[str, Any] = {"key": c["key"], "series_url": _clip(c["url"], 150), "resolution": c["resolution"]}
        if c["url"] != c["card_url"]:
            row["card_url"] = _clip(c["card_url"], 150)
        if c["how"]:
            row["how"] = c["how"]
        if c["page_key"] and c["page_key"] != c["key"]:
            row["key_mismatch"] = _clip(c["page_key"], 100)
            out["reasons"]["key_mismatch"] = out["reasons"].get("key_mismatch", 0) + 1
        sample = reads.get(c["url"])
        reason = None
        if c["resolution"] == "unknown":
            reason = "series_page_unknown"
            out["unknown"] += 1
        elif sample is not None and (sample.get("skipped") or sample.get("blocked")):
            row["state"] = "skipped" if sample.get("skipped") else "blocked"
            out["samples"].append(row)
            continue
        elif sample is not None:
            out["read"] += 1
            if sample.get("error"):
                reason = "page_error"
            elif not sample.get("episodes"):
                reason = _why_empty(sample)
        if c["resolution"] == "resolved":
            out["resolved"] += 1
        out["judged"] += 1
        if reason is None:
            out["ok"] += 1
            row["state"] = "ok" if sample is not None else "resolvable"
        else:
            row["state"], row["reason"] = "fail", reason
            out["reasons"][reason] = out["reasons"].get(reason, 0) + 1
        out["samples"].append(row)
    failing = [r for r in out["samples"] if r.get("state") == "fail"]
    if failing:
        shown = failing[0]
        warnings.append(f"series: {len(failing)} of {out['judged']} series item(s) of the ingest sample have no readable series page "
                        f"({', '.join(f'{n}x {why}' for why, n in out['reasons'].items() if why != 'key_mismatch')}); e.g. {shown['key']!r}: "
                        f"{shown['reason']} ({shown.get('card_url') or shown['series_url']})")
        for row in failing[:4]:
            _diag("series", {"where": "ingest_sample", "problem": row["reason"], "key": row["key"], "card_url": row.get("card_url") or row["series_url"],
                             "series_url": row["series_url"], "key_mismatch": row.get("key_mismatch"),
                             "advice": _INGEST_ADVICE.get(row["reason"], "")})
    return out


_INGEST_ADVICE = {
    "series_page_unknown": ("bu kart bir BÖLÜM sayfasına gidiyor ve sitenin dizi listesinde (list / koleksiyon satırları) karşılığı bulunamadı: "
                            "dizi arşivini (alfabetik liste, 'Diziler' menüsü) list_url ya da bir koleksiyon yap, ya da kart başlığı / "
                            "normalize.key.regex'i arşivdeki dizi adı / slug'ıyla eşleşecek şekilde düzelt"),
    "not a series page": "series_page.series_url_regex bu sayfayı dizi sayfası saymıyor: regex'i dizi sayfalarının yoluna göre düzelt",
    "same_series": "satırların hepsi 'başka diziye ait' sayıldı: series_page.series_slug_regex / same_series_regex'i bölüm bağlantılarının biçimine göre düzelt",
    "empty_inventory": "dizi sayfasında bölüm bulunamadı: series_page.row_selector / fields.url / episode_url_regex'i bu sayfaya göre düzelt",
    "page_error": "dizi sayfası okunamadı (ağ / engel): fetch_mode'u ve adresi kontrol et",
}


def _ingest_sample_criterion(sample: Optional[dict]) -> dict:
    """``ingest_sample_ok`` (hardened new site, a generic ``series_page`` + series items): the share of the judged series items of the ingest
    sample that resolve to a series page with a non-empty episode inventory (>= MIN_INGEST_SAMPLE_OK). Nothing judged = no criterion."""
    if not isinstance(sample, dict) or not sample.get("judged"):
        return {}
    return {"ingest_sample_ok": _criterion(_ratio(sample["ok"], sample["judged"]), MIN_INGEST_SAMPLE_OK, "min")}


def _series_criteria(block: Optional[dict], has_spec: bool) -> dict:
    """``series_inventory_ok`` (only when the yaml has a ``series_page``): the share of the checked series pages that gave at
    least one episode (all of them must; a page that was not read counts as 0)."""
    if not has_spec:
        return {}
    checked = (block or {}).get("checked") or 0
    return {"series_inventory_ok": _criterion(_ratio((block or {}).get("with_episodes") or 0, checked) if checked else 0.0,
                                              MIN_SERIES_INVENTORY_RATIO, "min")}


# --- live search (``search:``) ----------------------------------------------------------------------------------

def _search_mod():
    """The generic search engine (``scraper/search_generic.py``); imported lazily (tests swap it)."""
    from ..scraper import search_generic
    return search_generic


def _has_search(data: dict) -> bool:
    return data.get("search") is not None


def _check_search(data: dict) -> list[str]:
    """Static problems of the yaml ``search:`` block (``search_generic.validate_spec``); [] when the block is absent."""
    if not _has_search(data):
        return []
    spec = data["search"]
    if not isinstance(spec, dict):
        return ["search: must be a mapping (url, method, format, row_selector, fields, ...)"]
    try:
        problems = _search_mod().validate_spec(spec, str(data.get("base_url") or "") or None)
    except Exception as exc:
        return [f"search: cannot validate ({type(exc).__name__}: {exc})"]
    return [m if str(m).startswith("search") else f"search: {m}" for m in problems]


def _url_key(base: str, url: Any) -> Optional[tuple]:
    """A detail URL in comparable form (host without ``www.``, path without the trailing slash, query); None when unusable."""
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        part = urlsplit(urljoin(base, url.strip()))
    except ValueError:
        return None
    host = (part.hostname or "").lower()
    return (host[4:] if host.startswith("www.") else host, part.path.rstrip("/") or "/", part.query)


def _norm_keys(data: dict, items: list) -> Optional[list]:
    """``source_key`` of every item under the draft's ``normalize`` rules (None for a rejected one); None when the draft has no
    usable normalize block (the list part of the draft reports that)."""
    rules = data.get("normalize")
    if not isinstance(rules, dict) or not rules:
        return None
    try:
        from ..library import normalize as nrm
        if nrm.validate_rules(rules):
            return None
        out = []
        for item in items:
            try:
                norm = nrm.generic_normalize(rules, item, base_url=str(data.get("base_url") or ""))
            except Exception:
                norm = None
            out.append((norm or {}).get("source_key") or None)
        return out
    except Exception:
        return None


def _known_item(items: list, query: Optional[str]) -> tuple[Optional[dict], Optional[str]]:
    """(the list item the search is judged against, the query). No ``query``: the first list item with a usable title and its
    title is the query. A given ``query``: the first item whose title contains it (None when there is none: not judged)."""
    usable = [i for i in items if isinstance(i, dict) and _filled(i.get("title")) and _filled(i.get("detail_url"))]
    if query is None:
        for item in usable:
            title = " ".join(str(item["title"]).split())[:SEARCH_QUERY_MAX]
            if len(title) >= 3:
                return item, title
        return None, None
    wanted = " ".join(query.split()).casefold()
    return next((i for i in usable if wanted and wanted in " ".join(str(i["title"]).split()).casefold()), None), query


#: episode / season phrases a card title carries that a SERIES search must not (the site's search finds the series by its name): the title is
#: cut at the first of them. ("Final" only after other words: ``Final Destination`` stays.)
_QUERY_CUTS = tuple(re.compile(p, re.I) for p in (
    r"\s*\b\d+\s*\.?\s*b[öo]l[üu]m\b.*$",       # 37.Bölüm, 37. Bölüm Full izle
    r"\s*\bs\d+\s*e\d+\b.*$",                    # S04E10
    r"\s*\b\d+\s*\.?\s*sezon\b.*$",             # 2. Sezon
    r"\s+final(?:i)?\b.*$",                         # ... Final / Finali
))


def search_query(title: Any, site_names=()) -> str:
    """The live-search query of a list item title (``test_config`` / ``test_search`` only; a runtime user query goes as typed): the SERIES name
    without episode / season phrases (``Halef 37.Bölüm`` -> ``Halef``, ``Dizi S04E10`` -> ``Dizi``), site litter (`` izle``, `` HD``, ``| Site``
    when the part after the bar is one of ``site_names``) and a trailing number + bölüm gone; the title itself when less than 3 characters
    would be left."""
    from ..library import normalize as nrm
    text = " ".join(str(title or "").split())
    out = nrm.clean_title(text, series=True, site_names=site_names)
    for cut in _QUERY_CUTS:
        out = cut.sub("", out)
    out = " ".join(out.split()).strip(" -\u2013\u2014:|,.")
    return (out if len(out) >= 3 else text)[:SEARCH_QUERY_MAX]


def _site_names_of(data: dict) -> list:
    """The folded names the draft's site goes by (``normalize.clean_title`` strips a ``| <Site>`` suffix that names it)."""
    from ..library import normalize as nrm
    rules = data.get("normalize")
    return nrm._site_names(str(data.get("base_url") or ""), rules.get("host") if isinstance(rules, dict) else None)


def _first_words(query: str, n: int = 2) -> str:
    return " ".join(query.split()[:n])


def _series_directory(data: dict, rows: list):
    """The series-page directory (``library/series_dir.py``) of the draft out of raw list / collection rows, in memory; None when it does
    not apply (no generic ``series_page`` with ``series_url_regex``, a site with a code module, no usable ``normalize``)."""
    from ..library import normalize as nrm, series_dir
    rules = data.get("normalize")
    try:
        cfg = _draft_cfg(data)
        if series_dir.spec_of(cfg, _module_site()) is None or not isinstance(rules, dict) or nrm.validate_rules(rules):
            return None
        norms = []
        for raw in rows:
            try:
                norm = nrm.generic_normalize(rules, raw, base_url=cfg.base_url)
            except Exception:
                norm = None
            if norm:
                norms.append(norm)
        return series_dir.build(cfg, norms, _module_site())
    except Exception:
        return None


def _resolved_to_series(data: dict, results: list, directory) -> int:
    """How many search results are a series page (or a card the directory resolves to one)."""
    from ..library import normalize as nrm, series_dir
    cfg = _draft_cfg(data)
    count = 0
    for raw in results:
        try:
            norm = nrm.generic_normalize(data["normalize"], raw, base_url=cfg.base_url)
        except Exception:
            norm = None
        if norm and norm.get("type") == "series" and series_dir.ensure(cfg, norm, directory, site_id=_module_site()) in ("ok", "resolved"):
            count += 1
    return count


def _run_search(data: dict, query: str, known: Optional[dict], extra_providers: Optional[list] = None, directory=None) -> dict:
    """ONE query with the draft's ``search:`` spec, in memory (nothing is written; the engine checks every URL with netguard and
    keeps to the site's host). ``{query, count, samples, found_known, known, normalize_ok_ratio, ms, error?}`` + ``resolved_to_series``
    (results that are a series page or resolve to one through ``directory``; None without a directory)."""
    base = str(data.get("base_url") or "")
    block: dict[str, Any] = {"query": query, "count": 0, "samples": [], "found_known": None, "known": None,
                             "normalize_ok_ratio": None, "ms": 0, "resolved_to_series": None}
    if known is not None:
        block["known"] = {"title": _clip(known.get("title")), "detail_url": _clip(known.get("detail_url"), 300)}
    started = time.monotonic()
    results: list = []
    try:
        got = _search_mod().search(_draft_cfg(data, extra_providers), query, SEARCH_LIMIT)
        results = [r for r in (got or []) if isinstance(r, dict) and _filled(r.get("title")) and _filled(r.get("detail_url"))]
    except Exception as exc:   # SearchError (short text) or anything else: the stage reports it, the call goes on
        block["error"] = _clip(" ".join(str(exc).split()) or type(exc).__name__, 300)
    block["ms"] = int((time.monotonic() - started) * 1000)
    host = _url_key(base, base)
    if host:   # the engine keeps to the site's host; a result that does not is not counted (defence in depth)
        results = [r for r in results if (_url_key(base, r["detail_url"]) or ("",))[0] == host[0]]
    block["count"] = len(results)
    block["samples"] = [{k: (_clip(r.get(k), 300) if _filled(r.get(k)) else None) for k in ("title", "detail_url", "poster_url", "year")}
                        for r in results[:SEARCH_SAMPLES]]
    keys = _norm_keys(data, results) if results else None
    if keys:
        block["normalize_ok_ratio"] = _ratio(sum(1 for k in keys if k), len(keys))
    if known is not None:
        want = _url_key(base, known.get("detail_url"))
        found = any(want is not None and _url_key(base, r["detail_url"]) == want for r in results)
        if not found and results:   # the same title may be linked by another URL shape: compare the normalize keys
            known_key = (_norm_keys(data, [known]) or [None])[0]
            found = bool(known_key) and known_key in (keys or [])
        block["found_known"] = bool(found)
    if directory is not None and results:
        try:
            block["resolved_to_series"] = _resolved_to_series(data, results, directory)
        except Exception:
            block["resolved_to_series"] = None
    return block


def _search_with_fallback(data: dict, query: str, known: Optional[dict], extra_providers: Optional[list] = None, directory=None) -> dict:
    """``_run_search`` and, when it gave 0 results for a query of more than two words, ONE more run with its first two words (a site search
    often matches the start of a name only). ``query_used`` = the query the answer is from, ``fallback_used`` = it is the second one."""
    block = _run_search(data, query, known, extra_providers, directory)
    block["query_used"], block["fallback_used"] = query, False
    short = _first_words(query)
    if not block.get("error") and not block["count"] and len(query.split()) > 2 and len(short) >= 3:
        second = _run_search(data, short, known, extra_providers, directory)
        if not second.get("error"):
            second["query"], second["query_used"], second["fallback_used"] = query, short, True
            return second
    return block


def _search_stage(data: dict, items: list, deadline: float, errors: list, warnings: list,
                  extra_providers: Optional[list] = None, directory=None) -> dict:
    """The search stage of ``test_config(playable: true)`` / ``submit``: ONE query (the SERIES name of the first list item: a card title
    like ``Halef 37.Bölüm`` is cut to ``Halef``, ``search_query``; 0 results = one more try with its first two words) against the live
    search of the draft. A failed query is an error; no time left / no list item = ``skipped`` (a warning, not judged). ``directory`` =
    the series-page directory (``_series_directory``): the block's ``resolved_to_series`` counts the results that resolve to a series page."""
    known, query = _known_item(items, None)
    if query is None:
        warnings.append("search: not checked (no list item with a title to take the query from)")
        return {"skipped": "no list item to take a query from"}
    if deadline - time.monotonic() < SEARCH_NEED:
        warnings.append("search: not checked (the call ran out of time); call test_config again (playable: true) or test_search")
        return {"skipped": "the call ran out of time"}
    query = search_query(query, _site_names_of(data))
    block = _search_with_fallback(data, query, known, extra_providers, directory)
    if block.get("error"):
        errors.append(f"search: query {query!r} failed ({block['error']})")
    elif not block["count"]:
        warnings.append(f"search: query {query!r} gave no result")
    elif block["found_known"] is False:
        warnings.append(f"search: {block['count']} result(s) for {query!r} but not the title of the list item "
                        f"({block['known']['detail_url']}); check the result selectors / the detail_url of the search")
    _search_diagnostic(data, block)
    return block


def _search_diagnostic(data: dict, block: dict) -> None:
    """``report.diagnostics.search``: a live query that failed, gave nothing or did not find the known title, with the spec in play
    (url / method / format / row selector) and what the results looked like."""
    if not (block.get("error") or not block.get("count") or block.get("found_known") is False):
        return
    spec = data.get("search") if isinstance(data.get("search"), dict) else {}
    entry: dict[str, Any] = {"where": "search", "query": block.get("query"),
                             "spec": {k: spec.get(k) for k in ("url", "method", "format", "fetch", "row_selector", "results_path") if spec.get(k)}}
    if block.get("error"):
        entry["problem"] = block["error"]
        entry["advice"] = "aynı sorguyu sitenin arama sayfasında elle dene (fetch_page); URL / method / form alanları ve yanıt biçimi (html|json) doğru mu?"
    elif not block.get("count"):
        entry["problem"] = "sorgu 0 sonuç verdi"
        entry["advice"] = ("yanıt geldi ama satırlar okunamadı olabilir: row_selector / results_path'i bir arama yanıtına göre doğrula; "
                           "ya da sorgu sitede gerçekten sonuçsuz")
    else:
        entry["problem"] = "sonuçlar var ama listedeki başlık bulunamadı"
        entry["known_detail_url"] = (block.get("known") or {}).get("detail_url")
        entry["results"] = [s.get("detail_url") for s in (block.get("samples") or [])[:3]]
        entry["advice"] = "sonuç bağlantısı (detail_url) biçimi listedekinden farklı olabilir: fields.detail_url'i karşılaştır"
    _diag("search", entry)


def _search_criteria(data: dict, block: Optional[dict]) -> dict:
    """``search_ok`` (only when the yaml has ``search:``): the live query gave at least one result and the title it was taken from
    is among them (``found_known`` not false). An invalid block counts 0; a stage that was skipped is not judged."""
    if not _has_search(data):
        return {}
    if _check_search(data):
        return {"search_ok": _criterion(0, 1, "min")}
    if block is None or block.get("skipped"):
        return {}
    ok = not block.get("error") and block.get("count", 0) >= 1 and block.get("found_known") is not False
    return {"search_ok": _criterion(int(ok), 1, "min")}


def _do_test_search(body, *, deadline: float) -> dict:
    """``POST /test_search``: the draft's ``search:`` run on ONE query, in memory. ``query`` omitted = the first title of the draft's list
    page (``page_id`` or fetched); that list item's ``detail_url`` (or its normalize key) must be among the results (``found_known``)."""
    errors: list[str] = []
    warnings: list[str] = []
    out: dict[str, Any] = {"valid": False, "errors": errors, "warnings": warnings, "query": None, "count": 0, "samples": [],
                           "found_known": None, "known": None, "normalize_ok_ratio": None, "ms": 0,
                           "query_used": None, "fallback_used": False, "resolved_to_series": None}
    data, problem = _load_yaml(body.yaml_text)
    if problem:
        errors.append(problem)
        return out
    errors += [e for e in _check_core(data)[0] if e.startswith("base_url")]
    if not _has_search(data):
        errors.append("search: the yaml has no `search:` block (see references/search.md)")
    errors += _check_search(data)
    if errors:
        return out
    query = " ".join(str(body.query or "").split())[:SEARCH_QUERY_MAX] or None
    if query is not None and len(query) < 3:
        errors.append("query: at least 3 characters (the live search ignores shorter ones)")
        return out
    if query is not None:   # a given query is cleaned like a list title (episode / season phrases, site litter): a series is searched by its name
        query = search_query(query, _site_names_of(data))
    cfg = _draft_cfg(data)
    items: list = []
    if query is None or body.page_id:   # the known item: a stored list page, or (no query given) the draft's own list page
        try:
            html = _load_page(body.page_id)[0] if body.page_id else _fetch_store(
                urljoin(cfg.base_url, cfg.list_url or "/"), cfg.fetch_mode, cfg.row_selector, deadline)["html"]
            _block, items, _fill = _list_stage(cfg, html, [], [])
        except ApiError as exc:
            if query is None and not body.detail_page_id:
                errors.append("query: none given and the list page is not available "
                              f"({exc.detail.get('message') if isinstance(exc.detail, dict) else exc.detail}); pass a query or page_id")
                return out
            warnings.append("known title: the list page is not available")
    if not items and body.detail_page_id:   # a stored detail page is a known title too (its own URL)
        try:
            html, meta = _load_page(body.detail_page_id)
            tree = HTMLParser(html)
            node = tree.css_first('meta[property="og:title"]')
            title = (node.attributes.get("content") if node is not None else "") or ""
            if not title.strip():
                node = tree.css_first("h1") or tree.css_first("title")
                title = _text(node) if node is not None else ""
            items = [{"title": " ".join(title.split()), "detail_url": meta.get("final_url") or meta.get("url") or ""}]
        except ApiError:
            warnings.append("known title: the detail page is not available")
    known, found_query = _known_item(items, query)
    if found_query is None:
        errors.append("query: none given and no list item with a title to take it from; pass a query")
        return out
    if query is None:
        found_query = search_query(found_query, _site_names_of(data))
    _check_deadline(deadline)
    out.update(_search_with_fallback(data, found_query, known, None, _series_directory(data, items)))
    if out.get("error"):
        errors.append(f"search: query {found_query!r} failed ({out['error']})")
    elif not out["count"]:
        warnings.append(f"search: query {found_query!r} gave no result")
    elif known is None:
        warnings.append("found_known: not judged (no list item whose title contains the query); try the title of a list item")
    elif out["found_known"] is False:
        warnings.append(f"found_known: false: the list item {out['known']['detail_url']} is not among the {out['count']} result(s)")
    out["valid"] = not errors
    return out


def _collection_criteria(pairs: list) -> dict:
    """``passed`` rows of the collections: the least item count and the least normalize share over the collections that
    were checked (a skipped one is not judged; an unreadable one counts as 0)."""
    checked = [entry for _spec, entry in pairs if entry["status"] != "skipped"]
    if not checked:
        return {}
    return {"collections_valid_count": _criterion(min(e["valid_count"] for e in checked), MIN_COLLECTION_COUNT, "min"),
            "collections_normalize_ok_ratio": _criterion(min(e["normalize_ok_ratio"] for e in checked),
                                                         MIN_NORMALIZE_OK_RATIO, "min")}


# --- hardening criteria of a new site (``harden``) ----------------------------------------------------------------

def harden_enabled() -> bool:
    """Are the hardening criteria on? ``ONBOARD_HARDEN`` (default on; ``0`` / ``false`` / ``no`` / ``off`` = a kill switch: a new site is then
    judged by the older criteria only); read at call time. The test sandbox (``tests/_sandbox.py``) switches it off so the older suites keep
    exercising their own subject; ``tests/test_onboard_harden.py`` switches it on."""
    return os.environ.get("ONBOARD_HARDEN", "1").strip().lower() not in ("0", "false", "no", "off")


def _skip_names(skipped: Any) -> set:
    """``skipped`` (the draft's ``skipped_fields``: what the admin answered "Sitede yok, atla" for) as lower-case names."""
    return {str(f).strip().lower() for f in (skipped if isinstance(skipped, (list, tuple, set)) else []) if str(f).strip()}


def _draft_memory(draft_id: Optional[str]) -> tuple[list, Optional[dict]]:
    """What a stored draft remembers for the hardening criteria: (its ``skipped_fields`` = the admin's "Sitede yok, atla" answers, the report
    of its LAST submission, for ``removed_fields``); ``([], None)`` for a repair job / unknown draft."""
    draft = onboard_store.get_draft(draft_id) if draft_id and onboard_store.valid_draft_id(draft_id) else None
    raw = (draft or {}).get("skipped_fields")
    report = (draft or {}).get("report")
    return ([str(f).strip().lower() for f in (raw if isinstance(raw, list) else []) if str(f).strip()],
            report if isinstance(report, dict) else None)


def _produces_series(data: dict, norm: Optional[dict]) -> bool:
    """Does the yaml make SERIES titles: a normalized series item on the list, else ``normalize.type`` that can say ``series``."""
    if ((norm or {}).get("types") or {}).get("series"):
        return True
    rules = data.get("normalize")
    kind = rules.get("type") if isinstance(rules, dict) else None
    if kind == "series":
        return True
    if isinstance(kind, dict):
        mapping = kind.get("map")
        return kind.get("default") == "series" or (isinstance(mapping, dict) and "series" in mapping.values())
    return False


def _episode_cards(norm: Optional[dict], pairs: list) -> int:
    """Cards of the list and of the checked collections that ARE one episode's page (``normalize.preview`` ``episode_cards``)."""
    return int((norm or {}).get("episode_cards") or 0) + sum(int(entry.get("episode_cards") or 0) for _spec, entry in pairs)


def _home_path_criterion(data: dict, html: Optional[str], meta: dict) -> tuple[dict, Optional[dict]]:
    """``home_path_is_canonical``: the page the yaml's ``list_url`` names is really another path of the site (``_page_signals``:
    ``redirect_hint``, certain: same host, another path), yet ``list_url`` AND every collection ``path`` still name the redirecting
    path. Returns ({criterion: ...} | {}, the hint | None); no hint, or a ``list_url`` that already names another page = no criterion."""
    if not html or not meta.get("url"):
        return {}, None
    hint = _page_signals(html, str(meta.get("url") or ""), str(meta.get("final_url") or "")).get("redirect_hint")
    if not hint:
        return {}, None
    base = str(data.get("base_url") or "")
    asked = _norm_path(str(meta["url"]))
    if _norm_path(urljoin(base, str(data.get("list_url") or "/"))) != asked:
        return {}, hint
    paths = [c.get("path") for c in data.get("collections") or [] if isinstance(c, dict)]
    stale = all(isinstance(p, str) and _norm_path(urljoin(base, p.strip() or "/")) == asked for p in paths)
    return {"home_path_is_canonical": _criterion(0 if stale else 1, 1, "min")}, hint


def _group_of(name: str) -> Optional[str]:
    """The ``INFO_FIELDS`` group a field name belongs to (``overview`` -> ``synopsis``), None for any other field."""
    low = str(name).strip().lower()
    return next((g for g, names in INFO_FIELDS.items() if low in names), None)


def _detail_info(data: dict, detail: Optional[dict], skip: set) -> dict:
    """``detail_info_defined``'s facts: ``{state, good[], missing[], required}`` over the info groups the admin did NOT skip. A group is
    good when one of its fields is defined in ``detail.fields`` AND filled in every parsed detail page (``detail.samples``; one page =
    that page). ``required`` = ``MIN_DETAIL_INFO`` (fewer when the admin said the site lacks some groups). ``state``: ``judged``;
    ``unjudged`` (fields are defined but no detail page could be read); ``exempt`` (the admin skipped every group)."""
    groups = [g for g in INFO_FIELDS if not (skip & {g, *INFO_FIELDS[g]})]
    if not groups:
        return {"state": "exempt", "good": [], "missing": [], "required": 0}
    block = data.get("detail") if isinstance(data.get("detail"), dict) else {}
    fields = block.get("fields") if isinstance(block.get("fields"), dict) else {}
    if fields and detail is None:
        return {"state": "unjudged", "good": [], "missing": groups, "required": 0}
    samples = [x for x in (detail or {}).get("samples") or [] if isinstance(x, dict)] or (
        [{"fill": (detail or {}).get("fill") or {}}] if detail else [])
    good = []
    for group in groups:
        names = [n for n in INFO_FIELDS[group] if n in fields]
        if names and samples and all(any(float((x.get("fill") or {}).get(n) or 0) > 0 for n in names) for x in samples):
            good.append(group)
    return {"state": "judged", "good": good, "missing": [g for g in groups if g not in good],
            "required": min(MIN_DETAIL_INFO, len(groups))}


def _effective_fields(data: dict, collection_id: Any) -> Optional[set]:
    """The field names collection ``collection_id`` of the yaml reads (its own ``fields``, else the list's); None when it has no such collection."""
    for spec in data.get("collections") or []:
        if isinstance(spec, dict) and spec.get("id") == collection_id:
            own = spec.get("fields")
            list_block = data.get("list") if isinstance(data.get("list"), dict) else {}
            names = own if isinstance(own, dict) and own else list_block.get("fields")
            return set(names) if isinstance(names, dict) else set()
    return None


def _removed_fields(data: dict, previous: Any, skip: set) -> list[dict]:
    """Detail / collection fields that produced values in the draft's PREVIOUS submission (``previous`` = its report) but are gone from
    this yaml, plus the ones an earlier run already flagged that are still gone (sticky: the entry stays until the field is put back or
    the admin says the site lacks it). ``[{where, field}]``; a field the admin skipped ("Sitede yok, atla") is never flagged."""
    if not isinstance(previous, dict):
        return []
    detail = data.get("detail") if isinstance(data.get("detail"), dict) else {}
    detail_fields = set(detail.get("fields") or {}) if isinstance(detail.get("fields"), dict) else set()

    def gone(where: str, field: str) -> bool:
        if field.lower() in skip or ((_group_of(field) or "") in skip):
            return False
        if where != "detail" and SKIP_COLLECTION_POSTER in skip and _group_of(field) == "poster_url":
            return False   # "Varsa al, yoksa atla: collection_poster": a poster field that is gone is the admin's call
        if where == "detail":
            return field not in detail_fields
        names = _effective_fields(data, where[len("collections["):-1])
        return names is not None and field not in names

    out: list[dict] = []

    def add(where: str, field: str) -> None:
        if gone(where, field) and not any(x["where"] == where and x["field"] == field for x in out):
            out.append({"where": where, "field": field})

    for name, value in ((previous.get("detail") or {}).get("fill") or {}).items():
        if isinstance(value, (int, float)) and value > 0:
            add("detail", str(name))
    for entry in previous.get("collections") or []:
        if isinstance(entry, dict):
            for name, value in (entry.get("field_fill") or {}).items():
                if isinstance(value, (int, float)) and value > 0:
                    add(f"collections[{entry.get('id')}]", str(name))
    for entry in previous.get("removed_fields") or []:
        if isinstance(entry, dict) and isinstance(entry.get("where"), str) and isinstance(entry.get("field"), str):
            add(entry["where"], entry["field"])
    return out[:MAX_REMOVED]


def _removed_warning(removed: list[dict]) -> str:
    """The warning / hint text for ``removed_fields`` (Turkish, like the failing hints)."""
    names = ", ".join(f"{x['field']} ({x['where']})" for x in removed[:4])
    return (f"alan kaldırıldı: {names}: önceki gönderimde değer üretiyordu. Sitede gerçekten yoksa kullanıcıya sor (ask_user, "
            "field = alan adı), değilse geri koy")


def _hardening(data: dict, norm: Optional[dict], pairs: list, collections_ran: bool, skipped: Any, html: Optional[str],
               meta: dict, detail: Optional[dict] = None) -> tuple[dict, list, Optional[dict], Optional[dict]]:
    """The hardening criteria of a NEW site's onboarding, only where they apply (``HARDEN_CRITERIA``):

    * ``availability_gate_defined`` (``playback: video``): the yaml must have an ``availability_gate`` that is on (probe >= 1), so a series /
      film whose player cannot be found or is blocked is not taken. Never skippable.
    * ``series_signal_collection`` (a site that makes series, ``collections: true``): at least one ``trending`` / ``latest_series``
      collection that yields >= ``MIN_COLLECTION_COUNT`` items (an unchecked one is not judged). Exempt when the admin said "Sitede yok,
      atla: home_series_section" (draft ``skipped_fields``).
    * ``series_full_inventory`` (a site that makes series, ``playback: video``): cards that ARE an episode's page (list or collection)
      need a ``series_page`` inventory, else every series stays in the library with only the one episode its card shows. Exempt for
      ``series_inventory`` (the site's series pages do not list the episodes).
    * ``home_path_is_canonical`` (the list page carries a certain ``redirect_hint``): ``list_url`` / collection ``path``s must not
      still name the redirecting path.
    * ``collection_poster_fill`` (``collections: true``): every checked collection of a ``POSTER_ROLES`` role fills ``poster_url`` in
      >= ``MIN_COLLECTION_POSTER_FILL`` of its items (a collection that defines no poster field = 0). Exempt for ``collection_poster``.
    * ``detail_info_defined``: the detail page gives >= ``MIN_DETAIL_INFO`` of the ``INFO_FIELDS`` groups (defined AND filled in every
      parsed sample page); each group is exempt when the admin answered "Sitede yok, atla: <field>" (= "Varsa al, yoksa atla": the field is
      NOT removed; a skipped field the yaml defines stays defined, is taken where the pages have it, and is listed in ``exempt`` as ``optional``).

    Returns (criteria, exempt [{criterion, field}], the redirect hint | None, ``detail_info`` facts | None)."""
    out: dict[str, dict] = {}
    exempt: list[dict] = []
    skip = _skip_names(skipped)
    video = data.get("playback") == "video"
    series = _produces_series(data, norm)
    if video:
        out["availability_gate_defined"] = _criterion(1 if sblocked.gate_of(data.get("availability_gate")) else 0, 1, "min")
    if series and collections_ran:
        if SKIP_HOME_SERIES in skip:
            exempt.append({"criterion": "series_signal_collection", "field": SKIP_HOME_SERIES})
        else:
            entries = [entry for _spec, entry in pairs if entry.get("role") in SERIES_SIGNAL_ROLES]
            judged = [entry for entry in entries if entry.get("status") != "skipped"]
            if not entries or judged:   # none written = 0; written but never fetched (no time) = not judged
                out["series_signal_collection"] = _criterion(max((int(e.get("valid_count") or 0) for e in judged), default=0),
                                                             MIN_COLLECTION_COUNT, "min")
    if series and video:
        if SKIP_SERIES_INVENTORY in skip:
            exempt.append({"criterion": "series_full_inventory", "field": SKIP_SERIES_INVENTORY})
        else:
            out["series_full_inventory"] = _criterion(1 if (data.get("series_page") or not _episode_cards(norm, pairs)) else 0, 1, "min")
    if collections_ran:
        if SKIP_COLLECTION_POSTER in skip:
            item = {"criterion": "collection_poster_fill", "field": SKIP_COLLECTION_POSTER}
            if any(_group_of(n) == "poster_url" for spec in data.get("collections") or [] if isinstance(spec, dict) and spec.get("role") in POSTER_ROLES
                   for n in _effective_fields(data, spec.get("id")) or ()):
                item["optional"] = True   # the poster field stays in the yaml: taken where the cards have it, empty where they do not
            exempt.append(item)
        else:
            judged = [e for _spec, e in pairs if e.get("role") in POSTER_ROLES and e.get("status") != "skipped" and int(e.get("valid_count") or 0) > 0]
            if judged:   # one that yields no items at all is collections_valid_count's problem, not the poster's
                out["collection_poster_fill"] = _criterion(min(float((e.get("field_fill") or {}).get("poster_url") or 0.0) for e in judged),
                                                           MIN_COLLECTION_POSTER_FILL, "min")
    info = _detail_info(data, detail, skip)
    if info["state"] == "judged":
        out["detail_info_defined"] = _criterion(len(info["good"]), info["required"], "min")
    # a skipped info group whose field the yaml DEFINES is "optional": kept in the yaml, taken where the pages have it, empty elsewhere
    # (no fill requirement, no warning); ``exempt`` lists it as ``optional``
    detail_block = data.get("detail") if isinstance(data.get("detail"), dict) else {}
    defined = set(detail_block.get("fields") or ()) if isinstance(detail_block.get("fields"), dict) else set()
    optional = sorted(n for g, names in INFO_FIELDS.items() if skip & {g, *names} for n in names if n in defined)
    skipped_info = sorted(skip & {n for ns in INFO_FIELDS.values() for n in ns})
    if info["state"] == "exempt" or optional:
        item = {"criterion": "detail_info_defined", "field": ", ".join(optional or skipped_info)}
        if optional:
            item["optional"] = True
        exempt.append(item)
    home, hint = _home_path_criterion(data, html, meta)
    out.update(home)
    return out, exempt, hint, info


# --- provider recipes of the draft (in memory) -------------------------------------------------------------------

class _Recipes(NamedTuple):
    """``providers`` = the valid recipes as registry providers, ``entries`` = one report row per recipe asked for (valid or
    not), ``errors`` = every problem, ready for the report's ``errors``."""
    providers: list
    entries: list
    errors: list


def _recipe_dicts(items: Any) -> list[dict]:
    """``[{name, yaml}]`` of request items (pydantic models or dicts); ``mode: "update"`` is kept (the only mode that is not the default)."""
    out = []
    for item in items or []:
        get = item.get if isinstance(item, dict) else lambda key, _i=item: getattr(_i, key, None)
        entry = {"name": str(get("name") or "").strip(), "yaml": str(get("yaml") or "")}
        if get("mode") == "update":
            entry["mode"] = "update"
        out.append(entry)
    return out


def _active_recipe(name: str) -> Optional[dict]:
    """The active library recipe ``name`` as a mapping (None when there is none or it is unreadable)."""
    try:
        return scfg.load_recipe(name)
    except (OSError, ValueError):
        return None


def _prepare_recipes(items: Any, replace: bool = False) -> _Recipes:
    """Parse and validate the recipes the draft brings (``[{name, yaml}]``). A name that is a code provider's, already in the
    library, or used twice is an error (``onboard.save`` would refuse it); the rest is ``recipes.validate_recipe``.
    ``replace`` (repair mode) lets a name of the library through: the recipe is then an UPDATE of the existing one
    (``registry.providers(extra)`` lets the in-memory one win; the heal writes it as a new version)."""
    wanted = _recipe_dicts(items)
    out = _Recipes([], [], [])
    if len(wanted) > MAX_PROVIDER_RECIPES:
        out.errors.append(f"provider_recipes: at most {MAX_PROVIDER_RECIPES} recipes per draft (got {len(wanted)})")
        wanted = wanted[:MAX_PROVIDER_RECIPES]
    taken = set(scfg.recipe_names())
    seen: set = set()
    for index, item in enumerate(wanted):
        name = item["name"]
        entry = {"name": name, "valid": False, "errors": [], "version": None, "description": "", "hosts": [], "fetch": "", "label": ""}
        data, problem = recipes.parse(item["yaml"])
        problems: list[str] = [problem] if problem else []
        if data is not None:
            if data.get("name") in (None, ""):
                data = {**data, "name": name}
            elif data["name"] != name:
                problems.append(f"name: the yaml says {data['name']!r} but the entry is named {name!r}")
            problems += recipes.validate_recipe(data)
            update = item.get("mode") == "update"
            if update:
                entry["mode"] = "update"
                active = _active_recipe(name)
                if active is None:
                    problems.append(f"mode update: there is no provider recipe {name!r} in the library (a new recipe has no mode)")
                else:
                    problems += recipes.update_problems(active, data)
            if name in seen:
                problems.append("name: listed twice")
            elif name in taken and not replace and not update:
                problems.append(f"name: a provider recipe {name!r} already exists in the library; choose another name "
                                "(an existing recipe is used by listing it in providers:, or updated with mode update to add a host)")
            entry["description"] = str(data.get("description") or "")[:200]
        seen.add(name)
        if problems:
            entry["errors"] = [_clip(p, 300) for p in problems]
        else:
            provider = recipes.RecipeProvider(data)
            out.providers.append(provider)
            entry.update(valid=True, version=provider.version, hosts=list(provider.hosts), label=provider.label,
                         fetch=data.get("fetch") or "http")
        out.entries.append(entry)
        out.errors.extend(f"provider_recipes[{index}] {name or '?'}: {_clip(p, 300)}" for p in problems)
    return out


def _is_repair(job_id: Optional[str]) -> bool:
    """A sandbox token of a heal repair run (``rp_...``) rather than of an onboarding draft (``od_...``)."""
    return onboard_store.valid_repair_id(job_id)


def _stored_recipes(draft_id: str) -> list[dict]:
    """The recipes a draft already carries (or, for a repair job, the ones its last ``submit_repair`` recorded)."""
    draft = (onboard_store.load_repair(draft_id) if _is_repair(draft_id) else onboard_store.get_draft(draft_id)) or {}
    return [r for r in _recipe_dicts(draft.get("provider_recipes")) if r["name"]]


def _merge_recipes(draft_id: str, items: Any) -> list[dict]:
    """The recipes of a test call: the ones the draft already carries (a former ``submit_draft``) plus the call's own (same
    name: the call's wins)."""
    merged = {r["name"]: r for r in _stored_recipes(draft_id)}
    for item in _recipe_dicts(items):
        merged[item["name"]] = item
    return list(merged.values())


def _recipe_usage(entries: list, playable: Optional[dict]) -> list:
    """``entries`` with ``used`` (playable samples that resolved through the recipe) and ``streams`` (a few of those samples'
    streams) added; a recipe nothing resolved through is flagged by the caller."""
    samples = [x for x in (playable or {}).get("samples") or [] if x.get("ok")]
    out = []
    for entry in entries:
        hit = [x for x in samples if entry.get("label") and entry["label"] in (x.get("providers") or [])]
        streams: list = []
        for sample in hit:
            for stream in sample.get("streams") or []:
                if stream not in streams and len(streams) < 3:
                    streams.append(stream)
        out.append({**entry, "used": len(hit), "streams": streams})
    return out


def _analyze(*args, **kwargs) -> dict:
    """The report of a draft (``_analyze_report``) plus its actionable diagnostics: ``diagnostics`` = ``{list, detail, series,
    collections, search, player}`` (each a possibly empty list of ``{where, problem, ...}``: what the selectors extracted, why a
    row / field was rejected, what else sits on the page) and, when ``passed`` is false, ``failing`` = ``[{criterion, value, bound,
    hint}]``. Bounded (<= 1 KB per entry, <= 10 KB in all)."""
    box, token = _diag_start()
    try:
        out = _analyze_report(*args, **kwargs)
    finally:
        _DIAG.reset(token)
    out["diagnostics"] = box["stages"]
    if not out.get("passed"):
        out["failing"] = _failing(out)
    return out


def _analyze_report(yaml_text: str, page_id: Optional[str], detail_page_id: Optional[str], deadline: float,
                    collections: bool = False, site_hint: str = "", playable: bool = False,
                    draft_recipes: Optional[_Recipes] = None, baseline: bool = False, search_stage: bool = False,
                    harden: bool = False, skipped: Optional[list] = None, previous: Optional[dict] = None) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    out: dict[str, Any] = {"valid": False, "errors": errors, "warnings": warnings, "list": None, "normalize": None,
                           "detail": None, "ingest": None, "passed": False, "criteria": {}}
    if collections:
        out["collections"] = []
    if playable:
        out["playable"] = None
        out["series"] = None
    if search_stage:
        out["search"] = None
    data, problem = _load_yaml(yaml_text)
    if problem:
        errors.append(problem)
        out["criteria"] = _criteria(0, {}, None, len(errors))
        if playable:
            out["criteria"].update(_playable_criteria(None, None))
        if baseline:
            out["baseline"] = {"ok": False, "reasons": ["baseline: the yaml does not parse"]}
            out["criteria"]["baseline_ok"] = _criterion(0, 1, "min")
        return out
    core_errors, core_warnings = _check_core(data)
    out["ingest"] = _ingest_block(data, 0)   # item_limit is known before the list page is read
    field_errors = _check_fields(data)
    extra = draft_recipes.providers if draft_recipes else []
    play_errors, play_warnings = _check_playback(data, [p.name for p in extra])
    norm_errors = _check_normalize(data)
    url_errors = _url_params_errors(data)
    series_errors = _check_series_page(data)
    search_errors = _check_search(data)
    blocked_errors = _check_blocked_blocks(data)
    errors += core_errors + field_errors + play_errors + norm_errors + url_errors + series_errors + search_errors + blocked_errors
    errors += scfg.regex_lint(data)   # `\\d` in a single-quoted yaml scalar: a regex that can never match (a NEW site must not pass)
    if draft_recipes:
        errors += draft_recipes.errors
    warnings += core_warnings + play_warnings
    if search_stage and not _has_search(data):
        warnings.append(NO_SEARCH_WARNING)
    pairs: list = []
    if collections:
        pairs, col_errors, col_warnings = _check_collections(data, site_hint)
        errors += col_errors
        warnings += col_warnings
        if not pairs and not col_errors:
            warnings.append(NO_COLLECTIONS_WARNING)

    items: list = []
    fill: dict = {}
    norm: Optional[dict] = None
    main: Optional[tuple[str, str, list, int]] = None   # the list page for the collections: (url, page id, items, rows)
    collection_rows: list = []   # raw rows of the collections read before the series stage (the series directory's other half)
    collections_done = False
    list_html: Optional[str] = None   # the list page as the engine reads it + its meta (``harden``: home_path_is_canonical)
    list_meta: dict = {}
    if not (core_errors or field_errors):
        cfg = _draft_cfg(data, extra)
        html: Optional[str] = None
        try:
            if page_id:
                html, list_meta = _load_page(page_id)
            else:
                got = _fetch_store(urljoin(cfg.base_url, cfg.list_url or "/"), cfg.fetch_mode, cfg.row_selector, deadline)
                html, list_meta = got["html"], got["meta"]
                out["list_page_id"] = got["meta"]["page_id"]
        except ApiError as exc:
            errors.append(f"list: page not available ({exc.detail.get('message') if isinstance(exc.detail, dict) else exc.detail})")
        if html is not None:
            list_html = html
            out["list"], items, fill = _list_stage(cfg, html, errors, warnings)
            out["ingest"] = _ingest_block(data, len(items))
            if not norm_errors:
                norm = _normalize_stage(cfg, items, errors, warnings)
            out["normalize"] = norm
            out["detail"] = _detail_stage(cfg, items, detail_page_id, deadline, warnings, extra_sample=harden and harden_enabled())
            list_page = page_id or out.get("list_page_id")
            if list_page and out["list"] is not None:
                main = (urljoin(cfg.base_url, cfg.list_url or "/"), list_page, items, out["list"]["count"])
            # a site that links series pages by URL shape (``series_page.series_url_regex``): the cards of its "latest episodes" sections link
            # EPISODE pages, which the series directory resolves to series pages (``library/series_dir.py``). The directory needs the rows of the
            # collections, so they are read before the series stage (cheap pages; the playable stage keeps the rest of the call's time)
            series_spec = data.get("series_page") if isinstance(data.get("series_page"), dict) else {}
            if pairs and playable and series_spec.get("series_url_regex") and not (norm_errors or series_errors):
                _collections_stage(cfg, pairs, main, deadline, not norm_errors, errors, warnings, collection_rows)
                collections_done = True
            directory = _series_directory(data, list(items) + collection_rows) if (playable or search_stage) and not norm_errors else None
            if search_stage and _has_search(data) and not search_errors:   # one query, before the long playable stage
                out["search"] = _search_stage(data, items, deadline, errors, warnings, extra, directory)
            if playable and not (norm_errors or play_errors or url_errors or blocked_errors):   # before the collections: it has the call's time first
                gate_run = _GateRun(cfg)   # blocked: / availability_gate: judged like production does
                out["series"], inventories = _series_stage(cfg, items, series_errors, deadline, warnings, gate_run, collection_rows, directory,
                                                           ingest_sample=bool(harden and harden_enabled() and directory is not None))   # its episode pages feed the playable stage
                out["playable"] = _playable_stage(cfg, items, deadline, warnings, inventories, gate_run)
                if gate_run is not None:
                    out["blocked"] = gate_run.blocked_report()   # series / pages that would not be taken (count = rule + gate)
                    if gate_run.gate_report() is not None:
                        out["gate"] = gate_run.gate_report()
                        if out["gate"]["skipped"]:
                            warnings.append(f"gate: {out['gate']['skipped']} of {out['gate']['probed']} series / films would not be taken "
                                            f"({out['gate']['passed']} remain): " + "; ".join(
                                                f"{s['url']} ({s['reason']})" for s in out["gate"]["samples"])[:300])
        if pairs and not collections_done:
            _collections_stage(cfg, pairs, main, deadline, not norm_errors, errors, warnings)
    valid_count = (out["list"] or {}).get("valid_count", 0)
    play_criteria = _playable_criteria(out.get("playable"), norm, out.get("series")) if playable else {}
    if playable:
        play_criteria.update(_series_criteria(out.get("series"), bool(data.get("series_page"))))
    if play_criteria.get("series_have_episode_sources", {}).get("ok") is False:
        errors.append(SERIES_SOURCES_ERROR)
    out["criteria"] = _criteria(valid_count, fill, norm, len(errors))
    out["criteria"].update(play_criteria)
    if search_stage:
        out["criteria"].update(_search_criteria(data, out.get("search")))
    if baseline:   # repair mode: the yaml against the ACTIVE config of its site (fields kept, fill not clearly lower)
        out["baseline"] = _baseline_check(data, out)
        out["criteria"]["baseline_ok"] = _criterion(int(out["baseline"]["ok"]), 1, "min")
    if collections:
        waiting = [entry for _spec, entry in pairs if entry["status"] == "pending"]
        for entry in waiting:   # the list part is broken: the collections were not fetched at all
            entry["status"] = "error" if entry["errors"] else "skipped"
        if any(e["status"] == "skipped" for e in waiting):
            warnings.append("collections: not fetched (fix the list / config errors first)")
        out["collections"] = [entry for _spec, entry in pairs]
        out["criteria"].update(_collection_criteria(pairs))
    if harden and harden_enabled():   # a NEW site only: the rules the agent may not skip by taking the easy way (see ``_hardening``)
        extra_criteria, exempt, hint, info = _hardening(data, norm, pairs, collections, skipped, list_html, list_meta, out.get("detail"))
        out["criteria"].update(extra_criteria)
        if exempt:
            out["exempt"] = exempt
        if hint:
            out["redirect_hint"] = hint
        if info["state"] == "judged":
            out["detail_info"] = {k: info[k] for k in ("good", "missing", "required")}
        out["criteria"].update(_ingest_sample_criterion((out.get("series") or {}).get("ingest_sample")))
        removed = _removed_fields(data, previous, _skip_names(skipped))
        if removed:   # a field that gave values last time is gone: dropped silently to look better? the agent must say why
            out["removed_fields"] = removed
            warnings.insert(0, _removed_warning(removed))
    if draft_recipes and draft_recipes.entries:
        out["provider_recipes"] = _recipe_usage(draft_recipes.entries, out.get("playable"))
        if playable and (out.get("playable") or {}).get("checked"):
            for entry in out["provider_recipes"]:
                if entry["valid"] and not entry["used"]:
                    warnings.append(f"provider_recipes: no playable sample resolved through the recipe {entry['name']!r}; its match / "
                                    "rules were not exercised end to end (test_provider on a real player URL, and make the site "
                                    "yaml's providers: list it)")
    out["valid"] = not errors
    out["passed"] = all(c["ok"] for c in out["criteria"].values())
    return out


def _baseline_check(data: dict, out: dict) -> dict:
    """The proposal ``data`` (a repair of a registered site) against the ACTIVE config and baseline of the site named by its
    ``site_id``: ``heal.check_repair_proposal`` (no field dropped, attr / cast / all kept, schema and normalize key kept) and the
    heal's own measures on the list parse of ``out`` (``drift.detect`` thresholds of the baseline, ``heal._check_fill`` against
    ``last_good``). ``{ok, reasons[], site_id, version}``; nothing is written."""
    site_id = str(data.get("site_id") or "")
    if site_id not in scfg.list_sites():
        return {"ok": False, "site_id": site_id, "version": None,
                "reasons": [f"baseline: site_id {site_id!r} is not a registered site (a repair keeps the site_id of the site it repairs)"]}
    try:
        old = scfg.load_site(site_id)
    except Exception as exc:
        return {"ok": False, "site_id": site_id, "version": None, "reasons": [f"baseline: active config unreadable ({type(exc).__name__})"]}
    reasons = [f"baseline: {r}" for r in heal.check_repair_proposal(old, data)]
    block = out.get("list")
    if block is None:
        reasons.append("baseline: the list page could not be parsed with this yaml")
    else:
        metrics = {"valid_count": block.get("valid_count", 0), "fill_ratio": block.get("fill_ratio", 0.0),
                   "field_fill": block.get("key_field_fill") or {}}
        base = old.baseline()
        verdict = heal.drift.detect(metrics, base)
        reasons += [f"baseline: {r}" for r in verdict["reasons"]]
        bad = heal._check_fill(base, metrics, block.get("field_fill") or {})
        if bad:
            reasons.append(f"baseline: {bad}")
    return {"ok": not reasons, "site_id": site_id, "version": old.version, "reasons": [_clip(r, 300) for r in reasons[:8]]}


def _do_test_config(body, *, deadline: float, repair: bool = False, site_hint: str = "", harden: bool = False,
                    skipped: Optional[list] = None, previous: Optional[dict] = None) -> dict:
    # the search stage rides with ``playable`` (one live query); a repair run never queries the live search of a registered site;
    # ``site_hint`` = the edited site of an edit run (the collection ids are checked against it); ``harden`` = a NEW site's onboarding
    # (the hardening criteria, ``_hardening``; ``skipped`` = the draft's "Sitede yok, atla" answers)
    return _analyze(body.yaml_text, body.page_id, body.detail_page_id, deadline, collections=body.collections,
                    site_hint=site_hint, playable=body.playable, draft_recipes=_prepare_recipes(body.provider_recipes, replace=repair),
                    baseline=bool(getattr(body, "baseline", False)), search_stage=bool(body.playable) and not repair,
                    harden=harden, skipped=skipped, previous=previous)


# --- resolvers --------------------------------------------------------------------------------------------------

def _resolve_one(cfg, raw: dict, locator: str, load_cookies, deadline: float, site_id: str = DRAFT_SITE_ID) -> dict:
    """One candidate, start to finish (the playback path of ``library/videos`` minus the database); never raises."""
    from ..scraper import resolve, site_extractors
    out = {"label": raw.get("label") or "", "provider": "", "streams": [], "duration": 0, "error": "", "events": [],
           "ms": 0, "lang": raw.get("lang") or "", "resolver_type": raw.get("resolver_type") or ""}
    started = time.monotonic()
    trace.begin()
    try:
        _check_deadline(deadline)
        candidate = site_extractors.resolve_candidate(site_id, raw, locator, load_cookies, cfg=cfg)
        if not candidate:
            raise ValueError("hand-off could not be resolved")
        url = urljoin(locator, candidate.get("url") or "")
        _check(url)
        out["resolver_type"] = out["resolver_type"] or candidate.get("resolver_type") or ""
        if isinstance(candidate.get("stream"), dict) and candidate["stream"]:
            resolved = dict(candidate["stream"])   # json_api / player_page: the type produced the media itself
            if not resolved.get("streams") and resolved.get("url"):
                resolved["streams"] = [{k: resolved.get(k) for k in ("url", "type", "quality") if resolved.get(k)}]
        else:
            def load_handoff(page_url: str) -> str:
                _check(page_url)
                return fetch.page(cfg, page_url)
            extra = getattr(cfg, "extra_providers", None)   # the draft's provider recipes (not in the library yet)
            resolved = registry.resolve(url, referer=locator, load_handoff=load_handoff, allowed=cfg.providers,
                                        **({"extra": extra} if extra else {}))
            if not resolved and cfg.stream_resolver:
                resolved = resolve.resolve_stream(cfg, url)
        if not resolved or not resolved.get("streams"):
            raise ValueError("provider returned no stream")
        out["provider"] = resolved.get("provider") or candidate.get("label") or raw.get("label") or ""
        out["duration"] = resolved.get("duration", 0)
        out["streams"] = [s for s in resolved["streams"] if isinstance(s, dict) and s.get("url")]
        if not out["streams"]:
            raise ValueError("provider returned no playable stream")
    except Exception as exc:
        out["error"] = exc.detail.get("message", "") if isinstance(exc, ApiError) and isinstance(exc.detail, dict) else trace.short(exc)
        out["streams"] = []
    events = trace.take()
    failed = [e for e in events if not e.get("ok") and e.get("error")]
    if out["error"] and failed:   # the stage that failed last says why (e.g. "player_page.extract: ... Cloudflare challenge")
        out["error"] += f" [{failed[-1]['stage']}: {failed[-1]['error']}]"
    out["events"] = events
    out["ms"] = int((time.monotonic() - started) * 1000)
    return out


def _same_url(url: str) -> str:
    """``url`` as a comparison key (no fragment, no trailing slash, lower-case scheme/host)."""
    parts = urlsplit(url.strip())
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path.rstrip('/')}" + (f"?{parts.query}" if parts.query else "")


def _trial_need(data: dict) -> float:
    """Seconds of the call that must be left to start one more page of ``test_resolvers``: the page fetch and the candidate
    limit of live playback (the browser budget for ``fetch: browser`` items) plus a margin; else the page is skipped."""
    per = config.RESOLVE_BROWSER_TIMEOUT if _uses_browser(data) else config.RESOLVE_TOTAL_TIMEOUT
    return per + TRIAL_PAGE_SLACK


def _trial_streams(outcomes: list) -> list[dict]:
    """``[{type, host, quality}]`` of the streams the resolved candidates of one page yielded (distinct, capped)."""
    out: list[dict] = []
    for outcome in outcomes:
        for stream in (outcome or {}).get("streams") or []:
            entry = {"type": str(stream.get("type") or ""), "host": (urlsplit(str(stream.get("url") or "")).hostname or "").lower(),
                     "quality": str(stream.get("quality") or "")}
            if entry not in out and len(out) < TRIAL_STREAMS:
                out.append(entry)
    return out


def _trial_page(cfg, data: dict, url: str, stored_id: Optional[str], deadline: float, warnings: list, label: str = "") -> tuple[dict, dict]:
    """``test_resolvers`` on ONE detail page: fetch it (or take the stored page), find the candidates, resolve them like a play
    request. Returns ``(page, detail)``: ``page`` is the compact entry of ``pages[]`` (``detail_url``, ``status`` resolved |
    no_stream | no_candidates | error, ``candidates`` / ``resolved`` counts, ``streams`` ``[{type, host, quality}]``, ``error``,
    ``page_id``); ``detail`` the full candidates and per-candidate answers the old single-page fields carry. Never raises;
    ``label`` ("page 2: ") prefixes the warnings it adds."""
    from ..library import videos
    from ..scraper import site_extractors
    page: dict[str, Any] = {"detail_url": url, "status": "error", "candidates": 0, "resolved": 0, "streams": [], "error": ""}
    detail: dict[str, Any] = {"locator": url, "timeout": False, "candidate_count": None, "candidates": [], "resolved": []}
    try:
        if stored_id:
            html, meta = _load_page(stored_id)
            locator, page_id = meta.get("final_url") or meta.get("url") or url, stored_id
        else:
            got = _fetch_store(url, cfg.fetch_mode, "", deadline)
            html = got["bundle"].get("initial_html") or got["html"]
            locator, page_id = got["meta"]["final_url"], got["meta"]["page_id"]
    except ApiError as exc:
        page["error"] = f"detail page: {exc.detail.get('message') if isinstance(exc.detail, dict) else exc.detail}"
        detail["timeout"] = exc.status_code == 504
        return page, detail
    page["page_id"] = detail["page_id"] = page_id
    detail["locator"] = locator
    rules = [r for r in cfg.blocked if r.get("on") == "episode_page"]
    hit = sblocked.match(html, rules, "episode_page") if rules else None
    if hit:   # a placeholder of content that is not public (``blocked:`` rule): not a broken resolver, nothing to resolve
        page.update(status="blocked", reason=hit["reason"], error=f"blocked: {hit['reason']}")
        return page, detail
    module = _module_site()
    candidates = site_extractors.discover(module, html, locator, cfg=cfg)
    page["candidates"] = detail["candidate_count"] = len(candidates)
    if not candidates:
        page["status"] = "no_candidates"
        page["error"] = "no candidates found on the detail page (check the resolvers selectors against this page)"
        return page, detail
    candidates = candidates[:MAX_CANDIDATES]
    detail["candidates"] = [{"url": _clip(urljoin(locator, str(c.get("url") or "")), 200), "label": _clip(c.get("label") or "", 80),
                             "resolver_type": c.get("resolver_type") or ""} for c in candidates]

    load_cookies = _cookie_loader(cfg, locator)
    run_one = lambda raw: _resolve_one(cfg, raw, locator, load_cookies, deadline, module)
    # what live playback would allow: RESOLVE_CANDIDATE_TIMEOUT / RESOLVE_TOTAL_TIMEOUT, or a candidate's own larger
    # ``timeout`` (player_page with fetch: browser carries RESOLVE_BROWSER_TIMEOUT)
    live_per, live_total = videos.live_limits(candidates)
    if _uses_browser(data):
        # a browser fetch takes 10+ s and the worker lock serialises them: the live limits would
        # cut every candidate, so run them one by one within this call's own time limit instead
        per_text = f"{min(live_per):g}s"
        if max(live_per) > min(live_per):
            per_text += f" ({max(live_per):g}s for browser candidates)"
        text = ("a resolver item uses fetch: browser: candidates are tried one after the other within "
                f"{config.ONBOARD_TOOL_TIMEOUT:g}s; live playback allows each candidate only {per_text} ({live_total:g}s in total)")
        if text not in warnings:
            warnings.append(text)
        outcomes = _run_within(candidates, run_one, deadline)
    else:
        outcomes = videos._run_candidates(candidates, run_one)
    for index, outcome in enumerate(outcomes):
        outcome = outcome or {"streams": [], "provider": "", "error": "not run"}
        streams = outcome.get("streams") or []
        entry = {"index": index, "candidate": detail["candidates"][index]["url"], "ok": len(streams) > 0,
                 "provider": outcome.get("provider") or "", "streams_count": len(streams),
                 "error": _clip(outcome.get("error") or "", 300), "ms": int(outcome.get("ms") or 0)}
        if streams:
            first = streams[0]
            entry["host"] = (urlsplit(str(first.get("url") or "")).hostname or "").lower()
            entry["stream_type"] = first.get("type") or ""
            if entry["ms"] > live_per[index] * 1000:
                warnings.append(f"{label}candidate {index} took {entry['ms'] / 1000:.1f}s; live playback gives this candidate "
                                f"{live_per[index]:g}s, it would be cut off there")
        events = [{k: e.get(k) for k in ("stage", "host", "ok", "ms", "error") if e.get(k) not in (None, "")}
                  for e in (outcome.get("events") or [])[:8]]
        if events:
            entry["trace"] = _clip(events, FIELD_CLIP)
        for event in outcome.get("events") or []:   # a player_page warning: the URL contradicts the declared type / a skipped format
            stage = str(event.get("stage") or "")
            if stage.startswith(TRIAL_TYPE_STAGE):
                note = stage[len(TRIAL_TYPE_STAGE):]
                text = f"{label}candidate {index}: {note}" + (" (the URL's extension wins; leave type out or auto)" if note.startswith("declared") else "")
                if text not in warnings:
                    warnings.append(text)
        detail["resolved"].append(entry)
    page["resolved"] = sum(1 for r in detail["resolved"] if r["ok"])
    page["streams"] = _trial_streams(outcomes)
    page["status"] = "resolved" if page["resolved"] else "no_stream"
    if not page["resolved"]:
        page["error"] = next((r["error"] for r in detail["resolved"] if r["error"]), "")[:200]
    return page, detail


def _sample_detail_urls(cfg, list_page_id: Optional[str], deadline: float, skip: set, limit: int) -> tuple[list[str], str]:
    """Up to ``limit`` DIFFERENT detail / episode page URLs of the first items of the draft yaml's list page (the stored
    ``list_page_id``, else the page fetched from ``list.url``), leaving out the keys in ``skip``. ``(urls, why-not)``."""
    try:
        if list_page_id:
            html = _load_page(list_page_id)[0]
        else:
            html = _fetch_store(urljoin(cfg.base_url, cfg.list_url or "/"), cfg.fetch_mode, cfg.row_selector, deadline)["html"]
    except ApiError as exc:
        return [], f"the list page is not available ({exc.detail.get('message') if isinstance(exc.detail, dict) else exc.detail})"
    problems: list[str] = []
    _block, items, _fill = _list_stage(cfg, html, problems, [])
    urls: list[str] = []
    seen = set(skip)
    for item in items:
        target = item.get("detail_url")
        if not isinstance(target, str) or not target.strip():
            continue
        url = urljoin(cfg.base_url, target.strip())
        if _same_url(url) in seen:
            continue
        seen.add(_same_url(url))
        urls.append(url)
        if len(urls) >= limit:
            break
    if not urls:
        return [], problems[0] if problems else "the list page has no other item with a detail_url"
    return urls, ""


def _trial_status(checked: list) -> str:
    """Overall ``test_resolvers`` status over the pages that were really tried: ``resolved`` (all), ``partial`` (some),
    else ``no_stream`` (some page had candidates but no stream), ``no_candidates``, ``error``."""
    resolved = [p for p in checked if p["status"] == "resolved"]
    if checked and len(resolved) == len(checked):
        return "resolved"
    if resolved:
        return "partial"
    statuses = {p["status"] for p in checked}
    for status in ("no_stream", "no_candidates"):
        if status in statuses:
            return status
    return "error"


def _do_test_resolvers(body, *, deadline: float, repair: bool = False) -> dict:
    out: dict[str, Any] = {"status": "error", "candidates": [], "resolved": [], "errors": [], "warnings": []}
    errors = out["errors"]
    data, problem = _load_yaml(body.yaml_text)
    if problem:
        errors.append(problem)
        return out
    core_errors, _w = _check_core(data)
    draft_recipes = _prepare_recipes(body.provider_recipes, replace=repair)
    play_errors, out["warnings"] = _check_playback(data, [p.name for p in draft_recipes.providers])
    errors += ([e for e in core_errors if e.startswith(("base_url", "fetch_mode"))] + play_errors + _url_params_errors(data)
               + draft_recipes.errors + scfg.regex_lint(data))
    if not data.get("resolvers") and not errors:
        errors.append("resolvers: the yaml has no resolvers list (nothing to test)")
    if errors:
        return out
    cfg = _draft_cfg(data, draft_recipes.providers)
    if not cfg.resolvers:
        errors.append("resolvers: no valid resolver item")
        return out
    warnings = out["warnings"]
    first, detail = _trial_page(cfg, data, body.detail_url, body.page_id, deadline, warnings)
    # the old single-page fields describe the first page (``detail_url``); ``pages`` lists every page that was tried
    if detail.get("page_id"):
        out["page_id"] = detail["page_id"]
    if detail["candidate_count"] is not None:
        out["candidate_count"] = detail["candidate_count"]
    out["candidates"], out["resolved"] = detail["candidates"], detail["resolved"]
    out["resolved_ok"] = first["resolved"]
    pages = [first]
    seen = {_same_url(body.detail_url), _same_url(detail["locator"])}
    need = _trial_need(data)
    if body.detail_urls is not None:   # the agent names the pages ([] = this page only)
        targets = []
        for url in body.detail_urls:
            if url.strip() and _same_url(url) not in seen:
                seen.add(_same_url(url))
                targets.append(url.strip())
        targets = targets[:TRIAL_EXPLICIT_PAGES - 1]
    elif deadline - time.monotonic() < need:
        targets = []
        warnings.append("only the first page was tried (the call is out of time); name more pages in detail_urls")
    else:   # sample more pages from the list page of the draft yaml: sites serve different hosts / types per title
        targets, why = _sample_detail_urls(cfg, body.list_page_id, deadline, seen, TRIAL_AUTO_PAGES - 1)
        if not targets:
            warnings.append(f"only one page was tried: no other page could be sampled ({why}); name more in detail_urls")
    skipped = 0
    for url in targets:
        if deadline - time.monotonic() < need:
            page, other = {"detail_url": url, "status": "skipped", "candidates": 0, "resolved": 0, "streams": [],
                           "error": "not tried: the call ran out of time"}, {}
        else:
            page, other = _trial_page(cfg, data, url, None, deadline, warnings, f"page {len(pages) + 1}: ")
            if other["timeout"]:
                page.update(status="skipped", error="not tried: the call ran out of time")
        skipped += page["status"] == "skipped"
        pages.append(page)
    checked = [p for p in pages if p["status"] not in ("skipped", "blocked")]   # a blocked page is not public content: not judged
    blocked_pages = [p for p in pages if p["status"] == "blocked"]
    if blocked_pages:
        out["blocked"] = [{"url": p["detail_url"], "reason": p.get("reason", "")} for p in blocked_pages]
        warnings.append(f"{len(blocked_pages)} page(s) are blocked content (a `blocked:` rule matched: "
                        + "; ".join(f"{p['detail_url']} ({p.get('reason', '')})" for p in blocked_pages)[:240]
                        + "); they are left out of the verdict, try other pages")
    out["pages"] = pages
    out["pages_checked"] = len(checked)
    out["pages_resolved"] = sum(1 for p in checked if p["status"] == "resolved")
    out["status"] = _trial_status(checked)
    if skipped:
        warnings.append(f"{skipped} page(s) not tried (the call ran out of time); test them in a second call (detail_urls)")
    if out["status"] in ("no_candidates", "error"):
        errors.append(next((p["error"] for p in checked if p["error"]), "")
                      or ("every page tried is blocked content (a `blocked:` rule matched): name pages that are public in detail_urls"
                          if blocked_pages else "no page could be tried"))
    elif out["status"] == "partial":
        failing = ", ".join(f"{p['detail_url']} ({p['status']})" for p in checked if p["status"] != "resolved")
        warnings.append(f"partial: {out['pages_resolved']} of {len(checked)} pages resolved, failing: {failing}. The recipe fits only some "
                        "pages: generalize it (one extract rule per format / one resolver item per player, type auto) or say in "
                        "notes which kind of page is not playable")
    signatures = [(i + 1, sorted({f"{s['host']}/{s['type']}" for s in p["streams"]})) for i, p in enumerate(pages) if p["streams"]]
    if len(signatures) > 1 and len({tuple(sig) for _i, sig in signatures}) > 1:
        warnings.append("varied hosts/types: " + "; ".join(f"page {i} = {', '.join(sig)}" for i, sig in signatures)
                        + ". One extract rule rarely fits all of them: use type auto and a rule per format")
    return out


def _uses_browser(data: dict) -> bool:
    return any(isinstance(item, dict) and item.get("fetch") == "browser" for item in data.get("resolvers") or [])


def _run_within(candidates: list, run_one, deadline: float) -> list:
    """Run ``run_one`` for every candidate one at a time, stopping the wait RESOLVE_MARGIN before ``deadline``. The
    candidates that did not start or finish in time answer with a clear timeout error (the worker thread of a running
    one cannot be killed; it ends on its own network timeouts)."""
    outcomes: list = [None] * len(candidates)
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="onboard-resolve")
    futures = {pool.submit(run_one, raw): index for index, raw in enumerate(candidates)}
    pending = set(futures)
    try:
        while pending:
            left = deadline - RESOLVE_MARGIN - time.monotonic()
            if left <= 0:
                break
            done, pending = wait(pending, timeout=left, return_when=FIRST_COMPLETED)
            for future in done:
                try:
                    outcomes[futures[future]] = future.result()
                except Exception as exc:
                    outcomes[futures[future]] = {"streams": [], "provider": "", "error": trace.short(exc), "ms": 0}
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    for future in pending:
        outcomes[futures[future]] = {"streams": [], "provider": "", "ms": 0, "error": (
            f"timeout: not finished within the {config.ONBOARD_TOOL_TIMEOUT:g}s call limit (a browser fetch is slow: "
            "use fewer resolver items or follow hops, or test the player page by hand with fetch_page mode browser)")}
    return outcomes


# --- test_provider (one recipe, one player URL) -----------------------------------------------------------------

def _do_test_provider(body, *, deadline: float, repair: bool = False) -> dict:
    """Validate a provider recipe and run it on ONE player URL (``sample_url``), like the registry would for a play request.
    Nothing is written. ``status``: invalid (the recipe does not validate), no_match (``match`` does not cover ``sample_url``:
    the recipe would never be chosen), resolved, no_stream. ``repair`` (a heal repair run): a name that is in the library is
    the update of that recipe, not a clash."""
    out: dict[str, Any] = {"valid": False, "errors": [], "warnings": [], "matched": False, "status": "invalid", "streams": [], "trace": []}
    data, problem = recipes.parse(body.recipe_yaml)
    if problem:
        out["errors"].append(problem)
        return out
    errors = recipes.validate_recipe(data)
    if errors:
        out["errors"] = [_clip(e, 300) for e in errors]
        return out
    provider = recipes.RecipeProvider(data)
    out.update(valid=True, name=provider.name, version=provider.version, hosts=list(provider.hosts),
               fetch=data.get("fetch") or "http")
    taken = set(scfg.recipe_names())
    if provider.name in taken and not repair:
        out["warnings"].append(f"a provider recipe named {provider.name!r} already exists in the library: submit_draft would refuse "
                               "this name (use another name, or reference the existing recipe in providers:)")
    url = _check(body.sample_url)
    referer = _clean_referer(body.referer)
    out["matched"] = provider.matches(url)
    if not out["matched"]:
        parts = urlsplit(url)
        out["status"] = "no_match"
        out["errors"].append(f"match: the recipe does not cover sample_url (host {parts.hostname}, path {parts.path or '/'}); "
                             "fix match.host_regex / match.path_regex")
        return out
    if not referer:
        out["warnings"].append("no referer given: a player usually sits in a detail / episode page; pass that page's URL as referer "
                               "(it is the {page_url} of the recipe and the Referer header)")
    _check_deadline(deadline)
    started = time.monotonic()
    trace.begin()
    resolved = None
    crash = ""
    try:
        resolved = provider.resolve(url, referer=referer)
    except Exception as exc:
        crash = trace.short(exc)
    events = trace.take()
    streams = [s for s in (resolved or {}).get("streams") or [] if isinstance(s, dict) and s.get("url")]
    out["streams"] = _trial_streams([{"streams": streams}])
    out["status"] = "resolved" if streams else "no_stream"
    out["ms"] = int((time.monotonic() - started) * 1000)
    out["trace"] = _clip([{k: e.get(k) for k in ("stage", "host", "ok", "ms", "error") if e.get(k) not in (None, "")}
                          for e in events[:10]], FIELD_CLIP)
    if not streams:
        failed = [e for e in events if not e.get("ok") and e.get("error")]
        out["error"] = _clip(crash or (f"{failed[-1]['stage']}: {failed[-1]['error']}" if failed else "the recipe found no stream"), 300)
    for event in events:   # a declared type that the URL contradicts / a skipped format (as in test_resolvers)
        stage = str(event.get("stage") or "")
        if stage.startswith(TRIAL_TYPE_STAGE):
            note = stage[len(TRIAL_TYPE_STAGE):]
            text = note + (" (the URL's extension wins; leave type out or auto)" if note.startswith("declared") else "")
            if text not in out["warnings"]:
                out["warnings"].append(text)
    return out


# --- discover_site / match_providers ------------------------------------------------------------------------------

def _do_discover_site(body, *, deadline: float) -> dict:
    """The draft site yaml of ``body.url`` built by code (``scraper/discover.py``): <= 8 pages, each through the sandbox fetch (``netguard``,
    page store: the answer's ``pages[].page_id`` can be used with query_html / grep_page). Nothing is written."""
    from ..scraper import discover
    _check(body.url)
    limit = min(deadline, time.monotonic() + DISCOVER_SECONDS)

    def getter(url: str, role: str) -> "discover.Page":
        try:
            got = _fetch_store(url, "auto", "", limit)
        except ApiError as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            raise discover.PageError(f"{detail.get('code', exc.status_code)}: {detail.get('message', '')}")
        meta = got["meta"]
        return discover.Page(url=url, final_url=meta["final_url"], html=got["html"], fetch_mode=meta["fetch_mode"], status=int(meta["status"]),
                             page_id=meta["page_id"])

    return discover.discover(body.url, getter, outline=_outline_of, deadline=limit)


def _do_match_providers(body, *, deadline: float) -> dict:
    """Every provider of the library dry-run on ONE player page, host match ignored (``scraper/provider_match.py``); nothing is written."""
    from ..scraper import provider_match
    url = _check(body.player_url)
    referer = _clean_referer(body.referer) or _clean_referer(body.detail_url)
    _check_deadline(deadline)
    return provider_match.match(url, referer=referer, deadline=time.monotonic() + max(5.0, min(provider_match.MAX_SECONDS, deadline - time.monotonic() - 5.0)))


# --- submit -----------------------------------------------------------------------------------------------------

def _edit_summary(site_id: str, data: Optional[dict]) -> dict:
    """What an edit draft changes against the ACTIVE yaml of its site: ``{site_id, from_version, changed_keys[], changed_paths[]}``
    (informational; the layer-scope advice of the skill is not enforced here)."""
    out: dict[str, Any] = {"site_id": site_id, "from_version": None, "changed_keys": [], "changed_paths": []}
    try:
        from ..scraper import heal_agent
        cfg = scfg.load_site(site_id)
        out["from_version"] = cfg.version
        if isinstance(data, dict):
            keys, paths = heal_agent.touched(cfg.data, data)
            out["changed_keys"], out["changed_paths"] = keys, paths[:20]
    except Exception as exc:   # the summary never fails a submit
        log.debug("edit summary of %s failed: %s", site_id, type(exc).__name__)
    return out


def _do_submit(body, *, deadline: float) -> dict:
    draft = onboard_store.get_draft(body.draft_id)
    if draft is None:
        raise ApiError(404, "not_found", f"draft {body.draft_id!r} not found")
    if draft.get("status") in ("cancelled", "saved"):
        raise ApiError(409, "conflict", f"draft is {draft['status']}; it cannot take a new submission")
    suggestion = (body.site_id_suggestion or "").strip()
    edit = _edit_site(body.draft_id) if draft.get("mode") == "edit" else ""
    locked_from = ""
    if edit:   # an edit keeps the registered site: the agent cannot rename or re-id it
        locked_from = suggestion if suggestion and suggestion != edit else ""
        suggestion = edit
    # omitted = the recipes the draft already carries stay; a list (even empty) replaces them
    asked = _recipe_dicts(body.provider_recipes) if body.provider_recipes is not None else _stored_recipes(body.draft_id)
    draft_recipes = _prepare_recipes(asked)
    with module_site(edit):   # an edit run: the site's own code modules are found by its real id
        report = _analyze(body.yaml_text, body.page_id, body.detail_page_id, deadline, collections=True, site_hint=suggestion,
                          playable=True, draft_recipes=draft_recipes, search_stage=True,
                          harden=not edit, skipped=_skip_names(draft.get("skipped_fields")) if not edit else None,
                          previous=draft.get("report") if not edit and isinstance(draft.get("report"), dict) else None)
    for problem in draft_recipes.errors:   # idempotent: _analyze already added them (a canned _analyze may not)
        if problem not in report["errors"]:
            report["errors"].append(problem)
    if draft_recipes.entries and "provider_recipes" not in report:
        report["provider_recipes"] = _recipe_usage(draft_recipes.entries, report.get("playable"))
    if edit:
        if locked_from:
            report["warnings"].append(f"site_id_suggestion: {locked_from!r} ignored, this edit keeps the site id {edit!r}")
        data, _problem = _load_yaml(body.yaml_text)
        yaml_site = (data or {}).get("site_id")
        if yaml_site is not None and yaml_site != edit:
            report["errors"].append(f"site_id: the yaml says {yaml_site!r} but this edit keeps the site id {edit!r}")
        report["edit"] = _edit_summary(edit, data)
    elif suggestion and not SITE_ID_RE.match(suggestion):
        report["errors"].append("site_id_suggestion: must match ^[a-z][a-z0-9_]{1,31}$")
    elif suggestion and suggestion in scfg.list_sites():
        report["errors"].append(f"site_id_suggestion: site {suggestion!r} already exists")
    elif not suggestion:
        report["warnings"].append("site_id_suggestion: empty (the admin must choose a site id)")
    report["valid"] = not report["errors"]
    report["criteria"]["config_errors"] = _criterion(len(report["errors"]), 0, "max")
    report["passed"] = all(c["ok"] for c in report["criteria"].values())
    if report["passed"]:
        report.pop("failing", None)
    else:
        report["failing"] = _failing(report)   # the criteria changed above (config_errors): recompute
    report["notes"] = (body.notes or "")[:4000]
    extra = {"handoff": site_handoff.clip_agent_text(body.handoff)} if (body.handoff or "").strip() else {}   # omitted = an earlier one stays
    onboard_store.update_draft(body.draft_id, status="ready", yaml_text=body.yaml_text, site_id_suggestion=suggestion,
                               report=report, error=None, provider_recipes=asked, **extra)
    onboard_store.append_event(body.draft_id, {"type": "submit", "passed": report["passed"],
                                               "errors": len(report["errors"]), "warnings": len(report["warnings"])})
    return {"draft_id": body.draft_id, "status": "ready", "passed": report["passed"], "valid": report["valid"],
            "errors": report["errors"], "warnings": report["warnings"], "criteria": report["criteria"],
            "playable": {k: (report.get("playable") or {}).get(k, 0) for k in ("checked", "resolved", "skipped")},
            **({"series": {k: report["series"].get(k) for k in ("checked", "with_episodes", "skipped", "hint") if k in report["series"]}}
               if report.get("series") else {}),
            "ingest": report.get("ingest"),
            **({"blocked": report["blocked"]} if report.get("blocked") else {}),
            **({"gate": report["gate"]} if report.get("gate") else {}),
            **({"failing": report["failing"]} if report.get("failing") else {}),
            "diagnostics": report.get("diagnostics") or {},
            **({"search": {k: report["search"].get(k) for k in ("query", "count", "found_known", "normalize_ok_ratio", "skipped", "error")
                           if k in report["search"]}} if report.get("search") else {}),
            "collections": [{k: e.get(k) for k in ("id", "role", "status", "count", "valid_count", "would_ingest", "normalize_ok", "errors")}
                            for e in report.get("collections") or []],
            "provider_recipes": [{k: e.get(k) for k in ("name", "valid", "used", "errors")} for e in report.get("provider_recipes") or []]}


# --- request bodies / routes ------------------------------------------------------------------------------------

class FetchBody(BaseModel):
    url: str = Field(..., max_length=2048)
    mode: Literal["auto", "http", "browser", "chrome"] = "auto"
    wait_for: Optional[str] = Field(None, max_length=200, description="CSS selector the browser waits for")
    referer: Optional[str] = Field(None, max_length=2048, description="Referer header (an http(s) URL, usually the detail "
                                   "page); setting it (or mode chrome) fetches with the Chrome TLS fingerprint instead of "
                                   "crawlee / the browser")


class QueryBody(BaseModel):
    page_id: str = Field(..., max_length=32)
    selector: str = Field(..., min_length=1, max_length=500)
    attr: Optional[str] = Field(None, max_length=64)
    limit: int = 10


class OutlineBody(BaseModel):
    page_id: str = Field(..., max_length=32)


class GrepBody(BaseModel):
    page_id: str = Field(..., max_length=32)
    pattern: str = Field(..., min_length=1, max_length=GREP_MAX_PATTERN)
    context: int = Field(120, ge=0, le=GREP_MAX_CONTEXT, description="characters kept before and after each match")
    limit: int = Field(10, ge=1, le=GREP_MAX_LIMIT)
    flags: Optional[str] = Field(None, max_length=8, description="regex flags: i, m, s (default none)")


class RecipeBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=40, description="recipe name, ^[a-z][a-z0-9_]{1,31}$ (the yaml's own name: must be the same)")
    yaml: str = Field(..., max_length=recipes.MAX_RECIPE_BYTES, description="the provider recipe yaml")
    mode: Optional[Literal["new", "update"]] = Field(None, description="update = a new version of a recipe that is in the library (only match.host_regex may "
                                                     "widen: use the recipe_yaml of match_providers); default new")


RECIPES_FIELD = dict(default=None, max_length=MAX_PROVIDER_RECIPES, description="new provider recipes [{name, yaml}] (at most 3) that join "
                     "the providers IN MEMORY: the site yaml may name them in providers:; the draft's stored recipes are always included")


class DiscoverSiteBody(BaseModel):
    url: str = Field(..., max_length=2048, description="the site's address (any page: the root page is read)")


class MatchProvidersBody(BaseModel):
    player_url: str = Field(..., max_length=2048, description="a player (embed / iframe) URL, e.g. a candidate url of test_resolvers / discover_site")
    referer: Optional[str] = Field(None, max_length=2048, description="the detail / episode page that embeds the player")
    detail_url: Optional[str] = Field(None, max_length=2048, description="same as referer (used when referer is empty)")


class TestProviderBody(BaseModel):
    recipe_yaml: str = Field(..., max_length=recipes.MAX_RECIPE_BYTES)
    sample_url: str = Field(..., max_length=2048, description="a player URL the recipe should read (a candidate URL of a detail page)")
    referer: Optional[str] = Field(None, max_length=2048, description="the detail / episode page the player is embedded in")


class TestConfigBody(BaseModel):
    yaml_text: str = Field(..., max_length=100_000)
    page_id: Optional[str] = Field(None, max_length=32)
    detail_page_id: Optional[str] = Field(None, max_length=32)
    collections: bool = Field(False, description="also check and fetch every yaml collections: entry (home sections)")
    playable: bool = Field(False, description="also follow up to 3 normalized items from their playback page to a stream "
                                              "(playable_ratio / series_have_episode_sources criteria; submit always does)")
    provider_recipes: Optional[list[RecipeBody]] = Field(**RECIPES_FIELD)
    baseline: bool = Field(False, description="repair mode: compare the yaml (its site_id = the site being repaired) with the "
                           "ACTIVE config and baseline of that site: no field dropped, attr / cast kept, fill not clearly lower "
                           "than the last good parse; adds baseline {ok, reasons} and the criterion baseline_ok")


class TestSearchBody(BaseModel):
    yaml_text: str = Field(..., max_length=100_000)
    query: Optional[str] = Field(None, max_length=SEARCH_QUERY_MAX, description="search text (at least 3 characters); omitted = the title of the "
                                 "first item of the draft's list page, which is then also the title that must be found (found_known)")
    page_id: Optional[str] = Field(None, max_length=32, description="stored list page the known title is taken from (skips the fetch)")
    detail_page_id: Optional[str] = Field(None, max_length=32, description="stored detail page: its title and URL are the known title "
                                          "when the list gives none")


class TestResolversBody(BaseModel):
    yaml_text: str = Field(..., max_length=100_000)
    detail_url: str = Field(..., max_length=2048)
    page_id: Optional[str] = Field(None, max_length=32, description="a stored detail page instead of fetching detail_url")
    detail_urls: Optional[list[Annotated[str, Field(max_length=2048)]]] = Field(
        None, max_length=4, description="more detail / episode pages to try after detail_url (sites serve different players per "
        "title); omitted = up to 2 more are sampled from the list page of the yaml, [] = detail_url only")
    list_page_id: Optional[str] = Field(None, max_length=32, description="stored list page to sample the extra pages from "
                                        "(default: the yaml's list page is fetched)")
    provider_recipes: Optional[list[RecipeBody]] = Field(**RECIPES_FIELD)


class SubmitBody(BaseModel):
    draft_id: str = Field(..., max_length=32)
    yaml_text: str = Field(..., max_length=100_000)
    site_id_suggestion: str = Field("", max_length=64)
    notes: str = Field("", max_length=4000)
    handoff: str = Field("", max_length=4000, description="site handoff note for the next edit / repair agent (new site: findings and "
                         "solutions, <= 25 lines; edit: ONE change entry, <= 8 lines); stored with the draft, written by save")
    page_id: Optional[str] = Field(None, max_length=32, description="stored list page (skips the re-fetch)")
    detail_page_id: Optional[str] = Field(None, max_length=32)
    provider_recipes: Optional[list[RecipeBody]] = Field(**RECIPES_FIELD)


@router.post("/fetch")
async def sandbox_fetch(body: FetchBody, _draft: str = Depends(_guard)) -> dict:
    return await _bounded(_do_fetch, body)


@router.post("/query")
async def sandbox_query(body: QueryBody, _draft: str = Depends(_guard)) -> dict:
    return await _bounded(_do_query, body)


@router.post("/grep")
async def sandbox_grep(body: GrepBody, _draft: str = Depends(_guard)) -> dict:
    return await _bounded(_do_grep, body)


@router.post("/outline")
async def sandbox_outline(body: OutlineBody, _draft: str = Depends(_guard)) -> dict:
    return await _bounded(_do_outline, body)


async def _remember(draft_id: str, kind: str, result: Any) -> None:
    """Hand the latest answer of a test tool to the draft's ``live`` record (``onboard_store.save_live``: the admin's step view
    ``scraper/onboard_pipeline`` reads it while the agent works). Skipped for a repair job's token (no draft); never raises."""
    if not onboard_store.valid_draft_id(draft_id):
        return
    try:
        await asyncio.get_running_loop().run_in_executor(None, onboard_store.save_live, draft_id, kind, result)
    except Exception:
        log.debug("live result of %s for %s not saved", kind, draft_id, exc_info=True)


@router.post("/test_config")
async def sandbox_test_config(body: TestConfigBody, draft_id: str = Depends(_guard)) -> dict:
    body.provider_recipes = _merge_recipes(draft_id, body.provider_recipes)   # the draft's stored recipes join the call's own
    edit = _edit_site(draft_id)   # an edit run tests the yaml as that registered site (collection ids, its code modules)
    fn = (functools.partial(_do_test_config, repair=True) if _is_repair(draft_id)
          else functools.partial(_do_test_config, site_hint=edit) if edit
          else functools.partial(_do_test_config, harden=True, **dict(zip(("skipped", "previous"), _draft_memory(draft_id)))))   # a new site: hardening
    result = await _bounded(_scoped(fn, edit), body)
    await _remember(draft_id, "test_config", result)
    return result


@router.post("/test_search")
async def sandbox_test_search(body: TestSearchBody, draft_id: str = Depends(_guard)) -> dict:
    result = await _bounded(_do_test_search, body, hint="the site's search request is slow; try again or use fetch: http")
    await _remember(draft_id, "test_search", result)
    return result


@router.get("/resolvers")
def sandbox_resolvers(_draft: str = Depends(_guard)) -> dict:
    return {"resolvers": resolvers.catalog(), "providers": registry.catalog()}


@router.post("/test_provider")
async def sandbox_test_provider(body: TestProviderBody, draft_id: str = Depends(_guard)) -> dict:
    fn = functools.partial(_do_test_provider, repair=True) if _is_repair(draft_id) else _do_test_provider
    result = await _bounded(fn, body, hint="a recipe with fetch: browser is slow; try fetch: http with a referer")
    await _remember(draft_id, "test_provider", result)
    return result


@router.post("/discover_site")
async def sandbox_discover_site(body: DiscoverSiteBody, draft_id: str = Depends(_guard)) -> dict:
    if _is_repair(draft_id) or _edit_site(draft_id):
        raise ApiError(403, "forbidden", "discover_site belongs to onboarding a NEW site (a repair / edit run works on a registered site)")
    return await _bounded(_do_discover_site, body, hint="the site is slow: fetch_page its pages by hand")


@router.post("/match_providers")
async def sandbox_match_providers(body: MatchProvidersBody, draft_id: str = Depends(_guard)) -> dict:
    return await _bounded(_do_match_providers, body, hint="the player page is slow: use test_provider with one recipe")


@router.post("/test_resolvers")
async def sandbox_test_resolvers(body: TestResolversBody, draft_id: str = Depends(_guard)) -> dict:
    body.provider_recipes = _merge_recipes(draft_id, body.provider_recipes)
    fn = functools.partial(_do_test_resolvers, repair=True) if _is_repair(draft_id) else _do_test_resolvers
    result = await _bounded(_scoped(fn, _edit_site(draft_id)), body,
                            hint="a resolver with fetch: browser is slow; try fewer resolver items or follow hops")
    await _remember(draft_id, "test_resolvers", result)
    return result


# --- repair mode (heal agent): load_site_config / submit_repair --------------------------------------------------

_SECRET_LINE = re.compile(r"(?im)^(\s*(?:cookie|authorization|proxy-authorization|x-api-key|api[_-]?key|token|secret|password)\s*:\s*).+$")
SITE_CONFIG_MAX = 100_000


def _redact(text: str) -> str:
    """A yaml text with the value of secret-looking keys (cookie, authorization, api key, token, password) masked: a site
    config should carry none, but the answer goes to an LLM."""
    return _SECRET_LINE.sub(lambda m: m.group(1) + "***", text)


def _recipe_row(entry: dict, with_yaml: bool) -> dict:
    data = entry["data"]
    match = data.get("match") if isinstance(data.get("match"), dict) else {}
    row = {"name": entry["name"], "version": entry["version"], "description": str(data.get("description") or "")[:200],
           "hosts": [x for x in (match.get("host_regex"), match.get("path_regex")) if x], "fetch": data.get("fetch") or "http"}
    if with_yaml:
        try:
            with open(entry["path"], "r", encoding="utf-8") as fh:
                row["yaml"] = _redact(fh.read())[:recipes.MAX_RECIPE_BYTES]
        except OSError:
            row["yaml"] = ""
    return row


def _do_site_config(site_id: str, recipe: str = "") -> dict:
    """Read-only: the ACTIVE yaml text, version, baseline and the approved provider recipes of a REGISTERED site (what a repair
    starts from). The yaml of the recipes the site names in ``providers:`` (and of ``recipe``) is included. Nothing is written."""
    if not SITE_ID_RE.match(site_id or "") or site_id not in scfg.list_sites():
        raise ApiError(404, "not_found", f"site {site_id!r} is not a registered site")
    cfg = scfg.load_site(site_id)
    try:
        with open(cfg.path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        raise ApiError(404, "not_found", f"the config file of {site_id!r} could not be read")
    base = cfg.baseline()
    good = base.get("last_good") or {}
    used = set(cfg.providers or [])
    wanted = {recipe} if recipe else set()
    rows = [_recipe_row(e, e["name"] in used or e["name"] in wanted) for e in scfg.list_recipes()]
    return {"site_id": site_id, "version": cfg.version,
            "yaml_text": _redact(text)[:SITE_CONFIG_MAX], "yaml_truncated": len(text) > SITE_CONFIG_MAX,
            "archived_versions": scfg.archived_versions(site_id)[-5:],
            "baseline": {**{k: base[k] for k in ("min_items", "min_fill_ratio", "critical_field_fill") if k in base},
                         "last_good": {k: good[k] for k in ("valid_count", "fill_ratio", "field_fill", "field_fill_all", "at") if k in good}},
            "providers": cfg.providers, "provider_recipes": rows,
            "handoff": site_handoff.read(site_id),
            "note": "yaml_text is the active config (version above); a repair keeps site_id, schema and normalize.key; "
                    "handoff = the site's handoff note (earlier findings, the admin's decisions, results): read it, keep its decisions, do not ask again"}


class SubmitRepairBody(BaseModel):
    site_id: str = Field(..., max_length=64, description="the registered site being repaired")
    yaml_text: Optional[str] = Field(None, max_length=100_000, description="the COMPLETE corrected site yaml (omit when only a recipe changes)")
    provider_recipes: Optional[list[RecipeBody]] = Field(**RECIPES_FIELD)
    notes: str = Field("", max_length=4000)
    handoff: str = Field("", max_length=2000, description="ONE change entry for the site handoff note (what changed, why, result; <= 8 lines)")


def _do_submit_repair(job_id: str, body) -> dict:
    """Record the repair proposal of a repair run (``DATA_DIR/onboard/repairs/<job_id>.json``). Nothing is applied here: the heal
    (``scraper/heal_agent.py``) checks the proposal against the baseline, the working examples and the other users of a changed
    recipe, and applies it only when ``heal_autoapply`` is on. The answer lists what is wrong with the proposal so far."""
    if not _is_repair(job_id):
        raise ApiError(403, "forbidden", "submit_repair belongs to repair runs (the token is of another kind of job)")
    site = (body.site_id or "").strip()
    if site not in scfg.list_sites():
        raise ApiError(404, "not_found", f"site {site!r} is not a registered site")
    problems: list[str] = []
    yaml_text = body.yaml_text or ""
    if yaml_text.strip():
        data, problem = _load_yaml(yaml_text)
        if problem:
            problems.append(problem)
        elif str(data.get("site_id") or site) != site:
            problems.append(f"yaml: site_id {data.get('site_id')!r} is not the repaired site {site!r}")
    items = _recipe_dicts(body.provider_recipes)
    if items:
        problems += _prepare_recipes(items, replace=True).errors
    record = onboard_store.save_repair(job_id, {"site_id": site, "yaml_text": yaml_text, "provider_recipes": items,
                                               "notes": (body.notes or "")[:4000],
                                               "handoff": site_handoff.clip_agent_text(getattr(body, "handoff", "")),
                                               "problems": [_clip(p, 300) for p in problems[:10]]})
    return {"job_id": job_id, "status": "recorded", "site_id": site, "has_yaml": bool(yaml_text.strip()),
            "recipes": [r["name"] for r in items], "valid": not problems, "errors": [_clip(p, 300) for p in problems[:10]],
            "submissions": (record or {}).get("submissions", 1),
            "note": "nothing was applied; the server tests the proposal against the baseline and the working examples first"}


@router.get("/site_config/{site_id}")
def sandbox_site_config(site_id: str, recipe: str = "", job_id: str = Depends(_guard)) -> dict:
    """Read-only. A repair run may read the site it repairs; an EDIT draft only the site it edits; a new-site draft nothing."""
    if not _is_repair(job_id):
        edit = _edit_site(job_id)
        if not edit:
            raise ApiError(403, "forbidden", "load_site_config belongs to edit and repair runs (a new-site draft has no registered site)")
        if site_id != edit:
            raise ApiError(403, "forbidden", f"this edit run may only read its own site {edit!r}")
    return _do_site_config(site_id, recipe.strip())


@router.post("/submit_repair")
def sandbox_submit_repair(body: SubmitRepairBody, draft_id: str = Depends(_guard)) -> dict:
    return _do_submit_repair(draft_id, body)


@router.post("/submit")
async def sandbox_submit(body: SubmitBody, draft_id: str = Depends(_guard)) -> dict:
    if body.draft_id != draft_id:
        raise ApiError(403, "forbidden", "the token belongs to another draft")
    return await _bounded(_do_submit, body)
