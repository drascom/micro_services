// Failover faz 5/6 birim testleri (node:test). Calistirma: `npm run failover:test`.
// Gercek ag, gercek koordinator, gercek saat YOK: LAN ve koordinator sahte, saat sanal.
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const cjsPolicy = require(path.join(root, "apps/runtime/failover-policy.cjs"));
const tsPolicyRaw = await import(path.join(root, "dist/failover-policy.js"));
const sync = require(path.join(root, "apps/runtime/failover-sync.cjs"));
const { createReplicaSync } = require(path.join(root, "apps/runtime/failover-net.cjs"));
const { createFailoverController, STATES } = require(path.join(root, "apps/runtime/failover.cjs"));
const { createPeerWatchState } = require(path.join(root, "apps/runtime/peer-watch.cjs"));

// TS ikizini CJS ile ayni adlara esle: iki uygulama ayni tabloyla sinanir.
const tsPolicy = {
  isInSnapshotScope: tsPolicyRaw.isInSnapshotScope,
  networkIdentity: tsPolicyRaw.networkIdentity,
  compareNetworkIdentity: tsPolicyRaw.compareNetworkIdentity,
  outranks: tsPolicyRaw.outranks,
  evaluatePeers: tsPolicyRaw.evaluatePeers,
  claimVerdict: tsPolicyRaw.claimVerdict,
  ownerConflictVerdict: tsPolicyRaw.ownerConflictVerdict,
  syncDirection: tsPolicyRaw.syncDirection,
  decideSnapshot: tsPolicyRaw.decideSnapshot,
  shouldApplyReplica: tsPolicyRaw.shouldApplyReplica,
  SNAPSHOT_FILES: tsPolicyRaw.snapshotFiles
};

const network = {
  serial: { port: "tcp://10.0.0.5:6638", adapter: "zstack" },
  advanced: { pan_id: 6754, ext_pan_id: [1, 2, 3, 4, 5, 6, 7, 8], network_key: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16], channel: 15 }
};

for (const [name, policy] of [["cjs", cjsPolicy], ["ts", tsPolicy]]) {
  test(`${name}: replication scope includes user data and excludes platform files`, () => {
    for (const file of ["runtime/automations.json", "aliases.json", "runtime/auth.json", "runtime/home-groups.json",
      "runtime/appearance.json", "zigbee/database.db", "zigbee/coordinator_backup.json", "zigbee/state.json",
      "zigbee/external_converters/door_sensor.js"]) {
      assert.equal(policy.isInSnapshotScope(file), true, file);
    }
    for (const file of ["provisioning.json", "linux-runtime.json", "android-runtime.json", "failover-state.json",
      "zigbee/configuration.yaml", "runtime/villa-bridge.yaml", "config/villa-bridge.yaml",
      "matterbridge/storage/x.json", "runtime/device-events.json", "runtime/installation-state.json",
      "../runtime/auth.json", "/etc/passwd", "zigbee/external_converters/../../provisioning.json",
      "zigbee/external_converters/sub/x.js", "zigbee/external_converters/.hidden.js", "replica/meta.json"]) {
      assert.equal(policy.isInSnapshotScope(file), false, file);
    }
  });

  test(`${name}: network identity mismatch or unknown field never matches`, () => {
    const local = policy.networkIdentity(network);
    assert.equal(policy.compareNetworkIdentity(local, policy.networkIdentity(structuredClone(network))).match, true);
    const otherKey = structuredClone(network);
    otherKey.advanced.network_key[0] = 99;
    assert.deepEqual(policy.compareNetworkIdentity(local, policy.networkIdentity(otherKey)).mismatched, ["network_key"]);
    const otherAdapter = structuredClone(network);
    otherAdapter.serial.adapter = "ember";
    assert.deepEqual(policy.compareNetworkIdentity(local, policy.networkIdentity(otherAdapter)).mismatched, ["adapter"]);
    const missing = structuredClone(network);
    delete missing.advanced.pan_id;
    const result = policy.compareNetworkIdentity(local, policy.networkIdentity(missing));
    assert.equal(result.match, false);
    assert.deepEqual(result.unknown, ["pan_id"]);
    assert.equal(policy.compareNetworkIdentity(local, null).match, false);
  });

  test(`${name}: fixed priority, server wins, nodeId breaks ties`, () => {
    const server = { nodeId: "srv-b", priority: 0 };
    const tablet = { nodeId: "tab-a", priority: 1 };
    assert.equal(policy.outranks(server, tablet), true);
    assert.equal(policy.outranks(tablet, server), false);
    assert.equal(policy.outranks({ nodeId: "a", priority: 1 }, { nodeId: "b", priority: 1 }), true);
  });

  test(`${name}: claim is aborted by an owner, a releasing node or a better claimant`, () => {
    const self = { nodeId: "tab", priority: 1, coordinatorId: "c1" };
    assert.equal(policy.claimVerdict(self, []).verdict, "proceed");
    assert.equal(policy.claimVerdict(self, [{ nodeId: "srv", priority: 0, state: "owner", coordinatorId: "c1" }]).verdict, "abort-owner");
    assert.equal(policy.claimVerdict(self, [{ nodeId: "srv", priority: 0, state: "releasing", coordinatorId: "c1" }]).verdict, "abort-owner");
    assert.equal(policy.claimVerdict(self, [{ nodeId: "srv", priority: 0, state: "claiming", coordinatorId: "c1" }]).verdict, "abort-claimant");
    // Bizden sonra gelen talep bizi durdurmaz.
    assert.equal(policy.claimVerdict({ ...self, priority: 0, nodeId: "srv" }, [{ nodeId: "tab", priority: 1, state: "claiming", coordinatorId: "c1" }]).verdict, "proceed");
    // Baska koordinatorun sahibi bizi kilitlemez; bilinmeyen koordinator kilitler (suphede dur).
    assert.equal(policy.claimVerdict(self, [{ nodeId: "x", priority: 0, state: "owner", coordinatorId: "c2" }]).verdict, "proceed");
    assert.equal(policy.claimVerdict(self, [{ nodeId: "x", priority: 0, state: "owner", coordinatorId: null }]).verdict, "abort-owner");
    assert.equal(policy.evaluatePeers(self, [{ nodeId: "x", epoch: 7, state: "standby" }]).maxEpoch, 7);
  });

  test(`${name}: an owner yields only to a higher-priority owner`, () => {
    const tablet = { nodeId: "tab", priority: 1, coordinatorId: "c1" };
    const server = { nodeId: "srv", priority: 0, coordinatorId: "c1" };
    assert.equal(policy.ownerConflictVerdict(tablet, [{ ...server, state: "owner" }]).verdict, "yield");
    assert.equal(policy.ownerConflictVerdict(server, [{ ...tablet, state: "owner" }]).verdict, "keep");
  });

  test(`${name}: replication flows from owner to standby only`, () => {
    assert.equal(policy.syncDirection("owner", "standby"), "serve");
    assert.equal(policy.syncDirection("standby", "owner"), "pull");
    assert.equal(policy.syncDirection("standby", "standby"), "none");
    assert.equal(policy.syncDirection("standby", "claiming"), "none");
  });

  test(`${name}: epoch fencing never lets an older snapshot overwrite a newer one`, () => {
    const localNetwork = policy.networkIdentity(network);
    const incoming = { format: 1, epoch: 5, digest: "d1", createdAt: 1000, ownerNodeId: "srv", network: localNetwork };
    assert.deepEqual(policy.decideSnapshot({ incoming, localNetwork, knownEpoch: 5 }).reason, "newer");
    assert.equal(policy.decideSnapshot({ incoming, localNetwork, knownEpoch: 6 }).reason, "stale-epoch");
    assert.equal(policy.decideSnapshot({ incoming, localNetwork, replica: { epoch: 6, digest: "x", createdAt: 1 } }).reason, "stale-epoch");
    assert.equal(policy.decideSnapshot({ incoming, localNetwork, replica: { epoch: 5, digest: "d1", createdAt: 1000 } }).reason, "unchanged");
    assert.equal(policy.decideSnapshot({ incoming, localNetwork, replica: { epoch: 5, digest: "d0", createdAt: 2000 } }).reason, "older-than-replica");
    assert.equal(policy.decideSnapshot({ incoming, localNetwork, replica: { epoch: 4, digest: "d0", createdAt: 9000 } }).accept, true);
    assert.equal(policy.decideSnapshot({ incoming, localNetwork, selfNodeId: "srv" }).reason, "own-snapshot");
    assert.equal(policy.decideSnapshot({ incoming: { ...incoming, format: 2 }, localNetwork }).reason, "invalid");
    const otherNetwork = policy.networkIdentity({ ...network, advanced: { ...network.advanced, pan_id: 1 } });
    const halted = policy.decideSnapshot({ incoming: { ...incoming, network: otherNetwork }, localNetwork });
    assert.equal(halted.reason, "network-mismatch");
    assert.equal(halted.halt, true);
    assert.equal(halted.accept, false);
  });

  test(`${name}: replica is copied to live data only when it is newer`, () => {
    assert.equal(policy.shouldApplyReplica({ epoch: 3 }, null), true);
    assert.equal(policy.shouldApplyReplica({ epoch: 3 }, 2), true);
    assert.equal(policy.shouldApplyReplica({ epoch: 3 }, 3), false);
    assert.equal(policy.shouldApplyReplica({ epoch: 3 }, 4), false);
    assert.equal(policy.shouldApplyReplica(null, null), false);
  });
}

test("ts and cjs scope lists are identical", () => {
  assert.deepEqual([...tsPolicy.SNAPSHOT_FILES], [...cjsPolicy.SNAPSHOT_FILES]);
});

function tempDataDir(files) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "villa-failover-"));
  for (const [relative, content] of Object.entries(files)) {
    fs.mkdirSync(path.dirname(path.join(dir, relative)), { recursive: true });
    fs.writeFileSync(path.join(dir, relative), content);
  }
  return dir;
}

const yaml = (value) => JSON.stringify(value);

test("snapshot carries only in-scope files and round-trips through the replica", () => {
  const owner = tempDataDir({
    "runtime/automations.json": "[1]",
    "runtime/auth.json": "{\"users\":1}",
    "aliases.json": "{}",
    "zigbee/database.db": "db-content",
    "zigbee/external_converters/door.js": "module.exports=[]",
    "zigbee/configuration.yaml": yaml(network),
    "provisioning.json": "{}",
    "linux-runtime.json": "{}",
    "runtime/villa-bridge.yaml": "mode: direct"
  });
  const identity = sync.publicNetworkIdentity(sync.readNetworkIdentity(path.join(owner, "zigbee/configuration.yaml")));
  assert.notDeepEqual(identity.network_key, network.advanced.network_key, "network key never travels raw");
  const snapshot = sync.buildSnapshot(owner, { epoch: 3, nodeId: "srv", network: identity, createdAt: 10 });
  assert.deepEqual(snapshot.files.map((file) => file.path), [
    "aliases.json", "runtime/auth.json", "runtime/automations.json", "zigbee/database.db", "zigbee/external_converters/door.js"
  ]);
  assert.equal(snapshot.digest, sync.scopeManifest(owner).digest);

  const standby = tempDataDir({ "provisioning.json": "{\"keep\":true}", "runtime/automations.json": "[0]" });
  sync.writeReplica(standby, snapshot, 20);
  const meta = sync.readReplicaMeta(standby);
  assert.equal(meta.epoch, 3);
  assert.equal(meta.receivedAt, 20);
  assert.equal(fs.readFileSync(path.join(standby, "runtime/automations.json"), "utf8"), "[0]", "replica never touches live data");
  const applied = sync.applySnapshotToLive(standby, sync.readReplica(standby), Date.UTC(2026, 0, 1));
  assert.equal(applied.files, 5);
  assert.equal(fs.readFileSync(path.join(standby, "runtime/automations.json"), "utf8"), "[1]");
  assert.equal(fs.readFileSync(path.join(standby, "provisioning.json"), "utf8"), "{\"keep\":true}");
  assert.equal(fs.readFileSync(path.join(standby, applied.backup, "runtime/automations.json"), "utf8"), "[0]");
});

test("a snapshot with traversal, out-of-scope or corrupt files is refused", () => {
  const owner = tempDataDir({ "runtime/automations.json": "[1]" });
  const snapshot = sync.buildSnapshot(owner, { epoch: 1, nodeId: "srv", network: null, createdAt: 1 });
  const evil = structuredClone(snapshot);
  evil.files.push({ path: "../provisioning.json", sha256: "x", size: 1, data: "eA==" });
  assert.throws(() => sync.verifySnapshotFiles(evil), /outside the replication scope/);
  const outside = structuredClone(snapshot);
  outside.files[0].path = "provisioning.json";
  assert.throws(() => sync.verifySnapshotFiles(outside), /outside the replication scope/);
  const corrupt = structuredClone(snapshot);
  corrupt.files[0].data = Buffer.from("[2]").toString("base64");
  assert.throws(() => sync.verifySnapshotFiles(corrupt), /corrupt/);
});

test("failover token derives from the Zigbee network key and is absent without one", () => {
  const a = tempDataDir({ "zigbee/configuration.yaml": yaml(network) });
  const b = tempDataDir({ "zigbee/configuration.yaml": yaml(network) });
  const c = tempDataDir({ "zigbee/configuration.yaml": yaml({ advanced: { pan_id: 1 } }) });
  const tokenA = sync.failoverToken(path.join(a, "zigbee/configuration.yaml"));
  assert.match(tokenA, /^[0-9a-f]{64}$/);
  assert.equal(tokenA, sync.failoverToken(path.join(b, "zigbee/configuration.yaml")));
  assert.equal(sync.failoverToken(path.join(c, "zigbee/configuration.yaml")), null);
});

function replicaHarness({ selfState, peerState, snapshot, localNetwork, knownEpoch = 0 }) {
  const dir = tempDataDir({});
  const calls = [];
  const replicaSync = createReplicaSync({
    now: () => 1_000_000,
    readReplicaMeta: () => sync.readReplicaMeta(dir),
    writeReplica: (value, at) => sync.writeReplica(dir, value, at),
    self: () => ({ nodeId: "tab", state: selfState }),
    peer: () => ({ nodeId: "srv", address: "10.0.0.2", state: peerState }),
    token: () => "t",
    localNetwork: () => localNetwork,
    knownEpoch: () => knownEpoch,
    request: async ({ path: urlPath }) => {
      calls.push(urlPath);
      if (urlPath === "/api/failover/manifest") return { status: 200, body: { epoch: snapshot.epoch, digest: snapshot.digest } };
      return { status: 200, body: snapshot };
    },
    log: () => {}
  });
  return { dir, calls, replicaSync };
}

test("replica sync pulls from the owner and stops on a network mismatch without writing", async () => {
  const owner = tempDataDir({ "runtime/automations.json": "[1]", "zigbee/configuration.yaml": yaml(network) });
  const identity = sync.publicNetworkIdentity(sync.readNetworkIdentity(path.join(owner, "zigbee/configuration.yaml")));
  const snapshot = sync.buildSnapshot(owner, { epoch: 2, nodeId: "srv", network: identity, createdAt: 5 });

  const pulled = replicaHarness({ selfState: "standby", peerState: "owner", snapshot, localNetwork: identity });
  const ok = await pulled.replicaSync.syncOnce();
  assert.equal(ok.replica.epoch, 2);
  assert.deepEqual(pulled.calls, ["/api/failover/manifest", "/api/failover/snapshot"]);
  const again = await pulled.replicaSync.syncOnce();
  assert.equal(again.lastResult, "unchanged");
  assert.equal(pulled.calls.length, 3, "unchanged manifest does not pull the snapshot again");

  const wrongWay = replicaHarness({ selfState: "owner", peerState: "standby", snapshot, localNetwork: identity });
  await wrongWay.replicaSync.syncOnce();
  assert.deepEqual(wrongWay.calls, [], "an owner never pulls");
  const nobody = replicaHarness({ selfState: "standby", peerState: "standby", snapshot, localNetwork: identity });
  await nobody.replicaSync.syncOnce();
  assert.deepEqual(nobody.calls, [], "no owner, no replication");

  const otherNetwork = { ...identity, pan_id: 1 };
  const halted = replicaHarness({ selfState: "standby", peerState: "owner", snapshot, localNetwork: otherNetwork });
  const result = await halted.replicaSync.syncOnce();
  assert.equal(result.halted, true);
  assert.match(result.error, /pan_id/);
  assert.equal(sync.readReplicaMeta(halted.dir), null, "nothing was written");

  const stale = replicaHarness({ selfState: "standby", peerState: "owner", snapshot, localNetwork: identity, knownEpoch: 3 });
  const staleResult = await stale.replicaSync.syncOnce();
  assert.equal(staleResult.lastResult, "stale-epoch");
  assert.equal(sync.readReplicaMeta(stale.dir), null);
});

// ---------------------------------------------------------------------------------------------
// Durum makinesi simulasyonu: iki dugum, sanal saat, sahte LAN ve sahte koordinator.
// Degismez: koordinatore ayni anda en fazla BIR dugum bagli.

const settle = async () => {
  for (let index = 0; index < 30; index += 1) await new Promise((resolve) => setImmediate(resolve));
};

function createClock() {
  let now = 0;
  let sequence = 0;
  const timers = [];
  return {
    now: () => now,
    sleep: (ms) => new Promise((resolve) => timers.push({ at: now + Math.max(0, ms), sequence: sequence++, resolve })),
    async advance(ms) {
      const until = now + ms;
      for (;;) {
        await settle();
        timers.sort((a, b) => a.at - b.at || a.sequence - b.sequence);
        const next = timers[0];
        if (!next || next.at > until) {
          now = until;
          await settle();
          return;
        }
        timers.shift();
        now = next.at;
        next.resolve();
      }
    }
  };
}

function createWorld() {
  const clock = createClock();
  const holders = new Set();
  const world = {
    clock,
    nodes: new Map(),
    maxHolders: 0,
    holders,
    connect(id) {
      holders.add(id);
      world.maxHolders = Math.max(world.maxHolders, holders.size);
    },
    disconnect(id) {
      holders.delete(id);
    }
  };
  return world;
}

function addNode(world, { nodeId, priority, random = 0.5 }) {
  const node = {
    nodeId,
    alive: true,
    channels: { beacon: true, http: true, mqtt: true },
    releaseReachable: true,
    socketStaysOpen: false,
    startFails: false,
    networkUp: true,
    random,
    events: [],
    store: { value: { epoch: 0, liveEpoch: null }, load() { return { ...this.value }; }, save(value) { this.value = { ...value }; } },
    controller: null
  };
  const visible = (other) => other !== node && other.alive && other.controller;
  const boot = () => {
    node.alive = true;
    node.controller = createFailoverController({
      nodeId,
      priority,
      coordinatorId: "c1",
      now: world.clock.now,
      sleep: world.clock.sleep,
      random: () => node.random,
      store: node.store,
      discover: async () => [...world.nodes.values()]
        .filter((other) => visible(other) && other.channels.beacon)
        .map((other) => ({ ...other.controller.self(), address: other.nodeId })),
      probePeer: async (peer) => {
        const other = world.nodes.get(peer.nodeId);
        if (!other || !visible(other)) return { beacon: false, http: false, mqtt: false };
        return {
          ...other.channels,
          record: other.channels.beacon ? { ...other.controller.self(), address: other.nodeId } : null
        };
      },
      createPeerWatch: () => createPeerWatchState({ now: world.clock.now, logger: () => {} }),
      networkHealthy: async () => node.networkUp,
      startStack: async () => {
        node.events.push(["start", world.clock.now()]);
        world.connect(nodeId);
        return node.startFails ? { ok: false, error: "boom" } : { ok: true };
      },
      stopStack: async () => {
        node.events.push(["stop", world.clock.now()]);
        if (!node.socketStaysOpen) world.disconnect(nodeId);
      },
      coordinatorConnectionOpen: () => world.holders.has(nodeId),
      requestRelease: async (peer, self) => {
        const other = world.nodes.get(peer.nodeId);
        if (!other || !visible(other) || !other.releaseReachable) throw new Error("unreachable");
        return other.controller.release({ ...self, address: nodeId });
      },
      buildHandoverSnapshot: async ({ epoch }) => ({ format: 1, epoch, ownerNodeId: nodeId, digest: `d${epoch}`, createdAt: world.clock.now(), files: [] }),
      acceptHandoverSnapshot: async (snapshot) => {
        node.events.push(["accept", world.clock.now(), snapshot.epoch, snapshot.ownerNodeId]);
        return { ok: true, applied: true };
      },
      prepareLiveData: async () => ({ ok: true }),
      log: () => {}
    });
    void node.controller.run();
  };
  node.boot = boot;
  node.kill = () => {
    node.alive = false;
    world.disconnect(nodeId); // surec oldu: soket de gitti
    void node.controller.stop();
    node.controller = null;
  };
  world.nodes.set(nodeId, node);
  return node;
}

const owners = (world) => [...world.nodes.values()].filter((node) => node.controller?.state === STATES.OWNER).map((node) => node.nodeId);

test("tablet alone takes the coordinator; returning server gets it only through the release handshake", async () => {
  const world = createWorld();
  const tablet = addNode(world, { nodeId: "tab", priority: 1 });
  const server = addNode(world, { nodeId: "srv", priority: 0 });
  tablet.boot();
  await world.clock.advance(60_000);
  assert.deepEqual(owners(world), ["tab"]);
  assert.equal(tablet.controller.epoch, 1);

  server.boot();
  await world.clock.advance(60_000);
  assert.deepEqual(owners(world), ["srv"]);
  assert.equal(tablet.controller.state, STATES.MONITOR);
  assert.equal(server.controller.epoch, 2, "server claims with a larger epoch than the tablet's");
  const accepted = server.events.find((event) => event[0] === "accept");
  const started = server.events.find((event) => event[0] === "start");
  assert.ok(accepted && accepted[2] === 1 && accepted[3] === "tab", "tablet snapshot reached the server");
  assert.ok(accepted[1] <= started[1], "tablet data is applied BEFORE the server connects");
  assert.equal(world.maxHolders, 1);
});

test("monitor takes over only after 90 s of silence on all three channels", async () => {
  const world = createWorld();
  const server = addNode(world, { nodeId: "srv", priority: 0 });
  const tablet = addNode(world, { nodeId: "tab", priority: 1 });
  server.boot();
  await world.clock.advance(30_000);
  tablet.boot();
  await world.clock.advance(30_000);
  assert.equal(tablet.controller.state, STATES.MONITOR);

  // Iki kanal dusuk, biri ayakta: devralma YOK.
  server.channels.http = false;
  server.channels.mqtt = false;
  await world.clock.advance(300_000);
  assert.equal(tablet.controller.state, STATES.MONITOR);
  assert.deepEqual(owners(world), ["srv"]);

  server.kill();
  const diedAt = world.clock.now();
  await world.clock.advance(80_000);
  assert.equal(tablet.controller.state, STATES.MONITOR, "no take-over before 90 s");
  await world.clock.advance(60_000);
  assert.deepEqual(owners(world), ["tab"]);
  const started = tablet.events.find((event) => event[0] === "start");
  assert.ok(started[1] - diedAt >= 90_000, `took over after ${started[1] - diedAt} ms`);
  assert.equal(world.maxHolders, 1);
});

test("tablet with its own network down does not take over", async () => {
  const world = createWorld();
  const server = addNode(world, { nodeId: "srv", priority: 0 });
  const tablet = addNode(world, { nodeId: "tab", priority: 1 });
  server.boot();
  await world.clock.advance(30_000);
  tablet.boot();
  await world.clock.advance(30_000);
  tablet.networkUp = false;
  server.kill();
  await world.clock.advance(600_000);
  assert.equal(tablet.controller.state, STATES.MONITOR);
  assert.deepEqual(owners(world), []);
});

test("without a release confirmation the server never connects and keeps warning", async () => {
  const world = createWorld();
  const tablet = addNode(world, { nodeId: "tab", priority: 1 });
  const server = addNode(world, { nodeId: "srv", priority: 0 });
  tablet.boot();
  await world.clock.advance(60_000);
  tablet.releaseReachable = false; // tablet ayakta, ama release ucu yanit vermiyor
  server.boot();
  await world.clock.advance(20 * 60_000);
  assert.deepEqual(owners(world), ["tab"]);
  assert.equal(server.controller.state, STATES.AWAITING_RELEASE);
  assert.match(server.controller.status().warning, /not released/);
  assert.ok(server.controller.status().releaseAttempts >= 30, "retries every 30 s, does not give up");
  assert.equal(server.events.filter((event) => event[0] === "start").length, 0);

  tablet.releaseReachable = true;
  await world.clock.advance(60_000);
  assert.deepEqual(owners(world), ["srv"]);
  assert.equal(world.maxHolders, 1);
});

test("a coordinator socket that stays open blocks the release and the server stays off", async () => {
  const world = createWorld();
  const tablet = addNode(world, { nodeId: "tab", priority: 1 });
  const server = addNode(world, { nodeId: "srv", priority: 0 });
  tablet.boot();
  await world.clock.advance(60_000);
  tablet.socketStaysOpen = true;
  server.boot();
  await world.clock.advance(10 * 60_000);
  assert.equal(server.events.filter((event) => event[0] === "start").length, 0);
  assert.equal(tablet.controller.state, STATES.HALTED);
  assert.equal(tablet.controller.self().state, "releasing", "keeps announcing releasing, never standby");
  assert.equal(world.maxHolders, 1);
});

test("simultaneous boots end with exactly one owner whatever the jitter", async () => {
  for (const random of [0, 0.1, 0.33, 0.5, 0.99]) {
    const world = createWorld();
    const server = addNode(world, { nodeId: "srv", priority: 0 });
    const tablet = addNode(world, { nodeId: "tab", priority: 1, random });
    server.boot();
    tablet.boot();
    await world.clock.advance(5 * 60_000);
    assert.equal(world.maxHolders, 1, `jitter ${random}`);
    assert.deepEqual(owners(world), ["srv"], `jitter ${random}`);
    assert.equal(tablet.controller.state, STATES.MONITOR, `jitter ${random}`);
  }
});

test("a node whose stack fails backs off instead of hammering the coordinator", async () => {
  const world = createWorld();
  const tablet = addNode(world, { nodeId: "tab", priority: 1 });
  tablet.startFails = true;
  tablet.boot();
  await world.clock.advance(10 * 60_000);
  const starts = tablet.events.filter((event) => event[0] === "start").map((event) => event[1]);
  assert.ok(starts.length >= 2 && starts.length <= 5, `${starts.length} starts in 10 min`);
  assert.ok(starts[1] - starts[0] >= 60_000, "at least 60 s between attempts");
  assert.equal(world.holders.size, 0);
  assert.equal(world.maxHolders, 1);
});

test("a lower-priority owner yields when it sees the server owning the same coordinator", async () => {
  const world = createWorld();
  const tablet = addNode(world, { nodeId: "tab", priority: 1 });
  const server = addNode(world, { nodeId: "srv", priority: 0 });
  tablet.boot();
  await world.clock.advance(60_000);
  // Bolunmus beyin benzetimi: sunucu tableti hic gormeden sahip olmus gibi davranir.
  server.boot();
  tablet.channels.beacon = false;
  tablet.releaseReachable = false;
  await world.clock.advance(5_000);
  tablet.channels.beacon = true;
  await world.clock.advance(5 * 60_000);
  assert.ok(owners(world).length <= 1);
});

test("persisted epoch survives a restart and keeps growing", async () => {
  const world = createWorld();
  const tablet = addNode(world, { nodeId: "tab", priority: 1 });
  tablet.boot();
  await world.clock.advance(60_000);
  assert.equal(tablet.controller.epoch, 1);
  tablet.kill();
  tablet.boot();
  await world.clock.advance(60_000);
  assert.equal(tablet.controller.epoch, 2);
  assert.equal(tablet.store.value.epoch, 2);
});
