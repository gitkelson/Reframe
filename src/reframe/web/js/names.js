// Display names for things the database stores as long identifiers or paths. Pure: no DOM, no state.

/** Short name for an attachment: the file name for a file, the last path segment (else the host) for a URL. */
export function attachmentName(target, kind) {
  const t = String(target || '').trim();
  if (!t) return '';
  if (kind === 'url') {
    if (/^mailto:/i.test(t)) return t.slice(7).split('?')[0] || t;
    try {
      const u = new URL(t);
      const last = u.pathname.split('/').filter(Boolean).pop();
      return last ? decodeURIComponent(last) : u.hostname || t;
    } catch { return t; }
  }
  const last = t.replace(/[\\/]+$/, '').split(/[\\/]/).pop();
  return last || t;
}

/** "Name (folder path)" when a path is known, so calendars that share a name can be told apart. */
export function calendarLabel(calendars, id) {
  if (!id) return '';
  const c = calendars && calendars[id];
  if (!c) return 'Unknown calendar (not in your saved selection)';
  const path = c.path && c.path.includes('\\') ? c.path : '';
  return path && path !== c.name ? `${c.name} (${path})` : c.name;
}
