/*
 * IANA saat dilimi -> POSIX TZ kurali tablosu uretir (apps/runtime/tz-rules.cjs).
 *
 * Neden: Android'deki Node.js Mobile ICU'suz derlenmis; `Intl` yok. `intl-fallback.cjs`
 * baska bir dilimin (ör. dunya saati "Australia/Melbourne") ofsetini bu tablodaki POSIX
 * kuralindan (yaz saati dahil) hesaplar. Kural, TZif v2+ dosyalarinin son satirindaki
 * dipnottur: son gecisten sonraki (yani bugunku ve gelecekteki) davranisi tam tarif eder.
 * Gecmis tarihler icin tarihsel gecisler tasinmaz — cekirdek yalniz "simdi"yi hesaplar.
 *
 * Kullanim: node scripts/generate-tz-rules.mjs [zoneinfo-dizini]  (varsayilan /usr/share/zoneinfo)
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const zoneinfo = path.resolve(process.argv[2] || "/usr/share/zoneinfo");
const SKIP = new Set(["posix", "right", "posixrules", "localtime", "Factory", "SystemV"]);
const rules = {};

function footerOf(buffer) {
  if (buffer.length < 44 || buffer.toString("latin1", 0, 4) !== "TZif") return null;
  if (buffer[4] < 0x32) return null; // v1 dosyada dipnot yok
  const text = buffer.toString("latin1");
  const end = text.lastIndexOf("\n");
  const start = text.lastIndexOf("\n", end - 1);
  if (start < 0 || end <= start) return null;
  const footer = text.slice(start + 1, end);
  return /^[\x20-\x7e]*$/.test(footer) && footer.length > 0 ? footer : null;
}

function walk(directory, prefix) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    if (SKIP.has(entry.name) || entry.name.includes(".")) continue;
    const full = path.join(directory, entry.name);
    const name = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isDirectory()) {
      walk(full, name);
    } else if (entry.isFile() && /^[A-Z]/.test(entry.name)) {
      const footer = footerOf(fs.readFileSync(full));
      if (footer) rules[name] = footer;
    }
  }
}

walk(zoneinfo, "");
const names = Object.keys(rules).sort();
if (!rules["Europe/London"] || !rules["Australia/Melbourne"] || names.length < 300) {
  throw new Error(`zoneinfo tablosu eksik gorunuyor (${names.length} dilim): ${zoneinfo}`);
}
const body = names.map((name) => `  ${JSON.stringify(name)}: ${JSON.stringify(rules[name])}`).join(",\n");
const output = `"use strict";\n\n// Uretildi: scripts/generate-tz-rules.mjs — elle duzenleme. IANA dilimi -> POSIX TZ kurali.\nmodule.exports = {\n${body}\n};\n`;
fs.writeFileSync(path.join(projectRoot, "apps", "runtime", "tz-rules.cjs"), output);
console.log(`tz-rules.cjs: ${names.length} dilim.`);
