import { readFile } from "node:fs/promises";
import { writeJsonAtomic } from "./atomic-file.js";
import type { JsonObject } from "./types.js";

/**
 * Hava durumu tek kaynaktan gelir: konumu da veriyi de SUNUCU tutar. Eskiden her tarayıcı
 * Open-Meteo'ya kendisi giderdi ve seçili şehir `localStorage`ta durduğu için aynı evdeki iki
 * ekran farklı hava gösterebiliyordu. Artık panel yalnız bağlı olduğu sunucuya sorar; tablet
 * gömülü çekirdekle çalışırken de aynı yol işler, panelde kip ayrımı yoktur.
 */
export interface WeatherLocation {
  id: string;
  name: string;
  country: string;
  admin1: string;
  timeZone: string;
  latitude: number;
  longitude: number;
}

export interface WeatherSnapshot {
  location: WeatherLocation | null;
  data: JsonObject | null;
  /** Verinin çekildiği an (epoch ms); hiç çekilmediyse 0. */
  updatedAt: number;
  /** Son denemenin hatası; veri yine de sunulur (son bilinen değer). */
  error: string | null;
}

const text = (value: unknown, limit = 80): string =>
  typeof value === "string" ? value.trim().slice(0, limit) : "";

const coordinate = (value: unknown, limit: number, label: string): number => {
  const parsed = typeof value === "string" ? Number(value.trim()) : value;
  if (typeof parsed !== "number" || !Number.isFinite(parsed) || Math.abs(parsed) > limit) {
    throw new Error(`${label} geçersiz.`);
  }
  // Altı ondalık ≈ 11 cm; fazlası gürültü.
  return Number(parsed.toFixed(6));
};

/** Panelden ya da Open-Meteo aramasından gelen ham kaydı tek biçime indirir. */
export const validateWeatherLocation = (value: unknown): WeatherLocation => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("Hava durumu konumu geçersiz.");
  }
  const candidate = value as Record<string, unknown>;
  const latitude = coordinate(candidate.latitude, 90, "Enlem");
  const longitude = coordinate(candidate.longitude, 180, "Boylam");
  const name = text(candidate.name);
  if (!name) throw new Error("Hava durumu konumunun adı gereklidir.");
  return {
    id: text(candidate.id) || `${latitude},${longitude}`,
    name,
    country: text(candidate.country),
    admin1: text(candidate.admin1),
    // Open-Meteo arama sonucunda alan adı `timezone`; panelde `timeZone`. İkisi de kabul edilir.
    timeZone: text(candidate.timeZone) || text(candidate.timezone),
    latitude,
    longitude
  };
};

/** Konum dosyası — `aliases.ts`/`location.ts` deseniyle atomik yazılır. */
export class WeatherLocationStore {
  constructor(private readonly path: string) {}

  async get(): Promise<WeatherLocation | null> {
    try {
      return validateWeatherLocation(JSON.parse(await readFile(this.path, "utf8")));
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
      throw error;
    }
  }

  async save(value: unknown): Promise<WeatherLocation> {
    const location = validateWeatherLocation(value);
    await writeJsonAtomic(this.path, location);
    return location;
  }
}

export interface WeatherServiceOptions {
  store: WeatherLocationStore;
  /** Tazeleme aralığı; Open-Meteo saatlik çözünürlükte olduğu için varsayılan 10 dakika. */
  refreshIntervalMs?: number;
  requestTimeoutMs?: number;
  onError?: (message: string) => void;
}

const forecastUrl = "https://api.open-meteo.com/v1/forecast";
const geocodingUrl = "https://geocoding-api.open-meteo.com/v1/search";

export class WeatherService {
  private location: WeatherLocation | null = null;
  private data: JsonObject | null = null;
  private updatedAt = 0;
  private error: string | null = null;
  private timer: NodeJS.Timeout | null = null;
  private running: Promise<void> | null = null;
  private readonly refreshIntervalMs: number;
  private readonly requestTimeoutMs: number;

  constructor(private readonly options: WeatherServiceOptions) {
    this.refreshIntervalMs = options.refreshIntervalMs ?? 10 * 60_000;
    this.requestTimeoutMs = options.requestTimeoutMs ?? 10_000;
  }

  /** Açılış: kayıtlı konum okunur, varsa bir kez çekilir ve düzenli tazeleme kurulur. */
  async start(): Promise<void> {
    try {
      this.location = await this.options.store.get();
    } catch (error) {
      this.report(`Hava durumu konumu okunamadı: ${String(error)}`);
    }
    // Konum tanımlı değilse dışarıya hiç çıkılmaz; zamanlayıcı yine kurulur, konum
    // sonradan seçildiğinde `setLocation` zaten hemen çeker.
    if (this.location) await this.refresh();
    this.timer = setInterval(() => {
      void this.refresh();
    }, this.refreshIntervalMs);
    this.timer.unref();
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  snapshot(): WeatherSnapshot {
    return {
      location: this.location,
      data: this.data,
      updatedAt: this.updatedAt,
      error: this.error
    };
  }

  async setLocation(value: unknown): Promise<WeatherSnapshot> {
    const location = await this.options.store.save(value);
    this.location = location;
    // Yeni şehir eski veriyle gösterilmesin: kayıt sıfırlanır, veri hemen çekilir.
    this.data = null;
    this.updatedAt = 0;
    this.error = null;
    await this.refresh();
    return this.snapshot();
  }

  /** İnternet yoksa son bilinen veri korunur; hata yalnız not olarak taşınır. */
  async refresh(): Promise<void> {
    if (!this.location) return;
    if (this.running) return this.running;
    this.running = this.fetchForecast(this.location)
      .then((data) => {
        this.data = data;
        this.updatedAt = Date.now();
        this.error = null;
      })
      .catch((error: unknown) => {
        const message = error instanceof Error ? error.message : String(error);
        this.error = message;
        this.report(`Hava durumu verisi alınamadı: ${message}`);
      })
      .finally(() => {
        this.running = null;
      });
    return this.running;
  }

  /** Konum arama sunucudan geçer: istemci hiçbir dış servise doğrudan çıkmaz. */
  async search(query: unknown, language: unknown): Promise<WeatherLocation[]> {
    const name = text(query, 120);
    if (name.length < 2) return [];
    const parameters = new URLSearchParams({
      name,
      count: "5",
      language: /^[a-z]{2}$/.test(String(language ?? "")) ? String(language) : "en",
      format: "json"
    });
    const body = await this.request(`${geocodingUrl}?${parameters}`, "Konum araması");
    const results = Array.isArray((body as { results?: unknown }).results)
      ? (body as { results: unknown[] }).results
      : [];
    return results.flatMap((result) => {
      try {
        return [validateWeatherLocation(result)];
      } catch {
        return [];
      }
    });
  }

  private async fetchForecast(location: WeatherLocation): Promise<JsonObject> {
    const parameters = new URLSearchParams({
      latitude: String(location.latitude),
      longitude: String(location.longitude),
      current: "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,is_day,wind_speed_10m",
      hourly: "temperature_2m,weather_code,is_day",
      daily: "weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset",
      timezone: "auto",
      forecast_days: "4"
    });
    const body = await this.request(`${forecastUrl}?${parameters}`, "Hava durumu servisi");
    if (typeof (body as { current?: unknown }).current !== "object") {
      throw new Error("Hava durumu yanıtı eksik.");
    }
    return body;
  }

  private async request(url: string, label: string): Promise<JsonObject> {
    const response = await fetch(url, {
      signal: AbortSignal.timeout(this.requestTimeoutMs),
      headers: { accept: "application/json" }
    });
    if (!response.ok) throw new Error(`${label} yanıt vermedi (HTTP ${response.status}).`);
    const body = (await response.json()) as unknown;
    if (typeof body !== "object" || body === null || Array.isArray(body)) {
      throw new Error(`${label} beklenmeyen bir yanıt döndürdü.`);
    }
    return body as JsonObject;
  }

  private report(message: string): void {
    console.warn(message);
    this.options.onError?.(message);
  }
}
