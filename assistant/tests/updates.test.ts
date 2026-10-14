import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, rm, writeFile, access } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import type { ExtensionAPI } from '@earendil-works/pi-coding-agent';
import { compareVersions, pinRequirement, type Component, type Manifest } from '../src/components.ts';
import { detect, parseSummary, UpdateManager, type Http } from '../src/updates.ts';
import type { TaskLane } from '../src/tasks.ts';
import { ConfirmationStore, currentTurn, newTurn } from '../.pi/extensions/apple-reminders/confirmations.ts';
import { componentUpdates } from '../.pi/extensions/updates/index.ts';

// Fake upstreams (no network), fake installers (shell scripts in a temp root) and a fake clock.
const signal = new AbortController().signal;
const T0 = new Date(2026, 9, 14, 10, 0).getTime();
const freeLane: TaskLane = { acquire: async s => ({ signal: s, release: () => {} }) };
const exists = (file: string) => access(file).then(() => true, () => false);

function fakeHttp(routes: Record<string, unknown>): Http & { calls: string[] } {
  const calls: string[] = [];
  const http = (async (url: string) => {
    calls.push(url);
    const key = Object.keys(routes).find(k => url.startsWith(k));
    if (!key) throw Error('HTTP 404 ' + url);
    return structuredClone(routes[key]);
  }) as unknown as Http & { calls: string[] };
  http.calls = calls;
  return http;
}
const release = (tag: string, extra: Record<string, unknown> = {}) => ({ tag_name: tag, draft: false, prerelease: false, body: `notes ${tag}`, html_url: `https://x/${tag}`, assets: [], ...extra });
const component = (extra: Partial<Component>): Component => ({ id: 'x', name: 'X', kind: 'binary', version: '1.0.0', source: { type: 'local' }, channel: 'stable',
  platform: 'any', restart: 'none', ...extra } as Component);

test('versions order across tag styles', () => {
  assert.ok(compareVersions('b11617', 'b11574') > 0);
  assert.ok(compareVersions('v1.9.5', 'v1.9.4') > 0);
  assert.ok(compareVersions('2026.9.1', '2026.9.0') > 0);
  assert.ok(compareVersions('1.2.0-beta.1', '1.2.0') < 0);
  assert.equal(compareVersions('1.0', '1.0.0'), 0);
  assert.equal(pinRequirement('a==1\nkokoro-onnx==0.6.1\n', 'kokoro_onnx', '0.7.0'), 'a==1\nkokoro-onnx==0.7.0\n');
});

test('GitHub: prerelease policy, asset and tag family filters, digest and notes', async () => {
  const asset = (tag: string) => ({ name: `llama-${tag}-bin-macos-arm64.tar.gz`, digest: 'sha256:' + 'a'.repeat(64) });
  const http = fakeHttp({ 'https://api.github.com/repos/o/llama/releases': [
    release('b120', { prerelease: true, assets: [] }), release('b110', { prerelease: true, assets: [asset('b110')] }),
    release('b105', { prerelease: true, assets: [asset('b105')] }), release('b90', { prerelease: true, assets: [asset('b90')] })] });
  const c = component({ source: { type: 'github', repo: 'o/llama', asset: 'llama-{version}-bin-macos-arm64.tar.gz' }, channel: 'prerelease' });
  const up = await detect(c, 'b100', http, signal);
  assert.equal(up?.latest, 'b110'); // b120 has no macOS asset
  assert.equal(up?.sha256, 'a'.repeat(64));
  assert.match(up!.notes, /b110[\s\S]*b105/); assert.doesNotMatch(up!.notes, /b90/);
  assert.equal(await detect({ ...c, channel: 'stable' }, 'b100', http, signal), undefined, 'stable ignores prereleases');
  const family = fakeHttp({ 'https://api.github.com/repos/o/w/releases': [release('b4938'), release('v1.9.5'), release('v2.0.0-rc1', { prerelease: true })] });
  assert.equal((await detect(component({ source: { type: 'github', repo: 'o/w' } }), 'v1.9.4', family, signal))?.latest, 'v1.9.5');
});

test('npm, PyPI and Hugging Face detection', async () => {
  const http = fakeHttp({
    'https://registry.npmjs.org/-/package/@bitwarden%2Fcli/dist-tags': { latest: '2026.9.1', next: '2026.10.0-beta.1' },
    'https://api.github.com/repos/bitwarden/clients/releases/tags/cli-v2026.9.1': { body: 'Fixed unlock' },
    'https://pypi.org/pypi/kokoro-onnx/json': { info: { version: '0.6.1' }, releases: { '0.6.1': [{}], '0.7.0b1': [{}] } },
    'https://huggingface.co/api/models/o/m/tree/': [{ path: 'm.gguf', size: 10, lfs: { oid: 'b'.repeat(64) } }],
    'https://huggingface.co/api/models/o/m': { sha: 'f'.repeat(40) }
  });
  const npm = component({ kind: 'npm-runtime', source: { type: 'npm', package: '@bitwarden/cli', notes: { repo: 'bitwarden/clients', tag: 'cli-v{version}' } } });
  assert.deepEqual(await detect(npm, '2026.9.0', http, signal), { latest: '2026.9.1', notes: 'Fixed unlock', url: 'https://www.npmjs.com/package/@bitwarden/cli/v/2026.9.1' });
  assert.equal((await detect({ ...npm, channel: 'prerelease' }, '2026.9.0', http, signal))?.latest, '2026.10.0-beta.1');
  const pypi = component({ kind: 'python-venv', source: { type: 'pypi', package: 'kokoro-onnx' } });
  assert.equal(await detect(pypi, '0.6.1', http, signal), undefined);
  assert.equal((await detect({ ...pypi, channel: 'prerelease' }, '0.6.1', http, signal))?.latest, '0.7.0b1');
  const model = component({ kind: 'model', source: { type: 'hf', repo: 'o/m' }, files: [{ path: 'models/m.gguf', remote: 'm.gguf', size: 10, sha256: 'a'.repeat(64) }] });
  const up = await detect(model, 'abc1234', http, signal);
  assert.equal(up?.latest, 'fffffff'); assert.equal(up?.files?.[0].sha256, 'b'.repeat(64));
  assert.equal(await detect({ ...model, files: [{ ...model.files![0], sha256: 'b'.repeat(64) }] }, 'fffffff', http, signal), undefined);
});

test('summary JSON is parsed leniently and rejected when empty', () => {
  assert.deepEqual(parseSummary('Sure: {"summary": "Hız arttı. Hata düzeltildi.", "breaking": false, "security": true, "affects_our_flags": false}'),
    { summary: 'Hız arttı. Hata düzeltildi.', breaking: false, security: true, affects_our_flags: false });
  assert.equal(parseSummary('no json'), undefined);
  assert.equal(parseSummary('{"summary": ""}'), undefined);
});

/** A temp root with a manifest of fake components whose installers are shell scripts. */
async function fixture(options: { smokeOk?: boolean; components?: Component[]; lane?: TaskLane; perf?: (dir: string) => number } = {}) {
  const root = await mkdtemp(path.join(tmpdir(), 'ayhan-updates-'));
  await mkdir(path.join(root, 'scripts')); await mkdir(path.join(root, 'runtime/tool'), { recursive: true });
  await writeFile(path.join(root, 'runtime/tool/VERSION'), '1.0.0\n');
  // Installer contract: COMPONENT_TARGET, COMPONENT_VERSION. A broken build writes a wrong version.
  await writeFile(path.join(root, 'scripts/install-tool.sh'), `mkdir -p "$COMPONENT_TARGET"\necho "${options.smokeOk === false ? 'broken' : '$COMPONENT_VERSION'}" > "$COMPONENT_TARGET/VERSION"\n`);
  const tool = component({ id: 'tool', name: 'Tool', source: { type: 'github', repo: 'o/tool' }, path: 'runtime/tool', restart: 'llama',
    installed: { exists: 'runtime/tool/VERSION' }, install: { script: 'scripts/install-tool.sh' }, smoke: { cmd: ['/bin/cat', '{dir}/VERSION'], expect: '^{version}$' } });
  const manual = component({ id: 'model', name: 'Model', kind: 'model', channel: 'manual', source: { type: 'hf', repo: 'o/m' }, files: [] });
  const manifest: Manifest = { schema: 1, components: options.components ?? [tool, manual] };
  await writeFile(path.join(root, 'components.json'), JSON.stringify(manifest));
  const http = fakeHttp({ 'https://api.github.com/repos/o/tool/releases': [release('1.1.0')], 'https://huggingface.co/': { sha: 'f'.repeat(40) } });
  let now = T0;
  const restarts: string[] = [], notes: string[] = [], prompts: string[] = [];
  const store = new ConfirmationStore(() => now);
  const manager = new UpdateManager({ root, stateFile: path.join(root, 'data/updates.json'), lane: options.lane ?? freeLane, http, store,
    restart: async target => { restarts.push(target); }, notify: async (_t, body) => { notes.push(body); }, now: () => now, checkHour: () => 9,
    perf: async dir => options.perf?.(dir) ?? 30,
    summarize: async (_system, user) => { prompts.push(user); return '{"summary": "İki cümle. Tamam.", "breaking": true, "security": false, "affects_our_flags": false}'; } });
  const version = async () => (await readFile(path.join(root, 'runtime/tool/VERSION'), 'utf8')).trim();
  const manifestVersion = async () => JSON.parse(await readFile(path.join(root, 'components.json'), 'utf8')).components[0].version;
  return { root, manager, http, restarts, notes, prompts, store, version, manifestVersion, tick: (ms: number) => { now += ms; },
    cleanup: () => rm(root, { recursive: true, force: true }) };
}

test('check: manual components are never asked; new versions get a model summary', async () => {
  const f = await fixture();
  try {
    const found = await f.manager.check(signal);
    assert.deepEqual(found.map(x => [x.id, x.current, x.latest]), [['tool', '1.0.0', '1.1.0']]);
    assert.ok(!f.http.calls.some(url => url.includes('huggingface')), 'manual model not queried');
    assert.match(f.prompts[0], /Tool 1\.0\.0 -> 1\.1\.0[\s\S]*notes 1\.1\.0/);
    const view = await f.manager.list();
    assert.equal(view.updates[0].summary, 'İki cümle. Tamam.'); assert.equal(view.updates[0].breaking, true);
    const state = JSON.parse(await readFile(path.join(f.root, 'data/updates.json'), 'utf8'));
    assert.equal(state.available.tool.latest, '1.1.0');
    await f.manager.check(signal, { all: true });
    assert.ok(f.http.calls.some(url => url.includes('huggingface')), '--all includes manual');
  } finally { await f.cleanup(); }
});

test('daily schedule: due on startup after 24 h and once past the check hour', async () => {
  const f = await fixture();
  try {
    assert.equal(await f.manager.due(), true, 'never checked');
    await f.manager.check(signal);
    assert.equal(await f.manager.due(), false);
    f.tick(22 * 3600_000); // next day 08:00
    assert.equal(await f.manager.due(), false);
    f.tick(3600_000); // 09:00
    assert.equal(await f.manager.due(), true);
  } finally { await f.cleanup(); }
});

test('proactive offer: once a day, snoozed until the next morning, skip stops it', async () => {
  const f = await fixture();
  try {
    await f.manager.check(signal);
    const offer = await f.manager.announcement();
    assert.equal(offer?.text, '1 güncelleme var: Tool. Güncelleyeyim mi?');
    assert.equal(await f.manager.shown('wrong'), false);
    assert.equal(await f.manager.shown(offer!.id), true);
    assert.deepEqual(f.notes, [offer!.text]);
    assert.equal(await f.manager.announcement(), undefined, 'asked once today');
    assert.equal((await f.manager.list()).updates[0].status, 'snoozed');
    f.tick(20 * 3600_000); // tomorrow 06:00: still snoozed
    assert.equal(await f.manager.announcement(), undefined);
    f.tick(4 * 3600_000); // tomorrow 10:00
    assert.ok(await f.manager.announcement());
    await f.manager.skip('tool');
    assert.equal(await f.manager.announcement(), undefined, 'skipped version is not offered');
    assert.equal((await f.manager.list()).updates[0].status, 'skipped');
  } finally { await f.cleanup(); }
});

test('offer answer: next-turn token only, 2 min, then the apply is queued', async () => {
  const f = await fixture();
  try {
    await f.manager.check(signal);
    const offer = await f.manager.announcement();
    await f.manager.shown(offer!.id);
    const tools = new Map<string, { execute: (...a: unknown[]) => Promise<{ content: { text: string }[]; isError?: boolean }> }>();
    const call = async (turn: number, params: unknown) => {
      componentUpdates({ enabled: true, service: f.manager, turn, store: f.store })({ registerTool: (t: { name: string }) => tools.set(t.name, t as never) } as unknown as ExtensionAPI);
      const r = await tools.get('updates')!.execute('t', params, signal); return { ...JSON.parse(r.content[0].text), error_flag: r.isError === true };
    };
    const offered = currentTurn();
    const pending = f.store.pending(offered + 1)!;
    assert.match(pending.title, /^apply Tool 1\.1\.0/);
    assert.equal((await call(offered, { action: 'apply', confirm_token: pending.token })).error, 'confirmation_needs_reply', 'same turn cannot confirm');
    assert.equal((await call(offered + 1, { action: 'rollback', confirm_token: pending.token })).error, 'confirmation_invalid', 'wrong action');
    const started = await call(offered + 1, { action: 'apply', confirm_token: pending.token });
    assert.deepEqual(started.started, ['tool']);
    await f.manager.idle();
    assert.equal(await f.version(), '1.1.0');
    // The model asks itself: a token, which expires after 2 min.
    const turn = newTurn();
    const asked = await call(turn, { action: 'rollback' });
    assert.ok(asked.confirm_token); assert.deepEqual(asked.items, ['Tool -> 1.0.0']);
    f.tick(121_000);
    assert.match((await call(turn + 1, { action: 'rollback', confirm_token: asked.confirm_token })).error, /^confirmation_(expired|invalid)$/);
    assert.equal((await call(turn + 1, { action: 'list' })).updates.length, 0);
  } finally { await f.cleanup(); }
});

test('apply: staging, smoke test, swap with .prev, manifest updated, only the affected process restarted; rollback swaps back', async () => {
  const f = await fixture();
  try {
    await f.manager.check(signal);
    f.manager.startApply(['tool']); await f.manager.idle();
    assert.equal(await f.version(), '1.1.0');
    assert.equal((await readFile(path.join(f.root, 'runtime/tool.prev/VERSION'), 'utf8')).trim(), '1.0.0');
    assert.ok(!await exists(path.join(f.root, 'runtime/tool.staging')));
    assert.equal(await f.manifestVersion(), '1.1.0');
    assert.deepEqual(f.restarts, ['llama']);
    let view = await f.manager.list();
    assert.equal(view.updates.length, 0); assert.equal(view.manifest_changed, true);
    assert.deepEqual(view.rollback, [{ id: 'tool', name: 'Tool', version: '1.0.0' }]);
    assert.match(f.notes.at(-1)!, /Tool 1\.1\.0 kuruldu/);
    assert.match((await f.manager.announcement())!.text, /kuruldu/, 'the result is announced too');
    f.manager.startRollback(['tool']); await f.manager.idle();
    assert.equal(await f.version(), '1.0.0'); assert.equal(await f.manifestVersion(), '1.0.0');
    view = await f.manager.list();
    assert.deepEqual(view.rollback, [{ id: 'tool', name: 'Tool', version: '1.1.0' }], 'a rollback can be undone');
    assert.deepEqual(view.history.map(h => [h.action, h.ok]), [['rollback', true], ['apply', true]]);
  } finally { await f.cleanup(); }
});

test('apply: failing smoke test leaves the live version untouched', async () => {
  const f = await fixture({ smokeOk: false });
  try {
    await f.manager.check(signal);
    f.manager.startApply(['tool']); await f.manager.idle();
    assert.equal(await f.version(), '1.0.0');
    assert.ok(!await exists(path.join(f.root, 'runtime/tool.staging')) && !await exists(path.join(f.root, 'runtime/tool.prev')));
    assert.equal(await f.manifestVersion(), '1.0.0');
    assert.deepEqual(f.restarts, []);
    const view = await f.manager.list();
    assert.equal(view.history[0].ok, false); assert.match(view.history[0].error!, /smoke test/);
    assert.equal(view.updates.length, 1, 'still offered');
    assert.match(f.notes.at(-1)!, /kurulamadı.*1\.0\.0 kullanılmaya devam/);
  } finally { await f.cleanup(); }
});

test('apply: llama perf more than 10% slower is rejected', async () => {
  const f = await fixture();
  try {
    const c = JSON.parse(await readFile(path.join(f.root, 'components.json'), 'utf8'));
    c.components[0].perf = 'llama'; await writeFile(path.join(f.root, 'components.json'), JSON.stringify(c));
    const g = await fixture({ components: c.components, perf: dir => dir.endsWith('.staging') ? 25 : 30 });
    try {
      await g.manager.check(signal);
      g.manager.startApply(['tool']); await g.manager.idle();
      assert.equal(await g.version(), '1.0.0');
      assert.match((await g.manager.list()).history[0].error!, /25\.0 tok\/s < 30\.0 tok\/s/);
    } finally { await g.cleanup(); }
  } finally { await f.cleanup(); }
});

test('apply waits for a running request and is retried when a request interrupts it', async () => {
  let busy = true, wake: () => void = () => {};
  const slots: AbortController[] = [];
  const lane: TaskLane = { async acquire(outer) {
    while (busy) await new Promise<void>(resolve => { wake = resolve; });
    const controller = new AbortController(); slots.push(controller);
    return { signal: AbortSignal.any([outer, controller.signal]), release: () => {} };
  } };
  const f = await fixture({ lane });
  try {
    await f.manager.check(signal);
    f.manager.startApply(['tool']);
    await new Promise(resolve => setTimeout(resolve, 50));
    assert.equal(await f.version(), '1.0.0', 'nothing runs during a request');
    assert.deepEqual((await f.manager.list()).queued, ['tool']);
    busy = false; wake();
    await f.manager.idle();
    assert.equal(await f.version(), '1.1.0');
    // A user request arriving mid-apply cancels it (nothing swapped), and it is queued again.
    const g = await fixture({ lane: { async acquire(outer) {
      const controller = new AbortController(); slots.push(controller);
      if (slots.length === 2) controller.abort(); // first attempt: interrupted
      return { signal: AbortSignal.any([outer, controller.signal]), release: () => {} };
    } } });
    try {
      await g.manager.check(signal);
      g.manager.startApply(['tool']); await g.manager.idle();
      assert.equal(await g.version(), '1.1.0', 'retried after the request');
      assert.equal(slots.length, 3);
    } finally { await g.cleanup(); }
  } finally { await f.cleanup(); }
});
