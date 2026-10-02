/* Kategoriler sekmesi: ana ekranda gösterilen kategorileri yapılacaklar listesi gibi yönetir
   (/api/ops/categories: ekle, sil, başlık düzenle, aç/kapat, "en az öğe", sürükleyip sırala).
   Ana ekranın sabit (iskelet) satırları da listede kilitli görünür: silinmez/gizlenmez/adı değişmez, yalnız sırası değişir.
   Sıra ana ekrandaki sırayla aynıdır; her değişiklik anında kaydolur, hata olursa eski hâline döner. */
(function(){
'use strict';
var $=function(i){return document.getElementById(i)};
var C=null,failed=false,busy=false;

function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
function toast(msg,kind){
  var box=$('toasts');if(!box)return;
  var el=document.createElement('div');el.className='toast'+(kind==='bad'?' bad':'');el.textContent=msg;
  box.appendChild(el);setTimeout(function(){if(el.parentNode)el.parentNode.removeChild(el)},kind==='bad'?5000:2200);
}
function call(path,opt){
  return fetch(path,opt).then(function(r){
    return r.json().catch(function(){return null}).then(function(j){
      if(!r.ok){var m=(j&&j.error&&j.error.message)||(j&&j.detail&&(j.detail.message||j.detail))||('HTTP '+r.status);throw new Error(typeof m==='string'?m:JSON.stringify(m))}
      return j;
    });
  });
}
function send(method,path,body){
  return call(path,{method:method,headers:{'Content-Type':'application/json'},body:body==null?undefined:JSON.stringify(body)});
}
function find(slug){for(var i=0;i<(C||[]).length;i++)if(C[i].slug===slug)return C[i];return null}

/* ---- yükleme ---- */
function load(){
  return call('/api/ops/categories').then(function(d){C=d.categories||[];failed=false;render()})
    .catch(function(e){failed=String(e&&e.message||e);if(!C)render()});
}
function fail(e,msg){toast((msg||'Kaydedilemedi')+': '+(e&&e.message||e),'bad');return load()}

/* ---- çizim ---- */
function note(c){
  if(!c.lists)return 'Henüz hiçbir siteden başlık gelmedi';
  var t=c.titles+' başlık · '+c.playable+' oynatılabilir';
  if(c.enabled&&c.playable<c.min_items)t+=' · ana ekranda görünmüyor (en az '+c.min_items+' oynatılabilir başlık gerekir)';
  return t;
}
function moveHtml(c,i,n){
  return '<span class="cathandle" draggable="true" title="Sürükleyerek sırala" aria-hidden="true">⋮⋮</span>'+
    '<span class="catmove"><button class="btn" data-up="'+esc(c.slug)+'" aria-label="Yukarı taşı"'+(i===0?' disabled':'')+'>▲</button>'+
    '<button class="btn" data-down="'+esc(c.slug)+'" aria-label="Aşağı taşı"'+(i===n-1?' disabled':'')+'>▼</button></span>';
}
function lockedRowHtml(c,i,n){
  return '<li class="catrow locked" data-slug="'+esc(c.slug)+'">'+moveHtml(c,i,n)+
    '<span class="catmain"><span class="cattitle fixed"><span class="catlock" title="Kilitli satır" aria-label="Kilitli">🔒</span> '+esc(c.title)+'</span>'+
    '<span class="catnote">Ana ekranın sabit satırı</span></span></li>';
}
function rowHtml(c,i,n){
  if(c.kind==='system'||c.locked)return lockedRowHtml(c,i,n);
  return '<li class="catrow'+(c.enabled?'':' off')+'" data-slug="'+esc(c.slug)+'">'+
    moveHtml(c,i,n)+
    '<span class="catmain"><button class="cattitle" data-edit="'+esc(c.slug)+'" title="Adı düzenlemek için tıklayın">'+esc(c.title)+'</button>'+
    '<span class="catnote">'+esc(note(c))+'</span></span>'+
    '<label class="catmin">En az <input type="number" min="1" max="100" value="'+c.min_items+'" data-min="'+esc(c.slug)+'" aria-label="En az öğe"> öğe</label>'+
    '<label class="sw"><input type="checkbox" role="switch" data-on="'+esc(c.slug)+'" '+(c.enabled?'checked ':'')+'aria-label="Ana ekranda göster"><span class="knob"></span></label>'+
    '<button class="btn" data-del="'+esc(c.slug)+'" aria-label="Sil">Sil</button></li>';
}
function render(){
  var root=$('cat-root');if(!root)return;
  var keep=$('cat-new')?$('cat-new').value:'';
  var h='<section><h2>Ana ekran satırları</h2>'+
    '<p class="catlead">Burada eklediğiniz kategoriler ana ekranda bu sırayla satır olarak görünür. Sırayı değiştirmek için ⋮⋮ tutamacını sürükleyin.</p>'+
    '<p class="catlead">Kilitli satırlar silinemez, yalnız sırası değişir.</p>'+
    '<div class="catadd"><input id="cat-new" type="text" maxlength="60" placeholder="Yeni kategori adı (örn. Kore Dizileri)" aria-label="Yeni kategori adı" autocomplete="off">'+
    '<button class="btn" id="cat-add">Kategori ekle</button></div>';
  if(failed&&!C)h+='<div class="empty">Yüklenemedi: '+esc(failed)+'</div>';
  else if(!C)h+='<div class="empty">Yükleniyor…</div>';
  else if(!C.some(function(c){return c.kind!=='system'&&!c.locked}))h+='<div class="empty">Henüz kategori yok. Yukarıdan ilkini ekleyin.</div>'+(C.length?'<ol class="catlist" id="cat-list">'+C.map(function(c,i){return rowHtml(c,i,C.length)}).join('')+'</ol>':'');
  else h+='<ol class="catlist" id="cat-list">'+C.map(function(c,i){return rowHtml(c,i,C.length)}).join('')+'</ol>';
  root.innerHTML=h+'</section>';
  var inp=$('cat-new');if(inp&&keep)inp.value=keep;
}

/* ---- işlemler ---- */
function add(){
  var inp=$('cat-new');if(!inp||busy)return;
  var t=inp.value.trim();if(!t){inp.focus();return}
  busy=true;
  send('POST','/api/ops/categories',{title:t}).then(function(){inp.value='';toast('Kategori eklendi');return load()})
    .catch(function(e){return fail(e,'Eklenemedi')}).then(function(){busy=false;var n=$('cat-new');if(n)n.focus()});
}
function patch(slug,body,msg){
  return send('PUT','/api/ops/categories/'+encodeURIComponent(slug),body).then(function(){toast(msg||'Kaydedildi');return load()})
    .catch(function(e){return fail(e)});
}
function saveOrder(order,prev){
  C=order.map(find).filter(Boolean);render();   // anında göster; hata olursa sunucudaki sıraya dön
  return send('PUT','/api/ops/categories-order',{order:order}).then(function(d){C=d.categories||C;render();toast('Sıra kaydedildi')})
    .catch(function(e){toast('Sıra kaydedilemedi: '+(e&&e.message||e),'bad');
      C=prev.map(find).filter(Boolean);render();return load()});
}
function order(){return (C||[]).map(function(c){return c.slug})}
function move(slug,dir){
  var o=order(),i=o.indexOf(slug),j=i+dir;if(i<0||j<0||j>=o.length)return;
  var prev=o.slice();o[i]=o[j];o[j]=slug;saveOrder(o,prev);
}
function editTitle(btn){
  var slug=btn.dataset.edit,c=find(slug);if(!c)return;
  var inp=document.createElement('input');inp.type='text';inp.className='cattitle-in';inp.maxLength=60;inp.value=c.title;inp.setAttribute('aria-label','Kategori adı');
  var done=false;
  function finish(save){
    if(done)return;done=true;
    var v=inp.value.trim();
    if(save&&v&&v!==c.title)patch(slug,{title:v},'Ad kaydedildi');else render();
  }
  inp.addEventListener('keydown',function(e){if(e.key==='Enter'){e.preventDefault();finish(true)}else if(e.key==='Escape'){e.preventDefault();finish(false)}});
  inp.addEventListener('blur',function(){finish(true)});
  btn.replaceWith(inp);inp.focus();inp.select();
}

/* ---- sürükle-bırak (yalnız tutamaçtan) ---- */
var drag=null,dragPrev=null;
function overRow(ev){var li=ev.target.closest&&ev.target.closest('.catrow');return li&&li!==drag?li:null}
document.addEventListener('dragstart',function(ev){
  var h=ev.target.closest&&ev.target.closest('.cathandle');if(!h)return;
  drag=h.closest('.catrow');dragPrev=order();
  try{ev.dataTransfer.effectAllowed='move';ev.dataTransfer.setData('text/plain',drag.dataset.slug);ev.dataTransfer.setDragImage(drag,16,16)}catch(e){}
  setTimeout(function(){if(drag)drag.classList.add('dragging')},0);
});
document.addEventListener('dragover',function(ev){
  if(!drag)return;
  var li=overRow(ev);if(!li||!li.parentNode||li.parentNode!==drag.parentNode)return;
  ev.preventDefault();
  var r=li.getBoundingClientRect(),after=ev.clientY>r.top+r.height/2;
  li.parentNode.insertBefore(drag,after?li.nextSibling:li);
});
document.addEventListener('drop',function(ev){if(drag)ev.preventDefault()});
document.addEventListener('dragend',function(){
  if(!drag)return;
  var list=$('cat-list'),prev=dragPrev;drag.classList.remove('dragging');drag=null;dragPrev=null;
  if(!list)return;
  var now=[].map.call(list.querySelectorAll('.catrow'),function(li){return li.dataset.slug});
  if(now.join('|')!==prev.join('|'))saveOrder(now,prev);else render();
});

/* ---- olaylar ---- */
document.addEventListener('click',function(ev){
  var root=$('cat-root');if(!root||!root.contains(ev.target))return;
  var t=ev.target.closest('button');if(!t)return;
  if(t.id==='cat-add')add();
  else if(t.dataset.up)move(t.dataset.up,-1);
  else if(t.dataset.down)move(t.dataset.down,1);
  else if(t.dataset.edit)editTitle(t);
  else if(t.dataset.del){
    var c=find(t.dataset.del);if(!c)return;
    if(window.confirm('“'+c.title+'” kategorisi silinsin mi? Ana ekrandan kalkar; aynı adla yeniden eklerseniz içindeki diziler geri gelir.'))
      send('DELETE','/api/ops/categories/'+encodeURIComponent(c.slug)).then(function(){toast('Kategori silindi');return load()}).catch(function(e){return fail(e,'Silinemedi')});
  }
});
document.addEventListener('change',function(ev){
  var root=$('cat-root');if(!root||!root.contains(ev.target))return;
  var t=ev.target;
  if(t.dataset.on)patch(t.dataset.on,{enabled:t.checked},t.checked?'Ana ekranda gösterilecek':'Ana ekrandan gizlendi');
  else if(t.dataset.min){
    var n=parseInt(t.value,10);
    if(!(n>=1&&n<=100)){toast('En az öğe sayısı 1 ile 100 arasında olmalı','bad');render();return}
    patch(t.dataset.min,{min_items:n});
  }
});
document.addEventListener('keydown',function(ev){
  if(ev.key==='Enter'&&ev.target&&ev.target.id==='cat-new'){ev.preventDefault();add()}
});

/* ---- sekme kancası ---- */
function onShow(){render();load()}
window.dzTabHooks=window.dzTabHooks||{};
window.dzTabHooks.categories=onShow;
var tab=$('tab-categories');
if(tab&&!tab.classList.contains('hidden'))onShow();
})();
