"""ICS sources through the shared import engine (R-135 .. R-139)."""
import datetime as dt
from zoneinfo import ZoneInfo

from reframe.calendars import ics, ics_sync, sources
from reframe.db.changelog import writer
from reframe.domain import items

TODAY = dt.date(2026, 11, 10)
TZ = ZoneInfo("America/Los_Angeles")


def cal(*events: str) -> str:
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//t//EN\r\n" + "".join(events) + "END:VCALENDAR\r\n"


def ev(uid, start, end, summary="Ev", extra=""):
    return (f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20261101T000000Z\r\nDTSTART:{start}\r\nDTEND:{end}\r\n"
            f"SUMMARY:{summary}\r\n{extra}END:VEVENT\r\n")


def run(conn, **kw):
    return ics_sync.run_ics_sync(conn, today=TODAY, tz=TZ, **kw)


def live(conn):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM item WHERE origin='ics' AND deleted_at IS NULL AND hidden_at IS NULL ORDER BY start_date, title")]


def test_file_source_import_tags_lock_and_series(conn, tmp_path):
    f = tmp_path / "team.ics"
    f.write_bytes(cal(ev("A", "20261112T090000", "20261112T100000", "Standup", "RRULE:FREQ=WEEKLY;COUNT=3\r\n"),
                     ev("B", "20261120", "20261121", "Offsite")).encode("utf-8"))
    src = sources.add_ics(conn, str(f), name="Team")
    r = run(conn)
    assert r["added"] == 4 and r["status"] == "ok"
    rows = live(conn)
    assert [x["title"] for x in rows] == ["Standup", "Standup", "Offsite", "Standup"]
    standup = rows[0]
    assert standup["source_id"] == src["id"] and standup["outlook_time"] == "09:00-10:00"
    assert [t["name"] for t in items.get_item(conn, standup["id"])["tags"]] == ["meeting"]     # timed => meeting
    assert items.get_item(conn, rows[2]["id"])["tags"] == []                                   # all-day: no system tag
    with writer(conn, "user", "tag series"):
        items.set_item_tags(conn, standup["id"], ["Ops", "meeting"])
    assert all("Ops" in [t["name"] for t in items.get_item(conn, x["id"])["tags"]]
               for x in live(conn) if x["title"] == "Standup")
    with writer(conn, "user", "x"):
        try:
            items.update_item(conn, standup["id"], title="Renamed")
            assert False, "imported ICS items are locked"
        except items.LockedItem as e:
            assert "calendar feed Team" in str(e)
    assert run(conn)["change_set_id"] is None     # second read is a no-op


def test_moved_override_keeps_annotations_and_cancel_then_reappear(conn, tmp_path):
    f = tmp_path / "c.ics"
    base = ev("S", "20261112T090000", "20261112T100000", "Weekly", "RRULE:FREQ=WEEKLY;COUNT=4\r\n")
    f.write_bytes(cal(base).encode("utf-8"))
    sources.add_ics(conn, str(f))
    run(conn)
    second = live(conn)[1]
    with writer(conn, "user", "note"):
        items.update_item(conn, second["id"], notes="prep slides")
    moved = ev("S", "20261120T090000", "20261120T100000", "Weekly", "RECURRENCE-ID:20261119T090000\r\n")
    f.write_bytes(cal(base, moved).encode("utf-8"))
    run(conn)
    row = conn.execute("SELECT start_date, notes FROM item WHERE id=?", (second["id"],)).fetchone()
    assert tuple(row) == ("2026-11-20", "prep slides")
    cancelled = ev("S", "20261120T090000", "20261120T100000", "Weekly",
                   "RECURRENCE-ID:20261119T090000\r\nSTATUS:CANCELLED\r\n")
    f.write_bytes(cal(base, cancelled).encode("utf-8"))
    assert run(conn)["removed"] == 1
    f.write_bytes(cal(base, moved).encode("utf-8"))
    run(conn)
    assert conn.execute("SELECT deleted_at, notes FROM item WHERE id=?", (second["id"],)).fetchone()[1] == "prep slides"
    assert second["id"] in [x["id"] for x in live(conn)]


def test_unreadable_or_truncated_source_never_removes(conn, tmp_path):
    f = tmp_path / "c.ics"
    f.write_bytes(cal(*[ev(f"E{i}", f"202611{12 + i}T090000", f"202611{12 + i}T100000") for i in range(5)]).encode("utf-8"))
    sources.add_ics(conn, str(f))
    run(conn)
    f.write_bytes("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:E0".encode("utf-8"))
    r = run(conn)
    assert r["status"] == "partial" and r["removed"] == 0 and len(live(conn)) == 5
    f.unlink()
    assert run(conn)["removed"] == 0 and len(live(conn)) == 5


def test_removal_guard_per_source(conn, tmp_path):
    f = tmp_path / "c.ics"
    f.write_bytes(cal(*[ev(f"E{i}", f"202611{12 + i}T090000", f"202611{12 + i}T100000") for i in range(5)]).encode("utf-8"))
    sources.add_ics(conn, str(f))
    run(conn)
    f.write_bytes(cal(ev("E0", "20261112T090000", "20261112T100000")).encode("utf-8"))
    r = run(conn)
    assert r["removed"] == 0 and any("skipped 4 removals" in w for w in r["warnings"])


def test_not_modified_is_up_to_date_and_url_is_masked(conn):
    def reader(location, a, b, **kw):
        return ics.IcsRead(events=[], not_modified=True)
    src = sources.add_ics(conn, "https://cal.example.com/secret/abc123/basic.ics")
    assert "abc123" not in src["location"] and "abc123" not in str(sources.list_sources(conn))
    r = run(conn, reader=reader)
    assert r["calendars"][0]["not_modified"] and r["removed"] == 0
    assert conn.execute("SELECT status FROM sync_run").fetchone()[0] == "up to date"


def test_due_sources_respect_refresh_interval(conn, tmp_path):
    f = tmp_path / "c.ics"
    f.write_bytes(cal().encode("utf-8"))
    src = sources.add_ics(conn, str(f))
    assert [s["id"] for s in ics_sync.due_sources(conn)] == [src["id"]]
    run(conn)
    assert ics_sync.due_sources(conn) == []
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=61)
    assert [s["id"] for s in ics_sync.due_sources(conn, later)] == [src["id"]]
