/* ui/skeleton.js - anlik cizilen iskeletler (bos ekran asla gorunmez). */
(function (g) {
  'use strict';
  var DZ = g.DZ = g.DZ || {};

  function el(tag, cls, html) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html !== undefined && html !== null) n.innerHTML = html;
    return n;
  }

  /* hero iskeleti */
  function hero() {
    var h = el('div', 'hero');
    h.innerHTML =
      '<div class="hero-bg sk" style="opacity:1"></div>' +
      '<div class="hero-fade"></div>' +
      '<div class="hero-body">' +
      '  <div class="sk" style="width:760px;height:64px;margin-bottom:22px;"></div>' +
      '  <div class="sk" style="width:420px;height:26px;margin-bottom:20px;"></div>' +
      '  <div class="sk" style="width:880px;height:24px;margin-bottom:12px;"></div>' +
      '  <div class="sk" style="width:640px;height:24px;margin-bottom:34px;"></div>' +
      '  <div style="display:flex;">' +
      '    <div class="sk" style="width:220px;height:64px;margin-right:20px;"></div>' +
      '    <div class="sk" style="width:260px;height:64px;"></div>' +
      '  </div>' +
      '</div>';
    return h;
  }

  /* baslikli/basliksiz tek iskelet satir (poster: ana sayfa raflari gibi dikey poster boyutu -> gercek icerik gelince yerlesim kaymaz) */
  function row(title, count, poster) {
    var n = el('section', 'row');
    var t = el('div', 'row-title');
    if (title) { t.textContent = title; } else { t.innerHTML = '<span class="sk" style="display:inline-block;width:320px;height:30px;"></span>'; }
    n.appendChild(t);
    var vp = el('div', 'row-viewport');
    var strip = el('div', 'row-strip');
    var c = count || 6;
    if (c > 6) c = 6;
    for (var i = 0; i < c; i++) {
      var card = el('div', 'card skel' + (poster ? ' card-poster' : ''));
      card.appendChild(el('div', 'sk'));
      strip.appendChild(card);
    }
    vp.appendChild(strip);
    n.appendChild(vp);
    return n;
  }

  /* ilk acilis / veri gelene kadar: iskeletin ustunde ortada marka logosu (img/logo-square.png). Veri gelince ana sayfa
     yeniden kuruldugu icin (renderBoot) kendiliginden kalkar; data-nav YOK (odaga girmez), yalniz opacity girisi. */
  function bootLogo() {
    var box = el('div', 'boot-logo');
    var im = el('img', 'boot-logo-img');
    im.src = 'img/logo-square.png';
    im.alt = 'DiziFlix';
    im.onerror = function () { box.className = 'boot-logo hidden'; };
    box.appendChild(im);
    return box;
  }

  /* tam ana ekran iskeleti: hero + 5 shimmer satir + ortada logo */
  function home() {
    var frag = document.createDocumentFragment();
    var page = el('div', null);
    page.id = 'page';
    page.appendChild(hero());
    var rowsBox = el('div', 'rows');
    for (var i = 0; i < 5; i++) rowsBox.appendChild(row(null, 6, true));
    page.appendChild(rowsBox);
    page.appendChild(bootLogo());
    frag.appendChild(page);
    return frag;
  }

  /* detay ekrani iskeleti */
  function detail() {
    var d = el('div', 'detail');
    d.innerHTML =
      '<div class="detail-hero">' +
      '  <div class="detail-bg sk" style="opacity:1"></div>' +
      '  <div class="detail-fade"></div>' +
      '  <div class="detail-body">' +
      '    <div class="sk" style="width:700px;height:64px;margin-bottom:20px;"></div>' +
      '    <div class="sk" style="width:520px;height:26px;margin-bottom:20px;"></div>' +
      '    <div class="sk" style="width:900px;height:24px;margin-bottom:12px;"></div>' +
      '    <div class="sk" style="width:780px;height:24px;margin-bottom:34px;"></div>' +
      '    <div style="display:flex;">' +
      '      <div class="sk" style="width:220px;height:64px;margin-right:20px;"></div>' +
      '      <div class="sk" style="width:260px;height:64px;"></div>' +
      '    </div>' +
      '  </div>' +
      '</div>';
    return d;
  }

  DZ.skeleton = { hero: hero, row: row, home: home, detail: detail, bootLogo: bootLogo, el: el };
})(window);
