// Painting and hit testing for the File timeline canvas. Pure drawing from a model `m` built by index.js; colours
// come only from CSS custom properties of the active theme.
import { readTheme } from '../canvas_theme.js';
import { rulerRows, weekendSpans } from '../zoom.js';
import { badgeText, columnLayout, firstBucketFrom, fitInside, typeLabel } from './groups.js';

export const RULER_H = 44;
export const HEADER_H = 18;
export const BODY_TOP = RULER_H + HEADER_H;
export const MAX_SCREENS = 2;          // a column may scroll at most this many screens before it shows a count badge

const EXTRA = {
  'minimap-files-image': ['tag-1'],
  'minimap-files-other': ['minimap-bar'],
  'file-entry': ['bar-untagged'],
  'file-entry-fg': ['bar-untagged-fg'],
  'file-selected': ['focus-ring'],
  'file-placeholder-bg': ['weekend-shade'],
  'file-placeholder-fg': ['text-secondary'],
  'file-badge-bg': ['active-bg', 'ruler-bg'],
  'file-badge-fg': ['active-fg', 'ruler-text'],
  'lane-separator': ['grid-line-strong'],
};

/** Base canvas theme plus the File timeline tokens, each with a fallback to an existing variable. */
export function readFileTheme() {
  const theme = readTheme();
  const cs = getComputedStyle(document.documentElement);
  const get = (n) => cs.getPropertyValue(`--${n}`).trim();
  for (const [name, fallbacks] of Object.entries(EXTRA)) {
    let v = get(name);
    for (const f of fallbacks) { if (v) break; v = theme[f] && theme[f] !== 'currentColor' ? theme[f] : get(f); }
    theme[name] = v || theme.text;
  }
  return theme;
}

export const xOf = (m, day) => (day - m.view.start) * m.ppd;
export const maxColumnH = (m) => Math.max(160, m.H - BODY_TOP) * MAX_SCREENS;

/** Visible columns with their (cached) layouts. Also computes the tallest visible column for vertical scrolling. */
export function visibleColumns(m) {
  const list = m.groups.list;
  const out = [];
  const colW = m.groups.size * m.ppd;
  const maxH = maxColumnH(m);
  let tallest = 0;
  for (let i = firstBucketFrom(list, m.view.start); i < list.length && list[i].start < m.view.end; i++) {
    const b = list[i];
    const key = `${m.layoutVersion}|${m.groups.size}|${Math.round(colW)}|${Math.round(maxH)}|${b.k}`;
    let lay = m.layoutCache.get(b.k);
    if (!lay || lay.key !== key) {
      lay = { key, ...columnLayout(b.items, Math.round(colW), maxH) };
      m.layoutCache.set(b.k, lay);
    }
    tallest = Math.max(tallest, lay.contentH);
    out.push({ bucket: b, x: Math.round(xOf(m, b.start)), w: Math.round(colW), layout: lay });
  }
  m.contentH = tallest;
  return out;
}

export const cellKey = (cell, bucket) => (cell.type === 'badge' ? `badge:${bucket.k}` : cell.entry.id);

// ------------------------------------------------------------------ hit test

export function hitTest(m, x, y) {
  const day = m.view.start + x / m.ppd;
  if (y < RULER_H) return { zone: 'ruler', day };
  const col = (m.columns || []).find((c) => x >= c.x && x < c.x + c.w);
  if (!col) return { zone: 'empty', day };
  if (y < BODY_TOP) return { zone: 'header', day, col };
  const cy = y - BODY_TOP + m.scrollY, cx = x - col.x;
  for (const cell of col.layout.cells) {
    if (cx >= cell.x && cx < cell.x + cell.w && cy >= cell.y && cy < cell.y + cell.h) return { zone: 'cell', day, col, cell };
  }
  return { zone: 'column', day, col };
}

// ------------------------------------------------------------------ painting

function drawBackground(ctx, m) {
  const t = m.theme;
  ctx.fillStyle = t['canvas-bg'];
  ctx.fillRect(0, 0, m.W, m.H);
  ctx.fillStyle = t['weekend-shade'];
  for (const [s, e] of weekendSpans(m.view.start, m.view.end, m.ppd)) {
    const x0 = Math.round(xOf(m, s));
    ctx.fillRect(x0, RULER_H, Math.max(1, Math.round(xOf(m, e)) - x0), m.H - RULER_H);
  }
  const x0 = Math.round(xOf(m, m.today)), x1 = Math.round(xOf(m, m.today + 1));
  if (x1 >= 0 && x0 <= m.W) {           // today is the shaded day cell, never a line (R-03)
    ctx.fillStyle = t['today-shade'];
    ctx.fillRect(x0, RULER_H, Math.max(2, x1 - x0), m.H - RULER_H);
  }
  const lines = (ticks, color, minGap) => {
    if ((ticks[0] ? (ticks[0].end - ticks[0].start) * m.ppd : 0) < minGap) return;
    ctx.strokeStyle = color;
    ctx.beginPath();
    for (const k of ticks) {
      const x = Math.round(xOf(m, k.start)) + 0.5;
      ctx.moveTo(x, RULER_H);
      ctx.lineTo(x, m.H);
    }
    ctx.stroke();
  };
  ctx.lineWidth = 1;
  lines(m.ruler.minor, t['grid-line'], 12);
  lines(m.ruler.major, t['grid-line-strong'], 0);
}

function elide(text, maxW, measure) {
  if (maxW <= 4) return '';
  if (measure(text) <= maxW) return text;
  let lo = 0, hi = text.length;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (measure(`${text.slice(0, mid)}\u2026`) <= maxW) lo = mid; else hi = mid - 1;
  }
  return lo ? `${text.slice(0, lo)}\u2026` : '';
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  if (w < 6 || h < 6 || r < 1) ctx.rect(x, y, w, h);
  else ctx.roundRect(x, y, w, h, Math.min(r, w / 2, h / 2));
}

function drawHeader(ctx, m, col) {
  const t = m.theme;
  ctx.fillStyle = t['lane-header-bg'];
  ctx.fillRect(col.x + 1, RULER_H, col.w - 1, HEADER_H - 1);
  const n = col.bucket.items.length;
  const text = col.w >= 70 ? `${n} file${n === 1 ? '' : 's'}` : String(n);
  if (m.headMeasure(text) + 4 > col.w) return;
  ctx.fillStyle = t['lane-header-text'];
  ctx.fillText(text, col.x + 3, RULER_H + HEADER_H / 2 + 0.5);
}

function drawPlaceholder(ctx, m, r, label) {
  const t = m.theme;
  ctx.fillStyle = t['file-placeholder-bg'];
  ctx.fillRect(r.x, r.y, r.w, r.h);
  ctx.strokeStyle = t['file-placeholder-fg'];
  ctx.lineWidth = 1;
  ctx.strokeRect(r.x + 0.5, r.y + 0.5, r.w - 1, r.h - 1);
  if (label && r.w >= 36) {
    ctx.fillStyle = t['file-placeholder-fg'];
    const s = elide(label, r.w - 6, m.measure);
    if (s) ctx.fillText(s, r.x + 3, r.y + r.h / 2 + 0.5);
  }
}

function drawThumb(ctx, m, cell, r) {
  const e = cell.entry;
  const th = e.online ? { status: 'placeholder', reason: 'online-only' } : m.thumbs.get(e.id);
  ctx.save();
  ctx.beginPath();
  ctx.rect(r.x, r.y, r.w, r.h);          // a thumbnail never extends outside its hit target (R-146)
  ctx.clip();
  if (th && th.status === 'ok') {
    ctx.fillStyle = m.theme['file-placeholder-bg'];
    ctx.fillRect(r.x, r.y, r.w, r.h);
    const f = fitInside(r, th.w, th.h);
    ctx.drawImage(th.bitmap, f.x, f.y, f.w, f.h);
  } else {
    drawPlaceholder(ctx, m, r, th ? th.reason : '');
  }
  if (e.pending && m.mode === 'created') {     // provisional placement until the date taken is read
    ctx.setLineDash([3, 2]);
    ctx.strokeStyle = m.theme['file-placeholder-fg'];
    ctx.strokeRect(r.x + 1.5, r.y + 1.5, r.w - 3, r.h - 3);
    ctx.setLineDash([]);
  }
  ctx.restore();
}

function drawFileRow(ctx, m, cell, r) {
  const t = m.theme;
  const e = cell.entry;
  roundRect(ctx, r.x, r.y, r.w, r.h, 3);
  ctx.fillStyle = t['file-entry'];
  ctx.fill();
  ctx.save();
  ctx.beginPath();
  ctx.rect(r.x, r.y, r.w, r.h);
  ctx.clip();
  ctx.fillStyle = t['file-entry-fg'];
  const label = typeLabel(e.ext);
  const gw = Math.min(r.w - 2, m.measure(label) + 6);
  if (gw > 8) {
    ctx.globalAlpha = 0.18;
    ctx.fillRect(r.x + 1, r.y + 1, gw, r.h - 2);
    ctx.globalAlpha = 1;
    const g = elide(label, gw - 4, m.measure);
    if (g) ctx.fillText(g, r.x + 4, r.y + r.h / 2 + 0.5);
  }
  const room = r.w - gw - 8;
  const name = room > 14 ? elide(e.name, room, m.measure) : '';
  if (name) ctx.fillText(name, r.x + gw + 5, r.y + r.h / 2 + 0.5);
  if (e.online) {
    ctx.setLineDash([2, 2]);
    ctx.strokeStyle = t['file-entry-fg'];
    ctx.strokeRect(r.x + 0.5, r.y + 0.5, r.w - 1, r.h - 1);
    ctx.setLineDash([]);
  }
  ctx.restore();
}

function drawBadge(ctx, m, cell, r) {
  const t = m.theme;
  roundRect(ctx, r.x, r.y, r.w, r.h, 4);
  ctx.fillStyle = t['file-badge-bg'];
  ctx.fill();
  ctx.fillStyle = t['file-badge-fg'];
  const text = elide(badgeText(cell.hidden), r.w - 4, m.measure);
  if (text) ctx.fillText(text, r.x + (r.w - m.measure(text)) / 2, r.y + r.h / 2 + 0.5);
}

function outline(ctx, r, color, width, dash) {
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  if (dash) ctx.setLineDash(dash);
  ctx.strokeRect(r.x + width / 2, r.y + width / 2, r.w - width, r.h - width);
  ctx.restore();
}

function drawColumns(ctx, m) {
  const t = m.theme;
  ctx.save();
  ctx.beginPath();
  ctx.rect(0, BODY_TOP, m.W, m.H - BODY_TOP);
  ctx.clip();
  ctx.font = `11px ${t.fontUi}`;
  ctx.textBaseline = 'middle';
  const top = BODY_TOP - m.scrollY;
  for (const col of m.columns) {
    for (const cell of col.layout.cells) {
      const r = { x: col.x + cell.x, y: top + cell.y, w: cell.w, h: cell.h };
      if (r.y + r.h < BODY_TOP || r.y > m.H) continue;
      if (cell.type === 'thumb') drawThumb(ctx, m, cell, r);
      else if (cell.type === 'file') drawFileRow(ctx, m, cell, r);
      else drawBadge(ctx, m, cell, r);
      const key = cellKey(cell, col.bucket);
      if (cell.entry && cell.entry.id === m.selectedId) outline(ctx, r, t['file-selected'], 2);
      if (m.focused && key === m.focusKey) outline(ctx, { x: r.x - 0, y: r.y, w: r.w, h: r.h }, t['focus-ring'], 2, [3, 2]);
      else if (key === m.hoverKey) outline(ctx, r, t['file-selected'], 1);
    }
  }
  ctx.restore();
  ctx.save();
  ctx.font = `600 10px ${t.fontUi}`;
  ctx.textBaseline = 'middle';
  ctx.strokeStyle = t['lane-separator'];
  for (const col of m.columns) drawHeader(ctx, m, col);
  ctx.restore();
}

function drawScrollbar(ctx, m) {
  const bodyH = m.H - BODY_TOP;
  if (m.contentH <= bodyH) return;
  const h = Math.max(24, bodyH * (bodyH / m.contentH));
  const y = BODY_TOP + (m.scrollY / (m.contentH - bodyH)) * (bodyH - h);
  ctx.globalAlpha = 0.5;
  ctx.fillStyle = m.theme['grid-line-strong'];
  ctx.fillRect(m.W - 5, y, 3, h);
  ctx.globalAlpha = 1;
}

function drawTickRow(ctx, m, ticks, y, h, color, sticky) {
  const t = m.theme;
  ctx.strokeStyle = t['ruler-line'];
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (const k of ticks) {
    const x = Math.round(xOf(m, k.start)) + 0.5;
    ctx.moveTo(x, y);
    ctx.lineTo(x, y + h);
  }
  ctx.stroke();
  ctx.fillStyle = color;
  for (const k of ticks) {
    const x0 = xOf(m, k.start), x1 = xOf(m, k.end);
    const left = sticky ? Math.max(x0, 0) : x0;
    const label = k.labels.find((l) => m.rulerMeasure(l) <= x1 - left - 8);
    if (!label) continue;
    const x = sticky ? left + 5 : (x0 + x1) / 2 - m.rulerMeasure(label) / 2;
    ctx.fillText(label, x, y + h / 2 + 0.5);
  }
}

function drawRuler(ctx, m) {
  const t = m.theme;
  ctx.fillStyle = t['ruler-bg'];
  ctx.fillRect(0, 0, m.W, RULER_H);
  ctx.font = `11px ${t.fontUi}`;
  ctx.textBaseline = 'middle';
  drawTickRow(ctx, m, m.ruler.major, 0, RULER_H / 2, t['ruler-text'], true);
  drawTickRow(ctx, m, m.ruler.minor, RULER_H / 2, RULER_H / 2, t['ruler-text-dim'], false);
  ctx.strokeStyle = t['ruler-line'];
  ctx.beginPath();
  ctx.moveTo(0, RULER_H / 2 + 0.5);
  ctx.lineTo(m.W, RULER_H / 2 + 0.5);
  ctx.stroke();
  const nx = Math.round(xOf(m, m.today + 0.5));
  ctx.fillStyle = t['now-line-on-ruler'];
  ctx.beginPath();
  ctx.moveTo(nx - 5, RULER_H - 9);
  ctx.lineTo(nx + 5, RULER_H - 9);
  ctx.lineTo(nx, RULER_H - 1);
  ctx.closePath();
  ctx.fill();
  ctx.fillRect(nx - 1, RULER_H - 3, 2, 3);
}

function drawEmpty(ctx, m) {
  if (m.columns.length || !m.emptyText) return;
  ctx.font = `13px ${m.theme.fontUi}`;
  ctx.fillStyle = m.theme['text-secondary'];
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(m.emptyText, m.W / 2, BODY_TOP + 48);
  ctx.textAlign = 'left';
}

export function drawFileTimeline(ctx, m) {
  m.ruler = rulerRows(m.view.start, m.view.end, m.ppd);
  drawBackground(ctx, m);
  drawColumns(ctx, m);
  drawScrollbar(ctx, m);
  drawEmpty(ctx, m);
  drawRuler(ctx, m);
  if (m.focused) {
    ctx.strokeStyle = m.theme['focus-ring'];
    ctx.lineWidth = 2;
    ctx.strokeRect(1, 1, m.W - 2, m.H - 2);
  }
}
