import { readFile } from "node:fs/promises";
import { writeJsonAtomic } from "./atomic-file.js";

/**
 * Dünya saati şehirleri evin ayarıdır, cihazın değil: ürün duvara asılan tablet, her ekranda
 * ayrı şehir listesi tutmanın anlamı yok. Eskiden liste `localStorage`ta durduğu için aynı evdeki
 * iki panel farklı şehirler gösteriyordu. Artık `weather.ts` konum deposuyla aynı desen: liste
 * sunucuda, yapılandırmanın yanındaki dosyada durur; panel yalnız okur ve yönetici yazar.
 */
export interface WorldClockZone {
  id: string;
  name: string;
  country: string;
  admin1: string;
  timeZone: string;
  /** Hazır şehirlerin çeviri anahtarı (ör. `clockLondon`); aramadan gelen şehirde boştur. */
  label: string;
}

/** Hub'da ve pencerede okunabilir kalması için üst sınır; panel de aynı sayıyı uygular. */
export const worldClockZoneLimit = 8;

const text = (value: unknown, limit = 80): string =>
  typeof value === "string" ? value.trim().slice(0, limit) : "";

const knownTimeZone = (zone: string): boolean => {
  try {
    new Intl.DateTimeFormat("en", { timeZone: zone });
    return true;
  } catch {
    return false;
  }
};

/** Panelden gelen ham listeyi tek biçime indirir; aynı şehir iki kez listelenmez. */
export const validateWorldClockZones = (value: unknown): WorldClockZone[] => {
  if (!Array.isArray(value)) throw new Error("Dünya saati şehir listesi geçersiz.");
  const zones: WorldClockZone[] = [];
  const seen = new Set<string>();
  for (const item of value) {
    if (typeof item !== "object" || item === null || Array.isArray(item)) {
      throw new Error("Dünya saati şehri geçersiz.");
    }
    const candidate = item as Record<string, unknown>;
    // Open-Meteo arama sonucunda alan adı `timezone`; panelde `timeZone`. İkisi de kabul edilir.
    const timeZone = text(candidate.timeZone) || text(candidate.timezone);
    if (!timeZone || !knownTimeZone(timeZone)) {
      throw new Error(`Saat dilimi geçersiz: ${timeZone || "(boş)"}.`);
    }
    const name = text(candidate.name);
    if (!name) throw new Error("Dünya saati şehrinin adı gereklidir.");
    const id = text(candidate.id) || `${name}-${timeZone}`;
    if (seen.has(id)) continue;
    seen.add(id);
    zones.push({
      id,
      name,
      country: text(candidate.country),
      admin1: text(candidate.admin1),
      timeZone,
      label: text(candidate.label)
    });
  }
  if (zones.length > worldClockZoneLimit) {
    throw new Error(`En fazla ${worldClockZoneLimit} şehir listelenebilir.`);
  }
  return zones;
};

/** Şehir listesi dosyası — `weather.ts`/`aliases.ts` deseniyle atomik yazılır. */
export class WorldClockStore {
  constructor(private readonly path: string) {}

  /**
   * Dosya yoksa `null` döner: "liste tanımlı değil" ile "kullanıcı hepsini sildi" (`[]`) ayrı
   * durumlardır — göç yalnız birincisinde çalışır, ikincisinde cihazdaki eski liste geri gelmez.
   */
  async get(): Promise<WorldClockZone[] | null> {
    try {
      return validateWorldClockZones(JSON.parse(await readFile(this.path, "utf8")));
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
      throw error;
    }
  }

  async save(value: unknown): Promise<WorldClockZone[]> {
    const zones = validateWorldClockZones(value);
    await writeJsonAtomic(this.path, zones);
    return zones;
  }
}
