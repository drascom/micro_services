/*
 * Konak adaptoru sinir denetimi.
 *
 * Mimari kural: beyin ortaktir (`src/`, `public/`, `apps/runtime/`). `apps/android/`
 * ve `apps/linux/` yalnizca konak-ozel BASLATMA, PAKETLEME ve YASAM DONGUSU kodu
 * tasir. Kullanicinin yapabildigi bir yetenek konak adaptorune yerlesirse iki konak
 * catallanir: bir tarafta olan ozellik otekinde sessizce yoktur. (Yasanmis ornek:
 * dosya tohumlama `apps/linux/install.sh` icindeydi, Android'de karsiligi yoktu;
 * `apps/runtime/first-run.cjs`'e tasininca duzeldi.)
 *
 * Burada YALNIZCA mekanik olarak kanitlanabilen, yanlis alarm uretmeyen dort sinyal
 * aranir. Genel "zigbee gecen satiri yakala" turu bir tarama bilerek YAZILMADI:
 * konak adaptorunde kullaniciya gosterilen "Zigbee koordinatorune baglaniliyor"
 * gibi metinler mesrudur ve boyle bir kural sadece gurultu uretirdi.
 *
 *   1. Calisma zamani bayrak sozlesmesi — konagin `apps/runtime/main.cjs`'e verdigi
 *      her `--bayrak` gercekten ayristirilan bir bayrak olmali. Taninmayan bayrak
 *      sessizce dusuruldugu icin konaklar farkli davranir.
 *   2. Paylasilan yapilandirma/durum dosyalari — bu dosyalarin adlarini yalnizca
 *      paylasilan calisma zamani bilir. Konak betigi bir dosya adini yazarsa o
 *      bilgi tek konakta kalir.
 *   3. Cihaz modeli ve ev protokolleri — konak adaptoru Zigbee/MQTT/Matter alan
 *      adlariyla is yapmaz; bunlar cekirdegin isidir.
 *   4. Android JS koprusu — panele acilan her `@JavascriptInterface` yontemi, ortak
 *      kodda (`public/`, `apps/runtime/`) bir tuketiciye sahip olmali. Sahipsiz
 *      yontem, yalnizca tek konakta yasayan bir yetenektir.
 *
 * Izin listesi bilerek bostur; yeni istisna eklemeden once yetenegi ortak koda
 * tasimayi dene.
 */
import fs from "node:fs/promises";
import path from "node:path";

/** Konak adaptorleri: yalnizca baslatma/paketleme/yasam dongusu. */
const ADAPTER_ROOTS = ["apps/android", "apps/linux"];

/** Metin olarak taranan kaynak dosyalar. Uretilmis ciktilar ve belgeler haric. */
const SCANNED_EXTENSIONS = new Set([".kt", ".kts", ".sh", ".in", ".xml", ".cpp", ".gradle"]);

/**
 * Taranmayan dizinler.
 *  - `build`, `.cxx`, `.gradle`, `gradle`: derleyici ciktisi ve sarmalayici.
 *  - `node_modules`, `node-runtime`: satici agaci.
 *  - `patches`: satici bagimliligina uygulanan konak-ozel yama (Android'de seri
 *    tasima). Urun mantigi degil, tasiyici duzeltmesidir; adi geregi tek konakta olur.
 */
const IGNORED_DIRECTORIES = new Set([
  "build",
  ".cxx",
  ".gradle",
  "gradle",
  "node_modules",
  "node-runtime",
  "patches",
  ".idea"
]);

/** Ortak kodun tarandigi yerler: JS koprusunun tuketicileri burada aranir. */
const SHARED_CODE_ROOTS = ["public", "apps/runtime"];
const SHARED_CODE_EXTENSIONS = new Set([".js", ".cjs", ".mjs", ".html", ".ts"]);

/**
 * Cihaz modeli / ev protokolu izleri. Hepsi ya bir alan adi ya bir protokol
 * adresi; konak baslaticisinda hicbirinin mesru kullanimi yoktur.
 */
const DOMAIN_PATTERNS = [
  { pattern: /friendly_name|ieee_?address|ieeeAddr/i, what: "cihaz kimlik alani" },
  { pattern: /zigbee2mqtt\//i, what: "Zigbee2MQTT konu onu" },
  { pattern: /herdsman/i, what: "Zigbee yigini" },
  { pattern: /network_key|ext_pan_id|pan_id/i, what: "Zigbee ag parametresi" },
  { pattern: /mqtt:\/\/|MqttClient|mqtt\.connect/i, what: "dogrudan MQTT istemcisi" },
  { pattern: /matterbridge/i, what: "Matterbridge surumu" },
  { pattern: /\/api\/(devices|groups|settings)\b/i, what: "cekirdek cihaz API cagrisi" }
];

async function walk(root, keep) {
  const found = [];
  let entries;
  try {
    entries = await fs.readdir(root, { withFileTypes: true });
  } catch {
    return found;
  }
  for (const entry of entries) {
    const full = path.join(root, entry.name);
    if (entry.isDirectory()) {
      if (IGNORED_DIRECTORIES.has(entry.name)) continue;
      found.push(...(await walk(full, keep)));
    } else if (entry.isFile() && keep(entry.name)) {
      found.push(full);
    }
  }
  return found;
}

function scannedAdapterFile(name) {
  if (name === "local.properties") return false;
  const extension = path.extname(name);
  if (SCANNED_EXTENSIONS.has(extension)) return true;
  // `villa-bridge.service.in` gibi sablonlar.
  return name.endsWith(".service.in");
}

/**
 * Aciklama satirlarini siler. Bir kuralin ihlali kodda olur; aciklamada gecen
 * dosya adi ya da protokol adi kasitli ve faydalidir (`install.sh` tam olarak
 * "bu isi ben YAPMIYORUM" demek icin bu adlari yaziyor).
 */
function stripComments(source, file) {
  const extension = path.extname(file);
  if (extension === ".xml") {
    return source.replace(/<!--[\s\S]*?-->/g, (block) => block.replace(/[^\n]/g, " "));
  }
  if (extension === ".kt" || extension === ".kts" || extension === ".cpp" || extension === ".gradle") {
    return source
      .replace(/\/\*[\s\S]*?\*\//g, (block) => block.replace(/[^\n]/g, " "))
      .replace(/(^|[^:/])\/\/[^\n]*/g, (match, prefix) => prefix + " ".repeat(match.length - prefix.length));
  }
  // Kabuk ve systemd: satir basindaki ya da bosluk sonrasi `#`.
  return source.replace(/(^|\s)#[^\n]*/gm, (match, prefix) => prefix + " ".repeat(match.length - prefix.length));
}

function lineOf(source, index) {
  return source.slice(0, index).split("\n").length;
}

/** `apps/runtime/main.cjs` icindeki parseArguments'in gercekten okudugu bayraklar. */
async function runtimeFlags(projectRoot) {
  const file = path.join(projectRoot, "apps", "runtime", "main.cjs");
  const source = await fs.readFile(file, "utf8");
  const body = /function parseArguments\([\s\S]*?\n}\n/.exec(source);
  if (!body) {
    throw new Error(
      "apps/runtime/main.cjs icinde parseArguments bulunamadi; " +
        "konak bayrak sozlesmesi dogrulanamiyor (scripts/check-host-adapters.mjs)."
    );
  }
  const flags = new Set();
  for (const match of body[0].matchAll(/values\["([a-z][a-z-]*)"\]/g)) flags.add(match[1]);
  for (const match of body[0].matchAll(/values\.([a-z][a-zA-Z]*)/g)) flags.add(match[1]);
  return flags;
}

/**
 * Paylasilan calisma zamaninin sahibi oldugu dosya adlari. Elle listelenmez:
 * tohumlanan dosyalar `apps/runtime/templates/` icinden, degisken durum dosyalari
 * `src/index.ts` icinden turetilir; boylece liste kendiliginden guncel kalir.
 */
async function sharedDataFiles(projectRoot) {
  const names = new Set();
  const templates = await fs.readdir(path.join(projectRoot, "apps", "runtime", "templates"));
  for (const template of templates) names.add(template.replace(".example", ""));
  const indexSource = await fs.readFile(path.join(projectRoot, "src", "index.ts"), "utf8");
  for (const match of indexSource.matchAll(/dirname\(configPath\),\s*"([^"]+\.json)"/g)) names.add(match[1]);
  return names;
}

/** Panele acilan Android kopru yontemleri. */
function bridgeMethods(source) {
  const methods = [];
  for (const match of source.matchAll(
    /@(?:android\.webkit\.)?JavascriptInterface\s+fun\s+([A-Za-z0-9_]+)\s*\(/g
  )) {
    methods.push({ name: match[1], index: match.index });
  }
  return methods;
}

export async function assertHostAdapters(projectRoot) {
  const problems = [];
  const flags = await runtimeFlags(projectRoot);
  const dataFiles = await sharedDataFiles(projectRoot);

  const adapterFiles = [];
  for (const root of ADAPTER_ROOTS) {
    adapterFiles.push(...(await walk(path.join(projectRoot, root), scannedAdapterFile)));
  }
  adapterFiles.sort();

  const sharedSources = [];
  for (const root of SHARED_CODE_ROOTS) {
    sharedSources.push(
      ...(await walk(path.join(projectRoot, root), (name) =>
        SHARED_CODE_EXTENSIONS.has(path.extname(name))
      ))
    );
  }
  const sharedText = (
    await Promise.all(sharedSources.map((file) => fs.readFile(file, "utf8")))
  ).join("\n");

  let bridgeCount = 0;

  for (const file of adapterFiles) {
    const relative = path.relative(projectRoot, file);
    const raw = await fs.readFile(file, "utf8");
    const code = stripComments(raw, file);

    // 1. Calisma zamani bayrak sozlesmesi.
    if (code.includes("main.cjs")) {
      for (const match of code.matchAll(/--([a-z][a-z-]*)=/g)) {
        if (flags.has(match[1])) continue;
        problems.push(
          `${relative}:${lineOf(code, match.index)} — "--${match[1]}" calisma zamaninin ` +
            "tanidigi bir bayrak degil ve sessizce dusurulur, yani bu konak ayari hic uygulanmaz. " +
            "Gecerli bayraklar: " +
            `${[...flags].sort().join(", ")}. Yeni bir ayar gerekiyorsa once ` +
            "apps/runtime/main.cjs icindeki parseArguments'a ekle, sonra HER IKI konaktan da gec."
        );
      }
    }

    // 2. Paylasilan yapilandirma/durum dosyalari.
    for (const name of dataFiles) {
      const pattern = new RegExp(name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g");
      for (const match of code.matchAll(pattern)) {
        problems.push(
          `${relative}:${lineOf(code, match.index)} — "${name}" paylasilan calisma zamaninin ` +
            "sahibi oldugu bir dosya. Adini konak adaptorunde yazmak o bilgiyi tek konaga hapseder " +
            "(Android'de kabuk ve kurulum betigi yoktur). Dosyayi ureten/okuyan mantik " +
            "apps/runtime/ icine ait; konak yalnizca --data-dir verir ve durumu " +
            "/api/android/diagnostics uzerinden sorar."
        );
      }
    }

    // 3. Cihaz modeli ve ev protokolleri.
    for (const { pattern, what } of DOMAIN_PATTERNS) {
      const global = new RegExp(pattern.source, `${pattern.flags.replace("g", "")}g`);
      for (const match of code.matchAll(global)) {
        problems.push(
          `${relative}:${lineOf(code, match.index)} — "${match[0]}" (${what}) konak adaptorunde. ` +
            "Cihaz modeli ve ev protokolleri cekirdegin isidir: mantigi src/ ya da apps/runtime/ " +
            "icine tasi, konak yalnizca calisma zamanini baslatsin ve HTTP uzerinden sorsun."
        );
      }
    }

    // 4. Android JS koprusu.
    for (const method of bridgeMethods(code)) {
      bridgeCount += 1;
      const consumer = new RegExp(`VillaAndroid\\??\\.\\s*${method.name}\\b`);
      if (consumer.test(sharedText)) continue;
      problems.push(
        `${relative}:${lineOf(code, method.index)} — JS koprusu yontemi "${method.name}" ortak kodda ` +
          "kullanilmiyor. Kopruye acilan her yontem, yalnizca Android'de var olan bir yetenektir; " +
          "ya public/js icinden (ozellik denetimiyle) cagir, ya da kaldir. Sahipsiz kopru yontemi " +
          "iki konak arasinda catallanmanin baslangicidir."
      );
    }
  }

  if (problems.length > 0) {
    throw new Error(
      `Konak adaptoru sinir ihlali (${problems.length}):\n  - ${problems.join("\n  - ")}`
    );
  }

  return { files: adapterFiles.length, flags: flags.size, dataFiles: dataFiles.size, bridgeCount };
}
