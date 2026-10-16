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

  async next(): Promise<Announcement | undefined> {
    const all: Announcement[] = [];
    for (const source of this.sources) all.push(...await source.announcements().catch(() => []));
    if (!all.length) return undefined;
    const sorted = all.map((a, i) => ({ a, i })).sort((x, y) => (x.a.priority ?? 1) - (y.a.priority ?? 1) || x.i - y.i).map(x => x.a);
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
