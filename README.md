# Reframe

A single-user desktop timeline for the work you are actually doing. Every commitment, event, plan and log entry is an item on a zoomable Gantt-style timeline, tagged in a tree, searchable, undoable, and linked to the files and calendar events that belong with it. It runs entirely on your machine: a local Flask server, a native window (pywebview), and one SQLite database. No cloud account, no telemetry.

**Highlights**
- Timeline of items (plan, event, log, deadline) with tag groups, implied tags, filters and a day panel
- Full change history with undo/redo; soft-delete only
- Calendar import: ICS file or URL on any OS; classic Outlook on Windows
- File timeline and instant file search (Everything on Windows)
- Attachment version vault (content-addressed, compressed)
- Tray icon, optional start at sign-in, automatic rotating backups
- A documented database and `reframe_db` helper so an AI agent can read and add items safely (`agent/`)

## Documents
| File | Purpose |
|---|---|
| [INSTALL.md](INSTALL.md) | Agent-driven install for Windows and macOS. Starts with questions about how you want it integrated. |
| [SPEC.md](SPEC.md) | The single source of truth for what the app is and does. |
| `agent/reframe-agent-instructions.md` | How an AI agent should read and write the database. |

## Quick start
Point a coding agent (Claude Code or similar) at this folder and say: *"Install Reframe following INSTALL.md."* The agent will ask a few questions, install dependencies, create a launcher icon, and verify the result.

Manual outline: install Python 3.12+, `pip install -r requirements.txt`, then run `pythonw Reframe.pyw` (Windows) or `python Reframe.pyw` (development). See INSTALL.md for icons and platform details.

## Status
The Windows code path is the reference implementation. macOS is supported through the port steps in INSTALL.md, which an agent carries out on the target machine; it has not been verified on every macOS version.

Run the tests with `python -m pytest` and `node --test tests/js/*.test.js`. Some tests require Windows.

## Data
Your database, vault and backups live outside this folder (see INSTALL.md §Where data goes). Updating the app never touches them.
