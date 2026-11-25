// Day browser (R-146) and Undated list: a virtualized, keyboard-operable list of every matching file in a bucket,
// with name, type, size, date and date source. Only the visible rows exist in the DOM; thumbnails come from the
// shared bounded loader and are painted into small canvases (no blob: URLs, which the CSP does not allow).
import { formatShort } from '../dates.js';
import { h } from '../dom.js';
import { FILE_DRAG_TYPE } from './drag.js';
import { fitInside, formatSize, typeGroupOf, typeLabel, visibleRows } from './groups.js';

const ROW_H = 44;
const THUMB = 36;

export function createBrowser({ loader, icons, getTheme, sources, onSelect, onOpen, onDragEnd, idPrefix = 'ftb' }) {
  let items = [];
  let active = -1;
  let selectedId = null;
  const list = h('div', { class: 'ft-browser__list', role: 'listbox', tabindex: '0', 'aria-label': 'Files' });
  const spacer = h('div', { class: 'ft-browser__spacer' });
  list.append(spacer);
  const rendered = new Map();       // index -> row element
  let pending = false;
  let dragging = false, redrawAfterDrag = false;

  const typeNames = { image: 'Image', document: 'Document', video: 'Video', audio: 'Audio', archive: 'Archive', other: 'Other' };
  const typeName = (e) => e.ext === '.pdf' ? 'PDF' : typeNames[typeGroupOf(e.ext, e.kind)];

  function paintThumb(cv, e) {
    const theme = getTheme();
    const dpr = window.devicePixelRatio || 1;
    if (cv.width !== THUMB * dpr) { cv.width = THUMB * dpr; cv.height = THUMB * dpr; }
    const ctx = cv.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, THUMB, THUMB);
    ctx.fillStyle = theme['file-placeholder-bg'];
    ctx.fillRect(0, 0, THUMB, THUMB);
    const th = e.kind === 1 && !e.online ? loader.get(e.id) : null;
    const icon = icons?.get(e.ext);
    const asset = th?.status === 'ok' ? th : icon?.status === 'ok' ? icon : null;
    if (asset) {
      const f = fitInside({ x: 0, y: 0, w: THUMB, h: THUMB }, asset.w, asset.h);
      ctx.drawImage(asset.bitmap, f.x, f.y, f.w, f.h);
      return;
    }
    ctx.fillStyle = theme['file-placeholder-fg'];
    ctx.font = `600 10px ${theme.fontUi}`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(e.online ? 'cloud' : typeLabel(e.ext), THUMB / 2, THUMB / 2);
  }

  function rowFor(i) {
    const e = items[i];
    const cv = h('canvas', { class: 'ft-browser__thumb', 'aria-hidden': 'true' });
    cv.style.width = `${THUMB}px`;
    cv.style.height = `${THUMB}px`;
    paintThumb(cv, e);
    const src = sources()[String(e.src)] || '';
    const meta = [typeName(e), formatSize(e.size), Number.isFinite(e.day) ? formatShort(e.day) : 'Undated', src]
      .filter(Boolean).join(' \u00b7 ');
    const row = h('div', {
      class: `ft-browser__row${i === active ? ' is-active' : ''}${e.id === selectedId ? ' is-selected' : ''}`,
      role: 'option', id: `${idPrefix}-${i}`, 'aria-selected': String(e.id === selectedId),
      draggable: true,
      'aria-label': `${e.name}, ${meta}${e.online ? ', online-only' : ''}`,
      dataset: { index: String(i) },
    }, cv, h('div', { class: 'ft-browser__text' },
      h('div', { class: 'ft-browser__name', text: e.name }),
      h('div', { class: 'ft-browser__meta', text: meta + (e.online ? ' \u00b7 online-only' : '') })));
    row.style.top = `${i * ROW_H}px`;
    row.style.height = `${ROW_H}px`;
    return row;
  }

  function render() {
    pending = false;
    if (dragging) { redrawAfterDrag = true; return; }
    spacer.style.height = `${items.length * ROW_H}px`;
    const { first, last } = visibleRows(list.scrollTop, list.clientHeight || 400, ROW_H, items.length, 6);
    for (const [i, el] of rendered) {
      if (i < first || i > last) { el.remove(); rendered.delete(i); }
    }
    const want = [], wantedIcons = [];
    for (let i = first; i <= last; i++) {
      const e = items[i];
      if (e.kind === 1 && !e.online) want.push(e.id);
      wantedIcons.push(e.ext);
      const old = rendered.get(i);
      const fresh = rowFor(i);
      if (old) old.replaceWith(fresh); else list.append(fresh);
      rendered.set(i, fresh);
    }
    loader.want(want, 'browser');
    icons?.want(wantedIcons, 'browser');
    if (active >= 0) list.setAttribute('aria-activedescendant', `${idPrefix}-${active}`);
    else list.removeAttribute('aria-activedescendant');
  }

  function schedule() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(render);
  }

  function ensureVisible(i) {
    const top = i * ROW_H;
    if (top < list.scrollTop) list.scrollTop = top;
    else if (top + ROW_H > list.scrollTop + list.clientHeight) list.scrollTop = top + ROW_H - list.clientHeight;
  }

  function setActive(i, select = true) {
    if (!items.length) return;
    active = Math.max(0, Math.min(items.length - 1, i));
    ensureVisible(active);
    if (select) { selectedId = items[active].id; onSelect?.(items[active], { fromKeyboard: true }); }
    render();
  }

  list.addEventListener('scroll', schedule);
  list.addEventListener('dragstart', (ev) => {
    const row = ev.target.closest('.ft-browser__row');
    const item = row && items[Number(row.dataset.index)];
    if (!item || !ev.dataTransfer) { ev.preventDefault(); return; }
    dragging = true;
    ev.dataTransfer.setData(FILE_DRAG_TYPE, item.id);
    ev.dataTransfer.effectAllowed = 'copy';
  });
  list.addEventListener('dragend', () => {
    dragging = false;
    onDragEnd?.();
    if (redrawAfterDrag) { redrawAfterDrag = false; schedule(); }
  });
  list.addEventListener('click', (ev) => {
    const row = ev.target.closest('.ft-browser__row');
    if (!row) return;
    const i = Number(row.dataset.index);
    active = i;
    selectedId = items[i].id;
    onSelect?.(items[i], { fromKeyboard: false });
    render();
  });
  list.addEventListener('dblclick', (ev) => {
    const row = ev.target.closest('.ft-browser__row');
    if (row) onOpen?.(items[Number(row.dataset.index)]);
  });
  list.addEventListener('keydown', (ev) => {
    const page = Math.max(1, Math.floor(list.clientHeight / ROW_H) - 1);
    const map = { ArrowDown: active + 1, ArrowUp: active - 1, PageDown: active + page, PageUp: active - page, Home: 0, End: items.length - 1 };
    if (ev.key in map) {
      ev.preventDefault();
      ev.stopPropagation();
      setActive(active < 0 && ev.key !== 'End' ? 0 : map[ev.key]);
    } else if (ev.key === 'Enter' && active >= 0) {
      ev.preventDefault();
      ev.stopPropagation();
      onOpen?.(items[active]);
    }
  });
  list.addEventListener('focus', () => { if (active < 0 && items.length) { active = 0; render(); } });
  new ResizeObserver(schedule).observe(list);

  return {
    el: list,
    set(newItems, { keepScroll = false } = {}) {
      items = newItems;
      if (!keepScroll) { list.scrollTop = 0; active = -1; }
      active = Math.min(active, items.length - 1);
      for (const el of rendered.values()) el.remove();
      rendered.clear();
      render();
    },
    setSelected(id) {
      selectedId = id;
      const i = items.findIndex((e) => e.id === id);
      if (i >= 0) { active = i; ensureVisible(i); }
      schedule();
    },
    refresh: schedule,
    isDragging: () => dragging,
    release() { loader.want([], 'browser'); icons?.want([], 'browser'); },
    focus() { list.focus({ preventScroll: true }); },
    count: () => items.length,
  };
}
