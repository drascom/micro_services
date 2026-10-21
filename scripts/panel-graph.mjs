import { createHash } from "node:crypto";
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import vm from "node:vm";

/* Panel klasik `<script src>` ile bolundu: derleme adimi yok, kapsam yalitimi da yok. Bu dosya o
   yapinin diskteki gercegini korur — `index.html`'deki etiket sirasi ile `public/js/` icerigi,
   her dosyanin ayristirilabilirligi ve ust duzey ad cakismasi.

   Tek dogrulama noktasi burasidir; `npm run check` ve `npm run android:prepare` calistirir. */

const SCRIPT_TAG = /<script\s+src="([^"]+)"\s*><\/script>/g;
/* Panelde ust duzey bildirimler tam iki bosluk girintili (eski `<script>` blogundan devir).
   Daha derin girinti bir fonksiyon/blok icidir, o yuzden cakisma yaratmaz. */
const TOP_LEVEL_DECLARATION =
  /^ {2}(?:async +function\s*\*?|function\s*\*?|const|let|var|class)\s+([A-Za-z_$][\w$]*)/;
const LAST_PANEL_SCRIPT = "/js/99-bind.js";
const NUMERIC_PREFIX = /^\/js\/(\d+)-/;

/** Belge sirasindaki `<script src>` adresleri. */
export function parsePanelScriptTags(document) {
  return [...document.matchAll(SCRIPT_TAG)].map((match) => match[1]);
}

/** Bir panel dosyasindaki ust duzey bildirim adlari. */
export function collectTopLevelDeclarations(source) {
  const names = [];
  for (const line of source.split("\n")) {
    const match = TOP_LEVEL_DECLARATION.exec(line);
    if (match) {
      names.push(match[1]);
    }
  }
  return names;
}

/** Sozdizimi denetimi: gecerliyse null, degilse hata mesaji. */
export function parseFailure(source) {
  try {
    new vm.Script(source);
    return null;
  } catch (error) {
    return error instanceof Error ? error.message : String(error);
  }
}

function numericPrefix(route) {
  const match = NUMERIC_PREFIX.exec(route);
  return match ? Number(match[1]) : null;
}

/**
 * Panel script grafigini toplar. Donen deger:
 * { tags, files, missing, unlisted, order, parseErrors, duplicates, bundleError }
 * — `missing`: etiketi olup diskte olmayan, `unlisted`: diskte olup etiketi olmayan dosyalar.
 */
export async function collectPanelGraph(projectRoot) {
  const publicRoot = path.resolve(projectRoot, "public");
  const document = await readFile(path.join(publicRoot, "index.html"), "utf8");
  const tags = parsePanelScriptTags(document);
  const files = (await readdir(path.join(publicRoot, "js")))
    .filter((name) => name.endsWith(".js"))
    .sort()
    .map((name) => `/js/${name}`);

  const onDisk = new Set(files);
  const referenced = new Set(tags);
  const missing = tags.filter((route) => !onDisk.has(route));
  const unlisted = files.filter((route) => !referenced.has(route));

  const order = [];
  if (tags.length > 0 && tags.at(-1) !== LAST_PANEL_SCRIPT) {
    order.push(`son dosya ${LAST_PANEL_SCRIPT} olmali, ${tags.at(-1)} bulundu`);
  }
  let previous = null;
  for (const route of tags) {
    const prefix = numericPrefix(route);
    if (prefix === null) continue;
    if (previous !== null && prefix < previous.prefix) {
      order.push(`${route} dosyasi ${previous.route} dosyasindan sonra gelemez`);
    }
    previous = { route, prefix };
  }

  const parseErrors = [];
  const duplicates = [];
  const owners = new Map();
  const bodies = [];
  for (const route of tags) {
    if (missing.includes(route)) continue;
    const source = await readFile(path.join(publicRoot, route.slice(1)), "utf8");
    const failure = parseFailure(source);
    if (failure !== null) {
      parseErrors.push(`${route}: ${failure}`);
      continue;
    }
    bodies.push(source);
    for (const name of collectTopLevelDeclarations(source)) {
      const owner = owners.get(name);
      if (owner === undefined) {
        owners.set(name, route);
        continue;
      }
      if (owner !== route) {
        duplicates.push(`${name} (${owner} + ${route})`);
      }
    }
  }

  /* Tarayicida bu dosyalar tek bir global sozcuksel kapsami paylasir; birlesik metnin de
     ayristirilabilmesi gerekir. Girintisi ad taramasindan kacan bir `const` cakismasi burada
     yakalanir. Dosyalar tek tek gecerli oldugu icin araya konan `;` yalnizca satir sonu
     birlesmesini (ASI) engeller. */
  const bundleError =
    parseErrors.length === 0 && missing.length === 0 ? parseFailure(bodies.join("\n;\n")) : null;

  return { tags, files, missing, unlisted, order, parseErrors, duplicates, bundleError };
}

/** Panel grafigi bozuksa hata firlatir. */
export async function assertPanelGraph(projectRoot) {
  const graph = await collectPanelGraph(projectRoot);
  const problems = [];
  if (graph.missing.length > 0) {
    problems.push(`index.html'de etiketi olup public/js altinda olmayan dosya: ${graph.missing.join(", ")}`);
  }
  if (graph.unlisted.length > 0) {
    problems.push(
      `public/js altinda olup index.html'de etiketi olmayan dosya: ${graph.unlisted.join(", ")}. ` +
        "Etiketi ekleyin ve src/index.ts icindeki panelAssetRoutes tablosunu da guncelleyin."
    );
  }
  if (graph.order.length > 0) {
    problems.push(`Panel script sirasi bozuk: ${graph.order.join("; ")}`);
  }
  if (graph.parseErrors.length > 0) {
    problems.push(`Panel dosyasi ayristirilamadi: ${graph.parseErrors.join("; ")}`);
  }
  if (graph.duplicates.length > 0) {
    problems.push(
      `Ayni ust duzey ad iki panel dosyasinda tanimli: ${graph.duplicates.join(", ")}. ` +
        "Klasik script'te ikinci tanim calisma aninda panelin yarisini dusurur."
    );
  }
  if (graph.bundleError !== null) {
    problems.push(`Panel dosyalari birlikte ayristirilamadi: ${graph.bundleError}`);
  }
  if (problems.length > 0) {
    throw new Error(problems.join(" | "));
  }
  return graph;
}

async function collectFiles(directory, prefix, collected) {
  for (const entry of (await readdir(directory, { withFileTypes: true })).sort((a, b) =>
    a.name < b.name ? -1 : a.name > b.name ? 1 : 0
  )) {
    const relative = `${prefix}/${entry.name}`;
    if (entry.isDirectory()) {
      await collectFiles(path.join(directory, entry.name), relative, collected);
      continue;
    }
    collected.push(relative);
  }
  return collected;
}

/**
 * Panelin butun dosyalarini (index.html + public/css + public/js) kapsayan tek ozet.
 * Dosya sirasi POSIX goreli yola gore alfabetiktir — dizin gezintisinden gelen sira degil —
 * boylece ozet platformdan ve dosya sistemi sirasindan bagimsiz olarak deterministiktir.
 * Yeni bir panel dosyasi hicbir listeye yazilmadan kapsama girer.
 */
export async function panelDigest(projectRoot) {
  const publicRoot = path.resolve(projectRoot, "public");
  const files = ["index.html"];
  for (const directory of ["css", "js"]) {
    await collectFiles(path.join(publicRoot, directory), directory, files);
  }
  files.sort();

  const digest = createHash("sha256");
  for (const relative of files) {
    const content = await readFile(path.join(publicRoot, ...relative.split("/")));
    digest.update(`${relative}\n${createHash("sha256").update(content).digest("hex")}\n`);
  }
  return { files, sha256: digest.digest("hex") };
}
