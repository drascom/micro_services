/* Site page "Yönlendirme" tab (T12): source groups, rules, LLM suggestions, free-text rule, manual rule form. */
(function () {
  'use strict';
  var A = window.ADMIN;
  if (!A || !A.siteTab) return;

  var KIND = {section: 'ana sayfa bölümü', listing: 'liste', genre: 'tür', type: 'yapım türü', url_pattern: 'adres kalıbı'};
  var ROLE = {trending: 'Haftanın Trendleri', latest_episodes: 'Yeni Bölümler', latest_series: 'Yeni Diziler',
    latest_movies: 'Yeni Filmler', featured: 'Öne Çıkanlar', noteworthy_movies: 'Dikkate Değer Filmler'};

  function target(t, cats) {
    if (!t) return '-';
    if (t.target_kind === 'ignore') return '<span class="pill">hiçbir yere</span>';
    if (t.target_kind === 'role') return 'rol: ' + A.esc(ROLE[t.target_value] || t.target_value);
    var c = (cats || []).filter(function (x) { return x.slug === t.target_value; })[0];
    return 'kategori: ' + A.esc(c ? c.title : t.target_value);
  }
  function match(r) { return A.esc(KIND[r.match_kind] || r.match_kind) + ' = <b>' + A.esc(r.match_value) + '</b>'; }
  function examples(p) {
    if (!p) return '';
    return '<span class="muted">' + p.count + ' başlık' + (p.examples.length ? ': ' + p.examples.map(function (e) {
      return A.esc(e.title);
    }).join(', ') : '') + '</span>';
  }

  function tab(body, siteId) {
    var base = '/api/control/sites/' + encodeURIComponent(siteId) + '/routing';
    var data = null;

    function act(p) {
      return p.then(load).catch(function (e) { A.flash(e.message); });
    }

    function form() {
      var cats = data.categories || [];
      return '<form id="rform" class="bar">' +
        '<select name="match_kind">' + data.match_kinds.map(function (k) {
          return '<option value="' + k + '">' + A.esc(KIND[k] || k) + '</option>'; }).join('') + '</select> ' +
        '<input name="match_value" placeholder="değer (ör. Yeni Bölümler)" required> → ' +
        '<select name="target">' +
        data.roles.map(function (r) { return '<option value="role:' + r.id + '">rol: ' + A.esc(ROLE[r.id] || r.id) + '</option>'; }).join('') +
        cats.map(function (c) { return '<option value="category:' + A.esc(c.slug) + '">kategori: ' + A.esc(c.title) + '</option>'; }).join('') +
        '<option value="ignore:">hiçbir yere (yok say)</option></select> ' +
        '<input name="priority" type="number" value="0" style="width:5em" title="öncelik"> ' +
        '<label><input type="checkbox" name="all_sites"> tüm siteler</label> ' +
        '<button type="button" data-a="preview">önizle</button> <button type="submit">ekle</button>' +
        '<div id="rprev"></div></form>' +
        '<form id="cform" class="bar"><input name="title" placeholder="yeni kategori adı"> <button type="submit">kategori ekle</button></form>';
    }

    function formRule(f) {
      var t = f.target.value.split(':');
      return {match_kind: f.match_kind.value, match_value: f.match_value.value, target_kind: t[0], target_value: t[1] || '',
        priority: parseInt(f.priority.value || '0', 10), all_sites: f.all_sites.checked};
    }

    function draw() {
      var cats = data.categories;
      var h = '<h3>Kaynak grupları</h3>';
      h += data.groups.length ? '<table class="grid"><thead><tr><th>Tür</th><th>Ad</th><th>Başlık</th><th>Örnek</th><th>Şu an gittiği yer</th></tr></thead><tbody>' +
        data.groups.map(function (g) {
          return '<tr><td>' + A.esc(KIND[g.kind] || g.kind) + (g.from_field ? ' <span class="muted">(alan)</span>' : '') + '</td><td>' +
            A.esc(g.value) + '</td><td>' + g.count + '</td><td class="muted">' + A.esc(g.examples.join(', ')) + '</td><td>' +
            (g.targets.length ? g.targets.map(function (t) { return target(t, cats); }).join('<br>') : '<span class="muted">özel grup yok</span>') + '</td></tr>';
        }).join('') + '</tbody></table>' : '<p class="muted">Henüz kaynak grubu yok (tarama bekleniyor).</p>';

      h += '<h3>Kurallar</h3>';
      h += data.rules.length ? '<table class="grid"><thead><tr><th>Etkin</th><th>Eşleşme</th><th>Hedef</th><th>Öncelik</th><th>Kapsam</th><th>Ekleyen</th><th></th></tr></thead><tbody>' +
        data.rules.map(function (r) {
          return '<tr><td><input type="checkbox" data-toggle="' + r.id + '"' + (r.enabled ? ' checked' : '') + '></td><td>' + match(r) +
            '</td><td>' + target(r, cats) + '</td><td>' + r.priority + '</td><td>' + (r.site_id ? 'bu site' : 'tüm siteler') +
            '</td><td>' + (r.created_by === 'llm' ? 'LLM' : 'sen') + '</td><td><button type="button" data-del="' + r.id + '">sil</button></td></tr>';
        }).join('') + '</tbody></table>' : '<p class="muted">Kural yok: başlıklar yalnız "tüm diziler / tüm filmler" satırlarında görünür.</p>';

      h += '<h3>Öneriler</h3><p><button type="button" data-a="suggest">LLM\'den öneri iste</button>' +
        (data.suggestions.length ? ' <button type="button" data-a="acceptall">tümünü onayla</button>' : '') + '</p>';
      h += data.suggestions.length ? '<ul class="samples">' + data.suggestions.map(function (s) {
        return '<li>' + match(s.rule) + ' → ' + (s.new_category ? 'yeni kategori öner: <b>' + A.esc(s.new_category) + '</b>' : target(s.rule, cats)) +
          ' <span class="muted">(öncelik ' + s.rule.priority + (s.origin === 'text' ? ', metinden' : '') + ')</span>' +
          (s.rule.note ? ' <i class="muted">' + A.esc(s.rule.note) + '</i>' : '') + '<br>' + examples(s.preview) +
          ' <button type="button" data-ok="' + s.id + '">onayla</button> <button type="button" data-no="' + s.id + '">reddet</button></li>';
      }).join('') + '</ul>' : '<p class="muted">Bekleyen öneri yok.</p>';

      h += '<h3>Serbest metinle kural</h3><form id="tform" class="bar"><input name="text" style="min-width:24em" ' +
        'placeholder="ör. Kore dizileri Asya grubuna gitsin"> <button type="submit">çevir</button></form>' +
        '<p class="muted">Çevrilen kural önizlemesiyle birlikte öneriler listesine düşer; orada onayla.</p>';
      h += '<h3>Elle kural ekle</h3>' + form();
      body.innerHTML = h;
    }

    function load() {
      return A.api(base).then(function (d) { data = d; draw(); })
        .catch(function (e) { body.innerHTML = '<p class="err">' + A.esc(e.message) + '</p>'; });
    }

    body.addEventListener('click', function (ev) {
      var b = ev.target.closest('button,input[type=checkbox]');
      if (!b) return;
      var id;
      if ((id = b.getAttribute('data-del')) && window.confirm('Kural silinsin mi?')) act(A.del(base + '/rules/' + id));
      else if ((id = b.getAttribute('data-toggle'))) act(A.api(base + '/rules/' + id, {method: 'PUT', body: {enabled: b.checked}}));
      else if ((id = b.getAttribute('data-ok'))) act(A.post(base + '/suggestions/' + id + '/accept'));
      else if ((id = b.getAttribute('data-no'))) act(A.post(base + '/suggestions/' + id + '/reject'));
      else if (b.getAttribute('data-a') === 'acceptall') act(A.post(base + '/suggestions/accept-all'));
      else if (b.getAttribute('data-a') === 'suggest') {
        b.disabled = true; b.textContent = 'LLM çalışıyor…';
        act(A.post(base + '/suggest').then(function (r) {
          if (r.invalid) A.flash(r.invalid + ' geçersiz taslak elendi');
        }));
      } else if (b.getAttribute('data-a') === 'preview') {
        var f = body.querySelector('#rform'), out = body.querySelector('#rprev');
        A.post(base + '/preview', formRule(f)).then(function (p) { out.innerHTML = examples(p); })
          .catch(function (e) { out.innerHTML = '<span class="err">' + A.esc(e.message) + '</span>'; });
      }
    });
    body.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var f = ev.target;
      if (f.id === 'rform') act(A.post(base + '/rules', formRule(f)));
      else if (f.id === 'cform') act(A.post(base + '/categories', {title: f.elements['title'].value}));
      else if (f.id === 'tform') {
        f.querySelector('button').disabled = true;
        act(A.post(base + '/from-text', {text: f.elements['text'].value}));
      }
    });
    load();
  }

  A.siteTab('routing', 'Yönlendirme', tab, 70);
})();
