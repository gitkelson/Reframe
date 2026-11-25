import test from 'node:test';
import assert from 'node:assert/strict';
import { packItems, firstReaching } from '../../src/reframe/web/js/packing.js';
import { fromDayNumber, toDayNumber } from '../../src/reframe/web/js/dates.js';

const BASE = toDayNumber('2026-01-01');
const measure = (t) => t.length * 7;
let seq = 0;
const mk = (s, len, extra = {}) => ({
  id: extra.id ?? `i${String(seq++).padStart(5, '0')}`, kind: 'plan', title: extra.title ?? 'Item',
  start_date: fromDayNumber(BASE + s), end_date: fromDayNumber(BASE + s + len - 1), tags: [], ...extra,
});
const pack = (items, o = {}) => packItems(items, { band: 0, measure, ...o });

function maxConcurrency(items) {
  const ev = [];
  for (const it of items) { ev.push([toDayNumber(it.start_date), 1]); ev.push([toDayNumber(it.end_date) + 1, -1]); }
  ev.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  let cur = 0, best = 0;
  for (const [, d] of ev) { cur += d; best = Math.max(best, cur); }
  return best;
}

function rng(seed) { let x = seed; return () => (x = (x * 1664525 + 1013904223) % 4294967296) / 4294967296; }
function randomItems(n, seed = 1) {
  const r = rng(seed);
  return Array.from({ length: n }, (_, i) => mk(Math.floor(r() * 700), 1 + Math.floor(r() * r() * 40), { id: `r${i}`, title: `Task ${i}` }));
}

function assertNoOverlap(res) {
  for (const row of res.rows) {
    for (let i = 1; i < row.length; i++) assert.ok(row[i].s >= row[i - 1].ext, `overlap in row: ${row[i - 1].id} / ${row[i].id}`);
  }
}

test('deterministic: same input gives identical rows, input order irrelevant', () => {
  const items = randomItems(500);
  const a = pack(items), b = pack([...items].reverse());
  for (const it of items) assert.equal(a.rowOf.get(it.id), b.rowOf.get(it.id));
  assert.equal(a.rowCount, b.rowCount);
});

test('no overlap within a row', () => assertNoOverlap(pack(randomItems(800, 3))));

test('row count equals max concurrency when no outside labels (band 0)', () => {
  const items = randomItems(600, 5);
  const res = pack(items, { band: 0 });
  assert.equal(res.rowCount, maxConcurrency(items));
});

test('first fit puts an item into the lowest-numbered free row', () => {
  const items = [mk(0, 5, { id: 'a' }), mk(0, 2, { id: 'b' }), mk(3, 2, { id: 'c' }), mk(5, 1, { id: 'd' })];
  const res = pack(items);
  assert.equal(res.rowOf.get('a'), 0);
  assert.equal(res.rowOf.get('b'), 1);
  assert.equal(res.rowOf.get('c'), 1);   // b ended, a still busy
  assert.equal(res.rowOf.get('d'), 0);   // a ended on day 5
});

test('sort order: start asc, longer first, then id', () => {
  const items = [mk(0, 1, { id: 'b' }), mk(0, 3, { id: 'z' }), mk(0, 1, { id: 'a' })];
  const res = pack(items);
  assert.deepEqual(['z', 'a', 'b'].map((id) => res.rowOf.get(id)), [0, 1, 2]);
});

test('adjacent items share a row (end is inclusive, extent is end+1)', () => {
  const res = pack([mk(0, 3, { id: 'a' }), mk(3, 3, { id: 'b' })]);
  assert.equal(res.rowCount, 1);
  const res2 = pack([mk(0, 3, { id: 'a' }), mk(2, 3, { id: 'b' })]);
  assert.equal(res2.rowCount, 2);
});

test('stable: packing does not depend on the window, so panning never changes rows', () => {
  const items = randomItems(300, 9);
  const first = pack(items, { band: 2 });
  const second = pack(items, { band: 2 });
  for (const it of items) assert.equal(first.rowOf.get(it.id), second.rowOf.get(it.id));
});

test('outside labels reserve space at the band minimum px/day and can add rows', () => {
  // two 1-day items 2 days apart; at 8 px/day each is 8 px wide, too narrow for inside labels
  const items = [mk(0, 1, { id: 'a', title: 'Delivery of material' }), mk(2, 1, { id: 'b', title: 'Delivery of spares' })];
  const at8 = pack(items, { band: 3 });   // label ~144 px = 18 days reserved
  assert.equal(at8.rowCount, 2);
  assert.equal(at8.rowOf.get('b'), 1);
  assert.equal(pack(items, { band: 2 }).rowCount, 1);            // 4 px/day: no outside labels
  assert.equal(pack(items, { band: 2 }).entries.get('a').label, 'none');
});

test('repack on band change: reservations differ between bands', () => {
  const items = [mk(0, 1, { id: 'a', title: 'A fairly long label' }), mk(3, 1, { id: 'b' })];
  const low = pack(items, { band: 2 }), high = pack(items, { band: 6 });
  assert.equal(low.rowCount, 1);    // below 8 px/day there are no outside labels
  assert.equal(high.rowCount, 1);   // at 64 px/day the bar is wide enough for an inside label
  const mid = pack(items, { band: 3 });
  assert.equal(mid.rowCount, 2);    // at 8 px/day the 3-day gap is only 24 px
});

test('wide items label inside and reserve nothing', () => {
  const res = pack([mk(0, 30, { id: 'a', title: 'x'.repeat(80) }), mk(30, 1, { id: 'b' })], { band: 3 });
  assert.equal(res.entries.get('a').label, 'inside');
  assert.equal(res.entries.get('a').ext, res.entries.get('a').e);
  assert.equal(res.rowOf.get('b'), 0);
});

test('group by first tag: lanes, headers, untagged last, packing inside each lane', () => {
  const tags = { t1: { name: 'Beta' }, t2: { name: 'alpha' } };
  const items = [
    mk(0, 5, { id: 'a', tags: ['t1', 't2'] }), mk(1, 5, { id: 'b', tags: ['t1'] }),
    mk(0, 5, { id: 'c', tags: ['t2'] }), mk(0, 5, { id: 'd' }), mk(2, 2, { id: 'e' }),
  ];
  const res = pack(items, { groupByTag: true, tags });
  assert.deepEqual(res.lanes.map((l) => l.name), ['alpha', 'Beta', 'untagged']);
  assert.deepEqual(res.lanes.map((l) => l.headerRow), [0, 2, 5]);
  assert.equal(res.rowCount, 8);
  assert.equal(res.rowOf.get('c'), 1);
  assert.deepEqual([res.rowOf.get('a'), res.rowOf.get('b')], [3, 4]);
  assert.deepEqual([res.rowOf.get('d'), res.rowOf.get('e')], [6, 7]);
  for (const h of res.headerRows) assert.equal(res.rows[h].length, 0);
  assertNoOverlap(res);
});

test('rows index supports culling by day', () => {
  const res = pack([mk(0, 2, { id: 'a' }), mk(10, 2, { id: 'b' }), mk(20, 2, { id: 'c' })]);
  const row = res.rows[0];
  assert.equal(row[firstReaching(row, BASE + 5)].id, 'b');
  assert.equal(firstReaching(row, BASE + 100), 3);
});

test('10,000 items pack in under 100 ms', () => {
  const items = randomItems(10000, 11);
  pack(items, { band: 3 });   // warm up
  const t0 = performance.now();
  const res = pack(items, { band: 3 });
  const ms = performance.now() - t0;
  console.log(`packing 10,000 items: ${ms.toFixed(1)} ms, ${res.rowCount} rows`);
  assert.ok(ms < 100, `took ${ms} ms`);
});

// ---------------------------------------------------------------- strict Gantt (R-17) and tree lanes

test('strict mode: one item per row, rows in sort-key order, later start is lower', () => {
  const items = randomItems(300, 9);
  const res = pack(items, { mode: 'strict' });
  assert.equal(res.mode, 'strict');
  assert.equal(res.rowCount, items.length);
  const byRow = [...res.entries.values()].sort((a, b) => a.row - b.row);
  for (let i = 1; i < byRow.length; i++) {
    const a = byRow[i - 1], b = byRow[i];
    assert.ok(a.s < b.s || (a.s === b.s && (a.len > b.len || (a.len === b.len && a.id < b.id))), `row order ${a.id} / ${b.id}`);
  }
  for (const row of res.rows) assert.equal(row.length, 1);
});

test('strict mode: outside labels never add rows, per-lane strict packing', () => {
  const items = [mk(0, 1, { title: 'A very long title that would need room', tags: ['t1'] }), mk(1, 1, { tags: ['t1'] }), mk(0, 3, { tags: ['t2'] })];
  const a = packItems(items, { band: 5, measure, mode: 'strict' });
  const b = packItems(items, { band: 0, measure, mode: 'strict' });
  assert.equal(a.rowCount, b.rowCount);
  const g = packItems(items, { band: 0, measure, mode: 'strict', groupByTag: true, tags: { t1: { name: 'One' }, t2: { name: 'Two' } } });
  assert.deepEqual(g.lanes.map((l) => [l.name, l.rowCount]), [['One', 2], ['Two', 1]]);
});

test('lanes follow tag-tree order before names', () => {
  const items = [mk(0, 1, { tags: ['a'] }), mk(0, 1, { tags: ['b'] })];
  const tags = { a: { name: 'Alpha', tree_order: 5 }, b: { name: 'Beta', tree_order: 1 } };
  const res = pack(items, { groupByTag: true, tags });
  assert.deepEqual(res.lanes.map((l) => l.name), ['Beta', 'Alpha']);
});
