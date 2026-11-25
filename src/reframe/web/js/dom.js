// Tiny DOM helpers. User text goes in through `text` / string children (textContent), never innerHTML.

/** h('button', {class: 'x', text: 'Hi', onclick: fn, 'aria-label': 'Hi'}, child, [child2, child3]) */
export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    // spellcheck="false" must reach the DOM: titles, tags and paths are never spell-checked (R-190)
    if (k === 'spellcheck') { el.setAttribute('spellcheck', v ? 'true' : 'false'); continue; }
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'text') el.textContent = v;
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k in el && !k.includes('-')) el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export function clear(el) { el.replaceChildren(); return el; }

export function isTextField(el) {
  if (!el || el.nodeType !== 1) return false;
  if (el.isContentEditable) return true;
  if (el.tagName === 'TEXTAREA') return true;
  if (el.tagName !== 'INPUT') return false;
  return !['button', 'checkbox', 'radio', 'submit', 'range', 'color', 'file'].includes(el.type);
}

export function debounce(fn, ms) {
  let t = null;
  const d = (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  d.flush = (...a) => { clearTimeout(t); fn(...a); };
  d.cancel = () => clearTimeout(t);
  return d;
}

/** Re-run `render` when any of the given store events fire. */
export function bindRender(on, events, render) {
  for (const e of events) on(e, render);
}

export const plural = (n, one, many = one + 's') => `${n} ${n === 1 ? one : many}`;

/** Copy text to the clipboard; resolves to true on success. */
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = h('textarea', { value: text, class: 'offscreen', 'aria-hidden': 'true' });
    document.body.append(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch { /* not permitted */ }
    ta.remove();
    return ok;
  }
}
