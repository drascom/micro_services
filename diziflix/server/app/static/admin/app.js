(function(){
'use strict';
var $=function(i){return document.getElementById(i)};
var sites=[], events=[], nextBefore=null, sel=null, srcFilter='', typeFilter='all';
var lastKey=null, evLoaded=false, activeNow=[], lastHeal=null, onbHost={};

function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
function api(path,opt){return fetch(path,opt).then(function(r){return r.json()})}
function T(t){return t?new Date(t).getTime():0}
function ago(t){var d=(Date.now()-T(t))/1000;if(!T(t))return '-';d=Math.max(0,d);
  if(d<60)return Math.floor(d)+' sn önce';if(d<3600)return Math.floor(d/60)+' dk önce';
  if(d<86400)return Math.floor(d/3600)+' sa önce';return Math.floor(d/86400)+' g önce'}
function until(t){var s=(T(t)-Date.now())/1000;if(s<=0)return 'şimdi';return s<3600?Math.ceil(s/60)+' dk sonra':Math.round(s/360)/10+' sa sonra'}
function full(t){return T(t)?new Date(t).toLocaleString('tr-TR'):'-'}
function hhmm(t){return new Date(t).toLocaleTimeString('tr-TR',{hour:'2-digit',minute:'2-digit'})}
function dur(s){if(s==null)return '-';s=+s;if(s<60)return (Math.round(s*10)/10)+' sn';return Math.floor(s/60)+' dk '+Math.round(s%60)+' sn'}
function num(n){return n==null?'-':(+n).toLocaleString('tr-TR')}
function css(v){return getComputedStyle(document.documentElement).getPropertyValue(v).trim()}
function key(e){return e.kind+':'+e.id}
function hostOf(u){try{return new URL(u).host}catch(e){return String(u||'')}}

/* durum sözlüğü */
var SC={success:'ok',partial:'warn',error:'bad'};
var SL={success:'Başarılı',partial:'Kısmi',error:'Hata'};
var HL={fixed:'Düzeltildi',not_applied:'Uygulanmadı',failed:'İşe yaramadı',skipped_cooldown:'Cooldown',rolled_back:'Geri alındı'};
var HC={fixed:'heal',not_applied:'bad',failed:'bad',skipped_cooldown:'warn',rolled_back:'warn'};
var HI={fixed:'✦',not_applied:'✕',failed:'✕',skipped_cooldown:'⏸',rolled_back:'↶'};
/* "Site ekle" (onboarding) olayları */
var OL={running:'Çalışıyor',needs_input:'Yanıt bekliyor',ready:'Hazır',failed:'Başarısız',cancelled:'İptal edildi',saved:'Kaydedildi'};
/* Kaynak bulucu (Faz 6): oynatma kaynağı yokken arka planda arama */
var FL={found:'Bulundu',not_found:'Bulunamadı'};
var FM={retry:'kaynak yeniden çözüldü',search:'başka sitede bulundu',heal:'ajan onarımı'};
function finLabel(e){return (e.title||e.canonical_id||'?')+(e.season!=null&&e.episode!=null?' · S'+(e.season<10?'0':'')+e.season+' B'+(e.episode<10?'0':'')+e.episode:'')}
function onbCls(e){return e.status==='failed'?'bad':e.status==='needs_input'||(e.passed===false&&e.status!=='cancelled')?'warn':e.status==='saved'||e.status==='ready'?'ok':''}
function pill(cls,txt){return '<span class="pill '+cls+'">'+esc(txt)+'</span>'}
function evPill(e){
  if(e.kind==='onboard')return pill('heal','Site ekle');
  if(e.kind==='finder')return e.state==='found'?pill('ok','✓ Bulundu'):pill('warn','! Bulunamadı');
  if(e.kind==='tmdb')return e.status==='error'?pill('bad','✕ Hata'):e.dry_run?pill('run','Önizleme'):e.status==='partial'?pill('warn','! Kısmi'):pill('ok','✓ Uygulandı');
  if(e.kind==='scan')return pill(SC[e.status]||'',(e.status==='success'?'✓ ':e.status==='error'?'✕ ':'! ')+(SL[e.status]||e.status||'?'));
  return pill(HC[e.outcome]||'',(HI[e.outcome]||'')+' '+(HL[e.outcome]||e.outcome||'?'));
}
function siteHealth(s){
  if(s.heal_cooldown_until)return ['bad','Cooldown'];
  return {healthy:['ok','Sağlıklı'],partial:['warn','Kısmi'],drift:['warn','Drift'],error:['bad','Sorunlu'],never_run:['','Henüz yok']}[s.status]||['',s.status];
}
function spark(vals,col,w,h){
  vals=vals.filter(function(v){return v!=null});
  if(vals.length<2)return '';
  w=w||100;h=h||22;var mx=Math.max.apply(null,vals),mn=Math.min.apply(null,vals),sp=mx-mn||1;
  var p=vals.map(function(v,i){return (i/(vals.length-1)*w).toFixed(1)+','+(h-3-(v-mn)/sp*(h-6)).toFixed(1)}).join(' ');
  return '<svg class="spark" viewBox="0 0 '+w+' '+h+'" preserveAspectRatio="none" aria-hidden="true" style="height:'+h+'px"><polyline points="'+p+'" fill="none" stroke="'+col+'" stroke-width="1.8" vector-effect="non-scaling-stroke" stroke-linejoin="round"/></svg>';
}
/* otomatik tarama bilgisi (Ayarlar'dan gelir; yoksa boş) */
function autoLine(s){
  var a=s.auto_scan;if(!a)return '';
  if(!a.enabled)return 'planlı değil';
  var h=+a.interval_hours,every=h<1?Math.round(h*60)+' dk':(Math.round(h*100)/100)+' sa';
  return 'her '+every+' · sonraki '+(T(a.next_scan_at)-Date.now()>60000?until(a.next_scan_at):'birazdan');
}
function siteOf(id){return sites.filter(function(s){return s.site===id})[0]}
/* son oynatma çözümlemesi (state.last_resolver): "Son çözümleme: ok · 1,2 sn" */
function resolverLine(s){
  var r=s&&s.last_resolver;if(!r)return '';
  return 'Son çözümleme: '+(r.ok?'ok':'hata')+' · '+dur((+r.ms||0)/1000)+(r.at?' · '+ago(r.at):'');
}
function resolverHtml(s){
  var r=s&&s.last_resolver;if(!r)return '';
  var rows=(r.candidates||[]).map(function(c){
    return '<div class="mono" style="margin-top:4px"><span style="color:var(--'+(c.ok?'ok':'bad')+')">'+(c.ok?'ok':'hata')+'</span> · '+esc(c.provider||c.label||'-')+(c.lang?' · '+esc(c.lang):'')+
      (c.stage?' · '+esc(c.stage):'')+(c.host?' · '+esc(c.host):'')+' · '+dur((+c.ms||0)/1000)+(c.error?' · '+esc(c.error):'')+'</div>'}).join('');
  return '<h2 class="sec">Son oynatma çözümlemesi</h2><div class="note" style="border-color:var(--'+(r.ok?'ok':'bad')+')"><b>'+esc(resolverLine(s))+'</b>'+
    (r.page_ms?' · sayfa '+dur(r.page_ms/1000):'')+(r.streams!=null?' · '+num(r.streams)+' akış':'')+(r.error?'<br>'+esc(r.error):'')+rows+'</div>';
}
/* oynatma tetikli heal (Faz 5): pencere durumu, kanıt satırı, ajan günlüğü özeti */
function playbackHtml(s){
  var p=s&&s.playback_health;if(!p||(!p.window_n&&!p.last_trigger_at&&!p.last_skip))return '';
  return '<h2 class="sec">Oynatma sağlığı</h2><div class="note" style="border-color:var(--muted)">Pencere: <b>'+num(p.failed)+'</b> hata / '+num(p.window_n)+' kaynak'+
    (p.last_trigger_at?' · son oynatma heal: '+esc(ago(p.last_trigger_at)):'')+(p.last_skip?'<br>Atlandı: '+esc(p.last_skip.reason)+' · '+esc(ago(p.last_skip.at)):'')+'</div>';
}
function trigLabel(e){return e.trigger==='manual'?(e.playback?'elle · oynatma':'elle'):e.trigger==='playback'?'oynatma':e.trigger==='crawl'?'dizi sayfaları':'drift'}
function namedList(a){return (a||[]).map(function(x){return x.name+' ×'+x.count}).join(', ')}
function evidenceLine(ev){
  if(!ev)return '';
  var p=[num(ev.sources)+' kaynak'];
  if(ev.window_n!=null)p.push('pencere '+num(ev.window_failed)+'/'+num(ev.window_n));
  if(ev.stages&&ev.stages.length)p.push('aşama '+namedList(ev.stages));
  if(ev.hosts&&ev.hosts.length)p.push('host '+namedList(ev.hosts));
  return p.join(' · ');
}
function evidenceHtml(ev){
  if(!ev)return '';
  return '<h2 class="sec">Oynatma kanıtı</h2><div class="note" style="border-color:var(--muted)"><b>'+esc(evidenceLine(ev))+'</b>'+
    (ev.errors&&ev.errors.length?'<br>'+ev.errors.map(function(x){return '<span class="mono">'+esc(x.name)+' ×'+esc(x.count)+'</span>'}).join('<br>'):'')+
    (ev.ok_examples?'<br><small>çalışan örnek: '+num(ev.ok_examples)+'</small>':'')+'</div>';
}
function agentHtml(a){
  if(!a)return '';
  var tools=Object.keys(a.tools||{}).map(function(k){return k+' ×'+a.tools[k]}).join(' · ');
  return '<h2 class="sec">Ajan günlüğü</h2><div class="kv">'+kv(num(a.turns),'tur')+kv(num(a.events),'olay')+kv(num(a.tests),'test_config')+kv(num(a.errors),'hata')+'</div>'+
    (tools?'<div class="note" style="border-color:var(--muted)"><span class="mono">'+esc(tools)+'</span></div>':'')+
    (a.last_say?'<div class="note" style="white-space:pre-wrap">'+esc(a.last_say)+'</div>':'');
}

/* ---- üst: sistem + canlı çubuk ---- */
function renderSys(){
  var bad=sites.filter(function(s){return siteHealth(s)[0]==='bad'}).length;
  var warn=sites.filter(function(s){return siteHealth(s)[0]==='warn'}).length;
  var el=$('sys');
  if(!sites.length){el.className='pill';el.textContent='Kaynak yok';return}
  el.className='pill '+(bad?'bad':warn?'warn':'ok');
  el.innerHTML='<span class="dot"></span>'+(bad?bad+' kaynak sorunlu':warn?warn+' kaynak dikkat istiyor':'Sistem sağlıklı');
}
function renderBanner(){
  var el=$('banner'),h='';
  if(activeNow.length){
    h=activeNow.map(function(j){
      if(j.kind==='tmdb'){
        var pct=j.total>0?Math.min(100,Math.round((j.done||0)/j.total*100)):null;
        return '<div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap;width:100%"><span class="pill run"><span class="dot pulse"></span>'+(j.dry_run?'TMDB önizleme':'TMDB')+'</span>'+
          '<div class="grow"><b>'+esc(j.label||'TMDB zenginleştirme')+'</b> <span class="mono" style="color:var(--muted)">'+(pct!=null?'%'+pct+' · ':'')+dur(j.elapsed)+' geçti · başladı '+esc(hhmm(j.started_at))+'</span>'+
          '<div class="bar"><i class="'+(pct==null?'pulse':'')+'" style="width:'+(pct==null?100:pct)+'%;'+(pct==null?'opacity:.5':'')+'"></i></div></div></div>';
      }
      if(j.kind==='onboard'){
        var oh=hostOf(j.url||j.host||'')||onbHost[j.draft_id]||'';
        if(j.draft_id&&!(j.draft_id in onbHost)){ // aktif iş etiketi araç adına döner; host'u taslaktan bir kez al
          onbHost[j.draft_id]='';
          api('/api/ops/onboard/'+encodeURIComponent(j.draft_id)+'?events_after=1000000').then(function(d){
            onbHost[j.draft_id]=hostOf(d&&d.draft&&d.draft.url);renderBanner()}).catch(function(){});
        }
        return '<div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap;width:100%"><span class="pill heal"><span class="dot pulse"></span>Site ekle</span>'+
          '<div class="grow"><b>Site ekleniyor'+(oh?': '+esc(oh):'')+'</b>'+(j.label?' · '+esc(j.label):'')+' <span class="mono" style="color:var(--muted)">'+dur(j.elapsed)+' geçti · başladı '+esc(hhmm(j.started_at))+'</span>'+
          (j.draft_id?' <a class="ext" href="#onboard/'+esc(j.draft_id)+'">taslağı aç</a>':'')+
          '<div class="bar heal"><i class="pulse" style="width:100%;opacity:.5"></i></div></div></div>';
      }
      var heal=j.kind==='heal';
      return '<div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap;width:100%"><span class="pill '+(heal?'heal':'run')+'"><span class="dot pulse"></span>'+(heal?'LLM düzeltiyor':'Tarıyor')+'</span>'+
        '<div class="grow"><b>'+esc(j.site)+'</b> <span class="mono" style="color:var(--muted)">'+dur(j.elapsed)+' geçti · '+esc(j.trigger||'')+' · başladı '+esc(hhmm(j.started_at))+'</span>'+
        '<div class="bar '+(heal?'heal':'')+'"><i class="pulse" style="width:100%;opacity:.5"></i></div></div></div>'}).join('');
  } else {
    var lh=lastHeal&&(Date.now()-T(lastHeal.at))<600000?lastHeal:null;
    h='<span class="pill">Boşta</span><div class="grow"><b>Arka planda iş yok.</b>'+
      (lh?' <span style="color:var(--muted)">Az önce heal: '+esc(lh.site)+' '+esc(HL[lh.outcome]||lh.outcome)+' ('+esc(ago(lh.at))+')</span>':'')+'</div>';
  }
  el.innerHTML=h;
}

/* ---- kaynak süzgeçleri + sağlık şeridi ---- */
function renderFilters(){
  var c=css('--accent');
  var all='<button class="chip" data-src="" aria-pressed="'+(!srcFilter)+'"><span class="r"><span>Tüm kaynaklar</span></span><span class="s">'+sites.length+' kaynak</span></button>';
  $('filters').innerHTML=all+sites.map(function(s){
    var hl=siteHealth(s),rec=s.recent||[],l24=rec.slice(-24),cells='';
    for(var i=0;i<24;i++){var r=l24[l24.length-24+i];
      cells+=r?'<span class="'+(SC[r.status]||'')+'" title="'+esc(hhmm(r.started_at)+' '+(SL[r.status]||r.status)+' · '+num(r.scraped)+' öğe')+'"></span>':'<span></span>'}
    var last=rec[rec.length-1];
    var meta=last?(last.rate!=null?last.rate+' öğe/sn · ':'')+'son '+dur(last.duration):'henüz tarama yok';
    var auto=autoLine(s);
    return '<button class="chip" data-src="'+esc(s.site)+'" aria-pressed="'+(srcFilter===s.site)+'"><span class="r"><span>'+esc(s.site)+'</span>'+pill(hl[0],hl[1])+'</span>'+
      '<div class="strip" role="img" aria-label="Son 24 tarama durumu">'+cells+'</div>'+
      spark(rec.map(function(r){return r.duration}),c)+
      '<span class="s num">'+esc(meta)+'</span>'+
      (auto?'<span class="s num" title="Ayarlar sekmesinden yönetilir">'+esc(auto)+'</span>':'')+
      (resolverLine(s)?'<span class="s num" style="'+(s.last_resolver.ok?'':'color:var(--bad)')+'" title="Oynatma sırasında video sağlayıcısını çözme sonucu">'+esc(resolverLine(s))+'</span>':'')+
      (s.scanning?'<span class="s" style="color:var(--run)">taranıyor…</span>':s.healing?'<span class="s" style="color:var(--heal)">heal çalışıyor…</span>':
       s.heal_cooldown_until?'<span class="cd">heal cooldown: '+esc(until(s.heal_cooldown_until))+'</span>':'')+'</button>';
  }).join('');
}

/* ---- feed ---- */
function visible(){
  return events.filter(function(e){
    if(typeFilter==='scan'&&e.kind!=='scan')return false;
    if(typeFilter==='onboard'&&e.kind!=='onboard')return false;
    if(typeFilter==='heal'&&(e.kind==='scan'||e.kind==='tmdb'||e.kind==='onboard'||e.kind==='finder'))return false;
    if(typeFilter==='tmdb'&&!(e.kind==='tmdb'||(e.kind==='scan'&&e.tmdb_enrich)))return false;
    if(typeFilter==='bad'&&!(e.status==='error'||e.status==='partial'||e.outcome==='failed'||e.outcome==='not_applied'||e.kind==='cooldown'||e.kind==='rollback'||(e.kind==='onboard'&&(e.status==='failed'||e.passed===false))||(e.kind==='finder'&&e.state==='not_found')))return false;
    return true;
  });
}
function evHtml(e){
  var cls,title,meta;
  if(e.kind==='onboard'){
    cls='scan '+onbCls(e);
    title='Site ekle · '+esc(hostOf(e.url)||e.site_id||'?')+' '+evPill(e);
    meta=esc(OL[e.status]||e.status||'?')+(e.site_id?' · '+esc(e.site_id):'')+(e.turns!=null?' · '+num(e.turns)+' tur':'')+(e.passed===false?' · kriterler sağlanmadı':'')+(e.seconds!=null?' · '+dur(e.seconds):'');
  } else if(e.kind==='finder'){
    cls='scan '+(e.state==='found'?'ok':'warn');
    title='Kaynak bulucu · '+esc(finLabel(e))+' '+evPill(e);
    meta=esc(e.state==='found'?(FM[e.method]||e.method||'')+(e.site?' · '+e.site:''):'hiçbir yöntem akış bulamadı')+' · '+num((e.steps||[]).length)+' adım · '+dur(e.seconds);
  } else if(e.kind==='tmdb'){
    cls='scan '+(e.status==='error'?'bad':e.status==='partial'?'warn':'ok');
    if(e.scope==='seasons'){
      title='TMDB sezon ve bölüm görselleri '+evPill(e);
      meta=num(e.series)+' dizi · '+num(e.seasons)+' sezon · '+num(e.episodes)+' bölüm · '+num(e.posters)+' poster · '+num(e.stills)+' görsel'+(e.dry_run?'':' · '+num(e.written)+' sezon yazıldı')+' · '+dur(e.duration);
    } else {
      title='TMDB zenginleştirme · '+esc(e.media_type==='series'?'dizi':'film')+' '+evPill(e);
      meta=num(e.matched)+' eşleşti · '+num(e.review)+' inceleme · '+num(e.unmatched)+' eşleşmedi'+(e.dry_run?'':' · '+num(e.written)+' yazıldı')+' · '+dur(e.duration);
    }
  } else if(e.kind==='scan'){
    cls='scan '+(SC[e.status]||'');
    title=esc(e.site)+' '+evPill(e);
    meta=num(e.scraped)+' öğe · '+(e.pages==null?'-':e.pages)+' sayfa · '+dur(e.duration)+(e.rate!=null?' · '+e.rate+' öğe/sn':'')+(e.tmdb_enrich?' · TMDB':'')+(crawlOn(e.series_crawl)?' · '+num(e.series_crawl.new_episodes)+' yeni bölüm':'');
  } else {
    cls=e.kind==='heal'?'heal':e.kind;
    var f=(e.diff&&e.diff[0])?e.diff[0].path:'';
    title=(e.kind==='rollback'?'Geri alma':e.kind==='cooldown'?'Cooldown':(f?'<span class="mono" style="font-weight:500">'+esc(f)+'</span> düzeltmesi':e.playback?'Oynatma düzeltmesi':'Düzeltme'))+' · '+esc(e.site)+' '+evPill(e);
    meta=e.kind==='rollback'?'config v'+esc(e.new_version):e.kind==='cooldown'?esc(e.error||''):e.playback?esc('oynatma tetikli'+(e.evidence_summary?' · '+evidenceLine(e.evidence_summary):'')):esc((e.reasons||[]).join('; ')||(e.trigger==='manual'?'elle':'drift'));
  }
  return '<li class="ev '+cls+'" '+(sel===key(e)?'aria-current="true"':'')+'><span class="node" aria-hidden="true"></span><button data-k="'+esc(key(e))+'"><span class="t">'+title+'</span><span class="m">'+meta+'</span><span class="r">'+esc(hhmm(e.at))+'<br>'+esc(ago(e.at))+'</span></button></li>';
}
function renderFeed(){
  var v=visible(),out='',lastDay='';
  var today=new Date().toDateString(),yest=new Date(Date.now()-86400e3).toDateString();
  v.forEach(function(e){
    var d=new Date(e.at).toDateString();
    if(d!==lastDay){lastDay=d;out+='<li class="day">'+(d===today?'Bugün':d===yest?'Dün':d)+'</li>'}
    out+=evHtml(e);
  });
  $('feed').innerHTML=out||'<li class="empty">'+(evLoaded&&!events.length?'Henüz veri yok. İlk taramadan sonra olaylar burada görünür.':evLoaded?'Bu süzgeçle olay yok.':'Yükleniyor…')+'</li>';
  $('morew').style.display=nextBefore?'block':'none';
}

/* ---- detay ---- */
function catLine(){
  var withCat=sites.filter(function(s){return s.catalog});
  if(!withCat.length)return '';
  return '<div class="cat">Katalog: '+withCat.map(function(s){return esc(s.site)+' <b class="num">'+num(s.catalog.item_count)+'</b>'}).join(' · ')+'</div>';
}
function actions(site,extra,pb){
  var s=siteOf(site)||{},ph=s.playback_health;
  return '<div class="act">'+(extra||'')+
    '<button data-do="scan" data-site="'+esc(site)+'"'+(s.scanning?' disabled':'')+'>Tara</button>'+
    '<button data-do="heal" data-site="'+esc(site)+'"'+(s.healing?' disabled':'')+'>Heal</button>'+
    (pb||(ph&&ph.failed>0)?'<button data-do="heal-playback" data-site="'+esc(site)+'"'+(s.healing?' disabled':'')+'>Oynatma heal</button>':'')+'</div>';
}
function kv(v,l){return '<div><b>'+v+'</b><span>'+esc(l)+'</span></div>'}
function enrichHtml(t){
  if(!t||typeof t!=='object')return '';
  var map=[['matched','eşleşti'],['unmatched','eşleşmedi'],['review','inceleme'],['skipped','atlandı']],cells='';
  map.forEach(function(m){if(t[m[0]]!=null)cells+=kv(num(t[m[0]]),'TMDB '+m[1])});
  var d=t.seconds!=null?t.seconds:t.duration!=null?t.duration:t.duration_seconds;
  if(d!=null)cells+=kv(dur(d),'TMDB süresi');
  return cells?'<h2 class="sec">TMDB zenginleştirme</h2><div class="kv">'+cells+'</div>':'';
}
function crawlOn(t){return !!(t&&typeof t==='object'&&(t.due||t.series||t.errors))}
function crawlHtml(t){
  if(!crawlOn(t))return '';
  var cells=kv(num(t.series),'dizi tarandı')+kv(num(t.seasons),'sezon')+kv(num(t.episodes),'bölüm')+kv(num(t.new_episodes),'yeni bölüm')+
    kv(num(t.errors),'hata')+kv(num(t.deferred),'ertelendi')+kv(dur(t.seconds),'süre');
  var note='';
  if(t.stopped)note='<div class="note" style="border-color:var(--warn)">Tarama '+(t.stopped==='budget'?'dizi bütçesi (SERIES_CRAWL_BUDGET)':t.stopped==='time'?'süre bütçesi (SERIES_CRAWL_SECONDS)':'art arda hatalar')+' nedeniyle durdu; kalan '+num(t.deferred)+' dizi sonraki taramada.</div>';
  else if(t.incomplete||t.fallback)note='<div class="note" style="border-color:var(--muted)">'+(t.incomplete?num(t.incomplete)+' dizi envanteri eksik (sonra yeniden denenir)':'')+(t.incomplete&&t.fallback?' · ':'')+(t.fallback?num(t.fallback)+' dizi seçici yerine URL taramasıyla okundu':'')+'</div>';
  return '<h2 class="sec">Dizi bölüm envanteri</h2><div class="kv">'+cells+'</div>'+note;
}
function renderDetail(){
  var el=$('detail'),e=events.filter(function(x){return key(x)===sel})[0];
  if(!e){el.innerHTML='<div class="empty">Ayrıntısını görmek için soldan bir olay seçin.<br><small>Her tarama, düzeltme, cooldown ve geri alma bir olaydır.</small></div>'+catLine();return}
  var h='<button class="close" id="close">Kapat</button>',s=siteOf(e.site)||{};
  if(e.kind==='onboard'){
    h+=evPill(e)+'<h3>'+esc(hostOf(e.url)||'Site ekle')+'</h3><div class="sub">'+esc(full(e.at))+' · '+esc(ago(e.at))+'</div>'+
      '<div class="kv">'+kv(esc(OL[e.status]||e.status||'?'),'Durum')+kv(e.site_id?esc(e.site_id):'-','Site kimliği')+kv(num(e.turns),'tur')+kv(dur(e.seconds),'Süre')+'</div>';
    if(e.url)h+='<div class="note" style="border-color:var(--muted)"><span class="mono" style="overflow-wrap:anywhere">'+esc(e.url)+'</span></div>';
    if(e.passed===false)h+='<div class="note" style="border-color:var(--warn)">Kabul kriterleri sağlanmamıştı.</div>';
    if(e.error)h+='<div class="note" style="border-color:var(--bad)">'+esc(e.error)+'</div>';
    if(e.notes)h+='<h2 class="sec">Ajan notları</h2><div class="note" style="white-space:pre-wrap">'+esc(typeof e.notes==='string'?e.notes:JSON.stringify(e.notes))+'</div>';
    h+='<div class="act">'+(e.draft_id?'<a class="btn" href="#onboard/'+esc(e.draft_id)+'">Taslağı aç</a>':'<button data-goto="onboard">Siteler sekmesi</button>')+'</div>';
  } else if(e.kind==='finder'){
    h+=evPill(e)+' '+pill('heal','Kaynak bulucu')+'<h3>'+esc(finLabel(e))+'</h3><div class="sub">'+esc(full(e.at))+' · '+esc(ago(e.at))+(e.trigger?' · '+esc(e.trigger):'')+'</div>'+
      '<div class="kv">'+kv(esc(FL[e.state]||e.state||'?'),'Sonuç')+kv(e.method?esc(FM[e.method]||e.method):'-','Yöntem')+kv(e.site?esc(e.site):'-','Site')+kv(dur(e.seconds),'Süre')+'</div>';
    if(e.error)h+='<div class="note" style="border-color:var(--bad)">'+esc(e.error)+'</div>';
    h+='<h2 class="sec">Adımlar</h2>'+((e.steps||[]).map(function(st){
      return '<div class="mono" style="margin-top:4px"><span style="color:var(--'+(st.ok?'ok':'muted')+')">'+(st.ok?'bulundu':'yok')+'</span> · '+esc(st.name)+' · '+dur((+st.ms||0)/1000)+(st.note?' · '+esc(st.note):'')+'</div>'}).join('')||'<div class="note" style="border-color:var(--muted)">Adım kaydı yok.</div>');
    if(e.source_id)h+='<div class="note" style="border-color:var(--muted)">Kaynak: <span class="mono">'+esc(e.source_id)+'</span></div>';
  } else if(e.kind==='tmdb'){
    var seas=e.scope==='seasons';
    h+=evPill(e)+'<h3>'+(seas?'TMDB sezon ve bölüm görselleri':'TMDB zenginleştirme · '+esc(e.media_type==='series'?'dizi':'film'))+'</h3><div class="sub">'+esc(full(e.at))+' · '+esc(ago(e.at))+' · '+esc(e.trigger||'')+
      (e.dry_run?' · yazmadan önizleme':e.from_preview?' · önizlemeden uygulandı':' · canlı uygulama')+'</div>'+
      '<div class="kv">'+(seas?kv(num(e.series),'dizi')+kv(num(e.seasons),'sezon')+kv(num(e.episodes),'bölüm')+kv(num(e.posters),'sezon posteri')+kv(num(e.stills),'bölüm görseli')+kv(num(e.empty),'TMDB’de yok')
        :kv(num(e.matched),'otomatik eşleşme')+kv(num(e.review),'inceleme')+kv(num(e.unmatched),'eşleşmedi'))+kv(num(e.skipped),'atlandı')+
      (e.dry_run?'':kv(num(e.written),seas?'sezon yazıldı':'yazıldı'))+kv(num(e.errors),'hata')+kv(dur(e.duration),'Süre')+kv(num(e.lookup),seas?'sorgulanan dizi':'sorgulanan')+'</div>';
    if(e.aborted)h+='<div class="note" style="border-color:var(--warn)">TMDB ulaşılamadı; iş erken durduruldu.</div>';
    if(e.error)h+='<div class="note" style="border-color:var(--bad)">'+esc(e.error)+'</div>';
    h+='<div class="act"><button data-goto="settings">Ayarlar › TMDB</button></div>';
  } else if(e.kind==='scan'){
    var rec=(s.recent||[]);
    h+=evPill(e)+'<h3>'+esc(e.site)+'</h3><div class="sub">'+esc(full(e.at))+' · '+esc(ago(e.at))+(e.trigger?' · '+esc(e.trigger):'')+'</div>'+
      '<div class="kv">'+kv(dur(e.duration),'Süre')+kv(e.rate==null?'-':e.rate,'öğe/sn')+kv(num(e.scraped)+(e.ingested!=null?' <small style="font-size:12px;font-weight:400">('+num(e.ingested)+' aktarıldı)</small>':''),'öğe')+kv(e.pages==null?'-':e.pages,'sayfa')+'</div>';
    if(rec.length>1)h+='<h2>Süre trendi (son '+rec.length+' tarama)</h2>'+spark(rec.map(function(r){return r.duration}),css('--accent'),300,54)+
      '<h2 style="margin-top:12px">Öğe sayısı trendi</h2>'+spark(rec.map(function(r){return r.scraped}),css('--ok'),300,54);
    if(e.error)h+='<div class="note" style="border-color:var(--bad)">'+esc(e.error)+'</div>';
    if(e.collection_errors)h+='<div class="note" style="border-color:var(--warn)">'+esc(e.collection_errors)+' koleksiyon hatası</div>';
    if(s.drift&&s.drift_reasons&&s.drift_reasons.length)h+='<div class="note" style="border-color:var(--warn)"><b>Drift nedenleri</b><br>'+s.drift_reasons.map(esc).join('<br>')+'</div>';
    h+=crawlHtml(e.series_crawl)+enrichHtml(e.tmdb_enrich)+actions(e.site);
  } else if(e.kind==='rollback'){
    h+=evPill(e)+'<h3>'+esc(e.site)+'</h3><div class="sub">'+esc(full(e.at))+'</div>'+
      '<div class="note">Config v'+esc(e.new_version)+' sürümüne geri dönüldü.</div>'+actions(e.site);
  } else if(e.kind==='cooldown'){
    h+=evPill(e)+'<h3>'+esc(e.site)+'</h3><div class="sub">'+esc(full(e.at))+'</div>'+
      '<div class="note">Otomatik heal cooldown nedeniyle atlandı. Bitiş: <b>'+esc(full(e.cooldown_until))+'</b> ('+esc(until(e.cooldown_until))+')</div>'+
      ((e.reasons||[]).length?'<h2>Drift nedenleri</h2><div class="note">'+e.reasons.map(esc).join('<br>')+'</div>':'')+
      '<div class="note" style="border-color:var(--muted)">Elle Heal cooldown\'u yok sayar.</div>'+actions(e.site);
  } else {
    h+=evPill(e)+(e.playback?' '+pill('heal','Oynatma tetikli'):'')+'<h3>'+esc(e.site)+(e.diff&&e.diff[0]?' · <span class="mono">'+esc(e.diff[0].path)+'</span>':'')+'</h3>'+
      '<div class="sub">'+esc(full(e.at))+' · '+esc(e.provider||'-')+(e.model?' / '+esc(e.model):'')+' · '+dur(e.duration)+' · '+esc(trigLabel(e))+'</div>';
    if((e.reasons||[]).length)h+='<h2>'+(e.playback?'Nedenler':'Drift nedenleri')+'</h2><div class="note">'+e.reasons.map(esc).join('<br>')+'</div>';
    h+=evidenceHtml(e.evidence_summary);
    if(e.diff&&e.diff.length){
      h+='<h2 class="sec">Önce / sonra</h2><div class="diff">'+e.diff.map(function(d){
        return '<div class="mono" style="color:var(--muted);margin-top:6px">'+esc(d.path)+'</div><code class="b">− '+esc(d.before==null?'(yok)':d.before)+'</code><code class="a">+ '+esc(d.after==null?'(yok)':d.after)+'</code>'}).join('')+'</div>';
    } else h+='<div class="note" style="border-color:var(--muted)">Selector değişikliği yok.</div>';
    if(e.error)h+='<div class="note" style="border-color:var(--bad)">'+esc(e.error)+'</div>';
    h+='<div class="kv">'+kv(esc(HL[e.outcome]||e.outcome),'Sonuç')+kv(e.new_version?'v'+esc(e.new_version):'-','Yeni config')+'</div>';
    h+=agentHtml(e.agent_summary);
    h+=actions(e.site,e.can_rollback?'<button data-rollback="'+esc(e.site)+'">Bu düzeltmeyi geri al</button>':'',!!e.playback);
  }
  el.innerHTML=h+resolverHtml(siteOf(e.site))+playbackHtml(siteOf(e.site))+catLine();
}
function renderAll(){renderSys();renderBanner();renderFilters();renderFeed();renderDetail()}
function select(k){sel=k;renderFeed();renderDetail();var d=$('detail');if(window.innerWidth<=980){d.classList.add('open');d.focus()}}

/* ---- veri ---- */
function loadEvents(more){
  var q='/api/ops/events?limit=50'+(srcFilter?'&site='+encodeURIComponent(srcFilter):'')+(more&&nextBefore?'&before='+encodeURIComponent(nextBefore):'');
  return api(q).then(function(d){
    var list=d.events||[];
    if(more){events=events.concat(list);nextBefore=d.next_before}
    else{ // yenilemede daha önce yüklenen eski olayları koru
      var oldest=list.length?list[list.length-1].at:null;
      var kept=oldest&&d.next_before?events.filter(function(e){return e.at<oldest}):[];
      events=list.concat(kept);
      nextBefore=kept.length?nextBefore:d.next_before;
    }
    evLoaded=true;
    if(!events.some(function(e){return key(e)===sel}))sel=events.length?key(events[0]):null;
    renderFeed();renderDetail();
  });
}
function refresh(){
  return api('/api/ops/overview').then(function(d){sites=d.sites||[];renderSys();renderFilters();return loadEvents(false)});
}
function poll(){
  api('/api/ops/active').then(function(a){
    activeNow=a.active||[];lastHeal=a.last_heal;
    renderBanner();
    var k=activeNow.map(function(j){return j.site+j.kind}).join(',')+'|'+(a.last_run&&a.last_run.id)+'|'+(a.last_heal&&a.last_heal.id)+'|'+(a.last_tmdb&&a.last_tmdb.id)+'|'+(a.last_finder&&a.last_finder.id);
    if(k!==lastKey){var first=lastKey===null;lastKey=k;if(!first||!sites.length)refresh()}
    $('upd').textContent='güncellendi '+new Date().toLocaleTimeString('tr-TR');
  }).catch(function(){$('upd').textContent='bağlantı yok'});
}

/* ---- etkileşim ---- */
$('filters').addEventListener('click',function(ev){
  var b=ev.target.closest('.chip');if(!b)return;
  srcFilter=b.getAttribute('data-src')||'';nextBefore=null;events=[];evLoaded=false;renderFilters();renderFeed();loadEvents(false);
});
document.querySelector('.types').addEventListener('click',function(ev){
  var b=ev.target.closest('button');if(!b)return;typeFilter=b.getAttribute('data-type');
  document.querySelectorAll('.types button').forEach(function(x){x.setAttribute('aria-pressed',x===b)});renderFeed();
});
$('feed').addEventListener('click',function(ev){var b=ev.target.closest('button[data-k]');if(b)select(b.getAttribute('data-k'))});
$('more').addEventListener('click',function(){loadEvents(true)});
$('detail').addEventListener('click',function(ev){
  var t=ev.target.closest('button');if(!t)return;
  if(t.id==='close'){$('detail').classList.remove('open');return}
  if(t.dataset.goto){var tb=$('tab-btn-'+t.dataset.goto);if(tb)tb.click();return}
  if(t.dataset.rollback){
    if(!confirm(t.dataset.rollback+': önceki config sürümüne dönülsün mü?'))return;
    t.disabled=true;
    api('/api/ops/sites/'+encodeURIComponent(t.dataset.rollback)+'/rollback',{method:'POST'}).then(function(r){
      if(r&&r.ok)alert('v'+r.version+' geri yüklendi');
      else alert((r&&r.error&&r.error.message)||(r&&r.message)||'geri alınamadı');
      refresh();poll()}).catch(function(){t.disabled=false;alert('istek başarısız')});
    return;
  }
  if(t.dataset.do){
    t.disabled=true;
    var base='/api/ops/sites/'+encodeURIComponent(t.dataset.site)+'/'+t.dataset.do;
    api(base,{method:'POST'}).then(function(r){
      if(r&&r.reason==='no_drift'){
        if(confirm('Drift görünmüyor. Yine de heal çalıştırılsın mı (force)?'))return api(base+'?force=true',{method:'POST'});
        t.disabled=false;return}
      if(r&&r.started===false&&r.reason==='already_running')alert('Bu işlem zaten çalışıyor.');
      if(r&&r.started===false&&r.reason==='no_evidence')alert('Oynatma kanıtı yok: önce bu sitenin birkaç kaynağı oynatılmalı.');
      return r}).then(function(){poll();refresh()}).catch(function(){t.disabled=false});
  }
});
document.addEventListener('dz:settings-changed',function(){refresh()});
var mq=window.matchMedia&&window.matchMedia('(prefers-color-scheme:dark)');
if(mq&&mq.addEventListener)mq.addEventListener('change',renderAll);

renderAll();refresh();poll();
setInterval(poll,2500);
setInterval(function(){refresh()},30000);
})();
