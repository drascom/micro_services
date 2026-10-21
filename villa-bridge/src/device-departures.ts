/**
 * Ağdan düşen cihazın kısa süreli hafızası.
 *
 * Kurulum sırasında cihaz görüşmeyi tamamlayıp saniyeler sonra ağdan ayrılabiliyor. Cihaz
 * listeden düştüğü için kurulum uçları düz "bulunamadı" veriyordu ve kullanıcı ekranda hiçbir
 * sebep göremiyordu. Burada tutulan kayıt, "hiç tanımadım" ile "az önce düştü" arasındaki farkı
 * uca taşır. Kayıt bilerek yalnız bellektedir: bilgi dakikalar ölçeğinde anlamlıdır, kalıcı
 * durum değildir.
 */

export type DeviceDepartureReason = "left" | "removed";

export interface DeviceDeparture {
  id: string;
  reason: DeviceDepartureReason;
  at: number;
  /** Olay anındaki dost isim; kalıcı günlüğün okunur satır yazabilmesi için taşınır. */
  name?: string;
}

/** Beş dakika: kullanıcının aynı oturumda tekrar denemesini kapsayacak kadar uzun, eskimiş bilgiyi taşımayacak kadar kısa. */
export const DEVICE_DEPARTURE_TTL_MS = 5 * 60_000;

export class DeviceDepartureLog {
  private readonly entries = new Map<string, DeviceDeparture>();

  constructor(
    private readonly ttlMs: number = DEVICE_DEPARTURE_TTL_MS,
    private readonly limit: number = 64,
    /**
     * Kabul edilen her kayıt için çağrılır — bastırılanlar için çağrılmaz. Kalıcı ağ günlüğü
     * buradan beslenir: aynı olay, aynı bastırma kuralı, iki ayrı ömür.
     */
    private readonly onRecord?: (entry: DeviceDeparture) => void
  ) {}

  record(id: string, reason: DeviceDepartureReason, now = Date.now(), name?: string): void {
    const key = id.toLowerCase();
    this.prune(now);
    const previous = this.entries.get(key);
    // Silme kullanıcının kendi eylemidir; onun ardından gelen kendiliğinden ayrılma olayı
    // sebebi "kayboldu"ya düşürmemeli — kullanıcıya söylenecek doğru şey "az önce sildiniz".
    if (previous && previous.reason === "removed" && reason === "left") return;
    this.entries.delete(key);
    const entry: DeviceDeparture = { id: key, reason, at: now, ...(name ? { name } : {}) };
    this.entries.set(key, entry);
    this.onRecord?.(entry);
    while (this.entries.size > this.limit) {
      const oldest = this.entries.keys().next();
      if (oldest.done) break;
      this.entries.delete(oldest.value);
    }
  }

  get(id: string, now = Date.now()): DeviceDeparture | undefined {
    this.prune(now);
    return this.entries.get(id.toLowerCase());
  }

  list(now = Date.now()): DeviceDeparture[] {
    this.prune(now);
    return [...this.entries.values()];
  }

  forget(id: string): void {
    this.entries.delete(id.toLowerCase());
  }

  private prune(now: number): void {
    for (const [key, entry] of this.entries) {
      if (now - entry.at >= this.ttlMs) this.entries.delete(key);
    }
  }
}

export type DeviceMissingCode = "DEVICE_LEFT" | "DEVICE_REMOVED" | "DEVICE_UNKNOWN";

export interface DeviceMissingResponse {
  ok: false;
  code: DeviceMissingCode;
  error: string;
  departedAt?: string;
}

/**
 * Kurulum uçlarının ortak "cihaz yok" yanıtı. HTTP kodu 404 olarak kalır; ayrımı makine kodu
 * taşır, böylece panel sebebi söyleyebilir, eski çağıranlar da bozulmaz.
 */
export function deviceMissingResponse(departure?: DeviceDeparture): DeviceMissingResponse {
  if (departure?.reason === "removed") {
    return {
      ok: false,
      code: "DEVICE_REMOVED",
      error: "Bu cihaz az önce kaldırıldı. Yeniden eklemek için eşleştirmeyi baştan başlatın.",
      departedAt: new Date(departure.at).toISOString()
    };
  }
  if (departure) {
    return {
      ok: false,
      code: "DEVICE_LEFT",
      error: "Cihaz ağdan ayrıldığı için ayar kaydedilemedi. Cihazı yeniden eşleştirip tekrar deneyin.",
      departedAt: new Date(departure.at).toISOString()
    };
  }
  return { ok: false, code: "DEVICE_UNKNOWN", error: "Cihaz bulunamadı." };
}
