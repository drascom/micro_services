/* screens/settings.js - sunucu adresi + baglanti testi, profil degistir, varsayilan altyazi dili, en yuksek kalite,
   onbellegi temizle, surum/sunucu bilgisi. Tercihler localStorage'da KALICI:
   - altyazi: dz_pref_sub_<profil> ('off' | dil kodu) - oynaticidaki panelin yazdigi tercihle ayni anahtar
   - kalite:  dz_pref_quality ('1080' varsayilan | '1440' | 'auto') - playflow akis siralamasi
   ES2017, opsiyonel zincir YOK. Sayfa kaydirilmaz (nav'a pageEl verilmez): her sey 1080p'ye sigar.
   Yerlesim (css/settings.css): ust satir [logo · ← Geri ........ Profil degistir], "Sunucu" karti
   [girdi+gomulu "Baglantiyi test et"] [Kaydet], yan yana iki yuvarlak kart (altyazi / kalite; dikey radyo listeleri),
   "Onbellegi temizle", soluk bilgi satirlari.
   Kumanda (nav satirlari): settings-top [Profil degistir] -> url [girdi, Test et, Kaydet] -> prefs [altyazi secenekleri…, kalite
   secenekleri…] -> settings-cache [Onbellegi temizle]. prefs tek nav satiridir (kutular ayri kartlarda); ic Yukari/Asagi/Sol/Sag
   screen.key icinde ozel yurur (kart ici dikey, kartlar arasi ayni/en yakin satira yatay); kart ucundan Yukari/Asagi nav'a birakilir. */
(function (g) {
  'use strict';
  var DZ = g.DZ = g.DZ || {};

  var container = null;
  var state = null;

  var SUB_OPTIONS = [
    { value: 'tr', label: 'Türkçe' },
    { value: 'en', label: 'İngilizce' },
    { value: 'off', label: 'Kapalı' }
  ];
  var QUALITY_OPTIONS = [
    { value: '1080', label: '1080p' },
    { value: '1440', label: '1440p' },
    { value: 'auto', label: 'Otomatik' }
  ];

  /* url satirinin ogeleri: 0 girdi · 1 Baglantiyi test et · 2 Kaydet. Acilis odagi Kaydet: girdiye odak ekran klavyesini acar,
     Kaydet ise onceki davranisla ayni, idempotent (ayni adresi kaydedip test eder) ve ekrandaki en dogal eylem. */
  var URL_SAVE_COL = 2;

  function mk(tag, cls, txt) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (txt !== undefined && txt !== null) n.textContent = txt;
    return n;
  }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }

  function engineName() {
    try { return (typeof webapis !== 'undefined' && webapis && webapis.avplay) ? 'AVPlay' : 'HTML5 video'; }
    catch (e) { return 'HTML5 video'; }
  }

  /* kayitli altyazi tercihi: yoksa varsayilan kural Turkce onceliklidir -> 'tr'; listede olmayan dil (oynaticidan) -> '' (hicbiri isaretli degil) */
  function currentSub() {
    var pref = DZ.tracks && DZ.tracks.loadPrefs ? DZ.tracks.loadPrefs().sub : null;
    if (!pref) return 'tr';
    return pref === 'tr' || pref === 'en' || pref === 'off' ? pref : '';
  }

  /* secenek kutusunun (grup, sira) konumu; state.groups = [[el…] (altyazi), [el…] (kalite)] */
  function optPos(el) {
    if (!state || !el) return null;
    for (var g2 = 0; g2 < state.groups.length; g2++) {
      var i = state.groups[g2].indexOf(el);
      if (i >= 0) return { g: g2, i: i };
    }
    return null;
  }
  /* prefs satirindaki duz sutun indeksi (gruplar yan yana, DOM sirasiyla) */
  function optFlat(g2, i) {
    var n = 0;
    for (var k = 0; k < g2; k++) n += state.groups[k].length;
    return n + i;
  }
  function optFocus(g2, i) {
    return DZ.nav.focusRowById('prefs', optFlat(g2, i));
  }

  /* prefs satirinda ok tuslari: kart ici Yukari/Asagi, kartlar arasi Sol/Sag. true = islendi; false = nav varsayilani
     (kartin ilk/son kutusundan Yukari/Asagi komsu satira gider). */
  function optionKey(pos, name) {
    var len = state.groups[pos.g].length;
    if (name === 'up') { if (pos.i > 0) { optFocus(pos.g, pos.i - 1); return true; } return false; }
    if (name === 'down') { if (pos.i < len - 1) { optFocus(pos.g, pos.i + 1); return true; } return false; }
    var ng = pos.g + (name === 'right' ? 1 : -1);
    if (ng >= 0 && ng < state.groups.length) {
      var ni = Math.min(pos.i, state.groups[ng].length - 1);   /* ayni sira; hedef kart kisaysa en yakin */
      if (ni >= 0) optFocus(ng, ni);
    }
    return true;   /* en soldaki/sagdaki kartta Sol/Sag: yerinde kal (nav satir ici kayma ters kart sirasina sokmasin) */
  }

  function build() {
    clear(container);
    var page = mk('div', 'settings');
    page.id = 'page';
    state.groups = [];

    /* ---- ust satir: ← Geri (fare) ........ Profil degistir (kumanda); logo YOK ---- */
    var top = mk('div', 'set-top');
    /* fare/dokunma icin "← Geri" (Geri tusuyla ayni islem; kumanda odak sirasina girmez) */
    var backBtn = DZ.navigation && DZ.navigation.backButton ? DZ.navigation.backButton() : null;
    if (backBtn) top.appendChild(backBtn);
    top.appendChild(mk('div', 'set-spacer'));
    var topNav = mk('div', 'set-top-nav');
    topNav.setAttribute('data-nav-row', 'settings-top');
    top.appendChild(topNav);
    page.appendChild(top);
    page.appendChild(mk('div', 'set-title', 'Ayarlar'));

    function btn(parent, label, cls, key, fn) {
      var b = mk('div', 'set-btn' + (cls ? ' ' + cls : ''), label);
      b.setAttribute('role', 'button');
      b.setAttribute('data-nav', '1');
      b.setAttribute('data-focus-key', key);
      b.addEventListener('click', fn, false);
      parent.appendChild(b);
      return b;
    }

    btn(topNav, 'Profil değiştir', 'set-btn-top', 'settings-profile', function () {
      DZ.api.setProfileId(null);
      DZ.app.go('profiles', null, true);
    });

    /* ---- sunucu karti: [girdi + gomulu test] [Kaydet] + durum satiri ---- */
    var server = mk('div', 'set-card set-server');
    server.appendChild(mk('div', 'set-card-title', 'Sunucu adresi'));
    var urlRow = mk('div', 'set-server-row');
    urlRow.setAttribute('data-nav-row', 'url');
    var field = mk('div', 'set-field');
    var input = document.createElement('input');
    input.className = 'set-input';
    input.type = 'text';
    input.setAttribute('data-nav', '1');
    input.setAttribute('data-focus-key', 'url-input');
    input.setAttribute('aria-label', 'Sunucu adresi');
    input.value = DZ.api.baseUrl();
    input.placeholder = DZ.api.DEFAULT_BASE;
    /* cerceve vurgusu: kutu sarmalayicisinda (girdi odaklaninca); :focus-within eski Tizen motorlarinda yok */
    input.addEventListener('focus', function () { field.classList.add('is-focus'); }, false);
    input.addEventListener('blur', function () { field.classList.remove('is-focus'); }, false);
    field.appendChild(input);
    state.input = input;

    var status = mk('div', 'set-status', '');
    state.status = status;

    var info = {};   /* alttaki bilgi satirlari (odaga girmez) */

    function setStatus(text) { status.textContent = text || ''; }
    function refreshInfo() {
      info.build.textContent = 'Sürüm: build ' + (g.DZ_BUILD || 'dev') + '   ·   Motor: ' + engineName();
      info.addr.textContent = 'Sunucu adresi: ' + DZ.api.baseUrl() + '   ·   Varsayılan: ' + DZ.api.DEFAULT_BASE;
    }

    /* /api/health -> durum satiri + sunucu bilgisi. Gecici adres denendiyse ve basarisizsa ESKI adrese donulur. */
    function runTest(candidate, keep) {
      var prev = DZ.api.baseUrl();
      var applied = DZ.api.setBaseUrl(candidate);
      input.value = applied;
      setStatus('Test ediliyor…');
      var t0 = Date.now();
      DZ.api.health().then(function (res) {
        if (!state) return;
        var src = res && res.source ? res.source : '?';
        var items = res && res.items !== undefined ? res.items : '?';
        var ms = Date.now() - t0;
        setStatus('Bağlantı tamam · ' + ms + ' ms · kaynak: ' + src + ' · içerik: ' + items);
        info.server.textContent = 'Sunucu: ' + src + ' · ' + items + ' içerik' + (res && res.version ? ' · sürüm ' + res.version : '');
        refreshInfo();
      }, function (err) {
        if (!state) return;
        var msg = DZ.toast ? DZ.toast.errorText(err) : (err && err.message ? err.message : 'bağlantı hatası');
        if (!keep && applied !== prev) {
          DZ.api.setBaseUrl(prev);
          input.value = DZ.api.baseUrl();
          msg += ' (yeni adres kaydedilmedi)';
        }
        setStatus('Başarısız: ' + msg);
        info.server.textContent = 'Sunucu: ulaşılamıyor';
        refreshInfo();
      });
    }

    btn(field, 'Bağlantıyı test et', 'set-btn-inline', 'url-test', function () { runTest(input.value, false); });
    urlRow.appendChild(field);
    btn(urlRow, 'Kaydet', 'primary', 'url-save', function () {
      var v = DZ.api.setBaseUrl(input.value);
      input.value = v;
      runTest(v, true);   /* kaydet, sonra ulasilabildigini goster */
    });
    server.appendChild(urlRow);
    server.appendChild(status);
    page.appendChild(server);

    /* ---- tercih kartlari: yan yana iki dikey radyo listesi (tek nav satiri 'prefs') ---- */
    var cols = mk('div', 'set-cols');
    cols.setAttribute('data-nav-row', 'prefs');

    /* tek secimli kart: secili = dolu nokta + hafif sari zemin/kenar, odak = sari halka (ikisi ayri gorunur) */
    function prefCard(key, title, hint, options, get, set) {
      var card = mk('div', 'set-card set-pref');
      card.appendChild(mk('div', 'set-card-title', title));
      card.appendChild(mk('div', 'set-hint', hint || ''));
      var list = mk('div', 'set-radios');
      list.setAttribute('role', 'radiogroup');
      list.setAttribute('aria-label', title);
      var nodes = [];
      function paint() {
        var cur = get();
        for (var i = 0; i < nodes.length; i++) {
          var on = nodes[i].dzValue === cur;
          nodes[i].setAttribute('aria-checked', on ? 'true' : 'false');
          if (on) nodes[i].classList.add('on'); else nodes[i].classList.remove('on');
        }
      }
      for (var i = 0; i < options.length; i++) {
        (function (opt) {
          var b = mk('div', 'opt');
          b.setAttribute('role', 'radio');
          b.setAttribute('data-nav', '1');
          b.setAttribute('data-focus-key', key + ':' + opt.value);
          var mark = mk('span', 'opt-mark');
          mark.setAttribute('aria-hidden', 'true');
          b.appendChild(mark);
          b.appendChild(mk('span', 'opt-label', opt.label));
          b.dzValue = opt.value;
          b.dzLabel = opt.label;
          b.addEventListener('click', function () { set(opt.value); paint(); }, false);
          list.appendChild(b);
          nodes.push(b);
        })(options[i]);
      }
      paint();
      card.appendChild(list);
      state.groups.push(nodes);
      return card;
    }

    if (DZ.api.profileId() && DZ.tracks && DZ.tracks.setSubPref) {
      var other = DZ.tracks.loadPrefs().sub;
      if (!other || other === 'tr' || other === 'en' || other === 'off') other = '';
      cols.appendChild(prefCard('sub', 'Varsayılan altyazı dili',
        other ? 'şu an: ' + DZ.tracks.langName(other) + ' (oynatıcıdan seçildi)' : '',
        SUB_OPTIONS, currentSub,
        function (v) {
          DZ.tracks.setSubPref(v);
          setStatus('Varsayılan altyazı: ' + (v === 'off' ? 'Kapalı' : DZ.tracks.langName(v)));
        }));
    } else {
      var noProf = mk('div', 'set-card set-pref');
      noProf.appendChild(mk('div', 'set-card-title', 'Varsayılan altyazı dili'));
      noProf.appendChild(mk('div', 'set-note', 'Önce bir profil seçin'));
      cols.appendChild(noProf);
    }

    cols.appendChild(prefCard('quality', 'En yüksek kalite', 'bu çözünürlüğün üstündeki akışlar en son denenir',
      QUALITY_OPTIONS, function () { return DZ.api.qualityPref(); },
      function (v) {
        var applied = DZ.api.setQualityPref(v);
        setStatus('En yüksek kalite: ' + (applied === 'auto' ? 'Otomatik (sınırsız)' : applied + 'p'));
      }));
    page.appendChild(cols);

    /* ---- alt: onbellegi temizle + soluk bilgi satirlari ---- */
    var foot = mk('div', 'set-foot');
    foot.setAttribute('data-nav-row', 'settings-cache');
    btn(foot, 'Önbelleği temizle', null, 'settings-cache', function () {
      var n = DZ.api.clearCaches();
      setStatus('Önbellek temizlendi (' + n + ' kayıt) · tercihler ve profil korundu');
      if (DZ.toast) DZ.toast.show('Önbellek temizlendi');
    });
    page.appendChild(foot);

    var box = mk('div', 'set-info');
    info.build = mk('div', null, '');
    info.addr = mk('div', null, '');
    info.server = mk('div', null, 'Sunucu: bilgi alınıyor…');
    box.appendChild(info.build);
    box.appendChild(info.addr);
    box.appendChild(info.server);
    page.appendChild(box);
    refreshInfo();

    container.appendChild(page);
    DZ.nav.setRoot(container, null);
    DZ.nav.focusRowById('url', URL_SAVE_COL);

    /* sessiz ilk kontrol: sunucu bilgisi satirini doldur (durum satirina dokunmaz) */
    DZ.api.health().then(function (res) {
      if (!state) return;
      var src = res && res.source ? res.source : '?';
      var items = res && res.items !== undefined ? res.items : '?';
      info.server.textContent = 'Sunucu: ' + src + ' · ' + items + ' içerik' + (res && res.version ? ' · sürüm ' + res.version : '');
    }, function () {
      if (!state) return;
      info.server.textContent = 'Sunucu: ulaşılamıyor';
    });
  }

  var screen = {
    name: 'settings',
    enter: function (cnt) {
      container = cnt;
      state = { alive: true, groups: [] };
      build();
    },
    key: function (ev) {
      if (!state) return false;
      /* metin alani odakli iken Enter alani kapatir, odak Kaydet'e gecer */
      if (ev.name === 'enter' && document.activeElement && document.activeElement.tagName === 'INPUT') {
        try { document.activeElement.blur(); } catch (e) {}
        DZ.nav.focusRowById('url', URL_SAVE_COL);
        return true;
      }
      if (ev.name === 'left' || ev.name === 'right' || ev.name === 'up' || ev.name === 'down') {
        var pos = DZ.nav.currentEl ? optPos(DZ.nav.currentEl()) : null;
        if (pos) return optionKey(pos, ev.name);
      }
      return false;
    },
    back: function () { DZ.app.back(); return true; },
    exit: function () { state = null; container = null; }
  };

  screen.helpers = { SUB_OPTIONS: SUB_OPTIONS, QUALITY_OPTIONS: QUALITY_OPTIONS };

  DZ.screens = DZ.screens || {};
  DZ.screens.settings = screen;
})(window);
