import { access, open, readFile } from 'node:fs/promises';
import { constants } from 'node:fs';
import { inflateRawSync } from 'node:zlib';
import path from 'node:path';
import { runProcess } from './processes.ts';

/** Content extraction for the file index. No heavy dependencies; pdftotext and ffmpeg are optional (PATH or Homebrew). */

export type FileKind = 'text' | 'pdf' | 'office' | 'image' | 'audio' | 'video' | 'other';
const TEXT = new Set(['txt', 'md', 'markdown', 'csv', 'tsv', 'json', 'jsonl', 'html', 'htm', 'xml', 'yaml', 'yml', 'log', 'ini', 'toml', 'cfg', 'conf',
  'rst', 'tex', 'srt', 'vtt', 'rtf', 'ts', 'tsx', 'js', 'jsx', 'mjs', 'cjs', 'py', 'swift', 'go', 'rs', 'java', 'kt', 'c', 'h', 'cc', 'cpp', 'hpp', 'm',
  'mm', 'cs', 'rb', 'php', 'sh', 'zsh', 'bash', 'fish', 'sql', 'css', 'scss', 'less', 'vue', 'svelte', 'lua', 'r', 'pl', 'dart', 'scala', 'ex', 'exs', 'env.example']);
const OFFICE = new Set(['docx', 'xlsx', 'pptx', 'odt', 'ods', 'odp']);
const IMAGE = new Set(['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'heic', 'heif', 'tif', 'tiff']);
const AUDIO = new Set(['wav', 'mp3', 'm4a', 'aac', 'flac', 'ogg', 'opus', 'aiff', 'aif', 'wma']);
const VIDEO = new Set(['mp4', 'mov', 'm4v', 'mkv', 'avi', 'webm', 'mpg', 'mpeg', '3gp']);
export function kindOf(file: string): FileKind {
  const ext = path.extname(file).slice(1).toLowerCase();
  if (TEXT.has(ext)) return 'text';
  if (ext === 'pdf') return 'pdf';
  if (OFFICE.has(ext)) return 'office';
  if (IMAGE.has(ext)) return 'image';
  if (AUDIO.has(ext)) return 'audio';
  if (VIDEO.has(ext)) return 'video';
  return 'other';
}

const SEARCH_DIRS = ['/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', '/bin'];
const found = new Map<string, Promise<string | undefined>>();
/** An optional helper binary from PATH or the usual install locations (a Finder-launched app has a short PATH). */
export function tool(name: string): Promise<string | undefined> {
  let value = found.get(name);
  if (!value) {
    value = (async () => {
      for (const dir of [...(process.env.PATH ?? '').split(path.delimiter).filter(Boolean), ...SEARCH_DIRS]) {
        const candidate = path.join(dir, name);
        try { await access(candidate, constants.X_OK); return candidate; } catch {}
      }
      return undefined;
    })();
    found.set(name, value);
  }
  return value;
}

export const decodeEntities = (text: string) => text.replace(/&(#x?[0-9a-f]+|amp|lt|gt|quot|apos|nbsp);/gi, (_, e: string) => {
  const lower = e.toLowerCase();
  if (lower[0] === '#') { const code = lower[1] === 'x' ? parseInt(lower.slice(2), 16) : parseInt(lower.slice(1), 10); return Number.isFinite(code) && code < 0x110000 ? String.fromCodePoint(code) : ' '; }
  return ({ amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ' } as Record<string, string>)[lower] ?? ' ';
});
export const htmlText = (html: string) => decodeEntities(html.replace(/<(script|style|noscript)[\s\S]*?<\/\1>/gi, ' ')
  .replace(/<(br|\/p|\/div|\/li|\/h\d|\/tr)\b[^>]*>/gi, '\n').replace(/<[^>]+>/g, ' ')).replace(/[ \t\r\f\v]+/g, ' ').replace(/ ?\n[\s]*/g, '\n').trim();
const xmlText = (xml: string) => decodeEntities(xml.replace(/<\/(w:p|a:p|text:p|si|row)>/g, '\n').replace(/<(w:tab|w:br)\/>/g, ' ').replace(/<[^>]+>/g, ' '))
  .replace(/[ \t]+/g, ' ').replace(/ ?\n\s*/g, '\n').trim();

/** Minimal ZIP reader (central directory, stored/deflate) for Office XML parts; size-capped. */
export function unzip(buffer: Buffer, want: (name: string) => boolean, maxBytes = 20 * 1024 * 1024): Map<string, Buffer> {
  const result = new Map<string, Buffer>();
  let end = -1;
  for (let i = buffer.length - 22; i >= Math.max(0, buffer.length - 65_557); i--) if (buffer.readUInt32LE(i) === 0x06054b50) { end = i; break; }
  if (end < 0) throw Error('Not a zip file');
  let entries = buffer.readUInt16LE(end + 10), offset = buffer.readUInt32LE(end + 16), total = 0;
  while (entries-- > 0 && offset + 46 <= buffer.length && buffer.readUInt32LE(offset) === 0x02014b50) {
    const method = buffer.readUInt16LE(offset + 10), compressed = buffer.readUInt32LE(offset + 20), size = buffer.readUInt32LE(offset + 24);
    const nameLength = buffer.readUInt16LE(offset + 28), extra = buffer.readUInt16LE(offset + 30), comment = buffer.readUInt16LE(offset + 32);
    const local = buffer.readUInt32LE(offset + 42), name = buffer.toString('utf8', offset + 46, offset + 46 + nameLength);
    offset += 46 + nameLength + extra + comment;
    if (!want(name) || (total += size) > maxBytes) continue;
    if (local + 30 > buffer.length || buffer.readUInt32LE(local) !== 0x04034b50) continue;
    const start = local + 30 + buffer.readUInt16LE(local + 26) + buffer.readUInt16LE(local + 28);
    const data = buffer.subarray(start, start + compressed);
    if (method === 0) result.set(name, data);
    else if (method === 8) { try { result.set(name, inflateRawSync(data, { maxOutputLength: Math.max(size, 1) + 1024 })); } catch {} }
  }
  return result;
}

const order = (name: string) => Number(/(\d+)\.xml$/.exec(name)?.[1] ?? 0);
export function officeText(buffer: Buffer, ext: string): string {
  const parts = unzip(buffer, name => ext === 'docx' ? /^word\/(document|footnotes|endnotes)\.xml$/.test(name)
    : ext === 'xlsx' ? name === 'xl/sharedStrings.xml' || /^xl\/worksheets\/sheet\d+\.xml$/.test(name)
    : ext === 'pptx' ? /^ppt\/slides\/slide\d+\.xml$/.test(name) : name === 'content.xml');
  const names = [...parts.keys()].sort((a, b) => a.localeCompare(b) || order(a) - order(b)).sort((a, b) => order(a) - order(b));
  if (ext === 'xlsx') {
    // Shared strings hold the text; inline strings and numbers come from the sheets.
    const shared = parts.get('xl/sharedStrings.xml');
    const sheets = names.filter(n => n !== 'xl/sharedStrings.xml').map(n => parts.get(n)!.toString('utf8').replace(/<f>[\s\S]*?<\/f>/g, ''));
    const numbers = sheets.map(s => [...s.matchAll(/<c\b[^>]*?(?:t="(\w+)")?[^>]*>(?:<v>([^<]*)<\/v>|<is>([\s\S]*?)<\/is>)/g)]
      .filter(m => m[1] !== 's').map(m => m[2] ?? xmlText(m[3] ?? '')).join(' '));
    return [shared ? xmlText(shared.toString('utf8')) : '', ...numbers].join('\n').trim();
  }
  return names.map(n => xmlText(parts.get(n)!.toString('utf8'))).join('\n').trim();
}

async function readHead(file: string, bytes: number): Promise<Buffer> {
  const handle = await open(file, 'r');
  try { const buffer = Buffer.alloc(bytes); const { bytesRead } = await handle.read(buffer, 0, bytes, 0); return buffer.subarray(0, bytesRead); }
  finally { await handle.close(); }
}

/** Plain text of a document (empty when it has none or cannot be read); at most `maxChars`. */
export async function extractText(file: string, kind: FileKind, maxChars: number, signal: AbortSignal): Promise<string> {
  const ext = path.extname(file).slice(1).toLowerCase();
  try {
    if (kind === 'text') {
      const buffer = await readHead(file, maxChars * 4);
      if (buffer.includes(0)) return '';
      const text = buffer.toString('utf8');
      return (['html', 'htm'].includes(ext) ? htmlText(text) : ext === 'rtf' ? text.replace(/\\[a-z]+-?\d* ?|[{}]/g, ' ').replace(/\s+/g, ' ') : text).slice(0, maxChars);
    }
    if (kind === 'office') return officeText(await readFile(file), ext).slice(0, maxChars);
    if (kind === 'pdf') {
      const bin = await tool('pdftotext');
      if (!bin) return '';
      const output = await runProcess(bin, ['-q', '-l', '60', '-enc', 'UTF-8', file, '-'], AbortSignal.any([signal, AbortSignal.timeout(60_000)]));
      return output.replace(/\f/g, '\n').replace(/[ \t]+/g, ' ').replace(/\n\s*\n+/g, '\n').trim().slice(0, maxChars);
    }
  } catch { signal.throwIfAborted(); }
  return '';
}

/** Splits text into chunks of about `size` characters at paragraph/sentence boundaries. */
export function chunkText(text: string, size = 1500, max = 20): string[] {
  const chunks: string[] = [];
  let rest = text.replace(/\r/g, '').trim();
  while (rest && chunks.length < max) {
    if (rest.length <= size) { chunks.push(rest); break; }
    const window = rest.slice(0, size);
    const cut = Math.max(window.lastIndexOf('\n\n'), window.lastIndexOf('\n'), window.lastIndexOf('. '));
    const end = cut > size * 0.5 ? cut + 1 : size;
    chunks.push(rest.slice(0, end).trim()); rest = rest.slice(end).trim();
  }
  return chunks.filter(Boolean);
}

/** One media segment to embed (`at`: seconds into the file, for the search snippet). */
export type MediaPart = { kind: 'image' | 'frame' | 'audio'; at?: number; image?: Buffer; mime?: string; audio?: Buffer };
export const MEDIA_LIMITS = { frames: 4, video_seconds: 1800, audio_segments: 4, segment_seconds: 30, image_px: 896 } as const;

async function ffmpegOut(args: string[], signal: AbortSignal): Promise<Buffer> {
  const bin = await tool('ffmpeg');
  if (!bin) throw Error('ffmpeg missing');
  const { spawn } = await import('node:child_process');
  return new Promise<Buffer>((resolve, reject) => {
    const child = spawn(bin, ['-nostdin', '-hide_banner', '-loglevel', 'error', ...args], { stdio: ['ignore', 'pipe', 'ignore'] });
    const chunks: Buffer[] = []; let size = 0;
    const stop = () => child.kill('SIGKILL');
    const timer = setTimeout(stop, 120_000);
    signal.addEventListener('abort', stop, { once: true });
    child.stdout.on('data', (chunk: Buffer) => { size += chunk.length; if (size > 30 * 1024 * 1024) stop(); else chunks.push(chunk); });
    child.once('error', reject);
    child.once('close', code => {
      clearTimeout(timer); signal.removeEventListener('abort', stop);
      if (signal.aborted) reject(signal.reason); else if (code === 0 && size) resolve(Buffer.concat(chunks)); else reject(Error('ffmpeg failed'));
    });
  });
}
async function duration(file: string, signal: AbortSignal): Promise<{ seconds: number; audio: boolean; video: boolean }> {
  const bin = await tool('ffprobe');
  if (!bin) return { seconds: 0, audio: true, video: true };
  const output = await runProcess(bin, ['-v', 'error', '-show_entries', 'format=duration:stream=codec_type', '-of', 'json', file], AbortSignal.any([signal, AbortSignal.timeout(30_000)]));
  const value = JSON.parse(output) as { format?: { duration?: string }; streams?: { codec_type?: string }[] };
  const types = (value.streams ?? []).map(s => s.codec_type);
  return { seconds: Number(value.format?.duration) || 0, audio: types.includes('audio'), video: types.includes('video') };
}
const scale = `scale='min(${MEDIA_LIMITS.image_px},iw)':'min(${MEDIA_LIMITS.image_px},ih)':force_original_aspect_ratio=decrease`;
const frame = (file: string, at: number, signal: AbortSignal) =>
  ffmpegOut([...(at > 0 ? ['-ss', at.toFixed(2)] : []), '-i', file, '-frames:v', '1', '-vf', scale, '-f', 'image2pipe', '-vcodec', 'mjpeg', '-q:v', '4', '-'], signal);
const audioSegment = (file: string, at: number, signal: AbortSignal) =>
  ffmpegOut([...(at > 0 ? ['-ss', at.toFixed(2)] : []), '-i', file, '-t', String(MEDIA_LIMITS.segment_seconds), '-vn', '-ac', '1', '-ar', '16000', '-f', 'wav', '-'], signal);

/**
 * Image → one picture (downscaled JPEG via ffmpeg; without ffmpeg only JPEG/PNG/GIF/BMP as they are).
 * Audio → up to 4 × 30 s mono 16 kHz segments spread over the file. Video → up to 4 frames + 30 s of its audio track.
 */
export async function mediaParts(file: string, kind: 'image' | 'audio' | 'video', signal: AbortSignal): Promise<MediaPart[]> {
  const ext = path.extname(file).slice(1).toLowerCase();
  const ffmpeg = await tool('ffmpeg');
  if (kind === 'image') {
    if (ffmpeg) { try { return [{ kind: 'image', image: await frame(file, 0, signal), mime: 'image/jpeg' }]; } catch { signal.throwIfAborted(); } }
    const mime = ({ jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', gif: 'image/gif', bmp: 'image/bmp' } as Record<string, string>)[ext];
    const info = await open(file, 'r').then(async h => { try { return (await h.stat()).size; } finally { await h.close(); } });
    return mime && info <= 20 * 1024 * 1024 ? [{ kind: 'image', image: await readFile(file), mime }] : [];
  }
  if (!ffmpeg) return kind === 'audio' && ['wav', 'mp3'].includes(ext) && (await readHead(file, 1)).length
    ? [{ kind: 'audio', at: 0, audio: await readHead(file, 2 * 1024 * 1024) }] : [];
  const info = await duration(file, signal).catch(() => ({ seconds: 0, audio: true, video: kind === 'video' }));
  const length = Math.min(info.seconds, MEDIA_LIMITS.video_seconds);
  const parts: MediaPart[] = [];
  const spread = (count: number, span: number) => length > span * count ? Array.from({ length: count }, (_, i) => (length * (2 * i + 1)) / (2 * count) - (span / 2)) : [0];
  if (kind === 'video' && info.video) {
    const times = length > 4 ? Array.from({ length: MEDIA_LIMITS.frames }, (_, i) => (length * (2 * i + 1)) / (2 * MEDIA_LIMITS.frames)) : [0];
    for (const at of times) { try { parts.push({ kind: 'frame', at, image: await frame(file, at, signal), mime: 'image/jpeg' }); } catch { signal.throwIfAborted(); } }
  }
  if (info.audio) {
    const times = kind === 'video' ? [length > MEDIA_LIMITS.segment_seconds ? Math.max(0, length / 2 - MEDIA_LIMITS.segment_seconds / 2) : 0]
      : spread(MEDIA_LIMITS.audio_segments, MEDIA_LIMITS.segment_seconds).map(t => Math.max(0, t));
    for (const at of times) { try { parts.push({ kind: 'audio', at, audio: await audioSegment(file, at, signal) }); } catch { signal.throwIfAborted(); } }
  }
  return parts;
}
