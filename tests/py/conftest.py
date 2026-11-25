import pytest

from reframe.db.connection import init_db
from reframe.db.changelog import writer


@pytest.fixture(autouse=True)
def isolated_backup_dir(tmp_path, monkeypatch):
    """Tests must never write backups into the user's real Documents folder."""
    monkeypatch.setenv("REFRAME_BACKUP_DIR", str(tmp_path / "default-backups"))


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "reframe.sqlite"


@pytest.fixture
def conn(db_path):
    c = init_db(db_path)
    add_test_source(c)
    yield c
    c.close()


def add_test_source(c, source_id="testcal", external_id="cal", kind="outlook"):
    """A calendar source for tests that insert imported items directly (item.source_id is required, spec 5.2)."""
    c.execute("INSERT OR IGNORE INTO calendar_source (id, kind, external_id, name, location, visible) "
              "VALUES (?,?,?,?,?,1)", (source_id, kind, external_id, "Test calendar", "\\\\test\\Calendar"))
    return source_id


@pytest.fixture
def w(conn):
    """Run a callable inside a user writer session: w(lambda: ..., label)."""
    def run(fn, label="test", actor="user"):
        with writer(conn, actor, label):
            return fn()
    return run
