/* ui/modal.js - onay ve metin girisi diyaloglari. Kumandayla odaklanabilir. */
(function (g) {
  'use strict';
  var DZ = g.DZ = g.DZ || {};

  var host = null;
  var current = null;
  var offKey = null;

  function hostEl() {
    if (!host) host = document.getElementById('overlay');
    return host;
  }

  function close() {
    if (!current) return;
    if (current.stop) { try { current.stop(); } catch (eStop) {} }
    var h = hostEl();
    while (h.firstChild) h.removeChild(h.firstChild);
    h.classList.remove('active');
    if (offKey) { offKey(); offKey = null; }
    current = null;
    if (DZ.nav && DZ.nav.setEnabled) DZ.nav.setEnabled(true);
  }

  function isOpen() { return !!current; }

  /* cfg: {title, message, input:{placeholder,value}, buttons:[{label,primary,value}], onDone(value, inputText),
          onCancel(), ignoreEnterWhile(ev) -> true iken Enter yutulur (dugme tetiklenmez)} */
  function open(cfg) {
    close();
    var o = cfg || {};
    var h = hostEl();
    h.classList.add('active');
    if (DZ.nav && DZ.nav.setEnabled) DZ.nav.setEnabled(false);

    var back = document.createElement('div');
    back.className = 'modal-back' + (o.fullscreen ? ' modal-full' : '');
    var box = document.createElement('div');
    box.className = 'modal';

    var ttl = document.createElement('h2');
    ttl.textContent = o.title || '';
    box.appendChild(ttl);

    if (o.message) {
      var p = document.createElement('p');
      p.textContent = o.message;
      box.appendChild(p);
    }

    var input = null;
    if (o.input) {
      input = document.createElement('input');
      input.className = 'field';
      input.type = 'text';
      input.setAttribute('data-nav', '1');
      input.placeholder = o.input.placeholder || '';
      input.value = o.input.value || '';
      var wrap = document.createElement('div');
      wrap.setAttribute('data-nav-row', 'modal-input');
      wrap.appendChild(input);
      box.appendChild(wrap);
    }

    var brow = document.createElement('div');
    brow.className = 'row-btn';
    if (o.vertical) { brow.style.flexDirection = 'column'; brow.style.maxHeight = '560px'; brow.style.overflowY = 'auto'; }
    brow.setAttribute('data-nav-row', 'modal-buttons');
    var buttons = o.buttons || [{ label: 'Tamam', primary: true, value: true }];
    for (var i = 0; i < buttons.length; i++) {
      (function (b) {
        var el = document.createElement('div');
        el.className = 'btn' + (b.primary ? ' primary' : '');
        el.setAttribute('data-nav', '1');
        el.textContent = b.label;
        el.addEventListener('click', function () {
          var txt = input ? input.value : null;
          close();
          if (o.onDone) o.onDone(b.value, txt);
        }, false);
        brow.appendChild(el);
      })(buttons[i]);
    }
    box.appendChild(brow);
    back.appendChild(box);
    h.appendChild(back);

    current = { el: back, cfg: o };

    /* modal kendi mini navigasyonunu calistirir */
    var rows = [];
    if (input) rows.push([input]);
    var buttonEls = Array.prototype.slice.call(brow.querySelectorAll('[data-nav]'));
    if (o.vertical) buttonEls.forEach(function (el) { rows.push([el]); });
    else rows.push(buttonEls);
    var r = o.vertical ? Math.max(0, buttons.findIndex(function (b) { return b.primary; })) : rows.length - 1, c = 0;
    function paint() {
      for (var a = 0; a < rows.length; a++) {
        for (var b = 0; b < rows[a].length; b++) rows[a][b].classList.remove('focused');
      }
      var el = rows[r][c];
      if (el) {
        el.classList.add('focused');
        if (o.vertical) el.scrollIntoView({ block: 'nearest' });
        if (el.tagName === 'INPUT') { try { el.focus(); } catch (e) {} }
        else if (input) { try { input.blur(); } catch (e2) {} }
      }
    }
    paint();

    offKey = DZ.keys.onKey(function (ev) {
      if (!current) return false;
      if (ev.name === 'left') { if (c > 0) { c--; paint(); } return true; }
      if (ev.name === 'right') { if (c < rows[r].length - 1) { c++; paint(); } return true; }
      if (ev.name === 'up') { if (r > 0) { r--; c = 0; paint(); } return true; }
      if (ev.name === 'down') { if (r < rows.length - 1) { r++; c = 0; paint(); } return true; }
      if (ev.name === 'enter') {
        /* opsiyonel: menuyu acan Tamam/OK hala basiliyken (otomatik tekrar keydown'lari) dugmeyi tetikleme */
        if (o.ignoreEnterWhile && o.ignoreEnterWhile(ev)) return true;
        var el = rows[r][c];
        if (el && el.tagName === 'INPUT') { r = rows.length - 1; c = 0; paint(); return true; }
        if (el && el.click) el.click();
        return true;
      }
      if (ev.name === 'back') {
        close();
        if (o.onCancel) o.onCancel();
        return true;
      }
      return true;
    });
    return current;
  }

  /* text(title, bodyText, onClose): kaydirilabilir tam-metin modali.
     up/down -> govdeyi 60px kaydir (sinirda durur); back/enter -> kapat, onClose cagir. */
  function text(title, bodyText, onClose) {
    close();
    var h = hostEl();
    h.classList.add('active');
    if (DZ.nav && DZ.nav.setEnabled) DZ.nav.setEnabled(false);

    var back = document.createElement('div');
    back.className = 'modal-back';
    var box = document.createElement('div');
    box.className = 'modal modal-text';

    var ttl = document.createElement('h2');
    ttl.textContent = title || '';
    box.appendChild(ttl);

    var vp = document.createElement('div');
    vp.className = 'modal-text-vp';
    var content = document.createElement('div');
    content.className = 'modal-text-body';
    content.textContent = bodyText || '';
    vp.appendChild(content);
    box.appendChild(vp);

    back.appendChild(box);
    h.appendChild(back);

    current = { el: back, cfg: {} };

    var y = 0;
    var STEP = 60;
    function maxScroll() {
      var m = content.scrollHeight - vp.clientHeight;
      return m > 0 ? m : 0;
    }
    function paint() {
      var m = maxScroll();
      if (y < 0) y = 0;
      if (y > m) y = m;
      content.style.transform = 'translate3d(0,' + (-Math.round(y)) + 'px,0)';
    }
    paint();

    function doClose() {
      close();
      if (onClose) onClose();
    }

    offKey = DZ.keys.onKey(function (ev) {
      if (!current) return false;
      if (ev.name === 'up') { y -= STEP; paint(); return true; }
      if (ev.name === 'down') { y += STEP; paint(); return true; }
      if (ev.name === 'back' || ev.name === 'enter') { doClose(); return true; }
      return true;   /* modal acikken diger tuslari yut, alttaki nav calismasin */
    });
    return current;
  }

  /* loading(cfg): tam ekran yukleme modali. Donen gosterge + rastgele Turk atasozu
     (her PROVERB_MS'de yumusak gecisle degisir) + kucuk asama satiri.
     cfg: {onCancel, intervalMs, fadeMs, rng}. BACK -> modal kapanir, onCancel cagrilir;
     diger tuslar yutulur. Donen kontrolcu: {setStage(text), close(), isOpen()}. */
  var PROVERB_MS = 5000;
  var FADE_MS = 400;

  function loading(cfg) {
    close();
    var o = cfg || {};
    var h = hostEl();
    h.classList.add('active');
    if (DZ.nav && DZ.nav.setEnabled) DZ.nav.setEnabled(false);

    var back = document.createElement('div');
    back.className = 'modal-back modal-full';
    var box = document.createElement('div');
    box.className = 'loading-box';
    var mascot = document.createElement('img');   /* marka karakteri (img/mascot.png); dekoratif, data-nav degil */
    mascot.className = 'loading-mascot';
    mascot.src = 'img/mascot.png';
    mascot.alt = '';
    mascot.onerror = function () { mascot.className += ' hidden'; };
    box.appendChild(mascot);
    var spin = document.createElement('div');
    spin.className = 'loading-spinner';
    box.appendChild(spin);
    var proverbEl = document.createElement('div');
    proverbEl.className = 'loading-proverb';
    box.appendChild(proverbEl);
    var stageEl = document.createElement('div');
    stageEl.className = 'loading-stage';
    box.appendChild(stageEl);
    back.appendChild(box);
    h.appendChild(back);

    var picker = (DZ.proverbs && DZ.proverbs.createPicker) ? DZ.proverbs.createPicker(null, o.rng) : null;
    proverbEl.textContent = picker ? picker.next() : '';
    var fadeMs = o.fadeMs || FADE_MS;
    var fadeTimer = null;
    var rotTimer = setInterval(function () {
      if (!picker) return;
      proverbEl.className = 'loading-proverb fade';
      if (fadeTimer) clearTimeout(fadeTimer);
      fadeTimer = setTimeout(function () {
        fadeTimer = null;
        proverbEl.textContent = picker.next();
        proverbEl.className = 'loading-proverb';
      }, fadeMs);
    }, o.intervalMs || PROVERB_MS);

    var ctl = {
      setStage: function (text) { stageEl.textContent = text || ''; },
      close: function () { if (current && current.loading === ctl) close(); },
      isOpen: function () { return !!current && current.loading === ctl; },
      proverbEl: proverbEl,
      stageEl: stageEl
    };
    current = {
      el: back, cfg: o, loading: ctl,
      stop: function () {
        clearInterval(rotTimer);
        if (fadeTimer) { clearTimeout(fadeTimer); fadeTimer = null; }
      }
    };

    offKey = DZ.keys.onKey(function (ev) {
      if (!current || current.loading !== ctl) return false;
      if (ev.name === 'back') {
        ctl.close();
        if (o.onCancel) o.onCancel();
      }
      return true;   /* diger tuslari yut: alttaki ekran hareket etmesin */
    });
    return ctl;
  }

  function confirm(title, message, onYes, onNo) {
    return open({
      title: title,
      message: message,
      buttons: [
        { label: 'Evet', primary: true, value: true },
        { label: 'Hayir', value: false }
      ],
      onDone: function (v) { if (v) { if (onYes) onYes(); } else if (onNo) onNo(); },
      onCancel: onNo
    });
  }

  function prompt(title, message, placeholder, onDone) {
    return open({
      title: title,
      message: message,
      input: { placeholder: placeholder || '' },
      buttons: [
        { label: 'Kaydet', primary: true, value: true },
        { label: 'Vazgec', value: false }
      ],
      onDone: function (v, txt) { if (v && onDone) onDone(txt); }
    });
  }

  DZ.modal = { open: open, close: close, confirm: confirm, prompt: prompt, text: text, loading: loading, isOpen: isOpen };
})(window);
