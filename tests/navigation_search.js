const fs = require('fs');
const path = require('path');
const vm = require('vm');

function element(tag) {
  const listeners = {};
  return {
    tagName: tag.toUpperCase(), className: '', children: [], attributes: {}, style: {},
    value: '', placeholder: '', type: '', inputMode: '', textContent: '',
    setAttribute(k, v) { this.attributes[k] = String(v); },
    getAttribute(k) { return this.attributes[k] || null; },
    appendChild(child) { this.children.push(child); child.parentNode = this; return child; },
    addEventListener(type, fn) { listeners[type] = fn; },
    dispatch(type, event) { if (listeners[type]) listeners[type](event || {}); }
  };
}

const calls = [];
const values = {};
const timersQueue = [];
const window = { setTimeout(fn) { timersQueue.push(fn); return timersQueue.length; }, clearTimeout() {}, DZ: {
  api: { profileId() { return 'p1'; } },
  store: { get(k, fallback) { return values[k] === undefined ? fallback : values[k]; }, set(k, v) { values[k] = v; } },
  app: { go(screen, params, replace) { calls.push({ screen, params, replace }); } }
} };
const document = { createElement: element, addEventListener() {} };
const context = vm.createContext({ window, document, console });
vm.runInContext(fs.readFileSync(path.join(__dirname, '../tizen-client/js/ui/navigation.js'), 'utf8'), context);

const bar = window.DZ.navigation.create('home');
const search = bar.children.find(child => child.className.indexOf('top-search') >= 0);
const input = search.children.find(child => child.tagName === 'INPUT');
if (!input || input.getAttribute('data-nav') !== '1' || input.type !== 'search') throw new Error('visible TV search field missing');
if (bar.children.some(child => child.textContent === 'Arama')) throw new Error('old search navigation tab still exists');
// top bar: no "Ana Sayfa" button; the logo is clickable but not a remote focus stop; search box then ✕ / Listem / Profil / Ayarlar
if (bar.children.some(child => child.textContent === 'Ana Sayfa')) throw new Error('Ana Sayfa button should be gone');
const logo = bar.children[0];
if (logo.textContent !== 'DIZIFLIX' || logo.getAttribute('data-nav') !== null) throw new Error('logo must be plain text, not a focus stop');
const navLabels = node => { const out = []; (function walk(n) { n.children.forEach(c => { if (c.getAttribute('data-nav') === '1' && c.getAttribute('data-nav-off') !== '1') out.push(c.tagName === 'INPUT' ? 'input' : c.textContent); walk(c); }); })(node); return out; };
if (navLabels(bar).join(',') !== 'input,Listem,Profil,Ayarlar') throw new Error('focus order: ' + navLabels(bar).join(','));
const clearBtn = search.children.find(child => child.className === 'top-search-clear');
if (!clearBtn || clearBtn.getAttribute('data-nav') !== '1' || clearBtn.getAttribute('data-nav-off') !== '1' || clearBtn.style.display !== 'none') throw new Error('✕ must exist and stay hidden while empty');
logo.dispatch('click');                                       // already on home: no navigation, no error
if (calls.length) throw new Error('logo click on home navigated');
const mylistBar = window.DZ.navigation.create('mylist');
mylistBar.children[0].dispatch('click');
if (calls.length !== 1 || calls[0].screen !== 'home' || calls[0].params.profile !== 'p1' || calls[0].replace !== true) throw new Error('logo click did not go home');
calls.length = 0;
// the box is empty on non-search screens and the stored query is dropped; the search screen keeps it
values['dz.search.query'] = 'keep me';
const searchBar = window.DZ.navigation.create('search');
if (searchBar.dzInput.value !== 'keep me' || values['dz.search.query'] !== 'keep me') throw new Error('search screen must keep the query');
if (window.DZ.navigation.create('search', 'abc').dzInput.value !== 'abc') throw new Error('explicit query not shown');
const homeBar = window.DZ.navigation.create('home', 'zzz');
if (homeBar.dzInput.value !== '' || values['dz.search.query']) throw new Error('non-search screen must start empty and forget the query');
// ✕ appears once there is text, empties the box and the stored query on click
const hb = homeBar.dzInput, hc = homeBar.dzClear;
hb.value = 'ab'; hb.dispatch('input');
if (hc.style.display !== '' || hc.getAttribute('data-nav-off') !== '0') throw new Error('✕ should show with text');
hc.dispatch('click');
if (hb.value !== '' || hc.style.display !== 'none' || values['dz.search.query']) throw new Error('✕ click should empty the box and the stored query');

// Android ile ayni esik: 2 karakter yerel aramaya yeter, 1 karakter uyari (ekran degismez)
input.value = 'a';
input.dispatch('keydown', { keyCode: 13, preventDefault() {}, stopPropagation() {} });
if (calls.length || input.placeholder !== 'En az 2 harf yazın') throw new Error('short search should stay in the field');
input.value = 'ab';
input.dispatch('keydown', { keyCode: 13, preventDefault() {}, stopPropagation() {} });
if (calls.length !== 1 || calls[0].params.q !== 'ab' || calls[0].params.keepBar !== true || calls[0].params.submit !== true) {
  throw new Error('2-char Enter should run the local search immediately and keep the top bar');
}
calls.length = 0;

input.value = '  dark   city  ';
input.dispatch('keydown', { keyCode: 13, preventDefault() {}, stopPropagation() {} });
if (calls.length !== 1 || calls[0].screen !== 'catalog' || calls[0].params.view !== 'search' || calls[0].params.q !== 'dark city') {
  throw new Error('Enter did not open live search results');
}
if (values['dz.search.query'] !== 'dark city') throw new Error('search query was not retained');

input.value = 'the mentalist';
input.dispatch('keydown', { keyCode: 65376, preventDefault() {}, stopPropagation() {} });
if (calls.length !== 2 || calls[1].params.q !== 'the mentalist') throw new Error('Samsung IME search key did not submit');

let nativeKeydown = null, received = [];
const keyDocument = { addEventListener(type, fn) { if (type === 'keydown') nativeKeydown = fn; } };
const keyWindow = { document: keyDocument, DZ: {} };
const keyContext = vm.createContext({ window: keyWindow, document: keyDocument, console });
vm.runInContext(fs.readFileSync(path.join(__dirname, '../tizen-client/js/keys.js'), 'utf8'), keyContext);
if (keyWindow.DZ.keys.nameOf(65376) !== 'enter' || keyWindow.DZ.keys.nameOf(65385) !== 'back') {
  throw new Error('Samsung IME Done/Cancel codes are not mapped');
}
if (keyWindow.DZ.keys.nameOf(8) !== null || keyWindow.DZ.keys.nameOf(27) !== 'back') {
  throw new Error('desktop Backspace/Escape mapping is incorrect');
}
keyWindow.DZ.keys.onKey(ev => { received.push(ev.name); return true; });
keyWindow.DZ.keys.init();
const typingTarget = { tagName: 'INPUT' };
nativeKeydown({ keyCode: 38, target: typingTarget, preventDefault() {} });
nativeKeydown({ keyCode: 40, target: typingTarget, preventDefault() {} });
nativeKeydown({ keyCode: 37, target: typingTarget, preventDefault() {} });
nativeKeydown({ keyCode: 39, target: typingTarget, preventDefault() {} });
nativeKeydown({ keyCode: 8, target: typingTarget, preventDefault() { throw new Error('Backspace deletion was blocked'); } });
nativeKeydown({ keyCode: 10182, target: typingTarget, preventDefault() {} });
if (received.join(',') !== 'up,down') throw new Error('IME cursor/navigation or Exit isolation is incorrect');
nativeKeydown({ keyCode: 65376, target: typingTarget, preventDefault() {} });
nativeKeydown({ keyCode: 65385, target: typingTarget, preventDefault() {} });
if (received.join(',') !== 'up,down,enter,back') throw new Error('IME submit/cancel did not reach app');

// caret at the edge of the text hands Left/Right to the TV navigation (to reach ✕ / Listem); in the middle the caret keeps them
received.length = 0;
const editing = { tagName: 'INPUT', value: 'abc', selectionStart: 1, selectionEnd: 1 };
nativeKeydown({ keyCode: 39, target: editing, preventDefault() {} });
editing.selectionStart = editing.selectionEnd = 0; nativeKeydown({ keyCode: 37, target: editing, preventDefault() {} });
editing.selectionStart = editing.selectionEnd = 3; nativeKeydown({ keyCode: 39, target: editing, preventDefault() {} });
editing.selectionStart = editing.selectionEnd = 1; nativeKeydown({ keyCode: 37, target: editing, preventDefault() {} });
editing.selectionStart = editing.selectionEnd = 0; nativeKeydown({ keyCode: 39, target: Object.assign({}, editing, { value: '' }), preventDefault() {} });
if (received.join(',') !== 'left,right,right') throw new Error('caret-edge navigation: ' + received.join(','));

const css = fs.readFileSync(path.join(__dirname, '../tizen-client/css/base.css'), 'utf8');
if (!/search-cancel-button[^}]*display:none/.test(css) || !/\.top-search-clear\{/.test(css)) throw new Error('native cancel button must be hidden and ✕ styled');
if (!/\.top-search\{[^}]*width:510px/.test(css) || !/\.top-search-input\{/.test(css)) throw new Error('search field styling missing');
console.log('Navigation search: visible field, validation and Enter submit: OK');
