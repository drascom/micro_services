import { mkdir, readFile } from "node:fs/promises";
import { dirname } from "node:path";
import { writeJsonAtomic } from "./atomic-file.js";

export async function loadAliases(path?: string): Promise<Map<string, string>> {
  if (!path) return new Map();
  try {
    const parsed = JSON.parse(await readFile(path, "utf8")) as Record<string, unknown>;
    return new Map(
      Object.entries(parsed)
        .filter((entry): entry is [string, string] => typeof entry[1] === "string")
        .map(([key, value]) => [key.toLowerCase(), value.trim()])
    );
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    console.warn(`İngilizce isim dosyası okunamadı: ${reason}`);
    return new Map();
  }
}

export async function saveAliases(path: string | undefined, aliases: Map<string, string>): Promise<void> {
  if (!path) throw new Error("Ortak isim dosyası yapılandırılmamış.");
  await mkdir(dirname(path), { recursive: true });
  const sorted = Object.fromEntries(
    [...aliases.entries()]
      .filter(([, value]) => value.trim().length > 0)
      .sort(([left], [right]) => left.localeCompare(right, "en"))
  );
  await writeJsonAtomic(path, sorted);
}
