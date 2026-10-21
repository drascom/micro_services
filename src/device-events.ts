import { mkdir, readFile } from "node:fs/promises";
import { writeJsonAtomic } from "./atomic-file.js";
import { dirname } from "node:path";
import type { DeviceEventView, JsonScalar } from "./types.js";

const maximumEvents = 200;
const maximumSourceLength = 96;
const maximumPropertyLength = 64;

const isScalar = (value: unknown): value is JsonScalar =>
  typeof value === "string" || typeof value === "number" || typeof value === "boolean";

export const validateDeviceEvents = (value: unknown): DeviceEventView[] => {
  if (!Array.isArray(value)) return [];
  const result: DeviceEventView[] = [];
  for (const event of value) {
    if (!event || typeof event !== "object" || Array.isArray(event)) continue;
    const candidate = event as Partial<DeviceEventView>;
    if (
      typeof candidate.sourceName !== "string"
      || candidate.sourceName.length < 1
      || candidate.sourceName.length > maximumSourceLength
      || typeof candidate.property !== "string"
      || !/^[a-z0-9_]+$/i.test(candidate.property)
      || candidate.property.length > maximumPropertyLength
      || !isScalar(candidate.value)
      || typeof candidate.at !== "string"
      || !Number.isFinite(Date.parse(candidate.at))
    ) {
      continue;
    }
    result.push({
      sourceName: candidate.sourceName,
      property: candidate.property,
      value: candidate.value,
      at: new Date(candidate.at).toISOString()
    });
    if (result.length === maximumEvents) break;
  }
  return result;
};

export class DeviceEventsStore {
  private writeQueue: Promise<void> = Promise.resolve();

  constructor(private readonly path: string) {}

  async get(): Promise<DeviceEventView[]> {
    try {
      return validateDeviceEvents(JSON.parse(await readFile(this.path, "utf8")));
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
      throw error;
    }
  }

  save(events: DeviceEventView[]): Promise<void> {
    const snapshot = validateDeviceEvents(events);
    this.writeQueue = this.writeQueue.catch(() => undefined).then(async () => {
      await mkdir(dirname(this.path), { recursive: true, mode: 0o700 });
      await writeJsonAtomic(this.path, snapshot, { mode: 0o600 });
    });
    return this.writeQueue;
  }
}
