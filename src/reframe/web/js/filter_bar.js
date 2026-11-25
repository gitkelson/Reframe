// Filter bar (R-30..R-34, R-115, R-131): tag chips with All / None / Untagged and AND/OR, search, Plan/Log,
// Local/Outlook/Feed, the Calendars control, clear and the active badge. Chips can be dragged onto bars.
import * as actions from './actions.js';
import { commands, timelineHook } from './commands.js';
import { showContextMenu } from './context_menu.js';
import { toDayNumber } from './dates.js';
import { debounce, h } from './dom.js';
import { checkMonolith } from './easter_eggs.js';
import { UNTAGGED_CHIP, matches, searchMatches, selectAllTags, selectNoTags } from './filter.js';
import { icon } from './icons.js';
import { withShortcut } from './shortcut_table.js';
import { calendarSummary, filterActive, notify, on, setFilter, setSelection, setView, state } from './store.js';
import { tagChip } from './theme.js';
import { attachTooltip } from './tooltip.js';

const refs = {};
let chipSignature = '';
const DRAG_THRESHOLD_PX = 5;

export function focusSearch() {
  if (!refs.search) return;
  refs.search.focus();
  refs.search.select();
}

function toggleInList(list, value) {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

function segButton(label, pressed, onclick, tip) {
  const b = h('button', { type: 'button', class: 'seg__btn', text: label, 'aria-pressed': String(pressed), onclick });
  if (tip) attachTooltip(b, tip);
  return b;
}

export function selectAllChips() { setFilter(selectAllTags(state.tagList)); }
export function selectNoChips() { setFilter(selectNoTags()); }

// ------------------------------------------------------------------ chip drag onto bars (R-115)

function makeDraggable(chip, tag) {
  chip.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    const x0 = e.clientX, y0 = e.clientY;
    let dragging = false;
    const drag = () => timelineHook('chipDrag');
    const move = (ev) => {
      if (!dragging && Math.hypot(ev.clientX - x0, ev.clientY - y0) < DRAG_THRESHOLD_PX) return;
      if (!dragging) {
        dragging = true;
        chip.classList.add('is-dragging');
        document.body.classList.add('is-chip-dragging');
      }
      drag()?.move(ev.clientX, ev.clientY, tag);
    };
    const finish = (ev, cancel) => {
      window.removeEventListener('pointermove', move, true);
      window.removeEventListener('pointerup', up, true);
      window.removeEventListener('keydown', esc, true);
      chip.classList.remove('is-dragging');
      document.body.classList.remove('is-chip-dragging');
      if (!dragging) return;
      chip.dataset.suppressClick = '1';
      setTimeout(() => delete chip.dataset.suppressClick, 0);
      if (cancel) drag()?.cancel();
      else drag()?.end(ev.clientX, ev.clientY, tag);
    };
    const up = (ev) => finish(ev, false);
    const esc = (ev) => { if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); finish(ev, true); } };
    window.addEventListener('pointermove', move, true);
    window.addEventListener('pointerup', up, true);
    window.addEventListener('keydown', esc, true);
  });
}

function renderChips() {
  const f = state.filter;
  const sig = JSON.stringify([state.tagList.map((t) => [t.id, t.name, t.slot, t.retired, t.path]), f.tags, f.untagged, f.tagMode]);
  if (sig === chipSignature) return;
  chipSignature = sig;
  // retired tags have no chip unless they are part of the active filter (R-30, R-113)
  const shown = state.tagList.filter((t) => !t.retired || f.tags.includes(t.id));
  const chips = shown.map((t) => {
    const isOn = f.tags.includes(t.id);
    const chip = tagChip(t.name, t.slot, {
      tag: 'button', extraClass: `filter-chip${isOn ? ' is-on' : ''}${t.retired ? ' is-retired' : ''}`,
      props: { type: 'button', 'aria-pressed': String(isOn), dataset: { tagId: t.id },
        onclick: () => { if (!chip.dataset.suppressClick) setFilter({ tags: toggleInList(state.filter.tags, t.id) }); } },
    });
    if (isOn) chip.prepend(h('span', { class: 'filter-chip__check', 'aria-hidden': 'true', text: '✓' }));
    if (t.retired) chip.append(h('span', { class: 'chip-retired', text: 'retired' }));
    else makeDraggable(chip, { id: t.id, name: t.name, slot: t.slot });
    const path = t.path && t.path !== t.name ? `\n${t.path}` : '';
    attachTooltip(chip, `${t.name}: ${t.usage} item${t.usage === 1 ? '' : 's'}${path}\nClick to filter. Drag onto an item to add this tag.`);
    return chip;
  });
  const untaggedDisabled = f.tagMode === 'and';
  const untagged = h('button', { type: 'button', class: `tag-chip filter-chip filter-chip--untagged${f.untagged ? ' is-on' : ''}`,
    'aria-pressed': String(!!f.untagged), 'aria-disabled': String(untaggedDisabled), dataset: { tagId: UNTAGGED_CHIP },
    onclick: () => { if (!untaggedDisabled) setFilter({ untagged: !state.filter.untagged }); } },
  f.untagged ? h('span', { class: 'filter-chip__check', 'aria-hidden': 'true', text: '✓' }) : null,
  h('span', { class: 'tag-chip__label', text: 'Untagged' }));
  attachTooltip(untagged, untaggedDisabled ? 'Untagged items (not available in AND mode)' : 'Items with no tags');
  refs.chips.replaceChildren(...chips, untagged);
}

function renderSegments() {
  const f = state.filter;
  const mode = f.tagMode === 'and' ? 'and' : 'or';
  refs.mode.replaceChildren(
    segButton('OR', mode === 'or', () => setFilter({ tagMode: 'or' }), 'Items with any selected tag'),
    segButton('AND', mode === 'and', () => setFilter({ tagMode: 'and' }), 'Items with all selected tags'));
  refs.kind.replaceChildren(
    segButton('Plan', f.kind.includes('plan'), () => setFilter({ kind: toggleInList(f.kind, 'plan') }), 'Show plan items'),
    segButton('Log', f.kind.includes('log'), () => setFilter({ kind: toggleInList(f.kind, 'log') }), 'Show log entries'));
  refs.origin.replaceChildren(
    segButton('Local', f.origin.includes('local'), () => setFilter({ origin: toggleInList(f.origin, 'local') }), 'Show items created here'),
    segButton('Outlook', f.origin.includes('outlook'), () => setFilter({ origin: toggleInList(f.origin, 'outlook') }), 'Show imported Outlook items'),
    segButton('Feed', f.origin.includes('ics'), () => setFilter({ origin: toggleInList(f.origin, 'ics') }), 'Show items from calendar files and feeds'));
}

// ------------------------------------------------------------------ calendars control (R-131)

function calendarEntries() {
  const list = Object.entries(state.calendars).filter(([, c]) => c.connected)
    .sort(([, a], [, b]) => (a.kind === b.kind ? a.name.localeCompare(b.name) : a.kind === 'outlook' ? -1 : 1));
  const entries = list.map(([id, c]) => ({
    label: `${c.name}  (${c.item_count ?? 0})`, checked: !!c.visible, keepOpen: true,
    hint: c.kind === 'ics' ? 'feed' : '', onSelect: () => actions.setCalendarVisible(id, !c.visible),
  }));
  if (!entries.length) return [{ label: 'No calendars connected. Add them in Settings.', disabled: true }];
  return [
    { label: 'Show all', onSelect: () => actions.setCalendarVisible('*', true) },
    { label: 'Hide all', onSelect: () => actions.setCalendarVisible('*', false) },
    { separator: true }, ...entries,
    { separator: true }, { label: 'Calendar settings…', onSelect: () => commands.openSettings() },
  ];
}

export function openCalendarsMenu(anchor = refs.calendars) {
  const r = anchor.getBoundingClientRect();
  showContextMenu(r.left, r.bottom + 2, calendarEntries(), { returnFocus: anchor });
}

function renderCalendars() {
  const { connected, visible } = calendarSummary();
  const text = connected && visible < connected ? `Calendars: ${visible} of ${connected} shown` : 'Calendars';
  refs.calendars.querySelector('.tb-btn__text').textContent = text;
  refs.calendars.classList.toggle('is-partial', connected > 0 && visible < connected);
}

function renderState() {
  const active = filterActive();
  refs.root.classList.toggle('is-active', active);
  refs.root.dataset.active = String(active);
  refs.clear.setAttribute('aria-disabled', String(!active));
  refs.clear.classList.toggle('is-disabled', !active);
  refs.badge.hidden = !active;
  if (active) {
    const n = state.items.filter((it) => matches(it, state.filter, state.tags)).length;
    refs.badge.textContent = `${n} of ${state.items.length} match`;
  }
  if (refs.search.value !== (state.filter.text || '') && !refs.search.dataset.pending) refs.search.value = state.filter.text || '';
  renderChips();
  renderSegments();
  renderCalendars();
}

// ------------------------------------------------------------------ search navigation (R-33)

function jump(dir) {
  commit.flush(refs.search.value);
  const { visible, hiddenCalendars } = searchMatches(state.items, state.filter, state.tags, state.calendars);
  if (!state.filter.text.trim()) return;
  const tail = hiddenCalendars ? `, ${hiddenCalendars} in hidden calendars` : '';
  if (!visible.length) {
    notify(hiddenCalendars ? `No visible matches. ${hiddenCalendars} in hidden calendars.` : 'No matches', { kind: 'warn', ttl: 4000 });
    return;
  }
  const cur = visible.findIndex((i) => i.id === state.primaryId);
  const next = cur < 0 ? (dir > 0 ? 0 : visible.length - 1) : (cur + dir + visible.length) % visible.length;
  const it = visible[next];
  const mid = (toDayNumber(it.start_date) + toDayNumber(it.end_date) + 1) / 2;
  const span = state.view.end - state.view.start;
  setView(mid - span / 2, mid + span / 2, 'search');
  setSelection([it.id], it.id);
  notify(`Match ${next + 1} of ${visible.length}${tail}`, { ttl: 4000 });
}

const commit = debounce((text) => {
  delete refs.search.dataset.pending;
  if (text !== state.filter.text) setFilter({ text });
  checkMonolith(text);
}, 120);

function onSearchKey(e) {
  if (e.key === 'Enter') { e.preventDefault(); jump(e.shiftKey ? -1 : 1); }
  else if (e.key === 'Escape') {
    e.preventDefault();
    if (refs.search.value) { refs.search.value = ''; commit.flush(''); }
    else document.querySelector('#timeline-host canvas')?.focus();
  }
}

export function initFilterBar(el) {
  refs.root = el;
  refs.search = h('input', { type: 'text', class: 'input filter-bar__input', placeholder: 'Search title, notes, tags',
    'aria-label': 'Search items', autocomplete: 'off', spellcheck: false });
  refs.search.addEventListener('input', () => { refs.search.dataset.pending = '1'; commit(refs.search.value); });
  refs.search.addEventListener('keydown', onSearchKey);
  attachTooltip(refs.search, () => `${withShortcut('Search', 'search')}\nEnter / Shift+Enter: next / previous match`);

  refs.chips = h('div', { class: 'filter-bar__chips', role: 'group', 'aria-label': 'Filter by tag' });
  const all = h('button', { type: 'button', class: 'btn btn--small btn--ghost filter-bar__all', text: 'All', onclick: selectAllChips });
  const none = h('button', { type: 'button', class: 'btn btn--small btn--ghost filter-bar__none-btn', text: 'None', onclick: selectNoChips });
  attachTooltip(all, `${withShortcut('Select every tag', 'selectAllChips')}\nThen deselect the few to leave out. In OR mode an item stays visible while any of its tags is still selected.`);
  attachTooltip(none, withShortcut('Deselect every tag (no tag filter)', 'selectNoChips'));
  refs.mode = h('div', { class: 'seg', role: 'group', 'aria-label': 'Tag match mode' });
  refs.kind = h('div', { class: 'seg', role: 'group', 'aria-label': 'Kind' });
  refs.origin = h('div', { class: 'seg', role: 'group', 'aria-label': 'Origin' });
  refs.calendars = h('button', { type: 'button', class: 'btn btn--small btn--ghost filter-bar__calendars', 'aria-haspopup': 'menu',
    onclick: () => openCalendarsMenu() }, icon('calendar', 14), h('span', { class: 'tb-btn__text', text: 'Calendars' }), icon('menu', 12));
  attachTooltip(refs.calendars, 'Show or hide each connected calendar. Hidden calendars are still imported.');
  refs.badge = h('span', { class: 'filter-bar__badge', role: 'status', hidden: true });
  refs.clear = h('button', { type: 'button', class: 'btn btn--ghost filter-bar__clear', 'aria-label': 'Clear filter',
    onclick: () => { if (filterActive()) commands.clearFilter(); } }, icon('close', 14), h('span', { text: 'Clear' }));
  attachTooltip(refs.clear, withShortcut('Clear filter', 'clearFilter'));

  el.replaceChildren(
    h('div', { class: 'filter-bar__tags' }, h('span', { class: 'filter-bar__label', text: 'Tags' }), all, none, refs.chips, refs.mode),
    h('div', { class: 'filter-bar__search' }, icon('search', 14), refs.search),
    refs.kind, refs.origin, refs.calendars, refs.badge, refs.clear);
  on('layout', renderState);
  on('items', renderState);
  renderState();
}
