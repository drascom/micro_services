import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { mkdtemp, mkdir, writeFile, rm, chmod } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { defaults } from '../src/config.ts';
import { MailClient, MailError, mailAppAccounts, type MailTransport } from '../.pi/extensions/macos-mail/client.ts';
import { fallbackTransport, mailStoreTransport, MailStoreUnavailable } from '../.pi/extensions/macos-mail/mail-db.ts';
import { decodeQuotedPrintable, emlxMessage, htmlToText, messageText } from '../.pi/extensions/macos-mail/mime.ts';
import type { BridgeRequest } from '../.pi/extensions/macos-mail/schemas.ts';

// Synthetic fixtures only: invented accounts, addresses, subjects and bodies.
const GMAIL = '11111111-AAAA-4AAA-8AAA-111111111111', WORK = '22222222-BBBB-4BBB-8BBB-222222222222';
const LOCAL = '33333333-CCCC-4CCC-8CCC-333333333333', STORE = '44444444-DDDD-4DDD-8DDD-444444444444';
const NOW = Date.parse('2026-10-09T12:00:00Z'), H = 3600;
const live = () => new AbortController().signal;
async function code(promise: Promise<unknown>): Promise<string> {
  try { await promise; } catch (error) { if (error instanceof MailError) return error.code; if (error instanceof MailStoreUnavailable) return 'store_unavailable'; throw error; }
  return 'ok';
}
const emlx = (message: string) => { const bytes = Buffer.from(message.replace(/\n/g, '\r\n'), 'utf8'); return Buffer.concat([Buffer.from(`${bytes.length}\n`), bytes, Buffer.from('<?xml version="1.0"?><plist/>')]); };

type Fixture = { dir: string; mailRoot: string; accountsDb: string };
async function fixture(): Promise<Fixture> {
  const dir = await mkdtemp(path.join(tmpdir(), 'mail-db-'));
  const mailRoot = path.join(dir, 'V10'), accountsDb = path.join(dir, 'Accounts4.sqlite');
  await mkdir(path.join(mailRoot, 'MailData'), { recursive: true });
  const accounts = new DatabaseSync(accountsDb);
  accounts.exec(`CREATE TABLE ZACCOUNTTYPE (Z_PK INTEGER PRIMARY KEY, ZIDENTIFIER TEXT);
    CREATE TABLE ZACCOUNT (Z_PK INTEGER PRIMARY KEY, ZIDENTIFIER TEXT, ZACCOUNTDESCRIPTION TEXT, ZUSERNAME TEXT, ZPARENTACCOUNT INTEGER, ZACCOUNTTYPE INTEGER);
    INSERT INTO ZACCOUNTTYPE VALUES (1, 'com.apple.account.Google'), (2, 'com.apple.account.IMAP'), (3, 'com.apple.account.OnMyDevice');
    INSERT INTO ZACCOUNT VALUES (1, 'PARENT-1', 'Synthetic Gmail', 'user@example.invalid', NULL, 1),
      (2, '${GMAIL}', NULL, 'user@example.invalid', 1, 2), (3, '${WORK}', 'Synthetic Work', 'w@example.invalid', NULL, 2),
      (4, '${LOCAL}', 'On My Mac', NULL, NULL, 3);`);
  accounts.close();
  const db = new DatabaseSync(path.join(mailRoot, 'MailData', 'Envelope Index'));
  db.exec(`CREATE TABLE mailboxes (ROWID INTEGER PRIMARY KEY, url TEXT);
    CREATE TABLE subjects (ROWID INTEGER PRIMARY KEY, subject TEXT);
    CREATE TABLE addresses (ROWID INTEGER PRIMARY KEY, address TEXT, comment TEXT);
    CREATE TABLE message_global_data (ROWID INTEGER PRIMARY KEY, message_id_header TEXT);
    CREATE TABLE labels (message_id INTEGER, mailbox_id INTEGER);
    CREATE TABLE messages (ROWID INTEGER PRIMARY KEY, global_message_id INTEGER, sender INTEGER, subject_prefix TEXT, subject INTEGER,
      date_received INTEGER, mailbox INTEGER, read INTEGER, flagged INTEGER, deleted INTEGER, size INTEGER);
    INSERT INTO mailboxes VALUES (1, 'imap://${GMAIL}/%5BGmail%5D/All%20Mail'), (2, 'imap://${GMAIL}/INBOX'),
      (3, 'imap://${WORK}/INBOX'), (4, 'local://${LOCAL}/Drafts');
    INSERT INTO addresses VALUES (1, 'alice@example.invalid', 'Alice Example'), (2, 'bob@example.invalid', '');
    INSERT INTO subjects VALUES (1, 'Synthetic plain'), (2, 'Synthetic html'), (3, 'Synthetic partial'), (4, 'Synthetic missing'), (5, 'Synthetic old');`);
  const s = Math.floor(NOW / 1000);
  // id, sender, prefix, subject, received, mailbox, read, flagged, deleted
  const rows: [number, number, string, number, number, number, number, number, number][] = [
    [5, 1, '', 1, s - H, 1, 0, 1, 0], [1200, 2, 'Re: ', 2, s - 2 * H, 1, 1, 0, 0], [7, 1, '', 3, s - 3 * H, 1, 0, 0, 0],
    [8, 2, '', 4, s - 4 * H, 1, 0, 0, 0], [9, 1, '', 5, s - 40 * 24 * H, 1, 0, 0, 0], [10, 2, '', 1, s - 5 * H, 1, 0, 0, 1],
    [11, 1, '', 1, s - 6 * H, 1, 0, 0, 0], [20, 2, '', 1, s - H, 3, 0, 0, 0]];
  for (const [id, sender, prefix, subject, received, mailbox, read, flagged, deleted] of rows) {
    db.prepare('INSERT INTO message_global_data VALUES (?, ?)').run(id, `<synthetic-${id}@example.invalid>`);
    db.prepare('INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)').run(id, id, sender, prefix, subject, received, mailbox, read, flagged, deleted, 1000 + id);
    if (mailbox === 1 && id !== 11) db.prepare('INSERT INTO labels VALUES (?, 2)').run(id); // 11: archived (All Mail only)
  }
  db.close();
  const data = path.join(mailRoot, GMAIL, '[Gmail].mbox', 'All Mail.mbox', STORE, 'Data');
  await mkdir(path.join(data, 'Messages'), { recursive: true });
  await mkdir(path.join(data, '1', 'Messages'), { recursive: true });
  await writeFile(path.join(data, 'Messages', '5.emlx'), emlx('Subject: x\nContent-Type: text/plain; charset=iso-8859-9\nContent-Transfer-Encoding: quoted-printable\n\nMerhaba d=FCnya =\nve =FEehir.\n'));
  await writeFile(path.join(data, '1', 'Messages', '1200.emlx'), emlx('Content-Type: multipart/alternative; boundary="b1"\n\n--b1\nContent-Type: text/html; charset=utf-8\nContent-Transfer-Encoding: base64\n\n' +
    Buffer.from('<html><head><style>p{color:red}</style></head><body><p>Hello&nbsp;<b>World</b></p><p>Line &amp; two</p></body></html>').toString('base64') + '\n--b1--\n'));
  await writeFile(path.join(data, 'Messages', '7.partial.emlx'), emlx('Content-Type: multipart/mixed; boundary=zz\n\n--zz\nContent-Type: application/pdf\nContent-Disposition: attachment; filename=x.pdf\nX-Apple-Content-Length: 1000\n\n\n--zz--\n'));
  return { dir, mailRoot, accountsDb };
}

const settingsFor = (accounts: { account: string; mailbox: string; enabled: boolean }[]) => ({ ...defaults, mail_accounts: accounts });

test('MIME: quoted-printable, charsets, base64 html and attachments', () => {
  assert.equal(decodeQuotedPrintable('a=3Db=\r\nc').toString(), 'a=bc');
  assert.equal(messageText(emlxMessage(emlx('Content-Type: text/plain; charset="windows-1254"\nContent-Transfer-Encoding: quoted-printable\n\n=DE=FD=F0\n'))), 'Şığ');
  const html = htmlToText('<script>x()</script><div>A<br>B</div><table><tr><td>c</td><td>d</td></tr></table>&#x41;&lt;');
  assert.match(html, /A\nB/); assert.doesNotMatch(html, /x\(\)/); assert.match(html, /c d/); assert.match(html, /A<$/);
  const mixed = 'Content-Type: multipart/mixed; boundary=m\n\n--m\nContent-Type: multipart/alternative; boundary=a\n\n--a\nContent-Type: text/plain; charset=utf-8\n\nPlain wins\n--a\nContent-Type: text/html\n\n<p>Html</p>\n--a--\n--m\nContent-Type: text/plain\nContent-Disposition: attachment\n\nATTACHED\n--m--\n';
  assert.equal(messageText(Buffer.from(mixed)), 'Plain wins');
  assert.equal(messageText(Buffer.from('Content-Type: image/png\n\nxxx')), '');
});

test('store transport: accounts come from the store (parent account names, no On My Mac), Mail not needed', async () => {
  const f = await fixture();
  try {
    const transport = mailStoreTransport(f);
    assert.deepEqual((await mailAppAccounts(transport, live())).sort(), ['Synthetic Gmail', 'Synthetic Work']);
    assert.equal(JSON.parse(await transport.request({ op: 'count', account: 'Synthetic Gmail', mailbox: 'inbox' }, live())).count, 5);
    assert.equal(await code(transport.request({ op: 'count', account: 'Nope', mailbox: 'INBOX' }, live())), 'account_not_found');
    assert.equal(await code(transport.request({ op: 'count', account: 'Synthetic Gmail', mailbox: 'Nope' }, live())), 'mailbox_not_found');
  } finally { await rm(f.dir, { recursive: true, force: true }); }
});

test('store transport: list filters, label membership and the same contract through MailClient', async () => {
  const f = await fixture();
  try {
    const transport = mailStoreTransport(f);
    const client = new MailClient(settingsFor([{ account: 'Synthetic Gmail', mailbox: 'INBOX', enabled: true }, { account: 'Synthetic Work', mailbox: 'INBOX', enabled: true }]), transport, () => NOW);
    const all = await client.list({}, live());
    assert.deepEqual(all.messages.map(m => m.subject), ['Synthetic plain', 'Synthetic plain', 'Re: Synthetic html', 'Synthetic partial', 'Synthetic missing']);
    assert.equal(all.messages[0].sender, 'Alice Example <alice@example.invalid>');
    assert.equal(all.matched, 5); // old (40 days), deleted and archived-only messages are excluded
    assert.equal((await client.list({ unread_only: true, account: 'synthetic gmail' }, live())).messages.length, 3);
    assert.deepEqual((await client.list({ flagged_only: true }, live())).messages.map(m => m.flagged), [true]);
    assert.equal((await client.list({ sender_contains: 'ALICE' }, live())).messages.length, 2);
    assert.deepEqual((await client.list({ subject_contains: 're: synth' }, live())).messages.map(m => m.size), [2200]);
    const limited = await client.list({ limit: 2 }, live());
    assert.equal(limited.messages.length, 2); assert.equal(limited.truncated, true);
    const raw = JSON.parse(await transport.request({ op: 'list', account: 'Synthetic Gmail', mailbox: 'INBOX', received_after: new Date(NOW - 86400000).toISOString(),
      unread_only: false, flagged_only: false, limit: 10 }, live()));
    assert.equal(raw.messages[0].message_id, 'synthetic-5@example.invalid'); // brackets stripped like Mail's scripting messageId
  } finally { await rm(f.dir, { recursive: true, force: true }); }
});

test('store transport: read decodes .emlx, partial without text and missing files are content_not_cached, Message-ID is verified', async () => {
  const f = await fixture();
  try {
    const transport = mailStoreTransport(f);
    const client = new MailClient(settingsFor([{ account: 'Synthetic Gmail', mailbox: 'INBOX', enabled: true }]), transport, () => NOW);
    const listed = await client.list({}, live());
    const ref = (subject: string) => listed.messages.find(m => m.subject === subject)!.message_ref;
    assert.equal((await client.read({ message_ref: ref('Synthetic plain') }, live())).body, 'Merhaba dünya ve şehir.');
    assert.match((await client.read({ message_ref: ref('Re: Synthetic html') }, live())).body, /^Hello World\n+Line & two$/);
    assert.equal(await code(client.read({ message_ref: ref('Synthetic partial') }, live())), 'content_not_cached');
    assert.equal(await code(client.read({ message_ref: ref('Synthetic missing') }, live())), 'content_not_cached');
    const base = { op: 'read' as const, account: 'Synthetic Gmail', mailbox: 'INBOX', max_chars: 1000 };
    assert.equal(await code(transport.request({ ...base, id: 5, message_id: 'other@example.invalid' }, live())), 'stale_reference');
    assert.equal(await code(transport.request({ ...base, id: 11, message_id: 'synthetic-11@example.invalid' }, live())), 'stale_reference'); // not in INBOX
    const long = await client.read({ message_ref: ref('Synthetic plain'), max_chars: 500 }, live());
    assert.equal(long.truncated, false);
  } finally { await rm(f.dir, { recursive: true, force: true }); }
});

test('store transport: inactive accounts are rejected before the store is touched', async () => {
  const f = await fixture();
  try {
    const client = new MailClient(settingsFor([{ account: 'Synthetic Gmail', mailbox: 'INBOX', enabled: true }, { account: 'Synthetic Work', mailbox: 'INBOX', enabled: false }]),
      mailStoreTransport(f), () => NOW);
    assert.equal(await code(client.list({ account: 'Synthetic Work' }, live())), 'unknown_account');
    assert.ok((await client.list({}, live())).messages.every(m => m.account === 'Synthetic Gmail'));
  } finally { await rm(f.dir, { recursive: true, force: true }); }
});

test('fallback: EPERM, missing store or unknown schema hand over to the JXA bridge (sticky); Mail errors do not', async () => {
  const f = await fixture();
  const calls: BridgeRequest[] = [];
  const jxa: MailTransport = { async request(input) { calls.push(input); return JSON.stringify({ ok: false, error: 'mail_unavailable' }); } };
  try {
    const index = path.join(f.mailRoot, 'MailData', 'Envelope Index');
    await chmod(index, 0o000);
    if (process.getuid?.() !== 0) {
      assert.equal(await code(mailStoreTransport(f).request({ op: 'accounts' }, live())), 'store_unavailable');
      assert.equal(await code(mailAppAccounts(fallbackTransport(mailStoreTransport(f), () => jxa), live())), 'mail_unavailable');
      assert.equal(calls.length, 1);
    }
    await chmod(index, 0o644);
    assert.equal(await code(mailStoreTransport({ ...f, mailRoot: path.join(f.dir, 'missing') }).request({ op: 'accounts' }, live())), 'store_unavailable');
    const db = new DatabaseSync(index); db.exec('ALTER TABLE messages DROP COLUMN flagged'); db.close();
    const fallback = fallbackTransport(mailStoreTransport(f), () => jxa);
    assert.equal(await code(mailAppAccounts(fallback, live())), 'mail_unavailable');
    assert.equal(await code(mailAppAccounts(fallback, live())), 'mail_unavailable');
    assert.equal(calls.length, 3);
    const healthy = await fixture();
    try {
      const direct = fallbackTransport(mailStoreTransport(healthy), () => jxa);
      assert.equal(await code(direct.request({ op: 'count', account: 'Nope', mailbox: 'INBOX' }, live())), 'account_not_found');
      assert.equal(calls.length, 3);
    } finally { await rm(healthy.dir, { recursive: true, force: true }); }
  } finally { await rm(f.dir, { recursive: true, force: true }); }
});
