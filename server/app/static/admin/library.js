/* Kütüphane görünümü (Siteler › site › Kütüphane alt sekmesinde; dzLib.mount) + üst sekme geçişi: /api/ops/library (sunucu tarafı
   süzgeç + sayfalama). Öğeye tıklama yapım yan panelini açar (media.js window.dzOpenMedia). K/T = kırık / toplam video kaynağı;
   ?N = şüpheli (K'ya dahil değil). */
(function(){
'use strict';
var $=function(i){return document.getElementById(i)};
var PAGE=50;
var S={sites:[],broken:'',kind:'',tmdb:'',q:'',id:'',sort:'recent',view:'grid'};
var fixedSite=null;
var items=[],total=0,libTotal=0,facets=null,mode='',hasMore=false,loaded=false,failed=false;
var seq=0,busy=false,selId=null,loadedAt=0,qTimer=null;

function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
function T(t){return t?new Date(t).getTime():0}
function ago(t){if(!T(t))return '-';var d=Math.max(0,(Date.now()-T(t))/1000);
  if(d<60)return Math.floor(d)+' sn önce';if(d<3600)return Math.floor(d/60)+' dk önce';
  if(d<86400)return Math.floor(d/3600)+' sa önce';return Math.floor(d/86400)+' g önce'}
function full(t){return T(t)?new Date(t).toLocaleString('tr-TR'):'-'}
function num(n){return n==null?'-':(+n).toLocaleString('tr-TR')}
function api(path){return fetch(path).then(function(r){return r.json().then(function(j){if(!r.ok)throw new Error((j&&j.error&&j.error.message)||r.status);return j})})}
function store(k,v){try{if(v===undefined)return localStorage.getItem(k);localStorage.setItem(k,v)}catch(e){return null}}

var BROKEN=[['','Hepsi','all'],['ok','Hepsi sağlam','ok'],['partial','Kısmen kırık','partial'],['dead','Tamamen kırık','dead'],['suspect','Şüpheli var','suspect'],['none','Video kaynağı yok','none']];
var KIND=[['','Hepsi',null],['movie','Film','movie'],['series','Dizi','series']];
var TMDB=[['','Hepsi',null],['matched','Eşleşti','matched'],['review','İnceleme','review'],['unmatched','Eşleşmedi','unmatched']];
var TMDBL={matched:['ok','TMDB ✓'],review:['warn','TMDB inceleme'],unmatched:['','TMDB yok']};

/* ---- sekmeler ---- */
var TABS=['onboard','errors','live','settings'];
var TITLES={onboard:'Siteler',errors:'Hatalar',live:'Canlı TV',settings:'Ayarlar'};
/* eski sekmeler Ayarlar alt bölümü oldu: #categories, #backgrounds, #settings/<bölüm> */
var ALIAS={categories:'categories',backgrounds:'look'};
function showTab(name){
  name=String(name||'');
  var m=name.match(/^settings\/(\w+)$/);
  if(m){window.dzSetSecPending=m[1];name='settings'}
  else if(ALIAS[name]){window.dzSetSecPending=ALIAS[name];name='settings'}
  if(TABS.indexOf(name)<0)name='onboard';
  TABS.forEach(function(n){
    $('tab-'+n).classList.toggle('hidden',n!==name);
    $('tab-btn-'+n).setAttribute('aria-selected',String(n===name));
  });
  $('ttl').textContent=TITLES[name];
  try{if(name!=='settings')history.replaceState(null,'',name==='onboard'?location.pathname+location.search:'#'+name)}catch(e){}
  var hooks=window.dzTabHooks||{};if(hooks[name])hooks[name]();
}
document.querySelector('.tabs').addEventListener('click',function(ev){
  var b=ev.target.closest('button[data-tab]');if(b)showTab(b.getAttribute('data-tab'));
});

/* ---- yerleştirme: Siteler akordiyonu görünümü bir panele taşır (site = sabit süzgeç; null = tüm siteler) ---- */
function mount(container,site,opt){
  opt=opt||{};
  var node=$('lib-layout');if(!node||!container)return;
  if(node.parentNode!==container)container.appendChild(node);
  var same=fixedSite===(site||null)&&!opt.id&&!opt.q&&loaded&&!S.id;
  fixedSite=site||null;
  if(same&&Date.now()-loadedAt<30000)return;
  S.sites=fixedSite?[fixedSite]:[];S.broken=S.kind=S.tmdb='';
  S.id=opt.id||'';S.q=opt.id?'':(opt.q||'');$('lq').value=S.q;selId=null;
  load(true);
}
function park(){var node=$('lib-layout'),p=$('lib-park');if(node&&p&&node.parentNode!==p)p.appendChild(node)}
window.dzLib={mount:mount,park:park};

/* ---- sorgu ---- */
function enc(v){return encodeURIComponent(v)}
function qs(offset,withFacets){
  var p=[];
  S.sites.forEach(function(s){p.push('site='+enc(s))});
  if(S.broken)p.push('broken='+S.broken);
  if(S.kind)p.push('kind='+S.kind);
  if(S.tmdb)p.push('tmdb='+S.tmdb);
  if(S.q)p.push('q='+enc(S.q));
  if(S.id)p.push('id='+enc(S.id));
  p.push('sort='+S.sort,'limit='+PAGE,'offset='+offset);
  if(!withFacets)p.push('facets=0');
  return p.join('&');
}
function dirty(){return !!((!fixedSite&&S.sites.length)||S.broken||S.kind||S.tmdb||S.q||S.id)}
function load(reset){
  var my=++seq;
  if(reset){items=[];hasMore=false;loaded=false;failed=false;renderItems(false);renderMeta()}
  busy=true;$('lmore').disabled=true;
  var offset=reset?0:items.length;
  return api('/api/ops/library?'+qs(offset,reset)).then(function(d){
    if(my!==seq)return;
    busy=false;loaded=true;failed=false;loadedAt=Date.now();
    total=d.total;libTotal=d.library_total;mode=d.source_mode;hasMore=!!d.has_more;
    if(d.facets)facets=d.facets;
    var list=d.items||[];
    if(reset){items=list;renderFilters();renderItems(false)}
    else{
      var seen={};items.forEach(function(x){seen[x.id]=1});
      list=list.filter(function(x){return !seen[x.id]});
      items=items.concat(list);renderItems(true,list);
    }
    renderMeta();
  }).catch(function(e){
    if(my!==seq)return;
    busy=false;loaded=true;failed=String(e&&e.message||e);renderMeta();renderItems(false);
  });
}

/* ---- süzgeç çipleri ---- */
function chip(attr,val,label,count,pressed){
  return '<button class="ch'+(count===0&&!pressed?' zero':'')+'" '+attr+'="'+esc(val)+'" aria-pressed="'+pressed+'">'+esc(label)+(count==null?'':' <b>'+num(count)+'</b>')+'</button>';
}
function group(label,body){return '<div class="fg"><span class="fl">'+esc(label)+'</span>'+body+'</div>'}
function renderFilters(){
  if(!facets){$('lfilters').innerHTML='';return}
  var f=facets;
  var sites=fixedSite?'':(f.sites||[]).map(function(s){return chip('data-site',s.site,s.site,s.count,S.sites.indexOf(s.site)>=0)}).join('');
  var b=BROKEN.map(function(x){return chip('data-broken',x[0],x[1],f.broken[x[2]],S.broken===x[0])}).join('');
  var k=KIND.map(function(x){return chip('data-kind',x[0],x[1],x[2]?f.kind[x[2]]:null,S.kind===x[0])}).join('');
  var t=TMDB.map(function(x){return chip('data-tmdb',x[0],x[1],x[2]?f.tmdb[x[2]]:null,S.tmdb===x[0])}).join('');
  $('lfilters').innerHTML=
    (sites?group('Kaynak',sites):'')+group('Video',b)+group('Tür',k)+group('TMDB',t);
}

/* ---- rozet ---- */
function kt(it,inert){
  var v=it.videos,sus=v.suspect?'<span class="sus" title="'+v.suspect+' şüpheli (K\'ya dahil değil)">?'+v.suspect+'</span>':'';
  var tip=v.total?(v.broken+' kırık / '+v.total+' video kaynağı'+(v.trailers?' ('+v.trailers+' fragman)':'')+(v.suspect?' · '+v.suspect+' şüpheli':'')):'Video kaynağı yok';
  if(inert)return '<span class="kt '+v.state+'" title="'+esc(tip)+'" style="cursor:default;box-shadow:none">'+sus+esc(v.label)+'</span>';
  return '<button class="kt '+v.state+'" data-open="'+esc(it.id)+'" title="'+esc(tip+' — kaynakları göster')+'" aria-label="'+esc(tip)+'">'+sus+esc(v.label)+'</button>';
}
function ph(title){return '<span class="ph" aria-hidden="true">'+esc((title||'?').trim().charAt(0).toUpperCase())+'</span>'}
function img(src,alt){return src?'<img src="'+esc(src)+'" alt="'+esc(alt||'')+'" loading="lazy" decoding="async">':''}
function kindL(it){return it.type==='series'?'Dizi':'Film'}
function srcTags(it){return (it.sources||[]).map(function(s){return '<span class="tag">'+esc(s)+'</span>'}).join('')}
function tmdbTag(it){var t=TMDBL[it.tmdb]||['',it.tmdb];return '<span class="tag '+t[0]+'">'+esc(t[1])+'</span>'}
function imdbTag(it){return it.imdb_id?'<span class="mono" style="font-size:11.5px">'+esc(it.imdb_id)+'</span>':''}

/* ---- liste ---- */
function cardHtml(it){
  var cur=selId===it.id?' aria-current="true"':'';
  if(S.view==='list'){
    return '<div class="lrow" data-open="'+esc(it.id)+'"'+cur+'><div class="thumb">'+ph(it.title)+img(it.poster,'')+'</div>'+
      '<div class="mid"><span class="ti" title="'+esc(it.title)+'">'+esc(it.title)+'</span>'+
      '<span class="me"><span class="num">'+esc(it.year||'-')+'</span><span>'+kindL(it)+'</span>'+srcTags(it)+tmdbTag(it)+imdbTag(it)+'</span></div>'+
      kt(it)+'</div>';
  }
  return '<article class="lcard" data-open="'+esc(it.id)+'"'+cur+'><div class="poster">'+ph(it.title)+img(it.poster,'')+kt(it)+'</div>'+
    '<div class="body"><span class="ti" title="'+esc(it.title)+'">'+esc(it.title)+'</span>'+
    '<span class="me"><span class="num">'+esc(it.year||'-')+'</span><span>'+kindL(it)+'</span>'+tmdbTag(it)+'</span>'+
    '<span class="me">'+srcTags(it)+'</span></div></article>';
}
function renderItems(append,list){
  var el=$('litems');
  el.className=S.view==='list'?'llist':'lgrid';
  if(append){el.insertAdjacentHTML('beforeend',(list||[]).map(cardHtml).join(''));return}
  if(!loaded){el.innerHTML='<div class="empty" style="grid-column:1/-1">Yükleniyor…</div>';return}
  if(failed){el.innerHTML='<div class="empty err" style="grid-column:1/-1">Yüklenemedi: '+esc(failed)+'</div>';return}
  if(!items.length){
    var msg=libTotal===0?'Henüz veri yok.':fixedSite&&!dirty()?'Bu siteden yapım yok.':'Bu süzgeçle eşleşen öğe yok.';
    var hint=libTotal===0?(mode!=='library'?'<br><small>Kaynak modu: <b>'+esc(mode)+'</b>. Kütüphane için <span class="mono">SOURCE=library</span> ve en az bir tarama gerekir.</small>':'<br><small>İlk taramadan sonra öğeler burada görünür.</small>'):'';
    el.innerHTML='<div class="empty" style="grid-column:1/-1">'+msg+hint+'</div>';return;
  }
  el.innerHTML=items.map(cardHtml).join('');
}
function renderMeta(){
  var m=$('lmeta');
  if(!loaded){m.innerHTML='';$('lmorew').style.display='none';return}
  var h='';
  if(!failed){
    h=(S.id?'<span class="tag">tek yapım: <span class="mono">'+esc(S.id)+'</span></span>':'')+'<span><b>'+num(total)+'</b> öğe'+(dirty()&&libTotal?' <span>(kütüphanede toplam '+num(libTotal)+')</span>':'')+'</span>';
    if(items.length&&items.length<total)h+='<span>'+num(items.length)+' gösteriliyor</span>';
    if(dirty())h+='<button class="link" id="lreset">Süzgeçleri temizle</button>';
    if(libTotal&&mode!=='library')h+='<span>Kaynak modu: '+esc(mode)+' (görseller yer tutucu olabilir)</span>';
    if(libTotal)h+='<span class="lg"><b>K/T</b> = kırık / toplam video kaynağı (film, bölüm, fragman; devre dışı olanlar sayılmaz). <span class="mono">?N</span> = şüpheli sayısı, K\'ya dahil değil. Renk: yeşil hepsi sağlam · sarı kısmi kırık · kırmızı hepsi kırık · gri kaynak yok.</span>';
  }
  m.innerHTML=h;
  $('lmorew').style.display=hasMore&&!failed?'block':'none';
  $('lmore').disabled=busy;
  $('lmore').textContent='Daha fazla ('+num(Math.max(0,total-items.length))+' kaldı)';
}

/* ---- yapım yan paneli (media.js) ---- */
function find(id){return items.filter(function(x){return x.id===id})[0]}
function openItem(id){
  selId=id;
  var prev=document.querySelector('#litems [aria-current]');if(prev)prev.removeAttribute('aria-current');
  var cur=document.querySelector('#litems [data-open="'+(window.CSS&&CSS.escape?CSS.escape(id):id)+'"]');
  var card=cur&&cur.closest('.lcard,.lrow');if(card)card.setAttribute('aria-current','true');
  var it=find(id)||{};
  if(window.dzOpenMedia)window.dzOpenMedia({id:id,title:it.title||'',site:fixedSite||''});
}

/* ---- etkileşim ---- */
$('lfilters').addEventListener('click',function(ev){
  var b=ev.target.closest('button.ch');if(!b)return;
  if(b.hasAttribute('data-site')){
    var s=b.getAttribute('data-site'),i=S.sites.indexOf(s);
    if(i>=0)S.sites.splice(i,1);else S.sites.push(s);
  } else if(b.hasAttribute('data-broken'))S.broken=b.getAttribute('data-broken');
  else if(b.hasAttribute('data-kind'))S.kind=b.getAttribute('data-kind');
  else if(b.hasAttribute('data-tmdb'))S.tmdb=b.getAttribute('data-tmdb');
  renderFilters();load(true);
});
$('litems').addEventListener('click',function(ev){
  var t=ev.target.closest('[data-open]');if(t)openItem(t.getAttribute('data-open'));
});
$('litems').addEventListener('error',function(ev){ // görsel yüklenemezse yer tutucu kalsın
  var t=ev.target;if(t&&t.tagName==='IMG')t.remove();
},true);
$('lmore').addEventListener('click',function(){if(!busy&&hasMore)load(false)});
$('lmeta').addEventListener('click',function(ev){
  if(ev.target.id!=='lreset')return;
  S.sites=fixedSite?[fixedSite]:[];S.broken=S.kind=S.tmdb=S.q=S.id='';$('lq').value='';renderFilters();load(true);
});
$('lreload').addEventListener('click',function(){load(true)});
$('lq').addEventListener('input',function(){
  clearTimeout(qTimer);
  qTimer=setTimeout(function(){var v=$('lq').value.trim();if(v!==S.q||S.id){S.q=v;S.id='';load(true)}},250);
});
$('lsort').addEventListener('change',function(){S.sort=$('lsort').value;load(true)});
$('lview').addEventListener('click',function(ev){
  var b=ev.target.closest('button[data-view]');if(!b)return;
  S.view=b.getAttribute('data-view');store('dz_lib_view',S.view);
  document.querySelectorAll('#lview button').forEach(function(x){x.setAttribute('aria-pressed',String(x===b))});
  renderItems(false);
});

(function init(){
  var v=store('dz_lib_view');
  if(v==='list'||v==='grid'){
    S.view=v;
    document.querySelectorAll('#lview button').forEach(function(x){x.setAttribute('aria-pressed',String(x.getAttribute('data-view')===v))});
  }
  var h=location.hash.replace(/^#/,'');
  showTab(/^(errors|live|categories|settings|backgrounds|settings\/\w+)$/.test(h)?h:'onboard');   /* #events / #library / #onboard (eski) -> Siteler */
})();
})();
