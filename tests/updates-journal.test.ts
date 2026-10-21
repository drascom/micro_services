import test from 'node:test';
import assert from 'node:assert/strict';
import { access, mkdir, mkdtemp, readdir, readFile, rename, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { exec as realExec, type Component, type Exec, type Manifest } from '../src/components.ts';
import { UpdateManager, type Http } from '../src/updates.ts';
import { readJournal, writeJournal, type Journal, type Step } from '../src/updates-journal.ts';
import type { TaskLane } from '../src/tasks.ts';
import { ConfirmationStore } from '../.pi/extensions/apple-reminders/confirmations.ts';

// Crash-safe journal: a "crash" is a journal hook that never returns (the disk state stays exactly as at that step);
// a fresh manager on the same root is the restarted backend and must end clean (finished or rolled back).
const signal = new AbortController().signal;
const freeLane: TaskLane = { acquire: async s => ({ signal: s, release: () => {} }) };
const exists = (file: string) => access(file).then(() => true, () => false);
const component = (extra: Partial<Component>): Component => ({ id: 'x', name: 'X', kind: 'binary', version: '1.0.0', source: { type: 'local' }, channel: 'stable',
  platform: 'any', restart: 'none', ...extra } as Component);
const http = (async (url: string) => {
  if (url.startsWith('https://api.github.com/repos/o/tool/releases')) return [{ tag_name: '1.1.0', draft: false, prerelease: false, body: '', html_url: 'u', assets: [] }];
  const npm = /registry\.npmjs\.org\/-\/package\/(\w+)\/dist-tags/.exec(url);
  if (npm) return { latest: '2.0.0' };
  throw Error('HTTP 404 ' + url);
}) as Http;

/** Fake npm: install/ci write node_modules from package.json; check/test pass; the import self-check fails for `badImport`. */
function fakeNpm(root: string, state: { badImport?: string; calls: string[] }): Exec {
  const pkg = path.join(root, 'package.json');
  const sync = async () => {
    const deps = JSON.parse(await readFile(pkg, 'utf8')).dependencies as Record<string, string>;
    for (const [name, version] of Object.entries(deps)) {
      await mkdir(path.join(root, 'node_modules', name), { recursive: true });
      await writeFile(path.join(root, 'node_modules', name, 'package.json'), JSON.stringify({ name, version }));
    }
  };
  return async (cmd, args, options) => {
    if (path.basename(cmd) !== 'npm' && args[0] !== '--input-type=module') return realExec(cmd, args, options); // installers, smoke tests
    state.calls.push([path.basename(cmd), ...args.filter(a => !a.startsWith('--'))].join(' '));
    if (args[0] === 'install') {
      const value = JSON.parse(await readFile(pkg, 'utf8'));
      for (const spec of args.slice(1).filter(a => !a.startsWith('--'))) { const [name, version] = spec.split('@'); value.dependencies[name] = version; }
      await writeFile(pkg, JSON.stringify(value)); await sync();
    } else if (args[0] === 'ci') await sync();
    else if (args[0] === '--input-type=module' && state.badImport && args.includes(state.badImport)) return { code: 1, stdout: '', stderr: `Error: Cannot find module '${state.badImport}'` };
    return { code: 0, stdout: '', stderr: '' };
  };
}

async function fixture(kind: 'tool' | 'npm') {
  const root = await mkdtemp(path.join(tmpdir(), 'ayhan-journal-'));
  await mkdir(path.join(root, 'scripts')); await mkdir(path.join(root, 'runtime/tool'), { recursive: true });
  await writeFile(path.join(root, 'runtime/tool/VERSION'), '1.0.0\n');
  const tool = component({ id: 'tool', name: 'Tool', source: { type: 'github', repo: 'o/tool' }, path: 'runtime/tool', restart: 'llama',
    installed: { json: 'runtime/tool/BUILD.json' }, install: { script: 'scripts/install-tool.sh' }, smoke: { cmd: ['/bin/cat', '{dir}/VERSION'], expect: '^{version}$' } });
  // Installer contract: COMPONENT_TARGET, COMPONENT_VERSION; BUILD.json is the installed version.
  await writeFile(path.join(root, 'scripts/install-tool.sh'), 'mkdir -p "$COMPONENT_TARGET"\necho "$COMPONENT_VERSION" > "$COMPONENT_TARGET/VERSION"\n' +
    'printf \'{"version":"%s"}\' "$COMPONENT_VERSION" > "$COMPONENT_TARGET/BUILD.json"\n');
  await writeFile(path.join(root, 'runtime/tool/BUILD.json'), '{"version":"1.0.0"}');
  const npm = (id: string) => component({ id, name: id.toUpperCase(), kind: 'npm-dep', packages: ['p' + id], source: { type: 'npm', package: 'p' + id }, restart: 'app' });
  const manifest: Manifest = { schema: 1, components: kind === 'tool' ? [tool] : [npm('a'), npm('b')] };
  await writeFile(path.join(root, 'components.json'), JSON.stringify(manifest));
  await writeFile(path.join(root, 'package.json'), JSON.stringify({ dependencies: { pa: '1.0.0', pb: '1.0.0' } }));
  await writeFile(path.join(root, 'package-lock.json'), '{}');
  const npmState = { calls: [] as string[], badImport: undefined as string | undefined };
  const exec = fakeNpm(root, npmState);
  await exec('npm', ['ci'], { cwd: root, signal });
  const restarts: string[] = [], notes: string[] = [];
  let busy = false;
  const make = (hook?: (id: string, step: Step) => Promise<void> | void, lane: TaskLane = freeLane) => new UpdateManager({ root, stateFile: path.join(root, 'data/updates.json'),
    lane, http, exec, store: new ConfirmationStore(), restart: async target => { restarts.push(target); }, notify: async (_t, body) => { notes.push(body); },
    perf: async () => 30, busy: () => busy, journalHook: hook });
  /** Starts an apply that "crashes" right after `step` is journaled; resolves once it did. */
  const crashAt = async (ids: string[], step: Step, times = 1) => {
    let seen = 0, hit!: () => void;
    const crashed = new Promise<void>(resolve => { hit = resolve; });
    const manager = make((_id, s) => { if (s === step && ++seen >= times) { hit(); return new Promise<void>(() => {}); } });
    await manager.check(signal);
    manager.startApply(ids);
    await crashed;
  };
  const journal = async () => (await readJournal(path.join(root, 'data/updates/journal.json'))).journal;
  const read = (file: string) => readFile(path.join(root, file), 'utf8').then(t => t.trim());
  const manifestVersion = async (id: string) => (JSON.parse(await read('components.json')) as Manifest).components.find(c => c.id === id)!.version;
  const leftovers = async () => (await readdir(path.join(root, 'runtime'))).filter(n => /\.(staging|rollback)$/.test(n));
  return { root, make, crashAt, journal, read, manifestVersion, leftovers, restarts, notes, npmState, setBusy: (v: boolean) => { busy = v; },
    cleanup: () => rm(root, { recursive: true, force: true }) };
}

const expected: [Step, 'verified' | 'failed' | 'rolled_back'][] = [['staging', 'failed'], ['staged', 'failed'], ['smoke_ok', 'failed'], ['swapping', 'failed'],
  ['swapped', 'verified'], ['manifest_written', 'verified'], ['restart_pending', 'verified'], ['verifying', 'verified']];
for (const [step, outcome] of expected) {
  test(`crash after "${step}": startup recovery ends ${outcome}, no staging left, live = manifest`, async () => {
    const f = await fixture('tool');
    try {
      await f.crashAt(['tool'], step);
      const restarted = f.make();
      await restarted.recover();
      const run = (await f.journal()).run!;
      assert.equal(run.done, true); assert.equal(run.components[0].status, outcome);
      assert.deepEqual(await f.leftovers(), []);
      const version = outcome === 'verified' ? '1.1.0' : '1.0.0';
      assert.equal(await f.read('runtime/tool/VERSION'), version); assert.equal(await f.manifestVersion('tool'), version);
      const said = await restarted.announcement();
      assert.match(said!.text, outcome === 'verified' ? /^Tool 1\.1\.0 kuruldu, sorun yok\.$/ : /Tool 1\.1\.0 kurulumu yarıda kaldı/);
      assert.equal(await restarted.shown(said!.id), true);
      assert.notEqual((await restarted.announcement())?.id, said!.id, 'said once');
      const again = f.make(); await again.recover(); // idempotent: a second start changes nothing
      assert.equal((await f.journal()).run!.components[0].status, outcome);
      assert.deepEqual((await again.list()).last_run!.components[0].steps.at(-1), outcome);
    } finally { await f.cleanup(); }
  });
}

test('crash in the middle of the swap (live moved to .prev, staging not yet live): the old copy is put back', async () => {
  const f = await fixture('tool');
  try {
    await f.crashAt(['tool'], 'swapping');
    await rename(path.join(f.root, 'runtime/tool'), path.join(f.root, 'runtime/tool.prev'));
    await f.make().recover();
    assert.equal(await f.read('runtime/tool/VERSION'), '1.0.0');
    assert.deepEqual(await f.leftovers(), []);
    assert.equal((await f.journal()).run!.components[0].status, 'failed');
  } finally { await f.cleanup(); }
});

test('crash after the swap with an incomplete live copy: rolled back from .prev', async () => {
  const f = await fixture('tool');
  try {
    await f.crashAt(['tool'], 'swapped');
    await rm(path.join(f.root, 'runtime/tool/BUILD.json')); // swapped copy unverifiable
    await f.make().recover();
    assert.equal(await f.read('runtime/tool/VERSION'), '1.0.0'); assert.equal(await f.manifestVersion('tool'), '1.0.0');
    assert.equal((await f.journal()).run!.components[0].status, 'rolled_back');
  } finally { await f.cleanup(); }
});

test('restart_pending → verification fails → automatic rollback from .prev and the process restarted; said once', async () => {
  const f = await fixture('tool');
  try {
    await f.crashAt(['tool'], 'restart_pending');
    await writeFile(path.join(f.root, 'runtime/tool/VERSION'), 'broken\n'); // smoke on the live copy fails
    f.restarts.length = 0;
    const restarted = f.make();
    await restarted.recover();
    const entry = (await f.journal()).run!.components[0];
    assert.equal(entry.status, 'rolled_back'); assert.match(entry.reason!, /doğrulama/);
    assert.equal(await f.read('runtime/tool/VERSION'), '1.0.0'); assert.equal(await f.manifestVersion('tool'), '1.0.0');
    assert.deepEqual(f.restarts, ['llama']);
    assert.match((await restarted.announcement())!.text, /^Tool geri alındı: doğrulama: /);
    assert.equal((await restarted.list()).rollback[0].version, '1.1.0', 'the rollback can itself be undone');
  } finally { await f.cleanup(); }
});

test('a crash during verification twice counts as a failure (no crash loop)', async () => {
  const f = await fixture('tool');
  try {
    await f.crashAt(['tool'], 'restart_pending');
    for (let i = 0; i < 2; i++) {
      let hit!: () => void; const crashed = new Promise<void>(resolve => { hit = resolve; });
      void f.make((_id, s) => { if (s === 'verifying') { hit(); return new Promise<void>(() => {}); } }).recover();
      await crashed;
    }
    await f.make().recover();
    const entry = (await f.journal()).run!.components[0];
    assert.equal(entry.verify_attempts, 3); assert.equal(entry.status, 'rolled_back'); assert.match(entry.reason!, /interrupted/);
  } finally { await f.cleanup(); }
});

test('npm batch: one rebuild and one app restart for two deps, asked only when idle; verified after the restart', async () => {
  const f = await fixture('npm');
  try {
    const manager = f.make();
    await manager.check(signal);
    manager.startApply(['a', 'b']); await manager.idle();
    assert.deepEqual(f.restarts, ['app'], 'one rebuild for the batch');
    let run = (await f.journal()).run!;
    assert.equal(run.phase, 'awaiting_restart');
    assert.deepEqual(run.components.map(e => e.steps.map(s => s.step).filter(s => !['staging', 'swapping'].includes(s))),
      [['staged', 'smoke_ok', 'swapped', 'manifest_written', 'rebuilt', 'restart_pending'], ['staged', 'smoke_ok', 'swapped', 'manifest_written', 'rebuilt', 'restart_pending']]);
    f.setBusy(true);
    assert.equal(await manager.announcement(), undefined, 'waits while a request/speech runs');
    f.setBusy(false);
    const ask = await manager.announcement();
    assert.equal(ask?.restart, true);
    f.setBusy(true); assert.equal(await manager.shown(ask!.id), false, 'busy again: not now'); f.setBusy(false);
    assert.equal(await manager.shown(ask!.id), true);
    assert.equal(await manager.announcement(), undefined, 'asked once');
    // The restarted backend verifies (import self-check) and says it.
    const restarted = f.make();
    await restarted.recover();
    run = (await f.journal()).run!;
    assert.equal(run.done, true); assert.deepEqual(run.components.map(e => e.status), ['verified', 'verified']);
    assert.equal((await restarted.announcement())!.text, 'A 2.0.0, B 2.0.0 kuruldu, yeniden başladım, sorun yok.');
    assert.deepEqual(f.restarts, ['app']);
    assert.equal(await f.read('node_modules/pa/package.json').then(t => JSON.parse(t).version), '2.0.0');
  } finally { await f.cleanup(); }
});

test('npm verify fails after the restart → rollback (npm ci), one more restart at most, then it is said', async () => {
  const f = await fixture('npm');
  try {
    const manager = f.make();
    await manager.check(signal);
    manager.startApply(['a']); await manager.idle();
    await manager.shown((await manager.announcement())!.id);
    f.npmState.badImport = 'pa';
    const second = f.make();
    await second.recover();
    let run = (await f.journal()).run!;
    assert.equal(run.components[0].status, 'rolled_back'); assert.equal(run.restart.rollback_restarts, 1);
    assert.equal(await f.manifestVersion('a'), '1.0.0');
    assert.equal(await f.read('node_modules/pa/package.json').then(t => JSON.parse(t).version), '1.0.0');
    const ask = await second.announcement();
    assert.equal(ask?.restart, true, 'restart to load the restored deps');
    assert.equal(await second.shown(ask!.id), true);
    const third = f.make();
    await third.recover();
    run = (await f.journal()).run!;
    assert.equal(run.done, true);
    const said = await third.announcement();
    assert.match(said!.text, /^A geri alındı: doğrulama: Error: Cannot find module 'pa'/);
    assert.equal(said!.restart, undefined);
    await third.shown(said!.id);
    assert.notEqual((await third.announcement())?.restart, true, 'no restart loop');
  } finally { await f.cleanup(); }
});

test('npm crash after install but before the manifest: package files restored, nothing half-installed', async () => {
  const f = await fixture('npm');
  try {
    await f.crashAt(['a'], 'smoke_ok');
    assert.equal(JSON.parse(await f.read('package.json')).dependencies.pa, '2.0.0');
    await f.make().recover();
    assert.equal(JSON.parse(await f.read('package.json')).dependencies.pa, '1.0.0');
    assert.equal(await f.read('node_modules/pa/package.json').then(t => JSON.parse(t).version), '1.0.0');
    assert.equal((await f.journal()).run!.components[0].status, 'failed');
  } finally { await f.cleanup(); }
});

test('journal: atomic write (no temp left), corrupt/partial file set aside and staging leftovers swept', async () => {
  const f = await fixture('tool');
  try {
    const file = path.join(f.root, 'data/updates/journal.json');
    await writeJournal(file, { schema: 1 } as Journal);
    assert.deepEqual(await readdir(path.dirname(file)), ['journal.json']);
    await writeFile(file, '{"schema":1,"run":{"id":"r1","compo');
    await mkdir(path.join(f.root, 'runtime/tool.staging'));
    const manager = f.make();
    await manager.recover();
    assert.ok(await exists(path.join(f.root, 'data/updates/journal.corrupt.json')));
    assert.deepEqual(await f.leftovers(), []);
    assert.equal(await manager.announcement(), undefined);
    assert.equal((await manager.list()).last_run, null);
  } finally { await f.cleanup(); }
});
