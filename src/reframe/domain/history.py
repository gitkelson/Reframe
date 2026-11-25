"""Read side of the change log for the History panel (R-82) and item history."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..db import undo as undomod
from ..db.changelog import group_external


def list_change_sets(conn: sqlite3.Connection, limit: int = 200, before_id: int | None = None) -> list[dict[str, Any]]:
    group_external(conn)
    states = undomod.set_states(conn)
    undo_t, redo_t = undomod.undo_target(conn), undomod.redo_target(conn)
    sql = ("SELECT cs.id, cs.ts, cs.actor, cs.label, cs.reverts_change_set_id AS reverts, "
           "(SELECT count(*) FROM change c WHERE c.change_set_id = cs.id) AS rows FROM change_set cs ")
    params: list[Any] = []
    if before_id:
        sql += "WHERE cs.id < ? "
        params.append(before_id)
    sql += "ORDER BY cs.id DESC LIMIT ?"
    params.append(limit)
    out = []
    for r in conn.execute(sql, params):
        d = dict(r)
        d["in_effect"] = states[d["id"]]["in_effect"]
        d["is_undo_target"] = d["id"] == undo_t
        d["is_redo_target"] = d["id"] == redo_t
        out.append(d)
    return out


def change_set_detail(conn: sqlite3.Connection, cs_id: int) -> dict[str, Any]:
    cs = conn.execute("SELECT * FROM change_set WHERE id=?", (cs_id,)).fetchone()
    if cs is None:
        raise LookupError(f"No change set {cs_id}")
    changes = []
    for c in conn.execute("SELECT * FROM change WHERE change_set_id=? ORDER BY id", (cs_id,)):
        changes.append({"id": c["id"], "table": c["table_name"], "row_key": c["row_key"], "op": c["op"],
                        "before": json.loads(c["before_json"]) if c["before_json"] else None,
                        "after": json.loads(c["after_json"]) if c["after_json"] else None})
    conflicts = undomod.find_conflicts(conn, cs_id) if changes else []
    return {**dict(cs), "changes": changes, "conflicts": conflicts}


def item_history(conn: sqlite3.Connection, item_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """Changes that touched an item, its tag assignments or its attachments."""
    group_external(conn)
    rows = conn.execute(
        """SELECT c.id, c.change_set_id, c.ts, c.table_name, c.op, c.before_json, c.after_json,
                  cs.actor, cs.label
             FROM change c LEFT JOIN change_set cs ON cs.id = c.change_set_id
            WHERE (c.table_name = 'item' AND c.row_key = ?)
               OR (c.table_name = 'item_tag' AND c.row_key LIKE ? || '|%')
               OR (c.table_name = 'attachment' AND json_extract(COALESCE(c.after_json, c.before_json), '$.item_id') = ?)
            ORDER BY c.id DESC LIMIT ?""", (item_id, item_id, item_id, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["before"] = json.loads(d.pop("before_json")) if r["before_json"] else None
        d["after"] = json.loads(d.pop("after_json")) if r["after_json"] else None
        out.append(d)
    return out


def hidden_and_removed(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    """Hidden items and soft-deleted items, which can be restored from History (R-66, R-83)."""
    hidden = [dict(r) for r in conn.execute(
        "SELECT id, title, start_date, end_date, hidden_at, outlook_series_id FROM item "
        "WHERE hidden_at IS NOT NULL AND deleted_at IS NULL ORDER BY start_date DESC LIMIT 500")]
    removed = [dict(r) for r in conn.execute(
        "SELECT id, title, start_date, end_date, deleted_at, origin FROM item "
        "WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC LIMIT 500")]
    return {"hidden": hidden, "removed": removed}
