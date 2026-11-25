"""Opening pinned versions, version history and restore (R-162, R-164).

A pinned link opens its original only when the original's bytes hash to the pinned version (modification time is
never trusted). Otherwise the pinned version is written, verified, to ``<data_dir>/tmp/<random>/<name>`` as a
read-only file with a safe extension and opened from there. Temporary copies are removed on exit and at launch.
"""
from __future__ import annotations

import datetime as dt
import os
import secrets
import shutil
import sqlite3
import stat
from pathlib import Path
from typing import Any

from . import capture as cap
from .store import VaultError, VaultStore

# Extensions Windows would execute or interpret instead of viewing: a saved copy gets '.txt' appended.
UNSAFE_EXT = frozenset("""
.exe .com .bat .cmd .ps1 .psm1 .psd1 .ps1xml .psc1 .vbs .vbe .js .jse .wsf .wsh .wsc .hta .msi .msp .mst .lnk
.url .scr .pif .cpl .reg .inf .msc .jar .application .appref-ms .scf .gadget .settingcontent-ms .library-ms
.appx .appxbundle .msix .msixbundle .diagcab .sct .chm
""".split())

NOTICE_DEFS = {"current": "the file at its original location now",
               "latest": "the most recently captured version in the vault"}


class OpenError(Exception):
    def __init__(self, message: str, status: int = 400, kind: str = "error"):
        super().__init__(message)
        self.message, self.status, self.kind = message, status, kind


def tmp_root() -> Path:
    from ..db.connection import data_dir
    return data_dir() / "tmp"


def safe_name(name: str) -> str:
    name = os.path.basename(name.replace("/", "\\").rstrip("\\")) or "attachment"
    name = "".join("_" if c in '<>:"|?*' or ord(c) < 32 else c for c in name).strip(" .") or "attachment"
    if os.path.splitext(name)[1].lower() in UNSAFE_EXT:
        name += ".txt"
    return name


def _rm_readonly(func, path, exc):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def cleanup_tmp() -> int:
    """Remove materialized copies (exit and launch). Copies still open in a viewer stay until next time."""
    root = tmp_root()
    if not root.is_dir():
        return 0
    n = 0
    for d in root.iterdir():
        try:
            if d.is_dir():
                shutil.rmtree(d, onexc=_rm_readonly)
            else:
                os.chmod(d, stat.S_IWRITE)
                d.unlink()
            n += 0 if d.exists() else 1
        except OSError:
            pass
    return n


def materialize(store: VaultStore, sha: str, name: str) -> Path:
    d = tmp_root() / secrets.token_hex(6)
    d.mkdir(parents=True, exist_ok=False)
    dest = store.restore_to(sha, d / safe_name(name))       # verifies the hash while writing
    os.chmod(dest, stat.S_IREAD)
    return dest


def _startfile(path: str) -> None:
    os.startfile(path)  # type: ignore[attr-defined]  # default application (Windows)


# ----------------------------------------------------------------- lookups

def _attachment(conn: sqlite3.Connection, attachment_id: str) -> sqlite3.Row:
    a = conn.execute("SELECT * FROM attachment WHERE id=?", (attachment_id,)).fetchone()
    if a is None:
        raise OpenError("No such link", 404, "not_found")
    return a


def _version(conn: sqlite3.Connection, version_id: str | None) -> sqlite3.Row | None:
    if not version_id:
        return None
    return conn.execute("SELECT * FROM file_version WHERE id=?", (version_id,)).fetchone()


def source_for(conn: sqlite3.Connection, a: sqlite3.Row) -> sqlite3.Row | None:
    """The file_source of a link: its pinned version's source, else the one for its path."""
    if a["pinned_version_id"]:
        r = conn.execute("SELECT s.* FROM file_source s JOIN file_version v ON v.source_id = s.id WHERE v.id=?",
                         (a["pinned_version_id"],)).fetchone()
        if r is not None:
            return r
    return conn.execute("SELECT * FROM file_source WHERE path_key=?", (cap.path_key(a["target"]),)).fetchone()


def latest_version(conn: sqlite3.Connection, source_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM file_version WHERE source_id=? ORDER BY captured_at DESC, rowid DESC LIMIT 1",
                        (source_id,)).fetchone()


def local_time(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return t.astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


# ----------------------------------------------------------------- opening

def open_attachment(conn: sqlite3.Connection, store: VaultStore, attachment_id: str,
                    which: str = "pinned") -> dict[str, Any]:
    """Open a file link per R-162. ``which``: 'pinned' (default click), 'current' (the original now),
    'latest' (newest captured version) or a ``file_version.id`` of this link's file."""
    a = _attachment(conn, attachment_id)
    if a["kind"] != "file":
        raise OpenError("Only file links have saved versions", 400, "not_file")
    target = cap.strip_long(a["target"].strip().strip('"'))
    src = source_for(conn, a)
    pinned = _version(conn, a["pinned_version_id"])
    latest = latest_version(conn, src["id"]) if src is not None else None
    current = cap.hash_original(target)
    reachable = current is not None
    info: dict[str, Any] = {
        "original_reachable": reachable,
        "pinned_version_id": pinned["id"] if pinned else None,
        "saved_at": pinned["captured_at"] if pinned else None,
        "latest_version_id": latest["id"] if latest else None,
        "latest_saved_at": latest["captured_at"] if latest else None,
        "latest_differs": bool(pinned and latest and latest["sha256"] != pinned["sha256"]),
        "current_differs": bool(pinned and reachable and current != pinned["sha256"]),
        "current_captured": bool(src is not None and reachable and conn.execute(
            "SELECT 1 FROM file_version WHERE source_id=? AND sha256=?", (src["id"], current)).fetchone()),
        "definitions": NOTICE_DEFS, "notice": None, "path": None,
    }

    def open_original() -> dict[str, Any]:
        if not reachable:
            raise OpenError("That file is not reachable", 404, "broken")
        _startfile(target)
        return {**info, "opened": "original", "opened_version_id": None}

    if which == "current":
        return open_original()
    if which == "pinned":
        if pinned is None:                       # not yet protected: open the original
            if not os.path.exists(cap.io_path(target)):
                raise OpenError("That file or folder is not reachable", 404, "broken")
            _startfile(target)
            return {**info, "opened": "original", "opened_version_id": None}
        if current == pinned["sha256"]:
            return open_original()
        version = pinned
    elif which == "latest":
        if latest is None:
            raise OpenError("No saved version of this file yet", 404, "no_version")
        version = latest
    else:
        version = _version(conn, which)
        if version is None or src is None or version["source_id"] != src["id"]:
            raise OpenError("That version does not belong to this link's file", 404, "not_found")
    try:
        path = materialize(store, version["sha256"], os.path.basename(target))
    except VaultError as e:
        raise OpenError(f"The saved version from {local_time(version['captured_at'])} could not be read: {e}",
                        500, "vault") from None
    _startfile(str(path))
    when = local_time(version["captured_at"])
    if which == "pinned":
        notice = (f"Opened the saved version from {when}. " +
                  ("The file has changed since it was attached." if reachable else
                   "The original file is missing or unreachable."))
    else:
        notice = f"Opened the saved version from {when} (a read-only copy; edits do not change the vault)."
    return {**info, "opened": "saved", "opened_version_id": version["id"], "opened_saved_at": version["captured_at"],
            "path": str(path), "notice": notice}


# ----------------------------------------------------------------- history

def versions(conn: sqlite3.Connection, attachment_id: str) -> list[dict[str, Any]]:
    """All captured versions of this link's file, newest first, with the links that pin each (R-164)."""
    a = _attachment(conn, attachment_id)
    if a["kind"] != "file":
        return []
    src = source_for(conn, a)
    if src is None:
        return []
    rows = conn.execute("SELECT * FROM file_version WHERE source_id=? ORDER BY captured_at DESC, rowid DESC",
                        (src["id"],)).fetchall()
    out = []
    for i, v in enumerate(rows):
        pins = [dict(r) for r in conn.execute(
            "SELECT a.id attachment_id, a.item_id, i.title item_title, i.deleted_at IS NOT NULL item_deleted, "
            "a.pin_basis FROM attachment a JOIN item i ON i.id = a.item_id WHERE a.pinned_version_id=? "
            "ORDER BY a.created_at", (v["id"],))]
        out.append({"id": v["id"], "captured_at": v["captured_at"], "size": v["size"], "sha256": v["sha256"],
                    "sha_short": v["sha256"][:12], "file_mtime": v["file_mtime"],
                    "is_pinned": v["id"] == a["pinned_version_id"], "is_latest": i == 0, "pinned_by": pins})
    return out


def protection(conn: sqlite3.Connection, attachment_id: str) -> dict[str, Any]:
    """Protection state for the item panel (R-160, R-163)."""
    a = _attachment(conn, attachment_id)
    if a["kind"] != "file":
        return {"kind": a["kind"], "protected": False, "status": "not_applicable",
                "gap": "Folder links and web addresses are not version-protected."}
    src = source_for(conn, a)
    pinned = _version(conn, a["pinned_version_id"])
    latest = latest_version(conn, src["id"]) if src is not None else None
    status = src["status"] if src is not None else "pending"
    if pinned is None:
        state = "capturing" if status in ("pending", "protected") else "not_protected"
    else:
        state = "protected"
    gap = None
    if pinned is None and status not in ("pending", "protected"):
        gap = f"Not protected: {src['last_error'] or status}. Reframe retries automatically."
    elif pinned is None:
        gap = "Capturing a protected copy…"
    elif status not in ("protected", "pending"):
        gap = (f"The pinned version is safe, but newer edits are not being captured: "
               f"{src['last_error'] or status}.")
    return {
        "kind": "file", "protected": pinned is not None, "state": state, "status": status,
        "pinned_version_id": pinned["id"] if pinned else None,
        "pinned_at": pinned["captured_at"] if pinned else None,
        "pin_basis": a["pin_basis"],
        "pin_note": "captured at migration, not at attach time" if a["pin_basis"] == "migration" else None,
        "last_check_at": src["last_check_at"] if src is not None else None,
        "last_capture_at": src["last_capture_at"] if src is not None else None,
        "latest_version_id": latest["id"] if latest else None,
        "latest_saved_at": latest["captured_at"] if latest else None,
        "error": src["last_error"] if src is not None else None,
        "gap": gap,
        "best_effort": "Edits made while Reframe is not running, or overwritten before capture, may not be preserved.",
    }


def restore_version(conn: sqlite3.Connection, store: VaultStore, version_id: str, dest: str | os.PathLike) -> Path:
    """Write a version to a chosen path (from the native save chooser). Never overwrites."""
    v = _version(conn, version_id)
    if v is None:
        raise OpenError("No such version", 404, "not_found")
    d = Path(str(dest).strip().strip('"'))
    if not d.is_absolute():
        raise OpenError("Choose a full destination path", 400, "invalid")
    if d.exists():
        raise OpenError(f"'{d}' already exists; choose a new name (Reframe never overwrites)", 409, "exists")
    if not d.parent.is_dir():
        raise OpenError(f"The folder '{d.parent}' does not exist", 400, "invalid")
    try:
        return store.restore_to(v["sha256"], d)
    except FileExistsError:
        raise OpenError(f"'{d}' already exists; choose a new name (Reframe never overwrites)", 409, "exists") from None
    except VaultError as e:
        raise OpenError(f"Version {v['sha256'][:12]} could not be restored: {e}", 500, "vault") from None
