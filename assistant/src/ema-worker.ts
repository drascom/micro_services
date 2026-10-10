import { spawn, type ChildProcess } from 'node:child_process';
import { createInterface } from 'node:readline';
import { access } from 'node:fs/promises';
import { constants } from 'node:fs';
import { stopProcess } from './processes.ts';
import type { Config } from './config.ts';

interface WorkerReply { ready?: boolean; id?: number; ok?: boolean; error?: string }

export class EmaWorker {
  config: Config;
  child?: ChildProcess;
  ready?: Promise<void>;
  readyResolve?: () => void;
  readyReject?: (error: Error) => void;
  pending?: { id: number; resolve: () => void; reject: (error: Error) => void };
  active?: Promise<void>;
  nextId = 1;
  stderr = '';

  constructor(config: Config) { this.config = config; }

  private async start(signal: AbortSignal): Promise<void> {
    if (this.child && this.ready) return this.waitFor(this.ready, signal);
    await access(this.config.ema_python, constants.X_OK); await access(this.config.ema_worker);
    const child = spawn(this.config.ema_python, ['-u', this.config.ema_worker, this.config.ema_device],
      { stdio: ['pipe', 'pipe', 'pipe'], env: { ...process.env, HF_HOME: this.config.ema_cache } });
    this.child = child; this.stderr = '';
    this.ready = new Promise<void>((resolve, reject) => { this.readyResolve = resolve; this.readyReject = reject; });
    child.stderr?.setEncoding('utf8');
    child.stderr?.on('data', (chunk: string) => { this.stderr = (this.stderr + chunk).slice(-4000); });
    if (!child.stdout || !child.stdin) throw Error('EMA worker pipes unavailable');
    child.stdout.setEncoding('utf8');
    createInterface({ input: child.stdout }).on('line', line => this.receive(line));
    child.once('error', error => this.fail(error));
    child.once('exit', (code, childSignal) => this.fail(Error(`EMA worker stopped (${childSignal || (code ?? 'unknown')})`)));
    try { await this.waitFor(this.ready, signal); }
    catch (error) { await this.close(); throw error; }
  }

  private receive(line: string): void {
    let reply: WorkerReply;
    try { reply = JSON.parse(line) as WorkerReply; }
    catch { this.fail(Error('EMA worker returned invalid data')); return; }
    if (reply.ready) { this.readyResolve?.(); this.readyResolve = undefined; this.readyReject = undefined; return; }
    const pending = this.pending;
    if (!pending || reply.id !== pending.id) { this.fail(Error('EMA worker returned an unexpected response')); return; }
    this.pending = undefined;
    if (reply.ok) pending.resolve(); else pending.reject(Error(reply.error || 'EMA synthesis failed'));
  }

  private fail(error: Error): void {
    const child = this.child;
    const detail = this.stderr.trim();
    const failure = detail ? Error(`${error.message}: ${detail.slice(-400)}`) : error;
    this.readyReject?.(failure); this.pending?.reject(failure);
    this.readyResolve = undefined; this.readyReject = undefined; this.pending = undefined;
    this.child = undefined; this.ready = undefined;
    void stopProcess(child);
  }

  private async waitFor<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
    signal.throwIfAborted();
    let rejectAbort!: (error: unknown) => void;
    const aborted = new Promise<never>((_, reject) => { rejectAbort = reject; });
    const abort = () => rejectAbort(signal.reason || Error('EMA request cancelled'));
    signal.addEventListener('abort', abort, { once: true });
    try { return await Promise.race([promise, aborted]); }
    finally { signal.removeEventListener('abort', abort); }
  }

  async synthesize(text: string, output: string, signal: AbortSignal): Promise<void> {
    const previous = this.active;
    if (previous) await previous.catch(() => undefined);
    const current = this.perform(text, output, signal);
    this.active = current;
    try { await current; }
    finally { if (this.active === current) this.active = undefined; }
  }

  private async perform(text: string, output: string, signal: AbortSignal): Promise<void> {
    try {
      await this.start(signal);
      signal.throwIfAborted();
      const id = this.nextId++;
      const response = new Promise<void>((resolve, reject) => { this.pending = { id, resolve, reject }; });
      this.child?.stdin?.write(JSON.stringify({ id, text, output, speed: this.config.ema_speed, seed: 0 }) + '\n');
      await this.waitFor(response, signal);
    } catch (error) {
      if (signal.aborted) await this.close();
      throw error;
    }
  }

  async close(): Promise<void> {
    const child = this.child;
    this.child = undefined; this.ready = undefined;
    this.readyReject?.(Error('EMA worker closed')); this.pending?.reject(Error('EMA worker closed'));
    this.readyResolve = undefined; this.readyReject = undefined; this.pending = undefined;
    await stopProcess(child);
  }
}
