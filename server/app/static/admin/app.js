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
  var p=[cnt(ev.sources,'kaynak')];
  if(ev.window_n!=null)p.push('pencere '+B(num(ev.window_failed)+'/'+num(ev.window_n),+ev.window_failed>0));
  if(ev.stages&&ev.stages.length)p.push('aşama '+esc(namedList(ev.stages)));
  if(ev.hosts&&ev.hosts.length)p.push('host '+esc(namedList(ev.hosts)));
  p.push(cnt(ev.ok_examples,'çalışan örnek'));
  return line('Kanıt',p)+(ev.errors&&ev.errors.length?line('Hatalar',ev.errors.map(function(x){return '<span class="mono">'+esc(x.name)+' ×'+esc(x.count)+'</span>'})):'');
}
function agentHtml(a){
  if(!a)return '';
  var tools=Object.keys(a.tools||{}).map(function(k){return k+' ×'+a.tools[k]}).join(' · ');
  return line('Ajan',[cnt(a.turns,'tur'),cnt(a.events,'olay'),cnt(a.tests,'test_config'),cnt(a.errors,'hata',1)])+
    (tools?line('Araçlar',['<span class="mono">'+esc(tools)+'</span>']):'')+
    (a.last_say?'<div class="evq">'+esc(a.last_say)+'</div>':'');
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
/* ---- kompakt olay ayrıntısı: meta satırı + "Bölüm  değer · değer" satırları; sıfır/boş değer gösterilmez ---- */
function B(v,bad){return '<b'+(bad?' class="bad"':'')+'>'+v+'</b>'}
function cnt(v,l,bad){return v==null||v===''||isNaN(+v)||+v===0?'':B(num(v),bad)+(l?' '+esc(l):'')}
function tv(l,v){return v==null||v===''||v==='-'?'':(l?esc(l)+' ':'')+B(v)}
function dv(s){return s==null||isNaN(+s)||+s===0?'':dur(s)}
function line(label,parts){parts=(parts||[]).filter(Boolean);return parts.length?'<div class="evl"><span class="evk">'+esc(label)+'</span><span class="evv">'+parts.join(' · ')+'</span></div>':''}
function nt(tone,html,title){var ic={bad:'✕',warn:'⚠',ok:'✓'}[tone]||'ℹ';return '<div class="evn '+(tone||'')+'"'+(title?' title="'+esc(title)+'"':'')+'><span class="evni" aria-hidden="true">'+ic+'</span><span>'+html+'</span></div>'}
function tech(s){return ' <span class="evt">('+esc(s)+')</span>'}
function btns(site,extra,pb){
  var s=siteOf(site)||{},ph=s.playback_health;
  return (extra||'')+
    '<button data-do="scan" data-site="'+esc(site)+'"'+(s.scanning?' disabled':'')+'>⟳ Tara</button>'+
    '<button data-do="heal" data-site="'+esc(site)+'"'+(s.healing?' disabled':'')+'>✦ Heal</button>'+
    (pb||(ph&&ph.failed>0)?'<button data-do="heal-playback" data-site="'+esc(site)+'"'+(s.healing?' disabled':'')+'>▶ Oynatma heal</button>':'');
}
function actions(site,extra,pb){return '<div class="act">'+btns(site,extra,pb)+'</div>'}
function enrichHtml(t){
  if(!t||typeof t!=='object')return '';
  var d=t.seconds!=null?t.seconds:t.duration!=null?t.duration:t.duration_seconds;
  return line('TMDB',[cnt(t.matched,'eşleşti'),cnt(t.unmatched,'eşleşmedi'),cnt(t.review,'inceleme'),cnt(t.skipped,'atlandı'),dv(d)]);
}
function crawlOn(t){return !!(t&&typeof t==='object'&&(t.due||t.series||t.errors))}
function crawlHtml(t){
  if(!crawlOn(t))return '';
  var h=line('Bölüm envanteri',[cnt(t.series,'dizi'),cnt(t.seasons,'sezon'),
    t.episodes?B(num(t.episodes))+' bölüm'+(t.new_episodes?' ('+num(t.new_episodes)+' yeni)':''):cnt(t.new_episodes,'yeni bölüm'),
    cnt(t.errors,'hata',1),cnt(t.deferred,'ertelendi'),dv(t.seconds)]);
  if(t.stopped)h+=nt('warn','Tarama '+(t.stopped==='budget'?'dizi bütçesi'+tech('SERIES_CRAWL_BUDGET'):t.stopped==='time'?'süre bütçesi'+tech('SERIES_CRAWL_SECONDS'):'art arda hatalar')+' nedeniyle durdu; kalan '+num(t.deferred)+' dizi sonraki taramada.');
  else if(t.incomplete||t.fallback)h+=nt('',[t.incomplete?num(t.incomplete)+' dizi envanteri eksik (sonra yeniden denenir)':'',t.fallback?num(t.fallback)+' dizi seçici yerine URL taramasıyla okundu':''].filter(Boolean).join(' · '));
  return h;
}
function sec(t){return '<div class="evs">'+esc(t)+'</div>'}
function rows(a){return a.map(function(x){return '<div class="evr mono">'+x+'</div>'}).join('')}
/* detailHtml(e, extraButtons): üstte meta satırı + sağda eylem düğmeleri; altta kompakt satırlar */
function detailHtml(e,extra){
  var h='',m=[when(e.at)],a='',s=siteOf(e.site)||{};
  if(e.kind==='live'){
    var TG={auto:'otomatik',manual:'elle',cli:'komut satırı'};
    m.push(TG[e.trigger]||e.trigger,dv(e.duration),(e.countries||[]).join(', ').toUpperCase());
    if(e.status==='error')h+=nt('bad',esc(e.error||'hata'));
    else{
      if(e.status==='no_change')h+=nt('','Kaynak listesi değişmedi (ETag 304); kanal kaydı yazılmadı'+(e.probed!=null?', yalnız sağlık yoklaması yapıldı':'')+'.');
      else h+=line('Kanallar',[cnt(e.new,'yeni'),cnt(e.updated,'güncellendi'),cnt(e.deactivated,'pasifleşti'),cnt(e.unchanged,'aynı')]);
      h+=line('Sağlık',[cnt(e.ok,'sağlıklı (m3u8)'),cnt(e.youtube_only,'yalnız YouTube'),cnt(e.hidden,'gizli'),cnt(e.visible,'görünür'),cnt(e.active,'aktif'),cnt(e.inactive,'pasif')]);
      if(e.probed!=null)h+=line('Yoklama',[B(num(e.probe_ok))+' / '+num(e.probed)+' akış yanıt verdi',cnt(e.no_cors,'CORS yok (vekil)')]);
    }
    h+=line('Kaynak',[cnt(e.total,'kanal'),cnt(e.kept,'dil süzgecinden geçen')]);
    a='<button data-goto="onboard">Siteler</button><button data-goto="settings">⚙ Canlı TV</button>';
  } else if(e.kind==='blocked_page'){
    h+=line('Engel',[tv('',esc(e.site||'')),tv('neden',esc(BV[e.via]||e.via||'')),tv('sayfa',esc(e.page_kind||'')),e.until?'yeniden denenir '+B(esc(when(e.until))):''])+
      (e.reason?nt('warn',esc(e.reason)):'')+
      line('URL',['<span class="mono evu">'+esc(e.url)+'</span>'])+
      nt('','Engel yalnız bu sitedeki bu sayfa içindir; aynı yapım başka siteden gelebilir.');
    a='<button data-unblock="'+esc(e.url)+'" data-site="'+esc(e.site)+'">⊘ Engeli kaldır</button>';
  } else if(e.kind==='hydrate'){
    h+=line('Kaynak',[tv('',esc(e.site||'')),tv('durum',esc(e.status||'')),e.source_key?'<span class="mono">'+esc(e.source_key)+'</span>':''])+
      (e.reason?nt(e.status==='error'?'bad':'warn',esc(e.reason)):'')+
      (e.url?line('URL',['<span class="mono evu">'+esc(e.url)+'</span>']):'');
  } else if(e.kind==='reset'){
    m.push(dv(e.seconds));
    h+=line('Sıfırlama',[cnt((e.cleared||[]).length,'tablo temizlendi'),Object.keys(e.kept||{}).length?'korunan '+B(Object.keys(e.kept||{}).map(esc).join(', ')):'',e.cache?cnt(e.cache_files,'önbellek dosyası'):''])+
      line('Yedek',['<span class="mono evu">'+esc(e.backup||'-')+'</span>']);
  } else if(e.kind==='playback_issue'){
    m.push('ilk '+ago(e.first_at));
    h+=line('Sorun',[B(esc(e.label||e.code||'?'),!!e.heal_class),cnt(e.sources,'kaynak'),cnt(e.reports,'rapor'),e.http?'HTTP '+B(esc(e.http)):''])+
      line('Sağlayıcı',[tv('',esc(e.provider||'')),tv('akış',esc(e.stream_type||'')),e.code?'sınıf <span class="mono">'+esc(e.code)+'</span>':'',e.triggered_at?'heal başlatıldı '+esc(ago(e.triggered_at)):''])+
      (e.note?nt(e.heal_class?'bad':'',esc(e.note)):'')+
      (e.heal_class?'':nt('','Heal tetiklemez: kaynağın hatası değil; ajan düzeltmesi anlamlı olmayabilir.'));
    if(e.host_rule){var hr=e.host_rule;
      h+=line('Host kuralı',['<span class="mono">'+esc(hr.host)+'</span>',hr.reason==='referer'?'Referer '+esc((hr.headers||{}).Referer||''):'sunucu vekili (IP\'ye bağlı)',
        hr.active?'<span class="ok">etkin</span>':'askıda/süresi dolmuş','öğrenildi '+esc(ago(new Date(hr.learned_at*1000).toISOString())),
        hr.last_ok_at?'son çalıştı '+esc(ago(new Date(hr.last_ok_at*1000).toISOString())):'',cnt(hr.fail_count,'hata',1),
        '<span class="act"><button data-hostrule="suspend" data-host="'+esc(hr.host)+'">Askıya al</button><button data-hostrule="delete" data-host="'+esc(hr.host)+'">Sil</button></span>']);}
    var eps=(e.episodes||[]).map(function(x){
      return mediaLink(x.canonical_id||x.title?{id:x.canonical_id||'',title:x.title||'',site:e.site||''}:null,esc(pbiLabel(x)))+' · '+num(x.reports)+' rapor'+(x.client_code?' · '+esc(x.client_code):'')+' · '+esc(ago(x.at))+(x.locator?' <span class="evt evu">'+esc(x.locator)+'</span>':'')});
    h+=eps.length?sec('Bölümler')+rows(eps):'';
    a='<button data-do="heal-playback" data-site="'+esc(e.site)+'"'+(s.healing?' disabled':'')+'>✦ Ajan düzeltsin</button>';
  } else if(e.kind==='finder'){
    m.push(e.trigger?({heal_rerun:'heal sonrası yeniden deneme',playback_failed:'oynatma hatası',play:'oynatma'})[e.trigger]||e.trigger:'',dv(e.seconds));
    h+=line('Sonuç',[B(esc(FL[e.state]||e.state||'?'),e.state!=='found'),e.method?esc(FM[e.method]||e.method):'',tv('',esc(e.site||''))]);
    if(e.error)h+=nt('bad',esc(e.error));
    var st=(e.steps||[]).map(function(x){return '<span class="'+(x.ok?'ok':'evt')+'">'+(x.ok?'bulundu':'yok')+'</span> · '+esc(x.name)+' · '+dur((+x.ms||0)/1000)+(x.note?' · '+esc(x.note):'')});
    h+=st.length?sec('Adımlar')+rows(st):'';
    if(e.source_id)h+=line('Kaynak',['<span class="mono evu">'+esc(e.source_id)+'</span>']);
  } else if(e.kind==='tmdb'){
    var seas=e.scope==='seasons';
    m.push(e.trigger,e.dry_run?'yazmadan önizleme':e.from_preview?'önizlemeden uygulandı':'canlı uygulama',dv(e.duration));
    h+=line('TMDB',(seas?[cnt(e.series,'dizi'),cnt(e.seasons,'sezon'),cnt(e.episodes,'bölüm'),cnt(e.posters,'sezon posteri'),cnt(e.stills,'bölüm görseli'),cnt(e.empty,'TMDB’de yok')]
        :[cnt(e.matched,'otomatik eşleşme'),cnt(e.review,'inceleme'),cnt(e.unmatched,'eşleşmedi')]).concat([cnt(e.skipped,'atlandı'),
        e.dry_run?'':cnt(e.written,seas?'sezon yazıldı':'yazıldı'),cnt(e.errors,'hata',1),cnt(e.lookup,seas?'dizi sorgulandı':'sorgulandı')]));
    if(e.aborted)h+=nt('warn','TMDB ulaşılamadı; iş erken durduruldu.');
    if(e.error)h+=nt('bad',esc(e.error));
    a='<button data-goto="settings">⚙ TMDB</button>';
  } else if(e.kind==='scan'){
    var rec=(s.recent||[]);
    m.push(e.trigger,dv(e.duration));
    h+=line('Tarama',[e.scraped!=null?B(num(e.scraped))+' öğe'+(e.ingested!=null?' ('+num(e.ingested)+' aktarıldı)':''):'',cnt(e.pages,'sayfa'),e.rate?B(esc(e.rate))+' öğe/sn':'',cnt(e.collection_errors,'koleksiyon hatası',1)]);
    if(e.error)h+=nt('bad',esc(e.error));
    if(s.drift&&s.drift_reasons&&s.drift_reasons.length)h+=nt('warn','<b>Drift:</b> '+s.drift_reasons.map(esc).join(' · '));
    h+=crawlHtml(e.series_crawl)+enrichHtml(e.tmdb_enrich);
    if(rec.length>1)h+=line('Trend',['süre '+spark(rec.map(function(r){return r.duration}),css('--accent'),90,18),'öğe '+spark(rec.map(function(r){return r.scraped}),css('--ok'),90,18)]);
    a=btns(e.site);
  } else if(e.kind==='rollback'){
    h+=nt('','Config v'+esc(e.new_version)+' sürümüne geri dönüldü.');
    a=btns(e.site);
  } else if(e.kind==='cooldown'){
    h+=nt('warn','Otomatik heal cooldown nedeniyle atlandı; bitiş <b>'+esc(when(e.cooldown_until))+'</b> ('+esc(until(e.cooldown_until))+'). Elle Heal cooldown\'u yok sayar.')+
      ((e.reasons||[]).length?line('Drift',e.reasons.map(esc)):'');
    a=btns(e.site);
  } else {
    m.push((e.provider||'-')+(e.model?' / '+e.model:''),dv(e.duration),trigLabel(e));
    h+=line('Sonuç',[B(esc(HL[e.outcome]||e.outcome||'?'),HC[e.outcome]==='bad'),e.new_version?'config '+B('v'+esc(e.new_version)):'',e.playback?'oynatma tetikli':'']);
    if((e.reasons||[]).length)h+=line(e.playback?'Nedenler':'Drift',e.reasons.map(esc));
    h+=evidenceHtml(e.evidence_summary);
    if(e.error)h+=nt('bad',esc(e.error));
    if(e.diff&&e.diff.length){
      h+=sec('Önce / sonra')+'<div class="diff">'+e.diff.map(function(d){
        return '<div class="mono evt">'+esc(d.path)+'</div><code class="b">− '+esc(d.before==null?'(yok)':d.before)+'</code><code class="a">+ '+esc(d.after==null?'(yok)':d.after)+'</code>'}).join('')+'</div>';
    } else h+=nt('','Selector değişikliği yok.');
    h+=agentHtml(e.agent_summary);
    a=btns(e.site,e.can_rollback?'<button data-rollback="'+esc(e.site)+'">↶ Geri al</button>':'',!!e.playback);
  }
  m=m.filter(function(x){return x&&x!=='-'}).map(esc).join(' · ');
  return '<div class="evh"><div class="evm" title="'+esc(full(e.at))+'">'+m+'</div><div class="act sm">'+a+(extra||'')+'</div></div>'+h;
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
  var cp='<button data-evcopy="'+esc(k)+'" title="Olayı panoya kopyala">⧉ Kopyala</button>';
  if(open)h+='<div class="aevd">'+safe(function(){return detailHtml(e,cp)},function(x){return '<div class="evh"><div class="evm"></div><div class="act sm">'+cp+'</div></div><div class="note bad">Ayrıntı çizilemedi: '+esc(x&&x.message||x)+'</div>'})+'</div>';
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
  var d=this.el.querySelector('.aevd');if(d){d=d.cloneNode(true);[].forEach.call(d.querySelectorAll('.act'),function(x){x.parentNode.removeChild(x)})}var txt=lineText(e)+(d?'\n\n'+oneLines(d.innerText||d.textContent||''):'');
  copyText(txt).then(function(){toast('Kopyalandı')}).catch(function(){toast('Kopyalanamadı','bad')});
};
function oneLines(s){return String(s).split('\n').map(function(x){return x.trim()}).filter(function(x){return x&&x!=='Kopyala'&&x!=='⧉ Kopyala'}).join('\n')}
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
