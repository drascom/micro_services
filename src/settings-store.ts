import { copyFile, readFile, rename, stat, writeFile } from "node:fs/promises";
import YAML from "yaml";
import { atomicTemporaryPath } from "./atomic-file.js";
import { parseCoordinatorAddress } from "./coordinator-probe.js";

type YamlObject = Record<string, unknown>;

export interface ConnectionSettings {
  zigbee: {
    adapterUrl: string;
    channel: number;
  };
  mqtt: {
    url: string;
    baseTopic: string;
  };
  matter: {
    wsUrl: string;
  };
  homeAssistant: {
    discoveryEnabled: boolean;
  };
  alerts: {
    lowBatteryThreshold: number;
  };
  /** `probeOffline` Faz 2: çevrimdışı yönlendiricinin ucuz yoklaması; varsayılan açık. */
  selfHealing: {
    enabled: boolean;
    probeOffline: boolean;
  };
  debug: {
    enabled: boolean;
  };
}

const objectValue = (value: unknown): YamlObject =>
  typeof value === "object" && value !== null && !Array.isArray(value) ? value as YamlObject : {};

const endpoint = (value: unknown, protocols: string[], label: string): string => {
  if (typeof value !== "string" || value.length > 240) throw new Error(`${label} adresi geçersiz.`);
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error(`${label} adresi geçersiz.`);
  }
  if (!protocols.includes(parsed.protocol) || !parsed.hostname || !parsed.port || parsed.username || parsed.password) {
    throw new Error(`${label} adresi protokol, sunucu ve port içermeli; kimlik bilgisi içermemeli.`);
  }
  const port = Number(parsed.port);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error(`${label} portu geçersiz.`);
  return parsed.toString().replace(/\/$/, "");
};

export interface ConnectionSettingsOptions {
  /**
   * Seri yol (`/dev/ttyUSB0`) yalnız USB koordinatör takılabilen konaklarda geçerlidir.
   * Android tablette böyle bir yol hiç açılamaz; orada doğrulama eskisi gibi `tcp://` ister.
   */
  allowSerialAdapter?: boolean;
}

/** Koordinatör adresi: MQTT/Matter uçlarından ayrı kural — seri yol da geçerli bir değerdir. */
const coordinatorEndpoint = (value: unknown, allowSerial: boolean): string => {
  const address = parseCoordinatorAddress(value);
  if (address.kind === "serial" && !allowSerial) {
    throw new Error("Bu cihazda seri koordinatör yolu kullanılamaz; `tcp://sunucu:port` girin.");
  }
  return address.value;
};

export const validateConnectionSettings = (
  value: unknown,
  options: ConnectionSettingsOptions = {}
): ConnectionSettings => {
  const input = objectValue(value);
  const zigbee = objectValue(input.zigbee);
  const mqtt = objectValue(input.mqtt);
  const matter = objectValue(input.matter);
  const homeAssistant = objectValue(input.homeAssistant);
  const alerts = objectValue(input.alerts);
  const selfHealing = objectValue(input.selfHealing);
  const debug = objectValue(input.debug);
  const baseTopic = typeof mqtt.baseTopic === "string" ? mqtt.baseTopic.trim() : "";
  const channel = Number(zigbee.channel);
  if (!Number.isInteger(channel) || channel < 11 || channel > 26) {
    throw new Error("Zigbee kanalı 11-26 arasında olmalıdır.");
  }
  if (!/^[A-Za-z0-9][A-Za-z0-9/_-]{0,79}$/.test(baseTopic) || baseTopic.includes("//")) {
    throw new Error("MQTT temel konusu geçersiz.");
  }
  const lowBatteryThreshold = Number(alerts.lowBatteryThreshold ?? 15);
  if (
    !Number.isInteger(lowBatteryThreshold)
    || lowBatteryThreshold < 5
    || lowBatteryThreshold > 50
  ) {
    throw new Error("Düşük pil eşiği 5-50 arasında olmalıdır.");
  }
  return {
    zigbee: {
      adapterUrl: coordinatorEndpoint(zigbee.adapterUrl, options.allowSerialAdapter === true),
      channel
    },
    mqtt: {
      url: endpoint(mqtt.url, ["mqtt:", "mqtts:"], "MQTT sunucusu"),
      baseTopic
    },
    matter: {
      wsUrl: endpoint(matter.wsUrl, ["ws:", "wss:"], "Matterbridge")
    },
    homeAssistant: {
      discoveryEnabled: homeAssistant.discoveryEnabled === true
    },
    alerts: {
      lowBatteryThreshold
    },
    selfHealing: {
      enabled: selfHealing.enabled !== false,
      probeOffline: selfHealing.probeOffline !== false
    },
    debug: {
      enabled: debug.enabled !== false
    }
  };
};

const readYaml = async (path: string): Promise<YamlObject> =>
  objectValue(YAML.parse(await readFile(path, "utf8")));

const writeYamlTemporary = async (path: string, value: YamlObject): Promise<string> => {
  const temporary = atomicTemporaryPath(path);
  const current = await stat(path);
  await writeFile(temporary, YAML.stringify(value), { mode: current.mode });
  return temporary;
};

export class SettingsStore {
  constructor(
    private readonly configPath: string,
    private readonly z2mPath: string,
    private readonly fallback: ConnectionSettings,
    private readonly options: ConnectionSettingsOptions = {}
  ) {}

  /** Konak yeteneği: panel seri yol alanını yalnız bu doğruyken gösterir. */
  get serialSupported(): boolean {
    return this.options.allowSerialAdapter === true;
  }

  async get(): Promise<ConnectionSettings> {
    const [file, z2m] = await Promise.all([readYaml(this.configPath), readYaml(this.z2mPath)]);
    const fileMqtt = objectValue(file.mqtt);
    const fileMatter = objectValue(file.matterbridge);
    const fileHomeAssistant = objectValue(file.homeAssistant);
    const fileAlerts = objectValue(file.alerts);
    const fileSelfHealing = objectValue(file.selfHealing);
    const fileDebug = objectValue(file.debug);
    const z2mMqtt = objectValue(z2m.mqtt);
    const z2mSerial = objectValue(z2m.serial);
    const z2mAdvanced = objectValue(z2m.advanced);
    return validateConnectionSettings({
      zigbee: {
        adapterUrl: z2mSerial.port ?? this.fallback.zigbee.adapterUrl,
        channel: z2mAdvanced.channel ?? this.fallback.zigbee.channel
      },
      mqtt: {
        url: fileMqtt.url ?? z2mMqtt.server ?? this.fallback.mqtt.url,
        baseTopic: fileMqtt.baseTopic ?? z2mMqtt.base_topic ?? this.fallback.mqtt.baseTopic
      },
      matter: {
        wsUrl: fileMatter.wsUrl ?? this.fallback.matter.wsUrl
      },
      homeAssistant: {
        discoveryEnabled: fileHomeAssistant.discoveryEnabled ?? this.fallback.homeAssistant.discoveryEnabled
      },
      alerts: {
        lowBatteryThreshold:
          fileAlerts.lowBatteryThreshold
          ?? this.fallback.alerts.lowBatteryThreshold
      },
      selfHealing: {
        enabled: fileSelfHealing.enabled ?? this.fallback.selfHealing.enabled,
        probeOffline: fileSelfHealing.probeOffline ?? this.fallback.selfHealing.probeOffline
      },
      debug: {
        enabled: fileDebug.enabled ?? this.fallback.debug.enabled
      }
    }, this.options);
  }

  async save(
    value: unknown,
    options: { confirmZigbeeChannelChange?: boolean; confirmZigbeeAdapterChange?: boolean } = {}
  ): Promise<ConnectionSettings> {
    const settings = validateConnectionSettings(value, this.options);
    const [file, z2m] = await Promise.all([readYaml(this.configPath), readYaml(this.z2mPath)]);
    const fileMqtt = objectValue(file.mqtt);
    const fileMatter = objectValue(file.matterbridge);
    const fileHomeAssistant = objectValue(file.homeAssistant);
    const fileAlerts = objectValue(file.alerts);
    const fileSelfHealing = objectValue(file.selfHealing);
    const fileDebug = objectValue(file.debug);
    const z2mMqtt = objectValue(z2m.mqtt);
    const z2mSerial = objectValue(z2m.serial);
    const z2mAdvanced = objectValue(z2m.advanced);
    const currentChannel = Number(
      z2mAdvanced.channel
      ?? this.fallback.zigbee.channel
    );
    if (
      Number.isInteger(currentChannel)
      && currentChannel !== settings.zigbee.channel
      && options.confirmZigbeeChannelChange !== true
    ) {
      throw new Error(
        "Zigbee kanal değişikliği açık onay gerektirir; cihazlar bağlantısını kaybedebilir."
      );
    }
    /**
     * Koordinatör adresi kanaldan da tehlikelidir: yanlış adres kaydedilirse servis bir daha
     * açılmaz ve onu düzeltecek panel de gelmez — kurtarma bant dışıdır (SSH + `.settings-backup`).
     * Bu yüzden değişiklik açık onay ister. Adres okunamıyorsa (bozuk yapılandırma) onarım
     * yolunu kapatmamak için onay aranmaz.
     */
    let currentAdapter: string | null = null;
    try {
      currentAdapter = coordinatorEndpoint(
        z2mSerial.port ?? this.fallback.zigbee.adapterUrl,
        true
      );
    } catch {
      currentAdapter = null;
    }
    if (
      currentAdapter !== null
      && currentAdapter !== settings.zigbee.adapterUrl
      && options.confirmZigbeeAdapterChange !== true
    ) {
      throw new Error(
        "Koordinatör adresi değişikliği açık onay gerektirir; yanlış adres servisi açılamaz hâle getirir."
      );
    }

    file.mqtt = { ...fileMqtt, url: settings.mqtt.url, baseTopic: settings.mqtt.baseTopic };
    file.matterbridge = { ...fileMatter, wsUrl: settings.matter.wsUrl };
    file.homeAssistant = { ...fileHomeAssistant, discoveryEnabled: settings.homeAssistant.discoveryEnabled };
    file.alerts = {
      ...fileAlerts,
      lowBatteryThreshold: settings.alerts.lowBatteryThreshold
    };
    file.selfHealing = {
      ...fileSelfHealing,
      enabled: settings.selfHealing.enabled,
      probeOffline: settings.selfHealing.probeOffline
    };
    file.debug = { ...fileDebug, enabled: settings.debug.enabled };
    z2m.mqtt = { ...z2mMqtt, server: settings.mqtt.url, base_topic: settings.mqtt.baseTopic };
    z2m.serial = { ...z2mSerial, port: settings.zigbee.adapterUrl };
    z2m.advanced = { ...z2mAdvanced, channel: settings.zigbee.channel };

    const [fileTemporary, z2mTemporary] = await Promise.all([
      writeYamlTemporary(this.configPath, file),
      writeYamlTemporary(this.z2mPath, z2m)
    ]);
    const fileBackup = `${this.configPath}.settings-backup`;
    const z2mBackup = `${this.z2mPath}.settings-backup`;
    await Promise.all([
      copyFile(this.configPath, fileBackup),
      copyFile(this.z2mPath, z2mBackup)
    ]);
    try {
      await rename(fileTemporary, this.configPath);
      await rename(z2mTemporary, this.z2mPath);
    } catch (error) {
      await Promise.allSettled([
        copyFile(fileBackup, this.configPath),
        copyFile(z2mBackup, this.z2mPath)
      ]);
      throw error;
    }
    return settings;
  }
}
