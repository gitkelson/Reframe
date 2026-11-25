// Easter eggs for the 2001 Meridian theme (R-184, R-185). Nothing here writes to the database or makes a sound.
import { h } from './dom.js';
import { notify, state } from './store.js';

const HAL_WINDOW_MS = 10_000;
const HAL_SHOW_MS = 4000;
const HAL_COOLDOWN_MS = 60_000;
const MONOLITH_MS = 4000;

let lastLockedAt = 0;
let lastHalAt = -Infinity;
let eggHook = null;

/** Any other user action resets the "consecutive" count. */
export function otherAction() { lastLockedAt = 0; }

/**
 * A refused edit of a locked imported item. Returns true when HAL answered instead of the normal notice:
 * 2001 theme only, the second consecutive attempt within 10 s, at most once per minute.
 */
export function lockedAttempt(now = Date.now()) {
  const consecutive = lastLockedAt && now - lastLockedAt <= HAL_WINDOW_MS;
  lastLockedAt = now;
  if (state.theme !== 'meridian' || !consecutive || now - lastHalAt < HAL_COOLDOWN_MS) return false;
  lastHalAt = now;
  lastLockedAt = 0;
  showHal();
  return true;
}

function showHal() {
  const root = document.getElementById('overlay-root');
  if (!root) return;
  const line = "I'm sorry, Dave. I'm afraid I can't do that.";
  const el = h('div', { class: 'hal', role: 'status', 'aria-live': 'polite' },
    h('span', { class: 'hal__eye', 'aria-hidden': 'true' }), h('span', { class: 'hal__text', text: line }));
  root.append(el);
  notify(line, { kind: 'info', ttl: HAL_SHOW_MS });
  setTimeout(() => el.remove(), HAL_SHOW_MS);
}

/** Typing exactly "monolith" in the search field (2001 theme) stands a monolith on today's cell for 4 s. */
export function checkMonolith(text) {
  if (state.theme !== 'meridian' || (text || '').trim().toLowerCase() !== 'monolith' || !eggHook) return false;
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
  eggHook({ kind: 'monolith', start: performance.now(), reduced });
  setTimeout(() => eggHook && eggHook(null), MONOLITH_MS);
  return true;
}

export function initEasterEggs(timelineEggHook) {
  eggHook = timelineEggHook;
}
