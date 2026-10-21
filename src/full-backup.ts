import { createZipArchive, isZipArchive, readZipArchive, type ZipEntry } from "./zip-archive.js";

/**
 * BİRLEŞİK YEDEK — tek düğme, tek dosya.
 *
 * Panelde artık tek bir "Yedek al" vardır ve ürettiği dosya bir ZIP'tir. İçinde bölümler ayrı
 * durur, böylece geri yükleme hangisinin ne olduğunu bilir ve gelecekte yeni bir bölüm eklemek
 * eski dosyaları bozmaz:
 *
 *   manifest.json  — biçim, sürüm, üretim zamanı, hangi bölümler var
 *   home.json      — ev yapılandırması (`HomeBackup` zarfı, `/api/backup` ile aynı)
 *   zigbee.json    — Zigbee ağ yedeği (`ZigbeeNetworkBackup` zarfı, `/api/zigbee/backup` ile aynı)
 *
 * Bölüm zarfları bilerek DEĞİŞTİRİLMEDİ: aynı doğrulayıcılar (`validateZigbeeNetworkBackup`,
 * `HomeBackupService.plan`) hem birleşik dosyada hem de kullanıcının elindeki eski tekil
 * dosyalarda çalışır. Geriye uyum bundan gelir.
 *
 * GİZLİLİK: `zigbee.json` içindeki `coordinator_backup.json` Zigbee AĞ ANAHTARINI taşır. Bu
 * modül yedek içeriğini asla günlüğe, hataya ya da konsola yazmaz; hata metinleri yalnız
 * "hangi bölüm" düzeyinde konuşur.
 */

export const fullBackupFormat = "villa-bridge-backup";
export const fullBackupVersion = 1;

const manifestName = "manifest.json";
const homeName = "home.json";
const zigbeeName = "zigbee.json";
/** Shadow kipinde Zigbee2MQTT kendi ZIP'ini verir; olduğu gibi taşınır, geri yüklenmez. */
const zigbeeArchiveName = "zigbee-zigbee2mqtt.zip";

/**
 * Geri yüklemede dosya base64 gövdeyle gelir (%33 şişer) ve Fastify'ın gövde sınırı 30 MB'tır:
 * 20 MB'lık ham dosya ≈ 26,7 MB gövde, sınırın altında kalır. Kullanıcı bunu aşarsa açık bir
 * "çok büyük" hatası görür, sessiz bir gövde reddi değil.
 */
export const maximumFullBackupBytes = 20 * 1024 * 1024;

export interface FullBackupManifest {
  format: typeof fullBackupFormat;
  version: typeof fullBackupVersion;
  createdAt: string;
  sections: string[];
  /** Bir bölüm alınamadıysa sebebi; içerik değil, yalnız kısa açıklama. */
  notes?: Record<string, string>;
}

export interface FullBackupInput {
  home: unknown;
  zigbee?: unknown;
  /** Zigbee2MQTT'den gelen hazır arşiv (shadow kipi). */
  zigbeeArchive?: Buffer;
  /** Zigbee bölümü alınamadıysa kullanıcıya gösterilecek kısa sebep. */
  zigbeeNote?: string;
}

export const buildFullBackup = (input: FullBackupInput, now: Date = new Date()): Buffer => {
  const sections: string[] = ["home"];
  const entries: ZipEntry[] = [
    { name: homeName, data: Buffer.from(`${JSON.stringify(input.home)}\n`, "utf8") }
  ];
  if (input.zigbee !== undefined) {
    sections.push("zigbee");
    entries.push({ name: zigbeeName, data: Buffer.from(`${JSON.stringify(input.zigbee)}\n`, "utf8") });
  } else if (input.zigbeeArchive) {
    sections.push("zigbeeArchive");
    entries.push({ name: zigbeeArchiveName, data: input.zigbeeArchive });
  }
  const manifest: FullBackupManifest = {
    format: fullBackupFormat,
    version: fullBackupVersion,
    createdAt: now.toISOString(),
    sections,
    ...(input.zigbeeNote ? { notes: { zigbee: input.zigbeeNote } } : {})
  };
  return createZipArchive(
    [{ name: manifestName, data: Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`, "utf8") }, ...entries],
    now
  );
};

export interface ParsedBackupUpload {
  /** `combined` yeni ZIP; `home`/`zigbee` kullanıcının elindeki eski tekil JSON dosyalar. */
  kind: "combined" | "home" | "zigbee";
  createdAt: string | null;
  home?: unknown;
  zigbee?: unknown;
  /** ZIP birleşik yedekti ama Zigbee bölümü geri yüklenebilir biçimde değil. */
  zigbeeArchiveOnly: boolean;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const parseJsonEntry = (data: Buffer, section: string): unknown => {
  try {
    return JSON.parse(data.toString("utf8")) as unknown;
  } catch {
    throw new Error(`Yedek dosyasının "${section}" bölümü okunamadı.`);
  }
};

/**
 * Dosya tipini İÇERİĞİNDEN tanır — uzantıya ya da kullanıcının seçtiği düğmeye değil.
 * Böylece tek "Yedek yükle" akışı hem yeni birleşik ZIP'i hem de eski ev-only / zigbee-only
 * JSON dosyalarını kabul eder.
 */
export const parseBackupUpload = (data: Buffer): ParsedBackupUpload => {
  if (data.length === 0) throw new Error("Yedek dosyası boş.");
  if (data.length > maximumFullBackupBytes) throw new Error("Yedek dosyası çok büyük.");

  if (isZipArchive(data)) {
    const files = readZipArchive(data);
    const manifestEntry = files.get(manifestName);
    if (!manifestEntry) throw new Error("Bu dosya bir Villa Bridge yedeği değil.");
    const manifest = parseJsonEntry(manifestEntry, "manifest");
    if (
      !isRecord(manifest)
      || manifest.format !== fullBackupFormat
      || manifest.version !== fullBackupVersion
    ) {
      throw new Error("Yedek dosyası sürümü tanınmıyor; bu Villa Bridge sürümü onu açamıyor.");
    }
    const homeEntry = files.get(homeName);
    const zigbeeEntry = files.get(zigbeeName);
    if (!homeEntry && !zigbeeEntry) throw new Error("Yedek dosyasında geri yüklenecek bölüm yok.");
    return {
      kind: "combined",
      createdAt: typeof manifest.createdAt === "string" ? manifest.createdAt : null,
      ...(homeEntry ? { home: parseJsonEntry(homeEntry, "home") } : {}),
      ...(zigbeeEntry ? { zigbee: parseJsonEntry(zigbeeEntry, "zigbee") } : {}),
      zigbeeArchiveOnly: !zigbeeEntry && files.has(zigbeeArchiveName)
    };
  }

  const value = parseJsonEntry(data, "backup");
  if (!isRecord(value)) throw new Error("Bu dosya bir Villa Bridge yedeği değil.");
  const createdAt = typeof value.createdAt === "string" ? value.createdAt : null;
  if (value.format === "villa-bridge-zigbee-backup") {
    return { kind: "zigbee", createdAt, zigbee: value, zigbeeArchiveOnly: false };
  }
  if (isRecord(value.sections)) {
    return { kind: "home", createdAt, home: value, zigbeeArchiveOnly: false };
  }
  throw new Error("Bu dosya bir Villa Bridge yedeği değil.");
};
