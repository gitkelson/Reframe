// Thin client for the local JSON API. Auth is a same-site cookie set when the window loaded.

export class ApiError extends Error {
  constructor(status, body) {
    super((body && body.message) || `Request failed (${status})`);
    this.status = status;
    this.kind = body && body.error;
    this.data = body || {};
  }
}

async function request(method, path, body) {
  const init = { method, headers: {}, credentials: 'same-origin' };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch (e) {
    throw new ApiError(0, { error: 'offline', message: 'Cannot reach the Reframe server.' });
  }
  let data = null;
  try { data = await res.json(); } catch (e) { /* empty body */ }
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

const qs = (o) => {
  const p = Object.entries(o || {}).filter(([, v]) => v !== undefined && v !== null && v !== '');
  return p.length ? '?' + new URLSearchParams(p).toString() : '';
};

export const api = {
  bootstrap: () => request('GET', '/api/bootstrap'),
  items: (range) => request('GET', '/api/items' + qs(range)),
  item: (id) => request('GET', `/api/items/${id}`),
  itemHistory: (id) => request('GET', `/api/items/${id}/history`),
  createItem: (b) => request('POST', '/api/items', b),
  duplicateItem: (id, startDate) => request('POST', `/api/items/${id}/duplicate`, { start_date: startDate }),
  patchItem: (id, b) => request('PATCH', `/api/items/${id}`, b),
  deleteItem: (id) => request('DELETE', `/api/items/${id}`),
  restoreItem: (id) => request('POST', `/api/items/${id}/restore`, {}),
  hideItem: (id, series) => request('POST', `/api/items/${id}/hide`, { series: !!series }),
  unhideItem: (id, series) => request('POST', `/api/items/${id}/unhide`, { series: !!series }),
  setTags: (id, tags) => request('PUT', `/api/items/${id}/tags`, { tags }),
  bulk: (ids, action, extra) => request('POST', '/api/items/bulk', { ids, action, ...(extra || {}) }),
  addAttachment: (id, target, label) => request('POST', `/api/items/${id}/attachments`, { target, label }),
  addIndexedFileAttachment: (id, fileEntryId) => request('POST', `/api/items/${id}/attachments`, { file_entry_id: fileEntryId }),
  removeAttachment: (id) => request('DELETE', `/api/attachments/${id}`),
  openAttachment: (id) => request('POST', `/api/attachments/${id}/open-smart`, {}),
  attachmentVersions: (id) => request('GET', `/api/attachments/${id}/versions`),
  attachmentProtection: (id) => request('GET', `/api/attachments/${id}/protection`),
  openVersion: (id, version) => request('POST', `/api/attachments/${id}/open-version`, { version_id: version }),
  restoreVersion: (vid, dest) => request('POST', `/api/vault/versions/${vid}/restore`, { dest }),
  vaultStatus: () => request('GET', '/api/vault/status'),
  vaultPrune: (confirm) => request('POST', '/api/vault/prune', { confirm: !!confirm }),
  vaultMove: (dest) => request('POST', '/api/vault/move', { dest }),
  chooseSave: (name) => request('POST', '/api/host/choose-save', { name }),
  searchStatus: () => request('GET', '/api/search/status'),
  checkAttachments: (ids) => request('POST', '/api/attachments/check', { ids }),

  tags: () => request('GET', '/api/tags'),
  suggestTags: (q) => request('GET', '/api/tags/suggest' + qs({ q })),
  hygiene: () => request('GET', '/api/tags/hygiene'),
  patchTag: (id, b) => request('PATCH', `/api/tags/${id}`, b),
  mergeTag: (id, into) => request('POST', `/api/tags/${id}/merge`, { into }),
  deleteTag: (id, force) => request('DELETE', `/api/tags/${id}${force ? '?force=1' : ''}`),
  createTag: (name, parentId) => request('POST', '/api/tags', { name, parent_id: parentId || null }),
  moveTag: (id, parentId) => request('POST', `/api/tags/${id}/move`, { parent_id: parentId || null }),
  movePreview: (id) => request('GET', `/api/tags/${id}/move-preview`),
  retireTag: (id, retired) => request('POST', `/api/tags/${id}/retire`, { retired }),
  repairImplied: () => request('POST', '/api/tags/repair-implied', {}),

  history: (params) => request('GET', '/api/history' + qs(params)),
  historyDetail: (id) => request('GET', `/api/history/${id}`),
  revert: (id) => request('POST', `/api/history/${id}/revert`, {}),
  hiddenRemoved: () => request('GET', '/api/history/hidden-removed'),
  undoState: () => request('GET', '/api/undo-state'),
  undo: () => request('POST', '/api/undo', {}),
  redo: () => request('POST', '/api/redo', {}),
  needsAttention: () => request('GET', '/api/needs-attention'),

  settings: () => request('GET', '/api/settings'),
  putSettings: (b) => request('PUT', '/api/settings', b),
  calendars: () => request('GET', '/api/calendars'),
  refreshOutlookCalendars: () => request('POST', '/api/calendars/refresh-outlook', {}),
  addIcs: (b) => request('POST', '/api/calendars/ics', b),
  patchCalendar: (id, b) => request('PATCH', `/api/calendars/${id}`, b),
  setAllCalendarsVisible: (visible) => request('POST', '/api/calendars/visibility', { visible }),
  connectCalendar: (id, connected) => request('POST', `/api/calendars/${id}/connect`, { connected }),
  removeCalendar: (id) => request('DELETE', `/api/calendars/${id}`),
  sync: (kinds) => request('POST', '/api/sync', kinds ? { kinds } : {}),
  system: () => request('GET', '/api/system'),
  quit: () => request('POST', '/api/quit', {}),
  syncStatus: () => request('GET', '/api/sync/status'),
  backup: () => request('POST', '/api/backup', {}),
  backups: () => request('GET', '/api/backups'),
  restoreBackup: (name) => request('POST', '/api/backups/restore', { name }),
  chooseFolder: () => request('POST', '/api/host/choose-folder', {}),
  chooseFile: () => request('POST', '/api/host/choose-file', {}),
  revealDb: () => request('POST', '/api/host/reveal-db', {}),
};

/** Server-Sent Events with automatic reconnect (EventSource reconnects by itself). */
export function connectEvents(handlers) {
  const es = new EventSource('/api/events');
  for (const [type, fn] of Object.entries(handlers)) {
    es.addEventListener(type, (e) => { try { fn(JSON.parse(e.data || '{}')); } catch (err) { console.error(err); } });
  }
  return es;
}
