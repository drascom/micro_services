"""Agent-based heal: the onboarding skill in REPAIR mode fixes a registered site, the heal gates decide.

Two entry points, one agent:

* ``heal_site_playback(site, evidence=..., trigger=...)`` (called by ``scraper/playheal.py``; ``heal.heal_site_playback`` is the
  public name): the sources of one site keep failing the same way (``evidence`` of ``playheal.evaluate``) or the scan produced
  no episode sources at all (``evidence["kind"] == "no_sources"``). The agent may change the site yaml (page structure:
  selectors, ``resolvers:``, ``providers:``) and / or the provider library (a new recipe, or the complete new version of an
  existing one: the video host knowledge), and hands the proposal in with ``submit_repair``.
* ``drift_generate`` (the ``pi_agent`` provider of the classic selector heal): the same agent gets the list-page drift; only
  the selector block of its proposal goes on, into the unchanged gates of ``heal._heal_impl``.

The agent proposes, the server decides. A playback proposal is applied only when ALL of these hold: it is a mapping that keeps
site id, schema, the host of ``base_url``, the ``normalize`` key rules and every field (``heal.check_repair_proposal``); the
yaml / recipes validate without a NEW error; the list parse still meets the baseline (``test_config`` ``baseline``: fields
kept, fill not clearly below ``last_good``); the failing examples play again with it (at least ``MIN_PLAYABLE_RATIO``, resolved
through the sandbox exactly like a play request, in memory: nothing is written yet); the working examples of the site still
play; and every OTHER site that uses a new / changed provider recipe (``providers:`` names it, or its working sources went
through it) still plays its working examples (``regression: <site>`` otherwise). Applying follows the admin ``heal_autoapply``
switch (off = ``not_applied`` + the proposal in the ops record): a new site yaml version is ``scfg.save_new_version``, a recipe
``scfg.save_recipe`` (both archive the old version; ``rollback_config`` / ``<name>.vN.yaml`` bring it back). A repair that
needs code (signature, cookie, TLS, JavaScript API) is reported as such, never faked.

A repair is NARROW: the evidence points at one LAYER of the site (``layers_of``: list, detail, series_page, normalize, resolvers,
provider = the library recipe of a video host, fetch) and the proposal may only change the yaml keys of that layer
(``LAYERS``; the union when the evidence shows several) and, for the provider layer, only the recipes related to the failure
(a new recipe needs evidence of a host no provider covers). A change outside the layers is rejected before anything is tested:
``scope: touched <keys> outside layer <layers>``. The ops record carries ``layers`` and ``touched_keys`` / ``touched_paths``.

Nothing here raises (``heal_site_playback`` / ``drift_generate``); the agent runs through the shared runner ``pi_agent``.
"""
from __future__ import annotations

import copy
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

import yaml

from .. import db, llm_health
from . import config as scfg
from . import heal, onboard_store as store, pi_agent, state

log = logging.getLogger("scraper.heal_agent")

REGRESSION_SITES = 3        # other sites checked when a provider recipe is created / changed
REGRESSION_SAMPLES = 3      # working examples per site (the repaired site's own ok_examples included)
FIX_EXAMPLES = 3            # failing examples that must play again
MAX_AGENT_EVENTS = 60       # agent events kept in the ops heal record (the newest)
MAX_EVIDENCE_CHARS = 6000   # evidence JSON in the task message
PROPOSAL_YAML_CHARS = 12000  # proposal yaml kept in the ops record when it is not applied (the full text stays in repairs/<job>.json)
PROPOSAL_RECIPE_CHARS = 4000
NO_SOURCES = "no_sources"
SERIES_INVENTORY = "series_inventory"   # evidence of a scan whose series pages mostly could not be read (``playheal.record_crawl``; trigger ``crawl``)
STREAM_BLOCKED = "stream_blocked"       # evidence of streams the server resolves but the stream host refuses (``playheal.record_stream_blocked``)
SOURCELESS = (NO_SOURCES, SERIES_INVENTORY)   # evidence kinds without a failing PLAYBACK example: verified in the sandbox, not by playing examples


VERIFY_SECONDS_DEFAULT = 420


def verify_seconds() -> int:
    """Time the server-side verification (gates) of one proposal may take (``SCRAPER_HEAL_VERIFY_TIMEOUT``, default 420 s)."""
    return heal._int_env("SCRAPER_HEAL_VERIFY_TIMEOUT", VERIFY_SECONDS_DEFAULT)


# --- the agent run ----------------------------------------------------------------------------------------------

@dataclass
class AgentRun:
    job_id: str
    events: list
    result: pi_agent.RunResult
    record: Optional[dict]
    timeout: int
    seconds: float


def run_agent(site: str, first_message: str, timeout: int) -> AgentRun:
    """One repair run of the pi agent for ``site``: a repair job id, a sandbox token for it, the shared pi runner, the
    proposal the agent recorded with ``submit_repair`` (``record``, None when it never did). Never raises."""
    from ..routers import onboard_sandbox as sandbox
    started = time.monotonic()
    job_id = store.new_repair_id()
    token = sandbox.issue_token(job_id)
    events: list = []
    job = pi_agent.Job(job_id, first_message, "repair", pi_agent.REPAIR_TOOLS, {"DIZIFLIX_SITE_ID": site})
    try:
        result = pi_agent.run(job, token=token, timeout=float(timeout), on_event=events.append,
                              on_label=lambda label, phase: state.activity_update(site, "heal", label=label, phase=phase),
                              model_name=llm_health.heal_model())
    finally:
        sandbox.revoke_token(token)
    return AgentRun(job_id, events, result, store.load_repair(job_id), timeout, round(time.monotonic() - started, 1))


def agent_block(run: AgentRun) -> dict:
    """The agent log of the ops heal record in the onboarding event format (``kind`` tool | tool_result | say | error | retry);
    only the newest ``MAX_AGENT_EVENTS`` are kept (``events_dropped``)."""
    keep = run.events[-MAX_AGENT_EVENTS:]
    return {"events": [{"ts": e.get("t"), **e} for e in keep], "events_dropped": len(run.events) - len(keep),
            "turns": run.result.parser.turns, "job_id": run.job_id, "seconds": run.seconds}


def no_record_reason(run: AgentRun) -> str:
    """Why a run ended without a repair proposal."""
    r = run.result
    if r.timed_out:
        return f"agent timed out after {run.timeout}s"
    if r.crash:
        return f"agent could not run: {r.crash}"
    if r.exit_code not in (0, None):
        return "agent failed: " + (pi_agent.scrub(r.stderr, 300) or f"pi exit code {r.exit_code}")
    if r.parser.provider_error:
        return "agent provider error: " + r.parser.provider_error
    say = pi_agent.clip(r.parser.last_say, 300)
    return "agent finished without submit_repair" + (f": {say}" if say else "")


# --- layers: what the evidence points at, what a repair may therefore touch ----------------------------------------

AUTO_KEYS = ("version", "updated_at", "site_id")   # set by the server / checked by ``heal.check_repair_proposal``: never a "touched" key
#: layer -> (top-level yaml keys it may change, the evidence that points at it). The first column is the scope gate, the
#: second the diagnosis (``_layer_of_failure``); both are printed into references/heal.md (tools/gen_onboard_refs.py).
LAYERS = {
    "list": (("list", "collections"), "drift of the list page or of a home section (stage `list` / `collection` / `drift`)"),
    "detail": (("detail",), "the detail fields do not parse (stage `detail`)"),
    "series_page": (("series_page",), "the episode list of a series page (stage `series` / `inventory`; evidence `series_inventory`, where `normalize` joins)"),
    "normalize": (("normalize",), "titles without playable sources: normalize / episode_source (stage `normalize`, evidence `no_sources`; "
                                  "`series_page` joins, and `resolvers` when the yaml has none)"),
    "resolvers": (("resolvers", "providers"), "the player is not found on the page: no candidate (stage `discover`)"),
    "provider": (("providers",), "candidates found but no stream: the video HOST changed (host / type / quality; stage `player_page.*`, a "
                                 "provider stage) = a LIBRARY RECIPE; the site yaml may only change its `providers` names"),
    "fetch": (("fetch_mode",), "the page itself cannot be fetched (stage `page` / `fetch`)"),
}
LAYER_ORDER = tuple(LAYERS)
LAYER_KEYS = {name: keys for name, (keys, _doc) in LAYERS.items()}


def _layer_of_failure(f: dict) -> set:
    """The layer(s) one failing example points at (``stage`` / ``host`` / candidates of ``playheal`` evidence)."""
    stage = str(f.get("stage") or "").strip().lower()
    cands = [c for c in f.get("candidates") or [] if isinstance(c, dict)]
    if stage == "normalize":
        return {"normalize"}
    if stage.startswith(("list", "collection", "drift")):
        return {"list"}
    if stage.startswith("detail"):
        return {"detail"}
    if stage.startswith(("series", "inventory")):
        return {"series_page"}
    if stage in ("page", "fetch", "sayfa"):
        return {"fetch"}
    if stage == "discover":
        return {"resolvers"}
    if stage or f.get("host") or cands:
        return {"provider"}
    return {"resolvers", "provider"}   # the evidence names nothing (the library's suspect / broken fallback): both play layers


def layers_of(evidence: dict, cfg: Optional[scfg.SiteConfig] = None) -> list:
    """The layers the evidence points at, in ``LAYER_ORDER`` (the union when several failures point at different ones). A site
    without any episode source (``kind: no_sources``) is a ``normalize`` + ``series_page`` matter, and a ``resolvers`` one too
    when its active yaml has no ``resolvers:`` at all (nothing could play even with sources)."""
    layers: set = set()
    if str(evidence.get("kind") or "playback") == NO_SOURCES:
        layers |= {"normalize", "series_page"}
        if cfg is not None and not cfg.data.get("resolvers"):
            layers.add("resolvers")
    elif str(evidence.get("kind") or "playback") == STREAM_BLOCKED:   # the player is found and resolved, the stream host refuses: the video HOST side
        layers |= {"provider"}
    elif str(evidence.get("kind") or "playback") == SERIES_INVENTORY:   # series pages the inventory pass cannot read: the selectors / the key + title rules
        layers |= {"series_page", "normalize"}
    for f in evidence.get("failing") or []:
        if isinstance(f, dict):
            layers |= _layer_of_failure(f)
    if not layers:
        layers = {"resolvers", "provider"}
    return [name for name in LAYER_ORDER if name in layers]


def allowed_keys(layers: list) -> set:
    """The top-level yaml keys a repair of these layers may change."""
    return {key for name in layers for key in LAYER_KEYS.get(name, ())}


def touched(old: dict, new: dict) -> tuple:
    """``(top-level keys, inner paths)`` of the yaml that differ between the active config and a proposal (``AUTO_KEYS`` left
    out; the inner paths are ``heal.selector_diff`` paths, at most 40)."""
    keys = sorted(k for k in set(old) | set(new) if k not in AUTO_KEYS and old.get(k) != new.get(k))
    paths = [d["path"] for d in heal.selector_diff({k: old.get(k) for k in keys}, {k: new.get(k) for k in keys})]
    return keys, paths[:40]


def _host_covered(host: str) -> bool:
    """Does a provider of the library (a code module or a recipe) own this host?"""
    import re
    from .providers import registry
    for provider in registry.providers():
        try:
            pattern = getattr(provider, "host_regex", None)
            if pattern:
                if re.search(pattern, host, re.I):
                    return True
            elif provider.matches(f"https://{host}/"):
                return True
        except Exception:
            continue
    return False


def evidence_hosts(evidence: dict) -> set:
    """Hosts the failing examples name (their own ``host`` and the failing candidates' hosts)."""
    hosts: set = set()
    for f in evidence.get("failing") or []:
        if not isinstance(f, dict):
            continue
        if f.get("host"):
            hosts.add(str(f["host"]).lower())
        for c in f.get("candidates") or []:
            if isinstance(c, dict) and c.get("host") and not c.get("ok"):
                hosts.add(str(c["host"]).lower())
    return hosts


def new_hosts(evidence: dict) -> set:
    """The hosts of the evidence that NO provider covers: only these justify a new recipe."""
    return {h for h in evidence_hosts(evidence) if not _host_covered(h)}


def related_recipes(cfg: scfg.SiteConfig, evidence: dict) -> set:
    """Recipes a repair of this failure may change: the ones the site's ``providers:`` names, the ones that cover a host of the
    evidence and the ones whose stream label a failing candidate carries."""
    import re
    names = set(cfg.providers or [])
    hosts = evidence_hosts(evidence)
    labels = {str(c.get("provider") or "") for f in evidence.get("failing") or [] if isinstance(f, dict)
              for c in f.get("candidates") or [] if isinstance(c, dict)} - {""}
    for entry in scfg.list_recipes():
        data = entry["data"]
        match = data.get("match") if isinstance(data.get("match"), dict) else {}
        if str(data.get("label") or entry["name"]) in labels or entry["name"] in labels:
            names.add(entry["name"])
        elif match.get("host_regex") and any(re.search(match["host_regex"], h, re.I) for h in hosts):
            names.add(entry["name"])
    return names


def scope_violation(cfg: scfg.SiteConfig, evidence: dict, layers: list, new_data: Optional[dict], plan: list) -> Optional[str]:
    """``scope: ...`` when the proposal touches anything outside the layers of the evidence, else None. Yaml: every changed
    top-level key must belong to a layer. Recipes: only in the ``provider`` layer; an existing recipe only when it is related to
    the failure; a NEW recipe only for a host no provider covers, and it must cover one of those hosts."""
    label = "+".join(layers)
    if new_data is not None:
        outside = [k for k in touched(cfg.data, new_data)[0] if k not in allowed_keys(layers)]
        if outside:
            return f"scope: touched {', '.join(outside)} outside layer {label}"
    if not plan:
        return None
    names = ", ".join(p["name"] for p in plan)
    if "provider" not in layers:
        return f"scope: touched provider recipe {names} outside layer {label}"
    related, fresh = related_recipes(cfg, evidence), new_hosts(evidence)
    import re
    for entry in plan:
        if entry["action"] == "update":
            if entry["name"] not in related:
                return f"scope: touched provider recipe {entry['name']} outside layer {label} (it is not a provider of this failure)"
        else:
            if not fresh:
                return f"scope: new provider recipe {entry['name']} without new-host evidence (every failing host already has a provider)"
            pattern = entry["provider"].host_regex
            if not any(re.search(pattern, h, re.I) for h in fresh):
                return f"scope: new provider recipe {entry['name']} does not cover a new host of the evidence ({', '.join(sorted(fresh))})"
    return None


# --- the task message -------------------------------------------------------------------------------------------

def kind_of(evidence: dict) -> str:
    return str(evidence.get("kind") or "playback")


def _slim_evidence(evidence: dict) -> dict:
    out = {k: evidence.get(k) for k in ("kind", "window") if evidence.get(k)}
    out["failing"] = []
    for f in (evidence.get("failing") or [])[:8]:
        if not isinstance(f, dict):
            continue
        entry = {k: f.get(k) for k in ("kind", "episode_id", "locator", "error", "stage", "host", "probe") if f.get(k)}
        cands = [{k: c.get(k) for k in ("label", "provider", "ok", "stage", "host", "error") if c.get(k) not in (None, "")}
                 for c in (f.get("candidates") or [])[:4] if isinstance(c, dict)]
        if cands:
            entry["candidates"] = cands
        out["failing"].append(entry)
    out["ok_examples"] = [{"locator": o.get("locator")} for o in (evidence.get("ok_examples") or [])[:3] if isinstance(o, dict)]
    if isinstance(evidence.get("coverage"), dict):
        out["coverage"] = evidence["coverage"]
    if kind_of(evidence) == STREAM_BLOCKED or isinstance(evidence.get("issue"), dict):
        for entry, f in zip(out["failing"], [f for f in (evidence.get("failing") or [])[:8] if isinstance(f, dict)]):
            if isinstance(f.get("stream"), dict):
                entry["stream"] = f["stream"]   # host / type, the probe's answer (http, ct, body start) and the headers it used
        for key in ("stream", "hint", "issue"):
            if evidence.get(key):
                out[key] = evidence[key]
    text = json.dumps(out, ensure_ascii=False, default=str)
    while len(text) > MAX_EVIDENCE_CHARS and out["failing"]:   # drop the oldest failing examples until it fits
        out["failing"].pop()
        text = json.dumps(out, ensure_ascii=False, default=str)
    return out


def repair_message(cfg: scfg.SiteConfig, evidence: dict, trigger: str) -> str:
    """First message of a playback repair run: ``/skill:`` expands the onboarding skill; the text tells it to follow
    ``references/heal.md`` (repair mode) and what is wrong."""
    kind = str(evidence.get("kind") or "playback")
    window = evidence.get("window") if isinstance(evidence.get("window"), dict) else {}
    if kind == SERIES_INVENTORY:
        problem = (f"{window.get('failed')} of the {window.get('n')} series pages the last scan's inventory pass tried to read failed (the page is "
                   "no series page any more, or no episode could be found on it). The site's `series_page` selectors / regexes "
                   "(row_selector, fields.url, episode_url_regex, series_url_regex, series_slug_regex, same_series_regex) or the series "
                   "key / title rules (normalize) no longer fit. Failing examples are the series pages (`locator`); ok_examples still "
                   "read. Verify with test_config(playable: true): the criterion series_inventory_ok.")
    elif kind == STREAM_BLOCKED:
        stream = evidence.get("stream") if isinstance(evidence.get("stream"), dict) else {}
        problem = (f"{window.get('failed')} different sources of this site resolve to a stream the stream host `{stream.get('host')}` "
                   f"({stream.get('type')}) REFUSES (HTTP {stream.get('http') or '?'}; `failing[].stream` = what the server's own probe "
                   "got: status, content type, start of the body such as `security error`, the request headers it sent). The player "
                   "page and the resolution are fine, so the fix is the video host's recipe in the library: "
                   f"{evidence.get('hint') or ''} Verify with test_provider on >= 3 player URLs AND check that the stream URL it "
                   "returns answers (fetch_page it with the stream headers). The gate probes the stream again, a stream that is "
                   "still refused is rejected.")
    elif kind == NO_SOURCES:
        problem = (f"The last scan wrote {window.get('failed')} of {window.get('n')} series items WITHOUT any episode video source "
                   "(nothing to play: the client shows \"Bölümler alınamadı\"). The site's normalize has no episode_source, or "
                   "the site needs the series-page inventory (series_page) or resolvers. Failing examples are the series pages "
                   "(`locator`). Verify with test_config(playable: true): normalize.with_video_sources / "
                   "series_without_sources and the criteria series_have_episode_sources and playable_ratio.")
    elif isinstance(evidence.get("issue"), dict):   # the playback issue ledger: clients could not play, the server's diagnosis is in `issue`
        issue = evidence["issue"]
        problem = (f"{issue.get('sources')} different sources (episodes) of this site could not be played by real users "
                   f"(`{issue.get('code')}`: {issue.get('label')}; stream host `{issue.get('host') or '?'}` ({issue.get('stream_type') or '?'}), "
                   f"provider `{issue.get('provider') or '?'}`; the server's own note: {issue.get('note')}). The page and the resolution "
                   "worked (a stream was found), so the stream the recipe returns is the suspect: a WRONG stream (a promo clip / ad / "
                   "other video than the episode: check the media's duration against the episode; fix the recipe's `extract` / `follow` so it picks the episode file), a "
                   "stream the host refuses without a Referer / Origin (`stream_headers`, `stream_proxy: true`), or a changed host. Fetch one "
                   "failing player page (`locator`) with fetch_page and look at which media URLs it holds. Verify with test_provider on >= 3 "
                   "examples; the gate checks that the failing examples resolve" + (" and that the stream answers." if evidence.get("probe") else ".")
                   + " If it needs a signature, a cookie or a TLS fingerprint, submit nothing and report `needs code: <host>`.")
    else:
        problem = (f"{window.get('failed')} of the last {window.get('n')} playback attempts of this site failed in the same way "
                   "(stage / host / error in the evidence). The failing examples are playback pages (`locator`); ok_examples still "
                   "play. Find out whether the PAGE structure changed (site yaml: resolvers / selectors) or the VIDEO HOST changed "
                   "(provider recipe), or whether it needs code.")
    probes = [f.get("probe") for f in evidence.get("failing") or [] if isinstance(f, dict) and isinstance(f.get("probe"), dict)]
    if probes:   # content, not host: what the server measured on the failing streams
        problem += (" `failing[].probe` = what the server measured on the stream of that example (duration_min vs expected_min, duration_match "
                    "ok|short|long|unknown, segments, encrypted, needs_referer). `short` = a clip / ad / trailer: pick another media URL. "
                    + ("A probe with `duration_match: ok` is the REAL episode: if it is rejected / refused / not picked, write the rule / recipe "
                       "that accepts it BEFORE you consider `needs code`." if any(p.get("duration_match") == "ok" for p in probes) else ""))
    layers = layers_of(evidence, cfg)
    scope = (f"Diagnosed layer(s): {', '.join(layers)}. State the layer you diagnose in your first message. You may change ONLY these "
             f"top-level keys of the site yaml: {', '.join(sorted(allowed_keys(layers)))}."
             + (" A provider recipe change is limited to a recipe related to the failure (the site's `providers:`, the host or stream "
                "label in the evidence); a NEW recipe only for a host no provider covers (new-host evidence)."
                if "provider" in layers else " Do not touch provider recipes.")
             + " Anything outside is rejected (scope gate) and nothing is applied.")
    return (f"/skill:{pi_agent.SKILL_NAME} REPAIR MODE\n"
            f"site_id: {cfg.site_id}\nsite_url: {cfg.base_url}\ntrigger: {trigger}\nproblem: {kind}\nlayers: {', '.join(layers)}\n\n"
            "This is NOT an onboarding. Read references/heal.md of the skill and follow it. Start with load_site_config("
            f"\"{cfg.site_id}\"); change only what is broken; verify with test_config(baseline: true, playable: true) and "
            "test_provider on at least 3 different examples; finish with submit_repair (or `needs code: <host>` in notes).\n\n"
            f"{scope}\n\n{problem}\n\nEvidence (JSON):\n{json.dumps(_slim_evidence(evidence), ensure_ascii=False, default=str)}")


def drift_message(cfg: scfg.SiteConfig, page: str, reasons: list) -> str:
    """First message of a list-page drift run (the ``pi_agent`` provider of the classic heal)."""
    return (f"/skill:{pi_agent.SKILL_NAME} REPAIR MODE\n"
            f"site_id: {cfg.site_id}\nsite_url: {cfg.base_url}\ntrigger: drift\nproblem: drift\n\n"
            "This is NOT an onboarding. Read references/heal.md of the skill and follow it. Start with load_site_config("
            f"\"{cfg.site_id}\").\n\nDiagnosed layer: {page}. State it in your first message. You may change ONLY the top-level "
            f"keys {', '.join(LAYER_KEYS.get(page, (page,)))} of the site yaml; anything outside is rejected.\n\n"
            f"The {page} page of the site no longer parses: the CSS selectors of `{page}` stopped matching "
            f"(drift reasons: {json.dumps([str(r)[:200] for r in reasons][:6], ensure_ascii=False)}). Fetch "
            f"{cfg.base_url.rstrip('/')}/{(cfg.list_url or '').lstrip('/')} with fetch_page, find the new selectors "
            "with query_html / outline_page, and fix ONLY the "
            f"`{page}:` block (row_selector and field selectors; keep every field and its attr / cast). Verify with "
            "test_config(yaml_text, baseline: true) and submit the complete yaml with submit_repair.")


# --- the pi_agent provider of the classic selector heal ---------------------------------------------------------

def drift_generate(cfg: scfg.SiteConfig, page: str, html: str, reasons: list, timeout: int) -> dict:
    """``heal._provider_generate`` for ``SCRAPER_HEAL_PROVIDER=pi_agent``: run the repair agent on the drift and return the
    selector block of its proposal as yaml text (``{"status": "ok", "text": ..., "agent": ...}``) so ``heal._heal_impl`` applies
    its own gates; or a ``heal_failed`` dict. Never raises."""
    try:
        from ..routers import onboard_sandbox as sandbox
        run = run_agent(cfg.site_id, drift_message(cfg, page, reasons), timeout)
        agent = agent_block(run)
        record = run.record
        if record is None:
            return heal._fail(no_record_reason(run), agent=agent)
        yaml_text = str(record.get("yaml_text") or "")
        if not yaml_text.strip():
            return heal._fail("agent proposed no yaml" + (f": {pi_agent.clip(record.get('notes'), 200)}" if record.get("notes") else ""),
                              agent=agent)
        data, problem = sandbox._load_yaml(yaml_text)
        if problem:
            return heal._fail(f"could not parse selector yaml from the agent proposal ({problem})", agent=agent)
        keys, paths = touched(cfg.data, data)
        outside = [k for k in keys if k not in LAYER_KEYS.get(page, (page,))]
        if outside:   # the drift of one page is that page's selectors: nothing else may move with it
            return heal._fail(f"scope: touched {', '.join(outside)} outside layer {page}", agent=agent,
                              scope={"layers": [page], "touched_keys": keys, "touched_paths": paths})
        part = data.get(page) if isinstance(data.get(page), dict) else None
        if not part or not isinstance(part.get("fields"), dict):
            return heal._fail(f"the agent proposal has no {page}.fields block", agent=agent)
        block = {k: part[k] for k in ("row_selector", "fields") if k in part and (page == "list" or k == "fields")}
        return {"status": "ok", "text": "```yaml\n" + yaml.safe_dump(block, allow_unicode=True, sort_keys=False) + "```", "agent": agent,
                "scope": {"layers": [page], "touched_keys": keys, "touched_paths": paths}}
    except Exception as exc:
        return heal._fail(f"pi_agent heal error: {type(exc).__name__}: {pi_agent.scrub(exc, 200)}")


# --- playback / no-sources repair -------------------------------------------------------------------------------

def _reasons(evidence: dict) -> list[str]:
    """Short why-lines of the ops heal record."""
    window = evidence.get("window") if isinstance(evidence.get("window"), dict) else {}
    if evidence.get("kind") == NO_SOURCES:
        return [f"{window.get('failed')} of {window.get('n')} series items without episode sources"]
    if evidence.get("kind") == SERIES_INVENTORY:
        return [f"{window.get('failed')} of {window.get('n')} series pages could not be read"]
    if evidence.get("kind") == STREAM_BLOCKED:   # short Turkish reason for the admin heal record
        failing = [f for f in evidence.get("failing") or [] if isinstance(f, dict)]
        stream = evidence.get("stream") if isinstance(evidence.get("stream"), dict) else {}
        out = [(failing[0].get("error") if failing and failing[0].get("error") else "akış erişilemiyor")]
        out.append(f"akış sunucusu {stream.get('host') or '?'} ({window.get('failed')} kaynak)")
        return out
    if isinstance(evidence.get("issue"), dict):   # the playback issue ledger: short Turkish reason for the admin heal record
        issue = evidence["issue"]
        return [f"oynatma sorunu: {issue.get('label') or issue.get('code')}", f"{issue.get('sources')} kaynak"
                + (f" · akış sunucusu {issue.get('host')}" if issue.get("host") else "")]
    out = [f"{window.get('failed')} of {window.get('n')} recent sources failed"]
    failing = [f for f in evidence.get("failing") or [] if isinstance(f, dict)]
    for key in ("stage", "host", "error"):
        counts: dict[str, int] = {}
        for f in failing:
            if f.get(key):
                counts[str(f[key])[:80]] = counts.get(str(f[key])[:80], 0) + 1
        if counts:
            value, n = max(counts.items(), key=lambda kv: kv[1])
            out.append(f"{key} {value} ({n})")
    return out


def heal_site_playback(site_id: str, *, evidence: dict, trigger: str = "playback") -> dict:
    """See ``heal.heal_site_playback``. Never raises."""
    try:
        return _tracked(site_id, evidence if isinstance(evidence, dict) else {}, trigger)
    except Exception as exc:
        return {**heal._fail(f"unexpected heal error: {exc}"), "outcome": heal.FAILED, "applied": False}


def _tracked(site: str, evidence: dict, trigger: str) -> dict:
    base = {"site": site, "trigger": trigger, "reasons": _reasons(evidence), "provider": "pi_agent",
            "model": heal._env("SCRAPER_HEAL_MODEL", "") or None, "page": "playback", "evidence": evidence}
    skipped = heal.cooldown_skip(site, trigger, base)
    if skipped:
        return skipped
    owned = state.activity_start(site, "heal", trigger)
    started_at, t0 = state._now(), time.monotonic()
    trace: dict = {}
    try:
        result = _repair_impl(site, evidence, trigger, trace)
    except Exception as exc:   # never crash the playback thread
        log.exception("playback heal of %s failed", site)
        result = heal._fail(f"unexpected heal error: {exc}")
    finally:
        if owned:
            state.activity_end(site, "heal")
    result.setdefault("applied", False)
    outcome = heal.settle(site, result)
    extra = {k: trace[k] for k in ("agent", "layers", "touched_keys", "touched_paths", "recipes", "proposal", "regression", "verified")
             if trace.get(k)}
    entry = state.record_ops_heal({
        **base, "at": started_at, "outcome": outcome, "applied": bool(result.get("applied")),
        "new_version": result.get("new_version"), "error": result.get("reason"),
        "before": trace.get("before"), "after": trace.get("after"),
        "diff": heal.selector_diff(trace.get("before"), trace.get("after")),
        "duration": round(time.monotonic() - t0, 2), **extra,
    })
    if result.get("applied"):
        _note_handoff(site, result, trace, entry)
    return result


def _note_handoff(site: str, result: dict, trace: dict, entry: Any) -> None:
    """An APPLIED repair adds one change entry to the site's handoff note (the agent's ``handoff`` text, else its notes), titled with the heal
    record id. Never raises."""
    try:
        from . import site_handoff
        info = trace.get("handoff") or {}
        layers = ", ".join(str(x) for x in (trace.get("layers") or [])[:4])
        recipes = ", ".join(f"{r.get('name')} v{r.get('version')}" for r in (result.get("recipes") or [])[:3] if isinstance(r, dict))
        text = "\n".join(x for x in (str(info.get("text") or ""), f"Katman: {layers}" if layers else "",
                                     f"Tarif: {recipes}" if recipes else "") if x)
        data = None
        try:
            cfg = scfg.load_site(site)
            data, version = cfg.data, cfg.version
        except Exception:
            version = result.get("new_version")
        rid = (entry or {}).get("id") if isinstance(entry, dict) else None
        site_handoff.append_change(site, text or "(açıklama yok)", {"state": site_handoff.state_facts(data, version)} if data else None,
                                   title=f"Heal {rid or info.get('job') or ''} v{version}".strip())
    except Exception:
        log.warning("heal_agent: handoff note of %s not written", site, exc_info=True)


def _repair_impl(site: str, evidence: dict, trigger: str, trace: dict) -> dict:
    if not heal._enabled():
        return heal._fail("heal disabled (SCRAPER_HEAL_ENABLED)")
    try:
        cfg = scfg.load_site(site)
    except FileNotFoundError:
        return heal._fail(f"unknown site {site!r}")
    if str(evidence.get("kind") or "playback") != NO_SOURCES and not [f for f in evidence.get("failing") or [] if isinstance(f, dict)]:
        return heal._fail("no evidence to repair from: the evidence has no failing example")   # no agent run (and no LLM cost) for nothing
    trace["layers"] = layers_of(evidence, cfg)
    try:   # what the server MEASURED on the failing examples' streams (duration vs the episode's length): content, not host, says if it is the episode
        from ..library import streamprobe
        streamprobe.attach_probes(evidence)
    except Exception:
        log.warning("heal_agent: probes not attached", exc_info=True)
    run = run_agent(site, repair_message(cfg, evidence, trigger), heal.agent_timeout())
    trace["agent"] = agent_block(run)
    if run.record is None:
        return heal._fail(no_record_reason(run))
    return _gate_and_apply(cfg, evidence, run.record, trace)


# --- the gates --------------------------------------------------------------------------------------------------

def _core(data: dict) -> dict:
    return {k: v for k, v in data.items() if k not in ("version", "updated_at")}


def _changed_parts(old: dict, new: dict) -> tuple[dict, dict]:
    """The top-level keys of the yaml that differ: ``(before, after)`` for the ops record."""
    keys = sorted(k for k in set(old) | set(new) if k not in ("version", "updated_at") and old.get(k) != new.get(k))
    return {k: old.get(k) for k in keys}, {k: new.get(k) for k in keys}


def _static_errors(sb: Any, data: dict, extra_names: list, keep_normalize: bool) -> list[str]:
    """The sandbox's no-network validators over a yaml (the same ones ``test_config`` reports as ``errors``)."""
    errors = list(sb._check_core(data)[0]) + list(sb._check_fields(data))
    errors += list(sb._check_playback(data, extra_names)[0]) + list(sb._url_params_errors(data))
    if keep_normalize:
        errors += list(sb._check_normalize(data))
    return errors


def _plan_recipes(prepared: Any) -> list[dict]:
    """The recipes of the proposal that really change the library: new ones and updates of an existing recipe whose content
    differs (``[{name, provider, action, old}]``)."""
    plan = []
    known = set(scfg.recipe_names())
    for provider in prepared.providers:
        if provider.name in known:
            try:
                old = scfg.load_recipe(provider.name)
            except Exception:
                old = None
            if old is not None and _core(old) == _core(provider.data):
                continue
            plan.append({"name": provider.name, "provider": provider, "action": "update", "old": old})
        else:
            plan.append({"name": provider.name, "provider": provider, "action": "create", "old": None})
    return plan


def _cfg_with(cfg: scfg.SiteConfig, data: Optional[dict], extra: list) -> scfg.SiteConfig:
    """``cfg`` (or the proposed ``data`` of its site) as an in-memory config whose providers include the proposed recipes."""
    out = scfg.SiteConfig(site_id=cfg.site_id, data=copy.deepcopy(data if data is not None else cfg.data), path=cfg.path)
    out.extra_providers = list(extra)
    return out


def _follow(sb: Any, cfg: scfg.SiteConfig, locator: str, deadline: float, probe: bool = False) -> dict:
    """One example through the sandbox playback path (page -> discover -> providers), in memory; never raises. ``probe`` (a
    ``stream_blocked`` repair): a resolved stream only counts when it is also REACHABLE (the server's own probe with the stream's
    headers, ``streamdiag``), else ``ok`` is false with the reason."""
    if time.monotonic() >= deadline - 1:
        return {"ok": False, "error": "verification ran out of time", "timeout": True}
    raw: list = []
    until = min(deadline, time.monotonic() + sb.PLAYABLE_SAMPLE_SECONDS)
    result = (sb._follow_playback(cfg, locator, until, site_id=cfg.site_id, raw=raw) if probe
              else sb._follow_playback(cfg, locator, until, site_id=cfg.site_id))
    if probe and result.get("ok"):
        from ..library import streamdiag
        verdict = streamdiag.quick_check(raw or [], timeout=max(1.0, min(streamdiag.config.STREAM_DIAG_TIMEOUT, deadline - time.monotonic())))
        if verdict is None or not (verdict.get("code") == "reachable" or verdict.get("learn")):
            code = (verdict or {}).get("code") or "no answer"
            http = (verdict or {}).get("http")
            return {**result, "ok": False, "error": f"stream still refused ({code}{f', HTTP {http}' if http else ''})"}
    return result


def _healthy_samples(site: str, labels: set, limit: int) -> list[dict]:
    """Working examples of ``site`` from the library (``status = healthy`` page sources, newest success first); the ones whose
    stream went through a provider labelled ``labels`` come first."""
    try:
        rows = db.query("SELECT id,locator,resolved_payload FROM video_sources WHERE source=? AND resolver='page' AND kind!='trailer' "
                        "AND status='healthy' ORDER BY last_success_at DESC LIMIT 60", (site,))
    except Exception:
        return []
    through, other = [], []
    for row in rows:
        providers: set = set()
        try:
            for st in (json.loads(row["resolved_payload"]) if row["resolved_payload"] else {}).get("streams") or []:
                if isinstance(st, dict) and st.get("provider"):
                    providers.add(str(st["provider"]))
        except (TypeError, ValueError, AttributeError):
            pass
        (through if providers & labels else other).append({"source_id": row["id"], "locator": row["locator"], "through": bool(providers & labels)})
    return (through + other)[:limit]


def _other_users(plan: list, exclude: str) -> list[tuple[str, list[dict]]]:
    """Other sites that use one of the created / changed recipes (their ``providers:`` names it, or, without a ``providers:``
    list, working sources of theirs went through it) with the working examples to re-check: at most REGRESSION_SITES sites x
    REGRESSION_SAMPLES examples."""
    if not plan:
        return []
    names = {p["name"] for p in plan}
    labels = {p["provider"].label for p in plan} | names
    out: list[tuple[str, list[dict]]] = []
    for sid in scfg.list_sites():
        if sid == exclude or len(out) >= REGRESSION_SITES:
            continue
        try:
            other = scfg.load_site(sid)
        except Exception:
            continue
        named = bool(names & set(other.providers or []))
        samples = _healthy_samples(sid, labels, REGRESSION_SAMPLES)
        if not named:   # no providers: list = every provider may serve it: only a site whose streams really came through the recipe
            samples = [s for s in samples if s["through"]]
        if samples:
            out.append((sid, samples))
    return out


def _fail(reason: str, trace: dict, **extra: Any) -> dict:
    trace.setdefault("verified", {})["rejected"] = reason
    return heal._fail(reason, **extra)


def _gate_and_apply(cfg: scfg.SiteConfig, evidence: dict, record: dict, trace: dict) -> dict:
    """Validate the recorded proposal against every gate; apply it when they all pass and ``heal_autoapply`` is on."""
    from ..routers import onboard_sandbox as sb
    site, kind = cfg.site_id, str(evidence.get("kind") or "playback")
    notes = pi_agent.clip(record.get("notes"), 600)
    trace["handoff"] = {"text": str(record.get("handoff") or "") or notes, "job": record.get("job_id"), "kind": kind}   # -> the site handoff note when applied
    yaml_text = str(record.get("yaml_text") or "")
    items = [r for r in record.get("provider_recipes") or [] if isinstance(r, dict) and r.get("name")]
    if str(record.get("site_id") or site) != site:
        return _fail(f"proposal rejected: it is for site {record.get('site_id')!r}", trace)
    if not yaml_text.strip() and not items:
        return _fail("agent proposed no change" + (f": {notes}" if notes else ""), trace)
    deadline = time.monotonic() + verify_seconds()

    # --- the proposal itself: recipes, yaml
    prepared = sb._prepare_recipes(items, replace=True)
    if prepared.errors:
        return _fail("proposal rejected: " + "; ".join(prepared.errors[:3]), trace)
    plan = _plan_recipes(prepared)
    extra = [p["provider"] for p in plan]
    new_data: Optional[dict] = None
    if yaml_text.strip():
        data, problem = sb._load_yaml(yaml_text)
        if problem:
            return _fail(f"proposal rejected: {problem}", trace)
        data = {k: v for k, v in dict(data).items() if k not in ("version", "updated_at")}
        data.setdefault("site_id", site)
        if _core(data) != _core(cfg.data):
            new_data = data
    if new_data is None and not plan:
        return _fail("agent proposed no effective change (the yaml and the recipes equal the active ones)"
                     + (f": {notes}" if notes else ""), trace)
    if kind in SOURCELESS and new_data is None:
        return _fail("proposal rejected: a site without episode sources / readable series pages needs a yaml change (normalize.episode_source / "
                     "series_page / resolvers), the proposal changes none", trace)
    layers = trace.get("layers") or layers_of(evidence, cfg)
    trace["layers"] = layers
    if new_data is not None:
        trace["touched_keys"], trace["touched_paths"] = touched(cfg.data, new_data)
    bad_scope = scope_violation(cfg, evidence, layers, new_data, plan)   # the narrowest check first: it needs no network
    if bad_scope:
        return _fail(bad_scope, trace)
    if new_data is not None:
        bad = heal.check_repair_proposal(cfg, new_data)
        if bad:
            return _fail(f"proposal rejected: {bad[0]}", trace)
        names = [p.name for p in prepared.providers]
        keep_norm = bool(cfg.data.get("normalize"))
        old_errors = set(_static_errors(sb, cfg.data, names + scfg.recipe_names(), keep_norm))
        new_errors = [e for e in _static_errors(sb, new_data, names + scfg.recipe_names(), keep_norm) if e not in old_errors]
        if new_errors:
            return _fail(f"proposal rejected: {pi_agent.clip(new_errors[0], 300)}", trace)
    new_cfg = _cfg_with(cfg, new_data, list(prepared.providers))

    verified: dict[str, Any] = trace.setdefault("verified", {})
    # --- list part vs baseline (and, for a site without sources, the playable chain of the generic normalizer)
    if new_data is not None:
        report = sb._analyze(yaml.safe_dump(new_data, allow_unicode=True, sort_keys=False), None, None, deadline,
                             playable=(kind in SOURCELESS), draft_recipes=prepared, baseline=True)
        base = report.get("baseline") or {"ok": False, "reasons": ["no baseline result"]}
        verified["baseline"] = {"ok": bool(base.get("ok")), "reasons": list(base.get("reasons") or [])[:4]}
        if not base.get("ok"):
            return _fail("proposal rejected: " + pi_agent.clip("; ".join(base.get("reasons") or ["baseline"]), 300), trace)
        if kind in SOURCELESS:
            crit = report.get("criteria") or {}
            wanted = ("series_inventory_ok",) if kind == SERIES_INVENTORY else ("series_have_episode_sources", "playable_ratio")
            need = [c for c in wanted if not (crit.get(c) or {}).get("ok")]
            verified["criteria"] = {c: (crit.get(c) or {}).get("value") for c in wanted}
            if need:
                return _fail("proposal rejected: criteria not met in the sandbox: " + ", ".join(need), trace)
    # --- the failing examples play again (not for a no-sources site: its examples have no playback page yet)
    if kind not in SOURCELESS:
        locators = list(dict.fromkeys(str(f.get("locator")) for f in evidence.get("failing") or [] if isinstance(f, dict) and f.get("locator")))
        picks = locators[:FIX_EXAMPLES]
        if not picks:
            return _fail("proposal rejected: the evidence has no failing example to verify the repair with", trace)
        results = [_follow(sb, new_cfg, loc, deadline, probe=(kind == STREAM_BLOCKED or bool(evidence.get("probe")))) for loc in picks]
        ok = sum(1 for r in results if r.get("ok"))
        verified["failing"] = {"checked": len(results), "resolved": ok}
        if ok == 0 or round(ok / len(results), 2) < sb.MIN_PLAYABLE_RATIO:
            first = next((r.get("error") for r in results if not r.get("ok")), "")
            return _fail(f"proposal rejected: only {ok} of {len(results)} failing examples play with it ({pi_agent.clip(first, 160)})", trace)
        # --- the working examples must not break
        oks = [str(o.get("locator")) for o in evidence.get("ok_examples") or [] if isinstance(o, dict) and o.get("locator")][:REGRESSION_SAMPLES]
        own = [_follow(sb, new_cfg, loc, deadline) for loc in oks]
        verified["working"] = {"checked": len(own), "resolved": sum(1 for r in own if r.get("ok"))}
        if any(not r.get("ok") for r in own):
            return _fail(f"regression: {site}", trace)
    # --- every other user of a created / changed recipe
    regression = {"sites": []}
    trace["regression"] = regression
    for other_id, samples in _other_users(plan, site):
        try:
            other = scfg.load_site(other_id)
        except Exception:
            continue
        other.extra_providers = [p["provider"] for p in plan]
        results = [_follow(sb, other, s["locator"], deadline) for s in samples]
        row = {"site": other_id, "checked": len(results), "resolved": sum(1 for r in results if r.get("ok"))}
        regression["sites"].append(row)
        if row["resolved"] < row["checked"]:
            return _fail(f"regression: {other_id}", trace)

    # --- passed: the proposal for the record, then (heal_autoapply) apply
    result: dict[str, Any] = {"status": "healed", "page": "playback", "applied": False, "job_id": record.get("job_id"), "notes": notes}
    if new_data is not None:
        trace["before"], trace["after"] = _changed_parts(cfg.data, new_data)
    trace["recipes"] = [{"name": p["name"], "action": p["action"], "version": None,
                         "diff": heal.selector_diff(p["old"] or {}, p["provider"].data) if p["old"] else []} for p in plan]
    if not heal._autoapply():
        trace["proposal"] = {"job_id": record.get("job_id"), "notes": notes,
                             "yaml_text": yaml_text[:PROPOSAL_YAML_CHARS] if new_data is not None else "",
                             "provider_recipes": [{"name": p["name"], "action": p["action"],
                                                   "yaml": yaml.safe_dump(p["provider"].data, allow_unicode=True, sort_keys=False)[:PROPOSAL_RECIPE_CHARS]}
                                                  for p in plan]}
        result["reason"] = "proposal passed the gates; heal_autoapply is off (not applied)"
        return result
    return _apply(cfg, new_data, plan, trace, result)


def _apply(cfg: scfg.SiteConfig, new_data: Optional[dict], plan: list, trace: dict, result: dict) -> dict:
    """Write the recipes (versioned), then the site yaml (versioned); a failing site write puts the recipes back."""
    done: list[dict] = []
    try:
        for entry in plan:
            data = copy.deepcopy(entry["provider"].data)
            entry["version"] = scfg.save_recipe(entry["name"], data)
            done.append(entry)
        version = scfg.save_new_version(cfg.site_id, new_data) if new_data is not None else None
    except Exception as exc:
        for entry in done:   # undo: a new recipe is removed, an updated one gets its old content back (as a new version)
            try:
                if entry["action"] == "create":
                    scfg.delete_recipe(entry["name"])
                elif entry["old"] is not None:
                    scfg.save_recipe(entry["name"], entry["old"])
            except Exception:
                log.exception("heal_agent: could not undo recipe %s", entry["name"])
        return heal._fail(f"applying the proposal failed ({type(exc).__name__}): nothing was kept")
    for row in trace.get("recipes") or []:
        match = next((e for e in plan if e["name"] == row["name"]), None)
        if match:
            row["version"] = match.get("version")
    result.update(applied=True, new_version=version,
                  recipes=[{"name": e["name"], "action": e["action"], "version": e.get("version")} for e in plan])
    return result
