import { mkdir, readFile, readdir, unlink } from "node:fs/promises";
import { join } from "node:path";
import { writeFileAtomic } from "./atomic-file.js";

/**
 * Ajan yazmalarının yedeği. Kullanıcı canlı otomasyon kurallarına bir modelin yazmasına izin
 * verdi; karşılığında istediği ilk şey buydu: **yazmadan önce bir kenara kopya**.
 *
 * Yedek yalnız **ajan yolunda** alınır (`src/index.ts` wiring'i), panelden yapılan kaydetmelerde
 * alınmaz. Böylece "en yeni yedek" her zaman *son ajan değişikliğinden hemen önceki* hâldir ve
 * panelin "ajan değişikliklerini geri al" düğmesinin anlamı tek cümleye sığar.
 *
 * Yedek okunduğunda **tüketilir** (dosya silinir): düğmeye üst üste basmak geri/ileri arasında
 * gidip gelmez, geçmişte bir adım daha geriye gider. Aynı gerekçeyle sınırsız büyümez —
 * `maxAutomationBackups` kadarı tutulur, eskisi düşer.
 */
export const maxAutomationBackups = 20;

export interface AutomationBackupSummary {
  /** Yedek dosyasının adı — yol değil; dizin deponun kendi bilgisidir. */
  file: string;
  /** Yedeğin alındığı an (ISO). */
  at: string;
}

/**
 * `automations-<ISO>-<sıra>.json`. ISO damgasındaki `:` ve `.` dosya adında taşınamadığı için
 * `-` ile değiştirilir; sıra numarası aynı milisaniyede alınan iki yedeği ayırır ve sıralamayı
 * belirlenimci tutar.
 */
const backupPattern = /^automations-(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})-(\d{3})Z-(\d{3})\.json$/;

interface ParsedBackup extends AutomationBackupSummary {
  sequence: number;
}

const parseBackupName = (file: string): ParsedBackup | null => {
  const match = backupPattern.exec(file);
  if (!match) return null;
  const [, year, month, day, hour, minute, second, millisecond, sequence] = match;
  return {
    file,
    at: `${year}-${month}-${day}T${hour}:${minute}:${second}.${millisecond}Z`,
    sequence: Number(sequence)
  };
};

/** En yeniden eskiye; aynı milisaniyede alınanlar sıra numarasına göre. */
const newestFirst = (left: ParsedBackup, right: ParsedBackup): number =>
  left.at === right.at ? right.sequence - left.sequence : (left.at < right.at ? 1 : -1);

export interface AutomationBackupOptions {
  now?: () => Date;
  /** Tutulacak yedek sayısı; varsayılan `maxAutomationBackups`. */
  keep?: number;
}

export class AutomationBackupStore {
  private readonly now: () => Date;
  private readonly keep: number;
  private sequence = 0;

  constructor(
    /** Yedeklenecek dosya — `automations.json`. */
    private readonly sourcePath: string,
    /** Yedeklerin durduğu dizin; yapılandırma dosyasının yanında. */
    private readonly directory: string,
    options: AutomationBackupOptions = {}
  ) {
    this.now = options.now ?? (() => new Date());
    this.keep = options.keep ?? maxAutomationBackups;
  }

  /**
   * Yazmadan **önce** çağrılır. Dosya henüz yoksa (ilk kural ajan tarafından yazılıyorsa) yedek
   * de yoktur ve bu bir hata değildir: `null` döner.
   */
  async capture(): Promise<string | null> {
    let data: string;
    try {
      data = await readFile(this.sourcePath, "utf8");
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
      throw error;
    }
    await mkdir(this.directory, { recursive: true });
    this.sequence = (this.sequence + 1) % 1000;
    const stamp = this.now().toISOString().replaceAll(":", "-").replace(".", "-");
    const file = `automations-${stamp}-${String(this.sequence).padStart(3, "0")}.json`;
    await writeFileAtomic(join(this.directory, file), data, { mode: 0o600 });
    await this.prune();
    return file;
  }

  /** Yedekler, en yeniden eskiye. Dizin yoksa boş liste. */
  async list(): Promise<AutomationBackupSummary[]> {
    let files: string[];
    try {
      files = await readdir(this.directory);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
      throw error;
    }
    return files
      .map(parseBackupName)
      .filter((entry): entry is ParsedBackup => entry !== null)
      .sort(newestFirst)
      .map(({ file, at }) => ({ file, at }));
  }

  /**
   * En yeni yedeği okur, ayrıştırır ve dosyayı **siler**. İçerik doğrulanmadan döner: doğrulama
   * `AutomationsStore.save()` işidir, iki yerde tekrarlanmaz. Yedek yoksa `null`.
   */
  async takeLatest(): Promise<{ automations: unknown; at: string } | null> {
    const [latest] = await this.list();
    if (!latest) return null;
    const path = join(this.directory, latest.file);
    const automations = JSON.parse(await readFile(path, "utf8")) as unknown;
    await unlink(path).catch(() => undefined);
    return { automations, at: latest.at };
  }

  private async prune(): Promise<void> {
    const stale = (await this.list()).slice(this.keep);
    for (const entry of stale) {
      await unlink(join(this.directory, entry.file)).catch(() => undefined);
    }
  }
}
