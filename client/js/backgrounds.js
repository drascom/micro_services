/* backgrounds.js - kullanicinin yukledigi arka plan gorselleri (ES5).
   Kaynak sunucu: `GET /api/backgrounds` -> {"items":[{id,url,blur_url,width,height,added_at}]} (API.md "Arka planlar").
   DZP.BG_LIST = [{id, src, blur}] (mutlak URL; blur = 320x180 bulanik kopya, cam paneller icin). Okunamazsa liste korunur. */
(function (g) {
  'use strict';
  var DZP = g.DZP = g.DZP || {};

  DZP.BG_LIST = [];

  /* cb(err) */
  DZP.loadBackgrounds = function (cb) {
    var api = DZP.api;
    if (!api || !api.hasBase()) { if (cb) cb(null); return; }
    api.backgrounds(function (err, data) {
      if (!err) {
        var its = (data && data.items) || [], out = [], i;
        for (i = 0; i < its.length; i++) {
          if (!its[i] || !its[i].url) continue;
          out.push({ id: String(its[i].id || i), src: api.img(its[i].url), blur: api.img(its[i].blur_url || '') });
        }
        DZP.BG_LIST = out;
      }
      if (cb) cb(err);
    });
  };

  /* karisik sira (Fisher-Yates); `avoid` indeksi basa gelmez (dongu sinirinda ayni gorsel arka arkaya olmasin) */
  DZP.shuffleOrder = function (n, avoid) {
    var a = [], i, j, t;
    for (i = 0; i < n; i++) a.push(i);
    for (i = n - 1; i > 0; i--) { j = Math.floor(Math.random() * (i + 1)); t = a[i]; a[i] = a[j]; a[j] = t; }
    if (n > 1 && a[0] === avoid) { t = a[0]; a[0] = a[n - 1]; a[n - 1] = t; }
    return a;
  };
})(window);
