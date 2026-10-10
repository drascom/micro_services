import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { once } from 'node:events';
import { fauxAssistantMessage } from '@earendil-works/pi-ai';
import { defaults, type Config } from '../src/config.ts';
import { Orchestrator } from '../src/orchestrator.ts';
import { makeServer } from '../src/server.ts';
import { observeRequest } from '../src/activity.ts';

const skill = (name: string, description = 'Test skill.') => `---\nname: ${name}\ndescription: ${description}\n---\n# ${name}\n`;

async function setup() {
  const directory = await mkdtemp(path.join(tmpdir(), 'ayhan-admin-'));
  await mkdir(path.join(directory, '.pi/skills/demo'), { recursive: true });
  await mkdir(path.join(directory, '.pi/extensions'), { recursive: true });
  await writeFile(path.join(directory, '.pi/skills/demo/SKILL.md'), skill('demo'));
  await writeFile(path.join(directory, '.pi/extensions/tool.ts'), '/** Demo tool. */\nexport default function tool() {}\n');
  await writeFile(path.join(directory, 'secret.txt'), 'outside');
  await symlink(path.join(directory, 'secret.txt'), path.join(directory, '.pi/skills/demo/link.md'));
  const config: Config = { ...defaults, pi_agent_dir: directory };
  const reply = { complete: async () => ({ ...fauxAssistantMessage('ok'), provider: 'test' }) };
  const speech = { transcribe: async () => '', speak: async () => {}, stop: () => {}, close: async () => {} };
  const app = new Orchestrator(config, reply, reply, { ensure: async () => {}, close: async () => {} }, speech);
  const activityFile = path.join(directory, 'activity.log');
  const lifecycle = new AbortController(), server = await makeServer(config, app, lifecycle.signal, activityFile, directory);
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  const address = server.address(); assert.ok(address && typeof address === 'object');
  const base = `http://127.0.0.1:${address.port}`;
  const { token } = await (await fetch(base + '/session')).json() as { token: string };
  const call = async (route: string, body: object = {}, key = token) => {
    const response = await fetch(base + route, { method: 'POST', headers: { 'X-Assistant-Token': key }, body: JSON.stringify(body) });
    return { status: response.status, body: await response.json() as any };
  };
  const close = async () => { lifecycle.abort(); await new Promise<void>(resolve => server.close(() => resolve())); await rm(directory, { recursive: true, force: true }); };
  return { directory, call, close, activityFile };
}

test('status endpoint requires the session token and reports metadata without secrets', async () => {
  const f = await setup();
  try {
    await writeFile(path.join(f.directory, 'auth.json'), JSON.stringify({ openai: { type: 'oauth', access: 'SECRET-ACCESS', refresh: 'SECRET-REFRESH', expires: 1 } }));
    await observeRequest(f.activityFile, { kind: 'chat' }, async () => {});
    assert.equal((await f.call('/status', {}, 'bad')).status, 403);
    const { status, body } = await f.call('/status');
    assert.equal(status, 200);
    assert.equal(body.busy, false); assert.equal(body.backend.pid, process.pid);
    assert.deepEqual(body.pi_login, [{ provider: 'openai', type: 'oauth' }]);
    assert.equal(body.activity[0].event, 'request_finished');
    assert.ok(!JSON.stringify(body).includes('SECRET'));
  } finally { await f.close(); }
});

test('admin lists, reads, validates and writes app-owned skills and extensions', async () => {
  const f = await setup();
  try {
    const list = await f.call('/admin/resources');
    assert.equal(list.status, 200);
    assert.equal(list.body.skills[0].name, 'demo'); assert.equal(list.body.skills[0].description, 'Test skill.');
    assert.ok(!list.body.skills[0].files.includes('demo/link.md'));
    assert.equal(list.body.extensions[0].description, 'Demo tool.');
    assert.equal((await f.call('/admin/read', { kind: 'skill', path: 'demo/SKILL.md' })).body.content, skill('demo'));
    // Invalid skill content is rejected and the file is left unchanged.
    const invalid = await f.call('/admin/write', { kind: 'skill', path: 'demo/SKILL.md', content: skill('other') });
    assert.equal(invalid.status, 400); assert.match(invalid.body.error, /validation failed/);
    assert.equal(await readFile(path.join(f.directory, '.pi/skills/demo/SKILL.md'), 'utf8'), skill('demo'));
    assert.equal((await f.call('/admin/write', { kind: 'skill', path: 'demo/SKILL.md', content: skill('demo', 'Changed.') })).status, 200);
    assert.match(await readFile(path.join(f.directory, '.pi/skills/demo/SKILL.md'), 'utf8'), /Changed/);
    assert.equal((await f.call('/admin/write', { kind: 'extension', path: 'tool.ts', content: 'export default function tool() {}\n' })).status, 200);
    assert.equal((await f.call('/admin/create-skill', { name: 'new-skill', description: 'Does things.' })).status, 200);
    assert.equal((await f.call('/admin/create-skill', { name: 'new-skill', description: 'Again.' })).body.error, 'Already exists');
    assert.equal((await f.call('/admin/create-extension', { name: 'new-ext' })).status, 200);
    const after = await f.call('/admin/resources');
    assert.ok(after.body.skills.some((s: { name: string; valid: boolean }) => s.name === 'new-skill' && s.valid));
    assert.ok(after.body.extensions.some((e: { name: string }) => e.name === 'new-ext.ts'));
  } finally { await f.close(); }
});

test('admin rejects path traversal, symlinks, unknown kinds and missing files', async () => {
  const f = await setup();
  try {
    for (const [kind, file] of [['skill', '../extensions/tool.ts'], ['skill', '../../secret.txt'], ['skill', '/etc/passwd'],
      ['skill', 'demo/link.md'], ['skill', 'demo/./SKILL.md'], ['extension', '../skills/demo/SKILL.md'], ['permissions', 'x'], ['skill', 'demo/missing.md']]) {
      assert.equal((await f.call('/admin/read', { kind, path: file })).status, 400, `${kind}:${file}`);
      assert.equal((await f.call('/admin/write', { kind, path: file, content: 'x' })).status, 400, `${kind}:${file}`);
    }
    assert.equal(await readFile(path.join(f.directory, 'secret.txt'), 'utf8'), 'outside');
    assert.equal((await f.call('/admin/create-skill', { name: '../evil', description: 'x' })).status, 400);
    assert.equal((await f.call('/admin/create-extension', { name: '../../evil' })).status, 400);
  } finally { await f.close(); }
});
