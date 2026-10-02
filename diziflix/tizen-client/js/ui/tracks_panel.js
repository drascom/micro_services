/* ui/tracks_panel.js - "Ses ve Altyazılar" paneli (Netflix tarzi iki sutun: SES | ALTYAZI), kumanda odakli.
   Panel yalniz gosterir ve odagi tutar; ne yapilacagina player.js karar verir (key() bir eylem dondurur).
   Kumanda: Yukari/Asagi = secenekler, Sol/Sag = sutun, OK = uygula, Geri/Stop/Sari = kapat.
   Tek (ya da hic) secenekli sutun GRI ve odaklanmaz; secili oge ● ile isaretli; "gömülü" rozeti gömülü altyazıda.
   View: DZ.tracks.view(model, sel) -> { audio:{items:[{id,label,current}],dim}, subs:{items:[{id,label,badge,current,unavailable}],dim} }
   ES2017, opsiyonel zincir YOK. */
(function (g) {
  'use strict';
  var DZ = g.DZ = g.DZ || {};

  function mk(tag, cls, txt) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (txt !== undefined && txt !== null) n.textContent = txt;
    return n;
  }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }

  var KINDS = ['audio', 'sub'];
  var HEADS = ['SES', 'ALTYAZI'];

  function create(parent) {
    var root = mk('div', 'pl-tp hidden');
    var box = mk('div', 'pl-tp-box');
    box.appendChild(mk('div', 'pl-tp-title', 'Ses ve Altyazılar'));
    var cols = mk('div', 'pl-tp-cols');
    var colEls = [mk('div', 'pl-tp-col'), mk('div', 'pl-tp-col')];
    cols.appendChild(colEls[0]);
    cols.appendChild(colEls[1]);
    box.appendChild(cols);
    var note = mk('div', 'pl-tp-note', '');
    box.appendChild(note);
    box.appendChild(mk('div', 'pl-tp-hint', '◄ ► sütun · ▲ ▼ seç · OK uygula · GERİ kapat'));
    root.appendChild(box);
    parent.appendChild(root);

    var P = { el: root, isOpen: false, view: null, col: 1, idx: [0, 0], note: note };

    function colView(c) { return P.view ? P.view[KINDS[c] === 'audio' ? 'audio' : 'subs'] : null; }
    function active(c) { var v = colView(c); return !!(v && !v.dim && v.items.length); }
    function currentIndex(v) {
      for (var i = 0; i < v.items.length; i++) if (v.items[i].current) return i;
      return 0;
    }

    P.render = function () {
      for (var c = 0; c < 2; c++) {
        var el = colEls[c], v = colView(c);
        clear(el);
        el.className = 'pl-tp-col' + (v && v.dim ? ' dim' : '');
        el.appendChild(mk('div', 'pl-tp-head', HEADS[c]));
        var items = v ? v.items : [];
        for (var i = 0; i < items.length; i++) {
          var it = items[i];
          var cls = 'pl-tp-item' + (it.current ? ' current' : '') + (it.unavailable ? ' unavail' : '') +
            (P.isOpen && P.col === c && active(c) && P.idx[c] === i ? ' focused' : '');
          var row = mk('div', cls, it.label);
          if (it.badge) row.appendChild(mk('span', 'pl-tp-badge', it.badge));
          el.appendChild(row);
        }
      }
    };

    P.open = function (view) {
      P.view = view;
      P.isOpen = true;
      P.idx = [currentIndex(view.audio), currentIndex(view.subs)];
      P.col = active(1) ? 1 : (active(0) ? 0 : 1);
      P.setNote('');
      root.classList.remove('hidden');
      P.render();
    };

    /* secim uygulandiktan sonra: odak indeksleri korunur, ● yeni secimde */
    P.update = function (view) {
      P.view = view;
      for (var c = 0; c < 2; c++) {
        var n = colView(c) ? colView(c).items.length : 0;
        if (P.idx[c] > n - 1) P.idx[c] = Math.max(0, n - 1);
      }
      P.render();
    };

    P.close = function () {
      P.isOpen = false;
      root.classList.add('hidden');
      P.setNote('');
    };

    P.setNote = function (text) { note.textContent = text || ''; };

    function move(dir) {
      if (!active(P.col)) return;
      var n = colView(P.col).items.length;
      P.idx[P.col] = Math.max(0, Math.min(n - 1, P.idx[P.col] + dir));
      P.render();
    }
    function toColumn(c) {
      if (P.col === c || !active(c)) return;
      P.col = c;
      P.render();
    }

    /* -> null (yalniz gezinti) | {type:'close'} | {type:'select', kind:'audio'|'sub', id} */
    P.key = function (name) {
      if (!P.isOpen) return null;
      if (note.textContent) P.setNote('');
      switch (name) {
        case 'up': move(-1); return null;
        case 'down': move(1); return null;
        case 'left': toColumn(0); return null;
        case 'right': toColumn(1); return null;
        case 'enter':
        case 'playpause':
        case 'play':
          if (!active(P.col)) return null;
          return { type: 'select', kind: KINDS[P.col], id: colView(P.col).items[P.idx[P.col]].id };
        case 'back':
        case 'stop':
        case 'exit':
        case 'yellow': return { type: 'close' };
        default: return null;
      }
    };

    P.destroy = function () {
      P.isOpen = false;
      if (root.parentNode) root.parentNode.removeChild(root);
    };
    return P;
  }

  DZ.tracksPanel = { create: create };
})(window);
