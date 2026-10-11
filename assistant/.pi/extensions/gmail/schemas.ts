import { Type, type Static } from 'typebox';

/** One model-facing tool, kept tiny: its schema is sent with every request that enables it. */
export const OrganizeParams = Type.Object({
  action: Type.Enum(['propose', 'apply', 'purge_junk', 'undo']),
  confirm_token: Type.Optional(Type.String({ maxLength: 40 }))
}, { additionalProperties: false });
export type OrganizeInput = Static<typeof OrganizeParams>;

export const GMAIL_TOOL_NAMES = ['mail_organize'] as const;

export const LEVELS = ['urgent', 'important', 'later', 'junk'] as const;
export type Level = typeof LEVELS[number];

/** User-created labels the actions need; the app never creates labels. */
export const LATER_LABEL = 'Sonra Oku', JUNK_LABEL = 'Gereksiz';

/** Case (Turkish dotted/dotless i too), spacing and Unicode-normalization insensitive label name. */
export const labelKey = (name: string) => name.normalize('NFC').replace(/\s+/g, ' ').trim().toLocaleLowerCase('tr')
  .replace(/ı/g, 'i').normalize('NFD').replace(/\u0307/g, '').normalize('NFC');
/** Label id for a required name: exact, then tolerant (`labelKey`), then a nested `Parent/Name`. */
export function findLabel(labels: ReadonlyMap<string, string>, name: string): string | undefined {
  const exact = labels.get(name);
  if (exact) return exact;
  const want = labelKey(name);
  let nested: string | undefined;
  for (const [label, id] of labels) {
    if (labelKey(label) === want) return id;
    if (!nested && label.includes('/') && labelKey(label.slice(label.lastIndexOf('/') + 1)) === want) nested = id;
  }
  return nested;
}

export const GMAIL_ERROR_CODES = ['gmail_not_connected', 'gmail_auth_expired', 'gmail_rate_limited', 'gmail_unavailable',
  'gmail_wrong_account', 'gmail_not_configured', 'gmail_daily_limit', 'gmail_disabled', 'confirmation_invalid', 'confirmation_expired',
  'confirmation_needs_reply', 'nothing_to_undo', 'invalid_request', 'invalid_response', 'cancelled', 'timeout'] as const;
export type GmailErrorCode = typeof GMAIL_ERROR_CODES[number];

/** Generic messages only: never mail text, tokens or server bodies. */
const MESSAGES: Record<GmailErrorCode, string> = {
  gmail_not_connected: 'Gmail is not connected. The user connects it in Admin > Settings > Gmail.',
  gmail_auth_expired: 'Gmail access expired or was revoked. The user must reconnect in Admin > Settings > Gmail. Do not retry.',
  gmail_rate_limited: 'Gmail rate limit reached. Try again later; do not retry now.',
  gmail_unavailable: 'Gmail did not respond. Try again later.',
  gmail_wrong_account: 'The connected Google account is not the configured Gmail account.',
  gmail_not_configured: 'Gmail OAuth client ID/secret are missing in Admin.',
  gmail_daily_limit: 'The daily mail change limit is reached. Try again tomorrow.',
  gmail_disabled: 'Mail organizing is turned off in Admin.',
  confirmation_invalid: 'Unknown or outdated confirm_token. Run the proposal again.',
  confirmation_expired: 'The confirmation expired. Run the proposal again.',
  confirmation_needs_reply: 'Not applied: show the plan, ask the user and stop; the token works only after their next message.',
  nothing_to_undo: 'There is no applied mail change to undo.',
  invalid_request: 'Invalid mail_organize parameters.',
  invalid_response: 'Gmail returned unexpected data.',
  cancelled: 'The request was cancelled.',
  timeout: 'Gmail did not respond in time.'
};

export class GmailError extends Error {
  readonly code: GmailErrorCode;
  constructor(code: GmailErrorCode) { super(MESSAGES[code]); this.code = code; this.name = 'GmailError'; }
}
