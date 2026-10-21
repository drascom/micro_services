import { mkdir, readFile } from "node:fs/promises";
import { dirname } from "node:path";
import { writeJsonAtomic } from "./atomic-file.js";

/**
 * Faz 1 — cihaz kendini ilan edince (`deviceAnnounce`) raporlama ayarlarını yeniden kurar.
 *
 * Tetikleyici bilinçli olarak yalnız ilandır: ilan, cihazın o an erişilebilir olduğunun
 * kanıtıdır. Erişilemeyen cihazı yoklamak (ve `interview(true)` çağırmak) koordinatörü
 * dakikalarca meşgul edip hiçbir şey onarmadığı için bu katman **asla** görüşme başlatmaz;
 * yalnız dönüştürücünün `configure` adımını çalıştırır.
 *
 * Faz 2 — ilan hiç gelmeyen çevrimdışı cihaz için **ucuz yoklama**. Canlıda görülen vaka
 * budur: cihaz düşer, ilan etmez, elle "Onar" denince anında yanıt verir. Demek ki cihaz
 * erişilebilirdir, yalnız "erişilemez" işareti asılı kalmıştır. Yoklama tek hafif ZCL
 * okumasıdır; yanıt gelirse cihaz çevrimiçine çekilir, gelmezse katlanan geri çekilmeye
 * girilir. Yoklama yalnız şebeke beslemeli yönlendiricilere yapılır — pilli cihazda uzun
 * sessizlik normaldir, yoklamak boşuna pil harcar.
 */

/** Başarılı yapılandırmadan sonra bu süre içindeki ilanlar yok sayılır (ilan fırtınası). */
export const selfHealSkipWindowMs = 60_000;
/** Kuyruktaki iki iş arasında bırakılan en küçük aralık. */
export const selfHealQueueSpacingMs = 5_000;
/** Cihaz başına saatlik deneme tavanı. */
export const selfHealHourlyLimit = 3;
/** Tavan aşılınca uygulanan geri çekilme süresi. */
export const selfHealBackoffMs = 6 * 60 * 60 * 1_000;
/** Deneme sayacının penceresi. */
export const selfHealWindowMs = 60 * 60 * 1_000;

/** Yoklamanın tek okumasına tanınan süre; cevap gelmezse cihaz yanıtsız sayılır. */
export const selfHealProbeTimeoutMs = 5_000;
/** Yanıtsız yoklamalardan sonra beklenen süreler: 15 dk → 30 dk → 1 sa → 2 sa → 4 sa → 8 sa. */
export const selfHealProbeBackoffMs = [15, 30, 60, 120, 240, 480].map((minutes) => minutes * 60_000);
/** Bu kadar ardışık yanıtsızlıktan sonra cihaz "kalıcı ulaşılamaz" sayılır. */
export const selfHealProbeUnreachableAfter = 5;
/** Kalıcı ulaşılamaz cihaz günde bir yoklanır. */
export const selfHealProbeDailyMs = 24 * 60 * 60 * 1_000;

/**
 * Ardışık yanıtsızlık sayısına düşen bekleme. Merdiven tavanı 8 saattir; kalıcı ulaşılamaz
 * eşiği geçildiğinde günlük aralık merdivenin önüne geçer (aylardır ölü cihaz günde 5 saniye
 * harcasın, saatte dört kez değil).
 */
export const selfHealProbeDelayMs = (failures: number): number => {
  if (failures >= selfHealProbeUnreachableAfter) return selfHealProbeDailyMs;
  const index = Math.min(Math.max(failures, 1), selfHealProbeBackoffMs.length) - 1;
  return selfHealProbeBackoffMs[index] ?? selfHealProbeDailyMs;
};

export type SelfHealOutcome = "attempt" | "ok" | "failed";
export type SelfHealSkip = "recently_configured" | "backoff" | "hourly_limit";
export type SelfHealProbeSkip = SelfHealSkip | "probe_backoff";
/** Kuyruktaki işin türü: ilan sonrası yapılandırma mı, çevrimdışı yoklama mı. */
export type SelfHealJob = "announce" | "probe";

export interface SelfHealProbeResult {
  /** Cihaz yoklamaya yanıt verdi mi? */
  reachable: boolean;
  /** Erişilebilirlik gerçekten çevrimdışıdan çevrimiçine döndü mü? */
  recovered?: boolean;
  /** Yoklamadan sonra yapılandırma gerçekten çalıştı mı? */
  configured?: boolean;
  /** Hata günlüğüne düşecek Türkçe sebep. */
  message?: string;
}

export interface SelfHealDeviceState {
  /** Son başarılı yapılandırma (epoch ms). */
  lastConfiguredAt?: number;
  /** Son penceredeki deneme zamanları (epoch ms); sonuçtan bağımsız sayılır. */
  attempts: number[];
  /** Bu ana kadar yeni deneme yapılmaz (epoch ms). */
  backoffUntil?: number;
  /** Ardışık yanıtsız yoklama sayısı; yanıt gelince sıfırlanır. */
  probeFailures?: number;
  /** Bir sonraki yoklamanın en erken zamanı (epoch ms). */
  probeNextAt?: number;
  /** Cihazın kalıcı ulaşılamaz sayıldığı an (epoch ms); yalnız bir kez yazılır. */
  probeUnreachableAt?: number;
}

const isFiniteNumber = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value >= 0;

export const validateSelfHealState = (value: unknown): Map<string, SelfHealDeviceState> => {
  const result = new Map<string, SelfHealDeviceState>();
  if (typeof value !== "object" || value === null || Array.isArray(value)) return result;
  for (const [deviceId, entry] of Object.entries(value as Record<string, unknown>)) {
    if (!/^[a-z0-9x:_-]{1,64}$/i.test(deviceId)) continue;
    if (typeof entry !== "object" || entry === null || Array.isArray(entry)) continue;
    const candidate = entry as Partial<SelfHealDeviceState>;
    const attempts = Array.isArray(candidate.attempts)
      ? candidate.attempts.filter(isFiniteNumber).slice(-selfHealHourlyLimit * 4)
      : [];
    result.set(deviceId, {
      attempts,
      ...(isFiniteNumber(candidate.lastConfiguredAt) ? { lastConfiguredAt: candidate.lastConfiguredAt } : {}),
      ...(isFiniteNumber(candidate.backoffUntil) ? { backoffUntil: candidate.backoffUntil } : {}),
      ...(isFiniteNumber(candidate.probeFailures) ? { probeFailures: Math.floor(candidate.probeFailures) } : {}),
      ...(isFiniteNumber(candidate.probeNextAt) ? { probeNextAt: candidate.probeNextAt } : {}),
      ...(isFiniteNumber(candidate.probeUnreachableAt) ? { probeUnreachableAt: candidate.probeUnreachableAt } : {})
    });
  }
  return result;
};

/** Bu cihaz için şimdi deneme yapılmalı mı? Yalnız cihazın kendi geçmişine bakar. */
export const selfHealDecision = (
  state: SelfHealDeviceState | undefined,
  now: number
): "run" | SelfHealSkip => {
  if (!state) return "run";
  if (
    isFiniteNumber(state.lastConfiguredAt)
    && now - state.lastConfiguredAt < selfHealSkipWindowMs
  ) {
    return "recently_configured";
  }
  if (isFiniteNumber(state.backoffUntil) && now < state.backoffUntil) return "backoff";
  const recent = state.attempts.filter((at) => now - at < selfHealWindowMs);
  return recent.length >= selfHealHourlyLimit ? "hourly_limit" : "run";
};

/** Yoklama kararı: Faz 1'in bütün kısıtları geçerli, üstüne katlanan yoklama aralığı. */
export const selfHealProbeDecision = (
  state: SelfHealDeviceState | undefined,
  now: number
): "run" | SelfHealProbeSkip => {
  if (state && isFiniteNumber(state.probeNextAt) && now < state.probeNextAt) return "probe_backoff";
  return selfHealDecision(state, now);
};

export class SelfHealStateStore {
  private writeQueue: Promise<void> = Promise.resolve();

  constructor(private readonly path: string) {}

  async get(): Promise<Map<string, SelfHealDeviceState>> {
    try {
      return validateSelfHealState(JSON.parse(await readFile(this.path, "utf8")));
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return new Map();
      console.warn(`Otomatik onarım durumu okunamadı: ${String(error)}`);
      return new Map();
    }
  }

  save(state: Map<string, SelfHealDeviceState>): Promise<void> {
    const snapshot = Object.fromEntries(
      [...state.entries()].sort(([left], [right]) => left.localeCompare(right, "en"))
    );
    this.writeQueue = this.writeQueue.catch(() => undefined).then(async () => {
      await mkdir(dirname(this.path), { recursive: true, mode: 0o700 });
      await writeJsonAtomic(this.path, snapshot, { mode: 0o600 });
    });
    return this.writeQueue;
  }
}

export interface SelfHealSchedulerOptions {
  enabled: boolean;
  /** Çevrimdışı yoklama açık mı (`selfHealing.probeOffline`). */
  probeEnabled?: boolean;
  /**
   * Denemenin hiç yapılmaması gereken durumlarda Türkçe sebep döner (koordinatör hazır değil,
   * eşleştirme açık, OTA sürüyor, yedek geri yükleme bekliyor). Yoksa `null`.
   */
  blockedReason: (deviceId: string) => Promise<string | null> | string | null;
  /**
   * Yapılacak iş varsa onu çalıştıran işlevi döner, yoksa `null`. Hazırlık adımı yereldir:
   * koordinatöre hiç dokunmaz, bu yüzden iş yoksa deneme de sayılmaz.
   */
  prepare: (deviceId: string) => Promise<(() => Promise<void>) | null>;
  /**
   * Yoklanabilir cihaz için tek ucuz okumayı yapan işlevi döner, uygun değilse `null`
   * (pilli cihaz, uç cihaz, devre dışı cihaz, uç nokta yok). `null` dönerse deneme sayılmaz.
   */
  probe?: (deviceId: string) => Promise<(() => Promise<SelfHealProbeResult>) | null>;
  /** Olay akışına iz bırakır (`property: "self_heal"`). */
  onOutcome: (deviceId: string, outcome: SelfHealOutcome) => void;
  /** Başarısızlıkları hata günlüğüne yazar. */
  onFailure?: (deviceId: string, message: string) => void;
  initialState?: Map<string, SelfHealDeviceState>;
  persist?: (state: Map<string, SelfHealDeviceState>) => void;
  now?: () => number;
  spacingMs?: number;
  wait?: (ms: number) => Promise<void>;
}

const defaultWait = (ms: number): Promise<void> => new Promise((resolve) => {
  const timer = setTimeout(resolve, ms);
  timer.unref?.();
});

interface SelfHealQueueItem {
  deviceId: string;
  job: SelfHealJob;
}

export class SelfHealScheduler {
  private readonly states: Map<string, SelfHealDeviceState>;
  private readonly queue: SelfHealQueueItem[] = [];
  private enabled: boolean;
  private probeEnabled: boolean;
  private active: string | null = null;
  private running: Promise<void> | null = null;
  private lastRunAt = 0;
  private readonly now: () => number;
  private readonly spacingMs: number;
  private readonly wait: (ms: number) => Promise<void>;

  constructor(private readonly options: SelfHealSchedulerOptions) {
    this.states = options.initialState ?? new Map();
    this.enabled = options.enabled;
    this.probeEnabled = options.probeEnabled === true;
    this.now = options.now ?? (() => Date.now());
    this.spacingMs = options.spacingMs ?? selfHealQueueSpacingMs;
    this.wait = options.wait ?? defaultWait;
  }

  isEnabled(): boolean {
    return this.enabled;
  }

  setEnabled(enabled: boolean): void {
    this.enabled = enabled;
    if (!enabled) this.queue.length = 0;
  }

  isProbeEnabled(): boolean {
    return this.probeEnabled;
  }

  setProbeEnabled(enabled: boolean): void {
    this.probeEnabled = enabled;
    if (!enabled) {
      for (let index = this.queue.length - 1; index >= 0; index -= 1) {
        if (this.queue[index]?.job === "probe") this.queue.splice(index, 1);
      }
    }
  }

  /** Cihaz kendini ilan etti; sırayla ele alınmak üzere kuyruğa alınır. */
  schedule(deviceId: string): void {
    this.enqueue(deviceId, "announce");
  }

  /** Cihaz çevrimdışı göründü; ucuz yoklama için kuyruğa alınır. */
  scheduleProbe(deviceId: string): void {
    if (!this.probeEnabled || !this.options.probe) return;
    this.enqueue(deviceId, "probe");
  }

  private enqueue(deviceId: string, job: SelfHealJob): void {
    if (!this.enabled || !deviceId) return;
    // Cihaz başına aynı anda tek iş: koordinatöre iki koldan yüklenmeyi baştan keser.
    if (this.active === deviceId || this.queue.some((item) => item.deviceId === deviceId)) return;
    const decision = job === "probe" ? this.decideProbe(deviceId) : this.decide(deviceId);
    if (decision !== "run") return;
    this.queue.push({ deviceId, job });
    this.kick();
  }

  /** Kararı verir ve tavanı aşan cihazı aynı anda geri çekilmeye alır. */
  private decide(deviceId: string): "run" | SelfHealSkip {
    const decision = selfHealDecision(this.states.get(deviceId), this.now());
    if (decision === "hourly_limit") this.enterBackoff(deviceId);
    return decision;
  }

  private decideProbe(deviceId: string): "run" | SelfHealProbeSkip {
    const decision = selfHealProbeDecision(this.states.get(deviceId), this.now());
    if (decision === "hourly_limit") this.enterBackoff(deviceId);
    return decision;
  }

  /** Testler için: kuyruk boşalana kadar bekler. */
  async whenIdle(): Promise<void> {
    while (this.running) await this.running;
  }

  private kick(): void {
    if (this.running) return;
    this.running = this.pump()
      .catch((error) => console.warn(`Otomatik onarım kuyruğu durdu: ${String(error)}`))
      .finally(() => {
        this.running = null;
      });
  }

  private async pump(): Promise<void> {
    while (this.queue.length > 0) {
      const item = this.queue.shift();
      if (!item) continue;
      if (!this.enabled) continue;
      if (item.job === "probe" && !this.probeEnabled) continue;
      const idle = this.spacingMs - (this.now() - this.lastRunAt);
      if (this.lastRunAt > 0 && idle > 0) await this.wait(idle);
      if (!this.enabled) continue;
      this.active = item.deviceId;
      try {
        if (item.job === "probe") await this.probeAttempt(item.deviceId);
        else await this.attempt(item.deviceId);
      } finally {
        this.active = null;
      }
    }
  }

  /** Yasak durum kontrolü; her iki iş türü için de aynıdır. */
  private async blockedReason(deviceId: string): Promise<string | null> {
    try {
      return await this.options.blockedReason(deviceId);
    } catch (error) {
      return String(error);
    }
  }

  private async attempt(deviceId: string): Promise<void> {
    if (this.decide(deviceId) !== "run") return;
    const reason = await this.blockedReason(deviceId);
    if (reason) {
      console.info(`Otomatik onarım ertelendi (${deviceId}): ${reason}`);
      return;
    }
    let run: (() => Promise<void>) | null;
    try {
      run = await this.options.prepare(deviceId);
    } catch (error) {
      console.warn(`Otomatik onarım hazırlanamadı (${deviceId}): ${String(error)}`);
      return;
    }
    if (!run) return;
    this.markAttempt(deviceId);
    this.options.onOutcome(deviceId, "attempt");
    try {
      await run();
      const state = this.stateFor(deviceId);
      state.lastConfiguredAt = this.now();
      this.options.persist?.(this.states);
      this.options.onOutcome(deviceId, "ok");
      console.log(`Cihaz kendini ilan etti, raporlama ayarları yenilendi (${deviceId}).`);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.options.onOutcome(deviceId, "failed");
      this.options.onFailure?.(deviceId, message);
      console.warn(`Otomatik onarım başarısız (${deviceId}): ${message}`);
    } finally {
      this.lastRunAt = this.now();
    }
  }

  /**
   * Çevrimdışı cihazı tek ucuz okumayla yoklar. Başarısız yoklama olay akışına yazılmaz —
   * aylardır ölü cihazlar cihaz olay kaydını boğmasın; onlar konsol izinde ve hata
   * günlüğünde görünür.
   */
  private async probeAttempt(deviceId: string): Promise<void> {
    const probe = this.options.probe;
    if (!probe || !this.probeEnabled) return;
    if (this.decideProbe(deviceId) !== "run") return;
    const reason = await this.blockedReason(deviceId);
    if (reason) {
      console.info(`Çevrimdışı yoklama ertelendi (${deviceId}): ${reason}`);
      return;
    }
    let run: (() => Promise<SelfHealProbeResult>) | null;
    try {
      run = await probe(deviceId);
    } catch (error) {
      console.warn(`Çevrimdışı yoklama hazırlanamadı (${deviceId}): ${String(error)}`);
      return;
    }
    if (!run) return;
    this.markAttempt(deviceId);
    try {
      const result = await run();
      if (result.reachable) this.probeSucceeded(deviceId, result);
      else this.probeFailed(deviceId, result.message ?? "Cihaz yoklamaya yanıt vermedi.");
    } catch (error) {
      this.probeFailed(deviceId, error instanceof Error ? error.message : String(error));
    } finally {
      this.lastRunAt = this.now();
    }
  }

  private probeSucceeded(deviceId: string, result: SelfHealProbeResult): void {
    const state = this.stateFor(deviceId);
    delete state.probeFailures;
    delete state.probeNextAt;
    delete state.probeUnreachableAt;
    if (result.configured) state.lastConfiguredAt = this.now();
    this.options.persist?.(this.states);
    // Yalnız gerçekten bir şey değiştiyse iz bırakılır: erişilebilirlik döndüyse ya da
    // yapılandırma çalıştıysa.
    if (result.recovered || result.configured) this.options.onOutcome(deviceId, "ok");
    if (result.message) this.options.onFailure?.(deviceId, result.message);
    console.log(
      `Çevrimdışı cihaz yoklamaya yanıt verdi, erişilebilir işaretlendi (${deviceId})`
      + `${result.configured ? "; raporlama ayarları da yenilendi." : "."}`
    );
  }

  private probeFailed(deviceId: string, message: string): void {
    const state = this.stateFor(deviceId);
    const failures = (state.probeFailures ?? 0) + 1;
    const delay = selfHealProbeDelayMs(failures);
    const now = this.now();
    state.probeFailures = failures;
    state.probeNextAt = now + delay;
    const becameUnreachable =
      failures >= selfHealProbeUnreachableAfter && state.probeUnreachableAt === undefined;
    if (becameUnreachable) state.probeUnreachableAt = now;
    this.options.persist?.(this.states);
    this.options.onFailure?.(deviceId, message);
    if (becameUnreachable) {
      // Kalıcı ulaşılamaz durumuna geçiş yalnız bir kez olay akışına yazılır.
      this.options.onOutcome(deviceId, "failed");
      console.warn(`Cihaz kalıcı ulaşılamaz sayıldı, günde bir yoklanacak (${deviceId}): ${message}`);
      return;
    }
    console.info(
      `Çevrimdışı yoklama yanıtsız (${deviceId}); sonraki deneme ${Math.round(delay / 60_000)} dakika sonra: ${message}`
    );
  }

  private stateFor(deviceId: string): SelfHealDeviceState {
    const existing = this.states.get(deviceId);
    if (existing) return existing;
    const created: SelfHealDeviceState = { attempts: [] };
    this.states.set(deviceId, created);
    return created;
  }

  private markAttempt(deviceId: string): void {
    const now = this.now();
    const state = this.stateFor(deviceId);
    state.attempts = [...state.attempts.filter((at) => now - at < selfHealWindowMs), now];
    this.lastRunAt = now;
    this.options.persist?.(this.states);
  }

  private enterBackoff(deviceId: string): void {
    const state = this.stateFor(deviceId);
    const until = this.now() + selfHealBackoffMs;
    if (state.backoffUntil === until) return;
    state.backoffUntil = until;
    this.options.persist?.(this.states);
    console.info(
      `Otomatik onarım saatlik tavana ulaştı, ${selfHealBackoffMs / 3_600_000} saat beklenecek (${deviceId}).`
    );
  }
}
