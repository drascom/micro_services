import { readFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import YAML from "yaml";
import { isPlaceholderCoordinatorAddress } from "./coordinator-probe.js";
import { isValidTimeZone } from "./astronomy.js";
import { isValidLatitude, isValidLongitude } from "./sun.js";

interface FileConfig {
  mode?: string;
  pairingControl?: boolean;
  zigbee?: {
    dataDir?: string;
    configurationFile?: string;
    externalConvertersDir?: string;
  };
  mqtt?: {
    url?: string;
    baseTopic?: string;
    credentialsFile?: string;
    username?: string;
    password?: string;
  };
  aliasesFile?: string;
  matterbridge?: {
    wsUrl?: string;
  };
  homeAssistant?: {
    discoveryEnabled?: boolean;
  };
  alerts?: {
    lowBatteryThreshold?: number;
  };
  location?: {
    latitude?: number;
    longitude?: number;
    timeZone?: string;
  };
  selfHealing?: {
    enabled?: boolean;
    probeOffline?: boolean;
  };
  debug?: {
    enabled?: boolean;
  };
  mcp?: {
    allowedOrigins?: string[];
    allowDangerousControls?: boolean;
  };
  http?: {
    host?: string;
    port?: number;
  };
}

interface Z2mConfig {
  mqtt?: {
    server?: string;
    base_topic?: string;
    user?: string;
    password?: string;
  };
  serial?: {
    port?: string;
    baudrate?: number;
    rtscts?: boolean;
    adapter?: string;
    disable_led?: boolean;
  };
  advanced?: {
    channel?: number;
    pan_id?: number | "GENERATE";
    ext_pan_id?: number[] | "GENERATE";
    network_key?: number[] | "GENERATE";
    adapter_concurrent?: number;
    adapter_delay?: number;
    transmit_power?: number;
  };
  devices?: Record<string, {
    friendly_name?: string;
    disabled?: boolean;
    retention?: number;
    debounce?: number;
    throttle?: number;
    debounce_ignore?: string[];
    [key: string]: unknown;
  }>;
  groups?: Record<string, {
    friendly_name?: string;
    [key: string]: unknown;
  }>;
}

export interface DirectZigbeeConfig {
  dataDir: string;
  configurationFile?: string;
  /**
   * Kütüphanede bulunmayan cihazlar için harici converter klasörü. Zigbee2MQTT'nin
   * `external_converters/` klasörüyle aynı biçimdedir; varsayılanı da oradır.
   */
  externalConvertersDir: string;
  serial: {
    path: string;
    baudRate: number;
    rtscts?: boolean;
    adapter: "deconz" | "ember" | "zstack" | "zboss" | "zigate" | "ezsp" | "zoh";
  };
  network: {
    channel: number;
    panId: number;
    extendedPanId: number[];
    networkKey: number[];
  };
  adapter: {
    concurrent?: number;
    delay?: number;
    disableLed?: boolean;
    transmitPower?: number;
  };
  devices: NonNullable<Z2mConfig["devices"]>;
  groups: NonNullable<Z2mConfig["groups"]>;
}

export interface AppConfig {
  mode: "shadow" | "direct";
  /**
   * Kurulum henüz bitmedi: koordinatör adresi girilmemiş ya da ağ ayarları eksik.
   * Bu doğruyken HTTP ve panel **açılır** (sihirbaz orada çalışır) ama Zigbee yığını
   * hiç başlatılmaz. "Kuruldu mu" ölçütü dosyanın varlığı değil, adresin girilmiş
   * olmasıdır; ilk açılışta hiçbir koordinatör sahiplenilmez.
   */
  setupPending: boolean;
  pairingControl: boolean;
  mqtt: {
    url: string;
    baseTopic: string;
    username?: string;
    password?: string;
  };
  zigbee?: DirectZigbeeConfig;
  aliasesFile?: string;
  matterbridge: {
    wsUrl: string;
  };
  homeAssistant: {
    discoveryEnabled: boolean;
  };
  alerts: {
    lowBatteryThreshold: number;
  };
  /**
   * Evin koordinatları — yalnızca gün doğumu/batımı tetikleyicisi için. Ürün çok evli olduğu
   * için varsayılan yoktur; verilmezse güneş kuralları çalışmaz ve sebebi günlüğe yazılır.
   * Kullanıcı panelden girerse değer `location.json`'a yazılır ve bunun önüne geçer.
   */
  location?: {
    latitude: number;
    longitude: number;
    timeZone: string;
  };
  /**
   * Otomatik onarım. Faz 1: cihaz kendini ilan edince raporlama ayarları yeniden yazılır.
   * Faz 2 (`probeOffline`): çevrimdışı görünen şebeke beslemeli yönlendiriciler tek ucuz
   * okumayla yoklanır; yanıt verirse erişilebilirlik geri alınır.
   */
  selfHealing: {
    enabled: boolean;
    probeOffline: boolean;
  };
  debug: {
    enabled: boolean;
  };
  /**
   * MCP ucu (`POST /mcp`). `allowedOrigins` yalnız DNS rebinding koruması içindir: liste boşken
   * `Origin` başlığı gönderen (yani tarayıcı içinden gelen) istek reddedilir, başlık göndermeyen
   * makine istemcisi çalışır. Tarayıcıdan çağıracak bir istemci varsa kökeni buraya yazılır.
   */
  mcp: {
    allowedOrigins: string[];
    /**
     * §8.1 muafiyeti. Kapalıyken (varsayılan) kilit ve siren kanalları `set_device` ile
     * yazılamaz — bir otomasyonun evi açmaması gerekçesi bir model için de aynen geçerlidir.
     * Panelden elle kumanda bu bayraktan **etkilenmez**; yasak yalnız ajan yolundadır.
     */
    allowDangerousControls: boolean;
  };
  http: {
    host: string;
    port: number;
  };
}

async function readYaml<T>(path: string): Promise<T> {
  return YAML.parse(await readFile(path, "utf8")) as T;
}

async function optionalZ2mCredentials(path?: string): Promise<Z2mConfig> {
  if (!path) return {};
  try {
    return await readYaml<Z2mConfig>(path);
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    console.warn(`MQTT kimlik dosyası okunamadı; anonim bağlantı denenecek: ${reason}`);
    return {};
  }
}

export async function loadConfig(): Promise<AppConfig> {
  const configPath = resolve(process.env.VILLA_BRIDGE_CONFIG ?? "config/default.yaml");
  const file = await readYaml<FileConfig>(configPath);
  const requestedMode = process.env.VILLA_BRIDGE_MODE ?? file.mode ?? "shadow";
  if (requestedMode !== "shadow" && requestedMode !== "direct") throw new Error("Geçersiz çalışma modu.");

  const z2mPath = file.zigbee?.configurationFile ?? file.mqtt?.credentialsFile;
  const z2m = await optionalZ2mCredentials(z2mPath);
  const port = Number(process.env.VILLA_BRIDGE_PORT ?? file.http?.port ?? 8091);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error("Geçersiz HTTP portu.");
  }

  const result: AppConfig = {
    mode: requestedMode,
    setupPending: false,
    pairingControl: process.env.VILLA_BRIDGE_PAIRING_CONTROL
      ? process.env.VILLA_BRIDGE_PAIRING_CONTROL === "true"
      : file.pairingControl === true,
    mqtt: {
      url: process.env.VILLA_BRIDGE_MQTT_URL ?? file.mqtt?.url ?? z2m.mqtt?.server ?? "mqtt://127.0.0.1:1883",
      baseTopic: process.env.VILLA_BRIDGE_MQTT_BASE_TOPIC ?? file.mqtt?.baseTopic ?? z2m.mqtt?.base_topic ?? "zigbee2mqtt",
      username: process.env.VILLA_BRIDGE_MQTT_USERNAME ?? file.mqtt?.username ?? z2m.mqtt?.user,
      password: process.env.VILLA_BRIDGE_MQTT_PASSWORD ?? file.mqtt?.password ?? z2m.mqtt?.password
    },
    aliasesFile: process.env.VILLA_BRIDGE_ALIASES_FILE ?? file.aliasesFile,
    matterbridge: {
      wsUrl: process.env.VILLA_BRIDGE_MATTERBRIDGE_WS_URL ?? file.matterbridge?.wsUrl ?? "ws://127.0.0.1:8283"
    },
    homeAssistant: {
      discoveryEnabled: process.env.VILLA_BRIDGE_HOME_ASSISTANT_DISCOVERY
        ? process.env.VILLA_BRIDGE_HOME_ASSISTANT_DISCOVERY === "true"
        : file.homeAssistant?.discoveryEnabled === true
    },
    alerts: {
      lowBatteryThreshold: Number(
        process.env.VILLA_BRIDGE_LOW_BATTERY_THRESHOLD
        ?? file.alerts?.lowBatteryThreshold
        ?? 15
      )
    },
    selfHealing: {
      enabled: process.env.VILLA_BRIDGE_SELF_HEALING
        ? process.env.VILLA_BRIDGE_SELF_HEALING === "true"
        : file.selfHealing?.enabled !== false,
      probeOffline: process.env.VILLA_BRIDGE_SELF_HEALING_PROBE_OFFLINE
        ? process.env.VILLA_BRIDGE_SELF_HEALING_PROBE_OFFLINE === "true"
        : file.selfHealing?.probeOffline !== false
    },
    debug: {
      enabled: process.env.VILLA_BRIDGE_DEBUG
        ? process.env.VILLA_BRIDGE_DEBUG === "true"
        : file.debug?.enabled !== false
    },
    mcp: {
      allowedOrigins: (process.env.VILLA_BRIDGE_MCP_ALLOWED_ORIGINS !== undefined
        ? process.env.VILLA_BRIDGE_MCP_ALLOWED_ORIGINS.split(",")
        : file.mcp?.allowedOrigins ?? [])
        .map((origin) => String(origin).trim())
        .filter((origin) => origin.length > 0),
      allowDangerousControls: process.env.VILLA_BRIDGE_MCP_ALLOW_DANGEROUS_CONTROLS
        ? process.env.VILLA_BRIDGE_MCP_ALLOW_DANGEROUS_CONTROLS === "true"
        : file.mcp?.allowDangerousControls === true
    },
    http: {
      host: process.env.VILLA_BRIDGE_HOST ?? file.http?.host ?? "0.0.0.0",
      port
    }
  };
  const latitude = process.env.VILLA_BRIDGE_LATITUDE !== undefined
    ? Number(process.env.VILLA_BRIDGE_LATITUDE)
    : file.location?.latitude;
  const longitude = process.env.VILLA_BRIDGE_LONGITUDE !== undefined
    ? Number(process.env.VILLA_BRIDGE_LONGITUDE)
    : file.location?.longitude;
  const timeZone = process.env.VILLA_BRIDGE_TIME_ZONE
    ?? file.location?.timeZone
    ?? Intl.DateTimeFormat().resolvedOptions().timeZone
    ?? "UTC";
  if (latitude !== undefined || longitude !== undefined) {
    if (!isValidLatitude(latitude) || !isValidLongitude(longitude)) {
      throw new Error("Konum için enlem (-90..90) ve boylam (-180..180) birlikte verilmelidir.");
    }
    if (!isValidTimeZone(timeZone)) {
      throw new Error("Konum için Europe/London biçiminde geçerli bir IANA saat dilimi verilmelidir.");
    }
    result.location = { latitude, longitude, timeZone };
  }
  if (
    !Number.isInteger(result.alerts.lowBatteryThreshold)
    || result.alerts.lowBatteryThreshold < 5
    || result.alerts.lowBatteryThreshold > 50
  ) {
    throw new Error("Düşük pil eşiği 5-50 arasında olmalıdır.");
  }
  if (requestedMode === "direct") {
    const serial = z2m.serial;
    const advanced = z2m.advanced;
    const adapters = ["deconz", "ember", "zstack", "zboss", "zigate", "ezsp", "zoh"] as const;
    /**
     * Eskiden burada hata fırlatılırdı; sonuç kısır döngüydü: sihirbaza ulaşmak için
     * sihirbazın soracağı ayarları elle yazmak gerekiyordu. Artık eksik/yer tutucu
     * ayar süreci öldürmez — `setupPending` işaretlenir, panel açılır, Zigbee yığını
     * hiç kurulmaz (`src/index.ts` bu bayrağı görünce inert kaynak kullanır).
     */
    const networkComplete =
      Boolean(serial?.adapter) &&
      adapters.includes(serial?.adapter as (typeof adapters)[number]) &&
      typeof advanced?.channel === "number" &&
      typeof advanced.pan_id === "number" &&
      Array.isArray(advanced.ext_pan_id) &&
      Array.isArray(advanced.network_key);
    result.setupPending = !networkComplete || isPlaceholderCoordinatorAddress(serial?.port);
    const dataDir = file.zigbee?.dataDir ?? (z2mPath ? dirname(z2mPath) : "/opt/zigbee2mqtt/data");
    result.zigbee = {
      dataDir,
      configurationFile: z2mPath,
      externalConvertersDir: process.env.VILLA_BRIDGE_EXTERNAL_CONVERTERS_DIR
        ?? file.zigbee?.externalConvertersDir
        ?? join(dataDir, "external_converters"),
      serial: {
        // Kurulum bitmeden bu değerler yalnız panele gösterilir; hiçbir porta bağlanılmaz.
        path: serial?.port ?? "",
        baudRate: serial?.baudrate ?? 115200,
        rtscts: serial?.rtscts,
        adapter: adapters.includes(serial?.adapter as (typeof adapters)[number])
          ? serial?.adapter as (typeof adapters)[number]
          : "zstack"
      },
      network: {
        channel: typeof advanced?.channel === "number" ? advanced.channel : 15,
        panId: typeof advanced?.pan_id === "number" ? advanced.pan_id : 0,
        extendedPanId: Array.isArray(advanced?.ext_pan_id) ? advanced.ext_pan_id : [],
        networkKey: Array.isArray(advanced?.network_key) ? advanced.network_key : []
      },
      adapter: {
        concurrent: advanced?.adapter_concurrent,
        delay: advanced?.adapter_delay,
        disableLed: serial?.disable_led,
        transmitPower: advanced?.transmit_power
      },
      devices: z2m.devices ?? {},
      groups: z2m.groups ?? {}
    };
  }
  return result;
}
