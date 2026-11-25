// Pointer and wheel gestures for the File timeline canvas (R-08 gestures, R-145). Read-only view: there is no
// create, move, resize or tag drag (R-152); a left drag on blank space pans instead.
import { addLine } from '../canvas_tooltip.js';
import { formatLong, fromDayNumber } from '../dates.js';
import { RULER_H, cellKey, hitTest } from './draw.js';
import { formatSize } from './groups.js';

const DRAG_THRESHOLD_PX = 4;
const WHEEL_ZOOM_RATE = 0.0025;

export function attachInput(ctl) {
  const { canvas, host, store } = ctl;
  const m = ctl.m;
  let gesture = null;
  let spaceDown = false;

  const pos = (e) => {
    const r = canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  };

  function cursorFor(h) {
    if (gesture && gesture.moved) return 'grabbing';
    if (spaceDown) return 'grab';
    if (!h) return 'default';
    if (h.zone === 'cell' || h.zone === 'header' || h.zone === 'ruler') return 'pointer';
    return 'default';
  }

  function entryTooltip(e) {
    return (el) => {
      addLine(el, e.name, 'tt-title');
      const src = store.state.sources[String(e.src)] || '';
      addLine(el, `${formatLong(e.day)}${src ? `  \u00b7  ${src}` : ''}`);
      addLine(el, `${e.kind === 1 ? 'Image' : (e.ext ? e.ext.slice(1).toUpperCase() : 'No extension')}  \u00b7  ${formatSize(e.size)}`);
      if (e.dir) addLine(el, e.dir, 'tt-dim');
      if (e.online) addLine(el, 'Online-only: not downloaded for previews or dates', 'tt-dim');
      if (e.pending && store.state.mode === 'created') addLine(el, 'Date taken not read yet: placement is provisional', 'tt-dim');
      addLine(el, 'Double-click or Enter to open', 'tt-dim');
    };
  }

  function bucketTooltip(col) {
    return (el) => {
      const b = col.bucket;
      const range = b.end - b.start === 1 ? formatLong(b.start) : `${formatLong(b.start)} \u2013 ${formatLong(b.end - 1)}`;
      addLine(el, range, 'tt-title');
      addLine(el, `${b.items.length} file${b.items.length === 1 ? '' : 's'}: ${b.images} image${b.images === 1 ? '' : 's'}, ${b.others} other`);
      addLine(el, 'Click to browse every file in this group', 'tt-dim');
    };
  }

  function hover(p) {
    const h = hitTest(m, p.x, p.y);
    canvas.style.cursor = cursorFor(h);
    const key = h.zone === 'cell' ? cellKey(h.cell, h.col.bucket) : null;
    if (key !== m.hoverKey) { m.hoverKey = key; ctl.invalidate(); }
    if (h.zone === 'cell' && h.cell.entry) ctl.tip.hover(`e:${h.cell.entry.id}`, p.x, p.y, entryTooltip(h.cell.entry));
    else if (h.zone === 'cell' || h.zone === 'header') ctl.tip.hover(`b:${h.col.bucket.k}`, p.x, p.y, bucketTooltip(h.col));
    else if (h.zone === 'ruler') ctl.tip.hover(`d:${Math.floor(h.day)}`, p.x, p.y, (el) => { addLine(el, formatLong(Math.floor(h.day))); addLine(el, 'Click to browse this day', 'tt-dim'); });
    else ctl.tip.hover(null);
  }

  function begin(e, type, p, extra = {}) {
    gesture = { type, pointerId: e.pointerId, x0: p.x, y0: p.y, view0: { ...store.state.view }, scroll0: store.state.scrollY, moved: false, ...extra };
    canvas.setPointerCapture(e.pointerId);
  }

  function end() {
    if (!gesture) return;
    try { canvas.releasePointerCapture(gesture.pointerId); } catch { /* released */ }
    gesture = null;
  }

  function onPointerDown(e) {
    ctl.tip.hide();
    const p = pos(e);
    ctl.focus();
    if (e.button === 1 || (e.button === 0 && spaceDown)) {
      e.preventDefault();
      begin(e, 'pan', p);
      return;
    }
    if (e.button !== 0) return;
    e.preventDefault();
    const h = hitTest(m, p.x, p.y);
    begin(e, h.zone === 'ruler' ? 'ruler' : 'press', p, { hit: h });
  }

  function onPointerMove(e) {
    const p = pos(e);
    if (!gesture) { hover(p); return; }
    if (gesture.pointerId !== e.pointerId) return;
    const dx = p.x - gesture.x0, dy = p.y - gesture.y0;
    if (!gesture.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD_PX) return;
    gesture.moved = true;
    canvas.style.cursor = 'grabbing';
    const d = -dx / m.ppd;
    store.setView(gesture.view0.start + d, gesture.view0.end + d, 'timeline');
    if (gesture.type !== 'ruler') ctl.scrollTo(gesture.scroll0 - dy);
  }

  function onPointerUp(e) {
    if (!gesture || gesture.pointerId !== e.pointerId) return;
    const g = gesture;
    end();
    canvas.style.cursor = cursorFor(null);
    if (g.moved || g.type === 'pan') return;
    const h = g.hit;
    if (g.type === 'ruler') { ctl.openDay(Math.floor(h.day)); return; }
    if (h.zone === 'cell' && h.cell.entry) ctl.selectEntry(h.cell.entry.id, cellKey(h.cell, h.col.bucket));
    else if (h.zone === 'cell' || h.zone === 'header') ctl.openBucket(h.col.bucket, cellKey(h.cell || { type: 'badge' }, h.col.bucket));
    else ctl.selectEntry(null, null);
  }

  function onWheel(e) {
    e.preventDefault();
    ctl.tip.hide();
    const p = pos(e);
    const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? m.H : 1;
    const dx = e.deltaX * unit, dy = Math.max(-300, Math.min(300, e.deltaY * unit));
    if (e.ctrlKey || p.y < RULER_H) {
      store.zoomAround(Math.exp(-dy * WHEEL_ZOOM_RATE), m.view.start + p.x / m.ppd, 'timeline');
    } else if (e.shiftKey || Math.abs(dx) > Math.abs(dy)) {
      const d = (dx || dy) / m.ppd;
      store.setView(store.state.view.start + d, store.state.view.end + d, 'timeline');
    } else {
      ctl.scrollTo(store.state.scrollY + dy);
    }
  }

  canvas.addEventListener('pointerdown', onPointerDown);
  canvas.addEventListener('pointermove', onPointerMove);
  canvas.addEventListener('pointerup', onPointerUp);
  canvas.addEventListener('pointercancel', end);
  canvas.addEventListener('pointerleave', () => {
    ctl.tip.hide();
    if (m.hoverKey) { m.hoverKey = null; ctl.invalidate(); }
  });
  canvas.addEventListener('wheel', onWheel, { passive: false });
  canvas.addEventListener('dblclick', (e) => {
    const p = pos(e);
    const h = hitTest(m, p.x, p.y);
    if (h.zone === 'cell' && h.cell.entry) ctl.openFile(h.cell.entry.id);
  });
  canvas.addEventListener('contextmenu', (e) => e.preventDefault());
  host.addEventListener('keydown', (e) => {
    if (e.key === ' ' && !e.ctrlKey && !e.altKey && !e.metaKey && !spaceDown) {
      spaceDown = true;
      canvas.style.cursor = 'grab';
    }
  });
  host.addEventListener('keyup', (e) => {
    if (e.key === ' ' && spaceDown) { spaceDown = false; canvas.style.cursor = cursorFor(null); }
  });
  window.addEventListener('blur', () => { spaceDown = false; });
  return { dayISO: (x) => fromDayNumber(Math.floor(m.view.start + x / m.ppd)) };
}
