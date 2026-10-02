// Episode cards (continue / new_episodes rows) behave like every other poster card: series POSTER at rest in the 240px poster
// tile, on focus the tile expands horizontally (nav apply -> focus-expanded) and shows the real still (has_still) or the series'
// horizontal art (has_backdrop); the preview shows title + episode_label; the progress bar survives; card_key stays the focus
// identity (two cards of one series, restore after reorder); CSS contract: no fixed landscape size for .card-episode.
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, find, findAll, byAttr } = require('./_fake_dom');

const jsRoot = path.join(__dirname, '../tizen-client/js');
const read = f => fs.readFileSync(path.join(jsRoot, f), 'utf8');

const window = { requestAnimationFrame: f => f(), DZ: { api: { img: v => v } } };
const DZ = window.DZ;
const sb = { window, document: { createElement: el }, console: { log() {} }, setTimeout, clearTimeout };
vm.createContext(sb);
['ui/card.js', 'ui/row.js', 'nav.js'].forEach(f => vm.runInContext(read(f), sb));

const ep = (n, extra) => Object.assign({ id: 'dizi-1', type: 'series', title: 'Kayıp Sinyal', card_kind: 'episode', card_key: 'episode:dizi-1:s2:e' + n,
  episode_id: 'dizi-1:s2:e' + n, episode_label: 'S02 B' + (n < 10 ? '0' : '') + n + ' · Brito', season: 2, episode: n,
  still_url: '/img/dizi-1:s2:e' + n + '/still?w=320&h=180', has_still: true, has_backdrop: true,
  portrait: '/img/dizi-1/portrait?w=300&h=450', card: '/img/dizi-1/card?w=342&h=192', backdrop: '/img/dizi-1/backdrop?w=1280&h=720', progress: null }, extra || {});
const film = { id: 'film-1', type: 'movie', title: 'Film', year: 2020, rating: 7.5, card_kind: 'title', card_key: 'title:film-1', has_backdrop: true,
  portrait: '/img/film-1/portrait?w=300&h=450', card: '/img/film-1/card?w=342&h=192', backdrop: '/img/film-1/backdrop?w=1280&h=720' };

const items = [
  ep(12, { progress: { episode_id: 'dizi-1:s2:e12', position: 100, duration: 2700, pct: 40 } }),   // real still
  ep(11, { has_still: false }),                                                                     // no still -> series horizontal art
  ep(10, { has_still: false, has_backdrop: false }),                                                // nothing horizontal -> no expansion
  film
];
const row = DZ.row.create({ id: 'continue', title: 'İzlemeye Devam Et', loaded: true, items, portrait: true, focusLandscape: true });
const tiles = findAll(row, 'row-tile');
const [c12, c11, c10, cf] = row.dzCards;

// ---- poster tiles, identical to the title card's
tiles.forEach(t => { assert.equal(t.className, 'row-tile poster'); assert.equal(t.getAttribute('data-base-width'), '240'); });
[c12, c11, c10].forEach(c => { assert(c.classList.contains('card-episode') && c.classList.contains('card-poster'), 'episode card is a poster card'); assert.equal(c.dzSrc, '/img/dizi-1/portrait?w=300&h=450'); });
assert.deepEqual(tiles.slice(0, 3).map(t => findAll(t, 'row-card-score')[0].textContent), ['S02 B12 · Brito', 'S02 B11 · Brito', 'S02 B10 · Brito'], 'label line under the title stays');
assert.deepEqual(tiles.map(t => findAll(t, 'row-card-title')[0].textContent), ['Kayıp Sinyal', 'Kayıp Sinyal', 'Kayıp Sinyal', 'Film']);

// ---- focus art priority: real still > series horizontal art > none (focus effect); always an allowed /img size
assert.equal(c12.dzFocusSrc, '/img/dizi-1:s2:e12/still?w=640&h=360', 'has_still -> the episode still (640x360 is an allowed still size)');
assert.equal(c11.dzFocusSrc, '/img/dizi-1/backdrop?w=640&h=360', 'has_still=false -> series backdrop, never the generated still placeholder');
assert.equal(c10.dzFocusSrc, '');
assert(c12.classList.contains('card-focus-landscape') && c11.classList.contains('card-focus-landscape'));
assert(c10.classList.contains('card-focus-effect') && !c10.classList.contains('card-focus-landscape'), 'no horizontal art -> plain focus effect');
// a still without the flag (old server) counts; an explicit has_still=false with no backdrop/card falls back to the poster
assert.equal(DZ.card.focusArt(ep(1, { has_still: undefined })), '/img/dizi-1:s2:e1/still?w=640&h=360');
assert.equal(DZ.card.focusArt(ep(1, { has_still: false, backdrop: undefined })), '/img/dizi-1/card?w=640&h=360', 'no backdrop url: the horizontal card art');
assert.equal(DZ.card.focusArt(ep(1, { still_url: '' , has_still: true, has_backdrop: false })), '');

// ---- preview: title + episode_label, no year/rating; title cards keep year/rating
const pv = c => c.children.find(n => n.className === 'card-preview-info');
assert.deepEqual(pv(c12).children.map(n => n.textContent), ['Kayıp Sinyal', 'S02 B12 · Brito']);
assert.deepEqual(pv(cf).children.map(n => n.textContent), ['Film', '2020  •  ★ 7.5']);
assert(!pv(c10), 'no preview without expansion');

// ---- progress bar on the poster; unchanged after the focus image loads
assert.equal(find(c12, 'card-progress').children[0].style.width, '40%');
c12.dzLoadImage(); c12.dzLoadFocusImage();
const imgs = c12.querySelectorAll('img');
assert.equal(imgs.length, 2);
assert(imgs.some(i => i.className === 'card-primary' && i.src === '/img/dizi-1/portrait?w=300&h=450'), 'poster image');
assert(imgs.some(i => i.className === 'card-landscape' && i.src === c12.dzFocusSrc && i.width === 640 && i.height === 360), 'focus image at the focus-card size');
assert(find(c12, 'card-progress'), 'progress bar still there');

// ---- no portrait -> falls back to the still/horizontal art (still a poster tile)
const np = DZ.card.create(ep(9, { portrait: undefined }), { portrait: true, focusLandscape: true });
assert(np.classList.contains('card-poster') && np.dzSrc === '/img/dizi-1:s2:e9/still?w=454&h=254');
const np2 = DZ.card.create(ep(9, { portrait: undefined, has_still: false, has_backdrop: false }), { portrait: true, focusLandscape: true });
assert.equal(np2.dzSrc, '/img/dizi-1/card?w=342&h=192');

// ---- a non-portrait row keeps the landscape episode card (no poster tile, no expansion)
const plain = DZ.card.create(ep(3), { focusLandscape: true });
assert(!plain.classList.contains('card-poster') && !plain.classList.contains('card-focus-landscape') && plain.dzSrc === '/img/dizi-1:s2:e3/still?w=454&h=254');

// ---- real nav: focus expands the tile for episode cards exactly like for title cards; card_key is the focus identity
const root = el('main'); root.clientHeight = 1080;
root.appendChild(row);
DZ.nav.setRoot(root, null);
assert(tiles[0].classList.contains('focus-expanded') && c12.classList.contains('focused'), 'first episode card: tile expands horizontally');
DZ.nav.move('right');
assert(!tiles[0].classList.contains('focus-expanded') && tiles[1].classList.contains('focus-expanded'), 'expansion follows focus');
DZ.nav.move('right');
assert(tiles[2].classList.contains('focus-poster') && !tiles[2].classList.contains('focus-expanded'), 'no horizontal art: poster focus effect');
assert.equal(row.dzCards[2].getAttribute('data-focus-key'), 'episode:dizi-1:s2:e10');
assert.equal(DZ.nav.current().itemKey, 'data-focus-key:episode:dizi-1:s2:e10');
DZ.nav.move('left'); DZ.nav.move('right'); DZ.nav.move('right');
assert(tiles[3].classList.contains('focus-expanded'), 'title card expands the same way');

// two cards of ONE series are distinguished by card_key; restore after a reorder finds the exact card
DZ.nav.move('left'); DZ.nav.move('left');                    // e11
const saved = DZ.nav.snapshot();
assert.equal(saved.itemKey, 'data-focus-key:episode:dizi-1:s2:e11');
const reordered = DZ.row.create({ id: 'continue', title: 'İzlemeye Devam Et', loaded: true, items: [film, ep(10), ep(11), ep(12)], portrait: true, focusLandscape: true });
const root2 = el('main'); root2.clientHeight = 1080; root2.appendChild(reordered);
DZ.nav.reset();
DZ.nav.restoreNext(saved);
DZ.nav.setRoot(root2, null);
DZ.nav.focusRowById('continue', 0);
assert.equal(DZ.nav.current().col, 2, 'restored to the e11 card, not another card of the same series');
assert.equal(byAttr(root2, 'data-focus-key', 'episode:dizi-1:s2:e11').classList.contains('focused'), true);

// ---- CSS contract: episode cards take the poster / expanded sizes from the shared rules (no fixed landscape size)
const css = fs.readFileSync(path.join(__dirname, '../tizen-client/css/home.css'), 'utf8');
assert(!/\.card-episode\s*\{[^}]*(width|height)\s*:/.test(css), '.card-episode must not override the poster/expanded card size');
assert(/\.card-poster\s*\{[^}]*width:var\(--poster-w\)/.test(css));
assert(/\.card-focus-landscape\.focused\s*\{[^}]*width:var\(--focus-card-w\)/.test(css));
assert(/\.row-tile\.poster\.focus-expanded\s*\{[^}]*width:var\(--focus-card-w\)/.test(css));
assert(/\.card-progress\s*\{[^}]*z-index:4/.test(css) && /\.card-preview-info\s*\{[^}]*z-index:3/.test(css), 'progress bar stays above the preview gradient');

console.log('Home episode poster tiles: poster at rest, horizontal still/art on focus, label preview, progress, card_key focus identity, CSS contract: OK');
