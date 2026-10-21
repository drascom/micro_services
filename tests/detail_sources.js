// Detail screen: "Kaynak: <site>" labels from source_names (plain text, not focusable); nothing when the field is absent/empty (old server).
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, find, findAll } = require('./_fake_dom');

const src = fs.readFileSync(path.join(__dirname, '../tizen-client/js/screens/detail.js'), 'utf8');
const tick = async (n = 6) => { for (let i = 0; i < n; i++) await Promise.resolve(); };
const movie = extra => Object.assign({ id: 'm', type: 'movie', title: 'Film', overview: '', genres: [], backdrop: '', similar: [], seasons: [],
  availability: { state: 'ready', reason: null, has_trailer: false }, playback: 'video', in_mylist: false, progress: null }, extra || {});

async function open(data) {
  const window = { requestAnimationFrame: f => f(), Image: function () { return el('img'); }, DZ: {} };
  const DZ = window.DZ;
  DZ.nav = { setRoot() {}, onFocus() {}, focusRowById() { return true; }, focusIndex() {}, currentEl() { return null; }, refresh() {},
    restoreNext() {}, rowIds() { return []; }, current() { return null; } };
  DZ.api = { profileId: () => 'p1', detail: () => Promise.resolve(data), removeMyList: () => Promise.resolve({}), addMyList: () => Promise.resolve({}) };
  DZ.card = { sized: x => x || '', sizedTo: p => p };
  DZ.skeleton = { detail: () => el('div') };
  DZ.row = { create() { return el('section'); } };
  DZ.modal = { text() {}, open() {} };
  DZ.toast = { show() {}, errorText: e => e.message, isOffline: () => false };
  DZ.playflow = { start() {} };
  DZ.app = { go() {}, back() {} };
  vm.runInNewContext(src, { window, Image: window.Image, document: { createElement: el }, console: { log() {} }, setTimeout, clearTimeout });
  const root = el('div');
  DZ.screens.detail.enter(root, { id: data.id });
  await tick();
  return root;
}

(async () => {
  let root = await open(movie({ source_names: [{ id: 'yabancidizi', name: 'Yabancı Dizi' }, { id: 'sinemalar', name: 'Sinemalar.com' }] }));
  const box = find(root, 'detail-sources');
  assert(box, 'source row drawn');
  assert.equal(findAll(box, 'src-label')[0].textContent, 'Kaynak:');
  assert.deepEqual(findAll(box, 'src-tag').map(n => n.textContent), ['Yabancı Dizi', 'Sinemalar.com']);
  assert(!box.getAttribute('data-nav') && findAll(box, 'src-tag').every(n => !n.getAttribute('data-nav')), 'not focusable');

  root = await open(movie({ source_names: [{ id: 'ghost', name: 'ghost' }] }));
  assert.deepEqual(findAll(find(root, 'detail-sources'), 'src-tag').map(n => n.textContent), ['ghost'], 'unknown site shows its id');

  const many = 'abcdefg'.split('').map(c => ({ id: c, name: c.toUpperCase() }));
  root = await open(movie({ source_names: many }));
  assert.deepEqual(findAll(find(root, 'detail-sources'), 'src-tag').map(n => n.textContent), ['A', 'B', 'C', 'D', '+3'], 'capped at 4 + "+N"');

  assert.equal(find(await open(movie()), 'detail-sources'), null, 'old server: no field -> no row');
  assert.equal(find(await open(movie({ source_names: [] })), 'detail-sources'), null, 'empty list -> no row');
  console.log('detail source labels: OK');
})().catch(e => { console.error(e); process.exit(1); });
