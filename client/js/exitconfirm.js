/* exitconfirm.js - kok ekranlarda (ana ekran menu basi, soguk acilis profil secimi, ilk acilis sunucu adresi) Geri:
   cam "Uygulamadan çıkılsın mı?" onayi. Hayir (varsayilan odak) / Evet; ←→ gez, OK sec, Geri = Hayir.
   Fare: hover = odak, tik = OK, pencere disina tik = Geri.
   Evet: Android kabugu `DZAndroid.exit()`, Tizen `tizen.application.getCurrentApplication().exit()`, tarayici: yalniz kapanir.
   DZP.exitConfirm = { open(host, opts?), key(k) -> true, close(), active(), quit() } ; opts: {mat(cls, x, y) -> cam katmani, onClose(),
   title?, labels? [hayir, evet], onYes? (verilirse Evet cikis yerine bunu cagirir; ör. "Güncelleme hazır" Sonra / Şimdi kur)} */
(function (g) {
  'use strict';
  var DZP = g.DZP = g.DZP || {}, doc = g.document;
  var X = 640, Y = 360, W = 640, H = 300;
  var C = null;

  function el(tag, cls, txt) {
    var e = doc.createElement(tag);
    if (cls) e.className = cls;
    if (txt !== undefined) e.textContent = txt;
    return e;
  }
  function quit() {
    try { if (g.DZAndroid && g.DZAndroid.exit) { g.DZAndroid.exit(); return 'android'; } } catch (e) {}
    try {
      if (g.tizen && g.tizen.application && g.tizen.application.getCurrentApplication) {
        g.tizen.application.getCurrentApplication().exit();
        return 'tizen';
      }
    } catch (e2) {}
    return '';   /* tarayici: cikis yok */
  }
  function paint() {
    for (var i = 0; i < C.btns.length; i++) C.btns[i].className = 'xq-b' + (i === C.btn ? ' on' : '');
  }
  function open(host, opts) {
    if (C) return;
    opts = opts || {};
    var box = el('div', 'xq rclip');
    box.style.left = X + 'px'; box.style.top = Y + 'px'; box.style.width = W + 'px'; box.style.height = H + 'px';
    if (opts.mat) { try { box.appendChild(opts.mat('xq-mat', X, Y)); } catch (e) {} }
    box.appendChild(el('div', 'xq-t', opts.title || 'Uygulamadan çıkılsın mı?'));
    var row = el('div', 'xq-row'), btns = [], labels = opts.labels || ['Hayır', 'Evet'];
    for (var i = 0; i < labels.length; i++) {
      var b = el('div', 'xq-b', labels[i]);
      b.onclick = (function (k) { return function () { if (C) { C.btn = k; key('ok'); } }; })(i);
      /* fare: hover = odak (app.js gercek hareketle cagirir), tik = OK */
      b.dzHover = (function (k) { return function () { if (C && C.btn !== k) { C.btn = k; paint(); } }; })(i);
      row.appendChild(b); btns.push(b);
    }
    box.appendChild(row);
    var dim = el('div', 'xq-dim');
    dim.onclick = function () { key('back'); };   /* pencere disina tik = Geri (Hayir / Sonra) */
    host.appendChild(dim);
    host.appendChild(box);
    C = { box: box, dim: dim, btns: btns, btn: 0, onClose: opts.onClose, onYes: opts.onYes };
    paint();
    void box.offsetWidth;
    box.className = 'xq rclip on'; dim.className = 'xq-dim on';
  }
  function close() {
    if (!C) return;
    var c = C;
    C = null;
    if (c.box.parentNode) c.box.parentNode.removeChild(c.box);
    if (c.dim.parentNode) c.dim.parentNode.removeChild(c.dim);
    if (c.onClose) c.onClose();
  }
  function key(k) {
    if (!C) return false;
    if (k === 'left' && C.btn > 0) { C.btn--; paint(); }
    else if (k === 'right' && C.btn < C.btns.length - 1) { C.btn++; paint(); }
    else if (k === 'back') close();
    else if (k === 'ok') { var yes = C.btn === 1, onYes = C.onYes; close(); if (yes) { if (onYes) onYes(); else quit(); } }
    return true;
  }
  DZP.exitConfirm = { open: open, key: key, close: close, active: function () { return !!C; }, quit: quit };
})(window);
