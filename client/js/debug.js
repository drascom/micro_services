/* debug.js - "Hata ayıklama" modu (Ayarlar; tercih localStorage `dzproto.debug` = 'summary' | 'detail'; eski '1' = Özet).
   Kapaliyken hicbir sey cizilmez, log()/sum() bos doner ve istekler `debug=1` gondermez.
   Ozet: yalniz sum() satirlari (kaynak aramasi provider basina tek satir, akis denemesi yerinde guncellenen tek satir, hatalar).
   Ayrintili: yalniz log() satirlari (her olay, zaman damgali).
   DZDebug = {
     enabled(), mode() -> 'off'|'summary'|'detail', detailed(), set('off'|'summary'|'detail'|bool)
     log(area, text, cls?)            // Ayrintili satir; area: 'player' | 'detail'; cls: 'ok' | 'bad' | 'skip' | 'head' (renk)
     sum(area, text, cls?, key?)      // Ozet satiri; key verilirse ayni anahtarli satir yerinde guncellenir; ard arda ayni metin -> ×N
     trace(area, steps, prefix?)      // sunucunun debug_trace satirlari (API.md): Ayrintili = her adim, Ozet = provider basina tek satir
     parallel(area, n)                // Ozet: izdeki n. akis vekil-paralel mi
     mount(host, area, opts?)         // paneli host'a ekler; opts: {reset, rows, cls}; reset = alanin satirlarini sil + cihaz satiri
     reset(area, withDevice?), unmount(area), toggle()          // toggle: oynatici panelini gizle/goster (Yesil / masaustu G)
     device(), lines(area)            // cihaz satiri (Tizen surumu / model / AVPlay), testler icin satirlar
   }
   Uzak log: Ozet/Ayrintili acikken log()/sum() satirlari ayrica POST /api/debug-log'a gider (2 sn'de bir toplu, <=50 satir/istek,
   kuyruk <=200, ag hatasinda sessizce atilir). device = model (productinfo) ya da platform + kalici kisa rastgele id
   (`dzproto.devid`). Kapaliyken hic istek yok. */
(function (g) {
  'use strict';
  var K = 'dzproto.debug', MAX = 80;
  var md = null, hidden = false;
  var areas = {};   /* area -> {lines: [{t, text, cls, key?, n?}], t0, host, el, rows, par} */

  function norm(v) { return v === 'detail' ? 'detail' : (v === 'summary' || v === '1' || v === true || v === 'on') ? 'summary' : 'off'; }
  function mode() {
    if (md === null) { try { md = norm(g.localStorage.getItem(K)); } catch (e) { md = 'off'; } }
    return md;
  }
  function enabled() { return mode() !== 'off'; }
  function detailed() { return mode() === 'detail'; }
  function set(v) {
    var prev = mode();
    md = norm(v);
    try { if (md !== 'off') g.localStorage.setItem(K, md); else g.localStorage.removeItem(K); } catch (e) {}
    if (md !== prev) for (var a in areas) if (Object.prototype.hasOwnProperty.call(areas, a)) { areas[a].lines = []; if (md === 'off') unmount(a); }
  }
  function now() { return new Date().getTime(); }
  function area(name) { return areas[name] || (areas[name] = { lines: [], t0: now(), host: null, el: null, rows: 12 }); }

  function device(short) {
    var p = [];
    try {
      var pi = g.webapis && g.webapis.productinfo;
      if (pi) {
        try { p.push((pi.getRealModel && pi.getRealModel()) || pi.getModel()); } catch (e1) {}
        if (!short) try { p.push('FW ' + pi.getFirmware()); } catch (e2) {}
      }
    } catch (e) {}
    try {
      if (g.tizen && g.tizen.systeminfo && g.tizen.systeminfo.getCapability) {
        p.push('Tizen ' + g.tizen.systeminfo.getCapability('http://tizen.org/feature/platform.version'));
      }
    } catch (e3) {}
    if (!short) try { var av = g.webapis && g.webapis.avplay; if (av) p.push('AVPlay' + (av.getVersion ? ' ' + av.getVersion() : '')); } catch (e4) {}
    if (!p.length) {
      var ua = (g.navigator && g.navigator.userAgent) || '';
      var m = /(Tizen [\d.]+|Android [\d.]+|Edg\/[\d]+|Chrome\/[\d]+|Firefox\/[\d]+|Version\/[\d.]+ Safari)/.exec(ua);
      p.push(m ? m[1] : (ua.slice(0, 60) || 'tarayıcı'));
    }
    return p.join(' · ');
  }

  function render(a) {
    if (!a.el || !g.document) return;
    var e = a.el, doc = g.document;
    e.innerHTML = '';
    var sm = !detailed(), list = a.lines.slice(-(sm ? Math.min(a.rows, 8) : a.rows));
    for (var i = 0; i < list.length; i++) {
      var r = doc.createElement('div'), L = list[i], txt = lineText(L);
      if (sm) {
        /* Ozet: sakin; sonuc rengi yalniz bastaki ikonda (✓ ✗ – ▶ …) */
        r.className = 'dbg-l' + (L.cls === 'head' ? ' dbg-head' : '');
        var sp = txt.indexOf(' ');
        if (L.cls && L.cls !== 'head' && sp > 0 && sp <= 2) {
          var ic = doc.createElement('span');
          ic.className = 'dbg-' + L.cls;
          ic.textContent = txt.slice(0, sp);
          r.appendChild(ic);
          var rest = doc.createElement('span');
          rest.textContent = txt.slice(sp);
          r.appendChild(rest);
        } else r.textContent = txt;
      } else {
        r.className = 'dbg-l' + (L.cls ? ' dbg-' + L.cls : '');
        r.textContent = (L.cls === 'head' ? '' : '+' + (L.t / 1000).toFixed(1) + ' s  ') + txt;
      }
      e.appendChild(r);
    }
    e.className = 'dbg' + (sm ? ' dbg-sum' : '') + (a.cls ? ' ' + a.cls : '') + (hidden && a.toggles ? ' off' : '');
  }

  /* ---- uzak log (POST /api/debug-log) ---- */
  var RQ = [], RQ_MAX = 200, RQ_BATCH = 50, RQ_MS = 2000, rqTimer = null, rqBusy = false, devId = null;
  function platform() {
    if (g.tizen) return 'tizen';
    if (g.DZAndroid || /Android/i.test((g.navigator && g.navigator.userAgent) || '')) return 'android';
    return 'browser';
  }
  function deviceId() {
    if (devId) return devId;
    var id = '';
    try { id = g.localStorage.getItem('dzproto.devid') || ''; } catch (e) {}
    if (!/^[a-z0-9]{4,12}$/.test(id)) {
      id = Math.random().toString(36).slice(2, 8) || 'x0';
      try { g.localStorage.setItem('dzproto.devid', id); } catch (e2) {}
    }
    var model = '';
    try { var pi = g.webapis && g.webapis.productinfo; if (pi) model = (pi.getRealModel && pi.getRealModel()) || pi.getModel() || ''; } catch (e3) {}
    devId = (String(model || platform()).replace(/[^A-Za-z0-9_.-]/g, '').slice(0, 32) || 'device') + '-' + id;
    return devId;
  }
  function rbase() { try { return g.DZP && g.DZP.api && g.DZP.api.base ? g.DZP.api.base() : ''; } catch (e) { return ''; } }
  function remote(name, text, cls) {
    if (!enabled() || !g.XMLHttpRequest) return;
    RQ.push({ t: now(), area: name, level: cls === 'bad' ? 'error' : (cls || 'info'), text: String(text).slice(0, 500) });
    if (RQ.length > RQ_MAX) RQ.splice(0, RQ.length - RQ_MAX);
    if (!rqTimer) rqTimer = g.setTimeout(flushRemote, RQ_MS);
  }
  function flushRemote() {
    rqTimer = null;
    if (!enabled()) { RQ = []; return; }
    var B = rbase();
    if (!RQ.length || rqBusy || !B) { if (RQ.length && !rqTimer) rqTimer = g.setTimeout(flushRemote, RQ_MS); return; }
    var lines = RQ.splice(0, RQ_BATCH);
    rqBusy = true;
    function done() { rqBusy = false; if (RQ.length && !rqTimer) rqTimer = g.setTimeout(flushRemote, RQ_MS); }
    try {
      var x = new g.XMLHttpRequest();
      x.open('POST', B + '/api/debug-log', true);
      x.timeout = 8000;
      x.setRequestHeader('Content-Type', 'application/json');
      var ak = g.DZP && g.DZP.api && g.DZP.api.key ? g.DZP.api.key() : '';
      if (ak) x.setRequestHeader(g.DZP.api.keyHeader, ak);
      x.onreadystatechange = function () { if (x.readyState === 4) done(); };
      x.ontimeout = done;
      x.send(JSON.stringify({ device: deviceId(), ua: String((g.navigator && g.navigator.userAgent) || '').slice(0, 200),
        app_ver: String(g.DZ_BUILD || ''), mode: mode(), lines: lines }));
    } catch (e) { done(); }
  }

  function lineText(L) { return L.text + (L.n > 1 ? ' ×' + L.n : ''); }
  function clean(text) { return String(text === undefined || text === null ? '' : text).replace(/\s+/g, ' '); }
  function push(a, L) {
    a.lines.push(L);
    if (a.lines.length > MAX) a.lines.splice(0, a.lines.length - MAX);
    render(a);
  }
  /* Ayrintili satir (Ozet'te yok) */
  function log(name, text, cls) {
    if (!detailed()) return;
    var a = area(name);
    push(a, { t: now() - a.t0, text: clean(text), cls: cls || '' });
    remote(name, clean(text), cls);
  }
  /* Ozet satiri (Ayrintili'da yok): key -> yerinde guncelle; ard arda ayni metin -> ×N */
  function sum(name, text, cls, key) {
    if (mode() !== 'summary') return;
    var a = area(name), t = clean(text), i;
    remote(name, t, cls);
    if (key) for (i = a.lines.length - 1; i >= 0; i--) if (a.lines[i].key === key) {
      a.lines[i].text = t; a.lines[i].cls = cls || ''; a.lines[i].n = 1; render(a); return;
    }
    var last = a.lines[a.lines.length - 1];
    if (!key && last && !last.key && last.text === t && last.cls === (cls || '')) { last.n = (last.n || 1) + 1; render(a); return; }
    push(a, { t: now() - a.t0, text: t, cls: cls || '', key: key || '', n: 1 });
  }

  function mount(host, name, opts) {
    if (!enabled() || !host || !g.document) return null;
    opts = opts || {};
    var a = area(name);
    if (opts.reset) reset(name, true);
    if (a.el && a.el.parentNode) a.el.parentNode.removeChild(a.el);
    a.el = g.document.createElement('div');
    a.rows = opts.rows || 12;
    a.cls = opts.cls || '';
    a.toggles = name === 'player';
    host.appendChild(a.el);
    render(a);
    return a.el;
  }
  function reset(name, withDevice) {
    if (!enabled()) return;
    var a = area(name);
    a.lines = []; a.t0 = now(); a.par = {};
    if (withDevice) a.lines.push({ t: 0, text: device(!detailed()), cls: 'head' });
    render(a);
  }
  function unmount(name) {
    var a = areas[name];
    if (!a) return;
    if (a.el && a.el.parentNode) a.el.parentNode.removeChild(a.el);
    a.el = null;
  }
  function toggle() {
    if (!enabled()) return false;
    hidden = !hidden;
    for (var n in areas) if (Object.prototype.hasOwnProperty.call(areas, n) && areas[n].el) render(areas[n]);
    return true;
  }

  /* ---- sunucu izi (debug_trace) -> Turkce ---- */
  var RES = { found: 'ok', none: 'bad', error: 'bad', timeout: 'bad', skipped: 'skip', info: '' };
  function ms(s) { return s && typeof s.ms === 'number' && s.ms > 0 ? ' · ' + s.ms + ' ms' : ''; }
  function outcome(s) {
    var d = s.detail ? String(s.detail) : '';
    if (s.result === 'found') return d || 'bulundu';
    if (s.result === 'none') return 'kaynak yok' + (d ? ' (' + d + ')' : '');
    if (s.result === 'skipped') return 'atlandı' + (d ? ' (' + d + ')' : '');
    if (s.result === 'timeout') return 'zaman aşımı' + (d ? ' (' + d + ')' : '');
    if (s.result === 'error') return 'hata: ' + (d || '?');
    return d;
  }
  function stepText(s) {
    var who = s.provider || s.site || '', host = s.host ? ' (' + s.host + ')' : '';
    switch (s.stage) {
      case 'sources': return 'Sunucu: ' + (s.detail || '');
      case 'page': return (s.site || 'site') + ' sayfası' + host + ' → ' + outcome(s) + ms(s);
      case 'provider': return (s.provider || 'oynatıcı') + host + ' inceleniyor → ' + outcome(s) + ms(s);
      case 'cache': return (s.site || '') + ' önbellek → ' + outcome(s);
      case 'source': return (s.site || 'kaynak') + host + ' → ' + outcome(s) + ms(s);
      case 'probe': return 'Süre ölçümü · ' + (s.detail || '') + ms(s);
      case 'stream': return (s.result === 'found' ? 'Akış ' : 'Akış elendi: ') + (s.detail || '') + (s.host ? ' · ' + s.host : '');
      case 'finder': return 'Kaynak bulucu' + (who ? ': ' + who : '') + ' → ' + outcome(s) + ms(s);
      default: return (s.stage || '?') + (who ? ' · ' + who : '') + host + ' → ' + outcome(s) + ms(s);
    }
  }
  function trace(name, steps, prefix) {
    if (!enabled() || !steps || !steps.length) return;
    if (detailed()) {
      for (var i = 0; i < steps.length; i++) if (steps[i]) log(name, (prefix || '') + stepText(steps[i]), RES[steps[i].result] || '');
      return;
    }
    traceSummary(name, steps);
  }
  /* Ozet: provider basina TEK satir (en iyi sonuc: bulundu > hata/zaman asimi > yok > atlandi); site sayfasi / kaynak satiri
     yalniz hata/zaman asiminda; info/sources/cache/probe/finder adimlari yok. Akis adimlarindan yalniz "paralel" bilgisi alinir. */
  var RANK = { found: 4, error: 3, timeout: 3, none: 2, skipped: 1 };
  function short(d) { return String(d || '').replace(/\s*\([^)]*\)/g, '').replace(/\s+/g, ' ').trim().slice(0, 40); }
  function traceSummary(name, steps) {
    var a = area(name), order = [], prov = {}, i, s, m;
    a.par = a.par || {};
    for (i = 0; i < steps.length; i++) {
      s = steps[i];
      if (!s) continue;
      if (s.stage === 'stream') {
        m = /^(\d+)\//.exec(String(s.detail || ''));
        if (m && /paralel/.test(String(s.detail))) a.par[+m[1]] = true;
      } else if (s.stage === 'provider') {
        var who = s.provider || s.host || 'oynatıcı', p = prov[who];
        if (!p) { p = prov[who] = { who: who, best: null, n: 0 }; order.push(p); }
        if (s.result === 'found') { m = /(\d+)\s*akış/.exec(String(s.detail || '')); p.n += m ? +m[1] : 1; }
        if (!p.best || (RANK[s.result] || 0) > (RANK[p.best.result] || 0)) p.best = s;
      } else if ((s.stage === 'page' || s.stage === 'source') && (s.result === 'error' || s.result === 'timeout')) {
        order.push({ line: '✗ ' + (s.site || 'site') + (s.stage === 'page' ? ' sayfası' : '') + ' · ' +
          (s.result === 'timeout' ? 'zaman aşımı' : 'hata' + (s.detail ? ': ' + short(s.detail) : '')) });
      }
    }
    for (i = 0; i < order.length; i++) {
      var o = order[i];
      if (o.line) { sum(name, o.line, 'bad'); continue; }
      var b = o.best;
      if (!b || !RANK[b.result]) continue;
      if (b.result === 'found') sum(name, '✓ ' + o.who + ' · ' + o.n + ' akış', 'ok');
      else if (b.result === 'none') sum(name, '✗ ' + o.who + ' · kaynak yok', 'bad');
      else if (b.result === 'skipped') sum(name, '– ' + o.who + ' · atlandı' + (b.detail ? ' (' + short(b.detail) + ')' : ''), 'skip');
      else if (b.result === 'timeout') sum(name, '✗ ' + o.who + ' · zaman aşımı' + (b.ms ? ' ' + (b.ms / 1000).toFixed(1) + ' sn' : ''), 'bad');
      else sum(name, '✗ ' + o.who + ' · hata' + (b.detail ? ': ' + short(b.detail) : ''), 'bad');
    }
  }

  g.DZDebug = {
    enabled: enabled, mode: mode, detailed: detailed, set: set, log: log, sum: sum, reset: reset,
    parallel: function (name, n) { var a = areas[name]; return !!(a && a.par && a.par[n]); }, trace: trace, mount: mount, unmount: unmount, toggle: toggle, device: device,
    stepText: stepText,
    lines: function (name) { var a = areas[name]; return a ? a.lines.map(lineText) : []; },
    hidden: function () { return hidden; },
    deviceId: deviceId, flushRemote: flushRemote
  };
})(window);
