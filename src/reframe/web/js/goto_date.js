// Inline "Go to date" bar (Ctrl+J, R-100): top-centre of #overlay-root, not a dialog.
import { goToDate } from './commands.js';
import { h } from './dom.js';
import { DATE_HINT } from './dates.js';

let open = null;

export function closeGoToDate() {
  if (!open) return;
  open.root.remove();
  (open.previous && open.previous.isConnected ? open.previous : document.querySelector('#timeline-host canvas'))?.focus?.();
  open = null;
}

export function openGoToDate() {
  if (open) { open.input.focus(); open.input.select(); return; }
  const previous = document.activeElement;
  const error = h('div', { class: 'goto__error', role: 'alert' });
  const input = h('input', { type: 'text', class: 'input goto__input', placeholder: '2026-11-08', 'aria-label': 'Date to go to',
    autocomplete: 'off', spellcheck: false });
  const submit = () => {
    const msg = goToDate(input.value);
    if (msg) { error.textContent = msg || DATE_HINT; input.classList.add('is-invalid'); input.focus(); return; }
    closeGoToDate();
  };
  input.addEventListener('input', () => { error.textContent = ''; input.classList.remove('is-invalid'); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); submit(); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeGoToDate(); }
  });
  const bar = h('div', { class: 'goto', role: 'region', 'aria-label': 'Go to date' },
    h('label', { class: 'goto__row' }, h('span', { class: 'goto__label', text: 'Go to date' }), input,
      h('button', { type: 'button', class: 'btn btn--primary', text: 'Go', onclick: submit })),
    error,
    h('div', { class: 'goto__hint', text: DATE_HINT }));
  const root = h('div', { class: 'overlay-scrim goto-scrim', onmousedown: (e) => { if (e.target === root) closeGoToDate(); } }, bar);
  document.getElementById('overlay-root').append(root);
  open = { root, input, previous };
  input.focus();
}
