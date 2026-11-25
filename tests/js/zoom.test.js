import test from 'node:test';
import assert from 'node:assert/strict';
import {
  bandOf, bandMinPxPerDay, dayToX, xToDay, rulerRows, rulerUnits, ticksFor, weekendSpans, pxPerDay,
} from '../../src/reframe/web/js/zoom.js';
import { elide, insideThreshold, labelMode, outsideLabelPx } from '../../src/reframe/web/js/labels.js';
import { fromDayNumber, toDayNumber } from '../../src/reframe/web/js/dates.js';

const d = toDayNumber;
const measure = (t) => t.length * 7;

test('bands are factors of 2 in px/day', () => {
  assert.equal(bandOf(1), 0);
  assert.equal(bandOf(1.99), 0);
  assert.equal(bandOf(2), 1);
  assert.equal(bandOf(8), 3);
  assert.equal(bandOf(15.9), 3);
  assert.equal(bandOf(16), 4);
  assert.equal(bandOf(0.5), -1);
  assert.equal(bandOf(0.4), -2);
  assert.equal(bandMinPxPerDay(3), 8);
  assert.equal(bandMinPxPerDay(-1), 0.5);
  for (let b = -2; b < 8; b++) assert.equal(bandOf(bandMinPxPerDay(b)), b);   // exact boundaries
});

test('px/day and day<->x round trip', () => {
  const view = { start: 100, end: 142 };
  const ppd = pxPerDay(view, 840);
  assert.equal(ppd, 20);
  assert.equal(dayToX(110, view, 30, ppd), 230);
  assert.equal(xToDay(230, view, 30, ppd), 110);
});

test('ruler level follows zoom', () => {
  assert.deepEqual(rulerUnits(0.5), { major: 'year', minor: 'quarter' });
  assert.deepEqual(rulerUnits(2), { major: 'year', minor: 'month' });
  assert.deepEqual(rulerUnits(8), { major: 'month', minor: 'week' });
  assert.deepEqual(rulerUnits(30), { major: 'month', minor: 'day' });
});

test('year / quarter / month ticks cover the window exactly', () => {
  const y = ticksFor('year', d('2025-06-01'), d('2027-02-01'));
  assert.deepEqual(y.map((t) => t.labels[0]), ['2025', '2026', '2027']);
  assert.equal(y[1].start, d('2026-01-01'));
  assert.equal(y[1].end, d('2027-01-01'));
  const q = ticksFor('quarter', d('2026-02-10'), d('2026-08-01'));
  assert.deepEqual(q.map((t) => t.labels[0]), ['Q1', 'Q2', 'Q3']);
  const m = ticksFor('month', d('2026-01-15'), d('2026-03-01'), true);
  assert.deepEqual(m.map((t) => t.labels[0]), ['January 2026', 'February 2026']);
  assert.equal(m[1].end, d('2026-03-01'));
});

test('weeks start on Sunday and use US week numbers', () => {
  const w = ticksFor('week', d('2026-01-04'), d('2026-01-18'));   // 2026-01-04 is a Sunday
  assert.deepEqual(w.map((t) => t.labels[0]), ['W2', 'W3']);
  assert.equal(new Date(fromDayNumber(w[0].start) + 'T00:00:00Z').getUTCDay(), 0);
});

test('US week numbers around year ends: the week containing Jan 1 is week 1', () => {
  // 2025-12-28 (Sun) .. 2026-01-03 (Sat) contains Jan 1 2026
  const w = ticksFor('week', d('2025-12-14'), d('2026-01-11'));
  assert.deepEqual(w.map((t) => [fromDayNumber(t.start), t.labels[0]]),
    [['2025-12-14', 'W51'], ['2025-12-21', 'W52'], ['2025-12-28', 'W1'], ['2026-01-04', 'W2']]);
  // 2023: Jan 1 is a Sunday, so 2023-01-01 starts week 1 and 2022-12-25 is the last week of 2022
  const w2 = ticksFor('week', d('2022-12-25'), d('2023-01-08'));
  assert.deepEqual(w2.map((t) => t.labels[0]), ['W53', 'W1']);
});

test('day ticks carry weekday and day number labels', () => {
  const t = ticksFor('day', d('2026-11-08') + 0.4, d('2026-11-10'));
  assert.deepEqual(t.map((x) => x.labels), [['Sun 8', '8'], ['Mon 9', '9']]);
});

test('rulerRows returns both rows for the zoom level', () => {
  const r = rulerRows(d('2026-11-01'), d('2026-12-01'), 30);
  assert.equal(r.majorUnit, 'month');
  assert.equal(r.minorUnit, 'day');
  assert.equal(r.minor.length, 30);
});

test('weekend spans are Saturday+Sunday, only at readable zoom', () => {
  assert.deepEqual(weekendSpans(0, 100, 1), []);
  const spans = weekendSpans(d('2026-11-02'), d('2026-11-16'), 10);   // Mon .. Mon
  assert.deepEqual(spans.map(([s, e]) => [fromDayNumber(s), fromDayNumber(e - 1)]),
    [['2026-11-07', '2026-11-08'], ['2026-11-14', '2026-11-15']]);
  const clipped = weekendSpans(d('2026-11-08'), d('2026-11-10'), 10);   // starts on the Sunday
  assert.deepEqual(clipped.map(([s]) => fromDayNumber(s)), ['2026-11-07']);
});

test('label policy: inside when 6 chars fit, outside from 8 px/day, otherwise none', () => {
  const need = insideThreshold(measure);
  assert.equal(labelMode(need, 1, measure), 'inside');
  assert.equal(labelMode(need - 1, 7.9, measure), 'none');
  assert.equal(labelMode(need - 1, 8, measure), 'outside');
  assert.equal(labelMode(10, 40, measure), 'outside');
  assert.equal(labelMode(need + 100, 0.5, measure), 'inside');
});

test('outside label reservation is capped', () => {
  assert.equal(outsideLabelPx('abc', measure), 4 + 21);
  assert.equal(outsideLabelPx('x'.repeat(500), measure), 4 + 240);
});

test('elide fits text with an ellipsis', () => {
  assert.equal(elide('Hello', 100, measure), 'Hello');
  const out = elide('Hello world', 50, measure);
  assert.ok(measure(out) <= 50 && out.endsWith('\u2026'));
  assert.equal(elide('Hello', 5, measure), '');
  assert.equal(elide('Hello', 0, measure), '');
});
