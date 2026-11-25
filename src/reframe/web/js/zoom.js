// Zoom bands, view <-> pixel helpers and ruler ticks (spec 4.3, R-06, R-07). Pure: no DOM, no store.
import {
  MONTHS, MONTHS_SHORT, WEEKDAYS_SHORT, addMonths, dayNumberOf, monthStart, parts, quarterStart, usWeekNumber,
  weekStart, yearStart,
} from './dates.js';

export const INSIDE_MIN_CHARS = 6;          // a bar shows its label inside when >= 6 characters fit
export const OUTSIDE_MIN_PX_PER_DAY = 8;    // outside labels (and their reserved space) only at >= 8 px/day
export const WEEKEND_MIN_PX_PER_DAY = 3;    // below this the weekend stripes are sub-pixel noise

/** Zoom band: bands are a factor of 2 in px/day, band k covers [2^k, 2^(k+1)). */
export function bandOf(pxPerDay) { return Math.floor(Math.log2(pxPerDay) + 1e-9); }
export function bandMinPxPerDay(band) { return 2 ** band; }

export function pxPerDay(view, plotWidth) { return plotWidth / (view.end - view.start); }
export function dayToX(day, view, plotLeft, ppd) { return plotLeft + (day - view.start) * ppd; }
export function xToDay(x, view, plotLeft, ppd) { return view.start + (x - plotLeft) / ppd; }

const RULER_LEVELS = [
  { below: 1.2, major: 'year', minor: 'quarter' },
  { below: 4, major: 'year', minor: 'month' },
  { below: 14, major: 'month', minor: 'week' },
  { below: Infinity, major: 'month', minor: 'day' },
];

/** Which two ruler rows to show at this zoom: {major, minor} unit names. */
export function rulerUnits(ppd) {
  const lv = RULER_LEVELS.find((l) => ppd < l.below);
  return { major: lv.major, minor: lv.minor };
}

const UNIT_START = {
  year: (n) => yearStart(n),
  quarter: (n) => quarterStart(n),
  month: (n) => monthStart(n),
  week: (n) => weekStart(n),
  day: (n) => Math.floor(n),
};
const UNIT_NEXT = {
  year: (n) => dayNumberOf(parts(n).year + 1, 1, 1),
  quarter: (n) => addMonths(n, 3),
  month: (n) => addMonths(n, 1),
  week: (n) => n + 7,
  day: (n) => n + 1,
};

function describe(unit, s, wide) {
  const p = parts(s);
  switch (unit) {
    case 'year': return { labels: [String(p.year)] };
    case 'quarter': return { labels: [`Q${Math.floor((p.month - 1) / 3) + 1}`] };
    case 'month': return { labels: wide
      ? [`${MONTHS[p.month - 1]} ${p.year}`, `${MONTHS_SHORT[p.month - 1]} ${p.year}`, MONTHS_SHORT[p.month - 1]]
      : [MONTHS_SHORT[p.month - 1]] };
    case 'week': return { labels: [`W${usWeekNumber(s)}`, String(usWeekNumber(s))] };
    default: return { labels: [`${WEEKDAYS_SHORT[p.weekday]} ${p.day}`, String(p.day)] };
  }
}

/** Ticks of one unit that intersect [start, end): [{start, end, unit, labels: [longest..shortest]}]. */
export function ticksFor(unit, start, end, wide = false) {
  const out = [];
  let s = UNIT_START[unit](start);
  while (s < end) {
    const e = UNIT_NEXT[unit](s);
    out.push({ start: s, end: e, unit, ...describe(unit, s, wide) });
    s = e;
  }
  return out;
}

/** Both ruler rows for the window at this zoom. */
export function rulerRows(start, end, ppd) {
  const { major, minor } = rulerUnits(ppd);
  return { majorUnit: major, minorUnit: minor, major: ticksFor(major, start, end, true), minor: ticksFor(minor, start, end) };
}

/** Weekend spans [s, e) in day numbers (Saturday+Sunday merged) intersecting [start, end). Empty below ~3 px/day. */
export function weekendSpans(start, end, ppd) {
  if (ppd < WEEKEND_MIN_PX_PER_DAY) return [];
  const out = [];
  for (let s = weekStart(Math.floor(start)) - 1; s < end; s += 7) {   // s is a Saturday
    if (s + 2 > start) out.push([s, s + 2]);
  }
  return out;
}
