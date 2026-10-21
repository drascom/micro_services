import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { delimiter, resolve } from "node:path";

const repositoryRoot = resolve(import.meta.dirname, "..");
const androidRoot = resolve(repositoryRoot, "apps/android");
const isWindows = process.platform === "win32";

function firstExisting(candidates) {
  return candidates.find((candidate) => candidate && existsSync(candidate));
}

const javaHome = firstExisting([
  process.env.JAVA_HOME,
  "/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home",
  "/usr/local/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home",
  "/Applications/Android Studio.app/Contents/jbr/Contents/Home"
]);

if (!javaHome) {
  console.error("JDK 17 not found. Set JAVA_HOME or install OpenJDK 17.");
  process.exit(1);
}

const gradleWrapper = resolve(androidRoot, isWindows ? "gradlew.bat" : "gradlew");
const javaBin = resolve(javaHome, "bin");
const result = spawnSync(
  gradleWrapper,
  ["-p", androidRoot, "assembleDebug", ...process.argv.slice(2)],
  {
    cwd: repositoryRoot,
    env: {
      ...process.env,
      JAVA_HOME: javaHome,
      PATH: `${javaBin}${delimiter}${process.env.PATH ?? ""}`
    },
    stdio: "inherit"
  }
);

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}
process.exit(result.status ?? 1);
