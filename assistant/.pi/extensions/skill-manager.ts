import { loadSkillsFromDir, type ExtensionAPI } from '@earendil-works/pi-coding-agent';
import { Type } from 'typebox';
import { readdir, realpath } from 'node:fs/promises';
import path from 'node:path';

export default function skillManager(pi: ExtensionAPI) {
  pi.registerTool({
    name: 'test_skill', label: 'Validate skill',
    description: 'Validate an app-owned skill with the harness native skill parser. Checks discovery/frontmatter and lists supporting files. Does not execute scripts or prove task performance.',
    annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false },
    parameters: Type.Object({ name: Type.String({ minLength: 1, maxLength: 64, pattern: '^[a-z0-9]+(?:-[a-z0-9]+)*$' }) }),
    async execute(_id, params, signal, _update, ctx) {
      signal?.throwIfAborted();
      const base = await realpath(path.join(ctx.cwd, '.pi/skills'));
      const directory = await realpath(path.join(base, params.name));
      if (!directory.startsWith(base + path.sep)) throw Error('Skill must be inside the app skill directory');
      const result = loadSkillsFromDir({ dir: directory, source: 'ayhan' });
      const valid = result.skills.some(skill => skill.name === params.name) && !result.diagnostics.length;
      const files = await readdir(directory);
      return { content: [{ type: 'text', text: JSON.stringify({ valid, skills: result.skills.map(skill => ({ name: skill.name, description: skill.description })), diagnostics: result.diagnostics, files, scope: 'Static validation only. Saved skills are discovered on the next request.' }) }],
        details: { valid } };
    }
  });
}
