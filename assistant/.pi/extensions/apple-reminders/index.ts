import type { ExtensionAPI } from '@earendil-works/pi-coding-agent';
import { loadConfig } from '../../../src/config.ts';
import { RemindersClient, RemindersError, osascriptTransport, resultText, type RemindersSettings, type RemindersTransport } from './client.ts';
import { confirmations, newTurn, type ConfirmationStore } from './confirmations.ts';
import { RemindersParams } from './schemas.ts';

type Details = { error_code?: string; result_count?: number; truncated?: boolean };

function failure(error: unknown) {
  const value = error instanceof RemindersError ? error : new RemindersError('invalid_response');
  return { content: [{ type: 'text' as const, text: JSON.stringify({ error: value.code, message: value.message }) }],
    details: { error_code: value.code } as Details, isError: true };
}

/** Apple Reminders: one `reminders` tool. Registered only when enabled in Admin; one factory call = one Pi request. */
export function appleReminders(options: { settings: RemindersSettings; turn: number; transport?: RemindersTransport; store?: ConfirmationStore }) {
  return (pi: ExtensionAPI) => {
    if (!options.settings.reminders_enabled) return;
    const client = new RemindersClient({ settings: options.settings, transport: options.transport ?? osascriptTransport(),
      store: options.store ?? confirmations, turn: options.turn });
    pi.registerTool({
      name: 'reminders', label: 'Reminders', executionMode: 'sequential',
      annotations: { readOnlyHint: false, destructiveHint: true, openWorldHint: false },
      description: 'Apple Reminders. list gives refs for update/complete/delete; no list = default list. due: local YYYY-MM-DD[THH:MM], "none" clears. ' +
        'delete only asks: it returns confirm_token, valid after the user\'s next reply.',
      parameters: RemindersParams,
      async execute(_id, params, signal) {
        try {
          const result = await client.run(params, signal ?? new AbortController().signal);
          const count = Array.isArray(result.reminders) ? result.reminders.length : undefined;
          return { content: [{ type: 'text', text: resultText(result) }],
            details: { ...(count !== undefined ? { result_count: count } : {}),
              ...(typeof result.truncated === 'boolean' ? { truncated: result.truncated } : {}) } as Details };
        } catch (error) { return failure(error); }
      }
    });
  };
}

// Standard Pi CLI sessions read the same app config. A CLI session is one turn, so a deletion cannot be confirmed there.
export default async function (pi: ExtensionAPI) {
  appleReminders({ settings: await loadConfig(), turn: newTurn() })(pi);
}
