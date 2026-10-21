import type { DeviceDeparture } from "./device-departures.js";
import type { JsonObject } from "./types.js";

export interface ZigbeeNetworkMap {
  nodes: Array<{ id: string; name: string; type: string }>;
  links: Array<{ from: string; to: string; quality: number }>;
}

export interface PreparedNetworkBackup {
  extension: "zip";
  contentType: "application/zip";
  body: Buffer;
}

export interface OtaCheckResult {
  available: boolean;
  currentVersion?: number;
  availableVersion?: number;
}

export interface ZigbeeSource {
  start(): void | Promise<void>;
  stop(): Promise<void>;
  permitJoin(seconds: number, routerId?: string): Promise<void>;
  addInstallCode(value: string): Promise<void>;
  reconfigureDevice(id: string): Promise<void>;
  scanTouchlink(): Promise<Array<{ ieeeAddress: string; channel: number }>>;
  resetTouchlink(ieeeAddress: string, channel: number): Promise<void>;
  createGroup(name: string): Promise<void>;
  renameGroup(id: string, name: string): Promise<void>;
  removeGroup(id: string, force?: boolean): Promise<void>;
  setGroupMember(id: string, deviceId: string, add: boolean, endpoint?: number): Promise<void>;
  setGroup(id: string, command: JsonObject): Promise<void>;
  bindDevice(
    fromId: string,
    toId: string,
    bind: boolean,
    clusters?: string[],
    fromEndpoint?: number,
    toEndpoint?: number
  ): Promise<void>;
  groupScene(
    id: string,
    sceneId: number,
    action: "store" | "recall" | "remove",
    name?: string
  ): Promise<void>;
  networkMap(): Promise<ZigbeeNetworkMap>;
  checkOta(id: string): Promise<OtaCheckResult>;
  scheduleOta(id: string, enabled: boolean): Promise<void>;
  setDeviceOptions(id: string, options: { transition?: number; debounce?: number; retain?: boolean }): Promise<void>;
  setDevice(id: string, command: JsonObject): Promise<void>;
  renameDevice(id: string, name: string): Promise<void>;
  removeDevice(id: string, force?: boolean): Promise<void>;
  prepareNetworkBackup(): Promise<PreparedNetworkBackup | null>;
  setHomeAssistantDiscovery(enabled: boolean): void | Promise<void>;
  /**
   * Otomatik onarım yalnız koordinatörün sahibi olan kaynakta anlamlıdır; shadow modda
   * uygulanmaz, bu yüzden isteğe bağlıdır.
   */
  setSelfHealingEnabled?(enabled: boolean): void;
  /** Çevrimdışı cihaz yoklaması (Faz 2); yine yalnız koordinatör sahibinde anlamlıdır. */
  setSelfHealProbeEnabled?(enabled: boolean): void;
  /**
   * Son dakikalarda ağdan ayrılmış/kaldırılmış cihazın kaydı. Yalnız koordinatörün sahibi bunu
   * bilebilir; shadow modda yoktur, bu yüzden isteğe bağlıdır.
   */
  recentDeparture?(id: string): DeviceDeparture | undefined;
  recentDepartures?(): DeviceDeparture[];
}
