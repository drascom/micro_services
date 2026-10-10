import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { once } from 'node:events';
import { ModelRuntime } from '@earendil-works/pi-coding-agent';
import { fauxProvider, fauxAssistantMessage, fauxToolCall, type AssistantMessage, type Context } from '@earendil-works/pi-ai';
import { defaults, type Config } from '../src/config.ts';
import { Orchestrator } from '../src/orchestrator.ts';
import type { Chat, Classifier } from '../src/providers.ts';
import { makeServer } from '../src/server.ts';
import { createHarnessSession } from '../src/harness.ts';
import { RESULT_CHARS, RETRY_MS, TaskScheduler, appendResult, baseTitle, status, taskPrompt, userNotes, withStatus, type TaskLane, type TaskRun } from '../src/tasks.ts';
import type { RemindersTransport } from '../.pi/extensions/apple-reminders/client.ts';
import type { BridgeReminder, BridgeRequest } from '../.pi/extensions/apple-reminders/schemas.ts';

// Synthetic reminders and a fake clock; the real Reminders app is never contacted or written.
const T0 = new Date(2026, 9, 10, 12, 0).getTime();
const item = (n: number, extra: Partial<BridgeReminder> = {}): BridgeReminder => ({ id: `x-apple-reminder://TASK-${n}`, title: `Task ${n}`,
  notes: '', due: null, all_day: false, priority: 0, completed: false, list: 'Asistan', ...extra });

/** Fake bridge: filters like the real one (open, due before `due_before` or undated) and applies update/complete in memory. */
function bridge(items: BridgeReminder[]) {
  const requests: BridgeRequest[] = [];
  const transport: RemindersTransport = {
    async request(request) {
      requests.push(request);
      if (request.op === 'list') {
        const before = request.due_before ? Date.parse(request.due_before) : Infinity;
        const found = items.filter(i => !i.completed && (i.due === null ? request.undated : Date.parse(i.due) < before));
        return JSON.stringify({ ok: true, matched: found.length, reminders: found.slice(0, request.max) });
      }
      const target = 'id' in request ? items.find(i => i.id === request.id) : undefined;
      if (!target || ('expected_title' in request && request.expected_title !== target.title)) return JSON.stringify({ ok: false, error: 'stale_reference' });
      if (request.op === 'update' && request.title !== undefined) target.title = request.title;
      if (request.op === 'update' && request.flagged !== undefined) target.flagged = request.flagged;
      if (request.op === 'update' && request.notes !== undefined) target.notes = request.notes;
      if (request.op === 'complete') target.completed = true;
      return JSON.stringify({ ok: true, reminder: target });
    }
  };
  return { transport, requests };
}
const freeLane: TaskLane = { acquire: async signal => ({ signal, release: () => {} }) };
function scheduler(items: BridgeReminder[], run: TaskRun, config: Partial<Config> = {}, extra: { activityFile?: string; stateFile?: string } = {}) {
  let now = T0;
  const b = bridge(items), notes: string[][] = [];
  const s = new TaskScheduler({ config: { ...defaults, reminders_enabled: true, tasks_enabled: true, ...config }, transport: b.transport, lane: freeLane, run,
    notify: async (title, body) => { notes.push([title, body]); }, stateFile: extra.stateFile ?? null, activityFile: extra.activityFile ?? null, now: () => now });
  return { s, b, notes, tick: (ms: number) => { now += ms; }, scan: () => s.scan(new AbortController().signal) };
}
const ok: TaskRun = async () => ({ text: 'Done: 3 mails summarized.' });

test('off: no Reminders read when tasks or Reminders are disabled', async () => {
  for (const config of [{ tasks_enabled: false }, { reminders_enabled: false }]) {
    const f = scheduler([item(1)], ok, config);
    assert.equal(await f.scan(), 0); assert.equal(f.b.requests.length, 0);
  }
});

test('undated and due tasks run, not-yet-due ones wait; result appended, title ✅, task stays open', async () => {
  const runs: string[] = [];
  const items = [item(1, { notes: 'Only work mail.' }), item(2, { due: new Date(T0 - 60_000).toISOString() }), item(3, { due: new Date(T0 + 3_600_000).toISOString() })];
  const f = scheduler(items, async (prompt, intentText) => { runs.push(intentText); assert.match(prompt, /^Automated task/); return { text: 'Result ' + runs.length }; },
    { tasks_list: 'Asistan' });
  assert.equal(await f.scan(), 2);
  assert.deepEqual(runs, ['Task 1\nOnly work mail.', 'Task 2']);
  const list = f.b.requests[0] as Extract<BridgeRequest, { op: 'list' }>;
  assert.deepEqual([list.list, list.status, list.undated, list.due_before], ['Asistan', 'open', true, new Date(T0).toISOString()]);
  assert.ok(!items.some(i => i.completed)); assert.ok(!f.b.requests.some(r => r.op === 'complete'));
  assert.deepEqual(items.map(i => i.title), ['✅ Task 1', '✅ Task 2', 'Task 3']);
  assert.match(items[0].notes, /^Only work mail\.\n\n— Asistan 2026-10-10 12:00 —\nResult 1$/);
  assert.deepEqual(f.notes[0], ['Asistan: Task 1', 'Result 1']);
  // Later, once due: the third runs; ✅ ones never run again.
  f.tick(2 * 3_600_000);
  assert.equal(await f.scan(), 1); assert.deepEqual(runs, ['Task 1\nOnly work mail.', 'Task 2', 'Task 3']);
});

test('prefixes: no double prefix; removing ours makes it a new job; the prompt never sees a prefix', async () => {
  assert.equal(withStatus('⚠️ ⏳ Task', '✅'), '✅ Task'); assert.equal(baseTitle('✅⚠ ⏳  Mail'), 'Mail');
  assert.deepEqual(['Task', '⏳ T', '✅ T', '⚠️ T', '⚠ T'].map(status), ['', '⏳', '✅', '⚠️', '⚠️']);
  const prompts: string[] = [];
  const items = [item(1, { title: '⏳ ✅ Mail' })];
  const f = scheduler(items, async (prompt, intentText) => { prompts.push(prompt + '|' + intentText); return { text: 'r' + prompts.length }; });
  assert.equal(await f.scan(), 1); assert.equal(items[0].title, '✅ Mail');
  assert.ok(!/[⏳✅⚠]/u.test(prompts[0])); assert.match(prompts[0], /Task: Mail\|Mail$/);
  f.tick(RETRY_MS * 3); assert.equal(await f.scan(), 0);
  items[0].title = 'Mail'; // the user removed the prefix
  assert.equal(await f.scan(), 1); assert.equal(items[0].title, '✅ Mail'); assert.equal(prompts.length, 2);
  assert.equal(userNotes(items[0].notes), ''); assert.equal(taskPrompt(items[0]).intentText, 'Mail');
});

test('⏳ recovery: a task left running when the app closed runs again on startup; completed ones are never listed', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'ayhan-tasks-'));
  try {
    const stateFile = path.join(dir, 'state.json');
    const items = [item(1), item(2, { title: '✅ Old', completed: true }), item(3, { title: 'Ticked', completed: true })];
    let controller = new AbortController();
    const lane: TaskLane = { acquire: async () => { controller = new AbortController(); return { signal: controller.signal, release: () => {} }; } };
    const b = bridge(items);
    const s = new TaskScheduler({ config: { ...defaults, reminders_enabled: true, tasks_enabled: true }, transport: b.transport, lane, notify: async () => {},
      stateFile, activityFile: null, now: () => T0, run: async () => { controller.abort(); throw Error('closed'); } });
    assert.equal(await s.scan(new AbortController().signal), 0); assert.equal(items[0].title, '⏳ Task 1');
    const f = scheduler(items, ok, {}, { stateFile }); // restart
    assert.equal(await f.scan(), 1); assert.equal(items[0].title, '✅ Task 1');
    assert.deepEqual(items.slice(1).map(i => i.title), ['✅ Old', 'Ticked']);
    assert.ok(!f.b.requests.some(r => r.op !== 'list' && 'id' in r && r.id !== items[0].id));
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('failure: error line, ⚠️ and flagged; the retry shows ⏳ and unflags; then left alone', async () => {
  let runs = 0, seen: [string, boolean | undefined] | undefined;
  const items = [item(1, { notes: 'keep me' })];
  const f = scheduler(items, async () => { runs++; if (runs === 2) seen = [items[0].title, items[0].flagged]; throw Error('Remote unavailable; local fallback failed: unavailable'); });
  assert.equal(await f.scan(), 1); assert.equal(items[0].completed, false);
  assert.deepEqual([items[0].title, items[0].flagged], ['⚠️ Task 1', true]);
  assert.match(items[0].notes, /^keep me\n\n— Asistan .* —\nHata \/ Error: Remote unavailable/);
  assert.equal(await f.scan(), 0); // too soon
  f.tick(RETRY_MS); assert.equal(await f.scan(), 1); assert.match(items[0].notes, /not retried\)$/);
  assert.deepEqual(seen, ['⏳ Task 1', false]); assert.deepEqual([items[0].title, items[0].flagged], ['⚠️ Task 1', true]);
  f.tick(RETRY_MS * 10); assert.equal(await f.scan(), 0); assert.equal(runs, 2); assert.equal(items[0].completed, false);
  assert.equal(f.notes.length, 2);
});

test('flag: success after a failed try unflags ours; a flag the user set is never touched', async () => {
  let runs = 0;
  const items = [item(1), item(2, { flagged: true })];
  const f = scheduler(items, async (_p, intent) => { runs++; if (runs <= 2) throw Error('down ' + intent); return { text: 'ok' }; });
  assert.equal(await f.scan(), 2); assert.deepEqual(items.map(i => [i.title, i.flagged]), [['⚠️ Task 1', true], ['⚠️ Task 2', true]]);
  f.tick(RETRY_MS); assert.equal(await f.scan(), 2);
  assert.deepEqual(items.map(i => [i.title, i.flagged]), [['✅ Task 1', false], ['✅ Task 2', true]]);
  assert.ok(!f.b.requests.some(r => r.op === 'update' && r.id === items[1].id && r.flagged !== undefined));
});

test('a cancelled task is not counted and runs again on the next scan', async () => {
  let attempt = 0;
  const items = [item(1)];
  let controller = new AbortController();
  const lane: TaskLane = { acquire: async () => { controller = new AbortController(); return { signal: controller.signal, release: () => {} }; } };
  const b = bridge(items);
  const s = new TaskScheduler({ config: { ...defaults, reminders_enabled: true, tasks_enabled: true }, transport: b.transport, lane, notify: async () => {},
    stateFile: null, activityFile: null, now: () => T0,
    run: async () => { if (++attempt === 1) { controller.abort(); throw Error('aborted'); } return { text: 'ok' }; } });
  assert.equal(await s.scan(new AbortController().signal), 0); assert.equal(items[0].notes, ''); assert.equal(items[0].title, '⏳ Task 1');
  assert.ok(!b.requests.some(r => r.op === 'update' && r.notes !== undefined));
  assert.equal(await s.scan(new AbortController().signal), 1); assert.equal(items[0].title, '✅ Task 1');
});

test('state file holds hashes only; activity has a short reference, duration and success, not the title', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'ayhan-tasks-'));
  try {
    const stateFile = path.join(dir, 'state.json'), activityFile = path.join(dir, 'activity.log');
    const items = [item(1, { title: 'Secret project summary' })];
    const f = scheduler(items, ok, {}, { stateFile, activityFile });
    assert.equal(await f.scan(), 1);
    const state = await readFile(stateFile, 'utf8'), log = await readFile(activityFile, 'utf8');
    assert.ok(!state.includes('Secret') && !state.includes('TASK-1') && !log.includes('Secret') && !log.includes('Done:'));
    const events = log.trim().split('\n').map(line => JSON.parse(line));
    const finished = events.find(e => e.event === 'task_finished');
    assert.ok(events.some(e => e.event === 'task_started') && finished.success === true && /^[0-9a-f]{6}$/.test(finished.task) && 'duration_ms' in finished);
    // A new scheduler (app restart): the ✅ task does not run again.
    assert.equal(await scheduler(items, ok, {}, { stateFile }).scan(), 0);
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('notes: user text kept, result shortened, old result blocks dropped first', () => {
  const long = 'x'.repeat(RESULT_CHARS + 500);
  let notes = 'Mine';
  for (let i = 0; i < 6; i++) notes = appendResult(notes, T0 + i * 60_000, long);
  assert.ok(notes.startsWith('Mine\n\n— Asistan ')); assert.ok(notes.length <= 4000);
  assert.ok(notes.includes('…')); assert.ok(notes.endsWith('…') && notes.includes('12:05'));
  assert.equal(userNotes(notes), 'Mine');
});

test('automated ask: no history in or out, Jev classifies only the task text', async () => {
  let classified = '', contextMessages = -1, automated: boolean | undefined;
  const reply = (text: string): AssistantMessage => ({ ...fauxAssistantMessage(text), provider: 'openai' });
  const remote: Chat = { complete: async (context, _s, options) => { contextMessages = context.messages.length; automated = options?.automated; return reply('done'); } };
  const classifier: Classifier = { classify: async text => { classified = text; return { choice: 'email', confidence: 1, provider: 'jev' }; } };
  const app = new Orchestrator({ ...defaults }, remote, remote, { ensure: async () => {}, close: async () => {} },
    { transcribe: async () => '', speak: async () => {}, stop: () => {}, close: async () => {} }, classifier);
  app.loadInstructions = async () => '';
  await app.ask('hello'); assert.equal(app.history.length, 2);
  await app.ask('Automated task ...\nTask: summarize mail', false, new AbortController().signal, { automated: true, intentText: 'summarize mail' });
  assert.equal(classified, 'summarize mail'); assert.equal(contextMessages, 1); assert.equal(automated, true); assert.equal(app.history.length, 2);
});

test('harness: automated task gets no write tools and cannot delete reminders', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'ayhan-task-harness-'));
  await mkdir(path.join(dir, '.pi/skills'), { recursive: true }); await mkdir(path.join(dir, '.pi/extensions'), { recursive: true });
  await writeFile(path.join(dir, '.pi/permissions.json'), JSON.stringify({ skill_edit: 'allow' }));
  const b = bridge([item(1)]);
  const call = (args: Record<string, unknown>) => fauxAssistantMessage(fauxToolCall('reminders', args as Record<string, string>, { id: 'r' + Math.random() }), { stopReason: 'toolUse' });
  const fake = fauxProvider({ provider: 'task-test', models: [{ id: 'test' }], tokensPerSecond: 1000000 });
  fake.setResponses([call({ action: 'list' }),
    (context: Context) => call({ action: 'delete', ref: /r_[A-Za-z0-9_-]{12}/.exec(JSON.stringify(context.messages.filter(m => m.role === 'toolResult')))![0] }),
    fauxAssistantMessage('not deleted')] as never);
  const runtime = await ModelRuntime.create({ modelsPath: null, refreshOnCreate: false }); runtime.registerNativeProvider(fake.provider);
  const session = await createHarnessSession({ root: dir, config: { ...defaults, reminders_enabled: true, pi_agent_dir: path.join(dir, 'agent') },
    context: { systemPrompt: 'Task test', messages: [{ role: 'user', content: 'x', timestamp: 1 }] }, model: fake.getModel(), runtime, offline: true,
    extensionPaths: [], skill: 'skill-manager', automated: true, turn: 0, remindersTransport: b.transport });
  try {
    assert.deepEqual(session.getActiveToolNames().sort(), ['read']);
    await session.dispose();
  } catch (error) { session.dispose(); throw error; }
  const unrouted = await createHarnessSession({ root: dir, config: { ...defaults, reminders_enabled: true, pi_agent_dir: path.join(dir, 'agent') },
    context: { systemPrompt: 'Task test', messages: [{ role: 'user', content: 'x', timestamp: 1 }] }, model: fake.getModel(), runtime, offline: true,
    extensionPaths: [], automated: true, turn: 0, remindersTransport: b.transport });
  try {
    assert.ok(unrouted.getActiveToolNames().includes('reminders') && !unrouted.getActiveToolNames().includes('write'));
    await unrouted.prompt('x');
    const results = unrouted.messages.filter(m => m.role === 'toolResult');
    assert.deepEqual(results.map(r => r.isError), [false, true]);
    assert.match(JSON.stringify(results[1].content), /not available in automated tasks/);
    assert.ok(!b.requests.some(r => r.op === 'delete'));
  } finally { await unrouted.abort(); unrouted.dispose(); await rm(dir, { recursive: true, force: true }); }
});

test('server lane: a task never interrupts a user request; a user request during a task waits instead of 409', async () => {
  const lifecycle = new AbortController();
  let releaseTask!: () => void, taskStarted!: () => void, taskAborted = false;
  const taskGate = new Promise<void>(r => { releaseTask = r; }), started = new Promise<void>(r => { taskStarted = r; });
  const order: string[] = [];
  const remote: Chat = { complete: async (_context, signal, options) => {
    if (options?.automated) {
      taskStarted(); signal.addEventListener('abort', () => { taskAborted = true; }, { once: true });
      await taskGate; order.push('task'); return { ...fauxAssistantMessage('task result'), provider: 'openai' };
    }
    order.push('chat'); return { ...fauxAssistantMessage('chat reply'), provider: 'openai' };
  } };
  const config: Config = { ...defaults, reminders_enabled: true, tasks_enabled: true };
  const app = new Orchestrator(config, remote, remote, { ensure: async () => {}, close: async () => {} },
    { transcribe: async () => '', speak: async () => {}, stop: () => {}, close: async () => {} });
  app.loadInstructions = async () => '';
  const items = [item(1)], b = bridge(items), notes: string[] = [];
  const server = await makeServer(config, app, lifecycle.signal, null, undefined, undefined, {},
    { transport: b.transport, notify: async title => { notes.push(title); }, stateFile: null, firstMs: 0, everyMs: 3_600_000 });
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  const address = server.address(); assert.ok(address && typeof address === 'object');
  const base = `http://127.0.0.1:${address.port}`;
  try {
    const { token } = await (await fetch(base + '/session')).json() as { token: string };
    const headers = { 'X-Assistant-Token': token };
    await started;
    const chat = fetch(base + '/chat', { method: 'POST', headers, body: '{"text":"hi"}' });
    await new Promise(r => setTimeout(r, 100));
    assert.deepEqual(order, []); // the user request waits for the running task
    releaseTask();
    const response = await chat;
    assert.equal(response.status, 200); assert.equal(taskAborted, false);
    assert.deepEqual(order, ['task', 'chat']); assert.equal(app.history.length, 2);
    for (let i = 0; i < 50 && !notes.length; i++) await new Promise(r => setTimeout(r, 20));
    assert.equal(items[0].title, '✅ Task 1'); assert.deepEqual(notes, ['Asistan: Task 1']);
  } finally { releaseTask(); lifecycle.abort(); await new Promise<void>(resolve => server.close(() => resolve())); }
});
