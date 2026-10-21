import { readFile } from "node:fs/promises";
import { writeJsonAtomic } from "./atomic-file.js";

export interface InstallationState {
  onboardingComplete: boolean;
  onboardingCompletedAt: string | null;
}

const emptyInstallationState = (): InstallationState => ({
  onboardingComplete: false,
  onboardingCompletedAt: null
});

export const validateInstallationState = (value: unknown): InstallationState => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("Kurulum durumu geçersiz.");
  }
  const candidate = value as Record<string, unknown>;
  if (typeof candidate.onboardingComplete !== "boolean") {
    throw new Error("Kurulum tamamlanma durumu geçersiz.");
  }
  const completedAt = candidate.onboardingCompletedAt;
  if (completedAt !== null && (
    typeof completedAt !== "string"
    || Number.isNaN(Date.parse(completedAt))
  )) {
    throw new Error("Kurulum tamamlanma zamanı geçersiz.");
  }
  return {
    onboardingComplete: candidate.onboardingComplete,
    onboardingCompletedAt: candidate.onboardingComplete ? completedAt : null
  };
};

export class InstallationStateStore {
  constructor(private readonly path: string) {}

  async get(): Promise<InstallationState> {
    try {
      return validateInstallationState(JSON.parse(await readFile(this.path, "utf8")));
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return emptyInstallationState();
      throw error;
    }
  }

  async completeOnboarding(): Promise<InstallationState> {
    const current = await this.get();
    if (current.onboardingComplete) return current;
    const value: InstallationState = {
      onboardingComplete: true,
      onboardingCompletedAt: new Date().toISOString()
    };
    await writeJsonAtomic(this.path, value, { mode: 0o600 });
    return value;
  }
}
