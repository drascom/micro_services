import assert from "node:assert/strict";
import path from "node:path";
import { pathToFileURL } from "node:url";

const minutesBetween = (left, right) => Math.abs(left.valueOf() - right.valueOf()) / 60_000;

export async function verifyAstronomy(projectRoot) {
  const moduleUrl = pathToFileURL(path.join(projectRoot, "dist", "astronomy.js")).href;
  const {
    celestialSnapshot,
    localDateStamp,
    localNoonForZone,
    lunarIllumination,
    solarDay,
    solarPosition
  } = await import(moduleUrl);
  const automationModuleUrl = pathToFileURL(path.join(projectRoot, "dist", "automation-engine.js")).href;
  const { automationDueTrigger, localMinuteStamp } = await import(automationModuleUrl);

  const londonDate = new Date("2026-08-14T11:00:00.000Z");
  const london = solarDay(londonDate, 51.5074, -0.1278, "Europe/London");
  assert.equal(london.state, "normal");
  assert(london.sunrise && minutesBetween(london.sunrise, new Date("2026-08-14T04:45:00.000Z")) <= 4);
  assert(london.sunset && minutesBetween(london.sunset, new Date("2026-08-14T19:28:00.000Z")) <= 4);
  assert.equal(localNoonForZone(londonDate, "Europe/London").toISOString(), "2026-08-14T11:00:00.000Z");
  assert.equal(localDateStamp(new Date("2026-08-14T23:30:00.000Z"), "Europe/Istanbul"), "2026-08-15");
  const zoneBoundary = new Date("2026-08-14T21:30:00.000Z");
  assert.equal(localMinuteStamp(zoneBoundary, "Europe/London"), "2026-08-14 22:30");
  assert.equal(localMinuteStamp(zoneBoundary, "Europe/Istanbul"), "2026-08-15 00:30");
  const localAutomation = {
    triggers: [{ type: "time", at: "00:30", days: [6] }]
  };
  assert.equal(automationDueTrigger(localAutomation, zoneBoundary, null, "Europe/Istanbul")?.type, "time");
  assert.equal(automationDueTrigger(localAutomation, zoneBoundary, null, "Europe/London"), null);

  const position = solarPosition(londonDate, 51.5074, -0.1278);
  assert(position.altitudeDegrees > 45 && position.altitudeDegrees < 60);
  assert(position.azimuthDegrees > 130 && position.azimuthDegrees < 180);

  assert.equal(solarDay(new Date("2026-06-21T12:00:00Z"), 69.6492, 18.9553, "Europe/Oslo").state, "polar-day");
  assert.equal(solarDay(new Date("2026-12-21T12:00:00Z"), 69.6492, 18.9553, "Europe/Oslo").state, "polar-night");

  const moon = lunarIllumination(londonDate);
  assert(moon.phase >= 0 && moon.phase <= 1);
  assert(moon.illumination >= 0 && moon.illumination <= 1);
  const snapshot = celestialSnapshot(londonDate, {
    latitude: 51.5074,
    longitude: -0.1278,
    timeZone: "Europe/London",
    label: "London"
  });
  assert(Number.isFinite(snapshot.moon.position.altitudeDegrees));
  assert(Number.isFinite(snapshot.moon.position.azimuthDegrees));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const projectRoot = path.resolve(path.dirname(process.argv[1]), "..");
  verifyAstronomy(projectRoot)
    .then(() => console.log("Astronomi dogrulamasi tamam."))
    .catch((error) => {
      console.error("Astronomi dogrulamasi basarisiz:", error.message ?? error);
      process.exitCode = 1;
    });
}
