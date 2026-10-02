// Subtitles: WebVTT parser edge cases, binary-search cue lookup, the engine-independent DOM layer (fake clock),
// download (timeout / error / cache). Real fixture: server/tests/fixtures/vidmolly_subtitle_snw_s4e2_en.vtt.
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, find, timers, rng } = require('./_fake_dom');

const CLIENT = path.join(__dirname, '../tizen-client/js');
const FIXTURE = path.join(__dirname, '../server/tests/fixtures/vidmolly_subtitle_snw_s4e2_en.vtt');

function load() {
  const T = timers();
  const window = { DZ: {} };
  const sandbox = { window, document: { createElement: el }, console: { log() {} },
    setTimeout: T.setTimeout, clearTimeout: T.clearTimeout, setInterval: T.setInterval, clearInterval: T.clearInterval };
  vm.runInNewContext(fs.readFileSync(path.join(CLIENT, 'subs.js'), 'utf8'), sandbox);
  return { subs: window.DZ.subs, DZ: window.DZ, T };
}
const tick = async (n = 6) => { for (let i = 0; i < n; i++) await Promise.resolve(); };
const texts = cues => cues.map(c => c.t);

(async () => {
  const { subs, DZ, T } = load();

  // ------------------------------------------------ parser
  {
    const cues = subs.parseVtt(fs.readFileSync(FIXTURE, 'utf8'));
    assert.equal(cues.length, 41, 'header skipped, 40 cues + the Griffin cue');
    assert.deepEqual([cues[0].s, cues[0].e, cues[0].t], [2.503, 4.672, '* *']);
    assert.equal(cues[2].t, 'Previously on\nStar Trek: Strange New Worlds...', 'multi-line cue keeps its line break');
    const g = cues[cues.length - 1];
    assert.deepEqual([g.t, Math.round(g.s * 1000)], ['The Griffin.', 149517], 'MM:SS.mmm without hours');
    assert.equal(subs.findCue(cues, 150).t, 'The Griffin.');
    assert.equal(subs.findCue(cues, 149.516), null);

    // BOM + CRLF + header text + NOTE/STYLE/REGION + ids + settings + HH:MM:SS.mmm + comma + no fraction
    const messy = '﻿WEBVTT - Title\r\nKind: captions\r\n\r\nNOTE a comment\r\nwith --> arrow\r\n\r\nSTYLE\r\n::cue {color:red}\r\n\r\n' +
      'REGION\r\nid:r1\r\n\r\nintro-1\r\n00:00:01.000 --> 00:00:02.500 align:start line:80% position:10%\r\nHello\r\n\r\n' +
      '01:02:03,400 --> 01:02:05,000\r\nSRT style comma\r\n\r\n' +
      '3:04 --> 3:06\r\nNo fraction, one digit minutes\r\n\r\n' +
      '00:10.5 --> 00:11.25\r\nShort fractions\r\n';
    const m = subs.parseVtt(messy);
    assert.deepEqual(texts(m), ['Hello', 'Short fractions', 'No fraction, one digit minutes', 'SRT style comma'], 'sorted by start time');
    assert.deepEqual(m.map(c => [c.s, c.e]), [[1, 2.5], [10.5, 11.25], [184, 186], [3723.4, 3725]]);

    // tags, ASS braces, entities, whitespace, empty lines
    const tagged = 'WEBVTT\n\n00:01.000 --> 00:03.000\n<i>Italic</i> and <b>bold</b>\n<c.yellow>colored</c> <v Bob>Bob speaks\n' +
      '<00:01.500>timed{\\an8} &amp; &lt;i&gt; &#39;q&#39; &quot;x&quot;&nbsp;y &#x263A; &unknown;\n  spaced   out  \n';
    const tg = subs.parseVtt(tagged);
    assert.equal(tg.length, 1);
    assert.equal(tg[0].t, "Italic and bold\ncolored Bob speaks\ntimed & <i> 'q' \"x\" y ☺ &unknown;\nspaced out");

    // a whitespace-only line separates cues like an empty one (trailing spaces are common in real files)
    assert.deepEqual(texts(subs.parseVtt('WEBVTT\n\n00:01.000 --> 00:02.000\nA\n \t \n00:03.000 --> 00:04.000\nB\n')), ['A', 'B']);

    // bad cues are skipped, the rest survive
    const bad = 'WEBVTT\n\n00:01.000 --> nonsense\nbroken\n\n00:05.000 --> 00:04.000\nend before start\n\n00:06.000 --> 00:07.000\n\n\n' +
      '00:08.000 --> 00:09.000\nkept\n\ngarbage block without timing\n\n99:99 --> 99:98\nweird but valid ordering skipped\n';
    assert.deepEqual(texts(subs.parseVtt(bad)), ['kept']);

    // unsorted input is sorted; overlapping cues resolved by the latest start that is still active
    const unsorted = subs.parseVtt('WEBVTT\n\n00:20.000 --> 00:21.000\nlate\n\n00:00.000 --> 00:10.000\nlong\n\n00:02.000 --> 00:03.000\nshort\n');
    assert.deepEqual(texts(unsorted), ['long', 'short', 'late']);
    assert.equal(subs.findCue(unsorted, 2.5).t, 'short');
    assert.equal(subs.findCue(unsorted, 5).t, 'long', 'a long cue stays active after a short overlapping one ended');
    assert.equal(subs.findCue(unsorted, 15), null);
    assert.equal(subs.findCue(unsorted, 20.5).t, 'late');

    for (const bogus of [null, undefined, 42, {}, '', 'WEBVTT', 'not a subtitle file at all']) assert.deepEqual(subs.parseVtt(bogus), [], String(bogus));
  }

  // ------------------------------------------------ cue lookup: boundaries + brute-force equivalence
  {
    const cues = subs.parseVtt('WEBVTT\n\n00:10.000 --> 00:12.000\na\n\n00:12.000 --> 00:13.000\nb\n\n00:20.000 --> 00:25.000\nc\n');
    assert.equal(subs.findCue(cues, 0), null); assert.equal(subs.findCue(cues, 9.999), null);
    assert.equal(subs.findCue(cues, 10).t, 'a', 'start is inclusive');
    assert.equal(subs.findCue(cues, 11.999).t, 'a');
    assert.equal(subs.findCue(cues, 12).t, 'b', 'end is exclusive: the next cue takes over');
    assert.equal(subs.findCue(cues, 13), null); assert.equal(subs.findCue(cues, 19.5), null);
    assert.equal(subs.findCue(cues, 24.99).t, 'c'); assert.equal(subs.findCue(cues, 25), null);
    assert.equal(subs.findCue(cues, NaN), null); assert.equal(subs.findCue(cues, -1), null); assert.equal(subs.findCue([], 5), null); assert.equal(subs.findCue(null, 5), null);
    // random overlapping cues: same answer as a linear scan (latest start among the active ones)
    const r = rng(7);
    let lines = 'WEBVTT\n\n';
    const stamp = x => String(Math.floor(x / 3600)).padStart(2, '0') + ':' + String(Math.floor(x % 3600 / 60)).padStart(2, '0') + ':' + (x % 60).toFixed(3).padStart(6, '0');
    for (let i = 0; i < 3000; i++) {
      const s = r() * 3600, e = s + 0.5 + r() * (i % 40 === 0 ? 300 : 6);
      lines += stamp(s) + ' --> ' + stamp(e) + '\ncue' + i + '\n\n';
    }
    const big = subs.parseVtt(lines);
    assert.equal(big.length, 3000);
    for (let k = 0; k < 400; k++) {
      const t = r() * 3700;
      let want = null;
      for (const c of big) if (c.s <= t && t < c.e && (!want || c.s >= want.s)) want = c;
      const got = subs.findCue(big, t);
      assert.equal(got && got.t, want && want.t, 'lookup at ' + t);
    }
    const t0 = process.hrtime.bigint();
    for (let k = 0; k < 20000; k++) subs.findCue(big, (k * 0.17) % 3600);
    assert(Number(process.hrtime.bigint() - t0) / 1e6 < 500, '20k lookups stay cheap');
  }

  // ------------------------------------------------ layer: fake clock, seek/pause/resume, DOM writes only on change
  {
    const cues = subs.parseVtt('WEBVTT\n\n00:01.000 --> 00:03.000\none\n\n00:05.000 --> 00:07.000\ntwo\nlines\n');
    let now = 0, writes = 0;
    const parent = el('div');
    const layer = subs.createLayer(parent, () => now);
    const box = find(parent, 'pl-subs');
    assert(box && box.parentNode === parent, 'div.pl-subs lives in the given parent');
    let text = '';
    Object.defineProperty(box, 'textContent', { get() { return text; }, set(v) { writes++; text = v; } });
    assert(!box.classList.contains('on'), 'hidden without a cue');
    layer.setCues(cues);
    assert.equal(T.pending(), 1, 'one ~4 Hz timer while a track is active');
    T.advance(250 * 3); assert.equal(text, '', 'no cue yet at t=0');
    now = 1.2; T.advance(250);
    assert.equal(text, 'one'); assert(box.classList.contains('on'));
    const w1 = writes;
    T.advance(250 * 4);
    assert.equal(writes, w1, 'no DOM write while the cue does not change');
    now = 3.2; T.advance(250); assert.equal(text, ''); assert(!box.classList.contains('on'), 'cue ends -> hidden');
    now = 5.5; T.advance(250); assert.equal(text, 'two\nlines');
    // seek backwards / forwards: the next tick shows the right cue (time comes from the engine, not from events)
    now = 1.5; T.advance(250); assert.equal(text, 'one');
    now = 100; T.advance(250); assert.equal(text, '');
    // pause: time frozen -> the cue stays; resume continues
    now = 5.2; T.advance(250); const frozen = text; T.advance(2000); assert.equal(text, frozen); assert.equal(frozen, 'two\nlines');
    // raised while the controls are visible; NaN clock (no engine) leaves the text alone
    layer.setRaised(true); assert(box.classList.contains('up') && box.classList.contains('on'));
    layer.setRaised(false); assert(!box.classList.contains('up'));
    now = NaN; T.advance(250); assert.equal(text, 'two\nlines');
    now = 5.6;
    layer.setCues(cues); assert.equal(T.pending(), 1, 'replacing the cues never stacks timers');
    assert.equal(text, 'two\nlines', 'setCues shows the current cue immediately');
    layer.clear(); assert.equal(text, ''); assert.equal(T.pending(), 0, 'clear stops the timer'); assert(!box.classList.contains('on'));
    layer.setCues(cues); layer.destroy();
    assert.equal(T.pending(), 0); assert.equal(box.parentNode, null);
  }

  // ------------------------------------------------ load: url join, cache, http error, empty file, timeout, sync throw
  {
    const asked = [];
    DZ.api = { img: p => 'http://tv.test' + p };
    subs.fetchText = url => { asked.push(url); return Promise.resolve('WEBVTT\n\n00:01.000 --> 00:02.000\nhi\n'); };
    const a = await subs.load('/api/subtitles/abc.vtt');
    assert.deepEqual(asked, ['http://tv.test/api/subtitles/abc.vtt']); assert.equal(a.length, 1);
    await subs.load('/api/subtitles/abc.vtt');
    assert.equal(asked.length, 1, 'parsed cues are cached per url');
    subs.clearCache();

    for (const failing of [() => Promise.reject(new Error('http_404')), () => Promise.resolve('<html>blocked</html>'), () => { throw new Error('sync'); }]) {
      subs.fetchText = failing;
      let err = null;
      await subs.load('/api/subtitles/dead.vtt').then(() => {}, e => { err = e; });
      assert(err, 'a dead / non-subtitle source rejects (the player then goes silently to Kapali)');
    }
    subs.fetchText = () => new Promise(() => {});
    let timedOut = null;
    const p = subs.load('/api/subtitles/slow.vtt').then(() => {}, e => { timedOut = e; });
    T.advance(subs.LOAD_TIMEOUT_MS - 1); await tick(); assert.equal(timedOut, null);
    T.advance(2); await tick(); assert(timedOut && /timeout/.test(timedOut.message), 'short timeout');
    await p;
    assert.equal(T.pending(), 0, 'no leaked timers');
    subs.fetchText = () => Promise.resolve('WEBVTT\n\n00:01.000 --> 00:02.000\nx\n');
    let quick = null;
    await subs.load('/api/subtitles/quick.vtt', 50).then(c => { quick = c; });
    assert.equal(quick.length, 1); assert.equal(T.pending(), 0, 'the timeout timer is cleared on success');
  }

  console.log('Subtitles: VTT parser edge cases, cue lookup, DOM layer timing, load timeout/error/cache: OK');
})().catch(e => { console.error(e); process.exitCode = 1; });
