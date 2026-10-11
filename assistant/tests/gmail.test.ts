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
import { ACTIONS, authenticated, classifyAll, classifyPrompt, parseLevels, ruleLevel } from '../.pi/extensions/gmail/classify.ts';
import { PROFILE_URL, REVOKE_URL, SCOPE, TOKEN_URL, type Http } from '../.pi/extensions/gmail/oauth.ts';
import { MemorySecretStore } from '../.pi/extensions/gmail/secrets.ts';
import { GmailService } from '../.pi/extensions/gmail/service.ts';
import { GmailError } from '../.pi/extensions/gmail/schemas.ts';
import { MailTriage, TriageMemory } from '../.pi/extensions/gmail/triage.ts';
import type { MessageMeta } from '../.pi/extensions/gmail/provider.ts';

// Synthetic mail and a fake Gmail/OAuth transport only: no real network call, nothing is written to a real account.
const GOOGLE_OK = (domain: string) => `mx.google.com; dkim=pass header.i=@${domain} header.s=s1 header.b=x; spf=pass (google.com: domain of a@${domain} designates 1.2.3.4 as permitted sender) smtp.mailfrom=a@${domain}; dmarc=pass (p=REJECT sp=REJECT dis=NONE) header.from=${domain}`;
type Msg = { id: string; labels: string[]; from: string; subject: string; snippet?: string; auth?: string[]; date?: number };
const msg = (id: string, extra: Partial<Msg> = {}): Msg => ({ id, labels: ['INBOX', 'UNREAD', 'CATEGORY_PERSONAL'], from: `Sender ${id} <s${id}@example.org>`, subject: `Subject ${id}`, ...extra });

function fakeGmail(messages: Msg[], options: { email?: string; labels?: { id: string; name: string }[] } = {}) {
  const calls: { method: string; url: string; body?: string }[] = [];
  let access = 0, valid = '', refreshToken = 'rt-good';
  const labels = options.labels ?? [{ id: 'Label_1', name: 'Sonra Oku' }, { id: 'Label_2', name: 'Gereksiz' }, { id: 'INBOX', name: 'INBOX' }];
  const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
  const http: Http = async (url, init) => {
    calls.push({ method: init.method, url, ...(init.body ? { body: init.body } : {}) });
    if (url === TOKEN_URL) {
      const form = new URLSearchParams(init.body);
      if (form.get('grant_type') === 'refresh_token')
        return form.get('refresh_token') === refreshToken ? json({ access_token: valid = 'at' + ++access, expires_in: 3600 }) : json({ error: 'invalid_grant' }, 400);
      if (form.get('code') !== 'code-1' || !form.get('code_verifier')) return json({ error: 'invalid_grant' }, 400);
      return json({ access_token: valid = 'at' + ++access, refresh_token: refreshToken, expires_in: 3600, scope: SCOPE, verifier: form.get('code_verifier') });
    }
    if (url === REVOKE_URL) return new Response('', { status: 200 });
    if (url === PROFILE_URL) return json({ emailAddress: options.email ?? 'me@gmail.com' });
    if (init.headers?.Authorization !== `Bearer ${valid}`) return json({ error: { code: 401 } }, 401);
    const u = new URL(url), route = u.pathname.replace('/gmail/v1/users/me', '');
    if (init.method === 'GET' && route === '/labels') return json({ labels });
    if (init.method === 'GET' && route === '/messages') {
      const label = u.searchParams.get('labelIds')!;
      return json({ messages: messages.filter(m => m.labels.includes(label)).map(m => ({ id: m.id })) });
    }
    const one = /^\/messages\/([^/]+)(\/trash)?$/.exec(route), m = one && messages.find(x => x.id === one[1]);
    if (init.method === 'GET' && m) return json({ id: m.id, labelIds: m.labels, snippet: m.snippet ?? 'snippet', internalDate: String(m.date ?? 0),
      payload: { headers: [{ name: 'From', value: m.from }, { name: 'Subject', value: m.subject }, ...(m.auth ?? []).map(value => ({ name: 'Authentication-Results', value }))] } });
    if (init.method === 'POST' && one?.[2] && m) { m.labels = ['TRASH', ...m.labels.filter(l => l !== 'INBOX')]; return json({ id: m.id }); }
    if (init.method === 'POST' && route === '/messages/batchModify') {
      const body = JSON.parse(init.body!) as { ids: string[]; addLabelIds: string[]; removeLabelIds: string[] };
      for (const id of body.ids) { const x = messages.find(y => y.id === id)!; x.labels = [...x.labels.filter(l => !body.removeLabelIds.includes(l)), ...body.addLabelIds.filter(l => !x.labels.includes(l))]; }
      return new Response(null, { status: 204 });
    }
    return json({ error: 'not found' }, 404);
  };
  return { http, calls, messages, revokeRefresh: () => { refreshToken = 'revoked'; } };
}

async function setup(messages: Msg[], options: { email?: string; labels?: { id: string; name: string }[]; connected?: boolean } = {}) {
  const dir = await mkdtemp(path.join(tmpdir(), 'ayhan-gmail-'));
  const gmail = fakeGmail(messages, options), store = new MemorySecretStore();
  store.values.set('client', JSON.stringify({ client_id: 'id-123456.apps.googleusercontent.com', client_secret: 'GOCSPX-secret' }));
  if (options.connected !== false) store.values.set('token', JSON.stringify({ refresh_token: 'rt-good', email: 'me@gmail.com' }));
  const service = new GmailService({ dataDir: path.join(dir, 'data'), exampleFile: path.join(dir, 'example.md'), store, http: gmail.http });
  let now = Date.UTC(2026, 9, 11, 9);
  const memory = new TriageMemory(new ConfirmationStore(() => now));
  const reminders: BridgeRequest[] = [], notes: string[] = [];
  const settings = { ...defaults, gmail_urgent_senders: ['@work.example', 'mom@example.net'], reminders_enabled: true };
  const triage = (turn: number, extra: { model?: (s: string, u: string) => Promise<string>; settings?: Partial<Config>; proposeOnly?: boolean } = {}) =>
    new MailTriage({ provider: service.provider, settings: { ...settings, ...extra.settings }, memory, turn, logFile: service.logFile, stateFile: service.stateFile,
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

test('OAuth: PKCE + loopback consent stores the refresh token in the secret store; state and account are checked', async () => {
  const f = await setup([], { connected: false });
  try {
    const { auth_url, done } = await f.service.auth.connect('me@gmail.com');
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
    assert.deepEqual(JSON.parse(f.store.values.get('token')!), { refresh_token: 'rt-good', email: 'me@gmail.com' });
    const status = await f.service.auth.status();
    assert.deepEqual(status, { client_configured: true, connected: true, email: 'me@gmail.com', connecting: false });
    assert.ok(!JSON.stringify(status).includes('secret') && !JSON.stringify(status).includes('rt-good'));
  } finally { await f.close(); }
  // Another Google account than the configured one: revoked, nothing stored.
  const g = await setup([], { connected: false, email: 'other@gmail.com' });
  try {
    const { auth_url, done } = await g.service.auth.connect('me@gmail.com');
    const url = new URL(auth_url);
    await fetch(`${url.searchParams.get('redirect_uri')}/?code=code-1&state=${url.searchParams.get('state')}`);
    assert.equal(await code(done), 'gmail_wrong_account');
    assert.ok(!g.store.values.has('token') && g.gmail.calls.some(c => c.url === REVOKE_URL));
  } finally { await g.close(); }
});

test('OAuth: access token refreshes once on 401; a revoked refresh token is gmail_auth_expired; disconnect forgets it', async () => {
  const f = await setup([msg('1')]);
  try {
    assert.equal((await f.service.provider.labels(live())).get('Gereksiz'), 'Label_2');
    assert.equal(f.gmail.calls.filter(c => c.url === TOKEN_URL).length, 1);
    await f.service.provider.labels(live()); // Cached access token.
    assert.equal(f.gmail.calls.filter(c => c.url === TOKEN_URL).length, 1);
    f.gmail.revokeRefresh();
    assert.equal(await code(f.service.provider.labels(live()).then(() => f.service.auth.accessToken(live(), true))), 'gmail_auth_expired');
    await f.service.auth.disconnect();
    assert.ok(!f.store.values.has('token') && f.store.values.has('client'));
    assert.equal(await code(f.service.provider.labels(live())), 'gmail_not_connected');
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
    assert.deepEqual(proposal.urgent, [{ from: 'Boss', subject: 'Subject u1' }]);
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

test('config: urgent senders validated and lowercased; Gmail secrets never go to config.json', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'ayhan-gmail-config-'));
  try {
    const file = path.join(dir, 'config.json');
    await writeFile(file, JSON.stringify({ gmail_urgent_senders: ['Boss@Work.example', '@Council.gov.uk', '@council.gov.uk'], gmail_triage_enabled: true }));
    const config = await loadConfig(file);
    assert.deepEqual(config.gmail_urgent_senders, ['boss@work.example', '@council.gov.uk']);
    for (const bad of [['not an address'], ['@'], 'x', [1]]) {
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
  await writeFile(configPath, JSON.stringify({ language: 'tr' }));
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
      account: 'Me@Gmail.com', triage_enabled: true, urgent_senders: ['@Work.example'] });
    assert.equal(saved.status, 200, JSON.stringify(saved.body));
    assert.deepEqual([saved.body.client_configured, saved.body.connected, saved.body.account, saved.body.urgent_senders], [true, false, 'me@gmail.com', ['@work.example']]);
    const file = await readFile(configPath, 'utf8');
    assert.ok(!file.includes('GOCSPX') && !file.includes('googleusercontent'));
    assert.deepEqual(JSON.parse(file), { language: 'tr', gmail_account: 'me@gmail.com', gmail_triage_enabled: true, gmail_urgent_senders: ['@work.example'] });
    assert.ok(f.store.values.get('client')!.includes('GOCSPX-secret'));
    const connect = await call('/admin/gmail/connect');
    const url = new URL(connect.body.auth_url);
    assert.equal(url.searchParams.get('login_hint'), 'me@gmail.com');
    await fetch(`${url.searchParams.get('redirect_uri')}/?code=code-1&state=${url.searchParams.get('state')}`);
    await new Promise(resolve => setTimeout(resolve, 50));
    const status = (await call('/admin/gmail')).body;
    assert.deepEqual([status.connected, status.email, status.missing_labels], [true, 'me@gmail.com', []]);
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
    assert.equal((await call('/admin/gmail/actions')).body.batches[0].count, 1);
    const undone = (await call('/admin/gmail/undo')).body;
    assert.deepEqual([undone.restored, undone.batches[0].undone], [1, true]);
    assert.equal((await call('/admin/gmail/undo')).body.error, 'nothing_to_undo');
    assert.equal((await call('/admin/gmail/disconnect')).body.connected, false);
  } finally { lifecycle.abort(); await new Promise<void>(resolve => server.close(() => resolve())); await f.close(); }
});
