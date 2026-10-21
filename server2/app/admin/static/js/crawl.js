/* Site page "Tarama" tab (T11): the three crawl tiers, progress, today's quota, schedule, refresh / pause.
   T27: plan line, "tümünü şimdi tara" (admin crawl outside the quotas) + its progress / stop / duration. */
(function () {
  'use strict';
  var A = window.ADMIN;
  if (!A || !A.siteTab) return;

  var TIER_STATE = {
    done: ['bitti', 'ok'], running: ['sürüyor', 'run'], pending: ['sırada', ''], waiting: ['bekliyor', ''],
    waiting_a2: ['A2 bekleniyor', ''], scheduled: ['planlı', 'ok']
  };
  var TYPE = {home: 'ana sayfa', listing: 'liste', title: 'başlık', season: 'sezon', episode: 'bölüm'};

  function style() {
    if (document.getElementById('crawl-style')) return;
    var s = document.createElement('style');
    s.id = 'crawl-style';
    s.textContent =
      '.crawl-bar{height:10px;border-radius:99px;background:var(--surface2);overflow:hidden;margin:6px 0 10px}' +
      '.crawl-bar>i{display:block;height:100%;background:var(--ok)}' +
      '.crawl-stop td{background:var(--bad-bg);color:var(--bad)}' +
      '.crawl-form input{width:70px}';
    document.head.appendChild(s);
  }

  function pill(state) {
    var p = TIER_STATE[state] || [state || '-', ''];
    return '<span class="pill ' + p[1] + '">' + A.esc(p[0]) + '</span>';
  }

  function tierRow(t) {
    var detail = '';
    if (t.id === 'k1') {
      detail = t.day ? 'gün ' + t.day + '/' + t.days + ' · bugünkü kota ' + t.quota + ', kalan ' + t.quota_left : '';
    } else if (t.id === 'k2') {
      detail = 'son tazeleme ' + A.when(t.last) + ' · sırada ' + (t.due || 0) + ' sayfa';
    }
    return '<tr><td><b>' + A.esc(t.title) + '</b></td><td>' + pill(t.state) + '</td>' +
      '<td class="num">' + (t.done == null ? '-' : t.done + '/' + t.discovered) + '</td>' +
      '<td class="muted">' + A.esc(detail) + '</td></tr>';
  }

  function dur(sec) {
    sec = Math.round(sec || 0);
    if (sec < 60) return sec + ' sn';
    if (sec < 3600) return Math.floor(sec / 60) + ' dk ' + (sec % 60) + ' sn';
    return Math.floor(sec / 3600) + ' sa ' + Math.floor((sec % 3600) / 60) + ' dk';
  }

  /* T27: one line that explains why the import takes days */
  function planLine(pl) {
    if (!pl) return '';
    return '<p class="muted">Plan: tam aktarım <b>' + pl.days + '</b> güne yayılıyor; bugün K1 kotası ' +
      (pl.k1_active ? '<span class="num">' + pl.quota_used + '/' + pl.quota + '</span>' : '-') +
      ', kapı kotası <span class="num">' + pl.gate_used + '/' + pl.gate_limit + '</span>; bekleyen <span class="num">' +
      pl.pending + '</span> sayfa (<span class="num">' + pl.gate_wait + '</span> kapı bekliyor)</p>';
  }

  var MANUAL_END = {done: 'bitti', cancelled: 'durduruldu', held: 'site engelledi, durdu'};

  function manualLine(m) {
    if (!m || m.running || !m.finished_at) return '';
    return '<p class="muted">Son elle tarama ' + A.esc(MANUAL_END[m.state] || m.state || '') + ' (' + A.esc(A.when(m.finished_at)) + '): ' +
      (m.done || 0) + ' sayfa, ' + (m.ok || 0) + ' başarılı, ' + (m.failed || 0) + ' hata · süre ' + A.esc(dur(m.took_s)) + '</p>';
  }

  function html(siteId, d) {
    var p = d.progress || {}, s = d.stats || {}, m = d.manual || {};
    var pct = p.discovered ? Math.round(100 * p.done / p.discovered) : 0;
    var h = '';
    if (m.running) pct = m.total ? Math.round(100 * (m.done || 0) / m.total) : 0;
    if (d.hold_until) h += '<p class="err">Site taramayı engelliyor; tarama ' + A.esc(A.full(d.hold_until)) + ' saatine kadar duruyor.</p>';
    if (d.paused) h += '<p class="err">Tarama duraklatıldı.</p>';
    h += '<p><b>Aktarılan / keşfedilen başlık:</b> <span class="num">' + (p.done || 0) + ' / ' + (p.discovered || 0) + '</span>' +
      ' · halka kapalı: <span class="num">' + (s.blocked || 0) + '</span>' +
      ' · başarısız: <span class="num">' + ((s.failed || 0) + (s.gone || 0)) + '</span>' +
      (s.gate_wait ? ' · kontrol bekliyor: <span class="num">' + s.gate_wait + '</span>' : '') + '</p>' +
      (m.running ? '<p><b>elle tarama: <span class="num">' + (m.done || 0) + ' / ' + (m.total || 0) + '</span></b>' +
        ' <span class="muted">(kota dışı; site nezaket kuralları geçerli)</span></p>' : '') +
      '<div class="crawl-bar"><i style="width:' + pct + '%"></i></div>' + planLine(d.plan) + manualLine(m);
    h += '<table class="grid"><thead><tr><th>Kademe</th><th>Durum</th><th>Sayı</th><th></th></tr></thead><tbody>' +
      (d.tiers || []).map(tierRow).join('') +
      (d.stopped || []).map(function (x) {
        return '<tr class="crawl-stop"><td colspan="4">' + A.esc(TYPE[x.page_type] || x.page_type) +
          ' sayfalarının yapısı değişmiş: bu tür için tarama durdu. ' +
          '<a href="#/site/' + encodeURIComponent(siteId) + '/map">Harita → ' + A.esc(x.stage) + ' aşamasını yeniden çalıştır</a></td></tr>';
      }).join('') + '</tbody></table>';
    h += '<form class="bar crawl-form" id="crawlform" style="margin-top:12px">' +
      '<label>tamamı <input type="number" min="1" max="60" name="days" value="' + d.backfill_days + '"> günde</label>' +
      '<label>tazeleme saati <input type="number" min="0" max="23" name="hour" value="' + d.refresh_hour + '"></label>' +
      '<button type="submit">kaydet</button>' +
      '<button type="button" data-act="refresh">şimdi tazele</button>' +
      (m.running ? '<button type="button" class="danger" data-act="all/stop">elle taramayı durdur</button>'
        : '<button type="button" data-act="all" title="Bekleyen tüm sayfalar şimdi, günlük kota ve kapı kotası dışında">tümünü şimdi tara</button>') +
      '<button type="button" data-act="' + (d.paused ? 'resume' : 'pause') + '">' + (d.paused ? 'taramayı sürdür' : 'taramayı duraklat') + '</button>' +
      '<span class="err" id="crawlerr"></span></form>';
    return h;
  }

  function render(body, siteId) {
    style();
    var mine = A.gen(), path = '/api/control/sites/' + encodeURIComponent(siteId) + '/crawl';
    var box = document.createElement('div'), editing = false, last = '';
    body.innerHTML = '';
    body.appendChild(box);

    function draw(d) {
      if (!A.alive(mine) || editing) return;
      var next = html(siteId, d);
      if (next === last) return;          // unchanged: no redraw (no page jump while polling)
      last = next;
      box.innerHTML = next;
      var form = box.querySelector('#crawlform'), err = box.querySelector('#crawlerr');
      form.addEventListener('focusin', function (ev) { if (ev.target.tagName === 'INPUT') editing = true; });
      form.addEventListener('focusout', function () { editing = false; });
      form.addEventListener('submit', function (ev) {
        ev.preventDefault();
        editing = false;
        A.api(path, {method: 'PUT', body: {backfill_days: parseInt(form.days.value, 10), refresh_hour: parseInt(form.hour.value, 10)}})
          .then(draw).catch(function (e) { err.textContent = e.message; });
      });
      form.addEventListener('click', function (ev) {
        var b = ev.target.closest('button[data-act]');
        if (!b) return;
        b.disabled = true;
        A.post(path + '/' + b.getAttribute('data-act')).then(load)
          .catch(function (e) { err.textContent = e.message; b.disabled = false; });
      });
    }

    function load() { return A.api(path).then(draw); }
    A.poll(load, 5000);
  }

  A.siteTab('crawl', 'Tarama', render, 20);
})();
