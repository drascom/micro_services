import { createHash } from "node:crypto";
import {
  copyFile,
  mkdir,
  readFile,
  rename,
  rm,
  stat,
  writeFile
} from "node:fs/promises";
import { join } from "node:path";
import { atomicTemporaryPath } from "./atomic-file.js";

const format = "villa-bridge-zigbee-backup";
const version = 1;
const allowedFiles = [
  "coordinator_backup.json",
  "database.db",
  "database.db.backup",
  "state.json"
] as const;
const maximumFileBytes = 16 * 1024 * 1024;

interface BackupFile {
  data: string;
  sha256: string;
}

export interface ZigbeeNetworkBackup {
  format: typeof format;
  version: typeof version;
  createdAt: string;
  files: Partial<Record<(typeof allowedFiles)[number], BackupFile>>;
}

const digest = (value: Buffer): string =>
  createHash("sha256").update(value).digest("hex");

const decodeFile = (name: string, value: unknown): Buffer => {
  if (
    typeof value !== "object"
    || value === null
    || Array.isArray(value)
    || typeof (value as BackupFile).data !== "string"
    || typeof (value as BackupFile).sha256 !== "string"
  ) {
    throw new Error(`Yedek dosyası geçersiz: ${name}`);
  }
  const encoded = (value as BackupFile).data;
  if (!/^[A-Za-z0-9+/_=-]*$/.test(encoded)) throw new Error(`Yedek kodlaması geçersiz: ${name}`);
  const buffer = Buffer.from(encoded, "base64");
  if (buffer.length === 0 || buffer.length > maximumFileBytes) {
    throw new Error(`Yedek dosyası boyutu geçersiz: ${name}`);
  }
  if (digest(buffer) !== (value as BackupFile).sha256.toLowerCase()) {
    throw new Error(`Yedek bütünlük kontrolü başarısız: ${name}`);
  }
  return buffer;
};

export const validateZigbeeNetworkBackup = (
  value: unknown
): { backup: ZigbeeNetworkBackup; decoded: Map<string, Buffer> } => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("Zigbee ağ yedeği geçersiz.");
  }
  const candidate = value as Partial<ZigbeeNetworkBackup>;
  if (
    candidate.format !== format
    || candidate.version !== version
    || typeof candidate.createdAt !== "string"
    || !candidate.files
    || typeof candidate.files !== "object"
    || Array.isArray(candidate.files)
  ) {
    throw new Error("Bu dosya Villa Bridge Zigbee ağ yedeği değil.");
  }
  const names = Object.keys(candidate.files);
  if (!names.includes("coordinator_backup.json") || !names.includes("database.db")) {
    throw new Error("Yedekte koordinatör veya cihaz veritabanı eksik.");
  }
  if (names.some((name) => !allowedFiles.includes(name as (typeof allowedFiles)[number]))) {
    throw new Error("Yedek beklenmeyen bir dosya içeriyor.");
  }
  const decoded = new Map<string, Buffer>();
  let totalBytes = 0;
  for (const name of names) {
    const buffer = decodeFile(name, candidate.files[name as keyof typeof candidate.files]);
    totalBytes += buffer.length;
    if (totalBytes > 24 * 1024 * 1024) throw new Error("Zigbee ağ yedeği çok büyük.");
    decoded.set(name, buffer);
  }
  return { backup: candidate as ZigbeeNetworkBackup, decoded };
};

export const createZigbeeNetworkBackup = async (
  dataDir: string
): Promise<ZigbeeNetworkBackup> => {
  const files: ZigbeeNetworkBackup["files"] = {};
  for (const name of allowedFiles) {
    try {
      const value = await readFile(join(dataDir, name));
      if (value.length === 0 || value.length > maximumFileBytes) continue;
      files[name] = { data: value.toString("base64"), sha256: digest(value) };
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
  }
  const backup: ZigbeeNetworkBackup = {
    format,
    version,
    createdAt: new Date().toISOString(),
    files
  };
  validateZigbeeNetworkBackup(backup);
  return backup;
};

const pendingName = "pending-zigbee-restore.json";

export const stageZigbeeNetworkRestore = async (
  dataDir: string,
  value: unknown
): Promise<void> => {
  const { backup } = validateZigbeeNetworkBackup(value);
  await mkdir(dataDir, { recursive: true, mode: 0o700 });
  const temporary = atomicTemporaryPath(join(dataDir, pendingName));
  await writeFile(temporary, `${JSON.stringify(backup)}\n`, { mode: 0o600 });
  await rename(temporary, join(dataDir, pendingName));
};

/**
 * Sıraya alınmış bir geri yükleme bekliyorsa `true`. Koordinatöre dokunan arka plan işleri
 * (otomatik onarım gibi) bu sırada çalışmamalı: veritabanı birazdan değişecek.
 */
export const hasPendingZigbeeNetworkRestore = async (dataDir: string): Promise<boolean> => {
  try {
    return (await stat(join(dataDir, pendingName))).isFile();
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return false;
    throw error;
  }
};

export const applyPendingZigbeeNetworkRestore = async (
  dataDir: string
): Promise<boolean> => {
  const pending = join(dataDir, pendingName);
  let value: unknown;
  try {
    value = JSON.parse(await readFile(pending, "utf8"));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return false;
    throw error;
  }
  const { decoded } = validateZigbeeNetworkBackup(value);
  const rollbackDir = join(dataDir, "before-last-restore");
  await rm(rollbackDir, { recursive: true, force: true });
  await mkdir(rollbackDir, { recursive: true, mode: 0o700 });
  for (const name of allowedFiles) {
    const target = join(dataDir, name);
    try {
      const info = await stat(target);
      if (info.isFile()) await copyFile(target, join(rollbackDir, name));
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
  }
  for (const [name, buffer] of decoded) {
    const temporary = atomicTemporaryPath(join(dataDir, name));
    await writeFile(temporary, buffer, { mode: 0o600 });
    await rename(temporary, join(dataDir, name));
  }
  await rm(pending, { force: true });
  return true;
};
