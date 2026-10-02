// Player episode header + "next episode" card: "S04 B02 · Bölüm adı" under the title, a top-right card in the last ~45 s
// (OK = play now, BACK = cancel, never covers the bottom subtitle/controls area), 5 s auto-advance countdown when the episode ends
// (fake clock), cancellable, consecutive episode only (an unplayable successor is NOT silently skipped), stays within regular seasons / specials,
// no card for movies or the last episode.
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, find, findAll, timers } = require('./_fake_dom');

const CLIENT = path.join(__dirname, '../tizen-client');
const tick = async (n = 10) => { for (let i = 0; i < n; i++) await Promise.resolve(); };

const P = 'dizi';
const ep = (s, n, extra) => Object.assign({ id: `${P}:s${s}:e${n}`, season: s, episode: n, title: `Bölüm ${n}`, air_date: '2020-01-01', availability: { state: 'ready' } }, extra || {});
const season = (n, eps) => ({ season: n, title: `${n}. Sezon`, episodes: eps });
const DETAIL = {
  id: P, type: 'series', title: 'Kayıp Sinyal',
  seasons: [
    season(0, [ep(0, 1, { title: 'Özel' })]),
    season(1, [ep(1, 1), ep(1, 2)]),
    season(4, [ep(4, 1), ep(4, 2, { title: 'Sis' }), ep(4, 3, { title: 'Kırılma' }), ep(4, 4, { availability: { state: 'unavailable' } }),
      ep(4, 5, { title: 'Son' })])
  ]
};
const STREAM = { url: 'https://cdn.test/e.m3u8', type: 'hls', label: 'VidMolly · auto', quality: 'auto', attempt_token: 'tok', kind: 'episode' };

function env(o) {
  o = o || {};
  const T = timers();
  const window = { navigator: { onLine: true }, DZ: {}, localStorage: { getItem: () => null, setItem() {} } };
  const DZ = window.DZ;
  const dur = o.duration || 3600;
  const av = { listeners: [], now: 0, closed: 0, paused: 0,
    open() {}, setDisplayRect() {}, setDisplayMethod() {}, setListener(l) { av.listeners.push(l); }, prepareAsync(ok) { ok(); },
    getDuration() { return dur * 1000; }, getCurrentTime() { return Math.round(av.now * 1000); }, play() {}, pause() { av.paused++; }, stop() {},
    close() { av.closed++; }, seekTo() {} };
  const log = { go: [], back: 0, flows: [], progress: [], detail: 0 };
  DZ.api = { profileId: () => 'p1', img: p => p, progress: p => { log.progress.push(p); return Promise.resolve({}); },
    detail: () => { log.detail++; return o.lateDetail ? new Promise(r => { log.resolveDetail = () => r(o.detail || DETAIL); }) : Promise.resolve(o.detail || DETAIL); },
    streams: () => Promise.resolve({ streams: [STREAM], subtitles: [], audio: [], resume_position: 0, duration: dur }),
    playbackReport: () => Promise.resolve({}) };
  DZ.modal = { open() {} };
  DZ.app = { go: (n, p, r) => log.go.push({ n, p, r }), back: () => { log.back++; } };
  DZ.playflow = { start: opts => log.flows.push(opts) };
  const sandbox = { window, console: { log() {} }, webapis: { avplay: av }, setTimeout: T.setTimeout, clearTimeout: T.clearTimeout,
    setInterval: T.setInterval, clearInterval: T.clearInterval, document: { createElement: el, body: el('body'), documentElement: el('html') } };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(CLIENT, 'js/screens/player.js'), 'utf8'), sandbox);
  const root = el('div');
  const e = { T, av, log, DZ, root, screen: DZ.screens.player,
    press: name => DZ.screens.player.key({ name }),
    q: cls => find(root, cls),
    box: () => find(root, 'pl-next'),
    at(sec) { av.now = sec; const l = av.listeners[av.listeners.length - 1]; l.oncurrentplaytime(Math.round(sec * 1000)); },
    complete() { av.listeners[av.listeners.length - 1].onstreamcompleted(); },
    async start(params) {
      e.screen.enter(root, Object.assign({ itemId: P, episodeId: `${P}:s4:e2`, type: 'series', title: 'Kayıp Sinyal', detail: o.noDetail ? null : DETAIL }, params || {}));
      await tick();
    } };
  return e;
}

(async () => {
  // ---- header line + card window
  {
    const e = await (async () => { const x = env(); await x.start(); return x; })();
    assert.equal(e.q('pl-title').textContent, 'Kayıp Sinyal');
    const head = e.q('pl-ep');
    assert.equal(head.textContent, 'S04 B02 · Sis', 'episode line under the title');
    assert(!head.classList.contains('hidden'));
    const top = find(e.root, 'pl-top').children.map(c => c.className.split(' ')[0]);
    assert.deepEqual(top, ['pl-title', 'pl-ep', 'pl-sub'], 'order: title, episode line, source label');

    e.at(10); e.at(3500);
    assert(!e.box(), 'not before the last 45 s');
    e.at(3554.9);
    assert(!e.box(), '45.1 s left: still no card');
    e.at(3556);
    const box = e.box();
    assert(box, 'card in the last 45 s');
    assert.equal(find(box, 'btn').textContent, 'Şimdi oynat');
    assert.deepEqual(box.children.filter(c => c.tagName === 'P').map(p => p.textContent), ['S04 B03 · Kırılma'], 'next consecutive episode');
    assert(/GERİ: iptal/.test(find(box, 'pl-next-hint').textContent));
    assert(box.parentNode.classList.contains('player') && !box.parentNode.classList.contains('player-ui'), 'child of the player root, sibling of the fading control layer: stays when the controls hide');
    e.at(3557);
    assert.equal(findAll(e.root, 'pl-next').length, 1, 'created once');

    // seeking back out of the window removes it; it returns when re-entering
    e.at(3000); assert(!e.box(), 'rewound: card gone');
    e.at(3560); assert(e.box(), 'back in the window: card again');

    // OK -> play now: progress saved, engine released, next episode through the loading flow, replacing the player
    const before = e.log.progress.length;
    assert.equal(e.press('enter'), true);
    assert.equal(e.log.flows.length, 1);
    const f = e.log.flows[0];
    assert.deepEqual([f.itemId, f.episodeId, f.type, f.replace], [P, `${P}:s4:e3`, 'series', true]);
    assert.strictEqual(f.detail, DETAIL);
    assert(e.log.progress.length > before && e.log.progress[e.log.progress.length - 1].position >= 3560, 'progress of the finished episode saved');
    assert.equal(e.av.closed, 1, 'AVPlay released before the next one is prepared');
    assert.equal(e.log.go.length, 0, 'no black player screen');
    f.onCancel(); assert.equal(e.log.back, 1, 'cancelling the loading modal returns to the detail');
    e.screen.exit();
  }

  // ---- BACK cancels the card (and the auto-advance); a second BACK leaves
  {
    const e = env(); await e.start();
    e.at(3560); assert(e.box());
    assert.equal(e.press('back'), true);
    assert(!e.box(), 'card dismissed'); assert.equal(e.log.back, 0, 'player stays open');
    assert.equal(e.log.flows.length, 0);
    e.at(3570); e.at(3590); assert(!e.box(), 'a cancelled card does not come back');
    e.complete();
    assert.equal(e.log.back, 1, 'episode end after cancelling: no auto-advance, back to the detail');
    assert.equal(e.log.flows.length, 0);
    e.T.advance(20000); assert.equal(e.log.flows.length, 0);
    e.screen.exit();
  }
  {
    const e = env(); await e.start();
    e.at(3560); e.press('back');
    e.press('back');
    assert.equal(e.log.back, 1, 'second BACK leaves the player');
    e.screen.exit();
  }

  // ---- Play/Pause key still pauses; OK on a paused player resumes instead of skipping
  {
    const e = env(); await e.start();
    e.at(3560);
    e.press('playpause');
    assert(e.av.paused >= 1, 'playpause pauses even with the card visible');
    assert.equal(e.log.flows.length, 0);
    e.press('enter');
    assert.equal(e.log.flows.length, 0, 'OK on a paused player resumes playback, does not skip');
    e.press('enter');
    assert.equal(e.log.flows.length, 1, 'playing again: OK = next episode');
    e.screen.exit();
  }

  // ---- episode end without cancelling: 5 s countdown (fake clock), then the next episode
  {
    const e = env(); await e.start();
    e.at(3599); assert(e.box());
    e.complete();
    const btn = find(e.box(), 'btn');
    assert.equal(btn.textContent, 'Şimdi oynat (5)');
    assert(/Birazdan/.test(find(e.box(), 'pl-next-hint').textContent));
    e.T.advance(1000); assert.equal(btn.textContent, 'Şimdi oynat (4)');
    e.T.advance(3000); assert.equal(btn.textContent, 'Şimdi oynat (1)'); assert.equal(e.log.flows.length, 0, 'not yet');
    e.T.advance(1000);
    assert.equal(e.log.flows.length, 1); assert.equal(e.log.flows[0].episodeId, `${P}:s4:e3`);
    e.T.advance(10000); assert.equal(e.log.flows.length, 1, 'advance happens once');
    e.screen.exit();
  }
  // ... OK during the countdown skips the wait; BACK cancels it
  {
    const e = env(); await e.start();
    e.at(3599); e.complete();
    e.T.advance(2000); e.press('enter');
    assert.equal(e.log.flows.length, 1);
    e.screen.exit(); e.T.advance(10000); assert.equal(e.log.flows.length, 1);
  }
  {
    const e = env(); await e.start();
    e.complete();                       // ended without ever entering the window (e.g. seek to the end): countdown starts
    assert.equal(find(e.box(), 'btn').textContent, 'Şimdi oynat (5)');
    e.press('back');
    assert.equal(e.log.back, 1, 'BACK during the countdown leaves the player');
    e.screen.exit();
    e.T.advance(10000); assert.equal(e.log.flows.length, 0, 'cancelled: the timer is gone');
  }

  // ---- no card: last episode, movie, short episode; short episodes still get the end countdown
  {
    const last = env(); await last.start({ episodeId: `${P}:s4:e5` });
    last.at(3560); assert(!last.box(), 'last episode: no next card');
    assert.equal(last.q('pl-ep').textContent, 'S04 B05 · Son');
    last.complete(); assert.equal(last.log.back, 1); assert.equal(last.log.flows.length, 0);
    last.screen.exit();

    const gap = env(); await gap.start({ episodeId: `${P}:s4:e3` });
    gap.at(3560); assert(!gap.box(), 'the consecutive S04 B04 has no source: no card, no silent jump to S04 B05');
    gap.complete(); assert.equal(gap.log.back, 1); assert.equal(gap.log.flows.length, 0);
    gap.screen.exit();

    const movie = env(); await movie.start({ itemId: 'm', episodeId: null, type: 'movie', detail: null, title: 'Film' });
    assert(movie.q('pl-ep').classList.contains('hidden') && movie.q('pl-ep').textContent === '', 'movie: no episode line');
    movie.at(3560); assert(!movie.box());
    movie.screen.exit();

    const short = env({ duration: 300 }); await short.start();
    short.at(290); assert(!short.box(), 'clips under 10 minutes get no early card');
    short.complete(); assert.equal(find(short.box(), 'btn').textContent, 'Şimdi oynat (5)', 'but still the end countdown');
    short.screen.exit();
  }

  // ---- detail arriving late (direct open): header first from the id, then title + next episode
  {
    const e = env({ noDetail: true, lateDetail: true }); await e.start();
    assert.equal(e.q('pl-ep').textContent, 'S04 B02', 'tag from the episode id right away');
    e.at(3560); assert(!e.box(), 'next unknown until the detail arrives');
    e.log.resolveDetail(); await tick();
    assert.equal(e.q('pl-ep').textContent, 'S04 B02 · Sis');
    e.at(3561); assert(e.box(), 'card appears once the next episode is known');
    e.screen.exit();
  }

  // ---- pure rules
  {
    const e = env(); await e.start();
    const next = (id, d) => { const r = e.DZ.player.nextEpisodeOf(d || DETAIL, id, '2026-09-30'); return r && r.id; };
    assert.equal(next(`${P}:s1:e2`), `${P}:s4:e1`, 'regular seasons continue in number order (specials listed first by the server are skipped)');
    assert.equal(next(`${P}:s4:e5`), null, 'last regular episode: specials are not "next"');
    assert.equal(next(`${P}:s0:e1`), null, 'last special: nothing follows');
    assert.equal(next(`${P}:s4:e2`), `${P}:s4:e3`); assert.equal(next(`${P}:s4:e3`), null, 'unplayable successor: not skipped, no target');
    assert.equal(next(`${P}:s4:e4`), `${P}:s4:e5`, 'the (unavailable) episode itself can still have a successor');
    const future = { seasons: [season(1, [ep(1, 1), ep(1, 2, { air_date: '2999-01-01', availability: { state: 'unavailable' } }), ep(1, 3, { air_date: '2999-01-01', availability: { state: 'ready' } })])] };
    assert.equal(next(`${P}:s1:e1`, future), null, 'unaired + not ready successor: no target');
    assert.equal(next(`${P}:s1:e2`, future), `${P}:s1:e3`, 'unaired but ready is playable');
    assert.equal(next('nope'), null); assert.equal(next(null), null);
    assert.equal(e.DZ.player.episodeInfo(DETAIL, `${P}:s0:e1`).label, 'Özel B01 · Özel');
    assert.equal(e.DZ.player.episodeLabel('S01 B10', '10. Bölüm'), 'S01 B10', 'generic "N. Bölüm" title is not repeated');
    assert.equal(e.DZ.player.episodeLabel('S01 B10', ''), 'S01 B10');
    assert.equal(e.DZ.player.episodeInfo({ seasons: [] }, 'x:s2:e5').label, 'S02 B05', 'tag from the id when the list lacks the episode');
    e.screen.exit();
  }

  // ---- layout guard: the card sits at the top right, subtitles/controls at the bottom (no overlap by construction)
  {
    const css = fs.readFileSync(path.join(CLIENT, 'css/player.css'), 'utf8');
    const rule = sel => { const m = new RegExp('\\' + sel + '\\{([^}]*)\\}').exec(css); return m ? m[1] : ''; };
    const next = rule('.pl-next');
    assert(/top:var\(--safe\)/.test(next) && /right:var\(--safe\)/.test(next) && !/bottom:/.test(next), '.pl-next is anchored top-right');
    assert(/bottom:\d+px/.test(rule('.pl-subs')), 'subtitle layer is bottom-anchored');
    assert(/z-index:25/.test(next), 'above the subtitle layer (z 5), below the tracks panel (z 40)');
  }

  console.log('Player next episode: header line, 45 s card, OK/BACK, 5 s countdown (fake clock), consecutive-only next, no card for movie/last: OK');
})().catch(e => { console.error(e); process.exitCode = 1; });
