import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import path from 'node:path';
import type { Config } from './config.ts';
import { activity } from './activity.ts';
import { clip, hashText, PREVIOUS_CHARS, type TaskStore } from './task-store.ts';
import { SILENT, isSilent, osascriptNotify, type Notify, type TaskLane, type TaskRun } from './tasks.ts';
import { when } from './schedule.ts';
import type { Announcement, AnnouncementSource } from './announce.ts';
import type { Mailboxes } from '../.pi/extensions/gmail/service.ts';
import { parseFrom, urgentSender } from '../.pi/extensions/gmail/classify.ts';

/**
 * Heartbeat (TASKS.md): a background checklist (`data/heartbeat.md`), separate from chat, off by default. Every
 * `heartbeat_interval_minutes` within active hours, never while the user is in a conversation: deterministic pre-checks
 * first (new unread mail / urgent senders via Gmail read-only, tasks due soon, failed tasks). Nothing new = no model call.
 * Something new = one isolated unattended run (read-only tools, no history/memory, small prompt) that answers `[SILENT]`
 * or at most two sentences; the note goes to the announcement queue (asked first). A finding is never told twice.
 */
export const CHECKLIST_CHARS = 1500, FINDINGS_CHARS = 800, NOTE_CHARS = 300;
/** Upper bound of the heartbeat prompt (checklist + findings + previous note + rules). */
export const HEARTBEAT_PROMPT_CHARS = 3200;
export const HEARTBEAT_KEY = 'heartbeat';
const SEEN_MAX = 500, SAID_MAX = 100, PENDING_MAX = 5;
/** Tasks due within this window count as "due soon". */
export const DUE_SOON_MS = 60 * 60_000;
export const CONVERSATION_MS = 120_000;
/** Notes older than this are not spoken any more. */
const NOTE_MAX_AGE_MS = 6 * 3_600_000;

export type Finding = { key: string; kind: 'mail' | 'urgent' | 'due' | 'failed'; text: string };
export type PreCheck = (since: number, now: number, signal: AbortSignal) => Promise<Finding[]>;
type State = { last_run: number; seen: string[]; said: string[]; pending: { id: string; text: string; at: number }[]; last_note: string; next_id: number };
export type HeartbeatOutcome = 'off' | 'quiet' | 'conversation' | 'wait' | 'unchanged' | 'silent' | 'duplicate' | 'said' | 'failed' | 'cancelled' | 'empty';

/** Active hours `start`–`end` (local, end exclusive; start > end wraps midnight). */
export function activeHour(now: number, start: number, end: number): boolean {
  const hour = new Date(now).getHours();
  return start < end ? hour >= start && hour < end : hour >= start || hour < end;
}

/** Gmail read-only: unread inbox mail of the last two days (ids only) and, when urgent senders are set, their sender. Mail content is never read. */
export function mailCheck(gmail: Mailboxes, urgent: () => readonly string[]): PreCheck {
  return async (_since, _now, signal) => {
    const out: Finding[] = [];
    for (const account of await gmail.accounts().catch(() => [] as string[])) {
      try {
        const provider = gmail.provider(account), list = urgent();
        for (const id of await provider.search('is:unread in:inbox newer_than:2d', 20, AbortSignal.any([signal, AbortSignal.timeout(15_000)]))) {
          let finding: Finding = { key: `mail:${account}:${id}`, kind: 'mail', text: account };
          if (list.length) {
            const from = parseFrom((await provider.metadata(id, AbortSignal.any([signal, AbortSignal.timeout(10_000)]))).from);
            if (urgentSender(from.address, list)) finding = { ...finding, kind: 'urgent', text: clip(from.name || from.address, 60) };
          }
          out.push(finding);
        }
      } catch { signal.throwIfAborted(); } // One mailbox failing never stops the others.
    }
    return out;
  };
}

/** The task store: tasks due within the hour and failed runs since the last tick. */
export function tasksCheck(store: TaskStore): PreCheck {
  return async (since, now) => {
    const out: Finding[] = [];
    for (const task of store.list())
      if (task.status === 'active' && task.next_run_at !== null && task.next_run_at > now && task.next_run_at <= now + DUE_SOON_MS)
        out.push({ key: `due:${task.id}:${task.next_run_at}`, kind: 'due', text: `“${clip(task.name, 60)}” ${when(task.next_run_at, now)}` });
    for (const run of store.failedSince(since)) out.push({ key: `failed:${run.id}`, kind: 'failed', text: `“${clip(run.name, 60)}”` });
    return out;
  };
}

/** New findings as short lines for the model: counts and names only, no mail content. */
export function describeFindings(findings: Finding[]): string {
  const lines: string[] = [], of = (kind: Finding['kind']) => findings.filter(f => f.kind === kind);
  const mail = of('mail').length + of('urgent').length;
  if (mail) lines.push(`- ${mail} new unread email(s)`);
  const urgent = [...new Set(of('urgent').map(f => f.text))];
  if (urgent.length) lines.push(`- from urgent senders: ${urgent.join(', ')}`);
  const due = of('due').map(f => f.text);
  if (due.length) lines.push(`- tasks due soon: ${due.join('; ')}`);
  const failed = of('failed').map(f => f.text);
  if (failed.length) lines.push(`- failed tasks: ${failed.join(', ')}`);
  return lines.join('\n').slice(0, FINDINGS_CHARS);
}

/** The isolated unattended heartbeat prompt (≤ HEARTBEAT_PROMPT_CHARS). */
export function heartbeatPrompt(checklist: string, findings: string, previous = ''): string {
  return ('Heartbeat check; the user is not present: do not ask questions. Read-only tools only; never send, change or delete anything. ' +
    `Check the checklist against the changes below. If nothing needs the user's attention, reply exactly ${SILENT}. Otherwise reply in Turkish, ` +
    'at most 2 short sentences. Do not invent work; do not repeat the previous note.\nChecklist:\n' + checklist.trim().slice(0, CHECKLIST_CHARS) +
    '\nChanges since the last check:\n' + findings.slice(0, FINDINGS_CHARS) + (previous ? '\nPrevious note: ' + clip(previous, PREVIOUS_CHARS) : '')).slice(0, HEARTBEAT_PROMPT_CHARS);
}

/** At most two sentences, ≤ NOTE_CHARS. */
export function shortNote(text: string): string {
  const sentences = text.replace(/\s+/g, ' ').trim().match(/[^.!?…]+[.!?…]*/g) ?? [];
  return clip(sentences.slice(0, 2).join('').trim(), NOTE_CHARS);
}

/** Checklist file (gitignored) or the tracked example. */
export async function readChecklist(file: string, example: string): Promise<string> {
  try { return await readFile(file, 'utf8'); }
  catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
  return readFile(example, 'utf8').catch(() => '');
}
export async function saveChecklist(file: string, content: unknown): Promise<string> {
  if (typeof content !== 'string' || content.length > CHECKLIST_CHARS) throw Error(`Heartbeat listesi: en fazla ${CHECKLIST_CHARS} karakter`);
  await mkdir(path.dirname(file), { recursive: true, mode: 0o700 });
  const temporary = file + '.tmp';
  await writeFile(temporary, content, { mode: 0o600 });
  await rename(temporary, file);
  return content;
}
/** "- item" appended to the checklist (from chat, after the user's confirmation). */
export async function appendChecklist(file: string, example: string, item: string): Promise<string> {
  const current = (await readChecklist(file, example)).replace(/\s+$/, '');
  return saveChecklist(file, (current ? current + '\n' : '') + '- ' + clip(item, 200) + '\n');
}

/** "heartbeat'e ekle: …", "heartbeat listesine ekle …", "add to heartbeat: …" → the item; otherwise undefined. */
export function heartbeatAddRequest(text: string): string | undefined {
  const m = /^\s*(?:(?:heartbeat|kontrol\s+listesi)(?:['’]?(?:e|a|ye|ya)|\s+listesine|ne)?\s+ekle|add\s+to\s+(?:the\s+)?heartbeat)\s*[:,-]?\s*(.+)$/isu.exec(text);
  const item = m?.[1].trim().replace(/[.!]+$/, '');
  return item ? item : undefined;
}

export class Heartbeat implements AnnouncementSource {
  private readonly config: Config; private readonly store: TaskStore; private readonly lane: TaskLane; private readonly run: TaskRun;
  private readonly checks: PreCheck[]; private readonly conversation: () => boolean; private readonly notify: Notify; readonly now: () => number;
  readonly file: string; readonly example: string;
  private timer: NodeJS.Timeout | undefined;
  private passing: Promise<HeartbeatOutcome> | undefined;
  constructor(options: { config: Config; store: TaskStore; lane: TaskLane; run: TaskRun; checks: PreCheck[]; conversation: () => boolean;
    file: string; example: string; now?: () => number; notify?: Notify }) {
    this.config = options.config; this.store = options.store; this.lane = options.lane; this.run = options.run; this.checks = options.checks;
    this.conversation = options.conversation; this.file = options.file; this.example = options.example; this.now = options.now ?? (() => Date.now());
    this.notify = options.notify ?? osascriptNotify;
  }

  state(): State {
    const fresh: State = { last_run: 0, seen: [], said: [], pending: [], last_note: '', next_id: 1 };
    try { return { ...fresh, ...JSON.parse(this.store.meta(HEARTBEAT_KEY) ?? '{}') }; } catch { return fresh; }
  }
  private save(state: State): void {
    state.seen = state.seen.slice(-SEEN_MAX); state.said = state.said.slice(-SAID_MAX); state.pending = state.pending.filter(p => this.now() - p.at < NOTE_MAX_AGE_MS).slice(-PENDING_MAX);
    this.store.setMeta(HEARTBEAT_KEY, JSON.stringify(state));
  }

  /** Checked every minute; runs at most once per interval (missed ticks while asleep collapse into one). */
  start(signal: AbortSignal, firstMs = 60_000, everyMs = 60_000): void {
    const tick = (delay: number) => {
      this.timer = setTimeout(async () => {
        await this.tick(signal).catch(() => 'failed');
        if (!signal.aborted) tick(everyMs);
      }, delay);
      this.timer.unref();
    };
    signal.addEventListener('abort', () => clearTimeout(this.timer), { once: true });
    if (!signal.aborted) tick(firstMs);
  }

  tick(signal: AbortSignal): Promise<HeartbeatOutcome> {
    return this.passing ??= this.pass(signal).finally(() => { this.passing = undefined; });
  }

  private async pass(signal: AbortSignal): Promise<HeartbeatOutcome> {
    const c = this.config;
    if (!c.heartbeat_enabled) return 'off';
    const now = this.now();
    if (!activeHour(now, c.heartbeat_start_hour, c.heartbeat_end_hour)) return 'quiet';
    if (this.conversation()) return 'conversation';
    const state = this.state();
    if (now - state.last_run < c.heartbeat_interval_minutes * 60_000) return 'wait';
    const since = state.last_run || now - c.heartbeat_interval_minutes * 60_000;
    const findings: Finding[] = [];
    for (const check of this.checks) findings.push(...await check(since, now, signal).catch(() => { signal.throwIfAborted(); return []; }));
    const seen = new Set(state.seen), fresh = findings.filter(f => !seen.has(f.key));
    const previous = state.last_run;
    state.last_run = now;
    const markSeen = () => { for (const f of fresh) if (!seen.has(f.key)) { state.seen.push(f.key); seen.add(f.key); } };
    if (!fresh.length) { this.save(state); activity('heartbeat', { outcome: 'unchanged' }); return 'unchanged'; }
    const checklist = (await readChecklist(this.file, this.example).catch(() => '')).trim();
    if (!checklist) { markSeen(); this.save(state); return 'empty'; }
    this.save(state);
    const slot = await this.lane.acquire(signal).catch(() => undefined);
    if (!slot) return 'cancelled';
    let outcome: HeartbeatOutcome, notice = '';
    try {
      const findingsText = describeFindings(fresh);
      const text = (await this.run(heartbeatPrompt(checklist, findingsText, state.last_note), 'heartbeat: ' + findingsText, slot.signal)).text.trim();
      const after = this.state(); after.last_run = now;
      for (const f of fresh) if (!after.seen.includes(f.key)) after.seen.push(f.key);
      const note = isSilent(text) ? '' : shortNote(text), hash = note && hashText(note.toLocaleLowerCase('tr'));
      if (!note) outcome = 'silent';
      else if (after.said.includes(hash) || after.pending.some(p => p.text === note)) outcome = 'duplicate';
      else {
        after.said.push(hash); after.last_note = note;
        after.pending.push({ id: 'heartbeat-' + after.next_id++, text: note, at: this.now() });
        outcome = 'said'; notice = note;
      }
      this.save(after);
    } catch {
      // Cancelled by the user's request: the same changes are checked again at the next free tick. Failed: next interval.
      if (slot.signal.aborted) { const back = this.state(); back.last_run = previous; this.save(back); outcome = 'cancelled'; }
      else outcome = 'failed';
    } finally { slot.release(); }
    // Silent notification like a task result (once per new note; the spoken note still waits for the user's yes).
    if (notice) await this.notify('Asistan: Heartbeat', clip(notice, 140)).catch(() => {});
    activity('heartbeat', { outcome, findings: fresh.length });
    return outcome;
  }

  /** Waiting notes (task results' priority); old ones are dropped from speech. */
  async announcements(): Promise<Announcement[]> {
    const now = this.now();
    return this.state().pending.filter(p => now - p.at < NOTE_MAX_AGE_MS).map(p => ({ id: p.id, text: p.text, priority: 2 }));
  }
  async shown(id: string): Promise<boolean> {
    if (!id.startsWith('heartbeat-')) return false;
    const state = this.state(), before = state.pending.length;
    state.pending = state.pending.filter(p => p.id !== id);
    if (state.pending.length === before) return false;
    this.save(state);
    return true;
  }
}
