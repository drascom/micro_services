import { readFile } from "node:fs/promises";
import { writeJsonAtomic } from "./atomic-file.js";

export interface HomeGroupItem {
  deviceId: string;
  controlId: string;
}

export interface HomeGroup {
  id: string;
  name: string;
  items: HomeGroupItem[];
  /**
   * Oda ikonu — İSTEĞE BAĞLIDIR ve öyle kalmalıdır. Alan yoksa panel ikonu odanın kendi
   * cihazlarından türetir (`genCardModelForGroup`); yani ikonsuz eski kayıt eksik değil,
   * yalnız "seçim yapılmamış" demektir. Alan yokken JSON'a da YAZILMAZ: bugünkü
   * `home-groups.json` dosyaları bayt bayt aynı kalır.
   */
  icon?: string;
}

export const homeGroupDeviceControlId = "@device";
export const maxHomeGroups = 32;
export const maxHomeGroupItems = 64;
export const maxHomeGroupNameLength = 64;
/** İkon bir dosya yolu ya da serbest metin değil, panelin ikon setindeki bir ad. */
const homeGroupIconPattern = /^[a-z0-9-]{1,32}$/;

export interface ValidateHomeGroupsOptions {
  /**
   * Diskten okurken açılır: BOZUK KAYIT ATLANIR, dosyanın tamamı reddedilmez.
   * Sebebi somut — tek bir bozuk satır yüzünden evin BÜTÜN odalarının kaybolması kabul
   * edilemez. Yazma yolu (API) hoşgörüsüz kalır: kullanıcının gönderdiği bozuk veri
   * sessizce yutulmaz, hata olarak geri döner.
   */
  lenient?: boolean;
}

const readHomeGroup = (entry: unknown): HomeGroup => {
  if (typeof entry !== "object" || entry === null || Array.isArray(entry)) {
    throw new Error("Ana ekran grubu geçersiz.");
  }
  const candidate = entry as Record<string, unknown>;
  const id = typeof candidate.id === "string" ? candidate.id.trim().toLowerCase() : "";
  if (!/^[a-z0-9-]{1,64}$/.test(id)) throw new Error("Ana ekran grubu kimliği geçersiz.");
  const name = typeof candidate.name === "string" ? candidate.name.trim() : "";
  if (!name || name.length > maxHomeGroupNameLength) {
    throw new Error("Ana ekran grubu adı geçersiz.");
  }
  if (!Array.isArray(candidate.items) || candidate.items.length > maxHomeGroupItems) {
    throw new Error("Ana ekran grubu öğeleri geçersiz.");
  }
  const items: HomeGroupItem[] = [];
  const keys = new Set<string>();
  for (const item of candidate.items) {
    if (typeof item !== "object" || item === null || Array.isArray(item)) {
      throw new Error("Ana ekran grubu öğesi geçersiz.");
    }
    const member = item as Record<string, unknown>;
    const deviceId = typeof member.deviceId === "string" ? member.deviceId.trim().toLowerCase() : "";
    const controlId = typeof member.controlId === "string" ? member.controlId.trim().toLowerCase() : "";
    if (!/^0x[0-9a-f]{16}$/.test(deviceId) || !/^[a-z0-9:_@-]{1,64}$/.test(controlId)) {
      throw new Error("Ana ekran grubu öğesi UID veya kontrol kimliği geçersiz.");
    }
    const key = `${deviceId}|${controlId}`;
    if (keys.has(key)) continue;
    keys.add(key);
    items.push({ deviceId, controlId });
  }
  /* İkon YOKSA hata değil. `undefined`, `null` ve boş dize "seçim yapılmadı" demektir ve
     alan hiç üretilmez — eski kayıtların okunmasının tek koşulu budur. */
  const rawIcon = candidate.icon;
  if (rawIcon === undefined || rawIcon === null || rawIcon === "") return { id, name, items };
  const icon = typeof rawIcon === "string" ? rawIcon.trim().toLowerCase() : "";
  if (!homeGroupIconPattern.test(icon)) throw new Error("Ana ekran grubu ikonu geçersiz.");
  return { id, name, items, icon };
};

export const validateHomeGroups = (
  value: unknown,
  options: ValidateHomeGroupsOptions = {}
): HomeGroup[] => {
  const lenient = options.lenient === true;
  if (!Array.isArray(value)) {
    if (lenient) return [];
    throw new Error("Ana ekran grupları geçersiz.");
  }
  if (value.length > maxHomeGroups && !lenient) {
    throw new Error("Ana ekran grupları geçersiz.");
  }
  const result: HomeGroup[] = [];
  const ids = new Set<string>();
  for (const entry of value) {
    if (result.length >= maxHomeGroups) break;
    let group: HomeGroup;
    try {
      group = readHomeGroup(entry);
    } catch (error) {
      if (!lenient) throw error;
      console.error(`Ana ekran grubu okunamadı, atlandı: ${String(error)}`);
      continue;
    }
    if (ids.has(group.id)) continue;
    ids.add(group.id);
    result.push(group);
  }
  return result;
};

export const removeDeviceFromHomeGroups = (groups: HomeGroup[], deviceId: string): HomeGroup[] => {
  const normalizedId = deviceId.trim().toLowerCase();
  return groups.map((group) => ({
    ...group,
    items: group.items.filter((item) => item.deviceId !== normalizedId)
  }));
};

export class HomeGroupsStore {
  constructor(private readonly path: string) {}

  /**
   * Diskten okuma HOŞGÖRÜLÜDÜR (`lenient`): bozuk tek kayıt atlanır, kalan odalar açılır.
   * Yazma yolu (`save`) hoşgörüsüz kalır — bkz. `ValidateHomeGroupsOptions`.
   */
  async get(): Promise<HomeGroup[]> {
    try {
      return validateHomeGroups(JSON.parse(await readFile(this.path, "utf8")), { lenient: true });
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
      throw error;
    }
  }

  async save(value: unknown): Promise<HomeGroup[]> {
    const groups = validateHomeGroups(value);
    await writeJsonAtomic(this.path, groups, { mode: 0o600 });
    return groups;
  }

  async removeDevice(deviceId: string): Promise<HomeGroup[]> {
    return this.save(removeDeviceFromHomeGroups(await this.get(), deviceId));
  }
}
