import { localNoonForZone, solarDay } from "./astronomy.js";

/**
 * Gün doğumu / gün batımı hesabı.
 *
 * Geriye uyumluluk cephesidir. Hesap `astronomy.ts` içindeki ortak, bağımlılıksız motora gider;
 * otomasyonun eski `SunTimes` sözleşmesi korunur.
 */

export interface SunTimes {
  /** Kutup gününde/gecesinde `null` — güneş o gün ufku kesmiyor. */
  sunrise: Date | null;
  sunset: Date | null;
}

export const isValidLatitude = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value >= -90 && value <= 90;

export const isValidLongitude = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value >= -180 && value <= 180;

/**
 * Verilen ana en yakın gün doğumu ve batımı. Çağıran genelde ilgilendiği günün yerel öğlenini
 * verir; hesap o günün güneş geçişini bulur.
 */
export const sunTimes = (
  date: Date,
  latitude: number,
  longitude: number,
  timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
): SunTimes => {
  if (!isValidLatitude(latitude) || !isValidLongitude(longitude) || Number.isNaN(date.valueOf())) {
    return { sunrise: null, sunset: null };
  }
  const day = solarDay(date, latitude, longitude, timeZone);
  return { sunrise: day.sunrise, sunset: day.sunset };
};

/** Yerel günün öğleni — güneş hesabına verilecek referans an. */
export const localNoon = (date: Date): Date =>
  localNoonForZone(date, Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
