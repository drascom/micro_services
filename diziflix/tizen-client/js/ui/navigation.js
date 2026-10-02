(function(g){
  /* ui/navigation.js - ust menu + YAZARKEN ARAMA (Android SearchViewModel ile ayni kurallar).
     Menu: [amblem + logo DIZIFLIX] [arama kutusu + ✕] [Listem] [Profil] [Ayarlar]; "Ana Sayfa" dugmesi YOK (Geri tusu zaten ana sayfaya goturur).
     Logo yalniz fare/dokunma icin tiklanir (= Ana Sayfa; ana sayfadaysa en uste doner), kumanda odak sirasinda DEGIL
     -> odak sirasi: [arama kutusu (sutun 0)] [✕ (doluyken)] [Listem] [Profil] [Ayarlar].
     Arama: her tusta bekleyen arama iptal edilir; DEBOUNCE_MS sessizlikten sonra >= MIN_QUERY karakterde aranir (< MIN_QUERY: arama
     yok, arama gorunumundeyse sonuclar temizlenir). Enter/Ara bekletmez. Ekran/topbar YENIDEN KURULMAZ: arama gorunumundeyken yalniz sonuc
     alani guncellenir (DZ.screens.catalog.liveSearch); baska ekrandan ilk gecis input'u yerinde birakir (create({reuse}) ayni DOM
     dugumunu dondurur, catalog yalniz digerlerini siler) -> odak, imlec, deger ve TV ekran klavyesi korunur.
     Arama DISI bir ekranda (Ana Sayfa, Listem, ...) menu her kurulusta bos gelir ve saklanan sorgu silinir; detaydan arama sonuclarina
     donuste (active='search') sorgu korunur. */
  'use strict';var DZ=g.DZ=g.DZ||{};
  var DEBOUNCE_MS=450,MIN_QUERY=2,INPUT_COL=0,PLACEHOLDER='Film veya dizi ara',QUERY_KEY='dz.search.query';
  var liveBar=null,timer=null;
  function normalize(value){return String(value||'').replace(/\s+/g,' ').trim().slice(0,100);}
  function cancelPending(){if(timer){g.clearTimeout(timer);timer=null;}}
  function mounted(bar){return !!bar&&(bar.isConnected!==undefined?!!bar.isConnected:!!bar.parentNode);}
  function catalog(){return DZ.screens&&DZ.screens.catalog;}
  /* saklanan sorgu: dolu -> yaz, bos -> sil */
  function remember(query){if(query)DZ.store.set(QUERY_KEY,query);else if(DZ.store.del)DZ.store.del(QUERY_KEY);else DZ.store.set(QUERY_KEY,'');}
  /* arama bitti (arama disi ekrana gecildi): saklanan sorgu + arama ekraninin hafizasi silinir */
  function forget(){cancelPending();remember('');var view=catalog();if(view&&view.resetSearch)view.resetSearch();}
  /* opts.sort: katalog ekraninin siralamasi (ana ekran "Tümü" kartlari: trending | popular | new) */
  function go(view,opts){cancelPending();if(view==='home')DZ.app.go('home',{profile:DZ.api.profileId()},true);else{var p={view:view};if(opts&&opts.sort)p.sort=opts.sort;DZ.app.go('catalog',p,true);}}
  /* arama calistir: arama gorunumundeyse yerinde; degilse arama gorunumune gec (ust menu ayni dugum olarak kalir) */
  function run(query,immediate){
    remember(query);
    var view=catalog();
    if(view&&view.liveSearch&&view.liveSearch(query,{immediate:immediate,focusResults:immediate}))return true;
    /* keepBar: anlik nav konumu (kullanici debounce sirasinda ✕/Listem'e gectiyse gecisten sonra orada kalsin) */
    DZ.app.go('catalog',{view:'search',q:query,keepBar:(DZ.nav&&DZ.nav.snapshot&&DZ.nav.snapshot())||true,submit:!!immediate},true);
    return true;
  }
  /* Enter / Ara: bekleme yok. Android gibi 2 karakterden itibaren (yerel katalog; >= 3 ise canli kaynak da) */
  function openSearch(value){
    var query=normalize(value);
    if(query.length<MIN_QUERY)return false;
    cancelPending();
    return run(query,true);
  }
  /* her tus: onceki bekleyeni iptal et; yeterince uzunsa DEBOUNCE_MS sonra ara, kisaysa (arama gorunumunde) sonuclari temizle */
  function schedule(bar,value){
    cancelPending();
    var query=normalize(value),view=catalog();
    if(query.length<MIN_QUERY){
      if(view&&view.liveSearch&&view.liveSearch(query,{immediate:true}))remember(query);
      return;
    }
    timer=g.setTimeout(function(){timer=null;if(mounted(bar))run(query,false);},DEBOUNCE_MS);
  }
  function isTyping(){var d=g.document;return !!timer||!!(liveBar&&d&&d.activeElement&&d.activeElement===liveBar.dzInput);}
  function blur(){if(liveBar&&liveBar.dzInput&&liveBar.dzInput.blur){try{liveBar.dzInput.blur();}catch(e){}}}
  /* create(active, query, opts): opts.reuse = konteyner -> o konteynerde duran canli ust menu AYNEN dondurulur (yeniden kurulmaz);
     cagiran yalniz aktif sekmeyi gunceller, dugumu yeniden eklemez. `query` yalniz active='search' icin kullanilir. */
  function create(active,query,opts){
    if(opts&&opts.reuse&&liveBar&&liveBar.parentNode===opts.reuse){liveBar.dzSetActive(active);return liveBar;}
    cancelPending();
    if(active!=='search')forget();
    var bar=document.createElement('div');bar.className='topbar main-navigation';bar.setAttribute('data-nav-row','topbar');
    var search=null,listBtn=null,clearBtn=null,focused=false,invalid=false,ready=false,hadText=false;
    function paint(){
      if(listBtn)listBtn.className='tb-item'+(active==='mylist'?' selected':'');
      if(search)search.className='top-search'+(invalid?' invalid':'')+(focused?' focused':'')+(active==='search'?' selected':'');
    }
    /* logo = Ana Sayfa (yalniz fare/dokunma; kumanda odak duragi DEGIL: Geri tusu zaten ana sayfaya goturur). Ana sayfadaysa yalniz en uste (hero) doner. */
    function homeOrTop(){
      if(active==='home'){if(DZ.nav&&DZ.nav.focusRowById)DZ.nav.focusRowById('hero',0);return;}
      go('home');
    }
    var logo=document.createElement('div');logo.className='wordmark';logo.textContent='DIZIFLIX';
    logo.setAttribute('aria-label','Ana Sayfa');logo.addEventListener('click',homeOrTop,false);
    /* amblem (img/mascot-sm.png): yazinin SOLUNDA (CSS order:-1), dekoratif, data-nav DEGIL; tiklama amblem+yazi birlikte (logo dugumu). Yuklenemezse gizlenir. */
    var emblem=document.createElement('img');emblem.className='brand-emblem';emblem.src='img/mascot-sm.png';emblem.alt='';
    emblem.onerror=function(){emblem.className='brand-emblem hidden';};logo.appendChild(emblem);bar.appendChild(logo);
    function button(label,fn){var b=document.createElement('div');b.className='tb-item';b.setAttribute('data-nav','1');b.textContent=label;b.addEventListener('click',fn,false);bar.appendChild(b);return b;}
    search=document.createElement('div');
    var icon=document.createElement('span');icon.className='top-search-icon';icon.textContent='⌕';search.appendChild(icon);
    var input=document.createElement('input');input.className='top-search-input';input.type='search';input.inputMode='search';
    input.placeholder=PLACEHOLDER;input.setAttribute('aria-label',PLACEHOLDER);input.setAttribute('data-nav','1');
    input.value=active==='search'?String(query!==undefined?query:DZ.store.get(QUERY_KEY,'')||'').slice(0,100):'';
    /* ✕: yerel "x" (type=search cancel) TV'de odaklanamaz; kendi odaklanabilir dugmemiz yalniz input doluyken gorunur/gezilebilir */
    clearBtn=document.createElement('div');clearBtn.className='top-search-clear';clearBtn.setAttribute('data-nav','1');
    clearBtn.setAttribute('aria-label','Aramayı temizle');clearBtn.textContent='✕';
    function syncClear(){
      var has=input.value!=='';
      clearBtn.setAttribute('data-nav-off',has?'0':'1');if(clearBtn.style)clearBtn.style.display=has?'':'none';
      if(ready&&has!==hadText&&DZ.nav&&DZ.nav.refresh)DZ.nav.refresh();   /* gezilebilir ogeler degisti: nav yeniden toplasin */
      hadText=has;
    }
    function clearAll(){
      cancelPending();
      input.value='';invalid=false;input.placeholder=PLACEHOLDER;paint();syncClear();
      var view=catalog();if(view&&view.liveSearch)view.liveSearch('',{immediate:true});   /* arama gorunumundeyse sonuclar + durum sifirlanir */
      remember('');
      if(DZ.nav&&DZ.nav.focusRowById)DZ.nav.focusRowById('topbar',INPUT_COL);
      if(g.document&&g.document.activeElement!==input&&input.focus)input.focus();
    }
    clearBtn.addEventListener('click',clearAll,false);
    function submitSearch(){
      if(!openSearch(input.value)){invalid=true;input.placeholder='En az '+MIN_QUERY+' harf yazın';paint();return false;}
      return true;
    }
    input.dzSubmitSearch=submitSearch;
    input.addEventListener('keydown',function(ev){if(ev.metaKey||ev.ctrlKey||ev.altKey)return;var code=ev.keyCode||ev.which;
      if(code!==13&&code!==65376&&ev.key!=='Enter'&&ev.key!=='Search'&&ev.key!=='Done'&&ev.key!=='Go')return;
      if(ev.preventDefault)ev.preventDefault();if(ev.stopPropagation)ev.stopPropagation();
      submitSearch();
    },false);
    /* type=search'un yerel "x" dugmesi gizli (CSS) ama bos deger ile 'search' olayi yine gelebilir: uyari degil, temizleme */
    input.addEventListener('search',function(){if(!normalize(input.value)){schedule(bar,'');return;}submitSearch();},false);
    input.addEventListener('focus',function(){focused=true;paint();},false);
    input.addEventListener('blur',function(){focused=false;paint();},false);
    input.addEventListener('input',function(){invalid=false;input.placeholder=PLACEHOLDER;paint();syncClear();schedule(bar,input.value);},false);
    search.appendChild(input);search.appendChild(clearBtn);bar.appendChild(search);
    listBtn=button('Listem',function(){if(active!=='mylist')go('mylist');});
    var space=document.createElement('div');space.className='spacer';bar.appendChild(space);
    /* arama disi ekrana gecis: kutu bosalir, saklanan sorgu + arama hafizasi silinir (donuste arama bos gelir) */
    function leave(screen){input.value='';invalid=false;syncClear();forget();DZ.app.go(screen);}
    button('Profil',function(){leave('profiles');});button('Ayarlar',function(){leave('settings');});
    bar.dzInput=input;bar.dzClear=clearBtn;bar.dzLogo=logo;bar.dzSetActive=function(next){active=next;paint();};
    paint();syncClear();ready=true;liveBar=bar;return bar;
  }
  /* Ayarlar / Profil ekranlari icin fare-dokunma "← Geri": Geri tusuyla AYNI islem (DZ.app.goBack); kumanda odak sirasinda DEGIL
     (data-nav yok). Gidilecek yer yoksa (yigin tek ekran, ornegin ilk acilis profil secimi) null -> dugme gosterilmez. */
  function backButton(){
    if(!DZ.app||!DZ.app.canGoBack||!DZ.app.canGoBack())return null;
    var b=document.createElement('div');b.className='back-btn';b.textContent='← Geri';b.setAttribute('aria-label','Geri');
    b.addEventListener('click',function(){DZ.app.goBack();},false);
    return b;
  }
  DZ.navigation={create:create,backButton:backButton,go:go,search:openSearch,isTyping:isTyping,blur:blur,DEBOUNCE_MS:DEBOUNCE_MS,MIN_QUERY:MIN_QUERY,INPUT_COL:INPUT_COL};
})(window);
