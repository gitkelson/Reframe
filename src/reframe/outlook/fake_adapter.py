"""In-memory stand-in for classic Outlook, used by tests and development without Outlook."""
from __future__ import annotations

import datetime as dt

from .adapter import CalendarInfo, CalendarRead, OutlookUnavailable, RawEvent


class FakeAdapter:
    def __init__(self, calendars: list[CalendarInfo] | None = None):
        self.calendars = calendars or [CalendarInfo(id="cal1", name="Calendar", path="\\\\me\\Calendar", is_default=True)]
        self.events: dict[str, list[RawEvent]] = {c.id: [] for c in self.calendars}
        self.fail: dict[str, str] = {}          # calendar id -> error: read fails part-way (complete=False)
        self.unavailable: str | None = None
        self.reads = 0

    def add(self, calendar_id: str = "cal1", **kw) -> RawEvent:
        kw.setdefault("calendar_id", calendar_id)
        kw.setdefault("entry_id", f"E{len(self.events[calendar_id])}")
        kw.setdefault("gaid", "")
        kw.setdefault("subject", "Event")
        kw.setdefault("location", "")
        kw.setdefault("all_day", False)
        ev = RawEvent(**kw)
        self.events[calendar_id].append(ev)
        return ev

    def __enter__(self):
        if self.unavailable:
            raise OutlookUnavailable(self.unavailable)
        return self

    def __exit__(self, *exc):
        return None

    def list_calendars(self) -> list[CalendarInfo]:
        return [CalendarInfo(**{**c.__dict__, "item_count": len(self.events[c.id])}) for c in self.calendars]

    def read_calendar(self, calendar_id: str, lookback_start: dt.date, horizon_end: dt.date) -> CalendarRead:
        self.reads += 1
        if calendar_id in self.fail:
            partial = [e for e in self.events[calendar_id][:1]]
            return CalendarRead(events=partial, complete=False, error=self.fail[calendar_id])
        out = []
        for e in self.events[calendar_id]:
            if e.end.date() < lookback_start:
                continue
            if e.recurring and e.start.date() > horizon_end:
                continue
            out.append(e)
        return CalendarRead(events=out)
