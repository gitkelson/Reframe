"""Stable capture of one attached file into the vault, and pinning of links (R-160, R-161, R-163).

Order of operations (spec 5.4): read + store the blob with NO database transaction open, then one short
``BEGIN IMMEDIATE`` to upsert ``file_source`` and insert ``file_version``. ``file_source``/``file_version`` are not
change-logged, so they use plain transactions. Pinning changes ``attachment`` and therefore goes through the
change-log writer (actor ``user``).
"""
from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import errno
import hashlib
import os
import sqlite3
import stat as statmod
import threading
import time
from typing import BinaryIO, Iterable, Iterator

from ..db.changelog import writer
from .store import CHUNK, Staged, VaultError, VaultStore

PROTECTED = "protected"
RETRYABLE = frozenset({"locked", "changed", "unreachable", "failed"})
REMOTE_TIMEOUT_S = 3.0

MIGRATION_LABEL = "Capture attached files (imported links)"

_LOCKED = {32, 33}                       # ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION
_DENIED = {5, 1920}                      # ERROR_ACCESS_DENIED, ERROR_CANT_ACCESS_FILE
_MISSING = {2, 3, 123, 161}              # file/path not found, bad name, bad pathname
_UNREACHABLE = {21, 51, 53, 54, 55, 59, 64, 65, 67, 121, 1222, 1231, 1232, 1311, 1326, 2250}
_DISK_FULL = {39, 112}                   # ERROR_HANDLE_DISK_FULL, ERROR_DISK_FULL


class CaptureError(Exception):
    def __init__(self, status: str, message: str, detail: str = ""):
        super().__init__(f"{message} ({detail})" if detail else message)
        self.status = status
        self.message = message
        self.detail = detail


@dataclasses.dataclass
class CaptureResult:
    status: str                       # 'protected' or a failure status of file_source.status
    version_id: str | None = None
    sha256: str | None = None
    error: str | None = None
    source_id: str | None = None
    size: int | None = None
    new_version: bool = False         # a file_version row was created by this capture

    @property
    def ok(self) -> bool:
        return self.status == PROTECTED


# ----------------------------------------------------------------- paths

def strip_long(path: str) -> str:
    p = str(path)
    if p.startswith("\\\\?\\UNC\\"):
        return "\\\\" + p[8:]
    if p.startswith("\\\\?\\"):
        return p[4:]
    return p


def path_key(path: str) -> str:
    """``file_source.path_key``: normalized absolute path, case-folded, separators unified."""
    return os.path.normcase(os.path.abspath(strip_long(str(path).strip().strip('"'))))


def io_path(path: str) -> str:
    """A path usable for I/O, with the ``\\\\?\\`` prefix when it is longer than MAX_PATH allows."""
    p = os.path.abspath(strip_long(str(path)))
    if os.name == "nt" and len(p) >= 240:
        return "\\\\?\\UNC\\" + p[2:] if p.startswith("\\\\") else "\\\\?\\" + p
    return p


def _is_remote(p: str) -> bool:
    p = strip_long(p)
    if p.startswith("\\\\"):
        return True
    if os.name == "nt" and len(p) >= 2 and p[1] == ":":
        try:
            import ctypes
            return ctypes.windll.kernel32.GetDriveTypeW(p[:2] + "\\") == 4      # DRIVE_REMOTE
        except Exception:
            return False
    return False


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _mtime_iso(st: os.stat_result) -> str:
    return dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ----------------------------------------------------------------- reading

def open_shared(path: str) -> BinaryIO:
    """Open for reading while letting others keep reading/writing/deleting (files open in Office still capture).

    A handle opened by another program with an exclusive share mode fails with ERROR_SHARING_VIOLATION, which
    is reported as 'locked'. Uses CreateFileW so the Windows error code survives (the CRT ``open`` loses it).
    """
    if os.name != "nt":
        return open(path, "rb")
    import ctypes
    import msvcrt
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    h = k32.CreateFileW(path, 0x80000000, 0x1 | 0x2 | 0x4, None, 3, 0x08000000, None)
    if h is None or h == wintypes.HANDLE(-1).value:
        code = ctypes.get_last_error()
        raise ctypes.WinError(code, f"{ctypes.FormatError(code).strip()}: {path!r}")
    try:
        fd = msvcrt.open_osfhandle(h, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except OSError:
        k32.CloseHandle(h)
        raise
    return os.fdopen(fd, "rb")


def classify(e: BaseException) -> tuple[str, str]:
    """Map an exception to a file_source status and a plain-language reason."""
    if isinstance(e, CaptureError):
        return e.status, e.message
    win = getattr(e, "winerror", None)
    en = getattr(e, "errno", None)
    if win in _DISK_FULL or en == errno.ENOSPC:
        return "failed", "Disk full: the vault folder has no free space"
    if win in _LOCKED:
        return "locked", "The file is locked by another program"
    if win in _UNREACHABLE or isinstance(e, TimeoutError):
        return "unreachable", "The network location is not available"
    if win in _DENIED:
        return "denied", "Permission denied"
    if win in _MISSING or isinstance(e, FileNotFoundError):
        return "missing", "The file was not found (moved, renamed or deleted)"
    if isinstance(e, PermissionError):
        return "denied", "Permission denied"
    if isinstance(e, IsADirectoryError):
        return "failed", "The link points to a folder"
    if isinstance(e, VaultError):
        return "failed", f"Vault error: {e}"
    return "failed", f"{type(e).__name__}: {e}"


def _probe_remote(p: str, timeout: float) -> None:
    """Bound the time spent on an unreachable share: stat it on a helper thread."""
    box: dict = {}

    def run():
        try:
            box["st"] = os.stat(p)
        except BaseException as e:          # noqa: BLE001 - re-raised on the caller's thread
            box["e"] = e
    t = threading.Thread(target=run, daemon=True, name="vault-probe")
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise CaptureError("unreachable", f"The network location did not answer within {timeout:.0f} s")
    if "e" in box:
        raise box["e"]


def hash_file(path: str) -> tuple[str, int]:
    """SHA-256 and size of a file's current content (raises OSError)."""
    h = hashlib.sha256()
    n = 0
    with open_shared(path) as fh:
        while True:
            b = fh.read(CHUNK)
            if not b:
                break
            h.update(b)
            n += len(b)
    return h.hexdigest(), n


def hash_original(path: str, *, timeout: float = REMOTE_TIMEOUT_S) -> str | None:
    """SHA-256 of the reachable original, or None when it cannot be read."""
    p = io_path(path)
    try:
        if _is_remote(p):
            _probe_remote(p, timeout)
        if not os.path.isfile(p):
            return None
        return hash_file(p)[0]
    except (OSError, CaptureError):
        return None


def read_stable(store: VaultStore, path: str, *, timeout: float = REMOTE_TIMEOUT_S) -> tuple[Staged, os.stat_result]:
    """Stage the file's content; reject it when it changed during the read (stat + second hash)."""
    p = io_path(path)
    if _is_remote(p):
        _probe_remote(p, timeout)
    st1 = os.stat(p)
    if statmod.S_ISDIR(st1.st_mode):
        raise CaptureError("failed", "The link points to a folder")
    with open_shared(p) as fh:
        staged = store.stage_stream(fh)
    try:
        st2 = os.stat(p)
        if (st2.st_size, st2.st_mtime_ns) != (st1.st_size, st1.st_mtime_ns) or staged.size != st2.st_size:
            raise CaptureError("changed", "The file changed while it was being read; will retry",
                               detail=f"stat {st1.st_size}/{st1.st_mtime_ns} -> {st2.st_size}/{st2.st_mtime_ns}, "
                                      f"read {staged.size}")
        sha2, _ = hash_file(p)
        if sha2 != staged.sha256:
            raise CaptureError("changed", "The file changed while it was being read; will retry",
                               detail="second hash differs")
    except BaseException:
        store.discard(staged)
        raise
    return staged, st2


# ----------------------------------------------------------------- database

@contextlib.contextmanager
def _tx(conn: sqlite3.Connection, attempts: int = 6) -> Iterator[None]:
    if conn.in_transaction:
        raise RuntimeError("vault metadata writes must not run inside another transaction")
    delay = 0.05
    for i in range(attempts):
        try:
            conn.execute("BEGIN IMMEDIATE")
            break
        except sqlite3.OperationalError as e:
            if ("locked" in str(e) or "busy" in str(e)) and i < attempts - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    try:
        yield
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def _upsert_source(conn: sqlite3.Connection, display: str) -> str:
    return conn.execute(
        "INSERT INTO file_source (path_key, display_path) VALUES (?,?) "
        "ON CONFLICT(path_key) DO UPDATE SET display_path=excluded.display_path RETURNING id",
        (path_key(display), strip_long(display))).fetchone()[0]


def record_status(conn: sqlite3.Connection, path: str, status: str, error: str | None) -> str:
    """Remember a failed (or unchanged) check of an attached file. Returns the file_source id."""
    with _tx(conn):
        sid = _upsert_source(conn, path)
        conn.execute("UPDATE file_source SET status=?, last_error=?, last_check_at=? WHERE id=?",
                     (status, error, now_iso(), sid))
    return sid


def touch_checked(conn: sqlite3.Connection, source_id: str) -> None:
    with _tx(conn):
        conn.execute("UPDATE file_source SET status='protected', last_error=NULL, last_check_at=? WHERE id=?",
                     (now_iso(), source_id))


def _record_version(conn: sqlite3.Connection, display: str, staged: Staged, st: os.stat_result,
                    store: VaultStore) -> tuple[str, str, bool]:
    now = now_iso()
    with _tx(conn):
        sid = _upsert_source(conn, display)
        cur = conn.execute(
            "INSERT INTO file_version (source_id, sha256, size, file_mtime, backend_ref) VALUES (?,?,?,?,?) "
            "ON CONFLICT(source_id, sha256) DO NOTHING",
            (sid, staged.sha256, staged.size, _mtime_iso(st), store.backend_ref(staged.sha256)))
        created = cur.rowcount == 1
        vid = conn.execute("SELECT id FROM file_version WHERE source_id=? AND sha256=?",
                           (sid, staged.sha256)).fetchone()[0]
        conn.execute("UPDATE file_source SET last_size=?, last_mtime=?, last_hash=?, status='protected', "
                     "last_check_at=?, last_capture_at=?, last_error=NULL WHERE id=?",
                     (staged.size, _mtime_iso(st), staged.sha256, now, now, sid))
    return sid, vid, created


def capture_file(conn: sqlite3.Connection, store: VaultStore, path: str) -> CaptureResult:
    """Capture the current content of ``path`` as a version of its file_source (idempotent per content)."""
    display = strip_long(str(path).strip().strip('"'))
    try:
        staged, st = read_stable(store, display)
        try:
            store.commit(staged)
        finally:
            store.discard(staged)
    except (OSError, CaptureError, VaultError) as e:
        status, msg = classify(e)
        try:
            record_status(conn, display, status, msg)
        except sqlite3.Error:
            pass
        return CaptureResult(status, error=msg)
    sid, vid, created = _record_version(conn, display, staged, st, store)
    return CaptureResult(PROTECTED, vid, staged.sha256, None, sid, staged.size, created)


# ----------------------------------------------------------------- pinning

def protect_label(target: str) -> str:
    name = os.path.basename(strip_long(target).rstrip("\\/")) or target
    return f"Protect '{name}'"


def pin_attachments(conn: sqlite3.Connection, pins: Iterable[tuple[str, str]], basis: str,
                    label: str) -> list[str]:
    """Pin links (attachment_id, version_id) in ONE change set. Only unpinned ``kind='file'`` links whose path
    matches the version's file are changed. Returns the ids that were pinned."""
    if basis not in ("attach", "migration", "duplicate"):
        raise ValueError(f"unknown pin basis {basis!r}")
    pins = list(pins)
    if not pins:
        return []
    done: list[str] = []
    with writer(conn, "user", label):
        for att_id, vid in pins:
            a = conn.execute("SELECT kind, target, pinned_version_id FROM attachment WHERE id=?", (att_id,)).fetchone()
            v = conn.execute("SELECT s.path_key FROM file_version v JOIN file_source s ON s.id=v.source_id "
                             "WHERE v.id=?", (vid,)).fetchone()
            if a is None or v is None or a["kind"] != "file" or a["pinned_version_id"] is not None:
                continue
            if path_key(a["target"]) != v["path_key"]:
                continue
            cur = conn.execute("UPDATE attachment SET pinned_version_id=?, pin_basis=? "
                               "WHERE id=? AND kind='file' AND pinned_version_id IS NULL", (vid, basis, att_id))
            if cur.rowcount:
                done.append(att_id)
    return done


def pin_attachment(conn: sqlite3.Connection, attachment_id: str, version_id: str, basis: str = "attach",
                   label: str | None = None) -> bool:
    """Pin one unpinned file link to a captured version (change-logged, actor 'user')."""
    if label is None:
        r = conn.execute("SELECT target FROM attachment WHERE id=?", (attachment_id,)).fetchone()
        label = MIGRATION_LABEL if basis == "migration" else protect_label(r["target"] if r else "file")
    return bool(pin_attachments(conn, [(attachment_id, version_id)], basis, label))
