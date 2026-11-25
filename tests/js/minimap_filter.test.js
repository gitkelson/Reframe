// Minimap parity (R-21) and filter semantics (R-34, R-131): pure modules only.
import test from 'node:test';
import assert from 'node:assert/strict';
import { packItems } from '../../src/reframe/web/js/packing.js';
import { laneBands, rowMapping, rowScale } from '../../src/reframe/web/js/minimap_layout.js';
import { layoutItems, matches, searchMatches, selectAllTags, selectNoTags, EMPTY_FILTER } from '../../src/reframe/web/js/filter.js';

const measure = (t) => t.length * 7;
const it = (id, s, e, tags = [], extra = {}) => ({ id, title: id, kind: 'plan', notes: '', origin: 'local', start_date: s, end_date: e, tags, ...extra });
const ITEMS = [
  it('a', '2026-01-01', '2026-01-03', ['x']), it('b', '2026-01-02', '2026-01-02', ['y']),
  it('c', '2026-01-05', '2026-01-06'), it('d', '2026-01-01', '2026-01-01', ['x', 'y']),
];
const TAGS = { x: { name: 'X', tree_order: 0 }, y: { name: 'Y', tree_order: 1 } };

test('minimap uses the chart rows unchanged in every mode', () => {
  for (const mode of ['compact', 'strict']) {
    for (const groupByTag of [false, true]) {
      const layout = packItems(ITEMS, { band: 0, measure, mode, groupByTag, tags: TAGS });
      const rows = rowMapping(layout);
      for (const en of layout.entries.values()) assert.equal(rows.get(en.id), en.row);
      const bands = laneBands(layout);
      assert.equal(bands.at(-1).lastRow, layout.rowCount);
    }
  }
});

test('row scale keeps order and compresses rows when there are more rows than pixels', () => {
  const sc = rowScale(10, 56, 1);
  assert.ok(!sc.compressed && sc.y(1) > sc.y(0));
  const dense = rowScale(500, 56, 2);
  assert.ok(dense.compressed);
  assert.equal(dense.h, 0.5);
  for (let r = 1; r < 500; r++) assert.ok(dense.y(r) >= dense.y(r - 1));
  assert.ok(dense.y(499) < 56);
});

test('All selects every non-retired tag plus Untagged; None clears; OR keeps an item while any tag remains', () => {
  const list = [{ id: 'x' }, { id: 'y' }, { id: 'old', retired: true }];
  const all = selectAllTags(list);
  assert.deepEqual(all, { tags: ['x', 'y'], untagged: true });
  const f = { ...EMPTY_FILTER, ...all };
  assert.deepEqual(layoutItems(ITEMS, f, TAGS).map((i) => i.id), ['a', 'b', 'c', 'd']);
  const minusX = { ...f, tags: ['y'] };
  assert.deepEqual(layoutItems(ITEMS, minusX, TAGS).map((i) => i.id), ['b', 'c', 'd']);
  assert.deepEqual(selectNoTags(), { tags: [], untagged: false });
});

test('Untagged chip is ignored together with other chips in AND mode', () => {
  const and = { ...EMPTY_FILTER, tagMode: 'and', tags: ['x'], untagged: true };
  assert.deepEqual(ITEMS.filter((i) => matches(i, and, TAGS)).map((i) => i.id), ['a', 'd']);
  const onlyUntagged = { ...EMPTY_FILTER, untagged: true };
  assert.deepEqual(ITEMS.filter((i) => matches(i, onlyUntagged, TAGS)).map((i) => i.id), ['c']);
});

test('hidden calendars are excluded like a filter, local items never; search counts hidden matches', () => {
  const imported = it('m', '2026-01-02', '2026-01-02', [], { origin: 'outlook', source_id: 'cal', title: 'Standup' });
  const cals = { cal: { visible: false } };
  assert.deepEqual(layoutItems([...ITEMS, imported], EMPTY_FILTER, TAGS, cals).map((i) => i.id), ['a', 'b', 'c', 'd']);
  const r = searchMatches([...ITEMS, imported], { ...EMPTY_FILTER, text: 'standup' }, TAGS, cals);
  assert.equal(r.visible.length, 0);
  assert.equal(r.hiddenCalendars, 1);
});
