import type { ExtensionAPI } from '@earendil-works/pi-coding-agent';
import { Type } from 'typebox';
import type { MemoryItem, MemoryService } from '../../../src/memory.ts';

/** Model-facing schemas, kept tiny (sent only with a "hatırla / unut" request). */
const SaveParams = Type.Object({ kind: Type.Enum(['assistant', 'personal']), text: Type.String(), key: Type.Optional(Type.String()) }, { additionalProperties: false });
const ForgetParams = Type.Object({ query: Type.String() }, { additionalProperties: false });
export const MEMORY_TOOL_NAMES = ['memory_save', 'memory_forget'] as const;

const reply = (data: unknown, isError = false) => ({ content: [{ type: 'text' as const, text: JSON.stringify(data) }],
  details: (isError ? { error_code: (data as { error: string }).error } : {}) as { error_code?: string }, ...(isError ? { isError: true } : {}) });

/**
 * Explicit memory: `memory_save` / `memory_forget`, registered only for a user request that asks to remember/forget.
 * `local` false (remote model): personal texts never appear in results, only assistant rules.
 */
export function assistantMemory(options: { enabled: boolean; service?: MemoryService; local: boolean }) {
  return (pi: ExtensionAPI) => {
    if (!options.enabled || !options.service) return;
    const store = options.service.store;
    const view = (item: MemoryItem) => options.local || item.kind === 'assistant' ? { kind: item.kind, text: item.text } : { kind: item.kind };
    pi.registerTool({
      name: 'memory_save', label: 'Remember', executionMode: 'sequential',
      annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false },
      description: 'Save what the user asked to remember, one short sentence. assistant = how to work with the user; personal = fact about the user. key: short slot (e.g. reply.length) so a newer value replaces the old.',
      parameters: SaveParams,
      async execute(_id, params, signal) {
        const result = await store.save({ kind: params.kind as 'assistant' | 'personal', text: params.text, key: params.key, source: 'explicit' }, signal ?? new AbortController().signal);
        if (result.status === 'rejected') return reply({ error: result.reason, message: result.reason === 'private' ? 'Passwords, codes, account numbers and mail contents are never stored.' : 'Nothing saved.' }, true);
        return reply({ ok: true, status: result.status });
      }
    });
    pi.registerTool({
      name: 'memory_forget', label: 'Forget', executionMode: 'sequential',
      annotations: { readOnlyHint: false, destructiveHint: true, openWorldHint: false },
      description: 'Delete a saved memory matching the query. Returns deleted, or candidates to ask the user about.',
      parameters: ForgetParams,
      async execute(_id, params, signal) {
        const query = params.query.trim().slice(0, 200);
        if (!query) return reply({ error: 'invalid_request', message: 'query is empty.' }, true);
        const result = await store.forget(query, signal ?? new AbortController().signal);
        if (result.deleted) return reply({ deleted: view(result.deleted) });
        const candidates = result.candidates.map(view);
        return reply({ deleted: null, candidates, ...(candidates.some(c => !('text' in c)) ? { note: 'Personal items are listed in Admin > Hafıza or in Local mode.' } : {}) });
      }
    });
  };
}

// Standard Pi CLI sessions: the app's memory store is not available there.
export default function (_pi: ExtensionAPI) {}
