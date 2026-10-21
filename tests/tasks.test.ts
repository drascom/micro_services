import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
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
import { describe, firstRun, nextCron, nextRun, parseSchedule, when, type Schedule } from '../src/schedule.ts';
import { TaskStore } from '../src/task-store.ts';
import { FAILURE_NUDGE, MIGRATED_KEY, RETRY_MS, transientError, TaskScheduler, TaskService, migrateReminders, registeredTasks, runPrompt, taskRequest, type TaskLane, type TaskRun } from '../src/tasks.ts';
import { AnnouncementQueue, updatesSource, type Announcement } from '../src/announce.ts';
import { assistantTasks } from '../.pi/extensions/tasks/index.ts';
import { ConfirmationStore } from '../.pi/extensions/apple-reminders/confirmations.ts';
import { permissionsExtension } from '../.pi/extensions/permissions.ts';
import { MemoryService } from '../src/memory.ts';
import type { RemindersTransport } from '../.pi/extensions/apple-reminders/client.ts';
import type { BridgeReminder, BridgeRequest } from '../.pi/extensions/apple-reminders/schemas.ts';

// Fake clock (local time), in-memory stores, fake model/tools; the real Reminders app is never contacted.
const T0 = new Date(2026, 9, 16, 10, 0).getTime(); // Friday 16 Oct 2026, 10:00 local
const at = (day: number, h: number, m = 0) => new Date(2026, 9, day, h, m).getTime();
const MIN = 60_000, HOUR = 60 * MIN;

test('schedule parser: Turkish and English table', () => {
  const table: [string, Schedule][] = [
    ['in 30m', { kind: 'once', at: T0 + 30 * MIN }], ['In 2 hours', { kind: 'once', at: T0 + 2 * HOUR }],
    ['30 dakika sonra', { kind: 'once', at: T0 + 30 * MIN }], ['2 saat sonra', { kind: 'once', at: T0 + 2 * HOUR }],
    ['every 2h', { kind: 'interval', every: 2 * HOUR }], ['her 2 saatte bir', { kind: 'interval', every: 2 * HOUR }],
    ['30 dakikada bir', { kind: 'interval', every: 30 * MIN }], ['every hour', { kind: 'interval', every: HOUR }], ['her saat', { kind: 'interval', every: HOUR }],
    ['her gün 09:00', { kind: 'cron', expr: '0 9 * * *' }], ["her gün 9'da", { kind: 'cron', expr: '0 9 * * *' }], ["her sabah 9'da", { kind: 'cron', expr: '0 9 * * *' }],
    ["her akşam 8'de", { kind: 'cron', expr: '0 20 * * *' }], ['daily at 9:00', { kind: 'cron', expr: '0 9 * * *' }], ['every day at 9am', { kind: 'cron', expr: '0 9 * * *' }],
    ["hafta içi 9'da", { kind: 'cron', expr: '0 9 * * 1-5' }], ['weekdays at 9am', { kind: 'cron', expr: '0 9 * * 1-5' }], ['hafta sonu 10:00', { kind: 'cron', expr: '0 10 * * 0,6' }],
    ['her pazartesi', { kind: 'cron', expr: '0 9 * * 1' }], ['every monday 8:30', { kind: 'cron', expr: '30 8 * * 1' }], ['cuma günleri 17:00', { kind: 'cron', expr: '0 17 * * 5' }],
    ["her pazartesi ve perşembe 9'da", { kind: 'cron', expr: '0 9 * * 1,4' }], ['Every Sunday', { kind: 'cron', expr: '0 9 * * 0' }],
    ["ayın 1'i", { kind: 'cron', expr: '0 9 1 * *' }], ["her ayın 15'i 10:00", { kind: 'cron', expr: '0 10 15 * *' }], ['monthly on the 1st at 9am', { kind: 'cron', expr: '0 9 1 * *' }],
    ['0 9 * * 1-5', { kind: 'cron', expr: '0 9 * * 1-5' }], ['*/30 8-18 * * *', { kind: 'cron', expr: '*/30 8-18 * * *' }],
    ['2026-10-20 09:00', { kind: 'once', at: at(20, 9) }], ['2026-10-20', { kind: 'once', at: at(20, 9) }],
    ['yarın 09:00', { kind: 'once', at: at(17, 9) }], ['tomorrow 9am', { kind: 'once', at: at(17, 9) }], ['bugün 15:00', { kind: 'once', at: at(16, 15) }],
    ['saat 9:30', { kind: 'once', at: at(17, 9, 30) }], ['pazartesi 10:00', { kind: 'once', at: at(19, 10) }], ['at 9pm', { kind: 'once', at: at(16, 21) }]];
  for (const [text, expected] of table) assert.deepEqual(parseSchedule(text, T0), expected, text);
  for (const bad of ['every 10m', 'asdf', '2020-01-01', 'bugün 08:00', '', '61 * * * *', 'her gün 25:00'])
    assert.throws(() => parseSchedule(bad, T0), /Örnek \/ examples/, bad);
});

test('schedule: next runs, description and spoken time', () => {
  const daily = parseSchedule('her gün 09:00', T0), weekdays = parseSchedule('hafta içi 09:00', T0);
  assert.equal(firstRun(daily, T0), at(17, 9));
  assert.equal(firstRun(weekdays, T0), at(19, 9)); // Saturday/Sunday skipped
  assert.equal(nextCron('0 9 1 * *', T0), at(31 + 1, 9)); // 1 Nov
  assert.equal(nextRun({ kind: 'interval', every: 2 * HOUR }, T0 + 5 * HOUR, T0), T0 + 6 * HOUR); // missed slots collapse
  assert.equal(nextRun({ kind: 'once', at: T0 }, T0), null);
  assert.deepEqual([describe(daily), describe(weekdays), describe(parseSchedule('her pazartesi', T0)), describe(parseSchedule('every 2h', T0)), describe(parseSchedule("ayın 1'i", T0))],
    ['Her gün 09:00', 'Hafta içi 09:00', 'Her pazartesi 09:00', 'Her 2 saatte bir', 'Her ayın 1. günü 09:00']);
  assert.deepEqual([when(at(16, 18), T0), when(at(17, 9), T0), when(at(19, 9), T0)], ['bugün 18:00', 'yarın 09:00', '19 Eki Pzt 09:00']);
});

test('taskRequest: görev/task phrases, not plain reminders', () => {
  for (const t of ['sana bir görev vermek istiyorum', "her sabah 9'da maillerimi özetle", 'görevlerimi listele', 'add a task']) assert.ok(taskRequest(t), t);
  for (const t of ['yarın 9da bana hatırlat', 'hava nasıl']) assert.ok(!taskRequest(t), t);
});

const freeLane: TaskLane = { acquire: async signal => ({ signal, release: () => {} }) };
function fixture(run: TaskRun, config: Partial<Config> = {}, store = new TaskStore(':memory:')) {
  let now = T0;
  const notes: [string, string][] = [];
  const scheduler = new TaskScheduler({ config: { ...defaults, tasks_enabled: true, ...config }, store, lane: freeLane, run, activityFile: null, now: () => now,
    notify: async (title, body) => { notes.push([title, body]); } });
  const service = new TaskService(store, scheduler, () => now);
  const add = (schedule: string, prompt = 'Gelen kutusunu özetle', name?: string) => service.create(service.preview({ name, prompt, schedule }));
  return { store, scheduler, service, notes, add, set: (t: number) => { now = t; }, tick: () => scheduler.tick(new AbortController().signal) };
}

test('scheduler: due task runs once per slot; next slot from the schedule; off = nothing runs', async () => {
  const prompts: string[] = [];
  const f = fixture(async (prompt, intent) => { prompts.push(intent); assert.equal(prompt, runPrompt({ prompt: intent })); return { text: '3 yeni mail.' }; });
  const task = f.add('her gün 09:00');
  assert.equal(await f.tick(), 0);
  f.set(at(17, 9)); assert.equal(await f.tick(), 1); assert.equal(await f.tick(), 0);
  assert.deepEqual(prompts, ['Gelen kutusunu özetle']);
  assert.equal(f.store.get(task.id)!.next_run_at, at(18, 9));
  assert.deepEqual(f.notes, [['Asistan: Gelen kutusunu özetle', '3 yeni mail.']]);
  const off = fixture(async () => ({ text: 'x' }), { tasks_enabled: false }); off.add('in 30m'); off.set(T0 + HOUR);
  assert.equal(await off.tick(), 0);
});

test('ledger: the same slot never runs twice (two schedulers, one database)', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'ayhan-tasks-'));
  try {
    const file = path.join(dir, 'tasks.sqlite');
    let runs = 0;
    const slow: TaskRun = async () => { runs++; await new Promise(r => setTimeout(r, 20)); return { text: 'ok' }; };
    const a = fixture(slow, {}, new TaskStore(file)), b = fixture(slow, {}, new TaskStore(file));
    await a.tick(); await b.tick(); // startup recovery done
    const task = a.add('in 30m'); a.set(T0 + HOUR); b.set(T0 + HOUR);
    const [x, y] = await Promise.all([a.tick(), b.tick()]);
    assert.equal(x + y, 1); assert.equal(runs, 1);
    assert.equal(a.store.claim({ ...task, next_run_at: task.next_run_at }, T0 + HOUR), undefined, 'claimed slot');
    assert.equal(a.store.get(task.id)!.status, 'done');
    a.store.close(); b.store.close();
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('catch-up: after sleep/off a daily task runs once, then on the next regular slot', async () => {
  let runs = 0;
  const f = fixture(async () => { runs++; return { text: 'özet' }; });
  const task = f.add('her gün 09:00');
  f.set(at(20, 14)); // asleep for three days
  assert.equal(await f.tick(), 1); assert.equal(await f.tick(), 0); assert.equal(runs, 1);
  assert.equal(f.store.get(task.id)!.next_run_at, at(21, 9));
  const g = fixture(async () => ({ text: 'ok' }));
  const every = g.add('every 2h', 'Haberlere bak', 'Haberler');
  g.set(T0 + 7 * HOUR + 10 * MIN); // created at T0: slots T0+2h, +4h, +6h missed → one run
  assert.equal(await g.tick(), 1); assert.equal(g.store.get(every.id)!.next_run_at, T0 + 8 * HOUR);
});

test('[SILENT]: nothing delivered; errors always delivered; third failure in a row suggests pausing', async () => {
  let reply = '[SILENT]', fail = false;
  const f = fixture(async () => { if (fail) throw Error('Remote unavailable'); return { text: reply }; });
  const task = f.add('every 2h', 'Önemli mail var mı', 'Mail kontrol');
  f.set(T0 + 2 * HOUR); assert.equal(await f.tick(), 1);
  assert.equal(f.store.get(task.id)!.last_status, 'silent'); assert.deepEqual(f.notes, []); assert.deepEqual(await f.scheduler.announcements(), []);
  fail = true;
  for (let i = 1; i <= FAILURE_NUDGE; i++) { f.set(T0 + (2 + 2 * i) * HOUR); assert.equal(await f.tick(), 1); }
  assert.equal(f.store.get(task.id)!.failure_streak, 3); assert.equal(f.notes.length, 3);
  const said = await f.scheduler.announcements();
  assert.equal(said.length, 3); assert.ok(said.every(a => a.priority === 0 && a.text.startsWith('“Mail kontrol” görevi başarısız: Remote unavailable')));
  assert.ok(!said[0].text.includes('duraklatayım') && said[2].text.includes('3 kez üst üste başarısız oldu; duraklatayım mı?'));
  fail = false; reply = 'Yeni önemli mail yok ama bir fatura geldi.';
  f.set(T0 + 10 * HOUR); await f.tick(); assert.equal(f.store.get(task.id)!.failure_streak, 0);
});

test('retries: transient failures run again after 5/15/30 min (not delivered), then fail; other errors fail at once', async () => {
  let fail: string | undefined = 'fetch failed';
  const f = fixture(async () => { if (fail) throw Error(fail); return { text: 'Kur 34,10.' }; });
  const task = f.add('her gün 09:00', 'Kura bak', 'Kur');
  let t = at(17, 9); f.set(t); assert.equal(await f.tick(), 1);
  for (const [i, wait] of RETRY_MS.entries()) {
    assert.equal(f.store.get(task.id)!.retry_at, t + wait, `retry ${i + 1}`);
    assert.deepEqual(f.notes, []); assert.deepEqual(await f.scheduler.announcements(), []);
    f.set(t + wait - 1); assert.equal(await f.tick(), 0);
    t += wait; f.set(t); assert.equal(await f.tick(), 1);
  }
  // Fourth failure (after three retries): failed, delivered, one ledger row (attempt 4), schedule unchanged.
  assert.deepEqual(f.store.runs(task.id).map(r => [r.status, r.attempt]), [['failed', 4]]);
  assert.equal(f.store.get(task.id)!.failure_streak, 1); assert.equal(f.store.get(task.id)!.retry_at, null);
  assert.equal(f.store.get(task.id)!.next_run_at, at(18, 9)); assert.equal(f.notes.length, 1);
  // Network back on the first retry: the result is delivered normally.
  f.set(at(18, 9)); assert.equal(await f.tick(), 1); fail = undefined;
  f.set(at(18, 9, 5)); assert.equal(await f.tick(), 1);
  assert.deepEqual(f.store.runs(task.id, 1).map(r => [r.status, r.attempt]), [['ok', 2]]); assert.equal(f.store.get(task.id)!.failure_streak, 0);
  // Not transient: failed at once.
  fail = 'No usable reply'; f.set(at(19, 9)); await f.tick(); assert.equal(f.store.runs(task.id, 1)[0].status, 'failed');
  for (const m of ['fetch failed', 'connect ETIMEDOUT 1.2.3.4:443', 'getaddrinfo ENOTFOUND api.x', 'The operation was aborted due to timeout', '503 Service Unavailable'])
    assert.ok(transientError(m), m);
  for (const m of ['Remote unavailable', 'No usable reply', 'Invalid API key']) assert.ok(!transientError(m), m);
});

test('retries: a user request during a retry keeps it waiting; a paused task does not retry', async () => {
  let controller = new AbortController(), calls = 0;
  const store = new TaskStore(':memory:');
  let now = T0;
  const lane: TaskLane = { acquire: async () => { controller = new AbortController(); return { signal: controller.signal, release: () => {} }; } };
  const scheduler = new TaskScheduler({ config: { ...defaults, tasks_enabled: true }, store, lane, activityFile: null, now: () => now, notify: async () => {},
    run: async () => { calls++; if (calls === 1) throw Error('fetch failed'); if (calls === 2) { controller.abort(); throw Error('aborted'); } return { text: 'ok' }; } });
  const service = new TaskService(store, scheduler, () => now);
  const task = service.create(service.preview({ prompt: 'x', schedule: 'in 30m' }));
  now = T0 + 30 * MIN; assert.equal(await scheduler.tick(new AbortController().signal), 1);
  now += RETRY_MS[0];
  // Retry cancelled (call 2) → waits again and runs right after (call 3).
  assert.equal(await scheduler.tick(new AbortController().signal), 1);
  assert.deepEqual(store.runs(task.id).map(r => r.status), ['ok']); assert.equal(calls, 3);
  const other = service.create(service.preview({ prompt: 'y', schedule: 'every 2h' }));
  calls = 0; now += 2 * HOUR; await scheduler.tick(new AbortController().signal);
  assert.ok(store.get(other.id)!.retry_at); service.pause(store.get(other.id)!);
  now += HOUR; assert.equal(await scheduler.tick(new AbortController().signal), 0);
});

test('previous result: a run sees the last result (≤300 chars) to tell only what changed', async () => {
  const prompts: string[] = [];
  const f = fixture(async prompt => { prompts.push(prompt); return { text: prompts.length === 2 ? '[SILENT]' : 'Kur ' + 'x'.repeat(400) }; });
  f.add('every 2h', 'Kura bak', 'Kur');
  f.set(T0 + 2 * HOUR); await f.tick(); f.set(T0 + 4 * HOUR); await f.tick(); f.set(T0 + 6 * HOUR); await f.tick();
  assert.ok(!prompts[0].includes('Previous result'));
  const previous = /Previous result \(tell only what changed since; unchanged = \[SILENT\]\): (.*)$/.exec(prompts[1])?.[1] ?? '';
  assert.ok(previous.startsWith('Kur xxx') && previous.length <= 300, String(previous.length));
  assert.match(prompts[2], /Previous result .*: Kur xxx/); // a silent run keeps the last real result
  assert.equal(runPrompt({ prompt: 'a' }, 'b'), runPrompt({ prompt: 'a' }) + '\nPrevious result (tell only what changed since; unchanged = [SILENT]): b');
});

test('announcements: delivered once, same result deduped for 24 h, priority over the update offer, old ones dropped', async () => {
  const f = fixture(async () => ({ text: 'Kur 34,10.' }));
  f.add('every 2h', 'Dolar kuruna bak', 'Kur');
  f.set(T0 + 2 * HOUR); await f.tick();
  let offer: Announcement | undefined = { id: 'offer-x', text: '1 güncelleme var. Güncelleyeyim mi?' };
  const queue = new AnnouncementQueue([f.scheduler, updatesSource({ announcement: async () => offer, shown: async id => { if (id === offer?.id) { offer = undefined; return true; } return false; } })]);
  const first = await queue.next();
  assert.deepEqual(first, { id: first!.id, text: 'Kur: Kur 34,10.' });
  assert.ok(await queue.shown(first!.id)); assert.ok(!await queue.shown(first!.id));
  assert.equal((await queue.next())!.id, 'offer-x');
  f.set(T0 + 4 * HOUR); await f.tick(); // same text again: not repeated
  assert.deepEqual(await f.scheduler.announcements(), []);
  f.set(T0 + 6 * HOUR); await f.tick(); f.set(T0 + 6 * HOUR + 13 * HOUR); // different day but stale (> 12 h) and same text
  assert.deepEqual(await f.scheduler.announcements(), []);
  // Two identical texts waiting at once are said once.
  const twin = { announcements: async () => [{ id: 'task-a', text: 'Aynı', priority: 2 }, { id: 'task-b', text: 'Aynı', priority: 2 }],
    shown: async (id: string) => { acked.push(id); return id.startsWith('task-'); } };
  const acked: string[] = [];
  const q2 = new AnnouncementQueue([twin]);
  assert.equal((await q2.next())!.id, 'task-a'); await q2.shown('task-a'); assert.deepEqual(acked, ['task-a', 'task-b']);
});

test('user request cancels a run: the slot is re-armed and runs again; crash recovery re-arms a running slot', async () => {
  let controller = new AbortController(), attempt = 0;
  const store = new TaskStore(':memory:');
  let now = T0;
  const lane: TaskLane = { acquire: async () => { controller = new AbortController(); return { signal: controller.signal, release: () => {} }; } };
  const scheduler = new TaskScheduler({ config: { ...defaults, tasks_enabled: true }, store, lane, activityFile: null, now: () => now, notify: async () => {},
    run: async () => { if (++attempt === 1) { controller.abort(); throw Error('aborted'); } return { text: 'ok' }; } });
  const service = new TaskService(store, scheduler, () => now);
  const task = service.create(service.preview({ prompt: 'x', schedule: 'in 30m' }));
  now = T0 + HOUR;
  // Cancelled (attempt 1), re-armed, and run again as soon as the lane is free (same ledger row, attempt 2).
  assert.equal(await scheduler.tick(new AbortController().signal), 1);
  assert.deepEqual(store.runs(task.id).map(r => [r.status, r.attempt]), [['ok', 2]]); assert.equal(attempt, 2);
  assert.equal(await scheduler.tick(new AbortController().signal), 0);
  // Crash: a run left `running` is cancelled and re-armed on startup.
  now = T0; const daily = service.create(service.preview({ prompt: 'y', schedule: 'her gün 11:00' }));
  now = at(16, 11); const claim = store.claim(store.get(daily.id)!, now)!; assert.ok(claim);
  assert.equal(store.recover(now), 1); assert.equal(store.get(daily.id)!.next_run_at, at(16, 11));
});

test('isolation: a task run has no user history, no memory block and records nothing to memory', async () => {
  let classified = '', seen: Context | undefined, automated: boolean | undefined;
  const reply = (text: string): AssistantMessage => ({ ...fauxAssistantMessage(text), provider: 'openai' });
  const remote: Chat = { complete: async (context, _s, options) => { seen = context; automated = options?.automated; return reply('done'); } };
  const classifier: Classifier = { classify: async text => { classified = text; return { choice: 'conversation', confidence: 1, provider: 'jev' }; } };
  const config = { ...defaults, tasks_enabled: true };
  const app = new Orchestrator(config, remote, remote, { ensure: async () => {}, close: async () => {} },
    { transcribe: async () => '', speak: async () => {}, stop: () => {}, close: async () => {} }, classifier);
  app.loadInstructions = async () => '';
  const memory = new MemoryService(config, undefined, ':memory:'); app.memory = memory;
  await memory.store.save({ kind: 'assistant', text: 'Cevapları kısa tut', source: 'explicit', pinned: true }, new AbortController().signal);
  let recorded = 0; memory.extractor.record = () => { recorded++; };
  await app.ask('özel konu hakkında konuştuk'); assert.equal(app.history.length, 2); assert.equal(recorded, 1);
  assert.match(seen!.systemPrompt ?? '', /Cevapları kısa tut/);
  await app.ask(runPrompt({ prompt: 'maillerimi özetle' }), false, new AbortController().signal, { automated: true, intentText: 'maillerimi özetle' });
  assert.equal(classified, 'maillerimi özetle'); assert.equal(automated, true);
  assert.equal(seen!.messages.length, 1); assert.ok(!JSON.stringify(seen).includes('özel konu')); assert.ok(!(seen!.systemPrompt ?? '').includes('Cevapları kısa tut'));
  assert.equal(app.history.length, 2); assert.equal(recorded, 1);
  memory.close();
});

test('restricted tools: unattended runs cannot write mail, vault, files, memory, updates, tasks or reminders', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'ayhan-task-perm-'));
  try {
    await mkdir(path.join(dir, '.pi'), { recursive: true });
    await writeFile(path.join(dir, '.pi/permissions.json'), JSON.stringify({ mail_read: 'allow', mail_organize: 'allow', web_search: 'allow' }));
    const check = async (automated: boolean, toolName: string, input: Record<string, unknown> = {}) => {
      let handler: ((event: unknown) => Promise<{ block?: boolean } | undefined>) | undefined;
      permissionsExtension({ root: dir, offline: false, automated, local: true })({ on: (name: string, h: never) => { if (name === 'tool_call') handler = h; } } as never);
      return Boolean((await handler!({ toolName, input }))?.block);
    };
    for (const [tool, input] of [['mail_organize', { action: 'apply' }], ['mail_organize', { action: 'purge_junk' }], ['vault_save', {}], ['vault_copy', {}],
      ['file_organize', { action: 'apply' }], ['memory_save', {}], ['updates', { action: 'apply' }], ['tasks', { action: 'create' }], ['reminders', { action: 'add' }],
      ['reminders', { action: 'delete' }], ['write', { path: '.pi/skills/x/SKILL.md' }], ['bash', {}]] as const)
      assert.ok(await check(true, tool, input), `${tool} ${JSON.stringify(input)} must be blocked`);
    for (const [tool, input] of [['reminders', { action: 'list' }], ['mail_list_messages', {}], ['mail_organize', { action: 'propose' }], ['web_search', {}], ['file_search', {}]] as const)
      assert.ok(!await check(true, tool, input), `${tool} allowed`);
    assert.ok(!await check(false, 'tasks', { action: 'create' }), 'user request may use tasks');
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('harness: a task run never gets the tasks tool; a user request on the tasks route gets only it', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'ayhan-task-harness-'));
  await mkdir(path.join(dir, '.pi/skills'), { recursive: true }); await mkdir(path.join(dir, '.pi/extensions'), { recursive: true });
  const fake = fauxProvider({ provider: 'task-test', models: [{ id: 'test' }], tokensPerSecond: 1000000 });
  fake.setResponses([fauxAssistantMessage('ok')]);
  const runtime = await ModelRuntime.create({ modelsPath: null, refreshOnCreate: false }); runtime.registerNativeProvider(fake.provider);
  const tasks = new TaskService(new TaskStore(':memory:'));
  const config = { ...defaults, tasks_enabled: true, reminders_enabled: true, pi_agent_dir: path.join(dir, 'agent') };
  const open = (automated: boolean, skill?: 'tasks') => createHarnessSession({ root: dir, config, context: { systemPrompt: 'x', messages: [{ role: 'user', content: 'x', timestamp: 1 }] },
    model: fake.getModel(), runtime, offline: true, extensionPaths: [], automated, turn: 1, tasks, updates: null, ...(skill ? { skill } : {}) });
  try {
    for (const [automated, skill, expected] of [[true, 'tasks', []], [true, undefined, ['read', 'reminders']], [false, 'tasks', ['tasks']]] as const) {
      const session = await open(automated, skill);
      try { assert.deepEqual(session.getActiveToolNames().sort(), [...expected], `${automated} ${skill}`); } finally { session.dispose(); }
    }
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('tasks tool: create says the schedule and saves only after the next-turn confirmation', async () => {
  let now = T0;
  const service = new TaskService(new TaskStore(':memory:'), undefined, () => now);
  const store = new ConfirmationStore(() => now);
  const tool = (turn: number) => {
    let registered: { execute: (id: string, params: Record<string, unknown>) => Promise<{ content: { text: string }[]; isError?: boolean }> } | undefined;
    assistantTasks({ enabled: true, service, turn, store })({ registerTool: (t: never) => { registered = t; } } as never);
    return async (params: Record<string, unknown>) => { const r = await registered!.execute('1', params); return { ...JSON.parse(r.content[0].text), isError: r.isError === true }; };
  };
  const created = await tool(1)({ action: 'create', name: 'Mail özeti', prompt: 'Gelen kutusunu özetle', schedule: 'her gün 09:00' });
  assert.equal(created.schedule, 'Her gün 09:00, sonraki yarın 09:00'); assert.match(created.confirm_token, /^c_/);
  assert.equal(service.list().length, 0, 'not saved before the answer');
  assert.equal((await tool(1)({ action: 'create', confirm_token: created.confirm_token })).error, 'confirmation_needs_reply');
  const saved = await tool(2)({ action: 'create', confirm_token: created.confirm_token });
  assert.deepEqual([saved.saved, saved.schedule, saved.next], ['Mail özeti', 'Her gün 09:00', 'yarın 09:00']);
  assert.equal((await tool(3)({ action: 'create', prompt: 'x', schedule: 'bazen' })).error, 'invalid_schedule');
  assert.deepEqual((await tool(3)({ action: 'list' })).tasks.map((t: { name: string; next: string }) => [t.name, t.next]), [['Mail özeti', 'yarın 09:00']]);
  assert.equal((await tool(3)({ action: 'pause', name: 'mail' })).paused, 'Mail özeti');
  assert.equal(service.list()[0].next, 'duraklatıldı');
  now = at(18, 12);
  assert.equal((await tool(3)({ action: 'resume', name: 'Mail özeti' })).next, 'yarın 09:00'); // re-armed from now, no stale fire
  assert.equal((await tool(3)({ action: 'update', name: 'Mail özeti', schedule: 'hafta içi 08:30' })).schedule, 'Hafta içi 08:30');
  assert.equal((await tool(3)({ action: 'remove', name: 'yok' })).error, 'not_found');
  assert.equal((await tool(3)({ action: 'remove', name: 'Mail özeti' })).removed, 'Mail özeti');
  assert.equal(service.list().length, 0);
});

/** Fake Reminders bridge: list only is expected; any other op is recorded and fails the test. */
function fakeReminders(items: BridgeReminder[], error?: string) {
  const requests: BridgeRequest[] = [];
  const transport: RemindersTransport = { async request(request) {
    requests.push(request);
    if (error) return JSON.stringify({ ok: false, error });
    if (request.op !== 'list') throw Error('write attempted');
    const found = items.filter(i => !i.completed && !request.skip_prefixes?.some(p => i.title.trimStart().startsWith(p)));
    return JSON.stringify({ ok: true, matched: found.length, reminders: found });
  } };
  return { transport, requests };
}
const reminder = (n: number, extra: Partial<BridgeReminder> = {}): BridgeReminder => ({ id: `x-apple-reminder://T-${n}`, title: `Task ${n}`,
  notes: '', due: null, all_day: false, priority: 0, completed: false, list: 'Asistan', ...extra });

test('migration: open Reminders tasks are copied once (read-only); dated future = active, others paused', async () => {
  const store = new TaskStore(':memory:'), config = { ...defaults, reminders_enabled: true, tasks_enabled: true };
  const items = [reminder(1, { due: new Date(at(18, 9)).toISOString(), notes: 'Sadece iş mailleri.\n\n— Asistan 2026-10-10 12:00 —\nold result' }),
    reminder(2), reminder(3, { title: '✅ Done' }), reminder(4, { title: '📧 Cevapla: Boss' }), reminder(5, { title: '⚠️ Failed one', due: new Date(at(18, 9)).toISOString() }),
    reminder(6, { completed: true })];
  const fake = fakeReminders(items);
  assert.equal(await migrateReminders({ store, transport: fake.transport, config, signal: new AbortController().signal, now: T0 }), 3);
  assert.deepEqual(store.list().sort((x, y) => x.id - y.id).map(t => [t.name, t.status, t.next_run_at, t.source]),
    [['Task 1', 'active', at(18, 9), 'reminders'], ['Task 2', 'paused', T0, 'reminders'], ['Failed one', 'paused', at(18, 9), 'reminders']]);
  assert.equal(store.get(1)!.prompt, 'Task 1\nSadece iş mailleri.');
  assert.ok(fake.requests.every(r => r.op === 'list')); assert.deepEqual(items.map(i => i.title).slice(0, 2), ['Task 1', 'Task 2']);
  assert.equal(await migrateReminders({ store, transport: fake.transport, config, signal: new AbortController().signal, now: T0 }), undefined);
  assert.equal(fake.requests.length, 1); assert.equal(JSON.parse(store.meta(MIGRATED_KEY)!).count, 3);
  // Reminders unavailable: nothing marked, retried next start. Reminders off: not read at all.
  const other = new TaskStore(':memory:'), down = fakeReminders([], 'reminders_unavailable');
  assert.equal(await migrateReminders({ store: other, transport: down.transport, config, signal: new AbortController().signal, now: T0 }), undefined);
  assert.equal(other.meta(MIGRATED_KEY), undefined);
  const off = fakeReminders(items);
  await migrateReminders({ store: other, transport: off.transport, config: { ...config, reminders_enabled: false }, signal: new AbortController().signal, now: T0 });
  assert.equal(off.requests.length, 0);
});

test('server: lane, cancel by a user request, announcement asked first, Admin Görevler', async () => {
  const lifecycle = new AbortController();
  let releaseTask!: () => void, taskStarted!: () => void, taskAborted = false, attempts = 0;
  const taskGate = new Promise<void>(r => { releaseTask = r; }), started = new Promise<void>(r => { taskStarted = r; });
  const remote: Chat = { complete: async (_context, signal, options) => {
    if (options?.automated) {
      if (++attempts > 1) return { ...fauxAssistantMessage('Görev sonucu.'), provider: 'openai' };
      taskStarted();
      await Promise.race([taskGate, new Promise((_, reject) => signal.addEventListener('abort', () => { taskAborted = true; reject(Error('aborted')); }, { once: true }))]);
      return { ...fauxAssistantMessage('late'), provider: 'openai' };
    }
    return { ...fauxAssistantMessage('chat reply'), provider: 'openai' };
  } };
  const config: Config = { ...defaults, tasks_enabled: true };
  const app = new Orchestrator(config, remote, remote, { ensure: async () => {}, close: async () => {} },
    { transcribe: async () => '', speak: async () => {}, stop: () => {}, close: async () => {} });
  app.loadInstructions = async () => '';
  const store = new TaskStore(':memory:'), notes: string[] = [];
  const seed = new TaskService(store, undefined, () => T0);
  const task = seed.create(seed.preview({ name: 'Özet', prompt: 'özetle', schedule: 'in 30m' }));
  const server = await makeServer(config, app, lifecycle.signal, null, undefined, undefined, {},
    { store, transport: fakeReminders([]).transport, notify: async title => { notes.push(title); }, firstMs: 0, everyMs: 3_600_000, now: () => T0 + HOUR });
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  const address = server.address(); assert.ok(address && typeof address === 'object');
  const base = `http://127.0.0.1:${address.port}`;
  try {
    const { token } = await (await fetch(base + '/session')).json() as { token: string };
    const post = async (url: string, body: unknown = {}) => (await fetch(base + url, { method: 'POST', headers: { 'X-Assistant-Token': token }, body: JSON.stringify(body) })).json();
    await started;
    assert.deepEqual((await post('/chat', { text: 'hi' })).text, 'chat reply'); // the user comes first
    assert.equal(taskAborted, true);
    // The cancelled slot is re-armed and runs again once the user's request is done (same ledger row, attempt 2).
    for (let i = 0; i < 100 && store.runs(task.id)[0]?.status !== 'ok'; i++) await new Promise(r => setTimeout(r, 20));
    assert.deepEqual(store.runs(task.id).map(r => [r.status, r.attempt]), [['ok', 2]]);
    assert.deepEqual(notes, ['Asistan: Özet']); assert.ok(registeredTasks());
    // The user just talked: a spoken question first, never the content; "evet" says it.
    assert.deepEqual(await post('/announce/wait'), { pending: 1, action: 'ask', text: 'Sana söylemem gereken bir şey var, anlatayım mı?' });
    assert.equal((await post('/chat', { text: 'Evet.' })).text, 'Özet: Görev sonucu.');
    assert.deepEqual(await post('/announce/wait', { pending: 1 }), { pending: 0 });
    const admin = await post('/admin/tasks') as { enabled: boolean; tasks: { id: number; name: string; status: string; last: string }[] };
    assert.deepEqual(admin.tasks.map(t => [t.name, t.status, t.last]), [['Özet', 'done', 'Görev sonucu.']]);
    const removed = await post('/admin/tasks/action', { id: task.id, action: 'delete' }) as { tasks: unknown[] };
    assert.deepEqual(removed.tasks, []);
  } finally { releaseTask(); lifecycle.abort(); await new Promise<void>(resolve => server.close(() => resolve())); }
});
