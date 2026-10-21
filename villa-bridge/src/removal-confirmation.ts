export function isDeviceRemovalConfirmation(value: unknown): boolean {
  return typeof value === "string" && ["yes", "evet"].includes(value.trim());
}
