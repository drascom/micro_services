import { readFile } from "node:fs/promises";
import { writeJsonAtomic } from "./atomic-file.js";

export type DeviceNotes = Record<string, string>;

export function validateDeviceNote(value: unknown): string {
  if (typeof value !== "string") throw new Error("Cihaz notu geçersiz.");
  const note = value.trim();
  if (note.length > 500) throw new Error("Cihaz notu en fazla 500 karakter olabilir.");
  return note;
}

export class DeviceNotesStore {
  constructor(private readonly path: string) {}

  private async all(): Promise<DeviceNotes> {
    try {
      const parsed = JSON.parse(await readFile(this.path, "utf8")) as unknown;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
      return Object.fromEntries(
        Object.entries(parsed)
          .filter(([id, note]) => /^0x[0-9a-f]{16}$/i.test(id) && typeof note === "string")
          .map(([id, note]) => [id.toLowerCase(), validateDeviceNote(note)])
      );
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return {};
      throw error;
    }
  }

  async get(deviceId: string): Promise<string> {
    return (await this.all())[deviceId.toLowerCase()] ?? "";
  }

  async set(deviceId: string, value: unknown): Promise<string> {
    const id = deviceId.toLowerCase();
    if (!/^0x[0-9a-f]{16}$/.test(id)) throw new Error("Cihaz kimliği geçersiz.");
    const note = validateDeviceNote(value);
    const notes = await this.all();
    if (note) notes[id] = note;
    else delete notes[id];
    await writeJsonAtomic(this.path, notes, { mode: 0o600 });
    return note;
  }

  async removeDevice(deviceId: string): Promise<void> {
    await this.set(deviceId, "");
  }
}
