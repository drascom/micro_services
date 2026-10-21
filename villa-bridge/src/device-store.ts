import type {
  BridgeDevice,
  BridgeGroup,
  DeviceControlView,
  DeviceEventView,
  DeviceReadingView,
  DeviceView,
  GroupView,
  JsonObject,
  JsonScalar
} from "./types.js";
import { deviceButtons } from "./device-buttons.js";
import { validateDeviceEvents } from "./device-events.js";
import { detectDeviceCategory, resolveDeviceCategory, type DeviceRole } from "./device-category.js";
import { colorHex, deviceControls, type WritableFeature } from "./device-controls.js";
import { canonicalDeviceModel, deviceIdentity } from "./device-identity.js";
import { resolveChannelRole } from "./device-roles.js";
import type { DeviceImagePreferences } from "./device-images.js";

interface StateEntry {
  value: JsonObject;
  updatedAt: Date;
}

/**
 * §4.1 — kanal başına "ne zamandır bu değerde" defteri. Süre koşulunun (`forSeconds`) tek
 * altyapısı budur; olay akışının dar "interesting" listesinden bağımsızdır, böylece
 * `temperature` gibi olay üretmeyen özellikler için de süre işler.
 */
interface ChannelSinceEntry {
  value: JsonScalar;
  since: Date;
  /**
   * §4.2 — kanalın **en son ne zaman rapor edildiği**. `since` ile aynı şey değildir: sabit lüks
   * yayan bir sensörde değer aylarca değişmez ama her dakika rapor gelir. Tazelik ölçütü
   * (`freshWithinSeconds`) yalnız bunu okur; süre ölçütü (`forSeconds`) yalnız `since`'i.
   */
  reportedAt: Date;
}

/** Defterin üst sınırı — durum haritasıyla aynı büyüklük sınıfı; taşarsa en eski düşer. */
const maxChannelSinceEntries = 5_000;

/**
 * Cihaz başına son bağlantı kalitesi ve son görülme. Bu ikisi cihaz **durumu** değildir: gölge
 * kipinde Zigbee2MQTT payload'ında gelir, doğrudan kipte yalnızca ham Zigbee mesajında taşınır.
 * Ayrı defterde tutulur ki içinde `linkquality` olmayan bir yayın son bilinen değeri silmesin.
 * Alanlar bilerek opsiyoneldir: veri yoksa hiç yazılmaz, uydurma değer üretilmez.
 */
interface DeviceLinkEntry {
  linkquality?: number;
  lastSeen?: string;
}

/** LQI 0-255 aralığında tam sayıdır; dışındaki her şey "veri yok" sayılır. */
function normalizedLinkQuality(value: unknown): number | undefined {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return undefined;
  return Math.round(Math.max(0, Math.min(255, numeric)));
}

interface PairingDevice {
  id: string;
  name: string;
  interviewCompleted: boolean;
  supported: boolean | null;
  reconnected: boolean;
}

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function normalizeTopicPart(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function featureNames(exposes: unknown): string[] {
  const names = new Set<string>();
  const visit = (value: unknown): void => {
    if (Array.isArray(value)) {
      value.forEach(visit);
      return;
    }
    if (!isObject(value)) return;
    if (typeof value.property === "string") names.add(value.property);
    if (typeof value.name === "string") names.add(value.name);
    if ("features" in value) visit(value.features);
  };
  visit(exposes);
  return [...names].sort();
}

export function featureValues(exposes: unknown, property: string): string[] {
  const values = new Set<string>();
  const visit = (value: unknown): void => {
    if (Array.isArray(value)) {
      value.forEach(visit);
      return;
    }
    if (!isObject(value)) return;
    if (value.property === property && Array.isArray(value.values)) {
      for (const item of value.values) {
        if (typeof item === "string" && item.trim()) values.add(item);
      }
    }
    if ("features" in value) visit(value.features);
  };
  visit(exposes);
  return [...values].sort();
}

/** Durum haritasının anahtarı: cihazın MQTT konusundaki adı (`buildDeviceViews` ile aynı kural). */
function deviceSourceName(device: BridgeDevice): string {
  return device.friendly_name ?? device.ieee_address ?? "unknown";
}

function scalar(value: unknown): JsonScalar | undefined {
  return typeof value === "string" || typeof value === "number" || typeof value === "boolean"
    ? value
    : undefined;
}

function deviceEndpoints(device: BridgeDevice): DeviceView["endpoints"] {
  if (!isObject(device.endpoints)) return [];
  return Object.entries(device.endpoints).flatMap(([key, rawEndpoint]) => {
    if (!isObject(rawEndpoint)) return [];
    const id = Number(key);
    if (!Number.isInteger(id) || id < 1 || id > 240) return [];
    const clusters = isObject(rawEndpoint.clusters) ? rawEndpoint.clusters : {};
    const inputClusters = Array.isArray(clusters.input)
      ? clusters.input.filter((value): value is string | number =>
        typeof value === "string" || typeof value === "number"
      )
      : [];
    const outputClusters = Array.isArray(clusters.output)
      ? clusters.output.filter((value): value is string | number =>
        typeof value === "string" || typeof value === "number"
      )
      : [];
    const bindings = Array.isArray(rawEndpoint.bindings)
      ? rawEndpoint.bindings.flatMap((rawBinding) => {
        if (!isObject(rawBinding) || !isObject(rawBinding.target)) return [];
        const cluster = isObject(rawBinding.cluster)
          ? rawBinding.cluster.name
          : rawBinding.cluster;
        if (typeof cluster !== "string" && typeof cluster !== "number") return [];
        const target = rawBinding.target;
        const targetDevice = typeof target.ieee_address === "string"
          ? target.ieee_address.toLowerCase()
          : typeof target.deviceIeeeAddress === "string"
            ? target.deviceIeeeAddress.toLowerCase()
            : null;
        const groupId = Number(target.id ?? target.groupID);
        const targetType: "device" | "group" | null = targetDevice
          ? "device"
          : Number.isInteger(groupId)
            ? "group"
            : null;
        if (!targetType) return [];
        const endpoint = Number(target.endpoint ?? target.endpointID ?? target.ID);
        return [{
          cluster: String(cluster),
          targetType,
          targetId: targetDevice ?? `group-${groupId}`,
          targetEndpoint: Number.isInteger(endpoint) ? endpoint : null
        }];
      })
      : [];
    return [{
      id,
      name: device.endpoint_names?.[key] ?? `EP ${id}`,
      inputClusters,
      outputClusters,
      bindings
    }];
  }).sort((left, right) => left.id - right.id);
}

function writableFeatures(exposes: unknown): WritableFeature[] {
  const features = new Map<string, WritableFeature>();
  const visit = (
    value: unknown,
    parent: { type?: string; name?: string; category?: string } = {}
  ): void => {
    if (Array.isArray(value)) {
      value.forEach((item) => visit(item, parent));
      return;
    }
    if (!isObject(value)) return;
    const access = typeof value.access === "number" ? value.access : 0;
    const property = typeof value.property === "string" ? value.property : undefined;
    if ((access & 2) !== 0 && property) {
      features.set(property, {
        property,
        name: typeof value.name === "string" ? value.name : property,
        type: typeof value.type === "string" ? value.type : "",
        parentType: parent.type,
        parentName: parent.name,
        min: typeof value.value_min === "number" ? value.value_min : undefined,
        max: typeof value.value_max === "number" ? value.value_max : undefined,
        step: typeof value.value_step === "number" ? value.value_step : undefined,
        unit: typeof value.unit === "string" ? value.unit : undefined,
        values: Array.isArray(value.values)
          ? value.values.map(scalar).filter((item): item is JsonScalar => item !== undefined)
          : undefined,
        valueOn: scalar(value.value_on),
        valueOff: scalar(value.value_off),
        valueToggle: scalar(value.value_toggle),
        category: typeof value.category === "string" ? value.category : parent.category
      });
    }
    if ("features" in value) {
      visit(value.features, {
        type: typeof value.type === "string" ? value.type : parent.type,
        name: typeof value.name === "string" ? value.name : parent.name,
        category: typeof value.category === "string" ? value.category : parent.category
      });
    }
  };
  visit(exposes);
  return [...features.values()].sort((left, right) => left.property.localeCompare(right.property, "en"));
}

/**
 * Gösterimde asla ölçüm sayılmayan jenerik altyapı anahtarları. Cihaza özel değildir:
 * hepsi Zigbee2MQTT'nin her cihaza eklediği köprü/taşıma alanlarıdır.
 */
const nonReadingProperties: ReadonlySet<string> = new Set([
  "linkquality", "last_seen", "update", "update_available", "elapsed"
]);

/** Ölçüm etiketi: `label` varsa o, yoksa `name`; alt çizgiler boşluğa çevrilir. */
function readingLabel(label: unknown, name: string): string {
  const text = typeof label === "string" && label.trim().length > 0 ? label : name;
  return text.replaceAll("_", " ").trim();
}

/**
 * `writableFeatures()`in kardeşi: yayımlanan ama YAZILAMAYAN (`access & 1` var, `access & 2` yok)
 * her expose'u toplar. Yazılabilirler zaten `deviceControls()` ile kumanda satırına dönüyor,
 * burada tekrar edilmez. Kural tamamen Z2M expose sözleşmesinden türer; model listesi yoktur.
 */
function readableFeatures(exposes: unknown): Omit<DeviceReadingView, "value">[] {
  const features = new Map<string, Omit<DeviceReadingView, "value">>();
  const visit = (
    value: unknown,
    parent: { name?: string; category?: string } = {}
  ): void => {
    if (Array.isArray(value)) {
      value.forEach((item) => visit(item, parent));
      return;
    }
    if (!isObject(value)) return;
    const access = typeof value.access === "number" ? value.access : 0;
    const property = typeof value.property === "string" ? value.property : undefined;
    if ((access & 1) !== 0 && (access & 2) === 0 && property && !nonReadingProperties.has(property)) {
      const name = typeof value.name === "string" ? value.name : property;
      const category = typeof value.category === "string" ? value.category : parent.category;
      const valueOn = scalar(value.value_on);
      const valueOff = scalar(value.value_off);
      features.set(property, {
        property,
        name: readingLabel(value.label, name),
        type: typeof value.type === "string" ? value.type : "",
        ...(typeof value.unit === "string" && value.unit.length > 0 ? { unit: value.unit } : {}),
        ...(valueOn === undefined ? {} : { valueOn }),
        ...(valueOff === undefined ? {} : { valueOff }),
        ...(category ? { category } : {}),
        ...(parent.name ? { parentName: parent.name } : {})
      });
    }
    if ("features" in value) {
      visit(value.features, {
        name: typeof value.name === "string" ? value.name : parent.name,
        category: typeof value.category === "string" ? value.category : parent.category
      });
    }
  };
  visit(exposes);
  return [...features.values()];
}

/**
 * Ölçüm listesi: tanımdan gelenler + tanımda olmayıp durumda görünen skaler anahtarlar.
 * İkinci kaynak doğrudan kip / eksik tanım / `inferred-exposes` durumunda değerin yine
 * görünmesini sağlar. Kumanda olan özellikler dışarıda kalır — onların kendi satırı var.
 */
function deviceReadings(
  exposes: unknown,
  state: JsonObject,
  controls: DeviceControlView[]
): DeviceReadingView[] {
  const controlled = new Set(controls.map((control) => control.property));
  const readings: DeviceReadingView[] = [];
  const seen = new Set<string>();
  for (const feature of readableFeatures(exposes)) {
    if (controlled.has(feature.property)) continue;
    seen.add(feature.property);
    readings.push({ ...feature, value: scalar(state[feature.property]) ?? null });
  }
  for (const [property, raw] of Object.entries(state)) {
    if (seen.has(property) || controlled.has(property) || nonReadingProperties.has(property)) continue;
    const value = scalar(raw);
    if (value === undefined) continue;
    seen.add(property);
    readings.push({ property, name: readingLabel(undefined, property), type: "", value });
  }
  // Asıl ölçümler önce, `config`/`diagnostic` sonra; her küme kendi içinde alfabetik.
  const rank = (reading: DeviceReadingView): number => (reading.category ? 1 : 0);
  return readings.sort((left, right) =>
    rank(left) - rank(right) || left.property.localeCompare(right.property, "en"));
}

/**
 * Doğrudan Zigbee bağlamasında (binding) komut taşıyabilen kümeler: sahne, aç/kapa, seviye, renk.
 * `zigbee-herdsman` sayı, Zigbee2MQTT ad verir; iki yazım da tanınır.
 */
const bindableClusterIds: ReadonlySet<number> = new Set([5, 6, 8, 768]);
const bindableClusterNames: ReadonlySet<string> = new Set([
  "genScenes", "genOnOff", "genLevelCtrl", "lightingColorCtrl"
]);

/**
 * Bağlama yalnız kaynağın **çıkış** (client) kümesinden kurulabilir — cihaz o kümeyi
 * bildirmiyorsa bağlama kurulsa bile hiçbir komut gitmez. Bazı kumandalar tuş olayını
 * satıcıya özel bir komutla `genOnOff` **giriş** kümesi üzerinden yollar; onlar hiç
 * bağlanamaz, yolları köprü üzerinden otomasyondur. Kural jeneriktir: model ya da satıcı
 * listesi yok, yalnız cihazın kendi bildirdiği kümelere bakılır.
 */
export function hasBindableOutputCluster(device: DeviceView, endpointId?: number): boolean {
  return (device.endpoints ?? [])
    .filter((endpoint) => endpointId === undefined || endpoint.id === endpointId)
    .some((endpoint) =>
      endpoint.outputClusters.some((cluster) =>
        typeof cluster === "number"
          ? bindableClusterIds.has(cluster)
          : bindableClusterNames.has(cluster)
      )
    );
}

/** Cihaz başına saklanan en fazla gözlenmiş basış değeri; bozuk cihaz listeyi şişirmesin. */
const maximumObservedActions = 64;

/** Olay akışına (ve otomasyon tetikleyicilerine) giren taban özellikler. */
const interestingEventProperties = new Set([
  "action", "state", "contact", "occupancy", "presence", "smoke",
  "carbon_monoxide", "battery_low", "alarm", "lock_state", "water_leak"
]);

/**
 * Eşleşme **taban ada** göre yapılır: çok kanallı bir cihazın `state_l2` gibi kanal ekli
 * özellikleri de olay üretmeli. Aksi hâlde sihirbazda kanal seçen kullanıcı hiç ateşlenmeyen,
 * sessizce ölü bir otomasyon kuruyordu. Kanal adı serbest metindir (`l2`, `left`, `top`),
 * o yüzden model/satıcı listesi yerine ekin tamamı soyulup taban ad aranır.
 */
export function isInterestingEventProperty(property: string): boolean {
  if (interestingEventProperties.has(property)) return true;
  const parts = property.split("_");
  for (let count = parts.length - 1; count >= 1; count -= 1) {
    if (interestingEventProperties.has(parts.slice(0, count).join("_"))) return true;
  }
  return false;
}

/**
 * Otomasyon yoluna **girmeyen** meta/gürültü alanları. Burada ekleme değil **dışlama** listesi
 * kullanılır: kural motoru cihazın bildirdiği her skaler değişimi görebilmeli (`brightness`,
 * `color_temp`, `temperature`, `humidity`…), yalnız taşıma ve firmware gürültüsü dışarıda kalmalı.
 *
 * Bu liste olay **günlüğünü** (cihaz etkinlik listesi) etkilemez; o hâlâ `interestingEventProperty`
 * dar kümesinden beslenir — parlaklık kaydırmaları "Ev hareketleri" listesini boğmasın diye.
 */
const noisyEventProperties = new Set([
  "linkquality",
  "last_seen",
  "elapsed",
  "update",
  "update_available",
  "update_state"
]);

/**
 * Otomasyon motorunun gördüğü geniş akışın süzgeci. Dar küme bunun **alt kümesidir**: dar kümedeki
 * hiçbir özellik burada elenmez, yani olay günlüğüne giren her şey motora da ulaşır.
 */
export function isAutomationEventProperty(property: string): boolean {
  return !noisyEventProperties.has(property);
}

/**
 * Olay değeri skaler olmalıdır. Tek istisna renktir: Zigbee2MQTT rengi nesne olarak bildirir
 * (`{x,y}`), ama renk kanalı otomasyonda izlenebilsin diye ortak `#rrggbb` diline indirilir —
 * hedefe yazarken aynı normalizasyon onu geri xy'ye çevirir.
 */
function eventScalar(property: string, value: unknown): JsonScalar | undefined {
  const direct = scalar(value);
  if (direct !== undefined) return direct;
  if (!isObject(value)) return undefined;
  return property === "color" || property.startsWith("color_") ? colorHex(value) : undefined;
}

/**
 * Takma ad ve rol haritaları dışarıda tutulur ve dışarıda değiştirilir (API uçları, yedek geri
 * yükleme). Cihaz görünümü önbelleği bayat kalmasın diye harita örneğinin `set`/`delete`/`clear`
 * çağrıları sarmalanır: her mutasyon dinleyicileri uyarır. Aynı harita birden çok kez gözlenebilir;
 * dinleyiciler tek bir kümede birikir, harita yalnızca bir kez sarmalanır.
 */
const observedMapListeners = Symbol.for("villa-bridge.observed-map-listeners");

type ObservedMap = Map<string, unknown> & { [observedMapListeners]?: Set<() => void> };

function observeMapMutations(map: Map<string, unknown>, onChange: () => void): void {
  const observed = map as ObservedMap;
  const existing = observed[observedMapListeners];
  if (existing) {
    existing.add(onChange);
    return;
  }
  const listeners = new Set<() => void>([onChange]);
  Object.defineProperty(observed, observedMapListeners, {
    value: listeners,
    enumerable: false,
    configurable: true
  });
  for (const method of ["set", "delete", "clear"] as const) {
    const original = observed[method] as unknown as (...args: unknown[]) => unknown;
    Object.defineProperty(observed, method, {
      value: function patched(this: ObservedMap, ...args: unknown[]): unknown {
        const result = original.apply(this, args);
        for (const listener of listeners) listener();
        return result;
      },
      writable: true,
      configurable: true,
      enumerable: false
    });
  }
}

interface DeviceSnapshot {
  revision: number;
  list: DeviceView[];
  byId: Map<string, DeviceView>;
}

export class DeviceStore {
  private readonly aliases: Map<string, string>;
  private readonly states = new Map<string, StateEntry>();
  /**
   * `sourceName|property` → değerin kaçtan beri aynı olduğu. Anahtar durum haritasıyla aynı
   * uzayda tutulur (kaynak adı); okuma yolu IEEE adresini kaynak adına çevirir (UID kuralı).
   */
  private readonly channelSince = new Map<string, ChannelSinceEntry>();
  private readonly availability = new Map<string, "online" | "offline">();
  /** `sourceName` → son sinyal/son görülme. Durum haritasıyla aynı anahtar uzayında yaşar. */
  private readonly links = new Map<string, DeviceLinkEntry>();
  private devices: BridgeDevice[] = [];
  private groups: BridgeGroup[] = [];
  private bridgeOnline = false;
  private mqttConnected = false;
  private lastMessageAt: Date | null = null;
  private pairingUntil: Date | null = null;
  private pairingStatus: "closed" | "open" | "pending" | "error" = "closed";
  private pairingMessage: string | null = null;
  private pairingDevice: PairingDevice | null = null;
  private mode: "shadow" | "direct" = "shadow";
  private lowBatteryThreshold = 15;
  private readonly events: DeviceEventView[];
  /**
   * Cihazın **gerçekten yaydığı** `action` değerleri, konu adına göre. Cihaz tanımı (`exposes`)
   * tek kaynak olamaz: desteklenmeyen ya da eksik tanımlanmış bir kumandada `action` sözlüğü hiç
   * gelmez veya değer listesi boştur; o zaman düğme türetilmez, otomasyon sihirbazında da hiç
   * görünmez. Yayında görülen değer bunu kapatır — model listesi değil, cihazın kendi kanıtı.
   */
  private readonly observedActions = new Map<string, Set<string>>();
  /**
   * Cihaz görünümünü etkileyen her mutasyonda artar. Memo bu sayaca bağlıdır: sayaç aynıysa
   * altta hiçbir şey değişmemiştir ve liste yeniden kurulmaz (200 cihaz × 64 kural darboğazı).
   */
  private revision = 0;
  private snapshot: DeviceSnapshot | null = null;
  /** §6 — otomasyon motorunun geniş akış dinleyicisi; olay günlüğünden bağımsızdır. */
  private automationListener: ((events: DeviceEventView[]) => void) | null = null;
  /**
   * Yalnızca cihaz listesinin kendisi (exposes/kumanda topolojisi) değişince artar. Durum
   * mesajları bunu kıpırdatmaz; otomasyon doğrulaması gibi topolojiye bakan önbellekler
   * için doğru anahtar budur.
   */
  private topology = 0;

  constructor(
    aliases: Map<string, string>,
    private imagePreferences: DeviceImagePreferences = { devices: {}, models: {} },
    initialEvents: DeviceEventView[] = [],
    private readonly onEventsChanged?: (
      events: DeviceEventView[],
      added: DeviceEventView[]
    ) => void,
    /** Kullanıcının seçtiği roller; IEEE adresine göre, paylaşılan canlı harita. */
    private readonly roles: Map<string, DeviceRole> = new Map()
  ) {
    this.aliases = aliases;
    this.events = initialEvents.slice(0, 200);
    // Görülen basışlar olay günlüğüyle birlikte diske yazılır; yeniden başlatmada düğmeler
    // ilk basışı beklemeden geri gelsin diye defter oradan tohumlanır.
    for (const event of this.events) this.rememberAction(event.sourceName, event.property, event.value);
    const invalidate = (): void => this.invalidate();
    observeMapMutations(aliases, invalidate);
    observeMapMutations(roles, invalidate);
  }

  /**
   * §6 — otomasyon motorunun dinlediği **geniş** akış. Olay günlüğünün dar akışından ayrıdır:
   * aynı değişimler bir kez hesaplanır, iki ayrı yola dağıtılır. Dinleyici verilmezse geniş akış
   * hiç üretilmez (eski davranış).
   */
  setAutomationEventListener(listener: (events: DeviceEventView[]) => void): void {
    this.automationListener = listener;
  }

  /** Cihaz görünümü önbelleğini düşürür; bir sonraki okuma listeyi yeniden kurar. */
  invalidate(): void {
    this.revision += 1;
  }

  /** Cihaz topolojisi sürümü — yalnızca cihaz listesi değişince artar. */
  get topologyRevision(): number {
    return this.topology;
  }

  setImagePreferences(preferences: DeviceImagePreferences): void {
    this.imagePreferences = preferences;
    this.invalidate();
  }

  setLowBatteryThreshold(threshold: number): void {
    if (!Number.isInteger(threshold) || threshold < 5 || threshold > 50) {
      throw new Error("Düşük pil eşiği 5-50 arasında olmalıdır.");
    }
    const previousThreshold = this.lowBatteryThreshold;
    this.lowBatteryThreshold = threshold;
    if (previousThreshold === threshold) return;
    // Eşik uyarıları cihaz görünümünde taşınır.
    this.invalidate();
    const at = new Date().toISOString();
    const events: DeviceEventView[] = [];
    for (const [sourceName, state] of this.states) {
      if (typeof state.value.battery !== "number") continue;
      const wasLow = state.value.battery <= previousThreshold;
      const low = state.value.battery <= threshold;
      if (low !== wasLow) {
        events.push({ sourceName, property: "battery_threshold", value: low, at });
      }
    }
    this.recordEvents(events);
    // Köprünün kendi ürettiği olaylar iki yola da girer: eşik uyarısı hem listede hem kuralda.
    this.emitAutomationEvents(events);
  }

  setMqttConnected(connected: boolean): void {
    this.mqttConnected = connected;
  }

  setMode(mode: "shadow" | "direct"): void {
    this.mode = mode;
  }

  ingest(relativeTopic: string, payload: Buffer): void {
    this.lastMessageAt = new Date();
    const topic = normalizeTopicPart(relativeTopic);
    const parsed = this.parse(payload);
    if (topic === "bridge/devices" && Array.isArray(parsed)) {
      const previousNames = new Set(this.devices.map(deviceSourceName));
      this.devices = parsed.filter(isObject) as BridgeDevice[];
      // Silinen cihazın defter kayıtları kalmasın; aynı ad geri gelirse süre sıfırdan sayılır.
      for (const name of previousNames) {
        if (!this.devices.some((device) => deviceSourceName(device) === name)) {
          this.forgetChannelSince(name);
        }
      }
      this.topology += 1;
      this.invalidate();
      return;
    }
    if (topic === "bridge/groups" && Array.isArray(parsed)) {
      this.groups = parsed.filter(isObject) as BridgeGroup[];
      return;
    }
    if (topic === "bridge/state") {
      this.bridgeOnline = parsed === "online" || (isObject(parsed) && parsed.state === "online");
      return;
    }
    if (topic === "bridge/response/permit_join" && isObject(parsed)) {
      const status = parsed.status;
      if (status === "ok") {
        const time = isObject(parsed.data) && typeof parsed.data.time === "number" ? parsed.data.time : 0;
        this.setPairingOpen(time);
      } else {
        this.pairingStatus = "error";
        this.pairingMessage = typeof parsed.error === "string" ? parsed.error : "Eşleştirme açılamadı.";
        this.invalidate();
      }
      return;
    }
    if (topic === "bridge/event" && isObject(parsed)) {
      this.ingestPairingEvent(parsed);
      return;
    }
    if (topic.startsWith("bridge/") || topic.endsWith("/set") || topic.endsWith("/get")) return;
    if (topic.endsWith("/availability")) {
      const name = topic.slice(0, -"/availability".length);
      const state = typeof parsed === "string" ? parsed : isObject(parsed) ? parsed.state : undefined;
      if (state === "online" || state === "offline") {
        if (this.availability.get(name) !== state) {
          const lastAvailability = this.events.find((event) =>
            event.sourceName === name && event.property === "availability"
          );
          if (lastAvailability?.value !== state) {
            const events: DeviceEventView[] = [{
              sourceName: name,
              property: "availability",
              value: state,
              at: new Date().toISOString()
            }];
            this.recordEvents(events);
            this.emitAutomationEvents(events);
          }
        }
        if (this.availability.get(name) !== state) this.invalidate();
        this.availability.set(name, state);
      }
      return;
    }
    if (isObject(parsed)) {
      const previous = this.states.get(topic)?.value ?? {};
      const at = new Date();
      // Değişimler bir kez hesaplanır, iki ayrı yola dağıtılır: dar küme olay günlüğüne (kullanıcının
      // gördüğü "Ev hareketleri"), geniş küme yalnız otomasyon motoruna.
      const events: DeviceEventView[] = [];
      const automationEvents: DeviceEventView[] = [];
      for (const [property, raw] of Object.entries(parsed)) {
        const value = eventScalar(property, raw);
        if (value === undefined) continue;
        // `action` anlık bir kenar olayıdır ve kalıcı duruma yazılmaz: aynı düğmeye arka arkaya
        // basılırsa iki ayrı olay üretilmeli. Diğer özellikler yalnızca değer değişince olay olur.
        const isAction = property === "action" || property.startsWith("action_");
        if (!isAction && eventScalar(property, previous[property]) === value) continue;
        if (isAction && typeof value === "string" && value.trim() === "") continue;
        if (isAction && this.rememberAction(topic, property, value)) this.topology += 1;
        const event: DeviceEventView = { sourceName: topic, property, value, at: at.toISOString() };
        if (isInterestingEventProperty(property)) events.push(event);
        if (isAutomationEventProperty(property)) automationEvents.push(event);
      }
      if (typeof parsed.battery === "number") {
        const low = parsed.battery <= this.lowBatteryThreshold;
        const previousLow = typeof previous.battery === "number"
          ? previous.battery <= this.lowBatteryThreshold
          : undefined;
        if (low !== previousLow && (low || previousLow !== undefined)) {
          const event: DeviceEventView = {
            sourceName: topic,
            property: "battery_threshold",
            value: low,
            at: at.toISOString()
          };
          events.push(event);
          automationEvents.push(event);
        }
      }
      this.recordEvents(events);
      this.emitAutomationEvents(automationEvents);
      this.trackChannelSince(topic, parsed, at);
      // Gölge kipinin sinyal/son görülme yolu: Zigbee2MQTT ikisini de yayın payload'ında gönderir.
      this.recordDeviceLink(topic, {
        ...(parsed.linkquality === undefined ? {} : { linkquality: normalizedLinkQuality(parsed.linkquality) }),
        ...(typeof parsed.last_seen === "string" || typeof parsed.last_seen === "number"
          ? { lastSeen: String(parsed.last_seen) }
          : {})
      });
      this.states.set(topic, { value: parsed, updatedAt: at });
      this.invalidate();
      this.completeReconnectedPairingFromState(topic);
    }
  }

  /**
   * Gelen mesaj yolundan yakalanan sinyal/son görülme değerini cihaz başına saklar. Doğrudan kip
   * bunu ham Zigbee mesajından çağırır; gölge kipinde `ingest` Zigbee2MQTT payload'ından çağırır.
   * Verilmeyen alan **eskisini silmez**; hiç bilinmeyen alan hiç yazılmaz.
   */
  recordDeviceLink(sourceName: string, link: DeviceLinkEntry): void {
    const name = normalizeTopicPart(sourceName);
    const previous = this.links.get(name);
    const linkquality = normalizedLinkQuality(link.linkquality) ?? previous?.linkquality;
    const lastSeen = link.lastSeen ?? previous?.lastSeen;
    if (previous?.linkquality === linkquality && previous?.lastSeen === lastSeen) return;
    this.links.set(name, {
      ...(linkquality === undefined ? {} : { linkquality }),
      ...(lastSeen === undefined ? {} : { lastSeen })
    });
    this.invalidate();
  }

  /**
   * §4.1 — defteri günceller. **Yalnızca değer gerçekten değiştiyse** `since` tazelenir: aynı
   * değerin yeniden bildirilmesi süreyi sıfırlamaz, "1 dakikadır hareket var" ancak böyle çalışır.
   *
   * §4.2 — `reportedAt` ise **her raporda** tazelenir, değer değişmese bile: tazelik ölçütünün
   * sorduğu soru "ne zaman değişti" değil, "cihaz en son ne zaman konuştu". Aynı değer gelince
   * kayıt yerinde güncellenir, silinip yeniden eklenmez — böylece Map'in ekleme sırası
   * değişim sırası olmayı sürdürür ve tavan taşınca en eski **değişim** düşer.
   */
  private trackChannelSince(sourceName: string, payload: JsonObject, at: Date): void {
    for (const [property, raw] of Object.entries(payload)) {
      const value = scalar(raw);
      if (value === undefined) continue;
      const key = `${sourceName}|${property}`;
      const previous = this.channelSince.get(key);
      if (previous && previous.value === value) {
        previous.reportedAt = at;
        continue;
      }
      // Yeniden ekleme sırayı da tazeler: Map'in ekleme sırası = değişim sırası, en eski baştadır.
      this.channelSince.delete(key);
      this.channelSince.set(key, { value, since: at, reportedAt: at });
    }
    while (this.channelSince.size > maxChannelSinceEntries) {
      const oldest = this.channelSince.keys().next();
      if (oldest.done) break;
      this.channelSince.delete(oldest.value);
    }
  }

  private forgetChannelSince(sourceName: string): void {
    const prefix = `${sourceName}|`;
    for (const key of [...this.channelSince.keys()]) {
      if (key.startsWith(prefix)) this.channelSince.delete(key);
    }
  }

  /**
   * §2.1 — kanalın şu anki değeri ne zamandan beri aynı. Defter bellektedir: yeniden başlatmadan
   * sonra bilgi yoktur ve `null` döner; süre koşulu bunu kapalı tarafa yorumlar (§2.5).
   */
  stateSince(deviceId: string, property: string): Date | null {
    const sourceName = this.getDevice(deviceId)?.sourceName ?? deviceId;
    return this.channelSince.get(`${sourceName}|${property}`)?.since ?? null;
  }

  /**
   * §4.2 — kanal en son ne zaman rapor edildi. Aynı defterin ikinci damgası: bellektedir,
   * yeniden başlatmada boşalır ve `null` döner; tazelik ölçütü bunu kapalı tarafa yorumlar.
   */
  stateReportedAt(deviceId: string, property: string): Date | null {
    const sourceName = this.getDevice(deviceId)?.sourceName ?? deviceId;
    return this.channelSince.get(`${sourceName}|${property}`)?.reportedAt ?? null;
  }

  getEvents(limit = 20): DeviceEventView[] {
    return this.events.slice(0, Math.max(0, Math.min(100, limit)));
  }

  /**
   * Cihaz mesajından değil, köprünün kendi mekanizmalarından doğan olayları akışa yazar
   * (ör. otomatik onarım). Şema aynıdır: `property` küçük harf ve alt çizgi olmalıdır.
   */
  recordExternalEvent(sourceName: string, property: string, value: JsonScalar): void {
    const events = validateDeviceEvents([
      { sourceName, property, value, at: new Date().toISOString() }
    ]);
    this.recordEvents(events);
    this.emitAutomationEvents(events);
  }

  /**
   * Geniş akışı motora verir. Olay günlüğüne dokunmaz: iki yol bilerek ayrıdır, biri gürültülenirse
   * öbürü etkilenmez. Dinleyicinin hatası akışı kesmesin diye çağrı burada tutulmaz — çağıran
   * (index.ts) kendi hata yakalayıcısını kurar.
   */
  /**
   * Yayında görülen bir `action` değerini cihazın defterine yazar. Yeni bir değer eklendiyse
   * `true` döner — o zaman düğme topolojisi değişmiştir. Defter cihaz başına sınırlıdır:
   * bozuk/konuşkan bir cihaz listeyi şişirmesin.
   */
  private rememberAction(sourceName: string, property: string, value: unknown): boolean {
    if (property !== "action" && !property.startsWith("action_")) return false;
    if (typeof value !== "string") return false;
    const action = value.trim();
    if (action.length === 0 || action.length > 64) return false;
    let seen = this.observedActions.get(sourceName);
    if (!seen) {
      seen = new Set<string>();
      this.observedActions.set(sourceName, seen);
    }
    if (seen.has(action)) return false;
    if (seen.size >= maximumObservedActions) return false;
    seen.add(action);
    return true;
  }

  private emitAutomationEvents(events: DeviceEventView[]): void {
    if (events.length === 0 || !this.automationListener) return;
    this.automationListener(events.slice());
  }

  private recordEvents(events: DeviceEventView[]): void {
    if (events.length === 0) return;
    const added = events.slice();
    this.events.unshift(...events.reverse());
    if (this.events.length > 200) this.events.length = 200;
    // Son basılan düğme cihaz görünümünde taşınır.
    this.invalidate();
    this.onEventsChanged?.(this.events.slice(), added);
  }

  /** Olay akışındaki `sourceName` değerini kalıcı IEEE adresine çevirir (UID kuralı). */
  getDeviceIdBySourceName(sourceName: string): string | undefined {
    const device = this.devices.find((entry) => entry.friendly_name === sourceName);
    const id = device?.ieee_address ?? (
      this.devices.some((entry) => entry.ieee_address === sourceName) ? sourceName : undefined
    );
    return id?.toLowerCase();
  }

  getDevices(): DeviceView[] {
    return this.deviceSnapshot().list.slice();
  }

  getDevice(id: string): DeviceView | undefined {
    return this.deviceSnapshot().byId.get(id.toLowerCase());
  }

  /**
   * Memo: sürüm sayacı değişmedikçe liste ve IEEE→cihaz haritası yeniden kurulmaz. İkisi de
   * aynı yapımdan doğar, bu yüzden `getDevice` her zaman listedeki nesnenin ta kendisini verir.
   */
  private deviceSnapshot(): DeviceSnapshot {
    // Eşleştirme süresi zamanla dolar; sayacı artırabileceği için memo kontrolünden önce çalışır.
    this.expirePairingIfNeeded();
    const cached = this.snapshot;
    if (cached && cached.revision === this.revision) return cached;
    const list = this.buildDeviceViews();
    const snapshot: DeviceSnapshot = {
      revision: this.revision,
      list,
      byId: new Map(list.map((device) => [device.id, device]))
    };
    this.snapshot = snapshot;
    return snapshot;
  }

  private buildDeviceViews(): DeviceView[] {
    const pairingActive = this.pairingStatus === "open" || this.pairingStatus === "pending";
    // Olaylar en yeniden eskiye sıralı: her cihaz için ilk `action` kaydı son basılan düğmedir.
    const lastActions = new Map<string, DeviceEventView>();
    for (const event of this.events) {
      if (event.property !== "action" || typeof event.value !== "string") continue;
      if (!lastActions.has(event.sourceName)) lastActions.set(event.sourceName, event);
    }
    return this.devices
      .filter((device) => device.type !== "Coordinator")
      .map((device) => {
        const sourceName = device.friendly_name ?? device.ieee_address ?? "unknown";
        const id = (device.ieee_address ?? sourceName).toLowerCase();
        const state = this.states.get(sourceName);
        const link = this.links.get(sourceName);
        const lastSeen = state?.value.last_seen ?? link?.lastSeen;
        const availability: DeviceView["availability"] = this.availability.get(sourceName) ?? "unknown";
        const name = this.aliases.get(id) ?? sourceName;
        const exposes = device.definition?.exposes;
        const features = featureNames(exposes);
        // Sözlük + kanıt: cihaz tanımının bildirdiği basışlar ile cihazın gerçekten yaydıkları
        // birleşir. Tanımı olmayan/eksik kumandalar böylece ilk basıştan sonra düğme kazanır,
        // tanımı sağlam olanlarda ise liste aynen eskisi gibi kalır.
        const actionTypes = [...new Set([
          ...featureValues(exposes, "action"),
          ...(this.observedActions.get(sourceName) ?? [])
        ])].sort();
        const writable = writableFeatures(exposes);
        const image = deviceIdentity(
          id,
          device.definition?.model,
          device.manufacturer,
          this.imagePreferences
        );
        const stateValue = state?.value ?? {};
        const alerts: DeviceView["alerts"] = [];
        if (
          stateValue.battery_low === true
          || (
            typeof stateValue.battery === "number"
            && stateValue.battery <= this.lowBatteryThreshold
          )
        ) {
          alerts.push({
            code: "low_battery",
            severity: "warning",
            ...(typeof stateValue.battery === "number" ? { value: stateValue.battery } : {}),
            threshold: this.lowBatteryThreshold
          });
        }
        const buttons = deviceButtons(id, actionTypes, this.aliases);
        const lastActionEvent = lastActions.get(sourceName);
        const lastAction = lastActionEvent
          ? {
            action: String(lastActionEvent.value),
            buttonId: buttons.find((button) =>
              button.actions.some((action) => action.action === lastActionEvent.value)
            )?.id ?? null,
            at: lastActionEvent.at
          }
          : null;
        if (stateValue.smoke === true) alerts.push({ code: "smoke", severity: "critical" });
        if (stateValue.carbon_monoxide === true) {
          alerts.push({ code: "carbon_monoxide", severity: "critical" });
        }
        const detectedCategory = detectDeviceCategory(exposes);
        const role = this.roles.get(id) ?? "auto";
        // Rol kanal başınadır: her aç/kapa kanalı kendi sınıfını taşır, kanalın kaydı yoksa eski
        // cihaz seviyesi kayda düşer. Cihazın tek sınıfı gerektiğinde kanallardan türetilir —
        // lamba anahtarı yener, çünkü "anahtar" her açılıp kapanan kanalda çıkar.
        // Cihaz ayarı olarak işaretlenen aç/kapa satırları kanal değildir ("cihazı bul", çocuk
        // kilidi gibi): rol taşımazlar ve cihazın sınıfını belirlemezler.
        const isChannelControl = (control: DeviceControlView): boolean =>
          control.kind === "switch" && control.adminOnly !== true;
        const controls = deviceControls(id, name, writable, stateValue, this.aliases).map((control) => {
          if (!isChannelControl(control)) return control;
          const channelRole = resolveChannelRole(this.roles, id, control.id);
          return {
            ...control,
            role: channelRole,
            detectedCategory,
            category: resolveDeviceCategory(detectedCategory, channelRole)
          };
        });
        const channelCategories = controls
          .filter(isChannelControl)
          .map((control) => control.category);
        const effectiveRole: DeviceRole = channelCategories.includes("light")
          ? "light"
          : channelCategories.includes("switch")
          ? "switch"
          : role;
        return {
          id,
          sourceName,
          name,
          type: device.type ?? "Unknown",
          category: resolveDeviceCategory(detectedCategory, effectiveRole),
          detectedCategory,
          role,
          model: canonicalDeviceModel(device.definition?.model, device.manufacturer),
          image,
          vendor: device.definition?.vendor ?? null,
          description: device.definition?.description ?? null,
          supported: device.supported !== false,
          interviewCompleted: device.interview_completed !== false,
          preparing: pairingActive
            && this.pairingDevice?.id === id
            && this.pairingDevice.interviewCompleted !== true,
          availability,
          lastSeen: typeof lastSeen === "string" || typeof lastSeen === "number" ? String(lastSeen) : null,
          stateUpdatedAt: state?.updatedAt.toISOString() ?? null,
          // Sinyal defteri önce gelir; yayında `linkquality` hiç görülmediyse durumdan okunur.
          ...(() => {
            const linkquality = link?.linkquality ?? normalizedLinkQuality(stateValue.linkquality);
            return linkquality === undefined ? {} : { linkquality };
          })(),
          ...(typeof device.power_source === "string" && device.power_source.trim().length > 0
            ? { powerSource: device.power_source }
            : {}),
          ...(Number.isInteger(device.network_address) ? { networkAddress: device.network_address } : {}),
          otaSupported: device.definition?.ota === true || device.definition?.supports_ota === true,
          options: {
            transition: device.configured_options?.transition ?? 0,
            debounce: device.configured_options?.debounce ?? 0,
            retain: device.configured_options?.retain ?? false
          },
          endpoints: deviceEndpoints(device),
          features,
          actionTypes,
          buttons,
          lastAction,
          alerts,
          controls,
          readings: deviceReadings(exposes, stateValue, controls),
          state: stateValue
        };
      })
      .sort((a, b) => a.name.localeCompare(b.name, "en"));
  }

  getGroups(): GroupView[] {
    return this.groups
      .map((group) => {
        const sourceName = group.friendly_name ?? `group-${group.id ?? "unknown"}`;
        const id = `group-${group.id ?? sourceName}`.toLowerCase();
        return {
          id,
          sourceName,
          name: this.aliases.get(id) ?? sourceName,
          members: group.members?.length ?? 0,
          memberIds: (group.members ?? [])
            .map((member) => member.ieee_address?.toLowerCase())
            .filter((member): member is string => Boolean(member)),
          scenes: (group.scenes ?? []).flatMap((scene) =>
            Number.isInteger(scene.id)
              ? [{ id: Number(scene.id), name: scene.name?.trim() || `Scene ${scene.id}` }]
              : []
          ),
          state: this.states.get(sourceName)?.value ?? {}
        };
      })
      .sort((a, b) => a.name.localeCompare(b.name, "en"));
  }

  getHealth(): JsonObject {
    return {
      ok: this.mqttConnected && this.bridgeOnline,
      mode: this.mode,
      sourceConnected: this.mqttConnected,
      sourceOnline: this.bridgeOnline,
      mqttConnected: this.mqttConnected,
      sourceBridgeOnline: this.bridgeOnline,
      deviceCount: this.deviceSnapshot().list.length,
      groupCount: this.getGroups().length,
      stateCount: this.states.size,
      lastMessageAt: this.lastMessageAt?.toISOString() ?? null
    };
  }

  pairingRequested(seconds: number): void {
    this.pairingStatus = "pending";
    this.pairingUntil = new Date(Date.now() + seconds * 1_000);
    this.pairingMessage = "Koordinatörden onay bekleniyor.";
    this.pairingDevice = null;
    this.invalidate();
  }

  pairingRequestFailed(message: string): void {
    this.pairingStatus = "error";
    this.pairingUntil = null;
    this.pairingMessage = message;
    this.pairingDevice = null;
    this.invalidate();
  }

  getPairing(): JsonObject {
    this.expirePairingIfNeeded();
    return {
      status: this.pairingStatus,
      open: this.pairingStatus === "open" || this.pairingStatus === "pending",
      until: this.pairingUntil?.toISOString() ?? null,
      message: this.pairingMessage,
      device: this.pairingDevice
    };
  }

  private parse(payload: Buffer): unknown {
    const text = payload.toString("utf8");
    try {
      return JSON.parse(text);
    } catch {
      return text;
    }
  }

  private setPairingOpen(seconds: number): void {
    if (seconds > 0 && this.pairingStatus !== "pending" && this.pairingStatus !== "open") {
      this.pairingDevice = null;
    }
    this.pairingStatus = seconds > 0 ? "open" : "closed";
    this.pairingUntil = seconds > 0 ? new Date(Date.now() + seconds * 1_000) : null;
    this.pairingMessage = seconds > 0 ? "Yeni Zigbee cihazları eklenebilir." : null;
    this.invalidate();
  }

  private expirePairingIfNeeded(): void {
    if (!this.pairingUntil || this.pairingUntil.getTime() > Date.now()) return;
    this.pairingUntil = null;
    this.pairingStatus = "closed";
    this.pairingMessage = null;
    this.invalidate();
  }

  private completeReconnectedPairingFromState(sourceName: string): void {
    if (
      !this.pairingDevice?.reconnected
      || this.pairingDevice.interviewCompleted
    ) {
      return;
    }
    const device = this.devices.find((candidate) => {
      const id = candidate.ieee_address?.toLowerCase();
      const name = candidate.friendly_name ?? candidate.ieee_address;
      return id === this.pairingDevice?.id && name === sourceName;
    });
    if (!device) return;
    this.pairingDevice = {
      ...this.pairingDevice,
      interviewCompleted: true,
      supported: typeof device.supported === "boolean" ? device.supported : null
    };
    this.invalidate();
  }

  private ingestPairingEvent(event: JsonObject): void {
    if (this.pairingStatus !== "open" && this.pairingStatus !== "pending") return;
    if (!isObject(event.data)) return;
    const id = typeof event.data.ieee_address === "string"
      ? event.data.ieee_address.toLowerCase()
      : null;
    if (!id) return;
    if (event.type === "device_joined") {
      this.pairingDevice = {
        id,
        name: typeof event.data.friendly_name === "string" ? event.data.friendly_name : id,
        interviewCompleted: false,
        supported: null,
        reconnected: false
      };
      this.invalidate();
      return;
    }
    if (event.type === "device_announce") {
      const knownDevice = this.devices.find((device) =>
        device.ieee_address?.toLowerCase() === id
      );
      if (!knownDevice) return;
      this.pairingDevice = {
        id,
        name: typeof event.data.friendly_name === "string"
          ? event.data.friendly_name
          : knownDevice.friendly_name ?? id,
        interviewCompleted: false,
        supported: typeof knownDevice.supported === "boolean" ? knownDevice.supported : null,
        reconnected: true
      };
      this.invalidate();
      return;
    }
    if (
      event.type === "device_interview"
      && event.data.status === "successful"
      && this.pairingDevice?.id === id
    ) {
      this.pairingDevice = {
        ...this.pairingDevice,
        interviewCompleted: true,
        supported: typeof event.data.supported === "boolean" ? event.data.supported : null
      };
      this.invalidate();
    }
  }
}
