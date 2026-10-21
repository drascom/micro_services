/* keys.js - uzaktan kumanda tus sabitleri + normalize edilmis dinleyici. ES2017. */
(function (g) {
  'use strict';
  var DZ = g.DZ = g.DZ || {};

  var CODE = {
    LEFT: 37, UP: 38, RIGHT: 39, DOWN: 40,
    ENTER: 13, BACK: 10009, EXIT: 10182,
    IME_DONE: 65376, IME_CANCEL: 65385,
    PLAY_PAUSE: 10252, PLAY: 415, PAUSE: 19, STOP: 413,
    FF: 417, REW: 412,
    RED: 403, GREEN: 404, YELLOW: 405, BLUE: 406,
    INFO: 457, ESC: 27, SPACE: 32, BACKSPACE: 8,
    F1: 112   /* masaustunde Kirmizi taklidi */
  };

  /* Tizen medya tuslarini uygulamaya yonlendir */
  var MEDIA_KEYS = [
    'MediaPlayPause', 'MediaPlay', 'MediaPause', 'MediaStop',
    'MediaFastForward', 'MediaRewind',
    'ColorF2Yellow'   /* oynaticida Ses ve Altyazilar paneli */
  ];

  function registerMediaKeys() {
    try {
      if (typeof tizen !== 'undefined' && tizen.tvinputdevice) {
        for (var i = 0; i < MEDIA_KEYS.length; i++) {
          try { tizen.tvinputdevice.registerKey(MEDIA_KEYS[i]); }
          catch (e1) { console.log('[keys] registerKey basarisiz: ' + MEDIA_KEYS[i]); }
        }
        console.log('[keys] medya tuslari kaydedildi');
      } else {
        console.log('[keys] tizen.tvinputdevice yok (masaustu modu)');
      }
    } catch (e) {
      console.log('[keys] registerMediaKeys hata: ' + e);
    }
  }

  /* keyCode -> mantiksal isim */
  function nameOf(code) {
    switch (code) {
      case CODE.LEFT: return 'left';
      case CODE.UP: return 'up';
      case CODE.RIGHT: return 'right';
      case CODE.DOWN: return 'down';
      case CODE.ENTER: case CODE.SPACE: case CODE.IME_DONE: return 'enter';
      case CODE.BACK: case CODE.ESC: case CODE.IME_CANCEL: return 'back';
      case CODE.EXIT: return 'exit';
      case CODE.PLAY_PAUSE: return 'playpause';
      case CODE.PLAY: return 'play';
      case CODE.PAUSE: return 'pause';
      case CODE.STOP: return 'stop';
      case CODE.FF: return 'ff';
      case CODE.REW: return 'rew';
      case CODE.RED: case CODE.F1: return 'red'; /* masaustu: F1 (harf tusu DEGIL; Cmd/Ctrl+R yenileme calissin) */
      case CODE.GREEN: case 71: return 'green';  /* masaustu: G */
      case CODE.YELLOW: case 89: return 'yellow';/* masaustu: Y */
      case CODE.BLUE: case 66: return 'blue';    /* masaustu: B */
      case CODE.INFO: return 'info';
      default: return null;
    }
  }

  var handlers = [];
  var upHandlers = [];

  function caretAtEdge(t, name) {
    try {
      var a = t.selectionStart, b = t.selectionEnd;
      if (typeof a !== 'number' || typeof b !== 'number') return false;
      var n = String(t.value == null ? '' : t.value).length;
      return name === 'left' ? (a === 0 && b === 0) : (a === n && b === n);
    } catch (e) { return false; }
  }

  function dispatch(ev) {
    /* Tarayici kisayollari (Cmd/Ctrl/Alt + tus: yenile, adres cubugu, bul, kapat,
       Alt+Sol geri...) uygulamaya hic girmez: yakalama, preventDefault yok. */
    if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
    var code = ev.keyCode || ev.which;
    var name = nameOf(code);
    /* Metin girisi odakliyken sag/sol metin imlecine, diger normal tuslar
       IME'ye kalir. Yukari/asagi alanlar arasi TV navigasyonuna doner. */
    var t = ev.target;
    var typing = !!(t && t.tagName && t.tagName.toLowerCase() === 'input');
    if (typing && name !== 'back' && name !== 'up' && name !== 'down' && name !== 'enter') {
      /* sag/sol: imlec metnin ucundaysa ust menude komsu ogeye (logo / ✕ / Listem) gec; ortadaysa imlec hareketi icin alana birak */
      if (!((name === 'left' || name === 'right') && caretAtEdge(t, name))) return;
    }
    if (!name) return;
    var e = { name: name, code: code, repeat: !!ev.repeat, native: ev, typing: typing };
    for (var i = handlers.length - 1; i >= 0; i--) {
      var res = handlers[i](e);
      if (res === true) { /* islendi */ break; }
    }
    if (name !== 'back' || !typing) {
      if (ev.preventDefault) ev.preventDefault();
    }
  }

  /* onKey(fn) -> unsubscribe. fn true dondurursek olay tuketilmis sayilir. */
  function onKey(fn) {
    handlers.push(fn);
    return function () {
      var i = handlers.indexOf(fn);
      if (i >= 0) handlers.splice(i, 1);
    };
  }

  /* keyup: yalniz tus BIRAKILDIGINI bilmesi gerekenler icin (ornek: Ana sayfa "Izlemeye Devam Et" kartinda Tamam/OK
     kisa-uzun basis ayrimi). Ayni normalize olay; fn true dondurursek sonraki dinleyicilere gitmez. preventDefault YOK. */
  function dispatchUp(ev) {
    if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
    var code = ev.keyCode || ev.which;
    var name = nameOf(code);
    if (!name) return;
    var t = ev.target;
    var typing = !!(t && t.tagName && t.tagName.toLowerCase() === 'input');
    var e = { name: name, code: code, native: ev, typing: typing };
    for (var i = upHandlers.length - 1; i >= 0; i--) {
      if (upHandlers[i](e) === true) break;
    }
  }

  function onKeyUp(fn) {
    upHandlers.push(fn);
    return function () {
      var i = upHandlers.indexOf(fn);
      if (i >= 0) upHandlers.splice(i, 1);
    };
  }

  function init() {
    registerMediaKeys();
    g.document.addEventListener('keydown', dispatch, false);
    g.document.addEventListener('keyup', dispatchUp, false);
  }

  DZ.keys = {
    CODE: CODE,
    nameOf: nameOf,
    onKey: onKey,
    onKeyUp: onKeyUp,
    init: init,
    registerMediaKeys: registerMediaKeys
  };
})(window);
