// Search results show the sources a title was found on (real catalog.js on the fake DOM): `source_options` -> small tags under the card (first 2 + "+N",
// `broken` = warning mark, `unknown` = dimmed), and a dim single line "Bazı kaynaklar yanıt vermedi: <adlar>" below the grid when `remote_sites` reports sites
// with ok:false (skipped ones are not failures). Card focus / selection is unchanged (the tags are not focusable).
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, find, findAll, walk, timers } = require('./_fake_dom');

const read = f => fs.readFileSync(path.join(__dirname, '../tizen-client/js', f), 'utf8');
const tick = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
const opt = (site, name, status, episodes) => ({ site, name, kind: 'series', episodes: episodes === undefined ? 5 : episodes, status });
const item = (id, extra) => Object.assign({ id, title: id.toUpperCase(), year: 2020, type: 'series', availability: { state: 'ready' }, sources: ['yabancidizi'] }, extra || {});

function world() {
  const T = timers();
  const container = el('div');
  Object.defineProperty(container, 'innerHTML', { get() { return ''; }, set() { while (container.firstChild) container.removeChild(container.firstChild); } });
  const reqs = { catalog: [], search: [] };
  const selected = [];
  const request = (list, args) => new Promise((resolve, reject) => { list.push({ args, resolve, reject }); });
  const DZ = {
    store: { get: (k, d) => d, set() {}, del() {} },
    api: { profileId: () => 'p1', baseUrl: () => 'http://s', catalog: p => request(reqs.catalog, p), search: (q, pid, limit) => request(reqs.search, { q, pid, limit }) },
    card: { create(it, o) { const n = el('div'); n.setAttribute('data-nav', '1'); n.setAttribute('data-item-id', it.id); n.dzLoadImage = () => {}; n.dzUnloadImage = () => {}; n.select = () => o.onSelect(it); return n; } },
    navigation: { create: () => el('div'), INPUT_COL: 0, isTyping: () => false, blur() {} },
    nav: { setRoot() {}, onFocus() {}, restoreNext() {}, refresh() {}, snapshot: () => null, current: () => null, focusRowById() { return true; }, focusIndex() {} },
    app: { go(n, p) { selected.push([n, p]); }, updateParams() {} },
    modal: { open() {} }
  };
  const window = { DZ, requestAnimationFrame: fn => T.setTimeout(fn, 0), AbortController: undefined };
  const sandbox = { window, document: { createElement: el }, console: { log() {} }, setTimeout: T.setTimeout, clearTimeout: T.clearTimeout, Promise, Object, Array, Number, String, Math };
  vm.createContext(sandbox);
  vm.runInContext(read('screens/catalog.js'), sandbox);
  DZ.screens.catalog.enter(container, { view: 'search', q: 'dark' });
  const w = { T, DZ, container, reqs, selected,
    texts: () => { const out = []; walk(container, n => { if (n.textContent) out.push(n.textContent); }); return out; },
    heading: () => { const h = find(container, 'catalog-heading'); return h ? h.children.map(c => c.textContent).join(' | ') : ''; },
    tiles: () => findAll(container, 'catalog-tile'),
    async answer(list, i, value) { list[i].resolve(value); await tick(); T.advance(10); await tick(); } };
  return w;
}
const tags = tile => findAll(tile, 'src-tag');

(async () => {
  // ---- A) tags: first 2 + "+N", broken warning, unknown dimmed; cards keep their focus/selection behaviour
  {
    const w = world();
    const items = [
      item('a', { source_options: [opt('yabancidizi', 'Yabancı Dizi', 'ok'), opt('sinemalar', 'Sinemalar', 'broken'), opt('x', 'Üçüncü', 'ok'), opt('y', 'Dördüncü', 'ok')] }),
      item('b', { source_options: [opt('yabancidizi', 'Yabancı Dizi', 'unknown', 0)] }),
      item('c', { source_options: [opt('sinemalar', 'Sinemalar', 'ok', 1), opt('yabancidizi', 'Yabancı Dizi', 'broken')] }),
      item('d')                                               // old server / local catalog item: no source_options -> no tags row
    ];
    await w.answer(w.reqs.catalog, 0, { items: [], total: 0, genres: [], years: [] });
    await w.answer(w.reqs.search, 0, { items, total: 4, remote: true, remote_error: null, remote_sites: { yabancidizi: { ok: true, count: 4, ms: 400 } } });
    const t = w.tiles();
    assert.equal(t.length, 4);
    const a = tags(t[0]);
    assert.deepEqual(a.map(n => n.textContent), ['Yabancı Dizi', '⚠ Sinemalar', '+2'], 'first 2 tags + "+N" for the rest');
    assert(a[1].classList.contains('broken') && !a[0].classList.contains('broken'), 'broken source has the warning mark/class');
    assert(a[2].classList.contains('more'));
    const b = tags(t[1]);
    assert.equal(b.length, 1); assert(b[0].classList.contains('unknown'), 'unknown = dimmed'); assert.equal(b[0].textContent, 'Yabancı Dizi');
    assert.deepEqual(tags(t[2]).map(n => n.textContent), ['Sinemalar', '⚠ Yabancı Dizi']);
    assert.equal(findAll(t[3], 'catalog-sources').length, 0, 'no source_options: nothing drawn');
    assert.equal(findAll(t[0], 'catalog-sources').length, 1);
    // the tags are display-only: the focusable cards and their selection are unchanged
    const navCards = [];
    walk(w.container, n => { if (n.getAttribute && n.getAttribute('data-nav') === '1' && n.getAttribute('data-item-id')) navCards.push(n); });
    assert.deepEqual(navCards.map(n => n.getAttribute('data-item-id')), ['a', 'b', 'c', 'd']);
    let navInTags = false; walk(w.container, n => { if (n.className && /src-tag|catalog-sources/.test(n.className) && n.getAttribute('data-nav')) navInTags = true; });
    assert.equal(navInTags, false, 'tags never take focus');
    navCards[1].select();
    assert.deepEqual(w.selected.pop(), ['detail', { id: 'b' }]);
    assert(!w.texts().some(x => /Bazı kaynaklar/.test(x)), 'every site answered: no warning line');
  }

  // ---- B) warning line: only ok:false and not skipped; names from the results' source_options, else the site id; below the grid
  {
    const w = world();
    const items = [item('a', { source_options: [opt('yabancidizi', 'Yabancı Dizi', 'ok'), opt('sinemalar', 'Sinemalar.com', 'ok')] })];
    await w.answer(w.reqs.catalog, 0, { items: [], total: 0, genres: [], years: [] });
    await w.answer(w.reqs.search, 0, { items, total: 1, remote: true, remote_error: 'sinemalar: timeout; slow: down',
      remote_sites: { yabancidizi: { ok: true, count: 1, ms: 300 }, sinemalar: { ok: false, count: 0, ms: 8000, error: 'timeout' }, slow: { ok: false, count: 0, ms: 9000, error: 'down' },
        brk: { ok: false, count: 0, ms: 0, skipped: 'breaker' }, nosearch: { ok: false, count: 0, ms: 0, skipped: 'unsupported' }, short: { ok: false, skipped: 'short_query' } } });
    const note = findAll(w.container, 'catalog-sources-note');
    assert.equal(note.length, 1, 'a single dim line');
    assert.equal(note[0].textContent, 'Bazı kaynaklar yanıt vermedi: Sinemalar.com, slow', 'skipped sites are not listed; names resolved where known');
    const page = find(w.container, 'catalog-page');
    assert.strictEqual(page.children[page.children.length - 1], note[0], 'below the result grid');
    assert(!/yanıt vermedi, yerel sonuçlar/.test(w.heading()), 'a partial failure does not claim the whole live search failed');
  }
  {
    // nobody answered + no results: the line still shows under the empty message; heading keeps the global note
    const w = world();
    await w.answer(w.reqs.catalog, 0, { items: [], total: 0, genres: [], years: [] });
    await w.answer(w.reqs.search, 0, { items: [], total: 0, remote: true, remote_error: 'yabancidizi: boom', remote_sites: { yabancidizi: { ok: false, count: 0, ms: 12000, error: 'boom' } } });
    assert(w.texts().indexOf('Bazı kaynaklar yanıt vermedi: yabancidizi') >= 0);
    assert(/Canlı kaynak şu an yanıt vermedi/.test(w.heading()), 'no site answered: the existing heading note stays');
    // local-only data (no remote_sites): nothing
    const w2 = world();
    await w2.answer(w2.reqs.catalog, 0, { items: [item('l')], total: 1, genres: [], years: [] });
    assert(!w2.texts().some(x => /Bazı kaynaklar/.test(x)));
    assert.equal(w2.tiles().length, 1); assert.equal(findAll(w2.container, 'catalog-sources').length, 0);
  }

  // ---- C) CSS: dim line + tag styles exist; broken uses the warning colour
  {
    const css = read('../css/home.css');
    assert(/\.catalog-sources\{[^}]*display:flex/.test(css) && /\.src-tag\.unknown\{[^}]*opacity/.test(css) && /\.src-tag\.broken\{/.test(css) && /\.catalog-sources-note\{/.test(css));
  }

  console.log('Search sources: tags (first 2 + "+N", broken warning, unknown dimmed), "Bazı kaynaklar yanıt vermedi" line, focus unchanged: OK');
})().catch(e => { console.error(e); process.exitCode = 1; });
