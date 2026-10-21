import { spawnSync } from 'node:child_process';
import { readdir, rm } from 'node:fs/promises';
import path from 'node:path';

/** Prefix of bundles moved aside by `npm run rebuild-app` (a running instance keeps using its moved bundle). */
export const OLD_PREFIX = 'PersonalAssistant.app.old.';

/** True when some process has the bundle's executable open (the running app). `lsof` exits 1 when none does. */
export function bundleInUse(bundle: string): boolean {
  const result = spawnSync('/usr/sbin/lsof', ['-t', path.join(bundle, 'Contents/MacOS/PersonalAssistant')], { encoding: 'utf8' });
  return result.status === 0 && result.stdout.trim().length > 0;
}

/** Removes moved-aside bundles in `dir` that no running process uses; returns the removed paths. */
export async function cleanOldBundles(dir: string, inUse: (bundle: string) => boolean = bundleInUse): Promise<string[]> {
  let names: string[];
  try { names = await readdir(dir); } catch { return []; }
  const removed: string[] = [];
  for (const name of names) {
    if (!name.startsWith(OLD_PREFIX) || !/^\d+$/.test(name.slice(OLD_PREFIX.length))) continue;
    const bundle = path.join(dir, name);
    if (inUse(bundle)) continue;
    await rm(bundle, { recursive: true, force: true }); removed.push(bundle);
  }
  return removed;
}
