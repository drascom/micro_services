import { DatabaseSync } from 'node:sqlite';
import { lstat, mkdir, readdir, realpath, stat } from 'node:fs/promises';
import { homedir } from 'node:os';
import { setTimeout as delay } from 'node:timers/promises';
import path from 'node:path';
import { expandHome, type Config } from './config.ts';
import { documentText, queryText, type EmbedInput, type Embedder } from './embeddings.ts';
import { chunkText, extractText, kindOf, mediaParts, type FileKind } from './files-extract.ts';
import { activity } from './activity.ts';

/**
 * Local file index for `file_search`/`file_read`/`file_organize`: SQLite (`data/files-index.sqlite`) with one row per
 * file and its chunk vectors (a "card" from name/path/date, text chunks, image/frames/audio segments). Search is
 * brute-force cosine in memory. Nothing here logs file names or contents; activity carries counts and durations only.
 */
export type IndexSettings = Pick<Config, 'file_roots' | 'file_skip_dirs' | 'file_max_mb' | 'file_max_chunks' | 'embedding_dims' | 'embedding_model' | 'embedding_mmproj'>;
export type ChunkKind = 'card' | 'text' | 'image' | 'frame' | 'audio';
export type SearchHit = { path: string; name: string; modified: string; kind: FileKind; score: number; snippet: string; chunk?: number };
export type IndexStatus = { files: number; found: number; chunks: number; running: boolean; last_run: number | null; last_duration_ms: number | null;
  last_indexed: number | null; last_error: string | null; multimodal: boolean };

/** Packages and app bundles are opaque: never walked into. */
const BUNDLE = /\.(app|photoslibrary|photolibrary|musiclibrary|tvlibrary|imovielibrary|fcpbundle|lrdata|lrlibrary|bundle|framework|plugin|kext|pkg|xcodeproj|xcworkspace|xcassets|playground|band|logicx|rtfd|pages|numbers|key|sparsebundle|dmg)$/i;
export const MAX_FILES = 50_000;
const CHUNK = 1500;
export type Found = { mtime: number; size: number; depth: number };

/** Lowercase, no diacritics (ı/İ too), separators (`_ - .` …) → single spaces. */
export function normalizeName(value: string): string {
  return value.toLocaleLowerCase('en').replace(/ı/g, 'i').normalize('NFKD').replace(/\p{M}/gu, '')
    .replace(/([\p{L}])(?=\p{N})|([\p{N}])(?=\p{L})/gu, '$1$2 ').replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
}
const queryTokens = (query: string) => [...new Set(normalizeName(query).split(' ').filter(Boolean))];
/**
 * How well a file name matches the query words (0-1, share of query characters matched): a name word starts with the
 * token, or the name without separators contains a longer token (`mlphealth`, `uk014`). Below 0.5 or without a
 * ≥ 3-character match: 0.
 */
export function nameScore(query: string | string[], name: string): number {
  const tokens = typeof query === 'string' ? queryTokens(query) : query;
  if (!tokens.length) return 0;
  const words = normalizeName(name).split(' '), compact = words.join('');
  let matched = 0, total = 0, strong = false;
  for (const token of tokens) {
    total += token.length;
    const hit = words.some(w => w.startsWith(token)) || ((token.length >= 4 || /^\d{3,}$/.test(token)) && compact.includes(token));
    if (hit) { matched += token.length; if (token.length >= 3) strong = true; }
  }
  const score = matched / total;
  return strong && score >= 0.5 ? score : 0;
}
const group = (kind: FileKind) => ['pdf', 'office', 'text'].includes(kind) ? 0 : ['image', 'audio', 'video'].includes(kind) ? 1 : 2;
/** Embedding order: documents (shallow, then recent) first, then images/audio/video (recent first), then the rest. */
export function priority(a: [string, Found], b: [string, Found]): number {
  const ga = group(kindOf(a[0])), gb = group(kindOf(b[0]));
  return ga - gb || (ga === 0 ? a[1].depth - b[1].depth : 0) || b[1].mtime - a[1].mtime;
}

export const displayPath = (absolute: string) => {
  const home = homedir();
  return absolute.startsWith(home + path.sep) ? absolute.slice(home.length + 1) : absolute;
};
const day = (ms: number) => new Date(ms).toISOString().slice(0, 10);
const blob = (vector: Float32Array) => Buffer.from(vector.buffer, vector.byteOffset, vector.byteLength);
const unblob = (value: Uint8Array) => new Float32Array(value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength));
export const dot = (a: Float32Array, b: Float32Array, offset = 0) => { let s = 0; for (let i = 0; i < a.length; i++) s += a[i] * b[offset + i]; return s; };
export function normalize(vector: Float32Array): Float32Array {
  let n = 0; for (const v of vector) n += v * v; n = Math.sqrt(n) || 1;
  for (let i = 0; i < vector.length; i++) vector[i] /= n;
  return vector;
}
const clip = (text: string, max: number) => { const t = text.replace(/\s+/g, ' ').trim(); return t.length > max ? t.slice(0, max - 1) + '…' : t; };
const clock = (seconds = 0) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;

type Cache = { dims: number; vectors: Float32Array; rows: { file: number; idx: number; kind: ChunkKind; at: number | null; text: string }[];
  files: Map<number, { path: string; mtime: number; kind: FileKind }> };
type Name = { path: string; name: string; mtime: number; kind: FileKind };

export class FileIndex {
  readonly settings: IndexSettings;
  readonly embedder: Embedder;
  readonly dbFile: string;
  private db?: DatabaseSync;
  private cache?: Cache;
  private names?: Name[];
  private running?: Promise<void>;
  private lastError: string | null = null;
  constructor(settings: IndexSettings, embedder: Embedder, dbFile: string) { this.settings = settings; this.embedder = embedder; this.dbFile = dbFile; }

  /** Configured roots, resolved (missing ones are skipped by the walk). */
  roots(): string[] { return this.settings.file_roots.map(r => path.resolve(expandHome(r))); }
  /** Existing roots as real paths (every stored path is a real path). */
  async realRoots(): Promise<{ root: string; real: string }[]> {
    const result = [];
    for (const root of this.roots()) { try { result.push({ root, real: await realpath(root) }); } catch {} }
    return result;
  }
  private signature() {
    return `${path.basename(this.settings.embedding_model)}|${this.settings.embedding_dims}|${this.embedder.multimodal ? 'mm' : 'text'}`;
  }
  private async open(): Promise<DatabaseSync> {
    if (this.db) return this.db;
    await mkdir(path.dirname(this.dbFile), { recursive: true, mode: 0o700 });
    const db = new DatabaseSync(this.dbFile);
    db.exec(`PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;
      CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
      CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, mtime REAL NOT NULL, size INTEGER NOT NULL, kind TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS chunks (file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE, idx INTEGER NOT NULL, kind TEXT NOT NULL,
        at REAL, text TEXT NOT NULL, vector BLOB NOT NULL, PRIMARY KEY (file_id, idx));
      CREATE TABLE IF NOT EXISTS catalog (path TEXT PRIMARY KEY, mtime REAL NOT NULL, size INTEGER NOT NULL, kind TEXT NOT NULL);`);
    const current = db.prepare("SELECT value FROM meta WHERE key = 'signature'").get() as { value?: string } | undefined;
    if (current?.value !== this.signature()) {
      // Another model, size or modality set: vectors are not comparable, start over.
      db.exec('DELETE FROM chunks; DELETE FROM files;');
      db.prepare("INSERT OR REPLACE INTO meta VALUES ('signature', ?)").run(this.signature());
    }
    this.db = db;
    return db;
  }
  close(): void { this.db?.close(); this.db = undefined; this.cache = undefined; this.names = undefined; }
  private meta(db: DatabaseSync, key: string): number | null {
    const row = db.prepare('SELECT value FROM meta WHERE key = ?').get(key) as { value?: string } | undefined;
    return row?.value ? Number(row.value) : null;
  }

  async status(): Promise<IndexStatus> {
    const db = await this.open();
    const count = (sql: string) => Number((db.prepare(sql).get() as { n: number }).n);
    return { files: count('SELECT COUNT(*) AS n FROM files'), found: count('SELECT COUNT(*) AS n FROM catalog'), chunks: count('SELECT COUNT(*) AS n FROM chunks'), running: Boolean(this.running),
      last_run: this.meta(db, 'last_run'), last_duration_ms: this.meta(db, 'last_duration_ms'), last_indexed: this.meta(db, 'last_indexed'),
      last_error: this.lastError, multimodal: this.embedder.multimodal };
  }

  /**
   * All indexable files under the roots, breadth-first (shallow files first under the cap): no hidden entries, packages,
   * symlinks, `file_skip_dirs` or code repositories (a folder with `.git`) unless the folder is itself a root.
   */
  async walk(signal: AbortSignal): Promise<Map<string, Found>> {
    const result = new Map<string, Found>();
    const skip = new Set(this.settings.file_skip_dirs.map(d => d.toLowerCase()));
    let level: string[] = [];
    for (const { real } of await this.realRoots()) { try { if ((await lstat(real)).isDirectory() && !level.includes(real)) level.push(real); } catch {} }
    const roots = new Set(level);
    for (let depth = 0; level.length && depth <= 20 && result.size < MAX_FILES; depth++) {
      const next: string[] = [];
      for (const directory of level) {
        if (result.size >= MAX_FILES) break;
        signal.throwIfAborted();
        let entries;
        try { entries = await readdir(directory, { withFileTypes: true }); } catch { continue; }
        if (!roots.has(directory) && entries.some(e => e.name === '.git')) continue;
        for (const entry of entries) {
          if (entry.name.startsWith('.') || entry.isSymbolicLink()) continue;
          const full = path.join(directory, entry.name);
          if (entry.isDirectory()) { if (!skip.has(entry.name.toLowerCase()) && !BUNDLE.test(entry.name) && !roots.has(full)) next.push(full); continue; }
          if (!entry.isFile() || result.size >= MAX_FILES) continue;
          try { const info = await stat(full); result.set(full, { mtime: info.mtimeMs, size: info.size, depth }); } catch {}
        }
      }
      level = next;
    }
    return result;
  }

  /** Every walked file by name (no embedding): exact-name search works right after the walk. */
  private async saveCatalog(found: Map<string, Found>): Promise<void> {
    const db = await this.open();
    db.exec('BEGIN');
    try {
      db.exec('DELETE FROM catalog');
      const insert = db.prepare('INSERT INTO catalog (path, mtime, size, kind) VALUES (?, ?, ?, ?)');
      for (const [file, info] of found) insert.run(file, info.mtime, info.size, kindOf(file));
      db.exec('COMMIT');
    } catch (error) { db.exec('ROLLBACK'); throw error; }
    this.names = undefined;
  }
  private async catalog(): Promise<Name[]> {
    if (this.names) return this.names;
    const db = await this.open();
    const rows = db.prepare('SELECT path, mtime, kind FROM catalog UNION SELECT path, mtime, kind FROM files WHERE path NOT IN (SELECT path FROM catalog)')
      .all() as { path: string; mtime: number; kind: FileKind }[];
    this.names = rows.map(r => ({ ...r, name: path.basename(r.path) }));
    return this.names;
  }
  /** Files whose name matches the query words (case/diacritic-insensitive), best first, then newest. */
  async nameMatches(query: string): Promise<(Name & { score: number })[]> {
    const tokens = queryTokens(query);
    if (!tokens.length) return [];
    return (await this.catalog()).flatMap(n => { const score = nameScore(tokens, n.name); return score ? [{ ...n, score }] : []; })
      .sort((a, b) => b.score - a.score || b.mtime - a.mtime);
  }

  /** Embedding inputs for one file (card first). Large or unknown files get the card only. */
  async inputs(file: string, info: { mtime: number; size: number }, signal: AbortSignal): Promise<{ kind: FileKind; parts: { kind: ChunkKind; at: number | null; text: string; input: EmbedInput }[] }> {
    const kind = kindOf(file), name = path.basename(file);
    const parts: { kind: ChunkKind; at: number | null; text: string; input: EmbedInput }[] = [];
    let body: string[] = [];
    const small = info.size <= this.settings.file_max_mb * 1024 * 1024;
    if (small && ['text', 'pdf', 'office'].includes(kind)) body = chunkText(await extractText(file, kind, CHUNK * this.settings.file_max_chunks, signal), CHUNK, this.settings.file_max_chunks);
    const card = `${displayPath(file)} | ${kind} | ${day(info.mtime)}` + (body[0] ? '\n' + body[0].slice(0, 500) : '');
    parts.push({ kind: 'card', at: null, text: '', input: { text: documentText(name, card) } });
    for (const chunk of body) parts.push({ kind: 'text', at: null, text: chunk, input: { text: documentText(name, chunk) } });
    if (this.embedder.multimodal && (kind === 'image' || kind === 'audio' || kind === 'video') && (small || kind !== 'image')) {
      for (const part of await mediaParts(file, kind, signal)) {
        const input: EmbedInput = part.audio ? { audio: part.audio, format: 'wav' } : { image: part.image!, mime: part.mime ?? 'image/jpeg' };
        if (part.audio && path.extname(file).toLowerCase() === '.mp3' && !part.audio.subarray(0, 4).equals(Buffer.from('RIFF'))) (input as { format: string }).format = 'mp3';
        parts.push({ kind: part.kind, at: part.at ?? null, text: '', input });
      }
    }
    return { kind, parts };
  }

  /** Embeds one file and replaces its rows (or inserts it). */
  async indexFile(file: string, info: { mtime: number; size: number }, signal: AbortSignal): Promise<number> {
    const db = await this.open();
    const { kind, parts } = await this.inputs(file, info, signal);
    let vectors: Float32Array[];
    try { vectors = await this.embedder.embed(parts.map(p => p.input), signal); }
    catch (error) {
      signal.throwIfAborted();
      // A media part the server rejects (odd codec): keep the file findable by its card and text.
      if (parts.every(p => 'text' in p.input)) throw error;
      const textual = parts.filter(p => 'text' in p.input);
      vectors = await this.embedder.embed(textual.map(p => p.input), signal);
      parts.splice(0, parts.length, ...textual);
    }
    db.exec('BEGIN');
    try {
      db.prepare('DELETE FROM files WHERE path = ?').run(file);
      const { lastInsertRowid } = db.prepare('INSERT INTO files (path, mtime, size, kind) VALUES (?, ?, ?, ?)').run(file, info.mtime, info.size, kind);
      const insert = db.prepare('INSERT INTO chunks (file_id, idx, kind, at, text, vector) VALUES (?, ?, ?, ?, ?, ?)');
      parts.forEach((p, i) => insert.run(lastInsertRowid, i, p.kind, p.at, p.text, blob(vectors[i])));
      db.exec('COMMIT');
    } catch (error) { db.exec('ROLLBACK'); throw error; }
    this.cache = undefined;
    return parts.length;
  }

  /**
   * Incremental pass: the name catalog is refreshed, new/changed files are embedded (documents first, see `priority`),
   * removed ones dropped. Every file is committed on its own, so an aborted pass resumes where it stopped. `busy()`
   * pauses work while the user's request runs. One pass at a time; a second call waits for the running one.
   */
  run(signal: AbortSignal, busy: () => boolean = () => false, full = false): Promise<void> {
    if (this.running) return this.running;
    this.running = this.pass(signal, busy, full).finally(() => { this.running = undefined; });
    return this.running;
  }
  private async pass(signal: AbortSignal, busy: () => boolean, full: boolean): Promise<void> {
    const started = performance.now(), db = await this.open();
    let indexed = 0, removed = 0, failed = 0, chunks = 0;
    try {
      if (full) { db.exec('DELETE FROM chunks; DELETE FROM files;'); this.cache = undefined; }
      const found = await this.walk(signal);
      await this.saveCatalog(found);
      const known = new Map((db.prepare('SELECT path, mtime, size FROM files').all() as { path: string; mtime: number; size: number }[]).map(r => [r.path, r]));
      for (const file of known.keys()) if (!found.has(file)) { db.prepare('DELETE FROM files WHERE path = ?').run(file); removed++; }
      if (removed) this.cache = undefined;
      // Unknown types (binaries, archives, …) stay in the name catalog only.
      const todo = [...found].filter(([file, info]) => { const old = known.get(file); return kindOf(file) !== 'other' && (!old || old.mtime !== info.mtime || old.size !== info.size); });
      for (const [file, info] of todo.sort(priority)) {
        while (busy()) await delay(1000, undefined, { signal });
        signal.throwIfAborted();
        try { chunks += await this.indexFile(file, info, signal); indexed++; }
        catch (error) { signal.throwIfAborted(); failed++; if (failed >= 20 && !indexed) throw error; }
      }
      this.lastError = null;
      db.prepare("INSERT OR REPLACE INTO meta VALUES ('last_indexed', ?)").run(String(found.size));
    } catch (error) {
      this.lastError = signal.aborted ? 'cancelled' : error instanceof Error ? error.message.slice(0, 200) : 'failed';
      throw error;
    } finally {
      const duration = Math.round(performance.now() - started);
      db.prepare("INSERT OR REPLACE INTO meta VALUES ('last_run', ?)").run(String(Date.now()));
      db.prepare("INSERT OR REPLACE INTO meta VALUES ('last_duration_ms', ?)").run(String(duration));
      activity('file_index_finished', { indexed, removed, failed, chunks, duration_ms: duration });
    }
  }

  private async load(): Promise<Cache> {
    if (this.cache) return this.cache;
    const db = await this.open(), dims = this.settings.embedding_dims;
    const files = new Map((db.prepare('SELECT id, path, mtime, kind FROM files').all() as { id: number; path: string; mtime: number; kind: FileKind }[])
      .map(r => [r.id, { path: r.path, mtime: r.mtime, kind: r.kind }]));
    const rows = db.prepare('SELECT file_id, idx, kind, at, text, vector FROM chunks ORDER BY file_id, idx').all() as
      { file_id: number; idx: number; kind: ChunkKind; at: number | null; text: string; vector: Uint8Array }[];
    const vectors = new Float32Array(rows.length * dims);
    rows.forEach((r, i) => vectors.set(unblob(r.vector).subarray(0, dims), i * dims));
    this.cache = { dims, vectors, files, rows: rows.map(r => ({ file: r.file_id, idx: r.idx, kind: r.kind, at: r.at, text: r.text })) };
    return this.cache;
  }

  /** Best chunk per file for an already embedded query vector, highest first. `within`: absolute folder filter. */
  async rank(query: Float32Array, within?: string): Promise<{ file: string; score: number; row: Cache['rows'][number]; meta: { mtime: number; kind: FileKind } }[]> {
    const cache = await this.load(), best = new Map<number, { score: number; i: number }>();
    for (let i = 0; i < cache.rows.length; i++) {
      const score = dot(query, cache.vectors, i * cache.dims), file = cache.rows[i].file, current = best.get(file);
      if (!current || score > current.score) best.set(file, { score, i });
    }
    return [...best].flatMap(([id, { score, i }]) => {
      const meta = cache.files.get(id);
      if (!meta || (within && !meta.path.startsWith(within + path.sep))) return [];
      return [{ file: meta.path, score, row: cache.rows[i], meta }];
    }).sort((a, b) => b.score - a.score);
  }

  /**
   * Name matches (catalog, no embedding needed) merged with semantic hits: a full name match scores 1, a partial one
   * 0.75-1; semantic hits keep their cosine. Without the embedding server, name matches alone are returned.
   */
  async search(query: string, limit: number, signal: AbortSignal): Promise<SearchHit[]> {
    const merged = new Map<string, { score: number; mtime: number; kind: FileKind; row?: Cache['rows'][number] }>();
    for (const n of await this.nameMatches(query)) merged.set(n.path, { score: 0.5 + 0.5 * n.score, mtime: n.mtime, kind: n.kind });
    try {
      if ((await this.load()).rows.length) {
        const [vector] = await this.embedder.embed([{ text: queryText(query) }], signal);
        for (const hit of await this.rank(vector)) {
          const current = merged.get(hit.file);
          if (current) { current.row = hit.row; current.score = Math.max(current.score, hit.score); }
          else merged.set(hit.file, { score: hit.score, mtime: hit.meta.mtime, kind: hit.meta.kind, row: hit.row });
        }
      }
    } catch (error) { signal.throwIfAborted(); if (!merged.size) throw error; }
    const hits: SearchHit[] = [];
    for (const [file, m] of [...merged].sort((a, b) => b[1].score - a[1].score || b[1].mtime - a[1].mtime)) {
      if (hits.length >= limit) break;
      try { await stat(file); } catch { continue; } // Moved or deleted since the last pass.
      const r = m.row;
      const snippet = !r ? '' : r.kind === 'text' ? clip(r.text, 200) : r.kind === 'image' ? '[image]' : r.kind === 'frame' ? `[video frame ${clock(r.at ?? 0)}]`
        : r.kind === 'audio' ? `[audio ${clock(r.at ?? 0)}]` : (await this.firstText(r.file)) ?? '';
      hits.push({ path: displayPath(file), name: path.basename(file), modified: day(m.mtime), kind: m.kind,
        score: Math.round(m.score * 100) / 100, snippet, ...(r?.kind === 'text' ? { chunk: r.idx - 1 } : {}) });
    }
    return hits;
  }
  private async firstText(file: number): Promise<string | undefined> {
    const cache = await this.load();
    const row = cache.rows.find(r => r.file === file && r.kind === 'text');
    return row ? clip(row.text, 200) : undefined;
  }

  /**
   * Content vector of a file: mean of its content chunks (text, picture, frames, audio), normalized; the card (path,
   * type, date) only when there is no content, since it makes unrelated files look alike.
   */
  private async contentVectors(match: (file: string) => boolean): Promise<Map<number, Float32Array>> {
    const cache = await this.load(), sums = new Map<number, { v: Float32Array; n: number; card?: number }>();
    cache.rows.forEach((r, i) => {
      const meta = cache.files.get(r.file);
      if (!meta || !match(meta.path)) return;
      const entry = sums.get(r.file) ?? { v: new Float32Array(cache.dims), n: 0 };
      if (r.kind === 'card') entry.card = i;
      else { for (let d = 0; d < cache.dims; d++) entry.v[d] += cache.vectors[i * cache.dims + d]; entry.n++; }
      sums.set(r.file, entry);
    });
    const result = new Map<number, Float32Array>();
    for (const [id, e] of sums) {
      if (e.n) result.set(id, normalize(e.v));
      else if (e.card !== undefined) result.set(id, cache.vectors.slice(e.card * cache.dims, (e.card + 1) * cache.dims));
    }
    return result;
  }
  async fileVector(file: string): Promise<Float32Array | undefined> {
    return [...(await this.contentVectors(p => p === file)).values()][0];
  }
  /** Content vectors of every indexed file under `folder` (recursive). */
  async folderVectors(folder: string): Promise<Float32Array[]> {
    return [...(await this.contentVectors(p => p.startsWith(folder + path.sep))).values()];
  }
  /** After a move: the same rows under the new path (no re-embedding). */
  async renamed(from: string, to: string): Promise<void> {
    const db = await this.open();
    try { db.prepare('UPDATE files SET path = ? WHERE path = ?').run(to, from); } catch { db.prepare('DELETE FROM files WHERE path = ?').run(from); }
    try { db.prepare('UPDATE catalog SET path = ? WHERE path = ?').run(to, from); } catch { db.prepare('DELETE FROM catalog WHERE path = ?').run(from); }
    this.cache = undefined; this.names = undefined;
  }

  /** Resolves a user/model path (`Documents/x.pdf`, `~/…` or absolute) strictly inside a root; real path, no symlink escape. */
  async resolve(value: string): Promise<{ file: string; root: string }> {
    if (typeof value !== 'string' || !value.trim() || value.length > 1000 || value.includes('\0')) throw new FileToolError('invalid_path');
    const raw = value.trim();
    const absolute = path.resolve(path.isAbsolute(expandHome(raw)) ? expandHome(raw) : path.join(homedir(), raw));
    for (const { root, real: realRoot } of await this.realRoots()) {
      const under = (base: string) => absolute === base || absolute.startsWith(base + path.sep);
      if (!under(root) && !under(realRoot)) continue;
      let real: string;
      try { real = await realpath(absolute); } catch { throw new FileToolError('not_found'); }
      if (real !== realRoot && !real.startsWith(realRoot + path.sep)) throw new FileToolError('outside_roots');
      if (path.relative(realRoot, real).split(path.sep).some(s => s.startsWith('.'))) throw new FileToolError('outside_roots');
      return { file: real, root: realRoot };
    }
    throw new FileToolError('outside_roots');
  }

  /** Text of one chunk of a file (≤4000 characters), read fresh from disk. Media files return metadata only. */
  async read(value: string, chunk = 0, signal: AbortSignal): Promise<Record<string, unknown>> {
    const { file } = await this.resolve(value);
    const info = await stat(file);
    if (!info.isFile()) throw new FileToolError('not_a_file');
    const kind = kindOf(file), base = { path: displayPath(file), name: path.basename(file), kind, modified: day(info.mtimeMs), size_kb: Math.ceil(info.size / 1024) };
    if (!['text', 'pdf', 'office'].includes(kind) || info.size > this.settings.file_max_mb * 1024 * 1024) return { ...base, text: null, note: 'No readable text.' };
    const chunks = chunkText(await extractText(file, kind, CHUNK * this.settings.file_max_chunks, signal), CHUNK, this.settings.file_max_chunks);
    // Two index chunks per read: ≤ 4000 characters for the model.
    const pages = [] as string[];
    for (let i = 0; i < chunks.length; i += 2) pages.push(chunks.slice(i, i + 2).join('\n\n').slice(0, 4000));
    const index = Math.max(0, Math.min(Math.floor(chunk / 2), Math.max(0, pages.length - 1)));
    return { ...base, part: index, parts: pages.length, text: pages[index] ?? '' };
  }
}

export const FILE_ERROR_CODES = ['invalid_path', 'not_found', 'outside_roots', 'not_a_file', 'index_empty', 'embedding_unavailable',
  'confirmation_invalid', 'confirmation_expired', 'confirmation_needs_reply', 'nothing_to_undo', 'invalid_request', 'cancelled'] as const;
export type FileErrorCode = typeof FILE_ERROR_CODES[number];
const MESSAGES: Record<FileErrorCode, string> = {
  invalid_path: 'Invalid path.', not_found: 'File or folder not found.', outside_roots: 'Only the folders enabled in Admin can be used.',
  not_a_file: 'Not a file.', index_empty: 'The file index is empty; indexing may still be running.',
  embedding_unavailable: 'The local embedding model is unavailable.', confirmation_invalid: 'Unknown or outdated confirm_token. Run propose again.',
  confirmation_expired: 'The confirmation expired. Run propose again.',
  confirmation_needs_reply: 'Not applied: show the plan, ask the user and stop; the token works only after their next message.',
  nothing_to_undo: 'There is no file move to undo.', invalid_request: 'Invalid parameters.', cancelled: 'The request was cancelled.'
};
export class FileToolError extends Error {
  readonly code: FileErrorCode;
  constructor(code: FileErrorCode, detail?: string) { super(detail ?? MESSAGES[code]); this.code = code; this.name = 'FileToolError'; }
}
