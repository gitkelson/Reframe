"""Opening pinned versions, history, restore, prune, move, API and coordinated backup (R-162 - R-167)."""
import datetime as dt
import os
import shutil
import stat
from types import SimpleNamespace

import pytest

from reframe.db.changelog import writer
from reframe.domain import backup, items
from reframe.server import api_vault, security
from reframe.server.app import create_app
from reframe.vault import materialize
from reframe.vault.materialize import OpenError
from reframe.vault.service import VaultService

PORT = 50124
TOKEN = "vault-test-token"


@pytest.fixture
def v(tmp_path, monkeypatch, conn, db_path):
    monkeypatch.setenv("REFRAME_DATA_DIR", str(tmp_path / "data"))
    opened = []
    monkeypatch.setattr(os, "startfile", lambda p, *a: opened.append(str(p)), raising=False)
    files = tmp_path / "files"
    files.mkdir()
    svc = VaultService(db_path, None, tmp_path / "data" / "vault", watch=False, reconcile_interval_s=0,
                       retry_base_s=3600)
    yield SimpleNamespace(svc=svc, store=svc.store, files=files, conn=conn, db_path=db_path, opened=opened,
                          tmp=tmp_path)
    svc.stop()


def linked(v, name, content, item_id=None):
    f = v.files / name
    f.write_bytes(content)
    with writer(v.conn, "user", "add"):
        item_id = item_id or items.create_item(v.conn, title="Item", start="2026-12-01")
        aid = items.add_attachment(v.conn, item_id, str(f))
    v.svc.notify_attachment_added(aid).result(timeout=60)
    return f, aid, item_id


def pinned(v, aid):
    return v.conn.execute("SELECT pinned_version_id FROM attachment WHERE id=?", (aid,)).fetchone()[0]


def test_open_pinned_unchanged_opens_original(v):
    f, aid, _ = linked(v, "plan.pdf", b"%PDF A")
    r = materialize.open_attachment(v.conn, v.store, aid)
    assert r["opened"] == "original" and v.opened == [str(f)] and r["notice"] is None


def test_abc_open_each_link_its_own_version(v):
    f, a1, iid = linked(v, "spec.docx", b"A" * 100)
    f.write_bytes(b"B" * 100)
    _, a2, _ = linked(v, "spec.docx", b"B" * 100, iid)
    _, a3, _ = linked(v, "spec.docx", b"C" * 100, iid)
    r3 = materialize.open_attachment(v.conn, v.store, a3)
    assert r3["opened"] == "original"
    r1 = materialize.open_attachment(v.conn, v.store, a1)
    assert r1["opened"] == "saved" and r1["original_reachable"] and r1["latest_differs"] and r1["current_differs"]
    assert "The file has changed since it was attached." in r1["notice"]
    saved = v.opened[-1]
    assert saved == r1["path"] and saved.startswith(str(v.tmp / "data" / "tmp"))
    assert os.path.basename(saved) == "spec.docx"
    assert open(saved, "rb").read() == b"A" * 100
    assert not os.stat(saved).st_mode & stat.S_IWRITE                     # read-only copy
    r2 = materialize.open_attachment(v.conn, v.store, a2)
    assert open(r2["path"], "rb").read() == b"B" * 100
    latest = materialize.open_attachment(v.conn, v.store, a1, "latest")
    assert latest["opened"] == "saved" and open(latest["path"], "rb").read() == b"C" * 100
    cur = materialize.open_attachment(v.conn, v.store, a1, "current")
    assert cur["opened"] == "original" and v.opened[-1] == str(f)
    assert f.read_bytes() == b"C" * 100                                    # original never touched


def test_renamed_or_missing_original_opens_saved_version(v):
    f, aid, _ = linked(v, "report.xlsx", b"numbers")
    f.rename(f.with_name("report-renamed.xlsx"))
    r = materialize.open_attachment(v.conn, v.store, aid)
    assert r["opened"] == "saved" and not r["original_reachable"]
    assert "missing or unreachable" in r["notice"]
    assert open(r["path"], "rb").read() == b"numbers"
    with pytest.raises(OpenError):
        materialize.open_attachment(v.conn, v.store, aid, "current")


def test_unpinned_link_opens_original_or_reports_broken(v):
    with writer(v.conn, "user", "add"):
        iid = items.create_item(v.conn, title="Item", start="2026-12-01")
        aid = items.add_attachment(v.conn, iid, str(v.files / "nothing.pdf"))
    with pytest.raises(OpenError) as ei:
        materialize.open_attachment(v.conn, v.store, aid)
    assert ei.value.status == 404


def test_unsafe_extension_and_cleanup(v):
    _, aid, _ = linked(v, "tool.ps1", b"Write-Host hi")
    (v.files / "tool.ps1").write_bytes(b"changed")
    r = materialize.open_attachment(v.conn, v.store, aid)
    assert r["path"].endswith("tool.ps1.txt")
    assert materialize.cleanup_tmp() >= 1 and not os.path.exists(r["path"])


def test_versions_history_and_restore(v):
    f, a1, iid = linked(v, "doc.txt", b"one")
    _, a2, _ = linked(v, "doc.txt", b"two", iid)
    vs = materialize.versions(v.conn, a1)
    assert [x["size"] for x in vs] == [3, 3] and vs[0]["is_latest"]
    mine = [x for x in vs if x["is_pinned"]]
    assert len(mine) == 1 and mine[0]["pinned_by"][0]["attachment_id"] == a1
    dest = v.tmp / "restored.txt"
    out = materialize.restore_version(v.conn, v.store, mine[0]["id"], dest)
    assert out.read_bytes() == b"one"
    with pytest.raises(OpenError) as ei:
        materialize.restore_version(v.conn, v.store, mine[0]["id"], dest)
    assert ei.value.status == 409 and dest.read_bytes() == b"one"
    p = materialize.protection(v.conn, a1)
    assert p["protected"] and p["state"] == "protected" and p["pinned_at"] and p["last_capture_at"]


def test_prune_only_unreferenced(v):
    f, aid, _ = linked(v, "a.txt", b"pinned content")
    f.write_bytes(b"observed later, never pinned")
    v.svc.reconcile_now(deep=True).result(timeout=60)
    assert v.conn.execute("SELECT count(*) FROM file_version").fetchone()[0] == 2
    preview = v.svc.prune(confirm=False)
    assert preview["versions"] == 1 and preview["blobs"] == 1 and preview["bytes"] > 0 and not preview["deleted"]
    assert v.conn.execute("SELECT count(*) FROM file_version").fetchone()[0] == 2
    done = v.svc.prune(confirm=True)
    assert done["deleted"] and done["versions"] == 1
    left = v.conn.execute("SELECT id, sha256 FROM file_version").fetchall()
    assert [r[0] for r in left] == [pinned(v, aid)]
    assert list(v.store.iter_shas()) == [left[0][1]]


def test_move_vault_copies_verifies_and_keeps_old(v):
    _, aid, _ = linked(v, "m.txt", b"move me")
    old_root = v.store.root
    new_root = v.tmp / "new-vault"
    r = v.svc.move(new_root)
    assert r["copied"] == 1 and r["missing_before_move"] == []
    assert v.svc.store.root == new_root and list(v.svc.store.iter_shas())
    assert list(v.store.iter_shas())                                   # the old copy is kept
    res = materialize.open_attachment(v.conn, v.svc.store, aid, "latest")
    assert open(res["path"], "rb").read() == b"move me"
    assert old_root != new_root


def test_coordinated_backup_full_restore_after_vault_loss(v):
    f, a1, iid = linked(v, "contract.pdf", b"%PDF signed v1")
    _, a2, _ = linked(v, "contract.pdf", b"%PDF signed v2", iid)
    folder = v.tmp / "backups"
    s1 = backup.create_backup(v.conn, folder, now=dt.datetime(2026, 11, 20, 9, 0, 0), vault_root=v.store.root)
    info = backup.backup_info(s1)
    assert info["vault_blobs"] == 2 and info["vault_blobs_added"] == 2 and info["vault_missing"] == []
    s2 = backup.create_backup(v.conn, folder, now=dt.datetime(2026, 11, 20, 10, 0, 0), vault_root=v.store.root)
    assert backup.backup_info(s2)["vault_blobs_added"] == 0                 # incremental: nothing new
    with writer(v.conn, "user", "later"):
        items.create_item(v.conn, title="After backup", start="2026-12-05")
    shutil.rmtree(v.store.root)                                           # simulated vault loss
    f.unlink()                                                            # and the original
    safety = backup.restore_backup(v.db_path, s2, folder, vault_root=v.store.root)
    assert backup.is_complete(safety)
    assert v.conn.execute("SELECT count(*) FROM item WHERE title='After backup'").fetchone()[0] == 0
    for aid, content in ((a1, b"%PDF signed v1"), (a2, b"%PDF signed v2")):
        r = materialize.open_attachment(v.conn, v.store, aid)
        assert r["opened"] == "saved" and open(r["path"], "rb").read() == content


def test_restore_refuses_set_with_missing_versions(v):
    _, aid, _ = linked(v, "c.pdf", b"%PDF c")
    folder = v.tmp / "backups"
    s = backup.create_backup(v.conn, folder, now=dt.datetime(2026, 11, 20, 9, 0, 0), vault_root=v.store.root)
    shutil.rmtree(v.store.root)
    pool = backup.pool(folder)
    sha = next(pool.iter_shas())
    pool.delete(sha)
    with pytest.raises(backup.BackupError, match=sha[:12]):
        backup.restore_backup(v.db_path, s, folder, vault_root=v.store.root)
    assert pinned(v, aid)                                                 # live database untouched


# ------------------------------------------------------------------ API

@pytest.fixture
def api(v):
    app = create_app(v.db_path, token=TOKEN, port=PORT)
    app.config["VAULT"] = v.svc
    api_vault.init_app(app)
    c = app.test_client()

    def call(method, path, json=None):
        return getattr(c, method)(path, json=json, headers={security.TOKEN_HEADER: TOKEN},
                                  base_url=f"http://127.0.0.1:{PORT}")
    return call


def test_api_endpoints(v, api):
    f, a1, iid = linked(v, "api.docx", b"first")
    _, a2, _ = linked(v, "api.docx", b"second", iid)
    s = api("get", "/api/vault/status").get_json()
    assert s["protected"] == 2 and s["pending"] == 0 and s["usage"]["versions"] == 2
    assert s["vault_path"] == str(v.store.root) and s["synced_folder_warning"] is None
    p = api("get", f"/api/attachments/{a1}/protection").get_json()
    assert p["protected"] and p["status"] == "protected"
    vs = api("get", f"/api/attachments/{a1}/versions").get_json()["versions"]
    assert len(vs) == 2
    r = api("post", f"/api/attachments/{a1}/open-smart").get_json()
    assert r["opened"] == "saved" and r["latest_differs"]
    r = api("post", f"/api/attachments/{a2}/open-smart").get_json()
    assert r["opened"] == "original"
    r = api("post", f"/api/attachments/{a1}/open-version", {"version": "latest"}).get_json()
    assert open(r["path"], "rb").read() == b"second"
    dest = v.tmp / "out.docx"
    dest.write_bytes(b"exists")
    assert api("post", f"/api/vault/versions/{vs[0]['id']}/restore", {"dest": str(dest)}).status_code == 409
    ok = api("post", f"/api/vault/versions/{vs[0]['id']}/restore", {"dest": str(v.tmp / 'new.docx')})
    assert ok.status_code == 200 and (v.tmp / "new.docx").read_bytes() == b"second"
    assert api("post", "/api/vault/prune", {"confirm": False}).get_json()["versions"] == 0
    with writer(v.conn, "user", "url"):
        url = items.add_attachment(v.conn, iid, "https://example.com")
    assert api("get", f"/api/attachments/{url}/protection").get_json()["status"] == "not_applicable"
    assert api("post", "/api/attachments/nope/open-smart").status_code == 404
