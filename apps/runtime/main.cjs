"use strict";

// Node.js Mobile ICU'suz: cekirdek yuklenmeden once `Intl` yoksa dar bir yedegi kurulur.
const { installIntlFallback, intlSelfCheck } = require("./intl-fallback.cjs");
const intlFallback = installIntlFallback();

const dgram = require("node:dgram");
const { timingSafeEqual } = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");
const Aedes = require("aedes");
const mqtt = require("mqtt");
const { discoverVillaBridgeServer, resolveVillaBridgeNodeId } = require("./lan-discovery.cjs");
const { createPeerProbe, startPeerWatcher } = require("./peer-watch.cjs");
const { startFailoverRuntime } = require("./failover-runtime.cjs");
const { ensureFirstRunFiles } = require("./first-run.cjs");
const {
  loadProvisioning,
  matterbridgeReady,
  startMatterbridge,
  startVillaBridgeCore
} = require("./orchestration.cjs");

const runtime = {
  startedAt: new Date().toISOString(),
  monitor: null,
  mqtt: { listening: false, selfTest: false, error: null },
  core: { status: "unprovisioned", ready: false, error: null },
  matter: { status: "unprovisioned", ready: false, error: null },
  multicast: { available: false, error: null },
  // Three-channel peer watching (phase 2): observation only, never a take-over.
  peerWatch: null,
  // Failover (faz 5/6): sahiplik durumu, epoch, replika tazeligi. Kurulu degilse `null`.
  failover: null,
  lastProbe: null,
  provisioning: { provisioned: false, setupPending: false, reason: "Not checked." },
  firstRun: null,
  // Konagin yasam dongusu defteri (Android'de on plan servisi, Linux'ta systemd). Calisma
  // zamani kendi olumunu goremez; kac kez ve neden yeniden baslatildigini ancak konak bilir,
  // o yuzden konak buraya bildirir ve tanilama tek yerden okunur. Bildirilmediyse `null`.
  host: null
};

function parseArguments(argv) {
  const values = {};
  for (const argument of argv) {
    const match = /^--([a-z-]+)=(.*)$/.exec(argument);
    if (match) values[match[1]] = match[2];
  }
  const platform = ["android", "linux", "raspberry-pi"].includes(values.platform)
    ? values.platform
    : "android";
  return {
    platform,
    diagnosticsHost: isLoopbackAddress(values["diagnostics-host"])
      ? values["diagnostics-host"]
      : "127.0.0.1",
    diagnosticsPort: validPort(values["diagnostics-port"] || values.port) || 8092,
    coreHost: validBindAddress(values["core-host"]) || "127.0.0.1",
    corePort: validPort(values["core-port"]) || 8091,
    coreEntrypoint: values["core-entrypoint"]
      ? path.resolve(values["core-entrypoint"])
      : null,
    mqttHost: validBindAddress(values["mqtt-host"]) || "127.0.0.1",
    mqttPort: validPort(values["mqtt-port"]) || 1883,
    controlToken: values["control-token"] || "",
    dataDir: values["data-dir"] || path.join(process.cwd(), "data")
  };
}

function validPort(value) {
  const port = Number(value);
  return Number.isInteger(port) && port >= 1 && port <= 65535 ? port : null;
}

function validBindAddress(value) {
  return value === "0.0.0.0" || isLoopbackAddress(value) ? value : null;
}

function validProbe(input) {
  if (!input || typeof input !== "object") return null;
  const host = typeof input.host === "string" ? input.host.trim() : "";
  const port = validPort(input.port);
  if (!host || host.length > 253 || !/^[a-z0-9._:-]+$/i.test(host) || !port) return null;
  return { host, port };
}

function isLoopbackAddress(address) {
  if (typeof address !== "string") return false;
  const normalized = address.toLowerCase().replace(/^::ffff:/, "");
  return normalized === "::1" || normalized === "localhost" || /^127(?:\.\d{1,3}){3}$/.test(normalized);
}

function isLocalRequest(request) {
  return isLoopbackAddress(request?.socket?.remoteAddress);
}

function networkAddresses() {
  const addresses = [];
  for (const [name, entries] of Object.entries(os.networkInterfaces())) {
    for (const entry of entries || []) {
      if (entry.family === "IPv4" && !entry.internal) addresses.push({ name, address: entry.address });
    }
  }
  return addresses;
}

function diagnostics(config) {
  const monitorReady = runtime.monitor?.ready === true;
  const setupPending = runtime.provisioning.setupPending === true;
  const standaloneReady =
    runtime.provisioning.provisioned &&
    !setupPending &&
    runtime.mqtt.listening &&
    runtime.mqtt.selfTest &&
    !runtime.mqtt.error &&
    runtime.core.ready &&
    runtime.matter.ready;
  const ready = monitorReady || standaloneReady;
  const runtimePlatform = config.platform || "android";
  // `-setup`: panel ayakta ve sihirbaz calisiyor, ama hicbir koordinator sahiplenilmedi.
  const failoverState = runtime.failover?.state || null;
  const mode = monitorReady
    ? "android-monitor"
    : failoverState && failoverState !== "owner"
      ? `${runtimePlatform}-standby`
    : !runtime.provisioning.provisioned
      ? `${runtimePlatform}-unprovisioned`
      : setupPending
        ? `${runtimePlatform}-setup`
        : `${runtimePlatform}-standalone`;
  const dashboard = monitorReady
    ? runtime.monitor.dashboardUrl
    : `http://${config.coreHost}:${config.corePort}/`;
  return {
    ok: ready,
    ready,
    mode,
    runtimePlatform,
    node: process.versions.node,
    architecture: process.arch,
    platform: process.platform,
    uptimeSeconds: Math.floor(process.uptime()),
    startedAt: runtime.startedAt,
    addresses: networkAddresses(),
    monitor: runtime.monitor,
    provisioning: runtime.provisioning,
    firstRun: runtime.firstRun,
    mqtt: runtime.mqtt,
    core: runtime.core,
    matter: runtime.matter,
    multicast: runtime.multicast,
    intl: { fallback: intlFallback, ...intlSelfCheck() },
    host: runtime.host,
    peerWatch: runtime.peerWatch,
    failover: runtime.failover,
    lastProbe: runtime.lastProbe,
    endpoints: {
      dashboard,
      core: dashboard,
      diagnostics: `http://${config.diagnosticsHost}:${config.diagnosticsPort}`,
      mqtt: `mqtt://${config.mqttHost}:${config.mqttPort}`,
      matterbridge: "ws://127.0.0.1:8283"
    }
  };
}

/** Konak raporunu sinirlari belli sayilara indirger; gelen her sey dogrulanir. */
function hostState(input) {
  const number = (value, max) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed >= 0 ? Math.min(Math.floor(parsed), max) : 0;
  };
  return {
    restarts: number(input?.restarts, 10000),
    lastExitCode: Number.isFinite(Number(input?.lastExitCode)) ? Number(input.lastExitCode) : 0,
    lastFailureAt: number(input?.lastFailureAt, Number.MAX_SAFE_INTEGER) || null,
    nextDelayMs: number(input?.nextDelayMs, 86_400_000),
    maxDelayMs: number(input?.maxDelayMs, 86_400_000),
    maxRestarts: number(input?.maxRestarts, 10000),
    exhausted: input?.exhausted === true,
    reportedAt: new Date().toISOString()
  };
}

function probeTcp(input, timeoutMs = 3000) {
  const target = validProbe(input);
  if (!target) return Promise.resolve({ ok: false, error: "A valid host and port are required." });
  const started = Date.now();
  return new Promise((resolve) => {
    const socket = net.createConnection(target);
    let finished = false;
    const done = (result) => {
      if (finished) return;
      finished = true;
      socket.destroy();
      const report = { ...result, host: target.host, port: target.port, latencyMs: Date.now() - started };
      runtime.lastProbe = { ...report, at: new Date().toISOString() };
      resolve(report);
    };
    socket.setTimeout(timeoutMs);
    socket.once("connect", () => done({ ok: true }));
    socket.once("timeout", () => done({ ok: false, error: "Connection timed out." }));
    socket.once("error", (error) => done({ ok: false, error: error.message }));
  });
}

function probeMulticast(timeoutMs = 1200) {
  return new Promise((resolve) => {
    const socket = dgram.createSocket({ type: "udp4", reuseAddr: true });
    let completed = false;
    const done = (result) => {
      if (completed) return;
      completed = true;
      clearTimeout(timer);
      try {
        socket.close();
      } catch {
        // A timeout can occur before bind completes; an unbound UDP socket cannot be closed.
      }
      runtime.multicast = result;
      resolve(result);
    };
    const timer = setTimeout(() => done({ available: false, error: "Multicast probe timed out." }), timeoutMs);
    socket.once("error", (error) => done({ available: false, error: error.message }));
    socket.bind(0, () => {
      if (completed) {
        try {
          socket.close();
        } catch {
          // The socket may already have been closed by an error event.
        }
        return;
      }
      try {
        socket.addMembership("224.0.0.251");
        socket.setMulticastTTL(1);
        done({ available: true, error: null });
      } catch (error) {
        done({ available: false, error: error.message });
      }
    });
  });
}

async function readJson(request) {
  const chunks = [];
  let length = 0;
  for await (const chunk of request) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    length += buffer.length;
    if (length > 4096) throw new Error("Request is too large.");
    chunks.push(buffer);
  }
  const body = Buffer.concat(chunks).toString("utf8");
  return JSON.parse(body || "{}");
}

function sendJson(response, status, value) {
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store"
  });
  response.end(JSON.stringify(value));
}

function safeCredentialEqual(actual, expected) {
  const actualBuffer = Buffer.from(actual ?? "");
  const expectedBuffer = Buffer.from(expected ?? "");
  return actualBuffer.length === expectedBuffer.length &&
    timingSafeEqual(actualBuffer, expectedBuffer);
}

function startMqttBroker(config) {
  const broker = Aedes();
  const server = net.createServer(broker.handle);
  const authRequired = config.mqttAuthRequired === true;
  runtime.mqtt.authRequired = authRequired;
  if (authRequired) {
    broker.authenticate = (_client, username, password, callback) => {
      const allowed =
        safeCredentialEqual(username, config.mqttUsername) &&
        safeCredentialEqual(password, config.mqttPassword);
      if (allowed) {
        callback(null, true);
        return;
      }
      const error = new Error("Not authorized.");
      error.returnCode = 5;
      callback(error, false);
    };
  }
  let selfTestClient = null;
  let selfTestTimer = null;
  let selfTestFinished = false;
  let resolveReady;
  const ready = new Promise((resolve) => {
    resolveReady = resolve;
  });

  const finishSelfTest = (result) => {
    if (selfTestFinished) return;
    selfTestFinished = true;
    if (selfTestTimer) clearTimeout(selfTestTimer);
    runtime.mqtt.selfTest = result.ok;
    runtime.mqtt.error = result.error || null;
    if (selfTestClient) selfTestClient.end(true);
    resolveReady(result);
  };

  server.on("error", (error) => {
    runtime.mqtt.listening = false;
    runtime.mqtt.error = error.message;
    finishSelfTest({ ok: false, error: error.message });
    console.error("Embedded MQTT broker error:", error);
  });
  server.on("close", () => {
    runtime.mqtt.listening = false;
  });
  server.listen(config.mqttPort, config.mqttHost, () => {
    runtime.mqtt.listening = true;
    const address = server.address();
    const port = typeof address === "object" && address ? address.port : config.mqttPort;
    const connectHost =
      config.mqttHost === "0.0.0.0" ? "127.0.0.1" :
      config.mqttHost === "::" ? "::1" :
      config.mqttHost;
    const urlHost = connectHost.includes(":") ? `[${connectHost}]` : connectHost;
    const selfTestOptions = {
      clientId: `villa-android-self-test-${process.pid}`,
      reconnectPeriod: 0,
      connectTimeout: 2000,
      protocolVersion: 4
    };
    if (authRequired) {
      selfTestOptions.username = config.mqttUsername;
      selfTestOptions.password = config.mqttPassword;
    }
    selfTestClient = mqtt.connect(`mqtt://${urlHost}:${port}`, selfTestOptions);
    const topic = `_villa/android/self-test/${process.pid}/${Date.now()}`;
    selfTestTimer = setTimeout(() => {
      finishSelfTest({ ok: false, error: "Embedded MQTT self-test timed out." });
    }, 3000);
    selfTestClient.once("connect", () => {
      selfTestClient.subscribe(topic, { qos: 0 }, (subscribeError) => {
        if (subscribeError) {
          finishSelfTest({ ok: false, error: subscribeError.message });
          return;
        }
        selfTestClient.publish(topic, "ready");
      });
    });
    selfTestClient.on("message", (receivedTopic, payload) => {
      if (receivedTopic === topic && payload.toString() === "ready") {
        finishSelfTest({ ok: true, error: null, host: connectHost, port });
      }
    });
    selfTestClient.once("error", (error) => {
      finishSelfTest({ ok: false, error: error.message });
    });
  });

  const close = async () => {
    if (selfTestClient) selfTestClient.end(true);
    if (selfTestTimer) clearTimeout(selfTestTimer);
    await Promise.all([
      new Promise((resolve) => {
        if (!server.listening) {
          resolve();
          return;
        }
        server.close(resolve);
      }),
      new Promise((resolve) => broker.close(resolve))
    ]);
  };

  return { broker, server, ready, close };
}

function createHttpServer(config, hooks = {}) {
  const dashboard = fs.readFileSync(path.join(__dirname, "dashboard.html"));
  let shutdownRequested = false;
  return http.createServer(async (request, response) => {
    try {
      const url = new URL(request.url || "/", `http://${request.headers.host || "localhost"}`);
      if (request.method === "GET" && url.pathname === "/") {
        response.writeHead(200, {
          "content-type": "text/html; charset=utf-8",
          "cache-control": "no-store"
        });
        response.end(dashboard);
        return;
      }
      if (
        request.method === "GET" &&
        (url.pathname === "/api/health" || url.pathname === "/api/ready")
      ) {
        const report = diagnostics(config);
        sendJson(response, report.ok ? 200 : 503, report);
        return;
      }
      if (request.method === "GET" && url.pathname === "/api/android/diagnostics") {
        sendJson(response, 200, diagnostics(config));
        return;
      }
      if (request.method === "POST" && url.pathname === "/api/android/runtime/shutdown") {
        const bearer = /^Bearer (.+)$/.exec(request.headers.authorization || "")?.[1] || "";
        if (
          !isLocalRequest(request) ||
          !config.controlToken ||
          !safeCredentialEqual(bearer, config.controlToken)
        ) {
          sendJson(response, 403, { ok: false, error: "Runtime control was denied." });
          return;
        }
        sendJson(response, 202, { ok: true, status: "stopping" });
        if (!shutdownRequested) {
          shutdownRequested = true;
          setImmediate(() => void hooks.shutdown?.());
        }
        return;
      }
      /*
       * Konagin yasam dongusu raporu. Yalnizca yerelden ve denetim jetonuyla kabul edilir —
       * ayni kapi kapatma ucuyla ayni guven sinirinda. Icerik SAYIDIR: kac yeniden baslatma,
       * son cikis kodu, siradaki geri cekilme suresi. Konak adi ya da konak-ozel bir alan
       * beklenmez; hem Android on plan servisi hem baska bir denetleyici ayni sozlesmeyi
       * kullanabilir.
       */
      if (request.method === "POST" && url.pathname === "/api/android/runtime/host-state") {
        const bearer = /^Bearer (.+)$/.exec(request.headers.authorization || "")?.[1] || "";
        if (
          !isLocalRequest(request) ||
          !config.controlToken ||
          !safeCredentialEqual(bearer, config.controlToken)
        ) {
          sendJson(response, 403, { ok: false, error: "Runtime control was denied." });
          return;
        }
        runtime.host = hostState(await readJson(request));
        sendJson(response, 200, { ok: true, host: runtime.host });
        return;
      }
      if (request.method === "POST" && url.pathname === "/api/android/probe/tcp") {
        if (!isLocalRequest(request)) {
          sendJson(response, 403, { ok: false, error: "Network probes are restricted to the local app." });
          return;
        }
        sendJson(response, 200, await probeTcp(await readJson(request)));
        return;
      }
      if (request.method === "POST" && url.pathname === "/api/android/probe/multicast") {
        if (!isLocalRequest(request)) {
          sendJson(response, 403, { ok: false, error: "Network probes are restricted to the local app." });
          return;
        }
        sendJson(response, 200, await probeMulticast());
        return;
      }
      sendJson(response, 404, { ok: false, error: "Not found." });
    } catch (error) {
      sendJson(response, 400, { ok: false, error: error.message });
    }
  });
}

/**
 * Bagimsiz yigin: gomulu MQTT -> oz-sinama -> cekirdek (koordinator burada alinir) -> Matter.
 * Failover devralmasi ve eski yol ayni fonksiyonu kullanir.
 */
async function startStandaloneStack(config, provision, stack) {
  stack.mqttServer = startMqttBroker({
    ...config,
    mqttAuthRequired: provision.mqttAuthRequired === true,
    mqttUsername: provision.mqttUsername,
    mqttPassword: provision.mqttPassword
  });
  const mqttReady = await stack.mqttServer.ready;
  if (mqttReady.ok) {
    console.log(`Embedded MQTT self-test passed on ${mqttReady.host}:${mqttReady.port}`);
  } else {
    console.error("Embedded MQTT self-test failed:", mqttReady.error);
  }
  await probeMulticast();

  if (!provision.provisioned) {
    runtime.core = { status: "unprovisioned", ready: false, error: provision.reason };
    runtime.matter = { status: "unprovisioned", ready: false, error: provision.reason };
    console.warn(`Villa Bridge ${config.platform} runtime is unprovisioned: ${provision.reason}`);
  } else if (!mqttReady.ok) {
    runtime.core = { status: "blocked", ready: false, error: "Embedded MQTT is unavailable." };
    runtime.matter = { status: "blocked", ready: false, error: "Embedded MQTT is unavailable." };
  } else {
    runtime.core = { status: "starting", ready: false, error: null };
    try {
      stack.coreService = await startVillaBridgeCore(config, provision, { generation: stack.generation });
      stack.generation += 1;
      runtime.core = {
        status: "ready",
        ready: true,
        error: null,
        endpoint: `http://${config.coreHost}:${config.corePort}`
      };
      if (provision.setupPending === true) {
        const host = networkAddresses()[0]?.address || config.coreHost;
        console.warn(
          "Villa Bridge kurulum kipinde: hicbir koordinatore baglanilmadi. " +
          `Kuruluma panelden devam edin: http://${host}:${config.corePort}`
        );
      }
    } catch (error) {
      runtime.core = { status: "failed", ready: false, error: error.message };
      console.error("Villa Bridge direct core failed:", error);
    }

    if (provision.setupPending === true) {
      // Kurulum bitmeden Matter'a cihaz yayinlanmaz: ortada bir Zigbee agi yok.
      runtime.matter = {
        status: "setup",
        ready: false,
        error: "Matterbridge waits for the setup wizard to finish."
      };
    } else if (!provision.matterEnabled) {
      runtime.matter = { status: "disabled", ready: true, error: null };
    } else if (!runtime.core.ready) {
      runtime.matter = {
        status: "blocked",
        ready: false,
        error: "Matterbridge waits for the Villa Bridge core."
      };
    } else {
      runtime.matter = { status: "starting", ready: false, error: null };
      try {
        stack.matterService = await startMatterbridge(config, provision);
        const updateMatterState = () => {
          const ready = matterbridgeReady(stack.matterService);
          const plugin = stack.matterService.plugin;
          runtime.matter = {
            status: plugin?.error ? "failed" : ready ? "ready" : "starting",
            ready,
            error: plugin?.error ? "Matterbridge Zigbee2MQTT plugin reported an error." : null,
            plugin: {
              loaded: plugin?.loaded === true,
              started: plugin?.started === true,
              configured: plugin?.configured === true
            },
            commissioningPort: stack.matterService.instance?.serverNode ? 5540 : null
          };
        };
        updateMatterState();
        stack.matterMonitor = setInterval(updateMatterState, 1000);
        stack.matterMonitor.unref();
      } catch (error) {
        runtime.matter = { status: "failed", ready: false, error: error.message };
        console.error("Matterbridge failed:", error);
      }
    }
  }
}

/** Yigini ters sirayla kapatir: Matter -> cekirdek (koordinator oturumu kapanir) -> broker. */
async function stopStandaloneStack(stack) {
  if (stack.matterMonitor) clearInterval(stack.matterMonitor);
  stack.matterMonitor = null;
  await Promise.resolve(stack.matterService?.stop?.())
    .catch((error) => console.error("Matter shutdown failed:", error));
  stack.matterService = null;
  await Promise.resolve(stack.coreService?.stop?.())
    .catch((error) => console.error("Core shutdown failed:", error));
  stack.coreService = null;
  await Promise.resolve(stack.mqttServer?.close?.())
    .catch((error) => console.error("MQTT shutdown failed:", error));
  stack.mqttServer = null;
  runtime.core = { status: "stopped", ready: false, error: null };
  runtime.matter = { status: "stopped", ready: false, error: null };
  runtime.mqtt = { listening: false, selfTest: false, error: null };
}

async function start() {
  const config = parseArguments(process.argv.slice(2));
  fs.mkdirSync(config.dataDir, { recursive: true });
  fs.writeFileSync(
    path.join(config.dataDir, `${config.platform}-runtime.json`),
    JSON.stringify({
      startedAt: runtime.startedAt,
      node: process.versions.node,
      platform: config.platform
    }, null, 2)
  );

  // Ilk acilis tohumlamasi kabuk betiginde DEGIL burada: Android'de kabuk yok.
  // Var olan dosyaya dokunmaz; ikinci calistirmada hicbir sey degismez.
  try {
    const firstRun = ensureFirstRunFiles(config);
    runtime.firstRun = { created: firstRun.created, kept: firstRun.kept.length };
    if (firstRun.created.length > 0) {
      console.log(`Villa Bridge ilk acilis dosyalari uretildi: ${firstRun.created.join(", ")}`);
    }
  } catch (error) {
    runtime.firstRun = { created: [], kept: 0, error: error.message };
    console.error("Villa Bridge ilk acilis dosyalari uretilemedi:", error);
  }

  const provision = loadProvisioning(config);
  runtime.provisioning = {
    provisioned: provision.provisioned,
    setupPending: provision.setupPending === true,
    reason: provision.reason,
    configPath: provision.sourceConfigPath
      ? path.relative(config.dataDir, provision.sourceConfigPath)
      : null,
    matterEnabled: provision.matterEnabled
  };
  let shutdownRequestedEarly = false;
  const runtimeHooks = {
    shutdown: () => {
      shutdownRequestedEarly = true;
    }
  };
  const httpServer = createHttpServer(config, runtimeHooks);
  httpServer.listen(config.diagnosticsPort, config.diagnosticsHost, () => {
    console.log(
      `Villa Bridge ${config.platform} diagnostics listening on ` +
      `${config.diagnosticsHost}:${config.diagnosticsPort}`
    );
  });
  httpServer.on("error", (error) => console.error("HTTP server error:", error));

  const stack = { mqttServer: null, coreService: null, matterService: null, matterMonitor: null, generation: 0 };
  let peerWatcher = null;
  let failover = null;
  // Kurulmus dugum (koordinator adresi girilmis) failover protokolune katilir: talep et ve
  // bekle, sahip varsa baglanma. Kurulmamis/kurulum kipindeki dugum hicbir koordinatoru
  // sahiplenmedigi icin eski yoldan acilir.
  const failoverCapable = provision.provisioned && provision.setupPending !== true;
  const peer = !failoverCapable && config.platform === "android"
    ? await discoverVillaBridgeServer({ selfNodeId: resolveVillaBridgeNodeId("android") }).catch((error) => {
      console.warn(`Villa Bridge LAN discovery failed: ${error.message}`);
      return null;
    })
    : null;
  if (failoverCapable) {
    failover = await startFailoverRuntime({
      config,
      provision,
      runtime,
      stack: {
        start: async () => {
          await startStandaloneStack(config, provision, stack);
          return runtime.core.ready
            ? { ok: true }
            : { ok: false, error: runtime.core.error || `Core status: ${runtime.core.status}` };
        },
        stop: () => stopStandaloneStack(stack)
      }
    });
  } else if (peer) {
    runtime.monitor = {
      status: "ready",
      ready: true,
      address: peer.address,
      dashboardUrl: peer.dashboardUrl,
      serverMode: peer.mode,
      serverNodeId: peer.nodeId ?? null,
      serverState: peer.state ?? null,
      serverEpoch: peer.epoch ?? 0
    };
    runtime.mqtt = {
      status: "disabled",
      listening: false,
      selfTest: false,
      error: null,
      reason: "A Villa Bridge server is active on the LAN."
    };
    runtime.core = {
      status: "remote",
      ready: true,
      error: null,
      endpoint: peer.dashboardUrl
    };
    runtime.matter = {
      status: "remote",
      ready: true,
      error: null
    };
    console.log(
      `Villa Bridge server found at ${peer.dashboardUrl}; Android is running in monitor-only mode.`
    );
    // Phase 2: keep watching the server on three channels. No take-over is ever attempted here.
    peerWatcher = startPeerWatcher({
      probe: createPeerProbe({
        address: peer.address,
        dashboardPort: peer.dashboardPort,
        mqttPort: config.mqttPort,
        nodeId: peer.nodeId ?? null,
        mode: peer.mode ?? null,
        state: peer.state ?? null
      }),
      onStatus: (status) => {
        runtime.peerWatch = status;
      }
    });
  } else {
    await startStandaloneStack(config, provision, stack);
  }

  let shutdownPromise = null;
  const shutdown = async () => {
    if (shutdownPromise) return shutdownPromise;
    shutdownPromise = (async () => {
      peerWatcher?.stop?.();
      // Failover once durur (yigini kendi kilidiyle kapatir), sonra kalan her sey.
      await Promise.resolve(failover?.stop?.())
        .catch((error) => console.error("Failover shutdown failed:", error));
      await stopStandaloneStack(stack);
      await new Promise((resolve) => {
        if (!httpServer.listening) {
          resolve();
          return;
        }
        httpServer.close(resolve);
      });
      process.exitCode = 0;
    })();
    return shutdownPromise;
  };
  runtimeHooks.shutdown = shutdown;
  if (shutdownRequestedEarly) void shutdown();
  process.once("SIGTERM", () => void shutdown());
  process.once("SIGINT", () => void shutdown());
}

if (require.main === module) {
  start().catch((error) => {
    console.error("Villa Bridge shared runtime failed:", error);
    process.exitCode = 1;
  });
}

module.exports = {
  createHttpServer,
  diagnostics,
  isLocalRequest,
  isLoopbackAddress,
  networkAddresses,
  parseArguments,
  probeMulticast,
  probeTcp,
  startMqttBroker,
  runtime,
  validBindAddress,
  validPort,
  validProbe
};
