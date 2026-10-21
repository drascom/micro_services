/* screens/profiles.js - profil secme ekrani (acilis). Secim localStorage'a yazilir. */
(function (g) {
  'use strict';
  var DZ = g.DZ = g.DZ || {};
  var el = null;

  /* marka gorseli (img/*.png, kaynak docs/brand): dekoratif, data-nav DEGIL -> odak/gezinmeye girmez; yuklenemezse gizlenir */
  function brandImg(cls, src, alt) {
    var im = document.createElement('img');
    im.className = cls;
    im.src = src;
    im.alt = alt || '';
    im.onerror = function () { im.className += ' hidden'; };
    return im;
  }

  function initials(name) {
    var s = String(name || '?').trim();
    return s ? s.charAt(0).toUpperCase() : '?';
  }

  function profileNode(p, state) {
    var n = document.createElement('div');
    n.className = 'profile' + (state.manage ? ' manage' : '');
    n.setAttribute('data-nav', '1');
    n.setAttribute('data-profile-id', p.id);
    n.dzProfile = p;

    if (state.manage) {
      var pen = document.createElement('div');
      pen.className = 'edit-badge';
      pen.textContent = '✎';
      n.appendChild(pen);
    }

    var av = document.createElement('div');
    av.className = 'av';
    av.textContent = initials(p.name);
    var src = p.avatar ? DZ.card.sized(p.avatar, 200, 200) : '';
    if (src) {
      var img = document.createElement('img');
      img.onload = function () { av.textContent = ''; av.appendChild(img); img.className = 'ready'; };
      img.onerror = function () {};
      img.src = src;
    }
    n.appendChild(av);

    var nm = document.createElement('div');
    nm.className = 'nm';
    nm.textContent = p.name || '';
    n.appendChild(nm);

    if (p.is_kids) {
      var k = document.createElement('div');
      k.className = 'kids';
      k.textContent = 'COCUK';
      n.appendChild(k);
    }
    n.addEventListener('click', function () {
      if (state.manage) DZ.app.go('profile_edit', { id: p.id, profile: p });
      else state.onSelect(p);
    }, false);
    return n;
  }

  function build(container, profiles, state) {
    while (container.firstChild) container.removeChild(container.firstChild);
    var root = document.createElement('div');
    root.className = 'profiles';

    /* fare/dokunma icin "← Geri" (Geri tusuyla ayni islem; kumanda odak sirasina girmez; ilk acilista gidilecek yer yoksa yok) */
    var backBtn = DZ.navigation && DZ.navigation.backButton ? DZ.navigation.backButton() : null;
    if (backBtn) root.appendChild(backBtn);

    root.appendChild(brandImg('brand-logo', 'img/logo-wide.png', 'DiziFlix'));

    var h = document.createElement('h1');
    h.textContent = state.manage ? 'Duzenlenecek profili secin' : 'Kim izliyor?';
    root.appendChild(h);

    var strip = document.createElement('div');
    strip.className = 'profile-strip';
    strip.setAttribute('data-nav-row', 'profiles');
    for (var i = 0; i < profiles.length; i++) {
      strip.appendChild(profileNode(profiles[i], state));
    }
    if (!profiles.length) {
      var empty = document.createElement('div');
      empty.className = 'nm';
      empty.textContent = 'Henuz profil yok. "Profil Ekle" ile baslayin.';
      strip.appendChild(empty);
    }
    root.appendChild(strip);

    var foot = document.createElement('div');
    foot.className = 'foot';
    foot.setAttribute('data-nav-row', 'profiles-foot');

    var add = document.createElement('div');
    add.className = 'btn';
    add.setAttribute('data-nav', '1');
    add.textContent = 'Profil Ekle';
    add.addEventListener('click', state.onAdd, false);
    foot.appendChild(add);

    var mng = document.createElement('div');
    mng.className = 'btn' + (state.manage ? ' primary' : '');
    mng.setAttribute('data-nav', '1');
    mng.textContent = state.manage ? 'Bitti' : 'Profilleri Duzenle';
    mng.addEventListener('click', function () {
      state.manage = !state.manage;
      build(container, state.list, state);
      DZ.nav.focusRowById('profiles-foot', 1);
    }, false);
    foot.appendChild(mng);

    var set = document.createElement('div');
    set.className = 'btn';
    set.setAttribute('data-nav', '1');
    set.textContent = 'Ayarlar';
    set.addEventListener('click', function () { DZ.app.go('settings'); }, false);
    foot.appendChild(set);

    root.appendChild(foot);

    var hint = document.createElement('div');
    hint.className = 'nm';
    hint.style.marginTop = '28px';
    hint.textContent = state.manage
      ? 'Bir profili secip Enter ile duzenleyin  ·  "Bitti" ile cikin'
      : '"Profilleri Duzenle" ile profilleri yonetin  ·  Sari tus: secili profili sil';
    root.appendChild(hint);

    container.appendChild(root);
    el = root;
    DZ.nav.setRoot(container, null);
  }

  function showError(container, err, retry) {
    while (container.firstChild) container.removeChild(container.firstChild);
    var box = document.createElement('div');
    box.className = 'errscreen';
    box.setAttribute('data-nav-row', 'err');
    box.innerHTML = '<img class="brand-mascot" src="img/mascot.png" alt="">' +
      '<div class="wordmark big" style="margin-bottom:40px;">DIZIFLIX</div>' +
      '<h1>Profiller yüklenemedi</h1><p>' + (DZ.toast ? DZ.toast.errorText(err) : (err && err.message ? err.message : 'Bilinmeyen hata')) + '</p>';
    var r = document.createElement('div');
    r.className = 'btn primary';
    r.setAttribute('data-nav', '1');
    r.textContent = 'Tekrar dene';
    r.addEventListener('click', retry, false);
    box.appendChild(r);
    var s = document.createElement('div');
    s.className = 'btn';
    s.setAttribute('data-nav', '1');
    s.textContent = 'Ayarlar';
    s.addEventListener('click', function () { DZ.app.go('settings'); }, false);
    box.appendChild(s);
    container.appendChild(box);
    DZ.nav.setRoot(container, null);
  }

  var screen = {
    name: 'profiles',
    enter: function (container) {
      var state = {};
      state.onSelect = function (p) {
        DZ.api.setProfileId(p.id);
        DZ.app.go('home', { profile: p.id }, true);
      };
      state.onAdd = function () {
        DZ.app.go('profile_edit', { id: null });
      };
      state.list = [];
      state.manage = false;

      function load() {
        var sk = document.createElement('div');
        sk.className = 'profiles';
        sk.innerHTML = '<img class="brand-logo" src="img/logo-wide.png" alt="DiziFlix">' +
          '<div style="display:flex;">' +
          '<div class="sk" style="width:200px;height:200px;margin:0 24px;"></div>' +
          '<div class="sk" style="width:200px;height:200px;margin:0 24px;"></div>' +
          '<div class="sk" style="width:200px;height:200px;margin:0 24px;"></div></div>';
        while (container.firstChild) container.removeChild(container.firstChild);
        container.appendChild(sk);

        DZ.api.profiles().then(function (res) {
          state.list = (res && res.profiles) ? res.profiles : [];
          build(container, state.list, state);
        }, function (err) {
          showError(container, err, load);
        });
      }
      screen._reload = load;
      screen._state = state;
      load();
    },
    key: function (ev) {
      if (ev.name === 'green') {
        var g2 = DZ.nav.currentEl();
        if (g2 && g2.dzProfile) {
          DZ.app.go('profile_edit', { id: g2.dzProfile.id, profile: g2.dzProfile });
        }
        return true;
      }
      if (ev.name === 'yellow') {
        var cur = DZ.nav.currentEl();
        if (cur && cur.dzProfile) {
          var p = cur.dzProfile;
          DZ.modal.confirm('Profili sil', '"' + p.name + '" silinsin mi?', function () {
            DZ.api.deleteProfile(p.id).then(function () {
              if (DZ.api.profileId() === p.id) DZ.api.setProfileId(null);
              screen._reload();
            }, function (e) {
              DZ.modal.open({ title: 'Hata', message: e.message, buttons: [{ label: 'Tamam', primary: true }] });
            });
          });
        }
        return true;
      }
      return false;
    },
    back: function () {
      /* Stack'te alt ekran varsa oraya don ( or. Ayarlar'dan gelindi); yoksa cikis onayi.
         Ayarlar->Profil Degistir setProfileId(null) yapiyor: geri donuste onceki profil
         hala secili degilse home yerine profiles'ta kalinir; app.back zaten dogru davranir. */
      DZ.app.back();
      return true;
    },
    exit: function () { el = null; }
  };

  DZ.screens = DZ.screens || {};
  DZ.screens.profiles = screen;
})(window);
