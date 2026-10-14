import type { ExtensionAPI } from '@earendil-works/pi-coding-agent';
import { Type } from 'typebox';
import { VaultError, type SaveSource, type VaultService } from '../../../src/vault.ts';
import { ConfirmationStore } from '../apple-reminders/confirmations.ts';

/** Model-facing schemas, kept tiny. Read-only vault: no create, edit or delete. */
const SearchParams = Type.Object({ query: Type.String() }, { additionalProperties: false });
const CopyParams = Type.Object({ id: Type.String(), field: Type.Enum(['password', 'username', 'totp']) }, { additionalProperties: false });
// No value parameter on purpose: a secret can never pass through the model.
const SaveParams = Type.Object({
  name: Type.String(), source: Type.Enum(['clipboard', 'prompt', 'generate']), kind: Type.Optional(Type.Enum(['login', 'token'])),
  username: Type.Optional(Type.String()), uri: Type.Optional(Type.String()), length: Type.Optional(Type.Integer()), confirm_token: Type.Optional(Type.String())
}, { additionalProperties: false });
export const VAULT_TOOL_NAMES = ['vault_search', 'vault_copy', 'vault_save'] as const;
/** Pending "replace the password of an existing item" (next-turn token, as for reminders/Gmail); shared per backend. */
export const vaultConfirmations = new ConfirmationStore();
const clean = (value: unknown, max: number) => typeof value === 'string' && !/[\u0000-\u001f\u007f]/.test(value) ? value.trim().slice(0, max) : '';

type Details = { error_code?: string; result_count?: number };
const failure = (error: unknown) => {
  const code = error instanceof VaultError ? error.code : 'vault_failed';
  // Messages are this extension's own fixed texts; never a vault value.
  const message = error instanceof VaultError && error.message !== code ? error.message : undefined;
  return { content: [{ type: 'text' as const, text: JSON.stringify({ error: code, ...(message ? { message } : {}) }) }], details: { error_code: code } as Details, isError: true };
};

/**
 * Bitwarden vault (local model only): `vault_search` returns names/usernames/hosts/ids, never a secret; `vault_copy`
 * puts a value on the clipboard in the backend and returns only `{copied, field, item}`. Registered only when the
 * harness allows it (local model, switch on, unlocked).
 */
export function bitwardenVault(options: { enabled: boolean; service?: VaultService; turn?: number; store?: ConfirmationStore }) {
  return (pi: ExtensionAPI) => {
    if (!options.enabled || !options.service) return;
    const service = options.service;
    pi.registerTool({
      name: 'vault_search', label: 'Search vault', executionMode: 'sequential',
      annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false },
      description: 'Find password vault items by name or site. No secrets returned.',
      parameters: SearchParams,
      async execute(_id, params, signal) {
        try {
          const results = await service.search(params.query, signal ?? new AbortController().signal);
          return { content: [{ type: 'text', text: JSON.stringify({ results }) }], details: { result_count: results.length } as Details };
        } catch (error) { return failure(error); }
      }
    });
    pi.registerTool({
      name: 'vault_copy', label: 'Copy from vault', executionMode: 'sequential',
      annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false },
      description: 'Copy an item field to the clipboard (cleared after 30 s). The value is never shown.',
      parameters: CopyParams,
      async execute(_id, params, signal) {
        try {
          const { item } = await service.copy(params.id, params.field as 'password' | 'username' | 'totp', signal ?? new AbortController().signal);
          return { content: [{ type: 'text', text: JSON.stringify({ copied: true, field: params.field, item }) }], details: {} as Details };
        } catch (error) { return failure(error); }
      }
    });
    const store = options.store ?? vaultConfirmations, turn = options.turn ?? 0;
    pi.registerTool({
      name: 'vault_save', label: 'Save to vault', executionMode: 'sequential',
      annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false },
      description: 'Save a secret under name; the value comes from clipboard, secure prompt or generate (copied), never from you. Existing name: confirm_token, then call again with it after the user agrees.',
      parameters: SaveParams,
      async execute(_id, params, signal) {
        const live = signal ?? new AbortController().signal;
        try {
          const kind = params.kind === 'token' ? 'token' : 'login';
          if (params.confirm_token !== undefined) {
            const target = store.consume(params.confirm_token.slice(0, 40), turn);
            if (typeof target === 'string') throw new VaultError('invalid_request', target);
            const saved = JSON.parse(target.ref) as { source: SaveSource; length?: number; kind: string };
            const { item } = await service.save({ id: target.id, name: target.title, source: saved.source, length: saved.length }, live);
            return { content: [{ type: 'text', text: JSON.stringify({ saved: true, updated: true, item, kind: saved.kind }) }], details: {} as Details };
          }
          const name = clean(params.name, 100);
          if (!name) throw new VaultError('invalid_request', 'name is required');
          const existing = await service.findByName(name, live);
          if (existing) {
            const token = store.issue(turn, { id: existing.id, title: existing.name, ref: JSON.stringify({ source: params.source, length: params.length, kind }) });
            return { content: [{ type: 'text', text: JSON.stringify({ exists: true, item: existing.name, confirm_token: token,
              next: 'Ask if the password should be replaced (old one kept in history) and stop.' }) }], details: {} as Details };
          }
          const { item } = await service.save({ name, source: params.source as SaveSource, length: params.length,
            username: kind === 'login' ? clean(params.username, 200) : '', uri: clean(params.uri, 500) }, live);
          return { content: [{ type: 'text', text: JSON.stringify({ saved: true, item, kind }) }], details: {} as Details };
        } catch (error) { return failure(error); }
      }
    });
  };
}

// Standard Pi CLI sessions: the vault needs the app's in-memory unlock and the local model, so nothing is registered.
export default function (_pi: ExtensionAPI) {}
