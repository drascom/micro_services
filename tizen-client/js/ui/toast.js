/* ui/toast.js - TEK yerde tanimli gecici bildirim (toast) + cevrimdisi uyarisi. ES2017, opsiyonel zincir YOK.
   DZ.toast.show(mesaj[, ms])  -> ekranin altinda kisa sure gorunur (odaga girmez, tus yutmaz).
   DZ.toast.showRich({title, message, kind, ms}) -> ust-ortada buyuk, dikkat ceken kart (kind 'success'|'warn', ms varsayilan 7000);
                                   kose susu (img/ornament-corner.png); tek kart, gelenler sira bekler (en cok 3 bekleyen, fazlasi
                                   dusurulur -> false); odaga girmez, tus yutmaz, pointer-events yok. DZ.toast.hideRich() hepsini kapatir.
   DZ.toast.watchOnline()      -> tarayicinin online/offline olaylarini izler; cevrimdisiyken sol altta kalici uyari.
   DZ.toast.isOffline()        -> navigator.onLine === false
   DZ.toast.errorText(err)     -> hata metni (cevrimdisiyken "Baglanti yok…") */
(function (g) {
  'use strict';
  var DZ = g.DZ = g.DZ || {};

  var TOAST_MS = 3500;
  var RICH_MS = 7000;          /* zengin bildirim: varsayilan gorunme suresi */
  var RICH_EXIT_MS = 400;      /* cikis animasyonu bitsin, sonra siradaki gelsin */
  var RICH_MAX_WAIT = 3;       /* en cok 3 bekleyen (gosterilen haric) */
  var RICH_LABELS = { success: 'İzlemeye hazır', warn: 'Bilgi' };
  var toastEl = null;
  var richEl = null;
  var richParts = null;
  var richTimer = null;
  var richQueue = [];
  var richBusy = false;       /* kart gorunuyor ya da cikis araliginda */
  var offlineEl = null;
  var timer = null;
  var watching = false;

  function host() {
    try {
      var d = (typeof document !== "undefined") ? document : null;
      if (!d) return null;
      return (d.getElementById ? d.getElementById('app') : null) || d.body || null;
    } catch (e) { return null; }
  }

  function make(cls) {
    var n = document.createElement('div');
    n.className = cls;
    return n;
  }

  function isOffline() { return !!(g.navigator && g.navigator.onLine === false); }

  /* ag hatasi metni: cevrimdisiyken sunucu mesaji yerine net bir aciklama */
  function errorText(err) {
    if (isOffline()) return 'Bağlantı yok. İnternet bağlantınızı kontrol edin.';
    return err && err.message ? err.message : 'Bilinmeyen hata';
  }

  function hide() {
    if (timer) { clearTimeout(timer); timer = null; }
    if (toastEl) toastEl.classList.add('hidden');
  }

  function show(message, ms) {
    var h = host();
    if (!h) return false;
    if (!toastEl) { toastEl = make('toast hidden'); h.appendChild(toastEl); }
    else if (!toastEl.parentNode) h.appendChild(toastEl);
    toastEl.textContent = String(message == null ? '' : message);
    toastEl.classList.remove('hidden');
    if (timer) clearTimeout(timer);
    timer = setTimeout(hide, ms || TOAST_MS);
    return true;
  }

  /* ---- zengin bildirim: tek kart, sirali gosterim ---- */
  function richBuild() {
    var card = make('toast-rich tr-off');
    try { card.setAttribute('role', 'status'); card.setAttribute('aria-live', 'polite'); } catch (e) {}
    var tl = make('tr-orn tr-orn-tl');
    var br = make('tr-orn tr-orn-br');
    var body = make('tr-body');
    var label = make('tr-label');
    var title = make('tr-title');
    var msg = make('tr-msg');
    body.appendChild(label); body.appendChild(title); body.appendChild(msg);
    card.appendChild(tl); card.appendChild(br); card.appendChild(body);
    richParts = { label: label, title: title, msg: msg };
    return card;
  }

  function richPlay(item) {
    var h = host();
    if (!h) { richBusy = false; richQueue = []; return; }
    if (!richEl) richEl = richBuild();
    if (!richEl.parentNode) h.appendChild(richEl);
    richEl.classList.remove('tr-success'); richEl.classList.remove('tr-warn');
    richEl.classList.add(item.kind === 'warn' ? 'tr-warn' : 'tr-success');
    richParts.label.textContent = item.label;
    richParts.title.textContent = item.title;
    richParts.msg.textContent = item.message;
    if (item.message) richParts.msg.classList.remove('hidden'); else richParts.msg.classList.add('hidden');
    richEl.classList.remove('tr-off');
    richBusy = true;
    richTimer = setTimeout(richEnd, item.ms);
  }

  function richEnd() {
    if (richEl) richEl.classList.add('tr-off');
    richTimer = setTimeout(richNext, RICH_EXIT_MS);
  }

  function richNext() {
    richTimer = null;
    var next = richQueue.shift();
    if (next) richPlay(next); else richBusy = false;
  }

  /* o: {title, message, kind:'success'|'warn', ms}. true: gosterildi ya da siraya alindi; false: kuyruk dolu / ekran yok */
  function showRich(o) {
    o = o || {};
    if (!host()) return false;
    var kind = o.kind === 'warn' ? 'warn' : 'success';
    var ms = Number(o.ms);
    var item = {
      kind: kind,
      label: o.label ? String(o.label) : RICH_LABELS[kind],
      title: String(o.title == null ? '' : o.title),
      message: String(o.message == null ? '' : o.message),
      ms: ms > 0 ? ms : RICH_MS
    };
    if (!richBusy) { richPlay(item); return true; }
    if (richQueue.length >= RICH_MAX_WAIT) return false;
    richQueue.push(item);
    return true;
  }

  /* gorunen karti kapat, bekleyenleri at */
  function hideRich() {
    if (richTimer) { clearTimeout(richTimer); richTimer = null; }
    richQueue = [];
    richBusy = false;
    if (richEl) richEl.classList.add('tr-off');
  }

  function showOffline(on) {
    var h = host();
    if (!h) return;
    if (!offlineEl) { offlineEl = make('offline-note hidden'); offlineEl.textContent = 'Çevrimdışı · İnternet bağlantısı yok'; }
    if (!offlineEl.parentNode) h.appendChild(offlineEl);
    if (on) offlineEl.classList.remove('hidden'); else offlineEl.classList.add('hidden');
  }

  function watchOnline() {
    showOffline(isOffline());
    if (watching || !g.addEventListener) return;
    watching = true;
    g.addEventListener('offline', function () { showOffline(true); }, false);
    g.addEventListener('online', function () { showOffline(false); show('Bağlantı geri geldi'); }, false);
  }

  DZ.toast = { errorText: errorText, show: show, hide: hide, showRich: showRich, hideRich: hideRich, isOffline: isOffline, watchOnline: watchOnline, showOffline: showOffline, MS: TOAST_MS, RICH_MS: RICH_MS, RICH_MAX_WAIT: RICH_MAX_WAIT };
})(window);
