import { readFile } from "node:fs/promises";
import { writeJsonAtomic } from "./atomic-file.js";
import { themeColorTokens, type ThemeColorToken, type ThemePackage } from "./theme-package.js";

export interface AppearanceSettings {
  schemaVersion: 1;
  defaultPackageId: string;
  overrides: Record<string, {
    light?: Partial<Record<ThemeColorToken, string>>;
    dark?: Partial<Record<ThemeColorToken, string>>;
    solar?: Partial<Record<"night" | "dawn" | "day" | "dusk", Partial<Record<ThemeColorToken, string>>>>;
  }>;
}

const colorPattern = /^(?:#[0-9a-f]{6}|#[0-9a-f]{8}|rgba?\(\s*(?:\d{1,3}\s*,\s*){2}\d{1,3}(?:\s*,\s*(?:0|1|0?\.\d+))?\s*\))$/i;

const validateColorMap = (value: unknown): Partial<Record<ThemeColorToken, string>> => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error("Tema renk geçersiz kılmaları geçersiz.");
  const result: Partial<Record<ThemeColorToken, string>> = {};
  for (const [key, raw] of Object.entries(value)) {
    if (!themeColorTokens.includes(key as ThemeColorToken)) throw new Error(`Bilinmeyen tema tokenı: ${key}`);
    if (typeof raw !== "string" || raw.length > 64 || !colorPattern.test(raw.trim())) throw new Error(`${key}: renk geçersiz.`);
    result[key as ThemeColorToken] = raw.trim().toLowerCase();
  }
  return result;
};

export const validateAppearance = (value: unknown, packages: ThemePackage[]): AppearanceSettings => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error("Görünüm ayarı geçersiz.");
  const source = value as Record<string, unknown>;
  for (const key of Object.keys(source)) {
    if (key !== "schemaVersion" && key !== "defaultPackageId" && key !== "overrides") {
      throw new Error(`Görünüm ayarında bilinmeyen alan: ${key}`);
    }
  }
  if (source.schemaVersion !== 1) throw new Error("Görünüm şema sürümü desteklenmiyor.");
  if (typeof source.defaultPackageId !== "string" || !packages.some((item) => item.id === source.defaultPackageId)) {
    throw new Error("Varsayılan tema paketi bulunamadı.");
  }
  if (typeof source.overrides !== "object" || source.overrides === null || Array.isArray(source.overrides)) {
    throw new Error("Tema geçersiz kılmaları geçersiz.");
  }
  const overrides: AppearanceSettings["overrides"] = {};
  for (const [packageId, raw] of Object.entries(source.overrides)) {
    if (!packages.some((item) => item.id === packageId)) throw new Error(`Tema paketi bulunamadı: ${packageId}`);
    if (typeof raw !== "object" || raw === null || Array.isArray(raw)) throw new Error(`${packageId}: geçersiz kılma geçersiz.`);
    const item = raw as Record<string, unknown>;
    for (const key of Object.keys(item)) if (key !== "light" && key !== "dark" && key !== "solar") throw new Error(`${packageId}: bilinmeyen ${key} alanı.`);
    const normalized: AppearanceSettings["overrides"][string] = {};
    if (item.light !== undefined) normalized.light = validateColorMap(item.light);
    if (item.dark !== undefined) normalized.dark = validateColorMap(item.dark);
    if (item.solar !== undefined) {
      if (typeof item.solar !== "object" || item.solar === null || Array.isArray(item.solar)) throw new Error(`${packageId}: solar geçersiz.`);
      normalized.solar = {};
      for (const [anchor, colors] of Object.entries(item.solar)) {
        if (anchor !== "night" && anchor !== "dawn" && anchor !== "day" && anchor !== "dusk") throw new Error(`${packageId}: bilinmeyen solar durak ${anchor}.`);
        normalized.solar[anchor] = validateColorMap(colors);
      }
    }
    overrides[packageId] = normalized;
  }
  return { schemaVersion: 1, defaultPackageId: source.defaultPackageId, overrides };
};

export class AppearanceStore {
  constructor(
    private readonly path: string,
    private readonly packages: ThemePackage[],
    private readonly fallbackPackageId: string
  ) {}

  async get(): Promise<AppearanceSettings> {
    try {
      return validateAppearance(JSON.parse(await readFile(this.path, "utf8")), this.packages);
    } catch (error) {
      const code = (error as NodeJS.ErrnoException).code;
      if (code !== undefined && code !== "ENOENT") throw error;
      return { schemaVersion: 1, defaultPackageId: this.fallbackPackageId, overrides: {} };
    }
  }

  async save(value: unknown): Promise<AppearanceSettings> {
    const appearance = validateAppearance(value, this.packages);
    await writeJsonAtomic(this.path, appearance, { mode: 0o600 });
    return appearance;
  }
}
