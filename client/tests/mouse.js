// Mouse = remote (js/app.js "fare = kumanda"): hover (a REAL mousemove) focuses the item with the same S.* state as the remote,
// click = focus + OK (section lens: 1st click slides the card under the lens, 2nd opens it), right click / mouse back button = Back,
// wheel = up/down, shift+wheel = left/right (threshold + rate limit, no queue), after a key press hover does not steal focus until the
// mouse really moves (jitter / synthetic layout moves ignored), old-screen nodes (transition) ignore clicks.
// Fake DOM + fake XHR + fake timers + fake clock. Run: node client/tests/mouse.js
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, findAll, timers } = require('./_fake_dom');
const JS = path.join(__dirname, '../js');
const tick = async (n = 20) => { for (let i = 0; i < n; i++) await Promise.resolve(); };

function env(o) {
  const T = timers();
  const ls = Object.assign({}, o.ls || {});
  const log = { xhr: [] };
  const sb = { console: { log() {}, warn() {}, error() {} }, Promise, Math, JSON };
  const RealDate = Date, D0 = RealDate.UTC(2026, 9, 24, 12), clock = { t: 0 };
  class FakeDate extends RealDate { constructor(...a) { if (a.length) super(...a); else super(D0 + clock.t); } static now() { return D0 + clock.t; } }
  sb.Date = FakeDate;
  sb.window = sb;
  Object.assign(sb, { setTimeout: T.setTimeout, clearTimeout: T.clearTimeout, setInterval: T.setInterval, clearInterval: T.clearInterval });
  sb.location = { search: o.search || '', protocol: 'file:', hostname: '', port: '', host: '' };
  sb.localStorage = { getItem: k => (k in ls ? ls[k] : null), setItem: (k, v) => { ls[k] = String(v); }, removeItem: k => { delete ls[k]; } };
  sb.navigator = { userAgent: 'Mozilla/5.0' };
  sb.innerWidth = 1920; sb.innerHeight = 1080;
  sb.Image = function () {};
  sb.addEventListener = () => {};
  sb.scrollTo = () => {};
  const ids = {}, L = {};
  const doc = {
    createElement: t => { const n = el(t); n.blur = () => {}; n.focus = () => {}; return n; }, createTextNode: t => { const n = el('#text'); n.textContent = t; return n; },
    getElementById: id => (ids[id] = ids[id] || el('div')), body: el('body'),
    addEventListener: (t, fn) => { (L[t] = L[t] || []).push(fn); }, documentElement: { clientWidth: 1920, clientHeight: 1080 }, activeElement: null
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
    if (f === 'app.js') { let on = false; sb.DZP.player = { open: () => { on = true; }, active: () => on, key: () => {}, close: () => { on = false; } }; }
    vm.runInContext(fs.readFileSync(path.join(JS, f), 'utf8'), sb, { filename: f });
  }
  const screen = ids.screen;
  Object.defineProperty(screen, 'innerHTML', { set(v) { if (v === '') screen.children = []; }, get() { return ''; } });
  const fire = (type, ev) => { ev = Object.assign({ preventDefault() { ev.prevented = true; } }, ev); (L[type] || []).forEach(fn => fn(ev)); return ev; };
  return {
    T, sb, screen, clock, log, S: () => sb.DZP._ptr.S(), P: () => sb.DZP._ptr.state(),
    press: keyCode => fire('keydown', { keyCode, key: '', target: null }),
    move: (target, x, y) => fire('mousemove', { target, clientX: x, clientY: y }),
    click: node => { node.onclick({ button: 0, target: node }); },
    wheel: (dy, dx, shift) => fire('wheel', { deltaY: dy || 0, deltaX: dx || 0, deltaMode: 0, shiftKey: !!shift, target: screen }),
    fire,
    settle: async () => { for (let i = 0; i < 5; i++) { await tick(); T.advance(200); } }
  };
}

const mk = (p, n) => Array.from({ length: n }, (_, i) => ({ id: p + i, title: p.toUpperCase() + ' ' + i, type: 'movie', card: '/img/' + p + i + '/card' }));
const routes = [
  [/\/api\/profiles/, () => ({ status: 200, body: { profiles: [{ id: 'p1', name: 'Ali' }] } })],
  [/\/api\/sections/, () => ({ status: 200, body: { sections: [
    { id: 'series', title: 'Diziler', heroes: [] }, { id: 'movies', title: 'Filmler', heroes: [] }, { id: 'anime', title: 'Animeler', heroes: [] }] } })],
  [/\/api\/section\/series/, () => ({ status: 200, body: { rows: [{ id: 'r1', title: 'Bir', items: mk('a', 8), total: 8 }, { id: 'r2', title: 'İki', items: mk('b', 3), total: 3 }] } })],
  [/\/api\/row\/continue/, () => ({ status: 200, body: { items: [] } })],
  [/\/api\/detail\//, () => ({ status: 200, body: { id: 'a2', title: 'A 2', type: 'movie', actions: [{ kind: 'play_movie' }], in_mylist: false } })],
];

(async () => {
  const e = env({ search: '?profile=p1', ls: { 'dzproto.server': 'http://tv.lan:8090' }, routes });
  await e.settle();
  const S = e.S;
  const li = () => findAll(e.screen, 'li');
  assert.equal(S().screen, 'home'); assert.equal(li().length, 3);

  // hover: the first move only anchors, a real move focuses; S.idx is the remote's state
  e.move(li()[2], 400, 500);
  assert.equal(S().idx, 0, 'first move only anchors the pointer (no focus jump on wake)');
  e.move(li()[2], 420, 520);
  assert.equal(S().idx, 2, 'hover -> focus (S.idx)');
  assert.equal(S().zone, 'list');
  assert(/dz-ptr/.test(e.sb.document.body.className), 'pointer mode class');
  // remote continues from the hovered item
  e.press(38);
  assert.equal(S().idx, 1, 'key up continues from the hovered item');
  assert(!/dz-ptr/.test(e.sb.document.body.className), 'key mode');
  // after a key: synthetic move (same coords) and jitter do not steal focus, a real move does
  e.move(li()[0], 420, 520);
  assert.equal(S().idx, 1, 'synthetic move (same coords) ignored');
  e.move(li()[0], 424, 523);
  assert.equal(S().idx, 1, 'small jitter after a key press ignored');
  e.move(li()[0], 440, 560);
  assert.equal(S().idx, 0, 'real move -> hover focuses again');
  // controls pill hover, then wheel (= keys): up/down
  const ctrls = findAll(e.screen, 'ctrl');
  e.move(ctrls[1], 1500, 100);
  assert.equal(S().zone, 'controls'); assert.equal(S().ctrl, 1, 'hover on Listem pill');
  e.move(li()[0], 440, 700);
  assert.equal(S().zone, 'list'); assert.equal(S().idx, 0);
  e.clock.t += 1000;
  e.wheel(-120);
  assert.equal(S().zone, 'controls', 'wheel up from the menu top -> top row (like the up key)');
  // wheel = key mode: content moved under a still pointer -> hover does not steal focus back
  e.move(li()[1], 440, 700);
  assert.equal(S().zone, 'controls', 'synthetic move after wheel ignored');

  // click = focus + OK: open "Diziler" section
  e.click(li()[0]);
  assert.equal(S().screen, 'section', 'click -> OK');
  // old-screen node during the transition: ignored (no double OK)
  e.click(li()[1]);
  assert.equal(S().screen, 'section', 'old-screen click ignored');
  await e.settle();

  // section lens: hover does not move the lens; 1st click slides the card under the lens, 2nd click opens it
  const card = (r, title) => findAll(e.screen, 'gt').find(n => findAll(n, 'gt-name')[0].textContent === title);
  e.move(card(0, 'A 2'), 900, 400); e.move(card(0, 'A 2'), 960, 420);
  assert.equal(S().sec.rows[0].pos, 0, 'hover does not move the lens');
  e.click(card(0, 'A 2'));
  assert.equal(S().sec.rows[0].pos, 2, '1st click: card slides under the lens');
  assert.equal(S().screen, 'section', '1st click does not open');
  // wheel: down = next row; immediate 2nd notch dropped (rate limit), shift+wheel = right
  e.clock.t += 1000;
  e.wheel(100);
  assert.equal(S().sec.row, 1, 'wheel down -> next row');
  e.clock.t += 20;
  e.wheel(100);
  assert.equal(S().sec.row, 1, 'fast 2nd notch dropped (no queue)');
  e.clock.t += 1000;
  e.wheel(-30);
  assert.equal(S().sec.row, 1, 'small delta below the step threshold');
  e.wheel(-30);
  assert.equal(S().sec.row, 0, 'accumulated delta -> up');
  e.clock.t += 1000;
  e.wheel(100, 0, true);
  assert.equal(S().sec.rows[0].pos, 3, 'shift+wheel -> right');
  e.clock.t += 1000;
  e.wheel(0, -100);
  assert.equal(S().sec.rows[0].pos, 2, 'horizontal wheel -> left');
  e.click(card(0, 'A 2'));
  assert.equal(S().screen, 'detail', '2nd click on the lens card opens it');
  await e.settle();

  // detail: row hover -> focus; right click = Back
  const rows = () => findAll(e.screen, 'sr');
  assert(rows().length >= 2);
  e.move(rows()[1], 300, 600); e.move(rows()[1], 330, 640);
  assert.equal(S().detail.row, 1, 'detail row hover');
  const cm = e.fire('contextmenu', { button: 2, target: rows()[1] });
  assert(cm.prevented, 'context menu suppressed');
  assert.equal(S().screen, 'section', 'right click = Back');
  await e.settle();
  // mouse back button (3) = Back (mouseup); deduped within the same gesture
  e.clock.t += 1000;
  e.fire('mouseup', { button: 3, target: e.screen });
  assert.equal(S().screen, 'home', 'mouse back button = Back');
  await e.settle();

  // settings: arrow of an unfocused row only focuses it, arrow of the focused row changes the value
  const ctl = findAll(e.screen, 'ctrl');
  e.click(ctl[2]);
  assert.equal(S().screen, 'settings');
  await e.settle();
  const look0 = S().look.layout;
  let srs = findAll(e.screen, 'sr');
  findAll(srs[1], 'sr-arr')[1].onclick({ stopPropagation() {} });
  assert.equal(S().setRow, 1, 'arrow click focuses its row');
  assert.equal(S().look.layout, look0, 'hidden arrow of an unfocused row does not change the value');
  srs = findAll(e.screen, 'sr');
  findAll(srs[1], 'sr-arr')[1].onclick({ stopPropagation() {} });
  assert.notEqual(S().look.layout, look0, 'arrow of the focused row changes the value');
  console.log('client mouse: ok');
})().catch(err => { console.error(err); process.exit(1); });
