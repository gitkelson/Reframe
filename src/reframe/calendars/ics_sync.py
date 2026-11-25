"""ICS file and URL import (R-135 .. R-139) over the shared engine in ``calendars.sync``."""
from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any, Callable

from ..domain import settings
from . import ics
from . import sources as sourcemod
from .sync import Record, SourceRead, import_sources, log_runs

HORIZON_DAYS = 730

Reader = Callable[..., ics.IcsRead]


def due_sources(conn: sqlite3.Connection, now: dt.datetime | None = None) -> list[dict[str, Any]]:
    """Connected URL feeds whose refresh interval has passed (files are read on every sync run)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    default = int(settings.get(conn, "ics_refresh_min") or 60)
    out = []
    for s in sourcemod.list_sources(conn, kind="ics", public=False):
        if not s["connected"]:
            continue
        if not s["last_read_at"]:
            out.append(s)
            continue
        last = dt.datetime.fromisoformat(s["last_read_at"].replace("Z", "+00:00"))
        if now - last >= dt.timedelta(minutes=int(s["refresh_min"] or default)):
            out.append(s)
    return out


def run_ics_sync(conn: sqlite3.Connection, *, source_ids: list[str] | None = None, today: dt.date | None = None,
                 reader: Reader = ics.read_source, tz: dt.tzinfo | None = None,
                 progress: Callable[[str, int, int], None] | None = None) -> dict[str, Any]:
    """Read and import ICS sources (all connected ones, or ``source_ids``). Never raises for read problems."""
    today = today or dt.date.today()
    started = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    srcs = [s for s in sourcemod.list_sources(conn, kind="ics", public=False) if s["connected"]
            and (source_ids is None or s["id"] in source_ids)]
    result: dict[str, Any] = {"added": 0, "updated": 0, "removed": 0, "warnings": [], "calendars": [],
                              "status": "ok", "change_set_id": None}
    if not srcs:
        result["status"] = "no_calendars"
        return result
    reads: list[SourceRead] = []
    for n, src in enumerate(srcs):
        if progress:
            progress(f"Reading {src['name']}", n, len(srcs))
        first = src["last_read_at"] is None
        lookback = int(settings.get(conn, "first_sync_lookback_days" if first else "lookback_days"))
        win_start, win_end = today - dt.timedelta(days=lookback), today + dt.timedelta(days=HORIZON_DAYS)
        rd = reader(src["location"], win_start, win_end, etag=src["etag"], last_modified=src["last_modified"], tz=tz)
        sr = SourceRead(source=src, complete=rd.complete, error=rd.error, events=len(rd.events),
                        win_start=win_start, win_end=win_end, not_modified=rd.not_modified)
        for ev in rd.events:
            if ev.cancelled:
                sr.cancelled += 1
                continue
            series = f"{src['id']}|{ev.uid}" if ev.recurring else None
            tag = "holiday" if src["is_holiday"] else ("meeting" if not ev.all_day else None)
            sr.records.append(Record(uid=f"{src['id']}|{ev.uid}|{ev.original_start}", series_id=series,
                                     title=ev.title, start=ev.start_date, end=ev.end_date, time=ev.time_text,
                                     location=ev.location, system_tag=tag))
        sr._read = rd   # type: ignore[attr-defined]
        reads.append(sr)
    res = import_sources(conn, "ics", reads)
    for sr in reads:
        rd = sr._read   # type: ignore[attr-defined]
        status = "up to date" if rd.not_modified else ("ok" if rd.complete else "partial")
        sourcemod.record_read(conn, sr.source["id"], status=status, error=rd.error, etag=rd.etag,
                              last_modified=rd.last_modified, keep_validators=rd.not_modified or not rd.complete)
    result["calendars"] = res["sources"]
    result["warnings"] = res["warnings"]
    result.update(added=res["added"], updated=res["updated"], removed=res["removed"],
                  change_set_id=res["change_set_id"])
    if any(not c["complete"] for c in res["sources"]):
        result["status"] = "partial"
    log_runs(conn, started, res)
    result["message"] = f"{res['added']} added, {res['updated']} updated, {res['removed']} removed"
    return result
