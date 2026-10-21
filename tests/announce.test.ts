import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { fauxAssistantMessage } from '@earendil-works/pi-ai';
import { defaults, type Config } from '../src/config.ts';
import { Orchestrator, textOf } from '../src/orchestrator.ts';
import type { Chat } from '../src/providers.ts';
import { makeServer } from '../src/server.ts';
import { TaskStore } from '../src/task-store.ts';
import { TaskService } from '../src/tasks.ts';
import { AnnouncementQueue, ProactiveGate, classifyAnswer, type Announcement } from '../src/announce.ts';

// Fake clock and sources; no sound or speech is played (the gate only says what the ring should do).
const MIN = 60_000;
function fixture(items: Announcement[], chime = true) {
  let now = 1_000_000;
  const acked: string[] = [];
  const source = { announcements: async () => items.filter(a => !acked.includes(a.id)), shown: async (id: string) => { acked.push(id); return true; } };
  const gate = new ProactiveGate(new AnnouncementQueue([source]), { now: () => now, chime: () => chime, snoozeMs: () => 30 * MIN });
  return { gate, acked, items, advance: (ms: number) => { now += ms; } };
}

test('classifier: short Turkish/English yes and no, anything else is a request', () => {
  for (const yes of ['evet', 'Evet.', 'söyle', 'dinliyorum', 'anlat', 'tamam', 'Tamam, anlat bakalım!', 'evet söyle', 'olur', 'ok', 'yes please'])
    assert.equal(classifyAnswer(yes), 'yes', yes);
  for (const no of ['sonra', 'Şimdi değil.', 'hayır', 'daha sonra', 'hayır sonra söyle', 'not now', 'later'])
    assert.equal(classifyAnswer(no), 'no', no);
  for (const other of ['', 'şimdi', 'hava nasıl', 'evet ama önce hava durumunu söyle', 'tamam bir hatırlatma ekle', 'ne var ne yok bugün takvimde'])
    assert.equal(classifyAnswer(other), undefined, other);
});

test('gate: chime once, nothing said before accept, one more chime after the snooze interval at most', async () => {
  const f = fixture([{ id: 'task-1', text: 'Kur: 34,10.', priority: 2 }]);
  assert.deepEqual(await f.gate.poll(true), { pending: 0 }, 'busy: nothing offered');
  assert.deepEqual(await f.gate.poll(false), { pending: 1, action: 'chime', sound: true });
  assert.equal(f.gate.awaiting, true);
  assert.deepEqual(await f.gate.poll(false), { pending: 1 }, 'no repeated chime');
  f.advance(29 * MIN); assert.deepEqual(await f.gate.poll(false), { pending: 1 });
  f.advance(MIN); assert.deepEqual(await f.gate.poll(false), { pending: 1, action: 'chime', sound: true }, 'one more after the snooze interval');
  f.advance(5 * 60 * MIN); assert.deepEqual(await f.gate.poll(false), { pending: 1 }, 'never a third time');
  assert.deepEqual(f.acked, [], 'still queued, not said');
  assert.equal(await f.gate.accept(), 'Kur: 34,10.');
  assert.deepEqual(f.acked, ['task-1']);
  assert.deepEqual(await f.gate.poll(false), { pending: 0 });
});

test('gate: "sonra" snoozes (silent until then), chime off = ring state only, a new message prompts again', async () => {
  const f = fixture([{ id: 'task-1', text: 'A.', priority: 2 }], false);
  assert.deepEqual(await f.gate.poll(false), { pending: 1, action: 'chime', sound: false });
  f.gate.decline(); assert.equal(f.gate.awaiting, false);
  f.items.push({ id: 'task-2', text: 'B.', priority: 0 });
  f.advance(10 * MIN); assert.deepEqual(await f.gate.poll(false), { pending: 2 }, 'snoozed: even a new message waits');
  f.advance(20 * MIN); assert.deepEqual(await f.gate.poll(false), { pending: 2, action: 'chime', sound: false });
  f.advance(MIN); assert.deepEqual(await f.gate.poll(false), { pending: 2 });
  f.items.push({ id: 'task-3', text: 'C.', priority: 2 });
  assert.deepEqual(await f.gate.poll(false), { pending: 3, action: 'chime', sound: false }, 'a new message is new news');
  // Multiple messages: priority order, identical texts once, all acknowledged.
  f.items.push({ id: 'task-4', text: 'C.', priority: 2 });
  assert.equal(await f.gate.accept(), 'B.\nA.\nC.');
  assert.deepEqual(f.acked.sort(), ['task-1', 'task-2', 'task-3', 'task-4']);
});

test('gate: in a conversation a spoken question (count only), never the content; restarts bypass it', async () => {
  const f = fixture([{ id: 'task-1', text: 'Gizli sonuç.', priority: 2 }, { id: 'run-1', text: 'Güncellendi.', priority: 1 }]);
  f.gate.userActivity(); f.advance(MIN);
  const asked = await f.gate.poll(false);
  assert.deepEqual(asked, { pending: 2, action: 'ask', text: 'Sana söylemem gereken 2 şey var, anlatayım mı?' });
  assert.ok(!asked.text!.includes('Gizli'));
  f.advance(3 * MIN); assert.deepEqual(await f.gate.poll(false), { pending: 2 });
  const r = fixture([{ id: 'restart-1', text: 'Yeniden başlıyorum.', restart: true, priority: 1 }]);
  assert.deepEqual(await r.gate.poll(false), { pending: 0 });
  assert.deepEqual(await r.gate.restart(), { id: 'restart-1', text: 'Yeniden başlıyorum.', restart: true });
});

test('server: chime when idle, "sonra" snoozes, ring click says every message and joins the conversation', async () => {
  const lifecycle = new AbortController(), T = new Date(2026, 9, 16, 10, 0).getTime();
  let requests = 0;
  const remote: Chat = { complete: async (context, _signal, options) => {
    const prompt = textOf(context.messages.at(-1)!);
    if (options?.automated) return { ...fauxAssistantMessage(prompt.includes('kur') ? 'Kur 34,10.' : 'Hava güneşli.'), provider: 'openai' };
    requests++; return { ...fauxAssistantMessage('chat reply'), provider: 'openai' };
  } };
  const config: Config = { ...defaults, tasks_enabled: true };
  const app = new Orchestrator(config, remote, remote, { ensure: async () => {}, close: async () => {} },
    { transcribe: async () => '', speak: async () => {}, stop: () => {}, close: async () => {} });
  app.loadInstructions = async () => '';
  const store = new TaskStore(':memory:');
  const seed = new TaskService(store, undefined, () => T);
  for (const [name, prompt] of [['Kur', 'kur bak'], ['Hava', 'hava bak']]) seed.create(seed.preview({ name, prompt, schedule: 'in 30m' }));
  const server = await makeServer(config, app, lifecycle.signal, null, undefined, undefined, {},
    { store, transport: { send: async () => { throw Error('no reminders'); } } as never, notify: async () => {}, firstMs: 0, everyMs: 3_600_000, now: () => T + 60 * MIN });
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  const address = server.address(); assert.ok(address && typeof address === 'object');
  const base = `http://127.0.0.1:${address.port}`;
  try {
    const { token } = await (await fetch(base + '/session')).json() as { token: string };
    const post = async (url: string, body: unknown = {}, signal?: AbortSignal) =>
      (await fetch(base + url, { method: 'POST', headers: { 'X-Assistant-Token': token }, body: JSON.stringify(body), signal })).json();
    for (let i = 0; i < 100 && (store.runs(1)[0]?.status !== 'ok' || store.runs(2)[0]?.status !== 'ok'); i++) await new Promise(r => setTimeout(r, 20));
    let state = await post('/announce/wait') as { pending: number; action?: string };
    if (state.pending === 1) state = await post('/announce/wait', { pending: 1 }) as typeof state; // second result landed later
    assert.equal(state.pending, 2); assert.equal(state.action, 'chime');
    await assert.rejects(post('/announce/wait', { pending: 2 }, AbortSignal.timeout(1500)), 'no second chime');
    assert.deepEqual(await post('/chat', { text: 'Şimdi değil' }), { text: 'Tamam, sonra.', provider: 'Ayhan', fallback_reason: '' });
    assert.equal(requests, 0, 'answered without a model');
    assert.equal((await post('/chat', { text: 'evet' })).text, 'chat reply', 'no longer awaiting: an ordinary request');
    const said = await post('/announce/accept') as { text: string; pending: number };
    assert.deepEqual(said.text.split('\n').sort(), ['Hava: Hava güneşli.', 'Kur: Kur 34,10.']);
    assert.equal(textOf(app.history.at(-1)!), said.text);
    assert.deepEqual(await post('/announce/wait', { pending: 2 }), { pending: 0 });
    assert.deepEqual(await post('/announce/accept'), { pending: 0 });
  } finally { lifecycle.abort(); await new Promise<void>(resolve => server.close(() => resolve())); }
});
