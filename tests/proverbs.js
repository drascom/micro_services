// Proverbs list + no-repeat random picker, and the full-screen loading modal that rotates them.
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, find, timers, rng } = require('./_fake_dom');

const root = path.join(__dirname, '../tizen-client/js');
const T = timers();
const overlay = el('div');
const keyHandlers = [];
const enabledLog = [];
const window = {
  DZ: {
    keys: { onKey(fn) { keyHandlers.push(fn); return () => { const i = keyHandlers.indexOf(fn); if (i >= 0) keyHandlers.splice(i, 1); }; } },
    nav: { setEnabled(v) { enabledLog.push(v); } }
  }
};
const sandbox = {
  window, console: { log() {} },
  document: { createElement: el, getElementById: () => overlay },
  setTimeout: T.setTimeout, clearTimeout: T.clearTimeout, setInterval: T.setInterval, clearInterval: T.clearInterval
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(root, 'proverbs.js'), 'utf8'), sandbox);
vm.runInContext(fs.readFileSync(path.join(root, 'ui/modal.js'), 'utf8'), sandbox);
const P = window.DZ.proverbs;

// --- list quality: 80-100 real, unique, non-empty sentences
assert(P.list.length >= 80 && P.list.length <= 100, 'proverb count ' + P.list.length);
assert.equal(new Set(P.list).size, P.list.length, 'duplicate proverbs');
P.list.forEach(p => { assert(typeof p === 'string' && p.trim().length > 8 && /[.!?]$/.test(p), 'bad proverb: ' + p); });

// --- picker: a full cycle never repeats; the next cycle does not start with the last one
[rng(1), rng(42), Math.random].forEach(r => {
  const picker = P.createPicker(null, r);
  let previousLast = null;
  for (let cycle = 0; cycle < 4; cycle++) {
    const seen = [];
    for (let i = 0; i < P.list.length; i++) seen.push(picker.next());
    assert.equal(new Set(seen).size, P.list.length, 'repeat inside a cycle');
    if (previousLast !== null) assert.notEqual(seen[0], previousLast, 'cycle boundary repeat');
    previousLast = seen[seen.length - 1];
  }
});
// the order is actually random (two seeds differ) and the source list is not mutated
const before = P.list.slice();
const a = P.createPicker(null, rng(7)), b = P.createPicker(null, rng(8));
const seqA = Array.from({ length: 10 }, () => a.next()), seqB = Array.from({ length: 10 }, () => b.next());
assert.notDeepEqual(seqA, seqB, 'picker order should depend on rng');
assert.deepEqual(P.list, before, 'source list mutated');

// --- empty / tiny / invalid lists never throw
const empty = P.createPicker([]);
assert.equal(empty.next(), ''); assert.equal(empty.next(), '');
const one = P.createPicker(['tek']);
assert.equal(one.next(), 'tek'); assert.equal(one.next(), 'tek');
const two = P.createPicker(['a', 'b'], rng(3));
const twoSeq = Array.from({ length: 9 }, () => two.next());
twoSeq.forEach((v, i) => { if (i) assert.notEqual(v, twoSeq[i - 1], 'two-item list repeated back-to-back'); });
assert.deepEqual(P.shuffle(undefined), []);
assert.equal(typeof P.createPicker('nope').next(), 'string');

// --- loading modal
const modal = window.DZ.modal;
let cancelled = 0;
const ctl = modal.loading({ onCancel() { cancelled++; }, rng: rng(5) });
assert(modal.isOpen() && ctl.isOpen(), 'loading modal should be open');
assert(overlay.classList.contains('active'), 'overlay active');
assert.equal(enabledLog[enabledLog.length - 1], false, 'nav disabled while loading');
const proverbEl = find(overlay, 'loading-proverb');
const stageEl = find(overlay, 'loading-stage');
assert(find(overlay, 'loading-spinner') && find(overlay, 'modal-full'), 'spinner + fullscreen backdrop');
const first = proverbEl.textContent;
assert(P.list.includes(first), 'shows a real proverb');
ctl.setStage('Kaynak aranıyor…');
assert.equal(stageEl.textContent, 'Kaynak aranıyor…');

T.advance(4999);
assert.equal(proverbEl.textContent, first, 'must not change before ~5 s');
T.advance(1);
assert(proverbEl.className.split(' ').includes('fade'), 'soft transition starts (fade out)');
assert.equal(proverbEl.textContent, first, 'text swaps only after the fade-out');
T.advance(400);
assert(!proverbEl.className.split(' ').includes('fade'), 'fade back in');
assert.notEqual(proverbEl.textContent, first, 'next proverb shown');
assert(P.list.includes(proverbEl.textContent));

// other keys are swallowed, BACK cancels and closes
assert.equal(keyHandlers[keyHandlers.length - 1]({ name: 'left' }), true);
assert.equal(keyHandlers[keyHandlers.length - 1]({ name: 'enter' }), true);
assert.equal(cancelled, 0);
keyHandlers[keyHandlers.length - 1]({ name: 'back' });
assert.equal(cancelled, 1, 'BACK cancels');
assert(!modal.isOpen() && !ctl.isOpen());
assert.equal(T.pending(), 0, 'rotation timers must be cleared on close');
assert.equal(overlay.children.length, 0);
assert.equal(enabledLog[enabledLog.length - 1], true, 'nav re-enabled');
assert.equal(keyHandlers.length, 0, 'key handler removed');

// close() from code (success path) also stops timers and does not call onCancel
const ctl2 = modal.loading({ onCancel() { cancelled++; } });
T.advance(5100);
ctl2.close();
assert.equal(cancelled, 1); assert.equal(T.pending(), 0); assert(!modal.isOpen());

// opening a standard dialog replaces the loading modal (error state) and stops its timers
const ctl3 = modal.loading({});
modal.open({ fullscreen: true, title: 'Kaynak çalışmıyor', buttons: [{ label: 'Geri', value: 'back' }] });
assert(!ctl3.isOpen()); assert.equal(T.pending(), 0);
assert(find(overlay, 'modal-full'), 'fullscreen error dialog');
modal.close();

console.log('Proverbs: list, no-repeat picker, empty list, loading modal rotation/cancel: OK');
