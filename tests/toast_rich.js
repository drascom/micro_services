// Rich notice (js/ui/toast.js DZ.toast.showRich): one card in the stage (#app) with label / big title / optional message, kind class
// (success|warn), two corner ornament elements (top-left + rotated bottom-right, CSS only), default 7 s / custom ms then hidden,
// queue (one card at a time, at most 3 waiting, the rest dropped), never touches focus / nav / key handlers. The small
// DZ.toast.show() behaves as before (same element, 3.5 s, `hidden` class); offline note unchanged. CSS/asset/build wiring is checked statically.
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, find, findAll, timers } = require('./_fake_dom');

const client = path.join(__dirname, '../tizen-client');
const read = f => fs.readFileSync(path.join(client, f), 'utf8');

function load() {
  const t = timers();
  const app = el('div'); app.id = 'app';
  const body = el('body');
  const focusCalls = [];
  const factory = tag => {
    const n = el(tag);
    n.focus = () => { focusCalls.push(tag); };
    return n;
  };
  const document = {
    createElement: factory, body,
    getElementById: id => (id === 'app' ? app : null),
    activeElement: null
  };
  const listeners = [];
  const window = {
    DZ: { nav: new Proxy({}, { get() { throw new Error('toast must not touch nav'); } }) },
    navigator: { onLine: true },
    addEventListener: (type) => { listeners.push(type); }
  };
  vm.runInNewContext(read('js/ui/toast.js'), { window, document, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout, console: { log() {} } });
  return { t, app, body, document, window, T: window.DZ.toast, focusCalls, listeners };
}

const card = app => find(app, 'toast-rich');
const off = c => c.classList.contains('tr-off');
const text = (c, cls) => find(c, cls).textContent;

// ---- structure + success card
{
  const { t, app, T, focusCalls } = load();
  assert.equal(typeof T.showRich, 'function'); assert.equal(typeof T.hideRich, 'function');
  assert.equal(card(app), null, 'no card before the first call');
  assert.equal(T.showRich({ title: 'Kurtlar Vadisi', message: '', kind: 'success' }), true);
  const c = card(app);
  assert(c && c.parentNode === app, 'card lives in #app (stage), above the screens');
  assert(!off(c), 'visible');
  assert(c.classList.contains('tr-success') && !c.classList.contains('tr-warn'), 'kind class');
  assert.equal(text(c, 'tr-label'), 'İzlemeye hazır', 'yellow small label');
  assert.equal(text(c, 'tr-title'), 'Kurtlar Vadisi', 'big title = plain name');
  assert(find(c, 'tr-msg').classList.contains('hidden'), 'empty message is hidden (no repeat of the label)');
  assert.equal(findAll(c, 'tr-orn').length, 2, 'two corner ornaments');
  assert(find(c, 'tr-orn-tl') && find(c, 'tr-orn-br'), 'top-left + bottom-right (CSS rotates the second; no second file)');
  assert.equal(c.getAttribute('role'), 'status');
  assert.equal(c.getAttribute('tabindex'), null, 'not focusable');
  assert.equal(Object.keys(c.listeners).length, 0, 'no key/pointer handlers on the card');
  assert.equal(focusCalls.length, 0, 'never focuses anything');
  // default duration 7 s
  t.advance(6999); assert(!off(c), 'still visible just before 7 s');
  t.advance(1); assert(off(c), 'hidden after 7 s');
  assert.equal(t.pending(), 1, 'only the exit gap timer is left');
  t.advance(400); assert.equal(t.pending(), 0, 'nothing pending afterwards');
  assert.equal(T.RICH_MS, 7000);
}

// ---- warn card with message + custom ms; same element reused; text replaced
{
  const { t, app, T } = load();
  T.showRich({ title: 'Dizi', message: 'Bölümler şu an alınamadı, daha sonra tekrar deneyin', kind: 'warn', ms: 2000 });
  const c = card(app);
  assert(c.classList.contains('tr-warn') && !c.classList.contains('tr-success'));
  assert.equal(text(c, 'tr-label'), 'Bilgi');
  assert.equal(text(c, 'tr-title'), 'Dizi');
  assert.equal(text(c, 'tr-msg'), 'Bölümler şu an alınamadı, daha sonra tekrar deneyin');
  assert(!find(c, 'tr-msg').classList.contains('hidden'));
  t.advance(1999); assert(!off(c)); t.advance(1); assert(off(c), 'custom ms');
  t.advance(400);
  // second call later: same card element, kind class swapped, message hidden again
  T.showRich({ title: 'Film', kind: 'success' });
  assert.equal(findAll(app, 'toast-rich').length, 1, 'one card element only');
  assert(card(app) === c);
  assert(c.classList.contains('tr-success') && !c.classList.contains('tr-warn'));
  assert(find(c, 'tr-msg').classList.contains('hidden') && text(c, 'tr-msg') === '');
  // unknown kind -> success; missing opts -> empty card without throwing
  T.hideRich();
  T.showRich({ title: 'X', kind: 'nope' });
  assert(c.classList.contains('tr-success'));
  T.hideRich();
  assert.equal(T.showRich(), true);
  T.hideRich();
}

// ---- queue: one at a time, each for its own ms, at most 3 waiting (4th waiting one is dropped)
{
  const { t, app, T } = load();
  const r = [1, 2, 3, 4, 5].map(i => T.showRich({ title: 'T' + i, message: 'm' + i, kind: 'success', ms: 1000 }));
  assert.deepEqual(r, [true, true, true, true, false], '1 shown + 3 waiting; the 5th is dropped');
  assert.equal(T.RICH_MAX_WAIT, 3);
  const c = card(app);
  assert.equal(text(c, 'tr-title'), 'T1');
  t.advance(1000); assert(off(c), 'T1 done after its ms');
  assert.equal(text(c, 'tr-title'), 'T1', 'next one waits for the exit animation');
  t.advance(400); assert(!off(c)); assert.equal(text(c, 'tr-title'), 'T2'); assert.equal(text(c, 'tr-msg'), 'm2');
  t.advance(1000 + 400); assert.equal(text(c, 'tr-title'), 'T3'); assert(!off(c));
  // a new one arriving mid-show queues behind (room again: 1 waiting)
  assert.equal(T.showRich({ title: 'T6', ms: 1000 }), true);
  t.advance(1000 + 400); assert.equal(text(c, 'tr-title'), 'T4');
  t.advance(1000 + 400); assert.equal(text(c, 'tr-title'), 'T6');
  t.advance(1000); assert(off(c));
  // arriving during the exit gap queues as well (no overlap), then plays
  assert.equal(T.showRich({ title: 'T7', ms: 500 }), true);
  assert.equal(text(c, 'tr-title'), 'T6');
  t.advance(400); assert.equal(text(c, 'tr-title'), 'T7'); assert(!off(c));
  t.advance(500 + 400); assert(off(c)); assert.equal(t.pending(), 0, 'queue drained, no timers left');
  // idle again: next call shows immediately
  T.showRich({ title: 'T8' }); assert(!off(c)); assert.equal(text(c, 'tr-title'), 'T8');
}

// ---- hideRich: closes the card, drops the queue, cancels timers
{
  const { t, app, T } = load();
  T.showRich({ title: 'A' }); T.showRich({ title: 'B' });
  T.hideRich();
  assert(off(card(app))); assert.equal(t.pending(), 0);
  t.advance(20000); assert(off(card(app)), 'queue dropped');
  T.showRich({ title: 'C' }); assert(!off(card(app))); assert.equal(text(card(app), 'tr-title'), 'C');
}

// ---- no host (no #app / body): false, no throw
{
  const t = timers();
  const window = { DZ: {}, navigator: { onLine: true } };
  vm.runInNewContext(read('js/ui/toast.js'), { window, document: { createElement: el, getElementById: () => null, body: null }, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout, console: { log() {} } });
  assert.equal(window.DZ.toast.showRich({ title: 'X' }), false);
}

// ---- the small toast is unchanged: separate element, `hidden` class, 3.5 s; offline note untouched; rich and small coexist
{
  const { t, app, T, window, listeners } = load();
  assert.equal(T.show('Merhaba'), true);
  const small = find(app, 'toast');
  assert(small && small !== card(app), 'small toast element');
  assert.equal(small.textContent, 'Merhaba');
  assert(!small.classList.contains('hidden'));
  assert.equal(T.MS, 3500);
  T.showRich({ title: 'Büyük', ms: 7000 });
  t.advance(3499); assert(!small.classList.contains('hidden'));
  t.advance(1); assert(small.classList.contains('hidden'), 'small toast hides after 3.5 s');
  assert(!off(card(app)), 'the rich card is unaffected by the small one');
  T.show('İki'); T.hide(); assert(small.classList.contains('hidden'));
  assert(!off(card(app)), 'hide() touches the small toast only');
  // offline note
  window.navigator.onLine = false;
  T.watchOnline();
  const note = find(app, 'offline-note');
  assert(note && !note.classList.contains('hidden'), 'offline note shown');
  assert.equal(note.textContent, 'Çevrimdışı · İnternet bağlantısı yok');
  assert.deepEqual(listeners, ['offline', 'online']);
  assert.equal(T.isOffline(), true);
  assert.equal(T.errorText(new Error('x')), 'Bağlantı yok. İnternet bağlantınızı kontrol edin.');
}

// ---- never touches focus / nav / keys: only the listeners the old component already had (online/offline), no focus() calls
{
  const { t, T, focusCalls, listeners, document } = load();
  T.showRich({ title: 'A', ms: 100 }); T.showRich({ title: 'B', ms: 100 });
  t.advance(2000);
  assert.equal(focusCalls.length, 0); assert.deepEqual(listeners, []); assert.equal(document.activeElement, null);
}

// ---- static wiring: CSS (above overlay, no pointer events, ornament path, no heavy effects), asset, build, ES2017
{
  const css = read('css/base.css');
  const rule = sel => { const m = new RegExp('(?:^|\\n)' + sel.replace(/[.]/g, '\\.') + '\\{([^}]*)\\}').exec(css); assert(m, 'css rule ' + sel); return m[1]; };
  const rich = rule('.toast-rich');
  assert(/pointer-events:none/.test(rich), 'pointer-events none');
  assert(/border:2px solid var\(--accent\)/.test(rich), 'thin yellow border');
  const z = Number(/z-index:(\d+)/.exec(rich)[1]);
  assert(z > 10000, 'above #overlay (modal) z-index 10000; got ' + z);
  assert(/width:780px/.test(rich) && /top:calc\(var\(--stage-h\) \* \.14\)/.test(rich), 'wide card, ~14% from the top, stage-relative');
  assert(/border-radius/.test(rich));
  assert(/pointer-events:none/.test(rule('.tr-orn')) && /ornament-corner\.png/.test(rule('.tr-orn')), 'ornament css image');
  assert(/rotate\(180deg\)/.test(rule('.tr-orn-br')), 'bottom-right is the same image rotated 180deg');
  assert(/-webkit-line-clamp:2/.test(rule('.tr-title')), 'title: 2 lines max, ellipsis');
  assert(/\.toast-rich\.tr-off\{/.test(css) && /\.toast-rich\.tr-warn\{border-color:var\(--accent-soft\)/.test(css), 'hidden + warn styles');
  const block = css.slice(css.indexOf('/* zengin bildirim'), css.indexOf('.offline-note{'));
  assert(!/box-shadow|blur\(|filter:/.test(block.replace(/\/\*[\s\S]*?\*\//g, '')), 'no heavy effects (Tizen)');
  assert(!/display:none/.test(rich), 'the rich card is not display:none-hidden (would kill animation)');
  // asset: present, PNG, RGBA, small, yellow-only
  const png = fs.readFileSync(path.join(client, 'img/ornament-corner.png'));
  assert.equal(png.slice(1, 4).toString(), 'PNG');
  assert.equal(png.readUInt32BE(16), 480, 'width 480'); assert.equal(png.readUInt8(25), 6, 'RGBA');
  assert(png.length < 60 * 1024, 'under 60 KB; got ' + png.length);
  // build script ships the whole tree (img/ included) and does not exclude it
  const build = read('build-wgt.sh');
  assert(/zip -r -X "\$OUT" \./.test(build) && !/-x 'img/.test(build) && !/-x '\*\.png/.test(build), 'build-wgt.sh packages img/');
  // ES2017 only
  const src = read('js/ui/toast.js').replace(/\/\*[\s\S]*?\*\//g, '');
  assert(!/\?\.|\?\?/.test(src), 'no ?. / ??');
}

console.log('Rich toast: card structure, kind classes, ornaments, 7 s / custom ms, queue (max 3 waiting), hideRich, no focus/nav, old show() + offline note unchanged, css/asset/build wiring: OK');
