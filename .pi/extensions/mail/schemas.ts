import { Type, type Static } from 'typebox';

/** Same short `account` hint on every mail tool that takes one (mail_organize too). */
export const ACCOUNT_HINT = 'email or part; omit=all';

/** Model-facing inputs. The model never picks a mailbox (always the inbox); `account` only filters among the connected Gmail accounts. */
export const ListParams = Type.Object({
  received_after: Type.Optional(Type.String({ minLength: 10, maxLength: 40, description: 'ISO-8601 date; default 7 days, older clamped to 30' })),
  today_only: Type.Optional(Type.Boolean()),
  unread_only: Type.Optional(Type.Boolean()),
  flagged_only: Type.Optional(Type.Boolean()),
  sender_contains: Type.Optional(Type.String({ minLength: 1, maxLength: 200 })),
  subject_contains: Type.Optional(Type.String({ minLength: 1, maxLength: 200 })),
  limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 25 })),
  account: Type.Optional(Type.String({ minLength: 1, maxLength: 200, description: ACCOUNT_HINT }))
}, { additionalProperties: false });
export type ListInput = Static<typeof ListParams>;

export const ReadParams = Type.Object({
  message_ref: Type.String({ pattern: '^m_[A-Za-z0-9_-]{12}$' }),
  max_chars: Type.Optional(Type.Integer({ minimum: 500, maximum: 8000 }))
}, { additionalProperties: false });
export type ReadInput = Static<typeof ReadParams>;

export const MAIL_ERROR_CODES = ['mail_disabled', 'unknown_account', 'auth_expired', 'mail_unavailable', 'body_unavailable',
  'stale_reference', 'timeout', 'cancelled', 'output_limit', 'invalid_response', 'invalid_request'] as const;
export type MailErrorCode = typeof MAIL_ERROR_CODES[number];

export type ListedMessage = { message_ref: string; account: string; sender: string; subject: string; date_received: string;
  read: boolean; flagged: boolean };
/** `errors`: accounts that failed (code per address) while the others were listed. */
export type ListResult = { messages: ListedMessage[]; matched: number; truncated: boolean; note?: string; errors?: Record<string, string> };
export type ReadResult = { sender: string; subject: string; date_received: string; body: string; truncated: boolean };

export const MAIL_TOOL_NAMES = ['mail_list_messages', 'mail_read_message'] as const;
