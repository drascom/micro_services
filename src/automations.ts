import { readFile, stat } from "node:fs/promises";
import { writeJsonAtomic } from "./atomic-file.js";
import type { AutomationRunStateStore } from "./automation-run-state.js";
import { isHexColor, numericControlKinds } from "./device-controls.js";
import type { DeviceControlView, JsonScalar } from "./types.js";

export interface AutomationTimeTrigger {
  type: "time";
  /** Yerel saat, "HH:MM". */
  at: string;
  /** 1 = Pazartesi … 7 = Pazar. */
  days: number[];
}

/**
 * Düğme basışı — anlık kenar olayı (§5.2). Alt varlık kuralı (§5.1.1): tetikleyici cihaza değil,
 * cihaz + buton çiftine bağlanır; üç butonlu anahtarın her butonu ayrı tetikleyicidir.
 */
export interface AutomationDeviceActionTrigger {
  type: "deviceAction";
  /** IEEE adresi — kanonik bağ. */
  deviceId: string;
  /** MQTT `action` değeri, örn. "1_single". */
  action: string;
}

/**
 * Gün doğumu / gün batımı. Hesaplanan an yerel `HH:MM`'e çevrilip mevcut zaman yoluna sokulur;
 * ayrı bir zamanlayıcı yolu yoktur. Konum ayarlı değilse kural çalışmaz ve sebebi günlüğe düşer.
 */
export interface AutomationSunTrigger {
  type: "sun";
  event: "sunrise" | "sunset";
  /** Güneş anına eklenecek dakika; negatif = önce. */
  offsetMinutes: number;
  /** 1 = Pazartesi … 7 = Pazar. Verilmezse her gün. */
  days: number[];
}

/**
 * Sensör/cihaz durumu — yalnızca kenarda, yani değer değiştiğinde tetiklenir.
 * `equals` verilirse yalnızca o değere geçişte, verilmezse **her** değişimde tetiklenir (§5.2).
 *
 * `above`/`below` sayısal eşiktir ve **kenarda** tetikler: değer eşiğin öbür tarafından bu tarafa
 * geçtiği anda bir kez. 26,1 → 26,2 → 26,3 akışı tek tetikleme üretir. İkisi birden verilirse
 * "aralığa girdi" anlamına gelir. `equals` ile birlikte kullanılamaz.
 *
 * `forSeconds` verilirse tetikleyici **kenar olmaktan çıkar**: değer hedefte kesintisiz o kadar
 * saniye kaldığında bir kez ateşler ("hareket bir dakikadır sürüyorsa"). Bu tetikleyici olay
 * akışını dinlemez, motorun turunda değerlendirilir — ama cihaz kimliği yine vardır.
 */
export interface AutomationDeviceStateTrigger {
  type: "deviceState";
  /** IEEE adresi — kanonik bağ. */
  deviceId: string;
  /** MQTT özellik anahtarı, örn. "occupancy". */
  property: string;
  /** Yoksa özelliğin her değişimi tetikler. */
  equals?: JsonScalar;
  /** Değer bunun üstüne çıktığı anda. */
  above?: number;
  /** Değer bunun altına indiği anda. */
  below?: number;
  /**
   * Hedef değerin **kesintisiz** sürmesi gereken süre (saniye, 1..86400). Verilmesi için
   * tetikleyicinin bir hedefi olmalıdır (`equals` ya da sayısal eşik): çıplak "her değişimde"
   * ile süre birleşmez, süre sıfırdan sayılacak bir hedef durum olmadan anlamsızdır.
   */
  forSeconds?: number;
}

export type AutomationTrigger =
  | AutomationTimeTrigger
  | AutomationSunTrigger
  | AutomationDeviceActionTrigger
  | AutomationDeviceStateTrigger;

/** Olay akışına bağlanan tetikleyiciler — zaman/güneş tetikleyicileri buraya girmez. */
export type AutomationEventTrigger =
  | AutomationDeviceActionTrigger
  | AutomationDeviceStateTrigger;

/**
 * Zamana bağlı tetikleyiciler — olay akışına değil, 20 saniyelik tura bakarlar.
 *
 * **Bu yardımcı bilinçli olarak `time`/`sun` ile sınırlıdır ve süreli durum tetikleyicisini
 * kapsamaz.** Çağrı yerlerinde asıl soru "tur yolunda mı" değil, **"cihaz kimliği taşımıyor mu"**:
 * `automationTriggerDeviceIds()`, döngü koruması ve `removeDeviceFromAutomations()` bu anlama
 * dayanıyor. Süreli tetikleyicinin cihaz kimliği **vardır** ve o üçünün de onu görmesi gerekir.
 * "Olay akışını dinlemiyor" sorusu için `isAutomationEventTrigger()` kullanılır.
 */
export const isAutomationScheduleTrigger = (
  trigger: AutomationTrigger
): trigger is AutomationTimeTrigger | AutomationSunTrigger =>
  trigger.type === "time" || trigger.type === "sun";

/**
 * Süreli durum tetikleyicisi — "değer N saniyedir hedefte". Kenarda değil, motorun turunda
 * değerlendirilir; buna rağmen cihaz kimliği taşır (döngü koruması ve cihaz silme ona bakar).
 */
export const isAutomationHeldStateTrigger = (
  trigger: AutomationTrigger
): trigger is AutomationDeviceStateTrigger =>
  trigger.type === "deviceState" && trigger.forSeconds !== undefined;

/**
 * Olay akışından tetiklenen tetikleyiciler. Zaman/güneş tetikleyicileri ve süreli durum
 * tetikleyicisi dışarıda kalır; ikisi de motorun turunda değerlendirilir.
 */
export const isAutomationEventTrigger = (
  trigger: AutomationTrigger
): trigger is AutomationEventTrigger =>
  !isAutomationScheduleTrigger(trigger) && !isAutomationHeldStateTrigger(trigger);

/**
 * §2.3 — zaman aralığının bir ucu: ya sabit saat ya güneşe göreli bir an. "Hava karanlıkken"
 * ayrı bir tür değil, bu ucun bir ön ayarıdır (batış→doğuş).
 *
 * Doğrulama girdide eski `"19:00"` dizesini de kabul eder ama **her zaman nesne yazar**: eski
 * dosyalar dokunulmadan geçerli kalır, ilk kaydetmede kendiliğinden yükselir.
 */
export type AutomationTimePoint =
  | { kind: "clock"; at: string }
  | { kind: "sun"; event: "sunrise" | "sunset"; offsetMinutes: number };

/**
 * §5.3 — koşullar varsayılan olarak **VE** ile değerlendirilir (§2.4 ile `any` de mümkün).
 * `timeRange` gece yarısını aşabilir (22:00→06:00): uçlar gün içi dakikaya çözülür ve
 * başlangıç bitişten büyükse aralık ertesi güne taşar.
 */
export interface AutomationTimeRangeCondition {
  type: "timeRange";
  /** Aralığın başlangıcı — dahil. */
  from: AutomationTimePoint;
  /** Aralığın bitişi — hariç. */
  to: AutomationTimePoint;
  /**
   * 1 = Pazartesi … 7 = Pazar. Verilmezse her gün. Gece yarısını aşan aralıkta gün ölçütü
   * aralığın **başladığı** güne bakar: 22:00→06:00 + Cuma = "cuma gecesi".
   */
  days?: number[];
}

/**
 * "Lamba zaten açıksa dokunma" — değer `DeviceStore`'dan okunur.
 *
 * `above`/`below` sayısal eşiktir ve tetikleyicinin tersine **kenara değil, o anki değere** bakar:
 * "şu an eşiğin üstünde mi". İkisi birden verilirse "aralıkta" demektir ve `above < below` olmalıdır.
 * `equals`, `not` ve eşik grubundan **tam biri** verilir.
 */
export interface AutomationDeviceStateCondition {
  type: "deviceState";
  /** IEEE adresi — kanonik bağ. */
  deviceId: string;
  property: string;
  /** Değer buna eşitse koşul sağlanır. */
  equals?: JsonScalar;
  /** Değer buna eşit **değilse** koşul sağlanır. Ölçütlerden tam biri verilir. */
  not?: JsonScalar;
  /** Değer şu an bunun üstündeyse koşul sağlanır. */
  above?: number;
  /** Değer şu an bunun altındaysa koşul sağlanır. */
  below?: number;
  /**
   * §2.1 — değer ölçütünün **kesintisiz** sağlanması gereken süre (saniye, 1..86400).
   * Değer ölçütünün üstüne biner, yerine geçmez: "hareket 1 dakikadır var" =
   * `equals: true` + `forSeconds: 60`. Alan yoksa davranış eskisiyle birebir aynıdır.
   */
  forSeconds?: number;
  /**
   * §2.6 — **tazelik penceresi** (saniye, 1..`maxAutomationConditionFreshSeconds`). Değer yalnız
   * son bu kadar saniye içinde **rapor edilmişse** koşul sağlanabilir; daha eski okuma bayat
   * sayılır ve koşul değerine hiç bakılmadan `false` olur.
   *
   * `forSeconds` ile karıştırılmamalı: o "değer ne zamandır aynı", bu "cihaz en son ne zaman
   * konuştu" sorusudur. Sabit lüks yayan bir sensörde ikisi aylarca farklıdır.
   *
   * Alan yoksa pencere **sınırsızdır** ve davranış eskisiyle birebir aynıdır; varsayılan diske
   * yazılmaz. Rapor damgası bellektedir: yeniden başlatmadan sonra bilinmez ve koşul
   * **sağlanmamış** sayılır (güvenli taraf, §2.5 ile aynı yorum).
   */
  freshWithinSeconds?: number;
}

export type AutomationCondition =
  | AutomationTimeRangeCondition
  | AutomationDeviceStateCondition;

/**
 * Eylem koşulu (§5.4) — eylemi **tetikleyen olayın değerine** bağlar. Anahtar durumu eylemlere
 * böyle eşlenir: `ON` gelince Aç, `OFF` gelince Kapat. Tek alanlı tutulur; başka alan yoktur.
 */
export interface AutomationActionWhen {
  /** Tetikleyen olayın değeri buna eşitse eylem çalışır. */
  equals: JsonScalar;
}

/**
 * §9 — "sonra kapat". Eylem çalıştıktan sonra hedefi kendiliğinden geri alır; kullanıcı biri açan
 * biri kapatan iki ayrı kural kurmak zorunda kalmaz.
 * - `after`: eylemden `seconds` saniye sonra geri alınır.
 * - `idle`: tetikleyici kanal tetikleyen değerinden çıkınca (hareket bitince) geri alınır;
 *   `seconds` burada isteğe bağlı **ek bekleme**dir, 0 olabilir.
 */
export interface AutomationAutoOff {
  mode: "after" | "idle";
  /** `after` için 1..86400; `idle` için 0..86400 ek bekleme. */
  seconds: number;
  /** Geri alınırken hedefe yazılacak değer (genelde kapatma değeri). */
  value: JsonScalar;
}

/**
 * "İzle" kipi — eylem sabit bir değer yerine **tetikleyen olayın değerini** kullanır.
 * - `ratio`: sayısal kanallarda oranlı eşleme. Tetikleyen kanalın kendi `min`/`max`'ından yüzde
 *   çıkarılır, hedefin `min`/`max`/`step`'ine çevrilir; iki cihazın ölçeği farklı olduğu için ham
 *   değer asla kopyalanmaz.
 * - `copy`: renk kanalında doğrudan kopya. Renkte yüzde anlamsızdır; değer normalizasyondan
 *   geçerek (hex → xy) olduğu gibi yazılır.
 *
 * Alan yoksa eylem bugünkü davranışıyla, `value` alanındaki sabit değerle çalışır.
 */
export interface AutomationActionFollow {
  mode: "ratio" | "copy";
}

export interface AutomationDeviceAction {
  type: "device";
  /** IEEE adresi — otomasyonun kalıcı bağı. */
  deviceId: string;
  /** MQTT özellik anahtarı. Kanonik hedef budur (örn. "state_l1"). */
  property: string;
  /** DeviceControlView.id — yalnızca sunum için, opsiyonel. */
  controlId?: string;
  /**
   * Sabit değer. `follow` varsa bu değer **yedektir**: tetikleyenin değeri çözülemezse motor buna
   * düşer, böylece kural sessizce ölmez.
   */
  value: JsonScalar;
  /** Yoksa eylem her zaman çalışır — geriye tam uyumluluk. */
  when?: AutomationActionWhen;
  /** Yoksa geri alma yok — geriye tam uyumluluk. */
  autoOff?: AutomationAutoOff;
  /** Yoksa sabit değer yazılır — geriye tam uyumluluk. */
  follow?: AutomationActionFollow;
}

/**
 * Sıralı eylemler arasında bekleme (Alexa'nın "Wait"'i). Tek eylem zaman aşımı (10 sn) yalnızca
 * cihaz çağrısı içindir; `delay` onun kapsamına girmez. Motor, `delay` taşıyan kuralı ortak
 * döngüyü kilitlemesin diye arka planda yürütür — bkz. `automation-engine.ts`.
 *
 * Bekleyen bir `delay` süreç ölürse kaybolur (autoOff gibi kalıcı değildir); motor bunu
 * çalışma günlüğüne yazar.
 */
export interface AutomationDelayAction {
  type: "delay";
  /** 1..300 saniye. */
  seconds: number;
  when?: AutomationActionWhen;
}

/** Grup aç/kapat — `ZigbeeSource.setGroup()` üzerinden. */
export interface AutomationGroupAction {
  type: "group";
  /** `GroupView.id`, örn. "group-7". */
  groupId: string;
  property: string;
  value: JsonScalar;
  when?: AutomationActionWhen;
}

/** Sahne çağırma — `ZigbeeSource.groupScene(id, sceneId, "recall")` zaten var. */
export interface AutomationSceneAction {
  type: "scene";
  groupId: string;
  sceneId: number;
  when?: AutomationActionWhen;
}

export type AutomationAction =
  | AutomationDeviceAction
  | AutomationDelayAction
  | AutomationGroupAction
  | AutomationSceneAction;

/** Bir cihaz kanalına yazan eylemler — döngü koruması ve "sonra kapat" bunlara bakar. */
export const isAutomationDeviceAction = (
  action: AutomationAction
): action is AutomationDeviceAction => action.type === "device";

/**
 * Ajan damgası — kuralı hangi ajan token'ı, ne zaman yazdı. **Ham token hiçbir zaman yazılmaz**:
 * yalnız kaydın kimliği (`AgentTokenSummary.id`) ve kullanıcının verdiği ad tutulur.
 *
 * Alan **opsiyoneldir** ve öyle kalmalıdır: ajandan önce yazılmış kurallar ile elle kurulan
 * kurallar damgasızdır, damgasız kalmayı sürdürür. Panelden yapılan bir düzenleme kuralı sıfırdan
 * kurduğu için damgayı düşürür — düzenlenen kural artık insanındır.
 */
export interface AutomationAgentStamp {
  /** `AgentTokenSummary.id`; token'ın kendisi değil, kaydın kimliği. */
  tokenId: string;
  /** Token'ın kullanıcı tarafından verilmiş adı ("Asistan"). */
  tokenName: string;
  /** Yazma anı (ISO). */
  at: string;
}

export interface Automation {
  id: string;
  name: string;
  enabled: boolean;
  /**
   * Kullanıcının panelden çalıştırdığı sahne. `true` olduğunda tetikleyici listesi boş olur;
   * motor bu kaydı zaman/cihaz olaylarında indekslemez, yalnız `run(id)` yolu çalıştırır.
   */
  manual?: boolean;
  triggers: AutomationTrigger[];
  conditions: AutomationCondition[];
  /**
   * §2.4 — koşulların nasıl bağlanacağı **kural başına** tek anahtardır (koşul başına değil).
   * Alan yoksa `"all"` sayılır; varsayılan diske yazılmaz ki eski dosyalar aynen kalsın.
   */
  conditionMode?: "all" | "any";
  actions: AutomationAction[];
  /**
   * Son çalışma — **tanımın parçası değildir**, `AutomationRunStateStore` tutar ve
   * `AutomationsStore` okurken listeye ekler. Diske yazılan tanım dosyasında bu alanlar bulunmaz;
   * eski dosyalarda varsa okunur ve durum dosyasına taşınır (bkz. `automation-run-state.ts`).
   */
  lastRunAt?: string | null;
  lastRunOk?: boolean | null;
  /** Yoksa kural insanındır — bkz. `AutomationAgentStamp`. */
  agent?: AutomationAgentStamp;
}

/** Kilit/siren doğrulaması için cihaz çözümleyici (enjekte edilir). */
export type AutomationDeviceLookup = (deviceId: string) =>
  { controls: DeviceControlView[] } | undefined;

/** Grup eylemlerinin üyelerini çözer; kilit/siren yasağı ve döngü koruması buna bakar. */
export type AutomationGroupLookup = (groupId: string) =>
  { memberIds: string[] } | undefined;

export const maxAutomations = 64;
export const maxAutomationTriggers = 8;
export const maxAutomationConditions = 4;
export const maxAutomationActions = 8;
export const maxAutomationNameLength = 64;
/** Güneş tetikleyicisi kaydırması: dört saat yeter, fazlası "güneş" olmaktan çıkar. */
export const maxAutomationSunOffsetMinutes = 240;
/** `delay` üst sınırı — beş dakikadan uzun bekleme kural değil, zamanlayıcıdır. */
export const maxAutomationDelaySeconds = 300;
/** Bir gün — bundan uzun bir "sonra kapat" süresi kullanıcı hatasıdır. */
export const maxAutomationAutoOffSeconds = 86_400;
/** Bekleyen kapatma sayısı; durum dosyası şişmesin. */
export const maxAutomationAutoOffEntries = 128;
/**
 * §2.1 — "şu kadar süredir böyleyse" tavanı. Hem koşul hem **süreli tetikleyici** bunu paylaşır:
 * ikisi aynı kavramdır, biri değişirse öbürü de değişmelidir. "Sonra kapat" ile aynı sayıdır ama
 * ayrı sabittir: o farklı bir kavramdır (koşul ölçütü değil, eylem sonucu).
 */
export const maxAutomationConditionForSeconds = 86_400;
/**
 * §2.6 — tazelik penceresinin tavanı. Bilerek `forSeconds`'tan çok daha dar: "son bir saatte
 * rapor edilmiş" zaten pencere olmaktan çıkar, panel de en fazla beş dakika sunar. Pencere bir
 * ölçüt değil, **bayat veriye karşı koruma**dır; günlerce genişleyen bir koruma koruma değildir.
 */
export const maxAutomationConditionFreshSeconds = 3_600;

/** §8.1 — otomasyonu onaylayacak insan yok; bu kontroller eylem olamaz. */
export const forbiddenAutomationControlKinds: ReadonlySet<string> = new Set(["lock", "siren"]);

const timePattern = /^([01]\d|2[0-3]):[0-5]\d$/;
const deviceIdPattern = /^0x[0-9a-f]{16}$/;
const propertyPattern = /^[A-Za-z0-9_]{1,64}$/;
const actionPattern = /^[A-Za-z0-9_-]{1,64}$/;
const controlIdPattern = /^[a-z0-9:_@-]{1,64}$/;
const automationIdPattern = /^[a-z0-9-]{8,32}$/;
/** `AgentTokenStore` kimliği: `randomBytes(9).toString("base64url")`. Kalıp yine de geniş tutulur. */
const agentTokenIdPattern = /^[A-Za-z0-9_-]{1,64}$/;
/**
 * `GroupView.id` — `group-<zigbee id>`, dost isim düşerse `group-<isim>` olabildiği için
 * karakter kümesi geniş tutulur; yalnızca denetim karakterleri ve aşırı uzunluk elenir.
 */
const groupIdPattern = /^group-[^\x00-\x1f]{1,56}$/;

const isJsonScalar = (value: unknown): value is JsonScalar =>
  typeof value === "string"
  || typeof value === "boolean"
  || (typeof value === "number" && Number.isFinite(value));

const triggerDeviceId = (value: unknown): string => {
  const deviceId = typeof value === "string" ? value.trim().toLowerCase() : "";
  if (!deviceIdPattern.test(deviceId)) {
    throw new Error("Otomasyon tetikleyicisi cihaz UID'si geçersiz.");
  }
  return deviceId;
};

/**
 * Sayısal eşik ayrıştırması — tetikleyici ve koşul aynı kuralı paylaşır: alan yoksa `undefined`,
 * `"25"` gibi dize de kabul, sonuç sonlu bir sayı olmalıdır.
 */
const thresholdNumber = (value: unknown, message: string): number | undefined => {
  if (value === undefined || value === null) return undefined;
  const numeric = typeof value === "string" ? Number(value.trim()) : value;
  if (typeof numeric !== "number" || !Number.isFinite(numeric)) throw new Error(message);
  return numeric;
};

/**
 * Güneş kaydırması — tetikleyici ve `timeRange` ucu aynı sınırı paylaşır: tam sayı dakika,
 * `maxAutomationSunOffsetMinutes` içinde. Verilmezse 0.
 */
/**
 * §2.1 — "şu kadar süredir". Yokluk eski davranıştır ve **hiç yazılmaz**. Sıfır ve ondalık
 * reddedilir: yarım saniyelik bir ölçüt cihaz raporlama aralığının altında kalır. Koşul ve
 * tetikleyici aynı kuralı paylaşır, yalnızca hata metni farklıdır.
 */
const forSecondsValue = (
  value: unknown,
  message: string,
  max: number = maxAutomationConditionForSeconds
): number | undefined => {
  if (value === undefined || value === null) return undefined;
  if (
    typeof value !== "number"
    || !Number.isInteger(value)
    || value < 1
    || value > max
  ) {
    throw new Error(message);
  }
  return value;
};

const sunOffsetMinutes = (value: unknown, message: string): number => {
  const offsetMinutes = value === undefined || value === null
    ? 0
    : (typeof value === "string" ? Number(value.trim()) : value);
  if (
    typeof offsetMinutes !== "number"
    || !Number.isInteger(offsetMinutes)
    || Math.abs(offsetMinutes) > maxAutomationSunOffsetMinutes
  ) {
    throw new Error(message);
  }
  return offsetMinutes;
};

/**
 * §2.3 — aralık ucu. Girdide üç biçim kabul edilir (eski `"HH:MM"` dizesi, `{kind:"clock"}`,
 * `{kind:"sun"}`), çıktıda tek biçim yazılır. `kind` yoksa alanlardan çıkarılır: canlı sunucudaki
 * eski kurallar bozulmadan yükselsin diye giriş bilinçli olarak geniş, çıkış dar tutuldu.
 */
const validateTimePoint = (value: unknown): AutomationTimePoint => {
  const clock = (raw: unknown): AutomationTimePoint => {
    const at = typeof raw === "string" ? raw.trim() : "";
    if (!timePattern.test(at)) throw new Error("Otomasyon koşulu saat aralığı geçersiz.");
    return { kind: "clock", at };
  };
  if (typeof value === "string") return clock(value);
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("Otomasyon koşulu saat aralığı geçersiz.");
  }
  const candidate = value as Record<string, unknown>;
  const kind = candidate.kind ?? (candidate.event !== undefined ? "sun" : "clock");
  if (kind === "clock") return clock(candidate.at);
  if (kind !== "sun") throw new Error("Otomasyon koşulu saat aralığı geçersiz.");
  if (candidate.event !== "sunrise" && candidate.event !== "sunset") {
    throw new Error("Otomasyon koşulu güneş olayı geçersiz.");
  }
  return {
    kind: "sun",
    event: candidate.event,
    offsetMinutes: sunOffsetMinutes(
      candidate.offsetMinutes,
      "Otomasyon koşulu güneş kaydırması geçersiz."
    )
  };
};

/**
 * İki uç aynı anı mı gösteriyor? Aynı saat ya da aynı güneş olayı + aynı kaydırma "hiç mi hep mi"
 * belirsizliğidir. Karışık uçlarda (saat ↔ güneş) çakışma günden güne değişir, kontrol edilmez.
 */
const sameTimePoint = (from: AutomationTimePoint, to: AutomationTimePoint): boolean =>
  from.kind === "clock" && to.kind === "clock"
    ? from.at === to.at
    : from.kind === "sun" && to.kind === "sun"
      && from.event === to.event && from.offsetMinutes === to.offsetMinutes;

/** 1 = Pazartesi … 7 = Pazar; yinelenenler ayıklanır, sıralanır. */
const validateDays = (value: unknown, label: string): number[] => {
  if (!Array.isArray(value) || value.length === 0) throw new Error(label);
  const days: number[] = [];
  for (const day of value) {
    if (typeof day !== "number" || !Number.isInteger(day) || day < 1 || day > 7) {
      throw new Error(label);
    }
    if (!days.includes(day)) days.push(day);
  }
  days.sort((left, right) => left - right);
  return days;
};

const validateTriggers = (value: unknown, allowEmpty = false): AutomationTrigger[] => {
  if (
    !Array.isArray(value)
    || (!allowEmpty && value.length === 0)
    || value.length > maxAutomationTriggers
  ) {
    throw new Error("Otomasyon tetikleyicileri geçersiz.");
  }
  return value.map((entry) => {
    if (typeof entry !== "object" || entry === null || Array.isArray(entry)) {
      throw new Error("Otomasyon tetikleyicisi geçersiz.");
    }
    const candidate = entry as Record<string, unknown>;
    // §8.1 — kilit/siren yalnızca EYLEM olarak yasak; tetikleyici olarak serbesttir.
    if (candidate.type === "deviceAction") {
      const deviceId = triggerDeviceId(candidate.deviceId);
      const action = typeof candidate.action === "string" ? candidate.action.trim() : "";
      if (!actionPattern.test(action)) {
        throw new Error("Otomasyon tetikleyicisi düğme eylemi geçersiz.");
      }
      return { type: "deviceAction", deviceId, action } satisfies AutomationDeviceActionTrigger;
    }
    if (candidate.type === "deviceState") {
      const deviceId = triggerDeviceId(candidate.deviceId);
      const property = typeof candidate.property === "string" ? candidate.property.trim() : "";
      if (!propertyPattern.test(property)) {
        throw new Error("Otomasyon tetikleyicisi cihaz özelliği geçersiz.");
      }
      const trigger: AutomationDeviceStateTrigger = { type: "deviceState", deviceId, property };
      // `equals` opsiyoneldir: yoksa özelliğin her değişimi tetikler.
      if (candidate.equals !== undefined && candidate.equals !== null) {
        if (!isJsonScalar(candidate.equals)) {
          throw new Error("Otomasyon tetikleyicisi hedef değeri geçersiz.");
        }
        trigger.equals = candidate.equals;
      }
      const above = thresholdNumber(candidate.above, "Otomasyon tetikleyicisi üst eşiği geçersiz.");
      const below = thresholdNumber(candidate.below, "Otomasyon tetikleyicisi alt eşiği geçersiz.");
      if (above !== undefined) trigger.above = above;
      if (below !== undefined) trigger.below = below;
      // Eşitlik ile eşik aynı tetikleyicide iki ayrı kenar tanımı olurdu; biri seçilmeli.
      if (trigger.equals !== undefined && (above !== undefined || below !== undefined)) {
        throw new Error("Otomasyon tetikleyicisinde eşitlik ve sayısal eşik birlikte olamaz.");
      }
      if (above !== undefined && below !== undefined && above >= below) {
        throw new Error("Otomasyon tetikleyicisinde üst eşik alt eşikten küçük olmalıdır.");
      }
      // §2.1 — süreli tetikleyici. Hedefi olmayan ("her değişimde") bir tetikleyiciyle süre
      // birleşmez: neyin kaç saniyedir sürdüğü tanımsız kalır, sessiz kabul etmek yerine reddedilir.
      const forSeconds = forSecondsValue(
        candidate.forSeconds,
        "Otomasyon tetikleyicisi süresi geçersiz."
      );
      if (forSeconds !== undefined) {
        if (trigger.equals === undefined && above === undefined && below === undefined) {
          throw new Error(
            "Süreli otomasyon tetikleyicisi için hedef değer ya da sayısal eşik gerekir."
          );
        }
        trigger.forSeconds = forSeconds;
      }
      return trigger;
    }
    if (candidate.type === "sun") {
      if (candidate.event !== "sunrise" && candidate.event !== "sunset") {
        throw new Error("Otomasyon güneş olayı geçersiz.");
      }
      const offsetMinutes = sunOffsetMinutes(
        candidate.offsetMinutes,
        "Otomasyon güneş kaydırması geçersiz."
      );
      // Gün listesi opsiyoneldir; sihirbaz vermezse her gün.
      const days = candidate.days === undefined || candidate.days === null
        ? [1, 2, 3, 4, 5, 6, 7]
        : validateDays(candidate.days, "Otomasyon günleri geçersiz.");
      return {
        type: "sun",
        event: candidate.event,
        offsetMinutes,
        days
      } satisfies AutomationSunTrigger;
    }
    if (candidate.type !== "time") {
      throw new Error("Otomasyon tetikleyici türü bu sürümde desteklenmiyor.");
    }
    const at = typeof candidate.at === "string" ? candidate.at.trim() : "";
    if (!timePattern.test(at)) throw new Error("Otomasyon saati geçersiz.");
    const days = validateDays(candidate.days, "Otomasyon günleri geçersiz.");
    return { type: "time", at, days } satisfies AutomationTimeTrigger;
  });
};

/**
 * §2.4 — koşulların bağlanma biçimi. Yokluk ve `"all"` aynı şeydir ve **hiç yazılmaz**:
 * varsayılanı diske gömmek eski dosyalarla sahte bir fark üretirdi.
 */
const validateConditionMode = (value: unknown): "any" | undefined => {
  if (value === undefined || value === null || value === "all") return undefined;
  if (value === "any") return "any";
  throw new Error("Otomasyon koşul bağlama biçimi geçersiz.");
};

/** §5.3 — koşullar varsayılan olarak VE ile değerlendirilir; bilinmeyen tür reddedilir. */
const validateConditions = (value: unknown): AutomationCondition[] => {
  if (!Array.isArray(value) || value.length > maxAutomationConditions) {
    throw new Error("Otomasyon koşulları geçersiz.");
  }
  return value.map((entry) => {
    if (typeof entry !== "object" || entry === null || Array.isArray(entry)) {
      throw new Error("Otomasyon koşulu geçersiz.");
    }
    const candidate = entry as Record<string, unknown>;
    if (candidate.type === "timeRange") {
      const from = validateTimePoint(candidate.from);
      const to = validateTimePoint(candidate.to);
      // Aynı anı gösteren iki uç "hiç" mi "hep" mi belirsizdir; kullanıcı hatasıdır.
      if (sameTimePoint(from, to)) {
        throw new Error("Otomasyon koşulu saat aralığı başlangıç ve bitişi aynı olamaz.");
      }
      const condition: AutomationTimeRangeCondition = { type: "timeRange", from, to };
      if (candidate.days !== undefined && candidate.days !== null) {
        condition.days = validateDays(candidate.days, "Otomasyon koşulu günleri geçersiz.");
      }
      return condition;
    }
    if (candidate.type !== "deviceState") {
      throw new Error("Otomasyon koşul türü bu sürümde desteklenmiyor.");
    }
    const deviceId = typeof candidate.deviceId === "string"
      ? candidate.deviceId.trim().toLowerCase()
      : "";
    if (!deviceIdPattern.test(deviceId)) throw new Error("Otomasyon koşulu cihaz UID'si geçersiz.");
    const property = typeof candidate.property === "string" ? candidate.property.trim() : "";
    if (!propertyPattern.test(property)) {
      throw new Error("Otomasyon koşulu cihaz özelliği geçersiz.");
    }
    const hasEquals = candidate.equals !== undefined && candidate.equals !== null;
    const hasNot = candidate.not !== undefined && candidate.not !== null;
    const above = thresholdNumber(candidate.above, "Otomasyon koşulu üst eşiği geçersiz.");
    const below = thresholdNumber(candidate.below, "Otomasyon koşulu alt eşiği geçersiz.");
    const hasThreshold = above !== undefined || below !== undefined;
    // Üç ölçüt de aynı kanalı okur; ikisi birlikte "hangisi geçerli" sorusunu doğurur.
    if ([hasEquals, hasNot, hasThreshold].filter(Boolean).length !== 1) {
      throw new Error(
        "Otomasyon koşulunda `equals`, `not` ya da sayısal eşik alanlarından tam biri olmalıdır."
      );
    }
    // §2.1 — süre ölçütü üç değer ölçütünün de üstüne binebilir, o yüzden dallardan önce okunur.
    const forSeconds = forSecondsValue(candidate.forSeconds, "Otomasyon koşulu süresi geçersiz.");
    // §2.6 — tazelik penceresi de değer ölçütünden bağımsızdır; sayısal eşikte de eşitlikte de
    // aynı soruyu sorar ("cihaz en son ne zaman konuştu"), o yüzden burada bir kez okunur.
    const freshWithinSeconds = forSecondsValue(
      candidate.freshWithinSeconds,
      "Otomasyon koşulu tazelik penceresi geçersiz.",
      maxAutomationConditionFreshSeconds
    );
    if (hasThreshold) {
      // İkisi birden verilmişse ölçüt "aralıkta"dır; ters aralık hiçbir zaman sağlanmaz.
      if (above !== undefined && below !== undefined && above >= below) {
        throw new Error("Otomasyon koşulunda üst eşik alt eşikten küçük olmalıdır.");
      }
      const condition: AutomationDeviceStateCondition = { type: "deviceState", deviceId, property };
      if (above !== undefined) condition.above = above;
      if (below !== undefined) condition.below = below;
      if (forSeconds !== undefined) condition.forSeconds = forSeconds;
      if (freshWithinSeconds !== undefined) condition.freshWithinSeconds = freshWithinSeconds;
      return condition;
    }
    const raw = hasEquals ? candidate.equals : candidate.not;
    if (!isJsonScalar(raw)) throw new Error("Otomasyon koşulu değeri geçersiz.");
    const condition: AutomationDeviceStateCondition = hasEquals
      ? { type: "deviceState", deviceId, property, equals: raw }
      : { type: "deviceState", deviceId, property, not: raw };
    if (forSeconds !== undefined) condition.forSeconds = forSeconds;
    if (freshWithinSeconds !== undefined) condition.freshWithinSeconds = freshWithinSeconds;
    return condition;
  });
};

/** §5.2 — olay tetikleyicilerinin dinlediği cihaz kimlikleri. */
export const automationTriggerDeviceIds = (automation: Automation): string[] => {
  const ids: string[] = [];
  for (const trigger of automation.triggers) {
    if (isAutomationScheduleTrigger(trigger)) continue;
    if (!ids.includes(trigger.deviceId)) ids.push(trigger.deviceId);
  }
  return ids;
};

/**
 * Kuralın dokunduğu **tüm** IEEE adresleri: tetikleyiciler, koşullar ve cihaz eylemleri.
 * Yedek geri yükleme ve cihaz silme bu listeye bakar; grup eylemleri cihaz kimliği taşımaz.
 */
export const automationDeviceIds = (automation: Automation): string[] => {
  const ids = new Set(automationTriggerDeviceIds(automation));
  for (const condition of automation.conditions) {
    if (condition.type === "deviceState") ids.add(condition.deviceId);
  }
  for (const action of automation.actions) {
    if (isAutomationDeviceAction(action)) ids.add(action.deviceId);
  }
  return [...ids];
};

/**
 * §8.2 — döngü korumasının kanonik anahtarı: IEEE adresi + kanal (MQTT özelliği). Dost isim
 * kullanılmaz. Çok kanallı anahtarın `state_l1`/`state_l2` uçları ayrı kanal sayılır.
 */
const automationChannelKey = (
  entry: { deviceId: string; property: string }
): string => `${entry.deviceId}|${entry.property}`;

/** §5.4 — eylem koşulu yalnızca `equals` taşır; fazlası reddedilir. */
const validateActionWhen = (value: unknown): AutomationActionWhen => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("Otomasyon eylemi koşulu geçersiz.");
  }
  const candidate = value as Record<string, unknown>;
  for (const key of Object.keys(candidate)) {
    if (key !== "equals") throw new Error("Otomasyon eylemi koşulunda bilinmeyen alan var.");
  }
  if (!isJsonScalar(candidate.equals)) {
    throw new Error("Otomasyon eylemi koşul değeri geçersiz.");
  }
  return { equals: candidate.equals };
};

/** §9 — "sonra kapat" yalnızca üç alan tanır; fazlası reddedilir. */
const validateAutoOff = (value: unknown, actionValue: JsonScalar): AutomationAutoOff => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("Otomasyon otomatik kapatma ayarı geçersiz.");
  }
  const candidate = value as Record<string, unknown>;
  for (const key of Object.keys(candidate)) {
    if (key !== "mode" && key !== "seconds" && key !== "value") {
      throw new Error("Otomasyon otomatik kapatma ayarında bilinmeyen alan var.");
    }
  }
  if (candidate.mode !== "after" && candidate.mode !== "idle") {
    throw new Error("Otomasyon otomatik kapatma türü geçersiz.");
  }
  const seconds = candidate.seconds;
  if (
    typeof seconds !== "number"
    || !Number.isInteger(seconds)
    || seconds < 0
    || seconds > maxAutomationAutoOffSeconds
  ) {
    throw new Error("Otomasyon otomatik kapatma süresi geçersiz.");
  }
  // Süreyle kapatmada 0 saniye anlamsızdır; hareket bitince kapatmada 0 = ek bekleme yok.
  if (candidate.mode === "after" && seconds < 1) {
    throw new Error("Otomasyon otomatik kapatma süresi geçersiz.");
  }
  if (!isJsonScalar(candidate.value)) {
    throw new Error("Otomasyon otomatik kapatma değeri geçersiz.");
  }
  if (candidate.value === actionValue) {
    throw new Error("Otomasyon otomatik kapatma değeri eylemin değeriyle aynı olamaz.");
  }
  return { mode: candidate.mode, seconds, value: candidate.value };
};

/** §8.1 — yasak kontrol türü, hangi eylem türünden geçerse geçsin reddedilir. */
const rejectForbiddenControl = (
  deviceId: string,
  property: string | null,
  lookup?: AutomationDeviceLookup
): void => {
  const controls = lookup?.(deviceId)?.controls;
  if (!controls) return;
  const hit = controls.find((item) =>
    (property === null || item.property === property)
    && forbiddenAutomationControlKinds.has(item.kind));
  if (hit) throw new Error("Kilit ve siren bir otomasyon eylemi olamaz.");
};

/** §5.5 — "izle" yalnız iki alan tanır; fazlası reddedilir. */
const validateActionFollow = (value: unknown): AutomationActionFollow => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("Otomasyon eylemi izleme ayarı geçersiz.");
  }
  const candidate = value as Record<string, unknown>;
  for (const key of Object.keys(candidate)) {
    if (key !== "mode") throw new Error("Otomasyon eylemi izleme ayarında bilinmeyen alan var.");
  }
  if (candidate.mode !== "ratio" && candidate.mode !== "copy") {
    throw new Error("Otomasyon eylemi izleme türü geçersiz.");
  }
  return { mode: candidate.mode };
};

/** İzlenen ve yazılan kanalın türü uyumlu mu? Kumanda arayıcısı yoksa denetim atlanır. */
const followControlMatches = (
  control: DeviceControlView | undefined,
  mode: AutomationActionFollow["mode"]
): boolean => {
  if (!control) return true;
  return mode === "copy" ? control.kind === "color" : numericControlKinds.has(control.kind);
};

const rejectIncompatibleFollow = (
  deviceId: string,
  property: string,
  follow: AutomationActionFollow,
  lookup?: AutomationDeviceLookup
): void => {
  const control = lookup?.(deviceId)?.controls.find((item) => item.property === property);
  if (!control) return;
  if (followControlMatches(control, follow.mode)) return;
  throw new Error(follow.mode === "copy"
    ? "Renk izlemesi yalnız renk kumandasına yazılabilir."
    : "Oranlı izleme yalnız sayısal bir kumandaya yazılabilir.");
};

/**
 * Eylemin değeri kumandaya uyuyor mu? Kumanda arayıcısı yoksa (eski davranış) doğrulama atlanır.
 * Ölçüt kumandanın kendi tanım verisidir: renk `#rrggbb` biçimindedir, sayısal değer `min`/`max`
 * arasındadır. Panel zaten bu değerleri üretiyor; buradaki denetim elle yazılmış kural içindir.
 */
const rejectInvalidActionValue = (
  deviceId: string,
  property: string,
  value: JsonScalar,
  lookup?: AutomationDeviceLookup
): void => {
  const control = lookup?.(deviceId)?.controls.find((item) => item.property === property);
  if (!control) return;
  if (control.kind === "color") {
    if (!isHexColor(value)) throw new Error("Otomasyon eylemi renk değeri geçersiz.");
    return;
  }
  if (!numericControlKinds.has(control.kind) || typeof value !== "number") return;
  if (!Number.isFinite(value)) throw new Error("Otomasyon eylemi değeri geçersiz.");
  if (
    (control.min !== undefined && value < control.min)
    || (control.max !== undefined && value > control.max)
  ) {
    throw new Error("Otomasyon eylemi değeri kumandanın aralığı dışında.");
  }
};

const validateActions = (
  value: unknown,
  lookup?: AutomationDeviceLookup,
  groupLookup?: AutomationGroupLookup
): AutomationAction[] => {
  if (!Array.isArray(value) || value.length === 0 || value.length > maxAutomationActions) {
    throw new Error("Otomasyon eylemleri geçersiz.");
  }
  return value.map((entry) => {
    if (typeof entry !== "object" || entry === null || Array.isArray(entry)) {
      throw new Error("Otomasyon eylemi geçersiz.");
    }
    const candidate = entry as Record<string, unknown>;
    const when = candidate.when !== undefined && candidate.when !== null
      ? validateActionWhen(candidate.when)
      : undefined;
    if (candidate.type === "delay") {
      const raw = candidate.seconds;
      const seconds = typeof raw === "string" ? Number(raw.trim()) : raw;
      if (
        typeof seconds !== "number"
        || !Number.isInteger(seconds)
        || seconds < 1
        || seconds > maxAutomationDelaySeconds
      ) {
        throw new Error("Otomasyon bekleme süresi geçersiz.");
      }
      const action: AutomationDelayAction = { type: "delay", seconds };
      if (when) action.when = when;
      return action;
    }
    if (candidate.type === "group" || candidate.type === "scene") {
      const groupId = typeof candidate.groupId === "string"
        ? candidate.groupId.trim().toLowerCase()
        : "";
      if (!groupIdPattern.test(groupId)) throw new Error("Otomasyon eylemi grup kimliği geçersiz.");
      // Grubun üyeleri arasında kilit/siren varsa grup ya da sahne komutu onu da kapsar.
      const members = groupLookup?.(groupId)?.memberIds ?? [];
      if (candidate.type === "group") {
        const property = typeof candidate.property === "string" ? candidate.property.trim() : "";
        if (!propertyPattern.test(property)) {
          throw new Error("Otomasyon eylemi grup özelliği geçersiz.");
        }
        if (!isJsonScalar(candidate.value)) throw new Error("Otomasyon eylemi değeri geçersiz.");
        for (const memberId of members) rejectForbiddenControl(memberId, property, lookup);
        const action: AutomationGroupAction = {
          type: "group",
          groupId,
          property,
          value: candidate.value
        };
        if (when) action.when = when;
        return action;
      }
      const raw = candidate.sceneId;
      const sceneId = typeof raw === "string" ? Number(raw.trim()) : raw;
      if (
        typeof sceneId !== "number"
        || !Number.isInteger(sceneId)
        || sceneId < 0
        || sceneId > 255
      ) {
        throw new Error("Otomasyon eylemi sahne kimliği geçersiz.");
      }
      // Sahne hangi kanalı yazacağını söylemez; grupta kilit/siren varsa tamamen reddedilir.
      for (const memberId of members) rejectForbiddenControl(memberId, null, lookup);
      const action: AutomationSceneAction = { type: "scene", groupId, sceneId };
      if (when) action.when = when;
      return action;
    }
    if (candidate.type !== "device") {
      throw new Error("Otomasyon eylem türü bu sürümde desteklenmiyor.");
    }
    const deviceId = typeof candidate.deviceId === "string"
      ? candidate.deviceId.trim().toLowerCase()
      : "";
    if (!deviceIdPattern.test(deviceId)) throw new Error("Otomasyon eylemi cihaz UID'si geçersiz.");
    const property = typeof candidate.property === "string" ? candidate.property.trim() : "";
    if (!propertyPattern.test(property)) throw new Error("Otomasyon eylemi cihaz özelliği geçersiz.");
    if (!isJsonScalar(candidate.value)) throw new Error("Otomasyon eylemi değeri geçersiz.");
    const action: AutomationDeviceAction = { type: "device", deviceId, property, value: candidate.value };
    if (candidate.controlId !== undefined && candidate.controlId !== null) {
      const controlId = typeof candidate.controlId === "string"
        ? candidate.controlId.trim().toLowerCase()
        : "";
      if (!controlIdPattern.test(controlId)) throw new Error("Otomasyon eylemi kontrol kimliği geçersiz.");
      action.controlId = controlId;
    }
    if (when) action.when = when;
    if (candidate.autoOff !== undefined && candidate.autoOff !== null) {
      action.autoOff = validateAutoOff(candidate.autoOff, action.value);
    }
    if (candidate.follow !== undefined && candidate.follow !== null) {
      action.follow = validateActionFollow(candidate.follow);
      rejectIncompatibleFollow(deviceId, property, action.follow, lookup);
    }
    rejectForbiddenControl(deviceId, property, lookup);
    rejectInvalidActionValue(deviceId, property, action.value, lookup);
    if (action.autoOff) rejectInvalidActionValue(deviceId, property, action.autoOff.value, lookup);
    return action;
  });
};

/**
 * Ajan damgası doğrulaması. Yokluk geçerlidir ve **hiç yazılmaz**; damgasız eski kurallar
 * dokunulmadan geçer. Ham token buraya asla girmez: alan adları bilerek `tokenId`/`tokenName`,
 * yani gizli olmayan iki alan.
 */
const validateAgentStamp = (value: unknown): AutomationAgentStamp | undefined => {
  if (value === undefined || value === null) return undefined;
  if (typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Otomasyon ajan damgası geçersiz.");
  }
  const candidate = value as Record<string, unknown>;
  const tokenId = typeof candidate.tokenId === "string" ? candidate.tokenId.trim() : "";
  if (!agentTokenIdPattern.test(tokenId)) {
    throw new Error("Otomasyon ajan damgası kimliği geçersiz.");
  }
  const tokenName = typeof candidate.tokenName === "string" ? candidate.tokenName.trim() : "";
  if (!tokenName || tokenName.length > maxAutomationNameLength) {
    throw new Error("Otomasyon ajan damgası adı geçersiz.");
  }
  const at = candidate.at;
  if (typeof at !== "string" || Number.isNaN(Date.parse(at))) {
    throw new Error("Otomasyon ajan damgası zamanı geçersiz.");
  }
  return { tokenId, tokenName, at };
};

export const validateAutomations = (
  value: unknown,
  lookup?: AutomationDeviceLookup,
  groupLookup?: AutomationGroupLookup
): Automation[] => {
  if (!Array.isArray(value) || value.length > maxAutomations) {
    throw new Error("Otomasyonlar geçersiz.");
  }
  const result: Automation[] = [];
  const ids = new Set<string>();
  for (const entry of value) {
    if (typeof entry !== "object" || entry === null || Array.isArray(entry)) {
      throw new Error("Otomasyon geçersiz.");
    }
    const candidate = entry as Record<string, unknown>;
    const id = typeof candidate.id === "string" ? candidate.id.trim().toLowerCase() : "";
    if (!automationIdPattern.test(id)) throw new Error("Otomasyon kimliği geçersiz.");
    if (ids.has(id)) throw new Error("Otomasyon kimliği yinelenmiş.");
    ids.add(id);
    const name = typeof candidate.name === "string" ? candidate.name.trim() : "";
    if (!name || name.length > maxAutomationNameLength) throw new Error("Otomasyon adı geçersiz.");
    // Eski dosyalarda alan hep var ve boş; yokluğunu da kabul ederiz (geriye dönük uyumluluk).
    const conditions = validateConditions(candidate.conditions ?? []);
    const conditionMode = validateConditionMode(candidate.conditionMode);
    // `lastRunAt`/`lastRunOk` gelirse **sessizce düşer**: çalışma durumu tanımın parçası değildir
    // (eski dosyalar ve eski paneller alanı gönderdiği için reddetmek değil, yok saymak doğru).
    const agent = validateAgentStamp(candidate.agent);
    if (candidate.manual !== undefined && typeof candidate.manual !== "boolean") {
      throw new Error("Otomasyon elle çalıştırma bilgisi geçersiz.");
    }
    const manual = candidate.manual === true;
    const triggers = validateTriggers(candidate.triggers, manual);
    if (manual && triggers.length > 0) {
      throw new Error("Elle çalıştırılan otomasyonda otomatik tetikleyici olamaz.");
    }
    const actions = validateActions(candidate.actions, lookup, groupLookup);
    // Yalnız beklemeden oluşan bir kural hiçbir şey yapmaz; kullanıcı hatasıdır.
    if (actions.every((action) => action.type === "delay")) {
      throw new Error("Otomasyonda en az bir gerçek eylem olmalıdır.");
    }
    // §9 — "hareket bitince kapat" ölçütü jeneriktir: kuralın kendi tetikleyicisinin **tetikleyen
    // değerinden çıkması** demektir. Bu yüzden `equals` taşıyan bir durum tetikleyicisi şart;
    // sensör modeli listesi yoktur. Her değişimde tetiklenen kuralda "bitiş" tanımsızdır.
    if (actions.some((action) => isAutomationDeviceAction(action) && action.autoOff?.mode === "idle")) {
      const watchable = triggers.some((trigger) =>
        trigger.type === "deviceState" && trigger.equals !== undefined);
      if (!watchable) {
        throw new Error(
          "Hareket bitince kapatma için durum bildiren bir tetikleyici gerekir."
        );
      }
    }
    // §5.5 — "izle" kipi tetikleyenin **canlı** değerini kullanır. Bunu yalnız hedefsiz bir durum
    // tetikleyicisi ("değeri her değiştiğinde") besler: `equals` tek bir değere geçişte, sayısal
    // eşik ise yalnız eşiği geçerken bir kez ateşler — ikisinde de izlenen değer tek bir sayıda
    // donar, yani izleme sabit değerden farksız olurdu. Süreli tetikleyici de aynı sebeple dışarıda.
    const followActions = actions.filter((action): action is AutomationDeviceAction =>
      isAutomationDeviceAction(action) && action.follow !== undefined);
    if (followActions.length > 0) {
      const sources = triggers.filter((trigger): trigger is AutomationDeviceStateTrigger =>
        trigger.type === "deviceState"
        && trigger.equals === undefined
        && trigger.above === undefined
        && trigger.below === undefined
        && trigger.forSeconds === undefined);
      if (sources.length === 0) {
        throw new Error(
          "İzleme için değeri her değiştiğinde bildiren bir cihaz durumu tetikleyicisi gerekir."
        );
      }
      for (const action of followActions) {
        const mode = action.follow!.mode;
        const usable = sources.some((trigger) => followControlMatches(
          lookup?.(trigger.deviceId)?.controls.find((item) => item.property === trigger.property),
          mode
        ));
        if (usable) continue;
        throw new Error(mode === "copy"
          ? "Renk izlemesi için renk bildiren bir tetikleyici gerekir."
          : "Oranlı izleme için sayısal değer bildiren bir tetikleyici gerekir.");
      }
    }
    // §8.2 — geri besleme döngüsü kaydetme anında reddedilir, çalışma zamanında değil.
    // Koruma kanal granülerliğinde: çok kanallı anahtarda bir kanal tetikleyip komşu kanalı
    // çalıştırmak geçerlidir; yasak olan yalnızca kanalın kendi kendini tetiklemesidir.
    // Düğme tetikleyicisinde kanal (property) yoktur; orada cihaz granülerliği korunur.
    const deviceActions = actions.filter(isAutomationDeviceAction);
    const actionDeviceIds = new Set(deviceActions.map((action) => action.deviceId));
    const actionChannels = new Set(deviceActions.map((action) => automationChannelKey(action)));
    // Grup eylemi de üyelerinin kanallarını yazar; üyeler çözülebiliyorsa döngü orada da aranır.
    for (const action of actions) {
      if (action.type !== "group") continue;
      for (const memberId of groupLookup?.(action.groupId)?.memberIds ?? []) {
        actionDeviceIds.add(memberId);
        actionChannels.add(automationChannelKey({ deviceId: memberId, property: action.property }));
      }
    }
    for (const trigger of triggers) {
      if (isAutomationScheduleTrigger(trigger)) continue;
      const looped = trigger.type === "deviceState"
        ? actionChannels.has(automationChannelKey(trigger))
        : actionDeviceIds.has(trigger.deviceId);
      if (looped) {
        throw new Error(
          "Bir otomasyon kendi çalıştırdığı kanal tarafından tetiklenemez; döngü oluşur."
        );
      }
    }
    const automation: Automation = {
      id,
      name,
      enabled: candidate.enabled !== false,
      triggers,
      conditions,
      actions
    };
    if (manual) automation.manual = true;
    // Yalnız "any" korunur; varsayılan alan hiç görünmez (yukarıdaki gerekçe).
    if (conditionMode) automation.conditionMode = conditionMode;
    // Damga da aynı kuraldadır: yoksa yazılmaz, eski dosyalar birebir aynı kalır.
    if (agent) automation.agent = agent;
    result.push(automation);
  }
  return result;
};

/**
 * Cihaz silindiğinde o cihaza bağlı eylemler ve olay tetikleyicileri düşer;
 * eylemi ya da tetikleyicisi kalmayan otomasyon da silinir.
 */
export const removeDeviceFromAutomations = (
  automations: Automation[],
  deviceId: string
): Automation[] => {
  const normalizedId = deviceId.trim().toLowerCase();
  return automations
    .map((automation) => ({
      ...automation,
      triggers: automation.triggers.filter((trigger) =>
        isAutomationScheduleTrigger(trigger) || trigger.deviceId !== normalizedId),
      // Silinen cihaza bakan bir koşul sonsuza kadar `false` kalır ve kuralı sessizce öldürürdü;
      // koşul düşer, kural görünür kalır.
      conditions: automation.conditions.filter((condition) =>
        condition.type !== "deviceState" || condition.deviceId !== normalizedId),
      actions: automation.actions.filter((action) =>
        !isAutomationDeviceAction(action) || action.deviceId !== normalizedId)
    }))
    .filter((automation) =>
      (automation.manual === true || automation.triggers.length > 0)
      && automation.actions.some((action) => action.type !== "delay"));
};

/**
 * Bekleyen bir "sonra kapat" — süreç yeniden başlasa da unutulmaz. Işığı açan komut zaten
 * çalıştı; kapatma sözü o komutun parçasıdır, bu yüzden kalıcı tutulur.
 */
export interface AutomationAutoOffEntry {
  automationId: string;
  /** Yalnızca günlük metni için; kural silinse bile söz yerine getirilir. */
  automationName: string;
  deviceId: string;
  property: string;
  /** Geri alınırken yazılacak değer. */
  value: JsonScalar;
  /** Eylemin yazdığı değer — hedef bunun dışına çıktıysa niyet kullanıcınındır. */
  appliedValue: JsonScalar;
  mode: "after" | "idle";
  seconds: number;
  /** Sayaç işliyorsa bitiş anı (ISO); `idle` hareket beklerken null. */
  dueAt: string | null;
  /** `idle` için izlenen tetikleyici kanal. */
  watch: { deviceId: string; property: string; activeValue: JsonScalar } | null;
}

const autoOffDeviceId = (value: unknown, label: string): string => {
  const deviceId = typeof value === "string" ? value.trim().toLowerCase() : "";
  if (!deviceIdPattern.test(deviceId)) throw new Error(`${label} cihaz UID'si geçersiz.`);
  return deviceId;
};

const autoOffProperty = (value: unknown, label: string): string => {
  const property = typeof value === "string" ? value.trim() : "";
  if (!propertyPattern.test(property)) throw new Error(`${label} cihaz özelliği geçersiz.`);
  return property;
};

export const validateAutomationAutoOffEntries = (value: unknown): AutomationAutoOffEntry[] => {
  if (!Array.isArray(value) || value.length > maxAutomationAutoOffEntries) {
    throw new Error("Bekleyen otomatik kapatmalar geçersiz.");
  }
  return value.map((entry) => {
    if (typeof entry !== "object" || entry === null || Array.isArray(entry)) {
      throw new Error("Bekleyen otomatik kapatma geçersiz.");
    }
    const candidate = entry as Record<string, unknown>;
    const automationId = typeof candidate.automationId === "string"
      ? candidate.automationId.trim().toLowerCase()
      : "";
    if (!automationIdPattern.test(automationId)) {
      throw new Error("Bekleyen otomatik kapatma otomasyon kimliği geçersiz.");
    }
    if (!isJsonScalar(candidate.value) || !isJsonScalar(candidate.appliedValue)) {
      throw new Error("Bekleyen otomatik kapatma değeri geçersiz.");
    }
    if (candidate.mode !== "after" && candidate.mode !== "idle") {
      throw new Error("Bekleyen otomatik kapatma türü geçersiz.");
    }
    const seconds = candidate.seconds;
    if (
      typeof seconds !== "number"
      || !Number.isInteger(seconds)
      || seconds < 0
      || seconds > maxAutomationAutoOffSeconds
    ) {
      throw new Error("Bekleyen otomatik kapatma süresi geçersiz.");
    }
    const dueAt = candidate.dueAt;
    if (dueAt !== undefined && dueAt !== null) {
      if (typeof dueAt !== "string" || Number.isNaN(Date.parse(dueAt))) {
        throw new Error("Bekleyen otomatik kapatma zamanı geçersiz.");
      }
    }
    let watch: AutomationAutoOffEntry["watch"] = null;
    if (candidate.watch !== undefined && candidate.watch !== null) {
      const raw = candidate.watch;
      if (typeof raw !== "object" || Array.isArray(raw)) {
        throw new Error("Bekleyen otomatik kapatma izlemesi geçersiz.");
      }
      const source = raw as Record<string, unknown>;
      if (!isJsonScalar(source.activeValue)) {
        throw new Error("Bekleyen otomatik kapatma izleme değeri geçersiz.");
      }
      watch = {
        deviceId: autoOffDeviceId(source.deviceId, "Bekleyen otomatik kapatma izlemesi"),
        property: autoOffProperty(source.property, "Bekleyen otomatik kapatma izlemesi"),
        activeValue: source.activeValue
      };
    }
    const name = typeof candidate.automationName === "string"
      ? candidate.automationName.trim().slice(0, maxAutomationNameLength)
      : "";
    return {
      automationId,
      automationName: name || automationId,
      deviceId: autoOffDeviceId(candidate.deviceId, "Bekleyen otomatik kapatma"),
      property: autoOffProperty(candidate.property, "Bekleyen otomatik kapatma"),
      value: candidate.value,
      appliedValue: candidate.appliedValue,
      mode: candidate.mode,
      seconds,
      dueAt: typeof dueAt === "string" ? dueAt : null,
      watch
    } satisfies AutomationAutoOffEntry;
  });
};

/** Bekleyen kapatmaların durum dosyası — `aliases.ts` deseniyle atomik yazılır. */
export class AutomationAutoOffStore {
  constructor(private readonly path: string) {}

  async get(): Promise<AutomationAutoOffEntry[]> {
    try {
      return validateAutomationAutoOffEntries(JSON.parse(await readFile(this.path, "utf8")));
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
      throw error;
    }
  }

  async save(entries: AutomationAutoOffEntry[]): Promise<void> {
    const validated = validateAutomationAutoOffEntries(entries);
    await writeJsonAtomic(this.path, validated, { mode: 0o600 });
  }
}

interface AutomationsCacheEntry {
  /** Nanosaniye çözünürlüklü değişiklik damgası ve boyut — ikisi de aynıysa dosya aynıdır. */
  mtimeNs: bigint;
  size: bigint;
  /** Doğrulamanın dayandığı cihaz topolojisi sürümü; değişirse yeniden doğrulanır. */
  lookupRevision: number;
  automations: Automation[];
}

export class AutomationsStore {
  private cache: AutomationsCacheEntry | null = null;

  constructor(
    private readonly path: string,
    private readonly lookup?: AutomationDeviceLookup,
    /**
     * Cihaz topolojisi sürümü. `lookup` yalnızca kumanda türlerine bakar; bunlar da yalnızca
     * cihaz listesi değişince değişir. Sürüm verilmezse önbellek dosya damgasına dayanır.
     */
    private readonly lookupRevision?: () => number,
    /** Grup eylemlerinin üyelerini çözer; verilmezse grup içi kilit/siren denetimi atlanır. */
    private readonly groupLookup?: AutomationGroupLookup,
    /**
     * Son çalışma bilgisi. Tanım dosyasına **yazılmaz**; okuma yolunda listeye eklenir, böylece
     * API sözleşmesi (panel kartının alt satırı) değişmeden kalır.
     */
    private readonly runState?: AutomationRunStateStore
  ) {}

  /** Tanım + çalışma durumu; disk temsili yalnız tanımı taşır. */
  private decorate(automations: Automation[]): Automation[] {
    const runState = this.runState;
    if (!runState) return automations.slice();
    return automations.map((automation) => ({ ...automation, ...runState.snapshot(automation.id) }));
  }

  /**
   * Otomasyonlar her cihaz olayında okunur (§6). Diskten okuyup 64 kuralı yeniden doğrulamak
   * olay başına milisaniyeler yiyordu; dosya damgası değişmedikçe önbellekten dönülür.
   */
  async get(): Promise<Automation[]> {
    // Çalışma durumu bir kez okunur; sonraki çağrılarda bellekten gelir.
    await this.runState?.load();
    let stamp: { mtimeNs: bigint; size: bigint } | null = null;
    try {
      const info = await stat(this.path, { bigint: true });
      stamp = { mtimeNs: info.mtimeNs, size: info.size };
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
      this.cache = null;
      return [];
    }
    const revision = this.lookupRevision?.() ?? 0;
    const cached = this.cache;
    if (
      cached
      && cached.mtimeNs === stamp.mtimeNs
      && cached.size === stamp.size
      && cached.lookupRevision === revision
    ) {
      return this.decorate(cached.automations);
    }
    let automations: Automation[];
    try {
      automations = validateAutomations(
        JSON.parse(await readFile(this.path, "utf8")),
        this.lookup,
        this.groupLookup
      );
    } catch (error) {
      this.cache = null;
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
      throw error;
    }
    this.cache = { ...stamp, lookupRevision: revision, automations };
    return this.decorate(automations);
  }

  /**
   * Tanım dosyası **yalnız buradan** yazılır: kullanıcı (ya da ajan) kuralları değiştirdiğinde.
   * `validateAutomations` çalışma alanlarını düşürdüğü için yazılan dosya yalnız tanımı taşır;
   * eski dosyalardaki `lastRunAt`/`lastRunOk` ilk düzenlemede kendiliğinden temizlenir.
   */
  async save(value: unknown): Promise<Automation[]> {
    const automations = validateAutomations(value, this.lookup, this.groupLookup);
    await writeJsonAtomic(this.path, automations, { mode: 0o600 });
    await this.remember(automations);
    // Silinen kuralın tarihçesi durum dosyasında birikmesin.
    await this.runState?.prune(automations.map((automation) => automation.id));
    return this.decorate(automations);
  }

  /** Yazdıktan sonra önbelleği taze damgayla tazeler; aynı milisaniyede yazma yarışı kalmasın. */
  private async remember(automations: Automation[]): Promise<void> {
    try {
      const info = await stat(this.path, { bigint: true });
      this.cache = {
        mtimeNs: info.mtimeNs,
        size: info.size,
        lookupRevision: this.lookupRevision?.() ?? 0,
        automations
      };
    } catch {
      this.cache = null;
    }
  }

  async removeDevice(deviceId: string): Promise<Automation[]> {
    return this.save(removeDeviceFromAutomations(await this.get(), deviceId));
  }

  /**
   * Çalışma sonucu **tanım dosyasına yazılmaz**; ayrı durum dosyasına düşer. Kural yoksa hiçbir
   * şey yazılmaz — silinmiş kuralın tarihçesi geri gelmesin.
   */
  async markRun(id: string, ok: boolean, at: Date = new Date()): Promise<Automation[]> {
    const automations = await this.get();
    const normalizedId = id.trim().toLowerCase();
    if (!automations.some((automation) => automation.id === normalizedId)) return automations;
    await this.runState?.markRun(normalizedId, ok, at);
    return this.decorate(automations);
  }
}
