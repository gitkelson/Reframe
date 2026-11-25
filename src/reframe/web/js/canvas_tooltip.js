// Point-anchored hover tooltip for the canvas modules (R-96: appears after 400 ms). The shared tooltip.js is
// anchored to DOM elements, which canvas regions are not.

export const TOOLTIP_DELAY_MS = 400;

export function createCanvasTooltip(host) {
  const el = document.createElement('div');
  el.className = 'rf-tooltip';
  el.setAttribute('role', 'tooltip');
  el.hidden = true;
  host.appendChild(el);
  let timer = null;
  let key = null;

  function hide() {
    clearTimeout(timer);
    timer = null;
    key = null;
    el.hidden = true;
  }

  function place(x, y) {
    const hw = host.clientWidth, hh = host.clientHeight;
    el.style.left = `${Math.max(4, Math.min(x + 14, hw - el.offsetWidth - 4))}px`;
    const below = y + 20;
    el.style.top = `${below + el.offsetHeight > hh ? Math.max(4, y - el.offsetHeight - 8) : below}px`;
  }

  /** Call on every pointer move over a target. `build(el)` fills the tooltip with DOM nodes (never HTML). */
  function hover(newKey, x, y, build) {
    if (newKey === key) return;
    hide();
    if (newKey === null || newKey === undefined) return;
    key = newKey;
    timer = setTimeout(() => {
      el.replaceChildren();
      build(el);
      el.hidden = false;
      place(x, y);
    }, TOOLTIP_DELAY_MS);
  }

  return { hover, hide, el };
}

export function addLine(parent, text, className) {
  const div = document.createElement('div');
  if (className) div.className = className;
  div.textContent = text;
  parent.appendChild(div);
  return div;
}
