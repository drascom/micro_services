/* Ayarlar sekmesi: /api/ops/settings (heal + TMDB anahtarları; site otomatik tarama ayarları "Siteler" sekmesine taşındı), /api/ops/llm/health
   ve "TMDB zenginleştirme" kartı (/api/ops/tmdb/status|preview|backfill; tür seçicideki "Sezon ve bölüm görselleri"
   = kind=series + scope=seasons: dizi sezon posterleri + bölüm başlık/özet/görsel).
   Değişiklik anında kaydedilir (PUT) ve yeniden başlatma gerektirmez. */
(function(){
'use strict';
var $=function(i){return document.getElementById(i)};
var D=null,failed=false,llm=null,llmBusy=false,pollTimer=null;
var TS=null,tFail=false,tKind='movie',tDec='',tOff=0,tPrev=null,tPrevBusy=false,tStarting=false,tLast='',tTimer=null,tTicks=0,tWatch=false,tLastId=null;
var LV=null,lvFail=false,lvOpen=false,lvFilter='',LR=[],lrOpen=false;   /* LR: istemciden silinen kanallar (/api/ops/live/removed) */
var MS=null,msFail=false,msBusy=false,msLast=null,MF={kind:'link',name:'',url:'',llm:true,file:null};
var PG=20,KL={movie:'film',series:'dizi',seasons:'dizi (sezon/bölüm)'},KLG={movie:'filmden',series:'diziden'},KLP={movie:'Film',series:'Dizi',seasons:'Sezon ve bölüm görselleri'};
var DECL={auto:['ok','Otomatik'],review:['warn','İnceleme'],unmatched:['','Eşleşmedi'],error:['bad','Hata'],deferred:['','Ertelendi']};
var SDECL={auto:['ok','Bulundu'],empty:['','TMDB’de yok'],error:['bad','Hata'],deferred:['','Ertelendi']};
var DECS=[['','Hepsi'],['auto','Otomatik'],['review','İnceleme'],['unmatched','Eşleşmedi'],['error','Hata']];
var SDECS=[['','Hepsi'],['auto','Bulundu'],['empty','TMDB’de yok'],['error','Hata']];
function isSeasons(){return tKind==='seasons'}
function scopeBody(dry){return isSeasons()?{kind:'series',scope:'seasons',dry_run:!!dry}:{kind:tKind,dry_run:!!dry}}
function curSum(){return TS&&(isSeasons()?TS.season_preview:TS.preview)}

function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
function T(t){return t?new Date(t).getTime():0}
function ago(t){if(!T(t))return '-';var d=Math.max(0,(Date.now()-T(t))/1000);
  if(d<60)return Math.floor(d)+' sn önce';if(d<3600)return Math.floor(d/60)+' dk önce';
  if(d<86400)return Math.floor(d/3600)+' sa önce';return Math.floor(d/86400)+' g önce'}
function when(t){return T(t)?new Date(t).toLocaleString('tr-TR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}):'-'}
function secText(s){s=Math.max(0,Math.round(s));var h=Math.floor(s/3600),m=Math.floor(s%3600/60);
  return h?h+' sa'+(m?' '+m+' dk':''):m?m+' dk':s+' sn'}
function num(n){return n==null?'-':(+n).toLocaleString('tr-TR')}
function pill(cls,txt,extra){return '<span class="pill '+cls+'"'+(extra||'')+'>'+esc(txt)+'</span>'}

/* ---- bildirim ---- */
function toast(msg,kind){
  var box=$('toasts');if(!box)return;
  var el=document.createElement('div');el.className='toast'+(kind==='bad'?' bad':'');el.textContent=msg;
  box.appendChild(el);setTimeout(function(){if(el.parentNode)el.parentNode.removeChild(el)},kind==='bad'?5000:2200);
}

/* ---- api ---- */
function call(path,opt){
  return fetch(path,opt).then(function(r){
    return r.json().catch(function(){return null}).then(function(j){
      if(!r.ok){var m=(j&&j.error&&j.error.message)||(j&&j.detail&&(j.detail.message||j.detail))||('HTTP '+r.status);throw new Error(typeof m==='string'?m:JSON.stringify(m))}
      return j;
    });
  });
}
function load(){
  return call('/api/ops/settings').then(function(d){D=d;failed=false;render()}).catch(function(e){failed=String(e&&e.message||e);if(!D)render()});
}
function save(patch,msg){
  return call('/api/ops/settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(patch)})
    .then(function(d){D=d;failed=false;render();toast(msg||'Kaydedildi');
      try{document.dispatchEvent(new CustomEvent('dz:settings-changed'))}catch(e){}})
    .catch(function(e){toast('Kaydedilemedi: '+(e&&e.message||e),'bad');return load()});
}
function loadLlm(force){
  llmBusy=true;renderLlm();
  return call('/api/ops/llm/health',force?{method:'POST'}:undefined).then(function(d){llm=d}).catch(function(e){
    llm={status:'error',message:'Kontrol yapılamadı',detail:String(e&&e.message||e)};
  }).then(function(){llmBusy=false;renderLlm();if(force)toast(llm&&llm.status==='valid'?'LLM hesabı geçerli':(llm&&llm.message)||'Kontrol tamamlandı',llm&&llm.status==='valid'?'':'bad')});
}


/* ---- TMDB zenginleştirme ---- */
function covBar(c){
  if(!c||!c.total)return '';
  var pct=Math.round(c.matched/c.total*100);
  return '<div class="bar" role="img" aria-label="TMDB eşleşme oranı %'+pct+'"><i style="width:'+pct+'%;background:var(--ok)"></i></div>';
}
function covLine(k){
  var c=((TS&&TS.coverage)||{})[k];if(!c)return '';
  if(!c.total)return 'Kütüphanede '+KL[k]+' yok';
  var s='<b>'+num(c.total)+'</b> '+KLG[k]+' <b>'+num(c.matched)+'</b> TMDB’li';
  if(c.review)s+=', '+num(c.review)+' inceleme bekliyor';
  s+=c.no_poster?', <b>'+num(c.no_poster)+'</b> posteri yer tutucu':', posterlerin hepsi tamam';
  if(c.no_backdrop)s+=', '+num(c.no_backdrop)+' afişsiz';
  return s;
}
function seasonLine(){
  var c=((TS&&TS.coverage)||{}).seasons;if(!c)return '';
  if(!c.seasons)return 'kütüphanede sezonlu dizi yok';
  var s='<b>'+num(c.series)+'</b> sezonlu dizide <b>'+num(c.seasons)+'</b> sezon: <b>'+num(c.season_posters)+'</b> sezon posteri';
  s+=', <b>'+num(c.episodes)+'</b> bölümün <b>'+num(c.episode_stills)+'</b>’inde görsel ('+num(c.tmdb_stills)+' TMDB)';
  if(c.series_tmdb<c.series)s+=', '+num(c.series-c.series_tmdb)+' dizi TMDB’siz (önce eşleşmeli)';
  return s;
}
function seasonBar(){
  var c=((TS&&TS.coverage)||{}).seasons;if(!c||!c.seasons)return '';
  var pct=Math.round(c.season_posters/c.seasons*100);
  return '<div class="bar" role="img" aria-label="Sezon posteri oranı %'+pct+'"><i style="width:'+pct+'%;background:var(--ok)"></i></div>';
}
function tTypes(){return (D&&D.tmdb&&D.tmdb.types)||[]}
function jobBar(j){
  var pct=(j.total>0)?Math.min(100,Math.round(j.done/j.total*100)):null;
  return '<div class="tjob" role="status"><div class="lhead"><span class="pill run"><span class="dot pulse"></span>'+(j.dry_run?'Önizleme':j.phase==='refresh'?'Yenileniyor':'Çalışıyor')+'</span>'+
    '<b>'+esc(j.label||'TMDB zenginleştirme')+'</b><span class="grow"></span><span class="hint">'+(pct!=null?'%'+pct:'')+' · '+secText(j.elapsed||0)+'</span></div>'+
    '<div class="bar"><i class="'+(pct==null?'pulse':'')+'" style="width:'+(pct==null?100:pct)+'%;'+(pct==null?'opacity:.5':'')+'"></i></div></div>';
}
function previewMeta(pv){
  if(!pv)return '';
  var st=pv.applied?pill('ok','Uygulandı'):pv.fresh?pill('ok','Taze — uygulanabilir'):pill('warn','Bayat — uygulama canlı sorgular');
  var c=pv.counts||{};
  if(pv.scope==='seasons'){
    var t=pv.totals||{};
    return '<div class="pmeta">'+st+'<span>'+esc(KLP.seasons)+' · '+esc(ago(pv.created_at))+' · '+num(pv.lookup)+' dizi sorgulandı'+(pv.limit?' (sınır '+num(pv.limit)+')':'')+
      '</span><span class="hint">'+num(t.seasons)+' sezon · '+num(t.episodes)+' bölüm · '+num(t.posters)+' sezon posteri · '+num(t.stills)+' bölüm görseli'+(t.empty?' · '+num(t.empty)+' sezon TMDB’de yok':'')+(c.error?' · '+num(c.error)+' hata':'')+'</span></div>';
  }
  return '<div class="pmeta">'+st+'<span>'+esc(KLP[pv.kind]||pv.kind)+' · '+esc(ago(pv.created_at))+' · '+num(pv.lookup)+' öğe sorgulandı'+(pv.limit?' (sınır '+num(pv.limit)+')':'')+
    '</span><span class="hint">'+num(c.auto)+' otomatik · '+num(c.review)+' inceleme · '+num(c.unmatched)+' eşleşmedi'+(c.error?' · '+num(c.error)+' hata':'')+'</span></div>';
}
function prow(it){
  var d=DECL[it.decision]||['',it.decision];
  var cand=it.candidate,alt=(it.alternatives||[]).filter(Boolean);
  var img=cand&&cand.poster_url?'<img loading="lazy" referrerpolicy="no-referrer" alt="" src="'+esc(cand.poster_url)+'" onerror="this.remove()">':'';
  var src=esc(it.title||'-')+(it.year?' <span class="yr">('+esc(it.year)+')</span>':'');
  var to=cand?(cand.title?esc(cand.title)+(cand.year?' <span class="yr">('+esc(cand.year)+')</span>':''):'<span class="yr">bilinen TMDB kimliği</span>')+
      (cand.tmdb_id?' <span class="mono yr">#'+esc(cand.tmdb_id)+'</span>':''):'<span class="yr">aday yok</span>';
  var score=(typeof it.score==='number')?it.score.toFixed(2):'-';
  return '<li class="prow"><span class="pth">'+img+'</span><div class="pmain"><div class="ps">'+src+'</div><div class="pt">→ '+to+'</div>'+
    (alt.length?'<div class="pa">diğer adaylar: '+alt.map(function(a){return esc(a.title||'?')+(a.year?' ('+esc(a.year)+')':'')}).join(' · ')+'</div>':'')+
    (it.reason?'<div class="pr">'+esc(it.reason)+'</div>':'')+'</div>'+
    '<div class="pside">'+pill(d[0],d[1])+'<span class="mono sc" title="eşleşme skoru">'+score+'</span></div></li>';
}
function srow(it){
  var d=SDECL[it.decision]||['',it.decision];
  var ss=(it.seasons||[]).filter(function(x){return x.status==='ok'});
  var first=ss.filter(function(x){return x.poster_url})[0];
  var img=first?'<img loading="lazy" referrerpolicy="no-referrer" alt="" src="'+esc(first.poster_url)+'" onerror="this.remove()">':'';
  var strip=ss.filter(function(x){return x.poster_url}).slice(0,6).map(function(x){
    return '<img class="sp" loading="lazy" referrerpolicy="no-referrer" alt="" title="S'+esc(x.season)+'" src="'+esc(x.poster_url)+'" onerror="this.remove()">'}).join('')+
    (it.samples||[]).map(function(x){
    return '<img class="ss" loading="lazy" referrerpolicy="no-referrer" alt="" title="S'+esc(x.season)+'B'+esc(x.episode)+(x.title?' · '+esc(x.title):'')+'" src="'+esc(x.still_url)+'" onerror="this.remove()">'}).join('');
  var sum=ss.length?ss.length+' sezon · '+num(it.episodes_found)+' bölüm · '+num(it.posters)+' poster · '+num(it.stills)+' bölüm görseli':'';
  var bad=(it.seasons||[]).filter(function(x){return x.status!=='ok'}).map(function(x){return 'S'+x.season+': '+({empty:'TMDB’de yok',error:'hata',deferred:'ertelendi'}[x.status]||x.status)}).join(' · ');
  return '<li class="prow"><span class="pth">'+img+'</span><div class="pmain"><div class="ps">'+esc(it.title||'-')+(it.year?' <span class="yr">('+esc(it.year)+')</span>':'')+
    (it.tmdb_id?' <span class="mono yr">#'+esc(it.tmdb_id)+'</span>':'')+'</div>'+
    (sum?'<div class="pt">→ '+esc(sum)+'</div>':'')+(strip?'<div class="sstrip">'+strip+'</div>':'')+
    (bad?'<div class="pa">'+esc(bad)+'</div>':'')+(!sum&&it.reason?'<div class="pr">'+esc(it.reason)+'</div>':'')+'</div>'+
    '<div class="pside">'+pill(d[0],d[1])+'</div></li>';
}
function previewHtml(){
  var pv=curSum(),S=isSeasons();
  if(!pv&&!(tPrev&&tPrev.available))
    return '<div class="pempty"><b>Henüz önizleme yok.</b> “Önizleme (yazmadan)” '+(S?'TMDB’li dizilerin kütüphanedeki sezonları için sezon posterlerini ve bölüm başlık/özet/görsellerini TMDB’de arar':'eksik posterleri ve bilgileri TMDB’de arar')+', sonucu burada gösterir; hiçbir şey yazılmaz.</div>';
  var h=previewMeta(pv||(tPrev&&tPrev.summary));
  var counts=(pv&&pv.counts)||{};
  h+='<div class="fg" role="group" aria-label="Karar süzgeci">'+(S?SDECS:DECS).map(function(x){
    var n=x[0]?counts[x[0]]:pv&&pv.lookup;
    if(x[0]==='error'&&!n&&tDec!=='error')return '';
    return '<button class="ch'+(n?'':' zero')+'" data-tdec="'+x[0]+'" aria-pressed="'+(tDec===x[0])+'">'+x[1]+' <b>'+num(n||0)+'</b></button>'}).join('')+'</div>';
  if(!tPrev){return h+'<div class="empty">Yükleniyor…</div>'}
  if(!tPrev.items.length)
    return h+'<div class="pempty">'+(pv&&!pv.lookup?(S?'Sorgulanacak dizi yok — sezon verisi zaten alınmış, dizi TMDB’siz ya da kütüphanede sezon yok.':'Sorgulanacak öğe yok — hepsi zaten eşleşmiş ya da yakın zamanda denenmiş.'):'Bu süzgeçle öğe yok.')+'</div>';
  var from=tPrev.offset+1,to=tPrev.offset+tPrev.items.length;
  h+='<ul class="plist'+(tPrevBusy?' busy':'')+'">'+tPrev.items.map(S?srow:prow).join('')+'</ul>'+
    '<div class="pg"><button class="btn" data-tpg="-1"'+(tPrev.offset>0&&!tPrevBusy?'':' disabled')+'>← Önceki</button>'+
    '<span class="num">'+num(from)+'–'+num(to)+' / '+num(tPrev.total)+'</span>'+
    '<button class="btn" data-tpg="1"'+(tPrev.has_more&&!tPrevBusy?'':' disabled')+'>Sonraki →</button></div>';
  return h;
}
function tmdbHtml(){
  var T0=D&&D.tmdb||{},cfg=TS?TS.configured:T0.configured,types=tTypes();
  var h='<div class="lhead">'+(cfg?pill('ok','Anahtar tanımlı'):pill('bad','Anahtar yok'))+'<b>TMDB zenginleştirme</b><span class="grow"></span></div>';
  if(!cfg)h+='<div class="howto"><b>TMDB anahtarı tanımlı değil.</b> Sunucudaki <code>.env</code> dosyasına <code>TMDB_ACCESS_KEY=…</code> ekleyip servisi yeniden başlatın; o zamana kadar zenginleştirme çalışmaz.</div>';
  if(tFail&&!TS)return h+'<div class="empty">Durum alınamadı: '+esc(tFail)+'<br><button class="btn" id="tmdb-retry">Yeniden dene</button></div>';
  if(!TS)return h+'<div class="empty">Yükleniyor…</div>';
  h+='<div class="cov">'+['movie','series'].map(function(k){
    var on=types.indexOf(k)>=0;
    return '<div class="covrow'+(on?'':' off')+'"><div>'+covLine(k)+(on?'':' <span class="tag">seçili değil</span>')+'</div>'+covBar((TS.coverage||{})[k])+'</div>'}).join('')+
    '<div class="covrow'+(types.indexOf('series')>=0?'':' off')+'"><div><b>Sezon ve bölüm görselleri:</b> '+seasonLine()+'</div>'+seasonBar()+'</div></div>';
  h+='<div class="trow"><div><b>Yeni öğeler için otomatik zenginleştir</b>'+
    '<p class="hint">Yeni taranan her film/dizi için eksik poster, afiş ve bilgiler ingest sırasında arka planda TMDB’den alınır. Eşleşemeyenler '+esc(TS.retry_days||7)+' gün sonra tekrar denenir.'+(cfg?'':' (Anahtar yokken etkisiz.)')+'</p></div>'+
    sw('tmdb-auto',!!T0.auto,'Yeni öğeler için otomatik zenginleştir','data-tmdb-auto="1"')+'</div>';
  h+='<div class="trow"><div><b>Türler</b><p class="hint">Hem otomatik akışta hem elle çalıştırmada bu türler kullanılır.</p></div><div class="tchk">'+
    ['movie','series'].map(function(k){return '<label class="chk"><input type="checkbox" data-tmdb-type="'+k+'"'+(types.indexOf(k)>=0?' checked':'')+'> '+KLP[k]+'</label>'}).join('')+'</div></div>';
  var need=isSeasons()?'series':tKind;
  var running=!!TS.job,dis=!cfg||running||tStarting||types.indexOf(need)<0;
  h+='<div class="trow tact"><div class="seg" role="group" aria-label="Tür">'+['movie','series','seasons'].map(function(k){
    return '<button data-tkind="'+k+'" aria-pressed="'+(tKind===k)+'">'+KLP[k]+'</button>'}).join('')+'</div>'+
    '<button class="btn" id="tmdb-preview"'+(dis?' disabled':'')+'>Önizleme (yazmadan)</button>'+
    '<button class="btn primary" id="tmdb-apply"'+(dis?' disabled':'')+'>Uygula (eksikleri doldur)</button></div>';
  if(cfg&&types.indexOf(need)<0)h+='<div class="hint">'+KLP[need]+' türü seçili değil; önce yukarıdan işaretleyin.</div>';
  if(isSeasons())h+='<div class="hint">TMDB’ye eşleşmiş dizilerin kütüphanede bulunan sezonları için sezon posterlerini ve bölüm başlık/özet/tarih/süre/görsellerini alır (yalnızca eksik olanlar; kaynak alanları ezilmez). Yeni açılan/taranan diziler için ayrıca otomatik yapılır.</div>';
  if(running)h+=jobBar(TS.job);
  else if(TS.prewarm)h+='<div class="note tinfo"><span class="dot pulse"></span> Resimler indiriliyor… (poster/afişler sunucu önbelleğine alınıyor)</div>';
  else if(TS.last_job&&!TS.last_job.dry_run&&TS.last_job.status==='error')h+='<div class="howto">Son çalışma hata verdi: '+esc(TS.last_job.error||'bilinmeyen hata')+'</div>';
  h+='<h3 class="psec">Önizleme sonucu</h3>'+previewHtml();
  return h;
}
function renderTmdb(){
  var el=$('tmdb-card');if(!el)return;
  var html=tmdbHtml();
  var sig=html;
  if(sig===tLast)return;
  tLast=sig;el.innerHTML=html;
}
function tmdbLoad(){
  return call('/api/ops/tmdb/status').then(function(d){
    var was=!!(TS&&TS.job),id=d.last_job&&d.last_job.id;
    var finished=!d.job&&(was||(tLastId!==null&&id&&id!==tLastId));
    TS=d;tFail=false;tLastId=id||'';
    renderTmdb();
    if(finished)tmdbDone(d);
    else if(!tPrev&&curSum()&&!tPrevBusy)tmdbPreview(true);
  }).catch(function(e){tFail=String(e&&e.message||e);renderTmdb()});
}
function tmdbDone(d){
  var j=d.last_job||{};
  if(j.status==='error')toast('TMDB işi hata verdi: '+(j.error||''),'bad');
  else if(j.dry_run&&j.scope==='seasons')toast('Önizleme hazır: '+num(j.series)+' dizi, '+num(j.seasons)+' sezon, '+num(j.episodes)+' bölüm bulundu');
  else if(j.dry_run)toast('Önizleme hazır: '+num(j.matched)+' otomatik, '+num(j.review)+' inceleme, '+num(j.unmatched)+' eşleşmedi');
  else if(j.scope==='seasons')toast('TMDB: '+num(j.written)+' sezon, '+num(j.episodes)+' bölüm güncellendi'+(d.prewarm?' — resimler indiriliyor…':''));
  else toast('TMDB: '+num(j.written)+' öğe güncellendi'+(d.prewarm?' — resimler indiriliyor…':''));
  tDec='';tmdbPreview(true);
  if(!j.dry_run){try{document.dispatchEvent(new CustomEvent('dz:settings-changed'))}catch(e){}}
}
function tmdbPreview(reset){
  if(reset)tOff=0;
  tPrevBusy=true;renderTmdb();
  return call('/api/ops/tmdb/preview?limit='+PG+'&offset='+tOff+(tDec?'&decision='+encodeURIComponent(tDec):'')+(isSeasons()?'&scope=seasons':'')).then(function(d){tPrev=d}).catch(function(e){
    tPrev=null;toast('Önizleme alınamadı: '+(e&&e.message||e),'bad');
  }).then(function(){tPrevBusy=false;renderTmdb()});
}
function tmdbStart(dry){
  var S=isSeasons(),pv=curSum(),usable=pv&&pv.fresh&&!pv.applied,c=(pv&&pv.counts)||{};
  if(!dry){
    var msg;
    if(S)msg=usable?num(c.auto)+' dizinin sezon posterleri ve bölüm verisi/görselleri yazılacak (kaynak alanları ezilmez, yalnızca boşlar dolar).\n\nÖnizlemedeki veriler TMDB’yi yeniden sorgulamadan uygulanır. Devam edilsin mi?'
      :'Taze bir sezon önizlemesi yok: TMDB’li dizilerin eksik sezon/bölüm verisi TMDB’den canlı sorgulanacak ve yazılacak.\n\nÖnce “Önizleme (yazmadan)” almanız önerilir. Yine de devam edilsin mi?';
    else msg=usable?num(c.auto)+' öğe yazılacak, '+num(c.review)+' inceleme ve '+num(c.unmatched)+' eşleşmeyen atlanacak (eşleşme yazılmaz).\n\nÖnizlemedeki otomatik eşleşmeler TMDB’yi yeniden sorgulamadan uygulanır. Devam edilsin mi?'
      :'Taze bir önizleme yok: eksik '+KL[tKind]+' kayıtları TMDB’den canlı sorgulanacak ve yalnızca otomatik eşleşenler yazılacak.\n\nÖnce “Önizleme (yazmadan)” almanız önerilir. Yine de devam edilsin mi?';
    if(!confirm(msg))return;
  }
  tStarting=true;renderTmdb();
  call('/api/ops/tmdb/backfill',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(scopeBody(dry))})
    .then(function(){toast(dry?'Önizleme başladı':'Zenginleştirme başladı');tWatch=true;return tmdbLoad()})
    .catch(function(e){toast('Başlatılamadı: '+(e&&e.message||e),'bad')})
    .then(function(){tStarting=false;renderTmdb()});
}
function tmdbTick(){
  if($('tab-settings').classList.contains('hidden')||document.hidden)return;
  tTicks++;
  if((TS&&(TS.job||TS.prewarm))||tWatch||tTicks%8===0)tmdbLoad().then(function(){if(TS&&!TS.job&&!TS.prewarm)tWatch=false});
}

/* ---- görünüm ---- */
function sw(id,checked,label,attrs){
  return '<label class="sw"><input type="checkbox" role="switch" id="'+id+'" '+(checked?'checked ':'')+(attrs||'')+' aria-label="'+esc(label)+'"><span class="knob"></span></label>';
}
function llmBadge(){
  if(llmBusy&&!llm)return pill('run','Kontrol ediliyor…');
  if(!llm)return pill('','Bilinmiyor');
  var m={valid:['ok','Geçerli'],expired:['bad','Süresi dolmuş'],error:['warn','Hata'],pi_missing:['','pi yok']}[llm.status]||['',llm.status];
  return pill(m[0],m[1]);
}
function llmHtml(){
  var h='<div class="lhead">'+llmBadge()+'<b>LLM hesabı</b><span class="grow"></span>'+
    '<button class="btn" id="llm-test"'+(llmBusy?' disabled':'')+'>'+(llmBusy?'Test ediliyor…':'Test et')+'</button></div>';
  if(!llm)return h;
  var rows='';
  if(llm.provider)rows+='<div><dt>Hesap</dt><dd>'+esc(llm.provider)+'</dd></div>';
  if(llm.model)rows+='<div><dt>Model</dt><dd style="font-size:12.5px">'+esc(llm.model)+'</dd></div>';
  if(llm.expires_at)rows+='<div><dt>Bitiş</dt><dd>'+esc(when(llm.expires_at))+'<small>'+esc(T(llm.expires_at)>Date.now()?'yaklaşık '+secText((T(llm.expires_at)-Date.now())/1000)+' sonra':ago(llm.expires_at))+'</small></dd></div>';
  if(llm.refreshed_at)rows+='<div><dt>Son yenileme</dt><dd>'+esc(when(llm.refreshed_at))+'<small>'+esc(ago(llm.refreshed_at))+'</small></dd></div>';
  if(llm.checked_at)rows+='<div><dt>Son kontrol</dt><dd>'+esc(ago(llm.checked_at))+'<small>'+(llm.cached?'önbellekten':'canlı')+'</small></dd></div>';
  if(rows)h+='<dl class="skv">'+rows+'</dl>';
  if(llm.status==='expired')h+='<div class="howto"><b>Yeniden giriş gerekli.</b> Sunucuda <code>pi</code> ile yeniden giriş yapın: sunucuya SSH ile bağlanıp <code>pi</code> komutunu açın, <code>/login</code> ile <code>'+esc(llm.provider||'openai-codex')+'</code> hesabına tekrar giriş yapın, sonra “Test et”e basın.</div>';
  else if(llm.status==='error')h+='<div class="howto">Hesap durumu okunamadı'+(llm.detail?' ('+esc(llm.detail)+')':'')+'. Sunucuda <code>pi auth check --provider '+esc(llm.provider||'…')+' --no-refresh</code> çalıştırarak bakın; gerekirse <code>pi</code> ile yeniden giriş yapın.</div>';
  else if(llm.status==='pi_missing')h+='<div class="howto">Bu sunucuda <code>pi</code> kurulu değil; hesap kontrolü yapılamıyor.'+(llm.heal_provider&&llm.heal_provider!=='pi'?' Heal sağlayıcısı zaten <code>'+esc(llm.heal_provider)+'</code>.':'')+'</div>';
  if(llm.status!=='pi_missing'&&llm.provider_is_pi===false)h+='<div class="hint">Not: heal sağlayıcısı şu an <b>'+esc(llm.heal_provider)+'</b>; bu kontrol <code>pi</code> hesabı içindir.</div>';
  return h;
}
function renderLlm(){var el=$('llm-card');if(el)el.innerHTML=llmHtml()}
function healHtml(){
  var h=D.heal||{};
  return '<section><h2>Kendi kendini düzeltme (LLM)</h2><div class="scards">'+
    '<div class="card"><div class="sh"><span class="sname" style="font-size:16px">Düzeltmeyi otomatik uygula</span>'+
      sw('heal-auto',!!h.autoapply,'Düzeltmeyi otomatik uygula','data-autoapply="1"')+'</div>'+
      '<p class="hint">Kapalıyken LLM sayfa değişince düzeltme önerir ama uygulamaz (olay defterinde “Uygulanmadı” görünür). Açıkken, doğrulamadan geçen öneri yeni config sürümü olarak hemen etkinleşir; gerekirse geri alınır.</p>'+
      '<dl class="skv"><div><dt>Heal</dt><dd>'+(h.enabled?'etkin':'kapalı')+'<small>'+(h.enabled?'':'SCRAPER_HEAL_ENABLED=false')+'</small></dd></div>'+
      '<div><dt>Sağlayıcı</dt><dd><select id="heal-provider" data-heal-provider="1" aria-label="Heal sağlayıcısı">'+
        (h.providers||['pi_agent']).map(function(p){return '<option value="'+esc(p)+'"'+(p===h.provider?' selected':'')+'>'+esc(p)+(p==='pi_agent'?' (önerilen)':'')+'</option>'}).join('')+
        '</select>'+(h.provider_missing?'<small style="color:var(--bad,#c33)">'+esc(h.provider_missing)+' CLI bu sunucuda kurulu değil: heal çalışmaz</small>':'')+'</dd></div>'+
      '<div><dt>Model</dt><dd style="font-size:12.5px">'+esc(h.model||'varsayılan')+'</dd></div>'+
      '<div><dt>Cooldown</dt><dd>'+esc(secText(h.cooldown_seconds||0))+'<small>başarısız denemeden sonra</small></dd></div></dl>'+
    '</div>'+
    '<div class="card" id="llm-card">'+llmHtml()+'</div></div></section>';
}
function resetHtml(){
  return '<section><h2>Bakım</h2><div class="card"><div class="sh"><span class="sname" style="font-size:16px">Kütüphaneyi sıfırla</span></div>'+
    '<p class="hint">Deneme amaçlı: tüm dizi/film, bölüm, kaynak, kategori üyeliği, izleme ilerlemesi ve listem silinir. Ana ekran kategorileri, profiller, ayarlar ve site config’leri korunur; önce veritabanı yedeği alınır. Siteler sonraki taramada yeniden dolar.</p>'+
    '<label class="hint" style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="reset-cache"> Görsel ve altyazı önbelleğini de sil</label>'+
    '<div class="act" style="margin-top:10px"><button class="btn obdanger" id="lib-reset">Kütüphaneyi sıfırla</button></div></div></section>';
}
function libReset(b){
  if(!window.confirm('Kütüphane sıfırlansın mı?'))return;
  var c=$('reset-cache'),body={cache:!!(c&&c.checked)};
  b.disabled=true;
  call('/api/ops/library/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
    .then(function(d){toast('Kütüphane sıfırlandı · yedek: '+d.backup+(d.cache?' · '+num(d.cache_files)+' önbellek dosyası silindi':''))})
    .catch(function(e){toast('Sıfırlanamadı: '+(e&&e.message||e),'bad')})
    .then(function(){b.disabled=false});
}
/* ---- Canlı TV (/api/ops/live) ---- */
var LVH={ok:['ok','Çalışıyor'],youtube:['warn','Yalnız YouTube'],dead:['bad','Gizli (akış yok)'],inactive:['','Kaynakta yok']};
function liveLoad(){
  call('/api/ops/live/removed').then(function(d){LR=(d&&d.channels)||[];renderLive()}).catch(function(){});
  return call('/api/ops/live').then(function(d){LV=d;lvFail=false;renderLive()}).catch(function(e){lvFail=String(e&&e.message||e);renderLive()});
}
function liveHtml(){
  var on=!!(D&&D.live_tv&&D.live_tv.enabled);
  var h='<div class="sh"><span class="sname" style="font-size:16px">Saatlik kanal taraması</span>'+sw('live-auto',on,'Canlı TV otomatik tarama','data-live-auto="1"')+'</div>'+
    '<p class="hint">famelack kanal listesi (yalnız seçili dillerdeki kanallar); akışı çalışmayan ve YouTube’u olmayan kanallar istemcide gizlenir.</p>';
  if(lvFail&&!LV)return h+'<div class="empty">Durum alınamadı: '+esc(lvFail)+'</div>';
  if(!LV)return h+'<div class="empty">Yükleniyor…</div>';
  var c=LV.counts||{};
  h+='<dl class="skv"><div><dt>Ülke / dil</dt><dd>'+esc((LV.countries||[]).join(', ')||'-')+'<small>'+esc((LV.languages||[]).join(', ')||'tümü')+'</small></dd></div>'+
    '<div><dt>Son tarama</dt><dd>'+esc(LV.ran_at?when(LV.ran_at*1000):'hiç')+'<small>'+esc(LV.ran_at?ago(LV.ran_at*1000):'')+(LV.running?' · taranıyor…':'')+'</small></dd></div>'+
    '<div><dt>Kanallar</dt><dd>'+num(c.visible)+' görünür<small>'+num(c.ok)+' çalışan · '+num(c.youtube_only)+' yalnız YouTube · '+num(c.dead)+' gizli · '+num(c.total-c.active)+' kaynakta yok</small></dd></div></dl>'+
    '<div class="act"><button class="btn" id="live-scan"'+(LV.running?' disabled':'')+'>'+(LV.running?'Taranıyor…':'Şimdi tara')+'</button> '+
    '<button class="btn" id="live-list">'+(lvOpen?'Listeyi gizle':'Kanalları göster')+'</button> '+
    '<button class="btn" id="live-removed"'+(LR.length?'':' disabled')+'>'+(lrOpen&&LR.length?'Silinenleri gizle':'Silinen kanallar ('+num(LR.length)+')')+'</button></div>';
  if(lrOpen&&LR.length){
    h+='<p class="hint">İstemciden (OK basılı tut / çöp kutusu) herkesten silinen kanallar; taramalar geri getirmez. Geri al = yeniden görünür.</p>'+
      '<div style="max-height:320px;overflow:auto"><table style="width:100%;font-size:13px;border-collapse:collapse">'+LR.map(function(x){
      return '<tr style="border-top:1px solid var(--line)"><td style="padding:6px 6px 6px 0"><b>'+esc(x.title)+'</b><div class="hint">'+esc(x.source||'')+(x.country?' · '+esc(x.country):'')+'</div></td>'+
        '<td style="padding:6px;white-space:nowrap" class="num">'+esc(x.removed_at?when(x.removed_at*1000):'-')+'</td>'+
        '<td style="padding:6px;white-space:nowrap"><button class="btn" data-lv-restore="'+esc(x.id)+'" data-lv-rname="'+esc(x.title)+'">Geri al</button></td></tr>'}).join('')+'</table></div>';
  }
  if(lvOpen){
    var F=[['','Hepsi'],['ok','Çalışan'],['youtube','Yalnız YouTube'],['dead','Gizli'],['inactive','Kaynakta yok']];
    h+='<div class="act">'+F.map(function(f){return '<button class="btn'+(lvFilter===f[0]?' on':'')+'" data-lvf="'+f[0]+'" aria-pressed="'+(lvFilter===f[0])+'">'+f[1]+'</button>'}).join(' ')+'</div>';
    var rows=(LV.channels||[]).filter(function(x){return !lvFilter||x.health===lvFilter});
    h+='<div style="max-height:420px;overflow:auto"><table style="width:100%;font-size:13px;border-collapse:collapse">'+rows.map(function(x){
      var m=LVH[x.health]||['',x.health];
      var st=x.streams.map(function(s){return '<div class="mono" style="font-size:11.5px;overflow-wrap:anywhere">'+
        pill(s.status==='broken'||s.probe==='fail'?'bad':s.status==='healthy'||s.probe==='ok'?'ok':'',(s.probe||'?')+' · '+s.status)+
        (s.cors===false?' '+pill('warn','vekil'):'')+' '+esc(s.url)+(s.error?' <small>('+esc(s.error)+')</small>':'')+'</div>'}).join('');
      return '<tr style="border-top:1px solid var(--line);vertical-align:top"><td style="padding:6px 6px 6px 0;white-space:nowrap" class="num">'+esc(x.channel_no||'-')+'</td>'+
        '<td style="padding:6px"><b>'+esc(x.title)+'</b>'+(x.removed?' '+pill('bad','silindi'):'')+(x.geo_blocked?' '+pill('','geo'):'')+'<div class="hint">'+esc((x.categories||[]).join(', '))+(x.youtube?' · YouTube '+x.youtube:'')+'</div>'+st+'</td>'+
        '<td style="padding:6px;white-space:nowrap">'+pill(m[0],m[1])+'</td></tr>'}).join('')+'</table></div>';
  }
  return h;
}
function renderLive(){var el=$('live-card');if(el)el.innerHTML=liveHtml()}
function liveScan(b){
  b.disabled=true;
  call('/api/ops/live/scan',{method:'POST'}).then(function(){toast('Canlı TV taraması başladı');setTimeout(liveLoad,1500)})
    .catch(function(e){toast('Başlatılamadı: '+(e&&e.message||e),'bad');b.disabled=false});
}

/* ---- M3U kaynakları (/api/ops/live/sources; library/m3u.py) ---- */
var MLLM={not_needed:'gerekmedi',ok:'çalıştı',disabled:'kapalı (seçilmedi)'};
function m3uLoad(){
  return call('/api/ops/live/sources').then(function(d){MS=d;msFail=false;renderM3u()}).catch(function(e){msFail=String(e&&e.message||e);renderM3u()});
}
function llmText(s){if(!s)return '-';if(MLLM[s])return MLLM[s];if(s.indexOf('off:')===0)return 'atlandı ('+s.slice(4).trim()+')';return s}
function sumText(x){
  if(!x)return '';if(x.error)return 'Hata: '+x.error;
  return num(x.added)+' eklendi · '+num(x.updated)+' güncellendi · '+num(x.unchanged)+' aynı · '+num(x.skipped)+' atlandı · '+
    num(x.llm_fixed)+' LLM ile düzeltildi'+(x.deactivated?' · '+num(x.deactivated)+' kaldırıldı':'')+
    (x.problems?' · LLM: '+llmText(x.llm)+(x.unresolved?' ('+num(x.unresolved)+' satır okunamadı)':''):'');
}
function m3uHtml(){
  var mb=Math.round(((MS&&MS.max_bytes)||5242880)/1048576);
  var h='<div class="sh"><span class="sname" style="font-size:16px">M3U kaynakları</span></div>'+
    '<p class="hint">M3U/M3U8 listesi bağlantı ya da dosya olarak eklenir; kanallar Canlı TV’ye katılır (dil süzgeci yok). Bağlantılar saatlik taramayla yenilenir, dosya sabittir. En fazla '+mb+' MB.</p>'+
    '<div class="srow" style="flex-wrap:wrap;gap:8px;align-items:center">'+
      '<label class="chk"><input type="radio" name="m3u-kind" value="link" data-m3u-kind="1"'+(MF.kind==='link'?' checked':'')+'> Bağlantı</label>'+
      '<label class="chk"><input type="radio" name="m3u-kind" value="file" data-m3u-kind="1"'+(MF.kind==='file'?' checked':'')+'> Dosya</label>'+
      '<input type="text" id="m3u-name" data-m3u-field="name" maxlength="80" placeholder="Ad (örn. Ulusal kanallar)" value="'+esc(MF.name)+'" aria-label="Kaynak adı" style="min-width:180px">'+
      (MF.kind==='link'?'<input type="url" id="m3u-url" data-m3u-field="url" maxlength="2048" placeholder="https://…/liste.m3u" value="'+esc(MF.url)+'" aria-label="M3U bağlantısı" style="flex:1;min-width:240px">':
        '<input type="file" id="m3u-file" accept=".m3u,.m3u8,audio/x-mpegurl,application/vnd.apple.mpegurl,text/plain" aria-label="M3U dosyası">'+
        (MF.file?'<small>'+esc(MF.file.name)+' · '+num(Math.round(MF.file.size/1024))+' KB</small>':''))+
      '<label class="chk"><input type="checkbox" id="m3u-llm" data-m3u-llm="1"'+(MF.llm?' checked':'')+'> LLM ile düzelt</label>'+
      '<button class="btn" id="m3u-add"'+(msBusy?' disabled':'')+'>'+(msBusy?'İçe aktarılıyor…':'İçe aktar')+'</button></div>';
  if(msLast)h+='<p class="hint" style="margin-top:8px"><b>'+esc(msLast.name||'')+'</b>: '+esc(sumText(msLast.summary))+'</p>';
  if(msFail&&!MS)return h+'<div class="empty">Liste alınamadı: '+esc(msFail)+'</div>';
  if(!MS)return h+'<div class="empty">Yükleniyor…</div>';
  var rows=MS.sources||[];
  if(!rows.length)return h+'<div class="empty">Henüz M3U kaynağı yok.</div>';
  h+='<table style="width:100%;font-size:13px;border-collapse:collapse;margin-top:8px"><tr style="text-align:left"><th style="padding:6px 6px 6px 0">Ad</th><th>Tür</th><th>Kanal</th><th>Sağlıklı</th><th>Son tarama</th><th></th></tr>'+
    rows.map(function(x){
      return '<tr style="border-top:1px solid var(--line);vertical-align:top"><td style="padding:6px 6px 6px 0"><b>'+esc(x.name)+'</b>'+
        (x.url?'<div class="mono hint" style="font-size:11.5px;overflow-wrap:anywhere">'+esc(x.url)+'</div>':'')+
        (x.last?'<div class="hint">'+esc(sumText(x.last))+'</div>':'')+'</td>'+
        '<td style="padding:6px">'+(x.kind==='link'?'bağlantı':'dosya')+'</td>'+
        '<td style="padding:6px" class="num">'+num(x.channels)+'</td>'+
        '<td style="padding:6px" class="num">'+num(x.healthy)+'</td>'+
        '<td style="padding:6px;white-space:nowrap">'+esc(x.ran_at?when(x.ran_at*1000):'hiç')+'<small> '+esc(x.ran_at?ago(x.ran_at*1000):'')+'</small></td>'+
        '<td style="padding:6px;white-space:nowrap"><button class="btn" data-m3u-refresh="'+esc(x.id)+'"'+(msBusy?' disabled':'')+'>Yenile</button> '+
        '<button class="btn obdanger" data-m3u-del="'+esc(x.id)+'" data-m3u-dname="'+esc(x.name)+'"'+(msBusy?' disabled':'')+'>Sil</button></td></tr>'}).join('')+'</table>';
  return h;
}
function renderM3u(){var el=$('m3u-card');if(el)el.innerHTML=m3uHtml()}
function m3uDone(name,p){
  return p.then(function(d){msLast={name:name,summary:d.summary};toast('İçe aktarıldı: '+name);if(d.source!==undefined){MF.name='';MF.url='';MF.file=null}})
    .catch(function(e){toast('İçe aktarılamadı: '+(e&&e.message||e),'bad')})
    .then(function(){msBusy=false;return m3uLoad()}).then(function(){setTimeout(m3uLoad,8000);liveLoad()});
}
function m3uAdd(){
  var body={name:MF.name,kind:MF.kind,llm:!!MF.llm};
  if(MF.kind==='link'){if(!/^https?:\/\//i.test(MF.url||'')){toast('Geçerli bir http(s) bağlantısı girin','bad');return}body.url=MF.url}
  else{if(!MF.file){toast('Bir .m3u dosyası seçin','bad');return}body.content=MF.file.text;if(!body.name)body.name=MF.file.name.replace(/\.m3u8?$/i,'')}
  msBusy=true;renderM3u();
  m3uDone(body.name||MF.url||'M3U',call('/api/ops/live/sources',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}));
}
function m3uFile(inp){
  var f=inp.files&&inp.files[0];if(!f)return;
  var max=(MS&&MS.max_bytes)||5242880;
  if(f.size>max){toast('Dosya '+Math.round(max/1048576)+' MB sınırını aşıyor','bad');inp.value='';return}
  var r=new FileReader();
  r.onload=function(){MF.file={name:f.name,size:f.size,text:String(r.result||'')};renderM3u()};
  r.onerror=function(){toast('Dosya okunamadı','bad')};
  r.readAsText(f);
}

function render(){
  var root=$('set-root');if(!root)return;
  if(!D){root.innerHTML=failed?'<div class="empty">Ayarlar yüklenemedi: '+esc(failed)+'<br><button class="btn" id="set-retry">Yeniden dene</button></div>':'<div class="empty">Yükleniyor…</div>';return}
  var h='';
  h+='<section><h2>TMDB zenginleştirme</h2><div class="card tmdb" id="tmdb-card"></div></section>';
  h+='<section><h2>Canlı TV</h2><div class="card" id="live-card"></div><div class="card" id="m3u-card"></div></section>';
  h+=healHtml();
  h+=resetHtml();
  root.innerHTML=h;
  tLast='';renderTmdb();renderLive();renderM3u();
}

/* ---- yenileme ---- */
function poll(){
  if($('tab-settings').classList.contains('hidden')||document.hidden)return;
  var mc=$('m3u-card');if(mc&&mc.contains(document.activeElement)&&document.activeElement.tagName==='INPUT')return;   /* yazarken formu silme */
  load();
  liveLoad();
  if(!msBusy)m3uLoad();
}

/* ---- etkileşim ---- */
var root=$('set-root');
root.addEventListener('change',function(ev){
  var t=ev.target;
  if(t.dataset.tmdbAuto){
    var a=t.checked;
    save({tmdb:{auto:a}},'TMDB otomatik zenginleştirme '+(a?'açıldı':'kapatıldı'));
  } else if(t.dataset.tmdbType){
    var sel=[].slice.call(root.querySelectorAll('[data-tmdb-type]')).filter(function(x){return x.checked}).map(function(x){return x.dataset.tmdbType});
    save({tmdb:{types:sel}},'TMDB türleri: '+(sel.map(function(k){return KL[k]}).join(', ')||'hiçbiri')).then(tmdbLoad);
  } else if(t.dataset.liveAuto){
    var lv=t.checked;
    save({live_tv:{enabled:lv}},'Canlı TV otomatik tarama '+(lv?'açıldı':'kapatıldı')).then(liveLoad);
  } else if(t.dataset.m3uKind){
    MF.kind=t.value;renderM3u();
  } else if(t.dataset.m3uLlm){
    MF.llm=t.checked;
  } else if(t.id==='m3u-file'){
    m3uFile(t);
  } else if(t.dataset.healProvider){
    save({heal_provider:t.value},'Heal sağlayıcısı: '+t.value);
  } else if(t.dataset.autoapply){
    var v=t.checked;
    save({heal_autoapply:v},'Otomatik uygulama '+(v?'açıldı':'kapatıldı'));
  }
});
root.addEventListener('input',function(ev){
  var f=ev.target.dataset&&ev.target.dataset.m3uField;if(f)MF[f]=ev.target.value;
});
root.addEventListener('click',function(ev){
  var g=ev.target.closest('a[data-goto]');
  if(g){var tb=$('tab-btn-'+g.getAttribute('data-goto'));if(tb){ev.preventDefault();tb.click()}return}
  var b=ev.target.closest('button');if(!b)return;
  if(b.id==='llm-test'){loadLlm(true);return}
  if(b.id==='set-retry'){load();return}
  if(b.id==='lib-reset'){libReset(b);return}
  if(b.id==='live-scan'){liveScan(b);return}
  if(b.id==='live-list'){lvOpen=!lvOpen;renderLive();return}
  if(b.id==='live-removed'){lrOpen=!lrOpen;renderLive();return}
  if(b.dataset.lvRestore){
    b.disabled=true;
    call('/api/ops/live/channels/'+encodeURIComponent(b.dataset.lvRestore)+'/restore',{method:'POST'})
      .then(function(){toast('Geri alındı: '+b.dataset.lvRname);liveLoad()})
      .catch(function(e){toast('Geri alınamadı: '+(e&&e.message||e),'bad');b.disabled=false});
    return}
  if(b.id==='m3u-add'){m3uAdd();return}
  if(b.dataset.m3uRefresh){msBusy=true;renderM3u();
    var rn=(((MS&&MS.sources)||[]).filter(function(x){return x.id===b.dataset.m3uRefresh})[0]||{}).name||'';
    m3uDone(rn,call('/api/ops/live/sources/'+encodeURIComponent(b.dataset.m3uRefresh)+'/refresh',{method:'POST'}));return}
  if(b.dataset.m3uDel){
    if(!window.confirm('"'+b.dataset.m3uDname+'" kaynağı ve tüm kanalları silinsin mi?'))return;
    call('/api/ops/live/sources/'+encodeURIComponent(b.dataset.m3uDel),{method:'DELETE'})
      .then(function(d){toast('Silindi · '+num(d.removed)+' kanal kaldırıldı');m3uLoad();liveLoad()})
      .catch(function(e){toast('Silinemedi: '+(e&&e.message||e),'bad')});
    return}
  if(b.dataset.lvf!==undefined){lvFilter=b.dataset.lvf;renderLive();return}
  if(b.id==='tmdb-retry'){tFail=false;tmdbLoad();return}
  if(b.id==='tmdb-preview'){tmdbStart(true);return}
  if(b.id==='tmdb-apply'){tmdbStart(false);return}
  if(b.dataset.tkind){
    if(tKind!==b.dataset.tkind){tKind=b.dataset.tkind;tPrev=null;tDec='';tOff=0;renderTmdb();if(curSum())tmdbPreview(true)}
    return}
  if(b.dataset.tdec!==undefined){tDec=b.dataset.tdec;tmdbPreview(true);return}
  if(b.dataset.tpg){
    var off=tOff+PG*parseInt(b.dataset.tpg,10);
    tOff=Math.max(0,off);tmdbPreview(false);return}
});

/* ---- sekme kancası ---- */
function onShow(){
  load().then(tmdbLoad);
  liveLoad();
  m3uLoad();
  if(!llm||Date.now()-T(llm.checked_at)>60000)loadLlm(false);
  if(!tTimer)tTimer=setInterval(tmdbTick,2500);
  if(!pollTimer)pollTimer=setInterval(poll,20000);
}
window.dzTabHooks=window.dzTabHooks||{};
window.dzTabHooks.settings=onShow;
if(!$('tab-settings').classList.contains('hidden'))onShow();
})();
