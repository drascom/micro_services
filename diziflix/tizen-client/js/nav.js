/* nav.js - kendi spatial navigation'imiz. Kutuphane yok. ES2017.
   Model: [data-nav-row] konteynerleri = satirlar, icindeki [data-nav] = odaklanabilir ogeler.
   - Sol/Sag satir ici, Yukari/Asagi satirlar arasi
   - Sutun hafizasi: her satir kendi son yatay indeksini hatirlar
   - Yatay kaydirma transform: translate3d(x,0,0) ile (scrollLeft/left YOK)
*/
(function (g) {
  'use strict';
  var DZ = g.DZ = g.DZ || {};

  var root = null;      /* odak agacinin koku */
  var pageEl = null;    /* dikey kaydirilacak konteyner (opsiyonel) */
  var rows = [];        /* {el,id,items,strip,viewport} */
  var rowIdx = 0;
  var colMem = {};      /* rowId -> son sutun indeksi */
  var focusCb = null;
  var enterCb = null;
  var enabled = true;
  var pendingRestore = null;
  var restoreTicket = 0;

  function slice(list) { return Array.prototype.slice.call(list); }

  function visible(el) {
    if (!el) return false;
    if (el.getAttribute('data-nav-off') === '1') return false;
    if (el.disabled) return false;
    if (el.offsetWidth > 0 || el.offsetHeight > 0 || el.offsetParent !== null) return true;
    /* TV'de ilk render'da layout henuz olcemeyebilir; DOM'a bagli ve display:none degilse gorunur say */
    try {
      if (el.getClientRects && el.getClientRects().length > 0) return true;
      var st = (g.getComputedStyle ? g.getComputedStyle(el) : null);
      if (el.ownerDocument && el.ownerDocument.contains(el) && (!st || st.display !== 'none')) return true;
    } catch (e) {}
    return false;
  }

  function collect() {
    rows = [];
    if (!root) return;
    var rowEls = slice(root.querySelectorAll('[data-nav-row]'));
    for (var i = 0; i < rowEls.length; i++) {
      var rEl = rowEls[i];
      if (!visible(rEl)) continue;
      var items = slice(rEl.querySelectorAll('[data-nav]')).filter(visible);
      if (!items.length) continue;
      rows.push({
        el: rEl,
        id: rEl.getAttribute('data-nav-row') || ('row' + i),
        items: items,
        strip: rEl.querySelector('[data-nav-strip]'),
        viewport: rEl.querySelector('[data-nav-viewport]')
      });
    }
  }

  function clampCol(r, c) {
    var row = rows[r];
    if (!row) return 0;
    if (c < 0) c = 0;
    if (c > row.items.length - 1) c = row.items.length - 1;
    return c;
  }

  function itemKey(el) {
    if (!el || !el.getAttribute) return null;
    var attrs = ['data-focus-key', 'data-item-id', 'data-profile-id', 'data-seed'];
    for (var i = 0; i < attrs.length; i++) {
      var value = el.getAttribute(attrs[i]);
      if (value) return attrs[i] + ':' + value;
    }
    return null;
  }

  function clearFocus() {
    if (!root) return;
    var cur = slice(root.querySelectorAll('.focused'));
    for (var i = 0; i < cur.length; i++) cur[i].classList.remove('focused');
    var expanded = slice(root.querySelectorAll('.focus-expanded'));
    for (var j = 0; j < expanded.length; j++) expanded[j].classList.remove('focus-expanded');
    var posterFocus = slice(root.querySelectorAll('.focus-poster'));
    for (var k = 0; k < posterFocus.length; k++) posterFocus[k].classList.remove('focus-poster');
  }

  function scrollStrip(row, col) {
    if (!row || !row.strip) return;
    var item = row.items[col];
    if (!item) return;
    /* Odaklanan poster yatay onizlemeye genisleyebilir. Kaydirma hesabinda
       degisen kart olcusunu degil, sabit kalan tile olcusunu kullan. */
    var holder = item.parentNode;
    var base = holder && holder.classList && holder.classList.contains('row-tile') ? holder : item;
    var storedWidth = holder && holder.getAttribute ? parseInt(holder.getAttribute('data-base-width'), 10) : 0;
    /* Raftaki tum tile'lar ayni taban genislikte (poster rafi 240, yatay raf 342; bolum karti da poster tile):
       adim = taban genislik + bosluk, yani hemen soldaki kart hep tam gorunur. */
    var step = (storedWidth || base.offsetWidth) + 12;
    var firstItem = row.items[0];
    var firstHolder = firstItem && firstItem.parentNode;
    var firstBase = firstHolder && firstHolder.classList
      && firstHolder.classList.contains('row-tile') ? firstHolder : firstItem;
    var safeLeft = firstBase ? (firstBase.offsetLeft || 0) : 0;
    /* Ilk kart haric odakli kart her zaman ikinci gorunen yuvada kalir;
       hemen solundaki kart normal sol boslukta tam gorunur. */
    var x = base.offsetLeft - step - safeLeft;
    if (x < 0) x = 0;
    row.strip.style.transform = 'translate3d(' + (-Math.round(x)) + 'px,0,0)';
  }

  /* el'in pageEl icindeki gercek dikey ofseti (offsetParent zinciri boyunca toplanir) */
  function offsetTopWithin(el, ancestor) {
    var y = 0, n = el;
    while (n && n !== ancestor) { y += n.offsetTop || 0; n = n.offsetParent; }
    return y;
  }

  /* Odak icerige dogru asagi gitmez; yeni satir sabit ust yuvaya gelir.
     Son satirlarda da bu konum korunur, gerekirse ekranin altinda bosluk kalir. */
  function scrollPage(row, r) {
    if (!pageEl || !row) return;
    /* data-nav-noscroll: satir kendi ic kaydirmasini yonetir (detay: sezon/bolum listeleri);
       sayfa konumunu ekran kendisi ayarlar. */
    if (row.el && row.el.getAttribute && row.el.getAttribute('data-nav-noscroll') === '1') return;
    /* Detay ekraninda sabit ust menu yok. Odagi piksele kilitlemek yerine
       gorunen ekran yuksekliginin %28'ine yerlestir; farkli olceklerde ayni
       kompozisyon korunur ve alttaki bosluk daha dengeli kullanilir. */
    var screenHeight = (root && (root.clientHeight || root.offsetHeight)) || (DZ.stage && DZ.stage.h) || 1080;
    var TARGET = (pageEl.classList && pageEl.classList.contains('detail'))
      ? Math.round(screenHeight * 0.28) : 140;
    var top = offsetTopWithin(row.el, pageEl);
    var y = (row.id === "hero" || row.id === "hero_actions") ? 0 : top - TARGET;
    if (y < 0) y = 0;   /* en ustteyken hero tam gorunur */
    pageEl.style.transform = 'translate3d(0,' + (-Math.round(y)) + 'px,0)';
  }

  function apply(r, c, why) {
    if (!rows.length) return;
    if (r < 0) r = 0;
    if (r > rows.length - 1) r = rows.length - 1;
    rowIdx = r;
    var row = rows[r];
    c = clampCol(r, c);
    colMem[row.id] = c;
    clearFocus();
    var el = row.items[c];
    if (!el) return;
    el.classList.add('focused');
    var holder = el.parentNode;
    if (holder && holder.classList && holder.classList.contains('row-tile')
            && el.classList.contains('card-focus-landscape')) {
      holder.classList.add('focus-expanded');
    } else if (holder && holder.classList && holder.classList.contains('row-tile')
            && el.classList.contains('card-focus-effect')) {
      holder.classList.add('focus-poster');
    }
    /* ilk kurulum ('init') input'u DOM-odaklamaz: ust menude arama kutusu ilk ogedir; her ekran acilisinda odak/ekran klavyesi ona kaymasin */
    try { if (el.focus && el.tagName === 'INPUT' && why !== 'init') el.focus(); } catch (e) {}
    scrollStrip(row, c);
    scrollPage(row, r);
    if (focusCb) {
      try { focusCb({ el: el, rowId: row.id, rowIndex: r, col: c, rowCount: rows.length, why: why || 'move' }); }
      catch (e2) { console.log('[nav] focus callback hata: ' + e2); }
    }
  }

  function restorePending() {
    var saved = pendingRestore;
    if (!saved || !rows.length) return false;
    var r = -1, c = -1, i, j;

    /* Ayni yapim birden cok satirda bulunabilir; once eski satirini tercih et. */
    if (saved.rowId) {
      for (i = 0; i < rows.length; i++) {
        if (rows[i].id !== saved.rowId) continue;
        r = i;
        if (saved.itemKey) {
          for (j = 0; j < rows[i].items.length; j++) {
            if (itemKey(rows[i].items[j]) === saved.itemKey) { c = j; break; }
          }
        }
        break;
      }
    }
    /* Satir sirasi degistiyse kartin kalici kimligiyle yeni yerini bul. */
    if (saved.itemKey && c < 0) {
      for (i = 0; i < rows.length && c < 0; i++) {
        for (j = 0; j < rows[i].items.length; j++) {
          if (itemKey(rows[i].items[j]) === saved.itemKey) { r = i; c = j; break; }
        }
      }
    }
    if (r < 0) return false;
    if (c < 0) c = saved.col === undefined ? 0 : saved.col;
    pendingRestore = null;
    apply(r, c, 'restore');
    return true;
  }

  function move(dir) {
    if (!enabled || !rows.length) return false;
    var row = rows[rowIdx];
    var col = colMem[row.id] || 0;
    if (dir === 'left' || dir === 'right') {
      var nc = col + (dir === 'right' ? 1 : -1);
      if (nc < 0 || nc > row.items.length - 1) return false;
      apply(rowIdx, nc);
      return true;
    }
    if (dir === 'up' || dir === 'down') {
      var nr = rowIdx + (dir === 'down' ? 1 : -1);
      if (nr < 0 || nr > rows.length - 1) return false;
      var nextRow = rows[nr];
      var remembered = colMem[nextRow.id];
      if (remembered === undefined || remembered === null) remembered = 0;
      apply(nr, remembered);
      return true;
    }
    return false;
  }

  function currentEl() {
    var row = rows[rowIdx];
    if (!row) return null;
    return row.items[colMem[row.id] || 0] || null;
  }

  function enter() {
    var el = currentEl();
    if (!el) return false;
    if (enterCb) {
      var handled = enterCb(el, rows[rowIdx] ? rows[rowIdx].id : null);
      if (handled === true) return true;
    }
    if (el.tagName === 'INPUT') { try { el.focus(); } catch (e) {} return true; }
    if (el.click) { el.click(); return true; }
    return false;
  }

  var nav = {
    setRoot: function (rEl, pEl) {
      root = rEl || null;
      pageEl = pEl || null;
      rowIdx = 0;
      colMem = {};
      collect();
      if (rows.length) apply(0, 0, 'init');
      var ticket = ++restoreTicket;
      if (pendingRestore) {
        setTimeout(function () {
          if (ticket === restoreTicket) restorePending();
        }, 0);
      }
    },
    /* DOM degistiginde: odak konumunu (satir id + sutun) korumaya calisir */
    refresh: function () {
      var prevId = rows[rowIdx] ? rows[rowIdx].id : null;
      collect();
      if (!rows.length) return;
      if (restorePending()) return;
      var r = 0;
      if (prevId) {
        for (var i = 0; i < rows.length; i++) { if (rows[i].id === prevId) { r = i; break; } }
      }
      var c = colMem[rows[r].id];
      if (c === undefined || c === null) c = 0;
      apply(r, c, 'refresh');
    },
    move: move,
    enter: enter,
    currentEl: currentEl,
    current: function () {
      var row = rows[rowIdx];
      if (!row) return null;
      var col = colMem[row.id] || 0;
      return { rowId: row.id, rowIndex: rowIdx, col: col, rowCount: rows.length,
        itemKey: itemKey(row.items[col]) };
    },
    snapshot: function () {
      var row = rows[rowIdx];
      if (!row) return null;
      var col = colMem[row.id] || 0;
      return { rowId: row.id, rowIndex: rowIdx, col: col, itemKey: itemKey(row.items[col]) };
    },
    restoreNext: function (saved) { pendingRestore = saved || null; },
    rowIds: function () { return rows.map(function (r) { return r.id; }); },
    focusRowById: function (id, col) {
      if (restorePending()) return true;
      for (var i = 0; i < rows.length; i++) {
        if (rows[i].id === id) { apply(i, col === undefined ? (colMem[id] || 0) : col, 'jump'); return true; }
      }
      return false;
    },
    focusIndex: function (r, c) { if (!restorePending()) apply(r, c, 'jump'); },
    onFocus: function (fn) { focusCb = fn; },
    onEnter: function (fn) { enterCb = fn; },
    setEnabled: function (v) { enabled = !!v; },
    reset: function () { root = null; pageEl = null; rows = []; rowIdx = 0; colMem = {}; focusCb = null; enterCb = null; enabled = true; pendingRestore = null; restoreTicket++; },
    /* sahne boyutu degisti: odak/klasor durumu ve callback'ler DEGISMEDEN, odakli satirin yatay + dikey kaydirma konumunu
       yeni olculerle (root.clientHeight = sahne yuksekligi) yeniden hesapla */
    relayout: function () {
      var row = rows[rowIdx];
      if (!row) return;
      var c = colMem[row.id] || 0;
      scrollStrip(row, c);
      scrollPage(row, rowIdx);
    },
    /* satir bazli yatay konumu yeniden hesapla (kartlar sonradan geldiyse) */
    rescrollRow: function (id) {
      for (var i = 0; i < rows.length; i++) {
        if (rows[i].id === id) { scrollStrip(rows[i], colMem[id] || 0); return; }
      }
    }
  };

  DZ.nav = nav;
})(window);
