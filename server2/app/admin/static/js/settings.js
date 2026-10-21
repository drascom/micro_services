/* Ayarlar screen (T15): runtime settings (PUT /api/control/settings), LLM health + 7-day usage, TMDB key state,
   job queue summary + drain. Secrets are never shown: the API only says "tanımlı / tanımsız". */
(function () {
  'use strict';
  var A = window.ADMIN;
  if (!A || !A.screen) return;

  var GROUPS = [
    {title: 'LLM', keys: ['llm_daily_calls_per_site']},
    {title: 'Onarım', keys: ['repair_autoapply'],
      note: 'Açıkken ölçümle doğrulanan düzeltme kendiliğinden uygulanır ve zile bilgi notu düşer; kapalıyken her düzeltme sana sorulur.'},
    {title: 'Tarama varsayılanları', keys: ['backfill_days', 'refresh_hour', 'crawl_gate_daily_per_site', 'source_recheck_days'], note: 'Site sayfasındaki Tarama sekmesi site başına değiştirebilir.'},
    {title: 'Sorunlar', keys: ['problem_deadline_s', 'confirm_seconds']},
    {title: 'TMDB', keys: ['tmdb_auto', 'tmdb_types']},
    {title: 'Afiş ve akış vekili', keys: ['image_hosts_extra', 'stream_proxy_hosts', 'stream_proxy_force']}
  ];

  function field(key, sc, value) {
    var id = 'set-' + key;
    var lab = '<label for="' + id + '">' + A.esc(sc.label) + '</label>';
    var def = '<small class="muted"> varsayılan: ' + A.esc(JSON.stringify(sc['default'])) + '</small>';
    if (sc.type === 'bool') {
      return '<div class="srow"><label><input type="checkbox" id="' + id + '" data-k="' + key + '"' + (value ? ' checked' : '') +
        '> ' + A.esc(sc.label) + '</label>' + def + '</div>';
    }
    if (sc.type === 'int') {
      return '<div class="srow">' + lab + ' <input type="number" id="' + id + '" data-k="' + key + '" min="' + sc.min + '" max="' + sc.max +
        '" value="' + A.esc(value) + '" style="width:7em"> <small class="muted">(' + sc.min + '..' + sc.max + ')</small>' + def + '</div>';
    }
    if (sc.type === 'choices') {
      return '<div class="srow">' + A.esc(sc.label) + ': ' + sc.choices.map(function (c) {
        return '<label><input type="checkbox" data-k="' + key + '" data-c="' + A.esc(c) + '"' +
          ((value || []).indexOf(c) >= 0 ? ' checked' : '') + '> ' + A.esc(c === 'movie' ? 'film' : c === 'series' ? 'dizi' : c) + '</label>';
      }).join(' ') + def + '</div>';
    }
    return '<div class="srow">' + lab + '<br><input type="text" id="' + id + '" data-k="' + key + '" style="width:100%"' +
      ' placeholder="virgülle ayrılmış host listesi (ör. cdn.example.tld)" value="' + A.esc((value || []).join(', ')) + '"></div>';
  }

  function collect(view, schema) {
    var out = {};
    Object.keys(schema).forEach(function (key) {
      var sc = schema[key];
      if (sc.type === 'bool') out[key] = view.querySelector('input[data-k="' + key + '"]').checked;
      else if (sc.type === 'int') out[key] = parseInt(view.querySelector('input[data-k="' + key + '"]').value, 10);
      else if (sc.type === 'choices') {
        out[key] = Array.prototype.slice.call(view.querySelectorAll('input[data-k="' + key + '"]:checked')).map(function (i) {
          return i.getAttribute('data-c');
        });
      } else {
        out[key] = view.querySelector('input[data-k="' + key + '"]').value.split(/[\s,]+/).filter(Boolean);
      }
    });
    return out;
  }

  function healthLine(h) {
    if (!h) return '<span class="muted">bilinmiyor</span>';
    return (h.ok ? '<span class="pill ok">hesap sağlıklı</span>' : '<span class="pill bad">sorunlu: ' + A.esc(h.error || '?') + '</span>') +
      ' <span class="muted">' + A.esc(h.model || '') + ' · ' + A.esc(h.checked_at || '') + (h.cached ? ' (önbellek)' : '') + '</span>';
  }

  function usageTable(u) {
    if (!u || !u.sites || !u.sites.length) return '<p class="muted">Son ' + (u ? u.days : 7) + ' günde LLM çağrısı yok.</p>';
    return '<table class="grid"><thead><tr><th>Site</th><th>Çağrı</th><th>Başarılı</th><th>Admin (bütçe dışı)</th><th>Sistem (bütçeden)</th><th>Süre (sn)</th></tr></thead><tbody>' +
      u.sites.map(function (r) {
        return '<tr><td>' + A.esc(r.site_id || '(genel)') + '</td><td class="num">' + r.calls + '</td><td class="num">' + r.ok +
          '</td><td class="num">' + (r.admin || 0) + '</td><td class="num">' + r.background + '</td><td class="num">' + Math.round((r.ms || 0) / 1000) + '</td></tr>';
      }).join('') + '</tbody></table><p class="muted">Toplam ' + u.total.calls + ' çağrı (son ' + u.days + ' gün).</p>';
  }

  function failedList(jobs) {
    if (!jobs.length) return '<p class="muted">Başarısız iş yok.</p>';
    return '<table class="grid"><thead><tr><th>#</th><th>Tür</th><th>Site</th><th>Hata</th><th>Bitiş</th><th></th></tr></thead><tbody>' +
      jobs.map(function (j) {
        return '<tr><td class="num">' + j.id + '</td><td class="mono">' + A.esc(j.kind) + '</td><td>' + A.esc(j.site_id || '-') +
          '</td><td>' + A.esc(j.error || '') + '</td><td>' + A.when(j.finished_at) + '</td><td><button type="button" data-retry="' + j.id +
          '">yeniden dene</button></td></tr>';
      }).join('') + '</tbody></table>';
  }

  function render(view) {
    var data = null;
    var health = null;

    function draw() {
      var s = data.settings, sc = data.schema, j = data.jobs;
      var h = '<h2>Ayarlar</h2><form id="sform">';
      GROUPS.forEach(function (g) {
        h += '<section class="sgroup"><h3>' + A.esc(g.title) + '</h3>';
        if (g.title === 'LLM') {
          h += '<div class="srow">Hesap: <span id="llmh">' + healthLine(health) + '</span> <button type="button" id="llmre">yeniden kontrol et</button></div>' +
            '<div class="srow">Model: <span class="mono">' + A.esc(data.llm.model) + '</span></div>';
        }
        if (g.title === 'TMDB') {
          h += '<div class="srow">Anahtar: ' + (data.secrets.tmdb_key ? '<span class="pill ok">tanımlı</span>' :
            '<span class="pill warn">tanımsız</span> <small class="muted">(.env TMDB_ACCESS_KEY)</small>') +
            (data.tmdb && data.tmdb.key_invalid ? ' <span class="pill warn">geçersiz (401)</span>' : '') + '</div>';
          if (data.tmdb) {
            h += '<div class="srow">Zenginleştirilmiş <b>' + data.tmdb.matched + '</b> / ' + data.tmdb.total +
              (data.tmdb.pending ? ' <small class="muted">(' + data.tmdb.pending + ' iş sırada)</small>' : '') +
              ' <button type="button" id="tmdbfill">Eksikleri şimdi doldur</button> <span id="tmdbmsg" class="muted"></span></div>';
          }
        }
        g.keys.forEach(function (k) { if (sc[k]) h += field(k, sc[k], s[k]); });
        if (g.note) h += '<p class="muted">' + A.esc(g.note) + '</p>';
        if (g.title === 'LLM') h += '<h4>Son 7 gün, site başına</h4>' + usageTable(data.llm.usage);
        h += '</section>';
      });
      h += '<div class="bar"><button type="submit">kaydet</button> <span id="smsg" class="muted"></span></div></form>';
      h += '<section class="sgroup"><h3>Sistem</h3><div class="srow">İş kuyruğu: sıradaki <b>' + j.queued + '</b> · çalışan <b>' + j.running +
        '</b> · bekleyen <b>' + j.waiting + '</b> · başarısız <b>' + j.failed + '</b> <button type="button" id="showfail">başarısızları göster</button></div>' +
        '<div class="srow">Drain: ' + (j.drain.on ? '<span class="pill warn">açık (yeni iş başlamıyor)</span>' : '<span class="pill ok">kapalı</span>') +
        ' <button type="button" id="drain">' + (j.drain.on ? 'drain kapat' : 'drain aç') + '</button></div>' +
        '<div id="failed"></div></section>';
      view.innerHTML = '<style>.sgroup{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:10px 14px;margin-bottom:10px}' +
        '.sgroup h3{margin:2px 0 8px}.srow{margin:6px 0}.srow small{margin-left:6px}</style>' + h;
      bind();
    }

    function load() {
      return A.api('/api/control/settings').then(function (d) { data = d; draw(); })
        .catch(function (e) { view.innerHTML = '<p class="err">' + A.esc(e.message) + '</p>'; });
    }

    function bind() {
      view.querySelector('#sform').addEventListener('submit', function (ev) {
        ev.preventDefault();
        var msg = view.querySelector('#smsg');
        msg.textContent = 'kaydediliyor…';
        A.api('/api/control/settings', {method: 'PUT', body: collect(view, data.schema)}).then(function (d) {
          data = d; draw(); view.querySelector('#smsg').textContent = 'kaydedildi';
        }).catch(function (e) { msg.className = 'err'; msg.textContent = e.message; });
      });
      view.querySelector('#llmre').addEventListener('click', function () {
        view.querySelector('#llmh').textContent = 'kontrol ediliyor…';
        A.post('/api/control/llm/health').then(function (r) { health = r; view.querySelector('#llmh').innerHTML = healthLine(r); })
          .catch(function (e) { view.querySelector('#llmh').textContent = e.message; });
      });
      var fill = view.querySelector('#tmdbfill');
      if (fill) fill.addEventListener('click', function () {
        var msg = view.querySelector('#tmdbmsg');
        msg.textContent = 'sıraya alınıyor…';
        A.post('/api/control/settings/tmdb-backfill').then(function (r) {
          data.tmdb = r.tmdb; draw();
          view.querySelector('#tmdbmsg').textContent = r.queued + ' başlık sıraya alındı';
        }).catch(function (e) { msg.className = 'err'; msg.textContent = e.message; });
      });
      view.querySelector('#drain').addEventListener('click', function () {
        A.post('/api/control/drain', {on: !data.jobs.drain.on}).then(load).catch(function (e) { A.flash(e.message); });
      });
      view.querySelector('#showfail').addEventListener('click', function () {
        A.api('/api/control/jobs?state=failed&limit=50').then(function (r) {
          var box = view.querySelector('#failed');
          box.innerHTML = '<h4>Başarısız işler</h4>' + failedList(r.jobs || []);
          Array.prototype.forEach.call(box.querySelectorAll('[data-retry]'), function (b) {
            b.addEventListener('click', function () {
              A.post('/api/control/jobs/' + b.getAttribute('data-retry') + '/retry').then(load).catch(function (e) { A.flash(e.message); });
            });
          });
        }).catch(function (e) { A.flash(e.message); });
      });
    }

    A.api('/api/control/llm/health').then(function (r) {
      health = r;
      var el = view.querySelector('#llmh');
      if (el) el.innerHTML = healthLine(r);
    }).catch(function () {});
    load();
  }

  A.screen('settings', {title: 'Ayarlar', menu: true, render: render});
})();
