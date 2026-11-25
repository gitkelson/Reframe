// Day tab (spec 6.6, R-52): items overlapping one day grouped Log / Plan, plus an inline "Add log entry" row.
import * as actions from '../actions.js';
import { DATE_HINT, formatLong, fromDayNumber, parseTypedDate, toDayNumber, weekday, WEEKDAYS } from '../dates.js';
import { h } from '../dom.js';
import { icon } from '../icons.js';
import { on, setPanel, setSelection, setView, state } from '../store.js';
import { tagChip } from '../theme.js';
import { btn, field } from './ui.js';

export function createDayTab() {
  const root = h('div', { class: 'tab tab--day' });
  const heading = h('h2', { class: 'day-title' });
  const sub = h('div', { class: 'day-sub mono' });
  const dateIn = h('input', { type: 'text', class: 'input mono day-date', placeholder: 'YYYY-MM-DD', autocomplete: 'off' });
  const dateField = field('Go to day', dateIn, { id: 'day-date' });
  const list = h('div', { class: 'day-list' });
  const addIn = h('input', { type: 'text', class: 'input day-add-input', placeholder: 'What did you do? Enter to add', 'aria-label': 'Add log entry', autocomplete: 'off' });
  const addMsg = h('div', { class: 'field__msg', role: 'status' });

  const dayISO = () => state.panel.dayISO || state.todayISO;

  function gotoDay(iso) {
    const n = toDayNumber(iso);
    if (n < state.view.start || n >= state.view.end) {
      const span = state.view.end - state.view.start;
      setView(n - span / 2, n + span / 2, 'day-panel');
    }
    setPanel({ dayISO: iso });
  }
  const step = (d) => gotoDay(fromDayNumber(toDayNumber(dayISO()) + d));

  dateIn.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const iso = parseTypedDate(dateIn.value);
    if (!iso) { dateField.msg(DATE_HINT); return; }
    dateField.msg('');
    gotoDay(iso);
  });
  dateIn.addEventListener('input', () => dateField.msg(''));

  async function addEntry() {
    const title = addIn.value.trim();
    if (!title) return;
    const iso = dayISO();
    addIn.value = '';
    const id = await actions.createItem({ title, start: iso, end: iso, kind: 'log' });
    if (id) {
      setSelection([]);                       // stay on the day tab so the next entry can be typed straight away
      setPanel({ open: true, tab: 'day', dayISO: iso });
      addMsg.textContent = `Added “${title}”`;
      addIn.focus();
    }
  }
  addIn.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); addEntry(); } });

  root.append(
    h('div', { class: 'day-nav' },
      h('button', { type: 'button', class: 'btn btn--ghost btn--icon', 'aria-label': 'Previous day', onclick: () => step(-1) }, icon('left', 16)),
      heading,
      h('button', { type: 'button', class: 'btn btn--ghost btn--icon', 'aria-label': 'Next day', onclick: () => step(1) }, icon('right', 16))),
    sub,
    h('div', { class: 'day-go' }, dateField.el, btn('Today', { onclick: () => gotoDay(state.todayISO) })),
    h('div', { class: 'day-add' },
      h('label', { class: 'field__label', for: 'day-add-input', text: 'Add log entry' }),
      h('div', { class: 'add-row' }, addIn, btn('Add', { variant: 'primary', onclick: addEntry })), addMsg),
    list);
  addIn.id = 'day-add-input';

  function row(it) {
    const input = h('input', { type: 'text', class: 'input day-row__title', value: it.title, readOnly: !!it.locked, 'aria-label': `Title of ${it.title}`, spellcheck: false });
    const commit = () => {
      const v = input.value.trim();
      if (!v) { input.value = it.title; return; }
      if (v !== it.title && !it.locked) actions.patchItem(it.id, { title: v });
    };
    input.addEventListener('blur', commit);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
      else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); input.value = it.title; input.blur(); }
    });
    const range = it.start_date === it.end_date ? it.start_date : `${it.start_date} to ${it.end_date}`;
    return h('div', { class: `day-row kind-${it.kind}`, dataset: { id: it.id } },
      input,
      h('div', { class: 'day-row__meta' },
        h('span', { class: 'mono', text: range }),
        it.locked ? h('span', { class: 'badge', text: 'Outlook' }) : null,
        ...it.tags.slice(0, 3).filter((t) => state.tags[t]).map((t) => tagChip(state.tags[t].name, state.tags[t].slot, { extraClass: 'tag-chip--small' }))),
      btn('Select', { onclick: () => setSelection([it.id], it.id), ariaLabel: `Select ${it.title}` }));
  }

  function group(title, items) {
    return h('section', { class: 'psec', 'aria-label': title },
      h('h3', { class: 'psec__title', text: `${title} (${items.length})` }),
      items.length ? items.map(row) : h('p', { class: 'empty', text: `No ${title.toLowerCase()} items.` }));
  }

  function render() {
    if (root.hidden) return;
    const iso = dayISO();
    const n = toDayNumber(iso);
    heading.textContent = formatLong(n);
    sub.textContent = `${iso} · ${WEEKDAYS[weekday(n)]}`;
    if (document.activeElement !== dateIn) dateIn.value = iso;
    if (list.contains(document.activeElement)) return;
    const todays = state.items.filter((i) => i.start_date <= iso && iso <= i.end_date);
    const by = (k) => todays.filter((i) => i.kind === k).sort((a, b) => a.title.localeCompare(b.title));
    list.replaceChildren(group('Log', by('log')), group('Plan', by('plan')));
  }

  on('panel', render);
  on('items', render);
  return { el: root, activate: () => { addMsg.textContent = ''; render(); } };
}
