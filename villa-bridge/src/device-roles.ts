import { mkdir, readFile } from "node:fs/promises";
import { dirname } from "node:path";
import { writeJsonAtomic } from "./atomic-file.js";
import { isDeviceRole, type DeviceRole } from "./device-category.js";

/**
 * Kullanıcının seçtiği roller. UID kuralı: anahtar her zaman değişmez IEEE adresidir, asla dost
 * isim değil. Çok kanallı cihazda rol kanal başınadır; anahtar `<ieee>:<kanal>` olur ve kanal
 * kimliği kontrol listesindekiyle (takma ad şemasıyla) aynıdır. Yalnız `<ieee>` biçimindeki eski
 * cihaz seviyesi kayıtlar geçerli kalır: kanalın kendi kaydı yoksa cihaz kaydına düşülür.
 * Dosya çözümlenen yapılandırmanın yanında tutulur (depoda değil).
 */
export type DeviceRoleMap = Map<string, DeviceRole>;

const deviceIdPattern = /^0x[0-9a-f]{16}$/;
const channelPattern = /^[a-z0-9_]{1,32}$/;
const roleKeyPattern = /^0x[0-9a-f]{16}(?::[a-z0-9_]{1,32})?$/;

export function normalizeDeviceRoleId(deviceId: string, channel?: string | null): string {
  const id = deviceId.trim().toLowerCase();
  if (!deviceIdPattern.test(id)) throw new Error("Cihaz kimliği geçersiz.");
  if (channel === undefined || channel === null || channel === "") return id;
  const normalizedChannel = String(channel).trim().toLowerCase();
  if (!channelPattern.test(normalizedChannel)) throw new Error("Kanal kimliği geçersiz.");
  return `${id}:${normalizedChannel}`;
}

/**
 * Kanalın etkin rolü: önce kanalın kendi kaydı, sonra eski cihaz seviyesi kayıt, sonra `auto`.
 */
export function resolveChannelRole(
  roles: DeviceRoleMap,
  deviceId: string,
  channel: string
): DeviceRole {
  const id = deviceId.trim().toLowerCase();
  return roles.get(`${id}:${channel.trim().toLowerCase()}`) ?? roles.get(id) ?? "auto";
}

export function validateDeviceRole(value: unknown): DeviceRole {
  if (!isDeviceRole(value)) throw new Error("Cihaz rolü geçersiz.");
  return value;
}

export async function loadDeviceRoles(path: string): Promise<DeviceRoleMap> {
  try {
    const parsed = JSON.parse(await readFile(path, "utf8")) as unknown;
    if (!isPlainObject(parsed)) return new Map();
    return new Map(
      Object.entries(parsed)
        .filter((entry): entry is [string, DeviceRole] =>
          roleKeyPattern.test(entry[0].toLowerCase()) && isDeviceRole(entry[1]) && entry[1] !== "auto"
        )
        .map(([id, role]) => [id.toLowerCase(), role])
    );
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return new Map();
    console.warn(`Cihaz rolleri okunamadı: ${error instanceof Error ? error.message : String(error)}`);
    return new Map();
  }
}

export async function saveDeviceRoles(path: string, roles: DeviceRoleMap): Promise<void> {
  await mkdir(dirname(path), { recursive: true });
  const sorted = Object.fromEntries(
    [...roles.entries()]
      .filter(([, role]) => role !== "auto")
      .sort(([left], [right]) => left.localeCompare(right, "en"))
  );
  await writeJsonAtomic(path, sorted, { mode: 0o600 });
}

/**
 * Rolü paylaşılan haritada günceller ve dosyaya atomik yazar. `auto` kaydı siler.
 * Kanal verilirse kayıt yalnız o kanala işler; verilmezse eski cihaz seviyesi davranış sürer.
 */
export async function setDeviceRole(
  path: string,
  roles: DeviceRoleMap,
  deviceId: string,
  value: unknown,
  channel?: string | null
): Promise<DeviceRole> {
  const key = normalizeDeviceRoleId(deviceId, channel);
  const role = validateDeviceRole(value);
  if (role === "auto") roles.delete(key);
  else roles.set(key, role);
  await saveDeviceRoles(path, roles);
  return role;
}

/** Cihaz silinince hem cihaz seviyesi kayıt hem tüm kanal kayıtları düşer. */
export async function removeDeviceRole(
  path: string,
  roles: DeviceRoleMap,
  deviceId: string
): Promise<void> {
  const id = deviceId.trim().toLowerCase();
  let removed = roles.delete(id);
  for (const key of [...roles.keys()]) {
    if (key.startsWith(`${id}:`)) removed = roles.delete(key) || removed;
  }
  if (!removed) return;
  await saveDeviceRoles(path, roles);
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
