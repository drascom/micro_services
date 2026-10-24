// Detail "Bölümler" panel (js/app.js buildEps/epsKey + js/episodes.js): Sezonlar row OK -> glass episode list replaces the poster,
// focus on the resume episode; up/down = episode, left/right = season (synced with the Sezonlar row, focus kept per season);
// unavailable episode -> note, no playback; OK -> player opened with that episode (Izle flow); Back -> poster back, focus Sezonlar;
// returning from the player reopens the panel on the last played episode.
// Fake DOM + fake XHR + fake timers. Run: node client/tests/detail_episodes.js
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, findAll, timers } = require('./_fake_dom');
const JS = path.join(__dirname, '../js');
const tick = async (n = 20) => { for (let i = 0; i < n; i++) await Promise.resolve(); };

function env(o) {
  const T = timers();
  const ls = Object.assign({}, o.ls || {});
  const log = { xhr: [], open: [] };
  const sb = { console: { log() {}, warn() {}, error() {} }, Promise, Math, Date, JSON };
  sb.window = sb;
  Object.assign(sb, { setTimeout: T.setTimeout, clearTimeout: T.clearTimeout, setInterval: T.setInterval, clearInterval: T.clearInterval });
  sb.location = { search: o.search || '', protocol: 'file:', hostname: '', port: '', host: '' };
  sb.localStorage = { getItem: k => (k in ls ? ls[k] : null), setItem: (k, v) => { ls[k] = String(v); }, removeItem: k => { delete ls[k]; } };
  sb.navigator = { userAgent: 'Mozilla/5.0' };
  sb.innerWidth = 1920; sb.innerHeight = 1080;
  sb.Image = function () {};
  sb.addEventListener = () => {};
  sb.scrollTo = () => {};
  const ids = {}, keyFns = [];
  const doc = {
    createElement: t => { const n = el(t); n.blur = () => {}; n.focus = () => {}; return n; }, createTextNode: t => { const n = el('#text'); n.textContent = t; return n; },
    getElementById: id => (ids[id] = ids[id] || el('div')),
    addEventListener: (t, fn) => { if (t === 'keydown') keyFns.push(fn); }, documentElement: { clientWidth: 1920, clientHeight: 1080 }, activeElement: null
  };
  sb.document = doc;
  sb.XMLHttpRequest = function () {
    const x = this;
    x.open = (m, u) => { x.u = u; }; x.setRequestHeader = () => {}; x.abort = () => {};
    x.send = () => {
      log.xhr.push(x.u);
      const r = (o.routes || []).find(r => r[0].test(x.u));
      const res = r ? r[1](x.u) : { status: 0 };
      Promise.resolve().then(() => { x.readyState = 4; x.status = res.status; x.responseText = res.body ? JSON.stringify(res.body) : ''; x.onreadystatechange(); });
    };
  };
  vm.createContext(sb);
  for (const f of ['backgrounds.js', 'api.js', 'search.js', 'exitconfirm.js', 'episodes.js', 'app.js']) {
    if (f === 'app.js') {   // player stub: records open() options
      let on = false;
      sb.DZP.player = { open: opts => { on = true; log.open.push(opts); }, active: () => on, key: () => {}, close: () => { on = false; } };
    }
    vm.runInContext(fs.readFileSync(path.join(JS, f), 'utf8'), sb, { filename: f });
  }
  const screen = ids.screen;
  Object.defineProperty(screen, 'innerHTML', { set(v) { if (v === '') screen.children = []; }, get() { return ''; } });
  const press = keyCode => { const ev = { keyCode, key: '', target: null, preventDefault() {} }; keyFns.forEach(fn => fn(ev)); };
  return { T, sb, log, screen, press, settle: async () => { for (let i = 0; i < 5; i++) { await tick(); T.advance(200); } } };
}

const ep = (s, e, x) => Object.assign({ id: 'sl:s' + s + ':e' + e, season: s, episode: e, title: e + '. Bölüm', runtime: 24, air_date: '2024-01-0' + (e % 9 + 1),
  availability: { state: 'ready' }, still_url: '/img/sl:s' + s + ':e' + e + '/still', has_still: e === 1, progress: null }, x || {});
const S1 = Array.from({ length: 12 }, (_, i) => ep(1, i + 1));
S1[1].progress = { position: 300, duration: 1440, pct: 21 };
S1[11] = ep(1, 12, { availability: { state: 'unavailable' }, title: 'Final' });
const DETAIL = { id: 'sl', title: 'Solo Leveling', type: 'series', overview: '...', in_mylist: false, hydrating: false,
  seasons: [{ season: 0, title: 'Özel', episodes: [ep(0, 1)], episode_count: 1 },
    { season: 1, name: '1. Sezon', episodes: S1, episode_count: 12 },
    { season: 2, name: '2. Sezon', episodes: [ep(2, 1), ep(2, 2)], episode_count: 2 }],
  actions: [{ kind: 'resume_episode', item_id: 'sl', episode_id: 'sl:s1:e2', position: 300 }] };
const routes = [
  [/\/api\/health/, () => ({ status: 200, body: { status: 'ok' } })],
  [/\/api\/profiles/, () => ({ status: 200, body: { profiles: [{ id: 'p1', name: 'Ali' }] } })],
  [/\/api\/sections/, () => ({ status: 200, body: { sections: [{ id: 'series', title: 'Diziler', heroes: [] }] } })],
  [/\/api\/section\/series/, () => ({ status: 200, body: { rows: [{ id: 'all', title: 'Tüm', items: [{ id: 'sl', title: 'Solo Leveling', type: 'series' }], total: 1 }] } })],
  [/\/api\/row\/continue/, () => ({ status: 200, body: { items: [] } })],
  [/\/api\/detail\/sl/, () => ({ status: 200, body: JSON.parse(JSON.stringify(DETAIL)) })],
];

(async () => {
  const e = env({ search: '?profile=p1', ls: { 'dzproto.server': 'http://tv.lan:8090' }, routes });
  await e.settle();
  e.press(13); await e.settle();   // menu OK -> Diziler section
  e.press(13); await e.settle();   // card OK -> detail
  const rowOn = () => findAll(e.screen, 'sr').filter(n => / on/.test(n.className)).map(n => findAll(n, 'sr-label')[0].textContent);
  const panel = () => findAll(e.screen, 'dep')[0];
  const items = () => findAll(panel(), 'dep-it');
  const onItem = () => { const on = items().filter(n => / on/.test(n.className)); assert.equal(on.length, 1, 'one focused episode'); return findAll(on[0], 'dep-l')[0].textContent; };
  const head = () => findAll(panel(), 'dep-s')[0].children[1].textContent;
  for (let i = 0; i < 6 && !/^Sezonlar/.test(rowOn()[0] || ''); i++) e.press(40);
  assert(/^Sezonlar · 1\/3$/.test(rowOn()[0]), 'Sezonlar row focused: ' + rowOn());
  assert(findAll(e.screen, 'pv')[0], 'poster before');

  e.press(13);   // Sezonlar OK -> panel
  assert(panel(), 'episodes panel open');
  assert(!findAll(e.screen, 'pv')[0], 'poster replaced');
  assert.deepEqual(rowOn(), [], 'focus left the detail rows');
  assert.equal(head(), '1. Sezon · 1/3 · 12 bölüm', 'season header');
  assert.equal(onItem(), '▸ B02', 'focus on the resume episode');
  assert.equal(items().length, 6, 'virtual window: 6 rows rendered');
  assert(findAll(panel(), 'dep-fill')[0].style.width === '21%', 'progress bar');
  assert(findAll(panel(), 'dep-img').length === 1, 'still only where has_still');
  assert(/^24 dk · /.test(findAll(panel(), 'dep-m')[0].textContent), 'runtime + air date');

  e.press(40); assert.equal(onItem(), 'B03', 'down');
  for (let i = 0; i < 20; i++) e.press(40);
  assert.equal(onItem(), 'B12 · Final', 'down stops at last');
  assert.equal(items().length, 6, 'window still 6 rows');
  assert(findAll(panel(), 'pl-badge').some(n => n.textContent === 'Kaynak yok'), 'no-source badge');
  e.press(13);
  assert.equal(e.log.open.length, 0, 'unavailable episode not played');
  assert(/kaynağı henüz yok/.test(findAll(panel(), 'pl-side-note')[0].textContent), 'note shown');

  e.press(39);   // -> season 2
  assert.equal(head(), '2. Sezon · 2/3 · 2 bölüm');
  assert(/^Sezonlar · 2\/3$/.test(findAll(e.screen, 'sr-label').map(n => n.textContent).find(t => /^Sezonlar/.test(t))), 'Sezonlar row synced');
  assert.equal(onItem(), 'B01', 'new season: first episode');
  e.press(39); assert.equal(head(), 'Özel bölümler · 3/3 · 1 bölüm', 'specials last');
  e.press(37); e.press(37);
  assert.equal(onItem(), 'B12 · Final', 'season 1 focus kept');
  e.press(38); e.press(38);

  e.press(8);   // Back -> poster, focus Sezonlar
  assert(!panel(), 'panel closed');
  assert(findAll(e.screen, 'pv')[0], 'poster back');
  assert(/^Sezonlar/.test(rowOn()[0]), 'focus on Sezonlar');

  e.press(13);   // reopen: per-season focus reset, resume episode again
  assert.equal(onItem(), '▸ B02');
  e.press(40); e.press(40);
  e.press(13);   // OK -> player
  assert.equal(e.log.open.length, 1, 'player opened');
  const o = e.log.open[0];
  assert.equal(o.episodeId, 'sl:s1:e4', 'episode id');
  assert.equal(o.itemId, 'sl');
  assert.equal(o.position, 0);
  assert(o.detail && o.detail.seasons.length === 3, 'detail passed (player Bölümler panel works)');

  // back from the player: panel reopens on the last played episode
  o.onExit({ reason: 'back', itemId: 'sl', episodeId: 'sl:s2:e2' });
  await e.settle();
  assert(panel(), 'panel reopened after the player');
  assert.equal(head(), '2. Sezon · 2/3 · 2 bölüm');
  assert.equal(onItem(), '▸ B02', 'focus on the played episode');

  // resume episode with progress -> position passed
  e.press(37); assert.equal(onItem(), 'B01', 'season 1: no current episode -> first');
  e.press(40); e.press(13);
  assert.equal(e.log.open[1].episodeId, 'sl:s1:e2');
  assert.equal(e.log.open[1].position, 300, 'resume position');
  console.log('detail_episodes: ok');
})().catch(err => { console.error(err); process.exit(1); });
