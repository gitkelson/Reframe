import json

import pytest

from reframe import reframe_db
from reframe.db.connection import connect


@pytest.fixture
def cli(db_path, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("REFRAME_DB", str(db_path))

    def run(*args):
        from reframe.domain import settings
        c = reframe_db.open_db(db_path)
        settings.set_many(c, {"backup_folder": str(tmp_path / "bk")})
        c.close()
        rc = reframe_db.main(list(args))
        out = capsys.readouterr().out
        return rc, json.loads(out)
    return run


def test_add_item_and_schedule(cli):
    rc, out = cli("add-item", "--kind", "plan", "--title", "Spares order due", "--start", "20261128",
                  "--tag", "Spares", "--label", "From staff meeting")
    assert rc == 0 and out["created"][0]["start"] == "2026-11-28"
    rc, out = cli("schedule", "--from", "2026-11-01", "--to", "2026-12-31", "--tag", "spares!")
    assert [r["title"] for r in out] == ["Spares order due"]


def test_duplicate_is_returned_not_inserted_then_forced(cli):
    cli("add-item", "--title", "Spares order due", "--start", "2026-11-28")
    rc, out = cli("add-item", "--title", "Spares order due!", "--start", "2026-11-28")
    assert out["created"] == [] and out["skipped"][0]["reason"] == "likely duplicate"
    rc, out = cli("add-item", "--title", "Spares order due!", "--start", "2026-11-28", "--force")
    assert len(out["created"]) == 1


def test_near_duplicate_tag_refused_unless_allowed(cli):
    cli("add-item", "--title", "A", "--start", "2026-11-01", "--tag", "Spares Order")
    rc, out = cli("add-item", "--title", "B", "--start", "2026-11-02", "--tag", "Spares Ordr")
    assert rc == 2 and out["error"] == "NearTagMatch"
    rc, out = cli("add-item", "--title", "B", "--start", "2026-11-02", "--tag", "Spares Ordr", "--allow-similar-tags")
    assert rc == 0


def test_bad_date_reports_error(cli):
    rc, out = cli("add-item", "--title", "A", "--start", "11/08/2026")
    assert rc == 2 and out["error"] == "DateError"


def test_batch_is_one_change_set_and_undoable(cli, db_path):
    batch = db_path.parent / "b.json"
    batch.write_text(json.dumps([
        {"kind": "log", "title": "Reviewed drawings", "start": "2026-11-10", "tags": ["PRJ-20"]},
        {"kind": "plan", "title": "Delivery", "start": "2026-11-20", "end": "2026-11-21"},
    ]), encoding="utf-8")
    rc, out = cli("add-items", "--file", str(batch), "--label", "Staff meeting 11/10")
    assert len(out["created"]) == 2
    c = connect(db_path)
    assert c.execute("SELECT count(*) FROM change_set WHERE label='Staff meeting 11/10'").fetchone()[0] == 1
    c.close()
    rc, out = cli("undo")
    assert out["undone_label"] == "Staff meeting 11/10"
    rc, out = cli("schedule", "--from", "2026-11-01", "--to", "2026-12-01")
    assert out == []


def test_update_item_changes_only_given_fields(cli):
    rc, out = cli("add-item", "--title", "Delivery", "--start", "2026-11-20", "--note", "keep me")
    iid = out["created"][0]["id"]
    rc, out = cli("update-item", iid, "--start", "2026-11-25", "--end", "2026-11-25")
    assert out["changed"] == ["start_date", "end_date"]
    rc, out = cli("search", "keep me")
    assert out[0]["start_date"] == "2026-11-25"


def test_agent_actor_and_external_direct_sql_grouping(cli, db_path):
    cli("add-item", "--title", "A", "--start", "2026-11-20")
    c = connect(db_path)
    assert c.execute("SELECT actor FROM change_set").fetchone()[0] == "agent"
    c.execute("UPDATE item SET title='Edited by SQL'")
    c.close()
    rc, out = cli("history")
    assert out[0]["actor"] == "external"


def test_tag_tree_move_retire_and_attachment_commands(cli, tmp_path):
    rc, out = cli("add-item", "--title", "Fixture build", "--start", "2026-12-01", "--tag", "Line 2")
    item = out["created"][0]["id"]
    cli("add-item", "--title", "Group holder", "--start", "2026-12-02", "--tag", "ClientA")
    rc, out = cli("move-tag", "Line 2", "--to", "ClientA")
    assert rc == 0 and out["items_updated"] == 1
    rc, tree = cli("tag-tree")
    assert {t["name"]: t["path"] for t in tree}["Line 2"] == "ClientA > Line 2"
    rc, sched = cli("schedule", "--from", "2026-12-01", "--to", "2026-12-01")
    assert "ClientA" in sched[0]["tags"]                       # the group tag was added by the database
    rc, out = cli("retire-tag", "ClientA")
    assert rc == 0 and out["retired"] is True
    rc, out = cli("add-item", "--title", "New", "--start", "2026-12-05", "--tag", "ClientA", "--force")
    assert rc == 2 and out["error"] == "RetiredTag"
    rc, out = cli("add-item", "--title", "New", "--start", "2026-12-05", "--tag", "ClientA", "--force", "--allow-retired")
    assert rc == 0
    f = tmp_path / "spec.pdf"
    f.write_bytes(b"%PDF-1.4")
    rc, out = cli("add-attachment", item, str(f))
    assert rc == 0 and out["attachment"]
    rc, cals = cli("calendars")
    assert rc == 0 and isinstance(cals, list)
    rc, out = cli("move-tag", "Line 2", "--root")
    assert rc == 0
