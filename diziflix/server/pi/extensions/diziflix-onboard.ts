/**
 * diziflix-onboard: the pi extension of the diziflix site-onboarding agent.
 *
 * Narrow tools, each a thin HTTP call to the sandbox endpoints of the diziflix server
 * (server/app/routers/onboard_sandbox.py). The agent never touches the network or the server code: it only sees what
 * these endpoints answer, the only thing it can write is a DRAFT yaml (`submit_draft`) or, in repair mode, a repair
 * PROPOSAL (`submit_repair`: nothing is applied by the call), and the only files it can read are the skill's own (the
 * `read` guard below). The skill that tells the agent how to use them is server/pi/skills/diziflix-site-onboarding/SKILL.md
 * (onboarding) and its references/heal.md (repair mode).
 *
 * Onboarding runs get thirteen tools (discover_site, fetch_page ... match_providers, test_search, ask_user, submit_draft). Edit runs
 * (DIZIFLIX_MODE=edit, `onboard.start(mode="edit")`: an admin asks to change a registered site) get those minus `discover_site` plus the
 * read-only `load_site_config`; the server locks their draft to
 * the edited site (site id suggestion, `load_site_config` reads only that site). Repair runs (DIZIFLIX_MODE=repair, started by
 * server/app/scraper/heal_agent.py through the shared runner server/app/scraper/pi_agent.py) get the page / test tools
 * plus `load_site_config` and `submit_repair`, and no `submit_draft` and no `ask_user` (nobody is there to answer); the pi `--tools`
 * allow-list says the same.
 *
 * `ask_user` is the one tool that makes NO sandbox call (`local`): it only checks its arguments and tells the model to stop. The server
 * reads the question from pi's tool-call event (scraper/pi_agent.py `EventParser.ask`), ends the run `needs_input` and shows the admin a
 * card with buttons; the answer comes back as the next user message.
 *
 * Environment (set by server/app/scraper/pi_agent.py for every pi run):
 *   DIZIFLIX_SANDBOX_URL  base URL, default http://127.0.0.1:8090/api/onboard/sandbox
 *   DIZIFLIX_ONBOARD_TOKEN secret sent as `X-Onboard-Token` (one job, valid while the run lasts)
 *   DIZIFLIX_DRAFT_ID     the draft (od_...) or repair job (rp_...) this run belongs to; `submit_draft` adds it itself
 *   DIZIFLIX_MODE         `repair` for the heal's repair agent, `edit` for the edit of a registered site, else onboarding (which tools are registered)
 *   DIZIFLIX_SKILL_DIR    the skill directory: the ONLY place pi's built-in `read` tool may open (see the guard below)
 *   DIZIFLIX_TOOL_TIMEOUT_MS  optional, per-call timeout in ms (default 150000; tests lower it)
 *
 * The same extension also guards pi's built-in `read` tool. `read` has to be in the tool allow-list, otherwise pi does not
 * list the skill in the system prompt and the model cannot open the skill's `references/*.md`; but `read` itself can open
 * the whole file system, so a `tool_call` handler blocks every path that does not resolve (`..`, symlinks, relative paths
 * included) to a file inside DIZIFLIX_SKILL_DIR. No DIZIFLIX_SKILL_DIR = every `read` is blocked.
 *
 * Verified against pi 0.99.1 in the Faz 2b spike (docs/plans/site-onboarding/pi-spike-notes.md, run on server 61):
 *   - pi loads this .ts directly (jiti, no build step); the type import is erased, its package is
 *     `@earendil-works/pi-coding-agent`;
 *   - `pi.registerTool({ name, label, description, parameters, execute })`, `execute(toolCallId, params, signal, ...)`,
 *     the result shape `{ content: [{ type: "text", text }], details: {} }`;
 *   - a thrown Error becomes an error tool result (`isError: true`, the model sees the message);
 *   - `parameters` are plain JSON Schema objects (what TypeBox produces anyway), no TypeBox import needed;
 *   - `process.env.DIZIFLIX_*` of the child environment reach the extension;
 *   - `pi.on("tool_call", async (event) => ({ block: true, reason }))` blocks a built-in `read` (the model gets the
 *     reason as an error result and reports it); `event.toolName`, `event.input.path`.
 * UNVERIFIED (Faz 2b): that pi fires `signal` when a tool is cancelled (the spike's SIGTERM kills the whole process, so
 * the handler never ran); the code only relies on it for a nicer error text, the run ends the same way without it.
 * Only erasable TypeScript syntax is used (no enums/namespaces/parameter properties), so the file also runs under
 * `node` type stripping, which is how tests/test_onboard_refs.py exercises it against a fake sandbox and a fake `pi`.
 */
import { realpathSync } from "node:fs";
import { resolve, sep } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent"; // verified (Faz 2b, pi 0.99.1)

const DEFAULT_BASE = "http://127.0.0.1:8090/api/onboard/sandbox";
const DEFAULT_TIMEOUT_MS = 150_000; // above the server's own ONBOARD_TOOL_TIMEOUT (120 s), which answers first with a 504
const MAX_OUTPUT_CHARS = 20_000; // one tool answer; the sandbox already clips its fields, this is the safety net
const CLIP_STEPS = [4000, 1500, 600, 250, 100]; // string lengths tried, in order, until the JSON fits
const MAX_ARRAY_ITEMS = 40;

type Json = Record<string, unknown>;

interface ToolSpec {
  name: string;
  label: string;
  description: string;
  parameters: Json; // JSON Schema
  method: "GET" | "POST";
  path: string | ((params: Json) => string); // a function builds the path (and query) from the parameters
  body?: (params: Json) => Json;
  local?: (params: Json) => string; // answered by the extension itself, no sandbox call (ask_user)
  modes?: string[]; // runs the tool belongs to (default: every mode); see DIZIFLIX_MODE
}

/**
 * `repair` = the heal's repair agent (load_site_config / submit_repair, no submit_draft); `edit` = an admin changes a REGISTERED
 * site (the onboarding tools + load_site_config; submit_draft is locked to that site by the server); anything else = onboarding.
 */
function mode(): string {
  const value = process.env.DIZIFLIX_MODE;
  return value === "repair" || value === "edit" ? value : "onboard";
}

// --- helpers ----------------------------------------------------------------------------------------------------

function str(description: string): Json {
  return { type: "string", description };
}

function obj(properties: Json, required: string[]): Json {
  return { type: "object", properties, required, additionalProperties: false };
}

/** Only the named keys that carry a value (the server applies its own defaults for the rest). */
function pick(params: Json, keys: string[]): Json {
  const out: Json = {};
  for (const key of keys) {
    const value = params[key];
    if (value !== undefined && value !== null && value !== "") out[key] = value;
  }
  return out;
}

function clip(value: unknown, cap: number): unknown {
  if (typeof value === "string") return value.length <= cap ? value : value.slice(0, cap) + `…[+${value.length - cap} chars]`;
  if (Array.isArray(value)) return value.slice(0, MAX_ARRAY_ITEMS).map((v) => clip(v, cap));
  if (value && typeof value === "object") {
    const out: Json = {};
    for (const [k, v] of Object.entries(value as Json)) out[k] = clip(v, cap);
    return out;
  }
  return value;
}

/** JSON text of `value`, shortened until it fits MAX_OUTPUT_CHARS (long strings first, so the JSON stays valid). */
function shrink(value: unknown): string {
  let text = JSON.stringify(value) ?? "null";
  if (text.length <= MAX_OUTPUT_CHARS) return text;
  for (const cap of CLIP_STEPS) {
    text = JSON.stringify(clip(value, cap)) ?? "null";
    if (text.length <= MAX_OUTPUT_CHARS) return text;
  }
  return text.slice(0, MAX_OUTPUT_CHARS - 30) + "…[truncated, no longer valid JSON]";
}

function timeoutMs(): number {
  const value = Number(process.env.DIZIFLIX_TOOL_TIMEOUT_MS);
  return value > 0 ? value : DEFAULT_TIMEOUT_MS;
}

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** The sandbox error envelope `{"error": {"code", "message"}}` as one line; anything else, its first 300 chars. */
function errorText(status: number, raw: string): string {
  try {
    const error = (JSON.parse(raw) as Json).error as Json | undefined;
    if (error && (error.code || error.message)) return `HTTP ${status} ${String(error.code ?? "")}: ${String(error.message ?? "")}`.trim();
  } catch {
    // not JSON: fall through
  }
  return `HTTP ${status}: ${raw.replace(/\s+/g, " ").slice(0, 300)}`;
}

async function call(spec: ToolSpec, params: Json, outer?: AbortSignal): Promise<string> {
  const base = (process.env.DIZIFLIX_SANDBOX_URL || DEFAULT_BASE).replace(/\/+$/, "");
  const token = process.env.DIZIFLIX_ONBOARD_TOKEN || "";
  if (!token) throw new Error(`${spec.name}: DIZIFLIX_ONBOARD_TOKEN is not set, the sandbox would refuse the call`);
  const body = spec.method === "POST" ? JSON.stringify(spec.body ? spec.body(params) : params) : undefined;

  const limit = timeoutMs();
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, limit);
  const onOuterAbort = () => controller.abort();
  if (outer) {
    if (outer.aborted) controller.abort();
    else outer.addEventListener("abort", onOuterAbort, { once: true });
  }
  try {
    const path = typeof spec.path === "function" ? spec.path(params) : spec.path;
    const response = await fetch(base + path, {
      method: spec.method,
      headers: { "X-Onboard-Token": token, Accept: "application/json", ...(body ? { "Content-Type": "application/json" } : {}) },
      body,
      signal: controller.signal,
    });
    const raw = await response.text();
    if (!response.ok) throw new Error(`${spec.name}: ${errorText(response.status, raw)}`);
    try {
      return shrink(JSON.parse(raw));
    } catch {
      throw new Error(`${spec.name}: the sandbox answered with something that is not JSON (${raw.slice(0, 120)})`);
    }
  } catch (err) {
    if (err instanceof Error && err.message.startsWith(spec.name + ":")) throw err;
    if (timedOut) throw new Error(`${spec.name}: no answer from the sandbox within ${limit / 1000}s`);
    if (outer?.aborted) throw new Error(`${spec.name}: cancelled`);
    throw new Error(`${spec.name}: cannot reach the sandbox at ${base} (${message(err)})`);
  } finally {
    clearTimeout(timer);
    outer?.removeEventListener("abort", onOuterAbort);
  }
}

// --- the tools --------------------------------------------------------------------------------------------------

const YAML_TEXT = str("The complete site yaml (plain text, no aliases).");
const HANDOFF = str("Handoff note (Markdown). New site: problem, how found, solution (<= 25 lines). Edit / repair: ONE entry: change, why, result (<= 8 lines).");
const PAGE_ID = str("page_id from fetch_page or test_config.");
/** New provider recipes for the library (see the skill's providers reference); they join the providers IN MEMORY for the call. */
const PROVIDER_RECIPES: Json = {
  type: "array",
  maxItems: 3,
  items: obj(
    {
      name: str("Lower-case letters, digits, underscore, 2-32 chars; same as `name:` in the yaml."),
      yaml: str("The complete provider recipe yaml."),
      mode: { type: "string", enum: ["new", "update"], description: "update = new version of a library recipe, a host added: the recipe_yaml of match_providers." },
    },
    ["name", "yaml"],
  ),
  description: "New provider recipes [{name, yaml}] (at most 3) for the library; the site yaml may name them in providers:. Recipes the draft already has are kept.",
};

const TOOLS: ToolSpec[] = [
  {
    name: "discover_site",
    label: "Discover site",
    description:
      "Build a DRAFT site yaml from the site's own pages by code (<= 8 page requests): home sections and roles, list, series page, detail " +
      "fields, player candidates, normalize. Returns yaml_text, found, missing[] (not found, with what was tried), confidence, pages[] " +
      "(page_id to reuse), notes, errors. Verify with test_config; ask_user about the gaps.",
    parameters: obj({ url: str("The site's address (any page; its root page is read).") }, ["url"]),
    method: "POST",
    path: "/discover_site",
    body: (p) => pick(p, ["url"]),
    modes: ["onboard"],
  },
  {
    name: "fetch_page",
    label: "Fetch page",
    description:
      "Download a page of the site and store it: page_id, fetch_mode used (copy http / browser to the yaml fetch_mode), html_excerpt " +
      "(scripts stripped: use grep_page). A page that is really ANOTHER path (meta refresh, JavaScript jump, canonical url) also gives " +
      "canonical_url and redirect_hint {kind, target, note}: fetch the target and use it as list_url / collection path. A player / " +
      "iframe URL that answers 404, 403 or 'Just a moment' wants the page it sits in as Referer: fetch it again with referer=<the detail page " +
      "URL> (Chrome TLS fingerprint, passes Cloudflare's TLS check; fetch_mode 'chrome' is NOT a yaml fetch_mode).",
    parameters: obj(
      {
        url: str("Absolute http(s) URL."),
        mode: { type: "string", enum: ["auto", "http", "browser", "chrome"], description: "Default auto. 'browser' = slow, for JS pages; 'chrome' = Chrome TLS fingerprint over http (implied by referer)." },
        wait_for: str("CSS selector the browser waits for (browser mode)."),
        referer: str("Absolute URL of the detail page that embeds this player / iframe URL (mode chrome)."),
      },
      ["url"],
    ),
    method: "POST",
    path: "/fetch",
    body: (p) => pick(p, ["url", "mode", "wait_for", "referer"]),
  },
  {
    name: "query_html",
    label: "Query HTML",
    description: "Try a CSS selector on a stored page: count and items[] (text, attr_value, clipped outer_html) of the first `limit` matches.",
    parameters: obj(
      {
        page_id: PAGE_ID,
        selector: str("CSS selector."),
        attr: str("Attribute to read (href, src, data-src, content)."),
        limit: { type: "integer", minimum: 1, maximum: 50, description: "Default 10, max 50." },
      },
      ["page_id", "selector"],
    ),
    method: "POST",
    path: "/query",
    body: (p) => pick(p, ["page_id", "selector", "attr", "limit"]),
  },
  {
    name: "grep_page",
    label: "Grep page",
    description:
      "Regex search in the RAW text of a stored page, scripts and inline JSON included: count and matches[] ({offset, text}). Python regex, " +
      "at most 200 characters; JSON escapes slashes (\\/): allow them with \\\\?/.",
    parameters: obj(
      {
        page_id: PAGE_ID,
        pattern: str("Python regular expression, at most 200 characters."),
        context: { type: "integer", minimum: 0, maximum: 500, description: "Characters around a match (default 120)." },
        limit: { type: "integer", minimum: 1, maximum: 30, description: "Matches returned (default 10, max 30)." },
        flags: str("i, m, s. Default none."),
      },
      ["page_id", "pattern"],
    ),
    method: "POST",
    path: "/grep",
    body: (p) => pick(p, ["page_id", "pattern", "context", "limit", "flags"]),
  },
  {
    name: "outline_page",
    label: "Outline page",
    description:
      "Structure of a stored page: repeating[] (candidate row selectors), hosts of iframes and links, nav_links[] (menu links; href is a " +
      "usable collection path), sections[] (home sections) and blocks[] (EVERY repeating card block; link_kind = what the cards open: " +
      "series / episode / film / mixed / other), canonical_url / redirect_hint, episode_links[] ({selector, count, shape, sample_hrefs}: " +
      "the episode links of a SERIES page; shape writes digits as N, the start of series_page.episode_url_regex).",
    parameters: obj({ page_id: PAGE_ID }, ["page_id"]),
    method: "POST",
    path: "/outline",
    body: (p) => pick(p, ["page_id"]),
  },
  {
    name: "test_config",
    label: "Test site yaml",
    description:
      "Run a draft site yaml against stored pages exactly as the server will: errors[], warnings[], failing[], diagnostics, criteria{...}, " +
      "passed. Call it after every change; pass page_id (and detail_page_id) so nothing is fetched again. " +
      "collections: true also fetches every collections: entry (collections[]). playable: true follows up to 3 DIFFERENT titles from their " +
      "playback page to a stream (playable{checked, resolved, skipped, samples[]}; criteria playable_ratio, for series " +
      "series_have_episode_sources), first reads up to 3 series pages with the yaml series_page: block (series{checked, with_episodes, " +
      "skipped, hint?, samples[]}; series_inventory_ok) and runs one search: query (search_ok). Run it with playable: true before submit_draft.",
    parameters: obj(
      {
        yaml_text: YAML_TEXT,
        page_id: str("Stored list page (from fetch_page)."),
        detail_page_id: str("Stored detail page for detail.fields."),
        collections: { type: "boolean", description: "Also check and fetch every collections: entry. Default false." },
        playable: { type: "boolean", description: "Also follow up to 3 titles to a stream (slower). Default false." },
        provider_recipes: PROVIDER_RECIPES,
        baseline: { type: "boolean", description: "Repair mode: compare with the ACTIVE config and baseline of the site (no field dropped, key kept, fill not lower); adds baseline{ok, reasons[]}." },
      },
      ["yaml_text"],
    ),
    method: "POST",
    path: "/test_config",
    body: (p) => pick(p, ["yaml_text", "page_id", "detail_page_id", "collections", "playable", "provider_recipes", "baseline"]),
  },
  {
    name: "list_resolvers",
    label: "List resolvers",
    description:
      "The resolver types (with every parameter) and providers the yaml may use in `resolvers:` and `providers:`; only these exist. kind " +
      "\"code\" = a module (vidmolly, okru), kind \"recipe\" = a provider-library recipe (hosts, version): use one that matches the player's " +
      "host (providers: [name]); write a NEW one only when none does. No arguments.",
    parameters: obj({}, []),
    method: "GET",
    path: "/resolvers",
  },
  {
    name: "test_resolvers",
    label: "Test resolvers",
    description:
      "Run the yaml's `resolvers:` on detail pages and resolve the candidates through the providers, like a play request. Tries SEVERAL " +
      "pages: detail_url, then detail_urls, else up to 2 more from the list page. Returns status (resolved = every page | partial = some | " +
      "no_stream | no_candidates | error), pages[] (skipped = no time for it), candidates[] / resolved[] for detail_url alone, and " +
      "warnings ('varied hosts/types: ...'). partial or varied hosts: generalize the recipe (one extract rule per format) or say so in notes.",
    parameters: obj(
      {
        yaml_text: YAML_TEXT,
        detail_url: str("Absolute URL of a detail (or episode) page."),
        page_id: str("Stored detail page to use instead of fetching."),
        detail_urls: {
          type: "array",
          items: { type: "string" },
          maxItems: 4,
          description: "Up to 4 more URLs of different titles. Omitted = up to 2 sampled; [] = detail_url only.",
        },
        list_page_id: str("Stored list page to sample extra pages from."),
        provider_recipes: PROVIDER_RECIPES,
      },
      ["yaml_text", "detail_url"],
    ),
    method: "POST",
    path: "/test_resolvers",
    body: (p) => pick(p, ["yaml_text", "detail_url", "page_id", "detail_urls", "list_page_id", "provider_recipes"]),
  },
  {
    name: "test_provider",
    label: "Test provider recipe",
    description:
      "Validate ONE provider recipe and run it on a player URL like a play request: its match must cover sample_url, then fetch / referer / " +
      "follow / extract read the player page. Returns valid, errors[], matched, status (resolved | no_stream | no_match | invalid), " +
      "streams[], trace, warnings. Nothing is written. Call it with SEVERAL player URLs of different titles before " +
      "submit_draft(provider_recipes).",
    parameters: obj(
      {
        recipe_yaml: str("The complete provider recipe yaml."),
        sample_url: str("Absolute player (embed / iframe) URL, e.g. a candidate url of test_resolvers."),
        referer: str("The detail / episode page that embeds the player (players often 404 without it)."),
      },
      ["recipe_yaml", "sample_url"],
    ),
    method: "POST",
    path: "/test_provider",
    body: (p) => pick(p, ["recipe_yaml", "sample_url", "referer"]),
  },
  {
    name: "match_providers",
    label: "Match providers",
    description:
      "Dry-run EVERY provider of the library on one player page, host match ignored. Returns matches[] and recommendation: use_provider | " +
      "add_host (recipe_yaml: hand it in as provider_recipes with mode update) | new_recipe | needs_code. Call it before writing a recipe.",
    parameters: obj(
      {
        player_url: str("Absolute player (embed / iframe) URL."),
        referer: str("The detail / episode page that embeds it."),
      },
      ["player_url"],
    ),
    method: "POST",
    path: "/match_providers",
    body: (p) => pick(p, ["player_url", "referer"]),
  },
  {
    name: "test_search",
    label: "Test site search",
    description:
      "Run the draft yaml's `search:` block (the site's live search) on ONE query, as the server will. Nothing is written. Returns valid, " +
      "errors[], warnings[], count, samples[], found_known (the query is the first list item's title unless you give one; true = its " +
      "detail_url or normalize key is among the results; null = not judged), normalize_ok_ratio. Pass the list page_id. Also part of " +
      "test_config(playable: true) and submit_draft (criterion search_ok).",
    parameters: obj(
      {
        yaml_text: YAML_TEXT,
        query: str("At least 3 characters (default: the first list item's title)."),
        page_id: str("Stored list page the known title is taken from."),
        detail_page_id: str("Stored detail page whose title is the known title when the list gives none."),
      },
      ["yaml_text"],
    ),
    method: "POST",
    path: "/test_search",
    body: (p) => pick(p, ["yaml_text", "query", "page_id", "detail_page_id"]),
    modes: ["onboard", "edit"],
  },
  {
    name: "ask_user",
    label: "Ask the admin",
    description:
      "Ask the admin ONE question and END YOUR TURN. Only after you tried with evidence (two or more pages, grep_page for the label, the " +
      "diagnostics). kind 'missing_info' (default): a piece of information you cannot find; the admin gets 'not on the site, skip it', " +
      "'it exists, I will point at it' and, with a proposal, 'apply the proposal'. kind 'decision': a choice (options, at most 4) or " +
      "something that CANNOT be skipped (playability, list fill). kind 'engine_gap': the engine / yaml schema cannot express what the site " +
      "needs (no skip button). Write the question in Turkish, short and concrete. After this call write NO further tool call: the answer " +
      "arrives as your next message (\"Sitede yok, atla: <field>\", \"Var: <hint>\", \"Önerini uygula\" or free text).",
    parameters: obj(
      {
        kind: { type: "string", enum: ["missing_info", "decision", "engine_gap"], description: "Default missing_info." },
        field: str("What it is about: overview, cast, genres, year, rating, trailer, poster_url, collection:trending, search, playable, ..."),
        question: str("For the admin: Turkish, 1-3 sentences, plain words (no yaml keys)."),
        tried: { type: "array", maxItems: 8, items: { type: "string" }, description: "What you tried, one short line each." },
        proposal: str("Optional suggestion (button 'Önerilen: ...')."),
        options: {
          type: "array",
          maxItems: 4,
          description: "Only for kind 'decision': buttons [{id, label}].",
          items: obj({ id: str("Short id (lower-case, digits, underscore)."), label: str("Button text (Turkish).") }, ["id", "label"]),
        },
      },
      ["field", "question"],
    ),
    method: "POST",
    path: "",
    local: (p) => {
      const question = typeof p.question === "string" ? p.question.trim() : "";
      const field = typeof p.field === "string" ? p.field.trim() : "";
      if (!question) throw new Error("ask_user: `question` is empty; write the question for the admin");
      if (!field) throw new Error("ask_user: `field` is empty; name what the question is about");
      return JSON.stringify({
        recorded: true,
        instruction:
          "Your question is shown to the admin now. END YOUR TURN: call no further tool, write at most one short closing sentence. " +
          "The answer arrives as your next message.",
      });
    },
    modes: ["onboard", "edit"],
  },
  {
    name: "submit_draft",
    label: "Submit draft",
    description:
      "Hand the finished draft in: the server re-tests it and stores it for the admin. Call it once at the end, after a final test_config. " +
      "Returns passed, errors, warnings, criteria. New provider recipes go in provider_recipes (saved with the site).",
    parameters: obj(
      {
        yaml_text: YAML_TEXT,
        site_id_suggestion: str("Lower-case letters, digits, underscore, starts with a letter, 2-32 chars."),
        notes: str("What was found, failing criteria, open problems."),
        handoff: HANDOFF,
        page_id: str("Stored list page of the last test_config."),
        detail_page_id: str("Stored detail page of the last test_config."),
        provider_recipes: PROVIDER_RECIPES,
      },
      ["yaml_text", "site_id_suggestion"],
    ),
    method: "POST",
    path: "/submit",
    body: (p) => {
      const draftId = process.env.DIZIFLIX_DRAFT_ID || "";
      if (!draftId) throw new Error("submit_draft: DIZIFLIX_DRAFT_ID is not set");
      return { draft_id: draftId, ...pick(p, ["yaml_text", "site_id_suggestion", "notes", "handoff", "page_id", "detail_page_id", "provider_recipes"]) };
    },
    modes: ["onboard", "edit"],
  },
  // --- repair mode (the heal's repair agent; load_site_config also in edit mode): registered only when DIZIFLIX_MODE says so -------
  {
    name: "load_site_config",
    label: "Load site config",
    description:
      "Read-only: the ACTIVE yaml (secrets masked), version, baseline, provider recipes and the HANDOFF note (earlier findings, the admin's " +
      "decisions) of the REGISTERED site being repaired or edited. Call it FIRST in repair and edit mode: read the note, submit this yaml " +
      "with only the broken / requested part changed. In edit mode only the edited site can be read.",
    parameters: obj(
      {
        site_id: str("The registered site id (in edit mode the site being edited)."),
        recipe: str("Optional provider recipe name whose yaml you want too."),
      },
      ["site_id"],
    ),
    method: "GET",
    path: (p) => `/site_config/${encodeURIComponent(String(p.site_id ?? ""))}` + (p.recipe ? `?recipe=${encodeURIComponent(String(p.recipe))}` : ""),
    modes: ["repair", "edit"],
  },
  {
    name: "submit_repair",
    label: "Submit repair",
    description:
      "Hand in your repair PROPOSAL once, at the end, after test_config (baseline: true, playable: true) and test_provider passed on " +
      "3+ different examples. Nothing is applied by this call: the server re-tests it and applies it only when all checks pass. " +
      "yaml_text = the COMPLETE corrected yaml (omit when only a recipe changes); provider_recipes = a NEW recipe or the complete NEW " +
      "version of an existing one (same name). When yaml and recipes cannot fix it (a signature, cookie, TLS check, JavaScript API call) " +
      "submit NO yaml and NO recipe and write `needs code: <host>` plus the evidence in notes.",
    parameters: obj(
      {
        site_id: str("The registered site id being repaired."),
        yaml_text: str("The complete corrected yaml; omit when only a recipe changes."),
        provider_recipes: PROVIDER_RECIPES,
        notes: str("What was wrong, what you changed, the examples verified; or `needs code: <host>` + evidence."),
        handoff: HANDOFF,
      },
      ["site_id"],
    ),
    method: "POST",
    path: "/submit_repair",
    body: (p) => pick(p, ["site_id", "yaml_text", "provider_recipes", "notes", "handoff"]),
    modes: ["repair"],
  },
];

// --- read guard -------------------------------------------------------------------------------------------------

/** Why `rawPath` may not be read (null = allowed): it must resolve, symlinks followed, to something inside the skill dir. */
function readViolation(rawPath: unknown): string | null {
  const dir = process.env.DIZIFLIX_SKILL_DIR || "";
  if (!dir) return "read is disabled (DIZIFLIX_SKILL_DIR is not set)";
  let root: string;
  try {
    root = realpathSync(resolve(dir));
  } catch {
    return `read is disabled (the skill directory ${dir} does not exist)`;
  }
  const hint = `read is limited to the skill directory ${root}; use an absolute path inside it (e.g. ${root}${sep}references${sep}quality.md)`;
  if (typeof rawPath !== "string" || rawPath === "" || rawPath.includes("\0")) return `${hint}; the path is empty or invalid`;
  // like pi does, a relative path is taken from the cwd; `..` is folded away by resolve() and symlinks by realpath()
  const wanted = resolve(rawPath);
  let real: string;
  try {
    real = realpathSync(wanted);
  } catch {
    return `${hint}; ${wanted} does not exist or cannot be resolved`;
  }
  if (real === root || real.startsWith(root + sep)) return null;
  return `${hint}; ${wanted} is outside`;
}

export default function (pi: ExtensionAPI) {
  // verified (Faz 2b, pi 0.99.1): a blocked call comes back to the model as an error result carrying `reason`
  pi.on("tool_call", async (event) => {
    if (event.toolName !== "read") return undefined;
    const input = (event.input ?? {}) as Record<string, unknown>;
    const reason = readViolation(input.path ?? input.file_path);
    return reason === null ? undefined : { block: true, reason };
  });

  const current = mode();
  for (const spec of TOOLS) {
    if (spec.modes && !spec.modes.includes(current)) continue; // e.g. no submit_draft in a repair run, no submit_repair when onboarding
    // verified (Faz 2b, pi 0.99.1): registerTool shape, execute signature, result shape (see the header)
    pi.registerTool({
      name: spec.name,
      label: spec.label,
      description: spec.description,
      parameters: spec.parameters,
      async execute(_toolCallId: string, params: Json, signal?: AbortSignal) {
        const text = spec.local ? spec.local(params ?? {}) : await call(spec, params ?? {}, signal);
        return { content: [{ type: "text", text }], details: {} };
      },
    } as never);
  }
}
