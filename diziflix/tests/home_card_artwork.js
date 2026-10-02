const fs = require('fs');
const path = require('path');
const vm = require('vm');

function element(tag) {
  const listeners = {};
  const node = {
    tagName: tag.toUpperCase(), className: '', children: [], parentNode: null,
    attributes: {}, style: {}, firstChild: null,
    classList: {
      add(name) { if (!node.className.split(/\s+/).includes(name)) node.className += ' ' + name; },
      remove(name) { node.className = node.className.split(/\s+/).filter(x => x && x !== name).join(' '); },
      contains(name) { return node.className.split(/\s+/).includes(name); }
    },
    setAttribute(k, v) { this.attributes[k] = String(v); },
    getAttribute(k) { return this.attributes[k] || null; },
    removeAttribute(k) { delete this.attributes[k]; },
    appendChild(child) { child.parentNode = this; this.children.push(child); this.firstChild = this.children[0] || null; return child; },
    insertBefore(child, before) {
      child.parentNode = this;
      const at = this.children.indexOf(before);
      if (at < 0) this.children.push(child); else this.children.splice(at, 0, child);
      this.firstChild = this.children[0] || null; return child;
    },
    removeChild(child) { this.children = this.children.filter(x => x !== child); child.parentNode = null; this.firstChild = this.children[0] || null; },
    querySelectorAll(selector) { return selector === 'img' ? this.children.filter(x => x.tagName === 'IMG') : []; },
    addEventListener(type, fn) { listeners[type] = fn; },
    click() { if (listeners.click) listeners.click(); }
  };
  return node;
}

const window = { DZ: { api: { img: value => value } } };
const context = vm.createContext({ window, document: { createElement: element }, console, setTimeout });
vm.runInContext(fs.readFileSync(path.join(__dirname, '../tizen-client/js/ui/card.js'), 'utf8'), context);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../tizen-client/js/ui/row.js'), 'utf8'), context);

const item = {
  id: 'series-blackaf', title: '#blackAF', year: 2020, rating: 6.7, has_backdrop: true,
  portrait: '/img/series-blackaf/portrait?w=300&h=450',
  card: '/img/series-blackaf/card?w=342&h=192',
  backdrop: '/img/series-blackaf/backdrop?w=1280&h=720'
};
const card = window.DZ.card.create(item, { portrait: true, focusLandscape: true });
if (!/card-poster/.test(card.className) || !/card-focus-landscape/.test(card.className)) throw new Error('poster/preview class missing');
if (card.dzSrc !== item.portrait || card.dzFocusSrc !== '/img/series-blackaf/backdrop?w=640&h=360') throw new Error('artwork sources crossed');
const info = card.children.find(child => child.className === 'card-preview-info');
if (!info || info.children[0].textContent !== '#blackAF' || info.children[1].textContent !== '2020  \u2022  \u2605 6.7') throw new Error('preview metadata missing');
card.dzLoadImage();
card.dzLoadFocusImage();
const images = card.querySelectorAll('img');
if (images.length !== 2) throw new Error('expected lazy portrait + landscape images');
if (!images.some(img => img.className === 'card-primary' && img.src === item.portrait)) throw new Error('portrait missing');
if (!images.some(img => img.className === 'card-landscape' && img.src === card.dzFocusSrc && img.width === 640 && img.height === 360)) throw new Error('full-height landscape missing');
card.dzUnloadImage();
if (card.querySelectorAll('img').length || card.dzImgLoaded || card.dzFocusImgLoaded) throw new Error('artwork unload failed');

const fallback = window.DZ.card.create({ ...item, has_backdrop: false }, { portrait: true, focusLandscape: true });
if (!/card-focus-effect/.test(fallback.className) || fallback.dzFocusSrc) throw new Error('missing-backdrop effect fallback failed');

let openedCatalog = 0;
const catalogueItems = Array.from({ length: 24 }, (_, i) => ({ id: 'series-' + i, title: 'Dizi ' + i }));
const catalogueRow = window.DZ.row.create({
  id: 'series', title: 'Diziler', loaded: true, items: catalogueItems,
  portrait: true, focusLandscape: true,
  endAction: { label: 'Tüm Diziler', onSelect() { openedCatalog++; } }
});
if (catalogueRow.dzCards.length !== 21) throw new Error('home catalogue row must contain 20 posters and one end card');
const endCard = catalogueRow.dzCards[20];
if (!/all-items-card/.test(endCard.className) || endCard.getAttribute('data-col') !== '20') throw new Error('all-series end card missing');
endCard.click();
if (openedCatalog !== 1) throw new Error('all-series end card did not open the catalogue');

// The focused landscape card must expand its row tile so it cannot cover the next poster.
vm.runInContext(fs.readFileSync(path.join(__dirname, '../tizen-client/js/nav.js'), 'utf8'), context);
const strip = element('div'); strip.style = {}; strip.scrollWidth = 1200;
const viewport = element('div'); viewport.clientWidth = 1000; strip.parentNode = viewport;
const tile1 = element('div'); tile1.className = 'row-tile poster'; tile1.offsetWidth = 640; tile1.offsetLeft = 60;
tile1.setAttribute('data-base-width', '240');
const tile2 = element('div'); tile2.className = 'row-tile poster'; tile2.offsetWidth = 640; tile2.offsetLeft = 312;
tile2.setAttribute('data-base-width', '240');
const tile3 = element('div'); tile3.className = 'row-tile poster'; tile3.offsetWidth = 640; tile3.offsetLeft = 564;
tile3.setAttribute('data-base-width', '240');
const navCard1 = element('div'); navCard1.className = 'card card-poster card-focus-landscape'; navCard1.offsetWidth = 640;
const navCard2 = element('div'); navCard2.className = 'card card-poster card-focus-effect'; navCard2.offsetWidth = 270;
const navCard3 = element('div'); navCard3.className = 'card card-poster card-focus-landscape'; navCard3.offsetWidth = 640;
navCard1.setAttribute('data-item-id', 'movie-1');
navCard2.setAttribute('data-item-id', 'movie-2');
navCard3.setAttribute('data-item-id', 'movie-3');
tile1.appendChild(navCard1); tile2.appendChild(navCard2); tile3.appendChild(navCard3);
const rowEl = element('section'); rowEl.offsetWidth = 1200;
const pageEl = element('div'); pageEl.style = {}; pageEl.offsetHeight = 1080;
rowEl.offsetTop = 900; rowEl.offsetParent = pageEl;
rowEl.getAttribute = key => key === 'data-nav-row' ? 'test-row' : null;
let navItems = [navCard1, navCard2, navCard3];
rowEl.querySelectorAll = selector => selector === '[data-nav]' ? navItems : [];
rowEl.querySelector = selector => selector === '[data-nav-strip]' ? strip : (selector === '[data-nav-viewport]' ? viewport : null);
const root = element('main'); root.clientHeight = 1080;
root.querySelectorAll = selector => {
  if (selector === '[data-nav-row]') return [rowEl];
  if (selector === '.focused') return [navCard1, navCard2, navCard3].filter(x => x.classList.contains('focused'));
  if (selector === '.focus-expanded') return [tile1, tile2, tile3].filter(x => x.classList.contains('focus-expanded'));
  if (selector === '.focus-poster') return [tile1, tile2, tile3].filter(x => x.classList.contains('focus-poster'));
  return [];
};
window.DZ.nav.setRoot(root, pageEl);
if (!tile1.classList.contains('focus-expanded')) throw new Error('first focused tile did not expand');
if (pageEl.style.transform !== 'translate3d(0,-760px,0)') throw new Error('focused row did not move to the fixed top slot');
const detailPage = element('div'); detailPage.className = 'detail'; detailPage.style = {};
rowEl.offsetParent = detailPage;
window.DZ.nav.setRoot(root, detailPage);
if (detailPage.style.transform !== 'translate3d(0,-598px,0)') throw new Error('detail content did not use its percentage-based lower focus anchor');
rowEl.offsetParent = pageEl;
window.DZ.nav.setRoot(root, pageEl);
window.DZ.nav.move('right');
if (tile1.classList.contains('focus-expanded') || !tile2.classList.contains('focus-poster')) throw new Error('tile expansion did not follow artwork type');
if (strip.style.transform !== 'translate3d(0px,0,0)') throw new Error('second item should not scroll away the first poster');
window.DZ.nav.move('right');
if (!tile3.classList.contains('focus-expanded')) throw new Error('third focused tile did not expand');
if (strip.style.transform !== 'translate3d(-252px,0,0)') throw new Error('focused item was not anchored in second visual slot');
const remembered = window.DZ.nav.snapshot();
window.DZ.nav.reset();
navItems = [navCard1, navCard3, navCard2];
window.DZ.nav.restoreNext(remembered);
window.DZ.nav.setRoot(root, pageEl);
window.DZ.nav.focusRowById('test-row', 0);
if (window.DZ.nav.current().itemKey !== 'data-item-id:movie-3' || window.DZ.nav.current().col !== 1) {
  throw new Error('focus was not restored by persistent item identity after reorder');
}
const homeCss = fs.readFileSync(path.join(__dirname, '../tizen-client/css/home.css'), 'utf8');
const tileRule = homeCss.match(/\.row-tile\{[^}]+\}/);
if (!tileRule || /transition\s*:\s*width/.test(tileRule[0])) throw new Error('row tile width must settle before scroll anchoring');
if (!/\.card\.focused:before\{[^}]*z-index:10;[^}]*border:7px solid var\(--accent\)/.test(homeCss)) throw new Error('focused artwork border overlay missing');
if (!/\.all-items-card\{[^}]*display:inline-flex/.test(homeCss)) throw new Error('all-items poster styling missing');
console.log('Home cards: artwork focus, catalogue end cards, row navigation: OK');
