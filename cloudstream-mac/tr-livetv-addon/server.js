'use strict';
// Ulusal TV (TR) — bagimsiz Stremio addon sunucusu (sadece node stdlib)
const http = require('http');
const https = require('https');
const { URL } = require('url');

const PORT = parseInt(process.env.PORT || '7788', 10);
const HOST = '0.0.0.0';
const M3U_URL = 'https://iptv-org.github.io/iptv/countries/tr.m3u';
const REFRESH_MS = 6 * 60 * 60 * 1000;

let CHANNELS = [];      // son iyi kopya
let GENRES = [];
let lastFetch = 0;

function log() {
  const a = Array.prototype.slice.call(arguments);
  console.log('[' + new Date().toISOString() + '] ' + a.join(' '));
}

function fetchUrl(url, redirects) {
  redirects = redirects || 0;
  return new Promise((resolve, reject) => {
    if (redirects > 5) return reject(new Error('too many redirects'));
    const mod = url.startsWith('http:') ? http : https;
    const req = mod.get(url, { headers: { 'User-Agent': 'trlivetv/1.0' } }, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        res.resume();
        return resolve(fetchUrl(new URL(res.headers.location, url).toString(), redirects + 1));
      }
      if (res.statusCode !== 200) {
        res.resume();
        return reject(new Error('HTTP ' + res.statusCode));
      }
      let data = '';
      res.setEncoding('utf8');
      res.on('data', (c) => { data += c; });
      res.on('end', () => resolve(data));
    });
    req.setTimeout(30000, () => { req.destroy(new Error('timeout')); });
    req.on('error', reject);
  });
}

function slugify(s) {
  return s.toLowerCase()
    .replace(/ğ/g, 'g').replace(/ü/g, 'u').replace(/ş/g, 's')
    .replace(/ı/g, 'i').replace(/ö/g, 'o').replace(/ç/g, 'c')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '') || 'kanal';
}

function attr(line, name) {
  const m = line.match(new RegExp(name + '="([^"]*)"'));
  return m ? m[1] : '';
}

function parseM3U(text) {
  const lines = text.split(/\r?\n/);
  const out = [];
  const used = Object.create(null);
  let cur = null;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (line.startsWith('#EXTINF')) {
      const ci = line.indexOf(',');
      const name = (ci >= 0 ? line.slice(ci + 1) : '').trim();
      cur = {
        name: name || 'Kanal',
        logo: attr(line, 'tvg-logo'),
        group: attr(line, 'group-title') || 'Genel',
        tvgId: attr(line, 'tvg-id')
      };
    } else if (line && !line.startsWith('#') && cur) {
      let slug = slugify(cur.name);
      if (used[slug]) { used[slug]++; slug = slug + '-' + used[slug]; } else { used[slug] = 1; }
      out.push({
        id: 'trtv:' + slug,
        name: cur.name,
        logo: cur.logo,
        group: cur.group,
        url: line
      });
      cur = null;
    }
  }
  return out;
}

async function refresh() {
  try {
    const txt = await fetchUrl(M3U_URL);
    const ch = parseM3U(txt);
    if (!ch.length) throw new Error('bos liste');
    CHANNELS = ch;
    const g = {};
    ch.forEach((c) => { g[c.group] = 1; });
    GENRES = Object.keys(g).sort();
    lastFetch = Date.now();
    log('M3U yenilendi: ' + CHANNELS.length + ' kanal, ' + GENRES.length + ' grup');
  } catch (e) {
    log('M3U cekme hatasi: ' + e.message + ' (son iyi kopya: ' + CHANNELS.length + ' kanal)');
  }
}

function toMeta(c) {
  return {
    id: c.id,
    type: 'tv',
    name: c.name,
    poster: c.logo,
    posterShape: 'landscape',
    logo: c.logo,
    background: c.logo,
    description: c.group,
    genres: [c.group]
  };
}

function manifest() {
  return {
    id: 'com.local.trlivetv',
    version: '1.0.0',
    name: 'Ulusal TV (TR)',
    description: 'iptv-org Türkiye listesi — yayıncıların herkese açık HLS akışları',
    types: ['tv'],
    resources: ['catalog', 'meta', 'stream'],
    idPrefixes: ['trtv:'],
    catalogs: [{
      type: 'tv',
      id: 'tr-live',
      name: 'Ulusal TV',
      extra: [{ name: 'search', isRequired: false }, { name: 'genre', isRequired: false }],
      genres: GENRES
    }]
  };
}

function parseExtra(str) {
  const o = {};
  if (!str) return o;
  str.split('&').forEach((p) => {
    const i = p.indexOf('=');
    if (i > 0) {
      try { o[decodeURIComponent(p.slice(0, i))] = decodeURIComponent(p.slice(i + 1)); }
      catch (e) { o[p.slice(0, i)] = p.slice(i + 1); }
    }
  });
  return o;
}

function send(res, obj, code) {
  const body = JSON.stringify(obj);
  res.writeHead(code || 200, {
    'Content-Type': 'application/json; charset=utf-8',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Cache-Control': 'no-cache',
    'Content-Length': Buffer.byteLength(body)
  });
  res.end(body);
}

function findById(rawId) {
  let id = rawId;
  try { id = decodeURIComponent(rawId); } catch (e) {}
  let c = CHANNELS.find((x) => x.id === id);
  if (!c) c = CHANNELS.find((x) => x.id === rawId);
  if (!c && !id.startsWith('trtv:')) c = CHANNELS.find((x) => x.id === 'trtv:' + id);
  return c;
}

const server = http.createServer((req, res) => {
  let pathname;
  try { pathname = decodeURI(req.url.split('?')[0]); } catch (e) { pathname = req.url.split('?')[0]; }
  log(req.method + ' ' + req.url + ' <- ' + (req.socket.remoteAddress || '?') + ' UA=' + (req.headers['user-agent'] || '-'));

  if (req.method === 'OPTIONS') {
    res.writeHead(204, {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Headers': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS'
    });
    return res.end();
  }

  if (pathname === '/' || pathname === '/manifest.json') {
    return send(res, manifest());
  }

  // /catalog/tv/tr-live.json  |  /catalog/tv/tr-live/<extra>.json
  let m = pathname.match(/^\/catalog\/([^/]+)\/([^/]+?)(?:\/(.+?))?\.json$/);
  if (m) {
    const type = m[1], cid = m[2], extraStr = m[3] || '';
    if (type !== 'tv' || cid !== 'tr-live') return send(res, { metas: [] });
    const extra = parseExtra(extraStr);
    let list = CHANNELS;
    if (extra.genre) list = list.filter((c) => c.group === extra.genre);
    if (extra.search) {
      const q = extra.search.toLowerCase();
      list = list.filter((c) => c.name.toLowerCase().indexOf(q) >= 0);
    }
    const skip = parseInt(extra.skip || '0', 10) || 0;
    if (skip > 0) list = list.slice(skip);
    return send(res, { metas: list.map(toMeta) });
  }

  m = pathname.match(/^\/meta\/([^/]+)\/(.+)\.json$/);
  if (m) {
    const c = findById(m[2]);
    if (!c) return send(res, { meta: {} }, 404);
    const meta = toMeta(c);
    meta.videos = [];
    return send(res, { meta: meta });
  }

  m = pathname.match(/^\/stream\/([^/]+)\/(.+)\.json$/);
  if (m) {
    const c = findById(m[2]);
    if (!c) return send(res, { streams: [] }, 404);
    return send(res, {
      streams: [{
        url: c.url,
        name: 'Canlı',
        title: c.name,
        behaviorHints: { notWebReady: true }
      }]
    });
  }

  if (pathname === '/health') {
    return send(res, { ok: true, channels: CHANNELS.length, genres: GENRES.length, lastFetch: lastFetch });
  }

  send(res, { err: 'not found', path: pathname }, 404);
});

refresh().then(() => {
  server.listen(PORT, HOST, () => {
    log('Ulusal TV (TR) addon http://' + HOST + ':' + PORT + '/manifest.json (emulator: http://10.0.2.2:' + PORT + '/manifest.json)');
  });
});
setInterval(refresh, REFRESH_MS);
