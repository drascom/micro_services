import { Type, type Static } from 'typebox';

/** One model-facing tool. Kept tiny: every character is sent with each request that enables Reminders. */
export const RemindersParams = Type.Object({
  action: Type.Enum(['list', 'add', 'update', 'complete', 'delete']),
  // Lengths and reference formats are checked by the client (unknown_reference, confirmation_invalid, ...).
  ref: Type.Optional(Type.String()),
  title: Type.Optional(Type.String()),
  notes: Type.Optional(Type.String()),
  due: Type.Optional(Type.String()),
  priority: Type.Optional(Type.Enum(['none', 'low', 'medium', 'high'])),
  list: Type.Optional(Type.String()),
  status: Type.Optional(Type.Enum(['open', 'done', 'all'])),
  range: Type.Optional(Type.Enum(['today', 'overdue', 'upcoming'])),
  limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 50 })),
  confirm_token: Type.Optional(Type.String())
}, { additionalProperties: false });
export type RemindersInput = Static<typeof RemindersParams>;

export const REMINDERS_ERROR_CODES = ['reminders_disabled', 'list_not_found', 'automation_denied', 'reminders_unavailable',
  'unknown_reference', 'stale_reference', 'empty_title', 'invalid_date', 'invalid_request', 'confirmation_invalid',
  'confirmation_expired', 'confirmation_needs_reply', 'timeout', 'cancelled', 'output_limit', 'invalid_response'] as const;
export type RemindersErrorCode = typeof REMINDERS_ERROR_CODES[number];
/** Codes the fixed bridge script itself may report. */
export const BRIDGE_ERROR_CODES: readonly RemindersErrorCode[] = ['list_not_found', 'automation_denied', 'reminders_unavailable',
  'stale_reference', 'invalid_request'];

/** Bridge dates are absolute ISO instants; `all_day` marks a date-only due date (local midnight). */
export type BridgeRequest =
  | { op: 'list'; list?: string; status: 'open' | 'done' | 'all'; due_after?: string; due_before?: string; undated: boolean; max: number; notes_max?: number;
      skip_prefixes?: string[]; last_prefixes?: string[] }
  | { op: 'add'; title: string; notes?: string; due?: string; all_day?: boolean; priority?: number; list?: string }
  | { op: 'update'; id: string; expected_title: string; title?: string; notes?: string; due?: string | null; all_day?: boolean; priority?: number; flagged?: boolean }
  | { op: 'complete'; id: string; expected_title: string }
  | { op: 'delete'; id: string; expected_title: string };

export type BridgeReminder = { id: string; title: string; notes: string; due: string | null; all_day: boolean;
  priority: number; completed: boolean; flagged?: boolean; list: string };

export const REMINDERS_TOOL_NAMES = ['reminders'] as const;
