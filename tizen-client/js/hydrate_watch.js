/* hydrate_watch.js - detay "yukleniyor / hazir" izleyicisi (bellek ici, kalici degil). ES2017, opsiyonel zincir YOK.
   Sunucu detayda `hydrating: true` donerse (arka plan hidrasyonu suruyor) yapim id+baslik buraya alinir; her 3 sn
   `GET /api/detail/{id}?poll=1` (sunucuda ASLA is baslatmaz) en cok 120 sn (40 deneme). Kurallar:
   - id basina tek izleyici; en cok 3 eszamanli yoklama (fazlasi kuyrukta bekler, sira gelince baslar)
   - ag/sunucu hatasinda sessizce durur (sonuc 'error', toast YOK); sure dolarsa 'timeout' (toast YOK)
   - karar: hydrating true -> bekle; false/undefined -> `availability.state=='unavailable'` + `reason=='no_video_source'` (izleme kaynagi YOK, hata degil)
     'no_source'; aksi halde film: 'ready'; dizi: seasons.length > 0 ? 'ready' : 'unavailable' (bolumler alinamadi = gercek getirme hatasi)
   Sonuc olayi: {id, title, result: 'ready'|'no_source'|'unavailable'|'timeout'|'error', data}. Kullanici baska ekrana gecse de surer;
   varsayilan abone belirgin bildirim (DZ.toast.showRich) gosterir (odagi calmaz). Detay ekrani ayrica on() ile dinleyip acik detayi yeniler.
   DZ.hydrate.create(opts) test icin: opts {api, profileId, timers, now, interval, maxMs, maxPolls, maxConcurrent}. */
(function (g) {
  'use strict';
  var DZ = g.DZ = g.DZ || {};

  var INTERVAL = 3000;
  var MAX_MS = 120000;
  var MAX_POLLS = 40;
  var MAX_CONCURRENT = 3;

  var TEXT_UNAVAILABLE = 'Bölümler şu an alınamadı, daha sonra tekrar deneyin';
  var TEXT_NO_SOURCE_SERIES = 'Bu dizi için henüz izleme kaynağı yok.';
  var TEXT_NO_SOURCE_MOVIE = 'Bu film için henüz izleme kaynağı yok.';

  function isArr(v) { return Object.prototype.toString.call(v) === '[object Array]'; }

  /* sunucu "izleme kaynagi yok" diyor (availability.state 'unavailable' + reason 'no_video_source'): bu bir getirme HATASI degil */
  function noSource(d) {
    var a = d && d.availability;
    return !!(a && a.state === 'unavailable' && a.reason === 'no_video_source');
  }
  function noSourceText(d) { return d && d.type === 'series' ? TEXT_NO_SOURCE_SERIES : TEXT_NO_SOURCE_MOVIE; }

  /* yanit -> 'pending' (hidrasyon suruyor) | 'ready' | 'no_source' | 'unavailable' | 'error' (gecersiz yanit).
     `hydrating` alani yoksa (eski sunucu) false sayilir. */
  function decide(d) {
    if (!d || typeof d !== 'object') return 'error';
    if (d.hydrating === true) return 'pending';
    if (d.type !== 'series') return noSource(d) ? 'no_source' : 'ready';
    if (isArr(d.seasons) && d.seasons.length > 0) return 'ready';
    return noSource(d) ? 'no_source' : 'unavailable';
  }

  /* olay -> zengin bildirim {title, message, kind} (yok: null). Zaman asimi / hata icin bildirim yok.
     ready: etiket zaten "Izlemeye hazir" oldugundan mesaj bos (tekrar yok); unavailable: sari-soluk 'warn'. */
  function noticeFor(evt) {
    if (!evt) return null;
    if (evt.result === 'ready') return { title: evt.title || 'İçerik', message: '', kind: 'success' };
    if (evt.result === 'no_source') return { title: evt.title || 'İçerik', message: noSourceText(evt.data), kind: 'warn' };
    if (evt.result === 'unavailable') return { title: evt.title || 'İçerik', message: TEXT_UNAVAILABLE, kind: 'warn' };
    return null;
  }

  /* olay -> duz toast metni (yok: null); yalnizca DZ.toast.showRich yoksa yedek. */
  function messageFor(evt) {
    if (!evt) return null;
    if (evt.result === 'ready') return '«' + (evt.title || 'İçerik') + '» izlemeye hazır';
    if (evt.result === 'no_source') return noSourceText(evt.data);
    if (evt.result === 'unavailable') return TEXT_UNAVAILABLE;
    return null;
  }

  function create(o) {
    o = o || {};
    var interval = o.interval || INTERVAL;
    var maxMs = o.maxMs || MAX_MS;
    var maxPolls = o.maxPolls || MAX_POLLS;
    var maxConcurrent = o.maxConcurrent || MAX_CONCURRENT;
    var T = o.timers || {
      setTimeout: function (fn, ms) { return g.setTimeout(fn, ms); },
      clearTimeout: function (id) { g.clearTimeout(id); }
    };
    var now = o.now || function () { return new Date().getTime(); };
    function api() { return o.api || (DZ.api || null); }
    function profile() {
      if (o.profileId) return o.profileId();
      var a = api();
      return a && a.profileId ? a.profileId() : null;
    }

    var entries = {};      /* id -> {id,title,attempts,timer,active,stopped,startedAt} (kuyruktakiler dahil) */
    var queue = [];        /* baslamayi bekleyen girisler (FIFO) */
    var activeCount = 0;
    var listeners = [];

    function emit(evt) {
      var list = listeners.slice();
      for (var i = 0; i < list.length; i++) {
        try { list[i](evt); } catch (e) { try { console.log('[hydrate] dinleyici hata: ' + e); } catch (e2) {} }
      }
    }

    function pump() {
      while (activeCount < maxConcurrent && queue.length) start(queue.shift());
    }

    function start(e) {
      e.active = true;
      activeCount++;
      e.startedAt = now();
      schedule(e);
    }

    function schedule(e) {
      e.timer = T.setTimeout(function () { e.timer = null; poll(e); }, interval);
    }

    function release(e) {
      e.stopped = true;
      if (e.timer !== null && e.timer !== undefined) { T.clearTimeout(e.timer); e.timer = null; }
      if (entries[e.id] === e) delete entries[e.id];
      var qi = queue.indexOf(e);
      if (qi >= 0) queue.splice(qi, 1);
      if (e.active) { e.active = false; activeCount--; }
    }

    function finish(e, result, data) {
      if (e.stopped) return;
      release(e);
      emit({ id: e.id, title: e.title, result: result, data: data || null });
      pump();
    }

    function poll(e) {
      if (e.stopped) return;
      e.attempts++;
      var p;
      try {
        var a = api();
        p = a.detail(e.id, profile(), { poll: true });
      } catch (err) { finish(e, 'error'); return; }
      Promise.resolve(p).then(function (d) {
        if (e.stopped) return;
        var r = decide(d);
        if (r === 'pending') {
          if (e.attempts >= maxPolls || now() - e.startedAt >= maxMs) finish(e, 'timeout', d);
          else schedule(e);
          return;
        }
        finish(e, r, d);
      }, function () {
        finish(e, 'error');    /* ag hatasi: sessizce dur */
      });
    }

    return {
      /* izlemeye al; false: bu id zaten izleniyor (ya da id yok) */
      watch: function (id, title) {
        if (id === undefined || id === null || id === '') return false;
        var key = String(id);
        var have = entries[key];
        if (have) {
          if (!have.title && title) have.title = title;
          return false;
        }
        var e = { id: key, title: title || '', attempts: 0, timer: null, active: false, stopped: false, startedAt: 0 };
        entries[key] = e;
        if (activeCount < maxConcurrent) start(e); else queue.push(e);
        return true;
      },
      /* sessizce birak (olay yok) */
      cancel: function (id) {
        var e = entries[String(id)];
        if (!e) return false;
        release(e);
        pump();
        return true;
      },
      stopAll: function () {
        var keys = Object.keys(entries);
        for (var i = 0; i < keys.length; i++) if (entries[keys[i]]) release(entries[keys[i]]);
        queue = [];
      },
      isWatching: function (id) { return !!entries[String(id)]; },
      count: function () { return Object.keys(entries).length; },
      activeCount: function () { return activeCount; },
      /* abone ol; geri donus: abonelikten cik */
      on: function (fn) {
        if (typeof fn !== 'function') return function () {};
        listeners.push(fn);
        return function () {
          var at = listeners.indexOf(fn);
          if (at >= 0) listeners.splice(at, 1);
        };
      }
    };
  }

  /* genel izleyici + varsayilan abone: belirgin bildirim DZ.toast.showRich (odagi calmaz; kullanici hangi ekranda olursa olsun);
     showRich yoksa eski kucuk toast */
  var watcher = create({});
  watcher.on(function (evt) {
    var t = DZ.toast;
    if (!t) return;
    var n = noticeFor(evt);
    if (n && t.showRich) { t.showRich(n); return; }
    var m = messageFor(evt);
    if (m && t.show) t.show(m);
  });

  watcher.create = create;
  watcher.decide = decide;
  watcher.noSource = noSource;
  watcher.noSourceText = noSourceText;
  watcher.noticeFor = noticeFor;
  watcher.messageFor = messageFor;
  watcher.INTERVAL = INTERVAL;
  watcher.MAX_MS = MAX_MS;
  watcher.MAX_POLLS = MAX_POLLS;
  watcher.MAX_CONCURRENT = MAX_CONCURRENT;
  watcher.TEXT_UNAVAILABLE = TEXT_UNAVAILABLE;
  watcher.TEXT_NO_SOURCE_SERIES = TEXT_NO_SOURCE_SERIES;
  watcher.TEXT_NO_SOURCE_MOVIE = TEXT_NO_SOURCE_MOVIE;

  DZ.hydrate = watcher;
})(window);
