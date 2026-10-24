// Remote colour-key shortcuts (js/app.js): outside the player Red = Ara, Green = Listem, Blue = Ayarlar (desktop F1 / F2 / F4),
// also while a text input has focus (input is blurred, key not swallowed); letter keys (G) do not trigger; inside the player
// colour keys keep their player meaning (forwarded to DZP.player.key). Ara/Listem/Ayarlar pills carry a coloured dot. Also: List top-row focus (Ara · Listem · Ayarlar · Profil); returning home reuses the cached home DOM.
// Fake DOM + fake XHR + fake timers. Run: node client/tests/colorkeys.js
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

const ITEM = { id: 'm1', title: 'Film', type: 'movie', card: '/img/m1/card', progress: { pct: 40, position: 100 } };
const routes = [
  [/\/api\/health/, () => ({ status: 200, body: { status: 'ok' } })],
  [/\/api\/profiles/, () => ({ status: 200, body: { profiles: [{ id: 'p1', name: 'Ali' }] } })],
  [/\/api\/sections/, () => ({ status: 200, body: { sections: [{ id: 'series', title: 'Diziler', heroes: [] }] } })],
  [/\/api\/row\/continue/, () => ({ status: 200, body: { items: [ITEM] } })],
  [/\/api\/row\/mylist/, () => ({ status: 200, body: { items: [], total: 0 } })],
];
const mylistReq = e => e.log.xhr.filter(u => /\/api\/row\/mylist/.test(u)).length;
const isSettings = e => findAll(e.screen, 'pv').length > 0;
const isSearch = e => findAll(e.screen, 'sfld').length > 0;

(async () => {
  const e = env({ search: '?profile=p1', ls: { 'dzproto.server': 'http://tv.lan:8090' }, routes });
  await e.settle();
  assert.equal(findAll(e.screen, 'greet').length, 1, 'home');
  // dots on Ara / Listem / Ayarlar
  assert.deepEqual(findAll(e.screen, 'ctrl-dot').map(n => n.className), ['ctrl-dot k-red', 'ctrl-dot k-green', 'ctrl-dot k-blue']);

  // greeting: top-left row next to the clock, no profile name (name only on the pill) — List and Dock
  const greetOk = env => { const gr = findAll(env.screen, 'greet')[0];
    assert(/top-l/.test(gr.parentNode.className) && findAll(gr.parentNode, 'clock').length === 1, 'greet beside clock');
    assert(!/Ali/.test(gr.textContent) && !/,/.test(gr.textContent), 'greet without name: ' + gr.textContent); };
  greetOk(e);
  {
    const d = env({ search: '?profile=p1', ls: { 'dzproto.server': 'http://tv.lan:8090', 'dzproto.look': JSON.stringify({ layout: 'dock' }) }, routes });
    await d.settle();
    assert(findAll(d.screen, 'dock').length > 0, 'dock layout');
    greetOk(d);
  }
  // List top row focus: menu ^ -> Ara; -> x3 -> profile pill (last in row); v -> Devam Et (no hero); ^ -> profile again; Geri -> menu
  const onCtrl = () => findAll(e.screen, 'ctrl').findIndex(n => / on$/.test(n.className));
  e.press(38); assert.equal(onCtrl(), 0, 'menu up -> Ara');
  e.press(39); e.press(39); e.press(39); e.press(39); assert.equal(onCtrl(), 3, 'right x3 -> profile pill (stays at end)');
  assert(/ctrl-pf/.test(findAll(e.screen, 'ctrl')[3].className), 'profile pill is in the top row');
  e.press(40); assert.equal(onCtrl(), -1, 'down leaves the row'); assert.equal(findAll(e.screen, 'act').length, 1, 'down -> Devam Et');
  e.press(38); assert.equal(onCtrl(), 3, 'up -> last focused (profile)');
  e.press(27); assert.equal(onCtrl(), -1, 'back -> menu');
  const homeRoot = findAll(e.screen, 'home-root')[0];
  assert(homeRoot, 'home root node');
  // Green (Tizen 404) -> Listem; G letter (71) does nothing
  e.press(71); await e.settle();
  assert.equal(mylistReq(e), 0, 'G letter: no shortcut');
  e.press(404); await e.settle();
  assert.equal(mylistReq(e), 1, 'Green -> Listem');
  // Blue (Tizen 406) from Listem -> Ayarlar
  e.press(406); await e.settle();
  assert(isSettings(e), 'Blue -> Ayarlar');
  // Red (F1) -> Ara; then F2 while the search input has focus -> Listem (input blurred)
  e.press(112); await e.settle();
  assert(isSearch(e), 'Red/F1 -> Ara');
  let blurred = false;
  const inp = { tagName: 'INPUT', blur: () => { blurred = true; } };
  const ev = e.press(113, inp); await e.settle();
  assert(ev.prevented && blurred, 'F2 in input: handled, input blurred');
  assert.equal(mylistReq(e), 2, 'F2 -> Listem from search');
  // F4 -> Ayarlar
  e.press(115); await e.settle();
  assert(isSettings(e), 'F4 -> Ayarlar');

  // player: back home, Devam Et card -> player; colour keys go to the player
  e.press(27); await e.settle();
  assert.equal(findAll(e.screen, 'greet').length, 1, 'back home');
  // return to home reuses the cached home DOM (no rebuild / poster reload), focus restored on Ayarlar
  assert.strictEqual(findAll(e.screen, 'home-root')[0], homeRoot, 'home DOM reused on return');
  assert.equal(findAll(e.screen, 'home-root').length, 1, 'single home root');
  assert.equal(onCtrl(), 2, 'focus back on Ayarlar');
  e.press(27); await e.settle();   // top row -> menu
  e.press(39); await e.settle();   // menu -> Devam Et (no hero)
  e.press(13); await e.settle();   // OK -> player
  assert(e.sb.DZP.player.active(), 'player open');
  e.press(403); e.press(404); e.press(405); e.press(71);
  assert.deepEqual(e.pkeys, ['red', 'green', 'yellow', 'green'], 'player keeps colour meanings');
  assert.equal(mylistReq(e), 2, 'no Listem from player');
  console.log('colorkeys: ok');
})().catch(err => { console.error(err); process.exit(1); });
