// Status bar (R-99, R-84): date under the cursor, item count, sync status, notice with Undo, database path.
import { undoLast } from './actions.js';
import { formatLong, formatShort, toDayNumber } from './dates.js';
import { copyText, h } from './dom.js';
import { dismissNotice, notify, on, state, visibleItems } from './store.js';
import { attachTooltip } from './tooltip.js';

const refs = {};
let syncedAt = null;

function hoverText() {
  if (state.hoverDay !== null && state.hoverDay !== undefined) return formatLong(Math.floor(state.hoverDay));
  const sel = state.primaryId && state.itemsById.get(state.primaryId);
  if (sel) return `${formatShort(toDayNumber(sel.start_date))} to ${formatShort(toDayNumber(sel.end_date))}`;
  return `${formatShort(Math.floor(state.view.start))} to ${formatShort(Math.floor(state.view.end))}`;
}

function clock(d) { return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`; }

function syncText() {
  const s = state.sync;
  if (s.running) return { text: `Syncing: ${s.stage || 'starting'}…`, cls: 'is-running' };
  if (s.error) return { text: 'Sync failed (see message)', cls: 'is-error', tip: s.error };
  const at = syncedAt || (state.settings.last_sync_at ? new Date(state.settings.last_sync_at) : null);
  const sources = s.sources || [];
  const tip = sources.length ? sources.map((c) => `${c.name}: ${c.error && !c.complete ? c.error
    : c.not_modified || !(c.added || c.updated || c.removed) ? 'up to date' : `${c.added} added, ${c.updated} updated, ${c.removed} removed`}`).join('\n')
    : s.last_result?.message;
  const partial = sources.some((c) => !c.complete);
  if (at && !Number.isNaN(at.getTime())) {
    return { text: `Synced ${clock(at)}${partial ? ' (partly)' : ''}`, cls: partial ? 'is-error' : '', tip };
  }
  if (sources.length) return { text: partial ? 'Synced (partly)' : 'Synced', cls: partial ? 'is-error' : '', tip };
  return { text: 'Not synced', cls: '' };
}

function renderCounts() {
  refs.count.textContent = `${visibleItems().length} / ${state.items.length} items`;
}

/** "3 files waiting to be protected" (R-99, R-163). */
function renderCapture() {
  const v = state.vault || {};
  const pending = v.pending || 0, failed = v.unprotected || 0;
  refs.capture.hidden = !(pending || failed);
  refs.capture.textContent = pending ? `${pending} file${pending === 1 ? '' : 's'} waiting to be protected`
    : failed ? `${failed} file${failed === 1 ? '' : 's'} not protected` : '';
  refs.capture.classList.toggle('is-error', !pending && !!failed);
}

function renderSync() {
  const { text, cls, tip } = syncText();
  refs.sync.textContent = text;
  refs.sync.className = `status__sync ${cls}`;
  refs.sync.title = '';
  refs.syncTip = tip || '';
}

function renderNotice() {
  const n = state.notice;
  refs.notice.replaceChildren();
  refs.notice.className = 'status__notice';
  if (!n) return;
  refs.notice.classList.add('has-notice', `notice--${n.kind}`);
  refs.notice.setAttribute('role', n.kind === 'error' ? 'alert' : 'status');
  const prefix = n.kind === 'error' ? 'Error: ' : n.kind === 'warn' ? 'Notice: ' : '';
  refs.notice.append(h('span', { class: 'notice__text', text: prefix + n.text }));
  if (n.undo) {
    refs.notice.append(h('button', { type: 'button', class: 'notice__btn', text: 'Undo',
      onclick: () => { dismissNotice(); undoLast(); } }));
  }
  if (n.kind !== 'info' || !n.undo) {
    refs.notice.append(h('button', { type: 'button', class: 'notice__btn notice__close', 'aria-label': 'Dismiss', text: '✕', onclick: dismissNotice }));
  }
}

export function initStatusBar(el) {
  refs.hover = h('span', { class: 'status__hover' });
  refs.count = h('span', { class: 'status__count' });
  refs.sync = h('span', { class: 'status__sync' });
  refs.capture = h('button', { type: 'button', class: 'status__capture', hidden: true,
    onclick: () => import('./commands.js').then((m) => m.commands.openSettings()) });
  refs.notice = h('div', { class: 'status__notice', role: 'status', 'aria-live': 'polite' });
  refs.db = h('button', { type: 'button', class: 'status__db', 'aria-label': 'Database path' },
    h('bdi', { class: 'status__db-text', text: state.dbPath }));
  refs.db.addEventListener('click', async () => {
    notify((await copyText(state.dbPath)) ? 'Database path copied' : 'Could not copy the path', { ttl: 3000 });
  });
  attachTooltip(refs.db, () => `${state.dbPath}\nClick to copy the full path`);
  attachTooltip(refs.sync, () => refs.syncTip);
  el.replaceChildren(refs.hover, refs.count, refs.sync, refs.capture, refs.notice, refs.db);

  const all = () => { refs.hover.textContent = hoverText(); renderCounts(); renderSync(); renderNotice(); };
  on('hover', () => { refs.hover.textContent = hoverText(); });
  on('view', () => { refs.hover.textContent = hoverText(); });
  on('selection', () => { refs.hover.textContent = hoverText(); });
  on('layout', renderCounts);
  on('items', () => { renderCounts(); refs.db.firstChild.textContent = state.dbPath; });
  on('sync', () => {
    if (!state.sync.running && !state.sync.error && state.sync.last_result) syncedAt = new Date();
    renderSync();
  });
  on('notice', renderNotice);
  on('vault', renderCapture);
  all();
}
