"""Undo, redo and revert on top of the change log (spec 5.3, R-81, R-82)."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .build_schema import GENERATED, TABLES
from .changelog import group_external, writer


class NothingToDo(Exception):
    pass


class RevertConflict(Exception):
    def __init__(self, conflicts: list[dict[str, Any]]):
        self.conflicts = conflicts
        super().__init__("rows changed since: " + ", ".join(f"{c['table']} {c['row_key']}" for c in conflicts))


def _json_expr(table: str) -> str:
    cols = TABLES[table][0]
    return "json_object(" + ", ".join(f"'{c}', {c}" for c in cols) + ")"


def _pk_from_row(table: str, row: dict[str, Any]) -> list[Any]:
    return [row[c] for c in TABLES[table][1]]


def current_row(conn: sqlite3.Connection, table: str, pk_values: list[Any]) -> dict[str, Any] | None:
    where = " AND ".join(f"{c} = ?" for c in TABLES[table][1])
    r = conn.execute(f"SELECT {_json_expr(table)} AS j FROM {table} WHERE {where}", pk_values).fetchone()
    return json.loads(r["j"]) if r else None


def set_states(conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    """For every change set: whether it is currently in effect.

    A non-undo set is reverted when an (in-effect) undo set targets it. An undo
    set that is itself reverted (a redo) brings its target back into effect.
    """
    rows = conn.execute(
        "SELECT id, actor, label, reverts_change_set_id AS rev FROM change_set ORDER BY id").fetchall()
    state: dict[int, dict[str, Any]] = {}
    for r in rows:
        state[r["id"]] = {"id": r["id"], "actor": r["actor"], "label": r["label"],
                          "reverts": r["rev"], "in_effect": True}
        if r["actor"] == "undo" and r["rev"] in state:
            target = state[r["rev"]]
            target["in_effect"] = False
            if target["actor"] == "undo" and target["reverts"] in state:
                state[target["reverts"]]["in_effect"] = True
    return state


def _nonempty_ids(conn: sqlite3.Connection) -> set[int]:
    return {r[0] for r in conn.execute("SELECT DISTINCT change_set_id FROM change WHERE change_set_id IS NOT NULL")}


def undo_target(conn: sqlite3.Connection) -> int | None:
    state = set_states(conn)
    nonempty = _nonempty_ids(conn)
    for cs_id in sorted(state, reverse=True):
        s = state[cs_id]
        if s["actor"] != "undo" and s["in_effect"] and cs_id in nonempty:
            return cs_id
    return None


def redo_target(conn: sqlite3.Connection) -> int | None:
    state = set_states(conn)
    last_non_undo = max((i for i, s in state.items() if s["actor"] != "undo"), default=0)
    for cs_id in sorted(state, reverse=True):
        if cs_id <= last_non_undo:
            break
        s = state[cs_id]
        # A set that reverts another undo set is itself a redo, never a redo candidate
        if s["actor"] == "undo" and s["in_effect"] and state.get(s["reverts"], {}).get("actor") != "undo":
            return cs_id
    return None


def _changes(conn: sqlite3.Connection, cs_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM change WHERE change_set_id=? ORDER BY id", (cs_id,)).fetchall()


def find_conflicts(conn: sqlite3.Connection, cs_id: int) -> list[dict[str, Any]]:
    """Rows whose current state differs from what the change set left behind."""
    final: dict[tuple[str, str], sqlite3.Row] = {}
    for ch in _changes(conn, cs_id):
        final[(ch["table_name"], ch["row_key"])] = ch
    conflicts = []
    for (table, key), ch in final.items():
        if table not in TABLES:
            continue
        if ch["op"] == "delete":
            before = json.loads(ch["before_json"])
            cur = current_row(conn, table, _pk_from_row(table, before))
            expected = None
        else:
            after = json.loads(ch["after_json"])
            cur = current_row(conn, table, _pk_from_row(table, after))
            expected = after
        if cur is not None and expected is not None:
            # snapshots written before schema version 2 lack its columns: compare what they recorded
            cur = {k: v for k, v in cur.items() if k in expected}
        if cur != expected:
            conflicts.append({"table": table, "row_key": key, "expected": expected, "current": cur})
    return conflicts


def _apply_inverse(conn: sqlite3.Connection, ch: sqlite3.Row) -> None:
    """Inverse of one logged change. Columns missing from an old snapshot are left unchanged (spec 13.4)."""
    table = ch["table_name"]
    cols, pk = TABLES[table]
    writable = [c for c in cols if c not in GENERATED.get(table, [])]
    if ch["op"] == "insert":
        after = json.loads(ch["after_json"])
        where = " AND ".join(f"{c} = ?" for c in pk)
        conn.execute(f"DELETE FROM {table} WHERE {where}", _pk_from_row(table, after))
    elif ch["op"] == "delete":
        before = json.loads(ch["before_json"])
        if table == "item" and "source_id" not in before and before.get("origin") == "outlook":
            # a version-1 snapshot: the calendar source is found the way migration 002 assigns it
            r = conn.execute("SELECT id FROM calendar_source WHERE kind='outlook' AND external_id=?",
                             (before.get("outlook_calendar"),)).fetchone()
            before["source_id"] = r[0] if r else None
        present = [c for c in writable if c in before]
        names = ", ".join(present)
        marks = ", ".join("?" for _ in present)
        conn.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})", [before[c] for c in present])
    else:
        before = json.loads(ch["before_json"])
        after = json.loads(ch["after_json"])
        present = [c for c in writable if c in before]
        sets = ", ".join(f"{c} = ?" for c in present)
        where = " AND ".join(f"{c} = ?" for c in pk)
        conn.execute(f"UPDATE {table} SET {sets} WHERE {where}",
                     [before[c] for c in present] + _pk_from_row(table, after))


def revert_change_set(conn: sqlite3.Connection, cs_id: int, *, label: str | None = None) -> dict[str, Any]:
    """Apply the inverse of a change set as a new 'undo' set. Refuses on conflict."""
    group_external(conn)
    cs = conn.execute("SELECT * FROM change_set WHERE id=?", (cs_id,)).fetchone()
    if cs is None:
        raise NothingToDo(f"no change set {cs_id}")
    if not _changes(conn, cs_id):
        raise NothingToDo("change set has no changes")
    conflicts = find_conflicts(conn, cs_id)
    if conflicts:
        raise RevertConflict(conflicts)
    lbl = label or (("Redo: " if cs["actor"] == "undo" else "Undo: ") + cs["label"])
    with writer(conn, "undo", lbl, reverts=cs_id) as w:
        conn.execute("PRAGMA defer_foreign_keys=ON")
        for ch in reversed(_changes(conn, cs_id)):
            _apply_inverse(conn, ch)
        undo_id = w.change_set_id
    return {"change_set_id": undo_id, "reverted": cs_id, "label": lbl, "undone_label": cs["label"]}


def undo(conn: sqlite3.Connection) -> dict[str, Any]:
    group_external(conn)
    target = undo_target(conn)
    if target is None:
        raise NothingToDo("Nothing to undo")
    return revert_change_set(conn, target)


def redo(conn: sqlite3.Connection) -> dict[str, Any]:
    group_external(conn)
    target = redo_target(conn)
    if target is None:
        raise NothingToDo("Nothing to redo")
    return revert_change_set(conn, target)
