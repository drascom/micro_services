// Responsive stage: pure fit(innerWidth, innerHeight), CSS variables + #app scale, debounced resize -> screen relayout
// (detail list viewH), row visible-card count, and the CSS contract (no fixed 1920x1080 left except the TV AVPlay layer).
// Run from the repo root: node tests/stage_fit.js
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, findAll, walk, timers } = require('./_fake_dom');

const CLIENT = path.join(__dirname, '../tizen-client');
const read = f => fs.readFileSync(path.join(CLIENT, f), 'utf8');

function win(iw, ih, extra) {
  const T = timers();
  const rootStyle = { props: {}, setProperty(k, v) { this.props[k] = v; } };
  const app = el('div');
  const handlers = {};
  const window = Object.assign({
    innerWidth: iw, innerHeight: ih, DZ: {}, console: { log() {} },
    setTimeout: T.setTimeout, clearTimeout: T.clearTimeout,
    addEventListener(type, fn) { handlers[type] = fn; },
    requestAnimationFrame: f => f(), Image: function () { return el('img'); },
    document: { readyState: 'loading', documentElement: { style: rootStyle }, getElementById: id => (id === 'app' ? app : null) }
  }, extra || {});
  return { window, T, rootStyle, app, handlers, DZ: window.DZ };
}
function load(w, file, globals) {
  vm.runInNewContext(read(file), Object.assign({ window: w.window }, globals || {}));
}

// ---------------------------------------------------------------- pure fit()
{
  const w = win(1920, 1080);
  load(w, 'js/stage.js');
  const fit = w.DZ.stage.fit;

  const tv = fit(1920, 1080);
  assert.deepEqual([tv.scale, tv.w, tv.h], [1, 1920, 1080], 'TV 1920x1080: scale 1, stage 1920x1080 (unchanged)');
  const l = fit(1440, 900);
  assert.deepEqual([l.scale, l.w, l.h], [0.75, 1920, 1200], '16:10 -> width-limited, stage 1920x1200');
  const u = fit(2560, 1080);
  assert.deepEqual([u.scale, u.w, u.h], [1, 2560, 1080], 'ultrawide 21:9 -> scale 1, stage 2560x1080');
  const q = fit(1280, 1024);
  assert.equal(q.w, 1920); assert.equal(q.h, 1536, '5:4 -> stage 1920x1536');
  assert(Math.abs(q.scale - 1280 / 1920) < 1e-12);
  const hd = fit(1280, 720);
  assert.deepEqual([hd.scale, hd.w, hd.h].map(v => +v.toFixed(6)), [+(2 / 3).toFixed(6), 1920, 1080], '16:9 at another size: stage stays 1920x1080');
  const big = fit(3840, 2160);
  assert.deepEqual([big.scale, big.w, big.h], [2, 1920, 1080], '4K 16:9: scale 2, stage 1920x1080');

  // small window: reasonable lower bound
  const s = fit(800, 400);
  assert(s.w >= 1920 && s.h >= 1080, 'small window still gets a >= 1920x1080 stage');
  assert.equal(s.h, 1080); assert.equal(s.w, 2160, '800x400 (2:1) -> stage 2160x1080');
  assert(Math.abs(s.scale - 400 / 1080) < 1e-12);
  const tiny = fit(100, 50);
  assert(tiny.scale >= 320 / 1920 - 1e-12 && tiny.scale > 0, 'tiny window is clamped (scale floor), got ' + tiny.scale);
  assert(tiny.w >= 1920 && tiny.h >= 1080);
  for (const bad of [[0, 0], [NaN, NaN], [undefined, undefined], [-5, 100]]) {
    const r = fit(bad[0], bad[1]);
    assert(r.scale > 0 && r.w >= 1920 && r.h >= 1080, 'degenerate window ' + bad + ' still yields a valid stage');
  }
  // portrait / very tall and very wide never cut content: stage >= design size, both axes scaled exactly to the window
  const portrait = fit(1080, 1920);
  assert.equal(portrait.w, 1920); assert.equal(portrait.h, 3414, '1080x1920 portrait monitor -> 1920x3414 (rounded up: covers the window)');
  assert.equal(fit(3440, 1440).h, 1080);

  // invariant sweep: the stage always covers the window (no bands), overshoot < 1 device px, and fills one axis exactly
  let n = 0;
  for (let iw = 320; iw <= 5120; iw += 97) {
    for (let ih = 180; ih <= 2880; ih += 83) {
      const r = fit(iw, ih);
      n++;
      assert(r.w >= 1920 && r.h >= 1080, `stage >= 1920x1080 for ${iw}x${ih}`);
      assert(r.w * r.scale >= iw - 1e-6 && r.h * r.scale >= ih - 1e-6, `stage covers the window ${iw}x${ih}`);
      assert(r.w * r.scale - iw < r.scale + 1e-6 && r.h * r.scale - ih < r.scale + 1e-6, `overshoot < 1 stage unit for ${iw}x${ih}`);
      assert(Math.abs(r.w - 1920) < 1e-9 || Math.abs(r.h - 1080) < 1e-9, `one axis is exactly the design size for ${iw}x${ih}`);
    }
  }
  assert(n > 1000);
  // every exact 16:9 window keeps the TV stage
  for (const [a, b] of [[1280, 720], [1600, 900], [1920, 1080], [2560, 1440], [3840, 2160], [960, 540]]) {
    const r = fit(a, b);
    assert.equal(r.w, 1920); assert.equal(r.h, 1080);
  }
}

// ---------------------------------------------------------------- apply(): CSS variables + #app transform
{
  const w = win(1920, 1080);
  load(w, 'js/stage.js');
  const st = w.DZ.stage;
  st.init();
  assert.equal(w.rootStyle.props['--stage-w'], '1920px');
  assert.equal(w.rootStyle.props['--stage-h'], '1080px');
  assert.equal(w.app.style.transform, 'translate3d(0px,0px,0) scale(1)', 'TV: identical transform to the old fixed-stage fit (x=y=0, scale 1)');
  assert(w.handlers.resize && w.handlers.orientationchange, 'resize + orientationchange listeners installed');

  // desktop window 1440x900: stage grows, no letterbox offset
  w.window.innerWidth = 1440; w.window.innerHeight = 900;
  w.handlers.resize();
  assert.equal(w.rootStyle.props['--stage-h'], '1200px', 'stage variables update immediately on resize');
  assert.equal(w.rootStyle.props['--stage-w'], '1920px');
  assert.equal(w.app.style.transform, 'translate3d(0px,0px,0) scale(0.75)', 'no x/y letterbox offset, scale from the window');
  assert.equal(st.h, 1200); assert.equal(st.scale, 0.75);
  w.window.innerWidth = 2560; w.window.innerHeight = 1080;
  w.handlers.orientationchange();
  assert.equal(w.rootStyle.props['--stage-w'], '2560px'); assert.equal(st.w, 2560);
}

// ---------------------------------------------------------------- debounce + listeners
{
  const w = win(1920, 1080);
  load(w, 'js/stage.js');
  const st = w.DZ.stage;
  st.init();
  const seen = [];
  st.onChange(s => seen.push([s.w, s.h]));
  st.onChange(s => { throw new Error('a failing listener must not break the others'); });
  st.onChange(s => seen.push('after-throw'));
  const resize = (iw, ih) => { w.window.innerWidth = iw; w.window.innerHeight = ih; w.handlers.resize(); };
  resize(1800, 1000); w.T.advance(50); resize(1600, 1000); w.T.advance(50); resize(1440, 900);
  assert.deepEqual(seen, [], 'listeners wait for the resize burst to settle (debounce)');
  w.T.advance(119);
  assert.deepEqual(seen, [], 'still waiting at 119 ms');
  w.T.advance(1);
  assert.deepEqual(seen, [[1920, 1200], 'after-throw'], 'one notification with the final size');
  resize(720, 450);   // same 16:10 stage size? 720x450 -> 1920x1200 again
  w.T.advance(200);
  assert.equal(seen.length, 2, 'scale-only change (same stage size) does not notify');
  resize(1280, 720);
  w.T.advance(200);
  assert.deepEqual(seen.slice(2), [[1920, 1080], 'after-throw'], 'back to 16:9 notifies with the TV stage');
  assert.equal(w.T.pending(), 0);
  st.flush();
  assert.equal(seen.length, 4, 'flush with nothing changed does not re-notify');
}

// ---------------------------------------------------------------- app.js: resize -> screen.onResize + nav.relayout (real stage.js + app.js)
{
  const w = win(1920, 1080);
  const screenEl = el('div');
  const getEl = id => (id === 'app' ? w.app : id === 'screen' ? screenEl : el('div'));
  w.window.document.getElementById = getEl;
  w.window.document.createElement = () => el('div');
  w.window.document.addEventListener = () => {};
  w.window.document.readyState = 'complete';
  const DZ = w.DZ;
  const log = { resize: [], relayout: 0, enter: 0 };
  DZ.screens = { home: { enter() { log.enter++; }, exit() {}, onResize(s) { log.resize.push([s.w, s.h]); } } };
  DZ.nav = { snapshot() { return null; }, reset() {}, restoreNext() {}, relayout() { log.relayout++; } };
  DZ.keys = { init() {}, onKey() {} };
  DZ.api = { profileId() { return 'p1'; }, baseUrl() { return 'http://server'; }, debug: {} };
  DZ.modal = { confirm() {}, isOpen() { return false; } };
  DZ.toast = {};
  load(w, 'js/stage.js');
  vm.runInNewContext(read('js/app.js'), { window: w.window, document: w.window.document, console: w.window.console, setTimeout: w.T.setTimeout, tizen: undefined });
  w.T.advance(1);   // app.js defers start()
  assert.equal(log.enter, 1, 'home mounted');
  assert(w.handlers.resize, 'app.js installs the stage resize listener');
  w.window.innerWidth = 1280; w.window.innerHeight = 1024;
  w.handlers.resize();
  assert.equal(w.rootStyle.props['--stage-h'], '1536px');
  assert.deepEqual(log.resize, [], 'screen relayout is debounced');
  w.T.advance(120);
  assert.deepEqual(log.resize, [[1920, 1536]], 'active screen gets onResize(stage) after the debounce');
  assert.equal(log.relayout, 1, 'nav re-anchors the focused row');
  assert.equal(typeof w.DZ.app.fit, 'function', 'DZ.app.fit kept (delegates to the stage)');
}

// ---------------------------------------------------------------- detail: resize recomputes viewH / re-renders the window
(async () => {
  const P = 'series';
  const ep = (s, n) => ({ id: `${P}:s${s}:e${n}`, season: s, episode: n, title: `Bölüm ${n}`, overview: '', runtime: 42, still_url: '', has_still: false,
    air_date: '2020-05-01', availability: { state: 'ready' }, progress: null });
  const season = (n, count) => ({ season: n, title: `${n}. Sezon`, episode_count: count, has_poster: false, episodes: Array.from({ length: count }, (_, i) => ep(n, i + 1)) });
  const data = { id: P, type: 'series', title: 'Dizi', overview: '', genres: [], availability: { state: 'ready' }, backdrop: '',
    resume: { episode_id: `${P}:s1:e1`, position: 0 }, similar: [], seasons: [season(1, 400), season(2, 3)] };

  const w = win(1920, 1080);
  const DZ = w.DZ;
  let rootEl = null, rows = [], cur = null, focusCb = null, refreshes = 0;
  const firstNav = n => { for (const c of n.children) { if (c.getAttribute && c.getAttribute('data-nav')) return c; const r = firstNav(c); if (r) return r; } return null; };
  const collect = () => { rows = []; walk(rootEl, n => { const id = n.getAttribute && n.getAttribute('data-nav-row'); if (!id) return; const item = firstNav(n); if (item) rows.push({ id, item }); }); };
  const apply = id => { cur = { rowId: id, col: 0 }; const r = rows.find(x => x.id === id); if (focusCb) focusCb({ el: r.item, rowId: id, col: 0 }); };
  DZ.nav = {
    setRoot(r) { rootEl = r; collect(); cur = null; if (rows.length) apply(rows[0].id); },
    onFocus(fn) { focusCb = fn; },
    refresh() { refreshes++; collect(); if (cur && rows.find(r => r.id === cur.rowId)) apply(cur.rowId); },
    focusRowById(id) { if (!rows.find(r => r.id === id)) return false; apply(id); return true; },
    focusIndex() {}, restoreNext() {}, current() { return cur; }, currentEl() { return cur ? rows.find(r => r.id === cur.rowId).item : null; },
    rowIds() { return rows.map(r => r.id); }
  };
  DZ.api = { profileId: () => 'p1', detail: () => Promise.resolve(data), removeMyList() {}, addMyList() {} };
  DZ.card = { sized: x => x || '', sizedTo: (p, ww, hh) => p };
  DZ.skeleton = { detail: () => el('div') };
  DZ.row = { create() { return el('section'); } };
  DZ.modal = { text() {}, open() {} };
  DZ.playflow = { start() {} };
  DZ.app = { go() {}, back() {} };
  load(w, 'js/stage.js');
  vm.runInNewContext(read('js/screens/detail.js'), { window: w.window, document: { createElement: el }, console: w.window.console, setTimeout: w.T.setTimeout, clearTimeout: w.T.clearTimeout, Image: w.window.Image, Date });
  const screen = DZ.screens.detail;
  const h = screen.helpers;

  // pure helpers: TV value unchanged (900), grows with the stage, never below 3 rows
  assert.equal(h.viewMax(1080), 900, 'TV: list view max stays 900');
  assert.equal(h.viewMax(1200), 1020); assert.equal(h.viewMax(1536), 1356);
  assert.equal(h.viewMax(720), 540, '720-unit stage: list view shrinks to 540');
  assert.equal(h.viewMax(300), 3 * h.ROW, 'never below 3 episode rows');
  assert.equal(h.computeViewH(100000, 1080), 900); assert.equal(h.computeViewH(50, 1080), h.ROW, 'at least one row');
  assert.equal(h.computeViewH(700, 1536), 700, 'short lists are not stretched');

  const container = el('div');
  DZ.stage.init();
  assert.equal(DZ.stage.h, 1080);
  screen.enter(container, { id: P }, false);
  await Promise.resolve(); await Promise.resolve();
  assert.equal(h.viewH(), 900, 'TV: viewH = 900 (as before)');
  const views = findAll(container, 'br-view');
  assert.equal(views.length, 2);
  assert(views.every(v => v.style.height === '900px'));
  const rendered0 = findAll(container, 'ep-row').length;
  const total = 400;

  // move focus into the episode list, then grow the window: 1440x900 -> stage 1920x1200
  assert(DZ.nav.focusRowById('ep_5'), 'an episode row inside the window is focusable');
  w.window.innerWidth = 1280; w.window.innerHeight = 1024;   // 5:4 -> stage 1920x1536
  w.handlers.resize();
  w.T.advance(120);
  // app.js is not loaded here: drive the same hook the router calls
  assert.equal(DZ.stage.h, 1536);
  assert.equal(screen.onResize(DZ.stage), true, 'onResize reports a viewH change');
  assert.equal(h.viewH(), 1356, 'viewH grows with the stage height (1536 - 180)');
  assert(views.every(v => v.style.height === '1356px'), 'both list viewports resized');
  const rendered1 = findAll(container, 'ep-row').length;
  assert(rendered1 > rendered0, 'window re-rendered for the taller view (' + rendered0 + ' -> ' + rendered1 + ')');
  assert(rendered1 < 30, 'window stays bounded, got ' + rendered1);
  assert(refreshes > 0, 'nav refreshed after the window rows changed');
  assert.equal(screen.onResize(DZ.stage), false, 'no change -> no work');

  // shrink to a 720-unit stage (only possible for a hypothetical tiny stage): list view follows
  DZ.stage.h = 720; DZ.stage.w = 1920;
  assert.equal(screen.onResize(DZ.stage), true);
  assert.equal(h.viewH(), 540);
  assert(views.every(v => v.style.height === '540px'));
  const vis = findAll(container, 'ep-row').map(r => +r.getAttribute('data-nav-row').slice(3));
  assert(vis.includes(5), 'focused episode stays inside the rendered window');
  assert(vis.length <= Math.ceil(540 / 172) + 8, 'window shrinks with the view, got ' + vis.length);

  // back to TV size restores 900
  DZ.stage.h = 1080;
  assert.equal(screen.onResize(DZ.stage), true);
  assert.equal(h.viewH(), 900);
  assert.equal(total, 400);
  screen.exit();
  assert.equal(screen.onResize(DZ.stage), false, 'no state after exit');
})().then(() => {
  // ---------------------------------------------------------------- row.js visible-card count, home.js row span
  {
    const w = win(1920, 1080);
    load(w, 'js/ui/row.js', { document: { createElement: el } });
    const row = w.DZ.row;
    assert.equal(row.visible(), 8, 'no stage module: 8 (as before)');
    w.DZ.stage = { w: 1920, h: 1080 };
    assert.equal(row.visible(), 8, 'TV: 8 visible posters');
    w.DZ.stage = { w: 1600, h: 1200 };
    assert.equal(row.visible(), 8, 'never fewer than 8');
    w.DZ.stage = { w: 2560, h: 1080 };
    assert.equal(row.visible(), 11, 'ultrawide: proportionally more cards get artwork');
    w.DZ.stage = { w: 3840, h: 1080 };
    assert.equal(row.visible(), 16);

    const w2 = win(1920, 1080);
    load(w2, 'js/screens/home.js', { document: { createElement: el } });
    const span = w2.DZ.screens.home.rowSpan;
    assert.equal(span(), 2, 'no stage: focus row + 2 below (unchanged)');
    w2.DZ.stage = { w: 1920, h: 1080 };
    assert.equal(span(), 2, 'TV: unchanged');
    w2.DZ.stage = { w: 1920, h: 1200 };
    assert.equal(span(), 3, 'taller stage loads one more row');
    w2.DZ.stage = { w: 1920, h: 1536 };
    assert.equal(span(), 3);
    w2.DZ.stage = { w: 1920, h: 3413 };
    assert(span() >= 6, 'portrait monitor loads more rows, got ' + span());
  }

  // ---------------------------------------------------------------- nav.relayout keeps focus state
  {
    const w = win(1920, 1080);
    load(w, 'js/nav.js');
    assert.equal(typeof w.DZ.nav.relayout, 'function');
    w.DZ.nav.relayout();   // no rows: no throw
  }

  // ---------------------------------------------------------------- CSS / HTML contract
  {
    const css = ['base', 'home', 'detail', 'player', 'profiles', 'profile_edit', 'settings'].map(f => read('css/' + f + '.css')).join('\n');
    const base = read('css/base.css');
    assert(/--stage-w:1920px/.test(base) && /--stage-h:1080px/.test(base), 'TV defaults for the stage variables');
    assert(/#app\{[^}]*width:var\(--stage-w\);height:var\(--stage-h\)/.test(base), '#app sized by the stage variables');
    assert(/#screen\{[^}]*width:var\(--stage-w\);height:var\(--stage-h\)/.test(base));
    assert(/#overlay\{[^}]*width:var\(--stage-w\);height:var\(--stage-h\)/.test(base));
    assert(/html,body\{[^}]*background:var\(--bg\)/.test(base), 'html/body use the app background, not black');
    assert(!/html,body\{[^}]*background:#000/.test(base));
    assert(/\.toast\{[^}]*left:calc\(\(var\(--stage-w\) - 800px\) \/ 2\)/.test(base), 'toast stays centred (560px at 1920)');
    // no fixed 1920/1080 layout box survives except the TV AVPlay hardware layer
    const fixed = css.split('\n').filter(l => /(width|height):\s*(1920|1080)px/.test(l));
    assert.equal(fixed.length, 1, 'only #avplayer keeps a fixed size, got: ' + fixed.join(' | '));
    assert(/#avplayer\{/.test(fixed[0]));
    assert(/\.pl-bar\{[^}]*width:100%/.test(read('css/player.css')), 'progress bar spans the stage width minus the safe margins');
    assert(!/3\.5vh|2vh/.test(read('css/detail.css')), 'no viewport-relative spacing (vh is the window, not the stage)');
    const hero = read('css/home.css');
    assert(/614\.4px/.test(hero) && /1190\.4px/.test(hero), 'hero shade stops are px (same as 32%/62% of 1920)');
    const html = read('index.html');
    const order = ['js/stage.js', 'js/nav.js', 'js/ui/row.js', 'js/screens/detail.js', 'js/app.js'].map(s => html.indexOf(s));
    assert(order.every(i => i > 0) && order.every((v, i) => !i || v > order[i - 1]), 'stage.js is loaded before nav/row/detail/app');
    assert(/<meta name="viewport"/.test(html));
  }

  console.log('Responsive stage: fit(), CSS variables, debounced resize, detail viewH, row/home windows, CSS contract: OK');
}).catch(e => { console.error(e); process.exitCode = 1; });
