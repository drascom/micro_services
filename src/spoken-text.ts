/** Deterministic speech-only rendering. The displayed reply is never changed. */
export function spokenText(source: string): string {
  let text = source.replace(/```[^\n]*\n?[\s\S]*?(?:```|$)/g, '').replace(/~~~[^\n]*\n?[\s\S]*?(?:~~~|$)/g, '');
  // Keep link labels, omit destinations, including parentheses in URL paths.
  const opening = /!?\[([^\]\n]*)\]\(/g;
  let result = '', from = 0, match: RegExpExecArray | null;
  while ((match = opening.exec(text))) {
    let depth = 1, end = opening.lastIndex;
    for (; end < text.length && depth; end++) {
      if (text[end] === '\\') { end++; continue; }
      if (text[end] === '(') depth++;
      else if (text[end] === ')') depth--;
    }
    if (depth) continue;
    result += text.slice(from, match.index) + (match[0].startsWith('!') ? '' : match[1]);
    from = end; opening.lastIndex = end;
  }
  text = result + text.slice(from);
  text = text
    .replace(/<\/?(?:https?:\/\/|mailto:)[^>]*>/gi, '')
    .replace(/\b(?:https?:\/\/|ftp:\/\/|www\.)[^\s<>]+/gi, '')
    .replace(/\bmailto:[^\s<>]+/gi, '')
    .replace(/\b[\p{L}\p{N}._%+-]+@[\p{L}\p{N}.-]+\.[\p{L}]{2,}\b/gu, '')
    .replace(/\b(?:[\p{L}\p{N}][\p{L}\p{N}-]*\.)+(?:com|org|net|dev|io|ai|app|edu|gov|info|biz|co\.uk|uk|tr|de|fr)(?:\/[^\s<>]*)?\b/giu, '')
    .replace(/(?:^|\s)(?:\/(?:Users|home|tmp|var|opt|etc)\/[^\s]+|[A-Z]:\\[^\s]+)/g, ' ')
    .replace(/^\s*\[[^\]]+\]:\s*.*$/gm, '')
    .replace(/^\s*(?:#{1,6}\s+|[-*+]\s+|>\s*)/gm, '')
    .replace(/\[(?:\d+(?:\s*,\s*\d+)*)\]/g, '')
    .replace(/[()[\]<>]/g, '')
    .replace(/(?:^|\s)(?:source|sources|kaynak|kaynaklar|link|bağlantı)\s*:\s*$/gmi, '')
    .replace(/[*_`]/g, '');
  return text.split(/\r?\n/).map(line => line.trim())
    .filter(line => line && !/^(?:source|sources|kaynak|kaynaklar|link|bağlantı)\s*:?[\s.,;:]*$/i.test(line))
    .join('\n').replace(/[ \t]+/g, ' ').trim();
}
