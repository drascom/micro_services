import { Type, type Static } from 'typebox';

/** Model-facing inputs. The model never picks a mailbox; `account` only filters among the user's active accounts. */
export const ListParams = Type.Object({
  received_after: Type.Optional(Type.String({ minLength: 10, maxLength: 40, description: 'ISO-8601, max 30 days ago; default 7 days' })),
  today_only: Type.Optional(Type.Boolean()),
  unread_only: Type.Optional(Type.Boolean()),
  flagged_only: Type.Optional(Type.Boolean()),
  sender_contains: Type.Optional(Type.String({ minLength: 1, maxLength: 200 })),
  subject_contains: Type.Optional(Type.String({ minLength: 1, maxLength: 200 })),
  limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 25 })),
  account: Type.Optional(Type.String({ minLength: 1, maxLength: 200 }))
}, { additionalProperties: false });
export type ListInput = Static<typeof ListParams>;

export const ReadParams = Type.Object({
  message_ref: Type.String({ pattern: '^m_[A-Za-z0-9_-]{12}$' }),
  max_chars: Type.Optional(Type.Integer({ minimum: 500, maximum: 8000 }))
}, { additionalProperties: false });
export type ReadInput = Static<typeof ReadParams>;

export const MAIL_ERROR_CODES = ['mail_disabled', 'unknown_account', 'account_not_found', 'mailbox_not_found', 'automation_denied',
  'mail_unavailable', 'content_not_cached', 'stale_reference', 'timeout', 'cancelled', 'output_limit',
  'invalid_response', 'invalid_request'] as const;
export type MailErrorCode = typeof MAIL_ERROR_CODES[number];
/** Codes the fixed bridge script itself may report. */
export const BRIDGE_ERROR_CODES: readonly MailErrorCode[] = ['account_not_found', 'mailbox_not_found',
  'automation_denied', 'mail_unavailable', 'content_not_cached', 'stale_reference', 'invalid_request'];

export type BridgeRequest =
  | { op: 'accounts' }
  | { op: 'count'; account: string; mailbox: string }
  | { op: 'list'; account: string; mailbox: string; received_after: string; unread_only: boolean; flagged_only: boolean;
      sender_contains?: string; subject_contains?: string; limit: number }
  | { op: 'read'; account: string; mailbox: string; id: number; message_id: string; max_chars: number };

export type BridgeMessage = { id: number; message_id: string; sender: string; subject: string;
  date_received: string; read: boolean; flagged: boolean; size: number };

export type ListedMessage = { message_ref: string; account: string; sender: string; subject: string; date_received: string;
  read: boolean; flagged: boolean; size: number };
export type ListResult = { messages: ListedMessage[]; matched: number; truncated: boolean };
export type ReadResult = { sender: string; subject: string; date_received: string; body: string; truncated: boolean };

export const MAIL_TOOL_NAMES = ['mail_list_messages', 'mail_read_message'] as const;
