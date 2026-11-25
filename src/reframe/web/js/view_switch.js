// File browser attached to the Gantt ruler.
import { api } from './api.js';
import { registerFiles } from './commands.js';
import { debounce, h } from './dom.js';
import { notify, on, state } from './store.js';
import { RULER_H } from './timeline_draw.js';
import { rulerRows, xToDay } from './zoom.js';
import { fetchIcon, filesApi } from './file_timeline/api.js';
import { readFileTheme } from './file_timeline/draw.js';
import { isFileDrag } from './file_timeline/drag.js';
import { createFilterBar } from './file_timeline/filter_bar.js';
import { compileFilter } from './file_timeline/groups.js';
import { createPanel } from './file_timeline/panel.js';
import { createSettingsSection } from './file_timeline/settings.js';
import { createFileStore } from './file_timeline/store.js';
import { createThumbLoader } from './file_timeline/thumbs.js';

const HOVER_DELAY = 800;
const SCAN_RELOAD_INTERVAL = 2000;
let files = null;

export function onFilesEvent(data) { files?.onEvent(data); }

export async function filesSettingsSection() {
  return files.createSettingsSection();
}

export function initFilesOverlay(tl) {
  const { host, canvas, m } = tl;
  const store = createFileStore(() => state.today);
  const S = store.state;
  if (state.settings.files_view?.filter) store.applyViewState({ filter: state.settings.files_view.filter });
  let theme = readFileTheme();
  let panel;
  const loader = createThumbLoader({ onChange: () => panel.refreshThumbs() });
  const icons = createThumbLoader({ onChange: () => panel.refreshThumbs(), load: fetchIcon, maxBitmaps: 256 });
  const panelHost = h('div', { class: 'rf-files-overlay__panel' });
  const filterHost = h('div', { class: 'rf-files-overlay__filter' });
  const statusHost = h('div', { class: 'rf-files-overlay__status' });
  const closeButton = h('button', { type: 'button', class: 'btn btn--small rf-files-overlay__close', text: 'Close',
    'aria-label': 'Close file browser', onclick: () => close(true) });
  const overlay = h('section', { class: 'rf-files-overlay', hidden: true, 'aria-label': 'Files in timeline period' },
    closeButton, panelHost, h('div', { class: 'rf-files-overlay__footer' }, filterHost, statusHost));
  host.append(overlay);

  let loading = false, reloadAgain = false, dirty = true, lastLoad = 0, started = false;
  let activeRow = null, activeKey = null, pendingKey = null, timer = null, fileDragging = false;
  const settingsSections = new Set();

  function report(prefix, error) { notify(`${prefix}: ${error.message}`, { kind: 'error' }); }
  async function load() {
    if (loading) { reloadAgain = true; return; }
    loading = true;
    try {
      const payload = await filesApi.entries('created');
      lastLoad = Date.now();
      if (payload.status && !S.status) store.setStatus(payload.status);
      store.setData(payload);
      dirty = false;
    } catch (e) {
      report('Could not load files', e);
    } finally {
      loading = false;
      if (reloadAgain) { reloadAgain = false; load(); }
    }
  }
  const loadSoon = debounce(load, 300);
  async function refreshStatus() {
    try {
      const status = await filesApi.status();
      store.setStatus(status);
      return status;
    } catch (e) {
      report('Could not get file scan status', e);
      return null;
    }
  }
  const refreshStatusSoon = debounce(refreshStatus, 250);
  async function rescan() {
    try { store.setStatus(await filesApi.rescan()); }
    catch (e) { report('Refresh failed', e); }
  }
  async function cancelScan() {
    try { store.setStatus(await filesApi.cancel()); }
    catch (e) { report('Could not cancel scan', e); }
  }
  async function openFile(id) {
    try { await filesApi.open(id); }
    catch (e) { report('Could not open the file', e); }
  }
  async function start() {
    if (started) { if (dirty || !S.loaded) load(); return; }
    started = true;
    load();
    const status = await refreshStatus();
    if (status?.state === 'never') rescan();
  }

  panel = createPanel(panelHost, {
    store, loader, icons, getTheme: () => theme, compile: compileFilter, statusHost,
    onOpen: openFile, onRescan: rescan, onCancel: cancelScan,
  });
  const filterBar = createFilterBar(filterHost, { store, count: () => panel.count() });
  store.on('panel', () => { panel.render(); filterBar.render(); });
  store.on('data', () => { panel.dataChanged(); filterBar.render(); });
  store.on('status', () => {
    panel.renderStatus();
    for (const section of settingsSections) {
      if (section.el.isConnected) section.refresh(); else settingsSections.delete(section);
    }
  });
  const saveFilter = debounce(() => {
    const view = { ...(state.settings.files_view || {}), mode: 'created', filter: S.filter };
    api.putSettings({ files_view: view }).then(() => { state.settings.files_view = view; })
      .catch((e) => report('Could not save file filter', e));
  }, 800);
  store.on('filter', saveFilter);
  on('theme', () => { theme = readFileTheme(); panel.refreshThumbs(); });
  panel.render();
  panel.renderStatus();

  function cancelWait() {
    clearTimeout(timer);
    timer = null;
    pendingKey = null;
  }
  function close(focus = false) {
    cancelWait();
    if (overlay.hidden) return;
    overlay.hidden = true;
    activeRow = activeKey = null;
    panel.release();
    if (focus) host.focus({ preventScroll: true });
  }
  function tickAt(x, row) {
    tl.ensureLayout();
    const day = xToDay(x, m.view, m.plotLeft, m.ppd);
    const rows = rulerRows(m.view.start, m.view.end, m.ppd);
    return rows[row].find((tick) => tick.start <= day && day < tick.end);
  }
  function place(x) {
    const width = overlay.offsetWidth;
    overlay.style.left = `${Math.max(0, Math.min(x - width / 2, host.clientWidth - width))}px`;
  }
  function show(row, tick, x, focus = false) {
    const key = `${row}:${tick.unit}:${tick.start}`;
    activeRow = row;
    overlay.hidden = false;
    place(x);
    tl.tip.hide();
    if (key !== activeKey) {
      activeKey = key;
      store.select(null);
      store.setPanel({ mode: 'day', bucket: { ...tick, includeUndated: tick.start > state.today }, keepScroll: false });
    }
    start();
    if (focus) panel.focusBrowser();
  }
  function rulerMove(x, y) {
    if (fileDragging) return;
    if (x < 0 || x >= canvas.clientWidth || y < 0 || y >= RULER_H) { close(); return; }
    const row = y < RULER_H / 2 ? 'major' : 'minor';
    if (activeRow === 'major' && row === 'minor') { cancelWait(); return; }
    if (activeRow === 'minor' && row === 'major') close();
    const tick = tickAt(x, row);
    if (!tick) { close(); return; }
    const key = `${row}:${tick.unit}:${tick.start}`;
    if (activeRow) { show(row, tick, x); return; }
    if (pendingKey === key) return;
    cancelWait();
    pendingKey = key;
    timer = setTimeout(() => { timer = null; pendingKey = null; show(row, tick, x); }, HOVER_DELAY);
  }
  function point(e) {
    const rect = canvas.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }
  canvas.addEventListener('pointermove', (e) => {
    if (e.buttons) { close(); return; }
    const p = point(e);
    rulerMove(p.x, p.y);
  });
  canvas.addEventListener('pointerdown', () => close());
  canvas.addEventListener('wheel', () => close(), { passive: true });
  canvas.addEventListener('pointerleave', (e) => {
    cancelWait();
    if (!overlay.contains(e.relatedTarget)) close();
  });
  overlay.addEventListener('pointerleave', (e) => {
    if (fileDragging) return;
    if (e.relatedTarget === canvas) {
      const p = point(e);
      rulerMove(p.x, p.y);
    } else close();
  });
  overlay.addEventListener('dragstart', (e) => {
    if (isFileDrag(e.dataTransfer)) { fileDragging = true; cancelWait(); }
  });
  overlay.addEventListener('dragend', () => { fileDragging = false; });
  document.addEventListener('dragover', (e) => {
    if (fileDragging && !overlay.hidden && !overlay.contains(e.target)) close();
  }, true);
  overlay.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(true); }
  });
  window.addEventListener('blur', () => close());
  on('view', () => close());

  files = {
    command(name) {
      if (name === 'refresh') rescan();
      if (name === 'open') {
        const x = canvas.clientWidth / 2;
        const tick = tickAt(x, 'minor');
        if (tick) show('minor', tick, x, true);
      }
    },
    onEvent(data) {
      if (!data || typeof data !== 'object') return;
      if (data.type === 'scan' && data.state === 'progress' && S.status) {
        store.setStatus({ ...S.status, state: 'scanning', progress: { files: data.files, dirs: data.dirs, current: data.current } });
      } else refreshStatusSoon();
      if (data.type === 'options') {
        loader.clear();
        for (const section of settingsSections) section.reload();
      }
      const changed = (data.type === 'scan' && data.state !== 'started') || data.type === 'dates' || data.type === 'options';
      if (!changed || data.version === S.version) return;
      dirty = true;
      if (!started) return;
      if (data.type !== 'scan' || data.state !== 'progress' || Date.now() - lastLoad > SCAN_RELOAD_INTERVAL) loadSoon();
    },
    createSettingsSection() {
      const section = createSettingsSection({
        chooseFolder: async () => {
          try { return (await api.chooseFolder()).path || null; }
          catch (e) { report('Could not choose folder', e); return null; }
        },
        getStatus: () => S.status,
        onChanged: () => { dirty = true; refreshStatus(); if (started) loadSoon(); },
      });
      settingsSections.add(section);
      refreshStatus();
      return section.el;
    },
  };
  registerFiles(files);
}
