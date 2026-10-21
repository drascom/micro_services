// The real player + playflow + tracks/subs/panel scripts against a deterministic AVPlay double:
// soft subtitles shown from the engine clock, panel-driven variant switch that keeps the position, preference
// persistence + default rule at play start, silent "Kapali" when the subtitle cannot be loaded, burned-in notes, iframe mode.
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, find, findAll, timers } = require('./_fake_dom');

const CLIENT = path.join(__dirname, '../tizen-client/js');
const VTT = fs.readFileSync(path.join(__dirname, '../server/tests/fixtures/vidmolly_subtitle_snw_s4e2_en.vtt'), 'utf8');
const tick = async (n = 10) => { for (let i = 0; i < n; i++) await Promise.resolve(); };

const TR = { url: 'https://cdn.test/tr/master.m3u8', type: 'hls', label: 'VidMolly · Türkçe altyazı · auto', variant_id: 'v_tr', audio_lang: 'en',
  sub_mode: 'hard', hard_lang: 'tr', attempt_token: 'tok-tr', kind: 'episode', source_id: 'vs1' };
const EN = { url: 'https://cdn.test/en/master.m3u8', type: 'hls', label: 'VidMolly · İngilizce altyazı · auto', variant_id: 'v_en', audio_lang: 'en',
  sub_mode: 'soft', hard_lang: null, attempt_token: 'tok-en', kind: 'episode', source_id: 'vs1' };
const SUB_EN = { id: 'x1', lang: 'en', label: 'İngilizce', kind: 'captions', format: 'vtt', url: '/api/subtitles/x1.vtt', stream_ids: ['v_en'], default: true, origin: 'soft' };
const AUDIO = [{ id: 'a_en', lang: 'en', label: 'İngilizce', stream_ids: ['v_tr', 'v_en'], default: true }];
const SNW = { streams: [TR, EN], subtitles: [SUB_EN], audio: AUDIO, resume_position: 100, duration: 3600 };

function memory(init) {
  const data = Object.assign({}, init || {});
  return { data, getItem: k => Object.prototype.hasOwnProperty.call(data, k) ? data[k] : null, setItem: (k, v) => { data[k] = String(v); } };
}

function env(res, o) {
  o = o || {};
  const T = timers();
  const store = memory(o.prefs);
  const window = { navigator: { onLine: true }, DZ: {}, localStorage: store };
  const DZ = window.DZ;
  const av = { opens: [], seeks: [], listeners: [], now: 0, dur: 3600, url: '',
    open(u) { av.url = u; av.opens.push(u); }, setDisplayRect() {}, setDisplayMethod() {}, setListener(l) { av.listeners.push(l); },
    hold: false, held: null, release() { const f = av.held; av.held = null; if (f) f(); },
    prepareAsync(ok) { if (av.hold) av.held = ok; else ok(); }, getDuration() { return av.dur * 1000; }, getCurrentTime() { return Math.round(av.now * 1000); },
    play() {}, pause() {}, stop() {}, close() {}, seekTo(ms) { av.seeks.push(ms); } };
  const log = { reports: [], go: [], dialogs: [], back: 0, fetched: [] };
  DZ.api = { profileId: () => o.profile || 'p1', img: p => 'http://tv.test' + p, detail: () => Promise.resolve({}), progress: () => Promise.resolve({}),
    streams: () => Promise.resolve(res), playbackReport: p => { log.reports.push(p); return Promise.resolve({}); } };
  DZ.modal = { open(cfg) { log.dialogs.push(cfg); }, loading(cfg) { return { cfg, closed: false, setStage() {}, close() { this.closed = true; } }; } };
  DZ.app = { go(name, params, replace) { log.go.push({ name, params, replace }); }, back() { log.back++; } };
  const sandbox = { window, console: { log() {} }, webapis: { avplay: av },
    setTimeout: T.setTimeout, clearTimeout: T.clearTimeout, setInterval: T.setInterval, clearInterval: T.clearInterval,
    document: { createElement: el, body: el('body'), documentElement: el('html') } };
  vm.createContext(sandbox);
  for (const f of ['subs.js', 'tracks.js', 'ui/tracks_panel.js', 'screens/player.js', 'playflow.js']) vm.runInContext(fs.readFileSync(path.join(CLIENT, f), 'utf8'), sandbox);
  DZ.subs.fetchText = url => { log.fetched.push(url); return o.fetchText ? o.fetchText(url) : Promise.resolve(VTT); };
  const root = el('div');
  const e = { T, av, log, store, DZ, root, screen: DZ.screens.player,
    press: name => DZ.screens.player.key({ name }),
    q: cls => find(root, cls),
    subsBox: () => find(root, 'pl-subs'),
    subsText: () => find(root, 'pl-subs').textContent,
    subsOn: () => find(root, 'pl-subs').classList.contains('on'),
    panelOpen: () => !find(root, 'pl-tp').classList.contains('hidden'),
    tracksBtn: () => find(root, 'pl-tbtn').textContent,
    tracksVisible: () => !find(root, 'pl-tracks').classList.contains('hidden'),
    focusedItem: () => { const f = findAll(find(root, 'pl-tp'), 'pl-tp-item').filter(n => n.classList.contains('focused')); return f.length ? f[0].textContent : null; },
    at(sec) { av.now = sec; const l = av.listeners[av.listeners.length - 1]; if (l) l.oncurrentplaytime(Math.round(sec * 1000)); T.advance(250); },
    start(params) { e.screen.enter(root, params || { itemId: 'snw', episodeId: 'snw:s4:e2', type: 'series' }); return tick(); }
  };
  return e;
}

(async () => {
  // A) direct player: default rule, panel, variant switch keeping the position, soft subtitle from the engine clock, Kapali, back
  {
    const e = env(SNW);
    await e.start();
    assert.deepEqual(e.av.opens, [TR.url], 'default rule: no soft Turkish -> the Turkish hard-sub file first');
    assert.deepEqual(e.av.seeks, [100000], 'server resume position');
    assert(e.tracksVisible(), 'the audio/subtitle button appears next to the quality button');
    assert.equal(e.tracksBtn(), 'Ses ve Altyazılar · Türkçe (gömülü)');
    assert(!e.subsOn(), 'burned-in subtitle: the overlay stays empty');
    assert(!e.panelOpen());

    e.at(101); e.at(102);
    assert.equal(e.press('yellow'), true);
    assert(e.panelOpen(), 'yellow key opens the panel'); assert.equal(e.focusedItem(), 'Türkçe', 'opens on the current subtitle');
    assert.equal(e.press('down'), true); assert.equal(e.focusedItem(), 'İngilizce');
    e.at(150.5);
    e.av.hold = true;                         // a real prepareAsync answers later: the status must stay meanwhile
    e.press('enter');                         // English = the clean file with the soft VTT
    assert(!e.panelOpen(), 'a stream switch closes the panel');
    assert.deepEqual(e.av.opens, [TR.url, EN.url], 'switched to the other file');
    assert.equal(e.store.data['dz_pref_sub_p1'], 'en', 'the choice is remembered per profile');
    assert.equal(e.q('pl-state').textContent, 'Akış değiştiriliyor…', 'short loading indicator while switching');
    assert.equal(e.tracksBtn(), 'Ses ve Altyazılar · İngilizce');
    e.av.hold = false; e.av.release();
    await tick();
    assert.deepEqual(e.av.seeks, [100000, 150500], 'the playback position is kept across the switch');
    assert.deepEqual(e.log.fetched, ['http://tv.test/api/subtitles/x1.vtt'], 'subtitle comes from the server proxy path');
    e.at(150.5);
    assert.equal(e.q('pl-state').textContent, '', 'status cleared once the new file plays');
    assert.equal(e.subsText(), 'The Griffin.'); assert(e.subsOn());
    e.at(2.6); assert.equal(e.subsText(), '* *', 'seek backwards: the cue follows the engine clock');
    e.at(4000); assert.equal(e.subsText(), '');
    e.at(3.0); const shown = e.subsText(); e.T.advance(5000); assert.equal(e.subsText(), shown, 'paused: time frozen, cue stays');
    e.at(2.503); assert(e.subsOn());
    // controls raise the text above the progress bar, hidden controls put it back in the bottom 10%
    assert(!e.subsBox().classList.contains('up'), 'controls already hidden -> bottom of the screen');
    e.press('yellow'); e.press('back');   // any key wakes the controls
    assert(e.subsBox().classList.contains('up'), 'controls visible -> raised above the progress bar');
    e.T.advance(3100);
    assert(!e.subsBox().classList.contains('up'), 'controls hidden -> bottom of the screen');
    assert(e.subsOn(), 'subtitles stay visible when the controls hide');

    e.press('yellow'); assert.equal(e.focusedItem(), 'İngilizce');
    e.press('up'); e.press('up'); assert.equal(e.focusedItem(), 'Kapalı');
    e.press('enter');                         // Kapali: same clean file, overlay off, no reload
    assert.equal(e.av.opens.length, 2); assert(e.panelOpen(), 'a soft change keeps the panel open (Netflix style)');
    assert(!e.subsOn()); assert.equal(e.store.data['dz_pref_sub_p1'], 'off'); assert.equal(e.tracksBtn(), 'Ses ve Altyazılar · Kapalı');
    assert.equal(e.press('back'), true); assert(!e.panelOpen(), 'Back closes the panel');
    assert.equal(e.log.back, 0, 'Back with the panel open must not leave the player');
    e.press('yellow'); e.press('down'); e.press('down'); e.press('enter');   // English again (soft, same stream)
    await tick(); e.at(150.5); assert.equal(e.subsText(), 'The Griffin.');
    e.press('up'); e.press('enter');          // Turkce (gomulu) = the Turkish file again
    assert.equal(e.av.opens.length, 3); assert.equal(e.av.opens[2], TR.url);
    assert.equal(e.av.seeks[e.av.seeks.length - 1], 150500);
    assert.equal(e.store.data['dz_pref_sub_p1'], 'tr'); assert(!e.subsOn(), 'burned-in: overlay off');
    assert.equal(e.tracksBtn(), 'Ses ve Altyazılar · Türkçe (gömülü)');
    e.screen.exit();
    assert.equal(e.T.pending(), 0, 'no timers left behind');
  }

  // B) the subtitle cannot be loaded (404 / timeout / broken file): silently Kapali, preference untouched, video keeps playing
  for (const failing of [() => Promise.reject(new Error('http_404')), () => Promise.resolve('<html>no</html>')]) {
    const e = env(SNW, { prefs: { dz_pref_sub_p1: 'en' }, fetchText: failing });
    await e.start();
    assert.deepEqual(e.av.opens, [EN.url], 'preference English: the clean file first');
    await tick();
    e.at(150.5);
    assert(!e.subsOn(), 'no subtitle shown');
    assert.equal(e.tracksBtn(), 'Ses ve Altyazılar · Kapalı', 'the UI says Kapali, no error');
    assert.equal(e.log.dialogs.length, 0, 'no dialog for a missing subtitle');
    assert.equal(e.store.data['dz_pref_sub_p1'], 'en', 'a failed load does not erase the preference');
    e.press('yellow');
    const current = findAll(find(e.root, 'pl-tp'), 'pl-tp-item').filter(n => n.classList.contains('current')).map(n => n.textContent);
    assert.deepEqual(current, ['İngilizce', 'Kapalı'], 'the panel shows the effective choice (audio + Kapali)');
    e.screen.exit();
  }
  {
    // timeout: a stalled request ends after the short timeout
    const e = env(SNW, { prefs: { dz_pref_sub_p1: 'en' }, fetchText: () => new Promise(() => {}) });
    await e.start();
    e.T.advance(5900); await tick(); assert.equal(e.tracksBtn(), 'Ses ve Altyazılar · İngilizce', 'still waiting');
    e.T.advance(200); await tick();
    assert.equal(e.tracksBtn(), 'Ses ve Altyazılar · Kapalı');
    assert.equal(e.log.dialogs.length, 0);
    e.screen.exit();
  }

  // C) a late answer for a superseded choice is ignored (no ghost subtitle after choosing Kapali)
  {
    let release;
    const e = env(SNW, { prefs: { dz_pref_sub_p1: 'en' }, fetchText: () => new Promise(r => { release = r; }) });
    await e.start();
    e.press('yellow'); e.press('up'); e.press('up'); e.press('enter');   // Kapali while the VTT is still loading
    assert(!e.subsOn());
    release(VTT); await tick(); e.at(150.5);
    assert(!e.subsOn(), 'the stale VTT must not appear');
    assert.equal(e.tracksBtn(), 'Ses ve Altyazılar · Kapalı');
    e.screen.exit();
  }

  // D) burned-in only source: Kapali cannot be honoured -> explained on screen, nothing switches
  {
    const e = env({ streams: [TR], subtitles: [], audio: AUDIO, resume_position: 0, duration: 3600 });
    await e.start();
    assert(e.tracksVisible());
    e.press('down');                                   // Asagi ok da paneli acar
    assert(e.panelOpen());
    assert.equal(e.focusedItem(), 'Türkçe');
    assert(findAll(find(e.root, 'pl-tp'), 'pl-tp-item').filter(n => n.classList.contains('unavail')).map(n => n.textContent).indexOf('Kapalı') >= 0, 'Kapali greyed out');
    e.press('up'); e.press('enter');
    assert(/gömülü/.test(find(e.root, 'pl-tp-note').textContent), 'the reason is shown on screen');
    assert.equal(e.av.opens.length, 1); assert.equal(e.tracksBtn(), 'Ses ve Altyazılar · Türkçe (gömülü)');
    assert(e.panelOpen());
    assert.equal(e.store.data['dz_pref_sub_p1'], undefined, 'an impossible choice is not remembered');
    e.screen.exit();
  }

  // E) nothing to choose / iframe (embed) mode: no button, no panel, no overlay
  {
    const plain = env({ streams: [{ url: 'https://m.test/a.mp4', type: 'mp4', attempt_token: 't', kind: 'movie', label: 'A' }], subtitles: [], audio: [], resume_position: 0 });
    await plain.start();
    assert(!plain.tracksVisible(), 'a single stream without track data: no button');
    plain.press('yellow'); plain.press('down');
    assert(!plain.panelOpen());
    plain.screen.exit();
    const emb = env({ streams: [{ url: 'https://www.youtube.com/embed/x', type: 'embed', attempt_token: 't', kind: 'trailer', label: 'Fragman' }],
      subtitles: [SUB_EN], audio: AUDIO, resume_position: 0 });
    await emb.start();
    assert(!emb.tracksVisible(), 'iframe mode: no audio/subtitle button');
    emb.press('yellow');
    assert(!emb.panelOpen(), 'iframe mode: no panel');
    assert(!emb.subsOn());
    assert.deepEqual(emb.log.fetched, [], 'iframe mode: no subtitle request');
    emb.screen.exit();
  }

  // F) playflow: the first stream tried follows the preference; the player adopts it and shows its subtitle
  for (const c of [{ prefs: {}, first: TR, sub: 'Türkçe (gömülü)', on: false },
                   { prefs: { dz_pref_sub_p1: 'en' }, first: EN, sub: 'İngilizce', on: true },
                   { prefs: { dz_pref_sub_p1: 'off' }, first: EN, sub: 'Kapalı', on: false },
                   { prefs: { dz_pref_sub_p1: 'tr' }, first: TR, sub: 'Türkçe (gömülü)', on: false },
                   { prefs: { dz_pref_sub_p1: 'de' }, first: TR, sub: 'Türkçe (gömülü)', on: false }]) {
    const e = env(SNW, { prefs: c.prefs });
    e.DZ.playflow.start({ itemId: 'snw', episodeId: 'snw:s4:e2', title: 'SNW', type: 'series' });
    await tick(12);
    assert.equal(e.av.opens[0], c.first.url, JSON.stringify(c.prefs) + ': preferred file tried first');
    assert.equal(e.log.go.length, 0, 'the player opens only after real playback started');
    e.av.listeners[0].oncurrentplaytime(100000);
    assert.equal(e.log.go.length, 1);
    const params = e.log.go[0].params;
    assert.equal(params.prepared.streams[0].variant_id, c.first.variant_id);
    assert.deepEqual(params.prepared.subtitles, [SUB_EN]); assert.deepEqual(params.prepared.audio, AUDIO);
    assert.equal(params.prepared.streams.length, 2, 'the other file stays as a fallback in server order');
    e.screen.enter(e.root, params);
    await tick();
    assert.equal(e.av.opens.length, 1, 'the running engine is adopted, not reopened');
    assert.equal(e.tracksBtn(), 'Ses ve Altyazılar · ' + c.sub);
    e.at(150.5);
    assert.equal(e.subsOn(), c.on, JSON.stringify(c.prefs));
    if (c.on) assert.equal(e.subsText(), 'The Griffin.');
    e.screen.exit();
  }

  // G) fallback: the preferred file fails to prepare -> the other one plays, its own subtitle rule applies
  {
    const e = env(SNW, { prefs: { dz_pref_sub_p1: 'en' } });
    e.av.prepareAsync = (ok, err) => { if (e.av.url === EN.url) err('boom'); else ok(); };
    e.DZ.playflow.start({ itemId: 'snw', episodeId: 'snw:s4:e2', title: 'SNW', type: 'series' });
    await tick(12);
    assert.deepEqual(e.av.opens, [EN.url, TR.url]);
    e.av.listeners[e.av.listeners.length - 1].oncurrentplaytime(100000);
    const params = e.log.go[0].params;
    assert.equal(params.prepared.index, 1);
    e.screen.enter(e.root, params);
    await tick();
    assert.equal(e.tracksBtn(), 'Ses ve Altyazılar · Türkçe (gömülü)', 'English unavailable on this file: burned-in Turkish is what plays');
    assert.equal(e.store.data['dz_pref_sub_p1'], 'en', 'the preference survives the fallback');
    e.screen.exit();
  }

  console.log('Player tracks: overlay from engine clock, panel keys, variant switch keeps position, prefs + default rule, silent Kapali, burned-in note, iframe: OK');
})().catch(e => { console.error(e); process.exitCode = 1; });
