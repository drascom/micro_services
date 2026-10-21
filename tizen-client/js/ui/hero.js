/* ui/hero.js - ana ekran hero billboard'u (backdrop + baslik + ozet + butonlar). */
(function (g) {
  'use strict';
  var DZ = g.DZ = g.DZ || {};

  function metaLine(item) {
    var parts = [];
    if (item.year) parts.push(item.year);
    if (item.rating) parts.push('Puan ' + item.rating);
    if (item.genres && item.genres.length) parts.push(item.genres.slice(0, 3).join(' - '));
    if (item.type) parts.push(item.type === 'series' ? 'Dizi' : 'Film');
    return parts.join('   ·   ');
  }

  /* cfg: {item, onDetail, index, count} */
  function create(cfg) {
    var o = cfg || {};
    var item = o.item || {};
    var n = document.createElement('div');
    n.className = 'hero';


    var bg = document.createElement('div');
    bg.className = 'hero-bg';
    n.appendChild(bg);

    var src = DZ.card.sized(item.backdrop, 1280, 720);
    if (src) {
      var pre = new Image();
      pre.onload = function () {
        bg.style.backgroundImage = 'url("' + src + '")';
        bg.classList.add('ready');
      };
      pre.src = src;
    }

    var fade = document.createElement('div');
    fade.className = 'hero-fade';
    n.appendChild(fade);

    var body = document.createElement('div');
    body.className = 'hero-body';

    var t = document.createElement('div');
    t.className = 'hero-title';
    t.textContent = item.logo_text || item.title || '';
    var focusRow = document.createElement('div');
    focusRow.setAttribute('data-nav-row', 'hero');
    t.setAttribute('data-nav', '1');
    t.setAttribute('role', 'button');
    t.setAttribute('aria-label', (item.title || '') + ' — sağ/sol: yapım değiştir, Enter: ayrıntılar');
    if (o.onDetail) t.addEventListener('click', function () { o.onDetail(item); }, false);
    focusRow.appendChild(t); body.appendChild(focusRow);

    if (item.tagline) {
      var tg = document.createElement('div');
      tg.className = 'hero-tagline';
      tg.textContent = item.tagline;
      body.appendChild(tg);
    }

    var m = document.createElement('div');
    m.className = 'hero-meta';
    m.textContent = metaLine(item);
    body.appendChild(m);

    var ov = document.createElement('div');
    ov.className = 'hero-overview';
    ov.textContent = item.overview || '';
    body.appendChild(ov);

    if (o.count > 1) {
      var dots = document.createElement('div'); dots.className = 'hero-dots';
      dots.setAttribute('role', 'img');
      dots.setAttribute('aria-label', 'Öne çıkan yapım ' + (o.index + 1) + ' / ' + o.count);
      for (var i = 0; i < o.count; i++) {
        var dot = document.createElement('span');
        dot.className = 'hero-dot' + (i === o.index ? ' active' : '');
        dot.setAttribute('aria-hidden', 'true'); dots.appendChild(dot);
      }
      n.appendChild(dots);
    }
    n.appendChild(body);
    return n;
  }

  DZ.hero = { create: create, metaLine: metaLine };
})(window);
