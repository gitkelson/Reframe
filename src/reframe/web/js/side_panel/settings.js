// Settings tab (spec 8.3): Window & startup, Calendars, Attachments, Files, Backups, Database & agent.
// There is no Appearance section: the theme is chosen in View > Theme (R-90).
import { ApiError, api } from '../api.js';
import * as actions from '../actions.js';
import { commands } from '../commands.js';
import { copyText, h, plural } from '../dom.js';
import { notify, on, state } from '../store.js';
import { filesSettingsSection } from '../view_switch.js';
import { btn, field, section } from './ui.js';

function numberField(label, key, { min = 1, max = 3650, hint } = {}) {
  const input = h('input', { type: 'number', class: 'input mono', min, max, value: String(state.settings[key] ?? '') });
  const f = field(label, input, { id: `set-${key}`, hint });
  const commit = () => {
    const v = Number(input.value);
    if (!Number.isInteger(v) || v < min || v > max) { f.msg(`Enter a whole number from ${min} to ${max}`); return; }
    f.msg('');
    if (v !== state.settings[key]) actions.saveSettings({ [key]: v });
  };
  input.addEventListener('change', commit);
  input.addEventListener('blur', commit);
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); commit(); } });
  return { ...f, input, key };
}

function check(id, label, key, { onChange } = {}) {
  const box = h('input', { type: 'checkbox', id });
  box.addEventListener('change', () => { actions.saveSettings({ [key]: box.checked }); onChange?.(box.checked); });
  return { el: h('div', { class: 'check-row' }, box, h('label', { for: id, text: label })), box, key };
}

const fmtSize = (n) => (n == null ? '' : n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1048576).toFixed(1)} MB`);

export function createSettingsTab() {
  const root = h('div', { class: 'tab tab--settings' });
  const msg = (el, text, kind = 'error') => { el.textContent = text; el.dataset.kind = kind; };

  // ---- 1. Window & startup
  const trayChk = check('set-tray-on-minimize', 'Minimize to the system tray', 'tray_on_minimize');
  const startChk = check('set-start-with-windows', 'Start with Windows (hidden in the tray)', 'start_with_windows');
  const windowDays = numberField('Default window length (days)', 'window_days', { min: 7, max: 1830 });

  // ---- 2. Calendars (R-130 .. R-135)
  const calBox = h('div', { class: 'cal-list' });
  const calMsg = h('div', { class: 'field__msg', role: 'status' });
  const syncChk = check('set-sync-on-launch', 'Sync calendars when Reframe starts', 'sync_on_launch');
  const lookback = numberField('Lookback days for sync', 'lookback_days', { max: 3650, hint: 'Recurring meetings are expanded this far back after the first sync.' });
  const icsRefresh = numberField('Refresh calendar feeds every (minutes)', 'ics_refresh_min', { min: 5, max: 10080 });
  const icsUrl = h('input', { type: 'text', class: 'input', placeholder: 'https://… or webcal://… calendar feed', 'aria-label': 'Calendar feed URL', spellcheck: false, autocomplete: 'off' });
  let httpWarned = '';

  async function loadCalendars() {
    try {
      const r = await actions.refreshCalendars();
      renderCalendars(r.sources);
    } catch (e) { msg(calMsg, e.message); }
  }
  async function refreshOutlook() {
    msg(calMsg, 'Reading calendar folders from Outlook…', 'info');
    try {
      const r = await api.refreshOutlookCalendars();
      msg(calMsg, r.available ? (r.new ? `${plural(r.new, 'new calendar')} connected, hidden.` : 'Calendar list is up to date.') : r.message,
        r.available ? 'info' : 'warn');
      await actions.refreshCalendars();
      renderCalendars(r.sources, r.empty || []);
    } catch (e) { msg(calMsg, e.message); }
  }
  async function addIcs(location, { allowHttp = false } = {}) {
    try {
      await api.addIcs({ location, allow_http: allowHttp });
      icsUrl.value = '';
      httpWarned = '';
      msg(calMsg, 'Calendar added and syncing.', 'info');
      loadCalendars();
    } catch (e) {
      if (e instanceof ApiError && e.kind === 'http_warning') {
        httpWarned = location;
        calMsg.dataset.kind = 'warn';
        calMsg.replaceChildren(h('span', { text: e.message + ' ' }),
          btn('Add anyway', { onclick: () => addIcs(location, { allowHttp: true }) }));
        return;
      }
      msg(calMsg, e.message);
    }
  }
  async function chooseIcsFile() {
    try { const r = await api.chooseFile(); if (r.path) addIcs(r.path); } catch (e) { msg(calMsg, e.message); }
  }
  async function setFlag(c, patch) {
    try { await api.patchCalendar(c.id, patch); await loadCalendars(); } catch (e) { msg(calMsg, e.message); }
  }
  async function setConnected(c, connected) {
    try {
      const r = await api.connectCalendar(c.id, connected);
      if (!connected && r.removed) notify(`Disconnected ${c.name}: ${plural(r.removed, 'item')} removed`, { undo: true });
      await actions.refreshAll();
      await loadCalendars();
    } catch (e) { msg(calMsg, e.message); }
  }
  function calRow(c, empty) {
    const sw = (label, checked, onchange, id) => {
      const box = h('input', { type: 'checkbox', id, checked: !!checked, onchange: () => onchange(box.checked) });
      return h('span', { class: 'cal-row__opt' }, box, h('label', { for: id, text: label }));
    };
    return h('div', { class: `cal-row${c.connected ? '' : ' is-disconnected'}` },
      h('div', { class: 'cal-row__head' }, h('span', { class: 'cal-row__name', text: c.name }),
        h('span', { class: 'cal-row__kind', text: c.kind === 'ics' ? 'Feed' : 'Outlook' }),
        h('span', { class: 'cal-row__count', text: plural(c.item_count || 0, 'item') })),
      h('div', { class: 'cal-row__path mono', text: c.display_location || c.location || '' }),
      h('div', { class: 'cal-row__opts' },
        sw('Connected', c.connected, (v) => setConnected(c, v), `cal-con-${c.id}`),
        sw('Visible', c.visible, (v) => setFlag(c, { visible: v }), `cal-vis-${c.id}`),
        sw('Holidays', c.is_holiday, (v) => setFlag(c, { is_holiday: v }), `cal-hol-${c.id}`),
        c.kind === 'ics' && !c.connected ? btn('Remove', { onclick: () => api.removeCalendar(c.id).then(loadCalendars).catch((e) => msg(calMsg, e.message)) }) : null),
      c.last_status ? h('div', { class: 'cal-row__status', text: `Last read ${String(c.last_read_at || '').slice(0, 16).replace('T', ' ')}: ${c.last_status}${c.last_error ? ` (${c.last_error})` : ''}` }) : null,
      c.connected && (empty.includes(c.name) || (c.kind === 'outlook' && c.last_status === 'ok' && !c.item_count))
        ? h('div', { class: 'field__msg', 'data-kind': 'warn', text: `${c.name} returned 0 items` }) : null);
  }
  function renderCalendars(list, empty = []) {
    calBox.replaceChildren(...(list?.length ? list.map((c) => calRow(c, empty))
      : [h('p', { class: 'empty', text: 'No calendars yet. Every Outlook calendar is connected at the first sync; add calendar files or feeds below.' })]));
  }
  icsUrl.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); if (icsUrl.value.trim()) addIcs(icsUrl.value.trim(), { allowHttp: httpWarned === icsUrl.value.trim() }); } });

  // ---- 3. Attachments: vault (R-165, R-166) and Everything (R-128)
  const vaultBox = h('div', { class: 'vault-box' });
  const vaultMsg = h('div', { class: 'field__msg', role: 'status' });
  const evBox = h('div', { class: 'everything-box' });
  const evChk = check('set-everything', 'Enable Everything search in the attachment path bar', 'everything_enabled', { onChange: () => loadEverything() });
  let pruneOffer = null;

  async function loadVault() {
    try {
      const v = await api.vaultStatus();
      vaultBox.replaceChildren(
        h('dl', { class: 'kv' },
          h('dt', { text: 'Location' }), h('dd', { class: 'mono', text: v.vault_path || '' }),
          h('dt', { text: 'Stored' }), h('dd', { text: `${fmtSize(v.usage?.stored_bytes || 0)} in ${plural(v.usage?.versions || 0, 'version')}` }),
          h('dt', { text: 'Saved by deduplication' }), h('dd', { text: fmtSize(v.usage?.savings_bytes || 0) })),
        v.synced_folder_warning ? h('div', { class: 'field__msg', 'data-kind': 'warn', text: v.synced_folder_warning === true
          ? 'This folder is synced by OneDrive. Choose a local folder for the vault.' : String(v.synced_folder_warning) }) : null,
        v.unprotected?.length ? h('div', { class: 'vault-unprotected' },
          h('h4', { class: 'psec__sub', text: 'Files that could not be protected' }),
          h('ul', {}, v.unprotected.map((u) => h('li', { text: `${u.path}: ${u.error || u.status}` })))) : null,
        h('div', { class: 'row-actions' },
          state.hasHost ? btn('Move…', { onclick: moveVault }) : null,
          pruneOffer ? btn(`Delete ${plural(pruneOffer.versions, 'version')} (${fmtSize(pruneOffer.bytes)})`, { variant: 'danger', onclick: () => prune(true) })
            : btn('Delete versions no longer referenced by any link or undo history…', { onclick: () => prune(false) })));
    } catch (e) {
      vaultBox.replaceChildren(h('p', { class: 'empty', text: e.status === 404 ? 'File protection is not available.' : e.message }));
    }
  }
  async function prune(confirm) {
    try {
      const r = await api.vaultPrune(confirm);
      pruneOffer = confirm || !r.versions ? null : r;
      if (confirm) notify(`Deleted ${plural(r.versions || 0, 'unreferenced version')}`);
      else if (!r.versions) msg(vaultMsg, 'Every stored version is still referenced.', 'info');
      loadVault();
    } catch (e) { msg(vaultMsg, e.message); }
  }
  async function moveVault() {
    try {
      const r = await api.chooseFolder();
      if (!r.path) return;
      msg(vaultMsg, 'Copying and verifying all saved versions…', 'info');
      await api.vaultMove(r.path);
      msg(vaultMsg, 'Vault moved. The old copy was kept; remove it yourself when you are satisfied.', 'info');
      loadVault();
    } catch (e) { msg(vaultMsg, e.message); }
  }
  async function loadEverything() {
    try {
      const s = await api.searchStatus();
      evBox.replaceChildren(h('dl', { class: 'kv' },
        h('dt', { text: 'Everything' }), h('dd', { text: s.running ? `running${s.everything_version ? ` (${s.everything_version})` : ''}` : 'not running' }),
        h('dt', { text: 'Index' }), h('dd', { text: s.running ? (s.index_ready ? 'ready' : 'still indexing') : '—' }),
        h('dt', { text: 'SDK' }), h('dd', { text: s.sdk_present ? `version ${s.sdk_version ?? ''}` : 'missing' })),
      s.message ? h('p', { class: 'field__hint', text: s.message }) : null);
    } catch (e) { evBox.replaceChildren(h('p', { class: 'empty', text: e.message })); }
  }

  // ---- 4. Files (the File timeline provides its section)
  const filesBox = h('div', { class: 'files-settings' }, h('p', { class: 'empty', text: 'Loading…' }));
  let filesLoaded = false;
  async function loadFiles() {
    if (filesLoaded) return;
    filesLoaded = true;
    const sec = await filesSettingsSection();
    filesBox.replaceChildren(sec || h('p', { class: 'empty', text: 'The File timeline is not available.' }));
  }

  // ---- 5. Backups (spec 8.4)
  const folderText = h('code', { class: 'path' });
  const folderMsg = h('div', { class: 'field__msg', role: 'status' });
  const backupList = h('ul', { class: 'backup-list' });
  const backupMsg = h('div', { class: 'field__msg', role: 'status' });
  const nums = [numberField('Backups to keep (most recent)', 'backup_keep_last', { max: 1000 }),
    numberField('Daily backups kept (days)', 'backup_keep_days'), numberField('Monthly backups kept (months)', 'backup_keep_months', { max: 240 })];
  let restoring = null;
  async function choose() {
    try {
      const r = await api.chooseFolder();
      if (r.path) { await actions.saveSettings({ backup_folder: r.path }); renderFolder(); loadBackups(); }
    } catch (e) { msg(folderMsg, e.message); }
  }
  function renderFolder() { folderText.textContent = state.settings.backup_folder || 'Default: Documents/Reframe Backups'; }
  async function backupNow() {
    backupMsg.textContent = '';
    try { const r = await api.backup(); notify(`Backup written: ${r.path}`, { ttl: 5000 }); loadBackups(); }
    catch (e) { msg(backupMsg, e instanceof ApiError ? e.message : 'Backup failed'); }
  }
  async function loadBackups() {
    try {
      const r = await api.backups();
      backupList.replaceChildren(...(r.backups.length ? r.backups.map(backupRow) : [h('li', { class: 'empty', text: 'No backups in the backup folder yet.' })]));
    } catch (e) { msg(backupMsg, e.message); }
  }
  function backupRow(b) {
    const confirming = restoring === b.name;
    return h('li', { class: 'backup' },
      h('div', { class: 'backup__main' },
        h('span', { class: 'mono backup__name', text: b.name }),
        h('span', { class: 'backup__size', text: fmtSize(b.size) }),
        confirming ? null : btn('Restore', { iconName: 'restore', ariaLabel: `Restore ${b.name}`, onclick: () => { restoring = b.name; loadBackups(); } })),
      confirming ? h('div', { class: 'backup__confirm' },
        h('p', { text: 'Reframe first makes a safety backup of the current state, verifies this backup, then replaces the database and attachment versions with it.' }),
        btn('Restore now', { variant: 'danger', onclick: () => restore(b.name) }),
        btn('Cancel', { onclick: () => { restoring = null; loadBackups(); } })) : null);
  }
  async function restore(name) {
    restoring = null;
    try {
      const r = await api.restoreBackup(name);
      await actions.refreshAll();
      await actions.refreshMeta();
      notify(`Restored ${name}. Safety backup of the previous state: ${r.safety_backup}`, { ttl: 10000 });
    } catch (e) { msg(backupMsg, e instanceof ApiError ? e.message : 'Restore failed'); }
    loadBackups();
  }

  // ---- 6. Database & agent
  const dbPath = h('code', { class: 'path' });
  const agentPath = h('code', { class: 'path' });
  const schema = h('span', { class: 'mono' });
  const copy = (text) => async () => notify((await copyText(text)) ? 'Path copied' : 'Could not copy the path', { ttl: 3000 });
  async function reveal() { try { await api.revealDb(); } catch (e) { notify(e.message, { kind: 'warn' }); } }

  root.append(
    section('Window & startup', trayChk.el, startChk.el, windowDays.el,
      h('p', { class: 'field__hint', text: 'Closing the window keeps Reframe running in the tray. Use View > Quit Reframe (Ctrl+Q) or the tray menu to exit.' })),
    section('Calendars', calBox, calMsg,
      h('div', { class: 'row-actions' },
        btn('Refresh list from Outlook', { iconName: 'sync', onclick: refreshOutlook }),
        btn('Sync now', { variant: 'primary', iconName: 'sync', onclick: () => actions.syncNow() })),
      h('div', { class: 'add-row' }, icsUrl, btn('Add URL', { onclick: () => { const v = icsUrl.value.trim(); if (v) addIcs(v, { allowHttp: httpWarned === v }); } }),
        state.hasHost ? btn('Add ICS file…', { iconName: 'folder', onclick: chooseIcsFile }) : null),
      h('p', { class: 'field__hint', text: 'Secret feed addresses are stored only in the local database and shown shortened.' }),
      syncChk.el, lookback.el, icsRefresh.el),
    section('Attachments', vaultBox, vaultMsg, h('h4', { class: 'psec__sub', text: 'Everything search' }), evChk.el, evBox),
    section('Files', filesBox),
    section('Backups',
      h('div', { class: 'path-row' }, folderText, state.hasHost ? btn('Choose folder…', { iconName: 'folder', onclick: choose }) : null), folderMsg,
      ...nums.map((n) => n.el),
      btn('Back up now', { variant: 'primary', iconName: 'copy', onclick: backupNow }), backupMsg,
      h('h4', { class: 'psec__sub', text: 'Backups' }), backupList),
    section('Database & agent',
      h('div', { class: 'path-row' }, dbPath,
        btn('Copy path', { iconName: 'copy', onclick: () => copy(state.dbPath)() }),
        btn('Reveal in Explorer', { iconName: 'folder', onclick: reveal })),
      h('p', {}, 'Schema version ', schema),
      h('div', { class: 'path-row' }, agentPath, btn('Copy path', { iconName: 'copy', onclick: () => copy(state.agentFile)() }))),
    section('Help', btn('Keyboard shortcuts', { onclick: () => commands.cheatsheet() })));

  function render() {
    if (root.hidden) return;
    renderFolder();
    for (const c of [trayChk, startChk, syncChk, evChk]) c.box.checked = state.settings[c.key] !== false;
    dbPath.textContent = state.dbPath;
    agentPath.textContent = state.agentFile || '(not found)';
    for (const n of [...nums, lookback, windowDays, icsRefresh]) if (document.activeElement !== n.input) n.input.value = String(state.settings[n.key] ?? '');
  }
  on('items', render);
  return { el: root, activate: () => {
    render(); loadBackups(); loadCalendars(); loadVault(); loadEverything(); loadFiles();
    api.system().then((s) => { schema.textContent = String(s.schema_version); }).catch(() => {});
  } };
}
