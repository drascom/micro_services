/* playflow.js - "Oynat" akisi: kullanici siyah player ekranina DUSMEZ.
   Detay (veya player) ekraninin USTUNDE tam ekran yukleme modali acilir; akislar sirayla
   ekrandan bagimsiz (detached) bir motorda denenir. Player ekrani yalnizca bir akis
   GERCEKTEN oynamaya basladiktan sonra, hazir motorla birlikte acilir.
   - akis hatasi (prepare/onerror/zaman asimi) -> o akisin hatasi /api/playback-report ile raporlanir, siradaki denenir
   - basari (oynatma gercekten basladi) -> o akisin basarisi raporlanir (token+event+engine)
   - hepsi basarisiz -> modal "Kaynak calismiyor" hatasina doner (Tekrar dene / Geri)
   - hic akis yoksa ve sunucu `finder` verdiyse (js/finder.js): "Kaynak araniyor…" (5 sn'de bir yoklanir; bulununca akis otomatik yeniden
     istenir) ya da "Bu bolum icin kaynak bulunamadi."; finder yoksa genel mesaj
   - Geri tusu denemeyi durdurur, kullanici oldugu ekranda kalir.
   Embed (iframe) akislari dogrulanamaz: son care olarak sona siralanir, player'a dogrudan devredilir.
   Kalite tercihi (Ayarlar > "En yuksek kalite", localStorage dz_pref_quality): 1080 (varsayilan) | 1440 | auto.
   Cozunurlugu (quality/label'dan ayristirilan yukseklik) tercihi ASAN dogrudan akislar, dogrudan akislarin SONUNA
   (embed'lerin onune) atilir: TV'de cok yuksek cozunurluklu mp4 once denenip takilmasin. Yukseklik ayristirilamazsa akisa dokunulmaz;
   "auto" hicbir sey degistirmez.
   Ses/altyazi: ilk denenecek akis kullanici tercihine gore one alinir (DZ.tracks.prefer; tercih yoksa Turkce soft >
   Turkce gomulu > Ingilizce soft > kapali); sunucu sirasi digerlerinde korunur. ES2017. */
(function (g) {
  'use strict';
  var DZ = g.DZ = g.DZ || {};

  var STREAM_TIMEOUT_MS = 15000;   /* bir akisin oynamaya baslamasi icin azami sure */
  var SLOW_MS = 8000;              /* deneme bu kadar surerse "Baska kaynak deneniyor…" */
  var MAX_TRIES = 6;

  var MSG_SEARCH = 'Kaynak aranıyor…';
  var MSG_PREPARE = 'Video hazırlanıyor…';
  var MSG_OTHER = 'Başka kaynak deneniyor…';

  var active = null;
  var seq = 0;

  function isEmbed(stream) {
    return DZ.player && DZ.player.isEmbedStream ? DZ.player.isEmbedStream(stream) : (stream && stream.type === 'embed');
  }

  /* akisin dikey cozunurlugu (720, 1080, 1440…): quality, sonra label; "1920x1080", "1080p", "4K"/"2K". Bilinmiyorsa 0. */
  function streamHeight(stream) {
    var fields = [stream && stream.quality, stream && stream.label];
    for (var i = 0; i < fields.length; i++) {
      var t = String(fields[i] == null ? '' : fields[i]);
      var m = /(\d{3,4})\s*[x×]\s*(\d{3,4})/i.exec(t);
      if (m) return parseInt(m[2], 10);
      m = /(?:^|[^0-9])(\d{3,4})\s*p(?![a-z])/i.exec(t);
      if (m) return parseInt(m[1], 10);
      if (/(?:^|[^a-z0-9])(4k|uhd)(?![a-z0-9])/i.test(t)) return 2160;
      if (/(?:^|[^a-z0-9])2k(?![a-z0-9])/i.test(t)) return 1440;
    }
    return 0;
  }

  /* tercih -> en yuksek yukseklik (0 = sinirsiz / dokunma) */
  function qualityCap(pref) {
    var v = String(pref == null ? '1080' : pref);
    if (v === 'auto') return 0;
    if (v === '1440') return 1440;
    return 1080;
  }

  function readQualityPref() {
    try {
      if (DZ.api && DZ.api.qualityPref) return DZ.api.qualityPref();
      var v = g.localStorage && g.localStorage.getItem('dz_pref_quality');
      return v || '1080';
    } catch (e) { return '1080'; }
  }

  /* dogrulanabilir (video dosyasi) akislar once, embed'ler sona; kendi icinde sunucu sirasi korunur.
     cap (yukseklik, 0 = yok): cap'i asan dogrudan akislar dogrudan akislarin sonuna gider. */
  function orderStreams(list, cap) {
    var direct = [], over = [], embeds = [];
    var limit = cap > 0 ? cap : 0;
    for (var i = 0; i < (list || []).length; i++) {
      var s = list[i];
      if (isEmbed(s)) embeds.push(s);
      else if (limit && streamHeight(s) > limit) over.push(s);
      else direct.push(s);
    }
    return direct.concat(over, embeds);
  }

  function engineName(engine) {
    if (!engine) return 'embed';
    return engine.av ? 'avplay' : 'html5';
  }

  function clipDetail(d) {
    if (DZ.player && DZ.player.clipDetail) return DZ.player.clipDetail(d);
    var t = d === undefined || d === null ? '' : String(d).replace(/\s+/g, ' ').replace(/^ | $/g, '');
    return t.length > 120 ? t.slice(0, 120) : t;
  }

  /* her akisin kendi belirteci; ayni belirtec+olay yalnizca bir kez raporlanir.
     detail (<=120 karakter): yalniz basarisizlikta ve bos degilse payload'a girer (sunucuda alan yoksa zararsiz) */
  function report(run, index, event, code, engine, detail) {
    var s = run.streams[index];
    if (!s || !s.attempt_token) return;
    var key = s.attempt_token + ':' + event;
    if (run.reported[key]) return;
    run.reported[key] = true;
    var payload = { attempt_token: s.attempt_token, event: event, code: code || '', engine: engineName(engine) };
    var det = event === 'failure' ? clipDetail(detail) : '';
    if (det) payload.detail = det;
    try { DZ.api.playbackReport(payload).then(function () {}, function () {}); } catch (e) {}
  }

  function clearTimers(run) {
    if (run.timer) { clearTimeout(run.timer); run.timer = null; }
    if (run.slowTimer) { clearTimeout(run.slowTimer); run.slowTimer = null; }
  }

  function destroyEngine(run) {
    var e = run.tryEngine;
    run.tryEngine = null;
    if (e) { try { e.destroy(); } catch (err) {} }
  }

  /* Geri tusu / disaridan iptal: denemeyi durdur, motoru birak, oldugun ekranda kal. */
  function cancel(run) {
    if (!run.alive) return;
    run.alive = false;
    clearTimers(run);
    destroyEngine(run);
    if (active === run) active = null;
    if (run.ctl) run.ctl.close();
    if (run.o.onCancel) { try { run.o.onCancel(); } catch (e) {} }
  }

  /* Hata paneli ("Kaynak calismiyor": Tekrar dene / Geri). finder (sunucunun /api/streams `finder` alani): searching -> "Kaynak araniyor…" metni +
     5 sn'de bir durum yoklamasi (found -> akis otomatik yeniden istenir; not_found -> panel "bulunamadi" metniyle yenilenir);
     not_found -> "Bu bolum/film icin kaynak bulunamadi."; finder yoksa genel mesaj. */
  function openFail(o, message, finder) {
    var F = DZ.finder;
    var isEp = !!(o.episodeId && o.episodeId !== o.itemId) || o.type === 'series';
    var fmsg = F && F.messageFor ? F.messageFor(finder, isEp) : null;
    var title = (fmsg && F.titleFor(finder)) || 'Kaynak çalışmıyor';
    var watch = null;
    var closed = false;
    function stopWatch() { closed = true; if (watch) { watch.stop(); watch = null; } }
    var back = function () { stopWatch(); if (o.onCancel) { try { o.onCancel(); } catch (e) {} } };
    DZ.modal.open({
      fullscreen: true,
      title: title,
      message: fmsg || message || 'Bu içerik şu anda oynatılamıyor. Biraz sonra tekrar deneyebilirsiniz.',
      buttons: [{ label: 'Tekrar dene', primary: true, value: 'retry' }, { label: 'Geri', value: 'back' }],
      onDone: function (value) { stopWatch(); if (value === 'retry') start(o); else back(); },
      onCancel: back
    });
    if (fmsg && finder.state === 'searching' && F.poll && !closed) {
      watch = F.poll(o.itemId, o.episodeId || null, function (state) {
        watch = null;
        if (closed) return;
        closed = true;
        if (state === 'found') start(o, F.MSG_FOUND);                 /* bulundu: akisi otomatik yeniden iste */
        else openFail(o, message, { state: 'not_found' });            /* panel "bulunamadi" metniyle yenilenir */
      }, { isOpen: function () { return !DZ.modal.isOpen || DZ.modal.isOpen(); } });
    }
  }

  function fail(run, message, finder) {
    if (!run.alive) return;
    run.alive = false;
    clearTimers(run);
    destroyEngine(run);
    if (active === run) active = null;
    if (run.ctl) run.ctl.close();
    openFail(run.o, message, finder);
  }

  function succeed(run, index, engine, silent) {
    run.alive = false;
    clearTimers(run);
    run.tryEngine = null;
    if (active === run) active = null;
    if (engine && !silent) report(run, index, 'success', '', engine);   /* oynatma gercekten basladi */
    if (run.ctl) run.ctl.close();
    var o = run.o;
    DZ.app.go('player', {
      itemId: o.itemId,
      episodeId: o.episodeId || null,
      kind: o.kind || 'video',
      title: o.title,
      type: o.type,
      detail: o.detail || null,
      prepared: {
        streams: run.streams, index: index, tried: run.tried, reported: run.reported,
        engine: engine || null, resume: run.resume, duration: run.duration,
        subtitles: run.subtitles, audio: run.audio
      }
    }, !!o.replace);
  }

  function nextIndex(run) {
    for (var i = 0; i < run.streams.length; i++) if (!run.tried[i]) return i;
    return -1;
  }

  function tryNext(run, lastMessage) {
    if (!run.alive) return;
    var i = run.tries < MAX_TRIES ? nextIndex(run) : -1;
    if (i < 0) { fail(run, lastMessage); return; }
    run.tries++;
    run.tried[i] = true;
    var stream = run.streams[i];
    if (run.tries > 1) run.ctl.setStage(MSG_OTHER); else run.ctl.setStage(MSG_PREPARE);

    /* iframe/embed: baslama/hata gozlenemez -> player'a dogrudan devret */
    if (isEmbed(stream)) { succeed(run, i, null); return; }

    var useAv = DZ.player.hasAvplay();
    var own = ++run.gen;
    function valid() { return run.alive && run.gen === own; }
    var engine = null;
    var cb = {
      prepared: function () { if (valid() && engine) engine.start(); },   /* dogrulama icin oynatmayi baslat */
      started: function () { if (valid()) succeed(run, i, engine); },
      error: function (message, code, detail) {
        if (!valid()) return;
        /* masaustu tarayici: kullanici etkilesimi olmadan otomatik oynatma engellendi. Akis hazir,
           bozuk degil -> hata/basari raporu yazmadan player'a devret (oynat/duraklat orada). */
        if (code === 'autoplay' && engine && engine.prepared) { engine.playing = false; succeed(run, i, engine, true); return; }
        onStreamError(run, i, String(message || ''), code, detail);
      }
    };
    engine = DZ.player.createEngine(null, useAv, cb, { detached: true });
    run.tryEngine = engine;
    run.timer = setTimeout(function () {
      if (valid()) onStreamError(run, i, 'Video başlamadı', 'timeout', 'start-timeout');
    }, STREAM_TIMEOUT_MS);
    if (!run.slowTimer) {
      run.slowTimer = setTimeout(function () {
        run.slowTimer = null;
        if (run.alive && run.ctl) run.ctl.setStage(MSG_OTHER);
      }, SLOW_MS);
    }
    engine.prepare(stream.url, run.resume, stream.type);   /* type: html5 motoru HLS'te hls.js'e karar verir (AVPlay yok sayar) */
  }

  function onStreamError(run, index, message, code, detail) {
    if (!run.alive) return;
    if (run.timer) { clearTimeout(run.timer); run.timer = null; }
    run.gen++;                                   /* bu denemenin gec kalan callback'leri gecersiz */
    code = g.navigator && g.navigator.onLine === false ? 'offline' : (code || 'playback_failed');
    report(run, index, 'failure', code, run.tryEngine, detail);
    destroyEngine(run);
    if (code === 'offline') { fail(run, 'Bağlantı yok. İnternet bağlantınızı kontrol edin.'); return; }
    if (code === 'aborted') { fail(run, message); return; }
    tryNext(run, message);
  }

  function start(opts, firstStage) {
    if (active) { var prev = active; active = null; prev.alive = false; clearTimers(prev); destroyEngine(prev); if (prev.ctl) prev.ctl.close(); }
    var o = opts || {};
    var run = { id: ++seq, o: o, alive: true, streams: [], tried: {}, reported: {}, tries: 0, gen: 0,
      timer: null, slowTimer: null, tryEngine: null, resume: 0, duration: 0, ctl: null, subtitles: [], audio: [] };
    active = run;
    run.ctl = DZ.modal.loading({ onCancel: function () { cancel(run); } });
    run.ctl.setStage(firstStage || MSG_SEARCH);

    DZ.api.streams(o.itemId, o.episodeId || null, o.kind || null).then(function (res) {
      if (!run.alive) return;
      var streams = orderStreams(res && res.streams ? res.streams : [], qualityCap(readQualityPref()));
      if (!streams.length) { fail(run, 'Bu içerik için kullanılabilir video kaynağı bulunamadı.', res && res.finder); return; }
      run.subtitles = (res && res.subtitles) || [];
      run.audio = (res && res.audio) || [];
      if (DZ.tracks && DZ.tracks.prefer) {
        try { streams = DZ.tracks.prefer(streams, run.subtitles, run.audio); } catch (ePrefer) {}
      }
      run.streams = streams;
      run.resume = (res && res.resume_position) || 0;
      run.duration = (res && res.duration) || 0;
      tryNext(run, '');
    }, function (err) {
      if (!run.alive) return;
      fail(run, err && err.message ? err.message : 'Kaynak bilgisi alınamadı.');
    });
    return run;
  }

  DZ.playflow = {
    start: start,
    orderStreams: orderStreams,
    streamHeight: streamHeight,
    qualityCap: qualityCap,
    isActive: function () { return !!active; },
    cancel: function () { if (active) cancel(active); },
    STREAM_TIMEOUT_MS: STREAM_TIMEOUT_MS,
    SLOW_MS: SLOW_MS
  };
})(window);
