/* Kategoriler screen (T24): the home screen's rows in client order. Fixed rows (🔒) can be hidden, renamed and limited
   but never deleted; dynamic categories are defined by a source (genre / site section-listing / list / type, or only
   routing rules) and can be deleted. Drag the ⋮⋮ handle or use ▲▼ to reorder; every change is saved at once. */
(function () {
  'use strict';
  var A = window.ADMIN;
  if (!A || !A.screen) return;

  var B = '/api/control/categories';
  var C = null, O = null, view = null, drag = null, dragPrev = null;
  var KINDS = {lists: 'yalnız yönlendirme kuralları', genre: 'tür (genre)', site: 'site bölümü / listesi', list: 'liste (library_lists)', type: 'yapım tipi'};
  var ORIGINS = {section: 'ana sayfa bölümü', listing: 'listeleme', genre: 'site türü'};
  var TYPES = {'': 'hepsi', movie: 'film', series: 'dizi'};

  function find(slug) { for (var i = 0; i < (C || []).length; i++) if (C[i].slug === slug) return C[i]; return null; }
  function order() { return (C || []).map(function (c) { return c.slug; }); }
  function put(path, body) { return A.api(path, {method: 'PUT', body: body}); }
  function fail(e) { A.flash('Kaydedilemedi: ' + (e && e.message || e)); return load(); }

  function srcText(s) {
    if (!s) return '';
    var t = s.type ? ' · ' + TYPES[s.type] : '';
    if (s.kind === 'genre') return 'tür: ' + s.genre + t;
    if (s.kind === 'type') return 'tip: ' + TYPES[s.type];
    if (s.kind === 'site') return s.site_id + ' · ' + (ORIGINS[s.origin] || s.origin) + ': ' + s.value + t;
    if (s.kind === 'list') return 'liste: ' + s.list_id + t;
    return 'yönlendirme kuralları';
  }
  function note(c) {
    if (c.kind === 'system') return 'Sabit satır' + (c.enabled ? '' : ' · gizli');
    var t = srcText(c.source) + ' · ' + (c.titles || 0) + ' başlık · ' + (c.playable || 0) + ' oynatılabilir';
    if (c.enabled && (c.playable || 0) < c.min_items) t += ' · ana ekranda görünmüyor (en az ' + c.min_items + ' gerekir)';
    return t;
  }

  function rowHtml(c, i, n) {
    var sys = c.kind === 'system', s = A.esc(c.slug);
    return '<li class="catrow' + (c.enabled ? '' : ' off') + '" data-slug="' + s + '">' +
      '<span class="cathandle" draggable="true" title="Sürükleyerek sırala">⋮⋮</span>' +
      '<span class="catmove"><button class="btn" data-up="' + s + '"' + (i === 0 ? ' disabled' : '') + ' aria-label="Yukarı">▲</button>' +
      '<button class="btn" data-down="' + s + '"' + (i === n - 1 ? ' disabled' : '') + ' aria-label="Aşağı">▼</button></span>' +
      '<span class="catmain"><button class="cattitle" data-edit="' + s + '" title="Adı düzenle">' + (sys ? '🔒 ' : '') + A.esc(c.title) + '</button>' +
      '<small class="muted">' + A.esc(note(c)) + '</small></span>' +
      (sys ? '' : '<label class="catnum">en az <input type="number" min="1" max="100" value="' + c.min_items + '" data-min="' + s + '"></label>') +
      '<label class="catnum">sınır <input type="number" min="0" max="500" value="' + (c.max_items || 0) + '" data-max="' + s + '" title="0 = sınırsız"></label>' +
      '<label><input type="checkbox" data-on="' + s + '"' + (c.enabled ? ' checked' : '') + '> görünür</label>' +
      (sys ? '' : '<button class="btn" data-prev="' + s + '">Önizle</button><button class="btn" data-del="' + s + '">Sil</button>') +
      '<div class="catprev" id="prev-' + s + '" hidden></div></li>';
  }

  function opt(v, label, sel) { return '<option value="' + A.esc(v) + '"' + (v === sel ? ' selected' : '') + '>' + A.esc(label) + '</option>'; }

  function formHtml() {
    var o = O || {genres: [], sites: [], lists: []};
    return '<form id="catform" class="catform"><h3>Yeni dinamik kategori</h3>' +
      '<div class="bar"><input name="title" maxlength="60" placeholder="Ad (örn. Kore Dizileri)" required>' +
      '<select name="kind">' + Object.keys(KINDS).map(function (k) { return opt(k, KINDS[k], 'genre'); }).join('') + '</select>' +
      '<select name="type">' + Object.keys(TYPES).map(function (k) { return opt(k, 'tip: ' + TYPES[k]); }).join('') + '</select></div>' +
      '<div class="bar" data-for="genre"><select name="genre">' + o.genres.map(function (g) { return opt(g.value, g.value + ' (' + g.count + ')'); }).join('') + '</select></div>' +
      '<div class="bar" data-for="site" hidden><select name="site_id">' + o.sites.map(function (s) { return opt(s.site_id, s.name); }).join('') + '</select>' +
      '<select name="origin">' + Object.keys(ORIGINS).map(function (k) { return opt(k, ORIGINS[k]); }).join('') + '</select>' +
      '<select name="value"></select></div>' +
      '<div class="bar" data-for="list" hidden><select name="list_id">' + o.lists.map(function (l) { return opt(l.list_id, l.list_id + ' (' + l.count + ')'); }).join('') + '</select></div>' +
      '<div class="bar"><button type="button" class="btn" id="catpreview">Önizle</button><button class="btn primary">Ekle</button></div>' +
      '<div class="catprev" id="prev-new" hidden></div></form>';
  }

  function fillValues(f) {
    var site = (O.sites || []).filter(function (s) { return s.site_id === f.elements.site_id.value; })[0];
    var vals = site ? site[f.elements.origin.value] || [] : [];
    f.elements.value.innerHTML = vals.map(function (v) { return opt(v.value, v.value + ' (' + v.count + ')'); }).join('');
  }
  function syncForm(f) {
    var kind = f.elements.kind.value;
    [].forEach.call(f.querySelectorAll('[data-for]'), function (d) { d.hidden = d.getAttribute('data-for') !== kind; });
    if (kind === 'site') fillValues(f);
  }
  function sourceOf(f) {
    var e = f.elements, kind = e.kind.value, s = {kind: kind};
    if (e.type.value) s.type = e.type.value;
    if (kind === 'genre') s.genre = e.genre.value;
    else if (kind === 'site') { s.site_id = e.site_id.value; s.origin = e.origin.value; s.value = e.value.value; }
    else if (kind === 'list') s.list_id = e.list_id.value;
    return s;
  }

  function prevHtml(p) {
    var h = '<b>' + p.count + '</b> başlık' + (p.shown !== undefined && p.shown !== p.count ? ' (sınırla ' + p.shown + ')' : '') +
      (p.hidden_reason ? ' · <span class="pill warn">ana ekranda yok: ' + A.esc(p.hidden_reason) + '</span>' : '');
    return h + '<div class="catposters">' + (p.items || []).map(function (it) {
      return '<figure><img loading="lazy" src="' + A.esc(it.poster_url) + '" alt=""><figcaption>' + A.esc(it.title) + '</figcaption></figure>';
    }).join('') + '</div>';
  }
  function showPrev(box, p) { box.hidden = false; box.innerHTML = prevHtml(p); }

  function render() {
    if (!view) return;
    var h = '<div class="shead"><h2>Kategoriler</h2><span class="muted">Ana ekran satırları istemcide bu sırayla görünür; boş kategori gösterilmez.</span></div>';
    if (!C) h += '<p class="empty">Yükleniyor…</p>';
    else h += '<ol class="catlist" id="catlist">' + C.map(function (c, i) { return rowHtml(c, i, C.length); }).join('') + '</ol>';
    view.innerHTML = h + formHtml();
    var f = view.querySelector('#catform');
    if (f) syncForm(f);
  }

  function load() {
    return A.api(B).then(function (d) { C = d.categories || []; O = d.options || O; render(); })
      .catch(function (e) { if (view) view.innerHTML = '<p class="err">' + A.esc(e.message) + '</p>'; });
  }
  function patch(slug, body) { return put(B + '/' + encodeURIComponent(slug), body).then(load).catch(fail); }
  function saveOrder(next, prev) {
    C = next.map(find).filter(Boolean); render();
    return put(B + '-order', {order: next}).then(function (d) { C = d.categories || C; render(); })
      .catch(function (e) { C = prev.map(find).filter(Boolean); return fail(e); });
  }
  function move(slug, dir) {
    var o = order(), i = o.indexOf(slug), j = i + dir;
    if (i < 0 || j < 0 || j >= o.length) return;
    var prev = o.slice(); o[i] = o[j]; o[j] = slug; saveOrder(o, prev);
  }
  function editTitle(btn) {
    var c = find(btn.getAttribute('data-edit')); if (!c) return;
    var v = window.prompt('Satırın adı', c.title);
    if (v && v.trim() && v.trim() !== c.title) patch(c.slug, {title: v.trim()});
  }

  function bind(el) {
    el.addEventListener('click', function (ev) {
      var t = ev.target.closest('button'); if (!t) return;
      if (t.hasAttribute('data-up')) move(t.getAttribute('data-up'), -1);
      else if (t.hasAttribute('data-down')) move(t.getAttribute('data-down'), 1);
      else if (t.hasAttribute('data-edit')) editTitle(t);
      else if (t.hasAttribute('data-prev')) {
        var slug = t.getAttribute('data-prev'), box = el.querySelector('#prev-' + slug);
        if (!box.hidden) { box.hidden = true; return; }
        A.api(B + '/' + encodeURIComponent(slug) + '/preview').then(function (p) { showPrev(box, p); }).catch(fail);
      } else if (t.hasAttribute('data-del')) {
        var c = find(t.getAttribute('data-del'));
        if (c && window.confirm('“' + c.title + '” kategorisi silinsin mi?'))
          A.del(B + '/' + encodeURIComponent(c.slug)).then(load).catch(fail);
      } else if (t.id === 'catpreview') {
        var f = el.querySelector('#catform');
        A.post(B + '/preview', {source: sourceOf(f)}).then(function (p) { showPrev(el.querySelector('#prev-new'), p); })
          .catch(function (e) { A.flash(e.message); });
      }
    });
    el.addEventListener('change', function (ev) {
      var t = ev.target, f = t.form;
      if (f && f.id === 'catform') {
        if (t.name === 'kind') syncForm(f);
        else if (t.name === 'site_id' || t.name === 'origin') fillValues(f);
        return;
      }
      if (t.hasAttribute('data-on')) patch(t.getAttribute('data-on'), {enabled: t.checked});
      else if (t.hasAttribute('data-min')) patch(t.getAttribute('data-min'), {min_items: parseInt(t.value, 10)});
      else if (t.hasAttribute('data-max')) patch(t.getAttribute('data-max'), {max_items: parseInt(t.value, 10) || 0});
    });
    el.addEventListener('submit', function (ev) {
      var f = ev.target; if (f.id !== 'catform') return;
      ev.preventDefault();
      A.post(B, {title: f.elements.title.value, source: sourceOf(f)}).then(load).catch(function (e) { A.flash(e.message); });
    });
    el.addEventListener('dragstart', function (ev) {
      var h = ev.target.closest && ev.target.closest('.cathandle'); if (!h) return;
      drag = h.closest('.catrow'); dragPrev = order();
      try { ev.dataTransfer.effectAllowed = 'move'; ev.dataTransfer.setData('text/plain', drag.getAttribute('data-slug')); } catch (e) {}
      drag.classList.add('dragging');
    });
    el.addEventListener('dragover', function (ev) {
      if (!drag) return;
      var li = ev.target.closest && ev.target.closest('.catrow');
      if (!li || li === drag || li.parentNode !== drag.parentNode) return;
      ev.preventDefault();
      var r = li.getBoundingClientRect();
      li.parentNode.insertBefore(drag, ev.clientY > r.top + r.height / 2 ? li.nextSibling : li);
    });
    el.addEventListener('drop', function (ev) { if (drag) ev.preventDefault(); });
    el.addEventListener('dragend', function () {
      if (!drag) return;
      var list = el.querySelector('#catlist'), prev = dragPrev;
      drag.classList.remove('dragging'); drag = null; dragPrev = null;
      var now = [].map.call(list.querySelectorAll('.catrow'), function (li) { return li.getAttribute('data-slug'); });
      if (now.join('|') !== prev.join('|')) saveOrder(now, prev);
    });
  }

  A.screen('categories', {
    title: 'Kategoriler', menu: true,
    render: function (el) { view = el; C = null; bind(el); render(); load(); }
  });
})();
