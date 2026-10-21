"use strict";

// Failover'in calisma zamanina baglanmasi: durum makinesi (failover.cjs) + ag (failover-net.cjs)
// + replikasyon (failover-sync.cjs). Hem Android hem Linux konagi ayni yolu kullanir; konak
// yalniz `--platform` verir. Yigin (broker + cekirdek + Matter) main.cjs'den islem olarak gelir.

const fs = require("node:fs");
const http = require("node:http");
const net = require("node:net");
const os = require("node:os");
const YAML = require("yaml");
const { resolveVillaBridgeNodeId } = require("./lan-discovery.cjs");
const { createPeerProbe, createPeerWatchState } = require("./peer-watch.cjs");
const { decideSnapshot, shouldApplyReplica } = require("./failover-policy.cjs");
const { STATES, createFailoverController, createStateStore } = require("./failover.cjs");
const {
  FAILOVER_PORT,
  coordinatorConnectionOpen,
  coordinatorIdFor,
  createDiscoveryRecord,
  createFailoverHttpServer,
  createReplicaSync,
  queryPeers,
  requestJson,
  startDiscoveryResponder
} = require("./failover-net.cjs");
const {
  applySnapshotToLive,
  buildSnapshot,
  failoverToken,
  publicNetworkIdentity,
  readNetworkIdentity,
  readReplica,
  readReplicaMeta,
  scopeManifest,
  writeReplica
} = require("./failover-sync.cjs");

function readSerialPort(z2mConfigPath) {
  try {
    const value = YAML.parse(fs.readFileSync(z2mConfigPath, "utf8"));
    return typeof value?.serial?.port === "string" ? value.serial.port.trim() : "";
  } catch {
    return "";
  }
}

/** Ag geçidi tahmini: /proc/net/route okunabiliyorsa oradan, yoksa kendi alt aginin .1'i. */
function gatewayCandidates() {
  const candidates = new Set();
  try {
    for (const line of fs.readFileSync("/proc/net/route", "utf8").split("\n").slice(1)) {
      const fields = line.trim().split(/\s+/);
      if (fields[1] === "00000000" && fields[2] && fields[2] !== "00000000") {
        const hex = fields[2];
        candidates.add([6, 4, 2, 0].map((index) => parseInt(hex.slice(index, index + 2), 16)).join("."));
      }
    }
  } catch {
    // Android uygulamalarina kapali olabilir.
  }
  for (const entries of Object.values(os.networkInterfaces())) {
    for (const entry of entries || []) {
      if (entry.family !== "IPv4" || entry.internal) continue;
      const parts = entry.address.split(".");
      candidates.add(`${parts[0]}.${parts[1]}.${parts[2]}.1`);
    }
  }
  return [...candidates];
}

/** Hedef yanit verdi mi? Baglanti ya da RET (ECONNREFUSED) = makine ayakta ve erisilebilir. */
function hostAnswers(host, port, timeoutMs = 1500) {
  return new Promise((resolve) => {
    const socket = net.createConnection({ host, port });
    let finished = false;
    const done = (value) => {
      if (finished) return;
      finished = true;
      socket.destroy();
      resolve(value);
    };
    socket.setTimeout(timeoutMs);
    socket.once("connect", () => done(true));
    socket.once("timeout", () => done(false));
    socket.once("error", (error) => done(error?.code === "ECONNREFUSED"));
  });
}

/**
 * Kendi ag sagligi (§4.3/2): ag gecidi (ya da TCP koordinatorun web arayuzu, port 80 —
 * koordinator soketi DEGIL) yanit veriyor mu. Tablet kendi Wi-Fi'ini kaybettiyse devralmaz.
 */
async function networkHealthy(serialPort) {
  const targets = gatewayCandidates().flatMap((host) => [[host, 53], [host, 80]]);
  const tcp = /^tcp:\/\/([^:/]+):\d+$/i.exec(serialPort || "");
  if (tcp) targets.push([tcp[1], 80]);
  for (const [host, port] of targets) {
    if (await hostAnswers(host, port)) return true;
  }
  return false;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => `&#${character.charCodeAt(0)};`);
}

/**
 * Cekirdek calismiyorken (sahip degilken) panel portunu tutan bekleme sayfasi. `/` 200 doner
 * ve durumu/uyariyi gosterir (ör. "tablet henuz birakmadi"); `/api/*` 503 doner. Boylece
 * guncelleyici ve tarayici "servis yok" yerine nedenini gorur. Cekirdek baslamadan once kapanir.
 */
function createStandbyPage(config, status) {
  let server = null;
  const render = () => {
    const current = status();
    const rows = [
      ["Durum / State", current.state],
      ["Epoch", current.epoch],
      ["Karşı düğüm / Peer", current.peer ? `${current.peer.nodeId || "?"} (${current.peer.state || "?"})` : "—"],
      ["Uyarı / Warning", current.warning || "—"],
      ["Hata / Error", current.error || "—"]
    ];
    return "<!doctype html><html lang=\"tr\"><head><meta charset=\"utf-8\">" +
      "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">" +
      "<meta http-equiv=\"refresh\" content=\"10\"><title>Villa Bridge</title>" +
      "<style>body{font-family:system-ui,sans-serif;background:#111;color:#eee;margin:2rem}" +
      "td{padding:.3rem .8rem;vertical-align:top}td:first-child{color:#aaa}</style></head><body>" +
      "<h1>Villa Bridge — koordinatör bu düğümde değil</h1>" +
      "<p>Bu düğüm Zigbee koordinatörüne bağlı değil; failover protokolü bekliyor. " +
      "Koordinatör başka düğümdeyse o bırakmadan bu düğüm asla bağlanmaz.</p><table>" +
      rows.map(([key, value]) => `<tr><td>${escapeHtml(key)}</td><td>${escapeHtml(value)}</td></tr>`).join("") +
      "</table></body></html>";
  };
  return {
    open: () => new Promise((resolve) => {
      if (server) {
        resolve();
        return;
      }
      server = http.createServer((request, response) => {
        if ((request.url || "/").startsWith("/api/")) {
          response.writeHead(503, { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" });
          response.end(JSON.stringify({ ok: false, error: "Coordinator is not on this node.", failover: status() }));
          return;
        }
        response.writeHead(200, { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" });
        response.end(render());
      });
      server.once("error", () => {
        server = null;
        resolve();
      });
      server.listen(config.corePort, config.coreHost, () => resolve());
    }),
    close: () => new Promise((resolve) => {
      if (!server) {
        resolve();
        return;
      }
      const current = server;
      server = null;
      current.close(() => resolve());
      current.closeAllConnections?.();
    })
  };
}

/**
 * options: { config, provision, runtime, stack: { start(): Promise<{ok,error}>, stop(): Promise<void> }, log }
 */
async function startFailoverRuntime(options) {
  const { config, provision, runtime } = options;
  const log = options.log || ((message) => console.log(message));
  const role = ["linux", "raspberry-pi"].includes(config.platform) ? "server" : "android";
  const priority = role === "server" ? 0 : 1;
  const nodeId = resolveVillaBridgeNodeId(role);
  const serialPort = readSerialPort(provision.z2mConfigPath);
  const coordinatorId = coordinatorIdFor(serialPort);
  const failoverPort = config.failoverPort || FAILOVER_PORT;
  const token = failoverToken(provision.z2mConfigPath);
  const localNetwork = () => publicNetworkIdentity(readNetworkIdentity(provision.z2mConfigPath));
  const store = createStateStore(config.dataDir);
  let coordinator = { status: "stopped", error: null };
  let controller = null;
  let replicaSync = null;
  const probes = new Map();
  // Sahipken karsi dugumlerin replika durumu (panelde "tablet replikasi N dk once" icin).
  let peerStatuses = [];

  const getRecord = () => {
    const self = controller ? controller.self() : { state: "standby", epoch: store.load().epoch };
    return createDiscoveryRecord({
      role,
      dashboardPort: config.corePort,
      nodeId,
      state: self.state,
      epoch: self.epoch,
      coordinatorId,
      priority,
      failoverPort
    });
  };

  const status = () => ({
    ...(controller ? controller.status() : { state: "starting" }),
    role,
    coordinatorId,
    failoverPort,
    tokenAvailable: Boolean(token),
    coordinator,
    replication: replicaSync ? replicaSync.status() : null,
    peers: peerStatuses
  });

  const publish = () => {
    runtime.failover = status();
    const current = controller?.status();
    if (current && current.state === STATES.MONITOR && current.peer?.address) {
      const peerPort = current.peer.dashboardPort || 8091;
      runtime.monitor = {
        status: "ready",
        ready: true,
        address: current.peer.address,
        dashboardUrl: `http://${current.peer.address}:${peerPort}/`,
        serverMode: "direct",
        serverNodeId: current.peer.nodeId,
        serverState: current.peer.state,
        serverEpoch: current.peer.epoch ?? 0
      };
    } else {
      runtime.monitor = null;
    }
  };

  // Cekirdek bu kancadan duyuru kaydini, epoch'u okur ve koordinator sonucunu bildirir.
  globalThis.__villaBridgeFailover = {
    record: getRecord,
    epoch: () => (controller ? controller.epoch : store.load().epoch),
    status,
    coordinatorStatus: (value, error) => {
      coordinator = { status: value, error: error || null };
      publish();
    }
  };

  const standbyPage = createStandbyPage(config, () => status());
  await standbyPage.open();

  const responder = await startDiscoveryResponder(getRecord).catch((error) => {
    log(`Failover: UDP announcer could not start: ${error.message}`);
    return null;
  });

  const peerRecordProbe = (peer) => {
    const key = `${peer?.nodeId || ""}@${peer?.address || ""}`;
    let probe = probes.get(key);
    if (!probe) {
      probe = createPeerProbe({
        address: peer.address,
        dashboardPort: peer.failoverPort || FAILOVER_PORT,
        mqttPort: config.mqttPort,
        nodeId: peer.nodeId || null,
        state: peer.state || null
      });
      probes.set(key, probe);
    }
    return probe;
  };

  controller = createFailoverController({
    nodeId,
    priority,
    coordinatorId,
    role,
    now: Date.now,
    sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms).unref?.()),
    store,
    discover: () => queryPeers({ selfNodeId: nodeId, timeoutMs: 1200 }),
    probePeer: async (peer) => {
      if (!peer?.address) return { beacon: false, http: false, mqtt: false };
      const result = await peerRecordProbe(peer)();
      return {
        beacon: result.beacon,
        http: result.http,
        mqtt: result.mqtt,
        record: result.fresh
          ? { nodeId: result.nodeId, state: result.state, address: peer.address, ...(Number.isInteger(result.epoch) ? { epoch: result.epoch } : {}) }
          : null
      };
    },
    createPeerWatch: () => createPeerWatchState({ logger: log }),
    networkHealthy: () => networkHealthy(serialPort),
    coordinatorConnectionOpen: () => coordinatorConnectionOpen(serialPort),
    startStack: async () => {
      coordinator = { status: "starting", error: null };
      await standbyPage.close();
      const result = await options.stack.start();
      if (!result.ok) return result;
      if (coordinator.status !== "ready") {
        return { ok: false, error: coordinator.error || `Coordinator status: ${coordinator.status}` };
      }
      return { ok: true };
    },
    stopStack: async () => {
      await options.stack.stop();
      coordinator = { status: "stopped", error: null };
      await standbyPage.open();
    },
    requestRelease: async (peer, self) => {
      if (!token) return { ok: false, released: false, error: "Zigbee network key is unknown; cannot authenticate a release." };
      const response = await requestJson({
        address: peer.address,
        port: peer.failoverPort || FAILOVER_PORT,
        method: "POST",
        path: "/api/failover/release",
        token,
        timeoutMs: 90000,
        body: {
          nodeId: self.nodeId,
          priority: self.priority,
          coordinatorId: self.coordinatorId,
          epoch: self.epoch,
          dashboardPort: config.corePort,
          failoverPort
        }
      });
      return response.body || { ok: false, released: false };
    },
    buildHandoverSnapshot: async ({ epoch }) =>
      buildSnapshot(config.dataDir, { epoch, nodeId, network: localNetwork(), createdAt: Date.now() }),
    acceptHandoverSnapshot: async (snapshot) => {
      const current = store.load();
      const decision = decideSnapshot({
        incoming: snapshot,
        localNetwork: localNetwork(),
        knownEpoch: current.liveEpoch ?? 0,
        replica: null,
        selfNodeId: nodeId
      });
      if (decision.halt) return { ok: false, halt: true, reason: decision.reason };
      if (!decision.accept) {
        log(`Failover: handover snapshot not applied (${decision.reason}).`);
        return { ok: true, applied: false, reason: decision.reason };
      }
      const applied = applySnapshotToLive(config.dataDir, snapshot);
      log(`Failover: handover snapshot applied (epoch ${snapshot.epoch}, ${applied.files} files, backup ${applied.backup}).`);
      return { ok: true, applied: true };
    },
    prepareLiveData: async ({ liveEpoch }) => {
      const meta = readReplicaMeta(config.dataDir);
      if (!shouldApplyReplica(meta, liveEpoch)) return { ok: true, applied: false };
      const decision = decideSnapshot({
        incoming: meta,
        localNetwork: localNetwork(),
        knownEpoch: 0,
        replica: null,
        selfNodeId: nodeId
      });
      if (decision.halt) return { ok: false, halt: true, reason: decision.reason };
      const replica = readReplica(config.dataDir);
      const applied = applySnapshotToLive(config.dataDir, replica);
      log(`Failover: replica (epoch ${meta.epoch}) copied to live data before take-over (${applied.files} files).`);
      return { ok: true, applied: true, epoch: meta.epoch };
    },
    onChange: publish,
    log
  });

  replicaSync = createReplicaSync({
    readReplicaMeta: () => readReplicaMeta(config.dataDir),
    writeReplica: (snapshot, at) => writeReplica(config.dataDir, snapshot, at),
    self: () => controller.self(),
    peer: () => {
      const current = controller.status();
      return current.peer && current.state !== STATES.OWNER
        ? { ...current.peer, failoverPort: current.peer.failoverPort || FAILOVER_PORT }
        : null;
    },
    token: () => token,
    localNetwork,
    knownEpoch: () => controller.status().knownEpoch,
    request: requestJson,
    log
  });

  const httpServer = createFailoverHttpServer({
    getRecord,
    publicStatus: () => {
      const current = status();
      return {
        nodeId,
        role,
        state: current.state,
        announced: current.announced,
        epoch: current.epoch,
        replication: current.replication
      };
    },
    token: () => token,
    manifest: () => {
      if (controller.state !== STATES.OWNER) return null;
      const manifest = scopeManifest(config.dataDir);
      return { epoch: controller.epoch, nodeId, digest: manifest.digest, network: localNetwork(), files: manifest.files.length };
    },
    snapshot: async () => (controller.state === STATES.OWNER
      ? buildSnapshot(config.dataDir, { epoch: controller.epoch, nodeId, network: localNetwork(), createdAt: Date.now() })
      : null),
    release: (requester) => controller.release(requester)
  });
  await new Promise((resolve) => {
    httpServer.once("error", (error) => {
      log(`Failover: HTTP endpoint could not start on ${failoverPort}: ${error.message}`);
      resolve();
    });
    httpServer.listen(failoverPort, "0.0.0.0", resolve);
  });

  const refreshPeerStatuses = async () => {
    if (controller.state !== STATES.OWNER) {
      peerStatuses = [];
      return;
    }
    const peers = await queryPeers({ selfNodeId: nodeId, timeoutMs: 1200 }).catch(() => []);
    const next = [];
    for (const peer of peers) {
      const answer = await requestJson({
        address: peer.address,
        port: peer.failoverPort || FAILOVER_PORT,
        path: "/api/failover/status",
        timeoutMs: 3000
      }).catch(() => null);
      next.push({
        nodeId: peer.nodeId || null,
        address: peer.address,
        state: peer.state || null,
        replication: answer?.status === 200 ? answer.body?.replication || null : null
      });
    }
    peerStatuses = next;
    publish();
  };
  const peerStatusTimer = setInterval(() => void refreshPeerStatuses(), 60000);
  peerStatusTimer.unref?.();

  publish();
  if (!token) {
    log("Failover: Zigbee network key is unknown; release and replication endpoints stay closed.");
  }
  void controller.run();
  replicaSync.start();
  log(`Failover runtime started: node ${nodeId} (${role}, priority ${priority}).`);

  return {
    controller,
    status,
    stop: async () => {
      replicaSync.stop();
      clearInterval(peerStatusTimer);
      await controller.stop();
      await new Promise((resolve) => (httpServer.listening ? httpServer.close(() => resolve()) : resolve()));
      await responder?.close();
      await standbyPage.close();
      delete globalThis.__villaBridgeFailover;
    }
  };
}

module.exports = { gatewayCandidates, networkHealthy, startFailoverRuntime };
