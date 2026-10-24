/* search.js - arama denetleyicisi (ES5, DOM yok; gorunum js/app.js renderSearch).
   Eski istemciyle (git gecmisi: screens/catalog.js runSearch) ayni kural: < 2 karakter = temizle; >= 2 yerel kutuphane
   (/api/catalog?q=) hemen; >= 3 ayrica canli site aramasi (/api/search). Yazarken ~300 ms debounce, Enter/Done hemen.
   Her yeni sorgu oncekilerin istegini iptal eder (abort) ve sira numarasiyla bayat yaniti yok sayar.
   Gorunum: local = kutuphane sonuclari, sites = canli sonuclardan kutuphane listesinde OLMAYANLAR (id ile, tekrarsiz). */
(function (g) {
  'use strict';
  var DZP = g.DZP = g.DZP || {};
  var MIN_LOCAL = 2, MIN_REMOTE = 3, DEBOUNCE_MS = 300, LIMIT = 20;
  var REMOTE_NOTE = 'Canlı site araması şu an yanıt vermedi; kütüphane sonuçları gösteriliyor.';

  function norm(q) { return String(q || '').replace(/^\s+|\s+$/g, '').replace(/\s+/g, ' ').slice(0, 100); }

  /* remote_sites: yanit vermeyen (ok:false, skipped degil) siteler; ad icin sonuclardaki source_options.name, yoksa site kimligi */
  function failedSites(data) {
    var rs = data && data.remote_sites, names = {}, out = [], k, i, j;
    if (!rs || typeof rs !== 'object') return out;
    var its = (data && data.items) || [];
    for (i = 0; i < its.length; i++) {
      var so = its[i].source_options || [];
      for (j = 0; j < so.length; j++) if (so[j] && so[j].site && so[j].name) names[so[j].site] = so[j].name;
    }
    for (k in rs) if (Object.prototype.hasOwnProperty.call(rs, k) && rs[k] && rs[k].ok === false && !rs[k].skipped) out.push(names[k] || k);
    return out;
  }

  DZP.createSearch = function (o) {
    var api = o.api, setT = o.setTimeout || function (f, ms) { return g.setTimeout(f, ms); },
      clrT = o.clearTimeout || function (t) { g.clearTimeout(t); };
    var st = { q: '', token: 0, local: [], remote: [], lst: 'idle', rst: 'off', lerr: '', failed: [], rnote: '' };
    var reqs = [], timer = null;

    function abortAll() {
      for (var i = 0; i < reqs.length; i++) { try { if (reqs[i] && reqs[i].abort) reqs[i].abort(); } catch (e) {} }
      reqs = [];
    }
    function view() {
      var seen = {}, sites = [], i, it;
      for (i = 0; i < st.local.length; i++) seen[st.local[i].id] = 1;
      for (i = 0; i < st.remote.length; i++) {
        it = st.remote[i];
        if (!it || seen[it.id]) continue;
        seen[it.id] = 1; sites.push(it);
      }
      var note = st.rnote || (st.failed.length ? 'Yanıt vermeyen siteler: ' + st.failed.join(', ') : '');
      var busy = st.lst === 'loading' || st.rst === 'loading';
      return {
        q: st.q, idle: st.q.length < MIN_LOCAL, remoteOn: st.q.length >= MIN_REMOTE,
        local: st.local, sites: sites, remoteTotal: st.remote.length,
        localLoading: st.lst === 'loading', remoteLoading: st.rst === 'loading', localError: st.lerr,
        note: note, empty: !busy && st.q.length >= MIN_LOCAL && !st.local.length && !sites.length && !st.lerr
      };
    }
    function emit() { if (o.onChange) o.onChange(view()); }
    function run(raw, force) {
      var q = norm(raw);
      if (!force && q === st.q) return;
      var token = ++st.token;
      abortAll();
      st.q = q; st.local = []; st.remote = []; st.lerr = ''; st.failed = []; st.rnote = '';
      if (q.length < MIN_LOCAL) { st.lst = 'idle'; st.rst = 'off'; return emit(); }
      var pid = typeof o.pid === 'function' ? o.pid() : o.pid;
      st.lst = 'loading';
      st.rst = q.length >= MIN_REMOTE ? 'loading' : 'off';
      reqs.push(api.catalogSearch(q, pid, LIMIT, function (err, data) {
        if (token !== st.token) return;
        st.lst = 'done';
        if (err) st.lerr = err.message || 'Kütüphane araması başarısız';
        else st.local = (data && data.items) || [];
        emit();
      }));
      if (st.rst === 'loading') {
        reqs.push(api.search(q, pid, LIMIT, function (err, data) {
          if (token !== st.token) return;
          st.rst = 'done';
          if (err) st.rnote = REMOTE_NOTE;
          else {
            st.remote = (data && data.items) || [];
            st.failed = failedSites(data);
            if (data && data.remote_error && !st.failed.length) st.rnote = 'Bazı siteler yanıt vermedi.';
          }
          emit();
        }));
      }
      emit();
    }
    return {
      /* yazarken: < 2 karakter aninda temizlenir, aksi ~300 ms sonra aranir */
      input: function (raw) {
        if (timer) { clrT(timer); timer = null; }
        if (norm(raw).length < MIN_LOCAL) return run(raw);
        timer = setT(function () { timer = null; run(raw); }, DEBOUNCE_MS);
      },
      /* Enter / Done: hemen; ayni sorgu canli aramada sorun yasadiysa yeniden dener */
      submit: function (raw) {
        if (timer) { clrT(timer); timer = null; }
        var q = norm(raw);
        run(raw, q === st.q && !!(st.rnote || st.failed.length || st.lerr));
      },
      view: view,
      dispose: function () { if (timer) { clrT(timer); timer = null; } st.token++; abortAll(); }
    };
  };
  DZP.searchRules = { MIN_LOCAL: MIN_LOCAL, MIN_REMOTE: MIN_REMOTE, DEBOUNCE_MS: DEBOUNCE_MS };
})(window);
