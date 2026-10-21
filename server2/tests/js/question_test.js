// "Yardım gerekiyor" question render (pure, cardsync.js). Run: node tests/js/question_test.js ["<question text>"]
'use strict';
const assert = require('assert');
const S = require('../../app/admin/static/js/cardsync.js');

const text = process.argv[2] || 'Yardım gerekiyor\nNe yaptım\n • Aradığım: oynatıcı\n • Baktığım sayfalar (5):\n' +
  '    https://h.test/a\n    https://h.test/b\n    https://h.test/c\n    +2 daha\nBulamadığım\n • Oynatıcı yok <x>.\n' +
  'Senden istediğim\n • Göster.';
const secs = S.questionParse(text);
assert.deepStrictEqual(secs.slice(0, 2).map(s => s.title), ['Ne yaptım', 'Bulamadığım']);
assert.strictEqual(secs[0].items[1].lines.length, 4);                                  // 3 addresses + "+2 daha", own lines
assert.ok(/^\+\d+ daha$/.test(secs[0].items[1].lines[3]));
const html = S.questionHtml(secs);
assert.ok(html.indexOf('<b>Ne yaptım</b><ul><li>Aradığım: oynatıcı</li>') > 0);
assert.strictEqual((html.match(/class="qurl"/g) || []).length, 4);
assert.ok(html.indexOf('<x>') < 0);                                                    // escaped
assert.ok(S.questionSummary(secs).length > 0 && S.questionSummary(secs).length <= 90);
// older one-line form still parses; other messages are not a question
const old = S.questionParse('Yardım gerekiyor.\nNe yaptım: a; b.\nSenden istediğim: c');
assert.deepStrictEqual(old.map(s => [s.title, s.items[0].text]), [['Ne yaptım', 'a; b.'], ['Senden istediğim', 'c']]);
assert.strictEqual(S.questionParse('Motor boşluğu: x'), null);
