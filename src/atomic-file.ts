import { rename, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

/**
 * Atomik dosya yazımı: içerik önce benzersiz bir geçici dosyaya yazılır, sonra `rename` ile
 * hedefin yerine geçer — yarım yazılmış dosya asla görünmez.
 *
 * İki ayrı garanti gerekir, ikisi de burada:
 * 1. Geçici ad her yazma için benzersizdir. Aynı süreçte iki eşzamanlı yazma aynı geçici yolu
 *    kullanırsa ilki `rename` ile dosyayı alır, ikincisi kaynağı bulamaz ve `ENOENT` atar.
 * 2. Aynı hedefe yazmalar sıraya alınır. Sıradaki yazmalar tek işe indirgenir: son çağıran kazanır,
 *    aradaki yazmalar atlanabilir — ama atlanan çağrı da gerçekten diske inen yazmanın sonucunu alır.
 */
export interface AtomicWriteOptions {
  /** Hedef dosyanın izinleri (örn. 0o600). */
  mode?: number;
}

interface Waiter {
  resolve(): void;
  reject(error: unknown): void;
}

interface PendingWrite {
  data: string | Buffer;
  options: AtomicWriteOptions | undefined;
  waiters: Waiter[];
}

/** Henüz diske inmemiş, bir sonraki turda yazılacak iş — hedef yol başına en fazla bir tane. */
const pending = new Map<string, PendingWrite>();
/** Hedef yol başına çalışan yazma zinciri. */
const chains = new Map<string, Promise<void>>();
let sequence = 0;

/**
 * Süreç ve çağrı başına benzersiz geçici yol. Kendi iki aşamalı (önce hepsini yaz, sonra hepsini
 * `rename`) işlemini yürüten yazıcılar için dışa açık — aynı geçici adı paylaşan iki yazma
 * birbirinin kaynağını götürür ve `ENOENT` üretir.
 */
export function atomicTemporaryPath(path: string): string {
  sequence += 1;
  return `${path}.${process.pid}-${sequence}.tmp`;
}

async function flush(key: string): Promise<void> {
  const job = pending.get(key);
  if (!job) return;
  pending.delete(key);
  const temporary = atomicTemporaryPath(key);
  try {
    await writeFile(temporary, job.data, job.options?.mode === undefined ? {} : { mode: job.options.mode });
    await rename(temporary, key);
    for (const waiter of job.waiters) waiter.resolve();
  } catch (error) {
    for (const waiter of job.waiters) waiter.reject(error);
  }
}

export function writeFileAtomic(
  path: string,
  data: string | Buffer,
  options?: AtomicWriteOptions
): Promise<void> {
  const key = resolve(path);
  const queued = pending.get(key);
  if (queued) {
    // Sıradaki iş henüz başlamadı: içeriği son çağıranınkiyle değiştir, aynı sonucu paylaş.
    queued.data = data;
    queued.options = options;
    return new Promise<void>((res, rej) => {
      queued.waiters.push({ resolve: res, reject: rej });
    });
  }
  const job: PendingWrite = { data, options, waiters: [] };
  pending.set(key, job);
  const result = new Promise<void>((res, rej) => {
    job.waiters.push({ resolve: res, reject: rej });
  });
  const previous = chains.get(key);
  const chain = (previous ?? Promise.resolve()).then(() => flush(key), () => flush(key));
  chains.set(key, chain);
  void chain.then(() => {
    if (chains.get(key) === chain && !pending.has(key)) chains.delete(key);
  });
  return result;
}

/** JSON durum dosyaları için ortak biçim: iki boşluk girinti, sonda yeni satır. */
export function writeJsonAtomic(
  path: string,
  value: unknown,
  options?: AtomicWriteOptions
): Promise<void> {
  return writeFileAtomic(path, `${JSON.stringify(value, null, 2)}\n`, options);
}
