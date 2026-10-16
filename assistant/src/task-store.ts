import { DatabaseSync } from 'node:sqlite';
import { chmodSync, mkdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { decode, encode, nextRun, type Schedule } from './schedule.ts';

/**
 * Assistant task store (`data/tasks.sqlite`, see TASKS.md): tasks + a run ledger. A run row is UNIQUE per
 * (task, scheduled slot), so one slot never runs twice; `next_run_at` is advanced in the same transaction that claims
 * the slot (at most once). Plain node:sqlite, no macOS dependency.
 */
export type TaskStatus = 'active' | 'paused' | 'done';
/** `retry`: a transient failure waiting for its retry (5/15/30 min, `RETRY_MS` in tasks.ts); not delivered. */
export type RunStatus = 'running' | 'ok' | 'silent' | 'failed' | 'cancelled' | 'retry';
export type Task = { id: number; name: string; prompt: string; schedule_text: string; schedule: Schedule; next_run_at: number | null;
  status: TaskStatus; failure_streak: number; last_status: RunStatus | null; last_run_at: number | null; last_result: string; source: string;
  created: number; updated: number; retry_at: number | null; retry_count: number };
export type Run = { id: number; task_id: number; slot: number; status: RunStatus; result: string; started: number; finished: number | null;
  announce: number; delivered: number | null; attempt: number };
/** `retry`: this claim re-runs a transiently failed slot (same ledger row). */
export type Claim = { task: Task; run: number; slot: number; retry?: boolean };

export const NAME_CHARS = 60, PROMPT_CHARS = 1000, RESULT_CHARS = 1500, PREVIOUS_CHARS = 300;
const KEEP_RUNS = 20, KEEP_DAYS = 45;
export const hashText = (text: string) => createHash('sha256').update(text).digest('hex').slice(0, 16);
export const clip = (text: string, max: number) => { const t = text.replace(/\s+/g, ' ').trim(); return t.length > max ? t.slice(0, max - 1).trimEnd() + '…' : t; };

type Row = Record<string, string | number | null>;
const toTask = (r: Row): Task => ({ id: Number(r.id), name: String(r.name), prompt: String(r.prompt), schedule_text: String(r.schedule_text),
  schedule: decode(String(r.kind), String(r.spec)), next_run_at: r.next_run_at === null ? null : Number(r.next_run_at), status: r.status as TaskStatus,
  failure_streak: Number(r.failure_streak), last_status: (r.last_status ?? null) as RunStatus | null, last_run_at: r.last_run_at === null ? null : Number(r.last_run_at),
  last_result: String(r.last_result ?? ''), source: String(r.source), created: Number(r.created), updated: Number(r.updated),
  retry_at: r.retry_at === null || r.retry_at === undefined ? null : Number(r.retry_at), retry_count: Number(r.retry_count ?? 0) });

export class TaskStore {
  readonly file: string;
  private db?: DatabaseSync;
  constructor(file: string) { this.file = file; }

  open(): DatabaseSync {
    if (this.db) return this.db;
    if (this.file !== ':memory:') mkdirSync(path.dirname(this.file), { recursive: true, mode: 0o700 });
    const db = new DatabaseSync(this.file);
    db.exec(`PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;
      CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
      CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY, name TEXT NOT NULL, prompt TEXT NOT NULL, schedule_text TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('once','interval','cron')), spec TEXT NOT NULL, next_run_at REAL,
        status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','done')), failure_streak INTEGER NOT NULL DEFAULT 0,
        last_status TEXT, last_run_at REAL, last_result TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'chat',
        created REAL NOT NULL, updated REAL NOT NULL);
      CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        slot REAL NOT NULL, status TEXT NOT NULL, result TEXT NOT NULL DEFAULT '', result_hash TEXT, started REAL NOT NULL, finished REAL,
        announce INTEGER NOT NULL DEFAULT 0, delivered REAL, attempt INTEGER NOT NULL DEFAULT 1, UNIQUE (task_id, slot));`);
    // P6 retry ladder columns (added to databases created before it).
    const columns = new Set((db.prepare('PRAGMA table_info(tasks)').all() as { name: string }[]).map(c => c.name));
    if (!columns.has('retry_at')) db.exec('ALTER TABLE tasks ADD COLUMN retry_at REAL');
    if (!columns.has('retry_count')) db.exec('ALTER TABLE tasks ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0');
    if (this.file !== ':memory:') try { chmodSync(this.file, 0o600); } catch {}
    return this.db = db;
  }
  close(): void { this.db?.close(); this.db = undefined; }

  meta(key: string): string | undefined { return (this.open().prepare('SELECT value FROM meta WHERE key = ?').get(key) as { value?: string } | undefined)?.value; }
  setMeta(key: string, value: string): void { this.open().prepare('INSERT OR REPLACE INTO meta VALUES (?, ?)').run(key, value); }

  private tx<T>(action: () => T): T {
    const db = this.open(); db.exec('BEGIN IMMEDIATE');
    try { const result = action(); db.exec('COMMIT'); return result; } catch (error) { db.exec('ROLLBACK'); throw error; }
  }

  add(input: { name: string; prompt: string; schedule_text: string; schedule: Schedule; next_run_at: number | null; status?: TaskStatus; source?: string }, now: number): Task {
    const { kind, spec } = encode(input.schedule);
    const r = this.open().prepare(`INSERT INTO tasks (name, prompt, schedule_text, kind, spec, next_run_at, status, source, created, updated)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`).run(clip(input.name, NAME_CHARS), input.prompt.trim().slice(0, PROMPT_CHARS), input.schedule_text.slice(0, 120), kind, spec,
      input.next_run_at, input.status ?? 'active', input.source ?? 'chat', now, now);
    return this.get(Number(r.lastInsertRowid))!;
  }
  get(id: number): Task | undefined { const r = this.open().prepare('SELECT * FROM tasks WHERE id = ?').get(id) as Row | undefined; return r && toTask(r); }
  list(): Task[] { return (this.open().prepare("SELECT * FROM tasks ORDER BY status = 'done', next_run_at IS NULL, next_run_at, id").all() as Row[]).map(toTask); }
  update(id: number, change: Partial<{ name: string; prompt: string; schedule_text: string; schedule: Schedule; next_run_at: number | null; status: TaskStatus; failure_streak: number }>, now: number): Task | undefined {
    const sets: string[] = [], values: (string | number | null)[] = [];
    const set = (column: string, value: string | number | null) => { sets.push(column + ' = ?'); values.push(value); };
    if (change.name !== undefined) set('name', clip(change.name, NAME_CHARS));
    if (change.prompt !== undefined) set('prompt', change.prompt.trim().slice(0, PROMPT_CHARS));
    if (change.schedule_text !== undefined) set('schedule_text', change.schedule_text.slice(0, 120));
    if (change.schedule) { const { kind, spec } = encode(change.schedule); set('kind', kind); set('spec', spec); }
    if (change.next_run_at !== undefined) set('next_run_at', change.next_run_at);
    if (change.status !== undefined) set('status', change.status);
    if (change.failure_streak !== undefined) set('failure_streak', change.failure_streak);
    set('updated', now);
    this.open().prepare(`UPDATE tasks SET ${sets.join(', ')} WHERE id = ?`).run(...values, id);
    return this.get(id);
  }
  remove(id: number): boolean { return Number(this.open().prepare('DELETE FROM tasks WHERE id = ?').run(id).changes) > 0; }

  /** Earliest task due at `now`: an active slot or a waiting retry (a one-time task's retry runs after it is `done`; never while paused). */
  due(now: number): Task | undefined {
    const r = this.open().prepare(`SELECT * FROM tasks WHERE (status = 'active' AND next_run_at IS NOT NULL AND next_run_at <= ?)
      OR (status != 'paused' AND retry_at IS NOT NULL AND retry_at <= ?)
      ORDER BY CASE WHEN retry_at IS NOT NULL AND retry_at <= ? THEN retry_at ELSE next_run_at END, id LIMIT 1`).get(now, now, now) as Row | undefined;
    return r && toTask(r);
  }

  /**
   * Claims the task's due slot and advances `next_run_at` past `now` (missed slots collapse: catch-up once, then the
   * next regular slot). Returns undefined when that slot already has a run (never twice); a cancelled run is reused.
   * `manual`: a run-now at `now` that leaves the schedule alone.
   */
  claim(task: Task, now: number, manual = false): Claim | undefined {
    return this.tx(() => {
      const db0 = this.open();
      if (!manual && task.retry_at !== null && task.retry_at <= now) {
        // A waiting retry: the same ledger row runs again (attempt + 1).
        db0.prepare('UPDATE tasks SET retry_at = NULL, updated = ? WHERE id = ?').run(now, task.id);
        const row = db0.prepare(`UPDATE runs SET status = 'running', started = ?, finished = NULL, attempt = attempt + 1
          WHERE id = (SELECT id FROM runs WHERE task_id = ? AND status = 'retry' ORDER BY id DESC LIMIT 1) RETURNING id, slot`).get(now, task.id) as { id?: number; slot?: number } | undefined;
        if (row?.id) return { task: this.get(task.id)!, run: Number(row.id), slot: Number(row.slot), retry: true };
        db0.prepare('UPDATE tasks SET retry_count = 0 WHERE id = ?').run(task.id);
        return undefined;
      }
      const slot = manual ? now : task.next_run_at!;
      // A new slot supersedes a waiting retry (that failure stays in the ledger, not delivered; this run reports).
      if (task.retry_count) {
        db0.prepare("UPDATE runs SET status = 'failed', announce = 0 WHERE task_id = ? AND status = 'retry'").run(task.id);
        db0.prepare('UPDATE tasks SET retry_at = NULL, retry_count = 0 WHERE id = ?').run(task.id);
      }
      if (!manual) {
        const next = nextRun(task.schedule, now, slot);
        this.open().prepare('UPDATE tasks SET next_run_at = ?, status = ?, updated = ? WHERE id = ?').run(next, next === null ? 'done' : task.status, now, task.id);
      }
      const db = this.open();
      const inserted = db.prepare("INSERT OR IGNORE INTO runs (task_id, slot, status, started) VALUES (?, ?, 'running', ?)").run(task.id, slot, now);
      if (Number(inserted.changes)) return { task, run: Number(inserted.lastInsertRowid), slot };
      const again = db.prepare("UPDATE runs SET status = 'running', started = ?, finished = NULL, attempt = attempt + 1 WHERE task_id = ? AND slot = ? AND status = 'cancelled' RETURNING id")
        .get(now, task.id, slot) as { id?: number } | undefined;
      return again?.id ? { task, run: Number(again.id), slot } : undefined;
    });
  }

  /** A run the user's request (or shutdown) interrupted: the slot is re-armed and runs again later. */
  cancel(claim: Claim, now: number): void {
    this.tx(() => {
      const db = this.open();
      if (claim.retry) {
        // An interrupted retry waits again and runs at the next free moment.
        db.prepare("UPDATE runs SET status = 'retry', finished = ?, attempt = attempt - 1 WHERE id = ?").run(now, claim.run);
        db.prepare('UPDATE tasks SET retry_at = ?, updated = ? WHERE id = ?').run(now, now, claim.task.id);
        return;
      }
      db.prepare("UPDATE runs SET status = 'cancelled', finished = ? WHERE id = ?").run(now, claim.run);
      const task = this.get(claim.task.id);
      if (!task || task.status === 'paused') return;
      db.prepare("UPDATE tasks SET next_run_at = CASE WHEN next_run_at IS NULL OR next_run_at > ? THEN ? ELSE next_run_at END, status = 'active', updated = ? WHERE id = ?")
        .run(claim.slot, claim.slot, now, claim.task.id);
    });
  }

  /** Startup: a run left `running` (the app closed mid-run) is cancelled and its slot re-armed. */
  recover(now: number): number {
    const rows = this.open().prepare("SELECT r.id, r.task_id, r.slot FROM runs r WHERE r.status = 'running'").all() as { id: number; task_id: number; slot: number }[];
    for (const r of rows) { const task = this.get(Number(r.task_id)); if (task) this.cancel({ task, run: Number(r.id), slot: Number(r.slot), retry: task.retry_count > 0 }, now); }
    return rows.length;
  }

  /** Finished run: result kept (≤ RESULT_CHARS), streak updated; `announce` = deliver at an idle moment. */
  finish(claim: Claim, status: 'ok' | 'silent' | 'failed', result: string, now: number): Task | undefined {
    return this.tx(() => {
      const db = this.open(), text = result.trim().slice(0, RESULT_CHARS);
      db.prepare('UPDATE runs SET status = ?, result = ?, result_hash = ?, finished = ?, announce = ? WHERE id = ?')
        .run(status, text, hashText(text), now, status === 'silent' ? 0 : 1, claim.run);
      db.prepare(`UPDATE tasks SET last_status = ?, last_run_at = ?, last_result = ?, failure_streak = CASE WHEN ? = 'failed' THEN failure_streak + 1 ELSE 0 END,
        retry_at = NULL, retry_count = 0, updated = ? WHERE id = ?`).run(status, now, text, status, now, claim.task.id);
      this.prune(claim.task.id, now);
      return this.get(claim.task.id);
    });
  }
  /** Transient failure: the run waits (`retry`, not delivered) and the slot runs again at `at`. */
  retryLater(claim: Claim, error: string, at: number, now: number): Task | undefined {
    return this.tx(() => {
      const db = this.open(), text = error.trim().slice(0, RESULT_CHARS);
      db.prepare("UPDATE runs SET status = 'retry', result = ?, finished = ?, announce = 0 WHERE id = ?").run(text, now, claim.run);
      db.prepare('UPDATE tasks SET retry_at = ?, retry_count = retry_count + 1, updated = ? WHERE id = ?').run(at, now, claim.task.id);
      return this.get(claim.task.id);
    });
  }
  /** The task's latest delivered-kind result (`ok`), short: the next run reports only what changed. */
  previous(task: number): string {
    const r = this.open().prepare("SELECT result FROM runs WHERE task_id = ? AND status = 'ok' ORDER BY finished DESC, id DESC LIMIT 1").get(task) as { result?: string } | undefined;
    return r?.result ? clip(r.result, PREVIOUS_CHARS) : '';
  }
  /** Failed runs finished after `since` (heartbeat pre-check). */
  failedSince(since: number): { id: number; name: string }[] {
    return (this.open().prepare("SELECT r.id, t.name FROM runs r JOIN tasks t ON t.id = r.task_id WHERE r.status = 'failed' AND r.announce = 1 AND r.finished > ? ORDER BY r.id")
      .all(since) as { id: number; name: string }[]).map(r => ({ id: Number(r.id), name: String(r.name) }));
  }

  private prune(task: number, now: number): void {
    const db = this.open();
    db.prepare(`DELETE FROM runs WHERE task_id = ? AND status NOT IN ('running', 'retry') AND id NOT IN (SELECT id FROM runs WHERE task_id = ? ORDER BY id DESC LIMIT ${KEEP_RUNS})`).run(task, task);
    db.prepare("DELETE FROM runs WHERE status != 'running' AND finished < ?").run(now - KEEP_DAYS * 86_400_000);
  }

  /** Undelivered results finished after `since`; a result equal to one of the same task delivered within `dedupeMs` is dropped. */
  pending(since: number, now: number, dedupeMs: number): (Run & { name: string })[] {
    const db = this.open();
    const rows = db.prepare(`SELECT r.*, t.name FROM runs r JOIN tasks t ON t.id = r.task_id WHERE r.announce = 1 AND r.delivered IS NULL AND r.finished >= ?
      ORDER BY r.finished, r.id`).all(since) as (Row & { result_hash: string })[];
    const out: (Run & { name: string })[] = [];
    for (const r of rows) {
      const seen = db.prepare('SELECT 1 FROM runs WHERE task_id = ? AND id != ? AND result_hash = ? AND delivered >= ?').get(r.task_id, r.id, r.result_hash, now - dedupeMs);
      if (seen) { this.delivered(Number(r.id), now); continue; }
      out.push({ id: Number(r.id), task_id: Number(r.task_id), slot: Number(r.slot), status: r.status as RunStatus, result: String(r.result), started: Number(r.started),
        finished: Number(r.finished), announce: 1, delivered: null, attempt: Number(r.attempt), name: String(r.name) });
    }
    return out;
  }
  delivered(run: number, now: number): boolean {
    return Number(this.open().prepare('UPDATE runs SET delivered = ? WHERE id = ? AND announce = 1 AND delivered IS NULL').run(now, run).changes) > 0;
  }
  runs(task: number, limit = 5): Run[] {
    return (this.open().prepare('SELECT * FROM runs WHERE task_id = ? ORDER BY id DESC LIMIT ?').all(task, limit) as Row[]).map(r => ({ id: Number(r.id), task_id: Number(r.task_id),
      slot: Number(r.slot), status: r.status as RunStatus, result: String(r.result), started: Number(r.started), finished: r.finished === null ? null : Number(r.finished),
      announce: Number(r.announce), delivered: r.delivered === null ? null : Number(r.delivered), attempt: Number(r.attempt) }));
  }
}
