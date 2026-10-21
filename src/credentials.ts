import { readFile, mkdir, writeFile, rename } from 'node:fs/promises';
import path from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import type { Credential, CredentialInfo, CredentialStore, AuthOperationOptions } from '@earendil-works/pi-ai';
import lockfile from 'proper-lockfile';

/** Shares Pi's credential file and proper-lockfile protocol for coordinated refresh. */
export class FileCredentials implements CredentialStore {
  file: string;
  constructor(file: string) { this.file = file; }
  async contents(): Promise<Record<string, Credential>> {
    try { return JSON.parse(await readFile(this.file, 'utf8')); }
    catch (e) { if ((e as NodeJS.ErrnoException).code === 'ENOENT') return {}; throw e; }
  }
  async read(providerId: string): Promise<Credential | undefined> { return (await this.contents())[providerId]; }
  async list(): Promise<readonly CredentialInfo[]> {
    return Object.entries(await this.contents()).map(([providerId, value]) => ({ providerId, type: value.type }));
  }
  async locked<T>(operation: () => Promise<T>, options?: AuthOperationOptions): Promise<T> {
    await mkdir(path.dirname(this.file), { recursive: true, mode: 0o700 });
    const signal = options?.signal ? AbortSignal.any([options.signal, AbortSignal.timeout(30000)]) : AbortSignal.timeout(30000);
    let release: () => Promise<void>;
    let compromised: Error | undefined;
    while (true) {
      signal.throwIfAborted();
      try { release = await lockfile.lock(this.file, { realpath: false, retries: 0, stale: 30000,
        onCompromised: error => { compromised = error; } }); break; }
      catch (e) { if ((e as NodeJS.ErrnoException).code !== 'ELOCKED') throw e; await delay(100, undefined, { signal }); }
    }
    try { const result = await operation(); if (compromised) throw compromised; return result; }
    finally { await release(); }
  }
  async persist(data: Record<string, Credential>): Promise<void> {
    const temporary = this.file + '.' + process.pid + '.tmp';
    await writeFile(temporary, JSON.stringify(data), { mode: 0o600 });
    await rename(temporary, this.file);
  }
  async modify(providerId: string, fn: (current: Credential | undefined) => Promise<Credential | undefined>, options?: AuthOperationOptions): Promise<Credential | undefined> {
    return this.locked(async () => {
      const data = await this.contents();
      const next = await fn(data[providerId]);
      if (next) { data[providerId] = next; await this.persist(data); }
      return next ?? data[providerId];
    }, options);
  }
  async delete(providerId: string, options?: AuthOperationOptions): Promise<void> {
    await this.locked(async () => { const data = await this.contents(); delete data[providerId]; await this.persist(data); }, options);
  }
}
