// Tags tab (R-43, R-44, R-46, R-110 .. R-113): the tag tree with usage counts, create, rename, merge, delete,
// colour, move (drag onto a tag to make it a child, onto the top area to make it groupless, or "Move to…"),
// retire / un-retire (Retired section), expand / collapse (remembered) and hygiene repair.
import { ApiError, api } from '../api.js';
import * as actions from '../actions.js';
import { debounce, h, plural } from '../dom.js';
import { notify, on, setFilter, state } from '../store.js';
import { SLOT_COUNT, slotClass, slotSwatch } from '../theme.js';
import { btn, emptyState, section } from './ui.js';

const DRAG_TYPE = 'text/x-reframe-tag-move';

export function createTagsTab() {
  const root = h('div', { class: 'tab tab--tags' });
  const hygieneBox = h('div', { class: 'hygiene' });
  const createBox = h('div', { class: 'tag-create' });
  const tree = h('div', { class: 'tag-tree', role: 'tree', 'aria-label': 'Tags' });
  const topDrop = h('div', { class: 'tag-tree__top', text: 'Drop here to make a tag groupless (top level)', hidden: true });
  const retiredBox = h('div', { class: 'tag-tree tag-tree--retired', role: 'tree', 'aria-label': 'Retired tags' });
  const retiredToggle = h('button', { type: 'button', class: 'btn btn--ghost btn--small', 'aria-expanded': 'false' });
  const dragChip = h('div', { class: 'tag-drag-chip', role: 'status', hidden: true });
  root.append(
    section('Possible duplicates', hygieneBox),
    section('Tags', createBox, topDrop, tree,
      h('p', { class: 'field__hint', text: 'Drag a tag onto another tag to put it in that group; items with the tag also get the group. Colours are stored as a slot, so they follow the active theme. Slots 9-16 are hatched.' })),
    section('Retired', retiredToggle, retiredBox),
    dragChip);

  let open = null;        // {id, mode: 'color' | 'rename' | 'merge' | 'delete' | 'move'}
  let message = null;     // {id, text}
  let retiredOpen = false;
  let collapsed = new Set(state.settings.tag_tree_collapsed || []);
  let dragId = null;
  let dragCount = null;

  const setOpen = (id, mode) => { open = open && open.id === id && open.mode === mode ? null : { id, mode }; message = null; render(true); };
  const fail = (id, e) => { message = { id, text: e instanceof ApiError ? e.message : 'Something went wrong' }; render(true); };

  async function run(id, fn, undoNotice) {
    let err = null;
    await actions.mutate(async () => { try { await fn(); } catch (e) { err = e; } });
    if (err) { fail(id, err); return false; }
    await actions.refreshMeta().catch(() => {});
    if (undoNotice) notify(undoNotice, { undo: true });
    open = null; message = null;
    render(true);
    return true;
  }

  const saveCollapsed = debounce(() => actions.saveSettings({ tag_tree_collapsed: [...collapsed] }), 400);
  const children = (parentId) => state.tagList.filter((t) => (t.parent_id || null) === parentId && !t.retired);

  // ------------------------------------------------------------------ forms

  function colorGrid(t) {
    return h('div', { class: 'slot-grid', role: 'group', 'aria-label': `Colour for ${t.name}` },
      Array.from({ length: SLOT_COUNT }, (_, s) => h('button', {
        type: 'button', class: `slot-btn ${slotClass(s)}${t.slot === s ? ' is-current' : ''}`, 'aria-label': `Colour slot ${s + 1}${s >= 8 ? ' (hatched)' : ''}`,
        'aria-pressed': String(t.slot === s), onclick: () => run(t.id, () => api.patchTag(t.id, { color_slot: s })),
      }, t.slot === s ? h('span', { class: 'slot-btn__check', text: '✓' }) : null)));
  }

  function renameForm(t) {
    const input = h('input', { type: 'text', class: 'input', value: t.name, 'aria-label': `New name for ${t.name}`, spellcheck: false });
    const go = () => { const v = input.value.trim(); if (v && v !== t.name) run(t.id, () => api.patchTag(t.id, { name: v })); else setOpen(t.id, 'rename'); };
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); go(); }
      else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); setOpen(t.id, 'rename'); }
    });
    setTimeout(() => { input.focus(); input.select(); }, 0);
    return h('div', { class: 'tag-form' }, input, btn('Rename', { variant: 'primary', onclick: go }), btn('Cancel', { onclick: () => setOpen(t.id, 'rename') }));
  }

  function mergeForm(t) {
    const others = state.tagList.filter((o) => o.id !== t.id);
    const sel = h('select', { class: 'input', 'aria-label': `Merge ${t.name} into` },
      others.map((o) => h('option', { value: o.id, text: `${o.path || o.name} (${o.usage})` })));
    return h('div', { class: 'tag-form' }, h('span', { text: `Merge “${t.name}” into` }), sel,
      btn('Merge', { variant: 'primary', onclick: () => {
        const into = state.tags[sel.value];
        run(t.id, () => api.mergeTag(t.id, sel.value), `Merged '${t.name}' into '${into?.name ?? ''}'`);
      } }),
      btn('Cancel', { onclick: () => setOpen(t.id, 'merge') }));
  }

  function deleteForm(t) {
    return h('div', { class: 'tag-form' },
      h('span', { text: t.usage ? `“${t.name}” is on ${plural(t.usage, 'item')}.` : `Delete “${t.name}”?` }),
      t.has_children ? h('span', { class: 'field__hint', text: 'Its tags move up one level.' }) : null,
      btn(t.usage ? 'Remove from all items' : 'Delete tag', { variant: 'danger', iconName: 'trash',
        onclick: () => run(t.id, () => api.deleteTag(t.id, t.usage > 0), `Deleted tag '${t.name}'${t.usage ? ` (removed from ${plural(t.usage, 'item')})` : ''}`) }),
      btn('Cancel', { onclick: () => setOpen(t.id, 'delete') }));
  }

  /** Keyboard path for moving a tag: a type-ahead group picker (R-43). */
  function moveForm(t) {
    const banned = new Set([t.id]);
    const addDesc = (id) => { for (const c of state.tagList.filter((x) => x.parent_id === id)) { banned.add(c.id); addDesc(c.id); } };
    addDesc(t.id);
    const groups = state.tagList.filter((x) => !banned.has(x.id) && !x.retired);
    const input = h('input', { type: 'text', class: 'input', placeholder: 'Type a group name', 'aria-label': `Move ${t.name} to`, spellcheck: false });
    const list = h('div', { class: 'tag-move-list', role: 'listbox' });
    const pick = (parent) => run(t.id, () => api.moveTag(t.id, parent?.id || null),
      `Moved '${t.name}' to ${parent ? `'${parent.name}'` : 'the top level'}`);
    const paint = () => {
      const q = input.value.trim().toLowerCase();
      const hits = groups.filter((g) => !q || (g.path || g.name).toLowerCase().includes(q)).slice(0, 8);
      list.replaceChildren(
        h('button', { type: 'button', class: 'tag-move-opt', role: 'option', text: '(top level: no group)', onclick: () => pick(null) }),
        ...hits.map((g) => h('button', { type: 'button', class: 'tag-move-opt', role: 'option', text: g.path || g.name, onclick: () => pick(g) })));
    };
    input.addEventListener('input', paint);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); setOpen(t.id, 'move'); }
      else if (e.key === 'Enter') { e.preventDefault(); list.querySelectorAll('.tag-move-opt')[input.value.trim() ? 1 : 0]?.click(); }
      else if (e.key === 'ArrowDown') { e.preventDefault(); list.querySelector('.tag-move-opt')?.focus(); }
    });
    paint();
    setTimeout(() => input.focus(), 0);
    return h('div', { class: 'tag-form tag-form--move' }, input, list, btn('Cancel', { onclick: () => setOpen(t.id, 'move') }));
  }

  // ------------------------------------------------------------------ drag to group (R-112)

  function chipText(target) {
    const t = state.tags[dragId];
    const where = target ? `into ${target.name}` : 'to the top level';
    const n = dragCount == null ? '' : ` (${plural(dragCount, 'item')} updated)`;
    return `Move ${t?.name ?? ''} ${where}${n}`;
  }

  function allowedTarget(targetId) {
    if (!dragId || targetId === dragId) return false;
    let p = targetId ? state.tags[targetId] : null;
    while (p) { if (p.parent_id === dragId) return false; p = p.parent_id ? state.tags[p.parent_id] : null; }
    const cur = state.tags[dragId]?.parent_id || null;
    return cur !== (targetId || null);
  }

  function showChip(e, target) {
    dragChip.hidden = false;
    dragChip.textContent = chipText(target);
    const r = root.getBoundingClientRect();
    dragChip.style.left = `${Math.max(4, e.clientX - r.left + 12)}px`;
    dragChip.style.top = `${e.clientY - r.top + root.scrollTop + 12}px`;
  }

  function endDrag() {
    dragId = null; dragCount = null;
    dragChip.hidden = true;
    topDrop.hidden = true;
    tree.querySelectorAll('.is-drop').forEach((n) => n.classList.remove('is-drop'));
  }

  async function dropOn(targetId) {
    const id = dragId;
    endDrag();
    if (!id) return;
    const t = state.tags[id], target = targetId ? state.tags[targetId] : null;
    await run(id, () => api.moveTag(id, targetId), `Moved '${t?.name}' to ${target ? `'${target.name}'` : 'the top level'}`);
  }

  function wireDrag(rowEl, t) {
    rowEl.draggable = true;
    rowEl.addEventListener('dragstart', (e) => {
      dragId = t.id;
      dragCount = null;
      e.dataTransfer.setData(DRAG_TYPE, t.id);
      e.dataTransfer.effectAllowed = 'move';
      topDrop.hidden = !t.parent_id;
      api.movePreview(t.id).then((r) => { if (dragId === t.id) dragCount = r.items; }).catch(() => {});
    });
    rowEl.addEventListener('dragend', endDrag);
    rowEl.addEventListener('dragover', (e) => {
      if (!allowedTarget(t.id)) return;
      e.preventDefault();
      e.stopPropagation();
      rowEl.classList.add('is-drop');
      showChip(e, t);
    });
    rowEl.addEventListener('dragleave', () => rowEl.classList.remove('is-drop'));
    rowEl.addEventListener('drop', (e) => { if (!allowedTarget(t.id)) return; e.preventDefault(); e.stopPropagation(); dropOn(t.id); });
  }
  topDrop.addEventListener('dragover', (e) => { if (allowedTarget(null)) { e.preventDefault(); topDrop.classList.add('is-drop'); showChip(e, null); } });
  topDrop.addEventListener('dragleave', () => topDrop.classList.remove('is-drop'));
  topDrop.addEventListener('drop', (e) => { e.preventDefault(); dropOn(null); });

  // ------------------------------------------------------------------ rows

  function row(t, depth, { retired = false } = {}) {
    const isOpen = open && open.id === t.id;
    const kids = retired ? [] : children(t.id);
    const isCollapsed = collapsed.has(t.id);
    const form = isOpen && ({ rename: renameForm, merge: mergeForm, delete: deleteForm, color: colorGrid, move: moveForm })[open.mode]?.(t);
    const toggle = kids.length ? h('button', { type: 'button', class: 'tag-row__toggle', 'aria-label': `${isCollapsed ? 'Expand' : 'Collapse'} ${t.name}`,
      text: isCollapsed ? '▸' : '▾', onclick: () => { if (isCollapsed) collapsed.delete(t.id); else collapsed.add(t.id); saveCollapsed(); render(true); } })
      : h('span', { class: 'tag-row__toggle is-leaf', 'aria-hidden': 'true' });
    const main = h('div', { class: 'tag-row__main', style: `--depth:${depth}` },
      toggle,
      h('button', { type: 'button', class: 'tag-row__swatch', 'aria-label': `Change colour of ${t.name}`, 'aria-expanded': String(isOpen && open.mode === 'color'),
        onclick: () => setOpen(t.id, 'color') }, slotSwatch(t.slot)),
      h('button', { type: 'button', class: 'tag-row__name link', text: t.name, onclick: () => setFilter({ tags: [t.id], untagged: false }), 'aria-label': `Filter by ${t.name}` }),
      t.retired ? h('span', { class: 'chip-retired', text: 'retired' }) : null,
      h('span', { class: 'tag-row__count mono', text: String(t.usage), 'aria-label': `${plural(t.usage, 'item')}` }),
      h('div', { class: 'tag-row__actions' },
        retired
          ? btn('Un-retire', { onclick: () => run(t.id, () => api.retireTag(t.id, false), `Un-retired '${t.name}'`) })
          : btn('', { iconName: 'move', ariaLabel: `Move ${t.name} to…`, onclick: () => setOpen(t.id, 'move') }),
        btn('', { iconName: 'edit', ariaLabel: `Rename ${t.name}`, onclick: () => setOpen(t.id, 'rename') }),
        btn('', { iconName: 'merge', ariaLabel: `Merge ${t.name} into another tag`, onclick: () => setOpen(t.id, 'merge') }),
        retired ? null : btn('', { iconName: 'archive', ariaLabel: `Retire ${t.name}`, onclick: () => run(t.id, () => api.retireTag(t.id, true), `Retired '${t.name}'`) }),
        btn('', { iconName: 'trash', ariaLabel: t.usage ? `Remove ${t.name} from all items` : `Delete ${t.name}`, onclick: () => setOpen(t.id, 'delete') })));
    const el = h('div', { class: `tag-row${isOpen ? ' is-open' : ''}`, role: 'treeitem', dataset: { tagId: t.id },
      'aria-level': String(depth + 1), 'aria-expanded': kids.length ? String(!isCollapsed) : null },
    main, form || null,
    message && message.id === t.id ? h('div', { class: 'field__msg', 'data-kind': 'error', role: 'alert', text: message.text }) : null);
    if (!retired) wireDrag(main, t);
    const out = [el];
    if (kids.length && !isCollapsed) for (const k of kids) out.push(...row(k, depth + 1));
    return out;
  }

  function renderCreate() {
    const input = h('input', { type: 'text', class: 'input', placeholder: 'New tag or group', 'aria-label': 'New tag name', spellcheck: false });
    const parent = h('select', { class: 'input', 'aria-label': 'In group' },
      h('option', { value: '', text: '(no group)' }),
      ...state.tagList.filter((t) => !t.retired).map((t) => h('option', { value: t.id, text: t.path || t.name })));
    const go = async () => {
      const name = input.value.trim();
      if (!name) return;
      if (await run('new', () => api.createTag(name, parent.value || null))) input.value = '';
    };
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); go(); } });
    createBox.replaceChildren(input, parent, btn('Create', { variant: 'primary', onclick: go }),
      message && message.id === 'new' ? h('div', { class: 'field__msg', 'data-kind': 'error', role: 'alert', text: message.text }) : null);
  }

  function renderHygiene() {
    const pairs = state.hygiene || [];
    const drift = state.impliedDrift || { missing: 0, extra: 0 };
    const rows = pairs.map((p) => {
      const a = state.tags[p.a.id], b = state.tags[p.b.id];
      if (!a || !b) return null;
      const [src, dst] = a.usage <= b.usage ? [p.a, p.b] : [p.b, p.a];
      return h('div', { class: 'hygiene__row' },
        h('span', { text: `Possible duplicates: ${p.a.name} / ${p.b.name}` }),
        btn(`Merge into ${dst.name}`, { iconName: 'merge', onclick: () => run(src.id, () => api.mergeTag(src.id, dst.id), `Merged '${src.name}' into '${dst.name}'`) }));
    }).filter(Boolean);
    if (drift.missing || drift.extra) {
      rows.push(h('div', { class: 'hygiene__row' },
        h('span', { text: `${plural(drift.missing + drift.extra, 'group tag')} on items disagree with the tag tree (changed outside Reframe).` }),
        btn('Repair', { onclick: () => run('repair', () => api.repairImplied(), 'Repaired group tags') })));
    }
    hygieneBox.replaceChildren(...(rows.length ? rows : [emptyState('No near-duplicate tags found.')]));
  }

  function render(force = false) {
    if (root.hidden) return;
    if (!force && root.contains(document.activeElement) && document.activeElement.tagName === 'INPUT') return;
    renderHygiene();
    renderCreate();
    const tops = children(null);
    // a tag whose parent is retired still shows under the root so it stays reachable
    const orphans = state.tagList.filter((t) => !t.retired && t.parent_id && state.tags[t.parent_id]?.retired);
    const rows = [...tops, ...orphans].flatMap((t) => row(t, 0));
    tree.replaceChildren(...(rows.length ? rows : [emptyState('No tags yet. Type a tag into an item to create one.')]));
    const retired = state.tagList.filter((t) => t.retired);
    retiredToggle.textContent = `${retiredOpen ? 'Hide' : 'Show'} ${plural(retired.length, 'retired tag')}`;
    retiredToggle.setAttribute('aria-expanded', String(retiredOpen));
    retiredToggle.disabled = !retired.length;
    retiredBox.hidden = !retiredOpen;
    retiredBox.replaceChildren(...(retiredOpen ? retired.flatMap((t) => row(t, 0, { retired: true })) : []));
  }
  retiredToggle.addEventListener('click', () => { retiredOpen = !retiredOpen; render(true); });

  on('items', () => render());
  return { el: root, activate: () => { actions.refreshMeta().catch(() => {}); collapsed = new Set(state.settings.tag_tree_collapsed || []); render(true); } };
}
