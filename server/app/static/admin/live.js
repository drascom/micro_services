/* "Canlı TV" sekmesi (#live): famelack durum satırı (GET /api/ops/live, otomatik tarama = PUT /api/ops/settings live_tv.enabled,
   Tara = POST /api/ops/live/scan) + alt sekmeler Kanallar (liste/arama/sağlık) · Kaynaklar (M3U: /api/ops/live/sources) ·
   Silinenler (/api/ops/live/removed + restore) · Olaylar (GET /api/ops/events?site=famelack, dzEv). Gizliyken ağ isteği yok. */
(function(){
'use strict';
var $=function(i){return document.getElementById(i)};
var E=window.dzEv;
var built=false,sub='channels',LV=null,lvFail='',LR=null,lrFail='',MS=null,msFail='',msBusy=false,msLast=null;
var MF={kind:'link',name:'',url:'',llm:true,file:null},F={health:'',q:''},evl=null,timer=null,tickN=0,qTimer=null;
var SUBS=[['channels','Kanallar'],['sources','Kaynaklar'],['removed','Silinenler'],['events','Olaylar']];
var LVH={ok:['ok','Çalışıyor'],youtube:['warn','Yalnız YouTube'],dead:['bad','Gizli'],inactive:['','Kaynakta yok']};
var MLLM={not_needed:'gerekmedi',ok:'çalıştı',disabled:'kapalı'};

function esc(s){return E.esc(s)}
function enc(v){return encodeURIComponent(v)}
function num(n){return n==null?'-':(+n).toLocaleString('tr-TR')}
function pill(cls,txt,tip){return '<span class="pill '+cls+'"'+(tip?' title="'+esc(tip)+'"':'')+'>'+esc(txt)+'</span>'}
function ts(sec){return sec?new Date(sec*1000).toISOString():null}
function msgOf(e){return String(e&&e.message||e)}
function call(path,opt){
  return fetch(path,opt).then(function(r){
    return r.json().catch(function(){return null}).then(function(j){
      if(!r.ok){var m=(j&&j.error&&j.error.message)||(j&&j.detail&&(j.detail.message||j.detail))||('HTTP '+r.status);throw new Error(typeof m==='string'?m:JSON.stringify(m))}
      return j;
    });
  });
}
function sw(checked,label,attrs){
  return '<label class="sw" title="'+esc(label)+'"><input type="checkbox" role="switch" '+(checked?'checked ':'')+(attrs||'')+' aria-label="'+esc(label)+'"><span class="knob"></span></label>';
}

/* ---- iskelet ---- */
function build(){
  if(built)return;built=true;
  $('live-root').innerHTML='<div id="lv-head" class="lvhead"></div>'+
    '<div class="accsub" role="tablist" id="lv-subs">'+SUBS.map(function(s){
      return '<button role="tab" data-lvsub="'+s[0]+'" aria-selected="'+(s[0]===sub)+'">'+s[1]+'</button>'}).join('')+'</div>'+
    '<div id="lv-panel"></div>';
  bind();
}

/* ---- durum satırı ---- */
function headHtml(){
  if(!LV)return lvFail?'<div class="note bad">Durum alınamadı: '+esc(lvFail)+' <button class="btn" data-lv="retry">Yeniden dene</button></div>':'<div class="empty sm">Yükleniyor…</div>';
  var c=LV.counts||{},last=LV.last||{},err=last&&last.status==='error';
  var d=LV.running?['run','Taranıyor']:!LV.ran_at?['','Hiç taranmadı']:err?['bad','Hata']:['ok','Başarılı'];
  return '<div class="acch lvrow"><span class="sdot '+d[0]+'" title="'+esc(d[1])+'" aria-label="'+esc(d[1])+'"></span><b>famelack</b>'+
    '<span class="accm"><span title="'+esc(LV.ran_at?'Son tarama '+E.full(ts(LV.ran_at)):'')+'"'+(err?' class="bad"':'')+'>'+esc(LV.ran_at?E.ago(ts(LV.ran_at)):'hiç taranmadı')+'</span>'+
      '<span title="'+esc(num(c.ok)+' çalışan · '+num(c.youtube_only)+' yalnız YouTube · '+num(c.dead)+' gizli · '+num((c.total||0)-(c.active||0))+' kaynakta yok')+'">'+esc(num(c.visible)+' görünür / '+num(c.total)+' kanal')+'</span>'+
      '<span class="dim" title="Ülke / dil">'+esc(((LV.countries||[]).join(', ')||'-').toUpperCase()+((LV.languages||[]).length?' · '+LV.languages.join(', '):''))+'</span></span>'+
    '<span class="accc">'+sw(!!LV.enabled,'Otomatik tarama','data-lvauto="1"')+'<span class="dim">her '+esc(LV.interval_minutes||60)+' dk</span></span>'+
    '<button class="btn" data-lv="scan"'+(LV.running?' disabled':'')+'>'+(LV.running?'Taranıyor…':'Tara')+'</button></div>';
}
function drawHead(){var el=$('lv-head');if(el)el.innerHTML=safe(headHtml)}
function safe(fn){try{return fn()}catch(e){try{console.error('live: çizilemedi',e)}catch(_){}return '<div class="note bad">Çizilemedi: '+esc(msgOf(e))+'</div>'}}

/* ---- Kanallar ---- */
var LVRANK={ok:0,youtube:1,dead:2,inactive:3};
function rank(h){return LVRANK[h]==null?9:LVRANK[h]}
/* aynı kanalın satırları (famelack + M3U, ad varyantları) group_id altında tek kanal; sağlık = grubun en iyisi */
function groupsOf(all){
  var by={},out=[];
  all.forEach(function(x){
    var gid=x.group_id||x.id,g=by[gid];
    if(!g){g=by[gid]={id:gid,m:[]};out.push(g)}
    g.m.push(x);
  });
  out.forEach(function(g){
    g.m.sort(function(a,b){return (a.id===g.id?0:1)-(b.id===g.id?0:1)});
    var p=g.m[0];g.title=p.title;g.channel_no=p.channel_no;g.removed=p.removed;g.health=p.health;
    g.m.forEach(function(x){if(rank(x.health)<rank(g.health))g.health=x.health;if(x.channel_no&&!g.channel_no)g.channel_no=x.channel_no});
  });
  return out;
}
function channelsHtml(){
  if(!LV)return '<div class="empty sm">Yükleniyor…</div>';
  var all=groupsOf(LV.channels||[]),cnt={};all.forEach(function(x){cnt[x.health]=(cnt[x.health]||0)+1});
  var FL=[['','Hepsi',all.length],['ok','Çalışan',cnt.ok],['youtube','Yalnız YouTube',cnt.youtube],['dead','Gizli',cnt.dead],['inactive','Kaynakta yok',cnt.inactive]];
  var q=F.q.toLocaleLowerCase('tr-TR');
  function hit(x){return String(x.title||'').toLocaleLowerCase('tr-TR').indexOf(q)>=0||String(x.source||'').toLocaleLowerCase('tr-TR').indexOf(q)>=0}
  var rows=all.filter(function(g){return (!F.health||g.health===F.health)&&(!q||g.m.some(hit))});
  var h='<div class="lvbar"><input type="search" id="lv-q" placeholder="Kanal ara…" aria-label="Kanal ara" value="'+esc(F.q)+'" autocomplete="off">'+
    '<div class="fg">'+FL.map(function(f){return '<button class="ch'+(f[2]?'':' zero')+'" data-lvf="'+f[0]+'" aria-pressed="'+(F.health===f[0])+'">'+f[1]+' <b>'+num(f[2]||0)+'</b></button>'}).join('')+'</div></div>';
  if(!rows.length)return h+'<div class="empty sm">Bu süzgeçle kanal yok.</div>';
  var shown=rows.slice(0,300);
  h+='<ul class="lvlist">'+shown.map(function(g){
    var m=LVH[g.health]||['',g.health||'?'],multi=g.m.length>1;
    var body=g.m.map(function(x){
      var st=(x.streams||[]).map(function(s){
        var bad=s.status==='broken'||s.probe==='fail',ok=s.status==='healthy'||s.probe==='ok';
        return '<div class="lvst mono"><span class="'+(bad?'bad':ok?'ok':'dim')+'">'+esc((s.probe||'?')+' · '+(s.status||'?'))+'</span>'+(s.cors===false?' <span class="dim">vekil</span>':'')+' <span class="evu">'+esc(s.url)+'</span>'+(s.error?' <span class="dim">('+esc(s.error)+')</span>':'')+'</div>'}).join('');
      var meta=esc([x.source,x.country,(x.categories||[]).join(', '),x.youtube?'YouTube '+x.youtube:''].filter(Boolean).join(' · '));
      var geo=x.geo_blocked?' '+pill('','geo'):'';
      return (multi?'<div class="lvst"><span class="dim">'+(x.title!==g.title?esc(x.title)+' · ':'')+meta+'</span>'+geo+'</div>':' <span class="dim">'+meta+'</span>'+geo)+st}).join('');
    return '<li class="lvch"><span class="num dim">'+esc(g.channel_no||'-')+'</span><div class="grow"><b>'+esc(g.title)+'</b>'+(g.removed?' '+pill('bad','silindi'):'')+
      (multi?' '+pill('',g.m.length+' kaynak','Aynı kanalın kaynakları tek kanalda birleşti'):'')+body+'</div>'+pill(m[0],m[1])+'</li>'}).join('')+'</ul>'+
    (rows.length>shown.length?'<div class="dim errmeta">'+num(rows.length-shown.length)+' kanal daha; aramayla daraltın.</div>':'');
  return h;
}

/* ---- Kaynaklar (M3U) ---- */
function llmText(s){if(!s)return '-';if(MLLM[s])return MLLM[s];if(s.indexOf('off:')===0)return 'atlandı ('+s.slice(4).trim()+')';return s}
function sumText(x){
  if(!x)return '';if(x.error)return 'Hata: '+x.error;
  return num(x.added)+' eklendi · '+num(x.updated)+' güncellendi · '+num(x.unchanged)+' aynı · '+num(x.skipped)+' atlandı · '+
    num(x.llm_fixed)+' LLM ile düzeltildi'+(x.deactivated?' · '+num(x.deactivated)+' kaldırıldı':'')+
    (x.problems?' · LLM: '+llmText(x.llm)+(x.unresolved?' ('+num(x.unresolved)+' satır okunamadı)':''):'');
}
function sourcesHtml(){
  var mb=Math.round(((MS&&MS.max_bytes)||5242880)/1048576);
  var h='<div class="lvform" title="M3U/M3U8 listesi; bağlantılar saatlik taramayla yenilenir, dosya sabittir (en fazla '+mb+' MB)">'+
      '<div class="seg" role="group" aria-label="Kaynak türü">'+[['link','Bağlantı'],['file','Dosya']].map(function(k){
        return '<button data-m3u-kind="'+k[0]+'" aria-pressed="'+(MF.kind===k[0])+'">'+k[1]+'</button>'}).join('')+'</div>'+
      '<input type="text" id="m3u-name" data-m3u-field="name" maxlength="80" placeholder="Ad" value="'+esc(MF.name)+'" aria-label="Kaynak adı">'+
      (MF.kind==='link'?'<input type="url" id="m3u-url" class="grow" data-m3u-field="url" maxlength="2048" placeholder="https://…/liste.m3u" value="'+esc(MF.url)+'" aria-label="M3U bağlantısı">':
        '<input type="file" id="m3u-file" accept=".m3u,.m3u8,audio/x-mpegurl,application/vnd.apple.mpegurl,text/plain" aria-label="M3U dosyası">'+
        (MF.file?'<span class="dim">'+esc(MF.file.name)+' · '+num(Math.round(MF.file.size/1024))+' KB</span>':''))+
      '<label class="chk" title="Okunamayan satırları heal LLM’i ile düzelt"><input type="checkbox" id="m3u-llm" data-m3u-llm="1"'+(MF.llm?' checked':'')+'> LLM</label>'+
      '<button class="btn" id="m3u-add"'+(msBusy?' disabled':'')+'>'+(msBusy?'İçe aktarılıyor…':'Ekle')+'</button></div>';
  if(msLast)h+='<div class="dim errmeta"><b>'+esc(msLast.name||'')+'</b>: '+esc(sumText(msLast.summary))+'</div>';
  if(msFail&&!MS)return h+'<div class="note bad">Liste alınamadı: '+esc(msFail)+'</div>';
  if(!MS)return h+'<div class="empty sm">Yükleniyor…</div>';
  var rows=MS.sources||[];
  if(!rows.length)return h+'<div class="empty sm">Henüz M3U kaynağı yok.</div>';
  return h+'<ul class="lvlist">'+rows.map(function(x){
    return '<li class="lvch"><div class="grow"><b>'+esc(x.name)+'</b> <span class="dim">'+(x.kind==='link'?'bağlantı':'dosya')+' · '+num(x.channels)+' kanal · '+num(x.healthy)+' sağlıklı · '+
      esc(x.ran_at?E.ago(ts(x.ran_at)):'hiç taranmadı')+'</span>'+
      (x.url?'<div class="lvst mono evu dim">'+esc(x.url)+'</div>':'')+(x.last?'<div class="lvst dim">'+esc(sumText(x.last))+'</div>':'')+'</div>'+
      '<span class="act sm"><button data-m3u-refresh="'+esc(x.id)+'"'+(msBusy?' disabled':'')+'>Yenile</button>'+
      '<button class="obdanger" data-m3u-del="'+esc(x.id)+'" data-m3u-dname="'+esc(x.name)+'"'+(msBusy?' disabled':'')+'>Sil</button></span></li>'}).join('')+'</ul>';
}

/* ---- Silinenler ---- */
function removedHtml(){
  if(lrFail&&!LR)return '<div class="note bad">Liste alınamadı: '+esc(lrFail)+'</div>';
  if(!LR)return '<div class="empty sm">Yükleniyor…</div>';
  if(!LR.length)return '<div class="empty sm">Silinen kanal yok.</div>';
  return '<div class="dim errmeta" title="İstemciden herkes için silinen kanallar; taramalar geri getirmez">'+num(LR.length)+' silinen kanal</div><ul class="lvlist">'+LR.map(function(x){
    return '<li class="lvch"><div class="grow"><b>'+esc(x.title)+'</b>'+(x.sources>1?' '+pill('',x.sources+' kaynak'):'')+' <span class="dim">'+esc([x.source,x.country].filter(Boolean).join(' · '))+'</span></div>'+
      '<span class="dim num">'+esc(x.removed_at?E.when(ts(x.removed_at)):'-')+'</span>'+
      '<span class="act sm"><button data-lv-restore="'+esc(x.id)+'" data-lv-rname="'+esc(x.title)+'">Geri al</button></span></li>'}).join('')+'</ul>';
}

/* ---- panel ---- */
function drawPanel(){
  var p=$('lv-panel');if(!p)return;
  if(sub==='events'){
    if(!evl){p.innerHTML='<div class="evbox"></div>';
      evl=E.list(p.firstChild,{empty:'Canlı TV olayı yok.',fetch:function(before){
        return call('/api/ops/events?limit=30&site=famelack'+(before?'&before='+enc(before):''))}});
      evl.reset()}
    return;
  }
  evl=null;
  if(sub==='channels'){
    var a=document.activeElement,typing=a&&a.id==='lv-q',pos=typing?a.selectionStart:0;
    p.innerHTML=safe(channelsHtml);
    if(typing){var q=$('lv-q');if(q){q.focus();try{q.setSelectionRange(pos,pos)}catch(e){}}}
  }
  else if(sub==='sources')p.innerHTML=safe(sourcesHtml);
  else if(sub==='removed')p.innerHTML=safe(removedHtml);
}

/* ---- veri ---- */
function loadLive(){return call('/api/ops/live').then(function(d){LV=d;lvFail=''}).catch(function(e){lvFail=msgOf(e)}).then(function(){drawHead();if(sub==='channels')drawPanel()})}
function loadRemoved(){return call('/api/ops/live/removed').then(function(d){LR=(d&&d.channels)||[];lrFail=''}).catch(function(e){lrFail=msgOf(e)}).then(function(){if(sub==='removed')drawPanel()})}
function loadSources(){return call('/api/ops/live/sources').then(function(d){MS=d;msFail=''}).catch(function(e){msFail=msgOf(e)}).then(function(){if(sub==='sources')drawPanel()})}
function loadSub(){if(sub==='removed')loadRemoved();else if(sub==='sources'){if(!msBusy&&!editing())loadSources()}}
function editing(){var a=document.activeElement,p=$('lv-panel');return !!(a&&p&&p.contains(a)&&a.tagName==='INPUT')}

function scan(b){
  b.disabled=true;
  call('/api/ops/live/scan',{method:'POST'}).then(function(r){
    E.toast(r&&r.started===false?'Canlı TV: zaten çalışıyor':'Canlı TV taraması başladı',r&&r.started===false?'bad':'');
    setTimeout(loadLive,1500);
  }).catch(function(e){E.toast('Başlatılamadı: '+msgOf(e),'bad');b.disabled=false});
}
function setAuto(on){
  call('/api/ops/settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({live_tv:{enabled:on}})})
    .then(function(){E.toast('Canlı TV otomatik tarama '+(on?'açıldı':'kapatıldı'));try{document.dispatchEvent(new CustomEvent('dz:settings-changed'))}catch(e){}})
    .catch(function(e){E.toast('Kaydedilemedi: '+msgOf(e),'bad')}).then(loadLive);
}
function m3uDone(name,p){
  return p.then(function(d){msLast={name:name,summary:d.summary};E.toast('İçe aktarıldı: '+name);if(d.source!==undefined){MF.name='';MF.url='';MF.file=null}})
    .catch(function(e){E.toast('İçe aktarılamadı: '+msgOf(e),'bad')})
    .then(function(){msBusy=false;return loadSources()}).then(function(){setTimeout(loadSources,8000);loadLive()});
}
function m3uAdd(){
  var body={name:MF.name,kind:MF.kind,llm:!!MF.llm};
  if(MF.kind==='link'){if(!/^https?:\/\//i.test(MF.url||'')){E.toast('Geçerli bir http(s) bağlantısı girin','bad');return}body.url=MF.url}
  else{if(!MF.file){E.toast('Bir .m3u dosyası seçin','bad');return}body.content=MF.file.text;if(!body.name)body.name=MF.file.name.replace(/\.m3u8?$/i,'')}
  msBusy=true;drawPanel();
  m3uDone(body.name||MF.url||'M3U',call('/api/ops/live/sources',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}));
}
function m3uFile(inp){
  var f=inp.files&&inp.files[0];if(!f)return;
  var max=(MS&&MS.max_bytes)||5242880;
  if(f.size>max){E.toast('Dosya '+Math.round(max/1048576)+' MB sınırını aşıyor','bad');inp.value='';return}
  var r=new FileReader();
  r.onload=function(){MF.file={name:f.name,size:f.size,text:String(r.result||'')};drawPanel()};
  r.onerror=function(){E.toast('Dosya okunamadı','bad')};
  r.readAsText(f);
}

function bind(){
  var root=$('live-root');
  root.addEventListener('change',function(ev){
    var t=ev.target;
    if(t.dataset.lvauto){setAuto(t.checked);return}
    if(t.dataset.m3uLlm){MF.llm=t.checked;return}
    if(t.id==='m3u-file')m3uFile(t);
  });
  root.addEventListener('input',function(ev){
    var t=ev.target;
    if(t.dataset&&t.dataset.m3uField){MF[t.dataset.m3uField]=t.value;return}
    if(t.id==='lv-q'){clearTimeout(qTimer);qTimer=setTimeout(function(){F.q=t.value.trim();drawPanel()},200)}
  });
  root.addEventListener('click',function(ev){
    var b=ev.target.closest('button');if(!b||!root.contains(b)||b.disabled)return;
    if(b.closest('.aevl,.evbox'))return;   /* olay listesi kendi dinleyicisinde */
    if(b.dataset.lvsub){sub=b.dataset.lvsub;
      Array.prototype.forEach.call($('lv-subs').querySelectorAll('[data-lvsub]'),function(x){x.setAttribute('aria-selected',String(x===b))});
      drawPanel();loadSub();return}
    var a=b.getAttribute('data-lv');
    if(a==='scan'){scan(b);return}
    if(a==='retry'){loadLive();return}
    if(b.dataset.lvf!==undefined){F.health=b.dataset.lvf;drawPanel();return}
    if(b.dataset.m3uKind){MF.kind=b.dataset.m3uKind;drawPanel();return}
    if(b.id==='m3u-add'){m3uAdd();return}
    if(b.dataset.m3uRefresh){msBusy=true;drawPanel();
      var rn=(((MS&&MS.sources)||[]).filter(function(x){return x.id===b.dataset.m3uRefresh})[0]||{}).name||'';
      m3uDone(rn,call('/api/ops/live/sources/'+enc(b.dataset.m3uRefresh)+'/refresh',{method:'POST'}));return}
    if(b.dataset.m3uDel){
      if(!window.confirm('"'+b.dataset.m3uDname+'" kaynağı ve tüm kanalları silinsin mi?'))return;
      call('/api/ops/live/sources/'+enc(b.dataset.m3uDel),{method:'DELETE'})
        .then(function(d){E.toast('Silindi · '+num(d.removed)+' kanal kaldırıldı');loadSources();loadLive()})
        .catch(function(e){E.toast('Silinemedi: '+msgOf(e),'bad')});
      return}
    if(b.dataset.lvRestore){
      b.disabled=true;
      call('/api/ops/live/channels/'+enc(b.dataset.lvRestore)+'/restore',{method:'POST'})
        .then(function(){E.toast('Geri alındı: '+b.dataset.lvRname);loadRemoved();loadLive()})
        .catch(function(e){E.toast('Geri alınamadı: '+msgOf(e),'bad');b.disabled=false});
    }
  });
  root.addEventListener('keydown',function(ev){
    if(ev.key==='Enter'&&ev.target&&(ev.target.id==='m3u-url'||ev.target.id==='m3u-name')){ev.preventDefault();m3uAdd()}
  });
}

function visible(){var t=$('tab-live');return !!t&&!t.classList.contains('hidden')&&!document.hidden}
function tick(){
  if(!visible())return;
  tickN++;
  if(tickN%(LV&&LV.running?3:20)===0&&!editing())loadLive();
  if(tickN%30===0)loadSub();
}
document.addEventListener('dz:ops-changed',function(){if(visible()&&evl)evl.load(false)});
function onShow(){
  build();
  if(!timer)timer=setInterval(tick,1000);
  loadLive();loadSub();
  if(sub==='events'&&evl)evl.load(false);else drawPanel();
}
window.dzTabHooks=window.dzTabHooks||{};
window.dzTabHooks.live=onShow;
if(!$('tab-live').classList.contains('hidden'))onShow();
})();
