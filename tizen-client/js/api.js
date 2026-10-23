/* api.js - diziflix API istemcisi + localStorage yardimcilari. ES2017, opsiyonel zincir YOK. */
(function (g) {
  'use strict';
  var DZ = g.DZ = g.DZ || {};

  /* Gomulu varsayilan sunucu adresi YOK (acik kaynak: herkes kendi sunucusunu kurar). Adres yoksa ilk acilista
     screens/server_setup.js sorar ve /api/health basariliysa dz.baseUrl'e yazar. */
  var DEFAULT_PORT = '8090';
  var K_BASE = 'dz.baseUrl';
  var K_PROFILE = 'dz.profile';
  var K_BOOT = 'dz.boot.featured-hero-v3.';
  var K_QUALITY = 'dz_pref_quality';      /* "En yuksek kalite": '1080' (varsayilan) | '1440' | 'auto' */
  var CACHE_PREFIXES = ['dz.boot.', 'dz.row.'];   /* "Onbellegi temizle" yalniz bunlari siler */
  var TIMEOUT = 12000;

  /* ---------- localStorage ---------- */
  var store = {
    get: function (k, def) {
      try {
        var v = g.localStorage.getItem(k);
        return v === null || v === undefined ? def : v;
      } catch (e) { return def; }
    },
    set: function (k, v) {
      try { g.localStorage.setItem(k, v); return true; } catch (e) { return false; }
    },
    del: function (k) {
      try { g.localStorage.removeItem(k); } catch (e) {}
    },
    getJSON: function (k, def) {
      var raw = store.get(k, null);
      if (!raw) return def;
      try { return JSON.parse(raw); } catch (e) { return def; }
    },
    setJSON: function (k, obj) {
      try { return store.set(k, JSON.stringify(obj)); } catch (e) { return false; }
    }
  };

  /* ---------- debug bayraklari: ?delay=800&fail=1 ---------- */
  function qsParam(name) {
    var m = new RegExp('[?&]' + name + '=([^&]*)').exec(g.location.search || '');
    return m ? decodeURIComponent(m[1]) : null;
  }
  var debug = {
    delay: qsParam('delay'),
    fail: qsParam('fail')
  };

  function cleanBase(v) {
    var s = String(v || '').trim().replace(/\/+$/, '');
    if (s && !/^https?:\/\//i.test(s)) s = 'http://' + s;
    return s;
  }
  function serverPort() {
    var c = g.DZ_CONFIG || {};
    return String(c.port || DEFAULT_PORT).replace(/[^0-9]/g, '') || DEFAULT_PORT;
  }
  /* Kullanicinin yazdigi adres: semasiz ve portsuz `192.168.1.10` / `tv.lan` -> `http://<host>:8090`; sema verilmisse
     (`https://diziflix.ornek.com`, `http://host`) aynen (yalniz sondaki / atilir). */
  function normalizeServer(v) {
    var s = String(v || '').trim().replace(/\/+$/, '');
    if (!s) return '';
    if (/^https?:\/\//i.test(s)) return s;
    var hostPart = s.split('/')[0];
    if (!/:\d+$/.test(hostPart)) s = hostPart + ':' + serverPort() + s.slice(hostPart.length);
    return 'http://' + s;
  }
  /* Kullanici ayari yokken adres: (2) config.js `window.DZ_CONFIG.baseUrl`, (3) sayfa sunucunun KENDISINDEN (http(s), port =
     `DZ_CONFIG.port`, varsayilan 8090) aciliyorsa o origin, (4) aksi '' (adres yok -> ilk acilis "Sunucu adresi" ekrani). */
  function autoBase() {
    var c = g.DZ_CONFIG || {};
    var fixed = cleanBase(c.baseUrl);
    if (fixed) return fixed;
    var loc = g.location;
    if (loc && /^https?:$/i.test(loc.protocol || '') && loc.hostname && String(loc.port || '') === serverPort()) {
      return loc.protocol + '//' + loc.hostname + ':' + loc.port;
    }
    return '';
  }
  /* ilk acilis ekraninin oneri degeri: sayfa http(s) ile aciliyorsa ayni host + sunucu portu, aksi '' */
  function suggestBase() {
    var loc = g.location;
    if (!loc || !loc.hostname) return '';
    if (/^https:$/i.test(loc.protocol || '')) return 'https://' + loc.host;   /* ters vekil arkasi: ayni origin */
    if (/^http:$/i.test(loc.protocol || '')) return 'http://' + loc.hostname + ':' + serverPort();
    return '';
  }
  /* (1) localStorage `dz.baseUrl` (ilk acilis / Ayarlar'dan kullanici ayari) once; yoksa autoBase(); hicbiri yoksa ''. */
  function baseUrl() {
    return cleanBase(store.get(K_BASE, '')) || autoBase();
  }
  function hasBase() { return !!baseUrl(); }
  /* bos deger = kullanici ayarini sil (otomatik adrese don) */
  function setBaseUrl(v) {
    var s = cleanBase(normalizeServer(v));
    if (s) store.set(K_BASE, s); else store.del(K_BASE);
    return baseUrl();
  }

  function addDebug(path, only) {
    var out = path;
    var sep = out.indexOf('?') >= 0 ? '&' : '?';
    if (debug.delay) { out += sep + 'delay=' + encodeURIComponent(debug.delay); sep = '&'; }
    if (debug.fail && (!only || only === 'all' || only === debug.fail)) {
      out += sep + 'fail=1';
    }
    return out;
  }

  function url(path, base) { return (base || baseUrl()) + path; }

  /* Gorsel URL'i: server yolu ise base ekle, mutlaksa aynen birak */
  function img(path) {
    if (!path) return '';
    if (/^(https?:)?\/\//i.test(path) || path.indexOf('data:') === 0) return path;
    return baseUrl() + (path.charAt(0) === '/' ? path : '/' + path);
  }

  function ApiError(code, message, status) {
    this.name = 'ApiError';
    this.code = code || 'network';
    this.message = message || 'Baglanti hatasi';
    this.status = status || 0;
  }
  ApiError.prototype = Object.create(Error.prototype);

  function withTimeout(promise, ms) {
    return new Promise(function (resolve, reject) {
      var done = false;
      var t = g.setTimeout(function () {
        if (done) return;
        done = true;
        reject(new ApiError('timeout', 'Sunucu zaman asimi', 0));
      }, ms);
      promise.then(function (v) {
        if (done) return;
        done = true; g.clearTimeout(t); resolve(v);
      }, function (e) {
        if (done) return;
        done = true; g.clearTimeout(t); reject(e);
      });
    });
  }

  function request(path, opts) {
    var o = opts || {};
    var full = url(addDebug(path), o.base);
    var init = { method: o.method || 'GET', mode: 'cors', cache: 'no-cache' };
    if (o.signal) init.signal = o.signal;   /* AbortController (yazarken arama: bayat istegi iptal) */
    if (o.body !== undefined && o.body !== null) {
      init.headers = { 'Content-Type': 'application/json' };
      init.body = JSON.stringify(o.body);
    }
    var p = fetch(full, init).then(function (res) {
      return res.text().then(function (txt) {
        var data = null;
        if (txt) { try { data = JSON.parse(txt); } catch (e) { data = null; } }
        if (!res.ok) {
          var code = 'http_' + res.status;
          var msg = 'Sunucu hatasi (' + res.status + ')';
          if (data && data.error) {
            if (data.error.code) code = data.error.code;
            if (data.error.message) msg = data.error.message;
          }
          throw new ApiError(code, msg, res.status);
        }
        if (data === null) throw new ApiError('bad_json', 'Gecersiz sunucu yaniti', res.status);
        return data;
      });
    }, function () {
      throw new ApiError('network', 'Sunucuya ulasilamiyor: ' + (o.base || baseUrl() || '(adres yok)'), 0);
    });
    return withTimeout(p, o.timeout || TIMEOUT);
  }

  function enc(v) { return encodeURIComponent(String(v == null ? '' : v)); }

  function sourceId() { return ''; }
  function sourceQuery() { return '&source=' + enc(sourceId()); }
  function bootKey(pid) { return K_BOOT + baseUrl() + ':' + pid + ':' + sourceId(); }

  var api = {
    sourceId: sourceId,
    setSourceId: function (id) { store.set('dz.source.' + baseUrl(), id || ''); },
    defaultBase: autoBase,
    suggestBase: suggestBase,
    normalizeServer: normalizeServer,
    hasBase: hasBase,
    K_BASE: K_BASE,
    K_PROFILE: K_PROFILE,
    debug: debug,
    store: store,
    baseUrl: baseUrl,
    setBaseUrl: setBaseUrl,
    img: img,
    request: request,
    ApiError: ApiError,

    /* profil secimi (son secim saklanir; soguk acilista profil ekrani yine gelir, kart odagi bu profilde baslar) */
    profileId: function () { return store.get(K_PROFILE, null); },
    setProfileId: function (id) { if (id) store.set(K_PROFILE, id); else store.del(K_PROFILE); },

    catalog: function (params, opts) {
      var query=Object.keys(params).filter(function(k){return params[k]!=='' && params[k]!==null && params[k]!==undefined;}).map(function(k){return enc(k)+'='+enc(params[k]);}).join('&');
      return request('/api/catalog?'+query, opts && opts.signal ? { signal: opts.signal } : undefined);
    },
    /* uclar */
    health: function () { return request('/api/health', { timeout: 6000 }); },
    /* kaydetmeden aday adresi dene (ilk acilis "Baglan"): {status:'ok'} degilse reddeder */
    probe: function (base) {
      return request('/api/health', { timeout: 6000, base: base }).then(function (res) {
        if (!res || res.status !== 'ok') throw new ApiError('bad_server', 'Bu adres bir diziflix sunucusu degil', 0);
        return res;
      });
    },
    profiles: function () { return request('/api/profiles'); },
    createProfile: function (name, isKids, avatarSeed) {
      var body = { name: name, is_kids: !!isKids };
      if (avatarSeed) body.avatar_seed = avatarSeed;
      return request('/api/profiles', { method: 'POST', body: body });
    },
    updateProfile: function (id, patch) {
      return request('/api/profiles/' + enc(id), { method: 'PUT', body: patch || {} });
    },
    deleteProfile: function (id) { return request('/api/profiles/' + enc(id), { method: 'DELETE' }); },
    avatars: function () { return request('/api/avatars'); },

    boot: function (pid) { return request('/api/boot?profile=' + enc(pid) + '&layout=tv-v1'); },
    row: function (rowId, pid, offset, limit) {
      return request('/api/row/' + enc(rowId) + '?profile=' + enc(pid) +
        '&offset=' + (offset || 0) + '&limit=' + (limit || 20) + sourceQuery());
    },
    /* opts.poll: `?poll=1` yoklamasi (js/hydrate_watch.js) - sunucu ASLA is baslatmaz, yalniz `hydrating` durumunu yansitir;
       kisa zaman asimi. Imza geriye uyumlu: opts yoksa eski davranis. Yanit EK alani `source_names:[{id,name}]` (kaynak site adlari) aynen gecer. */
    detail: function (itemId, pid, opts) {
      var path = '/api/detail/' + enc(itemId) + '?profile=' + enc(pid);
      if (opts && opts.poll) return request(path + '&poll=1', { timeout: 15000 });
      // A series first found through live search discovers its season catalogue
      // only on this first open; multi-season shows may need several Obscura pages.
      return request(path, { timeout: 180000 });
    },
    /* yanit: streams[] (+ variant_id/audio_lang/sub_mode/hard_lang), subtitles[] (/api/subtitles/<id>.vtt), audio[]:
       hepsi js/tracks.js + screens/player.js tarafindan kullanilir (API.md "Ses / altyazi izleri") */
    streams: function (itemId, episodeId, kind) {
      var p = '/api/streams/' + enc(itemId) + '?profile=' + enc(api.profileId());
      if (episodeId) p += '&episode=' + enc(episodeId);
      if (kind) p += '&kind=' + enc(kind);
      return request(p, { timeout: 90000 });
    },
    /* kaynak bulucu durumu: {state: idle|searching|found|not_found, steps, updated_at}; episodeId yoksa film / dizinin en yeni isi */
    sourceFinder: function (itemId, episodeId) {
      var p = '/api/source-finder/' + enc(itemId);
      if (episodeId) p += '?episode=' + enc(episodeId);
      return request(p, { timeout: 8000 });
    },
    /* bildirimler: {items:[{id,kind:'source_found',canonical_id,episode_id,title,season,episode,site,method,created_at}], last_id};
       since verilmezse (ilk calistirma) profilin tum okunmamislari. markNotificationsRead: upto'ya kadar (dahil) okundu. */
    notifications: function (pid, since) {
      var p = '/api/notifications?profile=' + enc(pid);
      if (since !== undefined && since !== null && since !== '') p += '&since=' + enc(since);
      return request(p, { timeout: 8000 });
    },
    markNotificationsRead: function (pid, upto) {
      return request('/api/notifications/read?profile=' + enc(pid), { method: 'POST', body: { upto: upto }, timeout: 8000 });
    },
    /* payload: {attempt_token, event, code, engine, detail?, hlsjs?}; detail (hata ayrintisi, en cok 120 karakter) yalniz doluysa gider,
       hlsjs (bool) yalniz true ise gider (html5 motoru akisi hls.js ile oynatiyor); sunucuda alan yoksa ikisi de zararsiz.
       Yanit: {ok, finder?: {state:'searching'|'not_found'}} - sunucu bu bolum icin kaynak bulucuyu baslattiysa (API.md "Kaynak bulucu"); `?profile=` bildirim icin */
    playbackReport: function (payload) {
      var body = {};
      for (var k in payload) { if (Object.prototype.hasOwnProperty.call(payload, k)) body[k] = payload[k]; }
      if (body.detail === undefined || body.detail === null || body.detail === '') delete body.detail;
      else body.detail = String(body.detail).slice(0, 120);
      if (body.hlsjs !== true) delete body.hlsjs;
      return request('/api/playback-report?profile=' + enc(api.profileId()), { method: 'POST', body: body });
    },
    progress: function (payload) {
      return request('/api/progress', { method: 'POST', body: payload, timeout: 8000 });
    },
    mylist: function (pid) { return request('/api/mylist?profile=' + enc(pid)); },
    addMyList: function (pid, itemId) {
      return request('/api/mylist', { method: 'POST', body: { profile: pid, item_id: itemId } });
    },
    removeMyList: function (pid, itemId) {
      return request('/api/mylist/' + enc(itemId) + '?profile=' + enc(pid), { method: 'DELETE' });
    },
    /* "Izlemeye Devam Et" satirindan kaldir (yumusak gizleme; progress silinmez). itemId = YAPIM id'si (bolum karti dahil
       item.id; episode_id DEGIL). Yanit {ok, removed}; idempotent. pid verilmezse gecerli profil. */
    removeFromContinue: function (itemId, pid) {
      var p = pid || api.profileId();
      return request('/api/continue/' + enc(itemId) + '?profile=' + enc(p), { method: 'DELETE', timeout: 8000 });
    },
    search: function (q, pid, limit, opts) {
      var o = { timeout: 90000 };
      if (opts && opts.signal) o.signal = opts.signal;
      return request('/api/search?q=' + enc(q) + '&profile=' + enc(pid) + '&limit=' + (limit || 20) + sourceQuery(), o);
    },

    /* kalite tercihi (cihaz geneli; playflow akis siralamasinda kullanir) */
    qualityPref: function () {
      var v = store.get(K_QUALITY, '1080');
      return v === '1440' || v === 'auto' ? v : '1080';
    },
    setQualityPref: function (v) {
      var x = v === '1440' || v === 'auto' ? v : '1080';
      store.set(K_QUALITY, x);
      return x;
    },

    /* Yalniz onbellekler (boot / satir) silinir; tercihler (dz_pref_*), sunucu adresi, profil ve arama sorgusu KALIR.
       Silinen kayit sayisini dondurur. */
    clearCaches: function () {
      var keys = [], removed = 0, i, j;
      try {
        var ls = g.localStorage;
        for (i = 0; ls && i < ls.length; i++) {
          var k = ls.key(i);
          if (!k) continue;
          for (j = 0; j < CACHE_PREFIXES.length; j++) {
            if (k.indexOf(CACHE_PREFIXES[j]) === 0) { keys.push(k); break; }
          }
        }
      } catch (e) {}
      for (i = 0; i < keys.length; i++) { store.del(keys[i]); removed++; }
      return removed;
    },

    /* boot cache (stale-while-revalidate) */
    cachedBoot: function (pid) { return store.getJSON(bootKey(pid), null); },
    saveBoot: function (pid, data) { store.setJSON(bootKey(pid), data); },
    clearBoot: function (pid) { store.del(bootKey(pid)); }
  };

  DZ.api = api;
  DZ.store = store;
})(window);
