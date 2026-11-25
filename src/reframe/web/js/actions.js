// Everything that talks to the server to change or reload data. UI modules call these instead of api.* so
// error handling, undo notices, refresh and selection stay consistent.
import { ApiError, api } from './api.js';
import { otherAction } from './easter_eggs.js';
import { emit, highlight, notify, setCalendars, setData, setSelection, state } from './store.js';

let localWriteAt = 0;
/** True for a short while after this window itself wrote, so SSE echoes do not highlight our own edits. */
export function recentlyWroteLocally() { return Date.now() - localWriteAt < 3000; }

export async function refreshItems() {
  const data = await api.items();
  setData(data.items, data.tags, data.calendars || {});
}

export async function refreshUndo() {
  state.undo = await api.undoState();
  emit('undo');
}

export async function refreshMeta() {
  const b = await api.bootstrap();
  state.hygiene = b.hygiene;
  state.impliedDrift = b.implied_drift;
  state.needsAttention = b.needs_attention;
  state.dbPath = b.db_path;
  state.settings = { ...state.settings, ...b.settings };
  emit('items');
}

export async function refreshAll() {
  await Promise.all([refreshItems(), refreshUndo()]);
}

function fail(e) {
  if (e instanceof ApiError) {
    notify(e.message, { kind: 'error' });
    return e;
  }
  console.error(e);
  notify('Something went wrong. See the console for details.', { kind: 'error' });
  return e;
}

/** Run a mutation, then refresh. Resolves to the mutation result, or null after showing an inline error. */
export async function mutate(fn, { undoNotice = null } = {}) {
  localWriteAt = Date.now();
  otherAction();
  try {
    const res = await fn();
    await refreshAll();
    if (undoNotice) notify(undoNotice, { undo: true });
    return res ?? true;
  } catch (e) {
    fail(e);
    try { await refreshAll(); } catch { /* offline */ }
    return null;
  }
}

// ------------------------------------------------------------------- items

export async function createItem({ title = 'New item', start, end, kind = 'plan', tags = [] }) {
  const res = await mutate(() => api.createItem({ title, start_date: start, end_date: end ?? start, kind, tags }));
  if (res && res.id) setSelection([res.id], res.id);
  return res && res.id;
}

/** Copy of an item starting on `startISO`, with every field but the title (R-49). Selects the copy and returns its id. */
export async function duplicateItem(sourceId, startISO) {
  const res = await mutate(() => api.duplicateItem(sourceId, startISO));
  if (res && res.id) setSelection([res.id], res.id);
  return res && res.id;
}

/** Update only the changed fields. Applies the change locally first so drags feel instant. */
export async function patchItem(id, fields) {
  const it = state.itemsById.get(id);
  if (it) {
    Object.assign(it, fields);
    emit('items');
  }
  return mutate(() => api.patchItem(id, fields));
}

export async function moveItems(ids, days) {
  if (!days) return null;
  const res = await mutate(() => api.bulk(ids, 'move', { days }));
  if (res && res.skipped) notify(`${res.skipped} imported item(s) skipped: change them in Outlook`, { kind: 'warn' });
  return res;
}

export async function shiftEdge(ids, which, days) {
  if (!days) return null;
  const res = await mutate(() => api.bulk(ids, `shift_${which}`, { days }));
  if (res && res.skipped) notify(`${res.skipped} imported item(s) skipped: change them in Outlook`, { kind: 'warn' });
  return res;
}

export async function deleteItems(ids) {
  if (!ids.length) return null;
  const res = await mutate(() => api.bulk(ids, 'delete'));
  if (res) {
    const skipped = res.skipped || 0;
    if (res.deleted) {
      notify(`Deleted ${res.deleted} item${res.deleted > 1 ? 's' : ''}${skipped ? `, ${skipped} imported skipped` : ''}`, { undo: true });
    } else {
      notify('Imported items cannot be deleted. Hide them instead.', { kind: 'warn' });
    }
  }
  return res;
}

export async function deleteSelection() { return deleteItems([...state.selection]); }

export async function setItemTags(id, names) { return mutate(() => api.setTags(id, names)); }

export async function bulkTags(ids, { add = [], remove = [] }) {
  if (add.length) await mutate(() => api.bulk(ids, 'add_tags', { tags: add }));
  if (remove.length) await mutate(() => api.bulk(ids, 'remove_tags', { tags: remove }));
}

export async function bulkKind(ids, kind) { return mutate(() => api.bulk(ids, 'set_kind', { kind })); }

/** Give every target all the tags of `sourceId` (R-48). One change set, one undo. */
export async function copyTags(sourceId, targetIds) {
  const targets = targetIds.filter((id) => id !== sourceId);
  if (!targets.length) { notify('Select the items that should receive the tags first'); return null; }
  const source = state.itemsById.get(sourceId);
  const res = await mutate(() => api.bulk(targets, 'copy_tags', { source_id: sourceId }));
  if (!res) return null;
  const to = targets.length === 1 ? `'${state.itemsById.get(targets[0])?.title ?? 'item'}'` : `${targets.length} items`;
  if (res.changed) notify(`Copied the tags of '${source?.title ?? 'item'}' to ${to}`, { undo: true });
  else notify(`${to} already ${targets.length === 1 ? 'has' : 'have'} those tags`);
  return res;
}

/** Filter chip dropped on bars (R-115): appended after the item's tags; one change set, one undo. */
export async function addTagToItems(ids, name) {
  if (!ids.length) return null;
  const res = await mutate(() => api.bulk(ids, 'add_tag', { tag: name }));
  if (!res) return null;
  const what = ids.length === 1 ? `'${state.itemsById.get(ids[0])?.title ?? 'item'}'` : `${ids.length} items`;
  if (res.added) notify(`Added tag '${name}' to ${what}`, { undo: true });
  else notify('Already tagged');
  return res;
}

export async function hideItem(id, series = false) {
  const it = state.itemsById.get(id);
  const res = await mutate(() => api.hideItem(id, series));
  if (res) notify(series ? `Hid the whole series of '${it?.title ?? ''}'` : `Hid '${it?.title ?? ''}'`, { undo: true });
  return res;
}

export async function addAttachment(id, target, label) { return mutate(() => api.addAttachment(id, target, label)); }
export async function addIndexedFileAttachment(id, fileEntryId) {
  return mutate(() => api.addIndexedFileAttachment(id, fileEntryId), { undoNotice: 'File attached' });
}
export async function removeAttachment(attId) { return mutate(() => api.removeAttachment(attId)); }

// ----------------------------------------------------------- undo / history

export async function undoLast() {
  localWriteAt = Date.now();
  try {
    const r = await api.undo();
    notify(r.ok ? `Undid: ${r.undone_label}` : (r.message || 'Nothing to undo'), { ttl: 5000 });
    await refreshAll();
    return r.ok;
  } catch (e) { fail(e); return false; }
}

export async function redoLast() {
  localWriteAt = Date.now();
  try {
    const r = await api.redo();
    notify(r.ok ? `Redid: ${r.undone_label}` : (r.message || 'Nothing to redo'), { ttl: 5000 });
    await refreshAll();
    return r.ok;
  } catch (e) { fail(e); return false; }
}

/** Revert one change set from the History panel. Resolves to {ok, conflicts?, message?}. */
export async function revertChangeSet(id) {
  localWriteAt = Date.now();
  try {
    const r = await api.revert(id);
    notify(r.label || 'Reverted', { ttl: 5000 });
    await refreshAll();
    return { ok: true };
  } catch (e) {
    if (e instanceof ApiError && e.kind === 'conflict') return { ok: false, conflicts: e.data.conflicts, message: e.message };
    fail(e);
    return { ok: false, message: e.message };
  }
}

// ------------------------------------------------------------ sync / backup

export async function syncNow(kinds) {
  try {
    const r = await api.sync(kinds);
    if (!r.started) notify('A sync is already running');
  } catch (e) { fail(e); }
}

/** Calendar sources changed (visibility or connection): reload them; visibility repacks like a filter (R-131). */
export async function refreshCalendars() {
  const r = await api.calendars();
  const map = {};
  for (const c of r.sources) map[c.id] = c;
  setCalendars(map);
  return r;
}

export async function setCalendarVisible(id, visible) {
  try {
    if (id === '*') await api.setAllCalendarsVisible(visible);
    else await api.patchCalendar(id, { visible });
    await refreshCalendars();
  } catch (e) { fail(e); }
}

/** Handle a 'sync' SSE event or a status poll result. */
export function applySyncStatus(s) {
  const wasRunning = state.sync.running;
  state.sync = { ...state.sync, ...s };
  emit('sync');
  if (wasRunning && !s.running) {
    if (s.error && !s.last_result) {
      notify(`Sync did not run: ${s.error}`, { kind: 'warn', ttl: 0 });
    } else if (s.last_result) {
      const r = s.last_result;
      const extra = r.warnings && r.warnings.length ? ` (${r.warnings.join('; ')})` : '';
      const err = s.error ? ` ? ${s.error}` : '';
      notify(`${r.message}${extra}${err}`, { undo: r.change_set_id != null, kind: r.status === 'ok' && !s.error ? 'info' : 'warn' });
    }
    refreshAll();
    refreshCalendars().catch(() => {});
  }
}

export async function saveSettings(values) {
  try {
    await api.putSettings(values);
    state.settings = { ...state.settings, ...values };
    emit('items');
  } catch (e) { fail(e); }
}

// ---------------------------------------------------- external changes (R-85)

export async function onExternalData(ev) {
  await refreshAll();
  await refreshMeta().catch(() => {});
  const ids = (ev && ev.item_ids) || [];
  if (ids.length && (ev.external || !recentlyWroteLocally())) highlight(ids);
}
