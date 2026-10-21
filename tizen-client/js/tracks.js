/* tracks.js - ses / altyazi SECIM MODELI (saf mantik, DOM yok): sunucunun streams[] + subtitles[] + audio[] alanlarindan
   Netflix tarzi secenekler, varsayilan secim, kalici tercih, akis gecisi karari. ES2017, opsiyonel zincir YOK.

   Kavramlar
   - varyant = saglayicinin tek video dosyasi (streams[i].variant_id). Ayni bolumun iki dosyasi yalniz altyazida ayrisabilir:
     sub_mode 'hard' = altyazi goruntuye GOMULU (kapatilamaz, ancak baska dosyaya gecilir), 'soft' = ayri VTT izi
     (subtitles[]; stream_ids ile hangi dosyalarda gecerli), 'none' = bilinen altyazi yok.
   - secenek kimlikleri: altyazi 'off' | 's:<id>' (soft) | 'h:<dil>' (gomulu); ses 'a:<id>'.
   - secim sel = { audio: <ses secenek id>, sub: <altyazi secenek id> } o an OYNAYAN akis icin gecerli olandir.
   Varsayilan kural (tercih yoksa): Turkce soft > Turkce gomulu akis > (Turkce SES dosyasi, altyazisiz) > Ingilizce soft > Kapali.
   Tercih (localStorage, profil bazli): dz_pref_sub_<profil> = 'off' | dil kodu, dz_pref_audio_<profil> = dil kodu | 'orig'.
   Tercih karsilanamazsa varsayilana dusulur, tercih SILINMEZ. */
(function (g) {
  'use strict';
  var DZ = g.DZ = g.DZ || {};

  var LANGS = {
    tr: 'Türkçe', en: 'İngilizce', de: 'Almanca', fr: 'Fransızca', es: 'İspanyolca', it: 'İtalyanca', pt: 'Portekizce',
    ru: 'Rusça', ar: 'Arapça', ja: 'Japonca', ko: 'Korece', zh: 'Çince', nl: 'Felemenkçe', pl: 'Lehçe', sv: 'İsveççe',
    da: 'Danca', fi: 'Fince', no: 'Norveççe', hi: 'Hintçe', fa: 'Farsça', el: 'Yunanca', ro: 'Romence', hu: 'Macarca',
    cs: 'Çekçe', bg: 'Bulgarca', uk: 'Ukraynaca', he: 'İbranice', id: 'Endonezce', th: 'Tayca', vi: 'Vietnamca'
  };
  var LANG_RANK = { tr: 0, en: 1 };
  var DUB = '@tr-audio';   /* wishes() isaretcisi: Turkce sesli dosya, altyazi kapali */
  var NOTE_HARD = 'Bu kaynakta altyazı görüntüye gömülü; kapatılamıyor.';
  var NOTE_NONE = 'Bu seçenek şu an kullanılamıyor.';

  function langName(code) { return code ? (LANGS[code] || String(code).toUpperCase()) : ''; }
  function rank(lang) { return LANG_RANK[lang] === undefined ? 2 : LANG_RANK[lang]; }
  function isArr(v) { return Object.prototype.toString.call(v) === '[object Array]'; }
  function indexOf(list, v) { return list ? list.indexOf(v) : -1; }

  function isEmbed(s) {
    if (DZ.player && DZ.player.isEmbedStream) return !!DZ.player.isEmbedStream(s);
    return !!(s && s.type === 'embed');
  }

  /* ---------------- model ---------------- */
  function byId(list, id) {
    for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i];
    return null;
  }

  function build(streams, subtitles, audio) {
    var m = { streams: streams || [], info: [], soft: [], hard: [], audio: [], subs: [] };
    var i, s;
    for (i = 0; i < m.streams.length; i++) {
      s = m.streams[i] || {};
      m.info.push({
        embed: isEmbed(s),
        variant: s.variant_id || ('#' + i),
        audio: s.audio_lang || null,
        mode: s.sub_mode === 'hard' || s.sub_mode === 'soft' ? s.sub_mode : 'none',
        hardLang: s.hard_lang || null
      });
    }
    var list = isArr(subtitles) ? subtitles : [];
    for (i = 0; i < list.length; i++) {
      var t = list[i];
      if (!t || !t.url) continue;
      m.soft.push({
        id: 's:' + (t.id || i), kind: 'soft', lang: t.lang || null, url: t.url,
        label: t.label || langName(t.lang) || 'Altyazı', ids: isArr(t.stream_ids) ? t.stream_ids : null, order: m.soft.length
      });
    }
    for (i = 0; i < m.info.length; i++) {
      var inf = m.info[i];
      if (inf.embed || inf.mode !== 'hard' || byId(m.hard, 'h:' + (inf.hardLang || '?'))) continue;
      m.hard.push({ id: 'h:' + (inf.hardLang || '?'), kind: 'hard', lang: inf.hardLang, badge: 'gömülü',
        label: langName(inf.hardLang) || 'Gömülü altyazı', order: 1000 + m.hard.length });
    }
    var merged = m.soft.concat(m.hard);
    merged.sort(function (a, b) { return rank(a.lang) - rank(b.lang) || (a.kind === b.kind ? 0 : a.kind === 'soft' ? -1 : 1) || a.order - b.order; });
    m.subs = [{ id: 'off', kind: 'off', lang: null, label: 'Kapalı' }].concat(merged);

    var a = isArr(audio) ? audio : [];
    for (i = 0; i < a.length; i++) {
      if (!a[i]) continue;
      m.audio.push({ id: 'a:' + (a[i].id || i), kind: 'audio', lang: a[i].lang || null,
        label: a[i].label || langName(a[i].lang) || 'Orijinal', ids: isArr(a[i].stream_ids) ? a[i].stream_ids : null });
    }
    if (!m.audio.length) {   /* eski sunucu / audio[] yok: akislarin audio_lang'indan turet */
      for (i = 0; i < m.info.length; i++) {
        if (m.info[i].embed) continue;
        var key = m.info[i].audio || 'orig';
        var opt = byId(m.audio, 'a:' + key);
        if (!opt) { opt = { id: 'a:' + key, kind: 'audio', lang: m.info[i].audio, label: langName(m.info[i].audio) || 'Orijinal', ids: [] }; m.audio.push(opt); }
        opt.ids.push(m.info[i].variant);
      }
    }
    /* Dili BILINMEYEN ses ("Orijinal") secenek degil joker: dili bilinen bir secenek varsa listeden cikar, dili bilinmeyen
       akislar her sese uyar (yoksa OK.ru gibi dilsiz bir kaynak "Orijinal" diye sahte bir alternatif olurdu). */
    var known = false;
    for (i = 0; i < m.audio.length; i++) if (m.audio[i].lang) known = true;
    if (known) m.audio = m.audio.filter(function (o) { return !!o.lang; });
    return m;
  }

  function audioExplicit(m, opt, i) { return !opt.ids || indexOf(opt.ids, m.info[i].variant) >= 0; }
  function hasExplicitAudio(m, i) {
    for (var k = 0; k < m.audio.length; k++) if (audioExplicit(m, m.audio[k], i)) return true;
    return false;
  }
  /* i. akis bu ses secenegine uyar mi: acikca listeli ya da hicbir secenekte yer almayan (dili bilinmeyen) akis = joker */
  function audioHas(m, opt, i) { return !opt || audioExplicit(m, opt, i) || !hasExplicitAudio(m, i); }

  /* altyazi secenegi i. akista gecerli mi */
  function subValid(m, opt, i) {
    var inf = m.info[i];
    if (!opt || !inf || inf.embed) return false;
    if (opt.kind === 'off') return inf.mode !== 'hard';
    if (opt.kind === 'soft') return inf.mode !== 'hard' && (!opt.ids || indexOf(opt.ids, inf.variant) >= 0);
    return inf.mode === 'hard' && (inf.hardLang || null) === (opt.lang || null);
  }

  /* (ses, altyazi) ciftini saglayan akis indeksleri, sunucu sirasiyla; audioId/subId null = kisit yok */
  function candidates(m, audioId, subId) {
    var out = [];
    var a = audioId ? byId(m.audio, audioId) : null;
    var s = subId ? byId(m.subs, subId) : null;
    for (var i = 0; i < m.streams.length; i++) {
      if (m.info[i].embed) continue;
      if (a && !audioHas(m, a, i)) continue;
      if (s && !subValid(m, s, i)) continue;
      out.push(i);
    }
    return out;
  }

  function hasChoices(m) {
    var managed = false;
    for (var i = 0; i < m.info.length; i++) if (!m.info[i].embed) managed = true;
    return managed && (m.audio.length > 1 || m.subs.length > 1);
  }

  /* gosterilecek ses secimi: istenen (uyuyorsa) ya da akisin ACIKCA ait oldugu secenek; dili bilinmeyen akista null (isaret yok) */
  function audioFor(m, i, preferId) {
    var pref = preferId ? byId(m.audio, preferId) : null;
    if (pref && audioHas(m, pref, i)) return pref.id;
    for (var k = 0; k < m.audio.length; k++) if (audioExplicit(m, m.audio[k], i)) return m.audio[k].id;
    return null;
  }

  function audioByLang(m, pref, i) {
    if (!pref) return null;
    for (var k = 0; k < m.audio.length; k++) {
      if ((m.audio[k].lang || 'orig') === pref && (i === undefined || audioHas(m, m.audio[k], i))) return m.audio[k].id;
    }
    return null;
  }

  /* denenecek altyazi secenekleri (oncelik sirasi): tercih, sonra varsayilan kural */
  function wishes(m, prefSub) {
    var list = [], k;
    function add(id) { if (indexOf(list, id) < 0) list.push(id); }
    function lang(code, withHard) {
      for (k = 0; k < m.subs.length; k++) if (m.subs[k].kind === 'soft' && m.subs[k].lang === code) add(m.subs[k].id);
      if (withHard) for (k = 0; k < m.subs.length; k++) if (m.subs[k].kind === 'hard' && m.subs[k].lang === code) add(m.subs[k].id);
    }
    if (prefSub === 'off') add('off');
    else if (prefSub) lang(prefSub, true);
    lang('tr', true);
    add(DUB);   /* Turkce sesli (dublaj) dosya varsa altyazi gerekmez: Ingilizce altyaziya tercih edilir */
    lang('en', false);
    add('off');
    return list;
  }

  /* baslangic akisi + secim: tercih -> varsayilan kural; ilk saglanabilen (ses, altyazi) icin */
  function choose(m, prefs) {
    prefs = prefs || {};
    var audioId = audioByLang(m, prefs.audio);
    var list = wishes(m, prefs.sub);
    var passes = audioId ? [audioId, null] : [null];
    for (var p = 0; p < passes.length; p++) {
      for (var w = 0; w < list.length; w++) {
        var c, ta;
        if (list[w] === DUB) {   /* yalniz ses tercihi yokken: Turkce sesli dosya + altyazisiz */
          ta = passes[p] ? null : audioByLang(m, 'tr');
          c = ta ? candidates(m, ta, 'off') : [];
          if (c.length) return { index: c[0], audio: ta, sub: 'off' };
          continue;
        }
        c = candidates(m, passes[p], list[w]);
        if (c.length) return { index: c[0], audio: audioFor(m, c[0], passes[p]), sub: list[w] };
      }
    }
    var first = 0;
    for (var i = 0; i < m.info.length; i++) if (!m.info[i].embed) { first = i; break; }
    var sel = select(m, first, { prefs: prefs });
    return { index: first, audio: sel.audio, sub: sel.sub };
  }

  /* i. akista GECERLI secim: istenen (want) gecerliyse o, degilse tercih / varsayilan kural; gomulu akista altyazi sabit */
  function select(m, i, opts) {
    opts = opts || {};
    var want = opts.want || {}, prefs = opts.prefs || {};
    var inf = m.info[i];
    if (!inf || inf.embed) return { audio: null, sub: 'off' };
    var audio = null;
    if (want.audio) { var w = byId(m.audio, want.audio); if (w && audioHas(m, w, i)) audio = w.id; }
    if (!audio) { var pa = audioByLang(m, prefs.audio, i); audio = pa && hasExplicitAudio(m, i) ? pa : null; }
    if (!audio) audio = audioFor(m, i, null);
    var sub = null;
    if (inf.mode === 'hard') {
      var hard = byId(m.hard, 'h:' + (inf.hardLang || '?'));
      sub = hard ? hard.id : 'off';
    } else {
      if (want.sub && subValid(m, byId(m.subs, want.sub), i)) sub = want.sub;
      var list = wishes(m, prefs.sub);
      for (var k = 0; !sub && k < list.length; k++) if (subValid(m, byId(m.subs, list[k]), i)) sub = list[k];
      if (!sub) sub = 'off';
    }
    return { audio: audio, sub: sub };
  }

  /* Kullanici secimi (panel): {index, audio, sub, switch} ya da {note} (uygulanamaz, mevcut secim kalir).
     switch=true: baska akisa gecilmeli (oynatma konumu korunarak). */
  function pick(m, curIndex, sel, kind, id, prefs) {
    var cur = m.info[curIndex];
    var want = { audio: sel.audio, sub: sel.sub };
    if (kind === 'audio') want.audio = id; else want.sub = id;
    var c = candidates(m, want.audio, want.sub);
    if (c.length) {
      var at = indexOf(c, curIndex) >= 0 ? curIndex : c[0];
      return { index: at, audio: audioFor(m, at, want.audio), sub: want.sub, switch: at !== curIndex };
    }
    var idx, r;
    if (kind === 'sub') {   /* bu ses bu altyazi ile yok: sesi serbest birak */
      c = candidates(m, null, want.sub);
      if (!c.length) return { note: id === 'off' && cur && cur.mode === 'hard' ? NOTE_HARD : NOTE_NONE };
      idx = indexOf(c, curIndex) >= 0 ? curIndex : c[0];
      return { index: idx, audio: audioFor(m, idx, null), sub: want.sub, switch: idx !== curIndex };
    }
    c = candidates(m, want.audio, null);   /* ses: o sesin akislari, altyazi yeniden turetilir */
    if (!c.length) return { note: NOTE_NONE };
    idx = indexOf(c, curIndex) >= 0 ? curIndex : c[0];
    r = select(m, idx, { want: { audio: want.audio, sub: want.sub }, prefs: prefs });
    return { index: idx, audio: r.audio, sub: r.sub, switch: idx !== curIndex };
  }

  /* panel gorunumu: iki sutun; tek/yok secenekli sutun 'dim' (gri, odaklanmaz); 'unavailable' = secilirse aciklama notu */
  function view(m, sel) {
    var audio = [], subs = [], i;
    for (i = 0; i < m.audio.length; i++) {
      audio.push({ id: m.audio[i].id, label: m.audio[i].label, current: m.audio[i].id === sel.audio });
    }
    for (i = 0; i < m.subs.length; i++) {
      var o = m.subs[i];
      subs.push({ id: o.id, label: o.label, badge: o.badge || '', current: o.id === sel.sub,
        unavailable: candidates(m, null, o.id).length === 0 });   /* hicbir akista yok (ör. gomulu-tek kaynakta "Kapali") */
    }
    return { audio: { items: audio, dim: audio.length < 2 }, subs: { items: subs, dim: subs.length < 2 } };
  }

  /* dugme etiketi: o anki altyazi */
  function subLabel(m, sel) {
    var o = sel ? byId(m.subs, sel.sub) : null;
    if (!o) return 'Kapalı';
    return o.label + (o.badge ? ' (' + o.badge + ')' : '');
  }

  /* ---------------- tercih (localStorage) ---------------- */
  function pid() {
    try { return (DZ.api && DZ.api.profileId && DZ.api.profileId()) || ''; } catch (e) { return ''; }
  }
  function ls() {
    try { return g.localStorage || null; } catch (e) { return null; }
  }
  function get(key) {
    try { var s = ls(); var v = s ? s.getItem(key) : null; return v === null || v === undefined || v === '' ? null : String(v); } catch (e) { return null; }
  }
  function set(key, value) {
    try { var s = ls(); if (s) s.setItem(key, value); return !!s; } catch (e) { return false; }
  }
  function subKey() { return 'dz_pref_sub_' + pid(); }
  function audioKey() { return 'dz_pref_audio_' + pid(); }

  function loadPrefs() { return { sub: get(subKey()), audio: get(audioKey()) }; }

  /* Ayarlar ekrani: varsayilan altyazi dili ('off' | dil kodu). Profil secili degilse (anahtar bos profil) yazilmaz. */
  function setSubPref(value) {
    if (!pid()) return false;
    return set(subKey(), String(value));
  }

  /* kullanici bir secim yapinca: altyazi = dil kodu | 'off'; ses = dil kodu | 'orig'. Dili bilinmeyen secenek kaydedilmez. */
  function remember(m, kind, id) {
    if (kind === 'audio') {
      var a = byId(m.audio, id);
      if (a) set(audioKey(), a.lang || 'orig');
      return;
    }
    var o = byId(m.subs, id);
    if (!o) return;
    if (o.kind === 'off') set(subKey(), 'off');
    else if (o.lang) set(subKey(), o.lang);
  }

  /* playflow: ilk denenecek akisi tercihe gore one al; digerleri sunucu sirasini korur. Secenek yoksa aynen doner. */
  function prefer(streams, subtitles, audio) {
    var list = streams || [];
    if (list.length < 2) return list;
    var m = build(list, subtitles, audio);
    if (!hasChoices(m)) return list;
    var c = choose(m, loadPrefs());
    if (c.index <= 0) return list;
    var out = [list[c.index]];
    for (var i = 0; i < list.length; i++) if (i !== c.index) out.push(list[i]);
    return out;
  }

  DZ.tracks = {
    build: build, choose: choose, select: select, pick: pick, view: view, candidates: candidates,
    hasChoices: hasChoices, subLabel: subLabel, loadPrefs: loadPrefs, setSubPref: setSubPref, remember: remember,
    prefer: prefer, byId: byId, langName: langName, NOTE_HARD: NOTE_HARD, NOTE_NONE: NOTE_NONE
  };
})(window);
