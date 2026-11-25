// The single table of keyboard shortcuts (spec 6.7). The key handler, toolbar tooltips and the cheat sheet
// all read this table, so they cannot disagree.
//   scope 'timeline': fires only when the timeline host has focus and never while typing (R-95)
//   scope 'global'  : modifier shortcut; fires anywhere (preventDefault overrides browser accelerators)
//   scope 'doc'     : documented here only; handled by the component that owns it (search field)
//   scope 'filterbar': fires only while focus is inside the filter bar
//   hidden: true    : captured (so the browser accelerator never fires) but not listed in the cheat sheet
//   textField: false -> not while typing in a text field (there Ctrl+Z / Ctrl+Y are text undo / redo)
export const SHORTCUTS = [
  { action: 'today', keys: ['T'], description: 'Jump to today', scope: 'timeline', group: 'Navigate' },
  { action: 'openFiles', keys: ['Ctrl+2'], description: 'Browse files at the center of the timeline', scope: 'global', group: 'Navigate' },
  { action: 'goToDate', keys: ['Ctrl+J'], description: 'Go to date', scope: 'global', group: 'Navigate' },
  { action: 'panLeft', keys: ['Left'], description: 'Pan left 1 day', scope: 'timeline', group: 'Navigate' },
  { action: 'panRight', keys: ['Right'], description: 'Pan right 1 day', scope: 'timeline', group: 'Navigate' },
  { action: 'panWeekLeft', keys: ['Ctrl+Left'], description: 'Pan left 1 week', scope: 'timeline', group: 'Navigate' },
  { action: 'panWeekRight', keys: ['Ctrl+Right'], description: 'Pan right 1 week', scope: 'timeline', group: 'Navigate' },
  { action: 'pageLeft', keys: ['PgUp'], description: 'Pan one window left', scope: 'timeline', group: 'Navigate' },
  { action: 'pageRight', keys: ['PgDn'], description: 'Pan one window right', scope: 'timeline', group: 'Navigate' },
  { action: 'goHome', keys: ['Home'], description: 'Jump to the first item', scope: 'timeline', group: 'Navigate' },
  { action: 'goEnd', keys: ['End'], description: 'Jump to the last item', scope: 'timeline', group: 'Navigate' },
  { action: 'zoomIn', keys: ['Ctrl+='], description: 'Zoom in', scope: 'global', group: 'Navigate' },
  { action: 'zoomOut', keys: ['Ctrl+-'], description: 'Zoom out', scope: 'global', group: 'Navigate' },
  { action: 'zoomToSelection', keys: ['Z'], description: 'Zoom to selection', scope: 'timeline', group: 'Navigate' },

  { action: 'rowAbove', keys: ['Up'], description: 'Select item in the row above', scope: 'timeline', group: 'Select' },
  { action: 'rowBelow', keys: ['Down'], description: 'Select item in the row below', scope: 'timeline', group: 'Select' },
  { action: 'prevItem', keys: ['Alt+Left'], description: 'Previous item (time order)', scope: 'timeline', group: 'Select' },
  { action: 'nextItem', keys: ['Alt+Right'], description: 'Next item (time order)', scope: 'timeline', group: 'Select' },
  { action: 'scrollRowsUp', keys: ['Alt+Up'], description: 'Scroll rows up', scope: 'timeline', group: 'Select' },
  { action: 'scrollRowsDown', keys: ['Alt+Down'], description: 'Scroll rows down', scope: 'timeline', group: 'Select' },
  { action: 'selectAll', keys: ['Ctrl+A'], description: 'Select all visible items', scope: 'timeline', group: 'Select' },

  { action: 'newItem', keys: ['Ctrl+N'], description: 'New item at selected day', scope: 'global', group: 'Edit' },
  { action: 'newLog', keys: ['Ctrl+L'], description: 'New log entry at selected day', scope: 'global', group: 'Edit' },
  { action: 'rename', keys: ['F2', 'Enter'], description: 'Rename selected item', scope: 'timeline', group: 'Edit' },
  { action: 'deleteSelected', keys: ['Delete'], description: 'Delete selected', scope: 'timeline', group: 'Edit' },
  { action: 'undo', keys: ['Ctrl+Z'], description: 'Undo (database)', scope: 'global', textField: false, group: 'Edit' },
  { action: 'redo', keys: ['Ctrl+Y', 'Ctrl+Shift+Z'], description: 'Redo (database)', scope: 'global', textField: false, group: 'Edit' },
  { action: 'moveEarlier', keys: ['Alt+Shift+Left'], description: 'Move item 1 day earlier', scope: 'timeline', group: 'Edit' },
  { action: 'moveLater', keys: ['Alt+Shift+Right'], description: 'Move item 1 day later', scope: 'timeline', group: 'Edit' },
  { action: 'endEarlier', keys: ['Shift+Left'], description: 'End date earlier', scope: 'timeline', group: 'Edit' },
  { action: 'endLater', keys: ['Shift+Right'], description: 'End date later', scope: 'timeline', group: 'Edit' },
  { action: 'startEarlier', keys: ['Ctrl+Shift+Left'], description: 'Start date earlier', scope: 'timeline', group: 'Edit' },
  { action: 'startLater', keys: ['Ctrl+Shift+Right'], description: 'Start date later', scope: 'timeline', group: 'Edit' },
  { action: 'duplicate', keys: ['Ctrl+Shift+D'], description: 'Duplicate the selected item (then name it)', scope: 'global', textField: false, group: 'Edit' },
  { action: 'copyTags', keys: ['Ctrl+Alt+C'], description: 'Copy the tags of the selected item', scope: 'global', textField: false, group: 'Edit' },
  { action: 'pasteTags', keys: ['Ctrl+Alt+V'], description: 'Paste those tags onto the selected items', scope: 'global', textField: false, group: 'Edit' },

  { action: 'search', keys: ['Ctrl+F'], description: 'Search', scope: 'global', group: 'View' },
  { action: 'searchNext', keys: ['Enter', 'Shift+Enter'], description: 'Next / previous match (in the search field)', scope: 'doc', group: 'View' },
  { action: 'clearFilter', keys: ['Ctrl+Shift+F'], description: 'Clear filter', scope: 'global', group: 'View' },
  { action: 'selectAllChips', keys: ['Ctrl+Shift+A'], description: 'Select all tag chips (filter bar)', scope: 'filterbar', group: 'View' },
  { action: 'selectNoChips', keys: ['Ctrl+Shift+N'], description: 'Select no tag chips (filter bar)', scope: 'filterbar', group: 'View' },
  { action: 'noop', keys: ['Ctrl+H'], description: 'Hide past (removed)', scope: 'global', hidden: true, group: 'View' },
  { action: 'toggleLayoutMode', keys: ['Ctrl+Shift+L'], description: 'Compact / Strict Gantt layout', scope: 'global', group: 'View' },
  { action: 'toggleGroupByTag', keys: ['Ctrl+G'], description: 'Toggle group by tag', scope: 'global', group: 'View' },
  { action: 'togglePanel', keys: ['Ctrl+B'], description: 'Toggle side panel', scope: 'global', group: 'View' },
  { action: 'openDay', keys: ['Ctrl+D'], description: 'Open day panel', scope: 'global', group: 'View' },
  { action: 'openHistory', keys: ['Ctrl+Shift+H'], description: 'Open history', scope: 'global', group: 'View' },
  { action: 'sync', keys: ['Ctrl+R'], description: 'Sync now (all connected calendars)', scope: 'global', group: 'View' },
  { action: 'refreshFiles', keys: ['F5'], description: 'Refresh files', scope: 'global', group: 'View' },
  { action: 'cheatsheet', keys: ['?'], description: 'Shortcut cheat sheet', scope: 'timeline', group: 'View' },
  { action: 'quit', keys: ['Ctrl+Q'], description: 'Quit Reframe', scope: 'global', group: 'View' },
];

const NAMED = {
  left: 'arrowleft', right: 'arrowright', up: 'arrowup', down: 'arrowdown', pgup: 'pageup', pgdn: 'pagedown',
};
// keys whose character already implies Shift on some layouts: Shift is not compared
const SHIFT_FREE = new Set(['?', '=', '-']);

export function parseCombo(text) {
  const parts = text.split('+');
  // "Ctrl++" style: the last part is empty when the key itself is '+'
  const keyPart = parts.pop() || '+';
  const mods = new Set(parts.filter(Boolean).map((p) => p.toLowerCase()));
  const k = keyPart.toLowerCase();
  return { ctrl: mods.has('ctrl'), shift: mods.has('shift'), alt: mods.has('alt'), key: NAMED[k] || k };
}

function eventKey(e) {
  const k = e.key.toLowerCase();
  return k === '+' ? '=' : k;
}

export function matchCombo(e, combo) {
  if (eventKey(e) !== combo.key) return false;
  if ((e.ctrlKey || e.metaKey) !== combo.ctrl || e.altKey !== combo.alt) return false;
  return SHIFT_FREE.has(combo.key) ? true : e.shiftKey === combo.shift;
}

const compiled = SHORTCUTS.map((s) => ({ ...s, combos: s.keys.map(parseCombo) }));

/** The table entry that matches a keydown event, or null. */
export function findShortcut(e) {
  return compiled.find((s) => s.scope !== 'doc' && s.combos.some((c) => matchCombo(e, c))) || null;
}

export function shortcutFor(action) { return SHORTCUTS.find((s) => s.action === action) || null; }

/** Display text such as "Ctrl+Y / Ctrl+Shift+Z" for an action ('' if none). */
export function shortcutLabel(action) {
  const s = shortcutFor(action);
  return s ? s.keys.join(' / ') : '';
}

/** "Today (T)" style text for tooltips. */
export function withShortcut(text, action) {
  const label = shortcutLabel(action);
  return label ? `${text} (${label})` : text;
}
