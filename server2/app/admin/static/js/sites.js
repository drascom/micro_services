/* Siteler (#/sites): site table + "Site ekle" (one address box). */
(function () {
  'use strict';
  var A = window.ADMIN;
  var STATUS = {draft: ['taslak', ''], active: ['etkin', 'ok'], paused: ['duraklatıldı', 'warn']};

  function mapCell(m) {
    m = m || {};
    var out = '<span class="num">' + (m.passed || 0) + '/' + (m.total || 7) + '</span>';
    if (m.passed && m.passed === m.total) out += ' <span class="pill ok">✓</span>';
    if (m.stuck) {
      out += m.stuck.state === 'needs_human' || m.stuck.state === 'review'
        ? ' <span class="pill bad">✋ ' + A.esc(m.stuck.stage) + '</span>'
        : ' <span class="pill heal">⚠ ' + A.esc(m.stuck.stage) + '</span>';
    } else if (m.running) {
      out += ' <span class="pill run pulse">… ' + A.esc(m.running) + '</span>';
    } else if (m.warn) {
      out += ' <span class="pill warn">⚠ ' + A.esc(m.warn) + '</span>';
    }
    return out;
  }

  function row(s) {
    var st = STATUS[s.status] || [s.status, ''];
    var id = encodeURIComponent(s.site_id);
    var pause = s.status === 'paused'
      ? '<button type="button" data-act="resume" data-id="' + A.esc(s.site_id) + '">sürdür</button>'
      : '<button type="button" data-act="pause" data-id="' + A.esc(s.site_id) + '">duraklat</button>';
    return '<tr>' +
      '<td><a href="#/site/' + id + '/map"><b>' + A.esc(s.display_name || s.site_id) + '</b></a>' +
      '<div class="muted mono">' + A.esc(s.base_url) + '</div></td>' +
      '<td><span class="pill ' + st[1] + '">' + A.esc(st[0]) + '</span></td>' +
      '<td>' + mapCell(s.map) + '</td>' +
      '<td class="num">' + (s.titles || 0) + ' başlık' + (s.crawl && s.crawl.discovered ? '<div class="muted">tarama ' + s.crawl.done + '/' + s.crawl.discovered + '</div>' : '') + '</td>' +  /* T11 */
      '<td>' + A.esc(A.when(s.last_refresh)) + '</td>' +
      '<td class="num">' + (s.open_problems || 0) + '</td>' +
      '<td class="acts"><a class="btn" href="#/site/' + id + '/map">aç</a>' + pause +
      '<button type="button" class="danger" data-act="delete" data-id="' + A.esc(s.site_id) + '">sil</button></td>' +
      '</tr>';
  }

  function render(view) {
    view.innerHTML =
      '<h2>Siteler</h2>' +
      '<form class="bar" id="addsite">' +
      '<input type="url" name="url" placeholder="Site ekle: https://…" required aria-label="Site adresi">' +
      '<button class="primary" type="submit">Site ekle</button><span class="err" id="adderr"></span>' +
      '<details class="addnote"><summary>Site hakkında (isteğe bağlı)</summary>' +
      '<textarea name="notes" maxlength="4000" aria-label="Site hakkında" placeholder="Örn: Ana sayfada &quot;Son Eklenen Diziler&quot; ' +
      'dizi sayfasına gider, diğer kartlar bölüm sayfası. Dizi listesi /dizi-arsivi-01/ (sayfalı: -02, -03). Dizi sayfası ' +
      '/diziler/&lt;slug&gt;/, afiş .category_image içinde. Arama /?s=&lt;kelime&gt;."></textarea></details></form>' +
      '<div id="sitelist"><p class="muted">yükleniyor…</p></div>' +
      '<div id="delbox"></div>';
    var mine = A.gen();
    var list = view.querySelector('#sitelist');

    function load() {
      return A.api('/api/control/sites').then(function (d) {
        if (!A.alive(mine)) return;
        var sites = d.sites || [];
        list.innerHTML = sites.length
          ? '<table class="grid"><thead><tr><th>Site</th><th>Durum</th><th>Harita</th><th>Katalog</th><th>Son tazeleme</th>' +
            '<th>Sorun</th><th></th></tr></thead><tbody>' + sites.map(row).join('') + '</tbody></table>'
          : '<p class="empty">Henüz site yok. Yukarıya bir adres yazıp ekleyin.</p>';
      });
    }

    view.querySelector('#addsite').addEventListener('submit', function (ev) {
      ev.preventDefault();
      var input = ev.target.url, btn = ev.target.querySelector('button[type=submit]'), err = view.querySelector('#adderr');
      err.textContent = '';
      btn.disabled = true;
      A.post('/api/control/sites', {url: input.value.trim(), notes: ev.target.notes.value.trim()}).then(function (d) {
        A.go('#/site/' + encodeURIComponent(d.site_id) + '/map');
      }).catch(function (e) { err.textContent = e.message; }).then(function () { btn.disabled = false; });
    });

    list.addEventListener('click', function (ev) {
      var b = ev.target.closest('button[data-act]');
      if (!b) return;
      var id = b.getAttribute('data-id'), act = b.getAttribute('data-act');
      var path = '/api/control/sites/' + encodeURIComponent(id);
      if (act === 'delete') return askDelete(view.querySelector('#delbox'), id, load);
      b.disabled = true;
      A.post(path + '/' + act).then(load).catch(function (e) { A.flash(e.message); b.disabled = false; });
    });

    A.poll(load, 10000);
  }

  function askDelete(box, id, done) {
    box.innerHTML = '<div class="hintbox" style="margin-top:12px"><b>' + A.esc(id) + '</b> silinsin mi?' +
      '<label style="display:block;margin:8px 0"><input type="checkbox" id="purge"> katalogdaki içeriği de sil</label>' +
      '<button type="button" class="danger" id="delyes">sil</button> <button type="button" id="delno">vazgeç</button></div>';
    box.querySelector('#delno').onclick = function () { box.innerHTML = ''; };
    box.querySelector('#delyes').onclick = function () {
      var purge = box.querySelector('#purge').checked ? '1' : '0';
      A.del('/api/control/sites/' + encodeURIComponent(id) + '?purge=' + purge).then(function () {
        box.innerHTML = '';
        done();
      }).catch(function (e) { A.flash(e.message); });
    };
    box.scrollIntoView({block: 'nearest'});
  }

  A.screen('sites', {title: 'Siteler', menu: true, render: render});
})();
