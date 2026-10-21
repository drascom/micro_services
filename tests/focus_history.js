const fs = require('fs');
const path = require('path');
const vm = require('vm');

let ready = null;
const screen = { appendChild() {} };
const stage = {};
const app = { style: {}, appendChild() {} };
const document = {
  readyState: 'loading',
  addEventListener(type, fn) { if (type === 'DOMContentLoaded') ready = fn; },
  getElementById(id) { return id === 'screen' ? screen : id === 'stage' ? stage : app; },
  createElement() { return { id: '', textContent: '' }; }
};

let focus = null;
const restored = [];
const entered = [];
function route(name) {
  return { name, enter() { entered.push(name); }, exit() {} };
}
const window = {
  innerWidth: 1920, innerHeight: 1080,
  addEventListener() {},
  DZ: {
    screens: { home: route('home'), detail: route('detail'), catalog: route('catalog') },
    nav: {
      snapshot() { return focus; }, reset() {}, restoreNext(value) { restored.push(value); }
    },
    keys: { init() {}, onKey() {} },
    api: { profileId() { return 'p1'; }, baseUrl() { return 'http://server'; }, debug: {} },
    modal: { confirm() {}, isOpen() { return false; } }
  }
};
const context = vm.createContext({ window, document, console, setTimeout, tizen: undefined });
vm.runInContext(fs.readFileSync(path.join(__dirname, '../tizen-client/js/app.js'), 'utf8'), context);
ready();

const homeFocus = { rowId: 'movies', col: 7, itemKey: 'data-item-id:movie-7' };
focus = homeFocus;
window.DZ.app.go('detail', { id: 'movie-7' });
focus = { rowId: 'actions', col: 0 };
window.DZ.app.back();
if (window.DZ.app.currentName() !== 'home' || restored[restored.length - 1] !== homeFocus) {
  throw new Error('back did not restore the previous stack entry focus');
}

const newerHomeFocus = { rowId: 'series', col: 4, itemKey: 'data-item-id:series-4' };
focus = newerHomeFocus;
window.DZ.app.go('catalog', { view: 'series' }, true);
focus = { rowId: 'grid_2', col: 1, itemKey: 'data-item-id:series-11' };
window.DZ.app.go('home', { profile: 'p1' }, true);
if (restored[restored.length - 1] !== newerHomeFocus) {
  throw new Error('replace navigation did not restore remembered screen focus');
}

console.log('Navigation history: stack and screen focus restoration: OK');
