import { createHash, randomBytes, timingSafeEqual } from 'node:crypto';
import { createServer, type Server } from 'node:http';
import { once } from 'node:events';
import { GmailError } from './schemas.ts';
import type { SecretStore } from './secrets.ts';

/** fetch-compatible transport; tests pass a fake, so no real network call happens there. */
export type Http = (url: string, init: { method: string; headers?: Record<string, string>; body?: string; signal?: AbortSignal }) => Promise<Response>;

export const AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth';
export const TOKEN_URL = 'https://oauth2.googleapis.com/token';
export const REVOKE_URL = 'https://oauth2.googleapis.com/revoke';
export const PROFILE_URL = 'https://gmail.googleapis.com/gmail/v1/users/me/profile';
export const SCOPE = 'https://www.googleapis.com/auth/gmail.modify';
export const CONNECT_TIMEOUT_MS = 5 * 60_000;

type Client = { client_id: string; client_secret: string };
/** Secret names: `client`, `accounts` (JSON list of emails) and one `token-<email>` per account; `token` is the pre-list single account. */
const CLIENT = 'client', ACCOUNTS = 'accounts', LEGACY_TOKEN = 'token';
const tokenName = (email: string) => 'token-' + email;
const EMAIL = /^[a-z0-9._+-]{1,64}@[a-z0-9.-]{1,60}\.[a-z]{2,}$/;

const pkce = () => {
  const verifier = randomBytes(48).toString('base64url');
  return { verifier, challenge: createHash('sha256').update(verifier).digest('base64url') };
};
const same = (a: string, b: string) => { const x = Buffer.from(a), y = Buffer.from(b); return x.length === y.length && timingSafeEqual(x, y); };
const form = (values: Record<string, string>) => new URLSearchParams(values).toString();
const PAGE = (text: string) => `<!doctype html><meta charset="utf-8"><title>Ayhan</title><p>${text}</p>`;

/**
 * OAuth 2.0 for an installed ("Desktop app") client: PKCE + loopback redirect on 127.0.0.1 (random port); the user approves
 * in their default browser. Client ID/secret and the refresh token live in the SecretStore (Keychain), never in config.json
 * or logs. The access token stays in memory only.
 */
export class GmailAuth {
  private readonly store: SecretStore; private readonly http: Http; private readonly now: () => number;
  private readonly access = new Map<string, { token: string; expires: number }>();
  private readonly refreshing = new Map<string, Promise<string>>();
  /** Per-account error code (e.g. gmail_auth_expired) until it works again or is reconnected. */
  private readonly errors = new Map<string, string>();
  private pending?: { server: Server; done: Promise<string> };
  private cached?: { client?: Client; tokens: Map<string, string> };
  /** The latest browser-consent error. */
  lastError = '';
  constructor(options: { store: SecretStore; http?: Http; now?: () => number }) {
    this.store = options.store; this.http = options.http ?? ((url, init) => fetch(url, init)); this.now = options.now ?? (() => Date.now());
  }

  /** Client and per-account refresh tokens; a pre-list single `token` moves to its own `token-<email>` once (no reconnect). */
  private reading?: Promise<{ client?: Client; tokens: Map<string, string> }>;
  private async read() {
    if (this.cached) return this.cached;
    return this.reading ??= this.load().finally(() => { this.reading = undefined; });
  }
  private async load() {
    const parse = <T>(text: string | undefined) => { try { return text ? JSON.parse(text) as T : undefined; } catch { return undefined; } };
    const [client, list, legacy] = await Promise.all([this.store.get(CLIENT), this.store.get(ACCOUNTS), this.store.get(LEGACY_TOKEN)]);
    let emails = (parse<unknown[]>(list) ?? []).filter((e): e is string => typeof e === 'string' && EMAIL.test(e));
    const old = parse<{ refresh_token?: unknown; email?: unknown }>(legacy);
    if (old && typeof old.refresh_token === 'string' && typeof old.email === 'string' && EMAIL.test(old.email.toLowerCase())) {
      const email = old.email.toLowerCase();
      if (!emails.includes(email)) {
        await this.store.set(tokenName(email), JSON.stringify({ refresh_token: old.refresh_token }));
        emails = [email, ...emails];
        await this.store.set(ACCOUNTS, JSON.stringify(emails));
      }
      await this.store.delete(LEGACY_TOKEN);
    }
    const tokens = new Map<string, string>();
    for (const email of emails) {
      const token = parse<{ refresh_token?: unknown }>(await this.store.get(tokenName(email)));
      if (typeof token?.refresh_token === 'string') tokens.set(email, token.refresh_token);
    }
    return this.cached = { client: parse<Client>(client), tokens };
  }

  /** Connected account emails, in connection order. */
  async accounts(): Promise<string[]> { return [...(await this.read()).tokens.keys()]; }

  /** Metadata only: whether a client is configured and which accounts are connected (never secrets). */
  async status() {
    const { client, tokens } = await this.read().catch(() => ({ client: undefined, tokens: new Map<string, string>() }));
    return { client_configured: Boolean(client), connected: tokens.size > 0,
      accounts: [...tokens.keys()].map(email => ({ email, error: this.errors.get(email) ?? '' })), connecting: Boolean(this.pending) };
  }

  async saveClient(clientId: unknown, clientSecret: unknown) {
    if (typeof clientId !== 'string' || !/^[\w.-]{3,200}\.apps\.googleusercontent\.com$/.test(clientId.trim()) ||
        typeof clientSecret !== 'string' || !/^[\w.~-]{6,200}$/.test(clientSecret.trim())) throw Error('Invalid OAuth client ID or secret');
    await this.store.set(CLIENT, JSON.stringify({ client_id: clientId.trim(), client_secret: clientSecret.trim() }));
    this.cached = undefined;
  }

  /**
   * Starts the browser consent for any Google account: returns the URL to open; `done` resolves with the verified account
   * email (added, or its token replaced when already connected) once the loopback redirect arrives (or rejects on denial,
   * wrong state or timeout).
   */
  async connect(timeoutMs = CONNECT_TIMEOUT_MS): Promise<{ auth_url: string; done: Promise<string> }> {
    const { client } = await this.read();
    if (!client) throw new GmailError('gmail_not_configured');
    if (this.pending) { this.pending.server.close(); this.pending = undefined; }
    const { verifier, challenge } = pkce(), state = randomBytes(24).toString('base64url');
    const server = createServer();
    server.listen(0, '127.0.0.1'); await once(server, 'listening');
    const address = server.address();
    if (!address || typeof address !== 'object') { server.close(); throw new GmailError('gmail_unavailable'); }
    const redirect = `http://127.0.0.1:${address.port}`;
    const url = AUTH_URL + '?' + form({ client_id: client.client_id, redirect_uri: redirect, response_type: 'code', scope: SCOPE,
      code_challenge: challenge, code_challenge_method: 'S256', state, access_type: 'offline', prompt: 'select_account consent' });
    const done = new Promise<string>((resolve, reject) => {
      const timer = setTimeout(() => finish(new GmailError('timeout')), timeoutMs);
      const finish = (error?: Error, email?: string) => {
        clearTimeout(timer); server.close();
        if (this.pending?.server === server) this.pending = undefined;
        this.lastError = error ? (error instanceof GmailError ? error.code : 'gmail_unavailable') : '';
        if (error) reject(error); else resolve(email!);
      };
      server.on('request', async (request, response) => {
        const query = new URL(request.url ?? '/', redirect).searchParams;
        const reply = (code: number, text: string) => { response.writeHead(code, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' }); response.end(PAGE(text)); };
        if (request.method !== 'GET' || new URL(request.url ?? '/', redirect).pathname !== '/' || !query.get('state')) return reply(404, 'Not found');
        if (!same(query.get('state')!, state)) return reply(400, 'Invalid state.');
        const code = query.get('code');
        if (!code) { reply(400, 'Not connected.'); return finish(new GmailError('gmail_not_connected')); }
        try {
          const email = await this.exchange(client, code, verifier, redirect);
          reply(200, 'Gmail bağlandı / connected. Bu sekmeyi kapatabilirsiniz.'); finish(undefined, email);
        } catch (error) { reply(400, 'Not connected.'); finish(error as Error); }
      });
    });
    done.catch(() => {}); // Observed through status(); callers may also await it.
    this.pending = { server, done };
    return { auth_url: url, done };
  }

  private async exchange(client: Client, code: string, verifier: string, redirect: string): Promise<string> {
    const response = await this.http(TOKEN_URL, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: form({ code, client_id: client.client_id, client_secret: client.client_secret, redirect_uri: redirect,
        grant_type: 'authorization_code', code_verifier: verifier }), signal: AbortSignal.timeout(20_000) });
    if (!response.ok) throw new GmailError('gmail_not_connected');
    const value = await response.json() as { access_token?: unknown; refresh_token?: unknown; expires_in?: unknown; scope?: unknown };
    if (typeof value.access_token !== 'string' || typeof value.refresh_token !== 'string') throw new GmailError('invalid_response');
    if (typeof value.scope === 'string' && !value.scope.split(' ').includes(SCOPE)) throw new GmailError('gmail_not_connected');
    const profile = await this.http(PROFILE_URL, { method: 'GET', headers: { Authorization: `Bearer ${value.access_token}` }, signal: AbortSignal.timeout(20_000) });
    const address = profile.ok ? (await profile.json() as { emailAddress?: unknown }).emailAddress : undefined;
    const email = typeof address === 'string' ? address.toLowerCase() : '';
    if (!EMAIL.test(email)) { await this.revoke(value.refresh_token); throw new GmailError('invalid_response'); }
    const emails = await this.accounts();
    await this.store.set(tokenName(email), JSON.stringify({ refresh_token: value.refresh_token }));
    if (!emails.includes(email)) await this.store.set(ACCOUNTS, JSON.stringify([...emails, email]));
    this.cached = undefined; this.errors.delete(email);
    this.access.set(email, { token: value.access_token, expires: this.now() + (Number(value.expires_in) || 3600) * 1000 });
    return email;
  }

  private async revoke(token: string) {
    await this.http(REVOKE_URL, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: form({ token }),
      signal: AbortSignal.timeout(10_000) }).catch(() => undefined);
  }

  /** Revokes (best effort) and forgets one account's refresh token; the OAuth client and other accounts stay. */
  async disconnect(email: string) {
    const { tokens } = await this.read(), key = email.trim().toLowerCase(), token = tokens.get(key);
    if (!token) throw new GmailError('gmail_not_connected');
    await this.revoke(token);
    await this.store.set(ACCOUNTS, JSON.stringify([...tokens.keys()].filter(e => e !== key)));
    await this.store.delete(tokenName(key));
    this.cached = undefined; this.access.delete(key); this.errors.delete(key);
  }

  /** Records an account-level failure seen by the provider (shown in Admin until it works again). */
  fail(email: string, code: string) { this.errors.set(email, code); }

  /** One account's cached access token, refreshed a minute before expiry; `force` after a 401. */
  async accessToken(email: string, signal: AbortSignal, force = false): Promise<string> {
    const access = this.access.get(email);
    if (!force && access && access.expires - 60_000 > this.now()) return access.token;
    let refreshing = this.refreshing.get(email);
    if (!refreshing) {
      refreshing = this.refresh(email, signal).finally(() => this.refreshing.delete(email));
      this.refreshing.set(email, refreshing);
    }
    return refreshing;
  }

  private async refresh(email: string, signal: AbortSignal): Promise<string> {
    const { client, tokens } = await this.read(), token = tokens.get(email);
    if (!token) throw new GmailError('gmail_not_connected');
    if (!client) throw new GmailError('gmail_not_configured');
    let response: Response;
    try {
      response = await this.http(TOKEN_URL, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: form({ client_id: client.client_id, client_secret: client.client_secret, refresh_token: token, grant_type: 'refresh_token' }),
        signal: AbortSignal.any([signal, AbortSignal.timeout(20_000)]) });
    } catch { throw new GmailError(signal.aborted ? 'cancelled' : 'gmail_unavailable'); }
    if (response.status === 400 || response.status === 401) { this.errors.set(email, 'gmail_auth_expired'); throw new GmailError('gmail_auth_expired'); }
    if (response.status === 429) throw new GmailError('gmail_rate_limited');
    if (!response.ok) throw new GmailError('gmail_unavailable');
    const value = await response.json() as { access_token?: unknown; expires_in?: unknown };
    if (typeof value.access_token !== 'string') throw new GmailError('invalid_response');
    this.access.set(email, { token: value.access_token, expires: this.now() + (Number(value.expires_in) || 3600) * 1000 });
    this.errors.delete(email);
    return value.access_token;
  }
}
