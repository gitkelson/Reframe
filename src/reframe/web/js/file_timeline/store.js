// File timeline store (R-18): its own view window, zoom, vertical scroll, selection, filter and date mode,
// independent of the Gantt store. Events: 'view', 'data' (entries or filter changed: relayout), 'selection',
// 'panel', 'status', 'mode', 'filter', 'scroll'.
import {
  EMPTY_FILE_FILTER, applyFilter, clampSpan, dataRange, isFilterActive, normalizeFilter, rowsToEntries,
} from './groups.js';

export function createFileStore(todayFn) {
  const handlers = new Map();
  const on = (type, fn) => {
    if (!handlers.has(type)) handlers.set(type, new Set());
    handlers.get(type).add(fn);
    return () => handlers.get(type).delete(fn);
  };
  const emit = (type, payload = {}) => {
    for (const fn of handlers.get(type) || []) {
      try { fn(payload); } catch (e) { console.error(`file timeline handler '${type}' failed`, e); }
    }
  };

  const today = todayFn();
  const state = {
    mode: 'created',
    view: { start: today - 10, end: today + 32 },
    scrollY: 0,
    selectedId: null,
    filter: { ...EMPTY_FILE_FILTER, types: [] },
    panel: { mode: 'status', bucket: null, fromBucket: null },
    entries: [],
    byId: new Map(),
    filtered: [],
    undated: [],
    roots: [],
    excluded: [],
    includeHidden: false,
    sources: {},
    version: -1,
    loaded: false,
    loading: false,
    status: null,
    layoutVersion: 0,
  };

  let rangeCache = null;
  function range() {
    if (!rangeCache || rangeCache.today !== todayFn() || rangeCache.v !== state.layoutVersion) {
      rangeCache = { today: todayFn(), v: state.layoutVersion, r: dataRange(state.entries, todayFn()) };
    }
    return rangeCache.r;
  }

  function setView(start, end, source = 'api') {
    const v = clampSpan(start, end, range());
    if (v.start === state.view.start && v.end === state.view.end) return;
    state.view = v;
    emit('view', { source });
  }

  function zoomAround(factor, anchorDay, source = 'api') {
    const { start, end } = state.view;
    const span = end - start;
    const rel = (anchorDay - start) / span;
    const r = range();
    const ns = Math.min(Math.max(7, r.hi - r.lo), Math.max(7, span / factor));
    setView(anchorDay - rel * ns, anchorDay - rel * ns + ns, source);
  }

  function refilter() {
    state.filtered = applyFilter(state.entries, state.filter);
    state.layoutVersion += 1;
    emit('data');
  }

  function setData(payload) {
    state.entries = rowsToEntries(payload.columns, payload.rows);
    state.undated = rowsToEntries(payload.columns, payload.undated || []);
    state.byId = new Map();
    for (const e of state.entries) state.byId.set(e.id, e);
    for (const e of state.undated) state.byId.set(e.id, e);
    state.roots = payload.roots || [];
    state.excluded = payload.excluded_ext || [];
    state.includeHidden = !!payload.include_hidden;
    state.sources = payload.sources || {};
    state.version = payload.version;
    state.loaded = true;
    if (state.selectedId && !state.byId.has(state.selectedId)) {
      state.selectedId = null;
      emit('selection');
    }
    rangeCache = null;
    refilter();
    setView(state.view.start, state.view.end, 'data');      // re-clamp to the new range
  }

  function setFilter(patch) {
    state.filter = normalizeFilter({ ...state.filter, ...patch });
    emit('filter');
    refilter();
  }

  function setMode(mode) {
    if (mode !== 'created' && mode !== 'modified') return;
    if (state.mode === mode) return;
    state.mode = mode;
    emit('mode');
  }

  function select(id) {
    const v = id && state.byId.has(id) ? id : null;
    if (v === state.selectedId) return;
    state.selectedId = v;
    emit('selection');
  }

  function setPanel(patch) {
    state.panel = { ...state.panel, ...patch };
    emit('panel');
  }

  function setScroll(y) {
    if (y === state.scrollY) return;
    state.scrollY = y;
    emit('scroll');
  }

  function setStatus(st) {
    state.status = st;
    emit('status');
  }

  const filterActive = () => isFilterActive(state.filter);

  function viewState() {
    return { mode: state.mode, view: { ...state.view }, scrollY: state.scrollY, filter: { ...state.filter } };
  }

  function applyViewState(obj) {
    if (!obj || typeof obj !== 'object') return;
    if (obj.mode === 'created' || obj.mode === 'modified') state.mode = obj.mode;
    if (obj.filter) state.filter = normalizeFilter(obj.filter);
    if (obj.view && Number.isFinite(obj.view.start) && Number.isFinite(obj.view.end) && obj.view.end > obj.view.start) {
      // before the first load the data range is unknown: keep the saved window and clamp it in setData()
      state.view = state.loaded ? clampSpan(obj.view.start, obj.view.end, range()) : { start: obj.view.start, end: obj.view.end };
    }
    if (Number.isFinite(obj.scrollY) && obj.scrollY >= 0) state.scrollY = obj.scrollY;
    emit('mode');
    emit('filter');
    if (state.loaded) refilter();
    emit('view', { source: 'restore' });
  }

  return {
    state, on, emit, range, setView, zoomAround, setData, setFilter, setMode, select, setPanel, setScroll, setStatus,
    filterActive, viewState, applyViewState,
  };
}
