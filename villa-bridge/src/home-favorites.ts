import { readFile } from "node:fs/promises";
import { writeJsonAtomic } from "./atomic-file.js";

export interface HomeFavorite {
  deviceId: string;
  controlId: string;
}

const homeFavoriteControlKinds = new Set([
  "switch",
  "fan",
  "siren",
  "cover",
  "position",
  "lock",
  "climate"
]);

export const isHomeFavoriteControlKind = (kind: string): boolean =>
  homeFavoriteControlKinds.has(kind);

export const validateHomeFavorites = (value: unknown): HomeFavorite[] => {
  if (!Array.isArray(value) || value.length > 64) throw new Error("Ana ekran favorileri geçersiz.");
  const result: HomeFavorite[] = [];
  const keys = new Set<string>();
  for (const item of value) {
    if (typeof item !== "object" || item === null || Array.isArray(item)) {
      throw new Error("Ana ekran favorisi geçersiz.");
    }
    const candidate = item as Record<string, unknown>;
    const deviceId = typeof candidate.deviceId === "string" ? candidate.deviceId.trim().toLowerCase() : "";
    const controlId = typeof candidate.controlId === "string" ? candidate.controlId.trim().toLowerCase() : "";
    if (!/^0x[0-9a-f]{16}$/.test(deviceId) || !/^[a-z0-9:_-]{1,64}$/.test(controlId)) {
      throw new Error("Ana ekran favorisi UID veya kontrol kimliği geçersiz.");
    }
    const key = `${deviceId}|${controlId}`;
    if (keys.has(key)) continue;
    keys.add(key);
    result.push({ deviceId, controlId });
  }
  return result;
};

export class HomeFavoritesStore {
  constructor(private readonly path: string) {}

  async get(): Promise<HomeFavorite[]> {
    try {
      return validateHomeFavorites(JSON.parse(await readFile(this.path, "utf8")));
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
      throw error;
    }
  }

  async save(value: unknown): Promise<HomeFavorite[]> {
    const favorites = validateHomeFavorites(value);
    await writeJsonAtomic(this.path, favorites, { mode: 0o600 });
    return favorites;
  }

  async removeDevice(deviceId: string): Promise<HomeFavorite[]> {
    const normalizedId = deviceId.trim().toLowerCase();
    return this.save((await this.get()).filter((favorite) => favorite.deviceId !== normalizedId));
  }
}
