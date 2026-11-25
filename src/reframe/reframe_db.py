"""Agent helper module and CLI (spec 9.2).

Preferred way for an agent to write to the Reframe database. Each write:
  * takes a backup first if the newest one is more than an hour old,
  * runs in one change set (``BEGIN IMMEDIATE``, protocol of spec 5.3),
  * normalizes tags and refuses near-duplicate *new* tags unless told otherwise,
  * validates dates (ISO, with or without dashes),
  * returns likely duplicate items instead of inserting them (unless ``force``).

CLI: python -m reframe_db add-item --kind plan --title "..." --start 2026-11-20 --tag Spares
Every command prints JSON. Use ``--db`` or the REFRAME_DB environment variable
to point at a different database file.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Sequence

from .calendars import sources as sourcemod
from .db import undo as undomod
from .db.build_schema import SCHEMA_VERSION
from .db.changelog import group_external, writer
from .db.connection import connect, default_db_path, init_db
from .domain import backup, history, items, settings, tags


def open_db(path: str | Path | None = None):
    p = Path(path) if path else default_db_path()
    if p.exists():
        c = connect(p)
        try:
            v = c.execute("PRAGMA user_version").fetchone()[0]
        finally:
            c.close()
        if v > SCHEMA_VERSION:
            raise SystemExit(f"Database schema version {v} is newer than this helper ({SCHEMA_VERSION}); stop.")
    conn = init_db(p)
    return conn


def _backup_first(conn) -> str | None:
    folder = settings.get(conn, "backup_folder") or str(backup.default_backup_dir())
    try:
        made = backup.backup_if_stale(conn, Path(folder))
    except OSError as e:  # a missing folder never blocks work
        return f"backup skipped: {e}"
    return str(made) if made else None


def add_items(conn, specs: Sequence[dict[str, Any]], *, label: str, actor: str = "agent",
              force: bool = False, allow_similar_tags: bool = False, allow_retired: bool = False) -> dict[str, Any]:
    """Add several items in ONE change set. Returns created ids, skipped duplicates, and the set id.

    Each spec: kind ('plan'|'log'), title, start, end (optional), tags (list), notes, attachments.
    """
    backup_note = _backup_first(conn)
    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    with writer(conn, actor, label) as w:
        for spec in specs:
            start = spec.get("start") or spec.get("start_date")
            end = spec.get("end") or spec.get("end_date")
            if not force:
                dups = items.find_duplicates(conn, spec["title"], start, end)
                if dups:
                    skipped.append({"spec": spec, "reason": "likely duplicate", "existing": dups})
                    continue
            iid = items.create_item(
                conn, title=spec["title"], start=start, end=end, kind=spec.get("kind", "plan"),
                notes=spec.get("notes", ""), tags=spec.get("tags", ()), attachments=spec.get("attachments", ()),
                allow_near=allow_similar_tags, allow_retired=allow_retired)
            created.append({"id": iid, "title": spec["title"], "kind": spec.get("kind", "plan"),
                            "start": items.parse_date(start), "end": items.parse_date(end or start)})
        cs = w.change_set_id
    return {"change_set_id": cs or None, "created": created, "skipped": skipped, "backup": backup_note,
            "undo": "python -m reframe_db undo" if created else None}


def update_item(conn, item_id: str, *, actor: str = "agent", label: str | None = None,
                tags_: Sequence[str] | None = None, allow_similar_tags: bool = False, allow_retired: bool = False,
                **fields: Any) -> dict[str, Any]:
    """Update only the given columns (and optionally replace tags)."""
    _backup_first(conn)
    changed: list[str] = []
    with writer(conn, actor, label or f"Update item {item_id[:8]}"):
        changed = items.update_item(conn, item_id, **fields)
        if tags_ is not None:
            items.set_item_tags(conn, item_id, list(tags_), allow_near=allow_similar_tags, allow_retired=allow_retired)
            changed.append("tags")
    return {"item": item_id, "changed": changed}


def soft_delete(conn, item_ids: Sequence[str], *, actor: str = "agent", label: str | None = None) -> dict[str, int]:
    _backup_first(conn)
    with writer(conn, actor, label or f"Delete {len(item_ids)} item(s)"):
        return items.delete_items(conn, item_ids)


def tag_tree(conn) -> list[dict[str, Any]]:
    return [{k: t[k] for k in ("id", "name", "path", "depth", "parent_id", "usage", "retired")}
            for t in sorted(tags.list_tags(conn), key=lambda t: t["tree_order"])]


def _tag_id(conn, name: str) -> str:
    t = tags.find_tag(conn, name)
    if t is None:
        raise LookupError(f"No tag '{name}'")
    return t["id"]


def move_tag(conn, name: str, group: str | None, *, actor: str = "agent") -> dict[str, Any]:
    _backup_first(conn)
    tid = _tag_id(conn, name)
    gid = _tag_id(conn, group) if group else None
    with writer(conn, actor, f"Move tag '{name}' to " + (f"'{group}'" if group else "the top level")) as w:
        n = tags.move_tag(conn, tid, gid)
    return {"tag": name, "group": group, "items_updated": n, "change_set_id": w.change_set_id or None}


def retire_tag(conn, name: str, retired: bool = True, *, actor: str = "agent") -> dict[str, Any]:
    _backup_first(conn)
    tid = _tag_id(conn, name)
    with writer(conn, actor, ("Retire" if retired else "Un-retire") + f" tag '{name}'") as w:
        tags.set_retired(conn, tid, retired)
    return {"tag": name, "retired": retired, "change_set_id": w.change_set_id or None}


def add_attachment(conn, item_id: str, target: str, *, label: str | None = None, actor: str = "agent") -> dict[str, Any]:
    """Register a link; the running app (or the next launch) captures the file and pins the link (R-160)."""
    _backup_first(conn)
    with writer(conn, actor, f"Add link to item {item_id[:8]}") as w:
        aid = items.add_attachment(conn, item_id, target, label)
    return {"attachment": aid, "change_set_id": w.change_set_id or None,
            "note": "Reframe captures and pins file links itself; do not write file_source or file_version."}


def calendars(conn) -> list[dict[str, Any]]:
    return [{k: s[k] for k in ("id", "kind", "name", "location", "connected", "visible", "is_holiday", "item_count")}
            for s in sourcemod.list_sources(conn)]


def schedule(conn, start: str, end: str, *, kind: str | None = None, tag: str | None = None) -> list[dict[str, Any]]:
    s, e = items.parse_date(start), items.parse_date(end)
    sql = "SELECT id, kind, title, start_date, end_date, origin, tags FROM v_item WHERE end_date >= ? AND start_date <= ?"
    params: list[Any] = [s, e]
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    rows = [dict(r) for r in conn.execute(sql + " ORDER BY start_date, end_date, title", params)]
    if tag:
        key = tags.norm_key(tag)
        rows = [r for r in rows if key in [tags.norm_key(t) for t in (r["tags"] or "").split("; ")]]
    return rows


def search(conn, text: str, limit: int = 50) -> list[dict[str, Any]]:
    like = f"%{text}%"
    return [dict(r) for r in conn.execute(
        "SELECT id, kind, title, start_date, end_date, origin, tags FROM v_item "
        "WHERE title LIKE ? OR notes LIKE ? OR tags LIKE ? ORDER BY start_date DESC LIMIT ?",
        (like, like, like, limit))]


# ---------------------------------------------------------------- CLI

def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def _read_note(args) -> str:
    if getattr(args, "note_from_file", None):
        return Path(args.note_from_file).read_text(encoding="utf-8")
    return getattr(args, "note", None) or ""


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="reframe_db", description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", help="database path (default: reframe.sqlite in the data folder, or $REFRAME_DB)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add-item", help="add one item (refuses likely duplicates unless --force)")
    a.add_argument("--kind", choices=["plan", "log"], default="plan")
    a.add_argument("--title", required=True)
    a.add_argument("--start", required=True, help="ISO date, e.g. 2026-11-20 or 20261120")
    a.add_argument("--end", help="inclusive end date (default = start)")
    a.add_argument("--tag", action="append", default=[], help="repeatable; first tag sets the color")
    a.add_argument("--note")
    a.add_argument("--note-from-file")
    a.add_argument("--label", help="change-set label, e.g. 'Add item from 11/10 staff meeting'")
    a.add_argument("--force", action="store_true")
    a.add_argument("--allow-similar-tags", action="store_true",
                   help="create a new tag even if it nearly matches an existing one")
    a.add_argument("--allow-retired", action="store_true", help="apply a retired tag anyway (owner confirmed)")

    b = sub.add_parser("add-items", help="add many items from a JSON file in ONE change set")
    b.add_argument("--file", required=True, help="JSON list of {kind,title,start,end,tags,notes}")
    b.add_argument("--label", required=True)
    b.add_argument("--force", action="store_true")
    b.add_argument("--allow-similar-tags", action="store_true")
    b.add_argument("--allow-retired", action="store_true")

    u = sub.add_parser("update-item", help="change only the given fields of an item")
    u.add_argument("id")
    u.add_argument("--title")
    u.add_argument("--kind", choices=["plan", "log"])
    u.add_argument("--start")
    u.add_argument("--end")
    u.add_argument("--note")
    u.add_argument("--note-from-file")
    u.add_argument("--tag", action="append", help="replace ALL tags with these (repeatable)")
    u.add_argument("--label")
    u.add_argument("--allow-similar-tags", action="store_true")
    u.add_argument("--allow-retired", action="store_true")

    d = sub.add_parser("delete-item", help="soft delete (recoverable from History / undo)")
    d.add_argument("id", nargs="+")

    f = sub.add_parser("find-duplicates")
    f.add_argument("--title", required=True)
    f.add_argument("--start", required=True)
    f.add_argument("--end")

    s = sub.add_parser("schedule", help="items between two dates")
    s.add_argument("--from", dest="start", required=True)
    s.add_argument("--to", dest="end", required=True)
    s.add_argument("--kind", choices=["plan", "log"])
    s.add_argument("--tag")

    q = sub.add_parser("search", help="search title, notes and tags")
    q.add_argument("text")

    sub.add_parser("tags", help="list tags with usage counts")
    sub.add_parser("tag-tree", help="the tag tree: every tag with its group path, depth, usage and retired flag")
    mt = sub.add_parser("move-tag", help="put a tag into a group (its items get the group tags), or --root")
    mt.add_argument("tag")
    g = mt.add_mutually_exclusive_group(required=True)
    g.add_argument("--to", dest="group")
    g.add_argument("--root", action="store_true")
    rt = sub.add_parser("retire-tag", help="retire a tag: it stays on old items but is no longer offered")
    rt.add_argument("tag")
    ut = sub.add_parser("unretire-tag")
    ut.add_argument("tag")
    at = sub.add_parser("add-attachment", help="link a file, folder or URL to an item (the app protects files)")
    at.add_argument("id")
    at.add_argument("path")
    at.add_argument("--label")
    sub.add_parser("calendars", help="calendar sources with connected/visible flags and item counts")
    t = sub.add_parser("suggest-tags", help="existing tags that match or nearly match a name")
    t.add_argument("name")

    h = sub.add_parser("history", help="recent change sets")
    h.add_argument("--limit", type=int, default=20)
    sub.add_parser("undo", help="undo the most recent change set (by anyone)")
    sub.add_parser("redo")

    args = ap.parse_args(argv)
    conn = open_db(args.db)
    try:
        group_external(conn)
        if args.cmd == "add-item":
            spec = {"kind": args.kind, "title": args.title, "start": args.start, "end": args.end,
                    "tags": args.tag, "notes": _read_note(args)}
            _print(add_items(conn, [spec], label=args.label or f"Add item: {args.title[:60]}",
                             force=args.force, allow_similar_tags=args.allow_similar_tags,
                             allow_retired=args.allow_retired))
        elif args.cmd == "add-items":
            specs = json.loads(Path(args.file).read_text(encoding="utf-8"))
            _print(add_items(conn, specs, label=args.label, force=args.force,
                             allow_similar_tags=args.allow_similar_tags, allow_retired=args.allow_retired))
        elif args.cmd == "update-item":
            fields = {k: v for k, v in (("title", args.title), ("kind", args.kind), ("start_date", args.start),
                                        ("end_date", args.end)) if v is not None}
            note = _read_note(args)
            if args.note is not None or args.note_from_file:
                fields["notes"] = note
            _print(update_item(conn, args.id, label=args.label, tags_=args.tag,
                               allow_similar_tags=args.allow_similar_tags, allow_retired=args.allow_retired, **fields))
        elif args.cmd == "delete-item":
            _print(soft_delete(conn, args.id))
        elif args.cmd == "find-duplicates":
            _print(items.find_duplicates(conn, args.title, args.start, args.end))
        elif args.cmd == "schedule":
            _print(schedule(conn, args.start, args.end, kind=args.kind, tag=args.tag))
        elif args.cmd == "search":
            _print(search(conn, args.text))
        elif args.cmd == "tags":
            _print([{k: t[k] for k in ("name", "usage", "id")} for t in tags.list_tags(conn)])
        elif args.cmd == "tag-tree":
            _print(tag_tree(conn))
        elif args.cmd == "move-tag":
            _print(move_tag(conn, args.tag, None if args.root else args.group))
        elif args.cmd == "retire-tag":
            _print(retire_tag(conn, args.tag, True))
        elif args.cmd == "unretire-tag":
            _print(retire_tag(conn, args.tag, False))
        elif args.cmd == "add-attachment":
            _print(add_attachment(conn, args.id, args.path, label=args.label))
        elif args.cmd == "calendars":
            _print(calendars(conn))
        elif args.cmd == "suggest-tags":
            _print(tags.suggestions(conn, args.name))
        elif args.cmd == "history":
            _print([{k: c[k] for k in ("id", "ts", "actor", "label", "rows", "in_effect")}
                    for c in history.list_change_sets(conn, args.limit)])
        elif args.cmd == "undo":
            _print(undomod.undo(conn))
        elif args.cmd == "redo":
            _print(undomod.redo(conn))
        return 0
    except (ValueError, LookupError, sqlite3.IntegrityError, undomod.NothingToDo, undomod.RevertConflict,
            tags.NearTagMatch) as e:
        _print({"error": type(e).__name__, "message": str(e)})
        return 2
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
