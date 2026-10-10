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

// Context budget rule (local and remote): system prompt + skill catalog/body + tool schemas stay small.
// Limits are characters (about 4 per token), set about 10% above the measured values (offline 3907, mail-assistant 3359; 2026-10-09).
const LIMITS = { unrouted_offline: 4300, 'mail-assistant': 3500 } as const;

async function measure(skill: 'mail-assistant' | undefined, offline: boolean) {
  const agent = await mkdtemp(path.join(tmpdir(), 'ayhan-budget-'));
  const fake = fauxProvider({ provider: 'budget-test', models: [{ id: 'test' }], tokensPerSecond: 1000000 });
  fake.setResponses([fauxAssistantMessage('ok')]);
  const runtime = await ModelRuntime.create({ modelsPath: null, refreshOnCreate: false }); runtime.registerNativeProvider(fake.provider);
  const session = await createHarnessSession({ root, config: { ...defaults, pi_agent_dir: agent },
    context: { systemPrompt: defaults.system_prompt, messages: [{ role: 'user', content: 'test', timestamp: 1 }] },
    model: fake.getModel(), runtime, offline, skill, extensionPaths: [] });
  try {
    const tools = session.getActiveToolNames().map(name => {
      const tool = session.getToolDefinition(name)!;
      return JSON.stringify({ name, description: tool.description, parameters: tool.parameters });
    });
    // A preloaded skill body is sent with the user message.
    const body = skill ? (await import('node:fs/promises')).readFile(path.join(root, '.pi/skills', skill, 'SKILL.md'), 'utf8') : '';
    return { names: session.getActiveToolNames(), chars: session.systemPrompt.length + tools.join('').length + (await body).length };
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

test('upstream web tool schemas are compacted to first sentences', () => {
  const schema = compactSchema({ type: 'object', description: 'First sentence. Second sentence is long.',
    properties: { q: { type: 'string', description: 'x'.repeat(200) } } });
  assert.equal(schema.description, 'First sentence.');
  assert.equal(schema.properties.q.description.length, 80);
});
