// Deterministic first-fit interval packing (spec 4.1, R-02, R-15). Pure: no DOM, no store.
// The caller supplies measure(text) -> pixels (cached canvas measureText), so this module can be unit tested.
import { toDayNumber } from './dates.js';
import { labelMode, outsideLabelPx } from './labels.js';
import { bandMinPxPerDay } from './zoom.js';

const UNTAGGED = '';

class MinHeap {
  constructor(less) { this.a = []; this.less = less; }
  get size() { return this.a.length; }
  peek() { return this.a[0]; }
  push(v) {
    const a = this.a;
    let i = a.push(v) - 1;
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (!this.less(a[i], a[p])) break;
      [a[i], a[p]] = [a[p], a[i]];
      i = p;
    }
  }
  pop() {
    const a = this.a, top = a[0], last = a.pop();
    if (a.length) {
      a[0] = last;
      let i = 0;
      for (;;) {
        const l = 2 * i + 1, r = l + 1;
        let m = i;
        if (l < a.length && this.less(a[l], a[m])) m = l;
        if (r < a.length && this.less(a[r], a[m])) m = r;
        if (m === i) break;
        [a[i], a[m]] = [a[m], a[i]];
        i = m;
      }
    }
    return top;
  }
}

function compareRecords(a, b) {
  if (a.s !== b.s) return a.s - b.s;
  if (a.len !== b.len) return b.len - a.len;
  return a.item.id < b.item.id ? -1 : a.item.id > b.item.id ? 1 : 0;
}

function makeRecord(item, bandPpd, measure) {
  const s = toDayNumber(item.start_date);
  const e = toDayNumber(item.end_date) + 1;
  const label = labelMode((e - s) * bandPpd, bandPpd, measure);
  const ext = label === 'outside' ? e + Math.ceil(outsideLabelPx(item.title || '', measure) / bandPpd) : e;
  return { item, id: item.id, s, e, len: e - s, ext, label, row: 0, lane: null };
}

/**
 * First fit within one lane. Rows freed by an ending item go to a free heap ordered by row number,
 * so each item lands in the lowest-numbered row that is free at its start. Returns the row count.
 */
function packLane(records) {
  const busy = new MinHeap((x, y) => x[0] < y[0] || (x[0] === y[0] && x[1] < y[1]));   // [end, row]
  const free = new MinHeap((x, y) => x < y);                                         // row numbers
  let rows = 0;
  for (const r of records) {
    while (busy.size && busy.peek()[0] <= r.s) free.push(busy.pop()[1]);
    r.row = free.size ? free.pop() : rows++;
    busy.push([r.ext, r.row]);
  }
  return rows;
}

function laneKey(item) { return item.tags && item.tags.length ? item.tags[0] : UNTAGGED; }

/** Lane order: tag-tree order, then name; "untagged" last (spec 4.1 step 2). */
function buildLanes(records, tags) {
  const byKey = new Map();
  for (const r of records) {
    const k = laneKey(r.item);
    if (!byKey.has(k)) byKey.set(k, []);
    byKey.get(k).push(r);
  }
  const nameOf = (k) => (k === UNTAGGED ? 'untagged' : tags[k]?.name ?? k);
  const treeOf = (k) => (Number.isFinite(tags[k]?.tree_order) ? tags[k].tree_order : Infinity);
  const keys = [...byKey.keys()].sort((a, b) => {
    if (a === UNTAGGED || b === UNTAGGED) return a === UNTAGGED ? 1 : -1;
    const ta = treeOf(a), tb = treeOf(b);
    if (ta !== tb) return ta < tb ? -1 : 1;
    const x = nameOf(a).toLowerCase(), y = nameOf(b).toLowerCase();
    return x < y ? -1 : x > y ? 1 : a < b ? -1 : 1;
  });
  return keys.map((k) => ({ key: k, tagId: k === UNTAGGED ? null : k, name: nameOf(k), records: byKey.get(k) }));
}

/** Strict Gantt: one item per row in sort-key order, so a later start is always lower (R-17). */
function packStrict(records) {
  records.forEach((r, i) => { r.row = i; });
  return records.length;
}

/**
 * @param items   visible items (filter and calendar visibility already applied)
 * @param opts    {band, measure, groupByTag?, tags?, mode?: 'compact'|'strict'}
 * @returns {rowOf: Map(id->row), rowCount, entries: Map(id->entry), rows: entry[][] (per row, by start),
 *           lanes: [{key, tagId, name, headerRow, firstRow, rowCount, count}], headerRows: Set(row), mode}
 * rowCount includes lane header rows in group mode. Entries: {item, id, s, e (exclusive), ext, label, row, lane}.
 * This result is the only layout the minimap draws (spec 4.1 step 8).
 */
export function packItems(items, { band, measure, groupByTag = false, tags = {}, mode = 'compact' }) {
  const strict = mode === 'strict';
  const bandPpd = bandMinPxPerDay(band);
  const records = items.map((it) => makeRecord(it, bandPpd, measure)).sort(compareRecords);
  if (strict) for (const r of records) r.ext = r.e;   // outside labels never affect rows in strict mode
  const rowOf = new Map();
  const entries = new Map();
  const rows = [];
  const lanes = [];
  const headerRows = new Set();

  const place = (recs, base, lane) => {
    const n = strict ? packStrict(recs) : packLane(recs);
    for (let i = 0; i < n; i++) rows[base + i] = [];
    for (const r of recs) {
      r.row += base;
      r.lane = lane;
      rowOf.set(r.id, r.row);
      entries.set(r.id, r);
      rows[r.row].push(r);
    }
    return n;
  };

  if (!groupByTag) {
    const rowCount = place(records, 0, null);
    return { rowOf, rowCount, entries, rows, lanes, headerRows, mode: strict ? 'strict' : 'compact' };
  }
  let next = 0;
  for (const lane of buildLanes(records, tags)) {
    const info = { key: lane.key, tagId: lane.tagId, name: lane.name, headerRow: next, firstRow: next + 1, rowCount: 0, count: lane.records.length };
    rows[next] = [];
    headerRows.add(next);
    info.rowCount = place(lane.records, next + 1, info);
    next += 1 + info.rowCount;
    lanes.push(info);
  }
  return { rowOf, rowCount: next, entries, rows, lanes, headerRows, mode: strict ? 'strict' : 'compact' };
}

/** First index in an entry list (sorted by start, extents disjoint) whose extent reaches `day`. */
export function firstReaching(list, day) {
  let lo = 0, hi = list.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (list[mid].ext > day) hi = mid; else lo = mid + 1;
  }
  return lo;
}
