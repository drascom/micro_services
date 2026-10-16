import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile, stat, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createModels, fauxProvider, fauxAssistantMessage, type Context, type AssistantMessage } from '@earendil-works/pi-ai';
import { loadConfig, defaults, type Config } from '../src/config.ts';
import { Orchestrator } from '../src/orchestrator.ts';
import { createCollection, PiChat, Steering, type Chat, type Classifier } from '../src/providers.ts';
import { FileCredentials } from '../src/credentials.ts';
import { LocalRuntime, runProcess } from '../src/processes.ts';
import { NoSpeechError, guardText, wavDuration, type SpeechService } from '../src/speech.ts';
import { makeServer } from '../src/server.ts';

const config: Config = { ...defaults };
const signal = () => new AbortController().signal;
function fixture(remoteFailure = false) {
  let remoteCalls = 0, localCalls = 0, starts = 0, classified = 0;
  let lastContext: Context | undefined;
  const reply = (text: string, provider: string): AssistantMessage => ({ ...fauxAssistantMessage(text), provider });
  const remote: Chat = { complete: async context => { remoteCalls++; lastContext = context; if (remoteFailure) throw Error('Remote failed'); return reply('remote reply', 'openai'); } };
  const local: Chat = { complete: async context => { localCalls++; lastContext = context; return reply('local reply', 'local-gemma'); } };
  const classifier: Classifier = { classify: async () => { classified++; return { choice: 'conversation', confidence: 0.9, provider: 'jev' }; } };
  const runtime = { ensure: async () => { starts++; }, close: async () => {} };
  const speech: SpeechService = { transcribe: async () => 'transcript', speak: async () => {}, stop: () => {}, close: async () => {} };
  const app = new Orchestrator(config, remote, local, runtime, speech, classifier);
  app.loadInstructions = async () => ''; // Tests must not depend on editable personal rules.
  return { app, remote, local, classifier, runtime, speech, counts: () => ({ remoteCalls, localCalls, starts, classified }), context: () => lastContext };
}

test('remote primary uses Jev, avoids local startup, commits successful pair', async () => {
  const f = fixture(); const reply = await f.app.ask('hello');
  assert.equal(reply.provider, 'openai'); assert.equal(reply.intent?.choice, 'conversation');
  assert.deepEqual(f.counts(), { remoteCalls: 1, localCalls: 0, starts: 0, classified: 1 });
  assert.equal(f.app.history.length, 2);
});
test('offline sends nothing to Codex or Jev', async () => {
  const f = fixture(); assert.equal((await f.app.ask('hello', true)).provider, 'local-gemma');
  assert.deepEqual(f.counts(), { remoteCalls: 0, localCalls: 1, starts: 1, classified: 0 });
});
test('typed web intent selects a harness skill and survives remote fallback', async () => {
  const f = fixture();
  f.classifier.classify = async () => ({ choice: 'web_search', confidence: 1, provider: 'jev' });
  let remoteSkill: string | undefined, localSkill: string | undefined;
  f.remote.complete = async (_context, _signal, options) => { remoteSkill = options?.skill; throw Error('Remote failed'); };
  const complete = f.local.complete;
  f.local.complete = async (context, signal, options) => { localSkill = options?.skill; return complete(context, signal, options); };
  await f.app.ask('latest London news');
  assert.equal(remoteSkill, 'online-search'); assert.equal(localSkill, 'online-search');
});
test('Jev email intent selects the mail-assistant skill', async () => {
  const f = fixture(true);
  f.classifier.classify = async () => ({ choice: 'email', confidence: 1, provider: 'jev' });
  let remoteSkill: string | undefined, localSkill: string | undefined;
  f.remote.complete = async (_context, _signal, options) => { remoteSkill = options?.skill; throw Error('Remote failed'); };
  const complete = f.local.complete;
  f.local.complete = async (context, signal, options) => { localSkill = options?.skill; return complete(context, signal, options); };
  await f.app.ask('son okunmamış maillerimi sınıflandır');
  assert.equal(remoteSkill, 'mail-assistant'); assert.equal(localSkill, 'mail-assistant');
});
test('remote failure carries recent history into fallback', async () => {
  const f = fixture(true); await f.app.ask('first'); const reply = await f.app.ask('second');
  assert.equal(reply.fallback_reason, 'Remote failed'); assert.equal(f.context()?.messages.length, 3);
});
test('Jev failure does not prevent chat', async () => {
  const f = fixture(); f.classifier.classify = async () => { throw Error('unavailable'); };
  const reply = await f.app.ask('hello'); assert.equal(reply.provider, 'openai'); assert.match(reply.intent_note!, /unavailable/);
});
test('both providers failing leaves history unchanged', async () => {
  const f = fixture(true); f.local.complete = async () => { throw Error('Local failed'); };
  await assert.rejects(f.app.ask('hello'), /Remote failed.*Local failed/); assert.equal(f.app.history.length, 0);
});
test('cancellation never initiates fallback or commits partial reply', async () => {
  const f = fixture(); const abort = new AbortController();
  f.remote.complete = async () => { abort.abort(); throw Error('aborted'); };
  await assert.rejects(f.app.ask('hello', false, abort.signal)); assert.equal(f.counts().starts, 0); assert.equal(f.app.history.length, 0);
});
test('long input is rejected before remote classification', async () => {
  const f = fixture(); await assert.rejects(f.app.ask('x'.repeat(20000)), /too long/);
  assert.equal(f.counts().classified, 0); assert.equal(f.counts().remoteCalls, 0);
});
test('history keeps complete pairs and a system prompt within budget', async () => {
  const f = fixture(); f.app.config = { ...config, history_chars: 600 };
  for (let i = 0; i < 15; i++) await f.app.ask('hello ' + i);
  const context = await f.app.context('next', true, signal());
  assert.ok(context.systemPrompt); assert.equal(context.messages.length % 2, 1);
});
test('real speech retained, silence hallucinations rejected', () => {
  for (const text of ['', '!!!', '[BLANK_AUDIO]', 'Altyazı M.K. Altyazı M.K.', 'Thank you.', 'evet evet evet evet evet evet'])
    assert.throws(() => guardText(text, 3, 0.5), Error, text);
  for (const text of ['Merhaba', 'Altyazı M.K. Gemma sunucusunu yeniden başlat.', 'I would like to thank you for the detailed review.'])
    assert.equal(guardText(text, 3, 0.5), text);
});
function wav(): Buffer {
  const b = Buffer.alloc(44 + 32000); b.write('RIFF'); b.writeUInt32LE(b.length - 8, 4); b.write('WAVE', 8);
  b.write('fmt ', 12); b.writeUInt32LE(16, 16); b.writeUInt16LE(1, 20); b.writeUInt16LE(1, 22);
  b.writeUInt32LE(16000, 24); b.writeUInt32LE(32000, 28); b.writeUInt16LE(2, 32); b.writeUInt16LE(16, 34);
  b.write('data', 36); b.writeUInt32LE(32000, 40); return b;
}
test('WAV parser validates channels, sample rate, truncation, and duration', () => {
  assert.equal(wavDuration(wav()), 1); assert.throws(() => wavDuration(Buffer.from('invalid')));
  const wrong = wav(); wrong.writeUInt32LE(44100, 24); assert.throws(() => wavDuration(wrong), /16 kHz/);
  assert.throws(() => wavDuration(wav().subarray(0, 100)), /Truncated/);
});
test('Pi adapter rejects error and empty results, accepts text', async () => {
  const f = fauxProvider({ provider: 'local-gemma', models: [{ id: config.local_model }] });
  const models = createModels(); models.setProvider(f.provider);
  f.setResponses([fauxAssistantMessage('ok'), fauxAssistantMessage('', { stopReason: 'error' }), fauxAssistantMessage('')]);
  const chat = new PiChat(models, config, true);
  assert.equal((await chat.complete({ messages: [] }, signal())).content[0].type, 'text');
  await assert.rejects(chat.complete({ messages: [] }, signal()), /failed/);
  await assert.rejects(chat.complete({ messages: [] }, signal()), /no usable/);
});
test('subscription adapter refuses API-key credentials and ambient key', async () => {
  const folder = await mkdtemp(path.join(tmpdir(), 'assistant-auth-test-'));
  const previous = process.env.OPENAI_API_KEY; process.env.OPENAI_API_KEY = 'test-never-send';
  try {
    await writeFile(path.join(folder, 'auth.json'), JSON.stringify({ openai: { type: 'api_key', key: 'test-never-send' } }));
    const c = { ...config, pi_agent_dir: folder };
    await assert.rejects(new PiChat(createCollection(c), c).complete({ messages: [] }, signal()), /sign-in required/);
  } finally { if (previous === undefined) delete process.env.OPENAI_API_KEY; else process.env.OPENAI_API_KEY = previous; await rm(folder, { recursive: true }); }
});
test('shared Pi credentials serialize concurrent updates and protect file', async () => {
  const folder = await mkdtemp(path.join(tmpdir(), 'assistant-store-test-')); const file = path.join(folder, 'auth.json');
  try {
    const first = new FileCredentials(file), second = new FileCredentials(file);
    await first.modify('openai', async () => ({ type: 'oauth', access: 'test', refresh: 'test', expires: 1, counter: 0 }));
    await Promise.all(Array.from({ length: 8 }, (_, i) => (i % 2 ? first : second).modify('openai', async credential => {
      assert.equal(credential?.type, 'oauth'); if (credential?.type !== 'oauth') throw Error('unexpected');
      return { ...credential, counter: Number(credential.counter) + 1 };
    })));
    const credential = await first.read('openai'); assert.equal(credential?.type, 'oauth');
    if (credential?.type === 'oauth') assert.equal(credential.counter, 8);
    assert.equal((await stat(file)).mode & 0o777, 0o600); assert.deepEqual(await first.list(), [{ providerId: 'openai', type: 'oauth' }]);
  } finally { await rm(folder, { recursive: true }); }
});
test('attaching to an existing healthy local server does not own or stop it', async () => {
  const server = createServer((_, res) => { res.end('{"status":"ok"}'); }); server.listen(0, '127.0.0.1'); await once(server, 'listening');
  const address = server.address(); assert.ok(address && typeof address === 'object');
  const runtime = new LocalRuntime({ ...config, local_url: `http://127.0.0.1:${address.port}/v1` });
  try { await runtime.ensure(signal()); assert.equal(runtime.child, undefined); await runtime.close(); assert.ok(server.listening); }
  finally { await new Promise<void>(resolve => server.close(() => resolve())); }
});
test('native API has no web page and rejects cross-origin/token forgery', async () => {
  const f = fixture(), lifecycle = new AbortController(), server = await makeServer(config, f.app, lifecycle.signal, null);
  server.listen(0, '127.0.0.1'); await once(server, 'listening'); const address = server.address(); assert.ok(address && typeof address === 'object');
  const base = `http://127.0.0.1:${address.port}`;
  try {
    assert.equal((await fetch(base)).status, 404);
    assert.equal((await fetch(base + '/index.html')).status, 404);
    const session = await (await fetch(base + '/session')).json() as { token: string };
    const token = session.token; assert.equal(typeof token, 'string');
    assert.ok(!JSON.stringify(session).includes('TYPESAFE_AI_API_TOKEN'));
    const post = (value: string, origin = base) => fetch(base + '/chat', { method: 'POST', headers: { 'X-Assistant-Token': value, Origin: origin }, body: '{"text":"hi","offline":true}' });
    assert.equal((await post('bad')).status, 403); assert.equal((await post(token, 'https://evil.example')).status, 403);
    const good = await post(token); assert.equal(good.status, 200); assert.equal((await good.json() as { provider: string }).provider, 'local-gemma');
  } finally { lifecycle.abort(); await new Promise<void>(resolve => server.close(() => resolve())); }
});
test('process timeout terminates an unresponsive helper', async () => {
  await assert.rejects(runProcess(process.execPath, ['-e', 'setInterval(()=>{},1000)'], AbortSignal.timeout(100)));
});
test('invalid settings are rejected', async () => {
  const folder = await mkdtemp(path.join(tmpdir(), 'assistant-config-test-')), file = path.join(folder, 'config.json');
  try {
    for (const settings of [{ port: '8000' }, { typo: 1 }, { local_url: 'http://0.0.0.0:1234/v1' },
      { remote_timeout_ms: -1 }, { tts_engine: 'unknown' }, { ema_device: 'tpu' }, { ema_speed: 0.1 },
      { kokoro_speed: 0.1 }, { kokoro_voice: '' }]) {
      await writeFile(file, JSON.stringify(settings)); await assert.rejects(loadConfig(file));
    }
  } finally { await rm(folder, { recursive: true }); }
});

test('empty and silent recordings are recoverable no-speech outcomes; malformed audio stays an error', () => {
  const wav = Buffer.alloc(44);
  wav.write('RIFF'); wav.writeUInt32LE(36, 4); wav.write('WAVE', 8);
  wav.write('fmt ', 12); wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20);
  wav.writeUInt16LE(1, 22); wav.writeUInt32LE(16000, 24); wav.writeUInt32LE(32000, 28);
  wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34); wav.write('data', 36);
  assert.throws(() => wavDuration(wav), NoSpeechError);
  assert.throws(() => guardText('', 1, 0.5), NoSpeechError);
  assert.throws(() => guardText('[BLANK_AUDIO]', 1, 0.5), NoSpeechError);
  assert.throws(() => wavDuration(Buffer.from('not audio')), error => error instanceof Error && !(error instanceof NoSpeechError));
});

test('no-speech API result has a stable code and releases the busy slot for the next request', async () => {
  const f = fixture(), lifecycle = new AbortController();
  f.app.speech.transcribe = async () => { throw new NoSpeechError(); };
  const server = await makeServer(config, f.app, lifecycle.signal, null);
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  const address = server.address(); assert.ok(address && typeof address === 'object');
  const base = `http://127.0.0.1:${address.port}`;
  try {
    const { token } = await (await fetch(base + '/session')).json() as { token: string };
    const headers = { 'X-Assistant-Token': token };
    const silent = await fetch(base + '/transcribe', { method: 'POST', headers, body: Buffer.from('test') });
    assert.equal(silent.status, 400);
    assert.equal((await silent.json() as { code: string }).code, 'NO_SPEECH');
    const next = await fetch(base + '/chat', { method: 'POST', headers, body: '{"text":"hi","offline":true}' });
    assert.equal(next.status, 200);
  } finally { lifecycle.abort(); await new Promise<void>(resolve => server.close(() => resolve())); }
});

test('dictation is logged as app id and length only, never reaches the model; auth and validation hold', async () => {
  const f = fixture(), lifecycle = new AbortController();
  const dir = await mkdtemp(path.join(tmpdir(), 'dictation-test-')), log = path.join(dir, 'activity.log');
  const server = await makeServer(config, f.app, lifecycle.signal, log);
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  const address = server.address(); assert.ok(address && typeof address === 'object');
  const base = `http://127.0.0.1:${address.port}`;
  try {
    const { token } = await (await fetch(base + '/session')).json() as { token: string };
    const headers = { 'X-Assistant-Token': token };
    // Transcription alone (the dictation path) never asks the model.
    const heard = await fetch(base + '/transcribe', { method: 'POST', headers, body: Buffer.from('audio') });
    assert.equal(heard.status, 200); assert.equal((await heard.json() as { text: string }).text, 'transcript');
    assert.equal((await fetch(base + '/transcribe', { method: 'POST', headers: { 'X-Assistant-Token': 'x' }, body: Buffer.from('audio') })).status, 403);
    const body = (app: unknown, chars: unknown = 11) => JSON.stringify({ app, text_chars: chars });
    assert.equal((await fetch(base + '/dictation', { method: 'POST', body: body('com.apple.TextEdit') })).status, 403);
    assert.equal((await fetch(base + '/dictation', { method: 'POST', headers, body: body('com.apple.TextEdit') })).status, 200);
    const raw = await readFile(log, 'utf8'), entries = raw.trim().split('\n').map(line => JSON.parse(line));
    const dictation = entries.find(e => e.event === 'dictation');
    assert.equal(dictation?.app, 'com.apple.TextEdit'); assert.equal(dictation?.text_chars, 11);
    assert.ok(entries.some(e => e.event === 'request_started' && e.kind === 'dictation'));
    assert.equal(f.counts().localCalls + f.counts().remoteCalls, 0); assert.equal(f.app.history.length, 0);
    for (const [app, chars] of [[42, 1], ['', 1], ['x'.repeat(201), 1], ['a\nb', 1], ['ok', 0], ['ok', 1.5], ['ok', 'x']])
      assert.equal((await fetch(base + '/dictation', { method: 'POST', headers, body: body(app, chars) })).status, 400);
  } finally { lifecycle.abort(); await new Promise<void>(resolve => server.close(() => resolve())); await rm(dir, { recursive: true, force: true }); }
});

test('remote fallback releases local runtime; explicit offline use keeps it loaded', async () => {
  const f = fixture(true); let stops = 0;
  f.runtime.close = async () => { stops++; };
  await f.app.ask('remote fallback'); assert.equal(stops, 1);
  await f.app.ask('offline request', true); assert.equal(stops, 1);
});

test('explicit unload terminates an owned local helper process', async () => {
  const f = fixture(), runtime = new LocalRuntime(config);
  const child = spawn(process.execPath, ['-e', 'setInterval(()=>{},1000)'], { stdio: 'ignore' });
  await once(child, 'spawn'); runtime.child = child; f.app.runtime = runtime;
  try { await f.app.unloadLocal(); assert.ok(child.exitCode !== null || child.signalCode !== null); assert.equal(runtime.child, undefined); }
  finally { await runtime.close(); }
});

test('unload waits for active request cancellation and rejects new work until shutdown completes', async () => {
  const f = fixture(), lifecycle = new AbortController();
  let start!: () => void, cancel!: () => void, finish!: () => void, closing!: () => void, stop!: () => void;
  const started = new Promise<void>(r => { start = r; });
  const cancelled = new Promise<void>(r => { cancel = r; });
  const finishGate = new Promise<void>(r => { finish = r; });
  const closeStarted = new Promise<void>(r => { closing = r; });
  const stopGate = new Promise<void>(r => { stop = r; });
  f.local.complete = async (_context, requestSignal) => {
    start();
    try { return await new Promise<never>((_resolve, reject) => requestSignal.addEventListener('abort', () => { cancel(); reject(Error('cancelled')); }, { once: true })); }
    finally { await finishGate; }
  };
  f.runtime.close = async () => { closing(); await stopGate; };
  const server = await makeServer(config, f.app, lifecycle.signal, null);
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  const address = server.address(); assert.ok(address && typeof address === 'object');
  const base = `http://127.0.0.1:${address.port}`;
  try {
    const { token } = await (await fetch(base + '/session')).json() as { token: string };
    const headers = { 'X-Assistant-Token': token };
    const request = fetch(base + '/chat', { method: 'POST', headers, body: '{"text":"hi","offline":true}' });
    await started;
    const unload = fetch(base + '/unload-local', { method: 'POST', headers, body: '{}' });
    await cancelled; finish(); await closeStarted;
    assert.equal((await fetch(base + '/chat', { method: 'POST', headers, body: '{"text":"new"}' })).status, 409);
    stop(); assert.equal((await unload).status, 200); assert.equal((await request).status, 400);
    assert.equal(f.app.history.length, 0);
  } finally { finish(); stop(); lifecycle.abort(); await new Promise<void>(resolve => server.close(() => resolve())); }
});

test('transcription runs beside an active chat without cancelling it; speech can be paused and resumed', async () => {
  const f = fixture(), lifecycle = new AbortController();
  let start!: () => void, finish!: () => void, aborted = false, pauses = 0, resumes = 0;
  const started = new Promise<void>(r => { start = r; });
  const gate = new Promise<void>(r => { finish = r; });
  const complete = f.local.complete;
  f.local.complete = async (context, signal, options) => {
    start(); signal.addEventListener('abort', () => { aborted = true; }, { once: true });
    await gate; return complete(context, signal, options);
  };
  f.app.speech.pause = () => { pauses++; }; f.app.speech.resume = () => { resumes++; };
  const server = await makeServer(config, f.app, lifecycle.signal, null);
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  const address = server.address(); assert.ok(address && typeof address === 'object');
  const base = `http://127.0.0.1:${address.port}`;
  try {
    const { token } = await (await fetch(base + '/session')).json() as { token: string };
    const headers = { 'X-Assistant-Token': token };
    const chat = fetch(base + '/chat', { method: 'POST', headers, body: '{"text":"hi","offline":true}' });
    await started;
    const heard = await fetch(base + '/transcribe', { method: 'POST', headers, body: Buffer.from('audio') });
    assert.equal(heard.status, 200); assert.equal((await heard.json() as { text: string }).text, 'transcript');
    assert.equal((await fetch(base + '/pause-speech', { method: 'POST', headers, body: '{}' })).status, 200);
    assert.equal((await fetch(base + '/resume-speech', { method: 'POST', headers, body: '{}' })).status, 200);
    assert.equal(pauses, 1); assert.equal(resumes, 1);
    finish(); assert.equal((await chat).status, 200); assert.equal(aborted, false);
  } finally { finish(); lifecycle.abort(); await new Promise<void>(resolve => server.close(() => resolve())); }
});

async function serve(f: ReturnType<typeof fixture>) {
  const lifecycle = new AbortController();
  const server = await makeServer(config, f.app, lifecycle.signal, null);
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  const address = server.address(); assert.ok(address && typeof address === 'object');
  const base = `http://127.0.0.1:${address.port}`;
  const { token } = await (await fetch(base + '/session')).json() as { token: string };
  const post = (route: string, body: unknown) => fetch(base + route, { method: 'POST', headers: { 'X-Assistant-Token': token }, body: JSON.stringify(body) });
  return { post, close: async () => { lifecycle.abort(); await new Promise<void>(resolve => server.close(() => resolve())); } };
}

test('barge-in: a chat while a request generates is steered into it (one reply, both texts in history)', async () => {
  const f = fixture();
  let start!: () => void, finish!: () => void, steered: string[] = [], aborted = false;
  const started = new Promise<void>(r => { start = r; }), gate = new Promise<void>(r => { finish = r; });
  f.local.complete = async (context, signal, options) => {
    options?.steering?.attach(text => { steered.push(text); return true; });
    signal.addEventListener('abort', () => { aborted = true; }, { once: true });
    start(); await gate; options?.steering?.detach();
    return { ...fauxAssistantMessage('one reply for ' + [JSON.stringify(context.messages.at(-1)?.content), ...steered].join(' + ')), provider: 'local-gemma' };
  };
  const s = await serve(f);
  try {
    const first = s.post('/chat', { text: 'weather today', offline: true });
    await started;
    const second = await s.post('/chat', { text: 'and tomorrow', offline: true });
    assert.equal(second.status, 200); assert.deepEqual(await second.json(), { steered: true });
    finish();
    const reply = await (await first).json() as { text: string };
    assert.match(reply.text, /and tomorrow/); assert.equal(aborted, false);
    assert.equal(f.app.history.length, 2); assert.equal(f.app.history[0].content, 'weather today\n\nand tomorrow');
  } finally { finish(); await s.close(); }
});

test('barge-in without steering support: the running request is cancelled and a new one starts at once with both texts', async () => {
  const f = fixture();
  let start!: () => void, calls = 0;
  const started = new Promise<void>(r => { start = r; });
  const prompts: string[] = [];
  f.local.complete = async (context, signal) => {
    calls++; prompts.push(String(context.messages.at(-1)?.content));
    if (calls === 1) { start(); await new Promise((_, reject) => signal.addEventListener('abort', () => reject(Error('aborted')), { once: true })); }
    return { ...fauxAssistantMessage('fresh reply'), provider: 'local-gemma' };
  };
  const s = await serve(f);
  try {
    const first = s.post('/chat', { text: 'first', offline: true });
    await started;
    const second = await s.post('/chat', { text: 'second', offline: true });
    assert.equal(second.status, 200); assert.equal((await second.json() as { text: string }).text, 'fresh reply');
    const old = await first; assert.equal(old.status, 409); assert.equal((await old.json() as { code: string }).code, 'REPLACED');
    assert.deepEqual(prompts, ['first', 'first\n\nsecond']); assert.equal(f.app.history.length, 2);
  } finally { await s.close(); }
});

test('reply finished and only speech playing: stop-speech ends it and a new chat runs immediately', async () => {
  const f = fixture();
  let stops = 0; f.app.speech.stop = () => { stops++; };
  const s = await serve(f);
  try {
    assert.equal((await s.post('/chat', { text: 'one', offline: true })).status, 200);
    assert.equal((await s.post('/stop-speech', {})).status, 200); assert.equal(stops, 1);
    const next = await s.post('/chat', { text: 'two', offline: true });
    assert.equal(next.status, 200); assert.equal((await next.json() as { text: string }).text, 'local reply');
  } finally { await s.close(); }
});

test('steering is refused when no live session is attached', () => {
  const steering = new Steering();
  assert.equal(steering.steer('x'), false);
  steering.attach(() => true); assert.equal(steering.steer('  y '), true); assert.equal(steering.steer('   '), false);
  steering.detach(); assert.equal(steering.steer('z'), false); assert.deepEqual(steering.accepted, ['y']);
});
test('assistant task requests reach the Reminders tasks route, whatever Jev picked', async () => {
  for (const [choice, prompt] of [['skill_management', 'sana bir görev vermek istiyorum'], ['updates', "her sabah 9'da maillerimi özetle ve bana gönder"],
    ['conversation', 'görev ekle: faturaları kontrol et'], ['skill_management', 'görevlerimi listele'], ['updates', 'şu görevi sil']] as const) {
    const f = fixture(); f.app.config = { ...config, reminders_enabled: true, tasks_enabled: true };
    f.classifier.classify = async () => ({ choice, confidence: 0.8, provider: 'jev' });
    let skill: string | undefined; f.remote.complete = async (_c, _s, options) => { skill = options?.skill; return { ...fauxAssistantMessage('ok'), provider: 'openai' }; };
    await f.app.ask(prompt);
    assert.equal(skill, 'reminders', prompt);
  }
  // Unrelated requests keep Jev's route (updates stays updates); tasks off = no override.
  const f = fixture(); f.app.config = { ...config, reminders_enabled: true, tasks_enabled: true };
  let skill: string | undefined; f.remote.complete = async (_c, _s, options) => { skill = options?.skill; return { ...fauxAssistantMessage('ok'), provider: 'openai' }; };
  f.classifier.classify = async () => ({ choice: 'updates', confidence: 1, provider: 'jev' }); await f.app.ask('güncellemeleri kur');
  assert.equal(skill, 'updates');
  f.classifier.classify = async () => ({ choice: 'conversation', confidence: 1, provider: 'jev' }); await f.app.ask('bugün hava nasıl');
  assert.equal(skill, undefined);
  f.app.config = { ...config, reminders_enabled: true, tasks_enabled: false };
  f.classifier.classify = async () => ({ choice: 'skill_management', confidence: 1, provider: 'jev' }); await f.app.ask('sana bir görev vermek istiyorum');
  assert.equal(skill, 'skill-manager');
});
