/**
 * Tuya TS004x ailesi (TS0041/TS0042/TS0043/TS0044/TS004F) sahne anahtarları buton
 * basışlarını standart `genOnOff` kümesi içinde **standart dışı** `0xFD` komutuyla yollar.
 * Bu komut ZCL sözlüğünde tanımlı olmadığı için `zigbee-herdsman` çerçeveyi çözemez
 * (`frame=undefined`) ve mesaj bize `type: "raw"`, `data: Buffer` olarak ulaşır. Bu durumda
 * `zigbee-herdsman-converters` içindeki `commandTuyaAction` dönüştürücüsü hiç çalışmaz ve
 * `action` üretilmez.
 *
 * Burada ham baytları çözüp Zigbee2MQTT ile **birebir aynı** `action` değerini üretiyoruz.
 * Çerçeve düzeni (üretici bitleri kapalıyken):
 *
 *   [0] frame control  — bit0..1 = 1 (kümeye özel), bit2 = üreticiye özel
 *   [1] transaction sequence number
 *   [2] command id     — 0xFD (tuyaAction)
 *   [3] value          — 0 tek basış, 1 çift basış, 2 basılı tutma
 *
 * Buton numarası endpoint kimliğidir; tek butonlu varyantlarda önek kullanılmaz.
 */

/** Tuya'nın `genOnOff` içine gömdüğü buton olayı komutu. */
export const TUYA_ACTION_COMMAND_ID = 0xfd;

/** Basış tipi sözlüğü — Zigbee2MQTT `tuya.fz.on_off_action` ile aynı. */
const PRESS_TYPES: Readonly<Record<number, string>> = {
  0: "single",
  1: "double",
  2: "hold"
};

/**
 * Birden çok endpoint bildirse de tek butonu olan varyantlar; Zigbee2MQTT bunlarda
 * `1_single` yerine öneksiz `single` üretir.
 */
const SINGLE_BUTTON_MODELS: ReadonlySet<string> = new Set(["TS0041", "TS0041A"]);

/** Tuya sahne anahtarlarında görülen en yüksek buton (endpoint) numarası. */
const MAX_BUTTON_ENDPOINT = 8;

export interface TuyaButtonFrameInput {
  /** Çözümlenemeyen ZCL çerçevesinin ham baytları. */
  readonly data: ArrayLike<number> | undefined;
  /** Çerçevenin geldiği endpoint — buton numarası. */
  readonly endpointId: number;
  /** Cihazın toplam endpoint sayısı; tek endpoint'te buton öneki kullanılmaz. */
  readonly endpointCount: number;
  /** `modelID` — tek butonlu varyantları ayırmak için. */
  readonly modelId?: string | undefined;
}

export type TuyaButtonFrameResult =
  | {
    readonly ok: true;
    /** Zigbee2MQTT sözlüğüyle uyumlu değer, ör. `1_single`. */
    readonly action: string;
    /** ZCL işlem sırası numarası — yinelenen çerçeveleri elemek için. */
    readonly sequence: number;
  }
  | {
    readonly ok: false;
    /** Türkçe teşhis metni; çağıran tarafta loglanır, sessizce düşmez. */
    readonly reason: string;
  };

function hex(value: number): string {
  return `0x${value.toString(16).padStart(2, "0")}`;
}

/**
 * Ham `genOnOff` çerçevesini Tuya buton olayına çevirir. Tanımadığı her varyantta
 * `ok: false` ve Türkçe gerekçe döndürür — sessizce düşmez.
 */
export function decodeTuyaButtonFrame(input: TuyaButtonFrameInput): TuyaButtonFrameResult {
  const bytes = input.data ? Array.from(input.data) : [];
  if (bytes.length < 4) {
    return { ok: false, reason: `çerçeve çok kısa (${bytes.length} bayt)` };
  }
  const frameControl = bytes[0] ?? 0;
  if ((frameControl & 0x03) !== 0x01) {
    return { ok: false, reason: `kümeye özel olmayan çerçeve (frame control ${hex(frameControl)})` };
  }
  // Üreticiye özel bit açıksa araya iki baytlık üretici kodu girer.
  const offset = (frameControl & 0x04) !== 0 ? 3 : 1;
  if (bytes.length < offset + 3) {
    return { ok: false, reason: `çerçeve çok kısa (${bytes.length} bayt)` };
  }
  const sequence = bytes[offset] ?? 0;
  const commandId = bytes[offset + 1] ?? 0;
  if (commandId !== TUYA_ACTION_COMMAND_ID) {
    return { ok: false, reason: `Tuya buton komutu değil (komut ${hex(commandId)})` };
  }
  const rawPressType = bytes[offset + 2] ?? 0;
  const pressType = PRESS_TYPES[rawPressType];
  if (!pressType) {
    return { ok: false, reason: `bilinmeyen basış tipi ${rawPressType}` };
  }
  const endpointId = input.endpointId;
  if (!Number.isInteger(endpointId) || endpointId < 1 || endpointId > MAX_BUTTON_ENDPOINT) {
    return { ok: false, reason: `bilinmeyen buton endpoint'i ${String(endpointId)}` };
  }
  const singleButton = input.endpointCount <= 1 || SINGLE_BUTTON_MODELS.has(input.modelId ?? "");
  const prefix = singleButton ? "" : `${endpointId}_`;
  return { ok: true, action: `${prefix}${pressType}`, sequence };
}
