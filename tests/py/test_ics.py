"""ICS reader (R-135..R-138): parsing, date mapping, expansion, keys and HTTP fetch."""
from __future__ import annotations

import http.server
import os
import threading
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from reframe.calendars import ics

FIX = Path(__file__).parent / "fixtures" / "ics"
TZ = ZoneInfo("America/Los_Angeles")
WIN = (date(2026, 11, 1), date(2027, 1, 31))


def _read(name: str, win=WIN) -> ics.IcsRead:
    return ics.parse_and_expand((FIX / name).read_bytes(), *win, tz=TZ)


def _by_uid(name: str) -> dict[str, ics.IcsOccurrence]:
    r = _read(name)
    assert r.complete, r.error
    return {e.uid: e for e in r.events}


def _span(e: ics.IcsOccurrence):
    return e.start_date, e.end_date, e.time_text


# --------------------------------------------------------------------------- date mapping

def test_tzid_event_converted_to_local_zone_before_taking_dates():
    e = _by_uid("basic.ics")["tzid-1"]
    # 08:00-09:00 Berlin = 23:00-00:00 Los Angeles the previous day; 00:00 end does not spill
    assert _span(e) == ("2026-12-02", "2026-12-02", "23:00-00:00")
    assert e.original_start == "2026-12-02T23:00:00"
    assert e.location == "Room 1" and not e.all_day and not e.recurring


def test_utc_and_floating_times():
    ev = _by_uid("basic.ics")
    assert _span(ev["utc-1"]) == ("2026-12-04", "2026-12-04", "10:00-11:00")
    assert ev["utc-1"].title == "UTC meeting"
    assert _span(ev["floating-1"]) == ("2026-12-05", "2026-12-05", "09:00-10:00")


def test_all_day_single_multi_and_missing_end():
    ev = _by_uid("basic.ics")
    assert _span(ev["allday-1"]) == ("2026-12-07", "2026-12-07", None)
    assert ev["allday-1"].all_day and ev["allday-1"].original_start == "2026-12-07"
    assert _span(ev["allday-multi"]) == ("2026-12-10", "2026-12-12", None)
    assert _span(ev["allday-noend"]) == ("2026-12-15", "2026-12-15", None)


def test_timed_missing_end_and_no_subject():
    e = _by_uid("basic.ics")["timed-noend"]
    assert _span(e) == ("2026-12-16", "2026-12-16", "09:00-09:00")
    assert e.title == "(no subject)"


def test_midnight_end_does_not_spill_and_crossing_spans_both_dates():
    ev = _by_uid("basic.ics")
    assert _span(ev["ends-midnight"]) == ("2026-12-17", "2026-12-17", "22:00-00:00")
    assert _span(ev["crosses-midnight"]) == ("2026-12-19", "2026-12-20", "22:00-02:00")


def test_duration():
    assert _span(_by_uid("basic.ics")["duration-1"]) == ("2026-12-21", "2026-12-22", "23:00-02:00")


def test_cancelled_whole_event_flagged():
    ev = _by_uid("basic.ics")
    assert ev["cancelled-1"].cancelled
    assert not ev["utc-1"].cancelled


def test_non_recurring_no_upper_bound_but_lower_bound_applies():
    ev = _by_uid("basic.ics")
    assert "far-future" in ev and ev["far-future"].start_date == "2035-10-01"
    assert "past-1" not in ev


# --------------------------------------------------------------------------- recurrence

def test_weekly_series_expanded_only_inside_window_with_exdate_and_rdate():
    r = _read("recurring.ics")
    assert r.complete
    keys = sorted(e.original_start for e in r.events)
    assert all(e.uid == "weekly-1" and e.recurring for e in r.events)
    assert keys[0] == "2026-11-02T10:00:00" and keys[-1] == "2027-01-25T10:00:00"
    assert "2026-11-26T10:00:00" in keys            # RDATE
    assert "2026-12-21T10:00:00" not in keys        # EXDATE
    assert len(keys) == len(set(keys)) == 13        # 13 Mondays + RDATE - EXDATE

    narrow = _read("recurring.ics", (date(2026, 11, 10), date(2026, 11, 20)))
    assert [e.original_start for e in narrow.events] == ["2026-11-16T10:00:00"]


def test_moved_override_keeps_original_slot_key():
    ev = {e.original_start: e for e in _read("recurring.ics").events}
    moved = ev["2026-12-07T10:00:00"]
    assert moved.title == "Weekly sync (moved)"
    assert _span(moved) == ("2026-12-09", "2026-12-09", "15:00-16:00")
    assert not moved.cancelled


def test_cancelled_override_flagged():
    ev = {e.original_start: e for e in _read("recurring.ics").events}
    assert ev["2026-12-14T10:00:00"].cancelled
    assert sum(e.cancelled for e in ev.values()) == 1


def test_missing_uid_uses_stable_weak_key():
    a, b = _read("nouid.ics"), _read("nouid.ics")
    assert a.complete and len(a.events) == 1
    e = a.events[0]
    assert e.weak_key and e.uid.startswith("nouid:")
    assert e.uid == b.events[0].uid
    assert not _by_uid("basic.ics")["utc-1"].weak_key


@pytest.mark.parametrize("name", ["truncated.ics", "garbage.ics"])
def test_malformed_file_is_incomplete_and_does_not_raise(name):
    r = _read(name)
    assert not r.complete and r.error and r.events == []


def test_read_source_file_and_missing_file(tmp_path):
    r = ics.read_source(f'  "{FIX / "basic.ics"}" ', *WIN, tz=TZ)
    assert r.complete and len(r.events) == 12
    missing = ics.read_source(str(tmp_path / "nope.ics"), *WIN, tz=TZ)
    assert not missing.complete and missing.error


# --------------------------------------------------------------------------- locations

def test_normalize_location():
    assert ics.normalize_location(' "webcal://example.com/cal.ics" ') == ("url", "https://example.com/cal.ics")
    assert ics.normalize_location("https://example.com/a?b=1") == ("url", "https://example.com/a?b=1")
    kind, path = ics.normalize_location("'sub\\..\\cal.ics'")
    assert kind == "file" and os.path.isabs(path) and path == os.path.normpath(os.path.abspath("cal.ics"))
    assert ics.normalize_location(r"C:\cals\a.ics") == ("file", r"C:\cals\a.ics")
    for bad in ("", "  ", '""', "ftp://example.com/x.ics"):
        with pytest.raises(ValueError):
            ics.normalize_location(bad)


def test_is_plain_http_and_mask_url():
    assert ics.is_plain_http("http://example.com/x.ics")
    assert not ics.is_plain_http("https://example.com/x.ics")
    assert ics.mask_url("https://cal.example.com/private/abc123/basic.ics?token=s") == "https://cal.example.com/…"
    assert ics.mask_url("https://cal.example.com") == "https://cal.example.com"
    assert ics.mask_url(r"C:\cals\a.ics") == r"C:\cals\a.ics"


# --------------------------------------------------------------------------- HTTP

SECRET_PATH = "/private/s3cr3t-token/basic.ics"


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == SECRET_PATH:
            if self.headers.get("If-None-Match") == '"v1"':
                self.send_response(304)
                self.send_header("ETag", '"v1"')
                self.end_headers()
                return
            body = (FIX / "basic.ics").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/calendar")
            self.send_header("ETag", '"v1"')
            self.send_header("Last-Modified", "Tue, 24 Nov 2026 10:00:00 GMT")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(500)
            self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def test_fetch_200_then_304(server):
    url = server + SECRET_PATH
    first = ics.read_source(url, *WIN, tz=TZ, timeout=5)
    assert first.complete and not first.not_modified and len(first.events) == 12
    assert first.etag == '"v1"' and first.last_modified.startswith("Tue, 24 Nov 2026")

    second = ics.read_source(url, *WIN, etag=first.etag, last_modified=first.last_modified, tz=TZ, timeout=5)
    assert second.not_modified and second.complete and second.events == []
    assert second.etag == '"v1"'


def test_fetch_error_is_incomplete_and_masks_url(server):
    url = server + "/private/other-s3cr3t/feed.ics?key=abc"
    r = ics.read_source(url, *WIN, tz=TZ, timeout=5)
    assert not r.complete and "500" in r.error
    assert "other-s3cr3t" not in r.error and "key=abc" not in r.error

    data, meta = ics.fetch("http://127.0.0.1:1/private/s3cr3t-token/x.ics", timeout=2)
    assert data is None and not meta.complete and "s3cr3t-token" not in meta.error
