import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { setStopGrace, shutdown, stopProcess } from '../src/processes.ts';
import { closeServer } from '../src/server.ts';

// A helper that ignores SIGTERM (like a busy llama/whisper/python worker) and only dies on SIGKILL.
const stubborn = async () => {
  const child = spawn(process.execPath, ['-e', "process.on('SIGTERM', () => {}); console.log('ready'); setInterval(() => {}, 1000)"], { stdio: ['ignore', 'pipe', 'ignore'] });
  await once(child.stdout!, 'data');
  return child;
};

test('shutdown stops stubborn children and a polled keep-alive server within ~2-3 s, logging only step durations', async () => {
  const children = await Promise.all([stubborn(), stubborn(), stubborn()]);
  const server = http.createServer((_request, response) => setTimeout(() => response.end('{}'), 300));
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = (server.address() as { port: number }).port;
  // The app keeps polling over one keep-alive connection, which alone would hold server.close() open.
  const agent = new http.Agent({ keepAlive: true, maxSockets: 1 });
  let polling = true;
  const poll = () => { if (!polling) return; http.get({ host: '127.0.0.1', port, agent }, r => { r.resume(); r.on('end', poll); }).on('error', () => {}); };
  poll(); await new Promise(r => setTimeout(r, 100));
  const lines: string[] = [];
  setStopGrace(1000);
  const started = Date.now();
  try {
    await shutdown({
      http: () => closeServer(server),
      llama: () => stopProcess(children[0]), whisper: () => stopProcess(children[1]), ema: () => stopProcess(children[2]),
      hang: () => new Promise(() => {}),
    }, line => lines.push(line), 2500);
  } finally { setStopGrace(3000); polling = false; agent.destroy(); }
  const elapsed = Date.now() - started;
  assert.ok(elapsed < 3000, `shutdown took ${elapsed}ms`);
  for (const child of children) assert.ok(child.exitCode !== null || child.signalCode === 'SIGKILL', 'child still running');
  assert.equal(server.listening, false);
  assert.equal(lines.length, 1);
  assert.match(lines[0], /^shutdown: http \d+ms, llama \d+ms, whisper \d+ms, ema \d+ms, hang \d+ms \(timeout\); total \d+ms$/);
});
