// Application entry point: load state, start the modules, connect live refresh.
import { api, connectEvents } from './api.js';
import { applySyncStatus, onExternalData, refreshCalendars, refreshItems, refreshMeta, refreshUndo } from './actions.js';
import { registerUi, timelineHook } from './commands.js';
import { initEasterEggs } from './easter_eggs.js';
import { initFilesOverlay, onFilesEvent } from './view_switch.js';
import { initA11y } from './a11y.js';
import { initContextMenu } from './context_menu.js';
import { initFilterBar, selectAllChips, selectNoChips } from './filter_bar.js';
import { initMinimap } from './minimap.js';
import { initShortcuts } from './shortcuts.js';
import { initSidePanel } from './side_panel/index.js';
import { initStatusBar } from './status_bar.js';
import { initTheme } from './theme.js';
import { initTimeline } from './timeline.js';
import { initToolbar } from './toolbar.js';
import { applySettings, emit, flushPersist, notify, refreshToday, setFilter, state } from './store.js';

// Startup errors before the page is ready end up here; tell the user inline (spec 8.5).
window.addEventListener('unhandledrejection', (e) => console.error('unhandled', e.reason));

// Dialogs are never used (R-84, spec 6.8); fail loudly if anything tries.
for (const name of ['alert', 'confirm', 'prompt']) {
  window[name] = () => { console.error(`window.${name} is not allowed (R-84)`); return null; };
}
// Page zoom is replaced by timeline zoom.
window.addEventListener('wheel', (e) => { if (e.ctrlKey) e.preventDefault(); }, { passive: false });
window.addEventListener('keydown', (e) => {
  if (e.ctrlKey && ['+', '-', '=', '0'].includes(e.key)) e.preventDefault();
}, true);

// A drop that misses a target must not navigate the window to the dropped file.
for (const type of ['dragover', 'drop']) window.addEventListener(type, (e) => e.preventDefault());

/** A saved filter may refer to tags that no longer exist. */
function pruneSavedFilter() {
  const known = state.filter.tags.filter((id) => state.tags[id]);
  if (known.length !== state.filter.tags.length) setFilter({ tags: known });
}

function scheduleMidnight() {
  const now = new Date();
  const next = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 0, 0, 1);
  setTimeout(() => { refreshToday(); scheduleMidnight(); }, next - now);
}

async function start() {
  const boot = await api.bootstrap();
  state.dbPath = boot.db_path;
  state.agentFile = boot.agent_instructions;
  state.hasHost = boot.has_host;
  state.hygiene = boot.hygiene;
  state.needsAttention = boot.needs_attention;
  state.undo = boot.undo_state;
  state.todayISO = boot.today;
  state.version = boot.version;
  applySettings(boot.settings);
  await refreshItems();
  state.ready = true;
  refreshCalendars().catch(() => {});

  initTheme();
  initToolbar(document.getElementById('toolbar'));
  initFilterBar(document.getElementById('filterbar'));
  const timeline = initTimeline(document.getElementById('timeline-host'));
  initFilesOverlay(timeline);
  initMinimap(document.getElementById('minimap-host'));
  initSidePanel(document.getElementById('sidepanel'));
  initStatusBar(document.getElementById('statusbar'));
  initA11y(document.getElementById('a11y'));
  initContextMenu(document.getElementById('overlay-root'));
  initShortcuts();
  registerUi({ selectAllChips, selectNoChips });
  initEasterEggs((egg) => timelineHook('egg')?.(egg));
  emit('ready');

  connectEvents({
    hello: () => { refreshUndo(); },
    data: onExternalData,
    sync: applySyncStatus,
    backup: (e) => { if (e.error) notify(e.error, { kind: 'warn', ttl: 0 }); },
    files: onFilesEvent,
    vault: (e) => { state.vault = { ...(state.vault || {}), ...e }; emit('vault'); },
    notice: (e) => { if (e.text) notify(e.text, { kind: e.kind || 'info', ttl: e.ttl ?? 6000 }); },
  });
  if (boot.backup_error) notify(boot.backup_error, { kind: 'warn', ttl: 0 });
  pruneSavedFilter();
  scheduleMidnight();
  window.addEventListener('beforeunload', () => { flushPersist(); });
  window.addEventListener('pagehide', () => { flushPersist(); });
  // launch sync runs in the host (spec 8.1); the page only shows its progress
  api.syncStatus().then(applySyncStatus).catch(() => {});
  document.getElementById('timeline-host').querySelector('canvas')?.focus();
  refreshMeta().catch(() => {});
}

start().catch((e) => {
  console.error(e);
  const bar = document.getElementById('statusbar');
  if (bar) bar.textContent = `Could not start: ${e.message}`;
  notify(`Could not start: ${e.message}`, { kind: 'error' });
});
