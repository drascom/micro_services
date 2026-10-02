/* app.js - router + baslatma. Ekran yigini: profiles -> home -> detail -> player. */
(function (g) {
  'use strict';
  var DZ = g.DZ = g.DZ || {};

  var screenEl = null;
  var stageEl = null;
  var appEl = null;
  var stack = [];        /* [{name, params}] */
  var current = null;    /* aktif ekran nesnesi */
  var currentEntry = null;
  var focusMemory = {};  /* ekran kimligi -> son satir/kart */
  var exiting = false;

  /* ---------- sahneyi pencereye sigdir (js/stage.js: >= 1920x1080, pencereyi doldurur; 16:9'da olcek 1) ---------- */
  function fit() {
    if (DZ.stage && DZ.stage.apply) DZ.stage.apply();
  }

  /* sahne boyutu degisti (pencere/yakinlastirma/donme; debounce'lu): aktif ekran boyuta bagli yerlesimini
     (pencereli listeler, gorunen satirlar) yeniden hesaplar, sonra odak konumu yeni yukseklige gore yeniden kaydirilir. */
  function relayout(stage) {
    if (current && current.onResize) {
      try { current.onResize(stage); } catch (e) { console.log('[app] onResize hata: ' + e); }
    }
    if (DZ.nav && DZ.nav.relayout) {
      try { DZ.nav.relayout(); } catch (e2) { console.log('[app] relayout hata: ' + e2); }
    }
  }

  function screenByName(name) {
    return (DZ.screens && DZ.screens[name]) ? DZ.screens[name] : null;
  }

  function entryKey(entry) {
    var p = (entry && entry.params) || {};
    return [entry ? entry.name : '', p.view || '', p.id || p.itemId || '',
      p.episodeId || '', p.profile || '', p.q || ''].join('|');
  }

  function rememberFocus() {
    if (!currentEntry || !DZ.nav || !DZ.nav.snapshot) return;
    var saved = DZ.nav.snapshot();
    if (!saved) return;
    currentEntry.focus = saved;
    focusMemory[entryKey(currentEntry)] = saved;
  }

  function mount(entry, isResume) {
    var s = screenByName(entry.name);
    if (!s) { console.log('[app] bilinmeyen ekran: ' + entry.name); return; }
    if (current && current.exit) { try { current.exit(); } catch (e) { console.log('[app] exit hata: ' + e); } }
    DZ.nav.reset();
    DZ.nav.restoreNext(entry.focus || focusMemory[entryKey(entry)] || null);
    current = s;
    currentEntry = entry;
    /* 3. arguman: geri donus mu (ekran son konumunu/secimini hatirlayabilir) */
    try { s.enter(screenEl, entry.params, !!isResume); } catch (e3) { console.log('[app] enter hata: ' + e3); }
    /* bildirim yoklayicisi (js/notifications.js): ana ekran/detay/katalogda 30 sn'de bir; oynaticida durur, donuste bir kez sorgular */
    if (DZ.notifications && DZ.notifications.onScreen) {
      try { DZ.notifications.onScreen(entry.name); } catch (e4) { console.log('[app] notifications hata: ' + e4); }
    }
    console.log('[app] ekran: ' + entry.name);
  }

  function go(name, params, replace) {
    rememberFocus();
    var entry = { name: name, params: params || null };
    if (replace && stack.length) stack[stack.length - 1] = entry;
    else stack.push(entry);
    mount(entry, false);
  }

  /* aktif yigin girdisinin parametrelerini yerinde gunceller (ekran yeniden kurulmadan degisen sorgu:
     yazarken arama; detaydan donunce son sorgu gorunsun). undefined deger anahtari siler. */
  function updateParams(patch) {
    if (!currentEntry || !patch) return;
    var p = currentEntry.params || (currentEntry.params = {});
    for (var k in patch) {
      if (!Object.prototype.hasOwnProperty.call(patch, k)) continue;
      if (patch[k] === undefined) delete p[k]; else p[k] = patch[k];
    }
  }

  function back() {
    if (stack.length <= 1) {
      confirmExit();
      return;
    }
    rememberFocus();
    stack.pop();
    mount(stack[stack.length - 1], true);
  }

  function exitApp() {
    try {
      if (typeof tizen !== 'undefined' && tizen.application) {
        tizen.application.getCurrentApplication().exit();
        return;
      }
    } catch (e) {
      console.log('[app] exit hata: ' + e);
    }
    console.log('[app] cikis (tarayici modunda kapatilmaz)');
  }

  function confirmExit() {
    if (exiting) return;
    exiting = true;
    DZ.modal.confirm('Uygulamadan cikilsin mi?', 'DIZIFLIX kapatilacak.',
      function () { exiting = false; exitApp(); },
      function () { exiting = false; });
  }

  /* Geri tusu ile ayni is: ekranin kendi back()'i varsa o, yoksa yigin geri. Fare/dokunma "← Geri" dugmeleri de bunu kullanir. */
  function goBack() {
    if (DZ.modal && DZ.modal.isOpen && DZ.modal.isOpen()) return;
    if (current && current.back) current.back();
    else back();
  }
  /* gidilecek bir onceki ekran var mi (yoksa Geri = cikis onayi; dugme gosterilmez) */
  function canGoBack() { return stack.length > 1; }

  /* ---------- tus yonlendirme ---------- */
  function handleKey(ev) {
    if (DZ.modal.isOpen()) return false;   /* modal kendi dinleyicisi ile once yakalar */

    /* Samsung IME Done/Search ve Cancel olaylarini 13/10009 yerine kendi
       kodlariyla yollar. Input dinleyicisi kacirirsa aramayi burada tamamla;
       Cancel'da yalniz klavyeyi kapat (odak ogesi yerinde kalir; Enter tekrar yazdirir, Geri ustten devam eder). */
    if (ev.typing && ev.name === 'enter' && ev.native && ev.native.target
            && ev.native.target.dzSubmitSearch) {
      ev.native.target.dzSubmitSearch();
      return true;
    }
    if (ev.typing && ev.name === 'back') {
      try { if (ev.native && ev.native.target && ev.native.target.blur) ev.native.target.blur(); } catch (e) {}
      return true;
    }
    /* keys.js sag/sol'u yalniz imlec metnin ucundayken iletir: komsu oge (✕ / Listem), input'u birakarak */
    if (ev.typing && (ev.name === 'left' || ev.name === 'right')) {
      if (DZ.nav.move(ev.name)) {
        try { if (ev.native && ev.native.target && ev.native.target.blur) ev.native.target.blur(); } catch (e3) {}
      }
      return true;
    }
    if (ev.typing && (ev.name === 'up' || ev.name === 'down')) {
      try { if (ev.native && ev.native.target && ev.native.target.blur) ev.native.target.blur(); } catch (e2) {}
      DZ.nav.move(ev.name);
      return true;
    }

    if (current && current.key) {
      var handled = current.key(ev);
      if (handled === true) return true;
    }

    switch (ev.name) {
      case 'left': case 'right': case 'up': case 'down':
        DZ.nav.move(ev.name);
        return true;
      case 'enter':
        DZ.nav.enter();
        return true;
      case 'back':
        goBack();
        return true;
      case 'exit':
        confirmExit();
        return true;
      default:
        return false;
    }
  }

  function start() {
    screenEl = document.getElementById('screen');
    stageEl = document.getElementById('stage');
    appEl = document.getElementById('app');
    if (DZ.stage && DZ.stage.init) {
      DZ.stage.init();                 /* ilk yerlesim + resize/orientationchange dinleyicisi */
      DZ.stage.onChange(relayout);
    }

    /* build/versiyon damgasi: her ekranda sabit koste gorunur (nav disi) */
    var badge = document.createElement('div');
    badge.id = 'buildbadge';
    badge.textContent = 'build ' + (g.DZ_BUILD || 'dev');
    if (appEl) appEl.appendChild(badge);

    DZ.keys.init();
    DZ.keys.onKey(handleKey);
    if (DZ.toast && DZ.toast.watchOnline) DZ.toast.watchOnline();   /* cevrimdisi uyarisi (sol alt) */

    console.log('[app] base url: ' + DZ.api.baseUrl() +
      (DZ.api.debug.delay ? ('  debug delay=' + DZ.api.debug.delay) : '') +
      (DZ.api.debug.fail ? '  debug fail=1' : ''));

    var pid = DZ.api.profileId();
    if (pid) {
      stack = [{ name: 'home', params: { profile: pid } }];
      mount(stack[0], false);
    } else {
      stack = [{ name: 'profiles', params: null }];
      mount(stack[0], false);
    }
  }

  DZ.app = {
    go: go,
    updateParams: updateParams,
    back: back,
    goBack: goBack,
    canGoBack: canGoBack,
    start: start,
    fit: fit,
    relayout: relayout,
    exitApp: exitApp,
    confirmExit: confirmExit,
    stack: function () { return stack.slice(); },
    currentName: function () { return stack.length ? stack[stack.length - 1].name : null; }
  };

  if (document.readyState === 'complete' || document.readyState === 'interactive') {
    setTimeout(start, 0);
  } else {
    document.addEventListener('DOMContentLoaded', start, false);
  }
})(window);
