/* stage.js - dinamik sahne (responsive). ES2017.
   Arayuz 1920x1080 "tasarim birimi" uzerinde cizilir; sahne (#app) pencereyi HER ZAMAN tam doldurur:
     - pencere orani >= 16:9 : olcek = innerHeight/1080, sahne = (innerWidth/olcek) x 1080   (>= 1920 x 1080)
     - pencere orani <  16:9 : olcek = innerWidth/1920, sahne = 1920 x (innerHeight/olcek)   (>= 1920 x 1080)
   TV (1920x1080, 16:9): olcek 1, sahne tam 1920x1080 -> degisiklik yok. 16:9 disinda ust/alt/yan siyah bant yok;
   fazla alani arka plan/hero gorseli doldurur, icerik sola/uste hizali kalir.
   Sahne boyutu CSS degiskenlerine yazilir (--stage-w / --stage-h / --stage-scale, birim: tasarim pikseli); boyuta bagli
   yerlesimler (pencereli listeler, gorunen satir sayisi) DZ.stage.onChange ile yeniden hesaplanir.
   innerWidth/innerHeight tarayici yakinlastirmasi (Cmd +/-) ve yuksek DPI'yi zaten yansitir; ek islem gerekmez. */
(function (g) {
  'use strict';
  var DZ = g.DZ = g.DZ || {};

  var BASE_W = 1920, BASE_H = 1080;   /* tasarim (TV) cozunurlugu */
  var MIN_W = 320, MIN_H = 180;       /* alt sinir: bundan kucuk pencere bu boyuttaymis gibi hesaplanir (olcek >= ~0.167) */
  var DEBOUNCE_MS = 120;              /* yeniden yerlesim dinleyicilerini sakinlestir (olcek/CSS degiskenleri aninda) */

  /* ceil, ama kayan nokta gurultusunu (1920.0000000000002) bir ust piksele tasima */
  function up(v) { return Math.ceil(v - 1e-6); }

  /* SAF fonksiyon: pencere (innerWidth, innerHeight) -> {scale, w, h}. Olcek sahneyi pencereye sigdirir; w/h sahnenin
     tasarim pikseli cinsinden olculeri (her zaman >= 1920 x 1080; w*scale >= pencere genisligi, h*scale >= yuksekligi). */
  function fit(innerWidth, innerHeight) {
    var iw = +innerWidth, ih = +innerHeight;
    if (!(iw > 0)) iw = BASE_W;
    if (!(ih > 0)) ih = BASE_H;
    if (iw < MIN_W) iw = MIN_W;
    if (ih < MIN_H) ih = MIN_H;
    var scale, w, h;
    if (iw * BASE_H >= ih * BASE_W) {          /* 16:9 ya da daha yatay -> yukseklige gore */
      scale = ih / BASE_H;
      w = up(iw * BASE_H / ih);
      h = BASE_H;
    } else {                                   /* daha dar/uzun -> genislige gore */
      scale = iw / BASE_W;
      w = BASE_W;
      h = up(ih * BASE_W / iw);
    }
    if (w < BASE_W) w = BASE_W;
    if (h < BASE_H) h = BASE_H;
    return { scale: scale, w: w, h: h };
  }

  var stage = {
    BASE_W: BASE_W, BASE_H: BASE_H, MIN_W: MIN_W, MIN_H: MIN_H,
    w: BASE_W, h: BASE_H, scale: 1,
    fit: fit
  };
  var listeners = [];
  var timer = null;
  var inited = false;
  var notified = { w: BASE_W, h: BASE_H };

  /* pencere olcusunu oku, CSS degiskenlerini + #app olcegini yaz. Sahne boyutu degistiyse true. */
  function apply() {
    var r = fit(g.innerWidth, g.innerHeight);
    var changed = r.w !== stage.w || r.h !== stage.h;
    stage.w = r.w; stage.h = r.h; stage.scale = r.scale;
    var d = g.document;
    var root = d && d.documentElement;
    try {
      if (root && root.style && root.style.setProperty) {
        root.style.setProperty('--stage-w', r.w + 'px');
        root.style.setProperty('--stage-h', r.h + 'px');
        root.style.setProperty('--stage-scale', String(r.scale));
      }
      var app = d && d.getElementById ? d.getElementById('app') : null;
      if (app && app.style) app.style.transform = 'translate3d(0px,0px,0) scale(' + r.scale + ')';
    } catch (e) {}
    return changed;
  }

  function notify() {
    timer = null;
    apply();   /* orientationchange'de innerWidth/innerHeight gec guncellenebilir: son degeri yeniden oku */
    if (notified.w === stage.w && notified.h === stage.h) return;
    notified.w = stage.w; notified.h = stage.h;
    for (var i = 0; i < listeners.length; i++) {
      try { listeners[i](stage); } catch (e) { if (g.console) g.console.log('[stage] dinleyici hata: ' + e); }
    }
  }

  /* pencere degisti: olcek hemen (ucuz), agir yeniden yerlesim debounce'lu */
  function onWindowChange() {
    apply();
    if (timer) g.clearTimeout(timer);
    timer = g.setTimeout(notify, DEBOUNCE_MS);
  }

  stage.apply = apply;
  stage.onChange = function (fn) { if (typeof fn === 'function' && listeners.indexOf(fn) < 0) listeners.push(fn); };
  stage.offChange = function (fn) { var i = listeners.indexOf(fn); if (i >= 0) listeners.splice(i, 1); };
  /* testler/elle tetikleme: bekleyen debounce'u beklemeden dinleyicileri cagir */
  stage.flush = function () { if (timer) { g.clearTimeout(timer); timer = null; } notify(); };
  stage.init = function () {
    apply();
    notified.w = stage.w; notified.h = stage.h;
    if (inited) return;
    inited = true;
    if (g.addEventListener) {
      g.addEventListener('resize', onWindowChange, false);
      g.addEventListener('orientationchange', onWindowChange, false);
    }
  };

  DZ.stage = stage;
})(window);
