// Inline tag chip editor (R-40..R-42, R-44, R-111, R-113, R-114, spec 6.5): autocomplete with group paths,
// near-match suggestion, drag (or Alt+Left/Right) to reorder ANY chip, Enter/comma to add, Backspace to remove the
// last chip. The first chip is the primary tag (sets the bar colour). Implied group tags are subdued; retired
// tags carry a marker.
import { api } from '../api.js';
import { debounce, h } from '../dom.js';
import { icon } from '../icons.js';
import { state } from '../store.js';
import { tagChip } from '../theme.js';

const DRAG_TYPE = 'text/x-reframe-tag';
const keyOf = (s) => s.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim();

/**
 * createTagEditor({onAdd(name), onRemove(name), onReorder(names), readOnly, primaryMarker})
 * Returns {el, setTags([{name, slot, badge?, implied?, impliedBy?, retired?}]), focus(), input, showHint(text)}.
 */
export function createTagEditor({ onAdd, onRemove, onReorder, readOnly = false, primaryMarker = true, placeholder = 'Add tag' }) {
  let tags = [];
  let dragFrom = -1;
  let options = [];
  let active = -1;
  let pendingNear = null;

  const chips = h('div', { class: 'tag-editor__chips', role: 'list', 'aria-label': 'Tags' });
  const input = h('input', { type: 'text', class: 'input tag-editor__input', placeholder, 'aria-label': 'Add tag',
    autocomplete: 'off', spellcheck: false, role: 'combobox', 'aria-expanded': 'false', 'aria-autocomplete': 'list' });
  const list = h('div', { class: 'tag-editor__options', role: 'listbox', hidden: true });
  const hint = h('div', { class: 'tag-editor__suggest', role: 'status', hidden: true });
  const el = h('div', { class: 'tag-editor' }, chips, readOnly ? null : h('div', { class: 'tag-editor__entry' }, input), list, hint);
  el.dataset.readonly = String(readOnly);

  const names = () => tags.map((t) => t.name);

  function renderChips() {
    chips.replaceChildren(...tags.map((t, i) => {
      const cls = `tag-editor__chip${i === 0 && primaryMarker ? ' is-primary' : ''}${t.implied ? ' is-implied' : ''}${t.retired ? ' is-retired' : ''}`;
      const chip = tagChip(t.name, t.slot, { extraClass: cls,
        props: { role: 'listitem', tabIndex: readOnly ? null : 0, draggable: !readOnly && !!onReorder,
          title: t.implied ? `Added by group ${t.impliedBy || ''}`.trim() : null,
          'aria-label': `${t.name}${t.implied ? ' (added by group)' : ''}${t.retired ? ' (retired)' : ''}` } });
      if (t.retired) chip.append(h('span', { class: 'chip-retired', text: 'retired' }));
      if (i === 0 && primaryMarker && tags.length > 1) {
        chip.prepend(h('span', { class: 'tag-editor__primary', title: 'Primary tag: sets the bar colour', 'aria-label': 'Primary tag', text: '★' }));
      }
      if (t.badge) chip.append(h('span', { class: 'tag-editor__badge', text: t.badge }));
      if (!readOnly) {
        chip.append(h('button', { type: 'button', class: 'tag-editor__remove', 'aria-label': `Remove tag ${t.name}`,
          onclick: () => onRemove?.(t.name) }, icon('close', 10)));
        chip.addEventListener('keydown', (e) => chipKey(e, i));
        chip.addEventListener('dragstart', (e) => { dragFrom = i; e.dataTransfer.setData(DRAG_TYPE, t.name); e.dataTransfer.effectAllowed = 'move'; });
        chip.addEventListener('dragover', (e) => { if (dragFrom >= 0) { e.preventDefault(); chip.classList.add('is-drop'); } });
        chip.addEventListener('dragleave', () => chip.classList.remove('is-drop'));
        chip.addEventListener('drop', (e) => { e.preventDefault(); e.stopPropagation(); chip.classList.remove('is-drop'); reorder(dragFrom, i); dragFrom = -1; });
        chip.addEventListener('dragend', () => { dragFrom = -1; });
      }
      return chip;
    }));
    if (!tags.length) chips.append(h('span', { class: 'tag-editor__empty', text: readOnly ? 'No tags' : '' }));
  }

  function reorder(from, to) {
    if (from < 0 || to < 0 || from === to) return;
    const next = names();
    next.splice(to, 0, next.splice(from, 1)[0]);
    onReorder?.(next);
  }

  function chipKey(e, i) {
    if (e.altKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
      e.preventDefault();
      reorder(i, Math.max(0, Math.min(tags.length - 1, i + (e.key === 'ArrowLeft' ? -1 : 1))));
    } else if (e.key === 'Delete' || e.key === 'Backspace') {
      e.preventDefault();
      onRemove?.(tags[i].name);
    }
  }

  function closeOptions() {
    options = []; active = -1;
    list.hidden = true; list.replaceChildren();
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
  }

  function setActive(i) {
    active = i;
    [...list.children].forEach((c, k) => c.classList.toggle('is-active', k === i));
    if (i >= 0) input.setAttribute('aria-activedescendant', list.children[i].id); else input.removeAttribute('aria-activedescendant');
  }

  function showOptions(matches) {
    const have = new Set(tags.map((t) => keyOf(t.name)));
    options = matches.filter((m) => !have.has(keyOf(m.name))).slice(0, 6);
    list.replaceChildren(...options.map((m, i) => {
      const slot = state.tags[m.id]?.slot ?? m.color_slot ?? 0;
      const row = h('div', { class: 'tag-editor__option', role: 'option', id: `tag-opt-${i}`, onmousedown: (e) => { e.preventDefault(); add(m.name); } },
        tagChip(m.name, slot),
        m.path && m.path !== m.name ? h('span', { class: 'tag-editor__path', text: m.path }) : null);
      return row;
    }));
    list.hidden = !options.length;
    input.setAttribute('aria-expanded', String(!!options.length));
    setActive(-1);
  }

  const suggest = debounce(async () => {
    const q = input.value.trim();
    if (!q) { closeOptions(); return; }
    try {
      const r = await api.suggestTags(q);
      if (input.value.trim() === q) showOptions(r.matches);
    } catch { closeOptions(); }
  }, 90);

  function clearHint() { pendingNear = null; hint.hidden = true; hint.replaceChildren(); }

  function add(name) {
    input.value = '';
    closeOptions();
    clearHint();
    const key = keyOf(name);
    if (!key || tags.some((t) => keyOf(t.name) === key)) return;
    onAdd?.(name.trim());
  }

  async function commitTyped(force) {
    const text = input.value.trim().replace(/,+$/, '').trim();
    if (!text) return;
    let r;
    try { r = await api.suggestTags(text); } catch { add(text); return; }
    if (r.exact) { add(r.exact.name); return; }
    if (r.retired) { renderRetired(r.retired); return; }
    const near = (r.near || []).filter((n) => !tags.some((t) => keyOf(t.name) === keyOf(n.name)));
    if (near.length && !(force && pendingNear && pendingNear.text === text)) {
      pendingNear = { text, near };
      renderNear(text, near);
      return;
    }
    add(text);
  }

  function renderRetired(tag) {
    hint.hidden = false;
    hint.replaceChildren(
      h('span', { class: 'tag-editor__suggest-text' }, h('b', { text: tag.name }), ' is retired.'),
      h('button', { type: 'button', class: 'btn btn--small', text: `Un-retire ${tag.name}`, onclick: async () => {
        try { await api.retireTag(tag.id, false); add(tag.name); } catch (e) { showHint(e.message); }
      } }));
  }

  function showHint(text) {
    hint.hidden = false;
    hint.replaceChildren(h('span', { class: 'tag-editor__suggest-text', role: 'alert', text }));
  }

  function renderNear(text, near) {
    hint.hidden = false;
    hint.replaceChildren(
      h('span', { class: 'tag-editor__suggest-text' }, 'Use ', h('b', { text: near[0].name }), '?'),
      h('button', { type: 'button', class: 'btn btn--small', text: 'Use it', onclick: () => add(near[0].name) }),
      h('button', { type: 'button', class: 'btn btn--small btn--ghost', text: `Create “${text}”`, onclick: () => add(text) }),
      h('span', { class: 'tag-editor__suggest-hint', text: 'Enter again creates a new tag' }));
  }

  input.addEventListener('input', () => { clearHint(); suggest(); });
  input.addEventListener('blur', () => setTimeout(closeOptions, 120));
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' && options.length) { e.preventDefault(); setActive((active + 1) % options.length); }
    else if (e.key === 'ArrowUp' && options.length) { e.preventDefault(); setActive((active - 1 + options.length) % options.length); }
    else if (e.key === 'Escape') {
      if (options.length || !hint.hidden || input.value) { e.preventDefault(); e.stopPropagation(); closeOptions(); clearHint(); input.value = ''; }
    } else if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      if (e.key === 'Enter' && active >= 0) add(options[active].name);
      else commitTyped(e.key === 'Enter');
    } else if (e.key === 'Backspace' && !input.value && tags.length) {
      e.preventDefault();
      onRemove?.(tags[tags.length - 1].name);
    }
  });

  renderChips();
  return {
    el, input, showHint,
    setTags(next) { tags = next; renderChips(); },
    focus() { input.focus(); },
  };
}
