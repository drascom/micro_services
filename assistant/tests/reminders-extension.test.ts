import test from 'node:test';
import assert from 'node:assert/strict';
import { chmod, mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { ModelRuntime, loadSkillsFromDir } from '@earendil-works/pi-coding-agent';
import { fauxProvider, fauxAssistantMessage, fauxToolCall, type AssistantMessage, type Context } from '@earendil-works/pi-ai';
import { defaults, root, type Config } from '../src/config.ts';
import { createHarnessSession, harnessTools } from '../src/harness.ts';
import { RemindersClient, RemindersError, classifyStderr, formatDue, osascriptTransport, parseDue,
  type RemindersTransport } from '../.pi/extensions/apple-reminders/client.ts';
import { CONFIRM_TTL_MS, ConfirmationStore } from '../.pi/extensions/apple-reminders/confirmations.ts';
import type { BridgeReminder, BridgeRequest, RemindersInput } from '../.pi/extensions/apple-reminders/schemas.ts';

// Synthetic data only; the real Reminders app is never contacted.
const NOW = new Date(2026, 9, 10, 12, 0).getTime();
const item = (n: number, extra: Partial<BridgeReminder> = {}): BridgeReminder => ({ id: `x-apple-reminder://SYN-${n}`, title: `Synthetic ${n}`,
  notes: '', due: new Date(2026, 9, 10 + n, 9, 30).toISOString(), all_day: false, priority: 0, completed: false, list: 'Synthetic', ...extra });

function bridge(respond?: (request: BridgeRequest) => unknown) {
  const requests: BridgeRequest[] = [];
  const transport: RemindersTransport = {
    async request(request) {
      requests.push(request);
      if (respond) return JSON.stringify(respond(request));
      if (request.op === 'list') return JSON.stringify({ ok: true, matched: 3, reminders: [item(1), item(2, { due: null }), item(3)].slice(0, request.max) });
      if (request.op === 'delete') return JSON.stringify({ ok: true, deleted: true });
      if (request.op === 'add') return JSON.stringify({ ok: true, reminder: item(9, { title: request.title, list: request.list ?? 'Default' }) });
      return JSON.stringify({ ok: true, reminder: item(1, { completed: request.op === 'complete' }) });
    }
  };
  return { transport, requests };
}
const live = () => new AbortController().signal;
const enabled = { reminders_enabled: true };
function client(options: { turn?: number; store?: ConfirmationStore; transport?: RemindersTransport; now?: () => number; settings?: { reminders_enabled: boolean } } = {}) {
  const b = bridge();
  const c = new RemindersClient({ settings: options.settings ?? enabled, transport: options.transport ?? b.transport,
    store: options.store ?? new ConfirmationStore(() => NOW), turn: options.turn ?? 1, now: options.now ?? (() => NOW) });
  return { client: c, requests: b.requests, run: (input: RemindersInput) => c.run(input, live()) };
}
async function code(promise: Promise<unknown>): Promise<string> {
  try { await promise; } catch (error) { if (error instanceof RemindersError) return error.code; throw error; }
  return 'ok';
}
const refOf = (result: Record<string, unknown>, i = 0) => (result.reminders as { ref: string }[])[i].ref;

test('due dates: local date-only is all-day, local date-time keeps wall clock, impossible dates are rejected', () => {
  assert.deepEqual(parseDue('2026-10-11'), { time: new Date(2026, 9, 11).getTime(), all_day: true });
  assert.deepEqual(parseDue('2026-10-11T09:05'), { time: new Date(2026, 9, 11, 9, 5).getTime(), all_day: false });
  assert.equal(parseDue('2026-10-11T09:05Z')?.time, Date.UTC(2026, 9, 11, 9, 5));
  for (const bad of ['2026-02-30', '2026-13-01', 'tomorrow', '2026-10-11T25:00', '11/10/2026', '1999-01-01', ''])
    assert.equal(parseDue(bad), null, bad);
  assert.equal(formatDue(new Date(2026, 9, 11, 9, 5).toISOString(), false), '2026-10-11 09:05');
  assert.equal(formatDue(new Date(2026, 9, 11).toISOString(), true), '2026-10-11');
  assert.equal(formatDue(null, false), undefined);
});

test('list: opaque refs, short local dates, status/range/limit become bridge filters', async () => {
  const c = client();
  const result = await c.run({ action: 'list', limit: 2 });
  assert.deepEqual(c.requests[0], { op: 'list', status: 'open', undated: true, max: 2 });
  const reminders = result.reminders as { ref: string; due?: string; title: string }[];
  assert.equal(reminders.length, 2); assert.match(reminders[0].ref, /^r_[A-Za-z0-9_-]{12}$/);
  assert.equal(reminders[0].due, '2026-10-11 09:30'); assert.equal(reminders[1].due, undefined);
  assert.equal(result.truncated, true);
  assert.ok(!JSON.stringify(result).includes('x-apple-reminder'), 'internal ids never reach the model');
  await c.run({ action: 'list', range: 'today', status: 'all', list: 'Work' });
  assert.deepEqual(c.requests[1], { op: 'list', list: 'Work', status: 'all', due_before: new Date(2026, 9, 11).toISOString(), undated: false, max: 20 });
  await c.run({ action: 'list', range: 'overdue' });
  assert.equal((c.requests[2] as { due_before: string }).due_before, new Date(NOW).toISOString());
  await c.run({ action: 'list', range: 'upcoming' });
  assert.deepEqual([(c.requests[3] as { due_after: string }).due_after, (c.requests[3] as { due_before: string }).due_before],
    [new Date(NOW).toISOString(), new Date(2026, 9, 18).toISOString()]);
});

test('add: default list unless named, priority mapping, all-day dates; empty title and invalid dates are rejected', async () => {
  const c = client();
  const added = await c.run({ action: 'add', title: '  Synthetic task ', due: '2026-10-12', priority: 'high' });
  assert.deepEqual(c.requests[0], { op: 'add', title: 'Synthetic task', due: new Date(2026, 9, 12).toISOString(), all_day: true, priority: 1 });
  assert.match((added.added as { ref: string }).ref, /^r_/);
  await c.run({ action: 'add', title: 'Other', list: 'Work', due: '2026-10-12T08:00', notes: 'n' });
  assert.deepEqual(c.requests[1], { op: 'add', title: 'Other', notes: 'n', due: new Date(2026, 9, 12, 8).toISOString(), all_day: false, list: 'Work' });
  assert.equal(await code(c.run({ action: 'add', title: '   ' })), 'empty_title');
  assert.equal(await code(c.run({ action: 'add' })), 'empty_title');
  assert.equal(await code(c.run({ action: 'add', title: 'x', due: 'yarın' })), 'invalid_date');
  assert.equal(await code(c.run({ action: 'add', title: 'x', notes: 'y'.repeat(1001) })), 'invalid_request');
  assert.equal(c.requests.length, 2);
});

test('update/complete use refs from this request, carry the listed title and reject unknown refs', async () => {
  const c = client();
  const ref = refOf(await c.run({ action: 'list' }));
  await c.run({ action: 'update', ref, due: 'none', title: 'Renamed' });
  assert.deepEqual(c.requests[1], { op: 'update', id: 'x-apple-reminder://SYN-1', expected_title: 'Synthetic 1', title: 'Renamed', due: null });
  await c.run({ action: 'complete', ref });
  assert.deepEqual(c.requests[2], { op: 'complete', id: 'x-apple-reminder://SYN-1', expected_title: 'Synthetic 1' });
  assert.equal(await code(c.run({ action: 'complete', ref: 'r_unknownref00' })), 'unknown_reference');
  assert.equal(await code(c.run({ action: 'complete' })), 'invalid_request');
  assert.equal(await code(c.run({ action: 'update', ref })), 'invalid_request');
  assert.equal(await code(c.run({ action: 'update', ref, due: '2026-02-30' })), 'invalid_date');
  assert.equal(c.requests.length, 3);
});

test('delete needs a token confirmed in the next turn: same turn, expiry, skipped turn, wrong ref and reuse fail', async () => {
  let now = NOW;
  const store = new ConfirmationStore(() => now);
  const first = client({ store, turn: 5 });
  const ref = refOf(await first.run({ action: 'list' }));
  const asked = await first.run({ action: 'delete', ref });
  assert.equal(asked.status, 'confirmation_required'); assert.equal(asked.title, 'Synthetic 1');
  const token = asked.confirm_token as string; assert.match(token, /^c_[A-Za-z0-9_-]{12}$/);
  // Same turn: the model cannot confirm its own question; the token survives for the user's reply.
  assert.equal(await code(first.run({ action: 'delete', confirm_token: token })), 'confirmation_needs_reply');
  assert.ok(!first.requests.some(r => r.op === 'delete'));
  assert.deepEqual(store.pending(6), { token, title: 'Synthetic 1' }); assert.equal(store.pending(5), undefined);
  // Next turn: wrong token / wrong ref are rejected, then the right token deletes exactly the bound reminder once.
  const next = client({ store, turn: 6 });
  assert.equal(await code(next.run({ action: 'delete', confirm_token: 'c_wrongtoken00' })), 'confirmation_invalid');
  assert.equal(await code(next.run({ action: 'delete', confirm_token: token, ref: 'r_otherref0000' })), 'confirmation_invalid');
  assert.deepEqual(await next.run({ action: 'delete', confirm_token: token }), { deleted: 'Synthetic 1' });
  assert.deepEqual(next.requests, [{ op: 'delete', id: 'x-apple-reminder://SYN-1', expected_title: 'Synthetic 1' }]);
  assert.equal(await code(next.run({ action: 'delete', confirm_token: token })), 'confirmation_invalid');
  // Expiry after 2 minutes.
  const late = await first.run({ action: 'delete', ref });
  now += CONFIRM_TTL_MS + 1;
  assert.equal(await code(client({ store, turn: 6 }).run({ action: 'delete', confirm_token: late.confirm_token as string })), 'confirmation_expired');
  // Only the immediately following turn counts.
  now = NOW;
  const skipped = await first.run({ action: 'delete', ref });
  assert.equal(await code(client({ store, turn: 7 }).run({ action: 'delete', confirm_token: skipped.confirm_token as string })), 'confirmation_expired');
  // Unknown reference never issues a token.
  assert.equal(await code(first.run({ action: 'delete', ref: 'r_unknownref00' })), 'unknown_reference');
});

test('a reminder changed after confirmation is not deleted (bridge stale_reference)', async () => {
  const store = new ConfirmationStore(() => NOW);
  const b = bridge(request => request.op === 'delete' ? { ok: false, error: 'stale_reference' } : { ok: true, matched: 1, reminders: [item(1)] });
  const first = client({ store, turn: 1, transport: b.transport });
  const token = (await first.run({ action: 'delete', ref: refOf(await first.run({ action: 'list' })) })).confirm_token as string;
  assert.equal(await code(client({ store, turn: 2, transport: b.transport }).run({ action: 'delete', confirm_token: token })), 'stale_reference');
});

test('disabled settings fail closed; bridge and stderr errors map to generic codes', async () => {
  const c = client({ settings: { reminders_enabled: false } });
  assert.equal(await code(c.run({ action: 'list' })), 'reminders_disabled'); assert.equal(c.requests.length, 0);
  for (const [answer, expected] of [[{ ok: false, error: 'automation_denied' }, 'automation_denied'], [{ ok: false, error: 'list_not_found' }, 'list_not_found'],
    [{ ok: false, error: 'secret detail' }, 'invalid_response'], [{ ok: true, matched: 1, reminders: [{ id: 1 }] }, 'invalid_response'], ['nope', 'invalid_response']] as const) {
    const b = bridge(() => answer);
    assert.equal(await code(client({ transport: b.transport }).run({ action: 'list' })), expected);
  }
  assert.equal(classifyStderr('execution error: Not authorized to send Apple events to Reminders. (-1743)'), 'automation_denied');
  assert.equal(classifyStderr('something else'), 'reminders_unavailable');
});

test('osascript transport: absolute bin, fixed script, stdin JSON and permission errors', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'ayhan-reminders-bin-'));
  try {
    const request = { op: 'complete', id: 'x', expected_title: 'y' } as const;
    await assert.rejects(osascriptTransport({ bin: 'osascript' }).request(request, live()), (e: RemindersError) => e.code === 'reminders_unavailable');
    const outside = path.join(dir, 'other.js'); await writeFile(outside, '');
    await assert.rejects(osascriptTransport({ script: outside }).request(request, live()), (e: RemindersError) => e.code === 'invalid_response');
    const echo = path.join(dir, 'echo'); await writeFile(echo, '#!/bin/sh\ncat\n'); await chmod(echo, 0o755);
    assert.deepEqual(JSON.parse(await osascriptTransport({ bin: echo }).request(request, live())), request);
    const denied = path.join(dir, 'denied'); await writeFile(denied, '#!/bin/sh\necho "Not authorized (-1743)" >&2\nexit 1\n'); await chmod(denied, 0o755);
    await assert.rejects(osascriptTransport({ bin: denied }).request(request, live()), (e: RemindersError) => e.code === 'automation_denied');
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('reminders skill parses with the harness native parser; config keeps Reminders off by default', () => {
  const result = loadSkillsFromDir({ dir: path.join(root, '.pi/skills/reminders'), source: 'test' });
  assert.deepEqual(result.diagnostics, []); assert.equal(result.skills[0]?.name, 'reminders');
  assert.equal(defaults.reminders_enabled, false);
  assert.ok(!harnessTools(undefined, true).includes('reminders'));
  assert.deepEqual(harnessTools(undefined, true, true), ['read', 'reminders']);
  assert.deepEqual(harnessTools('reminders', false, true), ['reminders']);
});

// --- Pi harness integration (fake bridge) ---
const call = (args: Record<string, unknown>) =>
  fauxAssistantMessage(fauxToolCall('reminders', args as Record<string, string>, { id: 'r' + Math.random() }), { stopReason: 'toolUse' });
const resultsText = (context: Context) => JSON.stringify(context.messages.filter(m => m.role === 'toolResult'));
type Response = AssistantMessage | ((context: Context) => AssistantMessage);
async function harness(responses: Response[], options: { permission?: string; config?: Partial<Config>; turn: number; store: ConfirmationStore;
  transport: RemindersTransport; skill?: 'online-search' }) {
  const dir = await mkdtemp(path.join(tmpdir(), 'ayhan-reminders-harness-'));
  await mkdir(path.join(dir, '.pi/skills/reminders'), { recursive: true }); await mkdir(path.join(dir, '.pi/extensions'), { recursive: true });
  await writeFile(path.join(dir, '.pi/skills/reminders/SKILL.md'), '---\nname: reminders\ndescription: Synthetic reminders skill.\n---\n# r\n');
  await writeFile(path.join(dir, '.pi/permissions.json'), JSON.stringify(options.permission ? { reminders: options.permission } : {}));
  const fake = fauxProvider({ provider: 'reminders-test', models: [{ id: 'test' }], tokensPerSecond: 1000000 });
  fake.setResponses(responses as never);
  const runtime = await ModelRuntime.create({ modelsPath: null, refreshOnCreate: false }); runtime.registerNativeProvider(fake.provider);
  const session = await createHarnessSession({ root: dir, config: { ...defaults, reminders_enabled: true, pi_agent_dir: path.join(dir, 'agent'), ...options.config },
    context: { systemPrompt: 'Reminders test rules', messages: [{ role: 'user', content: 'test', timestamp: 1 }] },
    model: fake.getModel(), runtime, offline: true, extensionPaths: [], skill: options.skill,
    turn: options.turn, remindersStore: options.store, remindersTransport: options.transport });
  const results = () => session.messages.filter(m => m.role === 'toolResult');
  return { session, results, cleanup: async () => { await session.abort(); session.dispose(); await rm(dir, { recursive: true, force: true }); } };
}

test('turned off in Admin: the tool and its skill are not offered to the model at all', async () => {
  const b = bridge();
  const h = await harness([fauxAssistantMessage('ok')], { config: { reminders_enabled: false }, turn: 1, store: new ConfirmationStore(), transport: b.transport });
  try {
    assert.ok(!h.session.getActiveToolNames().includes('reminders'));
    assert.equal(h.session.getToolDefinition('reminders'), undefined);
    assert.ok(!h.session.systemPrompt.includes('Synthetic reminders skill'));
  } finally { await h.cleanup(); }
});

test('harness: list/add run without approval offline; an explicit deny blocks before execution', async () => {
  const b = bridge();
  const h = await harness([call({ action: 'list' }), call({ action: 'add', title: 'Synthetic' }), fauxAssistantMessage('done')],
    { turn: 1, store: new ConfirmationStore(), transport: b.transport });
  try {
    assert.ok(h.session.getActiveToolNames().includes('reminders'));
    assert.ok(h.session.systemPrompt.includes('Synthetic reminders skill') && /Local time: \d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(h.session.systemPrompt));
    await h.session.prompt('hatırlatıcılar');
    assert.deepEqual(h.results().map(r => r.isError), [false, false]); assert.deepEqual(b.requests.map(r => r.op), ['list', 'add']);
  } finally { await h.cleanup(); }
  const d = bridge();
  const denied = await harness([call({ action: 'list' }), fauxAssistantMessage('blocked')], { permission: 'deny', turn: 1, store: new ConfirmationStore(), transport: d.transport });
  try { await denied.session.prompt('x'); assert.equal(d.requests.length, 0); assert.equal(denied.results()[0].isError, true); }
  finally { await denied.cleanup(); }
});

test('harness: voice confirmation spans two user turns; the model cannot confirm in the same turn', async () => {
  const store = new ConfirmationStore(), b = bridge();
  let token = '';
  const first = await harness([call({ action: 'list' }),
    context => call({ action: 'delete', ref: /r_[A-Za-z0-9_-]{12}/.exec(resultsText(context))![0] }),
    context => { token = /c_[A-Za-z0-9_-]{12}/.exec(resultsText(context))![0]; return call({ action: 'delete', confirm_token: token }); },
    fauxAssistantMessage('Synthetic 1 silinsin mi?')], { turn: 40, store, transport: b.transport });
  try {
    await first.session.prompt('ilk hatırlatıcıyı sil');
    assert.deepEqual(first.results().map(r => r.isError), [false, false, true]);
    assert.match(JSON.stringify(first.results()[2].content), /confirmation_needs_reply/);
    assert.ok(!b.requests.some(r => r.op === 'delete'));
  } finally { await first.cleanup(); }
  // The next user message ("evet"): the pending deletion is announced and the tool is reachable even on another route.
  const second = await harness([call({ action: 'delete', confirm_token: token }), fauxAssistantMessage('Silindi.')],
    { turn: 41, store, transport: b.transport, skill: 'online-search' });
  try {
    assert.ok(second.session.systemPrompt.includes(token) && second.session.systemPrompt.includes('"Synthetic 1"'));
    assert.ok(second.session.getActiveToolNames().includes('reminders'));
    await second.session.prompt('evet');
    assert.deepEqual(second.results().map(r => r.isError), [false]);
    assert.deepEqual(b.requests.filter(r => r.op === 'delete'), [{ op: 'delete', id: 'x-apple-reminder://SYN-1', expected_title: 'Synthetic 1' }]);
  } finally { await second.cleanup(); }
});
