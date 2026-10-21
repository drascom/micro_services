import { appendFile, readFile } from "node:fs/promises";
import { writeFileAtomic } from "./atomic-file.js";

/**
 * Cihaz ağ üyeliği günlüğü — "hangi cihaz ne zaman katıldı, düştü, silindi".
 *
 * `DeviceDepartureLog` aynı olayları bellekte beş dakika tutar; o kayıt kurulum akışının
 * "az önce düştü" cümlesi içindir. Burada tutulan kayıt ise **kalıcıdır**: gece yarısı üç kez
 * katılıp düşen bir cihazın izi, sabah panelden okunabilsin diye. İkisi aynı olaydan beslenir,
 * biri diğerinin yerine geçmez.
 *
 * Dosya deseni `automation-runs.jsonl` ile aynıdır: olay başına ekleme, tavan aşılınca tek
 * seferlik atomik sıkıştırma (HANDOFF 2026-08-04 §6). Kalıcılık hatası sessiz kalmaz.
 *
 * **Çevrimdışı/çevrimiçi geçişleri buraya yazılmaz.** Pilli sensörler günde onlarca satır üretir,
 * gerçek katılma/ayrılma olayları aralarında kaybolurdu; o bilgi zaten cihaz tablosunda duruyor.
 */

/** `joined`: ağa katıldı · `left`: kendiliğinden ağdan ayrıldı · `removed`: kullanıcı sildi. */
export type DeviceNetworkEventReason = "joined" | "left" | "removed";

export interface DeviceNetworkEvent {
  /** ISO zaman damgası. */
  at: string;
  /** Değişmez IEEE adresi — kaydın gerçek kimliği. */
  id: string;
  /** O anki dost isim; yeni katılan cihazda henüz olmayabilir. */
  name?: string;
  reason: DeviceNetworkEventReason;
}

/**
 * Kaynakların bağlandığı uç. Kalıcı günlük olmayan kurulumlarda (ör. shadow mod) yerine
 * hiçbir şey verilmez ve kaynak sessizce çalışmaya devam eder.
 */
export interface DeviceNetworkEventSink {
  record(event: { id: string; reason: DeviceNetworkEventReason; name?: string }): void;
}

/** Tek satır tavanı; bozuk/şişkin bir kayıt dosyayı ele geçirmesin. */
const maxRecordLength = 1_000;

/** Dost isim uzunluğu tavanı; günlük satırı okunur kalsın. */
const maxNameLength = 120;

export interface DeviceNetworkEventLogOptions {
  /** Dosyada tutulacak kayıt sayısı. */
  maxRecords?: number;
  /** Kalıcılık hatası — sessiz kalmaz. */
  onError?: (message: string) => void;
  /** Testlerin sahte saati. */
  now?: () => number;
}

export class DeviceNetworkEventLog implements DeviceNetworkEventSink {
  private readonly maxRecords: number;
  private readonly onError: (message: string) => void;
  private readonly now: () => number;
  /** Yazmalar sıraya girer; JSONL'de satır karışması olmasın. */
  private chain: Promise<void> = Promise.resolve();
  /** Dosyadaki satır sayısı; ilk yazmada diskten sayılır. */
  private lineCount: number | null = null;

  constructor(private readonly path: string, options: DeviceNetworkEventLogOptions = {}) {
    // Son ~200 kayıt: "bu gece ne oldu" sorusunu fazlasıyla kapsar, dosyayı da şişirmez.
    this.maxRecords = options.maxRecords ?? 200;
    this.onError = options.onError ?? ((message) => console.error(message));
    this.now = options.now ?? (() => Date.now());
  }

  /** Ateşle-unut: Zigbee olay akışını bloke etmez, ama hata sessiz kalmaz. */
  record(event: { id: string; reason: DeviceNetworkEventReason; name?: string }): void {
    const name = event.name?.trim();
    const record: DeviceNetworkEvent = {
      at: new Date(this.now()).toISOString(),
      id: event.id.toLowerCase(),
      reason: event.reason,
      ...(name ? { name: name.slice(0, maxNameLength) } : {})
    };
    this.chain = this.chain
      .then(() => this.write(record))
      .catch((error) => {
        this.onError(`Cihaz ağ günlüğü yazılamadı: ${String(error)}`);
      });
  }

  /** Testlerin ve kapanışın bekleyebilmesi için. */
  async flush(): Promise<void> {
    await this.chain;
  }

  private async write(record: DeviceNetworkEvent): Promise<void> {
    const line = JSON.stringify(record);
    if (line.length > maxRecordLength) return;
    await appendFile(this.path, `${line}\n`, { mode: 0o600 });
    if (this.lineCount === null) this.lineCount = (await this.lines()).length;
    else this.lineCount += 1;
    // Tavanın %40 üstüne çıkınca bir kez sıkıştır; her yazmada dosya taramamak için pay bırakılır.
    if (this.lineCount > Math.ceil(this.maxRecords * 1.4)) await this.compact();
  }

  private async lines(): Promise<string[]> {
    try {
      return (await readFile(this.path, "utf8")).split("\n").filter((line) => line.trim() !== "");
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
      throw error;
    }
  }

  /** Son `maxRecords` kaydı bırakır; tam yeniden yazma yalnızca burada, seyrek olarak olur. */
  private async compact(): Promise<void> {
    const kept = (await this.lines()).slice(-this.maxRecords);
    await writeFileAtomic(this.path, kept.length === 0 ? "" : `${kept.join("\n")}\n`, { mode: 0o600 });
    this.lineCount = kept.length;
  }

  /** En yeniden eskiye. Bozuk satır atlanır — günlük okunamaz hale gelmesin. */
  async read(options: { limit?: number; deviceId?: string } = {}): Promise<DeviceNetworkEvent[]> {
    const limit = Math.min(Math.max(options.limit ?? this.maxRecords, 1), this.maxRecords);
    const deviceId = options.deviceId?.trim().toLowerCase();
    const events: DeviceNetworkEvent[] = [];
    const lines = await this.lines();
    for (let index = lines.length - 1; index >= 0 && events.length < limit; index -= 1) {
      let parsed: DeviceNetworkEvent;
      try {
        parsed = JSON.parse(lines[index] as string) as DeviceNetworkEvent;
      } catch {
        continue;
      }
      if (typeof parsed?.id !== "string" || typeof parsed?.at !== "string") continue;
      if (deviceId && parsed.id !== deviceId) continue;
      events.push(parsed);
    }
    return events;
  }
}
