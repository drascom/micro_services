// First run + cold start: no server address -> "Sunucu adresi" screen (Bağlan probes /api/health WITHOUT saving, then saves and
// opens the profile picker); with an address every cold start mounts the profile picker (never straight to home), and the
// server's guest profile is a "Misafir" button, not a card. Real api.js + app.js + server_setup.js + profiles.js.
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, findAll, walk } = require('./_fake_dom');

const read = f => fs.readFileSync(path.join(__dirname, '../tizen-client/js', f), 'utf8');
const tick = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

function world(stored, health) {
  const data = Object.assign({}, stored);
  const fetched = [];
  let ready = null;
  const screenEl = el('div');
  const document = {
    readyState: 'loading', addEventListener(type, fn) { if (type === 'DOMContentLoaded') ready = fn; },
    getElementById(id) { return id === 'screen' ? screenEl : { style: {}, appendChild() {} }; }, createElement: el, activeElement: null
  };
  const window = {
    location: { protocol: 'file:', hostname: '', port: '', search: '' }, document, DZ_CONFIG: { baseUrl: '', port: 8090 },
    localStorage: { getItem: k => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); }, removeItem: k => { delete data[k]; } },
    addEventListener() {}, setTimeout, clearTimeout, DZ: {}
  };
  const DZ = window.DZ;
  DZ.nav = { snapshot() { return null; }, reset() {}, restoreNext() {}, setRoot() {}, focusRowById() { return true; } };
  DZ.keys = { init() {}, onKey() {} };
  DZ.modal = { isOpen: () => false, confirm() {} };
  DZ.card = { sized: v => v };
  const ok = body => Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) });
  const fetch = u => {
    fetched.push(u);
    if (/\/api\/health/.test(u)) return health(u);
    if (/\/api\/profiles/.test(u)) return ok({ profiles: [
      { id: 'p1', name: 'A', avatar: '', is_kids: false, guest: false },
      { id: 'guest', name: 'Misafir', avatar: '', is_kids: false, guest: true }] });
    return Promise.reject(new Error('unexpected ' + u));
  };
  const ctx = vm.createContext({ window, document, console: { log() {} }, setTimeout, clearTimeout, fetch, encodeURIComponent,
    decodeURIComponent, JSON, Promise, RegExp, tizen: undefined });
  ['api.js', 'screens/server_setup.js', 'screens/profiles.js', 'app.js'].forEach(f => vm.runInContext(read(f), ctx));
  ready();
  return { DZ, data, fetched, screenEl };
}
const texts = root => { const out = []; walk(root, n => { if (n.textContent && !n.children.length) out.push(n.textContent); }); return out; };

(async () => {
  // 1) no address: first-run screen; a bare IP is normalised and probed, nothing is saved until /api/health answers ok
  let answer = () => Promise.reject(new Error('down'));
  const w = world({}, u => answer(u));
  assert.equal(w.DZ.app.currentName(), 'server_setup', 'no address -> first-run screen');
  const input = findAll(w.screenEl, 'ss-input')[0];
  input.value = '192.168.1.10';
  input.dzSubmitSearch();                          // remote keyboard Done = Bağlan
  await tick();
  assert.equal(w.fetched[0], 'http://192.168.1.10:8090/api/health');
  assert.ok(!('dz.baseUrl' in w.data), 'failed probe: nothing saved');
  assert.equal(w.DZ.app.currentName(), 'server_setup');
  assert.ok(texts(w.screenEl).some(t => /Bağlanılamadı/.test(t)), 'error shown');
  answer = () => Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve('{"status":"ok","source":"library","items":1,"cache_age":0}') });
  input.dzSubmitSearch();
  await tick();
  assert.equal(w.data['dz.baseUrl'], 'http://192.168.1.10:8090', 'working address saved');
  assert.equal(w.DZ.app.currentName(), 'profiles', 'then the profile picker');
  assert.equal(w.DZ.app.stack().length, 1, 'first-run screen replaced (Back does not return to it)');

  // 2) cold start with an address and a remembered profile: the picker again (not home); guest = "Misafir" button, not a card
  const c = world({ 'dz.baseUrl': 'http://192.168.1.10:8090', 'dz.profile': 'p1' }, () => Promise.reject(new Error('x')));
  assert.equal(c.DZ.app.currentName(), 'profiles', 'cold start always shows the profile picker');
  await tick();
  const cards = findAll(c.screenEl, 'profile');
  assert.deepEqual(cards.map(n => n.getAttribute('data-profile-id')), ['p1'], 'guest is not a card');
  const guestBtn = findAll(c.screenEl, 'guest')[0];
  assert.ok(guestBtn && guestBtn.textContent === 'Misafir', '"Misafir" button');
  console.log('First run: server address screen, cold start profile picker, guest button: OK');
})().catch(e => { console.error(e); process.exit(1); });
