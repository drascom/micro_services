import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile, readFile, chmod } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createServer } from 'node:net';
import { setTimeout as delay } from 'node:timers/promises';
import { defaults, type Config } from '../src/config.ts';
import { WhisperServer, whisperArgs } from '../src/whisper.ts';
import { Speech } from '../src/speech.ts';

// Fake whisper-server: records its argv and each start, answers /health and /inference, logs SIGTERM.
const fakeServer = (dir: string) => `#!/usr/bin/env node
const http = require('node:http'), fs = require('node:fs'), args = process.argv.slice(2);
fs.appendFileSync(${JSON.stringify(path.join(dir, 'starts'))}, JSON.stringify(args) + '\\n');
const port = Number(args[args.indexOf('--port') + 1]);
const server = http.createServer((req, res) => {
  if (req.url === '/health') { res.setHeader('content-type', 'application/json'); return res.end('{"status":"ok"}'); }
  if (req.url === '/inference') {
    let size = 0; req.on('data', c => { size += c.length; });
    req.on('end', () => {
      if (fs.existsSync(${JSON.stringify(path.join(dir, 'slow'))})) return setTimeout(() => res.end(' late '), 5000);
      res.end(' Merhaba dünya, bytes ' + size + ' ');
    });
    return;
  }
  res.statusCode = 404; res.end();
});
server.listen(port, '127.0.0.1');
process.on('SIGTERM', () => { fs.writeFileSync(${JSON.stringify(path.join(dir, 'sigterm'))}, 'yes'); process.exit(0); });
`;
const fakeCli = (dir: string) => `#!/bin/sh\necho "$@" >> ${JSON.stringify(path.join(dir, 'cli-args'))}\necho " CLI ile yazıya döküldü"\n`;

async function freePort(): Promise<number> {
  const server = createServer(); await new Promise<void>(r => server.listen(0, '127.0.0.1', r));
  const port = (server.address() as { port: number }).port; await new Promise(r => server.close(r)); return port;
}
function wav(seconds = 1): Buffer {
  const data = Math.round(seconds * 16000) * 2, b = Buffer.alloc(44 + data);
  b.write('RIFF', 0, 'ascii'); b.writeUInt32LE(36 + data, 4); b.write('WAVE', 8, 'ascii');
  b.write('fmt ', 12, 'ascii'); b.writeUInt32LE(16, 16); b.writeUInt16LE(1, 20); b.writeUInt16LE(1, 22);
  b.writeUInt32LE(16000, 24); b.writeUInt32LE(32000, 28); b.writeUInt16LE(2, 32); b.writeUInt16LE(16, 34);
  b.write('data', 36, 'ascii'); b.writeUInt32LE(data, 40);
  for (let i = 0; i < data / 2; i++) b.writeInt16LE(Math.round(Math.sin(i / 8) * 8000), 44 + i * 2);
  return b;
}
async function setup(t: test.TestContext, overrides: Partial<Config> = {}) {
  const dir = await mkdtemp(path.join(tmpdir(), 'whisper-test-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const server = path.join(dir, 'whisper-server'), cli = path.join(dir, 'whisper-cli'), model = path.join(dir, 'model.bin');
  await writeFile(server, fakeServer(dir)); await writeFile(cli, fakeCli(dir)); await writeFile(model, '');
  await chmod(server, 0o755); await chmod(cli, 0o755);
  const config: Config = { ...defaults, whisper_server_bin: server, whisper_bin: cli, whisper_model: model,
    whisper_url: `http://127.0.0.1:${await freePort()}`, whisper_start_timeout_ms: 10000, ...overrides };
  const starts = async () => { try { return (await readFile(path.join(dir, 'starts'), 'utf8')).trim().split('\n').map(l => JSON.parse(l) as string[]); } catch { return []; } };
  return { dir, config, starts };
}

test('whisper_beam_size drives -bs/-bo for both server and CLI fallback', async t => {
  assert.deepEqual(whisperArgs({ ...defaults }).slice(-4), ['-bs', '1', '-bo', '1']); // default: greedy
  const f = await setup(t, { whisper_beam_size: 5 });
  const server = new WhisperServer(f.config); t.after(() => server.close());
  await server.transcribe(wav(), AbortSignal.timeout(5000));
  const args = (await f.starts())[0];
  assert.equal(args[args.indexOf('-bs') + 1], '5'); assert.equal(args[args.indexOf('-bo') + 1], '5');
  const off = new Speech({ ...f.config, whisper_server: false }); t.after(() => off.close());
  await off.transcribe(wav(), AbortSignal.timeout(5000));
  const cli = (await readFile(path.join(f.dir, 'cli-args'), 'utf8')).trim().split(' ');
  for (const [flag, value] of [['-bs', '5'], ['-bo', '5'], ['-l', 'auto'], ['-t', '8']]) assert.equal(cli[cli.indexOf(flag) + 1], value, flag);
  assert.ok(cli.includes('-nt') && cli.includes('-np'));
});

test('whisper server starts lazily once, keeps CLI decoding flags and stays loopback-only', async t => {
  const f = await setup(t); const whisper = new WhisperServer(f.config); t.after(() => whisper.close());
  assert.equal(whisper.running(), false); assert.deepEqual(await f.starts(), []);
  const audio = wav();
  const [a, b] = await Promise.all([whisper.transcribe(audio, AbortSignal.timeout(5000)), whisper.transcribe(audio, AbortSignal.timeout(5000))]);
  assert.match(a, /Merhaba dünya/); assert.match(b, /Merhaba dünya/);
  assert.match(await whisper.transcribe(audio, AbortSignal.timeout(5000)), /Merhaba/);
  const starts = await f.starts(); assert.equal(starts.length, 1);
  const args = starts[0];
  for (const [flag, value] of [['-l', 'auto'], ['-t', '8'], ['-bs', '1'], ['-bo', '1'], ['--host', '127.0.0.1'],
    ['--port', new URL(f.config.whisper_url).port], ['-m', f.config.whisper_model]])
    assert.equal(args[args.indexOf(flag) + 1], value, flag);
  assert.ok(args.includes('-nt'));
});

test('whisper server stops with SIGTERM on close and after the idle timeout', async t => {
  const f = await setup(t, { whisper_idle_ms: 300 }); const whisper = new WhisperServer(f.config); t.after(() => whisper.close());
  await whisper.transcribe(wav(), AbortSignal.timeout(5000));
  assert.equal(whisper.running(), true);
  await delay(900);
  assert.equal(whisper.running(), false); assert.equal(await whisper.healthy(), false);
  assert.equal(await readFile(path.join(f.dir, 'sigterm'), 'utf8'), 'yes');
  await whisper.transcribe(wav(), AbortSignal.timeout(5000)); // restarts lazily after idle shutdown
  assert.equal((await f.starts()).length, 2);
  await rm(path.join(f.dir, 'sigterm'));
  await whisper.close(); assert.equal(await readFile(path.join(f.dir, 'sigterm'), 'utf8'), 'yes');
});

test('cancelling a transcription aborts the request without falling back to the CLI', async t => {
  const f = await setup(t); const speech = new Speech(f.config); t.after(() => speech.close());
  await writeFile(path.join(f.dir, 'slow'), '');
  const controller = new AbortController(); setTimeout(() => controller.abort(), 1500);
  await assert.rejects(speech.transcribe(wav(), controller.signal), { name: 'AbortError' });
});

test('broken whisper server falls back once to whisper-cli; disabled server uses only the CLI', async t => {
  const f = await setup(t);
  await writeFile(f.config.whisper_server_bin, '#!/bin/sh\nexit 3\n');
  const speech = new Speech(f.config); t.after(() => speech.close());
  assert.equal(await speech.transcribe(wav(), AbortSignal.timeout(5000)), 'CLI ile yazıya döküldü');
  assert.equal(speech.whisper.running(), false);
  const off = new Speech({ ...f.config, whisper_server: false }); t.after(() => off.close());
  await writeFile(f.config.whisper_server_bin, fakeServer(f.dir));
  assert.equal(await off.transcribe(wav(), AbortSignal.timeout(5000)), 'CLI ile yazıya döküldü');
  assert.deepEqual(await f.starts(), []);
});

test('whisper_url must be loopback HTTP on its own port', async t => {
  const { loadConfig } = await import('../src/config.ts');
  const dir = await mkdtemp(path.join(tmpdir(), 'whisper-config-')); t.after(() => rm(dir, { recursive: true, force: true }));
  const file = path.join(dir, 'config.json');
  for (const bad of ['http://0.0.0.0:8092', 'https://127.0.0.1:8092', 'http://127.0.0.1', 'http://127.0.0.1:8765', 'http://127.0.0.1:8091', 'http://127.0.0.1:8092/x']) {
    await writeFile(file, JSON.stringify({ whisper_url: bad }));
    await assert.rejects(loadConfig(file), /whisper_url/, bad);
  }
  for (const beam of [0, -1, 1.5, 17]) {
    await writeFile(file, JSON.stringify({ whisper_beam_size: beam }));
    await assert.rejects(loadConfig(file), /whisper_beam_size/, String(beam));
  }
  await writeFile(file, JSON.stringify({ whisper_url: 'http://localhost:9100', whisper_idle_ms: 1000 }));
  assert.equal((await loadConfig(file)).whisper_url, 'http://localhost:9100');
});
