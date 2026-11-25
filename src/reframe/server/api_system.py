"""Bootstrap, settings, Outlook, backup and host-bridge endpoints."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify, request

from ..db.connection import connect
from ..domain import backup as backupmod
from ..domain import items as itemmod
from ..domain import settings as settingsmod
from ..domain import tags as tagmod
from ..calendars import sources as sourcemod
from .common import body, db, err, undo_state

bp = Blueprint("system", __name__, url_prefix="/api")

from .. import __version__ as APP_VERSION


def _backup_folder(conn) -> Path:
    return Path(settingsmod.get(conn, "backup_folder") or backupmod.default_backup_dir())


@bp.get("/bootstrap")
def bootstrap():
    conn = db()
    tag_list = tagmod.list_tags(conn)
    total = conn.execute("SELECT count(*) FROM item WHERE deleted_at IS NULL AND hidden_at IS NULL").fetchone()[0]
    attention = itemmod.needs_attention(conn)
    return jsonify(
        version=APP_VERSION,
        db_path=str(current_app.config["DB_PATH"]),
        today=dt.date.today().isoformat(),
        settings=settingsmod.get_all(conn),
        item_total=total,
        tags=tag_list,
        hygiene=tagmod.hygiene(conn),
        implied_drift={k: len(v) for k, v in tagmod.implied_drift(conn).items()},
        calendars=sourcemod.visibility_summary(conn),
        needs_attention=sum(len(v) for v in attention.values()),
        undo_state=undo_state(conn),
        agent_instructions=str(current_app.config.get("AGENT_FILE", "")),
        backup_error=current_app.config.get("BACKUP_ERROR"),
        has_host=current_app.config.get("HOST") is not None,
    )


# ---------------------------------------------------------------- settings

@bp.get("/settings")
def get_settings():
    return jsonify(settingsmod.get_all(db()))


@bp.put("/settings")
def put_settings():
    values = body()
    settingsmod.set_many(db(), values)
    host = current_app.config.get("HOST")
    if "start_with_windows" in values and host is not None and hasattr(host, "set_start_with_windows"):
        host.set_start_with_windows(bool(values["start_with_windows"]))   # R-173: keeps the Startup shortcut in step
    return jsonify(ok=True)


@bp.get("/system")
def system_info():
    """Where Reframe keeps its state: everything is under the data folder or the backup folder."""
    from ..db.connection import data_dir
    conn = db()
    dd = data_dir()
    return jsonify(version=APP_VERSION, data_dir=str(dd), db_path=str(current_app.config["DB_PATH"]),
                   lock=str(dd / "reframe.lock"), backups=str(_backup_folder(conn)),
                   vault=str(settingsmod.get(conn, "vault_dir") or dd / "vault"), cache=str(dd / "cache"),
                   logs=str(dd / "logs"), tmp=str(dd / "tmp"),
                   schema_version=conn.execute("PRAGMA user_version").fetchone()[0])


@bp.post("/quit")
def quit_app():
    """Quit Reframe (View menu, Ctrl+Q): the host runs the exit sequence (spec 10.2)."""
    host = current_app.config.get("HOST")
    if host is None or not hasattr(host, "quit"):
        return err("Quit is only available in the app window", 501, "no_host")
    host.quit()
    return jsonify(ok=True)


# ------------------------------------------------------------------- sync

@bp.post("/sync")
def start_sync():
    kinds = tuple(k for k in (body().get("kinds") or ["outlook", "ics"]) if k in ("outlook", "ics"))
    started = current_app.config["SYNC"].start(kinds or ("outlook", "ics"))
    return jsonify(started=started, **current_app.config["SYNC"].status()), 202


@bp.get("/sync/status")
def sync_status():
    conn = db()
    last = conn.execute("SELECT * FROM sync_run ORDER BY id DESC LIMIT 1").fetchone()
    return jsonify(**current_app.config["SYNC"].status(), last_run=dict(last) if last else None,
                   last_sync_at=settingsmod.get(conn, "last_sync_at"))


# ------------------------------------------------------------------- backup

@bp.post("/backup")
def make_backup():
    conn = db()
    folder = _backup_folder(conn)
    try:
        path = backupmod.create_backup(conn, folder)
        removed = backupmod.apply_retention(
            folder, keep_last=settingsmod.get(conn, "backup_keep_last"),
            keep_days=settingsmod.get(conn, "backup_keep_days"),
            keep_months=settingsmod.get(conn, "backup_keep_months"))
    except OSError as e:
        return err(f"Backup failed: {e}. Check the backup folder in Settings.", 500, "backup")
    return jsonify(path=str(path), removed=len(removed))


@bp.get("/backups")
def list_backups():
    folder = _backup_folder(db())
    return jsonify(folder=str(folder), backups=[
        {"name": p.name, "size": backupmod.backup_size(p)} for p in backupmod.list_backups(folder)])


@bp.post("/backups/restore")
def restore():
    name = body().get("name", "")
    conn = db()
    folder = _backup_folder(conn)
    target = folder / Path(name).name          # no path traversal: file name only
    if target not in backupmod.list_backups(folder):
        return err("Backup not found", 404, "not_found")
    safety = backupmod.restore_backup(Path(current_app.config["DB_PATH"]), target, folder)
    return jsonify(ok=True, safety_backup=str(safety))


# ---------------------------------------------------------------- host bridge

def _host():
    host = current_app.config.get("HOST")
    if host is None:
        return None
    return host


@bp.post("/host/choose-folder")
def choose_folder():
    host = _host()
    if host is None:
        return err("Folder chooser is only available in the app window", 501, "no_host")
    return jsonify(path=host.choose_folder())


@bp.post("/host/choose-file")
def choose_file():
    host = _host()
    if host is None:
        return err("File chooser is only available in the app window", 501, "no_host")
    return jsonify(path=host.choose_file())


@bp.post("/host/choose-save")
def choose_save():
    host = _host()
    if host is None or not hasattr(host, "choose_save"):
        return err("The save chooser is only available in the app window", 501, "no_host")
    return jsonify(path=host.choose_save(body().get("name") or ""))


@bp.post("/host/reveal-db")
def reveal_db():
    host = _host()
    if host is None:
        return err("Only available in the app window", 501, "no_host")
    host.reveal(str(current_app.config["DB_PATH"]))
    return jsonify(ok=True)


@bp.post("/host/geometry")
def save_geometry():
    settingsmod.set_many(db(), {"window_geometry": body().get("geometry")})
    return jsonify(ok=True)


# ---------------------------------------------------------------------- SSE

@bp.get("/events")
def events():
    hub = current_app.config["HUB"]
    resp = Response(hub.stream(), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp
