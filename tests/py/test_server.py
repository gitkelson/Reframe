from pathlib import Path
import datetime as dt
import threading
import time

import pytest

from reframe.db.connection import connect
from reframe.outlook.adapter import CalendarInfo
from reframe.outlook.fake_adapter import FakeAdapter
from reframe.server import security
from reframe.server.app import create_app, start_watcher

PORT = 50123
TOKEN = "tok-for-tests"


@pytest.fixture
def fake():
    return FakeAdapter([CalendarInfo(id="cal1", name="Calendar", path="\\\\me\\Calendar", is_default=True)])


@pytest.fixture
def app(conn, db_path, fake):
    return create_app(db_path, token=TOKEN, port=PORT, adapter_factory=lambda c: fake)


@pytest.fixture
def api(app):
    c = app.test_client()

    class Api:
        base = f"http://127.0.0.1:{PORT}"
        headers = {security.TOKEN_HEADER: TOKEN}

        def req(self, method, path, json=None, **kw):
            h = {**self.headers, **kw.pop("headers", {})}
            r = getattr(c, method)(path, json=json, headers=h, base_url=self.base, **kw)
            return r

        def get(self, p, **kw): return self.req("get", p, **kw)
        def post(self, p, json=None, **kw): return self.req("post", p, json or {}, **kw)
        def patch(self, p, json=None, **kw): return self.req("patch", p, json or {}, **kw)
        def put(self, p, json=None, **kw): return self.req("put", p, json or {}, **kw)
        def delete(self, p, **kw): return self.req("delete", p, **kw)
    return Api()


# ------------------------------------------------------------------ security

def test_token_required(app):
    c = app.test_client()
    r = c.get("/api/bootstrap", base_url=f"http://127.0.0.1:{PORT}")
    assert r.status_code == 403
    r = c.get("/api/bootstrap", base_url=f"http://127.0.0.1:{PORT}", headers={security.TOKEN_HEADER: "wrong"})
    assert r.status_code == 403


def test_cookie_token_accepted_and_index_sets_it(app):
    c = app.test_client()
    base = f"http://127.0.0.1:{PORT}"
    assert c.get("/", base_url=base).status_code == 403
    r = c.get(f"/?t={TOKEN}", base_url=base)
    assert r.status_code == 302 and security.TOKEN_COOKIE in r.headers["Set-Cookie"]
    assert "HttpOnly" in r.headers["Set-Cookie"] and "SameSite=Strict" in r.headers["Set-Cookie"]
    assert c.get("/api/bootstrap", base_url=base).status_code == 200   # cookie jar carries the token
    assert c.get("/", base_url=base).status_code == 200


def test_wrong_host_and_origin_rejected(app, api):
    c = app.test_client()
    h = {security.TOKEN_HEADER: TOKEN}
    assert c.get("/api/bootstrap", headers=h, base_url=f"http://evil.example:{PORT}").status_code == 403
    assert c.get("/api/bootstrap", headers=h, base_url="http://127.0.0.1:9999").status_code == 403
    assert c.get("/api/bootstrap", headers=h, base_url="http://localhost:%d" % PORT).status_code == 403
    r = c.get("/api/bootstrap", headers={**h, "Origin": "http://evil.example"}, base_url=api.base)
    assert r.status_code == 403
    r = c.get("/api/bootstrap", headers={**h, "Origin": api.base}, base_url=api.base)
    assert r.status_code == 200


def test_csp_and_hardening_headers_and_no_cors(api):
    r = api.get("/api/bootstrap")
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp and "script-src 'self'" in csp and "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp and "http" not in csp
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "Access-Control-Allow-Origin" not in r.headers


def test_writes_require_json_body(app):
    c = app.test_client()
    r = c.post("/api/items", data="title=x", headers={security.TOKEN_HEADER: TOKEN,
               "Content-Type": "application/x-www-form-urlencoded"}, base_url=f"http://127.0.0.1:{PORT}")
    assert r.status_code == 415


def test_no_raw_sql_surface(app):
    rules = [r.rule for r in app.url_map.iter_rules()]
    assert not any("sql" in r.lower() or "query" in r.lower() for r in rules)


def test_html_in_title_returned_as_plain_json_text(api):
    r = api.post("/api/items", {"title": "<img src=x onerror=alert(1)>", "start_date": "2026-12-01"})
    assert r.status_code == 201 and r.mimetype == "application/json"
    items = api.get("/api/items").get_json()["items"]
    assert items[0]["title"] == "<img src=x onerror=alert(1)>"


def test_static_js_served_with_javascript_mime(app, tmp_path):
    from reframe.server.app import WEB_DIR
    p = WEB_DIR / "js" / "_probe.js"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("export const x = 1;", encoding="utf-8")
    try:
        r = app.test_client().get("/static/js/_probe.js", base_url=f"http://127.0.0.1:{PORT}")
        assert r.status_code == 200 and "javascript" in r.mimetype
        r.close()
    finally:
        p.unlink()


# ------------------------------------------------------------------- items

def test_item_crud_flow(api):
    r = api.post("/api/items", {"title": "Delivery", "start_date": "20261120", "tags": ["Spares"]})
    assert r.status_code == 201
    iid = r.get_json()["id"]
    r = api.patch(f"/api/items/{iid}", {"end_date": "2026-11-22"})
    assert r.get_json()["changed"] == ["end_date"]
    r = api.patch(f"/api/items/{iid}", {"end_date": "2026-11-10"})
    assert r.status_code == 400 and r.get_json()["error"] == "date"
    lst = api.get("/api/items").get_json()
    assert lst["items"][0]["end_date"] == "2026-11-22"
    tagid = lst["items"][0]["tags"][0]
    assert lst["tags"][tagid]["name"] == "Spares"
    assert api.delete(f"/api/items/{iid}").get_json()["deleted"] == 1
    assert api.get("/api/items").get_json()["items"] == []
    api.post(f"/api/items/{iid}/restore")
    assert len(api.get("/api/items").get_json()["items"]) == 1


def test_bad_date_message(api):
    r = api.post("/api/items", {"title": "x", "start_date": "11/08/2026"})
    assert r.status_code == 400 and "2026-11-08" in r.get_json()["message"]


def test_every_write_is_one_user_change_set_with_label(api, conn):
    api.post("/api/items", {"title": "Delivery", "start_date": "2026-11-20"})
    row = conn.execute("SELECT actor, label FROM change_set").fetchone()
    assert tuple(row) == ("user", "Add 'Delivery'")


def test_bulk_move_is_single_undo_and_reports_skipped(api, conn):
    ids = [api.post("/api/items", {"title": f"I{i}", "start_date": "2026-11-20"}).get_json()["id"] for i in range(3)]
    with conn:
        pass
    from reframe.db.changelog import writer
    with writer(conn, "sync", "imp"):
        conn.execute("INSERT INTO item (id,title,start_date,end_date,origin, source_id,outlook_uid) VALUES "
                     "('o','Imp','2026-11-20','2026-11-20','outlook', 'testcal','u')")
    r = api.post("/api/items/bulk", {"ids": ids + ["o"], "action": "move", "days": 2}).get_json()
    assert r == {"moved": 3, "skipped": 1}
    assert api.post("/api/undo").get_json()["ok"]
    starts = {i["start_date"] for i in api.get("/api/items").get_json()["items"]}
    assert starts == {"2026-11-20"}


def test_imported_item_core_edit_rejected_with_hint(api, conn):
    from reframe.db.changelog import writer
    with writer(conn, "sync", "imp"):
        conn.execute("INSERT INTO item (id,title,start_date,end_date,origin, source_id,outlook_uid) VALUES "
                     "('o','Imp','2026-11-20','2026-11-20','outlook', 'testcal','u')")
    r = api.patch("/api/items/o", {"title": "x"})
    assert r.status_code == 409 and r.get_json()["message"] == "Imported from Outlook. Change it there, then Sync."
    assert api.patch("/api/items/o", {"notes": "ok"}).status_code == 200


def test_tags_api_suggest_merge_color(api):
    a = api.post("/api/items", {"title": "A", "start_date": "2026-11-20", "tags": ["Spares Order"]}).get_json()
    api.post("/api/items", {"title": "B", "start_date": "2026-11-21", "tags": ["Other"]})
    s = api.get("/api/tags/suggest?q=Spares%20Ordr").get_json()
    assert [t["name"] for t in s["near"]] == ["Spares Order"]
    tags = {t["name"]: t["id"] for t in api.get("/api/tags").get_json()["tags"]}
    assert api.patch(f"/api/tags/{tags['Other']}", {"color_slot": 11}).status_code == 200
    assert api.patch(f"/api/tags/{tags['Other']}", {"color_slot": 99}).status_code == 400
    r = api.post(f"/api/tags/{tags['Other']}/merge", {"into": tags["Spares Order"]})
    assert r.get_json()["moved"] == 1
    assert api.delete(f"/api/tags/{tags['Spares Order']}").status_code == 400    # in use
    assert api.delete(f"/api/tags/{tags['Spares Order']}?force=1").status_code == 200


def test_history_revert_and_conflict(api):
    iid = api.post("/api/items", {"title": "A", "start_date": "2026-11-20"}).get_json()["id"]
    first = api.get("/api/history").get_json()["history"][0]["id"]
    api.patch(f"/api/items/{iid}", {"title": "B"})
    r = api.post(f"/api/history/{first}/revert")
    assert r.status_code == 409 and r.get_json()["conflicts"]
    st = api.get("/api/undo-state").get_json()
    assert st["undo"]["label"].startswith("Edit") and st["redo"] is None


def test_undo_nothing(api):
    assert api.post("/api/undo").get_json()["ok"] is False


def test_settings_roundtrip_and_unknown_key(api):
    assert api.put("/api/settings", {"theme": "spotify", "layout_mode": "strict"}).status_code == 200
    s = api.get("/api/settings").get_json()
    assert s["theme"] == "spotify" and s["layout_mode"] == "strict"
    assert api.put("/api/settings", {"bogus": 1}).status_code == 400
    assert api.put("/api/settings", {"hide_past": True}).status_code == 400    # removed (R-05)
    assert "hide_past" not in s


def test_attachments_api(api, tmp_path):
    iid = api.post("/api/items", {"title": "A", "start_date": "2026-11-20"}).get_json()["id"]
    real = tmp_path / "f.txt"
    real.write_text("x")
    a1 = api.post(f"/api/items/{iid}/attachments", {"target": str(real)}).get_json()["id"]
    a2 = api.post(f"/api/items/{iid}/attachments", {"target": str(tmp_path / "gone.txt")}).get_json()["id"]
    a3 = api.post(f"/api/items/{iid}/attachments", {"target": "https://example.com"}).get_json()["id"]
    res = api.post("/api/attachments/check", {}).get_json()
    assert res == {a1: "ok", a2: "broken", a3: "unchecked"}
    assert api.post(f"/api/attachments/{a2}/open").status_code == 404
    assert api.delete(f"/api/attachments/{a1}").status_code == 200


def test_bootstrap(api):
    b = api.get("/api/bootstrap").get_json()
    assert b["today"] == dt.date.today().isoformat() and b["settings"]["theme"] == "meridian"
    assert b["has_host"] is False and "reframe.sqlite" in b["db_path"]


def test_host_bridge_unavailable_without_window(api):
    assert api.post("/api/host/choose-folder").status_code == 501


def test_bulk_edge_shift_is_one_change_set_skips_imported(api, conn):
    from reframe.db.changelog import writer
    ids = [api.post("/api/items", {"title": f"I{i}", "start_date": "2026-11-20", "end_date": "2026-11-22"}).get_json()["id"]
           for i in range(3)]
    with writer(conn, "sync", "imp"):
        conn.execute("INSERT INTO item (id,title,start_date,end_date,origin, source_id,outlook_uid) VALUES "
                     "('o','Imp','2026-11-20','2026-11-20','outlook', 'testcal','u')")
    before = conn.execute("SELECT count(*) FROM change_set").fetchone()[0]
    r = api.post("/api/items/bulk", {"ids": ids + ["o"], "action": "shift_end", "days": 2}).get_json()
    assert r == {"changed": 3, "skipped": 1}
    assert conn.execute("SELECT count(*) FROM change_set").fetchone()[0] == before + 1
    ends = {i["end_date"] for i in api.get("/api/items").get_json()["items"] if i["origin"] == "local"}
    assert ends == {"2026-11-24"}
    # an item never shrinks below one day
    api.post("/api/items/bulk", {"ids": ids, "action": "shift_end", "days": -30})
    assert {i["end_date"] for i in api.get("/api/items").get_json()["items"] if i["origin"] == "local"} == {"2026-11-20"}
    api.post("/api/items/bulk", {"ids": ids, "action": "shift_start", "days": 5})
    assert {i["start_date"] for i in api.get("/api/items").get_json()["items"] if i["origin"] == "local"} == {"2026-11-20"}


def test_bare_path_url_attachment_is_never_opened(api, conn):
    iid = api.post("/api/items", {"title": "A", "start_date": "2026-11-20"}).get_json()["id"]
    conn.execute("INSERT INTO attachment (id,item_id,kind,target) VALUES ('bad',?, 'url', 'C:\\\\Windows\\\\notepad.exe')", (iid,))
    r = api.post("/api/attachments/bad/open")
    assert r.status_code == 400


def test_hourly_backup_runs_after_user_write_once(api, tmp_path):
    from reframe.domain import backup
    folder = tmp_path / "hourly"
    api.put("/api/settings", {"backup_folder": str(folder)})
    api.post("/api/items", {"title": "A", "start_date": "2026-11-20"})
    api.post("/api/items", {"title": "B", "start_date": "2026-11-21"})
    assert len(backup.list_backups(folder)) == 1


def test_duplicate_endpoint(api, conn):
    src = api.post("/api/items", {"title": "Source", "start_date": "2026-11-20", "end_date": "2026-11-22",
                                  "tags": ["Spares"], "notes": "n"}).get_json()["id"]
    before = conn.execute("SELECT count(*) FROM change_set").fetchone()[0]
    r = api.post(f"/api/items/{src}/duplicate", {"start_date": "2026-12-01"})
    assert r.status_code == 201
    body = r.get_json()
    assert body["item"]["title"] == "New item" and body["item"]["end_date"] == "2026-12-03"
    assert [t["name"] for t in body["item"]["tags"]] == ["Spares"]
    assert conn.execute("SELECT count(*) FROM change_set").fetchone()[0] == before + 1
    assert conn.execute("SELECT label FROM change_set ORDER BY id DESC LIMIT 1").fetchone()[0] == "Duplicate 'Source'"
    assert api.post(f"/api/items/{src}/duplicate", {"start_date": "soon"}).status_code == 400
    assert api.post("/api/items/nope/duplicate", {"start_date": "2026-12-01"}).status_code == 404
    assert api.post("/api/undo").get_json()["ok"]
    assert len(api.get("/api/items").get_json()["items"]) == 1


def test_copy_tags_bulk_endpoint_one_change_set_and_label(api, conn):
    src = api.post("/api/items", {"title": "Source", "start_date": "2026-11-20", "tags": ["Spares", "PRJ-20"]}).get_json()["id"]
    tgt = api.post("/api/items", {"title": "New item", "start_date": "2026-11-22"}).get_json()["id"]
    before = conn.execute("SELECT count(*) FROM change_set").fetchone()[0]
    r = api.post("/api/items/bulk", {"ids": [tgt], "action": "copy_tags", "source_id": src})
    assert r.get_json() == {"changed": 1, "skipped": 0, "tag_count": 2}
    assert conn.execute("SELECT count(*) FROM change_set").fetchone()[0] == before + 1
    assert conn.execute("SELECT label FROM change_set ORDER BY id DESC LIMIT 1").fetchone()[0] == \
        "Copy tags from 'Source' to 'New item'"
    assert [t["name"] for t in api.get(f"/api/items/{tgt}").get_json()["tags"]] == ["Spares", "PRJ-20"]
    assert api.post("/api/undo").get_json()["ok"]
    assert api.get(f"/api/items/{tgt}").get_json()["tags"] == []


def test_copy_tags_bulk_endpoint_errors(api):
    tgt = api.post("/api/items", {"title": "T", "start_date": "2026-11-22"}).get_json()["id"]
    plain = api.post("/api/items", {"title": "No tags", "start_date": "2026-11-22"}).get_json()["id"]
    assert api.post("/api/items/bulk", {"ids": [tgt], "action": "copy_tags", "source_id": "nope"}).status_code == 404
    r = api.post("/api/items/bulk", {"ids": [tgt], "action": "copy_tags", "source_id": plain})
    assert r.status_code == 400 and "no tags" in r.get_json()["message"]


def test_new_install_defaults_group_by_tag_on_and_tray_on(api):
    s = api.get("/api/settings").get_json()
    assert s["group_by_tag"] is True and s["tray_on_minimize"] is True


def test_calendar_names_and_identical_entries_for_imported_items(api, conn):
    from reframe.db.changelog import writer
    from conftest import add_test_source
    add_test_source(conn, "hol", "CAL-H")
    conn.execute("UPDATE calendar_source SET name='United States holidays', location=? WHERE id='hol'",
                 ("\\\\me\\Calendar\\United States holidays",))
    with writer(conn, "sync", "imp"):
        for n in range(2):   # Outlook can hold the same holiday twice (different entry ids)
            conn.execute("INSERT INTO item (id,title,start_date,end_date,origin, source_id,outlook_uid,outlook_calendar) VALUES "
                         "(?, 'Labor Day','2026-09-07','2026-09-07','outlook', 'hol',?, 'CAL-H')", (f"h{n}", f"CAL-H|entry:{n}"))
        conn.execute("INSERT INTO item (id,title,start_date,end_date,origin, source_id,outlook_uid,outlook_calendar) VALUES "
                     "('x','Labor Day','2026-09-07','2026-09-07','outlook', 'testcal','OTHER|1','OTHER')")
    lst = api.get("/api/items").get_json()
    assert lst["calendars"]["hol"]["name"] == "United States holidays"
    assert lst["calendars"]["hol"]["location"] == "\\\\me\\Calendar\\United States holidays"
    assert api.get("/api/items/h0").get_json()["source"]["name"] == "United States holidays"
    assert api.get("/api/items/h0").get_json()["identical_in_calendar"] == 1   # 'x' is in another calendar
    assert api.get("/api/items/x").get_json()["identical_in_calendar"] == 0
    api.post("/api/items", {"title": "Local", "start_date": "2026-09-07"})
    local = [i for i in api.get("/api/items").get_json()["items"] if i["origin"] == "local"][0]
    assert api.get(f"/api/items/{local['id']}").get_json()["identical_in_calendar"] == 0


def test_removed_by_sync_listed_with_origin(api, conn):
    from reframe.db.changelog import writer
    with writer(conn, "sync", "imp"):
        conn.execute("INSERT INTO item (id,title,start_date,end_date,origin, source_id,outlook_uid,deleted_at) VALUES "
                     "('o','Gone','2026-11-20','2026-11-20','outlook', 'testcal','u','2026-11-21T00:00:00Z')")
    removed = api.get("/api/history/hidden-removed").get_json()["removed"]
    assert removed[0]["origin"] == "outlook"


# ----------------------------------------------------------- backup & sync

def test_backup_endpoints(api, tmp_path):
    api.put("/api/settings", {"backup_folder": str(tmp_path / "bk")})
    r = api.post("/api/backup").get_json()
    assert Path(r["path"]).name.startswith("reframe-")   # a coordinated backup set (R-167)
    lst = api.get("/api/backups").get_json()
    assert len(lst["backups"]) == 1
    api.post("/api/items", {"title": "A", "start_date": "2026-11-20"})
    assert api.post("/api/backups/restore", {"name": lst["backups"][0]["name"]}).get_json()["ok"]
    assert api.get("/api/items").get_json()["items"] == []
    assert api.post("/api/backups/restore", {"name": "..\\..\\evil.sqlite"}).status_code == 404


def test_outlook_calendar_picker_and_sync_flow(api, fake, app, conn):
    cals = api.post("/api/calendars/refresh-outlook").get_json()
    mine = [c for c in cals["sources"] if c["external_id"] == "cal1"][0]
    assert cals["available"] and mine["location"] == "\\\\me\\Calendar" and "Calendar" in cals["empty"]
    assert mine["connected"] and not mine["visible"]                          # connected, viewed on demand (R-131)
    fake.add(gaid="A", subject="Staff", start=dt.datetime(2026, 11, 12, 9), end=dt.datetime(2026, 11, 12, 10))
    assert api.post("/api/sync").status_code == 202
    app.config["SYNC"].join(10)
    st = api.get("/api/sync/status").get_json()
    assert st["running"] is False and st["last_result"]["added"] == 1 and st["error"] is None
    assert "Outlook Calendar: 1 added" in st["last_result"]["message"]
    assert api.get("/api/items").get_json()["items"][0]["origin"] == "outlook"
    assert api.post("/api/undo").get_json()["undone_label"].startswith("Outlook sync")
    # disconnect: one undoable change set; visibility is a view setting
    api.post("/api/sync")
    app.config["SYNC"].join(10)
    r = api.post(f"/api/calendars/{mine['id']}/connect", {"connected": False}).get_json()
    assert r["removed"] == 1 and api.get("/api/items").get_json()["items"] == []
    assert api.patch(f"/api/calendars/{mine['id']}", {"visible": True}).status_code == 200
    assert api.post("/api/calendars/ics", {"location": "http://x.example/c.ics"}).get_json()["error"] == "http_warning"


def test_sync_when_outlook_unavailable_reports_reason(api, fake, app):
    fake.unavailable = "Outlook is not running"
    api.post("/api/sync")
    app.config["SYNC"].join(10)
    st = api.get("/api/sync/status").get_json()
    assert st["error"] == "Outlook is not running" and not st["running"]
    assert api.get("/api/items").status_code == 200   # app keeps working


# ------------------------------------------------------- live refresh (R-85)

def test_watcher_pushes_event_for_external_change_within_2s(app, db_path):
    hub = app.config["HUB"]
    q = hub.subscribe()
    w = start_watcher(app, 0.2)
    try:
        time.sleep(0.4)
        c = connect(db_path)
        t0 = time.time()
        c.execute("INSERT INTO item (id,title,start_date,end_date) VALUES ('x','Agent wrote','2026-11-20','2026-11-20')")
        et, data = q.get(timeout=2)
        assert et == "data" and time.time() - t0 < 2
        assert data["external"] is True and data["item_ids"] == ["x"]
        assert c.execute("SELECT actor FROM change_set").fetchone()[0] == "external"
        c.close()
    finally:
        w.stop()


def test_sse_stream_format(app):
    hub = app.config["HUB"]
    stop = threading.Event()
    gen = hub.stream(keepalive_s=0.1, stop=stop)
    assert next(gen).startswith("retry:")
    hub.publish("data", {"external": False})
    msg = next(gen)
    assert "event: data" in msg
    msg = next(gen)
    assert msg.startswith(": keepalive") or "event: data" in msg
    stop.set()
    gen.close()
