// Manual, read-only Apple Mail smoke test. Never run by `npm test`.
//   node scripts/test-mail-smoke.ts "<Mail account name>"   (default: first active mail_accounts entry)
// Prints the inbox count, up to 5 of today's message metadata rows and the length/status of one body
// (never the body text) to this terminal only. Nothing is written to disk; no message is changed.
// Uses Mail's local store (Full Disk Access); the JXA fallback may show the macOS Automation prompt.
import { loadConfig } from '../src/config.ts';
import { MailClient, MailError, cleanField } from '../.pi/extensions/macos-mail/client.ts';
import { defaultMailTransport } from '../.pi/extensions/macos-mail/mail-db.ts';

const config = await loadConfig();
const configured = config.mail_accounts.find(a => process.argv[2] === undefined ? a.enabled : a.account === process.argv[2]);
const account = process.argv[2] ?? configured?.account ?? '', mailbox = configured?.mailbox ?? 'INBOX';
if (!account.trim()) { console.error('Usage: node scripts/test-mail-smoke.ts "<Mail account name>" (or add an account in Admin)'); process.exit(2); }
const transport = defaultMailTransport();
const signal = AbortSignal.timeout(60_000);
const timed = async <T>(label: string, work: () => Promise<T>): Promise<T> => {
  const started = performance.now();
  try { return await work(); } finally { console.log(`[${label}: ${Math.round(performance.now() - started)} ms]`); }
};
try {
  const raw = await timed('count', async () => JSON.parse(await transport.request({ op: 'count', account, mailbox }, signal)));
  if (raw?.ok !== true) throw new MailError(['automation_denied', 'account_not_found', 'mailbox_not_found', 'mail_unavailable'].includes(raw?.error) ? raw.error : 'invalid_response');
  console.log(`Inbox messages: ${Number(raw.count)}`);
  const client = new MailClient({ ...config, mail_accounts: [{ account, mailbox, enabled: true }] }, transport);
  const midnight = new Date(); midnight.setHours(0, 0, 0, 0);
  const list = await timed('list', () => client.list({ received_after: midnight.toISOString(), limit: 5 }, signal));
  if (!list.messages.length) { console.log('No messages received today (since local 00:00).'); process.exit(0); }
  console.log(`Today: ${list.matched} message(s); showing ${list.messages.length}`);
  for (const m of list.messages)
    console.log(`- ${m.date_received} ${m.read ? 'read  ' : 'unread'}${m.flagged ? ' flagged' : ''} | ${cleanField(m.sender, 60)} | ${cleanField(m.subject, 80)}`);
  try {
    const body = await timed('read', () => client.read({ message_ref: list.messages[0].message_ref, max_chars: 500 }, signal));
    console.log(`Body of first message: ${body.body.length} chars${body.truncated ? ' (truncated)' : ''}`);
  } catch (error) {
    if (!(error instanceof MailError) || error.code !== 'content_not_cached') throw error;
    console.log('Body of first message: not available on this Mac (content_not_cached)');
  }
} catch (error) {
  console.error(error instanceof MailError ? `Mail smoke failed: ${error.code} - ${error.message}` : 'Mail smoke failed: unexpected error');
  process.exit(1);
}
