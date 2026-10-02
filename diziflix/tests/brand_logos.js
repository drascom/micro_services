// Brand images (docs/brand -> tizen-client/img + icon.png): files exist with sane sizes, every use site really references the image in the DOM
// (profile picker, boot skeleton, playback loading modal, top-bar emblem, error screens), none of them is a remote focus stop, the CSS sizes keep the
// PNG aspect ratios (2x crisp), and the top bar / focus order stay as they were. Run from the repo root: node tests/brand_logos.js
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, find, findAll, walk } = require('./_fake_dom');

const ROOT = path.join(__dirname, '..');
const CLIENT = path.join(ROOT, 'tizen-client');
const read = f => fs.readFileSync(path.join(CLIENT, f), 'utf8');
const tick = async (n = 10) => { for (let i = 0; i < n; i++) await Promise.resolve(); };

function png(rel) {
  const p = path.join(CLIENT, rel), buf = fs.readFileSync(p);
  assert.equal(buf.slice(1, 4).toString(), 'PNG', rel + ' is a PNG');
  return { w: buf.readUInt32BE(16), h: buf.readUInt32BE(20), kb: buf.length / 1024 };
}
const dimOf = (css, selector) => {
  const m = new RegExp(selector.replace(/[.]/g, '\\.') + '\\{[^}]*\\}').exec(css.replace(/\/\*[\s\S]*?\*\//g, ''));
  assert(m, 'css rule ' + selector);
  const w = /width:(\d+)px/.exec(m[0]), h = /height:(\d+)px/.exec(m[0]);
  assert(w && h, selector + ' has explicit width/height');
  return { w: +w[1], h: +h[1], rule: m[0] };
};
const imgs = root => { const out = []; walk(root, n => { if (n.tagName === 'IMG') out.push(n); }); return out; };
const focusStops = root => { const out = []; walk(root, n => { if (n.getAttribute && n.getAttribute('data-nav') !== null) out.push(n); }); return out; };

// ---------------------------------------------------------------- files
{
  // brand sources kept in the repo (originals), derived images small enough for the TV package
  for (const f of ['docs/brand/logo-a.png', 'docs/brand/logo-b.png']) assert(fs.existsSync(path.join(ROOT, f)), f + ' (brand source) exists');
  const spec = { 'img/logo-wide.png': [720, 710], 'img/logo-square.png': [630, 640], 'img/mascot.png': [310, 240], 'img/mascot-sm.png': [124, 96], 'img/favicon.png': [128, 128], 'icon.png': [512, 512] };
  for (const [rel, [w, h]] of Object.entries(spec)) {
    const p = png(rel);
    assert.deepEqual([p.w, p.h], [w, h], rel + ' size');
    assert(p.kb < 150, rel + ' < 150 KB, got ' + p.kb.toFixed(1));
  }
  const cfg = read('config.xml');
  assert(/<icon src="icon\.png"\/>/.test(cfg), 'config.xml icon -> icon.png');
  const html = read('index.html');
  assert(/<link rel="icon" type="image\/png" href="img\/favicon\.png">/.test(html), 'web favicon linked');
  assert(!/responsive-v31/.test(html) && /responsive-v32/.test(html), 'cache-bust bumped to v32');
}

// ---------------------------------------------------------------- CSS: sizes keep the PNG aspect ratio, 2x assets, no GPU-heavy effects
{
  const base = read('css/base.css'), prof = read('css/profiles.css'), home = read('css/home.css'), sets = read('css/settings.css');
  const check = (css, sel, file, minScale) => {
    const d = dimOf(css, sel), p = png(file);
    assert(Math.abs(d.w / d.h - p.w / p.h) < 0.012, `${sel} ${d.w}x${d.h} keeps ${file} ratio ${p.w}x${p.h}`);
    assert(p.w >= d.w * (minScale || 1.9), `${file} is >= ${minScale || 2}x of its ${d.w}px display width (crisp on 4K TVs)`);
    return d;
  };
  check(prof, '.profiles .brand-logo', 'img/logo-wide.png');
  check(home, '.boot-logo-img', 'img/logo-square.png');
  check(base, '.loading-mascot', 'img/mascot.png');
  check(base, '.brand-mascot', 'img/mascot.png', 1.55);
  const emb = check(home, '.main-navigation .brand-emblem', 'img/mascot-sm.png');
  assert.equal(emb.h, 48, 'top-bar emblem is 48px tall');
  assert(/order:-1/.test(emb.rule), 'emblem sits left of the DIZIFLIX text (flex order)');
  assert(/\.main-navigation \.wordmark\{[^}]*cursor:pointer[^}]*display:flex/.test(home), 'logo keeps the pointer cursor');
  assert(/\.set-info\{[^}]*url\(\.\.\/img\/mascot-sm\.png\)/.test(sets), 'settings footer stamp (background only, layout untouched)');
  const code = [prof, home, sets, base].join('\n').replace(/\/\*[\s\S]*?\*\//g, '');
  const boot = /\.boot-logo\{[^}]*\}/.exec(code)[0];
  assert(!/box-shadow|filter|blur/.test(boot) && /animation:dz-fade-in/.test(boot) && /@keyframes dz-fade-in\{from\{opacity:0;\}to\{opacity:1;\}\}/.test(code), 'boot logo: opacity fade only');
  assert(!/transform/.test(boot), 'boot logo: no transform animation');
  assert(/\.topbar\{[^}]*height:120px/.test(base), 'top bar height unchanged (120px)');
}

// ---------------------------------------------------------------- DOM: boot skeleton (real skeleton.js)
{
  const window = { DZ: {} };
  vm.runInNewContext(read('js/ui/skeleton.js'), { window, document: { createElement: el, createDocumentFragment: () => el('fragment') } });
  const home = window.DZ.skeleton.home();
  const page = home.children[0];
  assert.equal(page.id, 'page');
  const logo = find(page, 'boot-logo');
  assert(logo, 'boot logo in the home skeleton');
  const [im] = imgs(logo);
  assert.equal(im.src, 'img/logo-square.png'); assert.equal(im.alt, 'DiziFlix'); assert.equal(im.className, 'boot-logo-img');
  assert.equal(focusStops(page).length, 0, 'skeleton has no focus stops');
  assert(findAll(page, 'row').length === 5 && find(page, 'hero'), 'shimmer skeleton (hero + 5 rows) still there behind the logo');
  im.onerror(); assert(/hidden/.test(logo.className), 'a missing image just hides the logo');
  assert(window.DZ.skeleton.bootLogo, 'bootLogo exported');
  assert(!find(window.DZ.skeleton.detail(), 'boot-logo'), 'detail skeleton has no logo');
}

// ---------------------------------------------------------------- DOM: playback loading modal (real modal.js)
{
  const overlay = el('div');
  const window = { DZ: { keys: { onKey() { return () => {}; } }, nav: { setEnabled() {} } } };
  const sandbox = { window, console: { log() {} }, document: { createElement: el, getElementById: () => overlay }, setTimeout, clearTimeout, setInterval, clearInterval };
  vm.createContext(sandbox);
  vm.runInContext(read('js/proverbs.js'), sandbox);
  vm.runInContext(read('js/ui/modal.js'), sandbox);
  const ctl = window.DZ.modal.loading({});
  const box = find(overlay, 'loading-box');
  assert.deepEqual(box.children.map(c => c.className.split(' ')[0]), ['loading-mascot', 'loading-spinner', 'loading-proverb', 'loading-stage'], 'character above the spinner, rest unchanged');
  const [im] = imgs(box);
  assert.equal(im.src, 'img/mascot.png'); assert.equal(im.getAttribute('data-nav'), null);
  assert.equal(focusStops(overlay).length, 0, 'loading modal has no focus stops');
  ctl.close();
}

// ---------------------------------------------------------------- DOM: top-bar emblem (real navigation.js)
{
  const store = {};
  const window = { DZ: { api: { profileId: () => 'p1' }, store: { get: (k, d) => (store[k] === undefined ? d : store[k]), set: (k, v) => { store[k] = v; } }, app: { go() {} } }, setTimeout, clearTimeout };
  vm.runInNewContext(read('js/ui/navigation.js'), { window, document: { createElement: el, addEventListener() {}, activeElement: null }, console });
  const bar = window.DZ.navigation.create('mylist');
  const logo = bar.children[0];
  assert.equal(logo.className, 'wordmark'); assert.equal(logo.textContent, 'DIZIFLIX', 'the yellow spaced DIZIFLIX text stays');
  assert.equal(logo.getAttribute('data-nav'), null, 'logo is not a focus stop');
  assert.strictEqual(bar.dzLogo, logo, 'click target = emblem + text together (one node)');
  assert.equal(typeof logo.listeners.click, 'function');
  const [emblem] = imgs(logo);
  assert(emblem, 'emblem inside the logo node'); assert.equal(emblem.src, 'img/mascot-sm.png'); assert.equal(emblem.className, 'brand-emblem'); assert.equal(emblem.getAttribute('data-nav'), null);
  assert.equal(focusStops(logo).length, 0);
  assert.deepEqual(bar.querySelectorAll('[data-nav]').filter(n => n.getAttribute('data-nav-off') !== '1').map(n => (n.tagName === 'INPUT' ? 'input' : n.textContent)), ['input', 'Listem', 'Profil', 'Ayarlar'], 'focus order unchanged');
  assert.equal(bar.getAttribute('data-nav-row'), 'topbar');
  emblem.onerror(); assert(/hidden/.test(emblem.className), 'a missing emblem just hides');
}

// ---------------------------------------------------------------- DOM: profile picker + its error screen (real profiles.js)
function profileWorld(profilesFn) {
  const log = { go: [] };
  const window = { DZ: {}, console: { log() {} } };
  const DZ = window.DZ;
  const sandbox = { window, document: { createElement: el }, console: { log() {} }, setTimeout, clearTimeout };
  vm.createContext(sandbox);
  DZ.api = { profiles: profilesFn, profileId: () => null, setProfileId() {} };
  DZ.card = { sized: u => u };
  DZ.modal = { open() {}, confirm() {} };
  DZ.toast = { errorText: e => e.message };
  DZ.navigation = { backButton: () => null };
  DZ.nav = { setRoot() {}, focusRowById() { return true; }, currentEl() { return null; } };
  DZ.app = { go: (n, p) => log.go.push({ n, p }), back() {} };
  vm.runInContext(read('js/screens/profiles.js'), sandbox);
  return { DZ, log, container: el('div') };
}

(async () => {
  // normal picker
  {
    const w = profileWorld(() => Promise.resolve({ profiles: [{ id: 'p1', name: 'Ali' }, { id: 'p2', name: 'Ayşe', is_kids: true }] }));
    w.DZ.screens.profiles.enter(w.container); await tick();
    const root = find(w.container, 'profiles');
    assert(root, 'picker built');
    assert.deepEqual(root.children.map(c => c.className.split(' ')[0]), ['brand-logo', '', 'profile-strip', 'foot', 'nm'], 'wide logo on top (replaces the text wordmark), then title, profiles, buttons, hint');
    const logo = root.children[0];
    assert.equal(logo.tagName, 'IMG'); assert.equal(logo.src, 'img/logo-wide.png'); assert.equal(logo.alt, 'DiziFlix');
    assert.equal(logo.getAttribute('data-nav'), null, 'logo is not a focus stop');
    assert.equal(root.children[1].textContent, 'Kim izliyor?');
    // focus order unchanged: every data-nav is a profile tile or a bottom button
    assert.deepEqual(focusStops(root).map(n => n.textContent || n.getAttribute('data-profile-id')), ['p1', 'p2', 'Profil Ekle', 'Profilleri Duzenle', 'Ayarlar'].map(x => x));
    logo.onerror(); assert(/hidden/.test(logo.className), 'a missing logo just hides');
    // manage-mode rebuild keeps the logo
    findAll(w.container, 'btn').find(b => b.textContent === 'Profilleri Duzenle').click();
    assert.equal(imgs(find(w.container, 'profiles')).filter(i => i.src === 'img/logo-wide.png').length, 1, 'logo after rebuild');
  }
  // error screen: small character + the wordmark, buttons unchanged
  {
    const w = profileWorld(() => Promise.reject(new Error('Sunucuya ulasilamiyor')));
    w.DZ.screens.profiles.enter(w.container); await tick();
    const box = find(w.container, 'errscreen');
    assert(box, 'error screen');
    assert(/img class="brand-mascot" src="img\/mascot\.png"/.test(box.innerHTML), 'small character on the profile error screen');
    assert.deepEqual(findAll(box, 'btn').map(b => b.textContent), ['Tekrar dene', 'Ayarlar']);
    assert(findAll(box, 'btn').every(b => b.getAttribute('data-nav') === '1'));
  }
  console.log('Brand logos: assets + sizes, profile picker logo, boot skeleton logo, loading-modal character, top-bar emblem, error screens, no focus stops: OK');
})().catch(e => { console.error(e); process.exit(1); });
