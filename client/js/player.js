/* player.js - client oynaticisi (ES5, build yok). Eski istemcinin (git gecmisi: screens/player.js + playflow.js + finder.js)
   sahada dogrulanmis mantigi tasindi, gorunum yeni tasarima gore yeniden yazildi.
   - Motor karari `streams[].type`'a (hls|mp4|embed) gore, URL uzantisina DEGIL (uzanti yalniz type yoksa yedek).
     Tizen: varsayilan HTML5 <video> (eski istemci davranisi, sahada dogrulandi; dogal HLS). AVPlay yalniz Ayarlar > Oynatici = AVPlay
     (`dzproto.engine`) secilirse ve webapis.avplay varsa (HLS'i kendisi oynatir; <object> body'de, punch-through = html.av-active).
     Tarayici: <video>; type=hls ve (dogal HLS yok YA DA preferHlsJs(): MSE var + Safari/Tizen/webOS degil) -> js/vendor/hls.min.js tembel, bir kez.
   - Akis sirasi sunucunun; ses/altyazi tercihi varsa ilk akis DZ.tracks.choose ile secilir. Akis hatasi -> /api/playback-report (failure,
     detail <=120, hlsjs, variant_id, stream_url_host, stream_type) + dongusel siradaki (idx+1..n-1, 0..idx-1) dusmemis akis (konumdan);
     her akis oturumda en cok bir kez dusmus sayilir; >=10 sn oynayinca success. Baslama zamanlayicisi ilk karede silinir (20 sn, vekilli mp4
     30 sn, tampon olayinda bir kez +10 sn). Takilma: tek bekleme >15 sn ya da son 60 sn'de toplam >30 sn -> failure `stall` + siradaki akis. Hepsi basarisiz: son raporun yaniti (finder)
     <=2.5 sn beklenir; finder varsa "Kaynak araniyor" (5 sn yoklama, bulununca otomatik yeniden) yoksa "Oynatilamadi" (Geri / Yeniden dene).
   - Ilerleme: 20 sn'de bir + duraklat/seek/cikis/bitis POST /api/progress. Dizide son ~45 sn "Sonraki bolum" karti, bitince 5 sn geri sayim.
   - Altyazi: js/subs.js (VTT, motor-bagimsiz DOM katmani), secim modeli js/tracks.js (ikisi eski istemciden kopya).
   - Canli TV (streams yaniti `live:true`; opts.live): ilerleme/sure/±10/sonraki bolum/progress YOK; "CANLI" rozeti + `no · ad`.
     ↑ / chup (PageUp) = sonraki, ↓ / chdown (PageDown) = onceki kanal (opts.channels = acildigi satir {id, items, total}; gerekirse /api/row ile devami, uclarda
     sarar; hizli basista son secim 400 ms sonra acilir, eski yanitlar loadGen ile yok sayilir), OK (katman gizli) = kanal listesi,
     d0..d9 = numaraya git (1.5 sn). Akislarin hepsi duserse "Yayin yok" (finder yok), kanal degistirme yine calisir.
   DZP.player = { open(opts), key(k), close(), active() } ; opts: {stage, before, pid, itemId, episodeId, kind, position, restart, title, type,
   detail, backdrop, live, channels, channelNo, veil(title, sub), unveil(), onExit(info)}. */
(function (g) {
  'use strict';
  var DZP = g.DZP = g.DZP || {}, doc = g.document;
  var DZ = g.DZ = g.DZ || {};
  var PID = null;
  /* tracks.js / subs.js (eski istemciden kopya) DZ.api.profileId / DZ.api.img / DZ.player.isEmbedStream bekler */
  DZ.api = DZ.api || {};
  DZ.api.profileId = function () { return PID; };
  DZ.api.img = function (u) { return DZP.api && DZP.api.img ? DZP.api.img(u) : String(u || ''); };

  var HIDE_MS = 4000;
  var PROGRESS_MS = 20000;
  var SEEK_STEP = 10;
  var SEEK_COMMIT_MS = 350;
  var NEXT_WINDOW = 60;       /* kart penceresi: surenin son %10'u, en az 60 sn, en cok 300 sn (jenerik basinda) -> checkNext */
  var NEXT_WINDOW_MAX = 300;
  var NEXT_MIN_DURATION = 600;
  var NEXT_COUNT = 5;
  var START_TIMEOUT_MS = 20000, START_PROXIED_MP4_MS = 30000, START_EXTEND_MS = 10000;
  var STALL_ONE_MS = 15000, STALL_WINDOW_MS = 60000, STALL_TOTAL_MS = 30000;
  var K_ENGINE = 'dzproto.engine';
  var FINDER_WAIT_MS = 2500;
  var FINDER_POLL_MS = 5000;
  var FINDER_MAX_MS = 10 * 60 * 1000;
  var BAR_W = 1600;
  var SIDE_ROWS = 8, SIDE_ROW = 76;
  var CH_DEBOUNCE_MS = 400, BAND_MS = 3000, DIGIT_MS = 1500, CH_PAGE = 40;

  function el(tag, cls, txt) {
    var e = doc.createElement(tag);
    if (cls) e.className = cls;
    if (txt !== undefined && txt !== null) e.textContent = txt;
    return e;
  }
  function setT(e, v) { e.style.webkitTransform = v; e.style.transform = v; }
  function pad2(v) { return v < 10 ? '0' + v : String(v); }
  function fmt(sec) {
    var s = Math.max(0, Math.floor(sec || 0)), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    return h > 0 ? (h + ':' + pad2(m) + ':' + pad2(x)) : (m + ':' + pad2(x));
  }
  function clipDetail(d) {
    var t = d === undefined || d === null ? '' : String(d).replace(/\s+/g, ' ').replace(/^ | $/g, '');
    return t.length > 120 ? t.slice(0, 120) : t;
  }
  function cleanPath(u) { return String(u || '').split('#')[0].split('?')[0].toLowerCase(); }
  /* Hata ayiklama paneli (js/debug.js): kapaliyken bos doner */
  function dbgOn() { return !!(g.DZDebug && g.DZDebug.enabled()); }
  function D(t, cls) { if (dbgOn()) g.DZDebug.log('player', t, cls); }            /* Ayrintili satir */
  function SM(t, cls, key) { if (dbgOn()) g.DZDebug.sum('player', t, cls, key); }  /* Ozet satiri (key: yerinde guncellenir) */
  /* Ozet: akis denemesinin tek satiri ("1/3 OK.ru"); sonuc gelince ayni satir guncellenir */
  function attName(cur, i) {
    var s = cur.streams[i] || {}, lb = String(s.label || '').split(' · ')[0] || hostOf(s.url);
    return (i + 1) + '/' + cur.streams.length + ' ' + lb;
  }
  function attKey(cur, i) { return 'att:' + cur.loadGen + ':' + i; }
  function attEnd(cur, ok, text) {
    if (!dbgOn() || cur.attIdx === undefined || cur.attIdx === null) return;
    SM((ok ? '✓ ' : '✗ ') + attName(cur, cur.attIdx) + ' · ' + text, ok ? 'ok' : 'bad', attKey(cur, cur.attIdx));
    cur.attIdx = null;
  }
  function errShort(code, detail) {
    var m = /PLAYER_ERROR_[A-Z_]+/.exec(String(detail || ''));
    if (m) return m[0];
    if (code === 'timeout') return 'zaman aşımı ' + Math.round(((P && P.startLimit) || START_TIMEOUT_MS) / 1000) + ' sn';
    return code + (detail ? ' (' + clipDetail(detail).slice(0, 90) + ')' : '');
  }
  function nowMs() { return new Date().getTime(); }
  function errName(e) {
    if (e === undefined || e === null) return '?';
    if (typeof e !== 'object') return String(e);
    var n = (e.name || '') + (e.message ? (e.name ? ': ' : '') + e.message : '');
    return n || String(e);
  }
  function hostOf(u) { var m = /^[a-z]+:\/\/([^\/?#:]+)/i.exec(String(u || '')); return m ? m[1] : ''; }

  /* ================= motor (eski istemcinin player.js createEngine'inden) ================= */
  function hasAvplay() {
    try { return (typeof g.webapis !== 'undefined') && g.webapis && g.webapis.avplay ? true : false; } catch (e) { return false; }
  }
  /* motor tercihi (Ayarlar > Oynatici, yalniz Tizen): 'auto' = HTML5 <video> | 'avplay' */
  function enginePref() { try { return g.localStorage && g.localStorage.getItem(K_ENGINE) === 'avplay' ? 'avplay' : 'auto'; } catch (e) { return 'auto'; } }
  function setEnginePref(v) {
    try { if (v === 'avplay') g.localStorage.setItem(K_ENGINE, 'avplay'); else g.localStorage.removeItem(K_ENGINE); } catch (e) {}
  }
  function useAvplay() { return hasAvplay() && enginePref() === 'avplay'; }
  /* akisin motoru: sunucu `engine_hint` verdiyse dogrudan o (deneme-yanilma yok; dusen akis siradakine gecer), yoksa varsayilan.
     {av, mode ('hlsjs'|'native'|''), src ('learned'|'seed'|'')} */
  function pickEngine(s) {
    var h = s && s.engine_hint, src = s && s.engine_hint_source === 'learned' ? 'learned' : 'seed';
    if (h === 'avplay' && hasAvplay()) return { av: true, mode: '', src: src };
    if (h === 'hlsjs' && isHlsStream(s.type, s.url) && g.MediaSource) return { av: false, mode: 'hlsjs', src: src };
    if (h === 'native') return { av: false, mode: 'native', src: src };
    return { av: useAvplay(), mode: '', src: '' };
  }
  function engineName(s, pk) {
    if (isEmbedStream(s)) return 'gömülü oynatıcı';
    if (pk.av) return 'AVPlay';
    if (!isHlsStream(s.type, s.url)) return '<video>';
    if (pk.mode === 'hlsjs') return 'hls.js';
    if (pk.mode === 'native') return '<video>';
    return preferHlsJs() || !g.MediaSource ? 'hls.js' : '<video>';
  }
  /* AVPlay tekildir: stop/close rapor gonderildikten sonra (setTimeout 0); yeni AVPlay acilmadan once bekleyen kapatma hemen calisir */
  var avPendingClose = null;
  function avFlushClose() {
    var f = avPendingClose;
    if (!f) return;
    avPendingClose = null;
    g.clearTimeout(f.timer);
    f.run();
  }
  function isEmbedStream(s) {
    if (!s) return false;
    var t = s.type ? String(s.type).toLowerCase() : '';
    if (t) return t === 'embed';
    return !/\.(m3u8|mp4)$/.test(cleanPath(s.url));
  }
  function isHlsStream(type, url) {
    var t = type ? String(type).toLowerCase() : '';
    if (t) return t === 'hls';
    return /\.m3u8$/.test(cleanPath(url));
  }
  DZ.player = DZ.player || {};
  DZ.player.isEmbedStream = isEmbedStream;

  var HLS_SRC = 'js/vendor/hls.min.js?v=hlsjs-1.7.3';
  var hlsWaiters = null;
  function hlsSupported() { try { return !!(g.Hls && g.Hls.isSupported && g.Hls.isSupported()); } catch (e) { return false; } }
  function nativeHls(v) { try { return !!(v && v.canPlayType && v.canPlayType('application/vnd.apple.mpegurl')); } catch (e) { return false; } }
  /* Chrome 14x+ canPlayType("maybe") der ama dogal HLS AES-128/bazi TS'de DEMUXER_ERROR verir: MSE varsa ve Safari/TV degilse hls.js */
  function preferHlsJs() {
    try {
      if (!g.MediaSource) return false;
      var ua = (g.navigator && g.navigator.userAgent) || '';
      if (/Tizen|Web0S|webOS|SMART-TV/i.test(ua)) return false;
      if (/Safari\//.test(ua) && !/Chrome|Chromium|CriOS|Edg|OPR|Firefox|FxiOS|Android/.test(ua)) return false;
      return true;
    } catch (e) { return false; }
  }
  function loadHlsJs(done) {
    if (g.Hls) { done(null); return; }
    if (hlsWaiters) { hlsWaiters.push(done); return; }
    hlsWaiters = [done];
    var s = doc.createElement('script');
    function finish(err) {
      var w = hlsWaiters; hlsWaiters = null;
      if (err && s.parentNode) s.parentNode.removeChild(s);
      for (var i = 0; i < w.length; i++) { try { w[i](err); } catch (e) {} }
    }
    s.onload = function () { finish(g.Hls ? null : 'hls.js yüklenemedi'); };
    s.onerror = function () { finish('hls.js yüklenemedi'); };
    s.async = true;
    s.src = HLS_SRC;
    (doc.head || doc.body || doc.documentElement).appendChild(s);
  }
  function hlsDetail(data) {
    var code = data && data.response && data.response.code ? '/' + data.response.code : '';
    return clipDetail('hls:' + (data && data.type || '?') + '/' + (data && data.details || '?') + code);
  }
  /* tani: hls.js surumu, isSupported, MSE codec destegi, sayfa kokeni (Tizen wgt file:// + CSP blob: sorunu icin) */
  function mseType(t) { try { var M = g.MediaSource; return M && M.isTypeSupported ? (M.isTypeSupported(t) ? '1' : '0') : 'yok'; } catch (e) { return 'hata'; } }
  function hlsEnvInfo() {
    var H = g.Hls, ver = H ? (H.version || '?') : 'yüklenmedi';
    var proto = ''; try { proto = String((g.location && g.location.protocol) || ''); } catch (e) {}
    return 'hls.js ' + ver + ' · isSupported=' + (hlsSupported() ? '1' : '0') + ' · MSE ' + (g.MediaSource ? 'var' : 'yok') +
      ' · avc1.64001f+mp4a=' + mseType('video/mp4; codecs="avc1.64001f,mp4a.40.2"') +
      ' · avc1.42E01E+mp4a=' + mseType('video/mp4; codecs="avc1.42E01E,mp4a.40.2"') + (proto ? ' · ' + proto : '');
  }
  /* video.error.message ozeti: "MEDIA_ELEMENT_ERROR: " oneki atilir (Ozet satirinda yer kalsin) */
  function mediaMsg(v) { return v && v.error && v.error.message ? String(v.error.message).replace(/^MEDIA_ELEMENT_ERROR:\s*/, '') : ''; }

  /* cb: {ready, started, time, buffering, completed, error(message, code, detail)} */
  function createEngine(host, useAv, cb) {
    var eng = { av: useAv, dur: 0, cur: 0, playing: false, prepared: false, playIssued: false, startedFired: false, pendingResume: 0, hlsjs: false };
    function call(n, a, b, c) { var f = cb && cb[n]; if (f) f(a, b, c); }
    function markStarted() {
      if (eng.startedFired || !eng.playIssued) return;
      eng.startedFired = true;
      D('İlk kare · ' + (nowMs() - (eng.t0 || nowMs())) + ' ms', 'ok');
      call('started');
    }

    if (useAv) {
      var AV = g.webapis.avplay;
      avFlushClose();
      var obj = doc.createElement('object');
      obj.id = 'avplayer';
      obj.type = 'application/avplayer';
      obj.setAttribute('width', '1920'); obj.setAttribute('height', '1080');
      /* donanim video katmani transform'lu ebeveyni yok sayar: object sahnenin DISINDA, dogrudan body'de */
      doc.body.appendChild(obj);
      eng.el = obj;
      doc.documentElement.classList.add('av-active');
      eng.prepare = function (url, resume) {
        eng.pendingResume = resume || 0;
        eng.prepared = false; eng.playIssued = false; eng.startedFired = false;
        eng.t0 = nowMs();
        var bufT = 0;
        try {
          D('AVPlay open');
          AV.open(url);
          AV.setDisplayRect(0, 0, 1920, 1080);
          try { AV.setDisplayMethod('PLAYER_DISPLAY_MODE_LETTER_BOX'); } catch (e0) {}
          AV.setListener({
            onbufferingstart: function () { bufT = nowMs(); D('AVPlay onbufferingstart'); call('buffering', true); },
            onbufferingcomplete: function () { D('AVPlay onbufferingcomplete · ' + (bufT ? nowMs() - bufT : 0) + ' ms'); call('buffering', false); },
            oncurrentplaytime: function (ms) {
              eng.cur = (ms || 0) / 1000;
              /* bazi HLS akislarinda prepare sonrasi sure 0/eksik (o anki liste uzunlugu) gelir (Tizen): gecersiz ya da sona yakinken yeniden sor */
              if (!(eng.dur > eng.cur + 120)) { try { var dd = (AV.getDuration() || 0) / 1000; if (dd > eng.dur) eng.dur = dd; } catch (eD) {} }
              markStarted(); call('time');
            },
            onstreamcompleted: function () { D('AVPlay onstreamcompleted'); call('completed'); },
            onerror: function (err) {
              D('AVPlay onerror: ' + errName(err), 'bad');
              call('error', errName(err), /CODEC|NOT_SUPPORTED/i.test(errName(err)) ? 'unsupported' : 'playback_failed', 'avplay:' + errName(err));
            },
            onevent: function (type, data) { D('AVPlay onevent: ' + errName(type) + (data !== undefined && data !== null && data !== '' ? ' ' + String(data).slice(0, 80) : '')); }
          });
          D('AVPlay prepareAsync…');
          AV.prepareAsync(function () {
            try { eng.dur = (AV.getDuration() || 0) / 1000; } catch (e1) { eng.dur = 0; }
            eng.prepared = true;
            D('prepareAsync tamam · ' + (nowMs() - eng.t0) + ' ms · süre ' + fmt(eng.dur), 'ok');
            if (dbgOn()) {
              try {
                var ti = AV.getTotalTrackInfo ? AV.getTotalTrackInfo() : null, parts = [];
                for (var t = 0; ti && t < ti.length && t < 6; t++) parts.push(ti[t].type + ' ' + String(ti[t].extra_info || '').replace(/[{}"]/g, '').slice(0, 60));
                if (parts.length) D('İzler: ' + parts.join(' | '));
              } catch (eT) { D('İzler okunamadı: ' + errName(eT)); }
            }
            eng.start();
          }, function (e) {
            D('prepareAsync hata: ' + errName(e) + ' · ' + (nowMs() - eng.t0) + ' ms', 'bad');
            call('error', 'prepareAsync: ' + errName(e), undefined, 'avplay:prepareAsync ' + errName(e));
          });
        } catch (e) { D('AVPlay open hata: ' + errName(e), 'bad'); call('error', 'avplay open: ' + errName(e), undefined, 'avplay:open ' + errName(e)); }
      };
      eng.start = function () {
        try { AV.setDisplayRect(0, 0, 1920, 1080); } catch (eDR) {}   /* bazi Tizen surumleri prepare sonrasi tekrar ister */
        if (eng.pendingResume > 0) { try { AV.seekTo(Math.round(eng.pendingResume * 1000)); } catch (e2) {} eng.cur = eng.pendingResume; eng.pendingResume = 0; }
        try { AV.play(); eng.playing = true; eng.playIssued = true; } catch (e3) { D('AVPlay play hata: ' + errName(e3), 'bad'); call('error', errName(e3), 'playback_failed', 'avplay:play ' + errName(e3)); return; }
        call('ready');
      };
      eng.load = function (url, resume) { eng.prepare(url, resume); };
      eng.now = function () {
        try { var ms = AV.getCurrentTime(); if (typeof ms === 'number' && ms >= 0) return ms / 1000; } catch (e) {}
        return eng.cur;
      };
      eng.play = function () { try { AV.play(); eng.playing = true; } catch (e) {} };
      eng.pause = function () { try { AV.pause(); eng.playing = false; } catch (e) {} };
      eng.seek = function (sec) {
        var t = Math.max(0, Math.min(eng.dur > 0 && eng.dur >= eng.cur ? eng.dur - 2 : sec, sec));   /* gecersiz sure (< konum) sinir sayilmaz */
        try { AV.seekTo(Math.round(t * 1000)); eng.cur = t; } catch (e) {}
      };
      eng.destroy = function () {
        doc.documentElement.classList.remove('av-active');
        avFlushClose();
        var f = { run: function () {
          try { AV.stop(); } catch (e) {}
          try { AV.close(); } catch (e2) {}
          if (obj.parentNode) obj.parentNode.removeChild(obj);
        } };
        f.timer = g.setTimeout(function () { if (avPendingClose === f) { avPendingClose = null; f.run(); } }, 0);
        avPendingClose = f;
      };
      return eng;
    }

    var v = doc.createElement('video');
    v.setAttribute('playsinline', '');
    v.autoplay = true;
    eng.el = v;
    host.appendChild(v);
    var hls = null, hlsTried = { net: false, media: false }, loadSeq = 0;
    /* bayat olay korumasi: her akis denemesi TAZE bir motor + <video> alir (startStream -> createEngine); yok edilmis motorun
       (eng.dead) ya da src'si henuz atanmamis/sokulmekte olan <video>'nun (eng.armed=false) olaylari yok sayilir */
    eng.dead = false; eng.armed = false; eng.hlsStage = '';
    v.addEventListener('loadedmetadata', function () {
      if (eng.dead) return;
      eng.dur = v.duration || 0; eng.prepared = true;
      D('<video> loadedmetadata · ' + (nowMs() - (eng.t0 || nowMs())) + ' ms · süre ' + fmt(eng.dur));
      eng.start();
    }, false);
    v.addEventListener('timeupdate', function () { if (eng.dead) return; eng.cur = v.currentTime || 0; eng.dur = v.duration || eng.dur; call('time'); }, false);
    v.addEventListener('waiting', function () { if (eng.dead) return; D('<video> waiting (arabellek)'); call('buffering', true); }, false);
    v.addEventListener('playing', function () { if (eng.dead) return; markStarted(); call('buffering', false); }, false);
    v.addEventListener('ended', function () { if (eng.dead) return; call('completed'); }, false);
    v.addEventListener('error', function () {
      var code = v.error ? v.error.code : 0;
      if (eng.dead || !eng.armed) { D('<video> bayat hata yok sayıldı (code=' + code + ')', 'skip'); return; }
      if (hls && code === 3 && !hlsTried.media) { hlsTried.media = true; try { hls.recoverMediaError(); } catch (eR) {} return; }
      var full = v.error && v.error.message ? String(v.error.message) : '';
      D('<video> error: code=' + code + (full ? ' · ' + full : '') + (eng.hlsjs ? ' · hls.js aşama: ' + (eng.hlsStage || 'başlamadı') : ''), 'bad');
      if (eng.hlsjs) {
        D('hls.js ortam: ' + hlsEnvInfo(), 'bad');
        if (/URL safety/i.test(full)) D('→ MSE blob: URL reddedildi: Tizen CSP media-src blob: izni (config.xml tizen:content-security-policy)?', 'bad');
      }
      var det = 'video.error.code=' + code + (full ? ' ' + mediaMsg(v) : '') + (eng.hlsjs ? ' · hls.js@' + (eng.hlsStage || '-') : '');
      call('error', 'Video oynatılamadı', ({ 1: 'aborted', 2: 'network', 3: 'decode', 4: 'playback_failed' })[code] || 'playback_failed', clipDetail(det));
    }, false);
    function stopHls() { var h = hls; hls = null; if (h) { var a = eng.armed; eng.armed = false; try { h.destroy(); } catch (e) {} eng.armed = a; } }
    /* fatal AG hatasi (manifest/level disi) -> startLoad, fatal MEDYA -> recoverMediaError; her biri BIR kez, sonra hata */
    function attachHls(url, resume) {
      var H = g.Hls, ET = H.ErrorTypes || { NETWORK_ERROR: 'networkError', MEDIA_ERROR: 'mediaError' };
      /* sunucumuza giden istekler (vekil disi) erisim anahtari basligini tasir; ucuncu taraf CDN'e asla (api.keyFor) */
      var h = new H({ startPosition: resume > 0 ? resume : -1, xhrSetup: function (xhr, u) {
        var k = DZP.api && DZP.api.keyFor ? DZP.api.keyFor(u) : '';
        if (k) { try { xhr.setRequestHeader(DZP.api.keyHeader, k); } catch (e) {} }
      } });
      hlsTried = { net: false, media: false };
      hls = h; eng.hlsjs = true; eng.hlsStage = 'new';
      D('hls.js ortam: ' + hlsEnvInfo());
      var nonFatal = 0, fragSeen = false, EV = H.Events || {};
      function ms() { return (nowMs() - (eng.t0 || nowMs())) + ' ms'; }
      function onEv(name, stage, fn) {
        if (!EV[name]) return;
        h.on(EV[name], function (ev, data) { if (hls !== h || eng.dead) return; eng.hlsStage = stage; if (fn) fn(data); });
      }
      onEv('MEDIA_ATTACHING', 'attaching', function () { D('hls.js MEDIA_ATTACHING · ' + ms()); });
      onEv('MEDIA_ATTACHED', 'attached', function () { D('hls.js MEDIA_ATTACHED · ' + ms()); });
      onEv('MANIFEST_LOADED', 'manifest-loaded', function () { D('hls.js MANIFEST_LOADED · ' + ms()); });
      onEv('MANIFEST_PARSED', 'manifest', function (data) {
        D('hls.js MANIFEST_PARSED · ' + (data && data.levels ? data.levels.length : '?') + ' seviye · ' + ms());
      });
      onEv('LEVEL_LOADED', 'level', function (data) {
        var d = data && data.details;
        D('hls.js LEVEL_LOADED · ' + (d && d.fragments ? d.fragments.length : '?') + ' parça' + (d && d.live ? ' · canlı' : '') + ' · ' + ms());
      });
      onEv('FRAG_LOADED', 'frag', function (data) {
        if (fragSeen) return; fragSeen = true;
        var f = data && data.frag;
        D('hls.js FRAG_LOADED (ilk) · sn ' + (f && f.sn !== undefined ? f.sn : '?') + ' · ' + ms());
      });
      h.on(H.Events.ERROR, function (ev, data) {
        if (hls !== h || !data || eng.dead) return;
        var errTxt = data.error && data.error.message ? ' · ' + String(data.error.message).slice(0, 160) : (data.reason ? ' · ' + String(data.reason).slice(0, 160) : '');
        if (!data.fatal) { if (++nonFatal <= 5) D('hls.js uyarı: ' + hlsDetail(data) + ' fatal=0' + errTxt, 'skip'); return; }
        D('hls.js hata: ' + hlsDetail(data) + ' fatal=1 · aşama ' + (eng.hlsStage || '-') + errTxt, 'bad');
        var isNet = data.type === ET.NETWORK_ERROR, isMedia = data.type === ET.MEDIA_ERROR;
        if (isNet && !hlsTried.net && !/^(manifest|level)/.test(String(data.details || ''))) { hlsTried.net = true; D('hls.js startLoad (yeniden)'); try { h.startLoad(); } catch (e0) {} return; }
        if (isMedia && !hlsTried.media) { hlsTried.media = true; D('hls.js recoverMediaError'); try { h.recoverMediaError(); } catch (e1) {} return; }
        stopHls();
        call('error', 'Video oynatılamadı', isNet ? 'network' : isMedia ? 'decode' : 'playback_failed', hlsDetail(data));
      });
      eng.armed = true;
      h.loadSource(url);
      h.attachMedia(v);
    }
    /* mode (sunucunun streams[].engine_hint'i): 'hlsjs' = MSE varsa hls.js | 'native' = dogal <video> (dogal HLS yoksa hls.js) | '' = varsayilan */
    eng.prepare = function (url, resume, type, mode) {
      eng.pendingResume = resume || 0;
      eng.prepared = false; eng.playIssued = false; eng.startedFired = false;
      eng.t0 = nowMs();
      var seq = ++loadSeq;
      eng.armed = false;
      stopHls(); eng.hlsjs = false; eng.hlsStage = '';
      var hlsS = isHlsStream(type, url);
      var useJs = mode === 'hlsjs' ? !!g.MediaSource : mode === 'native' ? !nativeHls(v) : (preferHlsJs() || !nativeHls(v));
      if (hlsS && useJs) {
        loadHlsJs(function (err) {
          if (seq !== loadSeq || eng.dead) return;
          if (err) { call('error', err, 'playback_failed', 'hls.js:load'); return; }
          if (hlsSupported()) { attachHls(url, resume || 0); return; }
          /* hls.js desteklenmiyor: hint hlsjs (ya da dogal HLS yok) -> m3u8 <video>'ya ATANMAZ, acik hata + siradaki akis */
          var info = hlsEnvInfo();
          D('hls.js desteklenmiyor · ' + info, 'bad');
          if (mode !== 'hlsjs' && nativeHls(v)) { D('→ doğal HLS <video> ile deneniyor'); eng.armed = true; v.src = url; v.load(); return; }
          call('error', 'hls.js desteklenmiyor', 'unsupported', clipDetail('hls.js desteklenmiyor · ' + info));
        });
        return;
      }
      eng.armed = true;
      v.src = url;
      v.load();
    };
    eng.start = function () {
      if (eng.pendingResume > 0) { try { v.currentTime = eng.pendingResume; } catch (e) {} eng.cur = eng.pendingResume; }
      eng.pendingResume = 0;
      eng.playIssued = true;
      var pr = v.play();
      if (pr && pr.catch) pr.catch(function (e) { if (eng.dead) return; call('error', String(e), e && e.name === 'NotAllowedError' ? 'autoplay' : 'playback_failed', 'play():' + (e && e.name ? e.name : e)); });
      eng.playing = true;
      call('ready');
    };
    eng.load = function (url, resume, type, mode) { eng.prepare(url, resume, type, mode); };
    eng.now = function () { return v.currentTime || eng.cur; };
    eng.play = function () { var p = v.play(); if (p && p.catch) p.catch(function () {}); eng.playing = true; };
    eng.pause = function () { v.pause(); eng.playing = false; };
    eng.seek = function (sec) {
      var t = Math.max(0, Math.min(eng.dur > 0 && eng.dur >= eng.cur ? eng.dur - 1 : sec, sec));
      try { v.currentTime = t; eng.cur = t; } catch (e) {}
    };
    eng.destroy = function () {
      eng.dead = true; eng.armed = false;
      loadSeq++;
      stopHls();
      try { v.pause(); v.removeAttribute('src'); v.load(); } catch (e) {}
      if (v.parentNode) v.parentNode.removeChild(v);
    };
    return eng;
  }

  /* ================= bolum bilgisi + sonraki bolum: ortak js/episodes.js (detaydaki Bolumler paneliyle ayni) ================= */
  var EPS = DZP.episodes, todayIso = EPS.todayIso, episodeInfo = EPS.episodeInfo, nextEpisodeOf = EPS.nextEpisodeOf;

  /* ================= kaynak bulucu (eski istemcinin finder.js'inden) ================= */
  function finderState(f) { var s = f && typeof f === 'object' ? f.state : null; return s === 'searching' || s === 'not_found' ? s : null; }
  function finderMsg(f, isEp) {
    var s = finderState(f);
    if (s === 'searching') return 'Kaynak aranıyor… Bulununca yeniden denenecek. Burada bekleyebilir ya da Geri ile çıkabilirsin.';
    if (s === 'not_found') return isEp === false ? 'Bu film için kaynak bulunamadı.' : 'Bu bölüm için kaynak bulunamadı.';
    return null;
  }
  function finderTitle(f) { var s = finderState(f); return s === 'searching' ? 'Kaynak aranıyor' : (s === 'not_found' ? 'Kaynak bulunamadı' : 'Oynatılamadı'); }

  /* ================= ikonlar ================= */
  function svg(d, fill) {
    return '<svg width="30" height="30" viewBox="0 0 24 24" fill="' + (fill ? 'currentColor' : 'none') + '" stroke="currentColor" stroke-width="' + (fill ? 0 : 2) +
      '" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="' + d + '"/></svg>';
  }
  var IC_PLAY = svg('M7 4.5v15l12.5-7.5z', true);
  var IC_PAUSE = svg('M6.5 4.5h4v15h-4zM13.5 4.5h4v15h-4z', true);
  var IC_RW = svg('M4 12a8 8 0 1 0 2.3-5.6M4 4.5v4h4');
  var IC_FF = svg('M20 12a8 8 0 1 1-2.3-5.6M20 4.5v4h-4');
  var IC_NEXT = '<svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M5 5.5v13l9.5-6.5zM16.5 5.5h2.5v13h-2.5z"/></svg>';

  /* ================= durum ================= */
  var P = null;
  function api() { return DZP.api; }
  function alive(cur) { return P === cur && cur.alive; }
  function isEp() { return !!(P && (P.type === 'series' || (P.episodeId && P.episodeId !== P.itemId))); }
  /* gecerli sure: sonlu, > 0 ve konumdan kucuk degil. Motor suresi (HLS'de 0/NaN/Infinity ya da eksik gelebilir) -> sunucu
     `duration` -> detaydaki bolum/film `runtime` (dk). Hicbiri yoksa 0 (bilinmiyor). */
  function okDur(d, c) { return typeof d === 'number' && isFinite(d) && d > 0 && d >= c - 1 ? d : 0; }
  function runtimeSec() {
    var d = P && P.detail;
    if (!d) return 0;
    if (isEp()) {
      var ss = d.seasons || [];
      for (var i = 0; i < ss.length; i++) for (var j = 0, es = ss[i].episodes || []; j < es.length; j++)
        if (es[j].id === P.episodeId) return es[j].runtime > 0 ? es[j].runtime * 60 : 0;
      return 0;
    }
    return d.runtime > 0 ? d.runtime * 60 : 0;
  }
  function effDur() {
    if (!P || P.live) return 0;
    var e = P.eng, c = e ? (e.cur || 0) : 0;
    return okDur(e && e.dur, c) || okDur(P.duration, c) || okDur(runtimeSec(), c);
  }
  /* kalan sure: kalan sure yazisi + sonraki bolum karti AYNI kaynaktan (effDur - konum); sure bilinmiyorsa -1 */
  function remainSec() { var d = effDur(); return d > 0 && P.eng ? Math.max(0, d - (P.eng.cur || 0)) : -1; }
  function T(name) { if (P && P[name]) { g.clearTimeout(P[name]); g.clearInterval(P[name]); P[name] = null; } }

  function build() {
    var u = {};
    u.root = el('div', 'pl');
    u.poster = el('div', 'pl-poster');
    u.media = el('div', 'pl-media');
    u.subsHost = el('div', 'pl-subshost');
    u.ui = el('div', 'pl-ui');
    u.ui.appendChild(el('div', 'pl-scrim-t'));
    u.ui.appendChild(el('div', 'pl-scrim-b'));
    var top = el('div', 'pl-top');
    u.title = el('div', 'pl-title ell');
    u.ep = el('div', 'pl-ep ell');
    u.src = el('div', 'pl-src ell');
    top.appendChild(u.title); top.appendChild(u.ep); top.appendChild(u.src);
    u.ui.appendChild(top);
    var pn = el('div', 'pl-panel pl-glass');
    u.bar = el('div', 'pl-bar');
    u.fill = el('div', 'pl-fill'); u.knob = el('div', 'pl-knob');
    u.bar.appendChild(u.fill); u.bar.appendChild(u.knob);
    /* fare: ilerleme cubuguna tik = o konuma git. Oran ekran koordinatindan (getBoundingClientRect): sahne olcekli (transform)
       ve tik dolgu/topuz uzerine dusebilir -> offsetX guvenilmez. Hover = cubuk satiri odakta. */
    u.bar.onclick = function (ev) {
      if (ev && ev.stopPropagation) ev.stopPropagation();
      if (!P || !P.eng || P.live || P.ended) return;
      var dur = effDur();
      if (!dur) return;
      var r = null, x;
      try { r = u.bar.getBoundingClientRect(); } catch (e) { r = null; }
      if (r && r.width > 0 && ev && typeof ev.clientX === 'number') x = (ev.clientX - r.left) / r.width;
      else x = (ev && typeof ev.offsetX === 'number' ? ev.offsetX : 0) / BAR_W;
      T('seekTimer'); P.seeking = false; P.holdCount = 0;
      P.row = 0;
      P.eng.seek(Math.max(0, Math.min(1, x)) * dur); paint(); sendProgress(true);
      showUI();
    };
    u.bar.dzHover = function () { if (P && P.uiOn && !P.live && P.row !== 0) { P.row = 0; paintFocus(); } };
    var times = el('div', 'pl-times');
    u.tCur = el('span', '', '0:00'); u.tRem = el('span', '', '');
    times.appendChild(u.tCur); times.appendChild(u.tRem);
    u.state = el('div', 'pl-state');
    u.btns = el('div', 'pl-btns');
    pn.appendChild(u.bar); pn.appendChild(times); pn.appendChild(u.state); pn.appendChild(u.btns);
    u.ui.appendChild(pn);
    u.side = el('div', 'pl-side pl-glass');
    u.next = el('div', 'pl-next pl-glass');
    u.dim = el('div', 'pl-dim');
    u.err = el('div', 'pl-err pl-glass');
    u.hint = el('div', 'pl-ihint', 'Geri: oynatıcıdan çık');
    u.band = el('div', 'pl-band pl-glass');
    u.pn = pn;
    u.root.appendChild(u.poster); u.root.appendChild(u.media); u.root.appendChild(u.subsHost); u.root.appendChild(u.ui);
    u.root.appendChild(u.side); u.root.appendChild(u.next); u.root.appendChild(u.dim); u.root.appendChild(u.err); u.root.appendChild(u.hint);
    u.root.appendChild(u.band);
    /* fare: hareket = katman gorunur (app.js gercek hareketi suzer -> DZP.player.pointer); bos alana tik = oynat/duraklat
       (canli: katman), panel acikken = paneli kapat. Kontrollerin kendi tiklamasi var (cift OK yok). */
    u.root.onclick = function (ev) {
      if (!P || (ev && ev.button) || P.iframe || P.err || !P.shown) return;
      for (var n = ev && ev.target, d = 0; n && n !== u.root && d < 12; n = n.parentNode, d++)
        if (n.onclick || n.dzHover || n === u.pn || n === u.side || n === u.next || n === u.err || n === u.band) return;   /* kontrol / cam kutu ici */
      if (P.panel) { closePanel(); return; }
      if (P.live || P.ended) { showUI(); return; }
      togglePlay();
    };
    return u;
  }

  function veil(sub) { if (P && P.o.veil) P.o.veil('Açılıyor · ' + (P.title || ''), sub || ''); }
  function unveil() { if (P && P.o.unveil) P.o.unveil(); }
  function poster(on) {
    var u = P.ui;
    T('posterTimer');
    if (on) { u.poster.style.display = ''; u.poster.className = 'pl-poster'; return; }
    u.poster.className = 'pl-poster off';
    P.posterTimer = g.setTimeout(function () { if (P) P.ui.poster.style.display = 'none'; }, 500);   /* gorsel bellekten (TV) */
  }

  function chLabel(it) {
    var no = it ? it.channel_no : null;
    return (no ? no + ' · ' : '') + ((it && it.title) || '');
  }
  function curChannel() {
    var i = chIndexOf(P.itemId), l = chList();
    if (i >= 0) return l[i];
    return P.chInfo || { id: P.itemId, title: P.title, channel_no: P.channelNo };
  }
  function header() {
    var u = P.ui;
    u.pn.className = 'pl-panel pl-glass' + (P.live ? ' live' : (P.iframe ? ' emb' : ''));   /* emb: gomulu oynatici, yalniz dugmeler */
    if (P.live) {
      u.title.textContent = chLabel(curChannel());
      u.ep.textContent = '';
      u.ep.appendChild(el('span', 'pl-live', 'CANLI'));
      var s0 = P.streams && P.streams[P.idx];
      u.src.textContent = s0 && s0.label ? s0.label : '';
      return;
    }
    var info = isEp() && P.kind !== 'trailer' ? episodeInfo(P.detail, P.episodeId) : null;
    u.title.textContent = P.title || '';
    u.ep.textContent = P.kind === 'trailer' ? 'Fragman' : (info ? info.label : '');
    var s = P.streams && P.streams[P.idx];
    u.src.textContent = s && s.label ? s.label : '';
  }

  function status(t) { if (P) P.ui.state.textContent = t || ''; }

  function paint() {
    if (!P || !P.eng) return;
    var e = P.eng, pos = P.seeking ? P.seekTarget : e.cur, dur = effDur();
    var r = dur > 0 ? Math.max(0, Math.min(1, pos / dur)) : 0;
    setT(P.ui.fill, 'scaleX(' + r.toFixed(4) + ')');
    setT(P.ui.knob, 'translateX(' + Math.round(r * BAR_W) + 'px) scale(' + (P.row === 0 && P.uiOn ? 1 : 0.6) + ')');
    P.ui.tCur.textContent = fmt(pos);
    P.ui.tRem.textContent = dur > 0 ? ('-' + fmt(dur - pos)) : '';
  }

  function showUI() {
    if (!P) return;
    P.ui.ui.className = 'pl-ui';
    P.uiOn = true;
    if (P.subs) P.subs.setRaised(true);
    paintFocus();
    T('hideTimer');
    P.hideTimer = g.setTimeout(function () {
      if (P && !P.ended && !P.panel && !P.seeking && (P.iframe || (P.eng && P.eng.playing))) hideUI();
    }, HIDE_MS);
  }
  function hideUI() {
    if (!P) return;
    T('hideTimer');
    P.ui.ui.className = 'pl-ui hide';
    P.uiOn = false;
    P.row = P.live || P.iframe ? 1 : 0;
    if (P.subs) P.subs.setRaised(false);
    paintFocus();
  }

  /* ---------- kontrol dugmeleri ---------- */
  function tracksOn() { return !!(P && P.model && P.sel && DZ.tracks && !P.iframe); }
  function episodesList() {
    if (!isEp() || P.kind === 'trailer' || !P.detail) return [];
    return EPS.withEpisodes(P.detail);
  }
  function buttonDefs() {
    if (P.live) {
      var dl = [{ id: 'play' }];
      if (P.streams.length > 1) dl.push({ id: 'source', label: 'Kaynak' });
      if (chList().length > 1) dl.push({ id: 'channels', label: 'Kanallar' });
      return dl;
    }
    var d = P.iframe ? [] : [{ id: 'play' }, { id: 'rw' }, { id: 'ff' }];   /* gomulu oynatici: oynat/sar capraz kokende calismaz */
    if (tracksOn() && P.model.subs.length > 1) d.push({ id: 'subs', label: 'Altyazı', value: DZ.tracks.subLabel(P.model, P.sel) });
    if (tracksOn() && P.model.audio.length > 1) {
      var a = DZ.tracks.byId(P.model.audio, P.sel.audio);
      d.push({ id: 'audio', label: 'Ses', value: a ? a.label : '' });
    }
    if (P.streams.length > 1) d.push({ id: 'source', label: 'Kaynak' });
    if (episodesList().length) d.push({ id: 'episodes', label: 'Bölümler' });
    if (P.next) d.push({ id: 'next', label: 'Sonraki bölüm', icon: IC_NEXT });   /* dizi bolumu + sonraki bolum varsa */
    return d;
  }
  function renderButtons() {
    if (!P) return;
    var u = P.ui, defs = buttonDefs();
    P.btnDefs = defs;
    P.btn = Math.max(0, Math.min(defs.length - 1, P.btn));
    u.btns.innerHTML = '';
    P.btnEls = [];
    for (var i = 0; i < defs.length; i++) {
      var d = defs[i], b = el('div', 'pl-btn');
      b.appendChild(el('div', 'pl-hl'));
      if (d.id === 'play' || d.id === 'rw' || d.id === 'ff') {
        b.className = 'pl-btn ico';
        var ic = el('span', 'pl-ic');
        ic.innerHTML = d.id === 'play' ? (P.eng && !P.eng.playing ? IC_PLAY : IC_PAUSE) : (d.id === 'rw' ? IC_RW : IC_FF);
        b.appendChild(ic);
        if (d.id !== 'play') b.appendChild(el('span', 'pl-ten', '10'));
      } else {
        if (d.icon) { var ni = el('span', 'pl-ni'); ni.innerHTML = d.icon; b.appendChild(ni); }
        b.appendChild(el('span', '', d.label));
        if (d.value) b.appendChild(el('span', 'pl-bv ell', d.value));
      }
      b.onclick = (function (k) { return function () { if (!P) return; P.row = 1; P.btn = k; activate(P.btnDefs[k].id); }; })(i);
      b.dzHover = (function (k) { return function () { if (P && P.uiOn && (P.row !== 1 || P.btn !== k)) { P.row = 1; P.btn = k; paintFocus(); } }; })(i);
      u.btns.appendChild(b);
      P.btnEls.push(b);
    }
    paintFocus();
  }
  function paintFocus() {
    if (!P || !P.btnEls) return;
    for (var i = 0; i < P.btnEls.length; i++) {
      var base = P.btnDefs[i].id === 'play' || P.btnDefs[i].id === 'rw' || P.btnDefs[i].id === 'ff' ? 'pl-btn ico' : 'pl-btn';
      P.btnEls[i].className = base + (P.uiOn && P.row === 1 && i === P.btn ? ' on' : '');
    }
    P.ui.bar.className = 'pl-bar' + (P.uiOn && P.row === 0 ? ' on' : '');
    paint();
  }
  function playIcon() {
    if (!P || !P.btnEls) return;
    for (var i = 0; i < P.btnDefs.length; i++) if (P.btnDefs[i].id === 'play') {
      var ic = P.btnEls[i].querySelector('.pl-ic');
      if (ic) ic.innerHTML = P.eng && !P.eng.playing ? IC_PLAY : IC_PAUSE;
    }
  }
  function activate(id) {
    if (id === 'play') togglePlay();
    else if (id === 'rw') nudge(-1);
    else if (id === 'ff') nudge(1);
    else if (id === 'next') goNext();
    else openPanel(id);
  }

  /* ---------- seek / oynat-duraklat ---------- */
  function nudge(dir) {
    if (P && P.live) { showUI(); return; }   /* canli: arama yok */
    if (!P || P.ended || !P.eng) return;
    var e = P.eng, dur = effDur();
    if (!P.seeking) { P.seeking = true; P.seekTarget = e.cur; P.holdCount = 0; }
    P.holdCount++;
    var step = SEEK_STEP * Math.min(6, 1 + Math.floor(P.holdCount / 5));   /* basili tutunca hizlanir */
    P.seekTarget += dir * step;
    if (P.seekTarget < 0) P.seekTarget = 0;
    if (dur > 0 && P.seekTarget > dur - 2) P.seekTarget = dur - 2;
    status((dir > 0 ? '+' : '−') + step + ' sn');
    showUI();
    T('seekTimer');
    P.seekTimer = g.setTimeout(commitSeek, SEEK_COMMIT_MS);
  }
  function commitSeek() {
    if (!P || !P.seeking) return;
    P.seekTimer = null;
    P.eng.seek(P.seekTarget);
    P.seeking = false; P.holdCount = 0;
    status(P.eng.playing ? '' : 'Duraklatıldı');
    paint();
    sendProgress(true);
    showUI();
  }
  function togglePlay() {
    if (!P || P.ended || !P.eng) return;
    if (P.eng.playing) {
      P.eng.pause();
      status('Duraklatıldı');
      sendProgress(true);
      showUI();
      T('hideTimer');
    } else {
      P.eng.play();
      status('');
      showUI();
    }
    playIcon();
  }

  /* ---------- ilerleme ---------- */
  function sendProgress(force) {
    var cur = P;
    if (!cur || cur.live || !cur.ready || !cur.eng || cur.iframe || cur.kind === 'trailer') return;   /* kanal: progress YOK */
    var s = cur.streams[cur.idx];
    if (s && s.kind === 'trailer') return;
    var pos = Math.round(cur.eng.cur || 0), dur = Math.round(effDur());
    if (!dur) return;
    if (!force && Math.abs(pos - cur.lastSent) < 5) return;
    cur.lastSent = pos;
    api().progress({ profile: PID, item_id: cur.itemId, episode_id: cur.episodeId || cur.itemId, position: pos, duration: dur });
  }

  /* ---------- sonraki bolum ---------- */
  function refreshNext() {
    var had = !!P.next;
    P.next = isEp() && P.kind !== 'trailer' ? nextEpisodeOf(P.detail, P.episodeId, todayIso()) : null;
    header();
    if (had !== !!P.next && P.btnEls) renderButtons();
  }
  function removeNext() {
    if (!P) return;
    T('countTimer');
    P.nextOn = false;
    P.ui.next.className = 'pl-next pl-glass';
    P.ui.next.innerHTML = '';
  }
  function showNext(count) {
    if (!P || !P.next) return;
    var n = P.ui.next;
    if (!P.nextOn) {
      n.innerHTML = '';
      n.appendChild(el('div', 'pl-next-k', 'Sonraki bölüm'));
      n.appendChild(el('div', 'pl-next-t ell', P.next.label));
      P.nextBtn = el('div', 'pl-next-b', 'Şimdi oynat');
      P.nextBtn.onclick = function () { goNext(); };
      n.appendChild(P.nextBtn);
      P.nextHint = el('div', 'pl-next-h', 'OK: şimdi oynat · Geri: kal');
      n.appendChild(P.nextHint);
      P.nextOn = true;
      void n.offsetWidth;
      n.className = 'pl-next pl-glass on';
    }
    if (count !== undefined) {
      P.nextBtn.textContent = 'Şimdi oynat (' + count + ')';
      P.nextHint.textContent = 'Birazdan başlıyor · Geri: iptal';
    }
  }
  function dismissNext() { if (!P) return; P.nextDismissed = true; removeNext(); }
  function checkNext() {
    if (!P || !P.next || P.iframe || P.ended || P.nextDismissed || !P.eng) return;
    var dur = effDur();
    if (!(dur >= NEXT_MIN_DURATION)) { if (P.nextOn) removeNext(); return; }
    var remain = remainSec(), win = Math.max(NEXT_WINDOW, Math.min(NEXT_WINDOW_MAX, dur * 0.1));
    if (remain > 0 && remain <= win) showNext();
    else if (remain > win + 5 && P.nextOn) removeNext();
  }
  function onCompleted() {
    var cur = P;
    if (cur.live) { failed('Yayın kesildi', 'playback_failed', 'live-ended'); return; }
    cur.ended = true;
    cur.eng.cur = effDur() || cur.eng.cur;
    sendProgress(true);
    if (!cur.next) refreshNext();
    if (!cur.next || cur.nextDismissed) { exit('ended'); return; }
    showUI();
    var left = NEXT_COUNT;
    showNext(left);
    cur.countTimer = g.setInterval(function () {
      if (!alive(cur)) return;
      left--;
      if (left <= 0) { goNext(); return; }
      showNext(left);
    }, 1000);
  }
  function goNext() { if (P && P.next) switchEpisode(P.next.id); }

  /* ---------- ses / altyazi (eski istemcinin player.js'inden) ---------- */
  function subsTime() { var e = P && P.eng; if (!e || P.iframe) return NaN; return e.now ? e.now() : e.cur; }
  function applySubs() {
    if (!P || !P.subs) return;
    var cur = P, seq = ++cur.subSeq;
    cur.subs.clear();
    var opt = tracksOn() ? DZ.tracks.byId(cur.model.subs, cur.sel.sub) : null;
    if (!opt || opt.kind !== 'soft') return;
    DZ.subs.load(opt.url).then(function (cues) {
      if (!alive(cur) || seq !== cur.subSeq) return;
      cur.subs.setCues(cues);
    }, function () {
      if (!alive(cur) || seq !== cur.subSeq) return;
      cur.subs.clear();
      if (cur.sel && cur.sel.sub === opt.id) { cur.sel.sub = 'off'; renderButtons(); if (cur.panel) renderPanel(); }   /* 404 vb.: sessizce Kapali */
    });
  }
  function syncTracks(index) {
    if (!P.model || !DZ.tracks) return;
    if (isEmbedStream(P.streams[index])) { P.sel = null; if (P.subs) P.subs.clear(); return; }
    P.sel = DZ.tracks.select(P.model, index, { want: P.want, prefs: DZ.tracks.loadPrefs() });
    P.want = null;
    applySubs();
  }
  function applyTrackChoice(kind, id) {
    if (!tracksOn()) return;
    var r = DZ.tracks.pick(P.model, P.idx, P.sel, kind, id, DZ.tracks.loadPrefs());
    if (r.note) { P.panel.note = r.note; renderPanel(); return; }
    DZ.tracks.remember(P.model, kind, id);
    if (r.switch) {
      var pos = (P.eng && P.eng.cur) || P.resumePosition || 0;
      P.want = { audio: r.audio, sub: r.sub };
      closePanel();
      status('Akış değiştiriliyor…');
      startStream(r.index, pos);
      return;
    }
    P.sel = { audio: r.audio, sub: r.sub };
    applySubs();
    closePanel();
    renderButtons();
  }

  /* ---------- yan panel: Altyazi / Ses / Kaynak / Bolumler ---------- */
  function panelItems(pn) {
    var out = [], i, v;
    if (pn.kind === 'subs' || pn.kind === 'audio') {
      v = DZ.tracks.view(P.model, P.sel)[pn.kind === 'subs' ? 'subs' : 'audio'];
      for (i = 0; i < v.items.length; i++) out.push({ id: v.items[i].id, label: v.items[i].label, badge: v.items[i].badge || '', cur: v.items[i].current, dis: !!v.items[i].unavailable });
    } else if (pn.kind === 'source') {
      for (i = 0; i < P.streams.length; i++) out.push({ id: i, label: P.streams[i].label || ('Kaynak ' + (i + 1)), cur: i === P.idx, badge: isEmbedStream(P.streams[i]) ? 'gömülü oynatıcı' : '' });
    } else if (pn.kind === 'channels') {
      var cl = chList();
      for (i = 0; i < cl.length; i++) out.push({ id: cl[i].id, label: chLabel(cl[i]), cur: cl[i].id === P.itemId, badge: '' });
    } else if (pn.kind === 'episodes') {
      var er = EPS.rows(pn.seasons[pn.si], P.episodeId, todayIso());
      for (i = 0; i < er.length; i++) out.push({ id: er[i].id, label: er[i].label, cur: er[i].cur, dis: er[i].dis, sn: er[i].sn });
    }
    return out;
  }
  function openPanel(kind) {
    if (!P) return;
    if ((kind === 'subs' || kind === 'audio') && !tracksOn()) return;
    var pn = { kind: kind, idx: 0, note: '' };
    pn.title = { subs: 'Altyazı', audio: 'Ses', source: 'Kaynak', episodes: 'Bölümler', channels: 'Kanallar' }[kind];
    if (kind === 'episodes') {
      pn.seasons = episodesList();
      if (!pn.seasons.length) return;
      pn.si = 0;
      var info = episodeInfo(P.detail, P.episodeId);
      for (var i = 0; info && i < pn.seasons.length; i++) if (pn.seasons[i].season === info.season) pn.si = i;
    }
    P.panel = pn;
    pn.items = panelItems(pn);
    for (var j = 0; j < pn.items.length; j++) if (pn.items[j].cur) pn.idx = j;
    renderPanel();
    void P.ui.side.offsetWidth;
    P.ui.side.className = 'pl-side pl-glass on';
    showUI();
  }
  function closePanel() {
    if (!P || !P.panel) return;
    P.panel = null;
    P.ui.side.className = 'pl-side pl-glass';
    if (P.iframe || P.err) hideUI(); else showUI();   /* gomulu oynaticida katman kendiliginden gizlenmez */
  }
  function renderPanel() {
    var pn = P.panel, s = P.ui.side;
    if (!pn) return;
    s.innerHTML = '';
    s.appendChild(el('div', 'pl-side-t', pn.title));
    if (pn.kind === 'episodes') {
      var se = pn.seasons[pn.si];
      var sl = el('div', 'pl-side-s');
      var la = el('span', 'pl-side-arr', pn.seasons.length > 1 ? '‹' : ''), ra = el('span', 'pl-side-arr', pn.seasons.length > 1 ? '›' : '');
      la.onclick = function (ev) { if (ev && ev.stopPropagation) ev.stopPropagation(); if (P && P.panel === pn) panelKey('left'); };
      ra.onclick = function (ev) { if (ev && ev.stopPropagation) ev.stopPropagation(); if (P && P.panel === pn) panelKey('right'); };
      sl.appendChild(la);
      sl.appendChild(el('span', 'ell', se.season === 0 ? 'Özel bölümler' : (se.name || se.title || ('Sezon ' + se.season))));
      sl.appendChild(ra);
      s.appendChild(sl);
    }
    var list = el('div', 'pl-side-list' + (pn.kind === 'episodes' ? ' has-s' : ''));
    var n = pn.items.length, off = Math.max(0, Math.min(pn.idx - Math.floor(SIDE_ROWS / 2), n - SIDE_ROWS));
    /* fare hover'i: odaktaki satir zaten gorunuyorsa liste kaymaz (imlecin altindaki satir yerinde kalir) */
    if (pn.keep && typeof pn.off === 'number' && pn.idx >= pn.off && pn.idx < pn.off + SIDE_ROWS && pn.off <= Math.max(0, n - SIDE_ROWS)) off = pn.off;
    pn.keep = false; pn.off = off;
    var hl = el('div', 'pl-side-hl');
    setT(hl, 'translateY(' + ((pn.idx - off) * SIDE_ROW) + 'px)');
    list.appendChild(hl);
    for (var i = off; i < Math.min(n, off + SIDE_ROWS); i++) {
      var it = pn.items[i], r = el('div', 'pl-it' + (i === pn.idx ? ' on' : '') + (it.dis ? ' dis' : ''));
      r.style.top = ((i - off) * SIDE_ROW) + 'px';
      r.appendChild(el('span', 'pl-chk', it.cur ? '✓' : ''));
      r.appendChild(el('span', 'pl-it-l ell', it.label));
      if (it.badge) r.appendChild(el('span', 'pl-badge', it.badge));
      r.onclick = (function (k) { return function (ev) { if (ev && ev.stopPropagation) ev.stopPropagation(); if (P && P.panel === pn) { pn.idx = k; panelKey('ok'); } }; })(i);
      r.dzHover = (function (k) { return function () { if (P && P.panel === pn && pn.idx !== k) { pn.idx = k; pn.note = ''; pn.keep = true; renderPanel(); if (!P.iframe) showUI(); } }; })(i);
      list.appendChild(r);
    }
    list.style.height = (Math.min(n, SIDE_ROWS) * SIDE_ROW) + 'px';
    s.appendChild(list);
    s.appendChild(el('div', 'pl-side-note', pn.note || (pn.kind === 'episodes' && pn.seasons.length > 1 ? '‹ › sezon · OK oynat · Geri kapat' :
      (pn.kind === 'channels' ? 'OK aç · Geri kapat' : 'OK seç · Geri kapat'))));
  }
  function panelKey(k) {
    var pn = P.panel, n = pn.items.length;
    if (k === 'back' || k === 'stop' || (k === 'yellow' && pn.kind === 'subs')) { closePanel(); return true; }
    if (k === 'up' && pn.idx > 0) pn.idx--;
    else if (k === 'down' && pn.idx < n - 1) pn.idx++;
    else if ((k === 'left' || k === 'right') && pn.kind === 'episodes' && pn.seasons.length > 1) {
      pn.si = (pn.si + (k === 'left' ? -1 : 1) + pn.seasons.length) % pn.seasons.length;
      pn.items = panelItems(pn); pn.idx = 0;
      for (var j = 0; j < pn.items.length; j++) if (pn.items[j].cur) pn.idx = j;
    } else if (k === 'ok' && n) {
      var it = pn.items[pn.idx];
      if (pn.kind === 'subs') { applyTrackChoice('sub', it.id); return true; }
      if (pn.kind === 'audio') { applyTrackChoice('audio', it.id); return true; }
      if (pn.kind === 'source') {
        closePanel();
        if (it.id !== P.idx) { status('Kaynak değiştiriliyor…'); startStream(it.id, (P.eng && P.eng.cur) || 0); }
        return true;
      }
      if (pn.kind === 'channels') {
        closePanel();
        var ch = chList()[pn.idx];
        if (ch && ch.id !== P.itemId) switchChannel(ch);
        return true;
      }
      if (pn.kind === 'episodes') {
        if (it.dis) { pn.note = 'Bu bölümün kaynağı henüz yok.'; renderPanel(); return true; }
        closePanel();
        if (it.id !== P.episodeId) switchEpisode(it.id);
        return true;
      }
    } else return true;
    pn.note = '';
    renderPanel();
    if (!P.iframe) showUI();
    if (pn.kind === 'channels' && pn.idx >= n - 4 && chMoreAvail()) {   /* listenin sonuna yaklasinca devami */
      var cur = P;
      chLoadMore(function () { if (alive(cur) && cur.panel === pn) { pn.items = panelItems(pn); renderPanel(); } });
    }
    return true;
  }

  /* ---------- hata / kaynak bulucu ekrani ---------- */
  function showError(title, msg, searching) {
    var cur = P;
    if (!cur) return;
    unveil();
    stopEngine();
    hideUI();
    closePanel();
    removeNext();
    poster(true);
    cur.err = { btn: 0, searching: !!searching };
    var e = cur.ui.err;
    e.innerHTML = '';
    e.appendChild(el('div', 'pl-err-k', cur.live ? chLabel(curChannel()) : cur.title + (isEp() && cur.kind !== 'trailer' && episodeInfo(cur.detail, cur.episodeId) ? ' · ' + episodeInfo(cur.detail, cur.episodeId).tag : '')));
    e.appendChild(el('div', 'pl-err-t', title));
    e.appendChild(el('div', 'pl-err-m', msg));
    var row = el('div', 'pl-err-b');
    cur.err.els = [];
    var labels = ['Geri', searching ? 'Şimdi yeniden dene' : 'Yeniden dene'];
    if (cur.live) e.appendChild(el('div', 'pl-err-h', '↑ ↓ / CH: başka kanal'));
    for (var i = 0; i < labels.length; i++) {
      var b = el('div', 'pl-btn');
      b.appendChild(el('div', 'pl-hl'));
      b.appendChild(el('span', '', labels[i]));
      b.onclick = (function (k) { return function () { if (P && P.err) { P.err.btn = k; errKey('ok'); } }; })(i);
      b.dzHover = (function (k) { return function () { if (P && P.err && P.err.btn !== k) { P.err.btn = k; errPaint(); } }; })(i);
      row.appendChild(b); cur.err.els.push(b);
    }
    e.appendChild(row);
    errPaint();
    cur.ui.dim.className = 'pl-dim on';
    void e.offsetWidth;
    e.className = 'pl-err pl-glass on';
  }
  function errPaint() { for (var i = 0; i < P.err.els.length; i++) P.err.els[i].className = 'pl-btn' + (i === P.err.btn ? ' on' : ''); }
  function hideError() {
    if (!P) return;
    stopPoll();
    P.err = null;
    P.ui.err.className = 'pl-err pl-glass';
    P.ui.dim.className = 'pl-dim';
  }
  function errKey(k) {
    if (k === 'left' && P.err.btn > 0) P.err.btn--;
    else if (k === 'right' && P.err.btn < P.err.els.length - 1) P.err.btn++;
    else if (k === 'back' || k === 'stop' || (k === 'ok' && P.err.btn === 0)) { exit('back'); return true; }
    else if (k === 'ok') { hideError(); loadStreams(); return true; }
    errPaint();
    return true;
  }
  function noStreams(finder) {
    var cur = P;
    if (!cur || !cur.alive) return;
    if (cur.live) { showError('Yayın yok', 'Bu kanal için yayın kaynağı bulunamadı.'); return; }
    var msg = finderMsg(finder, isEp());
    if (!msg) { showError('Oynatılamadı', 'Bu içerik için kullanılabilir video kaynağı bulunamadı.'); return; }
    var searching = finderState(finder) === 'searching';
    showError(finderTitle(finder), msg, searching);
    if (searching) startPoll();
  }
  /* kaynak bulucu yoklamasi: found -> akis yeniden istenir, not_found -> metin; idle / sure doldu -> sessizce durur */
  function startPoll() {
    var cur = P, polls = 0, max = Math.ceil(FINDER_MAX_MS / FINDER_POLL_MS);
    stopPoll();
    SM('… Kaynak bulucu başladı', 'skip');
    function again() {
      if (!alive(cur) || !cur.err || polls >= max) return;
      cur.pollTimer = g.setTimeout(function () {
        cur.pollTimer = null;
        if (!alive(cur) || !cur.err) return;
        polls++;
        api().sourceFinder(cur.itemId, cur.episodeId, function (err, st) {
          if (!alive(cur) || !cur.err) return;
          var s = !err && st ? st.state : null;
          D('Kaynak bulucu yoklaması: ' + (err ? 'hata ' + (err.message || err.status) : s));
          if (s === 'found') SM('✓ Kaynak bulucu · kaynak bulundu', 'ok');
          else if (s === 'not_found') SM('✗ Kaynak bulucu · bulunamadı', 'bad');
          if (dbgOn() && st && (s === 'found' || s === 'not_found')) g.DZDebug.trace('player', st.debug_trace);
          if (s === 'found') { hideError(); loadStreams(); return; }
          if (s === 'not_found') { noStreams({ state: 'not_found' }); return; }
          if (s === 'idle') return;
          again();
        });
      }, FINDER_POLL_MS);
    }
    again();
  }
  function stopPoll() { if (P && P.pollTimer) { g.clearTimeout(P.pollTimer); P.pollTimer = null; } }

  /* ---------- akislar ---------- */
  function stopEngine() {
    if (!P) return;
    T('startTimer'); T('progTimer'); T('seekTimer'); T('focusTimer'); T('stallTimer');
    P.seeking = false;
    if (P.iframeEl && P.iframeEl.parentNode) P.iframeEl.parentNode.removeChild(P.iframeEl);
    P.iframeEl = null;
    if (P.eng) { try { P.eng.destroy(); } catch (e) {} P.eng = null; }
  }
  function report(event, code, detail) {
    var cur = P, s = cur && cur.streams[cur.idx];
    if (!s || !s.attempt_token) return;
    var key = s.attempt_token + ':' + (s.variant_id || '') + ':' + event;   /* akis basina (sunucu variant bazli tekillestirir) */
    if (cur.reported[key]) return;
    cur.reported[key] = true;
    var payload = { attempt_token: s.attempt_token, event: event, code: code || '',
      engine: cur.iframe ? 'embed' : (cur.eng ? (cur.eng.av ? 'avplay' : cur.eng.hlsjs === true ? 'hlsjs' : 'native') : (useAvplay() ? 'avplay' : 'native')),
      platform: api().platform ? api().platform() : '' };
    if (s.provider) payload.provider = String(s.provider).slice(0, 60);
    if (s.variant_id) payload.variant_id = String(s.variant_id);
    if (hostOf(s.url)) payload.stream_url_host = hostOf(s.url);
    if (s.type) payload.stream_type = String(s.type);
    var det = event === 'failure' ? clipDetail(detail) : '';
    if (det) payload.detail = det;
    if (cur.eng && cur.eng.hlsjs === true) payload.hlsjs = true;   /* hls.js ile oynatiliyordu: sunucu gercek oynatma hatasi sayar */
    var waiters = null;
    if (event === 'failure') { waiters = []; cur.reportPending = waiters; }
    D('playback-report gönderildi: ' + event + (code ? ' / ' + code : '') + ' (' + payload.engine + ')');
    api().playbackReport(PID, payload, function (err, res) {
      if (err) { D('playback-report hata: ' + (err.message || err.status), 'bad'); SM('✗ playback-report · ' + (err.message || err.status), 'bad'); }
      else if (res && res.finder && res.finder.state) D('playback-report → kaynak bulucu: ' + res.finder.state);
      if (event === 'failure' && !err && res && res.finder && res.finder.state) cur.reportFinder = res.finder;
      if (!waiters) return;
      if (cur.reportPending === waiters) cur.reportPending = null;
      for (var i = 0; i < waiters.length; i++) waiters[i]();
    });
  }
  function failed(message, code, detail) {
    var cur = P;
    if (!cur || !cur.alive || cur.handling) return;
    cur.handling = true;
    T('startTimer');
    code = g.navigator && g.navigator.onLine === false ? 'offline' : (code || 'playback_failed');
    D(code === 'timeout' ? '→ zaman aşımı ' + Math.round((cur.startLimit || START_TIMEOUT_MS) / 1000) + ' sn (oynatma başlamadı)' : '→ hata: ' + code + (detail ? ' (' + clipDetail(detail) + ')' : ''), 'bad');
    attEnd(cur, false, errShort(code, detail));
    report('failure', code, detail);
    cur.failedIdx[cur.idx] = true;   /* oturumda en cok bir kez dusmus sayilir */
    var next = -1;
    if (code !== 'autoplay' && code !== 'offline' && code !== 'aborted') {
      next = nextStreamIdx(cur, false);
      /* kanal: m3u8'lerin hepsi duserse YouTube (embed) son care */
      if (next < 0 && cur.live) next = nextStreamIdx(cur, true);
    }
    if (next >= 0) {
      if (cur.shown) status('Başka kaynak deneniyor…'); else veil('Başka kaynak deneniyor…');
      /* konum: son oynayan konum (cur.resumePosition time'da guncellenir); prepare'de coken akista eng.cur 0 olabilir */
      startStream(next, cur.live ? 0 : (cur.resumePosition || (cur.eng && cur.eng.cur) || 0));
      return;
    }
    stopEngine();
    D('Denenecek akış kalmadı', 'bad');
    SM('✗ Denenecek akış kalmadı', 'bad');
    var done = false;
    function show() {
      if (done || !alive(cur)) return;
      done = true;
      cur.handling = false;
      if (cur.live) { showError('Yayın yok', 'Bu kanalın yayınına şu an ulaşılamıyor.'); return; }
      if (cur.reportFinder && finderMsg(cur.reportFinder, isEp())) { noStreams(cur.reportFinder); return; }
      showError('Oynatılamadı', String(message || 'Video oynatılamadı'));
    }
    if (cur.reportPending && !cur.live) { cur.reportPending.push(show); g.setTimeout(show, FINDER_WAIT_MS); }
    else show();
  }
  /* dongusel siradaki: idx+1..n-1, sonra 0..idx-1; dusmus akis atlanir (elle secilen dusunce onceden oynayan / denenmemis akisa doner) */
  function nextStreamIdx(cur, allowEmbed) {
    var n = cur.streams.length;
    for (var k = 1; k < n; k++) {
      var i = (cur.idx + k) % n;
      if (cur.failedIdx[i]) continue;
      if (!allowEmbed && isEmbedStream(cur.streams[i])) continue;
      return i;
    }
    return -1;
  }
  /* takilma: oynatma basladiktan sonra tek bekleme >15 sn ya da son 60 sn'de toplam bekleme >30 sn */
  function stallCheck(cur) {
    if (!alive(cur) || !cur.stallT0 || cur.handling) return;
    if (cur.eng && !cur.eng.playing) { cur.stallT0 = nowMs(); return; }   /* duraklatilmis: bekleme sayilmaz */
    var now = nowMs(), one = now - cur.stallT0, tot = one;
    for (var i = 0; i < cur.waits.length; i++) if (now - cur.waits[i].end <= STALL_WINDOW_MS) tot += cur.waits[i].d;
    if (one > STALL_ONE_MS || tot > STALL_TOTAL_MS) {
      var s = cur.streams[cur.idx] || {}, who = String(s.label || '').split(' · ')[0] || hostOf(s.url) || 'akış';
      var why = one > STALL_ONE_MS ? 'bekleme ' + Math.round(one / 1000) + ' sn' : 'toplam bekleme ' + Math.round(tot / 1000) + ' sn / 60 sn';
      T('stallTimer');
      cur.stallT0 = 0;
      SM('✗ ' + who + ' · takılma (' + why + ')', 'bad');
      failed('Akış takılıyor', 'stall', 'stall: ' + why + ' (tek ' + Math.round(one / 1000) + ' sn, 60 sn toplam ' + Math.round(tot / 1000) + ' sn)');
    }
  }
  function stallBuffering(cur, on) {
    if (!cur.shown || cur.iframe) return;
    var now = nowMs();
    if (on) {
      if (cur.stallT0) return;
      cur.stallT0 = now;
      T('stallTimer');
      cur.stallTimer = g.setInterval(function () { stallCheck(cur); }, 1000);
      return;
    }
    if (!cur.stallT0) return;
    cur.waits.push({ end: now, d: now - cur.stallT0 });
    while (cur.waits.length && now - cur.waits[0].end > STALL_WINDOW_MS) cur.waits.shift();
    cur.stallT0 = 0;
    T('stallTimer');
  }
  function shown() {
    if (!P || P.shown) return;
    P.shown = true;
    P.everShown = true;
    D('→ oynuyor', 'ok');
    if (P.attT0) attEnd(P, true, 'oynuyor · ilk kare ' + ((nowMs() - P.attT0) / 1000).toFixed(1) + ' sn');
    unveil();
    poster(false);
    status('');
    showUI();
  }
  function startStream(index, resume) {
    var cur = P;
    if (!alive(cur)) return;
    cur.resumePosition = resume || 0;
    var gen = ++cur.gen;
    stopEngine();
    cur.idx = index; cur.tried[index] = true; cur.handling = false; cur.ready = false; cur.iframe = false;
    cur.stallT0 = 0; cur.waits = []; cur.startExtended = false;
    var s = cur.streams[index], played = 0, last = resume || 0;
    function valid() { return alive(cur) && gen === cur.gen; }
    var cb = {
      ready: function () {
        if (!valid()) return;
        cur.ready = true;
        playIcon();
        cur.progTimer = g.setInterval(function () { if (valid() && cur.eng && cur.eng.playing) sendProgress(false); }, PROGRESS_MS);
      },
      started: function () { if (valid()) { T('startTimer'); shown(); status(''); } },
      time: function () {
        if (!valid() || !cur.eng) return;
        var d = cur.eng.cur - last;
        last = cur.eng.cur;
        if (d > 0 && d < 5 && !cur.seeking) played += d;
        if (played > 0) T('startTimer');   /* ilk kare: baslama zamanlayicisi silinir */
        if (played > 0.25) shown();
        if (played > 0 && !cur.live && cur.eng.cur > 0) cur.resumePosition = cur.eng.cur;
        if (played >= 10) report('success', '');
        if (!cur.seeking) paint();
        checkNext();
      },
      buffering: function (on) {
        if (!valid()) return;
        if (cur.startTimer && !cur.startExtended) {   /* tampon olayi geldi: baslama suresi bir kez +10 sn */
          cur.startExtended = true;
          var left = Math.max(0, cur.startDeadline - nowMs()) + START_EXTEND_MS;
          cur.startLimit += START_EXTEND_MS;
          T('startTimer');
          cur.startTimer = g.setTimeout(function () { if (valid()) failed('Video başlamadı', 'timeout', 'start-timeout'); }, left);
          cur.startDeadline = nowMs() + left;
        }
        stallBuffering(cur, on);
        if (cur.shown && !cur.seeking) status(on ? 'Arabellek…' : (cur.eng && !cur.eng.playing ? 'Duraklatıldı' : ''));
      },
      completed: function () { if (valid()) onCompleted(); },
      error: function (m, c, d) { if (valid()) failed(m, c, d); }
    };
    header();
    syncTracks(index);
    var pk = pickEngine(s);
    if (dbgOn()) {
      var motor = engineName(s, pk), why = pk.src === 'learned' ? ' (öğrenilmiş)' : pk.src === 'seed' ? ' (tohum)' : '';
      D('Akış ' + (index + 1) + '/' + cur.streams.length + ' · ' + (s.label || '') + ' · ' + (s.type || '?') + ', ' + motor + why +
        (s.proxied ? ' · vekil üzerinden (' + (s.proxy_reason || '?') + ')' : ' · ' + hostOf(s.url)) +
        (resume ? ' · ' + fmt(resume) + "'dan" : '') + ' deneniyor');
      var flags = isEmbedStream(s) ? ['gömülü'] : [s.proxied ? 'vekil' : 'doğrudan'];
      if (s.proxied && g.DZDebug.parallel('player', index + 1)) flags.push('paralel');
      cur.attIdx = index; cur.attT0 = nowMs();
      SM('▶ ' + attName(cur, index) + ' ' + (s.type || '?') + (isEmbedStream(s) ? '' : ' · ' + motor + why) + ' (' + flags.join(', ') + ') … deneniyor', 'skip', attKey(cur, index));
    }
    if (isEmbedStream(s)) {
      /* gomulu oynatici (fragman vb.): capraz koken, hata okunamaz; Geri cikar, Kirmizi elle bildirir */
      cur.iframe = true;
      var fr = el('iframe', 'pl-iframe');
      fr.setAttribute('allow', 'autoplay; fullscreen');
      fr.setAttribute('allowfullscreen', 'true');
      fr.setAttribute('frameborder', '0');
      fr.setAttribute('tabindex', '-1');
      var u = String(s.url || '');
      fr.src = /[?&]autoplay=/.test(u) ? u : u + (u.indexOf('?') >= 0 ? '&' : '?') + 'autoplay=true';
      cur.ui.media.appendChild(fr);
      cur.iframeEl = fr;
      cur.shown = true; cur.row = 1;
      unveil(); poster(false); hideUI(); header();
      cur.ui.hint.textContent = cur.live ? 'Geri: çık · ↑ ↓ kanal · OK: kanal listesi' : iframeHint();
      cur.ui.hint.className = 'pl-ihint on';
      cur.hintTimer = g.setTimeout(function () { if (alive(cur)) cur.ui.hint.className = 'pl-ihint'; }, 3000);
      /* iframe odagi alirsa uzaktan kumanda tuslari (Geri, CH, ↑↓) capraz kokenli belgeye gider: odagi sayfaya geri al */
      cur.focusTimer = g.setInterval(function () {
        if (!alive(cur) || cur.iframeEl !== fr) return;
        try { if (doc.activeElement === fr) { fr.blur(); if (g.focus) g.focus(); } } catch (eF) {}
      }, 700);
      renderButtons();
      return;
    }
    cur.eng = createEngine(cur.ui.media, pk.av, cb);
    cur.eng.dur = cur.duration;
    renderButtons();
    cur.startLimit = s.proxied && !isHlsStream(s.type, s.url) ? START_PROXIED_MP4_MS : START_TIMEOUT_MS;
    cur.startDeadline = nowMs() + cur.startLimit;
    cur.startTimer = g.setTimeout(function () { if (valid()) failed('Video başlamadı', 'timeout', 'start-timeout'); }, cur.startLimit);
    cur.eng.load(s.url, resume || 0, s.type, pk.mode);
  }
  function iframeHint() { return buttonDefs().length ? 'Geri: oynatıcıdan çık · OK: kontroller' : 'Geri: oynatıcıdan çık'; }
  function loadStreams() {
    var cur = P, gen = ++cur.loadGen;
    stopEngine();
    cur.streams = []; cur.tried = {}; cur.failedIdx = {}; cur.reported = {}; cur.reportFinder = null; cur.reportPending = null;
    cur.model = null; cur.sel = null; cur.want = null; cur.idx = 0; cur.shown = false; cur.ready = false; cur.handling = false;
    if (cur.subs) cur.subs.clear();
    poster(true);
    hideUI();
    renderButtons();
    if (!(cur.live && cur.everShown)) veil(cur.live ? 'Kanal açılıyor…' : 'Video hazırlanıyor…');
    else status('Kanal açılıyor…');
    var reqT = nowMs();
    D('İstek: /api/streams · ' + (cur.live ? 'kanal ' : '') + (cur.episodeId || cur.itemId) + (cur.kind ? ' · ' + cur.kind : ''));
    api().streams(cur.itemId, PID, cur.episodeId, cur.kind, function (err, res) {
      if (!alive(cur) || gen !== cur.loadGen) return;
      if (err) { D('Sunucu hatası: ' + (err.message || err.status) + ' · ' + (nowMs() - reqT) + ' ms', 'bad'); SM('✗ Sunucu · ' + (err.message || err.status), 'bad'); showError('Oynatılamadı', err.message || 'Sunucuya ulaşılamıyor'); return; }
      if (dbgOn()) {
        D('Sunucu yanıtı · ' + ((res.streams || []).length) + ' akış · ' + (nowMs() - reqT) + ' ms' + (res.finder ? ' · bulucu: ' + res.finder.state : ''));
        g.DZDebug.trace('player', res.debug_trace);
        if (!(res.streams || []).length) SM('✗ Sunucu · akış yok' + (res.finder ? ' (bulucu: ' + res.finder.state + ')' : ''), 'bad');
      }
      cur.streams = res.streams || [];
      if (res.live === true) { cur.live = true; cur.row = 1; }
      if (res.channel && res.channel.id === cur.itemId) cur.chInfo = res.channel;
      cur.duration = cur.live ? 0 : (res.duration || 0);
      header();
      var resume = cur.restart ? 0 : (res.resume_position || cur.position || 0);   /* restart: "Baştan başla" */
      cur.position = 0; cur.restart = false;   /* detaydan gelen konum/restart yalniz ilk acilista */
      if (!cur.streams.length) { noStreams(res.finder); return; }
      cur.model = DZ.tracks ? DZ.tracks.build(cur.streams, res.subtitles, res.audio) : null;
      var first = cur.model && DZ.tracks.hasChoices(cur.model) ? DZ.tracks.choose(cur.model, DZ.tracks.loadPrefs()).index : 0;
      startStream(first, resume);
    });
  }
  function switchEpisode(id) {
    var cur = P;
    sendProgress(true);
    T('countTimer');
    closePanel(); removeNext(); hideError();
    stopEngine();
    cur.episodeId = id;
    cur.ended = false; cur.nextDismissed = false; cur.lastSent = -999; cur.position = 0;
    refreshNext();
    loadStreams();
  }

  /* ---------- canli TV: kanal listesi, gecis, bant, rakamla numara ---------- */
  function chList() { return P && P.channels && P.channels.items ? P.channels.items : []; }
  function chIndexOf(id) { var l = chList(); for (var i = 0; i < l.length; i++) if (l[i].id === id) return i; return -1; }
  function chMoreAvail() {
    var c = P && P.channels;
    return !!(c && c.id && !c.done && (c.total === null || c.total === undefined || c.items.length < c.total));
  }
  /* satirin bir sonraki sayfasi; bolum ekraninin satir nesnesi (items/total/loading) ortak: orada da gorunur */
  function chLoadMore(cb) {
    var cur = P, c = cur && cur.channels;
    if (!chMoreAvail()) { cb(false); return; }
    if (c.loading) { g.setTimeout(function () { if (alive(cur)) chLoadMore(cb); }, 300); return; }
    c.loading = true;
    api().row(c.id, PID, c.items.length, CH_PAGE, function (err, data) {
      c.loading = false;
      if (!alive(cur)) return;
      var items = (!err && data && data.items) || [];
      if (!err) {
        c.items = c.items.concat(items);
        c.total = data.total !== undefined && data.total !== null ? data.total : c.items.length;
        if (!items.length) c.done = true;
      }
      cb(!err && items.length > 0);
    });
  }
  function chLoadAll(cb) {
    var cur = P;
    (function step() { chLoadMore(function (ok) { if (!alive(cur)) return; if (ok && chMoreAvail()) step(); else cb(); }); })();
  }
  function showBand(it, txt) {
    var cur = P, b = cur.ui.band;
    b.innerHTML = '';
    var no = txt !== undefined ? txt : (it && it.channel_no ? String(it.channel_no) : '');
    if (no) b.appendChild(el('div', 'pl-band-n', no));
    b.appendChild(el('div', 'pl-band-t ell', it ? (it.title || '') : ''));
    b.className = 'pl-band pl-glass on';
    T('bandTimer');
    cur.bandTimer = g.setTimeout(function () { if (alive(cur)) cur.ui.band.className = 'pl-band pl-glass'; }, BAND_MS);
  }
  /* hizli ardisik basis: bant hemen guncellenir, son secim CH_DEBOUNCE_MS sonra acilir */
  function chTarget(cur, idx) {
    var it = chList()[idx];
    if (!it) return;
    cur.chPending = idx;
    showBand(it);
    T('chTimer');
    cur.chTimer = g.setTimeout(function () {
      cur.chTimer = null;
      if (!alive(cur)) return;
      var t = chList()[cur.chPending];
      cur.chPending = null;
      if (t && t.id !== cur.itemId) switchChannel(t);
    }, CH_DEBOUNCE_MS);
  }
  /* acik kanal listede yoksa (vitrinden acildi: sira = `live_all`, bos baslar) bulunana dek sayfa sayfa yukle */
  function chEnsure(cb) {
    var cur = P;
    if (chIndexOf(cur.itemId) >= 0 || !chMoreAvail()) { cb(); return; }
    chLoadMore(function (ok) { if (!alive(cur)) return; if (ok) chEnsure(cb); else cb(); });
  }
  function chStep(dir) {
    var cur = P, list = chList();
    var pend = cur.chPending !== null && cur.chPending !== undefined;
    if (!pend && chIndexOf(cur.itemId) < 0 && chMoreAvail()) { chEnsure(function () { if (alive(cur) && (chIndexOf(cur.itemId) >= 0 || chList().length)) chStep(dir); }); return; }
    if (!list.length) return;
    var base = pend ? cur.chPending : chIndexOf(cur.itemId);
    if (base < 0) base = dir > 0 ? -1 : 0;
    var t = base + dir;
    if (t >= list.length) {
      if (chMoreAvail()) { chLoadMore(function () { if (alive(cur)) chTarget(cur, base + 1 < chList().length ? base + 1 : 0); }); return; }
      t = 0;
    } else if (t < 0) {
      if (chMoreAvail()) { chLoadAll(function () { if (alive(cur)) chTarget(cur, chList().length - 1); }); return; }
      t = list.length - 1;
    }
    chTarget(cur, t);
  }
  function switchChannel(it) {
    var cur = P;
    T('chTimer'); cur.chPending = null;
    closePanel(); hideError(); stopEngine();
    cur.itemId = it.id; cur.title = it.title || ''; cur.channelNo = it.channel_no || null;
    cur.chInfo = { id: it.id, title: it.title || '', channel_no: it.channel_no || null };
    showBand(it);
    loadStreams();
  }
  function digitKey(d) {
    var cur = P;
    cur.digits = ((cur.digits || '') + d).slice(-4);
    showBand({ title: '…' }, cur.digits);
    T('digitTimer');
    cur.digitTimer = g.setTimeout(function () {
      cur.digitTimer = null;
      if (!alive(cur)) return;
      var num = parseInt(cur.digits, 10);
      cur.digits = '';
      gotoNumber(cur, num);
    }, DIGIT_MS);
  }
  function gotoNumber(cur, num) {
    var l = chList();
    for (var i = 0; i < l.length; i++) if (l[i].channel_no === num) { if (l[i].id !== cur.itemId) switchChannel(l[i]); else showBand(l[i]); return; }
    if (chMoreAvail()) { chLoadMore(function (ok) { if (alive(cur)) { if (ok) gotoNumber(cur, num); else showBand({ title: 'Kanal bulunamadı' }, String(num)); } }); return; }
    showBand({ title: 'Kanal bulunamadı' }, String(num));
  }
  /* kanal tuslari her durumda (yukleniyor, hata perdesi, gomulu oynatici, katman acik); panel acikken ↑↓ panelindir */
  function liveKey(k) {
    var cur = P;
    if (k === 'chup' || k === 'chdown') { if (cur.panel) closePanel(); chStep(k === 'chup' ? 1 : -1); return true; }
    if (cur.panel) return false;
    if (k === 'up' || k === 'down') { chStep(k === 'up' ? 1 : -1); return true; }   /* TV aliskanligi: ^ = sonraki (ChannelUp) */
    var m = /^d(\d)$/.exec(k);
    if (m) { digitKey(m[1]); return true; }
    return false;
  }

  /* ---------- giris / cikis ---------- */
  function open(o) {
    if (P) teardown();
    PID = o.pid || null;
    P = {
      o: o, alive: true, itemId: o.itemId, episodeId: o.episodeId || null, kind: o.kind || null, title: o.title || '', type: o.type || 'movie',
      detail: o.detail || null, position: o.position || 0, restart: !!o.restart, gen: 0, loadGen: 0, streams: [], tried: {}, failedIdx: {}, reported: {}, idx: 0, duration: 0,
      lastSent: -999, row: o.live ? 1 : 0, btn: 0, uiOn: false, subSeq: 0, panel: null, err: null, next: null, nextOn: false, nextDismissed: false,
      live: !!o.live, channels: o.channels || null, channelNo: o.channelNo || null, chInfo: null, chPending: null, digits: '', everShown: false
    };
    P.ui = build();
    if (o.backdrop) P.ui.poster.style.backgroundImage = 'url("' + o.backdrop + '")';
    o.stage.insertBefore(P.ui.root, o.before || null);
    if (dbgOn()) g.DZDebug.mount(P.ui.root, 'player', { reset: true, rows: g.DZDebug.detailed() ? 12 : 8 });   /* katmanin disinda: katman gizlense de gorunur */
    if (DZ.subs) P.subs = DZ.subs.createLayer(P.ui.subsHost, subsTime);
    refreshNext();
    if (P.live && P.channels && P.channels.id && !P.channels.items) P.channels.items = [];
    if (P.live && P.channels && P.channels.id && !P.channels.items.length) chEnsure(function () {});   /* tembel sira (live_all) */
    if (!P.detail && isEp() && P.kind !== 'trailer') {
      var cur = P;
      api().detail(P.itemId, PID, function (err, d) {
        if (!alive(cur) || err || !d) return;
        cur.detail = d; refreshNext(); renderButtons();
      });
    }
    loadStreams();
  }
  function teardown() {
    var cur = P;
    if (!cur) return;
    cur.alive = false;
    T('hideTimer'); T('countTimer'); T('posterTimer'); T('hintTimer'); T('chTimer'); T('bandTimer'); T('digitTimer');
    stopPoll();
    stopEngine();
    try { if (cur.subs) cur.subs.destroy(); } catch (e) {}
    if (cur.ui.root.parentNode) cur.ui.root.parentNode.removeChild(cur.ui.root);
    if (g.DZDebug) g.DZDebug.unmount('player');
    doc.documentElement.classList.remove('av-active');
    P = null;
  }
  function exit(reason) {
    var cur = P;
    if (!cur) return;
    sendProgress(true);
    unveil();
    var info = { reason: reason, itemId: cur.itemId, episodeId: cur.episodeId }, o = cur.o;
    teardown();
    if (o.onExit) o.onExit(info);
  }

  /* k: left right up down ok back | play pause playpause stop ff rew | red yellow green | chup chdown d0..d9 (canli) */
  function key(k) {
    var cur = P;
    if (!cur) return false;
    if (k === 'green') { if (g.DZDebug) g.DZDebug.toggle(); return true; }   /* Hata ayiklama paneli gizle/goster */
    if (k === 'red') { if (cur.ready || cur.iframe) failed('Kaynak çalışmıyor olarak bildirildi', 'playback_failed', 'user-report'); return true; }
    if (cur.live && liveKey(k)) return true;
    if (k === 'chup' || k === 'chdown' || /^d\d$/.test(k)) return true;   /* kanal disi: yok sayilir */
    if (k === 'next') { if (cur.next && !cur.live) goNext(); return true; }   /* kumanda Next (MediaTrackNext) */
    if (cur.panel) return panelKey(k);
    if (cur.err) return errKey(k);
    if (cur.iframe && !cur.live && cur.uiOn && cur.btnEls && cur.btnEls.length) {
      /* gomulu oynatici + kontrol katmani acik: tek satir dugme (Kaynak / Bolumler / Sonraki bolum) */
      if (k === 'back') { hideUI(); return true; }
      if (k === 'stop') { exit('back'); return true; }
      showUI();
      cur.row = 1;
      if (k === 'left' && cur.btn > 0) { cur.btn--; paintFocus(); }
      else if (k === 'right' && cur.btn < cur.btnEls.length - 1) { cur.btn++; paintFocus(); }
      else if (k === 'ok' && cur.btnDefs[cur.btn]) activate(cur.btnDefs[cur.btn].id);
      return true;
    }
    if (cur.iframe) {
      if (k === 'back' || k === 'stop') exit('back');
      else if (!cur.live && /^(ok|up|down|left|right)$/.test(k) && cur.btnEls && cur.btnEls.length) { cur.row = 1; showUI(); }
      else if (cur.live && k === 'ok' && chList().length > 1) openPanel('channels');
      else { cur.ui.hint.className = 'pl-ihint on'; T('hintTimer'); cur.hintTimer = g.setTimeout(function () { if (alive(cur)) cur.ui.hint.className = 'pl-ihint'; }, 3000); }
      return true;
    }
    if (!cur.shown) { if (k === 'back' || k === 'stop') exit('back'); return true; }   /* aciliyor: yalniz Geri (+ canli kanal tuslari) */
    if (cur.ended) {
      if (k === 'ok' || k === 'play' || k === 'playpause') goNext();
      else if (k === 'back' || k === 'stop') exit('back');
      return true;
    }
    if (cur.nextOn) {
      if (k === 'back') { dismissNext(); showUI(); return true; }
      if (k === 'ok' && cur.eng && cur.eng.playing && (!cur.uiOn || cur.row === 0)) { goNext(); return true; }
    }
    switch (k) {
      case 'play': if (cur.eng && !cur.eng.playing) togglePlay(); else showUI(); return true;
      case 'pause': if (cur.eng && cur.eng.playing) togglePlay(); else showUI(); return true;
      case 'playpause': togglePlay(); return true;
      case 'ff': nudge(1); return true;
      case 'rew': nudge(-1); return true;
      case 'stop': exit('back'); return true;
      case 'yellow': openPanel('subs'); return true;
      case 'back':
        if (cur.uiOn) hideUI(); else exit('back');
        return true;
    }
    if (cur.live) {
      /* canli: tek satir (dugmeler); katman gizliyken OK = kanal listesi, ←→ katmani acar */
      if (!cur.uiOn) { if (k === 'ok' && chList().length > 1) openPanel('channels'); else showUI(); return true; }
      showUI();
      if (k === 'left' && cur.btn > 0) { cur.btn--; paintFocus(); }
      else if (k === 'right' && cur.btn < cur.btnEls.length - 1) { cur.btn++; paintFocus(); }
      else if (k === 'ok' && cur.btnDefs[cur.btn]) activate(cur.btnDefs[cur.btn].id);
      return true;
    }
    if (!cur.uiOn) {
      if (k === 'left') nudge(-1);
      else if (k === 'right') nudge(1);
      else if (k === 'ok') togglePlay();
      else showUI();
      return true;
    }
    showUI();
    if (cur.row === 0) {
      if (k === 'left') nudge(-1);
      else if (k === 'right') nudge(1);
      else if (k === 'ok') togglePlay();
      else if (k === 'down' && cur.btnEls && cur.btnEls.length) { cur.row = 1; paintFocus(); }
    } else {
      if (k === 'left' && cur.btn > 0) { cur.btn--; paintFocus(); }
      else if (k === 'right' && cur.btn < cur.btnEls.length - 1) { cur.btn++; paintFocus(); }
      else if (k === 'up') { cur.row = 0; paintFocus(); }
      else if (k === 'ok') activate(cur.btnDefs[cur.btn].id);
    }
    return true;
  }

  DZP.player = {
    open: open, key: key, close: function () { exit('close'); }, active: function () { return !!P; },
    /* fare gercekten hareket etti (app.js): katman gorunur */
    pointer: function () { if (P && P.shown && !P.err && !P.iframe) showUI(); },
    enginePref: enginePref, setEnginePref: setEnginePref,
    /* kanal silme (app.js, OK basili tut): Kanallar paneli acikken odaktaki kanal karti, yoksa null */
    panelChannel: function () {
      if (!P || !P.panel || P.panel.kind !== 'channels') return null;
      return chList()[P.panel.idx] || null;
    },
    /* kanal silindi: siradan cikar (app.js ortak satiri zaten cikarmis olabilir), panel yeniden cizilir, odak komsu satirda */
    channelRemoved: function (id) {
      if (!P) return;
      var l = chList(), i;
      for (i = l.length - 1; i >= 0; i--) if (l[i] && l[i].id === id) {
        l.splice(i, 1);
        if (P.channels && typeof P.channels.total === 'number' && P.channels.total > 0) P.channels.total--;
      }
      var pn = P.panel;
      if (pn && pn.kind === 'channels') {
        pn.items = panelItems(pn);
        pn.idx = Math.max(0, Math.min(pn.idx, pn.items.length - 1));
        if (!pn.items.length) closePanel(); else renderPanel();
      }
    },
    /* testler icin */
    _: { createEngine: createEngine, preferHlsJs: preferHlsJs, isEmbedStream: isEmbedStream, isHlsStream: isHlsStream,
      nextEpisodeOf: nextEpisodeOf, episodeInfo: episodeInfo, state: function () { return P; } }
  };
})(window);
