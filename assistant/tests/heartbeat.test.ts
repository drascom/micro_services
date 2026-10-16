import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { once } from 'node:events';
import { fauxAssistantMessage } from '@earendil-works/pi-ai';
import { defaults, loadConfig, type Config } from '../src/config.ts';
import { Orchestrator } from '../src/orchestrator.ts';
import type { Chat } from '../src/providers.ts';
import { makeServer } from '../src/server.ts';
import { TaskStore } from '../src/task-store.ts';
import { TaskService, type TaskLane, type TaskRun } from '../src/tasks.ts';
import { AnnouncementQueue } from '../src/announce.ts';
import { CHECKLIST_CHARS, HEARTBEAT_PROMPT_CHARS, Heartbeat, activeHour, describeFindings, heartbeatAddRequest, heartbeatPrompt, mailCheck, shortNote, tasksCheck,
  type Finding, type PreCheck } from '../src/heartbeat.ts';
import type { Mailboxes } from '../.pi/extensions/gmail/service.ts';
import type { MailProvider } from '../.pi/extensions/gmail/provider.ts';

// Fake clock (local time), fake model, fake Gmail (read-only methods only; writes throw), in-memory store.
const T0 = new Date(2026, 9, 16, 10, 0).getTime(); // Friday 16 Oct 2026, 10:00 local
const MIN = 60_000;
const freeLane: TaskLane = { acquire: async signal => ({ signal, release: () => {} }) };

async function fixture(options: { run?: TaskRun; config?: Partial<Config>; checks?: PreCheck[]; checklist?: string } = {}) {
  const dir = await mkdtemp(path.join(tmpdir(), 'ayhan-heartbeat-'));
  const file = path.join(dir, 'heartbeat.md');
  await writeFile(file, options.checklist ?? '- Acil mail varsa söyle.\n');
  let now = T0, conversation = false, findings: Finding[] = [];
  const prompts: string[] = [];
  const config: Config = { ...defaults, heartbeat_enabled: true, ...options.config };
  const store = new TaskStore(':memory:');
  const heartbeat = new Heartbeat({ config, store, lane: freeLane, now: () => now, file, example: path.join(dir, 'missing.md'), conversation: () => conversation,
    checks: options.checks ?? [async () => findings],
    run: options.run ?? (async prompt => { prompts.push(prompt); return { text: prompts.length === 1 ? 'Patrondan acil bir mail var.' : `Not ${prompts.length}.` }; }) });
  return { dir, file, config, store, heartbeat, prompts, set: (t: number) => { now = t; }, talk: (on: boolean) => { conversation = on; },
    found: (f: Finding[]) => { findings = f; }, tick: () => heartbeat.tick(new AbortController().signal),
    done: () => rm(dir, { recursive: true, force: true }) };
}
const mail = (id: string, kind: Finding['kind'] = 'mail', text = 'me@gmail.com'): Finding => ({ key: 'mail:me@gmail.com:' + id, kind, text });

test('heartbeat: off by default; config validation', async () => {
  assert.equal(defaults.heartbeat_enabled, false);
  const f = await fixture({ config: { heartbeat_enabled: false } });
  try { f.found([mail('a')]); assert.equal(await f.tick(), 'off'); assert.deepEqual(f.prompts, []); } finally { await f.done(); }
  const dir = await mkdtemp(path.join(tmpdir(), 'ayhan-heartbeat-config-'));
  try {
    for (const [settings, ok] of [[{ heartbeat_interval_minutes: 10 }, false], [{ heartbeat_start_hour: 0, heartbeat_end_hour: 24 }, true],
      [{ heartbeat_start_hour: 8, heartbeat_end_hour: 8 }, false], [{ heartbeat_interval_minutes: 60 }, true]] as const) {
      await writeFile(path.join(dir, 'c.json'), JSON.stringify(settings));
      if (ok) await loadConfig(path.join(dir, 'c.json')); else await assert.rejects(loadConfig(path.join(dir, 'c.json')));
    }
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('heartbeat: active hours, skip in conversation, once per interval (missed ticks collapse)', async () => {
  assert.ok(activeHour(new Date(2026, 9, 16, 8).getTime(), 8, 23) && !activeHour(new Date(2026, 9, 16, 23).getTime(), 8, 23));
  assert.ok(activeHour(new Date(2026, 9, 16, 1).getTime(), 22, 6) && !activeHour(new Date(2026, 9, 16, 12).getTime(), 22, 6));
  const f = await fixture();
  try {
    f.found([mail('a')]);
    f.set(new Date(2026, 9, 16, 7, 30).getTime()); assert.equal(await f.tick(), 'quiet');
    f.set(T0); f.talk(true); assert.equal(await f.tick(), 'conversation');
    f.talk(false); assert.equal(await f.tick(), 'said'); assert.equal(f.prompts.length, 1);
    f.found([mail('a'), mail('b')]);
    f.set(T0 + 29 * MIN); assert.equal(await f.tick(), 'wait');
    f.set(T0 + 5 * 60 * MIN); assert.equal(await f.tick(), 'said'); // asleep for hours: one run
    assert.equal(await f.tick(), 'wait'); assert.equal(f.prompts.length, 2);
  } finally { await f.done(); }
});

test('heartbeat: nothing new = no model call; only new findings reach the prompt', async () => {
  const f = await fixture();
  try {
    assert.equal(await f.tick(), 'unchanged'); assert.equal(f.prompts.length, 0);
    f.found([mail('a'), mail('b', 'urgent', 'Patron')]); f.set(T0 + 30 * MIN);
    assert.equal(await f.tick(), 'said');
    assert.match(f.prompts[0], /2 new unread email\(s\)\n- from urgent senders: Patron/);
    assert.match(f.prompts[0], /Checklist:\n- Acil mail varsa söyle\./);
    f.set(T0 + 60 * MIN); assert.equal(await f.tick(), 'unchanged'); assert.equal(f.prompts.length, 1); // same mail again: seen
    f.found([mail('a'), mail('b', 'urgent', 'Patron'), mail('c')]); f.set(T0 + 90 * MIN);
    await f.tick(); assert.match(f.prompts[1], /- 1 new unread email\(s\)$/m);
    assert.match(f.prompts[1], /Previous note: Patrondan acil bir mail var\./);
  } finally { await f.done(); }
});

test('heartbeat: [SILENT] says nothing; a note is announced once, never repeated; at most two sentences', async () => {
  let reply = '[SILENT]';
  const f = await fixture({ run: async () => ({ text: reply }) });
  try {
    f.found([mail('a')]); assert.equal(await f.tick(), 'silent'); assert.deepEqual(await f.heartbeat.announcements(), []);
    reply = 'Patrondan acil mail var. İkinci cümle. Üçüncü cümle olmamalı.';
    f.found([mail('a'), mail('b')]); f.set(T0 + 30 * MIN); assert.equal(await f.tick(), 'said');
    const queue = new AnnouncementQueue([f.heartbeat]);
    const [note] = await queue.all();
    assert.deepEqual(note, { id: 'heartbeat-1', text: 'Patrondan acil mail var. İkinci cümle.', priority: 2 });
    f.found([mail('a'), mail('b'), mail('c')]); f.set(T0 + 60 * MIN); assert.equal(await f.tick(), 'duplicate'); // same finding text again
    assert.equal((await queue.all()).length, 1);
    assert.ok(await queue.shown('heartbeat-1')); assert.deepEqual(await queue.all(), []);
    f.found([mail('a'), mail('b'), mail('c'), mail('d')]); f.set(T0 + 90 * MIN); assert.equal(await f.tick(), 'duplicate'); // said before: never again
    assert.deepEqual(await queue.all(), []);
    assert.equal(shortNote('Bir. İki! Üç?'), 'Bir. İki!');
  } finally { await f.done(); }
});

test('heartbeat: a user request cancels the run; the same changes are checked again', async () => {
  let controller = new AbortController(), calls = 0;
  const f = await fixture({ run: async () => { if (++calls === 1) { controller.abort(); throw Error('aborted'); } return { text: 'Not.' }; } });
  (f.heartbeat as unknown as { lane: TaskLane }).lane = { acquire: async () => { controller = new AbortController(); return { signal: controller.signal, release: () => {} }; } };
  try {
    f.found([mail('a')]);
    assert.equal(await f.tick(), 'cancelled');
    assert.equal(await f.tick(), 'said'); assert.equal(calls, 2);
  } finally { await f.done(); }
});

test('heartbeat pre-checks: Gmail read-only (ids, urgent sender names), tasks due soon and failed; prompt budget', async () => {
  const calls: string[] = [];
  const provider = { search: async (q: string) => { calls.push('search ' + q); return ['m1', 'm2']; },
    metadata: async (id: string) => { calls.push('metadata ' + id); return { id, labels: [], subject: 'SECRET', snippet: 'SECRET', date: 0, from: id === 'm1' ? 'Boss <boss@work.com>' : 'x@y.com',
      auth: 'mx.google.com; dkim=pass header.i=@work.com; spf=pass; dmarc=pass' }; },
    body: async () => { throw Error('content must not be read'); }, modify: async () => { throw Error('write'); }, trash: async () => { throw Error('write'); } } as unknown as MailProvider;
  const gmail: Mailboxes = { accounts: async () => ['me@gmail.com'], provider: () => provider };
  const signal = new AbortController().signal;
  assert.deepEqual((await mailCheck(gmail, () => [])(0, T0, signal)).map(f => f.kind), ['mail', 'mail']);
  assert.deepEqual(calls, ['search is:unread in:inbox newer_than:2d']);
  const found = await mailCheck(gmail, () => ['boss@work.com'])(0, T0, signal);
  assert.deepEqual(found.map(f => [f.key, f.kind, f.text]), [['mail:me@gmail.com:m1', 'urgent', 'Boss'], ['mail:me@gmail.com:m2', 'mail', 'me@gmail.com']]);
  assert.ok(!describeFindings(found).includes('SECRET'));
  const broken: Mailboxes = { accounts: async () => ['me@gmail.com'], provider: () => ({ search: async () => { throw Error('fetch failed'); } }) as unknown as MailProvider };
  assert.deepEqual(await mailCheck(broken, () => [])(0, T0, signal), []);
  // Tasks: due within the hour, failed since the last tick.
  const store = new TaskStore(':memory:'), service = new TaskService(store, undefined, () => T0);
  const soon = service.create(service.preview({ name: 'Kur', prompt: 'kura bak', schedule: 'in 30m' }));
  service.create(service.preview({ name: 'Uzak', prompt: 'x', schedule: 'in 3h' }));
  const before = await tasksCheck(store)(T0, T0, signal);
  assert.deepEqual(before.map(f => [f.kind, f.text]), [['due', '“Kur” bugün 10:30']]);
  const claim = store.claim(store.get(soon.id)!, T0 + 30 * MIN)!; store.finish(claim, 'failed', 'hata', T0 + 31 * MIN);
  const tasks = await tasksCheck(store)(T0, T0 + 32 * MIN, signal);
  assert.deepEqual(tasks.map(f => [f.kind, f.text]), [['failed', '“Kur”']]);
  // Budget: the prompt stays small whatever the checklist and findings (≤ 4k tokens with the session).
  const prompt = heartbeatPrompt('x'.repeat(CHECKLIST_CHARS * 2), describeFindings(Array.from({ length: 200 }, (_, i) => mail(String(i), 'urgent', 'S' + i))), 'p'.repeat(1000));
  assert.ok(prompt.length <= HEARTBEAT_PROMPT_CHARS, String(prompt.length));
});

test('heartbeat chat: "heartbeat\'e ekle: …" parsing', () => {
  assert.equal(heartbeatAddRequest("heartbeat'e ekle: kargo geldi mi bak"), 'kargo geldi mi bak');
  assert.equal(heartbeatAddRequest('Heartbeat listesine ekle faturaları kontrol et.'), 'faturaları kontrol et');
  assert.equal(heartbeatAddRequest('add to heartbeat: check the weather'), 'check the weather');
  assert.equal(heartbeatAddRequest('heartbeat nedir'), undefined);
});

test('server: heartbeat is a read-only unattended run, asked first; chat add with next-turn confirmation; Admin', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'ayhan-heartbeat-server-'));
  const lifecycle = new AbortController();
  const automatedRuns: boolean[] = [];
  const remote: Chat = { complete: async (_context, _signal, options) => {
    automatedRuns.push(options?.automated === true);
    return { ...fauxAssistantMessage(options?.automated ? 'Acil bir mail geldi.' : 'chat reply'), provider: 'openai' };
  } };
  const configPath = path.join(dir, 'config.json'); await writeFile(configPath, '{}');
  const config: Config = { ...defaults, heartbeat_enabled: true };
  const app = new Orchestrator(config, remote, remote, { ensure: async () => {}, close: async () => {} },
    { transcribe: async () => '', speak: async () => {}, stop: () => {}, close: async () => {} });
  app.loadInstructions = async () => '';
  const file = path.join(dir, 'heartbeat.md'); await writeFile(file, '- Acil mail\n');
  const findings: Finding[] = [mail('a', 'urgent', 'Patron')];
  const server = await makeServer(config, app, lifecycle.signal, null, undefined, undefined, { configPath },
    { store: new TaskStore(':memory:'), notify: async () => {}, firstMs: 3_600_000, everyMs: 3_600_000, now: () => T0, heartbeatFile: file,
      heartbeatChecks: [async () => findings], heartbeatFirstMs: 0, heartbeatEveryMs: 20 });
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  const address = server.address(); assert.ok(address && typeof address === 'object');
  const base = `http://127.0.0.1:${address.port}`;
  try {
    const { token } = await (await fetch(base + '/session')).json() as { token: string };
    const post = async (url: string, body: unknown = {}) => (await fetch(base + url, { method: 'POST', headers: { 'X-Assistant-Token': token }, body: JSON.stringify(body) })).json();
    let state: { pending: number; action?: string } = { pending: 0 };
    for (let i = 0; i < 50 && !state.action; i++) state = await post('/announce/wait', { pending: 0 }) as typeof state;
    assert.deepEqual(state, { pending: 1, action: 'chime', sound: true }); // idle: chime only, no content
    assert.deepEqual(automatedRuns, [true]);
    assert.deepEqual(await post('/announce/accept'), { text: 'Acil bir mail geldi.', pending: 0 });
    // Chat: said back, appended only after "evet".
    assert.equal((await post('/chat', { text: "heartbeat'e ekle: kargo geldi mi" })).text, 'Heartbeat listesine “kargo geldi mi” ekleyeyim mi?');
    assert.equal(await readFile(file, 'utf8'), '- Acil mail\n');
    assert.equal((await post('/chat', { text: 'evet' })).text, 'Heartbeat listesine eklendi.');
    assert.equal(await readFile(file, 'utf8'), '- Acil mail\n- kargo geldi mi\n');
    assert.equal((await post('/chat', { text: "heartbeat'e ekle: x" })).text, 'Heartbeat listesine “x” ekleyeyim mi?');
    assert.equal((await post('/chat', { text: 'hayır' })).text, 'Tamam, eklemedim.');
    // Admin Görevler: status, checklist editor, settings.
    const status = await post('/admin/heartbeat') as { enabled: boolean; interval_minutes: number; content: string };
    assert.deepEqual([status.enabled, status.interval_minutes, status.content], [true, 30, '- Acil mail\n- kargo geldi mi\n']);
    const saved = await post('/admin/heartbeat/save', { content: '- yeni\n', enabled: false, interval_minutes: 60 }) as typeof status;
    assert.deepEqual([saved.enabled, saved.interval_minutes, saved.content], [false, 60, '- yeni\n']);
    assert.deepEqual(JSON.parse(await readFile(configPath, 'utf8')), { heartbeat_enabled: false, heartbeat_interval_minutes: 60 });
    assert.match((await post('/admin/heartbeat/save', { content: 'x'.repeat(CHECKLIST_CHARS + 1) }) as { error: string }).error, /en fazla/);
  } finally { lifecycle.abort(); await new Promise<void>(resolve => server.close(() => resolve())); await rm(dir, { recursive: true, force: true }); }
});
