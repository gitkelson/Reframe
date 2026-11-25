"""Item and attachment endpoints."""
from __future__ import annotations

import concurrent.futures
import os
import webbrowser
from urllib.parse import urlparse

from flask import Blueprint, current_app, jsonify, request

from ..calendars import sources as sourcemod
from ..domain import history as historymod
from ..domain import items as itemmod
from ..domain import tags as tagmod
from ..files.service import OpenRefused
from .common import body, db, err, user_write

bp = Blueprint("items", __name__, url_prefix="/api")

_FIELDS = ("kind", "title", "start_date", "end_date", "notes")


def _short(title: str) -> str:
    return title if len(title) <= 40 else title[:39] + "…"


def _titles(conn, ids) -> str:
    ids = list(ids)
    if len(ids) == 1:
        r = conn.execute("SELECT title FROM item WHERE id=?", (ids[0],)).fetchone()
        return f"'{_short(r[0])}'" if r else "item"
    return f"{len(ids)} items"


@bp.get("/items")
def list_items():
    conn = db()
    rows = itemmod.fetch_items(conn, request.args.get("from"), request.args.get("to"))
    return jsonify(items=rows, calendars=_calendar_sources(conn),
                   tags={t["id"]: {"name": t["name"], "slot": t["slot"], "usage": t["usage"],
                                   "parent_id": t["parent_id"], "path": t["path"], "tree_order": t["tree_order"],
                                   "depth": t["depth"], "retired": t["retired"], "has_children": t["has_children"]}
                         for t in tagmod.list_tags(conn)})


def _calendar_sources(conn) -> dict:
    """calendar_source id -> {name, location, kind, visible, connected, is_holiday} (R-130, R-131)."""
    return {s["id"]: {k: s[k] for k in ("name", "location", "kind", "visible", "connected", "is_holiday", "item_count")}
            for s in sourcemod.list_sources(conn)}


@bp.get("/items/<item_id>")
def get_item(item_id):
    return jsonify(itemmod.get_item(db(), item_id))


@bp.get("/items/<item_id>/history")
def item_history(item_id):
    return jsonify(historymod.item_history(db(), item_id))


@bp.post("/items")
def create_item():
    b = body()
    conn = db()
    start = b.get("start_date") or b.get("start")
    with user_write(f"Add '{_short((b.get('title') or '').strip())}'"):
        item_id = itemmod.create_item(conn, title=b.get("title", ""), start=start,
                                      end=b.get("end_date") or b.get("end"), kind=b.get("kind", "plan"),
                                      notes=b.get("notes", ""), tags=b.get("tags") or [])
    return jsonify(id=item_id, item=itemmod.get_item(conn, item_id)), 201


@bp.patch("/items/<item_id>")
def patch_item(item_id):
    b = body()
    conn = db()
    fields = {k: b[k] for k in _FIELDS if k in b}
    label = f"Edit {_titles(conn, [item_id])}"
    if set(fields) <= {"start_date", "end_date"} and fields:
        label = f"Change dates of {_titles(conn, [item_id])}"
    with user_write(label):
        changed = itemmod.update_item(conn, item_id, **fields)
        if "tags" in b:
            itemmod.set_item_tags(conn, item_id, b["tags"])
            changed.append("tags")
    return jsonify(changed=changed, item=itemmod.get_item(conn, item_id))


@bp.post("/items/<item_id>/duplicate")
def duplicate_item(item_id):
    b = body()
    conn = db()
    src = conn.execute("SELECT title FROM item WHERE id=?", (item_id,)).fetchone()
    if src is None:
        return err("Source item not found", 404, "not_found")
    with user_write(f"Duplicate '{_short(src['title'])}'"):
        new_id = itemmod.duplicate_item(conn, item_id, b.get("start_date") or b.get("start") or "")
    return jsonify(id=new_id, item=itemmod.get_item(conn, new_id)), 201


@bp.delete("/items/<item_id>")
def delete_item(item_id):
    conn = db()
    with user_write(f"Delete {_titles(conn, [item_id])}"):
        res = itemmod.delete_items(conn, [item_id])
    return jsonify(res)


@bp.post("/items/<item_id>/restore")
def restore_item(item_id):
    conn = db()
    with user_write(f"Restore {_titles(conn, [item_id])}"):
        itemmod.restore_item(conn, item_id)
    return jsonify(ok=True)


@bp.post("/items/<item_id>/hide")
def hide_item(item_id):
    conn = db()
    series = bool(body().get("series"))
    with user_write(("Hide series of " if series else "Hide ") + _titles(conn, [item_id])):
        n = itemmod.hide_item(conn, item_id, series=series)
    return jsonify(hidden=n)


@bp.post("/items/<item_id>/unhide")
def unhide_item(item_id):
    conn = db()
    series = bool(body().get("series"))
    with user_write(("Unhide series of " if series else "Unhide ") + _titles(conn, [item_id])):
        n = itemmod.unhide_item(conn, item_id, series=series)
    return jsonify(unhidden=n)


@bp.put("/items/<item_id>/tags")
def put_tags(item_id):
    conn = db()
    with user_write(f"Set tags on {_titles(conn, [item_id])}"):
        res = itemmod.set_item_tags(conn, item_id, body().get("tags") or [])
    return jsonify(res)


@bp.post("/items/bulk")
def bulk():
    """Each bulk action is one change set and one undo (R-101)."""
    b = body()
    ids = b.get("ids") or []
    action = b.get("action")
    if not ids:
        return err("No items selected")
    conn = db()
    n = len(ids)
    what = f"{n} item{'s' if n != 1 else ''}"
    if action == "move":
        days = int(b.get("days", 0))
        with user_write(f"Move {what} by {days:+d} day{'s' if abs(days) != 1 else ''}"):
            res = itemmod.move_items(conn, ids, days)
    elif action in ("shift_start", "shift_end"):
        days = int(b.get("days", 0))
        which = action.split("_")[1]
        with user_write(f"Move {which} date of {what} by {days:+d} day{'s' if abs(days) != 1 else ''}"):
            res = itemmod.shift_edge(conn, ids, which, days)
    elif action == "copy_tags":
        source = b.get("source_id") or ""
        src = conn.execute("SELECT title FROM item WHERE id=?", (source,)).fetchone()
        if src is None:
            return err("Source item not found", 404, "not_found")
        with user_write(f"Copy tags from '{_short(src['title'])}' to {_titles(conn, ids)}"):
            res = itemmod.copy_tags(conn, source, ids)
    elif action == "delete":
        with user_write(f"Delete {what}"):
            res = itemmod.delete_items(conn, ids)
    elif action == "add_tags":
        with user_write(f"Add tag(s) to {what}"):
            res = {"changed": itemmod.edit_tags_bulk(conn, ids, add=b.get("tags") or [])}
    elif action == "add_tag":
        name = (b.get("tag") or "").strip()
        if not name:
            return err("A tag name is required")
        with user_write(f"Add tag '{name}' to {_titles(conn, ids)}"):
            res = itemmod.add_tag(conn, ids, name)
    elif action == "remove_tags":
        with user_write(f"Remove tag(s) from {what}"):
            res = {"changed": itemmod.edit_tags_bulk(conn, ids, remove=b.get("tags") or [])}
    elif action == "set_kind":
        with user_write(f"Set kind of {what}"):
            res = {"changed": itemmod.set_kind(conn, ids, b.get("kind"))}
    else:
        return err(f"Unknown bulk action: {action}")
    return jsonify(res)


# ------------------------------------------------------------- attachments

CAPTURE_INLINE_MAX = 256 * 1024 * 1024   # larger files are captured in the background


def _capture_first(target: str) -> str | None:
    """Capture a file before the link is written so link and pin are one change set and one undo (R-160).

    Runs before the writer transaction (no database transaction is held during file I/O, spec 5.4). Returns the
    version id, or None when the file cannot be captured now (the vault service retries in the background).
    """
    svc = current_app.config.get("VAULT")
    t = (target or "").strip().strip('"')
    if svc is None or itemmod.detect_attachment_kind(t) != "file":
        return None
    try:
        if not os.path.isfile(t) or os.path.getsize(t) > CAPTURE_INLINE_MAX:
            return None
        from ..vault import capture
        res = capture.capture_file(db(), svc.store, t)
        return res.version_id if res.ok else None
    except Exception:
        return None


@bp.post("/items/<item_id>/attachments")
def add_attachment(item_id):
    b = body()
    conn = db()
    target = b.get("target", "")
    if "file_entry_id" in b:
        file_id = b["file_entry_id"]
        if target or not isinstance(file_id, str) or not file_id:
            return err("Provide one valid file entry id, without a target.", 400, "invalid")
        files = current_app.config.get("FILES")
        if files is None:
            return err("The file browser is unavailable.", 503, "unavailable")
        try:
            target = files.attachment_path(file_id)
        except OpenRefused as exc:
            return err(str(exc), exc.status, "refused")
    version = _capture_first(target)
    with user_write(f"Add link to {_titles(conn, [item_id])}"):
        aid = itemmod.add_attachment(conn, item_id, target, b.get("label"))
        if version:
            conn.execute("UPDATE attachment SET pinned_version_id=?, pin_basis='attach' WHERE id=?", (version, aid))
    svc = current_app.config.get("VAULT")
    if svc is not None and not version:
        try:
            svc.notify_attachment_added(aid)
        except Exception:
            pass
    return jsonify(id=aid, pinned=bool(version)), 201


@bp.delete("/attachments/<att_id>")
def remove_attachment(att_id):
    conn = db()
    with user_write("Remove link"):
        itemmod.remove_attachment(conn, att_id)
    return jsonify(ok=True)


_OPEN_SCHEMES = ("http", "https", "mailto")


@bp.post("/attachments/<att_id>/open")
def open_attachment(att_id):
    r = db().execute("SELECT kind, target FROM attachment WHERE id=?", (att_id,)).fetchone()
    if r is None:
        return err("No such link", 404, "not_found")
    target = r["target"]
    if r["kind"] == "url":
        if target.lower().startswith("mailto:"):
            scheme = "mailto"
        elif "://" in target:
            scheme = urlparse(target).scheme.lower()
        else:
            return err("That link is not a web address", 400)   # never hand a bare path to the OS
        if scheme not in _OPEN_SCHEMES:
            return err(f"Links of type '{scheme}' are not opened from Reframe", 400)
        webbrowser.open(target)
    else:
        if not os.path.exists(target):
            return err("That file or folder is not reachable", 404, "broken")
        os.startfile(target)  # type: ignore[attr-defined]  # default application (Windows)
    return jsonify(ok=True)


_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="linkcheck")


def _check_file(path: str) -> bool:
    return os.path.exists(path)


@bp.post("/attachments/check")
def check_attachments():
    """Broken-link check: 2 s timeout per file link, never blocks the UI (R-71).

    URLs are reported as 'unchecked': the app makes no network calls.
    """
    conn = db()
    ids = body().get("ids")
    if ids:
        marks = ",".join("?" for _ in ids)
        rows = conn.execute(f"SELECT id, kind, target FROM attachment WHERE id IN ({marks})", ids).fetchall()
    else:
        rows = conn.execute("SELECT id, kind, target FROM attachment").fetchall()
    futures = {r["id"]: _pool.submit(_check_file, r["target"]) for r in rows if r["kind"] == "file"}
    out = {}
    for r in rows:
        if r["kind"] != "file":
            out[r["id"]] = "unchecked"
            continue
        try:
            out[r["id"]] = "ok" if futures[r["id"]].result(timeout=2) else "broken"
        except concurrent.futures.TimeoutError:
            out[r["id"]] = "timeout"
        except OSError:
            out[r["id"]] = "broken"
    return jsonify(out)
