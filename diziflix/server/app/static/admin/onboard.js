/* "Siteler" sekmesi (#onboard): site yönetimi + pi ajanıyla site ekleme/düzenleme (/api/ops/sites/*, /api/ops/onboard/*).
   Ana ekran: site LİSTESİ (elle yapılmış olanlar dahil; Düzenle / YAML / Devir notu / Ad değiştir / Geri al / Ayarlar / Sil),
   "+ Yeni site" paneli (adres + not + Başlat), Taslaklar; sağlık kartı yalnız sorunda (çalışan iş varsa Aç / İptal).
   Taslak ekranı (#onboard/<id>): ÜSTTE yapışkan Kaydet şeridi (site_id, ad, Kaydet -> onay paneli: tara / otomatik tarama / force),
   genel durum + adım adım ilerleme (draft.pipeline) + "uygulamada ne görünecek", ajan günlüğü | yaml taslağı yan yana (altında sohbet).
   Ajan bir şey soruyorsa (draft.question_data) üstte "Ajan soruyor" kartı: düğmeler/ipucu girişi mevcut mesaj ucuna kalıp metin gönderir
   ("Sitede yok, atla: <alan>" / "Var: <ipucu>" / "Önerini uygula"); kind=engine_gap "Sistemde eksik özellik" kartı (kopyalanabilir).
   Sorunlu adımlarda tek tıkla "Ajan düzeltsin" / "Varsa al, yoksa atla" (bilgi alanı korunur; gönderilen cevap "Sitede yok, atla: <alan>") (step.actions[].message sunucudan hazır gelir).
   Sekme gizliyken ağ isteği yok. */
(function(){
'use strict';
var $=function(i){return document.getElementById(i)};
var BASE='/api/ops/onboard',SITES='/api/ops/sites';
var ID_RE=/^od_[0-9a-f]{12}$/,SITE_RE=/^[a-z][a-z0-9_]{1,31}$/;
var built=false,H=null,hFail=false,hAt=0,drafts=null,dFail=false,sites=null,sFail=false,sLoading=false,newOpen=false,ui={edit:null,rename:null},M=null;
var cur=null,D=null,evs=[],evN=0,sig='',yamlKey=null,fetching=false,fetchP=null,tickN=0,timer=null;
var sidTouched=false,dnTouched=false,savedInfo=null,starting=false,estarting=false,sending=false,saving=false,openSteps={};
var svOpen=false,svErr='',forceSrv=false,opt={scan:true,enable:false},panelKey='',askKey='';

function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
function T(t){return t?new Date(t).getTime():0}
function ago(t){if(!T(t))return '-';var d=Math.max(0,(Date.now()-T(t))/1000);
  if(d<60)return Math.floor(d)+' sn önce';if(d<3600)return Math.floor(d/60)+' dk önce';
  if(d<86400)return Math.floor(d/3600)+' sa önce';return Math.floor(d/86400)+' g önce'}
function until(t){if(!T(t))return '';var d=(T(t)-Date.now())/1000;
  if(d<=0)return 'şimdi';if(d<3600)return Math.ceil(d/60)+' dk sonra';
  if(d<86400)return Math.round(d/3600)+' sa sonra';return Math.round(d/86400)+' g sonra'}
function when(t){return T(t)?new Date(t).toLocaleString('tr-TR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}):'-'}
function dur(s){if(s==null)return '-';s=+s;if(s<60)return (Math.round(s*10)/10)+' sn';return Math.floor(s/60)+' dk '+Math.round(s%60)+' sn'}
function num(n){return n==null?'-':(+n).toLocaleString('tr-TR')}
function pill(cls,txt){return '<span class="pill '+cls+'">'+esc(txt)+'</span>'}
function clip(s,n){s=String(s==null?'':s);return s.length>n?s.slice(0,n-1)+'…':s}
function hostOf(u){try{return new URL(u).host}catch(e){return String(u||'')}}
function str(v){return typeof v==='string'?v:v==null?'':JSON.stringify(v)}
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

/* ---- durum sözlükleri ---- */
var ST={running:['run','Çalışıyor'],needs_input:['warn','Soru bekliyor'],ready:['ok','Hazır'],failed:['bad','Başarısız'],cancelled:['','İptal'],saved:['heal','Kaydedildi']};
function stPill(s){
  var m=ST[s]||['',s||'?'];
  return s==='running'?'<span class="pill run"><span class="dot pulse"></span>'+esc(m[1])+'</span>':pill(m[0],m[1]);
}
/* adım durumu: [css, simge, ad] */
var SS={pending:['pending','⏳','Bekliyor'],running:['running','◔','Çalışıyor'],ok:['ok','✓','Tamam'],warn:['warn','⚠','Dikkat'],fail:['fail','✗','Sorun var'],skipped:['skipped','–','Atlandı']};
/* genel durum: [css, simge] */
var OV={idle:['','⏳'],running:['run','◔'],ok:['ok','✓'],warn:['warn','⚠'],fail:['bad','✗']};
var BUSY={scan:'tarama',heal:'düzeltme',onboard:'site ekleme',finder:'kaynak arama'};
var RUNST={success:['ok','Başarılı'],ok:['ok','Başarılı'],partial:['warn','Kısmi'],error:['bad','Hata'],failed:['bad','Hata'],running:['run','Çalışıyor']};

/* ---- API adaptörleri (uç şekli değişirse yalnızca burası) ---- */
function listOf(r){return (r&&(r.drafts||r.items))||[]}
function flag(h,keys){
  for(var i=0;i<keys.length;i++){
    var v=h[keys[i]];if(v===undefined||v===null)continue;
    if(typeof v==='object')return v.ok!==undefined?!!v.ok:v.found!==undefined?!!v.found:v.exists!==undefined?!!v.exists:true;
    return !!v;
  }
  return null;
}
/* çalışan site ekleme işi: {draft_id,url,status}|null (eski şekil: etkinlik kaydı/true -> kimliksiz); bitmiş durum = çalışmıyor (yarış) */
function runningOf(h){
  var r=h&&h.running;if(!r)return null;
  if(typeof r!=='object')return {id:'',url:'',status:''};
  if(r.status&&r.status!=='running')return null;
  var id=r.draft_id||r.id||'';
  return {id:ID_RE.test(id)?id:'',url:r.url||'',status:r.status||''};
}
/* sağlık: yalnız sorun listesi (Başlat'ı pasifleştirir; boşsa kart görünmez); çalışan iş ayrı (runningOf) */
function healthProblems(h){
  var probs=[];
  function chk(ok,prob){if(ok===false)probs.push(prob)}
  if(h.enabled===false)probs.push('site ekleme kapalı (ONBOARD_ENABLED=0)');
  chk(flag(h,['pi','pi_ok','pi_found','pi_available']),'pi bulunamadı (ONBOARD_PI_BIN)');
  chk(flag(h,['skill','skill_ok','skill_found']),'skill dosyası yok (server/pi/skills/diziflix-site-onboarding)');
  chk(flag(h,['extension','extension_ok','extension_found']),'extension dosyası yok (server/pi/extensions/diziflix-onboard.ts)');
  if(h.model!==undefined||h.model_ok!==undefined)chk(h.model_ok!==undefined?!!h.model_ok:!!h.model,'model ayarlı değil (ONBOARD_MODEL / SCRAPER_HEAL_MODEL)');
  var llm=h.llm||h.llm_health;
  if(llm&&typeof llm==='object'){
    var ok=llm.status?llm.status==='valid':(llm.ok!==undefined?!!llm.ok:null);
    chk(ok,'LLM hesabı geçerli değil'+(llm.message?': '+llm.message:''));
  }
  (h.problems||[]).forEach(function(p){var t=str(p);if(t&&probs.indexOf(t)<0)probs.push(t)});
  if(h.reason&&!probs.length&&h.ok===false&&!runningOf(h))probs.push(str(h.reason));
  if((h.ready===false||h.ok===false)&&!probs.length&&!runningOf(h))probs.push('sağlık kontrolü başarısız');
  return probs;
}
function nextOffset(d,list){
  var v=d.events_total!=null?d.events_total:d.next_after!=null?d.next_after:null;
  return typeof v==='number'?v:evN+list.length;
}
function pipeOf(d){var p=d&&d.pipeline;return p&&typeof p==='object'&&Array.isArray(p.steps)&&p.steps.length?p:null}
function overallOf(d){var p=pipeOf(d);return (p&&p.overall)||(d&&d.overall)||null}
function siteById(id){var l=sites||[];for(var i=0;i<l.length;i++)if(l[i].site_id===id)return l[i];return null}

/* ---- iskelet ---- */
function build(){
  if(built)return;built=true;
  $('ob-root').innerHTML=
  '<div id="ob-listv" class="obl">'+
    '<div class="obhead"><h2 class="grow" style="margin:0">Siteler</h2>'+
      '<button class="btn primary" id="ob-newbtn" data-ob="new" aria-expanded="false">+ Yeni site</button></div>'+
    '<section class="card hidden" id="ob-health"></section>'+
    '<section class="card hidden" id="ob-newp"><h2>Yeni site</h2>'+
      '<form id="ob-form" class="obf" autocomplete="off">'+
        '<label class="obfl">Site adresi<input id="ob-url" type="url" required placeholder="https://ornek-site.com" inputmode="url" spellcheck="false"></label>'+
        '<label class="obfl">Not <small>(isteğe bağlı)</small><input id="ob-hint" type="text" maxlength="500" placeholder="örn. yalnızca filmler; liste sayfası /filmler"></label>'+
        '<div class="srow"><button type="submit" class="btn primary" id="ob-start">Başlat</button><button type="button" class="btn" data-ob="newclose">Vazgeç</button><span id="ob-why" class="hint"></span></div>'+
      '</form></section>'+
    '<section><div id="ob-sites"></div></section>'+
    '<section><h2>Taslaklar</h2><div id="ob-drafts"></div></section>'+
  '</div>'+
  '<div id="ob-detv" class="hidden">'+
    '<div class="obtop" id="ob-top">'+
      '<div class="obbar"><button class="btn" id="ob-back" data-ob="back">‹ Liste</button>'+
        '<span class="grow"></span>'+
        '<span id="ob-savewhy" class="hint obwhy"></span>'+
        '<button class="btn hidden" id="ob-cancel" data-ob="cancel">İptal</button>'+
        '<button class="btn primary" id="ob-savebtn" data-ob="save" disabled>Kaydet</button></div>'+
      '<div id="ob-sv-panel" class="hidden"></div>'+
    '</div>'+
    '<div class="obhd"><b id="ob-host" class="obhost"></b><span id="ob-st"></span><span id="ob-time" class="mono num dim"></span></div>'+
    '<div class="obids">'+
      '<label class="obfl">Site kimliği<input id="ob-sid" type="text" maxlength="32" placeholder="ornek_site" spellcheck="false" autocapitalize="off"></label>'+
      '<label class="obfl">Görünen ad <small>(isteğe bağlı)</small><input id="ob-dn" type="text" maxlength="60" placeholder="Örnek Site"></label>'+
    '</div>'+
    '<div id="ob-sid-err" class="err hint"></div>'+
    '<div id="ob-sv-info"></div>'+
    '<div id="ob-note"></div>'+
    '<div id="ob-ask"></div>'+
    '<div id="ob-headline"></div>'+
    '<section><h2>İlerleme</h2><div id="ob-steps"></div></section>'+
    '<div class="obpair">'+
      '<section class="obcol">'+
        '<div class="lhead"><h2 class="grow">Ajan günlüğü</h2><button class="btn" id="ob-copylog" data-ob="copylog">Günlüğü kopyala</button></div>'+
        '<div id="ob-log" class="oblog" role="log" aria-live="off" tabindex="0"></div>'+
        '<div class="obchat"><textarea id="ob-fb" rows="2" maxlength="2000" aria-label="Ajana mesaj" placeholder="Ajana yaz (Enter = gönder, Shift+Enter = yeni satır)"></textarea>'+
          '<button class="btn primary" id="ob-send" data-ob="send">Gönder</button></div>'+
        '<div id="ob-fbh" class="hint"></div>'+
      '</section>'+
      '<section class="obcol" id="ob-yaml"></section>'+
    '</div>'+
  '</div>'+
  '<div id="ob-modal" class="obmodal hidden"></div>';
  bind();
}

/* ---- ana ekran: sağlık, yeni site paneli ---- */
function renderHealth(){
  var el=$('ob-health'),html='';
  if(!H){
    if(hFail)html='<div class="note bad" style="margin:0"><b>Sağlık kontrolü alınamadı:</b> '+esc(hFail)+'</div>';
  } else {
    var run=runningOf(H),probs=healthProblems(H);
    if(run)html+='<h2>Çalışan iş</h2><div class="obrun">Şu an '+esc(run.url?hostOf(run.url)+' için bir':'bir')+' site ekleme işi çalışıyor.</div>'+
      (run.id?'<div class="srow"><button class="btn" data-ob="open" data-id="'+esc(run.id)+'">Aç</button>'+
        '<button class="btn" data-ob="hcancel" data-id="'+esc(run.id)+'">İptal</button></div>':'');
    if(probs.length)html+='<h2>Başlatılamıyor</h2><ul class="obhl">'+probs.map(function(p){return '<li class="bad"><span class="ci">✗</span><span>'+esc(clip(p,200))+'</span></li>'}).join('')+'</ul>';
  }
  if(html)html+='<div class="srow"><button class="btn" data-ob="hrefresh">Yeniden kontrol et</button></div>';
  el.innerHTML=html;
  el.classList.toggle('hidden',!html);
  updateStart();
}
function startBlock(){
  if(starting||estarting)return 'başlatılıyor…';
  if(!H)return hFail?'sağlık kontrolü alınamadı':'sağlık kontrolü bekleniyor';
  if(runningOf(H))return 'başka bir site ekleme işi çalışıyor';
  var p=healthProblems(H);
  return p.length?p[0]:'';
}
function updateStart(){
  var why=startBlock(),b=$('ob-start');
  if(b){b.disabled=!!why;$('ob-why').textContent=starting?'başlatılıyor…':(!H&&!hFail?'kontrol ediliyor…':'')}
  var e=$('ob-estart');if(e){e.disabled=!!why;var w=$('ob-ewhy');if(w)w.textContent=why}
}
function applyNew(){
  $('ob-newp').classList.toggle('hidden',!newOpen);
  $('ob-newbtn').setAttribute('aria-expanded',newOpen?'true':'false');
  if(newOpen){var u=$('ob-url');if(u&&u.focus)u.focus()}
}

/* ---- ana ekran: site listesi ---- */
function countsLine(c){
  c=c||{};
  return num(c.series||0)+' dizi · '+num(c.movies||0)+' film · '+num(c.episodes||0)+' bölüm · '+num(c.with_sources||0)+' kaynaklı';
}
function autoLine(a){
  a=a||{};
  if(!a.enabled)return 'kapalı';
  var h=+a.interval_hours,iv=h>0?(h<1?Math.round(h*60)+' dk':(Math.round(h*10)/10)+' sa'):'';
  return 'açık'+(iv?' · her '+iv:'')+(a.next_scan_at?' · sonraki '+when(a.next_scan_at)+' ('+until(a.next_scan_at)+')':'');
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
    '<dl class="obsd">'+
      '<dt>Otomatik tarama</dt><dd>'+esc(autoLine(s.auto_scan))+'</dd>'+
      '<dt>Son tarama</dt><dd>'+lastRunHtml(s.last_run)+'</dd>'+
      '<dt>İçerik</dt><dd>'+esc(countsLine(s.counts))+'</dd>'+
      ((s.providers&&s.providers.length)?'<dt>Oynatıcılar</dt><dd>'+s.providers.map(function(p){return '<span class="obchip">'+esc(p)+'</span>'}).join(' ')+'</dd>':'')+
    '</dl>';
  if(ui.edit===id){
    h+='<div class="obsf"><label class="obfl">Ne değişsin?<textarea id="ob-ehint" rows="2" maxlength="500" placeholder="örn. afişler yanlış geliyor; film listesi /filmler sayfasında"></textarea></label>'+
      '<div class="srow"><button class="btn primary" id="ob-estart" data-ob="estart" data-site="'+esc(id)+'">Ajanı başlat</button>'+
      '<button class="btn" data-ob="ecancel">Vazgeç</button><span id="ob-ewhy" class="hint"></span></div></div>';
  }
  if(ui.rename===id){
    h+='<div class="obsf"><label class="obfl">Görünen ad<input id="ob-rn" type="text" maxlength="60" value="'+esc(name)+'"></label>'+
      '<div class="srow"><button class="btn primary" data-ob="rnsave" data-site="'+esc(id)+'">Kaydet</button>'+
      '<button class="btn" data-ob="rncancel">Vazgeç</button></div></div>';
  }
  h+='<div class="obsa">'+
    '<button class="btn" data-ob="sedit" data-site="'+esc(id)+'"'+off+'>Düzenle</button>'+
    '<button class="btn" data-ob="syaml" data-site="'+esc(id)+'">YAML</button>'+
    '<button class="btn" data-ob="shandoff" data-site="'+esc(id)+'">Devir notu</button>'+
    '<button class="btn" data-ob="srename" data-site="'+esc(id)+'"'+off+'>Ad değiştir</button>'+
    (s.can_rollback?'<button class="btn" data-ob="srollback" data-site="'+esc(id)+'"'+off+'>Geri al</button>':'')+
    '<button class="btn" data-ob="goto-settings">Ayarlar</button>'+
    '<button class="btn obdanger" data-ob="sdel" data-site="'+esc(id)+'"'+off+'>Sil</button>'+
    (busy?'<span class="hint">'+esc(why)+'</span>':'')+
  '</div></li>';
  return h;
}
function drawSites(){
  var el=$('ob-sites');
  if(!sites){
    el.innerHTML=sFail?'<div class="note bad">Site listesi alınamadı: '+esc(sFail)+' <button class="btn" data-ob="srefresh">Yeniden dene</button></div>':'<div class="empty">Yükleniyor…</div>';
    return;
  }
  if(!sites.length){el.innerHTML='<div class="pempty">Kayıtlı site yok. “+ Yeni site” ile bir adres ekleyerek başla.</div>';return}
  el.innerHTML='<ul class="obsl">'+sites.map(siteRow).join('')+'</ul>';
  updateStart();
}
/* satır içi form açıkken yazılanı silmemek için yoklama yeniden çizmez */
function renderSites(){if(ui.edit||ui.rename)return;drawSites()}
function loadSites(){
  sLoading=true;
  return call(SITES+'/manage').then(function(d){sites=(d&&d.sites)||[];sFail=false}).catch(function(e){sFail=msgOf(e)})
    .then(function(){sLoading=false;renderSites()});
}

function renderDrafts(){
  var el=$('ob-drafts');
  if(!drafts){
    el.innerHTML=dFail?'<div class="note bad">Liste alınamadı: '+esc(dFail)+' <button class="btn" data-ob="lrefresh">Yeniden dene</button></div>':'<div class="empty">Yükleniyor…</div>';
    return;
  }
  if(!drafts.length){el.innerHTML='<div class="pempty">Henüz taslak yok. “+ Yeni site” ile bir adres ekleyerek ya da bir sitede “Düzenle” ile başlat.</div>';return}
  el.innerHTML='<ul class="obdr">'+drafts.map(function(d){
    var run=d.status==='running',o=d.overall||null,oc=o&&OV[o.state]?OV[o.state][0]:'';
    return '<li class="obdi"><div class="obdm"><a href="#onboard/'+esc(d.id)+'" class="obu" title="'+esc(d.url)+'">'+esc(hostOf(d.url))+'</a> '+stPill(d.status)+
      (d.mode==='edit'&&d.edit_site_id?pill('heal','düzenleme: '+d.edit_site_id):'')+
      '<span class="hint" title="'+esc(T(d.created_at)?new Date(d.created_at).toLocaleString('tr-TR'):'')+'">'+esc(when(d.created_at))+' · '+esc(ago(d.created_at))+'</span></div>'+
      '<div class="hint mono obdu">'+esc(clip(d.url||'',90))+'</div>'+
      (o&&o.headline?'<div class="obdh '+oc+'">'+esc(clip(o.headline,200))+'</div>':'')+
      '<div class="act"><button class="btn" data-ob="open" data-id="'+esc(d.id)+'">Aç</button>'+
      '<button class="btn" data-ob="del" data-id="'+esc(d.id)+'"'+(run?' disabled title="Çalışırken silinemez"':'')+'>Sil</button></div></li>'}).join('')+'</ul>';
}
function loadHealth(){
  return call(BASE+'/health').then(function(d){H=d;hFail=false;hAt=Date.now()}).catch(function(e){hFail=msgOf(e);H=null})
    .then(renderHealth);
}
function loadList(){
  return call(BASE+'/').then(function(d){drafts=listOf(d);dFail=false}).catch(function(e){dFail=msgOf(e)}).then(renderDrafts);
}
function startFailed(e){
  toast('Başlatılamadı: '+msgOf(e),'bad');
  if(e&&(e.code==='already_running'||e.status===409))loadHealth(); // çalışan işi kartta göster (Aç / İptal)
}
function startJob(){
  var url=$('ob-url').value.trim(),hint=$('ob-hint').value.trim();
  if(url&&!/^[a-z][a-z0-9+.-]*:\/\//i.test(url))url='https://'+url;
  try{var u=new URL(url);if(u.protocol!=='http:'&&u.protocol!=='https:')throw 0}catch(e){toast('Geçerli bir adres gir (https://…)','bad');return}
  starting=true;updateStart();
  post(BASE+'/',{url:url,hint:hint||undefined}).then(function(r){
    var d=r&&(r.draft||r);
    if(d&&d.id){toast('Ajan başlatıldı');$('ob-url').value='';$('ob-hint').value='';newOpen=false;applyNew();openDraft(d.id)}
    else if(r&&(r.started===false||r.reason==='already_running')){toast('Zaten çalışan bir site ekleme işi var','bad');loadHealth()}
    else toast('Başlatılamadı','bad');
  }).catch(startFailed).then(function(){starting=false;updateStart();loadList()});
}
function startEdit(site){
  var hint=$('ob-ehint').value.trim();
  if(!hint){toast('Ne değişmesini istediğini yaz','bad');return}
  var why=startBlock();if(why){toast(why,'bad');return}
  estarting=true;updateStart();
  post(BASE+'/',{mode:'edit',site_id:site,hint:hint}).then(function(r){
    var d=r&&(r.draft||r);
    if(d&&d.id){toast('Ajan başlatıldı');ui.edit=null;drawSites();openDraft(d.id)}
    else if(r&&(r.started===false||r.reason==='already_running')){toast('Zaten çalışan bir site ekleme işi var','bad');loadHealth()}
    else toast('Başlatılamadı','bad');
  }).catch(startFailed).then(function(){estarting=false;updateStart();loadList()});
}
function delDraft(id){
  if(!confirm('Bu taslak silinsin mi?'))return;
  call(BASE+'/'+enc(id),{method:'DELETE'}).then(function(){toast('Taslak silindi');loadList()})
    .catch(function(e){toast('Silinemedi: '+msgOf(e),'bad')});
}
function cancelRunning(id){
  post(BASE+'/'+enc(id)+'/cancel').then(function(){toast('İptal istendi')})
    .catch(function(e){var done=e&&e.code==='not_running';toast(done?'İş zaten bitmiş':'İptal edilemedi: '+msgOf(e),done?'':'bad')})
    .then(function(){loadHealth();loadList()});
}

/* ---- site eylemleri: yeniden adlandır, geri al, YAML, sil ---- */
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
      h+='<div class="hint">Salt okunur; sırlar maskelidir. Değiştirmek için “Düzenle”yi kullan.</div>';
    }
  } else if(M.kind==='handoff'){
    h='<div class="lhead"><h3 id="ob-mt" class="grow">'+esc(name)+' · devir notu</h3>'+
      (M.state==='ok'&&M.text?'<button class="btn" data-ob="mcopyh">Kopyala</button>':'')+'<button class="btn" data-ob="mclose">Kapat</button></div>';
    if(M.state==='loading')h+='<div class="empty">Yükleniyor…</div>';
    else if(M.state==='err')h+='<div class="note bad">Devir notu alınamadı: '+esc(M.err)+'</div>';
    else {
      h+=M.text?'<pre class="obyaml obmy mono" tabindex="0">'+esc(M.text)+'</pre>':'<div class="pempty">Bu site için henüz devir notu yok. Yeni site kaydedilince, düzenleme veya onarım uygulanınca ve ilk taramalardan sonra oluşur.</div>';
      h+='<div class="hint">Salt okunur. Düzenleme ve onarım ajanı geçmişi bu nottan okur; elle değiştirilmez.</div>';
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

/* ---- görünüm geçişi ---- */
function setHash(id){try{history.replaceState(null,'',id?'#onboard/'+id:'#onboard')}catch(e){}}
function show(detail){$('ob-listv').classList.toggle('hidden',detail);$('ob-detv').classList.toggle('hidden',!detail)}
function showList(){
  cur=null;D=null;show(false);setHash(null);renderDrafts();renderSites();loadList();loadSites();
  if(!H||Date.now()-hAt>30000||runningOf(H))loadHealth();
}
function resetDetail(){
  evs=[];evN=0;sig='';yamlKey=null;sidTouched=false;dnTouched=false;savedInfo=null;sending=false;saving=false;openSteps={};
  svOpen=false;svErr='';forceSrv=false;opt={scan:true,enable:false};panelKey='';askKey='';
  $('ob-log').innerHTML='';$('ob-fb').value='';$('ob-sid').value='';$('ob-dn').value='';$('ob-sid').readOnly=false;$('ob-dn').disabled=false;
  $('ob-sid-err').textContent='';
  ['ob-yaml','ob-steps','ob-headline','ob-sv-info','ob-sv-panel','ob-note','ob-ask'].forEach(function(i){$(i).innerHTML=''});
  $('ob-sv-panel').classList.add('hidden');
  $('ob-savebtn').disabled=true;$('ob-savebtn').textContent='Kaydet';$('ob-savewhy').textContent='yükleniyor…';
  $('ob-host').textContent='';$('ob-st').innerHTML='';$('ob-time').textContent='';$('ob-cancel').classList.add('hidden');
}
function openDraft(id){
  if(!ID_RE.test(id)){showList();return}
  if(cur===id&&D){show(true);setHash(id);loadDraft();return}
  cur=id;D=null;resetDetail();show(true);setHash(id);
  $('ob-host').textContent=id;$('ob-steps').innerHTML='<div class="empty">Yükleniyor…</div>';
  loadDraft();
}

/* ---- günlük ---- */
function evTime(e){var t=e.t||e.ts||e.at;return T(t)?new Date(t).toLocaleTimeString('tr-TR',{hour:'2-digit',minute:'2-digit',second:'2-digit'}):''}
function evEl(e){
  if(!e||typeof e!=='object')return null;
  var k=e.kind||e.type,d=document.createElement('div'),tm='<span class="obt">'+esc(evTime(e))+'</span>',txt;
  if(k==='tool'){
    d.className='obe tool';
    d.innerHTML=tm+'<span class="obb"><span class="obi">🔧</span><b class="mono">'+esc(e.name||'?')+'</b>'+
      (e.args_short?' <span class="mono dim">'+esc(clip(str(e.args_short),240))+'</span>':'')+'</span>';
  } else if(k==='tool_result'){
    var ok=e.ok!==false&&!e.error;
    d.className='obe res '+(ok?'ok':'bad');
    d.innerHTML=tm+'<span class="obb"><span class="obi">'+(ok?'✓':'✗')+'</span><b class="mono">'+esc(e.name||'?')+'</b>'+
      ((e.summary||e.error)?' <span class="dim">'+esc(clip(str(e.summary||e.error),300))+'</span>':'')+'</span>';
  } else if(k==='say'){
    d.className='obe say';
    d.innerHTML=tm+'<span class="obb bt">'+esc(e.text||'')+'</span>';
  } else if(k==='user'){
    d.className='obe user';
    d.innerHTML='<span class="obb bt">'+esc(e.text||'')+'</span><span class="obt">'+esc(evTime(e))+'</span>';
  } else if(k==='ask'){
    d.className='obe say ask';
    d.innerHTML=tm+'<span class="obb bt"><b>Ajan soruyor:</b> '+esc(e.text||'')+'</span>';
  } else if(k==='status'&&e.status==='auto_fix'){
    d.className='obe info warn';
    d.innerHTML=tm+'<span class="obb"><span class="obi">↻</span>'+esc(clip(str(e.text||'Otomatik düzeltme turu'),400))+'</span>';
  } else if(k==='submit'){
    d.className='obe info '+(e.passed?'ok':'warn');
    d.innerHTML=tm+'<span class="obb">Taslak teslim edildi · '+(e.passed?'kriterler sağlandı':'kriterler sağlanmadı')+
      (e.errors?' · '+num(e.errors)+' hata':'')+(e.warnings?' · '+num(e.warnings)+' uyarı':'')+'</span>';
  } else {
    txt=e.text||e.message||e.error||e.summary;
    if(!txt)return null; // tanınmayan, metinsiz olay: gösterme
    var bad=k==='error'||e.level==='error'||!!e.error;
    d.className='obe info'+(bad?' bad':'');
    d.innerHTML=tm+'<span class="obb">'+(k?'<span class="tag'+(bad?' bad':'')+'">'+esc(k)+'</span> ':'')+esc(clip(str(txt),600))+'</span>';
  }
  return d;
}
/* "Günlüğü kopyala": tüm olaylar düz metin (kırpmasız) */
function evLine(e){
  if(!e||typeof e!=='object')return '';
  var k=e.kind||e.type,tm=evTime(e),p=tm?'['+tm+'] ':'',txt;
  if(k==='tool')return p+'🔧 '+(e.name||'?')+(e.args_short?' '+str(e.args_short):'');
  if(k==='tool_result'){var ok=e.ok!==false&&!e.error;return p+(ok?'✓ ':'✗ ')+(e.name||'?')+((e.summary||e.error)?' — '+str(e.summary||e.error):'')}
  if(k==='say')return p+'Ajan: '+(e.text||'');
  if(k==='ask')return p+'Ajan soruyor: '+(e.text||'');
  if(k==='user')return p+'Sen: '+(e.text||'');
  if(k==='submit')return p+'Taslak teslim edildi · '+(e.passed?'kriterler sağlandı':'kriterler sağlanmadı')+(e.errors?' · '+e.errors+' hata':'')+(e.warnings?' · '+e.warnings+' uyarı':'');
  txt=e.text||e.message||e.error||e.summary;
  return txt?p+(k?'['+k+'] ':'')+str(txt):'';
}
function logText(){return evs.map(evLine).filter(function(x){return x}).join('\n')}
function appendEvents(list){
  var log=$('ob-log'),atBottom=log.scrollHeight-log.scrollTop-log.clientHeight<28,added=false;
  list.forEach(function(e){evs.push(e);var el=evEl(e);if(el){log.appendChild(el);added=true}});
  while(log.childNodes.length>600)log.removeChild(log.firstChild);
  if(added&&atBottom)log.scrollTop=log.scrollHeight;
}
function lastSay(){
  for(var i=evs.length-1;i>=0;i--){var e=evs[i];if((e.kind||e.type)==='say'&&e.text)return e.text}
  return '';
}

/* ---- taslak yükleme (artımlı: events_after=<events_total>) ---- */
function loadDraft(force){
  var id=cur;if(!id)return Promise.resolve();
  if(fetching)return force&&fetchP?fetchP.then(function(){return loadDraft()}):Promise.resolve();
  fetching=true;
  fetchP=call(BASE+'/'+enc(id)+'?events_after='+evN).then(function(d){
    fetching=false;if(id!==cur)return;
    var dr=d.draft||d,list=d.events;
    if(!Array.isArray(list))list=Array.isArray(dr.events)?dr.events.slice(evN):[];
    D=dr;
    var next=nextOffset(d,list);
    if(next<evN){evs=[];evN=0;$('ob-log').innerHTML='';list=Array.isArray(d.events)?d.events:[];next=nextOffset(d,list)} // sunucu günlüğü kırpıldı/sıfırlandı
    if(list.length)appendEvents(list);
    evN=next;
    renderDraft();
  }).catch(function(e){
    fetching=false;if(id!==cur)return;
    if(e&&e.status===404){toast('Taslak bulunamadı','bad');showList();return}
    $('ob-note').innerHTML='<div class="note bad">Taslak alınamadı: '+esc(msgOf(e))+'</div>';
  });
  return fetchP;
}
function sigOf(){
  var r=D.report;
  return [D.status,(D.yaml_text||'').length,r?JSON.stringify(r).length:0,str(D.error),D.site_id_suggestion||'',D.saved_site_id||'',D.mode||'',D.edit_site_id||'',JSON.stringify(D.pipeline||null),JSON.stringify(D.question_data||null),D.auto_round||0].join('|');
}
function renderDraft(){
  if(!D)return;
  var s=sigOf(),changed=s!==sig;sig=s;
  renderHead();
  if(changed){
    renderOverall();renderAsk();renderSteps();
    if(yamlKey!==(D.yaml_text||''))renderYaml();
  }
  renderControls();
  renderSave();
}
function renderTime(){
  if(!D)return;
  var a=T(D.run_started_at||D.created_at),b=D.status==='running'?Date.now():T(D.updated_at);
  $('ob-time').textContent=a?dur(Math.max(0,(b-a)/1000)):'';
}
function isEdit(){return !!D&&D.mode==='edit'}
function editSid(){return (D&&(D.edit_site_id||D.site_id_suggestion))||''}
function renderHead(){
  var host=hostOf(D.url);
  $('ob-host').textContent=host||D.id;$('ob-host').title=D.url||'';
  $('ob-st').innerHTML=stPill(D.status)+(isEdit()?' '+pill('heal','Düzenleme: '+editSid()):'');
  $('ob-cancel').classList.toggle('hidden',D.status!=='running');
  renderTime();
  var n='';
  if(D.status==='failed')n='<div class="note bad"><b>Ajan başarısız oldu.</b>'+(D.error?'<br>'+esc(clip(str(D.error),1000)):'')+'</div>';
  else if(D.status==='cancelled')n='<div class="note">İş iptal edildi. Aşağıdan mesaj yazarak aynı oturumdan devam edebilirsin.</div>';
  else if(D.status==='needs_input'){
    if(askOf(D))n=''; // soru kartı (ob-ask) gösterir
    else {
      var q=D.question||lastSay();
      n='<div class="note warn"><b>Ajan senin yanıtını bekliyor.</b>'+(q?'<span class="obq">'+esc(clip(q,1000))+'</span>':'')+'Yanıtını günlüğün altındaki kutuya yaz.</div>';
    }
  }
  else if(D.status==='running'&&D.auto_round>0)n='<div class="note warn"><b>Otomatik düzeltme turu '+esc(D.auto_round)+'/'+esc(D.auto_rounds||'?')+':</b> ajan eksikleri kendi gideriyor; bitince sonucu burada görürsün.</div>';
  else if(D.status==='ready'&&D.auto_round>0&&D.report&&D.report.passed===false)n='<div class="note warn">Ajan eksikleri kendi gidermeyi '+esc(D.auto_round)+' kez denedi, hâlâ eksik var. Sorunlu adımlardaki “Ajan düzeltsin” ya da “Varsa al, yoksa atla” (alan korunur) düğmelerini kullanabilirsin.</div>';
  $('ob-note').innerHTML=n;
}

/* ---- ajanın sorusu (draft.question_data) ---- */
var ASKF={synopsis:'özet',overview:'özet',description:'özet',cast:'oyuncular',actors:'oyuncular',genres:'tür',genre:'tür',year:'yıl',rating:'puan',
  trailer:'fragman',trailer_url:'fragman',poster_url:'poster (dikey)',poster:'poster (dikey)',backdrop:'yatay görsel',search:'site araması',collections:'ana sayfa bölümleri',
  playable:'oynatılabilirlik',stream:'video akışı',player:'oynatıcı'};
function askField(f){
  f=String(f||'');if(ASKF[f])return ASKF[f];
  if(f.indexOf('collection:')===0)return 'ana sayfa bölümü: '+f.slice(11);
  return f||'bilgi';
}
function askOf(d){var q=d&&d.question_data;return q&&typeof q==='object'&&q.text?q:null}
function askCopy(q){
  var t=(q.kind==='engine_gap'?'Sistemde eksik özellik':'Ajanın sorusu')+' ('+askField(q.field)+'): '+(q.text||'');
  var tr=Array.isArray(q.tried)?q.tried.filter(Boolean):[];
  if(tr.length)t+='\nDenenenler:\n'+tr.map(function(x){return '- '+x}).join('\n');
  if(q.proposal)t+='\nÖneri: '+q.proposal;
  return t;
}
function askHtml(q){
  var kind=q.kind||'missing_info',tried=Array.isArray(q.tried)?q.tried.filter(Boolean):[],opts=Array.isArray(q.options)?q.options:[],open=!!openSteps.ask;
  var tr=tried.length?'<button class="obdt" data-ob="tog" data-step="ask" aria-expanded="'+(open?'true':'false')+'">Denediklerim ('+tried.length+') '+(open?'▴':'▾')+'</button>'+
    (open?'<ul class="obaskt">'+tried.map(function(x){return '<li>'+esc(x)+'</li>'}).join('')+'</ul>':''):'';
  if(kind==='engine_gap')return '<div class="obask gap" data-kind="engine_gap">'+
    '<div class="obaskh"><span class="tag bad">Sistemde eksik özellik</span> <b>'+esc(askField(q.field))+'</b></div>'+
    '<div class="obaskq">'+esc(q.text)+'</div>'+tr+
    '<div class="hint">Bu, ajanın site tarifiyle çözemediği bir şey: sistemin bu özelliği desteklemesi gerekir. Metni kopyalayıp iletebilirsin; ajana yine de günlüğün altındaki kutudan yazabilirsin.</div>'+
    '<div class="act"><button class="btn" data-ob="askcopy">Metni kopyala</button></div></div>';
  var btns='',inp='';
  opts.forEach(function(o){
    if(!o||!o.id)return;
    if(o.input)inp='<div class="obaskin"><input id="ob-ask-hint" type="text" maxlength="300" autocomplete="off" aria-label="Nerede olduğunu kısaca yaz" placeholder="Nerede? örn. “Konu” başlığının altında (Enter = gönder)">'+
      '<button class="btn" data-ob="askopt" data-id="'+esc(o.id)+'">'+esc(o.label||o.id)+'</button></div>';
    else btns+='<button class="btn'+(o.id==='apply'?' primary':'')+'" data-ob="askopt" data-id="'+esc(o.id)+'">'+esc(o.label||o.id)+'</button>';
  });
  return '<div class="obask" data-kind="'+esc(kind)+'">'+
    '<div class="obaskh"><span class="tag warn">Ajan soruyor</span> <b>'+esc(askField(q.field))+'</b></div>'+
    '<div class="obaskq">'+esc(q.text)+'</div>'+tr+
    (btns?'<div class="obaskb">'+btns+'</div>':'')+inp+
    (opts.some(function(o){return o&&o.id==='absent'&&o.label==='Varsa al, yoksa atla'})?'<div class="hint">Alan korunur: bulunan sayfalarda alınır, bulunamayanlarda boş kalır.</div>':'')+
    (kind==='decision'?'<div class="hint">İstersen taslağı olduğu gibi de kaydedebilirsin (üstteki Kaydet; “Yine de kaydet”).</div>':'')+'</div>';
}
function renderAsk(){
  var el=$('ob-ask'),q=D&&D.status==='needs_input'?askOf(D):null;
  if(!q){askKey='';el.innerHTML='';return}
  var inp=$('ob-ask-hint'),keep=inp?inp.value:'',key=JSON.stringify(q)+'|'+(openSteps.ask?1:0);
  if(key===askKey)return;
  askKey=key;el.innerHTML=askHtml(q);
  var again=$('ob-ask-hint');if(again&&keep)again.value=keep; // "Denediklerim" açılıp kapanınca yazılan ipucu silinmesin
}
/* düğmenin gönderdiği kalıp metin */
function askAnswer(q,id){
  var o=null;(q.options||[]).forEach(function(x){if(x&&x.id===id)o=x});
  if(!o)return '';
  if(o.input){
    var h=($('ob-ask-hint').value||'').trim();
    if(!h){toast('Önce nerede olduğunu kısaca yaz','bad');var f=$('ob-ask-hint');if(f&&f.focus)f.focus();return ''}
    return (o.answer_prefix||'Var: ')+h;
  }
  if(o.answer)return o.answer;
  return id==='absent'?'Sitede yok, atla: '+(q.field||''):id==='apply'?'Önerini uygula':'Seçim: '+(o.label||id);
}
function askSend(id){
  var q=askOf(D);if(!q||D.status==='running'||sending)return;
  var t=askAnswer(q,id);if(t)sendMessage(t);
}

/* ---- genel durum + adım adım ilerleme ---- */
function renderOverall(){
  var o=overallOf(D),el=$('ob-headline');
  if(!pipeOf(D)||!o||!o.headline){el.innerHTML='';return}
  var m=OV[o.state]||OV.idle;
  el.innerHTML='<div class="obov '+m[0]+'" data-state="'+esc(o.state||'')+'"><span class="obovi'+(o.state==='running'?' pulse':'')+'">'+m[1]+'</span><b>'+esc(o.headline)+'</b></div>';
}
function stepHtml(s,i,prob){
  var m=SS[s.state]||SS.pending,bad=s.state==='warn'||s.state==='fail',dets=Array.isArray(s.details)?s.details.filter(function(d){return d&&(d.label||d.value!=null)}):[],
    nums=Array.isArray(s.numbers)?s.numbers.filter(function(n){return n&&n.value!=null&&n.value!==''}):[],open=!!openSteps[s.id];
  var h='<div class="obstep st-'+m[0]+(prob?' isprob':'')+'" data-step="'+esc(s.id)+'">'+
    '<div class="obsh"><span class="obsi'+(s.state==='running'?' pulse':'')+'" title="'+esc(m[2])+'">'+m[1]+'</span><span class="obsn">'+(i+1)+'.</span><b>'+esc(s.title||s.id)+'</b></div>'+
    (prob?'<div><span class="tag bad">Sorun bu adımda</span></div>':'')+
    (s.question?'<div class="obsq">'+esc(s.question)+'</div>':'')+
    (s.summary?'<div class="obss">'+esc(s.summary)+'</div>':'');
  if(nums.length)h+='<div class="obnums">'+nums.map(function(n){return '<span class="obchip"><b>'+esc(n.value)+'</b> '+esc(n.label||'')+'</span>'}).join('')+'</div>';
  if(bad&&s.problem)h+='<div class="obprob '+(s.state==='fail'?'bad':'warn')+'"><b>Sorun:</b> '+esc(s.problem)+'</div>';
  var acts=Array.isArray(s.actions)?s.actions.filter(function(a){return a&&a.id&&a.message}):[];
  if(bad&&s.problem&&acts.length){
    var off=!D||D.status==='running'||D.status==='saved'?' disabled':'';
    h+='<div class="obfix">'+acts.map(function(a){
      return '<button class="btn'+(a.id==='fix'?' primary':'')+'" data-ob="act" data-step="'+esc(s.id)+'" data-id="'+esc(a.id)+'"'+(a.hint?' title="'+esc(a.hint)+'"':'')+off+'>'+esc(a.label||a.id)+'</button>'}).join('')+'</div>'+
      acts.filter(function(a){return a.hint}).slice(0,1).map(function(a){return '<div class="hint">'+esc(a.hint)+'</div>'}).join('');
  }
  if(dets.length){
    h+='<button class="obdt" data-ob="tog" data-step="'+esc(s.id)+'" aria-expanded="'+(open?'true':'false')+'">Ayrıntılar ('+dets.length+') '+(open?'▴':'▾')+'</button>';
    if(open)h+='<dl class="obdl">'+dets.map(function(d){return '<dt>'+esc(d.label||'')+'</dt><dd>'+esc(str(d.value))+'</dd>'}).join('')+'</dl>';
  }
  return h+'</div>';
}
function renderSteps(){
  var el=$('ob-steps'),p=pipeOf(D);
  if(!p){el.innerHTML='<div class="pempty">İlerleme bilgisi yok (eski taslak)</div>';return}
  var pid=p.overall&&p.overall.problem_step;
  el.innerHTML='<div class="obsteps">'+p.steps.map(function(s,i){return stepHtml(s,i,!!pid&&s.id===pid)}).join('')+'</div>';
}
function renderYaml(){
  var y=D.yaml_text||'',el=$('ob-yaml');yamlKey=y;
  el.innerHTML='<div class="lhead"><h2 class="grow">YAML taslağı</h2>'+(y?'<button class="btn" data-ob="copy">Kopyala</button>':'')+'</div>'+
    (y?'<pre class="obyaml mono" tabindex="0">'+esc(y)+'</pre>':'<div class="pempty obyaml0">Henüz yaml yok</div>');
}
function copyText(t){
  if(navigator.clipboard&&navigator.clipboard.writeText)return navigator.clipboard.writeText(t);
  return new Promise(function(res,rej){
    var ta=document.createElement('textarea');ta.value=t;ta.style.cssText='position:fixed;opacity:0;top:0';
    document.body.appendChild(ta);ta.select();
    try{document.execCommand('copy')?res():rej(new Error('kopyalanamadı'))}catch(e){rej(e)}
    document.body.removeChild(ta);
  });
}

/* ---- sohbet ---- */
function renderControls(){
  var run=D.status==='running';
  $('ob-fb').disabled=run||sending;$('ob-send').disabled=run||sending;
  $('ob-fbh').textContent=run?'Ajan çalışırken mesaj yazılamaz; bitince yazabilirsin.':'';
}
function sendMessage(text,done){
  if(!cur||!D||!text||sending)return Promise.resolve();
  sending=true;renderControls();
  return post(BASE+'/'+enc(cur)+'/message',{text:text}).then(function(){
    toast('Mesaj gönderildi; ajan yeniden çalışıyor');if(done)done();sending=false;return loadDraft(true);
  }).catch(function(e){sending=false;toast('Gönderilemedi: '+msgOf(e),'bad');if(D)renderControls()});
}
function sendFb(){
  if(!D||D.status==='running'||sending)return;
  var t=$('ob-fb').value.trim();
  if(!t){toast('Önce bir mesaj yaz','bad');return}
  sendMessage(t,function(){$('ob-fb').value=''});
}

/* ---- yapışkan Kaydet şeridi + onay paneli ---- */
/* taslağın tarifleri (rapor satırı yoksa taslaktaki ham liste) */
function recipeNote(saved){
  var rs=(D.report&&D.report.provider_recipes)||[];
  if(!rs.length&&Array.isArray(D.provider_recipes))rs=D.provider_recipes.map(function(x){return {name:x&&x.name,valid:true}});
  rs=rs.filter(function(x){return x&&x.name});
  if(!rs.length)return '';
  return '<div class="hint obrec"><b>'+(saved?'Kütüphaneye eklenen provider tarifleri:':'Şu tarifler de kütüphaneye eklenecek:')+'</b> '+
    rs.map(function(x){return '<span class="mono">'+esc(x.name)+'</span>'+(x.valid===false?' <span class="err">(geçersiz)</span>':'')}).join(', ')+'</div>';
}
/* rapor kriterleri tutmuyor ya da genel durum dikkat/sorun: onay panelinde uyarı + "Yine de kaydet" (force) */
function warnish(){
  var o=overallOf(D);
  return !!((D.report&&D.report.passed===false)||(o&&(o.state==='warn'||o.state==='fail')));
}
function needsForce(){return warnish()||forceSrv}
/* yeni sitenin sertleştirme kriterleri: adım kartı yoksa (kayıt onayı yedeği) sade Türkçe adlarıyla yazılır */
var CRIT_TR={availability_gate_defined:'telif / erişim kapısı yok',series_signal_collection:'ana sayfada dizi bölümü yok',
  series_full_inventory:'dizi bölüm listesi okunmuyor',home_path_is_canonical:'ana sayfa başka adrese yönleniyor'};
function critName(k){return CRIT_TR[k]||k}
function problemItems(){
  var out=[],p=pipeOf(D);
  if(p)p.steps.forEach(function(s){if(s.state==='warn'||s.state==='fail')out.push({title:s.title||s.id,problem:s.problem||s.summary||''})});
  if(D.report&&D.report.passed===false&&!out.length){
    var bad=Object.keys(D.report.criteria||{}).filter(function(k){return D.report.criteria[k]&&D.report.criteria[k].ok===false});
    out.push({title:'Kabul kriterleri',problem:bad.map(critName).join(', ')});
  }
  return out;
}
function nextVer(){var s=siteById(editSid());return s&&s.version!=null&&!isNaN(+s.version)?+s.version+1:null}
function savedVer(){
  if(savedInfo&&savedInfo.ver)return savedInfo.ver;
  var s=siteById(D.saved_site_id||D.site_id||'');
  if(s&&s.version!=null)return s.version;
  return isEdit()?null:1;
}
function panelHtml(){
  var p=pipeOf(D),tot=(p&&p.app&&p.app.totals)||{},parts=[];
  [['series','dizi'],['movies','film'],['episodes','bölüm']].forEach(function(k){if(tot[k[0]]!=null&&tot[k[0]]!=='')parts.push(num(tot[k[0]])+' '+k[1])});
  var h='<div class="obpanel"><b>Kaydetmeden önce</b>'+
    '<div class="obsum">'+(parts.length?'Alınacak: '+esc(parts.join(' · ')):'Alınacak içerik sayısı henüz bilinmiyor')+
      (tot.playable!=null&&tot.playable!==''?' · oynatılabilir örnek: '+esc(tot.playable):'')+'</div>';
  if(warnish()){
    var items=problemItems();
    h+='<div class="note warn obwn"><b>Bazı adımlar tamam değil:</b> '+esc(items.map(function(x){return x.title}).join(', ')||'kabul kriterleri')+'. Yine de kaydedebilirsin.'+
      (items.length?'<ul>'+items.map(function(x){return '<li><b>'+esc(x.title)+'</b>'+(x.problem?' — '+esc(clip(x.problem,240)):'')+'</li>'}).join('')+'</ul>':'')+'</div>';
  } else if(forceSrv)h+='<div class="note warn obwn">Sunucu kabul kriterlerini sağlamadı. Yine de kaydedebilirsin.</div>';
  if(svErr)h+='<div class="note bad obwn">'+esc(svErr)+'</div>';
  h+='<label class="chk"><input type="checkbox" id="ob-scan"'+(opt.scan?' checked':'')+'> Kaydedince siteyi hemen tara</label>'+
    '<label class="chk"><input type="checkbox" id="ob-en"'+(opt.enable?' checked':'')+'> Otomatik taramayı aç</label>'+
    '<div class="act"><button class="btn primary" id="ob-confirm" data-ob="confirm"'+(saving?' disabled':'')+'>'+
      (saving?'Kaydediliyor…':needsForce()?'Yine de kaydet':isEdit()?'Onayla ve yeni sürüm kaydet':'Onayla ve kaydet')+'</button>'+
    '<button class="btn" data-ob="svclose">Vazgeç</button></div></div>';
  return h;
}
function infoHtml(saved){
  if(!saved)return recipeNote(false);
  var sid=D.saved_site_id||D.site_id||(savedInfo&&savedInfo.sid)||'',v=savedVer(),sc=savedInfo&&savedInfo.scan;
  return '<div class="obsavedrow"><div class="note ok obsaved"><span class="pill ok">Kaydedildi'+(v?' (v'+esc(v)+')':'')+'</span> '+(sid?'<b class="mono">'+esc(sid)+'</b> ':'')+
    (isEdit()?'yeni sürüm olarak kaydedildi.':'siteye eklendi.')+
    (sc?' Tarama başladı: ilerlemeyi Olay defterinde izleyebilirsin.':(savedInfo&&savedInfo.scan===false?' Tarama başlatılmadı; Ayarlar’dan “Şimdi tara” ile deneyebilirsin.':''))+'</div>'+
    '<div class="act"><button class="btn primary" data-ob="back">Site listesine dön</button>'+
    (sc?'<button class="btn" data-ob="goto-events">Olay defterinde izle</button>':'<button class="btn" data-ob="goto-settings">Ayarlar’a git</button>')+'</div></div>'+recipeNote(true);
}
function renderSave(){
  if(!D)return;
  var st=D.status,edit=isEdit(),saved=st==='saved',yaml=!!(D.yaml_text||'').trim(),sidEl=$('ob-sid'),dnEl=$('ob-dn');
  if(saved)sidEl.value=D.saved_site_id||D.site_id||sidEl.value;
  else if(edit)sidEl.value=editSid();
  else if(!sidTouched&&!sidEl.value&&D.site_id_suggestion)sidEl.value=D.site_id_suggestion;
  sidEl.readOnly=edit||saved;dnEl.disabled=saved;
  if(edit&&!dnTouched&&!dnEl.value){
    var cs=siteById(editSid());
    if(cs&&cs.display_name)dnEl.value=cs.display_name;
    else if(!sites&&!sFail&&!sLoading)loadSites().then(function(){if(D)renderSave()});
  }
  var sid=sidEl.value.trim(),why='';
  if(saved)why='bu taslak kaydedildi';
  else if(st==='running')why='ajan çalışıyor';
  else if(st==='needs_input'&&!(askOf(D)&&D.report&&yaml))why='ajan senin yanıtını bekliyor'; // teslim edilmiş taslağa soru sorulduysa olduğu gibi kaydedilebilir (force)
  else if(st==='failed')why='ajan başarısız oldu';
  else if(st==='cancelled')why='iş iptal edildi';
  else if(st!=='ready'&&st!=='needs_input')why='taslak hazır değil';
  else if(!yaml)why='henüz yaml taslağı yok';
  else if(!SITE_RE.test(sid))why='geçerli bir site kimliği gir';
  var nv=nextVer(),btn=$('ob-savebtn');
  btn.textContent=saved?'Kaydedildi':edit?'Yeni sürüm olarak kaydet'+(nv?' (v'+nv+')':''):'Kaydet';
  btn.disabled=!!why;
  $('ob-savewhy').textContent=why;btn.title=why;
  checkSid();
  if(why)svOpen=false;
  var pnl=$('ob-sv-panel'),ph=svOpen?panelHtml():'';
  if(ph!==panelKey){panelKey=ph;pnl.innerHTML=ph}
  pnl.classList.toggle('hidden',!svOpen);
  $('ob-sv-info').innerHTML=infoHtml(saved);
}
function checkSid(){
  var e=$('ob-sid-err');
  if(isEdit()||(D&&D.status==='saved')){e.textContent='';return true}
  var v=$('ob-sid').value.trim();
  e.textContent=v&&!SITE_RE.test(v)?'Küçük harfle başlamalı; yalnızca küçük harf, rakam ve _ ; 2-32 karakter.':'';
  return !v||SITE_RE.test(v);
}
function doSave(){
  if(!cur||!D||saving)return;
  var edit=isEdit(),sid=edit?editSid():$('ob-sid').value.trim();
  if(!SITE_RE.test(sid)){checkSid();toast('Geçerli bir site kimliği gir','bad');return}
  var dn=$('ob-dn').value.trim(),body={site_id:sid,display_name:dn||undefined,scan_now:!!opt.scan};
  if(!edit)body.enable=!!opt.enable;else if(opt.enable)body.enable=true; // düzenlemede açık otomatik tarama kendiliğinden kapanmasın
  if(needsForce())body.force=true;
  saving=true;svErr='';renderSave();
  post(BASE+'/'+enc(cur)+'/save',body).then(function(r){
    savedInfo={sid:(r&&r.site_id)||sid,ver:(r&&(r.version||r.config_version))||null,scan:!!(r&&r.scan_started),mode:(r&&r.mode)||(edit?'edit':'new')};
    svOpen=false;saving=false;sites=null;
    toast((edit?'Yeni sürüm kaydedildi: ':'Site eklendi: ')+sid);
    changed();
    return loadDraft(true);
  }).catch(function(e){
    saving=false;svErr=msgOf(e);
    if(e&&(e.code==='not_passed'||/not_passed|criteria/.test(msgOf(e))))forceSrv=true; // sunucu yeniden sınadı: "Yine de kaydet" gerekir
    toast('Kaydedilemedi: '+msgOf(e),'bad');
    if(D)renderSave();
  });
}

/* ---- olaylar ---- */
function onChange(ev){
  var t=ev&&ev.target;if(!t||!t.id)return;
  if(t.id==='ob-purge'&&M&&M.kind==='del'){M.purge=!!t.checked;$('ob-purge-line').textContent=purgeLine(siteById(M.site),M.purge)}
  else if(t.id==='ob-scan'){opt.scan=!!t.checked}
  else if(t.id==='ob-en'){opt.enable=!!t.checked}
}
function bind(){
  $('ob-form').addEventListener('submit',function(ev){ev.preventDefault();if(!$('ob-start').disabled)startJob()});
  $('ob-sid').addEventListener('input',function(){sidTouched=true;checkSid();if(D)renderSave()});
  $('ob-dn').addEventListener('input',function(){dnTouched=true});
  $('ob-root').addEventListener('change',onChange);
  $('ob-root').addEventListener('keydown',function(ev){
    var t=ev.target,id=t&&t.id;
    if(ev.key==='Escape'){
      if(M){closeModal();return}
      if(ui.edit||ui.rename){ui.edit=ui.rename=null;drawSites()}
      return;
    }
    if(ev.key!=='Enter'||ev.shiftKey||ev.isComposing)return;
    if(id==='ob-fb'){ev.preventDefault();sendFb()}
    else if(id==='ob-ask-hint'){ev.preventDefault();askSend('present')}
    else if(id==='ob-ehint'&&ui.edit){ev.preventDefault();startEdit(ui.edit)}
    else if(id==='ob-rn'&&ui.rename){ev.preventDefault();doRename(ui.rename)}
  });
  $('ob-root').addEventListener('click',function(ev){
    var b=ev.target.closest('[data-ob]');if(!b||b.disabled)return;
    var a=b.getAttribute('data-ob'),id=b.getAttribute('data-id'),site=b.getAttribute('data-site');
    if(a==='open'){openDraft(id)}
    else if(a==='del'){delDraft(id)}
    else if(a==='back'){showList()}
    else if(a==='new'){newOpen=!newOpen;applyNew()}
    else if(a==='newclose'){newOpen=false;applyNew()}
    else if(a==='hrefresh'){H=null;hFail=false;renderHealth();loadHealth()}
    else if(a==='hcancel'){b.disabled=true;cancelRunning(id)}
    else if(a==='lrefresh'){dFail=false;drafts=null;renderDrafts();loadList()}
    else if(a==='srefresh'){sFail=false;sites=null;drawSites();loadSites()}
    else if(a==='sedit'){ui.rename=null;ui.edit=ui.edit===site?null:site;drawSites();if(ui.edit){var eh=$('ob-ehint');if(eh&&eh.focus)eh.focus()}}
    else if(a==='ecancel'){ui.edit=null;drawSites()}
    else if(a==='estart'){startEdit(site)}
    else if(a==='srename'){ui.edit=null;ui.rename=ui.rename===site?null:site;drawSites();if(ui.rename){var rn=$('ob-rn');if(rn&&rn.focus)rn.focus()}}
    else if(a==='rncancel'){ui.rename=null;drawSites()}
    else if(a==='rnsave'){doRename(site)}
    else if(a==='srollback'){rollbackSite(site)}
    else if(a==='syaml'){openYaml(site)}
    else if(a==='shandoff'){openHandoff(site)}
    else if(a==='sdel'){openDel(site)}
    else if(a==='mclose'){closeModal()}
    else if(a==='mdel'){doDelete()}
    else if(a==='mcopyh'){var ht=M&&M.text||'';copyText(ht).then(function(){toast('Devir notu kopyalandı')}).catch(function(){toast('Kopyalanamadı','bad')})}
    else if(a==='mcopy'){var y=M&&M.cfg&&M.cfg.yaml_text||'';copyText(y).then(function(){toast('YAML kopyalandı')}).catch(function(){toast('Kopyalanamadı','bad')})}
    else if(a==='cancel'){
      b.disabled=true;
      post(BASE+'/'+enc(cur)+'/cancel').then(function(){toast('İptal istendi');return loadDraft(true)})
        .catch(function(e){toast('İptal edilemedi: '+msgOf(e),'bad')}).then(function(){b.disabled=false});
    }
    else if(a==='send'){sendFb()}
    else if(a==='tog'){var sid=b.getAttribute('data-step');if(sid){openSteps[sid]=!openSteps[sid];if(D){renderSteps();if(sid==='ask')renderAsk()}}}
    else if(a==='act'){
      if(!D||D.status==='running'||sending)return;
      var pp=pipeOf(D),stp=null,ac=null;
      ((pp&&pp.steps)||[]).forEach(function(x){if(x.id===b.getAttribute('data-step'))stp=x});
      ((stp&&stp.actions)||[]).forEach(function(x){if(x.id===id)ac=x});
      if(ac&&ac.message)sendMessage(ac.message);
    }
    else if(a==='askopt'){askSend(id)}
    else if(a==='askcopy'){var aq=askOf(D);if(aq)copyText(askCopy(aq)).then(function(){toast('Metin kopyalandı')}).catch(function(){toast('Kopyalanamadı','bad')})}
    else if(a==='save'){if(D){svOpen=true;svErr='';renderSave()}}
    else if(a==='svclose'){svOpen=false;if(D)renderSave()}
    else if(a==='confirm'){doSave()}
    else if(a==='copy'){copyText(D&&D.yaml_text||'').then(function(){toast('YAML kopyalandı')}).catch(function(){toast('Kopyalanamadı','bad')})}
    else if(a==='copylog'){var t=logText();if(!t){toast('Günlük boş','bad');return}copyText(t).then(function(){toast('Günlük kopyalandı')}).catch(function(){toast('Kopyalanamadı','bad')})}
    else if(a==='goto-settings'){var tb=$('tab-btn-settings');if(tb)tb.click()}
    else if(a==='goto-events'){var te=$('tab-btn-events');if(te)te.click()}
  });
}
function visible(){var t=$('tab-onboard');return !!t&&!t.classList.contains('hidden')&&!document.hidden}
function tick(){
  if(!visible())return; // gizliyken polling yok
  tickN++;
  if(cur){
    renderTime();
    if(tickN%2===0&&D&&D.status==='running')loadDraft();
  } else if(tickN%4===0){
    if(sites&&sites.some(function(s){return s.busy})&&!M)loadSites();
    if(drafts&&drafts.some(function(d){return d.status==='running'}))loadList();
    if(H&&runningOf(H))loadHealth(); // iş bitince sağlık kartı kendiliğinden kalkar (yarış kilidi olmasın)
  }
}

/* ---- sekme kancası ---- */
function hashId(){var m=/^#onboard\/(od_[0-9a-f]{12})$/.exec(location.hash);return m?m[1]:null}
function onShow(){
  build();
  if(!timer)timer=setInterval(tick,1000);
  var id=hashId();
  if(id)openDraft(id);
  else if(cur){show(true);setHash(cur);loadDraft()}
  else showList();
}
window.dzTabHooks=window.dzTabHooks||{};
window.dzTabHooks.onboard=onShow;
if(!$('tab-onboard').classList.contains('hidden'))onShow();
})();
