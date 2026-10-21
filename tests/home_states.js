// Home cards + loading/empty/error states: episode cards (card_kind=episode) are poster tiles (series poster at rest, horizontal
// still/art preview on focus), label + progress bar, card_key stays the focus identity; title cards look the same. A failed row shows a focusable
// "Tekrar dene" card (retry keeps the card in place), empty rows are hidden, a failed boot shows the retry screen, offline is announced,
// and the single toast component shows/hides on its own timer.
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, find, findAll, byAttr, timers } = require('./_fake_dom');

const root = path.join(__dirname, '../tizen-client/js');
const read = f => fs.readFileSync(path.join(root, f), 'utf8');
const tick = async (n = 8) => { for (let i = 0; i < n; i++) await Promise.resolve(); };

function imgEl() { const n = el('img'); return n; }
const sandboxDoc = { createElement: t => (t === 'img' ? imgEl() : el(t)) };

const epCard = (n, extra) => Object.assign({ id: 'dizi-1', type: 'series', title: 'Kayıp Sinyal', card_kind: 'episode', card_key: 'episode:dizi-1:s3:e' + n,
  episode_id: 'dizi-1:s3:e' + n, episode_label: 'S03 B0' + n + ' · Yankı', season: 3, episode: n,
  still_url: '/img/dizi-1:s3:e' + n + '/still?w=320&h=180', has_still: true, has_backdrop: true,
  portrait: '/img/dizi-1/portrait?w=300&h=450', card: '/img/dizi-1/card?w=342&h=192', progress: null }, extra || {});
const titleCard = { id: 'film-1', type: 'movie', title: 'Film', card_kind: 'title', card_key: 'title:film-1', has_backdrop: true,
  portrait: '/img/film-1/portrait?w=300&h=450', card: '/img/film-1/card?w=342&h=192', backdrop: '/img/film-1/backdrop?w=1280&h=720' };

function world(o) {
  o = o || {};
  const window = { requestAnimationFrame: f => f(), Image: function () { return imgEl(); }, DZ: {}, navigator: { onLine: !o.offline } };
  const DZ = window.DZ;
  const log = { boot: 0, rows: [], go: [], focus: [] };
  DZ.api = {
    profileId: () => 'p1', cachedBoot: () => o.cached || null, saveBoot() {}, img: v => v, baseUrl: () => 'http://s',
    boot: () => { log.boot++; return o.boot ? o.boot(log.boot) : new Promise(() => {}); },
    row: (id) => { log.rows.push(id); return o.row ? o.row(id, log.rows.length) : new Promise(() => {}); }
  };
  DZ.navigation = { create: () => el('div') };
  DZ.skeleton = { home: () => el('div') };
  DZ.hero = { create: () => el('div') };
  DZ.nav = { setRoot() {}, onFocus() {}, focusRowById(id) { log.focus.push(id); return true; }, focusIndex() {}, currentEl() { return null; },
    rowIds() { return []; }, refresh() { log.refresh = (log.refresh || 0) + 1; }, current() { return null; } };
  DZ.app = { go: (n, p) => log.go.push({ n, p }), confirmExit() {} };
  const T = timers();
  const sandbox = { window, document: sandboxDoc, console: { log() {} }, setTimeout: T.setTimeout, clearTimeout: T.clearTimeout };
  vm.createContext(sandbox);
  ['ui/toast.js', 'ui/card.js', 'ui/row.js', 'screens/home.js'].forEach(f => vm.runInContext(read(f), sandbox));
  return { DZ, window, log, T, sandbox };
}

(async () => {
  // ================= episode cards
  {
    const w = world();
    const items = [epCard(3, { progress: { episode_id: 'dizi-1:s3:e3', position: 600, duration: 3000, pct: 20 } }), epCard(2, { has_still: false }),
      epCard(1, { has_still: false, has_backdrop: false }), titleCard];
    const row = w.DZ.row.create({ id: 'continue', title: 'İzlemeye Devam Et', loaded: true, items, portrait: true, focusLandscape: true });
    const [c3, c2, c1, ct] = row.dzCards;
    const tiles = findAll(row, 'row-tile');

    // poster tile like every other card: series poster at rest, label + progress bar, card_key focus identity
    assert(c3.classList.contains('card-episode') && c3.classList.contains('card-poster') && c3.classList.contains('card-focus-landscape'));
    assert.equal(c3.dzSrc, '/img/dizi-1/portrait?w=300&h=450', 'the series poster at rest, not the still');
    assert.equal(c3.getAttribute('data-focus-key'), 'episode:dizi-1:s3:e3', 'card_key is the focus identity');
    assert.equal(c3.getAttribute('data-item-id'), 'dizi-1');
    assert(tiles[0].className === 'row-tile poster' && tiles[0].getAttribute('data-base-width') === '240', 'the 240px poster tile, same as title cards');
    assert.equal(tiles[0].className, tiles[3].className); assert.equal(tiles[0].getAttribute('data-base-width'), tiles[3].getAttribute('data-base-width'));
    assert.deepEqual(findAll(tiles[0], 'row-card-title').map(n => n.textContent), ['Kayıp Sinyal']);
    assert.deepEqual(findAll(tiles[0], 'row-card-score').map(n => n.textContent), ['S03 B03 · Yankı']);
    const bar = find(c3, 'card-progress');
    assert(bar && bar.children[0].style.width === '20%', 'progress bar for the episode (on the poster)');
    assert(!find(c2, 'card-progress'), 'no bar without progress');

    // focus: the real still wins (allowed size); no still -> the series' horizontal art; neither -> no expansion, focus effect
    assert.equal(c3.dzFocusSrc, '/img/dizi-1:s3:e3/still?w=640&h=360', 'real still, allowed /img size');
    assert.equal(c2.dzFocusSrc, '/img/dizi-1/card?w=640&h=360', 'has_still=false -> the series horizontal art');
    assert(c2.classList.contains('card-focus-landscape'));
    assert.equal(c1.dzFocusSrc, '', 'no still and no backdrop -> nothing to expand to');
    assert(c1.classList.contains('card-focus-effect') && !c1.classList.contains('card-focus-landscape') && c1.classList.contains('card-poster'));
    assert(!/still/.test(c2.dzFocusSrc + c2.dzSrc + c1.dzFocusSrc + c1.dzSrc), 'the generated still placeholder is never downloaded');

    // preview: title + episode label, no year/rating
    const info = c3.children.find(n => n.className === 'card-preview-info');
    assert(info && info.children[0].textContent === 'Kayıp Sinyal' && info.children.length === 2 && info.children[1].textContent === 'S03 B03 · Yankı', 'preview shows title + episode label');
    assert(!c1.children.some(n => n.className === 'card-preview-info'), 'no preview without horizontal art');
    const epWithMeta = w.DZ.card.create(epCard(1, { year: 2021, rating: 8.1 }), { portrait: true, focusLandscape: true });
    const metaInfo = epWithMeta.children.find(n => n.className === 'card-preview-info');
    assert(metaInfo.children[1].textContent === 'S03 B01 · Yankı' && !/2021|8\.1/.test(metaInfo.children.map(n => n.textContent).join('|')), 'episode preview never shows year/rating');
    // title cards in the same row keep the poster look and the preview
    assert(ct.classList.contains('card-poster') && ct.classList.contains('card-focus-landscape') && !ct.classList.contains('card-episode'));
    assert.equal(tiles[3].className, 'row-tile poster'); assert.equal(tiles[3].getAttribute('data-base-width'), '240');
    assert.equal(ct.dzSrc, '/img/film-1/portrait?w=300&h=450');
    assert.equal(ct.dzFocusSrc, '/img/film-1/backdrop?w=640&h=360');
    assert.equal(findAll(tiles[3], 'row-card-score').length, 0, 'no rating for an unrated title card');

    // no portrait -> falls back to the horizontal art at rest; still expands
    const noPoster = w.DZ.card.create(epCard(4, { portrait: undefined }), { portrait: true, focusLandscape: true });
    assert(noPoster.classList.contains('card-poster') && noPoster.dzSrc === '/img/dizi-1:s3:e4/still?w=454&h=254', 'no portrait: the still/art is used');

    // lazy image + failure placeholder: the title text stays, retries are capped
    c3.dzLoadImage();
    const im = c3.querySelectorAll('img')[0];
    assert(im && im.src === c3.dzSrc && im.width === 300 && im.height === 450);
    im.onerror();
    assert.equal(c3.querySelectorAll('img').length, 0, 'failed image removed');
    assert(find(c3, 'card-fallback').textContent === 'Kayıp Sinyal', 'placeholder text remains');
    c3.dzLoadImage(); assert.equal(c3.querySelectorAll('img').length, 1, 'one retry');
    c3.querySelectorAll('img')[0].onerror();
    c3.dzLoadImage(); assert.equal(c3.querySelectorAll('img').length, 0, 'no third request per focus move');
    c3.dzUnloadImage(); c3.dzLoadImage(); assert.equal(c3.querySelectorAll('img').length, 1, 'unload resets the counter');

    // an episode card in a non-portrait row stays a landscape card (no poster tile, no expansion), with the still at rest
    const plain = w.DZ.card.create(epCard(3), {});
    assert(plain.classList.contains('card-episode') && !plain.classList.contains('card-poster') && !plain.classList.contains('card-focus-landscape') && plain.dzSrc.indexOf('/still?') > 0);
    const plainRow = w.DZ.row.create({ id: 'x', title: 'X', loaded: true, items: [epCard(3)] });
    assert(findAll(plainRow, 'row-tile')[0].className === 'row-tile' && findAll(plainRow, 'row-tile')[0].getAttribute('data-base-width') === '342');
  }

  // ================= empty rows are hidden, error card is focusable, retry keeps it in place
  {
    const w = world();
    const empty = w.DZ.row.create({ id: 'mylist', title: 'Listem', loaded: true, items: [], portrait: true });
    assert(empty.classList.contains('hidden') && empty.getAttribute('data-nav-row') === null, 'empty row: hidden, not in the focus tree');
    empty.dzSetItems([titleCard]);
    assert(!empty.classList.contains('hidden') && empty.getAttribute('data-nav-row') === 'mylist', 'filled later -> visible again');

    const r = w.DZ.row.create({ id: 'series', title: 'Diziler', loaded: false, count: 6, portrait: true });
    let retried = 0;
    r.dzShowError('Sunucuya ulaşılamıyor: http://s', () => retried++);
    const card = find(r, 'card-retry');
    assert(card && card.getAttribute('data-nav') === '1', 'error card is focusable');
    assert.equal(r.getAttribute('data-nav-row'), 'series', 'the row stays in the focus tree so the user can reach it');
    assert.equal(find(card, 'retry-title').textContent, 'Yüklenemedi'); assert(/ulaşılamıyor/.test(find(card, 'retry-msg').textContent));
    assert.equal(find(card, 'retry-btn').textContent, 'Tekrar dene');
    assert(r.dzLoaded === true && r.dzError === true, 'no automatic reload storm: loaded flag stays set');
    card.click(); assert.equal(retried, 1);
    r.dzSetRetrying();
    assert.equal(find(card, 'retry-btn').textContent, 'Yükleniyor…'); assert.equal(r.dzLoaded, false);
  }

  // ================= home: row failure -> retry card -> Enter reloads the row; the focused card is not lost
  {
    let attempt = 0;
    const cached = { hero: null, catalog_total: 3, rows: [{ id: 'series', title: 'Diziler', loaded: false, count: 6 }] };
    const w = world({ cached, row: (id, n) => (n === 1 ? Promise.reject(new Error('Sunucu hatası (500)')) : Promise.resolve({ id, title: 'Diziler', items: [titleCard, epCard(1)] })) });
    const container = el('div');
    w.DZ.screens.home.enter(container, { profile: 'p1' });
    await tick();
    const retry = find(container, 'card-retry');
    assert(retry, 'failed row shows the retry card'); assert.equal(w.log.rows.length, 1);
    assert(/500/.test(find(retry, 'retry-msg').textContent));
    assert(!find(container, 'banner'), 'no top banner for a single row failure');
    retry.click();
    assert.equal(find(container, 'retry-btn').textContent, 'Yükleniyor…', 'card stays while loading');
    await tick();
    assert.equal(w.log.rows.length, 2, 'retry asks the server again');
    assert(!find(container, 'card-retry'), 'row rebuilt after success');
    const cards = findAll(container, 'card').filter(c => c.getAttribute('data-focus-key'));
    assert.deepEqual(cards.map(c => c.getAttribute('data-focus-key')), ['title:film-1', 'episode:dizi-1:s3:e1']);
    w.DZ.screens.home.exit();
  }

  // ================= home: boot failure with nothing cached -> retry screen (focusable) -> retry succeeds
  {
    const data = { hero: null, catalog_total: 2, rows: [{ id: 'series', title: 'Diziler', loaded: true, items: [titleCard] }] };
    const w = world({ boot: n => (n === 1 ? Promise.reject(new Error('Sunucuya ulasilamiyor: http://s')) : Promise.resolve(data)) });
    const container = el('div');
    w.DZ.screens.home.enter(container, { profile: 'p1' });
    await tick();
    const screenBox = find(container, 'errscreen');
    assert(screenBox, 'error screen'); assert.equal(screenBox.getAttribute('data-nav-row'), 'err');
    const btns = findAll(screenBox, 'btn');
    assert.deepEqual(btns.map(b => b.textContent), ['Tekrar dene', 'Ayarlar']);
    assert(btns.every(b => b.getAttribute('data-nav') === '1'), 'both buttons focusable');
    assert(/Sunucuya ulasilamiyor/.test(screenBox.children.map(n => n.textContent).join('|')), 'online: the server message is shown');
    btns[0].click();
    await tick();
    assert.equal(w.log.boot, 2); assert(!find(container, 'errscreen'), 'content replaces the error screen'); assert(findAll(container, 'row').length === 1);
    btns[1].click(); assert.deepEqual(w.log.go[w.log.go.length - 1], { n: 'settings', p: undefined });
    w.DZ.screens.home.exit();
  }

  // ================= offline: clear message instead of the server error; announced by the toast component
  {
    const w = world({ offline: true, boot: () => Promise.reject(new Error('Sunucuya ulasilamiyor: http://s')) });
    const container = el('div');
    w.DZ.screens.home.enter(container, { profile: 'p1' });
    await tick();
    const text = find(container, 'errscreen').children.map(c => c.textContent).join('|');
    assert(/Bağlantı yok/.test(text), 'offline message: ' + text);
    assert.equal(w.DZ.toast.isOffline(), true);
    assert.equal(w.DZ.toast.errorText({ message: 'x' }), 'Bağlantı yok. İnternet bağlantınızı kontrol edin.');
    w.DZ.screens.home.exit();
  }

  // ================= toast component: one element, reused, hides on its own timer; offline pill toggles
  {
    const body = el('div');
    const app = el('div');
    const listeners = {};
    const T = timers();
    const window = { DZ: {}, navigator: { onLine: true }, addEventListener: (t, f) => { listeners[t] = f; } };
    const sb = { window, document: { createElement: el, getElementById: id => (id === 'app' ? app : null), body }, setTimeout: T.setTimeout, clearTimeout: T.clearTimeout, console: { log() {} } };
    vm.createContext(sb); vm.runInContext(read('ui/toast.js'), sb);
    const toast = window.DZ.toast;
    assert.equal(toast.show('Merhaba'), true);
    const t1 = find(app, 'toast');
    assert(t1 && t1.textContent === 'Merhaba' && !t1.classList.contains('hidden'));
    toast.show('İkinci'); assert.equal(findAll(app, 'toast').length, 1, 'single toast element'); assert.equal(t1.textContent, 'İkinci');
    T.advance(toast.MS - 1); assert(!t1.classList.contains('hidden'));
    T.advance(1); assert(t1.classList.contains('hidden'), 'hides after the timeout');
    assert.equal(T.pending(), 0);
    toast.watchOnline();
    const pill = find(app, 'offline-note'); assert(pill && pill.classList.contains('hidden'), 'online: no offline pill');
    window.navigator.onLine = false; listeners.offline();
    assert(!pill.classList.contains('hidden'), 'offline pill shown');
    window.navigator.onLine = true; listeners.online();
    assert(pill.classList.contains('hidden')); assert.equal(t1.textContent, 'Bağlantı geri geldi');
    toast.watchOnline(); assert.equal(Object.keys(listeners).length, 2, 'listeners registered once');
  }

  // ================= nav: uniform rows (episode cards are poster tiles too): step = tile base width, the focused tile may be wider
  {
    const sb = { window: { DZ: {} }, document: { createElement: el }, console: { log() {} }, setTimeout };
    vm.createContext(sb); vm.runInContext(read('nav.js'), sb);
    const nav = sb.window.DZ.nav;
    function buildRow(id, base, focusedWidth) {
      const rowEl = el('section'); rowEl.setAttribute('data-nav-row', id);
      const viewport = el('div'); viewport.setAttribute('data-nav-viewport', '1');
      const strip = el('div'); strip.setAttribute('data-nav-strip', '1'); viewport.appendChild(strip); rowEl.appendChild(viewport);
      let left = 60, prev = null;
      for (let i = 0; i < 4; i++) {
        const tile = el('div'); tile.className = 'row-tile' + (base === 240 ? ' poster' : ''); tile.setAttribute('data-base-width', String(base));
        tile.offsetLeft = left; tile.offsetWidth = i === 2 ? focusedWidth : base; tile.previousSibling = prev;
        const card = el('div'); card.className = 'card'; card.setAttribute('data-nav', '1'); card.setAttribute('data-focus-key', id + i); card.offsetWidth = base;
        tile.appendChild(card); strip.appendChild(tile); prev = tile; left += base + 12;
      }
      return { rowEl, strip };
    }
    const rootEl = el('main'); rootEl.clientHeight = 1080;
    const poster = buildRow('continue', 240, 640), wide = buildRow('similar', 342, 342);
    rootEl.appendChild(poster.rowEl); rootEl.appendChild(wide.rowEl);
    nav.setRoot(rootEl, null);
    nav.focusRowById('continue', 2);
    assert.equal(poster.strip.style.transform, 'translate3d(-252px,0,0)', 'poster row: the focused (expanded) tile sits in the second slot, left neighbour fully visible');
    nav.focusRowById('continue', 3);
    assert.equal(poster.strip.style.transform, 'translate3d(-504px,0,0)', 'one step = 240 + 12');
    nav.focusRowById('continue', 1);
    assert.equal(poster.strip.style.transform, 'translate3d(0px,0,0)', 'second item does not scroll the first one away');
    nav.focusRowById('similar', 3);
    assert.equal(wide.strip.style.transform, 'translate3d(-708px,0,0)', 'landscape row: 342 + 12 per step');
  }

  console.log('Home states: episode poster tiles, empty rows hidden, row retry card, boot retry screen, offline message, toast, uniform-row nav step: OK');
})().catch(e => { console.error(e); process.exitCode = 1; });
