/* Yapım yan paneli: window.dzOpenMedia({id,title,site}) = olay bağlantıları + Kütüphane öğeleri için TEK hedef.
   Sağdan açılır (dar ekranda tam ekran), Esc / dışarı tıklama kapatır. Veri tek istek: GET /api/ops/library/{id}/detail
   (öğe, video kaynakları, sezon/bölüm listesi, kaynak bulucu geçmişi, ilişkili olaylar). id yoksa başlık aranır (/api/ops/library?q=). */
(function(){
'use strict';
var $=function(i){return document.getElementById(i)};
var E=window.dzEv;
var cur=null,D=null,fail='',seq=0,season=null,epSel=null,lastFocus=null;
var VST={broken:['bad','Kırık'],suspect:['warn','Şüpheli'],healthy:['ok','Sağlam'],unknown:['','Doğrulanmadı'],disabled:['','Devre dışı'],blocked:['','Engelli']};
var KINDL={movie:'Film',episode:'Bölüm',trailer:'Fragman'};
var TMDBL={matched:['ok','TMDB ✓'],review:['warn','TMDB inceleme'],unmatched:['','TMDB yok']};
var DIAG={reachable:['ok','Sunucu erişebiliyor'],ip_bound:['warn',"IP'ye bağlı"],forbidden:['warn','Site erişimi engelliyor'],
  not_media:['warn','Video yerine başka içerik'],gone:['bad','Dosya kaynakta yok'],expired:['warn','Adres süresi dolmuş'],
  timeout:['warn','Kaynak yanıt vermedi'],hls_unsupported_browser:['','Tarayıcı HLS desteklemiyor'],server_blocked:['bad','Sunucu da erişemiyor'],
  unreachable:['warn','Kaynağa ulaşılamadı']};

function esc(s){return E.esc(s)}
function enc(v){return encodeURIComponent(v)}
function num(n){return n==null?'-':(+n).toLocaleString('tr-TR')}
function pad(n){return (n==null?'?':n<10?'0'+n:String(n))}
function pill(cls,txt,tip){return '<span class="pill '+cls+'"'+(tip?' title="'+esc(tip)+'"':'')+'>'+esc(txt)+'</span>'}
function msgOf(e){return String(e&&e.message||e)}
function call(path){return fetch(path).then(function(r){return r.json().catch(function(){return null}).then(function(j){
  if(!r.ok)throw new Error((j&&j.error&&j.error.message)||('HTTP '+r.status));return j})})}
function safe(fn){try{return fn()}catch(e){try{console.error('media: çizilemedi',e)}catch(_){}return '<div class="note bad">Çizilemedi: '+esc(msgOf(e))+'</div>'}}

/* ---- aç / kapat ---- */
function open(ref){
  ref=ref||{};
  var root=$('mp');if(!root)return;
  if(root.classList.contains('hidden'))lastFocus=document.activeElement;
  cur=ref;D=null;fail='';season=null;epSel=null;
  root.classList.remove('hidden');root.setAttribute('aria-hidden','false');document.body.classList.add('mpopen');
  render();
  var box=$('mp-box');if(box&&box.focus)box.focus();
  var my=++seq;
  var idP=ref.id?Promise.resolve(ref.id):ref.title?call('/api/ops/library?facets=0&limit=1&q='+enc(ref.title)).then(function(d){var it=d&&d.items&&d.items[0];return it?it.id:null}):Promise.resolve(null);
  idP.then(function(id){
    if(!id)throw new Error('Yapım kütüphanede bulunamadı');
    return call('/api/ops/library/'+enc(id)+'/detail');
  }).then(function(d){if(my!==seq)return;D=d||{};var ss=seasonsOf();season=ss.length?ss[0]:null;render()})
    .catch(function(e){if(my!==seq)return;fail=msgOf(e);render()});
}
function close(){
  var root=$('mp');if(!root||root.classList.contains('hidden'))return;
  seq++;root.classList.add('hidden');root.setAttribute('aria-hidden','true');document.body.classList.remove('mpopen');
  $('mp-box').innerHTML='';cur=null;D=null;
  if(lastFocus&&lastFocus.focus&&document.contains(lastFocus))try{lastFocus.focus()}catch(e){}
}

/* ---- parçalar ---- */
function seasonsOf(){
  var s={};((D&&D.episodes)||[]).forEach(function(e){s[e.season]=1});
  ((D&&D.videos)||[]).forEach(function(v){if(v.kind==='episode'&&v.season!=null)s[v.season]=1});
  return Object.keys(s).map(Number).sort(function(a,b){return a-b});
}
function srcRow(v){
  var st=VST[v.status]||['',v.status||'?'];
  var prov=(v.providers&&v.providers.length?v.providers:v.stream_hosts||[]).join(', ');
  var d=v.diagnosis||{},dm=d.code?DIAG[d.code]||['',d.code]:null;
  var when=v.last_success_at?'son başarı '+E.ago(v.last_success_at):v.resolved_at?'çözüldü '+E.ago(v.resolved_at):v.last_checked_at?'kontrol '+E.ago(v.last_checked_at):'';
  var tip=[v.label,v.locator,v.last_attempt_at?'son deneme '+E.full(v.last_attempt_at):'',v.last_failure_at?'son hata '+E.full(v.last_failure_at):'',d.note||''].filter(Boolean).join('\n');
  return '<li class="msr st-'+esc(v.status)+'" title="'+esc(tip)+'">'+pill(st[0],st[1])+
    '<span class="grow"><b>'+esc(v.site)+'</b>'+(v.kind!=='episode'?' <span class="tag">'+esc(KINDL[v.kind]||v.kind)+'</span>':'')+
      (prov?' · '+esc(prov):v.host?' · <span class="mono">'+esc(v.host)+'</span>':'')+
      (when?' <span class="dim">· '+esc(when)+'</span>':'')+
      (v.last_error?'<span class="msre">'+esc(v.last_error)+(v.failures?' · '+num(v.failures)+' hata':'')+'</span>':'')+'</span>'+
    (dm?pill(dm[0],dm[1],d.note||''):'')+(d.proxied?pill('','vekil','Akışlar sunucu vekilinden geçiyor'):'')+
    (v.kind==='trailer'&&v.trailer_dead?pill('warn','ölü fragman'):'')+'</li>';
}
function srcList(list,empty){return list.length?'<ul class="msl">'+list.map(srcRow).join('')+'</ul>':'<div class="dim mpe">'+esc(empty||'Kaynak yok.')+'</div>'}
function ktPill(e){
  if(!e.sources)return '<span class="kt none" title="Kaynak yok">0</span>';
  var st=e.broken>=e.sources?'dead':e.broken?'partial':'ok';
  return '<span class="kt '+st+'" title="'+esc(e.broken+' kırık / '+e.sources+' kaynak'+(e.suspect?' · '+e.suspect+' şüpheli':''))+'">'+(e.suspect?'<span class="sus">?'+e.suspect+'</span>':'')+esc(e.broken+'/'+e.sources)+'</span>';
}
function headHtml(){
  var it=(D&&D.item)||{},sm=(D&&D.summary)||{};
  var t=TMDBL[it.tmdb];
  var meta=[it.type==='series'?'Dizi':it.type?'Film':'',it.year,(it.genres||[]).join(', '),it.rating?'★ '+(Math.round(it.rating*10)/10):''].filter(Boolean).map(esc).join(' · ');
  var links=[];
  if(it.tmdb_id)links.push('<a class="mlink" target="_blank" rel="noopener noreferrer" href="https://www.themoviedb.org/'+(it.type==='series'?'tv':'movie')+'/'+esc(it.tmdb_id)+'">TMDB ↗</a>');
  if(it.imdb_id)links.push('<a class="mlink" target="_blank" rel="noopener noreferrer" href="https://www.imdb.com/title/'+esc(it.imdb_id)+'/">IMDb ↗</a>');
  return '<div class="mph">'+(it.poster?'<img class="mpp" src="'+esc(it.poster)+'" alt="" loading="lazy" onerror="this.remove()">':'<span class="mpp ph">'+esc(String(it.title||cur.title||'?').charAt(0).toUpperCase())+'</span>')+
    '<div class="grow"><h3>'+esc(it.title||cur.title||cur.id||'?')+'</h3>'+
      (meta?'<div class="dim">'+meta+'</div>':'')+
      '<div class="mpt">'+(it.sources||[]).map(function(s){return '<span class="tag">'+esc(s)+'</span>'}).join('')+(t?'<span class="tag '+t[0]+'">'+esc(t[1])+'</span>':'')+links.join(' ')+'</div>'+
      (sm.label!=null?'<div class="dim mpsum">K/T <b>'+esc(sm.label)+'</b>'+(sm.suspect?' · '+num(sm.suspect)+' şüpheli':'')+(sm.healthy?' · '+num(sm.healthy)+' sağlam':'')+(sm.unknown?' · '+num(sm.unknown)+' doğrulanmadı':'')+(sm.blocked?' · '+num(sm.blocked)+' engelli':'')+'</div>':'')+
    '</div></div>';
}
function sourcesHtml(){
  var vids=(D&&D.videos)||[],it=(D&&D.item)||{};
  var other=vids.filter(function(v){return v.kind!=='episode'});
  var h='<h4 class="mps">Kaynaklar</h4>';
  if(it.type!=='series')return h+srcList(other,'Bu yapımın video kaynağı yok.')+(D.truncated?'<div class="dim mpe">Liste kısaltıldı.</div>':'');
  if(other.length)h+=srcList(other);
  var ss=seasonsOf();
  if(!ss.length)return h+'<div class="dim mpe">Bölüm yok.</div>';
  h+='<div class="seg mpss" role="group" aria-label="Sezon">'+ss.map(function(n){return '<button data-mpseason="'+n+'" aria-pressed="'+(n===season)+'">S'+n+'</button>'}).join('')+'</div>';
  var eps=((D.episodes)||[]).filter(function(e){return e.season===season});
  if(!eps.length){   /* bölüm tablosu yoksa kaynaklardan */
    var seen={};vids.forEach(function(v){if(v.kind==='episode'&&v.season===season&&!seen[v.episode]){seen[v.episode]=1;eps.push({season:v.season,episode:v.episode,title:v.episode_title,sources:0,broken:0,suspect:0})}});
    eps.forEach(function(e){vids.forEach(function(v){if(v.kind==='episode'&&v.season===e.season&&v.episode===e.episode&&v.status!=='disabled'&&v.status!=='blocked'){e.sources++;if(v.status==='broken')e.broken++;if(v.status==='suspect')e.suspect++}})});
    eps.sort(function(a,b){return a.episode-b.episode});
  }
  h+='<ul class="mpel">'+eps.map(function(e){
    var k=e.season+':'+e.episode,open=epSel===k;
    var r='<li class="mpe1'+(open?' open':'')+'"><button class="mpeb" data-mpep="'+esc(k)+'" aria-expanded="'+open+'"><span class="num dim">B'+pad(e.episode)+'</span><span class="grow">'+esc(e.title||'')+'</span>'+ktPill(e)+'</button>';
    if(open)r+=srcList(vids.filter(function(v){return v.kind==='episode'&&v.season===e.season&&v.episode===e.episode}),'Bu bölümün kaynağı yok.');
    return r+'</li>'}).join('')+'</ul>';
  return h;
}
function render(){
  var box=$('mp-box');if(!box)return;
  var h='<div class="mptop"><span class="dim">Yapım</span><span class="grow"></span><button class="btn icon" data-mpclose aria-label="Kapat" title="Kapat (Esc)">✕</button></div>';
  if(fail){box.innerHTML=h+'<div class="note bad">'+esc(fail)+'</div>';return}
  if(!D){box.innerHTML=h+'<h3>'+esc(cur&&(cur.title||cur.id)||'')+'</h3><div class="empty sm">Yükleniyor…</div>';return}
  box.innerHTML=h+safe(headHtml)+'<div id="mp-src">'+safe(sourcesHtml)+'</div>'+
    '<h4 class="mps">Kaynak bulucu</h4><div id="mp-fin"></div>'+
    '<h4 class="mps">Hatalar ve olaylar</h4><div id="mp-ev"></div>';
  E.list($('mp-fin'),{time:true,empty:'Kaynak bulucu kaydı yok.',fetch:function(){return Promise.resolve({events:D.finder||[],next_before:null})}}).reset();
  E.list($('mp-ev'),{time:true,err:true,empty:'Bu yapımla ilişkili olay yok.',fetch:function(){return Promise.resolve({events:D.events||[],next_before:null})}}).reset();
}
function drawSources(){var el=$('mp-src');if(el)el.innerHTML=safe(sourcesHtml)}

/* ---- etkileşim ---- */
document.addEventListener('keydown',function(ev){if(ev.key==='Escape'&&cur){ev.preventDefault();close()}});
document.addEventListener('click',function(ev){
  var root=$('mp');if(!root||root.classList.contains('hidden'))return;
  var t=ev.target;
  if(t.closest('[data-mpclose]')){close();return}
  if(t.closest('[data-goto]')&&root.contains(t)){setTimeout(close,0);return}
  var s=t.closest('[data-mpseason]');if(s){season=+s.getAttribute('data-mpseason');epSel=null;drawSources();return}
  var e=t.closest('[data-mpep]');if(e){var k=e.getAttribute('data-mpep');epSel=epSel===k?null:k;drawSources()}
});
$('mp-box').addEventListener('error',function(ev){var t=ev.target;if(t&&t.tagName==='IMG')t.remove()},true);

window.dzOpenMedia=open;
window.dzMedia={open:open,close:close};
})();
