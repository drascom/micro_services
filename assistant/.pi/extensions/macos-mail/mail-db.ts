// Read-only Apple Mail transport over Mail's on-disk store (no Apple Events, Mail.app may be closed).
// Needs Full Disk Access for the process: ~/Library/Mail/V*/MailData/Envelope Index (SQLite, opened
// read-only), ~/Library/Accounts/Accounts4.sqlite (account names) and the `.emlx` message files.
// Speaks the same request/response JSON as mail-reader.js, so MailClient is unchanged. When the
// store cannot be used (no permission, missing files, unknown schema) it throws MailStoreUnavailable
// and `fallbackTransport` hands the request to the JXA bridge instead.
import { DatabaseSync } from 'node:sqlite';
import { readdir, readFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import path from 'node:path';
import { MailError, osascriptTransport, type MailTransport } from './client.ts';
import { emlxMessage, messageText } from './mime.ts';
import type { BridgeRequest } from './schemas.ts';

/** The store cannot be used at all; the caller should fall back to another transport. */
export class MailStoreUnavailable extends Error {
  constructor(reason: string) { super(reason); this.name = 'MailStoreUnavailable'; }
}

export type MailStorePaths = { mailRoot?: string; accountsDb?: string };

const REQUIRED: Record<string, string[]> = {
  messages: ['ROWID', 'global_message_id', 'sender', 'subject_prefix', 'subject', 'date_received', 'mailbox', 'read', 'flagged', 'deleted', 'size'],
  message_global_data: ['ROWID', 'message_id_header'],
  subjects: ['ROWID', 'subject'],
  addresses: ['ROWID', 'address', 'comment'],
  mailboxes: ['ROWID', 'url']
};
const ACCOUNTS_REQUIRED: Record<string, string[]> = {
  ZACCOUNT: ['Z_PK', 'ZIDENTIFIER', 'ZACCOUNTDESCRIPTION', 'ZUSERNAME', 'ZPARENTACCOUNT', 'ZACCOUNTTYPE'],
  ZACCOUNTTYPE: ['Z_PK', 'ZIDENTIFIER']
};
const str = (value: unknown, max: number) => typeof value === 'string' ? value.slice(0, max) : '';

function open(file: string, required: Record<string, string[]>): DatabaseSync {
  let db: DatabaseSync;
  // Plain read-only open (not immutable) so the WAL with Mail's latest writes is included.
  try { db = new DatabaseSync(file, { readOnly: true, timeout: 2000 }); }
  catch { throw new MailStoreUnavailable('open'); }
  try {
    for (const [table, columns] of Object.entries(required)) {
      const have = new Set(db.prepare(`PRAGMA table_info("${table}")`).all().map(row => String(row.name)));
      if (!columns.every(column => have.has(column))) throw new MailStoreUnavailable('schema');
    }
    return db;
  } catch (error) { db.close(); throw error instanceof MailStoreUnavailable ? error : new MailStoreUnavailable('schema'); }
}

/** Newest ~/Library/Mail/V<n> holding an Envelope Index. */
async function findMailRoot(): Promise<string> {
  const base = path.join(homedir(), 'Library', 'Mail');
  let names: string[];
  try { names = await readdir(base); } catch { throw new MailStoreUnavailable('mail root'); }
  const versions = names.filter(name => /^V\d+$/.test(name)).sort((a, b) => Number(b.slice(1)) - Number(a.slice(1)));
  if (!versions.length) throw new MailStoreUnavailable('mail root');
  return path.join(base, versions[0]);
}

type Mailbox = { rowid: number; host: string; segments: string[] };
type Store = { db: DatabaseSync; root: string; accounts: Map<string, string[]>; mailboxes: Mailbox[]; labels: boolean };

function parseMailboxUrl(rowid: number, url: string): Mailbox | null {
  try {
    const parsed = new URL(url);
    if (parsed.protocol === 'local:' || !parsed.hostname) return null; // "On My Mac" is not a Mail account.
    const segments = parsed.pathname.split('/').filter(Boolean).map(decodeURIComponent);
    return segments.length ? { rowid, host: parsed.hostname.toUpperCase(), segments } : null;
  } catch { return null; }
}

/** Mail.app account name per store UUID: own description, else the parent (e.g. Google) account's, else user name. */
function accountNames(file: string, hosts: Set<string>): Map<string, string[]> {
  const db = open(file, ACCOUNTS_REQUIRED);
  try {
    const rows = db.prepare(`SELECT a.Z_PK pk, a.ZIDENTIFIER id, a.ZACCOUNTDESCRIPTION d, a.ZUSERNAME u, a.ZPARENTACCOUNT p, t.ZIDENTIFIER type
      FROM ZACCOUNT a LEFT JOIN ZACCOUNTTYPE t ON t.Z_PK = a.ZACCOUNTTYPE`).all();
    const byPk = new Map(rows.map(row => [row.pk, row]));
    const names = new Map<string, string[]>();
    for (const row of rows) {
      const id = str(row.id, 100).toUpperCase();
      if (!hosts.has(id) || /OnMyDevice/i.test(str(row.type, 200))) continue;
      const parent = row.p == null ? undefined : byPk.get(row.p);
      const name = (str(row.d, 200).trim() || str(parent?.d, 200).trim() || str(row.u, 200).trim());
      if (!name || /[\u0000-\u001f\u007f]/.test(name)) continue;
      names.set(name, [...(names.get(name) ?? []), id]);
    }
    return names;
  } catch (error) { throw error instanceof MailStoreUnavailable ? error : new MailStoreUnavailable('accounts'); }
  finally { db.close(); }
}

async function openStore(paths: MailStorePaths): Promise<Store> {
  const root = paths.mailRoot ?? await findMailRoot();
  const db = open(path.join(root, 'MailData', 'Envelope Index'), REQUIRED);
  try {
    const mailboxes = db.prepare('SELECT ROWID rowid, url FROM mailboxes').all()
      .map(row => parseMailboxUrl(Number(row.rowid), str(row.url, 4000))).filter((box): box is Mailbox => box !== null);
    const accounts = accountNames(paths.accountsDb ?? path.join(homedir(), 'Library', 'Accounts', 'Accounts4.sqlite'),
      new Set(mailboxes.map(box => box.host)));
    // Gmail keeps each message once (in All Mail); INBOX and other labels are rows in `labels`.
    const labels = db.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'labels'").get() !== undefined;
    return { db, root, accounts, mailboxes, labels };
  } catch (error) { db.close(); throw error instanceof MailStoreUnavailable ? error : new MailStoreUnavailable('store'); }
}

/** Same matching as the JXA bridge: exact account name; mailbox by name, case-insensitive (full path or last segment). */
function resolveMailbox(store: Store, account: unknown, mailbox: unknown): Mailbox {
  if (typeof account !== 'string' || !account || typeof mailbox !== 'string' || !mailbox) throw new MailError('invalid_request');
  const hosts = store.accounts.get(account);
  if (!hosts) throw new MailError('account_not_found');
  const wanted = mailbox.toLowerCase();
  const boxes = store.mailboxes.filter(box => hosts.includes(box.host));
  const box = boxes.find(b => b.segments.join('/') === mailbox) ?? boxes.find(b => b.segments.join('/').toLowerCase() === wanted) ??
    boxes.find(b => b.segments.at(-1)!.toLowerCase() === wanted);
  if (!box) throw new MailError('mailbox_not_found');
  return box;
}

const MESSAGE_SQL = `SELECT m.ROWID id, m.mailbox home, g.message_id_header mid, a.address address, a.comment comment, m.subject_prefix prefix,
  s.subject subject, m.date_received received, m.read read, m.flagged flagged, m.size size
  FROM messages m LEFT JOIN message_global_data g ON g.ROWID = m.global_message_id LEFT JOIN subjects s ON s.ROWID = m.subject
  LEFT JOIN addresses a ON a.ROWID = m.sender`;

type Row = Record<string, unknown>;
/** Message belongs to mailbox `?`: stored there or labelled with it (Gmail). */
const inMailbox = (store: Store) => store.labels
  ? '(m.mailbox = ?1 OR EXISTS (SELECT 1 FROM labels l WHERE l.message_id = m.ROWID AND l.mailbox_id = ?1))' : 'm.mailbox = ?1';
/** RFC Message-ID without angle brackets (as Mail's scripting `messageId` reports it). */
const messageId = (row: Row) => str(row.mid, 1000).trim().replace(/^<(.*)>$/s, '$1').slice(0, 998);
const sender = (row: Row) => {
  const address = str(row.address, 500), name = str(row.comment, 500).trim();
  return (name && address ? `${name} <${address}>` : name || address).slice(0, 1000);
};
const subject = (row: Row) => (str(row.prefix, 100) + str(row.subject, 2000)).slice(0, 2000);
const received = (row: Row) => new Date(Number(row.received) * 1000);

function list(store: Store, request: Extract<BridgeRequest, { op: 'list' }>) {
  const after = Date.parse(request.received_after);
  if (!Number.isFinite(after)) throw new MailError('invalid_request');
  const limit = Math.max(1, Math.min(25, request.limit | 0));
  const box = resolveMailbox(store, request.account, request.mailbox);
  const rows = store.db.prepare(`${MESSAGE_SQL} WHERE ${inMailbox(store)} AND m.deleted = 0 AND m.date_received > ?2
    ${request.unread_only ? 'AND m.read = 0' : ''} ${request.flagged_only ? 'AND m.flagged != 0' : ''}
    ORDER BY m.date_received DESC, m.ROWID DESC`).all(box.rowid, Math.floor(after / 1000));
  const senderWanted = str(request.sender_contains, 200).toLowerCase(), subjectWanted = str(request.subject_contains, 200).toLowerCase();
  const candidates = rows.filter(row => received(row).getTime() > after &&
    (!senderWanted || sender(row).toLowerCase().includes(senderWanted)) && (!subjectWanted || subject(row).toLowerCase().includes(subjectWanted)));
  const messages = [];
  for (const row of candidates) {
    if (messages.length >= limit) break;
    const id = messageId(row);
    if (!id) continue;
    messages.push({ id: Number(row.id), message_id: id, sender: sender(row), subject: subject(row),
      date_received: received(row).toISOString(), read: Boolean(row.read), flagged: Boolean(row.flagged), size: Math.max(0, Number(row.size) || 0) });
  }
  return { ok: true, matched: candidates.length, messages };
}

/** <mbox dir>/<store UUID>/Data/<reversed digits of id/1000>/Messages/<id>[.partial].emlx (older layout: <mbox>/Messages). */
async function readEmlx(store: Store, box: Mailbox, id: number): Promise<Buffer | null> {
  const mbox = path.join(store.root, box.host, ...box.segments.map(segment => segment + '.mbox'));
  const thousands = Math.floor(id / 1000);
  const digits = thousands ? String(thousands).split('').reverse() : [];
  let entries: string[];
  try { entries = await readdir(mbox); } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code === 'EPERM' || code === 'EACCES') throw new MailStoreUnavailable('emlx');
    return null;
  }
  const dirs = [...entries.filter(name => /^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$/i.test(name))
    .flatMap(name => [path.join(mbox, name, 'Data', ...digits, 'Messages'), path.join(mbox, name, 'Data', 'Messages')]),
    path.join(mbox, 'Messages')];
  for (const dir of dirs) for (const file of [`${id}.emlx`, `${id}.partial.emlx`]) {
    try { return await readFile(path.join(dir, file)); } catch (error) {
      const code = (error as NodeJS.ErrnoException).code;
      if (code === 'EPERM' || code === 'EACCES') throw new MailStoreUnavailable('emlx');
    }
  }
  return null;
}

async function read(store: Store, request: Extract<BridgeRequest, { op: 'read' }>) {
  const box = resolveMailbox(store, request.account, request.mailbox);
  const max = Math.max(500, Math.min(8000, request.max_chars | 0));
  if (!Number.isSafeInteger(request.id)) throw new MailError('stale_reference');
  const row = store.db.prepare(`${MESSAGE_SQL} WHERE ${inMailbox(store)} AND m.ROWID = ?2 AND m.deleted = 0`).get(box.rowid, request.id);
  if (!row || messageId(row) !== request.message_id) throw new MailError('stale_reference');
  // The file lives in the message's own (storage) mailbox, e.g. Gmail's All Mail for an INBOX message.
  const home = store.mailboxes.find(b => b.rowid === Number(row.home));
  const file = home ? await readEmlx(store, home, request.id) : null;
  // A .partial.emlx lacks attachments but usually still holds the text; no text at all = not cached.
  const body = file ? messageText(emlxMessage(file)) : '';
  if (!body.trim()) throw new MailError('content_not_cached');
  return { ok: true, id: request.id, message_id: request.message_id, sender: sender(row), subject: subject(row),
    date_received: received(row).toISOString(), body: body.slice(0, max * 2), body_length: body.length };
}

/** Transport over Mail's local store. Opens the database per request and closes it afterwards. */
export function mailStoreTransport(paths: MailStorePaths = {}): MailTransport {
  return {
    async request(input, signal) {
      signal.throwIfAborted();
      const store = await openStore(paths);
      try {
        let result: unknown;
        if (input.op === 'accounts') result = { ok: true, accounts: [...store.accounts.keys()].slice(0, 50) };
        else if (input.op === 'count') {
          const box = resolveMailbox(store, input.account, input.mailbox);
          result = { ok: true, count: Number(store.db.prepare(`SELECT count(*) n FROM messages m WHERE ${inMailbox(store)} AND m.deleted = 0`).get(box.rowid)?.n ?? 0) };
        } else if (input.op === 'list') result = list(store, input);
        else if (input.op === 'read') result = await read(store, input);
        else throw new MailError('invalid_request');
        return JSON.stringify(result);
      } catch (error) {
        if (error instanceof MailError || error instanceof MailStoreUnavailable) throw error;
        throw new MailStoreUnavailable('query'); // SQLite busy/corrupt or unexpected layout.
      } finally { store.db.close(); }
    }
  };
}

/** Tries `primary`; when it reports MailStoreUnavailable, uses `secondary` for this and every later call (sticky, so
 *  references from one transport are never read through the other). Mail errors from `primary` are final. */
export function fallbackTransport(primary: MailTransport, secondary: () => MailTransport): MailTransport {
  let fallback: MailTransport | undefined;
  return {
    async request(input, signal) {
      if (!fallback) {
        try { return await primary.request(input, signal); }
        catch (error) { if (!(error instanceof MailStoreUnavailable)) throw error; }
        fallback = secondary();
      }
      return fallback.request(input, signal);
    }
  };
}

/** Default: Mail's local store (works with Mail closed); JXA bridge when the store is unavailable. Never launches Mail. */
export function defaultMailTransport(): MailTransport {
  return fallbackTransport(mailStoreTransport(), () => osascriptTransport());
}
