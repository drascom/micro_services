import {
  createHash,
  randomBytes,
  scrypt as scryptCallback,
  timingSafeEqual
} from "node:crypto";
import { readFile } from "node:fs/promises";
import { writeJsonAtomic } from "./atomic-file.js";

/**
 * ROLLER KALKTI. Panel giriş ekranı olmadan **ev modunda** açılır; kurulum ekranları
 * "yönetici modu" ister ve o modun tek anahtarı burada duran **tek sır**dır (varsayılan `1234`).
 * Yükseltmenin kendisi bu dosyada tutulmaz — oturumun üstünde, `access-control.ts` içinde
 * yaşar ve hareketsizlikte düşer. Burada yalnız "sır doğru mu" sorusunun cevabı vardır.
 *
 * `agent` (makine istemcisi) kimliği bu depoya hiç uğramaz: onun kimliğini `agent-tokens.json`
 * içindeki Bearer token taşır ve yalnız `/mcp` yolunda geçerlidir.
 */

interface ScryptParameters {
  N: number;
  r: number;
  p: number;
  keyLength: number;
}

/** Yükseltme sırrı. `kind` yalnız panelin doğru klavyeyi açması için taşınır. */
interface StoredSecret {
  kind: "pin" | "password";
  salt: string;
  hash: string;
  scrypt: ScryptParameters;
}

interface StoredSession {
  tokenHash: string;
  csrfToken: string;
  expiresAt: string;
}

interface StoredAuthState {
  version: 2;
  admin: StoredSecret;
  /**
   * Sır hâlâ fabrika varsayılanı (`1234`) mı? Panel bunu görünce yönetici modunda kalıcı
   * uyarı şeridi gösterir. Yeni PIN yazılırken değeri yeniden hesaplanır.
   */
  mustChange: boolean;
  sessions: StoredSession[];
}

export interface AuthSession {
  csrfToken: string;
  expiresAt: string;
}

export interface CreatedAuthSession extends AuthSession {
  token: string;
}

export interface AdminSecretState {
  /** Panelin PIN alanını sayısal mı yoksa serbest metin mi açacağını söyler. */
  secretKind: "pin" | "password";
  /** `true` ise sır hâlâ `1234`. */
  mustChange: boolean;
}

export interface AuthStoreOptions {
  now?: () => Date;
  sessionLifetimeMs?: number;
  scrypt?: Partial<ScryptParameters>;
}

const defaultScrypt: ScryptParameters = {
  N: 2 ** 15,
  r: 8,
  p: 3,
  keyLength: 32
};

/** Fabrika PIN'i. Kurulum ekranı olmadığı için ilk açılışta geçerli olan tek sır budur. */
export const defaultAdminPin = "1234";

const tokenHash = (token: string): string =>
  createHash("sha256").update(token, "utf8").digest("base64url");

const deriveSecret = (
  secret: string,
  salt: Buffer,
  parameters: ScryptParameters
): Promise<Buffer> => new Promise((resolvePromise, rejectPromise) => {
  scryptCallback(secret, salt, parameters.keyLength, {
    N: parameters.N,
    r: parameters.r,
    p: parameters.p,
    maxmem: Math.max(64 * 1024 * 1024, 256 * parameters.N * parameters.r)
  }, (error, derived) => {
    if (error) rejectPromise(error);
    else resolvePromise(derived);
  });
});

/**
 * Yönetici PIN'i: 4–8 rakam. Üst sınır bilerek dar — bu bir parola değil, duvardaki tablette
 * parmakla girilen bir moddan-çıkış anahtarıdır; uzunluk yerine hız sınırı korur (bkz.
 * `access-control.ts`). Alt sınır 4, çünkü varsayılanın kendisi dört hanedir.
 */
export const validateAdminPin = (value: unknown): string => {
  if (typeof value !== "string" || !/^\d{4,8}$/.test(value)) {
    throw new Error("Yönetici PIN’i 4–8 rakam olmalıdır.");
  }
  return value;
};

const isStoredSecret = (value: unknown): value is StoredSecret => {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<StoredSecret>;
  return (candidate.kind === "pin" || candidate.kind === "password")
    && typeof candidate.salt === "string"
    && typeof candidate.hash === "string"
    && typeof candidate.scrypt === "object"
    && candidate.scrypt !== null
    && typeof (candidate.scrypt as ScryptParameters).N === "number"
    && typeof (candidate.scrypt as ScryptParameters).r === "number"
    && typeof (candidate.scrypt as ScryptParameters).p === "number"
    && typeof (candidate.scrypt as ScryptParameters).keyLength === "number";
};

const readSessions = (value: unknown): StoredSession[] => (Array.isArray(value) ? value : [])
  .filter((entry): entry is StoredSession =>
    typeof entry === "object" && entry !== null
    && typeof (entry as StoredSession).tokenHash === "string"
    && typeof (entry as StoredSession).csrfToken === "string"
    && typeof (entry as StoredSession).expiresAt === "string");

export class AuthStore {
  private readonly now: () => Date;
  private readonly sessionLifetimeMs: number;
  private readonly scryptParameters: ScryptParameters;
  private cachedState: StoredAuthState | null = null;
  private loading: Promise<StoredAuthState> | null = null;
  private mutation: Promise<void> = Promise.resolve();

  constructor(private readonly path: string, options: AuthStoreOptions = {}) {
    this.now = options.now ?? (() => new Date());
    this.sessionLifetimeMs = options.sessionLifetimeMs ?? 7 * 24 * 60 * 60 * 1000;
    this.scryptParameters = { ...defaultScrypt, ...options.scrypt };
  }

  /**
   * GÖÇ — canlı sunucudaki `auth.json` gerçek kimlik taşıyor, bu yüzden hiçbir yol
   * "erişilemez"e çıkmaz:
   *
   *  1. Dosya **yeni şekildeyse** (`version: 2`) olduğu gibi kullanılır.
   *  2. Dosya **eski şekildeyse** (`version: 1`, `users[]` + rol ayrımı): eski YÖNETİCİ
   *     kullanıcısının parola hash'i olduğu gibi yeni tek sırra taşınır (`kind: "password"`).
   *     Kullanıcı güncellemeden sonra eskiden kullandığı yönetici parolasıyla mod
   *     yükseltmeye devam eder — sessiz bir güvenlik düşüşü OLMAZ. Ev sakini PIN'i düşer,
   *     çünkü artık ev modu sır istemiyor. Eski oturumlar da düşer: rol taşıyorlardı,
   *     karşılıkları yok; panel bir sonraki istekte yeni ev oturumunu kendisi alır.
   *  3. Dosya yoksa, bozuksa ya da tanınmayan bir şekildeyse: varsayılan `1234` +
   *     `mustChange` ile açılır. Bu, panelin kendi kapısında kilitli kalmasını önleyen
   *     son çaredir; panel bu durumda kalıcı uyarı şeridi gösterir.
   */
  private async migrate(raw: unknown): Promise<{ state: StoredAuthState; rewrite: boolean }> {
    const candidate = (typeof raw === "object" && raw !== null && !Array.isArray(raw))
      ? raw as Record<string, unknown>
      : null;

    if (candidate?.version === 2 && isStoredSecret(candidate.admin)) {
      return {
        state: {
          version: 2,
          admin: candidate.admin,
          mustChange: candidate.mustChange === true,
          sessions: readSessions(candidate.sessions)
        },
        rewrite: false
      };
    }

    if (candidate?.version === 1 && Array.isArray(candidate.users)) {
      const legacyAdmin = (candidate.users as Array<Record<string, unknown>>).find((user) =>
        user?.role === "admin" && typeof user.salt === "string" && typeof user.hash === "string"
      );
      if (legacyAdmin && isStoredSecret({ ...legacyAdmin, kind: "password" })) {
        return {
          state: {
            version: 2,
            admin: {
              kind: "password",
              salt: legacyAdmin.salt as string,
              hash: legacyAdmin.hash as string,
              scrypt: legacyAdmin.scrypt as ScryptParameters
            },
            mustChange: false,
            sessions: []
          },
          rewrite: true
        };
      }
    }

    return { state: await this.defaultState(), rewrite: true };
  }

  private async defaultState(): Promise<StoredAuthState> {
    return {
      version: 2,
      admin: { kind: "pin", ...await this.hashSecret(defaultAdminPin) },
      mustChange: true,
      sessions: []
    };
  }

  private async load(): Promise<StoredAuthState> {
    if (this.cachedState) return this.cachedState;
    if (this.loading) return this.loading;
    this.loading = (async () => {
      let raw: unknown = null;
      try {
        raw = JSON.parse(await readFile(this.path, "utf8"));
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
          console.warn(
            `Erişim kaydı okunamadı (${this.path}); varsayılan yönetici PIN’i ile açılıyor.`,
            error
          );
        }
      }
      const { state, rewrite } = await this.migrate(raw);
      this.cachedState = state;
      if (rewrite) await this.save(state);
      return state;
    })().finally(() => { this.loading = null; });
    return this.loading;
  }

  private async save(state: StoredAuthState): Promise<void> {
    await writeJsonAtomic(this.path, state, { mode: 0o600 });
    this.cachedState = state;
  }

  private async exclusive<T>(operation: () => Promise<T>): Promise<T> {
    const result = this.mutation.then(operation, operation);
    this.mutation = result.then(() => undefined, () => undefined);
    return result;
  }

  private async hashSecret(secret: string, salt = randomBytes(16)): Promise<{
    salt: string;
    hash: string;
    scrypt: ScryptParameters;
  }> {
    const parameters = this.scryptParameters;
    const derived = await deriveSecret(secret.normalize("NFC"), salt, parameters);
    return {
      salt: salt.toString("base64url"),
      hash: derived.toString("base64url"),
      scrypt: { ...parameters }
    };
  }

  private activeSessions(sessions: StoredSession[]): StoredSession[] {
    const now = this.now().getTime();
    return sessions.filter((session) => Date.parse(session.expiresAt) > now).slice(-40);
  }

  /** Panelin uyarı şeridi ve PIN alanının klavyesi için gereken iki bilgi. */
  async adminSecretState(): Promise<AdminSecretState> {
    const state = await this.load();
    return { secretKind: state.admin.kind, mustChange: state.mustChange };
  }

  /** Yönetici sırrını doğrular. Hız sınırı çağıranın işidir (bkz. `access-control.ts`). */
  async verifyAdminSecret(secretValue: unknown): Promise<boolean> {
    if (typeof secretValue !== "string" || secretValue.length === 0 || secretValue.length > 128) {
      return false;
    }
    const state = await this.load();
    const expected = Buffer.from(state.admin.hash, "base64url");
    const actual = await deriveSecret(
      secretValue.normalize("NFC"),
      Buffer.from(state.admin.salt, "base64url"),
      state.admin.scrypt
    );
    return expected.length === actual.length && timingSafeEqual(expected, actual);
  }

  async setAdminPin(pinValue: unknown): Promise<void> {
    const pin = validateAdminPin(pinValue);
    await this.exclusive(async () => {
      const state = await this.load();
      await this.save({
        ...state,
        admin: { kind: "pin", ...await this.hashSecret(pin) },
        // Uyarı şeridi "hâlâ varsayılan" sorusuna bakar: kullanıcı bilerek 1234 seçtiyse de
        // şerit kalmalı, yoksa uyarı susturulabilir bir süse dönerdi.
        mustChange: pin === defaultAdminPin,
        sessions: this.activeSessions(state.sessions)
      });
    });
  }

  /** Giriş ekranı yok: her ziyaretçi kapıda kendi ev oturumunu alır. */
  async createSession(): Promise<CreatedAuthSession> {
    return this.exclusive(async () => {
      const state = await this.load();
      const session: CreatedAuthSession = {
        token: randomBytes(32).toString("base64url"),
        csrfToken: randomBytes(24).toString("base64url"),
        expiresAt: new Date(this.now().getTime() + this.sessionLifetimeMs).toISOString()
      };
      await this.save({
        ...state,
        sessions: [
          ...this.activeSessions(state.sessions),
          {
            tokenHash: tokenHash(session.token),
            csrfToken: session.csrfToken,
            expiresAt: session.expiresAt
          }
        ]
      });
      return session;
    });
  }

  async getSession(token: string | undefined): Promise<AuthSession | null> {
    if (!token) return null;
    const state = await this.load();
    const hash = tokenHash(token);
    const session = this.activeSessions(state.sessions).find((candidate) =>
      candidate.tokenHash.length === hash.length
      && timingSafeEqual(Buffer.from(candidate.tokenHash), Buffer.from(hash))
    );
    return session
      ? { csrfToken: session.csrfToken, expiresAt: session.expiresAt }
      : null;
  }

  async dropSession(token: string | undefined): Promise<void> {
    if (!token) return;
    await this.exclusive(async () => {
      const state = await this.load();
      const hash = tokenHash(token);
      await this.save({
        ...state,
        sessions: this.activeSessions(state.sessions)
          .filter((session) => session.tokenHash !== hash)
      });
    });
  }
}
