// "Izlemeye Devam Et" karti: uzun basista (kumanda Tamam/OK ~600 ms, fare/dokunma ~600 ms, sag tik) "Listeden kaldir" menusu.
// Gercek keys/nav/card/row/modal/toast/home/app kodu, sahte DOM + sahte zamanlayici (gorsel test yok, ag yok).
// Kapsam: kisa basis detay acar; uzun basis menu acar; uzun basis sonrasi keyup/click detay ACMAZ ve menu dugmesini tetiklemez;
// yalniz continue satirinda; bolum kartinda item.id gider; basarida kart + bellek/boot onbellegi guncellenir, odak komsuya gider,
// bos satir gizlenir; hata/cevrimdisi; cift istek yok; Geri = Vazgec; api.removeFromContinue istegi.
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, findAll, byAttr, timers, walk } = require('./_fake_dom');

const root = path.join(__dirname, '../tizen-client/js');
const read = f => fs.readFileSync(path.join(root, f), 'utf8');

const ENTER = 13, BACK = 10009, LEFT = 37, UP = 38, RIGHT = 39, DOWN = 40;
const tick = () => new Promise(r => setImmediate(r));

// fake element with real multi-listener dispatch (the shared fake keeps one listener per type)
function mkEl(tag) {
  const n = el(tag), ls = {};
  n.addEventListener = (type, fn) => { (ls[type] = ls[type] || []).push(fn); };
  n.dispatch = (type, ev) => {
    const e = Object.assign({ type, defaultPrevented: false, stopped: false, preventDefault() { e.defaultPrevented = true; }, stopPropagation() { e.stopped = true; } }, ev);
    (ls[type] || []).slice().forEach(f => f(e));
    return e;
  };
  n.click = () => n.dispatch('click');
  n.listenerTypes = () => Object.keys(ls);
  return n;
}

const epA = { id: 'dizi-A', type: 'series', title: 'Dizi A', card_kind: 'episode', card_key: 'episode:dizi-A:s1:e3', episode_id: 'dizi-A:s1:e3',
  episode_label: 'S01 B03 · Fırtına', progress: { episode_id: 'dizi-A:s1:e3', position: 10, duration: 100, pct: 10 } };
const movB = { id: 'film-B', type: 'movie', title: 'Film B', progress: { position: 10, duration: 100, pct: 10 } };
const serC = { id: 'dizi-C', type: 'series', title: 'Dizi C', progress: { episode_id: 'dizi-C:s2:e7', position: 10, duration: 100, pct: 10 } };
const bootData = () => ({
  hero: null, catalog_total: 9,
  rows: [
    { id: 'continue', title: 'İzlemeye Devam Et', loaded: true, items: [epA, movB, serC] },
    { id: 'series', title: 'Diziler', loaded: true, items: [{ id: 'dizi-S', type: 'series', title: 'Dizi S' }, { id: 'dizi-T', type: 'series', title: 'Dizi T' }] }
  ]
});

function env(opts) {
  const o = opts || {};
  const T = timers();
  const docL = {};
  const overlay = mkEl('div'); overlay.id = 'overlay';
  const screenEl = mkEl('div'), appEl = mkEl('div'), stageEl = mkEl('div');
  const byId = { overlay, screen: screenEl, app: appEl, stage: stageEl };
  const document = {
    readyState: 'loading', createElement: mkEl, body: mkEl('body'),
    getElementById: id => byId[id] || null,
    addEventListener(t, fn) { (docL[t] = docL[t] || []).push(fn); }
  };
  const window = { document, DZ: {}, requestAnimationFrame: f => f(), navigator: { onLine: true } };
  if (o.pointer !== false) window.PointerEvent = function () {};
  const DZ = window.DZ;

  const store = { boot: JSON.stringify(bootData()) };
  const calls = [], pending = [], saves = [];
  DZ.api = {
    profileId: () => 'p1', cachedBoot: () => JSON.parse(store.boot),
    saveBoot: (p, d) => { store.boot = JSON.stringify(d); saves.push(p); },
    boot: () => new Promise(() => {}), img: v => v, baseUrl: () => 'http://s', debug: {},
    removeFromContinue(id, pid) { calls.push({ id, pid }); return new Promise((res, rej) => pending.push({ res, rej })); }
  };
  DZ.navigation = {
    create: () => {
      const t = mkEl('div'); t.setAttribute('data-nav-row', 'topbar');
      const b = mkEl('div'); b.setAttribute('data-nav', '1'); b.setAttribute('data-focus-key', 'topbar:home'); t.appendChild(b);
      return t;
    }
  };
  DZ.skeleton = { home: () => mkEl('div') };
  DZ.hero = { create: () => mkEl('div') };
  const detail = [];
  DZ.screens = { detail: { name: 'detail', enter(c, p) { detail.push(p); }, exit() {} } };

  const sandbox = {
    window, document, console: { log() {} }, parseInt,
    setTimeout: T.setTimeout, clearTimeout: T.clearTimeout, setInterval: T.setInterval, clearInterval: T.clearInterval
  };
  vm.createContext(sandbox);
  ['keys.js', 'nav.js', 'ui/toast.js', 'ui/card.js', 'ui/row.js', 'ui/modal.js', 'screens/home.js', 'app.js'].forEach(f => vm.runInContext(read(f), sandbox));
  let opens = 0;
  const realOpen = DZ.modal.open;
  DZ.modal.open = function (cfg) { opens++; return realOpen(cfg); };
  (docL.DOMContentLoaded || []).forEach(f => f());   // app.start()

  const fire = (type, code, extra) => {
    const e = Object.assign({ keyCode: code, target: { tagName: 'DIV' }, preventDefault() {} }, extra);
    (docL[type] || []).forEach(f => f(e));
  };
  const e = {
    T, DZ, window, screenEl, appEl, overlay, calls, pending, saves, detail,
    home: DZ.screens.home,
    opens: () => opens,
    down: (code, extra) => fire('keydown', code, extra),
    up: (code) => fire('keyup', code),
    tap: (code) => { fire('keydown', code); fire('keyup', code); },
    card: id => byAttr(screenEl, 'data-item-id', id),
    continueCards: () => findAll(findAll(screenEl, 'row').find(r => r.getAttribute('data-row-id') === 'continue'), 'card'),
    contRow: () => findAll(screenEl, 'row').find(r => r.getAttribute('data-row-id') === 'continue'),
    toast: () => { const t = findAll(appEl, 'toast')[0]; return t && !t.classList.contains('hidden') ? t.textContent : null; },
    modalOpen: () => DZ.modal.isOpen(),
    modalTitle: () => { let h = null; walk(overlay, n => { if (!h && n.tagName === 'H2') h = n; }); return h ? h.textContent : null; },
    modalMsg: () => { const p = overlay.querySelector('p'); return p ? p.textContent : null; },
    modalFocus: () => { const f = findAll(overlay, 'focused')[0]; return f ? f.textContent : null; },
    modalButtons: () => findAll(overlay, 'btn').map(b => b.textContent),
    focusedItem: () => { const f = findAll(screenEl, 'focused')[0]; return f ? f.getAttribute('data-item-id') : null; },
    cur: () => DZ.nav.current(),
    backHome: () => { DZ.app.back(); T.advance(5); }
  };
  e.T.advance(5);
  return e;
}

function toContinue(x) {
  assert.equal(x.cur().rowId, 'topbar', 'home opens on the top bar row in this fixture');
  x.tap(DOWN);
  assert.equal(x.cur().rowId, 'continue');
  assert.equal(x.cur().col, 0);
}

(async function main() {
  // ---------------------------------------------------------------- 1) keyboard: short press, long press
  {
    const x = env();
    toContinue(x);
    assert.equal(x.focusedItem(), 'dizi-A');

    // short press: detail opens on keyup (not on keydown), target episode kept; no menu, no pending timers left
    x.down(ENTER);
    assert.equal(x.detail.length, 0, 'continue card: selection waits for keyup');
    assert(!x.modalOpen());
    x.T.advance(120);
    assert.equal(x.detail.length, 0);
    x.up(ENTER);
    assert.deepEqual(x.detail, [{ id: 'dizi-A', episodeId: 'dizi-A:s1:e3' }], 'short press opens the detail (episode target kept)');
    assert(!x.modalOpen() && x.opens() === 0);
    x.T.advance(5000);
    assert(!x.modalOpen() && x.opens() === 0, 'no menu after a short press');
    assert.equal(x.calls.length, 0);

    // back to home, focus restored on the same card
    x.DZ.app.back();
    x.T.advance(5);
    assert.equal(x.cur().rowId, 'continue');
    assert.equal(x.focusedItem(), 'dizi-A');

    // long press: menu at 600 ms, not before
    x.down(ENTER);
    x.T.advance(599);
    assert(!x.modalOpen(), 'no menu before 600 ms');
    x.T.advance(1);
    assert(x.modalOpen() && x.opens() === 1, 'menu opens after 600 ms hold');
    assert.equal(x.modalTitle(), 'Dizi A');
    assert.equal(x.modalMsg(), 'S01 B03 · Fırtına', 'episode label shown under the title');
    assert.deepEqual(x.modalButtons(), ['Listeden kaldır', 'Vazgeç']);
    assert.equal(x.modalFocus(), 'Listeden kaldır', 'primary button gets the first focus');
    assert.equal(x.detail.length, 1, 'long press did not open the detail');

    // key still held: auto-repeat keydowns (flagged or not) must not trigger the button, keyup must not open the detail
    x.down(ENTER, { repeat: true });
    x.T.advance(100);
    x.down(ENTER);                       // platform that does not set ev.repeat
    x.T.advance(700);
    x.down(ENTER, { repeat: true });
    x.T.advance(700);
    x.down(ENTER);                       // held far beyond the watchdog window, still refreshed by the repeats
    assert(x.modalOpen(), 'held key never activates the menu button');
    assert.equal(x.calls.length, 0);
    x.up(ENTER);
    assert(x.modalOpen() && x.calls.length === 0 && x.detail.length === 1, 'keyup after a long press neither opens the detail nor fires the button');

    // navigation behind the menu is frozen; Left/Right walk the menu; Back = Vazgec (closes menu only)
    x.down(RIGHT); x.up(RIGHT);
    assert.equal(x.modalFocus(), 'Vazgeç');
    assert.equal(x.cur().col, 0, 'background navigation untouched');
    x.down(LEFT); x.up(LEFT);
    assert.equal(x.modalFocus(), 'Listeden kaldır');
    x.down(DOWN); x.up(DOWN); x.down(UP); x.up(UP);
    assert.equal(x.cur().rowId, 'continue', 'Up/Down do not move the page behind the menu');
    x.tap(BACK);
    assert(!x.modalOpen(), 'Back closes the menu');
    assert.equal(x.DZ.app.currentName(), 'home', 'Back closes the menu, not the screen');
    assert.equal(x.calls.length, 0, 'Back = Vazgec: no request');
    assert.equal(x.focusedItem(), 'dizi-A', 'focus returns to the same card');
    x.tap(RIGHT);
    assert.equal(x.focusedItem(), 'film-B', 'navigation works again after the menu closes');
    x.tap(LEFT);
    assert.equal(x.focusedItem(), 'dizi-A');

    // fresh keydown after the hold ended activates the focused button; "Vazgec" button cancels without a request
    x.down(ENTER); x.T.advance(600); assert(x.modalOpen()); x.up(ENTER);
    x.tap(RIGHT); x.tap(ENTER);
    assert(!x.modalOpen() && x.calls.length === 0, '"Vazgeç" button cancels');
    assert.equal(x.focusedItem(), 'dizi-A');
    assert.equal(x.detail.length, 1);
  }

  // ---------------------------------------------------------------- 2) remove: episode card sends item.id, success updates everything
  {
    const x = env();
    toContinue(x);
    const openLongPress = () => { x.down(ENTER); x.T.advance(600); x.up(ENTER); assert(x.modalOpen()); };

    openLongPress();
    x.tap(ENTER);                                                   // "Listeden kaldir"
    assert(!x.modalOpen());
    assert.deepEqual(x.calls, [{ id: 'dizi-A', pid: 'p1' }], 'episode card sends the production id (item.id), not episode_id');
    assert.equal(x.continueCards().length, 3, 'card stays until the server confirms');

    // double trigger while pending: menu does not reopen, no second request
    x.down(ENTER); x.T.advance(700); x.up(ENTER);
    assert(!x.modalOpen() && x.opens() === 1, 'no second menu while that id is being removed');
    assert.equal(x.calls.length, 1, 'no second request');

    x.pending[0].res({ ok: true, removed: true });
    await tick();
    assert.deepEqual(x.continueCards().map(c => c.getAttribute('data-item-id')), ['film-B', 'dizi-C'], 'card removed from the row');
    assert.equal(x.toast(), 'Listeden kaldırıldı');
    assert.equal(x.cur().rowId, 'continue');
    assert.equal(x.cur().col, 0);
    assert.equal(x.focusedItem(), 'film-B', 'focus moves to the neighbour card');
    const mem = x.home.debugState().data.rows.find(r => r.id === 'continue').items.map(i => i.id);
    assert.deepEqual(mem, ['film-B', 'dizi-C'], 'in-memory boot data updated');
    const cached = x.DZ.api.cachedBoot('p1');
    assert.deepEqual(cached.rows.find(r => r.id === 'continue').items.map(i => i.id), ['film-B', 'dizi-C'], 'boot cache no longer brings it back');
    assert.deepEqual(x.saves, ['p1']);
    assert(x.continueCards().every(c => c.getAttribute('data-col') !== null));

    // the removed id can be requested again later (in-flight set cleared); remove the LAST card: focus goes to the previous one
    x.tap(RIGHT);                                                   // dizi-C (last)
    assert.equal(x.focusedItem(), 'dizi-C');
    openLongPress(); x.tap(ENTER);
    assert.deepEqual(x.calls[1], { id: 'dizi-C', pid: 'p1' });
    x.pending[1].res({ ok: true, removed: false });                 // idempotent "removed:false" is still success
    await tick();
    assert.deepEqual(x.continueCards().map(c => c.getAttribute('data-item-id')), ['film-B']);
    assert.equal(x.focusedItem(), 'film-B', 'last card removed: focus goes to the previous card');

    // remove the only card: row hidden and dropped from nav, focus goes to the row above
    openLongPress(); x.tap(ENTER);
    assert.deepEqual(x.calls[2], { id: 'film-B', pid: 'p1' }, 'movie card sends its id');
    x.pending[2].res({ ok: true, removed: true });
    await tick();
    assert.equal(x.continueCards().length, 0);
    assert(x.contRow().classList.contains('hidden'), 'empty row is hidden');
    assert.equal(x.contRow().getAttribute('data-nav-row'), null, 'empty row leaves the navigation');
    assert.equal(x.cur().rowId, 'topbar', 'focus goes to the row above');
    assert(!x.home.debugState().data.rows.some(r => r.id === 'continue'), 'empty continue row dropped from memory data');
    assert(!x.DZ.api.cachedBoot('p1').rows.some(r => r.id === 'continue'));
    x.tap(DOWN);
    assert.equal(x.cur().rowId, 'series', 'nav rows rebuilt without the empty row');
  }

  // ---------------------------------------------------------------- 3) failure / offline
  {
    const x = env();
    toContinue(x);
    const openLongPress = () => { x.down(ENTER); x.T.advance(600); x.up(ENTER); assert(x.modalOpen()); };

    openLongPress(); x.tap(ENTER);
    x.pending[0].rej(new Error('Sunucu hatasi (404)'));             // endpoint not deployed yet -> expected
    await tick();
    assert.equal(x.toast(), 'Kaldırılamadı, bağlantıyı kontrol edin');
    assert.equal(x.continueCards().length, 3, 'card stays in place on failure');
    assert.equal(x.focusedItem(), 'dizi-A');
    assert.deepEqual(x.saves, [], 'cache untouched on failure');
    assert.equal(x.home.debugState().data.rows[0].items.length, 3);

    // in-flight set is cleared after a failure: retry works
    openLongPress(); x.tap(ENTER);
    assert.equal(x.calls.length, 2, 'retry after a failure sends a new request');
    x.pending[1].res({ ok: true, removed: true });
    await tick();
    assert.equal(x.continueCards().length, 2);

    // offline: no request, dedicated toast, card stays
    const y = env();
    toContinue(y);
    y.window.navigator.onLine = false;
    y.down(ENTER); y.T.advance(600); y.up(ENTER);
    y.tap(ENTER);
    assert.equal(y.calls.length, 0, 'offline: no request');
    assert.equal(y.toast(), 'İnternet bağlantısı gerekli');
    assert.equal(y.continueCards().length, 3);
  }

  // ---------------------------------------------------------------- 4) only the continue row
  {
    const x = env();
    toContinue(x);
    x.tap(DOWN);
    assert.equal(x.cur().rowId, 'series');
    // Enter on another row is unchanged: acts on keydown, immediately
    x.down(ENTER);
    assert.deepEqual(x.detail, [{ id: 'dizi-S' }], 'other rows: selection on keydown, no delay');
    assert.equal(x.T.pending() >= 0, true);
    x.up(ENTER);
    x.T.advance(3000);
    assert(!x.modalOpen() && x.opens() === 0, 'no menu on other rows');
    // no long-press wiring on other rows' cards, present on continue cards
    const seriesCard = x.card('dizi-S');
    assert(!seriesCard.listenerTypes().includes('pointerdown') && !seriesCard.listenerTypes().includes('contextmenu'));
    assert(x.card('dizi-A').listenerTypes().includes('pointerdown') && x.card('dizi-A').listenerTypes().includes('contextmenu'));
    seriesCard.dispatch('contextmenu');
    x.T.advance(1000);
    assert(!x.modalOpen(), 'right click on a non-continue card does nothing');
  }

  // ---------------------------------------------------------------- 5) mouse / pointer
  {
    const x = env();
    toContinue(x);
    const ptr = (card, type, ev) => card.dispatch(type, Object.assign({ pointerType: 'mouse', button: 0, clientX: 100, clientY: 100 }, ev));

    // hold 600 ms -> menu; the click that follows the release is swallowed (no detail), and it moves focus to that card
    let card = x.card('film-B');
    ptr(card, 'pointerdown');
    x.T.advance(599); assert(!x.modalOpen());
    x.T.advance(1); assert(x.modalOpen() && x.opens() === 1);
    assert.equal(x.modalTitle(), 'Film B');
    assert.equal(x.modalMsg(), null, 'no episode label on a movie');
    assert.equal(x.focusedItem(), 'film-B', 'pressed card gets the focus');
    ptr(card, 'pointerup');
    const c1 = card.click();
    assert.equal(x.detail.length, 0, 'click after a long press does not open the detail');
    assert(c1.defaultPrevented);
    x.tap(BACK);
    assert(!x.modalOpen());
    assert.equal(x.focusedItem(), 'film-B');
    // the swallow is one-shot: the next normal click opens the detail
    card.click();
    assert.deepEqual(x.detail, [{ id: 'film-B' }]);
    x.backHome();

    // click on a menu button with the mouse; long press on an unfocused card focuses it first
    const cardC = x.card('dizi-C');
    ptr(cardC, 'pointerdown', { clientX: 5, clientY: 5 });
    x.T.advance(600);
    assert(x.modalOpen() && x.modalTitle() === 'Dizi C');
    assert.equal(x.focusedItem(), 'dizi-C', 'long press on an unfocused card focuses it');
    ptr(cardC, 'pointerup');
    cardC.click();
    assert.equal(x.detail.length, 1, 'release click after the long press swallowed');
    findAll(x.overlay, 'btn')[0].click();
    assert.deepEqual(x.calls, [{ id: 'dizi-C', pid: 'p1' }], 'mouse click on "Listeden kaldır" sends the request');
    x.pending[0].res({ ok: true });
    await tick();
    assert.deepEqual(x.continueCards().map(c => c.getAttribute('data-item-id')), ['dizi-A', 'film-B']);
    assert.equal(x.focusedItem(), 'film-B', 'last card removed: focus on the previous one');

    // movement > 10 px cancels, small jitter does not; early release cancels
    let a = x.card('dizi-A');
    const pa = (type, ev) => a.dispatch(type, Object.assign({ pointerType: 'touch', button: 0, clientX: 50, clientY: 50 }, ev));
    pa('pointerdown'); x.T.advance(300); pa('pointermove', { clientX: 62 }); x.T.advance(400);
    assert(!x.modalOpen(), 'moved > 10 px: cancelled');
    pa('pointerup'); a.click();
    assert.equal(x.detail.length, 2, 'normal tap after a cancelled press opens the detail');
    x.backHome(); a = x.card('dizi-A');
    pa('pointerdown'); pa('pointermove', { clientX: 55, clientY: 54 }); x.T.advance(600);
    assert(x.modalOpen(), 'small jitter keeps the press');
    pa('pointerup'); x.tap(BACK);
    pa('pointerdown'); x.T.advance(300); pa('pointerup'); x.T.advance(600);
    assert(!x.modalOpen(), 'released early: no menu');
    // right mouse button: handled by contextmenu only
    a.dispatch('pointerdown', { pointerType: 'mouse', button: 2, clientX: 1, clientY: 1 });
    x.T.advance(1000);
    assert(!x.modalOpen(), 'right button down alone does not start the hold');
    const cm = a.dispatch('contextmenu');
    assert(cm.defaultPrevented, 'browser context menu suppressed');
    assert(x.modalOpen() && x.modalTitle() === 'Dizi A', 'right click opens the menu');
    const opensBefore = x.opens();
    a.dispatch('contextmenu');
    assert.equal(x.opens(), opensBefore, 'menu is not opened twice while open');
    a.dispatch('pointerup');
    x.tap(BACK);
    a.dispatch('contextmenu');                       // second right click opens again
    assert(x.modalOpen(), 'right click works repeatedly');
    x.tap(BACK);
    x.T.advance(1000);                               // the swallow flag of a right click does not linger
    a.click();
    assert.equal(x.detail.length, 3, 'click works again after a right click');
  }

  // ---------------------------------------------------------------- 5b) hover "✕" button (mouse discoverability)
  {
    const x = env();
    toContinue(x);
    const a = x.card('dizi-A');
    const xs = findAll(x.contRow(), 'card-x');
    assert.equal(xs.length, 3, 'one ✕ per continue card');
    assert.equal(findAll(findAll(x.screenEl, 'row').find(r => r.getAttribute('data-row-id') === 'series'), 'card-x').length, 0, 'other rows have no ✕');
    const xa = a.dzRemoveBtn;
    assert(xa && xa.parentNode === a, '✕ lives inside its card');
    assert.equal(xa.textContent, '✕');
    assert.equal(xa.getAttribute('title'), 'Listeden kaldır');
    assert.equal(xa.getAttribute('aria-label'), 'Listeden kaldır');
    assert.equal(xa.getAttribute('data-nav'), null, '✕ is not a remote-control focus stop');
    assert.equal(findAll(x.screenEl, 'card-x').filter(n => n.getAttribute('data-nav') !== null).length, 0);
    assert.deepEqual(x.DZ.nav.rowIds(), ['topbar', 'continue', 'series']);

    // click: swallowed from the card (no detail), opens the same confirm menu (no direct delete)
    const e = xa.dispatch('click');
    assert(e.stopped, 'click propagation stopped: card click (detail) not triggered');
    assert.equal(x.detail.length, 0, 'clicking ✕ does not open the detail');
    assert.equal(x.calls.length, 0, 'clicking ✕ alone does not delete');
    assert(x.modalOpen() && x.modalTitle() === 'Dizi A' && x.modalMsg() === 'S01 B03 · Fırtına');
    assert.deepEqual(x.modalButtons(), ['Listeden kaldır', 'Vazgeç']);
    x.tap(BACK);
    assert(!x.modalOpen() && x.calls.length === 0);

    // press on ✕ does not start the card's long-press timer
    const d = xa.dispatch('pointerdown', { pointerType: 'mouse', button: 0, clientX: 1, clientY: 1 });
    assert(d.stopped, 'pointerdown stopped at ✕');
    x.T.advance(800);
    assert(!x.modalOpen());

    // ✕ on an unfocused card: menu for THAT card, focus moves to it; then confirm removes it
    const xc = x.card('dizi-C').dzRemoveBtn;
    xc.dispatch('click');
    assert(x.modalOpen() && x.modalTitle() === 'Dizi C');
    assert.equal(x.focusedItem(), 'dizi-C');
    findAll(x.overlay, 'btn')[0].click();
    assert.deepEqual(x.calls, [{ id: 'dizi-C', pid: 'p1' }]);
    x.pending[0].res({ ok: true, removed: true });
    await tick();
    assert.deepEqual(x.continueCards().map(c => c.getAttribute('data-item-id')), ['dizi-A', 'film-B']);
    assert.equal(x.detail.length, 0);

    // hover visibility is pure CSS: only for fine hovering pointers, never on the focused card
    const css = fs.readFileSync(path.join(__dirname, '../tizen-client/css/home.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    assert(/\.card-x\{[^}]*display:none/.test(css), '✕ hidden by default');
    assert(/@media \(hover:hover\) and \(pointer:fine\)\{[^@]*\.card-lp:hover \.card-x\{display:flex;\}/.test(css), '✕ shown on hover only for fine pointers');
    assert(/\.card-lp\.focused \.card-x\{display:none;\}/.test(css), '✕ hidden on the remote-focused card');
    assert(/\.card-x\{[^}]*var\(--accent\)/.test(css), 'accent (yellow) design language');
  }

  // ---------------------------------------------------------------- 6) touch-only browsers (no Pointer Events) + contextmenu-before-timer
  {
    const x = env({ pointer: false });
    toContinue(x);
    const card = x.card('dizi-A');
    assert(card.listenerTypes().includes('touchstart') && !card.listenerTypes().includes('pointerdown'));
    const ts = (type, cx, cy) => card.dispatch(type, { touches: [{ clientX: cx, clientY: cy }], changedTouches: [{ clientX: cx, clientY: cy }] });

    ts('touchstart', 20, 20); x.T.advance(600);
    assert(x.modalOpen() && x.opens() === 1, 'touch hold opens the menu');
    ts('touchend', 20, 20);
    card.click();
    assert.equal(x.detail.length, 0, 'click after touch long press is swallowed');
    x.tap(BACK);

    ts('touchstart', 20, 20); ts('touchmove', 40, 20); x.T.advance(800);
    assert(!x.modalOpen(), 'touch moved: cancelled');
    ts('touchend', 40, 20);

    // browsers that fire contextmenu on long touch before our timer: one menu only
    ts('touchstart', 20, 20); x.T.advance(500);
    card.dispatch('contextmenu');
    assert(x.modalOpen() && x.opens() === 2);
    x.T.advance(200);
    assert.equal(x.opens(), 2, 'timer after contextmenu does not reopen');
    ts('touchend', 20, 20);
    x.tap(BACK);
    assert.equal(x.detail.length, 0);
  }

  // ---------------------------------------------------------------- 7) lost keyup: the "held" state does not stick
  {
    const x = env();
    toContinue(x);
    x.down(ENTER); x.T.advance(600);
    assert(x.modalOpen());
    x.tap(BACK);                         // keyup of the long press never arrived
    x.T.advance(2000);                   // watchdog
    x.tap(ENTER);                        // fresh short press opens the detail again
    assert.deepEqual(x.detail, [{ id: 'dizi-A', episodeId: 'dizi-A:s1:e3' }], 'stuck hold state recovers');
  }

  // ---------------------------------------------------------------- 8) api.removeFromContinue request
  {
    const seen = [];
    const store = { 'dz.profile': 'p9' };
    const window = {
      DZ: {}, location: { search: '' }, setTimeout, clearTimeout,
      localStorage: { getItem: k => (k in store ? store[k] : null), setItem(k, v) { store[k] = String(v); }, removeItem(k) { delete store[k]; } }
    };
    let status = 200, body = '{"ok":true,"removed":true}';
    const sandbox = { window, console: { log() {} }, setTimeout, clearTimeout, encodeURIComponent, JSON, Promise, RegExp, decodeURIComponent,
      fetch: (u, init) => { seen.push({ u, init }); return Promise.resolve({ ok: status < 400, status, text: () => Promise.resolve(body) }); } };
    vm.createContext(sandbox);
    vm.runInContext(read('api.js'), sandbox);
    const api = window.DZ.api;
    const r = await api.removeFromContinue('dizi-A');
    assert.deepEqual(r, { ok: true, removed: true });
    assert.equal(seen[0].u, 'http://192.168.0.61:8090/api/continue/dizi-A?profile=p9', 'default profile, production id in the path');
    assert.equal(seen[0].init.method, 'DELETE');
    assert.equal(seen[0].init.cache, 'no-cache');
    await api.removeFromContinue('a b/c', 'p1');
    assert.equal(seen[1].u, 'http://192.168.0.61:8090/api/continue/a%20b%2Fc?profile=p1', 'id and profile are URL-encoded');
    status = 404; body = '{"error":{"code":"not_found","message":"yok"}}';
    await assert.rejects(() => api.removeFromContinue('x'), e => e.status === 404 && e.code === 'not_found');
    status = 0;
    sandbox.fetch = () => Promise.reject(new Error('net'));
    await assert.rejects(() => api.removeFromContinue('x'), e => e.code === 'network');
  }

  // ---------------------------------------------------------------- 9) keys.onKeyUp normalisation
  {
    const L = {};
    const doc = { addEventListener(t, f) { L[t] = f; } };
    const win = { document: doc, DZ: {} };
    vm.runInContext(read('keys.js'), vm.createContext({ window: win, document: doc, console: { log() {} } }));
    const got = [];
    const off = win.DZ.keys.onKeyUp(e => { got.push(e.name); return true; });
    win.DZ.keys.init();
    L.keyup({ keyCode: 13, target: { tagName: 'DIV' } });
    L.keyup({ keyCode: 13, metaKey: true, target: { tagName: 'DIV' } });
    L.keyup({ keyCode: 82, target: { tagName: 'DIV' } });
    L.keyup({ keyCode: 10009, target: { tagName: 'DIV' } });
    assert.deepEqual(got, ['enter', 'back'], 'keyup is normalised like keydown; modifier shortcuts and unknown keys ignored');
    off(); L.keyup({ keyCode: 13, target: { tagName: 'DIV' } });
    assert.equal(got.length, 2, 'unsubscribe works');
  }

  console.log('Continue remove: long press menu (remote/pointer/touch/contextmenu), short press, suppress, remove/fail/offline, focus, api: OK');
})().catch(e => { console.error(e); process.exit(1); });
