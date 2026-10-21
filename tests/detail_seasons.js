// Series detail: vertical season list on the left ("Sezonlar"), "Bölümler" + windowed episode list on the right,
// focus/keys between the lists, debounced season switch, playable/unwatched target, poster-only-if-real,
// unaired badge, 1000+ episodes stay windowed, playback goes through the loading flow, card -> episode focus.
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, find, findAll, byAttr, walk, timers } = require('./_fake_dom');

const src = fs.readFileSync(path.join(__dirname, '../tizen-client/js/screens/detail.js'), 'utf8');
const P = 'series';
const ep = (s, n, extra) => Object.assign({ id: `${P}:s${s}:e${n}`, season: s, episode: n, title: `Bölüm ${n}`, overview: `Özet ${n}`, runtime: 42,
  still: `/img/${P}:s${s}:e${n}/still?w=320&h=180`, still_url: `/img/${P}:s${s}:e${n}/still?w=320&h=180`, has_still: true,
  air_date: '2020-05-0' + (1 + (n % 8)), availability: { state: 'ready' }, progress: null }, extra || {});
const season = (n, eps, extra) => Object.assign({ season: n, title: `${n}. Sezon`, name: `${n}. Sezon`, overview: '', air_date: null,
  poster_url: `/img/${P}:s${n}/portrait?w=300&h=450`, has_poster: true, episode_count: eps.length, episodes: eps }, extra || {});
const many = (s, count, watched, extraFn) => Array.from({ length: count }, (_, i) => ep(s, i + 1, Object.assign(
  i < watched ? { progress: { position: 2500, duration: 2600, pct: 96 } } : {}, extraFn ? extraFn(i + 1) : {})));

const FUTURE = '2999-01-01';
function detailData() {
  return {
    id: P, type: 'series', title: 'Dizi', overview: 'Uzun bir özet', genres: [], availability: { state: 'ready' }, backdrop: '',
    resume: { episode_id: `${P}:s1:e1`, position: 0 },
    similar: [{ id: 'other', title: 'Benzer' }],
    seasons: [
      season(2, many(2, 1200, 40, n => (n === 41 ? {} : {}))),
      season(1, [ep(1, 1), ep(1, 2), ep(1, 3, { availability: { state: 'unavailable', reason: 'no_video_source' } }),
                 ep(1, 4, { air_date: FUTURE, availability: { state: 'unavailable' } })]),
      season(0, [ep(0, 1)], { has_poster: false, poster_url: `/img/${P}/portrait?w=300&h=450`, title: '0. Sezon' }),
      season(3, many(3, 5, 0), { has_poster: false, poster_url: `/img/${P}/portrait?w=300&h=450` })
    ]
  };
}

function setup(data, extra) {
  const T = timers();
  const calls = { playflow: [], modal: [], go: [] };
  const window = { requestAnimationFrame: f => f(), Image: function () { return el('img'); }, DZ: {} };
  const DZ = window.DZ;
  // mini nav: same model as nav.js (rows = [data-nav-row], first [data-nav] item), DOM order, focus callback
  let rootEl = null, pageEl = null, rows = [], cur = null, focusCb = null, pending = null;
  function firstNav(n) { for (const c of n.children) { if (c.getAttribute && c.getAttribute('data-nav')) return c; const r = firstNav(c); if (r) return r; } return null; }
  function collect() { rows = []; walk(rootEl, n => { const id = n.getAttribute && n.getAttribute('data-nav-row'); if (!id) return; const item = firstNav(n); if (item) rows.push({ id, el: n, item }); }); }
  function apply(id, why) { cur = { rowId: id, col: 0 }; walk(rootEl, n => n.classList && n.classList.remove('focused')); const r = rows.find(x => x.id === id); r.item.classList.add('focused'); if (focusCb) focusCb({ el: r.item, rowId: id, col: 0, why: why || 'move' }); }
  DZ.nav = {
    setRoot(r, p) { rootEl = r; pageEl = p; collect(); cur = null; if (rows.length) apply(rows[0].id, 'init'); },
    onFocus(fn) { focusCb = fn; },
    refresh() { collect(); if (cur && rows.find(r => r.id === cur.rowId)) apply(cur.rowId, 'refresh'); },
    focusRowById(id) { if (pending) { const p = pending; pending = null; if (rows.find(r => r.id === p)) { apply(p, 'restore'); return true; } } if (!rows.find(r => r.id === id)) return false; apply(id, 'jump'); return true; },
    focusIndex() {}, restoreNext(v) { pending = v && v.rowId || null; }, currentEl() { return cur ? rows.find(r => r.id === cur.rowId).item : null; },
    current() { return cur; }, rowIds() { return rows.map(r => r.id); },
    move(dir) { const i = rows.findIndex(r => r.id === cur.rowId); const n = dir === 'down' ? i + 1 : dir === 'up' ? i - 1 : i; if (n < 0 || n >= rows.length) return false; apply(rows[n].id); return true; }
  };
  DZ.api = { profileId: () => 'p1', detail: () => Promise.resolve(data), removeMyList() {}, addMyList() {} };
  DZ.card = { sized: x => x || '', sizedTo: (p, w, h) => p.split('?')[0] + `?w=${w}&h=${h}` };
  DZ.skeleton = { detail: () => el('div') };
  DZ.row = { create(o) { const s = el('section'); s.setAttribute('data-row-id', o.id); s.setAttribute('data-nav-row', o.id); const c = el('div'); c.setAttribute('data-nav', '1'); s.appendChild(c); s.dzUpdateWindow = () => {}; return s; } };
  DZ.modal = { text(t, m) { calls.modal.push({ t, m }); }, open() {} };
  DZ.playflow = { start(o) { calls.playflow.push(o); } };
  DZ.app = { go(n, p) { calls.go.push({ n, p }); }, back() {} };
  const sandbox = { window, Image: window.Image, document: { createElement: el }, console: { log() {} }, setTimeout: T.setTimeout, clearTimeout: T.clearTimeout, Date: extra && extra.Date || Date };
  vm.runInNewContext(src, sandbox);
  const screen = DZ.screens.detail;
  const container = el('div');
  return { T, calls, DZ, screen, container, rows: () => rows, cur: () => cur,
    press(name) { const handled = screen.key({ name }); if (handled !== true && /^(up|down|left|right)$/.test(name)) DZ.nav.move(name); return handled; },
    async open(params, resume) { screen.enter(container, params || { id: P }, !!resume); await Promise.resolve(); await Promise.resolve(); } };
}

const epRows = c => findAll(c, 'ep-row');
const seasonRows = c => findAll(c, 'season-row');
const activeSeasonCount = c => findAll(c, 'season').filter(n => n.classList.contains('active')).length;
const activeSeasonRowId = c => { const a = findAll(c, 'season').find(n => n.classList.contains('active')); return a ? a.parentNode.getAttribute('data-nav-row') : null; };
// season number of the episode list that is ACTUALLY rendered on the right (stills carry ":sN:eM")
const listedSeason = c => { const im = c.querySelectorAll('img').find(i => /:s\d+:e\d+\/still/.test(i.src || '')); return im ? Number(/:s(\d+):e/.exec(im.src)[1]) : null; };
const shownSeasonLabel = c => { const a = findAll(c, 'season').find(n => n.classList.contains('active')); return a ? find(a, 'season-name').textContent : null; };

(async () => {
  // ---------- layout: titles, order, posters
  const h = setup(detailData());
  await h.open();
  const c = h.container;
  const titles = findAll(c, 'br-title').map(n => n.textContent);
  assert.deepEqual(titles, ['Sezonlar', 'Bölümler'], 'left "Sezonlar", right "Bölümler"');
  const cols = findAll(c, 'br-col');
  assert.equal(cols.length, 2);
  assert.equal(cols[1].children[0].textContent, 'Bölümler', '"Bölümler" heading sits ABOVE the episode list');
  assert(cols[1].children[0] !== cols[1].children[1] && cols[1].children[1].className.indexOf('br-view') >= 0);
  assert(!findAll(c, 'season-tab').length, 'old season buttons are gone');
  const names = seasonRows(c).map(r => find(r, 'season-name').textContent);
  assert.deepEqual(names, ['1. Sezon', '2. Sezon', '3. Sezon', 'Özel Bölümler'], 'sorted; season 0 last');
  const counts = seasonRows(c).map(r => find(r, 'season-count').textContent);
  assert.deepEqual(counts, ['4 bölüm', '1200 bölüm', '5 bölüm', '1 bölüm']);
  // poster only when a REAL season poster exists; otherwise text only
  const sRows = seasonRows(c).map(r => r.children[0]);
  assert(find(sRows[0], 'season-poster') && find(sRows[1], 'season-poster'), 'real posters -> poster box');
  assert(!find(sRows[2], 'season-poster') && !find(sRows[3], 'season-poster'), 'has_poster=false -> no poster, only the "N. Sezon" text');
  assert(find(sRows[2], 'season-name').textContent === '3. Sezon');
  assert(!c.querySelectorAll('img').some(i => /:s3\/|:s0\//.test(i.src || '')), 'series poster is never used as a season poster');

  // initial state: resume episode is in season 1 -> that season shown, window tiny
  assert.equal(shownSeasonLabel(c), '1. Sezon');
  assert.equal(activeSeasonCount(c), 1, 'exactly one season row carries .active (pale-gold background) - the shown season');
  assert.equal(h.cur().rowId, 'actions', 'normal open focuses the action row');
  assert.equal(epRows(c).length, 4);
  const page = find(c, 'detail');

  // ---------- unaired / unavailable markers on season 1
  const flags = findAll(c, 'ep-flag');
  assert.deepEqual(flags.map(f => f.textContent), ['Kaynak yok', 'Yakında · 1 Oca 2999']);
  assert(findAll(c, 'ep').some(n => n.classList.contains('unaired')));
  // still image: real ones use small /img sizes
  const firstImg = c.querySelectorAll('img').find(i => /still/.test(i.src));
  assert(firstImg && /still\?w=320&h=180$/.test(firstImg.src), 'episode still requested small: ' + (firstImg && firstImg.src));

  // ---------- keys: Down -> overview -> Down enters the season list at the shown season
  h.press('down');
  assert.equal(h.cur().rowId, 'overview');
  h.press('down');
  assert.equal(h.cur().rowId, 'season_0', 'from the top rows down into the season list');
  assert(page.style.transform && /translate3d\(0,-\d+px,0\)/.test(page.style.transform), 'page scrolled to the browser');
  assert(sRows[0].querySelectorAll('img').length === 1, 'focused season poster loaded');
  // moving down inside the list switches the episode list after a 150 ms debounce
  h.press('down');
  assert.equal(h.cur().rowId, 'season_1');
  // visual highlight does NOT wait for the debounce: B is .active at once, A is not (no pale-gold pause on A)
  assert.equal(activeSeasonRowId(c), 'season_1', 'A -> B: B is .active immediately (before the debounce)');
  assert(!sRows[0].classList.contains('active'), 'A is no longer .active');
  assert.equal(activeSeasonCount(c), 1);
  assert.equal(listedSeason(c), 1, 'episode list still shows A right after the move');
  h.T.advance(149);
  assert.equal(listedSeason(c), 1, 'no episode-list switch before 150 ms');
  assert.equal(activeSeasonRowId(c), 'season_1');
  h.T.advance(1);
  assert.equal(listedSeason(c), 2, 'episode list switched to the focused season after the debounce');
  assert.equal(shownSeasonLabel(c), '2. Sezon');
  assert.equal(activeSeasonCount(c), 1, '.active stays on the focused/shown season (never two)');
  assert.equal(activeSeasonRowId(c), 'season_1');
  assert.equal(h.cur().rowId, 'season_1', 'focus stays on the season row');
  // 1200 episodes: windowed, target = first unwatched (index 40 = "Bölüm 41")
  assert(epRows(c).length > 0 && epRows(c).length <= 16, 'windowed render, got ' + epRows(c).length);
  const ids = epRows(c).map(r => r.getAttribute('data-nav-row'));
  assert(ids.includes('ep_40'), 'window contains the first unwatched episode');
  assert.equal(find(epRows(c)[ids.indexOf('ep_40')], 'ep-title').textContent, 'Bölüm 41');
  // quick season hop: two moves inside the debounce window only render the last one
  h.press('up'); h.T.advance(50); h.press('down'); h.T.advance(50); h.press('down');
  assert.equal(h.cur().rowId, 'season_2');
  h.T.advance(150);
  assert.equal(shownSeasonLabel(c), '3. Sezon');
  assert.equal(listedSeason(c), 3);
  assert.equal(epRows(c).length, 5, 'small season renders all its rows');
  // rapid successive Down: only the focused row is ever .active (no stale/duplicate highlight)
  h.press('up'); h.press('up');
  assert.equal(h.cur().rowId, 'season_0');
  for (let i = 0; i < 3; i++) {
    h.press('down');
    assert.equal(activeSeasonCount(c), 1, 'one .active row during rapid Down #' + i);
    assert.equal(activeSeasonRowId(c), h.cur().rowId, 'the .active row is the focused one during rapid Down #' + i);
    h.T.advance(40);
  }
  assert.equal(h.cur().rowId, 'season_3');
  assert.equal(listedSeason(c), 3, 'list did not switch during the rapid moves (debounce restarted each time)');
  h.T.advance(150);
  assert.equal(listedSeason(c), 0, 'last focused season row ("Özel Bölümler") rendered after the debounce');
  assert.equal(activeSeasonRowId(c), 'season_3');
  h.press('up'); h.press('up'); h.T.advance(150);
  assert.equal(h.cur().rowId, 'season_1');
  assert.equal(listedSeason(c), 2);
  assert.equal(activeSeasonRowId(c), 'season_1');
  h.press('down'); h.T.advance(150);       // back to the state the following checks expect: focus season_2, 3. Sezon listed
  assert.equal(h.cur().rowId, 'season_2');
  assert.equal(listedSeason(c), 3);
  // back to season 2, then Right -> the target episode
  h.press('up'); h.T.advance(150);
  assert.equal(shownSeasonLabel(c), '2. Sezon');
  h.press('right');
  assert.equal(h.cur().rowId, 'ep_40', 'Right: season list -> first unwatched episode');
  // .active (pale-gold, unfocused season) must SURVIVE the hand-over to the episode list: same single row, not focused
  assert.equal(activeSeasonCount(c), 1, '.active kept when focus moves season list -> episodes');
  assert.equal(activeSeasonRowId(c), 'season_1', '.active stays on the shown (2. Sezon) row while browsing episodes');
  assert(!findAll(c, 'season').some(n => n.classList.contains('focused')), 'no season row is .focused while an episode is focused');
  // Down inside the episode list keeps the window small and follows the focus
  for (let i = 0; i < 30; i++) h.press('down');
  assert.equal(h.cur().rowId, 'ep_70');
  assert(epRows(c).length <= 16, 'window stays bounded while scrolling, got ' + epRows(c).length);
  assert(epRows(c).some(r => r.getAttribute('data-nav-row') === 'ep_70'));
  assert(!epRows(c).some(r => r.getAttribute('data-nav-row') === 'ep_40'), 'rows that left the window are removed from the DOM');
  assert.equal(activeSeasonRowId(c), 'season_1', '.active survives episode scrolling + nav.refresh() + window re-render');
  assert.equal(activeSeasonCount(c), 1);
  const list = find(c, 'br-episodes').querySelector('.br-list');
  assert(/translate3d\(0,-\d+px,0\)/.test(list.style.transform), 'list scrolled with translate3d');
  for (let i = 0; i < 40; i++) h.press('up');
  assert.equal(h.cur().rowId, 'ep_30');
  // Left -> shown season row; Right again -> remembered episode
  h.press('left');
  assert.equal(h.cur().rowId, 'season_1', 'Left: episodes -> season list (current season)');
  assert.equal(activeSeasonRowId(c), 'season_1', 'Left: .active is the focused (= shown) season row, consistent');
  assert.equal(activeSeasonCount(c), 1);
  h.press('right');
  assert.equal(h.cur().rowId, 'ep_30', 'Right returns to the last focused episode of the shown season');
  // fast Right before the debounce fires still lands in the newly focused season
  h.press('left'); h.press('down');
  assert.equal(h.cur().rowId, 'season_2');
  assert.equal(activeSeasonRowId(c), 'season_2', 'pending switch: .active already on the focused row');
  assert.equal(listedSeason(c), 2, 'pending switch: episode list not switched yet');
  h.press('right');
  assert.equal(listedSeason(c), 3, 'pending season switch flushed by Right');
  assert.equal(shownSeasonLabel(c), '3. Sezon', 'pending season switch flushed by Right');
  assert.equal(h.cur().rowId, 'ep_0');
  assert.equal(activeSeasonRowId(c), 'season_2', 'fast Right: .active is on the newly shown season and stays there in the episode list');
  assert.equal(activeSeasonCount(c), 1);
  // up from the first episode leaves the browser upwards; down from the last goes to "similar"
  h.press('up');
  assert.equal(h.cur().rowId, 'overview');
  h.press('down'); assert.equal(h.cur().rowId, 'season_2', 'entering from above lands on the shown season');
  h.press('right'); assert.equal(h.cur().rowId, 'ep_0');
  for (let i = 0; i < 4; i++) h.press('down');
  assert.equal(h.cur().rowId, 'ep_4');
  h.press('down');
  assert.equal(h.cur().rowId, 'similar');
  h.press('up');
  assert(/^ep_/.test(h.cur().rowId), 'up from similar returns to the episode list');
  h.press('left'); assert.equal(h.cur().rowId, 'season_2');
  h.press('left'); assert.equal(h.cur().rowId, 'season_2', 'Left on the season list is a no-op');

  // ---------- Enter on an episode starts the loading flow (no direct player navigation)
  h.press('right');
  const epItem = h.DZ.nav.currentEl();
  epItem.click();
  assert.equal(h.calls.playflow.length, 1);
  assert.equal(h.calls.playflow[0].itemId, P); assert.equal(h.calls.playflow[0].episodeId, `${P}:s3:e5`, 'Right returned to the last focused episode');
  assert.equal(h.calls.playflow[0].kind, 'video'); assert.equal(h.calls.playflow[0].type, 'series');
  assert.equal(h.calls.go.length, 0, 'no direct DZ.app.go(player): the flow opens the player after the stream is ready');
  // season 1: unavailable episode -> info modal, unaired -> "Henüz yayınlanmadı"; neither starts playback
  h.press('left'); h.press('up'); h.press('up');
  h.T.advance(150);
  assert.equal(shownSeasonLabel(c), '1. Sezon');
  h.press('right');
  const target = h.cur().rowId;
  assert.equal(target, 'ep_0', 'season 1: first playable episode');
  h.press('down'); h.press('down');
  h.DZ.nav.currentEl().click();
  assert.equal(h.calls.playflow.length, 1, 'unavailable episode does not start playback');
  assert.equal(h.calls.modal[0].t, 'Bölüm kaynağı yok');
  h.press('down');
  h.DZ.nav.currentEl().click();
  assert.equal(h.calls.playflow.length, 1);
  assert.equal(h.calls.modal[1].t, 'Henüz yayınlanmadı'); assert(/1 Oca 2999/.test(h.calls.modal[1].m));

  // ---------- home episode card: season selected + episode focused (params.episodeId), page scrolls to the list
  const h2 = setup(detailData());
  await h2.open({ id: P, episodeId: `${P}:s2:e60` });
  assert.equal(shownSeasonLabel(h2.container), '2. Sezon');
  assert.equal(h2.cur().rowId, 'ep_59', 'card episode focused, not the action row');
  assert(/translate3d\(0,-\d+px,0\)/.test(find(h2.container, 'detail').style.transform), 'page scrolled so the list is visible');
  assert(epRows(h2.container).length <= 16);
  assert(epRows(h2.container).some(r => r.getAttribute('data-nav-row') === 'ep_59'));
  h2.press('left'); assert.equal(h2.cur().rowId, 'season_1');
  // episode id from a merged/aliased series still resolves by season+episode number
  const h3 = setup(detailData());
  await h3.open({ id: P, episodeId: 'old-alias:s1:e2' });
  assert.equal(h3.cur().rowId, 'ep_1'); assert.equal(shownSeasonLabel(h3.container), '1. Sezon');
  // unknown episode falls back to the normal page
  const h4 = setup(detailData());
  await h4.open({ id: P, episodeId: 'nope' });
  assert.equal(h4.cur().rowId, 'actions');

  // ---------- returning from the player restores the last episode's season (isResume)
  const h5 = setup(detailData());
  await h5.open({ id: P, episodeId: `${P}:s2:e60` });
  assert.equal(h5.cur().rowId, 'ep_59');
  h5.screen.exit();
  const h6 = h5;                       // same module state: memory kept for this series
  h6.container = el('div');
  h6.screen.enter(h6.container, { id: P, episodeId: `${P}:s2:e60` }, true);
  await Promise.resolve(); await Promise.resolve();
  assert.equal(shownSeasonLabel(h6.container), '2. Sezon');
  assert(epRows(h6.container).some(r => r.getAttribute('data-nav-row') === 'ep_59'), 'window rebuilt around the last watched episode');

  // ---------- fully watched season -> first playable; movie has no browser and keeps native keys
  const helpers = h.screen.helpers;
  assert.equal(helpers.targetEpisodeIndex(many(1, 3, 3), '2026-01-01'), 0);
  assert.equal(helpers.targetEpisodeIndex([], '2026-01-01'), -1);
  assert.equal(helpers.targetEpisodeIndex([ep(1, 1, { availability: { state: 'unavailable' } }), ep(1, 2)], '2026-01-01'), 1, 'skips unplayable');
  const movie = setup({ id: 'm', type: 'movie', title: 'Film', overview: '', seasons: [], availability: { state: 'ready' }, playback: 'video', similar: [] });
  await movie.open({ id: 'm' });
  assert(!findAll(movie.container, 'browser').length);
  assert.equal(movie.screen.key({ name: 'down' }), false);
  // Oynat (film) goes through the flow too
  movie.DZ.nav.currentEl().click();
  assert.equal(movie.calls.playflow.length, 1); assert.equal(movie.calls.playflow[0].episodeId, 'm');

  // ---------- many seasons: posters load lazily around the focus only
  const many30 = detailData();
  many30.seasons = Array.from({ length: 30 }, (_, i) => season(i + 1, [ep(i + 1, 1)]));
  many30.resume = { episode_id: `${P}:s1:e1`, position: 0 };
  const h7 = setup(many30);
  await h7.open();
  const posterImgs = () => findAll(h7.container, 'season-poster').filter(p => p.children.length).length;
  assert(posterImgs() > 0 && posterImgs() <= 9, 'only posters near the focus are requested, got ' + posterImgs());
  h7.press('down'); h7.press('down');
  for (let i = 0; i < 20; i++) h7.press('down');
  assert(posterImgs() > 9 && posterImgs() < 30, 'posters follow the focus lazily, got ' + posterImgs());
  assert.equal(h7.cur().rowId, 'season_20');
  h7.press('right');
  assert.equal(h7.cur().rowId, 'ep_0', 'Right from a far season flushes the switch');
  assert.equal(shownSeasonLabel(h7.container), '21. Sezon');

  // ---------- leaving the season list upwards while a switch is pending: no .active jump back to the old season
  const h8 = setup(detailData());
  await h8.open();
  h8.press('down'); h8.press('down'); h8.press('down');
  assert.equal(h8.cur().rowId, 'season_1');
  h8.T.advance(150);
  assert.equal(listedSeason(h8.container), 2);
  h8.press('up');
  assert.equal(h8.cur().rowId, 'season_0');
  assert.equal(activeSeasonRowId(h8.container), 'season_0');
  h8.press('up');
  assert.equal(h8.cur().rowId, 'overview');
  assert.equal(activeSeasonRowId(h8.container), 'season_0', 'leaving upwards with a pending switch keeps .active on the row that is about to be shown');
  h8.T.advance(150);
  assert.equal(listedSeason(h8.container), 1, 'pending switch still applies after leaving');
  assert.equal(activeSeasonRowId(h8.container), 'season_0');
  assert.equal(activeSeasonCount(h8.container), 1);
  // with no pending switch, off the list .active = the shown season
  h8.press('down');
  assert.equal(h8.cur().rowId, 'season_0');
  assert.equal(activeSeasonRowId(h8.container), 'season_0');

  // ---------- CSS contract: active season = PERMANENT opaque pale gold (not translucent, not live yellow); focus stays live yellow
  const css = f => fs.readFileSync(path.join(__dirname, '../tizen-client/css/' + f), 'utf8');
  const base = css('base.css'), det = css('detail.css');
  const soft = /--accent-soft:\s*(#[0-9a-fA-F]{6})\b/.exec(base);
  assert(soft, '--accent-soft must be an OPAQUE hex colour (rgba at 30% rendered as dark olive/black on TVs)');
  const lum = hex => { const v = [1, 3, 5].map(i => parseInt(hex.substr(i, 2), 16) / 255).map(x => x <= .03928 ? x / 12.92 : Math.pow((x + .055) / 1.055, 2.4)); return .2126 * v[0] + .7152 * v[1] + .0722 * v[2]; };
  const contrast = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + .05) / (Math.min(x, y) + .05); };
  assert(contrast(soft[1], '#ffffff') >= 4.5, 'white text on the active season >= 4.5:1, got ' + contrast(soft[1], '#ffffff'));
  assert(contrast(soft[1], '#1f1f1f') >= 3.2, 'active season clearly lighter than --surface, got ' + contrast(soft[1], '#1f1f1f'));
  assert(contrast(soft[1], '#F5C518') >= 2.3, 'active season clearly paler/darker than the live yellow focus, got ' + contrast(soft[1], '#F5C518'));
  assert(/\.season\.active\{[^}]*background:var\(--accent-soft\)[^}]*border-left-color:var\(--accent\)/.test(det), '.season.active uses --accent-soft + yellow left border');
  assert(/\.season\.focused\{[^}]*background:var\(--accent\);[^}]*color:#111/.test(det), '.season.focused stays the live yellow');
  // smooth colour-only transition on EVERY state change (dark <-> pale gold <-> live yellow), both directions
  const seasonBase = /\.season\{([^}]*)\}/.exec(det);
  const trans = seasonBase && /transition:([^;}]*)/.exec(seasonBase[1]);
  assert(trans, 'base .season carries the transition (applies to every state, both directions)');
  const tparts = trans[1].split(',').map(x => x.trim()).filter(Boolean);
  assert.deepEqual(tparts.map(x => x.split(/\s+/)[0]).sort(), ['background-color', 'border-left-color', 'color'], 'only colour properties animate (no transform/size)');
  tparts.forEach(x => { const d = /(\d*\.?\d+)s\b/.exec(x); assert(d && Number(d[1]) >= 0.2 && Number(d[1]) <= 0.4, 'season colour transition 0.2-0.4 s: ' + x); });
  assert(!/transform|width|height|all\b/.test(trans[1]), 'no transform/size/all in the season transition');
  assert(!/\.season\.(focused|active)\{[^}]*transition/.test(det), 'state rules do not override the transition (same smooth both ways)');
  const cnt = /\.season-count\{([^}]*)\}/.exec(det);
  assert(cnt && /transition:color\s+0?\.(2|3|4)\d*s/.test(cnt[1]), '.season-count colour animates too');
  assert(/\.season\.active \.season-count\{color:#fff;?\}/.test(det), 'season-count readable on the pale gold');

  console.log('Detail series browser: season list, windowed episodes, focus/keys, debounce, markers, flow, card focus: OK');
})().catch(e => { console.error(e); process.exitCode = 1; });
