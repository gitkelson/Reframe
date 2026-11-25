# Reframe: agent instructions

Reframe is the owner's single chronological timeline of work commitments, expected events and a daily record of what was done. The database is a SQLite file that you may read and write. Follow this file exactly: the owner relies on the data being clean, and every change you make can be undone in the app (Ctrl+Z) if you follow the rules below.

Schema version: `PRAGMA user_version` = 2. If it is higher than 2, stop and tell the owner this file may be out of date.

## 1. Where the database is and how to open it

- Default path: `reframe.sqlite` inside the data folder: `%LOCALAPPDATA%\Reframe\` on Windows, `~/Library/Application Support/Reframe/` on macOS. The installer may have chosen another place: check `agent/local-config.md` if it exists, otherwise ask the owner. The environment variable `REFRAME_DB` overrides it (and `REFRAME_DATA_DIR` moves the whole data folder). The app shows the path in its status bar.
- The app may be open while you work. The file is in WAL mode, so reading and writing from another process is safe.
- **Every connection you open must set**:
  ```sql
  PRAGMA foreign_keys = ON;
  PRAGMA busy_timeout = 5000;
  ```
- **Every write starts with `BEGIN IMMEDIATE`** (never a plain `BEGIN` that later upgrades to a write; in WAL mode that can fail immediately with BUSY). Keep transactions short. In Python use `sqlite3.connect(path, autocommit=True)` and issue `BEGIN IMMEDIATE` / `COMMIT` yourself.
- Update only the columns you are changing, never a whole row copied from an earlier read. The owner may have edited another field a moment ago.

### Preferred path: the helper (`reframe_db`)

Run from the `src` folder inside the Reframe install folder (or add it to `PYTHONPATH`):

```
python -m reframe_db add-item --kind plan --title "Spares order due" --start 2026-01-28 --tag Spares --label "From 11/10 staff meeting"
python -m reframe_db add-items --file items.json --label "Staff meeting 2026-11-10"
python -m reframe_db update-item <id> --start 2026-02-02 --end 2026-02-02
python -m reframe_db delete-item <id>
python -m reframe_db schedule --from 2026-11-10 --to 2026-12-31 [--tag X] [--kind plan|log]
python -m reframe_db search "spares"
python -m reframe_db tags
python -m reframe_db tag-tree
python -m reframe_db suggest-tags "Spares Ordr"
python -m reframe_db move-tag "Line 2 retrofit" --to "Customer A"      (or --root to make it groupless)
python -m reframe_db retire-tag "Old project"
python -m reframe_db unretire-tag "Old project"
python -m reframe_db add-attachment <item id> "C:\path\to\file.pdf" [--label "Spec"]
python -m reframe_db calendars
python -m reframe_db find-duplicates --title "..." --start 2026-11-20
python -m reframe_db history --limit 10
python -m reframe_db undo
```

The helper opens the transaction and the change set for you, normalizes tags, validates dates, refuses to insert likely duplicates (it returns them under `skipped`; add `--force` only after you have confirmed with the owner), refuses to create a new tag that nearly matches an existing one (`--allow-similar-tags` only if the owner confirms), refuses to apply a retired tag (`--allow-retired` only if the owner confirms), takes a backup if the newest backup is more than an hour old, and prints JSON. `add-items` takes a JSON list of `{"kind","title","start","end","tags":[...],"notes","attachments":[{"target":"..."}]}` and writes everything in ONE change set, so the owner can undo a whole meeting with one Ctrl+Z.

### Alternative: direct SQL

Reading with SQL is always fine. Writing with SQL is allowed if you follow the protocol in section 6. The helper is preferred because it also does duplicate detection and tag resolution, which raw SQL does not.

## 2. The model in plain language

There is **one kind of thing on the timeline: an item.** Everything else is a label on an item.

| Column (`item`) | Meaning |
|---|---|
| `id` | Text id. Defaults in SQL (`lower(hex(randomblob(16)))`), so do not supply one. |
| `kind` | `plan` = something committed to or expected (deadline, delivery, span of work, meeting, holiday, PTO, trip). `log` = something that **happened**, recorded for the daily record. Nothing else distinguishes items. |
| `title` | Short, specific. Put context, attendees and sources in `notes`. |
| `start_date`, `end_date` | ISO `YYYY-MM-DD`. **Date only, no time of day.** `end_date` is **inclusive** and `>= start_date`. A one-day item has `start_date = end_date`. The minimum length is one day. Invalid dates (`2026-02-31`) are rejected by the database. |
| `notes` | Plain text, any length. |
| `origin` | `local` (typed by the owner or an agent), `outlook` (imported from classic Outlook) or `ics` (imported from a calendar file or feed). |
| `source_id` | The calendar source of an imported item (`calendar_source.id`); NULL for local items. Never set it yourself. |
| `outlook_*`, `outlook_uid`, `ics_uid` | Import bookkeeping. Read only. |
| `deleted_at` | Soft delete (set to a UTC timestamp). Rows are never hard-deleted. |
| `hidden_at` | The owner hid an imported item. |
| `created_at`, `updated_at` | UTC `YYYY-MM-DDTHH:MM:SS.sssZ`. **You must set `updated_at` yourself** on every update (`strftime('%Y-%m-%dT%H:%M:%fZ','now')`). The helper does. |

Other tables:
- `tag` (`name`; `norm_key` is generated by SQLite, so insert only `name`; `parent_id` = the group the tag belongs to, NULL = groupless; `retired_at` = retired tag).
- `item_tag` (`item_id`, `tag_id`, `position`, `implied`): lowest position is the primary tag and sets the bar colour. `implied = 1` marks a group tag the database added because a tag inside that group is on the item (section 2a). Imported system tags (`meeting`, `holiday`) are inserted at position 1000 or more; that is only where new ones start, the owner may reorder them.
- `attachment` (links: `kind` is `file`, `folder` or `url`, `target`, `label`; `pinned_version_id` and `pin_basis` are set by the app when it protects a file).
- `calendar_source` (one row per Outlook calendar folder or calendar file/feed; `connected` = imported, `visible` = shown on the timeline). Hidden calendars are a display setting only: their items are still in the database and in `v_item`.
- `file_source`, `file_version` (the app's protected copies of attached files): **read only for agents**.
- `outlook_series_tag` and `outlook_hidden_series` (series bookkeeping for Outlook and calendar feeds), `setting`, `sync_run`, and the change log tables (`change_set`, `change`, `app_context`) described in section 6.

**Always read items through the view `v_item`**: it already excludes deleted and hidden items and gives you the tags as one text column in colour order, plus the calendar name of imported items:

```sql
SELECT id, kind, title, start_date, end_date, notes, origin, tags, calendar FROM v_item;
```

Concept rules:
- The model is **flat**: there are no tracks, projects, status fields, priorities, checkboxes, "done" or "overdue". If something slips, **change its dates**. Never invent a status.
- A plan item is never "completed". What happened is recorded as a **new `log` item**; there is no link between the two.
- **Imported items (`origin` `outlook` or `ics`) are locked**: never change their `title`, dates, `kind`, `source_id` or `outlook_*`/`ics_uid` columns. The database refuses these edits. You may add tags, notes and attachments to them. Never delete them.
- Recurring items exist only as imported individual occurrences. Do not create recurring items.
- Tags are the only classification. Tag colour comes from the first tag.

### 2a. Tags: groups and retired tags

- **Tag tree.** A tag can belong to a group (another tag), like folders: `Projects > Customer A > Line 2 retrofit`. Read the tree from the view `v_tag` (`id, name, parent_id, path, depth, retired, usage`) or with `python -m reframe_db tag-tree`.
- **Insert the most specific tag only.** When you put `Line 2 retrofit` on an item, the database adds `Customer A` and `Projects` automatically as implied tags. **Never insert `implied = 1` rows yourself**, and never insert the group tags by hand.
- **New tags you create are groupless.** Do not set `parent_id` yourself. If you think a new tag belongs in a group, tell the owner (or use `move-tag` only when the owner asks).
- **Do not apply retired tags** (`tag.retired_at IS NOT NULL`, or `retired = 1` in `v_tag`). They stay on old items; they are not offered for new ones.
- **Position 0 is the primary (colour) tag.** Put the project or topic tag first.
- To remove a tag from an item, delete its row; the database removes implied group tags that are no longer needed. Removing a group tag while a tag inside the group remains is refused ("Remove the descendant tag first").

### 2b. Attachments

- Insert links normally (`kind` `file`, `folder` or `url`), or use `add-attachment`. The app captures a protected copy of every attached file and pins the link to it.
- **Never write `file_source`, `file_version` or anything in the vault folder.** Never set `pinned_version_id` or `pin_basis`.

## 3. Resolving dates from notes

- Use the note's own date as "today" (a meeting note dated 2026-11-10 means today is 2026-11-10), not the machine's date.
- Resolve relative phrases explicitly ("next Friday", "end of month", "in two weeks", "Q1") to ISO dates, and **state the resolved date back to the owner** in your report. If a date is ambiguous (e.g. "Friday" when it could be this or next week, or "1/2" with no year), ask rather than guess.
- "From Monday to Wednesday" means `start_date` Monday, `end_date` Wednesday (inclusive).
- A deadline or delivery is a one-day plan item on that day. A span of work (a trip, an install window, a vacation) is one item spanning its days.
- Typed dates accepted by the helper: `2026-11-08` or `20261108`. Anything else is rejected.

## 4. Meeting-notes ingestion workflow

When the owner pastes meeting notes:

1. **What happened that day** (decisions made, work done, calls held) becomes `log` items dated the meeting date.
2. **Future commitments, deliveries, deadlines, trips and absences** become `plan` items on their dates (a span if the notes give a range).
3. **Changes to existing dates** ("delivery slipped to 1/28") are updates to the existing item. Search first (`search`, `schedule`, `find-duplicates`) and update it; **never create a second copy.** If you are not sure which item is meant, ask.
4. **Tags: look up before you create.** Run `tag-tree` (or `suggest-tags "<name>"`) and reuse an existing, non-retired tag. Case and punctuation variants resolve to the same tag automatically ("Spares", "SPARES!", "spares" are one tag; "PRJ-20" and "PRJ 20" are one). Plurals, reordered words and typos are *not* merged for you: use the existing spelling. Use the most specific project or topic tag first, because it sets the colour; its groups are added for you. Do not add `meeting` or `holiday` yourself (they are system tags added by import).
5. **One change set per meeting.** Use `add-items` with a label naming the meeting and its date (for example `"Staff meeting 2026-11-10"`), or, in raw SQL, one writer transaction (section 6). This makes the whole ingestion a single undo.
6. **Report back** a concise list: every item created (id, kind, title, dates, tags), every item changed (before and after), and everything skipped (with the reason, for example "likely duplicate of ..."). State the resolved dates and tell the owner how to undo: *Ctrl+Z in Reframe, or* `python -m reframe_db undo`.

Where to put detail: short titles ("Spares order due", "Install window Cell 4"); context, attendees and the source ("from 11/10 staff meeting, per Dana") in `notes`.

## 5. Safety rules

- Never delete outright. Soft delete only (`deleted_at`). The database refuses hard deletes of items.
- Never modify imported core fields (title, dates, kind, `source_id`, `outlook_*`, `ics_uid`) of `origin` `outlook` or `ics` items.
- Never edit `change`, `change_set` or `app_context` directly except as the protocol in section 6 describes. The database aborts any other edit to the log.
- Never write `implied` group-tag rows, `file_source`, `file_version`, `calendar_source` or settings.
- Never drop or alter triggers, tables or views, and do not change the schema. If you think the schema must change, tell the owner.
- Always follow the protocol in section 6 when writing with SQL.
- Update only the columns being changed. Set `updated_at` when you do.
- Do not write a status, "done", priority or similar into titles or tags.
- If anything looks wrong (an error, an unexpected row count), stop, do not retry blindly, and tell the owner. State how to undo what you already did.
- Backups: the helper takes a backup first when the newest one is over an hour old. With raw SQL, ask the owner to run "Back up now" in Settings, or call `python -m reframe_db history` to see your change set and `undo` to revert it.

## 6. Writing with raw SQL: the change-log protocol

The app records every change (who, when, before and after) so any change set can be undone. Triggers on `item`, `tag`, `item_tag`, `attachment`, `outlook_series_tag` and `outlook_hidden_series` write the log rows for you, using the single row in `app_context`; the rows the tag-tree triggers add are logged too. Wrap every write like this:

```sql
PRAGMA foreign_keys = ON;  PRAGMA busy_timeout = 5000;      -- once per connection
BEGIN IMMEDIATE;
INSERT INTO change_set (actor, label) VALUES ('agent', 'Staff meeting 2026-11-10');
UPDATE app_context SET actor = 'agent', change_set_id = last_insert_rowid() WHERE id = 1;

-- ... your INSERT / UPDATE statements on item, tag, item_tag, attachment ...

UPDATE app_context SET actor = 'external', change_set_id = NULL WHERE id = 1;
COMMIT;
```

- Always run the last `UPDATE app_context` **before** `COMMIT`, in the same transaction. If you forget it, the next writer's changes would be credited to your change set.
- If you skip the protocol entirely, the triggers still log your changes as `external`, and the app groups them into one "External change" set that can be undone. That is a fallback, not a plan.
- Label the change set with what you did and when ("Add 3 items from 11/10 staff meeting").
- Insert a tag with only a name: `INSERT INTO tag (name) VALUES ('Spares Order')`. Look up first: `SELECT id, name, retired_at FROM tag WHERE norm_key = <key>`, where the key is the name lower-cased (ASCII), with every run of punctuation and whitespace replaced by one space and trimmed. A duplicate key is rejected by the database (`UNIQUE constraint failed: tag.norm_key`); when that happens, use the existing tag.
- Tag assignment: `INSERT INTO item_tag (item_id, tag_id, position) VALUES (?, ?, 0)`; use `0, 1, 2 ...` in the order you want, first = colour. Do not set `implied`; group tags follow automatically.
- To change an item: `UPDATE item SET start_date = ?, end_date = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = ?` (only the columns that change).
- To soft delete: `UPDATE item SET deleted_at = strftime('%Y-%m-%dT%H:%M:%fZ','now'), updated_at = ... WHERE id = ?` (local items only).
- Before inserting an item, look for a likely duplicate: same title (ignoring case and punctuation) or a similar title with overlapping dates. If there is one, update it instead of inserting.

## 7. Query recipes

What is on the schedule between two dates (items overlapping the range):

```sql
SELECT start_date, end_date, kind, title, tags FROM v_item
WHERE end_date >= '2026-11-10' AND start_date <= '2026-12-31'
ORDER BY start_date, end_date, title;
```

What did I do last week:

```sql
SELECT start_date, title, tags, notes FROM v_item
WHERE kind = 'log' AND end_date >= '2026-11-02' AND start_date <= '2026-11-08'
ORDER BY start_date;
```

Items tagged X (use the normalized key for the match):

```sql
SELECT i.start_date, i.end_date, i.kind, i.title
FROM v_item i
JOIN item_tag it ON it.item_id = i.id
JOIN tag t ON t.id = it.tag_id
WHERE t.norm_key = 'spares'
ORDER BY i.start_date;
```

The tag tree with paths, and active (non-retired) tags only:

```sql
SELECT path, usage FROM v_tag WHERE retired = 0 ORDER BY path;
```

Everything under group X (the group itself and every tag inside it, at any depth). Because group tags are applied automatically, items carrying the group tag are exactly the items under it:

```sql
SELECT i.start_date, i.title, i.tags
FROM v_item i
JOIN item_tag it ON it.item_id = i.id
JOIN tag t ON t.id = it.tag_id
WHERE t.norm_key = 'customer a'
ORDER BY i.start_date;
```

The same with a recursive query over the tree (useful when you need the member tags themselves):

```sql
WITH RECURSIVE g(id) AS (
  SELECT id FROM tag WHERE norm_key = 'projects'
  UNION ALL SELECT t.id FROM tag t JOIN g ON t.parent_id = g.id)
SELECT name FROM tag WHERE id IN (SELECT id FROM g) ORDER BY name;
```

Calendars and how many items each holds (hidden calendars are only hidden on screen):

```sql
SELECT cs.name, cs.kind, cs.connected, cs.visible, count(i.id) AS items
FROM calendar_source cs LEFT JOIN item i ON i.source_id = cs.id AND i.deleted_at IS NULL
GROUP BY cs.id ORDER BY cs.name;
```

What changed in the last session (most recent change sets first):

```sql
SELECT cs.id, cs.ts, cs.actor, cs.label, count(c.id) AS rows
FROM change_set cs LEFT JOIN change c ON c.change_set_id = cs.id
GROUP BY cs.id ORDER BY cs.id DESC LIMIT 20;
```

Everything one change set did: `SELECT table_name, row_key, op, before_json, after_json FROM change WHERE change_set_id = ? ORDER BY id;`

Imported items for a date: add `AND origin <> 'local'`. Items I (an agent) created: join `change_set` on `actor = 'agent'` through `change.row_key`.

## 8. Undoing

- In the app: Ctrl+Z undoes the newest change set by anyone (including yours); Ctrl+Y redoes it. The History tab lists every change set and can revert any one of them (refused with the conflicting rows listed if those rows were edited afterwards).
- From the command line: `python -m reframe_db undo` (and `redo`).
- Undo itself is logged as a change set with `actor = 'undo'`; history is append-only.
