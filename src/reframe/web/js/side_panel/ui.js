// Small UI helpers shared by the side-panel tabs.
import { h } from '../dom.js';
import { icon } from '../icons.js';

/** A labelled form field with an inline message line. Returns {el, msg(text, kind)}. */
export function field(label, control, { id, hint } = {}) {
  const message = h('div', { class: 'field__msg', role: 'status' });
  if (id) control.id = id;
  const el = h('div', { class: 'field' },
    h('label', { class: 'field__label', for: id || null, text: label }),
    control,
    hint ? h('div', { class: 'field__hint', text: hint }) : null,
    message);
  return {
    el,
    msg(text, kind = 'error') {
      message.textContent = text || '';
      message.dataset.kind = text ? kind : '';
      control.classList.toggle('is-invalid', !!text && kind === 'error');
      if (text && kind === 'error') control.setAttribute('aria-invalid', 'true'); else control.removeAttribute('aria-invalid');
    },
  };
}

/** A button: variant 'primary' | 'ghost' (default) | 'danger'; optional icon name. */
export function btn(text, { onclick, variant = 'ghost', iconName, small = true, title, type = 'button', ariaLabel } = {}) {
  return h('button', {
    type, class: `btn btn--${variant}${small ? ' btn--small' : ''}`, onclick, title: null,
    'aria-label': ariaLabel || null,
  }, iconName ? icon(iconName, 14) : null, text ? h('span', { text }) : null);
}

export function section(title, ...children) {
  return h('section', { class: 'psec' }, h('h3', { class: 'psec__title', text: title }), ...children);
}

/** A dismissible inline banner. kind: info | warn | error */
export function banner(text, kind = 'info', actions = []) {
  return h('div', { class: `banner banner--${kind}`, role: kind === 'error' ? 'alert' : 'status' },
    icon(kind === 'info' ? 'info' : 'warn', 14), h('span', { class: 'banner__text', text }), ...actions);
}

const ACTORS = { user: 'You', agent: 'Agent', sync: 'Sync', undo: 'Undo', external: 'External' };
export function actorBadge(actor) {
  return h('span', { class: `actor actor--${actor}`, text: ACTORS[actor] || actor });
}

/** "2026-11-10 14:03:22" (UTC or local text from the DB) to "Nov 10 14:03". */
export function shortTime(ts) {
  if (!ts) return '';
  const d = new Date(String(ts).includes('T') || String(ts).endsWith('Z') ? ts : `${String(ts).replace(' ', 'T')}Z`);
  if (Number.isNaN(d.getTime())) return String(ts);
  const mon = d.toLocaleString('en-US', { month: 'short' });
  const p = (n) => String(n).padStart(2, '0');
  return `${mon} ${d.getDate()} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** Rows of conflicts returned by a refused revert (spec 8.2 / R-82). */
export function conflictList(conflicts) {
  return h('div', { class: 'conflicts', role: 'alert' },
    h('p', { class: 'conflicts__title', text: 'Revert refused: these rows changed since.' }),
    h('ul', {}, conflicts.map((c) => h('li', {},
      h('span', { class: 'conflicts__table', text: `${c.table} ${String(c.row_key).slice(0, 12)}` }),
      h('span', { class: 'conflicts__detail', text: describe(c) })))));
}

function describe(c) {
  const cur = c.current ? Object.entries(c.current).filter(([k]) => ['title', 'start_date', 'end_date', 'name', 'notes'].includes(k))
    .map(([k, v]) => `${k}: ${v}`).join(', ') : 'row removed';
  return `now ${cur || 'different'}`;
}

export function emptyState(text) { return h('p', { class: 'empty', text }); }
