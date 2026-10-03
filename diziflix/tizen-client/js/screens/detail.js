/* screens/detail.js - detay ekrani: backdrop, meta, aksiyonlar; dizide SOLDA dikey sezon listesi +
   SAGDA secili sezonun bolumleri (pencereli/sanal liste), benzerler.
   Oynat -> DZ.playflow (yukleme modali; siyah player ekrani yok).
   Odak: sezon/bolum satirlari nav'da tek ogeli satirlardir; yon tuslarini bu ekran yonetir
   (Sag: sezon->bolum, Sol: bolum->sezon, Yukari/Asagi liste icinde).
   Hidrasyon: yanit `hydrating: true` ise "Detaylar yukleniyor…" yer tutucusu + DZ.hydrate izleyicisi (js/hydrate_watch.js);
   `ready`/`no_source`/`unavailable` gelince ayni id aciksa yeni veriyle yeniden cizilir, odak (satir + oge anahtari) korunur.
   "Kaynak yok": `availability.state=='unavailable'` + `reason=='no_video_source'` bir getirme HATASI degildir -> "Bu dizi/film icin henuz izleme
   kaynagi yok." ("Bolumler su an alinamadi" yalniz gercek getirme hatasinda: hidrasyon bitti ama sezon yok ve kaynak-yok isareti de yok).
   ES2017, `?.`/`??` yok. */
(function (g) {
  'use strict';
  var DZ = g.DZ = g.DZ || {};

  var EP_H = 172;            /* bolum satiri adimi (satir 162 + bosluk 10) */
  var SEASON_H = 124;        /* sezon satiri adimi (satir 114 + bosluk 10) */
  var VIEW_MAX = 900;        /* 1080 yuksekliginde liste goruntu alani en cok bu kadar yuksek (= sahne yuksekligi - VIEW_RESERVE) */
  var WIN_ABOVE = 3;         /* goruntu alaninin uste/altina ek DOM satiri */
  var WIN_BELOW = 3;
  var PAGE_ANCHOR = 96;      /* liste alani ekranda ustten bu kadar asagida durur */
  var BASE_STAGE_H = 1080;
  /* liste alani disinda kalan dikey pay: ustte PAGE_ANCHOR (96) + baslik (60, css .br-title) + alt bosluk (24). 1080'de VIEW_MAX (900) verir. */
  var VIEW_RESERVE = BASE_STAGE_H - VIEW_MAX;
  var SEASON_DEBOUNCE = 150; /* sezon odagi degisince bolum listesi gecisi (ms) */
  var DONE_PCT = 92;         /* bu yuzdeden sonrasi "izlendi" */
  var HYDRATING_TEXT = 'Detaylar yükleniyor…';
  var NO_EPISODES_TEXT = 'Bu dizinin bölüm bilgileri henüz eklenmedi.';
  var FAILED_TEXT = 'Bölümler şu an alınamadı, daha sonra tekrar deneyin.';
  var NO_SOURCE_SERIES_TEXT = 'Bu dizi için henüz izleme kaynağı yok.';
  var NO_SOURCE_MOVIE_TEXT = 'Bu film için henüz izleme kaynağı yok.';
  var MONTHS = ['Oca', 'Şub', 'Mar', 'Nis', 'May', 'Haz', 'Tem', 'Ağu', 'Eyl', 'Eki', 'Kas', 'Ara'];

  var container = null;
  var state = null;
  var memory = {};           /* yapim id -> {epId}: oynatici/geri donusunde son bolum */

  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }
  function mk(tag, cls, txt) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (txt !== undefined && txt !== null) n.textContent = txt;
    return n;
  }
  function mmss(sec) {
    var s = Math.max(0, Math.round(sec || 0));
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return h > 0 ? (h + ' sa ' + m + ' dk') : (m + ' dk');
  }

  var SOURCE_MAX = 4;        /* detayda en cok bu kadar site etiketi, kalani "+N" */
  function sourceLabel(list) {
    if (!list || !list.length) return null;
    var names = [];
    for (var i = 0; i < list.length; i++) {
      var n = list[i] && (list[i].name || list[i].id);
      if (n) names.push(String(n));
    }
    if (!names.length) return null;
    var box = mk('div', 'detail-sources');
    box.appendChild(mk('span', 'src-label', 'Kaynak:'));
    names.slice(0, SOURCE_MAX).forEach(function (n) { box.appendChild(mk('span', 'src-tag', n)); });
    if (names.length > SOURCE_MAX) box.appendChild(mk('span', 'src-tag more', '+' + (names.length - SOURCE_MAX)));
    return box;
  }

  function truncate(text, limit) {
    var value = String(text || '').replace(/\s+/g, ' ').trim();
    if (value.length <= limit) return value;
    var cut = value.slice(0, limit - 1).replace(/\s+\S*$/, '');
    return (cut || value.slice(0, limit - 1)) + '…';
  }

  /* ---------- tarih / bolum durumu ---------- */
  function pad2(v) { return v < 10 ? '0' + v : String(v); }
  function todayIso() {
    var d = new Date();
    return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
  }
  function validIso(v) { return typeof v === 'string' && /^\d{4}-\d{2}-\d{2}/.test(v); }
  function fmtDate(v) {
    if (!validIso(v)) return '';
    return parseInt(v.slice(8, 10), 10) + ' ' + (MONTHS[parseInt(v.slice(5, 7), 10) - 1] || '') + ' ' + v.slice(0, 4);
  }

  /* 'unaired' (yayin tarihi gelecekte ve kaynak hazir degil) | 'unavailable' | 'check' | 'ready'.
     Sunucunun availability.state alani esastir; tarih yalnizca "yakinda" isareti icin. */
  function epState(ep, today) {
    var a = ep && ep.availability ? ep.availability.state : '';
    if (validIso(ep.air_date) && ep.air_date.slice(0, 10) > today && a !== 'ready') return 'unaired';
    if (a === 'unavailable') return 'unavailable';
    if (a === 'check_required') return 'check';
    return 'ready';
  }
  function epPlayable(ep, today) { var st = epState(ep, today); return st === 'ready' || st === 'check'; }
  function epDone(ep) { return !!(ep.progress && ep.progress.pct >= DONE_PCT); }

  /* Sezona gecince bolum odagi: oynatilabilir ILK izlenmemis bolum; hepsi izlendiyse ilk oynatilabilir; yoksa 0. */
  function targetEpisodeIndex(eps, today) {
    var firstPlayable = -1;
    for (var i = 0; i < eps.length; i++) {
      if (!epPlayable(eps[i], today)) continue;
      if (firstPlayable < 0) firstPlayable = i;
      if (!epDone(eps[i])) return i;
    }
    if (firstPlayable >= 0) return firstPlayable;
    return eps.length ? 0 : -1;
  }

  /* Sezonlar numara sirasinda; 0. sezon (ozel bolumler) sona. Sunucu dizisi degistirilmez. */
  function sortSeasons(list) {
    var out = (list || []).slice();
    out.sort(function (a, b) {
      var ra = a.season === 0 ? 1e9 : (a.season || 0);
      var rb = b.season === 0 ? 1e9 : (b.season || 0);
      return ra - rb;
    });
    return out;
  }

  function seasonLabel(s) {
    if (s.season === 0) return 'Özel Bölümler';
    return s.title || ((s.season || 0) + '. Sezon');
  }
  function episodeCount(s) {
    return s.episode_count || (s.episodes ? s.episodes.length : 0) || 0;
  }

  /* bolum id'si (ya da ":sN:eM" son eki) -> {s: sezon dizini, e: bolum dizini} */
  function findEpisode(seasons, epId) {
    if (!epId) return null;
    var si, ei, eps;
    for (si = 0; si < seasons.length; si++) {
      eps = seasons[si].episodes || [];
      for (ei = 0; ei < eps.length; ei++) if (eps[ei].id === epId) return { s: si, e: ei };
    }
    var m = /:s(\d+):e(\d+)$/i.exec(String(epId));
    if (m) {
      var sn = parseInt(m[1], 10), en = parseInt(m[2], 10);
      for (si = 0; si < seasons.length; si++) {
        if (seasons[si].season !== sn) continue;
        eps = seasons[si].episodes || [];
        for (ei = 0; ei < eps.length; ei++) if (eps[ei].episode === en) return { s: si, e: ei };
      }
    }
    return null;
  }

  /* Odakli satir goruntu alani icinde kalsin: ustten en az 1, alttan en az 2 satir bosluk. */
  function fitScroll(scroll, idx, pitch, viewH, total) {
    var top = idx * pitch;
    var minY = top - (viewH - 2 * pitch);
    var maxY = top - pitch;
    if (scroll < minY) scroll = minY; else if (scroll > maxY) scroll = maxY;
    var maxScroll = Math.max(0, total * pitch - viewH);
    if (scroll > maxScroll) scroll = maxScroll;
    if (scroll < 0) scroll = 0;
    return scroll;
  }
  function clampScroll(scroll, pitch, viewH, total) {
    var maxScroll = Math.max(0, total * pitch - viewH);
    return Math.max(0, Math.min(maxScroll, scroll));
  }

  /* sahne yuksekligi (tasarim birimi): js/stage.js; yoksa (test/eski) 1080 */
  function stageH() { return (g.DZ && g.DZ.stage && g.DZ.stage.h) || BASE_STAGE_H; }

  /* liste goruntu alani ust siniri: sahne yuksekligine bagli (1080 -> 900, 1200 -> 1020); en az 3 bolum satiri */
  function viewMax(h) { return Math.max(EP_H * 3, (h || stageH()) - VIEW_RESERVE); }

  /* goruntu alani yuksekligi: icerik ihtiyaci (en uzun sezon / sezon sayisi) ust sinirla kirpilir, en az bir satir */
  function computeViewH(need, h) { return Math.max(EP_H, Math.min(viewMax(h), need)); }

  function imgUrl(path, w, h) {
    if (!path) return '';
    return DZ.card.sizedTo ? DZ.card.sizedTo(path, w, h) : DZ.card.sized(path, w, h);
  }

  function rowIndex(rowId, prefix) {
    if (!rowId || rowId.indexOf(prefix) !== 0) return -1;
    var n = parseInt(rowId.slice(prefix.length), 10);
    return isNaN(n) ? -1 : n;
  }

  /* ---------- eylemler (actions[]) ---------- */
  function isArr(v) { return Object.prototype.toString.call(v) === '[object Array]'; }

  /* "S04 B02" (ozel bolum: "Ozel B02") */
  function epTag(season, episode) {
    var b = 'B' + pad2(episode || 0);
    return season === 0 ? ('Özel ' + b) : ('S' + pad2(season || 0) + ' ' + b);
  }

  /* bolum id'si -> "S04 B02": once listedeki kayittan, yoksa ":sN:eM" ekinden */
  function episodeTag(seasons, epId) {
    var pos = findEpisode(seasons, epId);
    if (pos) {
      var sn = seasons[pos.s], ep = sn.episodes[pos.e];
      return epTag(ep.season !== undefined && ep.season !== null ? ep.season : sn.season, ep.episode);
    }
    var m = /:s(\d+):e(\d+)$/i.exec(String(epId || ''));
    return m ? epTag(parseInt(m[1], 10), parseInt(m[2], 10)) : '';
  }

  /* ilk oynatilabilir bolum: once normal sezonlar (numara sirasi), ozel bolumler en sona */
  function firstPlayableEpisode(seasons, today) {
    var list = sortSeasons(seasons);
    for (var i = 0; i < list.length; i++) {
      var eps = list[i].episodes || [];
      for (var j = 0; j < eps.length; j++) if (epPlayable(eps[j], today)) return eps[j];
    }
    return null;
  }

  /* sunucu "izleme kaynagi yok" diyor (hata degil) */
  function noSource(d) {
    var a = d && d.availability;
    return !!(a && a.state === 'unavailable' && a.reason === 'no_video_source');
  }
  function noSourceText(d) { return d && d.type === 'series' ? NO_SOURCE_SERIES_TEXT : NO_SOURCE_MOVIE_TEXT; }

  function hasActionKind(d, kind) {
    var list = isArr(d.actions) ? d.actions : [];
    for (var i = 0; i < list.length; i++) if (list[i] && list[i].kind === kind) return true;
    return false;
  }

  /* 'live' (canli fragman) | 'dead' (fragman kaydi var ama oynatilamaz) | 'none' (hic kayit yok: dugme cizilmez).
     Sunucu yalniz fragman kaynagi olan yapimlarda availability.trailer ('ok'|'dead'|'unknown') yollar. */
  function trailerState(d) {
    var a = d.availability || {};
    if (a.trailer === 'dead') return 'dead';
    if (a.has_trailer === true || d.playback === 'trailer' || hasActionKind(d, 'play_trailer')) return 'live';
    if (typeof a.trailer === 'string' && a.trailer) return 'dead';
    return 'none';
  }

  /* Detay eylem dugmeleri: {kind, label, epId, primary, disabled}. actions[] varsa ONDAN (sunucu hedefi belirler),
     yoksa (eski sunucu) eski alanlardan (resume/progress/playback/availability). */
  function actionButtons(d, today) {
    var out = [];
    var status = d.availability || {};
    var seasons = d.seasons || [];
    var series = d.type === 'series';
    var acts = isArr(d.actions) ? d.actions : null;
    var play = null;
    var i;
    if (acts) {
      for (i = 0; i < acts.length && !play; i++) {
        var k = acts[i] && acts[i].kind;
        if (k === 'resume_movie' || k === 'play_movie' || k === 'resume_episode' || k === 'play_episode') play = acts[i];
      }
    } else if (!series) {
      if (status.state !== 'unavailable' && d.playback !== 'unavailable' && d.playback !== 'trailer') {
        var resumable = (d.progress && d.progress.pct > 0 && d.progress.pct < DONE_PCT) || (d.resume && d.resume.position > 0);
        play = { kind: resumable ? 'resume_movie' : 'play_movie', episode_id: null };
      }
    } else if (seasons.length && status.state !== 'unavailable') {
      var rid = d.resume && d.resume.episode_id;
      var pos = findEpisode(sortSeasons(seasons), rid);
      var target = null;
      if (pos) { var cand = sortSeasons(seasons)[pos.s].episodes[pos.e]; if (epPlayable(cand, today)) target = cand; }
      if (!target) target = firstPlayableEpisode(seasons, today);
      if (target) {
        var pr = d.progress;
        var again = !!(pr && pr.episode_id === target.id && pr.pct > 0 && pr.pct < DONE_PCT);
        play = { kind: again ? 'resume_episode' : 'play_episode', episode_id: target.id };
      }
    }
    if (play) {
      var isEp = play.kind === 'resume_episode' || play.kind === 'play_episode';
      var resume = play.kind === 'resume_movie' || play.kind === 'resume_episode';
      var label = resume ? 'Devam Et' : (status.state === 'check_required' && !isEp ? 'Yeniden Dene' : 'Oynat');
      var tag = isEp ? episodeTag(seasons, play.episode_id) : '';
      if (tag) label += ' · ' + tag;
      out.push({ kind: play.kind, label: label, epId: play.episode_id || (isEp ? null : d.id), primary: true });
      if (play.kind === 'resume_episode') {
        var first = firstPlayableEpisode(seasons, today);
        if (first && first.id !== play.episode_id) {
          out.push({ kind: 'play_episode', label: 'İlk Bölümden Başla', epId: first.id, primary: false });
        }
      }
    }
    var ts = trailerState(d);
    if (ts === 'live') out.push({ kind: 'play_trailer', label: 'Fragmanı Oynat', epId: null });
    else if (ts === 'dead') out.push({ kind: 'trailer_dead', label: 'Fragman yok', epId: null, disabled: true });
    return out;
  }

  function runAction(b) {
    if (!b) return;
    if (b.kind === 'trailer_dead') {
      if (DZ.toast) DZ.toast.show('Bu yapımın fragmanı artık izlenemiyor.');
      return;
    }
    if (b.kind === 'play_trailer') { playEpisode(null, 'trailer'); return; }
    playEpisode(b.epId, 'video');
  }

  /* ---------- oynatma ---------- */
  function playEpisode(epId, kind) {
    var d = state.data;
    DZ.playflow.start({
      itemId: d.id,
      episodeId: epId || null,
      kind: kind || 'video',
      title: d.title,
      type: d.type,
      detail: d
    });
  }

  function activateEpisode(ep, st) {
    if (st === 'unaired') {
      var when = fmtDate(ep.air_date);
      DZ.modal.text('Henüz yayınlanmadı', when ? (when + ' tarihinde yayınlanacak.') : 'Bu bölüm henüz yayınlanmadı.');
      return;
    }
    if (st === 'unavailable') {
      DZ.modal.text('Bölüm kaynağı yok', 'Bu bölümün izleme kaynağı henüz mevcut değil.');
      return;
    }
    playEpisode(ep.id);
  }

  /* ---------- bolum satiri ---------- */
  function episodeNode(ep, idx) {
    var st = epState(ep, state.today);
    var wrap = mk('div', 'ep-row');
    wrap.style.top = (idx * EP_H) + 'px';
    wrap.setAttribute('data-nav-row', 'ep_' + idx);
    wrap.setAttribute('data-nav-noscroll', '1');

    var n = mk('div', 'ep' + (st === 'unaired' ? ' unaired' : ''));
    n.setAttribute('data-nav', '1');
    n.setAttribute('data-focus-key', ep.id || ('episode-' + idx));

    var still = mk('div', 'ep-still');
    /* has_still === false: gercek gorsel yok -> /img uretilmis yer tutucusunu indirme, yerel yer tutucu goster */
    var src = ep.has_still === false ? '' : imgUrl(ep.still_url || ep.still, 320, 180);
    if (src) {
      var img = document.createElement('img');
      img.width = 240; img.height = 135; img.alt = '';
      img.onload = function () { img.className = 'ready'; };
      img.onerror = function () {
        if (img.parentNode) img.parentNode.removeChild(img);
        still.className = 'ep-still noimg';
      };
      img.src = src;
      still.appendChild(img);
    } else {
      still.className = 'ep-still noimg';
    }
    if (ep.progress && ep.progress.pct > 0) {
      var pr = mk('div', 'ep-prog');
      var i = document.createElement('i');
      i.style.width = Math.min(100, ep.progress.pct) + '%';
      pr.appendChild(i);
      still.appendChild(pr);
    }
    n.appendChild(still);

    var info = mk('div', 'ep-info');
    var head = mk('div', 'ep-head');
    head.appendChild(mk('div', 'ep-num', String(ep.episode || idx + 1)));
    head.appendChild(mk('div', 'ep-title', ep.title || ''));
    if (st === 'unaired') {
      head.appendChild(mk('div', 'ep-flag soon', 'Yakında' + (fmtDate(ep.air_date) ? ' · ' + fmtDate(ep.air_date) : '')));
    } else if (st === 'unavailable') {
      head.appendChild(mk('div', 'ep-flag off', 'Kaynak yok'));
    } else if (st === 'check') {
      head.appendChild(mk('div', 'ep-flag check', 'Kaynak kontrol ediliyor'));
    }
    info.appendChild(head);
    info.appendChild(mk('div', 'ep-ov', truncate(ep.overview, 180)));
    var meta = [];
    if (ep.runtime) meta.push(ep.runtime + ' dk');
    if (st !== 'unaired' && fmtDate(ep.air_date)) meta.push(fmtDate(ep.air_date));
    if (meta.length) info.appendChild(mk('div', 'ep-meta', meta.join(' · ')));
    n.appendChild(info);

    n.addEventListener('click', function () { activateEpisode(ep, st); }, false);
    wrap.appendChild(n);
    return wrap;
  }

  /* ---------- sezon satiri ---------- */
  function seasonNode(s, idx) {
    var wrap = mk('div', 'season-row');
    wrap.style.top = (idx * SEASON_H) + 'px';
    wrap.setAttribute('data-nav-row', 'season_' + idx);
    wrap.setAttribute('data-nav-noscroll', '1');

    var n = mk('div', 'season');
    n.setAttribute('data-nav', '1');
    n.setAttribute('data-focus-key', 'season:' + (s.season === undefined ? idx : s.season));
    /* Yalnizca GERCEK sezon posteri varsa poster alani olusur; yoksa satir yalnizca metindir
       (has_poster=false iken poster_url dizinin afisidir: gosterilmez). */
    var posterSrc = (s.has_poster === true && s.poster_url) ? imgUrl(s.poster_url, 200, 300) : '';
    var posterBox = null;
    if (posterSrc) {
      posterBox = mk('div', 'season-poster');
      n.appendChild(posterBox);
    }
    var txt = mk('div', 'season-txt');
    txt.appendChild(mk('div', 'season-name', seasonLabel(s)));
    txt.appendChild(mk('div', 'season-count', episodeCount(s) + ' bölüm'));
    n.appendChild(txt);

    n.dzPosterLoaded = false;
    n.dzLoadPoster = function () {
      if (!posterBox || n.dzPosterLoaded) return;
      n.dzPosterLoaded = true;
      var img = document.createElement('img');
      img.width = 68; img.height = 102; img.alt = '';
      img.onload = function () { img.className = 'ready'; };
      img.onerror = function () { if (img.parentNode) img.parentNode.removeChild(img); };
      img.src = posterSrc;
      posterBox.appendChild(img);
    };
    n.addEventListener('click', function () { enterEpisodes(); }, false);
    wrap.appendChild(n);
    return wrap;
  }

  /* ---------- sezon/bolum tarayicisi ---------- */
  function applyEpScroll() {
    state.eList.style.transform = 'translate3d(0,' + (-Math.round(state.epScroll)) + 'px,0)';
  }
  function applySeasonScroll() {
    state.sList.style.transform = 'translate3d(0,' + (-Math.round(state.sScroll)) + 'px,0)';
  }

  /* Pencereli render: yalnizca goruntu alani +- WIN_* satir DOM'da (1000+ bolumde TV donmaz).
     DOM sirasi indeks sirasidir. Degisiklik olduysa true. */
  function renderEpisodes() {
    var eps = state.eps || [];
    var total = eps.length;
    if (!total) return false;
    var first = Math.max(0, Math.floor(state.epScroll / EP_H) - WIN_ABOVE);
    var last = Math.min(total - 1, Math.ceil((state.epScroll + state.viewH) / EP_H) - 1 + WIN_BELOW);
    var nodes = state.epNodes;
    var changed = false;
    var keys = Object.keys(nodes);
    var k, i;
    for (k = 0; k < keys.length; k++) {
      var have = parseInt(keys[k], 10);
      if (have < first || have > last) {
        var old = nodes[keys[k]];
        if (old.parentNode) old.parentNode.removeChild(old);
        delete nodes[keys[k]];
        changed = true;
      }
    }
    for (i = first; i <= last; i++) {
      if (nodes[i]) continue;
      var node = episodeNode(eps[i], i);
      nodes[i] = node;
      var ref = null;
      for (var j = i + 1; j <= last; j++) { if (nodes[j]) { ref = nodes[j]; break; } }
      if (ref) state.eList.insertBefore(node, ref); else state.eList.appendChild(node);
      changed = true;
    }
    return changed;
  }

  /* bolum odagi icin kaydir + pencereyi guncelle; satirlar degistiyse true (nav.refresh gerekir) */
  function fitEpisode(idx) {
    var total = state.eps ? state.eps.length : 0;
    if (!total) return false;
    var ns = fitScroll(state.epScroll, idx, EP_H, state.viewH, total);
    var moved = ns !== state.epScroll;
    state.epScroll = ns;
    var changed = renderEpisodes();
    if (moved) applyEpScroll();
    return changed;
  }

  function fitSeason(idx) {
    var total = state.seasons.length;
    var ns = fitScroll(state.sScroll, idx, SEASON_H, state.viewH, total);
    if (ns !== state.sScroll) { state.sScroll = ns; applySeasonScroll(); }
  }

  function loadPosters(center) {
    var from = Math.max(0, center - 8), to = Math.min(state.sNodes.length - 1, center + 8);
    for (var i = from; i <= to; i++) state.sNodes[i].dzLoadPoster();
  }

  /* GORSEL vurgu (.active = soluk altin): debounce'u BEKLEMEZ. Odak sezon satirlarindayken (ya da bekleyen
     sezon gecisi varken) odaktaki satira hemen tasinir; boylece eski satir canli sariden dogrudan koyuya,
     yenisi koyudan dogrudan canli sariya (CSS gecisiyle) doner, arada soluk altin duraksamasi olmaz.
     Bolumlere gecince gercekte gosterilen sezonda (state.shown) kalir. state.shown anlami degismez. */
  function updateSeasonActive() {
    var vis = (state.inSeasons || state.seasonTimer) && state.seasonFocus !== undefined ? state.seasonFocus : state.shown;
    state.activeVisual = vis;
    for (var i = 0; i < state.sNodes.length; i++) {
      var n = state.sNodes[i];
      if (i === vis) n.classList.add('active'); else n.classList.remove('active');
    }
  }

  /* Sagdaki bolum listesini si. sezona gecir; bolum odagi (epIdx) hedef bolume, liste hedefte baslar. */
  function showSeason(si, forcedEpIdx) {
    var s = state.seasons[si];
    if (!s) return;
    var eps = s.episodes || [];
    state.shown = si;
    state.eps = eps;
    if (typeof forcedEpIdx === 'number' && forcedEpIdx >= 0 && forcedEpIdx < eps.length) state.epIdx = forcedEpIdx;
    else state.epIdx = targetEpisodeIndex(eps, state.today);
    updateSeasonActive();
    clear(state.eList);
    state.epNodes = {};
    state.eList.style.height = (eps.length * EP_H) + 'px';
    state.epScroll = state.epIdx >= 0 ? clampScroll(state.epIdx * EP_H, EP_H, state.viewH, eps.length) : 0;
    renderEpisodes();
    /* yeni sezonun listesi eski kaydirma konumundan "ucarak" gelmesin: gecisi kapat, yerlestir, geri ac */
    state.eList.style.transition = 'none';
    applyEpScroll();
    void state.eList.offsetHeight;
    state.eList.style.transition = '';
    if (state.emptyEl) {
      if (eps.length) state.emptyEl.classList.add('hidden'); else state.emptyEl.classList.remove('hidden');
    }
  }

  function scheduleSeason(si) {
    if (state.seasonTimer) { clearTimeout(state.seasonTimer); state.seasonTimer = null; }
    if (si === state.shown) return;
    var own = state;
    state.seasonTimer = setTimeout(function () {
      if (state !== own || !own.alive) return;
      own.seasonTimer = null;
      showSeason(si);
      DZ.nav.refresh();
    }, SEASON_DEBOUNCE);
  }

  function pageToBrowser() {
    if (!state || !state.page || !state.browserEl) return;
    var y = (state.browserEl.offsetTop || 0) - PAGE_ANCHOR;
    if (y < 0) y = 0;
    state.page.style.transform = 'translate3d(0,' + (-Math.round(y)) + 'px,0)';
  }

  function remember() {
    if (!state || !state.eps || state.epIdx < 0 || !state.eps[state.epIdx]) return;
    memory[state.id] = { epId: state.eps[state.epIdx].id };
  }

  function focusSeason(i) {
    if (i < 0 || i >= state.seasons.length) return false;
    return DZ.nav.focusRowById('season_' + i, 0);
  }

  function focusEpisode(idx) {
    if (!state || !state.eps || idx < 0 || idx >= state.eps.length) return false;
    state.epIdx = idx;
    if (fitEpisode(idx)) DZ.nav.refresh();
    return DZ.nav.focusRowById('ep_' + idx, 0);
  }

  /* Sag / Enter: sezon listesinden bolum listesine. Bekleyen sezon gecisi (debounce) hemen uygulanir. */
  function enterEpisodes() {
    if (!state || !state.browser) return;
    if (state.seasonTimer) {
      clearTimeout(state.seasonTimer);
      state.seasonTimer = null;
      if (state.seasonFocus !== undefined && state.seasonFocus !== state.shown) {
        showSeason(state.seasonFocus);
        DZ.nav.refresh();
      }
    }
    if (state.eps && state.eps.length) focusEpisode(state.epIdx >= 0 ? state.epIdx : 0);
  }

  function focusAbove() {
    var ids = DZ.nav.rowIds();
    if (ids.indexOf('overview') >= 0) return DZ.nav.focusRowById('overview', 0);
    return DZ.nav.focusRowById('actions', 0);
  }
  function focusBelow() {
    if (DZ.nav.rowIds().indexOf('similar') >= 0) return DZ.nav.focusRowById('similar');
    return false;
  }

  function onFocus(info) {
    if (!state) return;
    var id = info.rowId;
    if (state.browser) {
      var sf = rowIndex(id, 'season_');
      state.inSeasons = sf >= 0;
      if (sf >= 0) state.seasonFocus = sf;
      updateSeasonActive();
    }
    if (id === 'similar') {
      var sec = container.querySelector('[data-row-id="similar"]');
      if (sec && sec.dzUpdateWindow) sec.dzUpdateWindow(info.col);
      return;
    }
    if (!state.browser) return;
    var si = rowIndex(id, 'season_');
    if (si >= 0) {
      pageToBrowser();
      fitSeason(si);
      loadPosters(si);
      scheduleSeason(si);
      return;
    }
    var ei = rowIndex(id, 'ep_');
    if (ei >= 0) {
      state.epIdx = ei;
      pageToBrowser();
      if (fitEpisode(ei)) DZ.nav.refresh();
      remember();
    }
  }

  function key(ev) {
    if (!state || !state.browser) return false;
    var cur = DZ.nav.current();
    var id = cur ? cur.rowId : '';
    var name = ev.name;
    var si = rowIndex(id, 'season_');
    if (si >= 0) {
      if (name === 'up') { if (si > 0) focusSeason(si - 1); else focusAbove(); return true; }
      if (name === 'down') { if (si < state.seasons.length - 1) focusSeason(si + 1); else focusBelow(); return true; }
      if (name === 'right') { enterEpisodes(); return true; }
      if (name === 'left') return true;
      return false;
    }
    var ei = rowIndex(id, 'ep_');
    if (ei >= 0) {
      if (name === 'up') { if (ei > 0) focusEpisode(ei - 1); else focusAbove(); return true; }
      if (name === 'down') { if (ei < state.eps.length - 1) focusEpisode(ei + 1); else focusBelow(); return true; }
      if (name === 'left') { focusSeason(state.shown); return true; }
      if (name === 'right') return true;
      return false;
    }
    if (id === 'similar' && name === 'up') {
      if (state.eps && state.eps.length) { focusEpisode(state.epIdx >= 0 ? state.epIdx : 0); return true; }
      return focusSeason(state.shown);
    }
    if (name === 'down') {
      var ids = DZ.nav.rowIds();
      var at = ids.indexOf(id);
      if (at >= 0 && ids[at + 1] && rowIndex(ids[at + 1], 'season_') >= 0) {
        focusSeason(state.shown);
        return true;
      }
    }
    return false;
  }

  function buildBrowser(d, page) {
    var seasons = sortSeasons(d.seasons);
    state.seasons = seasons;
    state.today = todayIso();
    var maxEps = 0;
    for (var m = 0; m < seasons.length; m++) {
      var c = seasons[m].episodes ? seasons[m].episodes.length : 0;
      if (c > maxEps) maxEps = c;
    }
    state.viewNeed = Math.max(maxEps * EP_H, seasons.length * SEASON_H);
    state.viewH = computeViewH(state.viewNeed);

    var br = mk('div', 'browser');
    state.browserEl = br;

    var left = mk('div', 'br-col br-seasons');
    left.appendChild(mk('h2', 'br-title', 'Sezonlar'));
    var sView = mk('div', 'br-view');
    state.sView = sView;
    sView.style.height = state.viewH + 'px';
    var sList = mk('div', 'br-list');
    sList.style.height = (seasons.length * SEASON_H) + 'px';
    state.sList = sList;
    state.sNodes = [];
    state.sScroll = 0;
    for (var i = 0; i < seasons.length; i++) {
      var wrap = seasonNode(seasons[i], i);
      sList.appendChild(wrap);
      state.sNodes.push(wrap.firstChild);
    }
    sView.appendChild(sList);
    left.appendChild(sView);

    var right = mk('div', 'br-col br-episodes');
    right.appendChild(mk('h2', 'br-title', 'Bölümler'));
    var eView = mk('div', 'br-view');
    state.eView = eView;
    eView.style.height = state.viewH + 'px';
    var eList = mk('div', 'br-list');
    state.eList = eList;
    state.epNodes = {};
    state.epScroll = 0;
    eView.appendChild(eList);
    state.emptyEl = mk('div', 'br-empty hidden', 'Bu sezonda bölüm yok');
    eView.appendChild(state.emptyEl);
    right.appendChild(eView);

    br.appendChild(left);
    br.appendChild(right);
    page.appendChild(br);
    state.browser = true;

    /* ilk secim: bolum karti/geri donus -> o bolumun sezonu; yoksa devam bolumu; yoksa ilk sezon */
    var pos = findEpisode(seasons, state.initEpId);
    state.initEpFocus = !!(pos && state.focusInitEp);
    if (!pos) {
      pos = findEpisode(seasons, d.resume && d.resume.episode_id) ||
            findEpisode(seasons, d.progress && d.progress.episode_id);
    }
    state.inSeasons = false;
    if (pos) showSeason(pos.s, pos.e); else showSeason(0);
    state.seasonFocus = state.shown;
    updateSeasonActive();
    loadPosters(state.shown);
  }

  function toggleMyList(btn) {
    var pid = DZ.api.profileId();
    var inList = btn.dzInList === true;
    var p = inList ? DZ.api.removeMyList(pid, state.data.id) : DZ.api.addMyList(pid, state.data.id);
    p.then(function () {
      btn.dzInList = !inList;
      btn.textContent = btn.dzInList ? 'Listemden Çıkar' : 'Listeme Ekle';
    }, function (e) {
      console.log('[detail] mylist hata: ' + e.message);
      if (DZ.toast) DZ.toast.show('Listem güncellenemedi: ' + (e && e.message ? e.message : 'bağlantı hatası'));
    });
  }

  function build(d) {
    clear(container);
    var page = mk('div', 'detail');
    page.id = 'page';
    state.page = page;
    state.browser = false;

    /* hero */
    var hero = mk('div', 'detail-hero');
    var bg = mk('div', 'detail-bg');
    hero.appendChild(bg);
    var src = DZ.card.sized(d.backdrop, 1280, 720);
    if (src) {
      var pre = new Image();
      pre.onload = function () {
        bg.style.backgroundImage = 'url("' + src + '")';
        bg.classList.add('ready');
        /* backdrop yuklenince layout kayarsa odagi KAYBETME: hicbir odak yoksa Oynat'a geri ver */
        if (state && state.alive && !container.querySelector('.focused')) {
          DZ.nav.focusRowById('actions', 0);
          console.log('[detail] backdrop onload -> odak yeniden verildi (actions)');
        }
      };
      pre.src = src;
    }
    hero.appendChild(mk('div', 'detail-fade'));

    /* fare/dokunma icin sol ust "← Geri" (Geri tusuyla AYNI islem: DZ.app.goBack; data-nav yok -> kumanda odak sirasina girmez;
       gidilecek yer yoksa null -> gosterilmez). Hero'nun son cocugu: backdrop/golge ustunde. */
    var backBtn = DZ.navigation && DZ.navigation.backButton ? DZ.navigation.backButton() : null;
    if (backBtn) hero.appendChild(backBtn);

    var body = mk('div', 'detail-body');
    body.appendChild(mk('div', 'detail-title', d.title || ''));

    var meta = mk('div', 'detail-meta');
    var bits = [];
    if (d.year) bits.push(String(d.year));
    if (d.genres && d.genres.length) bits.push(d.genres.slice(0, 3).join(', '));
    if (d.country) bits.push(String(d.country));
    if (d.runtime) bits.push(d.type === 'series' ? (d.runtime + ' dk / bolum') : mmss(d.runtime * 60));
    if (d.followers) bits.push(String(d.followers) + ' takipçi');
    for (var bi = 0; bi < bits.length; bi++) {
      if (bi) meta.appendChild(mk('span', 'dot', '·'));
      meta.appendChild(mk('span', null, bits[bi]));
    }
    if (d.rating) {
      meta.appendChild(mk('span', 'dot', '·'));
      meta.appendChild(mk('span', 'score', 'Puan ' + d.rating));
    }
    body.appendChild(meta);
    /* "Kaynak: A · B": icerigin geldigi site(ler) (sunucu `source_names`; yoksa/bos ise hic cizilmez - eski sunucu). Salt metin, odaklanmaz. */
    var srcRow = sourceLabel(d.source_names);
    if (srcRow) body.appendChild(srcRow);
    var status = d.availability || {};
    if (status.state === 'unavailable') {
      var emptySeriesNow = d.type === 'series' && !(d.seasons && d.seasons.length);
      if (status.has_trailer) body.appendChild(mk('p', 'detail-meta', 'Tam izleme kaynağı yok · Fragman mevcut'));
      else if (noSource(d)) {
        /* bos dizide ayni cumle asagidaki "Bolumler" blogunda; burada tekrarlanmaz */
        if (!emptySeriesNow) body.appendChild(mk('p', 'detail-meta', noSourceText(d)));
      } else body.appendChild(mk('p', 'detail-meta', 'İzleme kaynağı henüz mevcut değil'));
    } else if (status.state === 'check_required') body.appendChild(mk('p', 'detail-meta', 'Kaynakta sorun bildirildi · Yeniden deneyebilirsiniz'));

    /* HIDRASYON: sunucu arka planda bolum/ozet bilgisini getiriyor. Bos sezon/bolum alani yerine yer tutucu (odaksiz);
       bos dizi icin asagidaki "Bolumler" blogunda, diger durumlarda (film / kismi sezon) burada tek satir. */
    var hydrating = d.hydrating === true;
    var emptySeries = d.type === 'series' && !(d.seasons && d.seasons.length);
    state.hydNote = null;
    state.hydEmpty = null;
    if (hydrating && !emptySeries) {
      state.hydNote = mk('p', 'detail-meta hydrating-note', HYDRATING_TEXT);
      body.appendChild(state.hydNote);
    }

    /* AKSIYON BUTONLARI: basligin/meta'nin hemen altinda, aciklamanin USTUNDE.
       Boylece uzun aciklamada odak butona inince sayfa kaymaz, BASLIK gorunur kalir. */
    var act = mk('div', 'detail-actions');
    act.setAttribute('data-nav-row', 'actions');

    state.today = todayIso();
    var buttons = actionButtons(d, state.today);
    for (var abi = 0; abi < buttons.length; abi++) {
      (function (spec) {
        var node = mk('div', 'btn' + (spec.primary ? ' primary' : '') + (spec.disabled ? ' disabled' : ''), spec.label);
        node.setAttribute('data-nav', '1');
        node.setAttribute('data-focus-key', 'act:' + spec.kind);   /* yeniden cizimde ayni dugmeye odak */
        if (spec.disabled) node.setAttribute('aria-disabled', 'true');
        node.addEventListener('click', function () { runAction(spec); }, false);
        act.appendChild(node);
      })(buttons[abi]);
    }

    var bList = mk('div', 'btn', d.in_mylist ? 'Listemden Çıkar' : 'Listeme Ekle');
    bList.setAttribute('data-nav', '1');
    bList.setAttribute('data-focus-key', 'act:mylist');
    bList.dzInList = d.in_mylist === true;
    bList.addEventListener('click', function () { toggleMyList(bList); }, false);
    act.appendChild(bList);

    body.appendChild(act);

    /* ACIKLAMA: butonlarin altinda; gorunumde CSS ile 4 satira clamp edilir.
       ODAKLANABILIR (data-nav-row 'overview', actions'in hemen ALTINDA): Enter -> tam-metin modali. */
    var ovText = d.overview || '';
    if (ovText) {
      var ovRow = mk('div', null);
      ovRow.setAttribute('data-nav-row', 'overview');
      var ov = mk('div', 'detail-overview', ovText);
      ov.setAttribute('data-nav', '1');
      ov.addEventListener('click', function () {
        DZ.modal.text(d.title || '', ovText, function () {
          DZ.nav.focusRowById('overview', 0);
        });
      }, false);
      ovRow.appendChild(ov);
      ovRow.appendChild(mk('div', 'ov-more', 'Devamini oku ▸'));
      body.appendChild(ovRow);
    } else {
      body.appendChild(mk('div', 'detail-overview', ''));
    }

    /* EKIP: director/cast doluysa goster, bossa hic gosterme. */
    var cred = [];
    if (d.director) cred.push('Yonetmen: ' + d.director);
    if (d.cast && d.cast.length) cred.push('Oyuncular: ' + d.cast.slice(0, 5).join(', '));
    if (cred.length) body.appendChild(mk('div', 'detail-credits', cred.join('    ')));

    hero.appendChild(body);
    page.appendChild(hero);

    /* dizi: solda sezon listesi + sagda bolumler */
    if (d.seasons && d.seasons.length) {
      buildBrowser(d, page);
    } else if (d.type === 'series') {
      var noEpisodes = mk('div', 'episodes');
      noEpisodes.appendChild(mk('h2', null, 'Bölümler'));
      state.hydEmpty = mk('p', 'detail-meta' + (hydrating ? ' hydrating-note' : ''),
        hydrating ? HYDRATING_TEXT : (noSource(d) ? noSourceText(d) : (state.hydrateFailed ? FAILED_TEXT : NO_EPISODES_TEXT)));
      noEpisodes.appendChild(state.hydEmpty);
      page.appendChild(noEpisodes);
    }

    /* benzerler */
    if (d.similar && d.similar.length) {
      var rowsBox = mk('div', 'rows detail-similar');
      rowsBox.appendChild(DZ.row.create({
        id: 'similar',
        title: 'Benzer Yapimlar',
        loaded: true,
        items: d.similar,
        onSelect: function (it) { DZ.app.go('detail', { id: it.id }, true); }
      }));
      page.appendChild(rowsBox);
    }

    container.appendChild(page);
    /* setRoot + odak render/layout HAZIR olduktan sonra (cift rAF): TV'de erken cagride
       'Oynat' butonu 0 olcup nav disi kalabiliyor -> odak Oynat'a gitmiyordu. */
    function initNav() {
      if (!state || !state.alive || state.data !== d) return;
      DZ.nav.setRoot(container, page);
      DZ.nav.onFocus(onFocus);
      var ok;
      if (state.browser && state.initEpFocus && state.epIdx >= 0) {
        /* ana sayfa bolum karti: ilgili sezon secili, ilgili bolume odak (sayfa listeye kayar) */
        ok = focusEpisode(state.epIdx);
      } else {
        ok = DZ.nav.focusRowById('actions', 0);
      }
      if (!ok) DZ.nav.focusIndex(0, 0);   /* actions yoksa ilk odaklanabilir data-nav'a dus */
      /* karsilanamayan eski odak kaydi sonradan (pencere degisince) devreye girmesin */
      DZ.nav.restoreNext(null);
      var cur = DZ.nav.currentEl();
      console.log('[detail] focus -> ' + (cur ? (cur.textContent || cur.getAttribute('data-nav')) : 'YOK') + ' ok=' + ok);
    }
    g.requestAnimationFrame(function () { g.requestAnimationFrame(initNav); });
  }

  /* sahne yuksekligi degisti (pencere/yakinlastirma): sezon/bolum listesi goruntu alani yeniden hesaplanir,
     pencereli bolum render'i yeni viewH ile yeniden cizilir, odakli satir goruntu alaninda kalir.
     Donus: viewH degistiyse true. */
  function onResize() {
    if (!state || !state.browser || !state.eView || !state.sView) return false;
    var vh = computeViewH(state.viewNeed);
    if (vh === state.viewH) return false;
    state.viewH = vh;
    state.sView.style.height = vh + 'px';
    state.eView.style.height = vh + 'px';
    var total = state.eps ? state.eps.length : 0;
    if (total) {
      /* bolum listesi: kaydirma yeni goruntu alanina sigsin (odakli bolum varsa onu koru), pencere yeni viewH ile */
      state.epScroll = state.epIdx >= 0
        ? fitScroll(state.epScroll, state.epIdx, EP_H, vh, total)
        : clampScroll(state.epScroll, EP_H, vh, total);
      renderEpisodes();
      applyEpScroll();
    }
    if (state.seasons.length) {
      var sf = state.seasonFocus !== undefined && state.seasonFocus >= 0 ? state.seasonFocus : Math.max(0, state.shown);
      state.sScroll = fitScroll(state.sScroll, sf, SEASON_H, vh, state.seasons.length);
      applySeasonScroll();
    }
    if (DZ.nav && DZ.nav.refresh) DZ.nav.refresh();   /* pencere satirlari degisti: odak agaci + konum */
    var cur = DZ.nav && DZ.nav.current ? DZ.nav.current() : null;
    if (cur && (rowIndex(cur.rowId, 'season_') >= 0 || rowIndex(cur.rowId, 'ep_') >= 0)) pageToBrowser();
    return true;
  }

  /* ---------- hidrasyon (arka plan bilgi getirme) ---------- */
  /* Yeni veriyle bastan ciz; odak (satir + oge anahtari) ve gosterilen bolum korunur. */
  function rerender(d, failed) {
    if (!state || !state.alive) return;
    var snap = DZ.nav && DZ.nav.snapshot ? DZ.nav.snapshot() : null;
    var cur = state.eps && state.epIdx >= 0 ? state.eps[state.epIdx] : null;
    if (cur && cur.id) state.initEpId = cur.id;
    state.focusInitEp = false;
    if (state.seasonTimer) { clearTimeout(state.seasonTimer); state.seasonTimer = null; }
    state.data = d;
    state.hydrateFailed = !!failed;
    state.browser = false; state.seasons = []; state.sNodes = []; state.eps = []; state.epNodes = {};
    state.epIdx = -1; state.shown = -1;
    if (snap && DZ.nav.restoreNext) DZ.nav.restoreNext(snap);
    build(d);
  }

  /* zaman asimi / ag hatasi: toast yok, yalnizca yukleniyor yer tutucusu kalkar (yeniden cizim yok, odak bozulmaz) */
  function endPlaceholder() {
    if (!state) return;
    if (state.hydNote && state.hydNote.parentNode) state.hydNote.parentNode.removeChild(state.hydNote);
    state.hydNote = null;
    if (state.hydEmpty) {
      state.hydEmpty.textContent = noSource(state.data) ? noSourceText(state.data) : NO_EPISODES_TEXT;
      state.hydEmpty.className = 'detail-meta';
      state.hydEmpty = null;
    }
  }

  function onHydrate(evt, own) {
    if (state !== own || !own.alive || !evt || evt.id !== own.id) return;
    if ((evt.result === 'ready' || evt.result === 'no_source' || evt.result === 'unavailable') && evt.data) {
      rerender(evt.data, evt.result === 'unavailable');
    } else {
      endPlaceholder();
    }
  }

  /* ilk (tetikleyen) yanit geldi: hydrating true -> global izleyiciye al; degilse suren izleme (varsa) birakilir */
  function trackHydration(d) {
    var H = DZ.hydrate;
    if (!H || !state) return;
    if (d && d.hydrating === true) H.watch(state.id, d.title);
    else if (H.isWatching(state.id)) H.cancel(state.id);
  }

  function errorScreen(err, retry) {
    clear(container);
    var box = mk('div', 'errscreen');
    box.setAttribute('data-nav-row', 'err');
    box.appendChild(mk('h1', null, 'Detay yüklenemedi'));
    box.appendChild(mk('p', null, DZ.toast ? DZ.toast.errorText(err) : (err && err.message ? err.message : 'Bilinmeyen hata')));
    var wrap = mk('div', null);
    wrap.style.display = 'flex';
    var b1 = mk('div', 'btn primary', 'Tekrar dene');
    b1.setAttribute('data-nav', '1');
    b1.addEventListener('click', retry, false);
    wrap.appendChild(b1);
    var b2 = mk('div', 'btn', 'Geri');
    b2.setAttribute('data-nav', '1');
    b2.addEventListener('click', function () { DZ.app.back(); }, false);
    wrap.appendChild(b2);
    box.appendChild(wrap);
    container.appendChild(box);
    DZ.nav.setRoot(container, null);
  }

  var screen = {
    name: 'detail',
    /* params: {id, episodeId?}. episodeId (ana sayfa bolum karti): ilgili sezon secili + bolume odak.
       isResume (oynaticidan geri donus): son bolum/sezon hatirlanir, odak nav tarafindan geri yuklenir. */
    enter: function (cnt, params, isResume) {
      container = cnt;
      var p = params || {};
      var id = p.id || null;
      var mem = isResume && id ? memory[id] : null;
      if (!isResume && id) delete memory[id];
      state = {
        alive: true, id: id,
        initEpId: mem ? mem.epId : (p.episodeId || null),
        focusInitEp: !isResume && !!p.episodeId,
        browser: false, seasons: [], sNodes: [], eps: [], epNodes: {}, epIdx: -1, shown: -1,
        seasonTimer: null, page: null
      };
      /* Bolum kartindan gelindiyse eski odak kaydi hedef bolumun onune gecmesin. */
      if (state.focusInitEp && DZ.nav.restoreNext) DZ.nav.restoreNext(null);
      console.log('[detail] enter id=' + (state.id || '?') + (p.episodeId ? ' episode=' + p.episodeId : '') + (isResume ? ' (geri)' : ''));
      clear(container);
      container.appendChild(DZ.skeleton.detail());
      if (DZ.hydrate && DZ.hydrate.on) {
        var own = state;
        state.unhydrate = DZ.hydrate.on(function (evt) { onHydrate(evt, own); });
      }

      function load() {
        clear(container);
        container.appendChild(DZ.skeleton.detail());
        DZ.api.detail(state.id, DZ.api.profileId()).then(function (d) {
          if (!state.alive) return;
          state.data = d;
          state.hydrateFailed = false;
          trackHydration(d);
          build(d);
        }, function (err) {
          if (!state.alive) return;
          errorScreen(err, load);
        });
      }
      state.load = load;
      load();
    },
    key: key,
    back: function () {
      DZ.app.back(); return true;
    },
    exit: function () {
      if (state) {
        state.alive = false;
        if (state.seasonTimer) { clearTimeout(state.seasonTimer); state.seasonTimer = null; }
        if (state.unhydrate) { state.unhydrate(); state.unhydrate = null; }   /* izleyici SURER (global); yalniz bu ekranin dinleyicisi kalkar */
      }
      state = null; container = null;
    },
    resume: function () { if (state && state.load) state.load(); },
    onResize: onResize
  };

  /* testler icin */
  screen.helpers = {
    targetEpisodeIndex: targetEpisodeIndex, sortSeasons: sortSeasons, findEpisode: findEpisode,
    epState: epState, fmtDate: fmtDate, fitScroll: fitScroll,
    actionButtons: actionButtons, trailerState: trailerState, epTag: epTag, firstPlayableEpisode: firstPlayableEpisode,
    ROW: EP_H, SEASON_ROW: SEASON_H, DEBOUNCE: SEASON_DEBOUNCE,
    HYDRATING_TEXT: HYDRATING_TEXT, FAILED_TEXT: FAILED_TEXT, NO_EPISODES_TEXT: NO_EPISODES_TEXT,
    NO_SOURCE_SERIES_TEXT: NO_SOURCE_SERIES_TEXT, NO_SOURCE_MOVIE_TEXT: NO_SOURCE_MOVIE_TEXT, noSource: noSource, noSourceText: noSourceText,
    viewMax: viewMax, computeViewH: computeViewH, viewH: function () { return state ? state.viewH : 0; }
  };

  DZ.screens = DZ.screens || {};
  DZ.screens.detail = screen;
})(window);
