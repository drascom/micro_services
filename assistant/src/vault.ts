import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { randomUUID } from 'node:crypto';
import { access, mkdir } from 'node:fs/promises';
import path from 'node:path';
import { root, type Config } from './config.ts';

/**
 * Bitwarden/Vaultwarden vault through the official `bw` CLI (data dir `data/bitwarden`). Read-only. The session key lives
 * in this object only (never on disk, argv or logs) and is dropped on Lock, idle timeout, system sleep and backend exit.
 * The master password reaches `bw` through an environment variable of that one child process, never argv.
 * Secret values (password, TOTP) go straight from `bw` to the clipboard; nothing returned here contains them.
 */

export type BwResult = { code: number | null; stdout: string; stderr: string };
/** Runs `bw` with these arguments and extra environment (tests inject a fake). */
export type BwTransport = (args: string[], env: Record<string, string>, signal: AbortSignal, input?: string) => Promise<BwResult>;
/** Asks the user for a secret outside the model (native secure input); undefined = cancelled. */
export type SecretAsk = (title: string, signal: AbortSignal) => Promise<string | undefined>;
export interface Clipboard { read(): Promise<string>; write(value: string): Promise<void>; }

export type VaultState = 'unauthenticated' | 'locked' | 'unlocked';
export type VaultErrorCode = 'clipboard_empty' | 'cancelled' | 'prompt_unavailable' | 'not_installed' | 'disabled' | 'locked' | 'invalid_password' | 'two_factor_required' | 'invalid_request' |
  'not_found' | 'no_value' | 'clipboard_unavailable' | 'server_unreachable' | 'vault_failed';
export class VaultError extends Error {
  code: VaultErrorCode;
  constructor(code: VaultErrorCode, message: string = code) { super(message); this.code = code; }
}

export type VaultItem = { id: string; name: string; username: string; host: string; has_totp: boolean };
export type VaultField = 'password' | 'username' | 'totp';
export type SaveSource = 'clipboard' | 'prompt' | 'generate';
const ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const EMAIL = /^[^\s@"'\\]{1,100}@[^\s@"'\\]{1,150}$/;

function bwBin(config: Config) { return config.bitwarden_bin; }

/** Default transport: `node bw.js …` with a minimal environment; output is bounded. */
export function cliTransport(config: Config, dataDir = path.join(root, 'data/bitwarden')): BwTransport {
  return async (args, env, signal, input) => {
    await mkdir(dataDir, { recursive: true, mode: 0o700 });
    const child = spawn(process.execPath, [bwBin(config), ...args, '--nointeraction'], { stdio: [input === undefined ? 'ignore' : 'pipe', 'pipe', 'pipe'], shell: false, signal,
      env: { PATH: `${path.dirname(process.execPath)}:/usr/bin:/bin`, HOME: process.env.HOME ?? '', BITWARDENCLI_APPDATA_DIR: dataDir,
        BW_NOINTERACTION: 'true', NODE_NO_WARNINGS: '1', ...env } });
    let stdout = '', stderr = '';
    child.stdout!.setEncoding('utf8'); child.stderr!.setEncoding('utf8');
    child.stdout!.on('data', (chunk: string) => { if (stdout.length < 20_000_000) stdout += chunk; });
    child.stderr!.on('data', (chunk: string) => { stderr = (stderr + chunk).slice(-2000); });
    if (input !== undefined) { child.stdin!.on('error', () => {}); child.stdin!.end(input); }
    const timer = setTimeout(() => child.kill('SIGKILL'), 60_000);
    try { const [code] = await once(child, 'close') as [number | null]; return { code, stdout, stderr }; }
    catch { signal.throwIfAborted(); return { code: -1, stdout: '', stderr: 'spawn failed' }; }
    finally { clearTimeout(timer); }
  };
}

/** macOS pbcopy/pbpaste; Linux wl-copy/wl-paste or xclip. Values go through stdin/stdout only. */
export function systemClipboard(): Clipboard {
  const run = async (bin: string, args: string[], input?: string) => {
    const child = spawn(bin, args, { stdio: ['pipe', 'pipe', 'ignore'], shell: false });
    let out = ''; child.stdout.setEncoding('utf8'); child.stdout.on('data', (c: string) => { out += c; });
    child.stdin.on('error', () => {}); child.stdin.end(input ?? '');
    const [code] = await once(child, 'close').catch(() => [-1]) as [number | null];
    if (code !== 0) throw new VaultError('clipboard_unavailable');
    return out;
  };
  const exists = async (bin: string) => access(bin).then(() => true, () => false);
  const pick = async (): Promise<{ read: [string, string[]]; write: [string, string[]] }> => {
    if (process.platform === 'darwin') return { read: ['/usr/bin/pbpaste', []], write: ['/usr/bin/pbcopy', []] };
    for (const dir of ['/usr/bin', '/usr/local/bin']) {
      if (process.env.WAYLAND_DISPLAY && await exists(`${dir}/wl-copy`)) return { read: [`${dir}/wl-paste`, ['-n']], write: [`${dir}/wl-copy`, []] };
      if (await exists(`${dir}/xclip`)) return { read: [`${dir}/xclip`, ['-selection', 'clipboard', '-o']], write: [`${dir}/xclip`, ['-selection', 'clipboard']] };
    }
    throw new VaultError('clipboard_unavailable');
  };
  return {
    async read() { const t = await pick(); return run(t.read[0], t.read[1]).catch(() => ''); },
    async write(value) { const t = await pick(); await run(t.write[0], t.write[1], value); }
  };
}

function failure(result: BwResult): VaultError {
  const text = `${result.stdout}\n${result.stderr}`;
  if (/two-step|two step|2fa|verification code|code is required/i.test(text)) return new VaultError('two_factor_required');
  if (/password is incorrect|invalid master password|username or password/i.test(text)) return new VaultError('invalid_password');
  if (/vault is locked|not logged in|you are not logged/i.test(text)) return new VaultError('locked');
  if (/not found/i.test(text)) return new VaultError('not_found');
  if (/ENOTFOUND|ECONNREFUSED|ETIMEDOUT|fetch failed|network/i.test(text)) return new VaultError('server_unreachable');
  if (result.code === -1 || /Cannot find module/.test(text)) return new VaultError('not_installed');
  return new VaultError('vault_failed');
}

export class VaultService {
  readonly config: Config;
  private readonly bw: BwTransport;
  private readonly clipboard: Clipboard;
  private readonly clearMs: number;
  private session?: string;
  private idle?: NodeJS.Timeout;
  private clearTimer?: NodeJS.Timeout;
  private copied?: string;
  /** Last unlock/login failure code for Admin (never a message from bw). */
  lastError = '';
  readonly prompts = new SecretPrompts();
  private readonly askSecret: SecretAsk;
  constructor(config: Config, options: { transport?: BwTransport; clipboard?: Clipboard; clearMs?: number; ask?: SecretAsk } = {}) {
    this.askSecret = options.ask ?? ((title, signal) => this.prompts.ask(title, signal));
    this.config = config;
    this.bw = options.transport ?? cliTransport(config);
    this.clipboard = options.clipboard ?? systemClipboard();
    this.clearMs = options.clearMs ?? 30_000;
  }
  get unlocked(): boolean { return Boolean(this.session); }
  /** The tools exist only for this: switch on and a session key in memory. */
  get available(): boolean { return this.config.bitwarden_enabled && this.unlocked; }

  async installed(): Promise<boolean> { return access(bwBin(this.config)).then(() => true, () => false); }

  /** `bw status` (no session needed): server and login state; our in-memory key decides locked/unlocked. */
  async status(signal = AbortSignal.timeout(20_000)): Promise<{ server: string; state: VaultState }> {
    const result = await this.bw(['status'], {}, signal);
    if (result.code !== 0) throw failure(result);
    let value: { serverUrl?: string; status?: string } = {};
    try { value = JSON.parse(result.stdout.trim().split('\n').at(-1) ?? '{}'); } catch { throw new VaultError('vault_failed'); }
    const state: VaultState = value.status === 'unauthenticated' ? 'unauthenticated' : this.session ? 'unlocked' : 'locked';
    if (state === 'unauthenticated') this.forget();
    return { server: value.serverUrl ?? '', state };
  }

  /** Points the CLI at the server (only possible while logged out). */
  async setServer(url: string, signal = AbortSignal.timeout(20_000)): Promise<void> {
    const result = await this.bw(['config', 'server', url], {}, signal);
    if (result.code !== 0) throw failure(result);
  }

  /**
   * Admin unlock: logs in first when needed (email from config, optional 2FA code), else `bw unlock`. The password is
   * passed in the child's environment (`--passwordenv`), never argv, and is not kept.
   */
  async unlock(password: unknown, code?: unknown, signal = AbortSignal.timeout(60_000)): Promise<void> {
    if (typeof password !== 'string' || !password || password.length > 1024) throw new VaultError('invalid_request', 'password is required');
    if (code !== undefined && code !== '' && (typeof code !== 'string' || !/^[0-9A-Za-z]{4,12}$/.test(code.trim())))
      throw new VaultError('invalid_request', 'Invalid 2FA code');
    if (!await this.installed()) throw new VaultError('not_installed');
    this.lastError = '';
    try {
      const { state } = await this.status(signal);
      let args: string[];
      if (state === 'unauthenticated') {
        const email = this.config.bitwarden_email.trim();
        if (!EMAIL.test(email)) throw new VaultError('invalid_request', 'email is required');
        args = ['login', email, '--passwordenv', 'BW_PASSWORD', '--raw', ...(typeof code === 'string' && code.trim() ? ['--method', '0', '--code', code.trim()] : [])];
      } else args = ['unlock', '--passwordenv', 'BW_PASSWORD', '--raw'];
      const result = await this.bw(args, { BW_PASSWORD: password }, signal);
      const key = result.stdout.trim().split('\n').at(-1)?.trim() ?? '';
      if (result.code !== 0 || !/^[A-Za-z0-9+/=_-]{20,200}$/.test(key)) throw failure(result);
      this.session = key; this.touch();
      await this.bw(['sync'], { BW_SESSION: key }, signal).catch(() => undefined); // Stale cache still works.
    } catch (error) {
      this.lastError = error instanceof VaultError ? error.code : 'vault_failed';
      throw error instanceof VaultError ? error : new VaultError('vault_failed');
    }
  }

  /** Drops the session key (Lock button, idle timeout, sleep, exit) and locks the CLI's local state. */
  async lock(): Promise<void> {
    const had = this.session; this.forget();
    if (had) await this.bw(['lock'], {}, AbortSignal.timeout(20_000)).catch(() => undefined);
  }
  private forget() { this.session = undefined; clearTimeout(this.idle); this.idle = undefined; }
  /** Each use restarts the idle timer (`bitwarden_idle_lock_min`). */
  touch(): void {
    if (!this.session) return;
    clearTimeout(this.idle);
    this.idle = setTimeout(() => { void this.lock(); }, this.config.bitwarden_idle_lock_min * 60_000);
    this.idle.unref?.();
  }
  private key(): string {
    if (!this.config.bitwarden_enabled) throw new VaultError('disabled');
    if (!this.session) throw new VaultError('locked');
    this.touch(); return this.session;
  }

  /** Up to 5 items: name, username, URI host, opaque id, has_totp. Never password, notes, cards, fields or TOTP secret. */
  async search(query: string, signal: AbortSignal): Promise<VaultItem[]> {
    const text = query.trim().slice(0, 100);
    if (!text) throw new VaultError('invalid_request', 'query is empty');
    const result = await this.bw(['list', 'items', '--search', text], { BW_SESSION: this.key() }, signal);
    if (result.code !== 0) { const error = failure(result); if (error.code === 'locked') this.forget(); throw error; }
    let items: unknown;
    try { items = JSON.parse(result.stdout); } catch { throw new VaultError('vault_failed'); }
    if (!Array.isArray(items)) throw new VaultError('vault_failed');
    return items.slice(0, 5).map(raw => {
      const item = raw as { id?: unknown; name?: unknown; login?: { username?: unknown; totp?: unknown; uris?: { uri?: unknown }[] } };
      const uri = item.login?.uris?.find(u => typeof u?.uri === 'string')?.uri as string | undefined;
      let host = '';
      try { if (uri) host = new URL(/^[a-z][a-z0-9+.-]*:/i.test(uri) ? uri : `https://${uri}`).hostname; } catch {}
      return { id: String(item.id ?? ''), name: String(item.name ?? '').slice(0, 100),
        username: typeof item.login?.username === 'string' ? item.login.username.slice(0, 100) : '', host: host.slice(0, 100),
        has_totp: Boolean(item.login?.totp) };
    });
  }

  /** Copies one field to the clipboard; clears it after 30 s if it still holds that value. Returns only the item name. */
  async copy(id: string, field: VaultField, signal: AbortSignal): Promise<{ item: string }> {
    if (!ID.test(id)) throw new VaultError('invalid_request', 'Unknown id; use vault_search first');
    if (!['password', 'username', 'totp'].includes(field)) throw new VaultError('invalid_request', 'field must be password, username or totp');
    const session = this.key();
    const got = await this.bw(['get', 'item', id], { BW_SESSION: session }, signal);
    if (got.code !== 0) { const error = failure(got); if (error.code === 'locked') this.forget(); throw error; }
    let item: { name?: unknown; login?: { username?: unknown; password?: unknown } };
    try { item = JSON.parse(got.stdout); } catch { throw new VaultError('vault_failed'); }
    let value: unknown;
    if (field === 'totp') {
      const totp = await this.bw(['get', 'totp', id], { BW_SESSION: session }, signal);
      if (totp.code !== 0) throw new VaultError('no_value');
      value = totp.stdout.trim();
    } else value = item.login?.[field];
    if (typeof value !== 'string' || !value) throw new VaultError('no_value');
    await this.copyForAWhile(value);
    return { item: String(item.name ?? '').slice(0, 100) };
  }
  private async copyForAWhile(value: string): Promise<void> {
    await this.clipboard.write(value);
    clearTimeout(this.clearTimer);
    this.copied = value;
    this.clearTimer = setTimeout(() => { void this.clearClipboard(); }, this.clearMs);
    this.clearTimer.unref?.();
  }

  /** Login items whose name matches exactly (case-insensitive): the save tool never overwrites one unconfirmed. */
  async findByName(name: string, signal: AbortSignal): Promise<{ id: string; name: string } | undefined> {
    const result = await this.bw(['list', 'items', '--search', name], { BW_SESSION: this.key() }, signal);
    if (result.code !== 0) { const error = failure(result); if (error.code === 'locked') this.forget(); throw error; }
    let items: unknown;
    try { items = JSON.parse(result.stdout); } catch { throw new VaultError('vault_failed'); }
    const match = (Array.isArray(items) ? items : []).find(i => (i as { type?: unknown }).type === 1 &&
      String((i as { name?: unknown }).name ?? '').trim().toLowerCase() === name.trim().toLowerCase()) as { id?: unknown; name?: unknown } | undefined;
    return match && typeof match.id === 'string' ? { id: match.id, name: String(match.name) } : undefined;
  }

  /**
   * Gets the secret outside the model: the clipboard (cleared afterwards if unchanged), the native secure prompt, or
   * `bw generate` (then copied to the clipboard for 30 s). Saves a new Login item, or with `id` updates only the
   * password of that item (Bitwarden keeps the old one in its password history). Returns no value.
   */
  async save(input: { id?: string; name: string; username?: string; uri?: string; source: SaveSource; length?: number }, signal: AbortSignal): Promise<{ item: string }> {
    const session = this.key();
    let value: string | undefined, raw = '';
    if (input.source === 'clipboard') {
      raw = await this.clipboard.read();
      value = raw.replace(/\r?\n$/, '');
      if (!value) throw new VaultError('clipboard_empty');
    } else if (input.source === 'prompt') {
      value = await this.askSecret(input.name, signal);
      if (!value) throw new VaultError('cancelled');
    } else {
      const length = Math.max(12, Math.min(128, Math.floor(input.length ?? 24)));
      const generated = await this.bw(['generate', '-ulns', '--length', String(length)], {}, signal);
      value = generated.stdout.trim();
      if (generated.code !== 0 || value.length < 12) throw new VaultError('vault_failed');
    }
    if (value.length > 10_000) throw new VaultError('invalid_request', 'Value too long');
    const encode = (item: unknown) => Buffer.from(JSON.stringify(item), 'utf8').toString('base64');
    let saved: BwResult;
    if (input.id) {
      if (!ID.test(input.id)) throw new VaultError('invalid_request');
      const got = await this.bw(['get', 'item', input.id], { BW_SESSION: session }, signal);
      if (got.code !== 0) throw failure(got);
      let item: { type?: unknown; login?: Record<string, unknown> };
      try { item = JSON.parse(got.stdout); } catch { throw new VaultError('vault_failed'); }
      if (item.type !== 1 || !item.login) throw new VaultError('not_found');
      item.login.password = value;
      saved = await this.bw(['edit', 'item', input.id], { BW_SESSION: session }, signal, encode(item));
    } else {
      saved = await this.bw(['create', 'item'], { BW_SESSION: session }, signal, encode({ type: 1, name: input.name, notes: null, favorite: false,
        fields: [], reprompt: 0, folderId: null, organizationId: null, collectionIds: null, secureNote: null, card: null, identity: null,
        login: { username: input.username || null, password: value, totp: null, uris: input.uri ? [{ match: null, uri: input.uri }] : [] } }));
    }
    if (saved.code !== 0) throw failure(saved);
    if (input.source === 'clipboard') { this.copied = raw; await this.clearClipboard(); }
    if (input.source === 'generate') await this.copyForAWhile(value);
    return { item: input.name };
  }

  /** Empties the clipboard only if it still holds the copied value (the user may have copied something else). */
  async clearClipboard(): Promise<void> {
    const secret = this.copied; this.copied = undefined; clearTimeout(this.clearTimer);
    if (!secret) return;
    try { if (await this.clipboard.read() === secret) await this.clipboard.write(''); } catch {}
  }

  /** Backend exit: lock and clear a pending copied secret if still on the clipboard. */
  async close(): Promise<void> { await this.clearClipboard(); await this.lock(); }
}

/**
 * Native secure input bridge: the Mac app long-polls `wait`; `ask` hands it a title and resolves with what the user
 * typed (NSSecureTextField) or undefined on cancel/timeout. The value goes app → backend only, never to the model.
 */
export class SecretPrompts {
  private waiter?: (prompt: { id: string; title: string }) => void;
  private queued?: { id: string; title: string };
  private readonly picked = new Set<string>();
  private readonly answers = new Map<string, (value: string | undefined) => void>();
  /** Native long-poll: a prompt to show, or undefined when `signal` ends first. */
  wait(signal: AbortSignal): Promise<{ id: string; title: string } | undefined> {
    const queued = this.queued;
    if (queued) { this.queued = undefined; this.picked.add(queued.id); return Promise.resolve(queued); }
    return new Promise(resolve => {
      const deliver = (prompt?: { id: string; title: string }) => {
        signal.removeEventListener('abort', stop);
        if (this.waiter === deliver) this.waiter = undefined;
        if (prompt) this.picked.add(prompt.id);
        resolve(prompt);
      };
      const stop = () => deliver();
      this.waiter = deliver;
      if (signal.aborted) stop(); else signal.addEventListener('abort', stop, { once: true });
    });
  }
  /** True if the id was waiting for an answer. */
  answer(id: unknown, value: unknown): boolean {
    const resolve = typeof id === 'string' ? this.answers.get(id) : undefined;
    if (!resolve) return false;
    resolve(typeof value === 'string' && value && value.length <= 10_000 ? value : undefined);
    return true;
  }
  /** Fails with `prompt_unavailable` if the app does not pick the prompt up within `pickupMs`; cancel/timeout = undefined. */
  ask(title: string, signal: AbortSignal, pickupMs = 4000, answerMs = 120_000): Promise<string | undefined> {
    const prompt = { id: randomUUID(), title: title.slice(0, 120) };
    return new Promise<string | undefined>((resolve, reject) => {
      const finish = (value: string | undefined, error?: VaultError) => {
        clearTimeout(pickup); clearTimeout(timeout); signal.removeEventListener('abort', abort);
        this.answers.delete(prompt.id); this.picked.delete(prompt.id);
        if (this.queued === prompt) this.queued = undefined;
        if (error) reject(error); else resolve(value);
      };
      const abort = () => finish(undefined);
      this.answers.set(prompt.id, value => finish(value));
      const pickup = setTimeout(() => { if (!this.picked.has(prompt.id)) finish(undefined, new VaultError('prompt_unavailable')); }, pickupMs);
      const timeout = setTimeout(() => finish(undefined), answerMs);
      signal.addEventListener('abort', abort, { once: true });
      if (this.waiter) this.waiter(prompt); else this.queued = prompt;
    });
  }
}

let shared: VaultService | undefined;
/** One per backend process (the app config object is shared and live). */
export function vaultService(config: Config): VaultService {
  if (!shared || shared.config !== config) shared = new VaultService(config);
  return shared;
}
