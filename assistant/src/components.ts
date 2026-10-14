import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { access, cp, mkdir, mkdtemp, readdir, readFile, rename, rm, stat, writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { homedir, tmpdir } from 'node:os';
import path from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { stopProcess } from './processes.ts';

/**
 * components.json: every runtime, model and pinned npm dependency with its version, upstream, channel policy, installer,
 * smoke test and the process to restart. Shared by scripts/setup.sh (via scripts/components.ts) and the updater (updates.ts).
 * Install = staging dir/file → smoke test → atomic swap; the previous version stays in `<path>.prev` for rollback.
 */
export type Kind = 'binary' | 'npm-runtime' | 'python-venv' | 'model' | 'npm-dep';
export type Channel = 'stable' | 'prerelease' | 'manual';
export type Restart = 'llama' | 'whisper' | 'ema' | 'kokoro' | 'app' | 'none';
export type ModelFile = { path: string; remote?: string; url?: string; size: number; sha256: string };
export type Source = { type: 'github'; repo: string; asset?: string } | { type: 'npm' | 'pypi'; package: string; notes?: { repo: string; tag: string } }
  | { type: 'hf'; repo: string; revision?: string } | { type: 'local' } | { type: 'url' };
export interface Component {
  id: string; name: string; kind: Kind; version: string; sha256?: string; source: Source; channel: Channel; platform: string;
  path?: string; installed?: { json?: string; key?: string; exists?: string };
  install?: { script?: string; app_build?: boolean; hf_snapshot?: string; python?: string };
  smoke?: { cmd: string[]; expect: string }; perf?: 'llama'; restart: Restart; uses?: string;
  python?: string; requirements?: string; packages?: string[]; files?: ModelFile[];
}
export type Manifest = { schema: number; components: Component[] };
/** What an upstream offers for a component (updates.ts fills it; setup uses the pinned values). */
export type Target = { version: string; sha256?: string; files?: ModelFile[]; revision?: string };

const KINDS = ['binary', 'npm-runtime', 'python-venv', 'model', 'npm-dep'];
const CHANNELS = ['stable', 'prerelease', 'manual'];
const RESTARTS = ['llama', 'whisper', 'ema', 'kokoro', 'app', 'none'];
const safeRelative = (value: unknown) => typeof value === 'string' && !!value && !path.isAbsolute(value) && !value.split('/').includes('..');

export function validateManifest(value: unknown): Manifest {
  const manifest = value as Manifest;
  if (!manifest || typeof manifest !== 'object' || !Array.isArray(manifest.components)) throw Error('components.json: components list missing');
  const seen = new Set<string>();
  for (const c of manifest.components) {
    if (!c || typeof c.id !== 'string' || !/^[a-z0-9-]{1,40}$/.test(c.id) || seen.has(c.id)) throw Error(`components.json: invalid id ${String(c?.id)}`);
    seen.add(c.id);
    if (!KINDS.includes(c.kind) || !CHANNELS.includes(c.channel) || !RESTARTS.includes(c.restart) || typeof c.version !== 'string' || !c.version)
      throw Error(`components.json: invalid entry ${c.id}`);
    if (c.path !== undefined && !safeRelative(c.path)) throw Error(`components.json: invalid path ${c.id}`);
    for (const file of c.files ?? []) if (!safeRelative(file.path) || !/^[0-9a-f]{64}$/.test(file.sha256) || !Number.isInteger(file.size))
      throw Error(`components.json: invalid file ${c.id}`);
    if (c.kind === 'npm-dep' && !c.packages?.length) throw Error(`components.json: packages missing ${c.id}`);
  }
  return manifest;
}
export async function loadManifest(file: string): Promise<Manifest> { return validateManifest(JSON.parse(await readFile(file, 'utf8'))); }
/** Atomic write; the running app never runs git (the user commits the change). */
export async function saveManifest(file: string, manifest: Manifest): Promise<void> {
  validateManifest(manifest);
  const temporary = `${file}.${process.pid}.tmp`;
  await writeFile(temporary, JSON.stringify(manifest, null, 2) + '\n');
  await rename(temporary, file);
}

/** Orders release versions: b11574 < b11617, v1.9.4 < v1.9.5, 1.2.0-beta.1 < 1.2.0. */
export function compareVersions(a: string, b: string): number {
  const parse = (v: string) => {
    const [main, pre] = v.replace(/^[^0-9]*/, '').split(/-(.*)/s);
    return { nums: main.split(/[.+_]/).map(n => Number.parseInt(n, 10) || 0), pre: pre ?? '' };
  };
  const x = parse(a), y = parse(b);
  for (let i = 0; i < Math.max(x.nums.length, y.nums.length); i++) {
    const d = (x.nums[i] ?? 0) - (y.nums[i] ?? 0); if (d) return Math.sign(d);
  }
  if (x.pre === y.pre) return 0;
  if (!x.pre) return 1; if (!y.pre) return -1;
  return x.pre < y.pre ? -1 : 1;
}

const exists = (file: string) => access(file).then(() => true, () => false);
const normalize = (name: string) => name.toLowerCase().replace(/[-_.]+/g, '_');

/** Version on disk, or null when missing. Reads metadata files only (no process is started). */
export async function installedVersion(c: Component, root: string): Promise<string | null> {
  const at = (p: string) => path.join(root, p);
  if (c.installed?.json) {
    try { const value = JSON.parse(await readFile(at(c.installed.json), 'utf8'))[c.installed.key ?? 'version']; return typeof value === 'string' ? value : null; }
    catch { return null; }
  }
  if (c.installed?.exists) return await exists(at(c.installed.exists)) ? c.version : null;
  if (c.kind === 'python-venv' && c.path && 'package' in c.source) {
    const lib = at(path.join(c.path, 'lib'));
    const name = normalize(c.source.package);
    for (const python of await readdir(lib).catch(() => [] as string[])) {
      for (const entry of await readdir(path.join(lib, python, 'site-packages')).catch(() => [] as string[])) {
        if (!entry.endsWith('.dist-info')) continue;
        const base = entry.slice(0, -'.dist-info'.length), dash = base.indexOf('-'); // {name}-{version}.dist-info
        if (dash > 0 && normalize(base.slice(0, dash)) === name) return base.slice(dash + 1);
      }
    }
    return null;
  }
  if (c.kind === 'npm-dep') {
    try { return JSON.parse(await readFile(at(path.join('node_modules', c.packages![0], 'package.json')), 'utf8')).version ?? null; } catch { return null; }
  }
  if (c.kind === 'model' && c.files) {
    for (const file of c.files) { const info = await stat(at(file.path)).catch(() => undefined); if (!info || info.size !== file.size) return null; }
    return c.version;
  }
  return null;
}

export type ExecResult = { code: number; stdout: string; stderr: string };
/** `env` is merged into the app's environment; `cleanEnv` replaces it (smoke tests run like a developer shell). */
export type Exec = (cmd: string, args: string[], options: { cwd: string; env?: Record<string, string>; cleanEnv?: boolean; signal: AbortSignal; timeoutMs?: number }) => Promise<ExecResult>;
/** PATH for installers: Homebrew, ~/.local/bin and this Node (the app may be launched with a minimal PATH). */
export function toolPath(): string {
  return [path.dirname(process.execPath), '/opt/homebrew/bin', '/usr/local/bin', path.join(homedir(), '.local/bin'), process.env.PATH || '/usr/bin:/bin'].join(path.delimiter);
}
export const exec: Exec = (cmd, args, options) => new Promise((resolve, reject) => {
  options.signal.throwIfAborted();
  const env = options.cleanEnv ? { ...options.env } : { ...process.env, PATH: toolPath(), ...options.env };
  const child = spawn(cmd, args, { cwd: options.cwd, env, stdio: ['ignore', 'pipe', 'pipe'] });
  let stdout = '', stderr = '';
  child.stdout.setEncoding('utf8').on('data', (d: string) => { stdout = (stdout + d).slice(-200_000); });
  child.stderr.setEncoding('utf8').on('data', (d: string) => { stderr = (stderr + d).slice(-200_000); });
  const stop = () => { void stopProcess(child); };
  const timer = options.timeoutMs ? setTimeout(stop, options.timeoutMs) : undefined;
  options.signal.addEventListener('abort', stop, { once: true });
  child.once('error', error => { clearTimeout(timer); options.signal.removeEventListener('abort', stop); reject(error); });
  child.once('close', code => {
    clearTimeout(timer); options.signal.removeEventListener('abort', stop);
    if (options.signal.aborted) reject(options.signal.reason ?? Error('Aborted')); else resolve({ code: code ?? 1, stdout, stderr });
  });
});

export type InstallContext = { root: string; signal: AbortSignal; exec?: Exec; log?: (line: string) => void };
/** Result of staging: a staging dir (binary/npm-runtime/python-venv), staging files (model) or an in-place change (npm-dep). */
export type Staged = { dir?: string; files?: { file: string; staging: string }[]; inPlace?: boolean; done?: boolean };

const run = async (ctx: InstallContext, cmd: string, args: string[], env: Record<string, string> = {}, timeoutMs = 30 * 60_000) => {
  ctx.log?.(`$ ${[cmd, ...args].join(' ')}`);
  const result = await (ctx.exec ?? exec)(cmd, args, { cwd: ctx.root, env, signal: ctx.signal, timeoutMs });
  if (result.code !== 0) throw Error(`${path.basename(cmd)} failed: ${(result.stderr || result.stdout).trim().split('\n').slice(-3).join(' ').slice(0, 300)}`);
  return result;
};
const backupDir = (root: string, id: string) => path.join(root, 'data/updates/prev', id);
const npmBin = () => path.join(path.dirname(process.execPath), 'npm');

export async function sha256File(file: string): Promise<string> {
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(file)) hash.update(chunk as Buffer);
  return hash.digest('hex');
}
export function fileUrl(c: Component, file: ModelFile, revision?: string): string {
  if (file.url) return file.url;
  if (c.source.type !== 'hf' || !file.remote) throw Error(`No download URL for ${file.path}`);
  return `https://huggingface.co/${c.source.repo}/resolve/${revision ?? c.source.revision ?? 'main'}/${file.remote.split('/').map(encodeURIComponent).join('/')}`;
}

/** Downloads with resume (`.part`), verifies size and SHA-256, leaves `<file>.staging`. */
export async function downloadFile(ctx: InstallContext, url: string, file: ModelFile, target: string): Promise<string> {
  const part = target + '.part', staging = target + '.staging';
  await mkdir(path.dirname(target), { recursive: true });
  if ((await stat(staging).catch(() => undefined))?.size === file.size && await sha256File(staging) === file.sha256) return staging;
  await run(ctx, 'curl', ['-fL', '--retry', '3', '-C', '-', '-o', part, url], {}, 6 * 3600_000);
  const size = (await stat(part)).size;
  if (size !== file.size) throw Error(`Size mismatch for ${path.basename(target)}: ${size}`);
  if (await sha256File(part) !== file.sha256) { await rm(part, { force: true }); throw Error(`Checksum mismatch for ${path.basename(target)}`); }
  await rename(part, staging);
  return staging;
}

/** Installs `target` next to the live copy; nothing in use is touched (npm-dep: package files are backed up first). */
export async function stage(c: Component, target: Target, ctx: InstallContext): Promise<Staged> {
  const root = ctx.root;
  if (c.kind === 'model') {
    if (c.install?.hf_snapshot) {
      if (c.source.type !== 'hf') throw Error('hf_snapshot needs an hf source');
      await run(ctx, path.join(root, c.install.python ?? 'runtime/ema/bin/python'), ['-c',
        'import sys; from huggingface_hub import snapshot_download; snapshot_download(sys.argv[1], revision=sys.argv[2])', c.source.repo,
        target.revision ?? c.source.revision ?? 'main'], { HF_HOME: path.join(root, c.install.hf_snapshot) });
      return { done: true };
    }
    const files = target.files ?? c.files ?? [];
    const staged = [];
    for (const file of files) staged.push({ file: path.join(root, file.path), staging: await downloadFile(ctx, fileUrl(c, file, target.revision), file, path.join(root, file.path)) });
    return { files: staged };
  }
  if (c.kind === 'npm-dep') {
    const backup = backupDir(root, c.id);
    await rm(backup, { recursive: true, force: true }); await mkdir(backup, { recursive: true });
    for (const name of ['package.json', 'package-lock.json']) await cp(path.join(root, name), path.join(backup, name));
    await writeFile(path.join(backup, 'VERSION'), c.version);
    await run(ctx, npmBin(), ['install', '--save-exact', '--ignore-scripts', '--no-audit', '--no-fund', '--loglevel=error',
      ...c.packages!.map(p => `${p}@${target.version}`)]);
    return { inPlace: true };
  }
  if (!c.path) throw Error(`${c.id}: no path`);
  const dir = path.join(root, c.path + '.staging');
  await rm(dir, { recursive: true, force: true });
  if (c.kind === 'python-venv') {
    if (!c.python || !c.requirements || !('package' in c.source)) throw Error(`${c.id}: python/requirements missing`);
    await run(ctx, 'uv', ['venv', '--quiet', '--python', c.python, dir]);
    const requirements = path.join(dir, 'requirements.txt'), python = path.join(dir, 'bin/python');
    await cp(path.join(root, c.requirements), requirements);
    // The pinned set installs exactly (sync). A newer version starts from that set and changes only what it needs: its new
    // dependencies may conflict with old exact pins (e.g. normalizer-tr==0.4.0 vs >=0.6), so the pins are not constraints.
    await run(ctx, 'uv', ['pip', 'sync', '--quiet', '--python', python, requirements]);
    if (target.version !== c.version) await run(ctx, 'uv', ['pip', 'install', '--quiet', '--python', python, `${c.source.package}==${target.version}`]);
    return { dir };
  }
  if (!c.install?.script) throw Error(`${c.id}: no installer`);
  await run(ctx, '/bin/sh', [path.join(root, c.install.script)], { COMPONENT_VERSION: target.version, COMPONENT_TARGET: dir,
    COMPONENT_SHA256: target.sha256 ?? (target.version === c.version ? c.sha256 ?? '' : '') });
  return { dir };
}

/**
 * Environment for `npm run check` / `npm test` during an update: what a developer shell has (HOME, USER, LANG, PATH, a fresh
 * TMPDIR), nothing the running app added (API keys from .env, Pi/agent flags, proxies, its bundle/XPC identity).
 */
export function testEnv(tmp: string, source: NodeJS.ProcessEnv = process.env): Record<string, string> {
  const env: Record<string, string> = { PATH: toolPath(), TMPDIR: tmp, CI: '1', NO_COLOR: '1', FORCE_COLOR: '0' };
  for (const name of ['HOME', 'USER', 'LOGNAME', 'SHELL', 'LANG', 'LC_ALL', 'TZ']) if (source[name]) env[name] = source[name]!;
  return env;
}
/** First failing test and its message from node:test spec output (short), e.g. `harness.test.ts › search parser: Missing expected rejection`. */
export function firstFailure(output: string): string | undefined {
  const lines = output.split('\n');
  let start = lines.findIndex(l => /^\s*✖ failing tests:/.test(l));
  if (start < 0) start = 0;
  for (let i = start; i < lines.length; i++) {
    const m = /^\s*✖ (.+?)(?: \([\d.]+m?s\))?\s*$/.exec(lines[i]);
    if (!m || /failing tests:/.test(m[1])) continue;
    const file = lines.slice(Math.max(start, i - 2), i).map(l => /^\s*test at (.+?):\d+:\d+/.exec(l)?.[1]).find(Boolean);
    const message = lines.slice(i + 1, i + 8).map(l => l.trim()).find(l => /Error|expected|Missing|rejection|!==|Expected/.test(l)) ?? '';
    return `${file ? path.basename(file) + ' › ' : ''}${m[1]}${message ? ': ' + message : ''}`.slice(0, 240);
  }
  return undefined;
}

/** Smoke test of a staged install (command + expected output; npm deps: type check and tests in a developer-like env). */
export async function smoke(c: Component, staged: Staged, version: string, ctx: InstallContext): Promise<{ ok: boolean; detail: string }> {
  try {
    if (c.kind === 'npm-dep') {
      const tmp = await mkdtemp(path.join(tmpdir(), 'ayhan-smoke-'));
      try {
        for (const script of [['run', 'check'], ['test']]) {
          ctx.log?.(`$ npm ${script.join(' ')}`);
          const result = await (ctx.exec ?? exec)(npmBin(), script, { cwd: ctx.root, env: testEnv(tmp), cleanEnv: true, signal: ctx.signal, timeoutMs: 20 * 60_000 });
          if (result.code === 0) continue;
          const output = result.stdout + '\n' + result.stderr;
          await mkdir(path.join(ctx.root, 'data/updates'), { recursive: true });
          await writeFile(path.join(ctx.root, 'data/updates', `smoke-${c.id}.log`), output.slice(-200_000), { mode: 0o600 }).catch(() => {});
          const reason = script[0] === 'test' ? firstFailure(output) : output.trim().split('\n').find(l => /error TS\d+/.test(l))?.trim();
          return { ok: false, detail: `npm ${script.join(' ')}: ${(reason ?? output.trim().split('\n').slice(-3).join(' ')).slice(0, 240)}` };
        }
      } finally { await rm(tmp, { recursive: true, force: true }); }
      return { ok: true, detail: 'check + test' };
    }
    if (!c.smoke || !staged.dir) return { ok: true, detail: c.kind === 'model' ? 'sha256' : 'none' };
    const fill = (v: string) => v.replaceAll('{dir}', staged.dir!).replaceAll('{node}', process.execPath).replaceAll('{version}', version);
    const [cmd, ...args] = c.smoke.cmd.map(fill);
    const result = await (ctx.exec ?? exec)(cmd, args, { cwd: ctx.root, signal: ctx.signal, timeoutMs: 120_000 });
    const output = result.stdout + result.stderr;
    const expect = new RegExp(c.smoke.expect.replaceAll('{version}', version.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')), 'im');
    return expect.test(output) ? { ok: true, detail: output.trim().split('\n')[0].slice(0, 120) } : { ok: false, detail: `unexpected output: ${output.trim().slice(0, 160)}` };
  } catch (error) { ctx.signal.throwIfAborted(); return { ok: false, detail: error instanceof Error ? error.message : 'smoke test failed' }; }
}

/** After a venv update: its requirements file records the installed set exactly (the user commits it). */
export async function freezeRequirements(c: Component, ctx: InstallContext): Promise<void> {
  if (c.kind !== 'python-venv' || !c.requirements || !c.path) return;
  const { stdout } = await run(ctx, 'uv', ['pip', 'freeze', '--python', path.join(ctx.root, c.path, 'bin/python')]);
  const lines = stdout.split('\n').map(l => l.trim()).filter(l => /^[A-Za-z0-9_.-]+==/.test(l)).sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
  if (lines.length) await writeFile(path.join(ctx.root, c.requirements), lines.join('\n') + '\n');
}

/** Venvs are not relocatable: scripts and activate files name the staging path; point them at the live path. */
async function relocateVenv(dir: string, from: string, to: string): Promise<void> {
  const bin = path.join(dir, 'bin');
  for (const name of await readdir(bin).catch(() => [] as string[])) {
    const file = path.join(bin, name);
    const info = await stat(file).catch(() => undefined);
    if (!info?.isFile() || info.size > 1_000_000) continue;
    const text = await readFile(file).catch(() => undefined);
    if (!text || text.includes(0)) continue;
    const value = text.toString('utf8');
    if (value.includes(from)) await writeFile(file, value.replaceAll(from, to), { mode: info.mode });
  }
  const cfg = path.join(dir, 'pyvenv.cfg');
  try { await writeFile(cfg, (await readFile(cfg, 'utf8')).replaceAll(from, to)); } catch {}
}

/** Atomic swap: live → `.prev` (older `.prev` removed), staged → live. Never aborted half-way. */
export async function swap(c: Component, staged: Staged, root: string): Promise<void> {
  if (staged.inPlace || staged.done) return;
  if (staged.files) {
    for (const { file, staging } of staged.files) {
      await rm(file + '.prev', { force: true });
      if (await exists(file)) await rename(file, file + '.prev');
      await rename(staging, file);
    }
    return;
  }
  const live = path.join(root, c.path!), prev = live + '.prev';
  if (c.kind === 'python-venv') await relocateVenv(staged.dir!, staged.dir!, live);
  await rm(prev, { recursive: true, force: true });
  if (await exists(live)) await rename(live, prev);
  await rename(staged.dir!, live);
}

/** Drops a failed staging; npm-dep: restores package files and node_modules. */
export async function discard(c: Component, staged: Staged | undefined, ctx: InstallContext): Promise<void> {
  if (c.kind === 'npm-dep') {
    const backup = backupDir(ctx.root, c.id);
    if (!await exists(path.join(backup, 'package.json'))) return;
    for (const name of ['package.json', 'package-lock.json']) await cp(path.join(backup, name), path.join(ctx.root, name));
    await run({ ...ctx, signal: new AbortController().signal }, npmBin(), ['ci', '--ignore-scripts', '--no-audit', '--no-fund', '--loglevel=error']);
    await rm(backup, { recursive: true, force: true });
    return;
  }
  if (staged?.dir) await rm(staged.dir, { recursive: true, force: true });
  for (const file of staged?.files ?? []) await rm(file.staging, { force: true });
  if (!staged && c.path) await rm(path.join(ctx.root, c.path + '.staging'), { recursive: true, force: true });
}

export async function hasPrevious(c: Component, root: string): Promise<boolean> {
  if (c.kind === 'npm-dep') return exists(path.join(backupDir(root, c.id), 'package.json'));
  if (c.kind === 'model') return !!c.files?.length && (await Promise.all(c.files.map(f => exists(path.join(root, f.path + '.prev'))))).every(Boolean);
  return !!c.path && exists(path.join(root, c.path + '.prev'));
}

/** Swaps live and `.prev` (so a rollback can itself be undone); npm-dep: restores the backed-up package files. */
export async function rollback(c: Component, ctx: InstallContext): Promise<void> {
  if (!await hasPrevious(c, ctx.root)) throw Error(`${c.id}: no previous version`);
  const flip = async (live: string) => {
    const held = live + '.rollback';
    await rm(held, { recursive: true, force: true });
    await rename(live, held); await rename(live + '.prev', live); await rename(held, live + '.prev');
  };
  if (c.kind === 'npm-dep') {
    const backup = backupDir(ctx.root, c.id), current = backup + '.current';
    await rm(current, { recursive: true, force: true }); await mkdir(current, { recursive: true });
    for (const name of ['package.json', 'package-lock.json']) {
      await cp(path.join(ctx.root, name), path.join(current, name)); await cp(path.join(backup, name), path.join(ctx.root, name));
    }
    await run(ctx, npmBin(), ['ci', '--ignore-scripts', '--no-audit', '--no-fund', '--loglevel=error']);
    await rm(backup, { recursive: true, force: true }); await rename(current, backup);
    return;
  }
  if (c.kind === 'model') { for (const f of c.files!) await flip(path.join(ctx.root, f.path)); return; }
  await flip(path.join(ctx.root, c.path!));
}

/** Decode speed (tokens/s) of a llama-server build on a temporary loopback port; the process is always stopped. */
export async function llamaTokensPerSecond(bin: string, model: string, signal: AbortSignal): Promise<number> {
  const port = await new Promise<number>((resolve, reject) => {
    const server = createServer().once('error', reject).listen(0, '127.0.0.1', () => {
      const address = server.address(); server.close(() => resolve(typeof address === 'object' && address ? address.port : 0));
    });
  });
  const child = spawn(bin, ['-m', model, '-ngl', '999', '-c', '2048', '--parallel', '1', '--poll', '0', '--host', '127.0.0.1', '--port', String(port)], { stdio: 'ignore' });
  const base = `http://127.0.0.1:${port}`;
  try {
    const deadline = AbortSignal.any([signal, AbortSignal.timeout(180_000)]);
    for (;;) {
      deadline.throwIfAborted();
      if (child.exitCode !== null) throw Error('llama-server exited');
      const ok = await fetch(base + '/health', { signal: AbortSignal.any([deadline, AbortSignal.timeout(2000)]) }).then(r => r.ok, () => false);
      if (ok) break;
      await delay(500, undefined, { signal: deadline });
    }
    const complete = async (n: number) => {
      const response = await fetch(base + '/completion', { method: 'POST', signal: AbortSignal.any([signal, AbortSignal.timeout(120_000)]),
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: 'Write a short paragraph about the sea and the wind.', n_predict: n, temperature: 0, cache_prompt: false }) });
      const value = await response.json() as { timings?: { predicted_per_second?: number } };
      return value.timings?.predicted_per_second ?? 0;
    };
    await complete(8); // warm-up
    return await complete(96);
  } finally { await stopProcess(child); }
}
