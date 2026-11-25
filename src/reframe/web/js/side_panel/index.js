// Side panel shell: tab bar (Item | Day | Tags | History | Settings), drag-to-resize left edge, collapse.
import { h } from '../dom.js';
import { icon } from '../icons.js';
import { shortcutLabel, withShortcut } from '../shortcut_table.js';
import { on, setPanel, state } from '../store.js';
import { attachTooltip } from '../tooltip.js';
import { createDayTab } from './day.js';
import { createHistoryTab } from './history.js';
import { createItemTab } from './item.js';
import { createMultiTab } from './multi.js';
import { createSettingsTab } from './settings.js';
import { createTagsTab } from './tags.js';

const TABS = [['item', 'Item'], ['day', 'Day'], ['tags', 'Tags'], ['history', 'History'], ['settings', 'Settings']];
const MIN_WIDTH = 300;
const MIN_TIMELINE_WIDTH = 360;   // the panel may take everything except this much of the window
const maxWidth = () => Math.max(MIN_WIDTH, window.innerWidth - MIN_TIMELINE_WIDTH);

export function initSidePanel(el) {
  const tabs = { item: createItemTab(), multi: createMultiTab(), day: createDayTab(), tags: createTagsTab(),
    history: createHistoryTab(), settings: createSettingsTab() };
  const buttons = new Map();

  const tabBar = h('div', { class: 'tabs', role: 'tablist', 'aria-label': 'Side panel' }, TABS.map(([id, label]) => {
    const b = h('button', { type: 'button', class: 'tab-btn', role: 'tab', id: `tab-${id}`, text: label, 'aria-controls': 'panel-body',
      onclick: () => setPanel({ tab: id }) });
    b.addEventListener('keydown', (e) => {
      const i = TABS.findIndex(([t]) => t === id);
      const next = e.key === 'ArrowRight' ? i + 1 : e.key === 'ArrowLeft' ? i - 1 : null;
      if (next === null) return;
      e.preventDefault();
      const [nid] = TABS[(next + TABS.length) % TABS.length];
      setPanel({ tab: nid });
      buttons.get(nid).focus();
    });
    buttons.set(id, b);
    return b;
  }));
  const close = h('button', { type: 'button', class: 'panel-close', 'aria-label': 'Close side panel', onclick: () => setPanel({ open: false }) }, icon('close', 14));
  attachTooltip(close, withShortcut('Close side panel', 'togglePanel'));
  const body = h('div', { class: 'panel-body', id: 'panel-body', role: 'tabpanel' }, Object.values(tabs).map((t) => t.el));
  const grip = h('div', { class: 'panel-resizer', role: 'separator', 'aria-orientation': 'vertical', tabIndex: 0,
    'aria-label': `Resize side panel (arrow keys). Toggle with ${shortcutLabel('togglePanel')}` });
  const head = h('div', { class: 'panel-head' }, tabBar, close);
  el.replaceChildren(grip, head, body);

  function setWidth(w) {
    setPanel({ width: Math.round(Math.max(MIN_WIDTH, Math.min(maxWidth(), w))) });
  }
  grip.addEventListener('pointerdown', (e) => {
    grip.setPointerCapture(e.pointerId);
    grip.classList.add('is-dragging');
    const move = (ev) => setWidth(window.innerWidth - ev.clientX);
    const up = () => {
      grip.classList.remove('is-dragging');
      grip.removeEventListener('pointermove', move);
      grip.removeEventListener('pointerup', up);
      grip.removeEventListener('pointercancel', up);
    };
    grip.addEventListener('pointermove', move);
    grip.addEventListener('pointerup', up);
    grip.addEventListener('pointercancel', up);
    e.preventDefault();
  });
  grip.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowLeft') { e.preventDefault(); setWidth(state.panel.width + 24); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); setWidth(state.panel.width - 24); }
  });

  function activeTab() {
    if (state.panel.tab === 'item') return state.selection.size > 1 ? 'multi' : 'item';
    return state.panel.tab;
  }

  let lastKey = '';

  function render() {
    const open = state.panel.open;
    el.classList.toggle('is-collapsed', !open);
    el.setAttribute('aria-hidden', String(!open));
    el.inert = !open;
    el.style.setProperty('--panel-width', `${state.panel.width}px`);
    for (const [id, b] of buttons) {
      b.setAttribute('aria-selected', String(state.panel.tab === id));
      b.tabIndex = state.panel.tab === id ? 0 : -1;
    }
    body.setAttribute('aria-labelledby', `tab-${state.panel.tab}`);
    const show = activeTab();
    for (const [id, t] of Object.entries(tabs)) t.el.hidden = id !== show;
    const key = `${open}|${show}`;
    if (open && key !== lastKey) tabs[show].activate();
    lastKey = key;
  }

  on('panel', render);
  on('selection', () => { if (state.panel.tab === 'item') render(); });
  render();
}
