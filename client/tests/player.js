// client player (js/player.js) headless test: engine choice (hls -> hls.js in a desktop browser, AVPlay when webapis.avplay
// exists), fallback to the next stream on failure (+ playback-report), progress POSTs, Back exits, all-failed -> finder screen.
// Real api.js / tracks.js / subs.js / player.js in a vm against a fake DOM, fake timers and a FAKE XMLHttpRequest (no server, no DB).
// Run: node client/tests/player.js
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, findAll, timers } = require('./_fake_dom');

const JS = path.join(__dirname, '../js');
const read = f => fs.readFileSync(path.join(JS, f), 'utf8');
const tick = async (n = 20) => { for (let i = 0; i < n; i++) await Promise.resolve(); };
const BASE = 'http://tv.test:8090';
const CHROME = 'Mozilla/5.0 (Macintosh) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36';

function makeHls(log) {
  class FakeHls {
    constructor(cfg) { this.cfg = cfg; this.handlers = {}; this.calls = []; log.hls.push(this); }
    on(ev, fn) { (this.handlers[ev] = this.handlers[ev] || []).push(fn); }
    loadSource(u) { this.url = u; this.calls.push('loadSource'); }
    attachMedia(v) { this.media = v; this.calls.push('attachMedia'); }
    startLoad() {} recoverMediaError() {} destroy() { this.destroyed = true; }
    static isSupported() { return !log.hlsUnsupported; }
  }
  FakeHls.Events = { ERROR: 'hlsError' };
  FakeHls.ErrorTypes = { NETWORK_ERROR: 'networkError', MEDIA_ERROR: 'mediaError' };
  return FakeHls;
}

function env(routes, o) {
  o = o || {};
  const T = timers();
  const log = { xhr: [], hls: [], scripts: [], videos: [], av: [], exits: [], veil: [], hlsUnsupported: !!o.hlsUnsupported };
  const sb = {};
  sb.window = sb;
  sb.console = { log() {} };
  const RealDate = Date, D0 = RealDate.UTC(2026, 9, 24, 12);
  class FakeDate extends RealDate { constructor(...a) { if (a.length) super(...a); else super(D0 + T.now()); } static now() { return D0 + T.now(); } }
  sb.Date = FakeDate;
  Object.assign(sb, { setTimeout: T.setTimeout, clearTimeout: T.clearTimeout, setInterval: T.setInterval, clearInterval: T.clearInterval, Promise });
  sb.navigator = { onLine: true, userAgent: o.ua || CHROME };
  sb.MediaSource = function () {};
  sb.location = { search: '', protocol: 'http:', hostname: 'localhost', port: '8001', host: 'localhost:8001', href: 'http://localhost:8001/' };
  const ls = { 'dzproto.server': BASE };
  if (o.engine) ls['dzproto.engine'] = o.engine;
  if (o.debug) ls['dzproto.debug'] = o.debug === true ? '1' : o.debug;   // true = eski '1' kaydi (-> Ozet)
  sb.localStorage = { getItem: k => (k in ls ? ls[k] : null), setItem: (k, v) => { ls[k] = String(v); }, removeItem: k => { delete ls[k]; } };
  sb.fetch = () => Promise.reject(new Error('no fetch in test'));

  function mk(tag) {
    const n = el(tag);
    let html = '';
    Object.defineProperty(n, 'innerHTML', { get: () => html, set: v => { html = v; n.children.slice().forEach(c => n.removeChild(c)); } });
    if (tag === 'video') {
      n.canPlayType = t => (t === 'application/vnd.apple.mpegurl' ? 'maybe' : 'maybe');
      n.play = () => { n.paused = false; return Promise.resolve(); };
      n.pause = () => { n.paused = true; };
      n.load = () => {};
      n.currentTime = 0; n.duration = 0; n.error = null;
      log.videos.push(n);
    }
    return n;
  }
  const head = mk('head');
  const append = head.appendChild.bind(head);
  head.appendChild = child => {
    append(child);
    if (child.tagName === 'SCRIPT') { log.scripts.push(child.src); Promise.resolve().then(() => { sb.Hls = makeHls(log); child.onload(); }); }
    return child;
  };
  sb.document = { createElement: mk, head, body: mk('body'), documentElement: mk('html') };

  if (o.avplay) {
    const av = { open(u) { log.av.push(['open', u]); }, setDisplayRect() {}, setDisplayMethod() {}, setListener(l) { av.l = l; },
      prepareAsync(ok, err) { av.ok = ok; av.err = err; }, getDuration: () => 3000000, getCurrentTime: () => 0, play() { log.av.push(['play']); },
      pause() {}, stop() {}, close() {}, seekTo(ms) { log.av.push(['seek', ms]); } };
    sb.webapis = { avplay: av, productinfo: { getModel: () => 'UE55TEST', getFirmware: () => 'T-FW-1' } };
    log.avObj = av;
  }

  // fake XHR: routes = [[method, regex, (body, url) => response | {status, body}]]
  sb.XMLHttpRequest = function () {
    const x = this;
    x.readyState = 0; x.status = 0; x.responseText = '';
    x.open = (m, u) => { x.m = m; x.u = u; };
    x.setRequestHeader = () => {};
    x.getResponseHeader = () => null;
    x.send = body => {
      const b = body ? JSON.parse(body) : null;
      log.xhr.push({ m: x.m, u: x.u, body: b });
      const r = routes.find(r => r[0] === x.m && r[1].test(x.u));
      let res = r ? r[2](b, x.u) : { status: 404, body: { error: { code: 'nf', message: 'nf' } } };
      if (!res || res.status === undefined) res = { status: 200, body: res || { ok: true } };
      Promise.resolve().then(() => { x.readyState = 4; x.status = res.status; x.responseText = JSON.stringify(res.body); x.onreadystatechange(); });
    };
  };

  vm.createContext(sb);
  for (const f of ['debug.js', 'api.js', 'tracks.js', 'subs.js', 'player.js']) vm.runInContext(read(f), sb, { filename: f });
  const stage = mk('div'), veil = mk('div');
  stage.appendChild(veil);
  const P = sb.DZP.player;
  return {
    T, log, sb, P, stage,
    open(extra) {
      P.open(Object.assign({ stage, before: veil, pid: 'p1', itemId: 'm1', title: 'Film', type: 'movie',
        veil: (t, s) => log.veil.push(s), unveil: () => log.veil.push('-'), onExit: i => log.exits.push(i) }, extra || {}));
      return tick();
    },
    video: () => log.videos[log.videos.length - 1],
    calls: (m, re) => log.xhr.filter(x => x.m === m && re.test(x.u)),
    st: () => P._.state()
  };
}

const S = (i, extra) => Object.assign({ url: 'https://cdn.test/v' + i + '.mp4', type: 'mp4', label: 'Kaynak ' + i, attempt_token: 'tok' + i,
  variant_id: 'v' + i, audio_lang: null, sub_mode: 'none', hard_lang: null }, extra || {});
const streamsRoute = res => ['GET', /\/api\/streams\//, () => res];
const okRoutes = [['POST', /\/api\/progress/, () => ({ ok: true })], ['POST', /\/api\/playback-report/, () => ({ ok: true })]];

(async () => {
  // (1) desktop Chrome + type=hls -> hls.js lazy-loaded once and drives the <video> (resume handed over)
  {
    const e = env([streamsRoute({ streams: [S(1, { type: 'hls', url: 'https://cdn.test/master.txt' })], resume_position: 33, duration: 2700 }), ...okRoutes]);
    await e.open();
    assert.equal(e.calls('GET', /\/api\/streams\/m1\?profile=p1/).length, 1, 'streams requested with profile');
    assert.equal(e.log.scripts.length, 1, 'hls.js script loaded');
    assert(/js\/vendor\/hls\.min\.js/.test(e.log.scripts[0]));
    const h = e.log.hls[0];
    assert.equal(h.url, 'https://cdn.test/master.txt', 'hls.js loadSource(url) despite .txt name (type decides)');
    assert.strictEqual(h.media, e.video(), 'attached to the <video>');
    assert.equal(h.cfg.startPosition, 33, 'resume position');
    assert.equal(e.P._.preferHlsJs(), true);
    e.P.close();
  }
  // (1b) Safari -> no hls.js (native HLS), mp4 never loads hls.js
  {
    const e = env([streamsRoute({ streams: [S(1, { type: 'hls', url: 'https://cdn.test/a.m3u8' })] }), ...okRoutes],
      { ua: 'Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15' });
    await e.open();
    assert.equal(e.log.scripts.length, 0, 'Safari plays HLS natively');
    assert.equal(e.video().src, 'https://cdn.test/a.m3u8');
    e.P.close();
  }
  // (2) AVPlay present -> AVPlay opens the stream (no <video>, no hls.js), punch-through class on <html>
  {
    const e = env([streamsRoute({ streams: [S(1, { type: 'hls', url: 'https://cdn.test/a.m3u8' })], resume_position: 100 }), ...okRoutes], { avplay: true, engine: 'avplay' });
    await e.open();
    assert.deepEqual(e.log.av[0], ['open', 'https://cdn.test/a.m3u8']);
    assert.equal(e.log.scripts.length, 0, 'AVPlay never touches hls.js');
    assert.equal(e.log.videos.length, 0, 'no <video>');
    assert(e.sb.document.documentElement.classList.contains('av-active'));
    e.log.avObj.ok();                                    // prepared -> seek to resume + play
    assert(e.log.av.some(a => a[0] === 'seek' && a[1] === 100000), 'resume seek');
    assert(e.log.av.some(a => a[0] === 'play'));
    e.P.close();
    assert(!e.sb.document.documentElement.classList.contains('av-active'), 'punch-through removed on exit');
  }
  // (2b) OK.ru-like proxied mp4 (proxy URL, no extension) on AVPlay -> AVPlay opens the proxy URL as-is (type decides, no hls.js)
  {
    const px = 'http://192.168.1.5:8090/api/stream-proxy/eyJ1IjoiaHR0cHM6Ly92ZDEub2tjZG4ucnUvIn0.sig';
    const e = env([streamsRoute({ streams: [S(1, { type: 'mp4', url: px, provider: 'OK.ru', proxied: true, proxy_reason: 'ua' })] }), ...okRoutes], { avplay: true, engine: 'avplay' });
    await e.open();
    assert.deepEqual(e.log.av[0], ['open', px], 'proxied mp4 handed to AVPlay unchanged');
    assert.equal(e.log.scripts.length, 0, 'mp4 never loads hls.js');
    assert.equal(e.log.videos.length, 0, 'no <video> fallback');
    e.P.close();
  }
  // (3) first stream fails -> failure report (token, code, detail, engine) + next stream; >=10 s played -> success report
  {
    const e = env([streamsRoute({ streams: [S(1), S(2)], duration: 2700 }), ...okRoutes]);
    await e.open();
    const v1 = e.video();
    assert.equal(v1.src, 'https://cdn.test/v1.mp4');
    v1.error = { code: 4, message: 'MEDIA_ERR_SRC_NOT_SUPPORTED' };
    v1.listeners.error();
    await tick();
    const fail = e.calls('POST', /\/api\/playback-report\?profile=p1/)[0];
    assert(fail, 'failure reported');
    assert.equal(fail.body.attempt_token, 'tok1');
    assert.equal(fail.body.event, 'failure');
    assert.equal(fail.body.engine, 'native');
    assert(/video\.error\.code=4/.test(fail.body.detail));
    const v2 = e.video();
    assert.notStrictEqual(v2, v1, 'new element for the next stream');
    assert.equal(v2.src, 'https://cdn.test/v2.mp4', 'fallback to stream 2');
    // play stream 2: metadata -> start -> playing -> time advances
    v2.duration = 2700; v2.listeners.loadedmetadata(); v2.listeners.playing();
    for (let t = 1; t <= 12; t++) { v2.currentTime = t; v2.listeners.timeupdate(); }
    await tick();
    const succ = e.calls('POST', /\/api\/playback-report/).filter(x => x.body.event === 'success');
    assert.equal(succ.length, 1, 'success reported once');
    assert.equal(succ[0].body.attempt_token, 'tok2');
    assert.equal(e.log.veil[e.log.veil.length - 1], '-', 'loading veil closed once playing');

    // (4) progress: periodic (20 s) + on pause (OK) + on exit (Back)
    v2.currentTime = 40; v2.listeners.timeupdate();
    e.T.advance(20000);
    await tick();
    let pr = e.calls('POST', /\/api\/progress/);
    assert.equal(pr.length, 1, 'periodic progress');
    assert.deepEqual(pr[0].body, { profile: 'p1', item_id: 'm1', episode_id: 'm1', position: 40, duration: 2700 });
    e.P.key('ok');                                       // UI hidden -> OK = pause
    await tick();
    assert(e.st().eng.playing === false, 'paused');
    pr = e.calls('POST', /\/api\/progress/);
    assert.equal(pr.length, 2, 'progress on pause');

    // (5) Back: first closes the visible overlay, second exits (progress + onExit, DOM removed)
    assert.equal(e.st().uiOn, true);
    e.P.key('back');
    assert.equal(e.st().uiOn, false, 'Back hides the overlay');
    v2.currentTime = 55; v2.listeners.timeupdate();
    e.P.key('back');
    await tick();
    assert.equal(e.log.exits.length, 1, 'Back exits the player');
    assert.equal(e.log.exits[0].reason, 'back');
    const last = e.calls('POST', /\/api\/progress/).pop();
    assert.equal(last.body.position, 55, 'progress on exit');
    assert.equal(e.P.active(), false);
    assert.equal(findAll(e.stage, 'pl').length, 0, 'player DOM removed');
  }
  // (6) every stream fails + report says the finder started -> "Kaynak aranıyor" screen, poll found -> streams re-requested
  {
    let finderState = 'searching';
    const e = env([streamsRoute({ streams: [S(1)] }), ['POST', /\/api\/progress/, () => ({ ok: true })],
      ['POST', /\/api\/playback-report/, () => ({ ok: true, finder: { state: 'searching' } })],
      ['GET', /\/api\/source-finder\/s1\?episode=s1%3As1%3Ae2/, () => ({ state: finderState })]]);
    await e.open({ itemId: 's1', type: 'series', episodeId: 's1:s1:e2', title: 'Dizi' });
    e.T.advance(20000);                                  // start timeout
    await tick();
    const st = e.st();
    assert(st.err && st.err.searching, 'finder screen');
    assert(/Kaynak aranıyor/.test(JSON.stringify(st.ui.err.children.map(c => c.textContent))));
    finderState = 'found';
    e.T.advance(5000);
    await tick();
    assert.equal(e.calls('GET', /\/api\/streams\/s1/).length, 2, 'found -> streams requested again');
    assert(e.calls('GET', /\/api\/streams\/s1\?profile=p1&episode=s1%3As1%3Ae2/).length === 2, 'episode param kept');
    assert(!e.st().err, 'error screen closed');
    e.P.key('back');
    assert.equal(e.log.exits.length, 1, 'Back during loading exits');
  }
  // (7) no streams + no finder -> error screen; OK on "Geri" exits
  {
    const e = env([streamsRoute({ streams: [] }), ...okRoutes]);
    await e.open();
    assert(e.st().err, 'error screen');
    e.P.key('ok');
    assert.equal(e.log.exits.length, 1);
  }
  // ---- Canli TV (kanal modu) ----
  const CH = n => ({ id: 'ch_' + n, type: 'channel', card_kind: 'channel', title: 'Kanal ' + n, channel_no: n, categories: ['news'] });
  const liveRes = (id, extra) => Object.assign({ live: true, channel: { id, title: 'Kanal ' + id.slice(3), channel_no: +id.slice(3) },
    streams: [S(1, { url: 'https://cdn.test/' + id + '.m3u8', type: 'mp4', label: 'cdn.test · canlı', attempt_token: 't_' + id })],
    resume_position: 0, duration: null }, extra || {});
  const liveRoutes = (over) => [
    ['GET', /\/api\/streams\//, (b, u) => { const id = /streams\/([^?]+)/.exec(u)[1]; return (over && over(id)) || liveRes(id); }],
    ['GET', /\/api\/row\/live_all/, (b, u) => {
      const off = +/offset=(\d+)/.exec(u)[1];
      return { items: [CH(4), CH(5)].filter(c => c.channel_no > off), total: 5 };
    }],
    ...okRoutes];
  const streamIds = e => e.calls('GET', /\/api\/streams\//).map(x => /streams\/([^?]+)/.exec(x.u)[1]);
  const startPlaying = e => { const v = e.video(); v.duration = Infinity; v.listeners.loadedmetadata(); v.listeners.playing();
    for (let t = 1; t <= 3; t++) { v.currentTime = t; v.listeners.timeupdate(); } };
  // (8) live response: CANLI header, no seek buttons, no progress; ↑↑ fast -> debounce opens only the last one
  {
    const row = { id: 'live_all', items: [CH(1), CH(2), CH(3)], total: 5 };
    const e = env(liveRoutes());
    await e.open({ itemId: 'ch_1', title: 'Kanal 1', type: 'channel', live: true, channels: row, channelNo: 1 });
    startPlaying(e);
    const st = e.st();
    assert.equal(st.live, true);
    assert(/CANLI/.test(JSON.stringify(st.ui.ep.children.map(c => c.textContent))), 'CANLI badge');
    assert.equal(st.ui.title.textContent, '1 · Kanal 1');
    assert(!st.btnDefs.some(d => d.id === 'rw' || d.id === 'ff'), 'no ±10 in live');
    assert(st.btnDefs.some(d => d.id === 'channels'), 'Kanallar button');
    e.video().currentTime = 40; e.video().listeners.timeupdate();
    e.T.advance(25000); await tick();
    e.P.key('right'); e.P.key('left');                   // no seek
    e.T.advance(1000); await tick();
    assert.equal(e.calls('POST', /\/api\/progress/).length, 0, 'no progress POST for channels');
    e.P.key('up'); e.T.advance(100); e.P.key('up');      // ↑ = next (TV habit, = ChannelUp)
    assert.equal(st.ui.band.className, 'pl-band pl-glass on', 'band shown');
    assert(/Kanal 3/.test(JSON.stringify(st.ui.band.children.map(c => c.textContent))), 'band shows the target');
    e.T.advance(399); await tick();
    assert.deepEqual(streamIds(e), ['ch_1'], 'debounced: nothing opened yet');
    e.T.advance(1); await tick();
    assert.deepEqual(streamIds(e), ['ch_1', 'ch_3'], 'only the last selection opened');
    assert.equal(e.st().itemId, 'ch_3');
    // (9) end of loaded list -> /api/row continues (offset 3), then wrap to the first; ↓ at first wraps to the last
    e.P.key('up'); await tick();
    assert.equal(e.calls('GET', /\/api\/row\/live_all\?profile=p1&offset=3/).length, 1, 'row continued from offset 3');
    assert.equal(row.items.length, 5, 'shared row object extended');
    e.T.advance(400); await tick();
    assert.equal(e.st().itemId, 'ch_4');
    e.P.key('chup'); e.T.advance(400); await tick();      // ChannelUp = next
    assert.equal(e.st().itemId, 'ch_5');
    e.P.key('up'); e.T.advance(400); await tick();
    assert.equal(e.st().itemId, 'ch_1', 'wraps to the first');
    e.P.key('down'); e.T.advance(400); await tick();     // ↓ = previous (= ChannelDown)
    assert.equal(e.st().itemId, 'ch_5', 'wraps to the last');
    // (9b) digits: 2 -> after 1.5 s channel_no 2
    e.P.key('d2'); e.T.advance(1499); await tick();
    assert.equal(e.st().itemId, 'ch_5');
    e.T.advance(1); await tick();
    assert.equal(e.st().itemId, 'ch_2', 'number entry');
    // (9c) OK (overlay hidden) = channel list panel; ↓ OK opens the next channel
    startPlaying(e);
    assert.equal(e.st().uiOn, true);
    e.P.key('back');                                     // hides the overlay
    assert.equal(e.st().uiOn, false);
    e.P.key('ok');
    assert.equal(e.st().panel && e.st().panel.kind, 'channels', 'channel panel');
    assert.equal(e.st().panel.idx, 1, 'focus on the playing channel');
    e.P.key('down'); e.P.key('ok'); await tick();
    assert.equal(e.st().itemId, 'ch_3', 'panel OK switches');
    assert(!e.st().panel);
    e.P.close();
    assert.equal(e.calls('POST', /\/api\/progress/).length, 0, 'never any progress');
  }
  // (10) every stream fails -> "Yayın yok" at once (no finder wait), channel switching still works; Back exits with the last channel
  {
    const row = { id: 'live_news', items: [CH(1), CH(2)], total: 2 };
    const e = env(liveRoutes(id => (id === 'ch_1' ? liveRes(id, { streams: [S(1, { attempt_token: 'a1' }), S(2, { attempt_token: 'a2' })] }) : null)));
    await e.open({ itemId: 'ch_1', title: 'Kanal 1', type: 'channel', live: true, channels: row });
    let v = e.video(); v.error = { code: 4 }; v.listeners.error(); await tick();
    v = e.video(); v.error = { code: 4 }; v.listeners.error(); await tick();
    assert.equal(e.calls('POST', /\/api\/playback-report/).filter(x => x.body.event === 'failure').length, 2, 'both failures reported');
    const st = e.st();
    assert(st.err, 'error screen');
    assert(/Yayın yok/.test(JSON.stringify(st.ui.err.children.map(c => c.textContent))), '"Yayın yok"');
    e.P.key('up'); e.T.advance(400); await tick();
    assert(!e.st().err, 'error closed by channel switch');
    assert.equal(e.st().itemId, 'ch_2');
    assert.deepEqual(streamIds(e), ['ch_1', 'ch_2']);
    startPlaying(e);
    e.P.key('back'); e.P.key('back');                    // overlay, then exit
    assert.equal(e.log.exits.length, 1);
    assert.equal(e.log.exits[0].itemId, 'ch_2', 'exit reports the last channel');
  }
  // (11) embed (YouTube) channel: ↓ still switches
  {
    const row = { id: 'live_all', items: [CH(1), CH(2)], total: 2 };
    const e = env(liveRoutes(id => (id === 'ch_1' ? liveRes(id, { streams: [S(1, { type: 'embed', url: 'https://www.youtube-nocookie.com/embed/x?autoplay=1' })] }) : null)));
    await e.open({ itemId: 'ch_1', title: 'Kanal 1', type: 'channel', live: true, channels: row });
    assert.equal(e.st().iframe, true);
    e.P.key('chdown'); e.T.advance(400); await tick();   // wraps: 1 -> 2
    assert.equal(e.st().itemId, 'ch_2');
    assert.equal(e.st().iframe, false, 'iframe removed for an hls channel');
    e.P.close();
  }
  // (12) opened from the vitrin: order = "Tüm kanallar" (live_all, starts empty, loaded lazily until the channel is found)
  {
    const row = { id: 'live_all', items: [], total: null };
    const e = env([
      ['GET', /\/api\/streams\//, (b, u) => liveRes(/streams\/([^?]+)/.exec(u)[1])],
      ['GET', /\/api\/row\/live_all/, (b, u) => {
        const off = +/offset=(\d+)/.exec(u)[1];
        return { items: [1, 2, 3, 4, 5].filter(n => n > off && n <= off + 2).map(CH), total: 5 };
      }],
      ...okRoutes]);
    await e.open({ itemId: 'ch_3', title: 'Kanal 3', type: 'channel', live: true, channels: row });
    await tick(); await tick();
    assert.deepEqual(row.items.map(c => c.id), ['ch_1', 'ch_2', 'ch_3', 'ch_4'], 'loaded until the open channel was found');
    e.P.key('up'); e.T.advance(400); await tick();
    assert.equal(e.st().itemId, 'ch_4', '↑ = next in live_all');
    e.P.key('chdown'); e.T.advance(400); await tick();
    assert.equal(e.st().itemId, 'ch_3', 'ChannelDown = previous');
    e.P.close();
  }
  // (13) Hata ayiklama: kapali -> debug=1 yok, panel yok; acik -> debug=1, panel (cihaz satiri + sunucu izi + AVPlay olaylari), Yesil gizler
  {
    const e = env([streamsRoute({ streams: [S(1)], resume_position: 0, duration: 2700 }), ...okRoutes], { avplay: true });
    await e.open();
    assert.equal(e.calls('GET', /debug=1/).length, 0, 'debug off: no debug=1');
    assert.equal(findAll(e.stage, 'dbg').length, 0, 'debug off: no panel');
    e.T.advance(5000); await tick();
    assert.equal(e.calls('POST', /\/api\/debug-log/).length, 0, 'debug off: no remote log');
    e.P.close();
  }
  {
    const trace = [
      { t_ms: 0, stage: 'sources', result: 'info', detail: '1 kaynak denenecek' },
      { t_ms: 900, stage: 'provider', result: 'none', provider: 'OK.ru', host: 'ok.ru', detail: 'sağlayıcı akış vermedi', ms: 840 },
      { t_ms: 950, stage: 'provider', result: 'skipped', provider: 'VidMolly', host: 'vidmoly.me', detail: 'engelli sunucu (blocked_hosts)' },
      { t_ms: 990, stage: 'stream', result: 'found', host: 'vd1.okcdn.ru', detail: '1/2 mp4 · vekil (ua) · paralel 6×1 MiB' }];
    const e = env([streamsRoute({ streams: [S(1, { proxied: true, proxy_reason: 'ua' }), S(2, { type: 'hls', url: 'https://cdn.test/fmp4.m3u8' })],
      duration: 2700, debug_trace: trace }), ...okRoutes], { avplay: true, engine: 'avplay', debug: 'detail' });
    await e.open();
    assert.equal(e.calls('GET', /\/api\/streams\/m1\?.*debug=1/).length, 1, 'debug on: debug=1 sent');
    const panel = findAll(e.stage, 'dbg')[0];
    assert(panel, 'panel drawn in the player');
    const lines = () => e.sb.DZDebug.lines('player');
    assert(/UE55TEST/.test(lines()[0]) && /AVPlay/.test(lines()[0]), 'first line: device (model, AVPlay)');
    assert(lines().some(l => /OK\.ru \(ok\.ru\) inceleniyor → kaynak yok .*840 ms/.test(l)), 'server trace translated: ok.ru none');
    assert(lines().some(l => /VidMolly .*→ atlandı \(engelli sunucu/.test(l)), 'skipped provider');
    assert(lines().some(l => /paralel 6×1 MiB/.test(l)), 'proxy parallel relay noted');
    assert(lines().some(l => /Akış 1\/2 .*AVPlay · vekil üzerinden \(ua\) .*deneniyor/.test(l)), 'client: stream attempt');
    assert(lines().some(l => /AVPlay prepareAsync/.test(l)));
    e.log.avObj.err({ name: 'PLAYER_ERROR_NOT_SUPPORTED_FORMAT', message: 'fmp4' });
    await tick();
    assert(lines().some(l => /prepareAsync hata: PLAYER_ERROR_NOT_SUPPORTED_FORMAT: fmp4/.test(l)), 'prepareAsync error name');
    assert(lines().some(l => /playback-report gönderildi: failure/.test(l)), 'report logged');
    assert(lines().some(l => /Akış 2\/2 .*hls, AVPlay/.test(l)), 'next stream logged');
    e.log.avObj.l.onbufferingstart(); e.log.avObj.l.onbufferingcomplete();
    e.log.avObj.ok();
    e.log.avObj.l.oncurrentplaytime(500);
    assert(lines().some(l => /prepareAsync tamam/.test(l)) && lines().some(l => /İlk kare/.test(l)) && lines().some(l => /onbufferingcomplete/.test(l)));
    e.P.key('green');
    assert(/\boff\b/.test(findAll(e.stage, 'dbg')[0].className), 'green hides the panel');
    e.P.key('green');
    assert(!/\boff\b/.test(findAll(e.stage, 'dbg')[0].className), 'green shows it again');
    e.P.close();
    assert.equal(findAll(e.stage, 'dbg').length, 0, 'panel goes with the player');
  }
  // (14) Hata ayiklama Ozet (eski '1' kaydi = Ozet): provider basina tek satir, akis denemesi yerinde guncellenir, ara adimlar yok
  {
    const trace = [
      { t_ms: 0, stage: 'sources', result: 'info', detail: '2 kaynak denenecek' },
      { t_ms: 100, stage: 'page', result: 'info', site: 'yabancidizi', detail: '3 aday' },
      { t_ms: 900, stage: 'provider', result: 'found', provider: 'OK.ru', host: 'ok.ru', detail: '1 akış', ms: 840 },
      { t_ms: 950, stage: 'provider', result: 'found', provider: 'VidMolly', host: 'vidmoly.me', detail: '1 akış', ms: 600 },
      { t_ms: 960, stage: 'provider', result: 'skipped', provider: 'Abyss', host: 'abyss.to', detail: 'engelli sunucu (blocked_hosts)' },
      { t_ms: 990, stage: 'stream', result: 'found', host: 'vd1.okcdn.ru', detail: '1/2 mp4 · vekil (ua) · paralel 6×1 MiB' }];
    const e = env([streamsRoute({ streams: [S(1, { label: 'OK.ru · 1080p', proxied: true, proxy_reason: 'ua' }),
      S(2, { label: 'VidMolly · Türkçe altyazı · 1080p', type: 'hls', url: 'https://cdn.test/v2.m3u8' })],
      duration: 2700, debug_trace: trace }), ...okRoutes], { avplay: true, engine: 'avplay', debug: true });
    await e.open();
    assert.equal(e.sb.DZDebug.mode(), 'summary', "old '1' -> summary");
    const lines = () => e.sb.DZDebug.lines('player');
    assert.equal(lines()[0], 'UE55TEST', 'summary head: model only (no FW/AVPlay)');
    assert.deepEqual(lines().slice(1, 4), ['✓ OK.ru · 1 akış', '✓ VidMolly · 1 akış', '– Abyss · atlandı (engelli sunucu)'], 'one line per provider');
    assert.equal(lines()[4], '▶ 1/2 OK.ru mp4 · AVPlay (vekil, paralel) … deneniyor');
    assert(!lines().some(l => /prepareAsync|AVPlay open|sayfası|denenecek|İstek/.test(l)), 'no intermediate steps in summary');
    e.T.advance(20000); await tick();
    assert.equal(lines()[4], '▶ 1/2 OK.ru mp4 · AVPlay (vekil, paralel) … deneniyor', 'proxied mp4: still waiting at 20 s');
    e.T.advance(10000); await tick();
    assert.equal(lines()[4], '✗ 1/2 OK.ru · zaman aşımı 30 sn', 'attempt line updated in place (proxied mp4: 30 s)');
    assert.equal(lines()[5], '▶ 2/2 VidMolly hls · AVPlay (doğrudan) … deneniyor');
    e.log.avObj.l.onbufferingstart(); e.log.avObj.l.onbufferingcomplete();
    e.log.avObj.ok();
    e.log.avObj.l.oncurrentplaytime(500);
    assert(/^✓ 2\/2 VidMolly · oynuyor · ilk kare \d+\.\d sn$/.test(lines()[5]), 'success in place: ' + lines()[5]);
    assert.equal(lines().length, 6, 'no buffering / report lines: ' + lines().join(' | '));
    const panel = findAll(e.stage, 'dbg')[0];
    assert(/dbg-sum/.test(panel.className) && panel.children.length <= 8, 'summary panel, at most 8 rows');
    e.sb.DZDebug.sum('player', '✗ x', 'bad'); e.sb.DZDebug.sum('player', '✗ x', 'bad');
    assert.equal(lines()[lines().length - 1], '✗ x ×2', 'repeat counter');
    e.P.close();
  }
  const fails = e => e.calls('POST', /\/api\/playback-report/).filter(x => x.body.event === 'failure');
  const play = (v, from, to) => { v.duration = 2700; v.listeners.loadedmetadata(); v.listeners.playing();
    for (let t = from; t <= to; t++) { v.currentTime = t; v.listeners.timeupdate(); } };
  // (15) Tizen: webapis.avplay present but engine pref = Otomatik -> HTML5 <video>; report engine html5 + stream identity
  {
    const e = env([streamsRoute({ streams: [S(1)], duration: 2700 }), ...okRoutes], { avplay: true });
    await e.open();
    assert.equal(e.log.av.length, 0, 'no AVPlay by default');
    assert.equal(e.video().src, 'https://cdn.test/v1.mp4', '<video> by default even with webapis');
    assert.equal(e.P.enginePref(), 'auto');
    e.video().error = { code: 4 }; e.video().listeners.error(); await tick();
    const f = fails(e)[0];
    assert.equal(f.body.engine, 'native');
    assert.equal(f.body.variant_id, 'v1', 'variant_id in report');
    assert.equal(f.body.stream_url_host, 'cdn.test');
    assert.equal(f.body.stream_type, 'mp4');
    e.P.close();
  }
  // (15b) Tizen + streams[].engine_hint: hlsjs hint -> hls.js straight away (no native try); failure -> NEXT stream (same stream is
  // not retried with another engine); native hint on hls -> <video> src; reports carry engine hlsjs/native + platform + provider
  {
    const TIZEN = 'Mozilla/5.0 (SMART-TV; LINUX; Tizen 6.5) AppleWebKit/537.36 (KHTML, like Gecko) 85.0.4183.93/6.5 TV Safari/537.36';
    const e = env([streamsRoute({ streams: [
      S(1, { type: 'hls', url: 'https://uns.test/master.m3u8', provider: 'UNS', label: 'UNS · auto', engine_hint: 'hlsjs', engine_hint_source: 'seed' }),
      S(2, { type: 'hls', url: 'https://vm.test/a.m3u8', provider: 'VidMolly', label: 'VidMolly · auto', engine_hint: 'native', engine_hint_source: 'learned' })],
      duration: 2700 }), ...okRoutes], { ua: TIZEN, debug: true });
    await e.open();
    assert(/[?&]platform=tizen/.test(e.calls('GET', /\/api\/streams\//)[0].u), 'platform sent with the streams request');
    assert.equal(e.log.hls.length, 1, 'hls.js used on Tizen because of the hint');
    assert.equal(e.log.hls[0].url, 'https://uns.test/master.m3u8');
    const sum = () => e.sb.DZDebug.lines('player');
    assert(sum().some(l => /^▶ 1\/2 UNS hls · hls\.js \(tohum\) \(doğrudan\) … deneniyor$/.test(l)), 'summary shows engine: ' + sum().join(' | '));
    e.T.advance(30000); await tick();
    const f = fails(e)[0];
    assert.equal(f.body.engine, 'hlsjs');
    assert.equal(f.body.platform, 'tizen');
    assert.equal(f.body.provider, 'UNS');
    assert.equal(e.log.hls.length, 1, 'no hls.js retry and no native retry of stream 1');
    assert.equal(e.video().src, 'https://vm.test/a.m3u8', 'stream 2 opened natively (hint native)');
    assert(sum().some(l => /2\/2 VidMolly hls · <video> \(öğrenilmiş\)/.test(l)), 'learned hint in summary');
    e.P.close();
  }
  // (16) start timer: cleared at the first frame (no 3 s wait); proxied mp4 = 30 s; a buffering event extends once by +10 s
  {
    const e = env([streamsRoute({ streams: [S(1), S(2)], duration: 2700 }), ...okRoutes]);
    await e.open();
    const v = e.video(); v.duration = 2700; v.listeners.loadedmetadata(); v.listeners.playing();   // first frame, 0 s played
    e.T.advance(25000); await tick();
    assert.equal(fails(e).length, 0, 'timer cleared at the first frame');
    e.P.close();
  }
  {
    const e = env([streamsRoute({ streams: [S(1, { proxied: true, proxy_reason: 'ua' }), S(2)], duration: 2700 }), ...okRoutes]);
    await e.open();
    e.T.advance(20000); await tick();
    assert.equal(fails(e).length, 0, 'proxied mp4: not yet at 20 s');
    e.video().listeners.waiting();                       // buffering event -> +10 s once
    e.video().listeners.waiting();
    e.T.advance(19999); await tick();
    assert.equal(fails(e).length, 0, 'extended: not yet at 39.9 s');
    e.T.advance(1); await tick();
    assert.equal(fails(e).length, 1, 'timeout at 40 s');
    assert.equal(fails(e)[0].body.code, 'timeout');
    e.P.close();
  }
  // (17) cyclic fallback: stream 2 chosen by hand fails -> back to stream 1 (already played) at the current position
  {
    const e = env([streamsRoute({ streams: [S(1), S(2)], duration: 2700 }), ...okRoutes]);
    await e.open();
    play(e.video(), 1, 50);
    const defs = e.st().btnDefs, si = defs.findIndex(d => d.id === 'source');
    assert(si > 0, 'source button');
    e.P.key('down'); for (let i = 0; i < si; i++) e.P.key('right');
    e.P.key('ok');                                       // source panel
    e.P.key('down'); e.P.key('ok');                      // pick stream 2
    assert.equal(e.video().src, 'https://cdn.test/v2.mp4', 'manual switch');
    e.video().error = { code: 4 }; e.video().listeners.error(); await tick();
    const v1 = e.video();
    assert.equal(v1.src, 'https://cdn.test/v1.mp4', 'cyclic: back to stream 1');
    assert.equal(e.st().idx, 0);
    v1.duration = 2700; v1.listeners.loadedmetadata();
    assert.equal(v1.currentTime, 50, 'resumed from the position');
    v1.error = { code: 4 }; v1.listeners.error(); await tick();
    assert(e.st().err, 'all failed -> error screen');
    assert.equal(fails(e).length, 2, 'each stream counted once');
    e.P.close();
  }
  // (18) stall: >15 s single wait after start -> failure `stall` + next stream from the position
  {
    const e = env([streamsRoute({ streams: [S(1, { label: 'VidHide · 720p' }), S(2)], duration: 2700 }), ...okRoutes], { debug: 'summary' });
    await e.open();
    play(e.video(), 1, 30);
    e.video().listeners.waiting();
    e.T.advance(15000); await tick();
    assert.equal(fails(e).length, 0, 'no stall at 15 s');
    e.T.advance(1000); await tick();
    const f = fails(e)[0];
    assert(f, 'stall reported');
    assert.equal(f.body.code, 'stall');
    assert(/bekleme 16 sn/.test(f.body.detail), f.body.detail);
    assert.equal(f.body.variant_id, 'v1');
    assert(e.sb.DZDebug.lines('player').some(l => l === '✗ VidHide · takılma (bekleme 16 sn)'), 'summary line');
    const rl = e.calls('POST', /\/api\/debug-log$/);
    assert(rl.length >= 1, 'remote debug log batched');
    assert(/^browser-[a-z0-9]+$/.test(rl[0].body.device), rl[0].body.device);
    assert.equal(rl[0].body.mode, 'summary');
    assert(rl.every(x => x.body.lines.length <= 50));
    const v2 = e.video();
    assert.equal(v2.src, 'https://cdn.test/v2.mp4', 'next stream');
    v2.duration = 2700; v2.listeners.loadedmetadata();
    assert.equal(v2.currentTime, 30, 'from the position');
    e.P.close();
  }
  // (18b) stall: total waits >30 s in 60 s (each <15 s)
  {
    const e = env([streamsRoute({ streams: [S(1), S(2)], duration: 2700 }), ...okRoutes]);
    await e.open();
    const v = e.video(); play(v, 1, 20);
    for (let k = 0; k < 3; k++) { v.listeners.waiting(); e.T.advance(10000); v.listeners.playing(); e.T.advance(1000); }
    assert.equal(fails(e).length, 0, '30 s total: not yet');
    v.listeners.waiting(); e.T.advance(2000); await tick();
    assert.equal(fails(e).length, 1, 'total > 30 s -> stall');
    assert(/toplam bekleme/.test(fails(e)[0].body.detail));
    e.P.close();
  }
  // (19) stale error guard: a destroyed engine's <video> error (old stream torn down) never fails the NEW attempt;
  // an error before the src/hls.js attach (unarmed video) is ignored too; every attempt gets a fresh <video>
  {
    const TIZEN = 'Mozilla/5.0 (SMART-TV; LINUX; Tizen 6.5) AppleWebKit/537.36 (KHTML, like Gecko) 85.0.4183.93/6.5 TV Safari/537.36';
    const e = env([streamsRoute({ streams: [S(1),
      S(2, { type: 'hls', url: 'https://vh.test/a.m3u8', provider: 'VidHide', engine_hint: 'hlsjs', engine_hint_source: 'seed' }), S(3)],
      duration: 2700 }), ...okRoutes], { ua: TIZEN, debug: true });
    await e.open();
    const v1 = e.video();
    v1.error = { code: 4, message: 'MEDIA_ELEMENT_ERROR: Format error' }; v1.listeners.error();   // stream 1 fails
    const v2 = e.video();
    assert.notStrictEqual(v2, v1, 'fresh <video> for the next attempt');
    v2.error = { code: 4, message: 'MEDIA_ELEMENT_ERROR: Empty src attribute' }; v2.listeners.error();   // before hls.js attach: unarmed
    v1.listeners.error();                                                                                 // stale: old engine destroyed
    await tick();
    assert.equal(fails(e).length, 1, 'only stream 1 failed: ' + fails(e).map(f => f.body.variant_id).join(','));
    assert.equal(e.st().idx, 1, 'still on stream 2');
    assert.equal(e.log.hls.length, 1, 'hls.js attached to stream 2');
    assert.strictEqual(e.log.hls[0].media, v2);
    v2.error = { code: 4, message: 'MEDIA_ELEMENT_ERROR: Media load rejected by URL safety check' }; v2.listeners.error(); await tick();
    const f2 = fails(e)[1];
    assert(f2 && f2.body.variant_id === 'v2', 'armed hls.js video error counts');
    assert(/Media load rejected by URL safety check · hls\.js@/.test(f2.body.detail), f2.body.detail);
    assert(e.sb.DZDebug.lines('player').some(l => /✗ 2\/3 .*URL safety check/.test(l)), 'summary keeps the full reason');
    e.P.close();
  }
  // (20) hint hlsjs but Hls.isSupported() false -> explicit "hls.js desteklenmiyor" failure + next stream; m3u8 never put on <video>
  {
    const TIZEN = 'Mozilla/5.0 (SMART-TV; LINUX; Tizen 6.5) AppleWebKit/537.36 (KHTML, like Gecko) 85.0.4183.93/6.5 TV Safari/537.36';
    const e = env([streamsRoute({ streams: [
      S(1, { type: 'hls', url: 'https://uns.test/master.m3u8', provider: 'UNS', engine_hint: 'hlsjs', engine_hint_source: 'seed' }), S(2)],
      duration: 2700 }), ...okRoutes], { ua: TIZEN, debug: true, hlsUnsupported: true });
    await e.open();
    const f = fails(e)[0];
    assert(f, 'failure reported');
    assert.equal(f.body.code, 'unsupported');
    assert(/hls\.js desteklenmiyor/.test(f.body.detail), f.body.detail);
    assert.equal(e.log.hls.length, 0, 'no Hls instance');
    assert(!e.log.videos.some(v => /\.m3u8/.test(v.src || '')), 'm3u8 never assigned to <video>');
    assert.equal(e.video().src, 'https://cdn.test/v2.mp4', 'next stream');
    e.P.close();
  }
  console.log('client player: ok');
})().catch(err => { console.error(err); process.exit(1); });
