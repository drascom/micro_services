import type { DeviceDeparture } from "./device-departures.js";
import type {
  OtaCheckResult,
  PreparedNetworkBackup,
  ZigbeeNetworkMap,
  ZigbeeSource
} from "./source.js";
import type { JsonObject } from "./types.js";

/**
 * Kurulum kipinin Zigbee kaynağı: **hiçbir şey yapmaz**.
 *
 * Koordinatör adresi girilmeden panel açılabilsin diye vardır. `DirectZigbeeSource`
 * bu durumda hiç kurulmaz — yanlış/yer tutucu ağ ayarlarıyla bir koordinatöre
 * bağlanmak, var olan bir Zigbee ağını yeniden kurup tüm cihazları öksüz bırakabilir.
 * Bu sınıf o yolu yapısal olarak kapatır: burada koordinatöre ulaşan tek bir çağrı
 * yoktur, her işlem aynı hatayla reddedilir.
 */
export class SetupZigbeeSource implements ZigbeeSource {
  private readonly refuse = (): never => {
    throw new Error("Kurulum tamamlanmadı: önce kurulum sihirbazından koordinatör adresini girin.");
  };

  start(): void {
    // Kurulum kipinde başlatılacak bir şey yok; panel yine de açılır.
  }

  async stop(): Promise<void> {
    // Açılmamış bir oturum kapatılmaz.
  }

  async permitJoin(_seconds: number, _routerId?: string): Promise<void> {
    this.refuse();
  }

  async addInstallCode(_value: string): Promise<void> {
    this.refuse();
  }

  async reconfigureDevice(_id: string): Promise<void> {
    this.refuse();
  }

  async scanTouchlink(): Promise<Array<{ ieeeAddress: string; channel: number }>> {
    return this.refuse();
  }

  async resetTouchlink(_ieeeAddress: string, _channel: number): Promise<void> {
    this.refuse();
  }

  async createGroup(_name: string): Promise<void> {
    this.refuse();
  }

  async renameGroup(_id: string, _name: string): Promise<void> {
    this.refuse();
  }

  async removeGroup(_id: string, _force?: boolean): Promise<void> {
    this.refuse();
  }

  async setGroupMember(
    _id: string,
    _deviceId: string,
    _add: boolean,
    _endpoint?: number
  ): Promise<void> {
    this.refuse();
  }

  async setGroup(_id: string, _command: JsonObject): Promise<void> {
    this.refuse();
  }

  async bindDevice(
    _fromId: string,
    _toId: string,
    _bind: boolean,
    _clusters?: string[],
    _fromEndpoint?: number,
    _toEndpoint?: number
  ): Promise<void> {
    this.refuse();
  }

  async groupScene(
    _id: string,
    _sceneId: number,
    _action: "store" | "recall" | "remove",
    _name?: string
  ): Promise<void> {
    this.refuse();
  }

  async networkMap(): Promise<ZigbeeNetworkMap> {
    return { nodes: [], links: [] };
  }

  async checkOta(_id: string): Promise<OtaCheckResult> {
    return this.refuse();
  }

  async scheduleOta(_id: string, _enabled: boolean): Promise<void> {
    this.refuse();
  }

  async setDeviceOptions(
    _id: string,
    _options: { transition?: number; debounce?: number; retain?: boolean }
  ): Promise<void> {
    this.refuse();
  }

  async setDevice(_id: string, _command: JsonObject): Promise<void> {
    this.refuse();
  }

  async renameDevice(_id: string, _name: string): Promise<void> {
    this.refuse();
  }

  async removeDevice(_id: string, _force?: boolean): Promise<void> {
    this.refuse();
  }

  async prepareNetworkBackup(): Promise<PreparedNetworkBackup | null> {
    return null;
  }

  setHomeAssistantDiscovery(_enabled: boolean): void {
    // Kurulum kipinde yayınlanacak cihaz yok; ayar dosyaya yazılır, burada iş yoktur.
  }

  recentDeparture(_id: string): DeviceDeparture | undefined {
    return undefined;
  }

  recentDepartures(): DeviceDeparture[] {
    return [];
  }
}
