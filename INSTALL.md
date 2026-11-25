# Installing Reframe (agent-driven)

This file is written for a coding agent installing Reframe for a user on **Windows** or **macOS**. Work in this order: ask the questions (§1), install dependencies (§2), apply the platform changes (§4), create the launcher (§5), verify (§6). Read [SPEC.md](SPEC.md) for what the app should do; this file only covers getting it running on a given machine.

Never write the user's personal paths, email addresses or account names into files in this repository. Machine-specific choices go in the app's settings (stored in the database) or in an untracked `agent/local-config.md`.

## 1. Ask the user first

Ask all of these before installing anything. Offer the default in brackets. Detect the OS yourself; do not ask.

1. **Install location** for the app folder. Offer: leave it where this repo was cloned, `%LOCALAPPDATA%\Programs\Reframe` (Windows), or `~/Applications/Reframe` (macOS). Do not guess a location from the user's home folder listing or from other folders you can see, and never write a resolved personal path into any file of this repo.
2. **Calendar integration** — none / ICS file or URL (any OS; Google, Outlook.com, iCloud, Exchange publish ICS links) / classic Outlook desktop (Windows only; needs `pywin32`; the new Outlook has no COM interface and cannot be used) [none; add ICS later in the app]. On macOS the Calendar app has no ICS URL; export or subscribe via an ICS link, or ask whether to build an adapter (§4).
3. **File search** — Windows: use voidtools Everything [yes]. macOS: Everything does not exist; ask whether to build a Spotlight (`mdfind`) adapter or turn file search off [off]. The file timeline browser works without it.
4. **Start at sign-in**, hidden in the tray [no]. **Minimize/close to tray** [yes]. **Sync calendars on launch** [yes if a calendar was chosen].
5. **Data folder** [OS default, §3], **vault folder** [inside data folder], **backup folder** [Documents/Reframe Backups].
6. **Shortcuts** — Start menu / Desktop / Dock or Applications [Start menu or Applications].
7. **AI agent use** — will an agent read or write the database? If yes, point it at `agent/reframe-agent-instructions.md`, and record the chosen app, database and interpreter paths in `agent/local-config.md` (do not commit it).
8. **Folders to show in the file timeline** (optional list of roots).

Apply the answers as described in §7.

## 2. Dependencies

| Dependency | Windows | macOS |
|---|---|---|
| Python 3.12+ | python.org installer or `winget install Python.Python.3.12`; tick "Add to PATH" | `brew install python@3.12` or python.org installer |
| Python packages | `pip install -r requirements.txt` (includes `pywin32`) | same; `pywin32` is skipped automatically; pywebview pulls in PyObjC |
| Web view | Microsoft Edge WebView2 Runtime (preinstalled on current Windows 10/11; else install the Evergreen runtime) | WKWebView (built in) |
| voidtools Everything | Install from voidtools.com and **keep it running** (a service or tray instance). Only needed if file search is wanted. The SDK DLL is bundled in `vendor/everything/`. | not applicable |
| Outlook | Classic Outlook desktop, only if chosen | not applicable |
| SQLite | bundled with Python; must be ≥ 3.44 (ordered `group_concat`). Check: `python -c "import sqlite3;print(sqlite3.sqlite_version)"` | same; python.org/Homebrew builds are new enough, the system Python may not be |
| Playwright | optional, only for browser e2e tests (`requirements-dev.txt`) | same |

Use a virtual environment if the user wants isolation; the launcher must then use that interpreter (§5).

## 3. What the app is and how it is built

Reframe is a local web app in a native window.
- **Backend** (`src/reframe/`): Python 3.12, Flask bound to `127.0.0.1` on a random port with a per-run token; `server/` holds API blueprints; `domain/` the business rules; `db/` SQLite (WAL) with triggers, change log, undo and migrations; `vault/` content-addressed attachment versions; `calendars/` ICS sync; `outlook/` Outlook COM (Windows); `search/` Everything SDK; `files/` file timeline scanner.
- **Frontend** (`src/reframe/web/`): vanilla JavaScript and canvas, no build step.
- **Shell**: `host.py` starts the server and a pywebview window, a pystray tray icon, and a single-instance lock. `launch.py` is the entry point with crash reporting. `Reframe.pyw` is the double-click launcher (`--tray` starts hidden).
- **Data**: default data folder is `%LOCALAPPDATA%\Reframe` (Windows) or `~/Library/Application Support/Reframe` (macOS). Overrides: `REFRAME_DATA_DIR`, `REFRAME_DB`, `REFRAME_BACKUP_DIR`. Delete the app folder freely; data stays.
- **CLI/agent**: `python -m reframe.reframe_db` (with `src` on `PYTHONPATH`), documented in `agent/`.

Development run without a window: `python Reframe.pyw --no-window` (prints the URL).

## 4. Per-system change points

The code is the Windows reference. On Windows nothing needs changing beyond configuration. On macOS, make these changes (guard with `sys.platform`, keep Windows behaviour intact), then run the tests.

| File / function | Windows mechanism | macOS action |
|---|---|---|
| `outlook/adapter.py` | Outlook COM via pywin32 | Leave disabled; ensure sync-on-launch fails gracefully (it must). Offer ICS instead. Optional: an EventKit/AppleScript adapter for Calendar.app with the same interface. |
| `search/everything.py` | Everything DLL via ctypes | Replace with an `mdfind`-based adapter returning the same result shape, or set `everything_enabled` false. Make the DLL load lazily so import never fails. |
| `files/scanner.py`, `service.py` | Win32 file attributes, `explorer /select,`, `os.startfile` | Use `st_flags` for hidden (`UF_HIDDEN`), `open` and `open -R` for open/reveal. |
| `files/roots.py` | `ntpath` normalisation, drive letters | Use `posixpath`/`pathlib`; case-sensitive compare per volume. |
| `files/icons.py` | Shell icon lookup (pywin32) | Return a generic icon, or use `NSWorkspace`. |
| `files/dates.py` | Property store media-created date | Use `mdls kMDItemContentCreationDate` or skip. |
| `vault/capture.py`, `store.py`, `service.py` | `\\?\` long paths, `normcase` | Plain paths; `normcase` is a no-op. |
| `vault/watch.py` | `ReadDirectoryChangesW` | Poll on a timer, or use `watchdog`/FSEvents. |
| `vault/materialize.py`, `server/api_items.py` | `os.startfile` | `subprocess.run(["open", path])`. |
| `host.py` | `msvcrt` single-instance lock, `win32gui` focus, message box | `fcntl.flock` lock file; activate via pywebview; native alert via `osascript`. |
| `shortcuts.py`, `tools/install_shortcuts.py` | `.lnk` files, Startup folder, AppUserModelID | A `.app` bundle (§5) and a LaunchAgent plist for start-at-login. |
| `tray.py` | pystray (Win32) | pystray on macOS needs the main thread, which pywebview also wants. **Verify** both coexist; if not, run the tray as a separate helper process or use `rumps`. |
| `launch.py` | `MessageBoxW` error report | `osascript -e 'display alert …'`. |
| `db/connection.py` | `%LOCALAPPDATA%` | already handled (Application Support). |

**Two macOS risks to verify before declaring success:** (1) pystray and pywebview both need the main thread; (2) system SQLite older than 3.44 breaks ordered `group_concat` (the §2 check).

## 5. Launcher (no terminal)

**Windows.** Run `python tools\install_shortcuts.py --start-menu [--desktop] [--startup]`. This creates `Reframe.lnk` targeting `pythonw.exe Reframe.pyw` with the bundled icon (`assets/reframe.ico`). Using `pythonw` means no console window. If a virtualenv is used, run the tool with that interpreter so the shortcut points to it. Remove with `--remove`.

**macOS.** Create `Reframe.app` in `~/Applications` (or the chosen folder):
```
Reframe.app/Contents/Info.plist        (CFBundleExecutable=Reframe, CFBundleIconFile=Reframe, LSUIElement unset)
Reframe.app/Contents/MacOS/Reframe     (chmod +x shell script: exec "<python>" "<app dir>/Reframe.pyw" "$@")
Reframe.app/Contents/Resources/Reframe.icns
```
Generate the `.icns` from `assets/reframe.ico` (Pillow can export a PNG; then `iconutil`). Use an absolute interpreter path in the script. For start at login, write `~/Library/LaunchAgents/reframe.plist` running the same script with `--tray`, and map the settings toggle to it.

## 6. Verify

1. `python -m pytest` — on macOS/Linux expect Windows-only tests to fail or skip; everything else should pass. `node --test tests/js/*.test.js` should pass.
2. `python Reframe.pyw --no-window` prints a URL; fetch it and confirm the page loads, then stop it.
3. Launch from the new icon. Confirm: window opens with no terminal, a new item can be created, undo works, the tray icon appears, second launch focuses the first.
4. If a calendar was chosen: add the source in the app and run a sync; if ICS, confirm events appear.
5. If file search was chosen: search for any known file name.
6. Close and reopen; confirm data persisted, and a backup appears in the backup folder after the first backup run.

## 7. Mapping answers to configuration

| Answer | Where it goes |
|---|---|
| Data / vault / backup folder | env vars `REFRAME_DATA_DIR`, `REFRAME_BACKUP_DIR` (set as user environment variables; a `.lnk` cannot set them; on macOS put `export` lines in the launcher script), or the `vault_dir` / `backup_folder` settings |
| Calendar: none | nothing; set `sync_on_launch` false |
| Calendar: ICS | user adds source in app (Settings panel), `ics_refresh_min` |
| Calendar: Outlook | `pywin32` installed; choose calendars in app; `outlook_calendars` |
| File search off | `everything_enabled` false |
| Start at sign-in | `start_with_windows` setting plus Startup shortcut / LaunchAgent |
| Tray | `tray_on_minimize` |
| File timeline roots | `files_roots` |

Settings can be changed through the app's Settings panel; prefer that over editing the database.

## 8. Updating and removing

Updating: replace the app folder contents; data is elsewhere. Schema migrations run automatically; the app refuses to open a newer database than it understands. Removing: run `install_shortcuts.py --remove` (Windows) or delete `Reframe.app`/LaunchAgent (macOS), then delete the app folder. Delete the data folder only if the user wants their data gone.
