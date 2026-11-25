// Pointer, wheel and keyboard gestures for the timeline canvas (R-08, R-11..R-14, R-16, R-101).
import * as actions from './actions.js';
import { addLine } from './canvas_tooltip.js';
import { formatRange, fromDayNumber } from './dates.js';
import { firstReaching } from './packing.js';
import { lockedAttempt, otherAction } from './easter_eggs.js';
import { ROW_H, RULER_H, barRect } from './timeline_draw.js';
import {
  notify, selectRange, setHoverDay, setPanel, setSelection, setView, state, toggleSelection, zoomAround,
} from './store.js';
import { xToDay } from './zoom.js';
import { FILE_DRAG_TYPE, isFileDrag } from './file_timeline/drag.js';

/** The inline hint for a locked imported item, naming its source (R-16). */
export function lockedMessage(item) {
  if (item && item.origin === 'ics') {
    const name = state.calendars[item.source_id]?.name || '';
    return `Imported from calendar feed ${name}. Change it at the source, then Sync.`;
  }
  return 'Imported from Outlook. Change it there, then Sync.';
}
export const LOCKED_MESSAGE = lockedMessage(null);

/** A refused edit of a locked item: the 2001 theme may answer with HAL instead (R-184). */
export function refuseLocked(item) {
  if (!lockedAttempt()) notify(lockedMessage(item), { kind: 'warn' });
}
const DRAG_THRESHOLD_PX = 4;
const WHEEL_ZOOM_RATE = 0.0025;

export function attachInput(tl) {
  const { m, canvas, host } = tl;
  let gesture = null;
  let spaceDown = false;

  const pos = (e) => {
    const r = canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  };
  const dayAt = (x) => xToDay(x, m.view, m.plotLeft, m.ppd);
  const itemOf = (en) => state.itemsById.get(en.id) ?? en.item;

  // ---------------------------------------------------------------- hit test

  function barAt(row, x, day) {
    const list = m.layout.rows[row];
    if (!list || !list.length) return null;
    const tol = 4 / m.ppd;
    for (let i = firstReaching(list, day - tol); i < list.length && list[i].s <= day + tol; i++) {
      const en = list[i];
      const r = barRect(m, en);
      const slack = r.w <= 12 ? 3 : 0;
      if (x < r.x - slack || x > r.x + r.w + slack) continue;
      const edge = Math.min(7, Math.max(3, r.w / 3));
      return { entry: en, edge: x - r.x < edge ? 'l' : x > r.x + r.w - edge ? 'r' : null };
    }
    return null;
  }

  function hitTest(x, y) {
    tl.ensureLayout();
    const day = dayAt(x);
    if (y < RULER_H) return { zone: 'ruler', day };
    const row = Math.floor((y - RULER_H + m.scrollY) / ROW_H);
    const base = { day, row };
    if (m.layout.headerRows.has(row)) return { zone: 'lane', ...base };
    const hit = barAt(row, x, day);
    return hit ? { zone: 'bar', ...base, ...hit } : { zone: 'empty', ...base };
  }

  function laneTagName(row) {
    const lane = m.layout.lanes.find((l) => row >= l.firstRow && row < l.firstRow + l.rowCount);
    return lane && lane.tagId ? state.tags[lane.tagId]?.name : null;
  }

  // ----------------------------------------------------------------- hovering

  function cursorFor(h) {
    if (gesture?.type === 'pan') return 'grabbing';
    if (spaceDown) return 'grab';
    if (!h) return 'default';
    if (h.zone === 'bar') return h.edge && !itemOf(h.entry).locked ? 'ew-resize' : 'pointer';
    return h.zone === 'ruler' ? 'pointer' : 'default';
  }

  function barTooltip(en) {
    return (el) => {
      const it = itemOf(en);
      const days = en.e - en.s;
      addLine(el, it.title || '(untitled)', 'tt-title');
      addLine(el, `${formatRange(en.s, en.e - 1)}  \u00b7  ${days} day${days === 1 ? '' : 's'}`);
      const names = it.tags.map((id) => state.tags[id]?.path || state.tags[id]?.name).filter(Boolean);
      if (names.length) addLine(el, `Tags: ${names.join(', ')}`);
      if (names.length) addLine(el, 'Ctrl+drag onto another item to copy its tags, or onto blank space to duplicate it', 'tt-dim');
      else addLine(el, 'Ctrl+drag onto blank space to duplicate this item', 'tt-dim');
      const kind = it.kind === 'log' ? 'Log entry' : 'Plan';
      const src = it.source_id ? state.calendars[it.source_id] : null;
      const origin = it.origin === 'local' ? 'Local'
        : `Imported from ${it.origin === 'ics' ? 'calendar feed' : 'Outlook'}${src ? ` ${src.name}` : ''} (read-only dates and title)`;
      addLine(el, `${kind} \u00b7 ${origin}`, 'tt-dim');
      if (it.attachments_summary) addLine(el, it.attachments_summary, 'tt-dim');
      const notes = (it.notes || '').trim();
      if (notes) addLine(el, notes.length > 160 ? `${notes.slice(0, 160)}\u2026` : notes, 'tt-notes');
    };
  }

  function hover(p) {
    const h = hitTest(p.x, p.y);
    setHoverDay(Math.floor(h.day));
    canvas.style.cursor = cursorFor(h);
    if (h.zone === 'bar') tl.tip.hover(`bar:${h.entry.id}`, p.x, p.y, barTooltip(h.entry));
    else if (h.zone === 'ruler') tl.tip.hide();
    else tl.tip.hover(null);
  }

  // ----------------------------------------------------------------- gestures

  function begin(e, g, p) {
    gesture = { ...g, pointerId: e.pointerId, x0: p.x, y0: p.y, view0: { ...state.view }, scroll0: m.scrollY, moved: false };
    canvas.setPointerCapture(e.pointerId);
    window.addEventListener('keydown', onEscape, true);
    canvas.style.cursor = cursorFor(null);
  }

  function endGesture() {
    if (!gesture) return;
    window.removeEventListener('keydown', onEscape, true);
    try { canvas.releasePointerCapture(gesture.pointerId); } catch { /* already released */ }
    gesture = null;
    m.drag = m.create = m.marquee = m.tagDrag = null;
    tl.invalidate();
  }

  function onEscape(e) {
    if (e.key !== 'Escape') return;
    e.preventDefault();
    e.stopImmediatePropagation();
    endGesture();
  }

  function panTo(g, dx) {
    const d = -dx / m.ppd;
    setView(g.view0.start + d, g.view0.end + d, 'timeline');
  }

  function beginBarDrag(g) {
    const en = g.entry;
    if (itemOf(en).locked) {
      refuseLocked(itemOf(en));
      g.type = 'dead';
      return;
    }
    const ids = g.mode === 'move'
      ? new Set([...state.selection].filter((id) => !state.itemsById.get(id)?.locked && m.layout.entries.has(id)))
      : new Set([en.id]);
    g.delta = 0;
    m.drag = {
      ids,
      span: (e2) => spanFor(g, e2),
    };
  }

  function spanFor(g, en) {
    if (g.mode === 'move') return { s: en.s + g.delta, e: en.e + g.delta };
    if (en.id !== g.entry.id) return { s: en.s, e: en.e };
    if (g.mode === 'l') return { s: Math.min(en.s + g.delta, en.e - 1), e: en.e };
    return { s: en.s, e: Math.max(en.e + g.delta, en.s + 1) };
  }

  const MOVES = {
    pan(g, p, dx, dy) {
      panTo(g, dx);
      m.scrollY = g.scroll0 - dy;
      tl.clampScroll();
    },
    ruler(g, p, dx) { panTo(g, dx); },
    bar(g, p, dx) {
      if (!m.drag) beginBarDrag(g);
      if (g.type !== 'bar') return;
      g.delta = Math.round(dx / m.ppd);
    },
    create(g, p) {
      const d = Math.floor(dayAt(p.x));
      m.create = { row: g.row, s: Math.min(g.day0, d), e: Math.max(g.day0, d) + 1 };
    },
    marquee(g, p) { m.marquee = { x0: g.x0, y0: g.y0, x1: p.x, y1: p.y }; },
    tagdrag(g, p) {
      const src = itemOf(g.entry);
      if (!m.tagDrag) {
        m.tagDrag = { sourceId: src.id, slots: src.tags.map((t) => state.tags[t]?.slot ?? -1), targetId: null, ghost: null, x: p.x, y: p.y };
      }
      const h = hitTest(p.x, p.y);
      const d = m.tagDrag;
      const onOther = h.zone === 'bar' && h.entry.id !== src.id;
      d.x = p.x;
      d.y = p.y;
      d.targetId = onOther && src.tags.length ? h.entry.id : null;
      d.noTags = onOther && !src.tags.length;
      if (h.zone === 'empty') {     // over blank space the copy is previewed where it will land (R-49)
        const delta = Math.round((p.x - g.x0) / m.ppd);
        d.ghost = { row: h.row, s: g.entry.s + delta, e: g.entry.e + delta };
      } else {
        d.ghost = null;
      }
    },
    blank() {},
    dead() {},
  };

  function marqueeIds(q) {
    const [xa, xb] = [Math.min(q.x0, q.x1), Math.max(q.x0, q.x1)];
    const [ya, yb] = [Math.min(q.y0, q.y1), Math.max(q.y0, q.y1)];
    const d0 = dayAt(xa), d1 = dayAt(xb);
    const r0 = Math.max(0, Math.floor((ya - RULER_H + m.scrollY) / ROW_H));
    const r1 = Math.min(m.layout.rows.length - 1, Math.floor((yb - RULER_H + m.scrollY) / ROW_H));
    const ids = [];
    for (let r = r0; r <= r1; r++) {
      for (const en of m.layout.rows[r]) if (en.s < d1 && en.e > d0) ids.push(en.id);
    }
    return ids;
  }

  async function createFromDrag(c) {
    const tag = laneTagName(c.row);
    const id = await actions.createItem({
      title: 'New item', start: fromDayNumber(c.s), end: fromDayNumber(c.e - 1), tags: tag ? [tag] : [],
    });
    if (id) tl.startRename(id);
  }

  async function duplicateFromDrag(sourceId, ghost) {
    const id = await actions.duplicateItem(sourceId, fromDayNumber(ghost.s));
    if (id) tl.startRename(id);
  }

  function commitBarDrag(g) {
    if (!m.drag || !g.delta) return null;
    const en = g.entry, id = en.id;
    if (g.mode === 'move') {
      const ids = [...m.drag.ids];
      if (ids.length > 1) return () => actions.moveItems(ids, g.delta);
      const sp = spanFor(g, en);
      return () => actions.patchItem(id, { start_date: fromDayNumber(sp.s), end_date: fromDayNumber(sp.e - 1) });
    }
    const sp = spanFor(g, en);
    return () => actions.patchItem(id, g.mode === 'l' ? { start_date: fromDayNumber(sp.s) } : { end_date: fromDayNumber(sp.e - 1) });
  }

  /** Returns the action to run once the gesture state is cleared (so repacking is no longer deferred). */
  function finish(g, p) {
    switch (g.type) {
      case 'ruler': return g.moved ? null : () => setPanel({ open: true, tab: 'day', dayISO: fromDayNumber(Math.floor(g.day)) });
      case 'bar': return g.moved ? commitBarDrag(g) : () => setSelection([g.entry.id], g.entry.id);
      case 'create': return g.moved && m.create ? createFromDrag.bind(null, { ...m.create }) : () => setSelection([]);
      case 'marquee': {
        const ids = g.moved && m.marquee ? marqueeIds(m.marquee) : [];
        return () => setSelection(ids, ids[0]);
      }
      case 'blank': return () => setSelection([]);
      case 'tagdrag': {
        if (!g.moved) return () => toggleSelection(g.entry.id);
        const d = m.tagDrag;
        if (d?.targetId) return () => actions.copyTags(g.entry.id, [d.targetId]);
        if (d?.noTags) return () => notify(`'${itemOf(g.entry).title}' has no tags to copy`, { kind: 'warn' });
        if (d?.ghost) return duplicateFromDrag.bind(null, g.entry.id, { ...d.ghost });
        return null;
      }
      default: return null;
    }
  }

  // ------------------------------------------------------------------ events

  function onPointerDown(e) {
    tl.tip.hide();
    const p = pos(e);
    if (e.button === 1 || (e.button === 0 && spaceDown)) {
      e.preventDefault();
      begin(e, { type: 'pan' }, p);
      return;
    }
    canvas.focus({ preventScroll: true });
    if (e.button === 2) return rightClick(p);
    if (e.button !== 0) return;
    e.preventDefault();
    const h = hitTest(p.x, p.y);
    if (h.zone === 'ruler') { begin(e, { type: 'ruler', day: h.day }, p); return; }
    if (h.zone === 'bar') { barDown(e, h, p); return; }
    if (e.shiftKey) begin(e, { type: 'marquee' }, p);
    else if (h.zone === 'empty') begin(e, { type: 'create', row: h.row, day0: Math.floor(h.day) }, p);
    else begin(e, { type: 'blank' }, p);
  }

  function barDown(e, h, p) {
    const id = h.entry.id;
    if (!itemOf(h.entry).locked || e.ctrlKey || e.shiftKey) otherAction();
    if (e.shiftKey) { selectRange(id); return; }
    if (e.ctrlKey || e.metaKey) {
      // a plain Ctrl+click still toggles the selection; Ctrl+drag copies the tags to the bar it is dropped on (R-48)
      begin(e, { type: 'tagdrag', entry: h.entry }, p);
      return;
    }
    if (!state.selection.has(id)) setSelection([id], id);
    else if (state.primaryId !== id && state.selection.size === 1) setSelection([id], id);
    begin(e, { type: 'bar', entry: h.entry, mode: h.edge === 'l' ? 'l' : h.edge === 'r' ? 'r' : 'move' }, p);
  }

  function rightClick(p) {
    const h = hitTest(p.x, p.y);
    if (h.zone === 'bar' && !state.selection.has(h.entry.id)) setSelection([h.entry.id], h.entry.id);
  }

  function onPointerMove(e) {
    const p = pos(e);
    if (!gesture) { hover(p); return; }
    if (gesture.pointerId !== e.pointerId) return;
    const dx = p.x - gesture.x0, dy = p.y - gesture.y0;
    if (!gesture.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD_PX) return;
    gesture.moved = true;
    MOVES[gesture.type](gesture, p, dx, dy);
    canvas.style.cursor = gesture.type === 'tagdrag' ? (m.tagDrag?.targetId || m.tagDrag?.ghost ? 'copy' : 'grabbing')
      : gesture.type === 'bar' && gesture.mode !== 'move' ? 'ew-resize' : gesture.type === 'bar' ? 'grabbing' : cursorFor(null);
    tl.invalidate();
  }

  function onPointerUp(e) {
    if (!gesture || gesture.pointerId !== e.pointerId) return;
    const g = gesture;
    const action = finish(g, pos(e));
    endGesture();
    canvas.style.cursor = cursorFor(null);
    if (action) action();
  }

  function onWheel(e) {
    e.preventDefault();
    tl.tip.hide();
    const p = pos(e);
    const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? m.bodyH : 1;
    const dx = e.deltaX * unit, dy = Math.max(-300, Math.min(300, e.deltaY * unit));
    tl.ensureLayout();
    if (e.ctrlKey || p.y < RULER_H) {
      zoomAround(Math.exp(-dy * WHEEL_ZOOM_RATE), dayAt(p.x), 'timeline');
    } else if (e.shiftKey || Math.abs(dx) > Math.abs(dy)) {
      const d = (dx || dy) / m.ppd;
      setView(state.view.start + d, state.view.end + d, 'timeline');
    } else {
      m.scrollY += dy;
      tl.clampScroll();
      tl.invalidate();
    }
  }

  function onKeyDown(e) {
    if (e.target !== canvas && e.target !== host) return;
    if (e.key === 'F2' && state.primaryId) { e.preventDefault(); tl.startRename(state.primaryId); }
    else if (e.key === ' ' && !e.ctrlKey && !e.altKey && !e.metaKey) {
      e.preventDefault();
      if (!spaceDown) { spaceDown = true; canvas.style.cursor = 'grab'; }
    }
  }

  function releaseSpace() { if (spaceDown) { spaceDown = false; canvas.style.cursor = cursorFor(null); } }

  canvas.addEventListener('pointerdown', onPointerDown);
  canvas.addEventListener('pointermove', onPointerMove);
  canvas.addEventListener('pointerup', onPointerUp);
  canvas.addEventListener('pointercancel', () => { endGesture(); });
  canvas.addEventListener('pointerleave', () => {
    tl.tip.hide();
    if (!gesture) setHoverDay(null);
  });

  // ---------------------------------------------------- filter chip dropped on a bar (R-115)

  function chipTargets(clientX, clientY) {
    const r = canvas.getBoundingClientRect();
    const x = clientX - r.left, y = clientY - r.top;
    if (x < 0 || y < 0 || x > r.width || y > r.height) return { x, y, ids: [] };
    const h = hitTest(x, y);
    if (h.zone !== 'bar') return { x, y, ids: [] };
    const id = h.entry.id;
    const ids = state.selection.has(id) && state.selection.size > 1 ? [...state.selection] : [id];
    return { x, y, ids };
  }
  tl.chipDrag = {
    move(clientX, clientY, tag) {
      const t = chipTargets(clientX, clientY);
      m.chipDrag = { name: tag.name, slot: tag.slot, x: t.x, y: t.y, targets: t.ids };
      canvas.style.cursor = t.ids.length ? 'copy' : 'no-drop';
      tl.invalidate();
      return t.ids.length > 0;
    },
    end(clientX, clientY, tag) {
      const t = chipTargets(clientX, clientY);
      m.chipDrag = null;
      canvas.style.cursor = cursorFor(null);
      tl.markDirty();
      if (t.ids.length) actions.addTagToItems(t.ids, tag.name);
      return t.ids.length > 0;
    },
    cancel() { m.chipDrag = null; canvas.style.cursor = cursorFor(null); tl.markDirty(); },
  };
  canvas.addEventListener('wheel', onWheel, { passive: false });
  canvas.addEventListener('dblclick', (e) => {
    const p = pos(e);
    const h = hitTest(p.x, p.y);
    if (h.zone === 'bar') tl.startRename(h.entry.id);
  });
  host.addEventListener('keydown', onKeyDown);
  host.addEventListener('keyup', (e) => { if (e.key === ' ') releaseSpace(); });
  window.addEventListener('blur', releaseSpace);

  // Dropping a file or URL on a bar adds a link to that item (R-72)
  const linkDrag = (dt) => isFileDrag(dt) || (!!dt && ['Files', 'text/uri-list', 'text/plain'].some((t) => [...dt.types].includes(t)));
  const dropPath = (dt) => {
    const f = dt.files && dt.files[0];
    if (f) return f.pywebviewFullPath || f.path || '';
    const uri = (dt.getData('text/uri-list') || dt.getData('text/plain') || '').split(/\r?\n/).find((l) => l && !l.startsWith('#'));
    return uri ? uri.trim() : '';
  };
  canvas.addEventListener('dragover', (e) => {
    if (!linkDrag(e.dataTransfer)) return;
    const p = pos(e);
    if (hitTest(p.x, p.y).zone === 'bar') e.preventDefault();
  });
  canvas.addEventListener('drop', (e) => {
    if (!linkDrag(e.dataTransfer)) return;
    e.preventDefault();
    const p = pos(e);
    const h = hitTest(p.x, p.y);
    if (h.zone !== 'bar') return;
    if (isFileDrag(e.dataTransfer)) {
      const fileId = e.dataTransfer.getData(FILE_DRAG_TYPE);
      if (!fileId) { notify('Could not read the dragged file.', { kind: 'warn' }); return; }
      actions.addIndexedFileAttachment(h.entry.id, fileId).then((result) => {
        if (result) setSelection([h.entry.id], h.entry.id);
      });
      return;
    }
    const path = dropPath(e.dataTransfer);
    if (!path) { notify('Could not read the path of that file. Paste it into the Add link field instead.', { kind: 'warn' }); return; }
    actions.addAttachment(h.entry.id, path);
    setSelection([h.entry.id], h.entry.id);
  });
}
