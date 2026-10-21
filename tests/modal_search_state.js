const fs = require('fs');
const path = require('path');
const vm = require('vm');

function element(tag) {
  const listeners = {};
  const node = {
    tagName: String(tag).toUpperCase(), className: '', children: [], parentNode: null,
    attributes: {}, style: {}, textContent: '', value: '', placeholder: '',
    classList: {
      add(name) { if (!node.className.split(/\s+/).includes(name)) node.className = (node.className + ' ' + name).trim(); },
      remove(name) { node.className = node.className.split(/\s+/).filter(x => x && x !== name).join(' '); },
      contains(name) { return node.className.split(/\s+/).includes(name); }
    },
    setAttribute(k, v) { this.attributes[k] = String(v); },
    getAttribute(k) { return this.attributes[k] || null; },
    appendChild(child) { child.parentNode = this; this.children.push(child); return child; },
    removeChild(child) { this.children = this.children.filter(x => x !== child); child.parentNode = null; },
    querySelectorAll(selector) {
      const result = [];
      function visit(parent) {
        parent.children.forEach(child => {
          if (selector === '[data-nav]' && child.attributes['data-nav']) result.push(child);
          visit(child);
        });
      }
      visit(this);
      return result;
    },
    addEventListener(type, fn) { listeners[type] = fn; },
    click() { if (listeners.click) listeners.click({ target: this }); },
    focus() {}, blur() {}, scrollIntoView() {}
  };
  Object.defineProperty(node, 'firstChild', { get() { return this.children[0] || null; } });
  Object.defineProperty(node, 'innerHTML', {
    get() { return ''; },
    set() { this.children.forEach(child => { child.parentNode = null; }); this.children = []; }
  });
  return node;
}

function all(root) {
  const result = [root];
  root.children.forEach(child => result.push(...all(child)));
  return result;
}

async function main() {
  const overlay = element('div');
  let modalKey = null;
  const navigationStates = [];
  const modalWindow = { DZ: {
    keys: { onKey(fn) { modalKey = fn; return function () { modalKey = null; }; } },
    nav: { setEnabled(value) { navigationStates.push(value); } }
  } };
  const modalDocument = { createElement: element, getElementById() { return overlay; } };
  vm.runInContext(
    fs.readFileSync(path.join(__dirname, '../tizen-client/js/ui/modal.js'), 'utf8'),
    vm.createContext({ window: modalWindow, document: modalDocument, console })
  );

  let answer = null;
  modalWindow.DZ.modal.confirm('Çıkış', 'Uygulamadan çıkılsın mı?', () => { answer = true; }, () => { answer = false; });
  const buttons = all(overlay).filter(node => node.attributes['data-nav'] === '1');
  if (!overlay.classList.contains('active') || navigationStates[0] !== false) throw new Error('modal did not lock underlying navigation');
  if (buttons.length !== 2 || buttons[0].textContent !== 'Evet' || buttons[1].textContent !== 'Hayir') throw new Error('exit choices missing');
  if (!buttons[0].classList.contains('focused')) throw new Error('Yes was not initially focused');
  modalKey({ name: 'right' });
  modalKey({ name: 'down' });
  if (!buttons[1].classList.contains('focused')) throw new Error('modal focus escaped Yes/No choices');
  modalKey({ name: 'enter' });
  if (answer !== false || navigationStates[navigationStates.length - 1] !== true || overlay.children.length) throw new Error('modal did not close cleanly');

  const css = fs.readFileSync(path.join(__dirname, '../tizen-client/css/base.css'), 'utf8');
  if (!/#overlay\{[^}]*z-index:10000;[^}]*pointer-events:none/.test(css) || !/#overlay\.active\{pointer-events:auto/.test(css)) {
    throw new Error('modal overlay does not block the full underlying screen');
  }

  let resolveRemote;
  const pendingRemote = new Promise(resolve => { resolveRemote = resolve; });
  const catalogWindow = {
    requestAnimationFrame() {},
    DZ: {
      api: {
        profileId() { return 'p1'; }, baseUrl() { return 'http://example'; },
        catalog() { return Promise.resolve({ items: [], total: 0, genres: [], years: [] }); },
        search() { return pendingRemote; }
      },
      navigation: { create() { return element('nav'); }, go() {}, isTyping() { return false; }, blur() {}, INPUT_COL: 0 },
      nav: { setRoot() {}, onFocus() {}, focusRowById() {}, snapshot() { return null; }, restoreNext() {}, refresh() {}, current() { return null; } },
      card: { create() { return element('div'); } },
      modal: { open() {} }, app: { go() {}, updateParams() {} }
    }
  };
  const catalogDocument = { createElement: element };
  const catalogContext = vm.createContext({ window: catalogWindow, document: catalogDocument, console, Promise, Object, Math, Number, String });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../tizen-client/js/screens/catalog.js'), 'utf8'), catalogContext);
  const container = element('main');
  catalogWindow.DZ.screens.catalog.enter(container, { view: 'search', q: 'dark' });
  if (!all(container).some(node => node.className.includes('catalog-spinner'))) throw new Error('initial search spinner missing');
  await Promise.resolve(); await Promise.resolve();
  if (!all(container).some(node => node.className.includes('catalog-spinner'))) throw new Error('spinner disappeared while remote search was pending');
  if (all(container).some(node => node.className.includes('catalog-empty'))) throw new Error('empty result message appeared before search completed');
  resolveRemote({ items: [], total: 0, genres: [], years: [] });
  await Promise.resolve(); await Promise.resolve();
  if (!all(container).some(node => node.className.includes('catalog-empty'))) throw new Error('completed empty search did not show its final state');

  console.log('Modal focus trap and pending-search spinner: OK');
}

main().catch(error => { console.error(error); process.exit(1); });
