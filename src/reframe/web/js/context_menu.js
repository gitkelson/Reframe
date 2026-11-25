// The app's own context menu (R-84, spec 10.2): DOM in #overlay-root, keyboard operable, never a browser menu.
import * as actions from './actions.js';
import { commands } from './commands.js';
import { fromDayNumber } from './dates.js';
import { h, isTextField } from './dom.js';
import { shortcutLabel } from './shortcut_table.js';
import { selectedItems, setPanel, state } from './store.js';

let current = null;   // {root, onDown, onKey, returnFocus}

export function closeContextMenu({ restore = true } = {}) {
  if (!current) return;
  document.removeEventListener('pointerdown', current.onDown, true);
  document.removeEventListener('keydown', current.onKey, true);
  window.removeEventListener('blur', current.onBlur);
  current.root.remove();
  if (restore) current.returnFocus?.focus?.();
  current = null;
}

const itemsOf = (root) => [...root.querySelectorAll('[role^="menuitem"]:not([aria-disabled="true"])')];

function move(root, dir) {
  const items = itemsOf(root);
  if (!items.length) return;
  const i = items.indexOf(document.activeElement);
  items[(i + dir + items.length) % items.length].focus();
}

/**
 * entry: {label, hint?, onSelect, disabled?, checked?, danger?, keepOpen?, submenu?: entries | () => entries}
 * keepOpen toggles a check item without closing (checklists); a submenu replaces the menu with its entries and a
 * "Back" row (keyboard: Enter or Right opens it, Left or Backspace goes back).
 */
function renderEntry(entry, ctl) {
  if (entry.separator) return h('div', { class: 'menu__sep', role: 'separator' });
  const checkable = entry.checked !== undefined;
  const el = h('div', {
    class: `menu__item${entry.danger ? ' is-danger' : ''}${entry.disabled ? ' is-disabled' : ''}${entry.submenu ? ' has-submenu' : ''}`,
    role: checkable ? 'menuitemcheckbox' : 'menuitem', tabIndex: -1,
    'aria-disabled': entry.disabled ? 'true' : null, 'aria-checked': checkable ? String(!!entry.checked) : null,
    'aria-haspopup': entry.submenu ? 'menu' : null,
  },
  h('span', { class: 'menu__check', 'aria-hidden': 'true', text: entry.checked ? '✓' : '' }),
  h('span', { class: 'menu__label', text: entry.label }),
  entry.hint ? h('span', { class: 'menu__hint', text: entry.hint }) : null,
  entry.submenu ? h('span', { class: 'menu__arrow', 'aria-hidden': 'true', text: '›' }) : null);
  const choose = () => {
    if (entry.disabled) return;
    if (entry.back) { ctl.back(); return; }
    if (entry.submenu) { ctl.open(typeof entry.submenu === 'function' ? entry.submenu() : entry.submenu, entry.label); return; }
    if (entry.keepOpen) {
      entry.onSelect?.();
      entry.checked = !entry.checked;
      el.setAttribute('aria-checked', String(!!entry.checked));
      el.querySelector('.menu__check').textContent = entry.checked ? '✓' : '';
      return;
    }
    closeContextMenu({ restore: false });
    entry.onSelect?.();
  };
  el.addEventListener('click', choose);
  el.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ' || (e.key === 'ArrowRight' && entry.submenu)) {
      e.preventDefault(); e.stopPropagation(); choose();
    }
  });
  el.addEventListener('mousemove', () => { if (!entry.disabled && document.activeElement !== el) el.focus(); });
  return el;
}

export function showContextMenu(x, y, entries, { returnFocus = document.activeElement } = {}) {
  closeContextMenu({ restore: false });
  const root = h('div', { class: 'menu', role: 'menu', 'aria-label': 'Context menu' });
  const stack = [];
  const ctl = {
    open(list, title) {
      stack.push(current.entries);
      current.entries = list;
      paint([{ label: `‹ ${title}`, back: true }, { separator: true }, ...list]);
    },
    back() {
      if (!stack.length) return false;
      current.entries = stack.pop();
      paint(current.entries);
      return true;
    },
  };
  const paint = (list) => {
    root.replaceChildren(...list.map((en) => renderEntry(en, ctl)));
    itemsOf(root)[0]?.focus();
  };
  document.getElementById('overlay-root').append(root);
  current = { root, entries, returnFocus };
  paint(entries);
  const w = root.offsetWidth, hgt = root.offsetHeight;
  root.style.left = `${Math.max(4, Math.min(x, window.innerWidth - w - 4))}px`;
  root.style.top = `${Math.max(4, Math.min(y, window.innerHeight - hgt - 4))}px`;

  const onDown = (e) => { if (!root.contains(e.target)) closeContextMenu({ restore: false }); };
  const onBlur = () => closeContextMenu({ restore: false });
  const onKey = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); if (!ctl.back()) closeContextMenu(); }
    else if ((e.key === 'ArrowLeft' || e.key === 'Backspace') && stack.length) { e.preventDefault(); e.stopPropagation(); ctl.back(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); e.stopPropagation(); move(root, 1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); e.stopPropagation(); move(root, -1); }
    else if (e.key === 'Home') { e.preventDefault(); e.stopPropagation(); itemsOf(root)[0]?.focus(); }
    else if (e.key === 'End') { e.preventDefault(); e.stopPropagation(); itemsOf(root).at(-1)?.focus(); }
    else if (e.key === 'Tab') { e.preventDefault(); closeContextMenu(); }
  };
  document.addEventListener('pointerdown', onDown, true);
  document.addEventListener('keydown', onKey, true);
  window.addEventListener('blur', onBlur);
  Object.assign(current, { onDown, onKey, onBlur });
}

// ------------------------------------------------------------ menu contents

function focusPanel(selector) {
  setTimeout(() => {
    const visible = [...document.querySelectorAll(`#sidepanel ${selector}`)].find((el) => el.offsetParent);
    visible?.focus();
  }, 60);
}

function itemEntries() {
  const sel = selectedItems();
  const one = sel.length === 1 ? sel[0] : null;
  const imported = sel.filter((i) => i.locked);
  const entries = [];
  if (one) {
    entries.push({ label: 'Rename', hint: shortcutLabel('rename').split(' / ')[0], disabled: one.locked,
      onSelect: () => commands.rename() });
  }
  entries.push({ label: 'Open in side panel', onSelect: () => setPanel({ open: true, tab: 'item' }) });
  entries.push({ label: 'Add tag…', onSelect: () => { setPanel({ open: true, tab: 'item' }); focusPanel('.tag-editor__input'); } });
  if (one) {
    entries.push({ label: 'Duplicate', hint: shortcutLabel('duplicate'), onSelect: () => commands.duplicate() });
    entries.push({ label: 'Copy tags', hint: shortcutLabel('copyTags'), disabled: !one.tags.length, onSelect: () => commands.copyTags() });
  }
  const clip = state.tagClipboard;
  if (clip && sel.some((i) => i.id !== clip.sourceId)) {
    entries.push({ label: `Paste tags from '${clip.title}'`, hint: shortcutLabel('pasteTags'), onSelect: () => commands.pasteTags() });
  }
  if (one && one.locked) {
    entries.push({ separator: true });
    entries.push({ label: 'Hide this item', onSelect: () => actions.hideItem(one.id, false) });
    if (one.outlook_series_id) entries.push({ label: 'Hide whole series', onSelect: () => actions.hideItem(one.id, true) });
  }
  entries.push({ separator: true });
  entries.push({ label: sel.length > 1 ? `Delete ${sel.length} items` : 'Delete', hint: shortcutLabel('deleteSelected'),
    danger: true, disabled: imported.length === sel.length, onSelect: () => commands.deleteSelected() });
  return entries;
}

async function createHere(kind) {
  const day = Math.floor(state.hoverDay ?? state.today);
  const iso = fromDayNumber(day);
  const id = await actions.createItem({ title: kind === 'log' ? 'New log entry' : 'New item', start: iso, end: iso, kind });
  if (id) focusPanel('.field-title');
}

function emptyEntries() {
  return [
    { label: 'New item here', hint: shortcutLabel('newItem'), onSelect: () => createHere('plan') },
    { label: 'New log entry here', hint: shortcutLabel('newLog'), onSelect: () => createHere('log') },
    { separator: true },
    { label: 'Go to today', hint: shortcutLabel('today'), onSelect: () => commands.today() },
  ];
}

function minimalEntries() {
  return [
    { label: 'Back to timeline', onSelect: () => document.querySelector('#timeline-host canvas, #timeline-host [tabindex]')?.focus() },
    { label: 'Go to today', hint: shortcutLabel('today'), onSelect: () => commands.today() },
    { label: 'Keyboard shortcuts', hint: shortcutLabel('cheatsheet'), onSelect: () => commands.cheatsheet() },
  ];
}

/** On a bar when a selected item covers the day under the cursor (the timeline selects on right-press). */
function overSelectedItem() {
  const day = state.hoverDay;
  if (day === null || day === undefined) return selectedItems().length > 0;
  const iso = fromDayNumber(Math.floor(day));
  return selectedItems().some((i) => i.start_date <= iso && iso <= i.end_date);
}

export function entriesFor(target) {
  const inTimeline = !target.closest?.('.rf-files-overlay') && target.closest?.('#timeline-host, #minimap-host');
  if (!inTimeline) return minimalEntries();
  return overSelectedItem() ? itemEntries() : emptyEntries();
}

export function initContextMenu() {
  document.addEventListener('contextmenu', (e) => {
    if (isTextField(e.target)) return;   // native cut/copy/paste menu inside text fields
    e.preventDefault();
    let { clientX: x, clientY: y } = e;
    if (!x && !y && document.activeElement) {
      const r = document.activeElement.getBoundingClientRect();
      x = r.left + 8; y = r.bottom;
    }
    showContextMenu(x, y, entriesFor(e.target));
  });
}
