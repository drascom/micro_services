// Settings screen with the REAL api.js + tracks.js: default subtitle language (same dz_pref_sub_<profile> key the player panel writes),
// "En yüksek kalite" (dz_pref_quality), persistence across screen re-entry, cache clearing that keeps preferences/profile/server address,
// connection test (latency, server info, revert of a failing NEW address), build stamp + server address display, no-profile hint.
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, find, findAll, walk } = require('./_fake_dom');

const root = path.join(__dirname, '../tizen-client/js');
const read = f => fs.readFileSync(path.join(root, f), 'utf8');
const tick = async (n = 10) => { for (let i = 0; i < n; i++) await Promise.resolve(); };

function memory(seed) {
  const data = Object.assign({}, seed || {});
  return { data,
    getItem: k => (Object.prototype.hasOwnProperty.call(data, k) ? data[k] : null),
    setItem: (k, v) => { data[k] = String(v); }, removeItem: k => { delete data[k]; },
    get length() { return Object.keys(data).length; }, key: i => Object.keys(data)[i] || null };
}

function world(seed, o) {
  o = o || {};
  const store = memory(seed);
  const log = { go: [], back: 0, fetched: [], toasts: [] };
  const window = { localStorage: store, location: { search: '' }, DZ: {}, DZ_BUILD: '2026-09-30 12:34', setTimeout, clearTimeout,
    navigator: { onLine: true } };
  const DZ = window.DZ;
  const sandbox = { window, document: { createElement: el, activeElement: null }, console: { log() {} }, setTimeout, clearTimeout,
    fetch: (url) => { log.fetched.push(url); if (/bad/.test(url)) return Promise.reject(new Error('down'));
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify({ status: 'ok', source: 'library', items: 101, cache_age: 3 })) }); } };
  vm.createContext(sandbox);
  ['api.js', 'tracks.js', 'ui/toast.js'].forEach(f => vm.runInContext(read(f), sandbox));
  DZ.toast.show = m => log.toasts.push(m);
  DZ.nav = { setRoot() {}, focusRowById() { return true; }, onFocus() {}, refresh() {} };
  DZ.app = { go: (n, p, r) => log.go.push({ n, p, r }), back() { log.back++; } };
  vm.runInContext(read('screens/settings.js'), sandbox);
  const w = { store, log, DZ, window, container: el('div'), screen: DZ.screens.settings,
    open() { w.container = el('div'); w.screen.enter(w.container); },
    // every focusable control: action buttons (.set-btn) and radio options (.opt)
    controls() { return findAll(w.container, 'set-btn').concat(findAll(w.container, 'opt')); },
    btn(label) { return w.controls().find(b => (b.dzLabel || b.textContent) === label); },
    // radio list by its accessible name; 'pref-sub' / 'pref-quality' kept as the local ids
    group(id) { return findAll(w.container, 'set-radios').find(r => r.getAttribute('aria-label') === { 'pref-sub': 'Varsayılan altyazı dili', 'pref-quality': 'En yüksek kalite' }[id]); },
    navRows() { const out = []; walk(w.container, n => { const r = n.getAttribute('data-nav-row'); if (r) out.push(r); }); return out; },
    marks(id) { return w.group(id).children.filter(b => b.getAttribute('aria-checked') === 'true').map(b => b.dzLabel); },
    status: () => find(w.container, 'set-status').textContent,
    info: () => find(w.container, 'set-info').children.map(c => c.textContent) };
  return w;
}

(async () => {
  const seed = { 'dz.profile': 'p1', 'dz.baseUrl': 'http://a:8090', 'dz.boot.featured-hero-v3.http://a:8090:p1:': '{}', 'dz.row.series': '[]',
    'dz.search.query': 'abc', 'dz_pref_sub_p1': 'de', 'dz_pref_audio_p1': 'en' };

  // ---- layout, defaults and the "other language" hint
  const w = world(seed);
  w.open(); await tick();
  assert.deepEqual(w.group('pref-sub').children.map(b => b.dzLabel), ['Türkçe', 'İngilizce', 'Kapalı']);
  assert.deepEqual(w.group('pref-quality').children.map(b => b.dzLabel), ['1080p', '1440p', 'Otomatik']);
  assert.deepEqual(w.marks('pref-sub'), [], 'a language chosen in the player (Almanca) is not one of the 3 options: none marked');
  assert(findAll(w.container, 'set-hint').some(h => /Almanca/.test(h.textContent)), 'hint names the current language');
  assert.deepEqual(w.marks('pref-quality'), ['1080p'], '1080p is the default');
  assert(w.controls().every(b => b.getAttribute('data-nav') === '1'), 'every control focusable');
  assert.deepEqual(w.container.querySelectorAll('input').length, 1);
  assert.deepEqual(w.navRows(), ['settings-top', 'url', 'prefs', 'settings-cache'], 'focus rows, top to bottom');
  const info = w.info();
  assert.equal(info[0], 'Sürüm: build 2026-09-30 12:34   ·   Motor: HTML5 video', 'build stamp shown');
  assert(/Sunucu adresi: http:\/\/a:8090/.test(info[1]), 'server address shown: ' + info[1]);
  await tick();
  assert.equal(w.info()[2], 'Sunucu: library · 101 içerik', 'server info from /api/health at entry');

  // ---- subtitle preference: same key as the player panel, persisted
  w.btn('İngilizce').click();
  assert.equal(w.store.data['dz_pref_sub_p1'], 'en'); assert.deepEqual(w.marks('pref-sub'), ['İngilizce']);
  assert.equal(w.DZ.tracks.loadPrefs().sub, 'en', 'the tracks module (playflow/player) reads it');
  assert(/Varsayılan altyazı: İngilizce/.test(w.status()));
  w.btn('Kapalı').click(); assert.equal(w.store.data['dz_pref_sub_p1'], 'off'); assert.deepEqual(w.marks('pref-sub'), ['Kapalı']);
  w.btn('Türkçe').click(); assert.equal(w.store.data['dz_pref_sub_p1'], 'tr');
  assert.equal(w.store.data['dz_pref_audio_p1'], 'en', 'audio preference untouched');

  // ---- quality preference
  w.btn('1440p').click();
  assert.equal(w.store.data['dz_pref_quality'], '1440'); assert.equal(w.DZ.api.qualityPref(), '1440'); assert.deepEqual(w.marks('pref-quality'), ['1440p']);
  w.btn('Otomatik').click(); assert.equal(w.DZ.api.qualityPref(), 'auto'); assert(/Otomatik/.test(w.status()));
  w.btn('1080p').click(); assert.equal(w.DZ.api.qualityPref(), '1080'); assert.equal(w.store.data['dz_pref_quality'], '1080');
  w.btn('1440p').click();

  // ---- persistence: leave and re-enter, everything is still selected
  w.screen.exit();
  w.open(); await tick();
  assert.deepEqual(w.marks('pref-sub'), ['Türkçe']); assert.deepEqual(w.marks('pref-quality'), ['1440p']);
  assert(!findAll(w.container, 'set-hint').some(h => /şu an/.test(h.textContent)), 'hint gone once a listed language is stored');
  assert.equal(w.DZ.api.qualityPref(), '1440');
  w.store.data['dz_pref_quality'] = 'garbage';
  assert.equal(w.DZ.api.qualityPref(), '1080', 'invalid stored value falls back to the default');
  w.store.data['dz_pref_quality'] = '1440';

  // ---- clear cache: boot/row caches only
  w.btn('Önbelleği temizle').click();
  assert(!('dz.boot.featured-hero-v3.http://a:8090:p1:' in w.store.data) && !('dz.row.series' in w.store.data), 'boot and row caches removed');
  ['dz.profile', 'dz.baseUrl', 'dz.search.query', 'dz_pref_sub_p1', 'dz_pref_audio_p1', 'dz_pref_quality'].forEach(k => assert(k in w.store.data, k + ' kept'));
  assert(/Önbellek temizlendi \(2 kayıt\)/.test(w.status()) && /tercihler/.test(w.status()));
  assert.deepEqual(w.log.toasts, ['Önbellek temizlendi']);
  assert.equal(w.DZ.api.clearCaches(), 0, 'nothing left to clear');

  // ---- connection test: latency + server info; a failing NEW address is reverted, "Kaydet" keeps it
  w.screen.exit(); w.open(); await tick();
  const input = w.container.querySelectorAll('input')[0];
  input.value = 'good:9000';
  w.btn('Bağlantıyı test et').click(); await tick();
  assert.equal(w.DZ.api.baseUrl(), 'http://good:9000', 'a working new address is kept (scheme added)');
  assert(/^Bağlantı tamam · \d+ ms · kaynak: library · içerik: 101$/.test(w.status()), w.status());
  assert.equal(input.value, 'http://good:9000'); assert(/Sunucu adresi: http:\/\/good:9000/.test(w.info()[1]));
  input.value = 'http://bad:1';
  w.btn('Bağlantıyı test et').click(); await tick();
  assert.equal(w.DZ.api.baseUrl(), 'http://good:9000', 'failing test reverts to the previous working address');
  assert.equal(input.value, 'http://good:9000');
  assert(/^Başarısız: .*\(yeni adres kaydedilmedi\)$/.test(w.status()), w.status());
  assert.equal(w.info()[2], 'Sunucu: ulaşılamıyor');
  input.value = 'http://bad:1';
  w.btn('Kaydet').click(); await tick();
  assert.equal(w.DZ.api.baseUrl(), 'http://bad:1', '"Kaydet" stores the address even when unreachable');
  assert(/^Başarısız: /.test(w.status()) && !/kaydedilmedi/.test(w.status()));

  // ---- profile switch, back
  w.btn('Profil değiştir').click();
  assert.equal(w.DZ.api.profileId(), null); assert.deepEqual(w.log.go[0], { n: 'profiles', p: null, r: true });
  assert(!w.btn('Geri'), 'no bottom "Geri" button (TV Back key / the top "← Geri" cover it)');
  assert.equal(w.screen.back(), true); assert.equal(w.log.back, 1, 'Back key handler unchanged');
  w.screen.exit();

  // ---- no profile selected: the (per-profile) subtitle list is replaced by a hint, quality stays
  const np = world({ 'dz.baseUrl': 'http://a:8090' });
  np.open(); await tick();
  assert(!np.group('pref-sub'), 'no subtitle list without a profile');
  assert(findAll(np.container, 'set-note').some(l => /Önce bir profil seçin/.test(l.textContent)), 'hint in the subtitle card');
  np.btn('1440p').click(); assert.equal(np.store.data['dz_pref_quality'], '1440', 'quality is device-wide: works without a profile');
  assert(!Object.keys(np.store.data).some(k => k === 'dz_pref_sub_'), 'nothing written under an empty profile key');

  console.log('Settings: subtitle + quality preferences persist, cache clear keeps prefs, connection test/revert, build stamp + server info: OK');
})().catch(e => { console.error(e); process.exitCode = 1; });
