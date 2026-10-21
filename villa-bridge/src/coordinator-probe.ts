import { constants } from "node:fs";
import { access, stat } from "node:fs/promises";
import { createConnection } from "node:net";

/**
 * Koordinatör adresinin ayrıştırılması ve **dokunmadan** yoklanması.
 *
 * Bu modül koordinatöre TEK BAYT YAZMAZ ve seri portu AÇMAZ. Gerekçe: seri portun açılması
 * DTR/RTS uçlarını oynatır ve koordinatörü resetler; ZNP/EZSP el sıkışması ise ağın ikinci
 * sahibi olmak demektir. Yoklama yalnız "adres var mı" sorusunu yanıtlar — "doğru koordinatör
 * mü" ya da "sahiplenilebilir mi" sorularını YANITLAMAZ (SLZB gibi ağ köprüleri birden çok
 * istemcinin TCP bağlantısını kabul eder). Panel metinleri bu yüzden "doğrulandı" demez.
 */

/** SLZB-06 ve benzeri ağ koordinatörlerinin öntanımlı ham TCP portu. */
export const defaultCoordinatorPort = 6638;

/**
 * "Koordinatör henüz girilmedi" işaretçisi. RFC 5737 belgeleme aralığından seçilmiştir;
 * gerçek bir koordinatör olamaz. İlk açılışta üretilen `configuration.yaml` bu adresle
 * gelir ve çekirdek bu adresi gördüğü sürece **hiçbir Zigbee oturumu açmaz** — kurulum
 * sihirbazı bitene kadar koordinatör sahiplenilmez.
 *
 * Aynı sabit `apps/runtime/first-run.cjs` içinde de vardır (CommonJS tarafı); ikisi
 * birlikte değişmelidir.
 */
export const placeholderCoordinatorAddress = "tcp://192.0.2.10:6638";

/** Adres hâlâ yer tutucu mu? Boş/okunamaz değer de "girilmemiş" sayılır. */
export const isPlaceholderCoordinatorAddress = (value: unknown): boolean => {
  if (typeof value !== "string") return true;
  const text = value.trim();
  if (text.length === 0) return true;
  try {
    return parseCoordinatorAddress(text).value === placeholderCoordinatorAddress;
  } catch {
    return true;
  }
};

export type CoordinatorAddressKind = "tcp" | "serial";

export interface CoordinatorAddress {
  kind: CoordinatorAddressKind;
  /** Kanonik biçim: `tcp://sunucu:port` ya da mutlak seri yol. Kaydedilen değer budur. */
  value: string;
  host: string | null;
  port: number | null;
  path: string | null;
}

export type CoordinatorProbeStatus =
  | "reachable"
  | "refused"
  | "timeout"
  | "dns-failed"
  | "unreachable"
  | "serial-present"
  | "serial-missing"
  | "serial-not-a-device"
  | "serial-no-access";

export interface CoordinatorProbeResult {
  address: string;
  kind: CoordinatorAddressKind;
  status: CoordinatorProbeStatus;
  /** İşletim sistemi hata kodu (`ECONNREFUSED`, `ENOENT` …); metni panel seçer. */
  code: string | null;
}

export interface CoordinatorProbeOptions {
  /** Varsayılan 2500 ms: kullanıcı düğmeye basıp bekliyor, uzun beklemenin karşılığı yok. */
  timeoutMs?: number;
}

const serialPathPattern = /^\/[A-Za-z0-9][A-Za-z0-9._/-]{0,180}$/;
const bareHostPattern = /^[A-Za-z0-9._-]+(:\d{1,5})?$/;

const invalid = (): Error => new Error(
  "Koordinatör adresi geçersiz: `tcp://sunucu:port` ya da `/dev/ttyUSB0` biçiminde olmalı."
);

const parsePort = (value: string): number => {
  const port = value === "" ? defaultCoordinatorPort : Number(value);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error("Koordinatör portu geçersiz.");
  }
  return port;
};

/**
 * Girdiyi kanonik adrese çevirir. `/` ile başlayan her şey seri yoldur; gerisi TCP'dir.
 * Çıplak `sunucu` ya da `sunucu:port` yazılırsa `tcp://` ve öntanımlı port tamamlanır —
 * sunucu adı ASLA ön-doldurulmaz, yalnız kullanıcının yazdığı tamamlanır.
 */
export const parseCoordinatorAddress = (input: unknown): CoordinatorAddress => {
  if (typeof input !== "string") throw invalid();
  const text = input.trim();
  if (text.length === 0 || text.length > 240) throw invalid();
  if (text.startsWith("/")) {
    if (!serialPathPattern.test(text) || text.includes("//") || text.includes("..")) throw invalid();
    return { kind: "serial", value: text, host: null, port: null, path: text };
  }
  if (!text.includes("://") && !bareHostPattern.test(text)) throw invalid();
  const candidate = text.includes("://") ? text : `tcp://${text}`;
  let parsed: URL;
  try {
    parsed = new URL(candidate);
  } catch {
    throw invalid();
  }
  if (parsed.protocol !== "tcp:") {
    throw new Error("Koordinatör adresi yalnız `tcp://` ya da seri yol olabilir.");
  }
  if (!parsed.hostname || parsed.username || parsed.password) throw invalid();
  if ((parsed.pathname !== "" && parsed.pathname !== "/") || parsed.search || parsed.hash) throw invalid();
  const port = parsePort(parsed.port);
  return {
    kind: "tcp",
    value: `tcp://${parsed.hostname}:${port}`,
    host: parsed.hostname,
    port,
    path: null
  };
};

/**
 * Bağlan ve kapat. `peer-watch.ts` içindeki `probeTcp` deseninin aynısı; tek farkı sonucun
 * ikili değil, nedenli olması. Bağlantı kurulur kurulmaz **hiçbir şey yazılmadan** düşürülür.
 */
const probeTcpEndpoint = (
  host: string,
  port: number,
  timeoutMs: number
): Promise<{ status: CoordinatorProbeStatus; code: string | null }> => new Promise((resolve) => {
  const socket = createConnection({ host, port });
  let finished = false;
  const done = (status: CoordinatorProbeStatus, code: string | null): void => {
    if (finished) return;
    finished = true;
    socket.destroy();
    resolve({ status, code });
  };
  socket.setTimeout(timeoutMs);
  socket.once("connect", () => done("reachable", null));
  socket.once("timeout", () => done("timeout", null));
  socket.once("error", (error: NodeJS.ErrnoException) => {
    const code = error.code ?? null;
    if (code === "ECONNREFUSED") return done("refused", code);
    if (code === "ENOTFOUND" || code === "EAI_AGAIN") return done("dns-failed", code);
    if (code === "ETIMEDOUT") return done("timeout", code);
    done("unreachable", code);
  });
});

/** Seri yol: yalnız dosya sistemi bakışı — aygıt düğümü var mı, karakter aygıtı mı, erişilir mi. */
const probeSerialPath = async (
  path: string
): Promise<{ status: CoordinatorProbeStatus; code: string | null }> => {
  let info: Awaited<ReturnType<typeof stat>>;
  try {
    info = await stat(path);
  } catch (error) {
    return { status: "serial-missing", code: (error as NodeJS.ErrnoException).code ?? null };
  }
  if (!info.isCharacterDevice()) return { status: "serial-not-a-device", code: null };
  try {
    await access(path, constants.R_OK | constants.W_OK);
  } catch (error) {
    return { status: "serial-no-access", code: (error as NodeJS.ErrnoException).code ?? null };
  }
  return { status: "serial-present", code: null };
};

export const probeCoordinator = async (
  address: CoordinatorAddress,
  options: CoordinatorProbeOptions = {}
): Promise<CoordinatorProbeResult> => {
  const timeoutMs = Math.min(5000, Math.max(500, options.timeoutMs ?? 2500));
  const outcome = address.kind === "serial"
    ? await probeSerialPath(address.path ?? "")
    : await probeTcpEndpoint(address.host ?? "", address.port ?? defaultCoordinatorPort, timeoutMs);
  return { address: address.value, kind: address.kind, status: outcome.status, code: outcome.code };
};
