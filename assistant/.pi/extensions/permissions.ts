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

export function permissionsExtension(options: { root: string; offline: boolean }) {
  return (pi: ExtensionAPI) => {
    let calls = 0;
    // Read-only Apple Mail: per-request budgets inside the overall 12-call limit.
    const mailBudgets: Record<string, number> = { mail_list_messages: 2, mail_read_message: 5 };
    const mailCalls: Record<string, number> = {};
    const check = async (event: ToolCallEvent): Promise<ToolCallEventResult | undefined> => {
      if (++calls > 12) return { block: true, reason: 'Tool budget exhausted. Finish with the evidence already retrieved.', terminate: true };
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
      if (event.toolName.startsWith('mail_')) {
        if (!Object.hasOwn(mailBudgets, event.toolName))
          return { block: true, reason: 'This ability is not authorized. New abilities require user authorization through Pi.' };
        try {
          const permissions = JSON.parse(await readFile(path.join(options.root, '.pi/permissions.json'), 'utf8'));
          if (permissions?.mail_read !== 'allow') return { block: true, reason: 'Mail reading is disabled in Pi permissions. No permission has been granted.' };
        } catch { return { block: true, reason: 'Mail reading is disabled in Pi permissions. No permission has been granted.' }; }
        mailCalls[event.toolName] = (mailCalls[event.toolName] ?? 0) + 1;
        if (mailCalls[event.toolName] > mailBudgets[event.toolName])
          return { block: true, reason: event.toolName === 'mail_list_messages' ? 'Mail listing budget (2 per request) is exhausted. Use the messages already listed.'
            : 'Mail body budget (5 per request) is exhausted. Summarize from the bodies and metadata already retrieved.' };
        return; // Local, read-only: available in offline mode too.
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
    pi.on('user_bash', () => ({ result: { output: 'Shell execution is not an approved assistant ability.', exitCode: 1, cancelled: false, truncated: false } }));
  };
}

// Standard Pi CLI sessions are online unless launched with --offline / PI_OFFLINE.
export default function (pi: ExtensionAPI) {
  permissionsExtension({ root: process.cwd(), offline: ['1', 'true', 'yes'].includes(process.env.PI_OFFLINE || '') })(pi);
}
