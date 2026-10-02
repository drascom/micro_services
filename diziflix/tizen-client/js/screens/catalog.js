(function(g){
  'use strict';var DZ=g.DZ=g.DZ||{},state=null,container=null,memory={};
  var MIN_LOCAL=2,MIN_REMOTE=3,REMOTE_NOTE='Canlı kaynak şu an yanıt vermedi, yerel sonuçlar gösteriliyor',EMPTY_HINT='Aramak için bir film veya dizi adı girin.';
  function el(tag,cls,text){var n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;}
  function button(text,fn){var n=el('div','btn small',text);n.setAttribute('data-nav','1');n.addEventListener('click',fn,false);return n;}
  function searchWait(){var n=el('div','catalog-loading search-loading');n.appendChild(el('span','catalog-spinner'));n.appendChild(el('span','catalog-loading-label','Aranıyor…'));return n;}
  function live(current){return state===current&&current.alive;}
  function pick(key,title,options){var current=state;DZ.modal.open({title:title,vertical:true,buttons:options.map(function(o){return {label:o.name,value:o.id,primary:String(current.saved[key])===String(o.id)};}),onDone:function(value){if(!live(current))return;current.saved[key]=value;current.saved.offset=0;current.saved.focus=null;load();}});}
  function label(value,options,fallback){for(var i=0;i<options.length;i++)if(String(options[i].id)===String(value))return options[i].name;return fallback;}
  var available=[{id:'',name:'Tümü'},{id:'ready',name:'İzlenebilir'},{id:'check_required',name:'Kontrol gereken'},{id:'unavailable',name:'İzleme kaynağı yok'}];
  var order=[{id:'new',name:'Yeni eklenen'},{id:'year',name:'Yıl'},{id:'title',name:'Ad'},{id:'trending',name:'Haftanın trendleri'},{id:'popular',name:'Dikkate değer'}];
  /* ana ekran "Tümü" kartlari sort=trending|popular ile acar (sunucu /api/catalog bu degerleri destekler); baslik siralamaya uyar */
  function pageTitle(c){var s=c.saved&&c.saved.sort,kind=c.view==='movies'?'Filmler':c.view==='series'?'Diziler':'';
    if(kind&&s==='trending')return 'Haftanın Trendleri · '+kind;
    if(kind&&s==='popular')return 'Dikkate Değer '+kind;
    return c.title;}
  /* arama sonucu kaynak etiketleri: ilk 2 + "+N"; broken = uyari isareti, unknown = soluk */
  function sourceTags(list){var box=el('div','catalog-sources');
    list.slice(0,2).forEach(function(o){var st=o&&o.status,name=(o&&(o.name||o.site))||'';
      box.appendChild(el('span','src-tag'+(st==='broken'?' broken':st==='unknown'?' unknown':''),(st==='broken'?'⚠ ':'')+name));});
    if(list.length>2)box.appendChild(el('span','src-tag more','+'+(list.length-2)));
    return box;}
  /* /api/search kokundaki remote_sites: yanit vermeyen (ok:false, skipped degil) siteler; ad icin sonuclardaki source_options.name, yoksa site kimligi */
  function failedSites(data,items){var rs=data&&data.remote_sites;if(!rs||typeof rs!=='object')return [];var names={};
    (items||[]).forEach(function(it){(it.source_options||[]).forEach(function(o){if(o&&o.site&&o.name)names[o.site]=o.name;});});
    return Object.keys(rs).filter(function(k){var r=rs[k];return r&&r.ok===false&&!r.skipped;}).map(function(k){return names[k]||k;});}
  function someSiteOk(data){var rs=data&&data.remote_sites;return !!rs&&typeof rs==='object'&&Object.keys(rs).some(function(k){return rs[k]&&rs[k].ok===true;});}
  function inputCol(){return DZ.navigation.INPUT_COL>0?DZ.navigation.INPUT_COL:0;}   /* arama kutusunun topbar satirindaki sutunu */
  function clearNode(n){while(n.firstChild)n.removeChild(n.firstChild);}
  /* konteynerdeki her sey silinir, yalniz `keep` (canli ust menu) yerinde kalir: input DOM'dan cikmaz -> odak/imlec/ekran klavyesi korunur */
  function emptyContainer(keep){var kids=Array.prototype.slice.call(container.children||[]);for(var i=0;i<kids.length;i++)if(kids[i]!==keep)container.removeChild(kids[i]);}
  /* 5'erli poster satirlari; current.cards doldurulur */
  function gridRows(current,page,items){current.cards=[];
    for(var start=0;start<items.length;start+=5){var line=el('div','catalog-grid-row');line.setAttribute('data-nav-row','grid_'+Math.floor(start/5));
      items.slice(start,start+5).forEach(function(item){var tile=el('div','catalog-tile');var card=DZ.card.create(item,{portrait:true,onSelect:function(it){current.saved.focus=it.id;DZ.app.go('detail',{id:it.id});}});tile.appendChild(card);tile.appendChild(el('div','catalog-title',item.title));
        var status=item.availability||{};var note=(item.year||'Yıl bilinmiyor')+' · '+(item.type==='series'?'Dizi':'Film');
        tile.appendChild(el('div','catalog-meta',note));if(status.state==='unavailable')tile.appendChild(el('div','catalog-status',current.view==='search'&&item.type==='series'?'Açınca bölümler yüklenecek':status.has_trailer?'Yalnızca fragman':'İzleme kaynağı yok'));else if(status.state==='check_required')tile.appendChild(el('div','catalog-status','Kaynak kontrol ediliyor'));
        if(item.source_options&&item.source_options.length)tile.appendChild(sourceTags(item.source_options));
        line.appendChild(tile);current.cards.push(card);});page.appendChild(line);}
  }
  function windowImages(current,row){current.cards.forEach(function(card,i){if(Math.abs(Math.floor(i/5)-row)<=1){if(card.dzLoadImage)card.dzLoadImage();}else if(card.dzUnloadImage)card.dzUnloadImage();});}
  /* ---------- film / dizi / listem (arama DEGIL) ---------- */
  function render(data){var current=state,s=current.saved;container.innerHTML='';container.appendChild(DZ.navigation.create(current.view,s.q));
    var page=el('div','catalog-page');page.id='page';var head=el('div','catalog-heading');head.appendChild(el('h1',null,pageTitle(current)));
    head.appendChild(el('p',null,data.total+' yapım'+(s.q?' · “'+s.q+'”':'')));page.appendChild(head);
    var filters=el('div','catalog-filters');filters.setAttribute('data-nav-row','filters');
    var genres=[{id:'',name:'Tümü'}].concat(data.genres||[]),years=[{id:'',name:'Tümü'}].concat((data.years||[]).map(function(y){return {id:y,name:String(y)};}));
    filters.appendChild(button('Tür: '+label(s.genre,genres,'Tümü'),function(){pick('genre','Tür seçimi',genres);}));
    filters.appendChild(button('Yıl: '+(s.year||'Tümü'),function(){pick('year','Yıl seçimi',years);}));
    filters.appendChild(button(label(s.availability,available,'Tümü'),function(){pick('availability','İzlenebilirlik',available);}));
    filters.appendChild(button('Sıra: '+label(s.sort,order,'Yeni eklenen'),function(){pick('sort','Sıralama',order);}));
    filters.appendChild(button('Filtreleri temizle',function(){Object.assign(s,{genre:'',year:'',availability:'',sort:'new',q:'',offset:0,focus:null});load();}));page.appendChild(filters);
    current.cards=[];
    if(!data.items.length)page.appendChild(el('div','catalog-empty','Bu filtrelerde içerik bulunamadı.'));
    gridRows(current,page,data.items);
    var pagination=el('div','catalog-pagination');pagination.setAttribute('data-nav-row','pagination');
    if(s.offset>0)pagination.appendChild(button('Önceki sayfa',function(){s.offset=Math.max(0,s.offset-20);s.focus=null;load();}));
    if(data.total)pagination.appendChild(el('span','catalog-meta',(s.offset+1)+'–'+Math.min(s.offset+20,data.total)+' / '+data.total));
    if(s.offset+20<data.total)pagination.appendChild(button('Sonraki sayfa',function(){s.offset+=20;s.focus=null;load();}));page.appendChild(pagination);container.appendChild(page);
    g.requestAnimationFrame(function(){if(!live(current))return;DZ.nav.setRoot(container,page);DZ.nav.onFocus(focusHandler(current));
      var found=data.items.findIndex(function(i){return i.id===s.focus;});if(found>=0)DZ.nav.focusRowById('grid_'+Math.floor(found/5),found%5);else if(data.items.length)DZ.nav.focusRowById('grid_0',0);else DZ.nav.focusRowById('filters',0);});
  }
  function load(){var current=state,version=++current.version;container.innerHTML='';container.appendChild(DZ.navigation.create(current.view,current.saved.q));var box=el('div','catalog-loading','Katalog yükleniyor…');container.appendChild(box);
    DZ.nav.setRoot(container,null);
    var query=Object.assign({},current.saved,{profile:current.profile,type:current.view==='movies'?'movie':current.view==='series'?'series':'',mine:current.view==='mylist',limit:20});delete query.focus;
    function failed(error){if(!live(current)||current.version!==version)return;box.textContent=error.message;var actions=el('div');actions.setAttribute('data-nav-row','retry');actions.appendChild(button('Tekrar dene',load));box.appendChild(actions);DZ.nav.setRoot(container,null);DZ.nav.focusRowById('retry',0);}
    DZ.api.catalog(query).then(function(data){if(live(current)&&current.version===version)render(data);},failed);
  }
  /* ---------- arama (yazarken): bar + sayfa bir kez kurulur, sonraki her sorguda yalniz sayfanin ICERIGI degisir ---------- */
  function focusHandler(current){return function(info){
    if(current.search&&info.why==='move')current.search.focusResults=false;   /* kullanici kendi gezindi: sonuc gelince odagi calma */
    if(info.rowId.indexOf('grid_')===0)windowImages(current,Number(info.rowId.slice(5)));
    else if(current.view==='search'&&info.rowId==='topbar')windowImages(current,0);
  };}
  function abortPending(se){if(se.controller){try{se.controller.abort();}catch(e){}se.controller=null;}}
  function searchHeading(current,data){var s=current.saved,head=el('div','catalog-heading');head.appendChild(el('h1',null,current.title));
    if(!data.loading&&!data.idle){var text=data.total+' yapım'+(s.q?' · “'+s.q+'”':'');
      if(data.searching)text+=' · Kaynakta aranıyor…';
      if(data.remote_error&&!someSiteOk(data))text+=' · '+REMOTE_NOTE;   /* bazi siteler yanit verdiyse: asagidaki "Bazi kaynaklar yanit vermedi" satiri */
      head.appendChild(el('p',null,text));}
    return head;}
  /* sonuc odagi: input'u birak (yoksa sag/sol tuslari metin imlecine gider), ilk (ya da kayitli) karta in */
  function focusResults(current){var se=current.search,s=current.saved;if(!current.cards.length)return false;
    se.focusResults=false;if(DZ.navigation.blur)DZ.navigation.blur();
    var found=-1;current.cards.forEach(function(card,i){var id=card.getAttribute&&card.getAttribute('data-item-id');if(found<0&&s.focus&&id===s.focus)found=i;});
    if(found<0)found=0;DZ.nav.focusRowById('grid_'+Math.floor(found/5),found%5);return true;}
  function paintSearch(current,data){
    var se=current.search,s=current.saved,page=current.page,items=data.items||[],snap=DZ.nav.snapshot?DZ.nav.snapshot():null;
    clearNode(page);page.appendChild(searchHeading(current,data));current.cards=[];
    if(data.error){var box=el('div','catalog-loading',data.error);var actions=el('div');actions.setAttribute('data-nav-row','retry');
      actions.appendChild(button('Tekrar dene',function(){if(live(current))runSearch(current,s.q,{force:true,immediate:true,focusResults:true});}));box.appendChild(actions);page.appendChild(box);}
    else if(!items.length){if(data.loading||data.searching)page.appendChild(searchWait());else page.appendChild(el('div','catalog-empty',s.q.length<MIN_LOCAL?EMPTY_HINT:'Bu filtrelerde içerik bulunamadı.'));}
    else gridRows(current,page,items);
    var down=data.error?[]:failedSites(data,items);
    if(down.length)page.appendChild(el('div','catalog-sources-note','Bazı kaynaklar yanıt vermedi: '+down.join(', ')));
    /* odak: input'tayken (yaziyorken) yerinde kalir; sonuc kartlarindayken ayni karta geri doner; Enter/ilk giriste ilk karta iner */
    var keep=snap&&snap.rowId&&snap.rowId!=='topbar'?snap:null,typing=!!(DZ.navigation.isTyping&&DZ.navigation.isTyping());
    if(keep)DZ.nav.restoreNext(keep);
    DZ.nav.refresh();
    if(keep)DZ.nav.restoreNext(null);
    var now=DZ.nav.current(),inBar=!now||now.rowId==='topbar';
    if(se.focusResults&&items.length){   /* refresh() kayitli kartı zaten geri yukladiysa (detaydan donus) ona dokunma */
      if(!inBar){se.focusResults=false;if(DZ.navigation.blur)DZ.navigation.blur();}else focusResults(current);
      return;}
    if(typing){if(!inBar)DZ.nav.focusRowById('topbar',inputCol());}
    else if(inBar&&(!now||now.col<=inputCol())&&!items.length&&!data.searching&&!data.loading&&!data.error)DZ.nav.focusRowById('topbar',inputCol());
  }
  /* Android SearchViewModel ile ayni: < 2 karakter temizle; >= 2 yerel katalog; >= 3 ayrica canli /api/search.
     Her yeni arama oncekini iptal eder (AbortController varsa) ve sira numarasi ile bayat yaniti yok sayar. */
  function runSearch(current,q,opts){
    var o=opts||{},se=current.search,s=current.saved;
    if(!o.force){
      var idle=q.length<MIN_LOCAL;
      if(idle&&se.phase==='idle'&&se.q===q)return;
      if(!idle&&se.q===q&&(se.phase==='local'||se.phase==='remote'||(se.phase==='done'&&!(o.immediate&&se.note)))){   /* ayni sorgu zaten calisiyor/bitti */
        if(o.focusResults){se.focusResults=true;focusResults(current);}
        return;
      }
    }
    var token=++se.token;abortPending(se);
    if(!o.initial)s.focus=null;
    s.q=q;s.offset=0;se.q=q;se.note=false;se.local=null;se.localError=null;se.remoteError=null;se.remoteDone=false;se.focusResults=!!o.focusResults;
    if(DZ.app&&DZ.app.updateParams)DZ.app.updateParams({q:q});   /* detaydan donunce son sorgu gorunsun */
    if(q.length<MIN_LOCAL){se.phase='idle';paintSearch(current,{items:[],total:0,idle:true});return;}
    se.phase='local';
    if(!current.cards.length)paintSearch(current,{items:[],total:0,loading:true});   /* eski sonuc varsa yenisi gelene dek kalir */
    var remote=q.length>=MIN_REMOTE,opt;
    if(typeof g.AbortController==='function'){se.controller=new g.AbortController();opt={signal:se.controller.signal};}
    function fresh(){return live(current)&&se.token===token;}
    var query=Object.assign({},s,{profile:current.profile,type:'',mine:false,limit:20});delete query.focus;
    DZ.api.catalog(query,opt).then(function(local){if(!fresh())return;se.local=local;if(se.remoteDone)return;
      if(remote){if(se.remoteError){se.phase='done';se.note=true;local.searching=false;local.remote_error=REMOTE_NOTE;}else{se.phase='remote';local.searching=true;}}else se.phase='done';
      paintSearch(current,local);
    },function(error){if(!fresh())return;se.localError=error;if(!remote||se.remoteError){se.phase='error';paintSearch(current,{items:[],total:0,error:error.message});}});
    if(remote)DZ.api.search(q,current.profile,20,opt).then(function(data){if(!fresh())return;se.remoteDone=true;se.phase='done';if(data.remote_error)se.note=true;paintSearch(current,data);},
      function(error){if(!fresh())return;se.remoteError=error;
        if(se.local){se.phase='done';se.note=true;se.local.searching=false;se.local.remote_error=REMOTE_NOTE;paintSearch(current,se.local);}
        else if(se.localError){se.phase='error';paintSearch(current,{items:[],total:0,error:error.message});}});
  }
  function startSearch(current,o){
    var bar=DZ.navigation.create('search',current.saved.q,o.keep?{reuse:container}:null);
    emptyContainer(bar);if(bar.parentNode!==container)container.appendChild(bar);
    var page=el('div','catalog-page');page.id='page';container.appendChild(page);current.page=page;
    DZ.nav.setRoot(container,page);DZ.nav.onFocus(focusHandler(current));
    if(o.keep){DZ.nav.restoreNext(null);   /* yaziyorsa input'ta kal; debounce sirasinda ust menude baska ogeye gectiyse orada kal */
      if(DZ.navigation.isTyping&&DZ.navigation.isTyping())DZ.nav.focusRowById('topbar',inputCol());
      else if(o.keepFocus&&o.keepFocus.rowId==='topbar')DZ.nav.focusRowById('topbar',o.keepFocus.col);}
    runSearch(current,current.saved.q,{initial:true,force:true,focusResults:o.focusResults});
    if(!o.keep&&current.saved.q.length<MIN_LOCAL)DZ.nav.focusRowById('topbar',inputCol());
  }
  DZ.screens=DZ.screens||{};DZ.screens.catalog={name:'catalog',enter:function(cnt,params,isResume){container=cnt;var view=(params&&params.view)||'movies',profile=DZ.api.profileId(),key=DZ.api.baseUrl()+':'+profile+':'+view;
    var saved=memory[key]||(memory[key]={genre:'',year:'',availability:'',sort:'new',q:'',offset:0,focus:null});if(view==='search'&&params&&params.q!==undefined){saved.q=String(params.q).trim().slice(0,100);saved.offset=0;saved.focus=null;}
    var keep=view==='search'&&!isResume&&!!(params&&params.keepBar),keepFocus=keep&&typeof params.keepBar==='object'?params.keepBar:null,submit=view==='search'&&!isResume&&!!(params&&params.submit);
    /* ana ekran "Tümü" kartlari: sort (trending|popular|new) yalniz ilk giriste uygulanir (geri donuste liste/odak/sayfa korunur) */
    if(view!=='search'&&!isResume&&params&&params.sort!==undefined&&params.sort!==null&&params.sort!==''){saved.sort=String(params.sort);saved.offset=0;saved.focus=null;}
    if(params){delete params.keepBar;delete params.submit;delete params.sort;}   /* tek kullanimlik; geri donuste (resume) yigin girdisinde kalmasin */
    state={alive:true,version:0,view:view,title:({movies:'Filmler',series:'Diziler',search:'Arama Sonuçları',mylist:'Listem'})[view]||'Katalog',profile:profile,saved:saved,cards:[],page:null,
      search:view==='search'?{token:0,q:null,phase:'idle',controller:null,local:null,localError:null,remoteError:null,remoteDone:false,note:false,focusResults:false}:null};
    if(view==='search')startSearch(state,{keep:keep,keepFocus:keepFocus,focusResults:keep?submit:true});else load();},
    /* yazarken arama: arama gorunumu ekrandaysa yerinde gunceller (true); degilse false (cagiran ekrana gecer) */
    liveSearch:function(q,opts){var current=state;if(!current||!current.alive||current.view!=='search'||!current.search||!current.page||!container)return false;
      var o=opts||{};runSearch(current,String(q||''),{immediate:!!o.immediate,focusResults:!!o.focusResults});return true;},
    /* arama disi bir ekrana gecildi (Ana Sayfa, Listem, ...): hafizadaki arama sorgusu/odagi silinir (detaydan donuste cagrilmaz) */
    resetSearch:function(){Object.keys(memory).forEach(function(k){if(/:search$/.test(k)){memory[k].q='';memory[k].offset=0;memory[k].focus=null;}});
      if(state&&state.view==='search'&&DZ.app&&DZ.app.updateParams)DZ.app.updateParams({q:''});},   /* Ayarlar/Profil'e gidip donunce arama bos gelir */
    back:function(){DZ.navigation.go('home');return true;},key:function(ev){if(ev.name==='red'){DZ.nav.focusRowById('topbar',inputCol());return true;}return false;},
    exit:function(){if(state){state.alive=false;if(state.search){state.search.token++;abortPending(state.search);}}state=null;container=null;}};
})(window);
