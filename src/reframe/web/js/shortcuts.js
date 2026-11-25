// Keyboard handling (spec 6.7, R-95). One capture-phase listener; the key table lives in shortcut_table.js.
import { commands, registerUi } from './commands.js';
import { toggleCheatsheet } from './cheatsheet.js';
import { focusSearch } from './filter_bar.js';
import { openGoToDate } from './goto_date.js';
import { isTextField } from './dom.js';
import { findShortcut } from './shortcut_table.js';

function timelineHasFocus() {
  const host = document.getElementById('timeline-host');
  return host?.contains(document.activeElement) && !document.activeElement.closest('.rf-files-overlay');
}

// Browser accelerators with no Reframe meaning that must never open browser UI in the WebView2 window.
const BROWSER_CTRL = new Set(['p', 'u', 's', 'o', 'e']);
const BROWSER_CTRL_SHIFT = new Set(['i', 'j', 'c', 'r', 'o', 'p']);
const BROWSER_FKEYS = new Set(['f5', 'f7', 'f12', 'f3']);

function isBrowserAccelerator(e) {
  const k = e.key.toLowerCase();
  const ctrl = e.ctrlKey || e.metaKey;
  if (BROWSER_FKEYS.has(k)) return true;
  if (ctrl && e.shiftKey && BROWSER_CTRL_SHIFT.has(k)) return true;
  if (ctrl && !e.shiftKey && !e.altKey && BROWSER_CTRL.has(k)) return true;
  if (ctrl && k === 'f5') return true;
  // Alt+Left/Right/Home would navigate the page history
  return e.altKey && !ctrl && ['arrowleft', 'arrowright', 'home'].includes(k);
}

/** Returns true when the shortcut was consumed. */
function dispatch(s, typing) {
  if (s.scope === 'timeline') {
    if (typing || !timelineHasFocus()) return false;
  } else if (s.scope === 'filterbar') {
    if (!document.getElementById('filterbar')?.contains(document.activeElement)) return false;
  } else if (typing && s.textField === false) {
    return false;
  }
  commands[s.action]?.();
  return true;
}

function onKeyDown(e) {
  if (e.isComposing) return;
  const typing = isTextField(document.activeElement);
  const s = findShortcut(e);
  if (s && dispatch(s, typing)) {
    e.preventDefault();
    e.stopPropagation();
    return;
  }
  if (isBrowserAccelerator(e)) { e.preventDefault(); return; }
  // Ctrl+A outside text fields would select the whole page
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a' && !typing) e.preventDefault();
  // handled modifier shortcuts never fall through to the browser even when declined above
  if (s && s.scope === 'global' && (e.ctrlKey || e.metaKey) && s.textField !== false) e.preventDefault();
}

export function initShortcuts() {
  registerUi({ focusSearch, openGoToDate, toggleCheatsheet });
  document.addEventListener('keydown', onKeyDown, true);
}
