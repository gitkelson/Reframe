# Manual verification checklist (spec section 11, "Manual")

Run once per release with real data. Use the real window (double-click the **Reframe** shortcut or `pythonw Reframe.pyw`), not `--no-window`.

## Launch, taskbar and tray (R-104, R-170 .. R-174)
- [ ] Double-click the Start-menu shortcut: no console window appears; the taskbar button shows the Reframe icon, never the Python icon.
- [ ] Pin the running window to the taskbar, quit, start from the pin: it groups with the same button.
- [ ] The tray icon is present while Reframe runs, also while the window is shown. Clicking it shows and focuses the window.
- [ ] Close the window (X): it hides to the tray; the first time, a notice says Reframe is still running and to use Quit.
- [ ] Minimize with "Minimize to the system tray" on: window and taskbar button disappear; tray click restores.
- [ ] Tray menu: Show Reframe, Sync now, Start with Windows (check mark follows Settings), Quit Reframe. Ctrl+Q and View > Quit Reframe also exit.
- [ ] Sign out and in with Start with Windows on: Reframe starts hidden with only the tray icon.
- [ ] Launch a second copy while hidden in the tray: the window comes back and the second copy exits.

## Real Outlook and calendar feeds (R-130 .. R-139)
- [ ] First sync connects every Outlook calendar folder (shared and Microsoft 365 group calendars too); new ones are hidden. The Calendars control says "n of m calendars shown".
- [ ] Show a calendar from the filter bar's Calendars control: its items appear; hide it again: they disappear from the chart, minimap, search and counts.
- [ ] Sync again: "up to date" per source and no Undo button.
- [ ] Move one occurrence of a recurring meeting in Outlook, Sync: the item moves, its notes and tags stay.
- [ ] Add a real ICS feed URL in Settings > Calendars: the address is shown shortened; after sync its items are locked with the feed's name in the hint.
- [ ] Disconnect a calendar: its items go in one undoable change set; reconnect and sync: they come back with their notes.
- [ ] Close Outlook, Sync: a non-blocking message names the reason; feeds still sync.

## Tags (R-110 .. R-115)
- [ ] Tags tab: drag a tag onto another to group it; the drag chip shows how many items change; one Ctrl+Z undoes the move.
- [ ] An item with a grouped tag shows the group as a subdued chip ("Added by group …"); removing the group chip is refused with a hint.
- [ ] Drag `meeting` (or a group chip) to the front of an item's chips: it stays first and colours the bar.
- [ ] Retire a tag: its filter chip and autocomplete entry disappear; items keep it with a "retired" marker; Un-retire brings it back.
- [ ] Filter bar: All, then deselect two chips; None; the Untagged chip. Drag a chip onto a bar: "Added tag …" with Undo.

## Attachments (R-120 .. R-128, R-160 .. R-169)
- [ ] Type part of a file name in the path bar: Everything results appear with folder, size and date; Enter puts the path in the bar; Enter again adds it.
- [ ] Quit Everything: the bar says Everything is not running and offers to start it; pasting a path still works.
- [ ] Attach a Word file, edit and save it: a new version appears under Versions; opening the link opens the version you attached with the "file has changed" notice and its three actions.
- [ ] Restore a version to a new location with the save chooser; Reframe never overwrites.
- [ ] Settings > Attachments shows stored size, versions and deduplication savings.

## File timeline (R-140 .. R-155)
- [ ] Settings > Files: add a real project root and a photo folder; Ctrl+2 shows files by modified date; Created shows photos on the day they were taken.
- [ ] A day with hundreds of photos shows thumbnails plus "+N"; opening it lists every file in the day browser; Enter opens a file; Open containing folder works.
- [ ] Ctrl+1 returns to the Gantt view exactly where it was.

## Spell check (R-190)
- [ ] Type a misspelled word in an item's notes: it is underlined; right-click shows spelling suggestions; choosing one replaces the word.
- [ ] Titles, tags and the path bar are not spell-checked.

## Keyboard-only walkthrough (no mouse)
- [ ] Tab reaches toolbar, filter bar, timeline, side panel and status bar in a logical order; the focus ring is visible everywhere.
- [ ] T, Z, Left/Right, Ctrl+Left/Right, PgUp/PgDn, Home/End, Ctrl+= and Ctrl+- move and zoom the timeline.
- [ ] Up/Down and Alt+Left/Right select items; Enter or F2 renames; Delete removes with an Undo notice; Ctrl+Z restores.
- [ ] Ctrl+N, Ctrl+L create items; Ctrl+D opens the day panel; Ctrl+J goes to a date typed as 20261108.
- [ ] Ctrl+F focuses search; Enter / Shift+Enter step through matches; Ctrl+Shift+F clears the filter.
- [ ] Ctrl+1, Ctrl+2, Ctrl+Shift+L, Ctrl+G, Ctrl+B, Ctrl+Shift+H, Ctrl+R, Ctrl+Q and ? work; Ctrl+H and Ctrl+Shift+T do nothing; no browser find bar, print dialog or reload happens.
- [ ] Alt+Shift+Left/Right on several selected items moves them together; one Ctrl+Z undoes all.
- [ ] A screen reader announces the selected item and the item list (R-103), also in the File timeline (R-155).

## Contrast and appearance (all nine themes, R-180 .. R-185)
- [ ] View > Theme lists the nine themes with a check on the active one; there is no theme button and no Appearance section in Settings.
- [ ] In every theme: bar labels are readable on all 16 tag slots (hatched slots show the label on a solid chip); today marker, selection outline, focus ring and minimap frame are clearly visible.
- [ ] Ferrari, SpaceX and Spotify: scroll bars, checkboxes and the text caret are dark-themed.
- [ ] Review the derived tag hues of each theme and confirm or request changes (spec 7.2).
- [ ] 2001 Meridian: a second attempt to drag a locked imported item shows HAL; typing "monolith" in search shows the monolith on today.
- [ ] Window at 125% and 150% Windows text scaling still works.

## Window and safety
- [ ] Resize and move the window, quit, relaunch: size and position are restored.
- [ ] Settings > Back up now writes a backup set folder; Restore makes a safety backup first and asks for no dialog.
