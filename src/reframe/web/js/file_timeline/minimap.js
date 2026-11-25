// File timeline minimap (R-151): its own state, file density per day (images and other files in their own token
// colours), and the same movable/resizable frame as the Gantt minimap (R-22 - R-25). It writes the view only through
// the store and never reacts to a 'view' event by writing back, so it cannot oscillate with the main canvas.
import { addLine, createCanvasTooltip } from '../canvas_tooltip.js';
import { formatLong, formatShort } from '../dates.js';
import { densityColumns, frameRect } from './groups.js';

const PAD_X = 8;
const PAD_Y = 6;
const MIN_FRAME_PX = 12;
const EDGE_PX = 6;
const WHEEL_ZOOM_RATE = 0.0025;

export function initFileMinimap(host, { store, getTheme, getToday, getScroll }) {
  const canvas = document.createElement('canvas');
  canvas.className = 'ft-minimap__canvas';
  canvas.setAttribute('role', 'img');
  canvas.setAttribute('aria-label', 'File timeline overview: file density per day. Drag the frame to move the visible window.');
  host.append(canvas);
  const ctx = canvas.getContext('2d');
  const tip = createCanvasTooltip(host);
  let W = 0, H = 0, dpr = 1;
  let pending = false;
  let range = { lo: 0, hi: 1 };
  let frozen = null;
  let hoverZone = null;
  let gesture = null;
  let dens = null;
  let densKey = '';

  function currentRange() {
    if (frozen) return frozen;
    const r = store.range();
    const v = store.state.view;
    const lo = Math.min(r.lo, v.start), hi = Math.max(r.hi, v.end);
    const pad = (hi - lo) * 0.01;
    return { lo: lo - pad, hi: hi + pad };
  }

  const plotW = () => Math.max(1, W - 2 * PAD_X);
  const scale = () => plotW() / (range.hi - range.lo);
  const xOf = (day) => PAD_X + (day - range.lo) * scale();
  const dayAt = (x) => range.lo + (x - PAD_X) / scale();
  const frame = () => frameRect(store.state.view, range, plotW(), PAD_X, MIN_FRAME_PX);

  function zoneAt(x) {
    const f = frame();
    if (x < f.x - 3 || x > f.x + f.w + 3) return 'outside';
    const edge = Math.min(EDGE_PX, f.w / 3);
    if (x <= f.x + edge) return 'l';
    if (x >= f.x + f.w - edge) return 'r';
    return 'body';
  }

  function syncSize() {
    const d = window.devicePixelRatio || 1;
    const w = host.clientWidth, h = host.clientHeight;
    if (w === W && h === H && d === dpr && canvas.width) return;
    W = w; H = h; dpr = d;
    canvas.width = Math.max(1, Math.round(W * dpr));
    canvas.height = Math.max(1, Math.round(H * dpr));
  }

  function draw() {
    pending = false;
    if (!host.isConnected || host.clientWidth === 0) return;
    syncSize();
    range = currentRange();
    const theme = getTheme();
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = theme['minimap-bg'];
    ctx.fillRect(0, 0, W, H);
    const key = `${store.state.layoutVersion}|${range.lo}|${range.hi}|${W}`;
    if (key !== densKey) {
      dens = densityColumns(store.state.filtered, range.lo, range.hi, plotW());
      densKey = key;
    }
    const avail = H - 2 * PAD_Y;
    const max = Math.max(1, dens.max);
    const hOf = (n) => (n ? Math.max(1, Math.round((Math.sqrt(n) / Math.sqrt(max)) * avail)) : 0);
    for (const [arr, color, stacked] of [[dens.img, theme['minimap-files-image'], false], [dens.oth, theme['minimap-files-other'], true]]) {
      ctx.fillStyle = color;
      for (let i = 0; i < dens.n; i++) {
        const n = arr[i];
        if (!n) continue;
        const base = stacked ? hOf(dens.img[i] + n) : hOf(n);
        const below = stacked ? hOf(dens.img[i]) : 0;
        const segH = Math.max(1, base - below);
        ctx.fillRect(PAD_X + i, PAD_Y + avail - base, 1, segH);
      }
    }
    const nx = Math.round(xOf(getToday() + 0.5));
    ctx.fillStyle = theme['now-line-on-ruler'];
    ctx.fillRect(nx - 1, 0, 2, H);
    drawFrame(theme);
  }

  function drawFrame(theme) {
    const f = frame();
    const active = !!gesture;
    ctx.fillStyle = theme['minimap-frame-fill'];
    ctx.fillRect(f.x, 1, f.w, H - 2);
    if (hoverZone === 'body' || (active && gesture.type === 'pan')) {
      ctx.globalAlpha = 0.6;
      ctx.fillRect(f.x, 1, f.w, H - 2);
      ctx.globalAlpha = 1;
    }
    ctx.strokeStyle = theme['minimap-frame'];
    ctx.lineWidth = active ? 3 : hoverZone && hoverZone !== 'outside' ? 2.5 : 2;
    ctx.strokeRect(f.x + 1, 2, f.w - 2, H - 4);
    const sc = getScroll();                 // vertical extent of the main view when it is scrolled (R-25)
    if (sc.contentH > sc.bodyH && sc.contentH > 0) {
      const y0 = 2 + ((H - 4) * sc.scrollY) / sc.contentH;
      const hh = Math.max(3, ((H - 4) * sc.bodyH) / sc.contentH);
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 2]);
      ctx.strokeRect(f.x + 3.5, y0 + 0.5, Math.max(1, f.w - 7), Math.min(hh, H - 4 - y0));
      ctx.setLineDash([]);
    }
    ctx.fillStyle = theme['minimap-frame'];
    const gh = Math.round(H * 0.5);
    for (const [side, x] of [['l', f.x - 1], ['r', f.x + f.w - 5]]) {
      const big = hoverZone === side || (active && gesture.type === side);
      ctx.fillRect(x, (H - gh) / 2 - (big ? 4 : 0), 6, gh + (big ? 8 : 0));
    }
  }

  function invalidate() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(draw);
  }

  const posX = (e) => e.clientX - canvas.getBoundingClientRect().left;
  const cursorFor = (z) => (z === 'l' || z === 'r' ? 'ew-resize' : z === 'body' ? 'grab' : 'pointer');

  function onDown(e) {
    if (e.button !== 0) return;
    e.preventDefault();
    tip.hide();
    range = currentRange();
    frozen = range;
    const x = posX(e);
    let zone = zoneAt(x);
    if (zone === 'outside') {
      const v = store.state.view, span = v.end - v.start, c = dayAt(x);
      store.setView(c - span / 2, c + span / 2, 'minimap');
      zone = 'body';
    }
    gesture = { type: zone === 'body' ? 'pan' : zone, pointerId: e.pointerId, x0: x, v0: { ...store.state.view } };
    canvas.setPointerCapture(e.pointerId);
    canvas.style.cursor = zone === 'body' ? 'grabbing' : 'ew-resize';
    invalidate();
  }

  function onMove(e) {
    const x = posX(e);
    if (!gesture) {
      range = currentRange();
      const zone = zoneAt(x);
      if (zone !== hoverZone) { hoverZone = zone; invalidate(); }
      canvas.style.cursor = cursorFor(zone);
      const v = store.state.view;
      const win = `Window: ${formatShort(Math.floor(v.start))} \u2013 ${formatShort(Math.floor(v.end))}`;
      if (zone === 'outside') {
        tip.hover(`out:${Math.floor(dayAt(x))}`, x, H / 2, (el) => { addLine(el, formatLong(Math.floor(dayAt(x))), 'tt-title'); addLine(el, 'Click to jump here', 'tt-dim'); });
      } else {
        tip.hover(`zone:${zone}`, x, H / 2, (el) => { addLine(el, zone === 'body' ? 'Drag to pan the file timeline' : 'Drag to zoom', 'tt-title'); addLine(el, win, 'tt-dim'); });
      }
      return;
    }
    if (gesture.pointerId !== e.pointerId) return;
    const { v0 } = gesture;
    const d = (x - gesture.x0) / scale();
    if (gesture.type === 'pan') store.setView(v0.start + d, v0.end + d, 'minimap');
    else if (gesture.type === 'l') store.setView(Math.min(v0.start + d, v0.end - 7), v0.end, 'minimap');
    else if (gesture.type === 'r') store.setView(v0.start, Math.max(v0.end + d, v0.start + 7), 'minimap');
    invalidate();
  }

  function endGesture() {
    if (!gesture) return;
    try { canvas.releasePointerCapture(gesture.pointerId); } catch { /* released */ }
    gesture = null;
    frozen = null;
    canvas.style.cursor = cursorFor(hoverZone);
    invalidate();
  }

  canvas.addEventListener('pointerdown', onDown);
  canvas.addEventListener('pointermove', onMove);
  canvas.addEventListener('pointerup', endGesture);
  canvas.addEventListener('pointercancel', endGesture);
  canvas.addEventListener('pointerleave', () => { tip.hide(); hoverZone = null; invalidate(); });
  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    tip.hide();
    const dy = Math.max(-300, Math.min(300, e.deltaY * (e.deltaMode === 1 ? 16 : 1)));
    const v = store.state.view;
    store.zoomAround(Math.exp(-dy * WHEEL_ZOOM_RATE), (v.start + v.end) / 2, 'minimap');
  }, { passive: false });
  new ResizeObserver(invalidate).observe(host);
  return { invalidate };
}
