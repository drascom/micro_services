/* "Siteler" sekmesi (#onboard): kayıtlı site LİSTESİ (elle yapılmış olanlar dahil; YAML / Devir notu / Ad değiştir / Geri al / Sil)
   + site başına otomatik tarama (açma-kapama, aralık preset/özel, sonraki/son tarama, Şimdi tara, heal cooldown ipucu;
   yazma = PUT /api/ops/settings sites.<site>, okuma = GET /api/ops/sites/manage auto_scan + limits/any_enabled/tick_seconds)
   + Canlı TV kaynağı satırı (live_sources). Site ekleme (pi onboarding ajanı) 2026-10-24'te kaldırıldı.
   Sekme gizliyken ağ isteği yok. */
(function(){
'use strict';
var $=function(i){return document.getElementById(i)};
var SITES='/api/ops/sites';
var PRESETS=[[0.25,'15 dk'],[1,'1 saat'],[3,'3 saat'],[6,'6 saat'],[12,'12 saat'],[24,'24 saat']],customOpen={},sMeta={};
var built=false,sites=null,sFail=false,sLoading=false,ui={rename:null},M=null,tickN=0,timer=null;
var lives=[];   /* GET /api/ops/sites/manage live_sources: Canlı TV kaynakları (scraper sitesi değil; heal/YAML yok) */
var BUSY={scan:'tarama',heal:'düzeltme',archive:'arşiv partisi',finder:'kaynak arama'};
var RUNST={success:['ok','Başarılı'],ok:['ok','Başarılı'],partial:['warn','Kısmi'],error:['bad','Hata'],failed:['bad','Hata'],running:['run','Çalışıyor']};

function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
function T(t){return t?new Date(t).getTime():0}
function ago(t){if(!T(t))return '-';var d=Math.max(0,(Date.now()-T(t))/1000);
  if(d<60)return Math.floor(d)+' sn önce';if(d<3600)return Math.floor(d/60)+' dk önce';
  if(d<86400)return Math.floor(d/3600)+' sa önce';return Math.floor(d/86400)+' g önce'}
function when(t){return T(t)?new Date(t).toLocaleString('tr-TR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}):'-'}
function hoursText(h){h=+h;if(h<1)return Math.round(h*60)+' dk';return (Math.round(h*100)/100)+' saat'}
function countdown(t){
  var s=Math.floor((T(t)-Date.now())/1000);
  if(s<=0)return 'birazdan (dakikada bir kontrol)';
  var h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;
  return (h?h+' sa ':'')+(h||m?m+' dk ':'')+(x<10&&(h||m)?'0':'')+x+' sn';
}
function num(n){return n==null?'-':(+n).toLocaleString('tr-TR')}
function pill(cls,txt){return '<span class="pill '+cls+'">'+esc(txt)+'</span>'}
function clip(s,n){s=String(s==null?'':s);return s.length>n?s.slice(0,n-1)+'…':s}
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
  if(navigator.clipboard&&navigator.clipboard.writeText)return navigator.clipboard.writeText(t);
  return new Promise(function(res,rej){
    var ta=document.createElement('textarea');ta.value=t;ta.style.cssText='position:fixed;opacity:0;top:0';
    document.body.appendChild(ta);ta.select();
    try{document.execCommand('copy')?res():rej(new Error('kopyalanamadı'))}catch(e){rej(e)}
    document.body.removeChild(ta);
  });
}
function siteById(id){var l=sites||[];for(var i=0;i<l.length;i++)if(l[i].site_id===id)return l[i];return null}

/* ---- iskelet ---- */
function build(){
  if(built)return;built=true;
  $('ob-root').innerHTML=
  '<div id="ob-listv" class="obl">'+
    '<div class="obhead"><h2 class="grow" style="margin:0">Siteler</h2></div>'+
    '<section><div id="ob-sites"></div></section>'+
  '</div>'+
  '<div id="ob-modal" class="obmodal hidden"></div>';
  bind();
}

function countsLine(c){
  c=c||{};
  return num(c.series||0)+' dizi · '+num(c.movies||0)+' film · '+num(c.episodes||0)+' bölüm · '+num(c.with_sources||0)+' kaynaklı';
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
  var inp=custom?'<input type="number" data-custom="'+esc(id)+'" value="'+esc(a.interval_hours)+'" min="'+esc(lim.min_interval_hours)+'" max="'+esc(lim.max_interval_hours)+'" step="0.25" aria-label="Özel aralık (saat)"> saat':'';
  return '<div class="srow"><span>Aralık</span>'+sel+inp+'</div>';
}
function scanHtml(s){
  var id=s.site_id,a=s.auto_scan||{},running=!!a.running||s.busy==='scan';
  var st=running?pill('run','Taranıyor…'):a.enabled?pill('ok','Açık'):pill('','Kapalı');
  var next=!a.enabled?'planlı değil':running?'çalışıyor…':
    '<span data-next="'+esc(a.next_scan_at)+'">'+esc(countdown(a.next_scan_at))+'</span> <span class="dim">'+esc(when(a.next_scan_at))+'</span>';
  var cd=s.heal_cooldown_until&&T(s.heal_cooldown_until)>Date.now()?'<div class="hint">Heal şu an cooldown: <b>'+esc(id)+'</b> ('+esc(when(s.heal_cooldown_until))+' sonrası)</div>':'';
  return '<div class="obscan"><div class="srow"><b>Otomatik tarama</b>'+st+
      sw('en-'+esc(id),!!a.enabled,id+' otomatik tarama','data-enable="'+esc(id)+'"')+'</div>'+
    (a.interval_hours!=null?intervalControl(id,a):'')+
    '<div class="srow"><span>Sonraki tarama</span><span>'+next+'</span></div>'+
    (a.source==='env'?'<div class="hint">Ayar henüz kaydedilmedi; .env varsayılanı kullanılıyor.</div>':'')+cd+'</div>';
}
function lastRunHtml(r){
  if(!r||!T(r.at))return '<span class="dim">henüz tarama yok</span>';
  var m=RUNST[r.status],nums=[];
  if(r.scraped!=null)nums.push(num(r.scraped)+' çekildi');
  if(r.ingested!=null)nums.push(num(r.ingested)+' işlendi');
  return esc(when(r.at))+' <span class="dim">('+esc(ago(r.at))+')</span> '+(m?pill(m[0],m[1]):(r.status?pill('',r.status):''))+(nums.length?' <span class="dim">'+esc(nums.join(' · '))+'</span>':'');
}
function siteRow(s){
  var id=s.site_id,name=s.display_name||id,busy=s.busy,bz=busy?(BUSY[busy]||busy):'',why=busy?'Çalışan iş var: '+bz:'';
  var off=busy?' disabled title="'+esc(why)+'"':'';
  var h='<li class="obs'+(busy?' isbusy':'')+'" data-site="'+esc(id)+'">'+
    '<div class="obsm"><b class="obsname">'+esc(name)+'</b>'+pill('','v'+(s.version==null?'?':s.version))+
    (s.hand_built?pill('heal','elle yapılmış'):'')+
    (busy?'<span class="pill run"><span class="dot pulse"></span>çalışıyor: '+esc(bz)+'</span>':'')+
    (s.search?pill('ok','🔎 arama var'):'<span class="hint">arama yok</span>')+'</div>'+
    '<div class="hint mono obsu">'+esc(id)+(s.base_url?' · '+esc(hostOf(s.base_url)):'')+'</div>'+
    scanHtml(s)+
    '<dl class="obsd">'+
      '<dt>Son tarama</dt><dd>'+lastRunHtml(s.last_run)+'</dd>'+
      '<dt>İçerik</dt><dd>'+esc(countsLine(s.counts))+'</dd>'+
      (s.archive?'<dt>Arşiv</dt><dd>'+esc(archiveLine(s.archive))+'</dd>':'')+
      ((s.providers&&s.providers.length)?'<dt>Oynatıcılar</dt><dd>'+s.providers.map(function(p){return '<span class="obchip">'+esc(p)+'</span>'}).join(' ')+'</dd>':
        s.providers_all?'<dt>Oynatıcılar</dt><dd><span class="obchip dim" title="yaml’da providers: listesi yok; tüm oynatıcılara izin verilir">Tümü</span></dd>':'')+
    '</dl>';
  if(ui.rename===id){
    h+='<div class="obsf"><label class="obfl">Görünen ad<input id="ob-rn" type="text" maxlength="60" value="'+esc(name)+'"></label>'+
      '<div class="srow"><button class="btn primary" data-ob="rnsave" data-site="'+esc(id)+'">Kaydet</button>'+
      '<button class="btn" data-ob="rncancel">Vazgeç</button></div></div>';
  }
  h+='<div class="obsa">'+
    '<button class="btn" data-ob="syaml" data-site="'+esc(id)+'">YAML</button>'+
    '<button class="btn" data-ob="shandoff" data-site="'+esc(id)+'">Devir notu</button>'+
    '<button class="btn" data-ob="srename" data-site="'+esc(id)+'"'+off+'>Ad değiştir</button>'+
    (s.can_rollback?'<button class="btn" data-ob="srollback" data-site="'+esc(id)+'"'+off+'>Geri al</button>':'')+
    '<button class="btn" data-ob="sscan" data-site="'+esc(id)+'"'+((s.auto_scan&&s.auto_scan.running)||busy?' disabled':'')+'>Şimdi tara</button>'+
    (s.archive?'<button class="btn" data-ob="sarchive" data-site="'+esc(id)+'"'+((s.auto_scan&&s.auto_scan.running)||busy?' disabled':'')+'>Arşivden bir parti al</button>':'')+
    '<button class="btn obdanger" data-ob="sdel" data-site="'+esc(id)+'"'+off+'>Sil</button>'+
    (busy?'<span class="hint">'+esc(why)+'</span>':'')+
  '</div></li>';
  return h;
}
function liveRow(l){
  var c=l.counts||{},le=l.last_event,m=le&&LIVEST[le.status];
  var last=!le?'<span class="dim">henüz tarama yok</span>':esc(when(le.at))+' <span class="dim">('+esc(ago(le.at))+')</span> '+(m?pill(m[0],m[1]):pill('',le.status||'?'))+
    ' <span class="dim">'+esc(le.status==='error'?(le.error||''):num(le.new)+' yeni · '+num(le.updated)+' güncellendi · '+num(le.deactivated)+' pasif')+'</span>';
  var st=l.running?pill('run','Taranıyor…'):l.enabled?pill('ok','Açık'):pill('','Kapalı');
  var next=!l.enabled?'planlı değil':l.running?'çalışıyor…':'<span data-next="'+esc(l.next_scan_at)+'">'+esc(countdown(l.next_scan_at))+'</span> <span class="dim">'+esc(when(l.next_scan_at))+'</span>';
  return '<li class="obs'+(l.running?' isbusy':'')+'" data-live="'+esc(l.source)+'">'+
    '<div class="obsm"><b class="obsname">'+esc(l.source)+'</b>'+pill('heal',l.label||'Canlı TV')+
    (l.running?'<span class="pill run"><span class="dot pulse"></span>çalışıyor: tarama</span>':'')+'</div>'+
    '<div class="hint mono obsu">'+esc(l.source)+' · canlı kanal listesi (scraper sitesi değil)</div>'+
    '<div class="obscan"><div class="srow"><b>Otomatik tarama</b>'+st+'</div>'+
      '<div class="srow"><span>Aralık</span><span>'+esc(l.interval_minutes||60)+' dk</span></div>'+
      '<div class="srow"><span>Sonraki tarama</span><span>'+next+'</span></div>'+
      '<div class="hint">Açma/kapama: Ayarlar › Canlı TV.</div></div>'+
    '<dl class="obsd">'+
      '<dt>Ülke</dt><dd>'+esc((l.countries||[]).join(', ').toUpperCase()||'-')+(l.languages&&l.languages.length?' <span class="dim">· dil '+esc(l.languages.join(', '))+'</span>':'')+'</dd>'+
      '<dt>Son tarama</dt><dd>'+last+'</dd>'+
      '<dt>Kanallar</dt><dd>'+esc(num(c.active)+' aktif · '+num(c.visible)+' görünür · '+num(c.hidden)+' gizli · '+num(c.inactive)+' pasif')+'</dd>'+
      '<dt>Sağlık</dt><dd>'+esc(num(c.ok)+' sağlıklı (m3u8) · '+num(c.youtube_only)+' yalnız YouTube · '+num(c.hidden)+' oynatılamaz')+'</dd>'+
    '</dl>'+
    '<div class="obsa"><button class="btn" data-ob="lscan" data-site="'+esc(l.source)+'"'+(l.running?' disabled':'')+'>Şimdi tara</button>'+
      '<button class="btn" data-ob="lsettings">Ayarlar › Canlı TV</button></div></li>';
}
function liveScanNow(b){
  b.disabled=true;
  call('/api/ops/live/scan',{method:'POST'}).then(function(r){
    toast(r&&r.started===false?'Canlı TV: zaten çalışıyor':'Canlı TV taraması başladı',r&&r.started===false?'bad':'');
    setTimeout(loadSites,1500);
  }).catch(function(e){toast('Başlatılamadı: '+msgOf(e),'bad');b.disabled=false});
}
function drawSites(){
  var el=$('ob-sites');
  if(!sites){
    el.innerHTML=sFail?'<div class="note bad">Site listesi alınamadı: '+esc(sFail)+' <button class="btn" data-ob="srefresh">Yeniden dene</button></div>':'<div class="empty">Yükleniyor…</div>';
    return;
  }
  if(!sites.length&&!lives.length){el.innerHTML='<div class="pempty">Kayıtlı site yok.</div>';return}
  var warn=sMeta.any_enabled===false?'<div class="note warnbar" role="alert"><b>Otomatik tarama kapalı</b>Hiçbir kaynak kendiliğinden taranmıyor; yeni içerik yalnızca elle “Şimdi tara” ile gelir. Aşağıdan bir kaynağı açın.</div>':'';
  var lim=sMeta.limits;
  var hint='<div class="hint obscanh">Kontrol '+esc(sMeta.tick_seconds||60)+' sn’de bir yapılır; değişiklikler yeniden başlatma gerektirmez.'+
    (lim?' Aralıklar '+esc(hoursText(lim.min_interval_hours))+' – '+esc(hoursText(lim.max_interval_hours))+' arasındadır.':'')+'</div>';
  el.innerHTML=warn+'<ul class="obsl">'+sites.map(siteRow).join('')+lives.map(liveRow).join('')+'</ul>'+hint;
}
function scanEditing(){var a=document.activeElement,r=$('ob-sites');return !!(a&&r&&r.contains(a)&&a.matches('select,input[type=number]'))}
function renderSites(){if(ui.rename||scanEditing())return;drawSites()}
function loadSites(){
  sLoading=true;
  return call(SITES+'/manage').then(function(d){sites=(d&&d.sites)||[];lives=(d&&d.live_sources)||[];sMeta={any_enabled:d&&d.any_enabled,limits:d&&d.limits,tick_seconds:d&&d.tick_seconds};sFail=false}).catch(function(e){sFail=msgOf(e)})
    .then(function(){sLoading=false;renderSites()});
}
function patchSite(site,fields,msg){
  var p={sites:{}};p.sites[site]=fields;
  return call('/api/ops/settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)})
    .then(function(){toast(msg||'Kaydedildi');changed()})
    .catch(function(e){toast('Kaydedilemedi: '+msgOf(e),'bad')}).then(loadSites);
}
function archiveLine(a){
  // zamanlama = sitenin "Otomatik tarama" düğmesi + "Aralık" (ayrı arşiv düğmesi yok)
  var m=+a.interval_minutes||0,iv=m>=60?(Math.round(m/6)/10)+' sa':m+' dk';
  var p=[a.enabled?'otomatik tarama ile açık (her '+iv+(a.next_batch_at?', sonraki '+a.next_batch_at:'')+')':'otomatik tarama kapalı'];
  if(a.series)p.push('dizi '+a.series.processed+'/'+a.series.total+(a.series.index_at?' (dizin '+a.series.index_at+')':''));
  if(a.movies)p.push('film '+a.movies.processed+'/'+a.movies.total+' · sayfa imleci '+a.movies.next_page+(a.movies.done?' (bitti)':''));
  p.push('son parti '+(a.last_batch_at||'yok'));
  if(a.last_error)p.push('son hata: '+a.last_error);
  if(a.last_drift)p.push('sapma: '+(a.last_drift.part||'')+' ('+(a.last_drift.heal||'')+')');
  return p.join(' · ');
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
      h+='<div class="hint">Salt okunur; sırlar maskelidir. Değişiklik elle (geliştirici) ya da heal ile yapılır.</div>';
    }
  } else if(M.kind==='handoff'){
    h='<div class="lhead"><h3 id="ob-mt" class="grow">'+esc(name)+' · devir notu</h3>'+
      (M.state==='ok'&&M.text?'<button class="btn" data-ob="mcopyh">Kopyala</button>':'')+'<button class="btn" data-ob="mclose">Kapat</button></div>';
    if(M.state==='loading')h+='<div class="empty">Yükleniyor…</div>';
    else if(M.state==='err')h+='<div class="note bad">Devir notu alınamadı: '+esc(M.err)+'</div>';
    else {
      h+=M.text?'<pre class="obyaml obmy mono" tabindex="0">'+esc(M.text)+'</pre>':'<div class="pempty">Bu site için henüz devir notu yok. Onarım uygulanınca ve ilk taramalardan sonra oluşur.</div>';
      h+='<div class="hint">Salt okunur. Onarım ajanı geçmişi bu nottan okur; elle değiştirilmez.</div>';
    }
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
    changed();return loadSites();
  }).catch(function(e){
    m.busy=false;m.err=e&&e.status===409?'Bu site için çalışan bir iş var; bitince tekrar dene. ('+msgOf(e)+')':'Silinemedi: '+msgOf(e);
    if(M===m)renderModal();
  });
}
function onChange(ev){
  var t=ev&&ev.target;if(!t)return;
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
  if(!t.id)return;
  if(t.id==='ob-purge'&&M&&M.kind==='del'){M.purge=!!t.checked;$('ob-purge-line').textContent=purgeLine(siteById(M.site),M.purge)}
}
function bind(){
  $('ob-root').addEventListener('change',onChange);
  $('ob-root').addEventListener('keydown',function(ev){
    var t=ev.target,id=t&&t.id;
    if(ev.key==='Escape'){
      if(M){closeModal();return}
      if(ui.rename){ui.rename=null;drawSites()}
      return;
    }
    if(ev.key!=='Enter'||ev.shiftKey||ev.isComposing)return;
    if(id==='ob-rn'&&ui.rename){ev.preventDefault();doRename(ui.rename)}
  });
  $('ob-root').addEventListener('click',function(ev){
    var b=ev.target.closest('[data-ob]');if(!b||b.disabled)return;
    var a=b.getAttribute('data-ob'),site=b.getAttribute('data-site');
    if(a==='srefresh'){sFail=false;sites=null;drawSites();loadSites()}
    else if(a==='srename'){ui.rename=ui.rename===site?null:site;drawSites();if(ui.rename){var rn=$('ob-rn');if(rn&&rn.focus)rn.focus()}}
    else if(a==='rncancel'){ui.rename=null;drawSites()}
    else if(a==='rnsave'){doRename(site)}
    else if(a==='srollback'){rollbackSite(site)}
    else if(a==='sscan'){scanNow(site,b)}
    else if(a==='lscan'){liveScanNow(b)}
    else if(a==='lsettings'){var tb=$('tab-btn-settings');if(tb)tb.click()}
    else if(a==='sarchive'){archiveNow(site,b)}
    else if(a==='syaml'){openYaml(site)}
    else if(a==='shandoff'){openHandoff(site)}
    else if(a==='sdel'){openDel(site)}
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
  var nx=$('ob-sites')&&$('ob-sites').querySelectorAll('[data-next]');
  if(nx)nx.forEach(function(el){var t=countdown(el.getAttribute('data-next'));if(el.textContent!==t)el.textContent=t});
  if(tickN%20===0&&!M&&!sLoading)loadSites(); // sonraki/son tarama ve ayar değişiklikleri tazelenir
  if(tickN%4===0&&sites&&sites.some(function(s){return s.busy||(s.auto_scan&&s.auto_scan.running)})&&!M&&!sLoading)loadSites();
}

/* ---- sekme kancası ---- */
function onShow(){
  build();
  if(!timer)timer=setInterval(tick,1000);
  try{if(/^#onboard\//.test(location.hash))history.replaceState(null,'','#onboard')}catch(e){}   /* eski taslak bağlantıları */
  loadSites();
}
window.dzTabHooks=window.dzTabHooks||{};
window.dzTabHooks.onboard=onShow;
if(!$('tab-onboard').classList.contains('hidden'))onShow();
})();
