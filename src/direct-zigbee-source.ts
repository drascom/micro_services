import { join } from "node:path";
import { readFile } from "node:fs/promises";
import mqtt, { type MqttClient } from "mqtt";
import YAML from "yaml";
import { Controller, setLogger as setHerdsmanLogger, type Events } from "zigbee-herdsman";
import {
  findByDevice,
  postProcessConvertedFromZigbeeMessage,
  setLogger as setConverterLogger,
  type Definition
} from "zigbee-herdsman-converters";
import { writeFileAtomic, writeJsonAtomic } from "./atomic-file.js";
import type { DirectZigbeeConfig } from "./config.js";
import type { AppConfig } from "./config.js";
import {
  DEVICE_DEPARTURE_TTL_MS,
  DeviceDepartureLog,
  type DeviceDeparture
} from "./device-departures.js";
import type { DeviceNetworkEventSink } from "./device-network-events.js";
import { DeviceStore, featureValues } from "./device-store.js";
import { describeExternalConverters, loadExternalConverters } from "./external-converters.js";
import { buildHomeAssistantDiscovery } from "./home-assistant-discovery.js";
import { inferFallbackExposes } from "./inferred-exposes.js";
import {
  SelfHealScheduler,
  selfHealProbeTimeoutMs,
  type SelfHealDeviceState,
  type SelfHealOutcome,
  type SelfHealProbeResult
} from "./self-heal.js";
import type { OtaCheckResult, ZigbeeNetworkMap, ZigbeeSource } from "./source.js";
import { decodeTuyaButtonFrame } from "./tuya-button-frames.js";
import type { BridgeDevice, BridgeGroup, JsonObject } from "./types.js";
import { hasPendingZigbeeNetworkRestore } from "./zigbee-backup.js";

function messageText(value: string | (() => string)): string {
  return typeof value === "function" ? value() : value;
}

const quietLogger = {
  debug: (_value: string | (() => string), _namespace: string) => undefined,
  info: (value: string | (() => string), namespace: string) => console.info(`[${namespace}] ${messageText(value)}`),
  warning: (value: string | (() => string), namespace: string) => console.warn(`[${namespace}] ${messageText(value)}`),
  error: (value: string | (() => string), namespace: string) => console.error(`[${namespace}] ${messageText(value)}`)
};
setConverterLogger(quietLogger);
setHerdsmanLogger(quietLogger);

export function parsePermitJoinSeconds(payload: Buffer): number {
  const parsed = JSON.parse(payload.toString("utf8")) as { time?: number; value?: boolean };
  const requested = parsed.time ?? (parsed.value === true ? 180 : 0);
  return Math.min(254, Math.max(0, requested));
}

export function zigbeeAvailabilityState(
  lastSeen: number | undefined,
  device: { type: string; powerSource?: string },
  now = Date.now()
): "online" | "offline" {
  const power = device.powerSource?.toLowerCase() ?? "";
  const timeout =
    device.type === "EndDevice" || power.includes("battery")
      ? 36 * 60 * 60 * 1_000
      : 15 * 60 * 1_000;
  return typeof lastSeen === "number" && now - lastSeen <= timeout ? "online" : "offline";
}

/**
 * Çevrimdışı yoklama yalnız şebeke beslemeli yönlendiricilere yapılır. Ayrım herdsman'in
 * kendi `type`/`powerSource` alanlarından gelir; model ya da satıcı listesi yoktur.
 * Pilli cihaz uyur — uzun sessizlik onda normaldir, yoklamak pil harcamaktan başka işe yaramaz.
 */
export function isSelfHealProbeTarget(device: { type: string; powerSource?: string }): boolean {
  if (device.type !== "Router") return false;
  return !(device.powerSource?.toLowerCase() ?? "").includes("battery");
}

export interface DeviceStatePublication {
  payload: string;
  at: number;
}

/** `applyEndpointSuffix` için yeterli olan tanım parçası — tam `Definition` gerekmez. */
export interface EndpointSuffixDefinition {
  meta?: { multiEndpoint?: boolean; multiEndpointSkip?: string[] };
}

/**
 * Zigbee2MQTT ile birebir davranış: çok uç noktalı bir cihaza uç nokta üzerinden komut
 * yazıldığında dönüştürücünün ürettiği iyimser durum anahtarları `${key}_${endpointName}`
 * olarak yazılır. Eksik olduğunda `state_l2` yazımı ana `state`'i eziyor, `state_l2` ise hiç
 * güncellenmiyordu. `meta.multiEndpointSkip` listesindeki anahtarlar cihaz geneli sayılır ve
 * ek almaz.
 */
export function applyEndpointSuffix(
  update: JsonObject,
  endpointName: string | undefined,
  definition: EndpointSuffixDefinition
): JsonObject {
  if (!endpointName || definition.meta?.multiEndpoint !== true) return update;
  const suffix = `_${endpointName}`;
  const skip = definition.meta?.multiEndpointSkip ?? [];
  return Object.fromEntries(Object.entries(update).map(([key, value]) =>
    skip.includes(key) || key.endsWith(suffix)
      ? [key, value]
      : [`${key}${suffix}`, value]));
}

/**
 * Zigbee2MQTT sözleşmesinde ON/OFF kullanan yazılabilir bir durum TOGGLE komutunu da kabul eder;
 * cihaz tanımları bunu `e.switch()` üzerinden **istisnasız** ilan eder (`value_toggle: "TOGGLE"`)
 * ve panel ile otomasyon "değiştir" seçeneğini buna dayanarak sunar. Ama her dönüştürücü bunu
 * gerçekten uygulamıyor: veri noktası (DP) tabanlı anahtarlarda değer bir ON/OFF sözlüğünde
 * aranır ve TOGGLE **gönderim öncesi** reddedilir ("Key 'TOGGLE' not found in: [ON, OFF]").
 * Böyle bir durumda komutu düşürmek yerine köprü TOGGLE'ı kendisi uygular: bilinen son durumun
 * tersini yazar. Cihaza özel kural yok — ölçüt yalnız "değer reddedildi + son durum biliniyor".
 *
 * Hata metni denenen değeri anmıyorsa (ulaşılamayan cihaz, zaman aşımı) `null` döner; o hatalar
 * yeniden denenmez.
 */
export function toggleFallbackValue(
  requestedValue: unknown,
  currentValue: unknown,
  error: unknown
): "ON" | "OFF" | null {
  if (typeof requestedValue !== "string") return null;
  const requested = requestedValue.toUpperCase();
  if (requested !== "TOGGLE") return null;
  if (!String(error).toUpperCase().includes(requested)) return null;
  const current = typeof currentValue === "string" ? currentValue.toUpperCase() : null;
  if (current === "ON") return "OFF";
  if (current === "OFF") return "ON";
  return null;
}

export function isUnresolvedActionMessage(
  message: Pick<Events.MessagePayload, "cluster" | "type">,
  matchingConverterCount: number,
  convertedPropertyCount: number
): boolean {
  return message.cluster === "genOnOff"
    && typeof message.type === "string"
    && message.type.startsWith("command")
    && (matchingConverterCount === 0 || convertedPropertyCount === 0);
}

/**
 * `zigbee-herdsman` çözemediği ZCL çerçevelerini `type: "raw"` olarak yollar. Tuya sahne
 * anahtarlarının buton olayları tam olarak bu yoldan gelir, bu yüzden ham `genOnOff`
 * çerçeveleri ayrıca ele alınır.
 */
export function isRawGenOnOffFrame(
  message: Pick<Events.MessagePayload, "cluster" | "type">
): boolean {
  return message.cluster === "genOnOff" && message.type === "raw";
}

export function shouldPublishDeviceState(
  previous: DeviceStatePublication | undefined,
  payload: string,
  debounceSeconds: number,
  now = Date.now(),
  eventPayload = false
): boolean {
  if (eventPayload) return true;
  if (!previous || previous.payload !== payload) return true;
  return debounceSeconds <= 0 || now - previous.at >= debounceSeconds * 1_000;
}

export interface RemovableZigbeeDevice {
  removeFromNetwork(): Promise<void>;
  removeFromDatabase(): void;
}

export async function removeZigbeeDevice(device: RemovableZigbeeDevice, force: boolean): Promise<void> {
  if (force) device.removeFromDatabase();
  else await device.removeFromNetwork();
}

export function directBridgeInfo(permitted: boolean, time: number): JsonObject {
  return {
    version: "1.0.0",
    commit: "villa-bridge-direct",
    permit_join: permitted,
    permit_join_timeout: time,
    zigbee_herdsman: { version: "embedded" },
    zigbee_herdsman_converters: { version: "embedded" },
    config: {
      availability: { enabled: true },
      advanced: {
        output: "json",
        legacy_api: false,
        legacy_availability_payload: false
      }
    }
  };
}

export function endpointNamesForDevice(
  id: string,
  aliases: ReadonlyMap<string, string>
): Record<string, string> {
  const prefix = `${id.toLowerCase()}:`;
  return Object.fromEntries(
    [...aliases.entries()]
      .filter(([key, value]) => key.toLowerCase().startsWith(prefix) && value.trim().length > 0)
      .map(([key, value]) => [key.slice(prefix.length), value.trim()])
  );
}

/**
 * Otomatik onarımın kurulumu. Mekanizma bilerek yalnız bu sınıfın içinde yaşar: shadow modda
 * hiç kurulmaz, Android izleyici modunda çekirdek hiç başlamaz — devir sırasında iki koordinatör
 * sahibinin aynı anda yapılandırma yazması yapısal olarak imkânsız.
 */
export interface DirectSelfHealingSetup {
  enabled: boolean;
  /** Faz 2: çevrimdışı yönlendiricileri ucuz okumayla yoklama. */
  probeOffline?: boolean;
  state?: Map<string, SelfHealDeviceState>;
  persist?: (state: Map<string, SelfHealDeviceState>) => void;
  recordFailure?: (deviceId: string, message: string) => void;
  /** Testler için; üretimde varsayılan zamanlayıcı kullanılır. */
  spacingMs?: number;
  wait?: (ms: number) => Promise<void>;
  now?: () => number;
}

export interface DirectConnectionRecoveryState {
  connected: boolean;
  error: string | null;
  retryInMs: number | null;
}

export interface DirectConnectionRecoverySetup {
  enabled?: boolean;
  initialDelayMs?: number;
  maximumDelayMs?: number;
  onStateChange?: (state: DirectConnectionRecoveryState) => void;
}

export class DirectZigbeeSource implements ZigbeeSource {
  private controller: Controller | null = null;
  private readonly definitions = new Map<string, Definition>();
  private readonly states = new Map<string, JsonObject>();
  private abort = new AbortController();
  private mqtt: MqttClient | null = null;
  private bridgeDevices: BridgeDevice[] = [];
  private bridgeGroups: BridgeGroup[] = [];
  private homeAssistantDiscoveryTopics = new Set<string>();
  private pairingState = { permitted: false, time: 0 };
  private statePersistTimer: NodeJS.Timeout | null = null;
  private availabilityTimer: NodeJS.Timeout | null = null;
  private connectionWatchTimer: NodeJS.Timeout | null = null;
  private connectionWatchBusy = false;
  private connectionWatchFailures = 0;
  private readonly deviceAvailability = new Map<string, "online" | "offline">();
  private readonly lastDevicePublications = new Map<string, DeviceStatePublication>();
  private readonly otaUpdates = new Set<string>();
  /** Tuya tuş çerçevelerinin son ZCL sıra numarası; cihazın tekrarlarını eler. */
  private readonly lastButtonSequences = new Map<string, number>();
  /** Az önce ağdan düşen/kaldırılan cihazlar; kurulum uçları sebebi buradan okur. */
  private readonly departures: DeviceDepartureLog;
  private readonly selfHeal: SelfHealScheduler;
  private reconnectTimer: NodeJS.Timeout | null = null;
  private reconnectAttempt = 0;
  private reconnecting = false;
  private closingSession = false;
  private stopped = true;

  constructor(
    private readonly config: DirectZigbeeConfig,
    private readonly mqttConfig: AppConfig["mqtt"],
    private readonly store: DeviceStore,
    private homeAssistantDiscoveryEnabled = false,
    private readonly aliases: ReadonlyMap<string, string> = new Map(),
    private readonly definitionResolver: typeof findByDevice = findByDevice,
    selfHealing: DirectSelfHealingSetup = { enabled: false },
    /**
     * Kalıcı ağ üyeliği günlüğü. İsteğe bağlıdır: verilmezse kaynak eskisi gibi yalnız kısa
     * ömürlü bellek kaydını tutar.
     */
    private readonly networkEvents?: DeviceNetworkEventSink,
    private readonly connectionRecovery: DirectConnectionRecoverySetup = {}
  ) {
    // Bellekteki kayıt ile kalıcı günlük aynı olaydan beslenir; "sildim, sonra düştü"yü
    // bastıran kural tek yerde kalsın diye kalıcı yazma da bu kancadan geçer.
    this.departures = new DeviceDepartureLog(
      DEVICE_DEPARTURE_TTL_MS,
      64,
      (entry) => {
        this.networkEvents?.record({
          id: entry.id,
          reason: entry.reason,
          ...(entry.name ? { name: entry.name } : {})
        });
      }
    );
    this.selfHeal = new SelfHealScheduler({
      enabled: selfHealing.enabled,
      probeEnabled: selfHealing.probeOffline === true,
      initialState: selfHealing.state,
      persist: selfHealing.persist,
      blockedReason: (deviceId) => this.selfHealBlockedReason(deviceId),
      prepare: (deviceId) => this.selfHealPrepare(deviceId),
      probe: (deviceId) => this.selfHealProbe(deviceId),
      onOutcome: (deviceId, outcome) => this.recordSelfHealEvent(deviceId, outcome),
      onFailure: selfHealing.recordFailure,
      ...(selfHealing.spacingMs === undefined ? {} : { spacingMs: selfHealing.spacingMs }),
      ...(selfHealing.wait ? { wait: selfHealing.wait } : {}),
      ...(selfHealing.now ? { now: selfHealing.now } : {})
    });
  }

  setSelfHealingEnabled(enabled: boolean): void {
    this.selfHeal.setEnabled(enabled);
  }

  setSelfHealProbeEnabled(enabled: boolean): void {
    this.selfHeal.setProbeEnabled(enabled);
  }

  /**
   * Otomatik onarımın asla denenmemesi gereken durumlar. Her biri var olan bir duruma bakar;
   * yeni bayrak icat edilmez.
   */
  private async selfHealBlockedReason(deviceId: string): Promise<string | null> {
    const controller = this.controller;
    if (!controller) return "Zigbee koordinatörü hazır değil.";
    if (this.pairingState.permitted) return "Eşleştirme açık.";
    if (this.otaUpdates.size > 0) return "Kablosuz yazılım güncellemesi sürüyor.";
    const device = controller.getDeviceByIeeeAddr(deviceId);
    if (!device) return "Cihaz bulunamadı.";
    if (device.scheduledOta) return "Cihaz için yazılım güncellemesi planlı.";
    if (await hasPendingZigbeeNetworkRestore(this.config.dataDir)) {
      return "Zigbee ağ yedeği geri yüklenmeyi bekliyor.";
    }
    return null;
  }

  /**
   * Yalnız yerel hazırlık: cihaz tanımını çözer. `interview(true)` **çağrılmaz** — ilan zaten
   * cihazın erişilebilir olduğunu söyler, pahalı ve sonuçsuz görüşmeye gerek yok.
   */
  private async selfHealPrepare(deviceId: string): Promise<(() => Promise<void>) | null> {
    const controller = this.controller;
    const device = controller?.getDeviceByIeeeAddr(deviceId);
    if (!controller || !device || device.type === "Coordinator") return null;
    if (this.config.devices[deviceId]?.disabled === true) return null;
    const definition = this.definitions.get(deviceId) ?? await this.definitionResolver(device);
    if (!definition) return null;
    this.definitions.set(deviceId, definition);
    if (!definition.configure) return null;
    const coordinatorDevice = controller.getDevicesByType("Coordinator")[0];
    const coordinator = coordinatorDevice?.getEndpoint(1) ?? coordinatorDevice?.endpoints[0];
    if (!coordinator) return null;
    return async () => {
      await definition.configure?.(device, coordinator, definition);
      await this.refreshDevices();
    };
  }

  /**
   * Çevrimdışı yönlendiriciyi tek hafif okumayla yoklar (`genBasic/zclVersion`, 5 sn,
   * `disableRecovery`). Amaç en ucuz yol: yanıt gelirse cihaz zaten erişilebilirdir,
   * "erişilemez" işareti düşürülür. `interview(true)` bu yolda **asla** çağrılmaz.
   */
  private async selfHealProbe(deviceId: string): Promise<(() => Promise<SelfHealProbeResult>) | null> {
    const device = this.controller?.getDeviceByIeeeAddr(deviceId);
    if (!device || !isSelfHealProbeTarget(device)) return null;
    if (this.config.devices[deviceId]?.disabled === true) return null;
    const endpoint = device.getEndpoint(1) ?? device.endpoints[0];
    if (!endpoint) return null;
    return async () => {
      try {
        await endpoint.read("genBasic", ["zclVersion"], {
          timeout: selfHealProbeTimeoutMs,
          disableRecovery: true
        });
      } catch (error) {
        // Tek deneme; ikinci okuma yok. Karar geri çekilmeye bırakılır.
        return { reachable: false, message: error instanceof Error ? error.message : String(error) };
      }
      const recovered = this.deviceAvailability.get(deviceId) !== "online";
      this.setAvailability(deviceId, "online");
      try {
        return { reachable: true, recovered, configured: await this.selfHealConfigureIfPending(device) };
      } catch (error) {
        // Cihaz erişilebilir; yalnız yapılandırma yazılamadı. Yoklama başarısız sayılmaz.
        return {
          reachable: true,
          recovered,
          configured: false,
          message: error instanceof Error ? error.message : String(error)
        };
      }
    };
  }

  /**
   * Yapılandırma gerçekten gerekiyor mu? Herdsman veritabanında `meta.configured` yazılıysa
   * cihaz zaten yapılandırılmıştır; yoklamanın ardından koordinatörü boşuna meşgul etmeyiz.
   */
  private async selfHealConfigureIfPending(device: { ieeeAddr: string; meta?: JsonObject }): Promise<boolean> {
    if (device.meta?.configured !== undefined) return false;
    const run = await this.selfHealPrepare(device.ieeeAddr);
    if (!run) return false;
    await run();
    return true;
  }

  /**
   * Zigbee2MQTT'nin `external_converters/` kancasının doğrudan moddaki karşılığı.
   * Hata toleranslıdır: klasör yoksa veya bir dosya patlarsa kaynak yine de açılır.
   */
  private async loadExternalConverters(): Promise<void> {
    try {
      const result = await loadExternalConverters(this.config.externalConvertersDir);
      if (result.directoryExists) console.log(describeExternalConverters(result));
    } catch (error) {
      console.warn(
        `Harici converter'lar yüklenemedi: ${error instanceof Error ? error.message : String(error)}`
      );
    }
  }

  private recordSelfHealEvent(deviceId: string, outcome: SelfHealOutcome): void {
    const friendlyName = this.config.devices[deviceId]?.friendly_name ?? deviceId;
    this.store.recordExternalEvent(friendlyName, "self_heal", outcome);
  }

  async start(): Promise<void> {
    this.stopped = false;
    try {
      await this.startSession();
      this.reconnectAttempt = 0;
      this.reportConnectionState(true, null, null);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      await this.closeSession().catch((closeError) => {
        console.warn(`Başarısız Zigbee oturumu kapatılamadı: ${String(closeError)}`);
      });
      this.scheduleReconnect(message);
      throw error;
    }
  }

  private async startSession(): Promise<void> {
    // Cihaz tanımları çözülmeye başlamadan ÖNCE harici converter'lar kütüphaneye kaydedilmeli;
    // yoksa kütüphanede olmayan cihaz "bilinmeyen" olarak önbelleğe girer.
    await this.loadExternalConverters();
    this.abort = new AbortController();
    const controller = new Controller({
      network: {
        panID: this.config.network.panId,
        extendedPanID: this.config.network.extendedPanId,
        channelList: [this.config.network.channel],
        networkKey: this.config.network.networkKey
      },
      databasePath: join(this.config.dataDir, "database.db"),
      databaseBackupPath: join(this.config.dataDir, "database.db.backup"),
      backupPath: join(this.config.dataDir, "coordinator_backup.json"),
      serialPort: {
        path: this.config.serial.path,
        baudRate: this.config.serial.baudRate,
        rtscts: this.config.serial.rtscts,
        adapter: this.config.serial.adapter
      },
      adapter: {
        concurrent: this.config.adapter.concurrent,
        delay: this.config.adapter.delay,
        disableLED: this.config.adapter.disableLed ?? false,
        transmitPower: this.config.adapter.transmitPower
      },
      acceptJoiningDeviceHandler: async () => true
    });
    this.controller = controller;
    this.attachEvents(controller);
    await controller.start(this.abort.signal);
    this.store.setMqttConnected(true);
    this.store.ingest("bridge/state", Buffer.from('{"state":"online"}'));
    this.refreshGroups();
    await this.loadCachedStates();
    await this.refreshDevices();
    await this.startMqttCompatibility();
    this.deviceAvailability.clear();
    this.refreshAvailability();
    this.availabilityTimer = setInterval(() => this.refreshAvailability(), 60_000);
    this.availabilityTimer.unref();
    const parameters = await controller.getNetworkParameters();
    console.log(`SLZB koordinatörü devralındı; Zigbee kanalı ${parameters.channel}.`);
    this.connectionWatchFailures = 0;
    this.connectionWatchTimer = setInterval(() => void this.checkConnection(controller), 30_000);
    this.connectionWatchTimer.unref();
    const initialStateTimer = setTimeout(() => void this.requestInitialActuatorStates(), 1_000);
    initialStateTimer.unref();
  }

  async permitJoin(seconds: number, routerId?: string): Promise<void> {
    if (!this.controller) throw new Error("SLZB koordinatörü hazır değil.");
    const router = routerId ? this.controller.getDeviceByIeeeAddr(routerId) : undefined;
    if (routerId && !router) throw new Error("Seçilen Zigbee yönlendiricisi bulunamadı.");
    await this.controller.permitJoin(seconds, router);
  }

  async addInstallCode(value: string): Promise<void> {
    if (!this.controller) throw new Error("SLZB koordinatörü hazır değil.");
    await this.controller.addInstallCode(value);
  }

  async reconfigureDevice(id: string): Promise<void> {
    const controller = this.controller;
    const device = controller?.getDeviceByIeeeAddr(id);
    if (!controller || !device) throw new Error("Cihaz bulunamadı.");
    await device.interview(true);
    const definition = await this.definitionResolver(device);
    const coordinator = controller.getDevicesByType("Coordinator")[0]?.endpoints[0];
    if (!definition || !coordinator) throw new Error("Cihaz yapılandırma tanımı bulunamadı.");
    if (definition.configure) await definition.configure(device, coordinator, definition);
    this.definitions.set(id, definition);
    await this.refreshDevices();
  }

  async scanTouchlink(): Promise<Array<{ ieeeAddress: string; channel: number }>> {
    if (!this.controller) throw new Error("SLZB koordinatörü hazır değil.");
    const devices = await this.controller.touchlink.scan();
    return devices.map((device) => ({ ieeeAddress: device.ieeeAddr, channel: device.channel }));
  }

  async resetTouchlink(ieeeAddress: string, channel: number): Promise<void> {
    if (!this.controller) throw new Error("SLZB koordinatörü hazır değil.");
    const reset = await this.controller.touchlink.factoryReset(ieeeAddress, channel);
    if (!reset) throw new Error("Yakındaki cihaz sıfırlanamadı.");
  }

  async createGroup(name: string): Promise<void> {
    const controller = this.controller;
    if (!controller) throw new Error("SLZB koordinatörü hazır değil.");
    let id = 1;
    while (id <= 0xfff7 && controller.getGroupByID(id)) id += 1;
    if (id > 0xfff7) throw new Error("Yeni Zigbee grubu için boş kimlik kalmadı.");
    const group = controller.createGroup(id);
    this.config.groups[String(id)] = { friendly_name: name };
    try {
      await this.persistGroupConfiguration(id, name);
    } catch (error) {
      delete this.config.groups[String(id)];
      group.removeFromDatabase();
      throw error;
    }
    this.refreshGroups();
  }

  async renameGroup(id: string, name: string): Promise<void> {
    const group = this.groupByIdentifier(id);
    await this.persistGroupConfiguration(group.groupID, name);
    this.config.groups[String(group.groupID)] = {
      ...(this.config.groups[String(group.groupID)] ?? {}),
      friendly_name: name
    };
    this.refreshGroups();
  }

  async removeGroup(id: string, force = false): Promise<void> {
    const group = this.groupByIdentifier(id);
    if (force) group.removeFromDatabase();
    else await group.removeFromNetwork();
    delete this.config.groups[String(group.groupID)];
    await this.persistGroupConfiguration(group.groupID, null);
    this.refreshGroups();
  }

  async setGroupMember(id: string, deviceId: string, add: boolean, endpoint?: number): Promise<void> {
    const controller = this.controller;
    const group = this.groupByIdentifier(id);
    const device = controller?.getDeviceByIeeeAddr(deviceId);
    if (!device) throw new Error("Cihaz bulunamadı.");
    const target = endpoint ? device.getEndpoint(endpoint) : device.endpoints.find((item) => item.inputClusters.includes(6)) ?? device.endpoints[0];
    if (!target) throw new Error("Cihazın gruba eklenebilir uç noktası bulunamadı.");
    if (add) await target.addToGroup(group);
    else await target.removeFromGroup(group);
    this.refreshGroups();
  }

  async setGroup(id: string, command: JsonObject): Promise<void> {
    const group = this.groupByIdentifier(id);
    const state = command.state;
    if (
      typeof state !== "string"
      || !["ON", "OFF", "TOGGLE"].includes(state.toUpperCase())
    ) {
      throw new Error("Direct Zigbee grubu yalnız aç, kapat veya değiştir komutunu destekliyor.");
    }
    await group.command("genOnOff", state.toLowerCase(), {} as never);
  }

  async bindDevice(
    fromId: string,
    toId: string,
    bind: boolean,
    clusters?: string[],
    fromEndpoint?: number,
    toEndpoint?: number
  ): Promise<void> {
    const controller = this.controller;
    const from = controller?.getDeviceByIeeeAddr(fromId);
    if (!controller || !from) throw new Error("Kaynak cihaz bulunamadı.");
    const sourceEndpoint = fromEndpoint ? from.getEndpoint(fromEndpoint) : from.endpoints[0];
    const targetDevice = controller.getDeviceByIeeeAddr(toId);
    const target = targetDevice
      ? toEndpoint
        ? targetDevice.getEndpoint(toEndpoint)
        : targetDevice.endpoints[0]
      : this.groupByIdentifier(toId);
    if (!sourceEndpoint || !target) throw new Error("Bağlama uç noktası bulunamadı.");
    const commonControlClusters = new Set([5, 6, 8, 768]);
    const selected = clusters?.length
      ? clusters
      : sourceEndpoint.outputClusters.filter((cluster) =>
        commonControlClusters.has(cluster)
        && ("inputClusters" in target ? target.inputClusters.includes(cluster) : true)
      );
    if (!selected.length) throw new Error("Cihazlar arasında bağlanabilir küme bulunamadı.");
    for (const cluster of selected) {
      if (bind) await sourceEndpoint.bind(cluster, target);
      else await sourceEndpoint.unbind(cluster, target);
    }
    await this.refreshDevices();
  }

  async groupScene(
    id: string,
    sceneId: number,
    action: "store" | "recall" | "remove",
    name?: string
  ): Promise<void> {
    const group = this.groupByIdentifier(id);
    if (action === "store") await group.command("genScenes", "store", { groupid: group.groupID, sceneid: sceneId });
    else if (action === "recall") {
      await group.command("genScenes", "recall", { groupid: group.groupID, sceneid: sceneId });
    } else await group.command("genScenes", "remove", { groupid: group.groupID, sceneid: sceneId });
    if (action !== "recall") {
      const scenes = this.configuredGroupScenes(group);
      const updated = action === "store"
        ? [
          ...scenes.filter((scene) => scene.id !== sceneId),
          { id: sceneId, name: name?.trim() || `Scene ${sceneId}` }
        ].sort((left, right) => left.id - right.id)
        : scenes.filter((scene) => scene.id !== sceneId);
      this.persistGroupScenes(group, updated);
    }
    this.refreshGroups();
  }

  async networkMap(): Promise<ZigbeeNetworkMap> {
    const controller = this.controller;
    if (!controller) throw new Error("SLZB koordinatörü hazır değil.");
    const devices = [...controller.getDevicesIterator()];
    const nodes = devices.map((device) => ({
      id: device.ieeeAddr,
      name: this.config.devices[device.ieeeAddr]?.friendly_name ?? (device.type === "Coordinator" ? "Coordinator" : device.ieeeAddr),
      type: device.type
    }));
    const links: ZigbeeNetworkMap["links"] = [];
    for (const device of devices.filter((item) => item.type !== "EndDevice")) {
      try {
        for (const neighbor of await device.lqi()) {
          const to = String(neighbor.eui64).toLowerCase();
          if (!nodes.some((node) => node.id.toLowerCase() === to)) continue;
          links.push({ from: device.ieeeAddr, to, quality: neighbor.lqi });
        }
      } catch {
        // Sleeping or busy routers may omit their table; keep the partial map useful.
      }
    }
    return { nodes, links };
  }

  async scheduleOta(id: string, enabled: boolean): Promise<void> {
    const device = this.controller?.getDeviceByIeeeAddr(id);
    if (!device) throw new Error("Cihaz bulunamadı.");
    const definition = this.definitions.get(id) ?? await findByDevice(device);
    if (!definition?.ota) throw new Error("Bu cihaz kablosuz yazılım güncellemesini desteklemiyor.");
    if (enabled) {
      device.scheduleOta({});
      this.publishOtaState(id, { state: "scheduled", progress: 0 });
    } else {
      device.unscheduleOta();
      this.publishOtaState(id, { state: "idle", progress: 0 });
    }
  }

  async checkOta(id: string): Promise<OtaCheckResult> {
    const device = this.controller?.getDeviceByIeeeAddr(id);
    if (!device) throw new Error("Cihaz bulunamadı.");
    const definition = this.definitions.get(id) ?? await findByDevice(device);
    if (!definition?.ota) throw new Error("Bu cihaz kablosuz yazılım güncellemesini desteklemiyor.");
    const extraMetas = typeof definition.ota === "object" ? definition.ota : {};
    const result = await device.checkOta({}, undefined, extraMetas);
    const checked: OtaCheckResult = {
      available: result.available,
      currentVersion: result.current.fileVersion,
      ...(result.availableMeta ? { availableVersion: result.availableMeta.fileVersion } : {})
    };
    this.publishOtaState(id, {
      state: result.available ? "available" : "idle",
      progress: 0,
      current_version: checked.currentVersion ?? null,
      available_version: checked.availableVersion ?? null
    });
    return checked;
  }

  async setDeviceOptions(
    id: string,
    options: { transition?: number; debounce?: number; retain?: boolean }
  ): Promise<void> {
    if (!this.controller?.getDeviceByIeeeAddr(id)) throw new Error("Cihaz bulunamadı.");
    const path = this.config.configurationFile;
    if (!path) throw new Error("Zigbee yapılandırma dosyası tanımlı değil.");
    const document = YAML.parseDocument(await readFile(path, "utf8"));
    const definedOptions = Object.fromEntries(
      Object.entries(options).filter((entry): entry is [string, number | boolean] => entry[1] !== undefined)
    );
    for (const [key, value] of Object.entries(definedOptions)) {
      document.setIn(["devices", id, key], value);
    }
    await writeFileAtomic(path, document.toString());
    this.config.devices[id] = { ...(this.config.devices[id] ?? {}), ...definedOptions };
    await this.refreshDevices();
  }

  async renameDevice(id: string, name: string): Promise<void> {
    const device = this.controller?.getDeviceByIeeeAddr(id);
    if (!device) throw new Error("Cihaz bulunamadı.");
    const previousName = this.config.devices[id]?.friendly_name ?? id;
    this.config.devices[id] = { ...(this.config.devices[id] ?? {}), friendly_name: name };
    try {
      await this.persistDeviceConfiguration(id, name);
    } catch (error) {
      this.config.devices[id] = { ...(this.config.devices[id] ?? {}), friendly_name: previousName };
      throw error;
    }
    await this.refreshDevices();
    const availability = this.deviceAvailability.get(id);
    if (availability) {
      this.publishRetained(`${previousName}/availability`, "");
      this.deviceAvailability.delete(id);
      this.setAvailability(id, availability);
    }
    this.publish("bridge/response/device/rename", {
      status: "ok",
      data: { from: previousName, to: name }
    });
    const state = this.states.get(id);
    if (state) {
      this.store.ingest(name, Buffer.from(JSON.stringify(state)));
      this.publishRetained(previousName, "");
      this.publish(name, state, this.config.devices[id]?.retain === true);
    }
    this.publishHomeAssistantDiscovery();
  }

  async removeDevice(id: string, force = false): Promise<void> {
    const device = this.controller?.getDeviceByIeeeAddr(id);
    if (!device) throw new Error("Cihaz bulunamadı.");
    const previousName = this.config.devices[id]?.friendly_name ?? id;
    await removeZigbeeDevice(device, force);
    // Zorlamasız silmede cihaza havadan "ağdan ayrıl" komutu gider; mesh'te geciken bu komut,
    // kullanıcı aynı cihazı hemen yeniden eşleştirirse yeni oturumu düşürebilir. Kayıt panelin
    // "bu cihazı az önce kaldırdınız" uyarısını verebilmesi için tutulur.
    this.departures.record(id, "removed", Date.now(), this.config.devices[id]?.friendly_name);
    delete this.config.devices[id];
    await this.persistDeviceConfiguration(id, null);
    this.definitions.delete(id);
    this.states.delete(id);
    this.deviceAvailability.delete(id);
    this.lastDevicePublications.delete(id);
    this.publishRetained(previousName, "");
    this.publishRetained(`${previousName}/availability`, "");
    await this.refreshDevices();
    this.publish("bridge/response/device/remove", {
      status: "ok",
      data: { id: previousName, block: false, force }
    });
    this.publish("bridge/event", {
      type: "device_leave",
      data: { friendly_name: previousName, ieee_address: id }
    });
    await this.persistStates();
  }

  recentDeparture(id: string): DeviceDeparture | undefined {
    return this.departures.get(id);
  }

  recentDepartures(): DeviceDeparture[] {
    return this.departures.list();
  }

  async prepareNetworkBackup(): Promise<null> {
    const controller = this.controller;
    if (!controller) throw new Error("Zigbee koordinatörü hazır değil.");
    await controller.backup();
    await this.persistStates();
    return null;
  }

  async stop(): Promise<void> {
    this.stopped = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.selfHeal.setEnabled(false);
    await this.closeSession();
    await this.persistStates();
  }

  private async closeSession(): Promise<void> {
    this.closingSession = true;
    this.abort.abort();
    const controller = this.controller;
    this.controller = null;
    this.store.setMqttConnected(false);
    this.store.ingest("bridge/state", Buffer.from('{"state":"offline"}'));
    const mqttClient = this.mqtt;
    this.mqtt = null;
    if (this.statePersistTimer) {
      clearTimeout(this.statePersistTimer);
      this.statePersistTimer = null;
    }
    if (this.availabilityTimer) {
      clearInterval(this.availabilityTimer);
      this.availabilityTimer = null;
    }
    if (this.connectionWatchTimer) {
      clearInterval(this.connectionWatchTimer);
      this.connectionWatchTimer = null;
    }
    if (mqttClient) {
      mqttClient.publish(`${this.mqttConfig.baseTopic}/bridge/state`, JSON.stringify({ state: "offline" }), { retain: true });
      await new Promise<void>((resolve) => mqttClient.end(false, {}, () => resolve()));
    }
    try {
      if (controller && !controller.isStopping()) await controller.stop();
    } finally {
      this.closingSession = false;
    }
  }

  private reportConnectionState(
    connected: boolean,
    error: string | null,
    retryInMs: number | null
  ): void {
    this.connectionRecovery.onStateChange?.({ connected, error, retryInMs });
  }

  private scheduleReconnect(error: string): void {
    if (this.connectionRecovery.enabled === false || this.stopped || this.reconnectTimer) return;
    if (this.connectionWatchTimer) {
      clearInterval(this.connectionWatchTimer);
      this.connectionWatchTimer = null;
    }
    const initialDelayMs = Math.max(1_000, this.connectionRecovery.initialDelayMs ?? 10_000);
    const maximumDelayMs = Math.max(initialDelayMs, this.connectionRecovery.maximumDelayMs ?? 60_000);
    const delayMs = Math.min(initialDelayMs * (2 ** this.reconnectAttempt), maximumDelayMs);
    this.reconnectAttempt += 1;
    this.reportConnectionState(false, error, delayMs);
    console.warn(
      `Zigbee koordinatör bağlantısı yok; ${Math.round(delayMs / 1_000)} saniye sonra yeniden denenecek: ${error}`
    );
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      void this.reconnect();
    }, delayMs);
    this.reconnectTimer.unref();
  }

  private async checkConnection(controller: Controller): Promise<void> {
    if (
      this.connectionWatchBusy
      || this.stopped
      || this.reconnecting
      || this.closingSession
      || controller !== this.controller
    ) return;
    this.connectionWatchBusy = true;
    try {
      await controller.getNetworkParameters();
      this.connectionWatchFailures = 0;
    } catch (error) {
      this.connectionWatchFailures += 1;
      if (this.connectionWatchFailures < 2) return;
      const message = error instanceof Error ? error.message : String(error);
      this.store.setMqttConnected(false);
      this.store.ingest("bridge/state", Buffer.from('{"state":"offline"}'));
      this.publishRetained("bridge/state", { state: "offline" });
      this.scheduleReconnect(`Koordinatör sağlık kontrolü başarısız: ${message}`);
    } finally {
      this.connectionWatchBusy = false;
    }
  }

  private async reconnect(): Promise<void> {
    if (this.stopped || this.reconnecting) return;
    this.reconnecting = true;
    try {
      await this.closeSession();
      if (this.stopped) return;
      await this.startSession();
      if (this.stopped) {
        await this.closeSession();
        return;
      }
      this.reconnectAttempt = 0;
      this.reportConnectionState(true, null, null);
      console.log("Zigbee koordinatör bağlantısı yeniden kuruldu.");
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      await this.closeSession().catch((closeError) => {
        console.warn(`Başarısız Zigbee oturumu kapatılamadı: ${String(closeError)}`);
      });
      this.scheduleReconnect(message);
    } finally {
      this.reconnecting = false;
    }
  }

  private attachEvents(controller: Controller): void {
    controller.on("adapterDisconnected", () => {
      this.store.setMqttConnected(false);
      this.store.ingest("bridge/state", Buffer.from('{"state":"offline"}'));
      this.publishRetained("bridge/state", { state: "offline" });
      for (const device of controller.getDevicesIterator()) {
        if (device.type !== "Coordinator") this.setAvailability(device.ieeeAddr, "offline");
      }
      if (controller === this.controller && !this.closingSession && !this.stopped) {
        this.scheduleReconnect("Koordinatör bağlantısı kesildi.");
      }
    });
    controller.on("permitJoinChanged", ({ permitted, time }) => {
      this.pairingState = { permitted, time: permitted ? time ?? 0 : 0 };
      const response = {
        status: "ok",
        data: {
          time: this.pairingState.time,
          value: permitted
        }
      };
      this.store.ingest(
        "bridge/response/permit_join",
        Buffer.from(JSON.stringify(response))
      );
      this.publish("bridge/response/permit_join", response);
      this.publishBridgeInfo();
    });
    controller.on("deviceJoined", async ({ device }) => {
      const friendlyName = this.config.devices[device.ieeeAddr]?.friendly_name ?? device.ieeeAddr;
      // Katılma kalıcı günlüğe doğrudan yazılır: `departures` yalnız ayrılmayı tutar. Yeni
      // cihazın henüz adı yoktur; o zaman satır IEEE adresiyle anılır.
      const knownName = this.config.devices[device.ieeeAddr]?.friendly_name;
      this.networkEvents?.record({
        id: device.ieeeAddr,
        reason: "joined",
        ...(knownName ? { name: knownName } : {})
      });
      const event = {
        type: "device_joined",
        data: { friendly_name: friendlyName, ieee_address: device.ieeeAddr }
      };
      this.store.ingest("bridge/event", Buffer.from(JSON.stringify(event)));
      this.publish("bridge/event", event);
      await this.refreshDevices();
    });
    const announceKnownDevice = (device: { ieeeAddr: string }): void => {
      if (!this.pairingState.permitted) return;
      const friendlyName = this.config.devices[device.ieeeAddr]?.friendly_name ?? device.ieeeAddr;
      const event = {
        type: "device_announce",
        data: { friendly_name: friendlyName, ieee_address: device.ieeeAddr }
      };
      this.store.ingest("bridge/event", Buffer.from(JSON.stringify(event)));
      this.publish("bridge/event", event);
    };
    // İlan = cihaz güç döngüsünden/rota kurulumundan sonra ağa döndüğünün kanıtı. Zigbee2MQTT
    // deseni: burada raporlama ayarları yeniden yazılır, görüşme başlatılmaz.
    controller.on("deviceAnnounce", ({ device }) => {
      announceKnownDevice(device);
      this.selfHeal.schedule(device.ieeeAddr);
    });
    controller.on("lastSeenChanged", ({ device, reason }) => {
      this.setAvailability(device.ieeeAddr, "online");
      if (reason === "deviceJoined") announceKnownDevice(device);
    });
    controller.on("deviceLeave", async ({ ieeeAddr }) => {
      const friendlyName = this.config.devices[ieeeAddr]?.friendly_name ?? ieeeAddr;
      // Cihaz listeden düşmeden önce sebebi not et: bundan sonraki her kurulum isteği
      // "bulunamadı" yerine "az önce ağdan ayrıldı" diyebilsin.
      this.departures.record(
        ieeeAddr,
        "left",
        Date.now(),
        this.config.devices[ieeeAddr]?.friendly_name
      );
      this.publish("bridge/event", {
        type: "device_leave",
        data: { friendly_name: friendlyName, ieee_address: ieeeAddr }
      });
      await this.refreshDevices();
    });
    controller.on("deviceInterview", async ({ status, device }) => {
      if (status !== "successful") return;
      const definition = await findByDevice(device);
      if (definition) {
        this.definitions.set(device.ieeeAddr, definition);
        const coordinator = controller.getDevicesByType("Coordinator")[0]?.getEndpoint(1);
        if (definition.configure && coordinator) {
          try {
            await definition.configure(device, coordinator, definition);
          } catch (error) {
            console.warn(`Yeni cihaz raporlama ayarları tamamlanamadı (${device.ieeeAddr}): ${String(error)}`);
          }
        }
      }
      await this.refreshDevices();
      void this.requestInitialActuatorStates();
      const event = {
        type: "device_interview",
        data: {
          friendly_name: this.config.devices[device.ieeeAddr]?.friendly_name ?? device.ieeeAddr,
          ieee_address: device.ieeeAddr,
          status,
          supported: Boolean(definition)
        }
      };
      this.store.ingest("bridge/event", Buffer.from(JSON.stringify(event)));
      this.publish("bridge/event", event);
    });
    // Sinyal/son görülme, cihaz tanımı çözülmeden yakalanır: desteklenmeyen bir cihaz da tabloda
    // "hangi cihaz ne zaman görüldü" sorusunu yanıtlayabilmeli. `onMessage` tanım bulamazsa döner.
    controller.on("message", (message) => {
      this.recordDeviceLink(message);
      void this.onMessage(message);
    });
  }

  /**
   * Doğrudan kipin sinyal yolu: `linkquality` her gelen Zigbee mesajında taşınır, `lastSeen` ise
   * herdsman'in cihaz kaydında tutulur. Zigbee2MQTT bunları yayın payload'ına eklerken biz cihaz
   * defterine yazarız — yayınlanan uyumluluk payload'ı olduğu gibi kalsın diye.
   */
  private recordDeviceLink(message: Events.MessagePayload): void {
    const ieeeAddress = message.device.ieeeAddr;
    const friendlyName = this.config.devices[ieeeAddress]?.friendly_name ?? ieeeAddress;
    const lastSeen = typeof message.device.lastSeen === "number" ? message.device.lastSeen : Date.now();
    this.store.recordDeviceLink(friendlyName, {
      ...(typeof message.linkquality === "number" ? { linkquality: message.linkquality } : {}),
      lastSeen: new Date(lastSeen).toISOString()
    });
  }

  private async refreshDevices(): Promise<void> {
    const controller = this.controller;
    if (!controller) return;
    const devices: BridgeDevice[] = [];
    for (const device of controller.getDevicesIterator()) {
      if (device.type === "Coordinator") {
        devices.push({
          ieee_address: device.ieeeAddr,
          friendly_name: "Coordinator",
          type: "Coordinator",
          disabled: false,
          supported: true,
          interview_completed: true,
          interviewing: false
        });
        continue;
      }
      const configured = this.config.devices[device.ieeeAddr] ?? {};
      const endpointNames = endpointNamesForDevice(device.ieeeAddr, this.aliases);
      const definition = await findByDevice(device);
      if (definition) this.definitions.set(device.ieeeAddr, definition);
      const options = configured as JsonObject;
      const fallbackExposes = definition
        ? []
        : inferFallbackExposes(this.states.get(device.ieeeAddr));
      const exposes = definition
        ? typeof definition.exposes === "function"
          ? definition.exposes(device, options)
          : definition.exposes
        : fallbackExposes;
      const inferredDefinition = fallbackExposes.length > 0 ? {
        model: device.modelID ?? "Unknown",
        vendor: device.manufacturerName ?? "Unknown",
        description: "Capabilities inferred from cached Zigbee state",
        exposes: fallbackExposes,
        options: []
      } : undefined;
      devices.push({
        ieee_address: device.ieeeAddr,
        friendly_name: configured.friendly_name ?? device.ieeeAddr,
        model_id: device.modelID ?? "",
        type: device.type,
        disabled: configured.disabled === true,
        interview_completed: device.interviewState === "SUCCESSFUL",
        interviewing: device.interviewState === "IN_PROGRESS",
        supported: Boolean(definition) || Boolean(inferredDefinition),
        network_address: device.networkAddress,
        date_code: device.dateCode,
        manufacturer: device.manufacturerName,
        power_source: device.powerSource,
        software_build_id: device.softwareBuildID,
        definition: definition ? {
          model: definition.model,
          vendor: definition.vendor,
          description: definition.description,
          exposes,
          options: definition.options ?? [],
          ota: Boolean(definition.ota)
        } : inferredDefinition,
        configured_options: {
          ...(typeof configured.transition === "number" ? { transition: configured.transition } : {}),
          ...(typeof configured.debounce === "number" ? { debounce: configured.debounce } : {}),
          ...(typeof configured.retain === "boolean" ? { retain: configured.retain } : {})
        },
        ...(Object.keys(endpointNames).length > 0 ? { endpoint_names: endpointNames } : {}),
        endpoints: Object.fromEntries(device.endpoints.map((endpoint) => [String(endpoint.ID), {
          bindings: endpoint.binds.map((binding) => ({
            cluster: binding.cluster.name,
            target: "groupID" in binding.target
              ? {
                type: "group",
                id: binding.target.groupID
              }
              : {
                type: "device",
                ieee_address: binding.target.deviceIeeeAddress,
                endpoint: binding.target.ID
              }
          })),
          configured_reportings: [],
          clusters: {
            input: endpoint.inputClusters,
            output: endpoint.outputClusters
          },
          scenes: {}
        }]))
      });
    }
    this.bridgeDevices = devices;
    this.store.ingest("bridge/devices", Buffer.from(JSON.stringify(devices)));
    this.publishRetained("bridge/devices", devices);
    this.publishHomeAssistantDiscovery();
  }

  private refreshGroups(): void {
    const controller = this.controller;
    if (!controller) return;
    this.bridgeGroups = [...controller.getGroupsIterator()].map((group) => ({
      id: group.groupID,
      friendly_name: this.config.groups[String(group.groupID)]?.friendly_name ?? `group-${group.groupID}`,
      members: group.members.map((endpoint) => ({
        ieee_address: endpoint.deviceIeeeAddress,
        endpoint: endpoint.ID
      })),
      scenes: this.configuredGroupScenes(group)
    }));
    this.store.ingest("bridge/groups", Buffer.from(JSON.stringify(this.bridgeGroups)));
    this.publishRetained("bridge/groups", this.bridgeGroups);
  }

  private async onMessage(message: Events.MessagePayload): Promise<void> {
    const definition = this.definitions.get(message.device.ieeeAddr) ?? await findByDevice(message.device);
    if (!definition) return;
    this.definitions.set(message.device.ieeeAddr, definition);
    if (await this.handleScheduledOta(message, definition)) return;
    const configured = this.config.devices[message.device.ieeeAddr] ?? {};
    const friendlyName = configured.friendly_name ?? message.device.ieeeAddr;
    this.setAvailability(message.device.ieeeAddr, "online");
    const options = configured as JsonObject;
    const previous = this.states.get(message.device.ieeeAddr) ?? {};
    const matching = definition.fromZigbee.filter((converter) => {
      const types = Array.isArray(converter.type) ? converter.type : [converter.type];
      return converter.cluster === message.cluster && types.includes(message.type as never);
    });
    const publish = (payload: JsonObject): void => {
      const merged = { ...(this.states.get(message.device.ieeeAddr) ?? {}), ...payload };
      // `action` anlık bir kenar olayıdır; yayınlanır ama kalıcı duruma yazılmaz (Zigbee2MQTT ile
      // aynı davranış). Aksi hâlde her sonraki durum yayını eski tuş basışını tekrar duyururdu.
      const { action: _action, ...retained } = merged;
      this.states.set(message.device.ieeeAddr, retained);
      this.store.ingest(friendlyName, Buffer.from(JSON.stringify(merged)));
      const serialized = JSON.stringify(merged);
      const debounce = typeof configured.debounce === "number" ? configured.debounce : 0;
      const now = Date.now();
      const actionEvent = typeof payload.action === "string" && payload.action.trim().length > 0;
      if (shouldPublishDeviceState(
        this.lastDevicePublications.get(message.device.ieeeAddr),
        serialized,
        debounce,
        now,
        actionEvent
      )) {
        this.publish(friendlyName, merged, configured.retain === true);
        this.lastDevicePublications.set(message.device.ieeeAddr, { payload: serialized, at: now });
      }
      this.queueStatePersistence();
    };
    let result: JsonObject = {};
    for (const converter of matching) {
      try {
        const converted = await converter.convert(
          definition,
          message as never,
          publish,
          options,
          {
            device: message.device,
            state: previous,
            deviceExposesChanged: () => void this.refreshDevices()
          }
        );
        if (converted && typeof converted === "object") result = { ...result, ...converted };
      } catch (error) {
        console.warn(`Zigbee mesajı dönüştürülemedi (${friendlyName}): ${String(error)}`);
      }
    }
    if (Object.keys(result).length === 0 && isRawGenOnOffFrame(message)) {
      const decoded = this.decodeButtonFrame(message, definition, friendlyName, options);
      if (decoded) {
        publish(decoded);
        return;
      }
    }
    if (isUnresolvedActionMessage(message, matching.length, Object.keys(result).length)) {
      const endpoint = "ID" in message.endpoint ? message.endpoint.ID : "unknown";
      console.warn(
        `Zigbee tuş olayı çözümlenemedi (${friendlyName}): `
        + JSON.stringify({
          endpoint,
          cluster: message.cluster,
          type: message.type,
          data: message.data,
          matchingConverters: matching.length
        })
      );
    }
    if (Object.keys(result).length > 0) {
      postProcessConvertedFromZigbeeMessage(definition, result, options, message.device);
      publish(result);
    }
  }

  /**
   * Tuya TS004x sahne anahtarlarının çözümlenemeyen `genOnOff` çerçevesini `action`
   * değerine çevirir. Üretilen değer cihaz tanımının `action` sözlüğüyle doğrulanır;
   * uymazsa yayımlanmaz ama Türkçe uyarı bırakılır.
   */
  private decodeButtonFrame(
    message: Events.MessagePayload,
    definition: Definition,
    friendlyName: string,
    options: JsonObject
  ): JsonObject | undefined {
    const endpointId = "ID" in message.endpoint ? message.endpoint.ID : Number.NaN;
    const decoded = decodeTuyaButtonFrame({
      data: message.data as ArrayLike<number>,
      endpointId,
      endpointCount: message.device.endpoints.length,
      modelId: message.device.modelID ?? undefined
    });
    if (!decoded.ok) {
      console.warn(`Zigbee tuş çerçevesi çözümlenemedi (${friendlyName}): ${decoded.reason}`);
      return undefined;
    }
    const exposes = typeof definition.exposes === "function"
      ? definition.exposes(message.device, options)
      : definition.exposes;
    const supported = featureValues(exposes, "action");
    if (supported.length > 0 && !supported.includes(decoded.action)) {
      console.warn(
        `Tuya tuş olayı cihaz sözlüğünde yok (${friendlyName}): ${decoded.action}`
      );
      return undefined;
    }
    // Cihaz aynı çerçeveyi tekrarlayabilir; Zigbee2MQTT gibi ZCL sıra numarasına bakıyoruz.
    if (this.lastButtonSequences.get(message.device.ieeeAddr) === decoded.sequence) return undefined;
    this.lastButtonSequences.set(message.device.ieeeAddr, decoded.sequence);
    return { action: decoded.action };
  }

  private publishOtaState(id: string, update: JsonObject): void {
    const configured = this.config.devices[id] ?? {};
    const friendlyName = configured.friendly_name ?? id;
    const merged = {
      ...(this.states.get(id) ?? {}),
      update
    };
    this.states.set(id, merged);
    this.store.ingest(friendlyName, Buffer.from(JSON.stringify(merged)));
    this.publish(friendlyName, merged, configured.retain === true);
    this.queueStatePersistence();
  }

  private async handleScheduledOta(
    message: Events.MessagePayload,
    definition: Definition
  ): Promise<boolean> {
    const id = message.device.ieeeAddr;
    if (
      message.cluster !== "genOta"
      || message.type !== "commandQueryNextImageRequest"
      || !message.device.scheduledOta
      || this.otaUpdates.has(id)
      || !definition.ota
    ) {
      return false;
    }
    this.otaUpdates.add(id);
    const extraMetas = typeof definition.ota === "object" ? definition.ota : {};
    this.publishOtaState(id, { state: "updating", progress: 0 });
    try {
      const [, installed] = await message.device.updateOta(
        undefined,
        message.data as never,
        message.meta.zclTransactionSequenceNumber,
        extraMetas,
        (progress, remaining) => this.publishOtaState(id, {
          state: "updating",
          progress: Math.max(0, Math.min(100, progress)),
          remaining
        }),
        {
          requestTimeout: 150_000,
          responseDelay: 250,
          baseSize: 50
        },
        message.endpoint
      );
      this.publishOtaState(id, {
        state: "idle",
        progress: installed ? 100 : 0,
        installed_version: installed?.fileVersion ?? null
      });
    } catch (error) {
      this.publishOtaState(id, {
        state: "failed",
        progress: 0,
        error: error instanceof Error ? error.message : String(error)
      });
      console.error(`OTA güncellemesi başarısız (${id}): ${String(error)}`);
    } finally {
      this.otaUpdates.delete(id);
    }
    return true;
  }

  private async startMqttCompatibility(): Promise<void> {
    const client = mqtt.connect(this.mqttConfig.url, {
      username: this.mqttConfig.username,
      password: this.mqttConfig.password,
      clientId: `villa-bridge-direct-${process.pid}`,
      clean: true,
      reconnectPeriod: 2_000,
      will: {
        topic: `${this.mqttConfig.baseTopic}/bridge/state`,
        payload: JSON.stringify({ state: "offline" }),
        qos: 1,
        retain: true
      }
    });
    this.mqtt = client;
    client.on("error", (error) => console.error(`Uyumluluk MQTT hatası: ${error.message}`));
    client.on("message", (topic, payload) => {
      if (topic === "homeassistant/status" && payload.toString("utf8").trim().toLowerCase() === "online") {
        this.publishHomeAssistantDiscovery();
        return;
      }
      void this.onMqttCommand(topic, payload);
    });
    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Uyumluluk MQTT bağlantısı zaman aşımına uğradı.")), 10_000);
      client.once("connect", () => {
        clearTimeout(timer);
        resolve();
      });
    });
    await new Promise<void>((resolve, reject) => {
      client.subscribe([
        `${this.mqttConfig.baseTopic}/+/set`,
        `${this.mqttConfig.baseTopic}/+/get`,
        `${this.mqttConfig.baseTopic}/bridge/request/permit_join`,
        "homeassistant/status"
      ], { qos: 1 }, (error) => error ? reject(error) : resolve());
    });
    this.publishRetained("bridge/state", { state: "online" });
    this.publishBridgeInfo();
    this.publishRetained("bridge/devices", this.bridgeDevices);
    this.publishRetained("bridge/groups", this.bridgeGroups);
    for (const [ieeeAddress, state] of this.states) {
      const friendlyName = this.config.devices[ieeeAddress]?.friendly_name ?? ieeeAddress;
      this.publish(friendlyName, state, this.config.devices[ieeeAddress]?.retain === true);
    }
    this.publishHomeAssistantDiscovery();
    console.log("Home Assistant ve Matterbridge için MQTT uyumluluk katmanı hazır.");
  }

  private setAvailability(ieeeAddress: string, availability: "online" | "offline"): void {
    if (this.deviceAvailability.get(ieeeAddress) === availability) return;
    this.deviceAvailability.set(ieeeAddress, availability);
    const friendlyName = this.config.devices[ieeeAddress]?.friendly_name ?? ieeeAddress;
    const payload = { state: availability };
    this.store.ingest(`${friendlyName}/availability`, Buffer.from(JSON.stringify(payload)));
    this.publish(`${friendlyName}/availability`, payload, true);
  }

  private refreshAvailability(): void {
    const controller = this.controller;
    if (!controller) return;
    const now = Date.now();
    for (const device of controller.getDevicesIterator()) {
      if (device.type === "Coordinator") continue;
      const availability = zigbeeAvailabilityState(device.lastSeen, device, now);
      this.setAvailability(device.ieeeAddr, availability);
      // Mesaj yolu yalnız çözümlenen çerçeveleri görür; dakikalık tarama son görülmeyi her cihaz
      // için tazeler. Sıklığı zamanlayıcı sınırlar, böylece cihaz görünümü önbelleği boğulmaz.
      if (typeof device.lastSeen === "number") {
        const friendlyName = this.config.devices[device.ieeeAddr]?.friendly_name ?? device.ieeeAddr;
        this.store.recordDeviceLink(friendlyName, { lastSeen: new Date(device.lastSeen).toISOString() });
      }
      // Çevrimdışı görünen şebeke beslemeli yönlendirici yoklanır; sıklığı zamanlayıcı belirler.
      if (availability === "offline" && isSelfHealProbeTarget(device)) {
        this.selfHeal.scheduleProbe(device.ieeeAddr);
      }
    }
  }

  private publishHomeAssistantDiscovery(): void {
    const client = this.mqtt;
    if (!client?.connected) return;
    const messages = buildHomeAssistantDiscovery(this.store.getDevices(), this.mqttConfig.baseTopic);
    const currentTopics = new Set(messages.map((item) => item.topic));
    if (!this.homeAssistantDiscoveryEnabled) {
      for (const topic of new Set([...this.homeAssistantDiscoveryTopics, ...currentTopics])) {
        client.publish(topic, "", { retain: true });
      }
      this.homeAssistantDiscoveryTopics.clear();
      return;
    }
    for (const topic of this.homeAssistantDiscoveryTopics) {
      if (!currentTopics.has(topic)) client.publish(topic, "", { retain: true });
    }
    for (const item of messages) {
      client.publish(item.topic, JSON.stringify(item.payload), { retain: true });
    }
    this.homeAssistantDiscoveryTopics = currentTopics;
  }

  setHomeAssistantDiscovery(enabled: boolean): void {
    this.homeAssistantDiscoveryEnabled = enabled;
    this.publishHomeAssistantDiscovery();
  }

  private async loadCachedStates(): Promise<void> {
    try {
      const cached = JSON.parse(
        await readFile(join(this.config.dataDir, "state.json"), "utf8")
      ) as Record<string, unknown>;
      for (const [ieeeAddress, configured] of Object.entries(this.config.devices)) {
        const value = cached[ieeeAddress];
        if (!value || typeof value !== "object" || Array.isArray(value)) continue;
        const state = value as JsonObject;
        const friendlyName = configured.friendly_name ?? ieeeAddress;
        this.states.set(ieeeAddress, state);
        this.store.ingest(friendlyName, Buffer.from(JSON.stringify(state)));
      }
      for (const group of this.bridgeGroups) {
        const value = cached[String(group.id)];
        if (!value || typeof value !== "object" || Array.isArray(value) || !group.friendly_name) continue;
        this.store.ingest(group.friendly_name, Buffer.from(JSON.stringify(value)));
      }
      console.log(`${this.states.size} cihazın son durumu önbellekten yüklendi.`);
    } catch (error) {
      console.warn(`Zigbee durum önbelleği okunamadı: ${String(error)}`);
    }
  }

  private async requestInitialActuatorStates(): Promise<void> {
    const controller = this.controller;
    if (!controller) return;
    for (const device of controller.getDevicesIterator()) {
      if (device.type !== "Router") continue;
      const definition = this.definitions.get(device.ieeeAddr) ?? await findByDevice(device);
      if (!definition) continue;
      const endpoint = device.getEndpoint(1) ?? device.endpoints[0];
      if (!endpoint) continue;
      const configured = this.config.devices[device.ieeeAddr] ?? {};
      const options = configured as JsonObject;
      const state = this.states.get(device.ieeeAddr) ?? {};
      for (const key of ["state", "brightness", "color_temp"]) {
        const converter = definition.toZigbee.find((item) =>
          item.key?.includes(key) && typeof item.convertGet === "function"
        );
        if (!converter?.convertGet) continue;
        try {
          await converter.convertGet(endpoint, key, {
            message: {},
            device,
            mapped: definition,
            options,
            state,
            endpoint_name: undefined
          } as never);
        } catch (error) {
          console.warn(`Başlangıç cihaz durumu okunamadı (${device.ieeeAddr}/${key}): ${String(error)}`);
        }
      }
    }
  }

  private async onMqttCommand(topic: string, payload: Buffer): Promise<void> {
    const prefix = `${this.mqttConfig.baseTopic}/`;
    if (!topic.startsWith(prefix)) return;
    const relative = topic.slice(prefix.length);
    if (relative === "bridge/request/permit_join") {
      try {
        await this.permitJoin(parsePermitJoinSeconds(payload));
      } catch (error) {
        console.warn(`Eşleştirme komutu işlenemedi: ${String(error)}`);
      }
      return;
    }
    if (relative.endsWith("/get")) {
      const friendlyName = relative.slice(0, -4);
      const deviceEntry = Object.entries(this.config.devices)
        .find(([, options]) => options.friendly_name === friendlyName);
      const state = deviceEntry ? this.states.get(deviceEntry[0]) : undefined;
      if (state) this.publish(friendlyName, state, deviceEntry?.[1].retain === true);
      return;
    }
    if (!relative.endsWith("/set")) return;
    const friendlyName = relative.slice(0, -4);
    const deviceEntry = Object.entries(this.config.devices)
      .find(([, options]) => options.friendly_name === friendlyName);
    // Zigbee2MQTT'de `<taban>/<grup adı>/set` de geçerlidir; Matterbridge ve Home Assistant oda
    // komutlarını buradan gönderir. Cihaz adı tutmadığında ada karşılık gelen grup aranır, yoksa
    // eskisi gibi cihaz yolu denenir (ham IEEE adresiyle gönderen istemciler bozulmasın).
    const isGroupName = !deviceEntry && Object.values(this.config.groups)
      .some((options) => options.friendly_name === friendlyName);
    const ieeeAddress = deviceEntry?.[0] ?? friendlyName;
    try {
      const text = payload.toString("utf8");
      let command: JsonObject;
      try {
        const parsed = JSON.parse(text) as unknown;
        command = typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)
          ? parsed as JsonObject
          : { state: parsed };
      } catch {
        command = { state: text };
      }
      if (isGroupName) await this.setGroup(friendlyName, command);
      else await this.setDevice(ieeeAddress, command);
    } catch (error) {
      console.warn(`Cihaz komutu işlenemedi (${friendlyName}): ${String(error)}`);
    }
  }

  async setDevice(ieeeAddress: string, command: JsonObject): Promise<void> {
    const controller = this.controller;
    const device = controller?.getDeviceByIeeeAddr(ieeeAddress);
    if (!device) throw new Error("Cihaz bulunamadı.");
    const definition = this.definitions.get(ieeeAddress) ?? await findByDevice(device);
    if (!definition) throw new Error("Cihaz dönüştürücüsü bulunamadı.");
    const configured = this.config.devices[ieeeAddress] ?? {};
    const options = configured as JsonObject;
    let state = this.states.get(ieeeAddress) ?? {};
    const endpointMap = definition.endpoint?.(device) ?? {};
    for (const [requestedKey, value] of Object.entries(command)) {
      const endpointName = Object.keys(endpointMap).find((name) => requestedKey.endsWith(`_${name}`));
      const baseKey = endpointName ? requestedKey.slice(0, -(endpointName.length + 1)) : requestedKey;
      const converter = definition.toZigbee.find((item) =>
        !item.key || item.key.includes(requestedKey) || item.key.includes(baseKey)
      );
      const convertSet = converter?.convertSet;
      if (!convertSet) throw new Error(`'${requestedKey}' komutu desteklenmiyor.`);
      const endpointId = endpointName ? endpointMap[endpointName] : undefined;
      const endpoint = (endpointId ? device.getEndpoint(endpointId) : device.getEndpoint(1)) ?? device.endpoints[0];
      if (!endpoint) throw new Error("Cihaz uç noktası bulunamadı.");
      const publish = (update: JsonObject): void => {
        state = { ...state, ...applyEndpointSuffix(update, endpointName, definition) };
        this.states.set(ieeeAddress, state);
      };
      const send = async (attemptValue: unknown): Promise<Awaited<ReturnType<typeof convertSet>>> => {
        const attemptCommand = attemptValue === value
          ? command
          : { ...command, [requestedKey]: attemptValue as JsonObject[string] };
        const converterMessage = endpointName
          ? Object.fromEntries(Object.entries(attemptCommand).map(([key, item]) => [
              key.endsWith(`_${endpointName}`) ? key.slice(0, -(endpointName.length + 1)) : key,
              item
            ]))
          : attemptCommand;
        return convertSet.call(converter, endpoint, baseKey, attemptValue, {
          message: converterMessage,
          device,
          mapped: definition,
          options,
          state,
          endpoint_name: endpointName,
          publish
        });
      };
      let result: Awaited<ReturnType<typeof convertSet>>;
      try {
        result = await send(value);
      } catch (error) {
        // Dönüştürücü TOGGLE'ı tanımıyorsa köprü onu bilinen durumdan uygular (bkz.
        // `toggleFallbackValue`). Uyarı günlüğe düşer ki sessiz bir davranış farkı olmasın.
        const fallback = toggleFallbackValue(value, state[requestedKey], error);
        if (fallback === null) throw error;
        console.warn(
          `'${requestedKey}' kanalı TOGGLE komutunu kabul etmedi (${ieeeAddress}); `
          + `bilinen son durumun tersi '${fallback}' olarak yazılıyor.`
        );
        result = await send(fallback);
      }
      if (result?.state) {
        state = {
          ...state,
          ...applyEndpointSuffix(result.state as JsonObject, endpointName, definition)
        };
      }
    }
    this.states.set(ieeeAddress, state);
    const friendlyName = configured.friendly_name ?? ieeeAddress;
    this.store.ingest(friendlyName, Buffer.from(JSON.stringify(state)));
    this.publish(friendlyName, state, configured.retain === true);
    // Komut cihaza gitti; durum önbelleğinin diske yazılamaması komutu başarısız saymamalı.
    // Hata yutulmuyor, yalnızca çağırana yansımıyor.
    try {
      await this.persistStates();
    } catch (error) {
      console.warn(`Zigbee durum önbelleği yazılamadı: ${String(error)}`);
    }
  }

  private publishBridgeInfo(): void {
    this.publishRetained(
      "bridge/info",
      directBridgeInfo(this.pairingState.permitted, this.pairingState.time)
    );
  }

  private queueStatePersistence(): void {
    if (this.statePersistTimer) clearTimeout(this.statePersistTimer);
    this.statePersistTimer = setTimeout(() => {
      this.statePersistTimer = null;
      void this.persistStates().catch((error) => {
        console.warn(`Zigbee durum önbelleği yazılamadı: ${String(error)}`);
      });
    }, 250);
    this.statePersistTimer.unref();
  }

  private async persistStates(): Promise<void> {
    const path = join(this.config.dataDir, "state.json");
    await writeJsonAtomic(path, Object.fromEntries(this.states));
  }

  private async persistDeviceConfiguration(id: string, name: string | null): Promise<void> {
    const path = this.config.configurationFile;
    if (!path) throw new Error("Zigbee yapılandırma dosyası tanımlı değil.");
    const document = YAML.parseDocument(await readFile(path, "utf8"));
    if (name === null) document.deleteIn(["devices", id]);
    else document.setIn(["devices", id, "friendly_name"], name);
    await writeFileAtomic(path, document.toString());
  }

  private groupByIdentifier(id: string) {
    const controller = this.controller;
    if (!controller) throw new Error("SLZB koordinatörü hazır değil.");
    const numeric = Number(id.replace(/^group-/, ""));
    const group = Number.isInteger(numeric)
      ? controller.getGroupByID(numeric)
      : [...controller.getGroupsIterator()].find(
        (item) => this.config.groups[String(item.groupID)]?.friendly_name === id
      );
    if (!group) throw new Error("Zigbee grubu bulunamadı.");
    return group;
  }

  private async persistGroupConfiguration(id: number, name: string | null): Promise<void> {
    const path = this.config.configurationFile;
    if (!path) throw new Error("Zigbee yapılandırma dosyası tanımlı değil.");
    const document = YAML.parseDocument(await readFile(path, "utf8"));
    if (name === null) document.deleteIn(["groups", String(id)]);
    else document.setIn(["groups", String(id), "friendly_name"], name);
    await writeFileAtomic(path, document.toString());
  }

  private configuredGroupScenes(group: {
    meta: JsonObject;
  }): Array<{ id: number; name: string }> {
    const raw = group.meta.villa_scenes;
    if (!Array.isArray(raw)) return [];
    return raw.flatMap((scene) => {
      if (!scene || typeof scene !== "object" || Array.isArray(scene)) return [];
      const value = scene as Record<string, unknown>;
      const sceneId = Number(value.id);
      if (!Number.isInteger(sceneId) || sceneId < 1 || sceneId > 255) return [];
      return [{
        id: sceneId,
        name: typeof value.name === "string" && value.name.trim()
          ? value.name.trim().slice(0, 64)
          : `Scene ${sceneId}`
      }];
    }).sort((left, right) => left.id - right.id);
  }

  private persistGroupScenes(
    group: {
      meta: JsonObject;
      save(): void;
    },
    scenes: Array<{ id: number; name: string }>
  ): void {
    if (scenes.length) group.meta.villa_scenes = scenes;
    else delete group.meta.villa_scenes;
    group.save();
  }

  private publish(relativeTopic: string, value: unknown, retain = false): void {
    const client = this.mqtt;
    if (!client?.connected) return;
    client.publish(
      `${this.mqttConfig.baseTopic}/${relativeTopic}`,
      typeof value === "string" ? value : JSON.stringify(value),
      { retain }
    );
  }

  private publishRetained(relativeTopic: string, value: unknown): void {
    this.publish(relativeTopic, value, true);
  }
}
