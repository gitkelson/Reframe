// File timeline view (spec 3.12): mounts into host elements owned by the app shell and keeps its own state (R-18).
// Interface used by the shell:
//   initFileTimeline({ toolbarSlot, filterHost, timelineHost, minimapHost, panelHost, statusSink, chooseFolder })
//   -> { activate, deactivate, onEvent, command, getViewState, setViewState, createSettingsSection }
import { createMeasurer, loadCanvasCss } from '../canvas_theme.js';
import { createCanvasTooltip } from '../canvas_tooltip.js';
import { formatLong, toDayNumber } from '../dates.js';
import { debounce, h } from '../dom.js';
import { notify, on as onApp, state as appState } from '../store.js';
import { attachTooltip } from '../tooltip.js';
import { filesApi } from './api.js';
import { BODY_TOP, cellKey, drawFileTimeline, readFileTheme, visibleColumns } from './draw.js';
import { createFilterBar } from './filter_bar.js';
import { bucketDaysFor, compileFilter, formatSize, groupIntoBuckets, typeGroupOf } from './groups.js';
import { attachInput } from './input.js';
import { initFileMinimap } from './minimap.js';
import { createPanel } from './panel.js';
import { createSettingsSection as buildSettings } from './settings.js';
import { createFileStore } from './store.js';
import { createThumbLoader } from './thumbs.js';

const DEFAULT_WINDOW_DAYS = 42;
const A11Y_MAX_OPTIONS = 600;
const RELOAD_WHILE_SCANNING_MS = 2000;

let cssLoaded = false;
function loadFileCss() {
  if (cssLoaded || document.querySelector('link[href$="/css/file_timeline.css"]')) { cssLoaded = true; return; }
  cssLoaded = true;
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = '/static/css/file_timeline.css';
  document.head.appendChild(link);
}

export function initFileTimeline({ toolbarSlot, filterHost, timelineHost, minimapHost, panelHost, statusSink, chooseFolder }) {
  loadCanvasCss();
  loadFileCss();
  const store = createFileStore(() => appState.today);
  const S = store.state;
  let active = false;
  let viewRestored = false;
  let dirty = true;

  // ------------------------------------------------------------------ DOM
  timelineHost.classList.add('ft-timeline');
  minimapHost.classList.add('ft-minimap');
  const canvas = h('canvas', { class: 'ft-canvas', 'aria-hidden': 'true' });
  const list = h('div', {
    class: 'ft-a11y', role: 'listbox', tabindex: '0',
    'aria-label': 'File timeline. Arrow keys move between files and day groups, Enter opens a file or browses a group, plus and minus zoom.',
  });
  const emptyMsg = h('div', { class: 'field__msg', role: 'alert' });
  const empty = h('div', { class: 'ft-empty', hidden: true });
  timelineHost.replaceChildren(canvas, list, empty);
  const ctx = canvas.getContext('2d');
  const tip = createCanvasTooltip(timelineHost);

  // ------------------------------------------------------------------ model
  const cellMeas = createMeasurer();
  const headMeas = createMeasurer();
  const measure = (t) => cellMeas.measure(t);
  const headMeasure = (t) => headMeas.measure(t);
  let theme = readFileTheme();
  const setFonts = () => { cellMeas.setFont(`11px ${theme.fontUi}`); headMeas.setFont(`600 10px ${theme.fontUi}`); };
  setFonts();
  const loader = createThumbLoader({ onChange: () => { invalidate(); panel.refreshThumbs(); } });
  const m = {
    W: 0, H: 0, dpr: 1, theme, view: S.view, ppd: 1, today: appState.today, scrollY: 0, mode: S.mode,
    groups: { size: 1, list: [] }, groupsKey: '', layoutCache: new Map(), layoutVersion: 0, columns: [], contentH: 0,
    selectedId: null, focusKey: null, focused: false, hoverKey: null, thumbs: loader,
    measure, headMeasure, rulerMeasure: measure, emptyText: '',
  };
  let framePending = false;

  function invalidate() {
    if (framePending || !active) return;
    framePending = true;
    requestAnimationFrame(frame);
  }

  function syncSize() {
    const d = window.devicePixelRatio || 1;
    const w = timelineHost.clientWidth, hh = timelineHost.clientHeight;
    if (w === m.W && hh === m.H && d === m.dpr && canvas.width) return;
    m.W = w; m.H = hh; m.dpr = d;
    canvas.width = Math.max(1, Math.round(w * d));
    canvas.height = Math.max(1, Math.round(hh * d));
    canvas.style.width = `${w}px`;
    canvas.style.height = `${hh}px`;
  }

  function clampScroll() {
    const max = Math.max(0, m.contentH - (m.H - BODY_TOP));
    if (S.scrollY > max) S.scrollY = max;
    if (S.scrollY < 0) S.scrollY = 0;
    m.scrollY = S.scrollY;
  }

  function frame() {
    framePending = false;
    if (!active) return;
    syncSize();
    if (!m.W || !m.H) return;
    m.view = S.view;
    m.ppd = m.W / (S.view.end - S.view.start);
    m.today = appState.today;
    m.mode = S.mode;
    m.selectedId = S.selectedId;
    m.theme = theme;
    const size = bucketDaysFor(m.ppd);
    const gk = `${S.layoutVersion}|${size}`;
    if (gk !== m.groupsKey) {
      m.groupsKey = gk;
      m.groups = groupIntoBuckets(S.filtered, size);
      m.layoutCache.clear();
      m.layoutVersion = S.layoutVersion;
    }
    m.columns = visibleColumns(m);
    clampScroll();
    m.emptyText = !S.loaded ? 'Loading files\u2026'
      : S.entries.length === 0 ? (S.status && S.status.state === 'scanning' ? 'Scanning\u2026' : 'No dated files.')
        : S.filtered.length === 0 ? 'No files match the filter.' : '';
    ctx.setTransform(m.dpr, 0, 0, m.dpr, 0, 0);
    drawFileTimeline(ctx, m);
    requestThumbs();
    a11ySoon();
    minimap.invalidate();
  }

  function requestThumbs() {
    const bodyH = m.H - BODY_TOP;
    const near = [], far = [];
    for (const col of m.columns) {
      for (const c of col.layout.cells) {
        if (c.type !== 'thumb' || c.entry.online) continue;
        const y = c.y - m.scrollY;
        if (y + c.h >= 0 && y <= bodyH) near.push(c.entry.id);
        else if (y + c.h >= -bodyH / 2 && y <= bodyH * 1.5) far.push(c.entry.id);
      }
    }
    loader.want(near.concat(far), 'main');
  }

  // ------------------------------------------------------------------ accessibility mirror (R-155)
  let navItems = [];
  const describe = (e) => {
    const src = S.sources[String(e.src)] || '';
    const kind = e.kind === 1 ? 'image' : typeGroupOf(e.ext, e.kind) === 'other' ? (e.ext ? `${e.ext.slice(1)} file` : 'file') : typeGroupOf(e.ext, e.kind);
    return `${e.name}, ${kind}, ${formatLong(e.day)}${src ? `, ${src}` : ''}, ${formatSize(e.size)}${e.online ? ', online-only' : ''}`;
  };
  const groupLabel = (b) => {
    const when = b.end - b.start === 1 ? formatLong(b.start) : `${formatLong(b.start)} to ${formatLong(b.end - 1)}`;
    return `${when}: ${b.items.length} file${b.items.length === 1 ? '' : 's'}, ${b.images} image${b.images === 1 ? '' : 's'}. Enter browses all.`;
  };

  function buildNav() {
    const items = [];
    for (const col of m.columns) {
      items.push({ key: `group:${col.bucket.k}`, type: 'group', col, label: groupLabel(col.bucket) });
      for (const c of col.layout.cells) {
        items.push({ key: cellKey(c, col.bucket), type: c.type, col, cell: c,
          label: c.type === 'badge' ? `${c.hidden} more files in this group. Enter browses all.` : describe(c.entry) });
      }
    }
    navItems = items;
  }

  const a11ySoon = debounce(() => {
    buildNav();
    const opts = [];
    let fi = navItems.findIndex((n) => n.key === m.focusKey);
    const from = Math.max(0, Math.min(fi < 0 ? 0 : fi - A11Y_MAX_OPTIONS / 2, navItems.length - A11Y_MAX_OPTIONS));
    navItems.slice(from, from + A11Y_MAX_OPTIONS).forEach((n, i) => {
      opts.push(h('div', { role: 'option', id: `ft-opt-${from + i}`, 'aria-selected': String(n.cell?.entry?.id === S.selectedId && !!n.cell?.entry), text: n.label }));
    });
    list.replaceChildren(...opts);
    if (fi < 0 && navItems.length && m.focused) { m.focusKey = navItems[0].key; fi = 0; invalidate(); }
    if (fi >= 0) list.setAttribute('aria-activedescendant', `ft-opt-${fi}`);
    else list.removeAttribute('aria-activedescendant');
  }, 120);

  function focusItem(item) {
    if (!item) return;
    m.focusKey = item.key;
    const top = item.cell ? item.cell.y : 0;
    const bodyH = m.H - BODY_TOP;
    if (item.cell && (top < S.scrollY || top + item.cell.h > S.scrollY + bodyH)) {
      S.scrollY = Math.max(0, top - bodyH / 3);
    }
    if (item.cell && item.cell.entry) selectEntry(item.cell.entry.id, item.key);
    invalidate();
    a11ySoon.flush();
  }

  function moveColumn(dir) {
    buildNav();
    const cur = navItems.find((n) => n.key === m.focusKey) || navItems[0];
    const list2 = m.groups.list;
    if (!list2.length) return;
    let idx = cur ? list2.indexOf(cur.col.bucket) : -1;
    const posInCol = cur ? cur.col.layout.cells.indexOf(cur.cell) : -1;
    idx = idx < 0 ? 0 : idx + dir;
    if (idx < 0 || idx >= list2.length) return;
    const b = list2[idx];
    const span = S.view.end - S.view.start;
    if (b.start < S.view.start || b.end > S.view.end) {
      const s = dir > 0 ? Math.max(S.view.start, b.end - span + span * 0.1) : Math.min(S.view.start, b.start - span * 0.1);
      store.setView(s, s + span, 'keyboard');
    }
    frame();
    buildNav();
    const col = m.columns.find((c) => c.bucket === b) || m.columns.find((c) => c.bucket.k === b.k);
    if (!col) return;
    const cells = col.layout.cells;
    const target = posInCol >= 0 && cells.length ? cells[Math.min(posInCol, cells.length - 1)] : null;
    focusItem(navItems.find((n) => n.col === col && (target ? n.cell === target : n.type === 'group')));
  }

  function moveWithin(dir) {
    buildNav();
    const i = navItems.findIndex((n) => n.key === m.focusKey);
    if (i < 0) { focusItem(navItems[0]); return; }
    const next = navItems[i + dir];
    if (next && next.col === navItems[i].col) focusItem(next);
  }

  function activateFocused() {
    buildNav();
    const it = navItems.find((n) => n.key === m.focusKey);
    if (!it) return;
    if (it.type === 'thumb' || it.type === 'file') openFile(it.cell.entry.id);
    else { openBucket(it.col.bucket, it.key); panel.focusBrowser(); }
  }

  list.addEventListener('keydown', (e) => {
    if (e.ctrlKey || e.altKey || e.metaKey) return;
    const keys = {
      ArrowRight: () => moveColumn(1), ArrowLeft: () => moveColumn(-1),
      ArrowDown: () => moveWithin(1), ArrowUp: () => moveWithin(-1),
      Enter: activateFocused,
      '+': () => command('zoomIn'), '=': () => command('zoomIn'), '-': () => command('zoomOut'),
      Escape: () => { selectEntry(null, m.focusKey); },
      Home: () => { buildNav(); focusItem(navItems[0]); },
      End: () => { buildNav(); focusItem(navItems[navItems.length - 1]); },
    };
    const fn = keys[e.key];
    if (!fn) return;
    e.preventDefault();
    e.stopPropagation();
    fn();
  });
  list.addEventListener('focus', () => { m.focused = true; invalidate(); a11ySoon(); });
  list.addEventListener('blur', () => { m.focused = false; invalidate(); });

  // ------------------------------------------------------------------ actions
  function selectEntry(id, key) {
    if (key) m.focusKey = key;
    store.select(id);
    if (id) store.setPanel({ mode: 'details', fromBucket: null });
    else if (S.panel.mode === 'details') store.setPanel({ mode: 'status' });
    invalidate();
  }

  function openBucket(bucket, key) {
    if (key) m.focusKey = key;
    store.setPanel({ mode: 'day', bucket: { start: bucket.start, end: bucket.end }, fromBucket: null });
    invalidate();
  }

  function openDay(day) {
    openBucket({ start: day, end: day + 1 });
  }

  async function openFile(id) {
    try {
      await filesApi.open(id);
    } catch (e) {
      notify(`Could not open the file: ${e.message}`, { kind: 'error' });
    }
  }

  function scrollTo(y) {
    S.scrollY = Math.max(0, y);
    clampScroll();
    invalidate();
  }

  const ctl = {
    canvas, host: timelineHost, store, m, tip, invalidate, scrollTo, selectEntry, openBucket, openDay, openFile,
    focus: () => list.focus({ preventScroll: true }),
  };
  attachInput(ctl);

  // ------------------------------------------------------------------ minimap, filter, panel
  const minimap = initFileMinimap(minimapHost, {
    store, getTheme: () => theme, getToday: () => appState.today,
    getScroll: () => ({ scrollY: S.scrollY, contentH: m.contentH, bodyH: m.H - BODY_TOP }),
  });
  const filterBar = createFilterBar(filterHost, { store });
  const panel = createPanel(panelHost, {
    store, loader, getTheme: () => theme,
    compile: compileFilter,
    onOpen: openFile,
    onRescan: () => command('refresh'),
    onCancel: async () => { try { setStatus(await filesApi.cancel()); } catch (e) { notify(e.message, { kind: 'error' }); } },
    onBrowserSelect: () => invalidate(),
  });

  // ------------------------------------------------------------------ toolbar
  const modeSeg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Date used for placement' });
  const undatedBtn = h('button', { type: 'button', class: 'btn btn--small', text: 'Undated',
    onclick: () => store.setPanel({ mode: 'undated' }) });
  const refreshBtn = h('button', { type: 'button', class: 'btn btn--small', text: 'Refresh', onclick: () => command('refresh') });
  attachTooltip(undatedBtn, 'Files whose date cannot be read');
  attachTooltip(refreshBtn, 'Rescan all folders (F5)');
  function renderToolbar() {
    const mk = (mode, label, tipText) => {
      const b = h('button', { type: 'button', class: 'seg__btn', text: label, 'aria-pressed': String(S.mode === mode),
        onclick: () => store.setMode(mode) });
      attachTooltip(b, tipText);
      return b;
    };
    modeSeg.replaceChildren(
      mk('created', 'Created', 'Place photos by date taken and MP4 videos by media created when present; otherwise use the file created date'),
      mk('modified', 'Modified', 'Place every file by its last-modified date'));
    undatedBtn.textContent = S.undated.length ? `Undated (${S.undated.length})` : 'Undated';
    undatedBtn.disabled = !S.undated.length;
  }
  toolbarSlot.replaceChildren(h('div', { class: 'ft-toolbar' }, modeSeg, undatedBtn, refreshBtn));
  renderToolbar();

  // ------------------------------------------------------------------ empty state (no roots)
  function renderEmpty() {
    const noRoots = S.status && S.status.state === 'no_roots';
    empty.hidden = !noRoots;
    if (!noRoots) return;
    const paste = h('input', { type: 'text', class: 'input', placeholder: 'or paste a folder path', 'aria-label': 'Folder path', spellcheck: false });
    const add = async (path) => {
      if (!path) return;
      emptyMsg.textContent = '';
      try {
        await filesApi.putOptions({ roots: [path] });
        await refreshStatus();
        load();
      } catch (e) {
        emptyMsg.dataset.kind = 'error';
        emptyMsg.textContent = e.data && e.data.problems ? e.data.problems.join(' ') : e.message;
      }
    };
    paste.addEventListener('keydown', (e) => { if (e.key === 'Enter') add(paste.value.trim()); });
    empty.replaceChildren(h('div', { class: 'ft-empty__card' },
      h('h2', { class: 'ft-empty__title', text: 'Show your files on a timeline' }),
      h('p', { text: 'Choose one or more folders. Their files are placed by date; nothing is moved, changed or stored in the Reframe database.' }),
      h('div', { class: 'ft-empty__row' },
        h('button', { type: 'button', class: 'btn btn--primary', text: 'Choose folder\u2026',
          onclick: async () => { try { add(await chooseFolder()); } catch (e) { emptyMsg.textContent = e.message; } } }),
        paste,
        h('button', { type: 'button', class: 'btn', text: 'Add', onclick: () => add(paste.value.trim()) })),
      emptyMsg));
  }

  // ------------------------------------------------------------------ data loading
  let loading = false, reloadAgain = false, lastLoad = 0;

  async function load() {
    if (loading) { reloadAgain = true; return; }
    loading = true;
    try {
      const p = await filesApi.entries(S.mode);
      lastLoad = Date.now();
      if (p.status) S.status = { ...(S.status || {}), ...p.status };
      store.setData(p);
      if (!viewRestored) {
        viewRestored = true;
        const t = appState.today;
        store.setView(t - DEFAULT_WINDOW_DAYS / 4, t + (DEFAULT_WINDOW_DAYS * 3) / 4, 'init');
      }
      dirty = false;
    } catch (e) {
      notify(`File timeline: ${e.message}`, { kind: 'error' });
    } finally {
      loading = false;
      if (reloadAgain) { reloadAgain = false; load(); }
    }
  }
  const loadSoon = debounce(load, 300);

  function setStatus(st) {
    store.setStatus(st);
  }

  async function refreshStatus() {
    try {
      const st = await filesApi.status();
      setStatus(st);
      return st;
    } catch (e) {
      return null;
    }
  }
  const refreshStatusSoon = debounce(refreshStatus, 250);

  const settingsSections = new Set();

  function sinkStatus() {
    const st = S.status;
    if (!active) return;
    const shown = S.filtered.length, total = S.entries.length;
    const bits = [store.filterActive() ? `${shown.toLocaleString()} of ${total.toLocaleString()} files` : `${total.toLocaleString()} files`];
    if (S.undated.length) bits.push(`${S.undated.length} undated`);
    if (st) {
      if (st.state === 'no_roots') bits.splice(0, bits.length, 'No folders chosen');
      else if (st.state === 'scanning') bits.push(`scanning\u2026 ${(st.progress?.files ?? 0).toLocaleString()} found`);
      if (st.partial) bits.push('partial scan');
      if (st.stale) bits.push('may be out of date');
      if (st.exif_pending) bits.push(`reading dates taken (${st.exif_pending})`);
    }
    statusSink?.(`Files: ${bits.join(' \u00b7 ')}`);
  }

  // ------------------------------------------------------------------ store wiring
  store.on('view', () => { invalidate(); minimap.invalidate(); });
  store.on('data', () => { renderToolbar(); panel.dataChanged(); invalidate(); sinkStatus(); });
  store.on('selection', () => { invalidate(); a11ySoon(); });
  store.on('panel', () => panel.render());
  store.on('mode', () => { renderToolbar(); if (active) load(); else dirty = true; });
  store.on('status', () => {
    panel.renderStatus();
    renderEmpty();
    sinkStatus();
    for (const s of settingsSections) { if (s.el.isConnected) s.refresh(); else settingsSections.delete(s); }
    invalidate();
  });
  onApp('theme', () => {
    theme = readFileTheme();
    setFonts();
    invalidate();
    minimap.invalidate();
    panel.refreshThumbs();
  });
  onApp('layout', () => { if (m.today !== appState.today) invalidate(); });
  new ResizeObserver(invalidate).observe(timelineHost);
  panel.render();
  panel.renderStatus();

  function onF5(e) {
    if (!active || e.key !== 'F5' || e.ctrlKey || e.altKey) return;
    e.preventDefault();
    command('refresh');
  }

  // ------------------------------------------------------------------ public interface
  function command(name, arg) {
    const v = S.view, span = v.end - v.start, mid = (v.start + v.end) / 2;
    switch (name) {
      case 'today': store.setView(appState.today + 0.5 - span / 2, appState.today + 0.5 + span / 2, 'command'); break;
      case 'zoomIn': store.zoomAround(1.5, mid, 'command'); break;
      case 'zoomOut': store.zoomAround(1 / 1.5, mid, 'command'); break;
      case 'fit': {
        const e = S.selectedId ? S.byId.get(S.selectedId) : null;
        if (e && Number.isFinite(e.day)) store.setView(e.day + 0.5 - 7, e.day + 0.5 + 7, 'command');
        else {
          const r = store.range();
          const days = S.filtered.length ? [S.filtered[0].day, S.filtered[S.filtered.length - 1].day + 1] : [r.lo, r.hi];
          store.setView(days[0] - 3, days[1] + 3, 'command');
        }
        break;
      }
      case 'refresh':
        filesApi.rescan().then(setStatus).catch((e) => notify(`Refresh failed: ${e.message}`, { kind: 'error' }));
        break;
      case 'focus': list.focus({ preventScroll: true }); break;
      case 'search': filterBar.focusSearch(); break;
      case 'undated': store.setPanel({ mode: 'undated' }); break;
      case 'goToDate': {
        let d;
        try { d = toDayNumber(String(arg)); } catch { notify('Use a date like 2026-11-08', { kind: 'warn' }); return; }
        store.setView(d + 0.5 - span / 2, d + 0.5 + span / 2, 'command');
        break;
      }
      default: break;
    }
  }

  return {
    activate() {
      if (active) return;
      active = true;
      document.addEventListener('keydown', onF5);
      renderToolbar();
      invalidate();
      minimap.invalidate();
      panel.refreshThumbs();
      refreshStatus().then((st) => {
        if (st && st.state === 'never') filesApi.rescan().then(setStatus).catch(() => {});
      });
      if (dirty || !S.loaded) load();
      sinkStatus();
    },
    deactivate() {
      if (!active) return;
      active = false;
      document.removeEventListener('keydown', onF5);
      tip.hide();
      loader.want([], 'main');
      loader.want([], 'browser');
    },
    onEvent(data) {
      if (!data || typeof data !== 'object') return;
      const t = data.type;
      if (t === 'scan' && data.state === 'progress' && S.status) {
        S.status = { ...S.status, state: 'scanning', progress: { files: data.files, dirs: data.dirs, current: data.current } };
        store.setStatus(S.status);
      } else {
        refreshStatusSoon();
      }
      if (t === 'options') { loader.clear(); for (const s of settingsSections) s.reload(); }
      const wantsData = (t === 'scan' && data.state !== 'started') || t === 'dates' || t === 'options';
      if (!wantsData) return;
      if (data.version === S.version) return;
      if (!active) { dirty = true; return; }
      if (t === 'scan' && data.state === 'progress') {
        if (Date.now() - lastLoad > RELOAD_WHILE_SCANNING_MS) loadSoon();
      } else {
        loadSoon();
      }
    },
    command,
    getViewState: () => store.viewState(),
    setViewState(obj) {
      store.applyViewState(obj);
      if (obj && obj.view) viewRestored = true;
      renderToolbar();
      filterBar.render();
      if (active) load(); else dirty = true;
    },
    createSettingsSection() {
      const s = buildSettings({ chooseFolder, getStatus: () => S.status, onChanged: () => { refreshStatus(); loadSoon(); } });
      settingsSections.add(s);
      refreshStatus();
      return s.el;
    },
  };
}
