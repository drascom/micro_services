// "En yüksek kalite" tercihi: playflow akış sıralaması. Çözünürlüğü (quality/label'dan ayrıştırılan yükseklik) tercihi aşan doğrudan
// akışlar doğrudan akışların SONUNA (embed'lerin önüne) atılır; yükseklik ayrıştırılamıyorsa akışa dokunulmaz; "auto" hiçbir şey değiştirmez;
// dil/altyazı tercihi (tracks.prefer) sıralamadan SONRA çalışır ve aynı dildeki düşük çözünürlüğü seçer.
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { timers } = require('./_fake_dom');

const root = path.join(__dirname, '../tizen-client/js');
const read = f => fs.readFileSync(path.join(root, f), 'utf8');
const tick = async (n = 8) => { for (let i = 0; i < n; i++) await Promise.resolve(); };

function load(extra) {
  const T = timers();
  const window = Object.assign({ navigator: { onLine: true }, DZ: {} }, extra && extra.window);
  const DZ = window.DZ;
  const log = { tried: [], go: [], reports: [] };
  DZ.api = Object.assign({
    streams: () => Promise.resolve(extra.res),
    playbackReport: p => { log.reports.push(p); return Promise.resolve({}); }
  }, extra && extra.api);
  DZ.modal = { loading() { return { setStage() {}, close() {} }; }, open() {} };
  DZ.app = { go: (name, params) => log.go.push({ name, params }), back() {} };
  DZ.player = { hasAvplay: () => true, isEmbedStream: s => s.type === 'embed',
    createEngine(h, av, cb) {
      const eng = { av: true, prepare(url) { log.tried.push(url); }, start() {}, destroy() {}, setCallbacks() {} };
      return eng;
    } };
  const sandbox = { window, console: { log() {} }, setTimeout: T.setTimeout, clearTimeout: T.clearTimeout, setInterval: T.setInterval, clearInterval: T.clearInterval };
  vm.createContext(sandbox);
  if (extra && extra.tracks) vm.runInContext(read('tracks.js'), sandbox);
  vm.runInContext(read('playflow.js'), sandbox);
  return { DZ, window, log, T, flow: DZ.playflow };
}

const S = (q, extra) => Object.assign({ url: 'https://ok.ru/' + q + '.mp4', type: 'mp4', quality: q, label: 'OK.ru · Türkçe altyazı · ' + q, attempt_token: q }, extra || {});

(async () => {
  const pf = load({ res: { streams: [] } }).flow;

  // ---- height parsing (quality first, then label; unknown = 0)
  const h = pf.streamHeight;
  assert.equal(h({ quality: '1440p' }), 1440);
  assert.equal(h({ quality: 'auto', label: 'OK.ru · Türkçe altyazı · 1080p' }), 1080, 'falls back to the label');
  assert.equal(h({ quality: 'auto', label: 'VidMolly · Türkçe altyazı · auto' }), 0, 'not parseable: 0');
  assert.equal(h({ quality: '2160p' }), 2160); assert.equal(h({ label: 'Film 4K' }), 2160); assert.equal(h({ label: 'Film 2K' }), 1440);
  assert.equal(h({ quality: '1920x1080' }), 1080); assert.equal(h({ quality: '720p60' }), 720);
  assert.equal(h({ label: 'stream.mp4' }), 0, '"mp4" is not a resolution'); assert.equal(h({}), 0); assert.equal(h(null), 0);

  // ---- preference -> cap
  assert.deepEqual(['1080', '1440', 'auto', undefined, 'junk', null].map(pf.qualityCap), [1080, 1440, 0, 1080, 1080, 1080]);

  // ---- ordering: over-cap direct streams go after the other direct streams, before embeds; everything else keeps the server order
  const list = [S('1440p'), S('1080p'), { url: 'https://v/x.m3u8', type: 'hls', quality: 'auto', label: 'VidMolly · auto', attempt_token: 'auto' },
    { url: 'https://e/embed', type: 'embed', quality: 'auto', label: 'Embed', attempt_token: 'emb' }, S('2160p'), S('720p')];
  const ids = l => l.map(s => s.attempt_token).join(',');
  assert.equal(ids(pf.orderStreams(list, 1080)), '1080p,auto,720p,1440p,2160p,emb', 'cap 1080: 1440p/2160p last among direct streams, unparseable untouched');
  assert.equal(ids(pf.orderStreams(list, 1440)), '1440p,1080p,auto,720p,2160p,emb', 'cap 1440: only 2160p demoted');
  assert.equal(ids(pf.orderStreams(list, 0)), '1440p,1080p,auto,2160p,720p,emb', 'auto: server order (embeds still last)');
  assert.equal(ids(pf.orderStreams(list)), ids(pf.orderStreams(list, 0)), 'no cap argument = untouched');
  assert.equal(ids(pf.orderStreams([S('1440p')], 1080)), '1440p', 'a lone high-resolution stream is still played');
  assert.equal(list.length, 6, 'input list not mutated'); assert.equal(list[0].attempt_token, '1440p');

  // ---- the play flow honours the stored preference (DZ.api.qualityPref, else localStorage, else 1080)
  const res = { streams: [S('1440p'), S('1080p'), S('720p')], resume_position: 0, duration: 100 };
  async function firstTried(extra) {
    const w = load(Object.assign({ res }, extra));
    w.flow.start({ itemId: 'm', title: 'Film', type: 'movie', kind: 'video' });
    await tick();
    return w.log.tried;
  }
  assert.deepEqual(await firstTried({ api: { qualityPref: () => '1080' } }), ['https://ok.ru/1080p.mp4'], 'default: 1080p first, 1440p is no longer tried first');
  assert.deepEqual(await firstTried({ api: { qualityPref: () => '1440' } }), ['https://ok.ru/1440p.mp4'], '1440p allowed');
  assert.deepEqual(await firstTried({ api: { qualityPref: () => 'auto' } }), ['https://ok.ru/1440p.mp4'], 'auto: server order');
  assert.deepEqual(await firstTried({}), ['https://ok.ru/1080p.mp4'], 'no preference stored: 1080 default');
  assert.deepEqual(await firstTried({ window: { localStorage: { getItem: k => (k === 'dz_pref_quality' ? 'auto' : null) } } }), ['https://ok.ru/1440p.mp4'], 'localStorage fallback');

  // ---- language preference still decides WHICH file first; the cap only reorders same-language alternatives
  const TR = (q, id) => Object.assign(S(q), { variant_id: id, audio_lang: 'en', sub_mode: 'hard', hard_lang: 'tr' });
  const EN = Object.assign(S('720p'), { url: 'https://ok.ru/en720.mp4', attempt_token: 'en720', variant_id: 'v_en', audio_lang: 'en', sub_mode: 'none', hard_lang: null });
  const withTr = { streams: [TR('1440p', 'v1'), EN, TR('1080p', 'v2')], resume_position: 0, duration: 100 };
  let w = load({ res: withTr, tracks: true, api: { qualityPref: () => '1080' } });
  w.flow.start({ itemId: 's', episodeId: 's:s1:e1', title: 'D', type: 'series', kind: 'video' }); await tick();
  assert.deepEqual(w.log.tried, ['https://ok.ru/1080p.mp4'], 'Turkish preferred, and the 1080p Turkish file beats the 1440p one');
  const onlyHigh = { streams: [TR('1440p', 'v1'), EN], resume_position: 0, duration: 100 };
  w = load({ res: onlyHigh, tracks: true, api: { qualityPref: () => '1080' } });
  w.flow.start({ itemId: 's', episodeId: 's:s1:e1', title: 'D', type: 'series', kind: 'video' }); await tick();
  assert.deepEqual(w.log.tried, ['https://ok.ru/1440p.mp4'], 'language preference wins when the only matching file is 1440p');

  console.log('Quality preference: height parsing, cap order (1440p last), auto untouched, preference read, language pref interplay: OK');
})().catch(e => { console.error(e); process.exitCode = 1; });
