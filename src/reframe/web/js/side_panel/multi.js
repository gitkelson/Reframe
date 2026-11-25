// Multi-select tab (R-101): shows "mixed" where values differ; tags and kind are editable for all.
import * as actions from '../actions.js';
import { h, plural } from '../dom.js';
import { icon } from '../icons.js';
import { on, selectedItems, state } from '../store.js';
import { createTagEditor } from './tag_editor.js';
import { section } from './ui.js';

const common = (values) => (new Set(values).size === 1 ? values[0] : null);

export function createMultiTab() {
  const root = h('div', { class: 'tab tab--multi' });
  const heading = h('h2', { class: 'multi-count' });
  const info = h('dl', { class: 'kv' });
  const kindPlan = h('button', { type: 'button', class: 'seg__btn', text: 'Plan', onclick: () => setKind('plan') });
  const kindLog = h('button', { type: 'button', class: 'seg__btn', text: 'Log', onclick: () => setKind('log') });
  const kindMixed = h('span', { class: 'mixed', text: 'mixed', hidden: true });
  const skipNote = h('p', { class: 'field__hint' });
  const editor = createTagEditor({
    primaryMarker: false,
    onAdd: (name) => actions.bulkTags(ids(), { add: [name] }),
    onRemove: (name) => actions.bulkTags(ids(), { remove: [name] }),
  });
  const del = h('button', { type: 'button', class: 'btn btn--danger', onclick: () => actions.deleteSelection() }, icon('trash', 14), h('span', { text: 'Delete selected' }));
  root.append(heading,
    section('Values', info),
    section('Kind', h('div', { class: 'seg', role: 'group', 'aria-label': 'Kind of all selected items' }, kindPlan, kindLog), kindMixed),
    section('Tags', editor.el, h('p', { class: 'field__hint', text: 'Adding or removing a tag applies to every selected item.' })),
    section('Delete', del, skipNote));

  const ids = () => [...state.selection];
  const setKind = (k) => actions.bulkKind(ids(), k);

  function render() {
    if (root.hidden) return;
    const sel = selectedItems();
    const n = sel.length;
    heading.textContent = `${n} items selected`;
    const locked = sel.filter((i) => i.locked).length;
    const val = (v) => v ?? 'mixed';
    info.replaceChildren(
      h('dt', { text: 'Start' }), h('dd', { class: 'mono', text: val(common(sel.map((i) => i.start_date))) }),
      h('dt', { text: 'End' }), h('dd', { class: 'mono', text: val(common(sel.map((i) => i.end_date))) }),
      h('dt', { text: 'Origin' }), h('dd', { text: val(common(sel.map((i) => i.origin))) }));
    const kind = common(sel.map((i) => i.kind));
    kindPlan.setAttribute('aria-pressed', String(kind === 'plan'));
    kindLog.setAttribute('aria-pressed', String(kind === 'log'));
    kindMixed.hidden = kind !== null;
    const counts = new Map();
    for (const it of sel) for (const t of it.tags) counts.set(t, (counts.get(t) || 0) + 1);
    editor.setTags([...counts].filter(([t]) => state.tags[t]).map(([t, c]) => ({
      name: state.tags[t].name, slot: state.tags[t].slot, badge: c === n ? null : `${c}/${n}` })));
    skipNote.textContent = locked
      ? `${plural(locked, 'imported item')} will be skipped by delete and date moves (change them in Outlook). Tags can still be edited.`
      : 'Delete can be undone from the status bar.';
  }

  on('selection', render);
  on('items', render);
  return { el: root, activate: render };
}
