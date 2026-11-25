// Attachment path bar (R-120 – R-124): paste/type a path, or search with voidtools Everything.
// All text goes in through textContent (via h()'s `text` prop or string children), never innerHTML.

import { h, debounce } from './dom.js';

export const PAGE_SIZE = 100;
export const DEBOUNCE_MS = 200;

const PATH_RE = /^(?:[A-Za-z]:\\|\\\\|https?:\/\/|webcal:\/\/|file:)/i;

/** R-120: path mode for drive paths, UNC, http(s)/webcal/file URLs or quoted text (same rule as the backend). */
export function looksLikePath(text) {
  const t = String(text ?? '').trim();
  if (!t) return false;
  if (t.length >= 2 && t[0] === t[t.length - 1] && (t[0] === '"' || t[0] === "'")) return true;
  return PATH_RE.test(t);
}

/** Request ids increase monotonically; a reply whose id is not the latest is stale (R-123). */
export function nextRequestId(prev) {
  const n = Number(prev);
  return Number.isFinite(n) && n >= 0 ? Math.floor(n) + 1 : 1;
}

/** Bytes → "512 B", "1.5 KB", "23 MB". Unknown (null/negative/non-number) → '' — never a false 0. */
export function formatSize(bytes) {
  if (bytes === null || bytes === undefined || bytes === '' || typeof bytes === 'boolean') return '';
  const n = Number(bytes);
  if (!Number.isFinite(n) || n < 0) return '';
  if (n < 1024) return `${n} B`;
  const units = ['KB', 'MB', 'GB', 'TB', 'PB'];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}

const pad = (n) => String(n).padStart(2, '0');

/** ISO timestamp → local "YYYY-MM-DD HH:MM". Unknown or unparseable → '' — never an invented date. */
export function formatDate(iso) {
  if (!iso || typeof iso !== 'string') return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** "100 of 1,234" / "3 shown" when the total is unknown. */
export function formatCount(shown, total) {
  const s = Number(shown).toLocaleString();
  return total === null || total === undefined ? `${s} shown` : `${s} of ${Number(total).toLocaleString()}`;
}

const STATE_TEXT = {
  no_matches: ['info', 'No matches.'],
  not_running: ['warn', 'Everything is not running.'],
  indexing: ['info', 'Everything is still indexing. Try again in a moment.'],
  timeout: ['warn', 'The search timed out.'],
  missing_sdk: ['warn', 'Search component missing (Everything SDK not found). You can still paste a path.'],
  error: ['error', 'Unexpected search error.'],
  disabled: ['info', 'Everything search is off in Settings. You can still paste or type a path.'],
  starting: ['info', 'Starting Everything…'],
};

let seq = 0;

async function getJson(url, init) {
  const res = await fetch(url, { credentials: 'same-origin', ...(init || {}) });
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) throw new Error((data && data.message) || `Request failed (${res.status})`);
  return data || {};
}

/**
 * createPathBar({ onAdd(path) => Promise<bool>, placeholder })
 * → { el, input, focus(), setEnabled(bool) }
 */
export function createPathBar({ onAdd, placeholder = 'Paste a path or URL, or type to search files' } = {}) {
  seq += 1;
  const uid = `path-bar-${seq}`;
  const listId = `${uid}-list`;

  let enabled = true;
  let sort = 'modified';
  let latest = 0;
  let query = '';
  let results = [];
  let total = null;
  let active = -1;
  let busyAdd = false;
  let loadingMore = false;

  const input = h('input', {
    type: 'text', class: 'input path-bar__input', placeholder,     autocomplete: 'off',
    role: 'combobox', 'aria-label': 'Add link: paste a path or search files', 'aria-autocomplete': 'list',
    'aria-haspopup': 'listbox', 'aria-expanded': 'false', 'aria-controls': listId,
  });
  input.setAttribute('spellcheck', 'false'); // h() drops false-valued props
  const count = h('span', { class: 'path-bar__count' });
  const sortBtn = h('button', { type: 'button', class: 'btn btn--ghost btn--small path-bar__sort', onclick: toggleSort });
  const list = h('ul', { class: 'path-bar__list', id: listId, role: 'listbox', 'aria-label': 'Matching files' });
  const popup = h('div', { class: 'path-bar__popup', hidden: true },
    h('div', { class: 'path-bar__head' }, count, sortBtn), list);
  const stateBox = h('div', { class: 'path-bar__state', hidden: true });
  const live = h('div', { class: 'offscreen', role: 'status', 'aria-live': 'polite' });
  const el = h('div', { class: 'path-bar' }, h('div', { class: 'path-bar__field' }, input, popup), stateBox, live);

  // Clicks inside the popup must not steal focus from the input.
  popup.addEventListener('mousedown', (e) => e.preventDefault());
  el.addEventListener('focusout', (e) => { if (!el.contains(e.relatedTarget)) closeList(); });
  input.addEventListener('input', onInput);
  input.addEventListener('keydown', onKey);
  renderSort();

  const runDebounced = debounce(() => runQuery(0), DEBOUNCE_MS);

  function renderSort() {
    sortBtn.textContent = sort === 'modified' ? 'Sort: Modified' : 'Sort: Name';
    sortBtn.setAttribute('aria-label', `Sorted by ${sort === 'modified' ? 'date modified, newest first' : 'name'}; switch`);
  }

  function announce(text) { live.textContent = text; }

  function invalidate() { latest = nextRequestId(latest); }

  function isOpen() { return !popup.hidden; }

  function closeList() {
    popup.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
    active = -1;
  }

  function clearState() {
    stateBox.hidden = true;
    stateBox.replaceChildren();
    stateBox.className = 'path-bar__state';
  }

  function resetSearch() {
    runDebounced.cancel();
    invalidate();
    results = [];
    total = null;
    closeList();
    clearState();
  }

  function showState(state, detail) {
    closeList();
    const [tone, text] = STATE_TEXT[state] || STATE_TEXT.error;
    const msg = state === 'error' && detail ? `${text} ${detail}` : text;
    stateBox.replaceChildren(h('span', { class: 'path-bar__state-text', text: msg }));
    stateBox.className = `path-bar__state path-bar__state--${tone}`;
    if (state === 'not_running') {
      stateBox.append(h('button', { type: 'button', class: 'btn btn--small', text: 'Start Everything', onclick: startEverything }));
    } else if (state === 'indexing' || state === 'timeout' || state === 'error') {
      stateBox.append(h('button', { type: 'button', class: 'btn btn--small', text: 'Retry', onclick: () => runQuery(0) }));
    }
    stateBox.hidden = false;
    announce(msg);
  }

  function optionId(i) { return `${uid}-opt-${i}`; }

  function renderList() {
    const rows = results.map((r, i) => h('li', {
      class: 'path-bar__option', role: 'option', id: optionId(i), 'aria-selected': 'false', title: r.path,
      onclick: () => choose(i),
    },
    h('span', { class: 'path-bar__name', text: r.name || r.path }),
    h('span', { class: 'path-bar__meta' },
      h('span', { class: 'path-bar__size', text: formatSize(r.size) }),
      h('span', { class: 'path-bar__date', text: formatDate(r.modified) })),
    h('span', { class: 'path-bar__folder', text: r.folder || '' })));
    if (hasMore()) {
      rows.push(h('li', {
        class: 'path-bar__option path-bar__more', role: 'option', id: optionId(results.length), 'aria-selected': 'false',
        text: loadingMore ? 'Loading…' : `Show more (next ${Math.min(PAGE_SIZE, total - results.length).toLocaleString()})`,
        onclick: () => loadMore(),
      }));
    }
    list.replaceChildren(...rows);
    count.textContent = formatCount(results.length, total);
    popup.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    setActive(Math.min(active, rows.length - 1));
  }

  function hasMore() { return total !== null && total !== undefined && results.length < total; }

  function optionCount() { return results.length + (hasMore() ? 1 : 0); }

  function setActive(i) {
    active = i;
    for (const li of list.children) li.setAttribute('aria-selected', 'false');
    const li = i >= 0 ? list.children[i] : null;
    if (li) {
      li.setAttribute('aria-selected', 'true');
      input.setAttribute('aria-activedescendant', li.id);
      if (li.scrollIntoView) li.scrollIntoView({ block: 'nearest' });
    } else {
      input.removeAttribute('aria-activedescendant');
    }
  }

  function choose(i) {
    if (i === results.length && hasMore()) { loadMore(); return; }
    const r = results[i];
    if (!r) return;
    resetSearch();
    input.value = r.path;
    el.classList.add('path-bar--path');
    input.focus();
    announce(`${r.name} selected. Press Enter to add it.`);
  }

  function loadMore() {
    if (loadingMore || !hasMore()) return;
    loadingMore = true;
    renderList();
    runQuery(results.length);
  }

  async function runQuery(offset) {
    if (!query) return;
    if (!enabled) { showState('disabled'); return; }
    invalidate();
    const rid = latest;
    const params = new URLSearchParams({ q: query, offset: String(offset), limit: String(PAGE_SIZE), sort, rid: String(rid) });
    let data;
    try {
      data = await getJson(`/api/search/files?${params}`);
    } catch (e) {
      if (rid !== latest) return;
      loadingMore = false;
      showState('error', e && e.message);
      return;
    }
    if (String(data.request_id) !== String(latest) || rid !== latest || data.state === 'superseded') return;
    loadingMore = false;
    if (data.state === 'ok') {
      clearState();
      const first = offset > 0 ? results.length : 0;
      results = offset > 0 ? results.concat(data.results || []) : (data.results || []);
      total = data.total ?? null;
      if (!results.length) { showState('no_matches'); return; }
      active = offset > 0 ? first : -1;
      renderList();
      announce(`${formatCount(results.length, total)} results`);
    } else if (data.state === 'disabled') {
      enabled = false;
      showState('disabled');
    } else if (data.state === 'empty') {
      resetSearch();
    } else {
      showState(data.state in STATE_TEXT ? data.state : 'error', data.message);
    }
  }

  async function startEverything() {
    showState('starting');
    try {
      await getJson('/api/search/start-everything', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
      });
    } catch (e) {
      showState('error', e && e.message);
      return;
    }
    // Everything needs a moment to create its IPC window and load its database.
    setTimeout(() => runQuery(0), 1500);
  }

  function toggleSort() {
    sort = sort === 'modified' ? 'name' : 'modified';
    renderSort();
    if (query) runQuery(0);
    input.focus();
  }

  function onInput() {
    const text = input.value.trim();
    const pathMode = looksLikePath(text);
    el.classList.toggle('path-bar--path', pathMode);
    if (!text || pathMode) { query = ''; resetSearch(); return; }
    query = text;
    if (!enabled) { runDebounced.cancel(); invalidate(); showState('disabled'); return; }
    runDebounced();
  }

  async function add(text) {
    if (busyAdd) return;
    busyAdd = true;
    try {
      const ok = await onAdd(text);
      if (ok) {
        input.value = '';
        query = '';
        el.classList.remove('path-bar--path');
        resetSearch();
      }
    } finally {
      busyAdd = false;
    }
  }

  function onKey(e) {
    const open = isOpen();
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (!open) {
        if (results.length && query) { renderList(); e.preventDefault(); }
        return;
      }
      e.preventDefault();
      const n = optionCount();
      if (!n) return;
      const step = e.key === 'ArrowDown' ? 1 : -1;
      setActive(active < 0 ? (step > 0 ? 0 : n - 1) : (active + step + n) % n);
    } else if (e.key === 'Enter') {
      if (e.isComposing) return;
      e.preventDefault();
      if (open && active >= 0) { choose(active); return; }
      const text = input.value.trim();
      if (!text) return;
      if (looksLikePath(text)) { add(text); return; }
      if (enabled) { query = text; runDebounced.flush(); }
    } else if (e.key === 'Escape') {
      if (open) { e.preventDefault(); e.stopPropagation(); closeList(); }
    } else if (e.key === 'Tab') {
      closeList();
    }
  }

  return {
    el,
    input,
    focus() { input.focus(); },
    setEnabled(on) {
      enabled = !!on;
      if (!enabled) {
        resetSearch();
        if (query) showState('disabled');
      } else if (query) {
        runQuery(0);
      }
    },
  };
}
