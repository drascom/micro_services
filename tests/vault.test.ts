import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { ModelRuntime, type ExtensionAPI } from '@earendil-works/pi-coding-agent';
import { fauxProvider, fauxAssistantMessage, fauxToolCall } from '@earendil-works/pi-ai';
import { defaults, root, type Config } from '../src/config.ts';
import { createHarnessSession, harnessTools } from '../src/harness.ts';
import { VaultError, VaultService, spellSecret, type BwTransport, type Clipboard, type SecretSpeaker } from '../src/vault.ts';
import { observeRequest } from '../src/activity.ts';
import { Speech, type SpeechService } from '../src/speech.ts';
import { Orchestrator } from '../src/orchestrator.ts';
import { makeServer } from '../src/server.ts';
import { registeredSecretSpeaker } from '../src/vault.ts';
import { chmod, readdir, readFile, writeFile } from 'node:fs/promises';
import { once } from 'node:events';
import { permissionsExtension } from '../.pi/extensions/permissions.ts';
import { bitwardenVault } from '../.pi/extensions/vault/index.ts';
import { ConfirmationStore } from '../.pi/extensions/apple-reminders/confirmations.ts';
import { SecretPrompts } from '../src/vault.ts';
import { unlockVault } from '../src/admin.ts';

const PASSWORD = 'Master-Pa55-secret!';
const SESSION = 'c2Vzc2lvbktleUZvclRlc3RzMTIzNDU2Nzg5MA==';
const ID = '11111111-2222-3333-4444-555555555555';
const SECRETS = ['hunter2-site-password', 'private note text', 'JBSWY3DPEHPK3PXP', '4111111111111111', 'hidden-field-value', '123', '654321'];
const ITEMS = [
  { id: ID, name: 'GitHub', notes: 'private note text', type: 1, fields: [{ name: 'pin', value: 'hidden-field-value', type: 1 }],
    login: { username: 'drascom', password: 'hunter2-site-password', totp: 'otpauth://totp/x?secret=JBSWY3DPEHPK3PXP', uris: [{ uri: 'https://github.com/login' }] } },
  { id: '99999999-2222-3333-4444-555555555555', name: 'Bank card', type: 3, card: { number: '4111111111111111', code: '123' } }
];

/** Drops clock- and run-dependent fields (timestamps, durations, faux api ids, request ids, token usage) so short
 * numeric secrets such as '123' are not matched by chance inside them (that made leak checks flaky). */
const stable = (text: string) => text
  .replace(/"usage":\{[^{}]*\{[^{}]*\}\}/g, '')
  .replace(/\\?"(timestamp|durationMs|api|time|request|elapsed_ms)\\?":(\\?"[^"\\]*\\?"|\d+)/g, '');

/** Fake `bw`: records every call (argv and env) and answers like the CLI. */
function fakeBw(options: { loggedIn?: boolean; wrongPassword?: boolean; twoFactor?: boolean } = {}) {
  const calls: { args: string[]; env: Record<string, string>; input?: string }[] = [];
  const saved: Record<string, unknown>[] = [];
  let loggedIn = options.loggedIn ?? true;
  const transport: BwTransport = async (args, env, _signal, input) => {
    calls.push({ args, env, input });
    const ok = (stdout: string) => ({ code: 0, stdout, stderr: '' });
    const unlocked = env.BW_SESSION === SESSION;
    switch (args[0]) {
      case 'status': return ok(JSON.stringify({ serverUrl: 'https://pass.drascom.uk', status: loggedIn ? 'locked' : 'unauthenticated' }));
      case 'config': return ok('Saved setting `config`.');
      case 'login':
        if (options.twoFactor && !args.includes('--code')) return { code: 1, stdout: '', stderr: 'Code is required.' };
        if (env.BW_PASSWORD !== PASSWORD) return { code: 1, stdout: '', stderr: 'Username or password is incorrect. Try again' };
        loggedIn = true; return ok(SESSION);
      case 'unlock': return env.BW_PASSWORD === PASSWORD && !options.wrongPassword ? ok(SESSION) : { code: 1, stdout: '', stderr: 'Invalid master password.' };
      case 'sync': case 'lock': return ok('');
      case 'generate': return ok('Gen3rated-Strong-Passw0rd!xyz');
      case 'create': case 'edit':
        if (!unlocked || !input) return { code: 1, stdout: '', stderr: 'Vault is locked.' };
        saved.push({ command: args[0], id: args[2], ...JSON.parse(Buffer.from(input, 'base64').toString()) }); return ok('{}');
      case 'list': return unlocked ? ok(JSON.stringify(ITEMS)) : { code: 1, stdout: '', stderr: 'Vault is locked.' };
      case 'get':
        if (!unlocked) return { code: 1, stdout: '', stderr: 'Vault is locked.' };
        if (args[1] === 'totp') return ok('654321');
        return args[2] === ID ? ok(JSON.stringify(ITEMS[0])) : { code: 1, stdout: '', stderr: 'Not found.' };
    }
    return { code: 1, stdout: '', stderr: 'unknown' };
  };
  return { transport, calls, saved };
}

function fakeClipboard(): Clipboard & { value: string; writes: string[] } {
  const board = { value: 'before', writes: [] as string[],
    async read() { return board.value; }, async write(v: string) { board.value = v; board.writes.push(v); } };
  return board;
}

/** bitwarden_bin must exist for unlock: point it at this test file. */
const config = (change: Partial<Config> = {}): Config => ({ ...defaults, bitwarden_enabled: true, bitwarden_email: 'me@example.com',
  bitwarden_bin: fileURLToPath(import.meta.url), ...change });

async function unlocked(change: Partial<Config> = {}, clearMs = 30_000, ask?: (title: string) => Promise<string | undefined>) {
  const bw = fakeBw(), clipboard = fakeClipboard();
  const vault = new VaultService(config(change), { transport: bw.transport, clipboard, clearMs, ask });
  await vault.unlock(PASSWORD);
  return { vault, bw, clipboard };
}

test('unlock: master password only in the child env, never argv; session key kept in memory and passed by env', async () => {
  const { vault, bw } = await unlocked();
  assert.ok(vault.unlocked);
  for (const call of bw.calls) {
    assert.ok(!call.args.some(arg => arg.includes(PASSWORD) || arg.includes(SESSION)), call.args.join(' '));
    assert.ok(!call.args.includes('--session'));
  }
  assert.equal(bw.calls.find(c => c.args[0] === 'unlock')!.env.BW_PASSWORD, PASSWORD);
  assert.deepEqual(bw.calls.find(c => c.args[0] === 'unlock')!.args, ['unlock', '--passwordenv', 'BW_PASSWORD', '--raw']);
  assert.equal(bw.calls.find(c => c.args[0] === 'sync')!.env.BW_SESSION, SESSION);
  assert.ok(!bw.calls.find(c => c.args[0] === 'sync')!.env.BW_PASSWORD, 'the password is used for one call only');
});

test('login when logged out: email from config, 2FA code required, errors carry codes only', async () => {
  const bw = fakeBw({ loggedIn: false, twoFactor: true });
  const vault = new VaultService(config(), { transport: bw.transport, clipboard: fakeClipboard() });
  await assert.rejects(vault.unlock(PASSWORD), (e: VaultError) => e.code === 'two_factor_required' && !e.message.includes(PASSWORD));
  assert.equal(vault.lastError, 'two_factor_required');
  await vault.unlock(PASSWORD, '123456');
  const login = bw.calls.filter(c => c.args[0] === 'login').at(-1)!;
  assert.deepEqual(login.args, ['login', 'me@example.com', '--passwordenv', 'BW_PASSWORD', '--raw', '--method', '0', '--code', '123456']);
  assert.ok(vault.unlocked);
  const wrong = new VaultService(config(), { transport: fakeBw({ wrongPassword: true }).transport, clipboard: fakeClipboard() });
  await assert.rejects(wrong.unlock('nope'), (e: VaultError) => e.code === 'invalid_password' && !e.message.includes('nope'));
  assert.ok(!wrong.unlocked);
});

test('Admin unlock refuses while Bitwarden is off', async () => {
  const vault = new VaultService(config({ bitwarden_enabled: false }), { transport: fakeBw().transport, clipboard: fakeClipboard() });
  await assert.rejects(unlockVault(vault.config, vault, { password: PASSWORD }), (e: VaultError) => e.code === 'disabled');
});

test('search never returns passwords, notes, card numbers, hidden fields or the TOTP secret', async () => {
  const { vault } = await unlocked();
  const results = await vault.search('git', AbortSignal.timeout(1000));
  assert.deepEqual(results[0], { id: ID, name: 'GitHub', username: 'drascom', host: 'github.com', has_totp: true });
  const text = JSON.stringify(results);
  for (const secret of SECRETS) assert.ok(!text.includes(secret), secret);
});

test('copy puts the value on the clipboard, returns no value, and clears it after 30 s only if unchanged', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { vault, clipboard } = await unlocked();
  const result = await vault.copy(ID, 'password', AbortSignal.timeout(1000));
  assert.deepEqual(result, { item: 'GitHub' });
  assert.equal(clipboard.value, 'hunter2-site-password');
  t.mock.timers.tick(30_000); await new Promise(resolve => setImmediate(resolve));
  assert.equal(clipboard.value, '');
  await vault.copy(ID, 'totp', AbortSignal.timeout(1000));
  assert.equal(clipboard.value, '654321');
  clipboard.value = 'user copied something else';
  t.mock.timers.tick(30_000); await new Promise(resolve => setImmediate(resolve));
  assert.equal(clipboard.value, 'user copied something else');
  await assert.rejects(vault.copy('not-an-id', 'password', AbortSignal.timeout(1000)), (e: VaultError) => e.code === 'invalid_request');
});

test('idle auto-lock drops the session key; Lock does too', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { vault, bw } = await unlocked({ bitwarden_idle_lock_min: 2 });
  t.mock.timers.tick(60_000);
  await vault.search('git', AbortSignal.timeout(1000)); // Use restarts the timer.
  t.mock.timers.tick(119_000); assert.ok(vault.unlocked);
  t.mock.timers.tick(1_000); assert.ok(!vault.unlocked);
  await assert.rejects(vault.search('git', AbortSignal.timeout(1000)), (e: VaultError) => e.code === 'locked');
  t.mock.timers.reset();
  const again = await unlocked();
  await again.vault.lock(); assert.ok(!again.vault.unlocked);
  assert.ok(bw.calls.length > 0);
});

async function session(options: { provider?: string; enabled?: boolean; unlock?: boolean; skill?: 'vault'; automated?: boolean; speak?: SecretSpeaker; responses?: Parameters<ReturnType<typeof fauxProvider>['setResponses']>[0] } = {}) {
  const agent = await mkdtemp(path.join(tmpdir(), 'ayhan-vault-'));
  const fake = fauxProvider({ provider: options.provider ?? 'local-gemma', models: [{ id: 'test' }], tokensPerSecond: 1000000 });
  fake.setResponses(options.responses ?? [fauxAssistantMessage('ok')]);
  const runtime = await ModelRuntime.create({ modelsPath: null, refreshOnCreate: false }); runtime.registerNativeProvider(fake.provider);
  const bw = fakeBw(), clipboard = fakeClipboard();
  const cfg = config({ pi_agent_dir: agent, bitwarden_enabled: options.enabled ?? true });
  const vault = new VaultService(cfg, { transport: bw.transport, clipboard });
  if (options.unlock ?? true) { cfg.bitwarden_enabled = true; await vault.unlock(PASSWORD); cfg.bitwarden_enabled = options.enabled ?? true; }
  const s = await createHarnessSession({ root, config: cfg, vault, context: { systemPrompt: defaults.system_prompt, messages: [{ role: 'user', content: 'test', timestamp: 1 }] },
    model: fake.getModel(), runtime, offline: true, skill: options.skill, automated: options.automated, extensionPaths: [], speakSecret: options.speak });
  return { session: s, clipboard, cleanup: async () => { s.dispose(); await rm(agent, { recursive: true, force: true }); } };
}

test('vault tools exist only for the local model, switch on and unlocked; never advertised otherwise', async () => {
  const cases = [
    { name: 'local, unlocked', expect: true },
    { name: 'remote model', provider: 'openai-codex', expect: false },
    { name: 'disabled', enabled: false, expect: false },
    { name: 'locked', unlock: false, expect: false },
    { name: 'automated task', automated: true, expect: false }
  ];
  for (const c of cases) {
    for (const skill of [undefined, 'vault'] as const) {
      const s = await session({ ...c, skill });
      try {
        const names = s.session.getActiveToolNames();
        assert.equal(names.includes('vault_search') && names.includes('vault_copy') && names.includes('vault_save'), c.expect, `${c.name} ${skill}`);
        if (!c.expect) assert.ok(!names.some(n => n.startsWith('vault_')), `${c.name} ${skill}`);
        if (!c.expect && !skill) assert.ok(!/vault/i.test(s.session.systemPrompt), `${c.name}: not advertised`);
      } finally { await s.cleanup(); }
    }
  }
  const remote = await session({ provider: 'openai-codex', skill: 'vault' });
  try { assert.match(remote.session.systemPrompt, /works only with the local model/); assert.ok(!/vault_/.test(remote.session.systemPrompt)); }
  finally { await remote.cleanup(); }
  assert.deepEqual(harnessTools('vault', true, false, false, false, false), []);
  assert.deepEqual(harnessTools('vault', true, false, false, false, true), ['vault_search', 'vault_copy', 'vault_save']);
});

test('end to end: tool results sent to the local model never contain a secret', async () => {
  const call = (name: string, args: Record<string, string>) => fauxAssistantMessage(fauxToolCall(name, args, { id: name }), { stopReason: 'toolUse' });
  const s = await session({ skill: 'vault', responses: [call('vault_search', { query: 'github' }), call('vault_copy', { id: ID, field: 'password' }),
    fauxAssistantMessage('GitHub parolası panoya kopyalandı.')] });
  try {
    await s.session.prompt('github şifrem');
    assert.equal(s.clipboard.value, 'hunter2-site-password');
    const results = s.session.messages.filter(m => m.role === 'toolResult');
    assert.equal(results.length, 2);
    assert.ok(results.every(m => !m.isError));
    const text = stable(JSON.stringify(s.session.messages));
    for (const secret of SECRETS) assert.ok(!text.includes(secret), secret);
    assert.match(text, /\\"copied\\":true/);
  } finally { await s.cleanup(); }
});

test('permissions block vault tools for a non-local session or a task (defence in depth)', async () => {
  const decide = async (options: { local?: boolean; automated?: boolean }) => {
    let handler: ((event: unknown) => unknown) | undefined;
    const pi = { on: (name: string, fn: (event: unknown) => unknown) => { if (name === 'tool_call') handler = fn; } } as unknown as ExtensionAPI;
    permissionsExtension({ root, offline: true, ...options })(pi);
    return await handler!({ toolName: 'vault_copy', input: { id: ID, field: 'password' } }) as { block?: boolean } | undefined;
  };
  assert.equal((await decide({ local: false }))?.block, true);
  assert.equal((await decide({}))?.block, true);
  assert.equal((await decide({ local: true, automated: true }))?.block, true);
  assert.equal(await decide({ local: true }), undefined);
});

/** The vault extension's tools against a fake pi (one call = one turn's tool use). */
function vaultTools(vault: VaultService, turn: number, store: ConfirmationStore) {
  const tools = new Map<string, { execute: (id: string, params: unknown, signal?: AbortSignal) => Promise<{ content: { text: string }[]; isError?: boolean }> }>();
  bitwardenVault({ enabled: true, service: vault, turn, store })({ registerTool: (tool: { name: string }) => tools.set(tool.name, tool as never) } as unknown as ExtensionAPI);
  return (name: string, params: unknown) => tools.get(name)!.execute('t', params, AbortSignal.timeout(2000)).then(r => ({ text: r.content[0].text, error: r.isError === true }));
}
const NEW_SECRET = 'ghp_newTokenValue123456';

test('save from clipboard: new item, value only in the bw stdin, result has no value, clipboard cleared', async () => {
  const { vault, bw, clipboard } = await unlocked();
  clipboard.value = NEW_SECRET + '\n';
  const save = vaultTools(vault, 1, new ConfirmationStore());
  const result = await save('vault_save', { name: 'GitLab token', source: 'clipboard', kind: 'token' });
  assert.deepEqual(JSON.parse(result.text), { saved: true, item: 'GitLab token', kind: 'token' });
  assert.ok(!result.text.includes(NEW_SECRET));
  assert.equal((bw.saved[0].login as { password: string }).password, NEW_SECRET);
  assert.equal(bw.saved[0].type, 1);
  for (const call of bw.calls) assert.ok(!call.args.some(a => a.includes(NEW_SECRET) || a.includes(Buffer.from(NEW_SECRET).toString('base64'))));
  assert.equal(clipboard.value, '', 'cleared when it still holds the saved value');
  clipboard.value = '';
  assert.equal(JSON.parse((await save('vault_save', { name: 'Empty', source: 'clipboard' })).text).error, 'clipboard_empty');
});

test('save never overwrites an existing name without a next-turn confirmation; then only the password changes', async () => {
  const { vault, bw, clipboard } = await unlocked();
  clipboard.value = NEW_SECRET;
  const store = new ConfirmationStore();
  const first = JSON.parse((await vaultTools(vault, 10, store)('vault_save', { name: 'github', source: 'clipboard' })).text);
  assert.equal(first.exists, true); assert.equal(first.item, 'GitHub'); assert.ok(first.confirm_token);
  assert.equal(bw.saved.length, 0, 'nothing written on propose');
  const same = JSON.parse((await vaultTools(vault, 10, store)('vault_save', { name: 'github', source: 'clipboard', confirm_token: first.confirm_token })).text);
  assert.equal(same.message, 'confirmation_needs_reply');
  assert.equal(bw.saved.length, 0);
  const next = JSON.parse((await vaultTools(vault, 11, store)('vault_save', { name: 'github', source: 'clipboard', confirm_token: first.confirm_token })).text);
  assert.deepEqual(next, { saved: true, updated: true, item: 'GitHub', kind: 'login' });
  assert.equal(bw.saved[0].command, 'edit'); assert.equal(bw.saved[0].id, ID);
  assert.equal((bw.saved[0].login as { password: string; username: string }).password, NEW_SECRET);
  assert.equal((bw.saved[0].login as { username: string }).username, 'drascom', 'other fields kept');
  const reuse = JSON.parse((await vaultTools(vault, 11, store)('vault_save', { name: 'github', source: 'clipboard', confirm_token: first.confirm_token })).text);
  assert.equal(reuse.error, 'invalid_request', 'single use');
});

test('generate saves a strong password and copies it (30 s); prompt cancel saves nothing', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { vault, bw, clipboard } = await unlocked();
  const save = vaultTools(vault, 1, new ConfirmationStore());
  const result = await save('vault_save', { name: 'New site', source: 'generate', username: 'me', uri: 'https://new.example' });
  assert.ok(!result.text.includes('Gen3rated'));
  assert.equal(clipboard.value, 'Gen3rated-Strong-Passw0rd!xyz');
  assert.deepEqual(bw.calls.find(c => c.args[0] === 'generate')!.args, ['generate', '-ulns', '--length', '24']);
  t.mock.timers.tick(30_000); await new Promise(resolve => setImmediate(resolve));
  assert.equal(clipboard.value, '');
  t.mock.timers.reset();
  const cancelled = await unlocked({}, 30_000, async () => undefined);
  const r = JSON.parse((await vaultTools(cancelled.vault, 1, new ConfirmationStore())('vault_save', { name: 'Typed', source: 'prompt' })).text);
  assert.equal(r.error, 'cancelled');
  assert.equal(cancelled.bw.saved.length, 0);
  const typed = await unlocked({}, 30_000, async title => title === 'Typed' ? NEW_SECRET : undefined);
  const ok = await vaultTools(typed.vault, 1, new ConfirmationStore())('vault_save', { name: 'Typed', source: 'prompt' });
  assert.ok(!ok.text.includes(NEW_SECRET));
  assert.equal((typed.bw.saved[0].login as { password: string }).password, NEW_SECRET);
});

test('secure prompt bridge: app picks up, answers or cancels; no app = prompt_unavailable', async () => {
  const prompts = new SecretPrompts();
  const waiting = prompts.wait(AbortSignal.timeout(2000));
  const asked = prompts.ask('Site', AbortSignal.timeout(2000));
  const shown = await waiting;
  assert.equal(shown?.title, 'Site');
  assert.ok(prompts.answer(shown!.id, NEW_SECRET));
  assert.equal(await asked, NEW_SECRET);
  const second = prompts.ask('Other', AbortSignal.timeout(2000));
  const queued = await prompts.wait(AbortSignal.timeout(2000));
  prompts.answer(queued!.id, undefined);
  assert.equal(await second, undefined);
  await assert.rejects(prompts.ask('Nobody', AbortSignal.timeout(2000), 50), (e: VaultError) => e.code === 'prompt_unavailable');
});

test('end to end save: secret never in model-visible messages', async () => {
  const call = (name: string, args: Record<string, string>) => fauxAssistantMessage(fauxToolCall(name, args, { id: name }), { stopReason: 'toolUse' });
  const s = await session({ skill: 'vault', responses: [call('vault_save', { name: 'New API', source: 'clipboard', kind: 'token' }), fauxAssistantMessage('Kaydedildi.')] });
  try {
    s.clipboard.value = NEW_SECRET;
    await s.session.prompt('panodaki tokeni New API olarak kaydet');
    const text = JSON.stringify(s.session.messages);
    assert.match(text, /\\"saved\\":true/);
    assert.ok(!text.includes(NEW_SECRET));
  } finally { await s.cleanup(); }
});

test('spelling: Turkish letter names with case, digits and named symbols; TOTP in digit groups', () => {
  assert.equal(spellSecret('Ab-1_', 'password'), 'büyük a, küçük be, tire, bir. alt çizgi.');
  assert.equal(spellSecret('xI.!', 'password'), 'küçük iks, büyük i, nokta, ünlem.');
  assert.equal(spellSecret('654321', 'totp'), 'altı, beş, dört. üç, iki, bir.');
});

test('speak: the spelled value goes only to the speaker; never to tool results, history, ring reply, log or clipboard', async () => {
  const call = (name: string, args: Record<string, string | boolean>) => fauxAssistantMessage(fauxToolCall(name, args, { id: name }), { stopReason: 'toolUse' });
  const heard: string[] = [];
  const s = await session({ skill: 'vault', speak: async text => { heard.push(text); return 'spoken'; },
    responses: [call('vault_search', { query: 'github' }), call('vault_copy', { id: ID, field: 'password', speak: true }), fauxAssistantMessage('GitHub password: ***')] });
  const dir = await mkdtemp(path.join(tmpdir(), 'ayhan-vault-log-')), log = path.join(dir, 'activity.log');
  try {
    await observeRequest(log, { kind: 'chat' }, () => s.session.prompt('github şifremi sesli oku'));
    const spelled = spellSecret('hunter2-site-password', 'password');
    assert.deepEqual(heard, [spelled]);
    assert.equal(s.clipboard.value, 'before', 'not copied');
    const text = stable(JSON.stringify(s.session.messages) + await readFile(log, 'utf8'));
    for (const secret of [...SECRETS, spelled, 'küçük he']) assert.ok(!text.includes(secret), secret);
    assert.match(text, /\\"spoken\\":true,\\"field\\":\\"password\\",\\"item\\":\\"GitHub\\"/);
    assert.equal(s.session.messages.at(-1)?.role, 'assistant');
  } finally { await s.cleanup(); await rm(dir, { recursive: true, force: true }); }
});

test('speak: stopped, read-aloud off, TTS failing or missing: an error code only, no text fallback, no value', async () => {
  const { vault, clipboard } = await unlocked();
  const tools = new Map<string, { execute: (id: string, params: unknown, signal?: AbortSignal) => Promise<{ content: { text: string }[]; isError?: boolean }> }>();
  const run = async (speak: SecretSpeaker | undefined) => {
    tools.clear();
    bitwardenVault({ enabled: true, service: vault, speak })({ registerTool: (tool: { name: string }) => tools.set(tool.name, tool as never) } as unknown as ExtensionAPI);
    const result = await tools.get('vault_copy')!.execute('t', { id: ID, field: 'password', speak: true }, AbortSignal.timeout(2000));
    assert.ok(result.isError); for (const secret of SECRETS) assert.ok(!result.content[0].text.includes(secret));
    return JSON.parse(result.content[0].text).error as string;
  };
  assert.equal(await run(async () => 'stopped'), 'speech_stopped');
  assert.equal(await run(async () => { throw new VaultError('speech_off'); }), 'speech_off');
  assert.equal(await run(async () => { throw Error('piper failed on hunter2-site-password'); }), 'speech_unavailable');
  assert.equal(await run(undefined), 'speech_unavailable');
  assert.equal(clipboard.value, 'before');
});

test('Speech.speakSecret: current engine, slower, temp audio removed after playback, stop ends it', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'ayhan-secret-tts-')), temp = path.join(dir, 'tmp'), previous = process.env.TMPDIR;
  try {
    await (await import('node:fs/promises')).mkdir(temp);
    const piper = path.join(dir, 'piper'), model = path.join(dir, 'tr.onnx');
    // Fake piper: writes its stdin to the -f file and records its arguments.
    await writeFile(piper, '#!/bin/sh\nout=""; prev=""; for a in "$@"; do [ "$prev" = "-f" ] && out="$a"; prev="$a"; done\necho "$@" > "' + dir + '/args"\ncat > "$out"\n');
    await chmod(piper, 0o755); await writeFile(model, ''); await writeFile(model + '.json', '{}');
    process.env.TMPDIR = temp;
    const speech = new Speech({ ...defaults, tts_engine: 'piper', piper_bin: piper, piper_model_tr: model, piper_speed_tr: 1.25 });
    const played: string[] = [];
    let hold: Promise<void> | undefined;
    speech.play = async (_bin, args, signal) => { played.push(await readFile(args[0], 'utf8')); await hold; signal.throwIfAborted(); };
    assert.equal(await speech.speakSecret('küçük a, bir.', AbortSignal.timeout(5000)), 'spoken');
    assert.deepEqual(played, ['küçük a, bir.']);
    assert.match(await readFile(path.join(dir, 'args'), 'utf8'), /--length-scale 1\.14/);
    assert.deepEqual(await readdir(temp), [], 'temp audio removed');
    let release!: () => void; hold = new Promise(resolve => { release = resolve; });
    const pending = speech.speakSecret('küçük be.', AbortSignal.timeout(5000));
    while (played.length < 2) await new Promise(resolve => setTimeout(resolve, 10));
    speech.stop(); release();
    assert.equal(await pending, 'stopped');
    assert.deepEqual(await readdir(temp), []);
  } finally { if (previous === undefined) delete process.env.TMPDIR; else process.env.TMPDIR = previous; await rm(dir, { recursive: true, force: true }); }
});

test('server: the registered speaker refuses while the ring does not read aloud; otherwise uses the app speech', async () => {
  const spoken: string[] = [];
  const speech: SpeechService = { transcribe: async () => '', speak: async () => {}, stop: () => {}, close: async () => {},
    speakSecret: async text => { spoken.push(text); return 'spoken'; } };
  const runtime = { ensure: async () => {}, close: async () => {} };
  const local = { complete: async () => {
    const result = await registeredSecretSpeaker()!('küçük a.', AbortSignal.timeout(2000)).then(r => r, (e: VaultError) => e.code);
    return { ...fauxAssistantMessage('result ' + result), provider: 'local-gemma' };
  } };
  const app = new Orchestrator({ ...defaults }, local, local, runtime, speech);
  app.loadInstructions = async () => '';
  const lifecycle = new AbortController(), server = await makeServer({ ...defaults }, app, lifecycle.signal, null);
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  try {
    const address = server.address() as { port: number }, base = `http://127.0.0.1:${address.port}`;
    const { token } = await (await fetch(base + '/session')).json() as { token: string };
    const chat = async (body: unknown) => (await (await fetch(base + '/chat', { method: 'POST', headers: { 'X-Assistant-Token': token }, body: JSON.stringify(body) })).json() as { text: string }).text;
    assert.equal(await chat({ text: 'oku', offline: true, read_aloud: false }), 'result speech_off');
    assert.deepEqual(spoken, []);
    assert.equal(await chat({ text: 'oku', offline: true, read_aloud: true }), 'result spoken');
    assert.deepEqual(spoken, ['küçük a.']);
  } finally { lifecycle.abort(); await new Promise<void>(resolve => server.close(() => resolve())); }
  assert.equal(registeredSecretSpeaker(), undefined, 'unregistered on shutdown');
});
