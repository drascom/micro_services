/* subs.js - altyazi: WebVTT ayristirici, ikili aramayla anlik cue bulma ve MOTOR-BAGIMSIZ DOM katmani
   (div.pl-subs). AVPlay altyazi iziyle ugrasilmaz: metin motorun oynatma zamanindan (eng.now()/eng.cur, AVPlay ve
   HTML5 icin ayni) ~4 Hz ile secilir; seek/duraklat/devam zamanla dogal olarak senkron kalir. ES2017, opsiyonel zincir YOK.
   DZ.subs = { parseVtt, findCue, load, fetchText, createLayer } */
(function (g) {
  'use strict';
  var DZ = g.DZ = g.DZ || {};

  var MAX_CUES = 20000;
  var LOAD_TIMEOUT_MS = 6000;
  var TICK_MS = 250;
  var CACHE_MAX = 3;

  var ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', lrm: '', rlm: '', zwnj: '', zwj: '' };

  function decodeEntities(s) {
    return s.replace(/&(#x[0-9a-f]+|#[0-9]+|[a-z]+);/gi, function (m, e) {
      var c;
      if (e.charAt(0) === '#') {
        c = e.charAt(1) === 'x' || e.charAt(1) === 'X' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
        if (!(c > 0) || c > 0x10FFFF) return '';
        try { return String.fromCodePoint(c); } catch (err) { return ''; }
      }
      var v = ENTITIES[e.toLowerCase()];
      return v === undefined ? m : v;
    });
  }

  /* cue metni: ASS/SRT suslu parantezleri ve <i>/<b>/<c.x>/<v X>/<00:01.000> etiketlerini at, varliklari coz, bos satirlari at */
  function cleanText(lines) {
    var out = [];
    for (var i = 0; i < lines.length; i++) {
      var line = decodeEntities(lines[i].replace(/\{\\[^}]*\}/g, '').replace(/<[^>]*>/g, '')).replace(/\s+/g, ' ').replace(/^ | $/g, '');
      if (line) out.push(line);
    }
    return out.join('\n');
  }

  var STAMP = '((?:\\d+:)?\\d{1,2}:\\d{2}(?:[.,]\\d{1,3})?)';
  var TIMING = new RegExp('^\\s*' + STAMP + '\\s*-->\\s*' + STAMP);
  var PARTS = /^(?:(\d+):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?$/;

  /* "MM:SS.mmm" | "HH:MM:SS.mmm" | ",": saniye (NaN: gecersiz) */
  function seconds(stamp) {
    var m = PARTS.exec(stamp);
    if (!m) return NaN;
    var frac = m[4] ? parseInt((m[4] + '00').slice(0, 3), 10) : 0;
    return (m[1] ? parseInt(m[1], 10) * 3600 : 0) + parseInt(m[2], 10) * 60 + parseInt(m[3], 10) + frac / 1000;
  }

  /* -> [{s, e, t}] baslangica gore sirali; hatali/bos cue atlanir; BOM, CRLF, WEBVTT basligi, NOTE/STYLE/REGION,
     cue kimligi, cue ayarlari (align:.. line:..) ve saatsiz zamanlar desteklenir. Asla firlatmaz. */
  function parseVtt(text) {
    var cues = [];
    if (typeof text !== 'string' || !text) return cues;
    var blocks = text.replace(/^﻿/, '').replace(/\r\n?/g, '\n').split(/\n[ \t]*\n/);
    for (var b = 0; b < blocks.length && cues.length < MAX_CUES; b++) {
      var lines = blocks[b].split('\n');
      var first = lines[0].replace(/^\s+/, '');
      if (/^(NOTE|STYLE|REGION)(\s|$)/.test(first)) continue;
      var k = -1;
      for (var i = 0; i < lines.length && i < 3; i++) { if (lines[i].indexOf('-->') >= 0) { k = i; break; } }
      if (k < 0) continue;
      var m = TIMING.exec(lines[k]);
      if (!m) continue;
      var s = seconds(m[1]), e = seconds(m[2]);
      if (!(s >= 0) || !(e > s)) continue;
      var t = cleanText(lines.slice(k + 1));
      if (!t) continue;
      cues.push({ s: s, e: e, t: t, n: cues.length });
    }
    var sorted = true;
    for (var j = 1; j < cues.length; j++) { if (cues[j].s < cues[j - 1].s) { sorted = false; break; } }
    if (!sorted) cues.sort(function (a, c) { return a.s - c.s || a.n - c.n; });
    return cues;
  }

  /* onceki cue'larin en buyuk bitisi (uzun cue'nun ustunden gecen kisa cue'lar yuzunden geri taramayi sinirlar) */
  function prefixEnds(cues) {
    var pm = new Array(cues.length), hi = 0;
    for (var i = 0; i < cues.length; i++) { if (cues[i].e > hi) hi = cues[i].e; pm[i] = hi; }
    return pm;
  }

  /* t saniyesinde gorunen cue (birden cok ise en son baslayan); yoksa null. Ikili arama: O(log n). */
  function findCue(cues, t) {
    var n = cues ? cues.length : 0;
    if (!n || !(t >= 0)) return null;
    var pm = cues._pm;
    if (!pm || pm.length !== n) { pm = prefixEnds(cues); try { cues._pm = pm; } catch (e) {} }
    var lo = 0, hi = n - 1, idx = -1;
    while (lo <= hi) {
      var mid = (lo + hi) >> 1;
      if (cues[mid].s <= t) { idx = mid; lo = mid + 1; } else { hi = mid - 1; }
    }
    for (var i = idx; i >= 0 && pm[i] > t; i--) { if (cues[i].e > t) return cues[i]; }
    return null;
  }

  /* ---------- indirme: kisa zaman asimi, hata -> reddet (cagiran sessizce Kapali'ya gecer) ---------- */
  function fullUrl(url) { return DZ.api && DZ.api.img ? DZ.api.img(url) : String(url || ''); }

  var subs = { parseVtt: parseVtt, findCue: findCue, seconds: seconds, LOAD_TIMEOUT_MS: LOAD_TIMEOUT_MS, TICK_MS: TICK_MS };

  /* testlerde degistirilebilir: (tam url) -> Promise<metin> */
  subs.fetchText = function (url) {
    return fetch(url, { mode: 'cors', cache: 'default' }).then(function (res) {
      if (!res.ok) throw new Error('http_' + res.status);
      return res.text();
    });
  };

  var cache = {}, cacheOrder = [];
  function remember(url, cues) {
    if (!cache[url]) cacheOrder.push(url);
    cache[url] = cues;
    while (cacheOrder.length > CACHE_MAX) delete cache[cacheOrder.shift()];
  }

  subs.load = function (url, timeoutMs) {
    var full = fullUrl(url);
    if (cache[full]) return Promise.resolve(cache[full]);
    return new Promise(function (resolve, reject) {
      var done = false;
      var timer = setTimeout(function () { if (!done) { done = true; reject(new Error('timeout')); } }, timeoutMs || LOAD_TIMEOUT_MS);
      function finish(err, cues) {
        if (done) return;
        done = true;
        clearTimeout(timer);
        if (err) reject(err); else resolve(cues);
      }
      var p;
      try { p = subs.fetchText(full); } catch (e) { finish(e); return; }
      p.then(function (txt) {
        var cues = parseVtt(txt);
        if (!cues.length) throw new Error('empty');
        remember(full, cues);
        finish(null, cues);
      }).then(null, function (err) { finish(err || new Error('failed')); });
    });
  };

  subs.clearCache = function () { cache = {}; cacheOrder = []; };

  /* ---------- katman: div.pl-subs ----------
     parent'a eklenir (kontrol katmaninin KARDESI: kontroller gizlenince de gorunur). getTime() = oynatma saniyesi.
     DOM yalnizca cue DEGISINCE yazilir. setRaised(true): kontroller gorunurken metin ilerleme cubugunun ustune cikar. */
  subs.createLayer = function (parent, getTime) {
    var el = document.createElement('div');
    el.className = 'pl-subs';
    parent.appendChild(el);
    var L = { el: el, cues: null, cur: null, timer: null, raised: false, offset: 0 };

    function paint() {
      el.className = 'pl-subs' + (L.cur ? ' on' : '') + (L.raised ? ' up' : '');
    }
    L.tick = function () {
      var t = getTime ? getTime() : NaN;
      if (typeof t !== 'number' || t !== t) return;
      var c = L.cues ? findCue(L.cues, t + L.offset) : null;
      if (c === L.cur) return;
      L.cur = c;
      el.textContent = c ? c.t : '';
      paint();
    };
    L.stop = function () { if (L.timer) { clearInterval(L.timer); L.timer = null; } };
    L.clear = function () {
      L.stop();
      L.cues = null;
      if (L.cur || el.textContent) { L.cur = null; el.textContent = ''; }
      paint();
    };
    L.setCues = function (cues) {
      L.stop();
      L.cues = cues && cues.length ? cues : null;
      L.cur = null;
      el.textContent = '';
      paint();
      if (L.cues) { L.timer = setInterval(L.tick, TICK_MS); L.tick(); }
    };
    L.setRaised = function (on) { on = !!on; if (L.raised !== on) { L.raised = on; paint(); } };
    L.destroy = function () {
      L.clear();
      if (el.parentNode) el.parentNode.removeChild(el);
    };
    return L;
  };

  DZ.subs = subs;
})(window);
