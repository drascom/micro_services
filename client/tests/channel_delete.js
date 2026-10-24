// Canlı TV channel delete (js/app.js): on a channel card OK short press (keyup) = play; OK held >= 800 ms = "silinsin mi?" confirm
// (focus Hayır), the keyup after a long press does not play; Evet -> DELETE /api/live/channels/{id}, the card drops from every row
// and the focus lands on the neighbour. Fake DOM + fake XHR + fake timers. Run: node client/tests/channel_delete.js
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, findAll, timers } = require('./_fake_dom');
const JS = path.join(__dirname, '../js');
const tick = async (n = 20) => { for (let i = 0; i < n; i++) await Promise.resolve(); };

const CH = n => ({ id: 'ch_' + n, type: 'channel', card_kind: 'channel', title: 'Kanal ' + n, channel_no: n, categories: ['news'], card: '' });
const rows = [{ id: 'live_all', title: 'Tüm kanallar', items: [CH(1), CH(2), CH(3)], total: 3 },
  { id: 'live_news', title: 'Haber', items: [CH(1)], total: 1 }];
const routes = [
  [/\/api\/health/, () => ({ status: 200, body: { status: 'ok' } })],
  [/\/api\/profiles/, () => ({ status: 200, body: { profiles: [{ id: 'p1', name: 'Ali' }] } })],
  [/\/api\/sections/, () => ({ status: 200, body: { sections: [{ id: 'live_tv', title: 'Canlı TV', heroes: [] }] } })],
  [/\/api\/section\/live_tv/, () => ({ status: 200, body: { rows: JSON.parse(JSON.stringify(rows)) } })],
  [/\/api\/row\/continue/, () => ({ status: 200, body: { items: [] } })],
  [/\/api\/live\/channels\//, u => ({ status: 200, body: { ok: true, id: u.split('/').pop(), removed: true } })],
];

async function env() {
  const T = timers();
  const ls = { 'dzproto.server': 'http://tv.lan:8090' };
  const log = { xhr: [], played: [] };
  const sb = { console: { log() {}, warn() {}, error() {} }, Promise, Math, Date, JSON };
  sb.window = sb;
  Object.assign(sb, { setTimeout: T.setTimeout, clearTimeout: T.clearTimeout, setInterval: T.setInterval, clearInterval: T.clearInterval });
  sb.location = { search: '?profile=p1', protocol: 'file:', hostname: '', port: '', host: '' };
  sb.localStorage = { getItem: k => (k in ls ? ls[k] : null), setItem: (k, v) => { ls[k] = String(v); }, removeItem: k => { delete ls[k]; } };
  sb.navigator = { userAgent: 'Mozilla/5.0' };
  sb.innerWidth = 1920; sb.innerHeight = 1080;
  sb.Image = function () {};
  sb.addEventListener = () => {};
  sb.scrollTo = () => {};
  const ids = {}, fns = { keydown: [], keyup: [] };
  sb.document = {
    createElement: t => { const n = el(t); n.blur = () => {}; n.focus = () => {}; return n; }, createTextNode: t => { const n = el('#text'); n.textContent = t; return n; },
    getElementById: id => (ids[id] = ids[id] || el('div')),
    addEventListener: (t, fn) => { if (fns[t]) fns[t].push(fn); }, documentElement: { clientWidth: 1920, clientHeight: 1080 }, activeElement: null
  };
  sb.XMLHttpRequest = function () {
    const x = this;
    x.open = (m, u) => { x.m = m; x.u = u; }; x.setRequestHeader = () => {}; x.abort = () => {};
    x.send = () => {
      log.xhr.push(x.m + ' ' + x.u);
      const r = routes.find(r => r[0].test(x.u));
      const res = r ? r[1](x.u) : { status: 0 };
      Promise.resolve().then(() => { x.readyState = 4; x.status = res.status; x.responseText = res.body ? JSON.stringify(res.body) : ''; x.onreadystatechange(); });
    };
  };
  vm.createContext(sb);
  for (const f of ['backgrounds.js', 'api.js', 'search.js', 'exitconfirm.js', 'app.js']) {
    if (f === 'app.js') {
      let on = false;
      sb.DZP.player = { open: o => { on = true; log.played.push(o.itemId); }, active: () => on, key: () => {}, close: () => { on = false; } };
    }
    vm.runInContext(fs.readFileSync(path.join(JS, f), 'utf8'), sb, { filename: f });
  }
  const screen = ids.screen;
  Object.defineProperty(screen, 'innerHTML', { set(v) { if (v === '') screen.children = []; }, get() { return ''; } });
  const fire = (type, keyCode, repeat) => { const ev = { keyCode, key: '', repeat: !!repeat, target: null, preventDefault() {} }; fns[type].forEach(fn => fn(ev)); };
  const e = { T, sb, log, screen, fire, down: (k, r) => fire('keydown', k, r), up: k => fire('keyup', k),
    settle: async () => { for (let i = 0; i < 5; i++) { await tick(); T.advance(200); } } };
  await e.settle();
  e.down(13); e.up(13); await e.settle();   // menu OK -> Canlı TV section (not a channel: normal OK)
  assert(findAll(screen, 'sec-wrap')[0], 'section screen');
  return e;
}
const onTitle = e => { const r = findAll(e.screen, 'srw').find(n => / on$/.test(n.className));
  const t = findAll(r, 'gt').find(n => / on$/.test(n.className)); return findAll(t, 'gt-name')[0].textContent; };

(async () => {
  // short press: nothing on keydown, play on keyup
  let e = await env();
  assert.equal(onTitle(e), 'Kanal 1');
  e.down(13);
  assert.equal(e.log.played.length, 0, 'keydown alone does not play');
  e.T.advance(300);
  e.up(13);
  assert.deepEqual(e.log.played, ['ch_1'], 'short press plays on keyup');
  assert(!e.sb.DZP.exitConfirm.active(), 'no confirm on a short press');

  // long press (keydown repeats while held): confirm opens, keyup does not play, Hayır is focused
  e = await env();
  e.down(13);
  for (let i = 0; i < 8; i++) { e.T.advance(100); e.down(13, true); }
  e.T.advance(100);
  assert(e.sb.DZP.exitConfirm.active(), 'long press opens the confirm');
  e.up(13);
  assert.equal(e.log.played.length, 0, 'keyup after a long press does not play');
  const title = findAll(e.sb.document.getElementById('stage'), 'xq-t')[0];
  assert(/Kanal 1.*silinsin mi\?/.test(title.textContent), title.textContent);
  const btns = findAll(e.sb.document.getElementById('stage'), 'xq-b');
  assert(/ on$/.test(btns[0].className) && btns[0].textContent === 'Hayır', 'focus on Hayır');
  // Hayır: closes, no API call
  e.down(13); e.up(13);
  assert(!e.sb.DZP.exitConfirm.active(), 'Hayır closes');
  assert(!e.log.xhr.some(u => /^DELETE/.test(u)), 'Hayır: no DELETE');
  assert.equal(e.log.played.length, 0);

  // long press again, Evet -> DELETE, card drops from every row, focus on the neighbour
  e.down(13); e.T.advance(900); e.up(13);
  assert(e.sb.DZP.exitConfirm.active());
  e.down(39); e.up(39); e.down(13); e.up(13);
  await e.settle();
  assert(e.log.xhr.includes('DELETE http://tv.lan:8090/api/live/channels/ch_1'), e.log.xhr.join('\n'));
  assert.equal(onTitle(e), 'Kanal 2', 'focus on the neighbour card');
  const titles = findAll(e.screen, 'srw-title').map(n => n.textContent);
  assert.deepEqual(titles, ['Tüm kanallar'], 'the emptied Haber row is gone');
  assert.equal(e.log.played.length, 0, 'nothing played');
  console.log('channel_delete: ok');
})().catch(err => { console.error(err); process.exit(1); });
