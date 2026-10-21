// Tuya TS0601 sirenli kapı/pencere kontağı (_TZE284_u8ouaqsz).
//
// Cihaz: manyetik kapı kontağı + dahili siren. Kapı açıldığında sesli alarm çalabiliyor;
// alarmın kendisi (`alarm`), ses düzeyi (`volume`) ve çalma süresi (`duration`) Tuya
// datapoint'leriyle yazılabiliyor.
//
// Neden bu dosya: zigbee-herdsman-converters kütüphanesinde `_TZE284_u8ouaqsz` için tanım yok.
// Tanım olmadan cihaz "bilinmeyen" kalır, hiçbir expose üretilmez. Villa Bridge doğrudan modda
// bu klasörü (`zigbee.externalConvertersDir`) okuyup dosyayı kütüphaneye kaydeder.
//
// TUZAK — bir kez düşüldü, bir daha düşülmesin:
// İnternetteki kopyalarda `const e = require(".../lib/exposes"); e.presets.binary(...)` kalıbı
// dolaşıyor. `binary`/`numeric` gibi jenerik yapıcılar exposes modülünün **kendisinde**
// bulunur; `presets` ise hazır cihaz özellikleri sözlüğüdür (`contact`, `battery`, ...).
// Kütüphanenin bazı sürümlerinde `presets` jenerik yapıcıları hiç içermez ve `e.presets.binary`
// "is not a function" ile patlar — dosya yüklenmez, tanım kütüphaneye hiç girmez, cihaz yine
// "bilinmeyen" kalır. Bu yüzden burada jenerik yapıcılar `exposes.binary` / `exposes.numeric`
// olarak, hazır özellikler `exposes.presets.contact()` olarak çağrılır. Her iki sürümde de
// çalışan biçim budur. `src/external-converters.test.ts` bu dosyayı gerçekten yükleyerek
// hatanın sessizce geri gelmesini engeller.

const tuya = require("zigbee-herdsman-converters/lib/tuya");
const exposes = require("zigbee-herdsman-converters/lib/exposes");

const e = exposes.presets;
const ea = exposes.access;

module.exports = [
  {
    fingerprint: tuya.fingerprint("TS0601", ["_TZE284_u8ouaqsz"]),
    model: "TS0601_u8ouaqsz",
    vendor: "Tuya",
    description: "Sirenli kapı/pencere kontağı (TS0601)",
    fromZigbee: [tuya.fz.datapoints],
    toZigbee: [tuya.tz.datapoints],
    configure: tuya.configureMagicPacket,
    exposes: [
      e.contact(),
      e.battery(),
      exposes
        .binary("alarm", ea.STATE_SET, "ON", "OFF")
        .withDescription("Sireni çaldır."),
      exposes
        .numeric("volume", ea.STATE_SET)
        .withValueMin(1)
        .withValueMax(100)
        .withValueStep(1)
        .withUnit("%")
        .withDescription("Siren ses düzeyi."),
      exposes
        .numeric("duration", ea.STATE_SET)
        .withValueMin(3)
        .withValueMax(180)
        .withValueStep(1)
        .withUnit("s")
        .withDescription("Siren çalma süresi.")
    ],
    meta: {
      tuyaDatapoints: [
        // DP 1 ham değeri "açık mı?" anlamında; `contact` expose'u ise ters yönde
        // ("kapalı mı?") tanımlı, o yüzden değer çevrilir. Kurulu sürümde (26.46.0)
        // `valueConverter.trueFalseInvert` var ve `from`/`to` ikisi de `(v) => !v`.
        // (`valueConverter.inverse` de birebir aynı işi yapıyor; adı daha açık olduğu ve
        // sunucudaki dosyada da bu kullanıldığı için `trueFalseInvert` seçildi.)
        [1, "contact", tuya.valueConverter.trueFalseInvert],
        [2, "battery", tuya.valueConverter.raw],
        // `onOff` = lookup({ON: true, OFF: false}); expose'un ON/OFF metinleriyle eşleşir.
        [101, "alarm", tuya.valueConverter.onOff],
        [103, "volume", tuya.valueConverter.raw],
        [104, "duration", tuya.valueConverter.raw]
      ]
    }
  }
];
