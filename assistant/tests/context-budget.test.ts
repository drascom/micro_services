import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { ModelRuntime } from '@earendil-works/pi-coding-agent';
import { fauxProvider, fauxAssistantMessage } from '@earendil-works/pi-ai';
import { defaults, root } from '../src/config.ts';
import { createHarnessSession, harnessTools } from '../src/harness.ts';
import { compactSchema } from '../.pi/extensions/web-access.ts';
import { GmailService } from '../.pi/extensions/gmail/service.ts';
import { MemorySecretStore } from '../.pi/extensions/gmail/secrets.ts';
import { FileService } from '../src/files.ts';

// Context budget rule (local and remote): system prompt + skill catalog/body + tool schemas stay small.
// Limits are characters (about 4 per token), set about 10% above the measured values (offline 3907, mail-assistant 3359; 2026-10-09).
// 2026-10-10: optional `account` filter for multiple Mail accounts measured mail-assistant 3531, offline 4282.
// 2026-10-10: Reminders (off by default) measured unrouted offline with it on 5374, reminders route 2018.
// 2026-10-10: "never pretend it was done" rule (+56) measured unrouted offline 4330.
// 2026-10-11: Gmail triage (off by default) measured mail-triage route 1709; unrouted online adds 831 when on.
// 2026-10-12: local files (off by default) measured file-assistant route 2119; unrouted offline adds 1165 when on.
const LIMITS = { unrouted_offline: 4360, 'mail-assistant': 3600, unrouted_offline_reminders: 5900, reminders: 2250,
  'mail-triage': 1880, unrouted_gmail_added: 920, 'file-assistant': 2330, unrouted_files_added: 1290 } as const;

/** A connected Gmail (fake secret store) for the mail_organize cases; no network call happens while measuring. */
function connectedGmail() {
  const store = new MemorySecretStore();
  store.values.set('token', JSON.stringify({ refresh_token: 'x', email: 'me@gmail.com' }));
  return new GmailService({ dataDir: tmpdir(), exampleFile: '', store, http: async () => { throw Error('no network in tests'); } });
}

async function measure(skill: 'mail-assistant' | 'reminders' | 'mail-triage' | 'file-assistant' | undefined, offline: boolean, reminders = false, organize = false, files = false) {
  const agent = await mkdtemp(path.join(tmpdir(), 'ayhan-budget-'));
  const fake = fauxProvider({ provider: 'budget-test', models: [{ id: 'test' }], tokensPerSecond: 1000000 });
  fake.setResponses([fauxAssistantMessage('ok')]);
  const runtime = await ModelRuntime.create({ modelsPath: null, refreshOnCreate: false }); runtime.registerNativeProvider(fake.provider);
  const config = { ...defaults, pi_agent_dir: agent, reminders_enabled: reminders, gmail_triage_enabled: organize, embedding_enabled: files };
  const session = await createHarnessSession({ root, config, ...(files ? { files: new FileService(config, { dataDir: null }) } : {}),
    context: { systemPrompt: defaults.system_prompt, messages: [{ role: 'user', content: 'test', timestamp: 1 }] },
    model: fake.getModel(), runtime, offline, skill, extensionPaths: [], ...(organize ? { gmail: connectedGmail() } : {}) });
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
  assert.deepEqual(harnessTools(undefined, true), ['read', 'mail_list_messages', 'mail_read_message']);
  assert.deepEqual(harnessTools(undefined, false), ['read', 'web_search', 'web_read', 'mail_list_messages', 'mail_read_message']);
  assert.deepEqual(harnessTools('mail-assistant', true), ['mail_list_messages', 'mail_read_message']);
  assert.deepEqual(harnessTools('skill-manager', false), ['read', 'write', 'edit', 'test_skill']);
  assert.ok(!harnessTools('online-search', false).includes('write'));
  assert.deepEqual(harnessTools('online-search', true), []);
});

test('offline unrouted request stays within the context budget', async () => {
  const result = await measure(undefined, true);
  assert.deepEqual(result.names.sort(), ['mail_list_messages', 'mail_read_message', 'read']);
  assert.ok(result.chars <= LIMITS.unrouted_offline, `${result.chars} chars`);
});

test('mail-assistant request stays within the context budget', async () => {
  const result = await measure('mail-assistant', false);
  assert.ok(result.chars <= LIMITS['mail-assistant'], `${result.chars} chars`);
});

test('Reminders adds one compact tool only when enabled', async () => {
  const unrouted = await measure(undefined, true, true);
  assert.deepEqual(unrouted.names.sort(), ['mail_list_messages', 'mail_read_message', 'read', 'reminders']);
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
