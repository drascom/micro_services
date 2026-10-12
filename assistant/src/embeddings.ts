import { spawn, type ChildProcess } from 'node:child_process';
import { access, mkdir, open } from 'node:fs/promises';
import { constants } from 'node:fs';
import { setPriority } from 'node:os';
import { setTimeout as delay } from 'node:timers/promises';
import path from 'node:path';
import { root, type Config } from './config.ts';
import { ownedProcess, stopProcess, type OwnedProcess } from './processes.ts';

/**
 * EmbeddingGemma 2 on its own llama-server (`--embeddings --pooling mean`, optional `--mmproj` for image/audio).
 * Text, images and audio land in one vector space; vectors are MRL-truncated to `embedding_dims` and re-normalized.
 */
export type EmbedInput = { text: string } | { image: Buffer; mime: string } | { audio: Buffer; format: 'wav' | 'mp3' };
export interface Embedder {
  /** One vector per input (same order); throws when the server is unavailable. */
  embed(inputs: EmbedInput[], signal: AbortSignal): Promise<Float32Array[]>;
  /** Image/audio inputs are accepted (projector loaded). */
  readonly multimodal: boolean;
  close(): Promise<void>;
  processes?(): OwnedProcess[];
  healthy?(signal?: AbortSignal): Promise<boolean>;
}

/** Model card task prompts (text only; media go without a prefix). */
export const queryText = (query: string) => `task: search result | query: ${query}`;
export const documentText = (title: string, text: string) => `title: ${title.replace(/\s+/g, ' ').trim() || 'none'} | text: ${text}`;
export const clusterText = (text: string) => `task: clustering | query: ${text}`;

/** Keep the first `dims` values (Matryoshka) and L2-normalize again. */
export function truncate(vector: ArrayLike<number>, dims: number): Float32Array {
  const out = new Float32Array(dims);
  let norm = 0;
  for (let i = 0; i < dims && i < vector.length; i++) { out[i] = vector[i]; norm += vector[i] * vector[i]; }
  norm = Math.sqrt(norm) || 1;
  for (let i = 0; i < dims; i++) out[i] /= norm;
  return out;
}

const MAX_TEXT = 6000;
function body(input: EmbedInput): unknown {
  if ('text' in input) return input.text.slice(0, MAX_TEXT);
  if ('image' in input) return { content: [{ type: 'image_url', image_url: { url: `data:${input.mime};base64,${input.image.toString('base64')}` } }] };
  return { content: [{ type: 'input_audio', input_audio: { data: input.audio.toString('base64'), format: input.format } }] };
}

export class EmbeddingRuntime implements Embedder {
  config: Pick<Config, 'embedding_bin' | 'embedding_model' | 'embedding_mmproj' | 'embedding_url' | 'embedding_dims' | 'embedding_context' |
    'embedding_idle_ms' | 'embedding_start_timeout_ms'>;
  /** false: never spawn (tests, or a server started elsewhere). */
  autoStart: boolean;
  child?: ChildProcess;
  private starting?: Promise<void>;
  private idle?: NodeJS.Timeout;
  private users = 0;
  constructor(config: EmbeddingRuntime['config'], autoStart = true) { this.config = config; this.autoStart = autoStart; }
  get multimodal(): boolean { return Boolean(this.config.embedding_mmproj); }
  private url(route: string): string { return new URL(route, this.config.embedding_url).href; }
  async healthy(signal?: AbortSignal): Promise<boolean> {
    try {
      const response = await fetch(this.url('/health'), { signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(2000)]) : AbortSignal.timeout(2000), redirect: 'error' });
      const value = await response.json() as { status?: string };
      return response.ok && value.status === 'ok';
    } catch { return false; }
  }
  private async ensure(signal: AbortSignal): Promise<void> {
    if (await this.healthy(signal)) return;
    if (!this.autoStart) throw Error('Embedding server unavailable');
    this.starting ??= this.start(signal).finally(() => { this.starting = undefined; });
    await this.starting;
  }
  private async start(signal: AbortSignal): Promise<void> {
    const c = this.config;
    if (this.child && this.child.exitCode === null && this.child.signalCode === null) await this.close();
    await access(c.embedding_bin, constants.X_OK); await access(c.embedding_model);
    if (c.embedding_mmproj) await access(c.embedding_mmproj);
    await mkdir(path.join(root, 'data'), { recursive: true, mode: 0o700 });
    const log = await open(path.join(root, 'data/embedding.log'), 'a', 0o600);
    const n = String(c.embedding_context);
    // Bidirectional attention: the micro-batch must hold the longest input, so -c, -b and -ub move together.
    const args = ['-m', c.embedding_model, ...(c.embedding_mmproj ? ['--mmproj', c.embedding_mmproj] : []), '--embeddings', '--pooling', 'mean',
      '-c', n, '-b', n, '-ub', n, '-ngl', '999', '--parallel', '1', '--no-webui', '--host', '127.0.0.1', '--port', new URL(c.embedding_url).port];
    try { this.child = spawn(c.embedding_bin, args, { stdio: ['ignore', log.fd, log.fd] }); }
    finally { await log.close(); }
    // Background work: below the chat model and the user's apps.
    try { if (this.child.pid) setPriority(this.child.pid, 10); } catch {}
    let launchError: Error | undefined;
    this.child.once('error', error => { launchError = error; });
    const deadline = AbortSignal.any([signal, AbortSignal.timeout(c.embedding_start_timeout_ms)]);
    try {
      while (true) {
        deadline.throwIfAborted();
        if (launchError || this.child.exitCode !== null || this.child.signalCode !== null)
          throw Error('Embedding server exited during startup; see data/embedding.log');
        if (await this.healthy(deadline)) return;
        await delay(250, undefined, { signal: deadline });
      }
    } catch (error) { await this.close(); throw error; }
  }
  async embed(inputs: EmbedInput[], signal: AbortSignal): Promise<Float32Array[]> {
    if (!inputs.length) return [];
    if (!this.multimodal && inputs.some(input => !('text' in input))) throw Error('Image/audio embeddings need the projector (embedding_mmproj)');
    this.users++; if (this.idle) { clearTimeout(this.idle); this.idle = undefined; }
    try {
      await this.ensure(signal);
      const response = await fetch(this.url('/v1/embeddings'), {
        method: 'POST', signal: AbortSignal.any([signal, AbortSignal.timeout(300_000)]), redirect: 'error',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ input: inputs.map(body), encoding_format: 'float' })
      });
      if (!response.ok) throw Error(`Embedding request failed (${response.status})`);
      const value = await response.json() as { data?: { index?: number; embedding?: number[] }[] };
      const data = value.data ?? [];
      if (data.length !== inputs.length) throw Error('Embedding response has the wrong length');
      const result: Float32Array[] = new Array(inputs.length);
      for (const [i, item] of data.entries()) {
        const index = Number.isInteger(item.index) ? item.index! : i;
        if (!Array.isArray(item.embedding) || item.embedding.length < this.config.embedding_dims || index < 0 || index >= inputs.length)
          throw Error('Embedding response is invalid');
        result[index] = truncate(item.embedding, this.config.embedding_dims);
      }
      return result;
    } finally {
      if (--this.users === 0 && this.autoStart && this.child) {
        this.idle = setTimeout(() => { void this.close(); }, this.config.embedding_idle_ms);
        this.idle.unref();
      }
    }
  }
  processes(): OwnedProcess[] { return [ownedProcess('embedding-server', this.child, Number(new URL(this.config.embedding_url).port))]; }
  async close(): Promise<void> {
    if (this.idle) { clearTimeout(this.idle); this.idle = undefined; }
    await stopProcess(this.child); this.child = undefined;
  }
}
