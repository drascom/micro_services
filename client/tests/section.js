// Section page (js/app.js): fixed "lens" focus + infinite rows. The focused card always sits in the first slot (x=140): <- -> move
// the strip (translateX), not the focus; rows loop (right end -> start, start <- -> end) when total > 5, lazily fetching the needed
// /api/row page (placeholder card meanwhile); rows with <= 5 items do not loop. 3-line captions: episode / movie / channel.
// Fake DOM + fake XHR + fake timers. Run: node client/tests/section.js
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, findAll, timers } = require('./_fake_dom');
const JS = path.join(__dirname, '../js');
const tick = async (n = 20) => { for (let i = 0; i < n; i++) await Promise.resolve(); };

function env(o) {
  const T = timers();
  const ls = Object.assign({}, o.ls || {});
  const log = { xhr: [] };
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
  const ids = {}, keyFns = [], pkeys = [];
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
  for (const f of ['backgrounds.js', 'api.js', 'search.js', 'exitconfirm.js', 'app.js']) {
    if (f === 'app.js') {   // player stub: records keys it receives
      let on = false;
      sb.DZP.player = { open: () => { on = true; }, active: () => on, key: k => { pkeys.push(k); }, close: () => { on = false; } };
    }
    if (fs.existsSync(path.join(JS, f))) vm.runInContext(fs.readFileSync(path.join(JS, f), 'utf8'), sb, { filename: f });
  }
  const screen = ids.screen;
  // the fake DOM keeps children on innerHTML = '': clear them like a browser
  Object.defineProperty(screen, 'innerHTML', { set(v) { if (v === '') screen.children = []; }, get() { return ''; } });
  const text = cls => findAll(screen, cls).map(n => n.textContent);
  const press = (keyCode, target) => { const ev = { keyCode, key: '', target: target || null, preventDefault() { ev.prevented = true; } };
    keyFns.forEach(fn => fn(ev)); return ev; };
  return { T, sb, ls, log, screen, text, press, pkeys, api: sb.DZP.api,
    settle: async () => { for (let i = 0; i < 5; i++) { await tick(); T.advance(200); } } };
}

const mk = (p, n, extra) => Array.from({ length: n }, (_, i) => Object.assign({ id: p + i, title: p.toUpperCase() + ' ' + i, type: 'series', card: '/img/' + p + i + '/card' }, extra && extra(i)));
const ALL = mk('a', 100);
ALL[0] = { id: 'a0', title: 'Dizi', type: 'series', card_kind: 'episode', episode_label: 'S01 B03 · Pilot', season: 1, episode: 3, year: 2024, rating: 7.8 };
ALL[1] = { id: 'a1', title: 'Film', type: 'movie', genres: ['Dram', 'Gerilim', 'Aksiyon'], year: 2020, rating: null };
const CH = { id: 'ch_1', type: 'channel', card_kind: 'channel', title: '24 TV', channel_no: 3, categories: ['news'], card: '' };
const rows = [
  { id: 'all', title: 'Tüm Diziler', items: ALL.slice(0, 8), total: 100 },
  { id: 'few', title: 'Az', items: mk('f', 3), total: 3 },
  { id: 'mid', title: 'Orta', items: mk('m', 7), total: 7 },
  { id: 'live', title: 'Kanallar', items: [CH], total: 1 },
];
const routes = [
  [/\/api\/health/, () => ({ status: 200, body: { status: 'ok' } })],
  [/\/api\/profiles/, () => ({ status: 200, body: { profiles: [{ id: 'p1', name: 'Ali' }] } })],
  [/\/api\/sections/, () => ({ status: 200, body: { sections: [{ id: 'series', title: 'Diziler', heroes: [] }] } })],
  [/\/api\/section\/series/, () => ({ status: 200, body: { rows } })],
  [/\/api\/row\/continue/, () => ({ status: 200, body: { items: [] } })],
  [/\/api\/row\/all/, u => { const off = +/offset=(\d+)/.exec(u)[1], lim = +/limit=(\d+)/.exec(u)[1];
    return { status: 200, body: { items: ALL.slice(off, off + lim), total: 100, offset: off } }; }],
];

(async () => {
  const e = env({ search: '?profile=p1', ls: { 'dzproto.server': 'http://tv.lan:8090' }, routes });
  await e.settle();
  e.press(13); await e.settle();   // menu OK -> Diziler section
  const wrap = findAll(e.screen, 'sec-wrap')[0];
  assert(wrap, 'section screen');
  const lens = findAll(wrap, 'lens');
  assert.equal(lens.length, 2, 'lens back + front');
  assert(lens.every(n => n.parentNode === wrap && / on$/.test(n.className)), 'lens fixed in wrap (not in a strip), shown');
  const srw = () => findAll(e.screen, 'srw').find(n => / on$/.test(n.className));
  const strip = () => findAll(srw(), 'srw-strip')[0];
  const tx = n => +(/translateX\((-?[\d.]+)px\)/.exec(n.style.transform) || [0, 0])[1];
  const onTile = () => findAll(srw(), 'gt').filter(n => / on$/.test(n.className));
  const onTitle = () => findAll(onTile()[0], 'gt-name')[0].textContent;
  const lensX = () => { const t = onTile(); assert.equal(t.length, 1, 'one focused card'); return parseFloat(t[0].style.left) + tx(strip()); };
  const lines = n => findAll(n, 'gt-name').concat(findAll(n, 'gt-sub')).map(x => x.textContent);

  // 3-line captions: episode, movie
  assert.deepEqual(lines(onTile()[0]), ['Dizi', 'S01 B03 · Pilot', '2024 · ★ 7.8'], 'episode card lines');
  assert.equal(lensX(), 140, 'focus in first slot');
  e.press(39);
  assert.deepEqual(lines(onTile()[0]), ['Film', 'Dram · Gerilim', '2020'], 'movie card lines (genres, no rating)');
  assert.equal(lensX(), 140, 'right: focus slot fixed');
  assert.equal(tx(strip()), -392, 'right: strip translated one step');
  assert(e.log.xhr.some(u => /\/api\/row\/all\?.*offset=(6\d|[789]\d)&/.test(u)), 'tail page prefetched for the wrap: ' + e.log.xhr.filter(u => /row\/all/.test(u)).join(' '));
  e.press(37); e.press(37);   // 0 -> wraps to 99
  assert.equal(lensX(), 140, 'wrap left: focus slot fixed');
  assert.equal(tx(strip()), 392, 'strip keeps moving (virtual position -1)');
  assert.equal(onTitle(), 'A 99', 'last item after wrap');
  e.press(39); assert.equal(onTitle(), 'Dizi', 'right from last -> first');
  // jump far left into a not-yet-loaded range: placeholder under the lens, page requested, then the real card
  e.press(37); for (let i = 0; i < 44; i++) e.press(37);   // -> index 55
  assert(/ ph/.test(onTile()[0].className), 'placeholder under the lens before the page arrives');
  assert.equal(lensX(), 140);
  await e.settle();
  assert.equal(onTitle(), 'A 55', 'page arrived');
  for (let i = 0; i < 45; i++) e.press(39);
  await e.settle();
  assert.equal(onTitle(), 'Dizi', 'full cycle back to first');

  // few-item row: no loop, starts at the lens, stops at ends
  e.press(40);
  assert.equal(findAll(srw(), 'srw-title')[0].textContent, 'Az');
  e.press(37); assert.equal(onTitle(), 'F 0', 'few: left at start stays');
  e.press(39); e.press(39); e.press(39); assert.equal(onTitle(), 'F 2', 'few: right stops at end');
  assert.equal(lensX(), 140, 'few: focus slot fixed');

  // fully loaded 7-item row: loops both ways
  e.press(40);
  e.press(37); assert.equal(onTitle(), 'M 6', 'mid: start <- -> end');
  e.press(39); assert.equal(onTitle(), 'M 0', 'mid: end -> start');
  for (let i = 0; i < 7; i++) e.press(39);
  assert.equal(onTitle(), 'M 0', 'mid: 7 x right = full cycle');
  assert.equal(lensX(), 140);

  // channel captions
  e.press(40);
  assert.deepEqual(lines(onTile()[0]), ['24 TV', '3 · Haber', 'CANLI'], 'channel card lines');
  // vertical: row container slides, focus row fixed
  const tyIn = () => +(/translateY\((-?[\d.]+)px\)/.exec(findAll(e.screen, 'sec-in')[0].style.transform) || [0, 0])[1];
  assert.equal(tyIn() + parseFloat(srw().style.top), 30, 'focused row at fixed vertical position');
  e.press(38); assert.equal(tyIn() + parseFloat(srw().style.top), 30, 'up: focused row still at fixed position');
  // per-row position kept: back to first row, still on "Dizi"
  e.press(38); e.press(38); assert.equal(onTitle(), 'Dizi', 'row position kept');
  console.log('section: ok');
})().catch(err => { console.error(err); process.exit(1); });
