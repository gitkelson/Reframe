"""Everything adapter (spec R-120 - R-128) with a fake SDK, plus one real-DLL check when available."""
import threading
import time
from pathlib import Path

import pytest

from reframe.search import everything as ev
from reframe.search.everything import EverythingService, looks_like_path
from reframe.server import api_search, security
from reframe.server.app import create_app

PORT = 50124
TOKEN = "tok-everything"
APP_ROOT = Path(__file__).resolve().parents[2]

FT_2026 = 134_090_000_000_000_000  # a FILETIME in 2025-2026


class FakeSdk:
    """Mimics the Everything64.dll functions the service calls (same names, Python values)."""

    def __init__(self, files=None):
        self.files = list(files or [])  # dicts: name, folder, size (None = unknown), mtime (FILETIME or None)
        self.running = True
        self.db_loaded = True
        self.query_error = None
        self.block = None  # threading.Event to hold QueryW
        self.calls = []
        self._reset()

    def _reset(self):
        self.search, self.max, self.offset, self.sort, self.flags = "", 0xFFFFFFFF, 0, 1, 3
        self.err, self.page, self.total = 0, [], 0

    def Everything_Reset(self): self._reset(); self.calls.append("reset")
    def Everything_SetSearchW(self, s): self.search = s
    def Everything_SetMatchPath(self, v): pass
    def Everything_SetMatchCase(self, v): pass
    def Everything_SetMatchWholeWord(self, v): pass
    def Everything_SetRegex(self, v): pass
    def Everything_SetMax(self, n): self.max = n
    def Everything_SetOffset(self, n): self.offset = n
    def Everything_SetSort(self, s): self.sort = s
    def Everything_SetRequestFlags(self, f): self.flags = f
    def Everything_GetLastError(self): return self.err

    def Everything_IsDBLoaded(self):
        if not self.running:
            self.err = ev.EVERYTHING_ERROR_IPC
            return False
        return self.db_loaded

    def Everything_QueryW(self, wait):
        assert wait
        self.calls.append(("query", self.search))
        if self.block is not None:
            self.block.wait(5)
        if not self.running:
            self.err = ev.EVERYTHING_ERROR_IPC
            return False
        if self.query_error:
            self.err = self.query_error
            return False
        terms = self.search.lower().split()
        hits = [f for f in self.files if all(t in f["name"].lower() for t in terms)]
        if self.sort == ev.EVERYTHING_SORT_NAME_ASCENDING:
            hits.sort(key=lambda f: f["name"].lower())
        else:
            hits.sort(key=lambda f: f.get("mtime") or 0, reverse=True)
        self.total = len(hits)
        self.page = hits[self.offset:self.offset + self.max]
        return True

    def Everything_GetNumResults(self): return len(self.page)
    def Everything_GetTotResults(self): return self.total
    def Everything_GetResultFileNameW(self, i): return self.page[i]["name"]
    def Everything_GetResultPathW(self, i): return self.page[i]["folder"]

    def Everything_GetResultFullPathNameW(self, i, buf, size):
        full = self.page[i]["folder"] + "\\" + self.page[i]["name"]
        if buf is None:
            return len(full)
        assert size == len(full) + 1, "length-query-then-allocate"
        buf.value = full
        return len(full)

    def Everything_GetResultSize(self, i, ref):
        s = self.page[i].get("size")
        if s == "fail":
            return False
        ref._obj.value = -1 if s is None else s
        return True

    def Everything_GetResultDateModified(self, i, ref):
        m = self.page[i].get("mtime")
        ref._obj.value = 0xFFFFFFFFFFFFFFFF if m is None else m
        return True

    def Everything_GetMajorVersion(self):
        if not self.running:
            self.err = ev.EVERYTHING_ERROR_IPC
            return 0
        return 1

    def Everything_GetMinorVersion(self): return 4
    def Everything_GetRevision(self): return 1
    def Everything_GetBuildNumber(self): return 1032


def f(name, folder=r"C:\Data", size=100, mtime=FT_2026):
    return {"name": name, "folder": folder, "size": size, "mtime": mtime}


@pytest.fixture
def fake():
    return FakeSdk([
        f("Order, \"rev B\" 1234.xlsx", r"C:\Projects\PRJ 20\Spares", 2048, FT_2026 + 10),
        f("Zeichnung Ä-100 – Ω.pdf", r"\\server\share\drawings", 0, FT_2026 + 30),
        f("notes order.txt", r"C:\Data", None, None),
        f("alpha order.md", r"C:\Data", "fail", FT_2026 + 20),
    ])


@pytest.fixture
def svc(fake):
    s = EverythingService(sdk=fake, timeout_s=2)
    yield s
    s.stop()


# ------------------------------------------------------------------ results

def test_results_copied_with_unicode_comma_quote_space(svc):
    r = svc.search("order", request_id="a1")
    assert r["state"] == "ok" and r["request_id"] == "a1" and r["total"] == 3
    names = [x["name"] for x in r["results"]]
    assert names == ["alpha order.md", "Order, \"rev B\" 1234.xlsx", "notes order.txt"]  # modified desc
    first = r["results"][1]
    assert first["path"] == r'C:\Projects\PRJ 20\Spares\Order, "rev B" 1234.xlsx'
    assert first["folder"] == r"C:\Projects\PRJ 20\Spares" and first["size"] == 2048
    assert first["modified"].startswith("202")
    u = svc.search("Ω", request_id=2)["results"][0]
    assert u["path"] == "\\\\server\\share\\drawings\\Zeichnung Ä-100 – Ω.pdf"
    assert u["size"] == 0  # a real zero-byte file stays 0


def test_unknown_size_and_date_are_none(svc):
    res = {x["name"]: x for x in svc.search("order", request_id=1)["results"]}
    assert res["notes order.txt"]["size"] is None and res["notes order.txt"]["modified"] is None
    assert res["alpha order.md"]["size"] is None  # getter failed
    assert ev.filetime_to_iso(0) is None and ev.filetime_to_iso(0xFFFFFFFFFFFFFFFF) is None


def test_paging_total_and_sort(svc, fake):
    fake.files = [f(f"file {i:03d}.txt", mtime=FT_2026 + i) for i in range(250)]
    p1 = svc.search("file", limit=100, request_id=1)
    assert p1["total"] == 250 and len(p1["results"]) == 100 and p1["results"][0]["name"] == "file 249.txt"
    assert fake.max == 100 and fake.offset == 0 and fake.sort == ev.EVERYTHING_SORT_DATE_MODIFIED_DESCENDING
    p3 = svc.search("file", offset=200, limit=100, request_id=2)
    assert len(p3["results"]) == 50 and p3["offset"] == 200 and p3["total"] == 250
    byname = svc.search("file", sort="name", limit=3, request_id=3)
    assert fake.sort == ev.EVERYTHING_SORT_NAME_ASCENDING
    assert [x["name"] for x in byname["results"]] == ["file 000.txt", "file 001.txt", "file 002.txt"]
    assert fake.flags & ev.EVERYTHING_REQUEST_SIZE and fake.flags & ev.EVERYTHING_REQUEST_DATE_MODIFIED
    with pytest.raises(ValueError):
        svc.search("file", sort="size", request_id=4)


# ------------------------------------------------------------------ failure states

def test_failure_states_distinct_from_no_matches(fake, tmp_path):
    s = EverythingService(sdk=fake, timeout_s=0.3)
    try:
        assert s.search("nothing-like-this", request_id=1)["state"] == "no_matches"
        fake.running = False
        assert s.search("order", request_id=2)["state"] == "not_running"
        fake.running, fake.db_loaded = True, False
        assert s.search("order", request_id=3)["state"] == "indexing"
        fake.db_loaded, fake.query_error = True, ev.EVERYTHING_ERROR_CREATEWINDOW
        r = s.search("order", request_id=4)
        assert r["state"] == "error" and r["error_code"] == 4
        fake.query_error, fake.block = None, threading.Event()
        assert s.search("order", request_id=5)["state"] == "timeout"
        fake.block.set()
        fake.block = None
    finally:
        s.stop()
    missing = EverythingService(dll_path=tmp_path / "nope" / "Everything64.dll")
    try:
        r = missing.search("x", request_id=1)
        assert r["state"] == "missing_sdk" and r["results"] == []
        st = missing.status()
        assert st["sdk_present"] is False and st["running"] is False
    finally:
        missing.stop()
    states = {"ok", "no_matches", "not_running", "indexing", "timeout", "missing_sdk", "error"}
    assert all(k in ev.MESSAGES for k in states)


def test_superseded_request_not_queried(fake):
    s = EverythingService(sdk=fake, timeout_s=3)
    try:
        fake.block = threading.Event()
        out = {}
        t1 = threading.Thread(target=lambda: out.setdefault(1, s.search("order", request_id=1)))
        t1.start()
        time.sleep(0.2)  # request 1 is inside QueryW
        t2 = threading.Thread(target=lambda: out.setdefault(2, s.search("alpha", request_id=2)))
        t2.start()
        time.sleep(0.1)
        t3 = threading.Thread(target=lambda: out.setdefault(3, s.search("notes", request_id=3)))
        t3.start()
        time.sleep(0.1)
        fake.block.set()
        for t in (t1, t2, t3):
            t.join(5)
        assert out[2]["state"] == "superseded" and out[2]["results"] == []
        assert out[3]["state"] == "ok" and out[3]["results"][0]["name"] == "notes order.txt"
        assert ("query", "alpha") not in fake.calls
        # a different client key is independent
        assert s.search("order", request_id=1, client="other")["state"] == "ok"
    finally:
        s.stop()


def test_recovers_after_everything_restart(svc, fake):
    assert svc.search("order", request_id=1)["state"] == "ok"
    fake.running = False
    assert svc.search("order", request_id=2)["state"] == "not_running"
    assert svc.status()["running"] is False
    fake.running = True
    assert svc.search("order", request_id=3)["state"] == "ok"
    st = svc.status()
    assert st["running"] and st["everything_version"] == "1.4.1.1032" and st["index_ready"]


def test_start_everything_uses_installed_exe_only(fake, monkeypatch, tmp_path):
    launched = []
    s = EverythingService(sdk=fake, launcher=lambda args, **kw: launched.append(args))
    try:
        assert s.start_everything() is True and launched == []  # already running
        fake.running = False
        exe = tmp_path / "Everything.exe"
        exe.write_bytes(b"")
        monkeypatch.setattr(ev, "EXE_CANDIDATES", (tmp_path / "missing.exe", exe))
        assert s.start_everything() is True and launched == [[str(exe), "-startup"]]
        monkeypatch.setattr(ev, "EXE_CANDIDATES", (tmp_path / "missing.exe",))
        assert s.start_everything() is False
    finally:
        s.stop()


@pytest.mark.parametrize("text,expected", [
    (r"C:\Projects\a.pdf", True), (r"c:\x", True), (r"\\server\share\x", True),
    ("http://x.org", True), ("HTTPS://x.org/a b", True), ("webcal://cal/x.ics", True), ("file:///C:/x", True),
    ('"order 1234"', True), ("'quoted'", True), ("  C:\\lead space", True),
    ("order 1234", False), ("ext:pdf invoice", False), ("C:", False), ("C:/x", False), ("", False),
    ('"unbalanced', False), ("www.example.com", False), ("\\single", False),
])
def test_looks_like_path(text, expected):
    assert looks_like_path(text) is expected


# ------------------------------------------------------------------ Flask API

@pytest.fixture
def client(conn, db_path, fake):
    app = create_app(db_path, token=TOKEN, port=PORT)
    app.config["EVERYTHING_FACTORY"] = lambda: EverythingService(sdk=fake, timeout_s=2,
                                                                  launcher=lambda *a, **k: None)
    api_search.init_app(app)
    c = app.test_client()
    yield app, c
    svc = app.config.get("EVERYTHING")
    if svc:
        svc.stop()


def _get(c, path):
    return c.get(path, headers={security.TOKEN_HEADER: TOKEN}, base_url=f"http://127.0.0.1:{PORT}")


def test_api_search_files(client):
    app, c = client
    r = _get(c, "/api/search/files?q=order&limit=2&sort=name&rid=7")
    assert r.status_code == 200
    d = r.get_json()
    assert d["state"] == "ok" and d["request_id"] == "7" and d["total"] == 3 and len(d["results"]) == 2
    assert d["results"][0]["name"] == "alpha order.md"
    assert set(d["results"][0]) == {"name", "folder", "path", "size", "modified"}
    assert _get(c, "/api/search/files?q=zzz&rid=8").get_json()["state"] == "no_matches"
    assert _get(c, "/api/search/files?q=order&limit=x").status_code == 400
    assert c.get("/api/search/files?q=order", base_url=f"http://127.0.0.1:{PORT}").status_code == 403


def test_api_status_and_start(client, fake):
    app, c = client
    st = _get(c, "/api/search/status").get_json()
    assert st["enabled"] is True and st["running"] is True and st["everything_version"] == "1.4.1.1032"
    fake.running = False
    r = c.post("/api/search/start-everything", json={}, headers={security.TOKEN_HEADER: TOKEN},
               base_url=f"http://127.0.0.1:{PORT}")
    assert r.status_code == 200 and "started" in r.get_json()


def test_api_disabled_setting(client, conn, fake):
    from reframe.domain import settings
    app, c = client
    settings.set_many(conn, {"everything_enabled": False})
    d = _get(c, "/api/search/files?q=order&rid=1").get_json()
    assert d["state"] == "disabled" and d["results"] == []
    assert ("query", "order") not in fake.calls
    assert _get(c, "/api/search/status").get_json()["enabled"] is False


# ------------------------------------------------------------------ real Everything (optional)

def test_real_everything_finds_spec():
    if not ev.DEFAULT_DLL.is_file():
        pytest.skip("Everything SDK DLL not vendored")
    s = EverythingService()
    try:
        if not s.status()["running"]:
            pytest.skip("Everything is not running")
        r = s.search("SPEC.md", request_id=1)
        assert r["state"] == "ok", r
        paths = {Path(x["path"]).resolve() for x in r["results"]}
        assert (APP_ROOT / "SPEC.md").resolve() in paths
        hit = next(x for x in r["results"] if Path(x["path"]).resolve() == (APP_ROOT / "SPEC.md").resolve())
        assert hit["size"] and hit["size"] > 0 and hit["modified"]
    finally:
        s.stop()
