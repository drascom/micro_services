import type { ExtensionAPI } from '@earendil-works/pi-coding-agent';
import { Type } from 'typebox';
import type { ConfirmationStore } from '../apple-reminders/confirmations.ts';
import { ScheduleError, taskConfirmations, type Preview, type TaskService } from '../../../src/tasks.ts';

/** Model-facing schema, kept tiny. */
const Params = Type.Object({
  action: Type.Enum(['create', 'list', 'update', 'pause', 'resume', 'run', 'remove']),
  name: Type.Optional(Type.String()), prompt: Type.Optional(Type.String()), schedule: Type.Optional(Type.String()), confirm_token: Type.Optional(Type.String())
}, { additionalProperties: false });
export const TASK_TOOL_NAMES = ['tasks'] as const;

const reply = (data: unknown, isError = false) => ({ content: [{ type: 'text' as const, text: JSON.stringify(data) }],
  details: (isError ? { error_code: (data as { error: string }).error } : {}) as { error_code?: string }, ...(isError ? { isError: true } : {}) });
const failure = (error: unknown) => reply({ error: error instanceof ScheduleError ? 'invalid_schedule' : 'invalid_request',
  message: error instanceof Error ? error.message.slice(0, 400) : 'failed' }, true);

/**
 * Scheduled assistant tasks (TASKS.md). `create` only parses and returns the schedule + a confirm_token (the user
 * answers in the next turn); the task is saved when called again with it. A user request only (never inside a task).
 */
export function assistantTasks(options: { enabled: boolean; service?: TaskService; turn: number; store?: ConfirmationStore }) {
  return (pi: ExtensionAPI) => {
    if (!options.enabled || !options.service) return;
    const service = options.service, store = options.store ?? taskConfirmations;
    pi.registerTool({
      name: 'tasks', label: 'Assistant tasks', executionMode: 'sequential',
      annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false },
      description: "Scheduled assistant tasks (görev) run later without the user. schedule: user's words, e.g. 'her gün 09:00', 'hafta içi 9:00', 'every 2h', 'in 30m', ISO. prompt: self-contained. name: short or existing task.",
      parameters: Params,
      async execute(_id, params) {
        try {
          if (params.action === 'list') return reply({ tasks: service.list().map(({ id: _, ...t }) => t) });
          if (params.action === 'create') {
            if (params.confirm_token !== undefined) {
              const target = store.consume(params.confirm_token.slice(0, 40), options.turn);
              if (typeof target === 'string') return reply({ error: target }, true);
              const preview = JSON.parse(target.ref) as Preview;
              const task = service.create(service.preview({ name: preview.name, prompt: preview.prompt, schedule: preview.schedule_text }));
              return reply({ saved: task.name, schedule: service.view(task).schedule, next: service.view(task).next });
            }
            const preview = service.preview(params);
            const token = store.issue(options.turn, { id: 'tasks', title: `${preview.name}: ${preview.text}`,
              ref: JSON.stringify({ name: preview.name, prompt: preview.prompt, schedule_text: preview.schedule_text }) });
            return reply({ name: preview.name, schedule: preview.text, confirm_token: token, next: 'Say the schedule, ask to save, stop.' });
          }
          const found = service.find(params.name);
          if ('error' in found) return reply(found, true);
          if (params.action === 'update') { const task = service.update(found, params); return reply({ updated: task.name, ...service.view(task) }); }
          if (params.action === 'pause') return reply({ paused: service.pause(found).name });
          if (params.action === 'resume') { const task = service.resume(found); return reply({ resumed: task.name, next: service.view(task).next }); }
          if (params.action === 'run') return reply({ started: service.runNow(found), next: 'It runs after this reply; the result is announced.' });
          return reply({ removed: service.remove(found) ? found.name : null });
        } catch (error) { return failure(error); }
      }
    });
  };
}

// Standard Pi CLI sessions: tasks need the running app's store, so nothing is registered.
export default function (_pi: ExtensionAPI) {}
