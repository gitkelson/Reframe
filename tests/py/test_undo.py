import json

import pytest

from reframe.db import undo as undomod
from reframe.db.changelog import writer
from reframe.domain import history, items, tags


def titles(conn):
    return sorted(r[0] for r in conn.execute("SELECT title FROM item WHERE deleted_at IS NULL"))


def test_undo_redo_sequence_r81(conn, w):
    a = w(lambda: items.create_item(conn, title="A", start="2026-12-01"), "add A")
    b = w(lambda: items.create_item(conn, title="B", start="2026-12-02"), "add B")
    w(lambda: items.update_item(conn, a, title="A2"), "rename A")
    assert titles(conn) == ["A2", "B"]

    undomod.undo(conn)                       # undoes rename
    assert titles(conn) == ["A", "B"]
    undomod.undo(conn)                       # undoes add B
    assert titles(conn) == ["A"]
    undomod.redo(conn)                       # redo add B
    assert titles(conn) == ["A", "B"]
    undomod.redo(conn)                       # redo rename
    assert titles(conn) == ["A2", "B"]
    with pytest.raises(undomod.NothingToDo):
        undomod.redo(conn)

    undomod.undo(conn)
    w(lambda: items.update_item(conn, b, title="B2"), "rename B")  # new non-undo change kills redo
    with pytest.raises(undomod.NothingToDo):
        undomod.redo(conn)
    undomod.undo(conn)                       # undoes rename B, not the earlier undo
    assert titles(conn) == ["A", "B"]
    undomod.undo(conn)                       # next in-effect: add B
    assert titles(conn) == ["A"]
    undomod.undo(conn)                       # add A
    assert titles(conn) == []
    with pytest.raises(undomod.NothingToDo):
        undomod.undo(conn)


def test_undo_label_and_actor_and_history_append_only(conn, w):
    w(lambda: items.create_item(conn, title="A", start="2026-12-01"), "Add A")
    res = undomod.undo(conn)
    assert res["label"] == "Undo: Add A"
    cs = conn.execute("SELECT * FROM change_set WHERE id=?", (res["change_set_id"],)).fetchone()
    assert cs["actor"] == "undo" and cs["reverts_change_set_id"] == res["reverted"]
    assert conn.execute("SELECT count(*) FROM change_set").fetchone()[0] == 2


def test_undo_restores_tags_and_attachments_and_cascades(conn, w):
    iid = w(lambda: items.create_item(conn, title="A", start="2026-12-01", tags=["X", "Y"],
                                      attachments=[{"target": "https://e.com"}]), "add")
    w(lambda: items.delete_items(conn, [iid]), "delete")
    undomod.undo(conn)
    d = items.get_item(conn, iid)
    assert d["deleted_at"] is None
    assert [t["name"] for t in d["tags"]] == ["X", "Y"]
    assert len(d["attachments"]) == 1


def test_undo_tag_merge_is_one_step(conn, w):
    a = w(lambda: items.create_item(conn, title="A", start="2026-12-01", tags=["Spares"]))
    b = w(lambda: items.create_item(conn, title="B", start="2026-12-01", tags=["Spares Order"]))
    ids = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM tag")}
    w(lambda: tags.merge_tags(conn, ids["Spares"], ids["Spares Order"]), "merge")
    assert [t["name"] for t in items.get_item(conn, a)["tags"]] == ["Spares Order"]
    undomod.undo(conn)
    assert [t["name"] for t in items.get_item(conn, a)["tags"]] == ["Spares"]
    assert [t["name"] for t in items.get_item(conn, b)["tags"]] == ["Spares Order"]
    assert conn.execute("SELECT count(*) FROM tag").fetchone()[0] == 2


def test_undo_tag_rename(conn, w):
    iid = w(lambda: items.create_item(conn, title="A", start="2026-12-01", tags=["old"]))
    tid = conn.execute("SELECT id FROM tag").fetchone()[0]
    w(lambda: tags.rename_tag(conn, tid, "New Name"), "rename")
    undomod.undo(conn)
    assert conn.execute("SELECT name, norm_key FROM tag").fetchone()[:] == ("old", "old")


def test_direct_sql_cascade_delete_then_undo_ordering(conn, w):
    iid = w(lambda: items.create_item(conn, title="A", start="2026-12-01", tags=["T"]))
    tid = conn.execute("SELECT id FROM tag").fetchone()[0]
    with writer(conn, "agent", "drop tag via cascade"):
        conn.execute("DELETE FROM tag WHERE id=?", (tid,))   # item_tag removed by FK cascade
    assert conn.execute("SELECT count(*) FROM item_tag").fetchone()[0] == 0
    undomod.undo(conn)
    assert conn.execute("SELECT count(*) FROM item_tag").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM tag").fetchone()[0] == 1


def test_revert_refused_on_conflict_lists_rows(conn, w):
    a = w(lambda: items.create_item(conn, title="A", start="2026-12-01"), "add A")
    cs1 = conn.execute("SELECT max(id) FROM change_set").fetchone()[0]
    w(lambda: items.update_item(conn, a, title="A2"), "rename")
    with pytest.raises(undomod.RevertConflict) as e:
        undomod.revert_change_set(conn, cs1)
    assert e.value.conflicts[0]["table"] == "item" and e.value.conflicts[0]["row_key"] == a
    detail = history.change_set_detail(conn, cs1)
    assert detail["conflicts"]


def test_revert_any_single_change_set_when_no_conflict(conn, w):
    a = w(lambda: items.create_item(conn, title="A", start="2026-12-01"), "add A")
    b = w(lambda: items.create_item(conn, title="B", start="2026-12-05"), "add B")
    cs_a = conn.execute("SELECT id FROM change_set WHERE label='add A'").fetchone()[0]
    undomod.revert_change_set(conn, cs_a)
    assert titles(conn) == ["B"]


def test_sync_set_can_be_undone_by_ctrl_z(conn):
    with writer(conn, "sync", "Outlook sync, 2 items"):
        for i in range(2):
            conn.execute("INSERT INTO item (title,start_date,end_date,origin, source_id,outlook_uid) "
                         "VALUES (?,'2026-12-01','2026-12-01','outlook', 'testcal',?)", (f"M{i}", f"u{i}"))
    res = undomod.undo(conn)
    assert res["undone_label"] == "Outlook sync, 2 items"
    assert conn.execute("SELECT count(*) FROM item").fetchone()[0] == 0


def test_history_listing_flags(conn, w):
    w(lambda: items.create_item(conn, title="A", start="2026-12-01"), "add A")
    w(lambda: items.create_item(conn, title="B", start="2026-12-01"), "add B")
    undomod.undo(conn)
    ls = history.list_change_sets(conn)
    assert ls[0]["actor"] == "undo" and ls[0]["is_redo_target"]
    assert any(c["label"] == "add A" and c["is_undo_target"] for c in ls)
    assert any(c["label"] == "add B" and not c["in_effect"] for c in ls)


def test_item_history(conn, w):
    iid = w(lambda: items.create_item(conn, title="A", start="2026-12-01", tags=["T"]), "add")
    w(lambda: items.update_item(conn, iid, title="B"), "rename")
    w(lambda: items.add_attachment(conn, iid, "C:/x.txt"), "att")
    h = history.item_history(conn, iid)
    assert {c["table_name"] for c in h} == {"item", "item_tag", "attachment"}
    assert h[0]["label"] == "att"
