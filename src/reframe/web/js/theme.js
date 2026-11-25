// Theme application and tag-slot styling helpers (R-90, R-91, spec 7.2). No colours here: slots are CSS classes
// (.slot-0..7, plus .is-hatched for slots 8-15) that app.css maps to the --tag-N variables.
import { h } from './dom.js';
import { on, state } from './store.js';

function apply() { document.documentElement.dataset.theme = state.theme; }

export function initTheme() {
  apply();
  on('theme', apply);
}

export const SLOT_COUNT = 16;
const norm = (slot) => (((Number(slot) || 0) % SLOT_COUNT) + SLOT_COUNT) % SLOT_COUNT;

/** Class list for a slot 0-15: solid for 0-7, hatched for 8-15. */
export function slotClass(slot) {
  const s = norm(slot);
  return `tag-slot slot-${s % 8}${s >= 8 ? ' is-hatched' : ''}`;
}

/** Replace the slot classes on `el`. */
export function applySlot(el, slot) {
  el.classList.remove(...[...el.classList].filter((c) => c.startsWith('slot-') || c === 'is-hatched' || c === 'tag-slot'));
  el.classList.add(...slotClass(slot).split(' '));
  return el;
}

/** A coloured tag chip. The label sits on a solid backing so hatching never hurts legibility. */
export function tagChip(name, slot, { extraClass = '', tag = 'span', props = {} } = {}) {
  const el = h(tag, { class: `tag-chip ${slotClass(slot)} ${extraClass}`.trim(), ...props },
    h('span', { class: 'tag-chip__label', text: name }));
  return el;
}

/** A small colour swatch for a slot. */
export function slotSwatch(slot, extraClass = '') {
  return h('span', { class: `swatch ${slotClass(slot)} ${extraClass}`.trim(), 'aria-hidden': 'true' });
}

export function tagById(id) { return state.tags[id] || null; }
