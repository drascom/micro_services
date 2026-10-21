import { randomBytes } from 'node:crypto';

/**
 * Backend-enforced voice confirmation for deleting a reminder.
 * A user message is one "turn" (remote attempt and local fallback share it). `delete` issues a single-use token
 * bound to the reminder; it is redeemable only in the user's NEXT turn and within TTL_MS, so the model can never
 * confirm its own question. Whether the reply means "yes" is the model's call; the turn/expiry rule is not.
 */
export const CONFIRM_TTL_MS = 2 * 60_000;

let turns = 0;
/** Monotonic turn sequence for the whole process: call once per user message. */
export function newTurn(): number { return ++turns; }
/** The latest turn (no increment): a question asked outside a request (e.g. the update offer) is answered in the next one. */
export function currentTurn(): number { return turns; }

export type DeleteTarget = { id: string; title: string; ref: string };
type Pending = DeleteTarget & { token: string; turn: number; issued: number };
export type ConfirmFailure = 'confirmation_invalid' | 'confirmation_expired' | 'confirmation_needs_reply';

export class ConfirmationStore {
  private pendingItem?: Pending;
  private readonly now: () => number;
  constructor(now = () => Date.now()) { this.now = now; }

  /** One pending deletion at a time; a new request replaces the previous one. */
  issue(turn: number, target: DeleteTarget): string {
    const token = 'c_' + randomBytes(9).toString('base64url');
    this.pendingItem = { ...target, token, turn, issued: this.now() };
    return token;
  }

  private live(turn: number): Pending | undefined {
    const item = this.pendingItem;
    if (item && (this.now() - item.issued > CONFIRM_TTL_MS || turn > item.turn + 1)) this.pendingItem = undefined;
    return this.pendingItem;
  }

  /** The deletion awaiting this turn's answer (for a one-line prompt hint), if any. */
  pending(turn: number): { token: string; title: string } | undefined {
    const item = this.live(turn);
    return item && item.turn === turn - 1 ? { token: item.token, title: item.title } : undefined;
  }

  /** Single use: success or expiry removes the token; a same-turn attempt keeps it for the user's reply. */
  consume(token: string, turn: number, ref?: string): DeleteTarget | ConfirmFailure {
    const item = this.pendingItem;
    if (!item || item.token !== token) return 'confirmation_invalid';
    if (!this.live(turn)) return 'confirmation_expired';
    if (turn <= item.turn) return 'confirmation_needs_reply';
    if (ref !== undefined && ref !== item.ref) return 'confirmation_invalid';
    this.pendingItem = undefined;
    return { id: item.id, title: item.title, ref: item.ref };
  }
}

/** Shared by every request of this backend process. */
export const confirmations = new ConfirmationStore();
