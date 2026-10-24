/* app.js - diziflix client ana ekran (ES5, build yok).
   Tasarim: MagicPath "Google TV Home - C - Clean Frame" (List/Dock ana ekran, Appearance, All apps, LaunchVeil);
   fikirler CleanFrame'den (MIT). Ekranlar: server (ilk acilis sunucu adresi) -> profiles (her soguk acilista) -> home ->
   section/grid -> detail -> player (js/player.js; Devam Et karti da dogrudan oynaticiya); settings (Gorunum); pedit (profil ekle/duzenle). */
(function (g) {
  'use strict';
  var DZP = g.DZP, api = DZP.api, doc = g.document;

  /* ---------- sabitler (tasarimdan birebir) ---------- */
  var W = 1920, H = 1080;
  var PE = 140;                 /* sol kenar bosluk */
  var BRAND_W = 107;            /* Dock: sol ustteki marka isareti (83px) + bosluk; saat/selamlama bu kadar saga */
  var LIST_TOP = 236, ROW_H = 128, VIS = 6;
  var DOCK = { left: 208, top: 856, width: 1504, height: 152, pad: 16, visible: 6 };   /* dock: kucuk, alt kenarda (856..1008) */
  var GRID = { left: 140, cols: 5, w: 300, h: 244, gap: 28 };
  var GRID_STRIDE = GRID.h + GRID.gap;
  /* bolum sayfasi: yatay satirlar (kart = izgara karti) */
  var SEC = { top: 268, stride: 330, cw: 300, gap: 28, vis: 5 };
  /* ana ekran "Devam Et" seridi: List = vitrin altinda (kontrollerin solu), Dock = dock'un ustunde */
  /* List sag sutunu x=920 (menu odak kutusu en fazla ~840'a kadar; >=80px bosluk), seridin sagi kontrollerin solunda (1600) */
  var CONT_L = { left: 912, top: 768, w: 208, h: 117, gap: 16, width: 688, labelW: 0 };
  /* Dock: baslik seridin USTUNDE (612), kartlar 300x169 (16:9) 658..827, dock 856'da */
  var CONT_D = { left: 200, top: 612, w: 300, h: 169, gap: 20, width: 1512, labelW: 0, big: 1 };
  var HERO_D_TOP = 224;   /* dock vitrini: selamlama (138..172) ile arasinda ~52px */
  var CONT_ENTRY = { id: 'continue', title: 'İzlemeye Devam Et' };
  var SOON_TEXT = { dvr: 'Kaydedilen yayınlar burada listelenecek.' };
  /* Canli TV kanal kartlari (card_kind:"channel"; gorsel/logo yok): kategoriye gore sabit ton + bas harf + kanal no */
  var CH_CAT = { news: 'Haber', 'top-news': 'Haber', sports: 'Spor', kids: 'Çocuk', animation: 'Çocuk', music: 'Müzik', general: 'Genel',
    entertainment: 'Eğlence', documentary: 'Belgesel', religious: 'Din', series: 'Dizi', movies: 'Film', comedy: 'Komedi', cooking: 'Yemek',
    culture: 'Kültür', education: 'Eğitim', family: 'Aile', lifestyle: 'Yaşam', business: 'Ekonomi', legislative: 'Meclis', outdoor: 'Doğa',
    travel: 'Gezi', weather: 'Hava', science: 'Bilim', shop: 'Alışveriş', show: 'Şov', classic: 'Klasik', relax: 'Rahatla', auto: 'Otomobil' };
  var CH_TONE = { news: 200, 'top-news': 200, sports: 140, kids: 32, animation: 32, music: 290, religious: 44, documentary: 172,
    movies: 228, series: 252, entertainment: 330, general: 210, business: 190, culture: 18, education: 160 };
  /* kontrol menusu: Ara (kutuphane + canli site aramasi) · Listem · Ayarlar · profil (avatar + ad; OK = hizli profil degistirici).
     List: Ara/Listem/Ayarlar sag alt sutunda, profil en ustte saatin solunda; Dock: ustte tek sira, profil en sagda */
  var CTRLS = [{ id: 'search', label: 'Ara' }, { id: 'mylist', label: 'Listem' }, { id: 'settings', label: 'Ayarlar' }, { id: 'profile', label: '' }];
  var PF = 3;   /* CTRLS icinde profil */
  var SEARCH_SVG = '<svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 5 5"/></svg>';
  function ctrlIdx(id) { for (var i = 0; i < CTRLS.length; i++) if (CTRLS[i].id === id) return i; return 0; }
  var SHADE_LIST = 'linear-gradient(90deg, rgba(8,8,10,.44) 0%, rgba(8,8,10,.20) 36%, rgba(8,8,10,0) 46%, rgba(8,8,10,.16) 100%), linear-gradient(0deg, rgba(8,8,10,.30), rgba(8,8,10,0) 34%), radial-gradient(ellipse 520px 220px at 1780px 104px, rgba(8,8,10,.22), rgba(8,8,10,0))';
  var SHADE_DOCK = 'linear-gradient(180deg, rgba(8,8,10,.34), rgba(8,8,10,0) 24%), linear-gradient(0deg, rgba(8,8,10,.30), rgba(8,8,10,0) 42%)';
  /* ortalanmis ekranlar (sunucu adresi, profil secimi): merkezde hafif karartma */
  var SHADE_CENTER = 'radial-gradient(ellipse 1100px 620px at 960px 560px, rgba(8,8,10,.34), rgba(8,8,10,.10) 70%, rgba(8,8,10,0)), linear-gradient(0deg, rgba(8,8,10,.26), rgba(8,8,10,0) 34%)';
  var CENTER_SCREENS = { server: 1, profiles: 1 };
  var K_LOOK = 'dzproto.look', K_PROFILE = 'dzproto.profile';
  var MANUAL_FADE_MS = 520;

  /* ---------- yardimcilar ---------- */
  function el(tag, cls, txt) {
    var e = doc.createElement(tag);
    if (cls) e.className = cls;
    if (txt !== undefined && txt !== null) e.textContent = txt;
    return e;
  }
  /* marka gorseli (img/*.png; dekoratif, odak/gezinme disi; yuklenemezse gizlenir) */
  function brandImg(cls, src, alt) {
    var im = el('img', cls);
    im.src = src; im.alt = alt || '';
    im.onerror = function () { im.style.display = 'none'; };
    return im;
  }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function tr(x, y) { return 'translate(' + x + 'px,' + y + 'px)'; }
  function setT(e, v) { e.style.webkitTransform = v; e.style.transform = v; }
  function setMask(e, v) { e.style.webkitMaskImage = v; e.style.maskImage = v; }
  function store(k, v) {
    try { if (v === undefined) return g.localStorage.getItem(k); g.localStorage.setItem(k, v); } catch (e) {}
    return null;
  }
  function pad2(n) { return (n < 10 ? '0' : '') + n; }
  function chev(left) {
    return '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="' +
      (left ? 'm15 5-7 7 7 7' : 'm9 5 7 7-7 7') + '"/></svg>';
  }
  /* detay eylem satiri ikonlari: ince cizgi, currentColor (odakta metinle birlikte koyulasir) */
  var ROW_ICONS = {
    play: '<path d="M7 4.5v15l12.5-7.5z"/>',
    restart: '<path d="M3.5 12a8.5 8.5 0 1 0 2.5-6"/><path d="M3.5 3.5V8H8"/>',
    trailer: '<rect x="3" y="9" width="18" height="11" rx="2"/><path d="m3.6 9-.9-3.4L19 1.8l.9 3.4z"/><path d="m8.2 3.3 2.6 3.6M13.4 2 16 5.6"/>',
    season: '<path d="m12 3 9 4.5-9 4.5-9-4.5z"/><path d="m3 12 9 4.5 9-4.5"/><path d="m3 16.5 9 4.5 9-4.5"/>',
    add: '<path d="M12 5v14M5 12h14"/>',
    check: '<path d="m4.5 12.5 5 5 10-11"/>'
  };
  function rowIcon(name) {
    return '<svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      ROW_ICONS[name] + '</svg>';
  }
  /* dock bolum ikonlari (bolum id'si -> ince cizgi, currentColor) */
  var DOCK_ICONS = {
    featured: '<path d="m12 3.2 2.7 5.5 6 .9-4.35 4.25 1 6L12 17l-5.35 2.85 1-6L3.3 9.6l6-.9z"/>',
    series: '<rect x="3" y="5" width="18" height="12" rx="2"/><path d="M8 21h8M12 17v4"/>',
    movies: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>',
    live_tv: '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="m8 3 4 4 4-4"/><circle cx="12" cy="13.5" r="2"/>',
    anime: '<path d="M12 3a9 9 0 1 0 9 9"/><path d="M15.5 3.5 17 7l3.5 1.5L17 10l-1.5 3.5L14 10l-3.5-1.5L14 7z"/>',
    dvr: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="3.5"/>'
  };
  function dockIcon(id) {
    var d = DOCK_ICONS[id] || DOCK_ICONS.featured;
    return '<svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + d + '</svg>';
  }
  function hourNow() { return new Date().getHours(); }

  /* ---------- durum ---------- */
  var BG_MODES = { posters: 1, mine: 1, dark: 1 };   /* Ayarlar > Arka plan */
  var LOOK_DEF = { bg: 'posters', layout: 'list', focus: 'glass', material: 'frost', rev: 3 };
  var LOOK_OK = { layout: { list: 1, dock: 1 }, focus: { glass: 1, type: 1 }, material: { soft: 1, frost: 1, tint: 1 } };
  var S = {
    look: loadLook(), screen: '', zone: 'list', idx: 0, ctrl: 0,
    entries: [], heroIdx: 0, pid: DZP.qs('profile') || null, profiles: [], pname: '',
    gen: 0, error: null, loading: true, grid: null, detail: null, setRow: 0, bgKind: null, bgKey: null, up: '', upBusy: false,
    cont: { items: [], idx: 0 }, sec: null, soon: null
  };
  function loadLook() {
    var o = {}, k, raw = null;
    try { raw = JSON.parse(store(K_LOOK) || 'null'); } catch (e) { raw = null; }
    for (k in LOOK_DEF) o[k] = (raw && raw[k]) ? raw[k] : LOOK_DEF[k];
    if (!raw || raw.rev !== LOOK_DEF.rev) o.rev = LOOK_DEF.rev;
    if (!BG_MODES[o.bg]) o.bg = LOOK_DEF.bg;   /* eski modlar (gun dongusu sahnesi, sabit gorsel) -> Afisler */
    for (k in LOOK_OK) if (!LOOK_OK[k][o[k]]) o[k] = LOOK_DEF[k];   /* bilinmeyen deger -> varsayilan */
    return o;
  }
  function saveLook() { try { store(K_LOOK, JSON.stringify(S.look)); } catch (e) {} }

  /* ---------- sahne ---------- */
  var stage = doc.getElementById('stage');
  var screenEl = doc.getElementById('screen');
  var veil = doc.getElementById('veil');
  var bgLayers = doc.getElementById('bg-layers');
  var bgShade = doc.getElementById('bg-shade');
  var dimImg = doc.getElementById('dim-img');

  /* TV ekran klavyesi (IME) acilinca pencere yuksekligi azalir -> sahne kuculmesin: bir girdi odaktayken (Tizen'de her zaman)
     genislik ayni ve yukseklik azaldiysa son "klavyesiz" olcu korunur; klavye kapaninca (yukseklik geri / blur) normal olcek */
  var FIT = { w: 0, h: 0 };
  function typingNow() { var a = doc.activeElement; return !!(a && a.tagName === 'INPUT'); }
  function fit() {
    var vw = g.innerWidth || doc.documentElement.clientWidth, vh = g.innerHeight || doc.documentElement.clientHeight;
    if (FIT.w && vw === FIT.w && vh < FIT.h && (typingNow() || g.tizen)) vh = FIT.h;
    else { FIT.w = vw; FIT.h = vh; }
    var s = Math.min(vw / W, vh / H);
    setT(stage, 'translate(' + Math.round((vw - W * s) / 2) + 'px,' + Math.round((vh - H * s) / 2) + 'px) scale(' + s + ')');
  }

  function applyClasses() {
    var L = S.look, b = curBg();
    var c = ['m-' + L.material, 'f-' + L.focus, 'l-' + L.layout];
    if (!b) c.push('flat'); else if (!b.blur) c.push('no-blur');
    if (!S.bgKind && L.bg !== 'dark') c.push('bg-grad');          /* gorsel yok (Siyah disi): koyu gradyan */
    if (S.screen === 'player') c.push('is-play');                 /* oynatici: arka plan + ekran DOM'u gizli (TV bellek/CPU) */
    else if (S.screen !== 'home' && !CENTER_SCREENS[S.screen]) c.push('is-dim');
    stage.className = c.join(' ');
    bgShade.style.background = S.bgKind ? (CENTER_SCREENS[S.screen] ? SHADE_CENTER : (S.look.layout === 'list' ? SHADE_LIST : SHADE_DOCK)) : 'none';
  }

  /* ---------- arka plan ----------
     Modlar (Ayarlar): posters = vitrin ogesinin genis gorseli (varsayilan), mine = Gorsellerim (sunucuya yuklenen gorseller,
     ~60 sn'de bir karisik sirayla crossfade), dark = Siyah. Afis yoksa (Canli TV/DVR, hata) Gorsellerim'den biri, o da yoksa
     koyu gradyan. Etkin tur S.bgKind: 'poster' | 'mine' | null. Her tur iki kareli bir "fader" (PB / MB): ust kare opak, yeni
     gorsel ustte 0 -> 1 belirir (yalniz opacity, canli blur YOK). Tur degisince yeni katman eskinin USTUNDE belirir. */
  var layerEls = {}, fadeTimer = null;
  function currentBg() {
    var v = S.look.bg;
    if (v === 'dark') return null;
    if (v === 'posters' && PB.src) return 'poster';
    return DZP.BG_LIST.length ? 'mine' : null;
  }
  function activeFader() { return S.bgKind === 'poster' ? PB : (S.bgKind === 'mine' ? MB : null); }
  /* etkin arka planin ozeti (cam, onizleme, karartma icin): {name, src, blur} */
  function curBg() {
    var F = activeFader(), sl = F && F.slots[F.cur];
    if (!sl) return null;
    return { name: F === PB ? 'Afişler (bölüme göre)' : 'Görsellerim', src: sl.src || '', blur: sl.blur || '' };
  }
  function buildFadeLayer(F) {
    var root = el('div', 'bg-layer fader');
    F.frs = [el('div', 'fd-fr'), el('div', 'fd-fr')];
    root.appendChild(F.frs[0]); root.appendChild(F.frs[1]);
    root.appendChild(el('div', 'poster-scrim'));
    F.layer = root;
    return root;
  }
  function applyBg() {
    var key = currentBg(), changed = key !== S.bgKey;
    S.bgKey = key; S.bgKind = key;
    var F = activeFader(), layer = null;
    if (changed && F) {
      if (F === MB) mineStart();
      layer = layerEls[key] = F.layer || buildFadeLayer(F);
      fdPaint(F, F.frs, 'src', 0);
    }
    applyClasses();
    updateDim();
    updateMats();
    if (!changed) return;
    var k;
    if (!F) {
      for (k in layerEls) layerEls[k].style.opacity = 0;
      teardown(null);
      return;
    }
    layer.style.transition = 'none';
    layer.style.opacity = 0;
    bgLayers.appendChild(layer);          /* en uste al: yeni gorsel eskinin USTUNDE belirir (ortada karanlik cukur yok) */
    var im = new Image();
    im.onload = im.onerror = function () {
      if (S.bgKey !== key) return;
      void layer.offsetWidth;
      layer.style.transition = 'opacity ' + MANUAL_FADE_MS + 'ms cubic-bezier(.2,.8,.2,1)';
      layer.style.opacity = 1;
      if (fadeTimer) g.clearTimeout(fadeTimer);
      fadeTimer = g.setTimeout(function () {
        for (var j in layerEls) if (j !== key) { layerEls[j].style.transition = 'none'; layerEls[j].style.opacity = 0; }
        teardown(key);
      }, MANUAL_FADE_MS + 60);
    };
    im.src = (F.slots[F.cur] || {}).src || '';
  }
  /* etkin olmayan katmanlari kaldir */
  function teardown(keep) {
    for (var k in layerEls) {
      if (k === keep) continue;
      var e = layerEls[k];
      if (e.parentNode) e.parentNode.removeChild(e);
      delete layerEls[k];
      if (k === 'poster') { PB.layer = null; PB.frs = null; }
      if (k === 'mine') { MB.layer = null; MB.frs = null; mineStop(); }
    }
  }
  function updateDim() {
    var b = curBg();
    dimImg.style.backgroundImage = b && (b.blur || b.src) ? 'url("' + (b.blur || b.src) + '")' : 'none';
  }
  /* iki kare: ust (F.cur) opak, alt altta; ms > 0: yeni ust kare 0 -> 1 belirir (eski altta opak kalir, karanlik cukur yok) */
  function fdPaint(F, frs, kind, ms) {
    if (!frs) return;
    var top = frs[F.cur], base = frs[1 - F.cur], ts = F.slots[F.cur], bs = F.slots[1 - F.cur];
    var tu = ts ? ts[kind] : '', bu = bs ? bs[kind] : '';
    if (base.fdU !== bu) { base.style.backgroundImage = bu ? 'url("' + bu + '")' : 'none'; base.fdU = bu; }
    base.style.zIndex = 1; top.style.zIndex = 2;
    base.style.transition = 'none';
    if (!ms) base.style.opacity = 0;
    top.style.transition = 'none';
    if (ms) { top.style.opacity = 0; void top.offsetWidth; }
    if (top.fdU !== tu) { top.style.backgroundImage = tu ? 'url("' + tu + '")' : 'none'; top.fdU = tu; }
    if (ms) { void top.offsetWidth; top.style.transition = 'opacity ' + ms + 'ms linear'; }
    top.style.opacity = 1;
  }
  /* fader'a yeni gorsel: ust kare degisir, arka plan + cam paneller crossfade */
  function fdShow(F, slot, ms) {
    F.cur = 1 - F.cur;
    F.slots[F.cur] = slot;
    fdPaint(F, F.frs, 'src', ms);
    var ml = stage.querySelectorAll('.mat-img');
    for (var i = 0; i < ml.length; i++) matFade(ml[i], F, ms);
    applyClasses();
    updateDim();
  }

  /* ---------- Gorsellerim: sunucuya yuklenen gorseller, karisik sira, ~60 sn'de bir crossfade ---------- */
  var MB = { slots: [null, null], cur: 0, layer: null, frs: null, order: [], pos: 0, last: -1, timer: null };
  var MINE_ROTATE_MS = 60000, MINE_FADE_MS = 2000;
  function mineNextItem() {
    var n = DZP.BG_LIST.length;
    if (!n) return null;
    if (MB.order.length !== n || MB.pos >= n) { MB.order = DZP.shuffleOrder(n, MB.last); MB.pos = 0; }
    MB.last = MB.order[MB.pos++];
    var b = DZP.BG_LIST[MB.last];
    return { src: b.src, blur: b.blur };
  }
  function mineStart() {
    var sl = MB.slots[MB.cur], i, ok = false;
    for (i = 0; sl && i < DZP.BG_LIST.length; i++) if (DZP.BG_LIST[i].src === sl.src) ok = true;   /* silinmis gorsel kalmasin */
    if (!MB.timer) MB.timer = g.setInterval(mineRotate, MINE_ROTATE_MS);
    if (ok) return false;
    MB.slots = [null, null]; MB.slots[MB.cur] = mineNextItem();
    return true;
  }
  function mineStop() { if (MB.timer) { g.clearInterval(MB.timer); MB.timer = null; } }
  function mineRotate() {
    if (S.screen === 'player' || S.bgKind !== 'mine' || DZP.BG_LIST.length < 2) return;   /* oynaticida durur */
    var it = mineNextItem(), im = new Image();
    im.onload = function () { if (S.bgKind === 'mine' && MB.frs && S.screen !== 'player') fdShow(MB, it, MINE_FADE_MS); };
    im.src = it.src;
  }
  /* liste yenilendi (acilis, sunucu degisti, yukleme): sira sifirlanir, mod yeniden degerlendirilir */
  function reloadBackgrounds(cb) {
    DZP.loadBackgrounds(function (err) {
      MB.order = []; MB.pos = 0;
      if (S.bgKind === 'mine' && DZP.BG_LIST.length && mineStart() && MB.frs) {   /* gosterilen gorsel silinmis: yenisine gec */
        var sl = MB.slots[MB.cur]; MB.slots[MB.cur] = null; MB.cur = 1 - MB.cur; fdShow(MB, sl, MANUAL_FADE_MS);
      }
      applyBg();
      if (cb) cb(err);
    });
  }

  /* ---------- afis arka plani (Ayarlar: "Afişler (bölüme göre)", varsayilan) ----------
     Ana menude vitrinin (odaktaki bolum / Devam Et karti) ogesinin genis gorseli (backdrop, yoksa kart) TAM EKRAN arka plan olur;
     yazi blogu ayni ogeyle esitlenir (gorsel yuklenince). 2 katman, yalniz opacity crossfade (~1 sn).
     Gorsel: /img vekilinden 1280x720 (net; canli blur YOK, ustte sabit karartma + yazi tarafinda gradyan). Cam paneller icin
     yuklenen gorsel bir kez canvas'ta kucultulup buyutulur (64x36 -> 320x180 JPEG dataURL = on-bulanik kopya); canvas
     kullanilamazsa cam yalniz ton. Icerik/gorsel yoksa (Canli TV, DVR) Gorsellerim'e, o da yoksa koyu gradyana donulur. */
  var PB = { want: '', src: '', slots: [null, null], cur: 0, layer: null, frs: null };
  var POSTER_FADE_MS = 1000, SECTION_FADE_MS = 400;
  function posterMode() { return S.look.bg === 'posters'; }
  function preblur(im) {
    try {
      var a = doc.createElement('canvas'), b = doc.createElement('canvas');
      a.width = 64; a.height = 36; b.width = 320; b.height = 180;
      a.getContext('2d').drawImage(im, 0, 0, 64, 36);
      var cb2 = b.getContext('2d');
      cb2.imageSmoothingEnabled = true;
      cb2.drawImage(a, 0, 0, 320, 180);
      return b.toDataURL('image/jpeg', 0.7);
    } catch (e) { return ''; }      /* CORS'suz gorsel (tainted canvas) ya da canvas yok: cam yalniz ton */
  }
  /* ---------- afis onbellegi (TV): URL -> hazir {blur}; en fazla PC_MAX (en eski cikar), son PC_KEEP Image nesnesi elde
     tutulur (cozulmus bitmap). Her bolumun ILK vitrin gorseli kalici (PC.pin; sinirlardan muaf, bolum sayisi kadar).
     Yuklenen gorsel img.decode() ile cozulur, sonra hazir sayilir. Cam icin on-bulanik kopya (canvas) gecisi BEKLETMEZ:
     bosta, sirayla uretilir (PC.bq) ve gosterilen gorselin cami gelince yumusakca guncellenir.
     Arka plan kuyrugu ayni anda en fazla PC_CONC; acil (gorunen/acilis) istek kuyrugu beklemez.
     CORS'lu yukleme basarisizsa (eski tarayici onbellegi ACAO'suz) CORS'suz yeniden denenir: gorsel gelir, cam yalniz ton. */
  var PC = { ready: {}, order: [], keep: [], bad: {}, wait: {}, live: {}, q: [], run: 0, pin: {}, pinWant: {}, bq: [], bt: null };
  var PC_MAX = 30, PC_KEEP = 8, PC_CONC = 2, PC_BAD_MS = 60000;
  function pcReady(u) {
    var r = PC.ready[u];
    if (r) { var i = PC.order.indexOf(u); if (i >= 0) PC.order.splice(i, 1); PC.order.push(u); }
    return r || null;
  }
  function pcPut(u, im) {
    if (!PC.ready[u]) PC.order.push(u);
    PC.ready[u] = { blur: '' };
    var i = 0;
    while (PC.order.length > PC_MAX && i < PC.order.length) {   /* kalici (pin) olanlar atlanir */
      if (PC.pin[PC.order[i]]) { i++; continue; }
      delete PC.ready[PC.order.splice(i, 1)[0]];
    }
    if (PC.pinWant[u]) PC.pin[u] = im;
    else { PC.keep.push(im); if (PC.keep.length > PC_KEEP) PC.keep.shift(); }
    PC.bq.push({ u: u, im: im });
    blurKick(400);
  }
  /* bosta on-bulaniklastirma: gecis sirasinda calismaz; gosterilen gorsel once */
  var PB_BUSY_UNTIL = 0;
  function blurKick(ms) {
    if (PC.bt) return;
    PC.bt = g.setTimeout(blurStep, ms);
  }
  function blurStep() {
    PC.bt = null;
    if (!PC.bq.length) return;
    var wait = PB_BUSY_UNTIL - Date.now();
    if (wait > 0) return blurKick(wait + 60);
    var cur = PB.slots[PB.cur], j = 0, i;
    for (i = 0; cur && i < PC.bq.length; i++) if (PC.bq[i].u === cur.src) { j = i; break; }
    var job = PC.bq.splice(j, 1)[0], r = PC.ready[job.u];
    if (r && !r.blur) {
      r.blur = preblur(job.im);
      if (r.blur) posterBlurReady(job.u, r.blur);
    }
    if (PC.bq.length) blurKick(60);
  }
  /* gosterilen afisin cam kopyasi sonradan geldi: cam panelleri kisa gecisle guncelle */
  function posterBlurReady(u, blur) {
    var sl = PB.slots[PB.cur];
    if (!sl || sl.src !== u || sl.blur) return;
    sl.blur = blur;
    if (S.bgKind !== 'poster') return;
    var ml = stage.querySelectorAll('.mat-img'), i;
    for (i = 0; i < ml.length; i++) if (ml[i].fd === PB) fdPaint(PB, ml[i].fdFr, 'blur', 300);
    updateDim();
  }
  function pcStart(u) {
    PC.live[u] = 1; PC.run++;
    function fin(im) {
      PC.run--; delete PC.live[u];
      if (im) pcPut(u, im); else PC.bad[u] = Date.now();
      var l = PC.wait[u] || [];
      delete PC.wait[u];
      for (var i = 0; i < l.length; i++) l[i](!!im);
      pcPump();
    }
    function attempt(cors) {
      var im = new Image();
      if (cors) im.crossOrigin = 'anonymous';
      im.onload = function () {
        var ok = function () { fin(im); };
        if (im.decode) { try { im.decode().then(ok, ok); return; } catch (e) {} }   /* cozulmus bitmap: gecis takilmaz */
        ok();
      };
      im.onerror = function () { if (cors) attempt(false); else fin(null); };
      im.src = u;
    }
    attempt(true);
  }
  function pcPump() {
    while (PC.run < PC_CONC && PC.q.length) {
      var u = PC.q.shift();
      if (PC.wait[u] && !PC.live[u]) pcStart(u);
    }
  }
  /* cb(ok); urgent: hemen baslar (kuyruk sinirini asar) */
  function pcLoad(u, urgent, cb) {
    if (!u) { if (cb) cb(false); return; }
    if (PC.ready[u]) { if (cb) cb(true); return; }
    if (PC.bad[u] && Date.now() - PC.bad[u] < PC_BAD_MS) { if (cb) cb(false); return; }
    if (PC.wait[u]) { if (cb) PC.wait[u].push(cb); }
    else { PC.wait[u] = cb ? [cb] : []; if (!urgent) PC.q.push(u); }
    if (urgent && !PC.live[u]) pcStart(u);
  }
  /* ogenin aday gorselleri: genis (backdrop/still/kart), kart, dikey afis */
  function posterUrls(it) {
    if (!it) return [];
    var c = [wideSrc(it, 1280, 720), it.card ? api.sized(it.card, 780, 439) : '', it.portrait ? api.sized(it.portrait, 500, 750) : ''], a = [], i;
    for (i = 0; i < c.length; i++) if (c[i] && a.indexOf(c[i]) < 0) a.push(c[i]);
    return a;
  }
  /* buyuk gorsel hazir degilken hemen gosterilecek kucuk kopya (ayni gorsel, 320x180) */
  function posterSmall(it) { return it ? wideSrc(it, 320, 180) : ''; }
  function posterIsReady(it) { var u = posterUrls(it)[0]; return !!(u && PC.ready[u]); }
  function posterPrefetch(it) { pcLoad(posterUrls(it)[0], false); }
  /* ogenin gorselini goster (onbellekte ise hemen); yuklenince crossfade + cb(true). Aday kalmazsa cb(false): cagiran baska
     ogeye gecer ya da sahneye doner. Eski istekler yok sayilir. ms: gecis suresi (bolum degisimi kisa, vitrin dongusu uzun).
     Buyuk gorsel hazir degilse kucuk kopya once gelirse o gosterilir (cb o an), buyuk gelince ustune yumusakca gecer. */
  function posterFor(item, cb, ms) {
    var urls = posterUrls(item);
    if (!urls.length) { PB.want = ''; posterShow('', ''); if (cb) cb(false); return; }
    var tok = PB.want = urls.join('|'), told = false;
    function tell(ok) { if (told) return; told = true; if (cb) cb(ok); }
    var r0 = pcReady(urls[0]);
    if (r0) { posterShow(urls[0], r0.blur, ms); return tell(true); }
    var sm = posterSmall(item), big = false;
    if (sm && sm !== urls[0]) {
      pcLoad(sm, true, function (ok) {
        if (!ok || big || PB.want !== tok) return;
        var r = pcReady(sm);
        if (r) { posterShow(sm, r.blur, ms); tell(true); }
      });
    }
    (function next(i) {
      if (PB.want !== tok) return;
      if (i >= urls.length) { if (!told) tell(false); return; }
      var r = pcReady(urls[i]);
      if (r) { big = true; posterShow(urls[i], r.blur, told ? POSTER_FADE_MS : ms); tell(true); return; }
      pcLoad(urls[i], true, function (ok) { next(ok ? i : i + 1); });
    })(0);
  }
  /* acilis: her bolumun ilk 2 vitrin gorseli paralel (en fazla PRELOAD_MS beklenir), kalanlar bosta kuyrukla.
     Her bolumun ILK gorseli kalici tutulur (pin): bolum degisiminde aninda hazir. */
  var PRELOAD_MS = 4000;
  function posterBoot(cb) {
    var first = [], rest = [], i, k, done = false, n, pins = {};
    for (i = 0; i < S.entries.length; i++) {
      var its = S.entries[i].items || [];
      for (k = 0; k < its.length; k++) {
        var rk = k, u = posterUrls(its[k])[0];   /* rk: vitrin sirasi (0 = ilk gosterilen) */
        if (u && rk === 0) pins[u] = 1;
        if (!u || first.indexOf(u) >= 0) continue;
        var ri = rest.indexOf(u);
        if (rk < 2) { if (ri >= 0) rest.splice(ri, 1); first.push(u); }   /* baska bolumde sonda olsa da burada one cikar */
        else if (ri < 0) rest.push(u);
      }
    }
    PC.pinWant = pins;
    for (u in PC.pin) if (!pins[u]) delete PC.pin[u];   /* eski profil/menu pinleri birakilir */
    for (u in pins) if (PC.ready[u] && !PC.pin[u]) { delete PC.ready[u]; k = PC.order.indexOf(u); if (k >= 0) PC.order.splice(k, 1); }   /* hazir ama Image'i yok: pinlemek icin yeniden yukle */
    function fin() { if (done) return; done = true; for (var j = 0; j < rest.length; j++) pcLoad(rest[j], false); cb(); }
    n = first.length;
    if (!n) return fin();
    g.setTimeout(fin, PRELOAD_MS);
    for (i = 0; i < first.length; i++) pcLoad(first[i], true, function () { if (!--n) fin(); });
  }
  function posterShow(src, blur, ms) {
    if (S.screen === 'player') { PB.want = ''; return; }   /* oynaticida donme durur; cikista yeniden yuklenir */
    if (!src) { if (PB.src) { PB.src = ''; applyBg(); } return; }   /* Gorsellerim / gradyana don */
    var live = S.bgKind === 'poster' && !!PB.frs;
    if (live && PB.slots[PB.cur] && PB.slots[PB.cur].src === src) return;
    PB.src = src;
    if (!live) { PB.slots[PB.cur] = { src: src, blur: blur }; applyBg(); return; }    /* ilk gosterim: afis katmani ustte belirir */
    ms = ms || POSTER_FADE_MS;
    PB_BUSY_UNTIL = Date.now() + ms;
    fdShow(PB, { src: src, blur: blur }, ms);
  }

  /* cam malzemesi: onceden bulaniklastirilmis kopya (afis: canvas; Gorsellerim: sunucunun blur_url'i), panel konumuna hizali
     (CleanFrame "registered material"); etkin fader'in iki karesini ayni gecisle izler */
  function matFade(img, F, ms) {
    if (img.fd !== F) {
      img.innerHTML = ''; img.style.backgroundImage = 'none';
      img.fdFr = [el('div', 'fd-fr'), el('div', 'fd-fr')];
      img.appendChild(img.fdFr[0]); img.appendChild(img.fdFr[1]);
      img.fd = F; ms = 0;
    }
    fdPaint(F, img.fdFr, 'blur', ms);
  }
  function updateMats() {
    var F = activeFader(), list = stage.querySelectorAll('.mat-img'), i;
    for (i = 0; i < list.length; i++) {
      if (F) { if (list[i].fd !== F) matFade(list[i], F, 0); }   /* kurulu olanlar suren gecisi korur */
      else if (list[i].fd) { list[i].innerHTML = ''; list[i].fd = null; list[i].fdFr = null; }
    }
  }
  function makeMat(cls, x, y) {
    var m = el('div', 'mat ' + (cls || ''));
    var img = el('div', 'mat-img');
    var F = activeFader();
    if (F) matFade(img, F, 0);
    if (x !== undefined) setT(img, tr(-x, -y));
    m.appendChild(img);
    m.appendChild(el('div', 'mat-tone'));
    m.img = img;
    return m;
  }

  /* ---------- saat ---------- */
  function clockText() { var d = new Date(); return pad2(d.getHours()) + ':' + pad2(d.getMinutes()); }
  function greetText() {
    if (S.error) return S.error + ' · OK: yeniden dene';
    if (S.loading) return S.opening ? 'Açılıyor…' : 'Yükleniyor…';
    var h = hourNow();
    var w = h >= 5 && h < 12 ? 'Günaydın' : h >= 12 && h < 18 ? 'İyi günler' : h >= 18 && h < 22 ? 'İyi akşamlar' : 'İyi geceler';
    return S.pname ? w + ', ' + S.pname : w;
  }

  /* ---------- kart metinleri ---------- */
  function isChannel(it) { return !!(it && (it.card_kind === 'channel' || it.type === 'channel')); }
  function chCats(it) {
    var c = it && it.categories && it.categories.length ? it.categories : [], out = [];
    for (var i = 0; i < c.length && out.length < 2; i++) { var t = CH_CAT[c[i]] || c[i]; if (out.indexOf(t) < 0) out.push(t); }
    return out;
  }
  function chHue(it) {
    var c = it && it.categories ? it.categories : [], i, h = 0, n = String((it && it.title) || '');
    for (i = 0; i < c.length; i++) if (CH_TONE[c[i]] !== undefined) return CH_TONE[c[i]];
    for (i = 0; i < n.length; i++) h = (h * 31 + n.charCodeAt(i)) % 360;
    return h;
  }
  function chInitials(it) {
    var w = String((it && it.title) || '?').replace(/^\s+|\s+$/g, '').split(/\s+/), a = (w[0] || '?').charAt(0);
    if (w.length > 1 && /^[0-9A-Za-zÇĞİÖŞÜçğıöşü]/.test(w[1])) a += w[1].charAt(0);
    return a.toLocaleUpperCase ? a.toLocaleUpperCase('tr') : a.toUpperCase();
  }
  function metaLine(it) {
    var p = [];
    if (isChannel(it)) {
      p.push('Canlı');
      if (it.channel_no) p.push('Kanal ' + it.channel_no);
      var cc = chCats(it);
      if (cc.length) p.push(cc.join(', '));
      return p.join(' · ');
    }
    if (it.year) p.push(it.year);
    p.push(it.type === 'series' ? 'Dizi' : 'Film');
    if (it.genres && it.genres.length) p.push(it.genres.slice(0, 2).join(', '));
    if (it.rating) p.push('★ ' + (+it.rating).toFixed(1));
    return p.join(' · ');
  }
  function tagLine(it, entry) {
    if (it.episode_label) return it.episode_label;
    if (it.tagline) return it.tagline;
    return entry ? entry.title : '';
  }
  function cardSrc(it) {
    if (it.card_kind === 'episode' && it.has_still && it.still_url) return api.sized(it.still_url, 320, 180);
    return api.sized(it.card || it.backdrop, 320, 180);
  }
  function wideSrc(it, w, h) {
    if (it.card_kind === 'episode' && it.has_still && it.still_url && !it.has_backdrop) return api.sized(it.still_url, w, h);
    return api.sized(it.has_backdrop === false ? (it.card || it.backdrop) : (it.backdrop || it.card), w, h);
  }
  function entryLead(e) {
    if (!e || !e.items || !e.items.length) return null;
    return e.items[S.heroIdx % e.items.length];
  }

  /* =====================================================================
     ANA EKRAN (List | Dock)
     ===================================================================== */
  var HV = null;   /* canli ana ekran gorunumu */

  function buildHome(root, preview) {
    var L = S.look, list = L.layout === 'list', glass = L.focus === 'glass';
    var n = S.entries.length, v = { names: [], tiles: [], ctrls: [], widths: [], list: list, preview: preview };

    var ck = el('div', 'clock', clockText());
    var gr = el('div', 'greet' + (S.error ? ' warn' : ''), preview ? '' : greetText());
    var top = null;   /* List: sag ust sira = profil hapi + saat (dikey ortali) */
    if (list) {
      top = el('div', 'top-r'); top.style.right = '144px'; top.style.top = '72px';
      ck.style.position = 'relative'; ck.style.top = '0';
      top.appendChild(ck); root.appendChild(top);
      gr.style.right = '144px'; gr.style.textAlign = 'right';
    } else { ck.style.left = (PE + BRAND_W) + 'px'; gr.style.left = (PE + BRAND_W) + 'px'; root.appendChild(ck); }
    root.appendChild(gr);
    /* kucuk marka isareti: sol ust (List: menunun ustu; Dock: saatin solu, saat/selamlama BRAND_W kadar saga) */
    root.appendChild(brandImg('brand-mark', 'img/mascot-sm.png', 'DiziFlix'));
    /* acilis/yukleme: ortada kare logo (yukleme bitince renderHome yeniden kurar, kaybolur) */
    if (!preview && S.loading && !S.error) root.appendChild(brandImg('boot-logo', 'img/logo-square.png', 'DiziFlix'));
    v.clock = ck; v.greet = gr;

    if (list) {
      if (glass) {
        v.plate = makeMat('plate', 0, 0);
        root.appendChild(v.plate);
      }
      var wrap = el('div', 'list-wrap'), inner = el('div', 'list-in');
      for (var i = 0; i < n; i++) {
        var li = el('div', 'li');
        li.style.top = (i * ROW_H) + 'px';
        var nm = el('span', 'li-name', S.entries[i].title);
        li.appendChild(nm);
        bindPointer(li, 'list', i);
        inner.appendChild(li);
        v.names.push(nm);
      }
      wrap.appendChild(inner); root.appendChild(wrap);
      v.wrap = wrap; v.inner = inner;
    } else {
      var nt = (DOCK.width - DOCK.pad * 2) / DOCK.visible;
      v.nt = nt;
      root.appendChild(el('div', 'dock-scrim'));   /* afis ne kadar parlak olursa olsun dock okunur */
      var dock = makeMat('dock', DOCK.left, DOCK.top);
      var clip = el('div', 'dock-clip'), din = el('div', 'dock-in');
      var dpl = el('div', 'dock-plate');
      dpl.style.width = nt + 'px';
      din.appendChild(dpl);
      for (var j = 0; j < n; j++) {
        var e = S.entries[j], t = el('div', 'dt');
        t.style.left = (j * nt) + 'px'; t.style.width = nt + 'px';
        var ic = el('div', 'dt-ic'); ic.innerHTML = dockIcon(e.id); t.appendChild(ic);
        t.appendChild(el('div', 'dt-name', e.title));
        t.appendChild(el('div', 'dt-count', e.status === 'soon' ? 'Yakında' : ''));
        bindPointer(t, 'list', j);
        din.appendChild(t); v.tiles.push(t);
      }
      clip.appendChild(din); dock.appendChild(clip); root.appendChild(dock);
      v.dockIn = din; v.dockClip = clip; v.dockPlate = dpl;
    }

    /* Devam Et seridi (profilin devam edecegi yoksa hic yok) */
    if (S.cont.items.length) {
      var C = list ? CONT_L : CONT_D;
      var cs = el('div', 'cs');
      cs.style.left = C.left + 'px'; cs.style.top = C.top + 'px';
      var lab = el('div', 'cs-label t-shadow', CONT_ENTRY.title);
      cs.appendChild(lab);
      var cclip = el('div', 'cs-clip'), cin = el('div', 'cs-in');
      cclip.style.left = C.labelW + 'px'; cclip.style.top = '38px';
      cclip.style.width = C.width + 'px'; cclip.style.height = (C.h + 16) + 'px';
      cclip.appendChild(cin); cs.appendChild(cclip); root.appendChild(cs);
      v.cont = { box: cs, clip: cclip, inner: cin, tiles: {}, C: C };
    }

    var cw = el('div', 'ctrls');
    if (list) { cw.style.right = '122px'; cw.style.top = '744px'; cw.style.webkitFlexDirection = cw.style.flexDirection = 'column';
      cw.style.webkitAlignItems = cw.style.alignItems = 'flex-end'; }
    else { cw.style.right = '122px'; cw.style.top = '72px'; }
    for (var c = 0; c < CTRLS.length; c++) {
      var b = el('div', 'ctrl');
      var inTop = list && CTRLS[c].id === 'profile';
      if (!list && c) b.style.marginLeft = '8px';
      if (inTop) b.style.marginRight = '28px';
      b.style.webkitTransformOrigin = b.style.transformOrigin = list ? 'right center' : 'center';
      b.appendChild(makeMat(''));
      if (CTRLS[c].id === 'profile') {
        var cp = curProfile(), guest = !cp || cp.guest, nm2 = guest ? 'Misafir' : (cp.name || '—');
        var av = el('span', 'ctrl-av', nm2.charAt(0).toUpperCase());
        if (cp && cp.avatar) {
          var aim = el('img'); aim.alt = '';
          aim.onload = (function (a, im) { return function () { a.textContent = ''; a.appendChild(im); }; })(av, aim);
          aim.src = api.sized(cp.avatar, 80, 80);
        }
        b.className = 'ctrl ctrl-pf';
        b.appendChild(av);
        b.appendChild(el('span', 'ctrl-name', nm2));
      } else {
        if (CTRLS[c].id === 'search') { var si = el('span', 'ctrl-ic'); si.innerHTML = SEARCH_SVG; b.appendChild(si); }
        b.appendChild(el('span', '', CTRLS[c].label));
      }
      bindPointer(b, 'controls', c);
      if (inTop) { top.insertBefore(b, ck); v.pf = b; v.top = top; } else cw.appendChild(b);
      v.ctrls.push(b);
    }
    root.appendChild(cw);
    v.cw = cw;

    /* vitrin (hero): odaktaki satirin one cikan yapimi; "One Cikanlar"da otomatik doner */
    var pk = el('div', 'peek');
    var tx = el('div', 'peek-txt');
    var pTag = el('div', 'p-tag ell t-shadow'), pTitle = el('div', 'p-title ell t-shadow'),
      pMeta = el('div', 'p-meta ell t-shadow'), pOv = el('div', 'p-ov t-shadow');
    tx.appendChild(pTag); tx.appendChild(pTitle); tx.appendChild(pMeta); tx.appendChild(pOv);
    /* Oynat dugmesi: aciklamanin altinda, yazi blogunun parcasi (blok + dugme birlikte ortalanir); OK = detay + otomatik oynatma */
    var pBtn = el('div', 'pk-play'), pBl = el('span', 'pk-play-l');
    pBtn.appendChild(el('span', 'pk-play-hl'));
    var pIc = el('span', 'pk-play-ic'); pIc.innerHTML = rowIcon('play');
    pBtn.appendChild(pIc); pBtn.appendChild(pBl);
    pBtn.style.display = 'none';
    if (!preview) bindPointer(pBtn, 'hplay', 0);
    tx.appendChild(pBtn);
    if (list) {
      /* konum/boyut CSS'te (.l-list .peek*): yazi selamlama ile Devam Et arasinda dikey ortali (kart yok) */
    } else {
      pk.style.left = DOCK.left + 'px'; pk.style.top = HERO_D_TOP + 'px';
      tx.style.left = '680px'; tx.style.top = '20px'; tx.style.width = '824px';
      pTitle.style.fontSize = '56px'; pTitle.style.lineHeight = '68px';
      pOv.style.maxHeight = '96px'; pOv.style.marginTop = '10px';   /* 3 satir: dugme Devam Et basligina (612) degmesin */
    }
    /* okunurluk katmani: metnin arkasinda sabit, yumusak kenarli koyu ton (canli blur yok) */
    var scrim = el('div', 'peek-scrim');
    if (!list) { scrim.style.left = '660px'; scrim.style.top = '4px'; scrim.style.width = '864px'; scrim.style.height = '364px'; }
    pk.appendChild(scrim); pk.appendChild(tx); root.appendChild(pk);
    v.peek = { el: pk, tx: tx, tag: pTag, title: pTitle, meta: pMeta, ov: pOv, btn: pBtn, btnL: pBl, key: null, item: null };
    pk.style.display = n ? '' : 'none';
    return v;
  }

  function measure(v) {
    for (var i = 0; i < v.names.length; i++) v.widths[i] = Math.min(v.names[i].offsetWidth || 0, 660);
  }

  function contTile(it, k, C) {
    var t = el('div', 'cc'), img = el('img');
    t.style.left = (8 + k * (C.w + C.gap)) + 'px'; t.style.width = C.w + 'px'; t.style.height = C.h + 'px';
    img.alt = ''; img.src = cardSrc(it);
    img.onerror = function () { img.style.visibility = 'hidden'; };
    t.appendChild(img);
    var pc = it.progress && it.progress.pct;
    if (pc) {
      var bar = el('div', 'cc-bar'), f = el('div', 'cc-fill');
      f.style.width = clamp(pc, 3, 100) + '%';
      bar.appendChild(f); t.appendChild(bar);
    }
    bindPointer(t, 'cont', k);
    return t;
  }
  function updateCont(v, zone) {
    var c = v.cont;
    if (!c) return;
    var C = c.C, items = S.cont.items, n = items.length, i = S.cont.idx, step = C.w + C.gap, k;
    var vis = Math.max(1, Math.floor(C.width / step));
    var off = clamp(i - (vis - 1), 0, Math.max(0, n - vis)), more = n - off > vis;
    setT(c.inner, 'translateX(' + (-off * step) + 'px)');
    setMask(c.clip, 'linear-gradient(90deg, ' + (off > 0 ? 'rgba(0,0,0,.3) 0px, #000 40px' : '#000 0px') + ', #000 ' +
      (C.width - (more ? 90 : 0)) + 'px, ' + (more ? 'rgba(0,0,0,.2)' : '#000') + ' ' + C.width + 'px)');
    /* sanal pencere: gorunen +-1 kart */
    var from = Math.max(0, off - 1), to = Math.min(n, off + vis + 1);
    for (k in c.tiles) if (+k < from || +k >= to) { c.inner.removeChild(c.tiles[k]); delete c.tiles[k]; }
    for (k = from; k < to; k++) {
      if (!c.tiles[k]) { c.tiles[k] = contTile(items[k], k, C); c.inner.appendChild(c.tiles[k]); }
      c.tiles[k].className = 'cc' + (zone === 'cont' && k === i ? ' on' : '');
    }
    c.box.className = 'cs' + (C.big ? ' big' : '') + (zone === 'cont' ? ' act' : '');
  }

  function showSoonPeek(v, e, instant) {
    var p = v.peek, key = 'soon:' + e.id;
    p.el.style.display = '';
    if (p.key === key) return;
    p.key = key;
    if (posterMode() && !v.preview) posterFor(null);
    function swapText() {
      p.tag.textContent = 'Yakında';
      p.title.textContent = e.title;
      p.meta.textContent = '';
      p.ov.textContent = SOON_TEXT[e.id] || 'Bu bölüm hazırlanıyor.';
      heroBtn(p, null);
      p.tx.className = 'peek-txt';
    }
    if (instant) swapText();
    else { p.tx.className = 'peek-txt out'; g.setTimeout(function () { if (p.key === key) swapText(); }, 180); }
  }
  /* vitrin: Devam Et seridinde odaktaki kart, menude o bolumun one cikani */
  function peekItem(zone, idx) {
    if (zone === 'cont' && S.cont.items.length) return S.cont.items[S.cont.idx];
    var e = S.entries[idx];
    return e && e.status !== 'soon' ? entryLead(e) : null;
  }
  /* ms: afis gecis suresi (bolum degisimi kisa) */
  function peekFor(v, zone, idx, instant, ms) {
    if (zone === 'cont' && S.cont.items.length) return showPeek(v, S.cont.items[S.cont.idx], CONT_ENTRY, instant, ms);
    var e = S.entries[idx];
    if (e && e.status === 'soon') return showSoonPeek(v, e, instant);
    showPeek(v, entryLead(e), e, instant, ms);
  }

  /* vitrin Oynat dugmesi: ilerleme varsa "Devam et", dizide bolum kodu (S01 B03) */
  function heroPlayLabel(it) {
    if (isChannel(it)) return 'İzle' + (it.channel_no ? ' · ' + it.channel_no : '');
    var pr = it.progress, pa = it.primary_action, code = '';
    if (it.type === 'series') code = epCode((it.card_kind === 'episode' && it.episode_id) || (pa && pa.episode_id) || (pr && pr.episode_id) || '');
    var resume = !!(pr && pr.position > 0) || !!(pa && /^resume_/.test(pa.kind || ''));
    return (resume ? 'Devam et' : 'Oynat') + (code ? ' · ' + code : '');
  }
  function heroBtn(p, item) {
    p.item = item;
    if (!p.btn) return;
    p.btn.style.display = item ? '' : 'none';
    if (item) p.btnL.textContent = heroPlayLabel(item);
  }
  /* vitrinde oynatilabilir oge var mi (dugme gorunur) */
  function heroPlayable(v) {
    var p = v && v.peek;
    return !!(p && p.item && p.el.style.display !== 'none');
  }
  /* Oynat: o yapimin detayi acilir, oynatma kendiliginden baslar (detaydaki Izle ile ayni); detayda Geri = ana menu, odak dugmede */
  function heroPlay() {
    var p = HV && HV.peek, it = p && p.item;
    if (!it) return;
    var e = S.entries[S.idx];
    S.zone = 'play';
    /* vitrinden kanal: sira = "Tum kanallar" (live_all, oynatici tembel yukler), yalniz vitrin kanallari degil */
    if (isChannel(it)) return playChannel(it, { id: 'live_all', items: [], total: null });
    openDetail(it, e ? { title: e.title } : null, 'home', null, true);
  }

  var peekTimer = null;
  function showPeek(v, item, entry, instant, ms) {
    var p = v.peek;
    if (!item) { p.el.style.display = 'none'; heroBtn(p, null); if (posterMode() && !v.preview) posterFor(null); return; }
    p.el.style.display = '';
    var key = item.card_key || item.id;
    if (p.key === key) return;
    p.key = key;
    function swapText() {
      p.tag.textContent = tagLine(item, entry);
      p.title.textContent = item.title || '';
      p.meta.textContent = metaLine(item);
      p.ov.textContent = item.overview || (isChannel(item) ? 'Canlı yayın · OK ile izle' : '');
      heroBtn(p, item);
      p.tx.className = 'peek-txt';
    }
    if (posterMode()) {
      /* afis modu: yazi, arka plan gorseli yuklenince (crossfade ile birlikte) degisir */
      if (v.preview) { swapText(); return; }
      if (!instant) p.tx.className = 'peek-txt out';
      if (p.skipE !== entry) { p.skipE = entry; p.skips = 0; }
      posterFor(item, function (ok) {
        if (p.key !== key) return;
        var its = entry && entry !== CONT_ENTRY && entry.items;
        if (!ok && !isChannel(item) && its && its.length > 1 && ++p.skips < its.length) {   /* gorseli yok: bolumun siradaki ogesine gec */
          S.heroIdx = (S.heroIdx + 1) % its.length; p.key = null;
          return showPeek(v, entryLead(entry), entry, instant, ms);
        }
        if (ok) p.skips = 0; else posterShow('', '');
        swapText();
      }, ms);
      if (entry && entry !== CONT_ENTRY && entry.items && entry.items.length > 1)
        posterPrefetch(entry.items[(S.heroIdx + 1) % entry.items.length]);   /* siradaki ogeyi onceden yukle */
      return;
    }
    if (instant) swapText();
    else { p.tx.className = 'peek-txt out'; g.setTimeout(function () { if (p.key === key) swapText(); }, 180); }
  }

  function updateHome(v, zone, idx, ctrl, peekMode) {
    var L = S.look, glass = L.focus === 'glass', photo = !!S.bgKind, n = S.entries.length, i;
    if (v.list) {
      var off = clamp(idx - (VIS - 2), 0, Math.max(0, n - VIS));
      setT(v.inner, 'translateY(' + (-off * ROW_H) + 'px)');
      setMask(v.wrap, 'linear-gradient(180deg, ' + (off > 0 ? 'rgba(0,0,0,.28) 0px, #000 84px' : '#000 0px') + ', #000 ' +
        ((VIS - 1) * ROW_H - 8) + 'px, rgba(0,0,0,.22) ' + (VIS * ROW_H - 12) + 'px, rgba(0,0,0,0) ' + (VIS * ROW_H + 40) + 'px)');
      var k = zone === 'list' ? (glass ? 0.78 : 0.6) : 0.86;
      for (i = 0; i < n; i++) {
        var on = zone === 'list' && i === idx;
        v.names[i].style.opacity = on ? 1 : (photo ? k : k - 0.12);
        setT(v.names[i], on && !glass ? 'scale(1.05)' : 'scale(1)');
        v.names[i].style.textShadow = photo ? (on && glass ? '0 1px 2px rgba(14,10,6,.22)' : '') : 'none';
      }
      if (v.plate) {
        var x = PE - 36, y = LIST_TOP + (idx - off) * ROW_H + 4;
        setT(v.plate, tr(x, y));
        setT(v.plate.img, tr(-x, -y));
        v.plate.style.width = ((v.widths[idx] || 400) + 72) + 'px';
        v.plate.className = 'mat plate' + (zone === 'list' && n ? ' on' : '');
      }
    } else {
      var nt = v.nt, doff = clamp(idx - (DOCK.visible - 2), 0, Math.max(0, n - DOCK.visible)), more = n - doff > DOCK.visible;
      setT(v.dockIn, 'translateX(' + (-doff * nt) + 'px)');
      setMask(v.dockClip, 'linear-gradient(90deg, ' + (doff > 0 ? 'rgba(0,0,0,.3) 0px, #000 120px' : '#000 0px') + ', #000 ' +
        (DOCK.width - (more ? 200 : 0)) + 'px, ' + (more ? 'rgba(0,0,0,.25)' : '#000') + ' ' + DOCK.width + 'px)');
      setT(v.dockPlate, 'translateX(' + (idx * nt) + 'px)');
      v.dockPlate.className = 'dock-plate' + (zone === 'list' ? ' on' : '');
      for (i = 0; i < n; i++) v.tiles[i].className = 'dt' + (zone === 'list' && i === idx ? ' on' : '');
    }
    for (i = 0; i < v.ctrls.length; i++) v.ctrls[i].className = 'ctrl' + (CTRLS[i].id === 'profile' ? ' ctrl-pf' : '') + (zone === 'controls' && i === ctrl ? ' on' : '');
    if (v.peek && v.peek.btn) v.peek.btn.className = 'pk-play' + (zone === 'play' ? ' on' : '');
    updateCont(v, zone);
    if (peekMode === 'skip') return;
    peekFor(v, zone, idx, peekMode !== 'fade');
  }

  function renderHome() {
    screenEl.innerHTML = '';
    HV = buildHome(screenEl, false);
    measure(HV);
    updateHome(HV, S.zone, S.idx, S.ctrl);
    if (S.pop) popBuild(false);
    /* yazi tipi yuklenince olc (plaka genisligi) */
    var re = function () { if (HV && S.screen === 'home') { measure(HV); updateHome(HV, S.zone, S.idx, S.ctrl); } };
    g.setTimeout(re, 400);
    try { if (doc.fonts && doc.fonts.ready) doc.fonts.ready.then(re); } catch (e) {}
    g.setTimeout(updatePrompt, 800);
  }

  var peekDebounce = null;
  function homeFocus(zone, idx, ctrl) {
    if (idx !== S.idx) S.heroIdx = 0;
    S.zone = zone; S.idx = idx; S.ctrl = ctrl;
    if (!HV) return;
    var v = HV;
    updateHome(v, zone, idx, ctrl, 'skip');
    /* odak hemen; vitrin kisa gecikmeyle (hizli gezinmede gorsel yuklemesin). Afis modunda gorsel hazirsa 100 ms, degilse
       150 ms; bolum degisiminde gecis kisa (SECTION_FADE_MS), vitrin dongusu POSTER_FADE_MS */
    if (peekDebounce) g.clearTimeout(peekDebounce);
    var pm = posterMode(), wait = pm ? (posterIsReady(peekItem(zone, idx)) ? 100 : 150) : 200;
    peekDebounce = g.setTimeout(function () { if (HV === v) peekFor(v, S.zone, S.idx, false, pm ? SECTION_FADE_MS : 0); }, wait);
  }

  /* bolum vitrini otomatik doner (7 sn) */
  g.setInterval(function () {
    if (S.screen !== 'home' || !HV || S.zone !== 'list') return;
    var e = S.entries[S.idx];
    if (!e || !e.items || e.items.length < 2) return;
    S.heroIdx = (S.heroIdx + 1) % e.items.length;
    showPeek(HV, entryLead(e), e, false);
  }, 7000);

  /* =====================================================================
     IZGARA (satirin tumu; tasarim: All apps)
     ===================================================================== */
  var GV = null;
  function openGrid(entry) {
    S.grid = { entry: entry, idx: 0 };
    go('grid');
    if (!entry.items.length || entry.items.length < Math.min(20, entry.total || 20)) loadMore(true);
  }
  function loadMore(first) {
    var G = S.grid;
    if (!G) return;
    var e = G.entry;
    if (e.loading || e.id === 'featured') return;
    if (!first && e.total !== null && e.items.length >= e.total) return;
    e.loading = true;
    var offset = first ? 0 : e.items.length;
    api.row(e.id, S.pid, offset, 40, function (err, data) {
      e.loading = false;
      if (err) { e.err = err.message; if (S.screen === 'grid' && S.grid && S.grid.entry === e) renderGridWindow(); return; }
      var items = data.items || [];
      e.items = first ? items : e.items.concat(items);
      e.total = data.total !== undefined ? data.total : e.items.length;
      if (S.screen === 'grid' && S.grid && S.grid.entry === e) { GV.count.textContent = e.total; renderGridWindow(); }
    });
  }
  function renderGrid() {
    var G = S.grid, e = G.entry;
    screenEl.innerHTML = '';
    var back = el('div', 'back'); back.innerHTML = chev(true); back.appendChild(doc.createTextNode('Ana sayfa'));
    back.onclick = function () { goHome(); };
    var ht = el('div', 'h-title nowrap', e.title);
    var cnt = el('span', 'h-count', e.total !== null && e.total !== undefined ? e.total : '');
    ht.appendChild(cnt);
    var wrap = el('div', 'grid-wrap'), inner = el('div', 'grid-in');
    var empty = el('div', 'grid-empty', '');
    wrap.appendChild(inner);
    screenEl.appendChild(back); screenEl.appendChild(ht); screenEl.appendChild(wrap); screenEl.appendChild(empty);
    GV = { wrap: wrap, inner: inner, count: cnt, empty: empty, tiles: {} };
    renderGridWindow();
  }
  function gridTile(it, i) {
    var t = cardTile(it);
    t.style.left = (GRID.left + (i % GRID.cols) * (GRID.w + GRID.gap)) + 'px';
    t.style.top = (Math.floor(i / GRID.cols) * GRID_STRIDE) + 'px';
    bindPointer(t, 'grid', i);
    return t;
  }
  function channelTile(it) {
    var t = el('div', 'gt'), inn = el('div', 'gt-in'), im = el('div', 'gt-img ch-art'), h = chHue(it);
    im.style.background = 'linear-gradient(150deg, hsla(' + h + ',42%,46%,.58), hsla(' + ((h + 28) % 360) + ',38%,22%,.62))';
    im.appendChild(el('div', 'ch-ini', chInitials(it)));
    if (it.channel_no) im.appendChild(el('div', 'ch-no', String(it.channel_no)));
    im.appendChild(el('div', 'ch-live', 'CANLI'));
    inn.appendChild(im);
    inn.appendChild(el('div', 'gt-name ell', it.title));
    var cc = chCats(it);
    inn.appendChild(el('div', 'gt-sub ell', (it.channel_no ? 'Kanal ' + it.channel_no : 'Canlı') + (cc.length ? ' · ' + cc.join(', ') : '')));
    t.appendChild(inn);
    return t;
  }
  function cardTile(it) {
    if (isChannel(it)) return channelTile(it);
    var t = el('div', 'gt'), inn = el('div', 'gt-in'), im = el('div', 'gt-img'), img = el('img');
    img.alt = ''; img.src = cardSrc(it);
    img.onerror = function () { img.style.visibility = 'hidden'; };
    im.appendChild(img); inn.appendChild(im);
    inn.appendChild(el('div', 'gt-name ell', it.title));
    inn.appendChild(el('div', 'gt-sub ell', it.episode_label || metaLine(it)));
    t.appendChild(inn);
    return t;
  }
  function renderGridWindow() {
    if (!GV) return;
    var G = S.grid, e = G.entry, n = e.items.length, row = Math.floor(G.idx / GRID.cols);
    var lastRow = Math.max(0, Math.floor((n - 1) / GRID.cols));
    var off = clamp(row - 1, 0, Math.max(0, lastRow - 2));
    setT(GV.inner, 'translateY(' + (-off * GRID_STRIDE) + 'px)');
    setMask(GV.wrap, 'linear-gradient(180deg, ' + (off > 0 ? 'rgba(0,0,0,0) 0px, #000 40px' : '#000 0px') + ', #000 700px, rgba(0,0,0,0) 840px)');
    /* sanal pencere: yalniz gorunen +-1 satir DOM'da */
    var from = Math.max(0, (off - 1) * GRID.cols), to = Math.min(n, (off + 5) * GRID.cols), k;
    for (k in GV.tiles) {
      if (+k < from || +k >= to) { GV.inner.removeChild(GV.tiles[k]); delete GV.tiles[k]; }
    }
    for (var i = from; i < to; i++) {
      if (!GV.tiles[i]) { GV.tiles[i] = gridTile(e.items[i], i); GV.inner.appendChild(GV.tiles[i]); }
      GV.tiles[i].className = 'gt' + (i === G.idx ? ' on' : '');
    }
    GV.empty.textContent = n ? '' : (e.err ? e.err : (e.loading || e.total === null ? 'Yükleniyor…' : 'Bu listede yapım yok.'));
    if (G.idx >= n - 10) loadMore(false);
  }
  function gridKey(k) {
    var G = S.grid, n = G.entry.items.length, c = GRID.cols, i = G.idx;
    if (k === 'left' && i % c) G.idx = i - 1;
    else if (k === 'right' && i % c < c - 1 && i + 1 < n) G.idx = i + 1;
    else if (k === 'up' && i - c >= 0) G.idx = i - c;
    else if (k === 'down' && Math.floor(i / c) < Math.floor((n - 1) / c)) G.idx = Math.min(n - 1, i + c);
    else if (k === 'ok' && n) return isChannel(G.entry.items[i]) ? playChannel(G.entry.items[i], G.entry) : openDetail(G.entry.items[i], G.entry, 'grid');
    else if (k === 'back') return goHome();
    renderGridWindow();
  }

  /* =====================================================================
     BOLUM SAYFASI (menu ogesi -> kendi satirlari; satir icinde yatay gezinme)
     ===================================================================== */
  var SECV = null;
  function openSection(entry) {
    if (entry.status === 'soon') { S.soon = entry; return go('soon'); }
    var P = S.sec = { entry: entry, rows: null, row: 0, err: null, loading: true };
    go('section');
    api.section(entry.id, S.pid, function (err, data) {
      if (S.sec !== P) return;
      P.loading = false;
      if (err) P.err = err.message;
      else {
        P.rows = [];
        var rs = data.rows || [];
        for (var i = 0; i < rs.length; i++) {
          if (!rs[i].items || !rs[i].items.length) continue;
          P.rows.push({ id: rs[i].id, title: rs[i].title, items: rs[i].items, total: rs[i].total || rs[i].items.length, col: 0 });
        }
      }
      if (S.screen === 'section') renderSectionWindow();
    });
  }
  function renderSection() {
    var P = S.sec;
    screenEl.innerHTML = '';
    var back = el('div', 'back'); back.innerHTML = chev(true); back.appendChild(doc.createTextNode('Ana sayfa'));
    back.onclick = function () { goHome(); };
    var info = el('div', 'sec-info ell', '');
    var wrap = el('div', 'sec-wrap'), inner = el('div', 'sec-in');
    var empty = el('div', 'grid-empty', '');
    wrap.appendChild(inner);
    screenEl.appendChild(back); screenEl.appendChild(el('div', 'h-title nowrap', P.entry.title));
    screenEl.appendChild(info); screenEl.appendChild(wrap); screenEl.appendChild(empty);
    SECV = { wrap: wrap, inner: inner, info: info, empty: empty, rows: {} };
    renderSectionWindow();
  }
  function renderSectionWindow() {
    var V = SECV, P = S.sec;
    if (!V || !P) return;
    var rs = P.rows || [], n = rs.length, k;
    V.empty.textContent = P.err ? P.err + ' · OK: yeniden dene' : (P.loading ? 'Yükleniyor…' : (n ? '' : 'Bu bölümde henüz yapım yok.'));
    setT(V.inner, 'translateY(' + (-P.row * SEC.stride) + 'px)');
    setMask(V.wrap, 'linear-gradient(180deg, ' + (P.row > 0 ? 'rgba(0,0,0,0) 0px, #000 36px' : '#000 0px') +
      ', #000 680px, rgba(0,0,0,0) 812px)');
    /* sanal pencere: odak satiri +-1 (ve alttaki bir sonraki) DOM'da */
    var from = Math.max(0, P.row - 1), to = Math.min(n, P.row + 3);
    for (k in V.rows) if (+k < from || +k >= to) { V.inner.removeChild(V.rows[k].el); delete V.rows[k]; }
    for (var ri = from; ri < to; ri++) {
      if (!V.rows[ri]) {
        var e = el('div', 'srw'), st = el('div', 'srw-strip');
        e.style.top = (ri * SEC.stride) + 'px';
        e.appendChild(el('div', 'srw-title nowrap t-shadow', rs[ri].title));
        e.appendChild(st);
        V.rows[ri] = { el: e, strip: st, tiles: {} };
        V.inner.appendChild(e);
      }
      secStrip(V.rows[ri], rs[ri], ri);
    }
    var r = rs[P.row], it = r && r.items[r.col];
    V.info.textContent = it ? (it.title + (it.episode_label ? ' · ' + it.episode_label : '') + ' · ' + metaLine(it)) : '';
  }
  function secStrip(R, r, ri) {
    var P = S.sec, n = r.items.length, on = ri === P.row, step = SEC.cw + SEC.gap, k;
    var off = clamp(r.col - 2, 0, Math.max(0, n - SEC.vis));
    setT(R.strip, 'translateX(' + (-off * step) + 'px)');
    var from = Math.max(0, off - 1), to = Math.min(n, off + SEC.vis + 2);
    for (k in R.tiles) if (+k < from || +k >= to) { R.strip.removeChild(R.tiles[k]); delete R.tiles[k]; }
    for (var i = from; i < to; i++) {
      if (!R.tiles[i]) {
        var t = cardTile(r.items[i]);
        t.style.left = (PE + i * step) + 'px';
        bindPointer(t, 'scard', ri * 10000 + i);
        R.tiles[i] = t; R.strip.appendChild(t);
      }
      R.tiles[i].className = 'gt' + (on && i === r.col ? ' on' : '');
    }
    R.el.className = 'srw' + (on ? ' on' : '');
    if (on && r.col >= n - 6) secMore(r);
  }
  function secMore(r) {
    if (r.loading || r.items.length >= r.total) return;
    var P = S.sec;
    r.loading = true;
    api.row(r.id, S.pid, r.items.length, 40, function (err, data) {
      r.loading = false;
      if (err) return;
      r.items = r.items.concat(data.items || []);
      r.total = data.total !== undefined ? data.total : r.items.length;
      if (S.screen === 'section' && S.sec === P) renderSectionWindow();
    });
  }
  function sectionKey(k) {
    var P = S.sec, rs = P.rows || [], r = rs[P.row];
    if (k === 'back') return goHome();
    if (!r) { if (k === 'ok' && P.err) openSection(P.entry); return; }
    if (k === 'up' && P.row > 0) P.row--;
    else if (k === 'down' && P.row < rs.length - 1) P.row++;
    else if (k === 'left' && r.col > 0) r.col--;
    else if (k === 'right' && r.col < r.items.length - 1) r.col++;
    else if (k === 'ok' && r.items[r.col]) {
      if (isChannel(r.items[r.col])) return playChannel(r.items[r.col], r);   /* kanal: detay yok, dogrudan oynatici */
      return openDetail(r.items[r.col], { title: P.entry.title }, 'section');
    }
    else return;
    renderSectionWindow();
  }

  /* "Yakinda" bolumu (DVR: henuz kaynak yok) */
  function renderSoon() {
    var e = S.soon;
    screenEl.innerHTML = '';
    var back = el('div', 'back'); back.innerHTML = chev(true); back.appendChild(doc.createTextNode('Ana sayfa'));
    back.onclick = function () { goHome(); };
    screenEl.appendChild(back);
    screenEl.appendChild(el('div', 'h-title nowrap', e.title));
    var box = el('div', 'soon');
    box.appendChild(el('div', 'soon-t', 'Yakında'));
    box.appendChild(el('div', 'soon-s', SOON_TEXT[e.id] || 'Bu bölüm hazırlanıyor.'));
    screenEl.appendChild(box);
    screenEl.appendChild(hintBar([['Geri', ' Ana sayfa']]));
  }

  /* =====================================================================
     ARAMA (Ara hapi / Kirmizi tus). Ustte cam arama kutusu (OK = ekran klavyesi; Enter/Done = ara; tarayicida yazdikca,
     ~300 ms debounce), altinda iki satir: "Kutuphanede" (/api/catalog?q=, >= 2 karakter) ve "Sitelerde" (canli /api/search,
     >= 3 karakter; kutuphanede olanlar tekrar gosterilmez). Kurallar + istek iptali js/search.js. Kart OK -> detay (canli
     sonucun ilk acilisinda sunucu sezon/bolum katalogunu o anda getirir, API.md); detaydan Geri -> arama (sorgu + odak korunur).
     Geri: sonuclardan -> kutu -> ana ekran (odak Ara hapinda).
     ===================================================================== */
  var SRV = null;
  var SR = { top: 300, stride: 330, vis: 5 };
  var SEARCH_FROM = { home: 1, grid: 1, section: 1, soon: 1, detail: 1, settings: 1, search: 1 };
  function openSearch() {
    if (S.screen === 'search' && S.srch) {            /* zaten aramada (Kirmizi): kutuya don */
      S.srch.zone = 'input'; searchPaint();
      if (SRV) { try { SRV.inp.focus(); } catch (e) {} }
      return;
    }
    if (S.pop) closePop();
    if (S.srch) S.srch.ctl.dispose();
    var X = S.srch = { text: '', zone: 'input', row: 0, cols: [0, 0], autofocus: !g.tizen, v: null };
    X.ctl = DZP.createSearch({ api: api, pid: function () { return S.pid; }, onChange: function (v) {
      if (S.srch !== X) return;
      X.v = v;
      if (S.screen === 'search') searchPaint();
    } });
    X.v = X.ctl.view();
    go('search');
  }
  function searchExit() {
    if (S.srch) S.srch.ctl.dispose();
    S.srch = null;
    S.zone = 'controls'; S.ctrl = ctrlIdx('search');
    goHome();
  }
  function searchRows() {
    var X = S.srch, v = X.v, lib, sites;
    lib = { title: 'Kütüphanede', items: v.idle ? [] : v.local,
      msg: v.idle ? '' : (v.localLoading ? 'Aranıyor…' : (v.localError ? v.localError : (v.local.length ? '' : 'Kütüphanede sonuç yok.'))) };
    sites = { title: 'Sitelerde', items: v.idle ? [] : v.sites,
      msg: v.idle ? '' : (!v.remoteOn ? 'Sitelerde aramak için en az 3 karakter yazın.' : (v.remoteLoading ? 'Sitelerde aranıyor…' :
        (v.sites.length ? '' : (v.remoteTotal ? 'Bulunanların hepsi kütüphanede.' : 'Sitelerde sonuç yok.')))) };
    return [lib, sites];
  }
  function renderSearch() {
    var X = S.srch;
    screenEl.innerHTML = '';
    var back = el('div', 'back'); back.innerHTML = chev(true); back.appendChild(doc.createTextNode('Ana sayfa'));
    back.onclick = function () { searchExit(); };
    screenEl.appendChild(back);
    var fx = PE, fy = 120;
    var fld = el('div', 'fld sfld');
    fld.style.left = fx + 'px'; fld.style.top = fy + 'px';
    fld.appendChild(makeMat('fld-mat', fx, fy));
    var ic = el('div', 'sfld-ic'); ic.innerHTML = SEARCH_SVG; fld.appendChild(ic);
    var inp = el('input', 'fld-in sfld-in');
    inp.type = 'text'; inp.value = X.text; inp.placeholder = 'Film ya da dizi ara';
    inp.setAttribute('aria-label', 'Ara');
    inp.setAttribute('autocomplete', 'off'); inp.setAttribute('spellcheck', 'false');
    inp.oninput = function () { X.text = inp.value; X.ctl.input(inp.value); };
    inp.onfocus = function () { X.zone = 'input'; searchPaint(); };
    inp.dzSubmit = function () {
      inp.blur();
      X.text = inp.value; X.ctl.submit(inp.value);
      var rs = searchRows(), r = firstRow(rs, 0, 1);
      if (r >= 0) { X.zone = 'rows'; X.row = r; }
      searchPaint();
    };
    fld.appendChild(inp);
    fld.appendChild(el('div', 'fld-ring'));
    bindPointer(fld, 'sfld', 0);
    screenEl.appendChild(fld);
    var busy = el('div', 'sbusy t-shadow');
    busy.appendChild(el('span', 'sbusy-dot'));
    var busyT = el('span', '', '');
    busy.appendChild(busyT);
    screenEl.appendChild(busy);
    var info = el('div', 'sec-info ell', ''); info.style.top = '236px';
    screenEl.appendChild(info);
    var wrap = el('div', 'sch-wrap'), rows = [];
    for (var i = 0; i < 2; i++) {
      var e = el('div', 'srw'), st = el('div', 'srw-strip'), tt = el('div', 'srw-title nowrap t-shadow', '');
      e.style.top = (i * SR.stride) + 'px';
      e.appendChild(tt); e.appendChild(st);
      var msg = el('div', 'sch-msg t-shadow', '');
      e.appendChild(msg);
      wrap.appendChild(e);
      rows.push({ el: e, strip: st, title: tt, msg: msg, tiles: {}, items: null });
    }
    screenEl.appendChild(wrap);
    var note = el('div', 'sch-note t-shadow', '');
    screenEl.appendChild(note);
    screenEl.appendChild(hintBar([['OK', ' Yaz / Aç'], ['Kırmızı', ' Ara'], ['Geri', ' Ana sayfa']]));
    SRV = { fld: fld, inp: inp, busy: busy, busyT: busyT, info: info, rows: rows, note: note };
    searchPaint();
    if (X.autofocus && X.zone === 'input') { X.autofocus = false; try { inp.focus(); } catch (e2) {} }   /* tarayici: dogrudan yaz */
  }
  /* dir yonunde (from dahil) sonucu olan ilk satir, yoksa -1 */
  function firstRow(rs, from, dir) {
    for (var i = from; i >= 0 && i < rs.length; i += dir) if (rs[i].items.length) return i;
    return -1;
  }
  function searchPaint() {
    var X = S.srch, V = SRV;
    if (!V || !X) return;
    var v = X.v, rs = searchRows(), i;
    if (X.zone === 'rows' && !rs[X.row].items.length) {   /* odaktaki satir bosaldi: digerine, o da yoksa kutuya */
      var alt = firstRow(rs, 0, 1);
      if (alt >= 0) X.row = alt; else X.zone = 'input';
    }
    V.fld.className = 'fld sfld' + (X.zone === 'input' ? ' on' : '');
    var busy = !v.idle && (v.localLoading || v.remoteLoading);
    V.busy.className = 'sbusy t-shadow' + (busy ? ' on' : '');
    V.busyT.textContent = busy ? (v.remoteLoading ? 'Sitelerde aranıyor…' : 'Aranıyor…') : '';
    for (i = 0; i < rs.length; i++) searchStrip(V.rows[i], rs[i], i);
    var r = rs[X.row], it = X.zone === 'rows' && r ? r.items[X.cols[X.row]] : null;
    V.info.textContent = it ? (it.title + (it.episode_label ? ' · ' + it.episode_label : '') + ' · ' + metaLine(it)) :
      (v.idle ? 'Kütüphanede 2, sitelerde 3 karakterden itibaren aranır.' : '');
    V.note.textContent = v.empty ? 'Sonuç bulunamadı.' + (v.note ? ' ' + v.note : '') : (v.note || '');
  }
  function searchStrip(R, r, ri) {
    var X = S.srch, items = r.items, n = items.length, step = SEC.cw + SEC.gap, k;
    if (R.items !== items) {   /* yeni sonuc listesi: kartlar yeniden */
      for (k in R.tiles) R.strip.removeChild(R.tiles[k]);
      R.tiles = {}; R.items = items;
      X.cols[ri] = clamp(X.cols[ri], 0, Math.max(0, n - 1));
    }
    var on = X.zone === 'rows' && X.row === ri, col = X.cols[ri];
    R.title.textContent = r.title + (n ? '  ·  ' + n : '');
    R.msg.textContent = r.msg;
    var off = clamp(col - 2, 0, Math.max(0, n - SR.vis));
    setT(R.strip, 'translateX(' + (-off * step) + 'px)');
    /* sanal pencere: gorunen +-1 kart DOM'da (gorseller yalniz bu kartlar icin yuklenir) */
    var from = Math.max(0, off - 1), to = Math.min(n, off + SR.vis + 1);
    for (k in R.tiles) if (+k < from || +k >= to) { R.strip.removeChild(R.tiles[k]); delete R.tiles[k]; }
    for (var i = from; i < to; i++) {
      if (!R.tiles[i]) {
        var t = cardTile(items[i]);
        t.style.left = (PE + i * step) + 'px';
        bindPointer(t, 'scard2', ri * 10000 + i);
        R.tiles[i] = t; R.strip.appendChild(t);
      }
      R.tiles[i].className = 'gt' + (on && i === col ? ' on' : '');
    }
    R.el.className = 'srw' + (on || X.zone === 'input' ? ' on' : '');
  }
  function searchKey(k) {
    var X = S.srch, rs = searchRows();
    if (X.zone === 'input') {
      if (k === 'down') { var d = firstRow(rs, 0, 1); if (d >= 0) { X.zone = 'rows'; X.row = d; } }
      else if (k === 'ok') { if (SRV) { try { SRV.inp.focus(); } catch (e) {} } return; }   /* TV: ekran klavyesi */
      else if (k === 'back') return searchExit();
      else return;
      return searchPaint();
    }
    var r = rs[X.row], n = r.items.length, c = X.cols[X.row];
    if (k === 'up') { var u = firstRow(rs, X.row - 1, -1); if (u >= 0) X.row = u; else X.zone = 'input'; }
    else if (k === 'down') { var dn = firstRow(rs, X.row + 1, 1); if (dn >= 0) X.row = dn; }
    else if (k === 'left' && c > 0) X.cols[X.row] = c - 1;
    else if (k === 'right' && c < n - 1) X.cols[X.row] = c + 1;
    else if (k === 'ok' && r.items[c]) return openDetail(r.items[c], { title: 'Arama' }, 'search');
    else if (k === 'back') X.zone = 'input';
    else return;
    searchPaint();
  }

  /* =====================================================================
     DETAY (tasarim: Appearance iki bolmeli ekran)
     ===================================================================== */
  /* autoplay: veri gelince Izle satiri kendiliginden oynatilir (ana menu vitrini Oynat); oynaticidan donuste detay, odak Izle */
  function openDetail(item, entry, from, focusId, autoplay) {
    S.detail = { item: item, entry: entry, data: null, row: 0, season: 0, err: null, from: from || 'grid' };
    go('detail');
    var D = S.detail, dbg = g.DZDebug && g.DZDebug.enabled() ? g.DZDebug : null, reqT = new Date().getTime();
    if (dbg) { dbg.reset('detail'); dbg.log('detail', 'İstek: /api/detail/' + item.id); }
    api.detail(item.id, S.pid, function (err, data) {
      if (S.detail !== D) return;
      if (dbg) {
        var ms = new Date().getTime() - reqT;
        if (err) dbg.log('detail', 'Detay hatası: ' + err.message + ' · ' + ms + ' ms', 'bad');
        if (err) dbg.sum('detail', '✗ Detay hatası: ' + err.message + ' · ' + ms + ' ms', 'bad');
        else dbg.sum('detail', 'Detay ' + ms + ' ms' + (data.hydrating ? ' · yükleniyor' : ''));
        if (!err) dbg.log('detail', 'Detay yanıtı · ' + ms + ' ms · hydrating: ' + (data.hydrating ? 'evet (arka planda dolduruluyor)' : 'hayır') +
          (data.seasons ? ' · ' + data.seasons.length + ' sezon' : '') + ' · ' + ((data.source_names || []).length) + ' kaynak sitesi', data.hydrating ? 'skip' : 'ok');
      }
      if (err) D.err = err.message; else D.data = data;
      if (data && data.seasons) {
        /* ozel bolumler (sezon 0) basta gelir -> sona al */
        var se = data.seasons.slice(0), sp = [];
        while (se.length && se[0].season === 0) sp.push(se.shift());
        data.seasons = se.concat(sp);
        if (item.season) for (var i = 0; i < data.seasons.length; i++) if (data.seasons[i].season === item.season) D.season = i;
      }
      if (focusId) { /* oynaticidan donus: odak girilen dugmeye (yoksa ust) */
        var fr = detailRows();
        for (var j = 0; j < fr.length; j++) if (fr[j].id === focusId) { D.row = j; break; }
      }
      if (autoplay && S.screen === 'detail' && !err) {
        var ar = detailRows();
        if (ar[0] && ar[0].id === 'play' && !ar[0].dis) { D.row = 0; D.rows = ar; return playFromDetail(D, ar[0]); }
        if (ar[0] && ar[0].dis) showVeil(item.title, ar[0].value);
      }
      if (S.screen === 'detail') renderDetail();
    });
  }
  function epCode(id) {
    var m = /:s(\d+):e(\d+)/.exec(id || '');
    return m ? 'S' + pad2(+m[1]) + ' B' + pad2(+m[2]) : '';
  }
  function detailRows() {
    var D = S.detail, d = D.data, it = D.item, rows = [];
    if (!d) { rows.push({ id: 'play', icon: 'play', label: 'İzle', value: D.err ? D.err : 'Yükleniyor…', dis: true }); return rows; }
    var acts = d.actions || [], play = null, trailer = null, i;
    for (i = 0; i < acts.length; i++) {
      if (!play && /^(play|resume)_(movie|episode)$/.test(acts[i].kind)) play = acts[i];
      if (!trailer && acts[i].kind === 'play_trailer') trailer = acts[i];
    }
    if (play) {
      var names = { play_movie: 'Filmi oynat', resume_movie: 'Kaldığın yerden devam', play_episode: 'Oynat', resume_episode: 'Devam et' };
      var code = it.card_kind === 'episode' ? (it.episode_label || epCode(it.episode_id)) : epCode(play.episode_id);
      rows.push({ id: 'play', icon: 'play', label: 'İzle', value: names[play.kind] + (code ? ' · ' + code : ''), act: play });
      /* ilerleme varsa: ayni hedefi 0'dan oynat (sunucunun resume_position'i yok sayilir) */
      if (/^resume_/.test(play.kind)) rows.push({ id: 'restart', icon: 'restart', label: 'Baştan', value: (code ? code + ' · ' : '') + 'Baştan başla', act: play });
    } else rows.push({ id: 'play', icon: 'play', label: 'İzle', value: 'Henüz izleme kaynağı yok', dis: true });
    if (trailer) rows.push({ id: 'trailer', icon: 'trailer', label: 'Fragman', value: 'Fragmanı oynat', act: trailer });
    if (d.seasons && d.seasons.length) {
      var s = d.seasons[D.season] || d.seasons[0];
      rows.push({ id: 'season', icon: 'season', label: 'Sezonlar · ' + (D.season + 1) + '/' + d.seasons.length,
        value: (s.name || s.title || ('Sezon ' + s.season)) + ' · ' + (s.episode_count || (s.episodes || []).length) + ' bölüm', arrows: true });
    }
    rows.push({ id: 'mylist', icon: d.in_mylist ? 'check' : 'add', label: 'Listem', value: d.in_mylist ? 'Listede · çıkarmak için OK' : 'Listeye ekle' });
    return rows;
  }
  function settingRow(r, y, focused, top, step) {
    var e = el('div', 'sr' + (focused ? ' on' : '') + (r.dis ? ' dis' : ''));
    e.style.top = (top + y * (step || 100)) + 'px';
    if (r.icon) { var ic = el('div', 'sr-ic'); ic.innerHTML = rowIcon(r.icon); e.appendChild(ic); }
    var body = el('div', 'sr-body');
    body.appendChild(el('div', 'sr-label', r.label));
    body.appendChild(el('div', 'sr-value', r.value));
    e.appendChild(body);
    if (r.arrows) {
      var a = el('div', 'sr-arr'), b = el('div', 'sr-arr');
      a.innerHTML = chev(true); b.innerHTML = chev(false);
      a.onclick = function (ev) { ev.stopPropagation(); onKey('left'); };
      b.onclick = function (ev) { ev.stopPropagation(); onKey('right'); };
      e.appendChild(a); e.appendChild(b);
    }
    return e;
  }
  function hintBar(parts) {
    var h = el('div', 'hint');
    for (var i = 0; i < parts.length; i++) {
      if (i) h.appendChild(el('span', 'gap'));
      h.appendChild(el('span', 'key', parts[i][0]));
      h.appendChild(doc.createTextNode(parts[i][1]));
    }
    return h;
  }
  function renderDetail() {
    var D = S.detail, it = D.item, d = D.data || it;
    screenEl.innerHTML = '';
    var back = el('div', 'back'); back.innerHTML = chev(true); back.appendChild(doc.createTextNode(D.entry ? D.entry.title : 'Geri'));
    back.onclick = function () { onKey('back'); };
    screenEl.appendChild(back);
    screenEl.appendChild(el('div', 'h-title ell', it.title));
    screenEl.appendChild(el('div', 'd-meta ell', metaLine(d)));
    screenEl.appendChild(el('div', 'd-ov', d.overview || (D.data ? 'Özet yok.' : '')));
    var rows = detailRows();
    D.rows = rows;
    D.row = clamp(D.row, 0, rows.length - 1);
    for (var i = 0; i < rows.length; i++) {
      var r = settingRow(rows[i], i, i === D.row, rows.length > 4 ? 486 : 500, rows.length > 4 ? 94 : 100);  /* 5 satir ipucu cubuguna tasmasin */
      bindPointer(r, 'drow', i);
      screenEl.appendChild(r);
    }
    var pv = el('div', 'pv'), img = el('img', 'pv-img');
    img.alt = ''; img.src = wideSrc(it, 912, 513);
    pv.appendChild(img); screenEl.appendChild(pv);
    var note = [];
    if (it.card_kind === 'episode' && it.episode_label) note.push(it.episode_label);
    if (D.data && D.data.source_names && D.data.source_names.length) {
      var sn = [];
      for (var j = 0; j < D.data.source_names.length && j < 4; j++) sn.push(D.data.source_names[j].name);
      note.push('Kaynak: ' + sn.join(', '));
    }
    if (D.data && D.data.cast && D.data.cast.length) note.push(D.data.cast.slice(0, 3).join(', '));
    var pn = el('div', 'pv-note', note.join(' · '));
    pn.style.top = (240 + 513 + 26) + 'px';
    screenEl.appendChild(pn);
    screenEl.appendChild(hintBar([['OK', ' Seç / Oynat'], ['Geri', ' ' + (D.entry ? D.entry.title : 'Geri')]]));
    if (g.DZDebug && g.DZDebug.enabled()) g.DZDebug.mount(screenEl, 'detail', { rows: g.DZDebug.detailed() ? 3 : 1, cls: 'dbg-detail' });
  }
  function detailKey(k) {
    var D = S.detail, rows = D.rows || [], r = rows[D.row];
    if (k === 'up') D.row = Math.max(0, D.row - 1);
    else if (k === 'down') D.row = Math.min(rows.length - 1, D.row + 1);
    else if ((k === 'left' || k === 'right') && r && r.id === 'season') {
      var n = D.data.seasons.length;
      D.season = (D.season + (k === 'left' ? -1 : 1) + n) % n;
    } else if (k === 'back') return D.from === 'home' ? homeRefresh() : go(D.from);
    else if (k === 'ok' && r) {
      if (r.id === 'play' || r.id === 'restart' || r.id === 'trailer') {
        if (r.dis) return showVeil(D.item.title, r.value);
        return playFromDetail(D, r);
      }
      if (r.id === 'mylist' && D.data) {
        var on = !D.data.in_mylist;
        var cb = function (err) { if (!err) { D.data.in_mylist = on; if (S.screen === 'detail' && S.detail === D) renderDetail(); } };
        if (on) api.mylistAdd(S.pid, D.item.id, cb); else api.mylistDel(S.pid, D.item.id, cb);
        return;
      }
    } else return;
    renderDetail();
  }

  /* =====================================================================
     AYARLAR ( tasarim: Appearance + canli onizleme)
     ===================================================================== */
  function settingDefs() {
    var n = DZP.BG_LIST.length;
    var defs = [
      { key: 'bg', label: 'Arka plan', values: [{ v: 'posters', name: 'Afişler (bölüme göre)' },
        { v: 'mine', name: 'Görsellerim' + (n ? ' (' + n + ')' : ' (yok)') }, { v: 'dark', name: 'Siyah' }] },
      { key: 'layout', label: 'Yerleşim', values: [{ v: 'list', name: 'Liste' }, { v: 'dock', name: 'Dock' }] },
      { key: 'focus', label: 'Odak', values: [{ v: 'glass', name: 'Cam' }, { v: 'type', name: 'Yazı' }] },
      { key: 'material', label: 'Malzeme', values: [{ v: 'soft', name: 'Yumuşak' }, { v: 'frost', name: 'Buzlu' }, { v: 'tint', name: 'Saydam ton' }] }
    ];
    /* tarayicida (TV degil) dogrudan yukleme: dosya secici -> POST /api/backgrounds */
    if (!g.tizen) defs.push({ key: 'upload', label: 'Görsel yükle · OK: dosya seç', action: true, text: S.up || (n + ' görsel') });
    defs.push({ key: 'profile', label: 'Profil · OK: profil değiştir', action: true, text: S.pname || '—' });
    defs.push({ key: 'server', label: 'Sunucu · OK: adresi değiştir', action: true, text: api.base() || '—' });
    /* Hata ayiklama (js/debug.js): oynaticida cozumleme/oynatma izi paneli; tercih `dzproto.debug` (S.look'ta degil) */
    /* Oynatici motoru (yalniz Tizen): Otomatik = HTML5 <video> (sahada dogrulanmis), AVPlay yalniz secilirse; tercih `dzproto.engine` */
    if (g.tizen) defs.push({ key: 'engine', label: 'Oynatıcı', values: [{ v: 'auto', name: 'Otomatik' }, { v: 'avplay', name: 'AVPlay' }] });
    defs.push({ key: 'debug', label: 'Hata ayıklama', values: [{ v: 'off', name: 'Kapalı' }, { v: 'summary', name: 'Özet' }, { v: 'detail', name: 'Ayrıntılı' }] });
    /* surum denetimi (js/update.js): Tizen + Android kabugu; tarayicida yok */
    if (DZP.update && DZP.update.kind() !== 'browser') defs.push({ key: 'update', label: 'Güncellemeleri denetle · OK', action: true, text: DZP.update.statusText() });
    return defs;
  }
  function settingValue(def) {
    if (def.action) return 0;
    var cur = def.key === 'debug' ? (g.DZDebug ? g.DZDebug.mode() : 'off') :
      def.key === 'engine' ? (DZP.player && DZP.player.enginePref ? DZP.player.enginePref() : 'auto') : S.look[def.key];
    for (var i = 0; i < def.values.length; i++) if (def.values[i].v === cur) return i;
    return 0;
  }
  function renderSettings() {
    var defs = settingDefs();
    S.setRow = clamp(S.setRow, 0, defs.length - 1);
    screenEl.innerHTML = '';
    var back = el('div', 'back'); back.innerHTML = chev(true); back.appendChild(doc.createTextNode('Ana sayfa'));
    back.onclick = function () { onKey('back'); };
    screenEl.appendChild(back);
    screenEl.appendChild(el('div', 'h-title nowrap', 'Ayarlar'));
    var tight = defs.length > 8;
    for (var i = 0; i < defs.length; i++) {
      var d = defs[i];
      var r = settingRow({ label: d.label, value: d.action ? d.text : d.values[settingValue(d)].name, arrows: !d.action }, i, i === S.setRow,
        tight ? 214 : defs.length > 7 ? 228 : 240, tight ? 82 : defs.length > 7 ? 92 : 100);   /* 8-9 satir ipucu cubuguna tasmasin */
      if (tight) r.style.height = '80px';
      bindPointer(r, 'srow', i);
      screenEl.appendChild(r);
    }
    /* onizleme: ana ekranin olceklenmis kopyasi (tasarimdaki gibi) */
    var pv = el('div', 'pv'), sc = el('div', 'pv-scale');
    setT(sc, 'scale(' + (912 / W) + ')');
    var b = curBg();
    var pbg = el('div', 'bg');
    if (b) {
      var l = el('div', 'bg-layer'); l.style.opacity = 1; l.style.backgroundImage = 'url("' + b.src + '")';
      var sh = el('div', 'bg-shade'); sh.style.background = S.look.layout === 'list' ? SHADE_LIST : SHADE_DOCK;
      pbg.appendChild(l); pbg.appendChild(sh);
    }
    sc.appendChild(pbg);
    var hv = buildHome(sc, true);
    pv.appendChild(sc); screenEl.appendChild(pv);
    g.setTimeout(function () { measure(hv); updateHome(hv, 'list', 0, 0); }, 0);
    updateHome(hv, 'list', 0, 0);
    var pn = el('div', 'pv-note', settingsNote());
    pn.style.top = (240 + 513 + 26) + 'px';
    screenEl.appendChild(pn);
    var cur = DZP.update ? DZP.update.current() : String(g.DZ_VERSION || '');
    var pver = el('div', 'pv-note', 'Sürüm ' + (cur || '—') + (g.DZ_BUILD ? ' · ' + g.DZ_BUILD : ''));
    pver.style.top = '900px';
    screenEl.appendChild(pver);
    screenEl.appendChild(hintBar([['‹ ›', ' Değiştir'], ['Geri', ' Ana sayfa']]));
  }
  function settingsNote() {
    var v = S.look.bg, n = DZP.BG_LIST.length;
    if (v === 'dark') return 'Düz siyah; yazı işi üstlenir.';
    var admin = 'Görselleri yönetim panelinden yükle (' + (api.base() || 'http://<sunucu>') + '/admin → Arka planlar)';
    if (v === 'mine') return n ? 'Görsellerim · ' + n + ' görsel, ~60 sn\'de bir karışık sırayla değişir.' : admin;
    return 'Afişler: odaktaki bölümün öne çıkanları. Görsel yoksa (Canlı TV, DVR) ' +
      (n ? 'Görsellerim\'den biri.' : 'koyu gradyan · ' + admin);
  }
  /* tarayicida gorsel yukleme: gizli dosya secici, secilenler sirayla POST edilir, sonra liste yenilenir */
  function pickUpload() {
    if (S.upBusy) return;
    var inp = doc.createElement('input');
    inp.type = 'file'; inp.accept = 'image/jpeg,image/png,image/webp'; inp.multiple = true;
    inp.style.display = 'none';
    inp.onchange = function () {
      var files = [], i;
      for (i = 0; inp.files && i < inp.files.length; i++) files.push(inp.files[i]);
      if (inp.parentNode) inp.parentNode.removeChild(inp);
      if (!files.length) return;
      S.upBusy = true;
      var ok = 0, errMsg = '';
      (function next(k) {
        if (k >= files.length) {
          S.upBusy = false;
          S.up = errMsg ? 'Hata: ' + errMsg : ok + ' görsel yüklendi';
          return reloadBackgrounds(function () { if (S.screen === 'settings') renderSettings(); });
        }
        S.up = 'Yükleniyor… ' + (k + 1) + '/' + files.length;
        if (S.screen === 'settings') renderSettings();
        api.uploadBackground(files[k], function (err) {
          if (err) errMsg = err.message; else ok++;
          next(k + 1);
        });
      })(0);
    };
    doc.body.appendChild(inp);
    inp.click();
  }
  function changeSetting(row, dir) {
    var defs = settingDefs(), d = defs[row];
    if (!d || d.action) return;
    var n = d.values.length, nv = d.values[(settingValue(d) + dir + n) % n].v;
    if (d.key === 'debug') { if (g.DZDebug) g.DZDebug.set(nv); renderSettings(); return; }
    if (d.key === 'engine') { if (DZP.player && DZP.player.setEnginePref) DZP.player.setEnginePref(nv); renderSettings(); return; }
    S.look[d.key] = nv; saveLook();
    applyBg();
    renderSettings();
  }
  function settingsKey(k) {
    var defs = settingDefs(), n = defs.length, d = defs[S.setRow];
    if (k === 'up') { S.setRow = Math.max(0, S.setRow - 1); renderSettings(); }
    else if (k === 'down') { S.setRow = Math.min(n - 1, S.setRow + 1); renderSettings(); }
    else if (k === 'left') changeSetting(S.setRow, -1);
    else if (k === 'ok' && d && d.key === 'upload') pickUpload();
    else if (k === 'ok' && d && d.key === 'profile') openProfiles('settings');
    else if (k === 'ok' && d && d.key === 'server') openServer('settings');
    else if (k === 'ok' && d && d.key === 'update') { if (!DZP.update.install()) DZP.update.check(true); }
    else if (k === 'right' && d && d.action) return;
    else if (k === 'right' || k === 'ok') changeSetting(S.setRow, 1);
    else if (k === 'back') { S.zone = 'controls'; S.ctrl = ctrlIdx('settings'); goHome(); }
  }

  /* =====================================================================
     SUNUCU ADRESI (ilk acilis; Ayarlar > Sunucu)
     IP ya da URL (kurallar: api.serverCandidates; alan adi once https://, sonra http://:8090; adaylar sirayla denenir). "Baglan" KAYDETMEDEN /api/health dener;
     basariliysa kaydeder ve profil ekranina gecer. Kumanda: satir 0 girdi (OK = ekran klavyesi; klavyede Done = Baglan),
     satir 1 [Baglan].
     ===================================================================== */
  var SV = null;
  function openServer(from) {
    var cur = api.base();
    S.srv = { from: from, value: cur || api.suggestBase(), row: 1, busy: false, msg: '', err: false, fail: false, cont: null, back: '' };
    if (!S.srv.value) S.srv.row = 0;
    go('server');
  }
  /* BAGLANTI HATASI (profil yukleme, ana ekran yukleme): ayni ekran hata kipinde -> hata mesaji + mevcut adresle dolu kutu
     (odak kutuda; OK = ekran klavyesi, Done/Enter = Baglan) + tek [Baglan]. Ayni adresle Baglan = yeniden dene: basariliysa
     `cont` (kaldigi yerden devam); adres degistiyse kaydedilir ve profil secimine gecilir. back: 'settings' | 'home' | '' (cikis onayi). */
  function openServerError(message, cont, back) {
    var cur = api.base();
    S.srv = { from: 'error', back: back || '', value: cur || api.suggestBase(), row: 0, busy: false,
      msg: 'Sunucuya bağlanılamadı (' + (cur || '—') + '): ' + (message || 'bağlantı hatası'), err: true, fail: true, cont: cont };
    go('server');
  }
  function liveClock() {
    var ck = el('div', 'clock live', clockText());
    ck.style.right = '144px';
    return ck;
  }
  function renderServer() {
    var V = S.srv;
    screenEl.innerHTML = '';
    var backLbl = V.from === 'settings' || V.back === 'settings' ? 'Ayarlar' : (V.back === 'home' ? 'Ana sayfa' : '');
    if (backLbl) {
      var back = el('div', 'back'); back.innerHTML = chev(true); back.appendChild(doc.createTextNode(backLbl));
      back.onclick = function () { onKey('back'); };
      screenEl.appendChild(back);
    }
    screenEl.appendChild(liveClock());
    var lg = brandImg('brand-logo', 'img/logo-wide.png', 'DiziFlix'); lg.style.top = '76px';
    screenEl.appendChild(lg);
    var t = el('div', 'c-title', V.fail ? 'Sunucuya bağlanılamadı' : 'Sunucu adresi'); t.style.top = '300px';
    var sub = el('div', 'c-sub', 'diziflix sunucunuzun IP adresini ya da URL\'sini girin (örn. 192.168.1.10 ya da https://diziflix.ornek.com).');
    sub.style.top = '392px';
    screenEl.appendChild(t); screenEl.appendChild(sub);

    var fx = 460, fy = 500;
    var fld = el('div', 'fld');
    fld.style.left = fx + 'px'; fld.style.top = fy + 'px';
    var m = makeMat('fld-mat', fx, fy);
    fld.appendChild(m);
    var inp = el('input', 'fld-in');
    inp.type = 'text'; inp.value = V.value; inp.placeholder = '192.168.1.10';
    inp.setAttribute('aria-label', 'Sunucu adresi');
    inp.setAttribute('autocomplete', 'off'); inp.setAttribute('spellcheck', 'false');
    inp.oninput = function () { V.value = inp.value; };
    inp.onfocus = function () { V.row = 0; serverPaint(); };
    inp.dzSubmit = function () { inp.blur(); serverConnect(); };
    fld.appendChild(inp);
    fld.appendChild(el('div', 'fld-ring'));
    bindPointer(fld, 'srv', 0);
    screenEl.appendChild(fld);

    var cw = el('div', 'c-ctrls'); cw.style.top = '648px';
    var b = el('div', 'ctrl');
    b.appendChild(makeMat(''));
    b.appendChild(el('span', '', 'Bağlan'));
    bindPointer(b, 'srv', 1);
    cw.appendChild(b);
    screenEl.appendChild(cw);

    var st = el('div', 'c-status'); st.style.top = '752px';
    screenEl.appendChild(st);
    screenEl.appendChild(hintBar(backLbl ? [['OK', ' Yaz / Bağlan'], ['Geri', ' ' + backLbl]] : [['OK', ' Yaz / Bağlan']]));
    SV = { fld: fld, inp: inp, btn: b, st: st };
    serverPaint();
  }
  function serverPaint() {
    if (!SV) return;
    var V = S.srv;
    SV.fld.className = 'fld' + (V.row === 0 ? ' on' : '');
    SV.btn.className = 'ctrl' + (V.row === 1 ? ' on' : '');
    SV.st.className = 'c-status' + (V.err ? ' err' : '');
    SV.st.textContent = V.msg;
  }
  function serverConnect() {
    var V = S.srv;
    if (!V || V.busy) return;
    var cands = api.serverCandidates(V.value), fails = [];
    if (!cands.length) { V.msg = 'Lütfen sunucunun IP adresini ya da URL\'sini girin.'; V.err = true; V.row = 0; return serverPaint(); }
    V.busy = true;
    (function next(i) {
      if (i >= cands.length) {
        V.busy = false; V.err = true;
        V.msg = 'Bağlanılamadı, kaydedilmedi — ' + fails.join(' · ');
        return serverPaint();
      }
      V.err = false; V.msg = 'Bağlanılıyor: ' + cands[i] + ' …';
      serverPaint();
      api.probe(cands[i], function (err) {
        if (S.srv !== V) return;
        if (err) { fails.push(cands[i] + ': ' + (err.message || 'bağlantı hatası')); return next(i + 1); }
        V.busy = false;
        connected(cands[i]);
      });
    })(0);
    function connected(base) {
      V.value = base; if (SV) SV.inp.value = base;
      if (V.cont && base === api.base()) return V.cont();   /* ayni adres: yeniden dene, kaldigi yerden */
      api.setBase(base);
      DZP.BG_LIST = []; reloadBackgrounds();
      S.pid = null; S.profiles = []; S.pname = '';
      openProfiles('start');
    }
  }
  function serverKey(k) {
    var V = S.srv;
    if (k === 'up') V.row = 0;
    else if (k === 'down') V.row = 1;
    else if (k === 'ok') {
      if (V.row === 0 && SV) { try { SV.inp.focus(); } catch (e) {} return; }   /* TV: odak girdiye -> ekran klavyesi */
      return serverConnect();
    } else if (k === 'back') {
      if (V.from === 'settings' || V.back === 'settings') go('settings');
      else if (V.back === 'home') goHome();
      else exitApp();
      return;
    }
    else return;
    serverPaint();
  }

  /* =====================================================================
     PROFIL SECIMI (her soguk acilista; Ayarlar > Profil). Kartlar = profiller (misafir HARIC); alt satir: Misafir, Profil ekle,
     Profilleri duzenle (duzenleme kipi: kart OK = duzenle). Yukleme hatasi -> sunucu adresi ekrani hata kipinde (openServerError). Odak son secilen profilde.
     ===================================================================== */
  var PV = null, PF_W = 240, PF_GAP = 40;
  function openProfiles(from) {
    S.pf = { from: from, list: [], guest: null, cards: [], zone: 'cards', idx: 0, foot: 0, manage: false, loading: true, err: null };
    go('profiles');
    pfLoad();
  }
  function pfLoad() {
    var P = S.pf;
    P.loading = true; P.err = null;
    if (S.screen === 'profiles' && PV) renderProfiles();
    api.profiles(function (err, data) {
      if (S.pf !== P) return;
      P.loading = false;
      if (err) {
        P.err = err.message;
        var from = P.from;
        return openServerError(err.message, function () { openProfiles(from); }, from === 'settings' ? 'settings' : (from === 'home' ? 'home' : ''));
      }
      {
        P.list = data.profiles || [];
        P.cards = []; P.guest = null;
        var last = S.pid || store(K_PROFILE);
        for (var i = 0; i < P.list.length; i++) {
          if (P.list[i].guest) { P.guest = P.guest || P.list[i]; continue; }
          if (P.list[i].id === last) P.idx = P.cards.length;
          P.cards.push(P.list[i]);
        }
        S.profiles = P.list;
      }
      var guestCur = !!(P.guest && S.pid && S.pid === P.guest.id);   /* misafirken odak alt satirdaki Misafir'de */
      P.zone = (P.cards.length && !guestCur) ? 'cards' : 'foot';
      if (guestCur) P.foot = 0;
      P.idx = clamp(P.idx, 0, Math.max(0, P.cards.length - 1));
      if (S.screen === 'profiles') renderProfiles();
    });
  }
  function pfFoot() {
    var P = S.pf, f = [];
    if (P.err) f.push({ id: 'retry', label: 'Yeniden dene' });
    if (P.guest && !P.manage) f.push({ id: 'guest', label: 'Misafir' });
    if (!P.err) {
      f.push({ id: 'add', label: 'Profil ekle' });
      if (P.cards.length) f.push({ id: 'manage', label: P.manage ? 'Bitti' : 'Profilleri düzenle' });
    }
    return f;
  }
  function avatarBox(p, cls) {
    var av = el('div', cls, String(p.name || '?').charAt(0).toUpperCase());
    if (p.avatar) {
      var img = el('img');
      img.alt = '';
      img.onload = function () { av.textContent = ''; av.appendChild(img); };
      img.src = api.sized(p.avatar, 200, 200);
    }
    return av;
  }
  function renderProfiles() {
    var P = S.pf;
    screenEl.innerHTML = '';
    if (P.from === 'settings' || P.from === 'home') {
      var back = el('div', 'back'); back.innerHTML = chev(true); back.appendChild(doc.createTextNode(P.from === 'home' ? 'Ana sayfa' : 'Ayarlar'));
      back.onclick = function () { onKey('back'); };
      screenEl.appendChild(back);
    }
    screenEl.appendChild(liveClock());
    var lg = brandImg('brand-logo pf-logo', 'img/logo-wide.png', 'DiziFlix'); lg.style.top = '52px';
    screenEl.appendChild(lg);
    var t = el('div', 'c-title', P.manage ? 'Düzenlenecek profili seçin' : 'Kim izliyor?'); t.style.top = '250px';
    screenEl.appendChild(t);
    var wrap = el('div', 'pf-wrap'), inn = el('div', 'pf-in'), nodes = [];
    for (var i = 0; i < P.cards.length; i++) {
      var p = P.cards[i], c = el('div', 'pf');
      c.style.left = (i * (PF_W + PF_GAP)) + 'px';
      c.appendChild(avatarBox(p, 'pf-av'));
      c.appendChild(el('div', 'pf-name ell', p.name || ''));
      if (p.is_kids) c.appendChild(el('div', 'pf-tag', 'Çocuk'));
      if (P.manage) c.appendChild(el('div', 'pf-badge', '✎'));
      bindPointer(c, 'pf', i);
      inn.appendChild(c); nodes.push(c);
    }
    wrap.appendChild(inn); screenEl.appendChild(wrap);
    var msg = el('div', 'c-status' + (P.err ? ' err' : ''), P.loading ? 'Yükleniyor…' : (P.err ? 'Profiller yüklenemedi: ' + P.err :
      (P.cards.length ? '' : 'Henüz profil yok. "Profil ekle" ile başlayın ya da Misafir olarak girin.')));
    msg.style.top = P.cards.length ? '730px' : '520px';
    screenEl.appendChild(msg);
    var foot = pfFoot(), cw = el('div', 'c-ctrls'), btns = [];
    cw.style.top = '800px';
    for (var j = 0; j < foot.length; j++) {
      var b = el('div', 'ctrl');
      b.appendChild(makeMat(''));
      b.appendChild(el('span', '', foot[j].label));
      bindPointer(b, 'pfoot', j);
      cw.appendChild(b); btns.push(b);
    }
    screenEl.appendChild(cw);
    screenEl.appendChild(hintBar(P.manage ? [['OK', ' Düzenle'], ['Geri', ' Bitti']] :
      (P.from === 'settings' ? [['OK', ' Seç'], ['Geri', ' Ayarlar']] : (P.from === 'home' ? [['OK', ' Seç'], ['Geri', ' Ana sayfa']] : [['OK', ' Seç']]))));
    PV = { inn: inn, cards: nodes, btns: btns, foot: foot };
    pfPaint();
  }
  function pfPaint() {
    if (!PV) return;
    var P = S.pf, n = P.cards.length, step = PF_W + PF_GAP, i;
    P.foot = clamp(P.foot, 0, PV.foot.length - 1);
    /* ortala; 6'dan fazlaysa odaga gore kaydir */
    var total = n * step - PF_GAP, vis = 6, off = 0;
    if (n > vis) off = clamp(P.idx - 2, 0, n - vis) * step;
    var x = n > vis ? (W - (vis * step - PF_GAP)) / 2 - off : (W - total) / 2;
    setT(PV.inn, 'translateX(' + Math.round(x) + 'px)');
    for (i = 0; i < n; i++) PV.cards[i].className = 'pf' + (P.zone === 'cards' && i === P.idx ? ' on' : '');
    for (i = 0; i < PV.btns.length; i++) PV.btns[i].className = 'ctrl' + (P.zone === 'foot' && i === P.foot ? ' on' : '');
  }
  function profilesKey(k) {
    var P = S.pf, n = P.cards.length;
    if (P.loading && k !== 'back') return;
    if (k === 'back') {
      if (P.manage) { P.manage = false; return renderProfiles(); }
      if (P.from === 'settings') return go('settings');
      /* ana ekrandan (profil pill'i): profil degismeden geri; secili profil silinmisse ekranda kal */
      if (P.from === 'home' && (P.loading || P.err || curProfile())) return goHome();
      return exitApp();   /* kok ekran (soguk acilis profil secimi) */
    }
    if (P.zone === 'cards') {
      if (k === 'left' && P.idx > 0) P.idx--;
      else if (k === 'right' && P.idx < n - 1) P.idx++;
      else if (k === 'down') P.zone = 'foot';
      else if (k === 'ok' && n) {
        var p = P.cards[P.idx];
        if (P.manage) return openPedit(p);
        return selectProfile(p);
      } else return;
    } else {
      if (k === 'left' && P.foot > 0) P.foot--;
      else if (k === 'right' && P.foot < PV.foot.length - 1) P.foot++;
      else if (k === 'up' && n) P.zone = 'cards';
      else if (k === 'ok') {
        var id = PV.foot[P.foot].id;
        if (id === 'guest') return selectProfile(P.guest);
        if (id === 'add') return openPedit(null);
        if (id === 'retry') return pfLoad();
        if (id === 'manage') { P.manage = !P.manage; P.foot = 0; return renderProfiles(); }
      } else return;
    }
    pfPaint();
  }

  /* =====================================================================
     PROFIL EKLE / DUZENLE (misafir haric). Satirlar: Ad (OK = ekran klavyesi), Avatar (‹ › katalog), Cocuk (‹ ›), Kaydet,
     Sil (yalniz duzenle; iki kez OK).
     ===================================================================== */
  var PEV = null;
  function openPedit(p) {
    var E = S.pe = { id: p ? p.id : null, name: p ? p.name : '', seed: p ? p.avatar_seed : null, kids: p ? !!p.is_kids : false,
      avatars: [], row: 0, confirm: false, busy: false, msg: '', err: false };
    go('pedit');
    api.avatars(function (err, data) {
      if (S.pe !== E) return;
      E.avatars = (!err && data && data.avatars) || [];
      if (!E.seed && E.avatars.length) E.seed = E.avatars[0].seed;
      if (S.screen === 'pedit') renderPedit();
    });
  }
  function peRows() {
    var E = S.pe, rows = [{ id: 'name', label: 'Ad' }, { id: 'avatar', label: 'Avatar', arrows: true },
      { id: 'kids', label: 'Çocuk profili', arrows: true }, { id: 'save', label: E.id ? 'Kaydet' : 'Oluştur' }];
    if (E.id) rows.push({ id: 'del', label: 'Sil' });
    return rows;
  }
  function peAvatarUrl() {
    var E = S.pe;
    for (var i = 0; i < E.avatars.length; i++) if (E.avatars[i].seed === E.seed) return E.avatars[i].url;
    return '';
  }
  function renderPedit() {
    var E = S.pe, rows = peRows(), i;
    E.row = clamp(E.row, 0, rows.length - 1);
    screenEl.innerHTML = '';
    var back = el('div', 'back'); back.innerHTML = chev(true); back.appendChild(doc.createTextNode('Profiller'));
    back.onclick = function () { onKey('back'); };
    screenEl.appendChild(back);
    screenEl.appendChild(el('div', 'h-title nowrap', E.id ? 'Profili düzenle' : 'Profil ekle'));
    var inp = null, pvName = null;
    for (i = 0; i < rows.length; i++) {
      var r = rows[i], val = '';
      if (r.id === 'avatar') {
        var ai = -1;
        for (var a = 0; a < E.avatars.length; a++) if (E.avatars[a].seed === E.seed) ai = a;
        val = E.avatars.length ? (ai + 1) + ' / ' + E.avatars.length : 'Yükleniyor…';
      } else if (r.id === 'kids') val = E.kids ? 'Evet' : 'Hayır';
      else if (r.id === 'save') val = E.busy ? 'Kaydediliyor…' : (E.id ? 'Değişiklikleri kaydet' : 'Profili oluştur');
      else if (r.id === 'del') val = E.confirm ? 'Emin misiniz? Silmek için tekrar OK' : 'Profili sil';
      var row = settingRow({ label: r.label, value: val, arrows: r.arrows }, i, i === E.row, 240);
      if (r.id === 'name') {
        var sv = row.querySelector('.sr-value');
        inp = el('input', 'sr-input');
        inp.type = 'text'; inp.value = E.name; inp.placeholder = 'Profil adı'; inp.maxLength = 40;
        inp.setAttribute('autocomplete', 'off'); inp.setAttribute('spellcheck', 'false');
        inp.oninput = function () { E.name = inp.value; if (pvName) pvName.textContent = E.name || ' '; };
        inp.dzSubmit = function () { inp.blur(); E.row = 1; renderPedit(); };
        sv.parentNode.replaceChild(inp, sv);
      }
      bindPointer(row, 'perow', i);
      screenEl.appendChild(row);
    }
    /* sagda onizleme: buyuk avatar + ad */
    var pv = el('div', 'pe-pv');
    var url = peAvatarUrl();
    var av = avatarBox({ name: E.name, avatar: url }, 'pe-av');
    if (url) { av.textContent = ''; var im = el('img'); im.alt = ''; im.src = api.sized(url, 200, 200); av.appendChild(im); }
    pv.appendChild(av);
    pvName = el('div', 'pe-name ell', E.name || ' ');
    pv.appendChild(pvName);
    if (E.kids) pv.appendChild(el('div', 'pe-tag', 'Çocuk profili'));
    screenEl.appendChild(pv);
    var st = el('div', 'pv-note' + (E.err ? ' warn' : ''), E.msg);
    st.style.top = (240 + rows.length * 100 + 20) + 'px'; st.style.left = '140px'; st.style.width = '700px';
    screenEl.appendChild(st);
    screenEl.appendChild(hintBar([['OK', ' Seç / Yaz'], ['‹ ›', ' Değiştir'], ['Geri', ' Profiller']]));
    PEV = { inp: inp };
  }
  function peSave() {
    var E = S.pe;
    if (E.busy) return;
    var name = String(E.name || '').replace(/^\s+|\s+$/g, '');
    if (!name) { E.msg = 'Profil adı boş olamaz.'; E.err = true; E.row = 0; return renderPedit(); }
    var body = { name: name, is_kids: E.kids };
    if (E.seed) body.avatar_seed = E.seed;
    E.busy = true; E.msg = ''; E.err = false;
    renderPedit();
    var cb = function (err) {
      if (S.pe !== E) return;
      E.busy = false;
      if (err) { E.msg = 'Kaydedilemedi: ' + err.message; E.err = true; return renderPedit(); }
      peBack();
    };
    if (E.id) api.updateProfile(E.id, body, cb); else api.createProfile(body, cb);
  }
  function peDelete() {
    var E = S.pe;
    if (E.busy) return;
    E.busy = true;
    api.deleteProfile(E.id, function (err) {
      if (S.pe !== E) return;
      E.busy = false; E.confirm = false;
      if (err) { E.msg = 'Silinemedi: ' + err.message; E.err = true; return renderPedit(); }
      if (store(K_PROFILE) === E.id) { try { g.localStorage.removeItem(K_PROFILE); } catch (e) {} }
      if (S.pid === E.id) { S.pid = null; S.pname = ''; }
      peBack();
    });
  }
  /* profil ekranina don (liste yeniden yuklenir; kip ve kaynak korunur) */
  function peBack() {
    var P = S.pf || { from: 'start', manage: false };
    S.pf = { from: P.from, list: [], guest: null, cards: [], zone: 'cards', idx: P.idx || 0, foot: P.foot || 0, manage: P.manage, loading: true, err: null };
    go('profiles');
    pfLoad();
  }
  function peditKey(k) {
    var E = S.pe, rows = peRows(), r = rows[E.row];
    if (E.busy) return;
    if (k === 'back') return peBack();
    if (k === 'up') { E.row = Math.max(0, E.row - 1); E.confirm = false; }
    else if (k === 'down') { E.row = Math.min(rows.length - 1, E.row + 1); E.confirm = false; }
    else if ((k === 'left' || k === 'right') && r.id === 'avatar' && E.avatars.length) {
      var n = E.avatars.length, ai = 0, d = k === 'left' ? -1 : 1;
      for (var i = 0; i < n; i++) if (E.avatars[i].seed === E.seed) ai = i;
      E.seed = E.avatars[(ai + d + n) % n].seed;
    } else if ((k === 'left' || k === 'right' || k === 'ok') && r.id === 'kids') E.kids = !E.kids;
    else if (k === 'ok' && r.id === 'name') { if (PEV && PEV.inp) { try { PEV.inp.focus(); } catch (e) {} } return; }
    else if (k === 'ok' && r.id === 'avatar' && E.avatars.length) return peditKey('right');
    else if (k === 'ok' && r.id === 'save') return peSave();
    else if (k === 'ok' && r.id === 'del') { if (E.confirm) return peDelete(); E.confirm = true; }
    else return;
    renderPedit();
  }

  /* =====================================================================
     ekran yonetimi, perde, tuslar
     ===================================================================== */
  var swapTimer = null;
  function go(screen) {
    S.screen = screen; S.pop = null;
    screenEl.className = 'is-out';
    if (swapTimer) g.clearTimeout(swapTimer);
    swapTimer = g.setTimeout(function () {
      applyClasses();
      HV = null; GV = null; SECV = null; SV = null; PV = null; PEV = null; SRV = null;
      if (screen === 'home') renderHome();
      else if (screen === 'search') renderSearch();
      else if (screen === 'grid') renderGrid();
      else if (screen === 'section') renderSection();
      else if (screen === 'soon') renderSoon();
      else if (screen === 'detail') renderDetail();
      else if (screen === 'settings') renderSettings();
      else if (screen === 'server') renderServer();
      else if (screen === 'profiles') renderProfiles();
      else if (screen === 'pedit') renderPedit();
      void screenEl.offsetWidth;
      screenEl.className = '';
    }, 140);
  }
  function goHome() { go('home'); }
  /* kok ekranlarda (ana ekran menu basi, profil secimi, ilk sunucu adresi) Geri: cam "Uygulamadan çıkılsın mı?" onayi
     (js/exitconfirm.js; Hayir varsayilan). Evet: Android kabugu `DZAndroid.exit()` (kabuk Android Geri'yi JS'e Escape olarak verir),
     Tizen `tizen.application.getCurrentApplication().exit()`, tarayici: yalniz kapanir. */
  function exitApp() {
    if (DZP.exitConfirm) DZP.exitConfirm.open(stage, { mat: makeMat });
  }
  /* guncelleme (js/update.js): yalniz ana ekranda, baska pencere/perde yokken (oynatmada asla).
     Android: "Güncelleme hazır · X" Sonra / Şimdi kur (-> DZAndroid.installUpdate()); Tizen: surum basina bir kez bildirim. */
  function updatePrompt() {
    var U = DZP.update;
    if (!U || S.screen !== 'home' || S.veil || S.pop || (DZP.exitConfirm && DZP.exitConfirm.active())) return;
    var p = U.homePrompt();
    if (!p) return;
    if (p.type === 'install' && DZP.exitConfirm) DZP.exitConfirm.open(stage, { mat: makeMat, title: p.title, labels: ['Sonra', 'Şimdi kur'], onYes: U.install });
    else if (p.type === 'notify') showVeil(p.title, p.text, false, false, 7000);
  }
  if (DZP.update) DZP.update.onChange = function () {
    if (S.screen === 'settings') renderSettings();
    updatePrompt();
  };

  var veilTimer = null;
  /* keep: kendiliginden kapanmaz (oynatici "Aciliyor…" perdesi; oynatici kapatir) */
  function showVeil(title, sub, keep, logo, ms) {
    veil.innerHTML = '';
    if (logo) veil.appendChild(brandImg('veil-logo', 'img/mascot.png', ''));
    veil.appendChild(el('div', 'veil-t', title));
    if (sub) veil.appendChild(el('div', 'veil-s', sub));
    veil.className = 'on';
    S.veil = !keep;
    if (veilTimer) g.clearTimeout(veilTimer);
    veilTimer = keep ? null : g.setTimeout(hideVeil, ms || 1600);
  }
  function hideVeil() { if (veilTimer) g.clearTimeout(veilTimer); veilTimer = null; veil.className = ''; S.veil = false; }

  /* =====================================================================
     OYNATICI (js/player.js). Detay > Izle/Fragman ya da Devam Et karti. Acilirken arka plan donmesi durur, ekran DOM'u
     bosaltilir (.is-play: .bg + #screen gizli); cikista geri gelir: detaydan -> detay (yeniden yuklenir, odak Izle), Devam Et'ten
     -> ana ekran (serit yenilenir).
     ===================================================================== */
  function startPlay(o) {
    var it = o.item;
    S.play = { from: S.screen, channels: o.channels || null };
    if (peekDebounce) { g.clearTimeout(peekDebounce); peekDebounce = null; }
    if (swapTimer) { g.clearTimeout(swapTimer); swapTimer = null; }
    hideVeil();
    S.screen = 'player'; S.pop = null;
    screenEl.className = ''; screenEl.innerHTML = '';
    HV = null; GV = null; SECV = null; SV = null; PV = null; PEV = null; SRV = null;
    applyClasses();
    var art = o.live ? '' : wideSrc(it, 1280, 720);
    DZP.player.open({
      stage: stage, before: veil, pid: S.pid, itemId: it.id, episodeId: o.episodeId || null, kind: o.kind || null,
      position: o.position || 0, restart: !!o.restart, title: it.title || '', type: o.type || it.type, detail: o.detail || null,
      backdrop: art, live: !!o.live, channels: o.channels || null, channelNo: it.channel_no || null,
      veil: function (t, s) { showVeil(t, s, true, !art); }, unveil: hideVeil,   /* afis yoksa perdede maskot */
      onExit: endPlay
    });
  }
  function endPlay(info) {
    var from = S.play ? S.play.from : 'home', chs = S.play ? S.play.channels : null;
    S.play = null;
    hideVeil();
    /* kanal: bolum ekranina donulur, odak oynatilan SON kanalin kartinda (oynaticida kanal degismis olabilir) */
    if (chs && info && info.itemId) {
      for (var ci = 0; ci < chs.items.length; ci++) if (chs.items[ci].id === info.itemId) { chs.col = ci; break; }
      if (from === 'section' && S.sec && S.sec.rows) for (var ri = 0; ri < S.sec.rows.length; ri++) if (S.sec.rows[ri] === chs) S.sec.row = ri;
      if (from === 'grid' && S.grid && S.grid.entry === chs && ci < chs.items.length) S.grid.idx = ci;
    }
    if (from === 'detail' && S.detail) { var D = S.detail, fid = D.rows && D.rows[D.row] ? D.rows[D.row].id : null; return openDetail(D.item, D.entry, D.from, fid); }
    if (from === 'home') return homeRefresh();
    go(from);
  }
  /* ana ekrana don + Devam Et seridi yenilenir (oynatma sonrasi ilerleme) */
  function homeRefresh() {
    go('home');
    var gen = S.gen;
    api.row('continue', S.pid, 0, 20, function (err, data) {
      if (err || gen !== S.gen) return;
      S.cont = { items: (data && data.items) || [], idx: 0 };
      if (S.zone === 'cont' && !S.cont.items.length) S.zone = 'list';
      if (S.screen === 'home') renderHome();
    });
  }
  function playFromDetail(D, r) {
    var it = D.item, d = D.data || {}, a = r.act || {}, ep = null, pos = 0;
    if (r.id === 'play' || r.id === 'restart') {
      if (it.card_kind === 'episode' && it.episode_id) ep = it.episode_id;            /* secili bolum karti */
      else if (a.episode_id && a.episode_id !== it.id) ep = a.episode_id;            /* devam edilecek / ilk bolum */
      if (r.id === 'play' && a.position && (!ep || a.episode_id === ep)) pos = a.position;
    }
    startPlay({ item: it, type: d.type || it.type, detail: D.data, episodeId: ep, position: pos, restart: r.id === 'restart',
      kind: r.id === 'trailer' ? 'trailer' : null });
  }
  /* kanal karti: detay yok; row = kanalin acildigi satir ({id, items, total}; oynatici gerekirse /api/row ile devamini alir) */
  function playChannel(it, row) {
    startPlay({ item: it, type: 'channel', live: true, channels: row });
  }
  function playContinue(it) {
    var pa = it.primary_action || {}, pr = it.progress || {};
    var ep = pa.episode_id || (it.card_kind === 'episode' ? it.episode_id : null) || pr.episode_id || null;
    if (ep === it.id) ep = null;                                                     /* film: episode_id == item_id */
    startPlay({ item: it, type: it.type, episodeId: ep, position: pa.position || pr.position || 0,
      kind: pa.kind === 'play_trailer' ? 'trailer' : null });
  }

  /* ---------- hizli profil degistirici (Profil pill'i OK): cam acilir menu ----------
     Profiller (misafir haric) · Misafir · Profiller (tam ekran profil sayfasi). Liste onbellekten (S.profiles), acilinca
     /api/profiles arka planda tazelenir. ^v gez, OK = gec (odak pill'de kalir), Geri / diger yon = kapat (profil degismez). */
  var POP_W = 440, POP_ROW = 72, POP_PAD = 12;
  function popItems() {
    var it = [], gst = null;
    for (var i = 0; i < S.profiles.length; i++) {
      if (S.profiles[i].guest) { gst = gst || S.profiles[i]; continue; }
      it.push({ key: S.profiles[i].id, p: S.profiles[i], label: S.profiles[i].name || '—' });
    }
    if (gst) it.push({ key: gst.id, p: gst, label: 'Misafir' });
    it.push({ key: '__all', label: 'Profiller' });
    return it;
  }
  function openPop() {
    var P = { items: popItems(), idx: 0 };
    for (var i = 0; i < P.items.length; i++) if (P.items[i].p && P.items[i].p.id === S.pid) P.idx = i;
    S.pop = P;
    popBuild(true);
    api.profiles(function (err, data) {
      if (err || S.pop !== P) return;
      var key = P.items[P.idx].key;
      S.profiles = data.profiles || [];
      P.items = popItems(); P.idx = 0;
      for (var j = 0; j < P.items.length; j++) if (P.items[j].key === key) P.idx = j;
      if (S.screen === 'home' && HV) popBuild(false);
    });
  }
  function closePop() {
    S.pop = null;
    if (HV && HV.pop) { var n = HV.pop; HV.pop = null; n.className = 'pop'; g.setTimeout(function () { if (n.parentNode) n.parentNode.removeChild(n); }, 200); }
  }
  function popBuild(anim) {
    var P = S.pop, v = HV;
    if (!P || !v) return;
    if (v.pop && v.pop.parentNode) v.pop.parentNode.removeChild(v.pop);
    var n = P.items.length, h = n * POP_ROW + POP_PAD * 2 + 9, x, y;
    /* her iki duzende profil hapi ustte: menu hapin altinda, hapin sag kenarina hizali, ekran icinde */
    if (v.list && v.pf) x = clamp((v.top.offsetLeft || 0) + v.pf.offsetLeft + v.pf.offsetWidth - POP_W, PE, 1920 - 122 - POP_W);
    else x = 1920 - 122 - POP_W;
    y = 72 + 64 + 14;
    var box = el('div', 'pop' + (anim ? '' : ' on'));
    box.style.left = x + 'px'; box.style.top = y + 'px'; box.style.width = POP_W + 'px'; box.style.height = h + 'px';
    box.appendChild(makeMat('pop-mat', x, y));
    var hl = el('div', 'pop-hl'); box.appendChild(hl);
    var rows = [];
    for (var i = 0; i < n; i++) {
      var it = P.items[i], r = el('div', 'pop-it'), top = POP_PAD + i * POP_ROW + (it.p ? 0 : 9);
      r.style.top = top + 'px';
      if (it.p) {
        var av = el('span', 'pop-av', it.label.charAt(0).toUpperCase());
        if (it.p.avatar) {
          var im = el('img'); im.alt = '';
          im.onload = (function (a, m) { return function () { a.textContent = ''; a.appendChild(m); }; })(av, im);
          im.src = api.sized(it.p.avatar, 80, 80);
        }
        r.appendChild(av);
      } else { box.appendChild(el('div', 'pop-sep')).style.top = (top - 6) + 'px'; r.className = 'pop-it pop-all'; }
      r.appendChild(el('span', 'pop-name', it.label));
      if (it.p && it.p.id === S.pid) r.appendChild(el('span', 'pop-chk', '\u2713'));
      bindPointer(r, 'pop', i);
      box.appendChild(r); rows.push({ node: r, top: top });
    }
    box.rows = rows; box.hl = hl;
    v.cw.parentNode.appendChild(box);
    v.pop = box;
    popPaint();
    if (anim) { void box.offsetWidth; box.className = 'pop on'; }
  }
  function popPaint() {
    var P = S.pop, b = HV && HV.pop;
    if (!P || !b) return;
    for (var i = 0; i < b.rows.length; i++) b.rows[i].node.className = 'pop-it' + (P.items[i].p ? '' : ' pop-all') + (i === P.idx ? ' on' : '');
    setT(b.hl, 'translateY(' + b.rows[P.idx].top + 'px)');
  }
  function popKey(k) {
    var P = S.pop;
    if (k === 'up' && P.idx > 0) { P.idx--; popPaint(); }
    else if (k === 'down' && P.idx < P.items.length - 1) { P.idx++; popPaint(); }
    else if (k === 'ok') {
      var it = P.items[P.idx];
      closePop();
      if (!it.p) return openProfiles('home');
      if (it.p.id !== S.pid) quickSwitch(it.p);
    } else if (k !== 'up' && k !== 'down') closePop();
  }
  /* aninda profil degisimi: oturum bosaltilir (eski yanitlar gen ile yok sayilir), ana ekran yeniden yuklenir, odak Profil pill'inde */
  function quickSwitch(p) {
    S.pid = p.id;
    if (!p.guest) store(K_PROFILE, p.id);
    setProfileName();
    clearSession();
    S.zone = 'controls'; S.ctrl = ctrlIdx('profile');
    if (S.screen === 'home') renderHome();
    loadHome();
  }

  function homeKey(k) {
    if (S.pop) return popKey(k);
    var list = S.look.layout === 'list', prev = list ? 'up' : 'left', next = list ? 'down' : 'right';
    var n = S.entries.length, cn = S.cont.items.length, ci = S.cont.idx;
    if (S.error || (!n && k === 'ok')) { if (k === 'ok') boot(); return; }
    if (S.zone === 'cont' && !cn) S.zone = 'list';
    var hp = heroPlayable(HV);
    if (S.zone === 'play' && !hp) S.zone = 'list';
    if (S.zone === 'list') {
      if (list && k === 'up' && !S.idx) homeFocus('controls', S.idx, PF);   /* List: menunun basindan ^ = profil hapi */
      else if (k === prev) homeFocus('list', Math.max(0, S.idx - 1), S.ctrl);
      else if (k === next) homeFocus('list', Math.min(n - 1, S.idx + 1), S.ctrl);
      else if (k === (list ? 'right' : 'up')) homeFocus(hp && (list || !cn) ? 'play' : (cn ? 'cont' : 'controls'), S.idx, S.ctrl);
      else if (k === 'ok' && n) openSection(S.entries[S.idx]);
      else if (k === 'back' && S.idx) homeFocus('list', 0, S.ctrl);
      else if (k === 'back') exitApp();   /* kok ekran: Android kabugu uygulamayi kapatir */
    } else if (S.zone === 'play') {
      /* vitrin Oynat: List: <- menu, v Devam Et (yoksa kontroller); Dock: v Devam Et (yoksa dock), ^ kontroller, <- dock */
      if (k === 'left' || k === 'back') homeFocus('list', S.idx, S.ctrl);
      else if (k === 'down') homeFocus(cn ? 'cont' : (list ? 'controls' : 'list'), S.idx, list ? 0 : S.ctrl);
      else if (k === 'up') homeFocus('controls', S.idx, list ? PF : S.ctrl);   /* List: ^ = profil hapi */
      else if (k === 'ok') heroPlay();
    } else if (S.zone === 'cont') {
      /* List: <- menu, ^ Oynat, v kontroller; Dock: v menu, ^ Oynat (yoksa kontroller) */
      if (k === 'left' && ci > 0) { S.cont.idx = ci - 1; homeFocus('cont', S.idx, S.ctrl); }
      else if (k === 'left' && list) homeFocus('list', S.idx, S.ctrl);
      else if (k === 'right' && ci < cn - 1) { S.cont.idx = ci + 1; homeFocus('cont', S.idx, S.ctrl); }
      else if (k === 'up' && hp) homeFocus('play', S.idx, S.ctrl);
      else if (k === (list ? 'down' : 'up')) homeFocus('controls', S.idx, 0);
      else if ((!list && k === 'down') || k === 'back') homeFocus('list', S.idx, S.ctrl);
      else if (k === 'ok') playContinue(S.cont.items[ci]);
    } else if (list && S.ctrl === PF) {
      /* List profil hapi (sag ust): v Oynat (yoksa sutun), <- / Geri menu */
      if (k === 'down') homeFocus(hp ? 'play' : 'controls', S.idx, 0);
      else if (k === 'left' || k === 'back') homeFocus('list', S.idx, S.ctrl);
      else if (k === 'ok') openPop();
    } else if (list) {
      /* List sutunu (Ara · Listem · Ayarlar): ^ basta profil hapi, <- Devam Et (yoksa menu) */
      if (k === 'up') homeFocus('controls', S.idx, S.ctrl > 0 ? S.ctrl - 1 : PF);
      else if (k === 'down') homeFocus('controls', S.idx, Math.min(PF - 1, S.ctrl + 1));
      else if (k === 'left' && cn) homeFocus('cont', S.idx, S.ctrl);
      else if (k === 'left' || k === 'back') homeFocus('list', S.idx, S.ctrl);
      else if (k === 'ok') ctrlOk();
    } else {
      /* Dock ust sirasi (Ara · Listem · Ayarlar · Profil) */
      if (k === 'left' && S.ctrl > 0) homeFocus('controls', S.idx, S.ctrl - 1);
      else if (k === 'right') homeFocus('controls', S.idx, Math.min(CTRLS.length - 1, S.ctrl + 1));
      else if (k === 'down' && hp) homeFocus('play', S.idx, S.ctrl);
      else if (k === 'down' && cn) homeFocus('cont', S.idx, S.ctrl);
      else if (k === 'down' || k === 'back') homeFocus('list', S.idx, S.ctrl);
      else if (k === 'ok') ctrlOk();
    }
  }
  function ctrlOk() {
    var id = CTRLS[S.ctrl].id;
    if (id === 'settings') { S.setRow = 0; S.up = ''; go('settings'); reloadBackgrounds(function () { if (S.screen === 'settings') renderSettings(); }); }
    else if (id === 'profile') openPop();
    else if (id === 'mylist') openGrid({ id: 'mylist', title: 'Listem', items: [], total: null });
    else if (id === 'search') openSearch();
  }

  function onKey(k) {
    if (S.screen === 'player') { if (DZP.player.active()) DZP.player.key(k); return; }
    if (DZP.exitConfirm && DZP.exitConfirm.active()) { DZP.exitConfirm.key(k); return; }
    if (S.veil) { if (k === 'back' || k === 'ok') hideVeil(); return; }
    if (S.screen === 'home') homeKey(k);
    else if (S.screen === 'grid') gridKey(k);
    else if (S.screen === 'detail') detailKey(k);
    else if (S.screen === 'section') sectionKey(k);
    else if (S.screen === 'search') searchKey(k);
    else if (S.screen === 'soon') { if (k === 'back' || k === 'ok') goHome(); }
    else if (S.screen === 'settings') settingsKey(k);
    else if (S.screen === 'server') serverKey(k);
    else if (S.screen === 'profiles') profilesKey(k);
    else if (S.screen === 'pedit') peditKey(k);
  }
  var KEYMAP = { 37: 'left', 38: 'up', 39: 'right', 40: 'down', 13: 'ok', 32: 'ok', 27: 'back', 8: 'back', 10009: 'back', 461: 'back',
    /* Tizen medya/renk tuslari (eski istemcinin keys.js'i ile ayni kodlar); masaustu: F1 = Kirmizi, Y = Sari, G = Yesil (oynaticida hata ayiklama paneli) */
    10252: 'playpause', 415: 'play', 19: 'pause', 413: 'stop', 417: 'ff', 412: 'rew', 403: 'red', 112: 'red', 405: 'yellow', 89: 'yellow', 404: 'green', 71: 'green',
    /* Canli TV: Tizen ChannelUp/Down (427/428), masaustu PageUp/PageDown; rakamlar (ust sira + sayi tus takimi) = kanal numarasi */
    427: 'chup', 428: 'chdown', 33: 'chup', 34: 'chdown' };
  for (var dk = 0; dk < 10; dk++) { KEYMAP[48 + dk] = 'd' + dk; KEYMAP[96 + dk] = 'd' + dk; }
  var MEDIA_KEYS = ['MediaPlayPause', 'MediaPlay', 'MediaPause', 'MediaStop', 'MediaFastForward', 'MediaRewind', 'ColorF0Red', 'ColorF1Green', 'ColorF2Yellow',
    'ChannelUp', 'ChannelDown', '0', '1', '2', '3', '4', '5', '6', '7', '8', '9'];
  var KEYNAME = { ArrowLeft: 'left', ArrowUp: 'up', ArrowRight: 'right', ArrowDown: 'down', Enter: 'ok', ' ': 'ok',
    Escape: 'back', Backspace: 'back', GoBack: 'back', BrowserBack: 'back', XF86Back: 'back',
    MediaPlayPause: 'playpause', MediaPlay: 'play', MediaPause: 'pause', MediaStop: 'stop', MediaFastForward: 'ff', MediaRewind: 'rew',
    ChannelUp: 'chup', ChannelDown: 'chdown', PageUp: 'chup', PageDown: 'chdown' };
  doc.addEventListener('keydown', function (ev) {
    var k = KEYNAME[ev.key] || KEYMAP[ev.keyCode];
    /* metin girdisi odakta (TV ekran klavyesi acik): Enter/Done = gonder, Geri/Cancel = klavyeyi kapat, ^v = birak ve gez;
       diger tuslar (harf, Backspace, <- ->) girdiye kalir. Tizen IME: Done 65376, Cancel 65385. */
    var t = ev.target;
    if (t && t.tagName === 'INPUT') {
      var kc = ev.keyCode;
      if (kc === 13 || kc === 65376) { ev.preventDefault(); if (t.dzSubmit) t.dzSubmit(); else t.blur(); return; }
      if (kc === 27 || kc === 10009 || kc === 461 || kc === 65385) { ev.preventDefault(); t.blur(); return; }
      if (k === 'up' || k === 'down') { ev.preventDefault(); t.blur(); onKey(k); }
      return;
    }
    if (!k) return;
    if (ev.metaKey || ev.ctrlKey || ev.altKey) return;   /* tarayici kisayollari (yenile vb.) */
    /* Kirmizi (masaustu F1): oynatici disinda arama kisayolu (eski istemci: katalogda Kirmizi = arama kutusu) */
    if (k === 'red' && S.screen !== 'player' && SEARCH_FROM[S.screen]) { ev.preventDefault(); return openSearch(); }
    /* medya/renk tuslari yalniz oynaticida; Y harfi / F1 baska ekranda olagan kalir */
    if (S.screen !== 'player' && !/^(left|right|up|down|ok|back)$/.test(k)) return;
    ev.preventDefault();
    onKey(k);
  });
  try { if (g.tizen && g.tizen.tvinputdevice) g.tizen.tvinputdevice.registerKey('Return'); } catch (e) {}
  for (var mk = 0; mk < MEDIA_KEYS.length; mk++) { try { if (g.tizen && g.tizen.tvinputdevice) g.tizen.tvinputdevice.registerKey(MEDIA_KEYS[mk]); } catch (e2) {} }

  /* fare (tarayici): yalniz gercek hareketten sonra odak izler (tasarim: pointermove < 250 ms) */
  var lastMove = 0;
  doc.addEventListener('mousemove', function () { lastMove = new Date().getTime(); });
  function bindPointer(node, kind, i) {
    node.onmouseenter = function () {
      if (new Date().getTime() - lastMove > 250) return;
      pointerFocus(kind, i);
    };
    node.onclick = function () { pointerFocus(kind, i); onKey('ok'); };
  }
  function pointerFocus(kind, i) {
    if (S.pop && kind !== 'pop') closePop();
    if (kind === 'pop' && S.pop) { S.pop.idx = i; popPaint(); }
    else if (kind === 'list' && S.screen === 'home') homeFocus('list', i, S.ctrl);
    else if (kind === 'controls' && S.screen === 'home') homeFocus('controls', S.idx, i);
    else if (kind === 'cont' && S.screen === 'home') { S.cont.idx = i; homeFocus('cont', S.idx, S.ctrl); }
    else if (kind === 'hplay' && S.screen === 'home' && S.zone !== 'cont' && heroPlayable(HV)) homeFocus('play', S.idx, S.ctrl);   /* Devam Et'te: dugme o karti oynatir */
    else if (kind === 'scard' && S.screen === 'section' && S.sec && S.sec.rows) {
      var sr = S.sec.rows[Math.floor(i / 10000)];
      if (sr) { S.sec.row = Math.floor(i / 10000); sr.col = i % 10000; renderSectionWindow(); }
    }
    else if (kind === 'grid' && S.screen === 'grid' && S.grid.idx !== i) { S.grid.idx = i; renderGridWindow(); }
    else if (kind === 'sfld' && S.screen === 'search' && S.srch) { S.srch.zone = 'input'; searchPaint(); }
    else if (kind === 'scard2' && S.screen === 'search' && S.srch) { S.srch.zone = 'rows'; S.srch.row = Math.floor(i / 10000); S.srch.cols[S.srch.row] = i % 10000; searchPaint(); }
    else if (kind === 'drow' && S.screen === 'detail' && S.detail.row !== i) { S.detail.row = i; renderDetail(); }
    else if (kind === 'srow' && S.screen === 'settings' && S.setRow !== i) { S.setRow = i; renderSettings(); }
    else if (kind === 'srv' && S.screen === 'server' && S.srv.row !== i) { S.srv.row = i; serverPaint(); }
    else if (kind === 'pf' && S.screen === 'profiles') { S.pf.zone = 'cards'; S.pf.idx = i; pfPaint(); }
    else if (kind === 'pfoot' && S.screen === 'profiles') { S.pf.zone = 'foot'; S.pf.foot = i; pfPaint(); }
    else if (kind === 'perow' && S.screen === 'pedit' && S.pe.row !== i) { S.pe.row = i; S.pe.confirm = false; renderPedit(); }
  }

  /* ---------- veri ---------- */
  function curProfile() {
    for (var i = 0; S.pid && i < S.profiles.length; i++) if (S.profiles[i].id === S.pid) return S.profiles[i];
    return null;
  }
  function setProfileName() {
    S.pname = '';
    for (var i = 0; i < S.profiles.length; i++) if (S.profiles[i].id === S.pid) S.pname = S.profiles[i].name;
  }
  /* menu (bolumler) + Devam Et seridi (mevcut /api/row/continue) */
  function loadHome() {
    var pending = 2, gen = S.gen;
    function done() {
      if (--pending || gen !== S.gen) return;
      if (posterMode() && !S.error) {      /* afis modu: ana menu acilmadan vitrin gorselleri onbellege (en fazla 4 sn) */
        S.opening = true;
        return posterBoot(function () { S.opening = false; if (gen === S.gen) finish(); });
      }
      finish();
    }
    function finish() {
      S.loading = false;
      if (S.zone === 'cont' && !S.cont.items.length) S.zone = 'list';
      if (S.error) return homeFail(S.error);
      if (S.screen === 'home') renderHome();
    }
    api.sections(S.pid, function (err, data) {
      if (gen !== S.gen) return;   /* profil degisti: eski yanit yok sayilir */
      if (err) { S.error = err.message; S.entries = []; }
      else {
        S.error = null;
        var en = [], ss = data.sections || [];
        for (var i = 0; i < ss.length; i++) {
          var h = ss[i].heroes || [];
          en.push({ id: ss[i].id, title: ss[i].title, status: ss[i].status, items: h, total: h.length });
        }
        S.entries = en;
        S.idx = clamp(S.idx, 0, Math.max(0, en.length - 1));
      }
      done();
    });
    api.row('continue', S.pid, 0, 20, function (err, data) {
      if (gen !== S.gen) return;
      S.cont = { items: (!err && data && data.items) || [], idx: 0 };
      done();
    });
  }
  /* ana ekran yuklenemedi: sunucu adresi ekrani hata kipinde (ayni adresle Baglan = ana ekrani yeniden yukle) */
  function homeFail(message) {
    if (S.screen !== 'home') return;
    openServerError(message, function () { go('home'); boot(); }, '');
  }
  /* profil secili (profil ekrani ya da ?profile= kisayolu): profil listesi + ana ekran verisi */
  function boot() {
    S.loading = true; S.error = null;
    if (S.screen === 'home') renderHome();
    var gen = S.gen;
    api.profiles(function (err, data) {
      if (gen !== S.gen) return;
      if (err) { S.loading = false; S.error = err.message; return homeFail(err.message); }
      S.profiles = data.profiles || [];
      setProfileName();
      loadHome();
    });
  }
  /* profil ekranindan secim: secim saklanir (sonraki soguk acilista odak o kartta), ana ekran sifirdan */
  /* oturumu bosalt: profil ana ekran verisi (menu, Devam Et, bolum/izgara/detay) bellekten silinir; yeni yanitlar gen ile ayrilir */
  function clearSession() {
    S.gen++;
    S.entries = []; S.cont = { items: [], idx: 0 }; S.idx = 0; S.zone = 'list'; S.ctrl = 0; S.heroIdx = 0;
    S.sec = null; S.grid = null; S.detail = null;
    if (S.srch) { S.srch.ctl.dispose(); S.srch = null; }
    S.loading = true; S.error = null;
  }
  function selectProfile(p) {
    S.pid = p.id;
    if (!p.guest) store(K_PROFILE, p.id);
    S.profiles = S.pf ? S.pf.list : S.profiles;
    setProfileName();
    clearSession();
    go('home');
    loadHome();
  }

  /* ---------- baslat ---------- */
  g.addEventListener('resize', fit);
  /* klavye kapandi: tarayicinin girdiyi gorunur tutmak icin yaptigi kaydirmayi geri al, olcegi yeniden degerlendir */
  doc.addEventListener('focusout', function () {
    g.setTimeout(function () { try { g.scrollTo(0, 0); } catch (e) {} fit(); }, 300);
  }, true);
  fit();
  applyClasses();
  g.setInterval(function () {
    if (HV) {
      var t = clockText();
      if (HV.clock.textContent !== t) HV.clock.textContent = t;
      var gt = greetText();
      if (HV.greet.textContent !== gt) HV.greet.textContent = gt;
    }
    var lc = screenEl.querySelector('.clock.live');
    if (lc && lc.textContent !== clockText()) lc.textContent = clockText();
  }, 1000);
  reloadBackgrounds();
  g.setInterval(function () { if (S.screen !== 'player') reloadBackgrounds(); }, 10 * 60000);   /* yonetim panelinden eklenenler (ETag, ucuz) */
  /* SOGUK acilis (sayfa yuklendi): adres yoksa "Sunucu adresi", varsa HER SEFERINDE profil secimi. Arka plana atilip donuste
     (visibilitychange) sayfa yeniden yuklenmez -> burasi calismaz, secili profil devam eder. ?profile= (test kisayolu) profil
     ekranini atlar. */
  if (!api.hasBase()) openServer('first');
  else if (S.pid) { go('home'); boot(); }
  else openProfiles('start');
  /* surum denetimi: acilista (6 saat siniri update.js'te) + 6 saatte bir; tarayicida no-op */
  if (DZP.update) { DZP.update.check(false); g.setInterval(function () { DZP.update.check(false); }, 6 * 3600 * 1000); }
})(window);
