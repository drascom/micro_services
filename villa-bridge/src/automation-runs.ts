import { appendFile, readFile } from "node:fs/promises";
import { writeFileAtomic } from "./atomic-file.js";
import type { JsonScalar } from "./types.js";

/**
 * Otomasyon çalışma günlüğü — "neden çalıştı / neden çalışmadı".
 *
 * **JSONL**, tam JSON yeniden yazma değil: ölçüm (HANDOFF 2026-08-04 §6) ekleme yolunun 200×
 * daha ucuz olduğunu gösterdi (0,015 ms vs 0,275 ms, yazma başına ~120 B vs 24,8 KB). Dosya
 * tavana ulaşınca son `maxRecords` kayıt tutulacak şekilde bir kez atomik olarak sıkıştırılır.
 *
 * Kalıcılık hatası **sessiz kalmaz**: yazma başarısız olursa `onError` çağrılır ve çağıran onu
 * `RecentErrorLog`'a düşürür (HANDOFF §7).
 */

/** `blocked`: kural eşleşti ama koşul/konum/veri yüzünden hiç çalıştırılmadı. */
export type AutomationRunOutcome = "ok" | "failed" | "busy" | "skipped" | "blocked";

export interface AutomationRunTriggerInfo {
  kind: "device" | "time" | "sun" | "manual";
  /** Cihaz tetikleyicisinde IEEE adresi. */
  deviceId?: string;
  property?: string;
  value?: JsonScalar;
  /**
   * §2.1 — süreli tetikleyici ateşlediyse, kanalın kaç saniyedir hedefte olduğu. Günlüğü okuyan
   * insan "neden şimdi çalıştı" sorusunun cevabını burada görür ("63 saniyedir açık").
   */
  heldSeconds?: number;
}

export interface AutomationRunConditionResult {
  type: string;
  ok: boolean;
  /** Sağlanmadıysa Türkçe sebep. */
  reason?: string;
}

export interface AutomationRunActionResult {
  type: string;
  /** Cihaz/grup kimliği ya da bekleme süresi — insanın tanıyacağı hedef. */
  target?: string;
  ok: boolean;
  error?: string;
}

export interface AutomationRunRecord {
  at: string;
  automationId: string;
  automationName: string;
  outcome: AutomationRunOutcome;
  /**
   * Makine tarafından okunabilir sebep kodu; arayüz bunu çevirir.
   * `busy` · `noMatchingAction` · `conditionFalse` · `locationMissing` · `sunUnavailable` ·
   * `nonNumericValue` · `stopped` · `unsupportedAction` · `autoOffCompleted` ·
   * `autoOffCanceled` · `autoOffFailed`
   */
  reason?: string;
  /** Türkçe açıklama — günlüğü ham okuyan insan için. */
  detail?: string;
  trigger?: AutomationRunTriggerInfo;
  conditions?: AutomationRunConditionResult[];
  actions?: AutomationRunActionResult[];
}

/** Tek satır tavanı; bozuk/şişkin bir kayıt dosyayı ele geçirmesin. */
const maxRecordLength = 4_000;

export interface AutomationRunLogOptions {
  /** Dosyada tutulacak kayıt sayısı. */
  maxRecords?: number;
  /** Kalıcılık hatası — sessiz kalmaz. */
  onError?: (message: string) => void;
}

const serialize = (record: AutomationRunRecord): string => {
  const line = JSON.stringify(record);
  if (line.length <= maxRecordLength) return line;
  return JSON.stringify({
    ...record,
    detail: (record.detail ?? "").slice(0, 200),
    conditions: undefined,
    actions: undefined
  });
};

export class AutomationRunLog {
  private readonly maxRecords: number;
  private readonly onError: (message: string) => void;
  /** Yazmalar sıraya girer; JSONL'de satır karışması olmasın. */
  private chain: Promise<void> = Promise.resolve();
  /** Dosyadaki satır sayısı; ilk yazmada diskten sayılır. */
  private lineCount: number | null = null;

  constructor(private readonly path: string, options: AutomationRunLogOptions = {}) {
    this.maxRecords = options.maxRecords ?? 500;
    this.onError = options.onError ?? ((message) => console.error(message));
  }

  /** Ateşle-unut: motorun akışını bloke etmez, ama hata sessiz kalmaz. */
  append(record: AutomationRunRecord): void {
    this.chain = this.chain
      .then(() => this.write(record))
      .catch((error) => {
        this.onError(`Otomasyon çalışma günlüğü yazılamadı: ${String(error)}`);
      });
  }

  /** Testlerin ve kapanışın bekleyebilmesi için. */
  async flush(): Promise<void> {
    await this.chain;
  }

  private async write(record: AutomationRunRecord): Promise<void> {
    await appendFile(this.path, `${serialize(record)}\n`, { mode: 0o600 });
    if (this.lineCount === null) this.lineCount = await this.countLines();
    else this.lineCount += 1;
    // Tavanın %40 üstüne çıkınca bir kez sıkıştır; her yazmada dosya taramamak için pay bırakılır.
    if (this.lineCount > Math.ceil(this.maxRecords * 1.4)) await this.compact();
  }

  private async countLines(): Promise<number> {
    return (await this.lines()).length;
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
  async read(options: { limit?: number; automationId?: string } = {}): Promise<AutomationRunRecord[]> {
    const limit = Math.min(Math.max(options.limit ?? 100, 1), 500);
    const automationId = options.automationId?.trim().toLowerCase();
    const records: AutomationRunRecord[] = [];
    const lines = await this.lines();
    for (let index = lines.length - 1; index >= 0 && records.length < limit; index -= 1) {
      let parsed: AutomationRunRecord;
      try {
        parsed = JSON.parse(lines[index] as string) as AutomationRunRecord;
      } catch {
        continue;
      }
      if (automationId && parsed.automationId !== automationId) continue;
      records.push(parsed);
    }
    return records;
  }
}
