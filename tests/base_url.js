/* api.js baseUrl() order: localStorage dz.baseUrl > config.js DZ_CONFIG.baseUrl > page served by the server itself (port 8090) > '' (first-run screen). */
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

const file = { protocol: 'file:', hostname: '', port: '', search: '' };
const web = { protocol: 'http:', hostname: 'tv.lan', port: '8091', search: '' };
const self = { protocol: 'http:', hostname: 'srv.lan', port: '8090', search: '' };
const tls = { protocol: 'https:', hostname: 'dz.example.com', host: 'dz.example.com', port: '', search: '' };
// no built-in default address: wgt / file:// and a page served elsewhere have NO address -> first-run "Sunucu adresi" screen
assert.equal(api({ location: file }).api.baseUrl(), '', 'wgt / file://: no default address');
assert.equal(api({ location: file }).api.hasBase(), false);
assert.equal(api({ location: web }).api.baseUrl(), '', 'browser on another port: no automatic address');
assert.equal(api({ location: web }).api.suggestBase(), 'http://tv.lan:8090', 'first-run suggestion: same host, server port');
assert.equal(api({ location: tls }).api.suggestBase(), 'https://dz.example.com', 'https page: same origin suggested');
assert.equal(api({ location: self }).api.baseUrl(), 'http://srv.lan:8090', 'page served by the server itself (8090): its origin');
assert.equal(api({ location: self }).api.hasBase(), true);
assert.equal(api({ location: { protocol: 'http:', hostname: 'srv.lan', port: '9000', search: '' }, config: { port: 9000 } }).api.baseUrl(), 'http://srv.lan:9000', 'DZ_CONFIG.port');
assert.equal(api({ location: web, config: { baseUrl: '10.0.0.5:8090/' } }).api.baseUrl(), 'http://10.0.0.5:8090', 'DZ_CONFIG.baseUrl');
const user = api({ location: web, config: { baseUrl: 'http://cfg:1' }, store: { 'dz.baseUrl': 'http://mine:8090' } });
assert.equal(user.api.baseUrl(), 'http://mine:8090', 'the user setting wins');
assert.equal(user.api.setBaseUrl(''), 'http://cfg:1', 'an empty setting falls back to the automatic address');
assert.ok(!('dz.baseUrl' in user.mem));
assert.equal(user.api.defaultBase(), 'http://cfg:1');
// typed address normalisation (first-run screen + Ayarlar)
const n = api({ location: file }).api.normalizeServer;
assert.equal(n('192.168.1.10'), 'http://192.168.1.10:8090', 'bare IP -> http + :8090');
assert.equal(n(' tv.lan/ '), 'http://tv.lan:8090');
assert.equal(n('192.168.1.10:9000'), 'http://192.168.1.10:9000', 'explicit port kept');
assert.equal(n('https://dz.example.com/'), 'https://dz.example.com', 'https URL as is');
assert.equal(n('http://host'), 'http://host', 'explicit scheme: as is');
assert.equal(n(''), '');
const fresh = api({ location: file });
assert.equal(fresh.api.setBaseUrl('192.168.1.10'), 'http://192.168.1.10:8090');
assert.equal(fresh.mem['dz.baseUrl'], 'http://192.168.1.10:8090');
console.log('Base URL order (localStorage > config.js > page served by the server > none -> first-run screen): OK');
