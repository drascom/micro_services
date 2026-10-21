"use strict";

// Faz 6 — veri replikasyonu (docs/tablet-failover-plani.md §6): sahiplikle tasinan snapshot.
// Yazma hakki yalniz sahipte; bekleyen dugum sahipten replika ceker (`replica/`), devirde
// snapshot devredilir. Karar mantigi failover-policy.cjs'de (saf); burada yalniz dosya isi.

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const YAML = require("yaml");
const {
  SNAPSHOT_DIRECTORIES,
  SNAPSHOT_FILES,
  SNAPSHOT_FORMAT,
  isInSnapshotScope,
  networkIdentity,
  normalizeSnapshotPath
} = require("./failover-policy.cjs");

const REPLICA_DIR = "replica";
const BACKUP_DIR = "failover-backups";
const BACKUPS_KEPT = 3;
const MAX_FILE_BYTES = 16 * 1024 * 1024;
const MAX_SNAPSHOT_BYTES = 48 * 1024 * 1024;

function sha256(buffer) {
  return crypto.createHash("sha256").update(buffer).digest("hex");
}

/** Kapsamdaki mevcut dosyalar (goreli yol, sirali). */
function listScopeFiles(dataDir) {
  const found = [];
  for (const relative of SNAPSHOT_FILES) {
    try {
      if (fs.statSync(path.join(dataDir, relative)).isFile()) found.push(relative);
    } catch {
      // Dosya yoksa kapsamda degil; eksik dosya hata degildir.
    }
  }
  for (const directory of SNAPSHOT_DIRECTORIES) {
    let entries = [];
    try {
      entries = fs.readdirSync(path.join(dataDir, directory.prefix), { withFileTypes: true });
    } catch {
      entries = [];
    }
    for (const entry of entries) {
      const relative = `${directory.prefix}${entry.name}`;
      if (entry.isFile() && isInSnapshotScope(relative)) found.push(relative);
    }
  }
  return found.sort();
}

/**
 * Tutarli okuma: dosya yazilirken okunursa (herdsman database.db'yi periyodik yaziyor) iki
 * okuma ayni olana kadar tekrarlanir. Atomik `rename` deseninde ilk deneme zaten tutarlidir.
 */
function readStable(file) {
  let previous = fs.readFileSync(file);
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const current = fs.readFileSync(file);
    if (current.equals(previous)) return current;
    previous = current;
  }
  return previous;
}

const manifestCache = new Map();

/** Kapsamin ozeti: her dosyanin sha256'si + tek bir ozet. mtime/boyut degismediyse yeniden hashlenmez. */
function scopeManifest(dataDir) {
  const files = [];
  for (const relative of listScopeFiles(dataDir)) {
    const full = path.join(dataDir, relative);
    const stat = fs.statSync(full);
    if (stat.size > MAX_FILE_BYTES) throw new Error(`Snapshot file is too large: ${relative}`);
    const key = `${full}:${stat.mtimeMs}:${stat.size}`;
    let hash = manifestCache.get(key);
    if (!hash) {
      hash = sha256(readStable(full));
      manifestCache.set(key, hash);
    }
    files.push({ path: relative, sha256: hash, size: stat.size });
  }
  if (manifestCache.size > 512) manifestCache.clear();
  return { files, digest: manifestDigest(files) };
}

function manifestDigest(files) {
  return sha256(Buffer.from(files.map((file) => `${file.path}:${file.sha256}`).join("\n")));
}

/** Zigbee ag kimligi (configuration.yaml); okunamazsa her alan `null`. */
function readNetworkIdentity(z2mConfigPath) {
  try {
    return networkIdentity(YAML.parse(fs.readFileSync(z2mConfigPath, "utf8")));
  } catch {
    return networkIdentity(null);
  }
}

/**
 * Devir anahtari: Zigbee ag anahtarindan turetilir. Ag anahtari iki dugumde zaten birebir ayni
 * olmak ZORUNDA (§5.1), yani yeni bir sir dagitmaya gerek yok; anahtari bilmeyen biri ne
 * snapshot cekebilir ne release isteyebilir. Ag anahtari yoksa failover HTTP'si kapali kalir.
 */
function failoverToken(z2mConfigPath) {
  const identity = readNetworkIdentity(z2mConfigPath);
  if (!identity.network_key) return null;
  return crypto
    .createHmac("sha256", Buffer.from(identity.network_key))
    .update("villa-bridge-failover-v1")
    .digest("hex");
}

/** Ag kimliginin karsiya giden hali: ag anahtari ham degil, ozet olarak tasinir. */
function publicNetworkIdentity(identity) {
  return {
    ...identity,
    network_key: identity.network_key
      ? [crypto.createHash("sha256").update(Buffer.from(identity.network_key)).digest("hex").slice(0, 32)]
      : null
  };
}

/** Sahibin veri dizininden snapshot (dosya icerikleri base64). */
function buildSnapshot(dataDir, meta) {
  const files = [];
  let total = 0;
  for (const relative of listScopeFiles(dataDir)) {
    const data = readStable(path.join(dataDir, relative));
    if (data.length > MAX_FILE_BYTES) throw new Error(`Snapshot file is too large: ${relative}`);
    total += data.length;
    if (total > MAX_SNAPSHOT_BYTES) throw new Error("Snapshot is too large.");
    files.push({ path: relative, sha256: sha256(data), size: data.length, data: data.toString("base64") });
  }
  return {
    format: SNAPSHOT_FORMAT,
    epoch: meta.epoch,
    ownerNodeId: meta.nodeId,
    createdAt: Number.isFinite(meta.createdAt) ? meta.createdAt : Date.now(),
    network: meta.network,
    digest: manifestDigest(files),
    files
  };
}

/** Gelen snapshot'in dosyalarini dogrular; sorun varsa hata firlatir. */
function verifySnapshotFiles(snapshot) {
  if (!snapshot || !Array.isArray(snapshot.files)) throw new Error("Snapshot has no file list.");
  const files = [];
  let total = 0;
  for (const file of snapshot.files) {
    const relative = normalizeSnapshotPath(file?.path);
    if (relative === null || !isInSnapshotScope(relative)) {
      throw new Error(`Snapshot file is outside the replication scope: ${String(file?.path)}`);
    }
    const data = Buffer.from(String(file.data || ""), "base64");
    if (data.length > MAX_FILE_BYTES) throw new Error(`Snapshot file is too large: ${relative}`);
    total += data.length;
    if (total > MAX_SNAPSHOT_BYTES) throw new Error("Snapshot is too large.");
    if (sha256(data) !== file.sha256) throw new Error(`Snapshot file is corrupt: ${relative}`);
    files.push({ path: relative, sha256: file.sha256, size: data.length, data });
  }
  if (manifestDigest(files) !== snapshot.digest) throw new Error("Snapshot digest does not match its files.");
  return files;
}

function writeFileAtomic(file, data) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  const temporary = `${file}.failover-${process.pid}-${Date.now()}`;
  fs.writeFileSync(temporary, data, { mode: 0o600 });
  fs.renameSync(temporary, file);
}

function snapshotMeta(snapshot, extra = {}) {
  return {
    format: snapshot.format,
    epoch: snapshot.epoch,
    ownerNodeId: snapshot.ownerNodeId,
    createdAt: snapshot.createdAt,
    digest: snapshot.digest,
    network: snapshot.network,
    files: snapshot.files.map((file) => ({ path: file.path, sha256: file.sha256, size: file.size })),
    ...extra
  };
}

/**
 * Replikayi `replica/` altina yazar. Once yeni dizin tamamen yazilir, sonra eskisiyle yer
 * degistirir: yarim kalan bir cekme eski replikayi bozmaz.
 */
function writeReplica(dataDir, snapshot, receivedAt = Date.now()) {
  const files = verifySnapshotFiles(snapshot);
  const target = path.join(dataDir, REPLICA_DIR);
  const staging = `${target}.staging-${process.pid}`;
  const previous = `${target}.previous-${process.pid}`;
  fs.rmSync(staging, { recursive: true, force: true });
  fs.mkdirSync(path.join(staging, "files"), { recursive: true, mode: 0o700 });
  for (const file of files) writeFileAtomic(path.join(staging, "files", file.path), file.data);
  writeFileAtomic(path.join(staging, "meta.json"), JSON.stringify(snapshotMeta(snapshot, { receivedAt }), null, 2));
  fs.rmSync(previous, { recursive: true, force: true });
  if (fs.existsSync(target)) fs.renameSync(target, previous);
  fs.renameSync(staging, target);
  fs.rmSync(previous, { recursive: true, force: true });
  return readReplicaMeta(dataDir);
}

function readReplicaMeta(dataDir) {
  try {
    const meta = JSON.parse(fs.readFileSync(path.join(dataDir, REPLICA_DIR, "meta.json"), "utf8"));
    return meta && Number.isInteger(meta.epoch) ? meta : null;
  } catch {
    return null;
  }
}

/** Replikayi snapshot bicimine geri okur (canliya uygulamak icin). */
function readReplica(dataDir) {
  const meta = readReplicaMeta(dataDir);
  if (!meta) return null;
  const files = meta.files.map((file) => {
    const data = fs.readFileSync(path.join(dataDir, REPLICA_DIR, "files", file.path));
    return { path: file.path, sha256: file.sha256, size: data.length, data: data.toString("base64") };
  });
  return { ...meta, files };
}

/**
 * Snapshot'i canli dizine yazar. Ustune yazilacak dosyalar once `failover-backups/<an>/`a
 * kopyalanir (son uc yedek tutulur). Snapshot'ta olmayan dosyalar SILINMEZ.
 * Bu cagri yalniz cekirdek calismiyorken yapilir (devralma oncesi / devir sirasinda).
 */
function applySnapshotToLive(dataDir, snapshot, at = Date.now()) {
  const files = verifySnapshotFiles(snapshot);
  const backupRoot = path.join(dataDir, BACKUP_DIR);
  const backup = path.join(backupRoot, `${new Date(at).toISOString().replace(/[:.]/g, "-")}-epoch-${snapshot.epoch}`);
  for (const file of files) {
    const live = path.join(dataDir, file.path);
    if (fs.existsSync(live)) {
      const copy = path.join(backup, file.path);
      fs.mkdirSync(path.dirname(copy), { recursive: true, mode: 0o700 });
      fs.copyFileSync(live, copy);
    }
  }
  for (const file of files) writeFileAtomic(path.join(dataDir, file.path), file.data);
  pruneBackups(backupRoot);
  return { files: files.length, backup: fs.existsSync(backup) ? path.relative(dataDir, backup) : null };
}

function pruneBackups(backupRoot) {
  let entries = [];
  try {
    entries = fs.readdirSync(backupRoot).sort();
  } catch {
    return;
  }
  for (const entry of entries.slice(0, Math.max(0, entries.length - BACKUPS_KEPT))) {
    fs.rmSync(path.join(backupRoot, entry), { recursive: true, force: true });
  }
}

module.exports = {
  BACKUP_DIR,
  REPLICA_DIR,
  applySnapshotToLive,
  buildSnapshot,
  failoverToken,
  listScopeFiles,
  publicNetworkIdentity,
  readNetworkIdentity,
  readReplica,
  readReplicaMeta,
  scopeManifest,
  verifySnapshotFiles,
  writeReplica
};
