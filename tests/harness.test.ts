import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, rm, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { ModelRuntime, type ExtensionFactory } from '@earendil-works/pi-coding-agent';
import { fauxProvider, fauxAssistantMessage, fauxToolCall, type AssistantMessage } from '@earendil-works/pi-ai';
import { Type } from 'typebox';
import { defaults } from '../src/config.ts';
import { createHarnessSession, promptSteered } from '../src/harness.ts';
import { Steering } from '../src/providers.ts';
import skillManager from '../.pi/extensions/skill-manager.ts';
import { publicUrl, searchResults, newsResults, publicHtml } from '../.pi/extensions/online-search.ts';

const tool = (name: string, args: Record<string, string>, id = name) => fauxAssistantMessage(fauxToolCall(name, args, { id }), { stopReason: 'toolUse' });
async function fixture(responses: AssistantMessage[], offline = false, allowed = true, extension?: ExtensionFactory,
  workflow: { briefNews?: boolean; skill?: 'online-search' | 'skill-manager' } = {}) {
  const root = await mkdtemp(path.join(tmpdir(), 'ayhan-harness-test-'));
  await mkdir(path.join(root, '.pi/skills/example'), { recursive: true });
  await mkdir(path.join(root, '.pi/extensions'), { recursive: true });
  await writeFile(path.join(root, '.pi/skills/example/SKILL.md'), '---\nname: example\ndescription: A test workflow.\n---\nTest skill.\n');
  await writeFile(path.join(root, '.pi/extensions/example.ts'), '// extension reference\n');
  await writeFile(path.join(root, 'secret.txt'), 'private data');
  await writeFile(path.join(root, '.pi/permissions.json'), JSON.stringify({ web_search: allowed ? 'allow' : 'deny', web_read: 'allow', skill_edit: 'allow' }));
  const fake = fauxProvider({ provider: 'harness-test', models: [{ id: 'test' }], tokensPerSecond: 1000000 });
  fake.setResponses(responses);
  const runtime = await ModelRuntime.create({ modelsPath: null, refreshOnCreate: false }); runtime.registerNativeProvider(fake.provider);
  let executed = 0;
  const search: ExtensionFactory = pi => {
    pi.registerTool({ name: 'web_search', label: 'Search', description: 'Test search', parameters: Type.Object({ query: Type.String() }),
      execute: async () => { executed++; return { content: [{ type: 'text', text: 'Verified source: https://example.com' }], details: {} }; } });
  };
  const session = await createHarnessSession({ root, config: { ...defaults, pi_agent_dir: path.join(root, 'agent') },
    context: { systemPrompt: 'Assistant test rules', messages: [{ role: 'user', content: 'previous', timestamp: 1 }, fauxAssistantMessage('previous answer'), { role: 'user', content: 'test', timestamp: 2 }] },
    model: fake.getModel(), runtime, offline, ...workflow, extensionPaths: [], extensionFactories: [extension || search] });
  return { root, session, fake, executed: () => executed, cleanup: async () => { await session.abort(); session.dispose(); await rm(root, { recursive: true, force: true }); } };
}

test('harness executes approved tool and preserves imported history and skill discovery', async () => {
  const f = await fixture([tool('web_search', { query: 'test' }), fauxAssistantMessage('Answer with https://example.com')]);
  try {
    assert.match(f.session.systemPrompt, /example/);
    assert.equal(f.session.messages.length, 2);
    assert.ok(!f.session.getActiveToolNames().includes('bash'));
    await f.session.prompt('test');
    assert.equal(f.executed(), 1);
    assert.match(f.session.getLastAssistantText()!, /https:\/\/example.com/);
    assert.ok(f.session.messages.some(message => message.role === 'toolResult' && !message.isError));
  } finally { await f.cleanup(); }
});
test('brief news workflow sets search recency inside Pi and prevents repeat searches', async () => {
  let category: string | undefined, calls = 0;
  const f = await fixture([tool('web_search', { query: 'London', category: 'web' }, 'one'),
    tool('web_search', { query: 'London again' }, 'two'), fauxAssistantMessage('short roundup')], false, true, pi => {
    pi.registerTool({ name: 'web_search', label: 'Search', description: 'Test', parameters: Type.Object({ query: Type.String(), category: Type.Optional(Type.String()), recencyFilter: Type.Optional(Type.String()) }),
      execute: async (_id, args) => { calls++; category = args.recencyFilter; return { content: [{ type: 'text', text: 'Headline' }], details: {} }; } });
  }, { briefNews: true, skill: 'online-search' });
  try {
    f.fake.setResponses([tool('web_search', { query: 'London', category: 'web' }, 'one'),
      tool('web_search', { query: 'London again' }, 'two'), context => {
        assert.match(JSON.stringify(context), /ALREADY supplied/);
        return fauxAssistantMessage('short roundup');
      }]);
    await f.session.prompt('test');
    assert.equal(category, 'day'); assert.equal(calls, 1);
    assert.ok(f.session.messages.some(message => message.role === 'toolResult' && message.isError));
  } finally { await f.cleanup(); }
});
for (const [name, offline, allowed] of [['offline', true, true], ['disabled permission', false, false]] as const) {
  test(`harness blocks search with ${name} before tool execution`, async () => {
    const f = await fixture([tool('web_search', { query: 'test' }), fauxAssistantMessage('Search unavailable')], offline, allowed);
    try { await f.session.prompt('test'); assert.equal(f.executed(), 0); assert.ok(f.session.messages.some(message => message.role === 'toolResult' && message.isError)); }
    finally { await f.cleanup(); }
  });
}

test('harness can read skills and extensions but blocks unrelated files and symlink escapes', async () => {
  const f = await fixture([
    tool('read', { path: '.pi/skills/example/SKILL.md' }, 'skill'),
    tool('read', { path: '.pi/extensions/example.ts' }, 'extension'),
    tool('read', { path: 'secret.txt' }, 'secret'),
    tool('read', { path: '.pi/skills/leak.md' }, 'link'), fauxAssistantMessage('done')
  ]);
  try {
    await symlink(path.join(f.root, 'secret.txt'), path.join(f.root, '.pi/skills/leak.md'));
    await f.session.prompt('test');
    const results = f.session.messages.filter(message => message.role === 'toolResult');
    assert.deepEqual(results.map(result => result.isError), [false, false, true, true]);
    assert.ok(!JSON.stringify(results).includes('private data'));
  } finally { await f.cleanup(); }
});

test('harness creates and edits skills; blocks permission and extension writes', async () => {
  const f = await fixture([
    tool('write', { path: '.pi/skills/new-skill/SKILL.md', content: 'initial rules' }, 'create'),
    tool('edit', { path: '.pi/skills/new-skill/SKILL.md', oldText: 'initial', newText: 'improved' }, 'edit'),
    tool('write', { path: '.pi/permissions.json', content: '{}' }, 'permission'),
    tool('write', { path: '.pi/extensions/example.ts', content: 'changed' }, 'extension'),
    tool('write', { path: '.pi/skills/escape/new.txt', content: 'changed' }, 'symlink'), fauxAssistantMessage('done')
  ], false, true, undefined, { skill: 'skill-manager' });
  try {
    await symlink(f.root, path.join(f.root, '.pi/skills/escape'));
    await f.session.prompt('test');
    assert.equal(await readFile(path.join(f.root, '.pi/skills/new-skill/SKILL.md'), 'utf8'), 'improved rules');
    assert.equal(await readFile(path.join(f.root, '.pi/extensions/example.ts'), 'utf8'), '// extension reference\n');
    assert.deepEqual(f.session.messages.filter(message => message.role === 'toolResult').map(result => result.isError), [false, false, true, true, true]);
  } finally { await f.cleanup(); }
});

test('test_skill uses the harness native parser and reports valid structure', async () => {
  const f = await fixture([tool('test_skill', { name: 'example' }), fauxAssistantMessage('validated')], false, true, skillManager, { skill: 'skill-manager' });
  try { await f.session.prompt('test'); assert.ok(f.session.messages.some(message => message.role === 'toolResult' && JSON.stringify(message.content).includes('Static validation only'))); }
  finally { await f.cleanup(); }
});

test('failed ability returns an error result through the harness', async () => {
  const f = await fixture([tool('web_search', { query: 'test' }), fauxAssistantMessage('The search provider failed')], false, true, pi => {
    pi.registerTool({ name: 'web_search', label: 'Search', description: 'Test', parameters: Type.Object({ query: Type.String() }), execute: async () => { throw Error('Provider unavailable'); } });
  });
  try { await f.session.prompt('test'); assert.ok(f.session.messages.some(message => message.role === 'toolResult' && message.isError && JSON.stringify(message.content).includes('Provider unavailable'))); }
  finally { await f.cleanup(); }
});

test('nested tool calls pass through the same harness permission hook', async () => {
  let nestedExecuted = false;
  const f = await fixture([tool('web_search', { query: 'test' }), fauxAssistantMessage('nested blocked')], false, true, pi => {
    pi.registerTool({ name: 'web_read', label: 'Read page', description: 'Test', parameters: Type.Object({ url: Type.String() }),
      execute: async () => { nestedExecuted = true; return { content: [{ type: 'text', text: 'unexpected' }], details: {} }; } });
    pi.registerTool({ name: 'web_search', label: 'Search', description: 'Test', parameters: Type.Object({ query: Type.String() }),
      execute: async (_id, _args, signal, _update, ctx) => {
        const result = await ctx.executeTool('web_read', { url: 'https://example.com' }, { signal });
        return { content: result.result.content, details: { nestedError: result.isError } };
      } });
  });
  try {
    await writeFile(path.join(f.root, '.pi/permissions.json'), JSON.stringify({ web_search: 'allow', web_read: 'deny' }));
    await f.session.prompt('test');
    assert.equal(nestedExecuted, false);
    assert.ok(f.session.messages.some(message => message.role === 'toolResult' && JSON.stringify(message.content).includes('disabled')));
  } finally { await f.cleanup(); }
});

test('harness abort propagates cancellation to an executing ability', async () => {
  let started!: () => void; const ready = new Promise<void>(resolve => started = resolve);
  let cancelled = false;
  const f = await fixture([tool('web_search', { query: 'test' })], false, true, pi => {
    pi.registerTool({ name: 'web_search', label: 'Search', description: 'Test', parameters: Type.Object({ query: Type.String() }),
      execute: async (_id, _args, signal) => {
        started(); await new Promise<void>((_, reject) => signal!.addEventListener('abort', () => { cancelled = true; reject(Error('cancelled')); }, { once: true }));
        throw Error('unreachable');
      } });
  });
  try { const work = f.session.prompt('test'); await ready; await f.session.abort(); await work; assert.equal(cancelled, true); }
  finally { await f.cleanup(); }
});

test('search parser extracts source URLs and rejects bot challenges', () => {
  const html = '<div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Farticle">Title</a><a class="result__snippet">Evidence</a></div>';
  assert.deepEqual(searchResults(html, 5), [{ title: 'Title', url: 'https://example.com/article', snippet: 'Evidence' }]);
  assert.throws(() => searchResults('<div class="anomaly-modal"></div>', 5), /challenge/);
  assert.throws(() => searchResults('<html>unexpected</html>', 5), /unsupported/);
});

test('news parser returns original source URLs, publication dates and source names', () => {
  const xml = '<rss><channel><item><title>London news</title><link>http://www.bing.com/news/apiclick?url=https%3A%2F%2Fexample.com%2Flondon</link><description>News &amp; details</description><pubDate>Fri, 02 Oct 2026 09:00:00 GMT</pubDate><News:Source>Source</News:Source></item></channel></rss>';
  const now = Date.parse('2026-10-02T12:00:00Z');
  assert.deepEqual(newsResults(xml, 5, now), [{ title: 'London news', url: 'https://example.com/london', snippet: 'News & details', published_at: 'Fri, 02 Oct 2026 09:00:00 GMT', source: 'Source' }]);
  assert.deepEqual(newsResults(xml.replace('02 Oct 2026', '02 Jun 2026'), 5, now), []);
});

test('web requests block local/private URLs and respect cancellation', async () => {
  for (const url of ['http://example.com', 'https://127.0.0.1', 'https://10.0.0.1', 'https://169.254.169.254', 'https://localhost', 'https://app.local', 'https://[::1]', 'https://[::ffff:127.0.0.1]', 'https://user:pass@example.com', 'https://example.com:8091']) assert.throws(() => publicUrl(url));
  const abort = new AbortController(); abort.abort();
  await assert.rejects(publicHtml('https://example.com', abort.signal));
});

for (const name of ['source_check', 'fetch_content', 'get_search_content'] as const) {
  for (const mode of ['allowed', 'denied', 'offline'] as const) {
    test(`web-access ${name} respects ${mode} through Pi before execution`, async () => {
      let executions = 0;
      const extension: ExtensionFactory = pi => pi.registerTool({ name, label: name, description: 'Permission test',
        parameters: Type.Object({}), execute: async () => { executions++; return { content: [{ type: 'text', text: 'done' }], details: {} }; } });
      const f = await fixture([tool(name, {}), fauxAssistantMessage('done')], mode === 'offline', true, extension, { skill: 'online-search' });
      try {
        await writeFile(path.join(f.root, '.pi/permissions.json'), JSON.stringify({ [name]: mode === 'denied' ? 'deny' : 'allow' }));
        await f.session.prompt('test');
        assert.equal(executions, mode === 'allowed' ? 1 : 0);
        assert.equal(f.session.messages.some(message => message.role === 'toolResult' && message.isError), mode !== 'allowed');
      } finally { await f.cleanup(); }
    });
  }
}

test('steer: text added while a request runs reaches the same session before the next model call; one reply', async () => {
  let started!: () => void, release!: () => void, calls = 0;
  const ready = new Promise<void>(resolve => started = resolve), gate = new Promise<void>(resolve => release = resolve);
  const f = await fixture([], false, true, pi => {
    pi.registerTool({ name: 'web_search', label: 'Search', description: 'Test', parameters: Type.Object({ query: Type.String() }),
      execute: async () => { started(); await gate; return { content: [{ type: 'text', text: 'result' }], details: {} }; } });
  });
  try {
    f.fake.setResponses([() => { calls++; return tool('web_search', { query: 'weather' }); }, context => {
      calls++; assert.match(JSON.stringify(context.messages.at(-1)), /also tomorrow/);
      return fauxAssistantMessage('today and tomorrow');
    }]);
    const steering = new Steering(), signal = new AbortController().signal;
    const work = promptSteered(f.session, 'weather today', { expandPromptTemplates: false }, signal, steering);
    await ready;
    assert.equal(steering.steer('also tomorrow'), true);
    release(); await work;
    assert.equal(calls, 2); assert.deepEqual(steering.accepted, ['also tomorrow']);
    const turn = f.session.messages.slice(f.session.messages.findIndex(m => m.role === 'user' && JSON.stringify(m.content).includes('weather today')));
    const replies = turn.filter(m => m.role === 'assistant' && m.content.some(b => b.type === 'text' && b.text.trim()));
    assert.equal(replies.length, 1);
    // Finished: no live session, so a later text is not steered (the server starts a new request instead).
    assert.equal(steering.steer('late'), false);
  } finally { release(); await f.cleanup(); }
});

test('every app extension module loads (no broken imports, e.g. after a removed file)', async () => {
  const { readdir } = await import('node:fs/promises');
  const { pathToFileURL } = await import('node:url');
  const directory = path.join(import.meta.dirname, '../.pi/extensions');
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const file = entry.isDirectory() ? path.join(directory, entry.name, 'index.ts') : /\.(ts|js)$/.test(entry.name) ? path.join(directory, entry.name) : '';
    if (!file) continue;
    const module = await import(pathToFileURL(file).href);
    assert.equal(typeof module.default, 'function', entry.name);
  }
});
