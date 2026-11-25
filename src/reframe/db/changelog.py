"""Change-log writer protocol (spec 5.3) and external-change grouping."""
from __future__ import annotations

import contextlib
import sqlite3
import time
from typing import Iterator

ACTORS = ("user", "agent", "sync", "undo", "external")


class EmptyWrite(Exception):
    """Raised internally to roll back a writer that changed nothing."""


class Session:
    """Handle yielded by ``writer``; exposes the open change set id."""

    def __init__(self, conn: sqlite3.Connection, change_set_id: int, actor: str):
        self.conn = conn
        self.change_set_id = change_set_id
        self.actor = actor

    def set_label(self, label: str) -> None:
        """Rename the open change set (allowed by trigger only while it is open)."""
        self.conn.execute("UPDATE change_set SET label=? WHERE id=?", (label, self.change_set_id))


def _begin_immediate(conn: sqlite3.Connection, attempts: int = 4) -> None:
    delay = 0.05
    for i in range(attempts):
        try:
            conn.execute("BEGIN IMMEDIATE")
            return
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if ("locked" in msg or "busy" in msg) and i < attempts - 1:
                time.sleep(delay)
                delay *= 3
                continue
            raise


@contextlib.contextmanager
def writer(conn: sqlite3.Connection, actor: str, label: str, *,
           reverts: int | None = None, keep_empty: bool = False) -> Iterator[Session]:
    """One writer transaction following the protocol in spec 5.3.

    BEGIN IMMEDIATE, open a change set, point ``app_context`` at it, yield for
    the data writes, reset ``app_context`` to ('external', NULL), COMMIT. If the
    block made no logged change the transaction is rolled back so no empty
    change set is left behind (unless ``keep_empty``).
    """
    if actor not in ACTORS:
        raise ValueError(f"unknown actor {actor!r}")
    if conn.in_transaction:
        raise RuntimeError("writer() cannot be nested; run inner operations in the open session")
    _begin_immediate(conn)
    try:
        cur = conn.execute(
            "INSERT INTO change_set (actor, label, reverts_change_set_id) VALUES (?,?,?)",
            (actor, label, reverts))
        cs_id = cur.lastrowid
        conn.execute("UPDATE app_context SET actor=?, change_set_id=? WHERE id=1", (actor, cs_id))
        sess = Session(conn, cs_id, actor)
        yield sess
        n = conn.execute("SELECT count(*) FROM change WHERE change_set_id=?", (cs_id,)).fetchone()[0]
        conn.execute("UPDATE app_context SET actor='external', change_set_id=NULL WHERE id=1")
        if n == 0 and not keep_empty:
            conn.execute("ROLLBACK")
            sess.change_set_id = 0
        else:
            conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def reset_stale_context(conn: sqlite3.Connection) -> bool:
    """Reset a leftover app_context found outside a transaction (spec 5.3)."""
    row = conn.execute("SELECT actor, change_set_id FROM app_context WHERE id=1").fetchone()
    if row["actor"] == "external" and row["change_set_id"] is None:
        return False
    _begin_immediate(conn)
    try:
        conn.execute("UPDATE app_context SET actor='external', change_set_id=NULL WHERE id=1")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return True


def group_external(conn: sqlite3.Connection) -> int | None:
    """Group change rows written without the protocol into one 'external' set.

    Returns the new change set id, or None if there was nothing to group.
    """
    if conn.execute("SELECT 1 FROM change WHERE change_set_id IS NULL LIMIT 1").fetchone() is None:
        return None
    _begin_immediate(conn)
    try:
        rows = conn.execute(
            "SELECT table_name, count(*) n FROM change WHERE change_set_id IS NULL GROUP BY table_name"
        ).fetchall()
        total = sum(r["n"] for r in rows)
        if total == 0:
            conn.execute("ROLLBACK")
            return None
        parts = ", ".join(f"{r['n']} {r['table_name']}" for r in rows)
        cs = conn.execute(
            "INSERT INTO change_set (actor, label) VALUES ('external', ?)",
            (f"External change ({parts})",)).lastrowid
        conn.execute("UPDATE change SET change_set_id=? WHERE change_set_id IS NULL", (cs,))
        conn.execute("COMMIT")
        return cs
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
