import { AsyncLocalStorage } from 'node:async_hooks';
import { appendFile, mkdir, stat, rename, chmod } from 'node:fs/promises';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { performance } from 'node:perf_hooks';

type RequestActivity = { id: string; start: number; file: string };
const current = new AsyncLocalStorage<RequestActivity>();
let pending = Promise.resolve();

/** Metadata only: no prompts, replies, tool arguments, credentials or reasoning. */
export function activity(event: string, details: Record<string, string | number | boolean> = {}): void {
  const request = current.getStore(); if (!request) return;
  const line = JSON.stringify({ time: new Date().toISOString(), request: request.id,
    elapsed_ms: Math.round(performance.now() - request.start), event, ...details }) + '\n';
  pending = pending.then(async () => {
    await mkdir(path.dirname(request.file), { recursive: true, mode: 0o700 });
    try {
      if ((await stat(request.file)).size > 2 * 1024 * 1024) await rename(request.file, request.file + '.previous');
    } catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
    await appendFile(request.file, line, { mode: 0o600 });
    await chmod(request.file, 0o600);
  }).catch(() => { console.error('Activity log could not be written'); });
}

export async function observeRequest<T>(file: string, details: Record<string, string | number | boolean>, action: () => Promise<T>): Promise<T> {
  return current.run({ id: randomUUID().slice(0, 8), start: performance.now(), file }, async () => {
    activity('request_started', details);
    try { const result = await action(); activity('request_finished', { success: true }); return result; }
    catch (error) { activity('request_finished', { success: false, error_type: error instanceof Error ? error.name : 'Unknown' }); throw error; }
    finally { await pending; }
  });
}
