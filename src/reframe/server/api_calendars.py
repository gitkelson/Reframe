"""Calendar source endpoints (R-130 .. R-135): list, connect/disconnect, visibility, ICS sources."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify

from ..calendars import ics
from ..calendars import sources as sourcemod
from ..outlook.adapter import OutlookUnavailable
from .common import body, db, err, hourly_backup

bp = Blueprint("calendars", __name__, url_prefix="/api")


def _payload(conn, **extra):
    return jsonify(sources=sourcemod.list_sources(conn), summary=sourcemod.visibility_summary(conn), **extra)


@bp.get("/calendars")
def list_calendars():
    return _payload(db())


@bp.post("/calendars/refresh-outlook")
def refresh_outlook():
    """Every calendar folder Outlook exposes; new ones are connected but hidden (R-61, R-131)."""
    conn = db()
    try:
        with current_app.config["ADAPTER_FACTORY"](conn) as adapter:
            found = adapter.list_calendars()
    except OutlookUnavailable as e:
        return _payload(conn, available=False, message=str(e))
    new = sourcemod.upsert_outlook_folders(conn, found)
    empty = [c.name for c in found if c.item_count == 0]
    return _payload(conn, available=True, new=len(new), empty=empty)


@bp.post("/calendars/ics")
def add_ics():
    b = body()
    location = (b.get("location") or "").strip()
    try:
        kind, normalized = ics.normalize_location(location)
    except ValueError as e:
        return err(str(e))
    if kind == "url" and ics.is_plain_http(normalized) and not b.get("allow_http"):
        return err("This feed uses plain http://, so its contents travel unencrypted. Add it anyway?", 409,
                   "http_warning")
    src = sourcemod.add_ics(db(), location, name=b.get("name"), is_holiday=bool(b.get("is_holiday")))
    current_app.config["SYNC"].start(("ics",), ics_sources=[src["id"]])
    return jsonify(source=src), 201


@bp.patch("/calendars/<source_id>")
def patch_source(source_id):
    b = body()
    fields = {k: b[k] for k in ("name", "is_holiday", "visible", "refresh_min") if k in b}
    sourcemod.update(db(), source_id, **fields)
    return _payload(db())


@bp.post("/calendars/visibility")
def all_visibility():
    sourcemod.set_all_visible(db(), bool(body().get("visible")))
    return _payload(db())


@bp.post("/calendars/<source_id>/connect")
def connect_source(source_id):
    """Disconnect soft-deletes the source's items in one undoable change set (R-132); reconnect re-imports."""
    conn = db()
    connected = bool(body().get("connected"))
    src = sourcemod.get(conn, source_id)
    res = sourcemod.set_connected(conn, source_id, connected)
    if connected:
        current_app.config["SYNC"].start((src["kind"],), ics_sources=[source_id] if src["kind"] == "ics" else None)
    else:
        hourly_backup()
    return _payload(conn, **res)


@bp.delete("/calendars/<source_id>")
def delete_source(source_id):
    sourcemod.remove(db(), source_id)
    return _payload(db())
