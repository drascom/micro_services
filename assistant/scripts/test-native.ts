import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { root } from '../src/config.ts';

const directory = mkdtempSync(path.join(tmpdir(), 'assistant-shortcut-tests-'));
try {
  const binary = path.join(directory, 'checks');
  execFileSync('/usr/bin/xcrun', ['swiftc', '-swift-version', '6', '-parse-as-library', '-o', binary,
    path.join(root, 'native/Shortcut.swift'), path.join(root, 'tests/shortcut-tests.swift'),
    '-framework', 'AppKit', '-framework', 'CoreGraphics'], { stdio: 'inherit' });
  execFileSync(binary, [], { stdio: 'inherit' });
  const recording = path.join(directory, 'recording-checks');
  execFileSync('/usr/bin/xcrun', ['swiftc', '-swift-version', '6', '-parse-as-library', '-o', recording,
    ...['native/Shortcut.swift', 'native/RingModel.swift', 'native/AudioRecorder.swift',
      'native/BackendClient.swift', 'tests/recording-tests.swift'].map(file => path.join(root, file)),
    '-framework', 'AppKit', '-framework', 'CoreGraphics', '-framework', 'AVFoundation'], { stdio: 'inherit' });
  execFileSync(recording, [], { stdio: 'inherit' });
  const backend = path.join(directory, 'backend-checks');
  execFileSync('/usr/bin/xcrun', ['swiftc', '-swift-version', '6', '-parse-as-library', '-o', backend,
    ...['native/BackendClient.swift', 'tests/backend-tests.swift'].map(file => path.join(root, file))], { stdio: 'inherit' });
  execFileSync(backend, [], { stdio: 'inherit' });
} finally { rmSync(directory, { recursive: true, force: true }); }
