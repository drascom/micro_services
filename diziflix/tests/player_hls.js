// Browser (HTML5) HLS: engine/embed choice follows streams[].type (never the URL extension); hls.js is lazy-loaded ONLY for
// html5 + type "hls" + no native HLS; AVPlay (Tizen TV) never touches it; fatal hls.js errors become playback failure reports
// carrying `detail` (<=120 chars); type "embed" is unchanged. Real player.js + playflow.js (+ api.js for the report body) against doubles.
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, find, findAll, timers } = require('./_fake_dom');

const CLIENT = path.join(__dirname, '../tizen-client/js');
const read = f => fs.readFileSync(path.join(CLIENT, f), 'utf8');
const tick = async (n = 12) => { for (let i = 0; i < n; i++) await Promise.resolve(); };

const HLS_URL = 'https://cdn.test/play/master.txt';             // real HLS served with a .txt name
const MP4_PROXY = 'http://tv.test/api/stream-proxy/abc123';      // extensionless proxied mp4
const S = (extra) => Object.assign({ url: HLS_URL, type: 'hls', label: 'HLS', attempt_token: 'tok', kind: 'movie', source_id: 's1' }, extra || {});

// hls.js double: records everything, mirrors the public constants the client uses
function makeHlsClass(log) {
  class FakeHls {
    constructor(cfg) { this.cfg = cfg; this.handlers = {}; this.calls = []; this.destroyed = false; log.instances.push(this); }
    on(ev, fn) { (this.handlers[ev] = this.handlers[ev] || []).push(fn); }
    loadSource(u) { this.calls.push('loadSource'); this.url = u; }
    attachMedia(v) { this.calls.push('attachMedia'); this.media = v; }
    startLoad() { this.calls.push('startLoad'); }
    recoverMediaError() { this.calls.push('recoverMediaError'); }
    destroy() { this.destroyed = true; this.calls.push('destroy'); }
    emit(data) { for (const f of this.handlers[FakeHls.Events.ERROR] || []) f(FakeHls.Events.ERROR, data); }
    static isSupported() { return log.hlsSupported !== false; }
  }
  FakeHls.Events = { ERROR: 'hlsError', MANIFEST_PARSED: 'hlsManifestParsed' };
  FakeHls.ErrorTypes = { NETWORK_ERROR: 'networkError', MEDIA_ERROR: 'mediaError', MUX_ERROR: 'muxError', OTHER_ERROR: 'otherError' };
  return FakeHls;
}

function env(res, o) {
  o = o || {};
  const T = timers();
  const log = { reports: [], go: [], dialogs: [], loadings: [], scripts: [], instances: [], iframes: [], videos: [], avOpens: [], back: 0 };
  const window = { navigator: { onLine: true }, DZ: {}, localStorage: { getItem() { return null; }, setItem() {} } };
  const DZ = window.DZ;
  const FakeHls = makeHlsClass(log);
  if (o.hlsPreloaded) window.Hls = FakeHls;

  const head = el('head');
  const origAppend = head.appendChild.bind(head);
  head.appendChild = function (child) {
    origAppend(child);
    if (child.tagName === 'SCRIPT') {
      log.scripts.push(child);
      // the "network": install the global then fire onload (or onerror), asynchronously like a real <script>
      Promise.resolve().then(() => { if (o.scriptFail) child.onerror(); else { window.Hls = FakeHls; child.onload(); } });
    }
    return child;
  };
  function createElement(tag) {
    const n = el(tag);
    if (tag === 'video') {
      n.canPlayType = type => (type === 'application/vnd.apple.mpegurl' ? (o.native ? 'maybe' : '') : 'maybe');
      n.play = () => Promise.resolve();
      n.pause = () => {}; n.load = () => { n.loadCalls = (n.loadCalls || 0) + 1; };
      n.currentTime = 0; n.duration = 120; n.error = null;
      log.videos.push(n);
    }
    if (tag === 'iframe') log.iframes.push(n);
    return n;
  }
  const av = { open(u) { log.avOpens.push(u); }, setDisplayRect() {}, setDisplayMethod() {}, setListener(l) { av.listener = l; },
    prepareAsync(ok) { ok(); }, getDuration() { return 120000; }, getCurrentTime() { return 0; }, play() {}, pause() {}, stop() {}, close() {}, seekTo() {} };

  DZ.api = { profileId: () => 'p1', detail: () => Promise.resolve({}), progress: () => Promise.resolve({}),
    streams: () => Promise.resolve(res), playbackReport: p => { log.reports.push(p); return Promise.resolve({}); } };
  DZ.modal = { open(cfg) { log.dialogs.push(cfg); }, loading(cfg) { const c = { cfg, closed: false, setStage() {}, close() { c.closed = true; } }; log.loadings.push(c); return c; },
    isOpen: () => true, close() {} };
  DZ.app = { go(name, params, replace) { log.go.push({ name, params, replace }); }, back() { log.back++; } };
  const sandbox = { window, console: { log() {} }, setTimeout: T.setTimeout, clearTimeout: T.clearTimeout, setInterval: T.setInterval, clearInterval: T.clearInterval,
    document: { createElement, body: el('body'), documentElement: el('html'), head } };
  if (o.avplay) sandbox.webapis = { avplay: av };
  vm.createContext(sandbox);
  for (const f of ['screens/player.js', 'playflow.js']) vm.runInContext(read(f), sandbox);
  const root = el('div');
  return { T, log, DZ, window, root, av, FakeHls, sandbox,
    enter(params) { DZ.screens.player.enter(root, params || { itemId: 'm1' }); return tick(); },
    video: () => log.videos[log.videos.length - 1],
    hls: () => log.instances[log.instances.length - 1] };
}

(async () => {
  // (4) embed decision follows `type`, not the URL extension
  {
    const e = env({ streams: [] });
    const is = e.DZ.player.isEmbedStream;
    assert.equal(is({ type: 'mp4', url: MP4_PROXY }), false, 'extensionless proxied mp4 is NOT an embed');
    assert.equal(is({ type: 'hls', url: HLS_URL }), false, '.txt HLS is NOT an embed');
    assert.equal(is({ type: 'hls', url: 'http://tv.test/api/stream-proxy/tok/index.m3u8' }), false);
    assert.equal(is({ type: 'embed', url: 'https://cdn.test/video.mp4' }), true, 'type=embed wins over a .mp4 URL');
    assert.equal(is({ type: 'EMBED', url: 'https://x/y' }), true);
    // extension is only the fallback when `type` is missing
    assert.equal(is({ url: 'https://x/a.m3u8?sig=1#t' }), false);
    assert.equal(is({ url: 'https://x/a.mp4' }), false);
    assert.equal(is({ url: 'https://x/embed/xyz' }), true);
    assert.equal(is({ type: '', url: 'https://x/embed/xyz' }), true);
    assert.equal(is(null), false);
    // playflow ordering uses the same decision: the .txt HLS + extensionless mp4 stay direct (ahead of the embed)
    const ordered = e.DZ.playflow.orderStreams([{ type: 'embed', url: 'https://e/1' }, { type: 'hls', url: HLS_URL }, { type: 'mp4', url: MP4_PROXY }], 0);
    assert.deepEqual(ordered.map(s => s.type), ['hls', 'mp4', 'embed']);
    assert.equal(e.DZ.player.isHlsStream('hls', 'x.mp4'), true);
    assert.equal(e.DZ.player.isHlsStream('mp4', 'x.m3u8'), false, 'type wins');
    assert.equal(e.DZ.player.isHlsStream('', 'x.m3u8'), true, 'extension only as fallback');
  }

  // (1) html5 + type hls + no native support -> hls.js is lazy-loaded ONCE and drives the <video>
  {
    const e = env({ streams: [S()], resume_position: 33, duration: 120 });
    assert.equal(e.window.Hls, undefined, 'hls.js is not preloaded');
    await e.enter();
    assert.equal(e.log.scripts.length, 1, 'one lazy <script>');
    assert(/js\/vendor\/hls\.min\.js/.test(e.log.scripts[0].src), 'vendored file');
    assert.equal(e.log.instances.length, 1, 'hls.js instance created after the script loaded');
    const h = e.hls(), v = e.video();
    assert.equal(h.url, HLS_URL, 'loadSource(url)');
    assert(h.calls.includes('loadSource') && h.calls.includes('attachMedia'));
    assert.strictEqual(h.media, v, 'attached to the <video>');
    assert.equal(h.cfg.startPosition, 33, 'resume position handed to hls.js');
    assert(!v.src, 'native src NOT set (hls.js feeds the element via MSE)');
    assert.equal(e.log.iframes.length, 0, 'not an iframe');
    // manifest parsed -> the element fires loadedmetadata -> the normal engine path starts playback
    v.listeners.loadedmetadata();
    assert.equal(v.currentTime, 33, 'start() seeks to the resume position');
    // a second HLS play reuses the loaded script (loaded once)
    e.DZ.screens.player.exit();
    assert(h.destroyed, 'leaving the player destroys the hls.js instance');
    await e.enter();
    assert.equal(e.log.scripts.length, 1, 'script appended only once');
    assert.equal(e.log.instances.length, 2);
    e.DZ.screens.player.exit();
  }

  // (1b) the loader failing (script error) is a normal playback failure with detail
  {
    const e = env({ streams: [S()] }, { scriptFail: true });
    await e.enter();
    assert.equal(e.log.instances.length, 0);
    assert.equal(e.log.reports.length, 1);
    assert.deepEqual(e.log.reports[0], { attempt_token: 'tok', event: 'failure', code: 'playback_failed', engine: 'html5', detail: 'hls.js:load' });
    e.DZ.screens.player.exit();
  }

  // (1c) no MSE (Hls.isSupported() false) -> falls back to the native path instead of a hls.js instance
  {
    const e = env({ streams: [S()] }, { hlsPreloaded: true });
    e.log.hlsSupported = false;
    await e.enter();
    assert.equal(e.log.instances.length, 0);
    assert.equal(e.video().src, HLS_URL);
    e.DZ.screens.player.exit();
  }

  // (2) native HLS support (Safari etc.) -> hls.js is never loaded, the element plays the URL itself
  {
    const e = env({ streams: [S()] }, { native: true });
    await e.enter();
    assert.equal(e.log.scripts.length, 0, 'no <script> when the browser plays HLS natively');
    assert.equal(e.log.instances.length, 0);
    assert.equal(e.video().src, HLS_URL);
    e.DZ.screens.player.exit();
  }

  // (2b) mp4 (even extensionless) never loads hls.js and is not an iframe
  {
    const e = env({ streams: [S({ type: 'mp4', url: MP4_PROXY, proxied: true })] });
    await e.enter();
    assert.equal(e.log.scripts.length, 0);
    assert.equal(e.log.iframes.length, 0, 'extensionless mp4 proxy URL must not become an iframe');
    assert.equal(e.video().src, MP4_PROXY);
    // a native <video> error carries video.error.code in the report detail
    e.video().error = { code: 4 };
    e.video().listeners.error();
    assert.deepEqual(e.log.reports[0], { attempt_token: 'tok', event: 'failure', code: 'playback_failed', engine: 'html5', detail: 'video.error.code=4' });
    e.DZ.screens.player.exit();
  }

  // (3) AVPlay (Tizen TV): hls.js is never loaded or touched, AVPlay opens the URL (whatever the extension)
  {
    const e = env({ streams: [S()] }, { avplay: true, hlsPreloaded: true });
    await e.enter();
    assert.deepEqual(e.log.avOpens, [HLS_URL]);
    assert.equal(e.log.scripts.length, 0);
    assert.equal(e.log.instances.length, 0, 'AVPlay path does not construct hls.js');
    assert.equal(e.log.videos.length, 0, 'no <video> element');
    // an AVPlay failure still reports (engine avplay) with its message as detail
    e.av.listener.onerror('PLAYER_ERROR_CONNECTION_FAILED');
    assert.equal(e.log.reports[0].engine, 'avplay');
    assert.equal(e.log.reports[0].detail, 'avplay:PLAYER_ERROR_CONNECTION_FAILED');
    e.DZ.screens.player.exit();
  }

  // (5) fatal hls.js errors -> playback_failed report with detail; recoveries happen once; non-fatal ignored
  {
    // fatal manifest load error: no retry (hls.js already retried), straight to the failure report
    const e = env({ streams: [S()] });
    await e.enter();
    const h = e.hls();
    h.emit({ fatal: false, type: 'networkError', details: 'fragLoadError' });
    assert.equal(e.log.reports.length, 0, 'non-fatal errors are left to hls.js');
    assert.deepEqual(h.calls.filter(c => c !== 'loadSource' && c !== 'attachMedia'), []);
    h.emit({ fatal: true, type: 'networkError', details: 'manifestLoadError', response: { code: 403 } });
    assert.equal(e.log.reports.length, 1);
    assert.deepEqual(e.log.reports[0], { attempt_token: 'tok', event: 'failure', code: 'network', engine: 'html5', detail: 'hls:networkError/manifestLoadError/403' });
    assert(h.destroyed, 'the failed hls.js instance is destroyed');
    assert.equal(e.log.dialogs.length, 1, 'only stream -> "Oynatilamadi" dialog (existing failure flow)');
    e.DZ.screens.player.exit();
  }
  {
    // fatal network error mid-stream: startLoad once, then the second fatal one fails
    const e = env({ streams: [S()] });
    await e.enter();
    const h = e.hls();
    h.emit({ fatal: true, type: 'networkError', details: 'fragLoadError' });
    assert(h.calls.includes('startLoad'), 'first fatal network error -> startLoad()');
    assert.equal(e.log.reports.length, 0);
    h.emit({ fatal: true, type: 'networkError', details: 'fragLoadError' });
    assert.equal(h.calls.filter(c => c === 'startLoad').length, 1, 'startLoad only once');
    assert.equal(e.log.reports.length, 1);
    assert.equal(e.log.reports[0].detail, 'hls:networkError/fragLoadError');
    e.DZ.screens.player.exit();
  }
  {
    // fatal media error: recoverMediaError once, then failure with code decode; the <video> decode error is recovered once too
    const e = env({ streams: [S()] });
    await e.enter();
    const h = e.hls();
    h.emit({ fatal: true, type: 'mediaError', details: 'bufferAppendError' });
    assert.equal(h.calls.filter(c => c === 'recoverMediaError').length, 1);
    assert.equal(e.log.reports.length, 0);
    e.video().error = { code: 3 };
    e.video().listeners.error();
    assert.equal(h.calls.filter(c => c === 'recoverMediaError').length, 1, 'video decode error does not recover twice');
    assert.equal(e.log.reports.length, 1, 'second media failure is reported');
    assert.equal(e.log.reports[0].code, 'decode');
    assert.equal(e.log.reports[0].detail, 'video.error.code=3');
    e.DZ.screens.player.exit();
  }
  {
    // unknown fatal error + very long details are clipped to 120
    const e = env({ streams: [S()] });
    await e.enter();
    e.hls().emit({ fatal: true, type: 'otherError', details: 'x'.repeat(300) });
    assert.equal(e.log.reports[0].code, 'playback_failed');
    assert.equal(e.log.reports[0].detail.length, 120);
    assert(e.log.reports[0].detail.startsWith('hls:otherError/xxx'));
    e.DZ.screens.player.exit();
  }

  // (5b) the loading flow (playflow): type reaches the engine, a fatal hls.js error is reported with detail, success carries none
  {
    const e = env({ streams: [S(), S({ type: 'mp4', url: MP4_PROXY, attempt_token: 'tok2' })], resume_position: 0, duration: 90 });
    e.DZ.playflow.start({ itemId: 'm1', title: 'Film', type: 'movie', kind: 'video' });
    await tick();
    assert.equal(e.log.scripts.length, 1, 'playflow hands the type to the html5 engine -> hls.js');
    const h = e.hls();
    assert.equal(h.url, HLS_URL);
    h.emit({ fatal: true, type: 'networkError', details: 'manifestLoadTimeOut' });
    await tick();
    assert.deepEqual(e.log.reports[0], { attempt_token: 'tok', event: 'failure', code: 'network', engine: 'html5', detail: 'hls:networkError/manifestLoadTimeOut' });
    // next stream (mp4, extensionless) is tried natively under the modal
    const v2 = e.log.videos[e.log.videos.length - 1];
    assert.equal(v2.src, MP4_PROXY);
    v2.listeners.loadedmetadata();
    await tick();
    v2.listeners.playing();
    await tick();
    const ok = e.log.reports.find(r => r.event === 'success');
    assert.deepEqual(ok, { attempt_token: 'tok2', event: 'success', code: '', engine: 'html5' }, 'success report has no detail');
    assert.equal(e.log.go.length, 1);
    assert.equal(e.log.go[0].name, 'player');
  }

  // (6) type "embed" is unchanged: iframe, no <video>, no hls.js
  {
    const e = env({ streams: [S({ type: 'embed', url: 'https://embed.test/v/abc', attempt_token: 'tok-e' })] });
    await e.enter();
    assert.equal(e.log.iframes.length, 1);
    assert(/^https:\/\/embed\.test\/v\/abc\?autoplay=true$/.test(e.log.iframes[0].src), 'autoplay added to the embed URL');
    assert.equal(e.log.videos.length, 0);
    assert.equal(e.log.scripts.length, 0);
    assert.equal(e.log.instances.length, 0);
    e.DZ.screens.player.key({ name: 'red' });   // the manual "Sorun bildir" key
    assert.deepEqual(e.log.reports[0], { attempt_token: 'tok-e', event: 'failure', code: 'playback_failed', engine: 'embed', detail: 'user-report' });
    e.DZ.screens.player.exit();
  }

  // api.js: detail is clipped to 120 and dropped when empty (old servers ignore an unknown field anyway)
  {
    const bodies = [];
    const window = { location: { search: '' }, localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} }, setTimeout, clearTimeout };
    const sandbox = { window, console: { log() {} }, setTimeout, clearTimeout, encodeURIComponent, decodeURIComponent,
      fetch: (u, init) => { bodies.push({ u, body: JSON.parse(init.body) }); return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve('{}') }); } };
    vm.createContext(sandbox);
    vm.runInContext(read('api.js'), sandbox);
    const api = window.DZ.api;
    await api.playbackReport({ attempt_token: 't', event: 'failure', code: 'network', engine: 'html5', detail: 'y'.repeat(500) });
    await api.playbackReport({ attempt_token: 't', event: 'success', code: '', engine: 'html5', detail: '' });
    await api.playbackReport({ attempt_token: 't', event: 'failure', code: 'network', engine: 'html5' });
    assert(/\/api\/playback-report$/.test(bodies[0].u));
    assert.equal(bodies[0].body.detail.length, 120);
    assert(!('detail' in bodies[1].body) && !('detail' in bodies[2].body), 'empty/absent detail is not sent');
  }

  // the vendored file is the pinned release (sha256 in js/vendor/README.md) and exports window.Hls
  {
    const crypto = require('crypto');
    const file = fs.readFileSync(path.join(CLIENT, 'vendor/hls.min.js'));
    const sha = crypto.createHash('sha256').update(file).digest('hex');
    assert(fs.readFileSync(path.join(CLIENT, 'vendor/README.md'), 'utf8').includes(sha), 'vendor README lists the file sha256');
    assert(fs.existsSync(path.join(CLIENT, 'vendor/hls.js.LICENSE')), 'license notice shipped');
  }

  console.log('HLS in the browser client (type-driven engine choice, lazy hls.js, AVPlay untouched, failure detail, embed unchanged): OK');
})().catch(e => { console.error(e); process.exitCode = 1; });
