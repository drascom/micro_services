import { createHash, randomBytes, timingSafeEqual } from "node:crypto";
import { readFile } from "node:fs/promises";
import { writeJsonAtomic } from "./atomic-file.js";

/**
 * Ajan (makine istemcisi) token'ları. Çerez oturumundan **ayrı** bir kavramdır: süresiz yaşar,
 * yenilenmez, tarayıcıya hiç girmez. `auth.json` ile aynı dizinde, kendi dosyasında durur —
 * kullanıcı hesaplarının döndüğü dosyayı ajan kayıtlarıyla karıştırmamak için.
 *
 * Token düz metin saklanmaz: yalnız özeti tutulur, ham değer üretim anında bir kez döner.
 * Parola/PIN'in aksine burada scrypt gerekmez — token kullanıcı seçimi değil, 32 bayt rastgele
 * veridir; sözlük saldırısına konu olmaz, sabit maliyetli SHA-256 özeti yeter (oturum
 * çerezlerinde de aynı gerekçeyle aynı yöntem kullanılıyor).
 */
export interface AgentTokenSummary {
  id: string;
  name: string;
  createdAt: string;
  lastUsedAt: string | null;
}

interface StoredAgentToken extends AgentTokenSummary {
  tokenHash: string;
}

interface StoredAgentTokenState {
  version: 1;
  tokens: StoredAgentToken[];
}

export interface AgentTokenStoreOptions {
  now?: () => Date;
  /** `lastUsedAt` yazma kısıtlaması; her istekte diske yazmamak için. */
  lastUsedWriteIntervalMs?: number;
}

export const maxAgentTokens = 32;
export const maxAgentTokenNameLength = 48;

const emptyState = (): StoredAgentTokenState => ({ version: 1, tokens: [] });

const hashToken = (token: string): string =>
  createHash("sha256").update(token, "utf8").digest("base64url");

const equalHashes = (left: string, right: string): boolean => {
  const leftBuffer = Buffer.from(left);
  const rightBuffer = Buffer.from(right);
  return leftBuffer.length === rightBuffer.length && timingSafeEqual(leftBuffer, rightBuffer);
};

export const validateAgentTokenName = (value: unknown): string => {
  const name = typeof value === "string" ? value.trim() : "";
  if (!name || name.length > maxAgentTokenNameLength) {
    throw new Error(`Ajan token adı 1–${maxAgentTokenNameLength} karakter olmalıdır.`);
  }
  return name;
};

const validateStoredState = (value: unknown): StoredAgentTokenState => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("Ajan token kaydı geçersiz.");
  }
  const candidate = value as Partial<StoredAgentTokenState>;
  if (candidate.version !== 1 || !Array.isArray(candidate.tokens)) {
    throw new Error("Ajan token kaydı sürümü geçersiz.");
  }
  return candidate as StoredAgentTokenState;
};

const summaryOf = (token: StoredAgentToken): AgentTokenSummary => ({
  id: token.id,
  name: token.name,
  createdAt: token.createdAt,
  lastUsedAt: token.lastUsedAt
});

export class AgentTokenStore {
  private readonly now: () => Date;
  private readonly lastUsedWriteIntervalMs: number;
  private cachedState: StoredAgentTokenState | null = null;
  private mutation: Promise<void> = Promise.resolve();

  constructor(private readonly path: string, options: AgentTokenStoreOptions = {}) {
    this.now = options.now ?? (() => new Date());
    this.lastUsedWriteIntervalMs = options.lastUsedWriteIntervalMs ?? 60_000;
  }

  private async load(): Promise<StoredAgentTokenState> {
    if (this.cachedState) return this.cachedState;
    try {
      this.cachedState = validateStoredState(JSON.parse(await readFile(this.path, "utf8")));
      return this.cachedState;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") {
        this.cachedState = emptyState();
        return this.cachedState;
      }
      throw error;
    }
  }

  private async save(state: StoredAgentTokenState): Promise<void> {
    await writeJsonAtomic(this.path, state, { mode: 0o600 });
    this.cachedState = state;
  }

  private async exclusive<T>(operation: () => Promise<T>): Promise<T> {
    const result = this.mutation.then(operation, operation);
    this.mutation = result.then(() => undefined, () => undefined);
    return result;
  }

  async list(): Promise<AgentTokenSummary[]> {
    return (await this.load()).tokens.map(summaryOf);
  }

  /** Ham token yalnız burada döner; kayıtta yalnız özeti kalır. */
  async create(nameValue: unknown): Promise<{ token: string; record: AgentTokenSummary }> {
    const name = validateAgentTokenName(nameValue);
    return this.exclusive(async () => {
      const state = await this.load();
      if (state.tokens.length >= maxAgentTokens) {
        throw new Error(`En fazla ${maxAgentTokens} ajan token'ı tutulabilir.`);
      }
      // 32 bayt = 256 bit entropi, URL-güvenli base64 ile taşınır (Bearer başlığına birebir girer).
      const token = randomBytes(32).toString("base64url");
      const record: StoredAgentToken = {
        id: randomBytes(9).toString("base64url"),
        name,
        createdAt: this.now().toISOString(),
        lastUsedAt: null,
        tokenHash: hashToken(token)
      };
      await this.save({ ...state, tokens: [...state.tokens, record] });
      return { token, record: summaryOf(record) };
    });
  }

  /**
   * Token doğrulaması. Karşılaştırma `timingSafeEqual` ile yapılır; eşleşen kaydın `lastUsedAt`
   * alanı **kısıtlanarak** güncellenir (varsayılan dakikada bir), çünkü ajan istemcisi saniyede
   * birden çok istek atabilir ve her istekte diske yazmak anlamsız olurdu.
   */
  async verify(token: string | undefined): Promise<AgentTokenSummary | null> {
    if (!token) return null;
    const state = await this.load();
    const hash = hashToken(token);
    const match = state.tokens.find((candidate) => equalHashes(candidate.tokenHash, hash));
    if (!match) return null;
    const now = this.now();
    const previous = match.lastUsedAt ? Date.parse(match.lastUsedAt) : 0;
    if (now.getTime() - previous >= this.lastUsedWriteIntervalMs) {
      const stamp = now.toISOString();
      await this.exclusive(async () => {
        const current = await this.load();
        const stored = current.tokens.find((candidate) => candidate.id === match.id);
        if (!stored) return;
        stored.lastUsedAt = stamp;
        await this.save(current);
      }).catch(() => undefined);
      return { ...summaryOf(match), lastUsedAt: stamp };
    }
    return summaryOf(match);
  }

  /** İptal: silinen token bir sonraki istekte geçersizdir (bellek kopyası da tazelenir). */
  async revoke(id: unknown): Promise<boolean> {
    if (typeof id !== "string" || !id) return false;
    return this.exclusive(async () => {
      const state = await this.load();
      const tokens = state.tokens.filter((token) => token.id !== id);
      if (tokens.length === state.tokens.length) return false;
      await this.save({ ...state, tokens });
      return true;
    });
  }
}
