import { spawn, type ChildProcess } from 'node:child_process';
import { access, mkdir, open } from 'node:fs/promises';
import { constants } from 'node:fs';
import { setTimeout as delay } from 'node:timers/promises';
import path from 'node:path';
import { root, type Config } from './config.ts';
import { stopProcess } from './processes.ts';

/** Whisper decoding flags shared by whisper-cli and whisper-server. -bo/-bs pin the CLI defaults
 *  (best-of 5, beam 5); the server's own defaults differ (2 / greedy) and would change accuracy. */
export function whisperArgs(c: Config): string[] {
  return ['-m', c.whisper_model, '-l', c.language, '-t', String(c.whisper_threads), '-nt', '-bo', '5', '-bs', '5'];
}

/** Persistent localhost whisper-server: started lazily on first transcription, model stays loaded,
 *  stopped with SIGTERM after whisper_idle_ms without use or when the assistant closes. */
export class WhisperServer {
  config: Config; child?: ChildProcess; starting?: Promise<void>; idle?: NodeJS.Timeout; busy = 0;
  constructor(config: Config) { this.config = config; }
  url(pathname: string): string { return new URL(pathname, this.config.whisper_url).href; }
  async healthy(signal?: AbortSignal): Promise<boolean> {
    try {
      const response = await fetch(this.url('/health'), {
        signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(2000)]) : AbortSignal.timeout(2000), redirect: 'error' });
      const value = await response.json() as { status?: string };
      return response.ok && value.status === 'ok';
    } catch { return false; }
  }
  running(): boolean { return Boolean(this.child && this.child.exitCode === null && this.child.signalCode === null); }
  async ensure(signal: AbortSignal): Promise<void> {
    signal.throwIfAborted();
    if (await this.healthy(signal)) return;
    if (this.running() && !this.starting) { await this.close(); }
    this.starting ??= this.start().finally(() => { this.starting = undefined; });
    // A cancelled request stops waiting; the shared startup keeps going for the next one.
    let onAbort = () => {};
    try {
      await Promise.race([this.starting, new Promise((_, reject) => {
        onAbort = () => reject(signal.reason); signal.addEventListener('abort', onAbort, { once: true });
      })]);
    } finally { signal.removeEventListener('abort', onAbort); }
  }
  private async start(): Promise<void> {
    const c = this.config;
    await access(c.whisper_server_bin, constants.X_OK); await access(c.whisper_model);
    await mkdir(path.join(root, 'data'), { recursive: true, mode: 0o700 });
    const log = await open(path.join(root, 'data/whisper.log'), 'a', 0o600);
    const url = new URL(c.whisper_url);
    // --public points at a missing directory so the server exposes no static files.
    const args = [...whisperArgs(c), '--host', '127.0.0.1', '--port', url.port, '--public', path.join(root, 'data/.whisper-no-public')];
    let child: ChildProcess;
    try { child = this.child = spawn(c.whisper_server_bin, args, { stdio: ['ignore', log.fd, log.fd] }); }
    finally { await log.close(); }
    let launchError: Error | undefined;
    child.once('error', error => { launchError = error; });
    const deadline = AbortSignal.timeout(c.whisper_start_timeout_ms);
    try {
      while (true) {
        deadline.throwIfAborted();
        if (launchError || child.exitCode !== null || child.signalCode !== null)
          throw Error('Whisper server exited during startup; see data/whisper.log');
        if (await this.healthy(deadline)) return;
        await delay(100, undefined, { signal: deadline });
      }
    } catch (error) { await this.close(); throw error; }
  }
  async transcribe(bytes: Buffer, signal: AbortSignal): Promise<string> {
    this.busy++; this.clearIdle();
    try {
      await this.ensure(signal);
      const form = new FormData();
      form.set('file', new Blob([new Uint8Array(bytes)], { type: 'audio/wav' }), 'speech.wav');
      form.set('response_format', 'text');
      // Aborting closes the connection; whisper-server checks that and stops decoding.
      const response = await fetch(this.url('/inference'), { method: 'POST', body: form, signal, redirect: 'error' });
      const text = await response.text();
      if (!response.ok) throw Error(`whisper-server failed (${response.status})`);
      return text;
    } finally { this.busy--; this.armIdle(); }
  }
  private clearIdle(): void { if (this.idle) clearTimeout(this.idle); this.idle = undefined; }
  private armIdle(): void {
    this.clearIdle();
    if (this.busy || !this.running()) return;
    this.idle = setTimeout(() => { if (!this.busy) void this.close(); }, this.config.whisper_idle_ms);
    this.idle.unref();
  }
  async close(): Promise<void> { this.clearIdle(); const child = this.child; this.child = undefined; await stopProcess(child); }
}
