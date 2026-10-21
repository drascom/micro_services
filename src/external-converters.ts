import { readFile, readdir } from "node:fs/promises";
import { Module } from "node:module";
import { basename, dirname, extname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { addExternalDefinition, removeExternalDefinitions } from "zigbee-herdsman-converters";

/** Bir converter dosyasının yükleme sonucu; panelde ve günlükte aynen gösterilir. */
export interface ExternalConverterEntry {
  /** Dosya adı (yol değil) — hem kayıt adı hem gösterim etiketi. */
  file: string;
  /** Kütüphaneye eklenen cihaz tanımı sayısı. */
  definitions: number;
  /** Eklenen tanımların `model` alanları; kullanıcı hangi cihazın geldiğini görsün. */
  models: string[];
  /** Dosya yüklenemediyse hata metni. */
  error?: string;
}

export interface ExternalConverterLoadResult {
  /** Taranan klasör. */
  directory: string;
  /** Klasör yoksa `false` — bu normal bir durumdur, hata değildir. */
  directoryExists: boolean;
  loaded: ExternalConverterEntry[];
  failed: ExternalConverterEntry[];
}

/** Dosyalardan gelen ham tanım; kütüphanenin beklediği biçime burada dönüştürülür. */
type RawDefinition = Record<string, unknown>;

type CompilableModule = Module & {
  _compile: (code: string, filename: string) => unknown;
  paths: string[];
  filename: string;
  loaded: boolean;
  exports: unknown;
};

const CONVERTER_EXTENSIONS = new Set([".js", ".cjs", ".mjs"]);

const emptyResult = (directory: string, directoryExists: boolean): ExternalConverterLoadResult => ({
  directory,
  directoryExists,
  loaded: [],
  failed: []
});

let lastResult: ExternalConverterLoadResult | null = null;

/** Son yükleme özeti; ayarlar ucu bunu okuyup kullanıcıya gösterir. */
export function lastExternalConverterLoad(): ExternalConverterLoadResult | null {
  return lastResult;
}

/**
 * Villa Bridge'in kendi `node_modules` zinciri. Converter dosyası nerede durursa dursun
 * `require("zigbee-herdsman-converters/lib/tuya")` bizim sürümümüze düşsün diye önce bu
 * yollar denenir; ancak böylece tanımlar çalışan kütüphaneyle aynı örneği paylaşır.
 */
function bridgeModulePaths(): string[] {
  const here = dirname(fileURLToPath(import.meta.url));
  const paths = (Module as unknown as { _nodeModulePaths: (from: string) => string[] })._nodeModulePaths(here);
  return [...paths];
}

function unwrapExports(value: unknown): RawDefinition[] {
  let current = value;
  if (current && typeof current === "object" && "default" in (current as Record<string, unknown>)) {
    current = (current as Record<string, unknown>).default;
  }
  if (current === undefined || current === null) return [];
  const list = Array.isArray(current) ? current : [current];
  return list.filter((item): item is RawDefinition => typeof item === "object" && item !== null);
}

function isDefinitionLike(definition: RawDefinition): boolean {
  return typeof definition.model === "string"
    && (Array.isArray(definition.zigbeeModel) || Array.isArray(definition.fingerprint));
}

/** CommonJS dosyasını Villa Bridge'in modül yollarıyla derler. */
async function loadCommonJs(file: string): Promise<unknown> {
  const code = await readFile(file, "utf8");
  const instance = new Module(file) as CompilableModule;
  instance.filename = file;
  const localPaths = (Module as unknown as { _nodeModulePaths: (from: string) => string[] })
    ._nodeModulePaths(dirname(file));
  instance.paths = [...bridgeModulePaths(), ...localPaths];
  instance._compile(code, file);
  instance.loaded = true;
  return instance.exports;
}

async function loadEsm(file: string): Promise<unknown> {
  const imported = await import(pathToFileURL(file).href) as Record<string, unknown>;
  return "default" in imported ? imported.default : imported;
}

async function loadFile(file: string): Promise<unknown> {
  const extension = extname(file).toLowerCase();
  if (extension === ".mjs") return loadEsm(file);
  if (extension === ".cjs") return loadCommonJs(file);
  try {
    return await loadCommonJs(file);
  } catch (error) {
    // `export default` kullanan dosyalar CommonJS derleyicisinde sözdizimi hatası verir;
    // Zigbee2MQTT için yazılmış dosyalar iki biçimde de gelebildiği için ESM'e düşülür.
    if (error instanceof SyntaxError) return loadEsm(file);
    throw error;
  }
}

/**
 * Klasördeki harici converter dosyalarını `zigbee-herdsman-converters` kütüphanesine kaydeder.
 * Zigbee2MQTT'nin `external_converters/` kancasının doğrudan modda karşılığıdır: kütüphanede
 * bulunmayan cihazlar (ör. bazı Tuya TS0601 türevleri) böylece tanınır.
 *
 * Hata toleranslıdır: bir dosya patlarsa yalnız o dosya atlanır, servis ayakta kalır.
 */
export async function loadExternalConverters(directory: string): Promise<ExternalConverterLoadResult> {
  let names: string[];
  try {
    names = await readdir(directory);
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code === "ENOENT" || code === "ENOTDIR") {
      lastResult = emptyResult(directory, false);
      return lastResult;
    }
    const result = emptyResult(directory, false);
    result.failed.push({
      file: basename(directory),
      definitions: 0,
      models: [],
      error: error instanceof Error ? error.message : String(error)
    });
    lastResult = result;
    return result;
  }

  const result = emptyResult(directory, true);
  const files = names
    .filter((name) => !name.startsWith(".") && CONVERTER_EXTENSIONS.has(extname(name).toLowerCase()))
    .sort();

  for (const name of files) {
    const file = join(directory, name);
    try {
      const definitions = unwrapExports(await loadFile(file));
      const valid = definitions.filter(isDefinitionLike);
      if (valid.length === 0) {
        result.failed.push({
          file: name,
          definitions: 0,
          models: [],
          error: "Dosyada geçerli cihaz tanımı bulunamadı."
        });
        continue;
      }
      const models: string[] = [];
      for (const definition of valid) {
        definition.externalConverterName = name;
        addExternalDefinition(definition as Parameters<typeof addExternalDefinition>[0]);
        models.push(String(definition.model));
      }
      result.loaded.push({ file: name, definitions: valid.length, models });
    } catch (error) {
      result.failed.push({
        file: name,
        definitions: 0,
        models: [],
        error: error instanceof Error ? error.message : String(error)
      });
    }
  }

  lastResult = result;
  return result;
}

/** Yüklenen tanımları kütüphaneden kaldırır; testler ve yeniden yükleme için. */
export function unloadExternalConverters(converterName?: string): void {
  removeExternalDefinitions(converterName);
  if (!converterName) lastResult = null;
}

/** Türkçe tek satırlık günlük özeti. */
export function describeExternalConverters(result: ExternalConverterLoadResult): string {
  if (!result.directoryExists) {
    return `Harici converter klasörü yok (${result.directory}); atlandı.`;
  }
  if (result.loaded.length === 0 && result.failed.length === 0) {
    return `Harici converter bulunamadı (${result.directory}).`;
  }
  const loaded = result.loaded
    .map((entry) => `${entry.file} (${entry.definitions})`)
    .join(", ");
  const failed = result.failed
    .map((entry) => `${entry.file}: ${entry.error ?? "bilinmeyen hata"}`)
    .join("; ");
  const parts = [`Harici converter yüklendi: ${loaded || "yok"}`];
  if (failed) parts.push(`yüklenemeyen: ${failed}`);
  return `${parts.join(" — ")}.`;
}
