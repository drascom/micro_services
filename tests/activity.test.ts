import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { activity, observeRequest } from '../src/activity.ts';

test('activity log correlates overlapping requests, records failure, and protects file permissions', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'ayhan-activity-')), file = path.join(directory, 'activity.log');
  try {
    await Promise.all([
      observeRequest(file, { kind: 'chat', text_chars: 8 }, async () => { activity('tool_finished', { tool: 'web_search', success: true, duration_ms: 10 }); return 'private answer'; }),
      assert.rejects(observeRequest(file, { kind: 'speech' }, async () => { throw Error('private error text'); }))
    ]);
    const raw = await readFile(file, 'utf8'), entries = raw.trim().split('\n').map(line => JSON.parse(line));
    assert.equal(new Set(entries.map(entry => entry.request)).size, 2);
    assert.equal(entries.filter(entry => entry.event === 'request_finished').length, 2);
    assert.ok(entries.some(entry => entry.event === 'request_finished' && entry.success === false));
    assert.ok(!raw.includes('private'));
    assert.equal((await stat(file)).mode & 0o777, 0o600);
    await writeFile(file, 'x'.repeat(2 * 1024 * 1024 + 1));
    await observeRequest(file, { kind: 'chat' }, async () => {});
    assert.ok((await stat(file + '.previous')).size > 2 * 1024 * 1024);
    assert.ok((await stat(file)).size < 1000);
  } finally { await rm(directory, { recursive: true, force: true }); }
});
