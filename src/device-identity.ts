import { resolveDeviceImage, type DeviceImagePreferences } from "./device-images.js";

export function canonicalDeviceModel(
  reportedModel: string | null | undefined,
  manufacturer: string | null | undefined
): string | null {
  return resolveDeviceImage("identity", reportedModel, manufacturer, { devices: {}, models: {} }).model;
}

export function deviceIdentity(
  deviceId: string,
  reportedModel: string | null | undefined,
  manufacturer: string | null | undefined,
  preferences: DeviceImagePreferences
) {
  return resolveDeviceImage(deviceId, reportedModel, manufacturer, preferences);
}
