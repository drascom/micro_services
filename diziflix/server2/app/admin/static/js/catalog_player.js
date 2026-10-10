/* In-panel player (T16): modal <video> + hls.js (served locally from /admin/vendor/, never a CDN) or a sandboxed <iframe>.
   Engine by streams[].type (not by URL extension): hls -> native when the browser plays HLS, else hls.js; mp4 -> native;
   embed -> iframe. Streams come from /api/control/catalog/streams/<id> (no attempt token, no progress, no report:
   admin playback never touches client telemetry). ADMIN.player.open({cid, episode, source, title, canReport}). */
(function () {
  'use strict';
  var A = window.ADMIN;
  var HLS_SRC = '/admin/vendor/hls.min.js';
  var hlsLoading = null;
  var cur = null;   // {hls, video, timer, modal}

  var CSS =
    '.pmodal{position:fixed;inset:0;background:rgba(0,0,0,.6);display:flex;align-items:flex-start;justify-content:center;z-index:50;overflow:auto;padding:24px 12px}' +
    '.pbox{background:var(--surface);border:1px solid var(--line);border-radius:12px;max-width:980px;width:100%;padding:14px}' +
    '.pbox .phead{display:flex;gap:10px;align-items:center;margin-bottom:8px}.pbox .phead b{flex:1}' +
    '.pstage{background:#000;border-radius:8px;overflow:hidden;aspect-ratio:16/9;display:flex}' +
    '.pstage video,.pstage iframe{width:100%;height:100%;border:0;background:#000}' +
    '.pctl{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:8px;font-size:13px}' +
    '.pctl select{font-size:13px;padding:4px 6px;max-width:260px}' +
    '.pinfo{font:12px var(--mono);color:var(--muted);margin-top:6px}' +
    '.perr{color:var(--bad);font-size:13px;margin-top:6px;white-space:pre-wrap}';

  function style() {
    if (document.getElementById('pcss')) return;
    var s = document.createElement('style');
    s.id = 'pcss'; s.textContent = CSS;
    document.head.appendChild(s);
  }

  function loadHls() {
    if (window.Hls) return Promise.resolve(window.Hls);
    if (hlsLoading) return hlsLoading;
    hlsLoading = new Promise(function (ok, fail) {
      var sc = document.createElement('script');
      sc.src = HLS_SRC;
      sc.onload = function () { ok(window.Hls); };
      sc.onerror = function () { hlsLoading = null; fail(new Error('hls.js yüklenemedi')); };
      document.head.appendChild(sc);
    });
    return hlsLoading;
  }

  function teardown() {
    if (!cur) return;
    if (cur.timer) clearInterval(cur.timer);
    if (cur.hls) { try { cur.hls.destroy(); } catch (e) { /* ignore */ } }
    if (cur.video) { try { cur.video.pause(); cur.video.removeAttribute('src'); cur.video.load(); } catch (e) { /* ignore */ } }
    if (cur.modal && cur.modal.parentNode) cur.modal.parentNode.removeChild(cur.modal);
    document.removeEventListener('keydown', onKey);
    cur = null;
  }

  function onKey(ev) { if (ev.key === 'Escape') teardown(); }

  function opt(v, t, sel) { return '<option value="' + A.esc(v) + '"' + (sel ? ' selected' : '') + '>' + A.esc(t) + '</option>'; }

  function streamName(s) {
    var q = s.height ? s.height + 'p' : (s.quality || 'auto');
    return (s.source || '') + ' · ' + (s.type || '?') + ' · ' + q + (s.label ? ' · ' + s.label : '') + (s.proxied ? ' · vekil' : '');
  }

  function fmtRate(b) { return b ? (b >= 1e6 ? (b / 1e6).toFixed(1) + ' Mb/s' : Math.round(b / 1e3) + ' kb/s') : '-'; }

  function open(o) {
    teardown();
    style();
    var q = '?episode=' + encodeURIComponent(o.episode || '') + (o.source ? '&source=' + encodeURIComponent(o.source) : '');
    var modal = A.el(
      '<div class="pmodal" role="dialog" aria-modal="true"><div class="pbox">' +
      '<div class="phead"><b></b><button type="button" data-x>kapat ✕</button></div>' +
      '<div class="pstage"></div>' +
      '<div class="pctl">' +
      '<label>akış <select data-s></select></label>' +
      '<label data-lvbox hidden>kalite <select data-lv></select></label>' +
      '<label>altyazı <select data-sub></select></label>' +
      '<label data-aubox hidden>ses <select data-au></select></label>' +
      '<label>hız <select data-rate>' + ['0.5', '1', '1.25', '1.5', '2'].map(function (r) { return opt(r, r + '×', r === '1'); }).join('') +
      '</select></label>' +
      '<button type="button" class="danger" data-rep hidden>bu kaynak çalışmıyor: sorun olarak kaydet</button>' +
      '</div><div class="pinfo">yükleniyor…</div><div class="perr"></div></div></div>');
    modal.querySelector('.phead b').textContent = o.title || 'Oynatıcı';
    document.body.appendChild(modal);
    cur = {modal: modal};
    var mine = cur;
    modal.addEventListener('click', function (ev) { if (ev.target === modal || ev.target.closest('[data-x]')) teardown(); });
    document.addEventListener('keydown', onKey);
    var errEl = modal.querySelector('.perr'), info = modal.querySelector('.pinfo');

    A.api('/api/control/catalog/streams/' + encodeURIComponent(o.cid) + q).then(function (d) {
      if (cur !== mine) return;
      var streams = d.streams || [];
      var errs = (d.errors || []).map(function (e) { return e.site + ': [' + (e.stage || '?') + '] ' + (e.error || ''); });
      if (!streams.length) {
        info.textContent = 'oynatılabilir akış yok';
        errEl.textContent = errs.join('\n') || 'Kaynak akış vermedi.';
        return;
      }
      if (errs.length) errEl.textContent = 'çözülemeyen kaynaklar:\n' + errs.join('\n');
      var sel = modal.querySelector('[data-s]');
      sel.innerHTML = streams.map(function (s, i) { return opt(i, streamName(s), i === 0); }).join('');
      sel.onchange = function () { play(streams[+sel.value], d, mine); };
      modal.querySelector('[data-rate]').onchange = function (ev) { if (mine.video) mine.video.playbackRate = +ev.target.value; };
      var rep = modal.querySelector('[data-rep]');
      if (o.canReport) {
        rep.hidden = false;
        rep.onclick = function () {
          var s = streams[+sel.value];
          if (!window.confirm('Bu kaynak için sorun kaydı açılsın mı?')) return;
          A.post('/api/control/catalog/sources/' + encodeURIComponent(s.source_id) + '/report', {})
            .then(function (r) { A.flash('Sorun kaydı: #' + r.problem_id); })
            .catch(function (e) { errEl.textContent = e.message; });
        };
      }
      play(streams[0], d, mine);
    }).catch(function (e) { if (cur === mine) { info.textContent = ''; errEl.textContent = e.message; } });
  }

  function play(s, d, mine) {
    var modal = mine.modal, stage = modal.querySelector('.pstage'), errEl = modal.querySelector('.perr');
    var info = modal.querySelector('.pinfo');
    if (mine.hls) { try { mine.hls.destroy(); } catch (e) { /* ignore */ } mine.hls = null; }
    if (mine.timer) { clearInterval(mine.timer); mine.timer = null; }
    modal.querySelector('[data-lvbox]').hidden = true;
    modal.querySelector('[data-aubox]').hidden = true;
    errEl.textContent = '';
    stage.innerHTML = '';
    mine.video = null;
    var subSel = modal.querySelector('[data-sub]');
    if (s.type === 'embed') {
      // allow-scripts + allow-same-origin would let a page on the admin's own origin lift the sandbox: refuse it
      var u = null;
      try { u = new URL(s.url, window.location.href); } catch (e) { u = null; }
      if (!u || (u.protocol !== 'https:' && u.protocol !== 'http:') || u.origin === window.location.origin) {
        errEl.textContent = 'Gömme adresi reddedildi (panelin kendi adresi ya da http(s) değil): ' + (s.url || '');
        subSel.innerHTML = opt('', 'yok', true);
        info.textContent = 'motor: embed (oynatılmadı)';
        return;
      }
      var fr = document.createElement('iframe');
      fr.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-presentation');
      fr.setAttribute('allow', 'autoplay; fullscreen; encrypted-media; picture-in-picture');
      fr.setAttribute('referrerpolicy', 'no-referrer');
      fr.src = s.url;
      stage.appendChild(fr);
      subSel.innerHTML = opt('', 'yok (gömülü)', true);
      info.textContent = 'motor: embed (iframe, sandbox) · ' + (s.source || '');
      return;
    }
    var v = document.createElement('video');
    v.controls = true; v.autoplay = true; v.playsInline = true;
    v.playbackRate = +modal.querySelector('[data-rate]').value || 1;
    stage.appendChild(v);
    mine.video = v;
    var subs = (d.subtitles || []).filter(function (t) { return !t.stream_ids || !s.variant_id || t.stream_ids.indexOf(s.variant_id) >= 0; });
    subs.forEach(function (t, i) {
      var tr = document.createElement('track');
      tr.kind = 'subtitles'; tr.src = t.url; tr.srclang = t.lang || 'und'; tr.label = t.label || ('altyazı ' + (i + 1));
      v.appendChild(tr);
    });
    subSel.innerHTML = opt('-1', 'kapalı', true) + subs.map(function (t, i) { return opt(i, t.label || t.lang || ('#' + (i + 1))); }).join('');
    subSel.onchange = function () {
      for (var i = 0; i < v.textTracks.length; i++) v.textTracks[i].mode = String(i) === subSel.value ? 'showing' : 'disabled';
    };
    v.addEventListener('error', function () {
      var me = v.error;
      errEl.textContent = 'video hatası: ' + (me ? 'kod ' + me.code + (me.message ? ' · ' + me.message : '') : 'bilinmiyor');
    });
    var engine = 'native';
    var nativeHls = !!v.canPlayType('application/vnd.apple.mpegurl');
    if (s.type === 'hls' && !nativeHls) {
      engine = 'hls.js';
      loadHls().then(function (Hls) {
        if (mine.video !== v) return;
        if (!Hls.isSupported()) { errEl.textContent = 'hls.js bu tarayıcıda desteklenmiyor'; return; }
        var hls = new Hls({enableWorker: true});
        mine.hls = hls;
        hls.on(Hls.Events.ERROR, function (_ev, data) {
          errEl.textContent = 'hls.js hata: ' + data.type + ' · ' + data.details + (data.fatal ? ' (ölümcül)' : '') +
            (data.response && data.response.code ? ' · HTTP ' + data.response.code : '') + (data.reason ? ' · ' + data.reason : '');
        });
        hls.on(Hls.Events.MANIFEST_PARSED, function () {
          var lv = modal.querySelector('[data-lv]');
          if (hls.levels.length > 1) {
            modal.querySelector('[data-lvbox]').hidden = false;
            lv.innerHTML = opt('-1', 'otomatik', true) + hls.levels.map(function (l, i) {
              return opt(i, (l.height ? l.height + 'p' : '#' + i) + ' · ' + fmtRate(l.bitrate));
            }).join('');
            lv.onchange = function () { hls.currentLevel = +lv.value; };
          }
        });
        hls.on(Hls.Events.AUDIO_TRACKS_UPDATED, function () {
          var au = modal.querySelector('[data-au]');
          if (hls.audioTracks.length > 1) {
            modal.querySelector('[data-aubox]').hidden = false;
            au.innerHTML = hls.audioTracks.map(function (t, i) {
              return opt(i, t.name || t.lang || ('#' + i), i === hls.audioTrack);
            }).join('');
            au.onchange = function () { hls.audioTrack = +au.value; };
          }
        });
        hls.loadSource(s.url);
        hls.attachMedia(v);
      }).catch(function (e) { errEl.textContent = e.message; });
    } else {
      v.src = s.url;
    }
    mine.timer = setInterval(function () {
      if (mine.video !== v) return;
      var buf = 0;
      for (var i = 0; i < v.buffered.length; i++) {
        if (v.buffered.start(i) <= v.currentTime && v.buffered.end(i) >= v.currentTime) buf = v.buffered.end(i) - v.currentTime;
      }
      var rate = mine.hls && mine.hls.levels && mine.hls.levels[mine.hls.currentLevel] ? mine.hls.levels[mine.hls.currentLevel].bitrate
        : (s.bitrate || 0);
      info.textContent = 'çözünürlük: ' + (v.videoWidth ? v.videoWidth + '×' + v.videoHeight : '-') + ' · bit hızı: ' + fmtRate(rate) +
        ' · tampon: ' + buf.toFixed(1) + ' sn · motor: ' + engine + (s.proxied ? ' · vekil (' + (s.proxy_reason || '') + ')' : '');
    }, 1000);
  }

  A.player = {open: open, close: teardown};
})();
