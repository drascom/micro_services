import { createHash, randomBytes } from 'node:crypto';
import { appendFile, mkdir, readFile, rename, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { ConfirmationStore } from '../apple-reminders/confirmations.ts';
import type { RemindersTransport } from '../apple-reminders/client.ts';
import { ACTIONS, classifyAll, clean, parseFrom, type ClassifyModel } from './classify.ts';
import type { MailProvider, MessageMeta } from './provider.ts';
import { GmailError, JUNK_LABEL, LATER_LABEL, type Level } from './schemas.ts';

export const MAX_PROPOSE = 50, MAX_PURGE = 200, KEEP_DAYS = 60, MAX_PROCESSED = 20_000;
export const REPLY_PREFIX = '📧';

export type TriageSettings = { gmail_urgent_senders: readonly string[]; gmail_daily_limit: number;
  reminders_enabled: boolean; tasks_list: string };
type Entry = { id: string; level: Level; labels: string[]; from: string; subject: string };
type Plan = { kind: 'apply'; entries: Entry[]; labelIds: Record<string, string> } | { kind: 'purge'; ids: string[] };
export type LogLine = { at: number; batch: string; kind: 'apply' | 'trash' | 'undo'; id?: string; level?: Level; before?: string[]; after?: string[] };

/** Plans awaiting the user's reply live for the whole backend process, like the reminder deletion token. */
export class TriageMemory {
  readonly store: ConfirmationStore;
  readonly plans = new Map<string, Plan>();
  constructor(store = new ConfirmationStore()) { this.store = store; }
}
export const triageMemory = new TriageMemory();

const hash = (value: unknown) => createHash('sha256').update(JSON.stringify(value)).digest('hex').slice(0, 24);
const sorted = (labels: string[]) => [...labels].sort();
async function mapLimit<T, R>(items: T[], limit: number, work: (item: T) => Promise<R>): Promise<R[]> {
  const result: R[] = new Array(items.length); let next = 0;
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (next < items.length) { const i = next++; result[i] = await work(items[i]); }
  }));
  return result;
}
const shown = (e: Pick<Entry, 'from' | 'subject'>) => { const f = parseFrom(e.from); return { from: f.name || f.address, subject: clean(e.subject, 100) }; };

/**
 * Inbox triage: propose (no change) → apply with a token valid only in the user's next turn; purge_junk moves the
 * `Gereksiz` label to Trash the same way; undo restores the last apply from the action log. The log and state hold
 * message ids, levels and labels only (no subject or body).
 */
export class MailTriage {
  private readonly o: { provider: MailProvider; settings: TriageSettings; memory: TriageMemory; turn: number; logFile: string | null;
    stateFile: string | null; importance: () => Promise<string>; model?: ClassifyModel; notify?: (title: string, body: string) => Promise<void>;
    reminders?: RemindersTransport; proposeOnly?: boolean; now: () => number };
  constructor(options: Omit<MailTriage['o'], 'now'> & { now?: () => number }) { this.o = { ...options, now: options.now ?? (() => Date.now()) }; }

  // ---- state (processed ids) and action log ----
  private async processed(): Promise<Record<string, number>> {
    if (!this.o.stateFile) return this.memoryState ??= {};
    try { const value = JSON.parse(await readFile(this.o.stateFile, 'utf8')); if (value && typeof value.processed === 'object') return value.processed; } catch {}
    return {};
  }
  private memoryState?: Record<string, number>;
  private async markProcessed(ids: string[]) {
    if (!ids.length) return;
    const state = await this.processed(), now = this.o.now(), cutoff = now - KEEP_DAYS * 86_400_000;
    for (const id of ids) state[id] = now;
    const kept = Object.entries(state).filter(([, at]) => at >= cutoff).sort((a, b) => b[1] - a[1]).slice(0, MAX_PROCESSED);
    const next = Object.fromEntries(kept);
    if (!this.o.stateFile) { this.memoryState = next; return; }
    await mkdir(path.dirname(this.o.stateFile), { recursive: true, mode: 0o700 });
    const temporary = this.o.stateFile + '.tmp';
    await writeFile(temporary, JSON.stringify({ processed: next }) + '\n', { mode: 0o600 });
    await rename(temporary, this.o.stateFile);
  }
  private memoryLog: LogLine[] = [];
  async log(): Promise<LogLine[]> {
    if (!this.o.logFile) return this.memoryLog;
    let text = '';
    try { text = await readFile(this.o.logFile, 'utf8'); } catch {}
    return text.split('\n').flatMap(line => { try { return line ? [JSON.parse(line) as LogLine] : []; } catch { return []; } });
  }
  private async append(lines: LogLine[]) {
    if (!lines.length) return;
    if (!this.o.logFile) { this.memoryLog.push(...lines); return; }
    await mkdir(path.dirname(this.o.logFile), { recursive: true, mode: 0o700 });
    try { if ((await stat(this.o.logFile)).size > 2 * 1024 * 1024) await rename(this.o.logFile, this.o.logFile + '.previous'); } catch {}
    await appendFile(this.o.logFile, lines.map(l => JSON.stringify(l)).join('\n') + '\n', { mode: 0o600 });
  }
  /** Changes left today: applied and trashed messages in the last 24 hours count against gmail_daily_limit. */
  async remaining(): Promise<number> {
    const since = this.o.now() - 86_400_000;
    const used = (await this.log()).filter(l => l.at >= since && (l.kind === 'apply' || l.kind === 'trash') && l.id).length;
    return Math.max(0, this.o.settings.gmail_daily_limit - used);
  }

  private issue(plan: Plan, kind: 'apply' | 'purge') {
    const id = hash(plan);
    this.o.memory.plans.clear(); // One pending plan at a time; a new proposal replaces the previous one.
    this.o.memory.plans.set(id, plan);
    return this.o.memory.store.issue(this.o.turn, { id, title: kind === 'apply' ? 'mail plan' : 'junk purge', ref: kind });
  }
  private redeem(token: string | undefined, kind: 'apply' | 'purge'): Plan {
    if (this.o.proposeOnly) throw new GmailError('invalid_request');
    if (!token) throw new GmailError('invalid_request');
    const result = this.o.memory.store.consume(token, this.o.turn, kind);
    if (typeof result === 'string') throw new GmailError(result);
    const plan = this.o.memory.plans.get(result.id);
    this.o.memory.plans.delete(result.id);
    if (!plan || plan.kind !== kind) throw new GmailError('confirmation_invalid');
    return plan;
  }

  // ---- actions ----
  async propose(signal: AbortSignal) {
    const labels = await this.o.provider.labels(signal);
    const done = await this.processed();
    const ids = await this.o.provider.inbox(MAX_PROPOSE, id => id in done, signal);
    if (!ids.length) return { status: 'empty', note: 'No new inbox mail to organize.' };
    const metas = await mapLimit(ids, 5, id => this.o.provider.metadata(id, signal));
    const levels = await classifyAll(metas, this.o.settings.gmail_urgent_senders, await this.o.importance().catch(() => ''), this.o.model, signal);
    const entries: Entry[] = metas.map((m, i) => ({ id: m.id, level: levels[i], labels: sorted(m.labels), from: m.from, subject: m.subject }));
    const missing = [LATER_LABEL, JUNK_LABEL].filter(name => !labels.has(name));
    const counts = { urgent: 0, important: 0, later: 0, junk: 0 } as Record<Level, number>;
    for (const e of entries) counts[e.level]++;
    const skipped = entries.filter(e => ACTIONS[e.level].label && missing.includes(ACTIONS[e.level].label!)).length;
    const labelIds = Object.fromEntries([LATER_LABEL, JUNK_LABEL].flatMap(name => labels.has(name) ? [[name, labels.get(name)!]] : []));
    const plan: Plan = { kind: 'apply', entries, labelIds };
    return { status: 'proposal', counts,
      urgent: entries.filter(e => e.level === 'urgent').slice(0, 10).map(shown),
      important: entries.filter(e => e.level === 'important').slice(0, 10).map(shown),
      ...(missing.length ? { missing_labels: missing, skipped } : {}),
      ...(this.o.proposeOnly ? { note: 'Plan only; nothing changes.' } : { confirm_token: this.issue(plan, 'apply'),
        next: 'Show the counts and the urgent/important lines, ask whether to apply, then stop. Call apply with confirm_token only after the user\'s reply confirms.' }) };
  }

  async apply(token: string | undefined, signal: AbortSignal) {
    const plan = this.redeem(token, 'apply');
    if (plan.kind !== 'apply') throw new GmailError('confirmation_invalid');
    let remaining = await this.remaining();
    if (remaining <= 0) throw new GmailError('gmail_daily_limit');
    const result = { applied: 0, skipped_changed: 0, skipped_label: 0, failed: 0, limited: 0 };
    // Recheck: a message that left the inbox or changed labels since the proposal is skipped.
    const current = await mapLimit(plan.entries, 5, e => this.o.provider.metadata(e.id, signal).catch(error => {
      if (error instanceof GmailError && ['gmail_auth_expired', 'gmail_rate_limited', 'cancelled'].includes(error.code)) throw error;
      return undefined;
    }));
    const groups = new Map<string, { add: string[]; remove: string[]; items: { e: Entry; before: string[]; after: string[] }[] }>();
    plan.entries.forEach((e, i) => {
      const now = current[i];
      if (!now || !now.labels.includes('INBOX') || JSON.stringify(sorted(now.labels)) !== JSON.stringify(e.labels)) { result.skipped_changed++; return; }
      const action = ACTIONS[e.level];
      if (action.label && !plan.labelIds[action.label]) { result.skipped_label++; return; }
      const wanted = [...action.add, ...(action.label ? [plan.labelIds[action.label]] : [])];
      const add = wanted.filter(l => !e.labels.includes(l)), remove = action.remove.filter(l => e.labels.includes(l));
      const after = sorted([...e.labels.filter(l => !remove.includes(l)), ...add]);
      const key = JSON.stringify([add, remove]);
      if (!groups.has(key)) groups.set(key, { add, remove, items: [] });
      groups.get(key)!.items.push({ e, before: e.labels, after });
    });
    const batch = 'b_' + randomBytes(6).toString('hex'), applied: Entry[] = [], done: string[] = [];
    for (const group of groups.values()) {
      const items = group.items.slice(0, remaining);
      result.limited += group.items.length - items.length;
      if (!items.length) continue;
      if (!group.add.length && !group.remove.length) { items.forEach(x => done.push(x.e.id)); applied.push(...items.map(x => x.e)); result.applied += items.length; continue; }
      try { await this.o.provider.modify(items.map(x => x.e.id), group.add, group.remove, signal); }
      catch (error) {
        if (error instanceof GmailError && ['gmail_auth_expired', 'cancelled'].includes(error.code) && !result.applied) throw error;
        result.failed += items.length; continue;
      }
      remaining -= items.length; result.applied += items.length;
      applied.push(...items.map(x => x.e)); done.push(...items.map(x => x.e.id));
      await this.append(items.map(x => ({ at: this.o.now(), batch, kind: 'apply' as const, id: x.e.id, level: x.e.level, before: x.before, after: x.after })));
    }
    await this.markProcessed(done);
    // Urgent: notification and a 📧 reminder (the task scanner never runs 📧 items).
    const urgent = applied.filter(e => e.level === 'urgent');
    for (const e of urgent) {
      const s = shown(e), line = `${s.from} – ${s.subject}`;
      if (this.o.notify) await this.o.notify('🔴 Hemen cevapla', clean(line, 140)).catch(() => {});
      if (this.o.reminders && this.o.settings.reminders_enabled)
        await this.o.reminders.request({ op: 'add', title: clean(`${REPLY_PREFIX} Cevapla: ${line}`, 200), list: this.o.settings.tasks_list }, signal).catch(() => '');
    }
    return { status: 'applied', ...result, urgent: urgent.length, ...(result.applied ? { undo: 'undo restores this change' } : {}) };
  }

  async purgeJunk(token: string | undefined, signal: AbortSignal) {
    if (token) {
      const plan = this.redeem(token, 'purge');
      if (plan.kind !== 'purge') throw new GmailError('confirmation_invalid');
      const remaining = await this.remaining();
      if (remaining <= 0) throw new GmailError('gmail_daily_limit');
      const ids = plan.ids.slice(0, remaining), batch = 'b_' + randomBytes(6).toString('hex');
      let trashed = 0, failed = 0;
      for (const id of ids) {
        try { await this.o.provider.trash(id, signal); trashed++; await this.append([{ at: this.o.now(), batch, kind: 'trash', id }]); }
        catch (error) {
          if (error instanceof GmailError && ['gmail_auth_expired', 'gmail_rate_limited', 'cancelled'].includes(error.code)) { failed += ids.length - trashed - failed; break; }
          failed++;
        }
      }
      return { status: 'trashed', trashed, failed, limited: plan.ids.length - ids.length, note: 'Moved to Trash (recoverable for 30 days), not deleted.' };
    }
    const labels = await this.o.provider.labels(signal), junk = labels.get(JUNK_LABEL);
    if (!junk) return { status: 'label_missing', missing_labels: [JUNK_LABEL] };
    const ids = await this.o.provider.labeled(junk, MAX_PURGE, signal);
    if (!ids.length) return { status: 'empty', note: `No mail labeled ${JUNK_LABEL}.` };
    // Listed newest first: the first and last give the date range.
    const [newest, oldest] = await Promise.all([this.o.provider.metadata(ids[0], signal), this.o.provider.metadata(ids.at(-1)!, signal)]);
    const day = (m: MessageMeta) => m.date ? new Date(m.date).toISOString().slice(0, 10) : undefined;
    return { status: 'purge_proposal', count: ids.length, ...(ids.length >= MAX_PURGE ? { more: true } : {}), oldest: day(oldest), newest: day(newest),
      ...(this.o.proposeOnly ? {} : { confirm_token: this.issue({ kind: 'purge', ids }, 'purge'),
        next: 'Ask the user to confirm moving these to Trash, then stop. Call purge_junk with confirm_token only after their reply confirms.' }) };
  }

  /** Restores the labels of the last apply that is not undone yet. Harmless, so no confirmation. */
  async undo(signal: AbortSignal) {
    const lines = await this.log();
    const undone = new Set(lines.filter(l => l.kind === 'undo').map(l => l.batch));
    const last = [...lines].reverse().find(l => l.kind === 'apply' && !undone.has(l.batch));
    if (!last) throw new GmailError('nothing_to_undo');
    const items = lines.filter(l => l.kind === 'apply' && l.batch === last.batch && l.id && l.before && l.after);
    const groups = new Map<string, { add: string[]; remove: string[]; ids: string[] }>();
    for (const l of items) {
      const add = l.before!.filter(x => !l.after!.includes(x)), remove = l.after!.filter(x => !l.before!.includes(x));
      const key = JSON.stringify([add, remove]);
      if (!groups.has(key)) groups.set(key, { add, remove, ids: [] });
      groups.get(key)!.ids.push(l.id!);
    }
    let restored = 0, failed = 0;
    for (const g of groups.values()) {
      if (!g.add.length && !g.remove.length) { restored += g.ids.length; continue; }
      try { await this.o.provider.modify(g.ids, g.add, g.remove, signal); restored += g.ids.length; }
      catch (error) { if (error instanceof GmailError && error.code === 'gmail_auth_expired') throw error; failed += g.ids.length; }
    }
    await this.append([{ at: this.o.now(), batch: last.batch, kind: 'undo' }]);
    return { status: 'undone', restored, failed };
  }

  /** Admin: the latest batches (counts only), newest first. */
  async recent(limit = 10) {
    const lines = await this.log(), undone = new Set(lines.filter(l => l.kind === 'undo').map(l => l.batch));
    const batches = new Map<string, { batch: string; at: number; kind: string; count: number; undone: boolean; levels: Record<string, number> }>();
    for (const l of lines) {
      if (l.kind === 'undo' || !l.id) continue;
      const b = batches.get(l.batch) ?? { batch: l.batch, at: l.at, kind: l.kind, count: 0, undone: undone.has(l.batch), levels: {} };
      b.count++; if (l.level) b.levels[l.level] = (b.levels[l.level] ?? 0) + 1;
      batches.set(l.batch, b);
    }
    return [...batches.values()].reverse().slice(0, limit);
  }
}
