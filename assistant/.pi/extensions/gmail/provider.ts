import { GmailError } from './schemas.ts';
import type { GmailAuth, Http } from './oauth.ts';

/** Header and label metadata of one message; never the body. */
export type MessageMeta = { id: string; labels: string[]; from: string; subject: string; snippet: string;
  /** The topmost Authentication-Results header (added by the receiving server), if any. */
  auth?: string; date: number };

/**
 * Provider-neutral mailbox operations used by triage (labels map to folders/flags on other providers).
 * Gmail is the only implementation; it uses REST + fetch, no SDK.
 */
export interface MailProvider {
  /** Label name → id. */
  labels(signal: AbortSignal): Promise<Map<string, string>>;
  /** Up to `max` inbox message ids, newest first, skipping ids for which `skip` is true. */
  inbox(max: number, skip: (id: string) => boolean, signal: AbortSignal): Promise<string[]>;
  /** Up to `max` ids carrying a label. */
  labeled(labelId: string, max: number, signal: AbortSignal): Promise<string[]>;
  metadata(id: string, signal: AbortSignal): Promise<MessageMeta>;
  /** Same change for all ids (one batch call per ≤1000 ids). */
  modify(ids: string[], add: string[], remove: string[], signal: AbortSignal): Promise<void>;
  /** Moves to Trash (recoverable for 30 days). Permanent deletion is deliberately not part of this interface. */
  trash(id: string, signal: AbortSignal): Promise<void>;
}

const BASE = 'https://gmail.googleapis.com/gmail/v1/users/me';
const ID = /^[A-Za-z0-9_-]{1,64}$/;
const str = (value: unknown, max: number) => typeof value === 'string' ? value.slice(0, max) : '';

export class GmailProvider implements MailProvider {
  private readonly auth: GmailAuth; private readonly http: Http;
  constructor(auth: GmailAuth, http: Http = (url, init) => fetch(url, init)) { this.auth = auth; this.http = http; }

  private async call(method: string, path: string, body?: unknown, signal = new AbortController().signal): Promise<any> {
    for (let attempt = 0; ; attempt++) {
      const token = await this.auth.accessToken(signal, attempt > 0);
      let response: Response;
      try {
        response = await this.http(BASE + path, { method, signal: AbortSignal.any([signal, AbortSignal.timeout(20_000)]),
          headers: { Authorization: `Bearer ${token}`, ...(body ? { 'Content-Type': 'application/json' } : {}) },
          ...(body ? { body: JSON.stringify(body) } : {}) });
      } catch { throw new GmailError(signal.aborted ? 'cancelled' : 'gmail_unavailable'); }
      if (response.status === 401 && attempt === 0) continue; // Access token expired early: refresh once.
      if (response.status === 401) throw new GmailError('gmail_auth_expired');
      if (response.status === 429) throw new GmailError('gmail_rate_limited');
      if (response.status === 403) {
        const text = await response.text().catch(() => '');
        throw new GmailError(/rateLimitExceeded|userRateLimitExceeded|quotaExceeded/.test(text) ? 'gmail_rate_limited' : 'gmail_auth_expired');
      }
      if (!response.ok) throw new GmailError('gmail_unavailable');
      if (response.status === 204) return {};
      const text = await response.text();
      if (!text) return {};
      try { return JSON.parse(text); } catch { throw new GmailError('invalid_response'); }
    }
  }

  async labels(signal: AbortSignal) {
    const value = await this.call('GET', '/labels', undefined, signal);
    if (!Array.isArray(value.labels)) throw new GmailError('invalid_response');
    return new Map<string, string>(value.labels.filter((l: any) => typeof l?.name === 'string' && typeof l?.id === 'string').map((l: any) => [l.name, l.id]));
  }

  private async ids(query: string, max: number, skip: (id: string) => boolean, signal: AbortSignal) {
    const found: string[] = [];
    let page = '';
    for (let pages = 0; pages < 5 && found.length < max; pages++) {
      const value = await this.call('GET', `/messages?${query}&maxResults=100${page ? '&pageToken=' + encodeURIComponent(page) : ''}`, undefined, signal);
      for (const item of Array.isArray(value.messages) ? value.messages : []) {
        if (typeof item?.id !== 'string' || !ID.test(item.id) || skip(item.id)) continue;
        found.push(item.id);
        if (found.length >= max) break;
      }
      if (typeof value.nextPageToken !== 'string') break;
      page = value.nextPageToken;
    }
    return found;
  }
  inbox(max: number, skip: (id: string) => boolean, signal: AbortSignal) { return this.ids('labelIds=INBOX', max, skip, signal); }
  labeled(labelId: string, max: number, signal: AbortSignal) { return this.ids('labelIds=' + encodeURIComponent(labelId), max, () => false, signal); }

  async metadata(id: string, signal: AbortSignal): Promise<MessageMeta> {
    if (!ID.test(id)) throw new GmailError('invalid_request');
    const value = await this.call('GET', `/messages/${id}?format=metadata&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Authentication-Results`, undefined, signal);
    if (value.id !== id || !Array.isArray(value.labelIds ?? [])) throw new GmailError('invalid_response');
    const headers: { name: string; value: string }[] = Array.isArray(value.payload?.headers) ? value.payload.headers : [];
    const header = (name: string) => headers.find(h => typeof h?.name === 'string' && h.name.toLowerCase() === name.toLowerCase())?.value;
    return { id, labels: (value.labelIds ?? []).filter((l: unknown) => typeof l === 'string'), from: str(header('From'), 300),
      subject: str(header('Subject'), 300), snippet: str(value.snippet, 300), ...(header('Authentication-Results') ? { auth: str(header('Authentication-Results'), 2000) } : {}),
      date: Number(value.internalDate) || 0 };
  }

  async modify(ids: string[], add: string[], remove: string[], signal: AbortSignal) {
    for (let i = 0; i < ids.length; i += 1000)
      await this.call('POST', '/messages/batchModify', { ids: ids.slice(i, i + 1000), addLabelIds: add, removeLabelIds: remove }, signal);
  }

  async trash(id: string, signal: AbortSignal) {
    if (!ID.test(id)) throw new GmailError('invalid_request');
    await this.call('POST', `/messages/${id}/trash`, undefined, signal);
  }
}
