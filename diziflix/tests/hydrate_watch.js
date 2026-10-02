// Hydration watcher (js/hydrate_watch.js): polls ?poll=1 every 3 s for at most 120 s (40 polls), one watcher per id, at most 3 concurrent
// (the rest queue), silent stop on a network error, decision hydrating/seasons (hydrating undefined = false), global notice via
// DZ.toast.showRich (ready = success + title, unavailable = warn + failure text; timeout and errors stay silent; plain toast only as fallback), plus the api.js `poll` option (and the old detail() signature unchanged).
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, timers } = require('./_fake_dom');

const root = path.join(__dirname, '../tizen-client/js');
const read = f => fs.readFileSync(path.join(root, f), 'utf8');
const tick = async (n = 8) => { for (let i = 0; i < n; i++) await Promise.resolve(); };

function load(extraDZ) {
  const t = timers();
  const window = { DZ: Object.assign({}, extraDZ || {}), setTimeout: t.setTimeout, clearTimeout: t.clearTimeout };
  vm.runInNewContext(read('hydrate_watch.js'), { window, console: { log() {} } });
  return { t, DZ: window.DZ, H: window.DZ.hydrate };
}

// fake api: per-id queue of answers ({...body} | Error); records every call
function fakeApi(script) {
  const calls = [];
  const api = {
    profileId: () => 'p1',
    detail(id, pid, opts) {
      calls.push({ id, pid, opts });
      const q = script[id] || [];
      const a = q.length > 1 ? q.shift() : q[0];
      return a instanceof Error ? Promise.reject(a) : Promise.resolve(a);
    }
  };
  return { api, calls, count: id => calls.filter(c => c.id === id).length };
}
const pending = extra => Object.assign({ id: 'x', type: 'series', hydrating: true, seasons: [] }, extra || {});
const done = extra => Object.assign({ id: 'x', type: 'series', hydrating: false, seasons: [{ season: 1, episodes: [{ id: 'x:s1:e1' }] }] }, extra || {});

(async () => {
  const { H: G } = load();

  // ---- decision table (hydrating undefined = false)
  assert.equal(G.decide({ type: 'series', hydrating: true, seasons: [] }), 'pending');
  assert.equal(G.decide({ type: 'movie', hydrating: true }), 'pending');
  assert.equal(G.decide({ type: 'series', hydrating: false, seasons: [{}] }), 'ready');
  assert.equal(G.decide({ type: 'series', hydrating: false, seasons: [] }), 'unavailable');
  assert.equal(G.decide({ type: 'series', hydrating: false }), 'unavailable');
  assert.equal(G.decide({ type: 'movie', hydrating: false, seasons: [] }), 'ready');
  assert.equal(G.decide({ type: 'movie' }), 'ready', 'old server (no hydrating) film = ready');
  assert.equal(G.decide({ type: 'series', seasons: [{}] }), 'ready', 'old server (no hydrating) series with seasons = ready');
  assert.equal(G.decide({ type: 'series' }), 'unavailable');
  assert.equal(G.decide(null), 'error');
  assert.deepEqual(G.noticeFor({ result: 'ready', title: 'Dizi' }), { title: 'Dizi', message: '', kind: 'success' }, 'plain title, label already says "İzlemeye hazır"');
  assert.deepEqual(G.noticeFor({ result: 'ready' }), { title: 'İçerik', message: '', kind: 'success' });
  assert.deepEqual(G.noticeFor({ result: 'unavailable', title: 'Dizi' }), { title: 'Dizi', message: 'Bölümler şu an alınamadı, daha sonra tekrar deneyin', kind: 'warn' });
  assert.equal(G.noticeFor({ result: 'timeout', title: 'Dizi' }), null);
  assert.equal(G.noticeFor({ result: 'error', title: 'Dizi' }), null);
  assert.equal(G.noticeFor(null), null);
  assert.equal(G.messageFor({ result: 'ready', title: 'Dizi' }), '«Dizi» izlemeye hazır');
  assert.equal(G.messageFor({ result: 'unavailable', title: 'Dizi' }), 'Bölümler şu an alınamadı, daha sonra tekrar deneyin');
  assert.equal(G.messageFor({ result: 'timeout', title: 'Dizi' }), null);
  assert.equal(G.messageFor({ result: 'error', title: 'Dizi' }), null);
  assert.equal(G.INTERVAL, 3000); assert.equal(G.MAX_MS, 120000); assert.equal(G.MAX_POLLS, 40); assert.equal(G.MAX_CONCURRENT, 3);

  // ---- polls every 3 s with {poll:true} and the profile id; ready after the 3rd poll; nothing after it
  {
    const t = timers(), f = fakeApi({ x: [pending(), pending(), done()] }), events = [];
    const H = G.create({ api: f.api, timers: t });
    H.on(e => events.push(e));
    assert.equal(H.watch('x', 'Dizi'), true);
    assert.equal(H.watch('x', 'Dizi'), false, 'one watcher per id');
    assert.equal(H.count(), 1);
    t.advance(2999); await tick();
    assert.equal(f.calls.length, 0, 'first poll only after 3 s');
    t.advance(1); await tick();
    assert.equal(f.calls.length, 1);
    assert.deepEqual(f.calls[0], { id: 'x', pid: 'p1', opts: { poll: true } });
    t.advance(3000); await tick();
    assert.equal(f.calls.length, 2);
    assert.equal(events.length, 0, 'still hydrating: no event');
    t.advance(3000); await tick();
    assert.equal(f.calls.length, 3);
    assert.equal(events.length, 1);
    assert.equal(events[0].id, 'x'); assert.equal(events[0].title, 'Dizi'); assert.equal(events[0].result, 'ready');
    assert.equal(events[0].data.seasons.length, 1, 'event carries the polled body');
    assert.equal(H.count(), 0); assert.equal(H.isWatching('x'), false); assert.equal(t.pending(), 0, 'no timer left');
    t.advance(60000); await tick();
    assert.equal(f.calls.length, 3, 'no polling after the decision');
    // the id can be watched again afterwards
    assert.equal(H.watch('x', 'Dizi'), true);
  }

  // ---- unavailable: hydration finished but the series is still empty (site failure)
  {
    const t = timers(), f = fakeApi({ x: [pending(), pending({ hydrating: false })] }), events = [];
    const H = G.create({ api: f.api, timers: t });
    H.on(e => events.push(e));
    H.watch('x', 'Dizi');
    t.advance(3000); await tick();
    t.advance(3000); await tick();
    assert.equal(events.length, 1); assert.equal(events[0].result, 'unavailable');
    assert.equal(H.count(), 0);
  }

  // ---- film: hydrating false is enough (no seasons); old server without the field = false
  {
    const t = timers(), f = fakeApi({ m: [{ id: 'm', type: 'movie', hydrating: true }, { id: 'm', type: 'movie', hydrating: false }], o: [{ id: 'o', type: 'movie' }] }), ev = [];
    const H = G.create({ api: f.api, timers: t });
    H.on(e => ev.push(e.id + ':' + e.result));
    H.watch('m', 'Film'); H.watch('o', 'Eski');
    t.advance(3000); await tick();
    assert.deepEqual(ev, ['o:ready'], 'hydrating undefined counts as false');
    t.advance(3000); await tick();
    assert.deepEqual(ev, ['o:ready', 'm:ready']);
  }

  // ---- timeout: 40 polls over 120 s, then stop; no ready/unavailable event, no further requests
  {
    const t = timers(), f = fakeApi({ x: [pending()] }), events = [];
    const H = G.create({ api: f.api, timers: t });
    H.on(e => events.push(e));
    H.watch('x', 'Dizi');
    for (let i = 0; i < 39; i++) { t.advance(3000); await tick(20); }
    assert.equal(f.calls.length, 39, 'one poll per 3 s');
    assert.equal(events.length, 0);
    t.advance(3000); await tick(20);
    assert.equal(f.calls.length, 40, 'poll #40 at 120 s');
    assert.equal(events.length, 1); assert.equal(events[0].result, 'timeout');
    assert.equal(t.pending(), 0); assert.equal(H.count(), 0);
    t.advance(120000); await tick(20);
    assert.equal(f.calls.length, 40, 'no more polls after the timeout');
  }

  // ---- wall-clock guard: slow requests cannot stretch past 120 s
  {
    const t = timers(), f = fakeApi({ x: [pending()] }), events = [];
    let clock = 0;
    const H = G.create({ api: f.api, timers: t, now: () => clock });
    H.on(e => events.push(e.result));
    H.watch('x', 'Dizi');
    t.advance(3000); await tick();
    clock = 121000;    // the request took very long
    t.advance(3000); await tick();
    assert.deepEqual(events, ['timeout']);
    assert(f.calls.length <= 2);
  }

  // ---- network/server error: silent stop after ONE attempt (no retry loop, no ready/unavailable)
  {
    const t = timers(), f = fakeApi({ x: [pending(), new Error('Sunucuya ulasilamiyor')] }), events = [];
    const H = G.create({ api: f.api, timers: t });
    H.on(e => events.push(e.result));
    H.watch('x', 'Dizi');
    t.advance(3000); await tick();
    t.advance(3000); await tick();
    assert.deepEqual(events, ['error']);
    assert.equal(f.calls.length, 2); assert.equal(t.pending(), 0); assert.equal(H.count(), 0);
    t.advance(60000); await tick();
    assert.equal(f.calls.length, 2, 'stopped after the failure');
    // a synchronous throw from the api is handled the same way
    const H2 = G.create({ api: { profileId: () => 'p', detail() { throw new Error('boom'); } }, timers: t });
    const ev2 = []; H2.on(e => ev2.push(e.result));
    H2.watch('y', 'Y'); t.advance(3000); await tick();
    assert.deepEqual(ev2, ['error']);
    // a throwing listener does not break the others
    const H3 = G.create({ api: fakeApi({ z: [done()] }).api, timers: t });
    const ev3 = []; H3.on(() => { throw new Error('listener'); }); H3.on(e => ev3.push(e.result));
    H3.watch('z', 'Z'); t.advance(3000); await tick();
    assert.deepEqual(ev3, ['ready']);
  }

  // ---- concurrency: at most 3 watchers poll; the 4th queues and starts (first poll 3 s later) when a slot frees
  {
    const t = timers();
    const f = fakeApi({ a: [pending(), done()], b: [pending()], c: [pending()], d: [pending(), done()] });
    const H = G.create({ api: f.api, timers: t });
    const ev = []; H.on(e => ev.push(e.id + ':' + e.result));
    ['a', 'b', 'c', 'd'].forEach(id => H.watch(id, id.toUpperCase()));
    assert.equal(H.count(), 4); assert.equal(H.activeCount(), 3);
    t.advance(3000); await tick();
    assert.equal(f.count('d'), 0, 'the 4th watcher does not poll while 3 are active');
    assert.equal(f.calls.length, 3);
    t.advance(3000); await tick();           // a finishes -> d takes the slot
    assert.deepEqual(ev, ['a:ready']);
    assert.equal(H.activeCount(), 3); assert.equal(H.count(), 3);
    assert.equal(f.count('d'), 0);
    t.advance(3000); await tick();
    assert.equal(f.count('d'), 1, 'queued watcher polls 3 s after it started');
    t.advance(3000); await tick();
    assert.deepEqual(ev, ['a:ready', 'd:ready']);
    // cancelling frees the slot too; queued ones are dropped silently
    assert.equal(H.cancel('b'), true); assert.equal(H.cancel('nope'), false);
    H.stopAll();
    assert.equal(H.count(), 0); assert.equal(H.activeCount(), 0); assert.equal(t.pending(), 0);
    assert.deepEqual(ev, ['a:ready', 'd:ready'], 'cancel/stopAll emit nothing');
  }

  // ---- default instance wiring: rich notice for ready/unavailable only, even with no detail screen; never takes focus (no nav calls)
  {
    const rich = [], plain = [];
    const f = fakeApi({ r: [done({ id: 'r' })], u: [pending({ id: 'u', hydrating: false })], t: [pending({ id: 't' })], e: [new Error('x')] });
    const { t, DZ, H } = load({ api: f.api, toast: { show: m => plain.push(m), showRich: o => rich.push(o) }, nav: new Proxy({}, { get() { throw new Error('toast must not touch nav'); } }) });
    H.watch('r', 'Dizi Bir'); H.watch('u', 'Dizi Iki'); H.watch('e', 'Hata');
    t.advance(3000); await tick();
    assert.deepEqual(rich, [
      { title: 'Dizi Bir', message: '', kind: 'success' },
      { title: 'Dizi Iki', message: 'Bölümler şu an alınamadı, daha sonra tekrar deneyin', kind: 'warn' }
    ]);
    assert.deepEqual(plain, [], 'the small toast is not used when showRich exists');
    H.watch('t', 'Sure');
    for (let i = 0; i < 41; i++) { t.advance(3000); await tick(20); }
    assert.equal(rich.length, 2, 'timeout: no notice');
    assert.equal(H.isWatching('t'), false);
    assert(DZ.hydrate === H);
  }

  // ---- fallback: a toast component without showRich still gets the old plain texts
  {
    const plain = [];
    const f = fakeApi({ r: [done({ id: 'r' })], u: [pending({ id: 'u', hydrating: false })] });
    const { t, H } = load({ api: f.api, toast: { show: m => plain.push(m) } });
    H.watch('r', 'Dizi Bir'); H.watch('u', 'Dizi Iki');
    t.advance(3000); await tick();
    assert.deepEqual(plain, ['«Dizi Bir» izlemeye hazır', 'Bölümler şu an alınamadı, daha sonra tekrar deneyin']);
  }

  // ---- api.js: detail(id, pid, {poll:true}) -> ?poll=1 with a short timeout; the old 2-argument call is unchanged
  {
    const fetched = [];
    const window = { localStorage: { getItem: () => null, setItem() {}, removeItem() {} }, location: { search: '' }, DZ: {}, setTimeout, clearTimeout };
    const sandbox = { window, console: { log() {} }, setTimeout, clearTimeout,
      fetch: url => { fetched.push(url); return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve('{"id":"a b","hydrating":false}') }); } };
    vm.runInNewContext(read('api.js'), sandbox);
    const api = window.DZ.api;
    const a = await api.detail('a b', 'p1');
    const b = await api.detail('a b', 'p1', { poll: true });
    const c = await api.detail('a b', 'p1', {});
    assert.equal(fetched[0], api.baseUrl() + '/api/detail/a%20b?profile=p1');
    assert.equal(fetched[1], api.baseUrl() + '/api/detail/a%20b?profile=p1&poll=1');
    assert.equal(fetched[2], fetched[0]);
    assert.equal(a.hydrating, false); assert.equal(b.id, 'a b'); assert.equal(c.id, 'a b');
  }

  // ---- wiring: script is shipped, loaded after toast.js and before the screens
  {
    const html = fs.readFileSync(path.join(root, '../index.html'), 'utf8');
    const at = s => html.indexOf(s);
    assert(at('js/hydrate_watch.js') > at('js/ui/toast.js') && at('js/hydrate_watch.js') < at('js/screens/detail.js'));
    assert(!/\?\.|\?\?/.test(read('hydrate_watch.js').replace(/\/\*[\s\S]*?\*\//g, '')), 'ES2017 only: no ?. / ??');
  }

  console.log('Hydration watcher: 3 s polling, 120 s timeout, silent network stop, 3 concurrent, decision, rich notice, api poll option: OK');
})().catch(e => { console.error(e); process.exitCode = 1; });
