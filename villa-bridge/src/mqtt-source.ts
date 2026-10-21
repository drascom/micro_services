import mqtt, { type MqttClient } from "mqtt";
import type { AppConfig } from "./config.js";
import { DeviceStore } from "./device-store.js";
import type {
  OtaCheckResult,
  PreparedNetworkBackup,
  ZigbeeNetworkMap,
  ZigbeeSource
} from "./source.js";
import type { JsonObject } from "./types.js";

export function z2mGroupIdentifier(id: string): string {
  const match = /^group-(\d+)$/.exec(id);
  return match?.[1] ?? id;
}

export class MqttShadowSource implements ZigbeeSource {
  private client: MqttClient | null = null;
  private transaction = 0;
  private readonly pendingRequests = new Map<string, {
    resolve: (data: JsonObject) => void;
    reject: (error: Error) => void;
    timer: NodeJS.Timeout;
  }>();

  constructor(
    private readonly config: AppConfig["mqtt"],
    private readonly store: DeviceStore,
    private readonly connect: typeof mqtt.connect = mqtt.connect
  ) {}

  start(): void {
    const prefix = `${this.config.baseTopic}/`;
    this.client = this.connect(this.config.url, {
      username: this.config.username,
      password: this.config.password,
      clientId: `villa-bridge-shadow-${process.pid}`,
      clean: true,
      reconnectPeriod: 2_000,
      connectTimeout: 10_000
    });
    this.client.on("connect", () => {
      this.store.setMqttConnected(true);
      this.client?.subscribe(`${this.config.baseTopic}/#`, { qos: 0 }, (error) => {
        if (error) console.error(`MQTT abonelik hatası: ${error.message}`);
      });
      console.log("Canlı Zigbee verisine bağlandı (salt okunur gölge modu).");
    });
    this.client.on("reconnect", () => this.store.setMqttConnected(false));
    this.client.on("close", () => this.store.setMqttConnected(false));
    this.client.on("offline", () => this.store.setMqttConnected(false));
    this.client.on("error", (error) => console.error(`MQTT bağlantı hatası: ${error.message}`));
    this.client.on("message", (topic, payload) => {
      if (!topic.startsWith(prefix)) return;
      const relativeTopic = topic.slice(prefix.length);
      this.resolveRequest(relativeTopic, payload);
      this.store.ingest(relativeTopic, payload);
    });
  }

  async permitJoin(seconds: number, routerId?: string): Promise<void> {
    await this.request("permit_join", {
      time: seconds,
      ...(routerId ? { device: routerId } : {})
    });
  }

  async addInstallCode(value: string): Promise<void> {
    await this.request("install_code/add", { value });
  }

  async reconfigureDevice(id: string): Promise<void> {
    await this.request("device/interview", { id }, 90_000);
    await this.request("device/configure", { id }, 90_000);
  }

  async scanTouchlink(): Promise<Array<{ ieeeAddress: string; channel: number }>> {
    const data = await this.request("touchlink/scan", {}, 75_000);
    const found = Array.isArray(data.found) ? data.found : [];
    return found.flatMap((entry) => {
      if (!entry || typeof entry !== "object" || Array.isArray(entry)) return [];
      const item = entry as Record<string, unknown>;
      return typeof item.ieee_address === "string" && Number.isInteger(item.channel)
        ? [{ ieeeAddress: item.ieee_address, channel: Number(item.channel) }]
        : [];
    });
  }

  async resetTouchlink(ieeeAddress: string, channel: number): Promise<void> {
    await this.request("touchlink/factory_reset", {
      ieee_address: ieeeAddress,
      channel
    }, 75_000);
  }

  async createGroup(name: string): Promise<void> {
    await this.request("group/add", { friendly_name: name });
  }

  async renameGroup(id: string, name: string): Promise<void> {
    await this.request("group/rename", {
      from: z2mGroupIdentifier(id),
      to: name,
      homeassistant_rename: true
    });
  }

  async removeGroup(id: string, force = false): Promise<void> {
    await this.request("group/remove", { id: z2mGroupIdentifier(id), force });
  }

  async setGroupMember(id: string, deviceId: string, add: boolean, endpoint?: number): Promise<void> {
    await this.request(
      `group/members/${add ? "add" : "remove"}`,
      { group: z2mGroupIdentifier(id), device: deviceId, ...(endpoint ? { endpoint } : {}) }
    );
  }

  async setGroup(id: string, command: JsonObject): Promise<void> {
    const group = this.store.getGroups().find((item) =>
      item.id === id || item.sourceName === id || item.id === `group-${z2mGroupIdentifier(id)}`
    );
    if (!group) throw new Error("Zigbee grubu bulunamadı.");
    await this.publish(`${this.config.baseTopic}/${group.sourceName}/set`, command);
  }

  async bindDevice(
    fromId: string,
    toId: string,
    bind: boolean,
    clusters?: string[],
    fromEndpoint?: number,
    toEndpoint?: number
  ): Promise<void> {
    const targetGroup = this.store.getGroups().find((group) =>
      group.id === toId || group.sourceName === toId
    );
    await this.request(`device/${bind ? "bind" : "unbind"}`, {
      from: fromId,
      to: targetGroup?.sourceName ?? toId,
      ...(fromEndpoint ? { from_endpoint: fromEndpoint } : {}),
      ...(toEndpoint && !targetGroup ? { to_endpoint: toEndpoint } : {}),
      ...(clusters?.length ? { clusters } : {})
    });
  }

  async groupScene(
    id: string,
    sceneId: number,
    action: "store" | "recall" | "remove",
    name?: string
  ): Promise<void> {
    const group = this.store.getGroups().find((item) => item.id === id || item.sourceName === id);
    if (!group) throw new Error("Zigbee grubu bulunamadı.");
    await this.publish(`${this.config.baseTopic}/${group.sourceName}/set`, {
      [`scene_${action}`]: action === "store" && name
        ? { ID: sceneId, name }
        : sceneId
    });
  }

  async networkMap(): Promise<ZigbeeNetworkMap> {
    const data = await this.request("networkmap", { type: "raw", routes: false }, 180_000);
    const raw = data.value;
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
      throw new Error("Zigbee2MQTT geçerli bir ağ haritası döndürmedi.");
    }
    const topology = raw as Record<string, unknown>;
    const rawNodes = Array.isArray(topology.nodes) ? topology.nodes : [];
    const rawLinks = Array.isArray(topology.links) ? topology.links : [];
    const nodes = rawNodes.flatMap((entry) => {
      if (!entry || typeof entry !== "object" || Array.isArray(entry)) return [];
      const node = entry as Record<string, unknown>;
      if (typeof node.ieeeAddr !== "string") return [];
      return [{
        id: node.ieeeAddr,
        name: typeof node.friendlyName === "string" ? node.friendlyName : node.ieeeAddr,
        type: typeof node.type === "string" ? node.type : "Unknown"
      }];
    });
    const links = rawLinks.flatMap((entry) => {
      if (!entry || typeof entry !== "object" || Array.isArray(entry)) return [];
      const link = entry as Record<string, unknown>;
      const source = link.source && typeof link.source === "object" && !Array.isArray(link.source)
        ? (link.source as Record<string, unknown>).ieeeAddr
        : link.sourceIeeeAddr;
      const target = link.target && typeof link.target === "object" && !Array.isArray(link.target)
        ? (link.target as Record<string, unknown>).ieeeAddr
        : link.targetIeeeAddr;
      const quality = Number(link.linkquality ?? link.lqi);
      return typeof source === "string" && typeof target === "string" && Number.isFinite(quality)
        ? [{ from: source, to: target, quality }]
        : [];
    });
    return { nodes, links };
  }

  async scheduleOta(id: string, enabled: boolean): Promise<void> {
    await this.request(
      `device/ota_update/${enabled ? "schedule" : "unschedule"}`,
      { id }
    );
  }

  async checkOta(id: string): Promise<OtaCheckResult> {
    const data = await this.request("device/ota_update/check", { id }, 180_000);
    const available = data.update_available === true || data.available === true;
    const currentVersion = Number(data.current_file_version ?? data.currentVersion);
    const availableVersion = Number(data.ota_file_version ?? data.availableVersion);
    return {
      available,
      ...(Number.isFinite(currentVersion) ? { currentVersion } : {}),
      ...(Number.isFinite(availableVersion) ? { availableVersion } : {})
    };
  }

  async setDeviceOptions(id: string, options: { transition?: number; debounce?: number; retain?: boolean }): Promise<void> {
    await this.request("device/options", { id, options });
  }

  async setDevice(id: string, command: JsonObject): Promise<void> {
    const device = this.store.getDevice(id);
    if (!device) throw new Error("Cihaz bulunamadı.");
    await this.publish(`${this.config.baseTopic}/${device.sourceName}/set`, command);
  }

  async renameDevice(id: string, name: string): Promise<void> {
    const device = this.store.getDevice(id);
    if (!device) throw new Error("Cihaz bulunamadı.");
    await this.request("device/rename", {
      from: device.sourceName,
      to: name,
      homeassistant_rename: true
    });
  }

  async removeDevice(id: string, force = false): Promise<void> {
    if (!this.store.getDevice(id)) throw new Error("Cihaz bulunamadı.");
    await this.request("device/remove", { id, force });
  }

  async prepareNetworkBackup(): Promise<PreparedNetworkBackup> {
    const data = await this.request("backup", {}, 120_000);
    const encoded = data.zip;
    if (typeof encoded !== "string" || !/^[A-Za-z0-9+/=]+$/.test(encoded)) {
      throw new Error("Zigbee2MQTT geçerli bir ağ yedeği döndürmedi.");
    }
    const body = Buffer.from(encoded, "base64");
    if (
      body.length < 4
      || body.length > 30 * 1024 * 1024
      || body[0] !== 0x50
      || body[1] !== 0x4b
    ) {
      throw new Error("Zigbee2MQTT ağ yedeği boyutu geçersiz.");
    }
    return { extension: "zip", contentType: "application/zip", body };
  }

  setHomeAssistantDiscovery(_enabled: boolean): void {
    // Shadow mode leaves discovery ownership with the external Zigbee2MQTT instance.
  }

  private resolveRequest(relativeTopic: string, payload: Buffer): void {
    if (!relativeTopic.startsWith("bridge/response/")) return;
    let response: Record<string, unknown>;
    try {
      const parsed = JSON.parse(payload.toString("utf8")) as unknown;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return;
      response = parsed as Record<string, unknown>;
    } catch {
      return;
    }
    const transaction = response.transaction;
    if (typeof transaction !== "string" && typeof transaction !== "number") return;
    const key = String(transaction);
    const pending = this.pendingRequests.get(key);
    if (!pending) return;
    this.pendingRequests.delete(key);
    clearTimeout(pending.timer);
    if (response.status !== "ok") {
      pending.reject(new Error(
        typeof response.error === "string" ? response.error : "Zigbee2MQTT işlemi başarısız."
      ));
      return;
    }
    pending.resolve(
      response.data && typeof response.data === "object" && !Array.isArray(response.data)
        ? response.data as JsonObject
        : {}
    );
  }

  private request(path: string, payload: JsonObject, timeoutMs = 30_000): Promise<JsonObject> {
    const client = this.client;
    if (!client?.connected) return Promise.reject(new Error("Zigbee ağına bağlı değil."));
    const transaction = `villa-${process.pid}-${++this.transaction}`;
    return new Promise<JsonObject>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pendingRequests.delete(transaction);
        reject(new Error("Zigbee2MQTT yanıtı zaman aşımına uğradı."));
      }, timeoutMs);
      timer.unref();
      this.pendingRequests.set(transaction, { resolve, reject, timer });
      client.publish(
        `${this.config.baseTopic}/bridge/request/${path}`,
        JSON.stringify({ ...payload, transaction }),
        { qos: 1 },
        (error) => {
          if (!error) return;
          clearTimeout(timer);
          this.pendingRequests.delete(transaction);
          reject(error);
        }
      );
    });
  }

  private async publish(topic: string, value: JsonObject): Promise<void> {
    const client = this.client;
    if (!client?.connected) throw new Error("Zigbee ağına bağlı değil.");
    await new Promise<void>((resolve, reject) => {
      client.publish(topic, JSON.stringify(value), { qos: 1 }, (error) => {
        if (error) reject(error);
        else resolve();
      });
    });
  }

  async stop(): Promise<void> {
    const client = this.client;
    this.client = null;
    this.store.setMqttConnected(false);
    for (const pending of this.pendingRequests.values()) {
      clearTimeout(pending.timer);
      pending.reject(new Error("Zigbee2MQTT bağlantısı kapatıldı."));
    }
    this.pendingRequests.clear();
    if (!client) return;
    await new Promise<void>((resolve, reject) => {
      client.end(false, {}, (error) => error ? reject(error) : resolve());
    });
  }
}
