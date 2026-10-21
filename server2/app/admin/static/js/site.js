/* Site page (#/site/<id>/<tab>): header + tabs. This file owns the "Harita" (stage cards) and "Sürümler" tabs;
   later tasks add theirs with ADMIN.siteTab(id, title, render, order). */
(function () {
  'use strict';
  var A = window.ADMIN;
  var tabs = [];

  A.siteTab = function (id, title, render, order) {
    tabs = tabs.filter(function (t) { return t.id !== id; });
    tabs.push({id: id, title: title, render: render, order: order == null ? 50 : order});
    tabs.sort(function (a, b) { return a.order - b.order; });
  };

  var STATUS = {draft: ['taslak', ''], active: ['etkin', 'ok'], paused: ['duraklatıldı', 'warn']};
  var noteText = '';   /* the current site note (Harita tab; the A1 review card shows the same editor) */

  function render(view, args) {
    var siteId = args[0], tabId = args[1] || 'map';
    var tab = tabs.filter(function (t) { return t.id === tabId; })[0] || tabs[0];
    var base = '#/site/' + encodeURIComponent(siteId) + '/';
    view.innerHTML =
      '<div class="shead"><h2 id="sname">' + A.esc(siteId) + '</h2><span id="sstat"></span>' +
      '<span class="muted mono grow" id="surl"></span><a class="btn" href="#/sites">← Siteler</a></div>' +
      '<nav class="tabs">' + tabs.map(function (t) {
        return '<a href="' + base + t.id + '"' + (t === tab ? ' aria-selected="true"' : '') + '>' + A.esc(t.title) + '</a>';
      }).join('') + '</nav><div id="tabbody"></div>';
    var body = view.querySelector('#tabbody');
    A.api('/api/control/sites/' + encodeURIComponent(siteId)).then(function (d) {
      var s = d.site, st = STATUS[s.status] || [s.status, ''];
      view.querySelector('#sname').textContent = s.display_name || s.site_id;
      view.querySelector('#surl').textContent = s.base_url || '';
      view.querySelector('#sstat').innerHTML = '<span class="pill ' + st[1] + '">' + A.esc(st[0]) + '</span>';
      tab.render(body, siteId, s);
    }).catch(function (e) {
      body.innerHTML = '<p class="err">' + A.esc(e.message) + '</p>';
    });
  }

  A.screen('site', {title: 'Site', menu: false, menuAs: 'sites', render: render});

  /* ------------------------------------------------------------------ Harita: stage cards */

  var STATE = {
    pending: ['bekliyor', ''], running: ['çalışıyor', 'run pulse'], passed: ['geçti', 'ok'],
    passed_warn: ['uyarıyla geçti', 'warn'], needs_human: ['SENİ BEKLİYOR', 'bad'], review: ['SENİ BEKLİYOR', 'bad'],
    engine_gap: ['motor boşluğu', 'heal'], skipped: ['atlandı', '']
  };
  var CHECK = {
    sections_valid_count: 'geçerli bölüm sayısı', valid_count: 'geçerli satır sayısı', url_fill: 'adres doluluğu',
    title_fill: 'başlık doluluğu', poster_fill: 'afiş doluluğu', year_fill: 'yıl doluluğu', genres_fill: 'tür doluluğu',
    overview_fill: 'özet doluluğu', key_ok_ratio: 'anahtar bulunan örnek oranı', duplicate_key_ratio: 'tekrarlanan anahtar oranı',
    series_inventory_ok: 'dizi bölüm envanteri', ingest_sample_ok: 'örnek aktarım', series_have_episode_sources: 'bölümlerde kaynak',
    playable_ratio: 'oynatılabilir oranı', alt_playable_ratio: 'en iyi alternatifin oynama oranı', search_ok: 'arama çalışıyor', llm_reply_invalid: 'LLM yanıtı geçersiz',
    section_cards: 'bölüm başına kart', pagination_ok: 'sayfalama', type_ok: 'tür belirlendi', key_unique: 'anahtar benzersiz',
    section_cards_min: 'bölümdeki en az kart', target_one_cluster: 'tek hedef küme', samples_count: 'örnek sayısı',
    series_samples: 'dizi örneği sayısı', episodes_ok_ratio: 'bölümü bulunan dizi oranı', numbers_ok: 'bölüm numaraları',
    unique_ok: 'bölümler benzersiz', seasons_ok: 'sezonlar', candidate_ratio: 'oynatıcı adayı bulunan örnek oranı',
    title_link_ok: 'başlık bağlantısı', other_sites_ok: 'diğer siteler bozulmadı', gate_catches: 'kapının yakaladığı yer tutucu oranı',
    gate_false: 'kapının yanlış yakaladığı sağlam sayfa', map_valid: 'harita geçerli'
  };
  var FIELD = {poster: 'afiş', year: 'yıl', genres: 'türler', overview: 'özet', air_date: 'yayın tarihi', backdrop: 'arka plan'};
  var ROLE = {system: 'sistem', user: 'sen', llm: 'LLM'};

  function fmt(v) {
    if (v == null) return '-';
    if (typeof v === 'number') return Math.round(v * 100) / 100;
    if (typeof v === 'boolean') return v ? 'evet' : 'hayır';
    return typeof v === 'string' ? v : JSON.stringify(v);
  }

  /* T23: what the LLM made of the page (one row per block / section / menu link) + the referee's verdict */
  function llmNote(it) {
    if (!it) return '';
    return '<span class="pill ' + (it.take ? 'ok">alınır' : 'warn">alınmaz') + '</span> ' + A.esc(it.meaning || '') +
      (it.why ? ' <span class="muted">(' + A.esc(it.why) + ')</span>' : '');
  }

  function readingHtml(ev) {
    var out = '', rd = ev.llm_read || {};
    if (rd.used === false && rd.note) out += '<p class="muted llmoff">' + A.esc(rd.note) + '</p>';
    var items = ev.interpretation || [];
    if (items.length) {
      out += '<h3>LLM\'in yorumu</h3><table class="checks interp">' + items.map(function (it) {
        return '<tr><td class="mono">' + A.esc(it.ref || '') + '</td><td>' + A.esc(it.heading || it.text || it.href || '') +
          '</td><td>' + llmNote(it) + '</td></tr>';
      }).join('') + '</table>';
    }
    if (rd.used && rd.chosen) {
      var who = {llm: 'LLM\'in kuralları', discover: 'sistem tahmini', candidates: 'iki aday (sen seç)', none: 'hiçbiri'}[rd.chosen] || rd.chosen;
      out += '<p class="muted">Ölçüm hakemi: ' + A.esc(who) + (rd.why ? ' — ' + A.esc(rd.why) : '') + '</p>';
    }
    return out;
  }

  function evidenceHtml(ev) {
    if (ev == null || ev === '' || (typeof ev === 'object' && !Object.keys(ev).length)) return '';
    if (typeof ev === 'string') return '<h3>Kanıt</h3><div class="evidence">' + A.esc(ev) + '</div>';
    var lines = Object.keys(ev).filter(function (k) {
      return k !== 'review' && k !== 'review_pending' && k !== 'note_used' && k !== 'alternatives' && k !== 'investigation' &&
        k !== 'question' && k !== 'interpretation' && k !== 'llm_read';
    }).map(function (k) {
      var v = ev[k];
      var label = {searched: 'ne arandı', found: 'ne bulundu', missing: 'bulunamayan', tried: 'denenen'}[k] || k;
      return '<b>' + A.esc(label) + ':</b> ' + A.esc(typeof v === 'string' ? v : JSON.stringify(v, null, 1));
    });
    return readingHtml(ev) + '<h3>Kanıt</h3><div class="evidence">' + lines.join('\n') + '</div>';
  }

  /* A5/A6 source alternatives (all of them: backups too) */
  var ALT_STATUS = {'çalışıyor': 'ok', 'kısmen': 'warn', 'çalışmıyor': 'bad'};
  function alternativesHtml(alts) {
    if (!alts || !alts.length) return '';
    return '<h3>Kaynak alternatifleri</h3><table class="checks alts"><thead><tr><th>Etiket</th><th>Host</th><th>Tür</th>' +
      '<th>Nasıl bulundu</th><th>Örneklerde</th><th>Durum</th></tr></thead><tbody>' + alts.map(function (a) {
        var per = (a.per_sample || []).map(function (x) {
          return (x.ok ? '✓' : '✗') + ' ' + (x.step || '');
        }).join(', ');
        var how = a.how ? a.how + (a.decode && a.decode.length ? ' · ' + a.decode.join('+') : '') + (a.found_by ? ' (' + a.found_by + ')' : '')
          : (a.kind || '');
        var status = a.status || (a.resolver ? 'çözüldü' : 'çözülemedi');
        return '<tr><td>' + A.esc(a.label || '-') + '</td><td>' + A.esc(a.host || '-') + '</td><td>' + A.esc(a.kind || '') + '</td>' +
          '<td class="muted">' + A.esc(how) + '</td><td>' + A.esc(per || (a.pages != null ? a.pages + ' sayfa' : '')) +
          (a.plays != null ? ' <span class="muted">(' + A.esc(a.plays + '/' + a.samples) + ')</span>' : '') + '</td>' +
          '<td><span class="pill ' + (ALT_STATUS[status] || (a.resolver ? 'ok' : 'warn')) + '">' + A.esc(status) + '</span></td></tr>';
      }).join('') + '</tbody></table>';
  }

  /* the LLM investigation trail ("inceleme izi"), folded */
  function investigationHtml(trail) {
    if (!trail || !trail.length) return '';
    var head = '<details class="itrail"><summary>İnceleme izi (' + trail.length + ' adım)</summary>';
    return head + '<ol class="msgs">' +
      trail.map(function (t) {
        return '<li>' + A.esc((t.step != null ? t.step + (t.n ? '.' + t.n : '') + ' ' : '') + (t.action || '')) +
          ' <span class="pill ' + (t.ok ? 'ok">✓' : 'bad">✗') + '</span> <span class="muted">' + A.esc(t.summary || '') + '</span></li>';
      }).join('') + '</ol></details>';
  }

  /* the card data, one html piece per section (cardsync.SECTIONS: polling redraws only the changed ones) */
  function dataParts(st) {
    var out = {handoff: '', noteused: '', checks: '', samples: '', alternatives: '', investigation: '', evidence: '', messages: ''};
    if (st.handoff_in) out.handoff = '<p class="muted handoff">' + A.esc(st.handoff_in) + '</p>';
    if (st.llm_line) out.handoff += '<p class="muted llmline">' + A.esc(st.llm_line) + '</p>';
    var used = (st.evidence || {}).note_used;
    if (used && used.length) {
      out.noteused = '<p class="muted noteused">Site notundan kullanılan: ' + A.esc([].concat(used).join('; ')) + '</p>';
    }
    var checks = st.checks || [];
    if (checks.length) {
      out.checks = '<h3>Ölçüm</h3><table class="checks">' + checks.map(function (c) {
        return '<tr><td>' + A.esc(CHECK[c.name] || c.name) + '</td><td class="num">' + A.esc(fmt(c.value)) + '</td>' +
          '<td class="muted num">eşik ' + A.esc(fmt(c.bound)) + '</td>' +
          '<td class="' + (c.ok ? 'ok">✓' : 'no">✗') + '</td></tr>';
      }).join('') + '</table>';
    }
    var samples = st.samples || [], canEx = allowed(st, 'exclude') && st.state !== 'running';
    var editor = st.id !== 'A1' && st.sample_list && st.sample_list.rows;   /* T25: the sample editor (A2-A7) */
    if (editor) out.samples = A.cardSync.samplesHtml(st, st.state !== 'running');
    if (!editor && samples.length) {
      out.samples += '<h3>Örnek adresler</h3><ul class="samples">' + samples.map(function (s) {
        var url = typeof s === 'string' ? s : s.url;
        var mark = s.ok === true ? ' <span class="pill ok">✓</span>' : (s.ok === false ? ' <span class="pill bad">✗</span>' : '');
        return '<li><a href="' + A.esc(url) + '" target="_blank" rel="noopener noreferrer">' + A.esc(url) + '</a>' + mark +
          (s.note ? ' <span class="muted">' + A.esc(s.note) + '</span>' : '') +
          (canEx ? ' <button type="button" class="small" data-a="exclude" data-u="' + A.esc(url) + '" title="ölçümden çıkar">çıkar</button>' : '') +
          '</li>';
      }).join('') + '</ul>';
    }
    var excluded = st.excluded_samples || [];
    if (!editor && excluded.length) {
      out.samples += '<h3>Çıkarılan örnekler</h3><ul class="samples">' + excluded.map(function (u) {
        return '<li class="muted">' + A.esc(u) + (canEx ? ' <button type="button" class="small" data-a="unexclude" data-u="' +
          A.esc(u) + '">geri al</button>' : '') + '</li>';
      }).join('') + '</ul>';
    }
    out.alternatives = alternativesHtml((st.evidence || {}).alternatives);
    out.investigation = investigationHtml((st.evidence || {}).investigation);
    var q = (st.evidence || {}).question, CS = A.cardSync;
    var qsecs = q ? ((st.evidence || {}).question_sections || CS.questionParse(q)) : null;
    out.evidence = (q && (st.state === 'needs_human' || st.state === 'engine_gap')
      ? (qsecs ? '<div class="squestion q">' + CS.questionHtml(qsecs) + '</div>' : '<div class="squestion">' + A.esc(q) + '</div>') : '') +
      evidenceHtml(st.evidence);
    var msgs = st.messages || [];
    if (msgs.length) {
      out.messages = '<h3>Mesajlar</h3><ul class="msgs">' + msgs.map(function (m) {
        var qm = CS.questionParse(m.text);   // "Yardım gerekiyor": a folded summary line, opens to the formatted question
        var body = qm ? '<details class="qmsg"><summary>Yardım gerekiyor — ' + A.esc(CS.questionSummary(qm)) + '</summary>' +
          CS.questionHtml(qm) + '</details>' : A.esc(m.text);
        return '<li><span class="role ' + A.esc(m.role) + '">' + A.esc(ROLE[m.role] || m.role) + '</span> ' + body +
          ' <span class="muted">' + A.esc(A.when(m.at)) + '</span></li>';
      }).join('') + '</ul>';
    }
    return out;
  }

  var DATA_SECTIONS = ['handoff', 'noteused', 'checks', 'samples', 'alternatives', 'investigation', 'evidence', 'messages'];

  function dataHtml(st) {
    var parts = dataParts(st);
    var out = DATA_SECTIONS.map(function (k) { return parts[k]; }).join('');
    return out || '<p class="muted">Henüz ölçüm yok.</p>';
  }

  function allowed(st, name) { return !Array.isArray(st.actions) || !st.actions.length || st.actions.indexOf(name) >= 0; }

  function actionsHtml(st) {
    var s = st.state, b = [];
    if (st.notes_changed && s !== 'running') {
      b.push('<button type="button" class="primary" data-a="noterun">Not değişti — aşamaları notla yeniden çalıştır</button>');
    }
    if (s === 'needs_human' || s === 'engine_gap') {
      if (allowed(st, 'rerun') || allowed(st, 'hint')) b.push('<button type="button" class="primary" data-a="retry">yeniden dene</button>');
      if (allowed(st, 'sample')) b.push('<button type="button" data-a="sample">örnek adres ver</button>');
      var fields = st.skippable || [];
      if (fields.length && allowed(st, 'skip')) {
        b.push('<select data-f aria-label="atlanacak alan">' + fields.map(function (f) {
          return '<option value="' + A.esc(f) + '">' + A.esc(FIELD[f] || f) + '</option>';
        }).join('') + '</select><button type="button" data-a="skipfield">bu alanı atla</button>');
      }
      if (st.optional) b.push('<button type="button" data-a="skipstage">aşamayı atla</button>');
      if (s === 'needs_human' && (allowed(st, 'gap') || allowed(st, 'mark_gap'))) {
        b.push('<button type="button" data-a="gap">motor boşluğu olarak kaydet</button>');
      }
    } else if (s === 'passed' || s === 'passed_warn' || s === 'skipped') {
      b.push('<button type="button" data-a="retry">yeniden çalıştır</button>');
    }
    return b.join('');
  }

  /* A1 review card (T17): what each found section / menu link is; the admin confirms, then the stage passes */
  var SECTION_CHOICE = {title: 'dizi/film kartları', episode: 'bölüm kartları', ignore: 'yoksay'};

  function options(list, selected) {
    return list.map(function (o) {
      return '<option value="' + A.esc(o.id) + '"' + (o.id === selected ? ' selected' : '') + '>' + A.esc(o.label) + '</option>';
    }).join('');
  }

  function meaningOptions(rv, selected, noIgnore) {
    return options((rv.meanings || []).filter(function (m) { return !noIgnore || m.id !== 'ignore'; }), selected);
  }

  function addedRow(rv, item) {
    item = item || {};
    return '<tr class="radd"><td><input type="text" maxlength="60" placeholder="ad" data-n value="' + A.esc(item.name || '') + '"></td>' +
      '<td><input type="text" maxlength="2048" placeholder="https://… ya da /yol/" data-u value="' + A.esc(item.url || '') + '">' +
      (item.from_note ? ' <span class="pill">site notundan</span>' : '') + '</td>' +
      '<td><select data-m>' + meaningOptions(rv, item.meaning || 'series_list', true) + '</select></td>' +
      '<td><button type="button" data-a="rdel">sil</button></td></tr>';
  }

  /* Site note (the admin's description of the site): one editor, used on top of the Harita tab and in the A1 review card */
  var NOTE_MAX = 4000;
  var NOTE_TIP = 'Örn: Ana sayfada "Son Eklenen Diziler" dizi sayfasına gider, diğer kartlar bölüm sayfası. ' +
    'Dizi listesi /dizi-arsivi-01/ (sayfalı). Dizi sayfası /diziler/<slug>/, afiş .category_image içinde. Arama /?s=<kelime>.';

  function noteBoxHtml(text, where) {
    return '<div class="notebox" data-where="' + A.esc(where) + '"><h3>Site notu</h3>' +
      '<p class="muted">Sitenin bölümleri, liste/arşiv adresi, arama biçimi, afişin yeri… Aşamalar ve LLM bu notu kullanır.</p>' +
      '<textarea class="notetext" maxlength="' + NOTE_MAX + '" placeholder="' + A.esc(NOTE_TIP) + '">' + A.esc(text || '') +
      '</textarea><div class="bar"><button type="button" data-a="notesave">Kaydet</button>' +
      '<span class="muted notecount"></span><span class="err noteerr"></span></div></div>';
  }

  /* T30: "Ana ekranda" column (A1 sections / links, A2 blocks): home row / category / slider / yok / a new category */
  function homeCell(rv, proposed) {
    var opts = (rv.home_options || []).map(function (o) { return {id: o.value, label: o.label}; });
    var sel = proposed || 'yok';
    if (sel.indexOf('new:') === 0) opts.push({id: sel, label: 'yeni: ' + sel.slice(4)});
    opts.push({id: '__new', label: 'yeni kategori…'});
    return '<select data-home>' + options(opts, sel) + '</select><input type="text" maxlength="60" data-home-new ' +
      'placeholder="yeni kategori adı" class="homenew">';
  }

  function homeOf(tr) {
    var sel = tr && tr.querySelector('select[data-home]');
    if (!sel) return undefined;
    if (sel.value === '__new') {
      var name = tr.querySelector('[data-home-new]').value.trim();
      return name ? 'new:' + name : 'yok';
    }
    return sel.value;
  }

  /* A2 block card: one listing page holds several blocks of the same rows (series / films / users ...) */
  var BLOCK_KIND = {series: 'dizi adresleri', film: 'film adresleri', episode: 'bölüm adresleri', mixed: 'karışık adresler',
                    other: 'başka adresler'};

  function blocksHtml(st, rv) {
    var out = '<h3>Liste bloklarını onayla</h3><p class="muted">Bu liste sayfasında aynı yapıda birden çok blok var. ' +
      'Her blok ne? Yalnız dizi/film/karışık seçilen blokların satırları okunur.</p>' +
      (rv.llm_read ? '<p class="muted">Seçimler LLM\'in yorumuyla önceden dolduruldu; değiştirip onayla.</p>' : '') +
      '<table class="grid review"><thead><tr><th>Sayfa</th><th>Blok</th><th>Satır</th><th>Örnek adresler</th><th>LLM\'in yorumu</th><th>Ne?</th><th>Sayfalama</th><th>Ana ekranda</th></tr></thead><tbody>' +
      rv.blocks.map(function (b) {
        return '<tr><td class="mono">' + A.esc(b.url) + '</td><td>' + A.esc(b.index) + '. ' + A.esc(b.heading || '(başlıksız)') +
          '<br><span class="muted">' + A.esc(BLOCK_KIND[b.kind] || b.kind || '') + (b.head ? ' /' + A.esc(b.head) + '/' : '') +
          (b.reason ? ' · öneri: ' + A.esc(b.reason) : '') + '</span></td><td class="num">' + A.esc(fmt(b.rows)) + '</td><td>' +
          (b.samples || []).map(function (u) {
            return '<a href="' + A.esc(u) + '" target="_blank" rel="noopener noreferrer">' + A.esc(u) + '</a>';
          }).join('<br>') + '</td><td>' + llmNote(b.llm) + '</td><td><select data-blk-url="' + A.esc(b.url) + '" data-blk-i="' + A.esc(b.index) + '">' +
          options(rv.block_choices || [], b.proposed) + '</select></td><td>sayfalama: ' + (b.paged ? 'var' : 'yok') +
          '<br><select data-blk-pg data-orig="' + (b.paged ? '1' : '0') + '">' + options([{id: '1', label: 'var'}, {id: '0', label: 'yok'}], b.paged ? '1' : '0') +
          '</select></td><td>' + homeCell(rv, b.home) + '</td></tr>';
      }).join('') + '</tbody></table><div class="bar"><button type="button" class="primary" data-a="review">Onayla</button>' +
      '<button type="button" data-a="retry">' + A.esc(st.id) + '\'i yeniden çalıştır</button></div><div class="err rerr"></div>';
    return out;
  }

  /* T23: the LLM's rules and the system's guess both passed the measurement but found different things */
  function candidatesHtml(st, rv) {
    return '<h3>Hangi sonuç doğru?</h3><p class="muted">LLM\'in kuralları da sistem tahmini de ölçümü geçti ama farklı ' +
      'şeyler buldu. Doğru olanı seç; gerekirse sonra blokları/bölümleri ayrıca onaylarsın.</p>' +
      '<table class="grid review"><thead><tr><th></th><th>Aday</th><th>Sonuç</th><th>Puan</th><th>Bulduğu örnekler</th></tr></thead><tbody>' +
      rv.candidates.map(function (c, i) {
        return '<tr><td><input type="radio" name="cand-' + A.esc(st.id) + '" data-cand="' + A.esc(c.id) + '"' + (i === 0 ? ' checked' : '') +
          '></td><td>' + A.esc(c.label || c.id) + '</td><td>' + A.esc(c.summary || '') + '<br><span class="muted">' +
          A.esc((c.notes || []).join('; ')) + '</span></td><td class="num">' + A.esc(fmt(c.score)) + '</td><td>' +
          (c.samples || []).map(function (u) { return A.esc(u); }).join('<br>') + '</td></tr>';
      }).join('') + '</tbody></table><div class="bar"><button type="button" class="primary" data-a="review">Onayla</button>' +
      '<button type="button" data-a="retry">' + A.esc(st.id) + '\'i yeniden çalıştır</button></div><div class="err rerr"></div>';
  }

  function reviewHtml(st) {
    var rv = st.review;
    if (!rv) return '';
    if ((rv.candidates || []).length) return candidatesHtml(st, rv);
    if ((rv.blocks || []).length) return blocksHtml(st, rv);
    var secChoices = (rv.section_choices || ['title', 'episode', 'ignore']).map(function (c) {
      return {id: c, label: SECTION_CHOICE[c] || c};
    });
    var out = '<h3>Bulunanları onayla</h3><p class="muted">' + (rv.first
      ? 'Ana sayfada bulunan bölümler ve menü bağlantıları ne? Seçimine göre içe aktarılır.'
      : 'Yeni bölüm/bağlantı bulundu; yalnız yenileri soruluyor.') + '</p>';
    if (rv.llm_read) out += '<p class="muted">Seçimler LLM\'in yorumuyla önceden dolduruldu; değiştirip onayla.</p>';
    if ((rv.sections || []).length) {
      out += '<table class="grid review"><thead><tr><th>Bölüm</th><th>Kart</th><th>Örnek adresler</th><th>LLM\'in yorumu</th><th>Ne?</th><th>Ana ekranda</th></tr></thead><tbody>' +
        rv.sections.map(function (sec) {
          return '<tr><td>' + A.esc(sec.title || sec.id) + '</td><td class="num">' + A.esc(fmt(sec.cards)) + '</td><td>' +
            (sec.samples || []).map(function (u) {
              return '<a href="' + A.esc(u) + '" target="_blank" rel="noopener noreferrer">' + A.esc(u) + '</a>';
            }).join('<br>') + '</td><td>' + llmNote(sec.llm) + '</td><td><select data-sec="' + A.esc(sec.id) + '">' + options(secChoices, sec.proposed) +
            '</select></td><td>' + homeCell(rv, sec.home) + '</td></tr>';
        }).join('') + '</tbody></table>';
    }
    if ((rv.links || []).length) {
      out += '<table class="grid review"><thead><tr><th>Bağlantı</th><th>Adres</th><th>LLM\'in yorumu</th><th>Ne?</th><th>Ana ekranda</th></tr></thead><tbody>' +
        rv.links.map(function (l) {
          return '<tr><td>' + A.esc(l.name || '') + '</td><td class="mono">' + A.esc(l.url) + '</td><td>' + llmNote(l.llm) +
            '</td><td><select data-link="' +
            A.esc(l.url) + '">' + meaningOptions(rv, l.proposed) + '</select></td><td>' + homeCell(rv, l.home) + '</td></tr>';
        }).join('') + '</tbody></table>';
    }
    var sug = rv.suggested || [];
    if (sug.length) out += '<p class="muted">Site notundaki ' + sug.length + ' adres aşağıya öneri olarak eklendi; istemiyorsan sil.</p>';
    out += '<table class="grid review"><thead><tr><th colspan="4">Eksik adres ekle</th></tr></thead><tbody class="raddlist">' +
      sug.map(function (item) { return addedRow(rv, item); }).join('') + '</tbody></table><div class="bar"><button type="button" data-a="radd">adres ekle</button>' +
      '<button type="button" class="primary" data-a="review">Onayla</button>' +
      '<button type="button" data-a="retry">A1\'i yeniden çalıştır</button></div><div class="err rerr"></div>';
    return out + noteBoxHtml(noteText, 'review');
  }

  function reviewBody(cardEl) {
    var out = {sections: [], links: [], added: [], blocks: []};
    var cand = cardEl.querySelector('input[data-cand]:checked');
    if (cand) return {candidate: cand.getAttribute('data-cand')};
    cardEl.querySelectorAll('select[data-blk-url]').forEach(function (el) {
      var tr = el.closest('tr'), pg = tr && tr.querySelector('select[data-blk-pg]');
      var item = {url: el.getAttribute('data-blk-url'), index: parseInt(el.getAttribute('data-blk-i'), 10), choice: el.value};
      if (pg && pg.value !== pg.getAttribute('data-orig')) item.paged = pg.value === '1';   /* only a change */
      var home = homeOf(tr);
      if (home !== undefined) item.home = home;
      out.blocks.push(item);
    });
    cardEl.querySelectorAll('select[data-sec]').forEach(function (el) {
      var item = {id: el.getAttribute('data-sec'), target: el.value}, home = homeOf(el.closest('tr'));
      if (home !== undefined) item.home = home;
      out.sections.push(item);
    });
    cardEl.querySelectorAll('select[data-link]').forEach(function (el) {
      var item = {url: el.getAttribute('data-link'), meaning: el.value}, home = homeOf(el.closest('tr'));
      if (home !== undefined) item.home = home;
      out.links.push(item);
    });
    cardEl.querySelectorAll('tr.radd').forEach(function (tr) {
      var url = tr.querySelector('[data-u]').value.trim();
      if (url) out.added.push({name: tr.querySelector('[data-n]').value.trim(), url: url, meaning: tr.querySelector('[data-m]').value});
    });
    return out;
  }

  function hintVisible(s) { return ['needs_human', 'engine_gap', 'passed', 'passed_warn', 'skipped'].indexOf(s) >= 0; }

  function makeCard(st) {
    var el = A.el(
      '<details class="stage" data-id="' + A.esc(st.id) + '"><summary><span class="sid"></span><span class="stitle"></span>' +
      '<span class="badge"></span><span class="ssum"></span></summary><div class="sbody"><div class="sreview"></div><div class="sdata">' +
      DATA_SECTIONS.map(function (k) { return '<div class="sd-' + k + '"></div>'; }).join('') +
      '<p class="muted sd-empty">Henüz ölçüm yok.</p></div>' +
      '<div class="hintbox" hidden><div class="htip muted"></div>' +
      '<textarea maxlength="2000" placeholder="İpucu yaz (ör. afiş img etiketinin data-src özniteliğinde)"></textarea>' +
      '<div class="hint-actions"></div><div class="err"></div></div></div></details>');
    var card = {el: el, id: st.id, state: null, keys: {}, lastInput: 0, reviewDirty: false,
      q: function (sel) { return el.querySelector(sel); }};
    el.querySelector('.sid').textContent = st.id;
    return card;
  }

  /* which form part of the card holds the focus ('review' | 'hint' | '') */
  function focusPart(card) {
    var f = document.activeElement;
    if (!f || !card.el.contains(f) || !/^(INPUT|TEXTAREA|SELECT)$/.test(f.tagName)) return '';
    return f.closest('.sreview') ? 'review' : (f.closest('.hintbox') ? 'hint' : (f.closest('.sd-samples') ? 'samples' : 'other'));
  }

  /* redraw one section; inner scroll positions (evidence box, lists) are kept */
  function redraw(el, html) {
    var keep = [].map.call(el.querySelectorAll('.evidence, .samples, .msgs'), function (n) { return n.scrollTop; });
    var opened = [].map.call(el.querySelectorAll('details'), function (n) { return n.open; });
    el.innerHTML = html;
    [].forEach.call(el.querySelectorAll('details'), function (n, i) { if (opened[i]) n.open = true; });
    [].forEach.call(el.querySelectorAll('.evidence, .samples, .msgs'), function (n, i) { if (keep[i]) n.scrollTop = keep[i]; });
  }

  function update(card, st) {
    var badge = STATE[st.state] || [st.state, ''];
    card.st = st;
    var plan = A.cardSync.plan(card.keys, st, {now: Date.now(), lastInput: card.lastInput, focus: focusPart(card),
                                                reviewDirty: card.reviewDirty, samplesEditing: card.samplesEditing});
    if (plan.skip) return;   /* the admin is typing in this card: nothing of it is touched */
    card.q('.stitle').textContent = st.title || st.id;
    card.q('.badge').innerHTML = '<span class="pill ' + badge[1] + '">' + A.esc(badge[0]) + '</span>';
    card.q('.ssum').textContent = st.waiting_for ? st.waiting_for + ' bekleniyor' : (st.summary || '');
    var parts = null;
    plan.sections.forEach(function (name) {
      if (DATA_SECTIONS.indexOf(name) >= 0) {
        parts = parts || dataParts(st);
        redraw(card.q('.sd-' + name), parts[name]);
      } else if (name === 'review') {
        card.q('.sreview').innerHTML = reviewHtml(st);
        card.reviewDirty = false;
      } else if (name === 'actions') {
        card.q('.hint-actions').innerHTML = actionsHtml(st);
        card.q('.htip').textContent = st.state === 'needs_human'
          ? 'Sistem bu aşamada takıldı. Bir ipucu yazıp yeniden deneyebilir, örnek adres verebilir ya da alanı atlayabilirsin.'
          : 'İstersen ipucu yazıp aşamayı yeniden çalıştırabilirsin (düzenleme).';
        card.q('.hintbox').hidden = !hintVisible(st.state);
      }
    });
    if (parts) card.q('.sd-empty').hidden = DATA_SECTIONS.some(function (k) { return parts[k]; });
    card.keys = plan.keys;
    var human = st.state === 'needs_human' || st.state === 'review';
    card.el.classList.toggle('human', human);   /* open/closed is the accordion's (mapTab), never polling's */
    card.state = st.state;
  }

  function mapTab(body, siteId, site) {
    noteText = (site && site.notes) || '';
    body.innerHTML = '<div id="sitenote">' + noteBoxHtml(noteText, 'top') + '</div>' +
      '<div class="pinned" id="pinned"></div><div id="stagelist"><p class="muted">yükleniyor…</p></div>';
    var pinned = body.querySelector('#pinned'), list = body.querySelector('#stagelist');
    var cards = {}, order = [], busy = false, first = true, accReady = false;
    var path = '/api/control/sites/' + encodeURIComponent(siteId) + '/stages/';
    var ACC_KEY = 'admin.accordion.' + siteId + '.map';

    /* accordion (T19 "Akordeon"): one open card at most, kept per site + tab in sessionStorage */
    function accLoad() {
      try { return JSON.parse(window.sessionStorage.getItem(ACC_KEY) || 'null'); } catch (e) { return null; }
    }
    function accSave(openId) {
      try { window.sessionStorage.setItem(ACC_KEY, JSON.stringify({open: openId, touched: true})); } catch (e) { /* ignore */ }
    }
    function openId() {
      var el = body.querySelector('details.stage[open]');
      return el ? el.getAttribute('data-id') : null;
    }
    /* first load only: the saved card, else the first card waiting for the admin (until the admin's first click) */
    function accInit() {
      var human = order.filter(function (id) { var s = cards[id].state; return s === 'needs_human' || s === 'review'; });
      var want = A.cardSync.accordionPick(order, human, accLoad());
      order.forEach(function (id) { cards[id].el.open = id === want; });
    }

    /* cards move only when the order really changed (moving a node drops the focus and jumps the page) */
    function place() {
      [pinned, list].forEach(function (target) {
        var wanted = order.filter(function (id) {
          var c = cards[id];
          return (c.state === 'needs_human' || c.state === 'review' ? pinned : list) === target;
        }).map(function (id) { return cards[id].el; });
        var current = [].filter.call(target.children, function (n) { return n.classList.contains('stage'); });
        if (A.cardSync.sameOrder(current, wanted)) return;
        wanted.forEach(function (el) { target.appendChild(el); });
      });
    }

    /* keep the card nearest the top of the screen where it was (growth above it does not push the page) */
    function anchor() {
      var els = [].slice.call(body.querySelectorAll('details.stage'));
      var el = els.filter(function (n) { return n.getBoundingClientRect().bottom > 0; })[0];
      if (!el) return function () {};
      var top = el.getBoundingClientRect().top, y = window.scrollY;
      return function () {
        var diff = el.getBoundingClientRect().top - top;
        if (diff && window.scrollY === y) window.scrollTo(window.scrollX, y + diff);
      };
    }

    function load() {
      return A.api('/api/control/sites/' + encodeURIComponent(siteId) + '/stages').then(function (d) {
        var stages = d.stages || [];
        if (first) { list.innerHTML = stages.length ? '' : '<p class="empty">Aşama bilgisi yok.</p>'; first = false; }
        busy = !!d.busy;
        syncNote((d.notes || {}).text || '');
        order = stages.map(function (st) { return st.id; });
        var restore = first ? function () {} : anchor();
        stages.forEach(function (st) {
          if (st.state === 'running' || st.state === 'pending' && busy) busy = true;
          var c = cards[st.id] || (cards[st.id] = makeCard(st));
          update(c, st);
        });
        place();
        if (!accReady && order.length) { accReady = true; accInit(); }
        if (A.stageDecor) A.stageDecor(siteId, cards);   // T14: open-problem badge on the cards
        restore();
      }).catch(function (e) {
        if (first) list.innerHTML = '<p class="err">' + A.esc(e.message) + '</p>';
      });
    }

    /* the note editors follow the server unless the admin is typing in them */
    function syncNote(text) {
      noteText = text;
      body.querySelectorAll('textarea.notetext').forEach(function (ta) {
        if (document.activeElement !== ta && !ta.getAttribute('data-dirty')) ta.value = text;
        countNote(ta);
      });
    }

    function countNote(ta) {
      var box = ta.closest('.notebox');
      box.querySelector('.notecount').textContent = ta.value.length + '/' + NOTE_MAX;
    }

    /* typing / choosing in a card: polling leaves that card alone for cardSync.TYPING_MS */
    ['input', 'change'].forEach(function (type) {
      body.addEventListener(type, function (ev) {
        var el = ev.target.closest && ev.target.closest('.stage');
        var c = el && cards[el.getAttribute('data-id')];
        if (!c) return;
        c.lastInput = Date.now();
        if (ev.target.closest('.sreview')) c.reviewDirty = true;
      });
    });

    body.addEventListener('input', function (ev) {
      if (!ev.target.matches('textarea.notetext')) return;
      ev.target.setAttribute('data-dirty', '1');
      countNote(ev.target);
    });

    function saveNote(b) {
      var box = b.closest('.notebox'), ta = box.querySelector('textarea'), err = box.querySelector('.noteerr');
      err.textContent = '';
      if (ta.value.length > NOTE_MAX) { err.textContent = 'En çok ' + NOTE_MAX + ' karakter.'; return; }
      b.disabled = true;
      A.api('/api/control/sites/' + encodeURIComponent(siteId) + '/notes', {method: 'PUT', body: {notes: ta.value}})
        .then(function () {
          ta.removeAttribute('data-dirty');
          body.querySelectorAll('textarea.notetext').forEach(function (o) { o.removeAttribute('data-dirty'); });
          syncNote(ta.value.trim());
          return load();
        }).catch(function (e) { err.textContent = e.message; }).then(function () { b.disabled = false; });
    }

    /* a click on a stage card's own summary: open it and close the others; the clicked card stays where it is on screen */
    body.addEventListener('click', function (ev) {
      var sum = ev.target.closest && ev.target.closest('summary');
      var el = sum && sum.parentElement;
      if (!el || !el.matches('details.stage') || !body.contains(el)) return;
      ev.preventDefault();
      var id = el.getAttribute('data-id'), wasOpen = el.open;
      var next = A.cardSync.accordionToggle(openId(), id, wasOpen);
      var top = sum.getBoundingClientRect().top;
      order.forEach(function (k) { if (cards[k]) cards[k].el.open = k === next; });
      var diff = sum.getBoundingClientRect().top - top;
      if (diff) window.scrollTo(window.scrollX, window.scrollY + diff);
      accSave(next);
    });

    body.addEventListener('click', function (ev) {
      var b = ev.target.closest('button[data-a]');
      if (!b) return;
      if (b.getAttribute('data-a') === 'notesave') { saveNote(b); return; }
      var cardEl = b.closest('.stage');
      if (!cardEl) return;
      var id = cardEl.getAttribute('data-id'), c = cards[id];
      c.lastInput = 0;   /* a user action: the card answers right away */
      var ta = cardEl.querySelector('.hintbox textarea'), err = cardEl.querySelector('.hintbox .err');
      var a = b.getAttribute('data-a'), req;
      if (a === 'radd' || a === 'rdel') c.reviewDirty = true;   /* the edited review form is not redrawn by polling */
      if (a === 'radd') { cardEl.querySelector('.raddlist').insertAdjacentHTML('beforeend', addedRow(c.st.review || {})); return; }
      if (a === 'rdel') { b.closest('tr').remove(); return; }
      if (/^s(add|del|res|edit|editok|editno|toggle|run)$/.test(a)) { sampleAction(cardEl, c, id, a, b); return; }
      if (b.closest('.sreview')) err = cardEl.querySelector('.rerr');
      err.textContent = '';
      if (a === 'noterun') {
        req = A.post('/api/control/sites/' + encodeURIComponent(siteId) + '/notes/rerun');
      } else if (a === 'review') {
        req = A.post(path + id + '/review', reviewBody(cardEl));
      } else if (a === 'retry') {
        var text = ta.value.trim();
        req = text ? A.post(path + id + '/hint', {text: text}).then(function () { ta.value = ''; }) : A.post(path + id + '/rerun');
      } else if (a === 'sample') {
        var url = window.prompt('Bu aşama için örnek sayfa adresi:');
        if (!url) return;
        req = A.post(path + id + '/sample', {url: url.trim()});
      } else if (a === 'exclude' || a === 'unexclude') {
        req = A.post(path + id + '/exclude', {url: b.getAttribute('data-u'), undo: a === 'unexclude'});
      } else if (a === 'skipfield') {
        var f = cardEl.querySelector('select[data-f]').value;
        if (!window.confirm('"' + (FIELD[f] || f) + '" alanı bu sitede yok sayılsın mı?')) return;
        req = A.post(path + id + '/skip', {field: f});
      } else if (a === 'skipstage') {
        if (!window.confirm((c.st.title || id) + ' aşaması atlansın mı?')) return;
        req = A.post(path + id + '/skip', {});
      } else if (a === 'gap') {
        var title = window.prompt('Motor boşluğunun kısa başlığı:', c.st.summary || '');
        if (!title) return;
        req = A.post(path + id + '/gap', {title: title.trim()});
      } else return;
      b.disabled = true;
      req.then(function () { busy = true; return load(); }).catch(function (e) { err.textContent = e.message; })
        .then(function () { b.disabled = false; A.refreshStrip(); });
    });

    /* T25 sample editor: add / inline edit / delete / restore / "only mine" / re-run (only srun starts the stage) */
    function sampleAction(cardEl, c, id, a, b) {
      var box = cardEl.querySelector('.sd-samples'), err = box.querySelector('.serr') || cardEl.querySelector('.hintbox .err');
      var tr = b.closest('[data-u]'), url = tr ? tr.getAttribute('data-u') : '';
      if (err) err.textContent = '';
      if (a === 'sedit') {
        c.samplesEditing = true;
        tr.querySelector('.surl').innerHTML = '<input type="text" maxlength="2048" data-sedit value="' + A.esc(url) + '">';
        tr.querySelector('.sbtns').innerHTML = '<button type="button" class="small primary" data-a="seditok">Kaydet</button> ' +
          '<button type="button" class="small" data-a="seditno">Vazgeç</button>';
        tr.querySelector('input[data-sedit]').focus();
        return;
      }
      if (a === 'seditno') { c.samplesEditing = false; c.lastInput = 0; c.keys.samples = ''; update(c, c.st); return; }
      var body;
      if (a === 'seditok') {
        var nu = tr.querySelector('input[data-sedit]').value.trim();
        if (!nu || nu === url) { c.samplesEditing = false; c.lastInput = 0; c.keys.samples = ''; update(c, c.st); return; }
        body = {op: 'edit', url: url, new_url: nu};
      } else if (a === 'sadd') {
        var inp = box.querySelector('input[data-snew]'), v = inp ? inp.value.trim() : '';
        if (!v) return;
        body = {op: 'add', url: v};
      } else if (a === 'sdel') {
        if (!window.confirm('Bu örnek ölçümden kalıcı olarak çıkarılsın mı?\n' + url)) return;
        body = {op: 'delete', url: url};
      } else if (a === 'sres') {
        body = {op: 'restore', url: url};
      } else if (a === 'stoggle') {
        body = {op: 'toggle', on: b.getAttribute('data-on') === '1'};
      } else {
        body = {op: 'run'};
      }
      b.disabled = true;
      A.post(path + id + '/samples', body).then(function () {
        c.samplesEditing = false;
        c.lastInput = 0;
        if (a === 'srun') busy = true;
        return load();
      }).catch(function (e) { if (err) err.textContent = e.message; })
        .then(function () { b.disabled = false; A.refreshStrip(); });
    }

    A.poll(load, function () { return busy ? 2000 : 10000; });
  }

  /* ------------------------------------------------------------------ Sürümler */

  var WHO = {onboard: 'ekleme', edit: 'düzenleme', repair: 'onarım', admin: 'admin'};

  function versionsTab(body, siteId) {
    var base = '/api/control/sites/' + encodeURIComponent(siteId);
    body.innerHTML =
      '<div class="bar"><button type="button" id="vdiff">seçili iki sürümün farkı</button>' +
      '<label class="btn">yaml içe aktar (taslak)<input type="file" id="vimport" accept=".yaml,.yml,text/yaml" hidden></label>' +
      '<a class="btn" id="vexport" href="' + base + '/export">yaml indir</a><span class="err" id="verr"></span></div>' +
      '<div id="vlist"><p class="muted">yükleniyor…</p></div><div id="vdiffout"></div>' +
      '<h3>Haritanın yaml\'ı (salt okunur)</h3><pre class="yaml" id="vyaml"></pre>';
    var err = body.querySelector('#verr');

    function load() {
      A.api(base + '/versions').then(function (d) {
        var vs = (d.versions || []).slice().reverse();
        if (!vs.length) {
          body.querySelector('#vlist').innerHTML = '<p class="empty">Henüz sürüm yok (harita taslak halinde).</p>';
        } else {
          body.querySelector('#vlist').innerHTML = '<table class="grid vlist"><thead><tr><th>A</th><th>B</th><th>No</th>' +
            '<th>Tarih</th><th>Kim</th><th>Not</th><th></th></tr></thead><tbody>' + vs.map(function (v, i) {
              var cur = v.version === d.active_version;
              return '<tr><td><input type="radio" name="va" value="' + v.version + '"' + (i === 1 ? ' checked' : '') + '></td>' +
                '<td><input type="radio" name="vb" value="' + v.version + '"' + (i === 0 ? ' checked' : '') + '></td>' +
                '<td class="num"><b>v' + v.version + '</b>' + (cur ? ' <span class="pill ok">etkin</span>' : '') + '</td>' +
                '<td>' + A.esc(A.full(v.created_at)) + '</td><td>' + A.esc(WHO[v.created_by] || v.created_by) + '</td>' +
                '<td>' + A.esc(v.note || '') + '</td><td class="acts">' +
                (cur ? '' : '<button type="button" data-rb="' + v.version + '">bu sürüme dön</button>') +
                '<a class="btn" href="' + base + '/export?version=' + v.version + '">yaml indir</a></td></tr>';
            }).join('') + '</tbody></table>';
        }
      }).catch(function (e) { err.textContent = e.message; });
      A.api(base + '/export?download=0').then(function (t) { body.querySelector('#vyaml').textContent = t; })
        .catch(function (e) { body.querySelector('#vyaml').textContent = e.message; });
    }

    body.querySelector('#vdiff').onclick = function () {
      var a = body.querySelector('input[name=va]:checked'), b = body.querySelector('input[name=vb]:checked');
      var out = body.querySelector('#vdiffout');
      if (!a || !b) { err.textContent = 'İki sürüm seç.'; return; }
      A.api(base + '/versions/diff?a=' + a.value + '&b=' + b.value).then(function (d) {
        var ch = d.changes || [];
        out.innerHTML = '<h3>v' + A.esc(a.value) + ' → v' + A.esc(b.value) + '</h3>' + (ch.length
          ? '<table class="diff"><thead><tr><th>Yol</th><th>Önce</th><th>Sonra</th></tr></thead><tbody>' + ch.map(function (c) {
              return '<tr><td class="mono">' + A.esc(c.path) + '</td><td class="b">' + A.esc(fmt(c.before)) + '</td>' +
                '<td class="a">' + A.esc(fmt(c.after)) + '</td></tr>';
            }).join('') + '</tbody></table>'
          : '<p class="muted">Fark yok.</p>');
      }).catch(function (e) { err.textContent = e.message; });
    };

    body.querySelector('#vlist').addEventListener('click', function (ev) {
      var b = ev.target.closest('button[data-rb]');
      if (!b) return;
      var v = b.getAttribute('data-rb');
      if (!window.confirm('v' + v + ' yeni bir sürüm olarak etkinleştirilsin mi?')) return;
      A.post(base + '/versions/' + v + '/rollback').then(load).catch(function (e) { err.textContent = e.message; });
    });

    body.querySelector('#vimport').onchange = function (ev) {
      var file = ev.target.files[0];
      if (!file) return;
      file.text().then(function (text) {
        if (!window.confirm('Bu yaml taslak olarak içe aktarılsın mı? (etkin sürüm değişmez)')) return;
        /* another site's yaml: never overwrite silently; that site's draft only after a named confirm */
        var m = /^site_id:[ \t]*["']?([^"'\s#]+)/m.exec(text), other = !m ? '?' : m[1] !== siteId ? m[1] : null;
        var send = function (overwrite) { return A.post('/api/control/sites/import', {yaml: text, overwrite: overwrite}); };
        var req = !other ? send(true) : send(false).catch(function (e) {
          if (e.code !== 'site_exists') throw e;
          if (!window.confirm(other + ' sitesinin taslağının üzerine yazılsın mı?')) return null;
          return send(true);
        });
        return req.then(function (d) {
          if (!d) return;
          err.textContent = '';
          A.flash('Taslak olarak içe aktarıldı: ' + d.site_id);
          if (d.site_id !== siteId) A.go('#/site/' + encodeURIComponent(d.site_id) + '/versions'); else load();
        });
      }).catch(function (e) { err.textContent = e.message; }).then(function () { ev.target.value = ''; });
    };

    load();
  }

  A.stageDataHtml = dataHtml;   // T14: the Problems screen shows the same card data
  A.siteTab('map', 'Harita', mapTab, 10);
  A.siteTab('versions', 'Sürümler', versionsTab, 90);
})();
