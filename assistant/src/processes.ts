import { spawn, type ChildProcess } from 'node:child_process';
import { access, mkdir, open } from 'node:fs/promises';
import { constants } from 'node:fs';
import { once } from 'node:events';
import { setTimeout as delay } from 'node:timers/promises';
import path from 'node:path';
import { root, type Config } from './config.ts';

export async function stopProcess(child?: ChildProcess): Promise<void> {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  const exited = once(child, 'exit').catch(() => undefined);
  child.kill('SIGTERM');
  const escalation = setTimeout(() => child.kill('SIGKILL'), 3000);
  escalation.unref();
  try { await exited; } finally { clearTimeout(escalation); }
}

export async function runProcess(bin: string, args: string[], signal: AbortSignal, input?: string, started?: (child: ChildProcess) => void): Promise<string> {
  signal.throwIfAborted();
  const child = spawn(bin, args, { stdio: ['pipe', 'pipe', 'pipe'] });
  started?.(child);
  let output = '', error = '', overflow = false;
  const abort = () => { void stopProcess(child); };
  signal.addEventListener('abort', abort, { once: true });
  child.stdout.setEncoding('utf8'); child.stderr.setEncoding('utf8');
  child.stdout.on('data', (chunk: string) => { output += chunk; if (output.length > 4_000_000) { overflow = true; abort(); } });
  child.stderr.on('data', (chunk: string) => { error = (error + chunk).slice(-4000); });
  child.stdin.on('error', () => {});
  child.stdin.end(input);
  try {
    const [code] = await once(child, 'close');
    signal.throwIfAborted();
    if (overflow) throw Error('Process output exceeded limit');
    if (code !== 0) throw Error(`${path.basename(bin)} failed: ${error.slice(-400)}`);
    return output;
  } finally { signal.removeEventListener('abort', abort); }
}

/** Status of a helper process owned by the backend (no arguments or secrets). */
export interface OwnedProcess { name: string; running: boolean; pid?: number; port?: number; }
export function ownedProcess(name: string, child: ChildProcess | undefined, port?: number): OwnedProcess {
  const running = Boolean(child && child.exitCode === null && child.signalCode === null);
  return { name, running, ...(running && child?.pid ? { pid: child.pid } : {}), ...(port ? { port } : {}) };
}
export interface Runtime { ensure(signal: AbortSignal): Promise<void>; close(): Promise<void>; processes?(): OwnedProcess[]; healthy?(signal?: AbortSignal): Promise<boolean>; }
export class LocalRuntime implements Runtime {
  config: Config; child?: ChildProcess;
  constructor(config: Config) { this.config = config; }
  async healthy(signal?: AbortSignal): Promise<boolean> {
    try {
      const response = await fetch(this.config.local_url.replace(/\/v1\/?$/, '/health'), {
        signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(2000)]) : AbortSignal.timeout(2000),
        redirect: 'error', headers: process.env.ASSISTANT_LOCAL_API_KEY ? { Authorization: `Bearer ${process.env.ASSISTANT_LOCAL_API_KEY}` } : {}
      });
      const value = await response.json() as { status?: string };
      return response.ok && value.status === 'ok';
    } catch { return false; }
  }
  async ensure(signal: AbortSignal): Promise<void> {
    signal.throwIfAborted();
    if (await this.healthy(signal)) return;
    const c = this.config;
    if (!c.auto_start_local) throw Error('Local server unavailable; automatic startup disabled');
    if (this.child && this.child.exitCode === null && this.child.signalCode === null)
      throw Error('Owned local server is unavailable; restart the assistant');
    await access(c.llama_bin, constants.X_OK); await access(c.llama_model);
    await mkdir(path.join(root, 'data'), { recursive: true, mode: 0o700 });
    const log = await open(path.join(root, 'data/llama.log'), 'a', 0o600);
    const args = ['-m', c.llama_model, '--alias', c.local_model, '-ngl', '999', '-c', String(c.local_context),
      '--flash-attn', 'on', '--cache-type-k', 'q8_0', '--cache-type-v', 'q8_0', '--reasoning-budget', String(c.local_reasoning_budget),
      // A clean hand-off line keeps a cut-off thought from leaking into the reply.
      '--reasoning-budget-message', ' Enough thinking, I will answer now.', '--parallel', '1', '--jinja', '--host', '127.0.0.1', '--port', new URL(c.local_url).port];
    if (process.env.ASSISTANT_LOCAL_API_KEY) args.push('--api-key', process.env.ASSISTANT_LOCAL_API_KEY);
    try { this.child = spawn(c.llama_bin, args, { stdio: ['ignore', log.fd, log.fd] }); }
    finally { await log.close(); }
    let launchError: Error | undefined;
    this.child.once('error', error => { launchError = error; });
    const deadline = AbortSignal.any([signal, AbortSignal.timeout(c.local_start_timeout_ms)]);
    try {
      while (true) {
        deadline.throwIfAborted();
        if (launchError || this.child.exitCode !== null || this.child.signalCode !== null)
          throw Error('Local model exited during startup; see data/llama.log');
        if (await this.healthy(deadline)) return;
        await delay(250, undefined, { signal: deadline });
      }
    } catch (error) { await this.close(); throw error; }
  }
  processes(): OwnedProcess[] { return [ownedProcess('llama-server', this.child, Number(new URL(this.config.local_url).port))]; }
  async close(): Promise<void> { await stopProcess(this.child); this.child = undefined; }
}
