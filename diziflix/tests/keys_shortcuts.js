'use strict';
/* Klavye: "R" arama/Kirmizi taklidi DEGIL; Cmd/Ctrl/Alt kisayollari tarayiciya kalir;
   TV renkli/medya/yon tuslari eskisi gibi calisir. Sahte DOM, tarayici/gorsel test yok. */
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

let nativeKeydown = null;
const doc = { addEventListener(type, fn) { if (type === 'keydown') nativeKeydown = fn; } };
const win = { document: doc, DZ: {} };
const ctx = vm.createContext({ window: win, document: doc, console: { log() {} } });
vm.runInContext(fs.readFileSync(path.join(__dirname, '../tizen-client/js/keys.js'), 'utf8'), ctx);
const keys = win.DZ.keys;

const received = [];
keys.onKey(ev => { received.push(ev.name); return true; });
keys.init();

function press(ev) {
  const r = { prevented: 0, stopped: 0 };
  nativeKeydown(Object.assign({
    target: { tagName: 'DIV' },
    preventDefault() { r.prevented++; },
    stopPropagation() { r.stopped++; }
  }, ev));
  return r;
}
function names() { return received.splice(0).join(','); }

/* (a) "r" harfi (82 / key r|R) hicbir eylem uretmez, olay yutulmaz */
assert.strictEqual(keys.nameOf(82), null, '"R" artik mantiksal isme baglanmamali');
let r = press({ keyCode: 82, key: 'r' });
assert.strictEqual(names(), '', '"r" arama cubugunu/Kirmizi eylemini tetiklememeli');
assert.strictEqual(r.prevented, 0, '"r" preventDefault cagirmamali');
press({ keyCode: 82, key: 'R', shiftKey: true });
assert.strictEqual(names(), '');

/* (b) Cmd+Shift+R, Ctrl+R, Cmd+L, Cmd+F, Cmd+W, Alt+Sol: eylem yok, preventDefault/stopPropagation yok */
[
  { keyCode: 82, key: 'R', metaKey: true, shiftKey: true },
  { keyCode: 82, key: 'r', metaKey: true },
  { keyCode: 82, key: 'r', ctrlKey: true },
  { keyCode: 82, key: 'R', ctrlKey: true, shiftKey: true },
  { keyCode: 76, key: 'l', metaKey: true },
  { keyCode: 70, key: 'f', metaKey: true },
  { keyCode: 87, key: 'w', metaKey: true },
  { keyCode: 37, key: 'ArrowLeft', altKey: true },
  { keyCode: 37, key: 'ArrowLeft', metaKey: true },
  { keyCode: 13, key: 'Enter', ctrlKey: true },
  { keyCode: 403, key: 'ColorF0Red', metaKey: true }
].forEach(ev => {
  const res = press(ev);
  assert.strictEqual(names(), '', 'modifier kisayolu eylem tetiklememeli: ' + JSON.stringify(ev));
  assert.strictEqual(res.prevented, 0, 'preventDefault cagrilmamali: ' + JSON.stringify(ev));
  assert.strictEqual(res.stopped, 0, 'stopPropagation cagrilmamali: ' + JSON.stringify(ev));
});
/* metin girisi odakliyken de Cmd/Ctrl kisayollari (Cmd+A, Cmd+V, Cmd+R) gecer */
r = press({ keyCode: 82, key: 'r', metaKey: true, shiftKey: true, target: { tagName: 'INPUT' } });
assert.strictEqual(r.prevented, 0);
assert.strictEqual(names(), '');

/* (c) TV Kirmizi (403), Yesil (404), Sari (405), Mavi (406), desktop F1 -> Kirmizi */
r = press({ keyCode: 403 });
assert.strictEqual(names(), 'red');
assert.strictEqual(r.prevented, 1, 'TV tuslari eskisi gibi tuketilir');
assert.strictEqual(keys.nameOf(404), 'green');
assert.strictEqual(keys.nameOf(405), 'yellow');
assert.strictEqual(keys.nameOf(406), 'blue');
press({ keyCode: 405 });
assert.strictEqual(names(), 'yellow');
press({ keyCode: 112 });
assert.strictEqual(names(), 'red', 'F1 masaustunde Kirmizi taklidi');
assert.strictEqual(keys.nameOf(89), 'yellow', 'Y masaustu Sari (ses/altyazi) aliasi degismedi');
assert.strictEqual(keys.nameOf(457), 'info');
assert.strictEqual(keys.nameOf(10252), 'playpause');
assert.strictEqual(keys.nameOf(417), 'ff');
assert.strictEqual(keys.nameOf(412), 'rew');

/* (d) ok tuslari / Enter / Geri etkilenmez */
[[37, 'left'], [38, 'up'], [39, 'right'], [40, 'down'], [13, 'enter'], [10009, 'back'], [27, 'back'], [10182, 'exit']].forEach(([code, name]) => {
  const res = press({ keyCode: code });
  assert.strictEqual(names(), name, 'tus ' + code);
  assert.strictEqual(res.prevented, 1, 'tus ' + code + ' preventDefault');
});
/* Shift tek basina (Shift+ok) kisayol modifikatoru sayilmaz */
press({ keyCode: 39, shiftKey: true });
assert.strictEqual(names(), 'right');

/* Metin girisi: harfler (r dahil) yutulmaz; yukari/asagi/enter/geri eskisi gibi */
let typed = press({ keyCode: 82, key: 'r', target: { tagName: 'INPUT' } });
assert.strictEqual(names(), '');
assert.strictEqual(typed.prevented, 0, 'input icinde "r" yazilabilmeli');
press({ keyCode: 40, target: { tagName: 'INPUT' } });
press({ keyCode: 13, target: { tagName: 'INPUT' } });
assert.strictEqual(names(), 'down,enter');
typed = press({ keyCode: 10009, target: { tagName: 'INPUT' } });
assert.strictEqual(names(), 'back');
assert.strictEqual(typed.prevented, 0, 'input icinde Geri preventDefault cagirmaz (eski davranis)');

/* Arama kutusu Enter isleyicisi de modifikatorde gecer */
const listeners = {};
const mkEl = tag => ({ tagName: tag, style: {}, children: [], appendChild(c) { this.children.push(c); return c; },
  addEventListener(t, f) { listeners[tag + ':' + t] = f; }, setAttribute() {}, removeChild() {}, focus() {}, blur() {} });
const navCtx = vm.createContext({
  window: { DZ: { store: { get: () => '', set() {} }, app: { go() { throw new Error('submit tetiklendi'); } } } },
  document: { createElement: t => mkEl(t.toUpperCase()) }, console
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../tizen-client/js/ui/navigation.js'), 'utf8'), navCtx);
const bar = navCtx.window.DZ.navigation.create('home', 'abc');
void bar;
const submitKey = listeners['INPUT:keydown'];
assert.ok(submitKey, 'arama input keydown dinleyicisi');
let pd = 0;
submitKey({ keyCode: 13, metaKey: true, preventDefault() { pd++; }, stopPropagation() { pd++; } });
assert.strictEqual(pd, 0, 'Cmd+Enter arama kutusunda yakalanmamali');

console.log('Keys shortcuts: R removed, modifier shortcuts pass through, TV keys intact: OK');
