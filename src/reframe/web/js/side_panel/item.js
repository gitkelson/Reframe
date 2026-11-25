// Item tab (spec 6.5): inline-editable fields for the selected item, attachments, Outlook details, history.
import { ApiError, api } from '../api.js';
import * as actions from '../actions.js';
import { DATE_HINT, durationDays, parseTypedDate, toDayNumber } from '../dates.js';
import { debounce, h, plural } from '../dom.js';
import { FILE_DRAG_TYPE, isFileDrag } from '../file_timeline/drag.js';
import { icon } from '../icons.js';
import { attachmentName } from '../names.js';
import { createPathBar } from '../path_bar.js';
import { notify, on, state } from '../store.js';
import { lockedMessage } from '../timeline_input.js';
import { attachTooltip } from '../tooltip.js';
import { firstRunHint } from './first_run.js';
import { createCalendar } from './calendar.js';
import { createTagEditor } from './tag_editor.js';
import { actorBadge, banner, btn, conflictList, field, section, shortTime } from './ui.js';

const keyOf = (s) => s.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim();

export function createItemTab() {
  const root = h('div', { class: 'tab tab--item' });
  const body = h('div', { class: 'item-body', hidden: true });
  const empty = h('div', { class: 'item-empty' });
  root.append(empty, body);

  let id = null;
  let detail = null;
  let historyOpen = false;
  let calFor = null;
  const linkStatus = new Map();

  // ---- fields
  const titleIn = h('input', { type: 'text', class: 'input field-title', autocomplete: 'off', spellcheck: false });
  const titleField = field('Title', titleIn, { id: 'item-title' });
  const startIn = h('input', { type: 'text', class: 'input field-start mono', placeholder: 'YYYY-MM-DD', autocomplete: 'off' });
  const endIn = h('input', { type: 'text', class: 'input field-end mono', placeholder: 'YYYY-MM-DD', autocomplete: 'off' });
  const startField = field('Start', startIn, { id: 'item-start' });
  const endField = field('End', endIn, { id: 'item-end' });
  const startCal = h('button', { type: 'button', class: 'btn btn--ghost btn--icon', 'aria-label': 'Pick start date', 'aria-expanded': 'false', onclick: () => toggleCal('start') }, icon('calendar', 14));
  const endCal = h('button', { type: 'button', class: 'btn btn--ghost btn--icon', 'aria-label': 'Pick end date', 'aria-expanded': 'false', onclick: () => toggleCal('end') }, icon('calendar', 14));
  const duration = h('div', { class: 'item-duration mono', 'aria-live': 'polite' });
  const calendar = createCalendar({ onPick: (iso) => pickDate(iso) });
  const lockHint = banner(lockedMessage(null), 'info');
  lockHint.classList.add('item-lock');
  const kindPlan = h('button', { type: 'button', class: 'seg__btn', text: 'Plan', onclick: () => setKind('plan') });
  const kindLog = h('button', { type: 'button', class: 'seg__btn', text: 'Log', onclick: () => setKind('log') });
  const kindSeg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Kind' }, kindPlan, kindLog);
  const notesIn = h('textarea', { class: 'input field-notes', rows: 6, spellcheck: true });
  const notesField = field('Notes', notesIn, { id: 'item-notes' });
  const seriesNote = h('p', { class: 'series-note', hidden: true });
  const tagEditor = createTagEditor({ onAdd: (n) => setNames([...names(), n]), onRemove: removeTag, onReorder: setNames });
  const attList = h('ul', { class: 'att-list' });
  const attMsg = h('div', { class: 'field__msg', role: 'status' });
  const pathBar = createPathBar({ placeholder: 'Search files, or paste a path or URL', onAdd: async (path) => !!(await addLink(path)) });
  const linkIn = pathBar.input;
  linkIn.classList.add('field-link');
  const outlookBox = h('dl', { class: 'kv item-outlook' });
  const hideRow = h('div', { class: 'row-actions' });
  const dupNote = h('p', { class: 'series-note', hidden: true });
  const histBody = h('div', { class: 'item-history', hidden: true });
  const histToggle = h('button', { type: 'button', class: 'btn btn--ghost btn--small', 'aria-expanded': 'false', onclick: toggleHistory },
    icon('restore', 14), h('span', { text: 'Show history for this item' }));

  const dateRow = h('div', { class: 'date-row' },
    h('div', { class: 'date-cell' }, startField.el, startCal),
    h('div', { class: 'date-cell' }, endField.el, endCal));
  const addRow = h('div', { class: 'add-row' }, pathBar.el,
    state.hasHost ? btn('Choose file…', { iconName: 'folder', onclick: chooseFile }) : null);

  body.append(
    lockHint,
    titleField.el,
    h('div', { class: 'field' }, h('span', { class: 'field__label', text: 'Kind' }), kindSeg),
    section('Dates', dateRow, duration, calendar.el),
    section('Tags', tagEditor.el, seriesNote),
    notesField.el,
    section('Attachments', attList, attMsg, addRow,
      h('p', { class: 'field__hint', text: 'Drop a file or URL anywhere on this panel to add a link. Attached files are protected by keeping a copy of each version while Reframe runs; edits made while Reframe is not running, or overwritten before capture, may not be preserved.' })),
    section('Calendar source', outlookBox, dupNote, hideRow),
    section('History', histToggle, histBody));

  // ---- helpers
  const item = () => state.itemsById.get(id);
  const names = () => (item()?.tags || []).map((t) => state.tags[t]?.name).filter(Boolean);
  const slotOf = (name) => state.tagList.find((t) => keyOf(t.name) === keyOf(name))?.slot ?? 0;
  const detailTag = (name) => (detail?.tags || []).find((t) => keyOf(t.name) === keyOf(name));
  const tagObjs = (list) => list.map((n) => {
    const d = detailTag(n);
    const t = state.tagList.find((x) => keyOf(x.name) === keyOf(n));
    return { name: n, slot: slotOf(n), implied: !!d?.implied, impliedBy: d?.implied_by, retired: !!(t?.retired) };
  });

  function setNames(list) {
    tagEditor.setTags(tagObjs(list));
    actions.setItemTags(id, list).then(() => loadDetail());
  }

  /** An implied group tag cannot go while the tag that put it there remains (R-111). */
  function removeTag(name) {
    const d = detailTag(name);
    if (d?.implied && d.implied_by && names().some((n) => keyOf(n) === keyOf(d.implied_by))) {
      tagEditor.showHint(`Remove ${d.implied_by} first, or move it out of the group.`);
      return;
    }
    setNames(names().filter((x) => x !== name));
  }

  function setKind(kind) {
    const it = item();
    if (it && !it.locked && it.kind !== kind) actions.patchItem(id, { kind });
  }

  // ---- title / dates / notes commits
  titleIn.addEventListener('blur', () => {
    const it = item();
    const v = titleIn.value.trim();
    if (!it || it.locked) return;
    if (!v) { titleIn.value = it.title; titleField.msg('A title is required'); return; }
    titleField.msg('');
    if (v !== it.title) actions.patchItem(id, { title: v });
  });
  titleIn.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); titleIn.blur(); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); titleIn.value = item()?.title ?? ''; titleIn.blur(); }
  });

  function commitDate(which) {
    const it = item();
    const input = which === 'start' ? startIn : endIn;
    const f = which === 'start' ? startField : endField;
    if (!it || it.locked) return;
    const iso = parseTypedDate(input.value);
    if (!iso) { input.dataset.invalid = '1'; f.msg(DATE_HINT); return; }
    delete input.dataset.invalid;
    f.msg('');
    if (iso === it[`${which}_date`]) { input.value = iso; return; }
    if (which === 'end' && iso < it.start_date) { input.dataset.invalid = '1'; f.msg('The end date is before the start date'); return; }
    const patch = { [`${which}_date`]: iso };
    if (which === 'start' && iso > it.end_date) patch.end_date = iso;
    input.value = iso;
    actions.patchItem(id, patch);
  }
  for (const [input, which] of [[startIn, 'start'], [endIn, 'end']]) {
    input.addEventListener('blur', () => commitDate(which));
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); commitDate(which); }
      else if (e.key === 'Escape') {
        e.preventDefault(); e.stopPropagation();
        delete input.dataset.invalid;
        (which === 'start' ? startField : endField).msg('');
        input.value = item()?.[`${which}_date`] ?? '';
      }
    });
  }

  function toggleCal(which) {
    if (calFor === which && calendar.isOpen) { closeCal(); return; }
    const it = item();
    if (!it || it.locked) return;
    calFor = which;
    calendar.open(it[`${which}_date`], it.start_date, it.end_date);
    startCal.setAttribute('aria-expanded', String(which === 'start'));
    endCal.setAttribute('aria-expanded', String(which === 'end'));
  }
  function closeCal() {
    calendar.close(); calFor = null;
    startCal.setAttribute('aria-expanded', 'false'); endCal.setAttribute('aria-expanded', 'false');
  }
  function pickDate(iso) {
    const input = calFor === 'start' ? startIn : endIn;
    input.value = iso;
    const which = calFor;
    commitDate(which);
    const it = item();
    if (it && !input.dataset.invalid) calendar.open(iso, which === 'start' ? iso : it.start_date, which === 'end' ? iso : (iso > it.end_date ? iso : it.end_date));
  }

  let notesDirty = false;
  const commitNotes = debounce(() => {
    const it = item();
    if (!it) return;
    notesDirty = false;
    if (notesIn.value !== (it.notes || '')) actions.patchItem(id, { notes: notesIn.value });
  }, 800);
  notesIn.addEventListener('input', () => { notesDirty = true; commitNotes(); });
  notesIn.addEventListener('blur', () => { if (notesDirty) commitNotes.flush(); });

  // ---- attachments (R-71, R-72)
  async function addLink(path) {
    const target = (path ?? linkIn.value).trim();
    if (!target) return null;
    attMsg.textContent = '';
    const res = await actions.addAttachment(id, target);
    if (res) { linkIn.value = ''; loadDetail(); }
    return res;
  }

  async function chooseFile() {
    try {
      const r = await api.chooseFile();
      if (r.path) await actions.addAttachment(id, r.path);
    } catch (e) { attMsg.textContent = e.message; attMsg.dataset.kind = 'error'; }
  }

  const openVersions = new Set();
  const protection = new Map();

  async function openAttachment(att) {
    attMsg.replaceChildren();
    try {
      const r = await api.openAttachment(att.id);
      if (r && r.opened === 'saved') showSavedNotice(att, r);
    } catch (e) {
      attMsg.textContent = e instanceof ApiError ? e.message : 'Could not open the link';
      attMsg.dataset.kind = 'error';
      if (e.kind === 'broken') linkStatus.set(att.id, 'broken');
      renderAttachments();
    }
  }

  /** R-162: the pinned version was opened because the file changed or is missing. */
  function showSavedNotice(att, r) {
    const when = r.saved_at ? shortTime(r.saved_at) : 'when it was attached';
    attMsg.dataset.kind = 'warn';
    attMsg.replaceChildren(
      h('p', { text: r.notice || `Opened the saved version from ${when}. The file has changed since it was attached.` }),
      h('p', { class: 'field__hint', text: '"Current file" is the file at its location now; "latest saved version" is the newest copy Reframe captured.' }),
      h('div', { class: 'row-actions' },
        r.original_reachable ? btn('Open current file', { onclick: () => openVersion(att, 'current') }) : null,
        r.latest_differs ? btn('Open latest saved version', { onclick: () => openVersion(att, 'latest') }) : null,
        btn('Restore a version to\u2026', { onclick: () => { openVersions.add(att.id); renderAttachments(); } })));
  }

  async function openVersion(att, version) {
    try { await api.openVersion(att.id, version); } catch (e) { attMsg.textContent = e.message; attMsg.dataset.kind = 'error'; }
  }

  async function restoreVersion(att, v) {
    try {
      const r = await api.chooseSave(attachmentName(att.target, att.kind));
      if (!r.path) return;
      await api.restoreVersion(v.id, r.path);
      notify(`Restored the version from ${shortTime(v.captured_at)} to ${r.path}`);
    } catch (e) { attMsg.textContent = e.message; attMsg.dataset.kind = 'error'; }
  }

  const PROTECTION_TEXT = {
    protected: 'Protected', changed: 'Protected (newer version captured)', capturing: 'Capturing\u2026', pending: 'Capturing\u2026',
    missing: 'Not protected: file missing', locked: 'Not protected: file locked (will retry)',
    denied: 'Not protected: permission denied', unreachable: 'Not protected: network unavailable', failed: 'Not protected',
  };

  /** Protection state with an icon and text, never colour alone (R-160, spec 6.9). */
  function protectionBadge(a) {
    if (a.kind !== 'file') return h('span', { class: 'att__protect is-none', text: 'Not version-protected' });
    const p = protection.get(a.id);
    const status = a.pinned_version_id ? (p?.status === 'changed' ? 'changed' : 'protected')
      : (p?.state === 'capturing' ? 'capturing' : p?.status || 'pending');
    const cls = status === 'protected' || status === 'changed' ? 'is-ok' : status === 'pending' || status === 'capturing' ? 'is-pending' : 'is-bad';
    const el = h('span', { class: `att__protect ${cls}` }, h('span', { class: 'att__protect-mark', 'aria-hidden': 'true',
      text: cls === 'is-ok' ? '\u2713' : cls === 'is-pending' ? '\u2026' : '!' }), h('span', { text: PROTECTION_TEXT[status] || status }));
    const lines = [];
    if (p?.pinned_at) lines.push(`Pinned version: ${shortTime(p.pinned_at)}${a.pin_basis === 'migration' ? ' (captured at migration, not at attach time)' : ''}`);
    if (p?.last_check_at) lines.push(`Last check: ${shortTime(p.last_check_at)}`);
    if (p?.last_capture_at) lines.push(`Last successful capture: ${shortTime(p.last_capture_at)}`);
    if (p?.error) lines.push(p.error);
    if (p?.gap) lines.push(p.gap);
    if (lines.length) attachTooltip(el, lines.join('\n'));
    return el;
  }

  async function loadProtection() {
    const files = (detail?.attachments || []).filter((a) => a.kind === 'file');
    let changed = false;
    for (const a of files) {
      try {
        protection.set(a.id, await api.attachmentProtection(a.id));
        changed = true;
      } catch { /* vault not available */ }
    }
    if (changed) renderAttachments();
  }

  async function versionList(a) {
    const box = h('div', { class: 'att__versions', role: 'list', 'aria-label': 'Saved versions' },
      h('p', { class: 'empty', text: 'Loading versions\u2026' }));
    try {
      const vs = await api.attachmentVersions(a.id);
      box.replaceChildren(...(vs.versions?.length ? vs.versions.map((v) => h('div', { class: `att__version${v.is_pinned ? ' is-pinned' : ''}`, role: 'listitem' },
        h('span', { class: 'mono', text: shortTime(v.captured_at) }),
        h('span', { text: `${Math.max(1, Math.round(v.size / 1024))} KB` }),
        v.is_pinned ? h('span', { class: 'att__pin', text: 'pinned here' }) : null,
        btn('Open', { onclick: () => openVersion(a, v.id) }),
        state.hasHost ? btn('Restore to\u2026', { onclick: () => restoreVersion(a, v) }) : null))
        : [h('p', { class: 'empty', text: 'No saved versions yet.' })]));
    } catch (e) { box.replaceChildren(h('p', { class: 'field__msg', 'data-kind': 'error', text: e.message })); }
    return box;
  }

  function renderAttachments() {
    const list = detail?.attachments || [];
    attList.replaceChildren(...list.map((a) => {
      const st = linkStatus.get(a.id);
      const shown = a.label || attachmentName(a.target, a.kind);
      const text = h('div', { class: 'att__text' }, h('span', { class: 'att__label', text: shown }));
      attachTooltip(text, a.label ? `${a.label}\n${a.target}` : a.target);   // the full path lives in the tooltip
      const expanded = openVersions.has(a.id);
      const li = h('li', { class: 'att' },
        h('div', { class: 'att__row' },
          icon(a.kind === 'url' ? 'link' : 'folder', 14),
          text,
          st === 'broken' ? h('span', { class: 'att__broken', text: 'Broken link' }) : null,
          btn('Open', { iconName: 'open', onclick: () => openAttachment(a), ariaLabel: `Open ${shown}` }),
          a.kind === 'file' ? btn(expanded ? 'Hide versions' : 'Versions', { ariaLabel: `Versions of ${shown}`,
            onclick: () => { if (expanded) openVersions.delete(a.id); else openVersions.add(a.id); renderAttachments(); } }) : null,
          btn('Remove', { iconName: 'trash', onclick: () => actions.removeAttachment(a.id), ariaLabel: `Remove ${shown}` })),
        protectionBadge(a));
      if (expanded) versionList(a).then((box) => li.append(box));
      return li;
    }));
    if (!list.length) attList.append(h('li', { class: 'empty', text: 'No links yet.' }));
  }

  async function checkLinks() {
    const files = (detail?.attachments || []).filter((a) => a.kind === 'file').map((a) => a.id);
    if (!files.length) return;
    try {
      const r = await api.checkAttachments(files);
      let changed = false;
      for (const [aid, s] of Object.entries(r)) if (linkStatus.get(aid) !== s) { linkStatus.set(aid, s); changed = true; }
      if (changed) renderAttachments();
    } catch { /* never blocks the UI */ }
  }

  function dropTarget(dt) {
    const f = dt.files && dt.files[0];
    if (f) return { path: f.pywebviewFullPath || f.path || '', name: f.name };
    const uri = (dt.getData('text/uri-list') || dt.getData('text/plain') || '').split(/\r?\n/).find((l) => l && !l.startsWith('#'));
    return uri ? { path: uri.trim() } : null;
  }
  const isLinkDrag = (dt) => isFileDrag(dt) || (!!dt && ([...dt.types].includes('Files') || [...dt.types].includes('text/uri-list') ||
    ([...dt.types].includes('text/plain') && ![...dt.types].includes('text/x-reframe-tag'))));
  root.addEventListener('dragover', (e) => { if (id && isLinkDrag(e.dataTransfer)) { e.preventDefault(); root.classList.add('is-dropping'); } });
  root.addEventListener('dragleave', (e) => { if (!root.contains(e.relatedTarget)) root.classList.remove('is-dropping'); });
  root.addEventListener('drop', async (e) => {
    root.classList.remove('is-dropping');
    if (!id || !isLinkDrag(e.dataTransfer)) return;
    e.preventDefault();
    if (isFileDrag(e.dataTransfer)) {
      const fileId = e.dataTransfer.getData(FILE_DRAG_TYPE);
      if (!fileId) { attMsg.textContent = 'Could not read the dragged file.'; attMsg.dataset.kind = 'error'; return; }
      attMsg.textContent = '';
      await actions.addIndexedFileAttachment(id, fileId);
      return;
    }
    const t = dropTarget(e.dataTransfer);
    if (!t || !t.path) {
      attMsg.textContent = `Could not read the path of “${t?.name ?? 'that file'}”. Paste the path into the Add link field instead.`;
      attMsg.dataset.kind = 'error';
      return;
    }
    attMsg.textContent = '';
    await actions.addAttachment(id, t.path);
  });

  // ---- history for this item (R-82)
  function toggleHistory() {
    historyOpen = !historyOpen;
    histToggle.setAttribute('aria-expanded', String(historyOpen));
    histToggle.lastChild.textContent = historyOpen ? 'Hide history for this item' : 'Show history for this item';
    histBody.hidden = !historyOpen;
    if (historyOpen) loadHistory();
  }

  async function loadHistory() {
    if (!historyOpen || !id) return;
    let rows;
    try { rows = await api.itemHistory(id); } catch { return; }
    const sets = new Map();
    for (const r of rows) {
      if (!sets.has(r.change_set_id)) sets.set(r.change_set_id, { ...r, count: 0 });
      sets.get(r.change_set_id).count++;
    }
    histBody.replaceChildren(...(sets.size ? [...sets.values()].map(historyRow) : [h('p', { class: 'empty', text: 'No changes recorded.' })]));
  }

  function historyRow(s) {
    const slot = h('div', {});
    const row = h('div', { class: 'hist-row' },
      actorBadge(s.actor), h('span', { class: 'hist-row__label', text: s.label || '(no label)' }),
      h('span', { class: 'hist-row__time mono', text: shortTime(s.ts) }),
      btn('Revert', { iconName: 'restore', ariaLabel: `Revert ${s.label}`, onclick: async () => {
        const r = await actions.revertChangeSet(s.change_set_id);
        slot.replaceChildren(r.ok || !r.conflicts ? '' : conflictList(r.conflicts));
        if (!r.ok && !r.conflicts && r.message) slot.replaceChildren(h('p', { class: 'field__msg', 'data-kind': 'error', text: r.message }));
      } }));
    return h('div', { class: 'hist-item' }, row, slot);
  }

  // ---- load / sync
  function syncFields() {
    const it = item();
    if (!it) return;
    const lock = !!it.locked;
    const set = (input, value, invalid) => { if (document.activeElement !== input && !invalid) input.value = value; input.readOnly = lock; input.setAttribute('aria-readonly', String(lock)); };
    set(titleIn, it.title);
    set(startIn, it.start_date, startIn.dataset.invalid);
    set(endIn, it.end_date, endIn.dataset.invalid);
    if (document.activeElement !== notesIn && !notesDirty) notesIn.value = it.notes || '';
    kindPlan.setAttribute('aria-pressed', String(it.kind === 'plan'));
    kindLog.setAttribute('aria-pressed', String(it.kind === 'log'));
    kindPlan.disabled = kindLog.disabled = lock;
    startCal.disabled = endCal.disabled = lock;
    if (lock) closeCal();
    lockHint.hidden = !lock;
    if (lock) lockHint.querySelector('.banner__text').textContent = lockedMessage(it);
    root.classList.toggle('is-locked', lock);
    const n = durationDays(toDayNumber(it.start_date), toDayNumber(it.end_date));
    duration.textContent = `Duration: ${plural(n, 'day')}`;
    tagEditor.setTags(tagObjs(names()));
    renderOutlook(it);
  }

  function renderOutlook(it) {
    const imported = it.origin !== 'local';
    outlookBox.parentElement.hidden = !imported;
    seriesNote.hidden = !(imported && it.outlook_series_id);
    seriesNote.textContent = imported && it.outlook_series_id
      ? `Applies to every occurrence of this series${detail?.series_size > 1 ? ` (${detail.series_size} items)` : ''}.` : '';
    if (!imported) return;
    const rows = [['Time', it.outlook_time], ['Location', it.outlook_location],
      ['Calendar', detail?.source ? `${detail.source.name}${detail.source.location ? ` (${detail.source.location})` : ''}` : (state.calendars[it.source_id]?.name || '')],
      ['Source', it.origin === 'ics' ? 'Calendar file or feed' : 'Classic Outlook'],
      ['Series', detail?.series_size > 1 ? `${detail.series_size} occurrences` : it.outlook_series_id ? 'Recurring' : 'Single meeting']];
    outlookBox.replaceChildren(...rows.filter(([, v]) => v).flatMap(([k, v]) => [h('dt', { text: k }), h('dd', { text: String(v) })]));
    const dupes = detail?.identical_in_calendar || 0;
    dupNote.hidden = !dupes;
    dupNote.textContent = dupes
      ? `This calendar has ${plural(dupes, 'other identical entry', 'other identical entries')} (same title and dates). Use “Hide this item” on the extras.` : '';
    hideRow.replaceChildren(
      btn('Hide this item', { iconName: 'eyeOff', onclick: () => actions.hideItem(id, false) }),
      it.outlook_series_id ? btn('Hide whole series', { iconName: 'eyeOff', onclick: () => actions.hideItem(id, true) }) : null);
  }

  async function loadDetail() {
    const mine = id;
    try { detail = await api.item(mine); } catch { return; }
    if (mine !== id) return;
    renderAttachments();
    const it = item();
    if (it) { renderOutlook(it); tagEditor.setTags(tagObjs(names())); }
    checkLinks();
    loadProtection();
    loadHistory();
  }
  const reloadDetail = debounce(loadDetail, 150);

  function renderEmpty() {
    const hint = firstRunHint();
    empty.replaceChildren(
      h('p', { class: 'empty', text: state.items.length ? 'Select an item on the timeline to edit it here.' : 'The timeline is empty.' }),
      h('p', { class: 'empty', text: 'Press Ctrl+N to add an item.' }));
    if (hint) empty.append(hint);
  }

  function refresh() {
    if (root.hidden) return;
    const next = state.selection.size === 1 ? state.primaryId : null;
    if (next !== id) {
      id = next;
      detail = null;
      closeCal();
      historyOpen = false;
      histBody.hidden = true;
      histToggle.setAttribute('aria-expanded', 'false');
      histToggle.lastChild.textContent = 'Show history for this item';
      for (const f of [titleField, startField, endField, notesField]) f.msg('');
      delete startIn.dataset.invalid; delete endIn.dataset.invalid;
      notesDirty = false;
      if (id) {
        notesIn.value = '';
        attList.replaceChildren(); outlookBox.replaceChildren(); hideRow.replaceChildren();
        loadDetail();
      }
    }
    body.hidden = !id || !item();
    empty.hidden = !body.hidden;
    if (!body.hidden) { syncFields(); if (detail) reloadDetail(); } else renderEmpty();
  }

  on('selection', refresh);
  on('items', refresh);
  return { el: root, activate: refresh };
}
