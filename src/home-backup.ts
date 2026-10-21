import { copyFile, mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { atomicTemporaryPath } from "./atomic-file.js";
import {
  automationDeviceIds,
  removeDeviceFromAutomations,
  validateAutomations,
  type Automation,
  type AutomationDeviceLookup,
  type AutomationGroupLookup
} from "./automations.js";
import {
  validateDeviceImagePreferences,
  type DeviceImagePreferences
} from "./device-images.js";
import { validateDeviceNote, type DeviceNotes } from "./device-notes.js";
import { validateHomeFavorites, type HomeFavorite } from "./home-favorites.js";
import {
  removeDeviceFromHomeGroups,
  validateHomeGroups,
  type HomeGroup
} from "./home-groups.js";
import {
  emptyHomeVisibility,
  validateHomeVisibility,
  type HiddenDeviceControl,
  type HomeVisibility
} from "./home-visibility.js";

/**
 * Ev yapılandırması yedeği — aynı evde güncelleme öncesi alınır, gerekirse geri yüklenir.
 *
 * Yedek indirilebilir bir dosyadır: içine ASLA sır girmez. Parola özetleri (`auth.json`),
 * Zigbee ağ anahtarı, MQTT/HA kimlik bilgileri, Matter sertifikaları ve kuruluma özel
 * durum dosyaları kapsam dışıdır. Kapsam yalnızca aşağıdaki yedi bölümdür.
 *
 * Sürüm bilerek 1'de kalır: yeni bölüm eksikse boş sayılır, böylece eski yedekler açılmaya
 * devam eder.
 */
export const homeBackupVersion = 1;

export const homeBackupSectionNames = [
  "automations",
  "aliases",
  "homeGroups",
  "favorites",
  "homeVisibility",
  "deviceNotes",
  "deviceImages"
] as const;

export type HomeBackupSectionName = (typeof homeBackupSectionNames)[number];

export type HomeBackupMode = "replace" | "merge";

export interface HomeBackupSections {
  /** Otomasyon kuralları. */
  automations: Automation[];
  /** Cihaz ve kanal adları (`0x...` veya `0x...:button:1`). */
  aliases: Record<string, string>;
  /** Ana ekran odaları. */
  homeGroups: HomeGroup[];
  /** Ana ekran favorileri. */
  favorites: HomeFavorite[];
  /** Gizlenen cihaz kontrolleri ve odalar — kullanıcının kurduğu bir tercih, sır içermez. */
  homeVisibility: HomeVisibility;
  /** Cihaz notları. */
  deviceNotes: DeviceNotes;
  /** Cihaz görsel tercihleri. */
  deviceImages: DeviceImagePreferences;
}

export interface HomeBackup {
  version: number;
  createdAt: string;
  deviceCount: number;
  sections: HomeBackupSections;
}

export interface HomeBackupSectionSummary {
  section: HomeBackupSectionName;
  /** Yedekten gelen ve uygulanabilir kayıt sayısı. */
  incoming: number;
  /** Mevcutta olmayan, yeni eklenecek kayıt sayısı. */
  added: number;
  /** Mevcut kaydın üzerine yazacak kayıt sayısı. */
  overwritten: number;
  /** Yalnızca `replace`: silinecek mevcut kayıt sayısı. */
  removed: number;
  /** Artık var olmayan cihaza ait olduğu için düşürülen kayıt sayısı. */
  skippedMissingDevices: number;
}

export interface HomeBackupSummary {
  mode: HomeBackupMode;
  createdAt: string | null;
  sections: HomeBackupSectionSummary[];
  totalIncoming: number;
  totalSkippedMissingDevices: number;
}

export interface HomeBackupPaths {
  automations: string;
  /** Takma ad dosyası yapılandırılmamış olabilir. */
  aliases?: string;
  homeGroups: string;
  favorites: string;
  homeVisibility: string;
  deviceNotes: string;
  deviceImages: string;
}

export interface HomeBackupOptions {
  paths: HomeBackupPaths;
  /** Canlı takma ad haritası; geri yükleme sonrası yerinde güncellenir. */
  aliases?: Map<string, string>;
  /** Şu anda tanınan cihaz UID'leri. Boş dizi "cihaz listesi hazır değil" demektir. */
  knownDeviceIds?: () => string[];
  /** Kilit/siren eylem yasağı için cihaz çözümleyici. */
  automationLookup?: AutomationDeviceLookup;
  /** Grup eylemlerinin üyelerini çözer; grup içi kilit/siren yasağı buna bakar. */
  automationGroupLookup?: AutomationGroupLookup;
}

const deviceIdPattern = /^0x[0-9a-f]{16}$/;
const aliasKeyPattern = /^0x[0-9a-f]{16}(?::[a-z0-9:_@-]{1,64})?$/;

export const maxBackupAliases = 2048;
export const maxBackupDeviceNotes = 2048;
export const maxBackupImagePreferences = 4096;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

export const validateBackupAliases = (value: unknown): Record<string, string> => {
  if (!isRecord(value)) throw new Error("Yedekteki cihaz adları geçersiz.");
  const entries = Object.entries(value);
  if (entries.length > maxBackupAliases) throw new Error("Yedekteki cihaz adı sayısı sınırı aşıyor.");
  const result: Record<string, string> = {};
  for (const [rawKey, rawName] of entries) {
    const key = rawKey.trim().toLowerCase();
    if (!aliasKeyPattern.test(key)) throw new Error("Yedekteki cihaz adı anahtarı geçersiz.");
    if (typeof rawName !== "string") throw new Error("Yedekteki cihaz adı geçersiz.");
    const name = rawName.trim();
    if (!name || Buffer.byteLength(name, "utf8") > 64) throw new Error("Yedekteki cihaz adı geçersiz.");
    result[key] = name;
  }
  return result;
};

export const validateBackupDeviceNotes = (value: unknown): DeviceNotes => {
  if (!isRecord(value)) throw new Error("Yedekteki cihaz notları geçersiz.");
  const entries = Object.entries(value);
  if (entries.length > maxBackupDeviceNotes) throw new Error("Yedekteki cihaz notu sayısı sınırı aşıyor.");
  const result: DeviceNotes = {};
  for (const [rawId, note] of entries) {
    const id = rawId.trim().toLowerCase();
    if (!deviceIdPattern.test(id)) throw new Error("Yedekteki cihaz notu UID'si geçersiz.");
    const text = validateDeviceNote(note);
    if (text) result[id] = text;
  }
  return result;
};

export const validateBackupDeviceImages = (value: unknown): DeviceImagePreferences => {
  if (!isRecord(value)) throw new Error("Yedekteki cihaz görsel tercihleri geçersiz.");
  const preferences = validateDeviceImagePreferences(value);
  const total = Object.keys(preferences.devices).length + Object.keys(preferences.models).length;
  if (total > maxBackupImagePreferences) {
    throw new Error("Yedekteki cihaz görsel tercihi sayısı sınırı aşıyor.");
  }
  for (const id of Object.keys(preferences.devices)) {
    if (!deviceIdPattern.test(id)) throw new Error("Yedekteki cihaz görseli UID'si geçersiz.");
  }
  return preferences;
};

interface KeyedCounts {
  added: number;
  overwritten: number;
  removed: number;
}

const keyedCounts = (existing: string[], incoming: string[], mode: HomeBackupMode): KeyedCounts => {
  const existingKeys = new Set(existing);
  const incomingKeys = new Set(incoming);
  let added = 0;
  let overwritten = 0;
  for (const key of incomingKeys) {
    if (existingKeys.has(key)) overwritten += 1;
    else added += 1;
  }
  const removed = mode === "replace"
    ? [...existingKeys].filter((key) => !incomingKeys.has(key)).length
    : 0;
  return { added, overwritten, removed };
};

const mergeRecords = <T>(
  existing: Record<string, T>,
  incoming: Record<string, T>,
  mode: HomeBackupMode
): Record<string, T> => (mode === "replace" ? { ...incoming } : { ...existing, ...incoming });

const mergeById = <T extends { id: string }>(
  existing: T[],
  incoming: T[],
  mode: HomeBackupMode
): T[] => {
  if (mode === "replace") return incoming;
  const result = existing.map((entry) =>
    incoming.find((candidate) => candidate.id === entry.id) ?? entry);
  const known = new Set(result.map((entry) => entry.id));
  return [...result, ...incoming.filter((entry) => !known.has(entry.id))];
};

const favoriteKey = (favorite: HomeFavorite): string => `${favorite.deviceId}|${favorite.controlId}`;

/**
 * Gizleme kaydının anahtarı KAPSAMI da içerir: aynı cihaz iki ayrı odanın kartında ayrı ayrı
 * gizlenebilir, iki kayıt birbirini ezmemeli. Kapsamsız (eski) kayıt kendi başına bir anahtardır.
 */
const hiddenDeviceKey = (entry: HiddenDeviceControl): string =>
  `${entry.scope ?? ""}|${entry.deviceId}|${entry.controlId}`;

/** Özet sayımı için: gizlenen cihaz ve oda kayıtları tek anahtar uzayında toplanır. */
const visibilityKeys = (visibility: HomeVisibility): string[] => [
  ...visibility.hiddenDevices.map((entry) => `device:${hiddenDeviceKey(entry)}`),
  ...visibility.hiddenGroups.map((groupId) => `group:${groupId}`)
];

const mergeVisibility = (
  current: HomeVisibility,
  incoming: HomeVisibility,
  mode: HomeBackupMode
): HomeVisibility => {
  if (mode === "replace") return incoming;
  const incomingDeviceKeys = new Set(incoming.hiddenDevices.map(hiddenDeviceKey));
  return {
    hiddenDevices: [
      ...current.hiddenDevices.filter((entry) => !incomingDeviceKeys.has(hiddenDeviceKey(entry))),
      ...incoming.hiddenDevices
    ],
    hiddenGroups: [
      ...current.hiddenGroups,
      ...incoming.hiddenGroups.filter((groupId) => !current.hiddenGroups.includes(groupId))
    ]
  };
};

const readJsonFile = async (path: string): Promise<unknown> => {
  try {
    return JSON.parse(await readFile(path, "utf8")) as unknown;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
    throw error;
  }
};

interface PendingWrite {
  path: string;
  content: string;
}

/** Yedeği üretir, önizler ve atomik olarak geri yükler. */
export class HomeBackupService {
  constructor(private readonly options: HomeBackupOptions) {}

  private knownDevices(): Set<string> | null {
    const ids = this.options.knownDeviceIds?.() ?? [];
    // Cihaz listesi henüz dolmadıysa hiçbir kaydı düşürmeyiz; yoksa yedek boşalırdı.
    if (ids.length === 0) return null;
    return new Set(ids.map((id) => id.trim().toLowerCase()));
  }

  private async currentSections(): Promise<HomeBackupSections> {
    const paths = this.options.paths;
    const [
      automations,
      homeGroups,
      favorites,
      homeVisibility,
      deviceNotes,
      deviceImages
    ] = await Promise.all([
      readJsonFile(paths.automations),
      readJsonFile(paths.homeGroups),
      readJsonFile(paths.favorites),
      readJsonFile(paths.homeVisibility),
      readJsonFile(paths.deviceNotes),
      readJsonFile(paths.deviceImages)
    ]);
    const aliases = this.options.aliases
      ? Object.fromEntries([...this.options.aliases.entries()])
      : paths.aliases ? (await readJsonFile(paths.aliases)) ?? {} : {};
    return {
      automations: validateAutomations(automations ?? []),
      aliases: validateBackupAliases(aliases),
      homeGroups: validateHomeGroups(homeGroups ?? []),
      favorites: validateHomeFavorites(favorites ?? []),
      homeVisibility: validateHomeVisibility(homeVisibility ?? emptyHomeVisibility()),
      deviceNotes: validateBackupDeviceNotes(deviceNotes ?? {}),
      deviceImages: validateBackupDeviceImages(deviceImages ?? { devices: {}, models: {} })
    };
  }

  /** Yedek zarfını üretir. Hiçbir sır bu zarfa girmez. */
  async create(now: Date = new Date()): Promise<HomeBackup> {
    return {
      version: homeBackupVersion,
      createdAt: now.toISOString(),
      deviceCount: this.options.knownDeviceIds?.().length ?? 0,
      sections: await this.currentSections()
    };
  }

  /** Yedeği doğrular, özetini çıkarır ve yazılacak son içeriği hazırlar. Dosyaya dokunmaz. */
  async plan(
    value: unknown,
    mode: HomeBackupMode
  ): Promise<{ summary: HomeBackupSummary; sections: HomeBackupSections }> {
    if (mode !== "replace" && mode !== "merge") {
      throw new Error("Geri yükleme biçimi geçersiz.");
    }
    if (!isRecord(value)) throw new Error("Yedek dosyası okunamadı.");
    if (value.version !== homeBackupVersion) {
      throw new Error("Yedek dosyası sürümü tanınmıyor; bu Villa Bridge sürümü onu açamıyor.");
    }
    if (!isRecord(value.sections)) throw new Error("Yedek dosyasında bölüm bulunamadı.");
    const raw = value.sections;
    const known = this.knownDevices();
    const alive = (deviceId: string): boolean => known === null || known.has(deviceId);

    const incomingAutomations = validateAutomations(
      raw.automations ?? [],
      this.options.automationLookup,
      this.options.automationGroupLookup
    );
    let automations = incomingAutomations;
    if (known !== null) {
      const missing = new Set<string>();
      for (const automation of incomingAutomations) {
        // Tetikleyici, koşul ve cihaz eylemlerinin tamamı; zaman/güneş ve grup eylemleri
        // cihaz kimliği taşımaz.
        for (const deviceId of automationDeviceIds(automation)) {
          if (!alive(deviceId)) missing.add(deviceId);
        }
      }
      for (const deviceId of missing) automations = removeDeviceFromAutomations(automations, deviceId);
    }
    const incomingAliases = validateBackupAliases(raw.aliases ?? {});
    const aliases = Object.fromEntries(
      Object.entries(incomingAliases).filter(([key]) => alive(key.split(":")[0] ?? ""))
    );
    const incomingGroups = validateHomeGroups(raw.homeGroups ?? []);
    let homeGroups = incomingGroups;
    let groupItemsDropped = 0;
    if (known !== null) {
      const missing = new Set<string>();
      for (const group of incomingGroups) {
        for (const item of group.items) if (!alive(item.deviceId)) missing.add(item.deviceId);
      }
      const before = incomingGroups.reduce((total, group) => total + group.items.length, 0);
      for (const deviceId of missing) homeGroups = removeDeviceFromHomeGroups(homeGroups, deviceId);
      groupItemsDropped = before - homeGroups.reduce((total, group) => total + group.items.length, 0);
    }
    const incomingFavorites = validateHomeFavorites(raw.favorites ?? []);
    const favorites = incomingFavorites.filter((favorite) => alive(favorite.deviceId));
    // Oda kimlikleri cihaz değil; yalnız gizlenen cihaz kayıtları ölü UID için düşer.
    const incomingVisibility = validateHomeVisibility(raw.homeVisibility ?? emptyHomeVisibility());
    const homeVisibility: HomeVisibility = {
      hiddenDevices: incomingVisibility.hiddenDevices.filter((entry) => alive(entry.deviceId)),
      hiddenGroups: incomingVisibility.hiddenGroups
    };
    const incomingNotes = validateBackupDeviceNotes(raw.deviceNotes ?? {});
    const deviceNotes = Object.fromEntries(
      Object.entries(incomingNotes).filter(([id]) => alive(id))
    );
    const incomingImages = validateBackupDeviceImages(raw.deviceImages ?? { devices: {}, models: {} });
    const deviceImages: DeviceImagePreferences = {
      devices: Object.fromEntries(
        Object.entries(incomingImages.devices).filter(([id]) => alive(id))
      ),
      // Model tercihleri cihaza değil ürün modeline bağlı; cihaz gitse de anlamlı kalır.
      models: incomingImages.models
    };

    const current = await this.currentSections();
    const merged: HomeBackupSections = {
      automations: validateAutomations(
        mergeById(current.automations, automations, mode),
        this.options.automationLookup,
        this.options.automationGroupLookup
      ),
      aliases: mergeRecords(current.aliases, aliases, mode),
      homeGroups: validateHomeGroups(mergeById(current.homeGroups, homeGroups, mode)),
      favorites: validateHomeFavorites(mode === "replace"
        ? favorites
        : [
          ...current.favorites.filter((favorite) =>
            !favorites.some((incoming) => favoriteKey(incoming) === favoriteKey(favorite))),
          ...favorites
        ]),
      homeVisibility: validateHomeVisibility(
        mergeVisibility(current.homeVisibility, homeVisibility, mode)
      ),
      deviceNotes: mergeRecords(current.deviceNotes, deviceNotes, mode),
      deviceImages: {
        devices: mergeRecords(current.deviceImages.devices, deviceImages.devices, mode),
        models: mergeRecords(current.deviceImages.models, deviceImages.models, mode)
      }
    };

    const sectionSummary = (
      section: HomeBackupSectionName,
      existingKeys: string[],
      incomingKeys: string[],
      skipped: number
    ): HomeBackupSectionSummary => ({
      section,
      incoming: incomingKeys.length,
      skippedMissingDevices: skipped,
      ...keyedCounts(existingKeys, incomingKeys, mode)
    });

    const summary: HomeBackupSummary = {
      mode,
      createdAt: typeof value.createdAt === "string" ? value.createdAt : null,
      sections: [
        sectionSummary(
          "automations",
          current.automations.map((automation) => automation.id),
          automations.map((automation) => automation.id),
          incomingAutomations.length - automations.length
        ),
        sectionSummary(
          "aliases",
          Object.keys(current.aliases),
          Object.keys(aliases),
          Object.keys(incomingAliases).length - Object.keys(aliases).length
        ),
        sectionSummary(
          "homeGroups",
          current.homeGroups.map((group) => group.id),
          homeGroups.map((group) => group.id),
          groupItemsDropped
        ),
        sectionSummary(
          "favorites",
          current.favorites.map(favoriteKey),
          favorites.map(favoriteKey),
          incomingFavorites.length - favorites.length
        ),
        sectionSummary(
          "homeVisibility",
          visibilityKeys(current.homeVisibility),
          visibilityKeys(homeVisibility),
          incomingVisibility.hiddenDevices.length - homeVisibility.hiddenDevices.length
        ),
        sectionSummary(
          "deviceNotes",
          Object.keys(current.deviceNotes),
          Object.keys(deviceNotes),
          Object.keys(incomingNotes).length - Object.keys(deviceNotes).length
        ),
        sectionSummary(
          "deviceImages",
          [
            ...Object.keys(current.deviceImages.devices).map((key) => `device:${key}`),
            ...Object.keys(current.deviceImages.models).map((key) => `model:${key}`)
          ],
          [
            ...Object.keys(deviceImages.devices).map((key) => `device:${key}`),
            ...Object.keys(deviceImages.models).map((key) => `model:${key}`)
          ],
          Object.keys(incomingImages.devices).length - Object.keys(deviceImages.devices).length
        )
      ],
      totalIncoming: 0,
      totalSkippedMissingDevices: 0
    };
    summary.totalIncoming = summary.sections.reduce((total, section) => total + section.incoming, 0);
    summary.totalSkippedMissingDevices = summary.sections
      .reduce((total, section) => total + section.skippedMissingDevices, 0);
    return { summary, sections: merged };
  }

  /** Yalnızca özet döndürür; hiçbir dosya değişmez. */
  async preview(value: unknown, mode: HomeBackupMode): Promise<HomeBackupSummary> {
    return (await this.plan(value, mode)).summary;
  }

  /**
   * Ya hepsi yazılır ya hiçbiri: önce her bölüm doğrulanır, sonra geçici dosyalar yazılır,
   * en sonda `rename` ile yerine geçer. Yazmadan önce mevcut durum `.bak` olarak saklanır.
   */
  async restore(value: unknown, mode: HomeBackupMode): Promise<HomeBackupSummary> {
    const { summary, sections } = await this.plan(value, mode);
    const paths = this.options.paths;
    if (!paths.aliases && Object.keys(sections.aliases).length > 0) {
      throw new Error("Cihaz adı dosyası yapılandırılmadığı için adlar geri yüklenemiyor.");
    }
    const serialize = (data: unknown): string => `${JSON.stringify(data, null, 2)}\n`;
    const sortedAliases = Object.fromEntries(
      Object.entries(sections.aliases).sort(([left], [right]) => left.localeCompare(right, "en"))
    );
    const writes: PendingWrite[] = [
      { path: paths.automations, content: serialize(sections.automations) },
      { path: paths.homeGroups, content: serialize(sections.homeGroups) },
      { path: paths.favorites, content: serialize(sections.favorites) },
      { path: paths.homeVisibility, content: serialize(sections.homeVisibility) },
      { path: paths.deviceNotes, content: serialize(sections.deviceNotes) },
      { path: paths.deviceImages, content: serialize(sections.deviceImages) },
      ...(paths.aliases ? [{ path: paths.aliases, content: serialize(sortedAliases) }] : [])
    ];

    const temporaries = writes.map((write) => atomicTemporaryPath(write.path));
    const backups = writes.map((write) => `${write.path}.bak`);
    const renamed: number[] = [];
    try {
      for (const [index, write] of writes.entries()) {
        await mkdir(dirname(write.path), { recursive: true });
        await copyFile(write.path, backups[index] as string).catch((error) => {
          if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
        });
        await writeFile(temporaries[index] as string, write.content, { mode: 0o600 });
      }
      for (const [index, write] of writes.entries()) {
        await rename(temporaries[index] as string, write.path);
        renamed.push(index);
      }
    } catch (error) {
      for (const index of renamed) {
        await copyFile(backups[index] as string, writes[index]?.path as string)
          .catch(() => rm(writes[index]?.path as string, { force: true }));
      }
      for (const temporary of temporaries) await rm(temporary, { force: true });
      throw new Error(
        `Yedek geri yüklenemedi, önceki durum korundu: ${error instanceof Error ? error.message : String(error)}`
      );
    }

    // Canlı takma ad haritası yerinde güncellenir; DeviceStore ve kaynaklar aynı haritayı okur.
    if (this.options.aliases) {
      this.options.aliases.clear();
      for (const [key, name] of Object.entries(sortedAliases)) this.options.aliases.set(key, name);
    }
    return summary;
  }
}
