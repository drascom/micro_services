// client search controller (js/search.js + api.js) headless test: < 2 chars clears without requests, 2 chars = library only,
// >= 3 chars = library + live /api/search, ~300 ms debounce, stale responses ignored / aborted, sites row drops library items,
// unresponsive sites -> note. Fake XHR + fake timers (no server, no DB). Run: node client/tests/search.js
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { timers } = require('./_fake_dom');

const JS = path.join(__dirname, '../js');
const tick = async (n = 10) => { for (let i = 0; i < n; i++) await Promise.resolve(); };

function env(routes) {
  const T = timers();
  const log = { xhr: [], aborted: [] };
  const sb = { console: { log() {} }, Promise };
  sb.window = sb;
  Object.assign(sb, { setTimeout: T.setTimeout, clearTimeout: T.clearTimeout, setInterval: T.setInterval, clearInterval: T.clearInterval });
  sb.location = { search: '', protocol: 'http:', hostname: 'localhost', port: '8001', host: 'localhost:8001' };
  const ls = { 'dzproto.server': 'http://tv.test:8090' };
  sb.localStorage = { getItem: k => (k in ls ? ls[k] : null), setItem: (k, v) => { ls[k] = String(v); }, removeItem: k => { delete ls[k]; } };
  const held = [];   // responses released manually (to test ordering)
  sb.XMLHttpRequest = function () {
    const x = this;
    x.readyState = 0; x.status = 0; x.responseText = '';
    x.open = (m, u) => { x.m = m; x.u = u; };
    x.setRequestHeader = () => {};
    x.abort = () => { x.aborted = true; log.aborted.push(x.u); };
    x.send = () => {
      log.xhr.push(x.u);
      const r = routes.find(r => r[0].test(x.u));
      const res = r ? r[1](x.u) : { status: 404, body: { error: { message: 'nf' } } };
      const fire = () => { if (x.aborted) return; x.readyState = 4; x.status = res.status || 200; x.responseText = JSON.stringify(res.body || res); x.onreadystatechange(); };
      if (res.hold) held.push(fire); else Promise.resolve().then(fire);
    };
  };
  vm.createContext(sb);
  for (const f of ['api.js', 'search.js']) vm.runInContext(fs.readFileSync(path.join(JS, f), 'utf8'), sb, { filename: f });
  const views = [];
  const ctl = sb.DZP.createSearch({ api: sb.DZP.api, pid: () => 'p1', onChange: v => views.push(v) });
  return { T, log, ctl, views, held, last: () => views[views.length - 1],
    count: re => log.xhr.filter(u => re.test(u)).length };
}

const it = (id, title) => ({ id, title, type: 'series', card: '/img/' + id + '/card?w=342&h=192' });
const CAT = /\/api\/catalog\?/, SRCH = /\/api\/search\?/;

(async () => {
  // (1) < 2 chars: cleared at once, no request; 2 chars: library only (after debounce); 3 chars: + live search
  {
    const e = env([[CAT, () => ({ items: [it('a', 'Dexter')], total: 1 })],
      [SRCH, () => ({ items: [it('a', 'Dexter'), it('r1', 'Dexter: New Blood')], total: 2, remote: true, remote_error: null,
        remote_sites: { yabancidizi: { ok: true, count: 2, ms: 300 } } })]]);
    e.ctl.input('d');
    assert.equal(e.log.xhr.length, 0, '1 char: no request');
    assert.equal(e.last().idle, true, '1 char: idle/cleared');
    e.ctl.input('de');
    e.T.advance(299);
    assert.equal(e.log.xhr.length, 0, 'debounced');
    e.T.advance(1); await tick();
    assert.equal(e.count(CAT), 1, '2 chars: library search');
    assert.equal(e.count(SRCH), 0, '2 chars: no live search');
    assert(/q=de&profile=p1/.test(e.log.xhr[0]), 'query + profile');
    assert.equal(e.last().local.length, 1);
    assert.equal(e.last().remoteOn, false);
    e.ctl.input('dex'); e.T.advance(300); await tick();
    assert.equal(e.count(SRCH), 1, '3 chars: live search too');
    const v = e.last();
    assert.deepEqual(v.local.map(x => x.id), ['a']);
    assert.deepEqual(v.sites.map(x => x.id), ['r1'], 'library item not repeated under Sitelerde');
    assert.equal(v.note, '');
    // same query again -> no new request; shrink below 2 -> cleared immediately
    e.ctl.input('dex '); e.T.advance(300); await tick();
    assert.equal(e.count(SRCH), 1, 'same (trimmed) query not repeated');
    e.ctl.input('x');
    assert.equal(e.last().idle, true); assert.equal(e.last().local.length, 0);
  }
  // (2) fast typing: only the last query is sent; stale in-flight response aborted / ignored
  {
    const e = env([[/q=abc&/, () => ({ hold: true, items: [it('old', 'Old')] })],
      [CAT, () => ({ items: [it('n', 'New')] })], [SRCH, () => ({ items: [], remote_sites: {}, remote_error: null })]]);
    e.ctl.input('ab'); e.ctl.input('abc'); e.T.advance(300); await tick();
    assert.equal(e.count(/q=ab&/), 0, 'debounce collapsed "ab"');
    e.ctl.submit('abcd'); await tick();
    assert(e.log.aborted.some(u => /q=abc&/.test(u)), 'stale requests aborted');
    e.held.forEach(f => f()); await tick();
    assert.deepEqual(e.last().local.map(x => x.id), ['n'], 'stale response ignored');
  }
  // (3) unresponsive site -> note; live search failure -> library kept + note; empty -> empty flag
  {
    const e = env([[CAT, () => ({ items: [] })],
      [SRCH, () => ({ items: [], remote_error: 'yabancidizi: timeout', remote_sites: { yabancidizi: { ok: false, count: 0, ms: 8000, error: 'timeout' }, other: { ok: false, skipped: 'breaker' } } })]]);
    e.ctl.submit('zzzz'); await tick();
    const v = e.last();
    assert.equal(v.empty, true, 'no results');
    assert(/yabancidizi/.test(v.note) && !/other/.test(v.note), 'failed (not skipped) sites named');
    const e2 = env([[CAT, () => ({ items: [it('a', 'A')] })], [SRCH, () => ({ status: 500, body: { error: { message: 'boom' } } })]]);
    e2.ctl.submit('aaa'); await tick();
    assert.equal(e2.last().local.length, 1, 'library results kept');
    assert(/yanıt vermedi/.test(e2.last().note), 'remote failure note');
    e2.ctl.submit('aaa'); await tick();
    assert.equal(e2.count(SRCH), 2, 'submit retries a failed live search');
  }
  console.log('client search: ok');
})().catch(e => { console.error(e); process.exit(1); });
