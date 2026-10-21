import type { DeviceCategory, DeviceRole } from "./device-category.js";

export type JsonObject = Record<string, unknown>;
export type JsonScalar = string | number | boolean;

export interface BridgeDeviceDefinition {
  model?: string;
  vendor?: string;
  description?: string;
  exposes?: unknown[];
  options?: unknown[];
  ota?: boolean;
  supports_ota?: boolean;
}

export interface BridgeDevice {
  ieee_address?: string;
  friendly_name?: string;
  model_id?: string;
  type?: string;
  disabled?: boolean;
  interview_completed?: boolean;
  interviewing?: boolean;
  supported?: boolean;
  network_address?: number;
  definition?: BridgeDeviceDefinition;
  endpoints?: Record<string, unknown>;
  endpoint_names?: Record<string, string>;
  date_code?: string;
  manufacturer?: string;
  power_source?: string;
  software_build_id?: string;
  configured_options?: {
    transition?: number;
    debounce?: number;
    retain?: boolean;
  };
}

export interface BridgeGroup {
  id?: number;
  friendly_name?: string;
  members?: Array<{ ieee_address?: string; endpoint?: number }>;
  scenes?: Array<{ id?: number; name?: string }>;
}

export interface DeviceView {
  id: string;
  sourceName: string;
  name: string;
  type: string;
  /** Arayüzde geçerli olan sınıf: kullanıcı rol seçtiyse o, seçmediyse tahmin. */
  category: DeviceCategory;
  /** Tanımdan çıkan tahmin — rolü "Otomatik"e döndürmek için saklanır. */
  detectedCategory: DeviceCategory;
  /** Kullanıcının seçimi; `auto` tahmini olduğu gibi bırakır. */
  role: DeviceRole;
  model: string | null;
  image: DeviceImageView;
  vendor: string | null;
  description: string | null;
  supported: boolean;
  interviewCompleted: boolean;
  preparing: boolean;
  availability: "online" | "offline" | "unknown";
  lastSeen: string | null;
  stateUpdatedAt: string | null;
  /**
   * Son bilinen bağlantı kalitesi (LQI, 0-255). Cihaz durumunun parçası değildir: gelen mesaj
   * yolundan yakalanıp cihaz başına saklanır. **Veri yoksa alan hiç bulunmaz** — arayüz "—" gösterir,
   * uydurma değer konmaz.
   */
  linkquality?: number;
  /** Zigbee güç kaynağı (`Battery`, `Mains (single phase)`, …). Bilinmiyorsa alan bulunmaz. */
  powerSource?: string;
  /** Kısa NWK adresi. Kalıcı kimlik değildir (UID kuralı), yalnızca gösterim içindir. */
  networkAddress?: number;
  otaSupported: boolean;
  options: {
    transition: number;
    debounce: number;
    retain: boolean;
  };
  endpoints?: DeviceEndpointView[];
  features: string[];
  actionTypes?: string[];
  buttons?: DeviceButtonView[];
  lastAction?: DeviceLastActionView | null;
  alerts: DeviceAlertView[];
  controls: DeviceControlView[];
  /**
   * Cihazın yayımladığı ama yazılamayan tüm ölçümler. Kumanda değildir, salt gösterimdir:
   * rol süzgecine takılmaz. Kural jeneriktir — cihaza/özelliğe özel liste yoktur.
   */
  readings: DeviceReadingView[];
  state: JsonObject;
}

export interface DeviceReadingView {
  property: string;
  /** Gösterim etiketi: expose `label` ?? `name`, alt çizgiler boşluğa çevrili. */
  name: string;
  /** Z2M expose tipi: `numeric` | `binary` | `enum` | `text` … Tanım yoksa boş dize. */
  type: string;
  unit?: string;
  /**
   * İkili ölçümün cihaz tarafındaki iki ucu (`ON`/`OFF`, `open`/`closed`, `true`/`false`…).
   * Panel ham dizeyi göstermek yerine bunlarla eşleyip dile çevirir.
   */
  valueOn?: JsonScalar;
  valueOff?: JsonScalar;
  /** `state`ten okunan son değer; henüz gelmediyse `null`. */
  value: JsonScalar | null;
  /** `config` | `diagnostic` — panelde ikinci sınıf gösterim için. */
  category?: string;
  /** Üst composite'in adı; çok endpoint'li cihazda aynı adlı ölçümleri ayırır. */
  parentName?: string;
}

export interface DeviceAlertView {
  code: "low_battery" | "smoke" | "carbon_monoxide";
  severity: "warning" | "critical";
  value?: number;
  threshold?: number;
}

export interface DeviceEndpointBindingView {
  cluster: string;
  targetType: "device" | "group";
  targetId: string;
  targetEndpoint: number | null;
}

export interface DeviceEndpointView {
  id: number;
  name: string;
  inputClusters: Array<string | number>;
  outputClusters: Array<string | number>;
  bindings: DeviceEndpointBindingView[];
}

export interface DeviceImageCandidate {
  model: string;
  label: "catalogMatch" | "miniModule" | "switchModule" | "wallSwitch" | "otherSwitch";
}

export interface DeviceImageView {
  model: string | null;
  /**
   * Bu modelin görseli GERÇEKTEN var mı? `true` önbellekte duruyor, `false` upstream'de yok
   * (panel `<img>` kurmaz, istek hiç atılmaz), `null` henüz denenmedi — panel eskisi gibi dener.
   * Değeri `resolveDeviceImage` bilmez; HTTP katmanı görsel önbelleğinden doldurur.
   */
  available: boolean | null;
  candidates: DeviceImageCandidate[];
  selectionRequired: boolean;
  userSelected: boolean;
  preferenceKey: string;
}

export interface DeviceButtonActionView {
  /** Ham Zigbee `action` değeri — kanonik kimlik budur, otomasyonlar buna bağlanır. */
  action: string;
  /** İnsan diline çevrilebilir basış kimliği (`single`, `double`, `hold`, `release`, …). */
  press: string;
}

export interface DeviceButtonView {
  id: string;
  name: string;
  kind: "numbered" | "named" | "single" | "ungrouped";
  number: number | null;
  key: string | null;
  actions: DeviceButtonActionView[];
}

export interface DeviceLastActionView {
  action: string;
  buttonId: string | null;
  at: string;
}

export interface DeviceControlView {
  id: string;
  property: string;
  name: string;
  kind:
    | "switch"
    | "level"
    | "temperature"
    | "color"
    | "cover"
    | "position"
    | "climate"
    | "lock"
    | "fan"
    | "siren"
    | "number"
    | "select";
  value: boolean | number | string | null;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  values?: JsonScalar[];
  valueOn?: JsonScalar;
  valueOff?: JsonScalar;
  valueToggle?: JsonScalar;
  /**
   * `true` when the expose contract accepts writes for this property. Some read-only binary
   * states are still rendered as controls for a compact device view; automation targets must
   * not mistake those states for writable commands.
   */
  writable?: boolean;
  adminOnly?: boolean;
  /**
   * Aç/kapa kanalları için sınıf bilgisi. Lamba↔anahtar ayrımı kanal başınadır: çok kanallı bir
   * duvar anahtarında bir kanal lambayı, diğeri prizi sürüyor olabilir.
   */
  role?: DeviceRole;
  category?: DeviceCategory;
  detectedCategory?: DeviceCategory;
}

export interface GroupView {
  id: string;
  sourceName: string;
  name: string;
  members: number;
  memberIds: string[];
  scenes: Array<{ id: number; name: string }>;
  state: JsonObject;
}

export interface DeviceEventView {
  sourceName: string;
  property: string;
  value: JsonScalar;
  at: string;
}
