import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import path from 'node:path';
import type { Config } from './config.ts';
import { activity, observeRequest } from './activity.ts';
import { stopProcess } from './processes.ts';
import { RemindersError, bridgeReminder, clean, parse, type RemindersTransport } from '../.pi/extensions/apple-reminders/client.ts';
import type { BridgeReminder } from '../.pi/extensions/apple-reminders/schemas.ts';

/**
 * Assistant tasks: open reminders in one Reminders list (Admin: "Assistant tasks" + list name) are run as automated
 * requests. Undated items run when seen, dated ones once due; items missed while the app was closed run on the next scan.
 * Success: a short dated result is appended to the reminder's notes and it is completed. Failure: an error line is
 * appended, it stays open and is retried at most once. State holds hashes only (no reminder text).
 */
export const SCAN_MS = 60_000, RETRY_MS = 5 * 60_000, RESULT_CHARS = 1500, NOTES_CHARS = 4000;
const MARK = '— Asistan ';
const KEEP_DAYS = 45;

/** One slot of the backend's request lane: tasks never interrupt a user request and wait for their turn. */
export interface TaskLane { acquire(signal: AbortSignal): Promise<{ signal: AbortSignal; release(): void }>; }
export type TaskRun = (prompt: string, intentText: string, signal: AbortSignal) => Promise<{ text: string }>;
export type Notify = (title: string, body: string) => Promise<void>;
type Entry = { attempts: number; at: number; done?: true };

const hash = (value: string, length: number) => createHash('sha256').update(value).digest('hex').slice(0, length);
const pad = (n: number) => String(n).padStart(2, '0');
const stamp = (time: number) => { const d = new Date(time); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`; };

/** The user's own note text: everything before the first appended result block. */
export function userNotes(notes: string): string { const at = notes.indexOf(MARK); return (at < 0 ? notes : notes.slice(0, at)).trim(); }

/** Appends one dated block; the oldest result blocks are dropped so the note stays within NOTES_CHARS. */
export function appendResult(notes: string, time: number, text: string): string {
  const own = userNotes(notes), at = notes.indexOf(MARK);
  const blocks = at < 0 ? [] : notes.slice(at).split(new RegExp(`(?=${MARK})`)).map(b => b.trim()).filter(Boolean);
  const body = text.trim().length > RESULT_CHARS ? text.trim().slice(0, RESULT_CHARS - 1).trimEnd() + '…' : text.trim();
  blocks.push(`${MARK}${stamp(time)} —\n${body}`);
  const join = () => [own, ...blocks].filter(Boolean).join('\n\n');
  // The user's own text is never shortened; only older result blocks are dropped.
  while (blocks.length > 1 && join().length > NOTES_CHARS) blocks.shift();
  return join();
}

export function taskPrompt(item: BridgeReminder): { prompt: string; intentText: string } {
  const title = clean(item.title, 300), notes = userNotes(item.notes).slice(0, 1000);
  const intentText = title + (notes ? '\n' + notes : '');
  return { intentText, prompt: 'Automated task from the user\'s Reminders list; the user is not present, so do not ask questions or wait for a reply. ' +
    'Do it with the available tools, then reply with a short result (a few sentences).\nTask: ' + title + (notes ? '\nNotes: ' + notes : '') };
}

/** macOS notification via osascript; title and body are argv data, never script text. */
export const osascriptNotify: Notify = async (title, body) => {
  const child = spawn('/usr/bin/osascript', ['-e', 'on run argv', '-e', 'display notification (item 2 of argv) with title (item 1 of argv)', '-e', 'end run',
    title, body], { stdio: 'ignore', shell: false, env: { PATH: '/usr/bin:/bin' } });
  const timer = setTimeout(() => { void stopProcess(child); }, 5000);
  await new Promise<void>(resolve => { child.once('close', () => resolve()); child.once('error', () => resolve()); });
  clearTimeout(timer);
};

export class TaskScheduler {
  private readonly config: Config; private readonly transport: RemindersTransport; private readonly lane: TaskLane;
  private readonly run: TaskRun; private readonly notify: Notify; private readonly stateFile: string | null;
  private readonly activityFile: string | null; private readonly now: () => number;
  private state: Record<string, Entry> | undefined;
  private scanning: Promise<number> | undefined;
  private timer: NodeJS.Timeout | undefined;
  private lastError = '';
  constructor(options: { config: Config; transport: RemindersTransport; lane: TaskLane; run: TaskRun; notify?: Notify;
    stateFile: string | null; activityFile: string | null; now?: () => number }) {
    this.config = options.config; this.transport = options.transport; this.lane = options.lane; this.run = options.run;
    this.notify = options.notify ?? osascriptNotify; this.stateFile = options.stateFile; this.activityFile = options.activityFile;
    this.now = options.now ?? (() => Date.now());
  }

  /** First scan shortly after startup (missed items), then every SCAN_MS; stops with `signal`. */
  start(signal: AbortSignal, firstMs = 5000, everyMs = SCAN_MS): void {
    const tick = (delay: number) => {
      this.timer = setTimeout(async () => {
        await this.scan(signal).catch(() => 0);
        if (!signal.aborted) tick(everyMs);
      }, delay);
      this.timer.unref();
    };
    signal.addEventListener('abort', () => clearTimeout(this.timer), { once: true });
    if (!signal.aborted) tick(firstMs);
  }

  /** One pass over the list; returns how many tasks ran. Off (tasks or Reminders disabled): nothing is read. */
  scan(signal: AbortSignal): Promise<number> {
    return this.scanning ??= this.pass(signal).finally(() => { this.scanning = undefined; });
  }

  private observe<T>(details: Record<string, string | number | boolean>, action: () => Promise<T>): Promise<T> {
    return this.activityFile ? observeRequest(this.activityFile, details, action) : action();
  }

  private async pass(signal: AbortSignal): Promise<number> {
    if (!this.config.reminders_enabled || !this.config.tasks_enabled) return 0;
    let items: BridgeReminder[];
    try {
      const value = parse(await this.transport.request({ op: 'list', list: this.config.tasks_list, status: 'open',
        due_before: new Date(this.now()).toISOString(), undated: true, max: 20, notes_max: 8000 }, signal));
      if (!Array.isArray(value.reminders)) throw new RemindersError('invalid_response');
      items = value.reminders.map(bridgeReminder);
      this.lastError = '';
    } catch (error) {
      if (signal.aborted) return 0;
      const code = error instanceof RemindersError ? error.code : 'reminders_unavailable';
      // Logged once per new error (e.g. missing list), not every minute.
      if (code !== this.lastError) { this.lastError = code; await this.observe({ kind: 'task_scan' }, async () => activity('task_scan_failed', { error_code: code })); }
      return 0;
    }
    const state = await this.load();
    let ran = 0;
    for (const item of items) {
      if (signal.aborted || !this.config.tasks_enabled || !this.config.reminders_enabled) break;
      const key = hash(item.id + '|' + (item.due ?? ''), 16), entry = state[key];
      if (entry?.done || (entry && (entry.attempts >= 2 || this.now() - entry.at < RETRY_MS))) continue;
      if (await this.execute(item, key, entry?.attempts ?? 0, signal)) ran++;
    }
    return ran;
  }

  private async execute(item: BridgeReminder, key: string, attempts: number, outer: AbortSignal): Promise<boolean> {
    const slot = await this.lane.acquire(outer).catch(() => undefined);
    if (!slot) return false;
    const ref = hash(item.id, 6), title = clean(item.title, 60) || 'Görev';
    try {
      return await this.observe({ kind: 'task' }, async () => {
        const started = this.now();
        activity('task_started', { task: ref, attempt: attempts + 1 });
        const { prompt, intentText } = taskPrompt(item);
        let text = '', failure = '';
        try { text = (await this.run(prompt, intentText, slot.signal)).text.trim(); if (!text) failure = 'No usable reply'; }
        catch (error) {
          if (slot.signal.aborted) { activity('task_finished', { task: ref, success: false, cancelled: true, duration_ms: this.now() - started }); return false; }
          failure = clean(error instanceof Error ? error.message : 'Failed', 200) || 'Failed';
        }
        const state = await this.load(), finalTry = attempts + 1 >= 2;
        state[key] = { attempts: attempts + 1, at: this.now(), ...(failure ? {} : { done: true as const }) };
        await this.save();
        const notes = appendResult(item.notes, this.now(), failure ? `Hata / Error: ${failure}${finalTry ? ' (tekrar denenmeyecek / not retried)' : ''}` : text);
        // Notes read back truncated (bridge limit) are not written back: that would cut the user's text.
        let written = false;
        try {
          if (item.notes.length < 8000) { await this.call({ op: 'update', id: item.id, expected_title: item.title, notes }, slot.signal); written = true; }
          if (!failure) await this.call({ op: 'complete', id: item.id, expected_title: item.title }, slot.signal);
        } catch {}
        activity('task_finished', { task: ref, success: !failure, duration_ms: this.now() - started, note_written: written });
        await this.notify('Asistan: ' + title, clean(failure ? 'Hata / Error: ' + failure : text, 140)).catch(() => {});
        return true;
      });
    } finally { slot.release(); }
  }

  private async call(request: Parameters<RemindersTransport['request']>[0], signal: AbortSignal) { return parse(await this.transport.request(request, signal)); }

  private async load(): Promise<Record<string, Entry>> {
    if (this.state) return this.state;
    let value: unknown = {};
    if (this.stateFile) try { value = JSON.parse(await readFile(this.stateFile, 'utf8')); } catch {}
    const state: Record<string, Entry> = {};
    if (value && typeof value === 'object' && !Array.isArray(value))
      for (const [key, entry] of Object.entries(value as Record<string, Entry>))
        if (/^[0-9a-f]{16}$/.test(key) && entry && Number.isInteger(entry.attempts) && Number.isFinite(entry.at))
          state[key] = { attempts: entry.attempts, at: entry.at, ...(entry.done ? { done: true as const } : {}) };
    return this.state = state;
  }

  private async save(): Promise<void> {
    const state = await this.load(), cutoff = this.now() - KEEP_DAYS * 86_400_000;
    for (const [key, entry] of Object.entries(state)) if (entry.at < cutoff) delete state[key];
    if (!this.stateFile) return;
    await mkdir(path.dirname(this.stateFile), { recursive: true, mode: 0o700 });
    const temporary = this.stateFile + '.tmp';
    await writeFile(temporary, JSON.stringify(state) + '\n', { mode: 0o600 });
    await rename(temporary, this.stateFile);
  }
}
