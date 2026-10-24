/* "Hatalar" sekmesi (#errors): sunucu hataları (GET /api/ops/errors?source=&since=, tüm siteler + Canlı TV + sistem)
   ve cihaz logları (GET /api/ops/debug-log, istemci hata ayıklama modu). Her iki bölümde "Kopyala" (düz metin).
   Ayrıca Ayarlar sekmesinin altındaki "Sistem olayları" listesi (GET /api/ops/events?system=1). Gizliyken ağ isteği yok. */
(function(){
'use strict';
var $=function(i){return document.getElementById(i)};
var E=window.dzEv;
var built=false,list=null,F={source:'',range:'24h'},sources=[],timer=null,tickN=0;
var dev={devices:null,sel:'',lines:[],seq:0,fail:'',busy:false};
var RANGES=[['24h','24 sa',86400e3],['7d','7 gün',7*86400e3],['all','Tümü',0]];
var DEV_MAX=500;

function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
function enc(v){return encodeURIComponent(v)}
function call(path){return fetch(path).then(function(r){return r.json().catch(function(){return null}).then(function(j){
  if(!r.ok)throw new Error((j&&j.error&&j.error.message)||('HTTP '+r.status));return j})})}
function sinceIso(){var r=RANGES.filter(function(x){return x[0]===F.range})[0];return r&&r[2]?new Date(Date.now()-r[2]).toISOString().replace(/\.\d+Z$/,'Z'):''}
function T(t){return t?new Date(t).getTime():0}
function stamp(t){return T(t)?new Date(t).toLocaleString('tr-TR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'}):'-'}

/* ---- iskelet ---- */
function build(){
  if(built)return;built=true;
  $('err-root').innerHTML=
    '<section class="errsec"><div class="errh"><h2>Sunucu hataları</h2><span class="grow"></span>'+
      '<select id="er-src" aria-label="Kaynak"></select>'+
      '<div class="seg" id="er-range" role="group" aria-label="Zaman">'+RANGES.map(function(r){return '<button data-range="'+r[0]+'" aria-pressed="'+(F.range===r[0])+'">'+r[1]+'</button>'}).join('')+'</div>'+
      '<button class="btn" id="er-copy">Kopyala</button><button class="btn" id="er-reload">Yenile</button></div>'+
      '<div id="er-meta" class="dim errmeta"></div><div id="er-list"></div></section>'+
    '<section class="errsec"><div class="errh"><h2>Cihaz logları</h2><span class="grow"></span>'+
      '<select id="dv-sel" aria-label="Cihaz"></select>'+
      '<button class="btn" id="dv-copy">Kopyala</button><button class="btn" id="dv-reload">Yenile</button></div>'+
      '<div id="dv-meta" class="dim errmeta"></div><pre id="dv-log" class="devlog mono" tabindex="0"></pre></section>';
  list=E.list($('er-list'),{source:true,err:true,time:true,empty:'Bu süzgeçle hata yok.',fetch:function(){
    var q=['limit=500'];if(F.source)q.push('source='+enc(F.source));var s=sinceIso();if(s)q.push('since='+enc(s));
    return call('/api/ops/errors?'+q.join('&')).then(function(d){
      sources=(d&&d.sources)||[];drawSources();
      $('er-meta').textContent=(d&&d.total!=null?d.total+' hata':'');
      return {events:(d&&d.events)||[],next_before:null};
    });
  }});
  $('er-src').addEventListener('change',function(){F.source=this.value;list.reset()});
  $('er-range').addEventListener('click',function(ev){
    var b=ev.target.closest('button[data-range]');if(!b)return;F.range=b.getAttribute('data-range');
    Array.prototype.forEach.call(this.querySelectorAll('button'),function(x){x.setAttribute('aria-pressed',String(x===b))});list.reset();
  });
  $('er-copy').addEventListener('click',function(){list.copyAll()});
  $('er-reload').addEventListener('click',function(){list.load(false)});
  $('dv-sel').addEventListener('change',function(){dev.sel=this.value;dev.lines=[];dev.seq=0;drawLog();loadLog()});
  $('dv-reload').addEventListener('click',function(){loadDevices();if(dev.sel){dev.lines=[];dev.seq=0;loadLog()}});
  $('dv-copy').addEventListener('click',function(){
    var t=dev.lines.map(lineOf).join('\n');
    E.copyText(t||'(boş)').then(function(){E.toast('Kopyalandı')}).catch(function(){E.toast('Kopyalanamadı','bad')});
  });
  drawSources();
}
function drawSources(){
  var el=$('er-src');if(!el)return;
  var all=sources.slice();if(F.source&&all.indexOf(F.source)<0)all.push(F.source);
  el.innerHTML='<option value="">Tüm kaynaklar</option>'+all.map(function(s){return '<option value="'+esc(s)+'"'+(s===F.source?' selected':'')+'>'+esc(E.srcLabel(s))+'</option>'}).join('');
}

/* ---- cihaz logları: yeni satırlar EN ALTTA ---- */
function lineOf(l){return [stamp(l.at||l.t),l.area||'-',l.level||'-',String(l.text==null?'':l.text).replace(/\s+/g,' ')].join(' | ')}
function loadDevices(){
  return call('/api/ops/debug-log').then(function(d){dev.devices=(d&&d.devices)||[];dev.fail=''}).catch(function(x){dev.fail=String(x&&x.message||x)}).then(drawDevices);
}
function drawDevices(){
  var el=$('dv-sel');if(!el)return;
  var l=dev.devices||[];
  if(dev.sel&&!l.some(function(d){return d.device===dev.sel}))dev.sel='';
  el.innerHTML='<option value="">'+(l.length?'Cihaz seç…':'Cihaz yok')+'</option>'+l.map(function(d){
    return '<option value="'+esc(d.device)+'"'+(d.device===dev.sel?' selected':'')+'>'+esc(d.device+(d.app_ver?' · v'+d.app_ver:'')+(d.mode?' · '+d.mode:'')+' · '+E.ago(d.last_at))+'</option>'}).join('');
  var cur=l.filter(function(d){return d.device===dev.sel})[0];
  $('dv-meta').textContent=dev.fail?'Alınamadı: '+dev.fail:cur?(cur.ua||'')+' · '+cur.lines+' satır':l.length?'':'Hata ayıklama modu açık cihaz yok.';
  if(!dev.sel)drawLog();
}
function loadLog(){
  if(!dev.sel||dev.busy)return;
  dev.busy=true;var sel=dev.sel;
  call('/api/ops/debug-log?device='+enc(sel)+'&limit=300&since='+dev.seq).then(function(d){
    if(sel!==dev.sel)return;
    var add=(d&&d.lines)||[];
    if(add.length){dev.lines=dev.lines.concat(add).slice(-DEV_MAX);drawLog()}
    dev.seq=(d&&d.seq)||dev.seq;
  }).catch(function(x){$('dv-meta').textContent='Alınamadı: '+(x&&x.message||x)}).then(function(){dev.busy=false});
}
function drawLog(){
  var el=$('dv-log');if(!el)return;
  var stick=el.scrollTop+el.clientHeight>=el.scrollHeight-20;
  el.textContent=dev.sel?(dev.lines.length?dev.lines.map(lineOf).join('\n'):'(satır yok)'):'';
  el.classList.toggle('hidden',!dev.sel);
  if(stick)el.scrollTop=el.scrollHeight;
}

function visible(){var t=$('tab-errors');return !!t&&!t.classList.contains('hidden')&&!document.hidden}
function tick(){
  if(!visible())return;
  tickN++;
  if(dev.sel&&tickN%3===0)loadLog();
  if(tickN%15===0)loadDevices();
}
document.addEventListener('dz:ops-changed',function(){if(visible()&&list)list.load(false)});
function onShow(){
  build();
  if(!timer)timer=setInterval(tick,1000);
  list.reset();loadDevices();if(dev.sel)loadLog();
}

/* ---- Ayarlar › Sistem olayları (siteye bağlı olmayan: TMDB, kütüphane sıfırlama, sitesiz kaynak bulucu) ---- */
var sys=null;
function sysShow(){
  var root=$('sysev-root');if(!root)return;
  if(!sys){
    root.innerHTML='<h2>Sistem olayları</h2><div id="sysev-list"></div>';
    sys=E.list($('sysev-list'),{empty:'Sistem olayı yok.',fetch:function(before){
      return call('/api/ops/events?system=1&limit=20'+(before?'&before='+enc(before):''))}});
    sys.reset();
  } else sys.load(false);
}
document.addEventListener('dz:ops-changed',function(){var t=$('tab-settings');if(sys&&t&&!t.classList.contains('hidden'))sys.load(false)});

window.dzTabHooks=window.dzTabHooks||{};
window.dzTabHooks.errors=onShow;
var prevSettings=window.dzTabHooks.settings;
window.dzTabHooks.settings=function(){if(prevSettings)prevSettings();sysShow()};
if(!$('tab-errors').classList.contains('hidden'))onShow();
if(!$('tab-settings').classList.contains('hidden'))sysShow();
})();
