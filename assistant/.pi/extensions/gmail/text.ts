// Plain text of a Gmail `format=full` payload: Gmail has already split the MIME tree and removed the transfer
// encoding (part bodies are base64url bytes in the part's charset). Prefers text/plain, otherwise converts text/html.
// Attachments are never decoded or fetched. Output is plain text only (no links are opened, no content is executed).

const MAX_DEPTH = 8, MAX_PARTS = 200;

/** The subset of Gmail's MessagePart this reader uses. */
export type Part = { mimeType?: string; filename?: string; headers?: { name?: string; value?: string }[];
  body?: { data?: string; attachmentId?: string }; parts?: Part[] };

/** `charset` parameter of a Content-Type value (lower case), if any. */
export function charsetOf(contentType: string): string | undefined {
  return /;\s*charset\s*=\s*"?([^";\s]+)"?/i.exec(contentType)?.[1]?.toLowerCase();
}

export function decodeCharset(bytes: Buffer, charset = 'utf-8'): string {
  const name = charset.trim().toLowerCase().replace(/^(x-|cs)/, '') || 'utf-8';
  for (const label of [name, name === 'ascii' || name === 'us-ascii' ? 'utf-8' : '']) {
    if (!label) continue;
    try { return new TextDecoder(label).decode(bytes); } catch { /* unknown label: try the next fallback */ }
  }
  return new TextDecoder('windows-1252').decode(bytes);
}

const ENTITIES: Record<string, string> = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', ndash: '–', mdash: '—',
  hellip: '…', laquo: '«', raquo: '»', lsquo: '‘', rsquo: '’', ldquo: '“', rdquo: '”', bull: '•', middot: '·', copy: '©',
  reg: '®', trade: '™', euro: '€', pound: '£', ccedil: 'ç', Ccedil: 'Ç', ouml: 'ö', Ouml: 'Ö', uuml: 'ü', Uuml: 'Ü',
  auml: 'ä', Auml: 'Ä', szlig: 'ß', eacute: 'é', egrave: 'è', aacute: 'á', iacute: 'í', oacute: 'ó', uacute: 'ú',
  zwnj: '', zwj: '', shy: '', rarr: '→', larr: '←', deg: '°', times: '×', thinsp: ' ', ensp: ' ', emsp: ' ' };

export function decodeEntities(text: string): string {
  return text.replace(/&(#x[0-9a-f]{1,6}|#\d{1,7}|[a-z]{2,8});/gi, (match, name: string) => {
    if (name[0] === '#') {
      const code = name[1] === 'x' || name[1] === 'X' ? parseInt(name.slice(2), 16) : parseInt(name.slice(1), 10);
      return code > 0 && code <= 0x10ffff ? String.fromCodePoint(code) : '';
    }
    return ENTITIES[name] ?? match;
  });
}

/** Readable plain text from HTML: drops head/script/style/comments, keeps block structure as line breaks. */
export function htmlToText(html: string): string {
  return decodeEntities(html
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/<(head|script|style|title|noscript|template|svg)\b[\s\S]*?<\/\1\s*>/gi, '')
    .replace(/<br\b[^>]*>/gi, '\n')
    .replace(/<\/?(p|div|tr|li|ul|ol|table|h[1-6]|blockquote|section|article|header|footer|pre|hr)\b[^>]*>/gi, '\n')
    .replace(/<\/t[dh]\s*>/gi, ' ')
    .replace(/<[^>]*>/g, ''))
    .replace(/[ \t]+\n/g, '\n');
}

type Found = { plain: string[]; html: string[]; parts: number };

function walk(part: Part | undefined, depth: number, found: Found): void {
  if (!part || typeof part !== 'object' || depth > MAX_DEPTH || ++found.parts > MAX_PARTS) return;
  const type = (part.mimeType ?? '').toLowerCase();
  if (Array.isArray(part.parts) && (type.startsWith('multipart/') || type === 'message/rfc822' || !type)) {
    for (const child of part.parts) walk(child, depth + 1, found);
    return;
  }
  // An attachment (named or stored separately) is never read.
  const header = (name: string) => (Array.isArray(part.headers) ? part.headers : []).find(h => h?.name?.toLowerCase() === name)?.value ?? '';
  if (part.filename || part.body?.attachmentId || /^\s*attachment/i.test(header('content-disposition'))) return;
  if (typeof part.body?.data !== 'string' || (type !== 'text/plain' && type !== 'text/html')) return;
  const text = decodeCharset(Buffer.from(part.body.data, 'base64url'), charsetOf(header('content-type')));
  // Some senders put HTML (or HTML entities) into the text/plain part.
  if (type === 'text/plain') found.plain.push(/<(html|body|div|table|p|br)\b/i.test(text) ? htmlToText(text) : decodeEntities(text));
  else found.html.push(htmlToText(text));
}

/** Plain-text body of a Gmail message payload ('' when no readable text part is present). */
export function messageText(payload: Part | undefined): string {
  const found: Found = { plain: [], html: [], parts: 0 };
  walk(payload, 0, found);
  const plain = found.plain.join('\n\n').trim();
  return plain || found.html.join('\n\n').trim();
}
