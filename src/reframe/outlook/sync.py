"""Outlook import (R-60..R-69): no COM here, works on ``RawEvent`` records from an adapter.

Every calendar folder Outlook exposes is a connected calendar source (R-131); reads happen first (possibly
slow, no database lock held), then ``calendars.sync.import_sources`` applies them as one ``sync`` change set.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from collections import Counter
from typing import Any, Callable

from ..calendars import sources as sourcemod
from ..calendars.sync import REMOVAL_GUARD_FRACTION, REMOVAL_GUARD_MIN, Record, SourceRead, import_sources, log_runs
from ..domain import settings
from .adapter import Adapter, RawEvent

HORIZON_DAYS = 730
__all__ = ["REMOVAL_GUARD_FRACTION", "REMOVAL_GUARD_MIN", "map_dates", "time_text", "build_uids", "run_sync"]

Progress = Callable[[str, int, int], None]


def map_dates(start: dt.datetime, end: dt.datetime, all_day: bool) -> tuple[str, str]:
    """Outlook start/end to inclusive date-only start/end (R-64)."""
    s = start.date()
    if all_day:
        # Outlook stores an exclusive end at the next midnight
        e = (end - dt.timedelta(days=1)).date() if end > start else s
    else:
        e = (end - dt.timedelta(seconds=1)).date() if end > start else s
    if e < s:
        e = s
    return s.isoformat(), e.isoformat()


def time_text(ev: RawEvent) -> str | None:
    if ev.all_day:
        return None
    return f"{ev.start:%H:%M}-{ev.end:%H:%M}"


def build_uids(events: list[RawEvent]) -> list[tuple[RawEvent, str, str | None]]:
    """(event, outlook_uid, series_id) for each event of ONE calendar (R-63).

    Recurring: calendar|series|original date, where the original date of a moved
    exception comes from the recurrence pattern. Non-recurring: calendar|gaid
    (stable when the meeting is moved); an EntryID key is used when the GlobalAppointmentID
    is empty or shared by several non-recurring items.
    """
    dup = Counter(e.gaid for e in events if not e.recurring and e.gaid)
    out: list[tuple[RawEvent, str, str | None]] = []
    seen: set[str] = set()
    for ev in events:
        cal = ev.calendar_id
        series_id = None
        if ev.recurring and ev.gaid:
            orig = ev.original_date or ev.start.date()
            series_id = f"{cal}|{ev.gaid}"
            uid = f"{series_id}|{orig.isoformat()}"
        elif ev.gaid and not ev.recurring and dup[ev.gaid] == 1:
            uid = f"{cal}|{ev.gaid}"
        else:
            uid = f"{cal}|entry:{ev.entry_id}"
        if uid in seen:
            uid = f"{cal}|entry:{ev.entry_id}"
        seen.add(uid)
        out.append((ev, uid, series_id))
    return out


def window_for(source: dict[str, Any], conn: sqlite3.Connection, today: dt.date) -> tuple[dt.date, dt.date]:
    """Sync window (R-62): a long lookback the first time a source is read, the short one afterwards."""
    first = source.get("last_read_at") is None
    lookback = settings.get(conn, "first_sync_lookback_days" if first else "lookback_days")
    return today - dt.timedelta(days=int(lookback)), today + dt.timedelta(days=HORIZON_DAYS)


def run_sync(conn: sqlite3.Connection, adapter: Adapter, *, today: dt.date | None = None,
             progress: Progress | None = None, backup_cb: Callable[[], Any] | None = None) -> dict[str, Any]:
    """Import every connected Outlook calendar. Never raises for per-calendar read problems."""
    today = today or dt.date.today()
    started = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    result: dict[str, Any] = {"added": 0, "updated": 0, "removed": 0, "warnings": [], "calendars": [],
                              "status": "ok", "change_set_id": None, "removals_skipped": None, "new_sources": 0}
    folders = adapter.list_calendars()
    new = sourcemod.upsert_outlook_folders(conn, folders)
    if new:
        result["new_sources"] = len(new)
        result["warnings"].append(f"{len(new)} new calendar{'s' if len(new) != 1 else ''} connected, hidden")
    listed = {f.id for f in folders}
    cfg = [s for s in sourcemod.list_sources(conn, kind="outlook", public=False)
           if s["connected"] and s["external_id"] in listed]
    if not cfg:
        result["status"] = "no_calendars"
        result["message"] = "No Outlook calendars are connected. Connect calendars in Settings."
        result["warnings"].append(result["message"])
        return result

    if backup_cb is not None:
        backup_cb()  # R-69: pre-sync state is protected by a backup

    reads: list[SourceRead] = []
    for n, src in enumerate(cfg):
        if progress:
            progress(f"Reading {src['name'] or src['external_id']}", n, len(cfg))
        win_start, win_end = window_for(src, conn, today)
        rd = adapter.read_calendar(src["external_id"], win_start, win_end)
        sr = SourceRead(source=src, complete=rd.complete, error=rd.error, events=len(rd.events),
                        win_start=win_start, win_end=win_end)
        tag = "holiday" if src["is_holiday"] else "meeting"
        for ev, uid, series_id in build_uids(rd.events):
            if ev.cancelled:
                sr.cancelled += 1
                continue
            s, e = map_dates(ev.start, ev.end, ev.all_day)
            sr.records.append(Record(uid=uid, series_id=series_id, title=(ev.subject or "").strip() or "(no subject)",
                                     start=s, end=e, time=time_text(ev), location=ev.location or None,
                                     system_tag=tag, calendar=src["external_id"]))
        reads.append(sr)

    if progress:
        progress("Updating timeline", 0, sum(len(r.records) for r in reads))
    res = import_sources(conn, "outlook", reads)
    for sr, info in zip(reads, res["sources"]):
        sourcemod.record_read(conn, sr.source["id"], status="ok" if sr.complete else "partial", error=sr.error)
        result["calendars"].append(info)
        if info["removals_skipped"]:
            result["removals_skipped"] = info["removals_skipped"]
    result["warnings"].extend(res["warnings"])
    result.update(added=res["added"], updated=res["updated"], removed=res["removed"],
                  change_set_id=res["change_set_id"], cancelled=sum(r.cancelled for r in reads))
    if any(not c["complete"] for c in result["calendars"]):
        result["status"] = "partial"
    log_runs(conn, started, res)
    if result["status"] != "partial":
        settings.set_many(conn, {"last_sync_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    result["message"] = f"{res['added']} added, {res['updated']} updated, {res['removed']} removed"
    return result
