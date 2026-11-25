// Inline expanding calendar grid (spec 6.5): lives inside the panel, never a popup.
import { MONTHS, WEEKDAYS_SHORT, dayNumberOf, formatLong, fromDayNumber, parts, toDayNumber, weekday } from '../dates.js';
import { h } from '../dom.js';
import { icon } from '../icons.js';
import { state } from '../store.js';

/** createCalendar({onPick(iso)}) -> {el, open(iso, rangeStartISO, rangeEndISO), close(), isOpen} */
export function createCalendar({ onPick }) {
  let year = 2026, month = 1, rangeStart = null, rangeEnd = null, selected = null;
  const title = h('div', { class: 'cal__title', role: 'heading', 'aria-level': '4', 'aria-live': 'polite' });
  const grid = h('div', { class: 'cal__grid', role: 'grid' });
  const step = (d) => { const t = year * 12 + month - 1 + d; year = Math.floor(t / 12); month = (t % 12) + 1; render(); };
  const el = h('div', { class: 'cal', hidden: true, role: 'group', 'aria-label': 'Calendar' },
    h('div', { class: 'cal__head' },
      h('button', { type: 'button', class: 'btn btn--ghost btn--icon', 'aria-label': 'Previous month', onclick: () => step(-1) }, icon('left', 14)),
      title,
      h('button', { type: 'button', class: 'btn btn--ghost btn--icon', 'aria-label': 'Next month', onclick: () => step(1) }, icon('right', 14))),
    grid);

  function render() {
    title.textContent = `${MONTHS[month - 1]} ${year}`;
    const first = dayNumberOf(year, month, 1);
    const start = first - weekday(first);
    const cells = WEEKDAYS_SHORT.map((d) => h('div', { class: 'cal__dow', role: 'columnheader', text: d.slice(0, 2) }));
    for (let i = 0; i < 42; i++) {
      const n = start + i;
      const p = parts(n);
      const inRange = rangeStart !== null && n >= rangeStart && n <= rangeEnd;
      const cls = ['cal__day'];
      if (p.month !== month) cls.push('is-other');
      if (n === state.today) cls.push('is-today');
      if (inRange) cls.push('is-range');
      if (n === selected) cls.push('is-selected');
      cells.push(h('button', { type: 'button', class: cls.join(' '), 'aria-label': formatLong(n), 'aria-pressed': String(n === selected),
        text: String(p.day), onclick: () => onPick(fromDayNumber(n)) }));
    }
    grid.replaceChildren(...cells);
  }

  return {
    el,
    get isOpen() { return !el.hidden; },
    open(iso, rs, re) {
      const n = toDayNumber(iso);
      const p = parts(n);
      year = p.year; month = p.month;
      selected = n;
      rangeStart = rs ? toDayNumber(rs) : null;
      rangeEnd = re ? toDayNumber(re) : null;
      el.hidden = false;
      render();
    },
    close() { el.hidden = true; },
  };
}
