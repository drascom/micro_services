import { createHash } from "node:crypto";
import { createSocket, type Socket } from "node:dgram";
import { hostname } from "node:os";

export const villaBridgeDiscoveryProtocol = "villa-bridge-lan";
export const villaBridgeDiscoveryVersion = 1;
export const villaBridgeDiscoveryQuery = "VILLA_BRIDGE_DISCOVER_V1";
export const villaBridgeDiscoveryPort = 8093;

export type VillaBridgeNodeRole = "server" | "android" | "disabled";

/** Sahiplik beyanı (tablet-failover-plani.md §3.1). */
export type VillaBridgeOwnershipState = "owner" | "standby" | "claiming" | "releasing";

export interface VillaBridgeDiscoveryRecord {
  protocol: typeof villaBridgeDiscoveryProtocol;
  version: typeof villaBridgeDiscoveryVersion;
  role: VillaBridgeNodeRole;
  mode: string;
  dashboardPort: number;
  /** Kalıcı, cihaza özgü kimlik; tie-break ve log için. */
  nodeId: string;
  state: VillaBridgeOwnershipState;
  /** Her sahiplik devrinde artan sayaç; bayat düğüm ayırt edilir. */
  epoch: number;
  /** Koordinatör adresinin özeti; farklı koordinatörlü düğümler birbirini kilitlemesin. */
  coordinatorId: string | null;
  /** Sabit öncelik: server = 0, diğerleri = 1. Küçük olan kazanır. */
  priority: number;
  /** Kaydın gönderildiği an (epoch ms); bayatlama tespiti için. */
  sentAt: number;
}

export interface VillaBridgeDiscoveryRecordOptions {
  nodeId?: string;
  state?: VillaBridgeOwnershipState;
  epoch?: number;
  coordinatorId?: string | null;
  priority?: number;
}

export interface LanDiscoveryResponder {
  port: number;
  close: () => Promise<void>;
}

export const resolveVillaBridgeNodeRole = (
  value = process.env.VILLA_BRIDGE_NODE_ROLE
): VillaBridgeNodeRole => {
  const normalized = value?.trim().toLowerCase();
  if (normalized === "server" || normalized === "android" || normalized === "disabled") {
    return normalized;
  }
  return process.platform === "android" ? "android" : "server";
};

export const villaBridgeNodePriority = (role: VillaBridgeNodeRole): number =>
  (role === "server" ? 0 : 1);

const shortDigest = (value: string, length = 12): string =>
  createHash("sha256").update(value).digest("hex").slice(0, length);

/** Koordinatör adresini (ör. `tcp://192.168.0.248:6638`) özet olarak taşır, adresi sızdırmaz. */
export const villaBridgeCoordinatorId = (value: string | null | undefined): string | null => {
  const normalized = value?.trim().toLowerCase();
  return normalized ? shortDigest(normalized, 16) : null;
};

/** Yeniden başlatmalar arasında sabit kalan düğüm kimliği. */
export const resolveVillaBridgeNodeId = (
  role: VillaBridgeNodeRole,
  value = process.env.VILLA_BRIDGE_NODE_ID,
  host = hostname()
): string => {
  const normalized = value?.trim();
  if (normalized) return normalized.slice(0, 64);
  const prefix = role === "server" ? "srv" : role === "android" ? "tab" : "node";
  return `${prefix}-${shortDigest(`${role}:${host}`, 10)}`;
};

export const createVillaBridgeDiscoveryRecord = (
  role: VillaBridgeNodeRole,
  mode: string,
  dashboardPort: number,
  options: VillaBridgeDiscoveryRecordOptions = {}
): VillaBridgeDiscoveryRecord => ({
  protocol: villaBridgeDiscoveryProtocol,
  version: villaBridgeDiscoveryVersion,
  role,
  mode,
  dashboardPort,
  nodeId: options.nodeId ?? resolveVillaBridgeNodeId(role),
  state: options.state ?? "standby",
  epoch: Number.isInteger(options.epoch) ? Number(options.epoch) : 0,
  coordinatorId: options.coordinatorId ?? null,
  priority: Number.isInteger(options.priority) ? Number(options.priority) : villaBridgeNodePriority(role),
  sentAt: Date.now()
});

/** Koordinatör oturumunun durumu; `state` bu değerden türetilir. */
export type VillaBridgeCoordinatorStatus = "starting" | "ready" | "coordinator-unavailable";

/**
 * Sahiplik beyanı **rolden değil koordinatör sahipliğinden** türetilir
 * (tablet-failover-plani.md §3.2, §4.7): koordinatör gerçekten alındıysa `owner`, aksi hâlde
 * `standby`. Bu kural sunucu ve Android için aynıdır.
 */
export const ownershipStateForCoordinator = (
  status: VillaBridgeCoordinatorStatus
): VillaBridgeOwnershipState => (status === "ready" ? "owner" : "standby");

/**
 * Duyuru kaydının sahiplik alanlarını koordinatör sonucuna göre günceller.
 * Sahiplik her kazanıldığında `epoch` artar (§3.1); bırakıldığında artmaz.
 */
export const applyCoordinatorOwnership = (
  record: VillaBridgeDiscoveryRecord,
  status: VillaBridgeCoordinatorStatus
): VillaBridgeDiscoveryRecord => {
  const next = ownershipStateForCoordinator(status);
  if (next === record.state) return record;
  record.state = next;
  if (next === "owner") record.epoch += 1;
  return record;
};

/** Ağdan okunan duyuru kaydı; eksik yeni alanlar `null` = "bilinmiyor" olur. */
export const parseVillaBridgeDiscoveryRecord = (
  message: Buffer | string
): VillaBridgeDiscoveryRecord | null => {
  try {
    const value = JSON.parse(
      typeof message === "string" ? message : message.toString("utf8")
    ) as Partial<VillaBridgeDiscoveryRecord> | null;
    const dashboardPort = Number(value?.dashboardPort);
    if (
      value?.protocol !== villaBridgeDiscoveryProtocol
      || value.version !== villaBridgeDiscoveryVersion
      || (value.role !== "server" && value.role !== "android" && value.role !== "disabled")
      || typeof value.mode !== "string"
      || !Number.isInteger(dashboardPort)
      || dashboardPort < 1
      || dashboardPort > 65535
    ) {
      return null;
    }
    const role = value.role;
    const state = value.state;
    return {
      protocol: villaBridgeDiscoveryProtocol,
      version: villaBridgeDiscoveryVersion,
      role,
      mode: value.mode,
      dashboardPort,
      nodeId: typeof value.nodeId === "string" && value.nodeId.trim() ? value.nodeId.trim() : "",
      state: state === "owner" || state === "standby" || state === "claiming" || state === "releasing"
        ? state
        : "standby",
      epoch: Number.isInteger(value.epoch) && Number(value.epoch) >= 0 ? Number(value.epoch) : 0,
      coordinatorId: typeof value.coordinatorId === "string" && value.coordinatorId
        ? value.coordinatorId
        : null,
      priority: Number.isInteger(value.priority) ? Number(value.priority) : villaBridgeNodePriority(role),
      sentAt: Number.isInteger(value.sentAt) && Number(value.sentAt) > 0 ? Number(value.sentAt) : 0
    };
  } catch {
    return null;
  }
};

export interface LanDiscoveryQueryOptions {
  /** Hedef adres; verilmezse yayın adresine sorulur. */
  address?: string;
  port?: number;
  timeoutMs?: number;
  /** Kendi kaydını yok saymak için. */
  selfNodeId?: string | null;
}

export interface LanDiscoveryPeer extends VillaBridgeDiscoveryRecord {
  address: string;
}

/** Tek turluk duyuru sorgusu: ilk geçerli yabancı kaydı döner, yoksa `null`. */
export const queryLanDiscovery = (
  options: LanDiscoveryQueryOptions = {}
): Promise<LanDiscoveryPeer | null> => {
  const target = options.address?.trim() || "255.255.255.255";
  const port = options.port ?? villaBridgeDiscoveryPort;
  const timeoutMs = Math.max(100, options.timeoutMs ?? 2000);
  const selfNodeId = options.selfNodeId?.trim() || null;
  return new Promise<LanDiscoveryPeer | null>((resolve) => {
    const socket = createSocket({ type: "udp4" });
    let finished = false;
    const done = (result: LanDiscoveryPeer | null): void => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      try {
        socket.close();
      } catch {
        // Zaman aşımı bind tamamlanmadan gelebilir.
      }
      resolve(result);
    };
    const timer = setTimeout(() => done(null), timeoutMs);
    timer.unref();
    socket.on("error", () => done(null));
    socket.on("message", (message, remote) => {
      const record = parseVillaBridgeDiscoveryRecord(message);
      if (!record || (selfNodeId !== null && record.nodeId === selfNodeId)) return;
      done({ ...record, address: remote.address });
    });
    socket.bind(0, "0.0.0.0", () => {
      try {
        if (target.endsWith(".255")) socket.setBroadcast(true);
        socket.send(Buffer.from(villaBridgeDiscoveryQuery), port, target);
      } catch {
        done(null);
      }
    });
  });
};

export const startLanDiscoveryResponder = async (
  record: VillaBridgeDiscoveryRecord,
  port = villaBridgeDiscoveryPort
): Promise<LanDiscoveryResponder | null> => {
  if (record.role === "disabled") return null;
  const socket: Socket = createSocket({ type: "udp4", reuseAddr: true });
  socket.on("error", () => undefined);
  socket.on("message", (message, remote) => {
    if (message.toString("utf8") !== villaBridgeDiscoveryQuery) return;
    // Kayıt gönderim anında damgalanır: `state`/`epoch` değişirse duyuru da güncel kalır.
    socket.send(Buffer.from(JSON.stringify({ ...record, sentAt: Date.now() })), remote.port, remote.address);
  });

  await new Promise<void>((resolve, reject) => {
    const onError = (error: Error): void => {
      socket.off("listening", onListening);
      reject(error);
    };
    const onListening = (): void => {
      socket.off("error", onError);
      resolve();
    };
    socket.once("error", onError);
    socket.once("listening", onListening);
    socket.bind(port, "0.0.0.0");
  });

  const address = socket.address();
  return {
    port: typeof address === "object" ? address.port : port,
    close: () => new Promise<void>((resolve) => {
      socket.close(() => resolve());
    })
  };
};

export interface LanPeersQueryOptions {
  windowMs?: number;
  port?: number;
  selfNodeId?: string | null;
}

/**
 * Pencere boyunca LAN'daki TÜM yabancı kayıtları toplar (yayın sorgusu 2 sn'de bir yinelenir).
 * Düğüm başına en son kayıt kalır. Talep-et-bekle kapısı bunu kullanır.
 */
export const queryLanPeers = (options: LanPeersQueryOptions = {}): Promise<LanDiscoveryPeer[]> => {
  const windowMs = Math.max(500, options.windowMs ?? 6000);
  const port = options.port ?? villaBridgeDiscoveryPort;
  const selfNodeId = options.selfNodeId?.trim() || null;
  return new Promise<LanDiscoveryPeer[]>((resolve) => {
    const socket = createSocket({ type: "udp4" });
    const found = new Map<string, LanDiscoveryPeer>();
    let finished = false;
    let repeat: NodeJS.Timeout | null = null;
    const done = (): void => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      if (repeat) clearInterval(repeat);
      try {
        socket.close();
      } catch {
        // Bind tamamlanmadan kapanabilir.
      }
      resolve([...found.values()]);
    };
    const timer = setTimeout(done, windowMs);
    timer.unref();
    socket.on("error", done);
    socket.on("message", (message, remote) => {
      const record = parseVillaBridgeDiscoveryRecord(message);
      if (!record || (selfNodeId !== null && record.nodeId === selfNodeId)) return;
      found.set(record.nodeId || remote.address, { ...record, address: remote.address });
    });
    socket.bind(0, "0.0.0.0", () => {
      const send = (): void => {
        try {
          socket.send(Buffer.from(villaBridgeDiscoveryQuery), port, "255.255.255.255");
        } catch {
          // Bir tur kaçarsa sonraki tur yeniden sorar.
        }
      };
      try {
        socket.setBroadcast(true);
      } catch {
        done();
        return;
      }
      send();
      repeat = setInterval(send, 2000);
      repeat.unref();
    });
  });
};
