# Ayhan web access

The complete `pi-web-access` 0.35.0 package is pinned in Ayhan's package.json and
installed under this app's node_modules. No global Pi package was installed.
The adapter in `.pi/extensions/web-access.ts` loads the full upstream extension
and retains its commands, hooks and four tools:

- `web_search`: one/many queries, provider choice, recency/domain filters and optional summaries.
- `fetch_content`: readable/raw/page-answer modes, PDF, GitHub, images, YouTube and local video.
- `source_check`: source/passages research artifacts. Its `unclear` status means evidence needs manual semantic review, not that a claim has been proved.
- `get_search_content`: bounded paging and text finding in stored results.

All four are enabled for the user's requested evaluation. Pi `tool_call`
permission checks, offline restrictions and the 12-call request budget remain in
force. Arbitrary bash and unrelated read/write tools remain outside this package
permission. Upstream may use helpers such as git, ffmpeg and yt-dlp inside a
permitted tool; enabling fetch_content therefore includes those helper actions.
The extension is trusted code, not a separate OS sandbox.

## Local settings and provider changes

Edit `.pi/agent/web-search.json`, then Restart Ayhan. Provider defaults to `exa`
(keyless MCP), workflow `none`, eager tool availability. Existing shared Pi OAuth
is used by OpenAI search/page-answer/summary when selected; OAuth credentials
remain with the user's existing Pi account rather than being duplicated.
Global skills, extensions and MCP configuration are not auto-loaded.

Set `provider` to another upstream provider (e.g. `openai`, `brave`, `tavily`),
and add its required key to this **local** JSON following upstream documentation.
The file is mode 600, its directory mode 700, and the agent directory is ignored.
Do not put credentials in skills or response instructions. The old DuckDuckGo
search registration is removed; web_search now belongs to the upstream package.
The original hardened public-page reader remains available as web_read.

Cache, cloned repositories, and future extraction temporary files use
`data/web-access/`. The local runtime under `runtime/web-access/` contains yt-dlp;
ffmpeg/ffprobe use the existing Homebrew installation. Install reproduction:

```sh
npm ci
/opt/homebrew/bin/python3.13 -m venv runtime/web-access
runtime/web-access/bin/python -m pip install -r scripts/web-access-requirements.txt
```

The adapter passes cancellation and a 45-second deadline to tools, normalizes
upstream failures into Pi errors, and records actual search provider metadata.
Package-internal helper operations share the approved tool's permission; they
are not individually visible as extra Pi tool calls.

## Validation and current limits

`node scripts/test-web-access.ts` and `node scripts/test-web-access.ts --extended`
run real tools through Pi using a deterministic test model. They do not grant
permissions outside `.pi/permissions.json`. Public-source excerpts and feature
errors are saved to `data/web-access/smoke-report.json` and `extended-report.json`;
these are deliberate diagnostic artifacts, separate from the ordinary activity
log which does not save question/answer text.

Verified: keyless Exa search, shared-auth OpenAI search, PDF extraction, small
GitHub repository cloning/reading, stored content paging, raw HTTP, source-check
passages, page-answer with the OpenAI model, local video frame extraction.
Multi-fetch reports per-URL failures honestly; a short example.com page fails
readability's completeness check while its raw HTTP mode succeeds.

YouTube transcript/video understanding needs Gemini access or a compatible
browser session. Neither Gemini keys nor browser authFetch profiles have been
configured, so those paths return explicit errors. Installing the package does
not create access to those accounts or to paid provider APIs. Providers requiring
keys and self-hosted endpoints cannot be live-validated without their credentials.

The upstream browser curator and TUI commands are installed but the native app's
non-interactive SDK context has no Pi TUI. Upstream resolves summary-review to
none in that context; full interactive review needs a host UI bridge. Ordinary
search does not require that bridge. Auto-summary is a separate optional extra
model pass; use workflow none for quick replies.

Next review can disable unwanted capabilities using the upstream `tools`,
`fetch.allowedModes`, `webSearch.allowedProviders` and `commands` settings, plus
Ayhan's per-tool permissions. No package tools have been removed for this review.

Additional evaluation: auto-summary and direct image extraction succeeded. The browser curator falls back to ordinary search in the native non-interactive context. Keyless Jina hosted extraction fallback is enabled for this full-feature evaluation; target URLs can be processed by that service. yt-dlp is pinned to 2026.8.19 on Python 3.13.

Final media recheck: YouTube timestamp frame extraction succeeds after upgrading yt-dlp/Python; semantic video understanding still needs Gemini access. Short-page readable extraction succeeds with the enabled Jina fallback. Results are in data/web-access/media-recheck-report.json. All 50 automated tests pass.
