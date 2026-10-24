// js/update.js headless test: semver compare, `diziflix-v` filter (draft/prerelease/other projects skipped), Tizen notice once per
// version, Android ready -> install prompt once, browser shows nothing. Run: node client/tests/update.js
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const SRC = fs.readFileSync(path.join(__dirname, '../js/update.js'), 'utf8');

function env(o) {
  const store = o.store || {};
  const sb = { console, DZ_VERSION: o.ver || '2.0.0', navigator: { userAgent: o.ua || 'Mozilla/5.0' } };
  sb.window = sb;
  sb.localStorage = { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } };
  if (o.tizen) sb.tizen = {};
  if (o.android) sb.DZAndroid = Object.assign({ version: () => o.ver || '2.0.0', pendingUpdate: () => '', installs: 0 }, o.android);
  sb.XMLHttpRequest = function () {
    const x = this;
    x.open = (m, u) => { x.url = u; }; x.setRequestHeader = () => {};
    x.send = () => { x.readyState = 4; x.status = o.http || 200; x.responseText = JSON.stringify(o.releases || []); x.onreadystatechange(); };
  };
  vm.createContext(sb);
  vm.runInContext(SRC, sb, { filename: 'update.js' });
  return { U: sb.DZUpdate, sb, store };
}
const RELS = [
  { tag_name: 'diziflix-v2.3.0', draft: true },
  { tag_name: 'diziflix-v2.2.0', prerelease: true },
  { tag_name: 'other-v9.0.0' },
  { tag_name: 'diziflix-v2.1.0', html_url: 'u' },
  { tag_name: 'diziflix-v2.0.5' },
  { tag_name: 'diziflix-vX' },
];

{ // compare + filter
  const { U } = env({});
  assert(U.cmp('2.10.0', '2.9.9') > 0 && U.cmp('2.0.0', '2.0.0') === 0 && U.cmp('1.9.9', '2.0.0') < 0);
  assert.equal(U.pickLatest(RELS).version, '2.1.0');
  assert.equal(U.pickLatest([{ tag_name: 'v3.0.0' }]), null);
}
{ // browser: nothing
  const { U } = env({ releases: RELS });
  assert.equal(U.kind(), 'browser');
  assert.equal(U.check(false), false);
  assert.equal(U.homePrompt(), null);
}
{ // Tizen: once per version, persisted
  const store = {};
  let e = env({ tizen: true, releases: RELS, store });
  e.U.check(false);
  const p = e.U.homePrompt();
  assert.equal(p.type, 'notify');
  assert.equal(p.title, 'Yeni sürüm 2.1.0 hazır');
  assert(/github\.com\/drascom\/micro_services\/releases/.test(p.text));
  assert.equal(e.U.homePrompt(), null, 'only once');
  e = env({ tizen: true, releases: RELS, store });   // restart (cached within 6 h)
  e.U.check(false);
  assert.equal(e.U.homePrompt(), null, 'seen version not repeated after restart');
  assert(/2\.1\.0/.test(e.U.statusText()));
  // up to date
  const u = env({ tizen: true, releases: RELS, ver: '2.1.0' });
  u.U.check(true);
  assert.equal(u.U.homePrompt(), null);
  assert.equal(u.U.statusText(), 'Güncel · 2.1.0');
  // network error: silent
  const n = env({ tizen: true, http: 0 });
  n.U.check(false);
  assert.equal(n.U.homePrompt(), null);
}
{ // Android: ready -> install prompt once per session, install -> bridge
  let installs = 0;
  const e = env({ android: { installUpdate: () => { installs++; } }, ver: '2.0.0' });
  assert.equal(e.U.kind(), 'android');
  assert.equal(e.U.homePrompt(), null);
  e.U.ready('1.9.0');
  assert.equal(e.U.homePrompt(), null, 'older ignored');
  e.U.ready('2.1.0');
  const p = e.U.homePrompt();
  assert.equal(p.type, 'install');
  assert.equal(p.title, 'Güncelleme hazır · 2.1.0');
  assert.equal(e.U.homePrompt(), null);
  assert(e.U.install());
  assert.equal(installs, 1);
}
console.log('client update: ok');
