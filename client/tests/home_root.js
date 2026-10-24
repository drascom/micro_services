// Home screen root (js/app.js + css/app.css): v2.2.0 regression "menus vanish, only the background shows".
// Root cause: .bg-layer lost its permanent stacking context (will-change removed); after the fade (opacity 1) the fader frames
// (.fd-fr z 1/2, .poster-scrim z 3) escaped above #screen. Checks: CSS stacking context; profile -> home; home -> section -> back
// reattaches the cached root, visible (no is-out); decode() rejection does not block. Run: node client/tests/home_root.js
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
  sb.Image = function () { const im = this;   // load ok, decode() REJECTS (Tizen/old Chromium): must not block the home screen
    im.decode = () => Promise.reject(new Error('decode'));
    Object.defineProperty(im, 'src', { set(v) { im._s = v; Promise.resolve().then(() => im.onload && im.onload()); }, get() { return im._s; } }); };
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

const routes = [
  [/\/api\/health/, () => ({ status: 200, body: { status: 'ok' } })],
  [/\/api\/profiles/, () => ({ status: 200, body: { profiles: [{ id: 'p1', name: 'Ali' }] } })],
  [/\/api\/sections/, () => ({ status: 200, body: { sections: [{ id: 'series', title: 'Diziler', heroes: [{ id: 'a0', title: 'A', type: 'series', card: '/img/a0/card', backdrop: '/img/a0/bd' }] }] } })],
  [/\/api\/section\/series/, () => ({ status: 200, body: { rows: [{ id: 'all', title: 'Tüm', items: [{ id: 'a0', title: 'A', type: 'series' }], total: 1 }] } })],
  [/\/api\/row\/continue/, () => ({ status: 200, body: { items: [] } })],
];

(async () => {
  // (0) CSS: background must be its own stacking context below #screen
  const css = fs.readFileSync(path.join(__dirname, '../css/app.css'), 'utf8');
  assert(/\n\.bg \{[^}]*z-index:\s*0/.test(css), '.bg z-index:0 (stacking context)');
  assert(/\n\.bg-layer \{[^}]*isolation:\s*isolate/.test(css), '.bg-layer isolation:isolate');

  const e = env({ search: '?profile=p1', ls: { 'dzproto.server': 'http://tv.lan:8090' }, routes });
  await e.settle(); await e.settle();
  const roots = () => findAll(e.screen, 'home-root');
  // (d) first open -> home (c: decode() rejects for every image)
  assert.equal(roots().length, 1, 'home root mounted');
  assert(!/is-out/.test(e.screen.className), 'screen visible after first open');
  assert(findAll(roots()[0], 'li-name').length || findAll(roots()[0], 'dt').length, 'menu rendered');
  const r0 = roots()[0];
  // (a) home -> section -> back: cached root reattached, visible
  e.press(13); await e.settle();
  assert(findAll(e.screen, 'sec-wrap')[0], 'section screen');
  assert.equal(roots().length, 0, 'home root detached in section');
  e.press(27); await e.settle();
  assert.equal(roots().length, 1, 'home root back');
  assert.strictEqual(roots()[0], r0, 'same cached root reused');
  assert.strictEqual(r0.parentNode, e.screen, 'root attached to #screen');
  assert(!/is-out/.test(e.screen.className), 'screen visible after back');
  // (b) no transitionend dependency: visibility is set by timers only (fake DOM never fires transitionend)
  console.log('home_root: ok');
})().catch(err => { console.error(err); process.exit(1); });
