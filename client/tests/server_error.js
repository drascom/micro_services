// Server address rules (js/api.js serverCandidates/normalizeServer) + connection-error screen (js/app.js): when the saved server
// is unreachable (profile load / home load) the server screen opens in error mode: message + address box (focused, prefilled)
// + single "Bağlan" (no retry button); Bağlan probes /api/health over the candidates, saves only on success.
// Fake DOM + fake XHR + fake timers. Run: node client/tests/server_error.js
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
  const ids = {};
  const doc = {
    createElement: t => { const n = el(t); n.blur = () => {}; n.focus = () => {}; return n; }, createTextNode: t => { const n = el('#text'); n.textContent = t; return n; },
    getElementById: id => (ids[id] = ids[id] || el('div')),
    addEventListener: () => {}, documentElement: { clientWidth: 1920, clientHeight: 1080 }, activeElement: null
  };
  sb.document = doc;
  sb.XMLHttpRequest = function () {
    const x = this;
    x.open = (m, u) => { x.u = u; x.h = {}; }; x.setRequestHeader = (k, v) => { x.h[k] = v; }; x.abort = () => {};
    x.send = () => {
      log.xhr.push(x.u);
      const r = (o.routes || []).find(r => r[0].test(x.u));
      const res = r ? r[1](x.u, x.h) : { status: 0 };
      Promise.resolve().then(() => { x.readyState = 4; x.status = res.status; x.responseText = res.raw || (res.body ? JSON.stringify(res.body) : ''); x.onreadystatechange(); });
    };
  };
  vm.createContext(sb);
  for (const f of ['backgrounds.js', 'api.js', 'exitconfirm.js', 'app.js']) {
    if (fs.existsSync(path.join(JS, f))) vm.runInContext(fs.readFileSync(path.join(JS, f), 'utf8'), sb, { filename: f });
  }
  const screen = ids.screen;
  // the fake DOM keeps children on innerHTML = '': clear them like a browser
  Object.defineProperty(screen, 'innerHTML', { set(v) { if (v === '') screen.children = []; }, get() { return ''; } });
  const text = cls => findAll(screen, cls).map(n => n.textContent);
  return { T, sb, ls, log, screen, text, api: sb.DZP.api,
    settle: async () => { for (let i = 0; i < 5; i++) { await tick(); T.advance(200); } } };
}

(async () => {
  // ---- address rules ----
  {
    const { api } = env({});
    const C = v => api.serverCandidates(v);
    assert.deepEqual(C('https://diziflix.drascom.uk'), ['https://diziflix.drascom.uk', 'https://diziflix.drascom.uk:8090'], 'schemed https, no port: as-is first, then :8090');
    assert.deepEqual(C('  http://x.lan:9000/ '), ['http://x.lan:9000']);
    assert.deepEqual(C('http://diziflix.internal'), ['http://diziflix.internal', 'http://diziflix.internal:8090'], 'schemed, no port -> as-is then :8090');
    assert.deepEqual(C('http://diziflix.internal:8090'), ['http://diziflix.internal:8090'], 'explicit port -> single');
    assert.deepEqual(C('http://x.lan/sub'), ['http://x.lan/sub'], 'path -> as-is only');
    assert.deepEqual(C('diziflix.drascom.uk'), ['https://diziflix.drascom.uk', 'http://diziflix.drascom.uk:8090'], 'domain: https first, then http:8090');
    assert.deepEqual(C('192.168.1.10'), ['http://192.168.1.10:8090'], 'IP -> http:8090');
    assert.deepEqual(C('192.168.1.10:9000'), ['http://192.168.1.10:9000']);
    assert.deepEqual(C('localhost'), ['http://localhost:8090']);
    assert.deepEqual(C('nas'), ['http://nas:8090']);
    assert.deepEqual(C(''), []);
    assert.equal(api.normalizeServer('diziflix.drascom.uk'), 'https://diziflix.drascom.uk');
    assert.equal(api.normalizeServer('10.0.0.5'), 'http://10.0.0.5:8090');
  }

  // ---- cold start, saved server unreachable -> error-mode server screen ----
  {
    const e = env({ ls: { 'dzproto.server': 'http://old.lan:8090' }, routes: [
      [/^https:\/\/diziflix\.drascom\.uk\/api\/health/, () => ({ status: 200, body: { status: 'ok' } })],
      [/^https:\/\/diziflix\.drascom\.uk\/api\/profiles/, () => ({ status: 200, body: { profiles: [{ id: 'p1', name: 'Ali' }] } })],
    ] });
    await e.settle();
    assert.deepEqual(e.text('c-title'), ['Sunucuya bağlanılamadı'], 'error screen instead of profiles');
    assert(/old\.lan/.test(e.text('c-status')[0]) && /ulaşılamıyor/.test(e.text('c-status')[0]), 'message names address + reason');
    const inp = findAll(e.screen, 'fld-in')[0];
    assert.equal(inp.value, 'http://old.lan:8090', 'box prefilled with current address');
    assert(/\bon\b/.test(findAll(e.screen, 'fld')[0].className), 'focus on the box');
    assert.deepEqual(findAll(e.screen, 'ctrl').map(b => b.textContent || b.children.map(c => c.textContent).join('')), ['Bağlan'], 'single Bağlan, no retry');

    // unreachable new domain: both candidates tried, error, not saved
    inp.value = 'bad.example.com'; inp.oninput();
    inp.dzSubmit();
    await e.settle();
    const msg = e.text('c-status')[0];
    assert(/kaydedilmedi/.test(msg) && /https:\/\/bad\.example\.com/.test(msg) && /http:\/\/bad\.example\.com:8090/.test(msg), msg);
    assert.equal(e.ls['dzproto.server'], 'http://old.lan:8090', 'failed probe does not save');

    // https domain without port works -> saved, profiles screen
    inp.value = 'diziflix.drascom.uk'; inp.oninput();
    inp.dzSubmit();
    await e.settle();
    assert.equal(e.ls['dzproto.server'], 'https://diziflix.drascom.uk');
    assert.deepEqual(e.text('c-title'), ['Kim izliyor?']);
    assert(e.log.xhr.some(u => u === 'https://diziflix.drascom.uk/api/profiles'));
  }

  // ---- ?profile= (home boot) error -> same error screen; same address Bağlan = retry and continue ----
  {
    let up = false;
    const e = env({ search: '?profile=p1', ls: { 'dzproto.server': 'http://tv.lan:8090' }, routes: [
      [/\/api\/health/, () => (up ? { status: 200, body: { status: 'ok' } } : { status: 0 })],
      [/\/api\/profiles/, () => (up ? { status: 200, body: { profiles: [{ id: 'p1', name: 'Ali' }] } } : { status: 0 })],
      [/\/api\/sections/, () => ({ status: 200, body: { sections: [{ id: 'series', title: 'Diziler', heroes: [] }] } })],
      [/\/api\/row\/continue/, () => ({ status: 200, body: { items: [] } })],
    ] });
    await e.settle();
    assert.deepEqual(e.text('c-title'), ['Sunucuya bağlanılamadı'], 'home load error -> error screen');
    up = true;
    findAll(e.screen, 'fld-in')[0].dzSubmit();
    await e.settle();
    assert.equal(e.text('c-title').length, 0, 'back on home');
    assert(findAll(e.screen, 'greet').length === 1);
    assert.equal(e.ls['dzproto.server'], 'http://tv.lan:8090');
  }
  // ---- access key: 401 auth_required -> key warning + focus on key box; right key -> saved, header on every request;
  //      non-JSON 401 (proxy login page) -> proxy message ----
  {
    const e = env({ routes: [
      [/^https:\/\/pin\.example\.com\/api\/health/, () => ({ status: 200, body: { status: 'ok' } })],
      [/^https:\/\/pin\.example\.com\/api\/profiles/, () => ({ status: 401, raw: '<html>PIN</html>' })],
      [/\/api\/health/, () => ({ status: 200, body: { status: 'ok' } })],
      [/\/api\/profiles/, (u, h) => (h['X-DZ-Key'] === 'secret-key-123' ? { status: 200, body: { profiles: [{ id: 'p1', name: 'Ali' }] } }
        : { status: 401, body: { error: { code: 'auth_required', message: 'access key required' } } })],
    ] });
    await e.settle();
    const inp = findAll(e.screen, 'fld-in')[0], kin = findAll(e.screen, 'fld-in')[1];
    assert(kin, 'key box present');
    inp.value = 'https://pin.example.com'; inp.oninput(); inp.dzSubmit();
    await e.settle();
    assert(/vekil/.test(e.text('c-status')[0]), e.text('c-status')[0]);
    inp.value = 'https://ext.example.com'; inp.oninput(); inp.dzSubmit();
    await e.settle();
    assert(/Erişim anahtarı/.test(e.text('c-status')[0]) && !e.ls['dzproto.server'], 'auth error, not saved');
    assert(/\bon\b/.test(findAll(e.screen, 'fld')[1].className), 'focus on key box');
    kin.value = ' secret-key-123 '; kin.oninput(); kin.dzSubmit();
    await e.settle();
    assert.equal(e.ls['dzproto.server'], 'https://ext.example.com');
    assert.equal(e.ls['dzproto.key'], 'secret-key-123');
    assert.deepEqual(e.text('c-title'), ['Kim izliyor?']);
    assert.equal(e.api.keyFor('https://ext.example.com/api/subtitles/x.vtt'), 'secret-key-123');
    assert.equal(e.api.keyFor('https://ext.example.com/api/stream-proxy/t'), '', 'signed proxy: no header');
    assert.equal(e.api.keyFor('https://cdn.other.com/a.m3u8'), '', 'third party: never');
  }
  console.log('server_error: ok');
})().catch(err => { console.error(err); process.exit(1); });
