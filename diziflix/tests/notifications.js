// Notification poller (real js/notifications.js, fake API/store/toast/timers): polls every 30 s only on home/detail/catalog screens, PAUSES on the
// player (one query right after leaving it), keeps last_id per profile in localStorage `dz_notif_since_<profile>`, never toasts the backlog on the
// first run (only advances last_id), toasts new `source_found` ("Kaynak bulundu: <title> S04 B02") and marks them read afterwards, a full toast queue
// leaves the rest for the next poll, errors/offline are silent. Plus the app.js hook and the api.js request shapes.
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { timers } = require('./_fake_dom');

const root = path.join(__dirname, '../tizen-client/js');
const read = f => fs.readFileSync(path.join(root, f), 'utf8');
const tick = async (n = 10) => { for (let i = 0; i < n; i++) await Promise.resolve(); };

function load() {
  const window = { DZ: {} };
  const sandbox = { window, console: { log() {} }, Promise, setInterval() { return 0; }, clearInterval() {} };
  vm.createContext(sandbox);
  vm.runInContext(read('notifications.js'), sandbox);
  return window.DZ.notifications;
}
const N = load();

function world(o) {
  o = o || {};
  const T = timers();
  const mem = {};
  const log = { gets: [], reads: [], toasts: [] };
  let profile = o.profile === undefined ? 'p1' : o.profile;
  let queue = [];            // server responses, shifted per notifications() call (last one repeats)
  let offline = false;
  let richOk = () => true;
  const api = {
    notifications(pid, since) {
      log.gets.push({ pid, since });
      const r = queue.length > 1 ? queue.shift() : queue[0];
      return typeof r === 'function' ? r() : Promise.resolve(r || { items: [], last_id: since || 0 });
    },
    markNotificationsRead(pid, upto) { log.reads.push({ pid, upto }); return Promise.resolve({ ok: true, marked: 1 }); }
  };
  const store = { get: (k, d) => (k in mem ? mem[k] : d), set: (k, v) => { mem[k] = String(v); }, del: k => { delete mem[k]; } };
  const toast = { showRich(x) { if (!richOk()) return false; log.toasts.push(x); return true; }, isOffline: () => offline };
  const n = N.create({ api, store, toast, timers: T, profileId: () => profile, interval: 30000 });
  return { T, mem, log, n, setProfile: p => { profile = p; }, respond: (...r) => { queue = r; }, setOffline: v => { offline = v; }, setRich: f => { richOk = f; } };
}
const item = (id, extra) => Object.assign({ id, kind: 'source_found', canonical_id: 'd1', episode_id: 'd1:s4:e2', title: 'Kayıp Sinyal', season: 4, episode: 2, site: 'yabancidizi', method: 'retry', created_at: 1 }, extra || {});

(async () => {
  // ---- A) first run: no `since` stored -> NO toast for the backlog, last_id advanced (request has no since param); later polls use it
  {
    const w = world();
    w.respond({ items: [item(3), item(4)], last_id: 4 });
    w.n.onScreen('home');
    await tick();
    assert.equal(w.log.gets.length, 1); assert.strictEqual(w.log.gets[0].since, null, 'first run: no since');
    assert.equal(w.log.toasts.length, 0, 'old notifications are not toasted on the first run');
    assert.equal(w.mem['dz_notif_since_p1'], '4', 'last_id advanced and persisted per profile');
    assert.equal(w.log.reads.length, 0, 'the backlog is not marked read either');
    w.respond({ items: [item(5, { episode: 3 })], last_id: 5 });
    w.T.advance(30000); await tick();
    assert.equal(w.log.gets[1].since, 4, 'next poll sends since=last_id');
    assert.equal(w.log.toasts.length, 1);
    assert.deepEqual([w.log.toasts[0].label, w.log.toasts[0].title, w.log.toasts[0].kind], ['Kaynak bulundu', 'Kayıp Sinyal S04 B03', 'success']);
    assert.deepEqual(w.log.reads, [{ pid: 'p1', upto: 5 }], 'marked read after it was shown');
    assert.equal(w.mem['dz_notif_since_p1'], '5');
    // an empty answer keeps the cursor; first run with an empty backlog still counts as "done" (cursor 0 stored)
    const w2 = world();
    w2.respond({ items: [], last_id: 0 });
    w2.n.onScreen('catalog'); await tick();
    assert.equal(w2.mem['dz_notif_since_p1'], '0', 'since "0" = first run is over');
    w2.respond({ items: [item(1)], last_id: 1 });
    w2.T.advance(30000); await tick();
    assert.equal(w2.log.toasts.length, 1, 'a notification after a (stored) empty first run IS toasted');
  }

  // ---- B) first run with a backlog > 50: pages through it silently
  {
    const w = world();
    const page = (from, n) => ({ items: Array.from({ length: n }, (_, i) => item(from + i)), last_id: from + n - 1 });
    w.respond(page(1, 50), page(51, 20));
    w.n.onScreen('home'); await tick(20);
    assert.deepEqual(w.log.gets.map(g => g.since), [null, 50]);
    assert.equal(w.mem['dz_notif_since_p1'], '70'); assert.equal(w.log.toasts.length, 0);
  }

  // ---- C) schedule: 30 s timer on home/detail/catalog, paused on the player, ONE immediate query after leaving it
  {
    const w = world();
    w.mem['dz_notif_since_p1'] = '10';
    w.respond({ items: [], last_id: 10 });
    w.n.onScreen('home'); await tick();
    assert.equal(w.log.gets.length, 1, 'query on entering the first screen');
    w.n.onScreen('detail'); w.n.onScreen('catalog'); w.n.onScreen('detail'); await tick();
    assert.equal(w.log.gets.length, 1, 'moving between polled screens does not add queries');
    w.T.advance(29999); await tick(); assert.equal(w.log.gets.length, 1);
    w.T.advance(1); await tick(); assert.equal(w.log.gets.length, 2, '30 s interval');
    assert.equal(w.n.isRunning(), true);
    w.n.onScreen('player');
    assert.equal(w.n.isRunning(), false, 'player: timer stopped');
    w.T.advance(120000); await tick();
    assert.equal(w.log.gets.length, 2, 'nothing polled while the player is open');
    w.respond({ items: [item(11)], last_id: 11 });
    w.n.onScreen('detail'); await tick();
    assert.equal(w.log.gets.length, 3, 'leaving the player: one query right away');
    assert.equal(w.log.toasts.length, 1, 'a source found while watching is announced on return');
    assert.equal(w.n.isRunning(), true);
    w.n.onScreen('settings'); assert.equal(w.n.isRunning(), false, 'settings/profiles are not polled');
    w.n.onScreen('profiles'); w.T.advance(90000); await tick(); assert.equal(w.log.gets.length, 3);
  }

  // ---- D) several notifications: toasted oldest first, read up to the last, non source_found kinds are skipped but advance the cursor
  {
    const w = world();
    w.mem['dz_notif_since_p1'] = '10';
    w.respond({ items: [item(11, { title: 'A', season: null, episode: null, episode_id: '' }), item(12, { kind: 'future_kind' }), item(13, { title: 'B', season: 1, episode: 9 })], last_id: 13 });
    w.n.onScreen('home'); await tick();
    assert.deepEqual(w.log.toasts.map(t => t.title), ['A', 'B S01 B09'], 'film: title only; series: title + S/B; unknown kind: no toast');
    assert.deepEqual(w.log.reads, [{ pid: 'p1', upto: 13 }]);
    assert.equal(w.mem['dz_notif_since_p1'], '13');
    assert.equal(N.textFor(item(1, { season: 0, episode: 5 })).title, 'Kayıp Sinyal S00 B05');
  }

  // ---- E) toast queue full: the rest is left for the next poll (cursor only advances to what was shown)
  {
    const w = world();
    w.mem['dz_notif_since_p1'] = '10';
    let accepted = 0;
    w.setRich(() => (++accepted <= 1));
    w.respond({ items: [item(11, { title: 'A' }), item(12, { title: 'B' }), item(13, { title: 'C' })], last_id: 13 });
    w.n.onScreen('home'); await tick();
    assert.deepEqual(w.log.toasts.map(t => t.title), ['A S04 B02']);
    assert.equal(w.mem['dz_notif_since_p1'], '11', 'cursor stops at the last shown one');
    assert.deepEqual(w.log.reads, [{ pid: 'p1', upto: 11 }]);
    w.setRich(() => true);
    w.respond({ items: [item(12, { title: 'B' }), item(13, { title: 'C' })], last_id: 13 });
    w.T.advance(30000); await tick();
    assert.equal(w.log.gets[1].since, 11);
    assert.deepEqual(w.log.toasts.map(t => t.title), ['A S04 B02', 'B S04 B02', 'C S04 B02']);
    assert.equal(w.mem['dz_notif_since_p1'], '13');
  }

  // ---- F) per-profile cursor, no profile -> no request, errors/offline silent (cursor untouched), no overlapping requests
  {
    const w = world({ profile: null });
    w.n.onScreen('home'); await tick();
    assert.equal(w.log.gets.length, 0, 'no profile selected: nothing to ask');
    w.setProfile('p2'); w.mem['dz_notif_since_p1'] = '7';
    w.respond({ items: [], last_id: 0 });
    w.T.advance(30000); await tick();
    assert.equal(w.log.gets[0].pid, 'p2'); assert.strictEqual(w.log.gets[0].since, null, 'p2 has its own (empty) cursor: first run');
    assert.equal(w.mem['dz_notif_since_p1'], '7', 'p1 cursor untouched'); assert.equal(w.mem['dz_notif_since_p2'], '0');
    w.respond(() => Promise.reject(new Error('down')));
    w.T.advance(30000); await tick();
    assert.equal(w.log.gets.length, 2); assert.equal(w.mem['dz_notif_since_p2'], '0', 'error: silent, cursor kept'); assert.equal(w.log.toasts.length, 0);
    w.setOffline(true);
    w.T.advance(30000); await tick();
    assert.equal(w.log.gets.length, 2, 'offline: no request');
    w.setOffline(false);
    let release;
    w.respond(() => new Promise(r => { release = r; }));
    w.T.advance(30000); await tick();
    w.T.advance(30000); await tick();
    assert.equal(w.log.gets.length, 3, 'a slow request is not overlapped by the next tick');
    release({ items: [item(1)], last_id: 1 }); await tick();
    assert.equal(w.log.toasts.length, 1);
    w.n.stop(); assert.equal(w.n.isRunning(), false);
  }

  // ---- G) wiring: app.js notifies on every screen change, api.js request shapes, script order in index.html
  {
    const calls = [];
    const el = tag => ({ tagName: tag, style: {}, children: [], appendChild() {}, removeChild() {}, setAttribute() {}, addEventListener() {} });
    const window = { DZ: { notifications: { onScreen: n => calls.push(n) }, nav: { reset() {}, restoreNext() {}, snapshot: () => null }, modal: { isOpen: () => false },
      screens: { home: { enter() {}, exit() {} }, player: { enter() {}, exit() {} } } }, addEventListener() {} };
    const document = { readyState: 'loading', addEventListener() {}, getElementById: () => el('div'), createElement: el };
    const sandbox = { window, document, console: { log() {} }, setTimeout, clearTimeout };
    vm.createContext(sandbox);
    vm.runInContext(read('app.js'), sandbox);
    window.DZ.app.go('home', {}); window.DZ.app.go('player', {}); window.DZ.app.back();
    assert.deepEqual(calls, ['home', 'player', 'home'], 'app.js tells the poller about every screen');

    const reqs = [];
    const w2 = { DZ: {}, fetch: (u, i) => { reqs.push({ u, i }); return Promise.resolve({ ok: true, text: () => Promise.resolve('{}') }); }, localStorage: { getItem: () => 'http://s', setItem() {}, removeItem() {} }, location: { search: '' }, setTimeout, clearTimeout };
    const sb2 = { window: w2, console: { log() {} }, setTimeout, clearTimeout, fetch: w2.fetch, Promise };
    vm.createContext(sb2);
    vm.runInContext(read('api.js'), sb2);
    const A = w2.DZ.api;
    await A.notifications('p 1', 12); await A.notifications('p1', null); await A.markNotificationsRead('p1', 12); await A.sourceFinder('d1', 'd1:s1:e2'); await A.sourceFinder('m1');
    assert.equal(reqs[0].u, 'http://s/api/notifications?profile=p%201&since=12');
    assert.equal(reqs[1].u, 'http://s/api/notifications?profile=p1', 'no since on the first run');
    assert.equal(reqs[2].u, 'http://s/api/notifications/read?profile=p1'); assert.equal(reqs[2].i.method, 'POST'); assert.equal(reqs[2].i.body, '{"upto":12}');
    assert.equal(reqs[3].u, 'http://s/api/source-finder/d1?episode=d1%3As1%3Ae2');
    assert.equal(reqs[4].u, 'http://s/api/source-finder/m1');

    const html = fs.readFileSync(path.join(root, '../index.html'), 'utf8');
    const at = s => html.indexOf(s);
    assert(at('js/notifications.js') > at('js/ui/toast.js') && at('js/notifications.js') < at('js/app.js'), 'notifications.js between toast.js and app.js');
    assert(at('js/finder.js') > at('js/api.js') && at('js/finder.js') < at('js/screens/player.js') && at('js/finder.js') < at('js/playflow.js'), 'finder.js before player/playflow');
    ['finder.js', 'notifications.js'].forEach(f => assert(!/\?\.|\?\?/.test(read(f).replace(/\/\*[\s\S]*?\*\//g, '')), f + ': ES2017 only'));
  }

  console.log('Notifications: first-run silent cursor, 30 s poll on home/detail/catalog, player pause + one query on return, toast + read, queue-full retry, errors silent: OK');
})().catch(e => { console.error(e); process.exitCode = 1; });
