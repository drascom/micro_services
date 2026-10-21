"use strict";

// Failover kararlarinin SAF cekirdegi (docs/tablet-failover-plani.md §3, §4, §6).
// Ag, dosya, saat bilmez; yalniz girdiye bakar. Ikizi: src/failover-policy.ts — iki dosya
// ayni tabloyla test edilir (test/failover.test.mjs), davranislari ayrisamaz.

const SNAPSHOT_FORMAT = 1;

/**
 * Replikasyon kapsami (veri dizinine gore goreli yol). Kullanici karari: otomasyonlar,
 * alias/odalar, panel/UI ayarlari, Zigbee cihaz veritabani, koordinator yedegi, cihaz durumu,
 * harici donusturuculer ve auth.json. Platforma ozel dosyalar (provisioning.json,
 * *-runtime.json, failover-state.json, configuration.yaml, villa-bridge.yaml) ve Matter verisi
 * BILEREK disarida: biri konagi, oteki ayri bir kimligi tarif eder.
 */
const SNAPSHOT_FILES = Object.freeze([
  "aliases.json",
  "runtime/appearance.json",
  "runtime/auth.json",
  "runtime/automation-auto-off.json",
  "runtime/automations.json",
  "runtime/device-images.json",
  "runtime/device-notes.json",
  "runtime/device-roles.json",
  "runtime/home-favorites.json",
  "runtime/home-groups.json",
  "runtime/home-visibility.json",
  "runtime/location.json",
  "runtime/weather-location.json",
  "runtime/world-clock.json",
  "zigbee/coordinator_backup.json",
  "zigbee/database.db",
  "zigbee/state.json"
]);
const SNAPSHOT_DIRECTORIES = Object.freeze([
  { prefix: "zigbee/external_converters/", extensions: [".js", ".cjs", ".mjs", ".ts"] }
]);
const NETWORK_FIELDS = Object.freeze(["pan_id", "ext_pan_id", "network_key", "channel", "adapter"]);

/** Goreli yolu duzler; disari tasan, mutlak ya da garip yol `null`. */
function normalizeSnapshotPath(value) {
  if (typeof value !== "string" || value.length === 0 || value.length > 200) return null;
  const normalized = value.replace(/\\/g, "/");
  if (normalized.startsWith("/") || /^[A-Za-z]:/.test(normalized)) return null;
  const parts = normalized.split("/");
  if (parts.some((part) => part === "" || part === "." || part === "..")) return null;
  return parts.join("/");
}

function isInSnapshotScope(value) {
  const normalized = normalizeSnapshotPath(value);
  if (normalized === null) return false;
  if (SNAPSHOT_FILES.includes(normalized)) return true;
  return SNAPSHOT_DIRECTORIES.some((directory) => {
    if (!normalized.startsWith(directory.prefix)) return false;
    const rest = normalized.slice(directory.prefix.length);
    return rest.length > 0 &&
      !rest.includes("/") &&
      !rest.startsWith(".") &&
      directory.extensions.some((extension) => rest.endsWith(extension));
  });
}

function asNumber(value) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value.trim());
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function asByteList(value) {
  if (!Array.isArray(value) || value.length === 0) return null;
  const bytes = value.map(asNumber);
  return bytes.every((byte) => Number.isInteger(byte) && byte >= 0 && byte <= 255) ? bytes : null;
}

/**
 * Zigbee ag kimligi (configuration.yaml `advanced` + `serial.adapter`) karsilastirilabilir
 * bicimde. Eksik alan `null` = bilinmiyor; bilinmeyen alan ASLA "ayni" sayilmaz.
 */
function networkIdentity(z2m) {
  const advanced = z2m && typeof z2m === "object" ? z2m.advanced || {} : {};
  const serial = z2m && typeof z2m === "object" ? z2m.serial || {} : {};
  const panId = asNumber(advanced.pan_id);
  const channel = asNumber(advanced.channel);
  return {
    pan_id: Number.isInteger(panId) ? panId : null,
    ext_pan_id: asByteList(advanced.ext_pan_id),
    network_key: asByteList(advanced.network_key),
    channel: Number.isInteger(channel) ? channel : null,
    adapter: typeof serial.adapter === "string" && serial.adapter.trim() ? serial.adapter.trim() : null
  };
}

/**
 * Iki ag kimligini karsilastirir. `match` yalniz BES alanin hepsi iki tarafta biliniyor ve
 * esitse `true`. Farkli ya da bilinmeyen alan senkronu DURDURUR (asla uzerine yazilmaz).
 */
function compareNetworkIdentity(local, remote) {
  const mismatched = [];
  const unknown = [];
  for (const field of NETWORK_FIELDS) {
    const a = local ? local[field] : null;
    const b = remote ? remote[field] : null;
    if (a === null || a === undefined || b === null || b === undefined) {
      unknown.push(field);
    } else if (JSON.stringify(a) !== JSON.stringify(b)) {
      mismatched.push(field);
    }
  }
  return { match: mismatched.length === 0 && unknown.length === 0, mismatched, unknown };
}

/** a, b'den once mi gelir? Kucuk `priority` kazanir; esitlikte `nodeId` sozluk sirasinda kucuk olan. */
function outranks(a, b) {
  const pa = Number.isInteger(a?.priority) ? a.priority : 1;
  const pb = Number.isInteger(b?.priority) ? b.priority : 1;
  if (pa !== pb) return pa < pb;
  return String(a?.nodeId || "") < String(b?.nodeId || "");
}

/** Ayni koordinator mu? Bilinmeyen kimlik ayni sayilir: suphede dur (§3.1). */
function sameCoordinator(self, peer) {
  if (!self?.coordinatorId || !peer?.coordinatorId) return true;
  return self.coordinatorId === peer.coordinatorId;
}

/**
 * LAN gozleminin ozeti: koordinatoru tutan (ya da birakmakta olan) baska bir dugum var mi,
 * bizden once gelen bir talep var mi, gorulen en buyuk epoch kac.
 * `releasing` sahip sayilir: o dugum koordinatoru henuz birakmis olmayabilir.
 */
function evaluatePeers(self, peers) {
  let owner = null;
  let claimant = null;
  let maxEpoch = 0;
  for (const peer of Array.isArray(peers) ? peers : []) {
    if (!peer || (self?.nodeId && peer.nodeId === self.nodeId)) continue;
    const epoch = Number.isInteger(peer.epoch) && peer.epoch >= 0 ? peer.epoch : 0;
    maxEpoch = Math.max(maxEpoch, epoch);
    if (!sameCoordinator(self, peer)) continue;
    if (peer.state === "owner" || peer.state === "releasing") {
      if (!owner || outranks(peer, owner)) owner = peer;
    } else if (peer.state === "claiming" && outranks(peer, self)) {
      if (!claimant || outranks(peer, claimant)) claimant = peer;
    }
  }
  return { owner, claimant, maxEpoch };
}

/**
 * Talep penceresinin sonucu (§3.3): sahip duyulduysa ya da bizden once gelen talep varsa
 * iptal; aksi halde devam.
 */
function claimVerdict(self, peers) {
  const seen = evaluatePeers(self, peers);
  if (seen.owner) return { verdict: "abort-owner", peer: seen.owner, maxEpoch: seen.maxEpoch };
  if (seen.claimant) return { verdict: "abort-claimant", peer: seen.claimant, maxEpoch: seen.maxEpoch };
  return { verdict: "proceed", peer: null, maxEpoch: seen.maxEpoch };
}

/**
 * Sahip baska bir sahip gorurse (bolunmus beyin): yalniz bizden once gelen sahip karsisinda
 * geri cekilinir. Geride kalan taraf kendiliginden birakir, oteki tutmaya devam eder.
 */
function ownerConflictVerdict(self, peers) {
  const seen = evaluatePeers(self, peers);
  if (seen.owner && seen.owner.state === "owner" && outranks(seen.owner, self)) {
    return { verdict: "yield", peer: seen.owner };
  }
  return { verdict: "keep", peer: seen.owner };
}

/**
 * Replikasyon yonu: daima aktif sahipten bekleyene. Sahip veri verir, sahip olmayan ve
 * karsisinda sahip goren dugum ceker; ikisi de sahip degilse kimse kimseye yazmaz.
 */
function syncDirection(selfState, peerState) {
  if (selfState === "owner") return "serve";
  if (peerState === "owner") return "pull";
  return "none";
}

/**
 * Gelen snapshot kabul edilsin mi? (§6.2 epoch fencing)
 *   - ag kimligi farkli/bilinmiyor -> RED ve senkron durur (`halt`), asla uzerine yazma;
 *   - epoch bildigimiz en buyuk epoch'tan kucuk -> RED (eski veri yeniyi ezmez);
 *   - ayni epoch'ta ayni icerik -> degisiklik yok; daha eski uretim -> RED.
 */
function decideSnapshot(input) {
  const incoming = input?.incoming;
  if (!incoming || incoming.format !== SNAPSHOT_FORMAT || !Number.isInteger(incoming.epoch) ||
      incoming.epoch < 0 || typeof incoming.digest !== "string" || !Number.isFinite(incoming.createdAt)) {
    return { accept: false, reason: "invalid", halt: false };
  }
  if (input.selfNodeId && incoming.ownerNodeId === input.selfNodeId) {
    return { accept: false, reason: "own-snapshot", halt: false };
  }
  const network = compareNetworkIdentity(input.localNetwork, incoming.network);
  if (!network.match) {
    return { accept: false, reason: "network-mismatch", halt: true, network };
  }
  const known = Number.isInteger(input.knownEpoch) ? input.knownEpoch : 0;
  if (incoming.epoch < known) return { accept: false, reason: "stale-epoch", halt: false };
  const replica = input.replica;
  if (replica && Number.isInteger(replica.epoch)) {
    if (incoming.epoch < replica.epoch) return { accept: false, reason: "stale-epoch", halt: false };
    if (incoming.epoch === replica.epoch) {
      if (incoming.digest === replica.digest) return { accept: false, reason: "unchanged", halt: false };
      if (incoming.createdAt < replica.createdAt) {
        return { accept: false, reason: "older-than-replica", halt: false };
      }
    }
  }
  return { accept: true, reason: "newer", halt: false };
}

/**
 * Replika canli veriye kopyalanmali mi? Yalniz replika canli veriden yeniyse (daha buyuk
 * epoch) — canli veri kendi sahipliginde yazildiysa eski replika onu ezmez.
 */
function shouldApplyReplica(replica, liveEpoch) {
  if (!replica || !Number.isInteger(replica.epoch)) return false;
  return replica.epoch > (Number.isInteger(liveEpoch) ? liveEpoch : -1);
}

module.exports = {
  NETWORK_FIELDS,
  SNAPSHOT_DIRECTORIES,
  SNAPSHOT_FILES,
  SNAPSHOT_FORMAT,
  claimVerdict,
  compareNetworkIdentity,
  decideSnapshot,
  evaluatePeers,
  isInSnapshotScope,
  networkIdentity,
  normalizeSnapshotPath,
  outranks,
  ownerConflictVerdict,
  sameCoordinator,
  shouldApplyReplica,
  syncDirection
};
