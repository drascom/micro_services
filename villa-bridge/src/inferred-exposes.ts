import type { JsonObject } from "./types.js";

const CONTACT_EXPOSE = {
  type: "binary",
  name: "contact",
  property: "contact",
  access: 1,
  description: "Indicates whether the contact is closed",
  value_on: true,
  value_off: false
};

export function inferFallbackExposes(state: JsonObject | undefined): JsonObject[] {
  if (!state) return [];

  const exposes: JsonObject[] = [];
  if (Object.hasOwn(state, "contact")) exposes.push({ ...CONTACT_EXPOSE });
  return exposes;
}
