import re
import sqlite3
from pathlib import Path

import pytest

from reframe.db import build_schema
from reframe.db.connection import connect, init_db
from reframe.domain import tags


def test_schema_file_is_current():
    assert build_schema.schema_path().read_text(encoding="utf-8") == build_schema.build()


def test_migration_002_file_is_current():
    assert build_schema.migration_002_path().read_text(encoding="utf-8") == build_schema.build_migration_002()


V1_SCHEMA = Path(__file__).with_name("fixtures") / "schema_v1.sql"


def make_v1(path):
    """A version-1 database with representative rows written through the version-1 protocol."""
    from reframe.db.connection import _apply
    c = connect(path)
    c.execute("BEGIN")
    _apply(c, V1_SCHEMA.read_text(encoding="utf-8-sig"))
    c.execute("PRAGMA user_version=1")
    c.execute("COMMIT")
    c.execute("BEGIN IMMEDIATE")
    cs = c.execute("INSERT INTO change_set (actor, label) VALUES ('user', 'v1 edit')").lastrowid
    c.execute("UPDATE app_context SET actor='user', change_set_id=?", (cs,))
    c.execute("INSERT INTO item (id, title, start_date, end_date, notes) VALUES ('i1','Spares due','2026-01-28','2026-01-28','n')")
    c.execute("INSERT INTO item (id, title, start_date, end_date, origin, outlook_uid, outlook_calendar) "
              "VALUES ('o1','Mtg','2026-01-05','2026-01-05','outlook','CALX|g','CALX')")
    c.execute("INSERT INTO tag (id, name, color_slot) VALUES ('t1','Spares', 3), ('t2','meeting', NULL)")
    c.execute("INSERT INTO item_tag VALUES ('i1','t1',0), ('o1','t2',1000)")
    c.execute("INSERT INTO attachment (id, item_id, kind, target) VALUES ('a1','i1','file','C:\\x.pdf')")
    c.execute("UPDATE app_context SET actor='external', change_set_id=NULL")
    c.execute("COMMIT")
    c.execute("INSERT INTO setting VALUES ('outlook_calendars', ?)", (
        '[{"id": "CALX", "name": "Calendar", "path": "\\\\\\\\me\\\\Calendar", "selected": true, "holidays": false},'
        ' {"id": "HOL", "name": "Holidays", "path": "", "selected": false, "holidays": true}]',))
    c.close()


def _normalized_master(c):
    return sorted((r[0], r[1], r[2], re.sub(r"\s+", " ", (r[3] or "").replace('"', "")))
                  for r in c.execute("SELECT type, name, tbl_name, sql FROM sqlite_master"))


def test_fresh_and_migrated_databases_have_identical_schema_and_data_is_kept(tmp_path):
    fresh = init_db(tmp_path / "fresh.sqlite")
    make_v1(tmp_path / "v1.sqlite")
    before = connect(tmp_path / "v1.sqlite")
    counts = {t: before.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
              for t in ("item", "tag", "item_tag", "attachment", "change_set", "change")}
    before.close()
    mig = init_db(tmp_path / "v1.sqlite")
    assert mig.execute("PRAGMA user_version").fetchone()[0] == 2
    assert _normalized_master(fresh) == _normalized_master(mig)
    assert mig.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert mig.execute("PRAGMA foreign_key_check").fetchall() == []
    assert mig.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert {t: mig.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in counts} == counts
    assert mig.execute("SELECT count(*) FROM item_tag WHERE implied <> 0").fetchone()[0] == 0
    assert tuple(mig.execute("SELECT title, notes, origin FROM item WHERE id='i1'").fetchone()) == ("Spares due", "n", "local")
    srcs = {r[0]: (r[1], r[2]) for r in mig.execute("SELECT external_id, connected, visible FROM calendar_source")}
    assert srcs == {"CALX": (1, 1), "HOL": (1, 0)}                     # selected calendars stay visible (13.9 #7)
    assert mig.execute("SELECT count(*) FROM item WHERE origin<>'local' AND source_id IS NULL").fetchone()[0] == 0


def test_pre_migration_change_set_can_be_reverted_and_reapplied(tmp_path):
    """v1 snapshots lack the version-2 columns; undo leaves those columns unchanged."""
    from reframe.db import undo
    make_v1(tmp_path / "v1.sqlite")
    c = init_db(tmp_path / "v1.sqlite")
    assert c.execute("SELECT source_id IS NOT NULL FROM item WHERE id='o1'").fetchone()[0] == 1
    cs = c.execute("SELECT id FROM change_set WHERE label='v1 edit'").fetchone()[0]
    res = undo.revert_change_set(c, cs)
    assert c.execute("SELECT count(*) FROM item").fetchone()[0] == 0
    undo.revert_change_set(c, res["change_set_id"])               # redo
    assert c.execute("SELECT title FROM item WHERE id='i1'").fetchone()[0] == "Spares due"
    assert c.execute("SELECT implied FROM item_tag WHERE item_id='i1'").fetchone()[0] == 0


def test_user_version_and_pragmas(conn):
    assert conn.execute("PRAGMA user_version").fetchone()[0] == build_schema.SCHEMA_VERSION
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_init_is_idempotent(db_path):
    init_db(db_path).close()
    c = init_db(db_path)
    assert c.execute("SELECT count(*) FROM app_context").fetchone()[0] == 1


@pytest.mark.parametrize("bad", ["2026-02-31", "11/08/2026", "20261108", "2026-1-8", ""])
def test_item_rejects_non_iso_dates(conn, bad):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO item (title, start_date, end_date) VALUES ('x', ?, '2026-12-01')", (bad,))


def test_end_before_start_rejected(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO item (title, start_date, end_date) VALUES ('x', '2026-12-02', '2026-12-01')")


def test_empty_title_rejected(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO item (title, start_date, end_date) VALUES ('  ', '2026-12-01', '2026-12-01')")


def test_ids_default_in_sql(conn):
    conn.execute("INSERT INTO item (title, start_date, end_date) VALUES ('x', '2026-12-01', '2026-12-01')")
    assert len(conn.execute("SELECT id FROM item").fetchone()[0]) == 32


def test_foreign_keys_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO item_tag (item_id, tag_id) VALUES ('nope', 'nope')")


def test_agent_can_insert_tag_with_name_only_and_duplicates_blocked(conn):
    conn.execute("INSERT INTO tag (name) VALUES ('Spares Order')")
    for dup in ("spares order", "SPARES-ORDER!", "  spares   order "):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO tag (name) VALUES (?)", (dup,))


def test_tag_with_no_alphanumerics_rejected(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO tag (name) VALUES ('!!!')")


@pytest.mark.parametrize("name", [
    "Spares", "SPARES!", "PRJ-20", "PRJ 20", "  a  b\t\nc  ", "it's", "x__y", "A.B,C;D", "[brackets] {braces}",
    "Ünïcode Café", "tab\tsep", "~~lead", "trail~~", "q\"uote", "a\\b/c", "100%", "a&b", "a|b", "$5",
])
def test_norm_key_python_matches_sql(conn, name):
    sql_key = conn.execute(
        "SELECT " + build_schema.norm_sql("?"), (name,)).fetchone()[0]
    assert tags.norm_key(name) == sql_key


def test_norm_key_examples():
    assert tags.norm_key("Spares") == tags.norm_key("spares") == tags.norm_key("SPARES!")
    assert tags.norm_key("PRJ-20") == tags.norm_key("PRJ 20")
    assert tags.norm_key("Spares Order") != tags.norm_key("Spare Order")
    assert tags.norm_key("Order Spares") != tags.norm_key("Spares Order")


def test_v_item_excludes_deleted_and_hidden(conn):
    conn.execute("INSERT INTO item (id,title,start_date,end_date) VALUES ('a','A','2026-12-01','2026-12-01')")
    conn.execute("INSERT INTO item (id,title,start_date,end_date,deleted_at) VALUES ('b','B','2026-12-01','2026-12-01','x')")
    conn.execute("INSERT INTO item (id,title,start_date,end_date,hidden_at) VALUES ('c','C','2026-12-01','2026-12-01','x')")
    assert [r["id"] for r in conn.execute("SELECT id FROM v_item")] == ["a"]


def test_readonly_connection(db_path, conn):
    ro = connect(db_path, readonly=True)
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("DELETE FROM setting")


def test_current_version_with_foreign_tables_is_refused_with_a_clear_message(tmp_path):
    import sqlite3 as _sq
    from reframe.db.connection import SchemaMismatch, init_db
    from reframe.db.build_schema import SCHEMA_VERSION
    p = tmp_path / "other-build.sqlite"
    c = _sq.connect(p)
    c.execute("CREATE TABLE calendar_source (id TEXT PRIMARY KEY, name TEXT)")
    c.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    c.close()
    with pytest.raises(SchemaMismatch, match=r"calendar_source\.external_id.*another build"):
        init_db(p)
