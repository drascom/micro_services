import { readFile } from "node:fs/promises";
import { writeJsonAtomic } from "./atomic-file.js";
import { isValidTimeZone } from "./astronomy.js";
import { isValidLatitude, isValidLongitude } from "./sun.js";

/**
 * Evin konumu — yalnızca güneş tetikleyicisi için. Ürün çok evli, sabit koordinat gömülmez;
 * değer ya yapılandırmadan (`config/default.yaml` + `VILLA_BRIDGE_LATITUDE/LONGITUDE`) gelir
 * ya da kullanıcı panelden girer ve yapılandırmanın yanındaki `location.json`'a yazılır.
 */
export interface HomeLocation {
  latitude: number;
  longitude: number;
  /** Evin yerel saati; yaz/kış saati geçişleri IANA verisinden çözülür. */
  timeZone: string;
  /** Kullanıcının seçtiği yerin adı — panelde koordinat yerine bu gösterilir. */
  label?: string;
}

/** Yer adı sunum verisidir: kırpılır, 80 karakterle sınırlanır, boşsa hiç yazılmaz. */
const normalizeLabel = (value: unknown): string | undefined => {
  if (value === undefined || value === null) return undefined;
  if (typeof value !== "string") throw new Error("Konum adı metin olmalıdır.");
  const label = value.trim().slice(0, 80);
  return label || undefined;
};

/** Kullanıcı girdisini sayıya çevirir; ikisi birden verilmek zorunda. */
export const validateLocation = (
  value: unknown,
  options: { fallbackTimeZone?: string } = {}
): HomeLocation => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("Konum geçersiz.");
  }
  const candidate = value as Record<string, unknown>;
  for (const key of Object.keys(candidate)) {
    if (key !== "latitude" && key !== "longitude" && key !== "timeZone" && key !== "label") {
      throw new Error("Konumda bilinmeyen alan var.");
    }
  }
  const latitude = typeof candidate.latitude === "string"
    ? Number(candidate.latitude.trim())
    : candidate.latitude;
  const longitude = typeof candidate.longitude === "string"
    ? Number(candidate.longitude.trim())
    : candidate.longitude;
  if (!isValidLatitude(latitude)) throw new Error("Enlem -90 ile 90 arasında olmalıdır.");
  if (!isValidLongitude(longitude)) throw new Error("Boylam -180 ile 180 arasında olmalıdır.");
  const timeZone = candidate.timeZone ?? options.fallbackTimeZone;
  if (!isValidTimeZone(timeZone)) {
    throw new Error("Geçerli bir IANA saat dilimi seçilmelidir.");
  }
  const label = normalizeLabel(candidate.label);
  // Altı ondalık ≈ 11 cm; daha fazlası gürültü.
  return {
    latitude: Number(latitude.toFixed(6)),
    longitude: Number(longitude.toFixed(6)),
    timeZone,
    ...(label ? { label } : {})
  };
};

/** Konum dosyası — `aliases.ts` deseniyle atomik yazılır. */
export class LocationStore {
  private verificationRequired = false;

  constructor(
    private readonly path: string,
    private readonly fallbackTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
  ) {}

  get needsTimeZoneVerification(): boolean {
    return this.verificationRequired;
  }

  async get(): Promise<HomeLocation | null> {
    try {
      const raw = JSON.parse(await readFile(this.path, "utf8")) as Record<string, unknown>;
      this.verificationRequired = !isValidTimeZone(raw.timeZone);
      return validateLocation(raw, { fallbackTimeZone: this.fallbackTimeZone });
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
      throw error;
    }
  }

  async save(value: unknown): Promise<HomeLocation> {
    const location = validateLocation(value);
    await writeJsonAtomic(this.path, location, { mode: 0o600 });
    this.verificationRequired = false;
    return location;
  }
}
