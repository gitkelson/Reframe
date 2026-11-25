"""Item, tag-assignment and attachment operations (spec sections 2, 3).

Every function here that writes expects an open ``changelog.writer`` session on
``conn`` (a transaction is already open). Read functions work on any connection.
"""
from __future__ import annotations

import datetime as dt
import difflib
import os
import re
import sqlite3
from typing import Any, Iterable, Sequence

from . import tags as tagmod

NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"
CORE_FIELDS = ("title", "start_date", "end_date", "kind")
EDITABLE_FIELDS = ("kind", "title", "start_date", "end_date", "notes")


class DateError(ValueError):
    pass


class LockedItem(ValueError):
    """Raised when core fields of an imported item are edited (R-16)."""

    MESSAGE = "Imported from Outlook. Change it there, then Sync."

    def __init__(self, msg: str | None = None):
        super().__init__(msg or self.MESSAGE)

    @classmethod
    def for_item(cls, conn: sqlite3.Connection, row: sqlite3.Row) -> "LockedItem":
        if row["origin"] == "ics":
            src = conn.execute("SELECT name FROM calendar_source WHERE id=?", (row["source_id"],)).fetchone()
            return cls(f"Imported from calendar feed {src[0] if src else ''}. Change it at the source, then Sync.")
        return cls()


class NotFound(LookupError):
    pass


_DATE_RE = re.compile(r"^(\d{4})-?(\d{2})-?(\d{2})$")


def parse_date(text: str) -> str:
    """ISO date, with or without dashes (R-100). Returns 'YYYY-MM-DD'."""
    m = _DATE_RE.match((text or "").strip())
    if not m:
        raise DateError("Use a date like 2026-11-08 or 20261108")
    try:
        return dt.date(int(m[1]), int(m[2]), int(m[3])).isoformat()
    except ValueError:
        raise DateError(f"{text.strip()} is not a real date") from None


def add_days(iso: str, n: int) -> str:
    return (dt.date.fromisoformat(iso) + dt.timedelta(days=n)).isoformat()


def today_iso() -> str:
    return dt.date.today().isoformat()


# ---------------------------------------------------------------- reads

def _item_row(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    d["locked"] = d["origin"] != "local"
    return d


_LIST_COLS = ("id, kind, title, start_date, end_date, notes, origin, source_id, outlook_time, outlook_location, "
              "outlook_calendar, outlook_series_id, updated_at")
_VALID = "date(start_date) IS start_date AND date(end_date) IS end_date AND end_date >= start_date"


def fetch_items(conn: sqlite3.Connection, start: str | None = None, end: str | None = None) -> list[dict[str, Any]]:
    """Visible items (not deleted, not hidden) overlapping [start, end], with ordered tag ids."""
    sql = f"SELECT {_LIST_COLS} FROM item WHERE deleted_at IS NULL AND hidden_at IS NULL AND {_VALID}"
    params: list[Any] = []
    if start:
        sql += " AND end_date >= ?"
        params.append(parse_date(start))
    if end:
        sql += " AND start_date <= ?"
        params.append(parse_date(end))
    sql += " ORDER BY start_date, end_date DESC, id"
    items = [_item_row(r) for r in conn.execute(sql, params)]
    by_id = {i["id"]: i for i in items}
    for i in items:
        i["tags"] = []
    for r in conn.execute(
            """SELECT it.item_id, it.tag_id FROM item_tag it JOIN tag t ON t.id = it.tag_id
               ORDER BY it.position, t.name"""):
        it = by_id.get(r["item_id"])
        if it is not None:
            it["tags"].append(r["tag_id"])
    return items


def get_item(conn: sqlite3.Connection, item_id: str) -> dict[str, Any]:
    r = conn.execute("SELECT * FROM item WHERE id=?", (item_id,)).fetchone()
    if r is None:
        raise NotFound(item_id)
    d = _item_row(r)
    d["tags"] = [dict(t) for t in conn.execute(
        """SELECT t.id, t.name, t.color_slot, t.parent_id, t.retired_at IS NOT NULL AS retired, it.position, it.implied
             FROM item_tag it JOIN tag t ON t.id = it.tag_id
            WHERE it.item_id=? ORDER BY it.position, t.name""", (item_id,))]
    by_id = {t["id"]: t for t in d["tags"]}
    for t in d["tags"]:   # the explicit tag that put an implied group tag here ("Added by group child")
        t["implied_by"] = None
        if t["implied"]:
            for other in d["tags"]:
                if not other["implied"] and t["id"] in tagmod.ancestors(conn, other["id"]):
                    t["implied_by"] = other["name"]
                    break
    del by_id
    src = conn.execute("SELECT id, kind, name, location FROM calendar_source WHERE id=?",
                       (d.get("source_id"),)).fetchone() if d.get("source_id") else None
    d["source"] = dict(src) if src else None
    d["attachments"] = [dict(a) for a in conn.execute(
        "SELECT id, kind, target, label, pinned_version_id, pin_basis, created_at FROM attachment "
        "WHERE item_id=? ORDER BY created_at, id",
        (item_id,))]
    d["series_size"] = (conn.execute(
        "SELECT count(*) FROM item WHERE outlook_series_id=? AND deleted_at IS NULL", (d["outlook_series_id"],)
    ).fetchone()[0] if d["outlook_series_id"] else 0)
    d["identical_in_calendar"] = (conn.execute(
        "SELECT count(*) FROM item WHERE origin<>'local' AND id<>? AND deleted_at IS NULL AND hidden_at IS NULL "
        "AND source_id IS ? AND title=? AND start_date=? AND end_date=?",
        (item_id, d["source_id"], d["title"], d["start_date"], d["end_date"])).fetchone()[0]
        if d["origin"] != "local" else 0)
    return d


def _require(conn: sqlite3.Connection, item_id: str) -> sqlite3.Row:
    r = conn.execute("SELECT * FROM item WHERE id=?", (item_id,)).fetchone()
    if r is None:
        raise NotFound(f"No item {item_id}")
    return r


# --------------------------------------------------------------- creates / edits

def create_item(conn: sqlite3.Connection, *, title: str, start: str, end: str | None = None,
                kind: str = "plan", notes: str = "", tags: Sequence[str] = (),
                attachments: Sequence[dict[str, Any]] = (), allow_near: bool = True,
                allow_retired: bool = False) -> str:
    title = (title or "").strip()
    if not title:
        raise ValueError("Title is required")
    if kind not in ("plan", "log"):
        raise ValueError("kind must be 'plan' or 'log'")
    s = parse_date(start)
    e = parse_date(end) if end else s
    if e < s:
        raise DateError("End date is before start date")
    item_id = conn.execute(
        "INSERT INTO item (kind, title, start_date, end_date, notes) VALUES (?,?,?,?,?) RETURNING id",
        (kind, title, s, e, notes or "")).fetchone()[0]
    if tags:
        set_item_tags(conn, item_id, list(tags), allow_near=allow_near, allow_retired=allow_retired, reorder=True)
    for a in attachments:
        add_attachment(conn, item_id, a["target"], a.get("label"))
    return item_id


def update_item(conn: sqlite3.Connection, item_id: str, **fields: Any) -> list[str]:
    """Update only the columns that actually change. Returns the changed field names."""
    cur = _require(conn, item_id)
    unknown = set(fields) - set(EDITABLE_FIELDS)
    if unknown:
        raise ValueError(f"Cannot edit: {', '.join(sorted(unknown))}")
    new: dict[str, Any] = {}
    for k, v in fields.items():
        if k in ("start_date", "end_date"):
            v = parse_date(v)
        elif k == "title":
            v = (v or "").strip()
            if not v:
                raise ValueError("Title is required")
        elif k == "kind" and v not in ("plan", "log"):
            raise ValueError("kind must be 'plan' or 'log'")
        elif k == "notes":
            v = v or ""
        if cur[k] != v:
            new[k] = v
    if not new:
        return []
    if cur["origin"] != "local" and any(k in CORE_FIELDS for k in new):
        raise LockedItem.for_item(conn, cur)
    s = new.get("start_date", cur["start_date"])
    e = new.get("end_date", cur["end_date"])
    if e < s:
        raise DateError("End date is before start date")
    sets = ", ".join(f"{k} = ?" for k in new)
    conn.execute(f"UPDATE item SET {sets}, updated_at = {NOW_SQL} WHERE id = ?", [*new.values(), item_id])
    return list(new)


def set_dates(conn: sqlite3.Connection, item_id: str, start: str, end: str) -> list[str]:
    return update_item(conn, item_id, start_date=start, end_date=end)


def move_items(conn: sqlite3.Connection, ids: Iterable[str], days: int) -> dict[str, int]:
    """Shift whole items by ``days``; imported items are skipped (R-101)."""
    moved = skipped = 0
    for item_id in ids:
        r = _require(conn, item_id)
        if r["origin"] != "local":
            skipped += 1
            continue
        if days:
            conn.execute(
                f"UPDATE item SET start_date = date(start_date, ?), end_date = date(end_date, ?), "
                f"updated_at = {NOW_SQL} WHERE id = ?", (f"{days:+d} days", f"{days:+d} days", item_id))
        moved += 1
    return {"moved": moved, "skipped": skipped}


def shift_edge(conn: sqlite3.Connection, ids: Iterable[str], which: str, days: int) -> dict[str, int]:
    """Move the start or end date of several items (an item never shrinks below one day)."""
    if which not in ("start", "end"):
        raise ValueError("which must be 'start' or 'end'")
    changed = skipped = 0
    for item_id in ids:
        r = _require(conn, item_id)
        if r["origin"] != "local":
            skipped += 1
            continue
        s, e = r["start_date"], r["end_date"]
        if which == "end":
            e = max(s, add_days(e, days))
        else:
            s = min(e, add_days(s, days))
        if (s, e) != (r["start_date"], r["end_date"]):
            conn.execute(f"UPDATE item SET start_date = ?, end_date = ?, updated_at = {NOW_SQL} WHERE id = ?",
                         (s, e, item_id))
            changed += 1
    return {"changed": changed, "skipped": skipped}


def delete_items(conn: sqlite3.Connection, ids: Iterable[str]) -> dict[str, int]:
    """Soft delete (R-83). Imported items are skipped; hide them instead (R-66)."""
    deleted = skipped = 0
    for item_id in ids:
        r = _require(conn, item_id)
        if r["origin"] != "local":
            skipped += 1
            continue
        if r["deleted_at"] is None:
            conn.execute(f"UPDATE item SET deleted_at = {NOW_SQL}, updated_at = {NOW_SQL} WHERE id = ?", (item_id,))
            deleted += 1
    return {"deleted": deleted, "skipped": skipped}


def restore_item(conn: sqlite3.Connection, item_id: str) -> None:
    _require(conn, item_id)
    conn.execute(f"UPDATE item SET deleted_at = NULL, updated_at = {NOW_SQL} WHERE id = ?", (item_id,))


def set_kind(conn: sqlite3.Connection, ids: Iterable[str], kind: str) -> int:
    n = 0
    for item_id in ids:
        if _require(conn, item_id)["origin"] != "local":
            continue   # the kind of an imported item is locked (R-16)
        if update_item(conn, item_id, kind=kind):
            n += 1
    return n


# ---------------------------------------------------------------- hide (R-66)

def hide_item(conn: sqlite3.Connection, item_id: str, *, series: bool = False) -> int:
    r = _require(conn, item_id)
    if r["origin"] == "local":
        raise ValueError("Only imported items can be hidden; delete local items instead")
    if series and r["outlook_series_id"]:
        conn.execute(f"INSERT OR IGNORE INTO outlook_hidden_series (series_id, hidden_at) VALUES (?, {NOW_SQL})",
                     (r["outlook_series_id"],))
        cur = conn.execute(
            f"UPDATE item SET hidden_at = {NOW_SQL}, updated_at = {NOW_SQL} "
            "WHERE outlook_series_id = ? AND hidden_at IS NULL", (r["outlook_series_id"],))
    else:
        cur = conn.execute(f"UPDATE item SET hidden_at = {NOW_SQL}, updated_at = {NOW_SQL} "
                           "WHERE id = ? AND hidden_at IS NULL", (item_id,))
    return cur.rowcount


def unhide_item(conn: sqlite3.Connection, item_id: str, *, series: bool = False) -> int:
    r = _require(conn, item_id)
    if series and r["outlook_series_id"]:
        conn.execute("DELETE FROM outlook_hidden_series WHERE series_id = ?", (r["outlook_series_id"],))
        cur = conn.execute(f"UPDATE item SET hidden_at = NULL, updated_at = {NOW_SQL} "
                           "WHERE outlook_series_id = ? AND hidden_at IS NOT NULL", (r["outlook_series_id"],))
    else:
        cur = conn.execute(f"UPDATE item SET hidden_at = NULL, updated_at = {NOW_SQL} "
                           "WHERE id = ? AND hidden_at IS NOT NULL", (item_id,))
    return cur.rowcount


# --------------------------------------------------------------------- tags

class TagRequired(ValueError):
    """Removing an implied group tag while a tag inside that group remains (R-111)."""

    def __init__(self, tag: str, child: str):
        self.tag, self.child = tag, child
        super().__init__(f"Remove {child} first, or move it out of the group.")


def _item_tags(conn: sqlite3.Connection, item_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT t.id, t.name, t.norm_key, t.retired_at, it.position, it.implied
             FROM item_tag it JOIN tag t ON t.id = it.tag_id
            WHERE it.item_id=? ORDER BY it.position, t.name""", (item_id,)).fetchall()


def _depth(conn: sqlite3.Connection, tag_id: str) -> int:
    return len(tagmod.ancestors(conn, tag_id))


def add_system_tag(conn: sqlite3.Connection, item_id: str, name: str) -> None:
    """System tags (meeting, holiday) are inserted after the user's tags (R-45); only an insertion default."""
    tag_id, _ = tagmod.get_or_create_tag(conn, name)
    conn.execute("INSERT OR IGNORE INTO item_tag (item_id, tag_id, position) VALUES (?,?,?)",
                 (item_id, tag_id, tagmod.SYSTEM_TAG_POSITION))


def _resolve(conn: sqlite3.Connection, names: Sequence[str], *, have: set[str], allow_near: bool = True,
             allow_retired: bool = False) -> list[str]:
    """Tag ids for ``names`` in order, creating new (groupless) tags. Retired tags may stay where they already
    are but are never newly applied unless ``allow_retired`` (R-113)."""
    out: list[str] = []
    for n in names:
        existing = tagmod.find_tag(conn, n)
        if existing is not None and existing["retired_at"] and existing["id"] not in have and not allow_retired:
            raise tagmod.RetiredTag(existing["name"])
        tag_id = existing["id"] if existing is not None else tagmod.get_or_create_tag(conn, n, allow_near=allow_near)[0]
        if tag_id not in out:
            out.append(tag_id)
    return out


def _apply_tags(conn: sqlite3.Connection, item_id: str, desired: list[str], *, reorder: bool | None = None,
                append_last: bool = False) -> None:
    """Make the item carry exactly the tags ``desired`` (display order; explicit and implied).

    * A tag that is currently implied stays implied; a new tag is explicit. Ancestors of explicit tags are added
      as implied by database triggers (R-111).
    * Removing a tag that a remaining explicit tag requires: an implied one is refused (``TagRequired``), an
      explicit one becomes implied (it stays as the group of the remaining tag).
    * ``reorder``: when the relative order of tags that stay changed (or ``reorder=True``) the order is stored
      exactly as given, positions 0..n-1, for every tag including system and implied tags (R-114). Otherwise
      existing positions are kept and new tags go after the explicit non-system tags (R-45), or after every tag
      with ``append_last`` (R-115).
    """
    rows = _item_tags(conn, item_id)
    cur = {r["id"]: r for r in rows}
    want = set(desired)
    names = {r["id"]: r["name"] for r in rows}
    remaining_explicit = [t for t in desired if t not in cur or not cur[t]["implied"]]
    required: dict[str, str] = {}
    for t in remaining_explicit:
        for a in tagmod.ancestors(conn, t):
            required.setdefault(a, t)
    removed = sorted((t for t in cur if t not in want), key=lambda t: -_depth(conn, t))
    for t in removed:
        if t in required:
            if cur[t]["implied"]:
                child = names.get(required[t]) or conn.execute("SELECT name FROM tag WHERE id=?",
                                                                (required[t],)).fetchone()[0]
                raise TagRequired(names[t], child)
            conn.execute("UPDATE item_tag SET implied=1 WHERE item_id=? AND tag_id=?", (item_id, t))
        elif not cur[t]["implied"]:
            conn.execute("DELETE FROM item_tag WHERE item_id=? AND tag_id=?", (item_id, t))
    for t in removed:   # implied rows a trigger did not already drop
        if t not in required and cur[t]["implied"]:
            conn.execute("DELETE FROM item_tag WHERE item_id=? AND tag_id=? AND implied=1", (item_id, t))
    if reorder is None:
        kept_before = [r["id"] for r in rows if r["id"] in want]
        kept_after = [t for t in desired if t in cur]
        reorder = kept_before != kept_after
    for t in desired:
        if t in cur:
            continue
        now = conn.execute("SELECT implied FROM item_tag WHERE item_id=? AND tag_id=?", (item_id, t)).fetchone()
        if now is not None:
            if now["implied"]:
                conn.execute("UPDATE item_tag SET implied=0 WHERE item_id=? AND tag_id=?", (item_id, t))
            continue
        if append_last:
            pos = conn.execute("SELECT coalesce(max(position), -1) + 1 FROM item_tag WHERE item_id=?",
                               (item_id,)).fetchone()[0]
        else:
            pos = conn.execute("SELECT coalesce(max(position), -1) + 1 FROM item_tag WHERE item_id=? AND position < ?",
                               (item_id, tagmod.SYSTEM_TAG_POSITION)).fetchone()[0]
        conn.execute("INSERT INTO item_tag (item_id, tag_id, position) VALUES (?,?,?)", (item_id, t, pos))
    if reorder:
        _renumber(conn, item_id, desired)


def _renumber(conn: sqlite3.Connection, item_id: str, order: list[str]) -> None:
    """Store the order exactly: positions 0..n-1, then any other row (e.g. implied tags just added) after it."""
    current = {r["id"]: r["position"] for r in _item_tags(conn, item_id)}
    rest = [t for t in current if t not in order]
    for i, t in enumerate([t for t in order if t in current] + rest):
        if current[t] != i:
            conn.execute("UPDATE item_tag SET position=? WHERE item_id=? AND tag_id=?", (i, item_id, t))


def _series_of(item: sqlite3.Row) -> str | None:
    return item["outlook_series_id"] if item["origin"] in ("outlook", "ics") else None


def _propagate_series(conn: sqlite3.Connection, source_id: str, series: str) -> int:
    """Copy the source occurrence's tags (explicit set, order and implied positions) to the series (R-47)."""
    rows = _item_tags(conn, source_id)
    explicit = [(r["id"], r["position"]) for r in rows if not r["implied"]]
    positions = {r["id"]: r["position"] for r in rows}
    keep = {t for t, _ in explicit}
    for r in conn.execute("SELECT tag_id FROM outlook_series_tag WHERE series_id=?", (series,)).fetchall():
        if r["tag_id"] not in keep:
            conn.execute("DELETE FROM outlook_series_tag WHERE series_id=? AND tag_id=?", (series, r["tag_id"]))
    for t, pos in explicit:
        conn.execute("INSERT INTO outlook_series_tag (series_id, tag_id, position) VALUES (?,?,?) "
                     "ON CONFLICT(series_id, tag_id) DO UPDATE SET position=excluded.position "
                     "WHERE position <> excluded.position", (series, t, pos))
    members = [r["id"] for r in conn.execute(
        "SELECT id FROM item WHERE outlook_series_id=? AND deleted_at IS NULL AND id<>?", (series, source_id))]
    for m in members:
        _match_explicit(conn, m, explicit, positions)
    return len(members) + 1


def _match_explicit(conn: sqlite3.Connection, item_id: str, explicit: list[tuple[str, int]],
                    positions: dict[str, int]) -> None:
    want = {t for t, _ in explicit}
    required = {a for t in want for a in tagmod.ancestors(conn, t)}
    rows = _item_tags(conn, item_id)
    for r in sorted((r for r in rows if not r["implied"] and r["id"] not in want), key=lambda r: -_depth(conn, r["id"])):
        if r["id"] in required:
            conn.execute("UPDATE item_tag SET implied=1 WHERE item_id=? AND tag_id=?", (item_id, r["id"]))
        else:
            conn.execute("DELETE FROM item_tag WHERE item_id=? AND tag_id=?", (item_id, r["id"]))
    for t, pos in explicit:
        now = conn.execute("SELECT implied FROM item_tag WHERE item_id=? AND tag_id=?", (item_id, t)).fetchone()
        if now is None:
            conn.execute("INSERT INTO item_tag (item_id, tag_id, position) VALUES (?,?,?)", (item_id, t, pos))
        elif now["implied"]:
            conn.execute("UPDATE item_tag SET implied=0 WHERE item_id=? AND tag_id=?", (item_id, t))
    for r in _item_tags(conn, item_id):
        pos = positions.get(r["id"])
        if pos is not None and pos != r["position"]:
            conn.execute("UPDATE item_tag SET position=? WHERE item_id=? AND tag_id=?", (pos, item_id, r["id"]))


def set_item_tags(conn: sqlite3.Connection, item_id: str, names: Sequence[str], *, allow_near: bool = True,
                  allow_retired: bool = False, reorder: bool | None = None, append_last: bool = False) -> dict[str, Any]:
    """Replace an item's tags with ``names`` in display order (explicit and implied, R-111, R-114).

    For an imported recurring item the tags apply to the whole series (R-47) and are recorded in
    ``outlook_series_tag``.
    """
    item = _require(conn, item_id)
    have = {r["id"] for r in _item_tags(conn, item_id)}
    desired = _resolve(conn, names, have=have, allow_near=allow_near, allow_retired=allow_retired)
    _apply_tags(conn, item_id, desired, reorder=reorder, append_last=append_last)
    series = _series_of(item)
    if not series:
        return {"items": 1}
    return {"items": _propagate_series(conn, item_id, series), "series": True}


def add_tag(conn: sqlite3.Connection, item_ids: Iterable[str], name: str, *, allow_retired: bool = False) -> dict[str, int]:
    """Append one tag after each item's tags without changing the primary tag (R-115)."""
    added = already = 0
    done_series: set[str] = set()
    for item_id in item_ids:
        item = _require(conn, item_id)
        series = _series_of(item)
        if series and series in done_series:
            continue
        rows = _item_tags(conn, item_id)
        tag = tagmod.find_tag(conn, name)
        if tag is not None and any(r["id"] == tag["id"] and not r["implied"] for r in rows):
            already += 1
            continue
        set_item_tags(conn, item_id, [r["name"] for r in rows] + [name], allow_retired=allow_retired,
                      reorder=False, append_last=True)
        if series:
            done_series.add(series)
        added += 1
    return {"added": added, "already": already}


def edit_tags_bulk(conn: sqlite3.Connection, ids: Iterable[str], *, add: Sequence[str] = (),
                   remove: Sequence[str] = (), allow_near: bool = True) -> int:
    """Add and/or remove tags on several items (R-101). Works for imported items too."""
    add_ids = [tagmod.get_or_create_tag(conn, n, allow_near=allow_near)[0] for n in add]
    rm_keys = {tagmod.norm_key(n) for n in remove}
    n = 0
    for item_id in ids:
        cur = _item_tags(conn, item_id)
        names = [r["name"] for r in cur if r["norm_key"] not in rm_keys]
        have = {r["id"] for r in cur}
        for tid in add_ids:
            if tid not in have:
                names.append(conn.execute("SELECT name FROM tag WHERE id=?", (tid,)).fetchone()[0])
        before = {(r["id"], r["implied"]) for r in cur}
        set_item_tags(conn, item_id, names, allow_near=True, reorder=False)
        after = {(r["id"], r["implied"]) for r in _item_tags(conn, item_id)}
        if before != after:
            n += 1
    return n


def copy_tags(conn: sqlite3.Connection, source_id: str, target_ids: Iterable[str]) -> dict[str, int]:
    """Give each target every explicit tag the source has (R-48).

    A target keeps its own tags first; the source's missing tags follow in the source's order, so an untagged
    target ends up with exactly the source's tags and the same primary (color) tag. Implied group tags follow
    automatically. Works on imported targets too, where the tags apply to the whole series (R-47).
    """
    source_tags = [r for r in _item_tags(conn, source_id) if not r["implied"]]
    if not source_tags:
        raise ValueError("That item has no tags to copy")
    changed = skipped = 0
    for target_id in target_ids:
        if target_id == source_id:
            skipped += 1
            continue
        current = _item_tags(conn, target_id)
        explicit = {r["id"] for r in current if not r["implied"]}
        missing = [r["name"] for r in source_tags if r["id"] not in explicit]
        if not missing:
            skipped += 1
            continue
        if not current:
            set_item_tags(conn, target_id, [r["name"] for r in source_tags], reorder=True, allow_retired=True)
        else:
            set_item_tags(conn, target_id, [r["name"] for r in current] + missing, reorder=False, allow_retired=True)
        changed += 1
    return {"changed": changed, "skipped": skipped, "tag_count": len(source_tags)}


def duplicate_item(conn: sqlite3.Connection, source_id: str, start: str, title: str = "New item") -> str:
    """A copy of ``source_id`` starting on ``start`` (R-49): everything except the title and calendar identity.

    Keeps the kind, the length in days, the notes, the tags in their order (system tags such as ``meeting``
    become ordinary tags at their current order) and the attachment links pinned to the same versions (R-161).
    The copy is always a local item, so it is fully editable even when the source was imported.
    """
    src = _require(conn, source_id)
    s = parse_date(start)
    length = (dt.date.fromisoformat(src["end_date"]) - dt.date.fromisoformat(src["start_date"])).days
    names = [r["name"] for r in _item_tags(conn, source_id) if not r["implied"]]
    new_id = create_item(conn, title=title, start=s, end=add_days(s, length), kind=src["kind"],
                         notes=src["notes"], tags=names, allow_retired=True)
    for a in conn.execute("SELECT kind, target, label, pinned_version_id FROM attachment WHERE item_id=? "
                          "ORDER BY created_at, id", (source_id,)).fetchall():
        conn.execute("INSERT INTO attachment (item_id, kind, target, label, pinned_version_id, pin_basis) "
                     "VALUES (?,?,?,?,?,?)",
                     (new_id, a["kind"], a["target"], a["label"], a["pinned_version_id"],
                      "duplicate" if a["pinned_version_id"] else None))
    return new_id


def apply_series_tags(conn: sqlite3.Connection, item_id: str, series_id: str) -> None:
    """Copy a series' tags onto a newly imported occurrence, in the owner's order (R-47, R-114)."""
    for r in conn.execute("SELECT tag_id, position FROM outlook_series_tag WHERE series_id=? ORDER BY position",
                          (series_id,)).fetchall():
        now = conn.execute("SELECT position, implied FROM item_tag WHERE item_id=? AND tag_id=?",
                           (item_id, r["tag_id"])).fetchone()
        if now is None:
            conn.execute("INSERT INTO item_tag (item_id, tag_id, position) VALUES (?,?,?)",
                         (item_id, r["tag_id"], r["position"]))
        elif now["position"] != r["position"] or now["implied"]:
            conn.execute("UPDATE item_tag SET position=?, implied=0 WHERE item_id=? AND tag_id=?",
                         (r["position"], item_id, r["tag_id"]))


# -------------------------------------------------------------- attachments

def detect_attachment_kind(target: str) -> str:
    """'url', 'folder' (an existing directory) or 'file' (R-71)."""
    t = target.strip()
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]{1,}://", t) or t.lower().startswith("mailto:"):
        return "url"
    try:
        if os.path.isdir(t):
            return "folder"
    except OSError:
        pass
    return "file"


def add_attachment(conn: sqlite3.Connection, item_id: str, target: str, label: str | None = None) -> str:
    _require(conn, item_id)
    target = (target or "").strip().strip('"')
    if not target:
        raise ValueError("A file path or URL is required")
    return conn.execute("INSERT INTO attachment (item_id, kind, target, label) VALUES (?,?,?,?) RETURNING id",
                        (item_id, detect_attachment_kind(target), target, (label or "").strip() or None)).fetchone()[0]


def remove_attachment(conn: sqlite3.Connection, attachment_id: str) -> None:
    conn.execute("DELETE FROM attachment WHERE id=?", (attachment_id,))


# -------------------------------------------------------------- duplicates

def find_duplicates(conn: sqlite3.Connection, title: str, start: str, end: str | None = None,
                    threshold: float = 0.8) -> list[dict[str, Any]]:
    """Likely duplicates: overlapping dates and a similar or containing title (spec 9.2)."""
    s = parse_date(start)
    e = parse_date(end) if end else s
    t = " ".join(title.lower().split())
    out = []
    for r in conn.execute(
            "SELECT id, kind, title, start_date, end_date, origin FROM item "
            "WHERE deleted_at IS NULL AND end_date >= ? AND start_date <= ?", (s, e)):
        rt = " ".join(r["title"].lower().split())
        score = difflib.SequenceMatcher(None, t, rt).ratio()
        if t and rt and (t in rt or rt in t):
            score = max(score, 0.9)
        if score >= threshold:
            d = dict(r)
            d["similarity"] = round(score, 2)
            out.append(d)
    out.sort(key=lambda d: -d["similarity"])
    return out


# --------------------------------------------------------- needs attention

def needs_attention(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    """Rows that violate expectations because a tool bypassed constraints (spec 8.5)."""
    bad_dates = [dict(r) for r in conn.execute(
        f"SELECT id, title, start_date, end_date FROM item WHERE NOT ({_VALID})")]
    orphan_tags = [dict(r) for r in conn.execute(
        "SELECT item_id, tag_id FROM item_tag WHERE item_id NOT IN (SELECT id FROM item) "
        "OR tag_id NOT IN (SELECT id FROM tag)")]
    orphan_att = [dict(r) for r in conn.execute(
        "SELECT id, item_id, target FROM attachment WHERE item_id NOT IN (SELECT id FROM item)")]
    return {"bad_dates": bad_dates, "orphan_item_tags": orphan_tags, "orphan_attachments": orphan_att}
