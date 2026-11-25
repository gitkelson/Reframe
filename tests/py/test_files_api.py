"""File timeline HTTP API: options, scan, entries, details, thumbnails by opaque id only, open/reveal re-validation."""
import io
import sys
import threading
import time

import pytest
from PIL import Image

from reframe.files.watch import RootWatcher
from reframe.server import api_files, security
from reframe.server.app import create_app

PORT = 50124
TOKEN = "tok-files"


class Launcher:
    def __init__(self):
        self.calls = []

    def open(self, p): self.calls.append(("open", p))
    def reveal(self, p): self.calls.append(("reveal", p))


@pytest.fixture
def app(conn, db_path, tmp_path):
    a = create_app(db_path, token=TOKEN, port=PORT)
    api_files.init_app(a, watcher_factory=None, reconcile_s=0, cache_dir=tmp_path / "thumbs", launcher=Launcher())
    yield a
    api_files.stop(a)


@pytest.fixture
def api(app):
    c = app.test_client()

    class Api:
        def req(self, method, path, json=None, **kw):
            h = {security.TOKEN_HEADER: TOKEN, **kw.pop("headers", {})}
            return getattr(c, method)(path, json=json, headers=h, base_url=f"http://127.0.0.1:{PORT}", **kw)

        def get(self, p, **kw): return self.req("get", p, **kw)
        def post(self, p, json=None, **kw): return self.req("post", p, json if json is not None else {}, **kw)
        def put(self, p, json=None, **kw): return self.req("put", p, json if json is not None else {}, **kw)
    return Api()


def make_tree(root):
    (root / "Trips" / "Rome").mkdir(parents=True)
    (root / "Docs").mkdir()
    Image.new("RGB", (64, 48), "green").save(root / "Trips" / "Rome" / "a.jpg", "JPEG")
    (root / "Docs" / "notes.txt").write_text("hi")
    (root / "top.txt").write_text("top")


def scanned(app, api, roots):
    r = api.put("/api/files/options", {"roots": [str(r) for r in roots]})
    assert r.status_code == 200, r.get_json()
    assert app.config["FILES"].wait_idle()


def entries(api, mode="modified"):
    r = api.get(f"/api/files/entries?mode={mode}")
    assert r.status_code == 200
    return r.get_json()


def test_token_required(app):
    c = app.test_client()
    assert c.get("/api/files/status", base_url=f"http://127.0.0.1:{PORT}").status_code == 403


def test_status_options_and_suggestions(api, app, tmp_path):
    st = api.get("/api/files/status").get_json()
    assert st["state"] == "no_roots" and st["ever_scanned"] is False
    assert api.get("/api/files/suggested-exclusions").get_json()["suggestions"][0] == ".tmp"
    root = tmp_path / "R"
    make_tree(root)
    r = api.put("/api/files/options", {"roots": [str(root)], "excluded_ext": [".TXT"], "include_hidden": False})
    assert r.status_code == 200
    assert r.get_json()["options"]["excluded_ext"] == [".txt"]
    app.config["FILES"].wait_idle()
    assert api.get("/api/files/options").get_json()["roots"] == [str(root)]
    assert [x[1] for x in entries(api)["rows"]] == ["a.jpg"]
    assert api.get("/api/files/status").get_json()["state"] == "idle"


def test_bad_roots_refused_with_reasons(api, tmp_path):
    a = tmp_path / "A"
    (a / "B").mkdir(parents=True)
    r = api.put("/api/files/options", {"roots": [str(a), str(a / "B"), str(a)]})
    assert r.status_code == 400
    body = r.get_json()
    assert body["error"] == "bad_roots" and len(body["problems"]) == 2
    assert "inside the root" in body["message"] and "twice" in body["message"]
    assert api.put("/api/files/options", {"bogus": 1}).status_code == 400
    assert api.put("/api/files/options", {"excluded_ext": ["a/b"]}).status_code == 400


def test_entries_shape_and_details_without_folder_tags(api, app, tmp_path):
    root = tmp_path / "R"
    make_tree(root)
    scanned(app, api, [root])
    p = entries(api, "created")
    assert p["columns"][:6] == ["id", "name", "ext", "kind", "day", "src"]
    rows = {r[1]: dict(zip(p["columns"], r)) for r in p["rows"]}
    assert "tags" not in p and "tags" not in p["columns"]
    assert rows["a.jpg"]["dir"] == "Trips\\Rome"
    assert rows["a.jpg"]["kind"] == 1 and rows["notes.txt"]["kind"] == 0
    assert api.get("/api/files/entries?mode=bogus").status_code == 400
    d = api.get(f"/api/files/entry/{rows['a.jpg']['id']}").get_json()
    assert d["path"] == str(root / "Trips" / "Rome" / "a.jpg")
    assert "folder_tags" not in d and d["root"]["path"] == str(root)
    assert d["modes"]["modified"]["source_label"] == "File modified"
    assert {"name", "size", "created", "modified", "taken"} <= set(d)
    assert api.get("/api/files/entry/nope").status_code == 404


def test_thumbnail_only_by_indexed_id(api, app, tmp_path):
    root = tmp_path / "R"
    make_tree(root)
    scanned(app, api, [root])
    rows = {r[1]: r for r in entries(api)["rows"]}
    r = api.get(f"/api/files/thumb/{rows['a.jpg'][0]}?g=1")
    assert r.status_code == 200 and r.mimetype == "image/jpeg"
    with Image.open(io.BytesIO(r.data)) as im:
        assert im.size == (64, 48)
    assert api.get(f"/api/files/thumb/{rows['notes.txt'][0]}").status_code == 404    # not an image
    assert api.get("/api/files/thumb/unknown").status_code == 404
    img = str(root / "Trips" / "Rome" / "a.jpg")
    assert api.get("/api/files/thumb/x", query_string={"path": img}).status_code == 404
    assert api.get("/api/files/thumb/" + img.replace("\\", "%5C")).status_code == 404
    assert api.get("/api/files/thumb/..%2F..%2Fa.jpg").status_code == 404
    # an id from before a root change no longer serves anything
    other = tmp_path / "Other"
    other.mkdir()
    scanned(app, api, [other])
    assert api.get(f"/api/files/thumb/{rows['a.jpg'][0]}").status_code == 404


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shell icons require Windows")
def test_windows_file_type_icons_are_valid_pngs_and_reject_paths(api):
    for extension in (".pdf", ".txt", ""):
        response = api.get("/api/files/icon", query_string={"ext": extension})
        assert response.status_code == 200 and response.mimetype == "image/png"
        with Image.open(io.BytesIO(response.data)) as image:
            assert image.size[0] > 0 and image.size[1] > 0
            assert image.convert("RGBA").getchannel("A").getbbox() is not None
    for extension in ("C:\\secret.txt", ".txt/other", ".x" + "a" * 255):
        assert api.get("/api/files/icon", query_string={"ext": extension}).status_code == 400


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shell icons require Windows")
def test_icon_lookup_uses_file_type_attributes_without_opening_a_file(monkeypatch):
    from win32com.shell import shell, shellcon
    from reframe.files.icons import file_type_icon

    calls = []
    original = shell.SHGetFileInfo

    def record(path, attrs, flags):
        calls.append((path, flags))
        return original(path, attrs, flags)

    monkeypatch.setattr(shell, "SHGetFileInfo", record)
    file_type_icon.cache_clear()
    try:
        file_type_icon(".txt")
        file_type_icon(".txt")
        assert len(calls) == 1
        assert calls[0][0] == "file.txt"
        assert calls[0][1] & shellcon.SHGFI_USEFILEATTRIBUTES
    finally:
        file_type_icon.cache_clear()


def test_windows_icon_failure_is_logged_and_returns_placeholder(api, monkeypatch, caplog):
    def fail(_extension):
        raise OSError("shell unavailable")

    monkeypatch.setattr(api_files, "file_type_icon", fail)
    response = api.get("/api/files/icon", query_string={"ext": ".txt"})
    assert response.get_json()["placeholder"] == "Windows icon unavailable"
    assert "shell unavailable" in caplog.text


def test_thumbnail_placeholder_for_bomb_and_corrupt(api, app, tmp_path):
    root = tmp_path / "R"
    root.mkdir()
    Image.new("1", (10000, 6000)).save(root / "bomb.png")
    (root / "corrupt.jpg").write_bytes(b"\xff\xd8\xff\xe0broken")
    scanned(app, api, [root])
    rows = {r[1]: r for r in entries(api)["rows"]}
    r = api.get(f"/api/files/thumb/{rows['bomb.png'][0]}")
    assert r.status_code == 200 and r.get_json() == {"placeholder": "too large", "retry": False}
    r = api.get(f"/api/files/thumb/{rows['corrupt.jpg'][0]}")
    assert r.get_json()["placeholder"] in ("corrupt", "unsupported")


def test_open_and_reveal_revalidate(api, app, tmp_path):
    root = tmp_path / "R"
    make_tree(root)
    scanned(app, api, [root])
    rows = {r[1]: r for r in entries(api)["rows"]}
    launcher = app.config["FILES"].launcher
    r = api.post(f"/api/files/open/{rows['top.txt'][0]}")
    assert r.status_code == 200 and launcher.calls == [("open", str(root / "top.txt"))]
    assert api.post(f"/api/files/reveal/{rows['top.txt'][0]}").status_code == 200
    (root / "top.txt").unlink()
    r = api.post(f"/api/files/open/{rows['top.txt'][0]}")
    assert r.status_code == 410 and "no longer exists" in r.get_json()["message"]
    assert api.post("/api/files/open/unknown").status_code == 404
    assert api.get(f"/api/files/open/{rows['notes.txt'][0]}").status_code == 405    # state change needs POST
    assert len(launcher.calls) == 2


def test_attach_indexed_file_revalidates_and_supports_undo(api, app, tmp_path):
    root = tmp_path / "R"
    make_tree(root)
    scanned(app, api, [root])
    file_id = next(row[0] for row in entries(api)["rows"] if row[1] == "top.txt")
    item_id = api.post("/api/items", {"title": "Task", "start_date": "2026-11-24"}).get_json()["id"]
    endpoint = f"/api/items/{item_id}/attachments"
    assert api.post(endpoint, {"file_entry_id": file_id, "target": "other"}).status_code == 400
    result = api.post(endpoint, {"file_entry_id": file_id})
    assert result.status_code == 201
    attachments = api.get(f"/api/items/{item_id}").get_json()["attachments"]
    assert len(attachments) == 1 and attachments[0]["target"] == str(root / "top.txt")
    assert api.post("/api/undo").get_json()["ok"]
    assert api.get(f"/api/items/{item_id}").get_json()["attachments"] == []
    (root / "top.txt").unlink()
    assert api.post(endpoint, {"file_entry_id": file_id}).status_code == 410
    assert api.post(endpoint, {"file_entry_id": "missing"}).status_code == 404
    assert api.get(f"/api/items/{item_id}").get_json()["attachments"] == []


def test_rescan_and_cancel_endpoints(api, app, tmp_path):
    root = tmp_path / "R"
    make_tree(root)
    scanned(app, api, [root])
    (root / "new.txt").write_text("n")
    st = api.post("/api/files/rescan").get_json()
    assert st["state"] in ("scanning", "idle")
    app.config["FILES"].wait_idle()
    assert "new.txt" in [r[1] for r in entries(api)["rows"]]
    assert api.post("/api/files/cancel").status_code == 200


def test_publishes_scan_events(api, app, tmp_path):
    q = app.config["HUB"].subscribe()
    root = tmp_path / "R"
    make_tree(root)
    scanned(app, api, [root])
    seen = []
    while not q.empty():
        et, data = q.get_nowait()
        if et == "files":
            seen.append((data["type"], data.get("state")))
    assert ("scan", "started") in seen and ("scan", "finished") in seen and ("options", None) in seen


def test_real_watcher_reports_changes(tmp_path):
    pytest.importorskip("win32file")
    hit = threading.Event()
    w = RootWatcher(str(tmp_path), 0, lambda i, reason: hit.set())
    w.start()
    try:
        time.sleep(0.3)
        (tmp_path / "new.txt").write_text("x")
        assert hit.wait(5)
    finally:
        w.stop()
        w.join(3)
    assert not w.is_alive()


def test_no_route_accepts_a_path(app):
    rules = [r for r in app.url_map.iter_rules() if r.rule.startswith("/api/files")]
    assert len(rules) >= 10
    for r in rules:
        assert r.arguments <= {"eid"}, r.rule
        assert "path" not in str(r._converters.get("eid", "")).lower(), r.rule
    for r in rules:
        if r.methods & {"PUT", "POST"}:
            assert "GET" not in r.methods or r.rule == "/api/files/options"
