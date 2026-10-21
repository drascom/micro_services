/* "Sorunlar" screen (T14): the one problem box (state badge, where, what, reporter, age; filters site / state), a row opens
   its timeline, evidence, linked problems, repair version, the question card (the stage card data) and its actions;
   "Motor boşlukları" below. Also decorates the site page's Harita cards with an open-problem badge (ADMIN.stageDecor).
   Routes: #/problems, #/problems/<id> (opened), #/problems/s/<site> (site filter). */
(function () {
  'use strict';
  var A = window.ADMIN;
  if (!A) return;

  var TONE = {
    waiting_user: 'bad', waiting_llm: 'warn', open: 'run', diagnosing: 'run', acting: 'run', verifying: 'run',
    resolved: 'ok', confirmed: 'ok', waiting_capability: 'heal', closed_not_public: '', exhausted: '', closed: ''
  };
  var ACTOR = {client: 'istemci', system: 'sistem', llm: 'LLM', admin: 'sen'};
  var ACT = {
    retry: 'yeniden dene', close_not_public: 'kapat: halka kapalı', close_unimportant: 'kapat: önemsiz',
    rollback: 'onarımı geri al', apply: 'onarımı uygula'
  };
  var GAP = {open: ['açık', 'heal'], shipped: ['eklendi', 'ok'], declined: ['yapılmayacak', '']};

  function style() {
    if (document.getElementById('problems-style')) return;
    var s = document.createElement('style');
    s.id = 'problems-style';
    s.textContent =
      '.prow summary{display:flex;gap:10px;align-items:center;cursor:pointer;padding:8px 4px;border-bottom:1px solid var(--surface2)}' +
      '.prow summary .what{flex:1}.prow .pbody{padding:8px 14px 16px}.prow.hot summary{background:var(--bad-bg)}' +
      '.ptl{list-style:none;padding:0;margin:0}.ptl li{padding:2px 0}.pacts button{margin-right:6px}' +
      '.pcard{border:1px solid var(--surface2);border-radius:8px;padding:8px 12px;margin:8px 0}' +
      '.pcard textarea{width:100%;min-height:60px}.pbadge{margin-left:6px;text-decoration:none}';
    document.head.appendChild(s);
  }

  function pill(state, label) {
    return '<span class="pill ' + (TONE[state] || '') + '">' + A.esc(label || state) + '</span>';
  }

  function rowHtml(p) {
    return '<details class="prow' + (p.state === 'waiting_user' ? ' hot' : '') + '" data-id="' + p.id + '"><summary>' +
      pill(p.state, p.state_label) + '<span class="mono">#' + p.id + '</span><b>' + A.esc(p.where) + '</b>' +
      '<span class="what">' + A.esc(p.what) + '</span><span class="muted">' + A.esc(p.reporter) + '</span>' +
      '<span class="muted">' + A.esc(A.when(p.opened_at)) + '</span></summary><div class="pbody">yükleniyor…</div></details>';
  }

  function evidenceHtml(ev) {
    var keys = Object.keys(ev || {}).filter(function (k) { return k !== 'repair'; });
    if (!keys.length) return '';
    return '<h3>Kanıt</h3><div class="evidence">' + keys.map(function (k) {
      var v = ev[k];
      return '<b>' + A.esc(k) + ':</b> ' + A.esc(typeof v === 'string' ? v : JSON.stringify(v, null, 1));
    }).join('\n') + '</div>';
  }

  function cardHtml(d) {
    var c = d.card;
    if (!c) return '';
    var data = A.stageDataHtml ? A.stageDataHtml(c) : A.esc(c.summary || '');
    return '<div class="pcard"><h3>Soru: ' + A.esc(c.id + ' ' + (c.title || '')) + ' <span class="muted">' + A.esc(c.summary || '') +
      '</span></h3>' + data + (d.actions.indexOf('hint') >= 0
        ? '<div class="phint"></div>' +
          '<button type="button" class="primary" data-a="hint">ipucuyla yeniden dene</button>' : '') + '</div>';
  }

  function detailHtml(d) {
    var h = '';
    h += '<p><b>Durum:</b> ' + pill(d.state, d.state_label) + ' · <b>deneme:</b> ' + (d.attempts || 0) +
      ' · <b>LLM onarım koşusu:</b> ' + (d.llm_runs || 0) + ' · <b>izleyen:</b> ' + (d.watchers || 0) +
      (d.deadline_at ? ' · <b>son tarih:</b> ' + A.esc(A.full(d.deadline_at)) : '') + '</p>';
    if (d.version) {
      h += '<p><b>Onarım sürümü:</b> <a href="#/site/' + encodeURIComponent(d.version.site_id) + '/versions">v' +
        A.esc(d.version.version) + ' (Sürümler sekmesinde fark)</a>' +
        (d.repair && d.repair.pending_apply ? ' <span class="pill warn">onay bekliyor</span>' : '') + '</p>';
    } else if (d.repair && d.repair.pending_apply) {
      h += '<p><span class="pill warn">onarım ölçümü geçti, taslak onayını bekliyor</span></p>';
    }
    if (d.gap) h += '<p><b>Motor boşluğu:</b> <span class="mono">' + A.esc(d.gap.key) + '</span> ' + A.esc(d.gap.title || '') + '</p>';
    if (d.state === 'waiting_user') h += cardHtml(d);
    var acts = (d.actions || []).filter(function (a) { return ACT[a]; });
    if (acts.length) {
      h += '<div class="pacts">' + acts.map(function (a) {
        return '<button type="button" data-a="' + a + '">' + A.esc(ACT[a]) + '</button>';
      }).join('') + '<span class="err"></span></div>';
    } else {
      h += '<div class="pacts"><span class="err"></span></div>';
    }
    h += evidenceHtml(d.evidence);
    if ((d.linked || []).length) {
      h += '<h3>Bağlı sorunlar</h3><ul class="ptl">' + d.linked.map(function (x) {
        return '<li><a href="#/problems/' + x.id + '">#' + x.id + '</a> ' + pill(x.state, x.state_label) + ' ' + A.esc(x.where) +
          ' <span class="muted">' + A.esc(x.what) + '</span></li>';
      }).join('') + '</ul>';
    }
    h += '<h3>Olaylar</h3><ul class="ptl">' + (d.events || []).slice().reverse().map(function (e) {
      return '<li><span class="muted">' + A.esc(A.full(e.at)) + '</span> <b>' + A.esc(ACTOR[e.actor] || e.actor) + '</b> ' +
        A.esc(e.kind) + (e.note ? ': ' + A.esc(e.note) : '') + '</li>';
    }).join('') + '</ul>';
    return h;
  }

  function gapsHtml(list) {
    if (!list.length) return '<p class="muted">Kayıtlı motor boşluğu yok.</p>';
    return '<table class="grid"><thead><tr><th>Anahtar</th><th>Başlık</th><th>Siteler</th><th>Bekleyen</th><th>Durum</th><th></th>' +
      '</tr></thead><tbody>' + list.map(function (g) {
        var st = GAP[g.status] || [g.status, ''];
        return '<tr><td class="mono">' + A.esc(g.key) + '</td><td>' + A.esc(g.title || '') + '</td><td>' +
          A.esc((g.sites || []).join(', ')) + '</td><td class="num">' + (g.waiting || 0) + '</td><td><span class="pill ' + st[1] +
          '">' + A.esc(st[0]) + '</span></td><td>' + (g.status === 'open'
            ? '<button type="button" data-gap="' + g.id + '" data-s="shipped">eklendi</button>' +
              '<button type="button" data-gap="' + g.id + '" data-s="declined">yapılmayacak</button>'
            : '<button type="button" data-gap="' + g.id + '" data-s="open">yeniden aç</button>') + '</td></tr>';
      }).join('') + '</tbody></table>';
  }

  function render(view, args) {
    style();
    var site = args[0] === 's' ? (args[1] || '') : '', openId = args[0] && args[0] !== 's' ? args[0] : '';
    var state = 'active';
    view.innerHTML = '<div class="bar"><h2>Sorunlar</h2><select id="pst" aria-label="durum">' +
      '<option value="active">açık olanlar</option><option value="waiting_user">seni bekliyor</option>' +
      '<option value="waiting_llm">LLM bekliyor</option><option value="waiting_capability">motor desteği bekliyor</option>' +
      '<option value="">tümü</option><option value="closed">kapananlar</option></select>' +
      '<select id="psite" aria-label="site"><option value="">tüm siteler</option></select><span class="err" id="perr"></span></div>' +
      '<div id="plist"><p class="muted">yükleniyor…</p></div><h2>Motor boşlukları</h2><div id="pgaps"></div>';
    var list = view.querySelector('#plist'), err = view.querySelector('#perr');
    var selState = view.querySelector('#pst'), selSite = view.querySelector('#psite');
    var key = null, opened = {};
    if (openId) opened[openId] = true;

    function loadDetail(el) {
      var id = el.getAttribute('data-id'), body = el.querySelector('.pbody');
      return A.api('/api/control/problems/' + id).then(function (d) {
        var ta = body.querySelector('textarea'), keep = ta ? ta.value : '';
        body.innerHTML = detailHtml(d);
        var slot = body.querySelector('.phint');
        if (slot) {   // the hint box (built as an element: the only free-text field of the card)
          var nta = document.createElement('textarea');
          nta.maxLength = 2000;
          nta.placeholder = 'İpucu yaz; onarım bu ipucuyla yeniden çalışır';
          nta.value = keep;
          slot.appendChild(nta);
        }
      }).catch(function (e) { body.innerHTML = '<p class="err">' + A.esc(e.message) + '</p>'; });
    }

    function load() {
      var q = '?state=' + encodeURIComponent(state) + '&site=' + encodeURIComponent(site);
      A.api('/api/control/gaps').then(function (g) { view.querySelector('#pgaps').innerHTML = gapsHtml(g.gaps || []); })
        .catch(function () {});
      return A.api('/api/control/problems' + q).then(function (d) {
        if (selSite.options.length - 1 !== (d.sites || []).length) {
          selSite.innerHTML = '<option value="">tüm siteler</option>' + (d.sites || []).map(function (s) {
            return '<option value="' + A.esc(s) + '"' + (s === site ? ' selected' : '') + '>' + A.esc(s) + '</option>';
          }).join('');
        }
        var rows = d.problems || [], nk = JSON.stringify(rows.map(function (p) { return [p.id, p.state, p.updated_at]; }));
        if (nk === key) {
          return Promise.all(Object.keys(opened).map(function (id) {
            var el = list.querySelector('.prow[data-id="' + id + '"]');
            return el && el.open && !el.contains(document.activeElement) ? loadDetail(el) : null;
          }));
        }
        key = nk;
        list.innerHTML = rows.length ? rows.map(rowHtml).join('') : '<p class="empty">Bu süzgeçte sorun yok.</p>';
        Object.keys(opened).forEach(function (id) {
          var el = list.querySelector('.prow[data-id="' + id + '"]');
          if (el) { el.open = true; loadDetail(el); }
        });
      }).catch(function (e) { err.textContent = e.message; });
    }

    selState.onchange = function () { state = selState.value; key = null; load(); };
    selSite.onchange = function () { site = selSite.value; key = null; load(); };

    list.addEventListener('toggle', function (ev) {
      var el = ev.target;
      if (!el.classList || !el.classList.contains('prow')) return;
      var id = el.getAttribute('data-id');
      if (el.open) { opened[id] = true; loadDetail(el); } else { delete opened[id]; }
    }, true);

    list.addEventListener('click', function (ev) {
      var b = ev.target.closest('button[data-a]');
      if (!b) return;
      var row = b.closest('.prow'), id = row.getAttribute('data-id'), a = b.getAttribute('data-a');
      var perr = row.querySelector('.pacts .err') || err, base = '/api/control/problems/' + id + '/', req;
      if (a === 'retry') req = A.post(base + 'retry');
      else if (a === 'close_not_public') {
        if (!window.confirm('Sorun "halka kapalı" olarak kapatılsın mı? (sayfaları istemciden gizlenir)')) return;
        req = A.post(base + 'close', {reason: 'not_public'});
      } else if (a === 'close_unimportant') req = A.post(base + 'close', {reason: 'unimportant'});
      else if (a === 'rollback') {
        if (!window.confirm('Onarımın değiştirdiği sayfa türü önceki içeriğine dönsün mü? (yalnız o bölüm, yeni sürüm olarak)')) return;
        req = A.post(base + 'rollback');
      } else if (a === 'apply') req = A.post(base + 'apply');
      else if (a === 'hint') {
        var ta = row.querySelector('textarea'), text = ta ? ta.value.trim() : '';
        if (!text) { perr.textContent = 'İpucu boş.'; return; }
        req = A.post(base + 'hint', {text: text}).then(function () { ta.value = ''; });
      } else return;
      b.disabled = true;
      perr.textContent = '';
      req.then(function () { key = null; return load(); }).catch(function (e) { perr.textContent = e.message; })
        .then(function () { b.disabled = false; A.refreshStrip(); });
    });

    view.querySelector('#pgaps').addEventListener('click', function (ev) {
      var b = ev.target.closest('button[data-gap]');
      if (!b) return;
      var s = b.getAttribute('data-s');
      if (s === 'declined' && !window.confirm('Bu boşluk "yapılmayacak" olarak işaretlensin mi? Bekleyen sorunları kapanır.')) return;
      A.post('/api/control/gaps/' + b.getAttribute('data-gap'), {status: s}).then(function () { key = null; return load(); })
        .catch(function (e) { err.textContent = e.message; });
    });

    selState.value = state;
    A.poll(load, 8000);
  }

  A.screen('problems', {title: 'Sorunlar', menu: true, render: render});

  /* Site page Harita cards: "N açık sorun" badge linking to the filtered list */
  A.stageDecor = function (siteId, cards) {
    A.api('/api/control/problems/badges?site=' + encodeURIComponent(siteId)).then(function (d) {
      var stages = d.stages || {};
      Object.keys(cards).forEach(function (id) {
        var sum = cards[id].el.querySelector('summary'), old = sum.querySelector('.pbadge'), n = stages[id] || 0;
        var text = n ? n + ' açık sorun' : '';
        if (old && old.getAttribute('data-n') === String(n)) return;
        if (old) old.remove();
        if (!n) return;
        var a = A.el('<a class="pill bad pbadge" data-n="' + n + '" href="#/problems/s/' + encodeURIComponent(siteId) + '">' +
          A.esc(text) + '</a>');
        a.addEventListener('click', function (ev) { ev.stopPropagation(); });
        sum.appendChild(a);
      });
    }).catch(function () {});
  };
})();
