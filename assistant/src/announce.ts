/**
 * Idle announcement queue (`/announce/wait`): one message per idle moment, shared by updates and tasks.
 * Priority (lower first): task errors 0, update results/restart 1, task results 2, the daily update offer 3.
 * Identical texts waiting at once are said once (the others are acknowledged with it).
 */
export type Announcement = { id: string; text: string; restart?: boolean; priority?: number };
export interface AnnouncementSource { announcements(): Promise<Announcement[]>; shown(id: string): Promise<boolean>; }

export class AnnouncementQueue {
  private readonly sources: AnnouncementSource[];
  private duplicates = new Map<string, string[]>();
  constructor(sources: AnnouncementSource[]) { this.sources = sources; }

  /** Everything waiting, in priority order (identical texts kept: `shown` acknowledges them together). */
  async all(): Promise<Announcement[]> {
    const all: Announcement[] = [];
    for (const source of this.sources) all.push(...await source.announcements().catch(() => []));
    return all.map((a, i) => ({ a, i })).sort((x, y) => (x.a.priority ?? 1) - (y.a.priority ?? 1) || x.i - y.i).map(x => x.a);
  }

  async next(): Promise<Announcement | undefined> {
    const sorted = await this.all();
    if (!sorted.length) return undefined;
    const first = sorted[0];
    this.duplicates.set(first.id, sorted.slice(1).filter(a => a.text === first.text && !a.restart && !a.id.startsWith('offer-')).map(a => a.id));
    const { priority: _, ...rest } = first;
    return rest;
  }

  async shown(id: unknown): Promise<boolean> {
    if (typeof id !== 'string') return false;
    for (const source of this.sources) {
      if (await source.shown(id).catch(() => false)) {
        for (const other of this.duplicates.get(id) ?? []) for (const s of this.sources) if (await s.shown(other).catch(() => false)) break;
        this.duplicates.delete(id);
        return true;
      }
    }
    return false;
  }
}

/** Updates (src/updates.ts) as a source: its offer goes last, its run results and restart before task results. */
export function updatesSource(updates: { announcement(): Promise<Announcement | undefined>; shown(id: unknown): Promise<boolean> }): AnnouncementSource {
  return {
    announcements: async () => { const a = await updates.announcement(); return a ? [{ ...a, priority: a.id.startsWith('offer-') ? 3 : 1 }] : []; },
    shown: id => updates.shown(id)
  };
}

/** A short spoken answer to "anlatayım mı?": deterministic word lists, no model. Anything else = an ordinary request. */
const YES = new Set(['evet', 'söyle', 'söylesene', 'dinliyorum', 'anlat', 'anlatsana', 'tamam', 'olur', 'peki', 'hadi', 'buyur', 'buyrun',
  'tabii', 'tabi', 'yes', 'yeah', 'ok', 'okay', 'sure', 'tell']);
const NO = new Set(['hayır', 'sonra', 'değil', 'istemiyorum', 'meşgulüm', 'no', 'later', 'not']);
const FILLER = new Set(['şimdi', 'daha', 'lütfen', 'bakalım', 'ya', 'ki', 'de', 'da', 'ne', 'var', 'bana', 'me', 'now', 'please', 'go', 'ahead', 'it']);
export function classifyAnswer(text: string): 'yes' | 'no' | undefined {
  const words = text.toLocaleLowerCase('tr').replace(/[^\p{L}\s]/gu, ' ').split(/\s+/).filter(Boolean);
  if (!words.length || words.length > 5) return undefined;
  if (!words.every(w => YES.has(w) || NO.has(w) || FILLER.has(w))) return undefined;
  if (words.some(w => NO.has(w))) return 'no';
  return words.some(w => YES.has(w)) ? 'yes' : undefined;
}

export type GateState = { pending: number; action?: 'chime' | 'ask'; sound?: boolean; text?: string };
export type GateOptions = { now?: () => number; chime: () => boolean; snoozeMs: () => number; conversationMs?: number };

/**
 * Proactive speech asks first (bystanders): content is said only after the user accepts (ring click or "evet").
 * Idle: a soft chime + waiting ring; in a conversation (user spoke in the last ~2 min): a spoken question, no content.
 * At most two prompts per waiting batch (the second after the snooze interval); "sonra" snoozes; a new message may prompt again.
 */
export class ProactiveGate {
  private readonly queue: AnnouncementQueue;
  private readonly options: GateOptions;
  private readonly now: () => number;
  private prompted = new Set<string>();
  private prompts = 0;
  private nextAt = 0;
  private snoozedUntil = 0;
  private pending = 0;
  /** A chime or question is out and unanswered: a short yes/no in chat answers it. */
  awaiting = false;
  lastUser = Number.NEGATIVE_INFINITY;
  constructor(queue: AnnouncementQueue, options: GateOptions) { this.queue = queue; this.options = options; this.now = options.now ?? (() => Date.now()); }

  /** The user talked to the assistant (a chat request). */
  userActivity(): void { this.lastUser = this.now(); }
  /** Waiting update restart (no speech; the native app restarts at an idle moment). */
  async restart(): Promise<Announcement | undefined> {
    const found = (await this.queue.all()).find(a => a.restart);
    if (!found) return undefined;
    const { priority: _, ...rest } = found;
    return rest;
  }
  private async items(): Promise<Announcement[]> { return (await this.queue.all()).filter(a => !a.restart); }

  /** `busy`: a request, task or unload runs (nothing is offered; the count is the last known one). */
  async poll(busy: boolean): Promise<GateState> {
    if (busy) return { pending: this.pending };
    const items = await this.items(), now = this.now();
    this.pending = new Set(items.map(a => a.text)).size;
    if (!items.length) { this.prompted.clear(); this.prompts = 0; this.nextAt = 0; this.awaiting = false; return { pending: 0 }; }
    if (items.some(a => !this.prompted.has(a.id)) && now >= this.snoozedUntil) { this.prompts = 0; this.nextAt = 0; }
    if (this.prompts >= 2 || now < this.nextAt || now < this.snoozedUntil) return { pending: this.pending };
    for (const a of items) this.prompted.add(a.id);
    this.prompts++; this.nextAt = now + this.options.snoozeMs(); this.awaiting = true;
    if (now - this.lastUser < (this.options.conversationMs ?? 120_000))
      return { pending: this.pending, action: 'ask', text: `Sana söylemem gereken ${this.pending === 1 ? 'bir' : this.pending} şey var, anlatayım mı?` };
    return { pending: this.pending, action: 'chime', sound: this.options.chime() };
  }

  /** Accepted: every waiting message (identical texts once), acknowledged at once. */
  async accept(): Promise<string | undefined> {
    const items = await this.items();
    this.awaiting = false; this.prompts = 0; this.nextAt = 0; this.snoozedUntil = 0; this.pending = 0;
    if (!items.length) return undefined;
    for (const a of items) await this.queue.shown(a.id);
    return [...new Set(items.map(a => a.text))].join('\n');
  }

  /** "sonra": stays queued, silent until the snooze ends, then one more prompt at most. */
  decline(): void {
    const until = this.now() + this.options.snoozeMs();
    this.snoozedUntil = until; this.nextAt = until; this.prompts = 1; this.awaiting = false;
  }
}
