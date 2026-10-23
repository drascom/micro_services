/* Arka planlar sekmesi: istemcinin ana ekran arka plan görselleri (/api/backgrounds).
   Yükle (çoklu seçim, jpg/png/webp ≤ 15 MB; sunucu 1920x1080'e küçültür + bulanık önizleme üretir), ızgara, sil. */
(function(){
'use strict';
var $=function(i){return document.getElementById(i)};
var B=null,failed=false,busy=0;

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
function load(){
  return call('/api/backgrounds',{cache:'no-cache'}).then(function(d){B=d.items||[];failed=false;render()})
    .catch(function(e){failed=String(e&&e.message||e);render()});
}
function render(){
  var root=$('bg-root');if(!root)return;
  var h='<section><h2>Arka planlar</h2>'+
    '<p class="catlead">İstemcinin ana ekran arka planları. JPG, PNG ya da WEBP (en çok 15 MB); sunucu 1920×1080\'e küçültür ve cam paneller için bulanık önizleme üretir.</p>'+
    '<div class="catadd"><input id="bg-file" type="file" accept="image/jpeg,image/png,image/webp" multiple hidden>'+
    '<button class="btn primary" id="bg-pick"'+(busy?' disabled':'')+'>'+(busy?'Yükleniyor… ('+busy+')':'Görsel yükle')+'</button></div>';
  if(failed&&!B)h+='<div class="empty">Yüklenemedi: '+esc(failed)+'</div>';
  else if(!B)h+='<div class="empty">Yükleniyor…</div>';
  else if(!B.length)h+='<div class="empty">Henüz arka plan yok. Yukarıdan ilkini yükleyin.</div>';
  else h+='<ul class="bggrid">'+B.map(function(b){
    return '<li class="bgcard"><a href="'+esc(b.url)+'" target="_blank" rel="noopener"><img src="'+esc(b.blur_url)+'" data-full="'+esc(b.url)+'" alt="" loading="lazy"></a>'+
      '<span class="bgmeta">'+b.width+'×'+b.height+'</span>'+
      '<button class="btn" data-bgdel="'+esc(b.id)+'" aria-label="Sil">Sil</button></li>';
  }).join('')+'</ul>';
  root.innerHTML=h+'</section>';
  root.querySelectorAll('img[data-full]').forEach(function(img){ // bulanık önizleme hemen, net küçük resim yüklenince
    var full=new Image();full.onload=function(){img.src=full.src};full.src=img.getAttribute('data-full');
  });
}
function upload(files){
  var list=Array.prototype.slice.call(files||[]);if(!list.length)return;
  busy=list.length;render();
  var ok=0,chain=Promise.resolve();
  list.forEach(function(f){
    chain=chain.then(function(){
      if(f.size>15*1024*1024){toast(f.name+': 15 MB\'tan büyük','bad');return}
      var fd=new FormData();fd.append('file',f,f.name);
      return call('/api/backgrounds',{method:'POST',body:fd}).then(function(){ok++})
        .catch(function(e){toast(f.name+': '+(e&&e.message||e),'bad')});
    }).then(function(){busy=Math.max(0,busy-1);render()});
  });
  chain.then(function(){if(ok)toast(ok+' görsel yüklendi');return load()});
}
document.addEventListener('click',function(ev){
  var root=$('bg-root');if(!root||!root.contains(ev.target))return;
  if(ev.target.closest('#bg-pick')){var inp=$('bg-file');if(inp)inp.click();return}
  var d=ev.target.closest('[data-bgdel]');
  if(d){
    if(!confirm('Bu arka plan silinsin mi?'))return;
    d.disabled=true;
    call('/api/backgrounds/'+encodeURIComponent(d.getAttribute('data-bgdel')),{method:'DELETE'})
      .then(function(){toast('Silindi');return load()})
      .catch(function(e){toast('Silinemedi: '+(e&&e.message||e),'bad');return load()});
  }
});
document.addEventListener('change',function(ev){
  if(ev.target&&ev.target.id==='bg-file'){upload(ev.target.files);ev.target.value=''}
});
function onShow(){load()}
window.dzTabHooks=window.dzTabHooks||{};
window.dzTabHooks.backgrounds=onShow;
var panel=$('tab-backgrounds');if(panel&&!panel.classList.contains('hidden'))load();
})();
