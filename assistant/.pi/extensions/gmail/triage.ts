import { createHash, randomBytes } from 'node:crypto';
import { appendFile, mkdir, readFile, rename, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { ConfirmationStore } from '../apple-reminders/confirmations.ts';
import type { RemindersTransport } from '../apple-reminders/client.ts';
import { ACTIONS, classifyAll, clean, parseFrom, type ClassifyModel } from './classify.ts';
import type { MessageMeta } from './provider.ts';
import type { Mailboxes } from './service.ts';
import { findLabel, GmailError, JUNK_LABEL, LATER_LABEL, LEVELS, type GmailErrorCode, type Level } from './schemas.ts';

export const MAX_PROPOSE = 50, MAX_PURGE = 200, MAX_FILTER = 100, KEEP_DAYS = 60, MAX_PROCESSED = 20_000;
export const REPLY_PREFIX = '📧';

export type TriageSettings = { gmail_urgent_senders: readonly string[]; gmail_daily_limit: number;
  reminders_enabled: boolean; tasks_list: string };
type Entry = { account: string; id: string; level: Level; labels: string[]; from: string; subject: string };
type ActPart = { account: string; entries: { id: string; labels: string[] }[]; add: string[]; remove: string[] };
/** One plan may span several accounts; each entry carries its account and label ids are per account. */
type Plan = { kind: 'apply'; entries: Entry[]; labelIds: Record<string, Record<string, string>> } | { kind: 'purge'; items: { account: string; id: string }[] }
  | { kind: 'act'; act: string; trash: boolean; parts: ActPart[] };
/** `account`: the mailbox the change was made in (lines written before multi-account have none: the first account). */
export type LogLine = { at: number; batch: string; kind: 'apply' | 'trash' | 'undo'; account?: string; id?: string; level?: Level; before?: string[]; after?: string[] };
type State = { processed?: Record<string, number>; accounts?: Record<string, Record<string, number>> };

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
/** Account-level failures: recorded for that account while the others go on. */
const ACCOUNT_ERRORS = new Set(['gmail_auth_expired', 'gmail_rate_limited', 'gmail_unavailable', 'gmail_not_connected', 'invalid_response', 'timeout']);
const accountError = (error: unknown) => error instanceof GmailError && ACCOUNT_ERRORS.has(error.code) ? error.code : undefined;
const BAD_ACT = 'act must be one of trash, archive, mark_read, mark_unread, star, unstar, label:<existing label>, unlabel:<label>, and needs a query.';
const FIXED_ACTS: Record<string, { add: string[]; remove: string[] }> = {
  archive: { add: [], remove: ['INBOX'] }, mark_read: { add: [], remove: ['UNREAD'] }, mark_unread: { add: ['UNREAD'], remove: [] },
  star: { add: ['STARRED'], remove: [] }, unstar: { add: [], remove: ['STARRED'] }
};
/** A closed set of explicit actions; labels must already exist (the app never creates labels). */
export function parseAct(act: string, labels: ReadonlyMap<string, string>): { add: string[]; remove: string[]; trash: boolean } | { missing: string } {
  const value = act.trim();
  if (value.length > 100) throw new GmailError('invalid_request', BAD_ACT);
  if (value === 'trash') return { add: [], remove: [], trash: true };
  if (FIXED_ACTS[value]) return { ...FIXED_ACTS[value], trash: false };
  const named = /^(label|unlabel):(.+)$/.exec(value), name = named ? clean(named[2], 100) : '';
  if (!named || !name) throw new GmailError('invalid_request', BAD_ACT);
  const id = findLabel(labels, name);
  if (!id) return { missing: name };
  return named[1] === 'label' ? { add: [id], remove: [], trash: false } : { add: [], remove: [id], trash: false };
}
/** Gmail search scope: the inbox unless the query names `in:`/`label:`; `in:all` = all mail (Gmail still skips Spam/Trash). */
export function scopedQuery(query: string): string {
  const q = clean(query, 201);
  if (q.length > 200) throw new GmailError('invalid_request', 'query is too long (max 200 characters).');
  if (!q) throw new GmailError('invalid_request', 'query is empty; use e.g. from:example.com or subject:invoice.');
  if (/(^|[\s(-])in:(anywhere|trash|spam)\b/i.test(q)) throw new GmailError('invalid_request', 'Trash and Spam cannot be searched; use in:all, in:inbox or label:<name>.');
  if (/(^|[\s(-])in:all\b/i.test(q)) return clean(q.replace(/(^|[\s(])in:all\b/gi, ' '), 200) || 'in:inbox';
  return /(^|[\s(-])(in|label):/i.test(q) ? q : `in:inbox ${q}`;
}
const shown = (e: Pick<Entry, 'from' | 'subject'>) => { const f = parseFrom(e.from); return { from: f.name || f.address, subject: clean(e.subject, 100) }; };

/** Subject for the model: long ids, numbers and hashes become `…` (years like 2026 stay). */
export function scrub(subject: string): string {
  const words = clean(subject, 200).split(' ').map(w => {
    const digits = w.replace(/\D/g, '').length;
    return digits >= 4 && !/^\W*(19|20)\d\d\W*$/.test(w) || /[0-9a-f]{12,}/i.test(w) || w.length >= 20 && /\d/.test(w) ? '…' : w;
  });
  return clean(words.join(' ').replace(/…(\s*…)+/g, '…'), 60);
}

export const MAX_GROUPS = 5;
/**
 * Compact per-sender summary of one level: `Sender (n): subject; subject`, at most MAX_GROUPS lines, the rest as one
 * `and N more …` line. Distinct scrubbed subjects only (2 per sender), so six "project … shut down" mails read once.
 */
export function groups(entries: Pick<Entry, 'from' | 'subject'>[]): string[] {
  const bySender = new Map<string, { n: number; subjects: Set<string> }>();
  for (const e of entries) {
    const f = parseFrom(e.from), key = f.name || f.domain || f.address || '?';
    const g = bySender.get(key) ?? { n: 0, subjects: new Set<string>() };
    g.n++; const subject = scrub(e.subject); if (subject) g.subjects.add(subject);
    bySender.set(key, g);
  }
  const all = [...bySender.entries()].sort((a, b) => b[1].n - a[1].n);
  const lines = all.slice(0, MAX_GROUPS).map(([from, g]) => {
    const subjects = [...g.subjects];
    return `${from} (${g.n}): ${subjects.slice(0, 2).join('; ')}${subjects.length > 2 ? '; …' : ''}`;
  });
  const rest = all.slice(MAX_GROUPS);
  if (rest.length) lines.push(`and ${rest.length} more senders (${rest.reduce((sum, [, g]) => sum + g.n, 0)} mails)`);
  return lines;
}

/** The latest proposal, per mail (sender + subject), for Admin > Status only; in memory, never sent to the model. */
export type PlanView = { at: number; counts: Record<Level, number>; missing_labels: string[]; items: { account: string; level: Level; from: string; subject: string }[] };
let lastPlanView: PlanView | undefined;
export const lastPlan = () => lastPlanView;

/**
 * Inbox triage over every connected Gmail account (or one, `account`): propose (no change) → apply with a token valid
 * only in the user's next turn; purge_junk moves the `Gereksiz` label to Trash the same way; undo restores the last apply
 * (in whichever accounts it touched) from the action log. One account failing (e.g. expired token) is reported under
 * `errors` and never stops the others. The log and state hold account, message ids, levels and labels only.
 */
export class MailTriage {
  private readonly o: { mailboxes: Mailboxes; settings: TriageSettings; memory: TriageMemory; turn: number; logFile: string | null;
    stateFile: string | null; importance: () => Promise<string>; model?: ClassifyModel; notify?: (title: string, body: string) => Promise<void>;
    reminders?: RemindersTransport; proposeOnly?: boolean; now: () => number };
  constructor(options: Omit<MailTriage['o'], 'now'> & { now?: () => number }) { this.o = { ...options, now: options.now ?? (() => Date.now()) }; }

  // ---- accounts ----
  /** All connected accounts, or the one `account` names (full address or a unique part such as "work"). */
  private async select(account?: string): Promise<string[]> {
    const all = await this.o.mailboxes.accounts();
    if (!all.length) throw new GmailError('gmail_not_connected');
    const want = clean(account ?? '', 100).toLowerCase();
    if (!want) return all;
    if (all.includes(want)) return [want];
    const found = all.filter(a => a.includes(want));
    if (found.length === 1) return found;
    throw new GmailError('invalid_request', `${found.length ? 'account is ambiguous' : 'Unknown account'}; connected: ${all.join(', ')}.`);
  }
  /** Runs `work` per account; account-level failures are collected, anything else (bad input, cancel) is thrown. */
  private async each<T>(accounts: string[], work: (account: string) => Promise<T>) {
    const ok: { account: string; value: T }[] = [], errors: Record<string, string> = {};
    for (const account of accounts) {
      try { ok.push({ account, value: await work(account) }); }
      catch (error) { const code = accountError(error); if (!code) throw error; errors[account] = code; }
    }
    if (!ok.length) throw new GmailError(Object.values(errors)[0] as GmailErrorCode);
    return { ok, errors };
  }
  private fallback?: string;
  /** Account of a log line; pre-multi-account lines belong to the first (migrated) account. */
  private async lineAccount(line: LogLine) { return line.account ?? (this.fallback ??= (await this.o.mailboxes.accounts())[0] ?? ''); }

  // ---- state (processed ids per account) and action log ----
  private memoryState?: State;
  private async state(): Promise<State> {
    if (!this.o.stateFile) return this.memoryState ??= {};
    try { const value = JSON.parse(await readFile(this.o.stateFile, 'utf8')); if (value && typeof value === 'object') return value as State; } catch {}
    return {};
  }
  /** Ids already organized in one account (pre-multi-account ids count for every account; Gmail ids are per mailbox). */
  private done(state: State, account: string): Record<string, number> { return { ...state.processed, ...state.accounts?.[account] }; }
  private async markProcessed(done: Map<string, string[]>) {
    if (![...done.values()].some(ids => ids.length)) return;
    const state = await this.state(), now = this.o.now(), cutoff = now - KEEP_DAYS * 86_400_000;
    const prune = (value: Record<string, number>) => Object.fromEntries(Object.entries(value).filter(([, at]) => at >= cutoff).sort((a, b) => b[1] - a[1]).slice(0, MAX_PROCESSED));
    const accounts = { ...state.accounts };
    for (const [account, ids] of done) { const value = { ...accounts[account] }; for (const id of ids) value[id] = now; accounts[account] = value; }
    const next: State = { ...(state.processed ? { processed: prune(state.processed) } : {}), accounts: Object.fromEntries(Object.entries(accounts).map(([a, v]) => [a, prune(v)])) };
    if (!this.o.stateFile) { this.memoryState = next; return; }
    await mkdir(path.dirname(this.o.stateFile), { recursive: true, mode: 0o700 });
    const temporary = this.o.stateFile + '.tmp';
    await writeFile(temporary, JSON.stringify(next) + '\n', { mode: 0o600 });
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
  /** Changes left today, all accounts together: applied and trashed messages in the last 24 hours count against gmail_daily_limit. */
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
    if (!token || token.length > 40) throw new GmailError('invalid_request');
    const result = this.o.memory.store.consume(token, this.o.turn, kind);
    if (typeof result === 'string') throw new GmailError(result);
    const plan = this.o.memory.plans.get(result.id);
    this.o.memory.plans.delete(result.id);
    if (!plan || (plan.kind === 'purge' ? 'purge' : 'apply') !== kind) throw new GmailError('confirmation_invalid');
    return plan;
  }
  /** Per-account details only when several accounts are involved, so one account reads exactly as before. */
  private static perAccount<T>(accounts: string[], values: Record<string, T>, keep: (value: T) => boolean = () => true) {
    const kept = Object.fromEntries(Object.entries(values).filter(([, v]) => keep(v)));
    return accounts.length > 1 ? kept : Object.values(kept)[0];
  }

  // ---- actions ----
  /** `query` limits the plan to matching mail; `act` replaces the importance levels with one explicit action; `account` picks one mailbox. */
  async propose(signal: AbortSignal, filter: { query?: string; act?: string; account?: string } = {}) {
    if (filter.act !== undefined && !filter.query) throw new GmailError('invalid_request', BAD_ACT);
    const query = filter.query !== undefined ? scopedQuery(filter.query) : undefined;
    const accounts = await this.select(filter.account);
    if (filter.act !== undefined) return this.proposeAct(accounts, query!, filter.act, signal);
    const state = await this.state();
    const { ok, errors } = await this.each(accounts, async account => {
      const provider = this.o.mailboxes.provider(account), labels = await provider.labels(signal), done = this.done(state, account);
      const ids = query ? await provider.search(query, MAX_PROPOSE, signal) : await provider.inbox(MAX_PROPOSE, id => id in done, signal);
      const metas = await mapLimit(ids, 5, id => provider.metadata(id, signal));
      const labelIds = Object.fromEntries([LATER_LABEL, JUNK_LABEL].flatMap(name => { const id = findLabel(labels, name); return id ? [[name, id]] : []; }));
      return { metas, labelIds };
    });
    const failed = Object.keys(errors).length ? { errors } : {};
    const metas = ok.flatMap(({ account, value }) => value.metas.map(m => ({ account, m })));
    if (!metas.length) return { status: 'empty', note: query ? 'No matching mail.' : 'No new inbox mail to organize.', ...failed };
    const levels = await classifyAll(metas.map(x => x.m), this.o.settings.gmail_urgent_senders, await this.o.importance().catch(() => ''), this.o.model, signal);
    const entries: Entry[] = metas.map(({ account, m }, i) => ({ account, id: m.id, level: levels[i], labels: sorted(m.labels), from: m.from, subject: m.subject }));
    const labelIds = Object.fromEntries(ok.map(({ account, value }) => [account, value.labelIds]));
    const missing = Object.fromEntries(ok.map(({ account, value }) => [account, [LATER_LABEL, JUNK_LABEL].filter(name => !value.labelIds[name])]));
    const involved = ok.map(x => x.account);
    // Counts come from the same entries the plan applies: they always add up to `total`.
    const count = (list: Entry[]) => Object.fromEntries(LEVELS.map(level => [level, list.filter(e => e.level === level).length])) as Record<Level, number>;
    const counts = count(entries);
    const skipped = entries.filter(e => ACTIONS[e.level].label && missing[e.account].includes(ACTIONS[e.level].label!)).length;
    const plan: Plan = { kind: 'apply', entries, labelIds };
    const missingAny = Object.values(missing).some(m => m.length);
    lastPlanView = { at: this.o.now(), counts, missing_labels: Object.entries(missing).flatMap(([a, names]) => names.map(n => involved.length > 1 ? `${n} (${a})` : n)),
      items: entries.map(e => ({ account: e.account, level: e.level, ...shown(e) })) };
    const of = (level: Level) => entries.filter(e => e.level === level);
    const byAccount = Object.fromEntries(involved.map(a => [a, Object.fromEntries(Object.entries(count(entries.filter(e => e.account === a))).filter(([, n]) => n))]));
    return { status: 'proposal', total: entries.length, counts, ...(involved.length > 1 ? { accounts: byAccount } : {}),
      ...(counts.urgent ? { urgent: groups(of('urgent')) } : {}), ...(counts.important ? { important: groups(of('important')) } : {}),
      ...(missingAny ? { missing_labels: MailTriage.perAccount(involved, missing, m => m.length > 0), skipped } : {}), ...failed,
      ...(this.o.proposeOnly ? { note: 'Plan only; nothing changes.' } : { confirm_token: this.issue(plan, 'apply'),
        next: 'Reply in 2-3 short sentences: level counts, the gist of the urgent/important senders (no ids or numbers), a missing label in one sentence; ' +
          'ask "Uygulayayım mı?" and stop. Call apply with confirm_token only after the user\'s reply confirms.' }) };
  }

  private async proposeAct(accounts: string[], query: string, act: string, signal: AbortSignal) {
    const { ok, errors } = await this.each(accounts, async account => {
      const provider = this.o.mailboxes.provider(account), spec = parseAct(act, await provider.labels(signal));
      if ('missing' in spec) return { missing: spec.missing };
      const found = await provider.search(query, MAX_FILTER + 1, signal), ids = found.slice(0, MAX_FILTER);
      return { spec, more: found.length > MAX_FILTER, metas: await mapLimit(ids, 5, id => provider.metadata(id, signal)) };
    });
    const failed = Object.keys(errors).length ? { errors } : {};
    const involved = ok.map(x => x.account);
    const missing = Object.fromEntries(ok.flatMap(({ account, value }) => 'missing' in value ? [[account, [value.missing!]]] : []));
    const parts = ok.flatMap(({ account, value }) => value.spec && value.metas.length ? [{ account, value }] : []);
    if (Object.keys(missing).length === ok.length)
      return { status: 'label_missing', missing_labels: Object.values(missing)[0], note: 'Labels are never created; the user creates it in Gmail.', ...failed };
    if (!parts.length) return { status: 'empty', note: 'No matching mail.', ...failed };
    const spec = parts[0].value.spec!;
    const plan: Plan = { kind: 'act', act: act.trim(), trash: spec.trash,
      parts: parts.map(({ account, value }) => ({ account, entries: value.metas!.map(m => ({ id: m.id, labels: sorted(m.labels) })), add: value.spec!.add, remove: value.spec!.remove })) };
    const total = parts.reduce((sum, p) => sum + p.value.metas!.length, 0);
    return { status: 'proposal', act: plan.act, total, ...(involved.length > 1 ? { accounts: Object.fromEntries(parts.map(p => [p.account, p.value.metas!.length])) } : {}),
      ...(parts.some(p => p.value.more) ? { more: true } : {}), senders: groups(parts.flatMap(p => p.value.metas!)),
      ...(Object.keys(missing).length ? { missing_labels: missing } : {}), ...failed,
      ...(spec.trash ? { note: 'Trash is recoverable for 30 days; nothing is deleted permanently.' } : {}),
      ...(this.o.proposeOnly ? { plan_only: true } : { confirm_token: this.issue(plan, 'apply'),
        next: 'Reply in 1-2 short sentences: count, action and main senders; ask "Uygulayayım mı?" and stop. Call apply with confirm_token only after the user\'s reply confirms.' }) };
  }

  /** Current labels per entry of one account; a message that cannot be read is `undefined` (skipped as changed). */
  private current(account: string, ids: string[], signal: AbortSignal) {
    const provider = this.o.mailboxes.provider(account);
    return mapLimit(ids, 5, id => provider.metadata(id, signal).catch(error => {
      if (error instanceof GmailError && ['gmail_auth_expired', 'gmail_rate_limited', 'gmail_not_connected', 'cancelled'].includes(error.code)) throw error;
      return undefined;
    }));
  }
  /** Ends an apply: nothing changed and every account failed → that error; otherwise the result with per-account `errors`. */
  private static settle<T extends object>(result: T, changed: number, errors: Record<string, string>, accounts: number) {
    const codes = Object.values(errors);
    if (!changed && codes.length && codes.length >= accounts) throw new GmailError(codes[0] as GmailErrorCode);
    return codes.length ? { ...result, errors } : result;
  }

  private async applyAct(plan: Extract<Plan, { kind: 'act' }>, signal: AbortSignal) {
    let remaining = await this.remaining();
    if (remaining <= 0) throw new GmailError('gmail_daily_limit');
    const result = { applied: 0, skipped_changed: 0, failed: 0, limited: 0 }, errors: Record<string, string> = {};
    const batch = 'b_' + randomBytes(6).toString('hex');
    for (const part of plan.parts) {
      const provider = this.o.mailboxes.provider(part.account), account = part.account;
      let left = part.entries.length;
      try {
        const current = await this.current(account, part.entries.map(e => e.id), signal);
        const unchanged = part.entries.filter((e, i) => current[i] && JSON.stringify(sorted(current[i]!.labels)) === JSON.stringify(e.labels));
        result.skipped_changed += part.entries.length - unchanged.length;
        const items = unchanged.slice(0, remaining);
        result.limited += unchanged.length - items.length;
        left = items.length;
        if (plan.trash) {
          for (const e of items) {
            try { await provider.trash(e.id, signal); } catch (error) { if (accountError(error) || (error as GmailError)?.code === 'cancelled') throw error; result.failed++; left--; continue; }
            result.applied++; remaining--; left--;
            await this.append([{ at: this.o.now(), batch, kind: 'trash', account, id: e.id, before: e.labels }]);
          }
        } else {
          const groups = new Map<string, { add: string[]; remove: string[]; items: { id: string; before: string[]; after: string[] }[] }>();
          for (const e of items) {
            const add = part.add.filter(l => !e.labels.includes(l)), remove = part.remove.filter(l => e.labels.includes(l));
            const key = JSON.stringify([add, remove]);
            if (!groups.has(key)) groups.set(key, { add, remove, items: [] });
            groups.get(key)!.items.push({ id: e.id, before: e.labels, after: sorted([...e.labels.filter(l => !remove.includes(l)), ...add]) });
          }
          for (const group of groups.values()) {
            if (group.add.length || group.remove.length) await provider.modify(group.items.map(x => x.id), group.add, group.remove, signal);
            result.applied += group.items.length; remaining -= group.items.length; left -= group.items.length;
            if (group.add.length || group.remove.length)
              await this.append(group.items.map(x => ({ at: this.o.now(), batch, kind: 'apply' as const, account, id: x.id, before: x.before, after: x.after })));
          }
        }
      } catch (error) {
        const code = accountError(error);
        if (!code && !(error instanceof GmailError)) throw error;
        if ((error as GmailError).code === 'cancelled' && !result.applied) throw error;
        errors[account] = code ?? (error as GmailError).code; result.failed += left;
      }
    }
    return { status: 'applied', act: plan.act, ...MailTriage.settle(result, result.applied, errors, plan.parts.length), ...(result.applied ? { undo: 'undo restores this change' } : {}) };
  }

  async apply(token: string | undefined, signal: AbortSignal) {
    const plan = this.redeem(token, 'apply');
    if (plan.kind === 'act') return this.applyAct(plan, signal);
    if (plan.kind !== 'apply') throw new GmailError('confirmation_invalid');
    let remaining = await this.remaining();
    if (remaining <= 0) throw new GmailError('gmail_daily_limit');
    const result = { applied: 0, skipped_changed: 0, skipped_label: 0, failed: 0, limited: 0 }, errors: Record<string, string> = {};
    const batch = 'b_' + randomBytes(6).toString('hex'), applied: Entry[] = [], done = new Map<string, string[]>();
    const accounts = [...new Set(plan.entries.map(e => e.account))];
    for (const account of accounts) {
      const entries = plan.entries.filter(e => e.account === account), labelIds = plan.labelIds[account] ?? {};
      const provider = this.o.mailboxes.provider(account), ids: string[] = [];
      done.set(account, ids);
      let left = entries.length;
      try {
        // Recheck: a message that left the inbox or changed labels since the proposal is skipped.
        const current = await this.current(account, entries.map(e => e.id), signal);
        const groups = new Map<string, { add: string[]; remove: string[]; items: { e: Entry; before: string[]; after: string[] }[] }>();
        entries.forEach((e, i) => {
          const now = current[i];
          if (!now || !now.labels.includes('INBOX') || JSON.stringify(sorted(now.labels)) !== JSON.stringify(e.labels)) { result.skipped_changed++; left--; return; }
          const action = ACTIONS[e.level];
          if (action.label && !labelIds[action.label]) { result.skipped_label++; left--; return; }
          const wanted = [...action.add, ...(action.label ? [labelIds[action.label]] : [])];
          const add = wanted.filter(l => !e.labels.includes(l)), remove = action.remove.filter(l => e.labels.includes(l));
          const after = sorted([...e.labels.filter(l => !remove.includes(l)), ...add]);
          const key = JSON.stringify([add, remove]);
          if (!groups.has(key)) groups.set(key, { add, remove, items: [] });
          groups.get(key)!.items.push({ e, before: e.labels, after });
        });
        for (const group of groups.values()) {
          const items = group.items.slice(0, remaining);
          result.limited += group.items.length - items.length; left -= group.items.length - items.length;
          if (!items.length) continue;
          if (group.add.length || group.remove.length) {
            try { await provider.modify(items.map(x => x.e.id), group.add, group.remove, signal); }
            catch (error) { if (accountError(error) || (error as GmailError)?.code === 'cancelled') throw error; result.failed += items.length; left -= items.length; continue; }
            await this.append(items.map(x => ({ at: this.o.now(), batch, kind: 'apply' as const, account, id: x.e.id, level: x.e.level, before: x.before, after: x.after })));
          }
          remaining -= items.length; result.applied += items.length; left -= items.length;
          applied.push(...items.map(x => x.e)); ids.push(...items.map(x => x.e.id));
        }
      } catch (error) {
        const code = accountError(error);
        if (!code && !(error instanceof GmailError)) throw error;
        if ((error as GmailError).code === 'cancelled' && !result.applied) throw error;
        errors[account] = code ?? (error as GmailError).code; result.failed += left;
      }
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
    return { status: 'applied', ...MailTriage.settle(result, result.applied, errors, accounts.length), urgent: urgent.length, ...(result.applied ? { undo: 'undo restores this change' } : {}) };
  }

  async purgeJunk(token: string | undefined, signal: AbortSignal, account?: string) {
    if (token) {
      const plan = this.redeem(token, 'purge');
      if (plan.kind !== 'purge') throw new GmailError('confirmation_invalid');
      const remaining = await this.remaining();
      if (remaining <= 0) throw new GmailError('gmail_daily_limit');
      const items = plan.items.slice(0, remaining), batch = 'b_' + randomBytes(6).toString('hex'), errors: Record<string, string> = {};
      let trashed = 0, failed = 0;
      for (const item of items) {
        if (errors[item.account]) { failed++; continue; }
        try { await this.o.mailboxes.provider(item.account).trash(item.id, signal); }
        catch (error) {
          if ((error as GmailError)?.code === 'cancelled') { failed += items.length - trashed - failed; break; }
          const code = accountError(error); if (code) errors[item.account] = code;
          failed++; continue;
        }
        trashed++; await this.append([{ at: this.o.now(), batch, kind: 'trash', account: item.account, id: item.id }]);
      }
      const accounts = new Set(plan.items.map(i => i.account)).size;
      return { status: 'trashed', ...MailTriage.settle({ trashed, failed, limited: plan.items.length - items.length }, trashed, errors, accounts),
        note: 'Moved to Trash (recoverable for 30 days), not deleted.' };
    }
    const { ok, errors } = await this.each(await this.select(account), async account => {
      const provider = this.o.mailboxes.provider(account), junk = findLabel(await provider.labels(signal), JUNK_LABEL);
      if (!junk) return { missing: true, ids: [] as string[], dates: [] as number[] };
      const ids = await provider.labeled(junk, MAX_PURGE, signal);
      // Listed newest first: the first and last give the date range.
      const ends = ids.length ? await Promise.all([provider.metadata(ids[0], signal), provider.metadata(ids.at(-1)!, signal)]) : [];
      return { missing: false, ids, dates: ends.map(m => m.date).filter(Boolean) };
    });
    const failed = Object.keys(errors).length ? { errors } : {};
    if (ok.every(x => x.value.missing)) return { status: 'label_missing', missing_labels: [JUNK_LABEL], ...failed };
    const items = ok.flatMap(({ account, value }) => value.ids.map(id => ({ account, id })));
    if (!items.length) return { status: 'empty', note: `No mail labeled ${JUNK_LABEL}.`, ...failed };
    const dates = ok.flatMap(x => x.value.dates), day = (at: number) => dates.length ? new Date(at).toISOString().slice(0, 10) : undefined;
    return { status: 'purge_proposal', count: items.length, ...(ok.length > 1 ? { accounts: Object.fromEntries(ok.map(x => [x.account, x.value.ids.length])) } : {}),
      ...(ok.some(x => x.value.ids.length >= MAX_PURGE) ? { more: true } : {}), oldest: day(Math.min(...dates)), newest: day(Math.max(...dates)), ...failed,
      ...(this.o.proposeOnly ? {} : { confirm_token: this.issue({ kind: 'purge', items }, 'purge'),
        next: 'Ask the user to confirm moving these to Trash, then stop. Call purge_junk with confirm_token only after their reply confirms.' }) };
  }

  /** Restores the last change not undone yet, in every account it touched: labels of an apply, or Trash → back. Harmless, so no confirmation. */
  async undo(signal: AbortSignal) {
    const lines = await this.log();
    const undone = new Set(lines.filter(l => l.kind === 'undo').map(l => l.batch));
    const last = [...lines].reverse().find(l => (l.kind === 'apply' || l.kind === 'trash') && l.id && !undone.has(l.batch));
    if (!last) throw new GmailError('nothing_to_undo');
    const batch = lines.filter(l => l.batch === last.batch && l.id && (l.kind === 'trash' || l.kind === 'apply' && l.before && l.after));
    const connected = await this.o.mailboxes.accounts(), byAccount = new Map<string, LogLine[]>();
    for (const l of batch) { const a = await this.lineAccount(l); byAccount.set(a, [...byAccount.get(a) ?? [], l]); }
    let restored = 0, failed = 0;
    const errors: Record<string, string> = {};
    for (const [account, list] of byAccount) {
      if (!connected.includes(account)) { errors[account] = 'gmail_not_connected'; failed += list.length; continue; }
      const provider = this.o.mailboxes.provider(account);
      const groups = new Map<string, { add: string[]; remove: string[]; ids: string[] }>();
      for (const l of list.filter(l => l.kind === 'apply')) {
        const add = l.before!.filter(x => !l.after!.includes(x)), remove = l.after!.filter(x => !l.before!.includes(x));
        const key = JSON.stringify([add, remove]);
        if (!groups.has(key)) groups.set(key, { add, remove, ids: [] });
        groups.get(key)!.ids.push(l.id!);
      }
      let left = list.length;
      try {
        for (const l of list.filter(l => l.kind === 'trash')) {
          try { await provider.untrash(l.id!, signal); restored++; }
          catch (error) { if (accountError(error) === 'gmail_auth_expired') throw error; failed++; }
          left--;
        }
        for (const g of groups.values()) {
          if (g.add.length || g.remove.length) {
            try { await provider.modify(g.ids, g.add, g.remove, signal); }
            catch (error) { if (accountError(error) === 'gmail_auth_expired') throw error; failed += g.ids.length; left -= g.ids.length; continue; }
          }
          restored += g.ids.length; left -= g.ids.length;
        }
      } catch (error) { errors[account] = (error as GmailError).code; failed += left; }
    }
    if (!restored && Object.keys(errors).length >= byAccount.size) throw new GmailError(Object.values(errors)[0] as GmailErrorCode);
    await this.append([{ at: this.o.now(), batch: last.batch, kind: 'undo' }]);
    return { status: 'undone', restored, failed, ...(Object.keys(errors).length ? { errors } : {}) };
  }

  /** Admin: the latest batches (counts and accounts only), newest first. */
  async recent(limit = 10) {
    const lines = await this.log(), undone = new Set(lines.filter(l => l.kind === 'undo').map(l => l.batch));
    const batches = new Map<string, { batch: string; at: number; kind: string; count: number; undone: boolean; levels: Record<string, number>; accounts: string[] }>();
    for (const l of lines) {
      if (l.kind === 'undo' || !l.id) continue;
      const b = batches.get(l.batch) ?? { batch: l.batch, at: l.at, kind: l.kind, count: 0, undone: undone.has(l.batch), levels: {}, accounts: [] };
      b.count++; if (l.level) b.levels[l.level] = (b.levels[l.level] ?? 0) + 1;
      const account = await this.lineAccount(l);
      if (account && !b.accounts.includes(account)) b.accounts.push(account);
      batches.set(l.batch, b);
    }
    return [...batches.values()].reverse().slice(0, limit);
  }
}
