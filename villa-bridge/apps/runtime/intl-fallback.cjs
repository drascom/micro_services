"use strict";

// Node.js Mobile (Android) ICU'suz derlenmis: `Intl` global'i hic yok ve cekirdek
// (`dist/`) saat dilimi hesaplarinda `Intl.DateTimeFormat` kullaniyor. Bu dosya yalniz
// `Intl` yoksa devreye girer (Linux/Mac Node'unda hicbir sey yapmaz) ve cekirdegin
// kullandigi dar alt kumeyi saglar: `DateTimeFormat(locale, options)` ile `format`,
// `formatToParts`, `resolvedOptions().timeZone`, `timeZoneName` (ofset). Cihazin yerel
// dilimi Date'in yerel saatinden okunur; baska IANA dilimleri (ör. dunya saati) `tz-rules.cjs`
// tablosundaki POSIX kuralindan (yaz saati dahil) hesaplanir. Tabloda olmayan dilim
// `RangeError` verir (gercek `Intl`'in gecersiz dilim davranisi).

const { execFileSync } = require("node:child_process");
const TZ_RULES = require("./tz-rules.cjs");

const UTC_NAMES = new Set(["UTC", "Etc/UTC", "GMT", "Etc/GMT", "Etc/UCT", "UCT", "Etc/Universal", "Universal", "Zulu", "Etc/Zulu"]);
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function androidTimeZone() {
  if (process.platform !== "android") return null;
  try {
    const value = execFileSync("/system/bin/getprop", ["persist.sys.timezone"], {
      encoding: "utf8",
      timeout: 2000,
      stdio: ["ignore", "pipe", "ignore"]
    }).trim();
    return value || null;
  } catch {
    return null;
  }
}

function detectLocalTimeZone() {
  const fromEnv = typeof process.env.TZ === "string" ? process.env.TZ.replace(/^:/, "").trim() : "";
  if (fromEnv) return fromEnv;
  const fromSystem = androidTimeZone();
  if (fromSystem) {
    // Bionic zaten ayni ozellikten okur; TZ'yi yazmak Date'in yerel saatiyle adin ayni
    // kaynaktan geldigini garanti eder.
    process.env.TZ = fromSystem;
    return fromSystem;
  }
  // Ad bilinmiyorsa yerel saate guvenilmez: tutarli kalmak icin UTC say.
  return "UTC";
}


const ZONE_INDEX = new Map(Object.keys(TZ_RULES).map((name) => [name.toLowerCase(), name]));

function canonicalZone(value) {
  return ZONE_INDEX.get(String(value).toLowerCase()) || null;
}

// "hh[:mm[:ss]]" -> saniye (isaretli).
function parseClock(text) {
  const match = /^([+-])?(\d{1,3})(?::(\d{1,2}))?(?::(\d{1,2}))?$/.exec(text);
  if (!match) return null;
  const seconds = Number(match[2]) * 3600 + Number(match[3] || 0) * 60 + Number(match[4] || 0);
  return match[1] === "-" ? -seconds : seconds;
}

/** POSIX TZ kurali (ör. "AEST-10AEDT,M10.1.0,M4.1.0/3") -> {std, dst, start, end} (saniye, UTC'ye gore). */
function parsePosixRule(rule) {
  const re = /^(<[^>]+>|[A-Za-z]{3,})([+-]?\d{1,2}(?::\d{1,2}){0,2})(?:(<[^>]+>|[A-Za-z]{3,})([+-]?\d{1,2}(?::\d{1,2}){0,2})?(?:,([^,]+),([^,]+))?)?$/;
  const match = re.exec(String(rule || ""));
  if (!match) return null;
  const strip = (name) => (name ? name.replace(/^<|>$/g, "") : null);
  const std = -parseClock(match[2]);
  const parsed = { stdName: strip(match[1]), std, dstName: null, dst: null, start: null, end: null };
  if (!match[3]) return parsed;
  parsed.dstName = strip(match[3]);
  parsed.dst = match[4] ? -parseClock(match[4]) : std + 3600;
  const transition = (text) => {
    const [date, time] = text.split("/");
    const at = time === undefined ? 7200 : parseClock(time);
    let m = /^M(\d{1,2})\.(\d)\.(\d)$/.exec(date);
    if (m) return { kind: "M", month: Number(m[1]), week: Number(m[2]), weekday: Number(m[3]), at };
    m = /^J(\d{1,3})$/.exec(date);
    if (m) return { kind: "J", day: Number(m[1]), at };
    m = /^(\d{1,3})$/.exec(date);
    if (m) return { kind: "N", day: Number(m[1]), at };
    return null;
  };
  parsed.start = match[5] ? transition(match[5]) : { kind: "M", month: 3, week: 2, weekday: 0, at: 7200 };
  parsed.end = match[6] ? transition(match[6]) : { kind: "M", month: 11, week: 1, weekday: 0, at: 7200 };
  if (!parsed.start || !parsed.end) return null;
  return parsed;
}

const isLeap = (year) => (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;

// Gecisin o yildaki yerel duvar saati (UTC gibi sayilan ms).
function transitionLocalMs(year, transition) {
  let dayMs;
  if (transition.kind === "M") {
    const first = Date.UTC(year, transition.month - 1, 1);
    const firstWeekday = new Date(first).getUTCDay();
    let day = 1 + ((transition.weekday - firstWeekday + 7) % 7) + (transition.week - 1) * 7;
    const daysInMonth = new Date(Date.UTC(year, transition.month, 0)).getUTCDate();
    while (day > daysInMonth) day -= 7;
    dayMs = Date.UTC(year, transition.month - 1, day);
  } else if (transition.kind === "J") {
    const ordinal = transition.day + (isLeap(year) && transition.day >= 60 ? 1 : 0);
    dayMs = Date.UTC(year, 0, ordinal);
  } else {
    dayMs = Date.UTC(year, 0, transition.day + 1);
  }
  return dayMs + transition.at * 1000;
}

/** Verilen UTC anindaki ofset (saniye) ve kisaltma. */
function posixOffsetAt(parsed, utcMs) {
  if (parsed.dst === null) return { offset: parsed.std, name: parsed.stdName, dst: false };
  const year = new Date(utcMs + parsed.std * 1000).getUTCFullYear();
  const start = transitionLocalMs(year, parsed.start) - parsed.std * 1000;
  const end = transitionLocalMs(year, parsed.end) - parsed.dst * 1000;
  const inDst = start < end ? utcMs >= start && utcMs < end : !(utcMs >= end && utcMs < start);
  return inDst
    ? { offset: parsed.dst, name: parsed.dstName, dst: true }
    : { offset: parsed.std, name: parsed.stdName, dst: false };
}

const ruleCache = new Map();
function zoneRule(zone) {
  if (!ruleCache.has(zone)) ruleCache.set(zone, parsePosixRule(TZ_RULES[zone]));
  return ruleCache.get(zone);
}

function offsetLabel(seconds, style) {
  if (seconds === 0) return style === "longOffset" ? "GMT+00:00" : "GMT";
  const sign = seconds < 0 ? "-" : "+";
  const total = Math.abs(Math.round(seconds / 60));
  const hours = Math.floor(total / 60);
  const minutes = total % 60;
  if (style === "longOffset") {
    return `GMT${sign}${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
  }
  return `GMT${sign}${hours}${minutes ? `:${String(minutes).padStart(2, "0")}` : ""}`;
}

function createIntlFallback(localTimeZone) {
  const localIsUtc = UTC_NAMES.has(localTimeZone);

  function resolveZone(value) {
    if (value === undefined) return localTimeZone;
    const zone = String(value);
    if (zone === localTimeZone) return zone;
    for (const name of UTC_NAMES) {
      if (name.toLowerCase() === zone.toLowerCase()) return "UTC";
    }
    const canonical = canonicalZone(zone);
    if (canonical) return canonical;
    throw new RangeError(`Invalid time zone specified: ${zone}`);
  }

  const pad = (value, width) => String(value).padStart(width, "0");

  class DateTimeFormat {
    constructor(locale, options) {
      const opts = options && typeof options === "object" ? options : {};
      this._locale = Array.isArray(locale) ? String(locale[0] ?? "en-US") : String(locale ?? "en-US");
      this._timeZone = resolveZone(opts.timeZone);
      this._options = opts;
      const anyField = ["weekday", "year", "month", "day", "hour", "minute", "second"].some(
        (key) => opts[key] !== undefined
      );
      // Alan verilmediyse gercek Intl gibi yalniz tarih (yil/ay/gun) bicimlenir.
      this._fields = anyField ? opts : { year: "numeric", month: "numeric", day: "numeric" };
      // Gercek Intl'de `format` bagli bir getter'dir; `const f = dtf.format; f(date)` calisir.
      this.format = this.format.bind(this);
    }

    _values(input) {
      const date = input === undefined ? new Date() : new Date(typeof input === "number" ? input : input.valueOf());
      if (Number.isNaN(date.getTime())) throw new RangeError("Invalid time value");
      const local = this._timeZone === localTimeZone && !localIsUtc;
      let offset = 0;
      let zoneName = "UTC";
      if (local) {
        offset = -date.getTimezoneOffset() * 60;
        zoneName = null;
      } else if (this._timeZone !== "UTC" && !UTC_NAMES.has(this._timeZone)) {
        const rule = zoneRule(this._timeZone);
        if (!rule) throw new RangeError(`Invalid time zone specified: ${this._timeZone}`);
        const at = posixOffsetAt(rule, date.getTime());
        offset = at.offset;
        zoneName = at.name;
      }
      if (!local && offset !== 0) {
        const shifted = new Date(date.getTime() + offset * 1000);
        return {
          year: shifted.getUTCFullYear(),
          month: shifted.getUTCMonth() + 1,
          day: shifted.getUTCDate(),
          hour: shifted.getUTCHours(),
          minute: shifted.getUTCMinutes(),
          second: shifted.getUTCSeconds(),
          weekday: shifted.getUTCDay(),
          offset,
          zoneName
        };
      }
      const utc = !local;
      return {
        offset,
        zoneName,
        year: utc ? date.getUTCFullYear() : date.getFullYear(),
        month: (utc ? date.getUTCMonth() : date.getMonth()) + 1,
        day: utc ? date.getUTCDate() : date.getDate(),
        hour: utc ? date.getUTCHours() : date.getHours(),
        minute: utc ? date.getUTCMinutes() : date.getMinutes(),
        second: utc ? date.getUTCSeconds() : date.getSeconds(),
        weekday: utc ? date.getUTCDay() : date.getDay()
      };
    }

    formatToParts(input) {
      const v = this._values(input);
      const f = this._fields;
      const parts = [];
      const literal = (value) => {
        if (parts.length > 0) parts.push({ type: "literal", value });
      };
      const num = (value, style) => (style === "2-digit" ? pad(value, 2) : String(value));
      if (f.weekday !== undefined) parts.push({ type: "weekday", value: WEEKDAYS[v.weekday] });
      const ymd = this._locale.toLowerCase() === "en-ca";
      const dateParts = [];
      if (f.year !== undefined) dateParts.push({ type: "year", value: f.year === "2-digit" ? pad(v.year % 100, 2) : String(v.year) });
      if (f.month !== undefined) dateParts.push({ type: "month", value: num(v.month, f.month) });
      if (f.day !== undefined) dateParts.push({ type: "day", value: num(v.day, f.day) });
      if (ymd) {
        // en-CA: 2026-10-20
        for (const part of dateParts) {
          if (part !== dateParts[0]) parts.push({ type: "literal", value: "-" });
          else literal(", ");
          parts.push(part);
        }
      } else {
        // en-US: 10/20/2026, en-GB: 20/10/2026
        const order = this._locale.toLowerCase() === "en-us" || this._locale === "en"
          ? ["month", "day", "year"]
          : ["day", "month", "year"];
        const sorted = order.map((type) => dateParts.find((part) => part.type === type)).filter(Boolean);
        sorted.forEach((part, index) => {
          if (index > 0) parts.push({ type: "literal", value: "/" });
          else literal(", ");
          parts.push(part);
        });
      }
      const timeParts = [];
      if (f.hour !== undefined) timeParts.push({ type: "hour", value: num(v.hour, f.hour) });
      if (f.minute !== undefined) timeParts.push({ type: "minute", value: pad(v.minute, 2) });
      if (f.second !== undefined) timeParts.push({ type: "second", value: pad(v.second, 2) });
      timeParts.forEach((part, index) => {
        if (index > 0) parts.push({ type: "literal", value: ":" });
        else literal(", ");
        parts.push(part);
      });
      if (this._options.timeZoneName !== undefined) {
        const style = this._options.timeZoneName;
        const named = style === "short" && v.zoneName && /^[A-Za-z]+$/.test(v.zoneName);
        literal(", ");
        parts.push({ type: "timeZoneName", value: named ? v.zoneName : offsetLabel(v.offset, style) });
      }
      return parts;
    }

    format(input) {
      return this.formatToParts(input).map((part) => part.value).join("");
    }

    resolvedOptions() {
      return {
        locale: this._locale,
        calendar: "gregory",
        numberingSystem: "latn",
        timeZone: this._timeZone,
        hourCycle: "h23"
      };
    }
  }

  const callable = function DateTimeFormatFallback(locale, options) {
    return new DateTimeFormat(locale, options);
  };
  callable.prototype = DateTimeFormat.prototype;
  callable.supportedLocalesOf = (locales) => (Array.isArray(locales) ? locales : locales ? [locales] : []);

  return { DateTimeFormat: callable };
}

function installIntlFallback() {
  if (typeof globalThis.Intl !== "undefined") return false;
  Object.defineProperty(globalThis, "Intl", {
    value: createIntlFallback(detectLocalTimeZone()),
    writable: true,
    configurable: true,
    enumerable: false
  });
  return true;
}

// Tanilama icin: cekirdegin kullandigi bicimle o anki yerel saati uretir (cihazda dogrulama).
function intlSelfCheck() {
  try {
    const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const sample = new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23"
    }).format(new Date());
    return { ok: true, timeZone, sample, error: null };
  } catch (error) {
    return { ok: false, timeZone: null, sample: null, error: error.message };
  }
}

module.exports = { createIntlFallback, installIntlFallback, intlSelfCheck, parsePosixRule, posixOffsetAt };
