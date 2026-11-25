// Filtering and search over the item list (R-30..R-34, R-131). Pure: no DOM.

export const UNTAGGED_CHIP = '__untagged__';
export const EMPTY_FILTER = Object.freeze({ tags: [], tagMode: 'or', text: '', kind: [], origin: [], untagged: false });

export function isFilterActive(f) {
  return !!(f.tags.length || f.untagged || (f.text && f.text.trim()) || f.kind.length || f.origin.length);
}

/** Does `item` match the filter? `tagsById` maps tag id -> {name}. */
export function matches(item, f, tagsById) {
  if (f.kind.length && !f.kind.includes(item.kind)) return false;
  if (f.origin.length && !f.origin.includes(item.origin)) return false;
  if (f.tags.length || f.untagged) {
    const has = (id) => item.tags.includes(id);
    if (f.tagMode === 'and') {
      // the Untagged chip cannot match together with another chip in AND mode, so it is ignored there (R-34)
      if (f.tags.length && !f.tags.every(has)) return false;
      if (!f.tags.length && f.untagged && item.tags.length) return false;
    } else if (!(f.tags.some(has) || (f.untagged && !item.tags.length))) {
      return false;
    }
  }
  const q = (f.text || '').trim().toLowerCase();
  if (q) {
    const hay = [item.title, item.notes || '', ...item.tags.map((id) => tagsById[id]?.name || '')]
      .join('\n').toLowerCase();
    if (!q.split(/\s+/).every((w) => hay.includes(w))) return false;
  }
  return true;
}

/** Items of a hidden calendar source are excluded exactly like a filter; local items never are (R-131). */
export function inVisibleCalendar(item, calendars) {
  if (!item.source_id) return true;
  const c = calendars[item.source_id];
  return !c || !!c.visible;
}

/** Items that take part in layout: calendar visibility and filter applied. Returns items in the input order. */
export function layoutItems(items, f, tagsById, calendars = {}) {
  const active = isFilterActive(f);
  return items.filter((it) => inVisibleCalendar(it, calendars) && (!active || matches(it, f, tagsById)));
}

/** All (every non-retired tag plus Untagged) and None (no tag filter) (R-34). */
export function selectAllTags(tagList) {
  return { tags: tagList.filter((t) => !t.retired).map((t) => t.id), untagged: true };
}
export function selectNoTags() { return { tags: [], untagged: false }; }

/** Search matches in time order, split into visible ones and those in hidden calendars (R-33). */
export function searchMatches(items, f, tagsById, calendars = {}) {
  const q = (f.text || '').trim();
  if (!q) return { visible: [], hiddenCalendars: 0 };
  const all = items.filter((it) => matches(it, { ...f, text: q }, tagsById))
    .sort((a, b) => a.start_date.localeCompare(b.start_date) || a.end_date.localeCompare(b.end_date) || a.id.localeCompare(b.id));
  const visible = all.filter((it) => inVisibleCalendar(it, calendars));
  return { visible, hiddenCalendars: all.length - visible.length };
}
