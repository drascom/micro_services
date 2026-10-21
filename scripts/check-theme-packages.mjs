import path from "node:path";
import { pathToFileURL } from "node:url";

export async function assertThemePackages(projectRoot) {
  const moduleUrl = pathToFileURL(path.join(projectRoot, "dist", "theme-package.js")).href;
  const { loadThemePackages } = await import(moduleUrl);
  // Calisma zamani bozuk paketi ATLAR (ev bir tema dosyasi yuzunden dusmesin); denetim ise
  // KATIDIR: atlanan her paket burada hataya donusur.
  const skipped = [];
  const packages = await loadThemePackages(
    path.join(projectRoot, "public", "themes"),
    (name, error) => skipped.push(`${name}: ${error?.message ?? error}`)
  );
  if (skipped.length > 0) throw new Error(`Tema paketi yuklenemedi -> ${skipped.join(" | ")}`);
  const ids = new Set(packages.map((theme) => theme.id));
  for (const required of ["villa-current", "villa-liquid-glass"]) {
    if (!ids.has(required)) throw new Error(`Zorunlu tema paketi bulunamadi: ${required}`);
  }
  return packages;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const projectRoot = path.resolve(path.dirname(process.argv[1]), "..");
  assertThemePackages(projectRoot)
    .then((packages) => console.log(`Tema paketleri tamam: ${packages.length} paket.`))
    .catch((error) => {
      console.error("Tema paketi denetimi basarisiz:", error.message ?? error);
      process.exitCode = 1;
    });
}
