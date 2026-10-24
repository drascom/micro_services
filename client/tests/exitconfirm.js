// js/exitconfirm.js headless test: root-screen Back -> "Uygulamadan çıkılsın mı?" (Hayır focused), Hayır / Back close it,
// Evet exits (Tizen tizen.application...exit(), Android DZAndroid.exit(), browser: just closes). Run: node client/tests/exitconfirm.js
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const { el, findAll } = require('./_fake_dom');

function env(o) {
  const log = { exits: [] };
  const sb = { console };
  sb.window = sb;
  sb.document = { createElement: el };
  if (o.tizen) sb.tizen = { application: { getCurrentApplication: () => ({ exit: () => log.exits.push('tizen') }) } };
  if (o.android) sb.DZAndroid = { exit: () => log.exits.push('android') };
  vm.createContext(sb);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../js/exitconfirm.js'), 'utf8'), sb, { filename: 'exitconfirm.js' });
  const host = el('div');
  return { log, X: sb.DZP.exitConfirm, host, btns: () => findAll(host, 'xq-b'), focused: () => findAll(host, 'xq-b').findIndex(b => /\bon\b/.test(b.className)) };
}

{
  const e = env({ tizen: true });
  e.X.open(e.host);
  assert(e.X.active());
  assert.equal(findAll(e.host, 'xq-t')[0].textContent, 'Uygulamadan çıkılsın mı?');
  assert.deepEqual(e.btns().map(b => b.textContent), ['Hayır', 'Evet']);
  assert.equal(e.focused(), 0, 'Hayır is the default');
  e.X.key('ok');
  assert(!e.X.active() && findAll(e.host, 'xq').length === 0, 'Hayır closes');
  assert.deepEqual(e.log.exits, []);
  e.X.open(e.host); e.X.key('right'); assert.equal(e.focused(), 1); e.X.key('back');
  assert(!e.X.active() && e.log.exits.length === 0, 'Back = Hayır');
  e.X.open(e.host); e.X.key('right'); e.X.key('ok');
  assert.deepEqual(e.log.exits, ['tizen'], 'Evet exits the Tizen app');
}
{
  const e = env({ android: true });
  e.X.open(e.host); e.X.key('right'); e.X.key('ok');
  assert.deepEqual(e.log.exits, ['android'], 'Evet -> DZAndroid.exit()');
}
{
  const e = env({});
  e.X.open(e.host); e.X.key('right'); e.X.key('ok');
  assert(!e.X.active(), 'browser: Evet only closes');
}
console.log('client exitconfirm: ok');
