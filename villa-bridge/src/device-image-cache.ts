import { mkdir, readdir, readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { writeFileAtomic, writeJsonAtomic } from "./atomic-file.js";

const safeModelPattern = /^[A-Za-z0-9._-]{1,64}$/;

const extensions = [
  { extension: "jpg", contentType: "image/jpeg" },
  { extension: "png", contentType: "image/png" }
] as const;

export interface DeviceImage {
  contentType: string;
  body: Buffer;
}

const maximumImageBytes = 2 * 1024 * 1024;

// Upstream'de gerçekten olmayan model: uzun süre yeniden denenmez.
const notFoundRetryMs = 24 * 60 * 60 * 1000;
// Ağ/zaman aşımı hatası geçicidir: internet dönünce görsel gelsin diye kısa aralıkla,
// üst üste başarısızlıkta katlanarak geri çekilerek yeniden denenir.
const transientRetryMs = 60_000;
const transientRetryCeilingMs = 30 * 60_000;

interface NegativeCacheEntry {
  retryAt: number;
  attempts: number;
  /** Upstream açıkça 404 dedi (ağ hatası değil): bu bilgi diske yazılır, yeniden başlatmayı aşar. */
  permanent: boolean;
}

/** Kalıcı "bu modelin görseli yok" listesi; önbellek dizininin içinde durur. */
const missingIndexFile = "missing.json";

interface DownloadResult {
  image: { extension: string; body: Buffer } | null;
  transient: boolean;
}

const contentTypeFor = (extension: string): string =>
  extensions.find((candidate) => candidate.extension === extension)?.contentType ?? "image/jpeg";

const extensionForContentType = (header: string | null): string | null => {
  const contentType = header?.split(";")[0]?.trim().toLowerCase() ?? "";
  if (!contentType.startsWith("image/")) return null;
  return extensions.find((candidate) => candidate.contentType === contentType)?.extension ?? null;
};

export class DeviceImageCache {
  private readonly missing = new Map<string, NegativeCacheEntry>();
  /** Diskte gerçekten görseli olan modeller — panelin "istek atmaya değer mi" sorusu bundan yanıtlanır. */
  private readonly present = new Set<string>();
  private warming = false;
  private loaded = false;

  constructor(
    private readonly directory: string,
    private readonly warmConcurrency = 2,
    private readonly warmPauseMs = 250,
    private readonly now: () => number = Date.now
  ) {}

  /**
   * Diskteki önbelleği bir kez tarar: hangi modelin görseli var, hangisi upstream'de yok.
   * Yeniden başlatmadan sonra bu bilgi olmasaydı panel her açılışta yok olan görselleri
   * yeniden isteyip 404 alırdı — konsol kirlenir, upstream boşuna yorulurdu.
   */
  async load(): Promise<void> {
    if (this.loaded) return;
    this.loaded = true;
    try {
      for (const name of await readdir(this.directory)) {
        const dot = name.lastIndexOf(".");
        if (dot <= 0) continue;
        const model = name.slice(0, dot);
        const extension = name.slice(dot + 1);
        if (!extensions.some((candidate) => candidate.extension === extension)) continue;
        if (safeModelPattern.test(model)) this.present.add(model);
      }
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
        console.error(`Cihaz görseli önbelleği taranamadı: ${String(error)}`);
      }
    }
    try {
      const raw: unknown = JSON.parse(await readFile(resolve(this.directory, missingIndexFile), "utf8"));
      if (typeof raw === "object" && raw !== null && !Array.isArray(raw)) {
        for (const [model, retryAt] of Object.entries(raw as Record<string, unknown>)) {
          if (!safeModelPattern.test(model) || this.present.has(model)) continue;
          if (typeof retryAt !== "number" || retryAt <= this.now()) continue;
          this.missing.set(model, { retryAt, attempts: 0, permanent: true });
        }
      }
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
        console.error(`Cihaz görseli yokluk listesi okunamadı: ${String(error)}`);
      }
    }
  }

  /**
   * Panel için tek soru: bu model için `<img>` kurulsun mu?
   * `true` görsel var, `false` upstream'de yok (istek hiç atılmasın), `null` henüz bilinmiyor
   * (istek atılır — mevcut davranış korunur, ilk denemeyi bir şey engellemez).
   */
  availability(model: string | null | undefined): boolean | null {
    const key = model?.trim();
    if (!key || !safeModelPattern.test(key)) return null;
    if (this.present.has(key)) return true;
    const entry = this.missing.get(key);
    return entry?.permanent === true && entry.retryAt > this.now() ? false : null;
  }

  /**
   * Verilen modelleri arka planda önden indirir. Aynı anda tek tur çalışır,
   * upstream'i yormamak için küçük gruplar hâlinde ve aralarında bekleyerek ilerler.
   * Hatalar yutulur; ısıtma başarısız olsa da servis etkilenmez.
   */
  async warm(models: string[]): Promise<void> {
    if (this.warming) return;
    this.warming = true;
    try {
      const pending = [...new Set(models)].filter(
        (model) => safeModelPattern.test(model) && !this.blocked(model)
      );
      for (let index = 0; index < pending.length; index += this.warmConcurrency) {
        const batch = pending.slice(index, index + this.warmConcurrency);
        await Promise.all(batch.map(async (model) => {
          try {
            await this.get(model);
          } catch (error) {
            console.error(`Cihaz görseli ön belleğe alınamadı (${model}): ${String(error)}`);
          }
        }));
        if (index + this.warmConcurrency < pending.length) {
          await new Promise((resolveDelay) => setTimeout(resolveDelay, this.warmPauseMs));
        }
      }
    } finally {
      this.warming = false;
    }
  }

  async get(model: string): Promise<DeviceImage | null> {
    if (!safeModelPattern.test(model) || model === "." || model === "..") return null;
    const cached = await this.readFromDisk(model);
    if (cached) {
      this.present.add(model);
      return cached;
    }
    if (this.blocked(model)) return null;
    const { image, transient } = await this.download(model);
    if (!image) {
      this.rememberFailure(model, transient);
      return null;
    }
    this.present.add(model);
    if (this.missing.delete(model)) void this.persistMissing();
    await this.writeToDisk(model, image.extension, image.body);
    return { contentType: contentTypeFor(image.extension), body: image.body };
  }

  /** Negatif önbellekteki model, yeniden deneme zamanı gelene kadar ağa çıkarılmaz. */
  private blocked(model: string): boolean {
    const entry = this.missing.get(model);
    return entry !== undefined && entry.retryAt > this.now();
  }

  private rememberFailure(model: string, transient: boolean): void {
    if (!transient) {
      this.missing.set(model, { retryAt: this.now() + notFoundRetryMs, attempts: 0, permanent: true });
      // Yalnız kalıcı yokluk diske yazılır. Geçici hata (internet yok, zaman aşımı) yazılmaz:
      // yeniden başlatmadan sonra o modele bir şans daha verilsin.
      void this.persistMissing();
      return;
    }
    const attempts = (this.missing.get(model)?.attempts ?? 0) + 1;
    const delay = Math.min(transientRetryMs * 2 ** (attempts - 1), transientRetryCeilingMs);
    this.missing.set(model, { retryAt: this.now() + delay, attempts, permanent: false });
  }

  private async persistMissing(): Promise<void> {
    const now = this.now();
    const index: Record<string, number> = {};
    for (const [model, entry] of this.missing) {
      if (entry.permanent && entry.retryAt > now) index[model] = entry.retryAt;
    }
    try {
      await mkdir(this.directory, { recursive: true });
      await writeJsonAtomic(resolve(this.directory, missingIndexFile), index, { mode: 0o600 });
    } catch (error) {
      console.error(`Cihaz görseli yokluk listesi yazılamadı: ${String(error)}`);
    }
  }

  private async readFromDisk(model: string): Promise<DeviceImage | null> {
    for (const { extension, contentType } of extensions) {
      try {
        return { contentType, body: await readFile(resolve(this.directory, `${model}.${extension}`)) };
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
          console.error(`Cihaz görseli önbellekten okunamadı: ${String(error)}`);
        }
      }
    }
    return null;
  }

  private async download(model: string): Promise<DownloadResult> {
    let transient = false;
    for (const { extension } of extensions) {
      try {
        const response = await fetch(
          `https://www.zigbee2mqtt.io/images/devices/${encodeURIComponent(model)}.${extension}`,
          { signal: AbortSignal.timeout(5000) }
        );
        if (!response.ok) {
          if (response.status !== 404) transient = true;
          continue;
        }
        const actualExtension = extensionForContentType(response.headers.get("content-type"));
        if (!actualExtension) {
          // Genelde captive portal / vekil sunucu araya girmiştir: geçici say, sonra tekrar dene.
          transient = true;
          console.error(`Cihaz görseli resim değil (${model}.${extension}): ${response.headers.get("content-type") ?? "bilinmiyor"}`);
          continue;
        }
        const body = Buffer.from(await response.arrayBuffer());
        if (body.byteLength === 0 || body.byteLength > maximumImageBytes) {
          console.error(`Cihaz görseli boyutu geçersiz (${model}.${extension}): ${body.byteLength} bayt`);
          continue;
        }
        return { image: { extension: actualExtension, body }, transient: false };
      } catch (error) {
        transient = true;
        console.error(`Cihaz görseli indirilemedi (${model}.${extension}): ${String(error)}`);
      }
    }
    return { image: null, transient };
  }

  private async writeToDisk(model: string, extension: string, body: Buffer): Promise<void> {
    try {
      await mkdir(this.directory, { recursive: true });
      const path = resolve(this.directory, `${model}.${extension}`);
      await writeFileAtomic(path, body, { mode: 0o600 });
    } catch (error) {
      console.error(`Cihaz görseli önbelleğe yazılamadı: ${String(error)}`);
    }
  }
}
