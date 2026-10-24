/* episodes.js - bolum/sezon yardimcilari (ES5). Oynaticinin "Bolumler" paneli (player.js) ve detaydaki Bolumler paneli (app.js)
   ayni veri/bicim mantigini kullanir. Girdi: /api/detail yaniti (`seasons[].episodes[]`, API.md "Episode").
   DZP.episodes = { todayIso, sortedSeasons, withEpisodes, episodePlayable, epTag, episodeLabel, episodeInfo, nextEpisodeOf, rows } */
(function (g) {
  'use strict';
  var DZP = g.DZP = g.DZP || {};
  function pad2(v) { return v < 10 ? '0' + v : String(v); }
  function todayIso() { var d = new Date(); return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()); }
  /* sezon numarasina gore artan; ozel bolumler (sezon 0) sonda */
  function sortedSeasons(detail) {
    var list = detail && detail.seasons && detail.seasons.slice ? detail.seasons.slice() : [];
    list.sort(function (a, b) { return (a.season === 0 ? 1e9 : (a.season || 0)) - (b.season === 0 ? 1e9 : (b.season || 0)); });
    return list;
  }
  /* yalniz bolumu olan sezonlar (oynatici paneli) */
  function withEpisodes(detail) {
    var ss = sortedSeasons(detail), out = [];
    for (var i = 0; i < ss.length; i++) if (ss[i].episodes && ss[i].episodes.length) out.push(ss[i]);
    return out;
  }
  function airOf(ep) { return ep && typeof ep.air_date === 'string' && /^\d{4}-\d{2}-\d{2}/.test(ep.air_date) ? ep.air_date.slice(0, 10) : ''; }
  function episodePlayable(ep, today) {
    var a = ep && ep.availability ? ep.availability.state : '';
    var air = airOf(ep);
    if (air && air > today && a !== 'ready') return false;
    return a !== 'unavailable';
  }
  function epTag(season, episode) { var b = 'B' + pad2(episode || 0); return season === 0 ? ('Özel ' + b) : ('S' + pad2(season || 0) + ' ' + b); }
  function episodeLabel(tag, title) {
    var t = String(title || '').replace(/^\s+|\s+$/g, '');
    if (!t || /^\d+\s*\.?\s*b[oö]l[uü]m$/i.test(t)) return tag;
    return tag ? (tag + ' · ' + t) : t;
  }
  function snOf(ep, s) { return ep.season !== undefined && ep.season !== null ? ep.season : s.season; }
  function epObj(ep, sn) { var tag = epTag(sn, ep.episode); return { id: ep.id, season: sn, episode: ep.episode, tag: tag, title: ep.title || '', label: episodeLabel(tag, ep.title) }; }
  function episodeInfo(detail, id) {
    if (!id) return null;
    var list = sortedSeasons(detail), si, ei;
    for (si = 0; si < list.length; si++) {
      var eps = list[si].episodes || [];
      for (ei = 0; ei < eps.length; ei++) if (eps[ei].id === id) return epObj(eps[ei], snOf(eps[ei], list[si]));
    }
    var m = /:s(\d+):e(\d+)$/i.exec(String(id));
    if (!m) return null;
    var tg = epTag(+m[1], +m[2]);
    return { id: id, season: +m[1], episode: +m[2], tag: tg, title: '', label: tg };
  }
  /* bir sonraki ARDISIK bolum; aradaki bolum oynatilamiyorsa null (sessizce atlanmaz); ozel bolumler yalniz kendi aralarinda */
  function nextEpisodeOf(detail, id, today) {
    if (!detail || !id) return null;
    var list = sortedSeasons(detail), found = false, special = false;
    for (var si = 0; si < list.length; si++) {
      var eps = list[si].episodes || [];
      for (var ei = 0; ei < eps.length; ei++) {
        var ep = eps[ei];
        if (!found) { if (ep.id === id) { found = true; special = list[si].season === 0; } continue; }
        if ((list[si].season === 0) !== special) return null;
        if (!episodePlayable(ep, today || todayIso())) return null;
        return epObj(ep, snOf(ep, list[si]));
      }
    }
    return null;
  }
  /* bir sezonun bolum satirlari: {id, ep, sn, tag ('B03'), label ('B03 · Ad'), dis, state ('soon'|'nosrc'|''), meta ('45 dk · 18.06.2021'),
     pct (0..100 ya da 0), cur (curId)} */
  function rows(season, curId, today) {
    var eps = (season && season.episodes) || [], out = [];
    today = today || todayIso();
    for (var i = 0; i < eps.length; i++) {
      var e = eps[i], tag = 'B' + pad2(e.episode || 0), ok = episodePlayable(e, today), air = airOf(e), meta = [];
      if (e.runtime > 0) meta.push(e.runtime + ' dk');
      if (air) meta.push(air.slice(8, 10) + '.' + air.slice(5, 7) + '.' + air.slice(0, 4));
      var pr = e.progress, pct = 0;
      if (pr) pct = pr.pct > 0 ? pr.pct : (pr.duration > 0 ? Math.round(100 * pr.position / pr.duration) : 0);
      out.push({ id: e.id, ep: e, sn: snOf(e, season), tag: tag, label: episodeLabel(tag, e.title), dis: !ok,
        state: ok ? '' : (air && air > today ? 'soon' : 'nosrc'), meta: meta.join(' · '), pct: Math.max(0, Math.min(100, pct || 0)),
        cur: !!curId && e.id === curId });
    }
    return out;
  }
  DZP.episodes = { todayIso: todayIso, sortedSeasons: sortedSeasons, withEpisodes: withEpisodes, episodePlayable: episodePlayable, epTag: epTag,
    episodeLabel: episodeLabel, episodeInfo: episodeInfo, nextEpisodeOf: nextEpisodeOf, rows: rows };
})(window);
