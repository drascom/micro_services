export interface RecentErrorEntry {
  id: number;
  timestamp: string;
  operation: string;
  statusCode: number;
  message: string;
}

export interface RecentErrorInput {
  operation: string;
  statusCode: number;
  message: unknown;
}

const MAX_MESSAGE_LENGTH = 500;

export const sanitizeErrorMessage = (value: unknown): string => {
  const raw = value instanceof Error ? value.message : String(value ?? "Unknown error");
  const sanitized = raw
    .replace(/\bBearer\s+\S+/gi, "Bearer [redacted]")
    .replace(
      /\b([a-z][a-z0-9+.-]*:\/\/)([^@\s/:]+):([^@\s]+)@/gi,
      "$1[redacted]@"
    )
    .replace(
      /\b(authorization|password|passwd|token|secret|api[_-]?key)\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;]+)/gi,
      "$1=[redacted]"
    )
    .replace(
      /([?&](?:authorization|password|passwd|token|secret|api[_-]?key)=)[^&\s]*/gi,
      "$1[redacted]"
    )
    .replace(/\s+/g, " ")
    .trim();
  return (sanitized || "Unknown error").slice(0, MAX_MESSAGE_LENGTH);
};

export class RecentErrorLog {
  private readonly entries: RecentErrorEntry[] = [];
  private nextId = 1;

  constructor(
    private readonly capacity = 50,
    private enabled = true,
    private readonly now: () => Date = () => new Date()
  ) {
    if (!Number.isInteger(capacity) || capacity < 1 || capacity > 500) {
      throw new Error("Recent error log capacity must be between 1 and 500.");
    }
  }

  isEnabled(): boolean {
    return this.enabled;
  }

  setEnabled(enabled: boolean): void {
    this.enabled = enabled;
    if (!enabled) this.clear();
  }

  record(input: RecentErrorInput): RecentErrorEntry | null {
    if (!this.enabled) return null;
    const entry: RecentErrorEntry = {
      id: this.nextId++,
      timestamp: this.now().toISOString(),
      operation: input.operation.slice(0, 120),
      statusCode: Number.isInteger(input.statusCode) ? input.statusCode : 500,
      message: sanitizeErrorMessage(input.message)
    };
    this.entries.push(entry);
    if (this.entries.length > this.capacity) {
      this.entries.splice(0, this.entries.length - this.capacity);
    }
    return { ...entry };
  }

  list(): RecentErrorEntry[] {
    if (!this.enabled) return [];
    return this.entries.map((entry) => ({ ...entry })).reverse();
  }

  clear(): void {
    this.entries.length = 0;
  }
}
