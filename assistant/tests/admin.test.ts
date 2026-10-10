import test from 'node:test';
import assert from 'node:assert/strict';
import { access, mkdtemp, mkdir, readdir, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { once } from 'node:events';
import { fauxAssistantMessage } from '@earendil-works/pi-ai';
import { defaults, type Config } from '../src/config.ts';
import { Orchestrator } from '../src/orchestrator.ts';
import { makeServer } from '../src/server.ts';
import { observeRequest } from '../src/activity.ts';
import type { MailTransport } from '../.pi/extensions/macos-mail/client.ts';

const skill = (name: string, description = 'Test skill.') => `---\nname: ${name}\ndescription: ${description}\n---\n# ${name}\n`;

async function setup(mailTransport?: MailTransport) {
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
  const configPath = path.join(directory, 'config.json');
  const lifecycle = new AbortController(), server = await makeServer(config, app, lifecycle.signal, activityFile, directory, [path.join(directory, 'trash')],
    { configPath, transport: mailTransport ?? { request: async () => JSON.stringify({ ok: false, error: 'automation_denied' }) } });
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  const address = server.address(); assert.ok(address && typeof address === 'object');
  const base = `http://127.0.0.1:${address.port}`;
  const { token } = await (await fetch(base + '/session')).json() as { token: string };
  const call = async (route: string, body: object = {}, key = token) => {
    const response = await fetch(base + route, { method: 'POST', headers: { 'X-Assistant-Token': key }, body: JSON.stringify(body) });
    return { status: response.status, body: await response.json() as any };
  };
  const close = async () => { lifecycle.abort(); await new Promise<void>(resolve => server.close(() => resolve())); await rm(directory, { recursive: true, force: true }); };
  return { directory, call, close, activityFile, config, configPath };
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

test('admin deletes skills and extensions into the trash, refusing built-ins, traversal and missing tokens', async () => {
  const f = await setup();
  try {
    await mkdir(path.join(f.directory, '.pi/extensions/macos-mail'));
    await writeFile(path.join(f.directory, '.pi/extensions/permissions.ts'), 'export default function p() {}\n');
    assert.equal((await f.call('/admin/delete', { kind: 'skill', name: 'demo' }, 'bad')).status, 403);
    for (const [kind, name] of [['skill', '..'], ['skill', '../extensions'], ['skill', '../../secret.txt'], ['extension', '../skills/demo'],
      ['skill', ''], ['skill', 'missing'], ['permissions', 'demo'], ['extension', 'macos-mail'], ['extension', 'permissions.ts']]) {
      assert.equal((await f.call('/admin/delete', { kind, name })).status, 400, `${kind}:${name}`);
    }
    assert.match((await f.call('/admin/delete', { kind: 'extension', name: 'macos-mail' })).body.error, /Built-in/);
    await access(path.join(f.directory, '.pi/extensions/permissions.ts'));
    await access(path.join(f.directory, 'secret.txt'));
    const removed = await f.call('/admin/delete', { kind: 'skill', name: 'demo' });
    assert.equal(removed.status, 200); assert.match(removed.body.note, /trash/);
    await assert.rejects(access(path.join(f.directory, '.pi/skills/demo')));
    assert.equal((await f.call('/admin/delete', { kind: 'extension', name: 'tool.ts' })).status, 200);
    await assert.rejects(access(path.join(f.directory, '.pi/extensions/tool.ts')));
    const trashed = await readdir(path.join(f.directory, 'trash'));
    assert.ok(trashed.some(n => n.startsWith('demo-')) && trashed.some(n => /^tool-.*\.ts$/.test(n)));
    await access(path.join(f.directory, '.pi/skills'));
    const list = await f.call('/admin/resources');
    assert.equal(list.body.skills.length, 0); assert.ok(!list.body.extensions.some((e: { name: string }) => e.name === 'tool.ts'));
  } finally { await f.close(); }
});

test('admin mail accounts: lists Mail.app names, saves a validated list into config.json and keeps other settings', async () => {
  const requests: unknown[] = [];
  const f = await setup({ request: async request => { requests.push(request); return JSON.stringify({ ok: true, accounts: ['Work', 'Home', 'bad\u0007'] }); } });
  try {
    await writeFile(f.configPath, JSON.stringify({ language: 'tr', local_context: 16384, mail_enabled: true, mail_account: 'Work', mail_mailbox: 'INBOX' }, null, 2));
    assert.equal((await f.call('/admin/mail/accounts', {}, 'bad')).status, 403);
    assert.equal((await f.call('/admin/mail/save', { accounts: [] }, 'bad')).status, 403);
    const listed = await f.call('/admin/mail/accounts');
    assert.equal(listed.status, 200); assert.deepEqual(listed.body.available, ['Work', 'Home']); assert.deepEqual(listed.body.accounts, []);
    assert.deepEqual(requests, [{ op: 'accounts' }]);
    for (const accounts of [undefined, [{ account: '' }], [{ account: 'Work' }, { account: 'Work', mailbox: 'inbox' }], [{ account: 'A', enabled: 'yes' }]])
      assert.equal((await f.call('/admin/mail/save', { accounts })).status, 400, JSON.stringify(accounts));
    const accounts = [{ account: 'Work', mailbox: 'INBOX', enabled: true }, { account: 'Home', mailbox: 'INBOX', enabled: false }];
    const saved = await f.call('/admin/mail/save', { accounts });
    assert.equal(saved.status, 200); assert.deepEqual(saved.body.accounts, accounts);
    const file = JSON.parse(await readFile(f.configPath, 'utf8'));
    assert.deepEqual(file, { language: 'tr', local_context: 16384, mail_accounts: accounts });
    assert.deepEqual(f.config.mail_accounts, accounts); // Live: the next Mail request uses it.
    assert.deepEqual((await f.call('/admin/mail/accounts')).body.accounts, accounts);
    assert.deepEqual(await readdir(f.directory).then(names => names.filter(n => n.includes('config.json'))), ['config.json']);
  } finally { await f.close(); }
});

test('admin mail accounts reports a Mail permission error and still saves manual names; invalid config.json is left alone', async () => {
  const f = await setup();
  try {
    const listed = await f.call('/admin/mail/accounts');
    assert.equal(listed.body.available, null); assert.equal(listed.body.available_error, 'automation_denied');
    assert.equal((await f.call('/admin/mail/save', { accounts: [{ account: 'Manual' }] })).status, 200);
    assert.deepEqual(JSON.parse(await readFile(f.configPath, 'utf8')), { mail_accounts: [{ account: 'Manual', mailbox: 'INBOX', enabled: true }] });
    await writeFile(f.configPath, '{"language": "tr", broken');
    const failed = await f.call('/admin/mail/save', { accounts: [] });
    assert.equal(failed.status, 400); assert.ok(!failed.body.error.includes('broken'));
    assert.equal(await readFile(f.configPath, 'utf8'), '{"language": "tr", broken');
  } finally { await f.close(); }
});

test('admin Reminders switch: token required, saved in config.json with other settings kept, applied live', async () => {
  const f = await setup();
  try {
    await writeFile(f.configPath, JSON.stringify({ language: 'tr' }));
    assert.equal((await f.call('/admin/reminders/save', { enabled: true }, 'bad')).status, 403);
    const off = { enabled: false, tasks_enabled: false, tasks_list: 'Asistan' };
    assert.deepEqual((await f.call('/admin/reminders')).body, off);
    assert.equal((await f.call('/admin/reminders/save', { enabled: 'yes' })).status, 400);
    assert.deepEqual((await f.call('/admin/reminders/save', { enabled: true })).body, { ...off, enabled: true });
    assert.deepEqual(JSON.parse(await readFile(f.configPath, 'utf8')), { language: 'tr', reminders_enabled: true });
    assert.equal(f.config.reminders_enabled, true);
    // Assistant tasks: switch + list name; an invalid name is rejected and nothing changes.
    assert.equal((await f.call('/admin/reminders/save', { tasks_list: '  ' })).status, 400);
    assert.deepEqual((await f.call('/admin/reminders/save', { tasks_enabled: true, tasks_list: ' İşler ' })).body,
      { enabled: true, tasks_enabled: true, tasks_list: 'İşler' });
    assert.deepEqual(JSON.parse(await readFile(f.configPath, 'utf8')), { language: 'tr', reminders_enabled: true, tasks_enabled: true, tasks_list: 'İşler' });
    assert.equal(f.config.tasks_list, 'İşler');
    assert.deepEqual((await f.call('/admin/reminders')).body, { enabled: true, tasks_enabled: true, tasks_list: 'İşler' });
  } finally { await f.close(); }
});
