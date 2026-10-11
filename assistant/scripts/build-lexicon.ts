// Rebuilds lexicon/*.gz from downloaded sources (one-off; the outputs are committed). See lexicon/SOURCES.txt.
// Usage: node scripts/build-lexicon.ts <dir with cmudict.dict, master-dictionary.dict, tr_50k.txt, en_50k.txt>
import { readFileSync, writeFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import path from 'node:path';
import { root } from '../src/config.ts';

const source = process.argv[2];
if (!source) throw Error('Usage: node scripts/build-lexicon.ts <source dir>');
const lines = (name: string) => readFileSync(path.join(source, name), 'utf8').split('\n');
const out = (name: string, text: string) => writeFileSync(path.join(root, 'lexicon', name), gzipSync(text, { level: 9 }));

// CMUdict: first pronunciation of plain alphabetic words (with apostrophes), stress digits kept.
const cmu = new Map<string, string>();
for (const line of lines('cmudict.dict')) {
  const match = line.match(/^([a-z']+)(\(\d+\))? ([A-Z0-9 ]+?)(?:\s+#.*)?$/);
  if (match && !match[2] && !cmu.has(match[1])) cmu.set(match[1], match[3]);
}
out('cmudict.txt.gz', [...cmu].map(([word, phones]) => `${word} ${phones}`).join('\n') + '\n');

// Turkish words: Zemberek lemmas (always) + Turkish subtitle frequency words that are not clearly English
// (English list ranks them at least 4x higher, or English-only spelling such as q/w/x, th, sh, ch, -y).
// Stored: words CMUdict also has (nothing else could be respelled) and plain 2-5 letter words (all-caps
// Turkish words like BU or VE are not acronyms).
const turkish = new Set<string>();
const needed = (word: string) => cmu.has(word) || /^[a-z]{2,5}$/.test(word);
for (const line of lines('master-dictionary.dict')) {
  const word = line.trim().split(' ')[0]?.toLocaleLowerCase('tr');
  if (word && !line.startsWith('#') && needed(word)) turkish.add(word);
}
const rank = (name: string) => new Map(lines(name).map((line, index) => [line.split(' ')[0], index] as const));
const english = rank('en_50k.txt');
for (const [word, index] of rank('tr_50k.txt')) {
  if (!needed(word)) continue;
  const en = english.get(word);
  if ((en !== undefined && en * 4 <= index) || (cmu.has(word) && /[qwx]|th|sh|ch|ck|ph|oo|ea|ou|gh|y$/.test(word))) continue;
  turkish.add(word);
}
out('turkish-words.txt.gz', [...turkish].sort().join('\n') + '\n');
console.log(`cmudict ${cmu.size} words, turkish ${turkish.size} words`);
