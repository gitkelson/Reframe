"""Reconciliation of attached files against the vault (R-160, R-163, R-168).

Pure functions over a connection and a store; the service (``service.py``) schedules them on worker threads.
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Callable

from . import capture as cap
from .store import VaultStore


def attached_files(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Every distinct path of ``kind='file'`` links on non-deleted items, keyed by ``path_key``."""
    out: dict[str, dict[str, Any]] = {}
    for r in conn.execute(
            "SELECT a.id, a.item_id, a.target, a.pinned_version_id, a.pin_basis, a.created_at, i.title "
            "FROM attachment a JOIN item i ON i.id = a.item_id "
            "WHERE a.kind = 'file' AND i.deleted_at IS NULL ORDER BY a.created_at, a.id"):
        target = (r["target"] or "").strip().strip('"')
        if not target:
            continue
        key = cap.path_key(target)
        entry = out.setdefault(key, {"path": cap.strip_long(target), "links": []})
        entry["links"].append(dict(r))
    return out


def migration_time(conn: sqlite3.Connection) -> str | None:
    """``imported_at`` of a data import (setting ``migrated_from``), or None when none was made."""
    r = conn.execute("SELECT value FROM setting WHERE key='migrated_from'").fetchone()
    if r is None:
        return None
    try:
        v = json.loads(r[0])
    except ValueError:
        return None
    if isinstance(v, dict) and v.get("imported_at"):
        return str(v["imported_at"])
    return None


def _norm_ts(ts: str) -> str:
    """Comparable UTC text: '2026-11-23T10:00:00Z' and '2026-11-23 10:00:00.000' sort alike."""
    return (ts or "").replace(" ", "T").rstrip("Z")


def pin_links(conn: sqlite3.Connection, version_id: str, links: list[dict], path: str,
              migrated_at: str | None) -> list[str]:
    """Pin the unpinned links of one file to ``version_id``: migration-era links in a migration change set,
    the others in a "Protect 'name'" change set."""
    unpinned = [l for l in links if not l.get("pinned_version_id")]
    if not unpinned:
        return []
    mig = [l for l in unpinned if migrated_at and _norm_ts(l["created_at"]) < _norm_ts(migrated_at)]
    rest = [l for l in unpinned if l not in mig]
    done: list[str] = []
    if mig:
        done += cap.pin_attachments(conn, [(l["id"], version_id) for l in mig], "migration", cap.MIGRATION_LABEL)
    if rest:
        done += cap.pin_attachments(conn, [(l["id"], version_id) for l in rest], "attach", cap.protect_label(path))
    return done


def reconcile_path(conn: sqlite3.Connection, store: VaultStore, path: str, links: list[dict], *,
                   deep: bool = True, migrated_at: str | None = None) -> dict[str, Any]:
    """Check one attached file: capture new content, pin unpinned links. Returns a summary dict.

    ``deep=False`` (periodic runs) trusts an unchanged size + mtime for files whose links are all pinned;
    ``deep=True`` (launch, watcher overflow) always hashes the original.
    """
    key = cap.path_key(path)
    src = conn.execute("SELECT * FROM file_source WHERE path_key=?", (key,)).fetchone()
    unpinned = [l for l in links if not l.get("pinned_version_id")]
    res: dict[str, Any] = {"path": path, "status": None, "captured": False, "pinned": [], "error": None}
    if not unpinned and src is not None and src["last_hash"] and store.has(src["last_hash"]):
        p = cap.io_path(path)
        if not deep and src["status"] == cap.PROTECTED:
            try:
                st = os.stat(p)
                if st.st_size == src["last_size"] and cap._mtime_iso(st) == src["last_mtime"]:
                    cap.touch_checked(conn, src["id"])
                    res["status"] = "unchanged"
                    return res
            except OSError:
                pass
        current = cap.hash_original(path)
        if current is not None and current == src["last_hash"]:
            cap.touch_checked(conn, src["id"])
            res["status"] = "unchanged"
            return res
    r = cap.capture_file(conn, store, path)
    res["status"], res["error"] = r.status, r.error
    res["captured"] = r.new_version
    res["version_id"] = r.version_id
    if r.ok:
        res["pinned"] = pin_links(conn, r.version_id, links, path, migrated_at)
    return res


def reconcile_all(conn: sqlite3.Connection, store: VaultStore, *, deep: bool = True,
                  on_path: Callable[[str, dict], None] | None = None,
                  should_stop: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Reconcile every attached file. Picks up links added by agents or raw SQL (R-160)."""
    migrated_at = migration_time(conn)
    summary = {"checked": 0, "captured": 0, "pinned": 0, "failed": []}
    for key, entry in attached_files(conn).items():
        if should_stop and should_stop():
            break
        r = reconcile_path(conn, store, entry["path"], entry["links"], deep=deep, migrated_at=migrated_at)
        summary["checked"] += 1
        summary["captured"] += int(bool(r["captured"]))
        summary["pinned"] += len(r["pinned"])
        if r["status"] not in (cap.PROTECTED, "unchanged"):
            summary["failed"].append({"path": entry["path"], "status": r["status"], "error": r["error"]})
        if on_path:
            on_path(key, r)
    return summary


def unprotected(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """File links on non-deleted items that are not pinned yet, with the reason from their file_source."""
    files = attached_files(conn)
    keys = list(files)
    status: dict[str, sqlite3.Row] = {}
    for i in range(0, len(keys), 500):
        part = keys[i:i + 500]
        marks = ",".join("?" for _ in part)
        for r in conn.execute(f"SELECT path_key, status, last_error, last_check_at FROM file_source "
                              f"WHERE path_key IN ({marks})", part):
            status[r["path_key"]] = r
    out = []
    for key, entry in files.items():
        for l in entry["links"]:
            if l["pinned_version_id"]:
                continue
            s = status.get(key)
            out.append({"attachment_id": l["id"], "item_id": l["item_id"], "item_title": l["title"],
                        "path": entry["path"], "status": s["status"] if s else "pending",
                        "error": s["last_error"] if s else None, "last_check_at": s["last_check_at"] if s else None})
    return out


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    r = conn.execute(
        "SELECT count(*) FILTER (WHERE a.pinned_version_id IS NULL) pending, "
        "count(*) FILTER (WHERE a.pinned_version_id IS NOT NULL) protected "
        "FROM attachment a JOIN item i ON i.id = a.item_id WHERE a.kind='file' AND i.deleted_at IS NULL").fetchone()
    return {"pending": r["pending"] or 0, "protected": r["protected"] or 0}
