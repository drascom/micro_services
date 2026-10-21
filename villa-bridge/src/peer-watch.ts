import { createConnection } from "node:net";
import { request as httpRequest } from "node:http";
import {
  parseVillaBridgeDiscoveryRecord,
  queryLanDiscovery,
  type LanDiscoveryPeer,
  type VillaBridgeOwnershipState
} from "./lan-discovery.js";

/**
 * Üç kanallı karşı düğüm izlemesi (tablet-failover-plani.md §4.2).
 *
 * Bu modül **yalnızca gözlem** yapar: eşiğe ulaşıldığında `readyToTakeOver` bayrağını kaldırır ve
 * loglar. Devralma, talep, release veya mod geçişi burada **yoktur** (Faz 2 kapsamı).
 */
export interface PeerWatchThresholds {
  /** Beacon aralığı: 2 s. */
  beaconIntervalMs: number;
  /** Beacon bayatlama: 15 s. */
  beaconStaleMs: number;
  /** Yoklama aralığı: 10 s. */
  pollIntervalMs: number;
  /** Kanal başına zaman aşımı: 2000 ms. */
  probeTimeoutMs: number;
  /** Devralma eşiği: 9 ardışık üç-kanallı başarısızlık = 90 s. */
  takeoverFailures: number;
  /** Geri çekilme histerezisi: 3 ardışık başarı. */
  recoverySuccesses: number;
  /** Claim öncesi jitter: 0-15 s (Faz 3'te kullanılacak, izleme jitter uygulamaz). */
  claimJitterMaxMs: number;
}

export const peerWatchThresholds: PeerWatchThresholds = {
  beaconIntervalMs: 2_000,
  beaconStaleMs: 15_000,
  pollIntervalMs: 10_000,
  probeTimeoutMs: 2_000,
  takeoverFailures: 9,
  recoverySuccesses: 3,
  claimJitterMaxMs: 15_000
};

export type PeerWatchChannel = "beacon" | "http" | "mqtt";

export interface PeerWatchProbeResult {
  beacon: boolean;
  http: boolean;
  mqtt: boolean;
  nodeId?: string | null;
  address?: string | null;
  state?: VillaBridgeOwnershipState | null;
  mode?: string | null;
}

export interface PeerWatchStatus {
  /** En az bir yoklama yapıldı mı? */
  watching: boolean;
  /** Karşı düğüm hiç görülmediyse sayaçlar ilerlemez. */
  peer: {
    nodeId: string | null;
    address: string | null;
    state: VillaBridgeOwnershipState | null;
    mode: string | null;
  } | null;
  lastProbeAt: number | null;
  lastSeenAt: number | null;
  lastSeenAgeMs: number | null;
  /** Son yoklamada düşen kanallar. */
  downChannels: PeerWatchChannel[];
  consecutiveFailures: number;
  consecutiveSuccesses: number;
  /** Eşiğe kalan üç-kanallı başarısızlık sayısı. */
  failuresToTakeover: number;
  readyToTakeOver: boolean;
  /** Faz 2 boyunca daima "none": izleme hiçbir eylem tetiklemez. */
  action: "none";
  thresholds: PeerWatchThresholds;
}

export interface PeerWatchStateOptions {
  thresholds?: PeerWatchThresholds;
  now?: () => number;
  logger?: (message: string) => void;
}

const downChannelsOf = (probe: PeerWatchProbeResult): PeerWatchChannel[] => {
  const down: PeerWatchChannel[] = [];
  if (!probe.beacon) down.push("beacon");
  if (!probe.http) down.push("http");
  if (!probe.mqtt) down.push("mqtt");
  return down;
};

/** Ardışık sayaçları tutan saf durum makinesi; ağ bilmez, saat enjekte edilir. */
export class PeerWatchState {
  private readonly thresholds: PeerWatchThresholds;
  private readonly now: () => number;
  private readonly logger: (message: string) => void;
  private peer: PeerWatchStatus["peer"] = null;
  private lastProbeAt: number | null = null;
  private lastSeenAt: number | null = null;
  private downChannels: PeerWatchChannel[] = [];
  private consecutiveFailures = 0;
  private consecutiveSuccesses = 0;
  private readyToTakeOver = false;

  constructor(options: PeerWatchStateOptions = {}) {
    this.thresholds = options.thresholds ?? peerWatchThresholds;
    this.now = options.now ?? Date.now;
    this.logger = options.logger ?? ((message) => console.log(message));
  }

  record(probe: PeerWatchProbeResult): PeerWatchStatus {
    const at = this.now();
    this.lastProbeAt = at;
    this.downChannels = downChannelsOf(probe);
    if (probe.nodeId || probe.address) {
      this.peer = {
        nodeId: probe.nodeId ?? this.peer?.nodeId ?? null,
        address: probe.address ?? this.peer?.address ?? null,
        state: probe.state ?? null,
        mode: probe.mode ?? this.peer?.mode ?? null
      };
    }
    // Üç kanalın üçü birden düşmeden yoklama başarısız sayılmaz (İlke 3, §4.2).
    const failed = this.downChannels.length === 3;
    if (!failed) {
      this.lastSeenAt = at;
      this.consecutiveFailures = 0;
      this.consecutiveSuccesses += 1;
      if (this.readyToTakeOver && this.consecutiveSuccesses >= this.thresholds.recoverySuccesses) {
        this.readyToTakeOver = false;
        this.logger("Karşı düğüm yeniden görülüyor; devralmaya hazır işareti kaldırıldı.");
      }
      return this.status();
    }
    this.consecutiveSuccesses = 0;
    // Hiç görülmemiş bir düğüm için sayaç ilerletilmez: yokluk, arıza kanıtı değildir.
    if (this.peer === null) return this.status();
    this.consecutiveFailures += 1;
    if (!this.readyToTakeOver && this.consecutiveFailures >= this.thresholds.takeoverFailures) {
      this.readyToTakeOver = true;
      const seconds = Math.round(
        (this.thresholds.takeoverFailures * this.thresholds.pollIntervalMs) / 1000
      );
      this.logger(
        `Karşı düğüm ${seconds} sn boyunca üç kanalda da görülmedi; devralmaya hazır ` +
        "(Faz 2: hiçbir devralma yapılmıyor, yalnızca raporlanıyor)."
      );
    }
    return this.status();
  }

  status(): PeerWatchStatus {
    const at = this.now();
    return {
      watching: this.lastProbeAt !== null,
      peer: this.peer === null ? null : { ...this.peer },
      lastProbeAt: this.lastProbeAt,
      lastSeenAt: this.lastSeenAt,
      lastSeenAgeMs: this.lastSeenAt === null ? null : Math.max(0, at - this.lastSeenAt),
      downChannels: [...this.downChannels],
      consecutiveFailures: this.consecutiveFailures,
      consecutiveSuccesses: this.consecutiveSuccesses,
      failuresToTakeover: Math.max(0, this.thresholds.takeoverFailures - this.consecutiveFailures),
      readyToTakeOver: this.readyToTakeOver,
      action: "none",
      thresholds: { ...this.thresholds }
    };
  }
}

export interface PeerWatcher {
  start: () => void;
  stop: () => void;
  status: () => PeerWatchStatus;
  /** Testler ve ilk tur için tek yoklama. */
  probeOnce: () => Promise<PeerWatchStatus>;
}

export interface PeerWatcherOptions extends PeerWatchStateOptions {
  probe: () => Promise<PeerWatchProbeResult>;
  intervalMs?: number;
}

export const createPeerWatcher = (options: PeerWatcherOptions): PeerWatcher => {
  const thresholds = options.thresholds ?? peerWatchThresholds;
  const state = new PeerWatchState({ ...options, thresholds });
  const intervalMs = Math.max(50, options.intervalMs ?? thresholds.pollIntervalMs);
  const logger = options.logger ?? ((message: string) => console.log(message));
  let timer: NodeJS.Timeout | null = null;
  let running = false;
  let busy = false;

  const probeOnce = async (): Promise<PeerWatchStatus> => {
    try {
      return state.record(await options.probe());
    } catch (error) {
      logger(`Karşı düğüm yoklaması başarısız: ${String(error)}`);
      return state.record({ beacon: false, http: false, mqtt: false });
    }
  };

  const tick = (): void => {
    if (busy) return;
    busy = true;
    void probeOnce().finally(() => {
      busy = false;
    });
  };

  return {
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
};

const probeHttpDiscovery = (
  address: string,
  port: number,
  timeoutMs: number
): Promise<boolean> => new Promise((resolve) => {
  const request = httpRequest(
    { host: address, port, path: "/api/discovery", method: "GET", timeout: timeoutMs },
    (response) => {
      const chunks: Buffer[] = [];
      let size = 0;
      response.on("data", (chunk: Buffer) => {
        size += chunk.length;
        if (size > 8 * 1024) {
          response.destroy();
          resolve(false);
          return;
        }
        chunks.push(chunk);
      });
      response.on("end", () => {
        resolve(
          response.statusCode === 200
          && parseVillaBridgeDiscoveryRecord(Buffer.concat(chunks)) !== null
        );
      });
      response.on("error", () => resolve(false));
    }
  );
  request.once("timeout", () => {
    request.destroy();
    resolve(false);
  });
  request.once("error", () => resolve(false));
  request.end();
});

const probeTcp = (address: string, port: number, timeoutMs: number): Promise<boolean> =>
  new Promise((resolve) => {
    const socket = createConnection({ host: address, port });
    let finished = false;
    const done = (result: boolean): void => {
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

export interface PeerProbeOptions {
  selfNodeId: string;
  mqttPort: number;
  thresholds?: PeerWatchThresholds;
  now?: () => number;
  /** Testlerde ağ yerine sahte kayıt vermek için. */
  discover?: (address: string | undefined) => Promise<LanDiscoveryPeer | null>;
}

/**
 * Üç kanallı yoklama: UDP duyuru (beacon), `/api/discovery` HTTP'si ve karşı düğümün MQTT portu.
 * Karşı düğüm bir kez görüldükten sonra adresi hatırlanır; unicast yoklamaya geçilir.
 */
export const createPeerProbe = (
  options: PeerProbeOptions
): (() => Promise<PeerWatchProbeResult>) => {
  const thresholds = options.thresholds ?? peerWatchThresholds;
  const discover = options.discover
    ?? ((address: string | undefined) => queryLanDiscovery({
      address,
      timeoutMs: thresholds.probeTimeoutMs,
      selfNodeId: options.selfNodeId
    }));
  const now = options.now ?? Date.now;
  let known: { address: string; dashboardPort: number; nodeId: string } | null = null;
  let lastBeaconAt: number | null = null;

  return async (): Promise<PeerWatchProbeResult> => {
    const record = await discover(known?.address);
    if (record) lastBeaconAt = now();
    // Beacon kanalı tek kayıp duyuruyla düşmez; bayatlama süresi dolmalı (§3.2).
    const beacon = lastBeaconAt !== null && now() - lastBeaconAt <= thresholds.beaconStaleMs;
    const peer = record ?? (known === null ? null : { ...known });
    if (record) {
      known = {
        address: record.address,
        dashboardPort: record.dashboardPort,
        nodeId: record.nodeId
      };
    }
    if (peer === null) return { beacon, http: false, mqtt: false };
    const address = record?.address ?? known?.address ?? "";
    const dashboardPort = record?.dashboardPort ?? known?.dashboardPort ?? 0;
    const [http, mqtt] = await Promise.all([
      dashboardPort > 0
        ? probeHttpDiscovery(address, dashboardPort, thresholds.probeTimeoutMs)
        : Promise.resolve(false),
      probeTcp(address, options.mqttPort, thresholds.probeTimeoutMs)
    ]);
    return {
      beacon,
      http,
      mqtt,
      nodeId: record?.nodeId ?? known?.nodeId ?? null,
      address: address || null,
      state: record?.state ?? null,
      mode: record?.mode ?? null
    };
  };
};
