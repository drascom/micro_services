import path from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { root, type Config } from './config.ts';
import { EmbeddingRuntime, type Embedder } from './embeddings.ts';
import { FileIndex } from './files-index.ts';
import { observeRequest } from './activity.ts';
import type { OwnedProcess } from './processes.ts';

/**
 * Files feature wiring: the EmbeddingGemma server, the index and its background schedule. Off (`embedding_enabled`
 * false): no tools are registered, no server is started and no pass runs.
 */
export class FileService {
  readonly config: Config;
  readonly embedder: Embedder;
  readonly index: FileIndex;
  readonly logFile: string | null;
  readonly activityFile: string | null;
  /** Set by the HTTP server: true while a user request or task runs (indexing waits). */
  busy: () => boolean = () => false;
  private controller?: AbortController;
  constructor(config: Config, options: { embedder?: Embedder; dataDir?: string | null; activityFile?: string | null } = {}) {
    this.config = config;
    const data = options.dataDir === undefined ? path.join(root, 'data') : options.dataDir;
    this.embedder = options.embedder ?? new EmbeddingRuntime(config);
    this.index = new FileIndex(config, this.embedder, data ? path.join(data, 'files-index.sqlite') : ':memory:');
    this.logFile = data ? path.join(data, 'file-actions.jsonl') : null;
    this.activityFile = options.activityFile === undefined ? (data ? path.join(data, 'activity.log') : null) : options.activityFile;
  }
  get enabled(): boolean { return this.config.embedding_enabled; }
  /** One pass now (Admin "Reindex": `full` re-embeds everything). Counts/durations only reach the activity log. */
  async runOnce(signal: AbortSignal, full = false): Promise<void> {
    if (!this.enabled) return;
    const pass = () => this.index.run(signal, this.busy, full);
    await (this.activityFile ? observeRequest(this.activityFile, { kind: 'file_index', full }, pass) : pass());
  }
  /**
   * Background schedule: first pass after `firstMs`, then every `file_index_interval_ms` (skipped while off). A pass
   * that fails is retried after `retryMs`, doubling up to the interval; finished files are never redone.
   */
  start(lifecycle: AbortSignal, firstMs = 60_000, retryMs = 60_000): void {
    this.controller = new AbortController();
    const signal = AbortSignal.any([lifecycle, this.controller.signal]);
    void (async () => {
      try {
        await sleep(firstMs, undefined, { signal });
        for (let failures = 0; ;) {
          try { await this.runOnce(signal); failures = 0; } catch { if (signal.aborted) return; failures++; }
          const wait = failures ? Math.min(retryMs * 2 ** (failures - 1), this.config.file_index_interval_ms) : this.config.file_index_interval_ms;
          await sleep(wait, undefined, { signal });
        }
      } catch {}
    })();
    lifecycle.addEventListener('abort', () => { void this.close(); }, { once: true });
  }
  reindex(lifecycle: AbortSignal): { started: boolean } {
    if (!this.enabled) return { started: false };
    void this.runOnce(lifecycle, true).catch(() => {});
    return { started: true };
  }
  processes(): OwnedProcess[] { return this.embedder.processes?.() ?? []; }
  async close(): Promise<void> { this.controller?.abort(); await this.embedder.close(); this.index.close(); }
}

let shared: FileService | undefined;
/** One per backend process (the app config object is shared and live). */
export function fileService(config: Config): FileService {
  if (!shared || shared.config !== config) shared = new FileService(config);
  return shared;
}
