import datetime as dt

import pytest

from reframe.db import undo as undomod
from reframe.db.changelog import writer
from reframe.domain import items, settings
from reframe.outlook import sync as S
from reframe.outlook.adapter import CalendarInfo, OutlookUnavailable
from reframe.outlook.fake_adapter import FakeAdapter

TODAY = dt.date(2026, 11, 10)
D = dt.datetime


@pytest.fixture
def fake():
    return FakeAdapter([CalendarInfo(id="cal1", name="Calendar", path="\\\\me\\Calendar", is_default=True),
                        CalendarInfo(id="hol", name="United States holidays", path="\\\\me\\Holidays"),
                        CalendarInfo(id="grp", name="Group", path="\\\\grp")])


@pytest.fixture
def cfg(conn):
    """Connected/holiday flags per Outlook folder, as calendar_source rows (R-130)."""
    def set_(**selected):
        for cid, name, hol in (("cal1", "Calendar", False), ("hol", "United States holidays", True), ("grp", "Group", False)):
            conn.execute("INSERT INTO calendar_source (kind, external_id, name, location, is_holiday, connected) "
                         "VALUES ('outlook', ?, ?, '', ?, ?) ON CONFLICT(kind, external_id) DO UPDATE SET "
                         "connected=excluded.connected, is_holiday=excluded.is_holiday",
                         (cid, name, int(hol), int(selected.get(cid, cid == "cal1"))))
    set_()
    return set_


def sync(conn, fake):
    return S.run_sync(conn, fake, today=TODAY)


def visible(conn):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM item WHERE deleted_at IS NULL AND hidden_at IS NULL ORDER BY start_date, title")]


# ------------------------------------------------------------ date mapping (R-64)

def test_all_day_end_is_exclusive_next_midnight():
    assert S.map_dates(D(2026, 11, 11), D(2026, 11, 12), True) == ("2026-11-11", "2026-11-11")
    assert S.map_dates(D(2026, 11, 11), D(2026, 11, 14), True) == ("2026-11-11", "2026-11-13")


def test_timed_meeting_ending_at_midnight_does_not_spill():
    assert S.map_dates(D(2026, 11, 11, 22), D(2026, 11, 12, 0, 0), False) == ("2026-11-11", "2026-11-11")


def test_timed_meeting_crossing_midnight_spans_both_dates():
    assert S.map_dates(D(2026, 11, 11, 22), D(2026, 11, 12, 1), False) == ("2026-11-11", "2026-11-12")


def test_zero_length_and_same_day():
    assert S.map_dates(D(2026, 11, 11, 9), D(2026, 11, 11, 9), False) == ("2026-11-11", "2026-11-11")
    assert S.map_dates(D(2026, 11, 11, 9), D(2026, 11, 11, 10, 30), False) == ("2026-11-11", "2026-11-11")


def test_dst_day_boundaries_use_wall_clock():
    # 2026-11-01 is the US fall-back day (25 hours). Wall-clock arithmetic keeps dates right.
    assert S.map_dates(D(2026, 11, 1), D(2026, 11, 2), True) == ("2026-11-01", "2026-11-01")
    assert S.map_dates(D(2026, 3, 8, 1), D(2026, 3, 8, 4), False) == ("2026-03-08", "2026-03-08")


def test_all_day_item_from_other_time_zone_is_not_shifted(conn, fake, cfg):
    # tzinfo is stripped by the adapter; the sync only sees naive wall-clock values
    fake.add(gaid="G", subject="Holiday elsewhere", start=D(2026, 11, 20), end=D(2026, 11, 21), all_day=True)
    sync(conn, fake)
    assert visible(conn)[0]["start_date"] == "2026-11-20"


# ------------------------------------------------------------------- basic

def test_import_adds_items_with_meeting_tag_and_single_change_set(conn, fake, cfg):
    fake.add(gaid="A", subject="Staff meeting", start=D(2026, 11, 12, 9), end=D(2026, 11, 12, 10))
    fake.add(gaid="B", subject="Offsite", start=D(2026, 11, 13), end=D(2026, 11, 15), all_day=True)
    r = sync(conn, fake)
    assert (r["added"], r["updated"], r["removed"]) == (2, 0, 0)
    rows = visible(conn)
    assert [(x["title"], x["start_date"], x["end_date"], x["outlook_time"]) for x in rows] == [
        ("Staff meeting", "2026-11-12", "2026-11-12", "09:00-10:00"), ("Offsite", "2026-11-13", "2026-11-14", None)]
    assert all(x["origin"] == "outlook" and x["kind"] == "plan" for x in rows)
    assert [t["name"] for t in items.get_item(conn, rows[0]["id"])["tags"]] == ["meeting"]
    sets = conn.execute("SELECT actor, label FROM change_set WHERE actor='sync'").fetchall()
    assert len(sets) == 1 and sets[0]["label"] == "Outlook sync: 2 added, 0 updated, 0 removed (Calendar)"
    assert conn.execute("SELECT status FROM sync_run WHERE calendars='Calendar'").fetchone()[0] == "ok"
    assert settings.get(conn, "last_sync_at") is not None


def test_second_sync_is_a_no_op_and_leaves_no_change_set(conn, fake, cfg):
    fake.add(gaid="A", subject="X", start=D(2026, 11, 12, 9), end=D(2026, 11, 12, 10))
    first = sync(conn, fake)
    assert first["change_set_id"] is not None
    n = conn.execute("SELECT count(*) FROM change_set").fetchone()[0]
    r = sync(conn, fake)
    assert (r["added"], r["updated"], r["removed"]) == (0, 0, 0)
    assert r["change_set_id"] is None       # no Undo button may point at an unrelated set
    assert conn.execute("SELECT count(*) FROM change_set").fetchone()[0] == n


def test_holiday_calendar_gets_holiday_tag(conn, fake, cfg):
    cfg(hol=True, cal1=False)
    fake.add("hol", gaid="H", subject="Thanksgiving", start=D(2026, 11, 26), end=D(2026, 11, 27), all_day=True)
    sync(conn, fake)
    assert [t["name"] for t in items.get_item(conn, visible(conn)[0]["id"])["tags"]] == ["holiday"]


def test_sync_undoable_in_one_step(conn, fake, cfg):
    fake.add(gaid="A", subject="X", start=D(2026, 11, 12, 9), end=D(2026, 11, 12, 10))
    sync(conn, fake)
    res = undomod.undo(conn)
    assert res["undone_label"].startswith("Outlook sync")
    assert visible(conn) == []


def test_no_calendars_selected(conn, fake):
    for cid in ("cal1", "hol", "grp"):
        conn.execute("INSERT INTO calendar_source (kind, external_id, name, location, connected) "
                     "VALUES ('outlook', ?, ?, '', 0)", (cid, cid))
    r = sync(conn, fake)
    assert r["status"] == "no_calendars"


def test_every_outlook_folder_is_connected_by_default_and_new_ones_are_hidden(conn, fake):
    fake.add(gaid="A", subject="X", start=D(2026, 11, 12, 9), end=D(2026, 11, 12, 10))
    r = sync(conn, fake)
    rows = conn.execute("SELECT external_id, connected, visible FROM calendar_source WHERE kind='outlook' "
                        "AND external_id IN ('cal1','hol','grp') ORDER BY external_id").fetchall()
    assert [tuple(x) for x in rows] == [("cal1", 1, 0), ("grp", 1, 0), ("hol", 1, 0)]
    assert r["new_sources"] == 3 and "3 new calendars connected, hidden" in r["warnings"]
    assert r["added"] == 1 and visible(conn)[0]["source_id"] is not None


def test_disconnect_soft_deletes_in_one_change_set_and_reconnect_restores_annotations(conn, fake, cfg):
    from reframe.calendars import sources
    fake.add(gaid="A", subject="X", start=D(2026, 11, 12, 9), end=D(2026, 11, 12, 10))
    sync(conn, fake)
    iid = visible(conn)[0]["id"]
    with writer(conn, "user", "note"):
        items.update_item(conn, iid, notes="keep me")
    sid = conn.execute("SELECT id FROM calendar_source WHERE external_id='cal1'").fetchone()[0]
    assert sources.set_connected(conn, sid, False)["removed"] == 1
    assert conn.execute("SELECT label FROM change_set ORDER BY id DESC").fetchone()[0].startswith("Removed: calendar disconnected")
    assert visible(conn) == []
    assert sync(conn, fake)["status"] == "no_calendars" or visible(conn) == []
    sources.set_connected(conn, sid, True)
    sync(conn, fake)
    rows = visible(conn)
    assert [r["id"] for r in rows] == [iid] and rows[0]["notes"] == "keep me"


def test_unavailable_adapter_raises_for_caller_to_report():
    f = FakeAdapter()
    f.unavailable = "Outlook is not running"
    with pytest.raises(OutlookUnavailable):
        f.__enter__()


# ------------------------------------------------- updates keep local annotations

def test_edit_in_outlook_updates_fields_and_keeps_local_tags_notes(conn, fake, cfg):
    ev = fake.add(gaid="A", subject="Design review", start=D(2026, 11, 12, 9), end=D(2026, 11, 12, 10))
    sync(conn, fake)
    iid = visible(conn)[0]["id"]
    with writer(conn, "user", "annotate"):
        items.update_item(conn, iid, notes="my notes")
        items.set_item_tags(conn, iid, ["Project X", "meeting"])
        items.add_attachment(conn, iid, "C:\\x.pdf")
    ev.subject, ev.location = "Design review v2", "Room 4"
    r = sync(conn, fake)
    assert r["updated"] == 1
    d = items.get_item(conn, iid)
    assert (d["title"], d["outlook_location"], d["notes"]) == ("Design review v2", "Room 4", "my notes")
    assert [t["name"] for t in d["tags"]] == ["Project X", "meeting"] and len(d["attachments"]) == 1


def test_non_recurring_move_keeps_identity(conn, fake, cfg):
    ev = fake.add(gaid="A", subject="Workshop", start=D(2026, 11, 12, 9), end=D(2026, 11, 12, 10))
    sync(conn, fake)
    iid = visible(conn)[0]["id"]
    with writer(conn, "user", "note"):
        items.update_item(conn, iid, notes="keep")
    ev.start, ev.end = D(2026, 11, 19, 9), D(2026, 11, 19, 10)
    r = sync(conn, fake)
    assert (r["added"], r["updated"], r["removed"]) == (0, 1, 0)
    d = items.get_item(conn, iid)
    assert d["start_date"] == "2026-11-19" and d["notes"] == "keep"


# ------------------------------------------------------------------ recurring

def series(fake, n=4, gaid="SER", **kw):
    evs = []
    for i in range(n):
        s = D(2026, 11, 16) + dt.timedelta(days=7 * i)
        evs.append(fake.add(gaid=gaid, subject="Weekly sync", start=s.replace(hour=9), end=s.replace(hour=10),
                            recurring=True, **kw))
    return evs


def test_series_occurrences_share_gaid_and_get_distinct_keys(conn, fake, cfg):
    series(fake)
    r = sync(conn, fake)
    assert r["added"] == 4
    uids = {x[0] for x in conn.execute("SELECT outlook_uid FROM item")}
    assert uids == {f"cal1|SER|2026-11-{d}" for d in (16, 23, 30)} | {"cal1|SER|2026-12-07"}
    assert {x[0] for x in conn.execute("SELECT outlook_series_id FROM item")} == {"cal1|SER"}


def test_moved_exception_updates_in_place_using_original_date(conn, fake, cfg):
    evs = series(fake)
    sync(conn, fake)
    target = conn.execute("SELECT id FROM item WHERE outlook_uid='cal1|SER|2026-11-23'").fetchone()[0]
    with writer(conn, "user", "note"):
        items.update_item(conn, target, notes="keep my note")
        items.set_item_tags(conn, target, ["Alpha", "meeting"])
    evs[1].start, evs[1].end = D(2026, 11, 25, 14), D(2026, 11, 25, 15)
    evs[1].original_date = dt.date(2026, 11, 23)
    r = sync(conn, fake)
    assert (r["added"], r["updated"], r["removed"]) == (0, 1, 0)
    d = items.get_item(conn, target)
    assert d["start_date"] == "2026-11-25" and d["notes"] == "keep my note"
    assert "Alpha" in [t["name"] for t in d["tags"]]


def test_exception_without_resolvable_original_date_falls_back_to_remove_and_add(conn, fake, cfg):
    evs = series(fake)
    sync(conn, fake)
    evs[1].start, evs[1].end = D(2026, 11, 25, 14), D(2026, 11, 25, 15)   # original_date unknown
    r = sync(conn, fake)
    assert (r["added"], r["removed"]) == (1, 1)


def test_series_tag_propagates_to_new_occurrences_on_sync(conn, fake, cfg):
    series(fake, n=2)
    sync(conn, fake)
    first = conn.execute("SELECT id FROM item ORDER BY start_date").fetchone()[0]
    with writer(conn, "user", "tag"):
        items.set_item_tags(conn, first, ["Weekly Sync Tag", "meeting"])
    fake.add(gaid="SER", subject="Weekly sync", start=D(2026, 12, 14, 9), end=D(2026, 12, 14, 10), recurring=True)
    sync(conn, fake)
    new = conn.execute("SELECT id FROM item WHERE start_date='2026-12-14'").fetchone()[0]
    names = [t["name"] for t in items.get_item(conn, new)["tags"]]
    assert names == ["Weekly Sync Tag", "meeting"]


def test_hidden_item_stays_hidden_and_hidden_series_new_occurrences_created_hidden(conn, fake, cfg):
    series(fake, n=2)
    fake.add(gaid="ONE", subject="One-off", start=D(2026, 11, 18, 9), end=D(2026, 11, 18, 10))
    sync(conn, fake)
    ids = {r["start_date"]: r["id"] for r in conn.execute("SELECT id, start_date FROM item")}
    with writer(conn, "user", "hide"):
        items.hide_item(conn, ids["2026-11-16"], series=True)
        items.hide_item(conn, ids["2026-11-18"])
    fake.add(gaid="SER", subject="Weekly sync", start=D(2026, 12, 14, 9), end=D(2026, 12, 14, 10), recurring=True)
    fake.events["cal1"][-2].subject = "One-off renamed"
    sync(conn, fake)
    assert visible(conn) == []
    hidden = conn.execute("SELECT count(*) FROM item WHERE hidden_at IS NOT NULL").fetchone()[0]
    assert hidden == 4
    assert conn.execute("SELECT title FROM item WHERE id=?", (ids["2026-11-18"],)).fetchone()[0] == "One-off renamed"


# --------------------------------------------------- identity corner cases (R-63)

def test_non_recurring_items_sharing_gaid_and_start_use_entry_id(conn, fake, cfg):
    cfg(hol=True, cal1=False)
    for i in range(3):
        fake.add("hol", gaid="SAME", entry_id=f"E{i}", subject=f"Holiday {i}", start=D(2026, 12, 25),
                 end=D(2026, 12, 26), all_day=True)
    r = sync(conn, fake)
    assert r["added"] == 3
    assert sync(conn, fake)["added"] == 0   # keys are stable


def test_empty_gaid_falls_back_to_entry_id(conn, fake, cfg):
    fake.add(gaid="", entry_id="ENTRY1", subject="No gaid", start=D(2026, 11, 12, 9), end=D(2026, 11, 12, 10))
    sync(conn, fake)
    assert conn.execute("SELECT outlook_uid FROM item").fetchone()[0] == "cal1|entry:ENTRY1"


def test_same_meeting_in_two_calendars_keeps_both(conn, fake, cfg):
    cfg(cal1=True, grp=True)
    for cal in ("cal1", "grp"):
        fake.add(cal, gaid="SHARED", subject="Shared mtg", start=D(2026, 11, 12, 9), end=D(2026, 11, 12, 10))
    r = sync(conn, fake)
    assert r["added"] == 2


# ----------------------------------------------------------------- removals

def test_cancelled_meeting_is_removed_and_reappears(conn, fake, cfg):
    for i in range(6):
        fake.add(gaid=f"G{i}", subject=f"M{i}", start=D(2026, 11, 12 + i, 9), end=D(2026, 11, 12 + i, 10))
    sync(conn, fake)
    ev = fake.events["cal1"][0]
    ev.cancelled = True
    r = sync(conn, fake)
    assert r["removed"] == 1
    assert conn.execute("SELECT count(*) FROM item WHERE deleted_at IS NOT NULL").fetchone()[0] == 1
    ev.cancelled = False
    r = sync(conn, fake)
    assert r["updated"] == 1 and conn.execute("SELECT count(*) FROM item WHERE deleted_at IS NOT NULL").fetchone()[0] == 0


def test_removal_guard_skips_when_over_20_percent(conn, fake, cfg):
    for i in range(10):
        fake.add(gaid=f"G{i}", subject=f"M{i}", start=D(2026, 11, 12 + i, 9), end=D(2026, 11, 12 + i, 10))
    sync(conn, fake)
    fake.events["cal1"] = fake.events["cal1"][:5]     # half vanish
    r = sync(conn, fake)
    assert r["removed"] == 0 and r["removals_skipped"]
    assert len(visible(conn)) == 10


def test_removal_below_guard_is_applied(conn, fake, cfg):
    for i in range(10):
        fake.add(gaid=f"G{i}", subject=f"M{i}", start=D(2026, 11, 12 + i, 9), end=D(2026, 11, 12 + i, 10))
    sync(conn, fake)
    fake.events["cal1"] = fake.events["cal1"][2:]     # exactly 20%: not "more than"
    r = sync(conn, fake)
    assert r["removed"] == 2


def test_partial_read_never_removes_but_still_upserts(conn, fake, cfg):
    for i in range(5):
        fake.add(gaid=f"G{i}", subject=f"M{i}", start=D(2026, 11, 12 + i, 9), end=D(2026, 11, 12 + i, 10))
    sync(conn, fake)
    fake.fail["cal1"] = "COM error"
    r = sync(conn, fake)
    assert r["status"] == "partial" and r["removed"] == 0 and len(visible(conn)) == 5
    assert any("COM error" in w for w in r["warnings"])


def test_empty_calendar_warns_but_never_removes(conn, fake, cfg):
    fake.add(gaid="A", subject="X", start=D(2026, 11, 12, 9), end=D(2026, 11, 12, 10))
    sync(conn, fake)
    fake.events["cal1"] = []
    r = sync(conn, fake)
    assert r["removed"] == 0 and any("returned 0 items" in w for w in r["warnings"])
    assert len(visible(conn)) == 1


def test_deselected_calendar_never_causes_removals(conn, fake, cfg):
    cfg(cal1=True, grp=True)
    for cal in ("cal1", "grp"):
        fake.add(cal, gaid=cal, subject=cal, start=D(2026, 11, 12, 9), end=D(2026, 11, 12, 10))
    sync(conn, fake)
    cfg(cal1=True, grp=False)
    fake.events["cal1"] = [fake.events["cal1"][0]]
    r = sync(conn, fake)
    assert r["removed"] == 0 and len(visible(conn)) == 2


def test_group_calendar_that_starts_returning_items_is_imported(conn, fake, cfg):
    cfg(cal1=True, grp=True)
    sync(conn, fake)     # both empty
    fake.add("grp", gaid="g", subject="Group event", start=D(2026, 11, 20, 9), end=D(2026, 11, 20, 10))
    assert sync(conn, fake)["added"] == 1


def test_items_outside_window_are_not_removed(conn, fake, cfg):
    settings.set_many(conn, {"last_sync_at": "2026-11-01T00:00:00Z", "lookback_days": 30})
    with writer(conn, "sync", "seed"):
        conn.execute("INSERT INTO item (title,start_date,end_date,origin, source_id,outlook_uid,outlook_calendar) "
                     "VALUES ('Old','2025-01-01','2025-01-01','outlook', 'testcal','cal1|OLD','cal1')")
    fake.add(gaid="A", subject="X", start=D(2026, 11, 12, 9), end=D(2026, 11, 12, 10))
    r = sync(conn, fake)
    assert r["removed"] == 0


def test_backup_callback_runs_before_sync(conn, fake, cfg):
    calls = []
    fake.add(gaid="A", subject="X", start=D(2026, 11, 12, 9), end=D(2026, 11, 12, 10))
    S.run_sync(conn, fake, today=TODAY, backup_cb=lambda: calls.append(conn.execute("SELECT count(*) FROM item").fetchone()[0]))
    assert calls == [0]


def test_first_sync_uses_long_lookback(conn, fake, cfg):
    fake.add(gaid="OLD", subject="Last year", start=D(2026, 1, 5, 9), end=D(2026, 1, 5, 10))
    assert sync(conn, fake)["added"] == 1          # first sync: 365 day lookback
    fake.events["cal1"].clear()
    fake.add(gaid="OLD2", subject="Last year 2", start=D(2026, 1, 6, 9), end=D(2026, 1, 6, 10))
    assert sync(conn, fake)["added"] == 0          # later syncs: 30 day lookback


def test_progress_callback(conn, fake, cfg):
    stages = []
    fake.add(gaid="A", subject="X", start=D(2026, 11, 12, 9), end=D(2026, 11, 12, 10))
    S.run_sync(conn, fake, today=TODAY, progress=lambda s, n, t: stages.append(s))
    assert stages[0].startswith("Reading") and stages[-1] == "Updating timeline"
