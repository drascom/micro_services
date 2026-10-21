// Audio/subtitle selection model (options from the server fields, default rule, persisted preference, variant
// switching decisions) and the two-column "Ses ve Altyazilar" panel (focus, dim columns, keys).
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, find, findAll } = require('./_fake_dom');

const CLIENT = path.join(__dirname, '../tizen-client/js');

function load(profile, storage) {
  const window = { DZ: { api: { profileId: () => profile === undefined ? 'p1' : profile } } };
  if (storage !== null) window.localStorage = storage || memory();
  const sandbox = { window, document: { createElement: el }, console: { log() {} } };
  vm.createContext(sandbox);
  for (const f of ['tracks.js', 'ui/tracks_panel.js']) vm.runInContext(fs.readFileSync(path.join(CLIENT, f), 'utf8'), sandbox);
  return { DZ: window.DZ, T: window.DZ.tracks, window };
}
function memory() {
  const data = {};
  return { data, getItem: k => Object.prototype.hasOwnProperty.call(data, k) ? data[k] : null, setItem: (k, v) => { data[k] = String(v); }, removeItem: k => { delete data[k]; } };
}

// The measured SNW S4E2 situation: [0] Turkish file with the subtitle burned in, [1] clean English file + soft VTT.
const TR = { url: 'https://cdn/tr.m3u8', type: 'hls', label: 'VidMolly · Türkçe altyazı · auto', variant_id: 'v_tr', audio_lang: 'en', sub_mode: 'hard', hard_lang: 'tr' };
const EN = { url: 'https://cdn/en.m3u8', type: 'hls', label: 'VidMolly · İngilizce altyazı · auto', variant_id: 'v_en', audio_lang: 'en', sub_mode: 'soft', hard_lang: null };
const SUB_EN = { id: 'x1', lang: 'en', label: 'İngilizce', kind: 'captions', format: 'vtt', url: '/api/subtitles/x1.vtt', stream_ids: ['v_en'], default: true, origin: 'soft' };
const AUDIO_EN = { id: 'a_en', lang: 'en', label: 'İngilizce', stream_ids: ['v_tr', 'v_en'], default: true };
const ids = list => list.map(o => o.id);

{
  // ------------------------------------------------ options from the server fields
  const { T } = load();
  const m = T.build([TR, EN], [SUB_EN], [AUDIO_EN]);
  assert.deepEqual(ids(m.subs), ['off', 'h:tr', 's:x1'], 'Kapali, then Turkish before English');
  assert.deepEqual(m.subs.map(o => o.label), ['Kapalı', 'Türkçe', 'İngilizce']);
  assert.equal(m.subs[1].badge, 'gömülü', 'burned-in Turkish is badged');
  assert.equal(m.subs[2].badge, undefined);
  assert.equal(m.audio.length, 1);
  assert.equal(T.hasChoices(m), true);
  const v = T.view(m, { audio: 'a:a_en', sub: 'h:tr' });
  assert.equal(v.audio.dim, true, 'a single audio track: the column is grey');
  assert.equal(v.subs.dim, false);
  assert.deepEqual(v.subs.items.map(i => [i.id, i.current, i.unavailable]), [['off', false, false], ['h:tr', true, false], ['s:x1', false, false]]);
  assert.equal(T.subLabel(m, { audio: 'a:a_en', sub: 'h:tr' }), 'Türkçe (gömülü)');
  assert.equal(T.subLabel(m, { audio: 'a:a_en', sub: 'off' }), 'Kapalı');

  // no track data at all (old server, mock source): no panel
  const plain = T.build([{ url: 'a.mp4', type: 'mp4' }, { url: 'b.mp4', type: 'mp4' }], [], []);
  assert.equal(T.hasChoices(plain), false);
  assert.deepEqual(plain.subs.map(o => o.id), ['off']);
  assert.equal(T.build([{ url: 'https://e/x', type: 'embed', sub_mode: 'hard', hard_lang: 'tr' }], [], []).hard.length, 0, 'embed files are never managed');
  assert.equal(T.hasChoices(T.build([{ url: 'https://e/x', type: 'embed' }], [SUB_EN], [])), false);
  // legacy subtitles[] item without id / stream_ids still becomes a soft option valid everywhere
  const legacy = T.build([{ url: 'a.mp4', type: 'mp4' }], [{ lang: 'tr', label: 'Türkçe', url: 'https://x/tr.vtt' }], []);
  assert.deepEqual(ids(legacy.subs), ['off', 's:0']); assert.equal(legacy.subs[1].ids, null);
  assert.equal(T.build([EN], [{ lang: 'en' }], []).soft.length, 0, 'a track without url is ignored');
  // audio derived from the streams when audio[] is missing
  const derived = T.build([{ ...TR, audio_lang: null }, { ...EN, audio_lang: 'tr' }], [], null);
  assert.deepEqual(derived.audio.map(a => [a.id, a.lang, a.label]), [['a:tr', 'tr', 'Türkçe']], 'unknown audio is a wildcard, not a fake alternative');
  const onlyUnknown = T.build([{ ...TR, audio_lang: null }], [], null);
  assert.deepEqual(onlyUnknown.audio.map(a => [a.id, a.lang, a.label]), [['a:orig', null, 'Orijinal']], 'all unknown: one "Orijinal" entry');

  // several sources: a VidMolly pair + an OK.ru file whose language/subtitle are unknown (server audio[] lists "Orijinal" for it)
  const OK = { url: 'https://ok.test/v.mp4', type: 'mp4', label: 'OK.ru · Türkçe altyazı · 1080p', variant_id: 'v_ok', audio_lang: null, sub_mode: 'none', hard_lang: null };
  const multi = T.build([TR, EN, OK], [SUB_EN], [AUDIO_EN, { id: 'a_orig', lang: null, label: 'Orijinal', stream_ids: ['v_ok'], default: false }]);
  assert.deepEqual(multi.audio.map(a => a.id), ['a:a_en'], 'no fake "Orijinal" next to a known language');
  assert.equal(T.view(multi, { audio: 'a:a_en', sub: 'off' }).audio.dim, true);
  assert.deepEqual(T.candidates(multi, 'a:a_en', 'off'), [1, 2], 'the unknown file fits every audio choice and "off"');
  assert.deepEqual(T.candidates(multi, 'a:a_en', 's:x1'), [1]);
  assert.deepEqual(T.candidates(multi, null, 'h:tr'), [0]);
  assert.deepEqual(T.select(multi, 2, {}), { audio: null, sub: 'off' }, 'no audio marker for a stream whose language is unknown');
  assert.deepEqual(T.select(multi, 2, { prefs: { audio: 'en', sub: 'en' } }), { audio: null, sub: 'off' });
  assert.equal(T.choose(multi, {}).index, 0);
  assert.equal(T.choose(multi, { sub: 'off' }).index, 1, 'off: the first stream that can show no subtitle, server order');
  const okFirst = T.pick(multi, 2, { audio: null, sub: 'off' }, 'sub', 's:x1');
  assert.deepEqual([okFirst.index, okFirst.audio, okFirst.switch], [1, 'a:a_en', true]);
}

{
  // ------------------------------------------------ default rule: soft TR > TR hard-sub stream > EN soft > Kapali
  const { T } = load();
  const both = T.build([TR, EN], [SUB_EN], [AUDIO_EN]);
  let c = T.choose(both, {});
  assert.deepEqual([c.index, c.sub], [0, 'h:tr'], 'no soft Turkish: the Turkish hard-sub file first');
  const SUB_TR = { id: 'x2', lang: 'tr', label: 'Türkçe', url: '/api/subtitles/x2.vtt', stream_ids: ['v_en'] };
  const softTr = T.build([TR, EN], [SUB_EN, SUB_TR], [AUDIO_EN]);
  c = T.choose(softTr, {});
  assert.deepEqual([c.index, c.sub], [1, 's:x2'], 'soft Turkish beats the hard-sub file');
  const onlyEn = T.build([EN], [SUB_EN], [AUDIO_EN]);
  assert.deepEqual([T.choose(onlyEn, {}).index, T.choose(onlyEn, {}).sub], [0, 's:x1'], 'English soft when there is no Turkish');
  const nothing = T.build([{ ...EN, sub_mode: 'none' }, { ...TR, sub_mode: 'none', hard_lang: null }], [], []);
  assert.equal(T.choose(nothing, {}).sub, 'off');
  const dutch = T.build([EN], [{ id: 'nl', lang: 'nl', label: 'Felemenkçe', url: '/n.vtt', stream_ids: ['v_en'] }], []);
  assert.equal(T.choose(dutch, {}).sub, 'off', 'other languages are not switched on by the default rule');

  // preference: language wish (soft or hard), off, unmet -> default (preference is not erased)
  const sel = p => { const r = T.choose(both, p); return [r.index, r.sub]; };
  assert.deepEqual(sel({ sub: 'en' }), [1, 's:x1']);
  assert.deepEqual(sel({ sub: 'tr' }), [0, 'h:tr']);
  assert.deepEqual(sel({ sub: 'off' }), [1, 'off'], 'off = the clean file with no subtitle shown');
  assert.deepEqual(sel({ sub: 'de' }), [0, 'h:tr'], 'unavailable wish falls back to the default rule');
  assert.deepEqual(sel({ sub: 'off', audio: 'en' }), [1, 'off']);
  assert.deepEqual(sel({ sub: 'off', audio: 'orig' }), [1, 'off'], 'unknown audio preference: not a constraint that blocks');
  const hardOnly = T.build([TR], [], [AUDIO_EN]);
  assert.deepEqual([T.choose(hardOnly, { sub: 'off' }).index, T.choose(hardOnly, { sub: 'off' }).sub], [0, 'h:tr'], 'off cannot be honoured on a burned-in file');

  // select(): the valid choice for one playing stream
  assert.deepEqual(T.select(both, 0, { want: { sub: 'off' } }), { audio: 'a:a_en', sub: 'h:tr' }, 'burned-in file: subtitle fixed');
  assert.equal(T.select(both, 1, {}).sub, 's:x1');
  assert.equal(T.select(both, 1, { prefs: { sub: 'off' } }).sub, 'off');
  assert.equal(T.select(both, 1, { prefs: { sub: 'off' }, want: { sub: 's:x1' } }).sub, 's:x1', 'an explicit wish beats the preference');
  assert.equal(T.select(both, 1, { want: { sub: 'h:tr' } }).sub, 's:x1', 'a wish invalid on this stream is ignored');
  assert.deepEqual(T.select(both, 1, { prefs: { sub: 'de' } }), { audio: 'a:a_en', sub: 's:x1' });
}

{
  // ------------------------------------------------ user choices: same stream vs switching variant
  const { T } = load();
  const m = T.build([TR, EN], [SUB_EN], [AUDIO_EN]);
  const onTr = { audio: 'a:a_en', sub: 'h:tr' }, onEn = { audio: 'a:a_en', sub: 's:x1' };
  let r = T.pick(m, 0, onTr, 'sub', 's:x1');
  assert.deepEqual(r, { index: 1, audio: 'a:a_en', sub: 's:x1', switch: true }, 'English subtitle = the clean file');
  r = T.pick(m, 0, onTr, 'sub', 'off');
  assert.deepEqual([r.index, r.sub, r.switch], [1, 'off', true], 'Kapali on a burned-in file switches to the clean file');
  r = T.pick(m, 1, onEn, 'sub', 'off');
  assert.deepEqual([r.index, r.sub, r.switch], [1, 'off', false], 'soft <-> off stays on the same stream');
  r = T.pick(m, 1, { audio: 'a:a_en', sub: 'off' }, 'sub', 's:x1');
  assert.deepEqual([r.index, r.switch], [1, false]);
  r = T.pick(m, 1, onEn, 'sub', 'h:tr');
  assert.deepEqual([r.index, r.sub, r.switch], [0, 'h:tr', true], 'burned-in Turkish = the Turkish file');
  r = T.pick(m, 0, onTr, 'sub', 'h:tr');
  assert.deepEqual([r.index, r.switch], [0, false]);

  // burned-in only: Kapali cannot be honoured -> a note, nothing changes
  const hardOnly = T.build([TR], [], [AUDIO_EN]);
  assert.equal(T.view(hardOnly, { audio: 'a:a_en', sub: 'h:tr' }).subs.items[0].unavailable, true, 'Kapali is greyed out');
  r = T.pick(hardOnly, 0, onTr, 'sub', 'off');
  assert.equal(r.note, T.NOTE_HARD); assert(/gömülü/.test(r.note)); assert.equal(r.index, undefined);

  // two audio tracks (Turkish dub file + English original), each with its own files
  const dub = { url: 'https://cdn/dub.m3u8', type: 'hls', variant_id: 'v_dub', audio_lang: 'tr', sub_mode: 'none' };
  const orig = { ...EN };
  const two = T.build([dub, orig], [SUB_EN], [{ id: 'a_tr', lang: 'tr', label: 'Türkçe', stream_ids: ['v_dub'], default: true },
    { id: 'a_en', lang: 'en', label: 'İngilizce', stream_ids: ['v_en'] }]);
  assert.equal(T.view(two, { audio: 'a:a_tr', sub: 'off' }).audio.dim, false);
  r = T.pick(two, 0, { audio: 'a:a_tr', sub: 'off' }, 'audio', 'a:a_en');
  assert.deepEqual([r.index, r.audio, r.switch], [1, 'a:a_en', true]);
  assert.equal(r.sub, 'off', 'the subtitle choice is kept when the new file supports it');
  r = T.pick(two, 1, { audio: 'a:a_en', sub: 's:x1' }, 'audio', 'a:a_tr');
  assert.deepEqual([r.index, r.audio, r.sub, r.switch], [0, 'a:a_tr', 'off', true], 'soft track does not exist on the dub file -> off');
  r = T.pick(two, 0, { audio: 'a:a_tr', sub: 'off' }, 'sub', 's:x1');
  assert.deepEqual([r.index, r.audio, r.switch], [1, 'a:a_en', true], 'a subtitle only the English file has switches the audio too');
  assert.equal(T.choose(two, { audio: 'en' }).index, 1, 'audio preference picks the file');
  assert.equal(T.choose(two, { audio: 'tr' }).index, 0);
  assert.equal(T.choose(two, { audio: 'fr' }).index, 0, 'unmet audio preference: default order');
}

{
  // ------------------------------------------------ preference persistence (per profile) + playflow ordering
  const store = memory();
  const { T, window } = load('p1', store);
  const m = T.build([TR, EN], [SUB_EN], [AUDIO_EN]);
  assert.deepEqual(T.loadPrefs(), { sub: null, audio: null });
  T.remember(m, 'sub', 's:x1'); assert.equal(store.data['dz_pref_sub_p1'], 'en');
  T.remember(m, 'sub', 'h:tr'); assert.equal(store.data['dz_pref_sub_p1'], 'tr');
  T.remember(m, 'sub', 'off'); assert.equal(store.data['dz_pref_sub_p1'], 'off');
  T.remember(m, 'audio', 'a:a_en'); assert.equal(store.data['dz_pref_audio_p1'], 'en');
  T.remember(T.build([{ ...EN, audio_lang: null }], [], []), 'audio', 'a:orig'); assert.equal(store.data['dz_pref_audio_p1'], 'orig');
  T.remember(T.build([EN], [{ id: 'q', url: '/q.vtt', label: 'Bilinmeyen' }], []), 'sub', 's:q');
  assert.equal(store.data['dz_pref_sub_p1'], 'off', 'a track of unknown language is not remembered as a language');
  assert.deepEqual(T.loadPrefs(), { sub: 'off', audio: 'orig' });
  window.DZ.api.profileId = () => 'p2';
  assert.deepEqual(T.loadPrefs(), { sub: null, audio: null }, 'profile based');
  T.remember(m, 'sub', 's:x1'); assert.equal(store.data['dz_pref_sub_p2'], 'en'); assert.equal(store.data['dz_pref_sub_p1'], 'off');

  // playflow: the first stream to try follows the preference; the server order of the rest is kept
  const list = [TR, EN, { url: 'https://cdn/x.mp4', type: 'mp4', variant_id: 'v_x' }];
  window.DZ.api.profileId = () => 'p9';
  assert.deepEqual(T.prefer(list, [SUB_EN], [AUDIO_EN]).map(s => s.variant_id), ['v_tr', 'v_en', 'v_x'], 'default: Turkish hard-sub first (as the server ordered)');
  store.data['dz_pref_sub_p9'] = 'en';
  assert.deepEqual(T.prefer(list, [SUB_EN], [AUDIO_EN]).map(s => s.variant_id), ['v_en', 'v_tr', 'v_x'], 'preferred English first, others keep their order');
  store.data['dz_pref_sub_p9'] = 'off';
  assert.equal(T.prefer(list, [SUB_EN], [AUDIO_EN])[0].variant_id, 'v_en');
  store.data['dz_pref_sub_p9'] = 'tr';
  assert.deepEqual(T.prefer(list, [SUB_EN], [AUDIO_EN]).map(s => s.variant_id), ['v_tr', 'v_en', 'v_x']);
  assert.strictEqual(T.prefer(list.slice(0, 1), [SUB_EN], [AUDIO_EN])[0], TR);
  const plain = [{ url: 'a.mp4', type: 'mp4' }, { url: 'b.mp4', type: 'mp4' }];
  assert.strictEqual(T.prefer(plain, [], []), plain, 'no track data: the list is returned untouched');
  const embedFirst = [{ url: 'https://e/x', type: 'embed' }, EN];
  assert.equal(T.prefer(embedFirst, [SUB_EN], [])[0].variant_id, 'v_en', 'an embed is never the preferred stream (the file we can observe goes first)');

  // storage that throws / is missing never breaks selection
  const boom = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } };
  for (const s of [boom, null]) {
    const w = load('p1', s);
    assert.deepEqual(w.T.loadPrefs(), { sub: null, audio: null });
    w.T.remember(m, 'sub', 's:x1');   // must not throw
    assert.equal(w.T.choose(w.T.build([TR, EN], [SUB_EN], [AUDIO_EN]), w.T.loadPrefs()).index, 0);
  }
}

{
  // ------------------------------------------------ panel: two columns, dim column, focus keys, actions
  const { DZ, T } = load();
  const parent = el('div');
  const panel = DZ.tracksPanel.create(parent);
  const root = find(parent, 'pl-tp');
  assert(root && root.classList.contains('hidden'), 'closed at start');
  const m = T.build([TR, EN], [SUB_EN], [AUDIO_EN]);
  let sel = { audio: 'a:a_en', sub: 'h:tr' };
  const rowsOf = col => findAll(col, 'pl-tp-item');
  panel.open(T.view(m, sel));
  assert(!root.classList.contains('hidden'));
  const cols = findAll(root, 'pl-tp-col');
  assert.equal(cols.length, 2);
  assert(cols[0].classList.contains('dim'), 'single audio track: grey column'); assert(!cols[1].classList.contains('dim'));
  assert.deepEqual(findAll(cols[0], 'pl-tp-head').map(n => n.textContent).concat(findAll(cols[1], 'pl-tp-head').map(n => n.textContent)), ['SES', 'ALTYAZI']);
  assert.deepEqual(rowsOf(cols[0]).map(n => n.textContent), ['İngilizce']);
  assert.deepEqual(rowsOf(cols[1]).map(n => n.textContent), ['Kapalı', 'Türkçe', 'İngilizce']);
  assert.equal(findAll(rowsOf(cols[1])[1], 'pl-tp-badge')[0].textContent, 'gömülü', 'the badge lives inside the burned-in item');
  assert.equal(findAll(root, 'pl-tp-badge').length, 1);
  assert(rowsOf(cols[1])[1].classList.contains('current') && rowsOf(cols[1])[1].classList.contains('focused'), 'opens on the current subtitle');
  assert(!rowsOf(cols[0])[0].classList.contains('focused'), 'a dim column never takes focus');
  assert(rowsOf(cols[0])[0].classList.contains('current'), 'selected audio is marked');

  assert.equal(panel.key('left'), null); assert(rowsOf(cols[1])[1].classList.contains('focused'), 'left does nothing: the audio column is dim');
  assert.equal(panel.key('up'), null); assert(rowsOf(cols[1])[0].classList.contains('focused'));
  panel.key('up'); assert(rowsOf(cols[1])[0].classList.contains('focused'), 'clamped at the top');
  panel.key('down'); panel.key('down'); panel.key('down');
  assert(rowsOf(cols[1])[2].classList.contains('focused'), 'clamped at the bottom');
  assert.deepEqual(panel.key('enter'), { type: 'select', kind: 'sub', id: 's:x1' });
  assert.deepEqual(panel.key('playpause'), { type: 'select', kind: 'sub', id: 's:x1' });
  assert.deepEqual(panel.key('back'), { type: 'close' });
  assert.deepEqual(panel.key('yellow'), { type: 'close' });
  assert.deepEqual(panel.key('stop'), { type: 'close' });
  assert.equal(panel.key('blue'), null, 'other keys are swallowed by the panel');

  // after applying a choice the marker moves, focus stays
  sel = { audio: 'a:a_en', sub: 's:x1' };
  panel.update(T.view(m, sel));
  const after = rowsOf(findAll(root, 'pl-tp-col')[1]);
  assert(after[2].classList.contains('current') && after[2].classList.contains('focused') && !after[1].classList.contains('current'));

  // notes are shown by the caller and cleared on the next key
  panel.setNote('Bu kaynakta altyazı görüntüye gömülü; kapatılamıyor.');
  assert(/gömülü/.test(find(root, 'pl-tp-note').textContent));
  panel.key('up'); assert.equal(find(root, 'pl-tp-note').textContent, '');
  panel.close(); assert(root.classList.contains('hidden')); assert.equal(panel.key('enter'), null, 'closed panel ignores keys');

  // two active columns: left/right switch columns; unavailable item is still selectable (the caller explains)
  const dub = { url: 'https://cdn/dub.m3u8', type: 'hls', variant_id: 'v_dub', audio_lang: 'tr', sub_mode: 'none' };
  const two = T.build([dub, EN], [SUB_EN], [{ id: 'a_tr', lang: 'tr', label: 'Türkçe', stream_ids: ['v_dub'] }, { id: 'a_en', lang: 'en', label: 'İngilizce', stream_ids: ['v_en'] }]);
  panel.open(T.view(two, { audio: 'a:a_tr', sub: 'off' }));
  const c2 = findAll(root, 'pl-tp-col');
  assert(!c2[0].classList.contains('dim') && !c2[1].classList.contains('dim'));
  assert(rowsOf(c2[1])[0].classList.contains('focused'), 'opens on the subtitle column');
  panel.key('left'); assert(rowsOf(c2[0])[0].classList.contains('focused') && !rowsOf(c2[1])[0].classList.contains('focused'));
  panel.key('down');
  assert.deepEqual(panel.key('enter'), { type: 'select', kind: 'audio', id: 'a:a_en' });
  panel.key('right'); assert(rowsOf(c2[1])[0].classList.contains('focused'));
  const hardOnly = T.build([TR], [], [AUDIO_EN]);
  panel.open(T.view(hardOnly, { audio: 'a:a_en', sub: 'h:tr' }));
  const c3 = findAll(root, 'pl-tp-col');
  assert(rowsOf(c3[1])[0].classList.contains('unavail'), 'Kapali greyed out on a burned-in-only source');
  panel.key('up');
  assert.deepEqual(panel.key('enter'), { type: 'select', kind: 'sub', id: 'off' });
  panel.destroy(); assert.equal(root.parentNode, null);
}

{
  // ------------------------------------------------ live S4E2 after the server's "yedek" / OK.ru-without-language change:
  // a spare copy of the Turkish file (same variant_id, mirror_of set) and the OK.ru files (sub_known false, joker audio)
  // must not add a second "Türkçe (gömülü)" row, an audio row or an "Orijinal" option.
  const { T } = load();
  const MIRROR = Object.assign({}, TR, { label: 'VidMolly · Türkçe altyazı · auto · yedek', url: 'https://cdn2/tr.m3u8', mirror_of: 'v_tr:auto', sub_known: true, site_lang_hint: 'tr' });
  const OK = { url: 'https://ok/1080.mp4', type: 'mp4', label: 'OK.ru · 1080p', variant_id: 'v_ok', audio_lang: null, sub_mode: 'none', hard_lang: null, sub_known: false, site_lang_hint: 'tr', mirror_of: null };
  const OK2 = Object.assign({}, OK, { url: 'https://ok/1440.mp4', label: 'OK.ru · 1440p' });
  const audio = [AUDIO_EN, { id: 'a_orig', lang: null, label: 'Orijinal', stream_ids: ['v_ok'], default: false }];
  const streams = [TR, MIRROR, OK2, OK, EN];
  const m = T.build(streams, [SUB_EN], audio);
  assert.deepEqual(ids(m.subs), ['off', 'h:tr', 's:x1'], 'one burned-in Turkish row, however many streams carry it');
  assert.deepEqual(ids(m.audio), ['a:a_en'], 'the unknown-language (OK.ru) audio is a joker, not an option');
  const view = T.view(m, { audio: 'a:a_en', sub: 'h:tr' });
  assert.deepEqual(view.subs.items.map(i => i.label), ['Kapalı', 'Türkçe', 'İngilizce']);
  assert.deepEqual(view.audio.items.map(i => i.label), ['İngilizce']);
  // the spare copy and the primary serve the same (audio, subtitle) pair; the primary comes first in server order
  assert.deepEqual(T.candidates(m, 'a:a_en', 'h:tr'), [0, 1]);
  assert.equal(T.choose(m, {}).index, 0, 'the primary stream is tried before its spare copy');
  assert.deepEqual(T.candidates(m, null, 'off'), [2, 3, 4], 'the OK.ru files (no subtitle known) match "Kapali" like the clean English file; the burned-in ones do not');
  assert.deepEqual(T.candidates(m, 'a:a_en', 'off'), [2, 3, 4], 'joker audio: the unknown-language files fit the English audio choice too');
}

console.log('Tracks: options, default rule, preference persistence, variant switching, panel focus/keys: OK');
