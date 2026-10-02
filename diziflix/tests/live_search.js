// Live (as-you-type) search on the TV client, mirroring Android's SearchViewModel: 450 ms debounce, < 2 chars clears, 2 chars local only,
// >= 3 chars local then live (/api/search), Enter searches immediately, stale answers never overwrite newer ones, and the top bar / input
// is NEVER rebuilt while typing (focus, caret, value and the TV on-screen keyboard survive). Real nav.js + navigation.js + catalog.js + app.js
// on the shared fake DOM, fake timers and a controllable fake API (no network).
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, find, findAll, walk, timers } = require('./_fake_dom');

const read = f => fs.readFileSync(path.join(__dirname, '../tizen-client/js', f), 'utf8');
const tick = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
const item = (id, extra) => Object.assign({ id, title: id.toUpperCase(), year: 2020, type: 'movie', availability: { state: 'ready' } }, extra || {});
const page = items => ({ items, total: items.length, genres: [], years: [] });

function world(opts) {
  opts = opts || {};
  const T = timers();
  const container = el('div');
  const removed = [];
  const plainRemove = container.removeChild;
  container.removeChild = function (child) { removed.push(child); return plainRemove.call(container, child); };
  Object.defineProperty(container, 'innerHTML', { get() { return ''; }, set() { while (container.firstChild) container.removeChild(container.firstChild); } });   // movies/mylist views clear the screen this way
  const store = {};
  const log = { go: [], catalog: [], search: [], images: [] };
  let ready = null;
  const handlers = [];
  const document = {
    readyState: 'loading', activeElement: null,
    addEventListener(type, fn) { if (type === 'DOMContentLoaded') ready = fn; },
    getElementById(id) { return id === 'screen' ? container : { style: {}, appendChild() {} }; },
    createElement(tag) {
      const n = el(tag);
      if (String(tag).toLowerCase() === 'input') {
        n.focus = () => { document.activeElement = n; };
        n.blur = () => { if (document.activeElement === n) document.activeElement = null; };
      }
      return n;
    }
  };
  function request(list, args, signal) {
    return new Promise((resolve, reject) => { list.push({ args, signal, resolve, reject }); });
  }
  class FakeAbort { constructor() { this.signal = { aborted: false }; } abort() { this.signal.aborted = true; } }
  const DZ = {
    store: { get: (k, d) => (k in store ? store[k] : d), set: (k, v) => { store[k] = v; }, del: k => { delete store[k]; } },
    api: {
      profileId: () => 'p1', baseUrl: () => 'http://s', debug: {},
      catalog: (params, o) => request(log.catalog, params, o && o.signal),
      search: (q, pid, limit, o) => request(log.search, { q, pid, limit }, o && o.signal)
    },
    card: {
      create(it, o) {
        const n = el('div');
        n.setAttribute('data-nav', '1'); n.setAttribute('data-item-id', it.id);
        n.dzLoadImage = () => log.images.push('load:' + it.id);
        n.dzUnloadImage = () => log.images.push('unload:' + it.id);
        n.select = () => o.onSelect(it);
        return n;
      }
    },
    modal: { isOpen: () => false, confirm() {}, open() {} },
    keys: { init() {}, onKey(fn) { handlers.push(fn); } },
    screens: {
      home: {
        enter(cnt) {
          while (cnt.firstChild) cnt.removeChild(cnt.firstChild);
          cnt.appendChild(DZ.navigation.create('home'));
          const pg = el('div'); pg.id = 'page';
          const hero = el('div'); hero.setAttribute('data-nav-row', 'hero');
          const play = el('div'); play.setAttribute('data-nav', '1'); hero.appendChild(play); pg.appendChild(hero);
          for (let i = 0; i < 12; i++) pg.appendChild(el('div'));
          cnt.appendChild(pg);
          DZ.nav.setRoot(cnt, pg);
        },
        exit() {}
      },
      detail: { enter(cnt) { while (cnt.firstChild) cnt.removeChild(cnt.firstChild); cnt.appendChild(el('div')); }, exit() {} },
      settings: { enter(cnt) { while (cnt.firstChild) cnt.removeChild(cnt.firstChild); cnt.appendChild(el('div')); }, exit() {} }
    }
  };
  const window = { innerWidth: 1920, innerHeight: 1080, addEventListener() {}, setTimeout: T.setTimeout, clearTimeout: T.clearTimeout,
    requestAnimationFrame: fn => T.setTimeout(fn, 0), document, DZ };
  if (opts.abort !== false) window.AbortController = FakeAbort;
  const ctx = vm.createContext({ window, document, console: { log() {} }, setTimeout: T.setTimeout, clearTimeout: T.clearTimeout, Promise, Object, Math, Number, String, Array, tizen: undefined });
  ['nav.js', 'ui/navigation.js', 'screens/catalog.js', 'app.js'].forEach(f => vm.runInContext(read(f), ctx));
  ready();
  const realGo = DZ.app.go;
  DZ.app.go = function () { log.go.push(Array.prototype.slice.call(arguments)); return realGo.apply(DZ.app, arguments); };

  const w = { T, DZ, log, key: ev => handlers[handlers.length - 1](ev), store, container, removed, document,
    bar: () => container.children.find(c => String(c.className).indexOf('main-navigation') >= 0),
    input: () => w.bar().dzInput,
    screen: () => DZ.app.currentName(),
    top: () => DZ.app.stack()[DZ.app.stack().length - 1],
    /* the user focuses the field on the TV (Enter on it) and types */
    focusInput() { DZ.nav.focusRowById('topbar', 0); assert.strictEqual(document.activeElement, w.input(), 'input has DOM focus'); },
    type(value) { const input = w.input(); input.value = value; input.listeners.input(); },
    enter() { w.input().listeners.keydown({ keyCode: 13, preventDefault() {}, stopPropagation() {} }); },
    heading: () => { const h = find(container, 'catalog-heading'); return h ? h.children.map(c => c.textContent).join(' | ') : ''; },
    ids: () => { const out = []; walk(container, n => { if (n.getAttribute && n.getAttribute('data-item-id') !== null && n.getAttribute('data-nav') === '1') out.push(n.getAttribute('data-item-id')); }); return out; },
    has: cls => findAll(container, cls).length > 0,
    cur: () => DZ.nav.current(),
    pendingCatalog: () => log.catalog.filter(c => !c.done),
    async answer(list, index, value, fail) { const c = list[index]; c.done = true; if (fail) c.reject(value); else c.resolve(value); await tick(); }
  };
  return w;
}

(async () => {
  // ---- 1. debounce + single transition from another screen, top bar kept as the SAME node ----
  {
    const w = world();
    assert.strictEqual(w.screen(), 'home');
    w.focusInput();
    const bar = w.bar(), input = w.input();
    w.type('d'); w.T.advance(5000);
    assert.strictEqual(w.screen(), 'home', '1 char never searches'); assert.strictEqual(w.log.catalog.length, 0);
    w.type('da'); w.T.advance(300); w.type('dar'); w.T.advance(300); w.type('dark'); w.T.advance(449);
    assert.strictEqual(w.screen(), 'home', 'nothing before 450 ms of silence'); assert.strictEqual(w.log.catalog.length + w.log.search.length, 0);
    assert.strictEqual(w.DZ.navigation.isTyping(), true, 'typing in progress');
    w.T.advance(1);
    assert.strictEqual(w.screen(), 'catalog'); assert.strictEqual(w.DZ.app.stack().length, 1, 'replaces the home entry (as before)');
    assert.strictEqual(w.log.catalog.length, 1, 'rapid keystrokes -> ONE local request'); assert.strictEqual(w.log.search.length, 1, 'and ONE live request');
    assert.strictEqual(w.log.catalog[0].args.q, 'dark'); assert.strictEqual(w.log.search[0].args.q, 'dark');
    assert.strictEqual(w.bar(), bar, 'top bar node is the same'); assert.strictEqual(w.input(), input, 'input node is the same');
    assert(!w.removed.includes(bar) && !w.removed.includes(input), 'top bar / input was never removed from the DOM');
    assert.strictEqual(input.parentNode.parentNode, bar, 'input still inside its bar'); assert.strictEqual(bar.parentNode, w.container);
    assert.strictEqual(input.value, 'dark', 'typed value untouched');
    assert.strictEqual(w.document.activeElement, input, 'input keeps DOM focus (on-screen keyboard stays)');
    assert.deepStrictEqual([w.cur().rowId, w.cur().col], ['topbar', 0], 'nav focus on the input');
    assert.strictEqual(w.container.children.length, 2, 'only bar + results page remain');
    assert(String(find(w.bar(), 'top-search').className).indexOf('selected') >= 0, 'search tab active');
    assert.strictEqual(w.store['dz.search.query'], 'dark', 'query retained');
    assert.strictEqual(w.top().params.q, 'dark'); assert.strictEqual(w.top().params.keepBar, undefined, 'one-shot flags are not left on the history entry');
    assert(w.has('search-loading'), 'spinner while waiting for the first answer');
    assert.strictEqual(w.DZ.navigation.isTyping(), true, 'still typing (input focused)');

    // local first (instant), live afterwards
    await w.answer(w.log.catalog, 0, page([item('a'), item('b'), item('c')]));
    assert.deepStrictEqual(w.ids(), ['a', 'b', 'c']);
    assert(/3 yapım · “dark” · Kaynakta aranıyor…/.test(w.heading()), 'local results + live indicator: ' + w.heading());
    assert.strictEqual(w.document.activeElement, input, 'focus stays in the input after local results');
    assert.deepStrictEqual([w.cur().rowId, w.cur().col], ['topbar', 0]);
    assert.strictEqual(w.bar(), bar); assert(!w.removed.includes(bar));
    assert(w.log.images.includes('load:a'), 'first rows get their images without focus being on a card');
    await w.answer(w.log.search, 0, page([item('a'), item('b'), item('c'), item('d')]));
    assert.deepStrictEqual(w.ids(), ['a', 'b', 'c', 'd']); assert(!/Kaynakta aranıyor/.test(w.heading()), 'live indicator gone: ' + w.heading());
    assert.strictEqual(w.document.activeElement, input, 'focus stays in the input after live results');
    assert(!w.removed.includes(bar) && !w.removed.includes(input));

    // ---- 2. next keystrokes update ONLY the results: no screen change, old requests cancelled ----
    const goCount = w.log.go.length;
    w.type('darkn'); w.T.advance(450);
    assert.strictEqual(w.log.go.length, goCount, 'no screen navigation while typing in the search view');
    assert.strictEqual(w.log.catalog.length, 2); assert.strictEqual(w.log.search.length, 2);
    assert.strictEqual(w.log.catalog[1].args.q, 'darkn');
    assert.deepStrictEqual(w.ids(), ['a', 'b', 'c', 'd'], 'old results stay until the new local answer arrives (no flicker)');
    assert.strictEqual(w.bar(), bar); assert.strictEqual(w.document.activeElement, input);
    assert.strictEqual(w.store['dz.search.query'], 'darkn'); assert.strictEqual(w.top().params.q, 'darkn', 'history entry follows the query');

    // ---- 3. stale answers never overwrite newer results ----
    w.type('darkni'); w.T.advance(450);
    assert.strictEqual(w.log.catalog.length, 3);
    assert.strictEqual(w.log.catalog[1].signal.aborted, true, 'previous local request aborted (AbortController)');
    assert.strictEqual(w.log.search[1].signal.aborted, true, 'previous live request aborted');
    await w.answer(w.log.catalog, 2, page([item('n1')]));
    assert.deepStrictEqual(w.ids(), ['n1']);
    await w.answer(w.log.catalog, 1, page([item('stale-local')]));           // late answer to 'darkn'
    await w.answer(w.log.search, 1, page([item('stale-live')]));
    assert.deepStrictEqual(w.ids(), ['n1'], 'stale answers ignored');
    assert(/“darkni”/.test(w.heading()) && /Kaynakta aranıyor/.test(w.heading()), w.heading());
    await w.answer(w.log.search, 2, page([item('n1'), item('n2')]));
    assert.deepStrictEqual(w.ids(), ['n1', 'n2']);

    // ---- 4. < 2 chars: clear immediately, cancel everything, no request ----
    w.type('darknix'); w.T.advance(450);                                      // a search in flight...
    const inFlight = w.log.catalog.length;
    w.type('d');                                                                // ...user deletes down to 1 char
    assert(w.has('catalog-empty') && find(w.container, 'catalog-empty').textContent === 'Aramak için bir film veya dizi adı girin.', 'empty hint');
    assert.deepStrictEqual(w.ids(), [], 'results cleared at once'); assert(!/yapım/.test(w.heading()), 'no stale count: ' + w.heading());
    assert.strictEqual(w.T.pending(), 0, 'no timer left (nothing scheduled for 1 char)');
    await w.answer(w.log.catalog, inFlight - 1, page([item('late')])); await w.answer(w.log.search, inFlight - 1, page([item('late2')]));
    assert.deepStrictEqual(w.ids(), [], 'late answers to a cleared query are ignored');
    w.T.advance(5000); assert.strictEqual(w.log.catalog.length, inFlight, 'no request for 1 char');
    assert.strictEqual(w.store['dz.search.query'], 'd'); assert.strictEqual(w.top().params.q, 'd');
    assert.strictEqual(w.bar(), bar); assert.strictEqual(w.document.activeElement, input); assert(!w.removed.includes(bar) && !w.removed.includes(input));
    w.type(''); assert(w.has('catalog-empty'), 'empty input keeps the hint');

    // ---- 5. retyping the same query while it is still running does not refetch ----
    w.type('matrix'); w.T.advance(450);
    const n = w.log.catalog.length;
    w.type('matrixx'); w.T.advance(200); w.type('matrix'); w.T.advance(450);
    assert.strictEqual(w.log.catalog.length, n, 'same query already running: no duplicate request');
    assert.strictEqual(w.log.search.length, n);
  }

  // ---- 6. 2 chars: local catalog only, no live request ----
  {
    const w = world();
    w.focusInput(); w.type('da'); w.T.advance(450);
    assert.strictEqual(w.log.catalog.length, 1); assert.strictEqual(w.log.search.length, 0, 'live search needs >= 3 chars');
    await w.answer(w.log.catalog, 0, page([item('dd')]));
    assert.deepStrictEqual(w.ids(), ['dd']); assert(!/Kaynakta aranıyor/.test(w.heading()), w.heading());
    w.type('dar'); w.T.advance(450);
    assert.strictEqual(w.log.catalog.length, 2); assert.strictEqual(w.log.search.length, 1, '3rd char adds the live request');
    assert.strictEqual(w.log.search[0].args.q, 'dar'); assert.strictEqual(w.log.search[0].args.limit, 20);
  }

  // ---- 7. live source failure keeps local results + note; both orders ----
  {
    const w = world();
    w.focusInput(); w.type('dark'); w.T.advance(450);
    await w.answer(w.log.catalog, 0, page([item('a')]));
    await w.answer(w.log.search, 0, { message: 'down' }, true);
    assert.deepStrictEqual(w.ids(), ['a'], 'local results stay');
    assert(/Canlı kaynak şu an yanıt vermedi, yerel sonuçlar gösteriliyor/.test(w.heading()) && !/Kaynakta aranıyor/.test(w.heading()), w.heading());
    const w2 = world();
    w2.focusInput(); w2.type('dark'); w2.T.advance(450);
    await w2.answer(w2.log.search, 0, { message: 'down' }, true);             // live fails BEFORE local arrives
    assert(w2.has('search-loading'), 'still waiting for local');
    await w2.answer(w2.log.catalog, 0, page([item('a'), item('b')]));
    assert.deepStrictEqual(w2.ids(), ['a', 'b']); assert(/Canlı kaynak şu an yanıt vermedi/.test(w2.heading()), w2.heading());
    const w3 = world();                                                        // server answers with remote_error
    w3.focusInput(); w3.type('dark'); w3.T.advance(450);
    await w3.answer(w3.log.catalog, 0, page([item('a')]));
    await w3.answer(w3.log.search, 0, Object.assign(page([item('a')]), { remote_error: 'timeout' }));
    assert(/Canlı kaynak şu an yanıt vermedi/.test(w3.heading()), w3.heading());
    const w4 = world();                                                        // live answers before local: local must not overwrite it
    w4.focusInput(); w4.type('dark'); w4.T.advance(450);
    await w4.answer(w4.log.search, 0, page([item('live1'), item('live2')]));
    await w4.answer(w4.log.catalog, 0, page([item('loc')]));
    assert.deepStrictEqual(w4.ids(), ['live1', 'live2'], 'late local answer does not replace the live results');
    assert(!/Kaynakta aranıyor/.test(w4.heading()));
    const w5 = world();                                                        // everything fails -> error + retry
    w5.focusInput(); w5.type('dark'); w5.T.advance(450);
    await w5.answer(w5.log.catalog, 0, { message: 'Sunucu hatasi (500)' }, true); await w5.answer(w5.log.search, 0, { message: 'Sunucu hatasi (500)' }, true);
    assert(findAll(w5.container, 'catalog-loading').some(n => /Sunucu hatasi/.test(n.textContent)), 'error shown');
    assert.strictEqual(w5.DZ.nav.rowIds().includes('retry'), true, 'retry row focusable');
    assert.strictEqual(w5.document.activeElement, w5.input(), 'input still focused after an error');
  }

  // ---- 8. Enter searches immediately, then moves to the results; no double request ----
  {
    const w = world();
    w.focusInput(); w.type('dark'); w.enter();
    assert.strictEqual(w.log.catalog.length, 1, 'Enter does not wait for the debounce'); assert.strictEqual(w.log.search.length, 1);
    assert.strictEqual(w.screen(), 'catalog'); assert.strictEqual(w.top().params.submit, undefined);
    w.T.advance(5000); assert.strictEqual(w.log.catalog.length, 1, 'pending debounce was cancelled by Enter');
    w.enter(); assert.strictEqual(w.log.catalog.length, 1, 'Enter for the query that is already running does not refetch');
    const bar = w.bar();
    await w.answer(w.log.catalog, 0, page([item('a'), item('b')]));
    assert.strictEqual(w.cur().rowId, 'grid_0', 'Enter: focus goes to the first result'); assert.notStrictEqual(w.document.activeElement, w.input(), 'input released so left/right work');
    assert.strictEqual(w.bar(), bar);
    await w.answer(w.log.search, 0, page([item('a'), item('b'), item('c')]));
    assert.strictEqual(w.cur().rowId, 'grid_0'); assert.strictEqual(w.cur().itemKey, 'data-item-id:a');
    // short query: warning, nothing runs
    const w2 = world();
    w2.focusInput(); w2.type('a'); w2.enter();
    assert.strictEqual(w2.screen(), 'home'); assert.strictEqual(w2.log.catalog.length, 0);
    assert(/invalid/.test(find(w2.bar(), 'top-search').className), 'invalid state'); assert.strictEqual(w2.input().placeholder, 'En az 2 harf yazın');
    w2.type('ab'); assert.strictEqual(w2.input().placeholder, 'Film veya dizi ara', 'warning cleared on next keystroke');
    w2.enter(); assert.strictEqual(w2.log.catalog.length, 1, 'Android parity: 2 chars are enough for Enter (local)'); assert.strictEqual(w2.log.search.length, 0);
    // IME "Done" goes through dzSubmitSearch
    const w3 = world();
    w3.focusInput(); w3.type('lost'); w3.input().dzSubmitSearch();
    assert.strictEqual(w3.log.catalog.length, 1);
    // Enter inside the search view searches in place (new query), no screen change
    w.focusInput(); w.type('other'); const goCount = w.log.go.length; w.enter();
    assert.strictEqual(w.log.go.length, goCount); assert.strictEqual(w.log.catalog.length, 2); assert.strictEqual(w.log.catalog[1].args.q, 'other');
    await w.answer(w.log.catalog, 1, page([item('o1'), item('o2')]));
    assert.strictEqual(w.cur().rowId, 'grid_0');
  }

  // ---- 9. result navigation still works: down into the grid and back up to the input; re-render keeps a focused card ----
  {
    const w = world();
    w.focusInput(); w.type('dark'); w.T.advance(450);
    await w.answer(w.log.catalog, 0, page([item('a'), item('b'), item('c'), item('d'), item('e'), item('f')]));
    w.input().blur();                                                          // app.js does this for Down while typing
    assert.strictEqual(w.DZ.nav.move('down'), true); assert.strictEqual(w.cur().rowId, 'grid_0');
    assert.strictEqual(w.DZ.nav.move('right'), true); assert.strictEqual(w.cur().itemKey, 'data-item-id:b');
    await w.answer(w.log.search, 0, page([item('a'), item('b'), item('c'), item('d'), item('e'), item('f'), item('g')]));   // live results arrive while a card is focused
    assert.strictEqual(w.cur().itemKey, 'data-item-id:b', 'focused card survives the re-render');
    assert.notStrictEqual(w.document.activeElement, w.input(), 'typing focus was not stolen back');
    assert.strictEqual(w.DZ.nav.move('up'), true);
    assert.deepStrictEqual([w.cur().rowId, w.cur().col], ['topbar', 0], 'Up returns to the input (column 0)');
    assert.strictEqual(w.document.activeElement, w.input());
    // focus on a card: a later typed query must not be disturbed (input focused again -> keeps typing focus)
    w.type('darkk'); w.T.advance(450); await w.answer(w.log.catalog, 1, page([item('z')]));
    assert.strictEqual(w.document.activeElement, w.input()); assert.strictEqual(w.cur().rowId, 'topbar');
  }

  // ---- 10. leaving for another screen cancels the pending debounce; back from a detail shows the latest query ----
  {
    const w = world();
    w.focusInput(); w.type('dark'); w.T.advance(200);
    w.DZ.app.go('settings');
    w.T.advance(5000);
    assert.strictEqual(w.screen(), 'settings', 'a debounced search does not hijack another screen'); assert.strictEqual(w.log.catalog.length, 0);
    const w2 = world();
    w2.focusInput(); w2.type('dark'); w2.T.advance(450);
    await w2.answer(w2.log.catalog, 0, page([item('a')])); await w2.answer(w2.log.search, 0, page([item('a'), item('b')]));
    w2.type('darkest'); w2.T.advance(450);
    await w2.answer(w2.log.catalog, 1, page([item('x'), item('y')])); await w2.answer(w2.log.search, 1, page([item('x'), item('y')]));
    find(w2.container, 'catalog-tile').children[0].select();                   // open a card
    assert.strictEqual(w2.screen(), 'detail');
    w2.DZ.app.back();
    assert.strictEqual(w2.screen(), 'catalog'); assert.strictEqual(w2.log.catalog[2].args.q, 'darkest', 'returns to the latest typed query');
    assert.strictEqual(w2.input().value, 'darkest', 'rebuilt bar shows the latest query');
    await w2.answer(w2.log.catalog, 2, page([item('x'), item('y')]));
    assert.strictEqual(w2.cur().rowId.indexOf('grid_'), 0, 'resume lands on results');
  }

  // ---- 11. old WebView without AbortController: sequence numbers ignore stale answers ----
  {
    const w = world({ abort: false });
    w.focusInput(); w.type('dark'); w.T.advance(450);
    w.type('darkn'); w.T.advance(450);
    assert.strictEqual(w.log.catalog[0].signal, undefined, 'no signal without AbortController');
    await w.answer(w.log.catalog, 1, page([item('new')]));
    await w.answer(w.log.catalog, 0, page([item('old')])); await w.answer(w.log.search, 0, page([item('old-live')]));
    assert.deepStrictEqual(w.ids(), ['new'], 'stale answer ignored by sequence number');
    await w.answer(w.log.search, 1, page([item('new'), item('new2')]));
    assert.deepStrictEqual(w.ids(), ['new', 'new2']);
  }

  // ---- 12. other views keep working (filters/paging are untouched) and red/back behaviours ----
  {
    const w = world();
    w.DZ.app.go('catalog', { view: 'movies' }, true);
    assert.strictEqual(w.log.catalog.length, 1, 'movies view loads the catalog');
    assert.strictEqual(w.log.catalog[0].args.type, 'movie'); assert.strictEqual(w.log.search.length, 0);
    w.focusInput(); w.type('dark'); w.T.advance(450);
    assert.strictEqual(w.screen(), 'catalog'); assert.strictEqual(w.top().params.view, 'search', 'typing on the movies view opens the search view');
    assert.strictEqual(w.log.catalog[1].args.q, 'dark');
  }


  // ---- 13. top bar: no "Ana Sayfa" button; the logo is clickable (mouse) but NOT a remote focus stop; focus order starts at the search box ----
  {
    const w = world();
    const bar = w.bar();
    assert(!bar.children.some(c => c.textContent === 'Ana Sayfa'), 'no Ana Sayfa button');
    assert.strictEqual(bar.dzLogo.textContent, 'DIZIFLIX'); assert.strictEqual(bar.dzLogo.getAttribute('data-nav'), null, 'logo is not a remote focus stop');
    const order = () => w.bar().querySelectorAll('[data-nav]').filter(n => n.getAttribute('data-nav-off') !== '1').map(n => (n.tagName === 'INPUT' ? 'input' : n.textContent));
    assert.deepStrictEqual(order(), ['input', 'Listem', 'Profil', 'Ayarlar'], 'focus order (✕ hidden while empty)');
    // on the home screen a logo click just scrolls back to the top, no navigation, no error
    w.DZ.nav.focusRowById('hero', 0);
    const goCount = w.log.go.length;
    w.DZ.nav.move('up');
    bar.dzLogo.listeners.click();
    assert.strictEqual(w.log.go.length, goCount, 'already home: no navigation'); assert.strictEqual(w.cur().rowId, 'hero', 'back to the top of home');
    // from the search view it goes home, with an empty box and no stored query
    w.focusInput(); w.type('dark'); w.T.advance(450);
    assert.strictEqual(w.top().params.view, 'search');
    w.bar().dzLogo.listeners.click();
    assert.strictEqual(w.screen(), 'home'); assert.strictEqual(w.input().value, ''); assert.strictEqual(w.store['dz.search.query'], undefined);
  }

  // ---- 14. ✕ clear button: remote-focusable only while the box has text; empties box, results and stored query, focus returns to the box ----
  {
    const w = world();
    const order = () => w.bar().querySelectorAll('[data-nav]').filter(n => n.getAttribute('data-nav-off') !== '1').map(n => (n.tagName === 'INPUT' ? 'input' : n.textContent));
    w.focusInput();
    assert.strictEqual(w.bar().dzClear.style.display, 'none', 'hidden while empty');
    w.type('da');
    assert.strictEqual(w.bar().dzClear.style.display, '', 'visible once there is text');
    assert.deepStrictEqual(order(), ['input', '✕', 'Listem', 'Profil', 'Ayarlar']);
    // caret at the end + Right (keys.js only forwards it then): leaves the box for the ✕ and closes the keyboard
    w.key({ name: 'right', typing: true, native: { target: w.input() } });
    assert.strictEqual(w.DZ.nav.currentEl(), w.bar().dzClear, 'Right from the box reaches ✕'); assert.notStrictEqual(w.document.activeElement, w.input(), 'box released');
    w.key({ name: 'left', typing: false, native: {} });
    assert.strictEqual(w.DZ.nav.currentEl(), w.input()); assert.strictEqual(w.document.activeElement, w.input(), 'Left returns into the box');
    w.key({ name: 'right', typing: true, native: { target: w.input() } });
    w.T.advance(450);                                                            // the debounce fires while the user is on ✕
    assert.strictEqual(w.DZ.nav.currentEl(), w.bar().dzClear, 'the search transition does not steal focus back from ✕');
    assert.notStrictEqual(w.document.activeElement, w.input());
    await w.answer(w.log.catalog, 0, page([item('a'), item('b')]));
    assert.deepStrictEqual(w.ids(), ['a', 'b']);
    w.key({ name: 'right', typing: false, native: {} });
    assert.strictEqual(w.DZ.nav.currentEl().textContent, 'Listem', 'Right from ✕ continues along the bar');
    w.key({ name: 'left', typing: false, native: {} });
    assert.strictEqual(w.DZ.nav.currentEl(), w.bar().dzClear);
    const bar = w.bar(), input = w.input();
    w.key({ name: 'enter', typing: false, native: {} });                        // Enter on ✕
    assert.strictEqual(input.value, ''); assert.strictEqual(w.bar().dzClear.style.display, 'none'); assert.deepStrictEqual(order(), ['input', 'Listem', 'Profil', 'Ayarlar']);
    assert.strictEqual(w.store['dz.search.query'], undefined, 'stored query removed');
    assert.deepStrictEqual(w.ids(), [], 'results cleared'); assert.strictEqual(find(w.container, 'catalog-empty').textContent, 'Aramak için bir film veya dizi adı girin.');
    assert.strictEqual(w.document.activeElement, input, 'focus given back to the box'); assert.deepStrictEqual([w.cur().rowId, w.cur().col], ['topbar', 0]);
    assert.strictEqual(w.bar(), bar, 'same bar'); assert(!w.removed.includes(bar) && !w.removed.includes(input));
    assert.strictEqual(w.top().params.q, ''); w.T.advance(5000); assert.strictEqual(w.log.catalog.length, 1, 'clearing never searches');
    // Cancel (IME) only closes the keyboard: nav focus stays on the box, nothing jumps
    w.type('xy'); w.key({ name: 'back', typing: true, native: { target: input } });
    assert.notStrictEqual(w.document.activeElement, input); assert.deepStrictEqual([w.cur().rowId, w.cur().col], ['topbar', 0]);
    // click (mouse) works too, also on the home screen
    const w2 = world();
    w2.focusInput(); w2.type('abc'); assert.strictEqual(w2.bar().dzClear.style.display, '');
    w2.bar().dzClear.click();
    assert.strictEqual(w2.input().value, ''); assert.strictEqual(w2.screen(), 'home'); assert.strictEqual(w2.document.activeElement, w2.input());
    w2.T.advance(5000); assert.strictEqual(w2.log.catalog.length, 0, 'the pending debounce died with the clear');
  }

  // ---- 15. leaving search for a non-search screen empties the box + stored query; coming back from a detail keeps them ----
  {
    const searched = async () => {
      const w = world();
      w.focusInput(); w.type('dark'); w.T.advance(450);
      await w.answer(w.log.catalog, 0, page([item('a')])); await w.answer(w.log.search, 0, page([item('a')]));
      assert.strictEqual(w.store['dz.search.query'], 'dark'); return w;
    };
    // Back key (catalog.back -> home)
    let w = await searched();
    w.DZ.screens.catalog.back();
    assert.strictEqual(w.screen(), 'home'); assert.strictEqual(w.input().value, '', 'box empty on Ana Sayfa'); assert.strictEqual(w.store['dz.search.query'], undefined);
    assert.strictEqual(w.bar().dzClear.style.display, 'none');
    // the search view itself starts clean afterwards (no stale memory of the old query)
    const before = w.log.catalog.length;
    w.DZ.app.go('catalog', { view: 'search' }, true);
    assert.strictEqual(w.log.catalog.length, before, 'no automatic re-search of the old query'); assert.strictEqual(w.input().value, '');
    assert.strictEqual(find(w.container, 'catalog-empty').textContent, 'Aramak için bir film veya dizi adı girin.');
    // Listem
    w = await searched();
    w.bar().children.find(c => c.textContent === 'Listem').click();
    assert.strictEqual(w.top().params.view, 'mylist'); assert.strictEqual(w.input().value, ''); assert.strictEqual(w.store['dz.search.query'], undefined);
    // Ayarlar / Profil buttons: box + store emptied now; returning shows an empty search
    w = await searched();
    const calls = w.log.catalog.length;
    w.bar().children.find(c => c.textContent === 'Ayarlar').click();
    assert.strictEqual(w.screen(), 'settings'); assert.strictEqual(w.store['dz.search.query'], undefined);
    w.DZ.app.back();
    assert.strictEqual(w.screen(), 'catalog'); assert.strictEqual(w.input().value, ''); assert.strictEqual(w.log.catalog.length, calls, 'empty search, no request');
    assert.strictEqual(find(w.container, 'catalog-empty').textContent, 'Aramak için bir film veya dizi adı girin.');
    w = await searched();
    w.bar().children.find(c => c.textContent === 'Profil').click();
    assert.strictEqual(w.store['dz.search.query'], undefined);
    // detail round trip keeps everything (existing behaviour)
    w = await searched();
    find(w.container, 'catalog-tile').children[0].select();
    assert.strictEqual(w.screen(), 'detail'); assert.strictEqual(w.store['dz.search.query'], 'dark', 'still stored while on the detail');
    w.DZ.app.back();
    assert.strictEqual(w.screen(), 'catalog'); assert.strictEqual(w.input().value, 'dark'); assert.strictEqual(w.store['dz.search.query'], 'dark');
    assert.strictEqual(w.log.catalog[w.log.catalog.length - 1].args.q, 'dark');
  }

  console.log('Live search: debounce, stale answers, Enter, focus/bar kept while typing: OK');
})().catch(error => { console.error(error); process.exit(1); });
