import { readFile, mkdir, rename, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { GmailAuth, type Http } from './oauth.ts';
import { GmailProvider, type MailProvider } from './provider.ts';
import { KeychainSecretStore, type SecretStore } from './secrets.ts';
import { findLabel, JUNK_LABEL, LATER_LABEL } from './schemas.ts';

export const MAX_IMPORTANCE = 4000;

/**
 * Process-wide Gmail wiring: secret store (Keychain on macOS), OAuth, REST provider and data file paths.
 * Nothing here is model-facing; Admin and the mail_organize tool use it.
 */
export class GmailService {
  readonly auth: GmailAuth; readonly provider: MailProvider; readonly dataDir: string; readonly exampleFile: string;
  private labelsCache?: { at: number; missing: string[] };
  constructor(options: { dataDir: string; exampleFile: string; store?: SecretStore; http?: Http; provider?: MailProvider }) {
    this.dataDir = options.dataDir; this.exampleFile = options.exampleFile;
    this.auth = new GmailAuth({ store: options.store ?? new KeychainSecretStore(), ...(options.http ? { http: options.http } : {}) });
    this.provider = options.provider ?? new GmailProvider(this.auth, options.http);
  }
  get logFile() { return path.join(this.dataDir, 'mail-actions.jsonl'); }
  get stateFile() { return path.join(this.dataDir, 'mail-triage-state.json'); }
  get importanceFile() { return path.join(this.dataDir, 'mail-importance.md'); }

  async connected(): Promise<boolean> { return (await this.auth.status()).connected; }

  /** The user's importance definition (gitignored data file); the tracked example until they save one. */
  async importance(): Promise<string> {
    try { return await readFile(this.importanceFile, 'utf8'); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
    return readFile(this.exampleFile, 'utf8').catch(() => '');
  }
  async saveImportance(content: unknown) {
    if (typeof content !== 'string' || content.length > MAX_IMPORTANCE) throw Error(`Importance text: up to ${MAX_IMPORTANCE} characters`);
    await mkdir(this.dataDir, { recursive: true, mode: 0o700 });
    const temporary = this.importanceFile + '.tmp';
    await writeFile(temporary, content, { mode: 0o600 });
    await rename(temporary, this.importanceFile);
    return { content };
  }

  /** Required labels the user has not created (cached for a minute); [] when not connected or unknown. */
  async missingLabels(): Promise<string[]> {
    if (this.labelsCache && Date.now() - this.labelsCache.at < 60_000) return this.labelsCache.missing;
    if (!await this.connected()) return [];
    try {
      const labels = await this.provider.labels(AbortSignal.timeout(10_000));
      const missing = [LATER_LABEL, JUNK_LABEL].filter(name => !findLabel(labels, name));
      this.labelsCache = { at: Date.now(), missing };
      return missing;
    } catch { return []; }
  }
  forgetLabels() { this.labelsCache = undefined; }
}

let shared: GmailService | undefined;
/** The backend's single instance (Keychain + real network); tests construct their own. */
export function gmailService(root: string): GmailService {
  return shared ??= new GmailService({ dataDir: path.join(root, 'data'), exampleFile: path.join(root, 'mail-importance.example.md') });
}
