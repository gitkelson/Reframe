// Main toolbar (spec 6.2, R-96). Every button has a tooltip that includes its shortcut.
import { commands } from './commands.js';
import { showContextMenu } from './context_menu.js';
import { h } from './dom.js';
import { icon } from './icons.js';
import { shortcutLabel, withShortcut } from './shortcut_table.js';
import { THEMES, calendarSummary, on, setTheme, state } from './store.js';
import { attachTooltip } from './tooltip.js';
import { formatShort, toDayNumber } from './dates.js';
import * as actions from './actions.js';

const refs = {};

function button({ id, action, iconName, label, text, toggle = false, tip }) {
  const btn = h('button', {
    type: 'button', class: `tb-btn${text ? ' tb-btn--text' : ''}`, id: `tb-${id}`, 'aria-label': label,
    dataset: { action },
    onclick: () => { if (btn.getAttribute('aria-disabled') !== 'true') commands[action]?.(); },
  }, iconName ? icon(iconName, 18) : null, text ? h('span', { class: 'tb-btn__text', text }) : null);
  if (toggle) btn.setAttribute('aria-pressed', 'false');
  attachTooltip(btn, tip || (() => withShortcut(label, action)));
  refs[id] = btn;
  return btn;
}

/** A two-state segmented control: [{id, label, action, tipLabel}] */
function segmented(id, label, options) {
  const seg = h('div', { class: 'seg tb-seg', role: 'group', 'aria-label': label, id: `tb-${id}` }, options.map((o) => {
    const b = h('button', { type: 'button', class: 'seg__btn', text: o.label, dataset: { value: o.value },
      'aria-pressed': 'false', onclick: () => commands[o.action]?.() });
    attachTooltip(b, () => withShortcut(o.tipLabel, o.shortcut || o.action));
    return b;
  }));
  refs[id] = seg;
  return seg;
}

function setSeg(seg, value) {
  for (const b of seg.querySelectorAll('.seg__btn')) b.setAttribute('aria-pressed', String(b.dataset.value === value));
}

const group = (...kids) => h('div', { class: 'tb-group', role: 'group' }, kids);
const sep = () => h('span', { class: 'tb-sep', role: 'separator' });

function setDisabled(btn, disabled) {
  btn.setAttribute('aria-disabled', disabled ? 'true' : 'false');
  btn.classList.toggle('is-disabled', disabled);
}

function undoTip(kind) {
  const entry = state.undo[kind];
  const verb = kind === 'undo' ? 'Undo' : 'Redo';
  const key = shortcutLabel(kind);
  return entry ? `${verb}: ${entry.label} (${key})` : `Nothing to ${verb.toLowerCase()} (${key})`;
}

function syncTip() {
  const s = state.sync;
  const head = withShortcut('Sync all connected calendars now', 'sync');
  if (s.running) return `${head}\nRunning: ${s.stage || 'starting'}`;
  const lines = [head];
  if (s.error) lines.push(`Problem: ${s.error}`);
  if (s.last_result) lines.push(`Last result: ${s.last_result.message}`);
  else if (state.settings.last_sync_at) lines.push(`Last synced ${String(state.settings.last_sync_at).slice(0, 16).replace('T', ' ')}`);
  else lines.push('Not synced yet');
  return lines.join('\n');
}

export function viewMenuEntries() {
  const e = (label, action, extra = {}) => ({ label, hint: shortcutLabel(action), onSelect: () => commands[action]?.(), ...extra });
  const cal = calendarSummary();
  return [
    e('Browse files', 'openFiles'),
    { separator: true },
    e('Compact', 'layoutCompact', { checked: state.layoutMode === 'compact' }),
    e('Strict Gantt', 'layoutStrict', { checked: state.layoutMode === 'strict' }),
    e('Group by tag', 'toggleGroupByTag', { checked: state.groupByTag }),
    { separator: true },
    { label: 'Theme', submenu: () => THEMES.map(([id, name]) => ({ label: name, checked: state.theme === id, onSelect: () => setTheme(id) })) },
    { label: cal.connected ? `Calendars (${cal.visible} of ${cal.connected} shown)` : 'Calendars', submenu: () => {
      const list = Object.entries(state.calendars).filter(([, c]) => c.connected);
      return [
        { label: 'Show all', onSelect: () => actions.setCalendarVisible('*', true) },
        { label: 'Hide all', onSelect: () => actions.setCalendarVisible('*', false) },
        { separator: true },
        ...(list.length ? list.map(([id, c]) => ({ label: c.name, checked: !!c.visible, keepOpen: true,
          onSelect: () => actions.setCalendarVisible(id, !c.visible) }))
          : [{ label: 'No calendars connected', disabled: true }]),
      ];
    } },
    { separator: true },
    e('Side panel', 'togglePanel', { checked: state.panel.open }),
    e('Day panel', 'openDay'), e('History', 'openHistory'),
    { label: 'Tags', onSelect: () => commands.openTags() },
    { label: 'Settings', onSelect: () => commands.openSettings() },
    { separator: true },
    e('Zoom in', 'zoomIn'), e('Zoom out', 'zoomOut'), e('Zoom to selection', 'zoomToSelection'),
    { label: 'Reset view', onSelect: () => commands.resetView() },
    e('Go to today', 'today'), e('Go to date…', 'goToDate'),
    { separator: true },
    e('Keyboard shortcuts', 'cheatsheet'),
    e('Quit Reframe', 'quit'),
  ];
}

function openViewMenu() {
  const r = refs.view.getBoundingClientRect();
  showContextMenu(r.left, r.bottom + 2, viewMenuEntries(), { returnFocus: refs.view });
}

function update() {
  setSeg(refs.layoutMode, state.layoutMode);
  refs.groupByTag.setAttribute('aria-pressed', String(state.groupByTag));
  refs.panel.setAttribute('aria-pressed', String(state.panel.open));
  setDisabled(refs.undo, !state.undo.undo);
  setDisabled(refs.redo, !state.undo.redo);
  refs.sync.classList.toggle('is-spinning', !!state.sync.running);
  refs.sync.setAttribute('aria-busy', String(!!state.sync.running));
  refs.sync.classList.toggle('has-error', !!state.sync.error);
}

export function initToolbar(el) {
  const view = h('button', { type: 'button', class: 'tb-btn tb-btn--text', id: 'tb-view', 'aria-haspopup': 'menu',
    'aria-label': 'View menu', onclick: openViewMenu }, h('span', { class: 'tb-btn__text', text: 'View' }), icon('menu', 14));
  attachTooltip(view, 'View menu: views, layout, theme, calendars, panels, Quit');
  refs.view = view;

  const layoutGroup = group(
    segmented('layoutMode', 'Layout', [
      { value: 'compact', label: 'Compact', action: 'layoutCompact', shortcut: 'toggleLayoutMode', tipLabel: 'Compact: items share rows when they do not overlap' },
      { value: 'strict', label: 'Strict', action: 'layoutStrict', shortcut: 'toggleLayoutMode', tipLabel: 'Strict Gantt: one item per row, later starts lower' },
    ]),
    button({ id: 'groupByTag', action: 'toggleGroupByTag', iconName: 'group', label: 'Group by tag', text: 'Group by tag', toggle: true }));
  const editGroup = group(
    button({ id: 'undo', action: 'undo', iconName: 'undo', label: 'Undo', tip: () => undoTip('undo') }),
    button({ id: 'redo', action: 'redo', iconName: 'redo', label: 'Redo', tip: () => undoTip('redo') }));
  const syncGroup = group(button({ id: 'sync', action: 'sync', iconName: 'sync', label: 'Sync now', text: 'Sync', tip: syncTip }));
  const seps = [sep(), sep(), sep()];

  el.replaceChildren(
    h('div', { class: 'tb-brand', text: 'Reframe' }),
    group(
      button({ id: 'today', action: 'today', iconName: 'today', label: 'Today', text: 'Today' }),
      button({ id: 'zoomOut', action: 'zoomOut', iconName: 'zoomOut', label: 'Zoom out' }),
      button({ id: 'zoomIn', action: 'zoomIn', iconName: 'zoomIn', label: 'Zoom in' }),
      button({ id: 'fit', action: 'zoomToSelection', iconName: 'fit', label: 'Fit selection' }),
    ),
    seps[0], layoutGroup, seps[1], editGroup, seps[2], syncGroup,
    view,
    h('div', { class: 'tb-spacer' }),
    h('div', { class: 'tb-today', 'aria-hidden': 'true', text: formatShort(toDayNumber(state.todayISO)) }),
    group(button({ id: 'panel', action: 'togglePanel', iconName: 'panel', label: 'Side panel', toggle: true })),
  );
  for (const ev of ['layout', 'undo', 'sync', 'panel', 'theme']) on(ev, update);
  on('layout', () => { el.querySelector('.tb-today').textContent = formatShort(toDayNumber(state.todayISO)); });
  update();
}
