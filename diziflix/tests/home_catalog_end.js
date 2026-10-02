// Home rows -> catalog "Tümü" cards: series/movies open the catalog (new first); trending_series/trending_movies open it with sort=trending, noteworthy_movies
// with sort=popular (GET /api/catalog?type=&sort=); the catalog screen sends `sort` to the API, titles itself "Haftanın Trendleri · Diziler/Filmler" /
// "Dikkate Değer Filmler", shows the sort in the "Sıra:" filter, and applies the incoming sort once (back from a detail keeps the page/sort).
// Real home.js + row.js + card.js + navigation.js (go) + catalog.js on the fake DOM.
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, find, findAll, walk, timers } = require('./_fake_dom');

const read = f => fs.readFileSync(path.join(__dirname, '../tizen-client/js', f), 'utf8');
const tick = async (n = 10) => { for (let i = 0; i < n; i++) await Promise.resolve(); };
const imgEl = () => el('img');

function world() {
  const T = timers();
  const container = el('div');
  Object.defineProperty(container, 'innerHTML', { get() { return ''; }, set() { while (container.firstChild) container.removeChild(container.firstChild); } });
  const log = { go: [], catalog: [] };
  const window = { requestAnimationFrame: f => f(), Image: function () { return imgEl(); }, DZ: {}, navigator: { onLine: true } };
  const DZ = window.DZ;
  DZ.store = { get: (k, d) => d, set() {}, del() {} };
  DZ.api = { profileId: () => 'p1', baseUrl: () => 'http://s', cachedBoot: () => null, saveBoot() {}, img: v => v, debug: {},
    boot: () => new Promise(() => {}), row: () => new Promise(() => {}),
    catalog: p => { log.catalog.push(Object.assign({}, p)); return Promise.resolve({ items: [], total: 0, genres: [], years: [] }); },
    search: () => new Promise(() => {}) };
  DZ.skeleton = { home: () => el('div') };
  DZ.hero = { create: () => el('div') };
  DZ.nav = { setRoot() {}, onFocus() {}, focusRowById() { return true; }, focusIndex() {}, currentEl() { return null; }, rowIds() { return []; }, refresh() {}, current() { return null; },
    snapshot: () => null, restoreNext() {} };
  DZ.app = { go: (n, p, r) => log.go.push({ n, p: Object.assign({}, p), r, ref: p }), confirmExit() {}, updateParams() {}, canGoBack: () => true };
  DZ.modal = { open() {}, isOpen: () => false };
  const sandbox = { window, document: { createElement: t => (t === 'img' ? imgEl() : el(t)) }, console: { log() {} }, setTimeout: T.setTimeout, clearTimeout: T.clearTimeout,
    Promise, Object, Array, Number, String, Math };
  vm.createContext(sandbox);
  ['ui/toast.js', 'ui/card.js', 'ui/row.js', 'ui/navigation.js', 'screens/home.js', 'screens/catalog.js'].forEach(f => vm.runInContext(read(f), sandbox));
  return { DZ, log, T, container, sandbox };
}
const card = (id, title) => ({ id, type: 'series', title, card_kind: 'title', card_key: 'title:' + id, portrait: '/img/' + id + '/portrait', has_backdrop: false });
const ROWS = [['continue', 'İzlemeye Devam Et'], ['trending_series', 'Haftanın Trendleri · Diziler'], ['series', 'Tüm Diziler'], ['trending_movies', 'Haftanın Trendleri · Filmler'],
  ['noteworthy_movies', 'Dikkate Değer Filmler'], ['movies', 'Tüm Filmler'], ['mylist', 'Listem']];

(async () => {
  // ---- A) which home rows get the end card, and where it goes
  {
    const w = world();
    const H = w.DZ.screens.home;
    const expect = { series: ['series', 'new'], movies: ['movies', 'new'], trending_series: ['series', 'trending'], trending_movies: ['movies', 'trending'], noteworthy_movies: ['movies', 'popular'] };
    for (const id of Object.keys(expect)) {
      const a = H.catalogEndAction(id);
      assert(a && typeof a.onSelect === 'function' && a.label, id + ' has an end card');
      a.onSelect();
      const last = w.log.go[w.log.go.length - 1];
      assert.deepEqual([last.n, last.p.view, last.p.sort, last.r], ['catalog', expect[id][0], expect[id][1], true], id + ' -> catalog ' + expect[id].join('/'));
    }
    assert.equal(H.catalogEndAction('series').label, 'Tüm Diziler'); assert.equal(H.catalogEndAction('movies').label, 'Tüm Filmler');
    for (const id of ['continue', 'mylist', 'new_series', 'genre_drama', 'whatever']) assert.strictEqual(H.catalogEndAction(id), null, id + ': no end card');

    // rendered on a real boot: exactly those 5 rows end with the "all items" card; the order/ids are rendered generically
    DZ_boot(w);
    function DZ_boot(world) {
      world.DZ.api.boot = () => Promise.resolve({ hero: null, heroes: [], catalog_total: 5, layout: 'tv-v1',
        rows: ROWS.map(([id, title]) => ({ id, title, loaded: true, items: [card(id + '-1', 'X')], total: 1, offset: 0 })) });
      world.DZ.screens.home.enter(world.container, { profile: 'p1' });
    }
    await tick();
    const withEnd = [];
    findAll(w.container, 'row').forEach(sec => { if (findAll(sec, 'all-items-card').length) withEnd.push(sec.getAttribute('data-row-id')); });
    assert.deepEqual(withEnd, ['trending_series', 'series', 'trending_movies', 'noteworthy_movies', 'movies']);
    const endCard = findAll(find(w.container.children.find(c => find(c, 'rows')) || w.container, 'rows').children.find(s => s.getAttribute('data-row-id') === 'noteworthy_movies'), 'all-items-card')[0];
    endCard.click();
    const last = w.log.go[w.log.go.length - 1];
    assert.deepEqual([last.n, last.p.view, last.p.sort], ['catalog', 'movies', 'popular'], 'clicking the card opens the catalog with the row\'s sort');
  }

  // ---- B) navigation.go forwards the sort only when given
  {
    const w = world();
    w.DZ.navigation.go('series', { sort: 'trending' }); w.DZ.navigation.go('movies'); w.DZ.navigation.go('mylist'); w.DZ.navigation.go('home');
    assert.deepEqual(w.log.go.map(g => [g.n, g.p.view, g.p.sort]), [['catalog', 'series', 'trending'], ['catalog', 'movies', undefined], ['catalog', 'mylist', undefined], ['home', undefined, undefined]]);
  }

  // ---- C) catalog screen: sort goes to the API, title/filter follow it, one-shot (resume keeps what the user had), "new" resets
  {
    const w = world();
    const C = w.DZ.screens.catalog;
    const title = () => find(w.container, 'catalog-heading').children[0].textContent;
    const filters = () => find(w.container, 'catalog-filters').children.map(b => b.textContent);
    const params = { view: 'series', sort: 'trending' };
    C.enter(w.container, params, false); await tick(); w.T.advance(10);
    assert.equal(w.log.catalog.length, 1);
    assert.deepEqual([w.log.catalog[0].type, w.log.catalog[0].sort, w.log.catalog[0].offset, w.log.catalog[0].profile], ['series', 'trending', 0, 'p1'], 'GET /api/catalog?type=series&sort=trending');
    assert.equal(title(), 'Haftanın Trendleri · Diziler');
    assert(filters().indexOf('Sıra: Haftanın trendleri') >= 0, 'the "Sıra:" filter shows the trending sort');
    assert.strictEqual(params.sort, undefined, 'one-shot: not left on the history entry');
    C.exit();

    C.enter(w.container, { view: 'movies', sort: 'popular' }, false); await tick(); w.T.advance(10);
    assert.equal(w.log.catalog[1].sort, 'popular'); assert.equal(w.log.catalog[1].type, 'movie'); assert.equal(title(), 'Dikkate Değer Filmler');
    assert(filters().indexOf('Sıra: Dikkate değer') >= 0);
    C.exit();
    C.enter(w.container, { view: 'movies', sort: 'trending' }, false); await tick(); w.T.advance(10);
    assert.equal(title(), 'Haftanın Trendleri · Filmler'); assert.equal(w.log.catalog[2].sort, 'trending');
    C.exit();

    // back from a detail (resume, no sort param): the chosen sort is kept
    C.enter(w.container, { view: 'movies' }, true); await tick(); w.T.advance(10);
    assert.equal(w.log.catalog[3].sort, 'trending', 'resume keeps the sort the user had');
    assert.equal(title(), 'Haftanın Trendleri · Filmler');
    C.exit();
    // the series/movies end cards send sort=new: a previous trending visit does not stick
    C.enter(w.container, { view: 'movies', sort: 'new' }, false); await tick(); w.T.advance(10);
    assert.equal(w.log.catalog[4].sort, 'new'); assert.equal(title(), 'Filmler'); assert(filters().indexOf('Sıra: Yeni eklenen') >= 0);
    C.exit();
    // a stale sort param on a RESUME (e.g. an old stack entry) is ignored and does not reset the page
    C.enter(w.container, { view: 'movies', sort: 'popular' }, true); await tick(); w.T.advance(10);
    assert.equal(w.log.catalog[5].sort, 'new', 'sort is applied only on a fresh entry');
    C.exit();
    // picking a sort in the filter still works: all five choices exist
    C.enter(w.container, { view: 'series' }, false); await tick(); w.T.advance(10);
    const picks = [];
    w.DZ.modal.open = cfg => picks.push(cfg);
    find(w.container, 'catalog-filters').children[3].click();
    assert.deepEqual(picks[0].buttons.map(b => b.value), ['new', 'year', 'title', 'trending', 'popular']);
    C.exit();
  }

  console.log('Home "Tümü" cards: trending/noteworthy sort, navigation.go(sort), catalog sort param + title + one-shot: OK');
})().catch(e => { console.error(e); process.exitCode = 1; });
