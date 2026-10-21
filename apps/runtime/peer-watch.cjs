"use strict";

// Three-channel peer watching (docs/tablet-failover-plani.md §4.2).
// Observation only: when the threshold is reached the watcher raises `readyToTakeOver` and logs.
// It never takes over, claims, releases or switches modes — that is a later phase.

const dgram = require("node:dgram");
const http = require("node:http");
const net = require("node:net");
const { DISCOVERY_PORT, DISCOVERY_QUERY, normalizeDiscoveryRecord } = require("./lan-discovery.cjs");

const THRESHOLDS = {
  beaconIntervalMs: 2000,
  beaconStaleMs: 15000,
  pollIntervalMs: 10000,
  probeTimeoutMs: 2000,
  takeoverFailures: 9,
  recoverySuccesses: 3,
  claimJitterMaxMs: 15000
};

function downChannels(probe) {
  const down = [];
  if (!probe.beacon) down.push("beacon");
  if (!probe.http) down.push("http");
  if (!probe.mqtt) down.push("mqtt");
  return down;
}

function createPeerWatchState(options = {}) {
  const thresholds = { ...THRESHOLDS, ...(options.thresholds || {}) };
  const now = typeof options.now === "function" ? options.now : Date.now;
  const log = typeof options.logger === "function" ? options.logger : (message) => console.log(message);
  let peer = null;
  let lastProbeAt = null;
  let lastSeenAt = null;
  let down = [];
  let consecutiveFailures = 0;
  let consecutiveSuccesses = 0;
  let readyToTakeOver = false;

  const status = () => ({
    watching: lastProbeAt !== null,
    peer: peer === null ? null : { ...peer },
    lastProbeAt,
    lastSeenAt,
    lastSeenAgeMs: lastSeenAt === null ? null : Math.max(0, now() - lastSeenAt),
    downChannels: [...down],
    consecutiveFailures,
    consecutiveSuccesses,
    failuresToTakeover: Math.max(0, thresholds.takeoverFailures - consecutiveFailures),
    readyToTakeOver,
    action: "none",
    thresholds: { ...thresholds }
  });

  const record = (probe) => {
    const at = now();
    lastProbeAt = at;
    down = downChannels(probe);
    if (probe.nodeId || probe.address) {
      peer = {
        nodeId: probe.nodeId || (peer && peer.nodeId) || null,
        address: probe.address || (peer && peer.address) || null,
        state: probe.state || null,
        mode: probe.mode || (peer && peer.mode) || null
      };
    }
    // A poll fails only when all three channels fail (principle 3, §4.2).
    if (down.length < 3) {
      lastSeenAt = at;
      consecutiveFailures = 0;
      consecutiveSuccesses += 1;
      if (readyToTakeOver && consecutiveSuccesses >= thresholds.recoverySuccesses) {
        readyToTakeOver = false;
        log("Peer node is visible again; the take-over-ready flag was cleared.");
      }
      return status();
    }
    consecutiveSuccesses = 0;
    // A peer that was never seen cannot be declared missing.
    if (peer === null) return status();
    consecutiveFailures += 1;
    if (!readyToTakeOver && consecutiveFailures >= thresholds.takeoverFailures) {
      readyToTakeOver = true;
      const seconds = Math.round((thresholds.takeoverFailures * thresholds.pollIntervalMs) / 1000);
      log(
        `Peer node was unreachable on all three channels for ${seconds}s; ready to take over ` +
        "(phase 2: nothing is taken over, this is reported only)."
      );
    }
    return status();
  };

  return { record, status };
}

function startPeerWatcher(options) {
  const thresholds = { ...THRESHOLDS, ...(options.thresholds || {}) };
  const state = createPeerWatchState({ ...options, thresholds });
  const intervalMs = Math.max(50, Number(options.intervalMs) || thresholds.pollIntervalMs);
  const log = typeof options.logger === "function" ? options.logger : (message) => console.log(message);
  let timer = null;
  let running = false;
  let busy = false;

  const probeOnce = async () => {
    let status;
    try {
      status = state.record(await options.probe());
    } catch (error) {
      log(`Peer probe failed: ${error.message}`);
      status = state.record({ beacon: false, http: false, mqtt: false });
    }
    if (typeof options.onStatus === "function") options.onStatus(status);
    return status;
  };

  const tick = () => {
    if (busy) return;
    busy = true;
    void probeOnce().finally(() => {
      busy = false;
    });
  };

  const watcher = {
    start: () => {
      if (running) return;
      running = true;
      tick();
      timer = setInterval(tick, intervalMs);
      timer.unref();
    },
    stop: () => {
      running = false;
      if (timer) clearInterval(timer);
      timer = null;
    },
    status: () => state.status(),
    probeOnce
  };
  if (options.autoStart !== false) watcher.start();
  return watcher;
}

// Channel 1: the peer answers a unicast discovery query.
function probeBeacon(address, timeoutMs, discoveryPort = DISCOVERY_PORT) {
  return new Promise((resolve) => {
    const socket = dgram.createSocket("udp4");
    let finished = false;
    const done = (result) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      try {
        socket.close();
      } catch {
        // A timeout can fire before bind completes.
      }
      resolve(result);
    };
    const timer = setTimeout(() => done(null), timeoutMs);
    timer.unref();
    socket.on("error", () => done(null));
    // Any role: the server watches the tablet as well as the other way round (failover phase 5).
    socket.on("message", (message) => done(normalizeDiscoveryRecord(message)));
    socket.bind(0, "0.0.0.0", () => {
      try {
        socket.send(Buffer.from(DISCOVERY_QUERY), discoveryPort, address);
      } catch {
        done(null);
      }
    });
  });
}

// Channel 2: the peer serves a valid /api/discovery record.
function probeHttpDiscovery(address, port, timeoutMs) {
  return new Promise((resolve) => {
    const request = http.get({ host: address, port, path: "/api/discovery", timeout: timeoutMs }, (response) => {
      const chunks = [];
      let size = 0;
      response.on("data", (chunk) => {
        size += chunk.length;
        if (size > 8 * 1024) {
          response.destroy();
          resolve(false);
          return;
        }
        chunks.push(chunk);
      });
      response.on("end", () => {
        resolve(response.statusCode === 200 && normalizeDiscoveryRecord(Buffer.concat(chunks)) !== null);
      });
      response.on("error", () => resolve(false));
    });
    request.once("timeout", () => {
      request.destroy();
      resolve(false);
    });
    request.once("error", () => resolve(false));
  });
}

// Channel 3: the peer's MQTT broker accepts a TCP connection.
function probeTcp(address, port, timeoutMs) {
  return new Promise((resolve) => {
    const socket = net.createConnection({ host: address, port });
    let finished = false;
    const done = (result) => {
      if (finished) return;
      finished = true;
      socket.destroy();
      resolve(result);
    };
    socket.setTimeout(timeoutMs);
    socket.once("connect", () => done(true));
    socket.once("timeout", () => done(false));
    socket.once("error", () => done(false));
  });
}

function createPeerProbe(options) {
  const thresholds = { ...THRESHOLDS, ...(options.thresholds || {}) };
  const now = typeof options.now === "function" ? options.now : Date.now;
  const beacon = options.probeBeacon || probeBeacon;
  const httpProbe = options.probeHttp || probeHttpDiscovery;
  const tcpProbe = options.probeMqtt || probeTcp;
  const address = options.address;
  const dashboardPort = options.dashboardPort;
  const mqttPort = Number(options.mqttPort) || 1883;
  let lastBeaconAt = null;
  let nodeId = options.nodeId || null;
  let mode = options.mode || null;
  let state = options.state || null;

  return async () => {
    const record = await beacon(address, thresholds.probeTimeoutMs, options.discoveryPort);
    if (record) {
      lastBeaconAt = now();
      nodeId = record.nodeId || nodeId;
      mode = record.mode || mode;
      state = record.state || null;
    }
    // The beacon channel tolerates lost announcements until the staleness window closes (§3.2).
    const beaconUp = lastBeaconAt !== null && now() - lastBeaconAt <= thresholds.beaconStaleMs;
    const [httpUp, mqttUp] = await Promise.all([
      httpProbe(address, dashboardPort, thresholds.probeTimeoutMs),
      tcpProbe(address, mqttPort, thresholds.probeTimeoutMs)
    ]);
    return {
      beacon: beaconUp,
      http: httpUp,
      mqtt: mqttUp,
      nodeId,
      address,
      state,
      mode,
      // `true` only when this very poll got a fresh record; `state` may otherwise be cached.
      fresh: Boolean(record),
      epoch: record && Number.isInteger(record.epoch) ? record.epoch : null
    };
  };
}

module.exports = {
  THRESHOLDS,
  createPeerProbe,
  createPeerWatchState,
  probeBeacon,
  probeHttpDiscovery,
  probeTcp,
  startPeerWatcher
};
