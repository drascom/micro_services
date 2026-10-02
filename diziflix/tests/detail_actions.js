// Detail action buttons: actions[] drives them (Devam Et · S04 B02 / İlk Bölümden Başla / Oynat / Fragmanı Oynat), old fields are the fallback,
// a dead trailer stays visible as a grey disabled "Fragman yok" (focusable; Enter = info toast, never a playback attempt),
// and no button is drawn when the title has no trailer record at all. List errors surface as a toast.
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, find, findAll } = require('./_fake_dom');

const src = fs.readFileSync(path.join(__dirname, '../tizen-client/js/screens/detail.js'), 'utf8');
const tick = async (n = 6) => { for (let i = 0; i < n; i++) await Promise.resolve(); };

const P = 'dizi';
const ep = (s, n, extra) => Object.assign({ id: `${P}:s${s}:e${n}`, season: s, episode: n, title: `Bölüm ${n}`, overview: '', runtime: 42,
  still: '', still_url: '', has_still: false, air_date: '2020-01-01', availability: { state: 'ready' }, progress: null }, extra || {});
const season = (n, eps) => ({ season: n, title: `${n}. Sezon`, name: `${n}. Sezon`, overview: '', air_date: null, poster_url: '', has_poster: false,
  episode_count: eps.length, episodes: eps });
const seriesData = extra => Object.assign({ id: P, type: 'series', title: 'Dizi', overview: '', genres: [], backdrop: '', similar: [],
  availability: { state: 'ready', reason: null, has_trailer: false }, in_mylist: false,
  seasons: [season(1, [ep(1, 1), ep(1, 2)]), season(4, [ep(4, 1), ep(4, 2)])] }, extra || {});
const movieData = extra => Object.assign({ id: 'm', type: 'movie', title: 'Film', overview: '', genres: [], backdrop: '', similar: [], seasons: [],
  availability: { state: 'ready', reason: null, has_trailer: false }, playback: 'video', in_mylist: false, progress: null }, extra || {});

async function open(data, o) {
  o = o || {};
  const flows = [], toasts = [], gos = [];
  const window = { requestAnimationFrame: f => f(), Image: function () { return el('img'); }, DZ: {} };
  const DZ = window.DZ;
  DZ.nav = { setRoot() {}, onFocus() {}, focusRowById() { return true; }, focusIndex() {}, currentEl() { return null; }, refresh() {},
    restoreNext() {}, rowIds() { return []; }, current() { return null; } };
  DZ.api = { profileId: () => 'p1', detail: () => Promise.resolve(data), removeMyList: () => o.listError ? Promise.reject(new Error('sunucu yok')) : Promise.resolve({}),
    addMyList: () => o.listError ? Promise.reject(new Error('sunucu yok')) : Promise.resolve({}) };
  DZ.card = { sized: x => x || '', sizedTo: (p) => p };
  DZ.skeleton = { detail: () => el('div') };
  DZ.row = { create() { return el('section'); } };
  DZ.modal = { text() { throw new Error('no modal expected'); }, open() {} };
  DZ.toast = { show: m => toasts.push(m), errorText: e => e.message, isOffline: () => false };
  DZ.playflow = { start: o2 => flows.push(o2) };
  DZ.app = { go: (n, p) => gos.push({ n, p }), back() {} };
  vm.runInNewContext(src, { window, Image: window.Image, document: { createElement: el }, console: { log() {} }, setTimeout, clearTimeout });
  const root = el('div');
  DZ.screens.detail.enter(root, { id: data.id });
  await tick();
  const actionsRow = find(root, 'detail-actions');
  const buttons = () => actionsRow.children;
  return { DZ, root, flows, toasts, gos, buttons, labels: () => buttons().map(b => b.textContent), helpers: DZ.screens.detail.helpers };
}
const isPrimary = b => b.classList.contains('primary');

(async () => {
  // ---- A) series with progress: resume target + restart + trailer, from actions[]
  {
    const d = seriesData({
      availability: { state: 'ready', reason: null, has_trailer: true, trailer: 'ok' },
      progress: { episode_id: `${P}:s4:e2`, position: 56, duration: 3000, pct: 2 },
      resume: { episode_id: `${P}:s4:e2`, position: 56 },
      actions: [{ kind: 'resume_episode', item_id: P, episode_id: `${P}:s4:e2`, position: 56 }, { kind: 'play_trailer', item_id: P }]
    });
    const t = await open(d);
    assert.deepEqual(t.labels(), ['Devam Et · S04 B02', 'İlk Bölümden Başla', 'Fragmanı Oynat', 'Listeme Ekle']);
    assert(isPrimary(t.buttons()[0]) && !isPrimary(t.buttons()[1]), 'only the resume button is primary');
    assert(t.buttons().every(b => b.getAttribute('data-nav') === '1'), 'every button is focusable');
    t.buttons()[0].click();
    assert.deepEqual([t.flows[0].itemId, t.flows[0].episodeId, t.flows[0].kind], [P, `${P}:s4:e2`, 'video'], 'resume goes to the action target episode');
    t.buttons()[1].click();
    assert.deepEqual([t.flows[1].episodeId, t.flows[1].kind], [`${P}:s1:e1`, 'video'], 'restart = first playable episode');
    t.buttons()[2].click();
    assert.deepEqual([t.flows[2].episodeId, t.flows[2].kind], [null, 'trailer']);
    assert.equal(t.gos.length, 0, 'playback only through the loading flow');
  }

  // ---- B) series without progress: single play button for the first playable episode; no trailer record -> no trailer button
  {
    const t = await open(seriesData({ actions: [{ kind: 'play_episode', item_id: P, episode_id: `${P}:s1:e1` }] }));
    assert.deepEqual(t.labels(), ['Oynat · S01 B01', 'Listeme Ekle'], 'no "Fragman yok" when no trailer record exists at all');
    t.buttons()[0].click();
    assert.equal(t.flows[0].episodeId, `${P}:s1:e1`);
  }
  // resume target that already is the first playable episode: no duplicate restart button
  {
    const t = await open(seriesData({ actions: [{ kind: 'resume_episode', item_id: P, episode_id: `${P}:s1:e1`, position: 9 }] }));
    assert.deepEqual(t.labels(), ['Devam Et · S01 B01', 'Listeme Ekle']);
  }
  // special episode target uses the "Özel" tag; unplayable first episodes are skipped for the restart button
  {
    const d = seriesData({ seasons: [season(0, [ep(0, 2)]), season(1, [ep(1, 1, { availability: { state: 'unavailable' } }), ep(1, 2)])],
      actions: [{ kind: 'resume_episode', item_id: P, episode_id: `${P}:s0:e2`, position: 9 }] });
    const t = await open(d);
    assert.deepEqual(t.labels(), ['Devam Et · Özel B02', 'İlk Bölümden Başla', 'Listeme Ekle']);
    t.buttons()[1].click();
    assert.equal(t.flows[0].episodeId, `${P}:s1:e2`, 'first PLAYABLE regular episode, specials last');
  }

  // ---- C) movies: resume / play / retry label / no source
  {
    let t = await open(movieData({ actions: [{ kind: 'resume_movie', item_id: 'm', position: 3000 }] }));
    assert.deepEqual(t.labels(), ['Devam Et', 'Listeme Ekle']); assert(isPrimary(t.buttons()[0]));
    t.buttons()[0].click(); assert.equal(t.flows[0].itemId, 'm'); assert.equal(t.flows[0].episodeId, 'm');
    t = await open(movieData({ actions: [{ kind: 'play_movie', item_id: 'm' }] }));
    assert.deepEqual(t.labels(), ['Oynat', 'Listeme Ekle']);
    t = await open(movieData({ availability: { state: 'check_required', reason: null, has_trailer: false }, actions: [{ kind: 'play_movie', item_id: 'm' }] }));
    assert.deepEqual(t.labels(), ['Yeniden Dene', 'Listeme Ekle']);
    t = await open(movieData({ availability: { state: 'unavailable', reason: 'no_video_source', has_trailer: false }, actions: [] }));
    assert.deepEqual(t.labels(), ['Listeme Ekle'], 'actions:[] = nothing playable, nothing invented');
  }

  // ---- D) trailer states
  {
    // dead: visible but grey/disabled, still focusable, Enter = toast, never a playback attempt
    const t = await open(movieData({ availability: { state: 'ready', reason: null, has_trailer: false, trailer: 'dead' },
      actions: [{ kind: 'play_movie', item_id: 'm' }] }));
    assert.deepEqual(t.labels(), ['Oynat', 'Fragman yok', 'Listeme Ekle']);
    const dead = t.buttons()[1];
    assert(dead.classList.contains('disabled') && !isPrimary(dead), 'dead trailer is the grey disabled button');
    assert.equal(dead.getAttribute('data-nav'), '1', 'still focusable (nav skips only [disabled]/data-nav-off)');
    assert.equal(dead.getAttribute('aria-disabled'), 'true');
    assert(!dead.disabled && dead.getAttribute('data-nav-off') === null);
    dead.click();
    assert.equal(t.toasts.length, 1); assert(/fragman/i.test(t.toasts[0]));
    assert.equal(t.flows.length, 0, 'no playback attempt for a dead trailer');
    assert.equal(t.helpers.trailerState({ availability: { has_trailer: false, trailer: 'dead' } }), 'dead');
    assert.equal(t.helpers.trailerState({ availability: { has_trailer: true, trailer: 'dead' } }), 'dead', 'dead wins');
    // a trailer record exists (ok/unknown) but has_trailer is false -> still the grey button
    assert.equal(t.helpers.trailerState({ availability: { has_trailer: false, trailer: 'unknown' } }), 'dead');
    assert.equal(t.helpers.trailerState({ availability: { has_trailer: true, trailer: 'ok' } }), 'live');
    assert.equal(t.helpers.trailerState({ availability: { has_trailer: true } }), 'live');
    assert.equal(t.helpers.trailerState({ playback: 'trailer', availability: {} }), 'live', 'legacy playback=trailer');
    assert.equal(t.helpers.trailerState({ availability: { has_trailer: false } }), 'none', 'no record: nothing is drawn');
    assert.equal(t.helpers.trailerState({}), 'none');
  }
  {
    // trailer-only title (no full source): dead trailer + no play action -> a single disabled button + list button
    const t = await open(movieData({ availability: { state: 'unavailable', reason: 'no_video_source', has_trailer: false, trailer: 'dead' }, playback: 'unavailable', actions: [] }));
    assert.deepEqual(t.labels(), ['Fragman yok', 'Listeme Ekle']);
  }

  // ---- E) legacy server (no actions field): old fields
  {
    let t = await open(movieData({ progress: { episode_id: 'm', position: 60, duration: 100, pct: 60 }, availability: { state: 'ready', has_trailer: true } }));
    assert.deepEqual(t.labels(), ['Devam Et', 'Fragmanı Oynat', 'Listeme Ekle'], 'progress + has_trailer from the old fields');
    t.buttons()[0].click(); assert.equal(t.flows[0].episodeId, 'm');
    t = await open(movieData());
    assert.deepEqual(t.labels(), ['Oynat', 'Listeme Ekle']);
    t = await open(movieData({ playback: 'trailer', availability: { state: 'unavailable', has_trailer: true } }));
    assert.deepEqual(t.labels(), ['Fragmanı Oynat', 'Listeme Ekle'], 'trailer-only legacy title: no Oynat');
    t = await open(seriesData({ resume: { episode_id: `${P}:s4:e1`, position: 30 }, progress: { episode_id: `${P}:s4:e1`, position: 30, duration: 100, pct: 30 } }));
    assert.deepEqual(t.labels(), ['Devam Et · S04 B01', 'İlk Bölümden Başla', 'Listeme Ekle'], 'series fallback from resume/progress');
    t = await open(seriesData({ resume: { episode_id: `${P}:s1:e1`, position: 0 } }));
    assert.deepEqual(t.labels(), ['Oynat · S01 B01', 'Listeme Ekle']);
    t = await open(seriesData({ seasons: [] }));
    assert.deepEqual(t.labels(), ['Listeme Ekle'], 'series without an episode list: no play button');
  }

  // ---- F) helpers + list error toast
  {
    const t = await open(movieData({ actions: [] }));
    assert.equal(t.helpers.epTag(0, 2), 'Özel B02'); assert.equal(t.helpers.epTag(4, 12), 'S04 B12');
    const listBtn = t.buttons()[t.buttons().length - 1];
    assert.equal(listBtn.textContent, 'Listeme Ekle');
    const f = await open(movieData({ actions: [] }), { listError: true });
    f.buttons()[0].click(); await tick();
    assert.deepEqual(f.toasts, ['Listem güncellenemedi: sunucu yok'], 'a failed list update is no longer silent');
    assert.equal(f.buttons()[0].textContent, 'Listeme Ekle', 'label unchanged on failure');
  }

  // ---- G) the censored server samples (docs/api-samples) through the real screen
  {
    const sample = n => JSON.parse(fs.readFileSync(path.join(__dirname, '../docs/api-samples', n), 'utf8'));
    let t = await open(sample('detail-series.json'));
    assert.deepEqual(t.labels(), ['Devam Et · S02 B01', 'İlk Bölümden Başla', 'Fragmanı Oynat', 'Listeme Ekle'], 'series sample: resume_episode + play_trailer');
    t = await open(sample('detail-movie.json'));
    assert.deepEqual(t.labels(), ['Devam Et', 'Fragmanı Oynat', 'Listemden Çıkar'], 'movie sample (already in the list): resume_movie + play_trailer');
    t = await open(sample('detail-movie-no-source.json'));
    assert.deepEqual(t.labels(), ['Listeme Ekle'], 'no source, no trailer record: nothing drawn but the list button');
    t = await open(sample('detail-series-no-episodes.json'));
    assert.deepEqual(t.labels(), ['Fragmanı Oynat', 'Listeme Ekle'], 'trailer-only series');
    const dead = sample('detail-movie.json'); dead.availability = Object.assign({}, dead.availability, { has_trailer: false, trailer: 'dead' });
    dead.actions = dead.actions.filter(a => a.kind !== 'play_trailer');   // what the server sends for a dead trailer
    t = await open(dead);
    assert.deepEqual(t.labels(), ['Devam Et', 'Fragman yok', 'Listemden Çıkar'], 'server-shaped dead trailer');
  }

  console.log('Detail actions: actions[] buttons, resume/restart labels, dead trailer grey button, legacy fallback, list error toast: OK');
})().catch(e => { console.error(e); process.exitCode = 1; });
