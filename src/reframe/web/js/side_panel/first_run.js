// First-run hint (spec 8.2): an inline block, not a dialog.
import { copyText, h } from '../dom.js';
import { saveSettings } from '../actions.js';
import { notify, setPanel, state } from '../store.js';
import { btn } from './ui.js';

export function firstRunHint() {
  if (state.items.length || state.settings.first_run_hint_dismissed) return null;
  return h('div', { class: 'first-run', role: 'region', 'aria-label': 'Getting started' },
    h('h3', { text: 'Welcome to Reframe' }),
    h('ul', {},
      h('li', {}, 'Press ', h('kbd', { text: 'Ctrl+N' }), ' to add your first item.'),
      h('li', {}, 'Connect your calendars (ICS feeds, or Outlook where supported): ',
        btn('Open calendar settings', { onclick: () => setPanel({ open: true, tab: 'settings' }) })),
      h('li', {}, 'Agents can read and write this timeline. Instruction file: ',
        h('code', { class: 'path', text: state.agentFile || '(not found)' }), ' ',
        btn('Copy path', { iconName: 'copy', onclick: async () => notify((await copyText(state.agentFile)) ? 'Path copied' : 'Could not copy', { ttl: 3000 }) }))),
    btn('Dismiss', { onclick: () => saveSettings({ first_run_hint_dismissed: true }) }));
}
