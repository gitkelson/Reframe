"""Performance targets of spec 4.2 that the backend can check: 10,000 items."""
import time

import pytest

from reframe.seed import seed
from reframe.server.app import create_app

PORT = 50124
TOKEN = "perf"


@pytest.fixture(scope="module")
def big_db(tmp_path_factory):
    p = tmp_path_factory.mktemp("perf") / "big.sqlite"
    t0 = time.perf_counter()
    seed(p, 5000, recurring=50)          # 5,000 local + 5,000 imported weekly occurrences
    return p, time.perf_counter() - t0


def test_seed_has_10000_items(big_db):
    from reframe.db.connection import connect
    c = connect(big_db[0])
    assert c.execute("SELECT count(*) FROM item").fetchone()[0] == 10000
    assert c.execute("SELECT count(*) FROM change WHERE change_set_id IS NULL").fetchone()[0] == 0


def test_items_endpoint_under_budget(big_db):
    app = create_app(big_db[0], token=TOKEN, port=PORT)
    c = app.test_client()
    h = {"X-Reframe-Token": TOKEN}
    base = f"http://127.0.0.1:{PORT}"
    t0 = time.perf_counter()
    r = c.get("/api/items", headers=h, base_url=base)
    dt = time.perf_counter() - t0
    assert r.status_code == 200 and len(r.get_json()["items"]) == 10000
    assert dt < 1.0, f"/api/items took {dt:.2f}s for 10,000 items"


def test_bootstrap_with_many_items_is_fast(big_db):
    app = create_app(big_db[0], token=TOKEN, port=PORT)
    c = app.test_client()
    t0 = time.perf_counter()
    r = c.get("/api/bootstrap", headers={"X-Reframe-Token": TOKEN}, base_url=f"http://127.0.0.1:{PORT}")
    assert r.status_code == 200 and time.perf_counter() - t0 < 1.0


def test_single_write_stays_fast_with_large_log(big_db):
    app = create_app(big_db[0], token=TOKEN, port=PORT)
    c = app.test_client()
    t0 = time.perf_counter()
    r = c.post("/api/items", json={"title": "x", "start_date": "2026-12-01"},
               headers={"X-Reframe-Token": TOKEN}, base_url=f"http://127.0.0.1:{PORT}")
    assert r.status_code == 201 and time.perf_counter() - t0 < 0.5


def test_undo_state_with_large_log(big_db):
    app = create_app(big_db[0], token=TOKEN, port=PORT)
    c = app.test_client()
    t0 = time.perf_counter()
    r = c.get("/api/undo-state", headers={"X-Reframe-Token": TOKEN}, base_url=f"http://127.0.0.1:{PORT}")
    assert r.status_code == 200 and time.perf_counter() - t0 < 1.0
