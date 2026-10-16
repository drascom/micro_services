import { spawn } from 'node:child_process';
import type { Config } from './config.ts';
import { activity, observeRequest } from './activity.ts';
import { stopProcess } from './processes.ts';
import { RemindersError, bridgeReminder, clean, parse, type RemindersTransport } from '../.pi/extensions/apple-reminders/client.ts';
import type { BridgeReminder } from '../.pi/extensions/apple-reminders/schemas.ts';
import { ConfirmationStore } from '../.pi/extensions/apple-reminders/confirmations.ts';
import { ScheduleError, describe, firstRun, parseSchedule, when, type Schedule } from './schedule.ts';
import { NAME_CHARS, PREVIOUS_CHARS, PROMPT_CHARS, clip, type Claim, type Task, type TaskStore } from './task-store.ts';
import type { Announcement, AnnouncementSource } from './announce.ts';

/**
 * Assistant tasks (TASKS.md): own store (`data/tasks.sqlite`), deterministic schedules, a 60 s tick in the backend's
 * request lane (never during a user request; a user request cancels a running task, which runs again later).
 * Each run is a fresh automated request: no history, no memory, read-only tools, no task tools. "[SILENT]" = nothing
 * delivered; results and every error go to the idle announcement queue and a notification.
 */
export const TICK_MS = 60_000;
/** Results older than this are not spoken any more (kept in Admin). */
export const ANNOUNCE_MAX_AGE_MS = 12 * 3_600_000;
/** The same result of the same task is not repeated within this window. */
export const DEDUPE_MS = 24 * 3_600_000;
export const FAILURE_NUDGE = 3;
/** Transient failures (network after wake, timeouts, 5xx) are retried after 5, 15 and 30 min before they count as failed. */
export const RETRY_MS = [5, 15, 30].map(m => m * 60_000);
const TRANSIENT = /fetch failed|network|ECONN(RESET|REFUSED|ABORTED)|ENOTFOUND|EAI_AGAIN|ETIMEDOUT|ENETUNREACH|EHOSTUNREACH|EPIPE|socket hang up|timed? ?out|timeout|\b(502|503|504|429)\b|overloaded|rate.?limit/i;
export const transientError = (message: string) => TRANSIENT.test(message);
export const SILENT = '[SILENT]';

/** A request to give, list, change or remove an assistant task ("sana bir görev vermek istiyorum", "her sabah 9'da … yap",
 *  "görev ekle/listele/sil"). Whatever the classifier picked, the orchestrator routes it to the tasks tool. */
export function taskRequest(text: string): boolean {
  return /(?<!\p{L})(görev|task)/iu.test(text) ||
    /(?<!\p{L})her\s+(sabah|akşam|gece|gün|hafta|ay|saat|pazartesi|salı|çarşamba|perşembe|cuma|cumartesi|pazar)(?!\p{L}).*(?<!\p{L})(yap|çalıştır|gönder|kontrol\s+et|özetle|bak|hazırla|oku)/iu.test(text);
}

/** One slot of the backend's request lane: tasks never interrupt a user request and wait for their turn. */
export interface TaskLane { acquire(signal: AbortSignal): Promise<{ signal: AbortSignal; release(): void }>; }
export type TaskRun = (prompt: string, intentText: string, signal: AbortSignal) => Promise<{ text: string }>;
export type Notify = (title: string, body: string) => Promise<void>;

/** macOS notification via osascript; title and body are argv data, never script text. Elsewhere: nothing (Linux: later). */
export const osascriptNotify: Notify = async (title, body) => {
  if (process.platform !== 'darwin') return;
  const child = spawn('/usr/bin/osascript', ['-e', 'on run argv', '-e', 'display notification (item 2 of argv) with title (item 1 of argv)', '-e', 'end run',
    title, body], { stdio: 'ignore', shell: false, env: { PATH: '/usr/bin:/bin' } });
  const timer = setTimeout(() => { void stopProcess(child); }, 5000);
  await new Promise<void>(resolve => { child.once('close', () => resolve()); child.once('error', () => resolve()); });
  clearTimeout(timer);
};

/** The unattended prompt: self-contained, the user is absent, nothing to say = [SILENT]. `previous`: the last result (≤300 chars), so only changes are told. */
export function runPrompt(task: Pick<Task, 'prompt'>, previous = ''): string {
  return 'Scheduled task; the user is not present: do not ask questions or wait for a reply. Read-only tools only; never send, change or delete anything. ' +
    `Do the task, then reply with a short result (a few sentences). If there is nothing new or worth telling, reply exactly ${SILENT}; do not invent work.\nTask: ` + task.prompt +
    (previous ? `\nPrevious result (tell only what changed since; unchanged = ${SILENT}): ` + clip(previous, PREVIOUS_CHARS) : '');
}
export const isSilent = (text: string) => text.includes(SILENT);

export class TaskScheduler implements AnnouncementSource {
  readonly store: TaskStore;
  private readonly config: Config; private readonly lane: TaskLane; private readonly run: TaskRun; private readonly notify: Notify;
  private readonly activityFile: string | null; readonly now: () => number;
  private readonly migrate?: (signal: AbortSignal) => Promise<unknown>;
  private passing: Promise<number> | undefined;
  private manual: number[] = [];
  private timer: NodeJS.Timeout | undefined;
  private recovered = false;
  private lifecycle?: AbortSignal;
  constructor(options: { config: Config; store: TaskStore; lane: TaskLane; run: TaskRun; notify?: Notify; activityFile: string | null; now?: () => number;
    migrate?: (signal: AbortSignal) => Promise<unknown> }) {
    this.config = options.config; this.store = options.store; this.lane = options.lane; this.run = options.run;
    this.notify = options.notify ?? osascriptNotify; this.activityFile = options.activityFile; this.now = options.now ?? (() => Date.now());
    this.migrate = options.migrate;
  }

  /** First tick shortly after startup (catch-up), then every `everyMs`; a late timer (sleep/wake) ticks at once. */
  start(signal: AbortSignal, firstMs = 5000, everyMs = TICK_MS): void {
    this.lifecycle = signal;
    const tick = (delay: number) => {
      this.timer = setTimeout(async () => {
        await this.tick(signal).catch(() => 0);
        if (!signal.aborted) tick(everyMs);
      }, delay);
      this.timer.unref();
    };
    signal.addEventListener('abort', () => clearTimeout(this.timer), { once: true });
    if (!signal.aborted) tick(firstMs);
  }

  /** Runs every due task (and queued run-nows) once; returns how many ran. Off (`tasks_enabled` false): nothing runs. */
  tick(signal: AbortSignal): Promise<number> {
    return this.passing ??= this.pass(signal).finally(() => { this.passing = undefined; });
  }

  /** Run now (Admin/tool): queued, runs in the lane after the current request; the schedule is unchanged. */
  runNow(id: number): boolean {
    if (!this.store.get(id)) return false;
    if (!this.manual.includes(id)) this.manual.push(id);
    if (this.lifecycle && !this.lifecycle.aborted) void this.tick(this.lifecycle).catch(() => 0);
    return true;
  }

  private observe<T>(details: Record<string, string | number | boolean>, action: () => Promise<T>): Promise<T> {
    return this.activityFile ? observeRequest(this.activityFile, details, action) : action();
  }

  private async pass(signal: AbortSignal): Promise<number> {
    if (!this.config.tasks_enabled) return 0;
    if (!this.recovered) { this.recovered = true; this.store.recover(this.now()); }
    if (this.migrate) await this.migrate(signal).catch(() => undefined);
    let ran = 0;
    while (!signal.aborted && this.config.tasks_enabled) {
      const manual = this.manual.shift();
      const task = manual !== undefined ? this.store.get(manual) : this.store.due(this.now());
      if (manual !== undefined && !task) continue;
      if (!task) break;
      const claim = this.store.claim(task, this.now(), manual !== undefined);
      if (!claim) continue; // that slot already ran (ledger)
      if (await this.execute(claim, signal)) ran++;
    }
    return ran;
  }

  private async execute(claim: Claim, outer: AbortSignal): Promise<boolean> {
    const slot = await this.lane.acquire(outer).catch(() => undefined);
    if (!slot) { this.store.cancel(claim, this.now()); return false; }
    const task = claim.task;
    try {
      return await this.observe({ kind: 'task' }, async () => {
        const started = this.now();
        activity('task_started', { task: task.id, attempt: claim.retry ? task.retry_count + 1 : 1 });
        let text = '', failure = '';
        try { text = (await this.run(runPrompt(task, this.store.previous(task.id)), task.prompt, slot.signal)).text.trim(); if (!text) failure = 'No usable reply'; }
        catch (error) {
          if (slot.signal.aborted) {
            this.store.cancel(claim, this.now());
            activity('task_finished', { task: task.id, success: false, cancelled: true, duration_ms: this.now() - started });
            return false;
          }
          failure = clean(error instanceof Error ? error.message : 'Failed', 200) || 'Failed';
        }
        // Transient (network after wake, timeout): retried after 5/15/30 min; only the last attempt counts as failed.
        const retries = this.store.get(task.id)?.retry_count ?? 0;
        if (failure && transientError(failure) && retries < RETRY_MS.length) {
          this.store.retryLater(claim, failure, this.now() + RETRY_MS[retries], this.now());
          activity('task_finished', { task: task.id, success: false, retry: retries + 1, duration_ms: this.now() - started });
          return true;
        }
        const status = failure ? 'failed' : isSilent(text) ? 'silent' : 'ok';
        // A failure is stored as its spoken text (with the pause suggestion from the third failure in a row).
        const result = failure ? failureText(task.name, failure, (this.store.get(task.id)?.failure_streak ?? 0) + 1) : text;
        this.store.finish(claim, status, result, this.now());
        activity('task_finished', { task: task.id, success: !failure, silent: status === 'silent', duration_ms: this.now() - started });
        if (status !== 'silent') await this.notify('Asistan: ' + clip(task.name, 60), clean(result, 140)).catch(() => {});
        return true;
      });
    } finally { slot.release(); }
  }

  /** Idle announcements: errors first (priority 0), results after update results (priority 2). Old ones are dropped from speech. */
  async announcements(): Promise<Announcement[]> {
    const now = this.now();
    return this.store.pending(now - ANNOUNCE_MAX_AGE_MS, now, DEDUPE_MS).map(run => ({ id: 'task-' + run.id, priority: run.status === 'failed' ? 0 : 2,
      text: run.status === 'failed' ? run.result : `${clip(run.name, 60)}: ${clip(run.result, 400)}` }));
  }
  async shown(id: string): Promise<boolean> {
    const m = /^task-(\d+)$/.exec(id);
    return m ? this.store.delivered(Number(m[1]), this.now()) : false;
  }
}

/** Errors are always delivered; from the third failure in a row a pause is suggested. */
export function failureText(name: string, error: string, streak: number): string {
  return `“${clip(name, 60)}” görevi başarısız: ${clip(error, 160)}` +
    (streak >= FAILURE_NUDGE ? ` ${streak} kez üst üste başarısız oldu; duraklatayım mı? (“${clip(name, 60)} görevini durdur”)` : '');
}

/** Pending task creations: the parsed schedule is said back and saved only after the user's next reply agrees. */
export const taskConfirmations = new ConfirmationStore();

export type TaskView = { id: number; name: string; schedule: string; next: string; status: string; last?: string; last_status?: string; last_run?: string; failures?: number };
export type TaskInput = { name?: string; prompt?: string; schedule?: string };
export type Preview = { name: string; prompt: string; schedule_text: string; schedule: Schedule; next_run_at: number | null; text: string };

/** What the `tasks` tool and Admin use. Model-facing texts stay short. */
export class TaskService {
  readonly store: TaskStore; readonly scheduler?: TaskScheduler; readonly now: () => number;
  constructor(store: TaskStore, scheduler?: TaskScheduler, now: () => number = () => Date.now()) { this.store = store; this.scheduler = scheduler; this.now = now; }

  view(task: Task): TaskView {
    const now = this.now();
    return { id: task.id, name: task.name, schedule: task.schedule.kind === 'once' ? 'Bir kez' : describe(task.schedule),
      next: task.status === 'active' ? when(task.next_run_at, now) : task.status === 'paused' ? 'duraklatıldı' : 'bitti', status: task.status,
      ...(task.last_status ? { last_status: task.last_status, last: clip(task.last_result, 160), last_run: when(task.last_run_at, now) } : {}),
      ...(task.failure_streak ? { failures: task.failure_streak } : {}) };
  }
  list(): TaskView[] { return this.store.list().map(task => this.view(task)); }

  /** Parses and checks a new task; throws ScheduleError / Error with a short reason for the model. */
  preview(input: TaskInput): Preview {
    const prompt = String(input.prompt ?? '').trim(), schedule_text = String(input.schedule ?? '').trim();
    if (!prompt) throw Error('prompt is required: what to do, self-contained.');
    if (prompt.length > PROMPT_CHARS) throw Error(`prompt is longer than ${PROMPT_CHARS} characters.`);
    const now = this.now(), schedule = parseSchedule(schedule_text, now), next_run_at = firstRun(schedule, now);
    const name = clip(String(input.name ?? '').trim() || prompt, NAME_CHARS);
    if (this.store.list().some(t => t.status !== 'done' && t.name.toLocaleLowerCase('tr') === name.toLocaleLowerCase('tr'))) throw Error('name_taken: pick another name.');
    const text = schedule.kind === 'once' ? `Bir kez, ${when(next_run_at, now)}` : `${describe(schedule)}, sonraki ${when(next_run_at, now)}`;
    return { name, prompt, schedule_text, schedule, next_run_at, text };
  }
  create(preview: Preview, source = 'chat'): Task {
    return this.store.add({ ...preview, status: 'active', source }, this.now());
  }

  /** Name (exact, then unique part) or numeric id. */
  find(ref: unknown): Task | { error: 'not_found' | 'ambiguous'; candidates?: string[] } {
    const text = String(ref ?? '').trim();
    if (!text) return { error: 'not_found' };
    const tasks = this.store.list(), key = text.toLocaleLowerCase('tr');
    if (/^#?\d+$/.test(text)) { const t = this.store.get(Number(text.replace('#', ''))); if (t) return t; }
    const exact = tasks.filter(t => t.name.toLocaleLowerCase('tr') === key);
    if (exact.length === 1) return exact[0];
    const part = tasks.filter(t => t.name.toLocaleLowerCase('tr').includes(key) || key.includes(t.name.toLocaleLowerCase('tr')));
    if (part.length === 1) return part[0];
    return part.length ? { error: 'ambiguous', candidates: part.slice(0, 5).map(t => t.name) } : { error: 'not_found' };
  }

  update(task: Task, input: TaskInput): Task {
    const now = this.now(), change: Parameters<TaskStore['update']>[1] = {};
    if (input.name?.trim()) change.name = input.name;
    if (input.prompt?.trim()) change.prompt = input.prompt;
    if (input.schedule?.trim()) {
      const schedule = parseSchedule(input.schedule, now);
      Object.assign(change, { schedule, schedule_text: input.schedule.trim(), next_run_at: firstRun(schedule, now), ...(task.status === 'done' ? { status: 'active' } : {}) });
    }
    return this.store.update(task.id, change, now)!;
  }
  pause(task: Task): Task { return this.store.update(task.id, { status: 'paused' }, this.now())!; }
  /** Resume re-arms from now (no stale fire); a one-time task whose time passed runs now. */
  resume(task: Task): Task {
    const now = this.now();
    const next = task.schedule.kind === 'once' ? Math.max(task.schedule.at, now) : firstRun(task.schedule, now);
    return this.store.update(task.id, { status: 'active', next_run_at: next, failure_streak: 0 }, now)!;
  }
  remove(task: Task): boolean { return this.store.remove(task.id); }
  runNow(task: Task): boolean { return this.scheduler?.runNow(task.id) ?? false; }
}

let registered: TaskService | undefined;
/** The running backend's task service (the `tasks` tool reads it); undefined in tests and CLI. */
export function registerTasks(service: TaskService | undefined): void { registered = service; }
export function registeredTasks(): TaskService | undefined { return registered; }

// ——— One-time migration of the old Reminders-based tasks (P4) ———
const PREFIX = /^(?:\s*(?:⏳|✅|⚠️?)\s*)+/u;
const MARK = '— Asistan ';
export const baseTitle = (title: string) => title.replace(PREFIX, '');
const userNotes = (notes: string) => { const at = notes.indexOf(MARK); return (at < 0 ? notes : notes.slice(0, at)).trim(); };
export const MIGRATED_KEY = 'reminders_migrated';

/**
 * Copies the open items of the Reminders tasks list into the store once (read-only: the reminders are never changed).
 * A future dated item becomes an active one-time task; undated, overdue, running or failed items are added paused
 * (the user resumes them). ✅ and 📧 items are skipped. Marked done in `meta` even when nothing was copied; a
 * Reminders error leaves it for the next start.
 */
export async function migrateReminders(options: { store: TaskStore; transport: RemindersTransport; config: Config; signal: AbortSignal; now: number }): Promise<number | undefined> {
  const { store, config } = options;
  if (store.meta(MIGRATED_KEY) || !config.reminders_enabled) return undefined;
  let items: BridgeReminder[];
  try {
    const value = parse(await options.transport.request({ op: 'list', list: config.tasks_list, status: 'open', undated: true, max: 50, notes_max: 2000,
      skip_prefixes: ['✅', '📧'] }, options.signal));
    if (!Array.isArray(value.reminders)) throw new RemindersError('invalid_response');
    items = value.reminders.map(bridgeReminder);
  } catch (error) {
    // Missing list = nothing to migrate; anything else is retried on the next start.
    if (error instanceof RemindersError && error.code === 'list_not_found') { store.setMeta(MIGRATED_KEY, JSON.stringify({ at: options.now, count: 0 })); return 0; }
    return undefined;
  }
  let count = 0;
  for (const item of items) {
    if (item.completed || /^\s*(✅|📧)/u.test(item.title)) continue;
    const title = clean(baseTitle(item.title), 300), notes = userNotes(item.notes).slice(0, 700);
    if (!title) continue;
    const due = item.due ? Date.parse(item.due) : NaN, future = Number.isFinite(due) && due > options.now && !/^\s*[⏳⚠]/u.test(item.title);
    const at = Number.isFinite(due) ? due : options.now;
    store.add({ name: title, prompt: title + (notes ? '\n' + notes : ''), schedule_text: 'Anımsatıcılardan', schedule: { kind: 'once', at },
      next_run_at: future ? due : at, status: future ? 'active' : 'paused', source: 'reminders' }, options.now);
    count++;
  }
  store.setMeta(MIGRATED_KEY, JSON.stringify({ at: options.now, count }));
  activity('tasks_migrated', { count });
  return count;
}
export { ScheduleError };
