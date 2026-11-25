// Shared application state and the single view model (R-23, R-25).
// The timeline, the minimap and the status bar all read and write `state.view` through setView();
// there is no other copy of "visible start / visible end", so they cannot drift apart.
//
// Events (use on(type, fn); each handler receives a payload):
//   'items'      data (items/tags) changed            {external?: bool}
//   'layout'     filter, calendar visibility, group-by, layout mode or today changed: anything that requires repacking
//   'view'       visible window changed               {source: string}   (source names the writer)
//   'selection'  selection changed
//   'panel'      side panel open/tab/width/day changed
//   'theme'      theme changed
//   'undo'       undo/redo availability changed
//   'sync'       sync status changed
//   'notice'     status-bar notice changed
//   'highlight'  items briefly highlighted (external change)
//   'hover'      day under the cursor changed
//   'ready'      initial load finished
import { api } from './api.js';
import { EMPTY_FILTER, isFilterActive, layoutItems } from './filter.js';
import { fromDayNumber, toDayNumber, todayISO } from './dates.js';

export const MIN_SPAN_DAYS = 7;
export const MAX_SPAN_DAYS = 5 * 366;

const handlers = new Map();
export function on(type, fn) {
  if (!handlers.has(type)) handlers.set(type, new Set());
  handlers.get(type).add(fn);
  return () => handlers.get(type).delete(fn);
}
export function emit(type, payload = {}) {
  for (const fn of handlers.get(type) || []) {
    try { fn(payload); } catch (e) { console.error(`handler for '${type}' failed`, e); }
  }
}

export const state = {
  ready: false,
  dbPath: '',
  agentFile: '',
  hasHost: false,
  todayISO: todayISO(),
  today: toDayNumber(todayISO()),
  items: [],
  itemsById: new Map(),
  tags: {},                 // id -> {name, slot, usage}
  tagList: [],              // [{id, name, slot, usage, norm_key}] sorted by name
  calendars: {},            // calendar_source id -> {name, location, kind, visible, connected, is_holiday}
  settings: {},
  theme: 'meridian',
  groupByTag: true,
  layoutMode: 'compact',    // 'compact' | 'strict' (R-17)
  view: { start: 0, end: 42 },   // floating day numbers
  selection: new Set(),
  primaryId: null,
  anchorId: null,
  filter: { ...EMPTY_FILTER },
  panel: { open: true, tab: 'item', width: 360, dayISO: null },
  undo: { undo: null, redo: null },
  sync: { running: false, stage: '', error: null, last_result: null },
  notice: null,
  highlights: new Map(),    // item id -> expiry timestamp (ms)
  hoverDay: null,
  tagClipboard: null,       // {sourceId, title, count}: tags copied with Copy tags, pasted onto the selection
  hygiene: [],
  needsAttention: 0,
  dataVersion: 0,
  layoutVersion: 0,
};

// ------------------------------------------------------------------ derived

let visibleCache = { key: '', items: [] };
/** Items that take part in layout: filter and calendar visibility applied (R-31, R-131). */
export function visibleItems() {
  const key = `${state.dataVersion}|${state.layoutVersion}`;
  if (visibleCache.key !== key) {
    visibleCache = { key, items: layoutItems(state.items, state.filter, state.tags, state.calendars) };
  }
  return visibleCache.items;
}

/** Calendar sources that are connected, and how many of them are shown (R-131). */
export function calendarSummary() {
  const list = Object.values(state.calendars).filter((c) => c.connected);
  return { connected: list.length, visible: list.filter((c) => c.visible).length };
}

export function filterActive() { return isFilterActive(state.filter); }
export function selectedItems() { return [...state.selection].map((id) => state.itemsById.get(id)).filter(Boolean); }
export function tagName(id) { return state.tags[id]?.name ?? ''; }

// --------------------------------------------------------------------- data

export function setData(items, tags, calendars = state.calendars) {
  state.items = items;
  state.calendars = calendars;
  state.itemsById = new Map(items.map((i) => [i.id, i]));
  state.tags = tags;
  state.tagList = Object.entries(tags)
    .map(([id, t]) => ({ id, ...t }))
    .sort((a, b) => (a.tree_order ?? 0) - (b.tree_order ?? 0) || a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
  for (const id of [...state.selection]) if (!state.itemsById.has(id)) state.selection.delete(id);
  if (state.primaryId && !state.itemsById.has(state.primaryId)) state.primaryId = [...state.selection][0] ?? null;
  state.dataVersion++;
  emit('items');
  emit('selection');
}

// --------------------------------------------------------------- view model

export function clampView(start, end) {
  let span = end - start;
  const mid = (start + end) / 2;
  if (!(span >= MIN_SPAN_DAYS)) span = MIN_SPAN_DAYS;
  if (span > MAX_SPAN_DAYS) span = MAX_SPAN_DAYS;
  start = mid - span / 2;
  end = mid + span / 2;
  return { start, end };
}

/** The only way to change the visible window. `source` names the writer (e.g. 'timeline', 'minimap'). */
export function setView(start, end, source = 'api') {
  const v = clampView(start, end);
  if (v.start === state.view.start && v.end === state.view.end) return;
  state.view = v;
  emit('view', { source });
  persistSoon(['view_start', 'view_end']);
}

export function panBy(days, source = 'api') { setView(state.view.start + days, state.view.end + days, source); }

/** Zoom by `factor` (>1 zooms in) keeping `anchorDay` at the same relative screen position. */
export function zoomAround(factor, anchorDay, source = 'api') {
  const { start, end } = state.view;
  const span = end - start;
  const rel = (anchorDay - start) / span;
  const ns = Math.min(MAX_SPAN_DAYS, Math.max(MIN_SPAN_DAYS, span / factor));
  setView(anchorDay - rel * ns, anchorDay - rel * ns + ns, source);
}

export function defaultView() {
  const win = Number(state.settings.window_days) || 42;
  const start = state.today - win / 4;
  return { start, end: start + win };
}

// ---------------------------------------------------------------- selection

export function setSelection(ids, primary) {
  const next = new Set(ids);
  const same = next.size === state.selection.size && [...next].every((i) => state.selection.has(i));
  const prim = primary ?? (next.has(state.primaryId) ? state.primaryId : [...next][0] ?? null);
  if (same && prim === state.primaryId) return;
  state.selection = next;
  state.primaryId = prim;
  if (next.size === 1) state.anchorId = prim;
  emit('selection');
  if (next.size && !state.panel.open) setPanel({ open: true });
  if (next.size && state.panel.tab === 'day') setPanel({ tab: 'item' });
}

export function toggleSelection(id) {
  const next = new Set(state.selection);
  if (next.has(id)) next.delete(id); else next.add(id);
  state.anchorId = id;
  setSelection(next, next.has(id) ? id : undefined);
}

/** Shift+click: range in time order between the anchor and `id` over the visible items. */
export function selectRange(id) {
  const list = [...visibleItems()].sort((a, b) => a.start_date.localeCompare(b.start_date) || a.id.localeCompare(b.id));
  const a = list.findIndex((i) => i.id === (state.anchorId ?? state.primaryId));
  const b = list.findIndex((i) => i.id === id);
  if (a < 0 || b < 0) { setSelection([id], id); return; }
  const [lo, hi] = a < b ? [a, b] : [b, a];
  const keepAnchor = state.anchorId;
  setSelection(list.slice(lo, hi + 1).map((i) => i.id), id);
  state.anchorId = keepAnchor ?? id;
}

export function selectAllVisible() {
  const ids = visibleItems().map((i) => i.id);
  setSelection(ids, ids[0]);
}

// ------------------------------------------------------------ filter / layout

export function setFilter(patch) {
  state.filter = { ...state.filter, ...patch };
  state.layoutVersion++;
  emit('layout');
  persistSoon(['filter']);
}
export function clearFilter() { setFilter({ ...EMPTY_FILTER, tags: [], kind: [], origin: [], text: '', untagged: false }); }

/** Calendar visibility changed on the server (R-131): repack like a filter change. */
export function setCalendars(calendars) {
  state.calendars = calendars;
  state.layoutVersion++;
  emit('layout');
}

export function setLayoutMode(mode) {
  const m = mode === 'strict' ? 'strict' : 'compact';
  if (m === state.layoutMode) return;
  state.layoutMode = m;
  state.layoutVersion++;
  emit('layout', { modeSwitch: true });
  persistSoon(['layout_mode']);
}

export function setGroupByTag(v) {
  state.groupByTag = !!v;
  state.layoutVersion++;
  emit('layout');
  persistSoon(['group_by_tag']);
}

export const THEMES = [
  ['meridian', '2001 Meridian'], ['catalog', 'Industrial Catalog'],
  ['claude', 'Claude'], ['dell-1996', 'Dell 1996'], ['dji', 'DJI'], ['ferrari', 'Ferrari'], ['spacex', 'SpaceX'],
  ['spotify', 'Spotify'], ['ibm', 'IBM'],
];
const THEME_IDS = new Set(THEMES.map(([id]) => id));

export function setTheme(name) {
  if (!THEME_IDS.has(name)) return;
  state.theme = name;
  document.documentElement.dataset.theme = name;
  emit('theme');
  persistSoon(['theme']);
}

export function setPanel(patch) {
  state.panel = { ...state.panel, ...patch };
  emit('panel');
  persistSoon(['side_panel_open', 'side_panel_width', 'side_panel_tab']);
}

export function setHoverDay(day) {
  if (day === state.hoverDay) return;
  state.hoverDay = day;
  emit('hover');
}

/** Local midnight rollover: the today shading and "today" update (R-03). */
export function refreshToday() {
  const iso = todayISO();
  if (iso === state.todayISO) return false;
  state.todayISO = iso;
  state.today = toDayNumber(iso);
  state.layoutVersion++;
  emit('layout');
  emit('view', { source: 'today' });
  return true;
}

// --------------------------------------------------------------- notices etc.

let noticeTimer = null;
/**
 * Status-bar notice (R-84: replaces confirmation dialogs). `undo: true` shows an Undo button; notices
 * with an undo option last 8 seconds, others 5, errors stay until replaced or dismissed.
 */
export function notify(text, { undo = false, kind = 'info', ttl } = {}) {
  clearTimeout(noticeTimer);
  const life = ttl ?? (undo ? 8000 : kind === 'error' ? 0 : 5000);
  state.notice = { text, undo, kind, id: Date.now() + Math.random() };
  emit('notice');
  if (life) noticeTimer = setTimeout(dismissNotice, life);
}
export function dismissNotice() { clearTimeout(noticeTimer); state.notice = null; emit('notice'); }

export function highlight(ids, ms = 2500) {
  const until = Date.now() + ms;
  for (const id of ids) state.highlights.set(id, until);
  emit('highlight');
  setTimeout(() => {
    const now = Date.now();
    for (const [id, t] of state.highlights) if (t <= now) state.highlights.delete(id);
    emit('highlight');
  }, ms + 50);
}

// -------------------------------------------------------------- persistence

const pending = new Set();
let persistTimer = null;
function persistSoon(keys) {
  if (!state.ready) return;
  keys.forEach((k) => pending.add(k));
  clearTimeout(persistTimer);
  persistTimer = setTimeout(flushPersist, 500);
}

export async function flushPersist() {
  clearTimeout(persistTimer);
  if (!pending.size) return;
  const values = {};
  for (const k of pending) {
    values[k] = ({
      theme: state.theme, group_by_tag: state.groupByTag, layout_mode: state.layoutMode,
      view_start: state.view.start, view_end: state.view.end, filter: state.filter,
      side_panel_open: state.panel.open, side_panel_width: state.panel.width, side_panel_tab: state.panel.tab,
    })[k];
  }
  pending.clear();
  try { await api.putSettings(values); } catch (e) { console.error('could not save view state', e); }
}

/** Apply persisted settings from /api/bootstrap to the state. Called once at startup. */
export function applySettings(s) {
  state.settings = s;
  state.theme = THEME_IDS.has(s.theme) ? s.theme : 'meridian';
  document.documentElement.dataset.theme = state.theme;
  state.groupByTag = s.group_by_tag !== false;
  state.layoutMode = s.layout_mode === 'strict' ? 'strict' : 'compact';
  state.filter = { ...EMPTY_FILTER, ...(s.filter || {}) };
  state.panel = { open: s.side_panel_open !== false, width: s.side_panel_width || 360,
    tab: ['item', 'day', 'tags', 'history', 'settings'].includes(s.side_panel_tab) ? s.side_panel_tab : 'item', dayISO: null };
  const dv = defaultView();
  const vs = Number(s.view_start), ve = Number(s.view_end);
  state.view = Number.isFinite(vs) && Number.isFinite(ve) && ve > vs ? clampView(vs, ve) : clampView(dv.start, dv.end);
}

export { fromDayNumber, toDayNumber };
