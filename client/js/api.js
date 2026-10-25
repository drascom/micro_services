/* api.js - diziflix API (ES5, XHR).
   Sunucu adresi sirasi: kayitli (localStorage `dzproto.server`, ilk acilis / Ayarlar > Sunucu) > `?server=` > sayfa sunucunun
   KENDISINDEN (port 8090) aciliyorsa o origin > yok ('' -> ilk acilis "Sunucu adresi" ekrani, js/app.js).
   Erisim anahtari (istege bagli, `dzproto.key`, adresle birlikte kaydedilir): sunucuya giden her istege `X-DZ-Key` basligi
   (internetten / ters vekil uzerinden baglanirken gerekli; yerel ag anahtarsiz). 401 auth_required -> DZP.api.onAuthError. */
(function (g) {
  'use strict';
  var DZP = g.DZP = g.DZP || {};
  var K_SERVER = 'dzproto.server', K_KEY = 'dzproto.key', PORT = '8090', KEY_HDR = 'X-DZ-Key';

  function qs(name) {
    var m = new RegExp('[?&]' + name + '=([^&]*)').exec(g.location.search || '');
    return m ? decodeURIComponent(m[1].replace(/\+/g, ' ')) : null;
  }
  DZP.qs = qs;

  function lsGet(k) { try { return g.localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { if (v) g.localStorage.setItem(k, v); else g.localStorage.removeItem(k); } catch (e) {} }

  function cleanBase(v) {
    var s = String(v || '').replace(/^\s+|\s+$/g, '').replace(/\/+$/, '');
    if (s && !/^https?:\/\//i.test(s)) s = 'http://' + s;
    return s;
  }
  /* kullanicinin yazdigi adresin deneme adaylari (sirayla /api/health):
     semali URL aynen (port/yol yoksa ek aday <sema>://<host>:8090); port verilmis semasiz -> http://<host:port>; IP / localhost / tek etiket (nas) -> http://<host>:8090;
     semasiz alan adi (diziflix.ornek.com) -> once https://<ad> (ters vekil, portsuz), olmazsa http://<ad>:8090 */
  function serverCandidates(v) {
    var s = String(v || '').replace(/^\s+|\s+$/g, '').replace(/\/+$/, '');
    if (!s) return [];
    if (/^https?:\/\//i.test(s)) {
      /* semali, portsuz ve yolsuz -> once aynen, sonra ayni sema+host :8090 (ters vekil yoksa dogrudan uvicorn) */
      var m = /^(https?:\/\/)([^\/:?#]+|\[[^\]]+\])$/i.exec(s);
      return m ? [s, m[1] + m[2] + ':' + PORT] : [s];
    }
    var hostPart = s.split('/')[0], rest = s.slice(hostPart.length);
    if (/:\d+$/.test(hostPart)) return ['http://' + s];
    var plain = 'http://' + hostPart + ':' + PORT + rest;
    var isIp = /^\d{1,3}(\.\d{1,3}){3}$/.test(hostPart) || /^\[.*\]$/.test(hostPart);
    if (isIp || hostPart.indexOf('.') < 0) return [plain];
    return ['https://' + s, plain];
  }
  /* tek adres (ilk aday): ?server= ve setBase icin */
  function normalizeServer(v) { return serverCandidates(v)[0] || ''; }
  function autoBase() {
    var saved = cleanBase(lsGet(K_SERVER));
    if (saved) return saved;
    var q = qs('server');
    if (q) return cleanBase(normalizeServer(q));
    var loc = g.location;
    if (/^https?:$/i.test(loc.protocol || '') && loc.hostname && String(loc.port || '') === PORT) {
      return loc.protocol + '//' + loc.hostname + ':' + loc.port;
    }
    return '';
  }
  /* ilk acilis ekraninin oneri degeri: https -> ayni origin (ters vekil), http -> <host>:8090 */
  function suggestBase() {
    var loc = g.location;
    if (!loc || !loc.hostname) return '';                                   /* file:// (Tizen wgt, Android asset) */
    if (/(^|\.)androidplatform\.net$/i.test(loc.hostname) || g.DZAndroid) return '';   /* Android WebView kabugu (appassets) */
    if (/^https:$/i.test(loc.protocol || '')) return 'https://' + loc.host;
    if (/^http:$/i.test(loc.protocol || '')) return 'http://' + loc.hostname + ':' + PORT;
    return '';
  }
  var BASE = autoBase();
  function cleanKey(v) { return String(v || '').replace(/\s+/g, ''); }
  var KEY = cleanKey(lsGet(K_KEY));
  /* url sunucumuza mi gidiyor (yalniz o zaman anahtar basligi eklenir; ucuncu taraf CDN'e asla) */
  function isServerUrl(u) { return !!(BASE && u && String(u).indexOf(BASE + '/') === 0); }
  /* imzali akis vekili basliksiz calisir: ozel baslik her parcada CORS on ucusu dogurmasin */
  function keyFor(u) { return KEY && isServerUrl(u) && String(u).indexOf(BASE + '/api/stream-proxy/') !== 0 ? KEY : ''; }

  function dbgOn() { try { return !!(g.DZDebug && g.DZDebug.enabled()); } catch (e) { return false; } }
  /* istemci platformu: tizen | android (WebView kabugu) | browser (sunucu engine_hint + motor istatistigi icin) */
  function platform() {
    try {
      var ua = (g.navigator && g.navigator.userAgent) || '';
      if (/Tizen|SMART-TV/i.test(ua)) return 'tizen';
      if (g.DZAndroid || /Android/i.test(ua)) return 'android';
    } catch (e) {}
    return 'browser';
  }
  function enc(v) { return encodeURIComponent(String(v == null ? '' : v)); }

  /* hata nesnesi: 401 JSON auth_required (anahtar yok/yanlis) | JSON olmayan 401 (vekilin giris sayfasi, orn. NetBird PIN) */
  function authErr(status, data) {
    var code = data && data.error && data.error.code;
    if (status === 401 && code === 'auth_required') return { status: 401, code: 'auth_required', message: 'Erişim anahtarı gerekli ya da yanlış' };
    if (status === 401 && !code) return { status: 401, code: 'proxy_auth', message: 'Sunucu önünde giriş koruması var (vekil)' };
    if (status === 429 && code === 'rate_limited') return { status: 429, code: 'rate_limited', message: 'Çok fazla hatalı deneme; birkaç dakika sonra yeniden deneyin' };
    return null;
  }
  /* key: undefined = kayitli anahtar; base/key verilirse (bağlantı testi) onAuthError CAGRILMAZ */
  function request(method, path, body, cb, base, timeout, key) {
    var x = new XMLHttpRequest(), B = base || BASE, K = key === undefined ? KEY : cleanKey(key), probing = !!base || key !== undefined;
    var done = false;
    function fin(err, data) { if (done) return; done = true; cb(err, data); }
    /* iptal tutamaci: abort() sonrasi geri cagri HIC calismaz (arama: eski istek yok sayilir) */
    var handle = { abort: function () { if (done) return; done = true; try { x.abort(); } catch (e) {} } };
    try {
      x.open(method, B + path, true);
      x.timeout = timeout || 12000;
      var form = !!(body && g.FormData && body instanceof g.FormData);   /* multipart: tarayici sinir (boundary) basligini kendisi koyar */
      if (body && !form) x.setRequestHeader('Content-Type', 'application/json');
      if (K) x.setRequestHeader(KEY_HDR, K);
      x.onreadystatechange = function () {
        if (x.readyState !== 4) return;
        var data = null;
        try { data = x.responseText ? JSON.parse(x.responseText) : null; } catch (e) { data = null; }
        if (x.status >= 200 && x.status < 300 && data) return fin(null, data);
        var msg = x.status ? ('Sunucu hatası (' + x.status + ')') : ('Sunucuya ulaşılamıyor: ' + B);
        if (data && data.error && data.error.message) msg = data.error.message;
        var ae = authErr(x.status, data);
        if (ae && !probing && ae.code === 'auth_required' && DZP.api.onAuthError) { try { DZP.api.onAuthError(ae); } catch (e) {} }
        fin(ae || { status: x.status, message: msg }, null);
      };
      x.ontimeout = function () { fin({ status: 0, message: 'Sunucu zaman aşımı' }, null); };
      x.send(form ? body : (body ? JSON.stringify(body) : null));
    } catch (e) { fin({ status: 0, message: 'Sunucuya ulaşılamıyor: ' + B }, null); }
    return handle;
  }

  DZP.api = {
    base: function () { return BASE; },
    hasBase: function () { return !!BASE; },
    normalizeServer: normalizeServer,
    serverCandidates: serverCandidates,
    suggestBase: suggestBase,
    /* adresi (+ istege bagli erisim anahtarini) kaydet ve etkinlestir (bos = kaydi sil, otomatik adrese don); key undefined = anahtara dokunma */
    setBase: function (v, key) {
      var s = cleanBase(normalizeServer(v)); lsSet(K_SERVER, s); BASE = s || autoBase();
      if (key !== undefined) { KEY = cleanKey(key); lsSet(K_KEY, KEY); }
      return BASE;
    },
    key: function () { return KEY; },
    keyHeader: KEY_HDR,
    isServerUrl: isServerUrl,
    /* bu url'ye giden istege eklenecek anahtar ('' = ekleme): hls.js xhrSetup, altyazi fetch, uzak log */
    keyFor: keyFor,
    /* app.js atar: calisirken 401 auth_required (anahtar yenilendi/silindi) -> sunucu adresi ekranina */
    onAuthError: null,
    /* KAYDETMEDEN aday adres + anahtari dene: /api/health {status:'ok'} degilse hata; sonra /api/profiles (anahtar gerekiyorsa
       401 -> {code:'auth_required'} ya da vekil girisi {code:'proxy_auth'}). key verilmezse ('' dahil) anahtarsiz. */
    probe: function (base, key, cb) {
      if (typeof key === 'function') { cb = key; key = ''; }
      request('GET', '/api/health', null, function (err, data) {
        if (err) return cb(err);
        if (!data || data.status !== 'ok') return cb({ status: 0, message: 'Bu adres bir diziflix sunucusu değil' });
        request('GET', '/api/profiles', null, function (err2) {
          if (err2 && err2.code) return cb(err2);
          cb(null, data);
        }, base, 6000, key || '');
      }, base, 6000, key || '');
    },
    img: function (p) {
      if (!p) return '';
      if (/^(https?:)?\/\//i.test(p) || p.indexOf('data:') === 0) return p;
      return BASE + (p.charAt(0) === '/' ? p : '/' + p);
    },
    /* /img yollarinda boyut degistir (kucuk TV bellegi icin) */
    sized: function (p, w, h) {
      if (!p) return '';
      var s = p.replace(/([?&])w=\d+/, '$1w=' + w).replace(/([?&])h=\d+/, '$1h=' + h);
      return DZP.api.img(s);
    },
    profiles: function (cb) { request('GET', '/api/profiles', null, cb); },
    avatars: function (cb) { request('GET', '/api/avatars', null, cb); },
    createProfile: function (body, cb) { request('POST', '/api/profiles', body, cb); },
    updateProfile: function (id, body, cb) { request('PUT', '/api/profiles/' + enc(id), body, cb); },
    deleteProfile: function (id, cb) { request('DELETE', '/api/profiles/' + enc(id), null, cb); },
    sections: function (pid, cb) { request('GET', '/api/sections?profile=' + enc(pid), null, cb); },
    section: function (id, pid, cb) { request('GET', '/api/section/' + enc(id) + '?profile=' + enc(pid), null, cb); },
    row: function (id, pid, offset, limit, cb) {
      request('GET', '/api/row/' + enc(id) + '?profile=' + enc(pid) + '&offset=' + (offset || 0) + '&limit=' + (limit || 40), null, cb);
    },
    /* canli aramayla ilk kez bulunan dizi: ilk aciliste sezon/bolum katalogu o anda getirilir (API.md) -> uzun zaman asimi */
    detail: function (id, pid, cb) { request('GET', '/api/detail/' + enc(id) + '?profile=' + enc(pid), null, cb, null, 180000); },
    /* arama (js/search.js). Yerel kutuphane: /api/catalog?q= (>= 2 karakter); canli site aramasi: /api/search (>= 3 karakter,
       {items, total, remote, remote_error, remote_sites}). Ikisi de iptal tutamaci ({abort}) dondurur. */
    catalogSearch: function (q, pid, limit, cb) {
      return request('GET', '/api/catalog?q=' + enc(q) + '&profile=' + enc(pid) + '&limit=' + (limit || 20) + '&sort=new', null, cb, null, 15000);
    },
    search: function (q, pid, limit, cb) {
      return request('GET', '/api/search?q=' + enc(q) + '&profile=' + enc(pid) + '&limit=' + (limit || 20), null, cb, null, 90000);
    },
    /* Canli TV kanalini HERKESTEN sil (admin geri alabilir): {ok, id, removed:true}; bilinmeyen id 404 */
    removeChannel: function (id, cb) { request('DELETE', '/api/live/channels/' + enc(id), null, cb); },
    mylistAdd: function (pid, id, cb) { request('POST', '/api/mylist', { profile: pid, item_id: id }, cb); },
    mylistDel: function (pid, id, cb) { request('DELETE', '/api/mylist/' + enc(id) + '?profile=' + enc(pid), null, cb); },
    /* oynatici (js/player.js). streams: {streams[], subtitles[], audio[], resume_position, duration, finder?} (API.md) */
    platform: platform,
    streams: function (id, pid, ep, kind, cb) {
      var p = '/api/streams/' + enc(id) + '?profile=' + enc(pid);
      if (ep) p += '&episode=' + enc(ep);
      if (kind) p += '&kind=' + enc(kind);
      p += '&platform=' + platform();   /* streams[].engine_hint bu platform icin (API.md) */
      if (dbgOn()) p += '&debug=1';   /* Hata ayiklama acik: yanitta debug_trace (cozumleme adimlari) */
      request('GET', p, null, cb, null, 90000);
    },
    /* {state: idle|searching|found|not_found} */
    sourceFinder: function (id, ep, cb) {
      var q = (ep ? ['episode=' + enc(ep)] : []).concat(dbgOn() ? ['debug=1'] : []);
      request('GET', '/api/source-finder/' + enc(id) + (q.length ? '?' + q.join('&') : ''), null, cb, null, 8000);
    },
    /* {attempt_token, event: success|failure, code, engine (native|hlsjs|avplay|embed), platform, provider?, detail?(<=120, yalniz failure), hlsjs?(yalniz true)} -> {ok, finder?} */
    playbackReport: function (pid, payload, cb) {
      var b = {}, k;
      for (k in payload) if (Object.prototype.hasOwnProperty.call(payload, k)) b[k] = payload[k];
      if (b.detail === undefined || b.detail === null || b.detail === '') delete b.detail; else b.detail = String(b.detail).slice(0, 120);
      if (b.hlsjs !== true) delete b.hlsjs;
      request('POST', '/api/playback-report?profile=' + enc(pid), b, cb || function () {});
    },
    /* {profile, item_id, episode_id, position, duration} (misafirde sunucu yazmaz) */
    /* arka plan gorselleri (API.md "Arka planlar"); yukleme multipart alan `file` */
    backgrounds: function (cb) { request('GET', '/api/backgrounds', null, cb, null, 8000); },
    uploadBackground: function (file, cb) {
      var fd = new g.FormData();
      fd.append('file', file, file.name || 'image');
      request('POST', '/api/backgrounds', fd, cb, null, 120000);
    },
    progress: function (body, cb) { request('POST', '/api/progress', body, cb || function () {}, null, 8000); }
  };
})(window);
