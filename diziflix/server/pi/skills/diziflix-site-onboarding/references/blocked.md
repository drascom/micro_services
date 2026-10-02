# `blocked:` and `availability_gate:` - content that is not public is never taken

The rule: **only publicly playable content enters the library.** A series or episode whose page shows a copyright / access block instead of a
player ("telif engeli") is NOT taken: its episodes are not written, a series already in the library is removed (the viewers' progress / my-list
rows stay and come back with the title), and the playback health counters, the playback repair trigger and the source finder's repair step ignore
it (it is not a broken player). Two optional yaml blocks say it; a site without them behaves as before.

## 1. `blocked:` - a placeholder you can name

For a page that shows a placeholder where the player should be. Real case (trdiziizle): every episode page of the series "Halka" has
`<iframe src="/player/telif.html">` instead of the player.

```yaml
blocked:
  - on: episode_page                    # episode_page = a playback page (an episode OR a film) | series_page
    iframe_src_regex: '/player/telif'   # or html_regex / selector; at least one, several = OR
    reason: telif engeli                # <= 80 characters, shown to the admin and stored with the verdict
```

- (YAML reads a bare `on:` as the boolean `true`; the engine accepts it.) `on: episode_page` is checked on the pages the player is looked for (and,
  at scan time, on the newest + oldest episode page of every series); `on: series_page` on the series page itself.
- `iframe_src_regex` is searched in the `src` / `data-src` of every `<iframe>`; `html_regex` in the whole page; `selector` must match an element.
  At most 6 rules, regexes <= 500 characters, no `(a+)+` shapes. Write the narrowest condition that is only the placeholder (`/player/telif`, not
  `telif`).
- Write it when `test_config` / `test_resolvers` shows a page that does not resolve and its iframe (or a block of text) is a placeholder
  (`diagnostics.player[].placeholder`, or the warning "this page looks like a content-block placeholder"); check with `grep_page` that the SAME
  placeholder is on the other episode pages of that series, and that other series' pages have a real player.
- NOT for a **missing / deleted** page ("sayfa bulunamadı", HTTP 404 / 410: nothing to hide; with `availability_gate` a 404 counts as "no player"), NOT
  for a player that only fails to RESOLVE (unknown host, a signature: a resolver / provider problem, or "needs code"), NOT for a temporary error
  (HTTP 5xx, 403, 429, a timeout: judged again at the next scan).

## 2. `availability_gate:` - no player = not taken (the general rule)

A NEW `playback: video` site must carry it: the criterion `availability_gate_defined` fails a draft without an `availability_gate` (probe >= 1), the
admin cannot skip it: write it every time (`availability_gate: {probe: 2, require: player}`), placeholder seen or not.
`blocked:` only catches a placeholder you know; the gate is the general form: **a page without a player is not taken.**

```yaml
availability_gate:
  probe: 2          # episode pages checked per series: the newest + the oldest (3 adds a middle one); a film: its page; 0 = off
  require: player   # player = the resolvers give >= 1 candidate on the page (one request, cheap) | stream = a stream really resolves (expensive)
```

- Every probed page without a player (no candidate; with `require: stream` no stream) = the series is blocked. Some pages fine, some not: the
  series is taken and only the probed pages without a player are not written. A transient failure is NOT "no player": not taken for now, judged
  again at the next scan.
- Cost: at most `probe` extra requests per series (paced like the series crawl), one per film (`item_limit`). A verdict is kept for
  `BLOCKED_RECHECK_DAYS` (7); then a blocked series is judged again, so a site that lifts the block gets its content back. Prefer `require: player`.
- `blocked:` and the gate work together: the rule names the placeholder and gives a readable `reason`; the gate catches the rest.

## How to check it

`test_config(..., playable: true)` (or `submit_draft`) shows `blocked` = `{count, rules, samples[{url, reason}]}` (what would not be taken) and, with an
`availability_gate`, `gate` = `{probed, passed, skipped, retry, samples}`. Blocked samples are LEFT OUT of `playable_ratio` (not broken players) and
replaced by another series (at most 2 spares); every sample blocked = the ratio fails: the site offers (almost) nothing public, say so in `notes`
instead of weakening the rule. Keep a `blocked:` rule only when `blocked.samples` shows it matches exactly the placeholder pages.
