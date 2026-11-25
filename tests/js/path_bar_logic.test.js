import test from 'node:test';
import assert from 'node:assert/strict';
import { looksLikePath, nextRequestId, formatSize, formatDate, formatCount } from '../../src/reframe/web/js/path_bar.js';

test('looksLikePath follows R-120 (same rule as the backend)', () => {
  for (const t of ['C:\\Projects\\a.pdf', 'c:\\x', '\\\\server\\share\\x', 'http://x.org', 'HTTPS://x.org/a b',
    'webcal://cal/x.ics', 'file:///C:/x', '"order 1234"', "'quoted'", '  C:\\lead space']) {
    assert.equal(looksLikePath(t), true, t);
  }
  for (const t of ['order 1234', 'ext:pdf invoice', 'C:', 'C:/x', '', '"unbalanced', 'www.example.com', '\\single', null, undefined]) {
    assert.equal(looksLikePath(t), false, String(t));
  }
});

test('nextRequestId increases monotonically', () => {
  assert.equal(nextRequestId(0), 1);
  assert.equal(nextRequestId(41), 42);
  assert.equal(nextRequestId(undefined), 1);
  assert.equal(nextRequestId('x'), 1);
});

test('formatSize never shows a false 0 for unknown sizes', () => {
  assert.equal(formatSize(null), '');
  assert.equal(formatSize(undefined), '');
  assert.equal(formatSize(-1), '');
  assert.equal(formatSize('abc'), '');
  assert.equal(formatSize(0), '0 B');
  assert.equal(formatSize(512), '512 B');
  assert.equal(formatSize(1536), '1.5 KB');
  assert.equal(formatSize(23 * 1024 * 1024), '23 MB');
  assert.equal(formatSize(5 * 1024 ** 3), '5.0 GB');
});

test('formatDate shows local date and time, blank when unknown', () => {
  assert.equal(formatDate(null), '');
  assert.equal(formatDate(''), '');
  assert.equal(formatDate('not a date'), '');
  assert.equal(formatDate('2026-11-24T10:09:09'), '2026-11-24 10:09');
  const d = new Date('2026-11-24T18:09:00Z');
  const p = (n) => String(n).padStart(2, '0');
  assert.equal(formatDate('2026-11-24T10:09:00-08:00'),
    `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`);
});

test('formatCount shows total when known', () => {
  assert.equal(formatCount(3, null), '3 shown');
  assert.equal(formatCount(100, 250), '100 of 250');
});
