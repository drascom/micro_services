// T25 "Örnekler" section of a stage card (pure render). Run: node tests/js/samples_test.js
'use strict';
const assert = require('assert');
const S = require('../../app/admin/static/js/cardsync.js');

const st = {id: 'A3', state: 'needs_human', sample_list: {
  rows: [{url: 'https://x.test/diziler/a/', source: 'senin', measured: true, ok: true, note: 'anahtar a', used: true},
         {url: 'https://x.test/diziler/<b>/', source: 'önceki aşamadan', measured: true, ok: false, note: 'yıl yok', used: true},
         {url: 'https://x.test/diziler/c/', source: 'otomatik', measured: false, ok: null, note: '', used: true}],
  excluded: ['https://x.test/diziler/old/'], own_only: false, min: 3, own_count: 1, warning: ''}};
const html = S.samplesHtml(st, true);
assert.ok(html.indexOf('<h3>Örnekler</h3>') === 0);
assert.strictEqual((html.match(/data-a="sdel"/g) || []).length, 3);                   // Sil + Düzenle on every row
assert.strictEqual((html.match(/data-a="sedit"/g) || []).length, 3);
assert.ok(html.indexOf('&lt;b&gt;') > 0 && html.indexOf('<b>/') < 0);                   // escaped
assert.ok(html.indexOf('pill ok">✓') > 0 && html.indexOf('pill bad">✗') > 0 && html.indexOf('<td>—') > 0);
assert.ok(html.indexOf('önceki aşamadan') > 0 && html.indexOf('otomatik') > 0);
assert.ok(html.indexOf('Silinenler (1)') > 0 && html.indexOf('data-a="sres"') > 0);
assert.ok(html.indexOf('Sadece bunları kullan?') > 0);                                  // offered after a given sample
assert.ok(html.indexOf('data-a="srun"') > 0 && html.indexOf('data-a="sadd"') > 0);
// switched on with too few: the message, no offer, non-own rows marked
const on = JSON.parse(JSON.stringify(st));
Object.assign(on.sample_list, {own_only: true, warning: 'En az 3 örnek gerekiyor; şu an 1'});
on.sample_list.rows[1].used = false;
const h2 = S.samplesHtml(on, true);
assert.ok(h2.indexOf('En az 3 örnek gerekiyor; şu an 1') > 0 && h2.indexOf('Sadece bunları kullan?') < 0);
assert.ok(h2.indexOf('(kullanılmayacak)') > 0 && h2.indexOf('data-on="0"') > 0);
// running: read only; no list: nothing
assert.strictEqual(S.samplesHtml(st, false).indexOf('data-a='), -1);
assert.strictEqual(S.samplesHtml({id: 'A2'}, true), '');
// a sample_list change redraws only the samples section; editing it holds the redraw
const k = S.plan({}, st, {now: 10000}).keys;
const changed = Object.assign({}, st, {sample_list: on.sample_list});
assert.deepStrictEqual(S.plan(k, changed, {now: 20000}).sections, ['samples']);
assert.deepStrictEqual(S.plan(k, changed, {now: 20000, samplesEditing: true}).sections, []);
console.log('samples_test ok');
