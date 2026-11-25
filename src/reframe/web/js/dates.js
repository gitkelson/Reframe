// Calendar helpers shared by every module. Pure: no DOM, no state.
// A "day number" is an integer count of days since 1970-01-01 (UTC calendar days), so date math is
// plain arithmetic with no time-zone or DST surprises. View positions are floating day numbers.

const MS_DAY = 86400000;
export const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
  'September', 'October', 'November', 'December'];
export const MONTHS_SHORT = MONTHS.map((m) => m.slice(0, 3));
export const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
export const WEEKDAYS_SHORT = WEEKDAYS.map((d) => d.slice(0, 3));

export function toDayNumber(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) throw new Error(`bad ISO date: ${iso}`);
  return Math.round(Date.UTC(+m[1], +m[2] - 1, +m[3]) / MS_DAY);
}

export function fromDayNumber(n) {
  const d = new Date(Math.floor(n) * MS_DAY);
  const y = d.getUTCFullYear();
  return `${String(y).padStart(4, '0')}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
}

/** {year, month (1-12), day, weekday (0=Sunday)} for a (possibly fractional) day number. */
export function parts(n) {
  const d = new Date(Math.floor(n) * MS_DAY);
  return { year: d.getUTCFullYear(), month: d.getUTCMonth() + 1, day: d.getUTCDate(), weekday: d.getUTCDay() };
}

export function dayNumberOf(year, month, day) {
  return Math.round(Date.UTC(year, month - 1, day) / MS_DAY);
}

export function todayISO(now = new Date()) {
  return `${String(now.getFullYear()).padStart(4, '0')}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
}

export function weekday(n) { return parts(n).weekday; }

/** First day (Sunday) of the week containing day n. Weeks start on Sunday (R-06). */
export function weekStart(n) { return Math.floor(n) - weekday(n); }

/** US week number: week 1 is the week (Sun-Sat) containing January 1 (R-06). */
export function usWeekNumber(n) {
  const { year } = parts(n);
  const jan1 = dayNumberOf(year, 1, 1);
  const firstWeekStart = jan1 - weekday(jan1);
  const ws = weekStart(n);
  // A week that runs into the next year is week 1 of that year (it contains Jan 1 of the next year).
  const nextJan1 = dayNumberOf(year + 1, 1, 1);
  if (ws + 6 >= nextJan1) return 1;
  return Math.floor((ws - firstWeekStart) / 7) + 1;
}

export function addMonths(n, delta) {
  const p = parts(n);
  const idx = p.year * 12 + (p.month - 1) + delta;
  return dayNumberOf(Math.floor(idx / 12), ((idx % 12) + 12) % 12 + 1, 1);
}

export function monthStart(n) { const p = parts(n); return dayNumberOf(p.year, p.month, 1); }
export function quarterStart(n) { const p = parts(n); return dayNumberOf(p.year, Math.floor((p.month - 1) / 3) * 3 + 1, 1); }
export function yearStart(n) { return dayNumberOf(parts(n).year, 1, 1); }

export function formatLong(n) {
  const p = parts(n);
  return `${WEEKDAYS[p.weekday]}, ${MONTHS[p.month - 1]} ${p.day}, ${p.year}`;
}

export function formatShort(n) {
  const p = parts(n);
  return `${MONTHS_SHORT[p.month - 1]} ${p.day}, ${p.year}`;
}

/** "Nov 20" or "Nov 20 - Nov 22, 2026" style range for tooltips. Inclusive end. */
export function formatRange(startN, endN) {
  if (startN === endN) return formatLong(startN);
  const a = parts(startN), b = parts(endN);
  const left = a.year === b.year ? `${MONTHS_SHORT[a.month - 1]} ${a.day}` : `${MONTHS_SHORT[a.month - 1]} ${a.day}, ${a.year}`;
  return `${left} - ${MONTHS_SHORT[b.month - 1]} ${b.day}, ${b.year}`;
}

export function durationDays(startN, endN) { return endN - startN + 1; }

/** ISO date with or without dashes (R-100). Returns 'YYYY-MM-DD' or null. */
export function parseTypedDate(text) {
  const m = /^(\d{4})-?(\d{2})-?(\d{2})$/.exec(String(text).trim());
  if (!m) return null;
  const y = +m[1], mo = +m[2], d = +m[3];
  const n = dayNumberOf(y, mo, d);
  const back = parts(n);
  return back.year === y && back.month === mo && back.day === d ? fromDayNumber(n) : null;
}

export const DATE_HINT = 'Use a date like 2026-11-08 or 20261108';
export function isWeekend(n) { const w = weekday(n); return w === 0 || w === 6; }
