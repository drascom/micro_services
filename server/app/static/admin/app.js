/* admin ortak modülü */
(function(){
'use strict';
var $=function(i){return document.getElementById(i)};
/* Ortak olay modülü (window.dzEv): olay rozeti/başlık/ayrıntı + üst sistem çubuğu/banner + /api/ops/active yoklaması.
   Olay listeleri Siteler (site olayları), Hatalar ve Ayarlar (sistem olayları) sekmelerinde dzEv.list ile çizilir. */
var sites=[], liveSrc=[], lastKey=null, activeNow=[], lastHeal=null;

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
/* Kaynak bulucu (Faz 6): oynatma kaynağı yokken arka planda arama */
var FL={found:'Bulundu',not_found:'Bulunamadı'};
var FM={retry:'kaynak yeniden çözüldü',search:'başka sitede bulundu',heal:'ajan onarımı'};
function finLabel(e){return (e.title||e.canonical_id||'?')+(e.season!=null&&e.episode!=null?' · S'+(e.season<10?'0':'')+e.season+' B'+(e.episode<10?'0':'')+e.episode:'')}
/* engelli sayfa (blocked_pages): neden */
var BV={gone:'Ölü sayfa',empty:'Bölümsüz',gate:'Oynatıcı yok',rule:'Telif/engel'};
function pill(cls,txt){return '<span class="pill '+cls+'">'+esc(txt)+'</span>'}
function pbiLabel(e){return (e.title||e.canonical_id||'?')+(e.season!=null&&e.episode!=null?' · S'+(e.season<10?'0':'')+e.season+' B'+(e.episode<10?'0':'')+e.episode:'')}
function evPill(e){
  if(e.kind==='live')return e.status==='error'?pill('bad','✕ Hata'):e.status==='no_change'?pill('','Değişiklik yok'):pill('ok','✓ Başarılı');
  if(e.kind==='reset')return pill('warn','Sıfırlama');
  if(e.kind==='hydrate')return pill(e.status==='error'?'bad':'warn','! Bölüm yok');
  if(e.kind==='blocked_page')return pill('warn','⊘ '+(BV[e.via]||e.via||'Engelli'));
  if(e.kind==='playback_issue')return pill(e.heal_class?'bad':'warn',(e.heal_class?'✕ ':'! ')+(e.label||e.code||'?'));
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
  var p=s&&s.playback_health,pi=p&&p.issues&&p.issues.open;if(!p||(!p.window_n&&!p.last_trigger_at&&!p.last_skip&&!pi))return '';
  return '<h2 class="sec">Oynatma sağlığı</h2><div class="note" style="border-color:var(--muted)">Pencere: <b>'+num(p.failed)+'</b> hata / '+num(p.window_n)+' kaynak'+(pi?' · açık oynatma sorunu: <b>'+num(p.issues.open)+'</b> kaynak ('+num(p.issues.heal_open)+' heal sınıfı)':'')+
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
/* ---- detay ---- */
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
function detailHtml(e){
  var h='',s=siteOf(e.site)||{};
  if(e.kind==='live'){
    var TG={auto:'otomatik',manual:'elle',cli:'komut satırı'};
    h+=evPill(e)+' '+pill('heal','Canlı TV')+'<h3>'+esc(e.site)+' · '+esc((e.countries||[]).join(', ').toUpperCase()||'-')+'</h3><div class="sub">'+esc(full(e.at))+' · '+esc(ago(e.at))+' · '+esc(TG[e.trigger]||e.trigger||'')+'</div>';
    if(e.status==='error')h+='<div class="note" style="border-color:var(--bad)">'+esc(e.error||'hata')+'</div>';
    else{
      if(e.status==='no_change')h+='<div class="note" style="border-color:var(--muted)">Kaynak listesi değişmedi (ETag 304); kanal kaydı yazılmadı'+(e.probed!=null?', yalnız sağlık yoklaması yapıldı':'')+'.</div>';
      else h+='<h2 class="sec">Kanal değişiklikleri</h2><div class="kv">'+kv(num(e.new),'yeni')+kv(num(e.updated),'güncellendi')+kv(num(e.deactivated),'pasifleşti')+kv(num(e.unchanged),'aynı')+'</div>';
      h+='<h2 class="sec">Sağlık</h2><div class="kv">'+kv(num(e.ok),'sağlıklı (m3u8)')+kv(num(e.youtube_only),'yalnız YouTube')+kv(num(e.hidden),'gizli (oynatılamaz)')+kv(num(e.visible),'görünür')+kv(num(e.active),'aktif')+kv(num(e.inactive),'pasif')+'</div>';
      if(e.probed!=null)h+='<div class="note" style="border-color:var(--muted)">Yoklama: '+num(e.probe_ok)+' / '+num(e.probed)+' akış yanıt verdi'+(e.no_cors?' · '+num(e.no_cors)+' CORS yok (vekil)':'')+'</div>';
    }
    h+='<div class="kv">'+kv(dur(e.duration),'Süre')+kv(num(e.total),'kaynaktaki kanal')+kv(num(e.kept),'dil süzgecinden geçen')+'</div>'+
      '<div class="act"><button data-goto="onboard">Siteler sekmesi</button><button data-goto="settings">Ayarlar › Canlı TV</button></div>';
  } else if(e.kind==='blocked_page'){
    h+=evPill(e)+'<h3>'+esc(e.source_key||e.url)+'</h3><div class="sub">'+esc(full(e.at))+' · '+esc(ago(e.at))+'</div>'+
      '<div class="kv">'+kv(esc(e.site||'-'),'Site')+kv(esc(BV[e.via]||e.via||'-'),'Neden')+kv(esc(e.page_kind||'-'),'Sayfa türü')+kv(e.until?esc(full(e.until)):'-','Yeniden denenir')+'</div>'+
      '<div class="note" style="border-color:var(--warn)">'+esc(e.reason||'')+'</div>'+
      '<div class="note" style="border-color:var(--muted)"><span class="mono" style="overflow-wrap:anywhere">'+esc(e.url)+'</span><br>Engel yalnız bu sitedeki bu sayfa içindir; aynı yapım başka siteden gelebilir.</div>'+
      '<div class="act"><button data-unblock="'+esc(e.url)+'" data-site="'+esc(e.site)+'">Engeli kaldır</button></div>';
  } else if(e.kind==='hydrate'){
    h+=evPill(e)+'<h3>'+mediaLink(mediaRef(e),esc(e.title||e.canonical_id||'?'))+'</h3><div class="sub">'+esc(full(e.at))+' · '+esc(ago(e.at))+'</div>'+
      '<div class="kv">'+kv(esc(e.site||'-'),'Site')+kv(esc(e.status||'-'),'Durum')+kv('<span class="mono">'+esc(e.source_key||'-')+'</span>','Kaynak anahtarı')+'</div>'+
      '<div class="note" style="border-color:var(--'+(e.status==='error'?'bad':'warn')+')">'+esc(e.reason||'')+'</div>'+
      (e.url?'<div class="note" style="border-color:var(--muted)"><span class="mono" style="overflow-wrap:anywhere">'+esc(e.url)+'</span></div>':'');
  } else if(e.kind==='reset'){
    h+=evPill(e)+'<h3>Kütüphane sıfırlandı</h3><div class="sub">'+esc(full(e.at))+' · '+esc(ago(e.at))+'</div>'+
      '<div class="kv">'+kv(num((e.cleared||[]).length),'temizlenen tablo')+kv(Object.keys(e.kept||{}).map(esc).join(', ')||'-','korunan')+kv(e.cache?num(e.cache_files):'-','önbellek dosyası')+kv(dur(e.seconds),'Süre')+'</div>'+
      '<div class="note" style="border-color:var(--muted)">Yedek: <span class="mono">'+esc(e.backup||'-')+'</span></div>';
  } else if(e.kind==='playback_issue'){
    h+=evPill(e)+' '+pill('heal','Oynatma sorunu')+'<h3>'+esc(e.site)+(e.host?' · <span class="mono">'+esc(e.host)+'</span>':'')+'</h3><div class="sub">'+esc(full(e.at))+' · '+esc(ago(e.at))+' · ilk: '+esc(ago(e.first_at))+'</div>'+
      '<div class="kv">'+kv(esc(e.label||e.code||'?'),'Sorun')+kv(num(e.sources),'kaynak (bölüm)')+kv(num(e.reports),'rapor')+kv(e.http?esc(e.http):'-','HTTP')+'</div>';
    if(e.note)h+='<div class="note" style="border-color:var(--'+(e.heal_class?'bad':'muted')+')">'+esc(e.note)+'</div>';
    h+='<div class="note" style="border-color:var(--muted)">Sağlayıcı: <b>'+esc(e.provider||'-')+'</b> · akış: '+esc(e.stream_type||'-')+' · sınıf: <span class="mono">'+esc(e.code)+'</span>'+
      (e.heal_class?'':' (heal tetiklemez: kaynağın hatası değil)')+(e.triggered_at?'<br>Bu sınıf için heal başlatıldı: '+esc(ago(e.triggered_at)):'')+'</div>';
    if(e.host_rule){var hr=e.host_rule;
      h+='<div class="note" style="border-color:var(--'+(hr.active?'ok':'muted')+')">Host kuralı (<span class="mono">'+esc(hr.host)+'</span>): '+(hr.reason==='referer'?'Referer '+esc((hr.headers||{}).Referer||''):'sunucu vekili (IP\'ye bağlı)')+
        ' · '+(hr.active?'etkin':'askıda/süresi dolmuş')+' · öğrenildi: '+esc(ago(new Date(hr.learned_at*1000).toISOString()))+(hr.last_ok_at?' · son çalıştı: '+esc(ago(new Date(hr.last_ok_at*1000).toISOString())):'')+' · hata: '+num(hr.fail_count)+
        '<div class="act"><button data-hostrule="suspend" data-host="'+esc(hr.host)+'">Askıya al</button><button data-hostrule="delete" data-host="'+esc(hr.host)+'">Sil</button></div></div>';}
    h+='<h2 class="sec">Bölümler</h2>'+((e.episodes||[]).map(function(x){
      return '<div class="mono" style="margin-top:4px">'+mediaLink(x.canonical_id||x.title?{id:x.canonical_id||'',title:x.title||'',site:e.site||''}:null,esc(pbiLabel(x)))+' · '+num(x.reports)+' rapor · '+esc(x.client_code||'')+' · '+esc(ago(x.at))+(x.locator?'<br><span style="color:var(--muted);overflow-wrap:anywhere">'+esc(x.locator)+'</span>':'')+'</div>'}).join('')||'<div class="note" style="border-color:var(--muted)">Bölüm kaydı yok.</div>');
    h+='<div class="act"><button data-do="heal-playback" data-site="'+esc(e.site)+'"'+(s.healing?' disabled':'')+'>Ajan düzeltsin</button></div>'+
      (e.heal_class?'':'<div class="note" style="border-color:var(--muted)">Bu sınıf için ajan düzeltmesi anlamlı olmayabilir; yine de oynatma kanıtıyla çalıştırılabilir.</div>');
  } else if(e.kind==='finder'){
    h+=evPill(e)+' '+pill('heal','Kaynak bulucu')+'<h3>'+mediaLink(mediaRef(e),esc(finLabel(e)))+'</h3><div class="sub">'+esc(full(e.at))+' · '+esc(ago(e.at))+(e.trigger?' · '+esc(({heal_rerun:'heal sonrası yeniden deneme',playback_failed:'oynatma hatası',play:'oynatma'})[e.trigger]||e.trigger):'')+'</div>'+
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
  return h;
}
/* ---- yapım bağlantısı: olay bir dizi/film/bölümle ilişkiliyse {id, title, site} ---- */
function mediaRef(e){
  if(!e)return null;
  if(e.kind==='finder'||e.kind==='hydrate')return (e.canonical_id||e.title)?{id:e.canonical_id||'',title:e.title||'',site:e.site||''}:null;
  if(e.kind==='playback_issue'){var x=(e.episodes||[])[0];return x&&(x.canonical_id||x.title)?{id:x.canonical_id||'',title:x.title||'',site:e.site||''}:null}
  return null;
}
function mediaLink(ref,html){
  if(!ref)return html;
  return '<a href="#" class="mlink" data-media="'+esc(ref.id||'')+'" data-mtitle="'+esc(ref.title||'')+'" data-msite="'+esc(ref.site||'')+'">'+html+'</a>';
}
/* tek hedef: Siteler › Kütüphane (Adım 2'de yapım yan paneli). Uygulama sites.js'te (window.dzOpenMedia). */
function openMedia(ref){if(window.dzOpenMedia)window.dzOpenMedia(ref)}
document.addEventListener('click',function(ev){
  var a=ev.target&&ev.target.closest&&ev.target.closest('[data-media]');if(!a)return;
  ev.preventDefault();ev.stopPropagation();
  openMedia({id:a.getAttribute('data-media')||'',title:a.getAttribute('data-mtitle')||'',site:a.getAttribute('data-msite')||''});
},true);

/* ---- kısa satır metinleri ---- */
var ICON={scan:'⟳',live:'◉',heal:'✦',rollback:'↶',cooldown:'⏸',finder:'⌕',playback_issue:'▶',blocked_page:'⊘',hydrate:'!',tmdb:'T',reset:'⌫'};
function srcLabel(src){return src==='live'?'Canlı TV':src==='system'?'Sistem':src||'-'}
function plainTitle(e){
  var k=e.kind;
  if(k==='scan')return 'Tarama';
  if(k==='live')return 'Canlı TV taraması';
  if(k==='blocked_page')return 'Engelli sayfa · '+(e.source_key||e.url||'');
  if(k==='hydrate')return 'Bölümler okunamadı · '+(e.title||e.canonical_id||'?');
  if(k==='reset')return 'Kütüphane sıfırlandı';
  if(k==='playback_issue')return 'Oynatma sorunu'+(e.host?' · '+e.host:e.provider?' · '+e.provider:'');
  if(k==='finder')return 'Kaynak bulucu · '+finLabel(e);
  if(k==='tmdb')return e.scope==='seasons'?'TMDB sezon görselleri':'TMDB zenginleştirme · '+(e.media_type==='series'?'dizi':'film');
  if(k==='rollback')return 'Geri alma';
  if(k==='cooldown')return 'Heal cooldown';
  var f=(e.diff&&e.diff[0])?e.diff[0].path:'';
  return (e.playback?'Oynatma düzeltmesi':'Düzeltme')+(f?' · '+f:'');
}
/* başlık HTML'i: yapımla ilişkiliyse yapım adı bağlantı olur */
function titleHtml(e){
  var r=mediaRef(e),k=e.kind;
  if(r&&k==='finder')return 'Kaynak bulucu · '+mediaLink(r,esc(finLabel(e)));
  if(r&&k==='hydrate')return 'Bölümler okunamadı · '+mediaLink(r,esc(e.title||e.canonical_id||'?'));
  if(r&&k==='playback_issue')return esc(plainTitle(e))+' · '+mediaLink(r,esc(r.title||r.id));
  return esc(plainTitle(e));
}
function pillText(e){var d=document.createElement('div');d.innerHTML=evPill(e);return (d.textContent||'').trim()}
function errLine(e){
  var k=e.kind;
  if(k==='scan')return e.error||(e.collection_errors?e.collection_errors+' koleksiyon hatası':'')||(e.status==='partial'?'kısmi: '+num(e.scraped)+' öğe, '+num(e.ingested)+' aktarıldı':'');
  if(k==='heal'||k==='cooldown')return e.error||(e.reasons||[]).join('; ');
  if(k==='finder')return e.error||(e.state==='not_found'?'hiçbir yöntem akış bulamadı':'');
  if(k==='playback_issue')return (e.label||e.code||'')+(e.note?' · '+e.note:'')+' · '+num(e.sources)+' kaynak';
  if(k==='hydrate')return e.reason||e.status||'';
  if(k==='live'||k==='tmdb')return e.error||(e.status==='partial'?'kısmi':'');
  if(k==='blocked_page')return e.reason||'';
  return e.error||'';
}
function oneLine(s){return String(s==null?'':s).replace(/\s+/g,' ').trim()}
function lineText(e){return [full(e.at),srcLabel(e.source||e.site),plainTitle(e)+' ['+pillText(e)+']',oneLine(errLine(e))].join(' | ')}
function copyText(t){
  if(navigator.clipboard&&navigator.clipboard.writeText&&window.isSecureContext!==false)return navigator.clipboard.writeText(t).catch(function(){return fallbackCopy(t)});
  return fallbackCopy(t);
}
function fallbackCopy(t){
  return new Promise(function(res,rej){
    var ta=document.createElement('textarea');ta.value=t;ta.style.cssText='position:fixed;opacity:0;top:0';
    document.body.appendChild(ta);ta.select();
    try{document.execCommand('copy')?res():rej(new Error('kopyalanamadı'))}catch(x){rej(x)}
    document.body.removeChild(ta);
  });
}
function toast(msg,kind){
  var box=$('toasts');if(!box)return;
  var el=document.createElement('div');el.className='toast'+(kind==='bad'?' bad':'');el.textContent=msg;
  box.appendChild(el);setTimeout(function(){if(el.parentNode)el.parentNode.removeChild(el)},kind==='bad'?5000:2200);
}
function safe(fn,fb){try{return fn()}catch(x){try{console.error('admin: çizilemedi',x)}catch(_){}return fb(x)}}

/* ---- ayrıntı düğmeleri (Tara/Heal/Geri al/Engeli kaldır/host kuralı/sekme git) ---- */
function act(t,done){
  done=done||function(){};
  if(t.dataset.goto){var tb=$('tab-btn-'+t.dataset.goto);if(tb)tb.click();return true}
  if(t.dataset.rollback){
    if(!confirm(t.dataset.rollback+': önceki config sürümüne dönülsün mü?'))return true;
    t.disabled=true;
    api('/api/ops/sites/'+encodeURIComponent(t.dataset.rollback)+'/rollback',{method:'POST'}).then(function(r){
      if(r&&r.ok)toast('v'+r.version+' geri yüklendi');
      else toast((r&&r.error&&r.error.message)||(r&&r.message)||'geri alınamadı','bad');
      refresh();poll();done()}).catch(function(){t.disabled=false;toast('istek başarısız','bad')});
    return true;
  }
  if(t.dataset.unblock){
    if(!confirm('Bu sayfanın engeli kaldırılsın mı? Sonraki tarama sayfayı yeniden alır.'))return true;
    t.disabled=true;
    api('/api/ops/sites/'+encodeURIComponent(t.dataset.site)+'/blocked?url='+encodeURIComponent(t.dataset.unblock),{method:'DELETE'})
      .then(function(){refresh();done()}).catch(function(){t.disabled=false;toast('istek başarısız','bad')});
    return true;
  }
  if(t.dataset.hostrule){
    t.disabled=true;
    var hu='/api/ops/stream-host-rules/'+encodeURIComponent(t.dataset.host);
    api(t.dataset.hostrule==='delete'?hu:hu+'/suspend',{method:t.dataset.hostrule==='delete'?'DELETE':'POST'}).then(function(){refresh();done()}).catch(function(){t.disabled=false});
    return true;
  }
  if(t.dataset.do){
    t.disabled=true;
    var base='/api/ops/sites/'+encodeURIComponent(t.dataset.site)+'/'+t.dataset.do;
    api(base,{method:'POST'}).then(function(r){
      if(r&&r.reason==='no_drift'){
        if(confirm('Drift görünmüyor. Yine de heal çalıştırılsın mı (force)?'))return api(base+'?force=true',{method:'POST'});
        t.disabled=false;return}
      if(r&&r.started===false&&r.reason==='already_running')toast('Bu işlem zaten çalışıyor.','bad');
      if(r&&r.started===false&&r.reason==='no_evidence')toast('Oynatma kanıtı yok: önce bu sitenin birkaç kaynağı oynatılmalı.','bad');
      if(r&&r.started)toast('Başlatıldı');
      return r}).then(function(){poll();refresh();done()}).catch(function(){t.disabled=false});
    return true;
  }
  return false;
}

/* ---- olay listesi denetleyicisi: kısa satır, tıklayınca ayrıntı, "daha fazla", kopyala ----
   o: {fetch(before) -> Promise<{events, next_before}>, source: kaynak sütunu, err: hata sütunu, time: saat sütunu, empty: metin} */
function EvList(el,o){
  var me=this;me.el=el;me.o=o||{};me.events=[];me.next=null;me.sel=null;me.loaded=false;me.fail='';me.seq=0;
  el.addEventListener('click',function(ev){
    if(ev.target.closest('[data-media]'))return;
    var b=ev.target.closest('button');
    if(b&&el.contains(b)){
      if(b.getAttribute('data-evmore')!=null){me.load(true);return}
      if(b.getAttribute('data-evcopy')!=null){me.copyOne(b.getAttribute('data-evcopy'));return}
      if(b.closest('.aevd')){act(b,function(){me.load(false)});return}
    }
    var r=ev.target.closest('.aevr');if(r&&el.contains(r))me.toggle(r.getAttribute('data-k'));
  });
  el.addEventListener('keydown',function(ev){
    var r=ev.target.closest&&ev.target.closest('.aevr');
    if(r&&(ev.key==='Enter'||ev.key===' ')){ev.preventDefault();me.toggle(r.getAttribute('data-k'))}
  });
}
EvList.prototype.load=function(more){
  var me=this,my=++me.seq;
  return me.o.fetch(more?me.next:null).then(function(d){
    if(my!==me.seq)return;
    var list=(d&&d.events)||[];
    if(more){var seen={};me.events.forEach(function(e){seen[key(e)]=1});me.events=me.events.concat(list.filter(function(e){return !seen[key(e)]}));me.next=d.next_before||null}
    else{ // yenilemede daha önce yüklenen eski olaylar korunur
      var oldest=list.length?list[list.length-1].at:null;
      var kept=oldest&&d.next_before?me.events.filter(function(e){return e.at<oldest}):[];
      me.events=list.concat(kept);
      if(!kept.length)me.next=d.next_before||null;
    }
    me.loaded=true;me.fail='';me.render();
  }).catch(function(x){if(my!==me.seq)return;me.loaded=true;me.fail=String(x&&x.message||x);me.render()});
};
EvList.prototype.reset=function(){this.events=[];this.next=null;this.sel=null;this.loaded=false;this.fail='';this.render();return this.load()};
EvList.prototype.toggle=function(k){this.sel=this.sel===k?null:k;this.render()};
EvList.prototype.find=function(k){return this.events.filter(function(e){return key(e)===k})[0]};
EvList.prototype.row=function(e){
  var o=this.o,k=key(e),open=this.sel===k;
  var tone=/\bbad\b/.test(evPill(e))?'bad':/\bwarn\b/.test(evPill(e))?'warn':/\bok\b/.test(evPill(e))?'ok':'';
  var h='<li class="aev '+tone+'"><div class="aevr" role="button" tabindex="0" data-k="'+esc(k)+'" aria-expanded="'+open+'">'+
    '<span class="aevi" aria-hidden="true">'+esc(ICON[e.kind]||'•')+'</span>'+
    (o.time?'<span class="aevw num" title="'+esc(full(e.at))+'">'+esc(when(e.at))+'</span>':'')+
    (o.source?'<span class="aevs">'+esc(srcLabel(e.source||e.site))+'</span>':'')+
    '<span class="aevt">'+titleHtml(e)+(o.err&&errLine(e)?'<span class="aeve">'+esc(oneLine(errLine(e)))+'</span>':'')+'</span>'+
    evPill(e)+(o.time?'':'<span class="aevw" title="'+esc(full(e.at))+'">'+esc(ago(e.at))+'</span>')+'</div>';
  if(open)h+='<div class="aevd">'+safe(function(){return detailHtml(e)},function(x){return '<div class="note bad">Ayrıntı çizilemedi: '+esc(x&&x.message||x)+'</div>'})+
    '<div class="act"><button class="btn" data-evcopy="'+esc(k)+'">Kopyala</button></div></div>';
  return h+'</li>';
};
EvList.prototype.render=function(){
  var me=this,el=me.el;
  if(!me.loaded){el.innerHTML='<div class="empty sm">Yükleniyor…</div>';return}
  if(me.fail&&!me.events.length){el.innerHTML='<div class="note bad">Olaylar alınamadı: '+esc(me.fail)+'</div>';return}
  var v=me.o.filter?me.events.filter(me.o.filter):me.events;
  if(!v.length){el.innerHTML='<div class="empty sm">'+esc(me.o.empty||'Olay yok.')+'</div>';return}
  el.innerHTML='<ul class="aevl">'+v.map(function(e){
    return safe(function(){return me.row(e)},function(x){return '<li class="aev bad"><div class="note bad">Satır çizilemedi: '+esc(x&&x.message||x)+'</div></li>'})}).join('')+'</ul>'+
    (me.next?'<div class="lmore" style="display:block"><button class="btn" data-evmore>Daha fazla</button></div>':'');
};
EvList.prototype.visible=function(){return this.o.filter?this.events.filter(this.o.filter):this.events};
EvList.prototype.copyAll=function(){
  var t=this.visible().map(lineText).join('\n');
  return copyText(t||'(boş)').then(function(){toast('Kopyalandı')}).catch(function(){toast('Kopyalanamadı','bad')});
};
EvList.prototype.copyOne=function(k){
  var e=this.find(k);if(!e)return;
  var d=this.el.querySelector('.aevd'),txt=lineText(e)+(d?'\n\n'+oneLines(d.innerText||d.textContent||''):'');
  copyText(txt).then(function(){toast('Kopyalandı')}).catch(function(){toast('Kopyalanamadı','bad')});
};
function oneLines(s){return String(s).split('\n').map(function(x){return x.trim()}).filter(function(x){return x&&x!=='Kopyala'}).join('\n')}
function when(t){return T(t)?new Date(t).toLocaleString('tr-TR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}):'-'}

/* ---- veri ---- */
function refresh(){
  return api('/api/ops/overview').then(function(d){sites=d.sites||[];liveSrc=d.live_sources||[];renderSys();
    try{document.dispatchEvent(new CustomEvent('dz:overview'))}catch(x){}});
}
function poll(){
  api('/api/ops/active').then(function(a){
    activeNow=a.active||[];lastHeal=a.last_heal;
    renderBanner();
    var k=activeNow.map(function(j){return j.site+j.kind}).join(',')+'|'+(a.last_run&&a.last_run.id)+'|'+(a.last_heal&&a.last_heal.id)+'|'+(a.last_tmdb&&a.last_tmdb.id)+'|'+(a.last_finder&&a.last_finder.id);
    if(k!==lastKey){var first=lastKey===null;lastKey=k;
      if(!first){refresh();try{document.dispatchEvent(new CustomEvent('dz:ops-changed'))}catch(x){}}}
    $('upd').textContent='güncellendi '+new Date().toLocaleTimeString('tr-TR');
  }).catch(function(){$('upd').textContent='bağlantı yok'});
}
document.addEventListener('dz:settings-changed',function(){refresh()});

window.dzEv={
  list:function(el,o){return new EvList(el,o)},
  detailHtml:detailHtml,pill:evPill,act:act,plainTitle:plainTitle,errLine:errLine,lineText:lineText,srcLabel:srcLabel,
  resolverHtml:resolverHtml,playbackHtml:playbackHtml,siteOf:siteOf,sites:function(){return sites},
  copyText:copyText,toast:toast,ago:ago,when:when,full:full,esc:esc,mediaRef:mediaRef,openMedia:openMedia,refresh:refresh,poll:poll
};

renderSys();renderBanner();refresh();poll();
setInterval(poll,2500);
setInterval(function(){refresh()},30000);
})();
