import { readFile, rm } from 'node:fs/promises';
import path from 'node:path';
import { root } from '../src/config.ts';

// Pi 0.99.2 ships a shrinkwrapped vulnerable brace-expansion. npm ignores
// overrides for that nested shrinkwrap. Use the explicitly pinned patched
// root dependency instead; minimatch resolves it normally after removal.
const patched = JSON.parse(await readFile(path.join(root, 'node_modules/brace-expansion/package.json'), 'utf8'));
if (patched.version !== '5.0.12') throw Error('Expected patched brace-expansion 5.0.12');
const bundled = path.join(root, 'node_modules/@earendil-works/pi-coding-agent/node_modules/brace-expansion');
try {
  const version = JSON.parse(await readFile(path.join(bundled, 'package.json'), 'utf8')).version;
  if (version !== patched.version) await rm(bundled, { recursive: true, force: true });
} catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
