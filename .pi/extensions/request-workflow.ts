import type { ExtensionAPI } from '@earendil-works/pi-coding-agent';
import { activity } from '../../src/activity.ts';

export function requestWorkflow(options: { skill?: string; briefNews?: boolean }) {
  return (pi: ExtensionAPI) => {
    let searches = 0;
    pi.on('before_agent_start', event => {
      let rules = '';
      if (options.skill) rules += `\nThe ${options.skill} skill is ALREADY supplied in the user message as a <skill> block. Use it directly; do not reread its SKILL.md unless the user explicitly asks to inspect/edit that file.\n`;
      if (options.briefNews) rules += 'This is a brief current-news request. Use one focused web_search with recencyFilter day, numResults 3 and workflow none. Include the place/topic and latest news in the query, in the user language or English when useful. Then give up to three dated headlines with original source links, at most 80 words in total. Report limited coverage honestly. Do not read pages or expand research unless requested.\n';
      return rules ? { systemPrompt: event.systemPrompt + rules } : undefined;
    });
    pi.on('tool_call', event => {
      if (!options.briefNews || event.toolName !== 'web_search') return;
      if (searches++) return { block: true, reason: 'The brief news search is already complete. Summarize the retrieved evidence or report limited coverage.' };
      event.input.recencyFilter = 'day';
      event.input.numResults = 3;
      event.input.workflow = 'none';
      activity('news_route', { recency: 'day' });
    });
  };
}

export default function (pi: ExtensionAPI) { requestWorkflow({})(pi); }
