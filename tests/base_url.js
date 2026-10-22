/* api.js baseUrl() order: localStorage dz.baseUrl > config.js DZ_CONFIG.baseUrl > http(s) page host + port > DEFAULT_BASE (file:// / wgt). */
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const SRC = fs.readFileSync(path.join(__dirname, '..', 'tizen-client', 'js', 'api.js'), 'utf8');

function api(opts) {
  const mem = Object.assign({}, opts.store || {});
  const g = {
    location: opts.location, DZ_CONFIG: opts.config,
    localStorage: { getItem: k => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: k => { delete mem[k]; } },
    console: { log() {} }, setTimeout, clearTimeout,
  };
  g.window = g;
  vm.createContext(g);
  vm.runInContext(SRC, g);
  return { api: g.DZ.api, mem };
}

const file = { protocol: 'file:', hostname: '', search: '' };
const web = { protocol: 'http:', hostname: 'tv.lan', search: '' };
assert.equal(api({ location: file }).api.baseUrl(), 'http://192.168.0.61:8090', 'wgt / file://: DEFAULT_BASE');
assert.equal(api({ location: web }).api.baseUrl(), 'http://tv.lan:8090', 'browser: same host, port 8090');
assert.equal(api({ location: web, config: { port: 9000 } }).api.baseUrl(), 'http://tv.lan:9000', 'DZ_CONFIG.port');
assert.equal(api({ location: web, config: { baseUrl: '10.0.0.5:8090/' } }).api.baseUrl(), 'http://10.0.0.5:8090', 'DZ_CONFIG.baseUrl');
const user = api({ location: web, config: { baseUrl: 'http://cfg:1' }, store: { 'dz.baseUrl': 'http://mine:8090' } });
assert.equal(user.api.baseUrl(), 'http://mine:8090', 'the user setting wins');
assert.equal(user.api.setBaseUrl(''), 'http://cfg:1', 'an empty setting falls back to the automatic address');
assert.ok(!('dz.baseUrl' in user.mem));
assert.equal(user.api.defaultBase(), 'http://cfg:1');
console.log('Base URL order (localStorage > config.js > page host:port > DEFAULT_BASE): OK');
