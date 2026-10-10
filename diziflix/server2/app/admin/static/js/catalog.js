/* Katalog (T16): #/catalog[/~<filters>] list (grid / table), #/catalog/<id> detail (meta, records, field provenance,
   season tabs -> episodes -> sources; movie -> sources), resolve trace modal, in-panel player (catalog_player.js),
   "Kaynak ekle", unlist / delete (also bulk), site page tab "Katalog", "[bu örneği çöz]" on A5/A6 stage-card samples.
   Images go through the server's /img proxy (no request to a foreign host from this page). */
(function () {
  'use strict';
  var A = window.ADMIN;
  var P = '/api/control/catalog';
  var VIEW_KEY = 'dz_catalog_view';
  var STATUS = {visible: ['görünür', 'ok'], no_source: ['kaynak bekliyor', 'warn'], not_public: ['halka kapalı', 'bad'],
    broken: ['kaynakları bozuk', 'bad'], disabled: ['devre dışı', ''], unlisted: ['liste dışı', '']};
  var SRC = {healthy: 'ok', unknown: '', suspect: 'warn', broken: 'bad', blocked: 'bad', disabled: ''};
  var STAGE = {page: 'sayfa', discover: 'aday bulma', provider: 'sağlayıcı', stream: 'akış'};
  var ORIGIN = {section: 'bölüm', listing: 'liste', genre: 'tür', type: 'tür sayfası'};

  var CSS =
    '.cfil{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:12px}.cfil input[type=search]{flex:1 1 200px}' +
    '.cgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}' +
    '.ccard{background:var(--surface);border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:12.5px;position:relative}' +
    '.ccard img{width:100%;aspect-ratio:2/3;object-fit:cover;display:block;background:var(--surface2)}' +
    '.ccard .cb{padding:6px 8px}.ccard .cb a{font-weight:600;font-size:13.5px;text-decoration:none;color:var(--text)}' +
    '.ccard input[type=checkbox]{position:absolute;top:6px;left:6px;transform:scale(1.3)}' +
    '.cwhy{color:var(--muted);font-size:12px}.cpager{display:flex;gap:8px;align-items:center;justify-content:center;margin:14px 0}' +
    '.chead{display:grid;grid-template-columns:160px 1fr;gap:16px;margin-bottom:12px}.chead img{width:160px;border-radius:8px}' +
    '.chero{background-size:cover;background-position:center;border-radius:12px;padding:16px;margin-bottom:12px}' +
    '.kv{display:grid;grid-template-columns:160px 1fr;gap:3px 12px;font-size:13px}.kv dt{color:var(--muted)}.kv dd{margin:0;overflow-wrap:anywhere}' +
    '.srcs{width:100%;border-collapse:collapse;font-size:12.5px;margin:6px 0}.srcs td{padding:5px 6px;border-top:1px solid var(--line);vertical-align:top}' +
    '.srcs td.u{overflow-wrap:anywhere;max-width:340px}.srcs td.acts{white-space:nowrap}.srcs button{padding:3px 8px;font-size:12px}' +
    'tr.ep{cursor:pointer}tr.ep:hover td{background:var(--surface2)}tr.epsrc>td{background:var(--surface2)}' +
    '.pmodal{position:fixed;inset:0;background:rgba(0,0,0,.6);display:flex;align-items:flex-start;justify-content:center;z-index:50;overflow:auto;padding:24px 12px}' +
    '.pbox{background:var(--surface);border:1px solid var(--line);border-radius:12px;max-width:980px;width:100%;padding:14px}' +
    '.pbox .phead{display:flex;gap:10px;align-items:center;margin-bottom:8px}.pbox .phead b{flex:1}' +
    '.trace .st{padding:6px 8px;border-left:3px solid var(--ok);margin:4px 0;background:var(--surface2);border-radius:4px;font-size:13px}' +
    '.trace .st.no{border-left-color:var(--bad);background:var(--bad-bg)}.trace pre{white-space:pre-wrap;font:12px var(--mono);margin:4px 0}';

  function style() {
    if (document.getElementById('ccss')) return;
    var s = document.createElement('style');
    s.id = 'ccss'; s.textContent = CSS;
    document.head.appendChild(s);
  }

  function pill(kind) { var s = STATUS[kind] || [kind, '']; return '<span class="pill ' + s[1] + '">' + A.esc(s[0]) + '</span>'; }
  function srcPill(s) { return '<span class="pill ' + (SRC[s.status] || '') + '">' + A.esc(s.status_text || s.status) + '</span>'; }
  function img(id, kind, w, h) { return '/img/' + encodeURIComponent(id) + '/' + kind + '?w=' + w + '&h=' + h; }

  function parseFilters(arg) {
    var f = {site: '', type: '', status: '', q: '', sort: 'added', page: '1'};
    (arg || '').replace(/^~/, '').split('&').forEach(function (kv) {
      var i = kv.indexOf('=');
      if (i > 0) f[kv.slice(0, i)] = decodeURIComponent(kv.slice(i + 1));
    });
    return f;
  }
  function filterHash(f) {
    var parts = Object.keys(f).filter(function (k) { return f[k] && !(k === 'sort' && f[k] === 'added') && !(k === 'page' && f[k] === '1'); })
      .map(function (k) { return k + '=' + encodeURIComponent(f[k]); });
    return '#/catalog' + (parts.length ? '/' + encodeURIComponent('~' + parts.join('&')) : '');
  }
  A.catalogLink = function (f) { return filterHash(f || {}); };

  function render(view, args) {
    style();
    if (args[0] && args[0].charAt(0) !== '~') return detail(view, args[0]);
    list(view, parseFilters(args[0]), null);
  }

  /* ------------------------------------------------------------------ list */

  function list(view, f, fixedSite) {
    var mode = 'grid';
    try { mode = localStorage.getItem(VIEW_KEY) || 'grid'; } catch (e) { /* ignore */ }
    view.innerHTML = '<div class="cfil">' +
      (fixedSite ? '' : '<select data-k="site" aria-label="site"><option value="">tüm siteler</option></select>') +
      '<select data-k="type" aria-label="tür"><option value="">film + dizi</option><option value="movie">film</option><option value="series">dizi</option></select>' +
      '<select data-k="status" aria-label="durum"></select>' +
      '<input type="search" data-k="q" placeholder="ad ara" aria-label="ara">' +
      '<select data-k="sort" aria-label="sıralama"><option value="added">eklenme</option><option value="name">ad</option></select>' +
      '<button type="button" data-view>' + (mode === 'grid' ? 'tablo' : 'ızgara') + '</button>' +
      '<span class="grow"></span><span data-selinfo class="muted"></span>' +
      '<button type="button" data-bulk="unlist">seçilenleri liste dışı bırak</button>' +
      '<button type="button" class="danger" data-bulk="delete">seçilenleri temizle (sil)</button></div>' +
      '<div data-out><p class="muted">yükleniyor…</p></div>';
    var out = view.querySelector('[data-out]');
    if (fixedSite) f.site = fixedSite;
    ['type', 'sort', 'q'].concat(fixedSite ? [] : ['site']).forEach(function (k) {
      var el = view.querySelector('[data-k="' + k + '"]');
      if (el && k !== 'site') el.value = f[k] || el.value;
    });
    var selected = {};
    var nav = function (nf) {
      if (fixedSite) { f = nf; load(); } else A.go(filterHash(nf));
    };
    view.querySelector('.cfil').addEventListener('change', function (ev) {
      var k = ev.target.getAttribute('data-k');
      if (!k) return;
      var nf = Object.assign({}, f); nf[k] = ev.target.value; nf.page = '1';
      nav(nf);
    });
    view.querySelector('[data-view]').onclick = function () {
      mode = mode === 'grid' ? 'table' : 'grid';
      try { localStorage.setItem(VIEW_KEY, mode); } catch (e) { /* ignore */ }
      this.textContent = mode === 'grid' ? 'tablo' : 'ızgara';
      load();
    };
    view.querySelectorAll('[data-bulk]').forEach(function (b) {
      b.onclick = function () {
        var ids = Object.keys(selected).filter(function (k) { return selected[k]; });
        var act = b.getAttribute('data-bulk');
        if (!ids.length) { A.flash('Önce başlık seç.'); return; }
        var msg = act === 'delete' ? ids.length + ' başlık katalogdan silinsin mi? (kullanıcı verisi kalır; tarama yeniden bulabilir)'
          : ids.length + ' başlık liste dışı bırakılsın mı? (kayıt kalır, istemci görmez, yeniden aktarım geri getirmez)';
        if (!window.confirm(msg)) return;
        A.post(P + '/bulk', {ids: ids, action: act}).then(function () { selected = {}; load(); })
          .catch(function (e) { A.flash(e.message); });
      };
    });

    function load() {
      var qs = ['site', 'type', 'status', 'q', 'sort', 'page'].map(function (k) { return k + '=' + encodeURIComponent(f[k] || ''); }).join('&');
      return A.api(P + '?' + qs).then(function (d) {
        var st = view.querySelector('[data-k="status"]'), c = d.counts || {};
        st.innerHTML = [['', 'liste dışı hariç tümü'], ['visible', 'görünür'], ['unplayable', 'Oynatılamayanlar'],
          ['no_source', 'kaynak bekliyor'], ['not_public', 'halka kapalı'], ['broken', 'kaynakları bozuk'],
          ['disabled', 'devre dışı'], ['unlisted', 'liste dışı'], ['all', 'hepsi (liste dışı dahil)']].map(function (o) {
          return '<option value="' + o[0] + '"' + (o[0] === (f.status || '') ? ' selected' : '') + '>' + A.esc(o[1]) +
            (c[o[0]] != null ? ' (' + c[o[0]] + ')' : '') + '</option>';
        }).join('');
        var ss = view.querySelector('[data-k="site"]');
        if (ss) {
          ss.innerHTML = '<option value="">tüm siteler</option>' + (d.sites || []).map(function (s) {
            return '<option' + (s === f.site ? ' selected' : '') + '>' + A.esc(s) + '</option>';
          }).join('');
        }
        var items = d.items || [];
        if (!items.length) { out.innerHTML = '<p class="empty">Bu süzgeçle başlık yok.</p>'; return; }
        var html = mode === 'grid' ? '<div class="cgrid">' + items.map(card).join('') + '</div>'
          : '<table class="grid"><thead><tr><th></th><th>Ad</th><th>Yıl</th><th>Tür</th><th>Site</th><th>Durum</th>' +
            '<th>Kaynaklar</th><th>Geldiği yer</th><th>TMDB</th></tr></thead><tbody>' + items.map(row).join('') + '</tbody></table>';
        html += '<div class="cpager">' + (d.page > 1 ? '<button type="button" data-pg="' + (d.page - 1) + '">← önceki</button>' : '') +
          '<span class="muted">' + d.page + ' / ' + d.pages + ' · ' + d.total + ' başlık</span>' +
          (d.page < d.pages ? '<button type="button" data-pg="' + (d.page + 1) + '">sonraki →</button>' : '') + '</div>';
        out.innerHTML = html;
        out.querySelectorAll('[data-pg]').forEach(function (b) {
          b.onclick = function () { var nf = Object.assign({}, f); nf.page = b.getAttribute('data-pg'); nav(nf); };
        });
        out.querySelectorAll('input[data-sel]').forEach(function (cb) {
          cb.checked = !!selected[cb.getAttribute('data-sel')];
          cb.onchange = function () { selected[cb.getAttribute('data-sel')] = cb.checked; info(); };
        });
        info();
      }).catch(function (e) { out.innerHTML = '<p class="err">' + A.esc(e.message) + '</p>'; });
    }
    function info() {
      var n = Object.keys(selected).filter(function (k) { return selected[k]; }).length;
      view.querySelector('[data-selinfo]').textContent = n ? n + ' seçili' : '';
    }
    load();
  }

  function originText(o) { return (ORIGIN[o.kind] || o.kind) + ': ' + o.value; }
  function why(it) { return it.status === 'visible' ? '' : '<div class="cwhy">' + A.esc(it.reason || '') + '</div>'; }

  function card(it) {
    var link = '#/catalog/' + encodeURIComponent(it.id);
    return '<div class="ccard"><input type="checkbox" data-sel="' + A.esc(it.id) + '" aria-label="seç">' +
      '<a href="' + link + '"><img loading="lazy" alt="" src="' + img(it.id, 'portrait', 300, 450) + '"></a>' +
      '<div class="cb"><a href="' + link + '">' + A.esc(it.title) + '</a><div class="muted">' + A.esc(it.year || '') + ' · ' +
      (it.type === 'series' ? 'dizi' : 'film') + ' · ' + A.esc(it.sites.join(', ')) + (it.tmdb_matched ? ' · TMDB' : '') + '</div>' +
      pill(it.status) + why(it) + '<div class="muted">' + A.esc(it.summary.text) + '</div>' +
      (it.origins.length ? '<div class="cwhy">' + A.esc(it.origins.slice(0, 2).map(originText).join(' · ')) + '</div>' : '') +
      '</div></div>';
  }

  function row(it) {
    return '<tr><td><input type="checkbox" data-sel="' + A.esc(it.id) + '" aria-label="seç"></td>' +
      '<td><a href="#/catalog/' + encodeURIComponent(it.id) + '">' + A.esc(it.title) + '</a></td><td>' + A.esc(it.year || '') + '</td>' +
      '<td>' + (it.type === 'series' ? 'dizi' : 'film') + '</td><td>' + A.esc(it.sites.join(', ')) + '</td>' +
      '<td>' + pill(it.status) + why(it) + '</td><td>' + A.esc(it.summary.text) + '</td>' +
      '<td class="cwhy">' + A.esc(it.origins.slice(0, 3).map(originText).join(' · ')) + '</td>' +
      '<td>' + (it.tmdb_matched ? '✓' : '-') + '</td></tr>';
  }

  /* ------------------------------------------------------------------ detail */

  function detail(view, cid) {
    view.innerHTML = '<p class="muted">yükleniyor…</p>';
    var state = {season: null, open: {}};
    function load() {
      return A.api(P + '/' + encodeURIComponent(cid)).then(function (d) { draw(view, d, state, load); })
        .catch(function (e) { view.innerHTML = '<p class="err">' + A.esc(e.message) + '</p><a class="btn" href="#/catalog">← Katalog</a>'; });
    }
    load();
  }

  /* T27: poll the title crawl job; when it ends the detail (season / episode tree) is reloaded with its result note */
  function watchCrawl(cid, state, reload) {
    var mine = A.gen(), tries = 0;
    function check() {
      if (!A.alive(mine)) return;
      A.api(P + '/' + encodeURIComponent(cid) + '/crawl').then(function (j) {
        if (!A.alive(mine)) return;
        if (j.state === 'done' || j.state === 'failed' || ++tries > 150) {
          var r = j.result || {};
          state.crawling = false;
          state.crawlNote = j.state === 'done' ? {ok: !!r.ok, text: r.text || 'bitti'}
            : {ok: false, text: j.error || (tries > 150 ? 'yanıt yok' : 'başarısız')};
          reload();
          return;
        }
        setTimeout(check, 2000);
      }).catch(function () { if (A.alive(mine)) setTimeout(check, 4000); });
    }
    setTimeout(check, 1000);
  }

  function sourcesTable(list, d) {
    if (!list.length) return '<p class="muted">Kaynak yok.</p>';
    return '<table class="srcs"><tbody>' + list.map(function (s) {
      var dg = s.diagnosis || {};
      var alt = s.alt_index != null ? '<div class="muted">alternatif ' + (s.alt_index + 1) + ': ' +
        A.esc((s.alt_label || '-') + (s.alt_host ? ' · ' + s.alt_host : '')) + '</div>' : '';
      return '<tr><td>' + A.esc(s.admin ? 'admin (elle)' : s.site) + '<div class="muted">' + A.esc(s.resolver) + '</div>' + alt + '</td>' +
        '<td class="u"><a href="' + A.esc(s.url) + '" target="_blank" rel="noopener noreferrer">' + A.esc(s.url) + '</a></td>' +
        '<td>' + srcPill(s) + (s.failures ? ' <span class="muted">' + s.failures + ' hata</span>' : '') + '</td>' +
        '<td class="cwhy">' + (s.last_error ? 'son hata: ' + A.esc(s.last_error) + '<br>' : '') +
        'son kontrol: ' + A.esc(A.when(s.last_checked_at)) +
        (s.candidate_url ? '<br>aday: ' + A.esc(s.candidate_url) + ' (' + A.esc(A.when(s.candidate_at)) + ')' : '') +
        (dg.code ? '<br>tanı: ' + A.esc(dg.code) + (dg.note ? ' · ' + A.esc(dg.note) : '') + (dg.http ? ' · HTTP ' + dg.http : '') : '') +
        (dg.proxied || dg.proxy_required ? '<br>vekil: ' + A.esc(dg.proxy_reason || (dg.proxy_required ? 'öğrenildi' : 'evet')) : '') + '</td>' +
        '<td class="acts"><button type="button" data-rs="' + A.esc(s.id) + '">çöz</button>' +
        '<button type="button" data-play="' + A.esc(s.id) + '">oynat</button>' +
        '<button type="button" data-rsf="' + A.esc(s.id) + '">yeniden çöz (önbelleksiz)</button>' +
        (s.status === 'disabled' ? '<button type="button" data-en="' + A.esc(s.id) + '">etkinleştir</button>'
          : '<button type="button" data-dis="' + A.esc(s.id) + '">devre dışı bırak</button>') + '</td></tr>';
    }).join('') + '</tbody></table>';
  }

  function draw(view, d, state, reload) {
    var kv = function (k, v) { return v == null || v === '' ? '' : '<dt>' + A.esc(k) + '</dt><dd>' + v + '</dd>'; };
    var prov = Object.keys(d.field_provenance || {}).map(function (k) { return k + ' ← ' + d.field_provenance[k]; }).join(', ');
    var html = '<div class="shead"><h2>' + A.esc(d.title) + '</h2>' + pill(d.status) +
      '<span class="muted grow">' + A.esc(d.reason || '') + '</span><a class="btn" href="#/catalog">← Katalog</a></div>' +
      '<div class="bar"><button type="button" data-tmdb>TMDB yenile</button>' +
      '<button type="button" data-crawl' + (state.crawling ? ' disabled' : '') + ' title="Başlık sayfası → sezon/bölüm → kaynaklar, kota dışı">' +
      (state.crawling ? 'taranıyor…' : 'bu başlığı şimdi tara') + '</button>' +
      (d.unlisted_at ? '<button type="button" data-relist>listeye geri al</button>' : '<button type="button" data-unlist>liste dışı bırak</button>') +
      '<button type="button" class="danger" data-del>sil</button><button type="button" data-add>kaynak ekle</button>' +
      '<span class="err" data-err></span></div>' +
      (state.crawlNote ? '<p class="' + (state.crawlNote.ok ? 'muted' : 'err') + '">Tarama: ' + A.esc(state.crawlNote.text) + '</p>' : '') +
      '<div class="chead"><img alt="" src="' + img(d.id, 'portrait', 300, 450) + '"><dl class="kv">' +
      kv('Yıl', A.esc(d.year || '')) + kv('Tür', d.type === 'series' ? 'dizi' : 'film') + kv('Türler', A.esc((d.genres || []).join(', '))) +
      kv('Özet', A.esc(d.overview)) + kv('Kanonik kimlik', '<span class="mono">' + A.esc(d.id) + '</span>') +
      kv('TMDB', d.tmdb_id ? A.esc(d.tmdb_id) + (d.imdb_id ? ' · ' + A.esc(d.imdb_id) : '') : A.esc(d.tmdb_status || 'eşleşmedi')) +
      kv('Kaynaklar', A.esc(d.summary.text)) + kv('Alanların kaynağı', A.esc(prov)) +
      kv('Arka plan', d.backdrop_url ? '<a href="' + img(d.id, 'backdrop', 1280, 720) + '" target="_blank">görüntüle</a>' : '') +
      '</dl></div><h3>Site kayıtları</h3><table class="srcs"><tbody>' + d.records.map(function (r) {
        return '<tr><td>' + A.esc(r.site) + '</td><td class="u"><a href="' + A.esc(r.url) + '" target="_blank" rel="noopener noreferrer">' +
          A.esc(r.url) + '</a></td><td class="cwhy">' + A.esc(r.origins.map(originText).join(' · ')) + '</td>' +
          '<td class="muted">' + A.esc(A.when(r.fetched_at)) + '</td><td class="acts"><button type="button" class="danger" data-delsite="' +
          A.esc(r.site) + '">bu sitenin kaydını kaldır</button></td></tr>';
      }).join('') + '</tbody></table>';
    if (d.type === 'series') {
      var seasons = d.seasons || [];
      if (state.season == null && seasons.length) state.season = seasons[0].season;
      html += '<h3>Sezonlar</h3>' + (seasons.length ? '<nav class="tabs">' + seasons.map(function (s) {
        return '<a href="javascript:void 0" data-season="' + s.season + '"' + (s.season === state.season ? ' aria-selected="true"' : '') + '>' +
          s.season + '. Sezon</a>';
      }).join('') + '</nav>' : '<p class="muted">Bölüm yok.</p>');
      var cur = seasons.filter(function (s) { return s.season === state.season; })[0];
      if (cur) {
        html += '<table class="grid"><thead><tr><th>No</th><th>Ad</th><th>Yayın</th><th>Kaynak</th><th>Sağlık</th></tr></thead><tbody>' +
          cur.episodes.map(function (e) {
            var open = state.open[e.id];
            return '<tr class="ep" data-ep="' + A.esc(e.id) + '"><td class="num">' + e.episode + '</td><td>' + A.esc(e.title) + '</td>' +
              '<td>' + A.esc(e.air_date || '') + '</td><td class="num">' + e.source_count + '</td><td>' + pill(e.health.status) + '</td></tr>' +
              (open ? '<tr class="epsrc"><td colspan="5">' + sourcesTable(e.sources, d) +
                '<button type="button" data-addep="' + A.esc(e.id) + '">bu bölüme kaynak ekle</button></td></tr>' : '');
          }).join('') + '</tbody></table>';
      }
    } else {
      html += '<h3>Kaynaklar</h3>' + sourcesTable(d.sources || [], d);
    }
    if ((d.trailers || []).length) html += '<h3>Fragman</h3>' + sourcesTable(d.trailers, d);
    view.innerHTML = html;
    var err = view.querySelector('[data-err]');
    var fail = function (e) { err.textContent = e.message; };
    var srcRow = function (sid) {
      var all = (d.sources || []).concat(d.trailers || []);
      (d.seasons || []).forEach(function (s) { s.episodes.forEach(function (e) { all = all.concat(e.sources.map(function (x) {
        return Object.assign({episode_id: e.id, ep_title: e.season + '×' + e.episode + ' ' + e.title}, x);
      })); }); });
      return all.filter(function (x) { return x.id === sid; })[0] || {};
    };
    view.onclick = function (ev) {
      var t = ev.target, b;
      if ((b = t.closest('[data-season]'))) { state.season = +b.getAttribute('data-season'); draw(view, d, state, reload); return; }
      if ((b = t.closest('tr.ep'))) { var id = b.getAttribute('data-ep'); state.open[id] = !state.open[id]; draw(view, d, state, reload); return; }
      if ((b = t.closest('[data-rs],[data-rsf]'))) {
        var sid = b.getAttribute('data-rs') || b.getAttribute('data-rsf');
        traceModal(P + '/sources/' + encodeURIComponent(sid) + '/resolve', {force: b.hasAttribute('data-rsf')}, d.title);
        return;
      }
      if ((b = t.closest('[data-play]'))) {
        var s = srcRow(b.getAttribute('data-play'));
        A.player.open({cid: d.id, episode: s.episode_id || '', source: s.id, title: d.title + (s.ep_title ? ' · ' + s.ep_title : ''),
          canReport: d.problems_available});
        return;
      }
      var act = null;
      if ((b = t.closest('[data-dis]'))) act = A.post(P + '/sources/' + encodeURIComponent(b.getAttribute('data-dis')) + '/disable');
      else if ((b = t.closest('[data-en]'))) act = A.post(P + '/sources/' + encodeURIComponent(b.getAttribute('data-en')) + '/enable');
      else if (t.closest('[data-crawl]')) {
        if (state.crawling) return;
        state.crawling = true;
        state.crawlNote = null;
        act = A.post(P + '/' + encodeURIComponent(d.id) + '/crawl').then(function () { watchCrawl(d.id, state, reload); },
          function (e) { state.crawling = false; throw e; });
      }
      else if (t.closest('[data-tmdb]')) act = A.post(P + '/' + encodeURIComponent(d.id) + '/tmdb-refresh').then(function () { A.flash('TMDB işi sıraya kondu.'); });
      else if (t.closest('[data-unlist]')) {
        if (!window.confirm('Liste dışı bırakılsın mı? İstemci görmez, Katalog varsayılan listede göstermez, yeniden aktarım geri getirmez.')) return;
        act = A.post(P + '/' + encodeURIComponent(d.id) + '/unlist');
      } else if (t.closest('[data-relist]')) act = A.post(P + '/' + encodeURIComponent(d.id) + '/relist');
      else if (t.closest('[data-del]')) {
        if (!window.confirm('"' + d.title + '" tüm site kayıtlarıyla silinsin mi? (kullanıcı verisi kalır)')) return;
        act = A.del(P + '/' + encodeURIComponent(d.id)).then(function (r) { if (r.title_gone) { A.go('#/catalog'); return 'gone'; } });
      } else if ((b = t.closest('[data-delsite]'))) {
        var site = b.getAttribute('data-delsite');
        if (!window.confirm(site + ' kaydı kaldırılsın mı?')) return;
        act = A.del(P + '/' + encodeURIComponent(d.id) + '?site=' + encodeURIComponent(site)).then(function (r) {
          if (r.title_gone) { A.go('#/catalog'); return 'gone'; }
        });
      } else if (t.closest('[data-add]') || (b = t.closest('[data-addep]'))) {
        var ep = b && b.getAttribute('data-addep') || '';
        if (d.type === 'series' && !ep) { A.flash('Dizi için bir bölüm satırını açıp "bu bölüme kaynak ekle"yi kullan.'); return; }
        var url = window.prompt('Başka bir açık kaynaktan adres (ör. YouTube bağlantısı ya da .m3u8/.mp4):');
        if (!url) return;
        act = A.post(P + '/' + encodeURIComponent(d.id) + '/sources', {url: url.trim(), episode: ep}).then(function (r) {
          A.flash('Kaynak eklendi (' + r.resolver + ', ' + r.provider + ').' + (r.expires ? ' Not: bu bağlantının süresi dolabilir.' : ''));
        });
      }
      if (!act) return;
      err.textContent = '';
      act.then(function (r) { if (r !== 'gone') reload(); }).catch(fail);
    };
  }

  /* ------------------------------------------------------------------ resolve trace */

  function probeText(p) {
    if (!p) return '';
    if (p.skipped) return p.skipped;
    return (p.ok ? '✓' : '✗') + ' HTTP ' + (p.http || '-') + (p.duration_min ? ' · ' + p.duration_min + ' dk' : '') +
      (p.variants != null ? ' · ' + p.variants + ' varyant' : '') + (p.encrypted ? ' · şifreli' : '') +
      (p.ms != null ? ' · ' + p.ms + ' ms' : '') + (p.note ? ' · ' + p.note : '');
  }

  function traceModal(path, body, title) {
    style();
    var modal = A.el('<div class="pmodal" role="dialog" aria-modal="true"><div class="pbox"><div class="phead"><b></b>' +
      '<button type="button" data-x>kapat ✕</button></div><div class="trace"><p class="muted">çözülüyor… (en çok ~30 sn)</p></div></div></div>');
    modal.querySelector('.phead b').textContent = 'Çözüm izi · ' + (title || '');
    document.body.appendChild(modal);
    modal.addEventListener('click', function (ev) { if (ev.target === modal || ev.target.closest('[data-x]')) modal.remove(); });
    var out = modal.querySelector('.trace');
    A.post(path, body).then(function (t) {
      var h = '<p>' + (t.ok ? '<span class="pill ok">akış bulundu</span>' : '<span class="pill bad">kaldığı adım: ' +
        A.esc(STAGE[t.failed_stage] || t.failed_stage) + '</span>') + ' <span class="muted">' + (t.ms ? t.ms + ' ms' : '') +
        (t.from_cache ? ' · önbellekten' : '') + (t.blocked ? ' · halka kapalı' : '') + '</span></p>' +
        '<p class="cwhy mono">' + A.esc(t.url || '') + '</p>';
      h += (t.steps || []).map(function (s) {
        var x = '<div class="st' + (s.ok ? '' : ' no') + '"><b>' + A.esc(STAGE[s.step] || s.step) + '</b> · ' + A.esc(s.note || '');
        if (s.candidates && s.candidates.length) {
          x += '<pre>' + s.candidates.map(function (c) {
            return (c.ok ? '✓ ' : '✗ ') + (c.resolver_type || '?') + ' → ' + (c.host || '') + (c.provider ? ' [' + c.provider + ']' : '') +
              '\n   ' + c.url + (c.error ? '\n   hata: ' + c.error : '');
          }).map(A.esc).join('\n') + '</pre>';
        }
        if (s.providers && s.providers.length) x += '<div>çözen: ' + A.esc(s.providers.join(', ')) + '</div>';
        return x + '</div>';
      }).join('');
      if ((t.streams || []).length) {
        h += '<h3>Akışlar</h3><table class="srcs"><tbody>' + t.streams.map(function (s) {
          return '<tr><td>' + A.esc(s.type) + '</td><td>' + A.esc(s.height ? s.width + '×' + s.height : (s.quality || '')) +
            (s.bitrate ? ' · ' + Math.round(s.bitrate / 1000) + ' kb/s' : '') + '</td><td>' + A.esc(s.label || '') +
            (s.provider ? ' <span class="muted">' + A.esc(s.provider) + '</span>' : '') + '</td>' +
            '<td>' + (s.proxied ? 'vekil: ' + A.esc(s.proxy_reason || '') : 'doğrudan') + '</td><td>' + A.esc(probeText(s.probe)) + '</td>' +
            '<td class="u mono">' + A.esc(s.url) + '</td></tr>';
        }).join('') + '</tbody></table>';
      }
      h += '<h3>Altyazılar</h3>' + ((t.subtitles || []).length ? '<p>' + t.subtitles.map(function (s) {
        return A.esc((s.lang || '?') + ' · ' + (s.label || '') + ' · ' + (s.format || ''));
      }).join('<br>') + '</p>' : '<p class="muted">yok</p>');
      h += '<h3>Ses izleri</h3>' + ((t.audio || []).length ? '<p>' + t.audio.map(function (a) {
        return A.esc((a.lang || '?') + ' · ' + (a.label || '') + (a.default ? ' (varsayılan)' : ''));
      }).join('<br>') + '</p>' : '<p class="muted">bilgi yok</p>');
      h += '<p class="muted">Bu inceleme kaynak sağlığını değiştirmez, sorun kaydı açmaz.</p>';
      out.innerHTML = h;
    }).catch(function (e) { out.innerHTML = '<p class="err">' + A.esc(e.message) + '</p>'; });
  }
  A.catalogTrace = traceModal;

  A.screen('catalog', {title: 'Katalog', menu: true, render: render});

  /* Site page: "Katalog" tab = this site's titles. */
  if (A.siteTab) {
    A.siteTab('catalog', 'Katalog', function (body, siteId) {
      style();
      var box = document.createElement('div');
      body.innerHTML = '<p><a href="' + filterHash({site: siteId}) + '">Katalog ekranında aç →</a> · ' +
        '<a href="' + filterHash({site: siteId, status: 'unplayable'}) + '">oynatılamayanlar</a> · ' +
        '<a href="' + filterHash({site: siteId, status: 'not_public'}) + '">halka kapalı</a></p>';
      body.appendChild(box);
      list(box, parseFilters(''), siteId);
    }, 80);
  }

  /* Stage cards A5/A6: "[bu örneği çöz]" next to each sample address (site.js renders the cards; this adds the button). */
  function sampleButtons() {
    var m = /^#\/site\/([^/]+)/.exec(location.hash || '');
    if (!m) return;
    document.querySelectorAll('.stage[data-id="A5"] ul.samples li, .stage[data-id="A6"] ul.samples li').forEach(function (li) {
      if (li.querySelector('[data-rsample]')) return;
      var a = li.querySelector('a[href]');
      if (!a) return;
      var b = document.createElement('button');
      b.type = 'button'; b.textContent = 'bu örneği çöz'; b.setAttribute('data-rsample', a.getAttribute('href'));
      b.style.cssText = 'margin-left:6px;padding:1px 7px;font-size:12px';
      li.appendChild(b);
    });
  }
  new MutationObserver(sampleButtons).observe(document.documentElement, {childList: true, subtree: true});
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest && ev.target.closest('[data-rsample]');
    if (!b) return;
    var m = /^#\/site\/([^/]+)/.exec(location.hash || '');
    if (!m) return;
    traceModal(P + '/resolve-url', {site: decodeURIComponent(m[1]), url: b.getAttribute('data-rsample')}, 'örnek');
  });
})();
