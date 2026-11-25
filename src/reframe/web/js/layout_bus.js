// The single shared Gantt layout (spec 4.1 step 8). The timeline publishes every new packing result here and the
// minimap redraws synchronously, in the same animation frame, from exactly that result (R-21).
let current = null;
const listeners = new Set();

export function publishLayout(layout) {
  current = layout;
  for (const fn of listeners) {
    try { fn(layout); } catch (e) { console.error('layout listener failed', e); }
  }
}

export function currentLayout() { return current; }

export function onLayout(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** The vertical extent of the main viewport in rows, outlined on the minimap frame (R-25). */
let viewport = { top: 0, bottom: 0, rows: 0 };
export function publishViewport(v) {
  viewport = v;
  for (const fn of viewportListeners) fn(v);
}
export function currentViewport() { return viewport; }
const viewportListeners = new Set();
export function onViewport(fn) { viewportListeners.add(fn); return () => viewportListeners.delete(fn); }
