/* notifications.js - sunucu bildirimleri (kaynak bulucu "kaynak bulundu") yoklayicisi. ES2017, opsiyonel zincir YOK.
   `GET /api/notifications?profile=&since=<id>` -> {items:[{id,kind:'source_found',title,season,episode,...}], last_id}; okununca
   `POST /api/notifications/read {upto}` (API.md "Kaynak bulucu ve bildirimler").
   - Yalniz ana ekran / detay / katalog (film, dizi, Listem, arama) ekranlarinda 30 sn'de bir; oynaticida (ve profil/ayarlar ekranlarinda)
     DURAKLAR, bu ekranlara donuste bir kez hemen sorgular (DZ.app.mount -> onScreen).
   - `last_id` profil basina kalici (localStorage `dz_notif_since_<profil>`). Anahtar YOKSA (ilk calistirma): eski bildirimler toast YAPILMAZ,
     yalnizca last_id ilerletilir (50'den fazlaysa sayfa sayfa).
   - Yeni `source_found`: zengin toast "Kaynak bulundu: <baslik> S04 B02" (dizi degilse yalniz baslik); gosterildikten sonra `read`.
     Toast kuyrugu doluysa (DZ.toast.showRich false) kalanlar bir sonraki yoklamaya birakilir (last_id yalniz gosterilene kadar ilerler).
   - Toast yalniz bilgidir (tus/odak almaz): ilgili detaya gitme yok.
   - Ag/sunucu hatasi sessiz; cevrimdisiyken istek atilmaz.
   DZ.notifications.create(opts) test icin: opts {api, store, toast, timers, profileId, interval}. */
(function (g) {
  'use strict';
  var DZ = g.DZ = g.DZ || {};

  var INTERVAL = 30000;
  var PAGE = 50;                    /* sunucunun tek yanittaki en cok oge sayisi */
  var MAX_PAGES = 10;               /* ilk calistirma: eski bildirimleri atlarken en cok bu kadar sayfa */
  var KEY_PREFIX = 'dz_notif_since_';
  var POLL_SCREENS = { home: true, detail: true, catalog: true };

  function isArr(v) { return Object.prototype.toString.call(v) === '[object Array]'; }
  function pad2(v) { return v < 10 ? '0' + v : String(v); }

  /* "Kaynak bulundu: <baslik> S04 B02" (bolum yoksa yalniz baslik) */
  function textFor(item) {
    var title = item && item.title ? String(item.title) : 'İçerik';
    var hasEp = item && typeof item.season === 'number' && typeof item.episode === 'number';
    return { label: 'Kaynak bulundu', title: title + (hasEp ? ' S' + pad2(item.season) + ' B' + pad2(item.episode) : '') };
  }

  function create(o) {
    o = o || {};
    var interval = o.interval || INTERVAL;
    var T = o.timers || {
      setInterval: function (fn, ms) { return g.setInterval(fn, ms); },
      clearInterval: function (id) { g.clearInterval(id); }
    };
    function api() { return o.api || DZ.api; }
    function store() { return o.store || DZ.store; }
    function toast() { return o.toast || DZ.toast || null; }
    function profile() {
      if (o.profileId) return o.profileId();
      var a = api();
      return a && a.profileId ? a.profileId() : null;
    }

    var timer = null;
    var onPollScreen = false;      /* son bilinen ekran yoklanan bir ekran mi */
    var busy = false;

    function key(pid) { return KEY_PREFIX + pid; }
    /* null = ilk calistirma (anahtar yok) */
    function loadSince(pid) {
      var v = store().get(key(pid), null);
      if (v === null || v === undefined || v === '') return null;
      var n = Number(v);
      return isNaN(n) ? null : n;
    }
    function saveSince(pid, n) { store().set(key(pid), String(n)); }

    function show(item) {
      var t = toast();
      if (!t) return false;
      var x = textFor(item);
      if (t.showRich) return t.showRich({ label: x.label, title: x.title, message: '', kind: 'success' }) !== false;
      if (t.show) return t.show(x.label + ': ' + x.title) !== false;
      return false;
    }

    function markRead(pid, upto) {
      try { api().markNotificationsRead(pid, upto).then(function () {}, function () {}); } catch (e) {}
    }

    /* ilk calistirma: eski bildirimleri toast'lamadan last_id'yi ilerlet (50'den fazlaysa sonraki sayfa) */
    function skipHistory(pid, since, depth) {
      return api().notifications(pid, since).then(function (res) {
        var items = res && isArr(res.items) ? res.items : [];
        var last = res && res.last_id !== undefined && res.last_id !== null ? Number(res.last_id) : NaN;
        if (isNaN(last)) return;
        saveSince(pid, last);
        if (items.length >= PAGE && depth + 1 < MAX_PAGES) return skipHistory(pid, last, depth + 1);
      });
    }

    function handle(pid, since, res) {
      var items = res && isArr(res.items) ? res.items : [];
      var last = res && res.last_id !== undefined && res.last_id !== null ? Number(res.last_id) : NaN;
      var upto = null;               /* islenen (gosterilen ya da bilinmeyen tur) son id */
      var complete = true;
      for (var i = 0; i < items.length; i++) {
        var it = items[i];
        if (!it || typeof it.id !== 'number' || it.id <= since) continue;
        if (it.kind === 'source_found') {
          if (!show(it)) { complete = false; break; }   /* kuyruk dolu: bu ve sonrakiler sonraki yoklamaya */
        }
        upto = it.id;
      }
      var next = since;
      if (upto !== null) next = upto;
      if (complete && !isNaN(last) && last > next) next = last;
      if (next !== since) saveSince(pid, next);
      if (upto !== null) markRead(pid, upto);
    }

    function pollNow() {
      if (busy) return;
      var pid = profile();
      if (!pid) return;
      var t = toast();
      if (t && t.isOffline && t.isOffline()) return;
      var since = loadSince(pid);
      busy = true;
      var p;
      try {
        p = since === null ? skipHistory(pid, null, 0) : api().notifications(pid, since).then(function (res) { handle(pid, since, res); });
      } catch (e) { busy = false; return; }
      Promise.resolve(p).then(function () { busy = false; }, function () { busy = false; });
    }

    function stopTimer() {
      if (timer !== null) { T.clearInterval(timer); timer = null; }
    }

    /* app.js her ekran gecisinde cagirir: yoklanan ekranda zamanlayici calisir, digerlerinde (oynatici...) durur */
    function onScreen(name) {
      if (!POLL_SCREENS[name]) { stopTimer(); onPollScreen = false; return; }
      if (timer === null) timer = T.setInterval(pollNow, interval);
      if (!onPollScreen) { onPollScreen = true; pollNow(); }
    }

    return {
      onScreen: onScreen,
      pollNow: pollNow,
      stop: function () { stopTimer(); onPollScreen = false; },
      isRunning: function () { return timer !== null; },
      lastSince: function (pid) { return loadSince(pid || profile()); }
    };
  }

  var inst = create({});
  inst.create = create;
  inst.textFor = textFor;
  inst.keyFor = function (pid) { return KEY_PREFIX + pid; };
  inst.INTERVAL = INTERVAL;
  inst.POLL_SCREENS = POLL_SCREENS;
  DZ.notifications = inst;
})(window);
