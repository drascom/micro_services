import { readFile } from "node:fs/promises";
import { writeJsonAtomic } from "./atomic-file.js";

/**
 * Otomasyonların **çalışma durumu** — kuralın tanımından ayrı dosya.
 *
 * Neden ayrı: `automations.json` kullanıcının yazdığı kuralların tanımıdır; her çalışmadan sonra
 * yeniden yazılırsa (a) yazma anındaki bir kesinti tanımları tehlikeye atar, (b) yedek/sürüm
 * karşılaştırması hiç değişmemiş kuralları değişmiş gösterir. Bu yüzden tanım dosyası **yalnız
 * kullanıcı düzenlemesinde** yazılır, son çalışma bilgisi buraya düşer.
 *
 * Neden `automation-runs.jsonl`'den türetilmiyor: o günlük kayan bir penceredir (son 500 kayıt,
 * tavan aşılınca sıkıştırılır). Seyrek çalışan bir kuralın son çalışması pencereden düşer ve
 * panelde "son çalışma" bilgisi kaybolurdu. Bu dosya kural başına tek satır tutar, taşmaz.
 */
export interface AutomationRunState {
  lastRunAt: string | null;
  lastRunOk: boolean | null;
}

const emptyState: AutomationRunState = { lastRunAt: null, lastRunOk: null };

export interface AutomationRunStateOptions {
  /**
   * Eski `automations.json` yolu. Durum dosyası yoksa bu dosyadaki `lastRunAt`/`lastRunOk`
   * alanları bir kez taşınır — kullanıcının elle bir şey yapması gerekmez.
   */
  legacyAutomationsPath?: string;
  /** Kalıcılık hatası sessiz kalmasın. */
  onError?: (message: string) => void;
}

interface StoredFile {
  version: 1;
  automations: Record<string, AutomationRunState>;
}

const readState = (value: unknown): Map<string, AutomationRunState> => {
  const state = new Map<string, AutomationRunState>();
  if (typeof value !== "object" || value === null || Array.isArray(value)) return state;
  const entries = (value as Record<string, unknown>).automations;
  if (typeof entries !== "object" || entries === null || Array.isArray(entries)) return state;
  for (const [id, raw] of Object.entries(entries as Record<string, unknown>)) {
    const entry = readEntry(raw);
    if (entry) state.set(id.trim().toLowerCase(), entry);
  }
  return state;
};

/** Bozuk satır kaydı düşürür, dosyayı okunamaz yapmaz — tarihçe kuralın çalışmasını engellemez. */
const readEntry = (raw: unknown): AutomationRunState | null => {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const candidate = raw as Record<string, unknown>;
  const at = candidate.lastRunAt;
  const ok = candidate.lastRunOk;
  const lastRunAt = typeof at === "string" && !Number.isNaN(Date.parse(at)) ? at : null;
  const lastRunOk = typeof ok === "boolean" ? ok : null;
  if (lastRunAt === null && lastRunOk === null) return null;
  return { lastRunAt, lastRunOk };
};

export class AutomationRunStateStore {
  private state = new Map<string, AutomationRunState>();
  private loading: Promise<void> | null = null;
  private readonly onError: (message: string) => void;

  constructor(
    private readonly path: string,
    private readonly options: AutomationRunStateOptions = {}
  ) {
    this.onError = options.onError ?? ((message) => console.error(message));
  }

  /** Bir kez okur; sonraki çağrılar aynı sözü döndürür (olay başına disk okuması olmaz). */
  async load(): Promise<void> {
    if (!this.loading) this.loading = this.read();
    await this.loading;
  }

  private async read(): Promise<void> {
    try {
      this.state = readState(JSON.parse(await readFile(this.path, "utf8")));
      return;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
        this.onError(`Otomasyon çalışma durumu okunamadı: ${String(error)}`);
      }
    }
    this.state = await this.migrate();
    // Göç yalnız bir kez: taşınan bilgi kendi dosyasına yazılır, tanım dosyasına dokunulmaz.
    if (this.state.size > 0) await this.persist();
  }

  /** Eski tanım dosyasındaki çalışma alanlarını taşır; okunamıyorsa boş başlanır. */
  private async migrate(): Promise<Map<string, AutomationRunState>> {
    const legacyPath = this.options.legacyAutomationsPath;
    const state = new Map<string, AutomationRunState>();
    if (!legacyPath) return state;
    let parsed: unknown;
    try {
      parsed = JSON.parse(await readFile(legacyPath, "utf8"));
    } catch {
      return state;
    }
    if (!Array.isArray(parsed)) return state;
    for (const raw of parsed) {
      if (typeof raw !== "object" || raw === null || Array.isArray(raw)) continue;
      const id = (raw as Record<string, unknown>).id;
      if (typeof id !== "string") continue;
      const entry = readEntry(raw);
      if (entry) state.set(id.trim().toLowerCase(), entry);
    }
    return state;
  }

  /** Yükleme sonrası eşzamanlı okuma; kural listesini süslemek için kullanılır. */
  snapshot(id: string): AutomationRunState {
    return this.state.get(id.trim().toLowerCase()) ?? emptyState;
  }

  async markRun(id: string, ok: boolean, at: Date = new Date()): Promise<void> {
    await this.load();
    this.state.set(id.trim().toLowerCase(), { lastRunAt: at.toISOString(), lastRunOk: ok });
    await this.persist();
  }

  /** Silinen kuralların tarihçesi birikmesin; kural kaydında çağrılır. */
  async prune(ids: Iterable<string>): Promise<void> {
    await this.load();
    const kept = new Set<string>();
    for (const id of ids) kept.add(id.trim().toLowerCase());
    let dropped = false;
    for (const id of [...this.state.keys()]) {
      if (kept.has(id)) continue;
      this.state.delete(id);
      dropped = true;
    }
    if (dropped) await this.persist();
  }

  private async persist(): Promise<void> {
    const file: StoredFile = { version: 1, automations: Object.fromEntries(this.state) };
    await writeJsonAtomic(this.path, file, { mode: 0o600 });
  }
}
