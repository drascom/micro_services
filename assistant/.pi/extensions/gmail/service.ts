import { readFile, mkdir, rename, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { GmailAuth, type Http } from './oauth.ts';
import { GmailProvider, type MailProvider } from './provider.ts';
import { KeychainSecretStore, type SecretStore } from './secrets.ts';
import { findLabel, JUNK_LABEL, LATER_LABEL } from './schemas.ts';

export const MAX_IMPORTANCE = 4000;

/** Connected accounts and one provider per account: what triage needs. */
export interface Mailboxes {
  accounts(): Promise<string[]>;
  provider(account: string): MailProvider;
}

/**
 * Process-wide Gmail wiring: secret store (Keychain on macOS), OAuth (one client, N accounts), one REST provider per
 * account and data file paths. Nothing here is model-facing; Admin and the mail_organize tool use it.
 */
export class GmailService implements Mailboxes {
  readonly auth: GmailAuth; readonly dataDir: string; readonly exampleFile: string;
  private readonly http?: Http;
  private readonly providers = new Map<string, MailProvider>();
  private labelsCache = new Map<string, { at: number; missing: string[] }>();
  constructor(options: { dataDir: string; exampleFile: string; store?: SecretStore; http?: Http }) {
    this.dataDir = options.dataDir; this.exampleFile = options.exampleFile; this.http = options.http;
    this.auth = new GmailAuth({ store: options.store ?? new KeychainSecretStore(), ...(options.http ? { http: options.http } : {}) });
  }
  accounts(): Promise<string[]> { return this.auth.accounts(); }
  provider(account: string): MailProvider {
    let provider = this.providers.get(account);
    if (!provider) this.providers.set(account, provider = new GmailProvider(this.auth, account, this.http));
    return provider;
  }
  get logFile() { return path.join(this.dataDir, 'mail-actions.jsonl'); }
  get stateFile() { return path.join(this.dataDir, 'mail-triage-state.json'); }
  get importanceFile() { return path.join(this.dataDir, 'mail-importance.md'); }

  async connected(): Promise<boolean> { return (await this.accounts()).length > 0; }

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

  /**
   * Required labels one account still lacks: missing ones are created first (`labels.create`, needs gmail.modify; never
   * renamed or deleted), so only the ones whose creation failed are returned. Cached for a minute; [] when unknown.
   */
  async missingLabels(account: string): Promise<string[]> {
    const cached = this.labelsCache.get(account);
    if (cached && Date.now() - cached.at < 60_000) return cached.missing;
    try {
      const provider = this.provider(account), labels = await provider.labels(AbortSignal.timeout(10_000));
      const missing: string[] = [];
      for (const name of [LATER_LABEL, JUNK_LABEL].filter(name => !findLabel(labels, name))) {
        try {
          if (!provider.createLabel) throw new Error('unsupported');
          await provider.createLabel(name, AbortSignal.timeout(10_000));
        } catch { missing.push(name); }
      }
      this.labelsCache.set(account, { at: Date.now(), missing });
      return missing;
    } catch { return []; }
  }
  /** Creates any missing required labels for a newly connected account (errors stay on that account). */
  async ensureLabels(account: string) { this.labelsCache.delete(account); return this.missingLabels(account); }
  forgetLabels() { this.labelsCache.clear(); }
}

let shared: GmailService | undefined;
/** The backend's single instance (Keychain + real network); tests construct their own. */
export function gmailService(root: string): GmailService {
  return shared ??= new GmailService({ dataDir: path.join(root, 'data'), exampleFile: path.join(root, 'mail-importance.example.md') });
}
