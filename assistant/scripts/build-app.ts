import { spawnSync } from 'node:child_process';
import { writeFile, mkdir, readdir, access, rename, rm } from 'node:fs/promises';
import path from 'node:path';
import { root, loadConfig } from '../src/config.ts';
import { signApp } from './signing.ts';
import { cleanOldBundles, OLD_PREFIX } from './app-bundles.ts';

const app = path.join(root, 'PersonalAssistant.app');
const replace = process.argv.includes('--replace');
let exists = true;
try { await access(app); } catch (e) { if ((e as NodeJS.ErrnoException).code !== 'ENOENT') throw e; exists = false; }
if (exists && !replace) throw Error('PersonalAssistant.app exists. Move it aside or pass --replace.');
// Build into a staging bundle and swap only when it is complete and signed: the installed app is never missing
// or half-built, so a restart during (or after a failed) rebuild still opens a working app.
await mkdir(path.join(root, 'data'), { recursive: true, mode: 0o700 });
const staging = path.join(root, 'data/app-build/PersonalAssistant.app');
await rm(staging, { recursive: true, force: true });
const macos = path.join(staging, 'Contents/MacOS'), resources = path.join(staging, 'Contents/Resources');
await mkdir(macos, { recursive: true }); await mkdir(resources, { recursive: true });
const config = await loadConfig();
await mkdir(path.dirname(config.language_detector), { recursive: true });
const detector = spawnSync('/usr/bin/xcrun', ['swiftc', '-O', '-o', config.language_detector,
  path.join(root, 'scripts/detect-language.swift'), '-framework', 'NaturalLanguage'], { stdio: 'inherit' });
if (detector.error || detector.status !== 0) throw Error('Language detector compilation failed');
await writeFile(path.join(resources, 'launcher.json'), JSON.stringify({ root, node: process.execPath, port: config.port }));
const sources = (await readdir(path.join(root, 'native'))).filter(name => name.endsWith('.swift')).map(name => path.join(root, 'native', name));
const compile = spawnSync('/usr/bin/xcrun', ['swiftc', '-swift-version', '6', '-O', '-parse-as-library',
  '-target', 'arm64-apple-macos14.0', '-o', path.join(macos, 'PersonalAssistant'), ...sources,
  '-framework', 'AppKit', '-framework', 'SwiftUI', '-framework', 'AVFoundation', '-framework', 'CoreGraphics'], { stdio: 'inherit' });
if (compile.error || compile.status !== 0) throw Error('Native assistant compilation failed');
const info = {
  CFBundleIdentifier: 'com.drascom.personal-assistant', CFBundleName: 'PersonalAssistant', CFBundleDisplayName: 'Ayhan Assistant',
  CFBundleExecutable: 'PersonalAssistant', CFBundlePackageType: 'APPL', CFBundleShortVersionString: '0.3.0',
  PAInstallPath: path.resolve(app),
  LSMinimumSystemVersion: '14.0', LSUIElement: true, NSHighResolutionCapable: true,
  NSMicrophoneUsageDescription: 'Ayhan records your voice while you hold Control and Option. Audio is transcribed locally on this Mac.',
  NSAppleEventsUsageDescription: 'Ayhan reads messages already available in Apple Mail only when you ask it to search, summarize, or classify email. It does not send or modify messages.'
};
const plist = path.join(staging, 'Contents/Info.plist'); await writeFile(plist, JSON.stringify(info));
const packaging = spawnSync('/usr/bin/plutil', ['-convert', 'xml1', plist], { stdio: 'inherit' });
if (packaging.error || packaging.status !== 0) throw Error('Native packaging failed');
await signApp(staging);
if (exists) {
  const old = path.join('/tmp', `${OLD_PREFIX}${Date.now()}`);
  await rename(app, old); console.log(`Moved previous app to ${old}`);
}
await rename(staging, app);
// Older moved-aside bundles are removed unless a running instance still uses one.
const removed = await cleanOldBundles('/tmp');
if (removed.length) console.log(`Removed ${removed.length} stale previous app bundle(s) from /tmp`);
console.log(app);
