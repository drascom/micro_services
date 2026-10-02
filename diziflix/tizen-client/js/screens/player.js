/* screens/player.js - Tizen AVPlay oynatici (webapis.avplay), yoksa HTML5 <video> yedegi.
   Kontroller 3 sn hareketsizlikte gizlenir. Ilerleme 20 sn'de bir + pause/seek/cikis/bitis.
   Ses/altyazi: "Ses ve Altyazilar" paneli (Sari tus / Asagi ok; js/tracks.js secim modeli, js/ui/tracks_panel.js panel,
   js/subs.js motor-bagimsiz DOM altyazi katmani). Iframe (embed) modunda altyazi/panel YOK.
   Motor/embed karari `streams[].type`'a (hls|mp4|embed) gore, URL uzantisina DEGIL. Tarayicida (HTML5 motoru) type=hls ve dogal HLS
   destegi yoksa js/vendor/hls.min.js (hls.js) tembel yuklenir; AVPlay (Tizen TV) yolu hls.js'e dokunmaz. Basarisizlik raporuna `detail` eklenir.
   Dizi bolumunde baslik altinda "S04 B02 · Bolum adi"; bolumun son ~45 sn'sinde (ya da bitince) sag ustte
   "Sonraki bolum" karti (OK = simdi oynat, GERI = iptal; bitince 5 sn geri sayim, iptal edilmediyse otomatik gecis). */
(function (g) {
  'use strict';
  var DZ = g.DZ = g.DZ || {};

  var HIDE_MS = 3000;
  var PROGRESS_MS = 20000;
  var SEEK_STEP = 10;         /* saniye */
  var SEEK_COMMIT_MS = 350;
  var NEXT_WINDOW = 45;       /* "Sonraki bolum" teklifi: bolumun son ~45 sn'si */
  var NEXT_MIN_DURATION = 600; /* daha kisa bolumlerde erken teklif YOK (yalniz bitince geri sayim) */
  var NEXT_COUNT = 5;         /* bolum bitince otomatik gecis geri sayimi (sn) */

  var container = null;
  var state = null;

  function mk(tag, cls, txt) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (txt !== undefined && txt !== null) n.textContent = txt;
    return n;
  }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }
  function pad2(v) { return v < 10 ? '0' + v : String(v); }
  function fmt(sec) {
    var s = Math.max(0, Math.floor(sec || 0));
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    return h > 0 ? (h + ':' + pad2(m) + ':' + pad2(x)) : (m + ':' + pad2(x));
  }

  function hasAvplay() {
    try {
      return (typeof webapis !== 'undefined') && webapis && webapis.avplay ? true : false;
    } catch (e) { return false; }
  }

  function cleanPath(u) { return String(u || '').split('#')[0].split('?')[0].toLowerCase(); }

  /* embed/iframe mi? Karar `type`'a gore (embed | hls | mp4): URL uzantisina BAKILMAZ (uzantisiz mp4 vekil adresi,
     `.txt` uzantili gercek HLS adresi iframe'e dusmesin). Uzanti yalniz `type` HIC yoksa yedek: .m3u8/.mp4 degilse embed. */
  function isEmbedStream(s) {
    if (!s) return false;
    var t = s.type ? String(s.type).toLowerCase() : '';
    if (t) return t === 'embed';
    return !/\.(m3u8|mp4)$/.test(cleanPath(s.url));
  }

  /* detail alani (playback-report): en cok 120 karakter */
  function clipDetail(d) {
    var t = d === undefined || d === null ? '' : String(d).replace(/\s+/g, ' ').replace(/^ | $/g, '');
    return t.length > 120 ? t.slice(0, 120) : t;
  }

  /* ---------------- hls.js (YALNIZ tarayici/HTML5 motoru) ----------------
     Masaustu Chrome/Firefox/Edge HLS'i dogal oynatamaz. Dogal destek yoksa (canPlayType bos) js/vendor/hls.min.js
     tembel yuklenir (bir kez) ve MSE ile oynatilir. AVPlay (Tizen TV) yolu buna DOKUNMAZ: AVPlay HLS'i kendisi oynatir. */
  var HLS_SRC = 'js/vendor/hls.min.js?v=hlsjs-1.7.3';
  var hlsWaiters = null;

  function hlsSupported() {
    try { return !!(g.Hls && g.Hls.isSupported && g.Hls.isSupported()); } catch (e) { return false; }
  }

  /* HLS akisi mi? `type` belirleyici; yalniz type yoksa .m3u8 uzantisi yedek */
  function isHlsStream(type, url) {
    var t = type ? String(type).toLowerCase() : '';
    if (t) return t === 'hls';
    return /\.m3u8$/.test(cleanPath(url));
  }

  function nativeHls(video) {
    try { return !!(video && video.canPlayType && video.canPlayType('application/vnd.apple.mpegurl')); } catch (e) { return false; }
  }

  /* tembel <script>: bir kez; ayni anda gelen istekler tek yuklemeyi paylasir; hata olursa bir sonraki denemede yeniden denenir */
  function loadHlsJs(done) {
    if (g.Hls) { done(null); return; }
    if (hlsWaiters) { hlsWaiters.push(done); return; }
    hlsWaiters = [done];
    var s = document.createElement('script');
    function finish(err) {
      var w = hlsWaiters; hlsWaiters = null;
      if (err && s.parentNode) s.parentNode.removeChild(s);
      for (var i = 0; i < w.length; i++) { try { w[i](err); } catch (e) {} }
    }
    s.onload = function () { finish(g.Hls ? null : 'hls.js yüklenemedi'); };
    s.onerror = function () { finish('hls.js yüklenemedi'); };
    s.async = true;
    s.src = HLS_SRC;
    (document.head || document.body || document.documentElement).appendChild(s);
  }

  /* "hls:<tur>/<ayrinti>[/<http kodu>]" (<=120) */
  function hlsDetail(data) {
    var code = data && data.response && data.response.code ? '/' + data.response.code : '';
    return clipDetail('hls:' + (data && data.type || '?') + '/' + (data && data.details || '?') + code);
  }

  /* ---------------- oynatici soyutlamasi ----------------
     Motor iki asamali calisir: prepare(url, resumeSec) -> hazir -> start() (konuma sar + oynat).
     load() = prepare + hazir olunca otomatik start (eski tek adimli yol).
     opts.detached: ekran yokken (yukleme modali) hazirlik icin; punch-through / player kokune
     baglanma attach(host) ile sonradan yapilir. Callback'ler setCallbacks() ile devredilebilir.
     cb: {prepared, ready, started, time, buffering, completed, error} (hepsi opsiyonel). */
  function createEngine(host, useAv, cb, opts) {
    var eng = { av: useAv, dur: 0, cur: 0, playing: false, cb: cb || {}, prepared: false,
                playIssued: false, startedFired: false, autoStart: false, attached: false,
                pendingResume: 0 };
    var detached = !!(opts && opts.detached);

    function call(name, a, b, c) {
      var f = eng.cb && eng.cb[name];
      if (f) f(a, b, c);
    }
    /* oynatma GERCEKTEN basladi: play() sonrasi ilk zaman ilerlemesi / 'playing' olayi */
    function markStarted() {
      if (eng.startedFired || !eng.playIssued) return;
      eng.startedFired = true;
      call('started');
    }
    eng.setCallbacks = function (next) { eng.cb = next || {}; };

    if (useAv) {
      var obj = document.createElement('object');
      obj.id = 'avplayer';
      obj.type = 'application/avplayer';
      /* fallback/placeholder kutusu cizilmesin: tam boyut + bos icerik (CSS'te de gizli) */
      obj.setAttribute('width', '1920');
      obj.setAttribute('height', '1080');
      /* AVPlay donanim video katmani, transform'lu parent'i YOK SAYAR (setDisplayRect
         gercek ekran pikselidir). Object'i translate3d+scale'li appEl zincirinin DISINA,
         dogrudan body'ye ekle -> punch-through gercek ekran koordinatiyla hizalanir. */
      document.body.appendChild(obj);
      eng.el = obj;
      var playerRoot = null;
      /* punch-through: video bolgesindeki katmanlari saydam yap (arkadaki video gorunsun).
         Hazirlik (detached) asamasinda SAYDAMLASTIRMA: ekranda detay/modal gorunur kalir. */
      eng.attach = function (hostEl) {
        if (eng.attached) return;
        eng.attached = true;
        document.documentElement.classList.add('av-active');
        playerRoot = hostEl && hostEl.parentNode;
        if (playerRoot && playerRoot.classList) playerRoot.classList.add('av-mode');
        console.log('[avplay] punch-through aktif');
      };
      if (!detached) eng.attach(host);
      console.log('[avplay] object body\'ye eklendi (transform disi)');

      /* 3. arguman (type) AVPlay'de YOK SAYILIR: HLS'i AVPlay kendisi oynatir (hls.js yalniz html5 motorunda) */
      eng.prepare = function (url, resumeSec) {
        eng.pendingResume = resumeSec || 0;
        eng.prepared = false; eng.playIssued = false; eng.startedFired = false;
        try {
          console.log('[avplay] open: ' + url);
          webapis.avplay.open(url);
          webapis.avplay.setDisplayRect(0, 0, 1920, 1080);
          try { webapis.avplay.setDisplayMethod('PLAYER_DISPLAY_MODE_LETTER_BOX'); } catch (e0) {}
          webapis.avplay.setListener({
            onbufferingstart: function () { call('buffering', true); },
            onbufferingcomplete: function () { call('buffering', false); },
            oncurrentplaytime: function (ms) {
              eng.cur = (ms || 0) / 1000;
              markStarted();
              call('time');
            },
            onstreamcompleted: function () { call('completed'); },
            onerror: function (err) { call('error', String(err), /CODEC|NOT_SUPPORTED/i.test(String(err)) ? 'unsupported' : 'playback_failed', 'avplay:' + String(err)); },
            onevent: function (t) { console.log('[avplay] event ' + t); }
          });
          console.log('[avplay] prepareAsync basliyor');
          webapis.avplay.prepareAsync(function () {
            console.log('[avplay] prepare tamam');
            try { eng.dur = (webapis.avplay.getDuration() || 0) / 1000; } catch (e1) { eng.dur = 0; }
            eng.prepared = true;
            call('prepared');
            if (eng.autoStart) eng.start();
          }, function (e) { call('error', 'prepareAsync: ' + e, undefined, 'avplay:prepareAsync ' + e); });
        } catch (e) {
          call('error', 'avplay open: ' + e, undefined, 'avplay:open ' + e);
        }
      };
      eng.start = function () {
        /* bazi Tizen surumlerinde prepare SONRASI setDisplayRect tekrar gerekir */
        try { webapis.avplay.setDisplayRect(0, 0, 1920, 1080); } catch (eDR) {}
        if (eng.pendingResume > 0) {
          try { webapis.avplay.seekTo(Math.round(eng.pendingResume * 1000)); } catch (e2) {}
          eng.cur = eng.pendingResume;
          eng.pendingResume = 0;
        }
        try { webapis.avplay.play(); eng.playing = true; eng.playIssued = true; console.log('[avplay] play'); }
        catch (e3) { call('error', String(e3), 'playback_failed', 'avplay:play ' + e3); return; }
        call('ready');
      };
      eng.load = function (url, resumeSec, type) { eng.autoStart = true; eng.prepare(url, resumeSec, type); };
      /* altyazi katmani icin anlik oynatma saniyesi: getCurrentTime (hazir degilse/atarsa oncurrentplaytime'in eng.cur'u) */
      eng.now = function () {
        try { var ms = webapis.avplay.getCurrentTime(); if (typeof ms === 'number' && ms >= 0) return ms / 1000; } catch (eNow) {}
        return eng.cur;
      };
      eng.play = function () { try { webapis.avplay.play(); eng.playing = true; } catch (e) {} };
      eng.pause = function () { try { webapis.avplay.pause(); eng.playing = false; } catch (e) {} };
      /* kalite degisimi: durdur/kapat -> yeni url ac -> prepare -> pozisyona seek -> oynat */
      eng.switchTo = function (url, resumeSec, type) {
        try { webapis.avplay.stop(); } catch (e) {}
        try { webapis.avplay.close(); } catch (e2) {}
        eng.load(url, resumeSec, type);
      };
      eng.seek = function (sec) {
        var t = Math.max(0, Math.min(eng.dur > 0 ? eng.dur - 2 : sec, sec));
        try { webapis.avplay.seekTo(Math.round(t * 1000)); eng.cur = t; } catch (e) {}
      };
      eng.destroy = function () {
        try { webapis.avplay.stop(); } catch (e) {}
        try { webapis.avplay.close(); } catch (e2) {}
        if (obj.parentNode) obj.parentNode.removeChild(obj);
        if (eng.attached) {
          eng.attached = false;
          document.documentElement.classList.remove('av-active');
          if (playerRoot && playerRoot.classList) playerRoot.classList.remove('av-mode');
        }
        console.log('[avplay] destroy');
      };
      return eng;
    }

    /* masaustu / TV disi: HTML5 video (HLS: dogal destek yoksa hls.js, bkz. yukaridaki bolum) */
    var v = document.createElement('video');
    v.setAttribute('playsinline', '');
    v.autoplay = true;
    eng.el = v;
    var hls = null;          /* aktif hls.js ornegi */
    var hlsTried = { net: false, media: false };   /* hls.js kurtarma denemeleri (her biri BIR kez) */
    var loadSeq = 0;         /* prepare/destroy sayaci: gec kalan tembel yukleme geri cagrisini gecersiz kilar */
    eng.hlsjs = false;       /* bu yuklemede hls.js kullaniliyor mu (test/teshis) */
    eng.attach = function (hostEl) {
      if (eng.attached) return;
      eng.attached = true;
      if (hostEl) hostEl.appendChild(v);
    };
    if (!detached) eng.attach(host);
    v.addEventListener('loadedmetadata', function () {
      eng.dur = v.duration || 0;
      eng.prepared = true;
      call('prepared');
      if (eng.autoStart) eng.start();
    }, false);
    v.addEventListener('timeupdate', function () { eng.cur = v.currentTime || 0; eng.dur = v.duration || eng.dur; call('time'); }, false);
    v.addEventListener('waiting', function () { call('buffering', true); }, false);
    v.addEventListener('playing', function () { markStarted(); call('buffering', false); }, false);
    v.addEventListener('ended', function () { call('completed'); }, false);
    v.addEventListener('error', function () {
      var code = v.error ? v.error.code : 0;
      /* hls.js acikken medya (cozme) hatasi: once hls.js'in kendi kurtarmasi (bir kez) */
      if (hls && code === 3 && !hlsTried.media) { hlsTried.media = true; try { hls.recoverMediaError(); } catch (eR) {} return; }
      var det = 'video.error.code=' + code + (v.error && v.error.message ? ' ' + v.error.message : '');
      call('error', 'Video oynatılamadı', ({1:'aborted',2:'network',3:'decode',4:'playback_failed'})[code] || 'playback_failed', clipDetail(det));
    }, false);

    function stopHls() {
      var h = hls; hls = null;
      if (h) { try { h.destroy(); } catch (e) {} }
    }
    /* hls.js: fatal olmayan hatalarda hls.js kendi kurtarir; fatal AG hatasinda startLoad, fatal MEDYA hatasinda recoverMediaError
       BIR kez denenir (manifest/seviye yuklenemediyse yeniden denenmez: hls.js zaten denedi, siradaki kaynaga gecilsin); sonra hata. */
    function attachHls(url, resumeSec) {
      var H = g.Hls;
      var ET = H.ErrorTypes || { NETWORK_ERROR: 'networkError', MEDIA_ERROR: 'mediaError' };
      var h = new H({ startPosition: resumeSec > 0 ? resumeSec : -1 });
      hlsTried = { net: false, media: false };
      hls = h;
      eng.hlsjs = true;
      h.on(H.Events.ERROR, function (ev, data) {
        if (hls !== h || !data || !data.fatal) return;
        var isNet = data.type === ET.NETWORK_ERROR, isMedia = data.type === ET.MEDIA_ERROR;
        if (isNet && !hlsTried.net && !/^(manifest|level)/.test(String(data.details || ''))) {
          hlsTried.net = true;
          try { h.startLoad(); } catch (e0) {}
          return;
        }
        if (isMedia && !hlsTried.media) {
          hlsTried.media = true;
          try { h.recoverMediaError(); } catch (e1) {}
          return;
        }
        stopHls();
        call('error', 'Video oynatılamadı', isNet ? 'network' : isMedia ? 'decode' : 'playback_failed', hlsDetail(data));
      });
      h.loadSource(url);
      h.attachMedia(v);
    }
    eng.prepare = function (url, resumeSec, type) {
      eng.pendingResume = resumeSec || 0;
      eng.prepared = false; eng.playIssued = false; eng.startedFired = false;
      var seq = ++loadSeq;
      stopHls();
      eng.hlsjs = false;
      if (isHlsStream(type, url) && !nativeHls(v)) {
        loadHlsJs(function (err) {
          if (seq !== loadSeq) return;
          if (err) { call('error', err, 'playback_failed', 'hls.js:load'); return; }
          if (hlsSupported()) { attachHls(url, resumeSec || 0); return; }
          v.src = url; v.load();   /* MSE yok: dogal yolu dene (video hatasi olagan akisla raporlanir) */
        });
        return;
      }
      v.src = url;
      v.load();
    };
    eng.start = function () {
      if (eng.pendingResume > 0) { try { v.currentTime = eng.pendingResume; } catch (e) {} eng.cur = eng.pendingResume; }
      eng.pendingResume = 0;
      eng.playIssued = true;
      var pr = v.play();
      if (pr && pr.catch) pr.catch(function (e) { call('error', String(e), e.name === 'NotAllowedError' ? 'autoplay' : 'playback_failed', 'play():' + (e && e.name ? e.name : e)); });
      eng.playing = true;
      call('ready');
    };
    eng.load = function (url, resumeSec, type) { eng.autoStart = true; eng.prepare(url, resumeSec, type); };
    eng.now = function () { return v.currentTime || eng.cur; };
    /* kalite degisimi: src degistir -> loadedmetadata'da currentTime=pozisyon -> play */
    eng.switchTo = function (url, resumeSec, type) { eng.load(url, resumeSec, type); };
    eng.play = function () { var p = v.play(); if (p && p.catch) p.catch(function () {}); eng.playing = true; };
    eng.pause = function () { v.pause(); eng.playing = false; };
    eng.seek = function (sec) {
      var t = Math.max(0, Math.min(eng.dur > 0 ? eng.dur - 1 : sec, sec));
      try { v.currentTime = t; eng.cur = t; } catch (e) {}
    };
    eng.destroy = function () {
      loadSeq++;
      stopHls();
      try { v.pause(); v.removeAttribute('src'); v.load(); } catch (e) {}
      if (v.parentNode) v.parentNode.removeChild(v);
    };
    return eng;
  }

  /* ---------------- arayuz ---------------- */
  function buildUI() {
    var root = mk('div', 'player');
    var media = mk('div', null);
    media.style.position = 'absolute';
    media.style.left = '0';
    media.style.top = '0';
    media.style.width = '100%';     /* .player = sahne boyutu (16:9'da 1920x1080); video object-fit contain */
    media.style.height = '100%';
    root.appendChild(media);

    var ui = mk('div', 'player-ui');
    var top = mk('div', 'pl-top');
    var title = mk('div', 'pl-title', '');
    var epLine = mk('div', 'pl-ep hidden', '');
    var sub = mk('div', 'pl-sub', '');
    top.appendChild(title); top.appendChild(epLine); top.appendChild(sub);
    ui.appendChild(top);

    var bot = mk('div', 'pl-bottom');
    var bar = mk('div', 'pl-bar');
    var fill = document.createElement('i');
    fill.style.width = '0%';
    bar.appendChild(fill);
    var knob = mk('div', 'pl-knob');
    knob.style.left = '0%';
    bar.appendChild(knob);
    bot.appendChild(bar);
    var times = mk('div', 'pl-times');
    var tCur = mk('div', null, '0:00');
    var tRem = mk('div', null, '-0:00');
    times.appendChild(tCur); times.appendChild(tRem);
    bot.appendChild(times);
    var st = mk('div', 'pl-state', '');
    bot.appendChild(st);

    /* sag alt kontrol satiri: [Ses ve Altyazilar] [Kaynak / kalite] */
    var ctl = mk('div', 'pl-ctl');
    var tWrap = mk('div', 'pl-tracks hidden');
    var tBtn = mk('div', 'pl-tbtn', 'Ses ve Altyazılar');
    tBtn.setAttribute('data-nav', '1');
    tBtn.setAttribute('tabindex', '0');
    tWrap.appendChild(tBtn);
    ctl.appendChild(tWrap);

    /* kalite: kontrol satirinda odaklanabilir buton + acilir menu (kumandayla erisilebilir) */
    var qWrap = mk('div', 'pl-quality hidden');
    var qBtn = mk('div', 'pl-qbtn', 'Kalite');
    qBtn.setAttribute('data-nav', '1');
    qBtn.setAttribute('tabindex', '0');
    qWrap.appendChild(qBtn);
    var qMenu = mk('div', 'pl-qmenu hidden');
    qWrap.appendChild(qMenu);
    ctl.appendChild(qWrap);
    bot.appendChild(ctl);

    ui.appendChild(bot);

    root.appendChild(ui);
    return { root: root, media: media, ui: ui, title: title, ep: epLine, sub: sub, fill: fill, knob: knob, tCur: tCur, tRem: tRem, st: st, qWrap: qWrap, qBtn: qBtn, qMenu: qMenu, tWrap: tWrap, tBtn: tBtn };
  }

  function showUI() {
    if (!state) return;
    state.ui.ui.classList.remove('hide');
    state.uiOn = true;
    syncSubsPos();
    if (state.hideTimer) clearTimeout(state.hideTimer);
    state.hideTimer = setTimeout(function () {
      if (state && state.eng && state.eng.playing && !state.ended && !state.qMenu && !state.tOpen) {
        state.ui.ui.classList.add('hide');
        state.uiOn = false;
        syncSubsPos();
      }
    }, HIDE_MS);
  }

  /* altyazi metni: kontroller gorunurken ilerleme cubugunun ustunde, gizlenince ekranin alt %10'unda */
  function syncSubsPos() {
    if (state && state.subs) state.subs.setRaised(state.uiOn !== false);
  }

  function paint() {
    if (!state) return;
    var e = state.eng;
    var pos = state.seeking ? state.seekTarget : e.cur;
    var dur = e.dur || state.duration || 0;
    var pct = dur > 0 ? Math.max(0, Math.min(100, (pos / dur) * 100)) : 0;
    state.ui.fill.style.width = pct + '%';
    state.ui.knob.style.left = pct + '%';   /* .pl-bar genisligine (sahne - 2*safe; 1920'de 1800px) gore */
    state.ui.tCur.textContent = fmt(pos);
    state.ui.tRem.textContent = dur > 0 ? ('-' + fmt(dur - pos)) : '';
  }

  function setStatus(txt) { if (state) state.ui.st.textContent = txt || ''; }

  /* ---------------- ilerleme kaydi ---------------- */
  function sendProgress(force) {
    if (!state || !state.streamReady || !state.eng) return;
    if (state.streams && state.streams[state.qIndex] && state.streams[state.qIndex].kind === "trailer") return;
    var e = state.eng;
    var pos = Math.round(e.cur || 0);
    var dur = Math.round(e.dur || state.duration || 0);
    if (!dur) return;
    if (!force && Math.abs(pos - state.lastSent) < 5) return;
    state.lastSent = pos;
    DZ.api.progress({
      profile: DZ.api.profileId(),
      item_id: state.itemId,
      episode_id: state.episodeId || state.itemId,
      position: pos,
      duration: dur
    }).then(function () {}, function (err) { console.log('[player] progress hata: ' + err.message); });
  }

  /* ---------------- bolum bilgisi + sonraki bolum ---------------- */
  function todayIso() {
    var d = new Date();
    return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
  }

  /* sezonlar numara sirasinda, 0. sezon (ozel bolumler) sona; sunucu dizisi degistirilmez */
  function sortedSeasons(detail) {
    var list = detail && detail.seasons && detail.seasons.slice ? detail.seasons.slice() : [];
    list.sort(function (a, b) {
      var ra = a.season === 0 ? 1e9 : (a.season || 0);
      var rb = b.season === 0 ? 1e9 : (b.season || 0);
      return ra - rb;
    });
    return list;
  }

  /* detay.js epPlayable ile ayni kural: yayinlanmamis (tarih gelecekte + kaynak hazir degil) ve kaynagi olmayan bolum atlanir */
  function episodePlayable(ep, today) {
    var a = ep && ep.availability ? ep.availability.state : '';
    var air = ep && typeof ep.air_date === 'string' && /^\d{4}-\d{2}-\d{2}/.test(ep.air_date) ? ep.air_date.slice(0, 10) : '';
    if (air && air > today && a !== 'ready') return false;
    return a !== 'unavailable';
  }

  function epTag(season, episode) {
    var b = 'B' + pad2(episode || 0);
    return season === 0 ? ('Özel ' + b) : ('S' + pad2(season || 0) + ' ' + b);
  }

  /* "S04 B02 · Bolum adi"; genel "10. Bolum" adi tekrarlanmaz */
  function episodeLabel(tag, title) {
    var t = String(title || '').replace(/^\s+|\s+$/g, '');
    if (!t || /^\d+\s*\.?\s*b[oö]l[uü]m$/i.test(t)) return tag;
    return tag ? (tag + ' · ' + t) : t;
  }

  /* {season, episode, tag, title, label, id} | null. Once listedeki kayittan, yoksa ":sN:eM" ekinden (yalniz etiket). */
  function episodeInfo(detail, episodeId) {
    if (!episodeId) return null;
    var list = sortedSeasons(detail);
    for (var si = 0; si < list.length; si++) {
      var eps = list[si].episodes || [];
      for (var ei = 0; ei < eps.length; ei++) {
        if (eps[ei].id !== episodeId) continue;
        var ep = eps[ei];
        var sn = ep.season !== undefined && ep.season !== null ? ep.season : list[si].season;
        var tag = epTag(sn, ep.episode);
        return { id: ep.id, season: sn, episode: ep.episode, tag: tag, title: ep.title || '', label: episodeLabel(tag, ep.title) };
      }
    }
    var m = /:s(\d+):e(\d+)$/i.exec(String(episodeId));
    if (!m) return null;
    var tg = epTag(parseInt(m[1], 10), parseInt(m[2], 10));
    return { id: episodeId, season: parseInt(m[1], 10), episode: parseInt(m[2], 10), tag: tg, title: '', label: tg };
  }

  /* Bir sonraki ARDISIK bolum (TV-EXPERIENCE-V1 5: aradaki bolum eksikse sessizce atlanmaz): ayni sezonun sonraki bolumu,
     sezon bitince sonraki normal sezonun ilk bolumu; ozel bolumler yalniz kendi aralarinda. Ardisik bolum oynatilamazsa
     (kaynak yok / yayinlanmadi) null: teklif YOK, kullanici detayda bolumun durumunu gorur. */
  function nextEpisodeOf(detail, episodeId, today) {
    if (!detail || !episodeId) return null;
    var list = sortedSeasons(detail);
    var found = false, special = false;
    for (var si = 0; si < list.length; si++) {
      var eps = list[si].episodes || [];
      for (var ei = 0; ei < eps.length; ei++) {
        var ep = eps[ei];
        if (!found) {
          if (ep.id === episodeId) { found = true; special = list[si].season === 0; }
          continue;
        }
        if ((list[si].season === 0) !== special) return null;
        if (!episodePlayable(ep, today || todayIso())) return null;
        var sn = ep.season !== undefined && ep.season !== null ? ep.season : list[si].season;
        var tag = epTag(sn, ep.episode);
        return { id: ep.id, season: sn, episode: ep.episode, tag: tag, title: ep.title || '', label: episodeLabel(tag, ep.title) };
      }
    }
    return null;
  }

  function updateEpisodeHeader() {
    if (!state || !state.ui || !state.ui.ep) return;
    var series = state.episodeId && state.episodeId !== state.itemId;
    var info = series ? episodeInfo(state.detail, state.episodeId) : null;
    state.ui.ep.textContent = info ? info.label : '';
    if (info && info.label) state.ui.ep.classList.remove('hidden'); else state.ui.ep.classList.add('hidden');
  }

  function refreshNext() {
    if (!state) return;
    var series = state.episodeId && state.episodeId !== state.itemId && state.requestedKind !== 'trailer';
    state.next = series ? nextEpisodeOf(state.detail, state.episodeId, todayIso()) : null;
    updateEpisodeHeader();
  }

  function removeNextBox() {
    if (!state || !state.nextBox) return;
    if (state.nextBox.parentNode) state.nextBox.parentNode.removeChild(state.nextBox);
    state.nextBox = null; state.nextBtn = null; state.nextHint = null;
  }

  /* sag ustte "Sonraki bolum" karti: altyazi (alt orta) ve alt kontrollerle cakismaz; kontroller gizlenince de kalir */
  function showNextPrompt() {
    if (!state || state.nextBox || !state.next) return;
    var box = mk('div', 'pl-next');
    box.appendChild(mk('h3', null, 'Sonraki bölüm'));
    box.appendChild(mk('p', null, state.next.label));
    var row = mk('div', null);
    row.style.display = 'flex';
    row.style.justifyContent = 'flex-end';
    var b = mk('div', 'btn primary focused', 'Şimdi oynat');
    row.appendChild(b);
    box.appendChild(row);
    var hint = mk('div', 'pl-next-hint', 'Bölüm bitince otomatik başlar · GERİ: iptal');
    box.appendChild(hint);
    state.ui.root.appendChild(box);
    state.nextBox = box; state.nextBtn = b; state.nextHint = hint;
    state.playNext = goNext;
  }

  function dismissNext() {
    if (!state) return;
    state.nextDismissed = true;
    removeNextBox();
  }

  /* sonraki bolume gec: biten/erken birakilan motoru birak (AVPlay tek ornek); sonraki bolum yukleme modalinin ALTINDA
     hazirlanir, siyah player ekrani gosterilmez. Modal Geri ile iptal edilirse detaya donulur. */
  function goNext() {
    if (!state || !state.next) return;
    var next = state.next;
    if (state.countTimer) { clearInterval(state.countTimer); state.countTimer = null; }
    sendProgress(true);
    var params = { itemId: state.itemId, episodeId: next.id, title: state.title, type: state.type, detail: state.detail };
    if (!DZ.playflow) { DZ.app.go('player', params, true); return; }
    try { if (state.eng) state.eng.destroy(); } catch (eDestroy) {}
    state.eng = null;
    params.replace = true;
    params.onCancel = function () { DZ.app.back(); };
    DZ.playflow.start(params);
  }

  /* bolumun son NEXT_WINDOW sn'sine girildi mi (geri sarilirsa teklif kapanir; iptal edilen geri gelmez) */
  function checkNext() {
    if (!state || !state.next || state.iframeMode || state.ended || state.nextDismissed || !state.eng) return;
    var e = state.eng;
    var dur = e.dur || state.duration || 0;
    if (!(dur >= NEXT_MIN_DURATION)) return;
    var remain = dur - (e.cur || 0);
    if (remain > 0 && remain <= NEXT_WINDOW) showNextPrompt();
    else if (remain > NEXT_WINDOW + 5) removeNextBox();
  }

  function onCompleted() {
    if (!state) return;
    state.ended = true;
    state.eng.cur = state.eng.dur || state.duration || state.eng.cur;
    sendProgress(true);
    showUI();
    if (!state.next) refreshNext();
    if (!state.next || state.nextDismissed) { DZ.app.back(); return; }

    showNextPrompt();
    var b = state.nextBtn;
    if (state.nextHint) state.nextHint.textContent = 'Birazdan başlıyor · GERİ: iptal';
    var left = NEXT_COUNT;
    if (b) b.textContent = 'Şimdi oynat (' + left + ')';
    state.playNext = goNext;
    state.countTimer = setInterval(function () {
      left--;
      if (left <= 0) { goNext(); return; }
      if (b) b.textContent = 'Şimdi oynat (' + left + ')';
    }, 1000);
  }

  /* ---------------- seek ---------------- */
  function nudge(dir) {
    if (!state || state.ended) return;
    var e = state.eng;
    var dur = e.dur || state.duration || 0;
    if (!state.seeking) { state.seeking = true; state.seekTarget = e.cur; state.holdCount = 0; }
    state.holdCount++;
    var factor = Math.min(6, 1 + Math.floor(state.holdCount / 5));   /* basili tutunca hizlan */
    var step = SEEK_STEP * factor;
    state.seekTarget += dir * step;
    if (state.seekTarget < 0) state.seekTarget = 0;
    if (dur > 0 && state.seekTarget > dur - 2) state.seekTarget = dur - 2;
    setStatus((dir > 0 ? '>> ' : '<< ') + step + ' sn');
    paint();
    showUI();
    if (state.seekTimer) clearTimeout(state.seekTimer);
    state.seekTimer = setTimeout(commitSeek, SEEK_COMMIT_MS);
  }

  function commitSeek() {
    if (!state || !state.seeking) return;
    state.eng.seek(state.seekTarget);
    state.seeking = false;
    state.holdCount = 0;
    setStatus('');
    paint();
    sendProgress(true);   /* seek bitisinde kaydet */
  }

  function togglePlay() {
    if (!state || state.ended) return;
    if (state.eng.playing) {
      state.eng.pause();
      if (state.startTimer) { clearTimeout(state.startTimer); state.startTimer = null; }
      setStatus('DURAKLATILDI');
      sendProgress(true);   /* pause aninda kaydet */
      showUI();
      if (state.hideTimer) clearTimeout(state.hideTimer);
    } else {
      state.eng.play();
      setStatus('');
      showUI();
    }
  }

  /* ---------------- kalite secici ---------------- */
  function currentQualityLabel() {
    if (!state || !state.streams || !state.streams.length) return '';
    var s = state.streams[state.qIndex] || state.streams[0];
    return (s && s.label) || '';
  }

  function updateQualityLabel() {
    if (!state || !state.ui.qBtn) return;
    var lbl = currentQualityLabel();
    state.ui.qBtn.textContent = 'Kaynak / kalite: ' + (lbl || '-');
  }

  function renderQualityMenu() {
    if (!state || !state.ui.qMenu) return;
    var menu = state.ui.qMenu;
    clear(menu);
    for (var i = 0; i < state.streams.length; i++) {
      var row = mk('div', 'pl-qitem', state.streams[i].label || String(i));
      if (i === state.qIndex) row.className += ' current';
      if (i === state.qHover) row.className += ' focused';
      menu.appendChild(row);
    }
  }

  function openQualityMenu() {
    if (!state || state.ended) return;
    if (!state.streams || state.streams.length < 1) return;
    state.qMenu = true;
    state.qHover = state.qIndex;
    state.ui.qMenu.classList.remove('hidden');
    state.ui.qBtn.classList.add('focused');
    renderQualityMenu();
    showUI();
  }

  function closeQualityMenu() {
    if (!state) return;
    state.qMenu = false;
    if (state.ui.qMenu) state.ui.qMenu.classList.add('hidden');
    if (state.ui.qBtn) state.ui.qBtn.classList.remove('focused');
    showUI();
  }

  function moveQ(dir) {
    if (!state || !state.qMenu) return;
    var n = state.streams.length;
    state.qHover = (state.qHover + dir + n) % n;
    renderQualityMenu();
    showUI();
  }

  function applyQuality(idx) {
    if (!state) { closeQualityMenu(); return; }
    var s = state.streams[idx];
    if (!s) { closeQualityMenu(); return; }
    if (idx === state.qIndex) { closeQualityMenu(); return; }
    var pos = (state.eng && state.eng.cur) || 0;   /* mevcut pozisyondan devam et */
    closeQualityMenu();
    if (state.startStream) state.startStream(idx, pos);

  }

  /* ---------------- ses / altyazi ---------------- */
  function tracksOn() { return !!(state && state.model && DZ.tracks); }

  /* altyazi katmaninin saati: motorun anlik oynatma saniyesi (AVPlay ve HTML5 icin ayni yol) */
  function subsTime() {
    var e = state && state.eng;
    if (!e || state.iframeMode) return NaN;
    return e.now ? e.now() : e.cur;
  }

  function updateTracksButton() {
    if (!state || !state.ui.tBtn) return;
    state.ui.tBtn.textContent = 'Ses ve Altyazılar · ' + (tracksOn() && state.sel ? DZ.tracks.subLabel(state.model, state.sel) : 'Kapalı');
  }

  function hideTracks() {
    if (!state) return;
    closeTracksPanel();
    if (state.subs) state.subs.clear();
    if (state.ui.tWrap) state.ui.tWrap.classList.add('hidden');
    state.sel = null;
  }

  /* Seciliyi katmana yansit: soft iz -> VTT'yi al ve goster; Kapali / gomulu altyazi -> katman bos.
     Indirme hatasi (404, zaman asimi, bozuk dosya) SESSIZCE Kapali olur; tercih silinmez. */
  function applySubs() {
    if (!state || !state.subs) return;
    var current = state;
    var seq = ++current.subSeq;
    current.subs.clear();
    var opt = tracksOn() && current.sel ? DZ.tracks.byId(current.model.subs, current.sel.sub) : null;
    if (!opt || opt.kind !== 'soft') return;
    DZ.subs.load(opt.url).then(function (cues) {
      if (state !== current || !current.alive || seq !== current.subSeq) return;
      current.subs.setCues(cues);
    }, function () {
      if (state !== current || !current.alive || seq !== current.subSeq) return;
      current.subs.clear();
      if (current.sel && current.sel.sub === opt.id) {
        current.sel.sub = 'off';
        updateTracksButton();
        if (current.tOpen) current.tPanel.update(DZ.tracks.view(current.model, current.sel));
      }
    });
  }

  /* startStream sonrasi: bu akista gecerli secimi hesapla (istenen > tercih > varsayilan kural), dugmeyi/katmani guncelle */
  function syncTracks(index) {
    if (!tracksOn()) return;
    var stream = state.streams[index];
    if (isEmbedStream(stream)) { hideTracks(); return; }
    state.sel = DZ.tracks.select(state.model, index, { want: state.want, prefs: DZ.tracks.loadPrefs() });
    state.want = null;
    if (state.ui.tWrap) {
      if (DZ.tracks.hasChoices(state.model)) state.ui.tWrap.classList.remove('hidden');
      else state.ui.tWrap.classList.add('hidden');
    }
    updateTracksButton();
    applySubs();
    if (state.tOpen) state.tPanel.update(DZ.tracks.view(state.model, state.sel));
  }

  function openTracksPanel() {
    if (!state || state.ended || state.iframeMode || !tracksOn() || !state.tPanel || !state.sel) return;
    if (!DZ.tracks.hasChoices(state.model)) return;
    closeQualityMenu();
    state.tOpen = true;
    state.tPanel.open(DZ.tracks.view(state.model, state.sel));
    state.ui.tBtn.classList.add('focused');
    showUI();
  }

  function closeTracksPanel() {
    if (!state) return;
    state.tOpen = false;
    if (state.tPanel) state.tPanel.close();
    if (state.ui.tBtn) state.ui.tBtn.classList.remove('focused');
    showUI();
  }

  /* panelde bir oge secildi: ayni akista ise katmani degistir, baska dosya gerekiyorsa konumu koruyarak o akisa gec */
  function applyTrackChoice(kind, id) {
    if (!tracksOn() || !state.sel) return;
    var r = DZ.tracks.pick(state.model, state.qIndex, state.sel, kind, id, DZ.tracks.loadPrefs());
    if (r.note) { state.tPanel.setNote(r.note); return; }   /* ör. gömülü altyazı kapatılamıyor */
    DZ.tracks.remember(state.model, kind, id);
    if (r.switch) {
      var pos = (state.eng && state.eng.cur) || state.resumePosition || 0;
      state.want = { audio: r.audio, sub: r.sub };
      closeTracksPanel();
      setStatus('Akış değiştiriliyor…');
      if (state.startStream) state.startStream(r.index, pos);
      return;
    }
    state.sel = { audio: r.audio, sub: r.sub };
    updateTracksButton();
    applySubs();
    state.tPanel.update(DZ.tracks.view(state.model, state.sel));
  }

  function handlePanelAction(action) {
    if (!action) return;
    if (action.type === 'close') closeTracksPanel();
    else if (action.type === 'select') applyTrackChoice(action.kind, action.id);
  }

  /* sunucu yanitindan model: streams/subtitles/audio (playflow'dan gelen hazir akis dahil) */
  function setModel(target, streams, subtitles, audio) {
    target.model = DZ.tracks ? DZ.tracks.build(streams, subtitles, audio) : null;
  }

  function leave() {
    sendProgress(true);
    DZ.app.back();
  }

  /* iframe modu: geri butonunu GOSTER, ~3 sn sonra TAM gizle (odak tuzagi olmasin) */
  function wakeBack() {
    if (!state || !state.backBtn) return;
    state.backVisible = true;
    state.backBtn.classList.remove('hidden');
    if (state.reportBtn) state.reportBtn.classList.remove('hidden');
    if (state.backDimTimer) clearTimeout(state.backDimTimer);
    state.backDimTimer = setTimeout(function () {
      if (state && state.backBtn) { state.backBtn.classList.add('hidden'); state.backVisible = false; }
      if (state && state.reportBtn) state.reportBtn.classList.add('hidden');
    }, 3000);
  }

  /* Basarili oynatmada gecici hata/durum etiketleri ASLA ekranda kalmasin. */
  function clearTransient() {
    if (!state) return;
    setStatus('');
    state.handlingError = false;
    if (state.ui && state.ui.sub && state.streams && state.streams[state.qIndex]) {
      state.ui.sub.textContent = state.streams[state.qIndex].label || '';
    }
  }

  /* iframe url'ine autoplay ekle (embed autoPlay=false ise fragman baslamaz) */
  function withAutoplay(u) {
    var s = String(u || '');
    if (/[?&]autoplay=/.test(s)) return s;
    return s + (s.indexOf('?') >= 0 ? '&' : '?') + 'autoplay=true';
  }

  /* iframe uzerine PARENT DOM'da gorunur "Geri" butonu (mouse + kumanda) */
  function buildBackButton(rootEl) {
    var b = mk('div', 'pl-back focused', '← Geri');
    b.setAttribute('data-nav', '1');
    b.setAttribute('tabindex', '0');
    b.addEventListener('click', function () { leave(); }, false);
    b.addEventListener('mouseenter', function () { wakeBack(); }, false);
    rootEl.appendChild(b);
    rootEl.addEventListener('mousemove', function () { wakeBack(); }, false);
    return b;
  }

  /* ---------------- iframe (embed) modu ---------------- */
  function loadEmbed(host, url, onErr) {
    var fr = document.createElement('iframe');
    fr.className = 'player-iframe';
    fr.setAttribute('allow', 'autoplay; fullscreen');
    fr.setAttribute('allowfullscreen', 'true');
    fr.setAttribute('frameborder', '0');
    fr.setAttribute('scrolling', 'no');
    fr.style.position = 'absolute';
    fr.style.left = '0';
    fr.style.top = '0';
    fr.style.width = '100%';        /* sahneyi doldurur (16:9'da 1920x1080) */
    fr.style.height = '100%';
    fr.style.border = '0';
    fr.style.background = '#000';
    fr.onerror = function () { if (onErr) onErr('iframe yuklenemedi'); };
    fr.src = url;
    host.appendChild(fr);
    return fr;
  }

  /* ---------------- ekran ---------------- */
  var screen = {
    name: 'player',
    enter: function (cnt, params) {
      container = cnt;
      var p = params || {};
      /* Yukleme akisi (playflow) hazirlanmis akisi/motoru devreder. Tekrar dene ile ayni
         params yeniden kullanilirsa olu motoru benimsememek icin bir kez tuketilir. */
      var pre = p.prepared || null;
      p.prepared = null;
      state = {
        alive: true,
        itemId: p.itemId,
        episodeId: p.episodeId || null,
        requestedKind: p.kind || null,
        title: p.title || '',
        type: p.type || 'movie',
        detail: p.detail || null,
        duration: 0,
        lastSent: -999,
        seeking: false,
        seekTarget: 0,
        holdCount: 0,
        ended: false,
        streamReady: false,
        model: null, sel: null, want: null, subs: null, tPanel: null, tOpen: false, subSeq: 0, uiOn: true,
        next: null, nextBox: null, nextBtn: null, nextHint: null, nextDismissed: false
      };

      clear(container);
      var ui = buildUI();
      state.ui = ui;
      container.appendChild(ui.root);
      /* altyazi katmani + panel, kontrol katmaninin KARDESI (kontroller gizlenince de gorunur / acik kalir) */
      if (DZ.subs && DZ.tracks) state.subs = DZ.subs.createLayer(ui.root, subsTime);
      if (DZ.tracksPanel && DZ.tracks) state.tPanel = DZ.tracksPanel.create(ui.root);
      ui.title.textContent = state.title;
      setStatus(pre ? '' : 'Yukleniyor...');
      showUI();

      var useAv = hasAvplay();
      console.log('[player] motor: ' + (useAv ? 'AVPlay' : 'HTML5 video'));

      var current = state;
      var generation = 0;
      state.streams = pre && pre.streams ? pre.streams : [];
      state.tried = {};
      if (pre && pre.tried) { for (var tk in pre.tried) { if (pre.tried.hasOwnProperty(tk)) state.tried[tk] = pre.tried[tk]; } }
      state.reported = pre && pre.reported ? pre.reported : {};
      state.duration = pre && pre.duration ? pre.duration : 0;
      state.qIndex = 0;
      if (pre && pre.streams) setModel(state, state.streams, pre.subtitles, pre.audio);
      function alive() { return state === current && current.alive; }
      function report(event, code, detail) {
        if (!alive()) return;
        var stream = current.streams[current.qIndex];
        if (!stream || !stream.attempt_token) return;
        var key = stream.attempt_token + ':' + event;
        if (current.reported[key]) return;
        current.reported[key] = true;
        var payload = {attempt_token:stream.attempt_token,event:event,code:code || '',
          engine:current.iframeMode ? 'embed' : useAv ? 'avplay' : 'html5'};
        /* hata ayrintisi (<=120): yalniz basarisizlikta, bos degilse (sunucuda alan yoksa zararsiz) */
        var det = event === 'failure' ? clipDetail(detail) : '';
        if (det) payload.detail = det;
        DZ.api.playbackReport(payload).then(function () {}, function () {});
      }
      function failed(message, code, detail) {
        if (!alive() || current.handlingError) return;
        current.handlingError = true;
        if (current.startTimer) clearTimeout(current.startTimer);
        code = g.navigator && g.navigator.onLine === false ? 'offline' : (code || 'playback_failed');
        report('failure', code, detail);
        var next = -1;
        if (code !== 'autoplay' && code !== 'offline' && code !== 'aborted') {
          for (var i=0; i<current.streams.length; i++) {
            if (!current.tried[i] && !isEmbedStream(current.streams[i])) { next=i; break; }
          }
        }
        if (next >= 0) {
          setStatus('Diğer kaynak deneniyor…');
          /* Kaynak prepare aşamasında çökerse motorun cur değeri henüz 0'dır.
             Sunucudan gelen kayıtlı konumu koru; oynatma başladıysa güncel konum
             zaten daha önceliklidir. */
          startStream(next, (current.eng && current.eng.cur) || current.resumePosition || 0);
          return;
        }
        setStatus('Oynatma başarısız');
        DZ.modal.open({title:'Oynatılamadı',message:String(message),
          buttons:[{label:'Geri',primary:true,value:'back'},{label:'Yeniden dene',value:'retry'}],
          onDone:function(value){ if (!alive()) return; if(value==='retry') DZ.app.go('player',p,true); else DZ.app.back(); }});
      }
      /* /api/streams hic akis vermedi: sunucunun kaynak bulucu durumu (res.finder, js/finder.js) varsa onun metni (aranıyor / bulunamadi);
         aranirken 5 sn'de bir yoklanir, bulununca panel kapanir ve akis yeniden istenir. Yoksa genel mesaj. */
      function noStreams(finder) {
        if (!alive() || current.handlingError) return;
        var F = DZ.finder;
        var isEp = current.type === 'series' || !!(current.episodeId && current.episodeId !== current.itemId);
        var fmsg = F && F.messageFor ? F.messageFor(finder, isEp) : null;
        if (!fmsg) { failed('Bu içerik için kullanılabilir video kaynağı bulunamadı'); return; }
        current.handlingError = true;
        setStatus(F.titleFor(finder));
        var watch = null;
        function stopWatch() { if (watch) { watch.stop(); watch = null; } }
        function again() { stopWatch(); if (!alive()) return; DZ.app.go('player', p, true); }
        DZ.modal.open({title: F.titleFor(finder), message: fmsg,
          buttons:[{label:'Geri',primary:true,value:'back'},{label:'Yeniden dene',value:'retry'}],
          onDone:function(value){ stopWatch(); if (!alive()) return; if(value==='retry') again(); else DZ.app.back(); },
          onCancel:stopWatch});
        if (finder.state === 'searching' && F.poll) {
          watch = F.poll(current.itemId, current.episodeId, function (state) {
            watch = null;
            if (!alive()) return;
            if (state === 'found') { if (DZ.modal.close) DZ.modal.close(); again(); }
            else { current.handlingError = false; noStreams({state:'not_found'}); }
          }, {isOpen: function () { return alive() && (!DZ.modal.isOpen || DZ.modal.isOpen()); }});
        }
      }
      function startStream(index, resume, adopted) {
        if (!alive()) return;
        current.resumePosition = resume || 0;
        generation++;
        var ownGeneration=generation;
        if (current.startTimer) clearTimeout(current.startTimer);
        if (current.progTimer) clearInterval(current.progTimer);
        if (current.eng) current.eng.destroy();
        if (current.backBtn && current.backBtn.parentNode) current.backBtn.parentNode.removeChild(current.backBtn);
        current.backBtn = null;
        if (current.reportBtn && current.reportBtn.parentNode) current.reportBtn.parentNode.removeChild(current.reportBtn);
        current.reportBtn = null;
        current.qIndex=index;current.tried[index]=true;current.handlingError=false;
        current.streamReady=false;current.iframeMode=false;
        var stream=current.streams[index];
        var played=0,lastTime=resume || 0;
        function valid(){return alive() && ownGeneration===generation;}
        var cb={
          ready:function(){if(!valid())return;current.streamReady=true;clearTransient();paint();showUI();
            current.progTimer=setInterval(function(){if(valid() && current.eng.playing)sendProgress(false);},PROGRESS_MS);},
          started:function(){if(valid())clearTransient();},
          time:function(){if(!valid() || !current.eng)return;
            var delta=current.eng.cur-lastTime;lastTime=current.eng.cur;
            if(delta>0 && delta<5 && !current.seeking)played+=delta;
            if(played>=3 && current.startTimer){clearTimeout(current.startTimer);current.startTimer=null;}
            if(played>=10)report('success','');
            if(!current.seeking)paint();
            checkNext();},
          buffering:function(on){if(valid())setStatus(on?'Arabellek…':'');},
          completed:function(){if(valid())onCompleted();},
          error:function(message,code,detail){if(valid())failed(message,code,detail);}
        };
        current.ui.sub.textContent=stream.label || '';
        current.ui.ui.classList.remove('hide');current.uiOn=true;syncSubsPos();
        updateQualityLabel();renderQualityMenu();
        syncTracks(index);
        if(isEmbedStream(stream)){
          current.iframeMode=true;
          var frame=loadEmbed(ui.media,withAutoplay(stream.url),function(m){cb.error(m,'network','iframe-error');});
          current.eng={cur:0,dur:0,playing:true,play:function(){},pause:function(){},seek:function(){},destroy:function(){if(frame.parentNode)frame.parentNode.removeChild(frame);}};
          current.streamReady=false;current.ui.ui.classList.add('hide');current.uiOn=false;
          clearTransient();
          current.backBtn=buildBackButton(current.ui.root);
          /* Elle bildirim dugmesi hata etiketi gibi gorunmesin: Geri ile birlikte belirir, 3 sn sonra gizlenir. */
          current.reportBtn=mk('div','pl-back hidden','Sorun bildir · Kırmızı');
          current.reportBtn.style.left='300px';
          current.reportBtn.addEventListener('click',function(){if(valid())current.reportBroken();},false);
          current.ui.root.appendChild(current.reportBtn);
          wakeBack();
          // Cross-origin embeds cannot expose video errors. Red key reports explicitly.
          return;
        }
        current.ui.qWrap.classList.remove('hidden');
        if(adopted){
          /* Yukleme akisinda zaten hazirlanip OYNAMAYA baslamis motor: callback'leri devral, ekrana bagla. */
          current.eng=adopted;adopted.setCallbacks(cb);adopted.attach(ui.media);
          lastTime=adopted.cur || resume || 0;
          cb.ready();
          return;
        }
        current.eng=createEngine(ui.media,useAv,cb);current.eng.dur=current.duration;
        current.startTimer=setTimeout(function(){if(valid())failed('Video başlamadı','timeout','start-timeout');},35000);
        current.eng.load(stream.url,resume || 0,stream.type);
      }
      current.startStream=startStream;
      current.reportBroken=function(){failed('Kaynak çalışmıyor olarak bildirildi','playback_failed','user-report');};
      refreshNext();
      if(!current.detail && current.type==='series') DZ.api.detail(current.itemId,DZ.api.profileId()).then(function(d){if(alive()){current.detail=d;refreshNext();}},function(){});
      if(pre && pre.streams && pre.streams.length){
        startStream(pre.index || 0,pre.resume || 0,pre.engine || null);
        return;
      }
      DZ.api.streams(current.itemId,current.episodeId,current.requestedKind).then(function(res){
        if(!alive())return;
        current.streams=res.streams || [];current.duration=res.duration || 0;
        if(!current.streams.length){noStreams(res.finder);return;}
        setModel(current,current.streams,res.subtitles,res.audio);
        /* ses/altyazi secenegi varsa: tercih -> varsayilan kural (Türkçe soft > Türkçe gömülü > İngilizce soft > kapalı) */
        var first=current.model && DZ.tracks.hasChoices(current.model) ? DZ.tracks.choose(current.model,DZ.tracks.loadPrefs()).index : 0;
        startStream(first,res.resume_position || 0);
      },function(err){if(alive())failed(err.message,'network','streams-api');});

    },

    key: function (ev) {
      if (!state) return false;
      if (ev.name === 'red' && state.reportBroken) { state.reportBroken(); return true; }
      if (!state.eng) { if (ev.name === 'back' || ev.name === 'stop') leave(); return true; }
      /* ses/altyazi paneli acikken: tum tuslar panele (Geri = paneli kapat, player'dan cikma) */
      if (state.tOpen) { handlePanelAction(state.tPanel.key(ev.name)); return true; }
      /* kalite menusu acikken: ok=gez, enter=uygula, BACK=menuyu kapat (player'dan cikma) */
      if (state.qMenu) {
        switch (ev.name) {
          case 'up': moveQ(-1); return true;
          case 'down': moveQ(1); return true;
          case 'enter':
          case 'playpause':
          case 'play': applyQuality(state.qHover); return true;
          case 'back':
          case 'stop': closeQualityMenu(); return true;
          default: return true;
        }
      }
      if (state.iframeMode) {
        if (ev.name === 'up') { openQualityMenu(); return true; }
        /* iframe cross-origin: fragman autoplay ile baslar. Odak Geri'ye KILITLENMEZ.
           BACK/stop -> cikis. Enter: Geri GORUNURken cikis, GIZLIyken sadece goster (cikma).
           Ok/diger tuslar: sadece Geri'yi uyandir, onceki ekrana ATMA. */
        if (ev.name === 'back' || ev.name === 'stop' || ev.name === 'exit') { leave(); return true; }
        if (ev.name === 'enter' || ev.name === 'playpause' || ev.name === 'play') {
          if (state.backVisible) { leave(); return true; }
          wakeBack();
          return true;
        }
        wakeBack();
        return true;
      }
      if (state.ended) {
        if (ev.name === 'enter' || ev.name === 'playpause' || ev.name === 'play') {
          if (state.playNext) state.playNext();
          return true;
        }
        if (ev.name === 'back' || ev.name === 'stop') { leave(); return true; }
        return true;
      }
      /* "Sonraki bolum" karti gorunurken: OK = simdi oynat (duraklatilmissa OK yine oynat/duraklat), GERI = teklifi iptal
         (oynatici acik kalir, otomatik gecis de iptal). Oynat/Duraklat tusu her zaman oynat/duraklat. */
      if (state.nextBox) {
        if (ev.name === 'back') { dismissNext(); showUI(); return true; }
        if (ev.name === 'enter' && state.eng.playing) { goNext(); return true; }
      }
      showUI();
      switch (ev.name) {
        case 'left': nudge(-1); return true;
        case 'right': nudge(1); return true;
        case 'rew': nudge(-1); return true;
        case 'ff': nudge(1); return true;
        case 'enter':
        case 'playpause': togglePlay(); return true;
        case 'play': if (!state.eng.playing) togglePlay(); return true;
        case 'pause': if (state.eng.playing) togglePlay(); return true;
        case 'back':
        case 'stop': leave(); return true;
        case 'up': openQualityMenu(); return true;   /* kalite menusunu ac */
        case 'down':
        case 'yellow': openTracksPanel(); return true;   /* ses ve altyazi paneli */
        default: return true;
      }
    },

    back: function () { leave(); return true; },

    exit: function () {
      if (!state) return;
      sendProgress(true);
      state.alive = false;
      if (state.startTimer) clearTimeout(state.startTimer);
      if (state.hideTimer) clearTimeout(state.hideTimer);
      if (state.seekTimer) clearTimeout(state.seekTimer);
      if (state.progTimer) clearInterval(state.progTimer);
      if (state.countTimer) clearInterval(state.countTimer);
      removeNextBox();
      if (state.backDimTimer) clearTimeout(state.backDimTimer);
      try { if (state.eng) state.eng.destroy(); } catch (e) {}
      try { if (state.subs) state.subs.destroy(); } catch (eS) {}
      try { if (state.tPanel) state.tPanel.destroy(); } catch (eP) {}
      state = null;
      container = null;
    }
  };

  DZ.screens = DZ.screens || {};
  DZ.screens.player = screen;
  /* playflow.js (yukleme modali) ayni motoru ekrandan ONCE hazirlamak icin kullanir. */
  DZ.player = { createEngine: createEngine, hasAvplay: hasAvplay, isEmbedStream: isEmbedStream, isHlsStream: isHlsStream, clipDetail: clipDetail,
    nextEpisodeOf: nextEpisodeOf, episodeInfo: episodeInfo, episodeLabel: episodeLabel, NEXT_WINDOW: NEXT_WINDOW, NEXT_COUNT: NEXT_COUNT };
})(window);
