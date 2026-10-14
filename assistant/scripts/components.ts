import { stat } from 'node:fs/promises';
import path from 'node:path';
import { root } from '../src/config.ts';
import { discard, installedVersion, loadManifest, smoke, stage, swap, type Component } from '../src/components.ts';

/**
 * components.json helper for scripts/setup.sh.
 *   node scripts/components.ts status                       pinned vs installed
 *   node scripts/components.ts install [--dry-run] [--skip-big-models]
 * install: every component whose installed version differs from the pin is staged, smoke-tested and swapped in
 * (idempotent; downloads resume from `.part`). npm deps come from `npm ci`; detect-language from the app build.
 */
const args = process.argv.slice(2), command = args[0] ?? 'status';
const dryRun = args.includes('--dry-run'), skipBig = args.includes('--skip-big-models');
const platform = `${process.platform}-${process.arch}`;
const BIG = 1_000_000_000;
const manifest = await loadManifest(path.join(root, 'components.json'));
const supported = (c: Component) => c.platform === 'any' || platform.startsWith(c.platform);

let failed = 0;
for (const c of manifest.components) {
  const installed = await installedVersion(c, root);
  const label = `${c.id.padEnd(18)} ${c.version.padEnd(12)}`;
  if (command === 'status') { console.log(`${label} ${installed === c.version ? 'ok' : installed ?? 'missing'}`); continue; }
  if (command !== 'install') throw Error('Commands: status, install');
  if (!supported(c)) { console.log(`${label} skip (${c.platform} only; TODO ${platform})`); continue; }
  if (installed === c.version) { console.log(`${label} ok`); continue; }
  if (c.kind === 'npm-dep' || c.install?.app_build) { console.log(`${label} ${c.kind === 'npm-dep' ? 'npm ci' : 'app build'}`); continue; }
  // Models: only the files that are missing or have the wrong size.
  const files = c.files ? (await Promise.all(c.files.map(async f => (await stat(path.join(root, f.path)).catch(() => undefined))?.size === f.size ? undefined : f)))
    .filter(f => f !== undefined) : undefined;
  const bytes = files?.reduce((n, f) => n + f.size, 0) ?? 0;
  if (skipBig && bytes >= BIG) { console.log(`${label} skipped (${Math.round(bytes / 1e6)} MB, --skip-big-models)`); continue; }
  if (dryRun) { console.log(`${label} would install (now: ${installed ?? 'missing'}${bytes ? `, ${Math.round(bytes / 1e6)} MB` : ''})`); continue; }
  const ctx = { root, signal: new AbortController().signal, log: (line: string) => console.log('  ' + line) };
  let staged;
  try {
    console.log(`${label} installing…`);
    staged = await stage(c, { version: c.version, ...(c.sha256 ? { sha256: c.sha256 } : {}), ...(files ? { files } : {}) }, ctx);
    const test = await smoke(c, staged, c.version, ctx);
    if (!test.ok) throw Error('smoke test: ' + test.detail);
    await swap(c, staged, root);
    console.log(`${label} installed (${test.detail})`);
  } catch (error) {
    failed++; await discard(c, staged, ctx).catch(() => {});
    console.error(`${label} FAILED: ${error instanceof Error ? error.message : error}`);
  }
}
if (failed) process.exitCode = 1;
