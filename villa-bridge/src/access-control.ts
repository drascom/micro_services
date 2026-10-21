import { createHash, timingSafeEqual } from "node:crypto";
import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import type { AgentTokenStore, AgentTokenSummary } from "./agent-tokens.js";
import type { AuthSession, AuthStore, CreatedAuthSession } from "./auth-store.js";
import { isAllowedMcpOrigin, mcpErrorBody, mcpErrorCodes, mcpRoutePath } from "./mcp.js";

declare module "fastify" {
  interface FastifyRequest {
    villaSession: AuthSession | null;
    /**
     * Oturum **yönetici moduna yükseltilmiş** mi? Rolün yerini alan tek bayrak budur ve
     * kaynağı sunucudur: istemci bunu isteyerek ya da isteyerek olmayarak belirleyemez.
     */
    villaElevated: boolean;
    /**
     * Bu istekte kurulan yeni oturum jetonu. Çerez aynı istek içinde geri okunamadığı için,
     * ilk turda yükseltme ve CSRF denetimi jetona buradan ulaşır.
     */
    villaSessionToken?: string;
    /** `/mcp` isteğini doğrulayan ajan token'ı; başka hiçbir yolda dolmaz. */
    villaAgent: AgentTokenSummary | null;
  }
}

const sessionCookieName = "villa_session";
const stateChangingMethods = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/**
 * Yükseltme hareketsizlikte düşer. "Hareket", **yükseltmeyi kullanan** istektir (yalnız
 * yönetici yolları); panelin 8 saniyede bir attığı ev modu yoklaması sayacı tazelemez, yoksa
 * açık duran bir tablette yönetici modu hiç kapanmazdı.
 */
const elevationIdleMs = 5 * 60 * 1000;

const publicRoutes = new Set([
  "/api/health",
  "/api/discovery",
  "/api/locales",
  "/api/auth/session",
  "/api/auth/setup",
  "/api/auth/login"
]);

const residentRoutes = new Set([
  "GET /api/devices",
  "GET /api/groups",
  "GET /api/pairing",
  "GET /api/overview",
  "GET /api/onboarding",
  "GET /api/favorites",
  "PUT /api/favorites",
  "GET /api/home-groups",
  "PUT /api/home-groups",
  // Görünürlük ("bu lambayı oda kartında gösterme", "bu oda Genel görünümde çıkmasın") günlük
  // bir tercihtir, kurulum ayarı değil: paneli kullanan ev sakini kendi ekranını düzenleyebilmeli.
  // Yazma da resident, çünkü aksi hâlde her düzeltme için yönetici çağırmak gerekirdi. Açtığı
  // veri ev sakininin zaten gördüğü cihaz ve oda listesinden fazlası değil.
  "GET /api/home-visibility",
  "PUT /api/home-visibility",
  "GET /api/automations",
  // Rutini ÇALIŞTIRMAK ev kullanıcısının günlük işidir (ana ekranın hızlı sahne şeridi ve
  // Rutinler görünümü tek dokunuşla bu ucu çağırır); DÜZENLEMEK değildir. Bu yüzden yalnız
  // çalıştırma yolu listelenir: `PUT /api/automations` (kural yazma) listede YOKTUR ve yönetici
  // modu ister. Açtığı yetki, ev sakininin panelden zaten elle yapabildiği cihaz komutlarının
  // aynısıdır — kuralın kendi eylemleri çalışır, yeni bir eylem tanımlanamaz.
  "POST /api/automations/:id/run",
  // Çalışma günlüğü, ev sakininin zaten gördüğü kural ve cihaz verisinden fazlasını açmaz;
  // "neden çalışmadı" sorusunu soran da odur. Yazma yolu yok, salt okunur.
  "GET /api/automation-runs",
  "GET /api/automations/:id/runs",
  // Konum okuması güneş kuralının saatini göstermek için gerekir; **yazma** listelenmez,
  // dolayısıyla yönetici modu ister (evin koordinatı kurulum ayarıdır).
  "GET /api/settings/location",
  "GET /api/celestial",
  "GET /api/theme-packages",
  "GET /api/appearance",
  // Hava durumu ana ekranın parçası: okuması herkese açık. **Konum yazma** (`PUT
  // /api/weather/location`) listede YOKTUR, yönetici modu ister — bir ekranda yapılan seçim
  // evdeki bütün panelleri değiştirir. Şehir araması ev modunda da açılan pencerelerde (dünya
  // saati) kullanıldığı için okumayla birlikte açılır; dışarıya çıkan tek taraf sunucudur.
  "GET /api/weather",
  "GET /api/locations/search",
  // Dünya saati şehirleri de evin ayarıdır (duvardaki tablet); okuması ana ekranın parçası olduğu
  // için ev modunda açık. **Yazma** (`PUT /api/world-clock`) listede YOKTUR, yönetici modu ister —
  // hava konumuyla aynı gerekçe: bir ekranda yapılan düzenleme bütün panelleri değiştirir.
  "GET /api/world-clock",
  "GET /api/device-image/:model",
  "GET /api/devices/:id/note",
  "PUT /api/devices/:id/note",
  // Rol yalnız arayüzdeki sunumu değiştirir (lamba mı anahtar mı) — ev modunda da düzeltilebilir.
  "GET /api/devices/:id/role",
  "PUT /api/devices/:id/role",
  "POST /api/devices/:id/command",
  "POST /api/groups/:id/command",
  "POST /api/auth/logout"
]);

/**
 * MOD KAPISI. Bu iki yol yetki tablolarının HİÇBİRİNDE yer almaz ve yer alamaz: listelenmeyen
 * her yol yönetici modu ister, ama yükseltmeyi isteyen ucun kendisi yükseltme isteyemez —
 * kimse moda giremezdi. Bu yüzden `/mcp` gibi tablo aramasından ÖNCE, kendi kuralıyla ele
 * alınır: ev modundan çağrılabilir, ama `elevate` doğru PIN'i ister ve hız sınırına tabidir.
 */
const modeRoutes = new Set(["/api/mode", "/api/mode/elevate", "/api/mode/leave"]);

const parseCookie = (header: string | undefined, name: string): string | undefined => {
  if (!header) return undefined;
  for (const part of header.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return rest.join("=");
  }
  return undefined;
};

const constantTimeStringEqual = (left: string | undefined, right: string): boolean => {
  if (!left) return false;
  const leftBuffer = Buffer.from(left);
  const rightBuffer = Buffer.from(right);
  return leftBuffer.length === rightBuffer.length && timingSafeEqual(leftBuffer, rightBuffer);
};

const sessionCookie = (token: string, maxAgeSeconds: number, secure: boolean): string =>
  `${sessionCookieName}=${token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=${maxAgeSeconds}${secure ? "; Secure" : ""}`;

const expiredSessionCookie = (secure: boolean): string =>
  `${sessionCookieName}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0${secure ? "; Secure" : ""}`;

const delay = (ms: number): Promise<void> => new Promise((done) => { setTimeout(done, ms); });

interface AttemptState {
  failures: number;
  lastFailureAt: number;
  blockedUntil: number;
}

export class LoginThrottle {
  private readonly attempts = new Map<string, AttemptState>();

  constructor(
    private readonly now: () => number = Date.now,
    private readonly maxAttempts = 5,
    private readonly baseBlockMs = 30_000
  ) {}

  retryAfterSeconds(key: string): number {
    const state = this.attempts.get(key);
    if (!state || state.blockedUntil <= this.now()) return 0;
    return Math.max(1, Math.ceil((state.blockedUntil - this.now()) / 1000));
  }

  /** Başarısız denemeyi işler ve o anahtarın **toplam** başarısızlık sayısını döndürür. */
  failure(key: string): number {
    const now = this.now();
    const previous = this.attempts.get(key);
    const failures = previous && now - previous.lastFailureAt < 15 * 60_000
      ? previous.failures + 1
      : 1;
    const penaltyLevel = Math.max(0, failures - this.maxAttempts);
    this.attempts.set(key, {
      failures,
      lastFailureAt: now,
      blockedUntil: failures >= this.maxAttempts
        ? now + Math.min(15 * 60_000, this.baseBlockMs * 2 ** penaltyLevel)
        : 0
    });
    return failures;
  }

  success(key: string): void {
    this.attempts.delete(key);
  }
}

/**
 * Yükseltmelerin tutulduğu yer: **bellek**, oturum çerezinin üstünde. Diske yazılmaz, çünkü
 * her yönetici isteğinde sayacı tazelemek dosyayı döverdi; ayrıca servis yeniden başladığında
 * yükseltmenin düşmesi doğru varsayılandır. İstemcide hiçbir karşılığı yoktur — panelin
 * gösterdiği bayrak yalnız sunucunun söylediğidir.
 */
class ElevationRegistry {
  private readonly entries = new Map<string, number>();

  constructor(private readonly now: () => number = Date.now) {}

  private static key(token: string): string {
    return createHash("sha256").update(token, "utf8").digest("base64url");
  }

  private sweep(): void {
    const now = this.now();
    for (const [key, expiresAt] of this.entries) {
      if (expiresAt <= now) this.entries.delete(key);
    }
  }

  isElevated(token: string | undefined): boolean {
    if (!token) return false;
    this.sweep();
    return (this.entries.get(ElevationRegistry.key(token)) ?? 0) > this.now();
  }

  /** Yükseltir ya da hareketsizlik sayacını sıfırdan başlatır. */
  elevate(token: string): string {
    this.sweep();
    const expiresAt = this.now() + elevationIdleMs;
    this.entries.set(ElevationRegistry.key(token), expiresAt);
    return new Date(expiresAt).toISOString();
  }

  /** Yalnız zaten yükseltilmiş oturumun sayacını tazeler; yeni yükseltme AÇMAZ. */
  touch(token: string | undefined): void {
    if (token && this.isElevated(token)) this.elevate(token);
  }

  drop(token: string | undefined): void {
    if (token) this.entries.delete(ElevationRegistry.key(token));
  }

  /** PIN değiştiğinde: eski PIN'le açılmış BÜTÜN yükseltmeler düşer. */
  dropAll(): void {
    this.entries.clear();
  }

  expiresAt(token: string | undefined): string | null {
    if (!token) return null;
    const value = this.entries.get(ElevationRegistry.key(token));
    return value && value > this.now() ? new Date(value).toISOString() : null;
  }
}

export interface AccessControlOptions {
  secureCookies?: boolean;
  /** Yönetici PIN'i hız sınırı. Verilmezse 5 denemede kilit + katlanan bekleme kurulur. */
  throttle?: LoginThrottle;
  /** Ajan token deposu; verilmezse `/mcp` her istekte 401 döner ve yönetim uçları açılmaz. */
  agentTokens?: AgentTokenStore;
  /** `/mcp` için izinli `Origin` başlıkları; boşsa Origin gönderen istemci reddedilir. */
  mcpAllowedOrigins?: readonly string[];
  /** Başarısız PIN denemeleri hata ayıklama listesine buradan düşer. */
  recordError?: (entry: { operation: string; statusCode: number; message: string }) => void;
}

export const registerAccessControl = async (
  app: FastifyInstance,
  authStore: AuthStore,
  options: AccessControlOptions = {}
): Promise<void> => {
  const secureCookies = options.secureCookies === true;
  // 5 yanlış PIN → 60 sn kilit, sonraki her yanlışta iki katı (en çok 15 dk). Dört haneli bir
  // sırrın 10.000 ihtimalini bu hızla taramak günler sürer; kaba kuvvet pratikte kapanır.
  const throttle = options.throttle ?? new LoginThrottle(Date.now, 5, 60_000);
  const elevations = new ElevationRegistry();
  const agentTokens = options.agentTokens;
  const mcpAllowedOrigins = options.mcpAllowedOrigins ?? [];
  const recordError = options.recordError ?? (() => undefined);
  app.decorateRequest("villaSession", null);
  app.decorateRequest("villaElevated", false);
  app.decorateRequest("villaSessionToken", undefined);
  app.decorateRequest("villaAgent", null);

  const requestToken = (request: FastifyRequest): string | undefined =>
    parseCookie(request.headers.cookie, sessionCookieName);

  /**
   * Giriş ekranı yok: oturumu olmayan ziyaretçi kapıda kendi **ev oturumunu** alır. Oturum
   * yalnız CSRF jetonunu ve yükseltmenin bağlanacağı kimliği taşır; hiçbir yetki içermez.
   */
  const ensureSession = async (
    request: FastifyRequest,
    reply: FastifyReply
  ): Promise<AuthSession> => {
    const existing = await authStore.getSession(requestToken(request));
    if (existing) return existing;
    const created: CreatedAuthSession = await authStore.createSession();
    reply.header("Set-Cookie", sessionCookie(
      created.token,
      Math.max(1, Math.floor((Date.parse(created.expiresAt) - Date.now()) / 1000)),
      secureCookies
    ));
    // Aynı istek içinde çerez daha okunamaz; jetonu isteğe iliştiriyoruz ki yükseltme ve CSRF
    // denetimi bu ilk turda da doğru oturuma baksın.
    request.villaSessionToken = created.token;
    return { csrfToken: created.csrfToken, expiresAt: created.expiresAt };
  };

  const effectiveToken = (request: FastifyRequest): string | undefined =>
    request.villaSessionToken ?? requestToken(request);

  /**
   * `/mcp` kapısı. Aşağıdaki tablo yalnız `/api/` yollarını kapsadığı için bu uç kendi başına
   * **korumasız kalırdı**; bu yüzden açıkça ilk sırada ele alınır. Yalnız `Authorization: Bearer`
   * geçer — çerez oturumu bilerek kabul EDİLMEZ, yoksa açık bir tarayıcı sekmesi üzerinden
   * (CSRF deseniyle) evin tamamı sürülebilirdi.
   */
  const authorizeMcpRequest = async (
    request: FastifyRequest,
    reply: FastifyReply
  ): Promise<FastifyReply | undefined> => {
    // Yöntem denetimi kimlikten önce gelir: `GET /mcp` hiçbir veri açmaz, yalnız "bu sürümde
    // akış yok" der; 401 döndürmek istemciyi yanlış yöne (token sorunu) sürüklerdi.
    if (request.method !== "POST") {
      return reply.code(405).header("Allow", "POST").send(mcpErrorBody(
        mcpErrorCodes.methodNotAllowed,
        "MCP ucu yalnız POST kabul eder; bu sürümde GET akışı yoktur."
      ));
    }
    if (!isAllowedMcpOrigin(request.headers.origin, mcpAllowedOrigins)) {
      return reply.code(403).send(mcpErrorBody(
        mcpErrorCodes.forbiddenOrigin,
        "İstek kökeni (Origin) bu uç için izinli değil."
      ));
    }
    const authorization = request.headers.authorization;
    const token = typeof authorization === "string" && /^Bearer /.test(authorization)
      ? authorization.slice("Bearer ".length).trim()
      : undefined;
    const agent = token && agentTokens ? await agentTokens.verify(token) : null;
    if (!agent) {
      return reply.code(401).header("WWW-Authenticate", "Bearer").send(mcpErrorBody(
        mcpErrorCodes.unauthorized,
        "Geçerli bir ajan token'ı gerekiyor: Authorization: Bearer <token>."
      ));
    }
    request.villaAgent = agent;
    return undefined;
  };

  app.addHook("onRequest", async (request, reply) => {
    const route = request.routeOptions.url ?? request.url.split("?")[0];
    if (route === mcpRoutePath) return authorizeMcpRequest(request, reply);
    if (!route.startsWith("/api/")) return;

    if (publicRoutes.has(route)) {
      // Açık yollara makineler de vuruyor (sağlık yoklaması, LAN keşfi): onlara oturum
      // AÇMIYORUZ, yalnız varsa taşıyoruz. Oturumu `/api/auth/session` kendi elinde açar.
      request.villaSession = await authStore.getSession(requestToken(request));
      request.villaElevated = elevations.isElevated(requestToken(request));
      return;
    }

    const session = await ensureSession(request, reply);
    request.villaSession = session;
    const token = effectiveToken(request);
    if (
      stateChangingMethods.has(request.method)
      && !constantTimeStringEqual(request.headers["x-villa-csrf"] as string | undefined, session.csrfToken)
    ) {
      return reply.code(403).send({
        ok: false,
        code: "INVALID_CSRF_TOKEN",
        error: "Güvenlik doğrulaması geçersiz."
      });
    }
    request.villaElevated = elevations.isElevated(token);

    // Mod uçları kendi kapılarını taşır (yukarıdaki `modeRoutes` yorumuna bakın).
    if (modeRoutes.has(route)) return;
    if (residentRoutes.has(`${request.method} ${route}`)) return;

    // Listelenmeyen her yol yönetici modu ister. Rolün yerini alan denetim tam olarak budur:
    // kontrol SUNUCUDA yapılır, istemcinin gizlediği düğmelerle hiçbir ilgisi yoktur.
    if (!request.villaElevated) {
      return reply.code(403).send({
        ok: false,
        code: "ELEVATION_REQUIRED",
        error: "Bu işlem için yönetici modu gerekiyor."
      });
    }
    // Yalnız yükseltmeyi KULLANAN istek hareketsizlik sayacını tazeler.
    elevations.touch(token);
  });

  const modeState = async (request: FastifyRequest) => {
    const secret = await authStore.adminSecretState();
    const token = effectiveToken(request);
    return {
      elevated: elevations.isElevated(token),
      elevationExpiresAt: elevations.expiresAt(token),
      elevationIdleMs,
      secretKind: secret.secretKind,
      mustChangePin: secret.mustChange
    };
  };

  /**
   * Panelin açılış ucu. Adı eski akıştan kalma (`publicRoutes` tablosuna dokunmuyoruz) ama
   * artık "kim giriş yaptı" değil, "hangi moddayız" sorusunu yanıtlar ve ev oturumu çerezini
   * bu istekle birlikte kurar.
   */
  app.get("/api/auth/session", async (request, reply) => {
    const session = await ensureSession(request, reply);
    return {
      ok: true,
      csrfToken: session.csrfToken,
      expiresAt: session.expiresAt,
      ...await modeState(request)
    };
  });

  app.get("/api/mode", async (request) => ({ ok: true, ...await modeState(request) }));

  app.post<{ Body?: { pin?: unknown } }>("/api/mode/elevate", async (request, reply) => {
    const key = `${request.ip}:mode`;
    const retryAfter = throttle.retryAfterSeconds(key);
    if (retryAfter > 0) {
      recordError({
        operation: "mode-elevate",
        statusCode: 429,
        message: `Kilitli istemci yeniden denedi (${request.ip}); ${retryAfter} sn kaldı.`
      });
      return reply
        .header("Retry-After", String(retryAfter))
        .code(429)
        .send({
          ok: false,
          code: "MODE_LOCKED",
          retryAfter,
          error: "Çok fazla yanlış PIN girildi. Biraz sonra yeniden deneyin."
        });
    }
    if (!await authStore.verifyAdminSecret(request.body?.pin)) {
      const failures = throttle.failure(key);
      recordError({
        operation: "mode-elevate",
        statusCode: 401,
        message: `Yanlış yönetici PIN'i (${request.ip}); art arda ${failures}. deneme.`
      });
      // ARTAN GECİKME: her yanlışta cevap gecikir. Kilit devreye girmeden önce bile taramayı
      // yavaşlatır ve doğru/yanlış arasındaki zamanlama farkını bastırır.
      await delay(Math.min(2000, 150 * 2 ** Math.min(failures, 4)));
      return reply.code(401).send({
        ok: false,
        code: "INVALID_PIN",
        error: "Yönetici PIN’i yanlış.",
        retryAfter: throttle.retryAfterSeconds(key)
      });
    }
    throttle.success(key);
    const token = effectiveToken(request);
    if (!token) {
      return reply.code(409).send({
        ok: false,
        code: "SESSION_REQUIRED",
        error: "Oturum kurulamadı; sayfayı yenileyip yeniden deneyin."
      });
    }
    elevations.elevate(token);
    return { ok: true, ...await modeState(request) };
  });

  app.post("/api/mode/leave", async (request) => {
    elevations.drop(effectiveToken(request));
    return { ok: true, ...await modeState(request) };
  });

  /** Yönetici modundayken PIN'i değiştirir. Yol listelenmediği için zaten yükseltme ister. */
  app.put<{ Body?: { pin?: unknown } }>("/api/auth/admin-pin", async (request, reply) => {
    try {
      await authStore.setAdminPin(request.body?.pin);
      // Eski PIN'le açılmış başka bir ekran açık kalmasın; PIN'i değiştiren oturum devam eder.
      elevations.dropAll();
      const token = effectiveToken(request);
      if (token) elevations.elevate(token);
      return { ok: true, ...await modeState(request) };
    } catch (error) {
      return reply.code(400).send({
        ok: false,
        error: error instanceof Error ? error.message : String(error)
      });
    }
  });

  /**
   * Oturumu tamamen bırakır (çerez silinir, yükseltme düşer). Giriş ekranı olmadığı için
   * pratikte "bu tarayıcıyı unut" anlamına gelir; panel bir sonraki istekte yeni ev oturumu alır.
   */
  app.post("/api/auth/logout", async (request, reply) => {
    const token = effectiveToken(request);
    elevations.drop(token);
    await authStore.dropSession(token);
    return reply
      .header("Set-Cookie", expiredSessionCookie(secureCookies))
      .send({ ok: true });
  });

  // Ajan token yönetimi. Yetki tablolarında listelenmediği için yönetici modu ister; ham token
  // yalnız üretim yanıtında bir kez döner, listede bir daha görünmez.
  if (agentTokens) {
    app.get("/api/agent-tokens", async (_request, reply) => {
      try {
        return { ok: true, tokens: await agentTokens.list() };
      } catch (error) {
        return reply.code(503).send({
          ok: false,
          error: error instanceof Error ? error.message : String(error)
        });
      }
    });

    app.post<{ Body?: { name?: unknown } }>("/api/agent-tokens", async (request, reply) => {
      try {
        const created = await agentTokens.create(request.body?.name);
        return { ok: true, token: created.token, record: created.record };
      } catch (error) {
        return reply.code(400).send({
          ok: false,
          error: error instanceof Error ? error.message : String(error)
        });
      }
    });

    app.delete<{ Params: { id: string } }>("/api/agent-tokens/:id", async (request, reply) => {
      if (!await agentTokens.revoke(request.params.id)) {
        return reply.code(404).send({ ok: false, error: "Ajan token'ı bulunamadı." });
      }
      return { ok: true, tokens: await agentTokens.list() };
    });
  }
};
