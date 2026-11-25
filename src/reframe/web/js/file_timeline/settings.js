// Settings â–¸ Files section (R-140, R-141, R-148): roots with per-root scan status, add (native chooser or paste) and
// remove; excluded extensions with one-click suggestions (none applied by default); include hidden/system files.
import { h } from '../dom.js';
import { attachTooltip } from '../tooltip.js';
import { filesApi } from './api.js';
import { rootStatusText } from './panel.js';

let sectionSeq = 0;

export function createSettingsSection({ chooseFolder, getStatus, onChanged }) {
  const hiddenId = `ft-include-hidden-${++sectionSeq}`;
  const el = h('section', { class: 'ft-settings', 'aria-label': 'File settings' });
  let opts = null;
  let suggestions = [];
  let busy = false;
  const msg = h('div', { class: 'field__msg', role: 'alert' });

  function say(text, kind = 'info') {
    msg.textContent = text || '';
    if (text) msg.dataset.kind = kind; else msg.removeAttribute('data-kind');
  }

  async function save(patch, okText) {
    if (busy) return;
    busy = true;
    say('');
    try {
      const r = await filesApi.putOptions(patch);
      opts = r.options;
      if (r.warnings && r.warnings.length) say(r.warnings.join(' '), 'warn');
      else if (okText) say(okText);
      onChanged?.();
    } catch (e) {
      const problems = e.data && e.data.problems;
      say(problems && problems.length ? problems.join(' ') : e.message, 'error');
    } finally {
      busy = false;
      render();
    }
  }

  function rootRow(path, i, st) {
    const rep = st && st.roots ? st.roots.find((r) => r.path === path) : null;
    const status = rep ? `${rootStatusText(rep)}${rep.files ? ` \u00b7 ${rep.files} files` : ''}${rep.message ? ` \u00b7 ${rep.message}` : ''}` : '';
    const remove = h('button', { type: 'button', class: 'btn btn--small btn--ghost', text: 'Remove', 'aria-label': `Remove root ${path}`,
      onclick: () => save({ roots: opts.roots.filter((_, j) => j !== i) }, 'Root removed.') });
    return h('li', { class: 'ft-settings__root' },
      h('div', { class: 'ft-settings__path', text: path }),
      status ? h('div', { class: `ft-settings__status is-${rep.status}`, text: status }) : null,
      remove);
  }

  function render() {
    if (!opts) { el.replaceChildren(h('h3', { class: 'ft-settings__title', text: 'Files' }), h('div', { class: 'field__hint', text: 'Loading\u2026' })); return; }
    const st = getStatus();
    const paste = h('input', { type: 'text', class: 'input', placeholder: 'Paste a folder path, e.g. C:\\Users\\me\\Pictures', 'aria-label': 'Folder path to add', spellcheck: false });
    const addTyped = () => { const v = paste.value.trim(); if (v) save({ roots: [...opts.roots, v] }, 'Root added; scanning.'); };
    paste.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); addTyped(); } });
    const choose = h('button', { type: 'button', class: 'btn btn--small', text: 'Choose folder\u2026',
      onclick: async () => {
        let p = null;
        try { p = await chooseFolder(); } catch (e) { say(e.message, 'error'); return; }
        if (p) save({ roots: [...opts.roots, p] }, 'Root added; scanning.');
      } });

    const ext = h('input', { type: 'text', class: 'input', placeholder: '.tmp, .tar.gz, ~$*, thumbs.db, (none)', 'aria-label': 'Extension or name pattern to exclude', spellcheck: false });
    const addExt = () => {
      const parts = ext.value.split(/[,;\s]+/).map((s) => s.trim()).filter(Boolean);
      if (parts.length) save({ excluded_ext: [...opts.excluded_ext, ...parts] }, 'Exclusion added.');
    };
    ext.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); addExt(); } });
    const exclChips = opts.excluded_ext.map((r) => h('button', { type: 'button', class: 'ft-chip is-on',
      'aria-label': `Stop excluding ${r}`, onclick: () => save({ excluded_ext: opts.excluded_ext.filter((x) => x !== r) }, `${r} is shown again.`) },
    h('span', { class: 'ft-chip__label', text: r }), h('span', { class: 'ft-chip__x', 'aria-hidden': 'true', text: '\u00d7' })));
    const sugg = suggestions.filter((s) => !opts.excluded_ext.includes(s)).map((s) => {
      const b = h('button', { type: 'button', class: 'ft-chip ft-chip--suggest', 'aria-label': `Exclude ${s}`,
        onclick: () => save({ excluded_ext: [...opts.excluded_ext, s] }, `${s} excluded.`) }, h('span', { class: 'ft-chip__label', text: `+ ${s}` }));
      attachTooltip(b, s === '~$*' ? 'Office lock files (~$name.docx)' : `Exclude ${s}`);
      return b;
    });
    const hidden = h('input', { type: 'checkbox', id: hiddenId, checked: !!opts.include_hidden,
      onchange: (e) => save({ include_hidden: e.target.checked }) });

    el.replaceChildren(
      h('h3', { class: 'ft-settings__title', text: 'Files' }),
      h('p', { class: 'field__hint', text: 'Folders shown in the timeline file browser. Files are read, never changed; the list is not stored in the database.' }),
      h('ul', { class: 'ft-settings__roots', 'aria-label': 'Roots' },
        opts.roots.length ? opts.roots.map((p, i) => rootRow(p, i, st)) : h('li', { class: 'field__hint', text: 'No folders yet.' })),
      h('div', { class: 'ft-settings__add' }, paste, h('button', { type: 'button', class: 'btn btn--small', text: 'Add', onclick: addTyped }), choose),
      h('div', { class: 'field' }, h('span', { class: 'field__label', text: 'Excluded extensions and names' }),
        h('div', { class: 'ft-settings__chips' }, exclChips.length ? exclChips : h('span', { class: 'field__hint', text: 'Nothing excluded: every file type is shown.' })),
        h('div', { class: 'ft-settings__add' }, ext, h('button', { type: 'button', class: 'btn btn--small', text: 'Exclude', onclick: addExt })),
        sugg.length ? h('div', { class: 'ft-settings__chips' }, h('span', { class: 'field__hint', text: 'Suggestions:' }), sugg) : null,
        h('p', { class: 'field__hint', text: 'Case-insensitive. ".tar.gz" matches multi-part endings, "(none)" matches files without an extension, "~$*" is a name pattern and "thumbs.db" an exact name.' })),
      h('div', { class: 'field ft-settings__check' }, hidden, h('label', { htmlFor: hiddenId, text: 'Include hidden and system files and folders' })),
      msg);
  }

  async function load() {
    try {
      const [o, s] = await Promise.all([filesApi.options(), filesApi.suggestions()]);
      opts = o;
      suggestions = s.suggestions || [];
    } catch (e) {
      say(e.message, 'error');
    }
    render();
  }

  render();
  load();
  return { el, refresh: () => { if (!busy) render(); }, reload: load };
}
