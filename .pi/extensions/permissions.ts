import type { ExtensionAPI, ToolCallEvent, ToolCallEventResult } from '@earendil-works/pi-coding-agent';
import { activity } from '../../src/activity.ts';
import { readFile, realpath } from 'node:fs/promises';
import path from 'node:path';

async function within(directory: string, filename: string, allowMissing = false): Promise<boolean> {
  const base = await realpath(directory);
  let target = path.resolve(filename);
  if (!target.startsWith(path.resolve(directory) + path.sep) && !target.startsWith(base + path.sep)) return false;
  while (true) {
    try {
      const resolved = await realpath(target);
      return resolved === base || resolved.startsWith(base + path.sep);
    } catch (error) {
      if (!allowMissing || (error as NodeJS.ErrnoException).code !== 'ENOENT') return false;
      const parent = path.dirname(target);
      if (parent === target) return false;
      target = parent;
    }
  }
}

/** Same list as TASK_TOOLS in src/harness.ts (kept here so Pi CLI sessions load without the app). */
const TASK_ALLOWED = /^(read|web_search|web_read|fetch_content|mail_[a-z_]+|reminders|file_search|file_read)$/;

/** `local`: the session runs on the local model (the only place the password vault and local files may be used). */
export function permissionsExtension(options: { root: string; offline: boolean; automated?: boolean; local?: boolean }) {
  return (pi: ExtensionAPI) => {
    let calls = 0;
    // Read-only mail (Gmail): per-request budgets inside the overall 12-call limit. Only successful calls count (a rejected
    // date or filter returns no data); 3 listings leave room for one narrower retry while results stay small for local models.
    const mailBudgets: Record<string, number> = { mail_list_messages: 3, mail_read_message: 5 };
    const mailCalls: Record<string, number> = {};
    let reminderCalls = 0, organizeCalls = 0, fileCalls = 0, fileOrganizeCalls = 0, vaultCalls = 0, updateCalls = 0, memoryCalls = 0, taskCalls = 0;
    const check = async (event: ToolCallEvent): Promise<ToolCallEventResult | undefined> => {
      if (++calls > 12) return { block: true, reason: 'Tool budget exhausted. Finish with the evidence already retrieved.', terminate: true };
      // Unattended task run: no user can confirm, so read-only tools only (reminders: list; mail_organize: propose, below).
      if (options.automated) {
        if (!TASK_ALLOWED.test(event.toolName)) return { block: true, reason: 'Not available in automated tasks.' };
        if (event.toolName === 'reminders' && (event.input as { action?: string }).action !== 'list')
          return { block: true, reason: 'Only listing reminders is available in automated tasks.' };
      }
      if (event.toolName === 'tasks') {
        // Assistant tasks: user requests only (no task creation from inside a task); creation is gated by the next-turn token.
        if (options.automated) return { block: true, reason: 'Not available in automated tasks.' };
        if (++taskCalls > 4) return { block: true, reason: 'Tasks budget (4 per request) is exhausted.' };
        return;
      }
      if (event.toolName.startsWith('vault_')) {
        // Defence in depth: the harness registers these only for the local model; secrets never reach a remote model.
        if (!options.local || options.automated) return { block: true, reason: 'The password vault works only with the local model.' };
        if (!['vault_search', 'vault_copy', 'vault_save'].includes(event.toolName)) return { block: true, reason: 'Not available.' };
        if (++vaultCalls > 4) return { block: true, reason: 'Vault budget (4 per request) is exhausted.' };
        return;
      }
      if (event.toolName === 'memory_save' || event.toolName === 'memory_forget') {
        // Assistant memory: user requests only (a Reminders task never reads or writes memory).
        if (options.automated) return { block: true, reason: 'Not available in automated tasks.' };
        if (++memoryCalls > 4) return { block: true, reason: 'Memory budget (4 per request) is exhausted.' };
        return;
      }
      if (event.toolName === 'updates') {
        // Component updates: user requests only; apply/rollback are additionally gated by the extension's next-turn token.
        if (options.automated) return { block: true, reason: 'Not available in automated tasks.' };
        try {
          const permissions = JSON.parse(await readFile(path.join(options.root, '.pi/permissions.json'), 'utf8'));
          if (permissions?.updates === 'deny') return { block: true, reason: 'Updates are disabled in Pi permissions.' };
        } catch {}
        if (++updateCalls > 3) return { block: true, reason: 'Updates budget (3 per request) is exhausted.' };
        return;
      }
      if (event.toolName === 'read') {
        try {
          const input = event.input as { path?: string };
          const target = path.resolve(options.root, input.path || '');
          for (const name of ['skills', 'extensions']) {
            if (await within(path.join(options.root, '.pi', name), target)) return;
          }
          if (await within(path.join(options.root, 'node_modules/pi-web-access'), target)) return;
        } catch {}
        return { block: true, reason: 'Only app-owned skill and extension files may be read.' };
      }
      if (['write', 'edit'].includes(event.toolName)) {
        const input = event.input as { path?: string };
        try {
          const permissions = JSON.parse(await readFile(path.join(options.root, '.pi/permissions.json'), 'utf8'));
          if (permissions?.skill_edit === 'allow' && typeof input.path === 'string' &&
              await within(path.join(options.root, '.pi/skills'), path.resolve(options.root, input.path), event.toolName === 'write')) return;
        } catch {}
        return { block: true, reason: 'Writing is authorized only inside app-owned skills. Extension code and permission files cannot be modified.' };
      }
      if (event.toolName === 'test_skill') {
        try {
          const permissions = JSON.parse(await readFile(path.join(options.root, '.pi/permissions.json'), 'utf8'));
          if (permissions?.skill_edit === 'allow') return;
        } catch {}
        return { block: true, reason: 'Skill management permission is disabled.' };
      }
      if (event.toolName === 'mail_organize') {
        // Gmail triage: apply/purge are additionally gated by the extension's next-turn token. A task may only propose.
        if (options.automated && (event.input as { action?: string }).action !== 'propose')
          return { block: true, reason: 'Automated tasks may only propose a mail plan.' };
        if (options.offline) return { block: true, reason: 'Offline mode: Gmail is unavailable.' };
        try {
          const permissions = JSON.parse(await readFile(path.join(options.root, '.pi/permissions.json'), 'utf8'));
          if (permissions?.mail_organize !== 'allow') return { block: true, reason: 'Mail organizing is disabled in Pi permissions.' };
        } catch { return { block: true, reason: 'Mail organizing is disabled in Pi permissions.' }; }
        if (++organizeCalls > 3) return { block: true, reason: 'Mail organize budget (3 per request) is exhausted. Report what was done.' };
        return;
      }
      if (event.toolName.startsWith('mail_')) {
        if (!Object.hasOwn(mailBudgets, event.toolName))
          return { block: true, reason: 'This ability is not authorized. New abilities require user authorization through Pi.' };
        if (options.offline) return { block: true, reason: 'Offline mode: Gmail is unavailable.' };
        try {
          const permissions = JSON.parse(await readFile(path.join(options.root, '.pi/permissions.json'), 'utf8'));
          if (permissions?.mail_read !== 'allow') return { block: true, reason: 'Mail reading is disabled in Pi permissions. No permission has been granted.' };
        } catch { return { block: true, reason: 'Mail reading is disabled in Pi permissions. No permission has been granted.' }; }
        if ((mailCalls[event.toolName] ?? 0) >= mailBudgets[event.toolName])
          return { block: true, reason: event.toolName === 'mail_list_messages' ? 'Mail listing budget (3 per request) is exhausted. Use the messages already listed.'
            : 'Mail body budget (5 per request) is exhausted. Summarize from the bodies and metadata already retrieved.' };
        return;
      }
      if (['file_search', 'file_read', 'file_organize'].includes(event.toolName)) {
        // Local files: on by default once the index is enabled in Admin; an explicit "deny" turns them off. Moves are
        // additionally gated by the extension's next-turn token; a task may not move files. Available offline.
        // Defence in depth: the harness registers them only for the local model; file contents never reach a remote model.
        if (!options.local) return { block: true, reason: 'Local files work only with the local model.' };
        try {
          const permissions = JSON.parse(await readFile(path.join(options.root, '.pi/permissions.json'), 'utf8'));
          if (permissions?.files === 'deny') return { block: true, reason: 'Local files are disabled in Pi permissions.' };
        } catch {}
        if (event.toolName === 'file_organize') {
          if (options.automated) return { block: true, reason: 'Automated tasks cannot organize files.' };
          if (++fileOrganizeCalls > 3) return { block: true, reason: 'File organize budget (3 per request) is exhausted. Report what was done.' };
          return;
        }
        if (++fileCalls > 8) return { block: true, reason: 'File budget (8 per request) is exhausted. Answer from what was found.' };
        return;
      }
      if (event.toolName === 'reminders') {
        // Full access by default (list/add/update/complete); only an explicit "deny" turns it off. Deletion is
        // additionally gated by the extension's next-turn confirmation token. Local: available offline.
        try {
          const permissions = JSON.parse(await readFile(path.join(options.root, '.pi/permissions.json'), 'utf8'));
          if (permissions?.reminders === 'deny') return { block: true, reason: 'Reminders is disabled in Pi permissions.' };
        } catch {}
        if (++reminderCalls > 8) return { block: true, reason: 'Reminders budget (8 per request) is exhausted. Finish with what is done.' };
        return;
      }
      if (!['web_search', 'web_read', 'source_check', 'fetch_content', 'get_search_content', 'web_enable'].includes(event.toolName)) {
        return { block: true, reason: 'This ability is not authorized. New abilities require user authorization through Pi.' };
      }
      if (options.offline) return { block: true, reason: 'Offline mode: live web access is unavailable.' };
      try {
        const permissions = JSON.parse(await readFile(path.join(options.root, '.pi/permissions.json'), 'utf8'));
        if (permissions?.[event.toolName] === 'allow') return;
      } catch {}
      return { block: true, reason: 'This web ability is disabled in Pi permissions. No permission has been granted.' };
    };
    pi.on('tool_call', async event => {
      const decision = await check(event);
      activity('permission_checked', { tool: event.toolName, allowed: !decision?.block,
        ...(decision?.reason ? { reason: decision.reason } : {}) });
      return decision;
    });
    pi.on('tool_result', event => {
      if (!event.isError && Object.hasOwn(mailBudgets, event.toolName)) mailCalls[event.toolName] = (mailCalls[event.toolName] ?? 0) + 1;
    });
    pi.on('user_bash', () => ({ result: { output: 'Shell execution is not an approved assistant ability.', exitCode: 1, cancelled: false, truncated: false } }));
  };
}

// Standard Pi CLI sessions are online unless launched with --offline / PI_OFFLINE.
export default function (pi: ExtensionAPI) {
  permissionsExtension({ root: process.cwd(), offline: ['1', 'true', 'yes'].includes(process.env.PI_OFFLINE || '') })(pi);
}
