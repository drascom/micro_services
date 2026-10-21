/* ui/row.js - yatay satir. Satir basina DOM'da max 20 kart, gorsel penceresi gorunen +-4. */
(function (g) {
  'use strict';
  var DZ = g.DZ = g.DZ || {};

  var MAX_DOM_CARDS = 20;
  var VISIBLE = 8;      /* 1920px'de kabaca gorunen poster sayisi (genis sahnede visible() orantili artar) */
  var PAD = 4;          /* gorunen +- 4 kart */

  /* sahne genisligine gore gorunen kart sayisi: 1920'de tam VISIBLE (8), genis sahnede orantili (2560 -> 11) */
  function visible() {
    var w = (DZ.stage && DZ.stage.w) || 1920;
    return w <= 1920 ? VISIBLE : Math.ceil(VISIBLE * w / 1920);
  }

  function create(cfg) {
    var o = cfg || {};
    var sec = document.createElement('section');
    sec.className = 'row';
    sec.setAttribute('data-row-id', o.id);

    var title = document.createElement('div');
    title.className = 'row-title';
    title.textContent = o.title || '';
    sec.appendChild(title);

    var vp = document.createElement('div');
    vp.className = 'row-viewport';
    vp.setAttribute('data-nav-viewport', '1');
    var strip = document.createElement('div');
    strip.className = 'row-strip';
    strip.setAttribute('data-nav-strip', '1');
    vp.appendChild(strip);
    sec.appendChild(vp);

    sec.dzRowId = o.id;
    sec.dzTitle = o.title || '';
    sec.dzLoaded = false;
    sec.dzLoading = false;
    sec.dzCards = [];
    sec.dzStrip = strip;

    function clearStrip() {
      while (strip.firstChild) strip.removeChild(strip.firstChild);
      sec.dzCards = [];
    }

    function appendEndAction(col) {
      if (!o.endAction || !o.endAction.onSelect) return;
      var card = document.createElement('div');
      card.className = 'card card-poster all-items-card';
      card.setAttribute('data-nav', '1');
      card.setAttribute('tabindex', '0');
      card.setAttribute('role', 'button');
      card.setAttribute('aria-label', o.endAction.label || 'Tümünü Gör');
      card.setAttribute('data-col', String(col));
      var arrow = document.createElement('div');
      arrow.className = 'all-items-arrow';
      arrow.textContent = '→';
      card.appendChild(arrow);
      var label = document.createElement('div');
      label.className = 'all-items-label';
      label.textContent = o.endAction.label || 'Tümünü Gör';
      card.appendChild(label);
      card.addEventListener('click', o.endAction.onSelect, false);
      card.dzLoadImage = card.dzLoadFocusImage = card.dzUnloadImage = function () {};
      card.dzItem = null;

      var tile = document.createElement('div');
      tile.className = 'row-tile poster all-items-tile';
      tile.setAttribute('data-base-width', '240');
      tile.appendChild(card);
      strip.appendChild(tile);
      sec.dzCards.push(card);
    }

    /* iskelet kartlar + satir navigasyona girmez (data-nav yok) */
    sec.dzShowSkeleton = function (count) {
      clearStrip();
      sec.removeAttribute('data-nav-row');
      sec.classList.remove('hidden');
      sec.dzError = false;
      var c = count || 6;
      var vis = visible();
      if (c > vis) c = vis;
      if (c < 1) c = 1;
      for (var i = 0; i < c; i++) strip.appendChild(DZ.card.skeleton({ portrait: !!o.portrait }));
      sec.dzLoaded = false;
    };

    sec.dzSetItems = function (items) {
      clearStrip();
      var list = (items || []).slice(0, MAX_DOM_CARDS);
      if (!list.length) {
        sec.dzShowEmpty();
        return;
      }
      sec.classList.remove('hidden');
      sec.dzError = false;
      sec.setAttribute('data-nav-row', o.id);
      for (var i = 0; i < list.length; i++) {
        var card = DZ.card.create(list[i], {
          onSelect: o.onSelect,
          portrait: !!o.portrait,
          focusLandscape: !!o.focusLandscape,
          onLongPress: o.onLongPress,
          onRemove: o.onRemove
        });
        card.setAttribute('data-col', String(i));
        /* bolum karti (card_kind=episode) dahil tum kartlar ayni kutu: poster rafinda 240 genis poster tile */
        var tile = document.createElement('div');
        tile.className = 'row-tile' + (o.portrait ? ' poster' : '');
        tile.setAttribute('data-base-width', String(o.portrait ? 240 : DZ.card.W));
        tile.appendChild(card);
        var caption = document.createElement('div'); caption.className = 'row-card-title';
        caption.textContent = list[i].title || ''; tile.appendChild(caption);
        var rating = list[i].rating;
        if (list[i].episode_label) {
          /* bolum karti: "S01 B03 · Firtina" (ayni dizinin kartlarini ayirt eder) */
          var epLabel = document.createElement('div'); epLabel.className = 'row-card-score';
          epLabel.textContent = String(list[i].episode_label); tile.appendChild(epLabel);
        } else if (typeof rating === 'number' && rating > 0) {
          var score = document.createElement('div'); score.className = 'row-card-score';
          score.textContent = '★ Puan ' + rating.toFixed(1); tile.appendChild(score);
        }
        strip.appendChild(tile);
        sec.dzCards.push(card);
      }
      appendEndAction(list.length);
      sec.dzLoaded = true;
      sec.dzLoading = false;
      strip.style.transform = 'translate3d(0,0,0)';
      sec.dzUpdateWindow(0);
    };

    /* Bos satir: gizlenir (bos "Icerik yok" karti gostermek yerine); sonradan dolarsa dzSetItems geri acar. */
    sec.dzShowEmpty = function () {
      clearStrip();
      sec.removeAttribute('data-nav-row');
      sec.classList.add('hidden');
      sec.dzLoaded = true;
      sec.dzLoading = false;
      sec.dzError = false;
    };

    /* Yukleme hatasi: satirda odaklanabilir "Tekrar dene" karti. dzLoaded true kalir (odak her gezindiginde
       kendiliginden yeniden istek atilmaz); yeniden deneme kullanicinin Enter'ina baglidir. */
    sec.dzShowError = function (message, onRetry) {
      clearStrip();
      sec.classList.remove('hidden');
      sec.setAttribute('data-nav-row', o.id);
      var n = document.createElement('div');
      n.className = 'card card-retry';
      n.setAttribute('data-nav', '1');
      n.setAttribute('data-focus-key', 'retry:' + o.id);
      n.setAttribute('role', 'button');
      var t = document.createElement('div');
      t.className = 'retry-title';
      t.textContent = 'Yüklenemedi';
      n.appendChild(t);
      var m = document.createElement('div');
      m.className = 'retry-msg';
      m.textContent = message ? String(message) : '';
      n.appendChild(m);
      var b = document.createElement('div');
      b.className = 'retry-btn';
      b.textContent = 'Tekrar dene';
      n.appendChild(b);
      sec.dzRetryBtn = b;
      n.dzLoadImage = n.dzLoadFocusImage = n.dzUnloadImage = function () {};
      n.dzItem = null;
      if (onRetry) n.addEventListener('click', onRetry, false);
      var tile = document.createElement('div');
      tile.className = 'row-tile';
      tile.setAttribute('data-base-width', String(DZ.card.W));
      tile.appendChild(n);
      strip.appendChild(tile);
      strip.style.transform = 'translate3d(0,0,0)';
      sec.dzLoaded = true;
      sec.dzLoading = false;
      sec.dzError = true;
    };

    /* yeniden deneme basladi: karti (ve odagi) yerinde tut, yalniz durum metni degisir */
    sec.dzSetRetrying = function () {
      if (sec.dzRetryBtn) sec.dzRetryBtn.textContent = 'Yükleniyor…';
      sec.dzLoaded = false;
      sec.dzError = false;
    };

    /* gorsel penceresi: [focus-PAD, focus+VISIBLE+PAD] */
    sec.dzUpdateWindow = function (focusCol) {
      var n = sec.dzCards.length;
      if (!n) return;
      var f = focusCol || 0;
      var start = f - PAD; if (start < 0) start = 0;
      var end = f + visible() + PAD; if (end > n - 1) end = n - 1;
      for (var i = 0; i < n; i++) {
        var c = sec.dzCards[i];
        if (i >= start && i <= end) c.dzLoadImage();
        else if (i < start - PAD || i > end + PAD) c.dzUnloadImage();
      }
    };

    sec.dzItemAt = function (col) {
      var c = sec.dzCards[col];
      return c ? c.dzItem : null;
    };

    if (o.loaded && o.items && o.items.length) sec.dzSetItems(o.items);
    else if (o.loaded) sec.dzShowEmpty();
    else sec.dzShowSkeleton(o.count);

    return sec;
  }

  DZ.row = { create: create, MAX_DOM_CARDS: MAX_DOM_CARDS, VISIBLE: VISIBLE, visible: visible };
})(window);
