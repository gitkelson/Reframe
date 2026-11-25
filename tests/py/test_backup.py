import datetime as dt
import json
import sqlite3
import threading

import pytest

from reframe.db.changelog import writer
from reframe.domain import backup, items


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """The default vault lives under the data folder; keep it inside tmp."""
    monkeypatch.setenv("REFRAME_DATA_DIR", str(tmp_path / "data"))


def test_backup_is_consistent_set_and_restorable(conn, db_path, tmp_path):
    with writer(conn, "user", "add"):
        items.create_item(conn, title="A", start="2026-12-01")
    folder = tmp_path / "bk"
    p = backup.create_backup(conn, folder, now=dt.datetime(2026, 11, 10, 8, 0, 0))
    assert p.name == "reframe-20261110-080000" and p.is_dir()
    assert [x.name for x in folder.iterdir()] == [p.name]  # no -wal / temp files left behind
    assert sorted(x.name for x in p.iterdir()) == ["COMPLETE", "reframe.sqlite", "vault-manifest.json"]
    assert backup.backup_info(p)["db_integrity"] == "ok"
    assert backup.list_backups(folder) == [p] and backup.backup_size(p) > 0
    chk = sqlite3.connect(backup.backup_db_file(p))
    assert chk.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert chk.execute("SELECT count(*) FROM item").fetchone()[0] == 1
    chk.close()

    with writer(conn, "user", "add b"):
        items.create_item(conn, title="B", start="2026-12-02")
    safety = backup.restore_backup(db_path, p, folder)
    assert safety.exists() and backup.is_complete(safety)
    assert conn.execute("SELECT count(*) FROM item").fetchone()[0] == 1
    s = sqlite3.connect(backup.backup_db_file(safety))
    assert s.execute("SELECT count(*) FROM item").fetchone()[0] == 2  # previous state kept
    s.close()


def test_same_second_backups_do_not_collide(conn, tmp_path):
    now = dt.datetime(2026, 11, 10, 8, 0, 0)
    a = backup.create_backup(conn, tmp_path, now=now)
    b = backup.create_backup(conn, tmp_path, now=now)
    assert a != b and a.exists() and b.exists()
    assert backup.list_backups(tmp_path) == [b, a]


def test_backup_while_writing(conn, db_path, tmp_path):
    from reframe.db.connection import connect
    stop = threading.Event()

    def writer_thread():
        c = connect(db_path)
        n = 0
        while not stop.is_set():
            with writer(c, "agent", f"w{n}"):
                items.create_item(c, title=f"I{n}", start="2026-12-01")
            n += 1
        c.close()

    t = threading.Thread(target=writer_thread)
    t.start()
    try:
        for i in range(3):
            p = backup.create_backup(conn, tmp_path / "bk", now=dt.datetime(2026, 11, 10, 8, 0, i))
            chk = sqlite3.connect(backup.backup_db_file(p))
            assert chk.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            chk.close()
    finally:
        stop.set()
        t.join()


def test_retention_policy(tmp_path):
    now = dt.datetime(2026, 11, 10, 12, 0, 0)
    made = []
    # 60 hourly backups today and yesterday, plus one per week going back 2 years
    for h in range(60):
        made.append(now - dt.timedelta(hours=h))
    for w in range(1, 110):
        made.append(now - dt.timedelta(weeks=w, hours=1))
    for t in made:
        (tmp_path / f"reframe-{t:%Y%m%d-%H%M%S}.sqlite").write_bytes(b"x")   # old single-file backups still count
    backup.apply_retention(tmp_path, now=now)
    left = backup.list_backups(tmp_path)
    times = [backup._backup_time(p) for p in left]
    assert all(t in times for t in sorted(made, reverse=True)[:20])             # last 20
    days = {t.date() for t in times if (now - t).days < 30}
    assert len(days) == len({t.date() for t in made if (now - t).days < 30})     # one per recent day
    months = {(t.year, t.month) for t in times}
    assert len(months) >= 12
    assert len(left) < len(made)
    assert not any((now - t).days > 366 + 31 for t in times)


def _fake_set(folder, t, blobs):
    d = folder / f"reframe-{t:%Y%m%d-%H%M%S}"
    d.mkdir(parents=True)
    (d / "reframe.sqlite").write_bytes(b"x")
    (d / "vault-manifest.json").write_text(json.dumps({"format": 1, "blobs": {b: 1 for b in blobs}}))
    (d / "COMPLETE").write_text("{}")
    return d


def test_retention_never_deletes_blobs_a_retained_set_needs(tmp_path):
    now = dt.datetime(2026, 11, 10, 12, 0, 0)
    pool = backup.pool(tmp_path)
    old_only = pool.put_bytes(b"only in old sets")
    shared = pool.put_bytes(b"old and new sets")
    for w in range(1, 30):                                    # weekly sets older than the keep window
        _fake_set(tmp_path, now - dt.timedelta(days=400 + 7 * w), [old_only, shared])
    for h in range(3):
        _fake_set(tmp_path, now - dt.timedelta(hours=h), [shared])
    removed = backup.apply_retention(tmp_path, keep_last=3, keep_days=1, keep_months=1, now=now)
    assert len(removed) == 29 and len(backup.list_backups(tmp_path)) == 3
    assert pool.has(shared) and pool.verify(shared)
    assert not pool.has(old_only)


def test_incomplete_set_is_never_offered(conn, tmp_path):
    good = backup.create_backup(conn, tmp_path, now=dt.datetime(2026, 11, 10, 8, 0, 0))
    partial = tmp_path / "reframe-20261110-090000"            # newer, but its COMPLETE marker was never written
    partial.mkdir()
    (partial / "reframe.sqlite").write_bytes(backup.backup_db_file(good).read_bytes())
    legacy = tmp_path / "reframe-20261109-080000.sqlite"
    legacy.write_bytes(backup.backup_db_file(good).read_bytes())
    assert backup.list_backups(tmp_path) == [good, legacy]
    assert backup.list_incomplete(tmp_path) == [partial]
    assert backup.newest_age_seconds(tmp_path) == pytest.approx(
        (dt.datetime.now() - dt.datetime(2026, 11, 10, 8, 0, 0)).total_seconds(), abs=5)
    with pytest.raises(backup.BackupError, match="incomplete"):
        backup.restore_backup(tmp_path / "reframe.sqlite", partial, tmp_path)


def test_failed_backup_leaves_no_set(conn, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(backup, "_write_json", boom)
    with pytest.raises(OSError):
        backup.create_backup(conn, tmp_path, now=dt.datetime(2026, 11, 10, 8, 0, 0))
    assert backup.list_backups(tmp_path) == [] and backup.list_incomplete(tmp_path) == []


def test_backup_if_stale(conn, tmp_path):
    assert backup.backup_if_stale(conn, tmp_path) is not None
    assert backup.backup_if_stale(conn, tmp_path) is None


def test_restore_rejects_corrupt_backup(conn, db_path, tmp_path):
    bad = tmp_path / "reframe-20261110-080000.sqlite"
    bad.write_bytes(b"not a database" * 100)
    with pytest.raises(Exception):
        backup.restore_backup(db_path, bad, tmp_path / "bk")


def test_legacy_single_file_backup_still_restores(conn, db_path, tmp_path):
    with writer(conn, "user", "add"):
        items.create_item(conn, title="A", start="2026-12-01")
    legacy = tmp_path / "reframe-20261101-080000.sqlite"
    dst = sqlite3.connect(legacy)
    conn.backup(dst)
    dst.execute("PRAGMA journal_mode=DELETE")
    dst.close()
    with writer(conn, "user", "add b"):
        items.create_item(conn, title="B", start="2026-12-02")
    assert legacy in backup.list_backups(tmp_path)
    backup.restore_backup(db_path, legacy, tmp_path / "bk", vault_root=tmp_path / "vault")
    assert conn.execute("SELECT count(*) FROM item").fetchone()[0] == 1
