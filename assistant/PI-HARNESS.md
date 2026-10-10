# Pi harness analysis and ability architecture

Inspected the globally installed Pi 0.99.2 CLI, its bundled SDK examples and exported TypeScript declarations. The assistant previously used `pi-ai` only: model/auth adapters and Jev classification, without the Pi agent/tool loop.

Pi separates these responsibilities:

- `pi-ai`: model catalogs, provider calls, subscription OAuth, and classification.
- `pi-coding-agent` SDK: `createAgentSession`, the model/tool loop, `SessionManager`, events, resource discovery, settings, and extension lifecycle.
- Extensions: trusted TypeScript modules registering tools and event handlers. `tool_call` can block execution. `ctx.ui.confirm()` is the permission prompt integration point; headless sessions have no usable confirmation UI unless a host binds one.
- Skills: discoverable Markdown workflows with frontmatter. The harness advertises a skill description and the model loads its full text through Pi's read tool. Skills describe behavior; they do not grant permissions.
- MCP/codemode: supported by Pi, but not loaded by SDK sessions unless explicitly installed. They are unnecessary for the first ability.

The new adapter creates an ephemeral Pi session per request, imports the orchestrator's bounded visible history through `SessionManager`, and disposes it on completion/cancellation. Pi owns all tool execution inside the request. The existing orchestrator retains voice, provider fallback, cancellation, and bounded conversation history. Tool results/private reasoning are not retained between requests.

Only reviewed app-owned extensions and skills are loaded. The permission extension permits approved tools and restricts the built-in `read` tool to all app-owned skill and extension files. Pi built-in `write` and `edit` are enabled only inside `.pi/skills/`, as authorized by the user. Shell execution, extension mutation and permission-file writes remain unavailable. `.pi/permissions.json` is local user configuration, not something the model can edit. Currently web search, reading public pages and read-only Apple Mail (`mail_read`) are approved; future abilities and every undefined `mail_*` tool default to denied. Offline mode denies all network abilities; the local Mail tools stay available. There is no second permissions engine in the native app.

Online search uses a Pi extension with `web_search` and `web_read` plus an online-search skill. Search queries are sent to DuckDuckGo's public HTML search, without an extra API key. HTML scraping can be throttled or challenged; errors are surfaced and no results are invented. Web reads use public HTTPS URLs, bounded responses, redirect checks and public-IP validation. No authenticated browser, mailbox, unrestricted filesystem browsing or sending capability is granted.

Sources: https://pi.dev/docs/latest/sdk ; https://pi.dev/docs/latest/extensions ; https://pi.dev/docs/latest/skills ; local SDK documentation shipped with 0.99.2. Latest public docs may describe newer releases; installed declarations are authoritative for this implementation.

## Bağlam bütçesi / Context budget

Rule for local Gemma and remote models: context holds the minimum the request needs. Only the routed skill (or, unrouted, the skill catalog) and its tools are sent: unrouted offline = `read` + Mail tools; unrouted online adds `web_search`/`web_read`; a Jev-routed skill gets only its own tools. Skill files, tool and schema descriptions stay short; pi-web-access parameter descriptions are cut to their first sentence. History is capped by `history_chars` (6000). Measured on 2026-10-09 (llama tokenizer): offline unrouted ~1.2K tokens (was ~5.0K), mail-assistant ~1.0K (was ~5.8K), online-search ~2.4K (was ~5.7K). `tests/context-budget.test.ts` enforces character limits. Note: Pi reserves 4096 tokens of safety margin when computing `max_tokens`, so `local_context` must exceed prompt + tool results + 4096 + `max_tokens`.

## Skill management and limits

The user calls Pi the harness. Its `skill-manager` skill describes creating and
editing app-owned skills and `test_skill` uses Pi's native `loadSkillsFromDir`
parser for static validation. Supporting files can also be read or edited in the
skill directory. Scripts are not executed. Behavioral testing uses already
approved tools; static validation alone does not prove that a skill works.
Saved skills/extensions are loaded anew on the next request. Extensions are
trusted code, so reviewing executable extension changes remains necessary.

Public web reads support HTML only (not PDFs, authenticated sites, or pages
requiring JavaScript). The first tool set permits at most 12 tool calls per
request, 15 seconds per web request, 3 redirects and 1 MiB per HTML response.
Mail tools allow at most 2 listings and 5 body reads per request inside the same
12-call limit, 12 seconds per Mail bridge call, 256 KiB bridge output, 64 KiB per
tool result and 40,000 body characters per request. Mail results carry an
"Untrusted local email data" header; activity logs keep only tool name, decision,
duration, result count, truncation and error code.
Local model tool quality is model-dependent. There is no interactive permission
dialog bridge yet; new/unapproved operations are blocked rather than prompting
through a separate app system. Native macOS microphone/Documents permissions
remain operating-system permissions, which Pi cannot grant.

## Dependency preparation

The app pins `pi-coding-agent` to the inspected global version 0.99.2. That
release's nested npm shrinkwrap pins vulnerable `brace-expansion` 5.0.9 and
ignores root npm overrides. A reviewed local postinstall routine removes only
that nested copy so minimatch resolves the explicit patched root 5.0.12 instead.
`npm ci` runs this routine. If installing with `--ignore-scripts`, run
`npm run prepare-deps` afterward. npm audit may still flag the published nested
shrinkwrap in the lockfile; the app's loaded implementation is 5.0.12.
The global Pi installation is not modified. Revisit this workaround when
upgrading the harness to a release with a corrected shrinkwrap.

## Timing and diagnostics

Metadata-only activity logging uses Pi session lifecycle events and the Pi
permission extension. The host adds voice, classification, fallback and request
boundaries. Request IDs correlate entries in `data/activity.log`; no prompt,
reply, tool arguments, OAuth token or reasoning is logged. Logs rotate at 2 MiB
with one previous file. The native menu opens the log in TextEdit.

Jev can classify web search and skill management. The decision passes a trusted
skill name to the adapter, which uses Pi's `/skill:name` expansion rather than
asking the model to spend a round reading the skill. Permission enforcement is
unchanged. News queries use Bing News RSS, general queries DuckDuckGo; blocked
providers should not be retried within the same request.
