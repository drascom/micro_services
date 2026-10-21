/**
 * Failover kararlarının SAF çekirdeği (docs/tablet-failover-plani.md §3, §4, §6).
 * İkizi: apps/runtime/failover-policy.cjs — iki dosya aynı tabloyla test edilir
 * (test/failover.test.mjs), davranışları ayrışamaz. Ağ, dosya, saat bilmez.
 */
export const snapshotFormat = 1;

/** Replikasyon kapsamı (veri dizinine göre göreli yol). Platforma özel dosyalar ve Matter dışarıda. */
export const snapshotFiles: readonly string[] = Object.freeze([
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

export const snapshotDirectories: readonly { prefix: string; extensions: string[] }[] = Object.freeze([
  { prefix: "zigbee/external_converters/", extensions: [".js", ".cjs", ".mjs", ".ts"] }
]);

export const networkFields = ["pan_id", "ext_pan_id", "network_key", "channel", "adapter"] as const;
export type NetworkField = (typeof networkFields)[number];

export interface NetworkIdentity {
  pan_id: number | null;
  ext_pan_id: number[] | null;
  network_key: number[] | null;
  channel: number | null;
  adapter: string | null;
}

export interface FailoverPeer {
  nodeId?: string | null;
  priority?: number | null;
  coordinatorId?: string | null;
  epoch?: number | null;
  state?: string | null;
}

export const normalizeSnapshotPath = (value: unknown): string | null => {
  if (typeof value !== "string" || value.length === 0 || value.length > 200) return null;
  const normalized = value.replace(/\\/g, "/");
  if (normalized.startsWith("/") || /^[A-Za-z]:/.test(normalized)) return null;
  const parts = normalized.split("/");
  if (parts.some((part) => part === "" || part === "." || part === "..")) return null;
  return parts.join("/");
};

export const isInSnapshotScope = (value: unknown): boolean => {
  const normalized = normalizeSnapshotPath(value);
  if (normalized === null) return false;
  if (snapshotFiles.includes(normalized)) return true;
  return snapshotDirectories.some((directory) => {
    if (!normalized.startsWith(directory.prefix)) return false;
    const rest = normalized.slice(directory.prefix.length);
    return rest.length > 0
      && !rest.includes("/")
      && !rest.startsWith(".")
      && directory.extensions.some((extension) => rest.endsWith(extension));
  });
};

const asNumber = (value: unknown): number | null => {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value.trim());
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
};

const asByteList = (value: unknown): number[] | null => {
  if (!Array.isArray(value) || value.length === 0) return null;
  const bytes = value.map(asNumber);
  return bytes.every((byte) => Number.isInteger(byte) && (byte as number) >= 0 && (byte as number) <= 255)
    ? bytes as number[]
    : null;
};

/** Zigbee ağ kimliği; eksik alan `null` = bilinmiyor (asla "aynı" sayılmaz). */
export const networkIdentity = (z2m: unknown): NetworkIdentity => {
  const root = z2m && typeof z2m === "object" ? z2m as Record<string, unknown> : {};
  const advanced = (root.advanced && typeof root.advanced === "object" ? root.advanced : {}) as Record<string, unknown>;
  const serial = (root.serial && typeof root.serial === "object" ? root.serial : {}) as Record<string, unknown>;
  const panId = asNumber(advanced.pan_id);
  const channel = asNumber(advanced.channel);
  return {
    pan_id: Number.isInteger(panId) ? panId : null,
    ext_pan_id: asByteList(advanced.ext_pan_id),
    network_key: asByteList(advanced.network_key),
    channel: Number.isInteger(channel) ? channel : null,
    adapter: typeof serial.adapter === "string" && serial.adapter.trim() ? serial.adapter.trim() : null
  };
};

export interface NetworkComparison {
  match: boolean;
  mismatched: NetworkField[];
  unknown: NetworkField[];
}

export const compareNetworkIdentity = (
  local: Partial<NetworkIdentity> | null | undefined,
  remote: Partial<NetworkIdentity> | null | undefined
): NetworkComparison => {
  const mismatched: NetworkField[] = [];
  const unknown: NetworkField[] = [];
  for (const field of networkFields) {
    const a = local ? local[field] : null;
    const b = remote ? remote[field] : null;
    if (a === null || a === undefined || b === null || b === undefined) {
      unknown.push(field);
    } else if (JSON.stringify(a) !== JSON.stringify(b)) {
      mismatched.push(field);
    }
  }
  return { match: mismatched.length === 0 && unknown.length === 0, mismatched, unknown };
};

/** Küçük `priority` kazanır; eşitlikte `nodeId` sözlük sırasında küçük olan. */
export const outranks = (a: FailoverPeer | null | undefined, b: FailoverPeer | null | undefined): boolean => {
  const pa = Number.isInteger(a?.priority) ? a!.priority as number : 1;
  const pb = Number.isInteger(b?.priority) ? b!.priority as number : 1;
  if (pa !== pb) return pa < pb;
  return String(a?.nodeId || "") < String(b?.nodeId || "");
};

/** Bilinmeyen koordinatör kimliği aynı sayılır: şüphede dur. */
export const sameCoordinator = (self: FailoverPeer | null | undefined, peer: FailoverPeer | null | undefined): boolean => {
  if (!self?.coordinatorId || !peer?.coordinatorId) return true;
  return self.coordinatorId === peer.coordinatorId;
};

export interface PeerEvaluation<T extends FailoverPeer> {
  owner: T | null;
  claimant: T | null;
  maxEpoch: number;
}

export const evaluatePeers = <T extends FailoverPeer>(
  self: FailoverPeer | null | undefined,
  peers: readonly T[] | null | undefined
): PeerEvaluation<T> => {
  let owner: T | null = null;
  let claimant: T | null = null;
  let maxEpoch = 0;
  for (const peer of Array.isArray(peers) ? peers : []) {
    if (!peer || (self?.nodeId && peer.nodeId === self.nodeId)) continue;
    const epoch = Number.isInteger(peer.epoch) && (peer.epoch as number) >= 0 ? peer.epoch as number : 0;
    maxEpoch = Math.max(maxEpoch, epoch);
    if (!sameCoordinator(self, peer)) continue;
    if (peer.state === "owner" || peer.state === "releasing") {
      if (!owner || outranks(peer, owner)) owner = peer;
    } else if (peer.state === "claiming" && outranks(peer, self)) {
      if (!claimant || outranks(peer, claimant)) claimant = peer;
    }
  }
  return { owner, claimant, maxEpoch };
};

export type ClaimVerdict = "proceed" | "abort-owner" | "abort-claimant";

export const claimVerdict = <T extends FailoverPeer>(
  self: FailoverPeer,
  peers: readonly T[]
): { verdict: ClaimVerdict; peer: T | null; maxEpoch: number } => {
  const seen = evaluatePeers(self, peers);
  if (seen.owner) return { verdict: "abort-owner", peer: seen.owner, maxEpoch: seen.maxEpoch };
  if (seen.claimant) return { verdict: "abort-claimant", peer: seen.claimant, maxEpoch: seen.maxEpoch };
  return { verdict: "proceed", peer: null, maxEpoch: seen.maxEpoch };
};

export const ownerConflictVerdict = <T extends FailoverPeer>(
  self: FailoverPeer,
  peers: readonly T[]
): { verdict: "keep" | "yield"; peer: T | null } => {
  const seen = evaluatePeers(self, peers);
  if (seen.owner && seen.owner.state === "owner" && outranks(seen.owner, self)) {
    return { verdict: "yield", peer: seen.owner };
  }
  return { verdict: "keep", peer: seen.owner };
};

export const syncDirection = (selfState: string | null | undefined, peerState: string | null | undefined): "serve" | "pull" | "none" => {
  if (selfState === "owner") return "serve";
  if (peerState === "owner") return "pull";
  return "none";
};

export interface SnapshotVersion {
  format?: number;
  epoch?: number;
  digest?: string;
  createdAt?: number;
  ownerNodeId?: string | null;
  network?: Partial<NetworkIdentity> | null;
}

export interface SnapshotDecision {
  accept: boolean;
  reason: "invalid" | "own-snapshot" | "network-mismatch" | "stale-epoch" | "unchanged" | "older-than-replica" | "newer";
  halt: boolean;
  network?: NetworkComparison;
}

export const decideSnapshot = (input: {
  incoming: SnapshotVersion | null | undefined;
  localNetwork: Partial<NetworkIdentity> | null | undefined;
  knownEpoch?: number;
  replica?: { epoch?: number; digest?: string; createdAt?: number } | null;
  selfNodeId?: string | null;
}): SnapshotDecision => {
  const incoming = input.incoming;
  if (!incoming || incoming.format !== snapshotFormat || !Number.isInteger(incoming.epoch)
    || (incoming.epoch as number) < 0 || typeof incoming.digest !== "string"
    || !Number.isFinite(incoming.createdAt)) {
    return { accept: false, reason: "invalid", halt: false };
  }
  const epoch = incoming.epoch as number;
  if (input.selfNodeId && incoming.ownerNodeId === input.selfNodeId) {
    return { accept: false, reason: "own-snapshot", halt: false };
  }
  const network = compareNetworkIdentity(input.localNetwork, incoming.network);
  if (!network.match) return { accept: false, reason: "network-mismatch", halt: true, network };
  const known = Number.isInteger(input.knownEpoch) ? input.knownEpoch as number : 0;
  if (epoch < known) return { accept: false, reason: "stale-epoch", halt: false };
  const replica = input.replica;
  if (replica && Number.isInteger(replica.epoch)) {
    if (epoch < (replica.epoch as number)) return { accept: false, reason: "stale-epoch", halt: false };
    if (epoch === replica.epoch) {
      if (incoming.digest === replica.digest) return { accept: false, reason: "unchanged", halt: false };
      if ((incoming.createdAt as number) < (replica.createdAt ?? 0)) {
        return { accept: false, reason: "older-than-replica", halt: false };
      }
    }
  }
  return { accept: true, reason: "newer", halt: false };
};

export const shouldApplyReplica = (
  replica: { epoch?: number } | null | undefined,
  liveEpoch: number | null | undefined
): boolean => {
  if (!replica || !Number.isInteger(replica.epoch)) return false;
  return (replica.epoch as number) > (Number.isInteger(liveEpoch) ? liveEpoch as number : -1);
};
