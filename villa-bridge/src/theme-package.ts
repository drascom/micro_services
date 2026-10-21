import type { Dirent } from "node:fs";
import { readFile, readdir } from "node:fs/promises";
import { resolve } from "node:path";

export const themeColorTokens = [
  "page", "ink", "inkSoft", "glassCard", "glassTile", "glassNav", "glassEdge", "glassSheen",
  "accent", "accentSoft", "stateOn", "stateActive", "stateAlert", "stateOffline", "skyTop",
  "skyBottom", "sun", "moon", "stars", "mountainFar", "mountainNear"
] as const;

export type ThemeColorToken = (typeof themeColorTokens)[number];
export type ThemeColors = Record<ThemeColorToken, string>;

export interface ThemeMaterials {
  blur: number;
  saturation: number;
  brightness: number;
  shadow: "none" | "soft" | "floating";
}

export interface ThemePalette {
  colors: ThemeColors;
  materials: ThemeMaterials;
}

export interface ThemePackage {
  schemaVersion: 1;
  id: string;
  name: string;
  author: string;
  behaviors: {
    light: "fixed-light-v1";
    dark: "fixed-dark-v1";
    system: "system-v1";
    sun: "solar-v1";
  };
  palettes: {
    light: ThemePalette;
    dark: ThemePalette;
    solar: {
      anchors: Record<"night" | "dawn" | "day" | "dusk", ThemePalette>;
    };
  };
}

const colorPattern = /^(?:#[0-9a-f]{6}|#[0-9a-f]{8}|rgba?\(\s*(?:\d{1,3}\s*,\s*){2}\d{1,3}(?:\s*,\s*(?:0|1|0?\.\d+))?\s*\))$/i;
const idPattern = /^[a-z0-9][a-z0-9-]{1,62}$/;

const record = (value: unknown, message: string): Record<string, unknown> => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error(message);
  return value as Record<string, unknown>;
};

const exactKeys = (value: Record<string, unknown>, allowed: readonly string[], label: string): void => {
  const expected = new Set(allowed);
  for (const key of Object.keys(value)) if (!expected.has(key)) throw new Error(`${label}: bilinmeyen ${key} alanı.`);
  for (const key of allowed) if (!(key in value)) throw new Error(`${label}: ${key} alanı eksik.`);
};

const normalizeColor = (value: unknown, label: string): string => {
  if (typeof value !== "string" || value.length > 64 || !colorPattern.test(value.trim())) {
    throw new Error(`${label}: renk yalnız #RRGGBB, #RRGGBBAA, rgb() veya rgba() olabilir.`);
  }
  return value.trim().toLowerCase();
};

const validatePalette = (value: unknown, label: string): ThemePalette => {
  const palette = record(value, `${label}: palet geçersiz.`);
  exactKeys(palette, ["colors", "materials"], label);
  const colors = record(palette.colors, `${label}: renkler geçersiz.`);
  exactKeys(colors, themeColorTokens, `${label}.colors`);
  const normalizedColors = Object.fromEntries(
    themeColorTokens.map((token) => [token, normalizeColor(colors[token], `${label}.${token}`)])
  ) as unknown as ThemeColors;
  const materials = record(palette.materials, `${label}: malzeme geçersiz.`);
  exactKeys(materials, ["blur", "saturation", "brightness", "shadow"], `${label}.materials`);
  const blur = Number(materials.blur);
  const saturation = Number(materials.saturation);
  const brightness = Number(materials.brightness);
  const shadow = materials.shadow;
  if (!Number.isFinite(blur) || blur < 0 || blur > 30) throw new Error(`${label}: blur 0..30 olmalıdır.`);
  if (!Number.isFinite(saturation) || saturation < 0.5 || saturation > 2) {
    throw new Error(`${label}: saturation 0.5..2 olmalıdır.`);
  }
  if (!Number.isFinite(brightness) || brightness < 0.5 || brightness > 1.5) {
    throw new Error(`${label}: brightness 0.5..1.5 olmalıdır.`);
  }
  if (shadow !== "none" && shadow !== "soft" && shadow !== "floating") {
    throw new Error(`${label}: gölge profili geçersiz.`);
  }
  return { colors: normalizedColors, materials: { blur, saturation, brightness, shadow } };
};

export const validateThemePackage = (value: unknown): ThemePackage => {
  const source = record(value, "Tema paketi geçersiz.");
  exactKeys(source, ["schemaVersion", "id", "name", "author", "behaviors", "palettes"], "theme");
  if (source.schemaVersion !== 1) throw new Error("Tema şema sürümü desteklenmiyor.");
  if (typeof source.id !== "string" || !idPattern.test(source.id)) throw new Error("Tema kimliği geçersiz.");
  if (typeof source.name !== "string" || source.name.trim().length < 2 || source.name.length > 80) {
    throw new Error("Tema adı geçersiz.");
  }
  if (typeof source.author !== "string" || source.author.trim().length < 2 || source.author.length > 80) {
    throw new Error("Tema yazarı geçersiz.");
  }
  const behaviors = record(source.behaviors, "Tema davranışları geçersiz.");
  exactKeys(behaviors, ["light", "dark", "system", "sun"], "behaviors");
  if (
    behaviors.light !== "fixed-light-v1" || behaviors.dark !== "fixed-dark-v1"
    || behaviors.system !== "system-v1" || behaviors.sun !== "solar-v1"
  ) throw new Error("Tema paketi bilinmeyen davranış istiyor.");
  const palettes = record(source.palettes, "Tema paletleri geçersiz.");
  exactKeys(palettes, ["light", "dark", "solar"], "palettes");
  const solar = record(palettes.solar, "Solar palet geçersiz.");
  exactKeys(solar, ["anchors"], "palettes.solar");
  const anchors = record(solar.anchors, "Solar duraklar geçersiz.");
  exactKeys(anchors, ["night", "dawn", "day", "dusk"], "palettes.solar.anchors");
  return {
    schemaVersion: 1,
    id: source.id,
    name: source.name.trim(),
    author: source.author.trim(),
    behaviors: {
      light: "fixed-light-v1",
      dark: "fixed-dark-v1",
      system: "system-v1",
      sun: "solar-v1"
    },
    palettes: {
      light: validatePalette(palettes.light, "palettes.light"),
      dark: validatePalette(palettes.dark, "palettes.dark"),
      solar: {
        anchors: {
          night: validatePalette(anchors.night, "solar.night"),
          dawn: validatePalette(anchors.dawn, "solar.dawn"),
          day: validatePalette(anchors.day, "solar.day"),
          dusk: validatePalette(anchors.dusk, "solar.dusk")
        }
      }
    }
  };
};

/**
 * GÖMÜLÜ VARSAYILAN — EV OTOMASYONU BİR TEMA DOSYASI YÜZÜNDEN DÜŞMEZ.
 * Diskteki paketlerin hepsi bozuksa (ya da dizin hiç yoksa) panel paletsiz kalmasın diye
 * derlenmiş halde taşınan asgari paket. Süs değil sigorta: renkleri panelin kendi CSS
 * varsayılanlarıyla aynı ailedendir, yani devreye girdiğinde ekran tanıdık görünür.
 */
export const embeddedThemePackage: ThemePackage = {
  schemaVersion: 1,
  id: "villa-embedded",
  name: "Villa Embedded",
  author: "Villa Bridge",
  behaviors: { light: "fixed-light-v1", dark: "fixed-dark-v1", system: "system-v1", sun: "solar-v1" },
  palettes: {
    light: {
      colors: {
        page: "#edf0f2", ink: "#17211d", inkSoft: "rgba(23,33,29,.66)",
        glassCard: "rgba(255,255,255,.47)", glassTile: "rgba(255,255,255,.62)",
        glassNav: "rgba(255,255,255,.72)", glassEdge: "rgba(255,255,255,.90)",
        glassSheen: "rgba(255,255,255,.84)", accent: "#12503b", accentSoft: "rgba(24,77,59,.10)",
        stateOn: "#be7410", stateActive: "#247f62", stateAlert: "#9d2f2a", stateOffline: "#6e7773",
        skyTop: "#f2f3f7", skyBottom: "#e7ebf1", sun: "#f1b554", moon: "#dfe5ef",
        stars: "rgba(255,255,255,.72)", mountainFar: "#c9d1d8", mountainNear: "#aab7c2"
      },
      materials: { blur: 22, saturation: 1.35, brightness: 1.05, shadow: "soft" }
    },
    dark: {
      colors: {
        page: "#0c0e11", ink: "#f1f5f3", inkSoft: "rgba(241,245,243,.68)",
        glassCard: "rgba(255,255,255,.055)", glassTile: "rgba(255,255,255,.095)",
        glassNav: "rgba(255,255,255,.075)", glassEdge: "rgba(255,255,255,.18)",
        glassSheen: "rgba(255,255,255,.15)", accent: "#9ee3c7", accentSoft: "rgba(113,198,162,.14)",
        stateOn: "#ffd17c", stateActive: "#68c297", stateAlert: "#ffaaa3", stateOffline: "#8b9a93",
        skyTop: "#0c0e11", skyBottom: "#171c22", sun: "#ffd17c", moon: "#eef2ff",
        stars: "rgba(255,255,255,.78)", mountainFar: "#24304a", mountainNear: "#151e32"
      },
      materials: { blur: 24, saturation: 1.2, brightness: 0.88, shadow: "floating" }
    },
    solar: {
      anchors: {
        night: {
          colors: {
            page: "#101831", ink: "#f5f8fb", inkSoft: "rgba(245,248,251,.70)",
            glassCard: "rgba(28,37,56,.34)", glassTile: "rgba(46,58,80,.48)",
            glassNav: "rgba(24,33,52,.62)", glassEdge: "rgba(255,255,255,.20)",
            glassSheen: "rgba(255,255,255,.23)", accent: "#a4ead1", accentSoft: "rgba(131,220,187,.14)",
            stateOn: "#ffd17c", stateActive: "#96e5c8", stateAlert: "#ffaaa3", stateOffline: "#8796a8",
            skyTop: "#101831", skyBottom: "#263757", sun: "#ffd17c", moon: "#eef2ff",
            stars: "rgba(255,255,255,.80)", mountainFar: "#24304a", mountainNear: "#151e32"
          },
          materials: { blur: 24, saturation: 1.3, brightness: 0.88, shadow: "floating" }
        },
        dawn: {
          colors: {
            page: "#777fae", ink: "#27243a", inkSoft: "rgba(39,36,58,.70)",
            glassCard: "rgba(255,247,244,.28)", glassTile: "rgba(255,247,244,.43)",
            glassNav: "rgba(255,247,244,.54)", glassEdge: "rgba(255,247,244,.64)",
            glassSheen: "rgba(255,255,255,.52)", accent: "#205c49", accentSoft: "rgba(32,92,73,.13)",
            stateOn: "#d98814", stateActive: "#2b6f58", stateAlert: "#ad4842", stateOffline: "#756f78",
            skyTop: "#777fae", skyBottom: "#f1ad82", sun: "#ffd282", moon: "#e5e7f2",
            stars: "rgba(255,255,255,.36)", mountainFar: "#88758d", mountainNear: "#5b536d"
          },
          materials: { blur: 24, saturation: 1.4, brightness: 1, shadow: "soft" }
        },
        day: {
          colors: {
            page: "#7cb7df", ink: "#10263a", inkSoft: "rgba(16,38,58,.70)",
            glassCard: "rgba(241,249,255,.31)", glassTile: "rgba(246,251,255,.48)",
            glassNav: "rgba(236,247,255,.60)", glassEdge: "rgba(255,255,255,.72)",
            glassSheen: "rgba(255,255,255,.58)", accent: "#176348", accentSoft: "rgba(23,99,72,.13)",
            stateOn: "#d98814", stateActive: "#247f62", stateAlert: "#ad4842", stateOffline: "#687988",
            skyTop: "#7cb7df", skyBottom: "#c4def0", sun: "#ffbd35", moon: "#e8edf7",
            stars: "rgba(255,255,255,.18)", mountainFar: "#7895ae", mountainNear: "#526e8a"
          },
          materials: { blur: 24, saturation: 1.45, brightness: 1.02, shadow: "soft" }
        },
        dusk: {
          colors: {
            page: "#4d527f", ink: "#fff8f2", inkSoft: "rgba(255,248,242,.72)",
            glassCard: "rgba(35,34,55,.27)", glassTile: "rgba(45,43,65,.44)",
            glassNav: "rgba(36,36,58,.58)", glassEdge: "rgba(255,255,255,.27)",
            glassSheen: "rgba(255,255,255,.28)", accent: "#a9efd7", accentSoft: "rgba(138,226,195,.15)",
            stateOn: "#ffd17a", stateActive: "#a2e8d0", stateAlert: "#ffaaa3", stateOffline: "#a59aa6",
            skyTop: "#4d527f", skyBottom: "#d97d66", sun: "#ffd183", moon: "#f0e8ee",
            stars: "rgba(255,255,255,.54)", mountainFar: "#66586f", mountainNear: "#3e4056"
          },
          materials: { blur: 24, saturation: 1.4, brightness: 0.92, shadow: "floating" }
        }
      }
    }
  }
};

/**
 * Paketleri DOSYA BAŞINA dener: bozuk/eksik bir `theme.json` yalnız KENDİ paketini düşürür,
 * sunucuyu değil. Atlanan paket `onError` ile bildirilir (çağıran loglar). Geriye tek paket bile
 * kalmazsa gömülü varsayılana düşülür — panel her koşulda bir paletle açılır.
 * Geliştirme kapısı (`scripts/check-theme-packages.mjs`) hâlâ katıdır: orada bozuk paket
 * `onError` ile görünür ve zorunlu kimlikler eksik kalınca denetim düşer.
 */
export const loadThemePackages = async (
  directory: string,
  onError?: (name: string, error: unknown) => void
): Promise<ThemePackage[]> => {
  const packages: ThemePackage[] = [];
  let entries: Dirent[] = [];
  try {
    entries = await readdir(directory, { withFileTypes: true });
  } catch (error) {
    onError?.(directory, error);
  }
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    try {
      const path = resolve(directory, entry.name, "theme.json");
      const text = await readFile(path, "utf8");
      if (Buffer.byteLength(text, "utf8") > 64 * 1024) throw new Error("tema paketi 64 KB sınırını aşıyor.");
      const theme = validateThemePackage(JSON.parse(text));
      if (packages.some((item) => item.id === theme.id)) throw new Error(`${theme.id}: yinelenen tema kimliği.`);
      packages.push(theme);
    } catch (error) {
      onError?.(entry.name, error);
    }
  }
  return packages.length > 0 ? packages : [embeddedThemePackage];
};
