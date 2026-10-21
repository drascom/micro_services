// "No source" detail state (real detail.js + hydrate_watch.js): `availability.state=='unavailable'` + `reason=='no_video_source'` is NOT a fetch error.
// Series -> "Bu dizi için henüz izleme kaynağı yok." (film -> "Bu film için henüz izleme kaynağı yok."), play/episode actions stay hidden;
// "Bölümler şu an alınamadı…" is kept ONLY for a real fetch failure (hydration finished, no seasons, no no-source marker).
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, find, findAll, walk, timers } = require('./_fake_dom');

const root = path.join(__dirname, '../tizen-client/js');
const read = f => fs.readFileSync(path.join(root, f), 'utf8');
const tick = async (n = 10) => { for (let i = 0; i < n; i++) await Promise.resolve(); };

const NO_SRC_SERIES = 'Bu dizi için henüz izleme kaynağı yok.';
const NO_SRC_MOVIE = 'Bu film için henüz izleme kaynağı yok.';
const FAILED = 'Bölümler şu an alınamadı, daha sonra tekrar deneyin';
const noSrc = (extra) => Object.assign({ state: 'unavailable', reason: 'no_video_source', has_trailer: false }, extra || {});
const series = extra => Object.assign({ id: 'd', type: 'series', title: 'Dizi', overview: '', genres: [], backdrop: '', similar: [], seasons: [], actions: [],
  availability: noSrc(), in_mylist: false }, extra || {});
const movie = extra => Object.assign({ id: 'm', type: 'movie', title: 'Film', overview: '', genres: [], backdrop: '', similar: [], seasons: [], actions: [],
  availability: noSrc(), playback: 'unavailable', in_mylist: false, progress: null }, extra || {});

async function open(first, polls) {
  const t = timers();
  const log = { toasts: [], flows: [], detailCalls: [] };
  const window = { requestAnimationFrame: f => f(), Image: function () { return el('img'); }, DZ: {}, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout };
  const DZ = window.DZ;
  const queue = (polls || []).slice();
  DZ.api = { profileId: () => 'p1',
    detail(id, pid, opts) {
      log.detailCalls.push({ id, opts });
      if (opts && opts.poll) return queue.length > 1 ? Promise.resolve(queue.shift()) : (queue[0] instanceof Error ? Promise.reject(queue[0]) : Promise.resolve(queue[0]));
      return Promise.resolve(first);
    } };
  DZ.nav = { setRoot() {}, onFocus() {}, focusRowById() { return true; }, focusIndex() {}, currentEl() { return null; }, refresh() {}, restoreNext() {}, rowIds() { return []; },
    current() { return null; }, snapshot() { return null; } };
  DZ.card = { sized: x => x || '', sizedTo: p => p };
  DZ.skeleton = { detail: () => el('div') };
  DZ.row = { create() { return el('section'); } };
  DZ.modal = { text() {}, open() {} };
  DZ.toast = { showRich: o => log.toasts.push(o.kind + ':' + o.title + (o.message ? '|' + o.message : '')), show: m => log.toasts.push('plain:' + m), errorText: e => e.message, isOffline: () => false };
  DZ.playflow = { start(o) { log.flows.push(o); } };
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
    count: txt => w.texts().filter(x => x === txt).length,
    labels: () => find(container, 'detail-actions').children.map(b => b.textContent),
    async advance(ms) { for (let done = 0; done < ms; done += 3000) { t.advance(Math.min(3000, ms - done)); await tick(); } } };
  return w;
}

(async () => {
  // ---- A) series, nothing hydrating: the no-source sentence (once), never the failure / "not added yet" text; no play button, no episode actions
  {
    const w = await open(series({ hydrating: false }));
    assert.equal(w.count(NO_SRC_SERIES), 1, 'exactly one no-source sentence (not duplicated in the meta line)');
    assert(!w.has('Bölümler şu an alınamadı, daha sonra tekrar deneyin.') && !w.has('Bu dizinin bölüm bilgileri henüz eklenmedi.') && !w.has('İzleme kaynağı henüz mevcut değil'));
    assert.deepEqual(w.labels(), ['Listeme Ekle'], 'play / episode actions are not offered');
    assert(!find(w.container, 'browser'));
    assert.equal(w.H.count(), 0);
    // old servers without `hydrating` behave the same
    const w2 = await open(series());
    assert.equal(w2.count(NO_SRC_SERIES), 1);
  }

  // ---- B) hydration finishes with no episodes AND the no-source marker: warn notice says "no source" (NOT "alınamadı"), the open page shows the same sentence
  {
    const w = await open(series({ hydrating: true }), [series({ hydrating: false })]);
    assert(w.has('Detaylar yükleniyor…') && !w.has(NO_SRC_SERIES), 'hydrating: only the loading placeholder');
    await w.advance(3000);
    assert(!w.has('Detaylar yükleniyor…'));
    assert.equal(w.count(NO_SRC_SERIES), 1);
    assert(!w.texts().some(t => /alınamadı/.test(t)), 'no "alınamadı" anywhere');
    assert.deepEqual(w.log.toasts, ['warn:Dizi|' + NO_SRC_SERIES]);
    assert.equal(w.H.count(), 0);
    assert.equal(w.H.decide(series({ hydrating: false })), 'no_source');
  }

  // ---- C) a REAL failure keeps the old text + toast: hydration finished, no seasons, no no-source marker (or another reason)
  {
    for (const availability of [{ state: 'ready', reason: null, has_trailer: false }, { state: 'unavailable', reason: 'sources_unavailable', has_trailer: false }, undefined]) {
      const w = await open(series({ hydrating: true, availability }), [series({ hydrating: false, availability })]);
      await w.advance(3000);
      assert(w.has('Bölümler şu an alınamadı, daha sonra tekrar deneyin.'), 'failure text kept for a real failure');
      assert(!w.has(NO_SRC_SERIES));
      assert.deepEqual(w.log.toasts, [`warn:Dizi|${FAILED}`]);
      assert.equal(w.H.decide(series({ hydrating: false, availability })), 'unavailable');
    }
  }

  // ---- D) polling timeout / network error with the no-source marker: the placeholder becomes the no-source sentence (no toast)
  {
    const w = await open(series({ hydrating: true }), [new Error('down')]);
    await w.advance(3000);
    assert.equal(w.count(NO_SRC_SERIES), 1); assert.equal(w.log.toasts.length, 0);
    const w2 = await open(series({ hydrating: true, availability: { state: 'ready' } }), [new Error('down')]);
    await w2.advance(3000);
    assert(w2.has('Bu dizinin bölüm bilgileri henüz eklenmedi.'), 'without the marker: the neutral text as before');
  }

  // ---- E) film: its own sentence in the meta line, no play button; a trailer-only film keeps "Tam izleme kaynağı yok · Fragman mevcut"
  {
    const w = await open(movie({ hydrating: false }));
    assert.equal(w.count(NO_SRC_MOVIE), 1); assert(!w.has('İzleme kaynağı henüz mevcut değil'));
    assert.deepEqual(w.labels(), ['Listeme Ekle']);
    const w2 = await open(movie({ availability: noSrc({ has_trailer: true }), actions: [{ kind: 'play_trailer', item_id: 'm' }] }));
    assert(w2.has('Tam izleme kaynağı yok · Fragman mevcut') && !w2.has(NO_SRC_MOVIE));
    assert.deepEqual(w2.labels(), ['Fragmanı Oynat', 'Listeme Ekle']);
    // legacy "unavailable" without a reason: the old meta line
    const w3 = await open(movie({ availability: { state: 'unavailable', has_trailer: false } }));
    assert(w3.has('İzleme kaynağı henüz mevcut değil') && !w3.has(NO_SRC_MOVIE));
    // film hydration ending without a source: warn notice, not the green "izlemeye hazır"
    const w4 = await open(movie({ hydrating: true }), [movie({ hydrating: false })]);
    await w4.advance(3000);
    assert.deepEqual(w4.log.toasts, ['warn:Film|' + NO_SRC_MOVIE]);
    assert(!w4.texts().some(t => /alınamadı/.test(t)));
  }

  // ---- F) helpers / watcher text API
  {
    const w = await open(series());
    const H = w.screen.helpers, G = w.H;
    assert.equal(H.NO_SOURCE_SERIES_TEXT, NO_SRC_SERIES); assert.equal(H.NO_SOURCE_MOVIE_TEXT, NO_SRC_MOVIE);
    assert.equal(H.noSource({ availability: noSrc() }), true);
    assert.equal(H.noSource({ availability: { state: 'unavailable', reason: 'sources_unavailable' } }), false);
    assert.equal(H.noSource({ availability: { state: 'ready', reason: 'no_video_source' } }), false, 'only together with state unavailable');
    assert.equal(H.noSource({}), false); assert.equal(H.noSource(null), false);
    assert.deepEqual(G.noticeFor({ result: 'no_source', title: 'X', data: series() }), { title: 'X', message: NO_SRC_SERIES, kind: 'warn' });
    assert.deepEqual(G.noticeFor({ result: 'no_source', title: 'Y', data: movie() }), { title: 'Y', message: NO_SRC_MOVIE, kind: 'warn' });
    assert.equal(G.messageFor({ result: 'no_source', data: series() }), NO_SRC_SERIES);
    assert.equal(G.decide(movie({ hydrating: true })), 'pending');
    assert.equal(G.decide(movie({ availability: { state: 'ready' } })), 'ready');
    assert.equal(G.decide(series({ seasons: [{ season: 1, episodes: [{}] }] })), 'ready', 'episodes present: ready even with a stale marker');
  }

  console.log('No-source detail: series/film sentences, no "alınamadı" for a missing source, real failures keep the old text, hydrate notice + watcher: OK');
})().catch(e => { console.error(e); process.exitCode = 1; });
