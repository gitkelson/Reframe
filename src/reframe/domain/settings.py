"""Settings stored in the ``setting`` table as JSON values (R-98).

View state (theme, zoom window, filter, panel layout) lives here rather than in
browser storage because WebView2 profile storage is not a durable location.
Settings are not part of the change log.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

DEFAULTS: dict[str, Any] = {
    "theme": "meridian",
    "group_by_tag": True,
    "layout_mode": "compact",          # 'compact' | 'strict' (R-17)
    "active_view": "gantt",            # 'gantt' | 'files' (R-18)
    "view_start": None,
    "view_end": None,
    "filter": {"tags": [], "tag_mode": "or", "text": "", "kind": [], "origin": [], "untagged": False},
    "side_panel_open": True,
    "side_panel_width": 360,
    "side_panel_tab": "item",
    "tag_tree_collapsed": [],          # tag ids collapsed in the tag manager (R-43)
    "backup_folder": "",
    "backup_keep_last": 20,
    "backup_keep_days": 30,
    "backup_keep_months": 12,
    "sync_on_launch": True,
    "tray_on_minimize": True,
    "start_with_windows": False,       # R-173 (start at sign-in, hidden in the tray)
    "tray_close_notice_shown": False,  # R-104 first close-to-tray notice
    "lookback_days": 30,
    "outlook_date_format": "%m/%d/%Y %I:%M %p",
    "first_sync_lookback_days": 365,
    "window_days": 42,
    "outlook_calendars": [],
    "ics_refresh_min": 60,             # R-134 default refresh for ICS URL feeds
    "everything_enabled": True,        # R-128
    "vault_dir": "",                   # R-166 ('' = default under the data folder)
    "files_roots": [],                 # R-140 File timeline roots
    "files_excluded_ext": [],          # R-148
    "files_include_hidden": False,     # R-148
    "files_view": {},                  # R-18 File timeline view state (zoom window, filter, date mode)
    "last_sync_at": None,
    "first_run_hint_dismissed": False,
    "window_geometry": None,
}


def _begin(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE")


def get_all(conn: sqlite3.Connection) -> dict[str, Any]:
    out = dict(DEFAULTS)
    for r in conn.execute("SELECT key, value FROM setting"):
        try:
            out[r["key"]] = json.loads(r["value"])
        except ValueError:
            pass
    return out


def get(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    r = conn.execute("SELECT value FROM setting WHERE key=?", (key,)).fetchone()
    if r is None:
        return DEFAULTS.get(key, default)
    try:
        return json.loads(r["value"])
    except ValueError:
        return DEFAULTS.get(key, default)


def set_many(conn: sqlite3.Connection, values: dict[str, Any]) -> None:
    unknown = [k for k in values if k not in DEFAULTS]
    if unknown:
        raise ValueError("Unknown setting(s): " + ", ".join(unknown))
    _begin(conn)
    try:
        for k, v in values.items():
            conn.execute("INSERT INTO setting (key, value) VALUES (?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, json.dumps(v)))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
