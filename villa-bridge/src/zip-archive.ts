import { deflateRawSync, inflateRawSync } from "node:zlib";

/**
 * Bağımlılıksız, en küçük ZIP yazıcı/okuyucu.
 *
 * Neden elle: yedek dosyasının tek bir kapsayıcıda gitmesi isteniyor ama projeye yeni bir npm
 * paketi girmiyor. Node'un yerleşik `zlib`i deflate'i zaten veriyor; eksik olan yalnız ZIP
 * çerçevesi (yerel başlık + merkezi dizin + EOCD) ve o da birkaç yüz satır tutmuyor.
 *
 * Kapsam bilerek dar: ZIP64 yok, şifreleme yok, veri tanımlayıcı (data descriptor) yok, dizin
 * girdisi yok. Yedek birkaç MB'lık bir avuç dosyadır; bu sınırların hiçbirine yaklaşmaz. Sınır
 * aşılırsa yazıcı hata verir, sessizce bozuk arşiv üretmez.
 */

const localSignature = 0x04034b50;
const centralSignature = 0x02014b50;
const endSignature = 0x06054b50;
const maximumEntries = 64;
const maximumEntryBytes = 64 * 1024 * 1024;

const crcTable = (() => {
  const table = new Int32Array(256);
  for (let index = 0; index < 256; index += 1) {
    let value = index;
    for (let bit = 0; bit < 8; bit += 1) {
      value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
    }
    table[index] = value;
  }
  return table;
})();

const crc32 = (data: Buffer): number => {
  let crc = -1;
  for (let index = 0; index < data.length; index += 1) {
    crc = (crc >>> 8) ^ (crcTable[(crc ^ (data[index] as number)) & 0xff] as number);
  }
  return (crc ^ -1) >>> 0;
};

/** MS-DOS tarih/saat alanı: 2 saniyelik çözünürlük, 1980 başlangıçlı. */
const dosStamp = (date: Date): { time: number; date: number } => {
  const year = Math.max(1980, date.getFullYear());
  return {
    time: (date.getHours() << 11) | (date.getMinutes() << 5) | (date.getSeconds() >> 1),
    date: ((year - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate()
  };
};

export interface ZipEntry {
  name: string;
  data: Buffer;
}

export const createZipArchive = (entries: ZipEntry[], now: Date = new Date()): Buffer => {
  if (entries.length === 0) throw new Error("Arşiv boş olamaz.");
  if (entries.length > maximumEntries) throw new Error("Arşiv çok fazla dosya içeriyor.");
  const stamp = dosStamp(now);
  const locals: Buffer[] = [];
  const centrals: Buffer[] = [];
  let offset = 0;

  for (const entry of entries) {
    const name = Buffer.from(entry.name, "utf8");
    if (name.length === 0 || name.length > 200) throw new Error("Arşiv dosya adı geçersiz.");
    if (entry.data.length > maximumEntryBytes) throw new Error("Arşiv dosyası çok büyük.");
    const deflated = deflateRawSync(entry.data, { level: 9 });
    // Sıkıştırma kazandırmadıysa (zaten sıkıştırılmış girdi) olduğu gibi sakla.
    const compressed = deflated.length < entry.data.length ? deflated : entry.data;
    const method = compressed === deflated ? 8 : 0;
    const crc = crc32(entry.data);

    const local = Buffer.alloc(30);
    local.writeUInt32LE(localSignature, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0, 6);
    local.writeUInt16LE(method, 8);
    local.writeUInt16LE(stamp.time, 10);
    local.writeUInt16LE(stamp.date, 12);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(compressed.length, 18);
    local.writeUInt32LE(entry.data.length, 22);
    local.writeUInt16LE(name.length, 26);
    local.writeUInt16LE(0, 28);
    locals.push(local, name, compressed);

    const central = Buffer.alloc(46);
    central.writeUInt32LE(centralSignature, 0);
    central.writeUInt16LE(20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt16LE(0, 8);
    central.writeUInt16LE(method, 10);
    central.writeUInt16LE(stamp.time, 12);
    central.writeUInt16LE(stamp.date, 14);
    central.writeUInt32LE(crc, 16);
    central.writeUInt32LE(compressed.length, 20);
    central.writeUInt32LE(entry.data.length, 24);
    central.writeUInt16LE(name.length, 28);
    central.writeUInt16LE(0, 30);
    central.writeUInt16LE(0, 32);
    central.writeUInt16LE(0, 34);
    central.writeUInt16LE(0, 36);
    // Unix hakları (0600) yüksek 16 bitte durur; `<<16` işaretli taşar, `>>>0` ile düzeltilir.
    central.writeUInt32LE((0o100600 << 16) >>> 0, 38);
    central.writeUInt32LE(offset, 42);
    centrals.push(central, name);

    offset += local.length + name.length + compressed.length;
  }

  const directory = Buffer.concat(centrals);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(endSignature, 0);
  end.writeUInt16LE(0, 4);
  end.writeUInt16LE(0, 6);
  end.writeUInt16LE(entries.length, 8);
  end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(directory.length, 12);
  end.writeUInt32LE(offset, 16);
  end.writeUInt16LE(0, 20);
  return Buffer.concat([...locals, directory, end]);
};

export const isZipArchive = (data: Buffer): boolean =>
  data.length >= 4 && data.readUInt32LE(0) === localSignature;

/** Arşivi tamamen çözer. Bozuk boyut/CRC durumunda hata atar; yarım sonuç dönmez. */
export const readZipArchive = (data: Buffer): Map<string, Buffer> => {
  if (data.length < 22) throw new Error("Arşiv okunamadı.");
  let endOffset = -1;
  for (let index = data.length - 22; index >= 0 && index >= data.length - 22 - 0xffff; index -= 1) {
    if (data.readUInt32LE(index) === endSignature) {
      endOffset = index;
      break;
    }
  }
  if (endOffset < 0) throw new Error("Arşiv dizini bulunamadı.");
  const total = data.readUInt16LE(endOffset + 10);
  if (total === 0 || total > maximumEntries) throw new Error("Arşiv dosya sayısı geçersiz.");
  const directorySize = data.readUInt32LE(endOffset + 12);
  let cursor = data.readUInt32LE(endOffset + 16);
  if (cursor + directorySize > data.length) throw new Error("Arşiv dizini bozuk.");

  const files = new Map<string, Buffer>();
  for (let index = 0; index < total; index += 1) {
    if (cursor + 46 > data.length || data.readUInt32LE(cursor) !== centralSignature) {
      throw new Error("Arşiv dizini bozuk.");
    }
    const method = data.readUInt16LE(cursor + 10);
    const crc = data.readUInt32LE(cursor + 16);
    const compressedSize = data.readUInt32LE(cursor + 20);
    const uncompressedSize = data.readUInt32LE(cursor + 24);
    const nameLength = data.readUInt16LE(cursor + 28);
    const extraLength = data.readUInt16LE(cursor + 30);
    const commentLength = data.readUInt16LE(cursor + 32);
    const localOffset = data.readUInt32LE(cursor + 42);
    const name = data.subarray(cursor + 46, cursor + 46 + nameLength).toString("utf8");
    cursor += 46 + nameLength + extraLength + commentLength;

    if (uncompressedSize > maximumEntryBytes) throw new Error("Arşiv dosyası çok büyük.");
    if (name.includes("..") || name.startsWith("/") || name.endsWith("/")) {
      throw new Error("Arşiv dosya adı geçersiz.");
    }
    if (localOffset + 30 > data.length || data.readUInt32LE(localOffset) !== localSignature) {
      throw new Error("Arşiv girdisi bozuk.");
    }
    const localNameLength = data.readUInt16LE(localOffset + 26);
    const localExtraLength = data.readUInt16LE(localOffset + 28);
    const start = localOffset + 30 + localNameLength + localExtraLength;
    if (start + compressedSize > data.length) throw new Error("Arşiv girdisi bozuk.");
    const stored = data.subarray(start, start + compressedSize);
    let content: Buffer;
    if (method === 0) content = Buffer.from(stored);
    else if (method === 8) content = inflateRawSync(stored, { maxOutputLength: maximumEntryBytes });
    else throw new Error("Arşiv sıkıştırma biçimi desteklenmiyor.");
    if (content.length !== uncompressedSize || crc32(content) !== crc) {
      throw new Error("Arşiv bütünlük kontrolü başarısız.");
    }
    files.set(name, content);
  }
  return files;
};
