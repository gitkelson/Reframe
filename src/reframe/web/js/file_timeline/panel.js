// Side panel content for the File timeline: scan-status strip (R-141, R-149), file details with Open / Open
// containing folder (R-147), the day browser (R-146) and the Undated list (R-144). All text via textContent.
import { formatLong } from '../dates.js';
import { h, plural } from '../dom.js';
import { createBrowser } from './browser.js';
import { filesApi } from './api.js';
import { formatSize } from './groups.js';

const ROOT_STATUS = {
  ok: 'Scanned', partial: 'Partly scanned', empty: 'Empty (no files after exclusions)', unreachable: 'Unreachable',
  denied: 'Access denied', not_a_folder: 'Not a folder', cancelled: 'Scan cancelled', not_scanned: 'Not scanned yet',
};
export const rootStatusText = (r) => ROOT_STATUS[r.status] || r.status;

export function createPanel(host, ctx) {
  const { store, loader, getTheme } = ctx;
  const statusEl = h('div', { class: 'ft-status', role: 'status', 'aria-live': 'polite' });
  const titleEl = h('h2', { class: 'ft-panel__title' });
  const backBtn = h('button', { type: 'button', class: 'btn btn--small ft-panel__back', text: '\u2039 Back', hidden: true });
  const bodyEl = h('div', { class: 'ft-panel__body' });
  const root = h('div', { class: 'ft-panel' }, ctx.statusHost ? null : statusEl, h('div', { class: 'ft-panel__head' }, titleEl, backBtn), bodyEl);
  host.replaceChildren(root);
  if (ctx.statusHost) ctx.statusHost.replaceChildren(statusEl);

  let dataPending = false;
  const browser = createBrowser({
    loader, icons: ctx.icons, getTheme, sources: () => store.state.sources, idPrefix: 'ft-browse',
    onSelect: (e) => { store.select(e.id); ctx.onBrowserSelect?.(e); renderBrowserDetails(e.id); },
    onOpen: (e) => ctx.onOpen(e.id),
    onDragEnd: () => { if (dataPending) { dataPending = false; dataChanged(); } },
  });
  const browserWrap = h('div', { class: 'ft-panel__browser' });
  const browserDetails = h('div', { class: 'ft-panel__mini' });
  let detailsToken = 0;
  let back = null;

  // -------------------------------------------------------------- status strip

  function renderStatus() {
    const st = store.state.status;
    if (!st) { statusEl.replaceChildren(h('span', { text: 'Loading\u2026' })); return; }
    const kids = [];
    let line;
    if (st.state === 'no_roots') line = 'No folders chosen yet. Add folders in Settings \u25b8 Files.';
    else if (st.state === 'scanning') line = `Scanning\u2026 ${st.progress?.files ?? 0} files found so far`;
    else if (st.state === 'never') line = 'Not scanned yet.';
    else line = `${plural(st.files, 'file')} indexed${st.finished_at ? ` \u00b7 last scan ${st.finished_at.replace('T', ' ')}` : ''}`;
    kids.push(h('div', { class: 'ft-status__line', text: line }));
    if (st.state === 'scanning') {
      kids.push(h('div', { class: 'ft-status__bar', role: 'progressbar', 'aria-label': 'Scan progress', 'aria-valuetext': line },
        h('div', { class: 'ft-status__bar-fill' })));
    }
    if (st.exif_pending) kids.push(h('div', { class: 'ft-status__note', text: `Reading dates taken: ${st.exif_pending} file${st.exif_pending === 1 ? '' : 's'} left (placement is provisional until then).` }));
    if (st.stale) kids.push(h('div', { class: 'ft-status__warn', text: st.stale_reason || 'The list may be out of date.' }));
    if (st.error) kids.push(h('div', { class: 'ft-status__warn', text: st.error }));
    const problems = (st.roots || []).filter((r) => !['ok', 'not_scanned'].includes(r.status) || r.links_skipped || r.online_only);
    if (problems.length) {
      const det = h('details', { class: 'ft-status__details' }, h('summary', { text: st.partial ? 'Partial scan: see roots' : 'Scan notes' }));
      for (const r of st.roots) {
        const bits = [rootStatusText(r)];
        if (r.message) bits.push(r.message);
        if (r.links_skipped) bits.push(`${r.links_skipped} link${r.links_skipped === 1 ? '' : 's'} / junctions not followed`);
        if (r.hidden_skipped) bits.push(`${r.hidden_skipped} hidden/system skipped`);
        if (r.excluded) bits.push(`${r.excluded} excluded by type`);
        if (r.online_only) bits.push(`${r.online_only} online-only`);
        const li = h('li', {}, h('div', { class: 'ft-status__root', text: r.path }), h('div', { class: 'ft-status__note', text: bits.join(' \u00b7 ') }));
        const lists = [['Access denied', r.denied, r.denied_count], ['Read errors', r.errors, r.error_count]];
        for (const [label, arr, n] of lists) {
          if (!n) continue;
          const ul = h('ul', { class: 'ft-status__paths' }, arr.map((p) => h('li', { text: p })));
          li.append(h('div', { class: 'ft-status__note', text: `${label} (${n}${n > arr.length ? `, first ${arr.length} listed` : ''}):` }), ul);
        }
        det.append(h('ul', { class: 'ft-status__roots' }, li));
      }
      kids.push(det);
    }
    const actions = h('div', { class: 'ft-status__actions' });
    if (st.state === 'scanning') actions.append(h('button', { type: 'button', class: 'btn btn--small', text: 'Cancel scan', onclick: ctx.onCancel }));
    else if (st.state !== 'no_roots') actions.append(h('button', { type: 'button', class: 'btn btn--small', text: 'Refresh', title: 'Rescan all roots (F5)', onclick: ctx.onRescan }));
    kids.push(actions);
    statusEl.replaceChildren(...kids);
  }

  // -------------------------------------------------------------- details

  function field(label, value, cls) {
    if (value === null || value === undefined || value === '') return null;
    return h('div', { class: 'field ft-field' }, h('span', { class: 'field__label', text: label }),
      h('div', { class: `ft-field__value${cls ? ` ${cls}` : ''}`, text: String(value) }));
  }

  async function fetchDetails(id, target, compact) {
    const token = ++detailsToken;
    target.replaceChildren(h('div', { class: 'field__hint', text: 'Loading details\u2026' }));
    let d;
    try {
      d = await filesApi.entry(id);
    } catch (e) {
      if (token === detailsToken) target.replaceChildren(h('div', { class: 'field__msg', 'data-kind': 'error', text: e.message }));
      return;
    }
    if (token !== detailsToken) return;
    const mode = store.state.mode;
    const active = d.modes[mode];
    const taken = d.taken.value ? `${d.taken.value}${d.taken.offset ? ` (UTC${d.taken.offset})` : ' (wall clock, no time zone)'}`
      : d.taken.message || '';
    const msg = h('div', { class: 'field__msg', role: 'alert' });
    const act = async (fn, verb) => {
      msg.textContent = '';
      msg.removeAttribute('data-kind');
      try { await fn(d.id); } catch (e) { msg.dataset.kind = 'error'; msg.textContent = `${verb}: ${e.message}`; }
    };
    const kids = [
      h('div', { class: 'ft-details__name', text: d.name }),
      h('div', { class: 'ft-details__actions' },
        h('button', { type: 'button', class: 'btn btn--primary btn--small', text: 'Open', onclick: () => act(filesApi.open, 'Could not open') }),
        h('button', { type: 'button', class: 'btn btn--small', text: 'Open containing folder', onclick: () => act(filesApi.reveal, 'Could not show the folder') })),
      msg,
      field('Placed by', active.undated ? `${active.source_label} (unreadable: in Undated)` : `${active.source_label}: ${formatLong(dayNum(active.date))}`),
      field('Full path', d.path, 'ft-field__value--path'),
      field('Size', `${formatSize(d.size)} (${d.size.toLocaleString()} bytes)`),
    ];
    if (!compact) {
      kids.push(
        field('Created', d.created || 'Unreadable'),
        field('Modified', d.modified || 'Unreadable'),
        d.kind === 'image' || d.ext === '.mp4' ? field('Date taken', taken) : null,
        field('Root', d.root.path),
      );
    }
    if (d.online_only) kids.push(field('Availability', 'Online-only: not downloaded for previews or dates'));
    if (!d.exists) kids.push(h('div', { class: 'field__msg', 'data-kind': 'warn', text: 'This file is no longer at this path. Refresh the view.' }));
    target.replaceChildren(...kids.filter(Boolean));
  }

  const dayNum = (iso) => Math.round(Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10)) / 86400000);

  function renderBrowserDetails(id) {
    if (!id) { detailsToken++; browserDetails.replaceChildren(); return; }
    fetchDetails(id, browserDetails, false);
  }

  // -------------------------------------------------------------- modes

  function setBack(fn) {
    back = fn;
    backBtn.hidden = !fn;
  }
  backBtn.addEventListener('click', () => back && back());

  function bucketItems(b) {
    const dated = store.state.filtered.filter((e) => e.day >= b.start && e.day < b.end);
    return b.includeUndated ? dated.concat(undatedItems()) : dated;
  }

  function undatedItems() {
    const f = store.state.filter;
    if (!store.filterActive()) return store.state.undated;
    const { compile } = ctx;
    return store.state.undated.filter(compile(f));
  }

  function render() {
    detailsToken++;
    const p = store.state.panel;
    if (p.mode !== 'day' && p.mode !== 'undated') browser.release();
    if (p.mode === 'details' && store.state.selectedId) {
      titleEl.textContent = 'File details';
      setBack(p.fromBucket ? () => store.setPanel({ mode: 'day', bucket: p.fromBucket, fromBucket: null }) : null);
      const box = h('div', { class: 'ft-details' });
      bodyEl.replaceChildren(box);
      fetchDetails(store.state.selectedId, box, false);
      return;
    }
    if (p.mode === 'day' && p.bucket) {
      const items = bucketItems(p.bucket);
      const b = p.bucket;
      const range = b.end - b.start === 1 ? formatLong(b.start) : `${formatLong(b.start)} \u2013 ${formatLong(b.end - 1)}`;
      titleEl.textContent = range;
      setBack(null);
      const filtered = store.filterActive() ? ' matching the filter' : '';
      const undated = b.includeUndated ? ' Includes undated files.' : '';
      bodyEl.replaceChildren(
        h('div', { class: 'field__hint', text: `${plural(items.length, 'file')}${filtered}: ${items.filter((e) => e.kind === 1).length} images.${undated} Enter or double-click opens a file; drag it onto an item to attach it.` }),
        browserWrap, browserDetails);
      browserWrap.replaceChildren(browser.el);
      browser.set(items, { keepScroll: p.keepScroll });
      browser.setSelected(store.state.selectedId);
      browserDetails.replaceChildren();
      return;
    }
    if (p.mode === 'undated') {
      const items = undatedItems();
      titleEl.textContent = 'Undated files';
      setBack(null);
      bodyEl.replaceChildren(
        h('div', { class: 'field__hint', text: `${plural(items.length, 'file')} whose ${store.state.mode === 'created' ? 'created' : 'modified'} date cannot be read.` }),
        browserWrap, browserDetails);
      browserWrap.replaceChildren(browser.el);
      browser.set(items, { keepScroll: p.keepScroll });
      browserDetails.replaceChildren();
      return;
    }
    titleEl.textContent = 'Files';
    setBack(null);
    bodyEl.replaceChildren(h('p', { class: 'field__hint', text: 'Select a file to see its details. Click the count above a column, a +N badge or a day on the ruler to browse every file in it. Double-click or Enter opens a file.' }));
  }

  function dataChanged() {
    if (browser.isDragging()) { dataPending = true; return; }
    const p = store.state.panel;
    if (p.mode === 'day' || p.mode === 'undated') {
      const focused = document.activeElement === browser.el;
      store.state.panel = { ...p, keepScroll: true };
      render();
      if (focused) browser.focus();
      store.state.panel = { ...store.state.panel, keepScroll: false };
    } else if (p.mode === 'details' && !store.state.selectedId) {
      store.setPanel({ mode: 'status' });
    }
  }

  return {
    render, renderStatus, dataChanged,
    count: () => store.state.panel.bucket ? bucketItems(store.state.panel.bucket).length : 0,
    release: () => browser.release(),
    refreshThumbs: () => browser.refresh(),
    focusBrowser: () => browser.focus(),
  };
}
