"use strict";

// Failover'in ag tarafi: UDP duyuru yanitlayicisi, LAN gozlemi, devir HTTP ucu, replika
// cekme dongusu ve koordinator soketi denetimi. Karar vermez; kararlar failover.cjs'de.

const crypto = require("node:crypto");
const dgram = require("node:dgram");
const http = require("node:http");
const {
  DISCOVERY_PORT,
  DISCOVERY_PROTOCOL,
  DISCOVERY_QUERY,
  DISCOVERY_VERSION,
  broadcastTargets,
  normalizeDiscoveryRecord
} = require("./lan-discovery.cjs");
const { decideSnapshot, syncDirection } = require("./failover-policy.cjs");

const FAILOVER_PORT = 8094;
const MAX_BODY_BYTES = 64 * 1024 * 1024;

function digest(value, length) {
  return crypto.createHash("sha256").update(value).digest("hex").slice(0, length);
}

/** src/lan-discovery.ts `villaBridgeCoordinatorId` ile ayni turetme: adres sizdirilmaz. */
function coordinatorIdFor(serialPort) {
  const normalized = typeof serialPort === "string" ? serialPort.trim().toLowerCase() : "";
  return normalized ? digest(normalized, 16) : null;
}

function createDiscoveryRecord(fields) {
  return {
    protocol: DISCOVERY_PROTOCOL,
    version: DISCOVERY_VERSION,
    role: fields.role,
    mode: "direct",
    dashboardPort: fields.dashboardPort,
    nodeId: fields.nodeId,
    state: fields.state,
    epoch: fields.epoch,
    coordinatorId: fields.coordinatorId,
    priority: fields.priority,
    failoverPort: fields.failoverPort,
    sentAt: Date.now()
  };
}

/** UDP 8093 yanitlayicisi; kayit her yanitta guncel durumdan uretilir. */
function startDiscoveryResponder(getRecord, port = DISCOVERY_PORT) {
  return new Promise((resolve, reject) => {
    const socket = dgram.createSocket({ type: "udp4", reuseAddr: true });
    socket.on("message", (message, remote) => {
      if (message.toString("utf8") !== DISCOVERY_QUERY) return;
      try {
        socket.send(Buffer.from(JSON.stringify(getRecord())), remote.port, remote.address);
      } catch {
        // Yanit gonderilemezse sorgulayan bir sonraki turda yeniden sorar.
      }
    });
    const onError = (error) => reject(error);
    socket.once("error", onError);
    socket.bind(port, "0.0.0.0", () => {
      socket.off("error", onError);
      socket.on("error", () => undefined);
      resolve({ close: () => new Promise((done) => socket.close(() => done())) });
    });
  });
}

/** Tek tur LAN gozlemi: pencere boyunca gelen TUM yabanci kayitlar (adresiyle). */
function queryPeers(options = {}) {
  const timeoutMs = Math.max(100, Number(options.timeoutMs) || 1200);
  const selfNodeId = options.selfNodeId || null;
  const targets = options.address ? [options.address] : (options.targets || broadcastTargets());
  const port = options.port || DISCOVERY_PORT;
  return new Promise((resolve) => {
    const socket = dgram.createSocket("udp4");
    const found = new Map();
    let finished = false;
    const done = () => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      try {
        socket.close();
      } catch {
        // Bind tamamlanmadan kapanabilir.
      }
      resolve([...found.values()]);
    };
    const timer = setTimeout(done, timeoutMs);
    timer.unref?.();
    socket.on("error", done);
    socket.on("message", (message, remote) => {
      const record = normalizeDiscoveryRecord(message);
      if (!record || (selfNodeId && record.nodeId === selfNodeId)) return;
      found.set(record.nodeId || remote.address, { ...record, address: remote.address });
    });
    socket.bind(0, "0.0.0.0", () => {
      try {
        socket.setBroadcast(true);
        for (const target of targets) socket.send(Buffer.from(DISCOVERY_QUERY), port, target);
      } catch {
        done();
      }
    });
  });
}

function requestJson(options) {
  const body = options.body === undefined ? null : Buffer.from(JSON.stringify(options.body));
  return new Promise((resolve, reject) => {
    const request = http.request({
      host: options.address,
      port: options.port,
      path: options.path,
      method: options.method || "GET",
      timeout: options.timeoutMs || 30000,
      headers: {
        accept: "application/json",
        ...(options.token ? { authorization: `Bearer ${options.token}` } : {}),
        ...(body ? { "content-type": "application/json", "content-length": body.length } : {})
      }
    }, (response) => {
      const chunks = [];
      let size = 0;
      response.on("data", (chunk) => {
        size += chunk.length;
        if (size > MAX_BODY_BYTES) {
          request.destroy(new Error("Response is too large."));
          return;
        }
        chunks.push(chunk);
      });
      response.on("end", () => {
        try {
          const value = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
          resolve({ status: response.statusCode, body: value });
        } catch (error) {
          reject(error);
        }
      });
      response.on("error", reject);
    });
    request.once("timeout", () => request.destroy(new Error("Request timed out.")));
    request.once("error", reject);
    if (body) request.write(body);
    request.end();
  });
}

function safeEqual(actual, expected) {
  const a = Buffer.from(String(actual || ""));
  const b = Buffer.from(String(expected || ""));
  return a.length === b.length && a.length > 0 && crypto.timingSafeEqual(a, b);
}

async function readBody(request, limit) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > limit) throw new Error("Request is too large.");
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
}

function sendJson(response, status, value) {
  response.writeHead(status, { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" });
  response.end(JSON.stringify(value));
}

/**
 * Devir HTTP ucu (LAN'a acik, varsayilan 8094). Kimlik dogrulamasi ag anahtarindan turetilen
 * jetonla; jeton yoksa (ag anahtari bilinmiyor) korumali uclar kapali.
 *   GET  /api/discovery          — duyuru kaydi (HTTP izleme kanali)
 *   GET  /api/failover/status    — sir icermeyen ozet
 *   GET  /api/failover/manifest  — sahibin veri ozeti (jeton)
 *   GET  /api/failover/snapshot  — sahibin snapshot'i (jeton)
 *   POST /api/failover/release   — oncelikli dugumun release istegi (jeton)
 */
function createFailoverHttpServer(options) {
  return http.createServer(async (request, response) => {
    try {
      const url = new URL(request.url || "/", "http://localhost");
      if (request.method === "GET" && url.pathname === "/api/discovery") {
        sendJson(response, 200, options.getRecord());
        return;
      }
      if (request.method === "GET" && url.pathname === "/api/failover/status") {
        sendJson(response, 200, options.publicStatus());
        return;
      }
      const token = options.token();
      const bearer = /^Bearer (.+)$/.exec(request.headers.authorization || "")?.[1] || "";
      if (!token || !safeEqual(bearer, token)) {
        sendJson(response, 403, { ok: false, error: "Failover access was denied." });
        return;
      }
      if (request.method === "GET" && url.pathname === "/api/failover/manifest") {
        const manifest = options.manifest();
        sendJson(response, manifest ? 200 : 409, manifest || { ok: false, error: "This node is not the owner." });
        return;
      }
      if (request.method === "GET" && url.pathname === "/api/failover/snapshot") {
        const snapshot = await options.snapshot();
        sendJson(response, snapshot ? 200 : 409, snapshot || { ok: false, error: "This node is not the owner." });
        return;
      }
      if (request.method === "POST" && url.pathname === "/api/failover/release") {
        const body = await readBody(request, 16 * 1024);
        const requester = {
          nodeId: typeof body.nodeId === "string" ? body.nodeId.slice(0, 64) : null,
          priority: Number.isInteger(body.priority) ? body.priority : 1,
          coordinatorId: typeof body.coordinatorId === "string" ? body.coordinatorId : null,
          epoch: Number.isInteger(body.epoch) ? body.epoch : 0,
          state: "standby",
          dashboardPort: Number.isInteger(body.dashboardPort) ? body.dashboardPort : null,
          failoverPort: Number.isInteger(body.failoverPort) ? body.failoverPort : FAILOVER_PORT,
          address: String(request.socket.remoteAddress || "").replace(/^::ffff:/, "")
        };
        const result = await options.release(requester);
        sendJson(response, result.status || (result.ok ? 200 : 409), result);
        return;
      }
      sendJson(response, 404, { ok: false, error: "Not found." });
    } catch (error) {
      sendJson(response, 400, { ok: false, error: error.message });
    }
  });
}

/**
 * Koordinator TCP soketi bu surecte acik mi? Cekirdek ayni surecte calistigi icin aktif
 * tutamaclardan okunur (Android'de /proc/net/tcp uygulamaya kapali). Seri port (USB) icin
 * olculemez: `null` doner ve isletim sistemi kilidine (exclusive-os) guvenilir.
 */
function coordinatorConnectionOpen(serialPort) {
  const match = /^tcp:\/\/\[?([^\]/]+?)\]?:(\d+)$/i.exec(String(serialPort || "").trim());
  if (!match) return null;
  const host = match[1].toLowerCase();
  const port = Number(match[2]);
  const isIp = /^[\d.]+$/.test(host) || host.includes(":");
  const handles = typeof process._getActiveHandles === "function" ? process._getActiveHandles() : [];
  return handles.some((handle) =>
    handle &&
    typeof handle.remotePort === "number" &&
    handle.remotePort === port &&
    !handle.destroyed &&
    (!isIp || String(handle.remoteAddress || "").replace(/^::ffff:/, "").toLowerCase() === host)
  );
}

/**
 * Faz 6 cekme dongusu: sahip olmayan dugum, karsisinda sahip gorurse her `intervalMs`'de
 * (varsayilan 60 sn) ucuz manifesti sorar; ozet degistiyse ya da replika `fullIntervalMs`'den
 * (varsayilan 24 saat) eskiyse tam snapshot'i ceker. Kabul karari epoch fencing'dendir.
 */
function createReplicaSync(options) {
  const now = options.now || Date.now;
  const intervalMs = options.intervalMs || 60000;
  const fullIntervalMs = options.fullIntervalMs || 24 * 3600 * 1000;
  const log = options.log || ((message) => console.log(message));
  const state = {
    lastCheckAt: null,
    lastSyncAt: null,
    lastResult: null,
    error: null,
    halted: false,
    replica: options.readReplicaMeta()
  };
  let timer = null;
  let busy = false;

  const status = () => ({
    lastCheckAt: state.lastCheckAt,
    lastSyncAt: state.lastSyncAt,
    lastResult: state.lastResult,
    error: state.error,
    halted: state.halted,
    replica: state.replica
      ? {
        epoch: state.replica.epoch,
        ownerNodeId: state.replica.ownerNodeId || null,
        createdAt: state.replica.createdAt,
        receivedAt: state.replica.receivedAt || null,
        files: Array.isArray(state.replica.files) ? state.replica.files.length : 0
      }
      : null
  });

  async function syncOnce() {
    const self = options.self();
    const peer = options.peer();
    if (syncDirection(self.state, peer?.state) !== "pull" || !peer?.address) return status();
    const token = options.token();
    if (!token) {
      state.error = "Zigbee network key is unknown; replication is disabled.";
      return status();
    }
    state.lastCheckAt = now();
    try {
      const port = peer.failoverPort || FAILOVER_PORT;
      const manifest = await options.request({ address: peer.address, port, path: "/api/failover/manifest", token, timeoutMs: 10000 });
      if (manifest.status !== 200) throw new Error(manifest.body?.error || `manifest HTTP ${manifest.status}`);
      const replica = state.replica;
      const stale = !replica || !replica.receivedAt || now() - replica.receivedAt >= fullIntervalMs;
      const sameDigest = replica && replica.epoch === manifest.body.epoch && replica.digest === manifest.body.digest;
      if (sameDigest && !stale) {
        state.lastResult = "unchanged";
        state.error = null;
        state.halted = false;
        return status();
      }
      const response = await options.request({ address: peer.address, port, path: "/api/failover/snapshot", token, timeoutMs: 60000 });
      if (response.status !== 200) throw new Error(response.body?.error || `snapshot HTTP ${response.status}`);
      const decision = decideSnapshot({
        incoming: response.body,
        localNetwork: options.localNetwork(),
        knownEpoch: options.knownEpoch(),
        replica: sameDigest ? null : replica,
        selfNodeId: self.nodeId
      });
      state.lastResult = decision.reason;
      if (decision.halt) {
        state.halted = true;
        state.error = `Zigbee network identity differs (${[...decision.network.mismatched, ...decision.network.unknown].join(", ")}); replication stopped, nothing was overwritten.`;
        log(`Failover replication halted: ${state.error}`);
        return status();
      }
      state.halted = false;
      if (decision.accept || (sameDigest && stale)) {
        state.replica = options.writeReplica(response.body, now());
        state.lastSyncAt = now();
        log(`Failover replica updated: epoch ${response.body.epoch}, ${response.body.files.length} files.`);
      }
      state.error = null;
    } catch (error) {
      state.error = error.message;
    }
    return status();
  }

  return {
    status,
    syncOnce,
    noteReplica(meta) {
      state.replica = meta;
    },
    start() {
      if (timer) return;
      const tick = () => {
        if (busy) return;
        busy = true;
        void syncOnce().finally(() => {
          busy = false;
        });
      };
      timer = setInterval(tick, intervalMs);
      timer.unref?.();
      setTimeout(tick, 5000).unref?.();
    },
    stop() {
      if (timer) clearInterval(timer);
      timer = null;
    }
  };
}

module.exports = {
  FAILOVER_PORT,
  coordinatorConnectionOpen,
  coordinatorIdFor,
  createDiscoveryRecord,
  createFailoverHttpServer,
  createReplicaSync,
  queryPeers,
  requestJson,
  startDiscoveryResponder
};
