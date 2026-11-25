import sqlite3

import pytest

from reframe.db import undo as undomod
from reframe.db.changelog import group_external, reset_stale_context, writer
from reframe.domain import items


def rows(conn, sql, *p):
    return [dict(r) for r in conn.execute(sql, p)]


def test_writer_protocol_logs_set_and_resets_context(conn):
    with writer(conn, "agent", "Add stuff") as s:
        items.create_item(conn, title="A", start="2026-12-01")
        assert conn.execute("SELECT actor, change_set_id FROM app_context").fetchone()[:] == ("agent", s.change_set_id)
    ctx = conn.execute("SELECT actor, change_set_id FROM app_context").fetchone()
    assert tuple(ctx) == ("external", None)
    cs = rows(conn, "SELECT * FROM change_set")
    assert [(c["actor"], c["label"]) for c in cs] == [("agent", "Add stuff")]
    ch = rows(conn, "SELECT * FROM change")
    assert len(ch) == 1 and ch[0]["change_set_id"] == cs[0]["id"] and ch[0]["op"] == "insert"


def test_empty_writer_leaves_nothing(conn):
    with writer(conn, "user", "noop"):
        pass
    assert conn.execute("SELECT count(*) FROM change_set").fetchone()[0] == 0


def test_writer_rolls_back_on_error_and_resets_context(conn):
    with pytest.raises(RuntimeError):
        with writer(conn, "user", "boom"):
            items.create_item(conn, title="A", start="2026-12-01")
            raise RuntimeError
    assert conn.execute("SELECT count(*) FROM item").fetchone()[0] == 0
    assert tuple(conn.execute("SELECT actor, change_set_id FROM app_context").fetchone()) == ("external", None)


def test_no_duplicate_log_rows_for_update(conn, w):
    iid = w(lambda: items.create_item(conn, title="A", start="2026-12-01"))
    w(lambda: items.update_item(conn, iid, title="B"))
    ups = rows(conn, "SELECT * FROM change WHERE op='update'")
    assert len(ups) == 1  # no updated_at trigger, so exactly one row


def test_direct_sql_without_protocol_is_external(conn):
    conn.execute("INSERT INTO item (id,title,start_date,end_date) VALUES ('x','Ext','2026-12-01','2026-12-01')")
    conn.execute("UPDATE item SET title='Ext2' WHERE id='x'")
    assert conn.execute("SELECT count(*) FROM change WHERE change_set_id IS NULL").fetchone()[0] == 2
    cs = group_external(conn)
    assert cs is not None
    row = rows(conn, "SELECT * FROM change_set WHERE id=?", cs)[0]
    assert row["actor"] == "external"
    assert conn.execute("SELECT count(*) FROM change WHERE change_set_id IS NULL").fetchone()[0] == 0
    assert group_external(conn) is None


def test_external_change_is_undoable(conn):
    conn.execute("INSERT INTO item (id,title,start_date,end_date) VALUES ('x','Ext','2026-12-01','2026-12-01')")
    res = undomod.undo(conn)
    assert "External" in res["undone_label"]
    assert conn.execute("SELECT count(*) FROM item").fetchone()[0] == 0  # undo may hard delete


def test_leftover_app_context_not_credited_to_next_writer(conn):
    conn.execute("UPDATE app_context SET actor='agent', change_set_id=NULL")
    assert reset_stale_context(conn) is True
    conn.execute("INSERT INTO item (id,title,start_date,end_date) VALUES ('x','Ext','2026-12-01','2026-12-01')")
    assert conn.execute("SELECT count(*) FROM change WHERE change_set_id IS NULL").fetchone()[0] == 1
    assert reset_stale_context(conn) is False


def test_before_triggers_protect_log(conn, w):
    w(lambda: items.create_item(conn, title="A", start="2026-12-01"))
    for sql in ("UPDATE change SET op='delete'", "DELETE FROM change", "DELETE FROM change_set",
                "UPDATE change_set SET label='x'", "UPDATE change SET change_set_id=1 WHERE change_set_id IS NOT NULL"):
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(sql)


def test_label_can_change_only_while_open(conn):
    with writer(conn, "user", "first") as s:
        items.create_item(conn, title="A", start="2026-12-01")
        s.set_label("renamed")
    assert conn.execute("SELECT label FROM change_set").fetchone()[0] == "renamed"
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE change_set SET label='again'")


def test_hard_delete_item_blocked_unless_undo(conn, w):
    iid = w(lambda: items.create_item(conn, title="A", start="2026-12-01"))
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM item WHERE id=?", (iid,))
    with pytest.raises(sqlite3.DatabaseError):
        with writer(conn, "agent", "hard delete"):
            conn.execute("DELETE FROM item WHERE id=?", (iid,))
    assert conn.execute("SELECT count(*) FROM item").fetchone()[0] == 1


def _outlook_item(conn):
    with writer(conn, "sync", "import"):
        conn.execute("INSERT INTO item (id,title,start_date,end_date,origin, source_id,outlook_uid) "
                     "VALUES ('o1','Meeting','2026-12-01','2026-12-01','outlook', 'testcal','u1')")
    return "o1"


def test_outlook_core_fields_locked_for_user_and_agent_and_external(conn):
    _outlook_item(conn)
    for actor in ("user", "agent"):
        with pytest.raises(sqlite3.DatabaseError):
            with writer(conn, actor, "edit"):
                conn.execute("UPDATE item SET title='x' WHERE id='o1'")
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE item SET start_date='2026-12-02', end_date='2026-12-02' WHERE id='o1'")
    # notes are not core and stay editable
    with writer(conn, "user", "note"):
        conn.execute("UPDATE item SET notes='hello' WHERE id='o1'")
    with writer(conn, "sync", "sync"):
        conn.execute("UPDATE item SET title='Renamed' WHERE id='o1'")
    assert conn.execute("SELECT title, notes FROM item").fetchone()[:] == ("Renamed", "hello")


def test_tag_and_item_tag_logged_with_composite_row_key(conn, w):
    iid = w(lambda: items.create_item(conn, title="A", start="2026-12-01", tags=["Spares"]))
    keys = [r["row_key"] for r in rows(conn, "SELECT row_key FROM change WHERE table_name='item_tag'")]
    tag_id = conn.execute("SELECT id FROM tag").fetchone()[0]
    assert keys == [f"{iid}|{tag_id}"]
