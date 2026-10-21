/* screens/profile_edit.js - profil olustur/duzenle + avatar secimi. ES2017. */
(function (g) {
  'use strict';
  var DZ = g.DZ = g.DZ || {};

  var container = null;
  var state = null;

  function mk(tag, cls, txt) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (txt !== undefined && txt !== null) n.textContent = txt;
    return n;
  }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }

  function initials(name) {
    var s = String(name || '?').trim();
    return s ? s.charAt(0).toUpperCase() : '?';
  }

  function setPreview() {
    var prev = state.prevEl;
    if (!prev) return;
    var seed = state.selectedSeed;
    var url = '';
    for (var i = 0; i < state.avatars.length; i++) {
      if (state.avatars[i].seed === seed) { url = state.avatars[i].url; break; }
    }
    clear(prev);
    var src = url ? DZ.card.sized(url, 200, 200) : '';
    if (src) {
      var img = document.createElement('img');
      img.src = src;
      prev.appendChild(img);
    } else {
      prev.textContent = initials(state.input ? state.input.value : state.name);
    }
  }

  function selectSeed(seed) {
    state.selectedSeed = seed;
    var grid = state.gridEl;
    if (grid) {
      var cells = grid.querySelectorAll('.pe-av');
      for (var i = 0; i < cells.length; i++) {
        if (cells[i].getAttribute('data-seed') === seed) cells[i].classList.add('sel');
        else cells[i].classList.remove('sel');
      }
    }
    setPreview();
  }

  function updateKidsLabel() {
    if (state.kidsBtn) state.kidsBtn.textContent = 'Cocuk profili: ' + (state.isKids ? 'Acik' : 'Kapali');
  }

  function save() {
    var name = state.input ? String(state.input.value || '').trim() : '';
    if (!name) {
      DZ.modal.open({ title: 'Isim gerekli', message: 'Lutfen bir profil adi girin.', buttons: [{ label: 'Tamam', primary: true }] });
      return;
    }
    var seed = state.selectedSeed || null;
    var done = function () { DZ.app.back(); };
    var fail = function (e) {
      DZ.modal.open({ title: 'Hata', message: (e && e.message) ? e.message : 'Kaydedilemedi', buttons: [{ label: 'Tamam', primary: true }] });
    };
    if (state.id) {
      DZ.api.updateProfile(state.id, { name: name, avatar_seed: seed, is_kids: state.isKids }).then(done, fail);
    } else {
      DZ.api.createProfile(name, state.isKids, seed).then(done, fail);
    }
  }

  function build() {
    clear(container);
    var page = mk('div', 'pe');
    /* fare/dokunma icin "← Geri" (Geri tusuyla ayni islem; kumanda odak sirasina girmez) */
    var backBtn = DZ.navigation && DZ.navigation.backButton ? DZ.navigation.backButton() : null;
    if (backBtn) page.appendChild(backBtn);

    /* sol: onizleme */
    var left = mk('div', 'pe-left');
    var prev = mk('div', 'pe-prev');
    state.prevEl = prev;
    left.appendChild(prev);
    var title = mk('div', 'pe-label', state.id ? 'Profili duzenle' : 'Yeni profil');
    left.appendChild(title);
    page.appendChild(left);

    /* sag: form */
    var right = mk('div', 'pe-right');

    var nameLbl = mk('div', 'pe-label', 'Profil adi');
    right.appendChild(nameLbl);
    var nameRow = mk('div', null);
    nameRow.setAttribute('data-nav-row', 'pe-name');
    var input = document.createElement('input');
    input.className = 'field';
    input.type = 'text';
    input.setAttribute('data-nav', '1');
    input.value = state.name || '';
    input.placeholder = 'Ad';
    input.addEventListener('input', function () { if (!state.selectedSeed) setPreview(); }, false);
    nameRow.appendChild(input);
    right.appendChild(nameRow);
    state.input = input;

    var kidsRow = mk('div', 'pe-kids');
    kidsRow.setAttribute('data-nav-row', 'pe-kids');
    var kidsBtn = mk('div', 'btn small');
    kidsBtn.setAttribute('data-nav', '1');
    kidsBtn.addEventListener('click', function () { state.isKids = !state.isKids; updateKidsLabel(); }, false);
    kidsRow.appendChild(kidsBtn);
    right.appendChild(kidsRow);
    state.kidsBtn = kidsBtn;
    updateKidsLabel();

    var avLbl = mk('div', 'pe-label', 'Avatar sec');
    right.appendChild(avLbl);

    var grid = mk('div', 'pe-grid');
    state.gridEl = grid;
    var perRow = 8;
    var rowEl = null;
    for (var i = 0; i < state.avatars.length; i++) {
      if (i % perRow === 0) {
        rowEl = mk('div', 'pe-grid-row');
        rowEl.setAttribute('data-nav-row', 'pe-av-' + (i / perRow));
        grid.appendChild(rowEl);
      }
      (function (av) {
        var cell = mk('div', 'pe-av');
        cell.setAttribute('data-nav', '1');
        cell.setAttribute('data-seed', av.seed);
        var im = document.createElement('img');
        im.src = DZ.card.sized(av.url, 200, 200);
        im.alt = '';
        cell.appendChild(im);
        cell.addEventListener('click', function () { selectSeed(av.seed); }, false);
        rowEl.appendChild(cell);
      })(state.avatars[i]);
    }
    right.appendChild(grid);

    var actions = mk('div', 'pe-actions');
    actions.setAttribute('data-nav-row', 'pe-actions');
    var saveBtn = mk('div', 'btn primary', 'Kaydet');
    saveBtn.setAttribute('data-nav', '1');
    saveBtn.addEventListener('click', save, false);
    actions.appendChild(saveBtn);
    var cancelBtn = mk('div', 'btn', 'Iptal');
    cancelBtn.setAttribute('data-nav', '1');
    cancelBtn.addEventListener('click', function () { DZ.app.back(); }, false);
    actions.appendChild(cancelBtn);
    right.appendChild(actions);

    page.appendChild(right);
    container.appendChild(page);

    selectSeed(state.selectedSeed || (state.avatars[0] ? state.avatars[0].seed : null));
    DZ.nav.setRoot(container, null);
    DZ.nav.focusRowById('pe-name', 0);
  }

  function showLoading() {
    clear(container);
    var sk = mk('div', 'pe');
    sk.innerHTML = '<div class="pe-left"><div class="sk" style="width:260px;height:260px;border-radius:12px;"></div></div>' +
      '<div class="pe-right"><div class="sk" style="width:640px;height:64px;margin-bottom:24px;"></div>' +
      '<div class="sk" style="width:1000px;height:280px;"></div></div>';
    container.appendChild(sk);
  }

  function showError(err, retry) {
    clear(container);
    var box = mk('div', 'errscreen');
    box.setAttribute('data-nav-row', 'err');
    box.innerHTML = '<h1>Yuklenemedi</h1><p>' + (err && err.message ? err.message : 'Bilinmeyen hata') + '</p>';
    var r = mk('div', 'btn primary', 'Tekrar dene');
    r.setAttribute('data-nav', '1');
    r.addEventListener('click', retry, false);
    box.appendChild(r);
    var b = mk('div', 'btn', 'Geri');
    b.setAttribute('data-nav', '1');
    b.addEventListener('click', function () { DZ.app.back(); }, false);
    box.appendChild(b);
    container.appendChild(box);
    DZ.nav.setRoot(container, null);
  }

  var screen = {
    name: 'profile_edit',
    enter: function (cnt, params) {
      container = cnt;
      var p = params || {};
      var prof = p.profile || null;
      state = {
        id: p.id || null,
        name: prof ? (prof.name || '') : '',
        isKids: prof ? !!prof.is_kids : false,
        selectedSeed: prof ? (prof.avatar_seed || null) : null,
        avatars: []
      };
      function load() {
        showLoading();
        DZ.api.avatars().then(function (res) {
          state.avatars = (res && res.avatars) ? res.avatars : [];
          build();
        }, function (err) {
          showError(err, load);
        });
      }
      load();
    },
    key: function (ev) {
      if (ev.name === 'enter' && document.activeElement && document.activeElement.tagName === 'INPUT') {
        try { document.activeElement.blur(); } catch (e) {}
        if (!DZ.nav.focusRowById('pe-av-0', 0)) DZ.nav.focusRowById('pe-actions', 0);
        return true;
      }
      return false;
    },
    back: function () { DZ.app.back(); return true; },
    exit: function () { state = null; container = null; }
  };

  DZ.screens = DZ.screens || {};
  DZ.screens.profile_edit = screen;
})(window);
