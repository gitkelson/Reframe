"""Executes the raw-SQL protocol exactly as written in agent/reframe-agent-instructions.md section 6."""
import re
import sqlite3
from pathlib import Path

from reframe.db import undo as undomod

INSTRUCTIONS = Path(__file__).resolve().parents[2] / "agent" / "reframe-agent-instructions.md"


def test_documented_protocol_snippet_works_and_is_undoable(conn, db_path):
    text = INSTRUCTIONS.read_text(encoding="utf-8")
    assert "BEGIN IMMEDIATE;" in text and "UPDATE app_context SET actor = 'external'" in text

    agent = sqlite3.connect(db_path, autocommit=True)
    agent.execute("PRAGMA foreign_keys = ON")
    agent.execute("PRAGMA busy_timeout = 5000")
    agent.execute("BEGIN IMMEDIATE")
    agent.execute("INSERT INTO change_set (actor, label) VALUES ('agent', 'Staff meeting 2026-11-10')")
    agent.execute("UPDATE app_context SET actor = 'agent', change_set_id = last_insert_rowid() WHERE id = 1")
    agent.execute("INSERT INTO tag (name) VALUES ('Spares Order')")
    agent.execute("INSERT INTO item (kind, title, start_date, end_date) VALUES ('plan', 'Spares order due', '2026-01-28', '2026-01-28')")
    agent.execute("INSERT INTO item_tag (item_id, tag_id, position) SELECT i.id, t.id, 0 FROM item i, tag t")
    agent.execute("UPDATE app_context SET actor = 'external', change_set_id = NULL WHERE id = 1")
    agent.execute("COMMIT")
    agent.close()

    row = conn.execute("SELECT actor, label FROM change_set").fetchone()
    assert tuple(row) == ("agent", "Staff meeting 2026-11-10")
    assert conn.execute("SELECT count(*) FROM change WHERE change_set_id IS NULL").fetchone()[0] == 0
    assert conn.execute("SELECT tags FROM v_item").fetchone()[0] == "Spares Order"
    assert undomod.undo(conn)["undone_label"] == "Staff meeting 2026-11-10"
    assert conn.execute("SELECT count(*) FROM item").fetchone()[0] == 0


def test_documented_recipes_are_valid_sql(conn):
    text = INSTRUCTIONS.read_text(encoding="utf-8")
    blocks = re.findall(r"```sql\n(.*?)```", text, flags=re.S)
    assert blocks
    for block in blocks:
        if "BEGIN IMMEDIATE" in block or "?" in block:
            continue
        for stmt in [s.strip() for s in block.split(";") if s.strip()]:
            if stmt.upper().startswith("PRAGMA"):
                continue
            conn.execute(stmt).fetchall()


def test_every_documented_cli_command_exists():
    from reframe import reframe_db
    text = INSTRUCTIONS.read_text(encoding="utf-8")
    cmds = set(re.findall(r"python -m reframe_db ([a-z-]+)", text))
    assert {"tag-tree", "move-tag", "retire-tag", "unretire-tag", "add-attachment", "calendars"} <= cmds
    for cmd in cmds:
        try:
            reframe_db.main([cmd, "--help"])
        except SystemExit as e:
            assert e.code == 0, cmd


def test_instructions_state_schema_2_and_name_the_data_folder():
    text = INSTRUCTIONS.read_text(encoding="utf-8")
    assert "`PRAGMA user_version` = 2" in text
    assert "Reframe v0.0" not in text and "%LOCALAPPDATA%\\Reframe\\" in text


def test_raw_sql_insert_of_the_specific_tag_brings_its_groups(conn, db_path):
    """Section 2a: agents insert the most specific tag; the database adds the group tags (R-111)."""
    agent = sqlite3.connect(db_path, autocommit=True)
    agent.execute("PRAGMA foreign_keys = ON")
    agent.execute("BEGIN IMMEDIATE")
    agent.execute("INSERT INTO change_set (actor, label) VALUES ('agent', 'tree')")
    agent.execute("UPDATE app_context SET actor = 'agent', change_set_id = last_insert_rowid() WHERE id = 1")
    agent.execute("INSERT INTO tag (id, name) VALUES ('g', 'ClientA'), ('c', 'Line 2')")
    agent.execute("UPDATE tag SET parent_id = 'g' WHERE id = 'c'")
    agent.execute("INSERT INTO item (id, kind, title, start_date, end_date) VALUES ('i', 'log', 'Fixture', '2026-11-10', '2026-11-10')")
    agent.execute("INSERT INTO item_tag (item_id, tag_id, position) VALUES ('i', 'c', 0)")
    agent.execute("UPDATE app_context SET actor = 'external', change_set_id = NULL WHERE id = 1")
    agent.execute("COMMIT")
    agent.close()
    assert conn.execute("SELECT tags FROM v_item").fetchone()[0] == "Line 2; ClientA"
    assert undomod.undo(conn)["undone_label"] == "tree"
    assert conn.execute("SELECT count(*) FROM item_tag").fetchone()[0] == 0
