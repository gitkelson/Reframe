// Overview minimap (R-20..R-25, spec 6.4). Reads state.view and writes it only through setView().
// It never reacts to a 'view' event by writing back, so it cannot oscillate with the timeline (R-23).
import { addLine, createCanvasTooltip } from './canvas_tooltip.js';
import { hatchPattern, loadCanvasCss, readTheme, slotStyle } from './canvas_theme.js';
import { formatLong, formatShort, toDayNumber } from './dates.js';
import { currentLayout, currentViewport, onLayout, onViewport } from './layout_bus.js';
import { laneBands, rowScale } from './minimap_layout.js';
import { on, setHoverDay, setView, state, visibleItems, zoomAround } from './store.js';

const PAD_X = 8;
const PAD_Y = 8;
const MIN_FRAME_PX = 12;
const EDGE_PX = 6;
const MAX_BAR_ITEMS = 3000;    // above this there are too many bars to resolve: per-lane density histogram
const PAD_BEFORE_DAYS = 183;   // at least today - 6 months
const PAD_AFTER_DAYS = 730;    // at least today + 2 years
const WHEEL_ZOOM_RATE = 0.0025;
const HIST_GROUPS = 9;          // eight tag hues plus untagged

/** Colour slot of an item's primary tag (-1 when untagged), the same rule the main chart uses. */
function slotOf(item) {
  const t = item.tags && item.tags.length ? state.tags[item.tags[0]] : null;
  return t && Number.isInteger(t.slot) ? t.slot : -1;
}

export function initMinimap(host) {
  loadCanvasCss();
  const canvas = document.createElement('canvas');
  canvas.setAttribute('role', 'img');
  canvas.setAttribute('aria-label', 'Timeline overview. Drag the frame to move the visible window.');
  host.append(canvas);
  const ctx = canvas.getContext('2d');
  const tip = createCanvasTooltip(host);

  let theme = readTheme();
  let W = 0, H = 0, dpr = 1;
  let framePending = false;
  let data = null;               // {lo, hi, layout|null} rebuilt when items/layout change
  let range = { lo: 0, hi: 1 };  // data range, optionally widened to include the view
  let frozen = null;             // range held fixed while a gesture is running
  let hoverZone = null;
  let gesture = null;

  // ------------------------------------------------------------------- model

  function rebuildData() {
    const items = visibleItems();
    let lo = state.today - PAD_BEFORE_DAYS;
    let hi = state.today + PAD_AFTER_DAYS;
    const spans = items.map((it) => [toDayNumber(it.start_date), toDayNumber(it.end_date) + 1, slotOf(it)]);
    for (const [s, e] of spans) { if (s < lo) lo = s; if (e > hi) hi = e; }
    data = { lo, hi, items, spans, hist: null };
    invalidate();
  }

  function currentRange() {
    if (frozen) return frozen;
    let { lo, hi } = data;
    const v = state.view;
    lo = Math.min(lo, v.start);
    hi = Math.max(hi, v.end);
    const pad = (hi - lo) * 0.01;
    return { lo: lo - pad, hi: hi + pad };
  }

  const plotW = () => Math.max(1, W - 2 * PAD_X);
  const scale = () => plotW() / (range.hi - range.lo);
  const xOf = (day) => PAD_X + (day - range.lo) * scale();
  const dayAt = (x) => range.lo + (x - PAD_X) / scale();

  /** Frame rectangle in pixels; the drawn width never drops below MIN_FRAME_PX (R-25). */
  function frameRect() {
    const x0 = xOf(state.view.start), x1 = xOf(state.view.end);
    const w = Math.max(MIN_FRAME_PX, x1 - x0);
    const cx = (x0 + x1) / 2;
    return { x: cx - w / 2, w };
  }

  function zoneAt(x) {
    const f = frameRect();
    if (x < f.x - 3 || x > f.x + f.w + 3) return 'outside';
    const edge = Math.min(EDGE_PX, f.w / 3);
    if (x <= f.x + edge) return 'l';
    if (x >= f.x + f.w - edge) return 'r';
    return 'body';
  }

  // ----------------------------------------------------------------- drawing

  /** Per-lane density histogram, lanes in the chart's order, each band as tall as its rows (R-21). */
  function histogram(layout) {
    const key = `${range.lo}|${range.hi}|${W}|${layout.rowCount}`;
    if (data.hist && data.hist.key === key && data.hist.layout === layout) return data.hist;
    const n = Math.ceil(plotW()) + 2;
    const bands = laneBands(layout);
    const laneIdx = new Map(bands.map((b, i) => [b.key, i]));
    const counts = bands.map(() => new Int32Array(n * HIST_GROUPS));
    const diffs = bands.map(() => new Int32Array((n + 1) * HIST_GROUPS));
    for (const en of layout.entries.values()) {
      const c0 = Math.max(0, Math.floor(xOf(en.s) - PAD_X));
      const c1 = Math.min(n - 1, Math.max(c0, Math.floor(xOf(en.e) - PAD_X - 0.01)));
      if (c1 < 0 || c0 > n - 1) continue;
      const slot = slotOf(en.item);
      const g = slot < 0 ? HIST_GROUPS - 1 : slot % 8;
      const d = diffs[laneIdx.get(en.lane ? en.lane.key : null) ?? 0];
      d[c0 * HIST_GROUPS + g]++;
      d[(c1 + 1) * HIST_GROUPS + g]--;
    }
    const maxes = bands.map((_, li) => {
      const run = new Int32Array(HIST_GROUPS);
      let max = 1;
      for (let i = 0; i < n; i++) {
        let total = 0;
        for (let g = 0; g < HIST_GROUPS; g++) {
          run[g] += diffs[li][i * HIST_GROUPS + g];
          counts[li][i * HIST_GROUPS + g] = run[g];
          total += run[g];
        }
        if (total > max) max = total;
      }
      return max;
    });
    data.hist = { key, layout, bands, counts, maxes, n };
    return data.hist;
  }

  /** Same fill the main chart uses for a tag slot: the hue, hatched for slots 8-15, neutral when untagged. */
  function fillFor(slot) {
    const st = slotStyle(theme, slot);
    return st.hatched ? hatchPattern(ctx, theme, st.hue, dpr) : st.fill;
  }

  function drawHistogram(layout, top, avail) {
    const { bands, counts, maxes, n } = histogram(layout);
    const total = Math.max(1, layout.rowCount);
    for (let li = 0; li < bands.length; li++) {
      const b = bands[li];
      const y0 = top + (b.firstRow / total) * avail, y1 = top + (b.lastRow / total) * avail;
      const bandH = Math.max(1, y1 - y0);
      for (let i = 0; i < n; i++) {
        let sum = 0;
        for (let g = 0; g < HIST_GROUPS; g++) sum += counts[li][i * HIST_GROUPS + g];
        if (!sum) continue;
        const h = Math.max(1, Math.round((sum / maxes[li]) * bandH));
        let y = y1, used = 0, acc = 0;
        for (let g = 0; g < HIST_GROUPS; g++) {
          const c = counts[li][i * HIST_GROUPS + g];
          if (!c) continue;
          acc += c;
          const segH = Math.max(1, Math.round((acc / sum) * h)) - used;
          if (segH <= 0) continue;
          ctx.fillStyle = g === HIST_GROUPS - 1 ? theme['bar-untagged'] : theme.tags[g].bg;
          y -= segH;
          ctx.fillRect(PAD_X + i, y, 1, segH);
          used += segH;
        }
      }
    }
  }

  /** The main chart's rows, lanes and layout mode, scaled to the strip; plain fills, no borders (R-21). */
  function drawItems() {
    const layout = currentLayout();
    if (!layout) return;
    const top = PAD_Y, avail = H - 2 * PAD_Y;
    const sc = rowScale(layout.rowCount, avail, dpr);
    if (layout.entries.size > MAX_BAR_ITEMS) { drawHistogram(layout, top, avail); return; }
    const bySlot = new Map();
    for (const en of layout.entries.values()) {
      const slot = slotOf(en.item);
      if (!bySlot.has(slot)) bySlot.set(slot, []);
      bySlot.get(slot).push(en);
    }
    for (const [slot, list] of bySlot) {      // one fill per colour keeps this cheap
      ctx.fillStyle = fillFor(slot);
      ctx.beginPath();
      for (const en of list) {
        const x0 = xOf(en.s), x1 = xOf(en.e);
        ctx.rect(x0, top + sc.y(en.row), Math.max(1.5, x1 - x0), sc.h);
      }
      ctx.fill();
    }
  }

  /** The vertical extent of the main viewport, outlined inside the frame when the chart is scrolled (R-25). */
  function drawViewport() {
    const layout = currentLayout();
    const vp = currentViewport();
    if (!layout || !vp.rows || (vp.top <= 0 && vp.bottom >= layout.rowCount)) return;
    const f = frameRect();
    const avail = H - 2 * PAD_Y;
    const sc = rowScale(layout.rowCount, avail, dpr);
    const y0 = PAD_Y + sc.y(Math.max(0, vp.top)), y1 = PAD_Y + sc.y(Math.min(layout.rowCount, vp.bottom));
    ctx.strokeStyle = theme['minimap-frame'];
    ctx.setLineDash([3, 2]);
    ctx.lineWidth = 1;
    ctx.strokeRect(f.x + 3.5, y0 + 0.5, Math.max(2, f.w - 7), Math.max(2, y1 - y0));
    ctx.setLineDash([]);
  }

  function drawNow() {
    const x = Math.round(xOf(state.today + 0.5));
    ctx.fillStyle = theme['now-line-on-ruler'];
    ctx.fillRect(x - 1, 0, 2, H);
  }

  function drawFrame() {
    const f = frameRect();
    const active = gesture && gesture.type !== 'blank';
    const hot = hoverZone && hoverZone !== 'outside';
    ctx.fillStyle = theme['minimap-frame-fill'];
    ctx.fillRect(f.x, 1, f.w, H - 2);
    if (hoverZone === 'body' || (active && gesture.type === 'pan')) {
      ctx.globalAlpha = 0.6;
      ctx.fillRect(f.x, 1, f.w, H - 2);
      ctx.globalAlpha = 1;
    }
    ctx.strokeStyle = theme['minimap-frame'];
    ctx.lineWidth = active ? 3 : hot ? 2.5 : 2;
    ctx.strokeRect(f.x + 1, 2, f.w - 2, H - 4);
    ctx.fillStyle = theme['minimap-frame'];
    const gh = Math.round(H * 0.5);
    for (const [side, x] of [['l', f.x - 1], ['r', f.x + f.w - 5]]) {
      const big = hoverZone === side || (active && gesture.type === side);
      ctx.fillRect(x, (H - gh) / 2 - (big ? 4 : 0), 6, gh + (big ? 8 : 0));
    }
  }

  function frame() {
    framePending = false;
    syncSize();
    range = currentRange();
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = theme['minimap-bg'];
    ctx.fillRect(0, 0, W, H);
    drawItems();
    drawNow();
    drawFrame();
    drawViewport();
  }

  function syncSize() {
    const d = window.devicePixelRatio || 1;
    const w = host.clientWidth, h = host.clientHeight;
    if (w === W && h === H && d === dpr && canvas.width) return;
    W = w; H = h; dpr = d;
    canvas.width = Math.max(1, Math.round(W * dpr));
    canvas.height = Math.max(1, Math.round(H * dpr));
    if (data) data.hist = null;
  }

  function invalidate() {
    if (framePending) return;
    framePending = true;
    requestAnimationFrame(frame);
  }

  // ------------------------------------------------------------- interaction

  const posX = (e) => e.clientX - canvas.getBoundingClientRect().left;

  function setSpan(start, end) { setView(start, end, 'minimap'); }

  function onDown(e) {
    if (e.button !== 0) return;
    e.preventDefault();
    tip.hide();
    range = currentRange();
    frozen = range;
    const x = posX(e);
    let zone = zoneAt(x);
    const v0 = { ...state.view };
    if (zone === 'outside') {
      const span = v0.end - v0.start, c = dayAt(x);
      setSpan(c - span / 2, c + span / 2);
      zone = 'body';
      v0.start = state.view.start;
      v0.end = state.view.end;
    }
    gesture = { type: zone === 'body' ? 'pan' : zone, pointerId: e.pointerId, x0: x, v0 };
    canvas.setPointerCapture(e.pointerId);
    canvas.style.cursor = zone === 'body' ? 'grabbing' : 'ew-resize';
    invalidate();
  }

  function onMove(e) {
    const x = posX(e);
    if (!gesture) { hover(x); return; }
    if (gesture.pointerId !== e.pointerId) return;
    const { v0 } = gesture;
    const dDays = (x - gesture.x0) / scale();
    if (gesture.type === 'pan') setSpan(v0.start + dDays, v0.end + dDays);
    else if (gesture.type === 'l') setSpan(v0.start + dDays, v0.end);
    else if (gesture.type === 'r') setSpan(v0.start, v0.end + dDays);
    invalidate();
  }

  function endGesture() {
    if (!gesture) return;
    try { canvas.releasePointerCapture(gesture.pointerId); } catch { /* already released */ }
    gesture = null;
    frozen = null;
    canvas.style.cursor = cursorFor(hoverZone);
    invalidate();
  }

  const cursorFor = (zone) => (zone === 'l' || zone === 'r' ? 'ew-resize' : zone === 'body' ? 'grab' : 'pointer');

  function tooltipFor(zone, x) {
    const v = state.view;
    if (zone === 'l' || zone === 'r') {
      return (el) => { addLine(el, 'Drag to zoom', 'tt-title'); addLine(el, `Window: ${formatShort(Math.floor(v.start))} \u2013 ${formatShort(Math.floor(v.end))}`, 'tt-dim'); };
    }
    if (zone === 'body') {
      return (el) => { addLine(el, 'Drag to pan the timeline', 'tt-title'); addLine(el, `Window: ${formatShort(Math.floor(v.start))} \u2013 ${formatShort(Math.floor(v.end))}`, 'tt-dim'); };
    }
    return (el) => { addLine(el, formatLong(Math.floor(dayAt(x))), 'tt-title'); addLine(el, 'Click to jump here', 'tt-dim'); };
  }

  function hover(x) {
    range = currentRange();
    const zone = zoneAt(x);
    if (zone !== hoverZone) { hoverZone = zone; invalidate(); }
    canvas.style.cursor = cursorFor(zone);
    setHoverDay(Math.floor(dayAt(x)));
    tip.hover(zone === 'outside' ? `out:${Math.floor(dayAt(x))}` : `zone:${zone}`, x, host.clientHeight / 2, tooltipFor(zone, x));
  }

  function onWheel(e) {
    e.preventDefault();
    tip.hide();
    const dy = Math.max(-300, Math.min(300, e.deltaY * (e.deltaMode === 1 ? 16 : 1)));
    zoomAround(Math.exp(-dy * WHEEL_ZOOM_RATE), (state.view.start + state.view.end) / 2, 'minimap');
  }

  canvas.addEventListener('pointerdown', onDown);
  canvas.addEventListener('pointermove', onMove);
  canvas.addEventListener('pointerup', endGesture);
  canvas.addEventListener('pointercancel', endGesture);
  canvas.addEventListener('pointerleave', () => {
    tip.hide();
    hoverZone = null;
    if (!gesture) setHoverDay(null);
    invalidate();
  });
  canvas.addEventListener('wheel', onWheel, { passive: false });

  new ResizeObserver(invalidate).observe(host);
  for (const type of ['items', 'layout']) on(type, rebuildData);
  // every repack of the main chart is drawn in the same animation frame (R-21)
  onLayout(() => { if (data) data.hist = null; framePending = false; frame(); });
  onViewport(invalidate);
  on('view', invalidate);
  on('theme', () => { theme = readTheme(); invalidate(); });
  rebuildData();
  invalidate();
}
