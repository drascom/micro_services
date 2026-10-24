/* "Siteler" sekmesi (#onboard): site AKORDİYONU. Kapalı satır = durum noktası · ad · son tarama · içerik · otomatik tarama
   (anahtar + aralık; PUT /api/ops/settings sites.<site>) · Tara · ⋯ menüsü (YAML / Devir notu / Ad değiştir / Heal / Geri al /
   Arşivden bir parti al / Sil). Açılınca alt sekmeler: Olaylar (GET /api/ops/events?site=), Kütüphane (library.js dzLib.mount,
   site süzgeçli), Oynatıcılar (providers + son çözümleme/oynatma sağlığı, /api/ops/overview). Canlı TV kaynağı ve "Tüm kütüphane"
   ayrı satırlar. window.dzOpenMedia = olay bağlantılarının tek hedefi. Sekme gizliyken ağ isteği yok. */
(function(){
'use strict';
var $=function(i){return document.getElementById(i)};
var SITES='/api/ops/sites';
var PRESETS=[[0.25,'15 dk'],[1,'1 saat'],[3,'3 saat'],[6,'6 saat'],[12,'12 saat'],[24,'24 saat']],customOpen={},sMeta={};
var built=false,sites=null,sFail=false,sLoading=false,ui={rename:null,menu:null},M=null,tickN=0,timer=null;
var lives=[];   /* GET /api/ops/sites/manage live_sources: Canlı TV kaynakları (scraper sitesi değil; heal/YAML yok) */
var LIB='__lib';   /* "Tüm kütüphane" satırı */
var acc={open:null,sub:{}},evl=null,evlSite=null;
var BUSY={scan:'tarama',heal:'düzeltme',archive:'arşiv partisi',finder:'kaynak arama'};
var RUNST={success:['ok','Başarılı'],ok:['ok','Başarılı'],partial:['warn','Kısmi'],error:['bad','Hata'],failed:['bad','Hata'],running:['run','Çalışıyor']};

function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
function T(t){return t?new Date(t).getTime():0}
function ago(t){if(!T(t))return '-';var d=Math.max(0,(Date.now()-T(t))/1000);
  if(d<60)return Math.floor(d)+' sn önce';if(d<3600)return Math.floor(d/60)+' dk önce';
  if(d<86400)return Math.floor(d/3600)+' sa önce';return Math.floor(d/86400)+' g önce'}
function until(t){var s=(T(t)-Date.now())/1000;if(s<=60)return 'birazdan';return s<3600?Math.ceil(s/60)+' dk sonra':Math.round(s/360)/10+' sa sonra'}
function when(t){return T(t)?new Date(t).toLocaleString('tr-TR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}):'-'}
function hoursText(h){h=+h;if(h<1)return Math.round(h*60)+' dk';return (Math.round(h*100)/100)+' saat'}
function num(n){return n==null?'-':(+n).toLocaleString('tr-TR')}
function pill(cls,txt){return '<span class="pill '+cls+'">'+esc(txt)+'</span>'}
function hostOf(u){try{return new URL(u).host}catch(e){return String(u||'')}}
function msgOf(e){return String(e&&e.message||e)}
function enc(v){return encodeURIComponent(v)}
function toast(msg,kind){
  var box=$('toasts');if(!box)return;
  var el=document.createElement('div');el.className='toast'+(kind==='bad'?' bad':'');el.textContent=msg;
  box.appendChild(el);setTimeout(function(){if(el.parentNode)el.parentNode.removeChild(el)},kind==='bad'?5000:2200);
}
function call(path,opt){
  return fetch(path,opt).then(function(r){
    return r.json().catch(function(){return null}).then(function(j){
      if(!r.ok){
        var m=(j&&j.error&&j.error.message)||(j&&j.detail&&(j.detail.message||j.detail))||('HTTP '+r.status);
        var err=new Error(typeof m==='string'?m:JSON.stringify(m));err.status=r.status;
        err.code=(j&&j.error&&j.error.code)||(j&&j.detail&&j.detail.code)||'';err.body=j;throw err;
      }
      return j;
    });
  });
}
function post(path,body){return call(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})})}
function changed(){try{document.dispatchEvent(new CustomEvent('dz:settings-changed'))}catch(e){}}
function copyText(t){
  if(window.dzEv)return window.dzEv.copyText(t);
  return Promise.reject(new Error('kopyalanamadı'));
}
function siteById(id){var l=sites||[];for(var i=0;i<l.length;i++)if(l[i].site_id===id)return l[i];return null}
function liveById(id){for(var i=0;i<lives.length;i++)if(lives[i].source===id)return lives[i];return null}
function ovSite(id){return window.dzEv?window.dzEv.siteOf(id):null}

/* ---- iskelet ---- */
function build(){
  if(built)return;built=true;
  $('ob-root').innerHTML=
  '<div id="ob-listv" class="obl">'+
    '<div id="ob-warn"></div>'+
    '<ul id="ob-sites" class="accl"></ul>'+
  '</div>'+
  '<div id="ob-modal" class="obmodal hidden"></div>';
  bind();
}

function sw(id,checked,label,attrs){
  return '<label class="sw"><input type="checkbox" role="switch" id="'+id+'" '+(checked?'checked ':'')+(attrs||'')+' aria-label="'+esc(label)+'"><span class="knob"></span></label>';
}
function intervalControl(id,a){
  var h=+a.interval_hours,preset=PRESETS.some(function(p){return Math.abs(p[0]-h)<1e-9});
  var custom=!!customOpen[id]||!preset;
  var sel='<select data-interval="'+esc(id)+'" aria-label="'+esc(id)+' tarama aralığı">'+PRESETS.map(function(p){
    return '<option value="'+p[0]+'"'+(!custom&&Math.abs(p[0]-h)<1e-9?' selected':'')+'>'+p[1]+'</option>'}).join('')+
    '<option value="custom"'+(custom?' selected':'')+'>Özel…</option></select>';
  var lim=sMeta.limits||{};
  var inp=custom?'<input type="number" data-custom="'+esc(id)+'" value="'+esc(a.interval_hours)+'" min="'+esc(lim.min_interval_hours)+'" max="'+esc(lim.max_interval_hours)+'" step="0.25" aria-label="Özel aralık (saat)">':'';
  return sel+inp;
}
/* durum noktası: mavi tarıyor · gri hiç taranmadı · kırmızı hata · sarı kısmi · yeşil başarılı */
function dotOf(s){
  var a=s.auto_scan||{},r=s.last_run;
  if(a.running||s.busy==='scan'||(s.archive&&s.archive.running))return ['run','Taranıyor'];
  if(!r||!T(r.at))return ['','Hiç taranmadı'];
  var m=RUNST[r.status];return m?[m[0],m[1]]:['',r.status||'?'];
}
function dot(d){return '<span class="sdot '+d[0]+'" title="'+esc(d[1])+'" aria-label="'+esc(d[1])+'"></span>'}
function countsShort(c){
  c=c||{};var p=[];
  if(c.series)p.push(num(c.series)+' dizi');
  if(c.movies)p.push(num(c.movies)+' film');
  return p.join(' · ')||'içerik yok';
}
function archiveShort(a){
  var done=0,tot=0;
  if(a.series){done+=+a.series.processed||0;tot+=+a.series.total||0}
  if(a.movies){done+=+a.movies.processed||0;tot+=+a.movies.total||0}
  if(!tot)return 'Arşiv';
  return 'Arşiv %'+Math.floor(done/tot*100);
}
function archiveTip(a){
  var p=[];
  if(a.series)p.push('dizi '+num(a.series.processed)+'/'+num(a.series.total));
  if(a.movies)p.push('film '+num(a.movies.processed)+'/'+num(a.movies.total)+(a.movies.done?' (bitti)':''));
  p.push('son parti '+(a.last_batch_at?ago(a.last_batch_at):'yok'));
  if(a.next_batch_at)p.push('sonraki '+until(a.next_batch_at));
  if(a.last_error)p.push('son hata: '+a.last_error);
  if(a.last_drift)p.push('sapma: '+(a.last_drift.part||'')+' ('+(a.last_drift.heal||'')+')');
  return p.join(' · ');
}
function menuHtml(id,items){
  var open=ui.menu===id;
  return '<div class="amenu"><button class="btn icon" data-ob="menu" data-site="'+esc(id)+'" aria-haspopup="true" aria-expanded="'+open+'" aria-label="Diğer işlemler">⋯</button>'+
    (open?'<div class="amenup" role="menu">'+items.join('')+'</div>':'')+'</div>';
}
function mi(act,id,label,dis,cls){return '<button role="menuitem" class="'+(cls||'')+'" data-ob="'+act+'" data-site="'+esc(id)+'"'+(dis?' disabled':'')+'>'+esc(label)+'</button>'}
function headSite(s){
  var id=s.site_id,name=s.display_name||id,busy=s.busy,a=s.auto_scan||{},running=!!a.running||!!busy,open=acc.open===id;
  var r=s.last_run,err=r&&r.status==='error';
  var h='<div class="acch">'+
    '<button class="acct" data-ob="toggle" data-site="'+esc(id)+'" aria-expanded="'+open+'">'+dot(dotOf(s))+'<b>'+esc(name)+'</b></button>'+
    '<span class="accm"><span title="'+esc(r&&T(r.at)?'Son tarama '+when(r.at):'')+'"'+(err?' class="bad"':'')+'>'+esc(r&&T(r.at)?ago(r.at):'hiç taranmadı')+'</span>'+
      '<span>'+esc(countsShort(s.counts))+'</span>'+
      (s.archive?'<span class="obchip" title="'+esc(archiveTip(s.archive))+'">'+esc(archiveShort(s.archive))+'</span>':'')+
      (busy?'<span class="pill run"><span class="dot pulse"></span>'+esc(BUSY[busy]||busy)+'</span>':'')+
      (s.heal_cooldown_until&&T(s.heal_cooldown_until)>Date.now()?pill('warn','heal cooldown'):'')+'</span>'+
    '<span class="accc">'+sw('en-'+esc(id),!!a.enabled,id+' otomatik tarama','data-enable="'+esc(id)+'"')+
      (a.interval_hours!=null?intervalControl(id,a):'')+
      (a.enabled&&a.next_scan_at&&!running?'<span class="dim" title="'+esc(when(a.next_scan_at))+'">'+esc(until(a.next_scan_at))+'</span>':'')+'</span>'+
    '<button class="btn" data-ob="sscan" data-site="'+esc(id)+'"'+(running?' disabled':'')+'>Tara</button>'+
    menuHtml(id,[
      mi('syaml',id,'YAML'),mi('shandoff',id,'Devir notu'),mi('srename',id,'Ad değiştir',busy),
      mi('sheal',id,'Heal',busy),
      s.can_rollback?mi('srollback',id,'Geri al',busy):'',
      s.archive?mi('sarchive',id,'Arşivden bir parti al',running):'',
      mi('sdel',id,'Sil',busy,'danger')])+
  '</div>';
  if(ui.rename===id){
    h+='<div class="obsf"><label class="obfl">Görünen ad<input id="ob-rn" type="text" maxlength="60" value="'+esc(name)+'"></label>'+
      '<div class="srow"><button class="btn primary" data-ob="rnsave" data-site="'+esc(id)+'">Kaydet</button>'+
      '<button class="btn" data-ob="rncancel">Vazgeç</button></div></div>';
  }
  return h;
}
function headLive(l){
  var c=l.counts||{},le=l.last_event,id=l.source,open=acc.open===id;
  var d=l.running?['run','Taranıyor']:!le?['','Hiç taranmadı']:le.status==='error'?['bad','Hata']:['ok','Başarılı'];
  return '<div class="acch">'+
    '<button class="acct" data-ob="toggle" data-site="'+esc(id)+'" aria-expanded="'+open+'">'+dot(d)+'<b>'+esc(l.label||'Canlı TV')+'</b><span class="dim mono">'+esc(id)+'</span></button>'+
    '<span class="accm"><span title="'+esc(le?'Son tarama '+when(le.at):'')+'"'+(le&&le.status==='error'?' class="bad"':'')+'>'+esc(le?ago(le.at):'hiç taranmadı')+'</span>'+
      '<span>'+esc(num(c.visible)+' görünür kanal')+'</span></span>'+
    '<span class="accc"><span class="dim">'+esc(l.enabled?'her '+(l.interval_minutes||60)+' dk'+(l.next_scan_at&&!l.running?' · '+until(l.next_scan_at):''):'otomatik kapalı')+'</span></span>'+
    '<button class="btn" data-ob="lscan" data-site="'+esc(id)+'"'+(l.running?' disabled':'')+'>Tara</button>'+
    menuHtml(id,[mi('lsettings',id,'Ayarlar › Canlı TV')])+
  '</div>';
}
function headLib(){
  return '<div class="acch"><button class="acct" data-ob="toggle" data-site="'+LIB+'" aria-expanded="'+(acc.open===LIB)+'">'+dot(['lib','Tüm siteler'])+'<b>Tüm kütüphane</b></button>'+
    '<span class="accm"><span>tüm sitelerin yapımları</span></span></div>';
}
/* ---- açık satır gövdesi ---- */
function subsOf(id){return id===LIB?[['library','Kütüphane']]:liveById(id)?[['events','Olaylar'],['channels','Kanallar']]:[['events','Olaylar'],['library','Kütüphane'],['players','Oynatıcılar']]}
function bodyHtml(id){
  var subs=subsOf(id),cur=acc.sub[id]||subs[0][0],s=siteById(id);
  var meta=s?'<div class="accinfo dim"><span class="mono">'+esc(id)+'</span>'+(s.base_url?' · '+esc(hostOf(s.base_url)):'')+' · v'+esc(s.version==null?'?':s.version)+
    (s.hand_built?' · elle yapılmış':'')+' · '+(s.search?'arama var':'arama yok')+'</div>':'';
  return (subs.length>1?'<div class="accsub" role="tablist">'+subs.map(function(x){
      return '<button role="tab" data-ob="sub" data-site="'+esc(id)+'" data-sub="'+x[0]+'" aria-selected="'+(x[0]===cur)+'">'+esc(x[1])+'</button>'}).join('')+meta+'</div>':'')+
    '<div class="accp" data-panel="'+esc(id)+'"></div>';
}
function panelOf(id){var r=$('ob-sites');return r?r.querySelector('.accp[data-panel="'+cssq(id)+'"]'):null}
function cssq(v){return window.CSS&&CSS.escape?CSS.escape(v):String(v).replace(/"/g,'\\"')}
function fillPanel(id,opt){
  var p=panelOf(id);if(!p)return;
  var subs=subsOf(id),cur=acc.sub[id]||subs[0][0];
  if(window.dzLib&&cur!=='library')window.dzLib.park();
  if(cur!=='events'){evl=null;evlSite=null}
  if(cur==='events'){
    p.innerHTML='<div class="evbox"></div>';
    evlSite=id;
    evl=window.dzEv.list(p.firstChild,{empty:'Bu kaynak için olay yok.',fetch:function(before){
      return call('/api/ops/events?limit=30&site='+enc(id)+(before?'&before='+enc(before):''))}});
    evl.reset();
  } else if(cur==='library'){
    p.innerHTML='';
    if(window.dzLib)window.dzLib.mount(p,id===LIB?null:id,opt||{});
  } else if(cur==='players'){
    p.innerHTML=safeHtml(function(){return playersHtml(id)});
  } else if(cur==='channels'){
    p.innerHTML=safeHtml(function(){return channelsHtml(liveById(id))});
  }
}
function safeHtml(fn){try{return fn()}catch(e){try{console.error('sites: panel çizilemedi',e)}catch(_){}return '<div class="note bad">Çizilemedi: '+esc(msgOf(e))+'</div>'}}
function playersHtml(id){
  var s=siteById(id)||{},o=ovSite(id),h='';
  var list=(s.providers||[]);
  h+='<div class="srow">'+(list.length?list.map(function(p){return '<span class="obchip">'+esc(p)+'</span>'}).join(' '):
    s.providers_all?'<span class="obchip dim" title="yaml’da providers: listesi yok; tüm oynatıcılara izin verilir">Tümü</span>':'<span class="dim">oynatıcı listesi yok</span>')+'</div>';
  if(o&&window.dzEv){
    h+=window.dzEv.resolverHtml(o)+window.dzEv.playbackHtml(o);
    var ph=o.playback_health;
    h+='<div class="act">'+(ph&&ph.failed>0?'<button class="btn" data-pdo="heal-playback" data-site="'+esc(id)+'"'+(o.healing?' disabled':'')+'>Oynatma heal</button>':'')+'</div>';
    if(!o.last_resolver&&!(ph&&(ph.window_n||ph.last_trigger_at||(ph.issues&&ph.issues.open))))h+='<div class="dim" style="margin-top:8px">Henüz oynatma verisi yok.</div>';
  }
  return h;
}
function channelsHtml(l){
  if(!l)return '';
  var c=l.counts||{},le=l.last_event;
  return '<dl class="obsd">'+
    '<dt>Ülke</dt><dd>'+esc((l.countries||[]).join(', ').toUpperCase()||'-')+(l.languages&&l.languages.length?' <span class="dim">· dil '+esc(l.languages.join(', '))+'</span>':'')+'</dd>'+
    '<dt>Son tarama</dt><dd>'+(le?esc(ago(le.at))+' <span class="dim">'+esc(le.status==='error'?(le.error||''):num(le.new)+' yeni · '+num(le.updated)+' güncellendi · '+num(le.deactivated)+' pasif')+'</span>':'<span class="dim">yok</span>')+'</dd>'+
    '<dt>Kanallar</dt><dd>'+esc(num(c.active)+' aktif · '+num(c.visible)+' görünür · '+num(c.hidden)+' gizli · '+num(c.inactive)+' pasif')+'</dd>'+
    '<dt>Sağlık</dt><dd>'+esc(num(c.ok)+' sağlıklı (m3u8) · '+num(c.youtube_only)+' yalnız YouTube · '+num(c.hidden)+' oynatılamaz')+'</dd>'+
  '</dl>';
}

/* ---- liste (anahtarlı DOM: yenilemede yalnız başlıklar değişir, açık gövde/Kütüphane korunur) ---- */
function entries(){
  var out=[];
  (sites||[]).forEach(function(s){out.push({id:s&&s.site_id,kind:'site',x:s})});
  lives.forEach(function(l){out.push({id:l&&l.source,kind:'live',x:l})});
  if(sites&&sites.length)out.push({id:LIB,kind:'lib',x:null});
  return out;
}
function headOf(en){return en.kind==='site'?headSite(en.x):en.kind==='live'?headLive(en.x):headLib()}
function drawSites(){
  var el=$('ob-sites'),w=$('ob-warn');if(!el)return;
  if(!sites){
    if(window.dzLib)window.dzLib.park();
    el.innerHTML=sFail?'<li class="note bad">Site listesi alınamadı: '+esc(sFail)+' <button class="btn" data-ob="srefresh">Yeniden dene</button></li>':'<li class="empty">Yükleniyor…</li>';
    return;
  }
  w.innerHTML=sMeta.any_enabled===false?'<div class="note warnbar" role="alert"><b>Otomatik tarama kapalı.</b> Yeni içerik yalnız elle “Tara” ile gelir.</div>':'';
  var list=entries();
  if(!list.length){if(window.dzLib)window.dzLib.park();el.innerHTML='<li class="pempty">Kayıtlı site yok.</li>';return}
  var keep={};
  Array.prototype.slice.call(el.children).forEach(function(li){if(!li.getAttribute('data-acc'))el.removeChild(li)});
  list.forEach(function(en){
    var id=String(en.id||'?');keep[id]=1;
    var li=el.querySelector('li[data-acc="'+cssq(id)+'"]');
    if(!li){li=document.createElement('li');li.className='acc';li.setAttribute('data-acc',id);li.innerHTML='<div class="acchw"></div><div class="accb hidden"></div>'}
    el.appendChild(li);   // sırayı korur
    var busy=en.kind==='site'?!!(en.x&&(en.x.busy||(en.x.auto_scan&&en.x.auto_scan.running))):en.kind==='live'?!!(en.x&&en.x.running):false;
    li.classList.toggle('isbusy',busy);li.classList.toggle('open',acc.open===id);
    li.firstChild.innerHTML=safeRow(headOf,en,id);
    var b=li.lastChild,isOpen=acc.open===id;
    if(isOpen&&b.classList.contains('hidden')){b.classList.remove('hidden');b.innerHTML=bodyHtml(id);fillPanel(id,acc.pending);acc.pending=null}
    else if(!isOpen&&!b.classList.contains('hidden')){if(window.dzLib&&b.contains($('lib-layout')))window.dzLib.park();b.classList.add('hidden');b.innerHTML=''}
  });
  Array.prototype.slice.call(el.children).forEach(function(li){
    if(!keep[li.getAttribute('data-acc')]){if(window.dzLib&&li.contains($('lib-layout')))window.dzLib.park();el.removeChild(li)}});
}
/* bir satırın render hatası tüm listeyi boşaltmasın */
function safeRow(fn,x,id){
  try{return fn(x)}catch(e){
    try{console.error('sites: satır çizilemedi',id,e)}catch(_){}
    return '<div class="acch"><b>'+esc(id||'?')+'</b><span class="note bad">Satır çizilemedi: '+esc(msgOf(e))+'</span></div>';
  }
}
function scanEditing(){var a=document.activeElement,r=$('ob-sites');return !!(a&&r&&r.contains(a)&&a.matches('.acch select,.acch input'))}
function renderSites(){if(ui.rename||scanEditing())return;drawSites()}
function loadSites(){
  sLoading=true;
  return call(SITES+'/manage').then(function(d){sites=(d&&d.sites)||[];lives=(d&&d.live_sources)||[];sMeta={any_enabled:d&&d.any_enabled,limits:d&&d.limits,tick_seconds:d&&d.tick_seconds};sFail=false}).catch(function(e){sFail=msgOf(e)})
    .then(function(){sLoading=false;renderSites()});
}
function toggle(id,force,sub,opt){
  var open=force!=null?force:acc.open!==id;
  if(sub)acc.sub[id]=sub;
  acc.open=open?id:null;acc.pending=opt||null;ui.menu=null;
  if(open&&opt){   // zorla yeniden doldur (yapım bağlantısı)
    var li=$('ob-sites').querySelector('li[data-acc="'+cssq(id)+'"]');
    if(li&&!li.lastChild.classList.contains('hidden')){li.lastChild.innerHTML=bodyHtml(id);fillPanel(id,opt);acc.pending=null}
  }
  drawSites();
}
function patchSite(site,fields,msg){
  var p={sites:{}};p.sites[site]=fields;
  return call('/api/ops/settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)})
    .then(function(){toast(msg||'Kaydedildi');changed()})
    .catch(function(e){toast('Kaydedilemedi: '+msgOf(e),'bad')}).then(loadSites);
}
function archiveNow(site,b){
  b.disabled=true;
  call(SITES+'/'+enc(site)+'/archive-batch',{method:'POST'}).then(function(){
    toast(site+': arşiv partisi başladı');return loadSites();
  }).catch(function(e){toast((e&&e.status===409?'Meşgul: ':'Başlatılamadı: ')+msgOf(e),'bad');b.disabled=false});
}
function scanNow(site,b){
  b.disabled=true;
  call(SITES+'/'+enc(site)+'/scan',{method:'POST'}).then(function(r){
    if(r&&r.started===false)toast(site+': zaten çalışıyor','bad');
    else toast(site+': tarama başlatıldı');
    return loadSites();
  }).catch(function(e){toast('Başlatılamadı: '+msgOf(e),'bad');b.disabled=false});
}
function healNow(site){
  var base=SITES+'/'+enc(site)+'/heal';
  call(base,{method:'POST'}).then(function(r){
    if(r&&r.reason==='no_drift'){
      if(!confirm('Drift görünmüyor. Yine de heal çalıştırılsın mı (force)?'))return null;
      return call(base+'?force=true',{method:'POST'});
    }
    return r;
  }).then(function(r){
    if(!r)return;
    toast(r.started===false?site+': '+(r.reason==='already_running'?'zaten çalışıyor':r.reason):site+': heal başlatıldı',r.started===false?'bad':'');
    return loadSites();
  }).catch(function(e){toast('Başlatılamadı: '+msgOf(e),'bad')});
}
function liveScanNow(b){
  b.disabled=true;
  call('/api/ops/live/scan',{method:'POST'}).then(function(r){
    toast(r&&r.started===false?'Canlı TV: zaten çalışıyor':'Canlı TV taraması başladı',r&&r.started===false?'bad':'');
    setTimeout(loadSites,1500);
  }).catch(function(e){toast('Başlatılamadı: '+msgOf(e),'bad');b.disabled=false});
}
function doRename(site){
  var dn=$('ob-rn').value.trim();
  if(!dn){toast('Bir ad yaz','bad');return}
  post(SITES+'/'+enc(site)+'/rename',{display_name:dn}).then(function(r){
    toast('Ad değişti'+(r&&r.version?' (v'+r.version+')':''));ui.rename=null;changed();return loadSites();
  }).catch(function(e){toast('Ad değiştirilemedi: '+msgOf(e),'bad')});
}
function rollbackSite(site){
  var s=siteById(site);
  if(!confirm(((s&&s.display_name)||site)+': önceki config sürümüne dönülsün mü?'))return;
  post(SITES+'/'+enc(site)+'/rollback').then(function(r){toast('v'+(r&&r.version!=null?r.version:'?')+' geri yüklendi');changed()})
    .catch(function(e){toast('Geri alınamadı: '+msgOf(e),'bad')}).then(loadSites);
}
function purgeLine(s,purge){
  var c=(s&&s.counts)||{};
  return purge?num(c.titles||0)+' başlık ve '+num(c.with_sources||0)+' kaynak katalogdan kaldırılacak.':'Kütüphane kayıtları kalır; yalnız site yaml’ı ve ayarları silinir.';
}
function openYaml(site){
  M={kind:'yaml',site:site,state:'loading'};renderModal();
  call(SITES+'/'+enc(site)+'/config').then(function(j){
    if(!M||M.kind!=='yaml'||M.site!==site)return;M.state='ok';M.cfg=j||{};renderModal();
  }).catch(function(e){if(!M||M.kind!=='yaml'||M.site!==site)return;M.state='err';M.err=msgOf(e);renderModal()});
}
function openHandoff(site){
  M={kind:'handoff',site:site,state:'loading'};renderModal();
  call(SITES+'/'+enc(site)+'/handoff').then(function(j){
    if(!M||M.kind!=='handoff'||M.site!==site)return;M.state='ok';M.text=(j&&j.text)||'';renderModal();
  }).catch(function(e){if(!M||M.kind!=='handoff'||M.site!==site)return;M.state='err';M.err=msgOf(e);renderModal()});
}
function openDel(site){M={kind:'del',site:site,purge:true,busy:false,err:''};renderModal()}
function closeModal(){M=null;renderModal()}
function modalHtml(){
  var s=siteById(M.site),name=(s&&s.display_name)||M.site,h='';
  if(M.kind==='yaml'){
    var c=M.cfg||{},vs=Array.isArray(c.versions)?c.versions:[];
    h='<div class="lhead"><h3 id="ob-mt" class="grow">'+esc(name)+' · yaml'+(c.version!=null?' (v'+esc(c.version)+')':'')+'</h3>'+
      (M.state==='ok'&&c.yaml_text?'<button class="btn" data-ob="mcopy">Kopyala</button>':'')+'<button class="btn" data-ob="mclose">Kapat</button></div>';
    if(M.state==='loading')h+='<div class="empty">Yükleniyor…</div>';
    else if(M.state==='err')h+='<div class="note bad">Config alınamadı: '+esc(M.err)+'</div>';
    else {
      if(vs.length)h+='<div class="hint">Sürümler: '+vs.map(function(v){return '<span class="obchip">v'+esc(v.version)+(v.updated_at?' · '+esc(when(v.updated_at)):'')+'</span>'}).join(' ')+'</div>';
      h+=c.yaml_text?'<pre class="obyaml obmy mono" tabindex="0">'+esc(c.yaml_text)+'</pre>':'<div class="pempty">Yaml boş</div>';
    }
  } else if(M.kind==='handoff'){
    h='<div class="lhead"><h3 id="ob-mt" class="grow">'+esc(name)+' · devir notu</h3>'+
      (M.state==='ok'&&M.text?'<button class="btn" data-ob="mcopyh">Kopyala</button>':'')+'<button class="btn" data-ob="mclose">Kapat</button></div>';
    if(M.state==='loading')h+='<div class="empty">Yükleniyor…</div>';
    else if(M.state==='err')h+='<div class="note bad">Devir notu alınamadı: '+esc(M.err)+'</div>';
    else h+=M.text?'<pre class="obyaml obmy mono" tabindex="0">'+esc(M.text)+'</pre>':'<div class="pempty">Henüz devir notu yok.</div>';
  } else {
    h='<h3 id="ob-mt">“'+esc(name)+'” silinsin mi?</h3>'+
      '<div class="obwarn"><b>Bu işlem geri alınamaz.</b></div>'+
      '<div class="obsum" id="ob-purge-line">'+esc(purgeLine(s,M.purge))+'</div>'+
      '<label class="chk"><input type="checkbox" id="ob-purge"'+(M.purge?' checked':'')+'> Kütüphane kayıtlarını da sil</label>'+
      (s&&s.hand_built?'<div class="note warn"><b>Elle yapılmış site:</b> kod modülü repoda kalır; yeniden eklemek için yaml gerekir.</div>':'')+
      (M.err?'<div class="note bad">'+esc(M.err)+'</div>':'')+
      '<div class="act"><button class="btn obdanger" id="ob-mdel" data-ob="mdel"'+(M.busy?' disabled':'')+'>'+(M.busy?'Siliniyor…':'Sil')+'</button>'+
      '<button class="btn" data-ob="mclose">Vazgeç</button></div>';
  }
  return h;
}
function renderModal(){
  var el=$('ob-modal');
  if(!M){el.innerHTML='';el.classList.add('hidden');return}
  el.innerHTML='<div class="obmb" data-ob="mclose"></div><div class="obmbox" id="ob-mbox" role="dialog" aria-modal="true" aria-labelledby="ob-mt" tabindex="-1">'+modalHtml()+'</div>';
  el.classList.remove('hidden');
  var b=$('ob-mbox');if(b&&b.focus)b.focus(); // yeniden çizimde odak pencerede kalsın (Esc çalışsın)
}
function doDelete(){
  var m=M;if(!m||m.kind!=='del'||m.busy)return;
  var s=siteById(m.site),name=(s&&s.display_name)||m.site;
  m.busy=true;m.err='';renderModal();
  call(SITES+'/'+enc(m.site)+'?purge='+(m.purge?'1':'0'),{method:'DELETE'}).then(function(r){
    var p=(r&&r.purged)||{};
    toast('Silindi: '+name+(m.purge&&(p.library_items!=null||p.video_sources!=null)?' · '+num(p.library_items||0)+' başlık, '+num(p.video_sources||0)+' kaynak kaldırıldı':''));
    if(M===m){M=null;renderModal()}
    if(acc.open===m.site)acc.open=null;
    changed();return loadSites();
  }).catch(function(e){
    m.busy=false;m.err=e&&e.status===409?'Bu site için çalışan bir iş var; bitince tekrar dene. ('+msgOf(e)+')':'Silinemedi: '+msgOf(e);
    if(M===m)renderModal();
  });
}
function onChange(ev){
  var t=ev&&ev.target;if(!t||!t.closest('.acch,#ob-modal'))return;   // Kütüphane süzgeçleri kendi dinleyicisinde
  if(t.dataset&&t.dataset.enable){var on=t.checked;patchSite(t.dataset.enable,{enabled:on},t.dataset.enable+': otomatik tarama '+(on?'açıldı':'kapatıldı'));return}
  if(t.dataset&&t.dataset.interval){
    var site=t.dataset.interval;
    if(t.value==='custom'){customOpen[site]=true;drawSites();var i=$('ob-sites').querySelector('[data-custom="'+site+'"]');if(i){i.focus();i.select()}return}
    delete customOpen[site];
    patchSite(site,{interval_hours:parseFloat(t.value)},site+': aralık '+hoursText(t.value));return;
  }
  if(t.dataset&&t.dataset.custom){
    var s2=t.dataset.custom,val=parseFloat(t.value),lim=sMeta.limits||{};
    if(!isFinite(val)||val<lim.min_interval_hours||val>lim.max_interval_hours){
      toast('Aralık '+hoursText(lim.min_interval_hours)+' – '+hoursText(lim.max_interval_hours)+' arasında olmalı','bad');drawSites();return}
    delete customOpen[s2];
    patchSite(s2,{interval_hours:val},s2+': aralık '+hoursText(val));return;
  }
  if(t.id==='ob-purge'&&M&&M.kind==='del'){M.purge=!!t.checked;$('ob-purge-line').textContent=purgeLine(siteById(M.site),M.purge)}
}
function bind(){
  $('ob-root').addEventListener('change',onChange);
  $('ob-root').addEventListener('keydown',function(ev){
    var t=ev.target,id=t&&t.id;
    if(ev.key==='Escape'){
      if(M){closeModal();return}
      if(ui.menu){ui.menu=null;drawSites();return}
      if(ui.rename){ui.rename=null;drawSites()}
      return;
    }
    if(ev.key!=='Enter'||ev.shiftKey||ev.isComposing)return;
    if(id==='ob-rn'&&ui.rename){ev.preventDefault();doRename(ui.rename)}
  });
  document.addEventListener('click',function(ev){   // menü dışına tıklama menüyü kapatır
    if(ui.menu&&!ev.target.closest('.amenu')){ui.menu=null;drawSites()}
  });
  $('ob-root').addEventListener('click',function(ev){
    var pb=ev.target.closest('[data-pdo]');
    if(pb&&!pb.disabled){pb.setAttribute('data-do',pb.getAttribute('data-pdo'));window.dzEv.act(pb,function(){fillPanel(pb.getAttribute('data-site'))});return}
    var b=ev.target.closest('[data-ob]');if(!b||b.disabled)return;
    var a=b.getAttribute('data-ob'),site=b.getAttribute('data-site');
    if(b.closest('.amenup')&&a!=='menu'){ui.menu=null}
    if(a==='srefresh'){sFail=false;sites=null;drawSites();loadSites()}
    else if(a==='toggle'){toggle(site)}
    else if(a==='sub'){acc.sub[site]=b.getAttribute('data-sub');
      var bar=b.parentNode;Array.prototype.forEach.call(bar.querySelectorAll('[data-sub]'),function(x){x.setAttribute('aria-selected',String(x===b))});fillPanel(site)}
    else if(a==='menu'){ui.menu=ui.menu===site?null:site;drawSites()}
    else if(a==='srename'){ui.rename=ui.rename===site?null:site;drawSites();if(ui.rename){var rn=$('ob-rn');if(rn&&rn.focus)rn.focus()}}
    else if(a==='rncancel'){ui.rename=null;drawSites()}
    else if(a==='rnsave'){doRename(site)}
    else if(a==='srollback'){drawSites();rollbackSite(site)}
    else if(a==='sheal'){drawSites();healNow(site)}
    else if(a==='sscan'){scanNow(site,b)}
    else if(a==='lscan'){liveScanNow(b)}
    else if(a==='lsettings'){var tb=$('tab-btn-settings');if(tb)tb.click()}
    else if(a==='sarchive'){drawSites();archiveNow(site,b)}
    else if(a==='syaml'){drawSites();openYaml(site)}
    else if(a==='shandoff'){drawSites();openHandoff(site)}
    else if(a==='sdel'){drawSites();openDel(site)}
    else if(a==='mclose'){closeModal()}
    else if(a==='mdel'){doDelete()}
    else if(a==='mcopyh'){var ht=M&&M.text||'';copyText(ht).then(function(){toast('Devir notu kopyalandı')}).catch(function(){toast('Kopyalanamadı','bad')})}
    else if(a==='mcopy'){var y=M&&M.cfg&&M.cfg.yaml_text||'';copyText(y).then(function(){toast('YAML kopyalandı')}).catch(function(){toast('Kopyalanamadı','bad')})}
  });
}
function visible(){var t=$('tab-onboard');return !!t&&!t.classList.contains('hidden')&&!document.hidden}
function tick(){
  if(!visible())return; // gizliyken polling yok
  tickN++;
  if(tickN%20===0&&!M&&!sLoading)loadSites(); // son tarama ve ayar değişiklikleri tazelenir
  if(tickN%4===0&&sites&&sites.some(function(s){return s.busy||(s.auto_scan&&s.auto_scan.running)})&&!M&&!sLoading)loadSites();
}
document.addEventListener('dz:ops-changed',function(){if(visible()&&evl)evl.load(false)});
document.addEventListener('dz:overview',function(){
  if(!visible()||!acc.open)return;
  if((acc.sub[acc.open]||'')==='players')fillPanel(acc.open);
});

/* ---- olay bağlantılarının TEK hedefi: yapımı Siteler › Kütüphane'de aç (Adım 2: yapım yan paneli) ----
   ref = {id, title, site}. id varsa yapımın sitelerinden (olayın sitesi önce, yoksa ilki) açılır; yoksa başlık aranır. */
function openMedia(ref){
  ref=ref||{};
  var tb=$('tab-btn-onboard');if(tb&&$('tab-onboard').classList.contains('hidden'))tb.click();
  build();
  var go=function(site,opt){
    var p=sites?Promise.resolve():loadSites();
    p.then(function(){
      if(site!==LIB&&!siteById(site))site=LIB;
      toggle(site,true,'library',opt);
      var li=$('ob-sites').querySelector('li[data-acc="'+cssq(site)+'"]');
      if(li&&li.scrollIntoView)li.scrollIntoView({block:'start',behavior:'smooth'});
    });
  };
  if(ref.id){
    call('/api/ops/library?facets=0&limit=1&id='+enc(ref.id)).then(function(d){
      var it=d&&d.items&&d.items[0];
      if(!it){go(ref.site||LIB,{q:ref.title||ref.id});return}
      var src=it.sources||[];
      go(ref.site&&src.indexOf(ref.site)>=0?ref.site:(src[0]||LIB),{id:ref.id});
    }).catch(function(){go(ref.site||LIB,{q:ref.title||''})});
  } else go(ref.site||LIB,{q:ref.title||''});
}
window.dzOpenMedia=openMedia;

/* ---- sekme kancası ---- */
function onShow(){
  build();
  if(!timer)timer=setInterval(tick,1000);
  loadSites();
  if(evl)evl.load(false);
}
window.dzTabHooks=window.dzTabHooks||{};
window.dzTabHooks.onboard=onShow;
if(!$('tab-onboard').classList.contains('hidden'))onShow();
})();
