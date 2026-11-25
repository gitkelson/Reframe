// History tab (R-82, spec 8.5): change sets with revert, hidden and removed items, needs-attention list.
import { api } from '../api.js';
import * as actions from '../actions.js';
import { debounce, h, plural } from '../dom.js';
import { on, state } from '../store.js';
import { actorBadge, btn, conflictList, emptyState, section, shortTime } from './ui.js';

const PAGE = 60;

export function createHistoryTab() {
  const root = h('div', { class: 'tab tab--history' });
  const setsBox = h('div', { class: 'hist-list' });
  const moreBox = h('div', { class: 'hist-more' });
  const hiddenBox = h('div', { class: 'hist-list' });
  const removedBox = h('div', { class: 'hist-list' });
  const attentionBox = h('div', { class: 'hist-list' });
  root.append(section('Change sets', setsBox, moreBox), section('Hidden items', hiddenBox),
    section('Removed items', removedBox), section('Needs attention', attentionBox));
  let sets = [];
  let canLoadMore = false;

  function setRow(s) {
    const slot = h('div', {});
    const verb = s.actor === 'undo' ? 'Redo' : 'Revert';
    const row = h('div', { class: `hist-row${s.in_effect ? '' : ' is-undone'}`, dataset: { cs: String(s.id) } },
      h('span', { class: 'hist-row__time mono', text: shortTime(s.ts) }),
      actorBadge(s.actor),
      h('span', { class: 'hist-row__label', text: s.label || '(no label)' }),
      h('span', { class: 'hist-row__count', text: plural(s.rows, 'row') }),
      s.in_effect ? null : h('span', { class: 'badge', text: 'undone' }),
      s.is_undo_target ? h('span', { class: 'badge', text: 'next undo' }) : null,
      btn(verb, { iconName: 'restore', ariaLabel: `${verb} change set ${s.id}: ${s.label}`, onclick: async () => {
        const r = await actions.revertChangeSet(s.id);
        slot.replaceChildren(!r.ok && r.conflicts ? conflictList(r.conflicts) : '');
        if (!r.ok && !r.conflicts && r.message) slot.replaceChildren(h('p', { class: 'field__msg', 'data-kind': 'error', role: 'alert', text: r.message }));
      } }));
    return h('div', { class: 'hist-item' }, row, slot);
  }

  function renderSets() {
    setsBox.replaceChildren(...(sets.length ? sets.map(setRow) : [emptyState('No changes yet.')]));
    moreBox.replaceChildren(canLoadMore ? btn('Load older changes', { onclick: loadMore }) : '');
  }

  async function loadSets() {
    const r = await api.history({ limit: Math.max(PAGE, sets.length) });
    sets = r.history;
    canLoadMore = sets.length >= PAGE;
    renderSets();
  }
  async function loadMore() {
    const r = await api.history({ limit: PAGE, before: sets[sets.length - 1].id });
    sets = sets.concat(r.history);
    canLoadMore = r.history.length >= PAGE;
    renderSets();
  }

  const itemLine = (it, extra) => h('div', { class: 'hist-row' },
    h('span', { class: 'hist-row__label', text: it.title }),
    h('span', { class: 'mono hist-row__time', text: it.start_date === it.end_date ? it.start_date : `${it.start_date} to ${it.end_date}` }),
    ...extra);

  async function loadHiddenRemoved() {
    const r = await api.hiddenRemoved();
    hiddenBox.replaceChildren(...(r.hidden.length ? r.hidden.map((it) => itemLine(it, [
      btn('Unhide', { onclick: () => actions.mutate(() => api.unhideItem(it.id, false)).then(loadAll) }),
      it.outlook_series_id ? btn('Unhide series', { onclick: () => actions.mutate(() => api.unhideItem(it.id, true)).then(loadAll) }) : null,
    ])) : [emptyState('Nothing is hidden.')]));
    removedBox.replaceChildren(...(r.removed.length ? r.removed.map((it) => itemLine(it, [
      h('span', { class: 'hist-row__why', text: it.origin === 'outlook' ? 'removed by sync' : 'deleted' }),
      btn('Restore', { iconName: 'restore', onclick: () => actions.mutate(() => api.restoreItem(it.id)).then(loadAll) }),
    ])) : [emptyState('No deleted or removed items.')]));
  }

  async function loadAttention() {
    const r = await api.needsAttention();
    const rows = [
      ...r.bad_dates.map((i) => `Item “${i.title}” has unreadable dates (${i.start_date} / ${i.end_date}).`),
      ...r.orphan_item_tags.map((i) => `Tag assignment points at a missing item or tag (${String(i.item_id).slice(0, 8)}).`),
      ...r.orphan_attachments.map((i) => `Attachment “${i.target}” belongs to a missing item.`),
    ];
    attentionBox.replaceChildren(...(rows.length ? rows.map((t) => h('div', { class: 'banner banner--warn' }, h('span', { class: 'banner__text', text: t }))) : [emptyState('Nothing needs attention.')]));
  }

  const loadAll = () => { if (!root.hidden) Promise.all([loadSets(), loadHiddenRemoved(), loadAttention()]).catch(() => {}); };
  const soon = debounce(loadAll, 250);
  for (const ev of ['items', 'undo']) on(ev, soon);
  return { el: root, activate: () => { sets = []; loadAll(); } };
}
