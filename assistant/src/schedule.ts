/**
 * Deterministic schedule parser for assistant tasks (Turkish + English), local time zone.
 * The model passes the user's words in `schedule`; this decides. An error text tells the model what to fix.
 *   once      "in 30m", "30 dakika sonra", "yarın 09:00", "tomorrow 9am", "saat 15:00", ISO "2026-10-20 09:00"
 *   interval  "every 2h", "her 2 saatte bir", "30 dakikada bir", "hourly"   (min 15 minutes)
 *   cron      "her gün 09:00", "hafta içi 9'da", "her pazartesi", "weekdays at 9am", "ayın 1'i", "0 9 * * 1-5"
 */
export type Schedule = { kind: 'once'; at: number } | { kind: 'interval'; every: number } | { kind: 'cron'; expr: string };
export class ScheduleError extends Error {}

const MINUTE = 60_000, HOUR = 60 * MINUTE, DAY = 24 * HOUR;
export const MIN_INTERVAL = 15 * MINUTE;
const EXAMPLES = "Örnek / examples: 'her gün 09:00', 'hafta içi 9:00', 'her pazartesi 08:30', 'ayın 1'i 10:00', 'every 2h', 'in 30m', '2026-10-20 09:00', '0 9 * * 1-5'.";
const fail = (text: string): never => { throw new ScheduleError(text + ' ' + EXAMPLES); };
const pad = (n: number) => String(n).padStart(2, '0');

const DAYS: [RegExp, number][] = [
  [/(?<!\p{L})(pazartesi|monday|mon)(?!\p{L})/u, 1], [/(?<!\p{L})(salı|sali|tuesday|tue|tues)(?!\p{L})/u, 2],
  [/(?<!\p{L})(çarşamba|carsamba|wednesday|wed)(?!\p{L})/u, 3], [/(?<!\p{L})(perşembe|persembe|thursday|thu|thurs)(?!\p{L})/u, 4],
  [/(?<!\p{L})(cuma|friday|fri)(?!\p{L})/u, 5], [/(?<!\p{L})(cumartesi|saturday|sat)(?!\p{L})/u, 6], [/(?<!\p{L})(pazar|sunday|sun)(?!\p{L})/u, 0]];
// Turkish plural/possessive suffixes ("pazartesileri", "cuma günleri") and English plurals ("mondays") mean every week.
const DAY_WORDS = /(?<!\p{L})(pazartesi|salı|sali|çarşamba|carsamba|perşembe|persembe|cuma|cumartesi|pazar|monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thurs|fri|sat|sun)(leri|ları|lari|s)?(?!\p{L})/gu;
const TR_DAYS = ['pazar', 'pazartesi', 'salı', 'çarşamba', 'perşembe', 'cuma', 'cumartesi'];
const TR_SHORT = ['Paz', 'Pzt', 'Sal', 'Çar', 'Per', 'Cum', 'Cmt'];
const TR_MONTHS = ['Oca', 'Şub', 'Mar', 'Nis', 'May', 'Haz', 'Tem', 'Ağu', 'Eyl', 'Eki', 'Kas', 'Ara'];

function normalize(text: string): string {
  return text.replace(/İ/g, 'i').replace(/I/g, 'ı').toLowerCase().replace(/[’`´]/g, "'").replace(/\s+/g, ' ').trim()
    // English words written with a capital I ("In 30m", "Daily") keep their i.
    .replace(/(?<!\p{L})ın(?= \d)/gu, 'in');
}

/** Clock time in the text, if any ("09:00", "9.30", "9'da", "saat 9", "9am", "at 21"). Day-part words shift to pm. */
export function clockTime(text: string): { h: number; m: number } | undefined {
  let h: number | undefined, m = 0, ampm = '';
  const patterns: [RegExp, (r: RegExpExecArray) => void][] = [
    [/(?<!\d)(\d{1,2})[:.](\d{2})\s*(am|pm)?(?!\d)/, r => { h = +r[1]; m = +r[2]; ampm = r[3] ?? ''; }],
    [/(?<!\d)(\d{1,2})\s*(am|pm)(?!\p{L})/u, r => { h = +r[1]; ampm = r[2]; }],
    [/(?<!\p{L})saat (\d{1,2})(?!\d)/u, r => { h = +r[1]; }],
    [/(?<![\d.:])(\d{1,2})'?(da|de|ta|te)(?!\p{L})/u, r => { h = +r[1]; }],
    [/(?<!\p{L})at (\d{1,2})(?![\d:.])/u, r => { h = +r[1]; }]];
  for (const [pattern, take] of patterns) { const r = pattern.exec(text); if (r) { take(r); break; } }
  if (h === undefined) return undefined;
  if (ampm === 'pm' && h < 12) h += 12;
  if (ampm === 'am' && h === 12) h = 0;
  if (!ampm && h < 12 && /(?<!\p{L})(akşam|aksam|öğleden sonra|ogleden sonra|gece|evening|afternoon|night|tonight)/u.test(text) && !(h <= 4 && /gece|night/.test(text))) h += 12;
  if (h > 23 || m > 59) fail('Geçersiz saat / invalid time.');
  return { h, m };
}

/** Default time when a recurring rule names none: morning 09:00, evening 20:00, night 22:00. */
function defaultTime(text: string): { h: number; m: number } {
  if (/(?<!\p{L})(akşam|aksam|evening)/u.test(text)) return { h: 20, m: 0 };
  if (/(?<!\p{L})(gece|night)/u.test(text)) return { h: 22, m: 0 };
  return { h: 9, m: 0 };
}

const UNITS: [RegExp, number][] = [[/^(m|min|mins|minute|minutes|dk|dakika)$/, MINUTE], [/^(h|hr|hrs|hour|hours|saat)$/, HOUR], [/^(d|day|days|gün|gun)$/, DAY]];
const unit = (word: string) => UNITS.find(([pattern]) => pattern.test(word))?.[1];

function parseCronField(field: string, min: number, max: number): Set<number> {
  const values = new Set<number>();
  for (const part of field.split(',')) {
    const r = /^(\*|(\d+)(?:-(\d+))?)(?:\/(\d+))?$/.exec(part);
    if (!r) fail(`Geçersiz cron alanı / invalid cron field: ${field}.`);
    const step = r![4] ? +r![4] : 1;
    let from = r![1] === '*' ? min : +r![2], to = r![1] === '*' ? max : r![3] !== undefined ? +r![3] : r![4] ? max : from;
    if (max === 7 && to === 7) to = 7; // 7 = Sunday as well
    if (from < min || to > max || from > to || step < 1) fail(`Geçersiz cron alanı / invalid cron field: ${field}.`);
    for (let v = from; v <= to; v += step) values.add(max === 7 && v === 7 ? 0 : v);
  }
  return values;
}
type Cron = { minute: Set<number>; hour: Set<number>; dom: Set<number>; month: Set<number>; dow: Set<number>; domAll: boolean; dowAll: boolean };
export function parseCron(expr: string): Cron {
  const f = expr.trim().split(/\s+/);
  if (f.length !== 5) fail('Cron 5 alan olmalı / cron needs 5 fields.');
  return { minute: parseCronField(f[0], 0, 59), hour: parseCronField(f[1], 0, 23), dom: parseCronField(f[2], 1, 31),
    month: parseCronField(f[3], 1, 12), dow: parseCronField(f[4], 0, 7), domAll: f[2] === '*', dowAll: f[4] === '*' };
}

/** First local minute strictly after `after` that matches (standard cron: day-of-month OR day-of-week when both are set). */
export function nextCron(expr: string, after: number): number {
  const c = parseCron(expr);
  const d = new Date(after); d.setSeconds(0, 0); d.setMinutes(d.getMinutes() + 1);
  const limit = after + 4 * 366 * DAY;
  while (d.getTime() <= limit) {
    const dayOk = c.month.has(d.getMonth() + 1) && (c.domAll && c.dowAll ? true : c.domAll ? c.dow.has(d.getDay()) :
      c.dowAll ? c.dom.has(d.getDate()) : c.dom.has(d.getDate()) || c.dow.has(d.getDay()));
    if (!dayOk) { d.setDate(d.getDate() + 1); d.setHours(0, 0, 0, 0); continue; }
    if (!c.hour.has(d.getHours())) { d.setHours(d.getHours() + 1, 0, 0, 0); continue; }
    if (!c.minute.has(d.getMinutes())) { d.setMinutes(d.getMinutes() + 1); continue; }
    return d.getTime();
  }
  return fail('Bu cron hiç çalışmaz / this cron never runs.');
}

function localAt(base: number, dayOffset: number, t: { h: number; m: number }): number {
  const d = new Date(base); d.setDate(d.getDate() + dayOffset); d.setHours(t.h, t.m, 0, 0); return d.getTime();
}

/** Parses the user's schedule words; `now` is the reference for relative and one-time schedules. */
export function parseSchedule(input: string, now: number): Schedule {
  const raw = String(input ?? '').trim();
  if (!raw) fail('Zaman boş / schedule is empty.');
  if (raw.length > 120) fail('Zaman çok uzun / schedule too long.');
  // 5-field cron expression.
  if (/^[\d*/,-]+(\s+[\d*/,-]+){4}$/.test(raw)) { nextCron(raw, now); return { kind: 'cron', expr: raw.split(/\s+/).join(' ') }; }
  // ISO date/time (date only = 09:00); with an offset/Z it is absolute, otherwise local.
  const iso = /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$/.exec(raw);
  if (iso) {
    const at = iso[7] ? Date.parse(raw.replace(' ', 'T')) : new Date(+iso[1], +iso[2] - 1, +iso[3], iso[4] ? +iso[4] : 9, iso[5] ? +iso[5] : 0, iso[6] ? +iso[6] : 0).getTime();
    if (!Number.isFinite(at)) fail('Geçersiz tarih / invalid date.');
    if (at <= now - MINUTE) fail('Bu zaman geçmişte / that time is in the past.');
    return { kind: 'once', at: Math.max(at, now) };
  }
  const text = normalize(raw);
  // Interval: "every 2h", "her 2 saatte bir", "30 dakikada bir", "every hour", "her saat", "hourly".
  const every = /(?<!\p{L})(?:every|her)\s*(\d+)?\s*(m|min|mins|minutes?|h|hrs?|hours?|dk|dakika|saat|d|days?)(?:t[ae]|d[ae])?(?: bir)?(?!\p{L})/u.exec(text) ??
    /(?<!\p{L})(\d+)\s*(dk|dakika|saat|gün|gun)(?:t[ae]|d[ae]) bir(?!\p{L})/u.exec(text);
  if (every || /(?<!\p{L})hourly(?!\p{L})/u.test(text)) {
    const n = every ? (every[1] ? +every[1] : 1) : 1, size = every ? unit(every[2]) : HOUR;
    // "her saat 9'da"-like phrases are not intervals; "her gün"/"every day" is handled as daily below.
    if (size && !(size === DAY && !every?.[1]) && !(size === HOUR && every && !every[1] && clockTime(text.replace(every![0], '')))) {
      const ms = n * size;
      if (ms < MIN_INTERVAL) fail('En kısa aralık 15 dakika / minimum interval is 15 minutes.');
      if (ms > 31 * DAY) fail('Aralık çok uzun / interval too long.');
      return { kind: 'interval', every: ms };
    }
  }
  // Relative once: "in 30m", "30 dakika sonra", "2 saat sonra", "in 2 hours".
  const rel = /(?<!\p{L})in (\d+)\s*(\p{L}+)/u.exec(text) ?? /(\d+)\s*(\p{L}+) sonra(?!\p{L})/u.exec(text);
  if (rel && unit(rel[2])) {
    const ms = +rel[1] * unit(rel[2])!;
    if (ms < MINUTE || ms > 366 * DAY) fail('Geçersiz süre / invalid duration.');
    return { kind: 'once', at: now + ms };
  }
  const time = clockTime(text);
  // Monthly: "ayın 1'i", "her ayın 15'inde", "monthly on the 1st", "1st of every month".
  const monthly = /(?<!\p{L})ayın (\d{1,2})/u.exec(text) ?? /(?<!\p{L})(?:monthly|every month)(?: on)?(?: the)? (\d{1,2})(?:st|nd|rd|th)?/u.exec(text) ??
    /(?<!\p{L})(?:on )?(?:the )?(\d{1,2})(?:st|nd|rd|th)? (?:of )?(?:every|each) month/u.exec(text);
  if (monthly) {
    const day = +monthly[1], t = time ?? defaultTime(text);
    if (day < 1 || day > 31) fail('Ayın günü 1-31 / day of month 1-31.');
    return { kind: 'cron', expr: `${t.m} ${t.h} ${day} * *` };
  }
  const dayWords = [...text.matchAll(DAY_WORDS)];
  const recurring = /(?<!\p{L})(her|every|daily|günlük|gunluk|hergün|hergun|günleri|gunleri)(?!\p{L})/u.test(text) || dayWords.some(m => m[2]) ||
    /(?<!\p{L})(weekdays|weekends|hafta (içi|ici|sonu))(?!\p{L})/u.test(text);
  if (/(?<!\p{L})(hafta (içi|ici)|weekdays?|every weekday)(?!\p{L})/u.test(text)) { const t = time ?? defaultTime(text); return { kind: 'cron', expr: `${t.m} ${t.h} * * 1-5` }; }
  if (/(?<!\p{L})(hafta sonu|weekends?)(?!\p{L})/u.test(text)) { const t = time ?? defaultTime(text); return { kind: 'cron', expr: `${t.m} ${t.h} * * 0,6` }; }
  const days = [...new Set(dayWords.map(m => DAYS.find(([pattern]) => pattern.test(m[1]))![1]))].sort();
  if (days.length && recurring) { const t = time ?? defaultTime(text); return { kind: 'cron', expr: `${t.m} ${t.h} * * ${days.join(',')}` }; }
  if (recurring && /(?<!\p{L})(gün|gun|day|daily|günlük|gunluk|hergün|hergun|sabah|akşam|aksam|gece|morning|evening|night|öğlen|oglen)/u.test(text)) {
    const t = time ?? defaultTime(text); return { kind: 'cron', expr: `${t.m} ${t.h} * * *` };
  }
  if (recurring) fail('Tekrar anlaşılamadı / could not read the repeat.');
  // One time: "yarın 09:00", "tomorrow 9am", "bugün 15:00", "pazartesi 10'da", "saat 15:00" (next such time).
  if (days.length === 1 || /(?<!\p{L})(yarın|yarin|tomorrow|bugün|bugun|today|tonight|bu akşam|bu aksam)(?!\p{L})/u.test(text) || time) {
    const t = time ?? defaultTime(text);
    if (/(?<!\p{L})(yarın|yarin|tomorrow)(?!\p{L})/u.test(text)) return { kind: 'once', at: localAt(now, 1, t) };
    if (days.length === 1) {
      const offset = (days[0] - new Date(now).getDay() + 7) % 7;
      const at = localAt(now, offset, t);
      return { kind: 'once', at: at > now ? at : localAt(now, offset + 7, t) };
    }
    const today = localAt(now, 0, t);
    if (/(?<!\p{L})(bugün|bugun|today|tonight|bu akşam|bu aksam)(?!\p{L})/u.test(text) && today <= now) fail('Bu saat bugün geçti / that time has passed today.');
    return { kind: 'once', at: today > now ? today : localAt(now, 1, t) };
  }
  return fail('Zaman anlaşılamadı / could not read the schedule.');
}

/** Next run strictly after `after` (once: its time if still ahead, else none). */
export function nextRun(schedule: Schedule, after: number, from?: number): number | null {
  if (schedule.kind === 'once') return schedule.at > after ? schedule.at : null;
  if (schedule.kind === 'interval') {
    // Anchored on the last slot (`from`); missed slots collapse into the next one after `after`.
    const base = from ?? after;
    return base + Math.max(1, Math.floor((after - base) / schedule.every) + 1) * schedule.every;
  }
  return nextCron(schedule.expr, after);
}

/** First run of a new task. */
export const firstRun = (schedule: Schedule, now: number): number | null =>
  schedule.kind === 'once' ? schedule.at : schedule.kind === 'interval' ? now + schedule.every : nextCron(schedule.expr, now);

const hm = (h: number, m: number) => `${pad(h)}:${pad(m)}`;
/** Short Turkish description ("Her gün 09:00", "Hafta içi 09:00", "Her 2 saatte bir", "Bir kez"). */
export function describe(schedule: Schedule): string {
  if (schedule.kind === 'once') return 'Bir kez';
  if (schedule.kind === 'interval') {
    const ms = schedule.every;
    return ms % DAY === 0 ? `Her ${ms / DAY} günde bir` : ms % HOUR === 0 ? (ms === HOUR ? 'Saatte bir' : `Her ${ms / HOUR} saatte bir`) : `Her ${ms / MINUTE} dakikada bir`;
  }
  const f = schedule.expr.split(' '), simple = /^\d+$/.test(f[0]) && /^\d+$/.test(f[1]) && f[3] === '*';
  if (simple) {
    const time = hm(+f[1], +f[0]);
    if (f[2] === '*' && f[4] === '*') return `Her gün ${time}`;
    if (f[2] === '*' && f[4] === '1-5') return `Hafta içi ${time}`;
    if (f[2] === '*' && f[4] === '0,6') return `Hafta sonu ${time}`;
    if (f[2] === '*' && /^[0-7](,[0-7])*$/.test(f[4])) return `Her ${f[4].split(',').map(d => TR_DAYS[+d % 7]).join(', ')} ${time}`;
    if (/^\d+$/.test(f[2]) && f[4] === '*') return `Her ayın ${f[2]}. günü ${time}`;
  }
  return `Cron ${schedule.expr}`;
}

/** When, relative to now: "bugün 09:00", "yarın 09:00", "20 Eki Pzt 09:00". */
export function when(at: number | null, now: number): string {
  if (at === null) return '—';
  const d = new Date(at), time = hm(d.getHours(), d.getMinutes());
  const day = (offset: number) => { const x = new Date(now); x.setDate(x.getDate() + offset); return x.toDateString(); };
  if (d.toDateString() === day(0)) return 'bugün ' + time;
  if (d.toDateString() === day(1)) return 'yarın ' + time;
  return `${d.getDate()} ${TR_MONTHS[d.getMonth()]}${d.getFullYear() !== new Date(now).getFullYear() ? ' ' + d.getFullYear() : ''} ${TR_SHORT[d.getDay()]} ${time}`;
}

/** Stored form: kind + value. */
export const encode = (s: Schedule) => ({ kind: s.kind, spec: s.kind === 'once' ? String(s.at) : s.kind === 'interval' ? String(s.every) : s.expr });
export function decode(kind: string, spec: string): Schedule {
  if (kind === 'once') return { kind, at: Number(spec) };
  if (kind === 'interval') return { kind, every: Number(spec) };
  return { kind: 'cron', expr: spec };
}
