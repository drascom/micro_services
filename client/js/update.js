/* update.js - surum denetimi (GitHub release `diziflix-v*`, drascom/micro_services).
   Android kabugu: indirme/dogrulama native'de (Updater.kt); hazir olunca `DZUpdate.ready(ver)` gelir, istemci ana ekranda
   "Güncelleme hazır" cam penceresi acar, "Şimdi kur" -> `DZAndroid.installUpdate()`. Elle denetim `DZAndroid.checkUpdate()`.
   Tizen (markette degil): ayni API'den en yeni surum; DZ_VERSION'dan buyukse ana ekranda surum basina BIR KEZ bildirim
   ("bilgisayardan kur"). Denetim en cok 6 saatte bir (API siniri 60/saat/IP); ag hatasi sessiz. Tarayici: hicbir sey.
   DZP.update = window.DZUpdate = { kind(), current(), cmp(a,b), pickLatest(releases), check(manual), ready(ver), status(state, v),
     homePrompt() -> null | {type:'install'|'notify', version, title, text}, statusText(), pending(), onChange } */
(function (g) {
  'use strict';
  var DZP = g.DZP = g.DZP || {};
  var API = 'https://api.github.com/repos/drascom/micro_services/releases?per_page=20';
  var PREFIX = 'diziflix-v';
  var PAGE = 'github.com/drascom/micro_services/releases';
  var INTERVAL = 6 * 3600 * 1000;
  var K_LAST = 'dzproto.updLast', K_LATEST = 'dzproto.updLatest', K_SEEN = 'dzproto.updSeen';
  var st = { pending: '', latest: '', state: '', busy: false, prompted: {} };

  function ls(k, v) {
    try {
      if (v === undefined) return g.localStorage ? g.localStorage.getItem(k) : null;
      if (g.localStorage) g.localStorage.setItem(k, String(v));
    } catch (e) {}
    return null;
  }
  function kind() {
    if (g.DZAndroid) return 'android';
    var ua = '';
    try { ua = (g.navigator && g.navigator.userAgent) || ''; } catch (e) {}
    if (g.tizen || /Tizen/i.test(ua)) return 'tizen';
    return 'browser';
  }
  function current() {
    if (g.DZAndroid && g.DZAndroid.version) { try { return String(g.DZAndroid.version()) || '0.0.0'; } catch (e) {} }
    return String(g.DZ_VERSION || '0.0.0');
  }
  function parts(v) {
    var p = String(v || '').split('.'), out = [];
    for (var i = 0; i < 3; i++) out.push(parseInt(p[i], 10) || 0);
    return out;
  }
  function cmp(a, b) {
    var x = parts(a), y = parts(b);
    for (var i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i] - y[i];
    return 0;
  }
  /* yalniz `diziflix-vX.Y.Z`, taslak/on surum degil; en yuksek surum (ya da null) */
  function pickLatest(rels) {
    var best = null;
    for (var i = 0; rels && i < rels.length; i++) {
      var r = rels[i] || {}, tag = String(r.tag_name || '');
      if (r.draft || r.prerelease || tag.indexOf(PREFIX) !== 0) continue;
      var v = tag.substring(PREFIX.length);
      if (!/^\d+\.\d+\.\d+$/.test(v)) continue;
      if (!best || cmp(v, best.version) > 0) best = { version: v, tag: tag, url: r.html_url || ('https://' + PAGE) };
    }
    return best;
  }
  function changed() { if (U.onChange) { try { U.onChange(); } catch (e) {} } }

  function fetchLatest(cb) {
    var x;
    try { x = new g.XMLHttpRequest(); } catch (e) { return cb(e); }
    var done = false;
    function fin(err, v) { if (done) return; done = true; cb(err, v); }
    try {
      x.open('GET', API, true);
      x.timeout = 15000;
      x.setRequestHeader('Accept', 'application/vnd.github+json');
      x.onreadystatechange = function () {
        if (x.readyState !== 4) return;
        if (x.status !== 200) return fin(new Error('HTTP ' + x.status));
        var best = null;
        try { best = pickLatest(JSON.parse(x.responseText)); } catch (e) { return fin(e); }
        fin(null, best ? best.version : '');
      };
      x.ontimeout = function () { fin(new Error('timeout')); };
      x.onerror = function () { fin(new Error('network')); };
      x.send();
    } catch (e2) { fin(e2); }
  }

  /* manual: Ayarlar > Guncellemeleri denetle (6 saat siniri yok, sonuc statusText'te) */
  function check(manual) {
    var k = kind();
    if (k === 'browser') return false;
    if (k === 'android') {
      var p = '';
      try { p = g.DZAndroid.pendingUpdate ? String(g.DZAndroid.pendingUpdate() || '') : ''; } catch (e) {}
      if (p) ready(p);
      if (manual && g.DZAndroid.checkUpdate) { st.state = 'checking'; changed(); try { g.DZAndroid.checkUpdate(); } catch (e2) {} }
      return true;
    }
    var last = parseInt(ls(K_LAST), 10) || 0, now = new Date().getTime();
    if (!manual && now - last < INTERVAL) {
      st.latest = ls(K_LATEST) || '';
      if (!st.state) st.state = st.latest && cmp(st.latest, current()) > 0 ? 'available' : '';
      changed();
      return true;
    }
    if (st.busy) return true;
    st.busy = true;
    if (manual) { st.state = 'checking'; changed(); }
    fetchLatest(function (err, v) {
      st.busy = false;
      if (err) { st.state = manual ? 'error' : st.state; changed(); return; }
      ls(K_LAST, now); ls(K_LATEST, v || '');
      st.latest = v || '';
      st.state = st.latest && cmp(st.latest, current()) > 0 ? 'available' : 'none';
      changed();
    });
    return true;
  }
  /* Android kabugu: guncelleme indirildi + dogrulandi */
  function ready(ver) {
    ver = String(ver || '');
    if (!ver || cmp(ver, current()) <= 0) return;
    st.pending = ver; st.state = 'ready';
    changed();
  }
  /* Android kabugu: checking | none | error (elle denetimin sonucu) */
  function status(state, v) {
    if (st.pending && state !== 'checking') return changed();
    st.state = state;
    changed();
  }
  /* ana ekran gosterildiginde: gosterilecek pencere/bildirim (en fazla bir kez) */
  function homePrompt() {
    var k = kind();
    if (k === 'android' && st.pending && !st.prompted[st.pending]) {
      st.prompted[st.pending] = 1;
      return { type: 'install', version: st.pending, title: 'Güncelleme hazır · ' + st.pending };
    }
    if (k === 'tizen' && st.latest && cmp(st.latest, current()) > 0 && ls(K_SEEN) !== st.latest) {
      ls(K_SEEN, st.latest);
      return { type: 'notify', version: st.latest, title: 'Yeni sürüm ' + st.latest + ' hazır',
        text: 'Bilgisayardan kur (' + PAGE + ')' };
    }
    return null;
  }
  function statusText() {
    var s = st.state, cur = current();
    if (s === 'checking') return 'Denetleniyor…';
    if (s === 'ready' && st.pending) return 'Güncelleme hazır · ' + st.pending + ' · OK: kur';
    if (s === 'available' && st.latest) return 'Yeni sürüm ' + st.latest + ' · bilgisayardan kur';
    if (s === 'none') return 'Güncel · ' + cur;
    if (s === 'error') return 'Denetlenemedi (ağ)';
    return 'Sürüm ' + cur;
  }
  function install() {
    if (!st.pending || !g.DZAndroid || !g.DZAndroid.installUpdate) return false;
    try { g.DZAndroid.installUpdate(); return true; } catch (e) { return false; }
  }

  var U = DZP.update = g.DZUpdate = {
    kind: kind, current: current, cmp: cmp, pickLatest: pickLatest, check: check, ready: ready, status: status,
    homePrompt: homePrompt, statusText: statusText, install: install,
    pending: function () { return st.pending; }, page: PAGE, onChange: null
  };
})(window);
