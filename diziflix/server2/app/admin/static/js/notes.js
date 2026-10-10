/* Admin notes bell (top strip): unread notes dropdown + "okundu". */
(function () {
  'use strict';
  var A = window.ADMIN;
  var LEVEL = {info: '', warn: 'warn', action: 'bad'};

  function render(box, notes) {
    if (!notes.length) {
      box.innerHTML = '<p class="muted" style="margin:6px">Okunmamış not yok.</p>';
      return;
    }
    box.innerHTML = notes.map(function (n) {
      var site = n.site_id ? ' · <a href="#/site/' + encodeURIComponent(n.site_id) + '/map">' + A.esc(n.site_id) + '</a>' : '';
      return '<div class="n"><span class="pill ' + (LEVEL[n.level] || '') + '">' + A.esc(n.level) + '</span> ' +
        A.esc(n.text) + '<small>' + A.esc(A.when(n.at)) + site + '</small></div>';
    }).join('') + '<div style="margin-top:8px;text-align:right"><button type="button" data-read="' + notes[0].id + '">okundu</button></div>';
  }

  function open(btn, box) {
    box.hidden = false;
    btn.setAttribute('aria-expanded', 'true');
    box.innerHTML = '<p class="muted" style="margin:6px">yükleniyor…</p>';
    A.api('/api/control/notes?unread=1').then(function (d) { render(box, d.notes || []); })
      .catch(function (e) { box.innerHTML = '<p class="err">' + A.esc(e.message) + '</p>'; });
  }

  function close(btn, box) { box.hidden = true; btn.setAttribute('aria-expanded', 'false'); }

  document.addEventListener('DOMContentLoaded', function () {
    var btn = document.getElementById('bell');
    var box = document.getElementById('bellmenu');
    if (!btn || !box) return;
    btn.addEventListener('click', function (ev) {
      ev.stopPropagation();
      if (box.hidden) open(btn, box); else close(btn, box);
    });
    box.addEventListener('click', function (ev) {
      ev.stopPropagation();
      var id = ev.target.getAttribute && ev.target.getAttribute('data-read');
      if (!id) return;
      A.post('/api/control/notes/read', {upto_id: +id}).then(function () {
        close(btn, box);
        A.refreshStrip();
      }).catch(function (e) { A.flash(e.message); });
    });
    document.addEventListener('click', function () { if (!box.hidden) close(btn, box); });
  });
})();
