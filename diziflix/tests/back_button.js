// Mouse/touch "← Geri" button on Settings, Profiles, Profile edit and the film/series Detail hero (top-left): same action as the Back key (DZ.app.goBack), never part of the
// remote focus order (no data-nav), and only shown when there is somewhere to go back to (not on the very first profile picker).
// Part 1: real app.js -> goBack / canGoBack. Part 2: real navigation.js + the three real screens.
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, find, findAll, walk } = require('./_fake_dom');

const read = f => fs.readFileSync(path.join(__dirname, '../tizen-client/js', f), 'utf8');
const tick = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

function memory() {
  const data = {};
  return { data, getItem: k => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); }, removeItem: k => { delete data[k]; },
    get length() { return Object.keys(data).length; }, key: i => Object.keys(data)[i] || null };
}

// ---------- part 1: real app.js ----------
{
  let ready = null;
  const calls = [];
  const screenEl = el('div');
  const document = {
    readyState: 'loading', addEventListener(type, fn) { if (type === 'DOMContentLoaded') ready = fn; },
    getElementById(id) { return id === 'screen' ? screenEl : { style: {}, appendChild() {} }; }, createElement: el
  };
  let modalOpen = false;
  const screen = (name, hasBack) => {
    const s = { name, enter() { calls.push('enter:' + name); }, exit() {} };
    if (hasBack) s.back = function () { calls.push('back:' + name); return true; };
    return s;
  };
  const window = { innerWidth: 1920, innerHeight: 1080, addEventListener() {}, document, DZ: {
    screens: { profiles: screen('profiles', true), settings: screen('settings', true), plain: screen('plain', false) },
    nav: { snapshot() { return null; }, reset() {}, restoreNext() {} }, keys: { init() {}, onKey() {} },
    api: { profileId: () => null, baseUrl: () => 'http://s', debug: {} }, modal: { isOpen: () => modalOpen, confirm() {} }
  } };
  const ctx = vm.createContext({ window, document, console: { log() {} }, setTimeout, tizen: undefined });
  vm.runInContext(read('app.js'), ctx);
  ready();
  const app = window.DZ.app;
  assert.strictEqual(app.currentName(), 'profiles'); assert.strictEqual(app.canGoBack(), false, 'first launch: nothing to go back to');
  app.go('settings');
  assert.strictEqual(app.canGoBack(), true);
  calls.length = 0;
  app.goBack();
  assert.deepStrictEqual(calls, ['back:settings'], 'goBack uses the screen\'s own Back handler, exactly like the Back key');
  app.go('plain');
  calls.length = 0;
  app.goBack();
  assert.strictEqual(app.currentName(), 'settings', 'screens without a handler: plain stack back'); assert.deepStrictEqual(calls, ['enter:settings']);
  modalOpen = true; calls.length = 0; app.goBack();
  assert.deepStrictEqual(calls, [], 'a click never acts under an open modal'); modalOpen = false;
}

// ---------- part 2: the buttons on the real screens ----------
function world(depth) {
  const store = memory();
  const log = { goBack: 0, back: 0 };
  const window = { localStorage: store, location: { search: '' }, DZ: {}, DZ_BUILD: 'x', setTimeout, clearTimeout, navigator: { onLine: true } };
  const DZ = window.DZ;
  const sandbox = { window, document: { createElement: el, activeElement: null }, console: { log() {} }, setTimeout, clearTimeout,
    fetch: () => Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify({ status: 'ok', source: 'library', items: 1 })) }) };
  vm.createContext(sandbox);
  ['api.js', 'tracks.js', 'ui/toast.js', 'ui/navigation.js'].forEach(f => vm.runInContext(read(f), sandbox));
  DZ.api.profiles = () => Promise.resolve({ profiles: [{ id: 'p1', name: 'Ali' }] });
  DZ.api.avatars = () => Promise.resolve({ avatars: [] });
  DZ.card = { sized: u => u };
  DZ.modal = { open() {}, confirm() {} };
  DZ.nav = { setRoot() {}, focusRowById() { return true; }, onFocus() {}, refresh() {}, currentEl() { return null; } };
  DZ.app = { go() {}, back() { log.back++; }, goBack() { log.goBack++; }, canGoBack: () => depth.value > 1 };
  ['screens/settings.js', 'screens/profiles.js', 'screens/profile_edit.js'].forEach(f => vm.runInContext(read(f), sandbox));
  return { DZ, log, container: el('div') };
}
const backBtns = container => findAll(container, 'back-btn');

// real navigation.js + real detail.js (film and series)
function detailWorld(depth, data) {
  const log = { goBack: 0, back: 0 };
  const window = { localStorage: memory(), location: { search: '' }, DZ: {}, DZ_BUILD: 'x', requestAnimationFrame: f => f(), Image: function () { return el('img'); },
    setTimeout, clearTimeout, navigator: { onLine: true } };
  const DZ = window.DZ;
  const sandbox = { window, Image: window.Image, document: { createElement: el, activeElement: null }, console: { log() {} }, setTimeout, clearTimeout };
  vm.createContext(sandbox);
  ['ui/navigation.js'].forEach(f => vm.runInContext(read(f), sandbox));
  DZ.api = { profileId: () => 'p1', detail: () => Promise.resolve(data) };
  DZ.nav = { setRoot() {}, onFocus() {}, focusRowById() { return true; }, focusIndex() {}, currentEl() { return null; }, refresh() {}, restoreNext() {}, rowIds() { return []; },
    current() { return null; }, snapshot() { return null; } };
  DZ.card = { sized: x => x || '', sizedTo: p => p };
  DZ.skeleton = { detail: () => el('div') };
  DZ.row = { create() { return el('section'); } };
  DZ.modal = { text() {}, open() {} };
  DZ.toast = { show() {}, showRich() {}, errorText: e => e.message, isOffline: () => false };
  DZ.playflow = { start() {} };
  DZ.app = { go() {}, back() { log.back++; }, goBack() { log.goBack++; }, canGoBack: () => depth.value > 1 };
  vm.runInContext(read('screens/detail.js'), sandbox);
  return { DZ, log, container: el('div') };
}
const base = { overview: '', genres: [], backdrop: '', similar: [], availability: { state: 'ready', reason: null, has_trailer: false }, in_mylist: false, progress: null };
const movieData = Object.assign({ id: 'm', type: 'movie', title: 'Film', seasons: [], playback: 'video' }, base);
const seriesData = Object.assign({ id: 's', type: 'series', title: 'Dizi', seasons: [{ season: 1, title: '1. Sezon', name: '1. Sezon', overview: '', air_date: null, poster_url: '', has_poster: false, episode_count: 1,
  episodes: [{ id: 's:s1:e1', season: 1, episode: 1, title: 'Bolum 1', overview: '', runtime: 42, still: '', still_url: '', has_still: false, air_date: '2020-01-01', availability: { state: 'ready' }, progress: null }] }] }, base);
function assertButton(w, label) {
  const btns = backBtns(w.container);
  assert.strictEqual(btns.length, 1, label + ': exactly one back button');
  assert.strictEqual(btns[0].textContent, '← Geri');
  assert.strictEqual(btns[0].getAttribute('data-nav'), null, label + ': not in the remote focus order');
  let focusable = false; walk(btns[0], n => { if (n.getAttribute('data-nav') !== null) focusable = true; }); assert(!focusable);
  const before = w.log.goBack;
  btns[0].click();
  assert.strictEqual(w.log.goBack, before + 1, label + ': click = the Back key action (DZ.app.goBack)');
}

(async () => {
  // settings
  let depth = { value: 2 }, w = world(depth);
  w.DZ.screens.settings.enter(w.container); await tick();
  assertButton(w, 'settings');
  assert(findAll(w.container, 'set-btn').concat(findAll(w.container, 'opt')).every(b => b.getAttribute('data-nav') === '1'), 'settings buttons unchanged');
  w.DZ.screens.settings.back(); assert.strictEqual(w.log.back, 1, 'Back key path unchanged');
  depth.value = 1; w = world(depth);
  w.DZ.screens.settings.enter(w.container); await tick();
  assert.strictEqual(backBtns(w.container).length, 0, 'settings: nowhere to go back to -> no button');

  // profile picker: first launch has nowhere to go, later (opened from settings) it does
  depth = { value: 1 }; w = world(depth);
  w.DZ.screens.profiles.enter(w.container); await tick();
  assert(find(w.container, 'profiles'), 'picker built'); assert.strictEqual(backBtns(w.container).length, 0, 'profile not chosen yet / first launch: no back button');
  depth.value = 3; w = world(depth);
  w.DZ.screens.profiles.enter(w.container); await tick();
  assertButton(w, 'profiles');
  assert.strictEqual(find(w.container, 'profiles').children.includes(backBtns(w.container)[0]), true);
  // manage-mode rebuild keeps the decision
  findAll(w.container, 'btn').find(b => b.textContent === 'Profilleri Duzenle').click();
  assert.strictEqual(backBtns(w.container).length, 1);

  // profile edit
  depth = { value: 2 }; w = world(depth);
  w.DZ.screens.profile_edit.enter(w.container, { id: null }); await tick();
  assert(find(w.container, 'pe'), 'editor built'); assertButton(w, 'profile_edit');
  depth.value = 1; w = world(depth);
  w.DZ.screens.profile_edit.enter(w.container, { id: null }); await tick();
  assert.strictEqual(backBtns(w.container).length, 0);

  // detail: film AND series get the hero back button; click = Back key action (goBack), never a focus stop
  for (const [label, data] of [['film', movieData], ['series', seriesData]]) {
    depth = { value: 2 }; w = detailWorld(depth, data);
    w.DZ.screens.detail.enter(w.container, { id: data.id }); await tick();
    assert(find(w.container, 'detail-hero'), label + ': detail built');
    assertButton(w, 'detail ' + label);
    assert.strictEqual(find(w.container, 'detail-hero').children.includes(backBtns(w.container)[0]), true, label + ': button sits in the hero (top-left over the backdrop)');
    // focus order unchanged: still exactly the same data-nav elements as before (none of them is the back button)
    const navEls = []; walk(w.container, n => { if (n.getAttribute('data-nav') !== null && n.className === 'back-btn') navEls.push(n); }); assert.strictEqual(navEls.length, 0);
    w.DZ.screens.detail.back(); assert.strictEqual(w.log.back, 1, label + ': Back key path unchanged');
    depth.value = 1; w = detailWorld(depth, data);
    w.DZ.screens.detail.enter(w.container, { id: data.id }); await tick();
    assert(find(w.container, 'detail-hero')); assert.strictEqual(backBtns(w.container).length, 0, label + ': nowhere to go back to -> no button');
  }
  // hydration placeholder does not remove or duplicate the button
  depth = { value: 2 }; w = detailWorld(depth, Object.assign({}, seriesData, { hydrating: true }));
  w.DZ.screens.detail.enter(w.container, { id: 's' }); await tick();
  assert.strictEqual(backBtns(w.container).length, 1, 'hydrating detail: one back button');

  const dcss = fs.readFileSync(path.join(__dirname, '../tizen-client/css/detail.css'), 'utf8');
  assert(/\.detail \.back-btn\{[^}]*background:rgba\(0,0,0,/.test(dcss) && /\.detail \.back-btn\{[^}]*border-radius:999px/.test(dcss) && /\.detail \.back-btn:hover\{[^}]*var\(--accent\)/.test(dcss), 'detail back pill styled');
  const zm = /\.detail \.back-btn\{[^}]*z-index:(\d+)/.exec(dcss); assert(zm && Number(zm[1]) < 9998, 'back button z-index stays below the toasts');

  // styled like the top bar buttons, pointer cursor, not reachable by the remote
  const css = fs.readFileSync(path.join(__dirname, '../tizen-client/css/base.css'), 'utf8');
  assert(/\.back-btn\{[^}]*cursor:pointer/.test(css) && /\.back-btn:hover\{/.test(css), 'back button styled');
  const home = fs.readFileSync(path.join(__dirname, '../tizen-client/css/home.css'), 'utf8');
  assert(/\.main-navigation \.wordmark\{[^}]*cursor:pointer/.test(home), 'logo has a pointer cursor');

  console.log('Back button: Settings/Profiles/Profile edit/Detail (film+series), same action as Back key, never a focus stop: OK');
})().catch(error => { console.error(error); process.exit(1); });
