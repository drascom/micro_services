"use strict";

// zigbee-herdsman, `patchBigIntSerialization` ile global olarak
// `BigInt.prototype.toJSON` tanimlar. `JSON.stringify` bu metodu replacer'dan
// once cagirdigi icin matter.js'in `toJson` serilestiricisindeki
// `typeof value === "bigint"` dali hic calismaz ve BigInt alanlari Matter
// deposuna duz string olarak yazilir. Sonraki aciliste matter.js bu degerleri
// reddeder ("Value ... is not a number or bigint") ve Apple Home eslesmesi kirilir.
//
// `@matter/general` ESM (ve CJS) yapisinda `toJson` disari salt-okunur olarak
// veriliyor; calisma zamaninda dogrudan sarmalanamiyor. Bu yuzden `toJson`'i
// cagiran matter depo siniflarinin prototip metotlarini sarmaliyoruz: yama
// yalnizca o senkron aralik boyunca kaldirilir, ardindan aynen geri konur.

const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");
const { pathToFileURL } = require("node:url");

const GUARD_FLAG = Symbol.for("villa-bridge.matter-bigint-guard");

const GUARDED_METHODS = [
  ["StorageBackendDiskAsync", "set"],
  ["StorageBackendDisk", "set"],
  ["StorageBackendJsonFile", "toJson"]
];

/**
 * `BigInt.prototype.toJSON` yamasini gecici olarak kaldirir ve geri koyan bir
 * fonksiyon dondurur. Yama yoksa hicbir sey yapmaz (prototipe `toJSON` eklemez).
 */
function suspendBigIntJsonPatch() {
  if (!Object.prototype.hasOwnProperty.call(BigInt.prototype, "toJSON")) {
    return () => {};
  }
  const descriptor = Object.getOwnPropertyDescriptor(BigInt.prototype, "toJSON");
  delete BigInt.prototype.toJSON;
  let restored = false;
  return () => {
    if (restored) return;
    restored = true;
    Object.defineProperty(BigInt.prototype, "toJSON", descriptor);
  };
}

/**
 * Verilen fonksiyonu, senkron govdesi boyunca `BigInt.prototype.toJSON`
 * yamasindan uzak calisacak sekilde sarmalar. Iki kez uygulanirsa cift
 * sarmalamaz.
 */
function guardBigIntJson(fn) {
  if (typeof fn !== "function") {
    throw new TypeError("BigInt korumasi yalnizca fonksiyonlara uygulanabilir.");
  }
  if (fn[GUARD_FLAG] === true) return fn;
  const guarded = function (...args) {
    const restore = suspendBigIntJsonPatch();
    try {
      return fn.apply(this, args);
    } finally {
      restore();
    }
  };
  Object.defineProperty(guarded, GUARD_FLAG, { value: true });
  Object.defineProperty(guarded, "name", { value: fn.name, configurable: true });
  Object.defineProperty(guarded, "length", { value: fn.length, configurable: true });
  return guarded;
}

/** Sarmalanmis bir metodu tanir (test ve idempotans kontrolu icin). */
function isGuarded(value) {
  return typeof value === "function" && value[GUARD_FLAG] === true;
}

/**
 * Matter depo siniflarinin `toJson` cagiran metotlarini sarmalar.
 * Sarmalanan `sinif.metot` adlarini dondurur.
 */
function guardMatterStorage(namespace) {
  const guarded = [];
  if (!namespace || typeof namespace !== "object") return guarded;
  for (const [className, methodName] of GUARDED_METHODS) {
    const target = namespace[className];
    const prototype = typeof target === "function" ? target.prototype : undefined;
    if (!prototype || typeof prototype[methodName] !== "function") continue;
    const original = prototype[methodName];
    if (isGuarded(original)) continue;
    prototype[methodName] = guardBigIntJson(original);
    guarded.push(`${className}.${methodName}`);
  }
  return guarded;
}

function findPackageRoot(entryFile, packageName) {
  let directory = path.dirname(entryFile);
  for (let depth = 0; depth < 10; depth += 1) {
    const packageFile = path.join(directory, "package.json");
    if (fs.existsSync(packageFile)) {
      try {
        if (JSON.parse(fs.readFileSync(packageFile, "utf8")).name === packageName) {
          return directory;
        }
      } catch {
        // bozuk package.json: ust dizine devam et
      }
    }
    const parent = path.dirname(directory);
    if (parent === directory) break;
    directory = parent;
  }
  return null;
}

/**
 * Matterbridge'in fiilen yukledigi `@matter/nodejs` ESM giris dosyasini bulur.
 * Matterbridge ESM oldugu icin `exports["."].import` dali kullanilir; CJS dali
 * calisma zamaninda hic yuklenmez.
 */
function resolveMatterNodejsEsmEntry(searchDirectory) {
  const resolver = createRequire(path.join(searchDirectory, "villa-bridge-resolver.cjs"));
  const cjsEntry = resolver.resolve("@matter/nodejs");
  const root = findPackageRoot(cjsEntry, "@matter/nodejs");
  if (!root) return null;
  const manifest = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8"));
  const relative = manifest.exports?.["."]?.import?.default ||
    manifest.module ||
    manifest.main;
  if (typeof relative !== "string") return null;
  const entry = path.resolve(root, relative);
  return fs.existsSync(entry) ? entry : null;
}

function defaultSearchDirectory() {
  const matterbridgeEntry = path.join(
    __dirname,
    "node_modules",
    "matterbridge",
    "package.json"
  );
  return fs.existsSync(matterbridgeEntry)
    ? path.dirname(matterbridgeEntry)
    : __dirname;
}

/**
 * Matterbridge yuklendikten sonra, ilk depo yazimindan once cagrilir.
 * Matter modulu bulunamazsa sessizce (uyariyla) devam eder.
 */
async function installMatterBigIntGuard(options = {}) {
  const searchDirectory = options.searchDirectory || defaultSearchDirectory();
  const importModule = options.importModule || ((specifier) => import(specifier));
  const log = options.log || (() => {});
  let entry = null;
  try {
    entry = resolveMatterNodejsEsmEntry(searchDirectory);
  } catch (error) {
    log(`Matter BigInt korumasi cozumlenemedi: ${error instanceof Error ? error.message : String(error)}`);
    return { guarded: [], entry: null };
  }
  if (!entry) {
    log("Matter BigInt korumasi atlandi: @matter/nodejs bulunamadi.");
    return { guarded: [], entry: null };
  }
  const namespace = await importModule(pathToFileURL(entry).href);
  const guarded = guardMatterStorage(namespace);
  log(
    guarded.length
      ? `Matter BigInt korumasi etkin: ${guarded.join(", ")}`
      : "Matter BigInt korumasi zaten etkin."
  );
  return { guarded, entry };
}

module.exports = {
  GUARDED_METHODS,
  guardBigIntJson,
  guardMatterStorage,
  installMatterBigIntGuard,
  isGuarded,
  resolveMatterNodejsEsmEntry,
  suspendBigIntJsonPatch
};
