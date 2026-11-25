// Hover tooltips (R-96): appear after 400 ms, one shared element, reusable by any module.
import { h } from './dom.js';

const DELAY_MS = 400;
let tip = null;
let timer = null;
let owner = null;

function ensure() {
  if (!tip) {
    tip = h('div', { id: 'tooltip', role: 'tooltip', class: 'tooltip' });
    document.body.append(tip);
  }
  return tip;
}

export function hideTooltip() {
  clearTimeout(timer);
  owner = null;
  if (tip) tip.classList.remove('is-visible');
}

function place(el) {
  const t = ensure();
  const r = el.getBoundingClientRect();
  const w = t.offsetWidth, hgt = t.offsetHeight;
  let x = r.left + r.width / 2 - w / 2;
  let y = r.bottom + 8;
  if (y + hgt > window.innerHeight - 4) y = r.top - hgt - 8;
  x = Math.max(4, Math.min(window.innerWidth - w - 4, x));
  t.style.left = `${Math.round(x)}px`;
  t.style.top = `${Math.round(Math.max(4, y))}px`;
}

export function showTooltipNow(el, text) {
  if (!text) return;
  const t = ensure();
  t.textContent = text;
  owner = el;
  t.classList.add('is-visible');
  place(el);
}

/** attachTooltip(el, 'text' | () => 'text'). The text may contain line breaks. */
export function attachTooltip(el, textOrFn) {
  const start = () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      if (!el.isConnected) return;
      const text = typeof textOrFn === 'function' ? textOrFn() : textOrFn;
      showTooltipNow(el, text);
    }, DELAY_MS);
  };
  const stop = () => { if (owner === el || timer) hideTooltip(); };
  el.addEventListener('mouseenter', start);
  el.addEventListener('mouseleave', stop);
  el.addEventListener('mousedown', stop);
  el.addEventListener('focus', () => { if (el.matches(':focus-visible')) start(); });
  el.addEventListener('blur', stop);
  el.dataset.tooltip = '1';
  return el;
}

document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hideTooltip(); }, true);
window.addEventListener('scroll', hideTooltip, true);
