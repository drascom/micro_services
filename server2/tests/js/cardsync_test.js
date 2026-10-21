// Pure "should update" decisions of the stage card polling (T19 user bug: sayfa kayması). Run: node tests/js/cardsync_test.js
'use strict';
const assert = require('assert');
const S = require('../../app/admin/static/js/cardsync.js');

const st = {state: 'passed', checks: [{name: 'x', value: 1}], samples: [], evidence: {found: 1}, messages: [{text: 'a'}],
  review: null, actions: ['rerun'], handoff_in: ''};
const first = S.plan({}, st, {now: 10000});
assert.deepStrictEqual(first.sections, S.SECTIONS);                                   // first render: every section
assert.deepStrictEqual(S.plan(first.keys, st, {now: 20000}).sections, []);             // same data: nothing touched
const msg = S.plan(first.keys, Object.assign({}, st, {messages: [{text: 'a'}, {text: 'b'}]}), {now: 20000});
assert.deepStrictEqual(msg.sections, ['messages']);                                    // only the changed section
const ev = Object.assign({}, st, {evidence: {found: 2}});
assert.strictEqual(S.plan(first.keys, ev, {now: 20000, lastInput: 18000}).skip, true);  // typing (< 3 s): card left alone
assert.deepStrictEqual(S.plan(first.keys, ev, {now: 20000, lastInput: 16000}).sections, ['evidence']);
const rv = Object.assign({}, st, {review: {links: [1]}});
const held = S.plan(first.keys, rv, {now: 20000, focus: 'review'});
assert.deepStrictEqual(held.sections, []);                                             // focused review form: not redrawn
assert.strictEqual(held.keys.review, first.keys.review);                               // ... and redrawn later
assert.deepStrictEqual(S.plan(first.keys, rv, {now: 20000, reviewDirty: true}).sections, []);   // chosen dropdowns kept
assert.deepStrictEqual(S.plan(first.keys, Object.assign({}, st, {actions: []}), {now: 20000, focus: 'hint'}).sections, ['samples']);  // hint box in use: its buttons wait
assert.ok(S.sameOrder([1, 2], [1, 2]) && !S.sameOrder([2, 1], [1, 2]) && !S.sameOrder([1], [1, 2]));
// accordion: one open card at most, auto-open only until the admin's first click
assert.strictEqual(S.accordionPick(['A1', 'A2', 'A3'], ['A3', 'A2'], null), 'A3');                 // first waiting card
assert.strictEqual(S.accordionPick(['A1', 'A2'], [], null), null);                                 // nothing waiting: all closed
assert.strictEqual(S.accordionPick(['A1', 'A2'], ['A2'], {open: 'A1', touched: true}), 'A1');      // kept after a refresh
assert.strictEqual(S.accordionPick(['A1', 'A2'], ['A2'], {open: null, touched: true}), null);      // admin closed all: no auto-open
assert.strictEqual(S.accordionPick(['A1', 'A2'], ['A2'], {open: 'A9', touched: true}), null);      // vanished card
assert.strictEqual(S.accordionToggle('A1', 'A2', false), 'A2');                                    // opening closes the other
assert.strictEqual(S.accordionToggle('A2', 'A2', true), null);                                     // closing it again: 0 open
assert.strictEqual(S.accordionToggle(null, 'A3', false), 'A3');
console.log('cardsync ok');
