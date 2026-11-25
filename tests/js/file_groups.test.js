import test from 'node:test';
import assert from 'node:assert/strict';
import {
  applyFilter, badgeText, bucketDaysFor, clampSpan, columnLayout, compileFilter, dataRange, densityColumns,
  firstBucketFrom, fitInside, frameRect, groupIntoBuckets, normalizeFilter, pickEvenly, rowsToEntries, typeGroupOf,
  visibleRows, MIN_BUCKET_PX,
} from '../../src/reframe/web/js/file_timeline/groups.js';
import { toDayNumber } from '../../src/reframe/web/js/dates.js';
import { createFileStore } from '../../src/reframe/web/js/file_timeline/store.js';

const D = toDayNumber;
let seq = 0;
function entry(day, kind, tags = [], name = null, ext = kind ? '.jpg' : '.txt', dir = '') {
  seq += 1;
  const n = name || `f${seq}${ext}`;
  return { id: `id${seq}`, name: n, ext, kind, day, tags, dir, lname: n.toLowerCase(), ldir: dir.toLowerCase() };
}

test('bucket size grows with zoom-out and keeps columns at least MIN_BUCKET_PX wide', () => {
  assert.equal(bucketDaysFor(100), 1);
  assert.equal(bucketDaysFor(10), 2);
  assert.equal(bucketDaysFor(5), 7);
  for (const ppd of [0.01, 0.3, 1, 3, 9, 31, 300]) assert.ok(bucketDaysFor(ppd) * ppd >= MIN_BUCKET_PX || bucketDaysFor(ppd) === 7168);
});

test('weekly buckets start on Sunday and grouping is deterministic', () => {
  const es = [entry(D('2026-11-22'), 1), entry(D('2026-11-24'), 0), entry(D('2026-11-28'), 1), entry(D('2026-11-29'), 0)];
  const g = groupIntoBuckets(es, 7);
  assert.equal(g.list.length, 2);
  assert.equal(new Date(g.list[0].start * 86400000).getUTCDay(), 0);
  assert.deepEqual(g.list.map((b) => [b.images, b.others]), [[2, 1], [0, 1]]);
  assert.deepEqual(groupIntoBuckets(es, 7).list.map((b) => b.items.map((e) => e.id)), g.list.map((b) => b.items.map((e) => e.id)));
  assert.equal(firstBucketFrom(g.list, D('2026-11-29')), 1);
  assert.equal(firstBucketFrom(g.list, D('2026-11-20')), 0);
});

test('representatives are evenly spread and deterministic', () => {
  assert.deepEqual(pickEvenly(5, 10), [0, 1, 2, 3, 4]);
  assert.deepEqual(pickEvenly(100, 4), [12, 37, 62, 87]);
  assert.deepEqual(pickEvenly(100, 4), pickEvenly(100, 4));
  assert.deepEqual(pickEvenly(3, 0), []);
});

test('a dense day shows representatives plus a count badge; counts add up', () => {
  const day = D('2026-03-01');
  const items = [...Array.from({ length: 230 }, () => entry(day, 1)), ...Array.from({ length: 20 }, () => entry(day, 0))];
  const lay = columnLayout(items, 120, 400);
  const badge = lay.cells.filter((c) => c.type === 'badge');
  assert.equal(badge.length, 1);
  assert.equal(lay.shown + lay.hidden, 250);
  assert.equal(badge[0].hidden, lay.hidden);
  assert.equal(badgeText(lay.hidden), `+${lay.hidden}`);
  assert.ok(lay.cells.some((c) => c.type === 'thumb') && lay.cells.some((c) => c.type === 'file'));
  assert.ok(lay.contentH <= 400);
  assert.deepEqual(columnLayout(items, 120, 400).cells.map((c) => c.entry?.id), lay.cells.map((c) => c.entry?.id));
});

test('a day with zero images and a small day show everything without a badge', () => {
  const day = D('2026-03-02');
  const docs = Array.from({ length: 4 }, () => entry(day, 0));
  const lay = columnLayout(docs, 200, 600);
  assert.equal(lay.hidden, 0);
  assert.equal(lay.cells.length, 4);
  assert.ok(lay.cells.every((c) => c.type === 'file'));
});

test('cells stay inside the column and never overlap; fitted thumbnails stay inside their cell', () => {
  const day = D('2026-03-03');
  const items = [...Array.from({ length: 57 }, () => entry(day, 1)), ...Array.from({ length: 9 }, () => entry(day, 0))];
  for (const [w, h] of [[30, 120], [64, 300], [210, 2000], [400, 90]]) {
    const lay = columnLayout(items, w, h);
    for (const c of lay.cells) {
      assert.ok(c.x >= 0 && c.y >= 0 && c.x + c.w <= w + 1e-9, `inside width ${w}`);
      assert.ok(c.y + c.h <= lay.contentH + 1e-9);
      for (const [nw, nh] of [[4000, 3000], [100, 900], [1, 1]]) {
        const r = fitInside(c, nw, nh);
        assert.ok(r.x >= c.x - 1e-9 && r.y >= c.y - 1e-9 && r.x + r.w <= c.x + c.w + 1e-9 && r.y + r.h <= c.y + c.h + 1e-9);
      }
    }
    for (let i = 0; i < lay.cells.length; i++) {
      for (let j = i + 1; j < lay.cells.length; j++) {
        const a = lay.cells[i], b = lay.cells[j];
        const overlap = a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
        assert.ok(!overlap, 'cells overlap');
      }
    }
  }
});

test('filter: text and types combine, counts consistent with the browser list', () => {
  const es = [
    entry(1, 1, [0, 1], 'IMG_1.jpg', '.jpg', 'Trips\\Rome'),
    entry(1, 1, [0], 'IMG_2.jpg', '.jpg', 'Trips'),
    entry(2, 0, [2], 'Budget.xlsx', '.xlsx', 'Work'),
    entry(2, 0, [], 'README', '', ''),
  ];
  const n = (f) => applyFilter(es, normalizeFilter(f)).length;
  assert.equal(n({}), 4);
  assert.equal(n({ text: 'rome' }), 1);                                   // matches the folder path
  assert.equal(n({ text: 'img trips' }), 2);
  assert.equal(n({ types: ['document'] }), 1);
  assert.equal(n({ types: ['other'] }), 1);
  assert.equal(n({ text: 'trips', types: ['media'] }), 2);
  assert.equal(n({ text: 'trips', types: ['document'] }), 0);
  assert.equal(typeGroupOf('.pdf', 0), 'document');
  // a dense bucket: the badge + shown equals the filtered count used by the day browser
  const day = 5;
  const many = Array.from({ length: 300 }, (_, i) => entry(day, i % 3 ? 1 : 0, i % 2 ? [0] : [2]));
  const f = normalizeFilter({ types: ['media'] });
  const visible = applyFilter(many, f);
  const bucket = groupIntoBuckets(visible, 1).list[0];
  const lay = columnLayout(bucket.items, 100, 300);
  assert.equal(lay.shown + lay.hidden, visible.length);
  assert.equal(bucket.items.length, many.filter(compileFilter(f)).length);
});

test('restoring legacy folder filters ignores tags but retains text and file types', () => {
  const store = createFileStore(() => 10);
  store.applyViewState({ filter: { tags: ['gone'], mode: 'and', untagged: true, text: 'trip', types: ['image'] } });
  assert.deepEqual(store.state.filter, { text: 'trip', types: ['media'] });
  store.setData({ columns: ['id', 'name', 'ext', 'kind', 'day', 'src', 'size', 'online', 'root', 'pending', 'dir'],
    rows: [['a', 'photo.jpg', '.jpg', 1, 10, 0, 5, 0, 0, 0, 'Trips']] });
  assert.equal(store.state.filtered.length, 1);
  store.applyViewState({ filter: { tags: ['gone'], untagged: true } });
  assert.equal(store.filterActive(), false);
  assert.equal(store.state.filtered.length, 1);
});

test('media, PDF, documents and other filters include the intended files', () => {
  const es = [
    entry(1, 1, [], 'photo.jpg', '.jpg'),
    entry(1, 0, [], 'clip.mp4', '.mp4'),
    entry(1, 0, [], 'audio.mp3', '.mp3'),
    entry(1, 0, [], 'report.pdf', '.pdf'),
    entry(1, 0, [], 'notes.txt', '.txt'),
    entry(1, 0, [], 'backup.zip', '.zip'),
    entry(1, 0, [], 'unknown.bin', '.bin'),
  ];
  const names = (types) => applyFilter(es, normalizeFilter({ types })).map((e) => e.name);
  assert.deepEqual(names(['media']), ['photo.jpg', 'clip.mp4', 'audio.mp3']);
  assert.deepEqual(names(['pdf']), ['report.pdf']);
  assert.deepEqual(names(['document']), ['report.pdf', 'notes.txt']);
  assert.deepEqual(names(['other']), ['backup.zip', 'unknown.bin']);
  assert.deepEqual(names(['pdf', 'document']), ['report.pdf', 'notes.txt']);
  assert.deepEqual(normalizeFilter({ types: ['image', 'video', 'audio', 'archive'] }).types, ['media', 'other']);
});

test('range, span clamp and minimap scaling', () => {
  const today = D('2026-11-24');
  const r0 = dataRange([], today);
  assert.deepEqual(r0, { lo: today - 183, hi: today + 730 });
  const old = D('2001-01-01');
  const r = dataRange([entry(old, 0)], today);
  assert.equal(r.lo, old);
  assert.ok(r.hi - r.lo > 5 * 366);                                    // may exceed the Gantt 5-year limit (R-150)
  const v = clampSpan(today, today + 3, r);
  assert.equal(v.end - v.start, 7);
  const wide = clampSpan(r.lo - 9999, r.hi + 9999, r);
  assert.equal(wide.end - wide.start, r.hi - r.lo);
  const es = [entry(today, 1), entry(today, 1), entry(today, 0), entry(today + 400, 0)];
  const d = densityColumns(es, r.lo, r.hi, 500);
  const c = Math.floor((today + 0.5 - r.lo) * (500 / (r.hi - r.lo)));
  assert.equal(d.img[c], 2);
  assert.equal(d.oth[c], 1);
  assert.equal(d.max, 3);
  const fr = frameRect({ start: today, end: today + 7 }, r, 500, 8, 12);
  assert.ok(fr.w >= 12);
  const big = frameRect({ start: r.lo, end: r.hi }, r, 500, 8, 12);
  assert.ok(Math.abs(big.x - 8) < 1e-9 && Math.abs(big.w - 500) < 1e-9);
});

test('virtualized rows and payload decoding', () => {
  assert.deepEqual(visibleRows(0, 100, 40, 1000, 2), { first: 0, last: 5 });
  assert.deepEqual(visibleRows(4000, 100, 40, 102, 2), { first: 98, last: 101 });
  assert.deepEqual(visibleRows(0, 100, 40, 0), { first: 0, last: -1 });
  const cols = ['id', 'name', 'ext', 'kind', 'day', 'src', 'size', 'online', 'root', 'pending', 'dir'];
  const es = rowsToEntries(cols, [['a', 'X.JPG', '.jpg', 1, 10, 0, 5, 0, 0, 1, 'Trips']]);
  assert.equal(es[0].lname, 'x.jpg');
  assert.equal(es[0].ldir, 'trips');
  assert.equal(es[0].size, 5);
});

test('10,000 entries: filter + bucketing timing (recorded, not a budget)', () => {
  const today = D('2026-11-24');
  const es = [];
  let s = 1;
  const rnd = () => { s = (s * 16807) % 2147483647; return s / 2147483647; };
  for (let i = 0; i < 10000; i++) {
    const day = i % 10 === 0 ? today - 30 : today - Math.floor(rnd() * 4000);
    es.push(entry(day, rnd() < 0.6 ? 1 : 0, [Math.floor(rnd() * 20)]));
  }
  es.sort((a, b) => a.day - b.day);
  const t0 = performance.now();
  const vis = applyFilter(es, normalizeFilter({ types: ['media'], text: 'f' }));
  const t1 = performance.now();
  const g = groupIntoBuckets(es, bucketDaysFor(1500 / 42));
  const t2 = performance.now();
  let cells = 0;
  for (const b of g.list) cells += columnLayout(b.items, 36, 1800).cells.length;
  const t3 = performance.now();
  const dens = densityColumns(es, today - 4100, today + 730, 1600);
  const t4 = performance.now();
  console.log(`[files perf js] filter ${(t1 - t0).toFixed(1)} ms (${vis.length} match); bucket ${(t2 - t1).toFixed(1)} ms `
    + `(${g.list.length} buckets); layout all columns ${(t3 - t2).toFixed(1)} ms (${cells} cells); minimap density ${(t4 - t3).toFixed(1)} ms`);
  assert.equal(g.list.reduce((a, b) => a + b.items.length, 0), 10000);
  assert.ok(dens.max >= 1000);
});
