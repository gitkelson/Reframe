// Label policy (spec 4.3). Pure: the caller supplies measure(text) -> pixels.
import { INSIDE_MIN_CHARS, OUTSIDE_MIN_PX_PER_DAY } from './zoom.js';

export const BAR_LABEL_PAD = 5;         // horizontal padding inside a bar
export const OUTSIDE_GAP = 4;           // gap between a bar and its outside label
export const OUTSIDE_MAX_PX = 240;      // longest outside label; longer titles are elided

/** Width a bar needs to show INSIDE_MIN_CHARS characters. */
export function insideThreshold(measure) { return measure('n'.repeat(INSIDE_MIN_CHARS)) + 2 * BAR_LABEL_PAD; }

/**
 * 'inside' when >= 6 characters fit in the bar, 'outside' when the bar is too narrow but the zoom is
 * >= 8 px/day, otherwise 'none'. Narrower bar => never 'inside', so evaluating this at the lowest
 * px/day of a band is a safe reservation for every zoom within the band.
 */
export function labelMode(barWidthPx, pxPerDay, measure) {
  if (barWidthPx >= insideThreshold(measure)) return 'inside';
  return pxPerDay >= OUTSIDE_MIN_PX_PER_DAY ? 'outside' : 'none';
}

/** Pixels reserved to the right of a bar for an outside label (gap + text, capped). */
export function outsideLabelPx(title, measure) { return OUTSIDE_GAP + Math.min(OUTSIDE_MAX_PX, measure(title)); }

/** Fit `text` into maxPx, adding an ellipsis; '' when not even one character plus the ellipsis fits. */
export function elide(text, maxPx, measure) {
  if (maxPx <= 0) return '';
  if (measure(text) <= maxPx) return text;
  const ell = '\u2026';
  let lo = 0, hi = text.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (measure(text.slice(0, mid) + ell) <= maxPx) lo = mid; else hi = mid - 1;
  }
  return lo > 0 ? text.slice(0, lo).trimEnd() + ell : '';
}
