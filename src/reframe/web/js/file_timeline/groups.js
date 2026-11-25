// File timeline pure helpers (spec 10.1: file bucket grouping and the minimap's scaling are pure modules, unit-tested).
// No DOM, no store. Entries are plain objects {id, name, ext, kind (1 image / 0 other), day, dir,
// size, src, ...} already sorted by (timestamp, normalized path) by the server.

export const MIN_BUCKET_PX = 18;           // a column narrower than this merges days into a wider bucket
export const BUCKET_SIZES = [1, 2, 7, 14, 28, 56, 112, 224, 448, 896, 1792, 3584, 7168];
export const BUCKET_ORIGIN = 3;            // day number of Sunday 1970-01-04, so 7-day multiples are US weeks (R-06)
export const MIN_SPAN_DAYS = 7;
export const PAD_BEFORE_DAYS = 183;        // R-20 / R-150: at least today - 6 months ...
export const PAD_AFTER_DAYS = 730;         // ... and today + 2 years

// ------------------------------------------------------------------ types

const TYPE_EXT = {
  document: ['.pdf', '.doc', '.docx', '.docm', '.dot', '.dotx', '.xls', '.xlsx', '.xlsm', '.xlsb', '.csv', '.ppt', '.pptx',
    '.pptm', '.odt', '.ods', '.odp', '.rtf', '.txt', '.md', '.msg', '.eml', '.one', '.vsd', '.vsdx', '.xps', '.epub', '.html', '.htm'],
  video: ['.mp4', '.mov', '.avi', '.mkv', '.wmv', '.m4v', '.mpg', '.mpeg', '.webm', '.3gp', '.mts', '.m2ts'],
  audio: ['.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg', '.wma', '.opus', '.aiff'],
  archive: ['.zip', '.7z', '.rar', '.gz', '.tgz', '.bz2', '.xz', '.tar', '.cab', '.iso'],
};
const EXT_TYPE = new Map(Object.entries(TYPE_EXT).flatMap(([t, list]) => list.map((e) => [e, t])));

export const TYPE_GROUPS = [
  { id: 'media', label: 'Media' },
  { id: 'pdf', label: 'PDF' },
  { id: 'document', label: 'Documents' },
  { id: 'other', label: 'Other' },
];

export function typeGroupOf(ext, kind) {
  if (kind === 1) return 'image';
  return EXT_TYPE.get(ext) || 'other';
}

/** Short type label for glyphs: 'PDF', 'DOCX', '?' for dotless names. */
export function typeLabel(ext) {
  return ext ? ext.slice(1, 5).toUpperCase() : '\u2014';
}

export function formatSize(bytes) {
  if (!(bytes >= 0)) return '';
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let v = bytes / 1024, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v >= 100 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

export const badgeText = (hidden) => `+${hidden}`;

// ------------------------------------------------------------------ filter (R-143)

export const EMPTY_FILE_FILTER = Object.freeze({ text: '', types: [] });

export function normalizeFilter(f) {
  const src = f && typeof f === 'object' ? f : {};
  const legacy = { image: 'media', video: 'media', audio: 'media', archive: 'other' };
  const types = Array.isArray(src.types) ? src.types.map((t) => legacy[t] || t)
    .filter((t) => TYPE_GROUPS.some((g) => g.id === t)) : [];
  return {
    text: typeof src.text === 'string' ? src.text : '',
    types: [...new Set(types)],
  };
}

export function isFilterActive(f) {
  return !!(f.text.trim() || f.types.length);
}

export function compileFilter(f) {
  const tokens = f.text.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const types = new Set(f.types);
  return (e) => {
    const type = typeGroupOf(e.ext, e.kind);
    const matchesType = (types.has('media') && ['image', 'video', 'audio'].includes(type)) ||
      (types.has('pdf') && e.ext === '.pdf') ||
      (types.has('document') && type === 'document') ||
      (types.has('other') && (type === 'other' || type === 'archive'));
    if (types.size && !matchesType) return false;
    if (tokens.length) {
      const name = e.lname ?? e.name.toLowerCase();
      const dir = e.ldir ?? (e.dir || '').toLowerCase();
      for (const t of tokens) if (!name.includes(t) && !dir.includes(t)) return false;
    }
    return true;
  };
}

export function applyFilter(entries, f) {
  if (!isFilterActive(f)) return entries;
  const pred = compileFilter(f);
  return entries.filter(pred);
}

// ------------------------------------------------------------------ buckets (R-146)

/** Smallest bucket (days) whose column is at least `minPx` wide at this zoom. */
export function bucketDaysFor(ppd, minPx = MIN_BUCKET_PX) {
  for (const s of BUCKET_SIZES) if (s * ppd >= minPx) return s;
  return BUCKET_SIZES[BUCKET_SIZES.length - 1];
}

export const bucketIndex = (day, size) => Math.floor((day - BUCKET_ORIGIN) / size);
export const bucketStart = (k, size) => BUCKET_ORIGIN + k * size;

/**
 * Group time-ordered entries into fixed buckets of `size` days. Returns {size, list, byKey} where list is sorted by
 * bucket and each bucket is {k, start, end, items, images, others}; items keep the input (time) order.
 */
export function groupIntoBuckets(entries, size) {
  const byKey = new Map();
  const list = [];
  for (const e of entries) {
    const k = bucketIndex(e.day, size);
    let b = byKey.get(k);
    if (!b) {
      b = { k, start: bucketStart(k, size), end: bucketStart(k + 1, size), items: [], images: 0, others: 0 };
      byKey.set(k, b);
      list.push(b);
    }
    b.items.push(e);
    if (e.kind === 1) b.images++; else b.others++;
  }
  list.sort((a, b) => a.k - b.k);
  return { size, list, byKey };
}

/** First bucket in a sorted list whose end is after `day` (binary search). */
export function firstBucketFrom(list, day) {
  let lo = 0, hi = list.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (list[mid].end <= day) lo = mid + 1; else hi = mid;
  }
  return lo;
}

/** `k` deterministic, evenly spread indices out of `n` (all of them when k >= n). */
export function pickEvenly(n, k) {
  if (k >= n) return Array.from({ length: n }, (_, i) => i);
  if (k <= 0) return [];
  const out = [];
  for (let i = 0; i < k; i++) out.push(Math.min(n - 1, Math.floor(((i + 0.5) * n) / k)));
  return out;
}

export const COLUMN_DEFAULTS = Object.freeze({ pad: 3, gap: 3, thumbMax: 96, thumbMin: 14, rowH: 18, badgeH: 20 });

/**
 * Layout of one bucket column (R-146): image thumbnails in a grid, then other files as rows, then a count badge
 * when not everything fits in `maxH`. Cells are relative to the column's top-left; every cell lies inside
 * [0, colW] x [0, contentH] and cells never overlap, so drawn thumbnails (clipped to their cell) never extend
 * outside their hit targets. The representative set is deterministic (evenly spread through time).
 */
export function columnLayout(items, colW, maxH, opts = {}) {
  const o = { ...COLUMN_DEFAULTS, ...opts };
  const imgs = [], others = [];
  for (const e of items) (e.kind === 1 ? imgs : others).push(e);
  const iw = Math.max(1, colW - 2 * o.pad);
  const S = Math.max(Math.min(o.thumbMin, iw), Math.min(o.thumbMax, iw));
  const perRow = Math.max(1, Math.floor((iw + o.gap) / (S + o.gap)));
  const rowsFor = (n) => Math.ceil(n / perRow);
  const imgH = (n) => rowsFor(n) * (S + o.gap);
  const othH = (n) => n * (o.rowH + o.gap);
  const avail = Math.max(0, maxH - 2 * o.pad);

  let nImg = imgs.length, nOth = others.length;
  let hidden = 0;
  if (imgH(nImg) + othH(nOth) > avail) {
    const budget = Math.max(0, avail - (o.badgeH + o.gap));
    const needI = imgH(imgs.length), needO = othH(others.length);
    let bI = needI + needO > 0 ? Math.round((budget * needI) / (needI + needO)) : 0;
    if (imgs.length && bI < S + o.gap && budget >= S + o.gap) bI = S + o.gap;
    nImg = Math.min(imgs.length, Math.floor(bI / (S + o.gap)) * perRow);
    nOth = Math.min(others.length, Math.floor((budget - imgH(nImg)) / (o.rowH + o.gap)));
    if (nImg < imgs.length && nOth === others.length) {          // give unused space back to the images
      nImg = Math.min(imgs.length, Math.floor((budget - othH(nOth)) / (S + o.gap)) * perRow);
    }
    nImg = Math.max(0, nImg);
    nOth = Math.max(0, nOth);
    hidden = imgs.length + others.length - nImg - nOth;
  }
  const cells = [];
  let y = o.pad;
  const shownImg = pickEvenly(imgs.length, nImg).map((i) => imgs[i]);
  shownImg.forEach((e, i) => {
    const col = i % perRow, row = Math.floor(i / perRow);
    cells.push({ type: 'thumb', entry: e, x: o.pad + col * (S + o.gap), y: y + row * (S + o.gap), w: S, h: S });
  });
  y += imgH(shownImg.length);
  for (const i of pickEvenly(others.length, nOth)) {
    cells.push({ type: 'file', entry: others[i], x: o.pad, y, w: iw, h: o.rowH });
    y += o.rowH + o.gap;
  }
  if (hidden > 0) {
    cells.push({ type: 'badge', entry: null, hidden, x: o.pad, y, w: iw, h: o.badgeH });
    y += o.badgeH + o.gap;
  }
  return { cells, contentH: y + o.pad - o.gap, hidden, shown: shownImg.length + nOth, thumbPx: S, perRow };
}

/** Rectangle a thumbnail of natural size (w, h) is drawn in: fitted inside its cell, centred, aspect kept. */
export function fitInside(cell, w, h) {
  if (!(w > 0 && h > 0)) return { x: cell.x, y: cell.y, w: cell.w, h: cell.h };
  const s = Math.min(cell.w / w, cell.h / h);
  const dw = w * s, dh = h * s;
  return { x: cell.x + (cell.w - dw) / 2, y: cell.y + (cell.h - dh) / 2, w: dw, h: dh };
}

// ------------------------------------------------------------------ range, zoom, minimap scaling (R-150, R-151)

/** Data range of the File timeline: earliest to latest dated file, padded to at least today-6 months..today+2 years. */
export function dataRange(entries, today) {
  let lo = today - PAD_BEFORE_DAYS, hi = today + PAD_AFTER_DAYS;
  for (const e of entries) {
    if (e.day < lo) lo = e.day;
    if (e.day + 1 > hi) hi = e.day + 1;
  }
  return { lo, hi };
}

/** Clamp a view to [MIN_SPAN_DAYS, the whole data range] in span, keeping its centre. */
export function clampSpan(start, end, range) {
  const maxSpan = Math.max(MIN_SPAN_DAYS, range.hi - range.lo);
  let span = end - start;
  if (!(span >= MIN_SPAN_DAYS)) span = MIN_SPAN_DAYS;
  if (span > maxSpan) span = maxSpan;
  const mid = Number.isFinite(start + end) ? (start + end) / 2 : (range.lo + range.hi) / 2;
  return { start: mid - span / 2, end: mid + span / 2 };
}

/** Pixel columns of per-day file density for the minimap: images and other files counted separately. */
export function densityColumns(entries, lo, hi, width) {
  const n = Math.max(1, Math.ceil(width));
  const img = new Uint32Array(n), oth = new Uint32Array(n);
  const scale = width / (hi - lo);
  let max = 0;
  for (const e of entries) {
    const c = Math.floor((e.day + 0.5 - lo) * scale);
    if (c < 0 || c >= n) continue;
    if (e.kind === 1) img[c]++; else oth[c]++;
    const t = img[c] + oth[c];
    if (t > max) max = t;
  }
  return { img, oth, max, n };
}

/** Minimap frame rectangle; never narrower than minPx so its handles stay usable (R-25). */
export function frameRect(view, range, plotW, padX, minPx = 12) {
  const scale = plotW / (range.hi - range.lo);
  const x0 = padX + (view.start - range.lo) * scale, x1 = padX + (view.end - range.lo) * scale;
  const w = Math.max(minPx, x1 - x0);
  const cx = (x0 + x1) / 2;
  return { x: cx - w / 2, w };
}

/** Rows [first, last] to render in a virtualized list with fixed row height. */
export function visibleRows(scrollTop, viewportH, rowH, count, overscan = 6) {
  if (count <= 0) return { first: 0, last: -1 };
  const first = Math.max(0, Math.floor(scrollTop / rowH) - overscan);
  const last = Math.min(count - 1, Math.ceil((scrollTop + viewportH) / rowH) + overscan);
  return { first, last };
}

/** Compact client entries from the server's column-oriented payload (GET /api/files/entries). */
export function rowsToEntries(columns, rows) {
  const c = Object.fromEntries(columns.map((n, i) => [n, i]));
  return rows.map((r, i) => {
    const name = r[c.name], dir = r[c.dir] || '';
    return {
      id: r[c.id], name, ext: r[c.ext], kind: r[c.kind], day: r[c.day], src: r[c.src],
      size: r[c.size], online: r[c.online], root: r[c.root], pending: r[c.pending], dir, ord: i,
      lname: name.toLowerCase(), ldir: dir.toLowerCase(),
    };
  });
}
