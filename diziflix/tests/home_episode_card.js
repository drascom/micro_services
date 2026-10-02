// Home episode cards: selecting one opens the series' NORMAL summary page (id = production id) with the season selected and
// the episode focused (episodeId) instead of a separate single-episode page; continue cards keep their episode target;
// cards keep distinct focus keys (card_key) so Back returns to the exact card; nav honours data-nav-noscroll.
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, find, findAll, byAttr } = require('./_fake_dom');

const root = path.join(__dirname, '../tizen-client/js');
const read = f => fs.readFileSync(path.join(root, f), 'utf8');

const gos = [];
const window = { requestAnimationFrame: f => f(), Image: function () { return el('img'); }, DZ: {} };
const DZ = window.DZ;
const epCard = (n, title) => ({ id: 'dizi-1', type: 'series', title: 'Dizi', card_kind: 'episode', card_key: 'episode:dizi-1:s1:e' + n,
  episode_id: 'dizi-1:s1:e' + n, episode_label: 'S01 B0' + n + ' · ' + title, portrait: '/img/dizi-1/portrait?w=300&h=450', card: '/img/dizi-1/card?w=342&h=192' });
const boot = {
  hero: null, catalog_total: 5,
  rows: [
    { id: 'continue', title: 'İzlemeye Devam Et', loaded: true, items: [{ id: 'dizi-2', type: 'series', title: 'Devam', progress: { episode_id: 'dizi-2:s2:e7', position: 10, duration: 100, pct: 10 } }] },
    { id: 'new_episodes', title: 'Yeni Eklenen Bölümler', loaded: true, items: [epCard(3, 'Fırtına'), epCard(2, 'Sis')] },
    { id: 'series', title: 'Diziler', loaded: true, items: [{ id: 'dizi-1', type: 'series', title: 'Dizi' }] }
  ]
};
DZ.api = { profileId: () => 'p1', cachedBoot: () => boot, boot: () => new Promise(() => {}), saveBoot() {}, img: v => v, baseUrl: () => 'http://s' };
DZ.navigation = { create: () => el('div') };
DZ.skeleton = { home: () => el('div') };
DZ.hero = { create: () => el('div') };
DZ.nav = { setRoot() {}, onFocus() {}, focusRowById() { return true; }, focusIndex() {}, currentEl() { return null; }, rowIds() { return []; }, refresh() {}, current() { return null; } };
DZ.app = { go: (n, p) => gos.push({ n, p }), confirmExit() {} };
const sandbox = { window, document: { createElement: el }, console: { log() {} }, setTimeout, clearTimeout };
vm.createContext(sandbox);
['ui/card.js', 'ui/row.js', 'screens/home.js'].forEach(f => vm.runInContext(read(f), sandbox));

const home = DZ.screens.home;
const container = el('div');
home.enter(container, { profile: 'p1' });

// distinct focus identity for two episode cards of the SAME series; title cards keep data-item-id only
const c3 = byAttr(container, 'data-focus-key', 'episode:dizi-1:s1:e3');
const c2 = byAttr(container, 'data-focus-key', 'episode:dizi-1:s1:e2');
assert(c3 && c2 && c3 !== c2, 'two episode cards of one series must have different focus keys');
assert.equal(c3.getAttribute('data-item-id'), 'dizi-1'); assert.equal(c2.getAttribute('data-item-id'), 'dizi-1');
const titleCard = findAll(container, 'card').find(n => n.getAttribute('data-item-id') === 'dizi-1' && n.getAttribute('data-focus-key') === null);
assert(titleCard, 'plain title card has no card_key focus key');
const labels = findAll(container, 'row-card-score').map(n => n.textContent);
assert(labels.includes('S01 B03 · Fırtına') && labels.includes('S01 B02 · Sis'), 'episode label shown under the card title: ' + labels);

// episode card -> the normal series page, target episode passed along
c3.click();
assert.deepEqual(gos[0], { n: 'detail', p: { id: 'dizi-1', episodeId: 'dizi-1:s1:e3' } }, 'episode card opens the series page focused on that episode');
c2.click();
assert.deepEqual(gos[1], { n: 'detail', p: { id: 'dizi-1', episodeId: 'dizi-1:s1:e2' } });
assert.equal(gos.every(g => g.n === 'detail'), true, 'never a separate episode-only page or the player');
// title card -> plain detail
titleCard.click();
assert.deepEqual(gos[2], { n: 'detail', p: { id: 'dizi-1' } });
// continue card keeps its episode target (resume_episode) through the same detail page
const contCard = byAttr(container, 'data-item-id', 'dizi-2');
contCard.click();
assert.deepEqual(gos[3], { n: 'detail', p: { id: 'dizi-2', episodeId: 'dizi-2:s2:e7' } });
// pure helper
assert.equal(home.cardParams(null, 'x'), null);
assert.deepEqual(home.cardParams({ id: 'a', type: 'series', card_kind: 'title' }, 'series'), { id: 'a' });
assert.deepEqual(home.cardParams({ id: 'a', type: 'series', progress: { episode_id: 'a:s1:e1' } }, 'mylist'), { id: 'a' }, 'only continue/episode cards carry a target');
assert.deepEqual(home.cardParams({ id: 'a', card_kind: 'episode' }, 'x'), { id: 'a' }, 'episode card without episode_id degrades to the plain page');

// ---- nav: rows flagged data-nav-noscroll keep the page where the screen put it
const navSandbox = { window: { DZ: { api: { img: v => v } } }, document: { createElement: el }, console: { log() {} }, setTimeout };
vm.createContext(navSandbox);
vm.runInContext(read('nav.js'), navSandbox);
const nav = navSandbox.window.DZ.nav;
const rootEl = el('main'); rootEl.clientHeight = 1080;
const pageEl = el('div'); pageEl.className = 'detail';
function navRow(id, noscroll, top) {
  const r = el('div'); r.setAttribute('data-nav-row', id); if (noscroll) r.setAttribute('data-nav-noscroll', '1'); r.offsetTop = top;
  const item = el('div'); item.setAttribute('data-nav', '1'); r.appendChild(item); rootEl.appendChild(r); return r;
}
navRow('actions', false, 500);
navRow('ep_5', true, 4000);
nav.setRoot(rootEl, pageEl);
const afterActions = pageEl.style.transform;
assert(/translate3d\(0,-\d+px,0\)/.test(afterActions), 'normal rows still scroll the page');
nav.focusRowById('ep_5', 0);
assert.equal(pageEl.style.transform, afterActions, 'noscroll rows do not move the page');
nav.focusRowById('actions', 0);
assert.equal(pageEl.style.transform, afterActions);

console.log('Home episode cards: series page + episode focus, continue target, focus keys, labels, noscroll: OK');
