import { spawnSync } from 'node:child_process';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import path from 'node:path';
import { root } from '../src/config.ts';

/** Keep macOS privacy grants associated with the same signer across updates. */
export async function signApp(app: string): Promise<void> {
  const saved = path.join(root, 'data/signing-identity.txt');
  const result = spawnSync('/usr/bin/security', ['find-identity', '-v', '-p', 'codesigning'], { encoding: 'utf8' });
  if (result.error || result.status !== 0) throw Error('Cannot inspect code-signing certificates');
  const identities = [...result.stdout.matchAll(/\b([A-F0-9]{40})\s+"/g)].map(match => match[1]);
  let previous: string | undefined;
  try { previous = (await readFile(saved, 'utf8')).trim(); }
  catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
  const identity = process.env.ASSISTANT_SIGNING_IDENTITY?.trim() || previous || (identities.length === 1 ? identities[0] : undefined);
  if (!identity || !identities.includes(identity)) {
    throw Error('A valid stable signing certificate is required. Set ASSISTANT_SIGNING_IDENTITY to its fingerprint; restore the saved certificate to preserve privacy grants.');
  }
  for (const args of [['--force', '--sign', identity, '--timestamp=none', app], ['--verify', '--deep', '--strict', app]]) {
    const signed = spawnSync('/usr/bin/codesign', args, { stdio: 'inherit' });
    if (signed.error || signed.status !== 0) throw Error('App signing or verification failed');
  }
  await mkdir(path.dirname(saved), { recursive: true, mode: 0o700 });
  await writeFile(saved, identity + '\n', { mode: 0o600 });
}
