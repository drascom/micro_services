import { ModelRuntime } from '@earendil-works/pi-coding-agent';
import { fauxProvider, fauxAssistantMessage, fauxToolCall } from '@earendil-works/pi-ai';
import { writeFile, mkdir } from 'node:fs/promises';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { createHarnessSession } from '../src/harness.ts';
import { root, loadConfig } from '../src/config.ts';

// Explicit live smoke test: real extension tools run through Pi's permission hook,
// with a deterministic model deciding the sequence (no paid model calls).
const report: any[] = [];
await mkdir(path.join(root, 'data/web-access'), { recursive: true });
const video = path.join(root, 'data/web-access/test-video.mp4');
spawnSync('ffmpeg', ['-y', '-f', 'lavfi', '-i', 'color=c=blue:s=160x120:d=2', '-pix_fmt', 'yuv420p', video], { stdio: 'ignore' });
const fake = fauxProvider({ provider: 'web-smoke', models: [{ id: 'test' }], tokensPerSecond: 1000000 });
let step = 0, responseId = '';
const calls: [string, any][] = process.argv.includes('--media-recheck') ? [
  ['fetch_content', { url: 'https://www.youtube.com/watch?v=jNQXAC9IVRw', timestamp: '0:01', frames: 2 }],
  ['fetch_content', { url: 'https://example.com' }]
] : process.argv.includes('--extended') ? [
  ['web_search', { query: 'TypeScript documentation', numResults: 2, workflow: 'auto-summary' }],
  ['web_search', { query: 'TypeScript documentation', numResults: 2, workflow: 'summary-review' }],
  ['fetch_content', { url: 'https://www.youtube.com/watch?v=jNQXAC9IVRw', timestamp: '0:01', frames: 2 }],
  ['fetch_content', { url: 'https://www.python.org/static/community_logos/python-logo.png' }]
] : [
  ['web_search', { query: 'London weather today', numResults: 3, workflow: 'none' }],
  ['get_search_content', { queryIndex: 0 }],
  ['fetch_content', { urls: ['https://example.com', 'https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf', 'https://github.com/octocat/Hello-World', 'https://www.youtube.com/watch?v=jNQXAC9IVRw', 'https://www.python.org/static/community_logos/python-logo.png'] }],
  ['get_search_content', { urlIndex: 1 }],
  ['fetch_content', { url: 'https://example.com', mode: 'raw' }],
  ['source_check', { claim: 'TypeScript is developed by Microsoft', numResults: 2, fetchContent: true }],
  ['fetch_content', { url: 'https://example.com', auth: true }],
  ['fetch_content', { url: video, frames: 2 }],
  ['fetch_content', { url: 'https://www.typescriptlang.org/docs/', mode: 'answer', answerModel: 'openai/gpt-5.5', prompt: 'What is this page for?' }],
  ['web_search', { query: 'London news today', numResults: 2, provider: 'openai', workflow: 'none' }]
];
const next = (context: any): any => {
  const last = context.messages.at(-1);
  if (last?.role === 'toolResult') {
    const text = last.content.filter((x: any) => x.type === 'text').map((x: any) => x.text).join('\n');
    responseId = /\[([a-f0-9]{8})\]|responseId["\s:]+([\w-]+)/i.exec(text)?.slice(1).find(Boolean) || responseId;
    report.push({ tool: last.toolName, args: calls[step-1][1], isError: last.isError, excerpt: text.slice(0, 12000), images: last.content.filter((x: any) => x.type === 'image').length });
    console.log(last.toolName, last.isError ? 'error' : 'completed');
  }
  if (step >= calls.length) return fauxAssistantMessage('Smoke checks complete.');
  const [name, args] = calls[step++];
  if (name === 'get_search_content') Object.assign(args, { responseId, limit: 1000 });
  return fauxAssistantMessage(fauxToolCall(name, args, { id: `smoke-${step}` }), { stopReason: 'toolUse' });
};
fake.setResponses(Array.from({ length: calls.length + 1 }, () => next));
const runtime = await ModelRuntime.create({ modelsPath: null, refreshOnCreate: false, allowModelNetwork: false });
runtime.registerNativeProvider(fake.provider);
const session = await createHarnessSession({ root, config: await loadConfig(),
 context: { systemPrompt: 'Follow the deterministic smoke test.', messages: [{ role: 'user', content: 'Test tools', timestamp: Date.now() }] },
 model: fake.getModel(), runtime, offline: false,
 extensionPaths: [path.join(root, '.pi/extensions/web-access.ts')] });
try {
 console.log('Active tools:', session.getActiveToolNames().join(', '));
 await session.prompt('Test');
} finally {
 await session.abort(); session.dispose();
 await mkdir(path.join(root, 'data/web-access'), { recursive: true });
 await writeFile(path.join(root, 'data/web-access', process.argv.includes('--media-recheck') ? 'media-recheck-report.json' : process.argv.includes('--extended') ? 'extended-report.json' : 'smoke-report.json'), JSON.stringify({ time: new Date().toISOString(), report }, null, 2));
}
