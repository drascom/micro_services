import {
  localDateStamp,
  lunarIllumination,
  lunarPosition,
  solarDay,
  solarPosition,
  type HorizontalPosition,
  type SolarDay
} from "./astronomy.js";
import type { HomeLocation } from "./location.js";

export interface ClockSource {
  now(): Date;
}

export class SystemClock implements ClockSource {
  now(): Date {
    return new Date();
  }
}

export interface CelestialContext {
  at: string;
  localDate: string;
  localTime: string;
  timeZone: string;
  location: HomeLocation | null;
  sun: {
    position: HorizontalPosition | null;
    sunrise: string | null;
    solarNoon: string | null;
    sunset: string | null;
    state: SolarDay["state"] | "location-missing";
  };
  moon: {
    position: HorizontalPosition | null;
    phase: number;
    illumination: number;
    aboveHorizon: boolean;
  };
}

export class CelestialService {
  private dayCache: { key: string; day: SolarDay } | null = null;

  constructor(
    private readonly location: () => HomeLocation | null,
    private readonly clock: ClockSource = new SystemClock()
  ) {}

  invalidate(): void {
    this.dayCache = null;
  }

  snapshot(requestedAt?: Date): CelestialContext {
    const at = requestedAt ?? this.clock.now();
    const location = this.location();
    const fallbackTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    const timeZone = location?.timeZone ?? fallbackTimeZone;
    const localDate = new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit"
    }).format(at);
    const localTime = new Intl.DateTimeFormat("en-GB", {
      timeZone,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23"
    }).format(at);
    if (!location) {
      return {
        at: at.toISOString(),
        localDate,
        localTime,
        timeZone,
        location: null,
        sun: { position: null, sunrise: null, solarNoon: null, sunset: null, state: "location-missing" },
        moon: { position: null, phase: 0, illumination: 0, aboveHorizon: false }
      };
    }
    const key = `${localDateStamp(at, timeZone)}:${location.latitude}:${location.longitude}:${timeZone}`;
    if (!this.dayCache || this.dayCache.key !== key) {
      this.dayCache = {
        key,
        day: solarDay(at, location.latitude, location.longitude, timeZone)
      };
    }
    const day = this.dayCache.day;
    const sunPosition = solarPosition(at, location.latitude, location.longitude);
    const moonPosition = lunarPosition(at, location.latitude, location.longitude);
    const moonIllumination = lunarIllumination(at);
    return {
      at: at.toISOString(),
      localDate,
      localTime,
      timeZone,
      location,
      sun: {
        position: sunPosition,
        sunrise: day.sunrise?.toISOString() ?? null,
        solarNoon: day.solarNoon.toISOString(),
        sunset: day.sunset?.toISOString() ?? null,
        state: day.state
      },
      moon: {
        position: moonPosition,
        phase: moonIllumination.phase,
        illumination: moonIllumination.illumination,
        aboveHorizon: moonPosition.altitudeDegrees > 0
      }
    };
  }
}
