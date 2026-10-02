/* ui/card.js - yatay/dikey kart. Gorseller tembel yuklenir, placeholder gradient kalir. */
(function (g) {
  'use strict';
  var DZ = g.DZ = g.DZ || {};

  var CARD_W = 342, CARD_H = 192, FOCUS_W = 640, FOCUS_H = 360;

  /* API'den gelen yol zaten ?w=342&h=192 iceriyor; yoksa biz ekleriz (CSS ile kuculme YOK). */
  function sized(path, w, h) {
    if (!path) return '';
    var url = DZ.api.img(path);
    if (!url) return '';
    if (url.indexOf('w=') >= 0 && url.indexOf('h=') >= 0) return url;
    return url + (url.indexOf('?') >= 0 ? '&' : '?') + 'w=' + w + '&h=' + h;
  }

  /* Boyutu ZORLA ayarla (sezon posteri / bolum gorseli: /img izinli kucuk boyutlar).
     Ayni boyut = ayni URL -> tarayici onbellegi + sunucu ETag/304 ile uyumlu. */
  function sizedTo(path, w, h) {
    if (!path) return '';
    var url = DZ.api.img(path);
    if (!url) return '';
    var q = url.indexOf('?');
    var base = q >= 0 ? url.slice(0, q) : url;
    var kept = [];
    if (q >= 0) {
      var parts = url.slice(q + 1).split('&');
      for (var i = 0; i < parts.length; i++) {
        if (parts[i] && !/^(w|h)=/.test(parts[i])) kept.push(parts[i]);
      }
    }
    kept.push('w=' + w, 'h=' + h);
    return base + '?' + kept.join('&');
  }

  /* Bolum karti (card_kind=episode): diger kartlar gibi davranir. Dinlenirken (portrait raf) dizinin POSTERI
     (poster kutusu, genislik 240); odaklaninca yatay genisler ve yatay gorsel gosterir: gercek still varsa
     (has_still !== false) still_url, yoksa dizinin yatay afisi (has_backdrop). Posteri yoksa yatay gorsele duser.
     Still /img'de yalniz izinli boyutlarda istenir (320x180, 454x254, 640x360). */
  var EP_W = 454, EP_H = 254, POSTER_W = 300, POSTER_H = 450;
  function isEpisodeCard(item) { return !!(item && item.card_kind === 'episode'); }
  function hasStill(item) { return !!(item && item.has_still !== false && item.still_url); }
  /* dinlenme gorseli: poster kutusunda dizi afisi; poster yoksa (ya da poster olmayan rafta) yatay bolum gorseli */
  function episodeArt(item, poster) {
    if (!item) return '';
    if (poster && item.portrait) return sized(item.portrait, POSTER_W, POSTER_H);
    if (hasStill(item)) return sizedTo(item.still_url, EP_W, EP_H);
    if (item.has_backdrop && item.card) return sized(item.card, CARD_W, CARD_H);
    return sized(item.portrait || item.card, CARD_W, CARD_H);
  }
  /* odak (yatay onizleme) gorseli; '' = genisleme yok. Bolumde gercek still oncelikli, yoksa yatay afis/card. */
  function focusArt(item) {
    if (!item) return '';
    if (isEpisodeCard(item) && hasStill(item)) return sizedTo(item.still_url, FOCUS_W, FOCUS_H);
    if (!item.has_backdrop) return '';
    return sized((item.backdrop || item.card || '').split('?')[0], FOCUS_W, FOCUS_H);
  }

  function create(item, opts) {
    var o = opts || {};
    var n = document.createElement('div');
    var ep = isEpisodeCard(item);
    var poster = !!o.portrait;
    var focusSrc = (poster && o.focusLandscape) ? focusArt(item) : '';
    var canPreview = !!focusSrc;
    n.className = 'card' + (poster ? ' card-poster' : '') + (ep ? ' card-episode' : '')
      + (canPreview ? ' card-focus-landscape' : (poster ? ' card-focus-effect' : ''));
    n.setAttribute('data-nav', '1');
    n.setAttribute('data-item-id', item && item.id ? item.id : '');
    /* Ayni dizinin birden cok bolum karti olabilir: odak kimligi id degil benzersiz card_key. */
    if (item && item.card_key) n.setAttribute('data-focus-key', String(item.card_key));

    var fb = document.createElement('div');
    fb.className = 'card-fallback';
    fb.textContent = item && item.title ? item.title : '';
    n.appendChild(fb);

    if (item && item.badge) {
      var b = document.createElement('div');
      b.className = 'card-badge';
      b.textContent = item.badge;
      n.appendChild(b);
    }

    if (item && item.progress && item.progress.pct > 0) {
      var p = document.createElement('div');
      p.className = 'card-progress';
      var i = document.createElement('i');
      var pct = item.progress.pct;
      if (pct > 100) pct = 100;
      i.style.width = pct + '%';
      p.appendChild(i);
      n.appendChild(p);
    }

    var src = ep ? episodeArt(item, poster) : sized(item ? (poster ? item.portrait : item.card) : '',
      poster ? POSTER_W : CARD_W, poster ? POSTER_H : CARD_H);

    if (canPreview) {
      var info = document.createElement('div');
      info.className = 'card-preview-info';
      var previewTitle = document.createElement('div');
      previewTitle.className = 'card-preview-title';
      previewTitle.textContent = item.title || '';
      info.appendChild(previewTitle);
      var metaBits = [];
      if (ep) {
        /* bolum karti: yil/puan yok, "S02 B12 · Ad" etiketi */
        if (item.episode_label) metaBits.push(String(item.episode_label));
      } else {
        if (item.year) metaBits.push(String(item.year));
        if (typeof item.rating === 'number' && item.rating > 0) metaBits.push('\u2605 ' + item.rating.toFixed(1));
      }
      if (metaBits.length) {
        var previewMeta = document.createElement('div');
        previewMeta.className = 'card-preview-meta';
        previewMeta.textContent = metaBits.join('  \u2022  ');
        info.appendChild(previewMeta);
      }
      n.appendChild(info);
    }

    n.dzSrc = src;
    n.dzFocusSrc = focusSrc;
    n.dzItem = item;
    n.dzImgLoaded = false;
    n.dzImgErrors = 0;
    n.dzFocusImgLoaded = false;

    /* tembel yukleme: sadece cagirilinca <img> olusur */
    n.dzLoadImage = function () {
      if (n.dzImgLoaded || !n.dzSrc) return;
      n.dzImgLoaded = true;
      var img = document.createElement('img');
      img.className = 'card-primary';
      img.width = poster ? POSTER_W : CARD_W;
      img.height = poster ? POSTER_H : CARD_H;
      img.alt = '';
      img.onload = function () { img.className = 'card-primary ready'; n.classList.add('has-img'); };
      /* yuklenemeyen gorsel: yer tutucu (baslik metni) kalir; odak her gezindiginde sunucuyu dovmemek icin en cok 2 deneme */
      img.onerror = function () {
        n.dzImgErrors = (n.dzImgErrors || 0) + 1;
        n.dzImgLoaded = n.dzImgErrors >= 2;
        if (img.parentNode) img.parentNode.removeChild(img);
      };
      img.src = n.dzSrc;
      n.insertBefore(img, n.firstChild);
    };
    /* Yatay afis yalniz kart gercekten odaklandiginda indirilir. */
    n.dzLoadFocusImage = function () {
      if (n.dzFocusImgLoaded || !n.dzFocusSrc) return;
      n.dzFocusImgLoaded = true;
      var img = document.createElement('img');
      img.className = 'card-landscape';
      img.width = FOCUS_W; img.height = FOCUS_H; img.alt = '';
      img.onload = function () { img.className = 'card-landscape ready'; };
      img.onerror = function () {
        n.dzFocusImgLoaded = false;
        if (img.parentNode) img.parentNode.removeChild(img);
      };
      img.src = n.dzFocusSrc;
      n.appendChild(img);
    };
    n.dzUnloadImage = function () {
      /* DOM'da kart kalir ama gorsel bosaltilir (bellek) */
      var images = n.querySelectorAll('img');
      for (var j = images.length - 1; j >= 0; j--) {
        if (images[j].parentNode) images[j].parentNode.removeChild(images[j]);
      }
      n.classList.remove('has-img');
      n.dzImgLoaded = false;
      n.dzImgErrors = 0;
      n.dzFocusImgLoaded = false;
    };

    if (o.onLongPress) attachLongPress(n, item, o.onLongPress);
    if (o.onRemove) attachRemoveButton(n, item, o.onRemove);
    if (o.onSelect) {
      n.addEventListener('click', function (ev) {
        /* uzun basistan (menu acildi) sonra gelen tiklama/birakma detayi ACMAZ */
        if (n.dzSuppressClick) {
          n.dzSuppressClick = false;
          if (ev && ev.preventDefault) ev.preventDefault();
          if (ev && ev.stopPropagation) ev.stopPropagation();
          return;
        }
        o.onSelect(item, n);
      }, false);
    }
    return n;
  }

  /* Fare imleci uzerine gelince sag ustte yuvarlak "✕" (Listeden kaldir): YALNIZ fareyle kesfedilebilirlik icin.
     Gorunurlugu CSS belirler (css/home.css: yalniz (hover:hover) and (pointer:fine), odakli kartta gizli). Kumanda odak sirasina
     GIRMEZ (data-nav yok); tiklama kartin tiklamasini (detay) tetiklemez; ayni menuyu acar (yanlislikla silmeye karsi onay). */
  function attachRemoveButton(n, item, cb) {
    var x = document.createElement('div');
    x.className = 'card-x';
    x.textContent = '\u2715';
    x.setAttribute('role', 'button');
    x.setAttribute('title', 'Listeden kaldır');
    x.setAttribute('aria-label', 'Listeden kaldır');
    function stop(ev) { if (ev && ev.stopPropagation) ev.stopPropagation(); }
    /* uzun basis zamanlayicisi kartta baslamasin */
    ['pointerdown', 'touchstart', 'mousedown'].forEach(function (t) { x.addEventListener(t, stop, false); });
    x.addEventListener('click', function (ev) {
      stop(ev);
      if (ev && ev.preventDefault) ev.preventDefault();
      cb(item, n);
    }, false);
    n.appendChild(x);
    n.dzRemoveBtn = x;
  }

  /* Uzun basis (fare/dokunma): ~600 ms basili tutma (kayma > ~10 px iptal) ya da sag tik/contextmenu -> cb(item, node).
     Pointer Events varsa pointer*, yoksa touch* + mouse* (ikisi birden kaydedilmez). Uzun basistan sonra ilk 'click'
     bastirilir (n.dzSuppressClick). Kumanda Tamam/OK uzun basisi burada DEGIL, screens/home.js'te (keydown/keyup). */
  var LONG_PRESS_MS = 600, LONG_PRESS_MOVE_PX = 10;
  function attachLongPress(n, item, cb) {
    var timer = null, resetTimer = null, sx = 0, sy = 0, pressing = false, fired = false;
    n.dzSuppressClick = false;
    n.className += ' card-lp';

    function cancel() { if (timer) { clearTimeout(timer); timer = null; } }
    /* birakma tiklamasi hemen gelir; gelmezse (sag tik, bazi tarayicilar) bayrak takili kalmasin */
    function armReset() {
      if (resetTimer) clearTimeout(resetTimer);
      resetTimer = setTimeout(function () { resetTimer = null; n.dzSuppressClick = false; }, 700);
    }
    function fire() {
      cancel();
      if (fired) return;
      fired = true;
      n.dzSuppressClick = true;
      if (!pressing) armReset();
      cb(item, n);
    }
    function start(x, y) {
      if (pressing) return;   /* ayni dokunus icin pointer + touch cift olayi tek sayilir */
      pressing = true; fired = false;
      n.dzSuppressClick = false;
      if (resetTimer) { clearTimeout(resetTimer); resetTimer = null; }
      sx = x; sy = y;
      cancel();
      timer = setTimeout(fire, LONG_PRESS_MS);
    }
    function move(x, y) {
      if (!timer) return;
      if (Math.abs(x - sx) > LONG_PRESS_MOVE_PX || Math.abs(y - sy) > LONG_PRESS_MOVE_PX) cancel();
    }
    function end() {
      cancel();
      pressing = false;
      if (fired) armReset();
    }
    function pt(ev, key) { return ev && typeof ev[key] === 'number' ? ev[key] : 0; }
    function touchPt(ev, key) {
      var t = ev && ev.touches && ev.touches[0] ? ev.touches[0] : (ev && ev.changedTouches && ev.changedTouches[0]);
      return t && typeof t[key] === 'number' ? t[key] : 0;
    }

    if (g.PointerEvent) {
      n.addEventListener('pointerdown', function (ev) {
        if (ev && ev.pointerType === 'mouse' && ev.button) return;   /* sag/orta tus: contextmenu ele alir */
        start(pt(ev, 'clientX'), pt(ev, 'clientY'));
      }, false);
      n.addEventListener('pointermove', function (ev) { move(pt(ev, 'clientX'), pt(ev, 'clientY')); }, false);
      n.addEventListener('pointerup', end, false);
      n.addEventListener('pointercancel', end, false);
      n.addEventListener('pointerleave', end, false);
    } else {
      n.addEventListener('touchstart', function (ev) { start(touchPt(ev, 'clientX'), touchPt(ev, 'clientY')); }, false);
      n.addEventListener('touchmove', function (ev) { move(touchPt(ev, 'clientX'), touchPt(ev, 'clientY')); }, false);
      n.addEventListener('touchend', end, false);
      n.addEventListener('touchcancel', end, false);
      n.addEventListener('mousedown', function (ev) {
        if (ev && ev.button) return;
        start(pt(ev, 'clientX'), pt(ev, 'clientY'));
      }, false);
      n.addEventListener('mousemove', function (ev) { move(pt(ev, 'clientX'), pt(ev, 'clientY')); }, false);
      n.addEventListener('mouseup', end, false);
      n.addEventListener('mouseleave', end, false);
    }
    /* sag tik / dokunmatik uzun basista tarayici menusu: tarayici menusunu engelle, ayni menuyu ac (cift tetik yok) */
    n.addEventListener('contextmenu', function (ev) {
      if (ev && ev.preventDefault) ev.preventDefault();
      if (pressing && fired) return;   /* zaman dolumu menuyu zaten acti */
      fired = false;                   /* sag tik: onceki basistan kalan durum tekrar acmayi engellemesin */
      fire();
    }, false);
  }

  /* iskelet kart (satir yuklenmeden once) */
  function skeleton(opts) {
    var o = opts || {};
    var n = document.createElement('div');
    n.className = 'card skel' + (o.portrait ? ' card-poster' : '');
    var s = document.createElement('div');
    s.className = 'sk';
    n.appendChild(s);
    return n;
  }

  DZ.card = { create: create, LONG_PRESS_MS: LONG_PRESS_MS, skeleton: skeleton, sized: sized, sizedTo: sizedTo, isEpisodeCard: isEpisodeCard, episodeArt: episodeArt, focusArt: focusArt, W: CARD_W, H: CARD_H };
})(window);
