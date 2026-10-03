// Settings screen layout + remote (D-pad) focus map, with the REAL settings.js, nav.js, api.js, tracks.js and navigation.js (fake DOM).
// Layout: the connection-test button lives INSIDE the server-address input wrapper, "Kaydet" comes after it (primary), the subtitle-language
// and max-quality choices are two side-by-side vertical radio lists (radiogroup/radio + aria-checked) in their own rounded cards, there is no
// bottom "Geri" button, "Profil değiştir" sits in the top row and "Önbelleği temizle" at the bottom. Focus: the row order is
// settings-top -> url [input, test, save] -> prefs [subtitle options…, quality options…] -> settings-cache, with custom in-card Up/Down and
// Left/Right between the cards (same / nearest row). Mirrors app.js handleKey: screen.key first, then nav.move / nav.enter.
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, find, findAll, walk } = require('./_fake_dom');

const read = f => fs.readFileSync(path.join(__dirname, '../tizen-client', f), 'utf8');
const tick = async (n = 10) => { for (let i = 0; i < n; i++) await Promise.resolve(); };

function memory(seed) {
  const data = Object.assign({}, seed || {});
  return { data,
    getItem: k => (Object.prototype.hasOwnProperty.call(data, k) ? data[k] : null),
    setItem: (k, v) => { data[k] = String(v); }, removeItem: k => { delete data[k]; },
    get length() { return Object.keys(data).length; }, key: i => Object.keys(data)[i] || null };
}

function world(seed) {
  const store = memory(seed);
  const log = { go: [], back: 0, goBack: 0, toasts: [], fetched: [] };
  const document = { createElement: el, activeElement: null };
  const window = { localStorage: store, location: { search: '' }, DZ: {}, DZ_BUILD: 'x', setTimeout, clearTimeout, navigator: { onLine: true } };
  const DZ = window.DZ;
  const sandbox = { window, document, console: { log() {} }, setTimeout, clearTimeout,
    fetch: (url) => { log.fetched.push(url); return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify({ status: 'ok', source: 'library', items: 7 })) }); } };
  vm.createContext(sandbox);
  ['api.js', 'tracks.js', 'ui/toast.js', 'nav.js', 'ui/navigation.js'].forEach(f => vm.runInContext(read('js/' + f), sandbox));
  DZ.toast.show = m => log.toasts.push(m);
  DZ.app = { go: (n, p, r) => log.go.push({ n, p, r }), back() { log.back++; }, goBack() { log.goBack++; }, canGoBack: () => true };
  vm.runInContext(read('js/screens/settings.js'), sandbox);
  const w = { store, log, DZ, document, container: el('div'), screen: DZ.screens.settings,
    open() {
      w.container = el('div'); DZ.nav.reset(); w.screen.enter(w.container);
      // a real input takes DOM focus and fires focus/blur listeners
      const input = w.input();
      input.focus = function () { document.activeElement = input; if (input.listeners.focus) input.listeners.focus(); };
      input.blur = function () { if (document.activeElement === input) document.activeElement = null; if (input.listeners.blur) input.listeners.blur(); };
    },
    input: () => w.container.querySelectorAll('input')[0],
    controls: () => findAll(w.container, 'set-btn').concat(findAll(w.container, 'opt')),
    byLabel: label => w.controls().find(b => (b.dzLabel || b.textContent) === label),
    // what app.js handleKey does for a non-typing key (and the typing Enter case, which reaches screen.key too)
    press(name) {
      if (w.screen.key({ name }) === true) return;
      if (['left', 'right', 'up', 'down'].includes(name)) DZ.nav.move(name);
      else if (name === 'enter') DZ.nav.enter();
    },
    goto(row, col) { if (w.document.activeElement === w.input()) w.input().blur(); DZ.nav.focusRowById(row, col); },
    at: () => { const e = DZ.nav.currentEl(); return e ? (e.dzLabel || e.textContent || e.tagName) : null; },
    row: () => DZ.nav.current().rowId };
  return w;
}

const isInside = (root, node) => { let f = false; walk(root, n => { if (n === node) f = true; }); return f; };
const checked = g => g.children.filter(b => b.getAttribute('aria-checked') === 'true').map(b => b.dzLabel);

(async () => {
  const seed = { 'dz.profile': 'p1', 'dz.baseUrl': 'http://a:8090' };
  const w = world(seed);
  w.open(); await tick();

  // ---------------------------------------------------------------- layout (DOM)
  const page = find(w.container, 'settings');
  assert(page, 'settings page built');
  assert.deepEqual(page.children.map(c => c.className.split(' ')[0]), ['set-top', 'set-title', 'set-card', 'set-cols', 'set-foot', 'set-info'],
    'page order: top row, title, server card, preference cards, cache button, info lines');

  // top row: NO logo; "← Geri" (mouse only) at the left edge, spacer, then "Profil değiştir" on the right, in its own nav row
  const top = page.children[0];
  assert.equal(findAll(w.container, 'wordmark').length, 0, 'settings screen has no DIZIFLIX wordmark');
  assert.equal(findAll(w.container, 'set-wordmark').length, 0);
  assert.deepEqual(top.children.map(c => c.className.split(' ')[0]), ['back-btn', 'set-spacer', 'set-top-nav']);
  assert.equal(top.children[0].textContent, '← Geri'); assert.equal(top.children[0].getAttribute('data-nav'), null, 'mouse-only Back button kept, not a focus stop');
  const topNav = top.children[2];
  assert.equal(topNav.getAttribute('data-nav-row'), 'settings-top');
  assert.deepEqual(topNav.children.map(c => c.textContent), ['Profil değiştir']);

  // server card: [input + embedded test button] then Kaydet
  const server = page.children[2];
  const urlRow = find(server, 'set-server-row');
  assert.equal(urlRow.getAttribute('data-nav-row'), 'url');
  assert.equal(urlRow.children.length, 2);
  const field = urlRow.children[0], save = urlRow.children[1];
  assert(field.classList.contains('set-field'), 'first: the input wrapper');
  const input = w.input();
  assert.equal(input.parentNode, field); assert.equal(input.getAttribute('aria-label'), 'Sunucu adresi'); assert.equal(w.container.querySelectorAll('input').length, 1);
  const test = w.byLabel('Bağlantıyı test et');
  assert(isInside(field, test), 'connection test button is embedded INSIDE the input wrapper');
  assert(field.children.indexOf(test) > field.children.indexOf(input), 'embedded at the right end (after the input)');
  assert.equal(save.textContent, 'Kaydet'); assert(save.classList.contains('primary'), 'Kaydet is the primary button');
  assert(!isInside(field, save), 'Kaydet is outside the input');
  assert(urlRow.children.indexOf(save) > urlRow.children.indexOf(field), 'Kaydet comes after the input, on the right');
  assert(find(server, 'set-status'), 'status line under the row');

  // two side-by-side vertical radio lists, each in its own card
  const cols = page.children[3];
  assert.equal(cols.getAttribute('data-nav-row'), 'prefs');
  const cards = cols.children;
  assert.equal(cards.length, 2); assert(cards.every(c => c.classList.contains('set-card')), 'each list in a rounded card');
  const groups = cards.map(c => find(c, 'set-radios'));
  assert(groups.every(g => g.getAttribute('role') === 'radiogroup'), 'radiogroup role');
  assert.deepEqual(groups.map(g => g.getAttribute('aria-label')), ['Varsayılan altyazı dili', 'En yüksek kalite']);
  assert.deepEqual(cards.map(c => find(c, 'set-card-title').textContent), ['Varsayılan altyazı dili', 'En yüksek kalite']);
  assert.deepEqual(groups[0].children.map(b => b.dzLabel), ['Türkçe', 'İngilizce', 'Kapalı']);
  assert.deepEqual(groups[1].children.map(b => b.dzLabel), ['1080p', '1440p', 'Otomatik']);
  assert(groups.every(g => g.children.every(b => b.getAttribute('role') === 'radio' && b.getAttribute('data-nav') === '1' && b.getAttribute('aria-checked') !== null)));
  assert.deepEqual(checked(groups[0]), ['Türkçe'], 'default subtitle language is marked'); assert.deepEqual(checked(groups[1]), ['1080p'], 'default quality is marked');
  assert(/bu çözünürlüğün üstündeki akışlar en son denenir/.test(find(cards[1], 'set-hint').textContent), 'quality hint kept');
  assert.equal(groups[0].children[0].classList.contains('on'), true); assert.equal(groups[0].children[1].classList.contains('on'), false);

  // bottom: no "Geri", cache button, info lines
  assert(!w.byLabel('Geri'), 'no bottom "Geri" button');
  assert.equal(w.controls().filter(b => b.textContent === 'Geri').length, 0);
  const foot = page.children[4];
  assert.equal(foot.getAttribute('data-nav-row'), 'settings-cache'); assert.deepEqual(foot.children.map(c => c.textContent), ['Önbelleği temizle']);
  assert.equal(page.children[5].children.length, 3, 'three info lines below');
  // every control is a remote focus stop with a stable focus key (focus restore after leaving the screen)
  assert(w.controls().concat(input).every(b => b.getAttribute('data-nav') === '1' && b.getAttribute('data-focus-key')));
  assert.equal(new Set(w.controls().concat(input).map(b => b.getAttribute('data-focus-key'))).size, 11, 'focus keys are unique (4 buttons + 6 options + input)');

  // ---------------------------------------------------------------- focus map
  assert.equal(w.row(), 'url'); assert.equal(w.at(), 'Kaydet', 'opening focus = Kaydet (existing behaviour; never the input: it would pop the on-screen keyboard)');
  assert(save.classList.contains('focused'));
  assert.equal(w.DZ.nav.current().rowCount, 4);

  w.press('left'); assert.equal(w.at(), 'Bağlantıyı test et');
  w.press('left'); assert.equal(w.row(), 'url'); assert.equal(w.DZ.nav.currentEl(), input); assert(field.classList.contains('is-focus'), 'wrapper shows the focus frame while typing');
  w.press('left'); assert.equal(w.DZ.nav.currentEl(), input, 'left edge of the row: stays');
  w.input().blur(); assert(!field.classList.contains('is-focus'));
  w.press('right'); assert.equal(w.at(), 'Bağlantıyı test et'); w.press('right'); assert.equal(w.at(), 'Kaydet'); w.press('right'); assert.equal(w.at(), 'Kaydet', 'right edge: stays');

  // up: top row; down: back to the server row at the remembered item
  w.press('up'); assert.equal(w.row(), 'settings-top'); assert.equal(w.at(), 'Profil değiştir');
  w.press('up'); assert.equal(w.at(), 'Profil değiştir', 'nothing above the top row');
  w.press('left'); w.press('right'); assert.equal(w.at(), 'Profil değiştir', 'single item row');
  w.press('down'); assert.equal(w.row(), 'url'); assert.equal(w.at(), 'Kaydet');

  // down into the preference cards: first subtitle option; Up/Down stay inside the card
  w.press('down'); assert.equal(w.row(), 'prefs'); assert.equal(w.at(), 'Türkçe');
  w.press('down'); assert.equal(w.at(), 'İngilizce'); w.press('down'); assert.equal(w.at(), 'Kapalı');
  w.press('down'); assert.equal(w.row(), 'settings-cache', 'past the last option: the cache button'); assert.equal(w.at(), 'Önbelleği temizle');
  w.press('down'); assert.equal(w.at(), 'Önbelleği temizle', 'nothing below the cache button');
  w.press('up'); assert.equal(w.row(), 'prefs'); assert.equal(w.at(), 'Kapalı', 'coming back up returns to the last visited option');

  // Left/Right between the cards, same row; edges stay
  w.press('right'); assert.equal(w.at(), 'Otomatik', 'same row in the quality card');
  w.press('right'); assert.equal(w.at(), 'Otomatik', 'right edge: stays in the quality card');
  w.press('up'); assert.equal(w.at(), '1440p'); w.press('up'); assert.equal(w.at(), '1080p');
  w.press('left'); assert.equal(w.at(), 'Türkçe', 'same row in the subtitle card'); w.press('left'); assert.equal(w.at(), 'Türkçe', 'left edge: stays');
  w.press('down'); w.press('right'); assert.equal(w.at(), '1440p'); w.press('left'); assert.equal(w.at(), 'İngilizce');

  // top of a card: Up goes to the server row (remembered item = Kaydet)
  w.press('up'); assert.equal(w.at(), 'Türkçe'); w.press('up'); assert.equal(w.row(), 'url'); assert.equal(w.at(), 'Kaydet');
  // from the quality card the top also leads to the server row
  w.press('down'); assert.equal(w.row(), 'prefs'); assert.equal(w.at(), 'Türkçe', 'column memory of the preference row');
  w.press('right'); assert.equal(w.at(), '1080p'); w.press('up'); assert.equal(w.row(), 'url');

  // selected vs focused are distinct: focused (ring) on an unselected option, selected (dot) on another
  w.press('down'); assert.equal(w.at(), '1080p', 'column memory: last visited column'); w.goto('prefs', 1); assert.equal(w.at(), 'İngilizce');
  const cur = w.DZ.nav.currentEl();
  const focusedOpt = findAll(w.container, 'opt').filter(o => o.classList.contains('focused'));
  assert.equal(focusedOpt.length, 1); assert.equal(focusedOpt[0], cur);
  assert(!cur.classList.contains('on') && cur.getAttribute('aria-checked') === 'false', 'focused but not selected');
  const sel = groups[0].children[0];
  assert(sel.classList.contains('on') && !sel.classList.contains('focused') && sel.getAttribute('aria-checked') === 'true', 'selected but not focused');
  const css = read('css/settings.css');
  assert(/\.opt\.on\{[^}]*rgba\(245,197,24/.test(css) && /\.opt\.focused\{[^}]*border-color:var\(--accent\)/.test(css), 'selected = soft yellow tint, focused = accent ring');
  assert(/\.opt\.on \.opt-mark:after\{/.test(css), 'selected option shows the filled dot');

  // ---------------------------------------------------------------- Enter: options save, buttons use the existing handlers
  w.goto('prefs', 1); assert.equal(w.at(), 'İngilizce');
  w.press('enter');
  assert.equal(w.store.data['dz_pref_sub_p1'], 'en', 'Enter on an option = select and save (dz_pref_sub_<profile>)');
  assert.deepEqual(checked(groups[0]), ['İngilizce']); assert.equal(w.at(), 'İngilizce', 'focus stays on the option');
  assert(w.DZ.nav.currentEl().classList.contains('focused') && w.DZ.nav.currentEl().classList.contains('on'), 'focused and selected at once');
  w.press('right'); w.press('enter');
  assert.equal(w.store.data['dz_pref_quality'], '1440'); assert.deepEqual(checked(groups[1]), ['1440p']); assert.equal(w.DZ.api.qualityPref(), '1440');

  // server row buttons
  w.DZ.nav.focusRowById('url', 0); w.input().value = 'http://good:9000';
  w.goto('url', 1); assert.equal(w.at(), 'Bağlantıyı test et'); w.press('enter'); await tick();
  assert(/^Bağlantı tamam · \d+ ms · kaynak: library · içerik: 7$/.test(find(w.container, 'set-status').textContent));
  w.press('right'); assert.equal(w.at(), 'Kaydet'); w.input().value = 'http://other:1'; w.press('enter'); await tick();
  assert.equal(w.DZ.api.baseUrl(), 'http://other:1', 'Kaydet stores the address');

  // typing in the input: Enter closes it and moves focus to Kaydet
  w.DZ.nav.focusRowById('url', 0); assert.equal(w.document.activeElement, w.input());
  w.press('enter'); assert.equal(w.document.activeElement, null, 'input blurred'); assert.equal(w.at(), 'Kaydet');

  // cache button and profile switch
  w.goto('settings-cache', 0); assert.equal(w.at(), 'Önbelleği temizle'); w.press('enter');
  assert(/Önbellek temizlendi/.test(find(w.container, 'set-status').textContent)); assert.deepEqual(w.log.toasts, ['Önbellek temizlendi']);
  w.goto('settings-top', 0); w.press('enter');
  assert.equal(w.DZ.api.profileId(), null); assert.deepEqual(w.log.go[0], { n: 'profiles', p: null, r: true });

  // Back handling unchanged
  assert.equal(w.screen.back(), true); assert.equal(w.log.back, 1);
  find(w.container, 'back-btn').click(); assert.equal(w.log.goBack, 1, 'mouse "← Geri" = Back key');
  w.screen.exit();

  // ---------------------------------------------------------------- no profile: only the quality card is a list
  const np = world({ 'dz.baseUrl': 'http://a:8090' });
  np.open(); await tick();
  const npCards = find(np.container, 'set-cols').children;
  assert.equal(npCards.length, 2); assert.equal(findAll(np.container, 'set-radios').length, 1, 'quality list only');
  assert(/Önce bir profil seçin/.test(find(npCards[0], 'set-note').textContent)); assert(!find(npCards[0], 'set-radios'));
  np.press('down'); assert.equal(np.row(), 'prefs'); assert.equal(np.at(), '1080p');
  np.press('left'); assert.equal(np.at(), '1080p', 'single list: Left/Right stay'); np.press('right'); assert.equal(np.at(), '1080p');
  np.press('down'); np.press('down'); assert.equal(np.at(), 'Otomatik'); np.press('down'); assert.equal(np.row(), 'settings-cache');
  np.screen.exit();

  // ---------------------------------------------------------------- a language chosen in the player (none of the 3): no option marked, focus still works
  const de = world({ 'dz.profile': 'p1', 'dz_pref_sub_p1': 'de' });
  de.open(); await tick();
  assert.deepEqual(checked(findAll(de.container, 'set-radios')[0]), []); de.press('down'); assert.equal(de.at(), 'Türkçe');
  de.screen.exit();

  // ---------------------------------------------------------------- CSS / HTML contract
  assert(/\.set-btn\.primary\{[^}]*background:var\(--accent\)/.test(css), 'Kaydet: yellow fill');
  assert(/\.set-cols\{[^}]*display:flex/.test(css), 'cards side by side');
  assert(/\.set-card\{[^}]*border-radius:/.test(css) && /\.opt\{[^}]*border-radius:/.test(css), 'rounded surfaces');
  const code = css.replace(/\/\*[\s\S]*?\*\//g, '');
  assert(!/box-shadow|filter:|blur\(/.test(code), 'no box-shadow / blur / filter (Tizen GPU)');
  assert(!/(width|height):\s*(1920|1080)px/.test(code), 'no fixed stage-size boxes (responsive stage)');
  assert(!/\.set-(row|label|last)\b/.test(read('css/base.css')), 'old settings rules removed from base.css');
  const html = read('index.html');
  assert(/css\/settings\.css\?v=responsive-v35/.test(html), 'settings.css linked');
  assert(!/responsive-v31/.test(html) && (html.match(/responsive-v35/g) || []).length === (html.match(/\?v=/g) || []).length, 'one cache-bust version everywhere');

  console.log('Settings layout: embedded test button, Kaydet after the input, two radio cards, no bottom Geri, top-right Profil değiştir, D-pad focus map: OK');
})().catch(e => { console.error(e); process.exitCode = 1; });
