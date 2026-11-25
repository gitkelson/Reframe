"""Capture, pinning and reconciliation (R-160, R-161, R-163, R-165, R-168)."""
import errno
import json
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from reframe.db import undo
from reframe.db.changelog import writer
from reframe.domain import items
from reframe.vault import capture as cap
from reframe.vault import store as storemod
from reframe.vault.service import VaultService


@pytest.fixture
def v(tmp_path, monkeypatch, conn, db_path):
    monkeypatch.setenv("REFRAME_DATA_DIR", str(tmp_path / "data"))
    files = tmp_path / "files"
    files.mkdir()
    svc = VaultService(db_path, None, tmp_path / "data" / "vault", watch=False, reconcile_interval_s=0,
                       retry_base_s=3600)
    yield SimpleNamespace(svc=svc, store=svc.store, files=files, conn=conn, db_path=db_path)
    svc.stop()


def new_item(conn, title="Report"):
    with writer(conn, "user", "add item"):
        return items.create_item(conn, title=title, start="2026-12-01")


def add_link(conn, item_id, path):
    with writer(conn, "user", "Add link"):
        return items.add_attachment(conn, item_id, str(path))


def protect(v, aid):
    return v.svc.notify_attachment_added(aid).result(timeout=60)


def att(conn, aid):
    return conn.execute("SELECT * FROM attachment WHERE id=?", (aid,)).fetchone()


def version_sha(conn, vid):
    return conn.execute("SELECT sha256 FROM file_version WHERE id=?", (vid,)).fetchone()[0]


def source(conn, path):
    return conn.execute("SELECT * FROM file_source WHERE path_key=?", (cap.path_key(str(path)),)).fetchone()


def test_abc_links_pin_their_own_versions(v):
    f = v.files / "spec.docx"
    iid = new_item(v.conn)
    pins = []
    for content in (b"A" * 5000, b"B" * 5000, b"C" * 5000):
        f.write_bytes(content)
        aid = add_link(v.conn, iid, f)
        r = protect(v, aid)
        assert r["status"] == "protected"
        a = att(v.conn, aid)
        assert a["pin_basis"] == "attach"
        pins.append(a["pinned_version_id"])
        assert v.store.open_reader(version_sha(v.conn, a["pinned_version_id"])).read() == content
    assert len(set(pins)) == 3
    assert v.conn.execute("SELECT count(*) FROM file_version").fetchone()[0] == 3
    assert v.conn.execute("SELECT count(*) FROM file_source").fetchone()[0] == 1
    assert source(v.conn, f)["status"] == "protected"
    labels = [r[0] for r in v.conn.execute("SELECT label FROM change_set WHERE label LIKE 'Protect%'")]
    assert labels == ["Protect 'spec.docx'"] * 3


def test_shared_content_dedup_across_paths(v):
    a, b = v.files / "one.pdf", v.files / "sub" / "two.pdf"
    b.parent.mkdir()
    a.write_bytes(b"%PDF same bytes" * 1000)
    b.write_bytes(b"%PDF same bytes" * 1000)
    iid = new_item(v.conn)
    for p in (a, b):
        protect(v, add_link(v.conn, iid, p))
    assert v.conn.execute("SELECT count(*) FROM file_source").fetchone()[0] == 2
    rows = v.conn.execute("SELECT sha256 FROM file_version").fetchall()
    assert len(rows) == 2 and rows[0][0] == rows[1][0]
    assert v.store.size_info()["blobs"] == 1
    info = v.store.size_info(v.conn)
    assert info["logical_bytes"] == 2 * 15000 and info["savings_bytes"] > 15000


def test_changes_while_closed_are_captured_and_pin_kept(v, tmp_path, db_path):
    f = v.files / "notes.txt"
    f.write_bytes(b"first")
    aid = add_link(v.conn, new_item(v.conn), f)
    protect(v, aid)
    pinned = att(v.conn, aid)["pinned_version_id"]
    v.svc.stop()
    f.write_bytes(b"edited while Reframe was not running")
    svc2 = VaultService(db_path, None, tmp_path / "data" / "vault", watch=False, reconcile_interval_s=0)
    try:
        svc2.start()
        assert svc2.wait_idle(60)
    finally:
        svc2.stop()
    assert att(v.conn, aid)["pinned_version_id"] == pinned               # R-163: later versions only captured
    shas = {r[0] for r in v.conn.execute("SELECT sha256 FROM file_version")}
    assert len(shas) == 2
    assert source(v.conn, f)["last_hash"] == cap.hash_file(str(f))[0]


def test_locked_file_reports_locked_then_retry_succeeds(v):
    import win32con
    import win32file
    f = v.files / "locked.xlsx"
    f.write_bytes(b"locked bytes" * 100)
    aid = add_link(v.conn, new_item(v.conn), f)
    h = win32file.CreateFile(str(f), win32con.GENERIC_READ, 0, None, win32con.OPEN_EXISTING, 0, None)
    try:
        r = protect(v, aid)
        assert r["status"] == "locked"
        assert att(v.conn, aid)["pinned_version_id"] is None
        s = source(v.conn, f)
        assert s["status"] == "locked" and "locked" in s["last_error"]
        assert not v.store.staging.exists() or list(v.store.staging.iterdir()) == []
    finally:
        h.Close()
    assert protect(v, aid)["status"] == "protected"
    assert att(v.conn, aid)["pinned_version_id"] is not None


def test_changed_during_read_is_rejected_then_captured(v, monkeypatch):
    f = v.files / "busy.txt"
    f.write_bytes(b"v1" * 1000)
    aid = add_link(v.conn, new_item(v.conn), f)
    real = cap.hash_file
    calls = {"n": 0}

    def writer_interferes(path):
        calls["n"] += 1
        if calls["n"] == 1:
            with open(path, "ab") as fh:        # another program appends between the two reads
                fh.write(b"more")
        return real(path)
    monkeypatch.setattr(cap, "hash_file", writer_interferes)
    r = protect(v, aid)
    assert r["status"] == "changed" and "changed while it was being read" in r["error"]
    assert att(v.conn, aid)["pinned_version_id"] is None
    assert v.conn.execute("SELECT count(*) FROM file_version").fetchone()[0] == 0
    assert list(v.store.staging.iterdir()) == [] and list(v.store.iter_shas()) == []
    assert protect(v, aid)["status"] == "protected"
    assert v.store.open_reader(version_sha(v.conn, att(v.conn, aid)["pinned_version_id"])).read() == b"v1" * 1000 + b"more"


def test_unicode_and_long_path(v):
    d = v.files / "Prüfberichte – 日本語 ✓" / ("x" * 110) / ("y" * 110)
    import os
    os.makedirs("\\\\?\\" + str(d))
    target = str(d / "Bericht ✓ final.pdf")
    assert len(target) > 260
    with open("\\\\?\\" + target, "wb") as fh:
        fh.write("Unicode ✓".encode() * 50)
    aid = add_link(v.conn, new_item(v.conn), target)
    assert att(v.conn, aid)["kind"] == "file"
    assert protect(v, aid)["status"] == "protected"
    vid = att(v.conn, aid)["pinned_version_id"]
    assert v.store.open_reader(version_sha(v.conn, vid)).read() == "Unicode ✓".encode() * 50
    assert source(v.conn, target)["display_path"] == target


def test_unavailable_share_is_unreachable(v):
    aid = add_link(v.conn, new_item(v.conn), r"\\reframe-test-nohost.invalid\share\plan.pdf")
    t = time.monotonic()
    r = protect(v, aid)
    assert r["status"] == "unreachable"
    assert time.monotonic() - t < cap.REMOTE_TIMEOUT_S + 10
    assert att(v.conn, aid)["pinned_version_id"] is None


def test_missing_and_permission_denied(v, monkeypatch):
    iid = new_item(v.conn)
    gone = add_link(v.conn, iid, v.files / "never-existed.pdf")
    assert protect(v, gone)["status"] == "missing"
    f = v.files / "secret.pdf"
    f.write_bytes(b"secret")
    denied = add_link(v.conn, iid, f)

    def deny(path):
        raise PermissionError(errno.EACCES, "Access is denied", path)
    monkeypatch.setattr(cap, "open_shared", deny)
    r = protect(v, denied)
    assert r["status"] == "denied" and r["error"] == "Permission denied"
    assert source(v.conn, f)["status"] == "denied"


def test_disk_full_reports_failed_with_reason(v, monkeypatch):
    f = v.files / "big.bin"
    f.write_bytes(b"data" * 10000)
    aid = add_link(v.conn, new_item(v.conn), f)

    def full(fh):
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(storemod, "_fsync", full)
    r = protect(v, aid)
    assert r["status"] == "failed" and "Disk full" in r["error"]
    assert source(v.conn, f)["last_error"].startswith("Disk full")
    assert list(v.store.staging.iterdir()) == [] and list(v.store.iter_shas()) == []
    monkeypatch.undo()
    assert protect(v, aid)["status"] == "protected"


def test_restart_recovers_staging_and_tmp(v, tmp_path, db_path):
    v.store.ensure()
    junk = v.store.staging / "deadbeef.part"
    junk.write_bytes(b"half a blob from a killed capture")
    stale = tmp_path / "data" / "tmp" / "abc123"
    stale.mkdir(parents=True)
    (stale / "old.pdf").write_bytes(b"x")
    f = v.files / "after-crash.txt"
    f.write_bytes(b"content")
    aid = add_link(v.conn, new_item(v.conn), f)          # added while the app was down (e.g. by an agent)
    svc = VaultService(db_path, None, v.store.root, watch=False, reconcile_interval_s=0)
    try:
        svc.start()
        assert svc.wait_idle(60)
    finally:
        svc.stop()
    assert not junk.exists() and not stale.exists()
    assert svc.last_startup["staging_removed"] == 1
    assert att(v.conn, aid)["pinned_version_id"] is not None


def test_concurrent_attachments(v):
    iid = new_item(v.conn)
    ids = []
    for i in range(12):
        p = v.files / f"doc{i % 6}.txt"               # 6 files, two links each
        if not p.exists():
            p.write_bytes(f"document {i}".encode() * 500)
        ids.append(add_link(v.conn, iid, p))
    futs = [v.svc.notify_attachment_added(a) for a in ids]
    for fu in futs:
        fu.result(timeout=60)
    rows = [att(v.conn, a) for a in ids]
    assert all(r["pinned_version_id"] for r in rows)
    assert v.conn.execute("SELECT count(*) FROM file_version").fetchone()[0] == 6
    for i in range(6):
        assert rows[i]["pinned_version_id"] == rows[i + 6]["pinned_version_id"]


def test_raw_sql_link_picked_up_by_reconcile(v):
    f = v.files / "agent.txt"
    f.write_bytes(b"agent added this")
    iid = new_item(v.conn)
    v.conn.execute("INSERT INTO attachment (item_id, kind, target) VALUES (?, 'file', ?)", (iid, str(f)))
    v.svc.reconcile_now(deep=True).result(timeout=60)
    a = v.conn.execute("SELECT * FROM attachment WHERE item_id=?", (iid,)).fetchone()
    assert a["pinned_version_id"] and a["pin_basis"] == "attach"


def test_link_removal_and_undo_restores_pin(v):
    f = v.files / "keep.txt"
    f.write_bytes(b"keep me")
    aid = add_link(v.conn, new_item(v.conn), f)
    protect(v, aid)
    pinned = att(v.conn, aid)["pinned_version_id"]
    with writer(v.conn, "user", "Remove link"):
        items.remove_attachment(v.conn, aid)
    assert att(v.conn, aid) is None
    sha = version_sha(v.conn, pinned)
    assert v.store.has(sha)                                       # R-165: removal keeps vault content
    assert v.svc.prune(confirm=False)["versions"] == 0            # the undo history still references it
    undo.undo(v.conn)
    a = att(v.conn, aid)
    assert a["pinned_version_id"] == pinned and a["pin_basis"] == "attach"


def test_duplicate_item_shares_pins(v):
    f = v.files / "drawing.pdf"
    f.write_bytes(b"%PDF drawing")
    src = new_item(v.conn, "Source")
    aid = add_link(v.conn, src, f)
    protect(v, aid)
    with writer(v.conn, "user", "Duplicate"):
        dup = items.duplicate_item(v.conn, src, "2026-12-10", "Copy")
    d = v.conn.execute("SELECT * FROM attachment WHERE item_id=?", (dup,)).fetchone()
    assert d["pinned_version_id"] == att(v.conn, aid)["pinned_version_id"] and d["pin_basis"] == "duplicate"
    f.write_bytes(b"%PDF drawing rev B")
    v.svc.reconcile_now(deep=True).result(timeout=60)
    assert v.conn.execute("SELECT pinned_version_id FROM attachment WHERE id=?", (d["id"],)).fetchone()[0] == \
        att(v.conn, aid)["pinned_version_id"]


def test_migration_pin_basis(v):
    old, new = v.files / "old.txt", v.files / "new.txt"
    old.write_bytes(b"from an earlier import")
    new.write_bytes(b"added after migration")
    iid = new_item(v.conn)
    with writer(v.conn, "user", "links"):
        v.conn.execute("INSERT INTO attachment (id, item_id, kind, target, created_at) VALUES "
                       "('old1', ?, 'file', ?, '2026-11-01T10:00:00.000Z')", (iid, str(old)))
        v.conn.execute("INSERT INTO attachment (id, item_id, kind, target, created_at) VALUES "
                       "('new1', ?, 'file', ?, '2026-11-30T10:00:00.000Z')", (iid, str(new)))
    v.conn.execute("INSERT INTO setting (key, value) VALUES ('migrated_from', ?)",
                   (json.dumps({"imported_at": "2026-11-23T12:00:00Z", "source_user_version": 1}),))
    v.svc.reconcile_now(deep=True).result(timeout=60)
    assert att(v.conn, "old1")["pin_basis"] == "migration"
    assert att(v.conn, "new1")["pin_basis"] == "attach"
    labels = {r[0] for r in v.conn.execute("SELECT label FROM change_set")}
    assert cap.MIGRATION_LABEL in labels and "Protect 'new.txt'" in labels


def test_folder_and_url_links_never_captured(v):
    iid = new_item(v.conn)
    folder = add_link(v.conn, iid, v.files)
    url = add_link(v.conn, iid, "https://example.com/spec.pdf")
    assert protect(v, folder) is None and protect(v, url) is None
    v.svc.reconcile_now(deep=True).result(timeout=60)
    assert v.conn.execute("SELECT count(*) FROM file_source").fetchone()[0] == 0
    assert att(v.conn, folder)["pinned_version_id"] is None and att(v.conn, url)["pinned_version_id"] is None


def test_file_version_is_immutable_and_delete_refused_while_referenced(v):
    f = v.files / "x.txt"
    f.write_bytes(b"x")
    aid = add_link(v.conn, new_item(v.conn), f)
    protect(v, aid)
    vid = att(v.conn, aid)["pinned_version_id"]
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        v.conn.execute("UPDATE file_version SET size=0 WHERE id=?", (vid,))
    with pytest.raises(sqlite3.IntegrityError, match="still referenced"):
        v.conn.execute("DELETE FROM file_version WHERE id=?", (vid,))


def test_watcher_captures_edit_while_running(v, tmp_path, db_path):
    f = v.files / "watched.txt"
    f.write_bytes(b"before")
    aid = add_link(v.conn, new_item(v.conn), f)
    svc = VaultService(db_path, None, v.store.root, debounce_s=0.3, reconcile_interval_s=0)
    try:
        svc.start()
        assert svc.wait_idle(60)
        assert att(v.conn, aid)["pinned_version_id"]
        time.sleep(0.5)
        f.write_bytes(b"after an edit")
        target = cap.hash_file(str(f))[0]
        deadline = time.time() + 20
        while time.time() < deadline:
            if v.conn.execute("SELECT 1 FROM file_version WHERE sha256=?", (target,)).fetchone():
                break
            time.sleep(0.2)
        else:
            pytest.fail("watcher did not capture the edit")
    finally:
        svc.stop()
    assert version_sha(v.conn, att(v.conn, aid)["pinned_version_id"]) != target   # pin unchanged
