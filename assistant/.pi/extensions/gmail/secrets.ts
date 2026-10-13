import { spawn } from 'node:child_process';
import { once } from 'node:events';

/**
 * Secret storage behind a tiny interface: macOS Keychain now; an encrypted file store can be plugged in on Linux later.
 * Values are opaque strings; callers never log them.
 */
export interface SecretStore {
  get(name: string): Promise<string | undefined>;
  set(name: string, value: string): Promise<void>;
  delete(name: string): Promise<void>;
}

export class MemorySecretStore implements SecretStore {
  readonly values = new Map<string, string>();
  async get(name: string) { return this.values.get(name); }
  async set(name: string, value: string) { this.values.set(name, value); }
  async delete(name: string) { this.values.delete(name); }
}

/** Secret names: fixed words or `token-<email>` (lowercase; no quote, space or backslash can reach `security -i`). */
const NAME = /^[a-z0-9_-][a-z0-9_.@+-]{0,119}$/;

/**
 * Generic passwords via /usr/bin/security. Secrets never appear on argv: writes go through `security -i` on stdin,
 * base64url-encoded so no quoting issue is possible.
 */
export class KeychainSecretStore implements SecretStore {
  readonly service: string;
  constructor(service = 'Ayhan Gmail') { this.service = service; }

  private async run(args: string[], input?: string): Promise<{ code: number | null; stdout: string }> {
    const child = spawn('/usr/bin/security', args, { stdio: ['pipe', 'pipe', 'ignore'], shell: false, env: { PATH: '/usr/bin:/bin' } });
    const chunks: Buffer[] = [];
    child.stdout.on('data', (chunk: Buffer) => chunks.push(chunk));
    child.stdin.on('error', () => {});
    const timer = setTimeout(() => child.kill('SIGKILL'), 10_000);
    child.stdin.end(input ?? '');
    try {
      const [code] = await once(child, 'close') as [number | null];
      return { code, stdout: Buffer.concat(chunks).toString('utf8') };
    } finally { clearTimeout(timer); }
  }

  async get(name: string) {
    if (!NAME.test(name)) throw Error('Invalid secret name');
    const { code, stdout } = await this.run(['find-generic-password', '-s', this.service, '-a', name, '-w']);
    if (code === 44) return undefined; // Not found.
    if (code !== 0) throw Error('Keychain read failed');
    const value = stdout.trim();
    return value ? Buffer.from(value, 'base64url').toString('utf8') : undefined;
  }

  async set(name: string, value: string) {
    if (!NAME.test(name)) throw Error('Invalid secret name');
    const encoded = Buffer.from(value, 'utf8').toString('base64url');
    const { code } = await this.run(['-i'], `add-generic-password -U -s "${this.service}" -a "${name}" -w "${encoded}"\n`);
    if (code !== 0) throw Error('Keychain write failed');
  }

  async delete(name: string) {
    if (!NAME.test(name)) throw Error('Invalid secret name');
    const { code } = await this.run(['delete-generic-password', '-s', this.service, '-a', name]);
    if (code !== 0 && code !== 44) throw Error('Keychain delete failed');
  }
}
