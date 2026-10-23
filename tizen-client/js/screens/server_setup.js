/* screens/server_setup.js - ilk acilis "Sunucu adresi" ekrani (kayitli/otomatik adres yokken; js/app.js start).
   IP ya da URL girilir: `192.168.1.10` -> `http://192.168.1.10:8090`, `https://...` aynen (DZ.api.normalizeServer).
   "Baglan" adresi KAYDETMEDEN /api/health ile dener (DZ.api.probe); basariliysa dz.baseUrl'e yazar ve profil ekranina gecer.
   Kumanda: satir 1 girdi (Enter ekran klavyesini acar; klavyede Done = Baglan), satir 2 [Baglan]. Adres sonra Ayarlar'dan degisir. */
(function (g) {
  'use strict';
  var DZ = g.DZ = g.DZ || {};

  var state = null;

  function mk(tag, cls, txt) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (txt !== undefined && txt !== null) n.textContent = txt;
    return n;
  }

  function connect() {
    if (!state || state.busy) return;
    var base = DZ.api.normalizeServer(state.input.value);
    if (!base) { state.status.textContent = 'Lütfen sunucunun IP adresini ya da URL\'sini girin.'; return; }
    state.input.value = base;
    state.busy = true;
    state.status.textContent = 'Bağlanılıyor: ' + base + ' …';
    var mine = state;
    DZ.api.probe(base).then(function () {
      if (state !== mine) return;
      state.busy = false;
      DZ.api.setBaseUrl(base);
      DZ.app.go('profiles', null, true);
    }, function (err) {
      if (state !== mine) return;
      state.busy = false;
      var msg = DZ.toast && DZ.toast.errorText ? DZ.toast.errorText(err) : (err && err.message ? err.message : 'bağlantı hatası');
      state.status.textContent = 'Bağlanılamadı: ' + msg;
    });
  }

  var screen = {
    name: 'server_setup',
    enter: function (container) {
      state = { busy: false };
      while (container.firstChild) container.removeChild(container.firstChild);
      var root = mk('div', 'profiles server-setup');

      var logo = document.createElement('img');
      logo.className = 'brand-logo';
      logo.src = 'img/logo-wide.png';
      logo.alt = 'DiziFlix';
      logo.onerror = function () { logo.className += ' hidden'; };
      root.appendChild(logo);

      root.appendChild(mk('h1', null, 'Sunucu adresi'));
      root.appendChild(mk('div', 'ss-hint', 'diziflix sunucunuzun IP adresini ya da URL\'sini girin (örn. 192.168.1.10 ya da https://diziflix.ornek.com).'));

      var inRow = mk('div', 'ss-row');
      inRow.setAttribute('data-nav-row', 'ss-input');
      var input = document.createElement('input');
      input.className = 'field ss-input';
      input.type = 'text';
      input.setAttribute('data-nav', '1');
      input.setAttribute('aria-label', 'Sunucu adresi');
      input.placeholder = '192.168.1.10';
      input.value = DZ.api.suggestBase ? DZ.api.suggestBase() : '';
      input.dzSubmitSearch = connect;   /* ekran klavyesi Done / Enter (js/app.js handleKey) */
      inRow.appendChild(input);
      root.appendChild(inRow);
      state.input = input;

      var actions = mk('div', 'ss-row');
      actions.setAttribute('data-nav-row', 'ss-actions');
      var btn = mk('div', 'btn primary', 'Bağlan');
      btn.setAttribute('data-nav', '1');
      btn.setAttribute('role', 'button');
      btn.addEventListener('click', connect, false);
      actions.appendChild(btn);
      root.appendChild(actions);

      var status = mk('div', 'ss-status', '');
      root.appendChild(status);
      state.status = status;

      container.appendChild(root);
      DZ.nav.setRoot(container, null);
      /* oneri varsa odak Baglan'da (girdiye 'jump' odagi ekran klavyesini hemen acar); yoksa ilk satir girdi ('init', klavye acilmaz) */
      if (input.value) DZ.nav.focusRowById('ss-actions', 0);
    },
    back: function () { DZ.app.back(); return true; },   /* tek ekran: cikis onayi */
    exit: function () { state = null; }
  };

  DZ.screens = DZ.screens || {};
  DZ.screens.server_setup = screen;
})(window);
