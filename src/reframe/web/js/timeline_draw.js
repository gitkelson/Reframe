// Painting for the timeline canvas. Reads the model `m` built by timeline.js; never mutates application state.
import { BAR_LABEL_PAD, OUTSIDE_GAP, OUTSIDE_MAX_PX, elide, insideThreshold, labelMode } from './labels.js';
import { firstReaching } from './packing.js';
import { hatchPattern, itemStyle, slotStyle } from './canvas_theme.js';
import { OUTSIDE_MIN_PX_PER_DAY, rulerRows, weekendSpans } from './zoom.js';

export const RULER_H = 44;
export const ROW_H = 26;
export const BAR_H = 20;
export const LABEL_FONT_PX = 12;
const MIN_BAR_PX = 2;
const DOT_R = 3;
const DOT_STEP = 9;

export const xOf = (m, day) => m.plotLeft + (day - m.view.start) * m.ppd;
export const rowTop = (m, row) => RULER_H + row * ROW_H - m.scrollY;

/** Pixel rectangle of an entry (optionally with overridden days, used while dragging). */
export function barRect(m, entry, s = entry.s, e = entry.e) {
  const x0 = Math.round(xOf(m, s));
  const x1 = Math.round(xOf(m, e));
  return { x: x0, y: rowTop(m, entry.row) + (ROW_H - BAR_H) / 2, w: Math.max(MIN_BAR_PX, x1 - x0 - 1), h: BAR_H };
}

const labelFont = (m) => `${LABEL_FONT_PX}px ${m.theme.fontUi}`;

function roundedPath(ctx, r, radius) {
  if (r.w < 5 || radius < 1) ctx.rect(r.x, r.y, r.w, r.h);
  else ctx.roundRect(r.x, r.y, r.w, r.h, Math.min(radius, r.w / 2, r.h / 2));
}

function fillStyleFor(ctx, m, style) {
  return style.hatched ? hatchPattern(ctx, m.theme, style.hue, m.dpr) : style.fill;
}

// ----------------------------------------------------------------------- body

function drawBackground(ctx, m) {
  const t = m.theme;
  ctx.fillStyle = t['canvas-bg'];
  ctx.fillRect(0, 0, m.W, m.H);
  ctx.fillStyle = t['weekend-shade'];
  for (const [s, e] of weekendSpans(m.view.start, m.view.end, m.ppd)) {
    ctx.fillRect(Math.round(xOf(m, s)), RULER_H, Math.max(1, Math.round(xOf(m, e)) - Math.round(xOf(m, s))), m.bodyH);
  }
  drawTodayCell(ctx, m);
}

/** Today is the whole day cell shaded behind the bars (no line over the chart). */
function drawTodayCell(ctx, m) {
  const x0 = Math.round(xOf(m, m.state.today));
  const x1 = Math.round(xOf(m, m.state.today + 1));
  if (x1 < m.plotLeft || x0 > m.W) return;
  ctx.fillStyle = m.theme['today-shade'];
  ctx.fillRect(x0, RULER_H, Math.max(2, x1 - x0), m.bodyH);
}

function drawGrid(ctx, m) {
  const rows = m.ruler;
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
  lines(rows.minor, m.theme['grid-line'], 12);
  lines(rows.major, m.theme['grid-line-strong'], 0);
}

function drawLaneHeaders(ctx, m) {
  const t = m.theme;
  ctx.font = `700 ${LABEL_FONT_PX}px ${t.fontUi}`;
  ctx.textBaseline = 'middle';
  for (const lane of m.layout.lanes) {
    const y = rowTop(m, lane.headerRow);
    if (y + ROW_H < RULER_H || y > m.H) continue;
    ctx.fillStyle = t['lane-header-bg'];
    ctx.fillRect(0, y + 1, m.W, ROW_H - 2);
    ctx.fillStyle = t['lane-header-text'];
    ctx.fillText(`${lane.name}  (${lane.count})`, 10, y + ROW_H / 2 + 0.5);
  }
}

function visibleEntries(m) {
  const out = [];
  const r0 = Math.max(0, Math.floor(m.scrollY / ROW_H));
  const r1 = Math.min(m.layout.rows.length - 1, Math.floor((m.scrollY + m.bodyH) / ROW_H));
  for (let r = r0; r <= r1; r++) {
    const list = m.layout.rows[r];
    for (let i = firstReaching(list, m.view.start); i < list.length && list[i].s < m.view.end; i++) {
      out.push({ en: list[i], rect: barRect(m, list[i]), style: itemStyle(m.theme, list[i].item, m.state.tags) });
    }
  }
  return out;
}

const NARROW_LOG_PX = 6;   // below this a log bar is drawn as a solid mark instead of a dashed outline

function drawPlainBars(ctx, m, visible) {
  const buckets = new Map();
  const bucket = (key, style) => {
    if (!buckets.has(key)) buckets.set(key, { style, list: [] });
    return buckets.get(key).list;
  };
  for (const v of visible) {
    if (m.drag?.ids.has(v.en.id)) continue;
    if (v.en.item.kind !== 'log') bucket(v.style.hatched ? `h${v.style.hue}` : v.style.fill, v.style).push(v.rect);
    else if (v.rect.w < NARROW_LOG_PX) bucket('log', null).push(v.rect);
  }
  for (const [key, { style, list }] of buckets) {
    ctx.fillStyle = key === 'log' ? m.theme['bar-log-outline'] : fillStyleFor(ctx, m, style);
    ctx.globalAlpha = key === 'log' ? 0.6 : 1;
    ctx.beginPath();
    for (const r of list) roundedPath(ctx, r, m.theme.radius);
    ctx.fill();
  }
  ctx.globalAlpha = 1;
}

/** Log items (R-50): light fill plus a dashed outline, so they differ by pattern and not by color alone. */
function paintLogBar(ctx, m, en, rect) {
  if (rect.w < NARROW_LOG_PX) return;   // batched by drawPlainBars
  const style = itemStyle(m.theme, en.item, m.state.tags);
  ctx.save();
  ctx.globalAlpha = 0.3;
  ctx.fillStyle = fillStyleFor(ctx, m, style);
  ctx.beginPath();
  roundedPath(ctx, rect, m.theme.radius);
  ctx.fill();
  ctx.restore();
  ctx.save();
  ctx.strokeStyle = m.theme['bar-log-outline'];
  ctx.lineWidth = 1.5;
  ctx.setLineDash([4, 2]);
  ctx.beginPath();
  roundedPath(ctx, { x: rect.x + 0.75, y: rect.y + 0.75, w: rect.w - 1.5, h: rect.h - 1.5 }, m.theme.radius);
  ctx.stroke();
  ctx.restore();
}

function paintOutlines(ctx, m, en, rect) {
  const t = m.theme;
  const until = m.state.highlights.get(en.id);
  if (until && until > Date.now()) {
    ctx.strokeStyle = t['highlight-external'];
    ctx.lineWidth = 3;
    ctx.beginPath();
    roundedPath(ctx, { x: rect.x - 2, y: rect.y - 2, w: rect.w + 4, h: rect.h + 4 }, t.radius + 2);
    ctx.stroke();
  }
  if (!m.state.selection.has(en.id)) return;
  ctx.lineWidth = 2;
  ctx.strokeStyle = en.id === m.state.primaryId ? t['focus-ring'] : t['bar-selected-outline'];
  ctx.beginPath();
  roundedPath(ctx, { x: rect.x - 1.5, y: rect.y - 1.5, w: rect.w + 3, h: rect.h + 3 }, t.radius + 1);
  ctx.stroke();
  ctx.lineWidth = 1;
  ctx.strokeStyle = t['canvas-bg'];
  ctx.beginPath();
  roundedPath(ctx, { x: rect.x + 0.5, y: rect.y + 0.5, w: rect.w - 1, h: rect.h - 1 }, t.radius);
  ctx.stroke();
}

function paintTagDots(ctx, m, en, rect) {
  const extra = en.item.tags.slice(1, 4);
  if (!extra.length || rect.w < 64) return 0;
  let x = rect.x + 6 + DOT_R;
  for (const id of extra) {
    const st = itemStyle(m.theme, { tags: [id] }, m.state.tags);
    ctx.fillStyle = st.fill;
    ctx.strokeStyle = m.theme['canvas-bg'];
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(x, rect.y + rect.h / 2, DOT_R, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    x += DOT_STEP;
  }
  return extra.length * DOT_STEP + 2;
}

function paintLabel(ctx, m, en, rect) {
  const title = en.item.title || '';
  if (!title) return;
  const mode = labelMode(rect.w, m.ppd, m.measure);
  const style = itemStyle(m.theme, en.item, m.state.tags);
  const cy = rect.y + rect.h / 2 + 0.5;
  ctx.font = labelFont(m);
  ctx.textBaseline = 'middle';
  if (mode === 'inside') {
    const dots = paintTagDots(ctx, m, en, rect);
    const left = Math.max(rect.x, m.plotLeft);
    const x = left + BAR_LABEL_PAD + dots;
    const text = elide(title, rect.x + rect.w - x - BAR_LABEL_PAD, m.measure);
    if (!text) return;
    if (style.hatched && en.item.kind !== 'log') {   // hatch hurts legibility: draw the label on a solid chip
      ctx.fillStyle = style.fill;
      ctx.fillRect(x - 3, rect.y + 2, m.measure(text) + 6, rect.h - 4);
    }
    ctx.fillStyle = en.item.kind === 'log' ? m.theme.text : style.fg;
    ctx.fillText(text, x, cy);
  } else if (mode === 'outside' && en.ext > en.e) {
    const x = rect.x + rect.w + OUTSIDE_GAP + 1;
    const text = elide(title, Math.min(OUTSIDE_MAX_PX, m.plotLeft + m.plotW - x), m.measure);
    ctx.fillStyle = m.theme.text;
    if (text) ctx.fillText(text, x, cy);
  }
}

function paintBar(ctx, m, en, rect, plain) {
  if (en.item.kind === 'log') paintLogBar(ctx, m, en, rect);
  else if (!plain) {
    ctx.fillStyle = fillStyleFor(ctx, m, itemStyle(m.theme, en.item, m.state.tags));
    ctx.beginPath();
    roundedPath(ctx, rect, m.theme.radius);
    ctx.fill();
  }
  paintOutlines(ctx, m, en, rect);
  paintLabel(ctx, m, en, rect);
}

/** Bars dragged by the user float over their row; the original position keeps a faint ghost. */
function drawDragged(ctx, m) {
  const d = m.drag;
  if (!d) return;
  for (const id of d.ids) {
    const en = m.layout.entries.get(id);
    if (!en) continue;
    const ghost = barRect(m, en);
    ctx.fillStyle = m.theme['drag-ghost'];
    ctx.beginPath();
    roundedPath(ctx, ghost, m.theme.radius);
    ctx.fill();
    const span = d.span(en);
    paintBar(ctx, m, en, barRect(m, en, span.s, span.e), false);
  }
}

function drawCreate(ctx, m) {
  const c = m.create;
  if (!c) return;
  const r = barRect(m, { row: c.row }, c.s, c.e);
  ctx.fillStyle = m.theme['drag-ghost'];
  ctx.beginPath();
  roundedPath(ctx, r, m.theme.radius);
  ctx.fill();
}

/** Tag-copy drag (R-48): the drop target is outlined and a chip of the copied tags follows the pointer. */
function drawTagDrag(ctx, m) {
  const d = m.tagDrag;
  if (!d) return;
  const t = m.theme;
  const outline = (id, dash, color) => {
    const en = m.layout.entries.get(id);
    if (!en) return;
    const r = barRect(m, en);
    ctx.setLineDash(dash);
    ctx.lineWidth = 2;
    ctx.strokeStyle = color;
    ctx.beginPath();
    roundedPath(ctx, { x: r.x - 2, y: r.y - 2, w: r.w + 4, h: r.h + 4 }, t.radius + 1);
    ctx.stroke();
    ctx.setLineDash([]);
  };
  outline(d.sourceId, [4, 3], t['marquee-stroke']);
  if (d.targetId) outline(d.targetId, [], t['drop-target']);
  if (d.ghost) {      // where the duplicate will land, drawn in the source's own color
    const src = m.layout.entries.get(d.sourceId);
    const g = barRect(m, { row: d.ghost.row }, d.ghost.s, d.ghost.e);
    ctx.globalAlpha = 0.55;
    ctx.fillStyle = src ? fillStyleFor(ctx, m, itemStyle(t, src.item, m.state.tags)) : t['drag-ghost'];
    ctx.beginPath();
    roundedPath(ctx, g, t.radius);
    ctx.fill();
    ctx.globalAlpha = 1;
    ctx.setLineDash([4, 3]);
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = t['marquee-stroke'];
    ctx.stroke();
    ctx.setLineDash([]);
  }

  const n = d.slots.length;
  const label = d.noTags ? 'No tags to copy'
    : d.ghost ? 'Duplicate'
    : `${n === 1 ? 'Copy 1 tag' : `Copy ${n} tags`}${d.targetId ? '' : ' to...'}`;
  ctx.font = `600 ${LABEL_FONT_PX}px ${t.fontUi}`;
  const dots = d.ghost || d.noTags ? 0 : Math.min(n, 6);
  const w = 10 + dots * (DOT_R * 2 + 3) + 6 + m.measure(label);
  const x = Math.min(Math.max(m.plotLeft, d.x + 14), m.W - w - 4), y = Math.max(RULER_H + 2, d.y + 14);
  ctx.fillStyle = t['canvas-bg'];
  ctx.strokeStyle = d.targetId || d.ghost ? t['focus-ring'] : t['marquee-stroke'];
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  roundedPath(ctx, { x, y, w, h: 22 }, t.radius + 2);
  ctx.fill();
  ctx.stroke();
  let cx = x + 8 + DOT_R;
  for (let i = 0; i < dots; i++) {
    ctx.fillStyle = slotStyle(t, d.slots[i]).fill;
    ctx.beginPath();
    ctx.arc(cx, y + 11, DOT_R, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = t.text;
    ctx.lineWidth = 0.5;
    ctx.stroke();
    cx += DOT_R * 2 + 3;
  }
  ctx.fillStyle = t.text;
  ctx.textBaseline = 'middle';
  ctx.fillText(label, cx + 2, y + 11.5);
}

function drawMarquee(ctx, m) {
  const q = m.marquee;
  if (!q) return;
  const x = Math.min(q.x0, q.x1), y = Math.min(q.y0, q.y1), w = Math.abs(q.x1 - q.x0), h = Math.abs(q.y1 - q.y0);
  ctx.fillStyle = m.theme['marquee-fill'];
  ctx.fillRect(x, y, w, h);
  ctx.strokeStyle = m.theme['marquee-stroke'];
  ctx.lineWidth = 1;
  ctx.strokeRect(x + 0.5, y + 0.5, w, h);
}

/** Filter-chip drop (R-115): the target bar is outlined and a chip at the pointer names the tag. */
function drawChipDrag(ctx, m) {
  const d = m.chipDrag;
  if (!d) return;
  const t = m.theme;
  for (const id of d.targets || []) {
    const en = m.layout.entries.get(id);
    if (!en) continue;
    const r = barRect(m, en);
    ctx.lineWidth = 2;
    ctx.strokeStyle = t['drop-target'];
    ctx.beginPath();
    roundedPath(ctx, { x: r.x - 2, y: r.y - 2, w: r.w + 4, h: r.h + 4 }, t.radius + 1);
    ctx.stroke();
  }
  const n = (d.targets || []).length;
  const label = n > 1 ? `Add tag ${d.name} to ${n} items` : `Add tag ${d.name}`;
  ctx.font = `600 ${LABEL_FONT_PX}px ${t.fontUi}`;
  const w = 16 + DOT_R * 2 + m.measure(label);
  const x = Math.min(Math.max(m.plotLeft, d.x + 14), m.W - w - 4), y = Math.max(RULER_H + 2, d.y + 14);
  ctx.fillStyle = t['canvas-bg'];
  ctx.strokeStyle = n ? t['drop-target'] : t['marquee-stroke'];
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  roundedPath(ctx, { x, y, w, h: 22 }, t.radius + 2);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = slotStyle(t, d.slot).fill;
  ctx.beginPath();
  ctx.arc(x + 8 + DOT_R, y + 11, DOT_R, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = t.text;
  ctx.textBaseline = 'middle';
  ctx.fillText(label, x + 12 + DOT_R * 2, y + 11.5);
}

/** Easter egg (R-185, 2001 theme only): a black monolith standing on today's day cell with a faint sunrise. */
function drawMonolith(ctx, m) {
  const egg = m.egg;
  if (!egg || egg.kind !== 'monolith') return;
  const now = performance.now();
  const p = egg.reduced ? 1 : Math.min(1, (now - egg.start) / 900);
  const x0 = xOf(m, m.state.today), x1 = xOf(m, m.state.today + 1);
  const cx = (x0 + x1) / 2;
  const w = Math.max(8, Math.min(28, (x1 - x0) * 0.5));
  const h = Math.min(m.bodyH * 0.6, w * 9 / 4 * 2);
  const base = RULER_H + m.bodyH - 12;
  const glow = ctx.createRadialGradient(cx, base, 2, cx, base, h * (0.6 + 0.5 * p));
  glow.addColorStop(0, m.theme['monolith-glow']);
  glow.addColorStop(1, 'transparent');
  ctx.save();
  ctx.globalAlpha = 0.55 * p;
  ctx.fillStyle = glow;
  ctx.fillRect(cx - h, base - h * 1.2, h * 2, h * 1.2);
  ctx.globalAlpha = 1;
  ctx.fillStyle = m.theme.monolith;
  ctx.fillRect(Math.round(cx - w / 2), Math.round(base - h * p), Math.round(w), Math.round(h * p));
  ctx.restore();
}

function drawScrollbar(ctx, m) {
  const total = m.layout.rowCount * ROW_H;
  if (total <= m.bodyH) return;
  const h = Math.max(24, m.bodyH * (m.bodyH / total));
  const y = RULER_H + (m.scrollY / (total - m.bodyH)) * (m.bodyH - h);
  ctx.globalAlpha = 0.5;
  ctx.fillStyle = m.theme['grid-line-strong'];
  ctx.fillRect(m.W - 5, y, 3, h);
  ctx.globalAlpha = 1;
}

function drawEmpty(ctx, m) {
  if (m.layout.entries.size) return;
  ctx.font = `13px ${m.theme.fontUi}`;
  ctx.fillStyle = m.theme['text-secondary'];
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText('Nothing here. Drag on empty space to create an item.', m.plotLeft + m.plotW / 2, RULER_H + 48);
  ctx.textAlign = 'left';
}

function drawBody(ctx, m) {
  ctx.save();
  ctx.beginPath();
  ctx.rect(0, RULER_H, m.W, m.bodyH);
  ctx.clip();
  drawBackground(ctx, m);
  drawGrid(ctx, m);
  drawLaneHeaders(ctx, m);
  ctx.beginPath();
  ctx.rect(m.plotLeft, RULER_H, m.plotW, m.bodyH);
  ctx.clip();
  const entries = visibleEntries(m);
  drawPlainBars(ctx, m, entries);
  const inside = insideThreshold(m.measure);
  const labelsPossible = m.ppd >= OUTSIDE_MIN_PX_PER_DAY;
  for (const { en, rect } of entries) {
    if (m.drag?.ids.has(en.id)) continue;
    const log = en.item.kind === 'log';
    const decorated = m.state.selection.has(en.id) || m.state.highlights.has(en.id);
    if (!(decorated || labelsPossible || rect.w >= inside || (log && rect.w >= NARROW_LOG_PX))) continue;
    paintBar(ctx, m, en, rect, !log);
  }
  drawDragged(ctx, m);
  drawCreate(ctx, m);
  drawMarquee(ctx, m);
  drawTagDrag(ctx, m);
  drawChipDrag(ctx, m);
  drawMonolith(ctx, m);
  ctx.restore();
  ctx.save();
  ctx.beginPath();
  ctx.rect(0, RULER_H, m.W, m.bodyH);
  ctx.clip();
  drawScrollbar(ctx, m);
  drawEmpty(ctx, m);
  ctx.restore();
}

// ---------------------------------------------------------------------- ruler

function drawTickRow(ctx, m, ticks, y, h, { color, sticky }) {
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
    const left = sticky ? Math.max(x0, m.plotLeft) : x0;
    const room = x1 - left - 8;
    const label = k.labels.find((l) => m.rulerMeasure(l) <= room);
    if (!label) continue;
    const x = sticky ? left + 5 : (x0 + x1) / 2 - m.rulerMeasure(label) / 2;
    ctx.fillText(label, x, y + h / 2 + 0.5);
  }
}

function drawRuler(ctx, m) {
  const t = m.theme;
  ctx.fillStyle = t['ruler-bg'];
  ctx.fillRect(0, 0, m.W, RULER_H);
  ctx.save();
  ctx.beginPath();
  ctx.rect(m.plotLeft, 0, m.plotW, RULER_H);
  ctx.clip();
  ctx.font = `11px ${t.fontUi}`;
  ctx.textBaseline = 'middle';
  drawTickRow(ctx, m, m.ruler.major, 0, RULER_H / 2, { color: t['ruler-text'], sticky: true });
  drawTickRow(ctx, m, m.ruler.minor, RULER_H / 2, RULER_H / 2, { color: t['ruler-text-dim'], sticky: false });
  ctx.strokeStyle = t['ruler-line'];
  ctx.beginPath();
  ctx.moveTo(0, RULER_H / 2 + 0.5);
  ctx.lineTo(m.W, RULER_H / 2 + 0.5);
  ctx.stroke();
  const nx = Math.round(xOf(m, m.state.today + 0.5));
  ctx.fillStyle = t['now-line-on-ruler'];
  ctx.beginPath();
  ctx.moveTo(nx - 5, RULER_H - 9);
  ctx.lineTo(nx + 5, RULER_H - 9);
  ctx.lineTo(nx, RULER_H - 1);
  ctx.closePath();
  ctx.fill();
  ctx.fillRect(nx - 1, RULER_H - 3, 2, 3);
  ctx.restore();
}

export function drawTimeline(ctx, m) {
  m.ruler = rulerRows(m.view.start, m.view.end, m.ppd);
  drawBody(ctx, m);
  drawRuler(ctx, m);
  if (m.focused) {
    ctx.strokeStyle = m.theme['focus-ring'];
    ctx.lineWidth = 2;
    ctx.strokeRect(1, 1, m.W - 2, m.H - 2);
  }
}
