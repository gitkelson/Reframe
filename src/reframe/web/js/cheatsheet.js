// Inline shortcut cheat sheet (not a dialog): a panel in #overlay-root, closes on Esc or click away.
import { h } from './dom.js';
import { SHORTCUTS } from './shortcut_table.js';

let open = null;   // {root, previous}

function build() {
  const groups = new Map();
  for (const s of SHORTCUTS.filter((x) => !x.hidden)) {
    if (!groups.has(s.group)) groups.set(s.group, []);
    groups.get(s.group).push(s);
  }
  const cols = [...groups].map(([name, list]) => h('section', { class: 'cheatsheet__group' },
    h('h3', { text: name }),
    h('dl', {}, list.flatMap((s) => [
      h('dt', {}, s.keys.map((k, i) => [i ? h('span', { class: 'cheatsheet__or', text: 'or' }) : null,
        h('kbd', { text: k })])),
      h('dd', { text: s.description }),
    ]))));
  return h('div', { class: 'cheatsheet', role: 'region', 'aria-label': 'Keyboard shortcuts', tabIndex: -1 },
    h('header', {},
      h('h2', { text: 'Keyboard shortcuts' }),
      h('p', { text: 'Single-key shortcuts work while the timeline has focus. Press Esc or ? to close.' })),
    h('div', { class: 'cheatsheet__cols' }, cols));
}

export function closeCheatsheet() {
  if (!open) return;
  document.removeEventListener('keydown', open.onKey, true);
  open.root.remove();
  open.previous?.focus?.();
  open = null;
}

export function openCheatsheet() {
  if (open) return;
  const previous = document.activeElement;
  const panel = build();
  const root = h('div', { class: 'overlay-scrim cheatsheet-scrim', onmousedown: (e) => { if (e.target === root) closeCheatsheet(); } }, panel);
  const onKey = (e) => {
    if (e.key === 'Escape' || e.key === '?') { e.preventDefault(); e.stopPropagation(); closeCheatsheet(); }
  };
  document.addEventListener('keydown', onKey, true);
  document.getElementById('overlay-root').append(root);
  open = { root, previous, onKey };
  panel.focus();
}

export function toggleCheatsheet() { if (open) closeCheatsheet(); else openCheatsheet(); }
