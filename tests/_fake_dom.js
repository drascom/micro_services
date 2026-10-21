// Shared test helpers (no output): minimal fake DOM + fake timers for the TV client tests.
// Not a test itself; `node tests/_fake_dom.js` does nothing.

function tokens(node) { return String(node.className || '').split(/\s+/).filter(Boolean); }

function el(tag) {
  const listeners = {};
  const node = {
    tagName: String(tag).toUpperCase(), className: '', children: [], parentNode: null,
    attributes: {}, style: {}, textContent: '', id: '', _offsetTop: 0,
    // layout stand-in: the series browser block starts below a 760px hero + margin
    get offsetTop() { return tokens(node).includes('browser') ? 840 : node._offsetTop; },
    set offsetTop(v) { node._offsetTop = v; },
    classList: {
      add(name) { if (!tokens(node).includes(name)) node.className = tokens(node).concat(name).join(' '); },
      remove(name) { node.className = tokens(node).filter(x => x !== name).join(' '); },
      contains(name) { return tokens(node).includes(name); },
      toggle(name, force) {
        const has = tokens(node).includes(name);
        const want = force === undefined ? !has : !!force;
        if (want) node.classList.add(name); else node.classList.remove(name);
        return want;
      }
    },
    setAttribute(k, v) { this.attributes[k] = String(v); },
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attributes, k) ? this.attributes[k] : null; },
    removeAttribute(k) { delete this.attributes[k]; },
    appendChild(child) {
      if (child.parentNode) child.parentNode.removeChild(child);
      child.parentNode = this; this.children.push(child); return child;
    },
    insertBefore(child, ref) {
      if (child.parentNode) child.parentNode.removeChild(child);
      child.parentNode = this;
      const at = this.children.indexOf(ref);
      if (at < 0) this.children.push(child); else this.children.splice(at, 0, child);
      return child;
    },
    removeChild(child) { this.children = this.children.filter(x => x !== child); child.parentNode = null; return child; },
    get firstChild() { return this.children[0] || null; },
    addEventListener(type, fn) { listeners[type] = fn; },
    click() { if (listeners.click) listeners.click(); },
    listeners,
    querySelectorAll(selector) { const out = []; walk(this, n => { if (matches(n, selector)) out.push(n); }); return out; },
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  };
  return node;
}

function walk(root, fn) {
  for (const child of root.children || []) { fn(child); walk(child, fn); }
}

// selectors used by the client: .cls  [attr]  [attr="v"]  tag
function matches(node, selector) {
  let m;
  if ((m = /^\.([\w-]+)$/.exec(selector))) return tokens(node).includes(m[1]);
  if ((m = /^\[([\w-]+)\]$/.exec(selector))) return node.getAttribute(m[1]) !== null;
  if ((m = /^\[([\w-]+)="([^"]*)"\]$/.exec(selector))) return node.getAttribute(m[1]) === m[2];
  if (/^[a-z]+$/i.test(selector)) return node.tagName === selector.toUpperCase();
  return false;
}

function findAll(root, cls) {
  const out = [];
  walk(root, n => { if (tokens(n).includes(cls)) out.push(n); });
  return out;
}
function find(root, cls) { return tokens(root).includes(cls) ? root : (findAll(root, cls)[0] || null); }
function byAttr(root, attr, value) {
  const out = [];
  walk(root, n => { if (n.getAttribute && n.getAttribute(attr) === value) out.push(n); });
  return out[0] || null;
}

// deterministic timers: advance(ms) fires due timers in order
function timers() {
  let now = 0, seq = 0;
  const list = [];
  function add(fn, ms, repeat) { const t = { id: ++seq, at: now + Math.max(0, ms || 0), fn, repeat: repeat ? Math.max(1, ms) : 0 }; list.push(t); return t.id; }
  return {
    setTimeout: (fn, ms) => add(fn, ms, false),
    setInterval: (fn, ms) => add(fn, ms, true),
    clearTimeout: id => { const i = list.findIndex(t => t.id === id); if (i >= 0) list.splice(i, 1); },
    clearInterval: id => { const i = list.findIndex(t => t.id === id); if (i >= 0) list.splice(i, 1); },
    advance(ms) {
      const end = now + ms;
      for (;;) {
        list.sort((a, b) => a.at - b.at || a.id - b.id);
        const t = list[0];
        if (!t || t.at > end) break;
        now = t.at;
        if (t.repeat) t.at += t.repeat; else list.shift();
        t.fn();
      }
      now = end;
    },
    pending: () => list.length
  };
}

// small deterministic RNG (mulberry32)
function rng(seed) {
  let a = seed >>> 0;
  return function () {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

module.exports = { el, find, findAll, byAttr, walk, timers, rng, tokens };
