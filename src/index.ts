import Fastify, { type FastifyReply, type FastifyRequest } from "fastify";
import { createHash } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { registerAccessControl } from "./access-control.js";
import { AppearanceStore } from "./appearance-store.js";
import { loadAliases, saveAliases } from "./aliases.js";
import { AutomationEngine } from "./automation-engine.js";
import { AutomationRunLog } from "./automation-runs.js";
import { AutomationRunStateStore } from "./automation-run-state.js";
import { AutomationBackupStore } from "./automation-backup.js";
import { AutomationAutoOffStore, AutomationsStore } from "./automations.js";
import { CelestialService } from "./celestial-service.js";
import { AgentTokenStore } from "./agent-tokens.js";
import { AuthStore } from "./auth-store.js";
import { loadConfig } from "./config.js";
import {
  defaultCoordinatorPort,
  parseCoordinatorAddress,
  probeCoordinator
} from "./coordinator-probe.js";
import { normalizeControlValue, soleSwitchChannelId } from "./device-controls.js";
import { deviceMissingResponse } from "./device-departures.js";
import { DeviceNetworkEventLog } from "./device-network-events.js";
import { DeviceImageCache } from "./device-image-cache.js";
import { DeviceImagesStore } from "./device-images.js";
import { DeviceEventsStore } from "./device-events.js";
import { DeviceNotesStore } from "./device-notes.js";
import { loadDeviceRoles, removeDeviceRole, setDeviceRole } from "./device-roles.js";
import { DeviceStore, hasBindableOutputCluster } from "./device-store.js";
import { lastExternalConverterLoad } from "./external-converters.js";
import {
  HomeFavoritesStore,
  isHomeFavoriteControlKind,
  validateHomeFavorites
} from "./home-favorites.js";
import { HomeGroupsStore, homeGroupDeviceControlId, validateHomeGroups } from "./home-groups.js";
import { HomeVisibilityStore, validateHomeVisibility } from "./home-visibility.js";
import { HomeBackupService, type HomeBackupMode } from "./home-backup.js";
import { InstallationStateStore } from "./installation-state.js";
import { LocationStore, type HomeLocation } from "./location.js";
import {
  applyCoordinatorOwnership,
  createVillaBridgeDiscoveryRecord,
  resolveVillaBridgeNodeId,
  resolveVillaBridgeNodeRole,
  queryLanPeers,
  startLanDiscoveryResponder,
  villaBridgeCoordinatorId,
  villaBridgeDiscoveryPort,
  type VillaBridgeCoordinatorStatus
} from "./lan-discovery.js";
import { createPeerProbe, createPeerWatcher, type PeerWatcher } from "./peer-watch.js";
import { evaluatePeers } from "./failover-policy.js";
import { MatterbridgeClient } from "./matterbridge-client.js";
import { registerMcpEndpoint } from "./mcp.js";
import { MqttShadowSource } from "./mqtt-source.js";
import { getNetworkInfo } from "./network-info.js";
import { isDeviceRemovalConfirmation } from "./removal-confirmation.js";
import { registerRecentErrorApi } from "./recent-error-api.js";
import { RecentErrorLog } from "./recent-error-log.js";
import { SelfHealStateStore } from "./self-heal.js";
import { SettingsStore } from "./settings-store.js";
import type { ZigbeeSource } from "./source.js";
import type { JsonObject } from "./types.js";
import { loadThemePackages } from "./theme-package.js";
import { WeatherLocationStore, WeatherService } from "./weather.js";
import { WorldClockStore } from "./world-clock.js";
import {
  applyPendingZigbeeNetworkRestore,
  createZigbeeNetworkBackup,
  stageZigbeeNetworkRestore,
  validateZigbeeNetworkBackup
} from "./zigbee-backup.js";
import {
  buildFullBackup,
  maximumFullBackupBytes,
  parseBackupUpload,
  type ParsedBackupUpload
} from "./full-backup.js";

const config = await loadConfig();
if (config.zigbee && await applyPendingZigbeeNetworkRestore(config.zigbee.dataDir)) {
  console.log("Doğrulanmış Zigbee ağ yedeği geri yüklendi.");
}
const aliases = await loadAliases(config.aliasesFile);
const configPath = resolve(process.env.VILLA_BRIDGE_CONFIG ?? "config/default.yaml");
const imagesStore = new DeviceImagesStore(resolve(dirname(configPath), "device-images.json"));
const imageCache = new DeviceImageCache(resolve(dirname(configPath), "device-image-cache"));
// Disk önbelleği açılışta bir kez taranır: hangi modelin görseli var, hangisi upstream'de yok.
// Bu olmadan her yeniden başlatmadan sonra panel yok olan görselleri yeniden isterdi.
await imageCache.load();
let imagePreferences = await imagesStore.get();
const deviceEventsStore = new DeviceEventsStore(resolve(dirname(configPath), "device-events.json"));
// Kullanıcının seçtiği cihaz rolleri — IEEE adresine göre, yapılandırmanın yanındaki JSON'da.
const deviceRolesPath = resolve(dirname(configPath), "device-roles.json");
const deviceRoles = await loadDeviceRoles(deviceRolesPath);
const store = new DeviceStore(
  aliases,
  imagePreferences,
  await deviceEventsStore.get(),
  (events) => {
    void deviceEventsStore.save(events).catch((error) => {
      console.error(`Cihaz olay geçmişi kaydedilemedi: ${String(error)}`);
    });
  },
  deviceRoles
);
// §6 — otomasyon tetikleyicileri olay akışına takılır, poll yok. Motorun yolu cihaz etkinlik
// listesinden **ayrıdır**: orası dar kümeyle sınırlıdır, burası cihazın bildirdiği her skaler
// değişimi görür (parlaklık, ışık sıcaklığı, sıcaklık…). Liste gürültülenmez.
store.setAutomationEventListener((events) => {
  const deviceEvents = events.flatMap((event) => {
    const deviceId = store.getDeviceIdBySourceName(event.sourceName);
    return deviceId ? [{ deviceId, property: event.property, value: event.value }] : [];
  });
  if (deviceEvents.length === 0) return;
  void automationEngine.handleDeviceEvents(deviceEvents).catch((error) => {
    console.error(`Otomasyon olayı işlenemedi: ${String(error)}`);
  });
});
store.setLowBatteryThreshold(config.alerts.lowBatteryThreshold);
const matterbridge = new MatterbridgeClient(config.matterbridge.wsUrl);
const favoritesStore = new HomeFavoritesStore(resolve(dirname(configPath), "home-favorites.json"));
const homeGroupsStore = new HomeGroupsStore(resolve(dirname(configPath), "home-groups.json"));
// Görünürlük ev genelinde tek doğru: tablette gizlenen cihaz sunucudaki panelde de gizli.
const homeVisibilityStore = new HomeVisibilityStore(
  resolve(dirname(configPath), "home-visibility.json")
);
const automationGroupLookup = (groupId: string): { memberIds: string[] } | undefined =>
  store.getGroups().find((group) => group.id === groupId);
// Çalışma durumu tanım dosyasından ayrı durur: kural değişmediği hâlde `automations.json`
// yeniden yazılmasın (yedek/sürüm gürültüsü ve yazma anındaki kesinti riski).
const automationRunState = new AutomationRunStateStore(
  resolve(dirname(configPath), "automation-state.json"),
  {
    legacyAutomationsPath: resolve(dirname(configPath), "automations.json"),
    onError: (message) => {
      recentErrors.record({ operation: "automation-state", statusCode: 500, message });
      console.error(message);
    }
  }
);
const automationsStore = new AutomationsStore(
  resolve(dirname(configPath), "automations.json"),
  (deviceId) => store.getDevice(deviceId),
  () => store.topologyRevision,
  automationGroupLookup,
  automationRunState
);
// Ajan yazmalarının yedeği; yalnız `/mcp` yolunda alınır, panelden kaydetmede alınmaz.
const automationBackups = new AutomationBackupStore(
  resolve(dirname(configPath), "automations.json"),
  resolve(dirname(configPath), "automation-backups")
);
// Konum yalnız güneş tetikleyicisi için; panelden girilen değer yapılandırmanın önüne geçer.
const locationStore = new LocationStore(
  resolve(dirname(configPath), "location.json"),
  config.location?.timeZone ?? (Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC")
);
let homeLocation: HomeLocation | null = null;
let homeLocationSource: "file" | "config" | null = null;
try {
  homeLocation = await locationStore.get();
  homeLocationSource = homeLocation ? "file" : null;
} catch (error) {
  console.error(`Konum dosyası okunamadı: ${String(error)}`);
}
if (!homeLocation && config.location) {
  homeLocation = config.location;
  homeLocationSource = "config";
}
const celestialService = new CelestialService(() => homeLocation);
const deviceNotesStore = new DeviceNotesStore(resolve(dirname(configPath), "device-notes.json"));
const installationStateStore = new InstallationStateStore(
  resolve(dirname(configPath), "installation-state.json")
);
const authStore = new AuthStore(resolve(dirname(configPath), "auth.json"));
// Ajan token'ları kullanıcı hesaplarından ayrı dosyada: süresiz yaşar, çerez taşımaz.
const agentTokenStore = new AgentTokenStore(resolve(dirname(configPath), "agent-tokens.json"));
// Yedek yalnızca ev yapılandırmasını kapsar: parola özetleri, ağ anahtarı ve kuruluma
// özel durum dosyaları bilerek dışarıda bırakılmıştır.
const homeBackupService = new HomeBackupService({
  paths: {
    automations: resolve(dirname(configPath), "automations.json"),
    aliases: config.aliasesFile,
    homeGroups: resolve(dirname(configPath), "home-groups.json"),
    favorites: resolve(dirname(configPath), "home-favorites.json"),
    homeVisibility: resolve(dirname(configPath), "home-visibility.json"),
    deviceNotes: resolve(dirname(configPath), "device-notes.json"),
    deviceImages: resolve(dirname(configPath), "device-images.json")
  },
  aliases,
  knownDeviceIds: () => store.getDevices().map((device) => device.id),
  automationLookup: (deviceId) => store.getDevice(deviceId),
  automationGroupLookup: (groupId) => automationGroupLookup(groupId)
});
/**
 * Konak rolü zaten var (LAN keşfi ve devir mantığı bunu kullanır); koordinatör adresi de aynı
 * ayrımı miras alır: seri yol (USB çubuk) yalnız sunucu/Pi konağında anlamlıdır, Android
 * tablette hiç açılamaz. Panel bu kararı istemcide tahmin ETMEZ, sunucudan gelen bayrağa bakar.
 */
const nodeRole = resolveVillaBridgeNodeRole();
const zigbeeSerialSupported = nodeRole !== "android";
const settingsStore = config.zigbee?.configurationFile ? new SettingsStore(
  configPath,
  config.zigbee.configurationFile,
  {
    zigbee: { adapterUrl: config.zigbee.serial.path, channel: config.zigbee.network.channel },
    mqtt: { url: config.mqtt.url, baseTopic: config.mqtt.baseTopic },
    matter: { wsUrl: config.matterbridge.wsUrl },
    homeAssistant: { discoveryEnabled: config.homeAssistant.discoveryEnabled },
    alerts: { lowBatteryThreshold: config.alerts.lowBatteryThreshold },
    selfHealing: {
      enabled: config.selfHealing.enabled,
      probeOffline: config.selfHealing.probeOffline
    },
    debug: { enabled: config.debug.enabled }
  },
  { allowSerialAdapter: zigbeeSerialSupported }
) : null;
const recentErrors = new RecentErrorLog(50, config.debug.enabled);
/**
 * Hava durumu evde tek kaynaktır: şehir de veri de burada durur, panel yalnız bunu okur.
 * Konum yapılandırmanın yanındaki `weather-location.json`da; evin koordinatından (`location.json`,
 * güneş tetikleyicisi) bilerek ayrıdır — biri kurulum ayarı, öbürü "hangi şehrin havası".
 */
const weatherService = new WeatherService({
  store: new WeatherLocationStore(resolve(dirname(configPath), "weather-location.json")),
  onError: (message) => {
    recentErrors.record({ operation: "weather", statusCode: 503, message });
  }
});
/**
 * Dünya saati şehirleri de evin ayarı: hava konumuyla aynı yerde, ayrı bir dosyada durur
 * (`world-clock.json`). Hava dosyasıyla birleştirilmedi — biri "hangi şehrin havası", öbürü
 * "saat panelinde hangi şehirler"; ayrı yazılır, ayrı yetkilenir.
 */
const worldClockStore = new WorldClockStore(resolve(dirname(configPath), "world-clock.json"));
const nodeId = resolveVillaBridgeNodeId(nodeRole);
const discoveryRecord = createVillaBridgeDiscoveryRecord(
  nodeRole,
  config.mode,
  config.http.port,
  {
    nodeId,
    state: "standby",
    // Kurulum bitmeden koordinatör kimliği duyurulmaz: ortada sahiplenilen bir koordinatör yok.
    coordinatorId: villaBridgeCoordinatorId(
      config.setupPending ? undefined : config.zigbee?.serial.path
    )
  }
);
/**
 * Paylaşılan çalışma zamanı (apps/runtime) failover protokolünü yürütüyorsa bu kancayı kurar:
 * duyuru kaydı, epoch ve sahiplik kararı ONUN. Çekirdek yalnız koordinatör sonucunu bildirir.
 * Kanca yoksa (çekirdek doğrudan `node dist/index.js` ile açıldı) çekirdek kendi duyurusunu
 * yapar ve `source.start()` öncesi LAN'da başka sahip arar (aşağıdaki kapı).
 */
interface FailoverHook {
  record: () => JsonObject;
  epoch: () => number;
  status: () => JsonObject;
  coordinatorStatus: (status: VillaBridgeCoordinatorStatus, error: string | null) => void;
}
const failoverHook = (globalThis as typeof globalThis & { __villaBridgeFailover?: FailoverHook })
  .__villaBridgeFailover ?? null;
/** Koordinatör oturumunun durumu; `source.start()` sonucuna göre güncellenir. */
let coordinatorStatus: VillaBridgeCoordinatorStatus = "starting";
let coordinatorError: string | null = null;
let peerWatcher: PeerWatcher | null = null;
/** `state` rolden değil koordinatör sahipliğinden türetilir (tablet-failover-plani.md §3.2). */
const applyCoordinatorStatus = (status: VillaBridgeCoordinatorStatus): void => {
  coordinatorStatus = status;
  applyCoordinatorOwnership(discoveryRecord, status);
  if (failoverHook) {
    discoveryRecord.epoch = failoverHook.epoch();
    failoverHook.coordinatorStatus(status, coordinatorError);
  }
};
const nodeStatus = (): JsonObject => ({
  nodeId,
  role: nodeRole,
  state: discoveryRecord.state,
  epoch: discoveryRecord.epoch,
  priority: discoveryRecord.priority,
  coordinatorStatus,
  coordinatorError,
  peerWatch: (peerWatcher?.status() ?? null) as JsonObject | null,
  failover: failoverHook ? failoverHook.status() : null
});
store.setMode(config.mode);
/**
 * Cihaz ağ üyeliği günlüğü — katıldı/düştü/silindi. Hata ayıklama anahtarından **bağımsız**
 * olarak hep yazılır: "dün gece ne oldu" sorusu, sorun yaşandıktan sonra sorulur.
 */
const deviceNetworkEventLog = new DeviceNetworkEventLog(
  resolve(dirname(configPath), "device-network-events.jsonl"),
  {
    onError: (message) => {
      console.error(message);
      recentErrors.record({ operation: "device-network-events", statusCode: 500, message });
    }
  }
);
type DirectConnectionState = { connected: boolean; error: string | null; retryInMs: number | null };
let handleDirectConnectionState: (state: DirectConnectionState) => void = () => undefined;
let source: ZigbeeSource;
if (config.setupPending) {
  // Kurulum bitmeden koordinatör SAHİPLENİLMEZ. `DirectZigbeeSource` hiç kurulmaz:
  // yer tutucu ağ ayarlarıyla bağlanmak var olan bir Zigbee ağını yeniden kurabilir.
  const { SetupZigbeeSource } = await import("./setup-zigbee-source.js");
  source = new SetupZigbeeSource();
  console.warn(
    "Villa Bridge kurulum kipinde açıldı: koordinatör adresi girilmedi, hiçbir Zigbee "
    + "oturumu başlatılmadı. Kuruluma panelden devam edin."
  );
} else if (config.mode === "direct") {
  if (!config.zigbee) throw new Error("Doğrudan Zigbee ayarları bulunamadı.");
  const { DirectZigbeeSource } = await import("./direct-zigbee-source.js");
  const selfHealStateStore = new SelfHealStateStore(
    resolve(dirname(configPath), "self-heal-state.json")
  );
  source = new DirectZigbeeSource(
    config.zigbee,
    config.mqtt,
    store,
    config.homeAssistant.discoveryEnabled,
    aliases,
    undefined,
    {
      enabled: config.selfHealing.enabled,
      probeOffline: config.selfHealing.probeOffline,
      state: await selfHealStateStore.get(),
      persist: (state) => {
        void selfHealStateStore.save(state).catch((error) => {
          console.error(`Otomatik onarım durumu kaydedilemedi: ${String(error)}`);
        });
      },
      recordFailure: (deviceId, message) => {
        recentErrors.record({ operation: "self-heal", statusCode: 503, message: `${deviceId}: ${message}` });
      }
    },
    deviceNetworkEventLog,
    {
      enabled: true,
      initialDelayMs: 10_000,
      maximumDelayMs: 60_000,
      onStateChange: (state) => handleDirectConnectionState(state)
    }
  );
} else {
  // Shadow modda koordinatör Zigbee2MQTT'nin; ağ üyeliği olayları için karşılık yok, günlük
  // sessizce boş kalır — kaynak kırılmaz.
  source = new MqttShadowSource(config.mqtt, store);
}
// Çalışma günlüğü JSONL: olay başına ekleme, tam JSON yeniden yazma yok (HANDOFF 2026-08-04 §6).
const automationRunLog = new AutomationRunLog(
  resolve(dirname(configPath), "automation-runs.jsonl"),
  {
    // Kalıcılık hatası sessiz kalmasın (HANDOFF §7).
    onError: (message) => {
      console.error(message);
      recentErrors.record({ operation: "automation-runs", statusCode: 500, message });
    }
  }
);
const automationEngine = new AutomationEngine({
  store: automationsStore,
  source,
  // Bekleyen "sonra kapat" kayıtları yeniden başlatmayı atlatır (§9).
  autoOffStore: new AutomationAutoOffStore(
    resolve(dirname(configPath), "automation-auto-off.json")
  ),
  location: () => homeLocation,
  deviceState: (deviceId, property) => {
    const value = store.getDevice(deviceId)?.state[property];
    return typeof value === "string" || typeof value === "boolean"
      || (typeof value === "number" && Number.isFinite(value))
      ? value
      : undefined;
  },
  // §4.1 — "şu kadar süredir böyleyse" koşulunun okuduğu değişim defteri.
  stateSince: (deviceId, property) => store.stateSince(deviceId, property),
  // §4.2 — tazelik penceresinin (`freshWithinSeconds`) okuduğu rapor zamanı defteri.
  stateReportedAt: (deviceId, property) => store.stateReportedAt(deviceId, property),
  // Eylem değeri panelin geçtiği normalizasyondan geçsin diye aynı kumanda arayıcısı.
  controls: (deviceId) => store.getDevice(deviceId),
  runLog: automationRunLog
});
let automationStartPromise: Promise<void> | null = null;
const ensureAutomationEngineStarted = (): Promise<void> => {
  if (automationStartPromise) return automationStartPromise;
  automationEngine.start();
  automationStartPromise = automationEngine.restoreAutoOff().catch((error) => {
    const message = `Bekleyen otomasyon kapatmaları geri yüklenemedi: ${String(error)}`;
    recentErrors.record({ operation: "automation-auto-off", statusCode: 503, message });
    console.error(message);
  });
  return automationStartPromise;
};
handleDirectConnectionState = (state): void => {
  if (state.connected) {
    applyCoordinatorStatus("ready");
    coordinatorError = null;
    void ensureAutomationEngineStarted();
    return;
  }
  applyCoordinatorStatus("coordinator-unavailable");
  const retry = state.retryInMs === null
    ? ""
    : ` Yeniden deneme ${Math.round(state.retryInMs / 1_000)} saniye içinde.`;
  coordinatorError = `${state.error ?? "Koordinatör bağlantısı yok."}${retry}`;
};
const app = Fastify({ logger: true, bodyLimit: 30 * 1024 * 1024 });
await registerAccessControl(app, authStore, {
  secureCookies: process.env.VILLA_BRIDGE_SECURE_COOKIES === "true",
  agentTokens: agentTokenStore,
  mcpAllowedOrigins: config.mcp.allowedOrigins,
  // Yanlış yönetici PIN'i denemeleri hata ayıklama listesine düşsün: kaba kuvvet denemesi
  // sunucu günlüğüne değil, kullanıcının gördüğü ekrana da yansımalı.
  recordError: (entry) => recentErrors.record(entry)
});
/*
 * Önbellek tabanı: kendi politikasını koymayan her yanıt saklanamaz olsun. API gövdeleri cihaz
 * durumu taşır; başlıksız yanıtta tarayıcının sezgisel önbelleklemesi orada da eski veri gösterir.
 * Statik varlıklar `sendAsset` içinde kendi `no-cache` + ETag başlığını koyar ve buradan dokunulmaz.
 * Rota tablosuna bağlı değil: yeni rota eklendiğinde de varsayılan güvenli tarafta kalır.
 */
app.addHook("onSend", (_request, reply, _payload, done) => {
  if (reply.getHeader("cache-control") === undefined) reply.header("Cache-Control", "no-store");
  done();
});
/**
 * Ajan ucu: cihaz okuma, tek kanal yazma ve otomasyon araçları. Kimlik kapısı yukarıdaki tabloda.
 *
 * Yazma yolları bilerek panelin kullandığı yolların **aynısı**: kumanda `source.setDevice`,
 * kurallar `automationsStore.save()` (doğrulama, kilit/siren ve döngü koruması orada). Ajan yoluna
 * özel tek şey yedektir: her kural yazmasından **önce** `automations.json` bir kenara kopyalanır,
 * panel bu yedeğe dönebilsin diye.
 */
registerMcpEndpoint(app, {
  devices: () => store.getDevices(),
  homeGroups: () => homeGroupsStore.get(),
  setDevice: (deviceId, payload) => source.setDevice(deviceId, payload),
  automations: {
    list: () => automationsStore.get(),
    save: async (automations) => {
      await automationBackups.capture();
      return automationsStore.save(automations);
    },
    run: (id) => automationEngine.run(id)
  },
  allowDangerousControls: () => config.mcp.allowDangerousControls
});
registerRecentErrorApi(app, recentErrors);

/**
 * Cihaz ağ olayları — en yeniden eskiye. Hata ayıklama ekranının ikinci listesi; yetkisi
 * `/api/debug/errors` ile aynıdır: yetki tablolarında listelenmediği için yönetici ister.
 */
app.get<{ Querystring: { limit?: string } }>("/api/debug/network-events", async (request, reply) => {
  try {
    const limit = Number(request.query.limit ?? 200);
    return {
      ok: true,
      events: await deviceNetworkEventLog.read({ limit: Number.isFinite(limit) ? limit : 200 })
    };
  } catch (error) {
    return reply.code(503).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});
/**
 * Önbellek doğrulayıcıları.
 *
 * Panel dosyaları uzun süre **hiçbir** önbellek başlığı olmadan sunuldu. Başlıksız yanıtta
 * tarayıcı sezgisel (heuristic) önbellekleme yapar: güncellemeden sonra eski CSS/JS ile yeni
 * HTML'i karıştırıp panelin yarısını düşürür (çeviriler gelmez, giriş "operationFailed" verir).
 * Hard refresh çözer ama kullanıcıdan bunu beklemek çözüm değil.
 *
 * Politika: statik içerik **içerikten türetilmiş güçlü ETag** + `Cache-Control: no-cache`
 * — gövde saklanır ama her kullanımda doğrulanır; değişmediyse gövdesiz 304 döner (LAN'da
 * bedava). Başlığını kendi koymayan her yanıt (API'ler) aşağıdaki `onSend` kancasıyla
 * `no-store` alır. Dosya adı listesi yok: ETag, açılışta belleğe okunan gövdenin kendisinden
 * hesaplanır, dolayısıyla panel dosyası ekleme/çıkarma akışı değişmez.
 *
 * NEDEN `?v=` / içerik hash'li dosya adı DEĞİL: sürüm damgası yalnız "yeni HTML + eski JS"
 * eşleşmemesini çözer. Panelde yaşanan arıza bunun TERSİ — "eski HTML + yeni JS": eski HTML
 * eski damgayı ister, sunucuda o damga diye bir şey yoktur, sonuç yine karışık sürümdür.
 * Tek gerçek kaldıraç HTML'in asla bayat kullanılmamasıdır (aşağıdaki `htmlCacheControl`),
 * ve o sağlanınca damganın kazandıracağı şey LAN'da birkaç milisaniyelik 304'tür. Karşılığında
 * `index.html`'i açılışta yeniden yazmak (panel-graph'ın doğruladığı `<script src>` listesinden
 * ayrışan ikinci bir gerçek) gerekirdi; alınmadı.
 */
/**
 * HTML her istekte YENİDEN DOĞRULANIR. `no-cache` tek başına bunu zaten söyler; `max-age=0,
 * must-revalidate` araya giren eski/aptal vekillere aynı şeyi ikinci kez, tartışmasız söyler.
 * Değişmediyse yanıt yine gövdesiz 304'tür — maliyet bir tur, kazanç HTML ile JS'in sürümünün
 * ASLA ayrışmaması.
 */
const htmlCacheControl = "no-cache, max-age=0, must-revalidate";
function assetETag(body: string | Buffer): string {
  return `"${createHash("sha256").update(body).digest("base64url")}"`;
}

/** `If-None-Match` listesi ETag'i kapsıyor mu? Liste, `*` ve zayıf (`W/`) ön eki desteklenir. */
function etagMatches(header: string | string[] | undefined, etag: string): boolean {
  const raw = Array.isArray(header) ? header.join(",") : header;
  if (!raw) return false;
  return raw
    .split(",")
    .map((value) => value.trim())
    .some((value) => value === "*" || value.replace(/^W\//, "") === etag);
}

/** Doğrulayıcılı statik yanıt: eşleşirse gövdesiz 304, değilse gövde + ETag. */
function sendAsset(
  request: FastifyRequest,
  reply: FastifyReply,
  contentType: string,
  body: string | Buffer,
  etag: string,
  cacheControl = "no-cache"
): FastifyReply {
  reply.header("Cache-Control", cacheControl).header("ETag", etag);
  if (etagMatches(request.headers["if-none-match"], etag)) return reply.code(304).send();
  return reply.type(contentType).send(body);
}

const moduleDir = dirname(fileURLToPath(import.meta.url));
/* Bozuk bir tema dosyası EVİ DÜŞÜRMEZ: paket başına denenir, hatalı olan atlanır ve loglanır;
   geriye tek paket kalmazsa yükleyici gömülü varsayılana düşer. Varsayılan kimlik de listeden
   seçilir — tercih edilen paket atlanmışsa elde kalan ilk paket varsayılan olur, yoksa
   `appearance.json` doğrulaması var olmayan bir kimliğe takılırdı. */
const themePackages = await loadThemePackages(resolve(moduleDir, "../public/themes"), (name, error) => {
  console.warn(`Tema paketi atlandı (${name}): ${error instanceof Error ? error.message : String(error)}`);
});
const defaultThemePackageId = themePackages.some((item) => item.id === "villa-liquid-glass")
  ? "villa-liquid-glass"
  : themePackages[0].id;
const appearanceStore = new AppearanceStore(
  resolve(dirname(configPath), "appearance.json"),
  themePackages,
  defaultThemePackageId
);
const dashboard = await readFile(resolve(moduleDir, "../public/index.html"), "utf8");
const dashboardETag = assetETag(dashboard);
const dashboardBackground = await readFile(resolve(moduleDir, "../public/assets/dashboard-landscape.jpg"));
const dashboardBackgroundETag = assetETag(dashboardBackground);
/**
 * Gece gökyüzündeki ayın YÜZÜ. Kaynak: NASA Scientific Visualization Studio, "Moon Phase and
 * Libration 2019" (SVS 4442) tek karesi; veri LRO LOLA + WAC. NASA görselleri KAMU MALIDIR
 * (telif korumasız), atıf istenir — bu yorum o atıftır. 512×512 JPEG, ~49 KB.
 * Neden `panelAssetRoutes` DEĞİL: o tablo dosyaları `utf8` metin olarak okuyup metin
 * `Content-Type`'ıyla yolluyor; ikili dosya oradan geçemez. Panelde ikili varlığın kurulu
 * kalıbı zaten bu satırın hemen üstünde (`dashboard-landscape.jpg`): `Buffer` olarak oku,
 * ETag'ini çıkar, kendi rotasını yaz. Aynı kalıp tekrarlandı, yeni mekanizma icat edilmedi.
 */
const moonTexture = await readFile(resolve(moduleDir, "../public/assets/moon-texture.jpg"));
const moonTextureETag = assetETag(moonTexture);
const localesDirectory = resolve(moduleDir, "../public/locales");

// Panel parçaları da açılışta belleğe okunur: yarım kopyalanmış dosya çalışan panele yansımaz,
// geçiş noktası servis yeniden başlatması olarak kalır. Yeni bir panel dosyası çıktığında yalnız
// bu listeye satır eklenir; sıra, `index.html`'deki `<script src>` sırasıdır.
const panelAssetRoutes = [
  "/css/panel.css",
  "/js/10-core.js",
  "/js/20-auth.js",
  "/js/30-device-view.js",
  "/js/35-generator.js",
  "/js/40-home.js",
  "/js/45-clock-weather.js",
  "/js/50-widgets.js",
  "/js/60-pairing.js",
  "/js/70-settings.js",
  "/js/72-admin-overview.js",
  "/js/80-zigbee-tools.js",
  "/js/82-theme-packages.js",
  "/js/84-celestial-view.js",
  "/js/88-simple-link.js",
  "/js/90-shell.js",
  "/js/panel-automation.js",
  "/js/95-tv-navigation.js",
  "/js/99-bind.js"
];

for (const route of panelAssetRoutes) {
  const body = await readFile(resolve(moduleDir, `../public${route}`), "utf8");
  const contentType = route.endsWith(".css") ? "text/css; charset=utf-8" : "text/javascript; charset=utf-8";
  const etag = assetETag(body);
  app.get(route, async (request, reply) => sendAsset(request, reply, contentType, body, etag));
}

/**
 * SERVICE WORKER KURTARMA KAPISI.
 *
 * Panelin kendi service worker'ı YOK ve hiç olmadı; buna rağmen sunucu günlüklerinde
 * `GET /sw-modern.js -> 404` görülüyor. Bunu isteyen tek şey, bir istemcide hâlâ KAYITLI duran
 * eski bir service worker'dır (aynı köken üzerinde daha önce çalışmış bir kurulum). Kayıtlı bir
 * worker gezinme isteklerine kendi önbelleğinden yanıt verebilir: kullanıcı normal yenilerken
 * BAYAT `index.html` alır ama betikler ağdan tazedir — "eski HTML + yeni JS" tam olarak budur.
 * Sert yenileme (Shift+Reload) service worker'ı ATLADIĞI için sorun orada kaybolur; şikâyetin
 * imzası birebir uyuyor. Sunucu başlıkları bu durumda çaresizdir, yanıtı sunan tarayıcının
 * içindeki worker'dır.
 *
 * Tarayıcı kayıtlı worker'ın betiğini düzenli olarak yeniden indirir. Bugün 404 dönüyor;
 * 404 güncellemeyi BAŞARISIZ sayar ve eski worker olduğu gibi kalır — kilit budur. Bu rota
 * onun yerine geçerli ama kendini imha eden bir worker sunar: kurulur kurulmaz tüm
 * `CacheStorage` girdilerini siler, kaydını iptal eder ve açık sekmeleri bir kez tazeler.
 * Sonrasında kayıt yok olur, bu rotayı bir daha kimse istemez.
 *
 * `index.html` içine gömülecek bir `unregister()` betiği bu istemcileri KURTARAMAZDI: o betik
 * yeni HTML'de yaşar, worker ise eski HTML'i servis ediyor — hiç çalışmazdı.
 */
const serviceWorkerKillSwitch = `self.addEventListener("install",()=>self.skipWaiting());
self.addEventListener("activate",event=>{
  event.waitUntil((async()=>{
    try{
      const keys=await caches.keys();
      await Promise.all(keys.map(key=>caches.delete(key)));
    }catch{}
    try{await self.registration.unregister()}catch{}
    try{
      const windows=await self.clients.matchAll({type:"window"});
      for(const client of windows){try{await client.navigate(client.url)}catch{}}
    }catch{}
  })());
});
`;
const serviceWorkerKillSwitchETag = assetETag(serviceWorkerKillSwitch);
for (const route of ["/sw-modern.js", "/sw.js", "/service-worker.js"]) {
  app.get(route, async (request, reply) =>
    sendAsset(request, reply, "text/javascript; charset=utf-8", serviceWorkerKillSwitch, serviceWorkerKillSwitchETag));
}

app.get("/assets/dashboard-landscape.jpg", async (request, reply) =>
  sendAsset(request, reply, "image/jpeg", dashboardBackground, dashboardBackgroundETag));

app.get("/assets/moon-texture.jpg", async (request, reply) =>
  sendAsset(request, reply, "image/jpeg", moonTexture, moonTextureETag));

/**
 * Hava sahnesi görselleri (Meteocons, MIT — `public/assets/weather/README.md`). Onlarca dosya
 * için tek tek rota yazmak yerine açılışta bir beyaz liste haritası kurulur: dosya adları yalnız
 * bu haritadan gelir, istek gövdesinden değil. Haritada olmayan ad 404 döner; böylece yol dışına
 * çıkmak (path traversal) mümkün değildir.
 */
const weatherAssetDirectory = resolve(moduleDir, "../public/assets/weather");
const weatherAssets = new Map<string, { body: Buffer; etag: string }>();
for (const file of await readdir(weatherAssetDirectory)) {
  if (!/^[a-z0-9-]+\.svg$/.test(file)) continue;
  const body = await readFile(resolve(weatherAssetDirectory, file));
  weatherAssets.set(file, { body, etag: assetETag(body) });
}

app.get<{ Params: { file: string } }>("/assets/weather/:file", async (request, reply) => {
  const asset = weatherAssets.get(request.params.file);
  if (!asset) return reply.code(404).send({ ok: false, error: "Bilinmeyen hava görseli" });
  return sendAsset(request, reply, "image/svg+xml", asset.body, asset.etag);
});

app.get("/api/locales", async (request, reply) => {
  try {
    const files = (await readdir(localesDirectory))
      .filter((file) => /^[a-z]{2}(?:-[A-Z]{2})?\.json$/.test(file))
      .sort((left, right) => left.localeCompare(right, "en"));
    const locales = await Promise.all(files.map(async (file) => {
      const parsed = JSON.parse(await readFile(resolve(localesDirectory, file), "utf8")) as {
        code?: unknown;
        name?: unknown;
        translations?: unknown;
      };
      if (
        typeof parsed.code !== "string"
        || typeof parsed.name !== "string"
        || typeof parsed.translations !== "object"
        || parsed.translations === null
        || Array.isArray(parsed.translations)
        || parsed.code !== file.slice(0, -".json".length)
        || !Object.values(parsed.translations).every((value) => typeof value === "string")
      ) {
        throw new Error(`Geçersiz dil paketi: ${file}`);
      }
      return parsed;
    }));
    // Dil paketleri çalışma anında keşfedilir (yeni dil için yeniden başlatma yok), o yüzden
    // ETag açılışta değil burada, üretilen gövdeden hesaplanır.
    const body = JSON.stringify({ defaultLanguage: "en", locales });
    return sendAsset(request, reply, "application/json; charset=utf-8", body, assetETag(body));
  } catch (error) {
    return reply.code(503).send({
      ok: false,
      error: error instanceof Error ? error.message : String(error)
    });
  }
});

app.get("/api/health", async () => ({ ...store.getHealth(), node: nodeStatus() }));
app.get("/api/discovery", async () =>
  (failoverHook ? failoverHook.record() : { ...discoveryRecord, sentAt: Date.now() }));
// Ajan bu yoldan hiç geçmez: yalnız `/mcp` üzerinden konuşur. Buraya gelen tek soru "istek
// yönetici moduna yükseltilmiş mi" — ayar niteliğindeki kanallar yalnız o zaman görünür.
const visibleDevices = (elevated: boolean) => store.getDevices().map((device) => ({
  ...device,
  // Görsel varlığı cihaz modelinden türetilemez: model adı geçerli olsa da upstream'de o görsel
  // olmayabilir (ör. `TS0601_u8ouaqsz`). Bilgiyi yalnız önbellek bilir, panele burada taşınır ki
  // görseli olmayan cihaz için `<img>` hiç kurulmasın — konsolda 404 birikmesin.
  image: { ...device.image, available: imageCache.availability(device.image.model) },
  controls: elevated
    ? device.controls
    : device.controls.filter((control) => control.adminOnly !== true)
}));

/**
 * Kurulum sırasında cihaz saniyeler içinde ağdan düşebiliyor; o an gelen "Kaydet" isteği düz 404
 * dönünce kullanıcı ekranda hiçbir sebep göremiyordu. Ortak yanıt, "hiç tanımadım" ile "az önce
 * ayrıldı/kaldırıldı" arasındaki farkı makine koduyla panele taşır.
 */
const replyDeviceMissing = (reply: FastifyReply, id: string): FastifyReply =>
  reply.code(404).send(deviceMissingResponse(source.recentDeparture?.(id)));

app.get("/api/devices", async (request) => ({
  devices: visibleDevices(request.villaElevated)
}));
app.get<{ Params: { id: string } }>("/api/devices/:id/note", async (request, reply) => {
  const id = request.params.id.toLowerCase();
  if (!store.getDevice(id)) return replyDeviceMissing(reply, id);
  return { ok: true, note: await deviceNotesStore.get(id) };
});
app.put<{ Params: { id: string }; Body?: { note?: unknown } }>("/api/devices/:id/note", async (request, reply) => {
  const id = request.params.id.toLowerCase();
  if (!store.getDevice(id)) return replyDeviceMissing(reply, id);
  try {
    return { ok: true, note: await deviceNotesStore.set(id, request.body?.note) };
  } catch (error) {
    return reply.code(400).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});
// Rol yalnız Villa Bridge arayüzünü etkiler: sihirbaz kümeleri, kart etiketi ve simgesi.
// Matter, Alexa, Apple Home ve Home Assistant tarafına bilerek yansıtılmaz.
app.get<{ Params: { id: string } }>("/api/devices/:id/role", async (request, reply) => {
  const id = request.params.id.toLowerCase();
  const device = store.getDevice(id);
  if (!device) return replyDeviceMissing(reply, id);
  return {
    ok: true,
    role: device.role,
    category: device.category,
    detectedCategory: device.detectedCategory,
    // Rol kanal başınadır; cihaz seviyesi alanlar kanallardan türetilmiş özettir.
    channels: device.controls
      .filter((control) => control.kind === "switch")
      .map((control) => ({
        channel: control.id,
        name: control.name,
        role: control.role ?? "auto",
        category: control.category ?? device.detectedCategory,
        detectedCategory: control.detectedCategory ?? device.detectedCategory
      }))
  };
});
app.put<{ Params: { id: string }; Body?: { role?: unknown; channel?: unknown } }>(
  "/api/devices/:id/role",
  async (request, reply) => {
    const id = request.params.id.toLowerCase();
    const device = store.getDevice(id);
    if (!device) return replyDeviceMissing(reply, id);
    const requestedChannel = request.body?.channel;
    if (requestedChannel !== undefined && requestedChannel !== null && typeof requestedChannel !== "string") {
      return reply.code(400).send({ ok: false, error: "Kanal kimliği geçersiz." });
    }
    // Kanal kimliği cihazın gerçek aç/kapa kanallarından biri olmalı: uydurma anahtar yazılmaz.
    if (requestedChannel) {
      const known = device.controls.some(
        (control) => control.kind === "switch" && control.id === requestedChannel.toLowerCase()
      );
      if (!known) return reply.code(400).send({ ok: false, error: "Kanal bulunamadı." });
    }
    try {
      await setDeviceRole(deviceRolesPath, deviceRoles, id, request.body?.role, requestedChannel ?? null);
      const updated = store.getDevice(id);
      const channelControl = requestedChannel
        ? updated?.controls.find((control) => control.kind === "switch" && control.id === requestedChannel.toLowerCase())
        : undefined;
      return {
        ok: true,
        channel: requestedChannel ?? null,
        role: channelControl ? channelControl.role ?? "auto" : updated?.role ?? "auto",
        category: channelControl
          ? channelControl.category ?? "unknown"
          : updated?.category ?? "unknown",
        detectedCategory: updated?.detectedCategory ?? "unknown",
        deviceCategory: updated?.category ?? "unknown"
      };
    } catch (error) {
      return reply.code(400).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
    }
  }
);
app.get<{ Params: { model: string } }>("/api/device-image/:model", async (request, reply) => {
  const image = await imageCache.get(request.params.model);
  if (!image) {
    return reply.code(404).send({ ok: false, code: "DEVICE_IMAGE_NOT_FOUND" });
  }
  return reply
    .header("Cache-Control", "public, max-age=604800, immutable")
    .type(image.contentType)
    .send(image.body);
});
app.get("/api/groups", async () => ({ groups: store.getGroups() }));
app.post<{ Body?: { name?: unknown } }>("/api/groups", async (request, reply) => {
  const name = typeof request.body?.name === "string" ? request.body.name.trim() : "";
  if (name.length < 2 || name.length > 64) {
    return reply.code(400).send({ ok: false, error: "Grup adı 2-64 karakter olmalıdır." });
  }
  try {
    await source.createGroup(name);
    return { ok: true, groups: store.getGroups() };
  } catch (error) {
    return reply.code(503).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});
app.put<{ Params: { id: string }; Body?: { name?: unknown } }>("/api/groups/:id", async (request, reply) => {
  const name = typeof request.body?.name === "string" ? request.body.name.trim() : "";
  if (name.length < 2 || name.length > 64) {
    return reply.code(400).send({ ok: false, error: "Grup adı 2-64 karakter olmalıdır." });
  }
  try {
    await source.renameGroup(request.params.id, name);
    return { ok: true, groups: store.getGroups() };
  } catch (error) {
    return reply.code(503).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});
app.delete<{ Params: { id: string }; Querystring: { force?: string } }>("/api/groups/:id", async (request, reply) => {
  try {
    await source.removeGroup(request.params.id, request.query.force === "true");
    return { ok: true, groups: store.getGroups() };
  } catch (error) {
    return reply.code(503).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});
app.put<{
  Params: { id: string };
  Body?: { deviceId?: unknown; add?: unknown; endpoint?: unknown };
}>("/api/groups/:id/member", async (request, reply) => {
  const deviceId = typeof request.body?.deviceId === "string" ? request.body.deviceId.toLowerCase() : "";
  const endpoint = request.body?.endpoint;
  if (
    !/^0x[0-9a-f]{16}$/.test(deviceId) ||
    typeof request.body?.add !== "boolean" ||
    (endpoint !== undefined && (!Number.isInteger(endpoint) || Number(endpoint) < 1 || Number(endpoint) > 240))
  ) {
    return reply.code(400).send({ ok: false, error: "Grup üyeliği isteği geçersiz." });
  }
  try {
    await source.setGroupMember(request.params.id, deviceId, request.body.add, endpoint === undefined ? undefined : Number(endpoint));
    return { ok: true, groups: store.getGroups() };
  } catch (error) {
    return reply.code(503).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});
app.post<{
  Params: { id: string };
  Body?: { property?: unknown; value?: unknown };
}>("/api/groups/:id/command", async (request, reply) => {
  if (
    request.body?.property !== "state"
    || typeof request.body.value !== "boolean"
  ) {
    return reply.code(400).send({ ok: false, error: "Grup aç/kapat komutu geçersiz." });
  }
  try {
    await source.setGroup(request.params.id, {
      state: request.body.value ? "ON" : "OFF"
    });
    return { ok: true };
  } catch (error) {
    return reply.code(503).send({
      ok: false,
      error: error instanceof Error ? error.message : String(error)
    });
  }
});
app.post<{
  Body?: {
    fromId?: unknown;
    toId?: unknown;
    bind?: unknown;
    clusters?: unknown;
    fromEndpoint?: unknown;
    toEndpoint?: unknown;
  };
}>("/api/zigbee/bind", async (request, reply) => {
  const fromId = typeof request.body?.fromId === "string" ? request.body.fromId.toLowerCase() : "";
  const toId = typeof request.body?.toId === "string" ? request.body.toId.toLowerCase() : "";
  const clusters = Array.isArray(request.body?.clusters)
    ? request.body.clusters.filter((value): value is string => typeof value === "string").slice(0, 32)
    : undefined;
  const fromEndpoint = request.body?.fromEndpoint;
  const toEndpoint = request.body?.toEndpoint;
  const validEndpoint = (value: unknown): boolean =>
    value === undefined || (Number.isInteger(value) && Number(value) >= 1 && Number(value) <= 240);
  if (
    !/^0x[0-9a-f]{16}$/.test(fromId)
    || !toId
    || typeof request.body?.bind !== "boolean"
    || !validEndpoint(fromEndpoint)
    || !validEndpoint(toEndpoint)
  ) {
    return reply.code(400).send({ ok: false, error: "Bağlama isteği geçersiz." });
  }
  // Kurulamayacak bağlama sessizce kaydedilmesin. Gölge kipinde istek Zigbee2MQTT'ye gidip
  // hiçbir şey yapmadan "tamam" dönebiliyordu; kullanıcı bağlantıyı kurdum sanıp düğmenin
  // çalışmamasıyla baş başa kalıyordu. Çözme (`bind:false`) hiç engellenmez — eski bir kaydı
  // kaldırmak her zaman serbest olmalı.
  if (request.body.bind === true) {
    const fromDevice = store.getDevices().find((device) => device.id === fromId);
    if (
      fromDevice
      && !hasBindableOutputCluster(
        fromDevice,
        fromEndpoint === undefined ? undefined : Number(fromEndpoint)
      )
    ) {
      return reply.code(422).send({
        ok: false,
        code: "not_bindable",
        error: "Bu cihaz doğrudan bağlanamaz: komut taşıyan bir çıkış kümesi bildirmiyor. "
          + "Bunun yerine bir kural kurun — düğmeye basılınca köprü komutu gönderir."
      });
    }
  }
  try {
    await source.bindDevice(
      fromId,
      toId,
      request.body.bind,
      clusters,
      fromEndpoint === undefined ? undefined : Number(fromEndpoint),
      toEndpoint === undefined ? undefined : Number(toEndpoint)
    );
    return { ok: true, devices: visibleDevices(request.villaElevated) };
  } catch (error) {
    return reply.code(503).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});
app.post<{
  Params: { id: string };
  Body?: { sceneId?: unknown; action?: unknown; name?: unknown };
}>("/api/groups/:id/scene", async (request, reply) => {
  const sceneId = Number(request.body?.sceneId);
  const action = request.body?.action;
  const name = typeof request.body?.name === "string" ? request.body.name.trim() : undefined;
  if (
    !Number.isInteger(sceneId)
    || sceneId < 1
    || sceneId > 255
    || !["store", "recall", "remove"].includes(String(action))
    || (name !== undefined && (name.length < 1 || name.length > 64))
  ) {
    return reply.code(400).send({ ok: false, error: "Sahne isteği geçersiz." });
  }
  try {
    await source.groupScene(
      request.params.id,
      sceneId,
      action as "store" | "recall" | "remove",
      name
    );
    return { ok: true, groups: store.getGroups() };
  } catch (error) {
    return reply.code(503).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});
app.get("/api/pairing", async () => store.getPairing());
app.post<{ Body?: { seconds?: number; routerId?: string } }>("/api/pairing/start", async (request, reply) => {
  if (!config.pairingControl) {
    return reply.code(403).send({ ok: false, error: "Cihaz ekleme denetimi kapalı." });
  }
  const requested = request.body?.seconds ?? 180;
  const seconds = Number.isInteger(requested) ? Math.min(254, Math.max(10, requested)) : 180;
  const routerId =
    typeof request.body?.routerId === "string" && /^0x[0-9a-f]{16}$/i.test(request.body.routerId)
      ? request.body.routerId.toLowerCase()
      : undefined;
  store.pairingRequested(seconds);
  try {
    await source.permitJoin(seconds, routerId);
    return { ok: true, pairing: store.getPairing() };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    store.pairingRequestFailed(message);
    return reply.code(503).send({ ok: false, error: message });
  }
});

app.post<{ Body?: { value?: unknown } }>("/api/zigbee/install-code", async (request, reply) => {
  const value = typeof request.body?.value === "string" ? request.body.value.trim() : "";
  // Herdsman accepts several vendor QR formats containing "$", "|" and spaces.
  if (value.length < 16 || value.length > 512 || !/^[\x20-\x7e]+$/.test(value)) {
    return reply.code(400).send({ ok: false, error: "Install code veya Zigbee QR değeri geçersiz." });
  }
  try {
    await source.addInstallCode(value);
    return { ok: true };
  } catch (error) {
    return reply.code(503).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});

app.post<{ Params: { id: string } }>("/api/devices/:id/reconfigure", async (request, reply) => {
  const id = request.params.id.toLowerCase();
  if (!store.getDevice(id)) return replyDeviceMissing(reply, id);
  try {
    await source.reconfigureDevice(id);
    return { ok: true, device: store.getDevice(id) };
  } catch (error) {
    return reply.code(503).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});

app.post("/api/zigbee/touchlink/scan", async (_request, reply) => {
  try {
    return { ok: true, devices: await source.scanTouchlink() };
  } catch (error) {
    return reply.code(503).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});
app.get("/api/zigbee/network-map", async (_request, reply) => {
  try {
    return { ok: true, map: await source.networkMap() };
  } catch (error) {
    return reply.code(503).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});
app.put<{ Params: { id: string }; Body?: { enabled?: unknown } }>(
  "/api/devices/:id/ota-schedule",
  async (request, reply) => {
    const id = request.params.id.toLowerCase();
    if (!store.getDevice(id) || typeof request.body?.enabled !== "boolean") {
      return reply.code(400).send({ ok: false, error: "Yazılım güncelleme isteği geçersiz." });
    }
    try {
      await source.scheduleOta(id, request.body.enabled);
      return { ok: true, scheduled: request.body.enabled };
    } catch (error) {
      return reply.code(503).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
    }
  }
);
app.post<{ Params: { id: string } }>("/api/devices/:id/ota-check", async (request, reply) => {
  const id = request.params.id.toLowerCase();
  if (!store.getDevice(id)) {
    return replyDeviceMissing(reply, id);
  }
  try {
    return { ok: true, ota: await source.checkOta(id) };
  } catch (error) {
    return reply.code(503).send({
      ok: false,
      error: error instanceof Error ? error.message : String(error)
    });
  }
});
app.put<{
  Params: { id: string };
  Body?: { transition?: unknown; debounce?: unknown; retain?: unknown };
}>("/api/devices/:id/options", async (request, reply) => {
  const id = request.params.id.toLowerCase();
  if (!store.getDevice(id)) return replyDeviceMissing(reply, id);
  const transition = request.body?.transition === undefined ? undefined : Number(request.body.transition);
  const debounce = request.body?.debounce === undefined ? undefined : Number(request.body.debounce);
  const retain = request.body?.retain;
  if (
    (transition !== undefined && (!Number.isFinite(transition) || transition < 0 || transition > 60)) ||
    (debounce !== undefined && (!Number.isFinite(debounce) || debounce < 0 || debounce > 60)) ||
    (retain !== undefined && typeof retain !== "boolean")
  ) {
    return reply.code(400).send({ ok: false, error: "Cihaz seçenekleri geçersiz." });
  }
  try {
    const options = {
      ...(transition !== undefined ? { transition } : {}),
      ...(debounce !== undefined ? { debounce } : {}),
      ...(retain !== undefined ? { retain } : {})
    };
    await source.setDeviceOptions(id, options);
    return { ok: true, options };
  } catch (error) {
    return reply.code(503).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});

app.post<{ Body?: { ieeeAddress?: unknown; channel?: unknown; confirmation?: unknown } }>(
  "/api/zigbee/touchlink/reset",
  async (request, reply) => {
    const ieeeAddress =
      typeof request.body?.ieeeAddress === "string" ? request.body.ieeeAddress.toLowerCase() : "";
    const channel = request.body?.channel;
    if (
      request.body?.confirmation !== "RESET" ||
      !/^0x[0-9a-f]{16}$/.test(ieeeAddress) ||
      !Number.isInteger(channel) ||
      Number(channel) < 11 ||
      Number(channel) > 26
    ) {
      return reply.code(400).send({ ok: false, error: "Touchlink sıfırlama isteği geçersiz." });
    }
    try {
      await source.resetTouchlink(ieeeAddress, Number(channel));
      return { ok: true };
    } catch (error) {
      return reply.code(503).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
    }
  }
);
app.post("/api/pairing/stop", async (_request, reply) => {
  if (!config.pairingControl) {
    return reply.code(403).send({ ok: false, error: "Cihaz ekleme denetimi kapalı." });
  }
  try {
    await source.permitJoin(0);
    return { ok: true };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    store.pairingRequestFailed(message);
    return reply.code(503).send({ ok: false, error: message });
  }
});
app.get("/api/overview", async (request) => ({
  health: { ...store.getHealth(), node: nodeStatus() },
  devices: visibleDevices(request.villaElevated),
  groups: store.getGroups(),
  pairing: store.getPairing(),
  events: store.getEvents(20),
  // Panel kurulum akışında cihazın neden kaybolduğunu buradan öğrenir; shadow modda boş kalır.
  departures: source.recentDepartures?.() ?? []
}));

app.get("/api/onboarding", async (_request, reply) => {
  try {
    return { ok: true, installation: await installationStateStore.get() };
  } catch (error) {
    return reply.code(503).send({
      ok: false,
      error: error instanceof Error ? error.message : String(error)
    });
  }
});

app.put<{ Body?: { completed?: unknown } }>("/api/onboarding", async (request, reply) => {
  if (request.body?.completed !== true) {
    return reply.code(400).send({ ok: false, error: "Onboarding tamamlanma durumu geçersiz." });
  }
  try {
    return {
      ok: true,
      installation: await installationStateStore.completeOnboarding()
    };
  } catch (error) {
    return reply.code(503).send({
      ok: false,
      error: error instanceof Error ? error.message : String(error)
    });
  }
});

app.get("/api/favorites", async (_request, reply) => {
  try {
    return { ok: true, favorites: await favoritesStore.get() };
  } catch (error) {
    return reply.code(503).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});

app.put<{ Body?: { favorites?: unknown } }>("/api/favorites", async (request, reply) => {
  try {
    const favorites = validateHomeFavorites(request.body?.favorites);
    const devices = store.getDevices();
    for (const favorite of favorites) {
      const device = devices.find((item) => item.id === favorite.deviceId);
      const control = device?.controls.find((item) =>
        item.id === favorite.controlId && isHomeFavoriteControlKind(item.kind)
      );
      if (!device || !control) {
        return reply.code(400).send({ ok: false, error: "Favori cihaz veya kontrol bulunamadı." });
      }
    }
    return { ok: true, favorites: await favoritesStore.save(favorites) };
  } catch (error) {
    return reply.code(400).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});

app.get("/api/home-groups", async (_request, reply) => {
  try {
    return { ok: true, groups: await homeGroupsStore.get() };
  } catch (error) {
    return reply.code(503).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});

app.put<{ Body?: { groups?: unknown } }>("/api/home-groups", async (request, reply) => {
  try {
    const groups = validateHomeGroups(request.body?.groups);
    const devices = store.getDevices();
    for (const group of groups) {
      for (const groupItem of group.items) {
        const device = devices.find((item) => item.id === groupItem.deviceId);
        if (!device) {
          return reply.code(400).send({ ok: false, error: "Grup cihazı veya kontrolü bulunamadı." });
        }
        if (groupItem.controlId === homeGroupDeviceControlId) continue;
        const control = device.controls.find((item) =>
          item.id === groupItem.controlId && isHomeFavoriteControlKind(item.kind)
        );
        if (!control) {
          return reply.code(400).send({ ok: false, error: "Grup cihazı veya kontrolü bulunamadı." });
        }
      }
    }
    const saved = await homeGroupsStore.save(groups);
    // Oda silmek bu uç noktadan geçer (liste komple yazılır): kalmayan odanın gizleme kaydı düşer.
    await homeVisibilityStore.pruneGroups(saved.map((group) => group.id)).catch((error) => {
      console.error(`Görünürlük kaydından silinen oda düşürülemedi: ${String(error)}`);
    });
    return { ok: true, groups: saved };
  } catch (error) {
    return reply.code(400).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});

/**
 * Görünürlük tercihleri — cihaz/oda gösterme kararı ev genelinde tektir, tarayıcıya bağlı
 * değildir. Yerleşim tercihleri (döşeme genişliği, kart sırası, seçili sekme) burada durmaz.
 */
app.get("/api/home-visibility", async (_request, reply) => {
  try {
    return { ok: true, visibility: await homeVisibilityStore.get() };
  } catch (error) {
    return reply.code(503).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});

app.put<{ Body?: { visibility?: unknown } }>("/api/home-visibility", async (request, reply) => {
  let visibility;
  try {
    // Kayıt canlı cihaz listesine karşı doğrulanmaz: açılışta liste henüz boşken gelen bir
    // yazma tüm tercihleri reddederdi. Ölü kayıtları cihaz silme ve oda kaydı temizler.
    visibility = validateHomeVisibility(request.body?.visibility);
  } catch (error) {
    return reply.code(400).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
  try {
    return { ok: true, visibility: await homeVisibilityStore.save(visibility) };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    recentErrors.record({ operation: "home-visibility", statusCode: 503, message });
    return reply.code(503).send({ ok: false, error: `Görünürlük tercihleri kaydedilemedi: ${message}` });
  }
});

app.get("/api/automations", async (_request, reply) => {
  try {
    const automations = await automationsStore.get();
    return {
      ok: true,
      // Kuralın neden pasif olduğu yanıtta görünür: konum yok / kutup günü (UI sonraki turda okur).
      automations: automations.map((automation) => ({
        ...automation,
        inactiveReason: automationEngine.inactiveReason(automation)
      })),
      sun: automationEngine.sunSummary(),
      location: homeLocation,
      // Ajan yedeği varsa panel "ajan değişikliklerini geri al" yolunu gösterir.
      agentBackups: (await automationBackups.list()).length
    };
  } catch (error) {
    return reply.code(503).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});

/**
 * Ajan değişikliklerini geri al. Geri alma **kullanıcının** işidir, modelin değil: bu yüzden bir
 * MCP aracı olarak değil, yönetici rotası olarak durur (yetki tablolarında listelenmediği için
 * yönetici ister).
 *
 * En yeni yedek okunur ve **tüketilir**: düğmeye ikinci kez basmak ileri/geri salınmaz, bir adım
 * daha geriye gider.
 */
app.post("/api/automations/agent-revert", async (_request, reply) => {
  try {
    const backup = await automationBackups.takeLatest();
    if (!backup) {
      return reply.code(404).send({ ok: false, error: "Geri alınacak ajan yedeği yok." });
    }
    const automations = await automationsStore.save(backup.automations);
    return {
      ok: true,
      restoredFrom: backup.at,
      automations,
      agentBackups: (await automationBackups.list()).length
    };
  } catch (error) {
    return reply.code(400).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});

/** Çalışma günlüğü — "neden çalıştı / neden çalışmadı"; en yeniden eskiye. */
app.get<{ Querystring: { limit?: string } }>("/api/automation-runs", async (request, reply) => {
  try {
    const limit = Number(request.query.limit ?? 100);
    return {
      ok: true,
      runs: await automationRunLog.read({ limit: Number.isFinite(limit) ? limit : 100 })
    };
  } catch (error) {
    return reply.code(503).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});

app.get<{ Params: { id: string }; Querystring: { limit?: string } }>(
  "/api/automations/:id/runs",
  async (request, reply) => {
    try {
      const limit = Number(request.query.limit ?? 50);
      return {
        ok: true,
        runs: await automationRunLog.read({
          limit: Number.isFinite(limit) ? limit : 50,
          automationId: request.params.id
        })
      };
    } catch (error) {
      return reply.code(503).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
    }
  }
);

/** Evin konumu — okuma ev sakinine açık, yazma yönetici işi (yetki tablosunda bilinçli). */
app.get("/api/settings/location", async () => ({
  ok: true,
  location: homeLocation,
  source: homeLocationSource,
  sun: automationEngine.sunSummary(),
  timeZoneVerificationRequired: locationStore.needsTimeZoneVerification
}));

app.put<{ Body?: { location?: unknown; latitude?: unknown; longitude?: unknown; timeZone?: unknown; label?: unknown } }>(
  "/api/settings/location",
  async (request, reply) => {
    try {
      const body = request.body ?? {};
      const raw = body.location ?? {
        latitude: body.latitude,
        longitude: body.longitude,
        timeZone: body.timeZone,
        label: body.label
      };
      const location = await locationStore.save(raw);
      homeLocation = location;
      homeLocationSource = "file";
      celestialService.invalidate();
      return {
        ok: true,
        location,
        source: homeLocationSource,
        sun: automationEngine.sunSummary(),
        timeZoneVerificationRequired: false
      };
    } catch (error) {
      return reply.code(400).send({
        ok: false,
        error: error instanceof Error ? error.message : String(error)
      });
    }
  }
);

app.get<{ Querystring: { at?: string } }>("/api/celestial", async (request, reply) => {
  let at: Date | undefined;
  if (request.query.at !== undefined) {
    at = new Date(request.query.at);
    const distance = Math.abs(at.valueOf() - Date.now());
    if (Number.isNaN(at.valueOf()) || distance > 2 * 366 * 24 * 60 * 60 * 1000) {
      return reply.code(400).send({ ok: false, error: "Önizleme zamanı bugünden en fazla iki yıl uzakta olabilir." });
    }
  }
  return { ok: true, celestial: celestialService.snapshot(at) };
});

app.get("/api/theme-packages", async () => ({ ok: true, packages: themePackages }));

app.get("/api/appearance", async (_request, reply) => {
  try {
    return { ok: true, appearance: await appearanceStore.get() };
  } catch (error) {
    return reply.code(503).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});

app.put<{ Body?: unknown }>("/api/appearance", async (request, reply) => {
  try {
    return { ok: true, appearance: await appearanceStore.save(request.body) };
  } catch (error) {
    return reply.code(400).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});

/**
 * Hava durumu — veri ve seçili şehir sunucudan. Okuma ev sakinine açık (yetki tablosunda),
 * konumu değiştirmek yönetici işi: tablette yapılan seçim evdeki bütün ekranları değiştirir.
 * İnternet yoksa son bilinen veri `error` notuyla birlikte döner, istek yine 200'dür.
 */
app.get("/api/weather", async () => ({ ok: true, ...weatherService.snapshot() }));

app.put<{ Body?: { location?: unknown } }>("/api/weather/location", async (request, reply) => {
  try {
    const body = request.body ?? {};
    return { ok: true, ...await weatherService.setLocation(body.location ?? body) };
  } catch (error) {
    return reply.code(400).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});

/**
 * Şehir araması sunucudan geçer; panel dış servise doğrudan çıkmaz. Hava penceresinin yanında
 * dünya saati ve ev konumu pencereleri de bunu kullandığı için okuma ev sakinine açıktır.
 */
app.get<{ Querystring: { q?: string; language?: string } }>(
  "/api/locations/search",
  async (request, reply) => {
    try {
      return { ok: true, results: await weatherService.search(request.query.q, request.query.language) };
    } catch (error) {
      return reply.code(503).send({
        ok: false,
        error: error instanceof Error ? error.message : String(error)
      });
    }
  }
);

/**
 * Dünya saati şehirleri — hava konumuyla aynı mantık: okuma ev sakinine açık (yetki tablosunda),
 * yazma yönetici işi. `zones: null` "liste hiç tanımlanmadı" demektir; panel o durumda kendi
 * varsayılanlarını gösterir ve cihazda kalmış eski listeyi bir kez yukarı taşır.
 */
app.get("/api/world-clock", async (_request, reply) => {
  try {
    return { ok: true, zones: await worldClockStore.get() };
  } catch (error) {
    return reply.code(503).send({
      ok: false,
      error: error instanceof Error ? error.message : String(error)
    });
  }
});

app.put<{ Body?: { zones?: unknown } }>("/api/world-clock", async (request, reply) => {
  try {
    return { ok: true, zones: await worldClockStore.save(request.body?.zones) };
  } catch (error) {
    return reply.code(400).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});

app.put<{ Body?: { automations?: unknown } }>("/api/automations", async (request, reply) => {
  try {
    return { ok: true, automations: await automationsStore.save(request.body?.automations) };
  } catch (error) {
    return reply.code(400).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});

app.post<{ Params: { id: string } }>("/api/automations/:id/run", async (request, reply) => {
  const result = await automationEngine.run(request.params.id);
  if (result === "missing") return reply.code(404).send({ ok: false, error: "Otomasyon bulunamadı." });
  if (result === "busy") {
    return reply.code(503).send({ ok: false, error: "Otomasyon şu anda çalışıyor. Birazdan yeniden deneyin." });
  }
  if (result === "failed") {
    return reply.code(503).send({ ok: false, error: "Otomasyon çalıştırılamadı." });
  }
  // Elle çalıştırmada tetikleyen olay yoktur; `when` taşıyan eylemler atlanır — hata değildir.
  if (result === "skipped") return { ok: true, skipped: true };
  // Koşullar sağlanmadı: hata değil, bilinçli atlama. Sebep çalışma günlüğünde.
  if (result === "blocked") {
    return { ok: true, blocked: true, error: "Kuralın koşulları şu anda sağlanmıyor." };
  }
  return { ok: true };
});

app.get("/api/backup", async (_request, reply) => {
  try {
    return { ok: true, backup: await homeBackupService.create() };
  } catch (error) {
    return reply.code(503).send({
      ok: false,
      error: error instanceof Error ? error.message : String(error)
    });
  }
});

app.post<{ Body?: { backup?: unknown; mode?: unknown } }>(
  "/api/backup/preview",
  async (request, reply) => {
    const mode = request.body?.mode === "merge" ? "merge" : "replace";
    try {
      return { ok: true, summary: await homeBackupService.preview(request.body?.backup, mode) };
    } catch (error) {
      return reply.code(400).send({
        ok: false,
        error: error instanceof Error ? error.message : String(error)
      });
    }
  }
);

app.post<{ Body?: { backup?: unknown; mode?: unknown } }>(
  "/api/backup/restore",
  async (request, reply) => {
    if (request.body?.mode !== "merge" && request.body?.mode !== "replace") {
      return reply.code(400).send({ ok: false, error: "Geri yükleme biçimi geçersiz." });
    }
    const mode: HomeBackupMode = request.body.mode;
    try {
      const summary = await homeBackupService.restore(request.body?.backup, mode);
      imagePreferences = await imagesStore.get();
      store.setImagePreferences(imagePreferences);
      return { ok: true, summary };
    } catch (error) {
      return reply.code(400).send({
        ok: false,
        error: error instanceof Error ? error.message : String(error)
      });
    }
  }
);

/**
 * BİRLEŞİK YEDEK — panelin tek "Yedek al" düğmesi buraya gelir.
 *
 * Eski uçlar (`GET /api/backup`, `GET /api/zigbee/backup`) yerinde bırakıldı: betikler ve eski
 * akışlar onları kullanıyor olabilir. Panelde görünen tek düğme artık bu uçtur.
 *
 * İndirme düz bir gezinmedir (`window.location`), blob değil: oturum çerezle taşındığı için
 * masaüstünde de Android WebView'de de tarayıcının normal indirme yolu çalışır.
 */
const collectZigbeeBackupSection = async (): Promise<{
  zigbee?: unknown;
  zigbeeArchive?: Buffer;
  zigbeeNote?: string;
}> => {
  // Kurulum kipi: ortada sahiplenilmiş bir koordinatör yok. Yedek yine de alınır, ama Zigbee
  // bölümü boş kalır — veri dizinini yoklayıp "koordinatör yedeği eksik" demek yanıltıcı olurdu.
  if (config.setupPending) {
    return { zigbeeNote: "Kurulum tamamlanmadı: yedeklenecek bir Zigbee ağı henüz yok." };
  }
  try {
    const prepared = await source.prepareNetworkBackup();
    // Shadow kipi: Zigbee2MQTT kendi arşivini verir, zarfımıza sığmaz — olduğu gibi taşınır.
    if (prepared) return { zigbeeArchive: prepared.body };
    if (!config.zigbee) return { zigbeeNote: "Yerel Zigbee veri dizini bulunamadı." };
    return { zigbee: await createZigbeeNetworkBackup(config.zigbee.dataDir) };
  } catch (error) {
    // Koordinatör hazır değilse bütün yedeği kaybetmeyiz; ev bölümü yine de iner.
    return { zigbeeNote: error instanceof Error ? error.message : String(error) };
  }
};

app.get("/api/backup/full", async (_request, reply) => {
  try {
    const now = new Date();
    const home = await homeBackupService.create(now);
    const archive = buildFullBackup({ home, ...(await collectZigbeeBackupSection()) }, now);
    const stamp = now.toISOString().slice(0, 10);
    return reply
      .header("Content-Disposition", `attachment; filename="villa-yedek-${stamp}.zip"`)
      .header("Cache-Control", "no-store")
      .type("application/zip")
      .send(archive);
  } catch (error) {
    return reply.code(503).send({
      ok: false,
      error: error instanceof Error ? error.message : String(error)
    });
  }
});

const decodeBackupUpload = (value: unknown): ParsedBackupUpload => {
  if (typeof value !== "string" || value.length === 0) throw new Error("Yedek dosyası alınamadı.");
  if (!/^[A-Za-z0-9+/=\s]+$/.test(value)) throw new Error("Yedek dosyası kodlaması geçersiz.");
  const data = Buffer.from(value, "base64");
  if (data.length === 0 || data.length > maximumFullBackupBytes) {
    throw new Error("Yedek dosyası boyutu geçersiz.");
  }
  return parseBackupUpload(data);
};

interface FullRestoreBody {
  file?: unknown;
  mode?: unknown;
  confirmation?: unknown;
}

/** Yüklenen dosyanın içine bakar: hangi bölümler var, ev bölümü uygulanınca ne olur. */
app.post<{ Body?: FullRestoreBody }>("/api/backup/full/preview", async (request, reply) => {
  const mode: HomeBackupMode = request.body?.mode === "replace" ? "replace" : "merge";
  try {
    const parsed = decodeBackupUpload(request.body?.file);
    const home = parsed.home === undefined
      ? null
      : await homeBackupService.preview(parsed.home, mode);
    let zigbeeFiles = 0;
    if (parsed.zigbee !== undefined) {
      zigbeeFiles = validateZigbeeNetworkBackup(parsed.zigbee).decoded.size;
    }
    return {
      ok: true,
      kind: parsed.kind,
      createdAt: parsed.createdAt,
      summary: home,
      zigbee: parsed.zigbee !== undefined ? { files: zigbeeFiles } : null,
      zigbeeArchiveOnly: parsed.zigbeeArchiveOnly
    };
  } catch (error) {
    return reply.code(400).send({
      ok: false,
      error: error instanceof Error ? error.message : String(error)
    });
  }
});

/**
 * TEK YENİDEN BAŞLATMA: önce ev bölümü atomik olarak yazılır, sonra Zigbee bölümü diske
 * sıraya alınır, en sonda servis BİR KEZ iner. Eski `/api/zigbee/restore` dosyayı yerleştirip
 * hemen SIGTERM atıyordu; birleşik akışta yarım geri yükleme + erken restart olmaz.
 */
app.post<{ Body?: FullRestoreBody }>("/api/backup/full/restore", async (request, reply) => {
  if (request.body?.confirmation !== "RESTORE") {
    return reply.code(400).send({ ok: false, error: "Geri yükleme onayı geçersiz." });
  }
  if (request.body?.mode !== "merge" && request.body?.mode !== "replace") {
    return reply.code(400).send({ ok: false, error: "Geri yükleme biçimi geçersiz." });
  }
  const mode: HomeBackupMode = request.body.mode;
  try {
    const parsed = decodeBackupUpload(request.body.file);
    if (parsed.zigbee !== undefined && !config.zigbee) {
      throw new Error("Yerel Zigbee ağı bu çalışma modunda kullanılmıyor.");
    }
    // Zigbee bölümü diske dokunmadan ÖNCE doğrulanır: bozuksa hiçbir şey değişmez.
    if (parsed.zigbee !== undefined) validateZigbeeNetworkBackup(parsed.zigbee);

    let summary = null;
    if (parsed.home !== undefined) {
      summary = await homeBackupService.restore(parsed.home, mode);
      imagePreferences = await imagesStore.get();
      store.setImagePreferences(imagePreferences);
    }
    let restarting = false;
    if (parsed.zigbee !== undefined && config.zigbee) {
      await stageZigbeeNetworkRestore(config.zigbee.dataDir, parsed.zigbee);
      restarting = true;
      const timer = setTimeout(() => process.kill(process.pid, "SIGTERM"), 750);
      timer.unref();
    }
    return { ok: true, summary, restarting, zigbeeArchiveOnly: parsed.zigbeeArchiveOnly };
  } catch (error) {
    return reply.code(400).send({
      ok: false,
      error: error instanceof Error ? error.message : String(error)
    });
  }
});

app.put<{
  Params: { id: string };
  Body?: { imageModel?: unknown; applyToModel?: unknown };
}>("/api/devices/:id/image", async (request, reply) => {
  const id = request.params.id.toLowerCase();
  const device = store.getDevice(id);
  if (!device) return replyDeviceMissing(reply, id);
  const imageModel = request.body?.imageModel;
  if (imageModel !== null && typeof imageModel !== "string") {
    return reply.code(400).send({ ok: false, error: "Görsel seçimi geçersiz." });
  }
  if (
    typeof imageModel === "string"
    && !device.image.candidates.some((candidate) => candidate.model === imageModel)
  ) {
    return reply.code(400).send({ ok: false, error: "Bu görsel cihaz için sunulan adaylar arasında değil." });
  }
  try {
    imagePreferences = await imagesStore.select(
      imagePreferences,
      id,
      device.image.preferenceKey,
      imageModel,
      request.body?.applyToModel === true
    );
    store.setImagePreferences(imagePreferences);
    return { ok: true, device: store.getDevice(id) };
  } catch (error) {
    return reply.code(503).send({
      ok: false,
      error: error instanceof Error ? error.message : String(error)
    });
  }
});

app.post<{
  Params: { id: string };
  Body?: { property?: string; value?: unknown };
}>("/api/devices/:id/command", async (request, reply) => {
  const id = request.params.id.toLowerCase();
  const device = store.getDevice(id);
  if (!device) return replyDeviceMissing(reply, id);
  if (device.preparing) {
    return reply.code(409).send({
      ok: false,
      code: "DEVICE_PREPARING",
      error: "Cihaz kurulumu tamamlanıyor. Lütfen hazır olmasını bekleyin."
    });
  }
  const control = device.controls.find((item) => item.property === request.body?.property);
  if (!control) return reply.code(400).send({ ok: false, error: "Bu denetim cihaz tarafından sunulmuyor." });
  if (control.adminOnly && !request.villaElevated) {
    return reply.code(403).send({ ok: false, error: "Bu cihaz ayarı için yönetici modu gerekiyor." });
  }
  // Değer dönüşümü/doğrulaması panelle otomasyonun paylaştığı tek fonksiyondadır; burada yalnız
  // hatanın HTTP karşılığı verilir. Panel aç/kapat değerini `boolean` yollar, otomasyon yollamaz.
  let value: unknown;
  try {
    value = normalizeControlValue(control, request.body?.value, { booleanSwitch: true });
  } catch (error) {
    return reply.code(400).send({
      ok: false,
      error: error instanceof Error ? error.message : String(error)
    });
  }
  try {
    await source.setDevice(id, { [control.property]: value } satisfies JsonObject);
    return { ok: true };
  } catch (error) {
    return reply.code(503).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});

app.put<{
  Params: { id: string };
  Body?: { name?: string; channel?: string };
}>("/api/devices/:id/name", async (request, reply) => {
  const id = request.params.id.toLowerCase();
  const device = store.getDevice(id);
  if (!device) return replyDeviceMissing(reply, id);
  const name = request.body?.name?.trim() ?? "";
  if (name.length < 2 || Buffer.byteLength(name, "utf8") > 32) {
    return reply.code(400).send({ ok: false, error: "İsim 2–32 bayt arasında olmalı." });
  }
  const channel = request.body?.channel?.trim().toLowerCase();
  // Kanal anahtarı hem kumanda kanallarını hem türetilmiş düğme alt varlıklarını (`button:1`) kapsar.
  const knownChannel = channel !== undefined && (
    device.controls.some((control) => control.id === channel || control.id.startsWith(`${channel}:`))
    || (device.buttons ?? []).some((button) => button.id === channel)
  );
  if (channel && !knownChannel) {
    return reply.code(400).send({ ok: false, error: "Kanal bulunamadı." });
  }
  const aliasKey = channel ? `${id}:${channel}` : id;
  // Tek aç/kapa kanallı cihazda kanalın ayrı bir adı yoktur; panelde kanal kalemi de çıkmaz. Bu yüzden
  // cihaz adı değişince tek kanalın adı onunla birlikte gider, yoksa dışarıya (Matter/Alexa/Home
  // Assistant) eski kanal adı sızmaya devam eder ve düzeltmenin yolu kalmaz. "main" kanalında takma ad
  // silinir — varsayılanı zaten cihaz adıdır; başka kanalda yeni ad yazılır, yoksa "Kanal 1"e düşerdi.
  const soleChannelId = channel ? null : soleSwitchChannelId(device.controls);
  const soleChannelKey = soleChannelId ? `${id}:${soleChannelId}` : null;
  const previousAliases = new Map(
    (soleChannelKey ? [aliasKey, soleChannelKey] : [aliasKey]).map((key) => [key, aliases.get(key)])
  );
  aliases.set(aliasKey, name);
  if (soleChannelKey) {
    if (soleChannelId === "main") aliases.delete(soleChannelKey);
    else aliases.set(soleChannelKey, name);
  }
  try {
    await saveAliases(config.aliasesFile, aliases);
    if (!channel) await source.renameDevice(id, name);
    return { ok: true, id, channel: channel ?? null, name, matterSyncRequired: true };
  } catch (error) {
    for (const [key, value] of previousAliases) {
      if (value === undefined) aliases.delete(key);
      else aliases.set(key, value);
    }
    await saveAliases(config.aliasesFile, aliases).catch(() => undefined);
    return reply.code(503).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});

app.delete<{
  Params: { id: string };
  Body?: { confirmation?: string; force?: boolean };
}>("/api/devices/:id", async (request, reply) => {
  const id = request.params.id.toLowerCase();
  const device = store.getDevice(id);
  if (!device) return replyDeviceMissing(reply, id);
  if (!isDeviceRemovalConfirmation(request.body?.confirmation)) {
    return reply.code(400).send({ ok: false, error: "Silmek için küçük harflerle yes veya evet yazın." });
  }
  try {
    const force = request.body?.force === true;
    await source.removeDevice(id, force);
    for (const key of [...aliases.keys()]) {
      if (key === id || key.startsWith(`${id}:`)) aliases.delete(key);
    }
    await saveAliases(config.aliasesFile, aliases);
    imagePreferences = await imagesStore.removeDevice(imagePreferences, id).catch(() => imagePreferences);
    store.setImagePreferences(imagePreferences);
    const favorites = await favoritesStore.removeDevice(id);
    const groups = await homeGroupsStore.removeDevice(id);
    const visibility = await homeVisibilityStore.removeDevice(id).catch((error) => {
      console.error(`Görünürlük kaydından cihaz düşürülemedi: ${String(error)}`);
      return null;
    });
    await automationsStore.removeDevice(id).catch((error) => {
      console.error(`Otomasyonlardan cihaz düşürülemedi: ${String(error)}`);
    });
    await deviceNotesStore.removeDevice(id);
    await removeDeviceRole(deviceRolesPath, deviceRoles, id).catch((error) => {
      console.error(`Cihaz rolü silinemedi: ${String(error)}`);
    });
    return { ok: true, force, favorites, groups, visibility };
  } catch (error) {
    return reply.code(503).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});

app.get("/api/matter", async (_request, reply) => {
  try {
    return await matterbridge.getStatus();
  } catch (error) {
    return reply.code(503).send({ online: false, error: error instanceof Error ? error.message : String(error) });
  }
});

app.get("/api/settings", async (_request, reply) => {
  if (!settingsStore) return reply.code(503).send({ ok: false, error: "Bağlantı ayarları bu çalışma modunda kullanılamıyor." });
  try {
    const authenticationRequired =
      typeof config.mqtt.username === "string"
      && config.mqtt.username.length > 0
      && typeof config.mqtt.password === "string";
    return {
      ok: true,
      settings: await settingsStore.get(),
      /**
       * Kurulum bitmedi mi? Panel bunu görünce sihirbazı zorunlu kılar: yerel "tamamlandı"
       * işareti taşıyan bir tarayıcı bile yarım kurulumu atlayamasın.
       */
      setupPending: config.setupPending,
      // Konak yeteneği: seri koordinatör yolu yalnız sunucu/Pi kurulumunda girilebilir.
      // `coordinatorRestart` de bir yetenektir: panel "telsiz çipini resetle" düğmesini
      // istemcide tahmin etmez, adresin ağ köprüsü mü USB çubuk mu olduğuna sunucu karar verir.
      zigbee: {
        serialSupported: settingsStore.serialSupported,
        defaultPort: defaultCoordinatorPort,
        coordinatorRestart: coordinatorRestartSummary()
      },
      network: getNetworkInfo(),
      // Doğrudan modda kütüphaneye eklenen harici cihaz tanımları; hangi dosyanın yüklendiği
      // (ve hangisinin patladığı) tek bakışta görülsün diye ayarlarla birlikte dönülür.
      externalConverters: lastExternalConverterLoad(),
      mqttAccess: {
        protocol: "3.1.1",
        authenticationRequired,
        username: authenticationRequired ? config.mqtt.username : null,
        password: authenticationRequired ? config.mqtt.password : null
      }
    };
  } catch (error) {
    return reply.code(503).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});

app.put<{ Body?: unknown }>("/api/settings", async (request, reply) => {
  if (!settingsStore) return reply.code(503).send({ ok: false, error: "Bağlantı ayarları bu çalışma modunda kullanılamıyor." });
  try {
    const confirmation =
      typeof request.body === "object"
      && request.body !== null
      && !Array.isArray(request.body)
      && "zigbeeChannelConfirmation" in request.body
        ? (request.body as { zigbeeChannelConfirmation?: unknown }).zigbeeChannelConfirmation
        : undefined;
    const adapterConfirmation =
      typeof request.body === "object"
      && request.body !== null
      && !Array.isArray(request.body)
      && "zigbeeAdapterConfirmation" in request.body
        ? (request.body as { zigbeeAdapterConfirmation?: unknown }).zigbeeAdapterConfirmation
        : undefined;
    const settings = await settingsStore.save(request.body, {
      confirmZigbeeChannelChange: confirmation === "CHANGE",
      confirmZigbeeAdapterChange: adapterConfirmation === "CHANGE"
    });
    store.setLowBatteryThreshold(settings.alerts.lowBatteryThreshold);
    recentErrors.setEnabled(settings.debug.enabled);
    // Otomatik onarım yeniden başlatma beklemeden açılıp kapanır; kuyruk anında boşalır.
    config.selfHealing.enabled = settings.selfHealing.enabled;
    config.selfHealing.probeOffline = settings.selfHealing.probeOffline;
    source.setSelfHealingEnabled?.(settings.selfHealing.enabled);
    source.setSelfHealProbeEnabled?.(settings.selfHealing.probeOffline);
    return { ok: true, settings, restartRequired: true };
  } catch (error) {
    return reply.code(400).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});

/**
 * Koordinatör adresi yoklaması — "adrese ulaşılıyor mu?" sorusunun tek yanıtı.
 *
 * Koordinatöre **bayt yazılmaz**: TCP'de bağlan-kapat, seri yolda yalnız dosya sistemi bakışı
 * (gerekçeler `coordinator-probe.ts` başında). Şu an bağlı olduğumuz adres sorulursa hiç soket
 * açılmaz, çalışan oturumun durumu dönülür — ikinci bir bağlantı açmanın anlamı yok.
 *
 * GET değil POST: adres sorgu dizesine, erişim günlüklerine ve tarayıcı geçmişine düşmesin.
 * Yetki: `access-control.ts` tablolarının hiçbirinde yok → yönetici gerekir. Hız sınırı da
 * oradaki gerekçenin devamı: aksi hâlde bu uç, yönetici oturumu üzerinden bir port tarayıcısına
 * dönerdi.
 */
let adapterProbeBusy = false;
let adapterProbeAt = 0;
const adapterProbeIntervalMs = 3000;
app.post<{ Body?: { address?: unknown } }>("/api/settings/zigbee-adapter/test", async (request, reply) => {
  if (!settingsStore) {
    return reply.code(503).send({ ok: false, error: "Bağlantı ayarları bu çalışma modunda kullanılamıyor." });
  }
  let address;
  try {
    address = parseCoordinatorAddress(request.body?.address);
  } catch (error) {
    return reply.code(400).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
  if (address.kind === "serial" && !zigbeeSerialSupported) {
    return reply.code(400).send({
      ok: false,
      error: "Bu cihazda seri koordinatör yolu kullanılamaz; `tcp://sunucu:port` girin."
    });
  }
  // Durum A: sorulan adres şu an sahip olduğumuz adres. Hiç soket açılmaz, hız sınırına da
  // takılmaz — dışarıya tek paket çıkmıyor, yalnız çalışan oturumun durumu okunuyor.
  let activeAddress: string | null = null;
  try {
    activeAddress = parseCoordinatorAddress(config.zigbee?.serial.path).value;
  } catch {
    activeAddress = null;
  }
  if (config.mode === "direct" && !config.setupPending && activeAddress === address.value) {
    return {
      ok: true,
      result: {
        address: address.value,
        kind: address.kind,
        status: coordinatorStatus === "ready" ? "active" : "active-degraded",
        code: coordinatorError,
        live: true
      }
    };
  }
  const now = Date.now();
  if (adapterProbeBusy || now - adapterProbeAt < adapterProbeIntervalMs) {
    return reply.code(429).header("Retry-After", "3").send({
      ok: false,
      error: "Bağlantı testi çok sık istendi; birkaç saniye sonra yeniden deneyin."
    });
  }
  adapterProbeBusy = true;
  adapterProbeAt = now;
  try {
    const result = await probeCoordinator(address);
    return { ok: true, result: { ...result, live: false } };
  } finally {
    adapterProbeBusy = false;
    adapterProbeAt = Date.now();
  }
});

app.put<{ Body?: { enabled?: unknown } }>("/api/home-assistant/discovery", async (request, reply) => {
  if (!settingsStore) {
    return reply.code(503).send({ ok: false, error: "Home Assistant keşif ayarı bu çalışma modunda kullanılamıyor." });
  }
  if (typeof request.body?.enabled !== "boolean") {
    return reply.code(400).send({ ok: false, error: "Home Assistant keşif durumu geçersiz." });
  }
  try {
    const current = await settingsStore.get();
    const settings = await settingsStore.save({
      ...current,
      homeAssistant: { discoveryEnabled: request.body.enabled }
    });
    await source.setHomeAssistantDiscovery(request.body.enabled);
    config.homeAssistant.discoveryEnabled = request.body.enabled;
    return { ok: true, settings, restartRequired: false };
  } catch (error) {
    return reply.code(503).send({
      ok: false,
      error: error instanceof Error ? error.message : String(error)
    });
  }
});

app.post<{ Body?: { confirmation?: string } }>("/api/settings/apply", async (request, reply) => {
  if (request.body?.confirmation !== "APPLY") {
    return reply.code(400).send({ ok: false, error: "Yeniden başlatma onayı geçersiz." });
  }
  const timer = setTimeout(() => process.kill(process.pid, "SIGTERM"), 750);
  timer.unref();
  return { ok: true, restarting: true };
});

/**
 * SİSTEM UÇLARI — panelden servisi ve koordinatörün telsiz çipini yeniden başlatma.
 *
 * Yetki: iki yol da `access-control.ts` tablolarının HİÇBİRİNDE yok, dolayısıyla yönetici ister.
 * Bilerek böyle: ikisi de evin kumandasını saniyeler boyunca kesiyor, ev sakini düğmesi değil.
 */

/** Servis inip kalkana kadar geçen tahmini süre; panel bunun üstünden geri sayar. */
const serviceRestartEstimateSeconds = 25;
/** Telsiz çipi resetlenip ağ toparlanana kadar geçen tahmini süre. */
const coordinatorRestartEstimateSeconds = 30;

type CoordinatorRestartReason = "ok" | "not-configured" | "serial" | "invalid-address";

interface CoordinatorRestartCapability {
  supported: boolean;
  reason: CoordinatorRestartReason;
  /** Yalnız `tcp://` adreste dolu olur; panele gösterilmez, isteği kurmak için kullanılır. */
  host: string | null;
}

/**
 * KOORDİNATÖR ADRESİ KODA GÖMÜLMEZ. Ürün çok evli: her evin köprüsü başka adreste durur.
 * Tek doğru kaynak, Zigbee2MQTT `configuration.yaml`'daki `serial.port` değerinden türeyen
 * `config.zigbee.serial.path`; host da oradan çıkarılır. Adres bir USB seri yolsa "uzaktan
 * telsiz reseti" diye bir şey yoktur — yetenek `supported:false` döner, panel düğmeyi kapatır.
 */
const coordinatorRestartCapability = (): CoordinatorRestartCapability => {
  const configuredPath = config.zigbee?.serial.path;
  if (config.mode !== "direct" || config.setupPending || !configuredPath) {
    return { supported: false, reason: "not-configured", host: null };
  }
  let address;
  try {
    address = parseCoordinatorAddress(configuredPath);
  } catch {
    return { supported: false, reason: "invalid-address", host: null };
  }
  if (address.kind !== "tcp" || !address.host) {
    return { supported: false, reason: "serial", host: null };
  }
  return { supported: true, reason: "ok", host: address.host };
};

/** Panele giden özet: adres burada da AÇILMAZ, yalnız "yapılabilir mi" ve gerekçesi gider. */
const coordinatorRestartSummary = (): { supported: boolean; reason: CoordinatorRestartReason } => {
  const capability = coordinatorRestartCapability();
  return { supported: capability.supported, reason: capability.reason };
};

/**
 * Servisi yeniden başlat. Yanıt ÖNCE gider (202), süreç sonra iner: panel gövdedeki tahmini
 * süreyle geri sayabilsin. `process.exit(0)` doğrudan çağrılmaz — SIGTERM düzgün kapanışı
 * tetikler (`shutdown`: otomasyon günlüğü diske iner, kaynak ve HTTP kapanır) ve kapanışın
 * sonunda süreç sıfır koduyla biter. Standalone runtime içinde gömülü çalışırken de aynı
 * sinyal `apps/runtime/main.cjs`'in kendi kapanışını çalıştırır. Geri getiren şey systemd:
 * `apps/linux/systemd/villa-bridge.service.in` şablonunda `Restart=always` + `RestartSec=5`.
 */
app.post<{ Body?: { confirmation?: unknown } }>("/api/system/restart", async (request, reply) => {
  if (request.body?.confirmation !== "RESTART") {
    return reply.code(400).send({ ok: false, error: "Yeniden başlatma onayı geçersiz." });
  }
  console.log("Panelden servis yeniden başlatma istendi.");
  const timer = setTimeout(() => process.kill(process.pid, "SIGTERM"), 750);
  timer.unref();
  return reply.code(202).send({
    ok: true,
    restarting: true,
    estimatedSeconds: serviceRestartEstimateSeconds
  });
});

/**
 * Koordinatörün TELSİZ ÇİPİNİ yeniden başlat (SLZB ve benzeri ağ köprüleri: `action=4&cmd=1`,
 * yani ZB_RST). Çip kilitlenip her komut `BUFFER_FULL` ile düştüğünde tek çıkış yolu budur.
 *
 * BİLEREK YALNIZ `cmd=1`. `cmd=9` (fabrika ayarlarına dönüş) ve `cmd=2` (bootloader) bu koda
 * ASLA girmemeli: ikisi de ağ anahtarını ve eşleşmiş cihaz listesini geri dönüşsüz siler,
 * yani evdeki bütün cihazların yeniden eşleştirilmesi gerekir. `cmd=3` (ESP32 reboot) de
 * yoktur — sorunu çözen çip reseti, kartın kendisinin yeniden başlaması değil.
 *
 * Köprüye yalnız bu tek GET gider; koordinatörün seri/TCP oturumuna dokunulmaz.
 */
app.post("/api/system/coordinator-restart", async (_request, reply) => {
  const capability = coordinatorRestartCapability();
  if (!capability.supported || !capability.host) {
    const serial = capability.reason === "serial";
    return reply.code(serial ? 409 : 503).send({
      ok: false,
      reason: capability.reason,
      error: serial
        ? "Koordinatör USB seri yolla bağlı; telsiz çipi uzaktan yeniden başlatılamaz."
        : "Koordinatör adresi tanımlı değil; telsiz çipi yeniden başlatılamaz."
    });
  }
  const target = new URL("/api2", `http://${capability.host}`);
  target.searchParams.set("action", "4");
  target.searchParams.set("cmd", "1");
  try {
    const response = await fetch(target, { signal: AbortSignal.timeout(5000) });
    if (!response.ok) {
      recentErrors.record({
        operation: "coordinator-restart",
        statusCode: response.status,
        message: `Koordinatör köprüsü ${response.status} yanıtı verdi.`
      });
      return reply.code(502).send({
        ok: false,
        error: `Koordinatör köprüsü ${response.status} yanıtı verdi.`
      });
    }
    console.log("Koordinatörün telsiz çipi panelden yeniden başlatıldı.");
    return { ok: true, estimatedSeconds: coordinatorRestartEstimateSeconds };
  } catch (error) {
    const timedOut = error instanceof Error && error.name === "TimeoutError";
    const message = timedOut
      ? "Koordinatör köprüsüne 5 saniyede ulaşılamadı."
      : `Koordinatör köprüsüne ulaşılamadı: ${error instanceof Error ? error.message : String(error)}`;
    recentErrors.record({ operation: "coordinator-restart", statusCode: 504, message });
    return reply.code(504).send({ ok: false, error: message });
  }
});

app.post<{ Body?: { open?: boolean } }>("/api/matter/commissioning", async (request, reply) => {
  if (typeof request.body?.open !== "boolean") {
    return reply.code(400).send({ ok: false, error: "Bağlantı durumu belirtilmedi." });
  }
  try {
    return { ok: true, matter: await matterbridge.setCommissioning(request.body.open) };
  } catch (error) {
    return reply.code(503).send({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});

app.get("/api/zigbee/backup", async (_request, reply) => {
  try {
    const prepared = await source.prepareNetworkBackup();
    const date = new Date().toISOString().slice(0, 10);
    if (prepared) {
      return reply
        .header("Content-Disposition", `attachment; filename="villa-bridge-zigbee-${date}.${prepared.extension}"`)
        .type(prepared.contentType)
        .send(prepared.body);
    }
    if (!config.zigbee) throw new Error("Yerel Zigbee veri dizini bulunamadı.");
    const backup = await createZigbeeNetworkBackup(config.zigbee.dataDir);
    return reply
      .header("Content-Disposition", `attachment; filename="villa-bridge-zigbee-${date}.json"`)
      .type("application/json; charset=utf-8")
      .send(`${JSON.stringify(backup, null, 2)}\n`);
  } catch (error) {
    return reply.code(503).send({
      ok: false,
      error: error instanceof Error ? error.message : String(error)
    });
  }
});

app.post<{ Body?: { confirmation?: unknown; backup?: unknown } }>(
  "/api/zigbee/restore",
  async (request, reply) => {
    if (!config.zigbee) {
      return reply.code(503).send({ ok: false, error: "Yerel Zigbee ağı bu çalışma modunda kullanılmıyor." });
    }
    if (request.body?.confirmation !== "RESTORE") {
      return reply.code(400).send({ ok: false, error: "Geri yükleme onayı geçersiz." });
    }
    try {
      await stageZigbeeNetworkRestore(config.zigbee.dataDir, request.body.backup);
      const timer = setTimeout(() => process.kill(process.pid, "SIGTERM"), 750);
      timer.unref();
      return { ok: true, restarting: true };
    } catch (error) {
      return reply.code(400).send({
        ok: false,
        error: error instanceof Error ? error.message : String(error)
      });
    }
  }
);

app.get("/", async (request, reply) =>
  sendAsset(request, reply, "text/html; charset=utf-8", dashboard, dashboardETag, htmlCacheControl));

type EmbeddedVillaBridgeGlobal = typeof globalThis & {
  __villaBridgeReady?: boolean;
  __villaBridgeStop?: () => Promise<void>;
};

const embeddedRuntime = process.env.VILLA_BRIDGE_EMBEDDED === "true";
const embeddedGlobal = globalThis as EmbeddedVillaBridgeGlobal;
let discoveryResponder: Awaited<ReturnType<typeof startLanDiscoveryResponder>> = null;
const shutdown = async (): Promise<void> => {
  if (!embeddedGlobal.__villaBridgeReady) return;
  embeddedGlobal.__villaBridgeReady = false;
  clearInterval(imageWarmTimer);
  clearTimeout(imageWarmStartTimer);
  automationEngine.stop();
  // Bekleyen `delay`'ler durdu; son günlük satırları yine de diske insin.
  await automationEngine.settle();
  await automationRunLog.flush();
  peerWatcher?.stop();
  weatherService.stop();
  await discoveryResponder?.close();
  await source.stop();
  await app.close();
  if (!embeddedRuntime) process.exit(0);
};
embeddedGlobal.__villaBridgeStop = shutdown;
if (!embeddedRuntime) {
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
}

// Sıra önemli: düğüm koordinatöre bağlanmadan ÖNCE ağda görünür olmalı (§1.5/5).
await app.listen({ host: config.http.host, port: config.http.port });
try {
  // Çalışma zamanı failover'ı yürütüyorsa UDP duyurusu onun: aynı portta iki yanıtlayıcı olmaz.
  discoveryResponder = failoverHook ? null : await startLanDiscoveryResponder(discoveryRecord);
  if (discoveryResponder) {
    console.log(
      `Villa Bridge LAN keşfi UDP ${villaBridgeDiscoveryPort} portunda hazır (düğüm ${nodeId}).`
    );
  }
} catch (error) {
  console.warn(`Villa Bridge LAN keşfi başlatılamadı: ${String(error)}`);
}

// Üç kanallı karşı düğüm izlemesi (§4.2): yalnızca gözlem, hiçbir devralma yok.
if (nodeRole !== "disabled") {
  const mqttPort = (() => {
    try {
      const port = Number(new URL(config.mqtt.url).port);
      return Number.isInteger(port) && port > 0 ? port : 1883;
    } catch {
      return 1883;
    }
  })();
  peerWatcher = createPeerWatcher({
    probe: createPeerProbe({ selfNodeId: nodeId, mqttPort }),
    logger: (message) => console.log(message)
  });
  peerWatcher.start();
}

if (config.setupPending) {
  // Kurulum kipi: koordinatör oturumu hiç denenmez, otomasyon motoru da beklemede kalır.
  // Düğüm ağda "standby" görünür, böylece hiçbir eş bu düğümü sahip sanmaz.
  coordinatorError = "Kurulum tamamlanmadı: koordinatör adresi girilmedi.";
  applyCoordinatorStatus("coordinator-unavailable");
  console.log("Kurulum sihirbazı bekleniyor; Zigbee kaynağı bilerek başlatılmadı.");
} else {
  // Çalışma zamanı yoksa (doğrudan `node dist/index.js`) talep-et-bekle kapısı: LAN'da aynı
  // koordinatörün başka bir sahibi varsa BAĞLANILMAZ; 30 sn'de bir yeniden bakılır.
  // Devir el sıkışması (release + snapshot) yalnız çalışma zamanındadır (apps/runtime).
  if (!failoverHook && nodeRole !== "disabled") {
    for (;;) {
      const peers = await queryLanPeers({ selfNodeId: nodeId, windowMs: 6_000 });
      const { owner } = evaluatePeers(discoveryRecord, peers);
      if (!owner) break;
      coordinatorStatus = "coordinator-unavailable";
      coordinatorError = `Koordinatör başka düğümde (${owner.nodeId || owner.address}); `
        + "o bırakmadan bağlanılmıyor.";
      console.warn(`${coordinatorError} 30 sn sonra yeniden bakılacak.`);
      await new Promise((resolveWait) => setTimeout(resolveWait, 30_000));
    }
  }
  try {
    await source.start();
    coordinatorError = null;
    applyCoordinatorStatus("ready");
    // Kaynak ayaktayken: süresi geçmiş kapatmalar hemen uygulanır, kalanlar kaldığı yerden sürer.
    await ensureAutomationEngineStarted();
  } catch (error) {
    // Süreç ölmez: HTTP ve duyuru ayakta kalır, durum arayüzde/diagnostikte görünür.
    coordinatorError = error instanceof Error ? error.message : String(error);
    applyCoordinatorStatus("coordinator-unavailable");
    recentErrors.record({
      operation: "source.start",
      statusCode: 503,
      message: coordinatorError
    });
    console.error(
      `Zigbee kaynağı başlatılamadı, düğüm koordinatörsüz sürüyor: ${coordinatorError}`
    );
  }
}
// Hava durumu koordinatörden bağımsızdır: Zigbee kaynağı düşse de panel havayı görmeye devam eder.
// Açılışı beklemeye değmez, arka planda kurulur; konum yoksa hiç dışarıya çıkmaz.
void weatherService.start().catch((error: unknown) => {
  console.error(`Hava durumu servisi başlatılamadı: ${String(error)}`);
});
const warmDeviceImages = (): void => {
  const models = store.getDevices()
    .map((device) => device.image.model?.trim())
    .filter((model): model is string => Boolean(model));
  if (models.length === 0) return;
  void imageCache.warm(models).catch((error) => {
    console.error(`Cihaz görselleri önden indirilemedi: ${String(error)}`);
  });
};
const imageWarmTimer = setInterval(warmDeviceImages, 5 * 60_000);
imageWarmTimer.unref();
const imageWarmStartTimer = setTimeout(warmDeviceImages, 5_000);
imageWarmStartTimer.unref();

embeddedGlobal.__villaBridgeReady = true;
console.log(`Villa Bridge paneli ${config.http.host}:${config.http.port} adresinde hazır.`);
