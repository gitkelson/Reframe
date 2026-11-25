// The timeline canvas (R-01..R-16): layout, rendering, rename overlay and the hooks used by the shortcuts.
// Pointer and keyboard gestures live in timeline_input.js; painting in timeline_draw.js.
import * as actions from './actions.js';
import { registerTimeline } from './commands.js';
import { createMeasurer, loadCanvasCss, readTheme } from './canvas_theme.js';
import { createCanvasTooltip } from './canvas_tooltip.js';
import { attachInput, refuseLocked } from './timeline_input.js';
import { LABEL_FONT_PX, ROW_H, RULER_H, barRect, drawTimeline } from './timeline_draw.js';
import { publishLayout, publishViewport } from './layout_bus.js';
import { packItems } from './packing.js';
import { bandOf, pxPerDay } from './zoom.js';
import { notify, on, setSelection, setView, state, visibleItems } from './store.js';

const PLOT_MIN_PX = 50;

let current = null;
/** Scroll and pan minimally so item `id` is visible (used by the accessibility list). */
export function revealItem(id) { current?.reveal(id); }

function el(tag, className, attrs = {}) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}

export function initTimeline(host) {
  loadCanvasCss();
  const canvas = el('canvas', '', { 'aria-hidden': 'true' });
  canvas.tabIndex = -1;
  host.tabIndex = 0;
  host.setAttribute('role', 'application');
  host.setAttribute('aria-label', 'Timeline. Use the item list to browse items by keyboard.');
  host.append(canvas);
  const ctx = canvas.getContext('2d');
  const measurer = createMeasurer();
  const rulerMeasurer = createMeasurer();
  const input = el('input', 'rf-title-input', { type: 'text', 'aria-label': 'Item title', maxlength: '300' });
  input.hidden = true;
  host.append(input);

  const m = {
    W: 0, H: 0, bodyH: 0, dpr: 1, theme: readTheme(), state, view: state.view, ppd: 1, plotLeft: 0, plotW: PLOT_MIN_PX,
    scrollY: 0, band: 0, layout: null, drag: null, create: null, marquee: null, tagDrag: null, chipDrag: null, focused: false,
    egg: null,
    measure: measurer.measure, rulerMeasure: rulerMeasurer.measure, ruler: null,
  };
  let layoutDirty = true;
  let framePending = false;
  let renameId = null;

  const tl = {
    m, host, canvas, tip: createCanvasTooltip(host), ensureLayout, invalidate, markDirty, clampScroll,
    startRename, reveal, selectRow, scrollRows, cancelRename,
  };

  function applyTheme() {
    m.theme = readTheme();
    measurer.setFont(`${LABEL_FONT_PX}px ${m.theme.fontUi}`);
    rulerMeasurer.setFont(`11px ${m.theme.fontUi}`);
    markDirty();
  }

  function geometry() {
    m.view = state.view;
    m.plotLeft = 0;
    m.plotW = Math.max(PLOT_MIN_PX, m.W - m.plotLeft);
    m.ppd = pxPerDay(m.view, m.plotW);
  }

  function syncSize() {
    const dpr = window.devicePixelRatio || 1;
    const W = host.clientWidth, H = host.clientHeight;
    if (W === m.W && H === m.H && dpr === m.dpr && canvas.width) return;
    Object.assign(m, { W, H, dpr, bodyH: Math.max(0, H - RULER_H) });
    canvas.width = Math.max(1, Math.round(W * dpr));
    canvas.height = Math.max(1, Math.round(H * dpr));
  }

  function clampScroll() {
    const max = Math.max(0, m.layout.rowCount * ROW_H + 8 - m.bodyH);
    m.scrollY = Math.min(max, Math.max(0, m.scrollY));
    const vp = { top: m.scrollY / ROW_H, bottom: (m.scrollY + m.bodyH) / ROW_H, rows: m.layout.rowCount };
    if (vp.top !== lastVp.top || vp.bottom !== lastVp.bottom || vp.rows !== lastVp.rows) { lastVp = vp; publishViewport(vp); }
  }
  let lastVp = { top: -1, bottom: -1, rows: -1 };

  /** Repack on data/filter/group changes and zoom-band changes only; never while dragging (spec 4.1). */
  function ensureLayout() {
    syncSize();
    geometry();
    const band = bandOf(m.ppd);
    if (m.layout && (m.drag || m.create || m.tagDrag || m.chipDrag)) return;
    // strict mode ignores outside labels, so zoom-band changes never repack it (spec 4.1 step 6)
    if (m.layout && !layoutDirty && (band === m.band || state.layoutMode === 'strict')) return;
    const prev = m.layout?.entries.get(state.primaryId);
    const screenY = prev ? prev.row * ROW_H - m.scrollY : null;
    m.band = band;
    layoutDirty = false;
    m.layout = packItems(visibleItems(), { band, measure: m.measure, groupByTag: state.groupByTag, tags: state.tags,
      mode: state.layoutMode });
    const kept = m.layout.entries.get(state.primaryId);
    if (kept && screenY !== null) m.scrollY = kept.row * ROW_H - screenY;   // selected item keeps its screen height
    clampScroll();
    publishLayout(m.layout);
  }

  function markDirty() { layoutDirty = true; invalidate(); }

  function invalidate() {
    if (framePending) return;
    framePending = true;
    requestAnimationFrame(frame);
  }

  function frame() {
    framePending = false;
    ensureLayout();
    clampScroll();
    ctx.setTransform(m.dpr, 0, 0, m.dpr, 0, 0);
    drawTimeline(ctx, m);
    positionRename();
  }

  // ------------------------------------------------------------------ rename

  function positionRename() {
    if (!renameId) return;
    const en = m.layout.entries.get(renameId);
    if (!en) { cancelRename(); return; }
    const r = barRect(m, en);
    input.style.left = `${Math.max(m.plotLeft, Math.min(r.x, m.W - 180))}px`;
    input.style.top = `${r.y - 1}px`;
    input.style.width = `${Math.max(180, Math.min(r.w, m.W - r.x))}px`;
    input.style.height = `${r.h + 2}px`;
  }

  function startRename(id) {
    const it = state.itemsById.get(id);
    if (!it) return;
    if (it.locked) { refuseLocked(it); return; }
    if (renameId === id) return;
    ensureLayout();
    if (!m.layout.entries.has(id)) return;
    reveal(id);
    renameId = id;
    input.value = it.title;
    input.hidden = false;
    positionRename();
    input.focus();
    input.select();
  }

  function endRename() {
    renameId = null;
    input.hidden = true;
  }

  function cancelRename() {
    if (!renameId) return;
    endRename();
    canvas.focus({ preventScroll: true });
  }

  function commitRename() {
    const id = renameId;
    if (!id) return;
    const title = input.value.trim();
    const it = state.itemsById.get(id);
    endRename();
    canvas.focus({ preventScroll: true });
    if (it && title && title !== it.title) actions.patchItem(id, { title });
  }

  input.addEventListener('keydown', (e) => {
    e.stopPropagation();
    if (e.key === 'Enter') { e.preventDefault(); commitRename(); }
    else if (e.key === 'Escape') { e.preventDefault(); cancelRename(); }
  });
  input.addEventListener('blur', () => { if (renameId) commitRename(); });

  // ------------------------------------------------------ keyboard navigation

  function reveal(id) {
    ensureLayout();
    const en = m.layout.entries.get(id);
    if (!en) return;
    const top = en.row * ROW_H;
    if (top < m.scrollY) m.scrollY = top;
    else if (top + ROW_H > m.scrollY + m.bodyH) m.scrollY = top + ROW_H - m.bodyH;
    clampScroll();
    panToShow(en);
    invalidate();
  }

  function panToShow(en) {
    const { start, end } = state.view;
    const span = end - start;
    const margin = Math.max(0.5, span * 0.03);
    const len = en.e - en.s;
    let ns = start;
    if (len >= span - 2 * margin) {
      if (en.e <= start || en.s >= end) ns = en.s - margin;
    } else if (en.s < start) ns = en.s - margin;
    else if (en.e > end) ns = en.e + margin - span;
    if (ns !== start) setView(ns, ns + span, 'timeline');
  }

  /** Select the item in the row above/below the primary item, nearest in time. */
  function selectRow(dir) {
    ensureLayout();
    const rows = m.layout.rows;
    const cur = m.layout.entries.get(state.primaryId);
    const mid = cur ? (cur.s + cur.e) / 2 : (state.view.start + state.view.end) / 2;
    const dist = (en) => (mid < en.s ? en.s - mid : mid >= en.e ? mid - en.e : 0);
    for (let r = (cur ? cur.row : dir > 0 ? -1 : rows.length) + dir; r >= 0 && r < rows.length; r += dir) {
      if (!rows[r].length) continue;
      const pick = rows[r].reduce((best, en) => (dist(en) < dist(best) ? en : best));
      setSelection([pick.id], pick.id);
      reveal(pick.id);
      return;
    }
  }

  function scrollRows(n) {
    ensureLayout();
    m.scrollY += n * ROW_H;
    clampScroll();
    invalidate();
  }

  // ----------------------------------------------------------------- wiring

  new ResizeObserver(() => invalidate()).observe(host);
  host.addEventListener('focusin', () => { m.focused = true; invalidate(); });
  host.addEventListener('focusout', () => { m.focused = host.contains(document.activeElement); invalidate(); });
  for (const type of ['items', 'layout']) on(type, markDirty);
  for (const type of ['view', 'selection', 'highlight', 'ready']) on(type, invalidate);
  on('theme', applyTheme);
  document.fonts?.addEventListener('loadingdone', applyTheme);
  applyTheme();
  m.layout = packItems([], { band: 0, measure: m.measure });
  attachInput(tl);

  registerTimeline({
    focus: () => canvas.focus({ preventScroll: true }),
    startRename, selectRow, scrollRows, reveal,
    chipDrag: tl.chipDrag,
    egg: (egg) => { m.egg = egg; invalidate(); if (egg) requestAnimationFrame(function tick() { if (m.egg) { invalidate(); requestAnimationFrame(tick); } }); },
  });
  window.__reframe = Object.freeze({
    barRect(id) {
      ensureLayout();
      const en = m.layout.entries.get(id);
      if (!en) return null;
      const r = barRect(m, en), b = canvas.getBoundingClientRect();
      return { x: b.left + r.x, y: b.top + r.y, w: r.w, h: r.h };
    },
    rowCount: () => m.layout.rowCount,
    egg: () => m.egg?.kind ?? null,
    layout: () => ({ mode: m.layout.mode, rows: [...m.layout.entries.values()].map((en) => [en.id, en.row]),
      lanes: m.layout.lanes.map((l) => [l.key, l.headerRow, l.rowCount]) }),
    ppd: () => m.ppd,
    scrollY: () => m.scrollY,
    plotLeft: () => m.plotLeft,
    canvas: () => canvas.getBoundingClientRect().toJSON(),
  });
  current = tl;
  invalidate();
  return tl;
}
