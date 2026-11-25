// File timeline filter bar (R-143): name/path search, file-type quick filter, the active exclusions in a
// tooltip, a match badge and Clear. Its state lives in the File timeline store only.
import { debounce, h } from '../dom.js';
import { attachTooltip } from '../tooltip.js';
import { EMPTY_FILE_FILTER, TYPE_GROUPS } from './groups.js';

function seg(label, pressed, onclick, tip, disabled = false) {
  const b = h('button', { type: 'button', class: 'seg__btn', text: label, 'aria-pressed': String(pressed), onclick, disabled });
  if (tip) attachTooltip(b, tip);
  return b;
}

export function createFilterBar(host, { store, count }) {
  const types = h('div', { class: 'seg ft-filter__types', role: 'group', 'aria-label': 'File type' });
  const search = h('input', { type: 'text', class: 'input ft-filter__input', placeholder: 'Search file name or folder path',
    'aria-label': 'Search file names and folder paths', autocomplete: 'off', spellcheck: false });
  const badge = h('span', { class: 'filter-bar__badge ft-filter__badge', role: 'status', hidden: true });
  const excl = h('span', { class: 'ft-filter__excl', tabindex: '0', role: 'note' });
  const clear = h('button', { type: 'button', class: 'btn btn--ghost btn--small', text: 'Clear',
    onclick: () => store.setFilter({ ...EMPTY_FILE_FILTER, types: [] }) });
  attachTooltip(clear, 'Clear the file filter');
  attachTooltip(excl, () => {
    const s = store.state;
    const rules = s.excluded.length ? `Excluded: ${s.excluded.join(', ')}` : 'No file types are excluded.';
    const hidden = s.includeHidden ? 'Hidden and system files are included.' : 'Hidden and system files are not shown.';
    return `${rules}\n${hidden}\nChange this in Settings \u25b8 Files.`;
  });

  const root = h('div', { class: 'ft-filter' },
    h('div', { class: 'ft-filter__search' }, search),
    types, excl, badge, clear);
  host.replaceChildren(root);

  const commit = debounce((text) => { delete search.dataset.pending; if (text !== store.state.filter.text) store.setFilter({ text }); }, 150);
  search.addEventListener('input', () => { search.dataset.pending = '1'; commit(search.value); });
  search.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      if (search.value) { search.value = ''; commit.flush(''); }
    } else if (e.key === 'Enter') {
      commit.flush(search.value);
    }
  });

  function render() {
    const s = store.state;
    const f = s.filter;
    types.replaceChildren(...TYPE_GROUPS.map((g) => seg(g.label, f.types.includes(g.id),
      () => store.setFilter({ types: f.types.includes(g.id) ? f.types.filter((t) => t !== g.id) : [...f.types, g.id] }),
      `Show ${g.label.toLowerCase()}${f.types.length ? '' : ' only'}`)));
    const active = store.filterActive();
    root.classList.toggle('is-active', active);
    badge.hidden = !active;
    if (active) badge.textContent = count ? `${count()} match in this period` : `${s.filtered.length} of ${s.entries.length} match`;
    clear.disabled = !active;
    excl.textContent = s.excluded.length ? `${s.excluded.length} excluded type${s.excluded.length === 1 ? '' : 's'}` : 'No exclusions';
    if (search.value !== f.text && !search.dataset.pending) search.value = f.text;
  }

  store.on('filter', render);
  store.on('data', render);
  render();
  return { render, focusSearch: () => { search.focus(); search.select(); } };
}
