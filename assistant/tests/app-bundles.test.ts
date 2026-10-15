import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readdir, rm, writeFile } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { cleanOldBundles, bundleInUse, OLD_PREFIX } from '../scripts/app-bundles.ts';

test('stale moved-aside app bundles are removed, the one in use and unrelated entries are kept', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'app-bundles-'));
  try {
    for (const name of [`${OLD_PREFIX}1`, `${OLD_PREFIX}2`, `${OLD_PREFIX}x`, 'PersonalAssistant.app', 'other'])
      await mkdir(path.join(dir, name, 'Contents/MacOS'), { recursive: true });
    const removed = await cleanOldBundles(dir, bundle => bundle.endsWith(`${OLD_PREFIX}2`));
    assert.deepEqual(removed, [path.join(dir, `${OLD_PREFIX}1`)]);
    assert.deepEqual((await readdir(dir)).sort(), ['PersonalAssistant.app', `${OLD_PREFIX}2`, `${OLD_PREFIX}x`, 'other'].sort());
    assert.deepEqual(await cleanOldBundles(path.join(dir, 'missing')), []);
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('a bundle whose executable is running counts as in use', { skip: process.platform !== 'darwin', timeout: 20_000 }, async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'app-bundles-live-'));
  const bundle = path.join(dir, `${OLD_PREFIX}9`), executable = path.join(bundle, 'Contents/MacOS/PersonalAssistant');
  await mkdir(path.dirname(executable), { recursive: true });
  await writeFile(executable, '/bin/sleep 30\ntrue\n'); // sh keeps the script open while it runs
  const child = spawn('/bin/sh', [executable], { stdio: 'ignore' });
  const exited = new Promise(resolve => child.once('exit', resolve));
  try {
    await new Promise(resolve => setTimeout(resolve, 300));
    assert.equal(bundleInUse(bundle), true);
    assert.deepEqual(await cleanOldBundles(dir), []);
  } finally { child.kill(); await exited; }
  assert.equal(bundleInUse(bundle), false);
  await rm(dir, { recursive: true, force: true });
});
