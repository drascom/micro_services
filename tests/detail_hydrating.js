// Detail screen + hydration watcher (real detail.js + hydrate_watch.js): `hydrating: true` shows "Detaylar yükleniyor…" instead of an empty
// season/episode area (not focusable), the watcher is started, `ready` redraws the open detail with the new data (focus snapshot restored BEFORE
// the nav root is rebuilt, buttons keep stable focus keys) and toasts once, `unavailable` shows the failure text + toast, timeout/error only drop
// the placeholder (no toast), leaving the screen keeps the watcher running (toast still appears, no redraw), `hydrating` undefined = old behaviour.
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, find, findAll, walk, timers } = require('./_fake_dom');

const root = path.join(__dirname, '../tizen-client/js');
const read = f => fs.readFileSync(path.join(root, f), 'utf8');
const tick = async (n = 10) => { for (let i = 0; i < n; i++) await Promise.resolve(); };

const ep = (s, n) => ({ id: `d:s${s}:e${n}`, season: s, episode: n, title: `Bölüm ${n}`, overview: '', runtime: 42, still: '', still_url: '', has_still: false,
  air_date: '2020-01-01', availability: { state: 'ready' }, progress: null });
const season = (n, eps) => ({ season: n, title: `${n}. Sezon`, name: `${n}. Sezon`, overview: '', air_date: null, poster_url: '', has_poster: false,
  episode_count: eps.length, episodes: eps });
const series = extra => Object.assign({ id: 'd', type: 'series', title: 'Dizi', overview: '', genres: [], backdrop: '', similar: [], seasons: [],
  availability: { state: 'ready', reason: null, has_trailer: false }, in_mylist: false }, extra || {});
const movie = extra => Object.assign({ id: 'm', type: 'movie', title: 'Film', overview: '', genres: [], backdrop: '', similar: [], seasons: [],
  availability: { state: 'ready', reason: null, has_trailer: false }, playback: 'video', in_mylist: false, progress: null }, extra || {});

// first (non-poll) response = `first`; ?poll=1 responses come from `polls` (last one repeats)
async function open(first, polls) {
  const t = timers();
  const log = { toasts: [], nav: [], detailCalls: [], snap: { rowId: 'actions', rowIndex: 0, col: 1, itemKey: 'data-focus-key:act:mylist' } };
  const window = { requestAnimationFrame: f => f(), Image: function () { return el('img'); }, DZ: {}, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout };
  const DZ = window.DZ;
  const queue = (polls || []).slice();
  DZ.api = { profileId: () => 'p1',
    detail(id, pid, opts) {
      log.detailCalls.push({ id, pid, opts });
      if (opts && opts.poll) return Promise.resolve(queue.length > 1 ? queue.shift() : queue[0]);
      return Promise.resolve(first);
    } };
  DZ.nav = { setRoot() { log.nav.push('setRoot'); }, onFocus() {}, focusRowById() { return true; }, focusIndex() {}, currentEl() { return null; }, refresh() {},
    restoreNext(s) { log.nav.push(s ? 'restoreNext:' + s.rowId + ':' + s.col + ':' + s.itemKey : 'restoreNext:null'); }, rowIds() { return []; },
    current() { return null; }, snapshot() { return log.snap; } };
  DZ.card = { sized: x => x || '', sizedTo: p => p };
  DZ.skeleton = { detail: () => el('div') };
  DZ.row = { create() { return el('section'); } };
  DZ.modal = { text() {}, open() {} };
  DZ.toast = { showRich: o => log.toasts.push(o.kind + ':' + o.title + (o.message ? '|' + o.message : '')), errorText: e => e.message, isOffline: () => false };
  DZ.playflow = { start() {} };
  DZ.app = { go() {}, back() {} };
  const sandbox = { window, Image: window.Image, document: { createElement: el }, console: { log() {} }, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout };
  vm.createContext(sandbox);
  vm.runInContext(read('hydrate_watch.js'), sandbox);
  vm.runInContext(read('screens/detail.js'), sandbox);
  const container = el('div');
  DZ.screens.detail.enter(container, { id: first.id });
  await tick();
  const w = { t, log, DZ, H: DZ.hydrate, screen: DZ.screens.detail, container,
    texts: () => { const out = []; walk(container, n => { if (n.textContent) out.push(n.textContent); }); return out; },
    has: txt => w.texts().indexOf(txt) >= 0,
    labels: () => find(container, 'detail-actions').children.map(b => b.textContent),
    async advance(ms) { for (let done = 0; done < ms; done += 3000) { t.advance(Math.min(3000, ms - done)); await tick(); } } };
  return w;
}
const inNavRow = (node) => { for (let n = node; n; n = n.parentNode) if (n.getAttribute && (n.getAttribute('data-nav') || n.getAttribute('data-nav-row'))) return true; return false; };

(async () => {
  const H = (await open(series({ hydrating: false, seasons: [season(1, [ep(1, 1)])] }))).screen.helpers;
  assert.equal(H.HYDRATING_TEXT, 'Detaylar yükleniyor…');

  // ---- A) empty series while hydrating: placeholder (no focus), watcher started with id+title, then ready -> redraw + one toast
  {
    const w = await open(series({ hydrating: true }), [series({ hydrating: true }), series({ hydrating: false, seasons: [season(1, [ep(1, 1), ep(1, 2)])] })]);
    assert(w.has('Detaylar yükleniyor…'), 'placeholder text');
    assert(!w.has('Bu dizinin bölüm bilgileri henüz eklenmedi.'), 'no "not added yet" text while hydrating');
    const ph = findAll(w.container, 'hydrating-note');
    assert.equal(ph.length, 1);
    assert(!inNavRow(ph[0]), 'placeholder is not focusable');
    assert(!find(w.container, 'browser'), 'no season browser yet');
    assert.equal(w.H.isWatching('d'), true);
    assert.equal(w.log.detailCalls.length, 1); assert.equal(w.log.detailCalls[0].opts, undefined, 'the opening call is the normal (triggering) detail call');
    assert.deepEqual(w.labels(), ['Listeme Ekle']);
    // focus keys are stable across redraws
    const keys = find(w.container, 'detail-actions').children.map(b => b.getAttribute('data-focus-key'));
    assert.deepEqual(keys, ['act:mylist']);

    await w.advance(3000);
    assert(w.has('Detaylar yükleniyor…') && w.log.toasts.length === 0, 'still hydrating after the first poll');
    assert.equal(w.log.detailCalls[1].opts.poll, true);
    w.log.nav.length = 0;
    await w.advance(3000);
    assert(!w.has('Detaylar yükleniyor…'), 'placeholder gone');
    assert(find(w.container, 'browser'), 'season browser drawn from the polled data');
    assert.equal(findAll(w.container, 'season').length, 1);
    assert.equal(findAll(w.container, 'ep-row').length, 2);
    assert.deepEqual(w.labels(), ['Oynat · S01 B01', 'Listeme Ekle'], 'action buttons recomputed from the new data');
    assert.deepEqual(w.log.nav.slice(0, 2), ['restoreNext:actions:1:data-focus-key:act:mylist', 'setRoot'], 'focus snapshot restored before the new root is set');
    assert.deepEqual(w.log.toasts, ['success:Dizi']);
    assert.equal(w.H.count(), 0);
    assert.equal(w.t.pending() <= 1, true);
  }

  // ---- B) unavailable: failure text in the episode area + failure toast (no success toast)
  {
    const w = await open(series({ hydrating: true }), [series({ hydrating: false, seasons: [] })]);
    await w.advance(3000);
    assert(w.has(H.FAILED_TEXT), 'failure text');
    assert(!w.has('Detaylar yükleniyor…'));
    assert.deepEqual(w.log.toasts, ['warn:Dizi|Bölümler şu an alınamadı, daha sonra tekrar deneyin']);
    assert(!find(w.container, 'browser'));
  }

  // ---- C) timeout (40 polls): placeholder replaced by the neutral text, NO toast, no focus/redraw
  {
    const w = await open(series({ hydrating: true }), [series({ hydrating: true })]);
    w.log.nav.length = 0;
    await w.advance(120000);
    assert.equal(w.log.detailCalls.length, 41, 'opening call + 40 polls');
    assert(!w.has('Detaylar yükleniyor…'));
    assert(w.has('Bu dizinin bölüm bilgileri henüz eklenmedi.'));
    assert.equal(w.log.toasts.length, 0);
    assert.deepEqual(w.log.nav, [], 'no redraw / nav change');
    assert.equal(w.H.count(), 0);
  }

  // ---- D) network error while polling: silent, placeholder dropped
  {
    const w = await open(series({ hydrating: true }), [series({ hydrating: true })]);
    w.DZ.api.detail = (id, pid, opts) => opts && opts.poll ? Promise.reject(new Error('down')) : Promise.resolve(series({ hydrating: true }));
    await w.advance(3000);
    assert(!w.has('Detaylar yükleniyor…')); assert.equal(w.log.toasts.length, 0); assert.equal(w.H.count(), 0);
  }

  // ---- E) hydrating undefined/false (old server): old behaviour, nothing watched
  {
    const w = await open(series());
    assert(w.has('Bu dizinin bölüm bilgileri henüz eklenmedi.'));
    assert(!w.has('Detaylar yükleniyor…'));
    assert.equal(findAll(w.container, 'hydrating-note').length, 0);
    assert.equal(w.H.count(), 0);
    const w2 = await open(series({ hydrating: false, seasons: [season(1, [ep(1, 1)])] }));
    assert(find(w2.container, 'browser')); assert.equal(w2.H.count(), 0);
  }

  // ---- F) user leaves the detail: watcher keeps running, toast appears, nothing is redrawn into the dead screen
  {
    const w = await open(series({ hydrating: true }), [series({ hydrating: false, seasons: [season(1, [ep(1, 1)])] })]);
    const before = w.container.children.length;
    w.screen.exit();
    assert.equal(w.H.isWatching('d'), true, 'the watcher survives the screen');
    await w.advance(3000);
    assert.deepEqual(w.log.toasts, ['success:Dizi']);
    assert.equal(w.container.children.length, before, 'old container untouched');
    assert.equal(w.H.count(), 0);
  }

  // ---- G) a ready event for ANOTHER title toasts but does not redraw the open detail
  {
    const w = await open(series({ hydrating: true }), [series({ hydrating: true })]);
    w.DZ.api.detail = (id, pid, opts) => Promise.resolve(id === 'other' ? { id: 'other', type: 'movie', hydrating: false } : series({ hydrating: true }));
    w.H.watch('other', 'Başka');
    w.log.nav.length = 0;
    await w.advance(3000);
    assert.deepEqual(w.log.toasts, ['success:Başka']);
    assert(w.has('Detaylar yükleniyor…'), 'open detail still shows its own placeholder');
    assert.deepEqual(w.log.nav, [], 'open detail not rebuilt');
  }

  // ---- H) film while hydrating: one-line note under the meta; ready -> redrawn with the filled overview + toast
  {
    const w = await open(movie({ hydrating: true }), [movie({ hydrating: false, overview: 'Yeni özet' })]);
    assert.equal(findAll(w.container, 'hydrating-note').length, 1);
    assert(!inNavRow(findAll(w.container, 'hydrating-note')[0]));
    assert(w.labels().indexOf('Oynat') >= 0);
    await w.advance(3000);
    assert(!w.has('Detaylar yükleniyor…')); assert(w.has('Yeni özet'));
    assert.deepEqual(w.log.toasts, ['success:Film']);
  }

  // ---- I) re-opening while the same id is already watched does not start a second watcher; a finished hydration cancels a stale one
  {
    const w = await open(series({ hydrating: true }), [series({ hydrating: true })]);
    assert.equal(w.H.count(), 1);
    w.screen.exit();
    w.screen.enter(el('div'), { id: 'd' });
    await tick();
    assert.equal(w.H.count(), 1, 'same id: still one watcher');
    w.DZ.api.detail = () => Promise.resolve(series({ hydrating: false, seasons: [season(1, [ep(1, 1)])] }));
    w.screen.exit();
    w.screen.enter(el('div'), { id: 'd' });
    await tick();
    assert.equal(w.H.count(), 0, 'detail already complete: stale watcher dropped (no duplicate toast)');
  }

  console.log('Detail hydration: placeholder, watcher start, ready redraw + focus restore, unavailable/timeout/error, leave-screen toast, old server: OK');
})().catch(e => { console.error(e); process.exitCode = 1; });
