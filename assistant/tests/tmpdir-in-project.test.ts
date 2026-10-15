import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdir, mkdtemp, rm } from 'node:fs/promises';
import path from 'node:path';

// Regression: the app sets TMPDIR inside the project (data/web-access/tmp), whose package.json is "type": "module".
// Tests that write fake scripts or temp repos must pass there too (an update's npm smoke test once failed only in the app).
const root = path.resolve(import.meta.dirname, '..');

test('whisper and updates tests pass with TMPDIR inside the project', { skip: process.env.ASSISTANT_TMPDIR_REGRESSION === '1' }, async () => {
  const base = path.join(root, 'data/web-access/tmp');
  await mkdir(base, { recursive: true });
  const tmp = await mkdtemp(path.join(base, 'tmpdir-regression-'));
  try {
    // NODE_TEST_CONTEXT would make the child report to this runner instead of running standalone (always exit 0).
    const env: NodeJS.ProcessEnv = { ...process.env, TMPDIR: tmp, ASSISTANT_TMPDIR_REGRESSION: '1' };
    delete env.NODE_TEST_CONTEXT;
    const { code, output } = await new Promise<{ code: number | null; output: string }>((resolve, reject) => {
      const child = spawn(process.execPath, ['--test', 'tests/whisper.test.ts', 'tests/updates.test.ts'],
        { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] });
      let output = '';
      child.stdout.on('data', d => { output += d; }); child.stderr.on('data', d => { output += d; });
      child.on('error', reject); child.on('close', code => resolve({ code, output }));
    });
    assert.equal(code, 0, output.slice(-4000));
  } finally { await rm(tmp, { recursive: true, force: true }); }
});
