import { readFile } from "node:fs/promises";
import { writeJsonAtomic } from "./atomic-file.js";
import type { DeviceImageCandidate, DeviceImageView } from "./types.js";

export interface DeviceImagePreferences {
  devices: Record<string, string | null>;
  models: Record<string, string | null>;
}

const emptyPreferences = (): DeviceImagePreferences => ({ devices: {}, models: {} });
const own = (value: object, key: string): boolean => Object.prototype.hasOwnProperty.call(value, key);

const genericCandidates = new Map<string, DeviceImageCandidate[]>([
  ["TS0001", [
    { model: "WHD02", label: "miniModule" },
    { model: "TS0001_switch_module_1", label: "switchModule" },
    { model: "TS0001_switch_1_gang", label: "wallSwitch" },
    { model: "TS0001", label: "otherSwitch" }
  ]],
  ["TS0002", [
    { model: "TS0002_basic", label: "switchModule" },
    { model: "TS0002", label: "wallSwitch" },
    { model: "TS0002_power", label: "otherSwitch" }
  ]],
  ["TS0003", [
    { model: "TS0003_switch_module_1", label: "switchModule" },
    { model: "TS0003", label: "wallSwitch" },
    { model: "TS0003_switch_3_gang", label: "otherSwitch" }
  ]],
  ["TS0011", [
    { model: "TS0011_switch_module", label: "switchModule" },
    { model: "TS0011", label: "wallSwitch" }
  ]],
  ["TS0012", [
    { model: "TS0012_switch_module", label: "switchModule" },
    { model: "TS0012", label: "wallSwitch" }
  ]],
  ["TS0013", [
    { model: "TS0013_switch_module", label: "switchModule" },
    { model: "TS0013", label: "wallSwitch" }
  ]]
]);

const canonicalModels = new Map<string, string>([
  ["TS0001::_TZ3000_i9oy2rdq", "WHD02"]
]);

function validateMap(value: unknown): Record<string, string | null> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return {};
  const result: Record<string, string | null> = {};
  for (const [key, selected] of Object.entries(value)) {
    if (typeof selected === "string" || selected === null) result[key] = selected;
  }
  return result;
}

export function validateDeviceImagePreferences(value: unknown): DeviceImagePreferences {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return emptyPreferences();
  const candidate = value as Record<string, unknown>;
  return {
    devices: validateMap(candidate.devices),
    models: validateMap(candidate.models)
  };
}

export function deviceImageKey(
  reportedModel: string | null | undefined,
  manufacturer: string | null | undefined
): string {
  return `${reportedModel?.trim() || "unknown"}::${manufacturer?.trim() || "unknown"}`;
}

export function resolveDeviceImage(
  deviceId: string,
  reportedModel: string | null | undefined,
  manufacturer: string | null | undefined,
  preferences: DeviceImagePreferences
): DeviceImageView {
  const model = reportedModel?.trim() || null;
  const key = deviceImageKey(model, manufacturer);
  const canonicalModel = model ? canonicalModels.get(key) ?? model : null;
  const candidates = model
    ? genericCandidates.get(model)?.map((candidate) => ({ ...candidate }))
      ?? [{ model: canonicalModel as string, label: "catalogMatch" }]
    : [];
  if (canonicalModel && !candidates.some((candidate) => candidate.model === canonicalModel)) {
    candidates.unshift({ model: canonicalModel, label: "catalogMatch" });
  }
  const normalizedId = deviceId.toLowerCase();
  const deviceSelected = own(preferences.devices, normalizedId);
  const modelSelected = own(preferences.models, key);
  const selected = deviceSelected
    ? preferences.devices[normalizedId]
    : modelSelected
      ? preferences.models[key]
      : canonicalModel;
  return {
    model: selected ?? null,
    // Burada ağ/önbellek bilgisi yok: doldurmak HTTP katmanının işi (bkz. `visibleDevices`).
    available: null,
    candidates,
    selectionRequired: candidates.length > 1 && !canonicalModels.has(key) && !deviceSelected && !modelSelected,
    userSelected: deviceSelected || modelSelected,
    preferenceKey: key
  };
}

export class DeviceImagesStore {
  constructor(private readonly path: string) {}

  async get(): Promise<DeviceImagePreferences> {
    try {
      return validateDeviceImagePreferences(JSON.parse(await readFile(this.path, "utf8")));
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return emptyPreferences();
      throw error;
    }
  }

  async save(preferences: DeviceImagePreferences): Promise<DeviceImagePreferences> {
    const validated = validateDeviceImagePreferences(preferences);
    await writeJsonAtomic(this.path, validated, { mode: 0o600 });
    return validated;
  }

  async select(
    preferences: DeviceImagePreferences,
    deviceId: string,
    preferenceKey: string,
    imageModel: string | null,
    applyToModel: boolean
  ): Promise<DeviceImagePreferences> {
    const next = validateDeviceImagePreferences(preferences);
    next.devices[deviceId.toLowerCase()] = imageModel;
    if (applyToModel) next.models[preferenceKey] = imageModel;
    return this.save(next);
  }

  async removeDevice(
    preferences: DeviceImagePreferences,
    deviceId: string
  ): Promise<DeviceImagePreferences> {
    const next = validateDeviceImagePreferences(preferences);
    delete next.devices[deviceId.toLowerCase()];
    return this.save(next);
  }
}
