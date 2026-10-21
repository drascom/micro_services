/* Stage card polling without page jumps (T19 user bug "sayfa kayması"): pure, DOM-free decisions. site.js keeps one key per
   card section and redraws only the sections whose data changed; a card the admin is typing in is not touched at all.
   Loaded in the browser (ADMIN.cardSync) and in node tests (module.exports). */
(function (root) {
  'use strict';

  var TYPING_MS = 3000;
  /* card sections redrawn on their own (the hint box / review form are never redrawn while in use) */
  var SECTIONS = ['handoff', 'noteused', 'checks', 'samples', 'alternatives', 'investigation', 'evidence', 'messages', 'review',
    'actions'];

  function sectionKeys(st) {
    var ev = st.evidence || {};
    return {
      handoff: JSON.stringify(st.handoff_in || ''),
      noteused: JSON.stringify(ev.note_used || null),
      checks: JSON.stringify(st.checks || []),
      samples: JSON.stringify([st.samples || [], st.excluded_samples || [], st.actions, st.state, st.sample_list || null]),
      alternatives: JSON.stringify(ev.alternatives || null),
      investigation: JSON.stringify(ev.investigation || null),
      evidence: JSON.stringify(ev),
      messages: JSON.stringify(st.messages || []),
      review: JSON.stringify(st.review || null),
      actions: JSON.stringify([st.state, st.actions, st.skippable, st.optional, st.notes_changed])
    };
  }

  /* ui = {now, lastInput (ms), focus: '' | 'review' | 'hint', reviewDirty: bool}
     -> {skip: true} (the admin is typing: leave the whole card) or {sections: [...names to redraw], keys} */
  function plan(prevKeys, st, ui) {
    ui = ui || {};
    if (ui.lastInput && (ui.now || 0) - ui.lastInput < TYPING_MS) return {skip: true, sections: [], keys: prevKeys || {}};
    var keys = sectionKeys(st), prev = prevKeys || {}, out = [];
    SECTIONS.forEach(function (name) {
      if (keys[name] === prev[name]) return;
      if (name === 'review' && (ui.focus === 'review' || (ui.reviewDirty && st.review))) { keys[name] = prev[name]; return; }
      if (name === 'actions' && ui.focus === 'hint') { keys[name] = prev[name]; return; }
      if (name === 'samples' && (ui.focus === 'samples' || ui.samplesEditing)) { keys[name] = prev[name]; return; }
      out.push(name);
    });
    return {skip: false, sections: out, keys: keys};
  }

  /* do the container's children already stand in the wanted order? (then nothing is moved: moving a node drops focus) */
  function sameOrder(current, wanted) {
    if (current.length !== wanted.length) return false;
    for (var i = 0; i < wanted.length; i++) if (current[i] !== wanted[i]) return false;
    return true;
  }

  /* Accordion (T19 "Akordeon"): at most one stage card open; polling never opens/closes a card.
     saved = {open: id|null, touched: bool} from sessionStorage (or null). First load: the saved card if it still exists,
     else nothing when the admin already chose, else the first card waiting for the admin (humanIds) or none. */
  function accordionPick(ids, humanIds, saved) {
    saved = saved || {};
    if (saved.open && (ids || []).indexOf(saved.open) >= 0) return saved.open;
    if (saved.touched) return null;
    return (humanIds || [])[0] || null;
  }

  /* the admin clicked a card's summary: the new single open card (null = all closed) */
  function accordionToggle(openId, clickedId, wasOpen) {
    if (!wasOpen) return clickedId;
    return openId === clickedId ? null : openId;
  }

  /* "Yardım gerekiyor" question (stages/explain.py): plain text -> sections, sections -> html. The text form is one heading per
     line, " • " item lines, an item's addresses indented below it; an older one-line form ("Ne yaptım: a; b") still parses. */
  var Q_TITLE = 'Yardım gerekiyor';
  var Q_HEADS = ['Ne yaptım', 'Bulamadığım', 'Ayrıntı', 'Senden istediğim'];
  var Q_CLIP = 90;

  function qesc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
  }

  function questionParse(text) {
    var lines = String(text || '').split('\n');
    if (lines[0].replace(/[.\s]+$/, '') !== Q_TITLE) return null;
    var secs = [], cur = null;
    lines.slice(1).forEach(function (raw) {
      var line = raw.replace(/\s+$/, '');
      if (!line.trim()) return;
      var head = Q_HEADS.filter(function (h) { return line === h || line.indexOf(h + ':') === 0; })[0];
      if (head) {
        cur = {title: head, items: []};
        secs.push(cur);
        var rest = line.slice(head.length + 1).trim();   // older one-line form: "Ne yaptım: a; b."
        if (rest) cur.items.push({text: rest});
        return;
      }
      if (!cur) { cur = {title: 'Ayrıntı', items: []}; secs.push(cur); }
      var bullet = /^\s*•\s*/.exec(line);
      if (bullet) cur.items.push({text: line.slice(bullet[0].length)});
      else if (/^\s/.test(line) && cur.items.length) (cur.items[cur.items.length - 1].lines = cur.items[cur.items.length - 1].lines || []).push(line.trim());
      else cur.items.push({text: line.trim()});
    });
    return secs;
  }

  function questionSummary(secs) {
    var pick = (secs || []).filter(function (s) { return s.title === 'Bulamadığım'; })[0] || (secs || [])[0];
    var text = pick && pick.items.length ? pick.items[0].text : '';
    return text.length > Q_CLIP ? text.slice(0, Q_CLIP - 1) + '…' : text;
  }

  function questionHtml(secs) {
    return '<div class="qbox"><b class="qtitle">' + Q_TITLE + '</b>' + (secs || []).map(function (sec) {
      return '<div class="qsec"><b>' + qesc(sec.title) + '</b><ul>' + (sec.items || []).map(function (it) {
        return '<li>' + qesc(it.text) + (it.lines && it.lines.length ? '<span class="qlines">' + it.lines.map(function (l) {
          return '<span class="qurl">' + qesc(l) + '</span>';
        }).join('') + '</span>' : '') + '</li>';
      }).join('') + '</ul></div>';
    }).join('') + '</div>';
  }

  /* T25 "Örnekler" section of a stage card (A2-A7): st.sample_list = {rows: [{url, source, ok, note, measured, used}],
     excluded, own_only, min, own_count, warning}; canEdit = the stage is not running. Returns '' without a list. */
  var SRC_PILL = {'senin': 'ok', 'önceki aşamadan': '', 'otomatik': ''};

  function samplesHtml(st, canEdit) {
    var sl = st.sample_list;
    if (!sl || !sl.rows) return '';
    var ex = sl.excluded || [];
    var out = '<h3>Örnekler</h3>';
    if (sl.warning) out += '<p class="err swarn">' + qesc(sl.warning) + '</p>';
    out += '<p class="muted">' + (sl.own_only
      ? 'Yalnız senin örneklerin ölçülüyor (en az ' + qesc(sl.min) + ').'
      : 'Aşama senin, önceki aşamanın ve kendi bulduğu örnekleri ölçer.') +
      (canEdit ? ' <button type="button" class="small" data-a="stoggle" data-on="' + (sl.own_only ? '0' : '1') + '">' +
        (sl.own_only ? 'Tüm örnekleri kullan' : 'Yalnız benim örneklerimi kullan') + '</button>' : '') + '</p>';
    if (canEdit && !sl.own_only && sl.own_count > 0) {
      out += '<p class="muted soffer">' + qesc(sl.own_count) + ' örneği sen verdin. Sadece bunları kullan? ' +
        '<button type="button" class="small" data-a="stoggle" data-on="1">evet, sadece bunlar</button></p>';
    }
    if (sl.rows.length) {
      out += '<table class="grid stable"><thead><tr><th>Adres</th><th>Kaynak</th><th>Son ölçüm</th>' +
        (canEdit ? '<th></th>' : '') + '</tr></thead><tbody>' + sl.rows.map(function (r) {
          var res = r.measured ? (r.ok ? '<span class="pill ok">✓</span>' : '<span class="pill bad">✗</span>') : '—';
          return '<tr data-u="' + qesc(r.url) + '"' + (r.used ? '' : ' class="muted"') + '><td class="surl"><a href="' + qesc(r.url) +
            '" target="_blank" rel="noopener noreferrer">' + qesc(r.url) + '</a></td><td><span class="pill ' +
            (SRC_PILL[r.source] || '') + '">' + qesc(r.source) + '</span></td><td>' + res +
            (r.note ? ' <span class="muted">' + qesc(r.note) + '</span>' : '') +
            (r.used ? '' : ' <span class="muted">(kullanılmayacak)</span>') + '</td>' +
            (canEdit ? '<td class="sbtns"><button type="button" class="small" data-a="sedit">Düzenle</button> ' +
              '<button type="button" class="small" data-a="sdel">Sil</button></td>' : '') + '</tr>';
        }).join('') + '</tbody></table>';
    } else {
      out += '<p class="muted">Henüz örnek yok.</p>';
    }
    if (canEdit) {
      out += '<div class="bar sadd"><input type="text" maxlength="2048" placeholder="https://… yeni örnek adres" data-snew>' +
        '<button type="button" data-a="sadd">Ekle</button>' +
        '<button type="button" class="primary" data-a="srun">Örneklerle yeniden çalıştır</button><span class="err serr"></span></div>';
    }
    if (ex.length) {
      out += '<details class="sgone"><summary>Silinenler (' + ex.length + ')</summary><ul class="samples">' + ex.map(function (u) {
        return '<li class="muted" data-u="' + qesc(u) + '">' + qesc(u) +
          (canEdit ? ' <button type="button" class="small" data-a="sres">geri al</button>' : '') + '</li>';
      }).join('') + '</ul></details>';
    }
    return out;
  }

  var api = {TYPING_MS: TYPING_MS, SECTIONS: SECTIONS, sectionKeys: sectionKeys, plan: plan, sameOrder: sameOrder,
    accordionPick: accordionPick, accordionToggle: accordionToggle, questionParse: questionParse,
    questionSummary: questionSummary, questionHtml: questionHtml, samplesHtml: samplesHtml};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root && root.ADMIN) root.ADMIN.cardSync = api;
})(typeof window !== 'undefined' ? window : null);
