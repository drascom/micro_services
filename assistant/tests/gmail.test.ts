import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { once } from 'node:events';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { ModelRuntime } from '@earendil-works/pi-coding-agent';
import { fauxProvider, fauxAssistantMessage, fauxToolCall } from '@earendil-works/pi-ai';
import { defaults, loadConfig, type Config } from '../src/config.ts';
import { createHarnessSession, harnessTools } from '../src/harness.ts';
import { makeServer } from '../src/server.ts';
import { Orchestrator } from '../src/orchestrator.ts';
import { ConfirmationStore, CONFIRM_TTL_MS } from '../.pi/extensions/apple-reminders/confirmations.ts';
import type { RemindersTransport } from '../.pi/extensions/apple-reminders/client.ts';
import type { BridgeRequest } from '../.pi/extensions/apple-reminders/schemas.ts';
import { ACTIONS, authenticated, classifyAll, urgentEntry, urgentSender, classifyPrompt, parseLevels, ruleLevel } from '../.pi/extensions/gmail/classify.ts';
import { PROFILE_URL, REVOKE_URL, SCOPE, TOKEN_URL, type Http } from '../.pi/extensions/gmail/oauth.ts';
import { MemorySecretStore } from '../.pi/extensions/gmail/secrets.ts';
import { GmailService } from '../.pi/extensions/gmail/service.ts';
import { findLabel, GmailError } from '../.pi/extensions/gmail/schemas.ts';
import { groups, lastPlan, MailTriage, scrub, TriageMemory } from '../.pi/extensions/gmail/triage.ts';
import type { MessageMeta } from '../.pi/extensions/gmail/provider.ts';

// Synthetic mail and a fake Gmail/OAuth transport only: no real network call, nothing is written to a real account.
const GOOGLE_OK = (domain: string) => `mx.google.com; dkim=pass header.i=@${domain} header.s=s1 header.b=x; spf=pass (google.com: domain of a@${domain} designates 1.2.3.4 as permitted sender) smtp.mailfrom=a@${domain}; dmarc=pass (p=REJECT sp=REJECT dis=NONE) header.from=${domain}`;
type Msg = { id: string; labels: string[]; from: string; subject: string; snippet?: string; auth?: string[]; date?: number };
const msg = (id: string, extra: Partial<Msg> = {}): Msg => ({ id, labels: ['INBOX', 'UNREAD', 'CATEGORY_PERSONAL'], from: `Sender ${id} <s${id}@example.org>`, subject: `Subject ${id}`, ...extra });

/** One fake Google: `messages` is the main account's mailbox (`email`, default me@gmail.com); `others` adds more accounts. */
function fakeGmail(messages: Msg[], options: { email?: string; labels?: { id: string; name: string }[]; others?: Record<string, Msg[]> } = {}) {
  const calls: { method: string; url: string; body?: string; account?: string }[] = [];
  const main = options.email ?? 'me@gmail.com', boxes: Record<string, Msg[]> = { [main]: messages, ...options.others };
  const refresh: Record<string, string> = Object.fromEntries(Object.keys(boxes).map(e => [e, e === main ? 'rt-good' : 'rt-' + e]));
  const valid = new Map<string, string>(); // access token → account
  let access = 0;
  const issue = (email: string) => { const token = 'at' + ++access; valid.set(token, email); return token; };
  const labels = options.labels ?? [{ id: 'Label_1', name: 'Sonra Oku' }, { id: 'Label_2', name: 'Gereksiz' }, { id: 'INBOX', name: 'INBOX' }];
  const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
  const http: Http = async (url, init) => {
    const bearer = init.headers?.Authorization?.replace('Bearer ', '') ?? '', account = valid.get(bearer);
    calls.push({ method: init.method, url, ...(init.body ? { body: init.body } : {}), ...(account ? { account } : {}) });
    if (url === TOKEN_URL) {
      const form = new URLSearchParams(init.body);
      if (form.get('grant_type') === 'refresh_token') {
        const email = Object.keys(refresh).find(e => refresh[e] === form.get('refresh_token'));
        return email ? json({ access_token: issue(email), expires_in: 3600 }) : json({ error: 'invalid_grant' }, 400);
      }
      const email = form.get('code') === 'code-1' ? main : form.get('code')?.replace(/^code-/, '') ?? '';
      if (!boxes[email] || !form.get('code_verifier')) return json({ error: 'invalid_grant' }, 400);
      refresh[email] ??= 'rt-' + email;
      return json({ access_token: issue(email), refresh_token: refresh[email], expires_in: 3600, scope: SCOPE, verifier: form.get('code_verifier') });
    }
    if (url === REVOKE_URL) return new Response('', { status: 200 });
    if (url === PROFILE_URL) return account ? json({ emailAddress: account === main ? main.replace(/^me@/, 'Me@') : account }) : json({}, 401);
    if (!account) return json({ error: { code: 401 } }, 401);
    const messages = boxes[account];
    const u = new URL(url), route = u.pathname.replace('/gmail/v1/users/me', '');
    if (init.method === 'GET' && route === '/labels') return json({ labels });
    if (init.method === 'GET' && route === '/messages') {
      const label = u.searchParams.get('labelIds'), q = u.searchParams.get('q');
      // Tiny Gmail search: in:inbox / label:<id> scope, from:<text>, other words match the subject; Trash is never searched.
      const match = (m: Msg) => !m.labels.includes('TRASH') && (q ?? '').split(' ').filter(Boolean).every(t =>
        t === 'in:inbox' ? m.labels.includes('INBOX') : t.startsWith('label:') ? m.labels.includes(t.slice(6))
          : t.startsWith('from:') ? m.from.toLowerCase().includes(t.slice(5).toLowerCase()) : m.subject.toLowerCase().includes(t.toLowerCase()));
      return json({ messages: messages.filter(m => label ? m.labels.includes(label) : match(m)).map(m => ({ id: m.id })) });
    }
    const one = /^\/messages\/([^/]+)(\/trash|\/untrash)?$/.exec(route), m = one && messages.find(x => x.id === one[1]);
    if (init.method === 'GET' && m) return json({ id: m.id, labelIds: m.labels, snippet: m.snippet ?? 'snippet', internalDate: String(m.date ?? 0),
      payload: { headers: [{ name: 'From', value: m.from }, { name: 'Subject', value: m.subject }, ...(m.auth ?? []).map(value => ({ name: 'Authentication-Results', value }))] } });
    if (init.method === 'POST' && one?.[2] === '/untrash' && m) { m.labels = ['INBOX', ...m.labels.filter(l => l !== 'TRASH')]; return json({ id: m.id }); }
    if (init.method === 'POST' && one?.[2] && m) { m.labels = ['TRASH', ...m.labels.filter(l => l !== 'INBOX')]; return json({ id: m.id }); }
    if (init.method === 'POST' && route === '/messages/batchModify') {
      const body = JSON.parse(init.body!) as { ids: string[]; addLabelIds: string[]; removeLabelIds: string[] };
      for (const id of body.ids) { const x = messages.find(y => y.id === id)!; x.labels = [...x.labels.filter(l => !body.removeLabelIds.includes(l)), ...body.addLabelIds.filter(l => !x.labels.includes(l))]; }
      return new Response(null, { status: 204 });
    }
    return json({ error: 'not found' }, 404);
  };
  return { http, calls, messages, boxes, revokeRefresh: (email = main) => { refresh[email] = 'revoked-' + email; } };
}

async function setup(messages: Msg[], options: { email?: string; labels?: { id: string; name: string }[]; connected?: boolean; others?: Record<string, Msg[]> } = {}) {
  const dir = await mkdtemp(path.join(tmpdir(), 'ayhan-gmail-'));
  const gmail = fakeGmail(messages, options), store = new MemorySecretStore();
  store.values.set('client', JSON.stringify({ client_id: 'id-123456.apps.googleusercontent.com', client_secret: 'GOCSPX-secret' }));
  // The main account as the pre-multi-account single `token` (migrated on first read); other accounts in the new layout.
  if (options.connected !== false) store.values.set('token', JSON.stringify({ refresh_token: 'rt-good', email: 'me@gmail.com' }));
  const others = Object.keys(options.others ?? {});
  if (others.length) store.values.set('accounts', JSON.stringify(others));
  for (const email of others) store.values.set('token-' + email, JSON.stringify({ refresh_token: 'rt-' + email }));
  const service = new GmailService({ dataDir: path.join(dir, 'data'), exampleFile: path.join(dir, 'example.md'), store, http: gmail.http });
  let now = Date.UTC(2026, 9, 11, 9);
  const memory = new TriageMemory(new ConfirmationStore(() => now));
  const reminders: BridgeRequest[] = [], notes: string[] = [];
  const settings = { ...defaults, gmail_urgent_senders: ['@work.example', 'mom@example.net'], reminders_enabled: true };
  const triage = (turn: number, extra: { model?: (s: string, u: string) => Promise<string>; settings?: Partial<Config>; proposeOnly?: boolean } = {}) =>
    new MailTriage({ mailboxes: service, settings: { ...settings, ...extra.settings }, memory, turn, logFile: service.logFile, stateFile: service.stateFile,
      importance: () => service.importance(), model: extra.model ?? (async () => '{}'), proposeOnly: extra.proposeOnly, now: () => now,
      notify: async (_t, body) => { notes.push(body); },
      reminders: { request: async request => { reminders.push(request); return JSON.stringify({ ok: true }); } } as RemindersTransport });
  return { dir, gmail, store, service, memory, triage, reminders, notes, tick: (ms: number) => { now += ms; },
    close: () => rm(dir, { recursive: true, force: true }) };
}
const live = () => new AbortController().signal;
async function code(promise: Promise<unknown>) {
  try { await promise; } catch (error) { if (error instanceof GmailError) return error.code; throw error; }
  return 'ok';
}

test('OAuth: PKCE + loopback consent stores one refresh token per account in the secret store; state is checked', async () => {
  const f = await setup([], { connected: false });
  try {
    const { auth_url, done } = await f.service.auth.connect();
    const url = new URL(auth_url);
    assert.equal(url.origin + url.pathname, 'https://accounts.google.com/o/oauth2/v2/auth');
    assert.equal(url.searchParams.get('scope'), SCOPE);
    assert.equal(url.searchParams.get('code_challenge_method'), 'S256');
    const redirect = url.searchParams.get('redirect_uri')!;
    assert.match(redirect, /^http:\/\/127\.0\.0\.1:\d+$/);
    assert.equal((await fetch(`${redirect}/?code=code-1&state=wrong`)).status, 400);
    assert.equal((await fetch(`${redirect}/?code=code-1&state=${url.searchParams.get('state')}`)).status, 200);
    assert.equal(await done, 'me@gmail.com');
    // The verifier sent to the token endpoint matches the challenge in the consent URL.
    const exchange = new URLSearchParams(f.gmail.calls.find(c => c.url === TOKEN_URL)!.body);
    assert.equal(createHash('sha256').update(exchange.get('code_verifier')!).digest('base64url'), url.searchParams.get('code_challenge'));
    assert.deepEqual(JSON.parse(f.store.values.get('token-me@gmail.com')!), { refresh_token: 'rt-good' });
    assert.deepEqual(JSON.parse(f.store.values.get('accounts')!), ['me@gmail.com']);
    const status = await f.service.auth.status();
    assert.deepEqual(status, { client_configured: true, connected: true, accounts: [{ email: 'me@gmail.com', error: '' }], connecting: false });
    assert.ok(!JSON.stringify(status).includes('secret') && !JSON.stringify(status).includes('rt-good'));
  } finally { await f.close(); }
  // "Add account": any other Google account is added next to the first; reconnecting one only renews its token.
  const g = await setup([], { others: { 'work@corp.example': [] } });
  try {
    for (const email of ['new@gmail.com', 'me@gmail.com']) {
      const { auth_url, done } = await g.service.auth.connect();
      const url = new URL(auth_url);
      assert.equal(url.searchParams.get('login_hint'), null);
      g.gmail.boxes[email] ??= [];
      await fetch(`${url.searchParams.get('redirect_uri')}/?code=code-${email}&state=${url.searchParams.get('state')}`);
      assert.equal(await done, email);
    }
    assert.deepEqual(await g.service.accounts(), ['me@gmail.com', 'work@corp.example', 'new@gmail.com']);
    assert.ok(g.store.values.has('token-new@gmail.com') && !g.store.values.has('token'));
  } finally { await g.close(); }
});

test('migration: the single pre-list token becomes token-<email> without a reconnect; old config gmail_account is ignored', async () => {
  const f = await setup([msg('1')]);
  try {
    assert.deepEqual(await f.service.accounts(), ['me@gmail.com']);
    assert.ok(!f.store.values.has('token'));
    assert.deepEqual(JSON.parse(f.store.values.get('token-me@gmail.com')!), { refresh_token: 'rt-good' });
    assert.equal((await f.service.provider('me@gmail.com').labels(live())).get('Gereksiz'), 'Label_2');
    // A second read (new process) finds the migrated layout.
    const again = new GmailService({ dataDir: path.join(f.dir, 'data'), exampleFile: '', store: f.store, http: f.gmail.http });
    assert.deepEqual(await again.accounts(), ['me@gmail.com']);
    const file = path.join(f.dir, 'config.json');
    await writeFile(file, JSON.stringify({ gmail_account: 'me@gmail.com', gmail_triage_enabled: true }));
    const config = await loadConfig(file);
    assert.equal(config.gmail_triage_enabled, true); assert.ok(!('gmail_account' in config));
    // Processed ids of the single account keep counting after the migration.
    await mkdir(f.service.dataDir, { recursive: true });
    await writeFile(f.service.stateFile, JSON.stringify({ processed: { '1': Date.UTC(2026, 9, 11, 8) } }));
    assert.equal((await f.triage(1).propose(live()) as any).status, 'empty');
  } finally { await f.close(); }
});

test('OAuth: access token refreshes once on 401; a revoked refresh token is gmail_auth_expired; disconnect forgets it', async () => {
  const f = await setup([msg('1')]);
  try {
    const me = f.service.provider('me@gmail.com');
    assert.equal((await me.labels(live())).get('Gereksiz'), 'Label_2');
    assert.equal(f.gmail.calls.filter(c => c.url === TOKEN_URL).length, 1);
    await me.labels(live()); // Cached access token.
    assert.equal(f.gmail.calls.filter(c => c.url === TOKEN_URL).length, 1);
    f.gmail.revokeRefresh();
    assert.equal(await code(me.labels(live()).then(() => f.service.auth.accessToken('me@gmail.com', live(), true))), 'gmail_auth_expired');
    assert.deepEqual((await f.service.auth.status()).accounts, [{ email: 'me@gmail.com', error: 'gmail_auth_expired' }]);
    await f.service.auth.disconnect('me@gmail.com');
    assert.ok(!f.store.values.has('token-me@gmail.com') && f.store.values.has('client'));
    assert.equal(await code(me.labels(live())), 'gmail_not_connected');
  } finally { await f.close(); }
});

test('DMARC/DKIM: only Gmail\'s own topmost header counts; pass, fail, missing, spoofed, misaligned', () => {
  const from = 'Boss <boss@work.example>';
  assert.equal(authenticated({ from, auth: GOOGLE_OK('work.example') }), true);
  assert.equal(authenticated({ from, auth: 'mx.google.com; dkim=pass header.i=@mail.work.example; dmarc=fail header.from=work.example' }), false);
  assert.equal(authenticated({ from, auth: 'mx.google.com; dkim=pass header.i=@work.example; dmarc=fail header.from=work.example' }), true);
  assert.equal(authenticated({ from, auth: 'mx.google.com; dkim=fail header.i=@work.example; spf=pass; dmarc=fail header.from=work.example' }), false);
  assert.equal(authenticated({ from, auth: 'mx.google.com; dkim=pass header.i=@evil.test; dmarc=pass header.from=evil.test' }), false);
  assert.equal(authenticated({ from, auth: 'evil.test; dmarc=pass header.from=work.example' }), false);
  assert.equal(authenticated({ from }), false);
});

test('rules: urgent list needs authentication; promotions junk, social/forums later; model urgency capped; invalid output → later', async () => {
  const meta = (extra: Partial<MessageMeta>): MessageMeta => ({ id: 'x', labels: ['INBOX'], from: 'A <a@b.example>', subject: 's', snippet: '', date: 0, ...extra });
  const list = ['@work.example'];
  assert.equal(ruleLevel(meta({ from: 'Boss <boss@work.example>', auth: GOOGLE_OK('work.example') }), list), 'urgent');
  assert.equal(ruleLevel(meta({ from: 'Boss <boss@work.example>' }), list), undefined);
  assert.equal(ruleLevel(meta({ labels: ['INBOX', 'CATEGORY_PROMOTIONS'] }), list), 'junk');
  assert.equal(ruleLevel(meta({ labels: ['INBOX', 'CATEGORY_SOCIAL'] }), list), 'later');
  assert.equal(ruleLevel(meta({ labels: ['INBOX', 'CATEGORY_FORUMS'] }), list), 'later');
  assert.deepEqual(parseLevels('nonsense', 2), ['later', 'later']);
  assert.deepEqual(parseLevels('```json\n{"1":"junk","2":"unsure","3":"delete everything"}\n```', 3), ['junk', 'later', 'later']);
  const items = [meta({ from: 'Spoof <boss@work.example>' }), meta({ from: 'Bank <x@bank.example>', auth: GOOGLE_OK('bank.example') }), meta({ labels: ['CATEGORY_PROMOTIONS'] })];
  let seen = '';
  const levels = await classifyAll(items, list, 'Hemen cevapla: banka.', async (system, user) => { seen = system + user; return '{"1":"urgent","2":"urgent"}'; }, live());
  assert.deepEqual(levels, ['important', 'urgent', 'junk']);
  assert.match(seen, /banka/); assert.ok(!seen.includes('CATEGORY_PROMOTIONS'));
  // Model input: sender, subject, snippet and category only (no body, no ids).
  const prompt = classifyPrompt([meta({ snippet: 'x'.repeat(500) })], '');
  assert.deepEqual(Object.keys(JSON.parse(prompt.user)[0]), ['id', 'from', 'subject', 'snippet', 'category']);
  assert.equal(JSON.parse(prompt.user)[0].snippet.length, 160);
  assert.equal(await classifyAll([meta({})], list, '', async () => { throw Error('down'); }, live()).then(l => l[0]), 'later');
  // Fixed action table.
  assert.deepEqual(ACTIONS.urgent, { add: ['STARRED', 'IMPORTANT'], remove: [], notify: true });
  assert.deepEqual(ACTIONS.important, { add: ['STARRED'], remove: [] });
  assert.deepEqual([ACTIONS.later.label, ACTIONS.later.remove], ['Sonra Oku', ['INBOX', 'UNREAD']]);
  assert.deepEqual([ACTIONS.junk.label, ACTIONS.junk.remove], ['Gereksiz', ['INBOX', 'UNREAD']]);
});

test('propose changes nothing; apply only in the next turn, within 2 minutes; labels, 📧 reminder and notification', async () => {
  const messages = [msg('u1', { from: 'Boss <boss@work.example>', auth: [GOOGLE_OK('work.example')] }),
    msg('p1', { labels: ['INBOX', 'UNREAD', 'CATEGORY_PROMOTIONS'] }), msg('s1', { labels: ['INBOX', 'CATEGORY_SOCIAL'] }), msg('m1')];
  const f = await setup(messages);
  try {
    const before = JSON.stringify(messages);
    const proposal = await f.triage(1, { model: async () => '{"1":"important"}' }).propose(live()) as any;
    assert.deepEqual(proposal.counts, { urgent: 1, important: 1, later: 1, junk: 1 });
    assert.deepEqual(proposal.urgent, ['Boss (1): Subject u1']);
    assert.equal(JSON.stringify(messages), before);
    assert.ok(!f.gmail.calls.some(c => c.method === 'POST' && c.url.includes('gmail.googleapis')));
    // Same turn: refused, token kept for the reply.
    assert.equal(await code(f.triage(1).apply(proposal.confirm_token, live())), 'confirmation_needs_reply');
    assert.equal(await code(f.triage(2).apply('c_wrong', live())), 'confirmation_invalid');
    const result = await f.triage(2).apply(proposal.confirm_token, live()) as any;
    assert.deepEqual([result.applied, result.skipped_changed, result.urgent], [4, 0, 1]);
    const labels = Object.fromEntries(messages.map(m => [m.id, [...m.labels].sort()]));
    assert.deepEqual(labels.u1, ['CATEGORY_PERSONAL', 'IMPORTANT', 'INBOX', 'STARRED', 'UNREAD']);
    assert.deepEqual(labels.m1, ['CATEGORY_PERSONAL', 'INBOX', 'STARRED', 'UNREAD']);
    assert.deepEqual(labels.p1, ['CATEGORY_PROMOTIONS', 'Label_2']);
    assert.deepEqual(labels.s1, ['CATEGORY_SOCIAL', 'Label_1']);
    assert.deepEqual(f.reminders, [{ op: 'add', title: '📧 Cevapla: Boss – Subject u1', list: 'Asistan' }]);
    assert.deepEqual(f.notes, ['Boss – Subject u1']);
    // Single use; processed messages are not proposed again.
    assert.equal(await code(f.triage(3).apply(proposal.confirm_token, live())), 'confirmation_invalid');
    assert.equal((await f.triage(3).propose(live()) as any).status, 'empty');
    // Log: ids, levels and labels only.
    const log = await readFile(f.service.logFile, 'utf8');
    assert.ok(!log.includes('Subject') && !log.includes('boss@') && log.includes('"level":"urgent"'));
    // Expiry.
    messages.push(msg('m2'));
    const next = await f.triage(4).propose(live()) as any;
    f.tick(CONFIRM_TTL_MS + 1);
    assert.equal(await code(f.triage(5).apply(next.confirm_token, live())), 'confirmation_expired');
  } finally { await f.close(); }
});

test('apply skips messages changed since the proposal and actions whose label is missing; never creates labels', async () => {
  const messages = [msg('a'), msg('b', { labels: ['INBOX', 'CATEGORY_PROMOTIONS'] }), msg('c', { labels: ['INBOX', 'CATEGORY_SOCIAL'] })];
  const f = await setup(messages, { labels: [{ id: 'Label_1', name: 'Sonra Oku' }] });
  try {
    const proposal = await f.triage(1).propose(live()) as any;
    assert.deepEqual([proposal.missing_labels, proposal.skipped], [['Gereksiz'], 1]);
    messages[0].labels = messages[0].labels.filter(l => l !== 'INBOX'); // The user archived it meanwhile.
    const result = await f.triage(2).apply(proposal.confirm_token, live()) as any;
    assert.deepEqual([result.applied, result.skipped_changed, result.skipped_label], [1, 1, 1]);
    assert.deepEqual(messages[1].labels, ['INBOX', 'CATEGORY_PROMOTIONS']);
    assert.ok(!f.gmail.calls.some(c => c.url.includes('/labels') && c.method === 'POST'));
    // Not processed: proposed again once the label exists.
    assert.deepEqual((await f.triage(3).propose(live()) as any).counts.junk, 1);
  } finally { await f.close(); }
});

test('labels match tolerantly (case, spaces, NFC, Turkish i, nested); a real miss keeps the exact name', () => {
  const labels = new Map([['sonra  OKU ', 'L1'], ['Arşiv/Gereksiz', 'L2']]);
  assert.equal(findLabel(labels, 'Sonra Oku'), 'L1');
  assert.equal(findLabel(labels, 'Gereksiz'), 'L2');
  assert.equal(findLabel(new Map([['GEREKSİZ', 'L3']]), 'Gereksiz'), 'L3');
  assert.equal(findLabel(new Map([['Sonra Oku'.normalize('NFD'), 'L4']]), 'Sonra Oku'), 'L4');
  assert.equal(findLabel(new Map([['Sonra', 'L5']]), 'Sonra Oku'), undefined);
});

test('propose: compact sender groups without ids; counts add up to the plan; full list only for Admin', async () => {
  const shut = (id: string, n: string) => msg(id, { from: 'Google Cloud <cloud@google.com>', subject: `Your project gen-lang-client-${n} has been shut down` });
  const messages = [shut('a', '0424431039'), shut('b', '0424431040'), shut('c', '0999111222'),
    msg('d', { from: 'Google <no-reply@accounts.google.com>', subject: 'Security alert' }), msg('e', { from: 'Google <no-reply@accounts.google.com>', subject: 'Security alert' }),
    ...['f', 'g', 'h', 'i', 'j', 'k'].map(id => msg(id)),
    ...['p', 'q'].map(id => msg(id, { labels: ['INBOX', 'CATEGORY_PROMOTIONS'] }))];
  const f = await setup(messages, { labels: [{ id: 'Label_1', name: 'sonra oku' }, { id: 'Label_2', name: 'Gereksiz' }] });
  try {
    const all = Object.fromEntries(Array.from({ length: 11 }, (_, i) => [String(i + 1), 'important']));
    const proposal = await f.triage(1, { model: async () => JSON.stringify(all) }).propose(live()) as any;
    assert.deepEqual(proposal.counts, { urgent: 0, important: 11, later: 0, junk: 2 });
    assert.equal(proposal.total, 13);
    assert.equal(proposal.missing_labels, undefined); // 'sonra oku' matches 'Sonra Oku'.
    assert.deepEqual(proposal.important, ['Google Cloud (3): Your project … has been shut down', 'Google (2): Security alert',
      'Sender f (1): Subject f', 'Sender g (1): Subject g', 'Sender h (1): Subject h', 'and 3 more senders (3 mails)']);
    assert.ok(!JSON.stringify(proposal).includes('0424431039') && proposal.urgent === undefined);
    // Admin keeps the per-mail list; counts = what apply changes.
    assert.equal(lastPlan()!.items.length, 13);
    assert.ok(lastPlan()!.items.some(i => i.subject.includes('0424431039')));
    const result = await f.triage(2).apply(proposal.confirm_token, live()) as any;
    assert.equal(result.applied, proposal.total);
    assert.equal(messages.filter(m => m.labels.includes('Label_2')).length, proposal.counts.junk);
    assert.equal(messages.filter(m => m.labels.includes('STARRED')).length, proposal.counts.important);
  } finally { await f.close(); }
  assert.equal(scrub('Order #12345678 for 2026 a1b2c3d4e5f6a7b8 shipped'), 'Order … for 2026 … shipped');
  assert.deepEqual(groups([]), []);
});

test('daily limit, purge_junk → Trash only (never permanent delete), undo restores the last apply', async () => {
  const messages = [msg('a', { labels: ['INBOX', 'CATEGORY_SOCIAL'] }), msg('b', { labels: ['INBOX', 'UNREAD', 'CATEGORY_PROMOTIONS'] }),
    msg('j1', { labels: ['Label_2'], date: Date.UTC(2026, 8, 1) }), msg('j2', { labels: ['Label_2'], date: Date.UTC(2026, 9, 1) })];
  const f = await setup(messages);
  try {
    const original = Object.fromEntries(messages.map(m => [m.id, [...m.labels].sort()]));
    const proposal = await f.triage(1, { settings: { gmail_daily_limit: 1 } }).propose(live()) as any;
    const applied = await f.triage(2, { settings: { gmail_daily_limit: 1 } }).apply(proposal.confirm_token, live()) as any;
    assert.deepEqual([applied.applied, applied.limited], [1, 1]);
    assert.equal(await code(f.triage(3, { settings: { gmail_daily_limit: 1 } }).purgeJunk(undefined, live()).then((p: any) =>
      f.triage(4, { settings: { gmail_daily_limit: 1 } }).purgeJunk(p.confirm_token, live()))), 'gmail_daily_limit');
    // Undo needs no confirmation and restores the previous labels.
    const undone = await f.triage(5).undo(live()) as any;
    assert.equal(undone.restored, 1);
    assert.deepEqual(Object.fromEntries(messages.map(m => [m.id, [...m.labels].sort()])), original);
    assert.equal(await code(f.triage(6).undo(live())), 'nothing_to_undo');
    // Purge: count and date range first, then Trash in the next turn.
    const preview = await f.triage(7).purgeJunk(undefined, live()) as any;
    assert.equal(preview.count, 2);
    assert.deepEqual([preview.newest, preview.oldest].sort(), ['2026-09-01', '2026-10-01']); // Gmail lists newest first; the fake keeps insertion order.
    assert.equal(await code(f.triage(7).purgeJunk(preview.confirm_token, live())), 'confirmation_needs_reply');
    const trashed = await f.triage(8).purgeJunk(preview.confirm_token, live()) as any;
    assert.equal(trashed.trashed, preview.count);
    assert.ok(messages.filter(m => m.labels.includes('Label_2')).every(m => m.labels.includes('TRASH')));
    assert.ok(!f.gmail.calls.some(c => c.method === 'DELETE' || /batchDelete|\/messages\/(?!batchModify)[^/?]+$/.test(c.url) && c.method === 'POST'));
    assert.ok(f.gmail.calls.filter(c => c.method === 'POST' && c.url.endsWith('/trash')).length === preview.count);
  } finally { await f.close(); }
});

test('filtered propose: query limits the importance plan; explicit act → Trash/archive/label in the next turn; daily limit and undo cover it', async () => {
  const cloud = (id: string, extra: Partial<Msg> = {}) => msg(id, { from: `Google Cloud <noreply-${id}@cloud.example>`, subject: 'Billing alert', ...extra });
  const messages = [cloud('c1'), cloud('c2'), cloud('c3', { labels: ['CATEGORY_UPDATES'] }), msg('o1'), msg('o2')];
  const f = await setup(messages, { labels: [{ id: 'Label_1', name: 'Sonra Oku' }, { id: 'Label_2', name: 'Gereksiz' }, { id: 'Label_9', name: 'Faturalar' }] });
  try {
    // No act: the usual importance plan, only for matching inbox mail.
    const sorted = await f.triage(1).propose(live(), { query: 'from:google' }) as any;
    assert.equal(sorted.total, 2);
    // act trash ("sil"): count + sender groups, nothing changes until the next turn.
    const plan = await f.triage(2).propose(live(), { query: 'from:google', act: 'trash' }) as any;
    assert.deepEqual([plan.status, plan.act, plan.total], ['proposal', 'trash', 2]);
    assert.match(plan.senders[0], /^Google Cloud \(2\)/); assert.ok(!JSON.stringify(plan).includes('c1'));
    assert.equal(await code(f.triage(2).apply(plan.confirm_token, live())), 'confirmation_needs_reply');
    assert.ok(messages.every(m => !m.labels.includes('TRASH')));
    const done = await f.triage(3).apply(plan.confirm_token, live()) as any;
    assert.equal(done.applied, 2);
    assert.deepEqual(messages.filter(m => m.labels.includes('TRASH')).map(m => m.id), ['c1', 'c2']);
    assert.ok(!f.gmail.calls.some(c => c.method === 'DELETE' || /batchDelete/.test(c.url)));
    // Undo brings trashed mail back.
    assert.equal((await f.triage(4).undo(live()) as any).restored, 2);
    assert.ok(messages.every(m => !m.labels.includes('TRASH')));
    // in:all reaches mail outside the inbox; label:X needs an existing label (never created).
    const label = await f.triage(5).propose(live(), { query: 'from:google in:all', act: 'label:faturalar' }) as any;
    assert.equal(label.total, 3);
    await f.triage(6).apply(label.confirm_token, live());
    assert.ok(messages.filter(m => m.id.startsWith('c')).every(m => m.labels.includes('Label_9')));
    const missing = await f.triage(7).propose(live(), { query: 'from:google', act: 'label:Yok' }) as any;
    assert.deepEqual([missing.status, missing.missing_labels], ['label_missing', ['Yok']]);
    assert.ok(!f.gmail.calls.some(c => c.method === 'POST' && c.url.endsWith('/labels')));
    // Daily limit covers explicit actions.
    const archive = await f.triage(8, { settings: { gmail_daily_limit: 6 } }).propose(live(), { query: 'from:google', act: 'archive' }) as any;
    const limited = await f.triage(9, { settings: { gmail_daily_limit: 6 } }).apply(archive.confirm_token, live()) as any;
    assert.deepEqual([limited.applied, limited.limited], [1, 1]);
    // Invalid input says what is wrong.
    for (const [filter, hint] of [[{ act: 'trash' }, /needs a query/], [{ query: 'x', act: 'delete_forever' }, /act must be one of/],
      [{ query: 'in:trash x', act: 'archive' }, /Trash and Spam/], [{ query: 'x'.repeat(201) }, /too long/]] as const) {
      const error = await f.triage(10).propose(live(), filter).catch(e => e);
      assert.equal(error.code, 'invalid_request'); assert.match(error.message, hint);
    }
  } finally { await f.close(); }
});

test('multiple accounts: propose over all or one, one token applies everywhere, undo across accounts, an expired account is isolated', async () => {
  const work = [msg('w1', { labels: ['INBOX', 'CATEGORY_PROMOTIONS'] }), msg('w2', { from: 'Boss <boss@work.example>', auth: [GOOGLE_OK('work.example')] })];
  const messages = [msg('m1', { labels: ['INBOX', 'CATEGORY_SOCIAL'] })];
  const f = await setup(messages, { others: { 'work@corp.example': work } });
  try {
    assert.deepEqual(await f.service.accounts(), ['me@gmail.com', 'work@corp.example']);
    // Account filter: full address or a unique part; unknown names say which accounts exist.
    const one = await f.triage(1).propose(live(), { account: 'corp' }) as any;
    assert.deepEqual([one.total, one.accounts], [2, undefined]);
    assert.ok(!f.gmail.calls.some(c => c.account === 'me@gmail.com' && c.url.includes('/messages')));
    const unknown = await f.triage(1).propose(live(), { account: 'nope' }).catch(e => e);
    assert.equal(unknown.code, 'invalid_request'); assert.match(unknown.message, /connected: me@gmail.com, work@corp.example/);
    // All accounts: per-account counts, one token for the whole plan.
    const snap = () => JSON.stringify([...messages, ...work].map(m => [m.id, [...m.labels].sort()]));
    const before = snap();
    const all = await f.triage(2).propose(live()) as any;
    assert.equal(all.total, 3);
    assert.deepEqual(all.accounts, { 'me@gmail.com': { later: 1 }, 'work@corp.example': { urgent: 1, junk: 1 } });
    assert.equal(snap(), before);
    assert.deepEqual(lastPlan()!.items.map(i => i.account).sort(), ['me@gmail.com', 'work@corp.example', 'work@corp.example']);
    const applied = await f.triage(3).apply(all.confirm_token, live()) as any;
    assert.deepEqual([applied.applied, applied.urgent, applied.errors], [3, 1, undefined]);
    assert.ok(messages[0].labels.includes('Label_1') && work[0].labels.includes('Label_2') && work[1].labels.includes('STARRED'));
    // Each account's changes went to its own mailbox; the log names the account; processed ids are per account.
    assert.ok(f.gmail.calls.filter(c => c.url.endsWith('/batchModify')).every(c => JSON.parse(c.body!).ids.every((id: string) => id.startsWith(c.account === 'me@gmail.com' ? 'm' : 'w'))));
    const log = (await readFile(f.service.logFile, 'utf8')).trim().split('\n').map(l => JSON.parse(l));
    assert.deepEqual([...new Set(log.map(l => l.account))].sort(), ['me@gmail.com', 'work@corp.example']);
    assert.deepEqual(Object.keys(JSON.parse(await readFile(f.service.stateFile, 'utf8')).accounts).sort(), ['me@gmail.com', 'work@corp.example']);
    assert.equal((await f.triage(4).propose(live()) as any).status, 'empty');
    // Undo reverses the whole last apply in both accounts.
    assert.equal((await f.triage(5).undo(live()) as any).restored, 3);
    assert.equal(snap(), before);
    // Explicit action with a label that exists in one account only: the other is reported, not changed.
    const act = await f.triage(6).propose(live(), { query: 'from:sender', act: 'archive' }) as any;
    assert.deepEqual([act.total, act.accounts], [2, { 'me@gmail.com': 1, 'work@corp.example': 1 }]);
    // Daily cap is shared by all accounts: 3 used above, limit 4 → one more change across both.
    const capped = await f.triage(7, { settings: { gmail_daily_limit: 4 } }).apply(act.confirm_token, live()) as any;
    assert.deepEqual([capped.applied, capped.limited], [1, 1]);
    await f.triage(8).undo(live());
    // One account's token revoked: the other still works; the plan says which account needs reconnecting.
    f.gmail.revokeRefresh('work@corp.example');
    f.service.auth['access'].clear();
    messages.push(msg('m2'));
    const partial = await f.triage(9).propose(live()) as any;
    assert.deepEqual([partial.total, partial.errors], [1, { 'work@corp.example': 'gmail_auth_expired' }]);
    assert.deepEqual((await f.service.auth.status()).accounts.map(a => a.error), ['', 'gmail_auth_expired']);
    const done = await f.triage(10).apply(partial.confirm_token, live()) as any;
    assert.equal(done.applied, 1);
    // Only the expired account selected: its error is the result.
    assert.equal(await code(f.triage(11).propose(live(), { account: 'work@corp.example' })), 'gmail_auth_expired');
    // Purge over both: the expired account is listed under errors.
    const purge = await f.triage(12).purgeJunk(undefined, live()) as any;
    assert.deepEqual([purge.status, purge.errors], ['empty', { 'work@corp.example': 'gmail_auth_expired' }]);
    // Disconnect one account: the other stays connected and usable.
    await f.service.auth.disconnect('work@corp.example');
    assert.deepEqual(await f.service.accounts(), ['me@gmail.com']);
    assert.ok(!f.store.values.has('token-work@corp.example') && f.store.values.has('token-me@gmail.com'));
    assert.equal((await f.triage(13).propose(live(), { query: 'from:sender' }) as any).accounts, undefined);
  } finally { await f.close(); }
});

test('automated task: propose only (plan, no token), and apply is blocked', async () => {
  const f = await setup([msg('a')]);
  try {
    const plan = await f.triage(0, { proposeOnly: true }).propose(live()) as any;
    assert.equal(plan.status, 'proposal'); assert.equal(plan.confirm_token, undefined);
    assert.equal(await code(f.triage(1, { proposeOnly: true }).apply('c_x', live())), 'invalid_request');
  } finally { await f.close(); }
});

test('harness: mail_organize exists only when enabled and connected; online; new pending plan reaches the next turn', async () => {
  assert.deepEqual(harnessTools('mail-triage', false, false, true), ['mail_organize']);
  assert.deepEqual(harnessTools('mail-triage', false, false, false), []);
  assert.deepEqual(harnessTools('mail-triage', true, false, true), []);
  assert.ok(!harnessTools(undefined, true, false, true).includes('mail_organize'));
  // A mail request routed to the read-only skill can still ask for a change: mail_organize only when on and online.
  assert.deepEqual(harnessTools('mail-assistant', false, false, true), ['mail_list_messages', 'mail_read_message', 'mail_organize']);
  assert.deepEqual(harnessTools('mail-assistant', false, false, false), ['mail_list_messages', 'mail_read_message']);
  assert.deepEqual(harnessTools('mail-assistant', true, false, true), ['mail_list_messages', 'mail_read_message']);
  const dir = await mkdtemp(path.join(tmpdir(), 'ayhan-gmail-harness-'));
  await mkdir(path.join(dir, '.pi/skills'), { recursive: true }); await mkdir(path.join(dir, '.pi/extensions'), { recursive: true });
  await writeFile(path.join(dir, '.pi/permissions.json'), JSON.stringify({ mail_organize: 'allow' }));
  const f = await setup([msg('a', { labels: ['INBOX', 'CATEGORY_SOCIAL'] })]);
  const fake = fauxProvider({ provider: 'gmail-test', models: [{ id: 'test' }], tokensPerSecond: 1000000 });
  const runtime = await ModelRuntime.create({ modelsPath: null, refreshOnCreate: false }); runtime.registerNativeProvider(fake.provider);
  const session = (config: Partial<Config>, extra: { automated?: boolean; turn?: number; connected?: boolean } = {}) => createHarnessSession({ root: dir,
    config: { ...defaults, pi_agent_dir: path.join(dir, 'agent'), ...config }, context: { systemPrompt: 'Gmail test', messages: [{ role: 'user', content: 'x', timestamp: 1 }] },
    model: fake.getModel(), runtime, offline: false, extensionPaths: [], skill: 'mail-triage', gmail: extra.connected === false ? new GmailService({ dataDir: dir, exampleFile: '', store: new MemorySecretStore() }) : f.service,
    gmailMemory: f.memory, automated: extra.automated, turn: extra.turn ?? 1, notify: async () => {} });
  const call = (args: Record<string, unknown>) => fauxAssistantMessage(fauxToolCall('mail_organize', args as Record<string, string>, { id: 't' + Math.random() }), { stopReason: 'toolUse' });
  try {
    for (const [config, connected] of [[{ gmail_triage_enabled: false }, true], [{ gmail_triage_enabled: true }, false]] as const) {
      const s = await session(config, { connected }); assert.deepEqual(s.getActiveToolNames(), []); s.dispose();
    }
    // Automated: the plan is proposed, apply is blocked by permissions.
    fake.setResponses([call({ action: 'propose' }), call({ action: 'apply', confirm_token: 'c_x' }), fauxAssistantMessage('plan noted')] as never);
    const task = await session({ gmail_triage_enabled: true }, { automated: true, turn: 0 });
    try {
      assert.deepEqual(task.getActiveToolNames(), ['mail_organize']);
      await task.prompt('x');
      const results = task.messages.filter(m => m.role === 'toolResult');
      assert.deepEqual(results.map(r => r.isError), [false, true]);
      assert.match(JSON.stringify(results[1].content), /may only propose/);
      assert.equal(f.memory.store.pending(1), undefined);
    } finally { await task.abort(); task.dispose(); }
    // User: propose in turn 1; turn 2 gets the tool and a one-line hint even unrouted.
    fake.setResponses([call({ action: 'propose' }), fauxAssistantMessage('Uygulayayım mı?')] as never);
    const first = await session({ gmail_triage_enabled: true }, { turn: 1 });
    try { await first.prompt('x'); } finally { await first.abort(); first.dispose(); }
    assert.ok(f.memory.store.pending(2));
    const second = await createHarnessSession({ root: dir, config: { ...defaults, pi_agent_dir: path.join(dir, 'agent'), gmail_triage_enabled: true },
      context: { systemPrompt: 'Gmail test', messages: [{ role: 'user', content: 'evet', timestamp: 1 }] }, model: fake.getModel(), runtime, offline: false,
      extensionPaths: [], skill: 'online-search', gmail: f.service, gmailMemory: f.memory, turn: 2 });
    try {
      assert.ok(second.getActiveToolNames().includes('mail_organize'));
      assert.match(second.systemPrompt, /Pending mail plan: if this message confirms, call mail_organize apply with confirm_token c_/);
    } finally { second.dispose(); }
  } finally { await f.close(); await rm(dir, { recursive: true, force: true }); }
});

test('urgent senders: @*.domain matches every subdomain but not the domain; patterns validated; DMARC still required', () => {
  const list = ['@*.gov.uk', '@exact.example'];
  assert.equal(urgentSender('a@council.gov.uk', list), true);
  assert.equal(urgentSender('a@x.y.gov.uk', list), true);
  assert.equal(urgentSender('A@Council.GOV.UK', list), true);
  assert.equal(urgentSender('a@gov.uk', list), false);
  assert.equal(urgentSender('a@evilgov.uk', list), false);
  assert.equal(urgentSender('a@sub.exact.example', list), false);
  assert.equal(urgentSender('a@exact.example', list), true);
  assert.equal(urgentEntry('@*.Gov.UK'), '@*.gov.uk');
  assert.equal(urgentEntry('@*.bücher.de'), '@*.xn--bcher-kva.de');
  assert.equal(urgentSender('x@shop.bücher.de', [urgentEntry('@*.bücher.de')]), true);
  for (const bad of ['@*', '@*.uk', '@*.com', 'a*.com', '@a*.com', '*@x.com', 'a@*.x.com', '@*.*.x.com', '@x.*.com', '@**.x.com', '@*x.com', '@.x.com'])
    assert.equal(urgentEntry(bad), '', bad);
  const meta = (from: string, auth?: string): MessageMeta => ({ id: 'x', labels: ['INBOX'], from, subject: 's', snippet: '', date: 0, auth });
  assert.equal(ruleLevel(meta('C <c@council.gov.uk>', GOOGLE_OK('council.gov.uk')), list), 'urgent');
  assert.equal(ruleLevel(meta('C <c@council.gov.uk>', 'mx.google.com; dmarc=fail header.from=council.gov.uk'), list), undefined);
  assert.equal(ruleLevel(meta('C <c@council.gov.uk>', 'mx.google.com; dkim=pass header.d=other.gov.uk'), list), undefined);
});

test('config: urgent senders validated and lowercased; Gmail secrets never go to config.json', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'ayhan-gmail-config-'));
  try {
    const file = path.join(dir, 'config.json');
    await writeFile(file, JSON.stringify({ gmail_urgent_senders: ['Boss@Work.example', '@Council.gov.uk', '@council.gov.uk', '@*.Gov.uk'], gmail_triage_enabled: true }));
    const config = await loadConfig(file);
    assert.deepEqual(config.gmail_urgent_senders, ['boss@work.example', '@council.gov.uk', '@*.gov.uk']);
    for (const bad of [['not an address'], ['@'], ['@*.uk'], ['a*.com'], 'x', [1]]) {
      await writeFile(file, JSON.stringify({ gmail_urgent_senders: bad }));
      await assert.rejects(loadConfig(file));
    }
    await writeFile(file, JSON.stringify({ gmail_client_secret: 'x' }));
    await assert.rejects(loadConfig(file), /Invalid setting/);
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('admin Gmail: client secret only in the secret store, settings in config.json, status without secrets, importance and undo', async () => {
  const f = await setup([msg('a', { labels: ['INBOX', 'CATEGORY_SOCIAL'] })], { connected: false });
  f.store.values.clear();
  const config: Config = { ...defaults, pi_agent_dir: f.dir };
  const reply = { complete: async () => ({ ...fauxAssistantMessage('ok'), provider: 'test' }) };
  const app = new Orchestrator(config, reply, reply, { ensure: async () => {}, close: async () => {} },
    { transcribe: async () => '', speak: async () => {}, stop: () => {}, close: async () => {} });
  const configPath = path.join(f.dir, 'config.json'), lifecycle = new AbortController();
  await writeFile(configPath, JSON.stringify({ language: 'tr', gmail_account: 'me@gmail.com' }));
  const server = await makeServer(config, app, lifecycle.signal, null, f.dir, [], { configPath, gmail: f.service });
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  const base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  const { token } = await (await fetch(base + '/session')).json() as { token: string };
  const call = async (route: string, body: object = {}) => {
    const response = await fetch(base + route, { method: 'POST', headers: { 'X-Assistant-Token': token }, body: JSON.stringify(body) });
    return { status: response.status, body: await response.json() as any };
  };
  try {
    assert.equal((await call('/admin/gmail/connect')).body.error, 'gmail_not_configured');
    assert.equal((await call('/admin/gmail/save', { client_id: 'bad', client_secret: 'x' })).status, 400);
    const saved = await call('/admin/gmail/save', { client_id: 'id-123456.apps.googleusercontent.com', client_secret: 'GOCSPX-secret',
      triage_enabled: true, urgent_senders: ['@Work.example'] });
    assert.equal(saved.status, 200, JSON.stringify(saved.body));
    assert.deepEqual([saved.body.client_configured, saved.body.connected, saved.body.accounts, saved.body.urgent_senders], [true, false, [], ['@work.example']]);
    const file = await readFile(configPath, 'utf8');
    assert.ok(!file.includes('GOCSPX') && !file.includes('googleusercontent'));
    assert.deepEqual(JSON.parse(file), { language: 'tr', gmail_triage_enabled: true, gmail_urgent_senders: ['@work.example'] });
    assert.ok(f.store.values.get('client')!.includes('GOCSPX-secret'));
    const connect = await call('/admin/gmail/connect');
    const url = new URL(connect.body.auth_url);
    await fetch(`${url.searchParams.get('redirect_uri')}/?code=code-1&state=${url.searchParams.get('state')}`);
    await new Promise(resolve => setTimeout(resolve, 50));
    const status = (await call('/admin/gmail')).body;
    assert.deepEqual([status.connected, status.accounts], [true, [{ email: 'me@gmail.com', error: '', missing_labels: [] }]]);
    assert.ok(!JSON.stringify(status).includes('GOCSPX') && !JSON.stringify(status).includes('rt-good'));
    // Importance definition: example until saved; bounded.
    await writeFile(path.join(f.dir, 'example.md'), '- Hemen cevapla: iş yeri');
    assert.equal((await call('/admin/gmail/importance')).body.content, '- Hemen cevapla: iş yeri');
    assert.equal((await call('/admin/gmail/importance/save', { content: 'x'.repeat(5000) })).status, 400);
    assert.equal((await call('/admin/gmail/importance/save', { content: '- Önemli: fatura' })).status, 200);
    assert.equal((await call('/admin/gmail/importance')).body.content, '- Önemli: fatura');
    // Actions and undo from Admin.
    const proposal = await f.triage(1).propose(live()) as any;
    await f.triage(2).apply(proposal.confirm_token, live());
    const actions = (await call('/admin/gmail/actions')).body;
    assert.deepEqual([actions.batches[0].count, actions.last_plan.items.length], [1, 1]);
    const undone = (await call('/admin/gmail/undo')).body;
    assert.deepEqual([undone.restored, undone.batches[0].undone], [1, true]);
    assert.equal((await call('/admin/gmail/undo')).body.error, 'nothing_to_undo');
    assert.equal((await call('/admin/gmail/disconnect', {})).status, 400);
    assert.equal((await call('/admin/gmail/disconnect', { account: 'me@gmail.com' })).body.connected, false);
  } finally { lifecycle.abort(); await new Promise<void>(resolve => server.close(() => resolve())); await f.close(); }
});
