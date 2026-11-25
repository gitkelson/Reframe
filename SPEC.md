# Reframe — Specification

| Item | Detail |
|------|--------|
| Product | **Reframe**: one chronological timeline of everything a person is committed to, expecting, or has done, with a daily record, calendar import and protected file links |
| Role of this document | The single source of truth for what Reframe is, what it must do and how it does it. Code, tests, agent instruction files and install guidance are written to agree with it. A difference between them and this document is a defect in one of them |
| Platform | Desktop application. Python backend (Flask on loopback) with an HTML/CSS/JavaScript front end in its own native window. The reference implementation is Windows (Edge WebView2); macOS is supported through the adaptations listed in §14 and `INSTALL.md` |
| Users | One user per installation. The schema does not preclude sharing later |

---

## 0. How to read this document

### 0.1 Scope

This document describes Reframe completely: purpose (§1), concepts (§2), behavior (§3), layout algorithm (§4), database (§5), user interface (§6), appearance (§7), application behavior (§8), agent operability (§9), technology and architecture (§10), verification (§11), risks (§12), design decisions (§13) and platform support (§14). A developer or an installing agent needs nothing else to build, adapt, test, run or maintain the app.

### 0.2 Requirement identifiers

Every testable requirement has an identifier **R-nn** (or R-nnn). Identifiers are stable names, not a sequence: they are not contiguous and their order in this document follows topic, not number. Code comments and tests cite them. Where a requirement describes a platform-specific mechanism, the Windows mechanism is given as the reference and §14 states the intended behavior on other platforms.

### 0.3 Notation

Paths are written with the Windows reference names (for example `%LOCALAPPDATA%\Reframe\`); §14 gives the macOS equivalents. "The user" is the person who runs Reframe. "An agent" is a chat or coding assistant that reads or writes the database on the user's behalf.

---

## 1. Purpose

Work commitments, expectations and notes are scattered (meeting notes, email, calendars, memory). Dates like "material delivered 11/8" or "spares order due 1/28" are buried in unrelated places and across unrelated projects.

Reframe is **one chronological timeline of everything the user is committed to, expecting, or has done**, so the user can look ahead to plan days and plan long-term with the full picture visible at once. It is also a running record of what was done each day. The timeline ruler also provides a chronological browser for the user's **files**, and files linked to items are protected against later change or loss.

### 1.1 Design principles (in priority order)

1. **Simplicity first.** One kind of thing on the timeline. Few concepts. When two designs work, build the simpler one.
2. **Flat.** All items are treated equally and laid out chronologically. Row position carries no meaning. Meaning comes from tags.
3. **No notion of dueness or completeness.** There is no status, no checkbox, no "overdue". If something slips, the user edits its dates.
4. **Robust.** Local file, no server, every change logged and undoable, automatic backups, nothing is ever silently lost — now including the contents of attached files (§3.7).
5. **Agent-operable.** A chat agent (for example a coding assistant in an editor) must be able to read and modify the database safely, using a documented schema and an instruction file.
6. **A normal, polished desktop app with a modern web look.** Launched like any desktop program, with its own icon. Keyboard shortcuts, tooltips, hover and focus states, no modal pop-ups. It runs in its own window, not a browser tab.

### 1.2 Non-goals

- Built-in AI features or any network calls to an AI service.
- Completion tracking, reminders, notifications, priorities, dependencies between items.
- Multi-user editing, cloud sync, mobile.
- Writing back to Outlook or to calendar feeds (all calendar import is read-only).
- Exchange public folders (not imported).
- Print/export.
- Editing, deleting, moving or renaming files from the file browser; OCR or file-content indexing; uploading files anywhere.
- An embedded PDF viewer.
- Use of the Everything HTTP server or command-line client (§3.8).
- Authenticated calendar feeds (only public or secret-URL ICS feeds, §3.6.2).

---

## 2. Concepts and data model summary

| Concept | Definition |
|---|---|
| **Item** | The only thing on the Gantt timeline. Has a title, a start date, an end date (inclusive, ≥ start; a one-day item has start = end), notes, tags, and optional attachments. Date-only; there is no time of day. Minimum visible length is one whole day. |
| **Kind** | Each item is `plan` (committed to or expected: deadline, delivery, span of work, meeting, holiday, PTO) or `log` (something that happened, recorded for the daily record). This is the only built-in distinction. |
| **Tag** | A label. Items have zero or more tags in an order. The **primary** tag (lowest position) sets the item's color. Tags are the means of filtering and optional grouping into lanes. |
| **Tag group** | A tag may have one **parent tag**, so tags form nested groups like folders (e.g. `Projects ▸ ClientA ▸ Line 2`). A tag with children is still an ordinary tag. Applying a tag to an item automatically applies all its ancestors (as *implied* tags). Tags may be groupless. |
| **Retired tag** | A tag marked as no longer in use (e.g. a finished project or a departed colleague). It stays on the items that carry it but is hidden from the filter bar and from autocomplete. Retirement is reversible. |
| **Origin** | `local` (typed by the user or an agent), `outlook` (imported from classic Outlook) or `ics` (imported from an ICS calendar file or feed). Imported items have locked core fields. |
| **Calendar source** | One Outlook calendar folder or one ICS file/URL. All connected sources are imported; each source is shown on the timeline only when the user switches it on (on-demand viewing). |
| **Meeting, holiday, PTO, deadline, delivery** | Not separate types. They are items distinguished by tags (`meeting` and `holiday` are applied automatically on import) and by shape (one day vs. span). |
| **Day** | A calendar date. Selecting a day opens the day panel (§6.6). |
| **Attachment** | A link from an item to a file, folder or URL. A **file** attachment is pinned to the exact content captured when it was attached; Reframe keeps its own protected copy of every captured version (§3.7). |
| **Attachment version** | An immutable, captured copy of a file's content (identified by content hash) stored in the **vault**. |
| **View** | The main window shows the Gantt timeline; its ruler opens a file browser for the hovered period. The file browser has its own filters and selection. |
| **Layout mode** | The Gantt view packs rows either **compact** (several non-overlapping items share a row) or **strict Gantt** (one item per row, each later-starting item one row lower). |
| **File entry** | A file discovered for the timeline browser. Read-only, session-only, never stored as an item. |

There are **no tracks, no projects table, no status, no recurrence engine, and no dependencies.** Recurring items come only from calendar imports and are stored as individual occurrences.

---

## 3. Functional requirements

Requirement IDs: **R-xx**. Each is testable.

### 3.1 Gantt timeline (main view)

| ID | Requirement |
|---|---|
| R-01 | Time runs left to right. Items are drawn as horizontal bars (spans) or one-day bars, stacked vertically in rows. |
| R-02 | **Row packing.** Rows are assigned by a deterministic algorithm (§4.1) in the active layout mode (R-17). Rows are geometry, not categories. In compact mode, when no outside labels are reserved, total rows equal peak concurrency, not total item count. In strict Gantt mode, total rows equal the number of visible items (per lane when grouped). |
| R-03 | **Today is shaded, not lined.** The whole day cell for today is tinted in the chart background, behind the bars (no line is drawn over the chart), and a small marker on the ruler points at it. The default window on launch is about 6 weeks, with about 1/4 of the window before today and 3/4 after. The today shading and "today" update at local midnight while the app is running. |
| R-04 | Past items look identical to future items (no dimming). Only the today shading distinguishes them. |
| R-06 | The time ruler adapts to zoom: years, quarters, months, weeks (US week numbers; weeks start on Sunday), days. Weekends are lightly shaded as a time reference. No other day shading exists: holidays and PTO are ordinary items and appear as bars. |
| R-07 | Zoom range in the Gantt view: the minimum visible window is 7 days; the maximum is 5 years. Zoom is anchored on the cursor position. |
| R-08 | **Mouse map.** Ctrl+wheel zooms (anchored on the cursor). Plain wheel scrolls rows vertically. Shift+wheel pans horizontally. Wheel over the ruler or the minimap zooms. Panning also by middle-mouse drag or Space+drag, arrow keys, and Home/End. |
| R-09 | Items show a label inside the bar when it fits, to the right of the bar when there is room and zoom allows it (§4.3), and otherwise no label (the tooltip supplies it). |
| R-10 | Bar color is the primary tag's color. Items with more tags show small tag dots at the left end of the bar when wide enough. Untagged items use the neutral item color. |
| R-11 | Click selects an item. Shift+click extends the selection as a range and Ctrl+click toggles one item (a Ctrl+*drag* instead copies tags onto another item, R-48, or duplicates the item onto blank space, R-49). Selecting opens the item side panel (§6.5). |
| R-12 | Dragging a bar's body moves it (snaps to whole days). Dragging a bar's left or right edge changes start or end. A one-day item can be dragged to extend it. Esc cancels a drag in progress. |
| R-13 | Dragging on empty space with the left mouse button creates a new item spanning the dragged days, then puts its title into inline edit. |
| R-14 | Double-click on a title (or F2 on a selected item) edits the title inline. Enter commits, Esc cancels. |
| R-15 | **Group by tag** (view option, **on by default**; the choice is remembered): rows are grouped under lane headers, one per item's **primary** tag, with an "untagged" lane. Rows are packed within each lane in the active layout mode. This changes the view only, never the data. |
| R-16 | Imported items (`outlook` or `ics`) cannot be moved, resized or retitled. Attempting to do so shows an inline hint naming the source ("Imported from Outlook. Change it there, then Sync." / "Imported from calendar feed *name*. Change it at the source, then Sync."). Tags, notes and attachments remain editable. |
| R-17 | **Layout mode switch.** A two-state toolbar control and View-menu entry switch the Gantt view between **Compact** (first-fit packing: items share a row when they do not overlap) and **Strict Gantt** (every visible item has its own row; rows are ordered by the §4.1 sort key, so an item that starts later is always on a lower row than one that starts earlier). Shortcut Ctrl+Shift+L. The choice is remembered (R-98). Switching re-packs instantly and keeps the selected item at the same screen height. Both modes honor group-by lanes (R-15), filters (R-31) and calendar visibility (R-131). |
| R-18 | **Ruler file browser.** Hover over either ruler row for 0.8 seconds to open an opaque file browser over the Gantt chart for that exact ruler segment. Moving to a different segment before opening restarts the delay; once open, horizontal movement on the same row updates the period immediately. An upper-row browser remains open when crossing the lower row; moving from a lower-row browser to the upper row closes it and requires a new delay. Leaving the browser and ruler closes it. Ctrl+2 or View ▸ Browse files opens the center lower-row segment for keyboard use. There is no separate Files view; legacy saved active-view settings are ignored. |

### 3.2 Overview minimap

| ID | Requirement |
|---|---|
| R-20 | A strip at the bottom of the window shows the Gantt data range from the earliest item to the latest, padded to at least today − 6 months and today + 2 years. |
| R-21 | **The minimap is a miniature of the main field.** It draws the **same rows, in the same order, with the same lanes and layout mode** as the main chart (R-02, R-15, R-17): same packing output, scaled to the strip's height. Lane labels and separator bars are omitted. When there are more rows than the strip has device pixels, consecutive rows share a pixel row (bars still drawn in their own colors); when there are too many bars to resolve horizontally, the strip switches to a density histogram stacked by primary-tag color, computed per lane in the same order. **Each bar has the same color as in the main chart** (primary-tag fill; hatched slots hatched; untagged neutral), drawn as plain fill with no border, outline or halo. The minimap redraws on every event that repacks the main chart (data, filter, calendar visibility, group-by, layout-mode and zoom-band changes) in the same animation frame. |
| R-22 | Dragging the frame body pans the main view. Dragging a frame edge resizes the frame, which zooms the main view. Clicking outside the frame re-centers it there. Wheel over the minimap zooms the frame. |
| R-23 | The frame and main view are bound in both directions with no lag or feedback oscillation. |
| R-24 | The minimap shows exactly the item set of the main chart: it respects the active filter (R-30), calendar visibility (R-131) and the active view. |
| R-25 | The frame has a minimum width (about 12 px) so its handles stay usable at the 7-day zoom. The main view and the minimap read and write one shared view model per view (visible start, visible end), which makes R-23 testable. The vertical extent of the main chart's viewport is also outlined on the minimap frame when the chart is scrolled vertically. |

### 3.3 Filtering and search

| ID | Requirement |
|---|---|
| R-30 | A filter bar offers: tag chips (multi-select, with an AND/OR switch), **All** and **None** buttons (R-34), free-text search (title, notes, tags), kind (plan / log), origin (local / Outlook / calendar feed), and a **Calendars** control (R-131). The tag chips **wrap onto as many lines as they need** (the bar grows taller); they never scroll sideways or hide behind a scroll bar. **Retired** tags (R-113) have no chip unless they are part of the active filter, in which case they stay visible, marked retired, until deselected. Chips of grouped tags are shown in tag-tree order with the group path in the tooltip. |
| R-31 | Applying a filter **hides** non-matching items and **re-packs** rows so the remaining items are compact (in the active layout mode). |
| R-32 | An active filter is clearly indicated (filter bar highlighted, badge with match count). One click clears it. |
| R-33 | Ctrl+F focuses the search field. Enter/Shift+Enter jump to the next/previous match in time order and center it. Matches in hidden calendars (R-131) are skipped, and the status bar says how many matches are in hidden calendars. |
| R-34 | **All / None.** **All** selects every non-retired tag chip; **None** deselects every chip (no tag filter). With all chips selected the user deselects the few tags to leave out. The existing AND/OR semantics apply unchanged to whatever is selected (there is no separate exclude mode). So that untagged items are not lost, the chip row ends with an **Untagged** pseudo-chip that matches items with no tags (it is disabled in AND mode, where it could never match together with another chip); All selects it and None clears it. The tooltip on All explains that in OR mode an item stays visible while *any* of its tags is still selected. Both buttons are keyboard operable, are one filter change each, and are remembered with the filter (R-98). |

### 3.4 Tags

| ID | Requirement |
|---|---|
| R-40 | Tags are free-form, created by typing. |
| R-41 | **Duplicate prevention.** Each tag has a normalized key: lower-cased, every run of punctuation and whitespace replaced by a single space, trimmed. The formula is computable in plain SQL (nested `lower`/`replace`/`trim`), so the `UNIQUE` constraint also blocks duplicates created by an agent writing SQL. Typing a tag whose key matches an existing tag silently resolves to the existing tag ("Spares", "spares", "SPARES!" are one tag; "PRJ-20" and "PRJ 20" are one tag). Plurals, word order and typos are never merged silently; they appear as visible suggestions (R-42). Keys are unique across the whole tag tree (a tag name cannot repeat in two groups). |
| R-42 | **Autocomplete.** While typing, an inline list shows existing **non-retired** tags by prefix and fuzzy match (typos, reordered words), each with its group path. A near-match (similarity above the fixed threshold 0.85, `difflib`) is shown as an inline suggestion ("Use *Spares Order*?") before a new tag is created. Typing the exact name of a retired tag offers "Un-retire *name*" rather than silently reusing it. No dialog. |
| R-43 | A tag manager (side panel **Tags** tab, not a dialog) shows the **tag tree** (R-110) with usage counts and supports: create group/tag, rename, merge into another tag, delete (only if unused, or merge/remove from all items), change color, **move** (drag a tag onto another tag to make it a child; onto the root area to make it groupless; keyboard: *Move to…* with a type-ahead group picker), **retire / un-retire** (R-113), and expand/collapse of groups (remembered). Retired tags are listed in a collapsed **Retired** section. Merge, rename, move and retire are each a single undoable change set. |
| R-44 | Tag colors are assigned automatically from the active theme's color set (§7.2) and can be overridden. Overrides are stored per tag as a color *slot* index (not a hex), so switching themes keeps tag identity coherent. |
| R-45 | Calendar import applies system tags: `meeting` for appointments, `holiday` for items from sources the user marks as holidays. They are ordinary tags (can be renamed, re-colored, grouped) and are **inserted after** the item's other tags (`item_tag.position` ≥ 1000), so a project tag the user adds sets the bar color and an untouched meeting is colored by `meeting`. The user may reorder them like any tag (R-114). |
| R-46 | A startup tag-hygiene check lists near-duplicate tags (e.g. created by an agent writing SQL directly) and items whose implied group tags are inconsistent with the tag tree (R-111), and offers merge / repair in the tag manager. |
| R-47 | **Tags on imported recurring meetings apply to the whole series.** Adding, removing or reordering a tag on any occurrence of an imported series (Outlook or ICS) applies to every existing occurrence and is recorded in `outlook_series_tag` (the table name is kept for agent compatibility; ICS series ids are stored in the same table, §5.2); import copies the series tags onto new occurrences. Notes and attachments stay per occurrence. A single-occurrence meeting is tagged individually. `item_tag` remains the single source of truth for rendering, filtering and agents. |
| R-48 | **Copy tags between items (Ctrl+drag onto another item).** Holding Ctrl and dragging from a bar onto another bar copies **all** of the source item's explicit tags to the target. The target keeps its own tags first; the source's tags it lacks follow in the source's order (their implied ancestors follow automatically, R-111), so an untagged target ends up with the source's tags and the same primary tag. The source is unchanged. While dragging, the source is outlined, the bar under the pointer is outlined as the drop target, and a chip at the pointer shows "Copy *n* tags"; the cursor shows a copy cursor over a valid target. Esc cancels; releasing on the source does nothing; a source with no tags reports there is nothing to copy. A Ctrl+click without movement still toggles the selection (R-101). Imported items may be targets (series rule R-47 applies). Keyboard path: **Copy tags** (Ctrl+Alt+C) and **Paste tags** (Ctrl+Alt+V), also in the bar context menu. Each drop or paste is one change set labelled "Copy tags from 'A' to 'B'" and one undo, confirmed in the status bar with an Undo button (R-84). |
| R-49 | **Duplicate an item (Ctrl+drag onto blank space).** The same Ctrl+drag, released on blank chart space, creates a **duplicate** at the dropped position and puts its title straight into inline edit with the placeholder name selected. The duplicate copies everything except the title: kind, length in days, notes, tags in order (system tags become ordinary tags at their current order) and attachment links **pinned to the same versions** as the source's links (R-161). Its start is the source's start shifted by the whole days dragged; it lands in the row under the pointer and is then packed normally. The duplicate is always local and fully editable. While dragging over blank space a translucent preview shows where it will land and the chip reads "Duplicate". Esc cancels; dropping on a lane header or the ruler does nothing. Keyboard path: **Duplicate** (Ctrl+Shift+D, also in the context menu). One change set "Duplicate 'A'", one undo; Esc in the name field keeps the placeholder-named copy. |
| R-110 | **Tag tree.** Each tag has at most one parent tag. Any depth is allowed; cycles are impossible (enforced by the database, §5.2). A tag with children is a **group tag** and is still an ordinary tag that can be applied, filtered, colored and retired. Tags created by typing in the item panel, the day panel, the multi-edit panel or by an agent that does not set a parent are **groupless** (no parent); they are placed in a group only from the tag manager or the helper's `move-tag` command. Deleting a group tag (when allowed by R-43) re-parents its children to the deleted tag's parent. |
| R-111 | **Implied group tags.** Whenever a tag is applied to an item (by any path: UI, bulk edit, tag copy, chip drop, import series tags, helper or raw SQL), every ancestor of that tag is also applied to the item as an **implied** tag, unless the item already carries it. Implied tags are stored as ordinary `item_tag` rows flagged `implied = 1` (§5.2), so rendering, filtering, `v_item` and agents see them like any tag. New implied tags are positioned after the item's explicit tags and before system tags. When an explicit tag is removed, each implied ancestor that no remaining tag on the item requires is removed too; an ancestor the user applied explicitly (`implied = 0`) is never removed automatically. An implied tag is shown in the item's chip editor with a subdued style and the tooltip "Added by group *child*"; removing it while a descendant remains is refused with the inline hint "Remove *child* first, or move it out of the group." The rules are enforced by database triggers so that agents writing SQL get the same result (§5.3). |
| R-112 | **Moving a tag between groups updates existing items.** Moving tag *T* (changing its parent) recomputes, for every item carrying *T* or any descendant of *T*: implied ancestors from the old path that are no longer required are removed; ancestors on the new path that are missing are added as implied. Explicitly applied tags are never removed. The whole move, including every item change, is **one** change set ("Move tag 'T' to 'G'") and one undo. The tag manager shows the number of affected items before the drop completes (in the drag chip, not a dialog). |
| R-113 | **Retired tags.** Retiring a tag sets `tag.retired_at`. A retired tag: stays on every item that has it and keeps its color; is drawn on its items unchanged (bar color, tag dots, chips — chips carry a small "retired" marker); has no filter-bar chip (R-30) and is excluded from autocomplete (R-42); is excluded from **All** (R-34); cannot be newly applied from the UI (the helper refuses unless `--allow-retired`); is listed in the tag manager's Retired section with its usage count and an **Un-retire** action. Retiring a group tag retires only that tag; its children remain as they were and are shown under it in the Retired section only if they are also retired. Retire and un-retire are change-logged and undoable. |
| R-114 | **Any tag can become the primary tag.** In the item panel's chip editor every tag — explicit, implied or system (`meeting`, `holiday`) — can be dragged (or moved with Alt+Left/Right on a focused chip) to any position, and the resulting order is stored exactly as shown; the first chip becomes the primary (color) tag. User reordering is stored as plain positions `0..n-1`; the ≥ 1000 range is only an *insertion default* for newly imported system tags (R-45), and import never re-positions a tag the user has ordered. For an imported series the new order applies to the whole series (R-47). A regression test reorders a system tag, an implied tag and a tag on a series occurrence. |
| R-115 | **Drop a filter chip onto an item.** Dragging a tag chip from the filter bar and releasing it over a bar applies that tag to the item (and to every selected item when the bar under the pointer is part of a multi-selection). While dragging, the target bar is outlined and the pointer chip reads "Add tag *name*" (or "Add tag *name* to *n* items"). The tag is appended after the item's explicit tags (it does not change the primary tag); its implied ancestors follow (R-111). If the item already has it explicitly, the drop reports "Already tagged". Dropping onto an imported series applies it to the series (R-47). Dropping anywhere else, or Esc, cancels; a click without movement still toggles the chip's filter state. One change set ("Add tag 'X' to 'A'"), one undo, status-bar confirmation with Undo. Retired chips (when shown because they are in the active filter) cannot be dragged. |

### 3.5 Daily log

| ID | Requirement |
|---|---|
| R-50 | A log entry is an item with `kind = 'log'`. Typically a single day, but may span days. It is drawn like any other item (flat model) with a distinct outlined treatment so it is distinguishable from plan items without changing layout rules. |
| R-51 | Ctrl+L creates a log entry for the selected day (default today) and enters inline title edit. The day panel also provides "Add log entry". |
| R-52 | The day panel shows, for the selected day, all visible items overlapping it, grouped as "Log" and "Plan". |
| R-53 | There is no completion link between plan and log items. The user records what happened as new log items. |

### 3.6 Calendar import (read-only)

#### 3.6.1 Calendar sources and on-demand viewing

| ID | Requirement |
|---|---|
| R-130 | **Calendar sources.** Each Outlook calendar folder and each ICS file or URL is one row in `calendar_source` (§5.2) with: kind, display name, location (Outlook folder path, file path or URL), holiday flag, **connected** flag (imported or not) and **visible** flag (shown or not). Sources are managed in the Settings tab (§8.3). |
| R-131 | **All connected, viewed on demand.** By default **every** calendar source is connected: every Outlook calendar folder the profile exposes (except public folders) and every ICS source the user adds. New Outlook folders discovered during a sync are connected automatically and announced in the status bar ("2 new calendars connected, hidden"). Each source's items are imported and kept current whether or not it is shown. **Visibility is on demand:** a newly connected source is **hidden**; the user shows or hides each source from the filter bar's **Calendars** control (a drop-down checklist of sources with item counts, plus Show all / Hide all) or from Settings. Hidden sources' items are excluded from layout, minimap, search, counts and the day panel, exactly like a filter, and the Calendars control shows "*n* of *m* calendars shown" while any connected source is hidden. Local items are never hidden by this control. Visibility is a view setting, not logged in the change log. |
| R-132 | A source can be **disconnected** in Settings. Disconnecting stops importing it and soft-deletes its items as "removed: calendar disconnected" in one change set (undoable; local tags, notes and attachments remain recoverable through History). Reconnecting re-imports and, through the stable keys (R-63, R-136), restores the same item rows with their annotations. |
| R-133 | Imported items show their **source** by name and location in the side panel. An imported item that has identical siblings in the same source (same title and dates) says so and offers Hide. |
| R-134 | Sync runs on launch (if enabled), on demand ("Sync now", Ctrl+R; also from the tray menu), and on each source's refresh interval (ICS URLs: default 60 min, setting). Sync runs on background threads, never blocks the UI, and reports progress and results per source in the status bar ("Outlook: 12 added, 3 updated, 1 removed · Team feed: up to date"). The first full sync of many connected sources can take minutes (about 19 ms per Outlook item was measured; 15 calendars with 3,412 items took 126 s); the app is fully usable meanwhile and already-imported data stays visible. |

#### 3.6.2 ICS calendar files and feeds

| ID | Requirement |
|---|---|
| R-135 | The user can add an ICS source by **file path** (local or network; chosen with the native file chooser or pasted) or by **URL** (`https://`, or `webcal://` which is fetched as `https://`). Plain `http://` is accepted only after an inline warning. Authenticated feeds are not supported; a secret-URL feed is treated as a credential: it is stored only in the local database, masked in the UI (host shown, path elided) and never written to logs. |
| R-136 | **ICS identity.** Each occurrence is upserted by `ics_uid` = *source id* + `|` + the event `UID` + `|` + the occurrence's **original** start (`RECURRENCE-ID` for an overridden instance, else the occurrence's DTSTART), so a moved occurrence keeps its local annotations. Events without a UID use a hash of (summary, DTSTART, DTEND) as a fallback, flagged in History as weakly keyed. The series id is *source id* + `|` + `UID`. |
| R-137 | **ICS date mapping.** Recurring events (RRULE, RDATE, EXDATE, overridden instances) are expanded over the same window as Outlook (R-62). Timed events with a `TZID` or UTC time are converted to the machine's local time zone before taking dates (unlike Outlook COM, ICS times carry real zone information); floating times are taken as local wall-clock. All-day events (`VALUE=DATE`) use an exclusive DTEND, so `end_date` = DTEND − 1 day; a missing DTEND means one day. Timed events use the same end rule as R-64 (End − 1 second, so a 00:00 end does not spill). `STATUS:CANCELLED` occurrences are treated as removed. |
| R-138 | ICS removals follow R-65 per source: an event inside the window that the source no longer contains is soft-deleted as "removed by sync"; the >20 % / ≥3 items guard applies per source; a source that could not be read completely (network error, parse error, HTTP status other than 200/304) causes no removals and reports the reason inline. URL fetches use `ETag`/`Last-Modified` and a 30 s timeout; a 304 is "up to date". |
| R-139 | ICS imports apply the same system tags (`meeting` for timed events, `holiday` when the source is marked holiday), the same series tagging (R-47), hiding (R-66) and locking (R-16) as Outlook. |

#### 3.6.3 Outlook (classic desktop, COM)

| ID | Requirement |
|---|---|
| R-60 | Source: **classic desktop Outlook** through COM automation (`pywin32`). The "new Outlook" and Outlook on the web are not supported. No Azure registration or Graph API is used. |
| R-61 | Reframe lists every calendar folder Outlook exposes (mailbox, shared, **Microsoft 365 group calendars**, holidays, birthdays), each with its folder path because names can repeat, and connects all of them by default (R-131). The user marks sources as "holidays". Group calendars are connected even when they currently return no items. **Exchange public folders are out of scope** and are never walked. A connected calendar that returns 0 items is reported inline ("Group calendar returned 0 items") and never causes removals by itself. |
| R-62 | Sync window: **recurring series** are expanded into occurrences over [today − *lookback*, today + 730 days] (lookback default 365 days on first sync, 30 thereafter). **Non-recurring** items are fetched with no upper date bound. |
| R-63 | Each occurrence is upserted by `outlook_uid` = *calendar id* + `|` + series id + `|` + the occurrence's **original** start date. The series id is the GlobalAppointmentID, shared by every occurrence of a series and carrying no date. For an ordinary occurrence the original date is its own Start date; for an *exception* (moved or edited) occurrence it is read from the series' `RecurrencePattern.Exceptions` by matching the exception's moved Start/End. Fallbacks: if the GlobalAppointmentID is empty, or several **non-recurring** items share a GlobalAppointmentID and start, use the item's EntryID. A non-recurring item with a unique GlobalAppointmentID is keyed `calendar|gaid` without a date so a move updates it in place. The same meeting in two calendars stays two items (calendar id is part of the key). If the original date of an occurrence cannot be resolved, sync falls back to remove + add, and the old item's annotations remain in History. Fields overwritten on sync: title, start date, end date, `outlook_time`, `outlook_location`. Local tags, notes and attachments are never overwritten. Imported items are `kind = 'plan'`. Everything is imported (free, tentative, declined) except cancelled meetings, which are treated as removed (R-65). |
| R-64 | **Date mapping.** `pywin32` returns COM dates with a misleading `tzinfo` label although the values are local wall-clock time; sync **strips `tzinfo` and never calls `astimezone()`**. All-day events: Outlook stores an exclusive end at the next midnight, so `end_date` = end − 1 day. Timed meetings: `start_date` = local date of Start; `end_date` = local date of (End − 1 second) when End > Start, otherwise the start date. Timed meetings crossing midnight span every date they touch. |
| R-65 | **Removals.** An imported item inside the sync window that its source no longer returns is soft-deleted (`deleted_at`) and listed in History as "removed by sync". If it reappears, sync clears `deleted_at`. Removals are applied only for sources read completely in this run. A disconnected source causes removals only through R-132. If at least 3 items **and** more than 20 % of a source's in-window imported items would be removed in one run, sync skips that source's removals and reports why inline. Cancelled meetings are treated as removed. |
| R-66 | The user can **hide** an imported item or an entire series. Hiding sets `hidden_at` on the item and, for a series, records it in `outlook_hidden_series` (also used for ICS series ids). Sync never clears `hidden_at`, and new occurrences of a hidden series are created hidden. Hidden items can be restored from History. Hidden (per item) is distinct from removed (R-65) and from a hidden *calendar* (R-131). |
| R-67 | Outlook sync runs on its own thread with COM initialised on that thread. Its scheduling and reporting follow R-134. |
| R-68 | If Outlook is not installed, not running, or blocked by a security prompt or policy, the app continues to work with existing data and shows a non-blocking message with the reason. ICS sources sync independently. |
| R-69 | The pre-sync state is protected by a backup (§8.4) and each sync run is one change set (`actor = 'sync'`, label naming the sources), so a bad sync can be reverted in one step. |

### 3.7 Attachments, notes and file versioning

| ID | Requirement |
|---|---|
| R-70 | Notes are plain text (Markdown-compatible, rendered as plain text) with no length limit. Spell checking in notes follows R-190. |
| R-71 | Attachments are **links**: a file path (local or network), a folder path, or a URL, with an optional label. The side panel lists each by **file name only** (last path segment for a URL) with the full path or URL in a tooltip, plus its protection state (R-160). A broken-link check runs asynchronously with a 2-second timeout per link and never blocks the UI. Folder links and URLs are visibly marked **"not version-protected"**. |
| R-72 | Drag-and-drop of a file or URL onto an item (or the side panel) adds an attachment link. Files may also be dragged from the ruler file browser onto a Gantt bar or the selected item's side panel; the browser closes when the drag leaves it. Indexed files are revalidated against current roots and existence before attaching, and use the normal capture and undo flow. The side panel's **path bar** (R-120) also adds links by paste, typing or Everything search; a "Choose file…" button opens the native chooser. Click opens the attachment per R-162. |
| R-160 | **Protection on attach.** When a file link is added (any path: UI, duplicate, agent helper, raw SQL picked up by the reconciler), Reframe captures a stable copy of the file's current content into the **vault** and **pins** the link to that captured version. A link is shown as *protected* only after the captured content and its metadata are durably committed; until then it shows *capturing…*, and on failure *not protected* with the reason (locked, missing, permission denied, network unavailable, disk full, changed during read) and automatic retry. Attaching the same file three times across edits (A, then B, then C) gives three links pinned to A, B and C respectively. The original file and its folder are never modified, and no Git or other metadata is added next to it. |
| R-161 | **Content identity.** Versions are identified by SHA-256 of the content. Identical content is stored once (whole-file deduplication at minimum; chunk-level deduplication if the chosen backend provides it, §10.3). Several links may share a version; a version is a separate record from the links that pin it. Duplicating an item (R-49) pins the copies to the same versions. |
| R-162 | **Opening a pinned file.** Before opening, Reframe hashes the reachable original (modification time alone is never trusted). If it equals the pinned version, the original opens with the default application. Otherwise Reframe materializes the pinned version to a temporary read-only file with the original's name and a safe extension, verifies its hash, opens it, and shows an inline notice in the item panel: "Opened the saved version from *date*. The file has changed since it was attached." with actions **Open current file** (when reachable), **Open latest saved version** (when it differs from the pinned one) and **Restore a version to…** (native save chooser; never overwrites without the chooser). "Newest" is defined in the notice: *current file* means the reachable original; *latest saved* means the most recent captured version. A missing original goes straight to the saved version with the same notice. Edits to a temporary copy never change the vault or the original. Temporary copies are cleaned up on exit and at next launch. |
| R-163 | **Automatic version capture.** While Reframe runs (including hidden in the tray), it watches only files that are attached to at least one non-deleted link (never whole folders), debounces bursts of writes, and captures each new stable version (content unchanged across a stability re-read). On launch, and after watcher errors, overflow or network reconnection, it reconciles every attached file and captures any changed content that still exists. Capture is best effort: the UI and docs state plainly that edits made while Reframe is not running, or overwritten before capture, may not be preserved. Each attachment's panel shows *pinned version time*, *last check time*, *last successful capture* and any protection gap. |
| R-164 | **Version history.** The item panel's attachment row expands into the file's version list (capture time, size, which links pin it). The user can open any version (as in R-162) or restore it to a chosen location. Versions are kept indefinitely. |
| R-165 | **Removal keeps versions.** Removing a link or deleting an item never deletes vault content; undo restores the link with its pin. Vault content is deleted only by an explicit user action in Settings ("Delete versions no longer referenced by any link or undo history…", showing count and size and confirmed inline by a second button press — no modal), and only for versions that no link, soft-deleted item or change-log snapshot references. |
| R-166 | **Vault location and usage.** The vault lives in a configurable local folder (default `vault\` inside the data folder; must not be inside a OneDrive or other synchronised folder, checked and warned). Settings shows total stored size, number of versions, deduplication savings, and lists files that could not be protected. Changing the location copies and verifies all content before switching, and keeps the old copy until the user removes it. |
| R-167 | **Coordinated backup.** Each backup (§8.4) is a matched set: the database copy plus the vault content it references, verified for integrity, so a restored database never references missing vault content. Backup and restore failures name the files or versions involved. |
| R-169 | Web URLs and folder links are never captured. A URL opens in the default web browser (only `http`, `https` and `mailto` addresses are opened); a folder link opens in the file manager. |

### 3.8 Attachment path bar with Everything search

| ID | Requirement |
|---|---|
| R-120 | The item panel's **Add link** field is a **path bar** with two modes chosen from what is typed: **path mode** when the text looks like a location (starts with a drive letter and `:\`, `\\`, `http://`, `https://`, `webcal://`, `file:`, or is wrapped in quotes); otherwise **search mode**. In path mode Enter adds the link. |
| R-121 | In search mode, after a debounce (initially 200 ms, tuned by measurement), the text is sent as an Everything query and a result list opens under the bar, showing for each result: file name, containing folder, size and modified date (blank when unavailable — never a false 0 or invented date). Up/Down move through results, Enter or click **puts the full path into the path bar** (switching it to path mode, not yet adding it); a second Enter adds the link. Esc closes the list and keeps the text. |
| R-122 | The list shows at most one page (initially 100 results) with the total match count when the adapter reports it, and a "Show more" row that fetches the next page. Results are sorted by date modified, newest first (the user can switch to name). Everything query syntax (e.g. `ext:pdf`, quoted paths) is passed through deliberately: advanced expressions are a feature. |
| R-123 | Each request carries a request id; a reply for a superseded request is discarded so fast typing can never show stale results. |
| R-124 | **Distinct failure states** shown inline under the bar, never as an empty list: *Everything is not running* (with a "Start Everything" action that launches the installed `Everything.exe` minimized), *Everything is still indexing*, *search timed out*, *search component missing* (SDK DLL not found), *unexpected error*, and a genuine *No matches*. Path mode keeps working in every state. Recovery after Everything restarts needs no Reframe restart. |
| R-125 | Results are discovery only: when a selected path is added or opened, Reframe re-checks that it exists and is accessible (junctions, removed files and network paths may have changed since indexing). |
| R-126 | Integration route: the **Everything SDK** called from Python `ctypes` on a dedicated worker thread, with the whole reset → set search → set max/offset/sort/request flags → `Everything_QueryW(TRUE)` → read counts → copy results sequence serialized; full paths fetched with the length-query-then-allocate pattern; results copied into Python objects before the next query. The 64-bit DLL matching the 64-bit interpreter is bundled in `vendor\everything\` with its licence notice and recorded version; it is loaded by absolute path, never from the current directory. The ES command-line client and the HTTP server are not used. |
| R-127 | Only the Flask backend talks to Everything; the web page calls the authenticated local API (`GET /api/search/files`), never the DLL or a process directly. |
| R-128 | Settings shows the Everything status (running/not, Everything version, SDK version, index ready) and an **Enable Everything search** toggle (default on). Disabling it leaves the bar in path-only mode. Reframe never installs, configures, or changes the Everything installation or its index. |

### 3.9 Undo, history, and safety

| ID | Requirement |
|---|---|
| R-80 | **Every** change to items, tags (including parent and retirement), tag assignments (including implied tags) and attachments (including pins) is recorded in the change log with a timestamp, actor (`user`, `agent`, `sync`, `undo`, or `external`) and a before/after snapshot. This holds for changes made by direct SQL from outside the app (§5.3). Vault version records are immutable and append-only and are not part of undo (R-165). |
| R-81 | **Undo/redo rules.** Ctrl+Z reverts the newest change set that is not itself an undo set and is currently in effect. Ctrl+Y (or Ctrl+Shift+Z) reverts the newest undo set made since the last non-undo change set. History is append-only. Inside a text field, Ctrl+Z/Ctrl+Y are text undo/redo. Ctrl+Z may target a change set made by anyone; the status bar names what was undone ("Undid: Outlook sync, 12 items"). |
| R-82 | A History panel lists change sets (time, actor, label, row count) and allows reverting any single change set. If any affected row has changed since, the revert is refused and the conflicting rows are listed inline. |
| R-83 | Deleting an item is a soft delete. Deleted items are not shown but are recoverable via History. |
| R-84 | There are **no modal dialogs** for normal operations. Confirmations are replaced by undo: the status bar shows an inline notice with an Undo button for 8 seconds. This overrides any "confirm destructive action" convention in the palette skills. |
| R-85 | When another process (an agent) changes the database while the app is open, the app refreshes within 2 seconds (server polls `PRAGMA data_version` and pushes an event), preserving view, selection, zoom and scroll, and briefly highlights changed items. A refresh repacks rows but keeps the selected item at the same screen height. |

### 3.10 Appearance

| ID | Requirement |
|---|---|
| R-90 | **Theme selection lives only in View ▸ Theme ▸ *(theme list)*.** The submenu lists the nine themes of R-180 with a check mark on the active one. Switching is instant and persisted. There is no theme button in the toolbar and no Appearance section in Settings. |
| R-91 | Every color in the UI and on the canvases comes from a theme token (§7). No hard-coded colors outside theme files, except the tray/window icon artwork (R-174). |
| R-180 | **Themes.** Nine themes, in this menu order: **2001 Meridian**, **Industrial Catalog** (McMaster-Carr-inspired), **Claude**, **Dell 1996**, **DJI**, **Ferrari**, **SpaceX**, **Spotify**, **IBM**. Each is one token file mapped to the semantic roles in §7.1, derived from its design skill file; none uses another company's logo, wordmark or proprietary assets. Ferrari, SpaceX and Spotify are **dark** themes; the app sets `color-scheme` accordingly so native controls, scroll bars and the WebView text caret follow. |
| R-181 | Each theme defines its own 16 tag color slots (§7.2) so tag colors stay distinguishable and legible on that theme's canvas, including the dark ones. Tag slot indices are shared across themes (R-44). |
| R-182 | Each theme meets the contrast targets of §7.3; the automated contrast test covers all ten. |
| R-183 | Fonts are bundled locally (no CDN). Only fonts under an open licence are bundled; where a design names a proprietary typeface, the theme uses the free substitute named by its skill file, or a documented open-licence substitute, falling back to Segoe UI (§7.4). |
| R-184 | **Easter egg — HAL.** With the 2001 Meridian theme active, the **second consecutive** attempt to move, resize or retitle a locked imported item (no other action in between, within 10 s) replaces the usual locked-item notice with a small glowing red HAL eye drawn from theme tokens and the line "I'm sorry, Dave. I'm afraid I can't do that." for 4 s. It is announced to the live region as text, blocks nothing, writes nothing, makes no sound, and appears at most once per minute. In every other theme the normal notice is shown. |
| R-185 | **Easter egg — Monolith.** With 2001 Meridian active, typing exactly `monolith` (case-insensitive) in the search field draws a black monolith standing on today's day cell for 4 s, with a faint rising "sunrise" glow behind it; with `prefers-reduced-motion` it appears without animation. The search otherwise behaves normally (matches, counts). Nothing is written to the database. |

### 3.11 Usability, launch and window behavior

| ID | Requirement |
|---|---|
| R-95 | Every editing operation has a keyboard equivalent (§6.7). Single-key shortcuts (T, Z, ?, Delete, Enter, arrows) are active only when the timeline has focus, never while typing or while the file browser has focus. The app captures its shortcuts with `keydown` handlers that call `preventDefault()` so they override WebView2 browser accelerators (Ctrl+F, Ctrl+R, Ctrl+N, Ctrl+L, Ctrl+D, Ctrl+H, Ctrl+J, Ctrl+B, Ctrl+G, Ctrl+2, Ctrl+Q, F5, Ctrl+P); any combination that cannot be overridden is remapped and the remap recorded in §6.7. |
| R-96 | Hover tooltips on bars (title, date range, duration in days, tags with group paths, origin and source, notes excerpt, attachment protection summary), on toolbar buttons (with shortcut), on ruler dates, and on minimap regions. Tooltips appear after 400 ms. |
| R-97 | Visible hover, pressed, focus and disabled states on every interactive element. Cursors reflect actions (resize arrows on edges, grab hand on pan, pointer on links, copy cursor on valid drops). |
| R-98 | State persistence between launches (stored in the `setting` table; window geometry via the host): window size and position, theme, Gantt zoom window, filters and selection, layout mode, group-by (default on), calendar visibility, side panel width and tab, tag-tree expansion, tray and startup options, and file browser filters, roots and options. |
| R-99 | The status bar shows: date under the cursor, item count (visible / total), sync status per source, capture status summary ("3 files waiting to be protected"), and the database path on hover. |
| R-100 | **Date entry.** Typed dates are accepted wherever a date is edited and in Go to date (Ctrl+J), in ISO form with or without dashes: `2026-11-08` or `20261108`. Anything else is rejected inline. |
| R-101 | **Multi-select.** Shift+click range, Ctrl+click toggle, Shift+drag marquee, Ctrl+A all visible. With several selected: drag or Alt+Shift+Left/Right moves all by the same number of days; Delete deletes them; the side panel shows a multi-edit view ("mixed" where values differ) with editable tags (add/remove for all) and kind. Locked items are skipped for date moves and delete (reported), but tags can be added. Each bulk action is one change set and one undo. |
| R-102 | **Single instance.** Launching a second copy (shortcut, double-click, Startup entry) focuses the running window — showing it if it is hidden in the tray — and exits. |
| R-103 | **Accessibility of the canvas.** The selected item and a summary of visible items are mirrored into an off-screen ARIA live region and a focusable list so keyboard and screen-reader users can navigate items. The file browser has its own accessible list (R-155). |
| R-104 | **System tray.** While Reframe runs, a Reframe icon is **always** present in the notification area (not only when minimized). Clicking it, or choosing **Show Reframe**, shows and focuses the window. Its menu: **Show Reframe**, **Sync now**, **Start with Windows** (check item, R-173), **Quit Reframe**. With *Minimize to the system tray* on (default), minimizing hides the window and its taskbar button. **Closing the window hides it to the tray** (it does not exit); the first time this happens a status/tray notice says "Reframe is still running in the tray. Use Quit to exit." **Quit Reframe** (tray menu, View menu, or Ctrl+Q) runs the exit sequence (§10.2). Sync, live refresh, version capture and backups continue while hidden. |
| R-170 | **Double-click launch, no console.** Reframe starts from a `Reframe.pyw` launcher in the app folder run by the global interpreter's `pythonw.exe`, so no terminal window ever appears. Start-menu (and optional Desktop) shortcuts named **Reframe** point to it. Startup errors that occur before a window exists are written to the log file (§8.5) and, because there is no window to show them in, reported with a single native message box — the only permitted modal (exception to §6.8). |
| R-171 | A tool `tools\install_shortcuts.py` creates, repairs or removes the shortcuts (Start menu, optional Desktop, Startup per R-173), each with the Reframe icon and AppUserModelID (R-174). It is idempotent and only touches shortcuts it created (identified by name and target). |
| R-172 | **Launch from the tray.** Because the app can start hidden at sign-in (R-173), the tray icon is available even before the user has opened the window; clicking it opens the window. Launching from the tray when the process is not running is not possible and not required: that case is covered by R-173 and the Start-menu shortcut. |
| R-173 | **Start at sign-in** (named *Start with Windows* on Windows; setting, default **off**; toggled in Settings or the tray menu) maintains a per-user startup entry that launches the app with `--tray`. On Windows it is a shortcut in the user's Startup folder that launches `Reframe.pyw --tray`. With `--tray` the app starts with the window hidden and only the tray icon showing; launch sync and launch backup run as usual. The installer may turn it on. |
| R-174 | **Taskbar identity.** The process sets an explicit AppUserModelID (`Reframe.Timeline`) before any window is created; the window, tray icon and all shortcuts use the bundled `assets\reframe.ico` (multi-size 16–256 px); the shortcuts carry the same AppUserModelID so a pinned taskbar button and the running window group together. The taskbar must never show the generic Python icon. |
| R-190 | **Spell checking with suggestions.** Notes fields are spell-checked. Right-clicking a flagged word shows the spell checker's suggestions (and *Add to dictionary* when available) above cut/copy/paste, using the web view's native text-field menu, which the app passes through in text fields (on Windows `host.py` enables WebView2's default context menus, which pywebview disables outside debug mode). If suggestions cannot be delivered reliably on a platform, spell checking is switched off there rather than underlining words with no way to fix them. Title, tag and path fields keep spell checking off. |

### 3.12 Timeline file browser

The ruler popup shows files in the hovered time segment, independent of the folders that hold them. Browsing does not modify files or Gantt data; dragging a file onto an item adds an attachment (R-72).

| ID | Requirement |
|---|---|
| R-140 | **Roots.** The user selects one or more local or network directory roots in Settings ▸ Files, using the native folder chooser or paste. Duplicate roots and roots nested inside another root are refused with an explanation. Adding, removing or changing roots updates the browser. Roots and options are stored in settings; discovered files are not stored in the database. |
| R-141 | **Recursive discovery.** All files under every root are discovered recursively by a background scanner that streams results, shows progress, can be cancelled, and keeps the UI interactive. Directory junctions and symbolic links are not followed by default. Unreachable roots, access-denied and skipped directories, and partial scans are reported explicitly (distinguished from empty folders) in a scan-status strip and in the root list. |
| R-142 | **No folder tags.** Files are not assigned tags based on their folders; scanning does not alter Gantt tags. |
| R-143 | **Filtering.** The popup has its own filter bar at the bottom: file-name/path text search and Media, PDF, Documents and Other quick filters, independent of Gantt filtering. Media combines images, video and audio; PDF files also match Documents; Other includes archives and unclassified files. Multiple selected types are combined with OR. Counts cover the selected period, including eligible undated files. Restored legacy folder-tag filters are ignored; old image/video/audio and archive selections map to Media and Other respectively. |
| R-144 | **Placement date.** A photo uses **date taken** (EXIF `DateTimeOriginal`, else `DateTimeDigitized`) and an MP4 video uses Windows' **Media created** property when present and valid, otherwise filesystem creation time is used. EXIF times are wall-clock dates; an explicit offset is honored. Media created is UTC and converted to the local day. Details identify the date source. Files without a readable date are included, along with matching dated files, only when the entire hovered segment is after today. Ties are ordered by timestamp then normalized path. |
| R-145 | **Placement.** The popup includes dated files in the hovered ruler segment, with the segment start inclusive and end exclusive, for every current ruler scale. Gantt ruler click, pan and zoom gestures remain available. |
| R-146 | **Thumbnails, icons and dense periods.** The popup provides a virtualized list of every matching file in the segment, with name, type, size, date and date source. Images use thumbnails when available; other files and unavailable images show the Windows-associated file-type icon. Icons are requested for visible rows without opening the files, including online-only files. Filters and exclusions apply consistently to the count and list. |
| R-147 | **File details and actions.** Selecting a file shows details in the popup (name, full path, size, created, modified, date taken, date source, root). Double-click (or Enter) opens it with the associated application; **Open containing folder** reveals it in Explorer. There is no edit, move, rename or delete. The path and root are re-validated before opening. |
| R-148 | **Exclusions and hidden files.** All extensions are included by default. Settings ▸ Files has an editable **excluded extensions** list (case-insensitive; handles dotless names and multi-part extensions such as `.tar.gz`) with one-click suggestions of common junk types (e.g. `.tmp`, `.bak`, `.lnk`, `~$*` Office lock files, `.ini`, `thumbs.db`) that are **not** excluded until the user adds them. Hidden and system files and folders are excluded by default with an **Include hidden and system files** option. The active exclusions are visible in the view's filter bar tooltip and are reversible. |
| R-149 | **Refresh.** Changes under the roots are picked up automatically while the app runs (bounded change notifications per root, coalesced, plus periodic reconciliation) and on **Refresh** (F5). Watcher failure, overflow, network loss and changes made while the app was closed trigger a rescan; until it completes the popup's scan-status strip shows a stale/partial indicator. Cloud placeholder files (OneDrive "online-only") are listed from metadata only; they are never downloaded to build a thumbnail or read a date, and are marked *online-only*. |
| R-150 | **Range and zoom.** The browser uses the current Gantt ruler range and zoom. |
| R-151 | **Minimap.** The Gantt minimap remains visible; there is no separate file minimap. |
| R-152 | **Interaction.** The browser does not replace the Gantt or change its filters or zoom. Browsing does not change Gantt selection; dropping a file on a bar selects the target after attachment. Gantt gestures remain available outside the popup. |
| R-153 | **Image safety.** Thumbnails are decoded on background workers with bounded concurrency, queue depth and retained images; decoding is limited by a maximum pixel count (decompression-bomb guard); EXIF orientation and aspect ratio are honored; animated images show a stable first frame; unsupported, corrupt, inaccessible or oversized images use the file-type icon or a labelled placeholder and can still be opened. Requests made stale by a viewport, filter, date-mode or root change are cancelled. |
| R-154 | **Thumbnail cache.** Resized thumbnails are cached on disk in a bounded, rebuildable cache under the data folder (`cache\thumbs`), keyed by file identity, size and modification time; eviction never touches originals. Thumbnails are served only through the authenticated loopback API by opaque entry id for files inside current roots — never by an arbitrary path parameter — and never sent anywhere else. |
| R-155 | **Accessibility.** Ctrl+2 or View ▸ Browse files opens the center lower-row segment and focuses its keyboard-operable list. Arrow keys select, Enter opens, and Escape closes and restores timeline focus. The popup provides descriptive labels and visible selection and focus. |

---

## 4. Timeline layout algorithm (Gantt view)

### 4.1 Packing

Inputs: the visible items (after filter and calendar visibility), the layout mode (R-17), the group-by flag (R-15), the current zoom band (§4.3), and a label-width function (canvas `measureText`, cached) supplied by the caller, so `packing.js` has no DOM dependency.

1. **Sort key** (both modes): `start_date` ascending, then span length descending, then `id` ascending. The same data always gives the same rows.
2. **Lanes.** With group-by on, items are partitioned by primary tag (lane order: tag-tree order, then name; "untagged" last) and steps 3–4 run per lane. Without group-by, there is one lane.
3. **Compact mode.** For each item, compute its **occupied extent** in whole days `[start_date, end_date + 1)`, extended on the right by the reserved label width (converted to days at the **lowest px/day of the current zoom band**) when the label is drawn outside the bar. Place it in the lowest-numbered row whose last occupied extent ends at or before the item's start (min-heap of row end times); otherwise open a new row.
4. **Strict Gantt mode.** Each item gets its own row, in sort-key order: row *n* holds the *n*-th item of the lane. Outside labels never affect rows in this mode.
5. Rows are packed over the **entire visible item set**, not only the current window, so vertical positions do not jitter while panning.
6. Packing recomputes on: data change, filter change, calendar-visibility change, layout-mode change, group-by toggle, and a zoom change that crosses into another zoom band (compact mode only). It does not recompute on pan or on zoom within a band.
7. **Stability during edits.** Rows are not repacked while a drag is in progress (the dragged bar floats). After a commit, an external refresh or a layout-mode switch, the layout repacks and the view scrolls vertically so the edited or selected item stays at the same screen height.
8. **Single source for the minimap.** The packing result (lanes, rows, entries) is the only layout the minimap draws (R-21); the minimap never runs its own packing.

### 4.2 Performance and rendering

| Target | Value |
|--------|-------|
| Items supported | 10,000 without degraded interaction |
| Pan/zoom frame rate | ≥ 60 fps with ≤ 1,000 visible labelled bars; ≥ 30 fps with 5,000 visible unlabelled bars (4K display), in both layout modes |
| Cold start to first paint | < 2 s with 10,000 items (window shown; tray start excluded) |
| Packing time | < 100 ms for 10,000 items in either mode |
| Minimap redraw after repack | Same animation frame as the main chart |
| Calendar sync | UI never blocked (R-134) |
| Everything search | UI never blocked; result list updated within one frame of the reply |
| File timeline | Budgets set from measured baselines on a synthetic ≥ 10,000-file tree with dense image days before acceptance (§11); no guarantee is claimed before measurement |

Implementation notes: the timeline and minimap are drawn on `<canvas>` (2D context); a row-indexed interval structure for culling and hit-testing; only visible items painted; fills batched by color; bars cached per zoom band on an offscreen canvas; `measureText` results and elided strings cached; `devicePixelRatio` aware; redraw via `requestAnimationFrame` only on state change. In strict mode the row count can be large: vertical culling by row index is mandatory. The client loads the full item list once (`GET /api/items`) and refreshes it on change.

### 4.3 Label policy and zoom bands

Zoom is quantized into **bands**, each a factor of 2 in px/day. Within a band, label-reservation decisions and compact packing are fixed.

| Condition | Label |
|-----------|-------|
| Bar wide enough for ≥ 6 characters | Inside, clipped with an ellipsis |
| Bar too narrow, and zoom ≥ 8 px/day | Outside, to the right; extent reserved in compact packing |
| Otherwise | No label; tooltip and selection panel only |

Thresholds are fixed constants, not user settings.

---

## 5. Database

### 5.1 Technology and location

SQLite (single file), accessed from Python `sqlite3` (SQLite 3.44 or newer is required: `group_concat(... ORDER BY ...)` and recursive CTEs). Per-connection settings that **every** writer including agents must set: `PRAGMA foreign_keys=ON`, `PRAGMA busy_timeout=5000`. Database-level: `PRAGMA journal_mode=WAL`. Schema version in `PRAGMA user_version` with forward-only migrations; **the current version is 2**. A fresh database is created directly at version 2 from `schema.sql`, which `build_schema.py` generates and which is the executable form of §5.2; `migrations/002_v002.sql` upgrades a version-1 database. A database newer than the app, or one whose tables do not match `schema.sql`, is refused with a clear message instead of being opened.

Primary keys are UUIDs (text); the change log is a full operation log; no autoincrement dependencies between data rows; all stored timestamps are UTC ISO-8601.

**Data folder:** a per-user, non-synchronised folder named `Reframe` (Windows `%LOCALAPPDATA%\Reframe\`, macOS `~/Library/Application Support/Reframe/`; override: `REFRAME_DATA_DIR`), containing `reframe.sqlite` (override: `REFRAME_DB`), `reframe.lock`, `vault\` (default vault, R-166), `cache\thumbs\` (R-154), `tmp\` (materialized versions, R-162) and `logs\reframe.log` (§8.5). The live database and vault must **not** live in a OneDrive or other synchronised folder; the path is shown in the status bar and can be revealed in the file manager.

### 5.2 Schema (normative, version 2)

The DDL below mirrors `src/reframe/db/schema.sql` (the generated, executable form, which also holds the triggers); the `tag.norm_key` expression is abbreviated here.

```sql
CREATE TABLE calendar_source (
  id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  kind        TEXT NOT NULL CHECK (kind IN ('outlook','ics')),
  external_id TEXT NOT NULL,          -- Outlook: the folder id stored in item.outlook_calendar; ICS: normalized path or URL
  name        TEXT NOT NULL,
  location    TEXT NOT NULL,          -- Outlook folder path, file path, or URL (URL masked in the UI, R-135)
  is_holiday  INTEGER NOT NULL DEFAULT 0 CHECK (is_holiday IN (0,1)),
  connected   INTEGER NOT NULL DEFAULT 1 CHECK (connected IN (0,1)),
  visible     INTEGER NOT NULL DEFAULT 0 CHECK (visible IN (0,1)),
  refresh_min INTEGER,                -- ICS URL refresh interval; NULL = default setting
  etag        TEXT, last_modified TEXT,
  last_read_at TEXT, last_status TEXT, last_error TEXT,
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE (kind, external_id)
);

CREATE TABLE item (
  id            TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  kind          TEXT NOT NULL DEFAULT 'plan' CHECK (kind IN ('plan','log')),
  title         TEXT NOT NULL CHECK (length(trim(title)) > 0),
  start_date    TEXT NOT NULL CHECK (date(start_date) IS start_date),
  end_date      TEXT NOT NULL CHECK (date(end_date)   IS end_date),
  notes         TEXT NOT NULL DEFAULT '',
  origin        TEXT NOT NULL DEFAULT 'local' CHECK (origin IN ('local','outlook','ics')),
  source_id     TEXT REFERENCES calendar_source(id),
  outlook_uid   TEXT UNIQUE,                                     -- R-63
  ics_uid       TEXT UNIQUE,
  outlook_series_id TEXT,                                        -- series identity (Outlook: calendar|gaid; ICS: source|UID)
  outlook_calendar TEXT,                                         -- Outlook folder id (kept for agents; = calendar_source.external_id)
  outlook_time  TEXT,                                            -- display text only, e.g. '09:00-10:30' (also used for ICS)
  outlook_location TEXT,                                         -- also used for ICS LOCATION
  deleted_at    TEXT,
  hidden_at     TEXT,
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  CHECK (end_date >= start_date),
  CHECK ((origin = 'local') = (source_id IS NULL))
);
CREATE INDEX item_dates  ON item (start_date, end_date) WHERE deleted_at IS NULL AND hidden_at IS NULL;
CREATE INDEX item_series ON item (outlook_series_id) WHERE outlook_series_id IS NOT NULL;
CREATE INDEX item_source ON item (source_id) WHERE source_id IS NOT NULL;

CREATE TABLE outlook_hidden_series (series_id TEXT PRIMARY KEY, hidden_at TEXT NOT NULL);  -- also ICS series ids

CREATE TABLE tag (
  id         TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  name       TEXT NOT NULL,
  norm_key   TEXT GENERATED ALWAYS AS (<nested lower/replace/trim formula of name>) STORED NOT NULL UNIQUE CHECK (norm_key <> ''),
  color_slot INTEGER,                                            -- NULL = auto; 0-15 (§7.2)
  parent_id  TEXT REFERENCES tag(id) ON DELETE SET NULL,         -- NULL = groupless
  sort_order INTEGER,
  retired_at TEXT,                                               -- NULL = active
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  CHECK (parent_id IS NULL OR parent_id <> id)
);
CREATE INDEX tag_parent ON tag (parent_id) WHERE parent_id IS NOT NULL;

CREATE TABLE item_tag (
  item_id  TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
  tag_id   TEXT NOT NULL REFERENCES tag(id)  ON DELETE CASCADE,
  position INTEGER NOT NULL DEFAULT 0,       -- lowest = primary (color) tag; ties by tag name. >=1000 only as the insertion default for system tags (R-45, R-114)
  implied  INTEGER NOT NULL DEFAULT 0 CHECK (implied IN (0,1)),
  PRIMARY KEY (item_id, tag_id)
);
CREATE INDEX item_tag_tag ON item_tag (tag_id);

CREATE TABLE outlook_series_tag (                                -- R-47; also ICS series ids
  series_id TEXT NOT NULL,
  tag_id    TEXT NOT NULL REFERENCES tag(id) ON DELETE CASCADE,
  position  INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (series_id, tag_id)
);

CREATE TABLE attachment (
  id         TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  item_id    TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
  kind       TEXT NOT NULL CHECK (kind IN ('file','folder','url')),
  target     TEXT NOT NULL,
  label      TEXT,
  pinned_version_id TEXT REFERENCES file_version(id),           -- NULL until captured
  pin_basis  TEXT CHECK (pin_basis IN ('attach','migration','duplicate')),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX attachment_item ON attachment (item_id);

CREATE TABLE file_source (
  id            TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  path_key      TEXT NOT NULL UNIQUE,     -- normalized absolute path (case-folded, separators unified)
  display_path  TEXT NOT NULL,
  last_size     INTEGER, last_mtime TEXT, last_hash TEXT,
  status        TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','protected','changed','missing','locked','denied','unreachable','failed')),
  last_check_at TEXT, last_capture_at TEXT, last_error TEXT
);

CREATE TABLE file_version (
  id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  source_id   TEXT NOT NULL REFERENCES file_source(id),
  sha256      TEXT NOT NULL,
  size        INTEGER NOT NULL,
  file_mtime  TEXT,                      -- original's modification time when captured
  captured_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  backend_ref TEXT NOT NULL,             -- locator inside the vault backend (§10.3)
  UNIQUE (source_id, sha256)
);
CREATE INDEX file_version_hash ON file_version (sha256);

CREATE TABLE change_set (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  ts     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  actor  TEXT NOT NULL CHECK (actor IN ('user','agent','sync','undo','external')),
  label  TEXT NOT NULL,
  reverts_change_set_id INTEGER REFERENCES change_set(id)
);

CREATE TABLE change (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  change_set_id INTEGER REFERENCES change_set(id),
  ts            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  table_name    TEXT NOT NULL,
  row_key       TEXT NOT NULL,          -- id, or 'item_id|tag_id' for item_tag, 'series_id|tag_id' for outlook_series_tag
  op            TEXT NOT NULL CHECK (op IN ('insert','update','delete')),
  before_json   TEXT,
  after_json    TEXT
);
CREATE INDEX change_set_idx ON change (change_set_id);
CREATE INDEX change_row_idx ON change (table_name, row_key);

CREATE TABLE app_context (                                       -- single row; valid only inside a writer's transaction
  id INTEGER PRIMARY KEY CHECK (id = 1), actor TEXT NOT NULL DEFAULT 'external', change_set_id INTEGER
);
INSERT INTO app_context (id) VALUES (1);

CREATE TABLE setting (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE sync_run (
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT,
  calendars TEXT, added INTEGER, updated INTEGER, removed INTEGER, status TEXT, error TEXT,
  source_id TEXT
);

-- Agent views (exclude deleted and hidden items; hidden *calendars* are a view setting and are NOT excluded)
CREATE VIEW v_item AS
SELECT i.id, i.kind, i.title, i.start_date, i.end_date, i.notes, i.origin,
       (SELECT group_concat(t.name, '; ' ORDER BY it.position, t.name)
          FROM item_tag it JOIN tag t ON t.id = it.tag_id WHERE it.item_id = i.id) AS tags,
       (SELECT cs.name FROM calendar_source cs WHERE cs.id = i.source_id) AS calendar
FROM item i WHERE i.deleted_at IS NULL AND i.hidden_at IS NULL;

CREATE VIEW v_tag AS
WITH RECURSIVE p(id, path, depth) AS (
  SELECT id, name, 0 FROM tag WHERE parent_id IS NULL
  UNION ALL SELECT t.id, p.path || ' > ' || t.name, p.depth + 1 FROM tag t JOIN p ON t.parent_id = p.id)
SELECT t.id, t.name, t.parent_id, p.path, p.depth, t.retired_at IS NOT NULL AS retired,
       (SELECT count(*) FROM item_tag it WHERE it.tag_id = t.id) AS usage
FROM tag t JOIN p ON p.id = t.id;
```

Column meanings not obvious from their names: `calendar_source.visible` is the on-demand display switch (R-131), not logged in the change log; `attachment.pin_basis` says whether a pin was made at attach time (`attach`), by duplication (`duplicate`, R-49) or by a later capture of a link that pre-dated protection (`migration`); `file_source.status` is the protection status summarized in the UI (R-160, R-163).

`updated_at` is set by the writer, **not** by a trigger (a self-updating trigger would log duplicate rows).

### 5.3 Change-log capture and database-enforced rules

**Writer protocol** (unchanged; every writer including agents):

1. `BEGIN IMMEDIATE`.
2. `INSERT INTO change_set (actor, label) VALUES (...)`; then `UPDATE app_context SET actor = ..., change_set_id = last_insert_rowid()`.
3. Make the data writes.
4. `UPDATE app_context SET actor = 'external', change_set_id = NULL`.
5. `COMMIT`.

**Logging triggers.** `AFTER INSERT/UPDATE/DELETE` triggers on `item`, `tag`, `item_tag`, `outlook_series_tag`, `outlook_hidden_series` and `attachment` write a `change` row with full before/after JSON of **every column** (including every column added in schema version 2). The trigger SQL is generated from the table definitions by `db\build_schema.py`; migration 002 drops and recreates every logging trigger so the snapshots include the version-2 columns. `calendar_source`, `file_source`, `file_version`, `setting` and `sync_run` are not change-logged (configuration, append-only version records, or run records).

A write that skips the protocol is logged with `actor = 'external'` and a NULL `change_set_id`; at the next poll the app groups all NULL-set rows into one `external` change set and resets a leftover non-default `app_context` found outside a transaction.

**Undo** opens a change set with `actor = 'undo'` and `reverts_change_set_id`, then applies the inverse of each `change` row in reverse order; inverse writes are logged, so redo is an undo of the undo.

**Protective triggers** (`BEFORE`, abort):
- any `UPDATE`/`DELETE` on `change` or `change_set` (allowed: setting a NULL `change_set_id`; updating the open set's `label`);
- a change to core fields (title, dates, kind, origin, uid columns, `outlook_*`, `source_id`) of an imported row (`origin IN ('outlook','ics')`) while `app_context.actor` is not `sync` or `undo`;
- a hard `DELETE` on `item` while the actor is not `undo`;
- any `UPDATE` of `file_version` and any `DELETE` of a `file_version` still referenced by an attachment or by a `change` snapshot (R-165);
- setting `tag.parent_id` to the tag itself or to any of its descendants (cycle check with a recursive CTE).

**Tag-tree triggers (R-111, R-112).** They run for every writer, so agents get the same behavior as the UI, and are **suppressed while `app_context.actor = 'undo'`** (undo replays the logged rows exactly, including the implied rows the triggers originally produced):
- `AFTER INSERT ON item_tag`: insert every missing ancestor of `NEW.tag_id` for that item with `implied = 1`, positioned after the item's current non-system tags (`max(position) + 1 …` among positions < 1000).
- `AFTER DELETE ON item_tag` (of an explicit or implied row): delete each `implied = 1` ancestor row on that item that no remaining tag on the item descends from.
- `AFTER UPDATE OF parent_id ON tag`: for every item carrying the moved tag or a descendant, delete implied ancestor rows no longer justified and insert newly required ancestors as implied.
- `BEFORE DELETE ON item_tag` of an ancestor while a descendant remains on the item: abort with "Remove the descendant tag first" (bypassed when the actor is `undo` or when the row is being removed by the cascade above).

**Verification item:** rows inserted or deleted *by* these triggers must themselves be change-logged. SQLite does not re-fire a trigger that is already running (`recursive_triggers` is off), so the test suite must prove that the logging triggers fire for trigger-generated `item_tag` rows; if they do not, the tag-tree triggers write their own `change` rows explicitly. Either way, one tag move is one change set and one undo (R-112).

These triggers cannot stop an agent that drops them or edits the schema; backups (§8.4) remain the defence for that case. The startup hygiene check (R-46) repairs implied-tag drift found after such edits.

Change-log size: History may prune sync-actor `change` rows older than 12 months while keeping `change_set` rows.

### 5.4 Concurrency and transactions

WAL mode permits the app to hold the file open while an agent reads or writes. Python 3.12's `sqlite3` is used with `autocommit=True` and an explicit `BEGIN IMMEDIATE` for **every** write. Writers use short transactions and never hold a write transaction while idle; vault capture (§10.3) never holds a database transaction while reading or writing file content. On `SQLITE_BUSY` the writer retries with back-off and surfaces an inline message. The app updates only the columns the user changed, never a whole row from a cached copy.

---

## 6. User interface

### 6.1 Window layout

```
+--------------------------------------------------------------------------------------+
| Toolbar: [Today] [Zoom - +] [Fit] [Compact|Strict] [Group by tag]                   |
|          [Undo] [Redo] [Sync] [View v] [Side panel]                                  |
| Filter bar: [All][None] [tag chips ... Untagged] [AND|OR] [search____] [Plan|Log]    |
|             [Local|Outlook|Feed] [Calendars: 3 of 15 v] [x Clear]                    |
+--------------------------------------------------------------------+-----------------+
| Ruler (years / months / weeks / days), today marker                |  Side panel     |
|--------------------------------------------------------------------|  (Item / Day /  |
| Lane: ClientA  [bar]  [bar----]   [bar]                             |   Tags /        |
|                  [ruler file browser popup when hovering]          |   History /     |
| Lane: R&D     [bar]       [bar]     [bar------]                    |   Settings)     |
|                                                                    |                 |
+--------------------------------------------------------------------+                 |
| Minimap: same lanes/rows, scaled  |[=======frame=======]|          |                 |
+--------------------------------------------------------------------+-----------------+
| Status: date under cursor | 214/260 items | Outlook 08:12 · Feed ok | 2 to protect   |
+--------------------------------------------------------------------------------------+
```

The file popup overlays the chart and contains the file list and selected-file details, with file filters and scan controls at the bottom. Folder roots and exclusions remain in Settings.

### 6.2 Toolbar and View menu

Toolbar: Today (T), Zoom out/in, Fit selection (Z), **Compact|Strict** (Ctrl+Shift+L), Group by tag (Ctrl+G), Undo, Redo, Sync now (Ctrl+R), **View ▾**, Side panel (Ctrl+B). Every button has a tooltip with its shortcut. There is no hide-past button and no theme button.

**View ▾** menu (an app menu, not a native dialog): Browse files; Compact / Strict Gantt; Group by tag; **Theme ▸** (the nine themes, R-180); Calendars ▸ (show/hide each source; Show all / Hide all); Side panel; Day panel (Ctrl+D); History (Ctrl+Shift+H); Go to date (Ctrl+J); Keyboard shortcuts (?); **Quit Reframe** (Ctrl+Q).

### 6.3 Ruler

Click a date to open the day panel. Drag on the ruler pans. Wheel over the ruler zooms. Hover opens the file browser as described in R-18.

### 6.4 Minimap

As R-20 – R-25. The frame has visible resize handles, hover and drag states.

### 6.5 Item side panel

Fields, inline-editable: title; start and end date (typed, with an inline calendar grid inside the panel); duration (computed); **tags** (chip editor: autocomplete with group paths, drag or Alt+Left/Right to reorder any tag, first = color (R-114); implied tags subdued with "Added by group" tooltip (R-111); retired tags marked); notes (multi-line, spell-checked, R-190); **attachments** (path bar R-120; list with protection state, version history, open/restore/remove, R-160 – R-165); origin and source details (read-only for imported items, R-133); history for this item (change entries with revert); Hide item / Hide whole series for imported items.

### 6.6 Day panel

Opens when a ruler date is clicked or Ctrl+D is pressed. Shows the date and weekday, "Log" entries and "Plan" items overlapping that day (visible calendars only), and an inline "Add log entry" row. Items listed here are editable as in the item panel.

### 6.7 Keyboard shortcuts

Single-key and arrow shortcuts apply only when the timeline has focus (R-95). In a text field, standard text-editing keys apply (including Ctrl+Z/Ctrl+Y as text undo/redo).

| Action | Shortcut |
|---|---|
| Jump to today | T |
| Browse files at center of timeline | Ctrl+2 |
| New item at selected day | Ctrl+N |
| New log entry at selected day (default today) | Ctrl+L |
| Edit title / rename (timeline focus) | F2 or Enter |
| Open selected file (file browser) | Enter or double-click |
| Delete selected | Delete |
| Undo / Redo (database) | Ctrl+Z / Ctrl+Y (or Ctrl+Shift+Z) |
| Search | Ctrl+F |
| Next / previous search match | Enter / Shift+Enter (in search) |
| Clear filter | Ctrl+Shift+F |
| Select all / no tag chips | Ctrl+Shift+A / Ctrl+Shift+N (filter bar focus) |
| Zoom in / out | Ctrl+= / Ctrl+- (and Ctrl+wheel) |
| Zoom to selection | Z |
| Jump to the first / last item | Home / End |
| Pan | Left/Right (1 day), Ctrl+Left/Right (1 week), PgUp/PgDn (one window) |
| Select item in the row above / below | Up / Down |
| Scroll rows | Alt+Up / Alt+Down |
| Previous / next item (time order) | Alt+Left / Alt+Right (timeline focus) |
| Move focused tag chip earlier / later | Alt+Left / Alt+Right (chip focus) |
| Move selected item | Alt+Shift+Left/Right (1 day) |
| Move end date later / earlier | Shift+Right / Shift+Left |
| Move start date later / earlier | Ctrl+Shift+Right / Ctrl+Shift+Left |
| Copy tags / paste tags | Ctrl+Alt+C / Ctrl+Alt+V |
| Duplicate the selected item, then name it | Ctrl+Shift+D |
| Select all visible items | Ctrl+A |
| Compact / Strict Gantt layout | Ctrl+Shift+L |
| Toggle group by tag | Ctrl+G |
| Toggle side panel | Ctrl+B |
| Open day panel | Ctrl+D |
| Open history | Ctrl+Shift+H |
| Sync now (all connected sources) | Ctrl+R |
| Refresh files | F5 |
| Go to date | Ctrl+J |
| Quit Reframe | Ctrl+Q |
| Shortcut cheat sheet (inline overlay) | ? |

### 6.8 No-popup rule

Dialogs, message boxes and modal windows are not used for editing, confirming or reporting, and `alert`/`confirm`/`prompt` are never called. Allowed: the native OS file and folder choosers (via the window host: backup folder, vault folder, attachments, ICS files, File timeline roots, restore-to location), the app's own menus and context menus, the WebView2 native text-field context menu (R-190), tooltips, tray notices, and the single startup-failure message box of R-170. Errors and notices use the status bar and inline banners in the relevant panel.

### 6.9 Accessibility

Keyboard-complete; logical focus order; visible focus ring; no information conveyed by color alone (tags are text in the panel, log items use an outline pattern, implied/retired tags carry text markers, protection state has an icon and text); minimum text contrast per §7.3 in all nine themes; respects Windows text scaling and HiDPI; respects `prefers-reduced-motion` (R-185).

---

## 7. Appearance and themes

Every theme is defined by an existing design skill file; this spec cites them rather than re-deriving them. Each theme is one CSS file of custom properties (`tokens-<id>.css`) applied by a `data-theme` attribute on the root element; the canvases read the same variables with `getComputedStyle`, so the timeline and the DOM never disagree. All files map the same **semantic role tokens** (§7.1), so components never reference a palette token directly.

### 7.1 Semantic role mapping

Values are the skill files' own token names and hex values. **(D)** marks a value **derived** for Reframe because the skill does not define that role; derived values must pass §7.3 and be reviewed by the user on first build.

| Theme (id) | Canvas / weekend shade | Ruler & minimap field / labels | Primary / secondary text | Today marker & shading | Minimap frame | Selection / focus | Primary button / danger | Fonts | Shape |
|---|---|---|---|---|---|---|---|---|---|
| 2001 Meridian (`meridian`) | `--surface #dce8ef` / `--surface-mid #c8d9e3` | `--navy #05032a` / `--amber-lt #be8a2a` | `--navy` / `--blue-dk #375464` | `--red #d00000` marker; tint of `--red` (D) | `--amber-lt` | `--blue-dk` | `--red` / `--red-mid #8d1425` | DM Sans, DM Mono, DM Serif Display | Rounded cards, frosted panel |
| Industrial Catalog (`catalog`) | `--catalog-page #ffffff` / `--catalog-rail #eeeeee` | `--catalog-rail` / `--catalog-text #252525`, 2 px `--catalog-yellow #e1c800` rule under the toolbar | `--catalog-text` / `--catalog-muted #545454` | `--catalog-green #315e32` | `--catalog-link #005b83` | `--catalog-link`, 2 px outline + 2 px offset | `--catalog-green` / `--catalog-error #a21d20` | Arial, Helvetica (system) | 2–4 px radius, no shadows |
| Claude (`claude`) | Canvas `#faf9f5` / Surface Card `#efe9de` | Surface Dark `#181715` / Canvas `#faf9f5` | Ink `#141413` / Muted `#6c6a64` | Coral `#cc785c` | Accent Amber `#e8a55a` | Ink `#141413` (D) | Coral / Error `#c64545` | Inter (body), Cormorant Garamond (display), JetBrains Mono | 8 px controls, 12 px cards, minimal shadow |
| Dell 1996 (`dell-1996`) | Canvas `#ffffff` / light gray (D) | Frame Ink `#000000` / Dell Yellow `#fcc20f` | Ink `#000000` / dark gray (D) | Dell Red `#e91d2a` | Dell Yellow | Link `#0000ee` | Dell Red (CTA only) / Dell Red with an explicit text label (D) | Arial Black (display), Arial/Helvetica (UI) (system) | Square, hard bevels, no soft shadow |
| DJI (`dji`) | Canvas `#ffffff` / Canvas Soft `#f7f9fa` | Footer `#272727` / Canvas `#ffffff` (D) | Ink `#262626` / Ink Secondary `#595959` | DJI Blue `#0070d5` | Canvas `#ffffff` on Footer (D) | DJI Blue (one blue per surface) | DJI Blue / Error `#f5222d` | Open Sans | Square tiles, pill controls |
| Ferrari (`ferrari`, dark) | Canvas `#181818` / Surface Card `#303030` | Canvas `#181818` with Hairline `#303030` / Body `#969696` | Ink `#ffffff` / Body `#969696` | Rosso Corsa `#da291c` | Yellow `#f6e500` | Hypersail Yellow `#fff200` (focus ring only) | Rosso Corsa / Semantic Warning `#f13a2c` | Open-licence substitute for FerrariSans: Inter (D) | Square, depth by brightness steps |
| SpaceX (`spacex`, dark) | Canvas Night `#000000` / Canvas Night Soft `#0a0a0a` | Canvas Night / On Primary Mute `#f0f0fa` | On Primary `#ffffff` / On Primary Mute | On Primary `#ffffff` outline marker (no color accent) | On Primary `#ffffff` | On Primary `#ffffff` | Ghost (outline) button in On Primary / light red (D) with text label | D-DIN (display), Inter (UI) | Pill ghost buttons, no shadows/glows/gradients |
| Spotify (`spotify`, dark) | Near Black `#121212` / Dark Surface `#181818` | Near Black / Silver `#b3b3b3` | White `#ffffff` / Silver `#b3b3b3` | Spotify Green `#1ed760` | Spotify Green | Spotify Green (functional use) | Spotify Green / Negative Red `#f3727f` | Open-licence substitute for SpotifyMix: Inter (D) | Pills and circles |
| IBM (`ibm`) | Canvas `#ffffff` / Surface 1 `#f4f4f4` | Inverse Canvas `#161616` / Canvas `#ffffff` | Ink `#161616` / Ink Muted `#525252` | IBM Blue `#0f62fe` | Canvas `#ffffff` on Inverse (D) | IBM Blue | IBM Blue / Semantic Error `#da1e28` | IBM Plex Sans, IBM Plex Mono (D: same family) | Square, flat, hairlines |

Rows carry no meaning, so there are no alternating row bands in any theme. Each theme follows its own scarcity rules: Meridian keeps red scarce and lets amber carry data; Industrial Catalog keeps green for identity and yellow for a thin rule, never text on white; Claude keeps coral scarce and the canvas warm, never pure white; Dell 1996 reserves red for the primary action; DJI uses only one blue per surface; Ferrari's Rosso Corsa is the single scarce accent and Hypersail Yellow is used only for focus; SpaceX has no colored accent (black, white and content only); Spotify's green is functional only, never decorative; IBM Blue is the only chromatic accent.

Theme roles the skills do not cover, which every token file must define: untagged bar, bar text, log-entry outline, selected bar, implied-tag chip, retired-tag marker, drag ghost and drop-target outline, selection marquee, active-filter highlight, external-change highlight (R-85), lane separator, Gantt minimap density colors, protection-state colors (protected / capturing / not protected), file-browser placeholder, info/warning/error banners, HAL eye (2001 only, R-184).

### 7.2 Tag categorical set

None of the palettes define a categorical series for simultaneous tag colors. Each theme therefore defines **8 base hues** derived from its own tokens (plus tints/shades), staying within the palette's character, and each hue has a **solid** and a **hatched** fill: **16 slots** (0–7 solid, 8–15 hatched, same hues). Rules: adjacent base hues differ clearly in hue and lightness; every slot has an accessible label color; a hatched bar draws its label on a solid backing chip. Dark themes use hues light enough to read against their dark canvas. **SpaceX exception:** the design forbids colored accents, but tag colors carry data, so the SpaceX theme uses a desaturated derived set; this is recorded as a deliberate deviation. All slot values are **derived, not taken from the skills**, and require user sign-off on first build.

### 7.3 Verification items for the themes

- WCAG contrast of bar text on every slot, in all nine themes: 4.5:1 for label text, 3:1 for non-text graphics (focus ring, today marker, minimap frame, drop-target outline, lane separators).
- Body and secondary text against canvas and panel surfaces: 4.5:1.
- Where a skill's accent fails a role on a given surface (e.g. a brand blue on a dark field), the token file uses the derived alternative listed in §7.1, and the contrast test proves it.
- Dark themes: native controls, scroll bars, selection highlight and text caret follow `color-scheme: dark`.
- No logo artwork, wordmark or proprietary asset of any referenced company is used.

### 7.4 Fonts

Bundled (open licences, licence files kept next to the font files, fetched by `tools\fetch_fonts.py` after confirming each licence): DM Sans, DM Mono, DM Serif Display, Barlow, Barlow Condensed, Inter, Cormorant Garamond, JetBrains Mono, Open Sans, D-DIN, IBM Plex Sans, IBM Plex Mono. Referenced from Windows without bundling: Arial, Arial Black, Helvetica fallback, Times New Roman, Segoe UI (universal fallback). A font whose licence cannot be confirmed is not bundled; the theme falls back to Segoe UI (the system UI font on other platforms).

---

## 8. Application behavior

### 8.1 Startup

Acquire the single-instance lock (else focus the running window, R-102) → set the AppUserModelID (R-174) → open the database (create or migrate) → start the log file → run the launch backup (§8.4) → apply theme and saved view state → create the tray icon → show the window (unless started with `--tray`, R-173) → start calendar sync for connected sources (if enabled) → start the attachment reconciler and watchers (R-163) → run tag hygiene (R-46) → clean stale temporary files (R-162). When the file browser is first opened, start discovery if the roots have never been scanned.

### 8.2 First run

A fresh install shows an empty timeline with a one-screen inline hint (not a dialog) pointing to Ctrl+N, calendar sources in Settings, and the agent instruction file. The hint can be dismissed.

### 8.3 Settings (side panel tab, no dialog)

Sections, in order (theme is chosen in View ▸ Theme, R-90, not here):

1. **Window & startup** — Minimize to the system tray (default on); Start at sign-in (R-173, default off); default window length (days).
2. **Calendars** — list of sources (name, kind, location, item count, last read, status), each with *Connected* and *Visible* switches and a *Holidays* flag; Add ICS file…; Add ICS URL; refresh interval for URL feeds; sync on launch; lookback days; Refresh list from Outlook.
3. **Attachments** — vault location (with Move…), usage and deduplication summary, files that could not be protected, *Delete unreferenced versions…* (R-165); Everything search status and toggle (R-128).
4. **Files** (File timeline) — roots (add / remove, status of each), excluded extensions with suggestions, include hidden and system files.
5. **Backups** — backup folder, retention, Back up now, Restore from backup (§8.4).
6. **Database & agent** — database path (copy, reveal), schema version, agent instruction file path (copy).

Weeks start on Sunday with US week numbering (fixed). Label thresholds and the tag near-match threshold are fixed constants, not settings.

### 8.4 Backup

- Uses the SQLite online backup API (safe while the app is open), written to a local temp file with `journal_mode=DELETE` and then moved into the destination so a sync client never sees a partial file.
- **Coordinated with the vault (R-167):** each backup set is a folder `reframe-YYYYMMDD-HHMMSS\` containing the database copy and a vault snapshot reference (the backend's incremental snapshot or a manifest plus the new version blobs since the previous set, per §10.3). A set is complete only when both halves are written and verified; incomplete sets are labelled and never offered for restore.
- Triggers: on launch, on Quit, immediately before every sync run, and at most once per hour while editing (`backup_if_stale` on user writes). The agent helper also backs up first if the newest set is more than 1 hour old.
- Destination: folder chosen by the user (default `Documents\Reframe Backups`). Documents may be redirected to OneDrive, which is acceptable for backups but not for the live database or vault.
- Retention: keep the last 20 sets plus the newest of each of the last 30 days and last 12 months (configurable). Retention never deletes vault content still needed by a retained set.
- **Restore** (Settings): takes a safety backup set of the current state, verifies the chosen set (`PRAGMA integrity_check`, vault content present and hash-verified), stages both halves, then switches the live database (SQLite backup API into the live file) and vault together. It never overwrites current data without a safety set.
- A backup failure (folder unavailable, disk full) raises a sticky status-bar warning, names what failed, and is retried at the next trigger; it never blocks use.

### 8.5 Error handling and logging

The app must not crash on malformed data. Rows that violate expectations (orphan `item_tag`/`attachment` rows, unparseable dates, implied-tag drift) are listed in a "needs attention" list in the History panel and excluded from layout. All Outlook COM calls, ICS fetch/parse, Everything SDK calls, filesystem scans, thumbnail decoding and vault operations are wrapped; a failure ends that operation with a clear inline message and leaves existing data intact. Because there is no console (R-170), diagnostics go to `logs\reframe.log` in the data folder (rotating, 5 × 1 MB); secret ICS URLs and file contents are never logged.

---

## 9. Agent operability

### 9.1 Goal

Pasting notes from a meeting into a chat agent should result in correct new log items for what happened and plan items for future events, without breaking the data or duplicating existing items. An agent should be able to record each day's work as clean, correctly tagged log entries.

### 9.2 Access paths

| Path | Use |
|------|-----|
| **Direct SQL** against the SQLite file | Reads (always). Writes only following the §5.3 protocol. Triggers log anything that skips it as `external`, and the tag-tree triggers add implied group tags automatically. |
| **`reframe_db` helper module and CLI** (`src/reframe_db.py` shim; run from the install folder's `src` folder with `python -m reframe_db`) | Preferred for writes. Opens the transaction and change set, resolves tags (`get_or_create_tag()`), validates dates, returns likely duplicates instead of inserting, refuses near-duplicate new tags and retired tags (unless the user confirms: `--allow-similar-tags`, `--allow-retired`), and takes a backup when stale. Commands: `add-item`, `add-items`, `update-item`, `delete-item`, `find-duplicates`, `schedule`, `search`, `tags`, `tag-tree`, `suggest-tags`, `move-tag`, `retire-tag`, `unretire-tag`, `add-attachment` (registers the link; the running app, or the next launch, captures and pins it), `calendars`, `history`, `undo`, `redo`. |

### 9.3 Agent instruction file (shipped deliverable)

`agent/reframe-agent-instructions.md`. It must cover:

1. Database location (the per-user data folder, overrides `REFRAME_DB` and `REFRAME_DATA_DIR`), how to open it (the PRAGMAs of §5.1, `BEGIN IMMEDIATE`) and **schema version 2** with plain-language field meanings (an agent stops if `user_version` is greater than 2).
2. Concept rules: flat model, no status, plan versus log, one-day minimum, inclusive end date, imported items (`outlook` and `ics`) are locked, hidden and deleted rows are excluded (use `v_item`), hidden *calendars* are a display setting only.
3. Tags: the tag tree (`v_tag`); never insert `implied = 1` rows; insert the most specific tag and the database adds its groups; new tags an agent creates are groupless; use `move-tag` to place tags in groups; do not apply retired tags; position 0 is the primary (color) tag.
4. Attachments: insert links normally (`kind` `file`, `folder` or `url`); never write `file_source`, `file_version` or vault files; protection is done by the app.
5. Resolving dates from notes (the note's date is "today"; state resolved dates; ask when ambiguous).
6. The meeting-notes ingestion workflow (log items for what happened, plan items for commitments, update existing items rather than duplicate, look up tags first, one labelled change set, report every item created, changed and skipped).
7. Where to put detail: short titles; context, attendees and sources in `notes`.
8. Safety: soft-delete only; never modify imported core fields; never edit log tables; follow §5.3; update only changed columns; state how to undo.
9. Query recipes, including tag-tree queries ("everything under group X" through `v_tag` or a recursive CTE).

The file contains no path into any particular machine. It names the data folder generically and points to `agent/local-config.md` (written by the installer when it chose a non-default location).

### 9.4 Design constraints that follow

Stable, documented schema; the `v_item` and `v_tag` views; ISO dates only; IDs default in SQL; the helper never silently writes through a near-duplicate; schema changes bump `user_version` and update the agent file in the same change; `tests/py/test_agent_protocol.py` executes the instruction file's SQL recipes against a fresh database and checks that every documented helper command exists.

---

## 10. Technology and architecture

| Concern | Choice |
|---|---|
| Language / runtime | Python 3.12 or newer (`sqlite3.connect(autocommit=True)` needs 3.12). `pythonw.exe` runs the launcher on Windows so no console appears |
| UI | HTML, CSS and vanilla JavaScript (ES modules, no build step, no framework), served by Flask; timeline and minimap on `<canvas>`; all assets bundled locally, no CDN |
| Server | Flask 3 on Werkzeug's threaded server, `127.0.0.1`, ephemeral port; JSON API + Server-Sent Events |
| Window host | `pywebview`: Edge WebView2 on Windows, WKWebView on macOS |
| Tray | `pystray` + `Pillow`; icon always present while running (R-104) |
| Launch / taskbar | Windows: `Reframe.pyw` via `pythonw.exe`; AppUserModelID via `ctypes` (`shell32.SetCurrentProcessExplicitAppUserModelID`); shortcuts via `pywin32` (shell link COM interface plus the property store for the AppUserModelID); `assets\reframe.ico`. macOS: an `.app` bundle (§14) |
| Database | `sqlite3` (stdlib, SQLite 3.44 or newer): WAL, triggers, recursive CTEs, views |
| Outlook | `pywin32` COM on a dedicated thread (`CoInitialize`); classic Outlook only; `Sort("[Start]")` before `IncludeRecurrences = True`; `Restrict` with local date strings; `GetFirst`/`GetNext` iteration; minimal property list (Subject, Location, Start, End, AllDayEvent, BusyStatus, MeetingStatus, ResponseStatus, IsRecurring, RecurrenceState, GlobalAppointmentID, Sensitivity, Categories) to avoid the Object Model Guard |
| ICS | `icalendar` (parse) + `recurring-ical-events` (RRULE/RDATE/EXDATE/overrides expansion); `urllib.request` for HTTPS with ETag/Last-Modified |
| Everything | Everything SDK DLL (x64) via `ctypes`, vendored in `vendor\everything\` (R-126) |
| File timeline | `os.scandir` scanner on worker threads; change notifications with `ReadDirectoryChangesW` via `pywin32` (one bounded watcher per root) plus periodic reconciliation; EXIF and thumbnails with `Pillow` (`Image.MAX_IMAGE_PIXELS` guard, `ImageOps.exif_transpose`); MP4 Media created via the Windows property system |
| Attachment vault | Content-addressed store of zlib blobs (§10.3), standard library only |
| Fuzzy matching | `difflib` (stdlib) |
| Tests | `pytest` (Python, Flask test client), `node --test` (pure JS modules), Playwright driving an installed Chromium-family browser (Edge by default, no browser download) |

Runtime libraries (`requirements.txt`): Flask, pywebview, pystray, Pillow, icalendar, recurring-ical-events, and on Windows pywin32. No other runtime dependencies.

### 10.1 Folder and module layout

```
Reframe/
  README.md, INSTALL.md, SPEC.md   overview, agent-driven install guide, this specification
  requirements.txt                 runtime libraries with platform markers
  requirements-dev.txt             test libraries
  Reframe.pyw                      launcher run by pythonw.exe: no console window (R-170)
  pytest.ini
  assets/reframe.ico               window, tray, taskbar and shortcut icon (R-174)
  agent/                           reframe-agent-instructions.md (§9)
  tools/                           fetch_fonts.py, install_shortcuts.py, make_icon.py, vault_bench/ (§10.3)
  vendor/everything/               Everything SDK DLL, MIT licence notice, VERSION.txt (R-126)
  src/
    reframe_db.py                  agent entry shim (python -m reframe_db)
    reframe/
      __init__.py, __main__.py, host.py (window, bridge, lifecycle), tray.py, launch.py,
      shortcuts.py (Windows shortcuts, R-171), seed.py (demo database), reframe_db.py (helper CLI)
      db/        schema.sql, build_schema.py (generates schema.sql and the 1-to-2 migration), migrations/,
                 connection.py, changelog.py (writer protocol), undo.py
      domain/    items.py, tags.py (tree, implied tags, retire, hygiene), backup.py, history.py, settings.py
      calendars/ sources.py (calendar_source model), sync.py (shared upsert/removal logic), ics.py, ics_sync.py
      outlook/   adapter.py (COM), fake_adapter.py (tests), sync.py (Outlook specifics over calendars/sync.py)
      vault/     store.py (content-addressed store), capture.py, reconcile.py, watch.py, materialize.py, service.py
      files/     roots.py, scanner.py, index.py (session-only), dates.py (EXIF/filesystem/media dates),
                 thumbs.py, icons.py, watch.py, service.py
      search/    everything.py (SDK adapter, serialized worker, request ids)
      server/    app.py, common.py, security.py, events.py, sync_manager.py, api_items.py, api_tags.py,
                 api_history.py, api_system.py, api_calendars.py, api_vault.py, api_files.py, api_search.py
      web/       index.html; css/ (app.css, canvas.css, file_timeline.css, path_bar.css, fonts.css,
                 tokens-<theme>.css x10, fonts/); js/ (ES modules: store, timeline, packing, minimap,
                 filter_bar, side_panel/*, file_timeline/*, shortcut_table, path_bar, easter_eggs, ...)
  tests/
    py/ (pytest), js/ (node --test), e2e/ (Playwright), manual/
```

**Architectural rules:** tags, sync logic, change log, undo, ICS mapping, date-source selection and vault metadata logic are pure Python; packing (both modes), zoom bands, label policy, file grouping and the minimap's scaling are pure JavaScript modules with no DOM dependency, each unit-tested. Packing lives in JavaScript because label reservation needs browser text metrics. The Gantt and file-browser stores are separate; the popup uses the Gantt ruler and zoom to determine its period.

### 10.2 Web architecture and security

| Topic | Requirement |
|-------|-------------|
| Process model | One Python process: Flask on a background thread; the pywebview window on the main thread; the tray icon on its own thread (created at startup, R-104); sync, vault capture, File timeline scanning and the Everything worker on their own threads. **Quit** (tray, View menu, Ctrl+Q) shuts down watchers and workers, stops the server, runs the exit backup, removes the tray icon, releases the lock and exits. **Closing the window hides it** (R-104). A hidden or minimized window never saves its geometry. |
| Single instance | A lock file in the data folder. A second launch finds the first window by its native window class (ignoring shell proxy windows), shows and restores it even if hidden, brings it to the front and exits. |
| Binding | `127.0.0.1` only, ephemeral port. Never `0.0.0.0`. |
| Request authentication | A random per-launch token is required on every API, SSE and thumbnail request. Requests without it get 403. |
| DNS-rebinding and CSRF | `Host` must be `127.0.0.1:<port>`; `Origin`, when present, must match. No CORS headers. State-changing requests use `POST`/`PATCH`/`DELETE` with JSON bodies. |
| Content security | Strict `Content-Security-Policy` (`default-src 'self'`; `img-src 'self'` for thumbnails; no inline script; no remote origins). Titles, notes, file names and search results are inserted as text, never HTML. |
| File access surface | The API never accepts an arbitrary path to read or serve. Thumbnails and file details are addressed by opaque ids of currently indexed entries inside current roots (R-154). Opening a file or attachment re-validates the target. Everything results are returned as data only. |
| Browser behavior | Release builds: developer tools off; the app's own context menu everywhere except text fields, where the WebView2 native menu (with spelling suggestions, R-190) is used; `alert`/`confirm`/`prompt` never called; page zoom suppressed in favor of timeline zoom; text selection only in editable fields and notes. |
| Database access | Each request handler uses its own short-lived connection with the §5.1 PRAGMAs. All writes go through one module that follows §5.3. The API never exposes raw SQL. |
| Live refresh | A watcher polls `PRAGMA data_version` every 1 s and pushes an SSE event; the client refetches. SSE also carries sync progress, capture status, scan progress and Everything replies' readiness. |
| Window state | Geometry via pywebview; view state in the `setting` table, not `localStorage`. |
| Network | Only for ICS URL sources the user configured (R-135). No other network access. |

### 10.3 Attachment vault backend

The vault is a **content-addressed store of whole-file zlib blobs named by the SHA-256 of the original content** (`src/reframe/vault/store.py`). It was chosen over a restic repository and a private Git object store because it is the simplest candidate that meets every criterion: verified restore of every captured version; atomic, crash-safe staged writes; deduplication of identical content; measurable size reporting; coordinated backup (§8.4); standard library only; no password or key that could lock the user out; no external executable. Measurements and operational limits are in `tools/vault_bench/RESULTS.md`; `tools/vault_bench/bench.py` reproduces them with synthetic files only.

Layout under the vault folder: `objects/ab/cdef….zlib` (a zlib stream of the original bytes) and `staging/` (in-progress writes). Rules:

- Content is staged (compressed into `staging/`, flushed, re-read, decompressed and re-hashed), then moved into `objects/` atomically; **only after that** is the `file_version` row committed (store first, metadata second). A crash leaves at most unreferenced staged content, which startup recovery removes.
- A blob that already exists is never rewritten (whole-file deduplication). Hard links are never used as snapshots, because an in-place overwrite of the source would change the "copy".
- zlib level 1 with a compressibility probe: most attachments are already compressed, so a higher level costs time and saves little.
- Capture never holds a database transaction while reading or writing file content.
- The vault folder must not be inside a OneDrive or other synchronised folder (R-166).

### 10.4 Tooling

`tools/install_shortcuts.py` (R-171, Windows), `tools/make_icon.py` (renders `assets/reframe.ico` from the tray tile artwork), `tools/fetch_fonts.py` (downloads and licence-checks the bundled fonts), `src/reframe/seed.py` (demo database including tag groups, retired tags and an ICS source).

---

## 11. Verification plan

Every requirement has an automated test where one is feasible; checks that need real external systems (Outlook, Everything, a real window and tray) are listed in `tests/manual/`. Run the suites from the install folder: `python -m pytest`, `node --test tests/js/*.test.js`, and (with Playwright and a Chromium-family browser installed) the browser smoke tests in `tests/e2e/`.

| Area | Test |
|---|---|
| Packing | Determinism; no overlap within a row; compact row count = max concurrency without labels; **strict mode: one item per row, rows in sort-key order, later start ⇒ lower row**; per-lane packing; stability under pan and within a band; no repack during a drag; filter / calendar-visibility repack |
| Minimap parity | Given a packing result, the minimap row/lane mapping equals the main chart's for: flat, grouped, compact, strict, filtered, calendar-hidden; row compression when rows exceed pixels; redraw on group-by toggle |
| Dates | Inclusive end; Outlook all-day end; meeting ending 00:00; midnight crossing; DST days; **ICS: TZID, UTC, floating, all-day, missing DTEND, RECURRENCE-ID override, EXDATE, cancelled** |
| Tags | Normalization; Python/SQL `norm_key` equality; autocomplete ranking excluding retired; merge/rename one undo; **reorder of system, implied and series tags persists (R-114 regression)**; **tag tree: implied insert, removal of unneeded implied tags, explicit ancestors kept, refusal to remove a still-required ancestor, cycle rejection, move tag reconciles all items as one change set and one undo, agent raw-SQL insert gets ancestors, trigger-generated rows are change-logged, undo replays without trigger interference**; retire/un-retire hides chip and autocomplete, keeps item rendering |
| Filter | All/None/Untagged semantics in OR and AND; retired chip shown only while active; chip drag onto item adds tag once, to selection, to series; Esc cancels; click still toggles |
| Change log | Direct SQL without protocol → `external`, undoable; leftover `app_context`; no duplicate rows; undo/redo per R-81; revert conflict; protective triggers incl. `ics` locks, `file_version` immutability, tag cycles |
| Sync | Fake Outlook adapter: add, move, cancel and reappear, series and exceptions, duplicates across calendars, hidden items and series, removal guard, partial reads; **all calendars connected by default; new folder auto-connected hidden; visibility excludes from layout/search/counts; disconnect soft-deletes in one change set and reconnect restores annotations**. ICS with local fixture files and a local test HTTP server: add, move (annotations kept), cancel and reappear, series tags, hidden series, removal guard per source, partial read (HTTP 500, truncated file) causes no removals, 304 no-op, URL masked in API output and logs |
| Attachments / vault | A/B/C pinning; shared-content dedup; pinned vs. original open; changed, renamed, missing original; changes while app closed (reconcile); locked file; partial write / changed during read; Unicode and long paths; unavailable share; permission error; disk full; kill during capture then restart recovery; concurrent attachments; link removal + undo keeps pin; duplicate item shares pins; pin basis of links that pre-dated protection; coordinated backup and full restore after simulated vault loss |
| Everything | Adapter with a fake SDK: result copying, paging, total count, stale reply discard, each failure state distinct from no matches, recovery after restart. Manual: real Everything with Unicode names, spaces, commas, quotes, network shares, add/rename/delete freshness |
| File browser | Synthetic trees: single and multiple roots, file-type filtering, dotless and multi-part extensions, exclusions, hidden/system toggle, created/date-taken placement, EXIF present/absent/malformed/with offset, MP4 Media created present/absent/invalid, junction not followed, offline share, access denied, refresh after create/rename/delete, root changes, dense periods, future undated files, Gantt state preserved, thumbnail endpoint rejects ids outside roots and raw paths |
| Concurrency | App open + external writer → refresh ≤ 2 s without losing selection or scroll; BUSY retry; `BEGIN IMMEDIATE` on every write; capture never holds a DB transaction during file I/O |
| Backup | Consistency while writing; temp-then-move; retention incl. vault references; coordinated restore; incomplete set never offered |
| UI smoke (Playwright) | core timeline flows (create, drag, resize, edit, minimap binding, shortcuts, theme persistence, no dialogs); ruler file popup timing, both rows, all zoom scales, filtering, keyboard use and file drag-to-attach; layout switch; View ▸ Theme for all nine themes and no theme button; Settings has no Appearance section; All/None; chip drop; tag tree drag; retired section; path bar path vs. search mode with a fake search backend; calendars menu; HAL after the second locked-edit attempt in 2001 only; monolith in 2001 only; no `alert`/`confirm` calls |
| Themes | Contrast test (§7.3) over all ten token files; every role token defined in every file; no hard-coded colors outside token files |
| Launch & tray | `TrayController` with fakes: icon present from start; close hides; minimize hides when enabled; restore by any route; Quit stops icon then exits; Start with Windows creates/removes only its own shortcut. Manual: double-click shortcut shows no console; taskbar shows the Reframe icon and groups with a pinned shortcut; `--tray` starts hidden; second launch restores |
| Spell check | Manual: misspelled word in notes shows suggestions on right-click and choosing one replaces the word |
| Layout unit tests (`node --test`) | Packing (both modes), zoom bands, label policy, minimap scaling, file bucket grouping |
| API and security | Token required; wrong Host/Origin rejected; no raw SQL; CSP present; HTML in titles, file names and search results rendered as text; no arbitrary-path endpoints |
| Agent protocol | Instruction-file SQL recipes run against a fresh v2 database; helper commands listed in the file all exist; no machine-specific paths in the file |
| Performance | Seeded 10,000-item database meets §4.2 in both layout modes; the file timeline is measured on a ≥ 10,000-file synthetic tree and its budgets recorded before acceptance |
| Manual | Real Outlook sync with all calendars; a real ICS feed; contrast review in all themes; keyboard-only walkthrough; launcher, tray and taskbar identity on the target platform; checklist in `tests/manual/` |

---

## 12. Known risks


| # | Risk | Notes / mitigation |
|---|------|--------------------|
| 1 | Outlook COM and corporate policy | IT may block automation or force "new Outlook" (no COM), ending Outlook import. ICS feeds are a partial fallback for calendars that offer them. |
| 2 | Connecting every calendar | First full sync of all ~15 calendars took about 2 minutes in testing; it runs in the background and items are hidden until shown, but the database grows (~3,400 items). Removal guard and per-source reporting limit damage from partial reads. |
| 3 | Tag-tree triggers | Complex trigger logic in SQLite; covered by exhaustive tests, undo suppression rule and the hygiene repair (R-46). |
| 4 | Version capture is best effort | Edits while Reframe is closed or quickly overwritten may be missed; stated in the UI (R-163). Vault storage can approach the size of the attached files. |
| 5 | File timeline scale | Large network trees can be slow to scan; scanning is streamed, cancellable and bounded; budgets are measured before acceptance. |
| 6 | Strict Gantt row count | One row per item can mean thousands of rows; vertical culling and minimap row compression are mandatory (§4.2, R-21). |
| 7 | Dark themes | Every role token and canvas color must be re-checked on the dark themes (§7.3). |
| 8 | Close-to-tray | Closing the window hides it instead of exiting; the first-close notice (R-104) and Ctrl+Q mitigate. |
| 9 | WebView2 dependency and shortcut capture | Some accelerators may not be overridable; each in §6.7 is tested and remapped if needed. |
| 10 | Local web surface | Loopback only, per-launch token, Host/Origin checks, no arbitrary-path endpoints (§10.2). |
| 11 | Agents bypassing triggers | `DROP TRIGGER` or schema edits cannot be prevented; backups and hygiene repair are the defence. |
| 12 | Proprietary design references | Themes are derived styles only; no logos, wordmarks or proprietary fonts (§7.3, R-183). |

---

## 13. Design decisions

### 13.1 Product decisions


| Decision | Choice | Why |
|----------|--------|-----|
| Tray | Optional start at sign-in hidden in the tray; icon always present; Close hides to tray; Quit exits | Sync, live refresh and version capture keep running while the window is hidden |
| External calendars | Outlook (all folders) + read-only ICS files and URL feeds; all connected by default, each shown on demand | Commitments already live in calendars; read-only import cannot damage them; showing on demand keeps the timeline readable |
| Tag filter toggle-all | All / None buttons with existing AND/OR semantics (plus an Untagged chip); no separate exclude mode | One filter model; the Untagged chip stops untagged items being lost |
| Easter eggs | Both: HAL on the **second** consecutive locked-edit attempt, and the monolith search | Harmless, theme-specific, never write data |
| Theme switching | Only View ▸ Theme (no toolbar button, no Settings section) | One place for the choice; fewer controls |
| Hide past | No hide-past control; past items look like future ones | Only the today shading distinguishes them (R-03, R-04) |
| Strict vs. compact | Toolbar/View toggle; compact is the default | Strict shows a classic Gantt order; compact uses less height |
| Minimap | Mirrors the main chart's lanes, rows and layout mode | The overview must show what the main field shows |
| Tag groups | Parent-tag tree; ancestors applied as implied tags; enforced in the database so agents match the UI; new tags groupless | Folders for tags; database enforcement keeps agents and UI consistent |
| Everything route | SDK via `ctypes` (integrated, frequent, paged search), not ES or HTTP | Best fit for integrated, frequent, paged search |
| Vault backend | Content-addressed store of whole-file zlib blobs named by SHA-256 (`src/reframe/vault/store.py`), zlib level 1 with a compressibility probe; restic and the Git object store rejected | Simplest candidate meeting the §10.3 criteria: stdlib only, no password, no external executable; measurements in `tools/vault_bench/RESULTS.md` |

### 13.2 Baseline decisions

| Decision | Choice |
|----------|--------|
| Tracks vs. flat | Flat packing, tags for color, filters as a lens, optional group-by |
| Row packing scope | Whole visible set, not only the window |
| Filter behavior | Hide and re-pack |
| Past / future | No visual difference |
| Daily log presentation | Log entries are ordinary items (`kind = 'log'`) in the flat timeline with a day panel (chosen over a separate log band or a separate journal view for simplicity) |
| Outlook access | Classic Outlook COM, read-only |
| Recurring items | Only from imports, stored as individual occurrences |
| Status / done | None |
| Storage | SQLite file in local app data with backups to a chosen folder |
| Agent safety | Full change log, easy undo, no confirm-before-write |
| Ctrl+Z scope | Latest change set by anyone; status bar names what was undone |
| Import scope | Everything except cancelled meetings (treated as removed); hide per item or series |
| Moved recurring occurrence | Annotations follow the occurrence (original-start key) |
| Tags on recurring meetings | Per series, propagated to new occurrences |
| System tag position | Inserted after the user's tags (now reorderable, R-114) |
| Silent tag merge | Case and punctuation only; everything else a visible suggestion |
| Agent write path | Direct SQL or the helper; helper preferred |
| Date entry | ISO with or without dashes |
| Day shading | Weekends only |
| Week start | Sunday, US week numbers |
| Multi-select | Full (drag several, bulk edit tags/kind) |
| Tag colors | 8 hues × solid/hatched = 16 slots per theme |
| Group by tag | On by default; remembered |
| Minimap colors | Main chart's tag colors as plain fills, no border or halo |
| Copy tags and duplicate | One Ctrl+drag gesture, two outcomes; keyboard equivalents |
| Filter tag chips | Wrap onto several lines, never scroll sideways |
| UI technology | Flask + HTML/CSS/JS in a pywebview (WebView2) window; canvas timeline; no framework, no build step |
| `tag.norm_key` | Generated stored column with `UNIQUE`; ASCII-only folding, matching Python |
| Change-set label | Updatable only while it is the open set |
| Series and non-recurring keys | Calendar-scoped series id; non-recurring Outlook key without date |
| Removal guard | ≥ 3 items **and** > 20 % of in-window items per source |
| Restore | Safety backup first, integrity check, backup API into the live file |
| Tag auto color | Least-used base hue at creation; NULL slot shown by creation order |
| Tests and backups | `REFRAME_BACKUP_DIR` / `REFRAME_DATA_DIR` keep tests out of the user's folders |
| Item fetch | Full item list once, refreshed on change |
| Bulk date edits | `move`, `shift_start`, `shift_end` as one change set each |

## 14. Platform support

Reframe's logic (database, sync rules, tags, undo, vault, layout, UI) is platform-neutral. The following pieces are platform-specific. The table gives the reference (Windows) mechanism and the behavior the app must have wherever it is installed; `INSTALL.md` turns each row into an installation step for the agent that installs it.

| Area | Windows reference mechanism (where in the code) | Required behavior on every platform; macOS approach |
|------|--------------------------------------------------|------------------------------------------------------|
| Data folder | `%LOCALAPPDATA%\Reframe\` (`db/connection.py: data_dir`) | A per-user, non-synchronised application-data folder. macOS: `~/Library/Application Support/Reframe/`. `REFRAME_DATA_DIR` always overrides |
| Window host | `pywebview` with Edge WebView2 (`host.py`); the WebView2 runtime must be installed | A native window hosting the loopback page. macOS: pywebview with WKWebView (`pyobjc`) |
| Launch without a terminal (R-170) | `Reframe.pyw` run by `pythonw.exe`; shortcuts | An icon or single file that starts the app with no terminal. macOS: a generated `Reframe.app` bundle whose executable starts the app (a `.command` file opens Terminal and does not qualify) |
| Taskbar/Dock identity (R-174) | `SetCurrentProcessExplicitAppUserModelID` plus `.ico` (`launch.py`, `shortcuts.py`) | The Reframe icon, never the interpreter's. macOS: `Reframe.icns` inside the `.app` bundle; the AppUserModelID call is a no-op |
| Start at sign-in (R-173) | `.lnk` in the Startup folder launching `--tray` (`shortcuts.py`) | A per-user login item that launches with `--tray`. macOS: a LaunchAgent plist or a Login Item pointing at the `.app` |
| System tray (R-104) | `pystray` icon on its own thread (`tray.py`, `host.py`) | A status icon with Show, Sync now, Start at sign-in, Quit. macOS: a menu-bar item; `pystray` and `pywebview` both want the main thread there, so the installer verifies that they coexist and otherwise runs the status item in a helper process or disables the tray (closing the window then quits) |
| Single instance and focusing the running window (R-102) | `msvcrt.locking` lock file; `win32gui` window enumeration (`host.py`) | One running copy; a second launch shows and focuses it. macOS: `fcntl.flock` on the lock file; activate the running app (`open -a` or AppleScript) |
| Opening files and folders (R-71, R-147, R-162) | `os.startfile`, `explorer /select,` (`server/api_items.py`, `server/api_vault.py`, `files/service.py`, `host.py`) | Open with the default application; reveal in the file manager. macOS: `open <path>` and `open -R <path>` |
| Path handling | `ntpath` normalisation, drive letters and UNC paths, case-insensitive keys (`files/roots.py`, `vault/store.py`, `vault/capture.py`; in the page: `js/path_bar.js`, `js/names.js`) | Absolute-path recognition, normalisation, containment checks and file-name extraction for the platform's own path syntax. macOS: POSIX paths, case-sensitive keys by default (the volume may be case-insensitive: compare with the volume's rule), `/` separators |
| File change notification (R-149, R-163) | `ReadDirectoryChangesW` via `pywin32` (`files/watch.py`, `vault/watch.py`) | Prompt notice of changes under attached files and timeline roots; where no watcher is available the periodic reconciliation (R-149, R-163) still runs. macOS: FSEvents through the `watchdog` package, or reconciliation only |
| File-type icons (R-146) | Shell icon lookup through `pywin32` (`files/icons.py`) | An icon per extension without opening the file. macOS: `NSWorkspace` icon for file type, or a generic icon |
| Media created date (R-144) | Windows property store (`files/dates.py`) | MP4/MOV creation time when present, else filesystem creation time. macOS: `mdls -name kMDItemContentCreationDate` or `ffprobe`, else `st_birthtime` |
| Local file search (R-120 to R-128) | voidtools Everything through its SDK DLL (`search/everything.py`) | Optional. Same request/response contract (request ids, paging, distinct failure states). macOS: an adapter over Spotlight (`mdfind`), or disabled with `everything_enabled = false` |
| Outlook calendar (R-60 to R-69) | Classic Outlook through COM (`outlook/adapter.py`) | Optional. Windows only. Elsewhere: ICS file/URL sources (R-135 to R-139) or an adapter implementing the `Adapter` protocol of `outlook/adapter.py` over the platform's calendar store (macOS: EventKit through `pyobjc`, or the Calendar app's ICS export) |
| Spell-check suggestions (R-190) | WebView2 native text-field menu enabled in `host.py` | Spelling suggestions on right-click in notes fields, or spell checking switched off. macOS: WKWebView provides the native menu |
| Startup failure notice (R-170) | `MessageBoxW` (`launch.py`) | One native message when no window exists. macOS: `osascript -e 'display dialog …'` |
| Keyboard modifier | `Ctrl` in every shortcut (`js/shortcut_table.js`) | The platform's primary modifier. macOS: `Cmd` (the shortcut table lists `Ctrl`; the macOS adaptation maps it to `Cmd`) |
| SQLite version | Python's bundled SQLite | 3.44 or newer: `v_item` uses `group_concat(... ORDER BY ...)`. The installer verifies this on the interpreter it uses |
| Fonts | Segoe UI as the fallback family (`css/fonts.css`) | A system UI font fallback (`system-ui`) on macOS; bundled open-licence fonts are unchanged |

Platform-specific code is isolated in the modules named in the table so that an installer changes those modules and nothing else. The first run on a new platform must complete §11's automated suites (the Windows-path unit tests are expected to be replaced or parameterised by the platform adaptation) and the manual checklist.

---

## Appendix A. Outlook automation facts

Behavior of classic Outlook's COM interface observed during development, which the Outlook import (§3.6.3) relies on:


| Finding | Consequence |
|---------|-------------|
| COM works without Azure registration; no Object Model Guard prompt with the minimal property list | R-60; keep the property list in §10 |
| Calendar folder names repeat (two "Calendar" folders) | Sources shown with folder path (R-61) |
| Holidays calendar readable; all items all-day | R-45, R-61 |
| `Sort` + `IncludeRecurrences` + `Restrict` expands series correctly | R-62 |
| Occurrences share the series GlobalAppointmentID, which carries no date | R-63 key = calendar + series id + original date |
| Original date of moved occurrences recoverable via `RecurrencePattern.Exceptions` (3 of 3) | R-63 annotations follow moved occurrences |
| All-day end is the next midnight (161 of 161) | R-64 |
| pywin32 attaches a wrong tz label; values are local wall-clock | R-64: strip tzinfo, never `astimezone()` |
| Non-recurring holiday items share GlobalAppointmentID + start; one id in two calendars | EntryID fallback; calendar in key |
| Non-recurring items exist beyond 730 days | No upper bound for non-recurring (R-62) |
| ~15–19 ms per item; 15 calendars / 3,412 items in 126 s; second sync a no-op | R-134 background sync; keys stable |
| Microsoft 365 group calendars visible but may return 0 items | Connected anyway; reported inline (R-61) |

---
