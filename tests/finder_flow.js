// Source finder on the TV client (real js/finder.js + js/playflow.js + js/screens/player.js, fake API/engine/modal/timers): when /api/streams gives
// NO stream, an optional `finder` field decides the error panel text: searching -> "Kaynak aranıyor… Bulununca haber vereceğiz…" (status polled every
// 5 s, found -> the stream is requested again automatically, not_found -> panel refreshed), not_found -> "Bu bölüm/film için kaynak bulunamadı.",
// no finder -> the old generic message. Buttons stay Tekrar dene/Geri (player: Geri/Yeniden dene). `proxied` streams play their `url` unchanged.
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, timers } = require('./_fake_dom');

const root = path.join(__dirname, '../tizen-client/js');
const read = f => fs.readFileSync(path.join(root, f), 'utf8');
const tick = async (n = 8) => { for (let i = 0; i < n; i++) await Promise.resolve(); };

const SEARCHING = 'Kaynak aranıyor… Bulununca haber vereceğiz. Sayfada kalabilir ya da uygulamada gezinebilirsin.';
const GENERIC = 'Bu içerik için kullanılabilir video kaynağı bulunamadı.';
const S = (id, extra) => Object.assign({ url: 'https://v.example/' + id + '.mp4', type: 'mp4', attempt_token: id + '-token', kind: 'movie', label: id }, extra || {});

// ---------------------------------------------------------------- playflow harness
function harness(streamsFn, finderFn, x) {
  x = x || {};
  const T = timers();
  const log = { dialogs: [], loadings: [], stages: [], go: [], streamCalls: 0, finderCalls: [], engines: [], modalOpen: false };
  const window = { navigator: { onLine: true }, DZ: {} };
  const DZ = window.DZ;
  DZ.api = {
    streams() { log.streamCalls++; return Promise.resolve(streamsFn(log.streamCalls)); },
    sourceFinder(id, ep) { log.finderCalls.push([id, ep]); return Promise.resolve(finderFn(log.finderCalls.length)); },
    playbackReport(p) { (log.reports = log.reports || []).push(p); return Promise.resolve(x.reportAnswer ? x.reportAnswer(p) : {}); }
  };
  DZ.modal = {
    loading(cfg) { log.modalOpen = false; const ctl = { stage: '', setStage(t) { ctl.stage = t; log.stages.push(t); }, close() {}, cfg }; log.loadings.push(ctl); return ctl; },
    open(cfg) { log.dialogs.push(cfg); log.modalOpen = true; },
    isOpen() { return log.modalOpen; },
    close() { log.modalOpen = false; }
  };
  DZ.app = { go(name, params, replace) { log.go.push({ name, params, replace }); }, back() {} };
  DZ.player = {
    hasAvplay: () => true, isEmbedStream: s => s.type === 'embed',
    createEngine(host, useAv, cb) {
      const eng = { av: true, cb, prepared: false, playing: false,
        setCallbacks(n) { eng.cb = n; },
        prepare(url) { eng.url = url; if (x.engineFails && x.engineFails(url)) { Promise.resolve().then(() => eng.cb.error('boom', 'playback_failed', 'hls:networkError/manifestLoadError/403')); return; } Promise.resolve().then(() => { eng.prepared = true; eng.cb.prepared(); }); },
        start() { eng.playing = true; Promise.resolve().then(() => eng.cb.started()); },
        destroy() {} };
      log.engines.push(eng);
      return eng;
    }
  };
  const sandbox = { window, console: { log() {} }, setTimeout: T.setTimeout, clearTimeout: T.clearTimeout, setInterval: T.setInterval, clearInterval: T.clearInterval, Promise };
  vm.createContext(sandbox);
  vm.runInContext(read('finder.js'), sandbox);
  vm.runInContext(read('playflow.js'), sandbox);
  return { T, log, DZ, flow: DZ.playflow, F: DZ.finder };
}
const opts = extra => Object.assign({ itemId: 'm1', episodeId: 'm1', title: 'Film', type: 'movie', kind: 'video', onCancel() { this.cancelled = (this.cancelled || 0) + 1; } }, extra || {});
const epOpts = extra => opts(Object.assign({ itemId: 'd1', episodeId: 'd1:s1:e3', title: 'Dizi', type: 'series' }, extra || {}));

(async () => {
  // ---- A) searching: the new text + polling every 5 s; found -> automatic retry ("Kaynak bulundu, yeniden deneniyor…") -> the player opens
  {
    let finderState = 'searching';
    const h = harness(n => (n === 1 ? { streams: [], finder: { state: 'searching' } } : { streams: [S('good')] }), () => ({ state: finderState }));
    assert.equal(h.F.MSG_SEARCHING, SEARCHING, 'exact text');
    h.flow.start(epOpts());
    await tick();
    assert.equal(h.log.dialogs.length, 1);
    const d = h.log.dialogs[0];
    assert.equal(d.message, SEARCHING); assert.equal(d.title, 'Kaynak aranıyor'); assert.equal(d.fullscreen, true);
    assert.deepEqual(d.buttons.map(b => b.label), ['Tekrar dene', 'Geri'], 'buttons: Tekrar dene / Geri (existing)');
    assert.equal(h.log.go.length, 0);
    assert.equal(h.log.finderCalls.length, 0, 'first poll after 5 s');
    h.T.advance(4999); await tick(); assert.equal(h.log.finderCalls.length, 0);
    h.T.advance(1); await tick();
    assert.deepEqual(h.log.finderCalls, [['d1', 'd1:s1:e3']], 'GET /api/source-finder/{item}?episode=');
    h.T.advance(5000); await tick();
    assert.equal(h.log.finderCalls.length, 2, 'still searching: polled again');
    assert.equal(h.log.dialogs.length, 1, 'panel unchanged while searching');
    finderState = 'found';
    h.T.advance(5000); await tick(20);
    assert.equal(h.log.finderCalls.length, 3);
    assert.equal(h.log.streamCalls, 2, 'found -> the stream is requested again automatically');
    assert.equal(h.log.loadings.length, 2); assert.equal(h.log.loadings[1].stage === 'Video hazırlanıyor…' || h.log.stages.indexOf('Kaynak bulundu, yeniden deneniyor…') >= 0, true);
    assert.equal(h.log.stages.indexOf('Kaynak bulundu, yeniden deneniyor…'), 1, 'stage line says the source was found');
    assert.equal(h.log.go.length, 1); assert.equal(h.log.go[0].name, 'player');
    h.T.advance(60000); await tick();
    assert.equal(h.log.finderCalls.length, 3, 'polling stopped');
  }

  // ---- B) searching -> not_found: the panel is refreshed with the not-found text, polling stops (film wording for a movie)
  {
    let n = 0;
    const h = harness(() => ({ streams: [], finder: { state: 'searching' } }), () => (++n < 2 ? { state: 'searching' } : { state: 'not_found', steps: [] }));
    h.flow.start(opts());
    await tick();
    h.T.advance(5000); await tick();
    h.T.advance(5000); await tick();
    assert.equal(h.log.dialogs.length, 2, 'panel re-opened with the new state');
    assert.equal(h.log.dialogs[1].message, 'Bu film için kaynak bulunamadı.'); assert.equal(h.log.dialogs[1].title, 'Kaynak bulunamadı');
    assert.deepEqual(h.log.dialogs[1].buttons.map(b => b.label), ['Tekrar dene', 'Geri']);
    h.T.advance(60000); await tick();
    assert.equal(h.log.finderCalls.length, 2, 'no polling after not_found');
    assert.equal(h.log.go.length, 0); assert.equal(h.log.streamCalls, 1);
  }

  // ---- C) not_found straight from /api/streams: episode wording, no polling at all
  {
    const h = harness(() => ({ streams: [], finder: { state: 'not_found' } }), () => ({ state: 'idle' }));
    h.flow.start(epOpts());
    await tick();
    assert.equal(h.log.dialogs[0].message, 'Bu bölüm için kaynak bulunamadı.');
    h.T.advance(60000); await tick();
    assert.equal(h.log.finderCalls.length, 0);
  }

  // ---- J) the stream RESOLVES but cannot be played: the failure report's answer carries `finder` -> "Kaynak aranıyor…" panel (not the generic one),
  //         polled every 5 s, found -> the stream is requested again automatically (the new stream plays)
  {
    let finderState = 'searching';
    const h = harness(n => ({ streams: [S(n === 1 ? 'bad' : 'good')] }), () => ({ state: finderState }),
      { engineFails: url => url.indexOf('bad') >= 0, reportAnswer: p => (p.event === 'failure' ? { ok: true, finder: { state: 'searching' } } : { ok: true }) });
    h.flow.start(epOpts());
    await tick(30);
    assert.deepEqual(h.log.reports.map(r => r.attempt_token + ':' + r.event), ['bad-token:failure']);
    assert.equal(h.log.dialogs.length, 1);
    assert.equal(h.log.dialogs[0].message, SEARCHING); assert.equal(h.log.dialogs[0].title, 'Kaynak aranıyor');
    assert.deepEqual(h.log.dialogs[0].buttons.map(b => b.label), ['Tekrar dene', 'Geri']);
    h.T.advance(5000); await tick();
    assert.deepEqual(h.log.finderCalls, [['d1', 'd1:s1:e3']]);
    finderState = 'found';
    h.T.advance(5000); await tick(30);
    assert.equal(h.log.streamCalls, 2, 'found -> /api/streams again');
    assert.equal(h.log.go.length, 1); assert.equal(h.log.go[0].name, 'player');
  }
  // ---- K) the report answer has no finder (or arrives late): the old generic panel, no polling
  {
    const h = harness(() => ({ streams: [S('bad')] }), () => ({ state: 'idle' }), { engineFails: () => true });
    h.flow.start(epOpts());
    await tick(30);
    assert.equal(h.log.dialogs.length, 1); assert.equal(h.log.dialogs[0].title, 'Kaynak çalışmıyor');
    h.T.advance(60000); await tick();
    assert.equal(h.log.finderCalls.length, 0);
  }

  // ---- D) no finder (or an unknown state): the old generic message and title, no polling
  {
    for (const res of [{ streams: [] }, { streams: [], finder: null }, { streams: [], finder: { state: 'weird' } }, { streams: [], finder: {} }]) {
      const h = harness(() => res, () => ({ state: 'searching' }));
      h.flow.start(epOpts());
      await tick();
      assert.equal(h.log.dialogs[0].title, 'Kaynak çalışmıyor'); assert.equal(h.log.dialogs[0].message, GENERIC);
      h.T.advance(60000); await tick();
      assert.equal(h.log.finderCalls.length, 0);
    }
  }

  // ---- E) Geri / Tekrar dene on the searching panel stop the polling; Back calls onCancel once
  {
    const h = harness(() => ({ streams: [], finder: { state: 'searching' } }), () => ({ state: 'searching' }));
    const o = opts();
    h.flow.start(o);
    await tick();
    h.T.advance(5000); await tick();
    assert.equal(h.log.finderCalls.length, 1);
    h.log.dialogs[0].onCancel();
    assert.equal(o.cancelled, 1);
    h.T.advance(60000); await tick();
    assert.equal(h.log.finderCalls.length, 1, 'Back stops the status polling');
    const h2 = harness(() => ({ streams: [], finder: { state: 'searching' } }), () => ({ state: 'searching' }));
    h2.flow.start(opts());
    await tick();
    h2.log.dialogs[0].onDone('retry');
    assert.equal(h2.log.streamCalls, 2, 'retry asks the server again'); assert.equal(h2.log.loadings.length, 2);
    h2.T.advance(60000); await tick();
    assert.equal(h2.log.finderCalls.length, 0, 'the old panel no longer polls');
    const h3 = harness(() => ({ streams: [], finder: { state: 'searching' } }), () => ({ state: 'searching' }));
    const o3 = opts();
    h3.flow.start(o3);
    await tick();
    h3.log.dialogs[0].onDone('back');
    assert.equal(o3.cancelled, 1);
    h3.T.advance(60000); await tick();
    assert.equal(h3.log.finderCalls.length, 0);
  }

  // ---- F) idle (no job / restarted server) and a closed panel stop the polling silently; network errors do not
  {
    const h = harness(() => ({ streams: [], finder: { state: 'searching' } }), () => ({ state: 'idle' }));
    h.flow.start(opts()); await tick();
    h.T.advance(5000); await tick(); h.T.advance(60000); await tick();
    assert.equal(h.log.finderCalls.length, 1, 'idle: stop'); assert.equal(h.log.dialogs.length, 1);
    const h2 = harness(() => ({ streams: [], finder: { state: 'searching' } }), () => ({ state: 'searching' }));
    h2.flow.start(opts()); await tick();
    h2.log.modalOpen = false;   // the panel was closed by something else
    h2.T.advance(5000); await tick(); h2.T.advance(60000); await tick();
    assert.equal(h2.log.finderCalls.length, 0, 'closed panel: no request');
    let calls = 0;
    const h3 = harness(() => ({ streams: [], finder: { state: 'searching' } }), () => ({ state: 'searching' }));
    h3.DZ.api.sourceFinder = () => { calls++; return calls === 1 ? Promise.reject(new Error('down')) : Promise.resolve({ state: 'not_found' }); };
    h3.flow.start(opts()); await tick();
    h3.T.advance(5000); await tick(); h3.T.advance(5000); await tick();
    assert.equal(calls, 2, 'a network error does not stop the polling');
    assert.equal(h3.log.dialogs[1].message, 'Bu film için kaynak bulunamadı.');
    // the poll is bounded (10 min = 120 polls)
    const h4 = harness(() => ({ streams: [], finder: { state: 'searching' } }), () => ({ state: 'searching' }));
    h4.flow.start(opts()); await tick();
    for (let i = 0; i < 130; i++) { h4.T.advance(5000); await tick(); }
    assert.equal(h4.log.finderCalls.length, 120, 'bounded polling');
  }

  // ---- G) `proxied` is ignored by the client: the stream url is played as given (the server already made it a proxy URL)
  {
    const proxied = S('p', { url: 'http://192.168.0.61:8090/api/stream-proxy/abc.def', proxied: true });
    const h = harness(() => ({ streams: [proxied, S('direct', { proxied: false })] }), () => ({ state: 'idle' }));
    h.flow.start(opts());
    await tick();
    assert.equal(h.log.engines[0].url, 'http://192.168.0.61:8090/api/stream-proxy/abc.def', 'proxy URL handed to the engine unchanged');
    assert.equal(h.log.go[0].params.prepared.streams[0].url, 'http://192.168.0.61:8090/api/stream-proxy/abc.def');
  }

  // ---- H) finder.messageFor / titleFor
  {
    const h = harness(() => ({ streams: [] }), () => ({ state: 'idle' }));
    assert.equal(h.F.messageFor({ state: 'searching' }, true), SEARCHING);
    assert.equal(h.F.messageFor({ state: 'not_found' }, true), 'Bu bölüm için kaynak bulunamadı.');
    assert.equal(h.F.messageFor({ state: 'not_found' }, false), 'Bu film için kaynak bulunamadı.');
    assert.strictEqual(h.F.messageFor(undefined, true), null); assert.strictEqual(h.F.messageFor({ state: 'found' }, true), null);
    assert.strictEqual(h.F.titleFor(null), null);
  }

  // ---- I) player screen opened directly (no prepared engine): same panel texts, buttons Geri / Yeniden dene, polling, found -> player re-entered
  {
    const T = timers();
    const log = { dialogs: [], go: [], backs: 0, finderCalls: 0, modalOpen: false, closed: 0, status: [] };
    let finderState = 'searching';
    const window = { navigator: { onLine: true }, DZ: {} };
    const DZ = window.DZ;
    DZ.api = { profileId: () => 'p1', streams: () => Promise.resolve({ streams: [], finder: { state: finderState === 'not_found_first' ? 'not_found' : 'searching' } }),
      sourceFinder: () => { log.finderCalls++; return Promise.resolve({ state: finderState }); }, playbackReport: () => Promise.resolve({}), progress: () => Promise.resolve({}) };
    DZ.modal = { open(cfg) { log.dialogs.push(cfg); log.modalOpen = true; }, isOpen: () => log.modalOpen, close() { log.modalOpen = false; log.closed++; } };
    DZ.app = { back() { log.backs++; }, go(n, p, r) { log.go.push({ n, p, r }); } };
    const sandbox = { window, document: { createElement: tag => { const n = el(tag); return n; }, body: el('body'), documentElement: el('html') }, webapis: undefined,
      console: { log() {} }, setTimeout: T.setTimeout, clearTimeout: T.clearTimeout, setInterval: T.setInterval, clearInterval: T.clearInterval, Promise,
      localStorage: { getItem() { return null; }, setItem() {} } };
    vm.createContext(sandbox);
    vm.runInContext(read('finder.js'), sandbox);
    vm.runInContext(read('screens/player.js'), sandbox);
    const screen = DZ.screens.player;
    const params = { itemId: 'd1', episodeId: 'd1:s1:e3', type: 'movie', title: 'Dizi' };
    screen.enter(el('div'), params);
    await tick();
    assert.equal(log.dialogs.length, 1);
    assert.equal(log.dialogs[0].message, SEARCHING); assert.equal(log.dialogs[0].title, 'Kaynak aranıyor');
    assert.deepEqual(log.dialogs[0].buttons.map(b => b.label), ['Geri', 'Yeniden dene'], 'player buttons: Geri / Yeniden dene (existing)');
    T.advance(5000); await tick();
    assert.equal(log.finderCalls, 1);
    finderState = 'found';
    T.advance(5000); await tick();
    assert.equal(log.go.length, 1, 'found: the player is re-entered (stream requested again)'); assert.equal(log.go[0].n, 'player'); assert.equal(log.go[0].r, true);
    assert.equal(log.closed, 1, 'search panel closed');
    // not_found straight away + Geri
    finderState = 'not_found_first';
    log.dialogs.length = 0;
    screen.exit();
    screen.enter(el('div'), { itemId: 'm1', episodeId: 'm1', type: 'movie' });
    await tick();
    assert.equal(log.dialogs[0].message, 'Bu film için kaynak bulunamadı.'); assert.equal(log.dialogs[0].title, 'Kaynak bulunamadı');
    log.dialogs[0].onDone('back');
    assert.equal(log.backs, 1);
    screen.exit();
    log.dialogs.length = 0;
    screen.enter(el('div'), { itemId: 'd1', episodeId: 'd1:s1:e3', type: 'movie' });   // an episode id (!= item id) reads as an episode
    await tick();
    assert.equal(log.dialogs[0].message, 'Bu bölüm için kaynak bulunamadı.');
    screen.exit();
    // no finder -> the old message
    DZ.api.streams = () => Promise.resolve({ streams: [] });
    log.dialogs.length = 0;
    screen.enter(el('div'), { itemId: 'm1', type: 'movie' });
    await tick();
    assert.equal(log.dialogs[0].title, 'Oynatılamadı'); assert.equal(log.dialogs[0].message, 'Bu içerik için kullanılabilir video kaynağı bulunamadı');
    screen.exit();
  }

  console.log('Source finder: searching/not_found/no-finder texts, 5 s polling (found -> auto retry), Back/Retry stop it, player direct path, proxied url unchanged: OK');
})().catch(e => { console.error(e); process.exitCode = 1; });
