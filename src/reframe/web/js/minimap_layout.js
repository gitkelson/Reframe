// Pure minimap scaling (R-21, spec 4.1 step 8): maps the main chart's packing result onto the minimap strip.
// The minimap never packs on its own; it scales rows, lanes and entries of the layout it is given.

/**
 * Vertical mapping of `rowCount` rows onto `height` CSS pixels at device pixel ratio `dpr`.
 * When there are more rows than device pixels, consecutive rows share a device-pixel row.
 * Returns {y(row) -> top in CSS px, h -> bar height in CSS px (>= one device pixel), compressed}.
 */
export function rowScale(rowCount, height, dpr = 1, maxRowPx = 6) {
  const rows = Math.max(1, rowCount);
  const devH = Math.max(1, Math.floor(height * dpr));
  const per = Math.min(maxRowPx * dpr, devH / rows);
  const compressed = per < 1;
  if (!compressed) {
    const ph = Math.floor(per);
    return { compressed, h: Math.max(1, ph - (ph > 2 ? 1 : 0)) / dpr, y: (row) => (row * Math.floor(per)) / dpr };
  }
  return { compressed, h: 1 / dpr, y: (row) => Math.floor((row * devH) / rows) / dpr };
}

/** Row ranges of each lane [{firstRow, lastRow (exclusive), key}] in drawing order (used by the histogram). */
export function laneBands(layout) {
  if (!layout.lanes || !layout.lanes.length) return [{ firstRow: 0, lastRow: Math.max(1, layout.rowCount), key: null }];
  return layout.lanes.map((l) => ({ firstRow: l.headerRow, lastRow: l.firstRow + l.rowCount, key: l.key }));
}

/** Row index of every entry, so a test can prove the minimap uses the chart's rows unchanged. */
export function rowMapping(layout) {
  const out = new Map();
  for (const en of layout.entries.values()) out.set(en.id, en.row);
  return out;
}
