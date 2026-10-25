/* Ayarlar sekmesi: alt menü Genel · Görünüm (backgrounds.js) · Kategoriler (categories.js) · Sistem olayları (errors.js).
   Genel = kompakt satırlar (etiket solda, kontrol sağda): dış erişim anahtarı (/api/ops/access-key), heal (/api/ops/settings, /api/ops/llm/health), TMDB
   (/api/ops/tmdb/status|preview|backfill; "Sezon" = kind=series + scope=seasons), Bakım (kütüphaneyi sıfırla).
   Değişiklik anında kaydedilir (PUT) ve yeniden başlatma gerektirmez. Canlı TV ayarları "Canlı TV" sekmesinde (live.js). */
(function(){
'use strict';
var $=function(i){return document.getElementById(i)};
var D=null,failed=false,llm=null,llmBusy=false,pollTimer=null;
var TS=null,tFail=false,tKind='movie',tDec='',tOff=0,tPrev=null,tPrevBusy=false,tStarting=false,tLast='',tTimer=null,tTicks=0,tWatch=false,tLastId=null;
var SECS=[['general','Genel'],['look','Görünüm'],['categories','Kategoriler'],['system','Sistem olayları']],sec='general';
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
    return '<div class="pempty">Henüz önizleme yok.</div>';
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
function rowH(label,ctl,tip,cls){return '<div class="srow2'+(cls?' '+cls:'')+'"'+(tip?' title="'+esc(tip)+'"':'')+'><span class="sk">'+label+'</span><span class="sv">'+ctl+'</span></div>'}
function covRow(k){
  var c=((TS&&TS.coverage)||{})[k],on=tTypes().indexOf(k)>=0;if(!c)return '';
  var txt=!c.total?'yok':num(c.matched)+' / '+num(c.total)+' TMDB’li'+(c.review?' · '+num(c.review)+' inceleme':'')+(c.no_poster?' · '+num(c.no_poster)+' posteri yer tutucu':'')+(c.no_backdrop?' · '+num(c.no_backdrop)+' afişsiz':'');
  return rowH(KLP[k]+(on?'':' <span class="dim">(seçili değil)</span>'),'<span class="dim">'+txt+'</span>'+covBar(c),'',on?'':'off');
}
function seasonRow(){
  var c=((TS&&TS.coverage)||{}).seasons;if(!c)return '';
  var txt=!c.seasons?'sezonlu dizi yok':num(c.season_posters)+' / '+num(c.seasons)+' sezon posteri · '+num(c.episode_stills)+' / '+num(c.episodes)+' bölüm görseli'+(c.series_tmdb<c.series?' · '+num(c.series-c.series_tmdb)+' dizi TMDB’siz':'');
  return rowH('Sezon/bölüm','<span class="dim">'+txt+'</span>'+seasonBar(),'',tTypes().indexOf('series')>=0?'':'off');
}
function tmdbHtml(){
  var T0=D&&D.tmdb||{},cfg=TS?TS.configured:T0.configured,types=tTypes();
  var h=rowH('Anahtar',cfg?pill('ok','Tanımlı'):pill('bad','Yok'),cfg?'':'Sunucudaki .env dosyasına TMDB_ACCESS_KEY eklenip servis yeniden başlatılmalı');
  if(tFail&&!TS)return h+'<div class="note bad">Durum alınamadı: '+esc(tFail)+' <button class="btn" id="tmdb-retry">Yeniden dene</button></div>';
  if(!TS)return h+'<div class="empty sm">Yükleniyor…</div>';
  h+=covRow('movie')+covRow('series')+seasonRow();
  h+=rowH('Otomatik zenginleştir',sw('tmdb-auto',!!T0.auto,'Yeni öğeler için otomatik zenginleştir','data-tmdb-auto="1"'),
    'Yeni taranan film/dizi için eksik poster, afiş ve bilgiler ingest sırasında TMDB’den alınır; eşleşemeyenler '+(TS.retry_days||7)+' gün sonra yeniden denenir'+(cfg?'':' (anahtar yokken etkisiz)'));
  h+=rowH('Türler','<span class="tchk">'+['movie','series'].map(function(k){return '<label class="chk"><input type="checkbox" data-tmdb-type="'+k+'"'+(types.indexOf(k)>=0?' checked':'')+'> '+KLP[k]+'</label>'}).join('')+'</span>',
    'Hem otomatik akışta hem elle çalıştırmada bu türler kullanılır');
  var need=isSeasons()?'series':tKind;
  var running=!!TS.job,dis=!cfg||running||tStarting||types.indexOf(need)<0;
  h+=rowH('Elle çalıştır','<span class="seg" role="group" aria-label="Tür">'+['movie','series','seasons'].map(function(k){
    return '<button data-tkind="'+k+'" aria-pressed="'+(tKind===k)+'"'+(k==='seasons'?' title="'+esc(KLP.seasons)+': sezon posterleri + bölüm başlık/özet/görselleri (yalnız eksikler)"':'')+'>'+(k==='seasons'?'Sezon':KLP[k])+'</button>'}).join('')+'</span>'+
    '<button class="btn" id="tmdb-preview"'+(dis?' disabled':'')+' title="Yazmadan TMDB’de ara, sonucu aşağıda göster">Önizle</button>'+
    '<button class="btn primary" id="tmdb-apply"'+(dis?' disabled':'')+' title="Eksikleri doldur">Uygula</button>',
    cfg&&types.indexOf(need)<0?KLP[need]+' türü seçili değil':'');
  if(running)h+=jobBar(TS.job);
  else if(TS.prewarm)h+='<div class="evn"><span class="dot pulse"></span> Resimler sunucu önbelleğine indiriliyor…</div>';
  else if(TS.last_job&&!TS.last_job.dry_run&&TS.last_job.status==='error')h+='<div class="evn bad"><span class="evni">✕</span><span>Son çalışma hata verdi: '+esc(TS.last_job.error||'bilinmeyen hata')+'</span></div>';
  if(curSum()||(tPrev&&tPrev.available))h+='<div class="sprev">'+previewHtml()+'</div>';
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
  if(!genVisible())return;
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
  var det=[];
  if(llm){
    if(llm.provider)det.push(esc(llm.provider));
    if(llm.expires_at)det.push('bitiş '+esc(T(llm.expires_at)>Date.now()?secText((T(llm.expires_at)-Date.now())/1000)+' sonra':ago(llm.expires_at)));
    if(llm.checked_at)det.push('kontrol '+esc(ago(llm.checked_at)));
  }
  var tip=llm?[llm.model?'Model: '+llm.model:'',llm.refreshed_at?'Son yenileme: '+when(llm.refreshed_at):'',llm.cached?'önbellekten':''].filter(Boolean).join(' · '):'';
  var h=rowH('LLM hesabı',llmBadge()+(det.length?'<span class="dim">'+det.join(' · ')+'</span>':'')+
    '<button class="btn" id="llm-test"'+(llmBusy?' disabled':'')+'>'+(llmBusy?'Test ediliyor…':'Test et')+'</button>',tip);
  if(!llm)return h;
  if(llm.status==='expired')h+='<div class="evn bad"><span class="evni">✕</span><span>Yeniden giriş gerekli: sunucuda <code>pi</code> → <code>/login</code> ('+esc(llm.provider||'openai-codex')+'), sonra “Test et”.</span></div>';
  else if(llm.status==='error')h+='<div class="evn warn"><span class="evni">⚠</span><span title="'+esc(llm.detail||'')+'">Hesap durumu okunamadı; sunucuda <code>pi auth check</code> ile bakın.</span></div>';
  else if(llm.status==='pi_missing')h+='<div class="evn"><span class="evni">ℹ</span><span>Bu sunucuda <code>pi</code> kurulu değil.</span></div>';
  if(llm.status!=='pi_missing'&&llm.provider_is_pi===false)h+='<div class="evn"><span class="evni">ℹ</span><span>Heal sağlayıcısı <b>'+esc(llm.heal_provider)+'</b>; bu kontrol <code>pi</code> hesabı içindir.</span></div>';
  return h;
}
function renderLlm(){var el=$('llm-card');if(el)el.innerHTML=llmHtml()}
function healHtml(){
  var h=D.heal||{};
  return '<section><h2>Kendi kendini düzeltme</h2>'+
    rowH('Heal',h.enabled?pill('ok','Etkin'):pill('','Kapalı'),h.enabled?'':'SCRAPER_HEAL_ENABLED=false')+
    rowH('Otomatik uygula',sw('heal-auto',!!h.autoapply,'Düzeltmeyi otomatik uygula','data-autoapply="1"'),
      'Kapalıyken LLM düzeltme önerir ama uygulamaz (olay defterinde “Uygulanmadı”). Açıkken doğrulamadan geçen öneri yeni config sürümü olarak hemen etkinleşir; gerekirse geri alınır')+
    rowH('Sağlayıcı',(h.provider_missing?'<span class="bad" title="'+esc(h.provider_missing)+' CLI bu sunucuda kurulu değil">heal çalışmaz</span>':'')+
      '<select id="heal-provider" data-heal-provider="1" aria-label="Heal sağlayıcısı">'+
      (h.providers||['pi_agent']).map(function(p){return '<option value="'+esc(p)+'"'+(p===h.provider?' selected':'')+'>'+esc(p)+(p==='pi_agent'?' (önerilen)':'')+'</option>'}).join('')+'</select>',
      'Model: '+(h.model||'varsayılan')+' · başarısız denemeden sonra cooldown '+secText(h.cooldown_seconds||0))+
    '<div id="llm-card">'+llmHtml()+'</div></section>';
}
/* ---- erişim anahtarı (dış istekler X-DZ-Key; /api/ops/access-key). Sunucu üretmez: admin yazar (>= min_length); boşken dış /api/* kapalı ---- */
var AK=null,akShow=false;
function akMin(){return (AK&&AK.min_length)||12}
function accessHtml(fresh){
  var k=(AK&&AK.key)||'',old=$('ak-val'),v=!fresh&&old?old.value:k;   /* yeniden çizimde yazılmakta olan taslak korunur */
  return '<section><h2>Dış erişim</h2>'+rowH('Erişim anahtarı'+(AK&&!k?' '+pill('bad','tanımsız'):''),
    '<input id="ak-val" type="'+(akShow?'text':'password')+'" autocomplete="off" spellcheck="false" placeholder="en az '+akMin()+' karakter" value="'+esc(v)+'" aria-label="Erişim anahtarı">'+
    '<button class="btn" id="ak-show">'+(akShow?'Gizle':'Göster')+'</button>'+
    '<button class="btn" id="ak-copy">Kopyala</button>'+
    '<button class="btn" id="ak-save">Kaydet</button>',
    'İnternetten ('+esc(((AK&&AK.public_hosts)||[]).join(', ')||'dış alan adı')+') bağlanan istemciler bu anahtarı “Sunucu adresi” ekranına girer (header X-DZ-Key). Anahtar boşken dışarıdan hiçbir API isteği kabul edilmez. Yerel ağ / NetBird anahtarsız çalışır; admin dışarıdan kapalıdır')+'</section>';
}
function renderAccess(fresh){var el=$('ak-card');if(el)el.innerHTML=accessHtml(fresh)}
function akVal(){var i=$('ak-val');return i?i.value.trim():''}
function akLoad(){return call('/api/ops/access-key',{cache:'no-cache'}).then(function(d){AK=d;renderAccess(true)}).catch(function(e){toast('Erişim anahtarı alınamadı: '+(e&&e.message||e),'bad')})}
function akCopy(){
  var k=akVal();if(!k){toast('Anahtar boş','bad');return}
  function fallback(){var t=document.createElement('textarea');t.value=k;document.body.appendChild(t);t.select();
    try{document.execCommand('copy');toast('Kopyalandı')}catch(e){toast('Kopyalanamadı','bad')}document.body.removeChild(t)}
  if(navigator.clipboard&&window.isSecureContext)navigator.clipboard.writeText(k).then(function(){toast('Kopyalandı')},fallback);else fallback();
}
function akSave(){
  var k=akVal();
  if(k.length<akMin()){toast('Anahtar en az '+akMin()+' karakter olmalı; kaydedilmedi','bad');return}
  if(/\s/.test(k)){toast('Anahtarda boşluk olamaz; kaydedilmedi','bad');return}
  if(AK&&AK.key&&AK.key!==k&&!window.confirm('Erişim anahtarı değişsin mi? Eski anahtarla dışarıdan bağlanan istemciler yeni anahtarı girene kadar bağlanamaz.'))return;
  call('/api/ops/access-key',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:k})})
    .then(function(d){AK=d;renderAccess(true);toast('Erişim anahtarı kaydedildi')})
    .catch(function(e){toast('Kaydedilemedi: '+(e&&e.message||e),'bad')});
}
function resetHtml(){
  return '<section><h2>Bakım</h2>'+rowH('Kütüphaneyi sıfırla',
    '<label class="chk" title="Görsel ve altyazı önbelleğini de sil"><input type="checkbox" id="reset-cache"> önbellek de</label>'+
    '<button class="btn obdanger" id="lib-reset">Sıfırla</button>',
    'Deneme amaçlı: tüm dizi/film, bölüm, kaynak, kategori üyeliği, izleme ilerlemesi ve listem silinir. Kategoriler, profiller, ayarlar ve site config’leri korunur; önce veritabanı yedeği alınır')+'</section>';
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
function render(){
  var root=$('set-root');if(!root)return;
  if(!D){root.innerHTML=failed?'<div class="note bad">Ayarlar yüklenemedi: '+esc(failed)+' <button class="btn" id="set-retry">Yeniden dene</button></div>':'<div class="empty sm">Yükleniyor…</div>';return}
  root.innerHTML=healHtml()+'<div id="ak-card">'+accessHtml()+'</div>'+'<section><h2>TMDB zenginleştirme</h2><div id="tmdb-card" class="tmdb"></div></section>'+resetHtml();
  if(!AK)akLoad();
  tLast='';renderTmdb();
}

/* ---- yenileme ---- */
function genVisible(){return !$('tab-settings').classList.contains('hidden')&&!document.hidden&&sec==='general'}
function poll(){
  if(!genVisible())return;
  var a=document.activeElement;if(a&&$('set-root').contains(a)&&a.tagName==='SELECT')return;
  load();
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
  } else if(t.dataset.healProvider){
    save({heal_provider:t.value},'Heal sağlayıcısı: '+t.value);
  } else if(t.dataset.autoapply){
    var v=t.checked;
    save({heal_autoapply:v},'Otomatik uygulama '+(v?'açıldı':'kapatıldı'));
  }
});
root.addEventListener('click',function(ev){
  var g=ev.target.closest('a[data-goto]');
  if(g){var tb=$('tab-btn-'+g.getAttribute('data-goto'));if(tb){ev.preventDefault();tb.click()}return}
  var b=ev.target.closest('button');if(!b)return;
  if(b.id==='llm-test'){loadLlm(true);return}
  if(b.id==='set-retry'){load();return}
  if(b.id==='lib-reset'){libReset(b);return}
  if(b.id==='ak-show'){akShow=!akShow;var i=$('ak-val');if(i)i.type=akShow?'text':'password';b.textContent=akShow?'Gizle':'Göster';return}
  if(b.id==='ak-copy'){akCopy();return}
  if(b.id==='ak-save'){akSave();return}
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

/* ---- alt menü: Genel · Görünüm · Kategoriler · Sistem olayları (diğer modüllerin kancaları dzTabHooks.backgrounds/categories/sysevents) ---- */
function navHtml(){return SECS.map(function(x){return '<button role="tab" data-sec="'+x[0]+'" aria-selected="'+(x[0]===sec)+'">'+x[1]+'</button>'}).join('')}
function showSec(name){
  if(!SECS.some(function(x){return x[0]===name}))name='general';
  sec=name;
  var nav=$('set-nav');if(nav)nav.innerHTML=navHtml();
  SECS.forEach(function(x){var el=$('sec-'+x[0]);if(el)el.classList.toggle('hidden',x[0]!==name)});
  try{history.replaceState(null,'','#settings'+(name==='general'?'':'/'+name))}catch(e){}
  var hooks=window.dzTabHooks||{};
  if(name==='general'){
    load().then(tmdbLoad);
    if(!llm||Date.now()-T(llm.checked_at)>60000)loadLlm(false);
    if(!tTimer)tTimer=setInterval(tmdbTick,2500);
    if(!pollTimer)pollTimer=setInterval(poll,20000);
  }
  else if(name==='look'&&hooks.backgrounds)hooks.backgrounds();
  else if(name==='categories'&&hooks.categories)hooks.categories();
  else if(name==='system'&&hooks.sysevents)hooks.sysevents();
}
$('set-nav').addEventListener('click',function(ev){var b=ev.target.closest('[data-sec]');if(b)showSec(b.getAttribute('data-sec'))});
window.dzSettingsSec=function(){return sec};

/* ---- sekme kancası ---- */
function onShow(){
  var want=window.dzSetSecPending;window.dzSetSecPending=null;
  showSec(want||sec);
}
window.dzTabHooks=window.dzTabHooks||{};
window.dzTabHooks.settings=onShow;
if(!$('tab-settings').classList.contains('hidden'))onShow();
})();
