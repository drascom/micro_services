/* finder.js - kaynak bulucu (sunucu arka plan isi) metinleri + durum yoklamasi. ES2017, opsiyonel zincir YOK.
   `GET /api/streams/{id}` akis cikaramazsa yanitta istege bagli `finder: {state: 'searching'|'not_found'}` bulunur (API.md "Kaynak bulucu").
   - searching -> "Kaynak araniyor… Bulununca haber verecegiz…" ; not_found -> "Bu bolum/film icin kaynak bulunamadi." ; finder yoksa genel mesaj.
   - poll(): hata panelindeyken 5 sn'de bir `GET /api/source-finder/{id}?episode=`; found / not_found olunca cb(state), idle'da sessizce durur.
   playflow.js (yukleme modali hata paneli) ve screens/player.js (dogrudan acilis) ortak kullanir. */
(function (g) {
  'use strict';
  var DZ = g.DZ = g.DZ || {};

  var INTERVAL = 5000;
  var MAX_MS = 10 * 60 * 1000;   /* panel bu kadar acik kalirsa yoklama durur */

  var MSG_SEARCHING = 'Kaynak aranıyor… Bulununca haber vereceğiz. Sayfada kalabilir ya da uygulamada gezinebilirsin.';
  var MSG_NOT_FOUND_EPISODE = 'Bu bölüm için kaynak bulunamadı.';
  var MSG_NOT_FOUND_MOVIE = 'Bu film için kaynak bulunamadı.';
  var MSG_FOUND = 'Kaynak bulundu, yeniden deneniyor…';

  function stateOf(finder) {
    var s = finder && typeof finder === 'object' ? finder.state : null;
    return s === 'searching' || s === 'not_found' ? s : null;
  }

  /* finder -> hata paneli metni (gecerli finder yoksa null: cagiran genel mesajini kullanir).
     isEpisode: dizi bolumu mu (true) / film mi (false) */
  function messageFor(finder, isEpisode) {
    var s = stateOf(finder);
    if (s === 'searching') return MSG_SEARCHING;
    if (s === 'not_found') return isEpisode === false ? MSG_NOT_FOUND_MOVIE : MSG_NOT_FOUND_EPISODE;
    return null;
  }

  function titleFor(finder) {
    var s = stateOf(finder);
    if (s === 'searching') return 'Kaynak aranıyor';
    if (s === 'not_found') return 'Kaynak bulunamadı';
    return null;
  }

  /* itemId/episodeId icin durumu yokla. cb(state, info): yalniz 'found' ya da 'not_found' oldugunda BIR kez; 'idle' (is yok) ya da sure dolunca
     sessizce durur. Gecici ag hatasi yoklamayi durdurmaz. Donus: {stop()}. */
  function poll(itemId, episodeId, cb, opts) {
    var o = opts || {};
    var interval = o.interval || INTERVAL;
    var maxMs = o.maxMs || MAX_MS;
    var maxPolls = Math.max(1, Math.ceil(maxMs / interval));
    var polls = 0;
    var timer = null;
    var stopped = false;
    var busy = false;

    function stop() {
      stopped = true;
      if (timer !== null) { clearTimeout(timer); timer = null; }
    }
    function again() {
      if (stopped) return;
      if (polls >= maxPolls) { stop(); return; }
      timer = setTimeout(tick, interval);
    }
    function tick() {
      timer = null;
      if (stopped || busy) return;
      if (typeof o.isOpen === 'function' && !o.isOpen()) { stop(); return; }   /* panel kapandi */
      busy = true;
      polls++;
      var p;
      try { p = DZ.api.sourceFinder(itemId, episodeId || null); } catch (e) { busy = false; again(); return; }
      Promise.resolve(p).then(function (st) {
        busy = false;
        if (stopped) return;
        var s = st && st.state;
        if (s === 'found' || s === 'not_found') { stop(); if (cb) cb(s, st); return; }
        if (s === 'idle') { stop(); return; }
        again();
      }, function () {
        busy = false;
        again();
      });
    }
    again();
    return { stop: stop };
  }

  DZ.finder = {
    messageFor: messageFor, titleFor: titleFor, poll: poll,
    MSG_SEARCHING: MSG_SEARCHING, MSG_NOT_FOUND_EPISODE: MSG_NOT_FOUND_EPISODE, MSG_NOT_FOUND_MOVIE: MSG_NOT_FOUND_MOVIE, MSG_FOUND: MSG_FOUND,
    INTERVAL: INTERVAL, MAX_MS: MAX_MS
  };
})(window);
