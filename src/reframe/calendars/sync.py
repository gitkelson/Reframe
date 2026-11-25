"""Source-independent calendar import: upsert, removal guard, hiding and series tags (R-62 .. R-66, R-138, R-139).

Each calendar kind (Outlook, ICS) reads its sources first (possibly slowly, without a database lock), turns the
events into ``Record``s and hands them to ``import_sources``, which applies everything in one short writer
transaction recorded as one ``sync`` change set (R-69).
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from ..db.changelog import writer
from ..domain import items, tags

REMOVAL_GUARD_FRACTION = 0.20
REMOVAL_GUARD_MIN = 3   # a single cancelled meeting in a tiny calendar must not trip the guard
KIND_LABEL = {"outlook": "Outlook", "ics": "Calendar feed"}


@dataclass
class Record:
    uid: str
    series_id: str | None
    title: str
    start: str
    end: str
    time: str | None
    location: str | None
    system_tag: str | None          # 'meeting' / 'holiday' / None
    calendar: str | None = None     # Outlook folder id (kept in item.outlook_calendar for agents)


@dataclass
class SourceRead:
    source: dict[str, Any]          # calendar_source row
    records: list[Record] = field(default_factory=list)
    complete: bool = True
    error: str | None = None
    events: int = 0                 # events read, including cancelled ones
    cancelled: int = 0
    win_start: dt.date | None = None
    win_end: dt.date | None = None
    not_modified: bool = False      # ICS 304: nothing to change, no removals


def import_sources(conn: sqlite3.Connection, kind: str, reads: list[SourceRead]) -> dict[str, Any]:
    """Apply the reads of one sync run. Returns totals and per-source results."""
    uid_col = "outlook_uid" if kind == "outlook" else "ics_uid"
    per: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    added = updated = removed = 0
    names = ", ".join(r.source["name"] for r in reads)
    with writer(conn, "sync", f"{KIND_LABEL[kind]} sync") as w:
        existing = {r[uid_col]: r for r in conn.execute(
            f"SELECT id, {uid_col}, title, start_date, end_date, outlook_time, outlook_location, outlook_calendar, "
            f"outlook_series_id, source_id, deleted_at FROM item WHERE origin=?", (kind,))}
        hidden_series = {r[0] for r in conn.execute("SELECT series_id FROM outlook_hidden_series")}
        tag_ids: dict[str, str] = {}

        def system_tag(name: str) -> str:
            if name not in tag_ids:
                tag_ids[name] = tags.get_or_create_tag(conn, name)[0]
            return tag_ids[name]

        for rd in reads:
            src = rd.source
            res = per[src["id"]] = {"id": src["id"], "name": src["name"], "events": rd.events,
                                    "complete": rd.complete, "error": rd.error, "added": 0, "updated": 0,
                                    "removed": 0, "removals_skipped": None, "not_modified": rd.not_modified}
            if rd.error:
                warnings.append(f"{src['name']}: {rd.error}")
            seen: set[str] = set()
            for rec in rd.records:
                seen.add(rec.uid)
                row = existing.get(rec.uid)
                if row is None:
                    hidden = items.NOW_SQL if rec.series_id in hidden_series else "NULL"
                    item_id = conn.execute(
                        f"INSERT INTO item (kind, title, start_date, end_date, origin, source_id, {uid_col}, "
                        "outlook_series_id, outlook_calendar, outlook_time, outlook_location, hidden_at) "
                        f"VALUES ('plan',?,?,?,?,?,?,?,?,?,?,{hidden}) RETURNING id",
                        (rec.title, rec.start, rec.end, kind, src["id"], rec.uid, rec.series_id, rec.calendar,
                         rec.time, rec.location)).fetchone()[0]
                    if rec.system_tag:
                        conn.execute("INSERT OR IGNORE INTO item_tag (item_id, tag_id, position) VALUES (?,?,?)",
                                     (item_id, system_tag(rec.system_tag), tags.SYSTEM_TAG_POSITION))
                    if rec.series_id:
                        items.apply_series_tags(conn, item_id, rec.series_id)
                    existing[rec.uid] = {"id": item_id}
                    res["added"] += 1
                    continue
                if "title" not in row.keys():
                    continue   # duplicate uid within this run
                changes = {}
                for col, val in (("title", rec.title), ("start_date", rec.start), ("end_date", rec.end),
                                 ("outlook_time", rec.time), ("outlook_location", rec.location),
                                 ("outlook_calendar", rec.calendar), ("outlook_series_id", rec.series_id),
                                 ("source_id", src["id"])):
                    if row[col] != val:
                        changes[col] = val
                if row["deleted_at"] is not None:
                    changes["deleted_at"] = None   # reappeared at the source (R-65)
                if changes:
                    sets = ", ".join(f"{c} = ?" for c in changes)
                    conn.execute(f"UPDATE item SET {sets}, updated_at = {items.NOW_SQL} WHERE id = ?",
                                 [*changes.values(), row["id"]])
                    res["updated"] += 1

            # Removals (R-65, R-138): only for sources read completely that returned something in this run
            if rd.not_modified or not rd.complete:
                continue
            if not rd.events:
                warnings.append(f"{src['name']} returned 0 items")   # never causes removals by itself (R-61)
                continue
            in_window = conn.execute(
                f"SELECT id, {uid_col} AS uid FROM item WHERE origin=? AND source_id=? AND deleted_at IS NULL "
                "AND end_date >= ? AND (start_date <= ? OR outlook_series_id IS NULL)",
                (kind, src["id"], rd.win_start.isoformat(), rd.win_end.isoformat())).fetchall()
            gone = [r for r in in_window if r["uid"] not in seen]
            if gone and len(gone) >= REMOVAL_GUARD_MIN and len(gone) / len(in_window) > REMOVAL_GUARD_FRACTION:
                note = (f"{src['name']}: skipped {len(gone)} removals: more than "
                        f"{int(REMOVAL_GUARD_FRACTION * 100)}% of the {len(in_window)} imported items in the window "
                        "would disappear. Check the source, then Sync again.")
                res["removals_skipped"] = note
                warnings.append(note)
            else:
                for r in gone:
                    conn.execute(f"UPDATE item SET deleted_at = {items.NOW_SQL}, updated_at = {items.NOW_SQL} "
                                 "WHERE id = ?", (r["id"],))
                res["removed"] = len(gone)

        added = sum(p["added"] for p in per.values())
        updated = sum(p["updated"] for p in per.values())
        removed = sum(p["removed"] for p in per.values())
        w.set_label(f"{KIND_LABEL[kind]} sync: {added} added, {updated} updated, {removed} removed"
                    + (f" ({names})" if names else ""))
    return {"added": added, "updated": updated, "removed": removed, "warnings": warnings,
            "sources": list(per.values()), "change_set_id": w.change_set_id or None}


def log_runs(conn: sqlite3.Connection, started: str, result: dict[str, Any]) -> None:
    """One sync_run row per source per run (spec 5.2)."""
    finished = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for s in result["sources"]:
        status = "ok" if s["complete"] else "partial"
        if s.get("not_modified"):
            status = "up to date"
        conn.execute("INSERT INTO sync_run (started_at, finished_at, calendars, added, updated, removed, status, "
                     "error, source_id) VALUES (?,?,?,?,?,?,?,?,?)",
                     (started, finished, s["name"], s["added"], s["updated"], s["removed"], status,
                      s["error"] or s["removals_skipped"], s["id"]))


def source_message(s: dict[str, Any]) -> str:
    if s.get("error") and not s.get("complete", True):
        return f"{s['name']}: {s['error']}"
    if s.get("not_modified") or not (s["added"] or s["updated"] or s["removed"]):
        return f"{s['name']}: up to date"
    return f"{s['name']}: {s['added']} added, {s['updated']} updated, {s['removed']} removed"
