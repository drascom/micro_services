import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fauxAssistantMessage, type AssistantMessage, type Context } from '@earendil-works/pi-ai';
import { defaults, loadConfig, type Config } from '../src/config.ts';
import { Orchestrator } from '../src/orchestrator.ts';
import type { Chat, Classifier } from '../src/providers.ts';
import type { Embedder } from '../src/embeddings.ts';
import { isPrivate, MEMORY_LIMITS, memoryRequest, MemoryService, parseExtraction, TOPIC_IDLE_MS, TOPIC_LIMITS, topicCommand, type ExtractModel } from '../src/memory.ts';
import { makeServer } from '../src/server.ts';

const signal = () => new AbortController().signal;
/** Deterministic bag-of-words vectors (task prefixes stripped): similar sentences → high cosine. */
function fakeEmbedder(state = { up: true, calls: 0 }): Embedder & { state: typeof state } {
  const vector = (text: string) => {
    const body = text.replace(/^.*\| (text|query): /, '');
    const v = new Float32Array(256);
    for (const word of body.toLocaleLowerCase('tr').split(/[^\p{L}\p{N}]+/u).filter(Boolean)) {
      let h = 0; for (const c of word) h = (h * 31 + c.codePointAt(0)!) >>> 0;
      v[h % 256] += 1;
    }
    let n = 0; for (const x of v) n += x * x; n = Math.sqrt(n) || 1;
    return v.map(x => x / n);
  };
  return { state, multimodal: false, close: async () => {}, healthy: async () => state.up,
    embed: async inputs => { state.calls++; if (!state.up) throw Error('Embedding server unavailable'); return inputs.map(i => vector('text' in i ? i.text : '')); } };
}
function service(config: Partial<Config> = {}, embedder: Embedder | undefined = fakeEmbedder()) {
  const settings = { ...defaults, ...config } as Config;
  return { settings, memory: new MemoryService(settings, embedder, ':memory:') };
}
const reply = (text: string, provider: string): AssistantMessage => ({ ...fauxAssistantMessage(text), provider });
function app(memory: MemoryService, config: Config, options: { remoteFails?: boolean; intent?: string; tools?: string[] } = {}) {
  const contexts: { provider: string; context: Context; memory?: boolean }[] = [];
  const remote: Chat = { complete: async (context, _s, o) => {
    contexts.push({ provider: 'remote', context, memory: o?.memory }); for (const t of options.tools ?? []) o?.used?.add(t);
    if (options.remoteFails) throw Error('Remote failed'); return reply('remote reply', 'openai'); } };
  const local: Chat = { complete: async (context, _s, o) => {
    contexts.push({ provider: 'local', context, memory: o?.memory }); for (const t of options.tools ?? []) o?.used?.add(t); return reply('local reply', 'local-gemma'); } };
  const classifier: Classifier = { classify: async () => ({ choice: options.intent ?? 'conversation', confidence: 1, provider: 'jev' }) };
  const speech = { transcribe: async () => '', speak: async () => {}, stop: () => {}, close: async () => {} };
  const orchestrator = new Orchestrator(config, remote, local, { ensure: async () => {}, close: async () => {} }, speech, classifier);
  orchestrator.loadInstructions = async () => '';
  orchestrator.memory = memory;
  return { orchestrator, contexts };
}

test('memoryRequest: hatırla/unut/remember, never hatırlat (reminders)', () => {
  for (const text of ['Bunu hatırla: kısa cevap ver', 'şunu unutma', 'aklında tut', 'Ayşe ile ilgili notu unut', 'Remember that I like tea', 'forget my old address', 'hafızandan sil'])
    assert.ok(memoryRequest(text), text);
  for (const text of ['yarın 9da bana hatırlat', 'hatırlatıcı ekle', 'hava nasıl']) assert.ok(!memoryRequest(text), text);
});

test('save and forget: explicit item stored, clear match deleted, unclear query gives candidates', async () => {
  const { memory } = service();
  assert.equal((await memory.store.save({ kind: 'assistant', text: 'Cevapları kısa tut', source: 'explicit' }, signal())).status, 'saved');
  assert.equal((await memory.store.save({ kind: 'personal', text: 'Kızımın adı Elif', source: 'explicit' }, signal())).status, 'saved');
  const unclear = await memory.store.forget('hava durumu', signal());
  assert.equal(unclear.deleted, undefined);
  const result = await memory.store.forget('kızımın adı', signal());
  assert.equal(result.deleted?.text, 'Kızımın adı Elif');
  assert.deepEqual((await memory.store.list()).items.map(i => i.text), ['Cevapları kısa tut']);
});

test('contradiction: the same key replaces the old value; a near duplicate merges', async () => {
  const { memory } = service();
  await memory.store.save({ kind: 'personal', key: 'device:printer', text: 'Yazıcı HP LaserJet', source: 'explicit' }, signal());
  assert.equal((await memory.store.save({ kind: 'personal', key: 'device:printer', text: 'Yazıcı Brother HL', source: 'explicit' }, signal())).status, 'updated');
  await memory.store.save({ kind: 'assistant', text: 'Önce plan yap sonra uygula', source: 'explicit' }, signal());
  await memory.store.save({ kind: 'assistant', text: 'önce plan yap, sonra uygula.', source: 'explicit' }, signal());
  const items = (await memory.store.list()).items;
  assert.deepEqual(items.filter(i => i.kind === 'personal').map(i => i.text), ['Yazıcı Brother HL']);
  assert.equal(items.filter(i => i.kind === 'assistant').length, 1);
});

test('manual beats auto: an extracted item never overwrites an explicit one; extracted rules need a second hit', async () => {
  const { memory } = service();
  await memory.store.save({ kind: 'assistant', key: 'reply.length', text: 'Cevaplar kısa olsun', source: 'explicit' }, signal());
  assert.equal((await memory.store.save({ kind: 'assistant', key: 'reply.length', text: 'Cevaplar uzun ve detaylı olsun', source: 'extracted', confidence: 1 }, signal())).status, 'kept');
  assert.equal((await memory.store.list()).items[0].text, 'Cevaplar kısa olsun');
  await memory.store.save({ kind: 'assistant', key: 'confirm.delete', text: 'Silmeden önce onay iste', source: 'extracted', confidence: 0.9 }, signal());
  assert.ok(!(await memory.store.block(false)).includes('Silmeden'), 'one extracted mention is not enough');
  await memory.store.save({ kind: 'assistant', key: 'confirm.delete', text: 'Silmeden önce onay iste', source: 'extracted', confidence: 0.9 }, signal());
  assert.ok((await memory.store.block(false)).includes('Silmeden'), 'repeated: promoted');
});

test('privacy filter: secrets and mail text are never stored or sent to an extractor', async () => {
  const secrets = ['Gmail şifrem Kedi123!', 'my password is hunter2', 'doğrulama kodu 482913', 'IBAN TR33 0006 1005 1978 6457 8413 26',
    'kart 4111 1111 1111 1111', 'api key sk-abcdefghijklmnopqrstuvwxyz0123456789', 'Konu: Fatura\nKimden: banka@example.com\nMerhaba',
    '> önceki mesaj alıntısı', 'Bitwarden kasamda github girişi var', 'kod 123456'];
  for (const text of secrets) assert.ok(isPrivate(text), text);
  for (const text of ['Kızımın adı Elif', 'Sabahları 7de kalkarım', 'Yazıcı Brother HL-L2350', '2026 yazında İzmir\'e taşındım']) assert.ok(!isPrivate(text), text);
  const { memory, settings } = service();
  for (const text of secrets) assert.equal((await memory.store.save({ kind: 'personal', text, source: 'explicit' }, signal())).status, 'rejected');
  let sent = '';
  memory.extractor.model = async (_system, user) => { sent = user; return JSON.stringify({ items: secrets.map(text => ({ kind: 'personal', text, confidence: 1 })), episode: 'şifre 482913 konuşuldu' }); };
  for (const text of secrets) memory.extractor.record({ user: text, assistant: 'tamam', offline: false, localReply: false, at: 1 });
  assert.equal(memory.extractor.turns.length, 0, 'private turns never enter the buffer');
  memory.extractor.record({ user: 'Bugün yürüyüşe çıktım', assistant: 'Güzel', offline: false, localReply: false, at: 1 });
  await memory.extractor.run();
  assert.ok(!secrets.some(s => sent.includes(s)));
  const all = await memory.store.list();
  assert.equal(all.items.length + all.episodes.length, 0);
  assert.ok(settings.memory_enabled);
});

test('remote request gets only assistant rules; personal facts and recall only with the local model', async () => {
  const { memory, settings } = service();
  await memory.store.save({ kind: 'assistant', text: 'Cevapları kısa tut', source: 'explicit' }, signal());
  await memory.store.save({ kind: 'personal', text: 'Kızımın adı Elif', source: 'explicit', pinned: true }, signal());
  await memory.store.save({ kind: 'personal', text: 'Arabam kırmızı Toyota Corolla', source: 'explicit' }, signal());
  await memory.store.addEpisode('Toyota servis randevusu konuşuldu', Date.now() - 1000, Date.now(), signal());
  const online = app(memory, settings, { remoteFails: true });
  await online.orchestrator.ask('Toyota Corolla arabam servis randevusu');
  const [remote, local] = online.contexts;
  assert.equal(remote.provider, 'remote');
  assert.match(remote.context.systemPrompt!, /Cevapları kısa tut/);
  for (const secret of ['Elif', 'Toyota Corolla', 'servis randevusu']) assert.ok(!remote.context.systemPrompt!.includes(secret), secret);
  assert.match(local.context.systemPrompt!, /Cevapları kısa tut/);
  assert.match(local.context.systemPrompt!, /Kızımın adı Elif/);
  assert.match(local.context.systemPrompt!, /Arabam kırmızı Toyota Corolla/);
  assert.match(local.context.systemPrompt!, /servis randevusu/);
});

test('memory tools are offered only for a hatırla/unut user request', async () => {
  const { memory, settings } = service();
  const a = app(memory, settings);
  await a.orchestrator.ask('Bunu hatırla: cevaplar kısa olsun');
  await a.orchestrator.ask('Hava nasıl?');
  assert.deepEqual(a.contexts.map(c => c.memory), [true, false]);
});

test('extractor: remote model online, local model offline; offline turns and local replies never reach the remote', async () => {
  const { memory, settings } = service();
  const calls: { local: boolean; user: string }[] = [];
  memory.extractor.model = async (_system, user, local) => { calls.push({ local, user }); return '```json\n{"items":[{"kind":"personal","key":"pet","text":"Kedisinin adı Pamuk","confidence":0.9},{"kind":"personal","text":"belki","confidence":0.3}],"episode":"Kedi Pamuk anlatıldı"}\n```'; };
  let offline = false; memory.extractor.offline = () => offline;
  const now = Date.now();
  memory.extractor.record({ user: 'Yerel modda söyledim: doktorum Dr. Kaya', assistant: 'tamam', offline: true, localReply: true, at: now - 2 });
  memory.extractor.record({ user: 'Kedimin adı Pamuk', assistant: 'Yerel cevap: Elif ile Pamuk', offline: false, localReply: true, at: now - 1 });
  await memory.extractor.run();
  assert.equal(calls.length, 1); assert.equal(calls[0].local, false);
  assert.ok(calls[0].user.includes('Kedimin adı Pamuk'));
  assert.ok(!calls[0].user.includes('Dr. Kaya') && !calls[0].user.includes('Elif'), 'remote run: no offline turn, no local reply');
  assert.deepEqual(memory.extractor.turns.map(t => t.at), [now - 2], 'offline turn waits for a local run');
  offline = true; await memory.extractor.run();
  assert.equal(calls[1].local, true); assert.ok(calls[1].user.includes('Dr. Kaya'));
  assert.equal(memory.extractor.turns.length, 0);
  const all = await memory.store.list();
  assert.deepEqual(all.items.map(i => i.text), ['Kedisinin adı Pamuk'], 'low confidence dropped, same key merged');
  assert.equal(all.items[0].source, 'extracted');
  assert.equal(all.episodes.length, 2);
  assert.ok(settings.memory_extract);
});

test('vault, mail and file conversations and the explicit memory turn are never buffered for extraction', async () => {
  for (const [intent, tools] of [['vault', []], ['email', []], ['mail_triage', []], ['files', []], ['conversation', ['mail_read_message']], ['conversation', ['vault_search']]] as const) {
    const { memory, settings } = service();
    await app(memory, settings, { intent, tools: [...tools] }).orchestrator.ask('bir şey sor');
    assert.equal(memory.extractor.turns.length, 0, `${intent} ${tools.join()}`);
  }
  const { memory, settings } = service();
  const a = app(memory, settings);
  await a.orchestrator.ask('bunu hatırla: çay severim');
  assert.equal(memory.extractor.turns.length, 0);
  await a.orchestrator.ask('Bugün hava güzel');
  assert.equal(memory.extractor.turns.length, 1);
});

test('Reminders tasks never read or write memory', async () => {
  const { memory, settings } = service();
  await memory.store.save({ kind: 'assistant', text: 'Cevapları kısa tut', source: 'explicit' }, signal());
  await memory.store.save({ kind: 'personal', text: 'Kızımın adı Elif', source: 'explicit', pinned: true }, signal());
  const a = app(memory, settings, { remoteFails: true });
  await a.orchestrator.ask('hatırla: görev metni', false, signal(), { automated: true, intentText: 'görev' });
  for (const c of a.contexts) { assert.ok(!c.context.systemPrompt!.includes('Memory'), c.provider); assert.equal(c.memory, false); }
  assert.equal(memory.extractor.turns.length, 0);
});

test('a new request cancels a running extraction; its turns stay buffered', async () => {
  const { memory, settings } = service();
  let started!: () => void; const running = new Promise<void>(resolve => { started = resolve; });
  let aborted = false;
  const model: ExtractModel = (_system, _user, _local, s) => new Promise((_resolve, reject) => {
    started(); s.addEventListener('abort', () => { aborted = true; reject(s.reason); }, { once: true });
  });
  memory.extractor.model = model;
  memory.extractor.record({ user: 'Bisiklete binmeyi severim', assistant: 'Güzel', offline: false, localReply: false, at: 1 });
  const pass = memory.extractor.run();
  await running;
  const a = app(memory, settings);
  await a.orchestrator.ask('Hava nasıl?');
  await pass;
  assert.ok(aborted);
  assert.equal(memory.extractor.turns.length, 2);
  memory.close();
});

test('embedding server down: saving still works, recall is skipped without waiting, the block still applies', async () => {
  const embedder = fakeEmbedder({ up: false, calls: 0 });
  const { memory, settings } = service({}, embedder);
  assert.equal((await memory.store.save({ kind: 'assistant', text: 'Cevapları kısa tut', source: 'explicit' }, signal())).status, 'saved');
  assert.equal((await memory.store.save({ kind: 'personal', text: 'Arabam kırmızı Toyota', source: 'explicit' }, signal())).status, 'saved');
  const started = performance.now();
  assert.equal(await memory.store.retrieve('Toyota', signal()), '');
  assert.ok(performance.now() - started < 300);
  assert.equal((await memory.store.forget('arabam kırmızı toyota', signal())).deleted?.text, 'Arabam kırmızı Toyota', 'forget by text without vectors');
  const a = app(memory, settings);
  await a.orchestrator.ask('merhaba', true);
  assert.match(a.contexts[0].context.systemPrompt!, /Cevapları kısa tut/);
  // The server comes back: rows saved without vectors are embedded on the next save, recall works.
  embedder.state.up = true;
  await memory.store.save({ kind: 'personal', text: 'Bisiklete binmeyi severim', source: 'explicit' }, signal());
  assert.match(await memory.store.retrieve('bisiklete binmeyi', signal()), /Bisiklete/);
});

test('context budget: memory blocks stay within their caps; the history window is not shrunk', async () => {
  const { memory, settings } = service();
  for (let i = 0; i < 30; i++) await memory.store.save({ kind: 'assistant', key: `rule.${i}`, text: `Kural ${i}: ` + 'uzun açıklama '.repeat(10), source: 'explicit' }, signal());
  for (let i = 0; i < 30; i++) await memory.store.save({ kind: 'personal', key: `fact.${i}`, text: `Bilgi ${i} ` + 'ayrıntı '.repeat(15), source: 'explicit', pinned: true }, signal());
  for (let i = 0; i < 30; i++) await memory.store.save({ kind: 'personal', key: `other.${i}`, text: `Bisiklet notu ${i} ` + 'bisiklet '.repeat(15), source: 'explicit' }, signal());
  const remote = await memory.store.block(false), local = await memory.store.block(true);
  const lines = (block: string, header: string) => block.split('\n').slice(block.split('\n').indexOf(header) + 1).filter(l => l.startsWith('- '));
  assert.ok(lines(remote, 'How to work with the user:').join('\n').length <= MEMORY_LIMITS.assistant);
  assert.ok(!remote.includes('About the user'));
  assert.ok(lines(local, 'About the user:').join('\n').length <= MEMORY_LIMITS.personal);
  const recall = await memory.store.retrieve('bisiklet', signal());
  assert.ok(recall && recall.length <= MEMORY_LIMITS.retrieved, String(recall.length));
  assert.ok(remote.length + local.length + recall.length <= settings.memory_chars + MEMORY_LIMITS.assistant + 200);
  // Full 6000-char history plus memory: nothing of the history is dropped.
  const a = app(memory, settings);
  const history = Array.from({ length: 10 }, (_, i) => i % 2 ? { ...fauxAssistantMessage('b'.repeat(560)), provider: 'openai' } : { role: 'user' as const, content: 'a'.repeat(560), timestamp: i });
  const remoteContext = await a.orchestrator.context('x'.repeat(50), false, signal(), history);
  assert.equal(remoteContext.messages.length, 11);
  assert.match(remoteContext.systemPrompt!, /Kural 0/);
  const without = app(new MemoryService({ ...settings, memory_enabled: false }, fakeEmbedder(), ':memory:'), settings);
  assert.equal((await without.orchestrator.context('x'.repeat(50), false, signal(), history)).messages.length, 11);
  assert.equal(settings.history_chars, 6000);
});

test('extractor output parsing tolerates prose and bad fields', () => {
  assert.deepEqual(parseExtraction('not json'), { items: [], episode: '' });
  const value = parseExtraction('Sure: {"items":[{"kind":"other","text":"x"},{"kind":"assistant","text":"Kısa yaz","confidence":0.9}],"episode":5} done');
  assert.deepEqual(value, { items: [{ kind: 'assistant', text: 'Kısa yaz', confidence: 0.9 }], episode: '' });
});

test('Admin memory endpoints: list, add, edit, pin, delete, switches (private text refused)', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'ayhan-memory-'));
  try {
    const { memory, settings } = service({ pi_agent_dir: directory } as Partial<Config>);
    const { orchestrator } = app(memory, settings);
    const lifecycle = new AbortController();
    const server = await makeServer(settings, orchestrator, lifecycle.signal, null, directory, [path.join(directory, 'trash')], { configPath: path.join(directory, 'config.json') });
    await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
    const port = (server.address() as { port: number }).port, base = `http://127.0.0.1:${port}`;
    const { token } = await (await fetch(base + '/session')).json() as { token: string };
    const call = async (route: string, body: unknown = {}) => {
      const response = await fetch(base + route, { method: 'POST', headers: { 'X-Assistant-Token': token, 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      return { status: response.status, data: await response.json() as { items: { id: number; text: string; pinned: boolean; source: string }[]; enabled: boolean; extract: boolean; error?: string } };
    };
    try {
      let result = await call('/admin/memory/save', { kind: 'assistant', text: 'Cevapları kısa tut' });
      assert.equal(result.data.items.length, 1);
      const id = result.data.items[0].id;
      result = await call('/admin/memory/save', { id, text: 'Cevapları çok kısa tut', pinned: true });
      assert.deepEqual([result.data.items[0].text, result.data.items[0].pinned], ['Cevapları çok kısa tut', true]);
      assert.equal((await call('/admin/memory/save', { kind: 'personal', text: 'şifrem 1234abcd' })).status, 400);
      result = await call('/admin/memory/settings', { extract: false });
      assert.equal(result.data.extract, false); assert.equal(settings.memory_extract, false);
      // The removed topic-to-remote switch: no longer a setting; an old config.json key is ignored.
      assert.equal((await call('/admin/memory/settings', { topic_remote: false })).status, 400);
      assert.ok(!('topic_remote' in (await call('/admin/memory')).data));
      await writeFile(path.join(directory, 'legacy.json'), JSON.stringify({ memory_topic_remote: false }));
      assert.ok(!('memory_topic_remote' in await loadConfig(path.join(directory, 'legacy.json'))));
      await stat(path.join(directory, 'config.json'));
      result = await call('/admin/memory/delete', { id });
      assert.equal(result.data.items.length, 0);
      assert.equal((await call('/admin/memory')).data.enabled, true);
    } finally { lifecycle.abort(); server.close(); server.closeAllConnections(); }
  } finally { await rm(directory, { recursive: true, force: true }); }
});

// ---------------------------------------------------------------------------------------------------------------
// Topics (P3)

test('topic commands: create, continue, close, list, delete; ordinary sentences are no command', () => {
  assert.deepEqual(topicCommand('Yeni konu: Tatil planı'), { type: 'create', title: 'Tatil planı' });
  assert.deepEqual(topicCommand('Ring projesi üzerinde çalışıyoruz.'), { type: 'create', title: 'Ring projesi' });
  assert.deepEqual(topicCommand("Ring'e devam edelim"), { type: 'continue', name: 'Ring' });
  assert.deepEqual(topicCommand('ring projesine devam edelim'), { type: 'continue', name: 'ring projesi' });
  assert.deepEqual(topicCommand('Ring projesi'), { type: 'continue', name: 'Ring projesi' });
  assert.deepEqual(topicCommand('Konuyu kapat'), { type: 'close' });
  assert.deepEqual(topicCommand('Hangi konular var?'), { type: 'list' });
  assert.deepEqual(topicCommand('Tatil konusunu sil'), { type: 'delete', name: 'Tatil' });
  for (const text of ['hava nasıl', 'yarın 9da bana hatırlat', 'bu projede ne kaldı']) assert.equal(topicCommand(text), undefined, text);
});

test('topics: create + activate, local and remote get the active summary; continue, auto-detect, idle expiry, reset', async () => {
  const { memory, settings } = service();
  const a = app(memory, settings, { remoteFails: true });
  await a.orchestrator.ask('Ring projesi üzerinde çalışıyoruz');
  const [created] = await memory.store.topics();
  assert.equal(created.title, 'Ring projesi'); assert.ok(created.active);
  await memory.store.mergeTopic(created.id, { state: 'Menü ikonları ve ayarlar ekranı yapılıyor', decisions: ['SF Symbols kullanılacak'], open: ['Koyu tema testi'] }, signal());
  await a.orchestrator.ask('Sıradaki adım ne?');
  const [remote, local] = a.contexts.slice(-2);
  assert.equal(remote.provider, 'remote');
  assert.match(remote.context.systemPrompt!, /Active topic \(reference only\): Ring projesi/);
  for (const text of ['Menü ikonları', 'SF Symbols', 'Koyu tema']) assert.ok(remote.context.systemPrompt!.includes(text), text);
  assert.match(local.context.systemPrompt!, /Active topic \(reference only\): Ring projesi/);
  assert.match(local.context.systemPrompt!, /SF Symbols kullanılacak/);
  assert.equal(memory.extractor.turns.at(-1)?.topic, created.id, 'turn carries the active topic');
  // Reset ends the segment: no topic; "Ring'e devam edelim" activates it again.
  a.orchestrator.reset();
  assert.equal(memory.store.activeTopic(), undefined);
  await a.orchestrator.ask("Ring'e devam edelim");
  assert.equal(memory.store.activeTopic(), created.id);
  // Idle expiry.
  const start = Date.now(); memory.store.now = () => start + TOPIC_IDLE_MS + 1000;
  assert.equal(memory.store.activeTopic(), undefined);
  // Auto-detect: a prompt close to the topic state activates it; an unrelated prompt does not.
  await a.orchestrator.ask('Bugün hava çok güzel');
  assert.equal(memory.store.activeTopic(), undefined);
  await a.orchestrator.ask('menü ikonları ve ayarlar ekranı yapılıyor mu');
  assert.equal(memory.store.activeTopic(), created.id);
});

test('remote topic block: always on, only the active topic, never personal facts/summaries, privacy filter, budget', async () => {
  const { memory, settings } = service();
  await memory.store.save({ kind: 'personal', text: 'Kızımın adı Elif', source: 'explicit', pinned: true }, signal());
  await memory.store.addEpisode('Toyota servis randevusu konuşuldu', Date.now() - 1000, Date.now(), signal());
  await memory.store.createTopic('Tatil planı', signal(), { state: 'Otel arıyoruz' });
  const ring = (await memory.store.createTopic('Ring projesi', signal(), { state: 'Ayarlar ekranı', decisions: Array.from({ length: 8 }, (_, i) => `Karar ${i} ${'x'.repeat(100)}`), open: ['Koyu tema'] }))!;
  // A private line slipped into storage (e.g. an older row) never reaches the remote model.
  (await memory.store.open()).prepare('UPDATE topics SET summary = ? WHERE id = ?').run('şifrem 1234abcd', ring.id);
  await memory.store.activate(ring.id);
  const a = app(memory, settings, { remoteFails: true });
  await a.orchestrator.ask('Sıradaki adım ne?');
  const [remote, local] = a.contexts.slice(-2);
  const prompt = remote.context.systemPrompt!;
  assert.match(prompt, /Active topic \(reference only\): Ring projesi/);
  assert.ok(prompt.includes('Koyu tema'));
  for (const secret of ['Elif', 'servis randevusu', 'Tatil planı', 'Otel arıyoruz', '1234abcd', 'Konular:']) assert.ok(!prompt.includes(secret), secret);
  assert.ok(local.context.systemPrompt!.includes('1234abcd'), 'local block unchanged');
  assert.ok((await memory.store.topicBlock(false)).length <= MEMORY_LIMITS.topic);
  assert.ok(prompt.length - settings.system_prompt.length <= settings.memory_chars + 1);
});

test('online auto-detect: works with the embedding server; skipped silently when it is down', async () => {
  const embedder = fakeEmbedder();
  const { memory, settings } = service({}, embedder);
  const ring = (await memory.store.createTopic('Ring projesi', signal(), { state: 'Menü ikonları ve ayarlar ekranı yapılıyor' }))!;
  const a = app(memory, settings);
  embedder.state.up = false;
  assert.equal((await a.orchestrator.ask('menü ikonları ve ayarlar ekranı yapılıyor mu')).text, 'remote reply');
  assert.equal(memory.store.activeTopic(), undefined);
  embedder.state.up = true;
  await a.orchestrator.ask('menü ikonları ve ayarlar ekranı yapılıyor mu');
  assert.equal(memory.store.activeTopic(), ring.id);
  assert.match(a.contexts.at(-1)!.context.systemPrompt!, /Active topic \(reference only\): Ring projesi/);
});

test('topic commands answered without a model: list, close, delete', async () => {
  const { memory, settings } = service();
  const a = app(memory, settings);
  await memory.store.createTopic('Tatil planı', signal());
  await memory.store.activate((await memory.store.createTopic('Ring projesi', signal()))!.id);
  let reply = await a.orchestrator.ask('Hangi konular var?');
  assert.equal(reply.text, 'Konular: Ring projesi (aktif), Tatil planı.'); assert.equal(reply.provider, 'memory');
  reply = await a.orchestrator.ask('Konuyu kapat');
  assert.equal(reply.text, '“Ring projesi” konusu kapatıldı.');
  assert.equal(memory.store.activeTopic(), undefined);
  assert.equal((await memory.store.topics()).find(t => t.title === 'Ring projesi')?.status, 'done');
  reply = await a.orchestrator.ask('Tatil konusunu sil');
  assert.equal(reply.text, '“Tatil planı” konusu silindi.');
  assert.equal((await a.orchestrator.ask('Hangi konular var')).text, 'Kayıtlı konu yok.');
  assert.equal(a.contexts.length, 0, 'no model call');
  assert.equal(memory.extractor.turns.length, 0, 'commands are not extracted');
});

test('extraction updates the active topic (merge, done, caps); remote extractor gets no topic text; proposals need two batches', async () => {
  const { memory } = service();
  const topic = (await memory.store.createTopic('Ring projesi', signal(), { state: 'Başladık', open: ['Koyu tema testi', 'İkon seçimi'] }))!;
  const calls: { system: string; user: string; local: boolean }[] = [];
  let answer = '';
  memory.extractor.model = async (system, user, local) => { calls.push({ system, user, local }); return answer; };
  let offline = true; memory.extractor.offline = () => offline;
  answer = JSON.stringify({ items: [], episode: '', topic: { title: 'Ring', state: 'İkonlar bitti, tema kaldı', decisions: Array.from({ length: 12 }, (_, i) => `Karar ${i}`),
    open: ['Ayarlar ekranı'], done: ['ikon seçimi'] } });
  memory.extractor.record({ user: 'İkonları bitirdik', assistant: 'Harika', offline: true, localReply: true, at: 1, topic: topic.id });
  await memory.extractor.run();
  assert.match(calls[0].system, /Current topic:\nActive topic \(reference only\): Ring projesi/);
  let [t] = await memory.store.topics();
  assert.equal(t.state, 'İkonlar bitti, tema kaldı');
  assert.equal(t.decisions.length, TOPIC_LIMITS.decisions); assert.equal(t.decisions.at(-1), 'Karar 9', 'extractor lists capped at 10, topic keeps the newest 8');
  assert.deepEqual(t.open, ['Koyu tema testi', 'Ayarlar ekranı']);
  // Remote extractor (online): no topic text in the prompt; its delta still merges locally.
  offline = false;
  answer = JSON.stringify({ items: [], topic: { title: 'Ring', state: 'Tema bitti', decisions: ['Şifre: abc123xyz kaydedildi'], open: [], done: ['koyu tema testi'] } });
  memory.extractor.record({ user: 'Temayı bitirdik', assistant: 'Süper', offline: false, localReply: false, at: 2, topic: topic.id });
  await memory.extractor.run();
  for (const secret of ['Ring', 'İkonlar bitti', 'Karar', 'Ayarlar ekranı', 'Current topic']) assert.ok(!(calls[1].system + calls[1].user).includes(secret), secret);
  [t] = await memory.store.topics();
  assert.equal(t.state, 'Tema bitti'); assert.deepEqual(t.open, ['Ayarlar ekranı']);
  assert.ok(!t.decisions.some(d => d.includes('Şifre')), 'privacy filter');
  // No active topic: a new subject becomes a topic only on its second proposal.
  answer = JSON.stringify({ items: [], topic: { title: 'Bahçe sulama', state: 'Damla sulama düşünülüyor', open: ['Hortum ölç'] } });
  memory.extractor.record({ user: 'Bahçeye sulama kuralım', assistant: 'Olur', offline: false, localReply: false, at: 3 });
  await memory.extractor.run();
  assert.equal((await memory.store.topics()).length, 1, 'first proposal: candidate only');
  memory.extractor.record({ user: 'Sulama için boru aldım', assistant: 'Güzel', offline: false, localReply: false, at: 4 });
  await memory.extractor.run();
  const garden = (await memory.store.topics()).find(x => x.title === 'Bahçe sulama');
  assert.ok(garden && !garden.active); assert.deepEqual(garden.open, ['Hortum ölç']);
});

test('topics: privacy filter, Reminders tasks isolated, block within 600 chars and the memory budget', async () => {
  const { memory, settings } = service();
  assert.equal(await memory.store.createTopic('Banka şifresi', signal()), undefined);
  const a = app(memory, settings, { remoteFails: true });
  await a.orchestrator.ask('yeni konu: Görev projesi', false, signal(), { automated: true, intentText: 'görev' });
  assert.equal((await memory.store.topics()).length, 0, 'task creates no topic');
  const t = (await memory.store.createTopic('Uzun proje', signal()))!;
  await memory.store.mergeTopic(t.id, { state: 'durum '.repeat(100), decisions: Array.from({ length: 20 }, (_, i) => `Karar ${i} ` + 'ayrıntı '.repeat(20)),
    open: Array.from({ length: 20 }, (_, i) => `İş ${i} ` + 'ayrıntı '.repeat(20)) }, signal());
  const stored = (await memory.store.topics())[0];
  assert.ok(stored.state.length <= TOPIC_LIMITS.state && stored.open.length <= TOPIC_LIMITS.open && stored.decisions.every(d => d.length <= TOPIC_LIMITS.entry));
  await memory.store.activate(t.id);
  const block = await memory.store.topicBlock();
  assert.ok(block.length <= MEMORY_LIMITS.topic, String(block.length));
  await a.orchestrator.ask('görev', false, signal(), { automated: true, intentText: 'görev' });
  for (const c of a.contexts) assert.ok(!c.context.systemPrompt!.includes('Active topic'), 'task gets no topic');
  assert.ok(MEMORY_LIMITS.assistant + MEMORY_LIMITS.personal + MEMORY_LIMITS.retrieved + MEMORY_LIMITS.topic <= settings.memory_chars);
  const local = await a.orchestrator.context('x', true, signal());
  assert.match(local.systemPrompt!, /Active topic/);
  assert.equal(settings.history_chars, 6000);
});

test('Admin topics: edit, activate, delete', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'ayhan-topics-'));
  try {
    const { memory, settings } = service({ pi_agent_dir: directory } as Partial<Config>);
    const { orchestrator } = app(memory, settings);
    const lifecycle = new AbortController();
    const server = await makeServer(settings, orchestrator, lifecycle.signal, null, directory, [path.join(directory, 'trash')], { configPath: path.join(directory, 'config.json') });
    await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
    const base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
    const { token } = await (await fetch(base + '/session')).json() as { token: string };
    type Topics = { topics: { id: number; title: string; state: string; decisions: string[]; open: string[]; active: boolean }[] };
    const call = async (route: string, body: unknown = {}) => {
      const response = await fetch(base + route, { method: 'POST', headers: { 'X-Assistant-Token': token, 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      return { status: response.status, data: await response.json() as Topics };
    };
    try {
      const id = (await memory.store.createTopic('Ring projesi', signal()))!.id;
      let result = await call('/admin/memory/topic', { id, state: 'İkonlar', decisions: ['SF Symbols'], open: ['Tema'], active: true });
      assert.deepEqual([result.data.topics[0].state, result.data.topics[0].decisions, result.data.topics[0].open, result.data.topics[0].active], ['İkonlar', ['SF Symbols'], ['Tema'], true]);
      assert.equal((await call('/admin/memory/topic', { id, open: ['şifre 1234abcd'] })).status, 400);
      result = await call('/admin/memory/topic', { id, active: false });
      assert.equal(result.data.topics[0].active, false);
      result = await call('/admin/memory/delete', { topic: id });
      assert.equal(result.data.topics.length, 0);
    } finally { lifecycle.abort(); server.close(); server.closeAllConnections(); }
  } finally { await rm(directory, { recursive: true, force: true }); }
});
