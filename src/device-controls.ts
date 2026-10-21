import type { DeviceControlView, JsonObject, JsonScalar } from "./types.js";

export interface WritableFeature {
  property: string;
  name: string;
  type: string;
  parentType?: string;
  parentName?: string;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  values?: JsonScalar[];
  valueOn?: JsonScalar;
  valueOff?: JsonScalar;
  valueToggle?: JsonScalar;
  category?: string;
}

const onOff = (value: unknown): boolean | null => {
  if (value === "ON" || value === true) return true;
  if (value === "OFF" || value === false) return false;
  return null;
};

const suffixOf = (property: string, prefix: string): string =>
  property === prefix ? "main" : property.slice(prefix.length + 1);

const defaultChannelName = (deviceName: string, channel: string): string => {
  if (channel === "main") return deviceName;
  const number = channel.match(/\d+/)?.[0];
  return number ? `Kanal ${number}` : channel.toUpperCase();
};

/**
 * Ayrı adı olan tek şey aç/kapa kanalıdır: `state` / `state_lN` kanalları `<ieee>:<kanal>` takma adını
 * okur ve kimlikleri o kanaldır ("main", "l1"). Perde, kilit, fan, siren ve cihazın yayımladığı ikili
 * ayarlar `cover:...`, `switch:child_lock` gibi kimlik taşır; adlarını cihazın kendi etiketinden alır,
 * takma ad okunmaz.
 */
export const isNamedChannelControl = (control: Pick<DeviceControlView, "id" | "kind">): boolean =>
  control.kind === "switch" && !control.id.includes(":");

/**
 * Cihazın tek aç/kapa kanalı varsa o kanalın kimliği, yoksa `null`. Tek kanallı cihazda kanal adı diye
 * ayrı bir kavram yoktur — cihaz adı kanalın da adıdır; birden çok kanalda her kanal kendi adını taşır.
 */
export const soleSwitchChannelId = (
  controls: Array<Pick<DeviceControlView, "id" | "kind">>
): string | null => {
  const channels = controls.filter(isNamedChannelControl);
  return channels.length === 1 ? channels[0].id : null;
};

const clampByte = (value: number): number => Math.round(Math.max(0, Math.min(255, value)));

const rgbToHex = (red: number, green: number, blue: number): string =>
  `#${[red, green, blue].map((value) => clampByte(value).toString(16).padStart(2, "0")).join("")}`;

const xyToHex = (x: number, y: number): string => {
  if (!Number.isFinite(x) || !Number.isFinite(y) || y <= 0) return "#ffffff";
  const X = x / y;
  const Y = 1;
  const Z = (1 - x - y) / y;
  let red = X * 1.656492 - Y * 0.354851 - Z * 0.255038;
  let green = -X * 0.707196 + Y * 1.655397 + Z * 0.036152;
  let blue = X * 0.051713 - Y * 0.121364 + Z * 1.01153;
  const gamma = (value: number): number =>
    value <= 0.0031308 ? 12.92 * value : (1.055 * Math.pow(Math.max(0, value), 1 / 2.4)) - 0.055;
  red = gamma(red);
  green = gamma(green);
  blue = gamma(blue);
  const maximum = Math.max(red, green, blue);
  if (maximum > 1) {
    red /= maximum;
    green /= maximum;
    blue /= maximum;
  }
  return rgbToHex(red * 255, green * 255, blue * 255);
};

const hsToHex = (hue: number, saturation: number): string => {
  const h = ((hue % 360) + 360) % 360;
  const s = Math.max(0, Math.min(100, saturation)) / 100;
  const chroma = s;
  const segment = h / 60;
  const secondary = chroma * (1 - Math.abs((segment % 2) - 1));
  const [red, green, blue] =
    segment < 1 ? [chroma, secondary, 0] :
    segment < 2 ? [secondary, chroma, 0] :
    segment < 3 ? [0, chroma, secondary] :
    segment < 4 ? [0, secondary, chroma] :
    segment < 5 ? [secondary, 0, chroma] :
    [chroma, 0, secondary];
  const match = 1 - chroma;
  return rgbToHex((red + match) * 255, (green + match) * 255, (blue + match) * 255);
};

/**
 * Rengin `#rrggbb` karşılığı. Zigbee2MQTT rengi nesne olarak bildirir (`{x,y}`, `{hue,saturation}`);
 * panelin ve otomasyonun paylaştığı tek renk dili ise hex'tir, o yüzden dönüşüm dışarı da açıktır.
 */
export const colorHex = (value: unknown): string => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return "#ffffff";
  const color = value as Record<string, unknown>;
  if (typeof color.hex === "string" && /^#[0-9a-f]{6}$/i.test(color.hex)) return color.hex.toLowerCase();
  if (typeof color.x === "number" && typeof color.y === "number") return xyToHex(color.x, color.y);
  if (typeof color.hue === "number" && typeof color.saturation === "number") {
    return hsToHex(color.hue, color.saturation);
  }
  return "#ffffff";
};

export const hexToXy = (hex: string): { x: number; y: number } => {
  const match = /^#([0-9a-f]{6})$/i.exec(hex);
  if (!match) throw new Error("Geçersiz renk değeri.");
  const component = (offset: number): number => {
    const value = Number.parseInt(match[1].slice(offset, offset + 2), 16) / 255;
    return value > 0.04045 ? Math.pow((value + 0.055) / 1.055, 2.4) : value / 12.92;
  };
  const red = component(0);
  const green = component(2);
  const blue = component(4);
  const X = red * 0.664511 + green * 0.154324 + blue * 0.162028;
  const Y = red * 0.283881 + green * 0.668433 + blue * 0.047685;
  const Z = red * 0.000088 + green * 0.07231 + blue * 0.986039;
  const sum = X + Y + Z;
  return sum === 0 ? { x: 0.3127, y: 0.329 } : {
    x: Number((X / sum).toFixed(4)),
    y: Number((Y / sum).toFixed(4))
  };
};

/** `#rrggbb` — panelin, otomasyonun ve kaydetme doğrulamasının paylaştığı tek renk biçimi. */
export const hexColorPattern = /^#[0-9a-f]{6}$/i;
export const isHexColor = (value: unknown): value is string =>
  typeof value === "string" && hexColorPattern.test(value);

/** Kumandaya yazılabilir değer: skaler ya da rengin xy karşılığı. */
export type ControlCommandValue = JsonScalar | { x: number; y: number };

export interface ControlValueOptions {
  /**
   * Panel aç/kapat değerini `boolean` yollar ve başka bir şey kabul edilmez; otomasyon kuralı ise
   * kumandanın kendi değerini (`valueOn`/`valueOff`/`valueToggle`) taşır. Tek fark budur.
   */
  booleanSwitch?: boolean;
}

const switchLikeKinds = new Set(["switch", "fan", "siren"]);
const listKinds = new Set(["cover", "lock", "select"]);

/**
 * Kumandaya yazılacak değerin **tek** doğrulama/dönüştürme yolu. Panelin `/api/devices/:id/command`
 * rotası da, otomasyon motoru da buradan geçer: renk `#rrggbb` olarak doğrulanıp xy'ye çevrilir,
 * sayısal değer `min`/`max`'a kırpılıp `step`'e yuvarlanır, liste kumandaları kendi değer listesine
 * denetlenir. Kabul edilmeyen değer anlaşılır bir hata fırlatır; hata metinleri rotanın bugünkü
 * yanıtlarıyla birebir aynıdır.
 */
export const normalizeControlValue = (
  control: Pick<
    DeviceControlView,
    "kind" | "min" | "max" | "step" | "values" | "valueOn" | "valueOff" | "valueToggle"
  >,
  value: unknown,
  options: ControlValueOptions = {}
): ControlCommandValue => {
  if (switchLikeKinds.has(control.kind)) {
    const valueOn = control.valueOn ?? "ON";
    const valueOff = control.valueOff ?? "OFF";
    if (typeof value === "boolean") return value ? valueOn : valueOff;
    if (options.booleanSwitch) throw new Error("Aç/kapat değeri geçersiz.");
    if (value === valueOn) return valueOn;
    if (value === valueOff) return valueOff;
    if (control.valueToggle !== undefined && value === control.valueToggle) return control.valueToggle;
    throw new Error("Aç/kapat değeri geçersiz.");
  }
  if (control.kind === "color") {
    if (!isHexColor(value)) throw new Error("Renk değeri geçersiz.");
    return hexToXy(value);
  }
  if (listKinds.has(control.kind)) {
    if (
      (typeof value !== "string" && typeof value !== "number" && typeof value !== "boolean")
      || (control.values?.length && !control.values.includes(value))
    ) {
      throw new Error("Seçilen cihaz komutu geçersiz.");
    }
    return value;
  }
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error("Sayısal denetim değeri geçersiz.");
  }
  const clamped = Math.min(control.max ?? value, Math.max(control.min ?? value, value));
  const step = control.step;
  return step && step > 0
    ? Number((Math.round((clamped - (control.min ?? 0)) / step) * step + (control.min ?? 0)).toFixed(6))
    : clamped;
};

/** Sayısal kumandalar — oranlı eşleme ve kaydetme doğrulaması aynı kümeyi paylaşır. */
export const numericControlKinds: ReadonlySet<string> = new Set([
  "level",
  "temperature",
  "number",
  "position",
  "climate"
]);

type RangeControl = Pick<DeviceControlView, "min" | "max">;

/**
 * Kumandanın kendi ölçeği. `min`/`max` bildirmeyen kumanda 0–100 sayılır (panelde de aynı kural);
 * ölçek çökmüşse (max ≤ min) bir birimlik yapay aralık kurulur ki bölme sıfıra düşmesin.
 */
export const controlValueRange = (control: RangeControl): { min: number; max: number } => {
  const min = typeof control.min === "number" && Number.isFinite(control.min) ? control.min : 0;
  const max = typeof control.max === "number" && Number.isFinite(control.max) ? control.max : 100;
  return max > min ? { min, max } : { min, max: min + 1 };
};

/**
 * Ham değerin kendi aralığındaki yüzdesi (0–100). Sayıya çevrilemeyen değerde `null` döner.
 * Panelin gösterdiği yüzdenin aynısıdır; tek fark burada tam sayıya yuvarlanmamasıdır — canlı
 * takipte ara değerler kaybolmasın diye kesir korunur, yuvarlama hedefin `step`'inde yapılır.
 */
export const controlValuePercent = (control: RangeControl, value: unknown): number | null => {
  const numeric = typeof value === "number"
    ? value
    : typeof value === "string" && value.trim() !== "" ? Number(value) : Number.NaN;
  if (!Number.isFinite(numeric)) return null;
  const { min, max } = controlValueRange(control);
  const clamped = Math.min(max, Math.max(min, numeric));
  return ((clamped - min) / (max - min)) * 100;
};

/**
 * Yüzdenin hedef kumandadaki ham karşılığı. Kırpma ve `step` yuvarlaması ortak normalizasyondan
 * geçer; adım bildirmeyen kumandada değer tam sayıya oturur (panelin `automationValueRaw`'ıyla
 * aynı kural — iki cihazın ölçeği farklı olduğu için ham değer asla kopyalanmaz).
 */
export const controlValueFromPercent = (
  control: Parameters<typeof normalizeControlValue>[0],
  percent: number
): number => {
  const { min, max } = controlValueRange(control);
  const clamped = Math.min(100, Math.max(0, Number.isFinite(percent) ? percent : 0));
  const exact = min + (max - min) * clamped / 100;
  const step = control.step;
  const snapped = step && step > 0 ? exact : Math.round(exact);
  const normalized = normalizeControlValue(control, snapped);
  return typeof normalized === "number" ? normalized : snapped;
};

/**
 * Otomasyon yolu için hoşgörülü sarmalayıcı: normalize edilebilen değer panelinkiyle aynı yoldan
 * geçer, edilemeyen değer olduğu gibi gider. Diskteki eski kurallar bir doğrulama sıkılaşmasıyla
 * sessizce çalışmaz hâle gelmesin diye eylem burada reddedilmez.
 */
export const normalizeControlValueOrRaw = (
  control: Parameters<typeof normalizeControlValue>[0],
  value: unknown
): unknown => {
  try {
    return normalizeControlValue(control, value);
  } catch {
    return value;
  }
};

export function deviceControls(
  id: string,
  deviceName: string,
  writableFeatures: Array<string | WritableFeature>,
  state: JsonObject,
  aliases: Map<string, string>
): DeviceControlView[] {
  const descriptors = writableFeatures.map((feature): WritableFeature =>
    typeof feature === "string"
      ? { property: feature, name: feature, type: "" }
      : feature
  );
  const writable = new Set(descriptors.map((feature) => feature.property));
  const descriptorByProperty = new Map(descriptors.map((feature) => [feature.property, feature]));
  const available = new Set([...writable, ...Object.keys(state)]);
  const controls: DeviceControlView[] = [];
  const added = new Set<string>();
  const add = (control: DeviceControlView): void => {
    if (added.has(control.property)) return;
    added.add(control.property);
    const colorWritable = control.kind === "color"
      && descriptors.some((feature) => ["color", "color_xy", "color_hs"].includes(feature.property));
    controls.push({ ...control, writable: writable.has(control.property) || colorWritable });
  };
  const descriptorKind = (feature: WritableFeature | undefined): string =>
    `${feature?.parentType ?? ""} ${feature?.parentName ?? ""}`.toLowerCase();
  /**
   * Siren tespiti. Birinci ölçüt her zamanki gibi sarmalayan tipli expose'un adı/tipidir.
   *
   * İkinci ölçüt bir boşluğu kapatır: Zigbee sözleşmesinde sireni saran **tipli** bir expose
   * yoktur (`light`, `cover`, `fan`, `climate`, `lock` var; `siren` yok). Siren çaldıran cihazlar
   * alarmı üst düzey jenerik bir `binary("alarm", ...)` olarak yayımlar, dolayısıyla
   * `parentType`/`parentName` boş kalır ve kanal `switch` sınıfına düşerdi. Bu yalnız görsel bir
   * kusur değil: §8.1'in kilit/siren yasağı (`forbiddenAutomationControlKinds`) `kind` üzerinden
   * çalışır, `switch` görünen bir siren otomasyonla öttürülebilir hâle gelirdi.
   *
   * Kural bilerek dar tutuldu — özellik tipi `binary` ve adı **tam olarak** `alarm` olmalı.
   * Kütüphanedeki yazılabilir `binary("alarm", ...)` expose'larının tamamı siren/alarm çaldıran
   * cihazlara ait (adurosmart, climax, develco, neo, senoro, woox ve Tuya sirenleri). Duman/su
   * dedektörlerindeki `alarm` salt okunurdur ve kumanda listesine hiç girmez; buraya yalnız
   * yazılabilir özellikler gelir. `alarm_time`, `alarm_volume` gibi ayarlar ad tam eşleşmediği
   * için kapsam dışıdır.
   */
  const isSirenFeature = (feature: WritableFeature | undefined): boolean =>
    /\bsiren\b/.test(descriptorKind(feature))
    || (feature?.type === "binary" && feature.property === "alarm");
  const isSpecial = (feature: WritableFeature | undefined): boolean =>
    /\b(cover|climate|lock|fan)\b/.test(descriptorKind(feature)) || isSirenFeature(feature);
  const isPrimaryContext = (feature: WritableFeature | undefined): boolean =>
    /\b(light|switch|cover|climate|lock|fan)\b/.test(descriptorKind(feature)) || isSirenFeature(feature);
  /**
   * Ayar mı, asıl işlev mi? Ölçüt tanım verisidir, özellik adı değil:
   *
   * 1. Expose açıkça `category: "config" | "diagnostic"` diyorsa ayardır.
   * 2. Zigbee tanım sözleşmesinde cihazın asıl işlevi **tipli** bir expose'un içinde yayımlanır
   *    (`light`, `switch`, `cover`, `lock`, `fan`, `climate`, `siren`). Üst düzeyde tek başına
   *    duran jenerik expose'lar (`binary`, `numeric`, `enum`, `text`) cihaz ayarı ya da
   *    tanılamasıdır: hassasiyet, algılama mesafesi, çocuk kilidi, "cihazı bul" düğmesi… Birçok
   *    tanım `category` alanını doldurmaz; sarmalayan tipli expose'un yokluğu ölçütün kendisidir.
   *
   * Ayar kaybolmaz — yalnız ana kontrol olmaktan çıkar, listenin ayar bölümüne iner.
   * `state`/`brightness`/`color_temp` kanalları bu kuralın dışındadır: onlar sözleşme gereği
   * zaten asıl işlevdir, tanım onları sarmalamamış olsa bile. Yazılabilir `alarm` da öyledir —
   * gerekçesi `isSirenFeature`'da.
   */
  const isDeviceSetting = (feature: WritableFeature | undefined): boolean =>
    feature?.category === "config"
    || feature?.category === "diagnostic"
    || !isPrimaryContext(feature);
  const isSettingCategory = (category: string | undefined): boolean =>
    category === "config" || category === "diagnostic";
  const labelFor = (feature: WritableFeature, fallback: string): string =>
    feature.name && feature.name !== feature.property
      ? feature.name.replaceAll("_", " ")
      : fallback;
  const scalarValue = (property: string): string | number | boolean | null => {
    const value = state[property];
    return typeof value === "string" || typeof value === "number" || typeof value === "boolean"
      ? value
      : null;
  };

  for (const property of [...available].filter((key) => key === "state" || key.startsWith("state_")).sort()) {
    const descriptor = descriptorByProperty.get(property);
    if (isSpecial(descriptor)) continue;
    const value = onOff(state[property]);
    if (value === null && !writable.has(property)) continue;
    const channel = suffixOf(property, "state");
    const valueOn = descriptor?.valueOn ?? "ON";
    const valueOff = descriptor?.valueOff ?? "OFF";
    // Zigbee2MQTT sözleşmesi: ON/OFF kullanan yazılabilir bir durum TOGGLE komutunu da kabul eder.
    const canToggle = writable.has(property) && valueOn === "ON" && valueOff === "OFF";
    add({
      id: channel,
      property,
      name: aliases.get(`${id}:${channel}`) ?? defaultChannelName(deviceName, channel),
      kind: "switch",
      value,
      valueOn,
      valueOff,
      valueToggle: descriptor?.valueToggle ?? (canToggle ? "TOGGLE" : undefined),
      adminOnly: isSettingCategory(descriptor?.category)
    });
  }

  for (const property of [...available].filter((key) => key === "brightness" || key.startsWith("brightness_")).sort()) {
    const raw = state[property];
    if (typeof raw !== "number" && !writable.has(property)) continue;
    const channel = suffixOf(property, "brightness");
    const descriptor = descriptorByProperty.get(property);
    add({
      id: `${channel}:brightness`,
      property,
      name: channel === "main"
        ? labelFor(descriptor ?? { property, name: property, type: "" }, "Parlaklık")
        : `${aliases.get(`${id}:${channel}`) ?? defaultChannelName(deviceName, channel)} parlaklığı`,
      kind: "level",
      value: typeof raw === "number" ? raw : null,
      min: descriptor?.min ?? 1,
      max: descriptor?.max ?? 254,
      step: descriptor?.step,
      unit: descriptor?.unit,
      adminOnly: isSettingCategory(descriptor?.category)
    });
  }

  for (const property of [...available].filter((key) => key === "color_temp" || key.startsWith("color_temp_")).sort()) {
    const raw = state[property];
    if (typeof raw !== "number" && !writable.has(property)) continue;
    const channel = suffixOf(property, "color_temp");
    const descriptor = descriptorByProperty.get(property);
    add({
      id: `${channel}:temperature`,
      property,
      name: channel === "main" ? "Işık sıcaklığı" : `${aliases.get(`${id}:${channel}`) ?? defaultChannelName(deviceName, channel)} sıcaklığı`,
      kind: "temperature",
      value: typeof raw === "number" ? raw : null,
      min: descriptor?.min ?? 153,
      max: descriptor?.max ?? 500,
      step: descriptor?.step,
      unit: descriptor?.unit,
      adminOnly: isSettingCategory(descriptor?.category)
    });
  }

  if (descriptors.some((feature) => ["color", "color_xy", "color_hs"].includes(feature.property))) {
    add({
      id: "main:color",
      property: "color",
      name: "Renk",
      kind: "color",
      value: state.color === undefined ? null : colorHex(state.color)
    });
  }

  for (const feature of descriptors) {
    const property = feature.property;
    if (added.has(property) || ["color_xy", "color_hs"].includes(property)) continue;
    const context = descriptorKind(feature);
    const raw = scalarValue(property);
    const common = {
      property,
      name: labelFor(feature, property.replaceAll("_", " ")),
      value: raw,
      min: feature.min,
      max: feature.max,
      step: feature.step,
      unit: feature.unit,
      values: feature.values,
      valueOn: feature.valueOn,
      valueOff: feature.valueOff,
      valueToggle: feature.valueToggle,
      adminOnly: isDeviceSetting(feature)
    };
    if (/\bcover\b/.test(context)) {
      add({
        ...common,
        id: `cover:${property}`,
        kind: property.includes("position") ? "position" : "cover",
        min: feature.min ?? (property.includes("position") ? 0 : undefined),
        max: feature.max ?? (property.includes("position") ? 100 : undefined),
        values: feature.values ?? (property.includes("state") ? ["OPEN", "STOP", "CLOSE"] : undefined)
      });
      continue;
    }
    if (/\bclimate\b/.test(context)) {
      const setpoint = /setpoint|target_temperature/.test(property);
      add({
        ...common,
        id: `climate:${property}`,
        kind: setpoint ? "climate" : feature.values?.length ? "select" : "number"
      });
      continue;
    }
    if (/\block\b/.test(context)) {
      add({
        ...common,
        id: `lock:${property}`,
        kind: "lock",
        values: feature.values ?? ["LOCK", "UNLOCK"]
      });
      continue;
    }
    if (/\bfan\b/.test(context)) {
      add({
        ...common,
        id: `fan:${property}`,
        kind: property === "state" || property.startsWith("state_") || property.endsWith("_state")
          ? "fan"
          : feature.values?.length ? "select" : "number"
      });
      continue;
    }
    if (isSirenFeature(feature)) {
      add({
        ...common,
        id: `siren:${property}`,
        kind: "siren",
        value: onOff(raw),
        valueOn: feature.valueOn ?? "ON",
        valueOff: feature.valueOff ?? "OFF"
      });
      continue;
    }
    if (feature.values?.length) {
      add({ ...common, id: `select:${property}`, kind: "select" });
      continue;
    }
    if (feature.type === "numeric" || feature.min !== undefined || feature.max !== undefined) {
      add({ ...common, id: `number:${property}`, kind: "number" });
      continue;
    }
    if (feature.type === "binary") {
      add({
        ...common,
        id: `switch:${property}`,
        kind: "switch",
        value: onOff(raw),
        valueOn: feature.valueOn ?? "ON",
        valueOff: feature.valueOff ?? "OFF"
      });
    }
  }

  const priority = (control: DeviceControlView): number => {
    const base = control.kind === "switch" || control.kind === "cover" ? 0
      : control.kind === "level" || control.kind === "position" ? 1
      : control.kind === "temperature" || control.kind === "climate" ? 2
      : control.kind === "color" || control.id.startsWith("climate:") ? 3
      : control.kind === "lock" ? 4
      : control.kind === "fan" ? 5
      : control.kind === "siren" ? 6
      : 10;
    return base + (control.adminOnly ? 100 : 0);
  };
  return controls.sort((left, right) => priority(left) - priority(right));
}
