import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { ModelRuntime } from '@earendil-works/pi-coding-agent';
import { fauxProvider, fauxAssistantMessage } from '@earendil-works/pi-ai';
import { defaults, root, type Config } from '../src/config.ts';
import { createHarnessSession, harnessTools } from '../src/harness.ts';
import { compactSchema } from '../.pi/extensions/web-access.ts';
import { GmailService } from '../.pi/extensions/gmail/service.ts';
import { MemorySecretStore } from '../.pi/extensions/gmail/secrets.ts';
import { FileService } from '../src/files.ts';
import { VaultService } from '../src/vault.ts';
import type { UpdateService } from '../src/updates.ts';
import { ConfirmationStore } from '../.pi/extensions/apple-reminders/confirmations.ts';
import { MemoryService } from '../src/memory.ts';
import { TaskService } from '../src/tasks.ts';
import { TaskStore } from '../src/task-store.ts';

// Context budget rule (local and remote): system prompt + skill catalog/body + tool schemas stay small.
// Limits are characters (about 4 per token), set about 10% above the measured values (offline 3907, mail-assistant 3359; 2026-10-09).
// 2026-10-10: optional `account` filter for multiple Mail accounts measured mail-assistant 3531, offline 4282.
// 2026-10-10: Reminders (off by default) measured unrouted offline with it on 5374, reminders route 2018.
// 2026-10-10: "never pretend it was done" rule (+56) measured unrouted offline 4330.
// 2026-10-11: Gmail triage (off by default) measured mail-triage route 1709; unrouted online adds 831 when on.
// 2026-10-12: local files (off by default) measured file-assistant route 2119; unrouted offline adds 1165 when on.
// 2026-10-14: Bitwarden vault (off by default, local model only) measured vault route 2594; unrouted local adds 1072 when unlocked.
// 2026-10-15: vault_copy `speak` + one skill line measured vault route 2777, unrouted local adds 1132 (limits unchanged).
// 2026-10-14: component updates (user requests only) measured updates route 1633; a pending offer adds 571 to an unrouted request.
// 2026-10-15: memory_save/memory_forget (only for a hatırla/unut request) measured 688 added.
// 2026-10-15: `account` hint "email or part; omit=all" (+40 per schema) on mail_list_messages/mail_organize measured
//   mail-assistant 3607 (limit +40 = 3640); mail-triage 1878, unrouted Gmail added 910 (limits unchanged).
// 2026-10-16: assistant tasks (own store, user requests only) measured tasks route 1763; a pending creation adds 1008 to an unrouted request.
const LIMITS = { unrouted_offline: 4360, 'mail-assistant': 3640, unrouted_offline_reminders: 5900, reminders: 2250,
  'mail-triage': 1880, unrouted_gmail_added: 920, 'file-assistant': 2330, unrouted_files_added: 1290,
  vault: 2850, unrouted_vault_added: 1180, updates: 1800, updates_pending_added: 630, memory_tools_added: 760, tasks: 1940, tasks_pending_added: 1110 } as const;

/** A connected Gmail (fake secret store) for the mail_organize cases; no network call happens while measuring. */
function connectedGmail() {
  const store = new MemorySecretStore();
  store.values.set('token', JSON.stringify({ refresh_token: 'x', email: 'me@gmail.com' }));
  return new GmailService({ dataDir: tmpdir(), exampleFile: '', store, http: async () => { throw Error('no network in tests'); } });
}

/** An unlocked vault (fake `bw`), for the local-model vault cases. */
async function unlockedVault(config: Config) {
  const vault = new VaultService({ ...config, bitwarden_enabled: true, bitwarden_bin: path.join(root, 'package.json') } as never, {
    transport: async args => ({ code: 0, stdout: args[0] === 'status' ? '{"status":"locked"}' : 'c2Vzc2lvbktleUZvclRlc3RzMTIzNDU2Nzg5MA==', stderr: '' }) });
  await vault.unlock('x');
  return vault;
}

async function measure(skill: 'mail-assistant' | 'reminders' | 'mail-triage' | 'file-assistant' | 'vault' | undefined, offline: boolean, reminders = false, organize = false, files = false,
  vault = false, remote = false) {
  const agent = await mkdtemp(path.join(tmpdir(), 'ayhan-budget-'));
  const fake = fauxProvider({ provider: (vault || files) && !remote ? 'local-gemma' : 'budget-test', models: [{ id: 'test' }], tokensPerSecond: 1000000 });
  fake.setResponses([fauxAssistantMessage('ok')]);
  const runtime = await ModelRuntime.create({ modelsPath: null, refreshOnCreate: false }); runtime.registerNativeProvider(fake.provider);
  const config = { ...defaults, pi_agent_dir: agent, reminders_enabled: reminders, gmail_triage_enabled: organize, embedding_enabled: files, bitwarden_enabled: vault };
  const session = await createHarnessSession({ root, config, ...(files ? { files: new FileService(config, { dataDir: null }) } : {}),
    context: { systemPrompt: defaults.system_prompt, messages: [{ role: 'user', content: 'test', timestamp: 1 }] },
    model: fake.getModel(), runtime, offline, skill, extensionPaths: [], ...(organize ? { gmail: connectedGmail() } : {}),
    ...(vault ? { vault: await unlockedVault(config) } : {}) });
  try {
    const tools = session.getActiveToolNames().map(name => {
      const tool = session.getToolDefinition(name)!;
      return JSON.stringify({ name, description: tool.description, parameters: tool.parameters });
    });
    // A preloaded skill body is sent with the user message.
    const body = skill ? (await import('node:fs/promises')).readFile(path.join(root, '.pi/skills', skill, 'SKILL.md'), 'utf8') : '';
    return { names: session.getActiveToolNames(), prompt: session.systemPrompt, chars: session.systemPrompt.length + tools.join('').length + (await body).length };
  } finally { session.dispose(); await rm(agent, { recursive: true, force: true }); }
}

test('only request-relevant tools are sent to the model', () => {
  assert.deepEqual(harnessTools(undefined, true), ['read']);
  assert.deepEqual(harnessTools(undefined, false), ['read', 'web_search', 'web_read', 'mail_list_messages', 'mail_read_message']);
  assert.deepEqual(harnessTools('mail-assistant', false), ['mail_list_messages', 'mail_read_message']);
  assert.deepEqual(harnessTools('mail-assistant', true), []);
  assert.deepEqual(harnessTools('skill-manager', false), ['read', 'write', 'edit', 'test_skill']);
  assert.ok(!harnessTools('online-search', false).includes('write'));
  assert.deepEqual(harnessTools('online-search', true), []);
});

test('offline unrouted request stays within the context budget', async () => {
  const result = await measure(undefined, true);
  assert.deepEqual(result.names.sort(), ['read']); // Gmail mail tools need the network.
  assert.ok(result.chars <= LIMITS.unrouted_offline, `${result.chars} chars`);
});

test('mail-assistant request stays within the context budget', async () => {
  const result = await measure('mail-assistant', false);
  assert.ok(result.chars <= LIMITS['mail-assistant'], `${result.chars} chars`);
});

test('Reminders adds one compact tool only when enabled', async () => {
  const unrouted = await measure(undefined, true, true);
  assert.deepEqual(unrouted.names.sort(), ['read', 'reminders']);
  assert.ok(unrouted.chars <= LIMITS.unrouted_offline_reminders, `${unrouted.chars} chars`);
  const routed = await measure('reminders', false, true);
  assert.deepEqual(routed.names, ['reminders']);
  assert.ok(routed.chars <= LIMITS.reminders, `${routed.chars} chars`);
});

test('Gmail triage adds one compact tool only when enabled and connected', async () => {
  const routed = await measure('mail-triage', false, false, true);
  assert.deepEqual(routed.names, ['mail_organize']);
  assert.ok(routed.chars <= LIMITS['mail-triage'], `${routed.chars} chars`);
  const plain = await measure(undefined, false), organize = await measure(undefined, false, false, true);
  assert.deepEqual(organize.names.filter(name => !plain.names.includes(name)), ['mail_organize']);
  assert.ok(organize.chars - plain.chars <= LIMITS.unrouted_gmail_added, `${organize.chars - plain.chars} chars added`);
  // mail-assistant route with Gmail on: the same one tool, no more.
  const mail = await measure('mail-assistant', false), mailOrganize = await measure('mail-assistant', false, false, true);
  assert.deepEqual(mailOrganize.names.filter(name => !mail.names.includes(name)), ['mail_organize']);
  assert.ok(mailOrganize.chars - mail.chars <= LIMITS.unrouted_gmail_added, `${mailOrganize.chars - mail.chars} chars added`);
});

test('local files add three compact tools only when enabled', async () => {
  const plain = await measure(undefined, true), files = await measure(undefined, true, false, false, true);
  assert.deepEqual(files.names.filter(name => !plain.names.includes(name)), ['file_search', 'file_read', 'file_organize']);
  assert.ok(files.chars - plain.chars <= LIMITS.unrouted_files_added, `${files.chars - plain.chars} chars added`);
  const routed = await measure('file-assistant', true, false, false, true);
  assert.deepEqual(routed.names, ['file_search', 'file_read', 'file_organize']);
  assert.ok(routed.chars <= LIMITS['file-assistant'], `${routed.chars} chars`);
  assert.ok(!plain.prompt.includes('file-assistant'), 'the skill is not advertised while off');
  // Remote model: no file tools and no skill, routed or not.
  for (const remote of [await measure(undefined, false, false, false, true, false, true), await measure('file-assistant', false, false, false, true, false, true)]) {
    assert.ok(!remote.names.some(name => name.startsWith('file_')), remote.names.join());
    assert.ok(!remote.prompt.includes('file-assistant'));
  }
});

test('vault adds three compact tools only for the local model when on and unlocked', async () => {
  const plain = await measure(undefined, true), vault = await measure(undefined, true, false, false, false, true);
  assert.deepEqual(vault.names.filter(name => !plain.names.includes(name)), ['vault_search', 'vault_copy', 'vault_save']);
  assert.ok(vault.chars - plain.chars <= LIMITS.unrouted_vault_added, `${vault.chars - plain.chars} chars added`);
  const routed = await measure('vault', true, false, false, false, true);
  assert.deepEqual(routed.names, ['vault_search', 'vault_copy', 'vault_save']);
assert.ok(routed.chars <= LIMITS.vault, `${routed.chars} chars`);
  assert.ok(!plain.prompt.includes('vault'), 'the skill is not advertised while off');
});

test('missing tools are never faked, routed or not', async () => {
  for (const result of [await measure(undefined, true), await measure('mail-assistant', false)])
    assert.match(result.prompt, /unavailable, say so; never pretend it was done, saved or remembered/);
});

test('upstream web tool schemas are compacted to first sentences', () => {
  const schema = compactSchema({ type: 'object', description: 'First sentence. Second sentence is long.',
    properties: { q: { type: 'string', description: 'x'.repeat(200) } } });
  assert.equal(schema.description, 'First sentence.');
  assert.equal(schema.properties.q.description.length, 80);
});

const fakeUpdates: UpdateService = { list: async () => { throw Error('unused'); }, describe: async () => ({ ids: [], items: [] }), startApply: () => [], startRollback: () => [] };
async function measureUpdates(skill: 'updates' | undefined, pending: boolean, automated = false) {
  const agent = await mkdtemp(path.join(tmpdir(), 'ayhan-budget-'));
  const fake = fauxProvider({ provider: 'budget-test', models: [{ id: 'test' }], tokensPerSecond: 1000000 });
  fake.setResponses([fauxAssistantMessage('ok')]);
  const runtime = await ModelRuntime.create({ modelsPath: null, refreshOnCreate: false }); runtime.registerNativeProvider(fake.provider);
  const store = new ConfirmationStore();
  if (pending) store.issue(1, { id: 'updates', title: 'apply llama.cpp b11617, Pi 1.2.0', ref: '{}' });
  const session = await createHarnessSession({ root, config: { ...defaults, pi_agent_dir: agent },
    context: { systemPrompt: defaults.system_prompt, messages: [{ role: 'user', content: 'test', timestamp: 1 }] },
    model: fake.getModel(), runtime, offline: false, skill, extensionPaths: [], turn: 2, automated, updates: fakeUpdates, updatesStore: store });
  try {
    const tools = session.getActiveToolNames().map(name => {
      const tool = session.getToolDefinition(name)!;
      return JSON.stringify({ name, description: tool.description, parameters: tool.parameters });
    });
    const body = skill ? await (await import('node:fs/promises')).readFile(path.join(root, '.pi/skills', skill, 'SKILL.md'), 'utf8') : '';
    return { names: session.getActiveToolNames(), prompt: session.systemPrompt, chars: session.systemPrompt.length + tools.join('').length + body.length };
  } finally { session.dispose(); await rm(agent, { recursive: true, force: true }); }
}

test('updates: one compact tool when routed or when the offer awaits an answer; never for tasks', async () => {
  const routed = await measureUpdates('updates', false);
  assert.deepEqual(routed.names, ['updates']);
  assert.ok(routed.chars <= LIMITS.updates, `${routed.chars} chars`);
  const plain = await measureUpdates(undefined, false), pending = await measureUpdates(undefined, true);
  assert.ok(!plain.names.includes('updates') && !plain.prompt.includes('updates'), 'unrouted: not sent without a pending offer');
  assert.deepEqual(pending.names.filter(name => !plain.names.includes(name)), ['updates']);
  assert.match(pending.prompt, /Pending updates apply llama\.cpp b11617, Pi 1\.2\.0: if this message agrees, call updates apply with confirm_token c_/);
  assert.ok(pending.chars - plain.chars <= LIMITS.updates_pending_added, `${pending.chars - plain.chars} chars added`);
  assert.ok(!(await measureUpdates(undefined, true, true)).names.includes('updates'), 'automated task');
});

async function measureMemory(memoryTools: boolean, automated = false) {
  const agent = await mkdtemp(path.join(tmpdir(), 'ayhan-budget-'));
  const fake = fauxProvider({ provider: 'budget-test', models: [{ id: 'test' }], tokensPerSecond: 1000000 });
  fake.setResponses([fauxAssistantMessage('ok')]);
  const runtime = await ModelRuntime.create({ modelsPath: null, refreshOnCreate: false }); runtime.registerNativeProvider(fake.provider);
  const config = { ...defaults, pi_agent_dir: agent };
  const session = await createHarnessSession({ root, config, context: { systemPrompt: defaults.system_prompt, messages: [{ role: 'user', content: 'test', timestamp: 1 }] },
    model: fake.getModel(), runtime, offline: false, extensionPaths: [], automated, updates: null, memory: new MemoryService(config, undefined, ':memory:'), memoryTools });
  try {
    const tools = session.getActiveToolNames().map(name => {
      const tool = session.getToolDefinition(name)!;
      return JSON.stringify({ name, description: tool.description, parameters: tool.parameters });
    });
    return { names: session.getActiveToolNames(), chars: session.systemPrompt.length + tools.join('').length };
  } finally { session.dispose(); await rm(agent, { recursive: true, force: true }); }
}

test('memory: two compact tools only for a hatırla/unut user request; never for tasks', async () => {
  const plain = await measureMemory(false), memory = await measureMemory(true);
  assert.deepEqual(memory.names.filter(name => !plain.names.includes(name)), ['memory_save', 'memory_forget']);
  assert.ok(memory.chars - plain.chars <= LIMITS.memory_tools_added, `${memory.chars - plain.chars} chars added`);
  assert.ok(!(await measureMemory(true, true)).names.some(name => name.startsWith('memory_')), 'automated task');
});

async function measureTasks(skill: 'tasks' | undefined, pending: boolean, automated = false) {
  const agent = await mkdtemp(path.join(tmpdir(), 'ayhan-budget-'));
  const fake = fauxProvider({ provider: 'budget-test', models: [{ id: 'test' }], tokensPerSecond: 1000000 });
  fake.setResponses([fauxAssistantMessage('ok')]);
  const runtime = await ModelRuntime.create({ modelsPath: null, refreshOnCreate: false }); runtime.registerNativeProvider(fake.provider);
  const store = new ConfirmationStore();
  if (pending) store.issue(1, { id: 'tasks', title: 'Mail özeti: Her gün 09:00, sonraki yarın 09:00', ref: '{}' });
  const session = await createHarnessSession({ root, config: { ...defaults, pi_agent_dir: agent, tasks_enabled: true },
    context: { systemPrompt: defaults.system_prompt, messages: [{ role: 'user', content: 'test', timestamp: 1 }] },
    model: fake.getModel(), runtime, offline: false, skill, extensionPaths: [], turn: 2, automated, updates: null,
    tasks: new TaskService(new TaskStore(':memory:')), tasksStore: store });
  try {
    const tools = session.getActiveToolNames().map(name => {
      const tool = session.getToolDefinition(name)!;
      return JSON.stringify({ name, description: tool.description, parameters: tool.parameters });
    });
    const body = skill ? await (await import('node:fs/promises')).readFile(path.join(root, '.pi/skills', skill, 'SKILL.md'), 'utf8') : '';
    return { names: session.getActiveToolNames(), prompt: session.systemPrompt, chars: session.systemPrompt.length + tools.join('').length + body.length };
  } finally { session.dispose(); await rm(agent, { recursive: true, force: true }); }
}

test('tasks: one compact tool on the tasks route or with a pending creation; never unrouted or in a task run', async () => {
  const routed = await measureTasks('tasks', false);
  assert.deepEqual(routed.names, ['tasks']);
  assert.ok(routed.chars <= LIMITS.tasks, `${routed.chars} chars`);
  const plain = await measureTasks(undefined, false), pending = await measureTasks(undefined, true);
  assert.ok(!plain.names.includes('tasks') && !plain.prompt.includes('<name>tasks</name>'), 'unrouted: not sent without a pending creation');
  assert.deepEqual(pending.names.filter(name => !plain.names.includes(name)), ['tasks']);
  assert.match(pending.prompt, /Pending task "Mail özeti: Her gün 09:00, sonraki yarın 09:00": if this message agrees, call tasks create with confirm_token c_/);
  assert.ok(pending.chars - plain.chars <= LIMITS.tasks_pending_added, `${pending.chars - plain.chars} chars added`);
  assert.ok(!(await measureTasks('tasks', true, true)).names.includes('tasks'), 'automated task');
});

test('pending updates are added beside the tasks route, never instead of it', async () => {
  const agent = await mkdtemp(path.join(tmpdir(), 'ayhan-budget-'));
  const fake = fauxProvider({ provider: 'budget-test', models: [{ id: 'test' }], tokensPerSecond: 1000000 });
  fake.setResponses([fauxAssistantMessage('ok')]);
  const runtime = await ModelRuntime.create({ modelsPath: null, refreshOnCreate: false }); runtime.registerNativeProvider(fake.provider);
  const store = new ConfirmationStore(); store.issue(1, { id: 'updates', title: 'apply Pi 1.3.0', ref: '{}' });
  const session = await createHarnessSession({ root, config: { ...defaults, pi_agent_dir: agent, reminders_enabled: true, tasks_enabled: true, tasks_list: 'Asistan' },
    context: { systemPrompt: defaults.system_prompt, messages: [{ role: 'user', content: 'test', timestamp: 1 }] },
    model: fake.getModel(), runtime, offline: false, skill: 'tasks', extensionPaths: [], turn: 2, updates: fakeUpdates, updatesStore: store,
    tasks: new TaskService(new TaskStore(':memory:')) });
  try {
    assert.deepEqual(session.getActiveToolNames(), ['tasks', 'updates']);
    assert.ok(!session.systemPrompt.includes('reminders in list'));
  } finally { session.dispose(); await rm(agent, { recursive: true, force: true }); }
});
