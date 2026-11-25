"""Shared helpers for the API blueprints."""
from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Any, Iterator

from flask import current_app, g, jsonify, request

from ..db import undo as undomod
from ..db.changelog import group_external, writer
from ..db.connection import connect
from ..domain import backup, settings
from ..domain import items as itemmod
from ..domain import tags as tagmod


def db() -> sqlite3.Connection:
    """One short-lived connection per request, with the standard PRAGMAs."""
    if "conn" not in g:
        g.conn = connect(current_app.config["DB_PATH"])
    return g.conn


def body() -> dict[str, Any]:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


@contextlib.contextmanager
def user_write(label: str) -> Iterator[Any]:
    with writer(db(), "user", label) as w:
        yield w
    hourly_backup()


def hourly_backup() -> None:
    """At most one backup per hour while editing (spec 8.4). A failure is reported and retried on the next write."""
    conn = db()
    folder = Path(settings.get(conn, "backup_folder") or backup.default_backup_dir())
    try:
        backup.backup_if_stale(conn, folder)
    except OSError as e:
        current_app.config["HUB"].publish("backup", {"error": f"Backup failed: {e}. Check the backup folder in Settings."})


def err(message: str, status: int = 400, kind: str = "error", **extra: Any):
    return jsonify(error=kind, message=message, **extra), status


def register_errors(app) -> None:
    @app.teardown_appcontext
    def _close(exc):
        conn = g.pop("conn", None)
        if conn is not None:
            conn.close()

    @app.errorhandler(itemmod.LockedItem)
    def _locked(e):
        return err(str(e), 409, "locked")

    @app.errorhandler(itemmod.DateError)
    def _date(e):
        return err(str(e), 400, "date")

    @app.errorhandler(itemmod.NotFound)
    def _nf(e):
        return err(str(e), 404, "not_found")

    @app.errorhandler(tagmod.NearTagMatch)
    def _near(e):
        return err(str(e), 409, "near_tag", near=e.near)

    @app.errorhandler(tagmod.RetiredTag)
    def _retired(e):
        return err(str(e), 409, "retired_tag", tag=e.name)

    @app.errorhandler(itemmod.TagRequired)
    def _required(e):
        return err(str(e), 409, "tag_required", tag=e.tag, child=e.child)

    @app.errorhandler(LookupError)
    def _lookup(e):
        return err(str(e).strip("'\"") or "Not found", 404, "not_found")

    @app.errorhandler(undomod.RevertConflict)
    def _conflict(e):
        return err("Rows changed since this change set; revert refused.", 409, "conflict", conflicts=e.conflicts)

    @app.errorhandler(ValueError)
    def _value(e):
        return err(str(e), 400, "invalid")

    @app.errorhandler(sqlite3.IntegrityError)
    def _integrity(e):
        return err(f"Database rejected the change: {e}", 400, "integrity")

    @app.errorhandler(sqlite3.OperationalError)
    def _busy(e):
        msg = str(e)
        if "locked" in msg or "busy" in msg:
            return err("The database is busy. Try again in a moment.", 503, "busy")
        return err(f"Database error: {msg}", 500, "database")


def undo_state(conn: sqlite3.Connection) -> dict[str, Any]:
    group_external(conn)
    u, r = undomod.undo_target(conn), undomod.redo_target(conn)
    states = undomod.set_states(conn)
    return {"undo": {"id": u, "label": states[u]["label"]} if u else None,
            "redo": {"id": r, "label": states[r]["label"]} if r else None}
