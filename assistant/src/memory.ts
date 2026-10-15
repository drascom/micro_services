import { DatabaseSync } from 'node:sqlite';
import { chmod, mkdir } from 'node:fs/promises';
import path from 'node:path';
import type { Config } from './config.ts';
import { documentText, queryText, type Embedder } from './embeddings.ts';
import { dot } from './files-index.ts';

/**
 * Assistant memory (`data/memory.sqlite`, see MEMORY.md): short durable items, never raw conversation text.
 * - assistant: how the user wants the assistant to work (the only kind a remote model ever sees).
 * - personal: stable facts about the user (local model only).
 * - episodes: one short summary per extracted conversation batch (local model only).
 * `topics` / `topic_fact` are the P3 seam (table and kind exist, nothing writes them yet).
 */
export type MemoryKind = 'assistant' | 'personal';
export type MemorySource = 'explicit' | 'extracted';
export type MemoryItem = { id: number; kind: MemoryKind; text: string; key: string | null; source: MemorySource; confidence: number;
  hits: number; pinned: boolean; created: number; updated: number };
export type Episode = { id: number; started: number; ended: number; summary: string };
export type SaveResult = { status: 'saved' | 'updated' | 'kept' | 'rejected'; id?: number; reason?: string };
export type MemorySettings = Pick<Config, 'memory_enabled' | 'memory_extract' | 'memory_chars' | 'memory_extract_every' | 'memory_idle_ms' |
  'embedding_model' | 'embedding_dims'>;

/** Character budgets of the system-prompt memory blocks (about 4 chars per token). */
export const MEMORY_LIMITS = { assistant: 500, personal: 200, retrieved: 600 } as const;
const CAPS = { assistant: 40, personal: 300, episodes: 2000 } as const;
const MERGE = { assistant: 0.88, personal: 0.92 } as const;
const DAY = 86_400_000;
const MAX_TEXT = 200, MAX_EPISODE = 300;

const blob = (vector: Float32Array) => Buffer.from(vector.buffer, vector.byteOffset, vector.byteLength);
const unblob = (value: Uint8Array) => new Float32Array(value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength));
const clean = (text: string, max: number) => { const t = text.replace(/\s+/g, ' ').trim(); return t.length > max ? t.slice(0, max - 1) + '…' : t; };
const fold = (text: string) => text.toLocaleLowerCase('tr').normalize('NFKD').replace(/\p{M}/gu, '').replace(/[^\p{L}\p{N}]+/gu, ' ').trim();

function luhn(digits: string): boolean {
  let sum = 0;
  for (let i = 0; i < digits.length; i++) {
    let d = Number(digits[digits.length - 1 - i]);
    if (i % 2) { d *= 2; if (d > 9) d -= 9; }
    sum += d;
  }
  return sum % 10 === 0;
}
/**
 * Privacy filter: true when the text must never be stored or sent to an extractor (passwords, one-time codes, keys,
 * IBAN/card numbers, long tokens, mail bodies/headers, vault).
 */
export function isPrivate(text: string): boolean {
  const lower = text.toLocaleLowerCase('tr');
  if (/(şifre|sifre|parola|password|passwd|passcode|\bpin\b|pin kodu|totp|\botp\b|2fa|iki adımlı|doğrulama kodu|dogrulama kodu|onay kodu|verification code|security code|güvenlik kodu|\btoken\b|api[ _-]?key|api anahtar|secret key|private key|gizli anahtar|\biban\b|kart numara|card number|kredi kartı|\bcvv\b|\bcvc\b|bitwarden|\bvault\b)/u.test(lower)) return true;
  if (/\b[a-z]{2}\d{2}(?:[ ]?[a-z0-9]{4}){3,}/i.test(text)) return true; // IBAN
  for (const match of text.matchAll(/(?:\d[ -]?){13,19}/g)) { const digits = match[0].replace(/\D/g, ''); if (digits.length >= 13 && luhn(digits)) return true; }
  if (/(?<![\d.,:/-])\d{6}(?![\d.,:/-])/.test(text)) return true; // one-time codes
  if (/(?=[A-Za-z0-9+/_-]*\d)(?=[A-Za-z0-9+/_-]*[A-Za-z])[A-Za-z0-9+/_-]{32,}/.test(text) || /\b[0-9a-f]{24,}\b/i.test(text)) return true; // keys, tokens, hashes
  if (/^\s*(konu|subject|kimden|from|kime|to|cc|tarih|date)\s*:/im.test(text) || /^\s*>/m.test(text) || /-----\s*(original message|orijinal ileti|forwarded message)/i.test(text)) return true;
  return false;
}

type Row = { id: number; kind: MemoryKind; text: string; key: string | null; source: MemorySource; confidence: number; hits: number; pinned: number;
  created: number; updated: number; last_used: number | null; vector: Uint8Array | null };
const item = (r: Row): MemoryItem => ({ id: r.id, kind: r.kind, text: r.text, key: r.key, source: r.source, confidence: r.confidence, hits: r.hits,
  pinned: Boolean(r.pinned), created: r.created, updated: r.updated });

export class MemoryStore {
  readonly settings: MemorySettings;
  readonly embedder?: Embedder;
  readonly dbFile: string;
  private db?: DatabaseSync;
  /** A background embedding-server warm-up started by a retrieval that found it down. */
  private warming?: Promise<unknown>;
  now: () => number = Date.now;
  constructor(settings: MemorySettings, embedder: Embedder | undefined, dbFile: string) { this.settings = settings; this.embedder = embedder; this.dbFile = dbFile; }

  private signature() { return `${path.basename(this.settings.embedding_model)}|${this.settings.embedding_dims}`; }
  async open(): Promise<DatabaseSync> {
    if (this.db) return this.db;
    if (this.dbFile !== ':memory:') await mkdir(path.dirname(this.dbFile), { recursive: true, mode: 0o700 });
    const db = new DatabaseSync(this.dbFile);
    db.exec(`PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;
      CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
      CREATE TABLE IF NOT EXISTS topics (id INTEGER PRIMARY KEY, title TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','done')), updated REAL NOT NULL, vector BLOB);
      CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('assistant','personal','topic_fact')),
        topic_id INTEGER REFERENCES topics(id) ON DELETE CASCADE, text TEXT NOT NULL, key TEXT,
        source TEXT NOT NULL CHECK (source IN ('explicit','extracted')), confidence REAL NOT NULL, hits INTEGER NOT NULL DEFAULT 0,
        pinned INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL, last_used REAL, vector BLOB);
      CREATE TABLE IF NOT EXISTS episodes (id INTEGER PRIMARY KEY, started REAL NOT NULL, ended REAL NOT NULL, topic_id INTEGER,
        summary TEXT NOT NULL, vector BLOB);`);
    if (this.dbFile !== ':memory:') await chmod(this.dbFile, 0o600).catch(() => {});
    const current = db.prepare("SELECT value FROM meta WHERE key = 'signature'").get() as { value?: string } | undefined;
    if (current?.value !== this.signature()) {
      // Another embedding model or size: keep the text, embed again later.
      db.exec('UPDATE items SET vector = NULL; UPDATE episodes SET vector = NULL; UPDATE topics SET vector = NULL;');
      db.prepare("INSERT OR REPLACE INTO meta VALUES ('signature', ?)").run(this.signature());
    }
    this.db = db;
    this.prune();
    return db;
  }
  close(): void { this.db?.close(); this.db = undefined; }

  /** One vector per text, or undefined when the embedding server is unavailable (memory still works by text/key). */
  private async vectors(texts: string[], signal: AbortSignal, timeoutMs = 20_000): Promise<Float32Array[] | undefined> {
    if (!this.embedder || !texts.length) return undefined;
    try {
      const result = await this.embedder.embed(texts.map(text => ({ text })), AbortSignal.any([signal, AbortSignal.timeout(timeoutMs)]));
      return result.every(v => v?.length === this.settings.embedding_dims) ? result : undefined;
    } catch { signal.throwIfAborted(); return undefined; }
  }
  /** Rows saved while the embedding server was down get their vectors once it answers again. */
  private async backfill(signal: AbortSignal): Promise<void> {
    const db = await this.open();
    const items = db.prepare('SELECT id, kind, text FROM items WHERE vector IS NULL LIMIT 32').all() as { id: number; kind: string; text: string }[];
    const episodes = db.prepare('SELECT id, summary FROM episodes WHERE vector IS NULL LIMIT 32').all() as { id: number; summary: string }[];
    const vectors = await this.vectors([...items.map(r => documentText(r.kind, r.text)), ...episodes.map(r => documentText('episode', r.summary))], signal);
    if (!vectors) return;
    items.forEach((r, i) => db.prepare('UPDATE items SET vector = ? WHERE id = ?').run(blob(vectors[i]), r.id));
    episodes.forEach((r, i) => db.prepare('UPDATE episodes SET vector = ? WHERE id = ?').run(blob(vectors[items.length + i]), r.id));
  }

  /** Expiry and caps: unused extracted rows go first; explicit and pinned rows are never dropped by age. */
  prune(): void {
    const db = this.db; if (!db) return;
    const now = this.now();
    db.prepare("DELETE FROM items WHERE kind = 'assistant' AND source = 'extracted' AND pinned = 0 AND hits = 0 AND updated < ?").run(now - 90 * DAY);
    db.prepare("DELETE FROM items WHERE kind = 'personal' AND source = 'extracted' AND pinned = 0 AND COALESCE(last_used, updated) < ?").run(now - 180 * DAY);
    db.prepare('DELETE FROM episodes WHERE ended < ?').run(now - 180 * DAY);
    for (const kind of ['assistant', 'personal'] as const)
      db.prepare(`DELETE FROM items WHERE id IN (SELECT id FROM items WHERE kind = ? ORDER BY pinned DESC, source = 'explicit' DESC, hits DESC, updated DESC LIMIT -1 OFFSET ?)`)
        .run(kind, CAPS[kind]);
    db.prepare('DELETE FROM episodes WHERE id IN (SELECT id FROM episodes ORDER BY ended DESC LIMIT -1 OFFSET ?)').run(CAPS.episodes);
  }

  async list(): Promise<{ items: MemoryItem[]; episodes: Episode[] }> {
    const db = await this.open();
    const items = (db.prepare("SELECT * FROM items WHERE kind IN ('assistant','personal') ORDER BY kind, pinned DESC, updated DESC").all() as Row[]).map(item);
    const episodes = db.prepare('SELECT id, started, ended, summary FROM episodes ORDER BY ended DESC LIMIT 200').all() as Episode[];
    return { items, episodes };
  }

  /**
   * Saves one item. Contradiction: the same `key` (or a near-duplicate by cosine/text) replaces the old text; an
   * extracted item never overwrites an explicit one (manual beats auto). Private text is rejected.
   */
  async save(input: { kind: MemoryKind; text: string; key?: string | null; source: MemorySource; confidence?: number; pinned?: boolean }, signal: AbortSignal): Promise<SaveResult> {
    if (input.kind !== 'assistant' && input.kind !== 'personal') return { status: 'rejected', reason: 'invalid_kind' };
    const text = clean(input.text, MAX_TEXT);
    if (!text) return { status: 'rejected', reason: 'empty' };
    if (isPrivate(text) || isPrivate(input.key ?? '')) return { status: 'rejected', reason: 'private' };
    const key = input.key ? clean(input.key, 60).toLocaleLowerCase('en') : null;
    const db = await this.open();
    const [vector] = await this.vectors([documentText(input.kind, text)], signal) ?? [];
    if (vector) await this.backfill(signal).catch(() => {});
    const rows = db.prepare('SELECT * FROM items WHERE kind = ?').all(input.kind) as Row[];
    const folded = fold(text);
    let match: Row | undefined = key ? rows.find(r => r.key === key) : undefined;
    // Two different slots never merge, however similar the wording.
    const other = (r: Row) => Boolean(key && r.key && r.key !== key);
    match ??= rows.find(r => !other(r) && fold(r.text) === folded);
    if (!match && vector) {
      let best: number = MERGE[input.kind];
      for (const r of rows) if (r.vector && !other(r)) { const s = dot(vector, unblob(r.vector)); if (s >= best) { best = s; match = r; } }
    }
    const now = this.now(), explicit = input.source === 'explicit';
    const confidence = explicit ? 1 : Math.max(0, Math.min(1, (input.confidence ?? 0.8) * (input.kind === 'assistant' ? 0.8 : 1)));
    if (match) {
      if (match.source === 'explicit' && !explicit) {
        db.prepare('UPDATE items SET hits = hits + 1 WHERE id = ?').run(match.id);
        return { status: 'kept', id: match.id };
      }
      // A repeated extracted rule earns trust (second hit promotes it into the always-on block).
      const next = explicit ? 1 : Math.min(1, Math.max(match.confidence, confidence) + 0.15);
      db.prepare('UPDATE items SET text = ?, key = COALESCE(?, key), source = ?, confidence = ?, hits = hits + 1, pinned = MAX(pinned, ?), updated = ?, vector = ? WHERE id = ?')
        .run(text, key, explicit ? 'explicit' : match.source, next, input.pinned ? 1 : 0, now, vector ? blob(vector) : null, match.id);
      return { status: 'updated', id: match.id };
    }
    const result = db.prepare('INSERT INTO items (kind, text, key, source, confidence, pinned, created, updated, vector) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)')
      .run(input.kind, text, key, input.source, confidence, input.pinned ? 1 : 0, now, now, vector ? blob(vector) : null);
    this.prune();
    return { status: 'saved', id: Number(result.lastInsertRowid) };
  }

  /** Admin edit (the user's own text: explicit from now on). */
  async edit(id: number, change: { text?: string; pinned?: boolean }, signal: AbortSignal): Promise<SaveResult> {
    const db = await this.open();
    const row = db.prepare('SELECT * FROM items WHERE id = ?').get(id) as Row | undefined;
    if (!row) return { status: 'rejected', reason: 'not_found' };
    if (change.pinned !== undefined) db.prepare('UPDATE items SET pinned = ? WHERE id = ?').run(change.pinned ? 1 : 0, id);
    if (change.text !== undefined) {
      const text = clean(change.text, MAX_TEXT);
      if (!text) return { status: 'rejected', reason: 'empty' };
      if (isPrivate(text)) return { status: 'rejected', reason: 'private' };
      const [vector] = await this.vectors([documentText(row.kind, text)], signal) ?? [];
      db.prepare("UPDATE items SET text = ?, source = 'explicit', confidence = 1, updated = ?, vector = ? WHERE id = ?").run(text, this.now(), vector ? blob(vector) : null, id);
    }
    return { status: 'updated', id };
  }
  async remove(id: number): Promise<boolean> {
    return Number((await this.open()).prepare('DELETE FROM items WHERE id = ?').run(id).changes) > 0;
  }
  async removeEpisode(id: number): Promise<boolean> {
    return Number((await this.open()).prepare('DELETE FROM episodes WHERE id = ?').run(id).changes) > 0;
  }
  /** "Tümünü sil": rows of a kind (or episodes) go, then the file is compacted. */
  async clear(kind: MemoryKind | 'episodes'): Promise<number> {
    const db = await this.open();
    const changes = kind === 'episodes' ? db.prepare('DELETE FROM episodes').run().changes : db.prepare('DELETE FROM items WHERE kind = ?').run(kind).changes;
    db.exec('VACUUM');
    return Number(changes);
  }

  /** Scores items against a query: text containment first, cosine when the embedding server answers. */
  private async rank(query: string, kinds: MemoryKind[], signal: AbortSignal): Promise<{ row: Row; score: number }[]> {
    const db = await this.open();
    const rows = db.prepare(`SELECT * FROM items WHERE kind IN (${kinds.map(() => '?').join(',')})`).all(...kinds) as Row[];
    const words = fold(query).split(' ').filter(w => w.length >= 3);
    const [vector] = await this.vectors([queryText(query)], signal, 10_000) ?? [];
    return rows.map(row => {
      const text = fold(row.text);
      const contained = words.length > 0 && words.every(w => text.includes(w)) ? 0.85 + 0.1 * Math.min(1, words.length / 3) : 0;
      return { row, score: Math.max(contained, row.vector && vector ? dot(vector, unblob(row.vector)) : 0) };
    }).sort((a, b) => b.score - a.score);
  }
  /** Forget: deletes the best match when it is clear (score ≥ 0.8), else returns up to 3 candidates. */
  async forget(query: string, signal: AbortSignal): Promise<{ deleted?: MemoryItem; candidates: MemoryItem[] }> {
    const ranked = (await this.rank(query, ['assistant', 'personal'], signal)).slice(0, 3);
    const best = ranked[0];
    if (best && best.score >= 0.8) { await this.remove(best.row.id); return { deleted: item(best.row), candidates: [] }; }
    return { candidates: ranked.filter(r => r.score >= 0.3).map(r => item(r.row)) };
  }

  /**
   * Always-on block (deterministic order, stable prefix for the llama cache): assistant rules ≤ 500 chars; with the
   * local model also pinned personal facts ≤ 200 chars. A remote model gets the assistant rules only.
   */
  async block(local: boolean): Promise<string> {
    if (!this.settings.memory_enabled) return '';
    const db = await this.open();
    const take = (rows: { text: string }[], limit: number) => {
      const lines: string[] = []; let size = 0;
      for (const r of rows) { const line = '- ' + r.text; if (size + line.length + 1 > limit) continue; lines.push(line); size += line.length + 1; }
      return lines;
    };
    const rules = take(db.prepare("SELECT text FROM items WHERE kind = 'assistant' AND (source = 'explicit' OR pinned = 1 OR confidence >= 0.75) " +
      "ORDER BY pinned DESC, source = 'explicit' DESC, hits DESC, id ASC").all() as { text: string }[], MEMORY_LIMITS.assistant);
    const facts = local ? take(db.prepare("SELECT text FROM items WHERE kind = 'personal' AND pinned = 1 ORDER BY id ASC").all() as { text: string }[], MEMORY_LIMITS.personal) : [];
    if (!rules.length && !facts.length) return '';
    return ['Memory (personal instructions above win):', ...(rules.length ? ['How to work with the user:', ...rules] : []),
      ...(facts.length ? ['About the user:', ...facts] : [])].join('\n');
  }

  /**
   * Per-request recall (local model only): top 4 personal facts and conversation summaries with cosine ≥ 0.55,
   * ≤ 600 chars. Never waits for an embedding-server start: when it is down, recall is skipped and the server warms up.
   */
  async retrieve(query: string, signal: AbortSignal): Promise<string> {
    if (!this.settings.memory_enabled || !this.embedder) return '';
    const up = this.embedder.healthy ? await this.embedder.healthy(AbortSignal.any([signal, AbortSignal.timeout(300)])).catch(() => false) : true;
    if (!up) {
      this.warming ??= this.embedder.embed([{ text: queryText('warm up') }], AbortSignal.timeout(120_000)).catch(() => {}).finally(() => { this.warming = undefined; });
      return '';
    }
    const [vector] = await this.vectors([queryText(query.slice(0, 1000))], signal, 2000) ?? [];
    if (!vector) return '';
    const db = await this.open();
    const scored: { kind: 'item' | 'episode'; id: number; text: string; score: number }[] = [];
    for (const r of db.prepare("SELECT id, text, vector FROM items WHERE kind = 'personal' AND pinned = 0 AND confidence >= 0.75 AND vector IS NOT NULL").all() as Row[])
      scored.push({ kind: 'item', id: r.id, text: r.text, score: dot(vector, unblob(r.vector!)) });
    for (const r of db.prepare('SELECT id, summary, ended, vector FROM episodes WHERE vector IS NOT NULL').all() as { id: number; summary: string; ended: number; vector: Uint8Array }[])
      scored.push({ kind: 'episode', id: r.id, text: `(${new Date(r.ended).toISOString().slice(0, 10)}) ${r.summary}`, score: dot(vector, unblob(r.vector)) });
    const top = scored.filter(s => s.score >= 0.55).sort((a, b) => b.score - a.score).slice(0, 4);
    const lines: string[] = []; let size = 0;
    for (const s of top) {
      const line = '- ' + s.text; if (size + line.length + 1 > MEMORY_LIMITS.retrieved - 40) continue;
      lines.push(line); size += line.length + 1;
      if (s.kind === 'item') db.prepare('UPDATE items SET hits = hits + 1, last_used = ? WHERE id = ?').run(this.now(), s.id);
    }
    return lines.length ? 'Related memory (reference only):\n' + lines.join('\n') : '';
  }

  async addEpisode(summary: string, started: number, ended: number, signal: AbortSignal): Promise<boolean> {
    const text = clean(summary, MAX_EPISODE);
    if (!text || isPrivate(text)) return false;
    const db = await this.open();
    const [vector] = await this.vectors([documentText('episode', text)], signal) ?? [];
    db.prepare('INSERT INTO episodes (started, ended, summary, vector) VALUES (?, ?, ?, ?)').run(started, ended, text, vector ? blob(vector) : null);
    this.prune();
    return true;
  }
  /** Keys the extractor should reuse (remote extractor: assistant keys only). */
  async keys(local: boolean): Promise<string[]> {
    const db = await this.open();
    return (db.prepare(`SELECT DISTINCT key FROM items WHERE key IS NOT NULL ${local ? '' : "AND kind = 'assistant'"} ORDER BY updated DESC LIMIT 40`).all() as { key: string }[]).map(r => r.key);
  }
}

// ---------------------------------------------------------------------------------------------------------------
// Background extraction (P2)

/** One finished user turn. `localReply`: answered by the local model (may carry local-only memory). */
export type Turn = { user: string; assistant: string; offline: boolean; localReply: boolean; at: number };
/** Runs the extraction prompt; `local` true = local Gemma, false = the remote model. */
export type ExtractModel = (system: string, user: string, local: boolean, signal: AbortSignal) => Promise<string>;
/** The server's background lane: never during a request; a new user request aborts the holder. */
export type Lane = { acquire(signal: AbortSignal): Promise<{ signal: AbortSignal; release: () => void }> };

/** Intents/tools whose turns never reach an extractor (vault secrets, mail contents, local file contents). */
export const EXCLUDED_INTENTS = ['vault', 'email', 'mail_triage', 'files'];
export const excludedTool = (name: string) => /^(vault_|mail_|file_)/.test(name);

export function extractionPrompt(keys: string[]): string {
  return 'Extract durable memory from the conversation. Reply with JSON only:\n' +
    '{"items":[{"kind":"assistant|personal","key":"short.slot","text":"<=20 words","confidence":0-1}],"episode":"<=40 words"}\n' +
    'assistant = how the user wants the assistant to work. personal = stable facts about the user (people, places, devices, routines, likes).\n' +
    'Write items in the user\'s language. Skip one-off requests, guesses, mail/web content, passwords, codes and account numbers. Empty list is fine.' +
    (keys.length ? '\nKnown keys: ' + keys.join(', ').slice(0, 300) : '');
}
/** Tolerant parse of the extractor's reply (code fences, prose around the JSON, bad fields). */
export function parseExtraction(reply: string): { items: { kind: MemoryKind; key?: string; text: string; confidence: number }[]; episode: string } {
  const start = reply.indexOf('{'), end = reply.lastIndexOf('}');
  let value: { items?: unknown; episode?: unknown } = {};
  if (start >= 0 && end > start) { try { value = JSON.parse(reply.slice(start, end + 1)); } catch {} }
  const items = (Array.isArray(value.items) ? value.items : []).flatMap(raw => {
    const r = raw as Record<string, unknown>;
    if (!r || (r.kind !== 'assistant' && r.kind !== 'personal') || typeof r.text !== 'string' || !r.text.trim()) return [];
    const confidence = typeof r.confidence === 'number' && Number.isFinite(r.confidence) ? r.confidence : 0;
    return [{ kind: r.kind as MemoryKind, ...(typeof r.key === 'string' && r.key.trim() ? { key: r.key } : {}), text: r.text, confidence }];
  }).slice(0, 10);
  return { items, episode: typeof value.episode === 'string' ? value.episode : '' };
}

export class MemoryExtractor {
  readonly store: MemoryStore;
  readonly settings: MemorySettings;
  model?: ExtractModel;
  lane?: Lane;
  /** Current mode of the ring (true = Local/offline); defaults to the last turn's mode. */
  offline?: () => boolean;
  turns: Turn[] = [];
  private timer?: NodeJS.Timeout;
  private running?: AbortController;
  private lifecycle = new AbortController();
  /** Resolves when the current run ends (tests). */
  idle: Promise<void> = Promise.resolve();
  constructor(store: MemoryStore, settings: MemorySettings) { this.store = store; this.settings = settings; }

  /** Adds a finished turn (already filtered by intent/tools); private text never enters the buffer. */
  record(turn: Turn): void {
    if (!this.settings.memory_enabled || !this.settings.memory_extract) return;
    if (isPrivate(turn.user) || isPrivate(turn.assistant)) return;
    this.turns.push({ ...turn, user: clean(turn.user, 1500), assistant: clean(turn.assistant, 300) });
    if (this.turns.length > 20) this.turns.splice(0, this.turns.length - 20);
  }
  /** Schedules the next run: soon after N turns or a reset, else after the idle time. */
  schedule(soon = false): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = undefined;
    if (!this.turns.length || !this.model || this.lifecycle.signal.aborted) return;
    const delay = soon || this.turns.length >= this.settings.memory_extract_every ? 3000 : this.settings.memory_idle_ms;
    this.timer = setTimeout(() => { this.timer = undefined; void this.run(); }, delay);
    this.timer.unref();
  }
  /** A new user request: the running extraction stops (its turns stay buffered) and nothing starts until it ends. */
  pause(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = undefined;
    this.running?.abort();
  }
  close(): void { this.pause(); this.lifecycle.abort(); }

  /** One extraction pass over the buffered turns the current mode may use. */
  run(): Promise<void> {
    const done = this.pass().catch(() => {});
    this.idle = done;
    return done;
  }
  private async pass(): Promise<void> {
    if (!this.model || this.running || !this.turns.length) return;
    const controller = new AbortController(); this.running = controller;
    let slot: { signal: AbortSignal; release: () => void } | undefined;
    try {
      const signal = AbortSignal.any([controller.signal, this.lifecycle.signal]);
      if (this.lane) slot = await this.lane.acquire(signal);
      const live = slot ? AbortSignal.any([signal, slot.signal]) : signal;
      const local = this.offline ? this.offline() : this.turns.at(-1)!.offline;
      // Offline-mode turns stay on this Mac: they wait for a local run. A remote run never sees local replies.
      const batch = this.turns.filter(t => local || !t.offline);
      if (!batch.length) return;
      const text = batch.map(t => `User: ${t.user}` + (t.assistant && (local || !t.localReply) ? `\nAssistant: ${t.assistant}` : '')).join('\n\n');
      const reply = await this.model(extractionPrompt(await this.store.keys(local)), text, local, live);
      live.throwIfAborted();
      const result = parseExtraction(reply);
      for (const candidate of result.items) {
        if (candidate.confidence < 0.75) continue;
        await this.store.save({ ...candidate, source: 'extracted' }, live);
      }
      if (result.episode) await this.store.addEpisode(result.episode, batch[0].at, batch.at(-1)!.at, live);
      this.turns = this.turns.filter(t => !batch.includes(t));
    } finally {
      slot?.release();
      if (this.running === controller) this.running = undefined;
    }
  }
}

/** Memory wiring for the orchestrator (one per backend process). */
export class MemoryService {
  readonly store: MemoryStore;
  readonly extractor: MemoryExtractor;
  readonly settings: MemorySettings;
  constructor(settings: MemorySettings, embedder: Embedder | undefined, dbFile: string) {
    this.settings = settings;
    this.store = new MemoryStore(settings, embedder, dbFile);
    this.extractor = new MemoryExtractor(this.store, settings);
  }
  get enabled(): boolean { return this.settings.memory_enabled; }
  close(): void { this.extractor.close(); this.store.close(); }
}

let registered: MemoryService | undefined;
/** The backend's memory (set by main); the harness reads it for the memory tools. */
export function registerMemory(service: MemoryService | undefined): void { registered = service; }
export function registeredMemory(): MemoryService | undefined { return registered; }

/** "hatırla / unutma / aklında tut / unut / remember / forget": preloads the two memory tools (not "hatırlat" = reminders). */
export function memoryRequest(text: string): boolean {
  return /(^|[^\p{L}])(hat[ıi]rla(?!t)\p{L}*|akl[ıi]nda tut|akl[ıi]na (yaz|kaydet|not et)|unutma\p{L}*|unut(?!ma)\p{L}*|haf[ıi]za\p{L}*|remember|forget|memoriz\p{L}*)/iu
    .test(text.toLocaleLowerCase('tr'));
}
