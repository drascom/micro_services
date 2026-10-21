"use strict";

// Faz 5 — otomatik devralma ve duzenli devir (docs/tablet-failover-plani.md §3, §4).
//
// Tek yazar, devredilebilir rol. Bu modul durum makinesidir; ag, dosya, cekirdek ve saat
// disaridan verilir (testlerde sahte saat ve sahte LAN ile iki dugum birlikte kosar).
//
//   discovering -> (sahip var) monitor | awaiting-release ; (sahip yok) claiming
//   monitor     -> (90 sn uc kanal sessiz + kendi agi saglam) claiming
//   claiming    -> jitter -> talep penceresi -> epoch+1 -> owner beacon -> 1 sn -> cekirdek
//   owner       -> (oncelikli dugum release isterse) releasing -> monitor
//   awaiting-release -> release onayi + beacon dogrulamasi + snapshot -> claiming
//                       onay yoksa BAGLANMAZ, uyarir, 30 sn'de bir yeniden ister
//   degraded    -> ustel geri cekilme (60 sn -> 15 dk) -> discovering
//   halted      -> suphede dur: elle mudahale gerekir (ag kimligi uyusmazligi, kapanmayan soket)
//
// Cekirdek hicbir durumda iki kez baslatilmaz; yigin gecisleri tek bir kilitten gecer.

const fs = require("node:fs");
const path = require("node:path");
const {
  claimVerdict,
  evaluatePeers,
  outranks,
  ownerConflictVerdict
} = require("./failover-policy.cjs");

const STATES = Object.freeze({
  DISCOVERING: "discovering",
  MONITOR: "monitor",
  CLAIMING: "claiming",
  OWNER: "owner",
  RELEASING: "releasing",
  AWAITING_RELEASE: "awaiting-release",
  DEGRADED: "degraded",
  HALTED: "halted"
});

const TIMINGS = Object.freeze({
  /** Acilista ve sunucu donusunde LAN dinleme: en az 6 sn (§4.4/1). */
  discoverWindowMs: 6000,
  discoverIntervalMs: 2000,
  claimWindowMs: 5000,
  preConnectMs: 1000,
  claimJitterMaxMs: 15000,
  pollIntervalMs: 10000,
  releaseRetryMs: 30000,
  releaseVerifyMs: 6000,
  socketCloseTimeoutMs: 10000,
  socketCloseIntervalMs: 250,
  degradedBackoffMs: 60000,
  degradedBackoffMaxMs: 15 * 60000
});

const STATE_FILE = "failover-state.json";

/** Kalici epoch ve canli verinin epoch'u (§4.7: bayat sahiplik dirilmez). */
function createStateStore(dataDir) {
  const file = path.join(dataDir, STATE_FILE);
  return {
    file,
    load() {
      try {
        const value = JSON.parse(fs.readFileSync(file, "utf8"));
        return {
          epoch: Number.isInteger(value?.epoch) && value.epoch >= 0 ? value.epoch : 0,
          liveEpoch: Number.isInteger(value?.liveEpoch) && value.liveEpoch >= 0 ? value.liveEpoch : null
        };
      } catch {
        return { epoch: 0, liveEpoch: null };
      }
    },
    save(value) {
      const temporary = `${file}.${process.pid}.tmp`;
      fs.writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
      fs.renameSync(temporary, file);
    }
  };
}

function announcedState(state, claimPhase) {
  if (state === STATES.OWNER) return "owner";
  if (state === STATES.RELEASING) return "releasing";
  if (state === STATES.HALTED && claimPhase === "release-failed") return "releasing";
  if (state === STATES.CLAIMING) {
    if (claimPhase === "window") return "claiming";
    if (claimPhase === "owner-pending" || claimPhase === "starting") return "owner";
  }
  return "standby";
}

function createSerialLock() {
  let tail = Promise.resolve();
  return (task) => {
    const run = tail.then(task, task);
    tail = run.catch(() => undefined);
    return run;
  };
}

/**
 * deps:
 *   nodeId, priority, coordinatorId, role
 *   now(), sleep(ms), random()
 *   store: { load(), save(value) }
 *   discover(): Promise<peer[]>              — LAN'daki diger dugumlerin guncel kayitlari
 *   probePeer(peer): Promise<{beacon,http,mqtt,record?}> — uc kanalli yoklama
 *   createPeerWatch(): { record(probe) -> status }
 *   networkHealthy(): Promise<boolean>        — kendi ag gecidine erisim
 *   startStack({epoch}): Promise<{ok, error?}> — broker + cekirdek (koordinator burada alinir)
 *   stopStack(): Promise<void>
 *   coordinatorConnectionOpen(): boolean|null — null = olculemiyor
 *   requestRelease(peer, self): Promise<{ok, released, epoch?, snapshot?, error?}>
 *   buildHandoverSnapshot({epoch}): Promise<object|null>
 *   acceptHandoverSnapshot(snapshot): Promise<{ok, halt?, reason?}>
 *   prepareLiveData({epoch}): Promise<{ok, halt?, reason?}> — devralma oncesi replika -> canli
 *   onChange(status), log(message)
 */
function createFailoverController(deps) {
  const t = { ...TIMINGS, ...(deps.timings || {}) };
  const now = deps.now || Date.now;
  const sleep = deps.sleep;
  const random = deps.random || Math.random;
  const log = deps.log || ((message) => console.log(message));
  const lock = createSerialLock();
  const persisted = deps.store.load();

  let state = STATES.DISCOVERING;
  let claimPhase = null;
  let epoch = persisted.epoch;
  let liveEpoch = persisted.liveEpoch;
  let knownEpoch = persisted.epoch;
  let peer = null;
  let watch = deps.createPeerWatch();
  let watchStatus = null;
  let stackUp = false;
  let stopped = false;
  let claimAborted = false;
  let backoffMs = t.degradedBackoffMs;
  let lastError = null;
  let warning = null;
  let changedAt = now();
  let lastReleaseAttemptAt = null;
  let releaseAttempts = 0;
  let wake = null;

  const self = () => ({
    nodeId: deps.nodeId,
    priority: deps.priority,
    coordinatorId: deps.coordinatorId,
    epoch,
    state: announcedState(state, claimPhase)
  });

  const status = () => ({
    state,
    announced: announcedState(state, claimPhase),
    nodeId: deps.nodeId,
    priority: deps.priority,
    epoch,
    knownEpoch,
    liveEpoch,
    stackUp,
    changedAt,
    peer: peer ? {
      nodeId: peer.nodeId || null,
      address: peer.address || null,
      state: peer.state || null,
      epoch: Number.isInteger(peer.epoch) ? peer.epoch : null,
      dashboardPort: Number.isInteger(peer.dashboardPort) ? peer.dashboardPort : null,
      failoverPort: Number.isInteger(peer.failoverPort) ? peer.failoverPort : null
    } : null,
    peerWatch: watchStatus,
    warning,
    error: lastError,
    releaseAttempts
  });

  const notify = () => {
    try {
      deps.onChange?.(status());
    } catch {
      // Bildirim hatasi durum makinesini durdurmaz.
    }
  };

  const setState = (next, reason) => {
    if (next === state) return;
    log(`Failover: ${state} -> ${next}${reason ? ` (${reason})` : ""}`);
    state = next;
    changedAt = now();
    // HALTED, "release-failed" isaretini korur: o durumda `releasing` duyurulmaya devam eder.
    if (next !== STATES.CLAIMING && next !== STATES.HALTED) claimPhase = null;
    notify();
  };

  const persist = () => deps.store.save({ epoch, liveEpoch });

  const noteEpochs = (peers) => {
    for (const record of peers) {
      if (Number.isInteger(record?.epoch)) knownEpoch = Math.max(knownEpoch, record.epoch);
    }
  };

  const wait = (ms) => new Promise((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      if (wake === finish) wake = null;
      resolve();
    };
    wake = finish;
    Promise.resolve(sleep(ms)).then(finish, finish);
  });

  /** Pencere boyunca LAN'i dinler; dugum basina en son kayit kalir. */
  const observe = async (windowMs) => {
    const end = now() + windowMs;
    const seen = new Map();
    for (;;) {
      const list = await Promise.resolve(deps.discover()).catch(() => []);
      for (const record of list || []) {
        if (!record || record.nodeId === deps.nodeId) continue;
        seen.set(record.nodeId || record.address, record);
      }
      const left = end - now();
      if (left <= 0 || stopped) break;
      await sleep(Math.min(t.discoverIntervalMs, left));
    }
    const peers = [...seen.values()];
    noteEpochs(peers);
    return peers;
  };

  const startWatching = (record) => {
    peer = record;
    watch = deps.createPeerWatch();
    // Karsi dugum az once goruldu: izleme "hic gorulmemis" sayilmasin, sayac ondan sonra islesin.
    watchStatus = watch.record({
      beacon: true,
      http: true,
      mqtt: true,
      nodeId: record?.nodeId || null,
      address: record?.address || null,
      state: record?.state || null
    });
  };

  const waitSocketsClosed = async () => {
    const end = now() + t.socketCloseTimeoutMs;
    for (;;) {
      const open = deps.coordinatorConnectionOpen ? deps.coordinatorConnectionOpen() : null;
      if (open !== true) return true;
      if (now() >= end) return false;
      await sleep(t.socketCloseIntervalMs);
    }
  };

  /** Acik yigini kapatir ve koordinator baglantisinin gercekten kapandigini dogrular. */
  const tearDown = async () => {
    if (stackUp) {
      await Promise.resolve(deps.stopStack()).catch((error) => log(`Failover: stack stop failed: ${error.message}`));
      stackUp = false;
    }
    return waitSocketsClosed();
  };

  async function stepDiscover() {
    const peers = await observe(t.discoverWindowMs);
    const seen = evaluatePeers(self(), peers);
    if (seen.owner) {
      startWatching(seen.owner);
      if (outranks(self(), seen.owner)) {
        setState(STATES.AWAITING_RELEASE, `${seen.owner.nodeId || seen.owner.address} owns the coordinator`);
      } else {
        setState(STATES.MONITOR, `${seen.owner.nodeId || seen.owner.address} owns the coordinator`);
      }
      return;
    }
    if (seen.claimant) {
      // Bizden once gelen bir talep suruyor: sonucunu bekle.
      await wait(t.discoverIntervalMs);
      return;
    }
    setState(STATES.CLAIMING, "no owner on the LAN");
  }

  async function stepMonitor() {
    const probe = await Promise.resolve(deps.probePeer(peer)).catch(() => ({ beacon: false, http: false, mqtt: false }));
    if (state !== STATES.MONITOR) return;
    if (probe.record) {
      peer = { ...peer, ...probe.record };
      noteEpochs([probe.record]);
    }
    watchStatus = watch.record({
      beacon: probe.beacon === true,
      http: probe.http === true,
      mqtt: probe.mqtt === true,
      nodeId: peer?.nodeId || null,
      address: peer?.address || null,
      state: probe.record?.state || null
    });
    notify();
    if (watchStatus.readyToTakeOver) {
      if (await Promise.resolve(deps.networkHealthy()).catch(() => false)) {
        setState(STATES.CLAIMING, "peer silent on all three channels");
        return;
      }
      warning = "Peer is silent but this node's own network looks down; not taking over.";
    } else {
      warning = null;
    }
    await wait(t.pollIntervalMs);
  }

  async function stepClaim() {
    claimAborted = false;
    claimPhase = "jitter";
    const jitter = deps.priority === 0 ? 0 : Math.floor(random() * t.claimJitterMaxMs);
    if (jitter > 0) await sleep(jitter);
    if (state !== STATES.CLAIMING) return;
    if (claimAborted) {
      setState(STATES.DISCOVERING, "claim cancelled");
      return;
    }
    claimPhase = "window";
    notify();
    const peers = await observe(t.claimWindowMs);
    if (state !== STATES.CLAIMING) return;
    const verdict = claimVerdict(self(), peers);
    if (verdict.verdict !== "proceed" || claimAborted) {
      claimPhase = null;
      if (verdict.peer && verdict.verdict === "abort-owner") {
        startWatching(verdict.peer);
        setState(outranks(self(), verdict.peer) ? STATES.AWAITING_RELEASE : STATES.MONITOR, verdict.verdict);
      } else {
        setState(STATES.DISCOVERING, claimAborted ? "claim cancelled" : verdict.verdict);
      }
      return;
    }
    // Pencere sessiz: epoch kalici olarak artar, owner beacon'i baglanmadan ONCE baslar (§3.2).
    epoch = Math.max(epoch, knownEpoch) + 1;
    knownEpoch = epoch;
    persist();
    claimPhase = "owner-pending";
    notify();
    await sleep(t.preConnectMs);
    const late = await Promise.resolve(deps.discover()).catch(() => []);
    noteEpochs(late || []);
    const lateVerdict = claimVerdict(self(), late || []);
    if (lateVerdict.verdict !== "proceed" || claimAborted || state !== STATES.CLAIMING) {
      claimPhase = null;
      if (state === STATES.CLAIMING) setState(STATES.DISCOVERING, "late beacon or cancelled claim");
      return;
    }
    await lock(async () => {
      if (claimAborted || state !== STATES.CLAIMING) {
        claimPhase = null;
        if (state === STATES.CLAIMING) setState(STATES.DISCOVERING, "claim cancelled");
        return;
      }
      claimPhase = "starting";
      const prepared = await Promise.resolve(deps.prepareLiveData({ epoch, liveEpoch }))
        .catch((error) => ({ ok: false, halt: true, reason: error.message }));
      if (!prepared.ok) {
        lastError = `Replica could not be applied: ${prepared.reason || "unknown"}`;
        if (prepared.halt) {
          warning = lastError;
          setState(STATES.HALTED, "replica rejected");
          return;
        }
      }
      if (prepared.applied) liveEpoch = prepared.epoch;
      liveEpoch = epoch;
      persist();
      const result = await Promise.resolve(deps.startStack({ epoch }))
        .catch((error) => ({ ok: false, error: error.message }));
      stackUp = true;
      if (!result.ok) {
        lastError = result.error || "Stack failed to start.";
        const closed = await tearDown();
        if (!closed) {
          claimPhase = "release-failed";
          warning = "Coordinator connection did not close after a failed start; manual check needed.";
          setState(STATES.HALTED, "socket still open");
          return;
        }
        setState(STATES.DEGRADED, lastError);
        return;
      }
      lastError = null;
      warning = null;
      backoffMs = t.degradedBackoffMs;
      setState(STATES.OWNER, `epoch ${epoch}`);
    });
  }

  async function stepOwner() {
    await wait(t.pollIntervalMs);
    if (state !== STATES.OWNER) return;
    const peers = await Promise.resolve(deps.discover()).catch(() => []);
    noteEpochs(peers || []);
    const verdict = ownerConflictVerdict(self(), peers || []);
    if (verdict.verdict === "yield") {
      // Bolunmus beyin: bizden once gelen bir sahip var. Geride kalan birakir (§4.6).
      await lock(async () => {
        if (state !== STATES.OWNER) return;
        setState(STATES.RELEASING, `higher-priority owner ${verdict.peer.nodeId}`);
        const closed = await tearDown();
        if (!closed) {
          claimPhase = "release-failed";
          setState(STATES.HALTED, "socket still open");
          return;
        }
        startWatching(verdict.peer);
        setState(STATES.MONITOR, "yielded");
      });
    }
  }

  async function stepAwaitRelease() {
    const probe = await Promise.resolve(deps.probePeer(peer)).catch(() => ({ beacon: false, http: false, mqtt: false }));
    if (state !== STATES.AWAITING_RELEASE) return;
    if (probe.record) {
      peer = { ...peer, ...probe.record };
      noteEpochs([probe.record]);
    }
    watchStatus = watch.record({
      beacon: probe.beacon === true,
      http: probe.http === true,
      mqtt: probe.mqtt === true,
      nodeId: peer?.nodeId || null,
      address: peer?.address || null,
      state: probe.record?.state || null
    });
    if (probe.record && probe.record.state === "standby") {
      // Sahip kendiliginden birakti (ya da yeniden basladi): bastan bak.
      setState(STATES.DISCOVERING, "owner is no longer owner");
      return;
    }
    if (watchStatus.readyToTakeOver) {
      // Sahip uc kanalda da 90 sn sessiz: ayni muhafazakar esik, tablet icin oldugu gibi.
      if (await Promise.resolve(deps.networkHealthy()).catch(() => false)) {
        setState(STATES.CLAIMING, "owner silent on all three channels");
        return;
      }
    }
    if (lastReleaseAttemptAt === null || now() - lastReleaseAttemptAt >= t.releaseRetryMs) {
      lastReleaseAttemptAt = now();
      releaseAttempts += 1;
      const answer = await Promise.resolve(deps.requestRelease(peer, self()))
        .catch((error) => ({ ok: false, released: false, error: error.message }));
      if (state !== STATES.AWAITING_RELEASE) return;
      if (answer.ok && answer.released) {
        if (Number.isInteger(answer.epoch)) knownEpoch = Math.max(knownEpoch, answer.epoch);
        // Beacon'in gercekten birakildigini dinleyerek dogrula (§4.4/5).
        const peers = await observe(t.releaseVerifyMs);
        const seen = evaluatePeers(self(), peers);
        if (seen.owner) {
          warning = `${seen.owner.nodeId || "peer"} still announces ${seen.owner.state}; not connecting.`;
          notify();
          await wait(t.pollIntervalMs);
          return;
        }
        if (answer.snapshot) {
          const accepted = await Promise.resolve(deps.acceptHandoverSnapshot(answer.snapshot))
            .catch((error) => ({ ok: false, halt: true, reason: error.message }));
          if (!accepted.ok && accepted.halt) {
            lastError = `Handover snapshot rejected: ${accepted.reason || "unknown"}`;
            warning = lastError;
            setState(STATES.HALTED, "handover snapshot rejected");
            return;
          }
          if (accepted.ok && accepted.applied) {
            liveEpoch = answer.snapshot.epoch;
            persist();
          }
        }
        warning = null;
        releaseAttempts = 0;
        lastReleaseAttemptAt = null;
        setState(STATES.CLAIMING, "peer released the coordinator");
        return;
      }
      lastError = answer.error || "Release was not confirmed.";
      warning = "The other node has not released the coordinator yet; this node will not connect. " +
        "Retrying every 30 s.";
      notify();
    }
    await wait(t.pollIntervalMs);
  }

  async function run() {
    while (!stopped) {
      try {
        switch (state) {
          case STATES.DISCOVERING:
            await stepDiscover();
            break;
          case STATES.MONITOR:
            await stepMonitor();
            break;
          case STATES.CLAIMING:
            await stepClaim();
            break;
          case STATES.OWNER:
            await stepOwner();
            break;
          case STATES.AWAITING_RELEASE:
            await stepAwaitRelease();
            break;
          case STATES.DEGRADED:
            await wait(backoffMs);
            backoffMs = Math.min(backoffMs * 2, t.degradedBackoffMaxMs);
            if (state === STATES.DEGRADED) setState(STATES.DISCOVERING, "retry after back-off");
            break;
          default:
            await wait(t.pollIntervalMs);
        }
      } catch (error) {
        lastError = error.message;
        log(`Failover step failed: ${error.stack || error.message}`);
        await wait(t.pollIntervalMs);
      }
    }
  }

  /**
   * Oncelikli dugumun release istegi (§4.4/3). Yalniz bizden once gelen dugum isteyebilir.
   * Sahipsek: releasing -> yigini durdur -> koordinator soketinin kapandigini dogrula ->
   * son snapshot -> monitor. Soket kapanmazsa onay VERILMEZ ve "releasing" duyurulmaya devam
   * eder: karsi taraf asla baglanmaz.
   */
  function release(requester) {
    if (!requester || !outranks(requester, self())) {
      return Promise.resolve({ ok: false, released: false, status: 403, error: "Only a higher-priority node may request a release." });
    }
    if (state === STATES.CLAIMING) claimAborted = true;
    return lock(async () => {
      if (state === STATES.HALTED && claimPhase === "release-failed") {
        const closed = await waitSocketsClosed();
        if (!closed) return { ok: false, released: false, status: 409, error: "Coordinator connection is still open." };
        claimPhase = null;
      }
      if (state !== STATES.OWNER && !stackUp) {
        if (state === STATES.CLAIMING || state === STATES.DEGRADED || state === STATES.HALTED) {
          setState(STATES.DISCOVERING, "release requested");
        }
        return { ok: true, released: true, epoch: knownEpoch, snapshot: null };
      }
      setState(STATES.RELEASING, `requested by ${requester.nodeId || requester.address || "peer"}`);
      const closed = await tearDown();
      if (!closed) {
        claimPhase = "release-failed";
        lastError = "Coordinator connection is still open after stopping the stack.";
        setState(STATES.HALTED, "socket still open");
        return { ok: false, released: false, status: 409, error: lastError };
      }
      let snapshot = null;
      try {
        snapshot = await deps.buildHandoverSnapshot({ epoch });
      } catch (error) {
        lastError = `Handover snapshot failed: ${error.message}`;
      }
      startWatching({ ...requester, state: requester.state || "standby" });
      setState(STATES.MONITOR, "released");
      if (wake) wake();
      return { ok: true, released: true, epoch, snapshot };
    });
  }

  async function stop() {
    stopped = true;
    if (wake) wake();
    await lock(async () => {
      if (stackUp) await tearDown();
    });
  }

  return {
    run,
    stop,
    release,
    status,
    self,
    get state() {
      return state;
    },
    get epoch() {
      return epoch;
    }
  };
}

module.exports = {
  STATES,
  STATE_FILE,
  TIMINGS,
  announcedState,
  createFailoverController,
  createSerialLock,
  createStateStore
};
