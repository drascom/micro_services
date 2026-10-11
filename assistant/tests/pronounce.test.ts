import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { arpabetToTurkish, loadLexicon, parsePronunciations, readPronunciations, respellEnglish, savePronunciations,
  ttsText, userPronunciations } from '../src/pronounce.ts';
import { spokenText } from '../src/spoken-text.ts';

const none = new Map<string, string>();
test('CMUdict words become Turkish spellings', () => {
  assert.equal(respellEnglish('meeting update Google weather phone download computer doctor about', none),
    'miting apdeyt Gugıl vedır fon daunlod kompyutır doktır abaut');
  assert.equal(arpabetToTurkish('DH AH0'), 'dı');
  assert.equal(arpabetToTurkish('JH AA1 B'), 'cab');
  assert.equal(arpabetToTurkish('JH AA1 B', 'job'), 'cob');
  assert.equal(arpabetToTurkish('SH UW1 Z', 'shoes'), 'şuz');
});
test('user dictionary wins, case-insensitive, also for acronyms and hyphenated words', () => {
  const user = parsePronunciations('# yorum\nGoogle=Gugıl\nupdate = apdeyt\nAPI=a pe i\nWi-Fi=vay fay\nbozuk satır\n=x\n');
  assert.deepEqual([...user.keys()], ['google', 'update', 'api', 'wi-fi']);
  assert.equal(respellEnglish('GOOGLE update API Wi-Fi meeting', user), 'Gugıl apdeyt a pe i vay fay miting');
});
test('Turkish words and letters stay as written', () => {
  const text = 'Bir on ten set kim beni sana gel git eve şimdi çalış öğle 2026 yılı x';
  assert.equal(respellEnglish(text, none), text);
});
test('suffixes after an apostrophe are kept', () => {
  assert.equal(respellEnglish("Google'da meeting'e e-mail’i", parsePronunciations('e-mail=imeyl')), "Gugıl'da miting'e imeyl’i");
});
test('all-caps 2-5 letters read as English letter names', () => {
  assert.equal(respellEnglish('API, URL ve PDF; BU VE BUNU; AI TV FBI', none), 'ey-pi-ay, yu-ar-el ve pi-di-ef; BU VE BUNU; ey-ay ti-vi ef-bi-ay');
});
test('only Turkish speech is respelled; displayed text is unchanged', () => {
  const reply = "**Google**'da meeting var: [link](https://example.com)";
  const spoken = spokenText(reply);
  assert.equal(ttsText(spoken, 'en', none), spoken);
  assert.equal(ttsText(spoken, 'tr', none), "Gugıl'da miting var: link");
  assert.equal(reply, "**Google**'da meeting var: [link](https://example.com)");
});
test('lexicon loads once and respelling is fast', () => {
  const lexicon = loadLexicon();
  assert.ok(lexicon.cmu.size > 100000 && lexicon.turkish.has('bir'));
  assert.equal(loadLexicon(), lexicon);
  const sentence = 'Bugün meeting var, Google update ve email kontrol et. '.repeat(10);
  const started = performance.now();
  for (let i = 0; i < 20; i++) respellEnglish(sentence, none);
  assert.ok((performance.now() - started) / 20 < 5);
});
test('user dictionary file: example until saved, reloads after save', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'pronounce-'));
  try {
    const file = path.join(directory, 'data', 'pronunciations.txt'), example = path.join(directory, 'example.txt');
    assert.equal(await readPronunciations(file, example), '');
    assert.equal(userPronunciations(file, example).size, 0);
    await savePronunciations('Slack=Slek\n', file);
    assert.equal(await readFile(file, 'utf8'), 'Slack=Slek\n');
    assert.equal(userPronunciations(file, example).get('slack'), 'Slek');
    await assert.rejects(savePronunciations(42, file));
  } finally { await rm(directory, { recursive: true, force: true }); }
});
