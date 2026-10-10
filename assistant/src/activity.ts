import { AsyncLocalStorage } from 'node:async_hooks';
import { appendFile, mkdir, stat, rename, chmod, open } from 'node:fs/promises';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { performance } from 'node:perf_hooks';

type RequestActivity = { id: string; start: number; file: string };
const current = new AsyncLocalStorage<RequestActivity>();
let pending = Promise.resolve();
/** In-memory stage of each running request, for the native admin status view. */
const live = new Map<string, { kind: string; stage: string; start: number }>();
export function liveRequests(): { request: string; kind: string; stage: string; elapsed_ms: number }[] {
  return [...live].map(([request, value]) => ({ request, kind: value.kind, stage: value.stage, elapsed_ms: Math.round(performance.now() - value.start) }));
}

/** Last `limit` activity entries, newest first, read from the bounded tail of the log. */
export async function recentActivity(file: string, limit = 100): Promise<Record<string, unknown>[]> {
  let handle;
  try { handle = await open(file, 'r'); }
  catch (error) { if ((error as NodeJS.ErrnoException).code === 'ENOENT') return []; throw error; }
  try {
    const size = (await handle.stat()).size, length = Math.min(size, 128 * 1024);
    const buffer = Buffer.alloc(length);
    await handle.read(buffer, 0, length, size - length);
    const lines = buffer.toString('utf8').split('\n').slice(size > length ? 1 : 0).filter(Boolean);
    const entries: Record<string, unknown>[] = [];
    for (const line of lines.slice(-limit).reverse()) { try { entries.push(JSON.parse(line)); } catch {} }
    return entries;
  } finally { await handle.close(); }
}

/** Metadata only: no prompts, replies, tool arguments, credentials or reasoning. */
export function activity(event: string, details: Record<string, string | number | boolean> = {}): void {
  const request = current.getStore(); if (!request) return;
  const state = live.get(request.id); if (state) state.stage = event;
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
  const id = randomUUID().slice(0, 8), start = performance.now();
  live.set(id, { kind: String(details.kind ?? ''), stage: 'request_started', start });
  return current.run({ id, start, file }, async () => {
    activity('request_started', details);
    try { const result = await action(); activity('request_finished', { success: true }); return result; }
    catch (error) { activity('request_finished', { success: false, error_type: error instanceof Error ? error.name : 'Unknown' }); throw error; }
    finally { live.delete(id); await pending; }
  });
}
