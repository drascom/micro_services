import { mkdir, open, readFile, rename, rm } from 'node:fs/promises';
import path from 'node:path';
import type { ModelFile, Restart, Staged, Target } from './components.ts';

/**
 * Crash-safe update journal (`data/updates/journal.json`): one run = a batch of applies. Every step is written before
 * and after it happens (tmp + fsync + rename + dir fsync), so a crash at any point leaves enough to finish or roll back
 * on the next start (updates.ts `recover`). Step names, ids and versions only; no paths outside the project, no secrets.
 */
export type Step = 'staging' | 'staged' | 'smoke_ok' | 'swapping' | 'swapped' | 'manifest_written' | 'rebuilt' | 'restart_pending'
  | 'verifying' | 'verified' | 'rolling_back' | 'rolled_back' | 'failed';
export const TERMINAL: readonly Step[] = ['verified', 'rolled_back', 'failed'];
export type JournalEntry = {
  id: string; name: string; kind: string; restart: Restart; from: string; to: string; target: Target;
  /** Manifest values before the apply (restored by a rollback). */
  prev: { version: string; sha256?: string; files?: ModelFile[] };
  staged?: Staged; status: Step; reason?: string; reported?: boolean; verify_attempts: number;
  steps: { step: Step; at: number; reason?: string }[];
};
export type Run = {
  id: string; started: number; finished?: number; done: boolean; phase: 'applying' | 'finishing' | 'awaiting_restart' | 'done';
  components: JournalEntry[];
  /** wanted: an app restart is due; requested: how many the backend asked for; rollback_restarts: guard (max 1). */
  restart: { wanted: boolean; requested: number; rollback_restarts: number };
  rebuild_error?: string; announce?: { id: string; text: string };
};
export type Journal = { schema: 1; run?: Run };

/** Atomic, durable write: tmp file fsync'd, renamed over the journal, directory fsync'd. */
export async function writeJournal(file: string, journal: Journal): Promise<void> {
  await mkdir(path.dirname(file), { recursive: true, mode: 0o700 });
  const temporary = `${file}.${process.pid}.tmp`;
  const handle = await open(temporary, 'w', 0o600);
  try { await handle.writeFile(JSON.stringify(journal, null, 1) + '\n'); await handle.sync(); } finally { await handle.close(); }
  await rename(temporary, file);
  try { const dir = await open(path.dirname(file), 'r'); try { await dir.sync(); } finally { await dir.close(); } } catch {}
}

/** Missing → empty; corrupt/partial → set aside as `journal.corrupt.json` and reported (`corrupt: true`). */
export async function readJournal(file: string): Promise<{ journal: Journal; corrupt: boolean }> {
  let text: string;
  try { text = await readFile(file, 'utf8'); } catch { return { journal: { schema: 1 }, corrupt: false }; }
  try {
    const value = JSON.parse(text) as Journal;
    if (!value || typeof value !== 'object' || value.schema !== 1) throw Error('schema');
    if (value.run && (!Array.isArray(value.run.components) || typeof value.run.id !== 'string')) throw Error('run');
    return { journal: value, corrupt: false };
  } catch {
    await rm(file.replace(/\.json$/, '.corrupt.json'), { force: true });
    await rename(file, file.replace(/\.json$/, '.corrupt.json')).catch(() => {});
    return { journal: { schema: 1 }, corrupt: true };
  }
}

export function mark(entry: JournalEntry, step: Step, at: number, reason?: string): void {
  entry.status = step;
  if (reason) entry.reason = reason.slice(0, 200);
  entry.steps.push({ step, at, ...(reason ? { reason: reason.slice(0, 200) } : {}) });
  if (entry.steps.length > 40) entry.steps.splice(0, entry.steps.length - 40);
}

/** Admin: the last run's steps, compact (completed steps only, no begin markers). */
export type RunView = { id: string; started: number; finished: number | null; phase: Run['phase'];
  components: { id: string; name: string; from: string; to: string; status: Step; steps: string[]; reason?: string }[] };
const BEGIN: Step[] = ['staging', 'swapping', 'verifying', 'rolling_back'];
export function runView(run: Run | undefined): RunView | null {
  if (!run) return null;
  return { id: run.id, started: run.started, finished: run.finished ?? null, phase: run.phase,
    components: run.components.map(e => ({ id: e.id, name: e.name, from: e.from, to: e.to, status: e.status,
      steps: e.steps.filter((s, i) => !BEGIN.includes(s.step) || i === e.steps.length - 1).map(s => s.step),
      ...(e.reason && (e.status === 'failed' || e.status === 'rolled_back') ? { reason: e.reason } : {}) })) };
}
