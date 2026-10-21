/* screens/home.js - ana ekran. KADEMELI YUKLEME:
   1) aninda iskelet  2) cache'ten stale render  3) /api/boot  4) gorunen+1 satir icin /api/row/{id}
   5) basarili boot cache'lenir  6) hata: cache varsa serit uyari, yoksa "Tekrar dene" ekrani;
   tek satir hatasi: o satirda odaklanabilir "Tekrar dene" karti; bos satir gizlenir */
(function (g) {
  'use strict';
  var DZ = g.DZ = g.DZ || {};

  var container = null;
  var state = null;

  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }

  function topbar() { return DZ.navigation.create('home'); }

  function banner(msg) {
    hideBanner();
    var b = document.createElement('div');
    b.className = 'banner';
    b.textContent = msg;
    container.appendChild(b);
    state.banner = b;
    state.bannerTimer = setTimeout(hideBanner, 6000);
  }
  function hideBanner() {
    if (state && state.bannerTimer) { clearTimeout(state.bannerTimer); state.bannerTimer = null; }
    if (state && state.banner && state.banner.parentNode) state.banner.parentNode.removeChild(state.banner);
    if (state) state.banner = null;
  }

  function openDetail(item) {
    if (item && item.id) DZ.app.go('detail', { id: item.id });
  }

  /* Bolum karti (card_kind=episode) / "Devam Et" karti: AYRI bir tek-bolumluk sayfa acilmaz.
     Dizinin normal ozet sayfasi (id = yapim id'si) acilir; detay ilgili sezonu secer ve
     ilgili bolume odaklanir (episodeId). Enter o bolumde oynatma akisini baslatir. */
  function cardParams(item, rowId) {
    if (!item || !item.id) return null;
    var params = { id: item.id };
    var epId = null;
    if (item.card_kind === 'episode' && item.episode_id) epId = item.episode_id;
    else if (rowId === 'continue' && item.type === 'series' && item.progress && item.progress.episode_id) {
      epId = item.progress.episode_id;
    }
    if (epId) params.episodeId = epId;
    return params;
  }

  function openCard(item, rowId) {
    var params = cardParams(item, rowId);
    if (params) DZ.app.go('detail', params);
  }

  function toggleMyList(item, btn) {
    var pid = DZ.api.profileId();
    var inList = btn.dzInList === true;
    var p = inList ? DZ.api.removeMyList(pid, item.id) : DZ.api.addMyList(pid, item.id);
    p.then(function () {
      btn.dzInList = !inList;
      item.in_mylist = !inList;
      btn.textContent = btn.dzInList ? 'Listemden Çıkar' : 'Listeme Ekle';
      invalidateRow('mylist');
    }, function (e) { banner('Listem guncellenemedi: ' + e.message); });
  }

  function invalidateRow(rowId) {
    for (var i = 0; i < state.rowEls.length; i++) {
      var r = state.rowEls[i];
      if (r.dzRowId === rowId) {
        r.dzLoaded = false;
        r.dzLoading = false;
        delete state.inflight[rowId];
        r.dzShowSkeleton(6);
        DZ.nav.refresh();
        ensureRows(state.focusRowIndex);
        return;
      }
    }
  }

  /* ---------- satirlarin tembel yuklenmesi ---------- */
  function rowIndexById(id) {
    for (var i = 0; i < state.rowEls.length; i++) if (state.rowEls[i].dzRowId === id) return i;
    return -1;
  }

  function loadRow(rowEl) {
    var current = state;
    var id = rowEl.dzRowId;
    if (rowEl.dzLoaded || state.inflight[id]) return;   /* in-flight guard */
    state.inflight[id] = true;
    rowEl.dzLoading = true;
    DZ.api.row(id, state.profile, 0, 20).then(function (res) {
      if (state !== current || !current.alive) return;
      delete state.inflight[id];
      if (res && res.title) {
        var t = rowEl.querySelector('.row-title');
        if (t) t.textContent = res.title;
      }
      rowEl.dzSetItems(res && res.items ? res.items : []);
      DZ.nav.refresh();
    }, function (err) {
      if (state !== current || !current.alive) return;
      delete state.inflight[id];
      rowEl.dzLoading = false;
      /* satirda odaklanabilir "Tekrar dene" karti; otomatik yeniden istek YOK (dzLoaded true) */
      rowEl.dzShowError(DZ.toast ? DZ.toast.errorText(err) : (err && err.message), function () { retryRow(rowEl); });
      DZ.nav.refresh();
    });
  }

  /* satir hatasi -> Enter: kart yerinde kalir ("Yükleniyor…"), odak kaybolmaz */
  function retryRow(rowEl) {
    if (!state || !state.alive || rowEl.dzLoading) return;
    if (rowEl.dzSetRetrying) rowEl.dzSetRetrying();
    delete state.inflight[rowEl.dzRowId];
    loadRow(rowEl);
  }

  /* odaktan sonra yuklenecek satir sayisi: 1080 sahnede 2 (odak + 2 alt = toplam 3); uzun sahnede (16:10, 5:4, dikey)
     ekrana daha fazla satir sigar -> her ~600 sahne birimi icin +1 (satir ~600 birim yuksek) */
  function rowSpan() {
    var h = (DZ.stage && DZ.stage.h) || 1080;
    return 2 + Math.max(0, Math.ceil((h - 1080) / 600));
  }

  /* gorunen + 1 alt satir (toplam 3; uzun sahnede daha fazla) icin veri cek, gorsel penceresini tazele */
  function ensureRows(focusIdx) {
    var f = focusIdx || 0;
    var span = rowSpan();
    for (var i = f; i <= f + span && i < state.rowEls.length; i++) {
      var r = state.rowEls[i];
      if (!r.dzLoaded) loadRow(r);
      else r.dzUpdateWindow(i === f ? state.focusCol : 0);
    }
  }

  /* satir sonundaki "Tümü" karti -> katalog ekrani (tür + sıralama): series/movies = yeni eklenen; trending_* = sort=trending;
     noteworthy_movies = sort=popular (GET /api/catalog?type=&sort=). Diger satirlar (continue, mylist, bilinmeyen) kart almaz. */
  var CATALOG_ENDS = {
    series: { view: 'series', sort: 'new', label: 'Tüm Diziler' },
    movies: { view: 'movies', sort: 'new', label: 'Tüm Filmler' },
    trending_series: { view: 'series', sort: 'trending', label: 'Tüm Trend Diziler' },
    trending_movies: { view: 'movies', sort: 'trending', label: 'Tüm Trend Filmler' },
    noteworthy_movies: { view: 'movies', sort: 'popular', label: 'Tüm Dikkate Değer Filmler' }
  };

  function catalogEndAction(rowId) {
    var def = CATALOG_ENDS.hasOwnProperty(rowId) ? CATALOG_ENDS[rowId] : null;
    if (!def) return null;
    return {
      label: def.label,
      onSelect: function () { DZ.navigation.go(def.view, { sort: def.sort }); }
    };
  }

  function makeCardSelect(rowId) {
    return function (item) { openCard(item, rowId); };
  }

  /* ---------- "Izlemeye Devam Et": uzun basis -> "Listeden kaldir" menusu ----------
     Yalniz 'continue' satiri kartlari. Kumanda Tamam/OK: keydown'da zamanlayici (HOLD_MS), kisa basis (zaman dolmadan keyup)
     detayi KEYUP'ta acar (yalniz bu kartlarda; digerlerinde Enter eskisi gibi keydown'da). Uzun basista menu acilir; ayni
     basisin keyup'i / otomatik tekrar keydown'lari detayi acmaz ve menu dugmesini tetiklemez. Fare/dokunma: ui/card.js. */
  var HOLD_MS = 600;
  var HOLD_DOG_MS = 1500;   /* keyup hic gelmezse (kayip) "basili" durumu takili kalmasin */
  var hold = { down: false, fired: false, card: null, timer: null, dog: null };
  var removing = {};        /* devam eden kaldirma istekleri: profil:yapim id -> true (cift tetik tek istek) */
  var offKeyUp = null;

  function resetHold() {
    if (hold.timer) { clearTimeout(hold.timer); hold.timer = null; }
    if (hold.dog) { clearTimeout(hold.dog); hold.dog = null; }
    hold.down = false; hold.fired = false; hold.card = null;
  }

  function armHoldDog() {
    if (hold.dog) clearTimeout(hold.dog);
    hold.dog = setTimeout(function () { hold.dog = null; resetHold(); }, HOLD_DOG_MS);
  }

  /* odaktaki oge 'continue' satirinda gercek bir kart mi (yeniden dene karti / son-eylem karti degil) */
  function continueCardAtFocus() {
    var f = DZ.nav.current();
    if (!f || f.rowId !== 'continue') return null;
    var el = DZ.nav.currentEl();
    return (el && el.dzItem && el.dzItem.id) ? el : null;
  }

  function removeKey(id) { return String(state ? state.profile : '') + ':' + id; }

  function openContinueMenu(item, cardEl) {
    if (!state || !state.alive || !item || !item.id) return;
    if (!DZ.modal || DZ.modal.isOpen()) return;
    if (removing[removeKey(item.id)]) return;
    /* fare/dokunma: basilan kart klavye odagindan farkliysa odagi ona al (menu kapaninca odak ayni kartta) */
    var col = cardEl && cardEl.getAttribute ? parseInt(cardEl.getAttribute('data-col'), 10) : NaN;
    var cur = DZ.nav.current();
    if (!isNaN(col) && (!cur || cur.rowId !== 'continue' || cur.col !== col)) DZ.nav.focusRowById('continue', col);
    DZ.modal.open({
      title: item.title || '',
      message: item.episode_label ? String(item.episode_label) : '',
      buttons: [
        { label: 'Listeden kaldır', primary: true, value: true },
        { label: 'Vazgeç', value: false }
      ],
      ignoreEnterWhile: holdGuard,
      onDone: function (v) { if (v) removeContinue(item); }
    });
  }

  /* menu acikken Tamam/OK hala basiliysa (tekrar keydown'lari) menu dugmesi tetiklenmez; yeni basis icin yeni keydown gerekir */
  function holdGuard(ev) {
    if (ev && ev.repeat) return true;
    if (!hold.down) return false;
    armHoldDog();
    return true;
  }

  function holdFire() {
    hold.timer = null;
    if (!hold.down || !state || !state.alive) return;
    hold.fired = true;
    openContinueMenu(hold.card ? hold.card.dzItem : null, hold.card);
  }

  /* Tamam/OK keydown (home.key'den). true = yutuldu. */
  function onEnterDown(ev) {
    if (hold.down) { armHoldDog(); return true; }   /* otomatik tekrar / cift keydown */
    var card = continueCardAtFocus();
    if (!card) return false;                        /* baska kart/satir: eski davranis (nav.enter) */
    if (ev.repeat) return true;                     /* onceki ekrandan suren basis: hold baslatma */
    hold.down = true; hold.fired = false; hold.card = card;
    hold.timer = setTimeout(holdFire, HOLD_MS);
    armHoldDog();
    return true;
  }

  function onEnterUp(ev) {
    if (!ev || ev.name !== 'enter' || !hold.down) return false;
    var card = hold.card, fired = hold.fired;
    resetHold();
    if (!fired && state && state.alive && card && card.dzItem) openCard(card.dzItem, 'continue');
    return true;
  }

  /* boot verisindeki (bellek ya da localStorage kopyasi) continue satirindan yapim id'sini cikar; bosalirsa satiri sil */
  function dropFromRows(rows, id) {
    var changed = false;
    if (!rows) return false;
    for (var i = rows.length - 1; i >= 0; i--) {
      var r = rows[i];
      if (!r || r.id !== 'continue' || !r.items) continue;
      var kept = r.items.filter(function (it) { return !(it && it.id === id); });
      if (kept.length === r.items.length) continue;
      changed = true;
      if (kept.length) r.items = kept; else rows.splice(i, 1);
    }
    return changed;
  }

  function purgeContinueCache(pid, id) {
    try {
      var c = DZ.api.cachedBoot(pid);
      if (c && dropFromRows(c.rows, id)) DZ.api.saveBoot(pid, c);
    } catch (e) { console.log('[home] continue onbellegi guncellenemedi: ' + e); }
  }

  /* karti satirdan cikar: verileri guncelle, satiri yeniden ciz, odagi komsuya (yoksa oncekine / ust satira) tasi */
  function dropContinueCards(id) {
    if (state.data) dropFromRows(state.data.rows, id);
    var idx = rowIndexById('continue');
    if (idx < 0) return;
    var rowEl = state.rowEls[idx];
    var cards = rowEl.dzCards || [];
    var cur = DZ.nav.current();
    var inRow = !!cur && cur.rowId === 'continue';
    var rowIds = DZ.nav.rowIds();
    var pos = rowIds.indexOf('continue');
    var aboveId = pos > 0 ? rowIds[pos - 1] : null;
    var keep = [], newCol = inRow ? cur.col : 0;
    for (var i = 0; i < cards.length; i++) {
      if (cards[i].dzItem && cards[i].dzItem.id === id) { if (inRow && i < cur.col) newCol--; continue; }
      keep.push(cards[i].dzItem);
    }
    if (newCol > keep.length - 1) newCol = keep.length - 1;
    if (newCol < 0) newCol = 0;
    rowEl.dzSetItems(keep);       /* bossa satir gizlenir (dzShowEmpty) */
    DZ.nav.refresh();
    if (!inRow) return;
    if (keep.length) DZ.nav.focusRowById('continue', newCol);
    else if (aboveId) DZ.nav.focusRowById(aboveId);
    else DZ.nav.focusIndex(0, 0);
  }

  function removeContinue(item) {
    if (!state || !state.alive || !item || !item.id) return;
    var key = removeKey(item.id), pid = state.profile, current = state;
    if (removing[key]) return;
    if (DZ.toast && DZ.toast.isOffline && DZ.toast.isOffline()) { DZ.toast.show('İnternet bağlantısı gerekli'); return; }
    removing[key] = true;
    /* yapim id'si (bolum karti dahil item.id); episode_id DEGIL */
    DZ.api.removeFromContinue(item.id, pid).then(function () {
      delete removing[key];
      purgeContinueCache(pid, item.id);
      try {
        if (state === current && current.alive) dropContinueCards(item.id);
      } catch (e) { console.log('[home] continue karti cikarilamadi: ' + e); }
      if (DZ.toast) DZ.toast.show('Listeden kaldırıldı');
    }, function () {
      delete removing[key];
      if (DZ.toast) DZ.toast.show('Kaldırılamadı, bağlantıyı kontrol edin');
    });
  }

  /* ---------- render ---------- */
  function buildPage(data) {
    var page = document.createElement('div');
    page.id = 'page';

    var heroItem = data && data.hero ? data.hero : null;
    if (heroItem) {
      page.classList.add("has-hero");
      var heroes = data.heroes && data.heroes.length ? data.heroes : [heroItem];
      var heroIndex = 0, holder = document.createElement('div');
      function drawHero(focus) {
        clear(holder);
        var hn = DZ.hero.create({
          item: heroes[heroIndex], onDetail: openDetail,
          index: heroIndex, count: heroes.length
        });
        holder.appendChild(hn);
        if (focus) { DZ.nav.refresh(); DZ.nav.focusRowById('hero', 0); }
      }
      state.moveHero = function (step) { heroIndex = (heroIndex + step + heroes.length) % heroes.length; drawHero(true); };
      drawHero();
      page.appendChild(holder);
    } else {
      var empty = document.createElement('div');
      empty.className = 'empty';
      empty.style.padding = '145px 60px 65px';
      var message = document.createElement('p');
      message.textContent = data.catalog_total ? 'Yeni eklenen diziler ve filmler' : 'Kataloğa henüz içerik eklenmedi.';
      empty.appendChild(message);
      page.appendChild(empty);
    }

    var rowsBox = document.createElement('div');
    rowsBox.className = 'rows';
    state.rowEls = [];

    var rows = (data && data.rows) ? data.rows : [];
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      var rowEl = DZ.row.create({
        id: r.id,
        title: r.title,
        loaded: r.loaded === true,
        items: r.items || [],
        count: r.count || 6,
        portrait: true,
        focusLandscape: true,
        endAction: catalogEndAction(r.id),
        onSelect: makeCardSelect(r.id),
        onLongPress: r.id === 'continue' ? openContinueMenu : undefined,
        onRemove: r.id === 'continue' ? openContinueMenu : undefined   /* fare hover "✕" */
      });
      rowsBox.appendChild(rowEl);
      state.rowEls.push(rowEl);
    }
    page.appendChild(rowsBox);
    state.rowsBox = rowsBox;
    return page;
  }

  function renderBoot(data, fromCache) {
    state.data = data;
    clear(container);
    container.appendChild(topbar());
    var page = buildPage(data);
    container.appendChild(page);
    state.page = page;

    /* setRoot + ilk odak render/layout HAZIR olduktan sonra (cift rAF): TV'de erken
       olcumde hero/topbar butonlari 0 olcup nav disi kaliyordu -> odak alt karta dusuyordu. */
    function initNav() {
      if (!state || !state.alive || state.page !== page) return;
      DZ.nav.setRoot(container, page);
      DZ.nav.onFocus(function (info) {
        if (info.el && info.el.dzLoadFocusImage) info.el.dzLoadFocusImage();
        var hero = page.querySelector('.hero');
        if (hero) hero.classList.toggle('hero-focused', info.rowId === 'hero' || info.rowId === 'hero_actions');
        state.focusCol = info.col;
        var idx = rowIndexById(info.rowId);
        if (idx >= 0) {
          state.focusRowIndex = idx;
          state.rowEls[idx].dzUpdateWindow(info.col);
          ensureRows(idx);
        } else if (info.rowId === 'hero' || info.rowId === 'topbar') {
          state.focusRowIndex = 0;
          ensureRows(0);
        }
        if (info.rowId !== 'hero' && info.rowId !== 'topbar') state.interacted = true;
      });
      /* hero 'Oynat' butonundan basla; bulunamazsa bir kez daha dene, sonra ilk satira dus */
      var ok = DZ.nav.focusRowById('hero', 0);
      if (!ok) {
        g.requestAnimationFrame(function () {
          if (!state || !state.alive || state.page !== page) return;
          if (!DZ.nav.focusRowById('hero', 0)) DZ.nav.focusIndex(0, 0);
          var c2 = DZ.nav.currentEl();
          console.log('[home] focus(retry) -> ' + (c2 ? (c2.textContent || c2.getAttribute('data-nav')) : 'YOK'));
        });
      }
      var cur = DZ.nav.currentEl();
      console.log('[home] focus -> ' + (cur ? (cur.textContent || cur.getAttribute('data-nav')) : 'YOK') + ' hero=' + ok + ' rows=' + DZ.nav.rowIds().join(','));
      ensureRows(0);
    }
    g.requestAnimationFrame(function () { g.requestAnimationFrame(initNav); });
    if (fromCache) console.log('[home] cache render');
  }

  /* taze veri geldiginde: kullanici gezinmediyse tam yeniden ciz, gezindiyse sadece bos satirlari doldur */
  function applyFresh(data) {
    /* kullanici arama kutusuna yaziyorsa tam yeniden cizme (topbar/input yeniden kurulur, odak + ekran klavyesi kaybolur) */
    var typing = !!(DZ.navigation && DZ.navigation.isTyping && DZ.navigation.isTyping());
    if (!state.rendered || (!state.interacted && !typing)) { renderBoot(data, false); return; }
    var rows = (data && data.rows) ? data.rows : [];
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      var idx = rowIndexById(r.id);
      if (idx < 0) continue;
      var el = state.rowEls[idx];
      if (r.loaded === true && (!el.dzLoaded || el.dzError) && !state.inflight[r.id]) {
        el.dzSetItems(r.items || []);
      }
    }
    DZ.nav.refresh();
  }

  function errorScreen(err, retry) {
    clear(container);
    var box = document.createElement('div');
    box.className = 'errscreen';
    box.setAttribute('data-nav-row', 'err');
    var mascot = document.createElement('img');   /* kucuk marka karakteri (dekoratif, data-nav degil) */
    mascot.className = 'brand-mascot';
    mascot.src = 'img/mascot.png';
    mascot.alt = '';
    mascot.onerror = function () { mascot.className += ' hidden'; };
    box.appendChild(mascot);
    var wm = document.createElement('div');
    wm.className = 'wordmark big';
    wm.style.marginBottom = '40px';
    wm.textContent = 'DIZIFLIX';
    box.appendChild(wm);
    var h = document.createElement('h1');
    h.textContent = 'İçerik yüklenemedi';
    box.appendChild(h);
    var p = document.createElement('p');
    p.textContent = (DZ.toast ? DZ.toast.errorText(err) : (err && err.message ? err.message : 'Bilinmeyen hata')) + '  (' + DZ.api.baseUrl() + ')';
    box.appendChild(p);
    var wrap = document.createElement('div');
    wrap.style.display = 'flex';
    var b1 = document.createElement('div');
    b1.className = 'btn primary';
    b1.setAttribute('data-nav', '1');
    b1.textContent = 'Tekrar dene';
    b1.addEventListener('click', retry, false);
    wrap.appendChild(b1);
    var b2 = document.createElement('div');
    b2.className = 'btn';
    b2.setAttribute('data-nav', '1');
    b2.textContent = 'Ayarlar';
    b2.addEventListener('click', function () { DZ.app.go('settings'); }, false);
    wrap.appendChild(b2);
    box.appendChild(wrap);
    container.appendChild(box);
    DZ.nav.setRoot(container, null);
  }

  function fetchBoot() {
    var current = state;
    DZ.api.boot(state.profile).then(function (data) {
      if (state !== current || !current.alive) return;
      hideBanner();
      DZ.api.saveBoot(state.profile, data);
      if (state.rendered) applyFresh(data);
      else { renderBoot(data, false); state.rendered = true; }
    }, function (err) {
      if (state !== current || !current.alive) return;
      if (state.rendered) banner((DZ.toast && DZ.toast.isOffline() ? 'Bağlantı yok' : 'Sunucuya ulaşılamadı') + ', önbellek gösteriliyor');
      else errorScreen(err, function () {
        clear(container);
        container.appendChild(DZ.skeleton.home());
        fetchBoot();
      });
    });
  }

  /* ---------- arama ---------- */
  function openSearch() { DZ.navigation.go('search'); }

  function removeSearchRow() {
    if (!state.searchRow) return;
    if (state.searchRow.parentNode) state.searchRow.parentNode.removeChild(state.searchRow);
    var i = state.rowEls.indexOf(state.searchRow);
    if (i >= 0) state.rowEls.splice(i, 1);
    state.searchRow = null;
    DZ.nav.refresh();
  }

  var screen = {
    name: 'home',
    enter: function (cnt, params) {
      container = cnt;
      state = {
        alive: true,
        profile: (params && params.profile) ? params.profile : DZ.api.profileId(),
        rowEls: [],
        inflight: {},
        focusRowIndex: 0,
        focusCol: 0,
        rendered: false,
        interacted: false,
        banner: null,
        bannerTimer: null,
        searchRow: null
      };

      resetHold();
      if (offKeyUp) { offKeyUp(); offKeyUp = null; }
      if (DZ.keys && DZ.keys.onKeyUp) offKeyUp = DZ.keys.onKeyUp(onEnterUp);

      /* 1) aninda iskelet */
      clear(container);
      container.appendChild(topbar());
      container.appendChild(DZ.skeleton.home());

      /* 2) cache varsa hemen goster (stale-while-revalidate) */
      var cached = DZ.api.cachedBoot(state.profile);
      if (cached) { renderBoot(cached, true); state.rendered = true; }

      /* 3) taze veri */
      fetchBoot();
    },
    key: function (ev) {
      var focused = DZ.nav.current();
      if (state && state.moveHero && focused && focused.rowId === 'hero' && (ev.name === 'left' || ev.name === 'right')) {
        state.moveHero(ev.name === 'left' ? -1 : 1); return true;
      }
      if (ev.name === 'red') { openSearch(); return true; }
      if (ev.name === 'enter' && !ev.typing && state && state.alive && onEnterDown(ev)) return true;
      return false;
    },
    back: function () {
      if (state && state.searchRow) { removeSearchRow(); return true; }
      DZ.app.confirmExit();
      return true;
    },
    exit: function () {
      resetHold();
      if (offKeyUp) { offKeyUp(); offKeyUp = null; }
      if (state) { state.alive = false; hideBanner(); }
      state = null;
      container = null;
    },
    /* detay/player donusunde ilerlemeler degismis olabilir */
    resume: function () {
      if (state && state.alive) fetchBoot();
    },
    /* sahne boyutu degisti: daha fazla satir/kart gorunur olabilir -> veri + gorsel penceresini tazele */
    onResize: function () {
      if (state && state.alive && state.rendered && state.rowEls.length) ensureRows(state.focusRowIndex);
    }
  };

  screen.cardParams = cardParams;   /* testler icin */
  screen.catalogEndAction = catalogEndAction;
  screen.debugState = function () { return state; };   /* testler icin */
  screen.rowSpan = rowSpan;
  DZ.screens = DZ.screens || {};
  DZ.screens.home = screen;
})(window);
