// Named commands shared by the toolbar, the keyboard shortcuts, menus and the cheat sheet (spec 6.7).
// Pure behaviour lives here; the two UI hooks below are filled in by the timeline and the panels.
import * as actions from './actions.js';
import { refuseLocked } from './timeline_input.js';
import { fromDayNumber, toDayNumber, parseTypedDate } from './dates.js';
import { api } from './api.js';
import {
  MAX_SPAN_DAYS, MIN_SPAN_DAYS, clearFilter, defaultView, notify, panBy, selectAllVisible, selectedItems,
  setGroupByTag, setLayoutMode, setPanel, setSelection, setView, state, visibleItems, zoomAround,
} from './store.js';

const hooks = {
  timeline: {},   // set by timeline.js: focus(), startRename(id), selectRow(dir), scrollRows(n), reveal(id)
  ui: {},         // set by the chrome: focusSearch(), openGoToDate(), toggleCheatsheet(), selectAllChips()...
  files: null,
};
export function registerFiles(api) { hooks.files = api; }

export function registerTimeline(api) { hooks.timeline = api; }
export function registerUi(api) { hooks.ui = { ...hooks.ui, ...api }; }
/** Call a hook registered by the timeline (e.g. 'chipDrag', 'egg'). */
export function timelineHook(name) { return hooks.timeline[name]; }

const centre = () => (state.view.start + state.view.end) / 2;

/** The day new items are created on: the open day, else the single selected item's start, else today. */
export function selectedDay() {
  if (state.panel.dayISO) return toDayNumber(state.panel.dayISO);
  const sel = selectedItems();
  if (sel.length === 1) return toDayNumber(sel[0].start_date);
  return state.today;
}

function inTimeOrder() {
  return [...visibleItems()].sort((a, b) => a.start_date.localeCompare(b.start_date)
    || a.end_date.localeCompare(b.end_date) || a.id.localeCompare(b.id));
}

function stepItem(dir) {
  const list = inTimeOrder();
  if (!list.length) return;
  const i = list.findIndex((it) => it.id === state.primaryId);
  const next = list[Math.min(list.length - 1, Math.max(0, i < 0 ? (dir > 0 ? 0 : list.length - 1) : i + dir))];
  setSelection([next.id], next.id);
  hooks.timeline.reveal?.(next.id);
}

async function newItem(kind) {
  const day = fromDayNumber(selectedDay());
  const id = await actions.createItem({ title: kind === 'log' ? 'New log entry' : 'New item', start: day, end: day, kind });
  if (id) hooks.timeline.startRename?.(id);
}

/** Resize the window to the selection (Z). */
function zoomToSelection() {
  const sel = selectedItems();
  if (!sel.length) { notify('Select an item first'); return; }
  const s = Math.min(...sel.map((i) => toDayNumber(i.start_date)));
  const e = Math.max(...sel.map((i) => toDayNumber(i.end_date))) + 1;
  const pad = Math.max(1, (e - s) * 0.1);
  setView(s - pad, e + pad, 'command');
}

function editDates(which, delta) {
  const sel = selectedItems();
  const editable = sel.filter((i) => !i.locked);
  if (!editable.length) { refuseLocked(sel[0]); return; }
  // one bulk request = one change set = one undo (R-101)
  const ids = sel.map((i) => i.id);
  if (which === 'both') actions.moveItems(ids, delta); else actions.shiftEdge(ids, which, delta);
}

const raw = {
  openFiles: () => hooks.files?.command('open'),
  layoutCompact: () => setLayoutMode('compact'),
  layoutStrict: () => setLayoutMode('strict'),
  toggleLayoutMode: () => setLayoutMode(state.layoutMode === 'strict' ? 'compact' : 'strict'),
  selectAllChips: () => hooks.ui.selectAllChips?.(),
  selectNoChips: () => hooks.ui.selectNoChips?.(),
  refreshFiles: () => hooks.files?.command('refresh'),
  quit: async () => { try { await api.quit(); } catch (e) { notify(e.message, { kind: 'warn' }); } },
  noop: () => {},
  duplicate: async () => {
    const src = state.itemsById.get(state.primaryId);
    if (!src) { notify('Select an item first'); return; }
    const id = await actions.duplicateItem(src.id, src.start_date);   // lands on the same days, named next
    if (id) hooks.timeline.startRename?.(id);
  },
  copyTags: () => {
    const src = state.itemsById.get(state.primaryId);
    if (!src || !src.tags.length) { notify(src ? 'That item has no tags to copy' : 'Select an item first'); return; }
    state.tagClipboard = { sourceId: src.id, title: src.title, count: src.tags.length };
    notify(`Copied ${src.tags.length} tag${src.tags.length > 1 ? 's' : ''} from '${src.title}'. Select other items and paste.`, { ttl: 5000 });
  },
  pasteTags: () => {
    const clip = state.tagClipboard;
    if (!clip || !state.itemsById.has(clip.sourceId)) { notify('Copy the tags of an item first'); return; }
    return actions.copyTags(clip.sourceId, [...state.selection]);
  },
  today: () => {
    const span = state.view.end - state.view.start;
    const start = state.today - span / 4;
    setView(start, start + span, 'command');
    hooks.timeline.focus?.();
  },
  resetView: () => { const v = defaultView(); setView(v.start, v.end, 'command'); },
  zoomIn: () => zoomAround(1.5, centre(), 'command'),
  zoomOut: () => zoomAround(1 / 1.5, centre(), 'command'),
  zoomToSelection,
  panLeft: () => panBy(-1, 'command'),
  panRight: () => panBy(1, 'command'),
  panWeekLeft: () => panBy(-7, 'command'),
  panWeekRight: () => panBy(7, 'command'),
  pageLeft: () => panBy(-(state.view.end - state.view.start), 'command'),
  pageRight: () => panBy(state.view.end - state.view.start, 'command'),
  goHome: () => { const items = visibleItems(); if (items.length) { const s = Math.min(...items.map((i) => toDayNumber(i.start_date))); const span = state.view.end - state.view.start; setView(s - 1, s - 1 + span, 'command'); } },
  goEnd: () => { const items = visibleItems(); if (items.length) { const e = Math.max(...items.map((i) => toDayNumber(i.end_date))) + 1; const span = state.view.end - state.view.start; setView(e + 1 - span, e + 1, 'command'); } },
  newItem: () => newItem('plan'),
  newLog: () => newItem('log'),
  rename: () => { if (state.primaryId) hooks.timeline.startRename?.(state.primaryId); },
  deleteSelected: () => actions.deleteSelection(),
  undo: () => actions.undoLast(),
  redo: () => actions.redoLast(),
  search: () => hooks.ui.focusSearch?.(),
  clearFilter: () => clearFilter(),
  selectAll: () => selectAllVisible(),
  toggleGroupByTag: () => setGroupByTag(!state.groupByTag),
  togglePanel: () => setPanel({ open: !state.panel.open }),
  openDay: () => setPanel({ open: true, tab: 'day', dayISO: fromDayNumber(selectedDay()) }),
  openHistory: () => setPanel({ open: true, tab: 'history' }),
  openSettings: () => setPanel({ open: true, tab: 'settings' }),
  openTags: () => setPanel({ open: true, tab: 'tags' }),
  sync: () => actions.syncNow(),
  goToDate: () => hooks.ui.openGoToDate?.(),
  cheatsheet: () => hooks.ui.toggleCheatsheet?.(),
  rowAbove: () => hooks.timeline.selectRow?.(-1),
  rowBelow: () => hooks.timeline.selectRow?.(1),
  scrollRowsUp: () => hooks.timeline.scrollRows?.(-3),
  scrollRowsDown: () => hooks.timeline.scrollRows?.(3),
  prevItem: () => stepItem(-1),
  nextItem: () => stepItem(1),
  moveEarlier: () => editDates('both', -1),
  moveLater: () => editDates('both', 1),
  endEarlier: () => editDates('end', -1),
  endLater: () => editDates('end', 1),
  startEarlier: () => editDates('start', -1),
  startLater: () => editDates('start', 1),
};

export const commands = raw;

/** Jump so that `dateText` (ISO, with or without dashes) is centred. Returns an error message or null (R-100). */
export function goToDate(dateText) {
  const iso = parseTypedDate(dateText);
  if (!iso) return 'Use a date like 2026-11-08 or 20261108';
  const span = state.view.end - state.view.start;
  const n = toDayNumber(iso);
  setView(n - span / 2, n + span / 2, 'command');
  setPanel({ open: true, tab: 'day', dayISO: iso });
  return null;
}

export { MAX_SPAN_DAYS, MIN_SPAN_DAYS };
