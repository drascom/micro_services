import type { ExtensionAPI } from '@earendil-works/pi-coding-agent';
import { Type } from 'typebox';
import type { ConfirmationStore } from '../apple-reminders/confirmations.ts';
import { updateConfirmations, type UpdateService } from '../../../src/updates.ts';

/** Model-facing schema, kept tiny. */
const Params = Type.Object({
  action: Type.Enum(['list', 'apply', 'rollback']), ids: Type.Optional(Type.Array(Type.String())), confirm_token: Type.Optional(Type.String())
}, { additionalProperties: false });
export const UPDATE_TOOL_NAMES = ['updates'] as const;

const reply = (data: unknown, isError = false) => ({ content: [{ type: 'text' as const, text: JSON.stringify(data) }],
  details: (isError ? { error_code: (data as { error: string }).error } : {}) as { error_code?: string }, ...(isError ? { isError: true } : {}) });

/**
 * Component updates (llama.cpp, Pi, bw, …): `list`; `apply`/`rollback` first return a confirm_token (the user answers in
 * the next turn), then queue the work, which runs after this request in the background. A user request only.
 */
export function componentUpdates(options: { enabled: boolean; service?: UpdateService; turn: number; store?: ConfirmationStore }) {
  return (pi: ExtensionAPI) => {
    if (!options.enabled || !options.service) return;
    const service = options.service, store = options.store ?? updateConfirmations;
    pi.registerTool({
      name: 'updates', label: 'Component updates', executionMode: 'sequential',
      annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false },
      description: "List or install updates of the assistant's own components. apply/rollback: first call returns confirm_token; ask the user, call again with it only after they agree.",
      parameters: Params,
      async execute(_id, params) {
        const ids = params.ids?.map(id => String(id).slice(0, 40)).slice(0, 20);
        if (params.action === 'list') {
          const view = await service.list();
          return reply({ last_check: view.last_check ? new Date(view.last_check).toISOString().slice(0, 16) : null, applying: view.applying,
            updates: view.updates.filter(u => u.status !== 'skipped').map(u => ({ id: u.id, name: u.name, current: u.current, latest: u.latest,
              ...(u.summary ? { summary: u.summary } : {}), ...(u.breaking ? { breaking: true } : {}), ...(u.security ? { security: true } : {}),
              ...(u.affects_our_flags ? { affects_our_flags: true } : {}), ...(u.size_mb ? { size_mb: u.size_mb } : {}) })),
            ...(view.rollback.length ? { rollback: view.rollback.map(r => r.id) } : {}) });
        }
        const action = params.action as 'apply' | 'rollback';
        if (params.confirm_token !== undefined) {
          // A token for the other action is refused without spending it.
          const waiting = store.pending(options.turn);
          if (waiting?.token === params.confirm_token && waiting.title.split(' ')[0] !== action) return reply({ error: 'confirmation_invalid' }, true);
          const target = store.consume(params.confirm_token.slice(0, 40), options.turn);
          if (typeof target === 'string') return reply({ error: target }, true);
          const ref = JSON.parse(target.ref) as { action: string; ids: string[] };
          if (ref.action !== action) return reply({ error: 'confirmation_invalid' }, true);
          const chosen = ids?.length ? ref.ids.filter(id => ids.includes(id)) : ref.ids;
          const started = action === 'apply' ? service.startApply(chosen) : service.startRollback(chosen);
          return reply({ started, next: 'Say it runs in the background after this reply and a notification follows. It is not done yet.' });
        }
        const plan = await service.describe(ids, action);
        if (!plan.ids.length) return reply({ error: action === 'apply' ? 'no_updates' : 'nothing_to_roll_back' }, true);
        const token = store.issue(options.turn, { id: 'updates', title: `${action} ${plan.items.join(', ')}`, ref: JSON.stringify({ action, ids: plan.ids }) });
        return reply({ items: plan.items, confirm_token: token, next: 'Ask the user to confirm and stop.' });
      }
    });
  };
}

// Standard Pi CLI sessions: updates need the running app's queue, so nothing is registered.
export default function (_pi: ExtensionAPI) {}
