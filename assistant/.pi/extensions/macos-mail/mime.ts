// Minimal, dependency-free MIME reader for Apple Mail `.emlx` files: multipart, quoted-printable,
// base64 and charsets; prefers text/plain, otherwise converts text/html to plain text. Attachments
// are never decoded. Output is plain text only (no links are opened, no content is executed).

const MAX_DEPTH = 8, MAX_PARTS = 200;

/** `.emlx` = "<byte count>\n" + RFC 822 message + Apple plist trailer. Returns the RFC 822 bytes. */
export function emlxMessage(file: Buffer): Buffer {
  const newline = file.indexOf(0x0a);
  const length = newline > 0 && newline <= 20 ? Number(file.subarray(0, newline).toString('latin1').trim()) : NaN;
  if (!Number.isSafeInteger(length) || length < 0) return file;
  return file.subarray(newline + 1, Math.min(file.length, newline + 1 + length));
}

type Headers = Map<string, string>;
type Part = { headers: Headers; body: Buffer };

function split(raw: Buffer): Part {
  let end = raw.indexOf('\r\n\r\n'), skip = 4;
  const lf = raw.indexOf('\n\n');
  if (lf >= 0 && (end < 0 || lf < end)) { end = lf; skip = 2; }
  if (end < 0) { end = raw.length; skip = 0; }
  const headers: Headers = new Map();
  const text = raw.subarray(0, end).toString('latin1').replace(/\r?\n[ \t]+/g, ' ');
  for (const line of text.split(/\r?\n/)) {
    const colon = line.indexOf(':');
    if (colon > 0) { const name = line.slice(0, colon).trim().toLowerCase(); if (!headers.has(name)) headers.set(name, line.slice(colon + 1).trim()); }
  }
  return { headers, body: raw.subarray(Math.min(raw.length, end + skip)) };
}

/** `type/subtype; a=b; c="d"` → value + lower-case parameter map. */
export function headerParams(value: string): { value: string; params: Map<string, string> } {
  const params = new Map<string, string>();
  const [first, ...rest] = value.match(/(?:[^;"]|"(?:[^"\\]|\\.)*")+/g) ?? [''];
  for (const item of rest) {
    const eq = item.indexOf('=');
    if (eq > 0) params.set(item.slice(0, eq).trim().toLowerCase().replace(/\*$/, ''),
      item.slice(eq + 1).trim().replace(/^"(.*)"$/s, '$1').replace(/\\(.)/g, '$1').replace(/^[\w-]+'[\w-]*'/, ''));
  }
  return { value: first.trim().toLowerCase(), params };
}

export function decodeQuotedPrintable(text: string): Buffer {
  const soft = text.replace(/=\r?\n/g, '');
  const bytes: number[] = [];
  for (let i = 0; i < soft.length; i++) {
    const c = soft.charCodeAt(i);
    if (c === 0x3d && /^[0-9A-Fa-f]{2}$/.test(soft.slice(i + 1, i + 3))) { bytes.push(parseInt(soft.slice(i + 1, i + 3), 16)); i += 2; }
    else bytes.push(c & 0xff);
  }
  return Buffer.from(bytes);
}

export function decodeCharset(bytes: Buffer, charset = 'utf-8'): string {
  const name = charset.trim().toLowerCase().replace(/^(x-|cs)/, '') || 'utf-8';
  for (const label of [name, name === 'ascii' || name === 'us-ascii' ? 'utf-8' : '']) {
    if (!label) continue;
    try { return new TextDecoder(label).decode(bytes); } catch { /* unknown label: try the next fallback */ }
  }
  return new TextDecoder('windows-1252').decode(bytes);
}

function decodeBody(part: Part): string {
  const encoding = (part.headers.get('content-transfer-encoding') ?? '').trim().toLowerCase();
  const charset = headerParams(part.headers.get('content-type') ?? '').params.get('charset');
  let bytes = part.body;
  if (encoding === 'base64') bytes = Buffer.from(bytes.toString('latin1').replace(/[^A-Za-z0-9+/=]/g, ''), 'base64');
  else if (encoding === 'quoted-printable') bytes = decodeQuotedPrintable(bytes.toString('latin1'));
  return decodeCharset(bytes, charset);
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

function walk(raw: Buffer, depth: number, found: Found): void {
  if (depth > MAX_DEPTH || ++found.parts > MAX_PARTS) return;
  const part = split(raw);
  const type = headerParams(part.headers.get('content-type') ?? 'text/plain');
  const disposition = headerParams(part.headers.get('content-disposition') ?? '').value;
  if (type.value.startsWith('multipart/')) {
    const boundary = type.params.get('boundary');
    if (!boundary) return;
    const marker = Buffer.from('--' + boundary, 'latin1');
    const pieces: Buffer[] = [];
    let start = part.body.indexOf(marker);
    while (start >= 0) {
      const after = start + marker.length;
      if (part.body.subarray(after, after + 2).toString('latin1') === '--') break;
      let bodyStart = part.body.indexOf('\n', after);
      if (bodyStart < 0) break;
      bodyStart++;
      const next = part.body.indexOf(marker, bodyStart);
      let bodyEnd = next < 0 ? part.body.length : next;
      if (next >= 0 && part.body[bodyEnd - 1] === 0x0a) bodyEnd -= part.body[bodyEnd - 2] === 0x0d ? 2 : 1;
      pieces.push(part.body.subarray(bodyStart, Math.max(bodyStart, bodyEnd)));
      start = next;
    }
    // multipart/alternative lists the richest form last; any order works since plain text wins overall.
    for (const piece of pieces) walk(piece, depth + 1, found);
    return;
  }
  if (type.value === 'message/rfc822' && disposition !== 'attachment') { walk(part.body, depth + 1, found); return; }
  if (disposition === 'attachment') return;
  if (type.value === 'text/plain' || type.value === '') {
    // Some senders put HTML (or HTML entities) into the text/plain part.
    const text = decodeBody(part);
    found.plain.push(/<(html|body|div|table|p|br)\b/i.test(text) ? htmlToText(text) : decodeEntities(text));
  }
  else if (type.value === 'text/html') found.html.push(htmlToText(decodeBody(part)));
}

/** Plain-text body of an RFC 822 message ('' when no readable text part is present). */
export function messageText(raw: Buffer): string {
  const found: Found = { plain: [], html: [], parts: 0 };
  walk(raw, 0, found);
  const plain = found.plain.join('\n\n').trim();
  return plain || found.html.join('\n\n').trim();
}
