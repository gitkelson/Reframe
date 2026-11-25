"""Calendar sources: one row per Outlook calendar folder or ICS file/URL (R-130 .. R-132).

Every source is imported while ``connected``; ``visible`` is the on-demand display switch, a view setting that is
not change-logged (R-131). Disconnecting soft-deletes the source's items in one change set (R-132).
"""
from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from . import ics

NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"

_COLS = ("id, kind, external_id, name, location, is_holiday, connected, visible, refresh_min, etag, last_modified, "
         "last_read_at, last_status, last_error, created_at")


def _public(row: sqlite3.Row | dict[str, Any], counts: dict[str, int] | None = None) -> dict[str, Any]:
    d = dict(row)
    d["is_holiday"], d["connected"], d["visible"] = bool(d["is_holiday"]), bool(d["connected"]), bool(d["visible"])
    d.pop("etag", None)
    d.pop("last_modified", None)
    if d["kind"] == "ics":
        d["display_location"] = ics.mask_url(d["location"])   # secret-URL feeds are never shown in full (R-135)
        d["location"] = d["display_location"]
        d["external_id"] = d["display_location"]
    else:
        d["display_location"] = d["location"]
    if counts is not None:
        d["item_count"] = counts.get(d["id"], 0)
    return d


def list_sources(conn: sqlite3.Connection, *, kind: str | None = None, public: bool = True) -> list[dict[str, Any]]:
    sql = f"SELECT {_COLS} FROM calendar_source"
    params: list[Any] = []
    if kind:
        sql += " WHERE kind=?"
        params.append(kind)
    rows = conn.execute(sql + " ORDER BY kind DESC, lower(name), location", params).fetchall()
    if not public:
        return [dict(r) for r in rows]
    counts = {r[0]: r[1] for r in conn.execute(
        "SELECT source_id, count(*) FROM item WHERE source_id IS NOT NULL AND deleted_at IS NULL AND hidden_at IS NULL "
        "GROUP BY source_id")}
    return [_public(r, counts) for r in rows]


def get(conn: sqlite3.Connection, source_id: str) -> dict[str, Any]:
    r = conn.execute(f"SELECT {_COLS} FROM calendar_source WHERE id=?", (source_id,)).fetchone()
    if r is None:
        raise LookupError(f"No calendar source {source_id}")
    return dict(r)


def _begin(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE")


def _config_write(conn: sqlite3.Connection, fn) -> Any:
    """calendar_source is configuration, not change-logged; written in its own short transaction."""
    own = not conn.in_transaction
    if own:
        _begin(conn)
    try:
        res = fn()
        if own:
            conn.execute("COMMIT")
        return res
    except BaseException:
        if own:
            conn.execute("ROLLBACK")
        raise


def upsert_outlook_folders(conn: sqlite3.Connection, folders: Iterable[Any]) -> list[dict[str, Any]]:
    """Record every Outlook calendar folder; new ones are connected but hidden (R-131). Returns the new sources."""
    def run() -> list[dict[str, Any]]:
        new = []
        for f in folders:
            row = conn.execute("SELECT id, name, location FROM calendar_source WHERE kind='outlook' AND external_id=?",
                               (f.id,)).fetchone()
            if row is None:
                sid = conn.execute(
                    "INSERT INTO calendar_source (kind, external_id, name, location, is_holiday, connected, visible) "
                    "VALUES ('outlook', ?, ?, ?, ?, 1, 0) RETURNING id",
                    (f.id, f.name or "Calendar", f.path or "", int(bool(getattr(f, "looks_like_holidays", False))))
                ).fetchone()[0]
                new.append({"id": sid, "name": f.name, "location": f.path})
            elif (row["name"], row["location"]) != (f.name or "Calendar", f.path or ""):
                conn.execute("UPDATE calendar_source SET name=?, location=? WHERE id=?",
                             (f.name or "Calendar", f.path or "", row["id"]))
        return new
    return _config_write(conn, run)


def add_ics(conn: sqlite3.Connection, location: str, *, name: str | None = None, is_holiday: bool = False,
            visible: bool = True) -> dict[str, Any]:
    """Add an ICS file or URL source (R-135). Connected; shown because the owner just added it on purpose."""
    kind, normalized = ics.normalize_location(location)
    label = (name or "").strip()
    if not label:
        label = normalized.rsplit("\\", 1)[-1] if kind == "file" else ics.mask_url(normalized)

    def run() -> dict[str, Any]:
        row = conn.execute("SELECT id FROM calendar_source WHERE kind='ics' AND external_id=?", (normalized,)).fetchone()
        if row:
            raise ValueError("That calendar is already connected")
        sid = conn.execute(
            "INSERT INTO calendar_source (kind, external_id, name, location, is_holiday, connected, visible) "
            "VALUES ('ics', ?, ?, ?, ?, 1, ?) RETURNING id",
            (normalized, label, normalized, int(is_holiday), int(visible))).fetchone()[0]
        return get(conn, sid)
    return _public(_config_write(conn, run))


def update(conn: sqlite3.Connection, source_id: str, **fields: Any) -> None:
    """Change name, is_holiday, visible or refresh_min (configuration; not change-logged)."""
    allowed = {"name", "is_holiday", "visible", "refresh_min"}
    bad = set(fields) - allowed
    if bad:
        raise ValueError("Cannot change: " + ", ".join(sorted(bad)))
    get(conn, source_id)
    vals = {k: (int(bool(v)) if k in ("is_holiday", "visible") else v) for k, v in fields.items()}
    if "name" in vals and not str(vals["name"]).strip():
        raise ValueError("A name is required")
    if not vals:
        return
    sets = ", ".join(f"{k}=?" for k in vals)
    _config_write(conn, lambda: conn.execute(f"UPDATE calendar_source SET {sets} WHERE id=?", [*vals.values(), source_id]))


def set_all_visible(conn: sqlite3.Connection, visible: bool) -> None:
    _config_write(conn, lambda: conn.execute("UPDATE calendar_source SET visible=?", (int(visible),)))


def set_connected(conn: sqlite3.Connection, source_id: str, connected: bool, *, actor: str = "user") -> dict[str, Any]:
    """Connect or disconnect a source. Call outside any transaction.

    Disconnecting stops importing and soft-deletes the source's items ('removed: calendar disconnected') as one
    undoable change set (R-132); the flag itself is configuration. Reconnecting only sets the flag: the next sync
    restores the same item rows, with their annotations, through their stable keys.
    """
    from ..db.changelog import writer
    src = get(conn, source_id)
    _config_write(conn, lambda: conn.execute("UPDATE calendar_source SET connected=? WHERE id=?",
                                             (int(connected), source_id)))
    if connected:
        return {"removed": 0, "change_set_id": None}
    with writer(conn, actor, f"Removed: calendar disconnected ({src['name']})") as w:
        cur = conn.execute(f"UPDATE item SET deleted_at = {NOW_SQL}, updated_at = {NOW_SQL} "
                           "WHERE source_id=? AND deleted_at IS NULL", (source_id,))
        n = cur.rowcount
    return {"removed": n, "change_set_id": w.change_set_id or None}


def remove(conn: sqlite3.Connection, source_id: str) -> None:
    """Forget a disconnected ICS source with no live items."""
    src = get(conn, source_id)
    if src["connected"]:
        raise ValueError("Disconnect the calendar first")
    if conn.execute("SELECT 1 FROM item WHERE source_id=? LIMIT 1", (source_id,)).fetchone():
        raise ValueError("Its items are kept in History; the source stays listed while they exist")
    _config_write(conn, lambda: conn.execute("DELETE FROM calendar_source WHERE id=?", (source_id,)))


def record_read(conn: sqlite3.Connection, source_id: str, *, status: str, error: str | None = None,
                etag: str | None = None, last_modified: str | None = None, keep_validators: bool = False) -> None:
    def run() -> None:
        if keep_validators:
            conn.execute(f"UPDATE calendar_source SET last_read_at={NOW_SQL}, last_status=?, last_error=? WHERE id=?",
                         (status, error, source_id))
        else:
            conn.execute(f"UPDATE calendar_source SET last_read_at={NOW_SQL}, last_status=?, last_error=?, etag=?, "
                         "last_modified=? WHERE id=?", (status, error, etag, last_modified, source_id))
    _config_write(conn, run)


def visibility_summary(conn: sqlite3.Connection) -> dict[str, int]:
    r = conn.execute("SELECT count(*), sum(visible) FROM calendar_source WHERE connected=1").fetchone()
    return {"connected": r[0] or 0, "visible": r[1] or 0}
