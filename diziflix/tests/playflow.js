// Play flow: loading modal over the screen, streams tried in order under the modal, player opens only
// after a stream REALLY started, per-stream health reports, cancel/timeout/all-failed paths, and the
// player screen adopting the prepared engine without leaving stale error labels.
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, find, findAll, timers } = require('./_fake_dom');

const root = path.join(__dirname, '../tizen-client/js');
const read = f => fs.readFileSync(path.join(root, f), 'utf8');

// ---------------------------------------------------------------- scenario harness (fake engines)
function harness(streamsResponse, behaviours, opts) {
  const T = timers();
  const log = { reports: [], go: [], stages: [], dialogs: [], loadings: [], destroyed: [], streamCalls: 0, cancels: 0 };
  const engines = [];
  const window = { navigator: { onLine: !(opts && opts.offline) }, DZ: {} };
  const DZ = window.DZ;
  DZ.api = {
    streams() { log.streamCalls++; return typeof streamsResponse === 'function' ? streamsResponse() : Promise.resolve(streamsResponse); },
    playbackReport(p) { log.reports.push(p); return Promise.resolve({}); }
  };
  DZ.modal = {
    loading(cfg) {
      const ctl = { open: true, stage: '', closed: false,
        setStage(t) { ctl.stage = t; log.stages.push(t); },
        close() { ctl.open = false; ctl.closed = true; }, cfg };
      log.loadings.push(ctl);
      return ctl;
    },
    open(cfg) { log.dialogs.push(cfg); }
  };
  DZ.app = { go(name, params, replace) { log.go.push({ name, params, replace }); }, back() {} };
  DZ.player = {
    hasAvplay: () => true,
    isEmbedStream: s => s.type === 'embed',
    createEngine(host, useAv, cb, o) {
      const idx = engines.length;
      const eng = { idx, av: true, cb, detached: o && o.detached, prepared: false, playing: false, destroyed: false, started: null,
        setCallbacks(n) { eng.cb = n; },
        prepare(url, resume) { eng.url = url; eng.resume = resume; const b = behaviours[idx] || 'hang'; if (b === 'prepare-error') Promise.resolve().then(() => eng.cb.error('prepareAsync: boom')); else if (b === 'ok') Promise.resolve().then(() => { eng.prepared = true; eng.cb.prepared(); }); },
        start() { eng.playing = true; const b = behaviours[idx]; if (b === 'ok') Promise.resolve().then(() => eng.cb.started()); },
        destroy() { eng.destroyed = true; log.destroyed.push(idx); }
      };
      engines.push(eng);
      return eng;
    }
  };
  const sandbox = { window, console: { log() {} }, setTimeout: T.setTimeout, clearTimeout: T.clearTimeout, setInterval: T.setInterval, clearInterval: T.clearInterval };
  vm.runInNewContext(read('playflow.js'), sandbox);
  return { T, log, engines, flow: DZ.playflow, DZ, window };
}
const tick = async (n = 14) => { for (let i = 0; i < n; i++) await Promise.resolve(); };
const S = (id, extra) => Object.assign({ url: 'https://v.example/' + id + '.mp4', type: 'mp4', attempt_token: id + '-token', kind: 'movie', label: id }, extra || {});
const opts = extra => Object.assign({ itemId: 'movie-1', episodeId: null, title: 'Film', type: 'movie', kind: 'video', onCancel() { this.cancelled = (this.cancelled || 0) + 1; } }, extra || {});

(async () => {
  // A) first stream fails, second plays: everything under the modal, correct per-stream reports
  {
    const h = harness({ streams: [S('bad'), S('good')], resume_position: 37, duration: 90 }, ['prepare-error', 'ok']);
    const o = opts();
    h.flow.start(o);
    assert.equal(h.log.loadings.length, 1, 'one loading modal');
    assert.equal(h.log.stages[0], 'Kaynak aranıyor…');
    assert.equal(h.log.go.length, 0, 'player must not open while resolving');
    await tick();
    assert.equal(h.log.go.length, 1, 'player opens once, after the second stream started');
    assert.equal(h.engines.length, 2);
    assert.equal(h.engines[0].detached, true, 'probe engines are detached from the player screen');
    assert.equal(h.engines[0].resume, 37, 'resume position handed to the engine');
    assert(h.engines[0].destroyed && !h.engines[1].destroyed, 'failed engine released, playing engine kept');
    assert.deepEqual(h.log.stages, ['Kaynak aranıyor…', 'Video hazırlanıyor…', 'Başka kaynak deneniyor…']);
    assert.equal(h.log.reports.length, 2);
    assert.deepEqual(h.log.reports[0], { attempt_token: 'bad-token', event: 'failure', code: 'playback_failed', engine: 'avplay' });
    assert.deepEqual(h.log.reports[1], { attempt_token: 'good-token', event: 'success', code: '', engine: 'avplay' });
    const go = h.log.go[0];
    assert.equal(go.name, 'player'); assert.equal(go.replace, false);
    assert.equal(go.params.prepared.index, 1);
    assert.strictEqual(go.params.prepared.engine, h.engines[1]);
    assert.equal(go.params.prepared.resume, 37); assert.equal(go.params.prepared.duration, 90);
    assert.equal(go.params.itemId, 'movie-1'); assert.equal(go.params.title, 'Film');
    assert(h.log.loadings[0].closed, 'modal closed before the player opens');
    assert.equal(h.log.dialogs.length, 0, 'no error dialog when a fallback worked');
    assert.equal(h.T.pending(), 0, 'timers cleared after success');
    h.engines[1].cb.started();  // late duplicate
    assert.equal(h.log.reports.length, 2); assert.equal(h.log.go.length, 1);
  }

  // B) all streams fail -> error dialog, no player; retry restarts, Back returns without a player
  {
    const h = harness({ streams: [S('a'), S('b')] }, ['prepare-error', 'prepare-error']);
    const o = opts();
    h.flow.start(o);
    await tick();
    assert.equal(h.log.go.length, 0);
    assert.equal(h.log.dialogs.length, 1);
    const d = h.log.dialogs[0];
    assert.equal(d.title, 'Kaynak çalışmıyor'); assert.equal(d.fullscreen, true);
    assert.deepEqual(d.buttons.map(b => b.label), ['Tekrar dene', 'Geri']);
    assert.deepEqual(h.log.reports.map(r => r.attempt_token + ':' + r.event), ['a-token:failure', 'b-token:failure']);
    assert(h.engines.every(e => e.destroyed));
    d.onDone('back'); assert.equal(o.cancelled, 1);
    d.onDone('retry'); assert.equal(h.log.streamCalls, 2, 'retry asks the server again');
    assert.equal(h.log.loadings.length, 2);
  }

  // C) BACK cancels: engine released, late callbacks ignored, no reports, stay on the screen
  {
    const h = harness({ streams: [S('slow')] }, ['hang']);
    const o = opts();
    h.flow.start(o);
    await tick();
    assert.equal(h.engines.length, 1);
    h.log.loadings[0].cfg.onCancel();
    assert.equal(o.cancelled, 1);
    assert(h.engines[0].destroyed, 'cancel stops the attempt');
    h.engines[0].cb.started(); h.engines[0].cb.error('late');
    await tick();
    assert.equal(h.log.go.length, 0); assert.equal(h.log.reports.length, 0); assert.equal(h.log.dialogs.length, 0);
    assert.equal(h.T.pending(), 0);
    // cancel while the stream list is still loading
    let release;
    const h2 = harness(() => new Promise(r => { release = r; }), []);
    const o2 = opts();
    h2.flow.start(o2);
    h2.log.loadings[0].cfg.onCancel();
    release({ streams: [S('x')] });
    await tick();
    assert.equal(h2.engines.length, 0, 'no attempt after cancel'); assert.equal(o2.cancelled, 1);
  }

  // D) timeouts: "Başka kaynak deneniyor…" after 8 s, stream failure with code timeout after ~15 s, next stream
  {
    const h = harness({ streams: [S('hang'), S('good')] }, ['hang', 'ok']);
    h.flow.start(opts());
    await tick();
    assert.equal(h.log.stages[h.log.stages.length - 1], 'Video hazırlanıyor…');
    h.T.advance(7999);
    assert.equal(h.log.loadings[0].stage, 'Video hazırlanıyor…');
    h.T.advance(1);
    assert.equal(h.log.loadings[0].stage, 'Başka kaynak deneniyor…', 'slow attempt message at 8 s');
    h.T.advance(15000 - 8000 - 1);
    assert.equal(h.log.reports.length, 0, 'not failed yet');
    h.T.advance(2);
    assert.deepEqual(h.log.reports[0], { attempt_token: 'hang-token', event: 'failure', code: 'timeout', engine: 'avplay', detail: 'start-timeout' });
    await tick();
    assert.equal(h.log.go.length, 1); assert.equal(h.log.go[0].params.prepared.index, 1);
    assert.deepEqual(h.log.reports[1], { attempt_token: 'good-token', event: 'success', code: '', engine: 'avplay' });
  }

  // D2) html5 engine playing HLS with hls.js: the failure report carries hlsjs:true (the server then counts it as a real playback failure)
  {
    const h = harness({ streams: [S('bad', { type: 'hls' })] }, ['prepare-error']);
    const make = h.DZ.player.createEngine;
    h.DZ.player.createEngine = (host, useAv, cb, o) => { const e = make(host, useAv, cb, o); e.av = false; e.hlsjs = true; return e; };
    h.flow.start(opts());
    await tick();
    assert.deepEqual(h.log.reports[0], { attempt_token: 'bad-token', event: 'failure', code: 'playback_failed', engine: 'html5', hlsjs: true });
  }

  // E) ordering: verifiable files first, embed last; embed-only goes straight to the player (no probe, no report)
  {
    const emb = S('emb', { type: 'embed', url: 'https://embed.example/x' });
    const h = harness({ streams: [emb, S('file')] }, ['ok']);
    h.flow.start(opts({ kind: 'trailer' }));
    await tick();
    assert.equal(h.log.go[0].params.prepared.index, 0, 'file stream tried before the embed');
    assert.equal(h.log.go[0].params.prepared.streams[0].attempt_token, 'file-token', 'streams handed over in the tried order');
    assert.equal(h.log.go[0].params.prepared.streams[1].attempt_token, 'emb-token', 'embed sorted last');
    assert.equal(h.log.go[0].params.kind, 'trailer');
    const h2 = harness({ streams: [emb] }, []);
    h2.flow.start(opts());
    await tick();
    assert.equal(h2.log.go.length, 1); assert.equal(h2.engines.length, 0);
    assert.strictEqual(h2.log.go[0].params.prepared.engine, null);
    assert.equal(h2.log.reports.length, 0, 'embed playback cannot be observed: no success/failure guess');
    // the failed file stream falls back to the embed as the last resort
    const h3 = harness({ streams: [emb, S('file')] }, ['prepare-error']);
    h3.flow.start(opts());
    await tick();
    assert.equal(h3.log.go[0].params.prepared.index, 1, 'embed is the last resort after the file stream failed');
    assert.strictEqual(h3.log.go[0].params.prepared.engine, null);
    assert.deepEqual(h3.log.reports.map(r => r.attempt_token + ':' + r.event), ['file-token:failure']);
  }

  // F) no streams / server error / offline -> error dialog; replace + onCancel are forwarded for next-episode use
  {
    const h = harness({ streams: [] }, []);
    h.flow.start(opts()); await tick();
    assert.equal(h.log.dialogs[0].title, 'Kaynak çalışmıyor'); assert(/kullanılabilir video kaynağı/.test(h.log.dialogs[0].message));
    const h2 = harness(() => Promise.reject({ message: 'Sunucu zaman aşımı' }), []);
    h2.flow.start(opts()); await tick();
    assert.equal(h2.log.dialogs[0].message, 'Sunucu zaman aşımı'); assert.equal(h2.log.go.length, 0);
    const h3 = harness({ streams: [S('a'), S('b')] }, ['prepare-error', 'ok'], { offline: true });
    h3.flow.start(opts()); await tick();
    assert.equal(h3.engines.length, 1, 'offline: no point trying other streams');
    assert.equal(h3.log.reports[0].code, 'offline'); assert.equal(h3.log.dialogs.length, 1);
    const h4 = harness({ streams: [S('good')] }, ['ok']);
    h4.flow.start(opts({ replace: true })); await tick();
    assert.equal(h4.log.go[0].replace, true);
  }

  // ------------------------------------------------ integration: real engine + player adopting the prepared stream
  {
    const T = timers();
    const reports = [], goCalls = [], avOpens = [], listeners = [];
    let currentUrl = '';
    const av = {
      open(url) { currentUrl = url; avOpens.push(url); }, setDisplayRect() {}, setDisplayMethod() {},
      setListener(l) { listeners.push(l); },
      prepareAsync(ok, err) { if (currentUrl.includes('bad')) err('connection failed'); else ok(); },
      getDuration() { return 90000; }, play() {}, stop() {}, close() {}, seekTo() {}, pause() {}
    };
    const window = { navigator: { onLine: true }, DZ: {} };
    const DZ = window.DZ;
    const rootEl = el('div');
    const streams = [
      { url: 'https://v.example/bad.mp4', type: 'mp4', attempt_token: 'bad-token', kind: 'movie', label: 'A' },
      { url: 'https://v.example/good.mp4', type: 'mp4', attempt_token: 'good-token', kind: 'movie', label: 'B' }
    ];
    DZ.api = { profileId: () => 'p1', streams: () => Promise.resolve({ streams, duration: 90, resume_position: 12 }),
      playbackReport: p => { reports.push(p); return Promise.resolve({}); }, progress: () => Promise.resolve({}), detail: () => Promise.resolve({}) };
    let loadingCtl = null;
    DZ.modal = { loading(cfg) { loadingCtl = { cfg, closed: false, setStage() {}, close() { this.closed = true; } }; return loadingCtl; },
      open() { throw new Error('no dialog expected'); } };
    DZ.app = { go(name, params, replace) { goCalls.push({ name, params }); }, back() {} };
    const sandbox = { window, console: { log() {} }, webapis: { avplay: av }, setTimeout: T.setTimeout, clearTimeout: T.clearTimeout,
      setInterval: T.setInterval, clearInterval: T.clearInterval,
      document: { createElement: el, body: el('body'), documentElement: el('html') } };
    vm.createContext(sandbox);
    vm.runInContext(read('screens/player.js'), sandbox);
    vm.runInContext(read('playflow.js'), sandbox);
    DZ.playflow.start({ itemId: 'movie-1', title: 'Film', type: 'movie' });
    await tick(10);
    assert.equal(goCalls.length, 0, 'still waiting for the first real playback tick');
    assert.deepEqual(avOpens, ['https://v.example/bad.mp4', 'https://v.example/good.mp4']);
    assert.equal(reports.length, 1); assert.equal(reports[0].attempt_token, 'bad-token'); assert.equal(reports[0].event, 'failure');
    assert(!sandbox.document.documentElement.classList.contains('av-active'), 'probe must not make the app transparent');
    listeners[1].oncurrentplaytime(12000);   // playback really started
    assert.equal(goCalls.length, 1); assert.equal(goCalls[0].name, 'player');
    assert.deepEqual(reports[1], { attempt_token: 'good-token', event: 'success', code: '', engine: 'avplay' });

    // the player adopts the running engine: no new open(), status/labels clean
    const params = goCalls[0].params;
    const screen = DZ.screens.player;
    screen.enter(rootEl, params);
    assert.equal(avOpens.length, 2, 'adoption must not reopen the stream');
    assert(sandbox.document.documentElement.classList.contains('av-active'), 'punch-through on after the player opened');
    assert.equal(find(rootEl, 'pl-state').textContent, '', 'no transient status text after success');
    assert.equal(params.prepared, null, 'prepared handoff consumed (retry cannot reuse a dead engine)');
    for (let i = 13; i <= 30; i++) listeners[1].oncurrentplaytime(i * 1000);
    assert.equal(reports.filter(r => r.event === 'success').length, 1, 'success reported once (flow), not again by the player');
    assert.equal(reports.length, 2);
    listeners[0].onerror('stale error from the first stream');
    assert.equal(reports.length, 2);
    assert.equal(find(rootEl, 'pl-state').textContent, '');
    screen.exit();
    assert(!sandbox.document.documentElement.classList.contains('av-active'));
  }

  // ------------------------------------------------ embed player: the manual report label is not a permanent error tag
  {
    const T = timers();
    const window = { navigator: { onLine: true }, DZ: {} };
    const DZ = window.DZ;
    DZ.api = { profileId: () => 'p1', streams: () => Promise.resolve({ streams: [] }), playbackReport: () => Promise.resolve({}), progress: () => Promise.resolve({}), detail: () => Promise.resolve({}) };
    DZ.app = { go() {}, back() {} }; DZ.modal = { open() {} };
    const sandbox = { window, console: { log() {} }, setTimeout: T.setTimeout, clearTimeout: T.clearTimeout, setInterval: T.setInterval, clearInterval: T.clearInterval,
      document: { createElement: el, body: el('body'), documentElement: el('html') } };
    vm.createContext(sandbox);
    vm.runInContext(read('screens/player.js'), sandbox);
    const rootEl = el('div');
    const embed = { url: 'https://embed.example/t', type: 'embed', attempt_token: 'e-token', kind: 'trailer', label: 'Fragman' };
    DZ.screens.player.enter(rootEl, { itemId: 'x', kind: 'trailer', prepared: { streams: [embed], index: 0, tried: { 0: true }, reported: {}, engine: null, resume: 0, duration: 0 } });
    const labels = findAll(rootEl, 'pl-back');
    assert.equal(labels.length, 2, 'Geri + report button exist in embed mode');
    T.advance(3100);
    assert(labels.every(l => l.classList.contains('hidden')), 'both auto-hide; no "Kaynak çalışmıyor" tag stays over the video');
    assert(!labels.some(l => l.textContent === 'Kaynak çalışmıyor'), 'report button is not worded as an error');
    assert.equal(find(rootEl, 'pl-state').textContent, '');
    DZ.screens.player.exit();
  }

  console.log('Play flow: modal, ordered fallback, reports, cancel, timeouts, player adoption, no stale labels: OK');
})().catch(e => { console.error(e); process.exitCode = 1; });
