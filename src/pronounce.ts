import { readFileSync, statSync } from 'node:fs';
import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import { gunzipSync } from 'node:zlib';
import path from 'node:path';
import { root } from './config.ts';

/**
 * Turkish TTS only: English words are respelled the way a Turkish voice should read them. Offline; the displayed
 * text and the model context never change. Order: user dictionary, Turkish word (untouched), acronym letter names,
 * CMUdict ARPAbet -> Turkish letters.
 */

export const MAX_PRONUNCIATIONS = 32000;
const lexiconDir = path.join(root, 'lexicon');
export const pronunciationsFile = path.join(root, 'data', 'pronunciations.txt');
export const pronunciationsExample = path.join(root, 'pronunciations.example.txt');

type Lexicon = { cmu: Map<string, string>; turkish: Set<string>; ms: number };
let lexicon: Lexicon | undefined;
/** Loaded once, on first use (synchronous gunzip + parse). */
export function loadLexicon(directory = lexiconDir): Lexicon {
  if (lexicon) return lexicon;
  const started = performance.now();
  const read = (name: string) => gunzipSync(readFileSync(path.join(directory, name))).toString('utf8');
  const cmu = new Map<string, string>();
  for (const line of read('cmudict.txt.gz').split('\n')) {
    const space = line.indexOf(' ');
    if (space > 0) cmu.set(line.slice(0, space), line.slice(space + 1));
  }
  const turkish = new Set(read('turkish-words.txt.gz').split('\n').filter(Boolean));
  return lexicon = { cmu, turkish, ms: Math.round(performance.now() - started) };
}

/** `word=reading` lines, case-insensitive keys; blank lines and `#` comments ignored. */
export function parsePronunciations(text: string): Map<string, string> {
  const entries = new Map<string, string>();
  for (const line of text.split(/\r?\n/)) {
    const at = line.indexOf('=');
    if (line.trim().startsWith('#') || at < 1) continue;
    const key = line.slice(0, at).trim().toLowerCase(), value = line.slice(at + 1).trim();
    if (key && value) entries.set(key, value);
  }
  return entries;
}

let cached: { file: string; mtime: number; entries: Map<string, string> } | undefined;
/** The user's dictionary (gitignored data file), else the tracked example; re-read only when the file changes. */
export function userPronunciations(file = pronunciationsFile, example = pronunciationsExample): Map<string, string> {
  for (const candidate of [file, example]) {
    let mtime: number;
    try { mtime = statSync(candidate).mtimeMs; } catch { continue; }
    if (cached?.file === candidate && cached.mtime === mtime) return cached.entries;
    cached = { file: candidate, mtime, entries: parsePronunciations(readFileSync(candidate, 'utf8')) };
    return cached.entries;
  }
  return new Map();
}
export async function readPronunciations(file = pronunciationsFile, example = pronunciationsExample): Promise<string> {
  try { return await readFile(file, 'utf8'); }
  catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
  return readFile(example, 'utf8').catch(() => '');
}
export async function savePronunciations(content: unknown, file = pronunciationsFile) {
  if (typeof content !== 'string' || content.length > MAX_PRONUNCIATIONS) throw Error(`Pronunciations: up to ${MAX_PRONUNCIATIONS} characters`);
  await mkdir(path.dirname(file), { recursive: true, mode: 0o700 });
  const temporary = file + '.tmp';
  await writeFile(temporary, content, { mode: 0o600 });
  await rename(temporary, file);
  return { content };
}

const LETTERS: Record<string, string> = { a: 'ey', b: 'bi', c: 'si', d: 'di', e: 'i', f: 'ef', g: 'ci', h: 'eyç', i: 'ay', j: 'cey',
  k: 'key', l: 'el', m: 'em', n: 'en', o: 'o', p: 'pi', q: 'kyu', r: 'ar', s: 'es', t: 'ti', u: 'yu', v: 'vi', w: 'dabılyu',
  x: 'eks', y: 'vay', z: 'zi' };
const PHONES: Record<string, string> = { AA: 'a', AE: 'e', AH: 'a', AO: 'o', AW: 'au', AY: 'ay', EH: 'e', ER: 'ır', EY: 'ey',
  IH: 'i', IY: 'i', OW: 'o', OY: 'oy', UH: 'u', UW: 'u', B: 'b', CH: 'ç', D: 'd', DH: 'd', F: 'f', G: 'g', HH: 'h', JH: 'c',
  K: 'k', L: 'l', M: 'm', N: 'n', NG: 'ng', P: 'p', R: 'r', S: 's', SH: 'ş', T: 't', TH: 't', V: 'v', W: 'v', Y: 'y', Z: 'z', ZH: 'j' };

/**
 * ARPAbet (stress digits dropped) to Turkish letters. When the spelling's vowel groups line up one-to-one with the
 * vowel phones, the spelling picks AA/AH0 -> o ("online" onlayn, "doctor" doktır) or a word-final AH0 -> a ("data" deyta);
 * a word-initial AH0 is a ("update" apdeyt); otherwise unstressed AH is ı ("Google" gugıl, "storage" storıc).
 */
export function arpabetToTurkish(phones: string, spelling = ''): string {
  const list = phones.split(' ').filter(Boolean);
  const groups = spelling.toLowerCase().replace(/([^aeiouy])e$/, '$1').match(/[aeiouy]+/g) ?? [];
  const vowels = list.filter(p => /\d$/.test(p)).length, aligned = groups.length === vowels;
  let index = 0, result = '';
  for (const [i, phone] of list.entries()) {
    const last = i === list.length - 1;
    const base = phone.replace(/\d$/, ''), group = /\d$/.test(phone) ? (aligned ? groups[index++] : '') : undefined;
    let letters = PHONES[base] ?? '';
    if (base === 'AA' && group?.includes('o') && !group.includes('a')) letters = 'o';
    if (phone === 'AH0') letters = !result || (group === 'a' && last) ? 'a' : group === 'o' ? 'o' : 'ı';
    result += letters;
  }
  return result;
}

const plain = /^[A-Za-z]+$/;
function automatic(word: string, lex: Lexicon): string | undefined {
  if (!plain.test(word) || word.length < 2) return undefined; // Turkish letters, digits, single letters: never.
  const lower = word.toLowerCase();
  if (word.length <= 5 && word === word.toUpperCase()) {
    // An all-caps Turkish word (BU, BUNU) stays when it is also pronounceable; FBI, USB, TV, AI are acronyms.
    const pronounceable = /[aeiou]/.test(lower) && /[^aeiou]/.test(lower) && !/^[^aeiou]{2}|[^aeiou]{3}/.test(lower);
    return lex.turkish.has(lower) && pronounceable ? undefined : [...lower].map(c => LETTERS[c]).join('-');
  }
  if (lex.turkish.has(lower)) return undefined;
  const phones = lex.cmu.get(lower);
  if (!phones) return undefined;
  const said = arpabetToTurkish(phones, lower);
  return /^[A-Z]/.test(word) ? said[0].toLocaleUpperCase('tr') + said.slice(1) : said;
}

/** Respells English words for a Turkish voice. Suffixes after an apostrophe ("Google'da") are kept as written. */
export function respellEnglish(text: string, user: ReadonlyMap<string, string> = userPronunciations(), lex = loadLexicon()): string {
  const word = (part: string) => user.get(part.toLowerCase()) ?? automatic(part, lex);
  const stem = (part: string) => word(part) ?? (part.includes('-') ? part.split('-').map(p => word(p) ?? p).join('-') : part);
  return text.replace(/[\p{L}\p{N}]+(?:[-'’][\p{L}\p{N}]+)*/gu, token => {
    const whole = user.get(token.toLowerCase());
    if (whole) return whole;
    const apostrophe = token.search(/['’]/);
    return apostrophe < 0 ? stem(token) : stem(token.slice(0, apostrophe)) + token.slice(apostrophe);
  });
}

/** Text handed to the TTS engine: respelled for Turkish only. */
export function ttsText(text: string, language: string, user?: ReadonlyMap<string, string>): string {
  return language === 'tr' ? respellEnglish(text, user ?? userPronunciations()) : text;
}
