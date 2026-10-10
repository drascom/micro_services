/* diziflix server2 admin shell: hash routing, api()/esc() helpers, poll timers, top strip.
   Screens register themselves: ADMIN.screen(name, {title, menu, render(el, params, ctx)}).
   Site page tabs register with ADMIN.siteTab(id, title, render) (site.js). */
(function () {
  'use strict';
  var A = window.ADMIN = {screens: {}, order: []};
  var gen = 0;              // route generation: timers of an old screen stop when it changes
  var stripTimer = null;

  A.esc = function (s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
  };

  /* fetch JSON; non-2xx -> Error(message from the {"error": {...}} envelope) */
  A.api = function (path, opt) {
    opt = opt || {};
    var init = {method: opt.method || 'GET', headers: {}};
    if (opt.body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(opt.body);
    }
    return fetch(path, init).then(function (r) {
      var ct = r.headers.get('Content-Type') || '';
      var p = ct.indexOf('json') >= 0 ? r.json() : r.text();
      return p.then(function (data) {
        if (!r.ok) {
          var e = (data && data.error) || {};
          var err = new Error(e.message || ('HTTP ' + r.status));
          err.status = r.status; err.code = e.code;
          throw err;
        }
        return data;
      });
    });
  };
  A.post = function (path, body) { return A.api(path, {method: 'POST', body: body === undefined ? {} : body}); };
  A.del = function (path) { return A.api(path, {method: 'DELETE'}); };

  A.when = function (epoch) {
    if (!epoch) return '-';
    var d = Date.now() / 1000 - epoch;
    if (d < 60) return 'az önce';
    if (d < 3600) return Math.floor(d / 60) + ' dk önce';
    if (d < 86400) return Math.floor(d / 3600) + ' sa önce';
    return new Date(epoch * 1000).toLocaleString('tr-TR', {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'});
  };
  A.full = function (epoch) { return epoch ? new Date(epoch * 1000).toLocaleString('tr-TR') : '-'; };
  A.el = function (html) { var t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstChild; };
  A.go = function (hash) { if (location.hash === hash) route(); else location.hash = hash; };
  A.flash = function (msg) { window.alert(msg); };

  /* poll(fn, delay): fn() returns a promise (or nothing); delay is ms or a function returning ms. Stops on route change. */
  A.poll = function (fn, delay) {
    var mine = gen;
    function tick() {
      if (mine !== gen) return;
      Promise.resolve().then(fn).catch(function () {}).then(function () {
        if (mine !== gen) return;
        setTimeout(tick, typeof delay === 'function' ? delay() : delay);
      });
    }
    tick();
  };
  A.alive = function (mine) { return mine === gen; };
  A.gen = function () { return gen; };

  A.screen = function (name, def) {
    A.screens[name] = def;
    if (A.order.indexOf(name) < 0) A.order.push(name);
  };

  function menu(active) {
    var nav = document.getElementById('menu');
    nav.innerHTML = A.order.filter(function (n) { return A.screens[n].menu; }).map(function (n) {
      var s = A.screens[n];
      return '<a href="#/' + n + '"' + (n === active ? ' aria-current="page"' : '') + '>' + A.esc(s.title) + '</a>';
    }).join('');
  }

  function parse() {
    var parts = (location.hash || '').replace(/^#\/?/, '').split('/').filter(Boolean).map(decodeURIComponent);
    return {name: parts[0] || 'sites', args: parts.slice(1)};
  }

  function route() {
    gen++;
    var r = parse();
    var view = document.getElementById('view');
    var s = A.screens[r.name];
    menu(s && s.menuAs ? s.menuAs : r.name);
    view.innerHTML = '';
    if (!s) {
      view.innerHTML = '<p class="empty">Bu ekran henüz yok.</p>';
      return;
    }
    try { s.render(view, r.args); } catch (e) { view.innerHTML = '<p class="err">' + A.esc(e.message) + '</p>'; }
  }

  /* top strip: running jobs / waiting for you / open problems + notes bell count */
  A.refreshStrip = function () {
    return A.api('/api/control/summary').then(function (s) {
      var w = s.waiting_for_you || 0;
      document.getElementById('sumtxt').innerHTML =
        'çalışan işler: <b>' + (s.running_jobs || 0) + '</b> · ' +
        '<span class="' + (w ? 'hot' : '') + '">senden bekleyen: <b>' + w + '</b></span> · ' +
        'açık sorun: <b>' + (s.open_problems || 0) + '</b>' +
        (s.drain ? ' · <span class="pill bad">drain açık: yeni iş başlamıyor</span>' : '');
      document.getElementById('bellcount').textContent = s.unread_notes ? String(s.unread_notes) : '';
    }).catch(function () {
      document.getElementById('sumtxt').textContent = 'sunucuya ulaşılamıyor';
    });
  };

  A.start = function () {
    window.addEventListener('hashchange', route);
    route();
    A.refreshStrip();
    stripTimer = setInterval(A.refreshStrip, 5000);
  };
})();
