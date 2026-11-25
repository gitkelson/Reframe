// Accessibility mirror for the canvas (R-103): an off-screen live region with a summary of the visible items and
// the selected item, plus a focusable listbox of the items in the current window.
import { formatRange, toDayNumber } from './dates.js';
import { revealItem } from './timeline.js';
import { setSelection, on, state, visibleItems } from './store.js';

const MAX_OPTIONS = 200;
const REBUILD_DELAY_MS = 300;
const ANNOUNCE_DELAY_MS = 600;

function make(tag, attrs = {}) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}

/** Items overlapping the window, capped to the MAX_OPTIONS nearest its centre, in time order. */
export function itemsInWindow(items, view, cap = MAX_OPTIONS) {
  const centre = (view.start + view.end) / 2;
  const rows = [];
  for (const it of items) {
    const s = toDayNumber(it.start_date), e = toDayNumber(it.end_date) + 1;
    if (e > view.start && s < view.end) rows.push({ it, s, e, d: Math.abs((s + e) / 2 - centre) });
  }
  const total = rows.length;
  const kept = total > cap ? rows.sort((a, b) => a.d - b.d).slice(0, cap) : rows;
  kept.sort((a, b) => a.s - b.s || (a.it.id < b.it.id ? -1 : 1));
  return { total, rows: kept };
}

export function initA11y(el) {
  const live = make('div', { role: 'status', 'aria-live': 'polite', 'aria-atomic': 'true' });
  const list = make('ul', { role: 'listbox', 'aria-label': 'Items in the current window. Enter selects an item.', tabindex: '-1' });
  el.append(live, list);
  let rebuildTimer = null;
  let announceTimer = null;
  let firstTitle = '';

  const describe = (it, s, e) => `${it.title || '(untitled)'}, ${formatRange(s, e - 1)}${it.kind === 'log' ? ', log entry' : ''}`;

  function rebuild() {
    const focusedId = list.contains(document.activeElement) ? document.activeElement.dataset.id : null;
    const { total, rows } = itemsInWindow(visibleItems(), state.view);
    list.replaceChildren(...rows.map(({ it, s, e }) => {
      const li = make('li', { role: 'option', tabindex: '-1', 'data-id': it.id, 'aria-selected': String(state.selection.has(it.id)) });
      li.textContent = describe(it, s, e);
      return li;
    }));
    const first = rows[0];
    firstTitle = first ? describe(first.it, first.s, first.e) : '';
    list.dataset.total = String(total);
    syncTabStop();
    if (focusedId) list.querySelector(`[data-id="${CSS.escape(focusedId)}"]`)?.focus();
    scheduleAnnounce();
  }

  function syncTabStop() {
    const options = [...list.children];
    options.forEach((o) => { o.tabIndex = -1; });
    const stop = options.find((o) => o.getAttribute('aria-selected') === 'true') || options[0];
    if (stop) stop.tabIndex = 0;
  }

  function announce() {
    const total = Number(list.dataset.total || 0);
    const sel = state.primaryId ? state.itemsById.get(state.primaryId) : null;
    const parts = [`${total} item${total === 1 ? '' : 's'} in view`];
    if (firstTitle) parts.push(`first ${firstTitle}`);
    if (sel) parts.push(`selected ${sel.title}, ${formatRange(toDayNumber(sel.start_date), toDayNumber(sel.end_date))}`);
    live.textContent = parts.join(', ');
  }

  function scheduleAnnounce() {
    clearTimeout(announceTimer);
    announceTimer = setTimeout(announce, ANNOUNCE_DELAY_MS);
  }

  function scheduleRebuild() {
    clearTimeout(rebuildTimer);
    rebuildTimer = setTimeout(rebuild, REBUILD_DELAY_MS);
  }

  function onSelection() {
    for (const o of list.children) o.setAttribute('aria-selected', String(state.selection.has(o.dataset.id)));
    syncTabStop();
    scheduleAnnounce();
  }

  function activate(option) {
    const id = option.dataset.id;
    setSelection([id], id);
    revealItem(id);
  }

  list.addEventListener('click', (e) => { const o = e.target.closest('[role=option]'); if (o) activate(o); });
  list.addEventListener('keydown', (e) => {
    const o = e.target.closest('[role=option]');
    if (!o) return;
    const options = [...list.children];
    const i = options.indexOf(o);
    const go = (n) => { e.preventDefault(); options[Math.max(0, Math.min(options.length - 1, n))]?.focus(); };
    if (e.key === 'ArrowDown') go(i + 1);
    else if (e.key === 'ArrowUp') go(i - 1);
    else if (e.key === 'Home') go(0);
    else if (e.key === 'End') go(options.length - 1);
    else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); activate(o); }
    else if (e.key === 'Escape') { e.preventDefault(); document.querySelector('#timeline-host canvas')?.focus(); }
    e.stopPropagation();
  });

  for (const type of ['items', 'layout', 'view']) on(type, scheduleRebuild);
  on('selection', onSelection);
  rebuild();
}
