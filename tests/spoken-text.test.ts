import test from 'node:test';
import assert from 'node:assert/strict';
import { spokenText } from '../src/spoken-text.ts';
import { Speech } from '../src/speech.ts';
import { defaults } from '../src/config.ts';

test('speech keeps Markdown link labels and removes raw links and empty source lines', () => {
  assert.equal(spokenText('**Londra haberleri**\nAyrıntılar [BBC](https://bbc.com/news/story_(London)) üzerinden.\nKaynak: https://example.com/news?a=1\nwww.example.org\nBitti.'), 'Londra haberleri\nAyrıntılar BBC üzerinden.\nBitti.');
  assert.equal(spokenText('Kısa haber. Kaynak: https://example.com'), 'Kısa haber.');
});
test('speech removes email, domains, images, code blocks, paths and citation markers', () => {
  assert.equal(spokenText('Read [the source](https://example.com "Title"). [1]\nSource: <https://example.com>\nContact user@example.org\n![chart](https://example.org/a.png)\n```ts\nconsole.log(1);\n```\nPath /Users/name/Documents/file.txt\nSee example.co.uk/news\nmailto:user@example.com'), 'Read the source.\nContact\nPath\nSee');
});
test('speech preserves Turkish/English prose, numbers and useful technical names', () => {
  const text = 'Sıcaklık 21,5 °C. 2026 yılında 3 haber var. Control + Option ile aç.\nRAM 12.4 GB; Gemma 4 ve Piper 1.8.';
  assert.equal(spokenText(text), text);
  assert.equal(spokenText('Use `Control + Option` and **Piper**.'), 'Use Control + Option and Piper.');
});
test('URL-only responses skip speech processes and leave displayed source unchanged', async () => {
  const original = 'https://example.com/news';
  const speech = new Speech({ ...defaults, language_detector: '/missing/detector' });
  await speech.speak(original, new AbortController().signal);
  assert.equal(original, 'https://example.com/news');
  assert.equal(speech.speaker, undefined);
});
