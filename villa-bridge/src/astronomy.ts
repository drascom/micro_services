/**
 * Yerel güneş ve ay hesabı.
 *
 * Formüller SunCalc'tan (Vladimir Agafonkin, MIT) uyarlanmıştır. Çalışma zamanı bağımlılığı
 * yoktur; bütün çıktılar tarih, koordinat ve IANA saat diliminden üretilir.
 */

import type { HomeLocation } from "./location.js";

const rad = Math.PI / 180;
const dayMs = 86_400_000;
const j1970 = 2440588;
const j2000 = 2451545;
const obliquity = rad * 23.4397;
const sunriseAltitude = -0.833 * rad;
const j0 = 0.0009;

const toJulian = (date: Date): number => date.valueOf() / dayMs - 0.5 + j1970;
const fromJulian = (julian: number): Date => new Date((julian + 0.5 - j1970) * dayMs);
const toDays = (date: Date): number => toJulian(date) - j2000;
const rightAscension = (longitude: number, latitude: number): number =>
  Math.atan2(
    Math.sin(longitude) * Math.cos(obliquity) - Math.tan(latitude) * Math.sin(obliquity),
    Math.cos(longitude)
  );
const declination = (longitude: number, latitude = 0): number =>
  Math.asin(
    Math.sin(latitude) * Math.cos(obliquity)
    + Math.cos(latitude) * Math.sin(obliquity) * Math.sin(longitude)
  );
const azimuth = (hourAngle: number, latitude: number, declinationValue: number): number =>
  Math.atan2(
    Math.sin(hourAngle),
    Math.cos(hourAngle) * Math.sin(latitude) - Math.tan(declinationValue) * Math.cos(latitude)
  );
const altitude = (hourAngle: number, latitude: number, declinationValue: number): number =>
  Math.asin(
    Math.sin(latitude) * Math.sin(declinationValue)
    + Math.cos(latitude) * Math.cos(declinationValue) * Math.cos(hourAngle)
  );
const siderealTime = (days: number, westLongitude: number): number =>
  rad * (280.16 + 360.9856235 * days) - westLongitude;
const solarMeanAnomaly = (days: number): number => rad * (357.5291 + 0.98560028 * days);
const eclipticLongitude = (meanAnomaly: number): number => {
  const center = rad * (
    1.9148 * Math.sin(meanAnomaly)
    + 0.02 * Math.sin(2 * meanAnomaly)
    + 0.0003 * Math.sin(3 * meanAnomaly)
  );
  return meanAnomaly + center + rad * 102.9372 + Math.PI;
};
const sunCoordinates = (days: number): { declination: number; rightAscension: number } => {
  const longitude = eclipticLongitude(solarMeanAnomaly(days));
  return { declination: declination(longitude), rightAscension: rightAscension(longitude, 0) };
};
const julianCycle = (days: number, westLongitude: number): number =>
  Math.round(days - j0 - westLongitude / (2 * Math.PI));
const approximateTransit = (hourAngleValue: number, westLongitude: number, cycle: number): number =>
  j0 + (hourAngleValue + westLongitude) / (2 * Math.PI) + cycle;
const solarTransit = (approximate: number, meanAnomaly: number, longitude: number): number =>
  j2000 + approximate + 0.0053 * Math.sin(meanAnomaly) - 0.0069 * Math.sin(2 * longitude);
const hourAngle = (height: number, latitude: number, declinationValue: number): number =>
  Math.acos(
    (Math.sin(height) - Math.sin(latitude) * Math.sin(declinationValue))
    / (Math.cos(latitude) * Math.cos(declinationValue))
  );

export interface HorizontalPosition {
  altitudeDegrees: number;
  /** Kuzey 0°, doğu 90°, güney 180°, batı 270°. */
  azimuthDegrees: number;
}

export interface SolarDay {
  sunrise: Date | null;
  solarNoon: Date;
  sunset: Date | null;
  state: "normal" | "polar-day" | "polar-night";
}

export interface LunarState {
  phase: number;
  illumination: number;
  position: HorizontalPosition;
}

export const isValidTimeZone = (value: unknown): value is string => {
  if (typeof value !== "string" || value.length < 1 || value.length > 100) return false;
  try {
    new Intl.DateTimeFormat("en", { timeZone: value }).format(0);
    return true;
  } catch {
    return false;
  }
};

const zonedParts = (date: Date, timeZone: string): Record<string, number> => {
  const result: Record<string, number> = {};
  for (const part of new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23"
  }).formatToParts(date)) {
    if (part.type !== "literal") result[part.type] = Number(part.value);
  }
  return result;
};

const zonedDate = (
  year: number,
  month: number,
  day: number,
  hour: number,
  timeZone: string
): Date => {
  const target = Date.UTC(year, month - 1, day, hour, 0, 0);
  let guess = target;
  for (let iteration = 0; iteration < 3; iteration += 1) {
    const parts = zonedParts(new Date(guess), timeZone);
    const observed = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute, parts.second);
    guess += target - observed;
  }
  return new Date(guess);
};

export const localDateStamp = (at: Date, timeZone: string): string => {
  const parts = zonedParts(at, timeZone);
  return `${String(parts.year).padStart(4, "0")}-${String(parts.month).padStart(2, "0")}-${String(parts.day).padStart(2, "0")}`;
};

export const localNoonForZone = (at: Date, timeZone: string): Date => {
  const parts = zonedParts(at, timeZone);
  return zonedDate(parts.year, parts.month, parts.day, 12, timeZone);
};

export const solarPosition = (at: Date, latitude: number, longitude: number): HorizontalPosition => {
  const westLongitude = rad * -longitude;
  const latitudeRad = rad * latitude;
  const days = toDays(at);
  const coordinates = sunCoordinates(days);
  const hour = siderealTime(days, westLongitude) - coordinates.rightAscension;
  const angle = azimuth(hour, latitudeRad, coordinates.declination) + Math.PI;
  return {
    altitudeDegrees: altitude(hour, latitudeRad, coordinates.declination) / rad,
    azimuthDegrees: ((angle / rad) + 360) % 360
  };
};

export const solarDay = (
  at: Date,
  latitude: number,
  longitude: number,
  timeZone: string
): SolarDay => {
  const reference = localNoonForZone(at, timeZone);
  const westLongitude = rad * -longitude;
  const latitudeRad = rad * latitude;
  const days = toDays(reference);
  const cycle = julianCycle(days, westLongitude);
  const approximate = approximateTransit(0, westLongitude, cycle);
  const meanAnomaly = solarMeanAnomaly(approximate);
  const longitudeValue = eclipticLongitude(meanAnomaly);
  const declinationValue = declination(longitudeValue);
  const noon = solarTransit(approximate, meanAnomaly, longitudeValue);
  const solarNoon = fromJulian(noon);
  const angle = hourAngle(sunriseAltitude, latitudeRad, declinationValue);
  if (!Number.isFinite(angle)) {
    const state = solarPosition(solarNoon, latitude, longitude).altitudeDegrees > 0
      ? "polar-day" as const
      : "polar-night" as const;
    return { sunrise: null, solarNoon, sunset: null, state };
  }
  const setJulian = solarTransit(
    approximateTransit(angle, westLongitude, cycle),
    meanAnomaly,
    longitudeValue
  );
  return {
    sunrise: fromJulian(noon - (setJulian - noon)),
    solarNoon,
    sunset: fromJulian(setJulian),
    state: "normal"
  };
};

const moonCoordinates = (days: number): {
  distance: number;
  declination: number;
  rightAscension: number;
} => {
  const longitude = rad * (218.316 + 13.176396 * days);
  const meanAnomaly = rad * (134.963 + 13.064993 * days);
  const distanceArgument = rad * (93.272 + 13.22935 * days);
  const eclipticLongitudeValue = longitude + rad * 6.289 * Math.sin(meanAnomaly);
  const eclipticLatitude = rad * 5.128 * Math.sin(distanceArgument);
  return {
    rightAscension: rightAscension(eclipticLongitudeValue, eclipticLatitude),
    declination: declination(eclipticLongitudeValue, eclipticLatitude),
    distance: 385001 - 20905 * Math.cos(meanAnomaly)
  };
};

export const lunarPosition = (at: Date, latitude: number, longitude: number): HorizontalPosition => {
  const westLongitude = rad * -longitude;
  const latitudeRad = rad * latitude;
  const days = toDays(at);
  const coordinates = moonCoordinates(days);
  const hour = siderealTime(days, westLongitude) - coordinates.rightAscension;
  let height = altitude(hour, latitudeRad, coordinates.declination);
  height += rad * 0.017 / Math.tan(height + rad * 10.26 / (height / rad + 5.10));
  const angle = azimuth(hour, latitudeRad, coordinates.declination) + Math.PI;
  return {
    altitudeDegrees: height / rad,
    azimuthDegrees: ((angle / rad) + 360) % 360
  };
};

export const lunarIllumination = (at: Date): Omit<LunarState, "position"> => {
  const days = toDays(at);
  const sun = sunCoordinates(days);
  const moon = moonCoordinates(days);
  const sunDistance = 149598000;
  const phi = Math.acos(
    Math.sin(sun.declination) * Math.sin(moon.declination)
    + Math.cos(sun.declination) * Math.cos(moon.declination)
    * Math.cos(sun.rightAscension - moon.rightAscension)
  );
  const incidence = Math.atan2(
    sunDistance * Math.sin(phi),
    moon.distance - sunDistance * Math.cos(phi)
  );
  const angle = Math.atan2(
    Math.cos(sun.declination) * Math.sin(sun.rightAscension - moon.rightAscension),
    Math.sin(sun.declination) * Math.cos(moon.declination)
    - Math.cos(sun.declination) * Math.sin(moon.declination)
    * Math.cos(sun.rightAscension - moon.rightAscension)
  );
  return {
    illumination: (1 + Math.cos(incidence)) / 2,
    phase: 0.5 + 0.5 * incidence * (angle < 0 ? -1 : 1) / Math.PI
  };
};

export const celestialSnapshot = (at: Date, location: HomeLocation): {
  day: SolarDay;
  sun: HorizontalPosition;
  moon: LunarState;
} => {
  const illumination = lunarIllumination(at);
  return {
    day: solarDay(at, location.latitude, location.longitude, location.timeZone),
    sun: solarPosition(at, location.latitude, location.longitude),
    moon: {
      ...illumination,
      position: lunarPosition(at, location.latitude, location.longitude)
    }
  };
};
