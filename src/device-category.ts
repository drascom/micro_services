/**
 * Cihaz sınıflandırması iki katmandır:
 *
 * 1. Tahmin — standart Zigbee tanım verisi olan `definition.exposes[].type` alanına dayanır.
 *    Bu alan hem Zigbee2MQTT bridge yükünde (shadow modu) hem de zigbee-herdsman-converters
 *    tanımlarında (direct modu) bulunur; Home Assistant da aynı sinyali kullanır. Model listesi,
 *    satıcıya özgü özellik adı ya da kullanıcının verdiği ad ölçüt DEĞİLDİR — öyle bir kural
 *    yalnız tek bir evde çalışır.
 * 2. Kullanıcının seçtiği rol — tahmini ezer. Donanım "anahtar" derken kullanıcı onu lamba
 *    olarak kullanıyor olabilir (lambayı süren röle/LED kontrolcüsü); kararı kullanıcı verir.
 */
export type DeviceCategory = "light" | "switch" | "cover" | "lock" | "climate" | "fan" | "unknown";

/** Kullanıcının elle seçebildiği rol. `auto` tahmini olduğu gibi bırakır. */
export type DeviceRole = "auto" | "light" | "switch";

export const deviceRoles: readonly DeviceRole[] = ["auto", "light", "switch"];

/**
 * Öncelik sırası: daha özel donanım daha genel olanı yener. Çok uçlu bir cihaz hem `light` hem
 * `switch` bildirebilir; o durumda lamba kazanır, çünkü `switch` her açılıp kapanan uçta çıkar.
 */
const categoryPriority: DeviceCategory[] = ["lock", "cover", "climate", "fan", "light", "switch"];

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Tanımdaki üst düzey expose tiplerinden cihaz sınıfını çıkarır. Tanım yoksa ya da yalnız
 * jenerik tipler (`binary`, `numeric`, `enum`, `composite`, …) varsa sonuç `unknown` olur —
 * eleme yapılmaz, karar kullanıcıya bırakılır.
 */
export function detectDeviceCategory(exposes: unknown): DeviceCategory {
  if (!Array.isArray(exposes)) return "unknown";
  const found = new Set<string>();
  for (const expose of exposes) {
    if (!isObject(expose)) continue;
    if (typeof expose.type === "string") found.add(expose.type);
  }
  return categoryPriority.find((category) => found.has(category)) ?? "unknown";
}

export function isDeviceRole(value: unknown): value is DeviceRole {
  return typeof value === "string" && (deviceRoles as readonly string[]).includes(value);
}

/** Kullanıcının seçtiği rol her zaman tahmini ezer; `auto` tahmini olduğu gibi bırakır. */
export function resolveDeviceCategory(detected: DeviceCategory, role: DeviceRole): DeviceCategory {
  return role === "auto" ? detected : role;
}
