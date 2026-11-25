"""Tag endpoints (R-40..R-47)."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..domain import tags as tagmod
from .common import body, db, err, user_write

bp = Blueprint("tags", __name__, url_prefix="/api")


@bp.get("/tags")
def list_tags():
    return jsonify(tags=tagmod.list_tags(db()))


@bp.get("/tags/suggest")
def suggest():
    return jsonify(tagmod.suggestions(db(), request.args.get("q", "")))


@bp.get("/tags/hygiene")
def hygiene():
    conn = db()
    drift = tagmod.implied_drift(conn)
    return jsonify(pairs=tagmod.hygiene(conn), drift={k: len(v) for k, v in drift.items()})


@bp.post("/tags/repair-implied")
def repair_implied():
    with user_write("Repair group tags"):
        n = tagmod.repair_implied(db())
    return jsonify(repaired=n)


@bp.post("/tags")
def create_tag():
    """Create a tag (optionally inside a group) from the tag manager (R-43)."""
    b = body()
    conn = db()
    parent = b.get("parent_id") or None
    with user_write(f"Create tag '{(b.get('name') or '').strip()}'"):
        tag_id, created = tagmod.get_or_create_tag(conn, b.get("name") or "", parent_id=parent)
    if not created:
        return err("A tag with that name already exists", 409, "exists", id=tag_id)
    return jsonify(id=tag_id), 201


def _name(conn, tag_id):
    row = conn.execute("SELECT name FROM tag WHERE id=?", (tag_id,)).fetchone()
    if row is None:
        raise LookupError("No such tag")
    return row["name"]


@bp.get("/tags/<tag_id>/move-preview")
def move_preview(tag_id):
    """Number of items a move would update, shown in the drag chip (R-112)."""
    return jsonify(items=tagmod.items_affected_by_move(db(), tag_id))


@bp.post("/tags/<tag_id>/move")
def move(tag_id):
    b = body()
    conn = db()
    parent = b.get("parent_id") or None
    label = f"Move tag '{_name(conn, tag_id)}' to " + (f"'{_name(conn, parent)}'" if parent else "the top level")
    with user_write(label):
        n = tagmod.move_tag(conn, tag_id, parent)
    return jsonify(items=n)


@bp.post("/tags/<tag_id>/retire")
def retire(tag_id):
    conn = db()
    retired = bool(body().get("retired", True))
    with user_write(("Retire" if retired else "Un-retire") + f" tag '{_name(conn, tag_id)}'"):
        tagmod.set_retired(conn, tag_id, retired)
    return jsonify(ok=True)


@bp.patch("/tags/<tag_id>")
def patch_tag(tag_id):
    b = body()
    conn = db()
    row = conn.execute("SELECT name FROM tag WHERE id=?", (tag_id,)).fetchone()
    if row is None:
        return err("No such tag", 404, "not_found")
    with user_write(f"Edit tag '{row['name']}'"):
        if "name" in b:
            tagmod.rename_tag(conn, tag_id, b["name"])
        if "color_slot" in b:
            tagmod.set_color(conn, tag_id, b["color_slot"])
    return jsonify(ok=True)


@bp.post("/tags/<tag_id>/merge")
def merge(tag_id):
    into = body().get("into")
    conn = db()
    a = conn.execute("SELECT name FROM tag WHERE id=?", (tag_id,)).fetchone()
    b = conn.execute("SELECT name FROM tag WHERE id=?", (into,)).fetchone()
    if a is None or b is None:
        return err("No such tag", 404, "not_found")
    with user_write(f"Merge tag '{a['name']}' into '{b['name']}'"):
        moved = tagmod.merge_tags(conn, tag_id, into)
    return jsonify(moved=moved)


@bp.delete("/tags/<tag_id>")
def delete(tag_id):
    conn = db()
    a = conn.execute("SELECT name FROM tag WHERE id=?", (tag_id,)).fetchone()
    if a is None:
        return err("No such tag", 404, "not_found")
    with user_write(f"Delete tag '{a['name']}'"):
        n = tagmod.delete_tag(conn, tag_id, force=request.args.get("force") == "1")
    return jsonify(removed_assignments=n)
