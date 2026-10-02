/* Kütüphane sekmesi: /api/ops/library (sunucu tarafı süzgeç + sayfalama) ve
   /api/ops/library/{id}/videos. K/T = kırık / toplam video kaynağı; ?N = şüpheli (K'ya dahil değil). */
(function(){
'use strict';
var $=function(i){return document.getElementById(i)};
var PAGE=50;
var S={sites:[],broken:'',kind:'',tmdb:'',q:'',sort:'recent',view:'grid'};
var items=[],total=0,libTotal=0,facets=null,mode='',hasMore=false,loaded=false,failed=false;
var seq=0,busy=false,selId=null,loadedAt=0,vcache={},vseq=0,qTimer=null;

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
var VST={broken:['bad','Kırık'],suspect:['warn','Şüpheli'],healthy:['ok','Sağlam'],unknown:['','Doğrulanmadı'],disabled:['','Devre dışı']};
var KINDL={movie:'Film',episode:'Bölüm',trailer:'Fragman'};
/* oynatma hatası tanısı (sunucunun arka plan yoklaması, library/streamdiag.py): rozet = [pill sınıfı, metin]. K/T sayacını DEĞİŞTİRMEZ */
var DIAG={reachable:['ok','Sunucu erişebiliyor (sorun istemcide olabilir)'],ip_bound:['warn',"IP'ye bağlı"],
  forbidden:['warn','Site erişimi engelliyor'],not_media:['warn','Site video yerine başka içerik veriyor'],
  gone:['bad','Dosya kaynakta yok'],expired:['warn','Adres süresi dolmuş'],timeout:['warn','Kaynak yanıt vermedi'],
  hls_unsupported_browser:['','Tarayıcı HLS desteklemiyor (kaynak sağlam)'],server_blocked:['bad','Sunucu da erişemiyor'],
  unreachable:['warn','Kaynağa ulaşılamadı']};
var PROXYL={ua:'User-Agent bağlı',ip:'IP\'ye bağlı',recipe:'tarif istiyor',learned:'öğrenildi',env:'ayar'};
function diagPills(v){
  var d=v.diagnosis;if(!d)return '';
  var h='';
  if(d.code){
    var m=DIAG[d.code]||['',d.code],txt=m[1];
    if(d.code==='ip_bound'&&d.proxied)txt="IP'ye bağlı: vekille oynatılıyor";
    h+='<span class="pill '+m[0]+'" title="'+esc((d.note||'')+(d.at?' · '+full(d.at):''))+'">'+esc(txt)+'</span>';
  }
  if(d.proxied&&!(d.code==='ip_bound'))h+='<span class="pill" title="Bu kaynağın akışları sunucu vekilinden geçiyor ('+esc(PROXYL[d.proxy_reason]||d.proxy_reason||'')+')">Vekille oynatılıyor</span>';
  return h;
}

/* ---- sekmeler ---- */
var TABS=['events','library','settings','onboard'];
var TITLES={events:'Olay Defteri',library:'Kütüphane',settings:'Ayarlar',onboard:'Siteler'};
function showTab(name){
  TABS.forEach(function(n){
    $('tab-'+n).classList.toggle('hidden',n!==name);
    $('tab-btn-'+n).setAttribute('aria-selected',String(n===name));
  });
  $('ttl').textContent=TITLES[name]||TITLES.events;
  try{history.replaceState(null,'',name==='events'?location.pathname+location.search:(name==='onboard'&&/^#onboard\//.test(location.hash))?location.hash:'#'+name)}catch(e){}
  if(name==='library'&&(!loaded||Date.now()-loadedAt>30000))load(true);
  var hooks=window.dzTabHooks||{};if(hooks[name])hooks[name]();
}
document.querySelector('.tabs').addEventListener('click',function(ev){
  var b=ev.target.closest('button[data-tab]');if(b)showTab(b.getAttribute('data-tab'));
});

/* ---- sorgu ---- */
function enc(v){return encodeURIComponent(v)}
function qs(offset,withFacets){
  var p=[];
  S.sites.forEach(function(s){p.push('site='+enc(s))});
  if(S.broken)p.push('broken='+S.broken);
  if(S.kind)p.push('kind='+S.kind);
  if(S.tmdb)p.push('tmdb='+S.tmdb);
  if(S.q)p.push('q='+enc(S.q));
  p.push('sort='+S.sort,'limit='+PAGE,'offset='+offset);
  if(!withFacets)p.push('facets=0');
  return p.join('&');
}
function dirty(){return !!(S.sites.length||S.broken||S.kind||S.tmdb||S.q)}
function load(reset){
  var my=++seq;
  if(reset){items=[];hasMore=false;vcache={};loaded=false;failed=false;renderItems(false);renderMeta()}
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
  var sites=(f.sites||[]).map(function(s){return chip('data-site',s.site,s.site,s.count,S.sites.indexOf(s.site)>=0)}).join('');
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
    var msg=libTotal===0?'Henüz veri yok.':'Bu süzgeçle eşleşen öğe yok.';
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
    h='<span><b>'+num(total)+'</b> öğe'+(dirty()&&libTotal?' <span>(kütüphanede toplam '+num(libTotal)+')</span>':'')+'</span>';
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

/* ---- detay ---- */
function find(id){return items.filter(function(x){return x.id===id})[0]}
function extLinks(it){
  var l=[];
  if(it.tmdb_id)l.push('<a class="ext" target="_blank" rel="noopener noreferrer" href="https://www.themoviedb.org/'+(it.type==='series'?'tv':'movie')+'/'+esc(it.tmdb_id)+'">TMDB '+esc(it.tmdb_id)+' ↗</a>');
  if(it.imdb_id)l.push('<a class="ext" target="_blank" rel="noopener noreferrer" href="https://www.imdb.com/title/'+esc(it.imdb_id)+'/">'+esc(it.imdb_id)+' ↗</a>');
  return l.join(' · ');
}
function epLabel(v){
  if(v.kind==='episode')return 'S'+String(v.season==null?'?':v.season).padStart(2,'0')+'B'+String(v.episode==null?'?':v.episode).padStart(2,'0');
  return KINDL[v.kind]||v.kind;
}
function videoRow(v){
  var st=VST[v.status]||['',v.status];
  var hosts=(v.providers&&v.providers.length?v.providers:v.stream_hosts&&v.stream_hosts.length?v.stream_hosts:[]).join(', ');
  var who='<b>'+esc(v.site)+'</b>'+(v.host?' · <span class="mono">'+esc(v.host)+'</span>':'');
  var meta=[];
  if(hosts)meta.push('sağlayıcı: '+esc(hosts));
  if(v.episode_title)meta.push(esc(v.episode_title));
  var when=[];
  if(v.last_checked_at)when.push('kontrol '+esc(ago(v.last_checked_at)));
  if(v.last_attempt_at)when.push('deneme '+esc(ago(v.last_attempt_at)));
  if(v.last_failure_at)when.push('deneme hatası '+esc(ago(v.last_failure_at)));
  if(v.last_success_at)when.push('son başarı '+esc(ago(v.last_success_at)));
  if(v.diagnosis&&v.diagnosis.code&&v.diagnosis.at)when.push('tanı '+esc(ago(v.diagnosis.at)));
  /* ölü fragman: YouTube videosu silinmiş/özel/gömülemez (oEmbed). Oynatma hatası DEĞİL: durum rozeti ve K/T değişmez */
  var dead=v.kind==='trailer'&&v.trailer_dead;
  if(dead)when.unshift('fragman kontrolü '+esc(ago(v.trailer_checked_at)));
  return '<li class="vrow st-'+esc(v.status)+(dead?' trailer-dead':'')+'"><span class="vs"><span class="pill '+st[0]+'">'+esc(st[1])+'</span>'+
    (dead?'<span class="pill warn" title="YouTube fragmanı artık oynatılamıyor (oEmbed). K/T sayımına katılmaz; TV\'de Fragman düğmesi gizlenir.">Ölü fragman</span>':'')+'</span>'+
    '<span class="vt">'+who+' <span class="tag">'+esc(epLabel(v))+'</span></span>'+
    (diagPills(v)?'<span class="vd">'+diagPills(v)+'</span>':'')+
    (v.last_error?'<span class="vm err">'+esc(v.last_error)+(v.failures?' · '+v.failures+' hata':'')+'</span>':'')+
    '<span class="vm" title="'+esc(full(v.last_checked_at))+'">'+(meta.concat(when).join(' · ')||'henüz kontrol/deneme yok')+'</span></li>';
}
function seasonStrip(d){
  var ss=d&&d.seasons;if(!ss||!ss.length)return '';
  return '<h2 class="sec">Sezonlar</h2><div class="sstrip">'+ss.map(function(s){
    var tip='Sezon '+s.season+(s.name?' · '+s.name:'')+' — '+s.episodes+' bölüm, '+s.stills+' bölüm görseli'+(s.has_poster?'':' (TMDB sezon posteri yok)');
    return '<figure class="sz" title="'+esc(tip)+'"><span class="szp"><span class="ph" aria-hidden="true">S'+esc(s.season)+'</span>'+
      (s.has_poster?'<img src="/img/'+enc(s.id)+'/portrait?w=200" alt="" loading="lazy" decoding="async">':'')+'</span>'+
      '<figcaption><b>S'+esc(s.season)+'</b> · '+num(s.episodes)+' böl.'+(s.stills?' · '+num(s.stills)+' görsel':'')+'</figcaption></figure>'}).join('')+'</div>';
}
function videosHtml(d){
  var s=d.summary,h='<h2 class="sec">Video kaynakları</h2>';
  if(!d.videos.length)return h+'<div class="note" style="border-color:var(--muted)">Bu öğenin video kaynağı yok.</div>';
  h+='<div class="kv">'+
    '<div><b>'+esc(s.label)+'</b><span>K/T (kırık / toplam)</span></div>'+
    '<div><b>'+num(s.suspect)+'</b><span>şüpheli (K\'ya dahil değil)</span></div>'+
    '<div><b>'+num(s.healthy)+'</b><span>sağlam (oynatma doğruladı)</span></div>'+
    '<div><b>'+num(s.unknown)+'</b><span>doğrulanmadı'+(s.disabled?' · '+s.disabled+' devre dışı':'')+'</span></div>'+
    (s.trailers_dead?'<div><b>'+num(s.trailers_dead)+'</b><span>ölü fragman (K\'ya dahil değil)</span></div>':'')+'</div>';
  h+='<ul class="vlist">'+d.videos.map(videoRow).join('')+'</ul>';
  if(d.truncated)h+='<div class="note" style="border-color:var(--warn)">Liste kısaltıldı (ilk '+d.videos.length+' kaynak; kırıklar önce).</div>';
  return h;
}
function renderDetail(){
  var el=$('ldetail'),it=find(selId);
  if(!it){el.innerHTML='<div class="empty">Video kaynaklarını görmek için bir öğe ya da K/T rozeti seçin.<br><small>Kırık kaynaklar listenin başında görünür.</small></div>';return}
  var d=vcache[it.id];
  var h='<button class="close" id="lclose">Kapat</button>'+
    '<div class="hero">'+ph(it.title)+img(it.backdrop,'')+'</div>'+
    kt(it,true)+'<h3>'+esc(it.title)+'</h3>'+
    '<div class="sub">'+esc(it.year||'-')+' · '+kindL(it)+(it.original_title&&it.original_title!==it.title?' · '+esc(it.original_title):'')+'</div>'+
    '<div class="me" style="margin-bottom:6px">'+srcTags(it)+tmdbTag(it)+'</div>'+
    (extLinks(it)?'<div style="margin:6px 0">'+extLinks(it)+'</div>':'')+
    '<div class="sub" style="margin:6px 0 0">Eklendi: '+esc(full(it.added_at))+(it.poster_origin?' · poster: '+esc(it.poster_origin==='tmdb'?'TMDB':'kaynak'):'')+'</div>';
  if(!d)h+='<h2 class="sec">Video kaynakları</h2><div class="empty" style="padding:16px 0">Yükleniyor…</div>';
  else if(d.error)h+='<div class="note err" style="border-color:var(--bad)">'+esc(d.error)+'</div>';
  else h+=seasonStrip(d)+videosHtml(d);
  el.innerHTML=h;
}
function openItem(id){
  selId=id;
  var prev=document.querySelector('#litems [aria-current]');if(prev)prev.removeAttribute('aria-current');
  var cur=document.querySelector('#litems [data-open="'+(window.CSS&&CSS.escape?CSS.escape(id):id)+'"]');
  var card=cur&&cur.closest('.lcard,.lrow');if(card)card.setAttribute('aria-current','true');
  renderDetail();
  var det=$('ldetail');if(window.innerWidth<=980){det.classList.add('open');det.focus()}
  if(vcache[id]&&!vcache[id].error)return;
  var my=++vseq;
  api('/api/ops/library/'+enc(id)+'/videos').then(function(d){vcache[id]=d}).catch(function(e){vcache[id]={error:'Yüklenemedi: '+(e&&e.message||e)}}).then(function(){if(my===vseq&&selId===id)renderDetail()});
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
$('ldetail').addEventListener('error',function(ev){var t=ev.target;if(t&&t.tagName==='IMG')t.remove()},true);
$('ldetail').addEventListener('click',function(ev){
  var b=ev.target.closest('button');if(!b)return;
  if(b.id==='lclose')$('ldetail').classList.remove('open');
});
$('lmore').addEventListener('click',function(){if(!busy&&hasMore)load(false)});
$('lmeta').addEventListener('click',function(ev){
  if(ev.target.id!=='lreset')return;
  S.sites=[];S.broken=S.kind=S.tmdb=S.q='';$('lq').value='';renderFilters();load(true);
});
$('lreload').addEventListener('click',function(){load(true)});
$('lq').addEventListener('input',function(){
  clearTimeout(qTimer);
  qTimer=setTimeout(function(){var v=$('lq').value.trim();if(v!==S.q){S.q=v;load(true)}},250);
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
  renderDetail();
  if(location.hash==='#library')showTab('library');
  else if(location.hash==='#settings')showTab('settings');
  else if(/^#onboard(\/|$)/.test(location.hash))showTab('onboard');
})();
window.addEventListener('hashchange',function(){ // Olay defteri/banner "Taslağı aç" bağlantıları (#onboard/<id>)
  if(/^#onboard(\/|$)/.test(location.hash))showTab('onboard');
});
})();
