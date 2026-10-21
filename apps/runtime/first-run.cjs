"use strict";

/**
 * Ilk acilis tohumlamasi — HER konakta ayni yol.
 *
 * Android tablette kabuk, `install.sh` ve dosya duzenleme YOKTUR; kurulum yalnizca
 * panelden yapilir. Bu yuzden "eksik yapilandirmayi ornekten uret" mantigi kabuk
 * betiginde degil burada, paylasilan calisma zamaninda durur. `install.sh` yalnizca
 * isletim sistemi duzeyindeki isleri (paket, kullanici, systemd, dizin sahipligi)
 * yapar.
 *
 * Iki pazarliksiz kural:
 *   1. Var olan dosyanin uzerine YAZILMAZ. Ikinci calistirmada hicbir sey degismez.
 *   2. Gizli degerler (Zigbee ag anahtari, pan id, MQTT parolasi) depoda SABIT
 *      DURMAZ; ornek dosyadaki `GENERATE` isaretcileri burada, `node:crypto` ile
 *      uretilen rastgele degerlerle degistirilir. Boylece her kurulum kendi
 *      anahtariyla cikar.
 */

const { randomBytes, randomInt } = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const TEMPLATE_DIR = path.join(__dirname, "templates");
const CONFIG_MODE = 0o600;
const DIRECTORY_MODE = 0o700;

/**
 * Kurulmamis koordinatoru isaretleyen yer tutucu adres (RFC 5737 belgeleme araligi).
 * Gercek bir koordinator olamaz. Ayni sabit `src/config.ts` icinde de vardir —
 * ikisi birlikte degismelidir.
 */
const PLACEHOLDER_COORDINATOR = "tcp://192.0.2.10:6638";

/** 16 baytlik Zigbee ag anahtari; Zigbee2MQTT bicimiyle sayi dizisi. */
function generateNetworkKey() {
  return `[${[...randomBytes(16)].join(", ")}]`;
}

/** 8 baytlik genisletilmis pan id. */
function generateExtendedPanId() {
  return `[${[...randomBytes(8)].join(", ")}]`;
}

/** 0x0000 ve 0xFFFF ayrilmistir; 0x1A62 Zigbee2MQTT'nin herkeste ayni olan varsayilanidir. */
function generatePanId() {
  let value = 0x1a62;
  while (value === 0x1a62) value = randomInt(1, 0xfffe);
  return String(value);
}

/** MQTT parolasi: url/YAML guvenli alfabe, 24 karakter. */
function generatePassword() {
  const alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  let value = "";
  for (const byte of randomBytes(24)) value += alphabet[byte % alphabet.length];
  return value;
}

const GENERATORS = {
  network_key: generateNetworkKey,
  ext_pan_id: generateExtendedPanId,
  pan_id: generatePanId,
  password: generatePassword
};

/**
 * `anahtar: GENERATE` satirlarini uretilen degerle degistirir. YAML'i yeniden
 * yazmak yerine metin uzerinde calisiriz: ornekteki aciklama satirlari kullanicinin
 * dosyasinda aynen kalsin.
 */
function materializeTemplate(source) {
  const unknown = [];
  const output = source.replace(
    /^([ \t]*)([A-Za-z0-9_]+):[ \t]*GENERATE[ \t]*$/gm,
    (line, indent, key) => {
      const generator = GENERATORS[key];
      if (!generator) {
        unknown.push(key);
        return line;
      }
      return `${indent}${key}: ${generator()}`;
    }
  );
  if (unknown.length > 0) {
    throw new Error(`Ornek dosyada uretilemeyen GENERATE alani var: ${unknown.join(", ")}`);
  }
  return output;
}

function ensureDirectory(directory) {
  fs.mkdirSync(directory, { recursive: true, mode: DIRECTORY_MODE });
}

/**
 * Hedef dosya varsa dokunulmaz. Yazma once gecici dosyaya yapilir, sonra tasinir:
 * yarim yazilmis bir yapilandirma dosyasi hicbir zaman diskte kalmaz.
 */
function seedFile(templateName, target, transform) {
  if (fs.existsSync(target)) return { target, created: false };
  const source = fs.readFileSync(path.join(TEMPLATE_DIR, templateName), "utf8");
  const content = typeof transform === "function" ? transform(source) : source;
  const temporary = `${target}.seed-${process.pid}`;
  fs.writeFileSync(temporary, content, { mode: CONFIG_MODE });
  try {
    fs.renameSync(temporary, target);
  } catch (error) {
    fs.rmSync(temporary, { force: true });
    throw error;
  }
  return { target, created: true };
}

/**
 * Veri dizinini ve ilk acilis yapilandirmasini hazirlar.
 * Donen deger gunluge yazilir; hicbir gizli deger iceremez.
 */
function ensureFirstRunFiles(config) {
  const dataDir = config.dataDir;
  const configDir = path.join(dataDir, "config");
  const zigbeeDir = path.join(dataDir, "zigbee");
  for (const directory of [dataDir, configDir, zigbeeDir]) ensureDirectory(directory);

  const results = [];
  // Android'in eski `android-provisioning.json` dosyasi varsa ikinci bir dosya yaratilmaz.
  const legacyProvisioning = path.join(dataDir, "android-provisioning.json");
  if (!fs.existsSync(legacyProvisioning)) {
    results.push(seedFile("provisioning.example.json", path.join(dataDir, "provisioning.json")));
  }
  results.push(seedFile("villa-bridge.example.yaml", path.join(configDir, "villa-bridge.yaml")));
  results.push(seedFile(
    "configuration.example.yaml",
    path.join(zigbeeDir, "configuration.yaml"),
    materializeTemplate
  ));
  // Ornek dosya kullanicinin yaninda dursun: neyin ne oldugunu okuyabilsin.
  results.push(seedFile(
    "configuration.example.yaml",
    path.join(zigbeeDir, "configuration.example.yaml")
  ));

  const created = results.filter((entry) => entry.created).map((entry) => path.relative(dataDir, entry.target));
  const kept = results.filter((entry) => !entry.created).map((entry) => path.relative(dataDir, entry.target));
  return { dataDir, created, kept };
}

module.exports = {
  PLACEHOLDER_COORDINATOR,
  ensureFirstRunFiles,
  materializeTemplate,
  seedFile
};
