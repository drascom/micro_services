"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const YAML = require("yaml");
const { installMatterBigIntGuard } = require("./matter-bigint-guard.cjs");
const { PLACEHOLDER_COORDINATOR } = require("./first-run.cjs");

const PROVISIONING_FILE = "provisioning.json";
const LEGACY_PROVISIONING_FILE = "android-provisioning.json";
const DEFAULT_CONFIG_FILE = path.join("config", "villa-bridge.yaml");
const COORDINATOR_BLACKLIST = ["Coordinator"];

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

function isPathInside(root, candidate) {
  const relative = path.relative(path.resolve(root), path.resolve(candidate));
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function resolveDataPath(dataDir, value, fallback) {
  const requested = typeof value === "string" && value.trim() ? value.trim() : fallback;
  const resolved = path.resolve(dataDir, requested);
  if (!isPathInside(dataDir, resolved)) {
    throw new Error(`Provisioned path must stay inside the runtime data directory: ${requested}`);
  }
  return resolved;
}

function readJsonIfPresent(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (error) {
    if (error?.code === "ENOENT") return {};
    throw error;
  }
}

function loadProvisioning(config) {
  const preferredProvisionFile = path.join(config.dataDir, PROVISIONING_FILE);
  const legacyProvisionFile = path.join(config.dataDir, LEGACY_PROVISIONING_FILE);
  const provisionFile = fs.existsSync(preferredProvisionFile)
    ? preferredProvisionFile
    : legacyProvisionFile;
  try {
    const options = readJsonIfPresent(provisionFile);
    const sourceConfigPath = resolveDataPath(
      config.dataDir,
      options.config,
      DEFAULT_CONFIG_FILE
    );
    if (!fs.existsSync(sourceConfigPath)) {
      return {
        provisioned: false,
        setupPending: false,
        reason: `Waiting for ${path.relative(config.dataDir, sourceConfigPath)}.`,
        provisionFile,
        sourceConfigPath,
        matterEnabled: options.matter !== false
      };
    }

    const source = YAML.parse(fs.readFileSync(sourceConfigPath, "utf8"));
    if (!source || typeof source !== "object") {
      throw new Error("Villa Bridge configuration must be a YAML object.");
    }
    if (source.mode !== "direct") {
      throw new Error("Shared standalone mode requires 'mode: direct'.");
    }
    const z2mValue = source.zigbee?.configurationFile;
    if (typeof z2mValue !== "string" || !z2mValue.trim()) {
      throw new Error("zigbee.configurationFile is required for standalone direct mode.");
    }
    const z2mConfigPath = resolveDataPath(
      config.dataDir,
      path.isAbsolute(z2mValue)
        ? path.relative(config.dataDir, z2mValue)
        : path.join(path.dirname(path.relative(config.dataDir, sourceConfigPath)), z2mValue),
      path.join("zigbee2mqtt", "configuration.yaml")
    );
    if (!fs.existsSync(z2mConfigPath)) {
      return {
        provisioned: false,
        setupPending: false,
        reason: `Waiting for ${path.relative(config.dataDir, z2mConfigPath)}.`,
        provisionFile,
        sourceConfigPath,
        z2mConfigPath,
        matterEnabled: options.matter !== false
      };
    }
    const z2m = YAML.parse(fs.readFileSync(z2mConfigPath, "utf8"));
    if (!z2m || typeof z2m !== "object") {
      throw new Error("Zigbee2MQTT configuration must be a YAML object.");
    }
    const mqttUsername = typeof z2m.mqtt?.user === "string" && z2m.mqtt.user
      ? z2m.mqtt.user
      : null;
    const mqttPassword = typeof z2m.mqtt?.password === "string"
      ? z2m.mqtt.password
      : null;
    const mqttAuthRequired = mqttUsername !== null && mqttPassword !== null;
    /**
     * "Kuruldu mu" olcutu artik dosyanin VARLIGI degil, koordinator adresinin gercekten
     * girilmis olmasidir. Yer tutucu adres duruyorsa panel ve HTTP acilir (sihirbaz orada
     * calisir) ama hicbir koordinator sahiplenilmez — ilk acilis guvenligi budur.
     */
    const coordinatorAddress = typeof z2m.serial?.port === "string" ? z2m.serial.port.trim() : "";
    const setupPending = coordinatorAddress === "" || coordinatorAddress === PLACEHOLDER_COORDINATOR;

    const runtimeDir = path.join(config.dataDir, "runtime");
    const resolvedConfigPath = path.join(runtimeDir, "villa-bridge.yaml");
    fs.mkdirSync(runtimeDir, { recursive: true });
    source.mode = "direct";
    source.zigbee = {
      ...(source.zigbee || {}),
      dataDir: path.join(config.dataDir, "zigbee"),
      configurationFile: z2mConfigPath
    };
    source.mqtt = {
      ...(source.mqtt || {}),
      url: `mqtt://127.0.0.1:${config.mqttPort}`
    };
    source.aliasesFile = path.join(config.dataDir, "aliases.json");
    source.matterbridge = {
      ...(source.matterbridge || {}),
      wsUrl: "ws://127.0.0.1:8283"
    };
    source.http = { host: config.coreHost, port: config.corePort };
    fs.writeFileSync(resolvedConfigPath, YAML.stringify(source), { mode: 0o600 });

    return {
      provisioned: true,
      setupPending,
      reason: setupPending ? "Coordinator address is not set yet; finish the setup wizard." : null,
      provisionFile,
      sourceConfigPath,
      resolvedConfigPath,
      z2mConfigPath,
      matterEnabled: options.matter !== false,
      baseTopic: source.mqtt.baseTopic || "zigbee2mqtt",
      mqttUsername,
      mqttPassword,
      mqttAuthRequired
    };
  } catch (error) {
    return {
      provisioned: false,
      setupPending: false,
      reason: errorMessage(error),
      provisionFile,
      sourceConfigPath: null,
      matterEnabled: false
    };
  }
}

function buildCoreEnvironment(config, provision) {
  if (!provision.provisioned) {
    throw new Error(provision.reason || "Standalone runtime is not provisioned.");
  }
  return {
    VILLA_BRIDGE_CONFIG: provision.resolvedConfigPath,
    VILLA_BRIDGE_MODE: "direct",
    VILLA_BRIDGE_EMBEDDED: "true",
    VILLA_BRIDGE_NODE_ROLE: ["linux", "raspberry-pi"].includes(config.platform)
      ? "server"
      : "android",
    VILLA_BRIDGE_HOST: config.coreHost,
    VILLA_BRIDGE_PORT: String(config.corePort),
    VILLA_BRIDGE_MQTT_URL: `mqtt://127.0.0.1:${config.mqttPort}`,
    VILLA_BRIDGE_MQTT_BASE_TOPIC: provision.baseTopic || "zigbee2mqtt",
    VILLA_BRIDGE_MQTT_USERNAME: provision.mqttAuthRequired
      ? provision.mqttUsername
      : "",
    VILLA_BRIDGE_MQTT_PASSWORD: provision.mqttAuthRequired
      ? provision.mqttPassword
      : "",
    VILLA_BRIDGE_MATTERBRIDGE_WS_URL: "ws://127.0.0.1:8283"
  };
}

async function startVillaBridgeCore(config, provision, dependencies = {}) {
  const entrypoint = dependencies.entrypoint || config.coreEntrypoint ||
    path.join(__dirname, "villa-bridge", "dist", "index.js");
  const importModule = dependencies.importModule ||
    ((specifier) => import(specifier));
  if (!fs.existsSync(entrypoint) && !dependencies.entrypoint) {
    throw new Error("Bundled Villa Bridge core is missing.");
  }
  Object.assign(process.env, buildCoreEnvironment(config, provision));
  globalThis.__villaBridgeReady = false;
  globalThis.__villaBridgeStop = undefined;
  // Ayni surecte ikinci kez baslatma (failover: birakip yeniden devralma) modul onbellegini
  // atlatmak icin nesil sorgusu alir; ilk baslatmada URL degismez.
  const generation = Number.isInteger(dependencies.generation) && dependencies.generation > 0
    ? `?generation=${dependencies.generation}`
    : "";
  await importModule(`${pathToFileURL(entrypoint).href}${generation}`);
  if (globalThis.__villaBridgeReady !== true) {
    throw new Error("Villa Bridge core loaded but did not signal readiness.");
  }
  return {
    entrypoint,
    stop: async () => {
      if (typeof globalThis.__villaBridgeStop === "function") {
        await globalThis.__villaBridgeStop();
      }
    }
  };
}

function appendMatterbridgeArguments(argv, matterHome) {
  const valuedFlags = [
    ["-homedir", matterHome],
    ["-frontend", "8283"],
    ["-logger", "info"],
    ["-matterlogger", "info"]
  ];
  for (const [flag, value] of valuedFlags) {
    if (!argv.includes(flag)) argv.push(flag, value);
  }
  return argv;
}

function readPluginList(file, key) {
  try {
    const existing = JSON.parse(fs.readFileSync(file, "utf8"));
    return Array.isArray(existing?.[key]) ? existing[key] : [];
  } catch {
    // Bozuk ya da eksik config listeyi sifirlar; koordinator yine de disarida kalir.
    return [];
  }
}

function readPluginBlackList(file) {
  return readPluginList(file, "blackList");
}

function mergeBlackList(...sources) {
  const merged = [];
  for (const source of sources) {
    if (!Array.isArray(source)) continue;
    for (const entry of source) {
      if (typeof entry !== "string") continue;
      const value = entry.trim();
      if (value && !merged.includes(value)) merged.push(value);
    }
  }
  return merged;
}

function writeMatterbridgePluginConfig(config, provision, matterHome) {
  const directory = path.join(matterHome, ".matterbridge");
  fs.mkdirSync(directory, { recursive: true });
  const file = path.join(directory, "matterbridge-zigbee2mqtt.config.json");
  // Eklenti koordinatoru Matter'a kilit olarak sunuyor ve kilidi acmak Zigbee
  // eslestirme modunu (permit_join) aciyor. Eklentinin kendi kosulu
  // friendly_name === "Coordinator" esitligine baktigi icin ayni adi kara listeye
  // aliyoruz; kullanicinin kendi ekledigi girdiler korunur.
  const blackList = mergeBlackList(
    readPluginBlackList(file),
    provision.matterBlackList,
    config.matterBlackList,
    COORDINATOR_BLACKLIST
  );
  const value = {
    name: "matterbridge-zigbee2mqtt",
    type: "DynamicPlatform",
    debug: false,
    unregisterOnShutdown: false,
    host: "127.0.0.1",
    port: config.mqttPort,
    topic: provision.baseTopic || "zigbee2mqtt",
    protocolVersion: 4,
    username: provision.mqttAuthRequired ? provision.mqttUsername : "",
    password: provision.mqttAuthRequired ? provision.mqttPassword : "",
    whiteList: mergeBlackList(readPluginList(file, "whiteList")),
    blackList,
    // Eklenti varsayilan olarak bircok cihazi MA-onoffswitch (0x0103) diye yayinlar;
    // 0x0103 bir istemci tipi oldugu icin Alexa onu kontrol edilebilir cihaz saymaz.
    // Kullanicinin bu listelere yazdigi adlar cihazi light/outlet/switch tipine cevirir,
    // o yuzden yeniden yazarken korunur.
    switchList: mergeBlackList(readPluginList(file, "switchList")),
    lightList: mergeBlackList(readPluginList(file, "lightList")),
    outletList: mergeBlackList(readPluginList(file, "outletList")),
    featureBlackList: [],
    deviceFeatureBlackList: {}
  };
  fs.writeFileSync(file, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  return file;
}

async function defaultMatterbridgeLoader() {
  const implementation = path.join(
    __dirname,
    "node_modules",
    "matterbridge",
    "dist",
    "matterbridge.js"
  );
  if (!fs.existsSync(implementation)) {
    throw new Error("Bundled Matterbridge implementation is missing.");
  }
  return import(pathToFileURL(implementation).href);
}

function matterbridgePluginPackage() {
  const packageFile = path.join(
    __dirname,
    "node_modules",
    "matterbridge-zigbee2mqtt",
    "package.json"
  );
  if (!fs.existsSync(packageFile)) {
    throw new Error("Bundled Matterbridge Zigbee2MQTT plugin is missing.");
  }
  return packageFile;
}

async function preRegisterMatterbridgePlugin(matterHome, pluginPackage) {
  const packageJson = JSON.parse(fs.readFileSync(pluginPackage, "utf8"));
  const { NodeStorageManager } = await import("node-persist-manager");
  const manager = new NodeStorageManager({
    dir: path.join(matterHome, ".matterbridge", "storage"),
    writeQueue: false,
    expiredInterval: undefined,
    logging: false
  });
  try {
    const context = await manager.createStorage("matterbridge");
    const plugins = await context.get("plugins", []);
    const existing = plugins.find((plugin) => plugin.name === packageJson.name);
    if (!existing) {
      plugins.push({
        name: packageJson.name,
        enabled: true,
        path: pluginPackage,
        type: "AnyPlatform",
        version: packageJson.version,
        description: packageJson.description,
        author: typeof packageJson.author === "string"
          ? packageJson.author
          : packageJson.author?.name || "Unknown author"
      });
      await context.set("plugins", plugins);
    } else {
      existing.enabled = true;
      existing.path = pluginPackage;
      existing.version = packageJson.version;
      await context.set("plugins", plugins);
    }
  } finally {
    await manager.close();
  }
}

async function startMatterbridge(config, provision, dependencies = {}) {
  if (!provision.provisioned || !provision.matterEnabled) {
    throw new Error(provision.reason || "Matterbridge is disabled.");
  }
  const matterHome = path.join(config.dataDir, "matterbridge");
  fs.mkdirSync(matterHome, { recursive: true });
  appendMatterbridgeArguments(process.argv, matterHome);
  const pluginConfig = writeMatterbridgePluginConfig(config, provision, matterHome);
  const loadMatterbridge = dependencies.loadMatterbridge || defaultMatterbridgeLoader;
  const pluginPackage = dependencies.pluginPackage || matterbridgePluginPackage();
  const registerPlugin = dependencies.registerPlugin || preRegisterMatterbridgePlugin;
  await registerPlugin(matterHome, pluginPackage);
  const { Matterbridge } = await loadMatterbridge();
  // zigbee-herdsman'in BigInt yamasi Matter deposunu bozar; ilk depo yaziminden
  // once koruma devreye girmeli.
  const installGuard = dependencies.installBigIntGuard || installMatterBigIntGuard;
  await installGuard({ log: (message) => console.log(message) });
  const instance = await Matterbridge.loadInstance(true);
  const plugin = instance.plugins.get("matterbridge-zigbee2mqtt");
  if (!plugin) {
    throw new Error("Pre-registered Matterbridge Zigbee2MQTT plugin was not loaded.");
  }
  return {
    instance,
    plugin,
    pluginConfig,
    stop: async () => {
      if (typeof instance.shutdownProcess === "function") {
        await instance.shutdownProcess();
      }
    }
  };
}

function matterbridgeReady(service) {
  if (!service?.instance?.serverNode || !service.plugin) return false;
  return service.plugin.loaded === true &&
    service.plugin.started === true &&
    service.plugin.configured === true &&
    service.plugin.error !== true;
}

module.exports = {
  DEFAULT_CONFIG_FILE,
  PROVISIONING_FILE,
  appendMatterbridgeArguments,
  buildCoreEnvironment,
  isPathInside,
  loadProvisioning,
  matterbridgeReady,
  preRegisterMatterbridgePlugin,
  resolveDataPath,
  startMatterbridge,
  startVillaBridgeCore,
  writeMatterbridgePluginConfig
};
