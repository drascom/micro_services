import { readFile, stat } from "node:fs/promises";
import path from "node:path";

const STATIC_REQUIRE = /require\(\s*(["'])([^"'\n]+)\1\s*\)/g;
const DYNAMIC_REQUIRE = /require\(\s*(?!["'])/g;
const CANDIDATE_SUFFIXES = ["", ".cjs", ".js", ".json"];

function isLocalSpecifier(specifier) {
  return specifier.startsWith("./") || specifier.startsWith("../");
}

// Statik `require("./x.cjs")` cagrilarindaki yerel modul yollarini toplar.
export function collectLocalRequires(source) {
  const specifiers = [];
  for (const match of source.matchAll(STATIC_REQUIRE)) {
    if (isLocalSpecifier(match[2])) {
      specifiers.push(match[2]);
    }
  }
  return specifiers;
}

// Statik olarak izlenemeyen require cagrilari (uyari icin).
export function countDynamicRequires(source) {
  return [...source.matchAll(DYNAMIC_REQUIRE)].length;
}

async function resolveCandidate(file) {
  for (const suffix of CANDIDATE_SUFFIXES) {
    const candidate = `${file}${suffix}`;
    try {
      const info = await stat(candidate);
      if (info.isFile()) {
        return candidate;
      }
    } catch {
      // sonraki adayi dene
    }
  }
  return null;
}

/**
 * Verilen dizindeki giris dosyasindan baslayarak yerel `require` grafigini gezer.
 * Donen deger: { resolved, missing, dynamic } — hepsi dizine gore goreli yollar.
 */
export async function collectRuntimeModuleGraph(directory, entry = "main.cjs") {
  const root = path.resolve(directory);
  const resolved = [];
  const missing = [];
  const dynamic = [];
  const seen = new Set();
  const queue = [path.resolve(root, entry)];

  while (queue.length > 0) {
    const file = queue.shift();
    const relative = path.relative(root, file);
    if (seen.has(file)) {
      continue;
    }
    seen.add(file);

    let source;
    try {
      source = await readFile(file, "utf8");
    } catch {
      missing.push(relative);
      continue;
    }
    resolved.push(relative);
    if (countDynamicRequires(source) > 0) {
      dynamic.push(relative);
    }

    for (const specifier of collectLocalRequires(source)) {
      const target = await resolveCandidate(path.resolve(path.dirname(file), specifier));
      if (target === null) {
        missing.push(path.relative(root, path.resolve(path.dirname(file), specifier)));
        continue;
      }
      queue.push(target);
    }
  }

  return { resolved, missing, dynamic };
}

/**
 * Paketlenmis dizinde modul grafigi cozulemiyorsa hata firlatir.
 * `node --check` yalnizca sozdizimini dogruladigi icin bu adim gerekli.
 */
export async function assertRuntimeModuleGraph(directory, entry = "main.cjs") {
  const graph = await collectRuntimeModuleGraph(directory, entry);
  if (graph.missing.length > 0) {
    throw new Error(
      `Pakette eksik calisma zamani modulu var: ${graph.missing.join(", ")} ` +
        `(giris: ${entry}). Dosyayi scripts/prepare-android-node.mjs icindeki requiredFiles listesine ekleyin.`
    );
  }
  return graph;
}

/**
 * Kaynak dizindeki grafigi `requiredFiles` listesiyle karsilastirir; listeye
 * yazilmayi unutulan yerel modul varsa paketlemeyi baslamadan durdurur.
 */
export async function assertRuntimeFileList(directory, requiredFiles, entry = "main.cjs") {
  const graph = await assertRuntimeModuleGraph(directory, entry);
  const listed = new Set(requiredFiles);
  const unlisted = graph.resolved.filter((file) => !listed.has(file));
  if (unlisted.length > 0) {
    throw new Error(
      `Calisma zamani modulu paket listesinde yok: ${unlisted.join(", ")}. ` +
        "scripts/prepare-android-node.mjs icindeki requiredFiles listesine ekleyin."
    );
  }
  return graph;
}
