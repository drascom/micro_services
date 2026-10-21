/*
 * Yapisal tutarlilik kapisi. Test degil; panelin ve calisma zamani paketinin
 * kendi icinde tutarli kaldigini dogrular:
 *   - panel dosyalari <-> index.html <-> src/index.ts icindeki panelAssetRoutes kaymasi,
 *     ayristirilamayan panel dosyasi, iki dosyada ayni ust duzey ad (panel-graph.mjs),
 *   - apps/runtime modul grafiginde eksik yerel modul (runtime-module-graph.mjs).
 * Paket listesi (requiredFiles) denetimi paketleme adiminda kalir: npm run android:prepare.
 */
import path from "node:path";
import { fileURLToPath } from "node:url";
import { assertPanelGraph } from "./panel-graph.mjs";
import { assertHomeLayout } from "./check-home-layout.mjs";
import { assertRuntimeModuleGraph } from "./runtime-module-graph.mjs";
import { assertHostAdapters } from "./check-host-adapters.mjs";
import { assertThemePackages } from "./check-theme-packages.mjs";
import { assertPanelInk } from "./check-panel-ink.mjs";
import { verifyAstronomy } from "./verify-astronomy.mjs";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

async function main() {
  const panel = await assertPanelGraph(projectRoot);
  console.log(`Panel grafigi tamam: ${panel.files.length} dosya.`);

  const runtime = await assertRuntimeModuleGraph(path.join(projectRoot, "apps", "runtime"));
  console.log(`Calisma zamani modul grafigi tamam: ${runtime.resolved.length} modul.`);

  const adapters = await assertHostAdapters(projectRoot);
  console.log(
    `Konak adaptoru siniri tamam: ${adapters.files} dosya, ${adapters.flags} calisma zamani bayragi, ` +
      `${adapters.bridgeCount} JS koprusu yontemi.`
  );

  const themes = await assertThemePackages(projectRoot);
  console.log(`Tema paketleri tamam: ${themes.length} paket.`);

  const ink = await assertPanelInk(projectRoot);
  console.log(
    `Yuzey murekkep denetimi tamam: koyu ${ink.surfaces} yuzey / ${ink.tokens.length} token, ` +
      `acik ${ink.lightSurfaces} yuzey / ${ink.lightTokens.length} token, ` +
      `dolu aksan ${ink.accentSurfaces} yuzey.`
  );

  await verifyAstronomy(projectRoot);
  console.log("Astronomi dogrulamasi tamam.");

  const home = await assertHomeLayout(projectRoot);
  console.log(
    `1024x640 ana ekran akis denetimi tamam: baslik ${home.head}px, ` +
      `ust cubuk payi ${home.strip.join("/")}px, panoya kalan ${home.board}px.`
  );
}

main().catch((error) => {
  console.error("Yapisal denetim basarisiz:", error.message ?? error);
  process.exitCode = 1;
});
